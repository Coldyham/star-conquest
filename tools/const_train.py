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
    """One game: the candidate's score in [0, 1], the turns played, and seconds.

    ``opp`` names the other seats in order, joined by ``+`` for a free-for-all
    (``knower+actuary`` is a three-seat game). A duel at the turn cap scores
    ``TIMEOUT_LEAD`` for the side ahead; a free-for-all scores only a win.
    """
    _worker_setup()
    for name, value in _stock.items():
        setattr(_cand, name, consts.get(name, value))
    t0 = time.perf_counter()
    cell = sweep.parse_cell(cell_spec)
    settings_mod.apply_globals(cell.settings())
    others = opp.split("+")
    state = mapgen.generate(seed, cell.mode, cell.nodes, 1 + len(others))
    rest = iter(others)
    for pid, p in sorted(state.players.items()):
        if p.is_neutral:
            continue
        p.is_human = False
        strategy = CAND if pid == cand_seat else next(rest)
        p.ai_strategy = strategy
        p.ai_params = replace(p.ai_params, aux=OPP_AUX.get(strategy, p.ai_params.aux))
    while state.winner is None and state.turn < cap:
        engine.end_turn(state, decide=ai.decide)
    if state.winner is not None:
        score = 1.0 if state.winner == cand_seat else 0.0
    elif len(others) > 1:
        score = 0.0
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


CONFIRM_DUELS = ("random:11:11", "random:13:3", "random:18:6", "random:24:3",
                 "random:24:12", "random:18:18", "random:18:6,defender_advantage=1.25",
                 "random:18:6,defender_advantage=1.5")
CONFIRM_FIELDS = (("random:18:6", "knower+actuary", 7_100_000, 300),
                  ("random:24:6", "marshal+knower+actuary", 7_200_000, 200))
CONFIRM_SEED = 7_000_000
CONFIRM_RULE = ("passes on a weighted gain (timeout tilt) at z >= 2, with no opponent "
                "4 points or more worse, the same sign in both halves of the seeds "
                "against every opponent, and neither free-for-all worse at z <= -2")


def _z(d):
    m = sum(d) / len(d)
    se = math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1) / len(d))
    return 100 * m, 100 * se, (m / se if se else 0.0)


