#!/usr/bin/env python3
"""Tune a rule bot's module constants by CMA-ES against the roster.

    uv run --with cma python tools/const_train.py --pilot --seeds 8
    uv run --with cma python tools/const_train.py --run m1 --gens 40
    uv run --with cma python tools/const_train.py --run m1 --gens 60     # resume

A candidate is marshal with some of its module constants moved. Each worker
holds its own import of ``models/marshal.py`` (registered as ``CAND``) and sets
every genome constant on it before each game, so the opponents, including the
marshal that knower borrows, read the stock module throughout.

The objective is the 2026-10-17 screen's (``docs/design/marshal-joint.md``,
"Screening against the roster"): duels against stock marshal, knower (Predict)
and actuary (Field), both seatings of each seed, a win 1 and a loss 0, a game at
the turn cap ``tools/sweep.py``'s ``TIMEOUT_LEAD`` of a win or a loss for the
side ahead, and the three opponents averaged with actuary weighted 1.25. Every
generation also plays stock marshal on the same games, so each candidate's
fitness reads as points against stock, paired seed by seed.

A genome coordinate is ``(value - stock) / scale``: CMA starts at the shipped
values with sigma 1, one scale step per constant.

``--pilot`` plays fixed arms instead of a search, on the same games: stock, the
constants before the 2026-10-17 ship, and random genomes one sigma from stock,
and reports seconds per game and the paired standard error per arm.

Each run lives in ``results/const/<run>/``: ``es.pkl`` (resumable) and
``log.jsonl`` (one line per generation).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import multiprocessing
import os
import pickle
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from tools import sweep  # noqa: E402

RESULTS = ROOT / "results" / "const"
BOT = "marshal"
CAND = "__const_cand"
SEED_BASE = 6_100_000
CELLS = ("random:13:3", "random:18:6", "random:11:11", "random:24:12")
OPPONENTS = ("marshal", "knower", "actuary")
OPP_AUX = {"knower": 1.0, "actuary": 1.0}    # Predict, Field
WEIGHTS = {"marshal": 1.0, "knower": 1.0, "actuary": 1.25}

# (name, scale, low, high): one CMA step is one scale. Duel-inert constants
# (RIVAL_WEDGE, REMNANT_MAX_PLAYERS) and the switches are left out.
GENOME = (
    ("FRONTIER_GUARD", 0.15, 0.0, 1.5),
    ("POUR_GUARD", 0.15, 0.0, 2.0),
    ("RELIEF_AWARE", 0.15, 0.0, 1.5),
    ("RELIEF_IN_FLIGHT", 0.15, 0.0, 1.5),
    ("DENY_SWAP", 0.25, 0.0, 2.5),
    ("FEED", 0.75, 0.0, 8.0),
    ("DEFEND_PAD", 0.05, 0.0, 0.5),
    ("NEUTRAL_PAD", 0.05, 0.0, 0.5),
    ("NEUTRAL_MARGIN", 0.15, 1.0, 2.5),
    ("OVERWHELM", 0.3, 1.0, 4.0),
    ("BEYOND_DECAY", 0.1, 0.0, 1.0),
    ("RISK_PARITY", 0.1, 0.5, 2.0),
)
NAMES = tuple(g[0] for g in GENOME)

# The constants before the 2026-10-17 ship: the screen measured the bot after it
# +4.7 points weighted (z +15.1) over this.
PRE_1017 = {"FEED": 1.0, "FEED_MAX_TURNS": 2, "RELIEF_AWARE": 0.5, "POUR_GUARD": 0.55}

_cand = None
_stock: dict[str, object] = {}


def _load(alias: str):
    spec = importlib.util.spec_from_file_location(alias, ROOT / "models" / f"{BOT}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def stock_values() -> dict[str, object]:
    mod = _load("sc_const_stock")
    return {n: getattr(mod, n) for n in NAMES + tuple(PRE_1017)}


def decode(z) -> dict[str, float]:
    stock = stock_values() if not _stock else _stock
    out = {}
    for (name, scale, lo, hi), v in zip(GENOME, z):
        out[name] = round(min(hi, max(lo, stock[name] + scale * float(v))), 4)
    return out


def bounds() -> list[list[float]]:
    stock = stock_values()
    return [[(lo - stock[n]) / s for n, s, lo, hi in GENOME],
            [(hi - stock[n]) / s for n, s, lo, hi in GENOME]]


def _worker_setup() -> None:
    global _cand
    if _cand is not None:
        return
    ai.load_models()
    ai.set_budget_scale(sweep.BUDGET_SCALE)
    _stock.update(stock_values())
    _cand = _load("sc_const_cand")
    ai.register(CAND, _cand.decide)


def play(consts: dict, cell_spec: str, seed: int, opp: str, cand_seat: int, cap: int):
    """One duel: the candidate's score in [0, 1], the turns played, and seconds."""
    _worker_setup()
    for name, value in _stock.items():
        setattr(_cand, name, consts.get(name, value))
    t0 = time.perf_counter()
    cell = sweep.parse_cell(cell_spec)
    settings_mod.apply_globals(cell.settings())
    state = mapgen.generate(seed, cell.mode, cell.nodes, 2)
    for pid, p in state.players.items():
        if p.is_neutral:
            continue
        p.is_human = False
        p.ai_strategy = CAND if pid == cand_seat else opp
        aux = p.ai_params.aux if pid == cand_seat else OPP_AUX.get(opp, p.ai_params.aux)
        p.ai_params = replace(p.ai_params, aux=aux)
    while state.winner is None and state.turn < cap:
        engine.end_turn(state, decide=ai.decide)
    if state.winner is not None:
        score = 1.0 if state.winner == cand_seat else 0.0
    else:
        ahead = sweep.leader(state)
        lead = sweep.TIMEOUT_LEAD / 2
        score = 0.5 if ahead == 0 else (0.5 + lead if ahead == cand_seat else 0.5 - lead)
    return score, state.turn, time.perf_counter() - t0


