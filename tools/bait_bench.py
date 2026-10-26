#!/usr/bin/env python3
"""knower at Search offered bait openings in a free-for-all, paired against stock.

    uv run python tools/bait_bench.py --seeds 40
    uv run python tools/bait_bench.py --report

Baiting is leaving a system weak so that two rivals strike it together and fight
each other first: a pile-up folds its attackers among themselves, strongest
first, and the survivor meets the garrison last (`combat.resolve_arrival`).
Turns resolve simultaneously, so a rival sees a garrison thinned this turn only
on the next one, and only a look-ahead can price the move. That makes it an
opening for knower's Search, whose rollouts play every rival it can run with
that rival's own code.

The ``bait`` arm adds openings to the seat under test's Search (`_candidates`):
for each of our systems bordering two or more rivals (a wall), up to ``WALLS``
of them by the rival ships next door, the default plan with whatever of the
wall's garrison it leaves at home moved to the nearest friendly neighbour that
is not a wall itself. The search keeps the default unless a bait line's rollout
scores better. Every other seat plays as it ships, and the patch is keyed by
pid.

Two posted configs (`tools/pileup_bench.SETUPS`: five seats at 31 systems and
3 ly/turn, four at 23 and 8 ly/turn), two fields of opponents (``spread``:
rusherplus, heuristic, marshal, thinker; ``strong``: marshal, actuary at
Field, knower at Predict, marshal; a four-seat game takes the first three), the
seat under test turning with the seed, and each arm paired with stock on the
same seat and seed as `tools/pileup_bench.py` pairs them (beats, Δwon, Δturns,
place). Each row records how many of the seat's searches were offered a bait
line, how many picked one, and any error the patch raised (knower swallows a
search's exception and plays its plain plan, so an error would read as a null).

Guards lifted (``ai.set_budget_scale(inf)``). Rows append to
``results/bait/<label>.jsonl`` as each game finishes; a rerun plays only the
games missing.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest.model import Order  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402
from tools.pileup_bench import SETUPS, load_rows, paired  # noqa: E402
from tools.win_turns import git_hint, play_rev  # noqa: E402

RESULTS = ROOT / "results" / "bait"
SEED_BASE = 8_400_000
MAX_TURNS = 600
STOP = 2                                    # Search
ARMS = ("stock", "bait")
WALLS = 2
LINEUPS = {
    "spread": (("rusherplus", None), ("heuristic", None), ("marshal", None), ("thinker", None)),
    "strong": (("marshal", None), ("actuary", 1.0), ("knower", 1.0), ("marshal", None)),
}

_patched = None
_bait: frozenset[int] = frozenset()
_ids: set[int] = set()
_fires: dict[str, int] = {}


def walls(state, pid) -> list[int]:
    """Our systems bordering two or more rivals, most rival ships next door first."""
    out = []
    for s in state.systems.values():
        if s.owner_id != pid:
            continue
        rivals = {state.systems[n].owner_id for n in s.neighbors} - {0, pid}
        if len(rivals) >= 2:
            near = sum(state.systems[n].ships for n in s.neighbors
                       if state.systems[n].owner_id in rivals)
            out.append((-near, s.id))
    return [sid for _n, sid in sorted(out)]


def bait_plan(state, pid, base: list, wall: int, wall_set) -> list | None:
    """``base`` with the garrison it leaves on ``wall`` moved out, or None when
    nothing is left there or there is nowhere friendly to put it."""
    s = state.systems[wall]
    left = s.ships - sum(o.ships for o in base if o.source_id == wall)
    if left <= 0:
        return None
    homes = [n for n in s.neighbors
             if state.systems[n].owner_id == pid and n not in wall_set]
    if not homes:
        return None
    dest = min(homes, key=lambda n: (state.travel_turns(wall, n) or 99,
                                     -state.systems[n].ships, n))
    plan, merged = [], False
    for o in base:
        if o.source_id == wall and o.dest_id == dest:
            o, merged = Order(pid, wall, dest, o.ships + left), True
        plan.append(o)
    if not merged:
        plan.append(Order(pid, wall, dest, left))
    return plan


def _patch():
    global _patched
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    if _patched is k:
        return
    candidates, search = k._candidates, k._search

    def _candidates(state, pid, orc, base):
        yield from candidates(state, pid, orc, base)
        if pid not in _bait:
            return
        try:
            ws = walls(state, pid)
            offered = 0
            for w in ws[:WALLS]:
                plan = bait_plan(state, pid, base, w, frozenset(ws))
                if plan is not None:
                    _ids.add(id(plan))
                    offered += 1
                    yield plan
            _fires["offered"] += offered > 0
            _fires["lines"] += offered
        except Exception:                 # noqa: BLE001 — counted, never silent
            _fires["errors"] += 1

    def _search(state, pid, orc, turns, deadline):
        if pid not in _bait:
            return search(state, pid, orc, turns, deadline)
        _ids.clear()
        _fires["searches"] += 1
        root = search(state, pid, orc, turns, deadline)
        _fires["picked"] += id(root) in _ids
        return root

    k._candidates, k._search = _candidates, _search
    _patched = k


def seating(setup: str, lineup: str, i: int) -> tuple[int, list]:
    n = SETUPS[setup]["players"]
    test = i % n + 1
    rest = iter(LINEUPS[lineup][:n - 1])
    return test, [None if pid == test else next(rest) for pid in range(1, n + 1)]


def play_one(setup: str, lineup: str, arm: str, i: int) -> dict:
    global _bait, _fires
    if "knower" not in ai.STRATEGIES:
        ai.load_models()
    _patch()
    ai.set_budget_scale(math.inf)
    spec = {key: v for key, v in SETUPS[setup].items() if key != "key"}
    cfg = replace(Settings.from_dict(spec), autoplay=True)
    seed = SEED_BASE + i
    state = build_state(cfg, seed)
    test, seats = seating(setup, lineup, i)
    for pid, seat in enumerate(seats, start=1):
        name, aux = ("knower", float(STOP)) if seat is None else seat
        player = state.players[pid]
        player.is_human = False
        player.ai_strategy = name
        player.ai_params = replace(player.ai_params, aux=1.0 if aux is None else aux)
    _bait = frozenset({test}) if arm == "bait" else frozenset()
    _fires = {"searches": 0, "offered": 0, "lines": 0, "picked": 0, "errors": 0}
    out: dict[int, int] = {}
    t0 = time.perf_counter()
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
        for p in state.non_neutral_players():
            if not p.alive and p.id not in out:
                out[p.id] = state.turn
    held = defaultdict(int)
    for s in state.systems.values():
        held[s.owner_id] += 1
    order = sorted((p.id for p in state.non_neutral_players()),
                   key=lambda q: (q == state.winner, -out.get(q, -MAX_TURNS - 1), held[q]),
                   reverse=True)
    return {"setup": setup, "lineup": lineup, "arm": arm, "k": i, "seed": seed,
            "test": test, "winner": state.winner, "turns": state.turn,
            "won": int(state.winner == test), "place": order.index(test) + 1,
            "lost": state.players[test].ships_lost,
            "out": {str(q): t for q, t in sorted(out.items())},
            "secs": round(time.perf_counter() - t0, 2),
            "bait": dict(_fires),
            "fires": [_fires["searches"], _fires["picked"]]}


def _job(task):
    return play_one(*task)


def _key(row) -> tuple:
    return row["setup"], row["lineup"], row["arm"], row["k"]


def report(rows: list[dict]) -> None:
    groups: dict[tuple, dict[str, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        groups[(r["setup"], r["lineup"])][r["arm"]][r["k"]] = r
    for (setup, lineup), arms in sorted(groups.items()):
        players = SETUPS[setup]["players"]
        print(f"\n{setup} ({players} seats, {SETUPS[setup]['key']}), {lineup}, "
              f"knower at Search  (fair share {100 / players:.1f}%)")
        stock = arms.get("stock", {})
        for arm in ARMS:
            got = arms.get(arm, {})
            if not got:
                continue
            n = len(got)
            won = sum(r["won"] for r in got.values())
            secs = sum(r["secs"] for r in got.values()) / n
            print(f"  {arm:6} {won:4}/{n:<4} {100 * won / n:5.1f}%  {secs:6.1f} s/game")
            if arm != "stock" and stock:
                b = defaultdict(int)
                for r in got.values():
                    for key, v in r["bait"].items():
                        b[key] += v
                print(paired(got, stock))
                print(f"      searches {b['searches']}, offered a bait line {b['offered']}"
                      f" ({100 * b['offered'] / max(1, b['searches']):.1f}%),"
                      f" picked one {b['picked']} ({100 * b['picked'] / max(1, b['searches']):.2f}%),"
                      f" errors {b['errors']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--setup", nargs="+", default=list(SETUPS), choices=list(SETUPS))
    ap.add_argument("--lineup", nargs="+", default=list(LINEUPS), choices=list(LINEUPS))
    ap.add_argument("--arm", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--first", type=int, default=0, help="first seed index")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--label", default="")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    rev = play_rev()
    path = RESULTS / f"{args.label or rev}.jsonl"
    rows = load_rows(path, None if args.label else rev)
    if args.report:
        report(rows)
        return 0
    done = {_key(r) for r in rows}
    tasks = [(s, lu, arm, i)
             for i in range(args.first, args.first + args.seeds)
             for s in args.setup for lu in args.lineup
             for arm in args.arm if (s, lu, arm, i) not in done]
    print(f"{path.relative_to(ROOT)}: {len(done)} rows, {len(tasks)} games to play "
          f"on {args.jobs} workers (rev {rev}, {git_hint()})", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool, path.open("a") as out:
        futures = [pool.submit(_job, t) for t in tasks]
        for n, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            row["rev"] = rev
            out.write(json.dumps(row) + "\n")
            out.flush()
            if n % 10 == 0 or n == len(tasks):
                print(f"  {n}/{len(tasks)}  {time.perf_counter() - t0:7.0f} s", flush=True)
    report(load_rows(path, None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