def confirm(args) -> None:
    """Pre-register the run's candidate (the mean of its last ``--tail``
    generations' means, to two places) and play it against stock on fresh seeds."""
    folder = RESULTS / args.confirm
    rows = [json.loads(x) for x in (folder / "log.jsonl").read_text().splitlines() if x.strip()]
    tail = rows[-args.tail:]
    cand = {n: round(sum(r["mean"][n] for r in tail) / len(tail), 2) for n in NAMES}
    stock = stock_values()
    cand.update({n: stock[n] for n in args.revert})
    base0 = args.seed_base
    field_specs = tuple((c, o, b - CONFIRM_SEED + base0, n) for c, o, b, n in CONFIRM_FIELDS)
    plan = {"candidate": cand, "from": f"{args.confirm} gens {tail[0]['gen']}..{tail[-1]['gen']}",
            "duel_cells": CONFIRM_DUELS, "duel_seeds": [base0, args.confirm_seeds],
            "opponents": OPPONENTS, "weights": WEIGHTS, "fields": field_specs,
            "cap": args.cap, "rule": CONFIRM_RULE}
    if args.revert:
        plan["reverted_to_stock"] = list(args.revert)
    suffix = f"_{args.label}" if args.label else ""
    plan_path = folder / f"confirm_plan{suffix}.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != json.loads(json.dumps(plan)):
            sys.exit(f"{plan_path} holds a different plan; move it aside to register a new one")
    else:
        plan_path.write_text(json.dumps(plan, indent=1) + "\n")
    print("candidate: " + " ".join(f"{k}={v}" for k, v in cand.items()), flush=True)

    arms = {"stock": {}, "cand": cand}
    seeds = [base0 + k for k in range(args.confirm_seeds)]
    duels = [(c, s, o, seat) for s in seeds for c in CONFIRM_DUELS for o in OPPONENTS
             for seat in (1, 2)]
    fields = [(c, s, o, seat) for c, o, base, n in field_specs for s in range(base, base + n)
              for seat in range(1, 2 + o.count("+") + 1)]
    t0 = time.perf_counter()
    out = run_arms(args.jobs, arms, duels + fields, args.cap)
    with open(folder / f"confirm{suffix}.jsonl", "w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"{len(out)} games in {time.perf_counter() - t0:.0f}s\n")

    for label, as_loss in (("timeout tilt", False), ("timeout as loss", True)):
        unit = defaultdict(lambda: defaultdict(float))
        for a, cell, seed, opp, seat, score, _t, _x in out:
            if "+" in opp:
                continue
            v = (1.0 if score == 1.0 else 0.0) if as_loss else score
            unit[(cell, seed, opp)][a] += v / 2
        diff = {k: v["cand"] - v["stock"] for k, v in unit.items()}
        print(f"duels, {label}: points against stock (z)")
        per = {}
        for opp in OPPONENTS:
            d = [x for k, x in diff.items() if k[2] == opp]
            lo = [x for k, x in diff.items() if k[2] == opp and k[1] < base0 + args.confirm_seeds // 2]
            hi = [x for k, x in diff.items() if k[2] == opp and k[1] >= base0 + args.confirm_seeds // 2]
            per[opp] = _z(d)
            print(f"  {opp:8} {per[opp][0]:+6.2f} ± {per[opp][1]:.2f}  z {per[opp][2]:+6.2f}"
                  f"   halves {_z(lo)[0]:+5.1f} / {_z(hi)[0]:+5.1f}")
        wsum = sum(WEIGHTS.values())
        w = [sum(WEIGHTS[o] * diff[(c, s, o)] for o in OPPONENTS) / wsum
             for c in CONFIRM_DUELS for s in seeds]
        wm = _z(w)
        print(f"  weighted {wm[0]:+6.2f} ± {wm[1]:.2f}  z {wm[2]:+6.2f}")
        for c in CONFIRM_DUELS:
            bits = "  ".join(f"{o} {_z([x for k, x in diff.items() if k[0] == c and k[2] == o])[0]:+5.1f}"
                             for o in OPPONENTS)
            print(f"    {c:36} {bits}")
        print()

    for cell, opp, base, n in field_specs:
        unit = defaultdict(lambda: defaultdict(float))
        won = defaultdict(float)
        capped = defaultdict(int)
        seats = opp.count("+") + 2
        for a, c, seed, o, seat, score, t, _x in out:
            if (c, o) == (cell, opp):
                unit[seed][a] += score / seats
                won[a] += score
                capped[a] += t >= args.cap
        games = n * seats
        d = [v["cand"] - v["stock"] for v in unit.values()]
        m = _z(d)
        print(f"field {cell} vs {opp}: stock won {100 * won['stock'] / games:.1f}% "
              f"({capped['stock']} at cap), candidate {100 * won['cand'] / games:.1f}% "
              f"({capped['cand']} at cap); {m[0]:+.2f} ± {m[1]:.2f}, z {m[2]:+.2f}")


ABLATE_SEED = 7_500_000
ABLATE_GROUPS = {"pads+overwhelm": ("DEFEND_PAD", "NEUTRAL_PAD", "OVERWHELM")}


def ablate(args) -> None:
    """The registered candidate with each constant (and each group in
    ``ABLATE_GROUPS``) put back to stock, in the confirmation's duel cells: what
    each part of the candidate is worth, against every opponent."""
    folder = RESULTS / args.ablate
    cand = json.loads((folder / "confirm_plan.json").read_text())["candidate"]
    stock = stock_values()
    arms = {"stock": {}, "cand": cand}
    for name in NAMES:
        if cand[name] != stock[name]:
            arms[f"-{name}"] = {**cand, name: stock[name]}
    for label, names in ABLATE_GROUPS.items():
        arms[f"-{label}"] = {**cand, **{n: stock[n] for n in names}}
    seeds = [ABLATE_SEED + k for k in range(args.confirm_seeds)]
    gs = [(c, s, o, seat) for s in seeds for c in CONFIRM_DUELS for o in OPPONENTS
          for seat in (1, 2)]
    t0 = time.perf_counter()
    out = run_arms(args.jobs, arms, gs, args.cap)
    with open(folder / "ablate.jsonl", "w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"{len(out)} games in {time.perf_counter() - t0:.0f}s\n")
    print("what each part is worth: the candidate's points over the arm that puts it back")
    print(f"{'arm':22} {'weighted':>14}   " + "   ".join(f"{o:>14}" for o in OPPONENTS))
    for arm in arms:
        if arm == "cand":
            continue
        mean, se, per = paired(out, "cand", arm)
        bits = "   ".join(f"{per[o][0]:+6.2f} ± {per[o][1]:4.2f}" for o in OPPONENTS)
        print(f"{arm:22} {mean:+6.2f} ± {se:4.2f}   {bits}")


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
    ap.add_argument("--confirm", metavar="RUN",
                    help="pre-register RUN's candidate and play it against stock on fresh seeds")
    ap.add_argument("--ablate", metavar="RUN",
                    help="put each constant of RUN's registered candidate back to stock")
    ap.add_argument("--tail", type=int, default=10,
                    help="with --confirm: average the means of the last this many generations")
    ap.add_argument("--confirm-seeds", type=int, default=300)
    ap.add_argument("--seed-base", type=int, default=CONFIRM_SEED,
                    help="with --confirm: the first duel seed; the fields' blocks move with it")
    ap.add_argument("--revert", nargs="*", default=[], metavar="NAME",
                    help="with --confirm: put these constants of the candidate back to stock")
    ap.add_argument("--label", default="",
                    help="with --confirm: name this plan and its results (confirm_plan_LABEL.json)")
    args = ap.parse_args()
    if args.confirm:
        confirm(args)
    elif args.ablate:
        ablate(args)
    elif args.pilot:
        pilot(args)
    elif args.run:
        train(args)
    else:
        ap.error("--run NAME, --pilot or --confirm RUN")


if __name__ == "__main__":
    main()
