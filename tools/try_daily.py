#!/usr/bin/env python3
"""Play what the daily deals on a weekday, to feel the generator and tune it.

Deals a map from a weekday's daily spec (the board's, or with ``--puzzle`` the
puzzle's) as ``tools/daily.py`` would, prints it,
and opens it in the desktop game's setup menu (``--no-menu`` starts it
straight away). Each run deals a fresh map; ``--seed`` deals one again. The
spec is read live: edit ``tools/daily_spec.json`` (``tools/puzzle_spec.json``),
or ``PLAYERS``, ``systems``, ``PUZZLE_OPPONENTS`` and ``turn_cap`` in
``tools/daily.py``, and the next run deals from the change. Reasons:
``docs/design/daily.md``.

    uv run python tools/try_daily.py sat                 # a fresh Saturday map, in the menu
    uv run python tools/try_daily.py sat --seed 7        # that one again
    uv run python tools/try_daily.py sat --probe         # only a map a probe won inside the cap
    uv run python tools/try_daily.py --day 2026-11-01    # that day's real daily (probed)
    uv run python tools/try_daily.py --week              # print one map per weekday, open none
    uv run python tools/try_daily.py thu --puzzle        # a fresh Thursday puzzle

A plain deal skips the probes, so it may be a map the worker would throw
away; ``--probe`` keeps dealing until a probe wins one, as the worker does,
recording each probe game under ``results/daily/try/`` (``--day`` records
under the day's own file, which the worker then reuses). Nothing is stored on
the board.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import dealer, replay, settings as settings_mod    # noqa: E402
from starconquest.settings import Settings    # noqa: E402
from tools import daily    # noqa: E402

TRY_DIR = daily.RESULTS / "try"
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def weekday(text: str) -> int:
    """Monday 0 ... Sunday 6, from "sat", "Saturday" or 1-7 (Monday 1)."""
    t = text.strip().lower()
    if t.isdigit() and 1 <= int(t) <= 7:
        return int(t) - 1
    matches = [w for w, name in enumerate(WEEKDAYS) if len(t) >= 2 and name.lower().startswith(t)]
    if len(matches) != 1:
        raise argparse.ArgumentTypeError(f"not a weekday: {text!r} (mon ... sun, or 1-7)")
    return matches[0]


def next_weekday(w: int, today: dt.date | None = None) -> dt.date:
    """The first day on or after ``today`` that falls on weekday ``w``."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return today + dt.timedelta(days=(w - today.weekday()) % 7)


def survey(s: Settings) -> str:
    """The lanes in turns, shortest / median / longest, as the lane_turns rule
    reads them."""
    lanes = settings_mod.lane_turns(settings_mod.lane_lengths(s), s.ship_ly_per_turn)
    return "no lanes" if lanes is None else "lanes {} / {} / {} turns (shortest / median / longest)".format(*lanes)


def report(day: dt.date, s: Settings, note: str = "", kind: str = "board") -> str:
    w = day.weekday()
    spec = (f"  spec: puzzle vs {daily.PUZZLE_OPPONENTS[w]}" if kind == "puzzle"
            else f"  spec: {daily.systems(w)[0]}-{daily.systems(w)[1]} systems, "
                 f"{daily.PLAYERS[w][0]}-{daily.PLAYERS[w][1]} players, each opponent drawn on its own")
    lines = [f"{day.strftime('%A')} ({day}){note}",
             f"  {dealer.describe(s)}",
             f"  {s.ship_ly_per_turn:g} ly/turn · {survey(s)} · cap {daily.turn_cap(w, kind)} turns",
             spec]
    return "\n".join(lines)


def deal(day: dt.date, trial: str, kind: str = "board") -> Settings:
    """One candidate from the day's spec on the trial's stream, unprobed."""
    return daily.candidates(day, limit=1, trial=trial, kind=kind)[0]


def save(s: Settings, name: str) -> Path:
    """The setup as an empty game log, which ``main.py --settings-from`` opens."""
    TRY_DIR.mkdir(parents=True, exist_ok=True)
    path = TRY_DIR / f"{name}.json"
    log = replay.GameLog(seed=s.seed if s.seed is not None else 0, settings=s.to_dict())
    path.write_text(json.dumps(log.to_dict(), indent=1), encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("weekday", nargs="?", type=weekday, help="mon ... sun (or 1-7, Monday 1)")
    parser.add_argument("--seed", default="", help="deal this trial again (each run prints its own)")
    parser.add_argument("--probe", action="store_true",
                        help="deal until a probe wins a map inside the day's cap, as the worker keeps")
    parser.add_argument("--day", type=dt.date.fromisoformat, help="that day's real daily, probed as the worker makes it")
    parser.add_argument("--week", action="store_true", help="print one fresh map per weekday and open none")
    parser.add_argument("--puzzle", action="store_true", help="the daily puzzle, not the board")
    parser.add_argument("--no-play", action="store_true", help="print the map, don't open the game")
    parser.add_argument("--no-menu", action="store_true", help="start the game straight away")
    args = parser.parse_args(argv)
    if [args.weekday is not None, args.day is not None, args.week].count(True) != 1:
        parser.error("give one of: a weekday, --day, --week")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trial = args.seed or str(random.randrange(10 ** 6))
    kind = "puzzle" if args.puzzle else "board"

    if args.week:
        monday = next_weekday(0)
        for w in range(7):
            day = monday + dt.timedelta(days=w)
            s = deal(day, trial, kind)
            print(report(day, s, kind=kind))
        print(f"(--seed {trial} deals these again)")
        return 0

    if args.day:
        day, trial, note = args.day, "", " · the real daily"
    else:
        day, note = next_weekday(args.weekday), f" · trial --seed {trial}"

    if args.probe or args.day:
        print(f"probing {day.strftime('%A')}'s candidates (each game recorded as it finishes)", flush=True)
        row = daily.make(day, out=lambda line: print(line, flush=True), trial=trial, kind=kind)
        if row is None:
            print(f"no candidate of {daily.MAX_CANDIDATES} was won inside {daily.turn_cap(day.weekday(), kind)} turns",
                  file=sys.stderr)
            return 1
        s = Settings.from_dict(row["settings"])
    else:
        s = deal(day, trial, kind)
    print(report(day, s, note, kind))

    if args.no_play:
        return 0
    name = day.isoformat() if args.day else f"{WEEKDAYS[day.weekday()][:3].lower()}-{trial}"
    name = f"puzzle-{name}" if kind == "puzzle" else name
    path = save(s, name)
    print(f"opening {path.relative_to(ROOT)}", flush=True)
    cmd = [sys.executable, str(ROOT / "main.py"), "--settings-from", str(path)]
    return subprocess.call(cmd + (["--no-menu"] if args.no_menu else []), cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
