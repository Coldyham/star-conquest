#!/usr/bin/env python3
"""Hunt's gate: a bot (or a variant of actuary at Style: Hunt) against actuary,
paired by seed.

    uv run python tools/hunt_bench.py --seeds 50                    # Hunt vs Field
    uv run python tools/hunt_bench.py --variant EDGE_RISK_WEIGHT=0.8 --seeds 50
    uv run python tools/hunt_bench.py --bot actuary:1 --label fork --seeds 20   # exactly 50%

A seat is a strategy, or ``name:aux`` for a stop of its knob; Hunt is
``actuary:3`` (older ledgers in ``results/hunter/`` call it ``hunter``). A variant sets actuary's module constants
(`configure`), so one that Hunt shares with the other stops (``RISK_WEIGHT``,
``TAIL_TURNS``, ``FRONT_BONUS``, ...) moves an actuary opponent too: vary the
``HUNT_``, ``EDGE_``, ``CAPTURE_`` and ``LOOK_`` constants, which only Hunt reads.

Every seed is played twice on the same map with the seats swapped, so
first-position advantage cancels; a seed scores 1, 0.5 or 0 and z is taken over
seed scores. Wall-clock guards are lifted, so the result is the same on any
machine. Rows append to ``results/hunter/<label>.jsonl`` and a rerun skips games
already there, so two ledgers on the same seeds pair seed by seed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

RESULTS = ROOT / "results" / "hunter"
SEED_BASE = 9_100_000
MAX_TURNS = 600
CELLS = ("7:6", "11:6", "18:6", "24:3", "18:18")   # nodes:ly, two seats


def _variant(spec: list[str]) -> dict[str, float]:
    out = {}
    for item in spec:
        name, _, value = item.partition("=")
        if "," in value or value == "none":       # a tuple: "0.4,2.5", or "none" for ()
            out[name] = tuple(float(x) for x in value.split(",") if x) if value != "none" else ()
        else:
            out[name] = float(value)
    return out


def profile(seat: str, aux: float = 1.0) -> tuple[str, float]:
    """``(strategy, aux)`` for a seat written ``name`` or ``name:aux``."""
    name, _, stop = seat.partition(":")
    return name, float(stop) if stop else aux


def play_one(cell: str, seed: int, seats: tuple[str, str], variant: dict, extra: dict,
             vs_aux: float = 1.0, vs: str = "") -> dict:
    if "actuary" not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    if variant:
        sys.modules[ai.STRATEGIES["actuary"].__module__].configure(variant)
    nodes, ly = cell.split(":")
    cfg = Settings(mode="random", nodes=int(nodes), players=2,
                   ship_ly_per_turn=float(ly), autoplay=True, **extra)
    state = build_state(cfg, seed)
    for pid, seat in enumerate(seats, start=1):
        p = state.players[pid]
        p.ai_strategy, aux = profile(seat, vs_aux if seat == vs else 1.0)
        p.ai_params = replace(p.ai_params, aux=aux)
    t0 = time.perf_counter()
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    return {"cell": cell, "seed": seed, "seats": list(seats), "winner": state.winner,
            "turns": state.turn, "secs": round(time.perf_counter() - t0, 2)}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def per_seed(rows: list[dict], bot: str, cell: str) -> dict[int, float]:
    by: dict[int, list[float]] = {}
    for r in rows:
        if r["cell"] != cell:
            continue
        w = r["winner"]
        res = 0.5 if w is None or w == 0 else (1.0 if r["seats"][w - 1] == bot else 0.0)
        by.setdefault(r["seed"], []).append(res)
    return {s: sum(v) / 2 for s, v in by.items() if len(v) == 2}


def stats(xs: list[float], fair: float = 0.5) -> tuple[float, float, int]:
    n = len(xs)
    if not n:
        return math.nan, math.nan, 0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / max(1, n - 1)
    z = (m - fair) / math.sqrt(var / n) if var > 0 else 0.0
    return m, z, n


def report(rows: list[dict], bot: str, cells, base: list[dict] | None = None) -> None:
    pooled, diffs = [], []
    for cell in cells:
        ps = per_seed(rows, bot, cell)
        m, z, n = stats(list(ps.values()))
        line = f"  {cell:>6}  {100 * m:5.1f}%  z {z:+5.2f}  n {n}"
        if base is not None:
            bs = per_seed(base, bot, cell)
            d = [ps[s] - bs[s] for s in ps if s in bs]
            dm, dz, dn = stats(d, 0.0)
            diffs += d
            line += f"   vs base {100 * dm:+5.1f}  z {dz:+5.2f}"
        pooled += ps.values()
        print(line)
    m, z, n = stats(pooled)
    line = f"  pooled  {100 * m:5.1f}%  z {z:+5.2f}  n {n}"
    if diffs:
        dm, dz, _ = stats(diffs, 0.0)
        line += f"   vs base {100 * dm:+5.1f}  z {dz:+5.2f}"
    print(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="actuary:3")
    ap.add_argument("--vs", default="actuary")
    ap.add_argument("--variant", nargs="*", default=[], help="NAME=VALUE overrides for actuary.configure")
    ap.add_argument("--cells", nargs="*", default=list(CELLS))
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--jitter", type=float, default=None)
    ap.add_argument("--vs-aux", type=float, default=1.0, help="the opponent's aux knob (knower 2 = Search)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--base", default=None, help="ledger label to pair against")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--report", action="store_true", help="only print the ledger")
    a = ap.parse_args()

    variant = _variant(a.variant)
    extra = {"combat_jitter": a.jitter} if a.jitter is not None else {}
    label = a.label or (a.bot.replace(":", "@") + ("_" + "_".join(a.variant) if a.variant else ""))
    if a.jitter is not None:
        label += f"_j{a.jitter:g}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{label}.jsonl"
    rows = load(path)
    done = {(r["cell"], r["seed"], tuple(r["seats"])) for r in rows}
    seeds = range(a.seed_base, a.seed_base + a.seeds)
    jobs = [(c, s, seats) for c in a.cells for s in seeds
            for seats in ((a.bot, a.vs), (a.vs, a.bot)) if (c, s, seats) not in done]
    if not a.report and jobs:
        t0 = time.time()
        with ProcessPoolExecutor(a.jobs) as ex, path.open("a") as out:
            futs = [ex.submit(play_one, c, s, seats, variant, extra, a.vs_aux, a.vs) for c, s, seats in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                row = f.result()
                out.write(json.dumps(row) + "\n")
                out.flush()
                rows.append(row)
                if i % 50 == 0:
                    print(f"  {i}/{len(jobs)} games, {time.time() - t0:.0f}s", flush=True)
    seeds_set = set(seeds)
    rows = [r for r in rows if r["seed"] in seeds_set and r["cell"] in a.cells]
    base = None
    if a.base:
        base = [r for r in load(RESULTS / f"{a.base}.jsonl") if r["seed"] in seeds_set]
    print(f"{label} vs {a.vs}, seeds {a.seed_base}+{a.seeds}")
    report(rows, a.bot, a.cells, base)


if __name__ == "__main__":
    main()
