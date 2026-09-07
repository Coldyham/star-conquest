#!/usr/bin/env python3
"""Replay every ``models/`` bot through the human's seat on each posted map.

This is the leaderboard's "how would each bot have done?" column, computed
offline by a scheduled GitHub Actions job (``.github/workflows/bot-replay.yml``)
and cached in Supabase as ``public.bot_scores``. It answers, for one map and one
bot: would that bot have taken the board, in how many turns, losing how many
ships — the same two numbers a human's score carries, measured the same way.

Why a batch job and not a request-time service: the answer is a pure function of
the setup, the seed and the code, so it only ever has to be computed once. There
is nothing to serve live, and nothing here needs a host that stays up. It also
keeps a free Supabase project from idling into a pause, which it otherwise does
after about a week.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SERVICE_KEY=<service_role key>       # never the anon key
    uv run python tools/bot_replay.py                    # fill in what's missing
    uv run python tools/bot_replay.py --game-key abc123  # just this map
    uv run python tools/bot_replay.py --stale            # code changed; redo
    uv run python tools/bot_replay.py --dry-run          # compute, post nothing

The service_role key bypasses row-level security, which is the point:
``bot_scores`` has a read policy and no insert policy, so this worker is its only
writer and a bot result cannot be posted by hand the way a human score can. Keep
that key in the workflow's secrets and out of the repo — ``leaderboard/js`` ships
the anon key precisely because it is *not* this one.

Nothing here imports pygame (or anything off PyPI): the simulation core is pure,
which is what lets a plain ``python tools/bot_replay.py`` on a bare runner do the
whole job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai  # noqa: E402
from starconquest.settings import Settings  # noqa: E402
from tests import sim  # noqa: E402  — the shared headless harness (see its docstring)

# Modules whose contents can change what a replay produces. Deliberately not
# "every file in the package": the shell (render/input/menu) and the
# presentation-only layers (fog, replay, viewstate, paths) cannot move a result,
# and including them would make --stale fire on a UI tweak. `starnames` *is*
# outcome-bearing despite being cosmetic — mapgen rolls names off `state.rng`,
# so changing the name list shifts every draw taken after it.
_OUTCOME_MODULES = (
    "ai", "combat", "config", "engine", "geometry", "mapgen", "model",
    "settings", "starnames",
)

# ...and the subset that can move a *recorded* game: `_OUTCOME_MODULES` minus
# `ai`, and none of `models/`. Replaying a log applies its recorded orders and
# deals its recorded dice, so no bot is ever consulted (see `replay_rev`).
_REPLAY_MODULES = tuple(name for name in _OUTCOME_MODULES if name != "ai")

# Where a bot is replayed at something other than its default profile.
#
# `AiParams.aux` is the one bot-defined knob (`models/README.md`): the core never
# interprets it and each strategy assigns its own meaning, so "this bot at its
# best" is a judgement only a caller can make. 1.0 is the documented untuned
# value and stays the default for everything not named here.
#
# knower reads aux as search depth, and 12 is the top of its own slider
# (`SEARCH_DEPTH_MAX`) and the setting its measurements favour — "ahead in every
# measurement taken and behind in none". Its work is iteration-bounded, so the
# result stays reproducible; the caveat is `knower.SEARCH_BUDGET_S`, a 150 ms
# per-decide catastrophe guard that, if it ever trips, makes the plan depend on
# the wall clock. Measured headroom at depth 12 is comfortable on an ordinary map
# and thin (~1.1x) on the largest the menu can build — 40 nodes and 6 seats. Depth
# 12 also costs ~100x depth 1 in wall clock, which is what --limit and the
# deadline are for.
REPLAY_AUX: dict[str, float] = {
    "knower": 12,
}

# How far to lift the bots' own wall-clock catastrophe guards (`ai.set_budget_scale`).
#
# Those guards are sized for the browser build, where the alternative to giving up
# mid-search is freezing the tab. Nothing is waiting on this job, so that trade
# does not apply — and since tripping a guard is the one thing that makes such a
# bot's output depend on the wall clock, lifting it out of the way makes these
# cached results *more* reproducible, not less. 100x turns knower's 150 ms search
# guard into 15 s and its 50 ms oracle guard into 5 s, against a measured worst
# case of ~131 ms per decide at depth 12 on the largest map the menu can build.
#
# Still a guard, not a removal: a genuinely wedged bot is stopped long before the
# workflow's own timeout-minutes has to do it.
BUDGET_SCALE = 100.0

PAGE = 1000          # PostgREST's own default ceiling; page rather than assume
POST_TIMEOUT = 60    # seconds, per HTTP call
RETRIES = 4          # network blips on a CI runner are ordinary


def _digest(paths: list[Path]) -> str:
    """A short content digest of ``paths``, name-sensitive so a rename counts."""
    digest = hashlib.blake2s(digest_size=8)
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def engine_rev() -> str:
    """A digest of everything that determines a *bot replay's* outcome.

    Stored on each ``bot_scores`` row for provenance and read back by ``--stale``.
    A git SHA would be the obvious choice and is the wrong one: it moves on every
    commit, so a CSS change would invalidate the whole board. This moves when —
    and only when — the simulation or a bot does.
    """
    return _digest([ROOT / "starconquest" / f"{name}.py" for name in _OUTCOME_MODULES]
                   + sorted((ROOT / "models").glob("*.py")))


def replay_rev() -> str:
    """A digest of everything that determines a *stored log's* replay.

    Strictly smaller than ``engine_rev``, and the difference is the whole point:
    replaying a log never asks a seat to decide anything (``end_turn(script=…)``
    applies the recorded orders and deals the recorded dice), so neither ``ai``
    nor any ``models/*.py`` can move the result. Pinned by
    ``test_a_replay_does_not_consult_a_bot_even_a_deleted_one``.

    Keying score verdicts off ``engine_rev`` instead would mark every check on the
    board stale each time a bot was tuned — re-deciding hundreds of scores to
    reach byte-identical answers, and implying in the stored row that the verdict
    had depended on a bot.
    """
    return _digest([ROOT / "starconquest" / f"{name}.py" for name in _REPLAY_MODULES])


def replay_aux(bot: str, overrides: dict[str, float] | None = None) -> float:
    """The `aux` value this bot is replayed at (`config.AI_AUX`'s 1.0 by default)."""
    if overrides and bot in overrides:
        return overrides[bot]
    return REPLAY_AUX.get(bot, 1.0)


def aux_note(bot: str, aux: float) -> str:
    """How to say what a non-default `aux` meant, e.g. "Search depth" for knower.

    Read off the strategy's own `AUX_LABEL` through `ai.aux_spec`, the same
    declaration the menu's slider uses, so the board never invents a name for a
    knob it doesn't own. Empty for a bot at its default, or one that ignores aux
    entirely — there is nothing to disclose in either case.
    """
    if aux == 1.0:
        return ""
    spec = ai.aux_spec(bot)
    return spec[0] if spec else ""


# --------------------------------------------------------------------------- #
# Supabase (PostgREST over stdlib http)
# --------------------------------------------------------------------------- #
class ApiError(RuntimeError):
    pass


@dataclass
class Supabase:
    url: str
    key: str

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def _call(self, path: str, *, method: str = "GET", body: bytes | None = None,
              extra: dict[str, str] | None = None):
        request = urllib.request.Request(
            f"{self.url.rstrip('/')}/rest/v1/{path}", data=body, method=method)
        for name, value in {**self._headers(), **(extra or {})}.items():
            request.add_header(name, value)
        last = ""
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=POST_TIMEOUT) as response:
                    text = response.read().decode("utf-8")
                return json.loads(text) if text.strip() else None
            except urllib.error.HTTPError as err:
                # PostgREST explains itself in the body; surface that, not "400".
                detail = err.read().decode("utf-8", "replace")[:500]
                last = f"HTTP {err.code}: {detail}"
                if err.code < 500:  # a 4xx will fail identically on a retry
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                # Safe to retry a POST here: the only one is the upsert, and it
                # is keyed, so a duplicate delivery lands on the same row.
                last = str(err)
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
        raise ApiError(f"{method} {path} failed: {last}")

    def select(self, table: str, query: str) -> list[dict]:
        """Every row of a query, paged — PostgREST silently truncates otherwise.

        Stops on an *empty* page rather than a short one. A project that sets
        PostgREST's ``db-max-rows`` below our page size would answer every
        request with a short page, and "short means last" would then quietly read
        only the first slice of the board. Offsets advance by what actually came
        back, so this walks correctly whatever the server's own cap turns out to
        be. Callers must pass a total ``order``: limit/offset over an unordered
        result is free to repeat and skip rows between pages.
        """
        rows: list[dict] = []
        while True:
            page = self._call(f"{table}?{query}&limit={PAGE}&offset={len(rows)}") or []
            rows.extend(page)
            if not page:
                return rows

    def upsert(self, table: str, rows: list[dict]) -> None:
        """Insert or replace by primary key. A recompute overwrites in place, so a
        map only ever holds one answer per bot."""
        if not rows:
            return
        self._call(table, method="POST",
                   body=json.dumps(rows).encode("utf-8"),
                   extra={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def delete(self, table: str, query: str) -> None:
        """Delete the rows a filter selects. Refuses an unfiltered call, which
        PostgREST would happily read as "every row in the table"."""
        if not query.strip():
            raise ValueError("refusing to delete without a filter")
        self._call(f"{table}?{query}", method="DELETE",
                   extra={"Prefer": "return=minimal"})


# --------------------------------------------------------------------------- #
# Work selection
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    game_key: str
    cfg: Settings
    seed: int
    bot: str
    aux: float


def _settings_for(row: dict) -> tuple[Settings, int] | None:
    """Rebuild a posted setup, or None if the row can't be replayed.

    ``Settings.from_dict`` is tolerant on purpose — it clamps, defaults and pads
    so a stale or hand-edited *link* still loads to something playable. That is
    the wrong contract here: handed a non-dict it returns a plain ``Settings()``,
    and this worker would then post confident results for a default 18-node map
    under a real map's key. So the shape is checked first, and anything that
    isn't an object is skipped rather than defaulted.

    The seed comes from the setup itself where it has one — that is what the game
    encoded — and falls back to the column, which mapgen needs concretely either way.
    """
    raw = row.get("settings_json")
    if not isinstance(raw, dict):
        return None
    try:
        cfg = Settings.from_dict(raw)
    except Exception:  # noqa: BLE001 — one bad row must not stop the batch
        return None
    seed = cfg.seed if cfg.seed is not None else row.get("seed")
    if not isinstance(seed, int):
        return None
    cfg.seed = seed
    return cfg, seed


def pending(games: list[dict], done: dict[tuple[str, str], dict], roster: list[str],
            rev: str, aux_for: Callable[[str], float], *,
            recompute: bool = False, stale: bool = False) -> list[Job]:
    """The (map, bot) pairs still owing an answer, in the order given.

    ``done`` maps a computed pair to the stored row, so a cached answer can be
    judged on more than its existence:

    * a different ``engine_rev`` means the simulation has moved on — picked up by
      ``--stale``, deliberately opt-in, since a bot changing does not make the old
      number wrong so much as old.
    * a different ``aux`` means the row answers a *different question* — it is
      some other version of that bot. That one refills on an ordinary run, with
      no flag: leaving it would put two incomparable knowers side by side on one
      board. It only ever fires when ``REPLAY_AUX`` actually changes.
    """
    jobs: list[Job] = []
    for row in games:
        built = _settings_for(row)
        if built is None:
            print(f"  skipping {row.get('game_key')}: unreadable settings_json")
            continue
        cfg, seed = built
        for bot in roster:
            aux = aux_for(bot)
            previous = done.get((row["game_key"], bot))
            if previous is not None and not recompute:
                outdated = stale and previous.get("engine_rev", "") != rev
                # Absent on a row written before aux was recorded, which is the
                # 1.0 every bot was replayed at then.
                reprofiled = float(previous.get("aux", 1.0)) != float(aux)
                if not outdated and not reprofiled:
                    continue
            jobs.append(Job(row["game_key"], cfg, seed, bot, aux))
    return jobs


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game-key", default="",
                        help="replay only this map (what a webhook-triggered run passes)")
    parser.add_argument("--bots", nargs="*", default=None,
                        help="strategy names to replay (default: every registered one)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many replays (0 = no cap)")
    parser.add_argument("--max-turns", type=int, default=600,
                        help="give up on a map after this many turns (matches tests.sim)")
    parser.add_argument("--bot-timeout", type=float, default=0.0,
                        help="per-decision wall-clock budget in seconds. 0 (the default) "
                             "keeps every result reproducible; anything else makes a "
                             "slow turn depend on the runner, which is why a row that "
                             "hits it records the fact")
    parser.add_argument("--deadline-minutes", type=float, default=40.0,
                        help="stop starting new replays after this long, so a run "
                             "always finishes and the next one resumes")
    parser.add_argument("--budget-scale", type=float, default=BUDGET_SCALE,
                        help="multiply the bots' own per-decide wall-clock guards "
                             f"by this (default {BUDGET_SCALE:g}). They are sized for "
                             "the browser build; nothing waits on this job, and a "
                             "guard that never trips is what keeps a result "
                             "reproducible. 1 restores the in-game behaviour")
    parser.add_argument("--aux", nargs="*", default=[], metavar="BOT=VALUE",
                        help="override a bot's replay profile for this run, e.g. "
                             "--aux knower=8 (default: REPLAY_AUX in this file)")
    parser.add_argument("--recompute", action="store_true",
                        help="redo pairs that already have a row")
    parser.add_argument("--stale", action="store_true",
                        help="redo only rows computed by a different engine_rev")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and print, but write nothing back")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (the service_role key).",
              file=sys.stderr)
        return 2

    try:
        overrides = {}
        for item in args.aux:
            name, _, value = item.partition("=")
            overrides[name] = float(value)
    except ValueError:
        print(f"--aux wants BOT=VALUE pairs, got: {' '.join(args.aux)}", file=sys.stderr)
        return 2

    loaded = ai.load_models()
    roster = args.bots if args.bots is not None else ai.available_strategies()
    unknown = [bot for bot in roster if bot not in ai.STRATEGIES]
    if unknown:
        print(f"Unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2
    # Once, at startup and before any decide — the scale is process-wide.
    widened = ai.set_budget_scale(args.budget_scale)

    rev = engine_rev()
    aux_for = lambda bot: replay_aux(bot, overrides)  # noqa: E731
    profile = ", ".join(f"{bot}@{aux_for(bot):g}" for bot in roster if aux_for(bot) != 1.0)
    print(f"engine_rev {rev} · models {', '.join(loaded) or 'none'} · "
          f"roster {', '.join(roster)}")
    print(f"replay profile: {profile or 'every bot at its default aux'}"
          + (f" · wall-clock guards x{args.budget_scale:g} on {', '.join(widened)}"
             if widened and args.budget_scale != 1 else ""))

    api = Supabase(url, key)
    # A hand-written link's key is "j:"-prefixed (see schema.sql), so it is not
    # necessarily bare hex — quote it rather than trusting its shape.
    where = (f"&game_key=eq.{urllib.parse.quote(args.game_key, safe='')}"
             if args.game_key else "")
    # game_key breaks the ordering tie: first_seen_at alone is not unique, and a
    # paged read needs a total order or it can repeat and skip rows.
    games = api.select("games", f"select=game_key,seed,settings_json"
                                f"&order=first_seen_at.desc,game_key.asc{where}")
    computed = api.select("bot_scores",
                          "select=game_key,bot,engine_rev,aux&order=game_key.asc,bot.asc")
    done = {(row["game_key"], row["bot"]): row for row in computed}
    print(f"{len(games)} maps on the board, {len(done)} results cached")

    jobs = pending(games, done, roster, rev, aux_for,
                   recompute=args.recompute, stale=args.stale)
    if args.limit > 0:
        jobs = jobs[: args.limit]
    if not jobs:
        print("Nothing to do.")
        return 0
    print(f"{len(jobs)} replays to run")

    deadline = time.monotonic() + args.deadline_minutes * 60
    batch: list[dict] = []
    written = 0
    for index, job in enumerate(jobs, 1):
        if time.monotonic() > deadline:
            print(f"Deadline reached after {index - 1} replays; the next run resumes.")
            break
        result = sim.play_settings(job.cfg, job.seed, job.bot, aux=job.aux,
                                   max_turns=args.max_turns,
                                   bot_timeout=args.bot_timeout)
        outcome = f"won in {result.turns}" if result.won else f"lost after {result.turns}"
        tuned = f"@{job.aux:g}" if job.aux != 1.0 else ""
        print(f"  [{index}/{len(jobs)}] {job.game_key[:12]:<12} {job.bot + tuned:<14} "
              f"{outcome} turns, {result.lost} ships lost")
        batch.append({
            "game_key": job.game_key,
            "bot": job.bot,
            "won": result.won,
            "turns": result.turns,
            "lost": result.lost,
            "bot_timeouts": result.bot_timeouts,
            # The profile this answer belongs to. Recorded, not implied: a board
            # showing knower at search depth 12 beside a menu default of 1 owes
            # the reader that much, and `pending` reads it back to notice when
            # the policy has moved.
            "aux": job.aux,
            "aux_label": aux_note(job.bot, job.aux),
            "engine_rev": rev,
            # Sent rather than left to the column default: on an upsert PostgREST
            # only SETs the columns present in the payload, so an omitted
            # computed_at would keep the *original* row's timestamp on a redo.
            "computed_at": datetime.now(timezone.utc).isoformat(),
        })
        # Flush as we go so a run that is cut short still banks its work.
        if len(batch) >= 50:
            if not args.dry_run:
                api.upsert("bot_scores", batch)
            written += len(batch)
            batch = []

    if batch and not args.dry_run:
        api.upsert("bot_scores", batch)
    written += len(batch)
    print(f"{'Would write' if args.dry_run else 'Wrote'} {written} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
