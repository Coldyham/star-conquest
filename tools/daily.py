#!/usr/bin/env python3
"""Make the daily challenges: two dealt setups a day, stored in ``public.dailies``.

Every UTC day has two maps, each the same for everyone: the **board** and the
**puzzle** (a row's ``kind``). The board is dealt by ``starconquest.dealer``
from ``tools/daily_spec.json`` with the weekday's systems and players added
(the board grows from Monday to Sunday), its ship speed fitted so the median
lane takes 2-5 turns (the ``lane_turns`` rule), and each opponent's seat drawn
on its own from every allowed bot. The puzzle is a small duel dealt from
``tools/puzzle_spec.json`` against the weekday's one opponent
(``PUZZLE_OPPONENTS``). A candidate is kept only once a generated-gauntlet probe
has won it from the human's seat within the day's turn cap; the first such
candidate is the day's map. Reasons and measurements:
``docs/design/daily.md``.

The map is made here and stored, never made by the game: which candidate
survives depends on how the probes play, bots are free to move, and actuary,
an allowed opponent, is time-budgeted, so two builds would make two dailies.

Runs hourly on the same job as the campaign. It makes today's and tomorrow's
rows of each kind if missing and does nothing once they exist, so the day
rolls over with its rows already there. Each probe game is appended to
``results/daily/<day>.jsonl`` (``results/daily/puzzle/<day>.jsonl``) as it
finishes, and a rerun resumes from it:

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...
    uv run python tools/daily.py                     # today's and tomorrow's, if not made yet
    uv run python tools/daily.py --day 2026-11-02    # that day's
    uv run python tools/daily.py --kind puzzle       # only the puzzles
    uv run python tools/daily.py --dry-run           # make them, print them, store nothing

To play what a weekday deals, and feel a change to the spec or the tables
below, use ``tools/try_daily.py``.

Pure of pygame, like the rest of the worker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, dealer, engine, gauntletgen    # noqa: E402
from starconquest.settings import Settings    # noqa: E402
from tools.bot_replay import MISSING_CREDENTIALS, ApiError, Supabase, credentials    # noqa: E402

KINDS = ("board", "puzzle")
SPEC_FILE = ROOT / "tools" / "daily_spec.json"
PUZZLE_SPEC_FILE = ROOT / "tools" / "puzzle_spec.json"
RESULTS = ROOT / "results" / "daily"
# Players by weekday, Monday first: never a duel, since the daily puzzle is one,
# and the most rises so no small board is crowded (the author, 2026-10-28).
PLAYERS = ((3, 4), (3, 4), (3, 5), (3, 5), (3, 6), (4, 6), (5, 6))
# The puzzle's opponent by weekday, Monday first: a bot, a stop of one, or a
# probe tweak (`gauntletgen.TWEAKS`). The same every week (the author, 2026-10-28).
PUZZLE_OPPONENTS = ("heuristic", "heuristic+lean", "rusherplus", "thinker:0", "thinker", "marshal", "actuary")
# A puzzle is easy to win; the cap only turns away a map nobody can finish.
PUZZLE_TURN_CAP = 60
# A day with no candidate a probe won inside its cap after this many is left
# empty. Sunday keeps about one candidate in four, so an empty Sunday is about
# one in 10,000 (docs/design/daily.md, "Random, but not unplayable").
MAX_CANDIDATES = 32
DAYS_AHEAD = 1


def systems(weekday: int) -> tuple[int, int]:
    """The day's system count range: Monday 11-14, two more each day."""
    return 11 + 2 * weekday, 14 + 2 * weekday


def turn_cap(weekday: int, kind: str = "board") -> int:
    """The most turns a probe may take to win the day's map: 80 on Monday, 110
    on Sunday; a puzzle's every day."""
    return PUZZLE_TURN_CAP if kind == "puzzle" else 80 + 5 * weekday


def base_spec(kind: str = "board") -> dict:
    return json.loads((PUZZLE_SPEC_FILE if kind == "puzzle" else SPEC_FILE).read_text(encoding="utf-8"))


def day_spec(day: dt.date, base: dict | None = None, kind: str = "board") -> dealer.Spec:
    """The spec ``day`` is dealt from: the board's file with the weekday's
    systems and players, or the puzzle's with the weekday's opponent (a tweak
    deals as its bot and is seated by ``candidates``)."""
    data = json.loads(json.dumps(base if base is not None else base_spec(kind)))
    w = day.weekday()
    if kind == "puzzle":
        opponent = PUZZLE_OPPONENTS[w]
        data["opponents"] = {"in": [gauntletgen.TWEAKS[opponent][0] if opponent in gauntletgen.TWEAKS else opponent]}
    else:
        data["rules"]["systems"] = {"range": list(systems(w))}
        data["rules"]["players"] = {"range": list(PLAYERS[w])}
    data["stages"] = 1
    data.pop("seed", None)
    return dealer.Spec.from_dict(data)


def day_rng(day: dt.date, trial: str = "", kind: str = "board") -> random.Random:
    """The day's rng: every draw a day's dealing makes comes from it, a stream
    per kind. A ``trial`` (``tools/try_daily.py``) deals from a stream of its
    own and the weekday's, never the day's, so it deals the same on any week's
    day."""
    tag = "daily" if kind == "board" else f"daily:{kind}"
    return random.Random(f"{tag}:try:{day.weekday()}:{trial}" if trial else f"{tag}:{day.isoformat()}")


def _seat_puzzle_opponent(settings: Settings, weekday: int) -> Settings:
    """Seat the weekday's puzzle opponent with its params: a tweak's own, a
    stop's aux, or the dealt defaults."""
    strategy, params = gauntletgen.probe_profile(PUZZLE_OPPONENTS[weekday])
    settings.ai_strategy[1] = strategy
    settings.ai[1] = params
    return settings