def _play_task(task):
    arm, consts, cell, seed, opp, seat, cap = task
    score, turns, secs = play(consts, cell, seed, opp, seat, cap)
    return arm, cell, seed, opp, seat, score, turns, secs


def games(gen: int, n_seeds: int, cells, opps) -> list[tuple]:
    """``(cell, seed, opp, seat)``: every seed in every cell against every
    opponent, in both seatings."""
    seeds = [SEED_BASE + 1000 * gen + k for k in range(n_seeds)]
    return [(c, s, o, seat) for s in seeds for c in cells for o in opps for seat in (1, 2)]


def run_arms(jobs: int, arms: dict[str, dict], gs, cap: int):
    """Every arm on every game, on a pool of its own: workers start fresh each
    call, so nothing an opponent keeps between games outlives one batch."""
    tasks = [(a, consts, *g, cap) for a, consts in arms.items() for g in gs]
    random.Random(0).shuffle(tasks)   # mix slow and fast games across workers
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(jobs, mp_context=ctx) as pool:
        return list(pool.map(_play_task, tasks, chunksize=2))


def paired(rows, arm: str, base: str = "stock"):
    """Points against ``base``, weighted over opponents, with its paired standard
    error; and the same per opponent."""
    unit = defaultdict(lambda: defaultdict(float))
    for a, cell, seed, opp, _seat, score, _t, _s in rows:
        if a in (arm, base):
            unit[(cell, seed, opp)][a] += score / 2
    per = {}
    for opp in {k[2] for k in unit}:
        d = [v[arm] - v[base] for k, v in unit.items() if k[2] == opp]
        m = sum(d) / len(d)
        var = sum((x - m) ** 2 for x in d) / max(1, len(d) - 1)
        per[opp] = (100 * m, 100 * math.sqrt(var / len(d)))
    wsum = sum(WEIGHTS[o] for o in per)
    mean = sum(WEIGHTS[o] * per[o][0] for o in per) / wsum
    se = math.sqrt(sum((WEIGHTS[o] / wsum) ** 2 * per[o][1] ** 2 for o in per))
    return mean, se, per


def fitness(rows, arm: str) -> float:
    by = defaultdict(list)
    for a, _c, _s, opp, _seat, score, _t, _x in rows:
        if a == arm:
            by[opp].append(score)
    wsum = sum(WEIGHTS[o] for o in by)
    return sum(WEIGHTS[o] * sum(v) / len(v) for o, v in by.items()) / wsum


