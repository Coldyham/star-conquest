#!/usr/bin/env python3
"""Make the daily challenge: one dealt setup a day, stored in ``public.dailies``.

Every UTC day has one map, the same for everyone. It is dealt by
``starconquest.dealer`` from ``tools/daily_spec.json`` with the weekday's
systems and players added (the board grows from Monday to Sunday), its ship
speed fitted so the median lane takes 2-5 turns (the ``lane_turns`` rule), and
its opponents drawn at one depth chosen for the day. A candidate is kept only
once a generated-gauntlet probe has won it from the human's seat within the
day's turn cap; the first such candidate is the day's map. Reasons and
measurements: ``docs/design/daily.md``.

The map is made here and stored, never made by the game: which candidate
survives depends on how the probes play, bots are free to move, and actuary,
an allowed opponent, is time-budgeted, so two builds would make two dailies.

Runs hourly on the same job as the campaign. It makes today's and tomorrow's
rows if missing and does nothing once both exist, so the day rolls over with
its row already there. Each probe game is appended to
``results/daily/<day>.jsonl`` as it finishes, and a rerun resumes from it:

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...
    uv run python tools/daily.py                     # today's and tomorrow's, if not made yet
    uv run python tools/daily.py --day 2026-11-02    # that day's
    uv run python tools/daily.py --dry-run           # make them, print them, store nothing

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

SPEC_FILE = ROOT / "tools" / "daily_spec.json"
RESULTS = ROOT / "results" / "daily"
# Players by weekday, Monday first: the fewest rises so no big board is a duel,
# the most rises so no small board is crowded (the author, 2026-10-28).
PLAYERS = ((2, 3), (2, 4), (2, 5), (3, 6), (3, 6), (4, 6), (5, 6))
# A day with no candidate a probe won inside its cap after this many is left
# empty. Sunday keeps about one candidate in four, so an empty Sunday is about
# one in 10,000 (docs/design/daily.md, "Random, but not unplayable").
MAX_CANDIDATES = 32
DAYS_AHEAD = 1


def systems(weekday: int) -> tuple[int, int]:
    """The day's system count range: Monday 7-12, three more each day."""
    return 7 + 3 * weekday, 12 + 3 * weekday


def turn_cap(weekday: int) -> int:
    """The most turns a probe may take to win the day's map: 80 on Monday, 110
    on Sunday."""
    return 80 + 5 * weekday


def base_spec() -> dict:
    return json.loads(SPEC_FILE.read_text(encoding="utf-8"))


def day_spec(day: dt.date, base: dict | None = None) -> dealer.Spec:
    """The spec ``day`` is dealt from: the file's, with the weekday's systems
    and players."""
    data = json.loads(json.dumps(base if base is not None else base_spec()))
    w = day.weekday()
    data["rules"]["systems"] = {"range": list(systems(w))}
    data["rules"]["players"] = {"range": list(PLAYERS[w])}
    data["stages"] = 1
    data.pop("seed", None)
    return dealer.Spec.from_dict(data)


def day_rng(day: dt.date) -> random.Random:
    """The day's rng: every draw a day's dealing makes comes from it."""
    return random.Random(f"daily:{day.isoformat()}")


def candidates(day: dt.date, spec: dealer.Spec | None = None, limit: int = MAX_CANDIDATES):
    """``(depth, [settings, ...])``: the day's opponents' depth and its
    candidates in the order they are tried. A pure function of the day and the
    spec, so a rerun deals the same ones."""
    spec = spec if spec is not None else day_spec(day)
    rng = day_rng(day)
    depth = round(rng.random(), 4)
    return depth, [dealer.deal(spec, rng, depth) for _ in range(limit)]


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


def make(day: dt.date, results: Path = RESULTS, limit: int = MAX_CANDIDATES,
         probes=gauntletgen.PROBES, out=print) -> dict | None:
    """The day's row, or None when no candidate was won inside the cap. Probes
    play a candidate weakest first and stop at the first win; each game is
    recorded as it finishes and reused on a rerun."""
    w = day.weekday()
    cap = turn_cap(w)
    spec = day_spec(day)
    depth, cands = candidates(day, spec, limit)
    path = results / f"{day.isoformat()}.jsonl"
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
            out(f"  {day} candidate {i + 1}: {dealer.describe(settings)} -> "
                + (f"won by {best['bot']} in {best['turns']}" if best else f"no probe win within {cap}"))
            if best:
                return {"day": day.isoformat(), "settings": stage,
                        "spec": {**spec.to_dict(), "depth": depth, "candidate": i, "turn_cap": cap},
                        "probes": [{k: r[k] for k in ("bot", "won", "turns", "lost", "timed_out")}
                                   for r in rows],
                        "rules_version": engine.RULES_VERSION}
    return None


def days_to_make(today: dt.date) -> list[dt.date]:
    return [today + dt.timedelta(days=i) for i in range(DAYS_AHEAD + 1)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--day", help="make only this day (YYYY-MM-DD); default today and tomorrow")
    parser.add_argument("--dry-run", action="store_true",
                        help="make the days and print them, store nothing (needs no credentials)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    days = ([dt.date.fromisoformat(args.day)] if args.day
            else days_to_make(dt.datetime.now(dt.timezone.utc).date()))
    api = None
    if not args.dry_run:
        url, key = credentials()
        if not url or not key:
            print(MISSING_CREDENTIALS, file=sys.stderr)
            return 2
        api = Supabase(url, key)
    failed = False
    for day in days:
        if api is not None and api.select("dailies", f"select=day&day=eq.{day.isoformat()}"):
            print(f"daily for {day} already exists — nothing to do")
            continue
        print(f"daily for {day} ({day.strftime('%A')}): dealing", flush=True)
        row = make(day, out=lambda line: print(line, flush=True))
        if row is None:
            print(f"daily for {day}: no candidate of {MAX_CANDIDATES} was won inside "
                  f"{turn_cap(day.weekday())} turns — left empty", file=sys.stderr)
            failed = True
            continue
        print(f"daily for {day}: {dealer.describe(Settings.from_dict(row['settings']))}")
        if api is None:
            print(json.dumps(row, indent=1))
            continue
        try:
            api.insert("dailies", [row])
        except ApiError as err:
            if "409" in str(err) or "23505" in str(err):   # another run beat us to it
                print(f"daily for {day} was written by another run")
                continue
            raise
        print("stored")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
