#!/usr/bin/env python3
"""What one Hunt decide costs, for `decide_ms` (models/actuary.py `HUNT_COST_*`,
`COST_LOOK_MS`; Hunt is actuary's Style stop 3).

    uv run python tools/hunt_cost.py                 # in-game guard (BUDGET_SCALE 1)
    uv run python tools/hunt_cost.py --budget-scale inf

Plays Hunt against actuary over a grid of system counts, ship speeds and
seat counts, records Hunt's CPU ms per decide (process time, so other load
on the machine does not count), prints the 75th percentile per cell and fits
``ms = A * (nodes / 40) ** B`` by least squares on the logs, as actuary's
constants are fitted. Time it on a quiet machine: the guard reads the wall clock.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(job):
    nodes, ly, seats, seed, scale = job
    from starconquest import ai, engine, replay
    from starconquest.settings import Settings, build_state
    if "actuary" not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(scale)
    st = build_state(Settings(mode="random", nodes=nodes, players=seats,
                              ship_ly_per_turn=ly, autoplay=True), seed)
    for pid in range(1, seats + 1):
        st.players[pid].ai_strategy = "actuary"
        st.players[pid].ai_params = replace(st.players[pid].ai_params, aux=3.0 if pid == 1 else 1.0)
    ms = []

    def dec(s, pid):
        if pid != 1:
            return ai.decide(s, pid)
        t0 = time.process_time()
        orders = ai.decide(s, pid)
        ms.append(1000 * (time.process_time() - t0))
        return orders

    while st.winner is None and st.turn < 300 and st.players[1].alive:
        replay.reseed(st)
        engine.end_turn(st, decide=dec)
    return (nodes, ly, seats), ms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, nargs="*", default=[18, 40, 80])
    ap.add_argument("--speeds", type=float, nargs="*", default=[3, 6, 18])
    ap.add_argument("--seats", type=int, nargs="*", default=[2, 4])
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--budget-scale", type=float, default=1.0)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    jobs = [(n, ly, k, 9_900_000 + s, a.budget_scale) for n in a.nodes for ly in a.speeds
            for k in a.seats for s in range(a.seeds)]
    by: dict = {}
    with ProcessPoolExecutor(a.jobs) as ex:
        for key, ms in ex.map(run, jobs):
            by.setdefault(key, []).extend(ms)
    pts = []
    for (n, ly, k), ms in sorted(by.items()):
        ms.sort()
        if not ms:
            continue
        p75 = ms[int(0.75 * len(ms))]
        print(f"{n:4} nodes {ly:>4g} ly {k} seats  p75 {p75:6.1f} ms  max {ms[-1]:7.1f}  (n {len(ms)})")
        pts.append((n, max(p75, 0.01)))
    xs = [math.log(n / 40) for n, _ in pts]
    ys = [math.log(p) for _, p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    print(f"fit: {math.exp(my - b * mx):.1f} ms at 40 nodes, exponent {b:.2f}")


if __name__ == "__main__":
    main()