def pilot(args) -> None:
    rnd = random.Random(7)
    arms = {"stock": {}, "pre1017": dict(PRE_1017)}
    for i in range(args.pilot_steps):
        arms[f"step{i}"] = decode([rnd.gauss(0, 1) for _ in GENOME])
    gs = games(0, args.seeds, args.cell or CELLS, args.opp or OPPONENTS)
    t0 = time.perf_counter()
    rows = run_arms(args.jobs, arms, gs, args.cap)
    wall = time.perf_counter() - t0
    out = RESULTS / "pilot.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print(f"\n{len(rows)} games in {wall:.0f}s wall on {args.jobs} workers "
          f"({wall / len(rows) * args.jobs:.2f} worker-s a game)\n")
    secs = defaultdict(list)
    turns = defaultdict(list)
    caps = defaultdict(int)
    for _a, cell, _s, opp, _seat, _sc, t, s in rows:
        secs[(cell, opp)].append(s)
        turns[(cell, opp)].append(t)
        caps[(cell, opp)] += t >= args.cap
    print(f"{'cell':16} {'opponent':9} {'s/game':>7} {'turns':>6} {'at cap':>6}")
    for k in sorted(secs):
        print(f"{k[0]:16} {k[1]:9} {sum(secs[k]) / len(secs[k]):7.2f} "
              f"{sum(turns[k]) / len(turns[k]):6.0f} {caps[k]:6d}")
    print(f"\npoints against stock, paired ({args.seeds} seeds x {len(args.cell or CELLS)} cells, "
          "both seatings)")
    for arm in arms:
        if arm == "stock":
            continue
        mean, se, per = paired(rows, arm)
        opp_bits = "  ".join(f"{o} {m:+5.1f}±{s:.1f}" for o, (m, s) in sorted(per.items()))
        print(f"  {arm:8} {mean:+6.2f} ± {se:.2f}   {opp_bits}")
    for arm, consts in arms.items():
        if consts and arm.startswith("step"):
            print(f"  {arm}: " + " ".join(f"{k}={v}" for k, v in consts.items()))


def train(args) -> None:
    import cma

    folder = RESULTS / args.run
    folder.mkdir(parents=True, exist_ok=True)
    es_path, log_path = folder / "es.pkl", folder / "log.jsonl"
    if es_path.exists():
        es, gen = pickle.loads(es_path.read_bytes())
        print(f"resuming {args.run} at generation {gen}")
    else:
        opts = {"popsize": args.pop, "seed": 1, "verbose": -9, "bounds": bounds()}
        es = cma.CMAEvolutionStrategy([0.0] * len(GENOME), args.sigma, opts)
        gen = 0
    started = time.perf_counter()
    while gen < args.gens:
        if args.hours and time.perf_counter() - started > args.hours * 3600:
            print(f"stopping at generation {gen}: --hours {args.hours} reached", flush=True)
            break
        t0 = time.perf_counter()
        xs = es.ask()
        arms = {"stock": {}}
        arms.update({f"c{i}": decode(x) for i, x in enumerate(xs)})
        rows = run_arms(args.jobs, arms, games(gen, args.seeds, args.cell or CELLS,
                                          args.opp or OPPONENTS), args.cap)
        stock = fitness(rows, "stock")
        fits = [fitness(rows, f"c{i}") for i in range(len(xs))]
        es.tell(xs, [-f for f in fits])
        gen += 1
        best = max(range(len(xs)), key=fits.__getitem__)
        b_mean, b_se, b_per = paired(rows, f"c{best}")
        row = {"gen": gen, "stock": stock, "avg": sum(fits) / len(fits), "best": fits[best],
               "best_pts": round(b_mean, 2), "best_se": round(b_se, 2),
               "best_per": {o: round(m, 2) for o, (m, _s) in b_per.items()},
               "sigma": es.sigma, "secs": round(time.perf_counter() - t0, 1),
               "mean": decode(es.mean), "best_consts": decode(xs[best])}
        with open(log_path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp = es_path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            fh.write(pickle.dumps((es, gen)))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, es_path)
        print(f"gen {gen:3d}  avg {100 * (row['avg'] - stock):+5.2f}  "
              f"best {b_mean:+5.2f}±{b_se:.2f}  sigma {es.sigma:.2f}  {row['secs']}s", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--pilot", action="store_true", help="fixed arms, timing and noise only")
    ap.add_argument("--pilot-steps", type=int, default=3,
                    help="with --pilot: random genomes one sigma from stock")
    ap.add_argument("--gens", type=int, default=40)
    ap.add_argument("--pop", type=int, default=12)
    ap.add_argument("--sigma", type=float, default=1.0)
    ap.add_argument("--seeds", type=int, default=20,
                    help="seeds per cell and opponent, each in both seatings")
    ap.add_argument("--cap", type=int, default=600)
    ap.add_argument("--cell", action="append", default=[])
    ap.add_argument("--opp", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=22)
    ap.add_argument("--hours", type=float, default=0.0)
    args = ap.parse_args()
    if not args.pilot and not args.run:
        ap.error("--run NAME, or --pilot")
    pilot(args) if args.pilot else train(args)


if __name__ == "__main__":
    main()
