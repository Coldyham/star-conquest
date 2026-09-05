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

PAGE = 1000          # PostgREST's own default ceiling; page rather than assume
POST_TIMEOUT = 60    # seconds, per HTTP call
RETRIES = 4          # network blips on a CI runner are ordinary


def engine_rev() -> str:
    """A digest of everything that determines a replay's outcome.

    Stored on each row for provenance and read back by ``--stale``. A git SHA
    would be the obvious choice and is the wrong one: it moves on every commit,
    so a CSS change would invalidate the whole board. This moves when — and only
    when — the simulation or a bot does.
    """
    digest = hashlib.blake2s(digest_size=8)
    paths = [ROOT / "starconquest" / f"{name}.py" for name in _OUTCOME_MODULES]
    paths += sorted((ROOT / "models").glob("*.py"))
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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


# --------------------------------------------------------------------------- #
# Work selection
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    game_key: str
    cfg: Settings
    seed: int
    bot: str


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


def pending(games: list[dict], done: dict[tuple[str, str], str], roster: list[str],
            rev: str, *, recompute: bool = False, stale: bool = False) -> list[Job]:
    """The (map, bot) pairs still owing an answer, in the order given.

    ``done`` maps a computed pair to the ``engine_rev`` that produced it, so
    ``--stale`` can pick out rows the simulation has moved on from without
    recomputing the ones it hasn't.
    """
    jobs: list[Job] = []
    for row in games:
        built = _settings_for(row)
        if built is None:
            print(f"  skipping {row.get('game_key')}: unreadable settings_json")
            continue
        cfg, seed = built
        for bot in roster:
            previous = done.get((row["game_key"], bot))
            if previous is not None and not recompute and not (stale and previous != rev):
                continue
            jobs.append(Job(row["game_key"], cfg, seed, bot))
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

    loaded = ai.load_models()
    roster = args.bots if args.bots is not None else ai.available_strategies()
    unknown = [bot for bot in roster if bot not in ai.STRATEGIES]
    if unknown:
        print(f"Unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2
    rev = engine_rev()
    print(f"engine_rev {rev} · models {', '.join(loaded) or 'none'} · "
          f"roster {', '.join(roster)}")

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
                          "select=game_key,bot,engine_rev&order=game_key.asc,bot.asc")
    done = {(row["game_key"], row["bot"]): row.get("engine_rev", "") for row in computed}
    print(f"{len(games)} maps on the board, {len(done)} results cached")

    jobs = pending(games, done, roster, rev,
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
        result = sim.play_settings(job.cfg, job.seed, job.bot,
                                   max_turns=args.max_turns,
                                   bot_timeout=args.bot_timeout)
        outcome = f"won in {result.turns}" if result.won else f"lost after {result.turns}"
        print(f"  [{index}/{len(jobs)}] {job.game_key[:12]:<12} {job.bot:<12} "
              f"{outcome} turns, {result.lost} ships lost")
        batch.append({
            "game_key": job.game_key,
            "bot": job.bot,
            "won": result.won,
            "turns": result.turns,
            "lost": result.lost,
            "bot_timeouts": result.bot_timeouts,
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
