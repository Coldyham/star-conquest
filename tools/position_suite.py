#!/usr/bin/env python3
"""Measure the roster against positions from real games, not against itself.

    uv run python tools/position_suite.py                     # local games/ dir
    uv run python tools/position_suite.py --bots marshal knower
    uv run python tools/position_suite.py --every 10 --skip-last 15
    uv run python tools/position_suite.py --supabase           # the shared corpus
    uv run python tools/position_suite.py --csv out.csv        # every row, for later

Every bot measurement in ``docs/bot-design.md`` is a bot against another bot, and
that is a real limit rather than a stylistic one: a roster playing itself only
ever visits positions bots create. A stored replay is a position a *person*
built — a different shape, with different mistakes in it — and it comes with a
free baseline, because we know exactly how long that person then took to finish.

So: rebuild the board as it stood after N turns of a recorded match, hand the
seat to a bot, and let it play the rest. Did it win, and did it win sooner than
the player did? Sample a position every few turns and one game becomes dozens of
paired trials instead of one number.

The losses and the abandoned games matter as much as the wins, and are the reason
the game stores more than posted scores (`starconquest/upload.py`). There is no
human finish to compare against in those, but "can any bot still take this board?"
is a fair question, a hard one, and one no leaderboard score can ever pose —
only wins are postable.

Where the games come from:

* by default the local ``games/`` dir, which is every match played on this
  machine and needs no credentials at all;
* ``--supabase`` reads the shared corpus instead (``SUPABASE_URL`` and
  ``SUPABASE_SERVICE_KEY``, the same pair the other two workers use — ``game_logs``
  has no public read, so the service_role key is the only way in).

Read the output as a *direction*, never as a verdict. The sample is however many
games happen to exist, drawn from whoever played them, on whatever setups they
chose — see "Sweep the speed and node knobs" in bot-design for why a measurement
taken in one corner of the parameter space says nothing about the others. What
this is good for is finding where a bot is worse than a person, which is a
hypothesis worth a proper paired sweep afterwards.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay  # noqa: E402
from tests import sim  # noqa: E402 — the shared headless harness

# A position is only worth playing if there is a game left to play from it. The
# defaults sample every 20 turns and drop the last 20, where a decided match is a
# formality every bot completes identically.
DEFAULT_EVERY = 20
DEFAULT_SKIP_LAST = 20


def local_logs(directory: Path | None = None) -> list[replay.GameLog]:
    """Every replayable match saved on this machine, newest first.

    Version-1 logs are skipped for the same reason ``replay.latest_log`` skips
    them: they recorded the human's orders alone and re-ran the AI, so they cannot
    be replayed faithfully and the position they rebuild is not the one that was
    played (see ``replay``'s module docstring).
    """
    paths = replay.list_logs() if directory is None else sorted(
        directory.glob("game_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in paths:
        try:
            log = replay.load(path)
        except (OSError, ValueError):
            continue
        if log.version >= replay.FORMAT_VERSION and log.turn_count:
            out.append(log)
    return out


def supabase_logs(limit: int = 0) -> list[replay.GameLog]:
    """The shared corpus, newest first, longest upload per match.

    Imported lazily so the local path — the one that needs no credentials — does
    not depend on the worker's HTTP client at all.
    """
    from tools.bot_replay import Supabase          # noqa: PLC0415 — see above
    from tools.verify_scores import best_logs      # noqa: PLC0415

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (the service_role key).")
    rows = Supabase(url, key).select(
        "game_logs", "select=id,match_id,turns,log&order=id.desc")
    logs = []
    for row in best_logs(rows).values():
        try:
            logs.append(replay.GameLog.decode(row["log"]))
        except ValueError:
            continue                                # one bad blob, not a dead run
    logs.sort(key=lambda log: log.turn_count, reverse=True)
    return logs[:limit] if limit > 0 else logs


def summarise(results: list[sim.PositionResult]) -> dict:
    """One bot's rows, rolled up into the three numbers worth reading.

    They answer different questions and must not be blurred together:

    * ``beaten``/``compared`` — of the positions where *both* the bot and the
      person finished, how often was the bot faster. This is the only paired
      comparison here, and the only one with a baseline.
    * ``recovered``/``recoverable`` — of the positions from games the person did
      *not* win, how often did the bot take the board anyway. No baseline, but the
      most interesting question the corpus can pose.
    * ``median_gain`` — turns saved against the person, over the compared
      positions. Median rather than mean: one 400-turn grind would otherwise
      decide the number on its own.
    """
    compared = [r for r in results if r.gain is not None]
    recoverable = [r for r in results if not r.human_won]
    gains = [r.gain for r in compared]
    return {
        "positions": len(results),
        "won": sum(1 for r in results if r.won),
        "compared": len(compared),
        "beaten": sum(1 for g in gains if g < 0),
        "median_gain": statistics.median(gains) if gains else None,
        "recoverable": len(recoverable),
        "recovered": sum(1 for r in recoverable if r.won),
        "timeouts": sum(1 for r in results if r.timed_out),
        "bot_timeouts": sum(r.bot_timeouts for r in results),
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    --"


def report(by_bot: dict[str, list[sim.PositionResult]]) -> None:
    print()
    print(f"{'bot':<14}{'positions':>10}{'won':>8}{'faster':>18}"
          f"{'median gain':>13}{'recovered':>18}{'timeouts':>10}")
    for bot, rows in by_bot.items():
        s = summarise(rows)
        gain = "--" if s["median_gain"] is None else f"{s['median_gain']:+.0f}"
        faster = f"{_pct(s['beaten'], s['compared'])} of {s['compared']}"
        recovered = f"{_pct(s['recovered'], s['recoverable'])} of {s['recoverable']}"
        print(f"{bot:<14}{s['positions']:>10}{_pct(s['won'], s['positions']):>8}"
              f"{faster:>18}{gain:>13}{recovered:>18}{s['timeouts']:>10}")
    print()
    print("faster    = of positions both finished, how often the bot took fewer turns")
    print("recovered = of positions from games the player lost, how often a bot won")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bots", nargs="+", default=None,
                        help="strategies to measure (default: every registered one)")
    parser.add_argument("--every", type=int, default=DEFAULT_EVERY,
                        help=f"sample a position every N turns (default {DEFAULT_EVERY})")
    parser.add_argument("--skip-last", type=int, default=DEFAULT_SKIP_LAST,
                        help="drop the last N turns of each game, where a decided "
                             f"match is a formality (default {DEFAULT_SKIP_LAST})")
    parser.add_argument("--max-turns", type=int, default=600,
                        help="turns allowed from each position (default 600)")
    parser.add_argument("--games", type=int, default=0,
                        help="use at most this many games (0 = all)")
    parser.add_argument("--dir", type=Path, default=None,
                        help="read logs from this dir instead of the game's games/")
    parser.add_argument("--supabase", action="store_true",
                        help="read the shared corpus instead of local games")
    parser.add_argument("--bot-timeout", type=float, default=0.0,
                        help="seconds per decide() before a seat forfeits the turn")
    parser.add_argument("--csv", type=Path, default=None,
                        help="also write every row here, for analysis elsewhere")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    loaded = ai.load_models()
    roster = args.bots or ai.available_strategies()

    logs = supabase_logs(args.games) if args.supabase else local_logs(args.dir)
    if args.games > 0:
        logs = logs[: args.games]
    if not logs:
        where = "the shared corpus" if args.supabase else str(args.dir or replay.GAMES_DIR)
        print(f"No replayable games in {where}.", file=sys.stderr)
        return 1

    plan = [(log, turn) for log in logs
            for turn in sim.positions(log, args.every, args.skip_last)]
    print(f"models {', '.join(loaded) or 'none'}")
    print(f"{len(logs)} games · {len(plan)} positions · {len(roster)} bots "
          f"= {len(plan) * len(roster)} runs")

    by_bot: dict[str, list[sim.PositionResult]] = {bot: [] for bot in roster}
    started = time.time()
    for index, (log, turn) in enumerate(plan, 1):
        for bot in roster:
            try:
                by_bot[bot].append(
                    sim.play_from(log, turn, bot, max_turns=args.max_turns,
                                  bot_timeout=args.bot_timeout))
            except Exception as err:  # noqa: BLE001 — one bad position, not a dead run
                print(f"  {log.match_id[:8]}@{turn} {bot}: skipped ({err})")
        print(f"  [{index}/{len(plan)}] {log.match_id[:8]} turn {turn}"
              f" · {time.time() - started:.0f}s", end="\r", flush=True)
    print(" " * 70, end="\r")

    report(by_bot)
    if args.csv:
        rows = [asdict(r) | {"gain": r.gain} for rows in by_bot.values() for r in rows]
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
