#!/usr/bin/env python3
"""Tune Hunt's constants (actuary's Style: Hunt, once the bot hunter) together
with CMA-ES against a panel of bots.

    uv run --with cma python tools/hunt_tune.py --hours 0.1 --pop 4 --seeds 2   # smoke
    uv run --with cma python tools/hunt_tune.py --hours 10 --run t1

Every candidate in a generation plays the same fresh seeds (seed base
8,000,000 + generation x 1,000), both seatings, against every panel bot in
every cell, so candidates are ranked on common dice. Fitness is the mean seed
score. Each generation's mean, best and the CMA mean vector append to
results/hunter/tune/<run>.jsonl, so a stopped run still says where it was. The
search runs in a normalised space: each constant is its value over its scale,
clamped to its bounds on read. The constants Hunt shares with actuary's other
stops (``SELF_RELIEF``, ``FRONT_BONUS``, ...) move an actuary panel bot too
(`hunt_bench`): tune them against marshal alone, or not at all.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import hunt_bench  # noqa: E402

# name: (start, scale, low, high, integer)
SPACE = {
    "EDGE_RISK_WEIGHT": (1.0, 0.3, 0.2, 3.0, False),
    "HUNT_REINFORCE_WEIGHT": (0.45, 0.15, 0.0, 1.2, False),
    "SELF_RELIEF": (0.8, 0.2, 0.0, 1.0, False),
    "FRONT_BONUS": (0.5, 0.2, 0.1, 1.5, False),
    "FRONT_DECAY": (0.5, 0.15, 0.1, 0.95, False),
    "TAIL_TURNS": (24.0, 6.0, 8.0, 60.0, False),
    "HORIZON_PAD": (3, 1.5, 0, 10, True),
    "CAPTURE_SWING": (1.0, 0.05, 0.9, 1.25, False),
    "CAPTURE_LANE": (6, 1.5, 0, 12, True),
    "EDGE_GATHER_LANE": (2, 1.0, 0, 6, True),
    "EDGE_SWING": (1.222, 0.06, 1.0, 1.4, False),
}
PANEL = ("actuary", "marshal")
HUNT = "actuary:3"
CELLS = ("7:6", "11:6", "18:6", "24:3", "18:18")


def decode(x, names) -> dict:
    out = {}
    for v, name in zip(x, names):
        start, scale, lo, hi, integer = SPACE[name]
        val = min(hi, max(lo, start + v * scale))
        out[name] = int(round(val)) if integer else round(val, 4)
    if "EDGE_SWING" in out and out["EDGE_SWING"] >= 1.2215:
        out["EDGE_SWING"] = 0.0          # the worst roll, as shipped
    return out


def one(job):
    cell, seed, seats, variant = job
    return hunt_bench.play_one(cell, seed, seats, variant, {})


def fitness(variants, seeds, ex) -> list[float]:
    jobs, keys = [], []
    for i, v in enumerate(variants):
        for opp in PANEL:
            for cell in CELLS:
                for seed in seeds:
                    for seats in ((HUNT, opp), (opp, HUNT)):
                        jobs.append((cell, seed, seats, v))
                        keys.append((i, opp, cell, seed))
    scores: dict = {}
    for key, row in zip(keys, ex.map(one, jobs, chunksize=4)):
        w = row["winner"]
        res = 0.5 if not w else (1.0 if row["seats"][w - 1] == HUNT else 0.0)
        scores.setdefault(key, []).append(res)
    out = [[] for _ in variants]
    for (i, *_), v in scores.items():
        out[i].append(sum(v) / len(v))
    return [sum(v) / len(v) for v in out]


def main() -> None:
    import cma
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="t1")
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=16, help="seeds per cell per generation")
    ap.add_argument("--sigma", type=float, default=0.8)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    names = list(SPACE)
    out = ROOT / "results" / "hunter" / "tune"
    out.mkdir(parents=True, exist_ok=True)
    log = out / f"{a.run}.jsonl"
    es = cma.CMAEvolutionStrategy([0.0] * len(names), a.sigma,
                                  {"popsize": a.pop, "seed": 1, "verbose": -9})
    t0, gen, per = time.time(), 0, None
    with ProcessPoolExecutor(a.jobs) as ex:
        while True:
            left = a.hours * 3600 - (time.time() - t0)
            if per is not None and left < per * 1.2:
                break
            g0 = time.time()
            xs = es.ask()
            seeds = range(8_000_000 + gen * 1000, 8_000_000 + gen * 1000 + a.seeds)
            # The CMA mean is played too, on the same seeds, as the run's estimate.
            fits = fitness([decode(x, names) for x in xs] + [decode(es.mean, names)], seeds, ex)
            es.tell(xs, [-f for f in fits[:-1]])
            per = time.time() - g0
            row = {"gen": gen, "mean_fit": round(fits[-1], 4), "best_fit": round(max(fits[:-1]), 4),
                   "pop_fit": round(sum(fits[:-1]) / len(xs), 4),
                   "mean": decode(es.mean, names), "secs": round(per)}
            with log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)
            gen += 1


if __name__ == "__main__":
    main()
