#!/usr/bin/env python3
"""Read every posted replay into `playstyle_readings`, for the board's Playstyle panel.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...
    uv run python tools/playstyle_worker.py              # read what's unread or stale
    uv run python tools/playstyle_worker.py --prune      # ...and drop rows no replay backs
    uv run python tools/playstyle_worker.py --dry-run    # read, write nothing

One row per match_id, from `public_replays` only: the logs a posted score
already points at. A row names nobody; the board finds a player's rows through
their own `scores.match_id`. `rev` is `playstyle.reading_rev()`, and a row from
other code is read again while its log still replays (`GameLog.is_current`).
A log that no longer replays keeps the row it has. `--prune` deletes a row
whose match is no longer in `public_replays`.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import replay
from tools.bot_replay import MISSING_CREDENTIALS, Supabase, credentials
from tools.playstyle import load, reading_rev, readings

MIN_HAND = 10
BATCH = 25


def replayable(rows: list[dict], min_hand: int) -> dict[str, replay.GameLog]:
    """{match_id: log} for each public replay that decodes, replays under
    today's rules, and was played by hand for at least `min_hand` turns."""
    out = {}
    for row in rows:
        match_id = str(row.get("match_id") or "")
        if not match_id or match_id in out:
            continue
        try:
            log = replay.GameLog.decode(row["log"])
        except (ValueError, KeyError, TypeError):
            continue
        if log.is_current and log.hand_turns >= min_hand:
            out[match_id] = log
    return out


def pending(logs: dict[str, replay.GameLog], stored: dict[str, str], rev: str) -> list[str]:
    """Matches with no row, or a row from other code."""
    return sorted(m for m in logs if stored.get(m) != rev)


def orphans(public: set[str], stored: dict[str, str]) -> list[str]:
    """Stored rows whose match no posted score points at any more."""
    return sorted(m for m in stored if m not in public)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-hand", type=int, default=MIN_HAND,
                        help="skip a game with fewer turns played by hand")
    parser.add_argument("--prune", action="store_true",
                        help="delete rows whose match is no longer a public replay")
    parser.add_argument("--deadline-minutes", type=float, default=0,
                        help="stop reading after this long (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url, key = credentials()
    if not url or not key:
        print(MISSING_CREDENTIALS, file=sys.stderr)
        return 2
    api = Supabase(url, key)
    rev = reading_rev()
    print(f"reading_rev {rev}")

    rows = api.select("public_replays", "select=match_id,log&order=match_id.asc")
    public = {str(r.get("match_id") or "") for r in rows} - {""}
    logs = replayable(rows, args.min_hand)
    stored = {r["match_id"]: r["rev"] for r in
              api.select("playstyle_readings", "select=match_id,rev&order=match_id.asc")}
    todo = pending(logs, stored, rev)
    print(f"{len(public)} public replays, {len(logs)} readable, {len(stored)} stored, "
          f"{len(todo)} to read")

    load()
    deadline = time.monotonic() + args.deadline_minutes * 60 if args.deadline_minutes else None
    batch: list[dict] = []
    done = 0
    for match_id in todo:
        if deadline is not None and time.monotonic() > deadline:
            print("deadline reached; the next run picks up the rest")
            break
        try:
            row = {"match_id": match_id, "rev": rev, "readings": readings(logs[match_id])}
        except Exception as err:            # one bad log must not stop the rest
            print(f"  {match_id}: {type(err).__name__}: {err}")
            continue
        batch.append(row)
        done += 1
        if len(batch) >= BATCH:
            if not args.dry_run:
                api.upsert("playstyle_readings", batch)
            batch = []
    if batch and not args.dry_run:
        api.upsert("playstyle_readings", batch)
    print(f"read {done}" + (" (dry run, nothing written)" if args.dry_run else ""))

    if args.prune:
        gone = orphans(public, stored)
        if not args.dry_run:
            for match_id in gone:
                api.delete("playstyle_readings", f"match_id=eq.{match_id}")
        print(f"pruned {len(gone)} row(s) no public replay backs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
