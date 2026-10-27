#!/usr/bin/env python3
"""Measure the roster against positions from real games, not against itself.

    uv run python tools/position_suite.py                     # local games/ dir
    uv run python tools/position_suite.py --bots marshal knower
    uv run python tools/position_suite.py --every 10 --skip-last 15
    uv run python tools/position_suite.py --supabase           # the shared corpus
    uv run python tools/position_suite.py --supabase --unwon   # only games not won
    uv run python tools/position_suite.py --csv out.csv        # every row, for later

Every bot measurement in ``docs/design/bots.md`` is a bot against another bot, and
that is a real limit rather than a stylistic one: a roster playing itself only
ever visits positions bots create. A stored replay is a position a *person*
built — a different shape, with different mistakes in it — and it comes with a
free baseline, because we know exactly how long that person then took to finish.

So: rebuild the board as it stood after N turns of a recorded match, hand the
seat to a bot, and let it play the rest. Did it win, and did it win sooner than
the player did? Sample a position every few turns and one game becomes dozens of
paired trials instead of one number.

The losses and the abandoned games matter as much as the wins, and are the reason
the game stores more than posted scores (`starconquest/share.py`). There is no
human finish to compare against in those, but "can any bot still take this board?"
is a fair question, a hard one, and one no leaderboard score can ever pose —
only wins are postable. ``--unwon`` keeps only those. A won game is sampled from
its opening; one the player did not win is sampled back from its last board,
the latest board the corpus holds of a game the player stopped (see ``plan``).

Each run is appended to ``results/position_suite/<rev>.jsonl`` the moment it
finishes (``--record`` to name another file), and a rerun plays only the runs
that file lacks. ``rev`` is ``sim.play_rev``, a digest of the code that decides a
game, so any change to it starts a fresh file. Runs play on a worker pool
(``--workers``).

Where the games come from:

* by default the local ``games/`` dir, which is every match played on this
  machine and needs no credentials at all;
* ``--supabase`` reads the shared corpus instead (``SUPABASE_URL`` and
  ``SUPABASE_SECRET_KEY``, the same pair the other two workers use — ``game_logs``
  has no public read, so a secret key is the only way in).

Read the output as a *direction*, never as a verdict. The sample is however many
games happen to exist, drawn from whoever played them, on whatever setups they
chose — see "Lane length across the parameter space" in docs/design/bots.md for why a measurement
taken in one corner of the parameter space says nothing about the others. What
this is good for is finding where a bot is worse than a person, which is a
hypothesis worth a proper paired sweep afterwards.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
import os
import signal
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay
from starconquest.settings import Settings
from tests import sim

RESULTS = ROOT / "results" / "position_suite"

# A position is only worth playing if there is a game left to play from it. The
# defaults sample every 20 turns and drop the last 20, where a decided match is a
# formality every bot completes identically.
DEFAULT_EVERY = 20
DEFAULT_SKIP_LAST = 20


class _Cancelled(BaseException):
    """Raised from the SIGTERM handler below, never caught as an ordinary error.

    A CI timeout kills the process a fixed, short grace period after asking it to
    stop (GitHub Actions: SIGINT then SIGTERM, ~7.5s apart before SIGKILL), and a
    single position can cost over a hundred seconds — so waiting for the current
    position to finish before checking a flag can miss that whole window. Raising
    immediately, the same way Python's own SIGINT-to-KeyboardInterrupt already
    does, is what makes a slow position interruptible rather than merely the gaps
    between them. Subclassing ``BaseException`` (not ``Exception``) keeps it out
    of ``except Exception`` — a genuine cancellation must never be logged and
    swallowed as "one bad position, skipped".
    """


def replayable(log: replay.GameLog) -> bool:
    """Whether ``log`` rebuilds the positions that were really played.

    Version-1 logs recorded the human's orders alone and re-ran the AI, so they
    cannot be replayed faithfully (see ``replay``'s module docstring), and a log
    stamped under rules the engine has since moved past (``GameLog.is_current``)
    can land on a different board — the same reasons ``replay.latest_log`` skips
    both. Either way the position ``sim.play_from`` would branch from is not the
    one that was reached.
    """
    return log.version >= replay.FORMAT_VERSION and log.turn_count > 0 and log.is_current


def local_logs(directory: Path | None = None) -> list[replay.GameLog]:
    """Every replayable match saved on this machine, newest first."""
    paths = replay.list_logs() if directory is None else sorted(
        directory.glob("game_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in paths:
        try:
            log = replay.load(path)
        except (OSError, ValueError):
            continue
        if replayable(log):
            out.append(log)
    return out


def supabase_logs(limit: int = 0) -> list[replay.GameLog]:
    """The shared corpus's replayable matches, longest first, longest upload per
    match.

    Imported lazily so the local path — the one that needs no credentials — does
    not depend on the worker's HTTP client at all.
    """
    from tools.bot_replay import (
        MISSING_CREDENTIALS,
        Supabase,
        credentials,
    )
    from tools.verify_scores import best_logs

    url, key = credentials()
    if not url or not key:
        raise SystemExit(MISSING_CREDENTIALS)
    rows = Supabase(url, key).select(
        "game_logs", "select=id,match_id,turns,log&order=id.desc")
    logs = []
    for row in best_logs(rows).values():
        try:
            log = replay.GameLog.decode(row["log"])
        except ValueError:
            continue                                # one bad blob, not a dead run
        if replayable(log):
            logs.append(log)
    logs.sort(key=lambda log: log.turn_count, reverse=True)
    return logs[:limit] if limit > 0 else logs


def person_won(log: replay.GameLog) -> bool | None:
    """Whether the person in ``log`` won it, or ``None`` when nobody held a seat.

    The person is ``replay.HUMAN_SEAT``, from the start or from the first turn
    they ended themselves (``replay.reconstruct``); an autoplay demo nobody took
    over has no seat to hand a bot.
    """
    if (Settings.from_dict(log.settings).autoplay
            and all(log.turn_is_ai(i) for i in range(log.turn_count))):
        return None
    return log.finished and log.winner == replay.HUMAN_SEAT


def plan(log: replay.GameLog, won: bool, every: int, skip_last: int) -> list[int]:
    """Which turns of ``log`` to hand over.

    A won game is sampled from the opening (``sim.positions``), less its last
    ``skip_last`` turns, where the match is decided and every bot finishes alike.
    A game the person did not win is sampled back from its end instead, because
    there the end is the point: the last board an abandoned game reached is the
    nearest the corpus comes to where its player gave up. A shared game uploads
    every ``share.CHECKPOINT_TURNS``, so that board can be that many turns short
    of where they stopped; a local log runs to the last turn played. A lost game
    starts a turn short of its end, the last board that was still being played.
    """
    if won:
        return sim.positions(log, every, skip_last)
    last = log.turn_count - 1 if log.finished else log.turn_count
    return list(range(last, -1, -max(1, every)))[::-1]


def run_key(setup: dict, log: replay.GameLog, turn: int, bot: str, aux: float) -> str:
    """One run's identity in the record. The log's length and winner are in it
    because a match uploaded again after more play is the same ``match_id`` with
    a different baseline."""
    return json.dumps([setup, log.match_id, log.turn_count, log.winner, turn, bot, aux],
                      sort_keys=True)


def load_record(path: Path) -> dict[str, sim.PositionResult]:
    """Every run recorded in ``path``, the first of any written twice."""
    done: dict[str, sim.PositionResult] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue                    # a line cut short by a killed run
            done.setdefault(row["key"], sim.PositionResult(**row["result"]))
    return done


def _worker(budget_scale: float) -> None:
    # The parent owns stopping: it catches the interrupt and terminates the pool.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    ai.load_models()
    ai.set_budget_scale(budget_scale)


def _play(job) -> tuple[int, sim.PositionResult | None, str]:
    index, log, turn, bot, aux, max_turns, bot_timeout = job
    try:
        return index, sim.play_from(log, turn, bot, aux=aux, max_turns=max_turns,
                                    bot_timeout=bot_timeout), ""
    except Exception as err:  # noqa: BLE001 — one bad position, not a dead run
        return index, None, str(err)


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
    print("recovered = of positions from games the player did not win, how often a bot won")


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
    parser.add_argument("--unwon", action="store_true",
                        help="only games the player did not win: abandoned or lost")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 1) - 2),
                        help="worker processes (default every core but two)")
    parser.add_argument("--record", type=Path, default=None,
                        help="the JSON-lines file each run is appended to, and resumed "
                             "from (default results/position_suite/<rev>.jsonl)")
    parser.add_argument("--bot-timeout", type=float, default=0.0,
                        help="seconds per decide() before a seat forfeits the turn")
    parser.add_argument("--aux", nargs="*", default=[], metavar="BOT=VALUE",
                        help="override a bot's aux for this run, e.g. --aux knower=8 "
                             "(default: tools.bot_replay.replay_aux, the same profile "
                             "the leaderboard's bot column replays each bot at)")
    parser.add_argument("--budget-scale", type=float, default=None,
                        help="multiply the bots' own per-decide wall-clock guards by "
                             "this (default: tools.bot_replay.BUDGET_SCALE). Sized for "
                             "the browser build; nothing here waits on a frame, and a "
                             "guard that never trips is what keeps a result "
                             "reproducible. 1 restores the in-game behaviour")
    parser.add_argument("--csv", type=Path, default=None,
                        help="also write every row here, for analysis elsewhere")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Lazy, and read even off the local path: this is a batch tool with nothing
    # waiting on a frame, so — same reasoning as tools/bot_replay.py — a bot's own
    # wall-clock guard is worth lifting out of the way, and each bot is worth
    # measuring at its best profile rather than at its untuned default.
    from tools.bot_replay import BUDGET_SCALE, replay_aux

    args = parse_args(argv)
    try:
        overrides = dict(item.split("=", 1) for item in args.aux)
        overrides = {bot: float(value) for bot, value in overrides.items()}
    except ValueError:
        print(f"--aux wants BOT=VALUE pairs, got: {' '.join(args.aux)}", file=sys.stderr)
        return 2

    loaded = ai.load_models()
    roster = args.bots or ai.available_strategies()
    aux_for = lambda bot: replay_aux(bot, overrides)
    profile = ", ".join(f"{bot}@{aux_for(bot):g}" for bot in roster if aux_for(bot) != 1.0)
    scale = BUDGET_SCALE if args.budget_scale is None else args.budget_scale
    widened = ai.set_budget_scale(scale)

    logs = supabase_logs() if args.supabase else local_logs(args.dir)
    games = [(log, won) for log in logs
             if (won := person_won(log)) is not None and not (args.unwon and won)]
    if args.games > 0:
        games = games[: args.games]
    if not games:
        where = "the shared corpus" if args.supabase else str(args.dir or replay.GAMES_DIR)
        print(f"No replayable games in {where}.", file=sys.stderr)
        return 1

    positions = [(log, turn) for log, won in games
                 for turn in plan(log, won, args.every, args.skip_last)]
    jobs = [(log, turn, bot) for log, turn in positions for bot in roster]
    setup = {"rev": sim.play_rev(), "max_turns": args.max_turns,
             "bot_timeout": args.bot_timeout, "budget_scale": scale}
    keys = [run_key(setup, log, turn, bot, aux_for(bot)) for log, turn, bot in jobs]
    record = args.record or RESULTS / f"{setup['rev']}.jsonl"
    record.parent.mkdir(parents=True, exist_ok=True)
    done = load_record(record)
    todo = [i for i, key in enumerate(keys) if key not in done]
    workers = max(1, min(args.workers, len(todo)))

    print(f"models {', '.join(loaded) or 'none'}")
    print(f"replay profile: {profile or 'every bot at its default aux'}"
          + (f" · wall-clock guards x{scale:g} on {', '.join(widened)}"
             if widened and scale != 1 else ""))
    print(f"{len(games)} games · {len(positions)} positions · {len(roster)} bots "
          f"= {len(jobs)} runs · {len(jobs) - len(todo)} recorded, playing {len(todo)} "
          f"on {workers} worker(s) -> {record}", flush=True)

    def _cancel(signum, frame):
        raise _Cancelled()

    signal.signal(signal.SIGTERM, _cancel)

    payload = ((i, *jobs[i], aux_for(jobs[i][2]), args.max_turns, args.bot_timeout)
               for i in todo)
    pool = (multiprocessing.Pool(workers, initializer=_worker, initargs=(scale,))
            if workers > 1 else None)
    results = pool.imap_unordered(_play, payload) if pool is not None else map(_play, payload)
    started, step = time.monotonic(), max(1, len(todo) // 20)
    finished = 0
    stopped = False
    try:
        with record.open("a") as out:
            for finished, (i, result, error) in enumerate(results, 1):
                log, turn, bot = jobs[i]
                if result is None:
                    print(f"  {log.match_id[:8]}@{turn} {bot}: skipped ({error})", flush=True)
                else:
                    done[keys[i]] = result
                    out.write(json.dumps({"key": keys[i], "result": asdict(result)}) + "\n")
                    out.flush()
                if finished % step == 0 or finished == len(todo):
                    print(f"  {finished}/{len(todo)} runs · {time.monotonic() - started:.0f}s",
                          flush=True)
    except (KeyboardInterrupt, _Cancelled):
        # A CI timeout, or a person losing patience — either way every run that
        # finished is already in the record, and the report covers them.
        stopped = True
    finally:
        if pool is not None:
            pool.terminate()
    if stopped:
        print(f"stopped after {finished}/{len(todo)} runs — reporting on what finished")

    by_bot: dict[str, list[sim.PositionResult]] = {bot: [] for bot in roster}
    for (_log, _turn, bot), key in zip(jobs, keys):
        if key in done:
            by_bot[bot].append(done[key])
    report(by_bot)
    if args.csv:
        rows = [asdict(r) | {"gain": r.gain} for rows in by_bot.values() for r in rows]
        if rows:
            with open(args.csv, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(f"wrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