def candidates(day: dt.date, spec: dealer.Spec | None = None, limit: int = MAX_CANDIDATES, trial: str = "",
               kind: str = "board") -> list[Settings]:
    """The day's candidates in the order they are tried. A pure function of the
    day, the spec, the trial and the kind, so a rerun deals the same ones. A
    board's every opponent seat is drawn on its own from the allowed bots, so
    any mix can be dealt on any day; a puzzle has the weekday's one opponent."""
    spec = spec if spec is not None else day_spec(day, kind=kind)
    rng = day_rng(day, trial, kind)
    if kind == "puzzle":
        return [_seat_puzzle_opponent(dealer.deal(spec, rng), day.weekday()) for _ in range(limit)]
    return [dealer.deal(spec, rng, None) for _ in range(limit)]


def _load(path: Path) -> list[dict]:
    """The games already played for a day. A line cut short by a killed run is
    skipped."""
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def winner(rows: list[dict], cap: int) -> dict | None:
    """The fastest probe win inside the cap among one candidate's games."""
    wins = [r for r in rows if r["won"] and r["turns"] <= cap]
    return min(wins, key=lambda r: (r["turns"], r["lost"])) if wins else None


def record_path(day: dt.date, results: Path = RESULTS, trial: str = "", kind: str = "board") -> Path:
    """Where a day's probe games are recorded: a puzzle's under ``puzzle/``, a
    trial's under ``try/``."""
    folder = results / "puzzle" if kind == "puzzle" else results
    return folder / (f"try/{day.strftime('%a').lower()}-{trial}.jsonl" if trial else f"{day.isoformat()}.jsonl")


def make(day: dt.date, results: Path = RESULTS, limit: int = MAX_CANDIDATES,
         probes=gauntletgen.PROBES, out=print, trial: str = "", kind: str = "board") -> dict | None:
    """The day's row of ``kind``, or None when no candidate was won inside the
    cap. Probes play a candidate weakest first and stop at the first win; each
    game is recorded as it finishes and reused on a rerun. A ``trial`` deals
    from its own stream and records under ``try/``, so it never touches the
    day's."""
    w = day.weekday()
    cap = turn_cap(w, kind)
    spec = day_spec(day, kind=kind)
    cands = candidates(day, spec, limit, trial, kind)
    path = record_path(day, results, trial, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["candidate"], r["bot"]): r for r in _load(path)}
    ai.load_models()
    ai.set_budget_scale(math.inf)
    with path.open("a", encoding="utf-8") as f:
        for i, settings in enumerate(cands):
            key, stage = settings.challenge_key(), dealer.stage_dict(settings)
            rows = []
            for bot in probes:
                row = done.get((i, bot))
                if row is None or row.get("key") != key:
                    row = {"candidate": i, **gauntletgen.play_one(key, stage, bot, cap)}
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                rows.append(row)
                if winner([row], cap):
                    break
            best = winner(rows, cap)
            out(f"  {day} {kind} candidate {i + 1}: {dealer.describe(settings)} -> "
                + (f"won by {best['bot']} in {best['turns']}" if best else f"no probe win within {cap}"))
            if best:
                spec_out = {**spec.to_dict(), "candidate": i, "turn_cap": cap}
                if kind == "puzzle":
                    spec_out["opponent"] = PUZZLE_OPPONENTS[w]
                return {"day": day.isoformat(), "kind": kind, "settings": stage, "spec": spec_out,
                        "probes": [{k: r[k] for k in ("bot", "won", "turns", "lost", "timed_out")}
                                   for r in rows],
                        "rules_version": engine.RULES_VERSION}
    return None


def days_to_make(today: dt.date) -> list[dt.date]:
    return [today + dt.timedelta(days=i) for i in range(DAYS_AHEAD + 1)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--day", help="make only this day (YYYY-MM-DD); default today and tomorrow")
    parser.add_argument("--kind", choices=KINDS, help="make only this kind; default both")
    parser.add_argument("--dry-run", action="store_true",
                        help="make the days and print them, store nothing (needs no credentials)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    days = ([dt.date.fromisoformat(args.day)] if args.day
            else days_to_make(dt.datetime.now(dt.timezone.utc).date()))
    kinds = [args.kind] if args.kind else list(KINDS)
    api = None
    if not args.dry_run:
        url, key = credentials()
        if not url or not key:
            print(MISSING_CREDENTIALS, file=sys.stderr)
            return 2
        api = Supabase(url, key)
    failed = False
    for day in days:
        for kind in kinds:
            name = f"{kind} daily for {day}"
            if api is not None and api.select("dailies", f"select=day&day=eq.{day.isoformat()}&kind=eq.{kind}"):
                print(f"{name} already exists — nothing to do")
                continue
            print(f"{name} ({day.strftime('%A')}): dealing", flush=True)
            row = make(day, out=lambda line: print(line, flush=True), kind=kind)
            if row is None:
                print(f"{name}: no candidate of {MAX_CANDIDATES} was won inside "
                      f"{turn_cap(day.weekday(), kind)} turns — left empty", file=sys.stderr)
                failed = True
                continue
            print(f"{name}: {dealer.describe(Settings.from_dict(row['settings']))}")
            if api is None:
                print(json.dumps(row, indent=1))
                continue
            try:
                api.insert("dailies", [row])
            except ApiError as err:
                if "409" in str(err) or "23505" in str(err):   # another run beat us to it
                    print(f"{name} was written by another run")
                    continue
                raise
            print("stored")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
