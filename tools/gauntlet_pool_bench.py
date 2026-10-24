#!/usr/bin/env python3
"""Compare how a generated gauntlet deals its candidates, on the same seeds.

    uv run python tools/gauntlet_pool_bench.py --seeds 41 42 43
    uv run python tools/gauntlet_pool_bench.py --seeds 41 --report      # rerun: plays nothing

Three ways to spend probe games on one spec and seed, all picked by
``gauntletgen.pick``:

- **stage** (what ships): ``--per`` candidates dealt per stage, in blocks, each
  block at its stage's depth (``gauntletgen.candidates``); every one probed.
- **pool** (a pool sized on its own): ``stages + --extra`` candidates, their
  depths spread evenly over 0..1 whatever the stage count.
- **stop** (stop early): the same pool order, but dealt one at a time with no
  size fixed, stopping once the pick is complete (every stage filled, the last
  unwon if ``unwon_last``) and either every usable difficulty level is seen or
  the last ``stages`` candidates added no new level. Capped at stage's budget.

The pool order deals depths 0, 1, 1/2, 1/4, 3/4, 1/8, ... so any prefix of it
is a pool with its depths spread evenly: pool is one prefix, stop another, and
both are read from the same games. Design: docs/design/gauntlet.md, "Generated
gauntlets", "Dealing fewer candidates".

Every probe game is a line in ``results/gauntlet_pool/<seed>.jsonl`` the moment
it finishes; a rerun plays only the games not there.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, dealer  # noqa: E402
from starconquest import gauntletgen as gg  # noqa: E402

RESULTS = ROOT / "results" / "gauntlet_pool"
# The first run's spec (docs/design/gauntlet.md, "The first run").
SPEC = {"stages": 8,
        "rules": {"mode": {"fix": "symmetric"}, "systems": {"range": [13, 29]},
                  "ship_speed_growth_pct": {"fix": 0}, "home_production": {"fix": 2},
                  "fog": {"fix": False}},
        "opponents": {"not": ["knower"]}}


def depths():
    """0, 1, then each dyadic level's new midpoints: 1/2, 1/4, 3/4, 1/8, ..."""
    yield 0.0
    yield 1.0
    level = 2
    while True:
        for k in range(1, level, 2):
            yield float(Fraction(k, level))
        level *= 2


def pool_candidates(spec: dealer.Spec, seed: int, count: int) -> list[dict]:
    """``count`` candidates in pool order: any prefix spreads its depths evenly."""
    rng = random.Random(f"{seed}:gauntlet-pool")
    out, seen = [], set()
    for depth in depths():
        if len(out) >= count:
            break
        s = dealer.deal(spec, rng, depth)
        key = s.challenge_key()
        if key not in seen:
            seen.add(key)
            out.append({"key": key, "depth": round(depth, 4), "stage": dealer.stage_dict(s)})
    return out


def stop_at(measured: list[dict], stages: int, unwon_last: bool, levels: int) -> int:
    """How many of ``measured`` (in deal order) an early-stopping run probes."""
    seen, last_new = set(), 0
    for i in range(1, len(measured) + 1):
        m = measured[i - 1]
        if m["wins"]:
            level = m["difficulty"]
        elif not m["timeouts"]:
            level = "unwon"
        else:
            level = None
        if level is not None and level not in seen:
            seen.add(level)
            last_new = i
        picked = gg.pick(measured[:i], stages, unwon_last)
        complete = len(picked) == stages and (not unwon_last or not picked[-1]["wins"])
        usable_levels = len(seen - {"unwon"})
        if complete and (usable_levels >= levels or i - last_new >= stages):
            return i
    return len(measured)


def play_one(key: str, stage: dict, bot: str) -> dict:
    if bot not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    return gg.play_one(key, stage, bot, gg.MAX_TURNS)


def load(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def summary(name: str, picked: list[dict], games: int) -> dict:
    diffs = [m["difficulty"] for m in picked]
    usable = [d for m, d in zip(picked, diffs) if m["wins"]]
    return {"arm": name, "games": games, "stages": len(picked),
            "levels": len(set(usable)), "unwon_last": bool(picked) and not picked[-1]["wins"],
            "diffs": diffs, "turns": [round(m["win_turns"]) if m["win_turns"] else None for m in picked]}


def show(s: dict) -> None:
    diffs = " ".join("--" if d is None else f"{d:.0%}".rjust(4) for d in s["diffs"])
    print(f"  {s['arm']:<6} {s['games']:>4} games  {s['stages']} stages  "
          f"{s['levels']} usable levels  last unwon {'yes' if s['unwon_last'] else 'no '}  [{diffs}]")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=[41])
    ap.add_argument("--spec-json", help="a spec as JSON text (default: the first run's)")
    ap.add_argument("--per", type=int, default=gg.CANDIDATES, help="stage: candidates per stage")
    ap.add_argument("--extra", type=int, default=4, help="pool: candidates beyond the stage count")
    ap.add_argument("--no-unwon-last", action="store_true")
    ap.add_argument("--report", action="store_true", help="play nothing; report what is recorded")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    spec = dealer.Spec.from_dict(json.loads(a.spec_json) if a.spec_json else SPEC)
    if stuck := dealer.problems(spec):
        sys.exit("\n".join(stuck))
    probes = list(gg.PROBES)
    unwon_last = not a.no_unwon_last
    cap = spec.stages * a.per
    RESULTS.mkdir(parents=True, exist_ok=True)
    ai.load_models()
    totals: dict[str, list[dict]] = {}

    for seed in a.seeds:
        path = RESULTS / f"{seed}.jsonl"
        staged = gg.candidates(spec, seed, a.per)
        pooled = pool_candidates(spec, seed, cap)
        rows = load(path)
        done = {(r["key"], r["bot"]) for r in rows}
        stage_of = {c["key"]: c["stage"] for c in staged + pooled}
        # Pool order first, so a killed run has stop's games before the rest.
        jobs = list(dict.fromkeys((c["key"], b) for c in pooled + staged for b in probes))
        jobs = [j for j in jobs if j not in done]
        print(f"seed {seed}: {len(staged)} staged + {len(pooled)} pooled candidates, "
              f"{len(jobs)} games to play ({len(done)} recorded)", flush=True)
        if jobs and not a.report:
            t0 = time.time()
            with open(path, "a") as out, ProcessPoolExecutor(a.workers) as pool:
                futures = [pool.submit(play_one, k, stage_of[k], b) for k, b in jobs]
                for i, fut in enumerate(as_completed(futures), 1):
                    row = fut.result()
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                    rows.append(row)
                    if i % 16 == 0 or i == len(jobs):
                        print(f"  {i}/{len(jobs)} ({time.time() - t0:.0f}s)", flush=True)

        m_staged = gg.measure(staged, rows, probes)
        m_pooled = gg.measure(pooled, rows, probes)
        if any(m["played"] < len(probes) for m in m_staged + m_pooled):
            print("  incomplete: rerun without --report to play the rest")
            continue
        n_pool = min(len(m_pooled), spec.stages + a.extra)
        n_stop = stop_at(m_pooled, spec.stages, unwon_last, len(probes))
        arms = [summary("stage", gg.pick(m_staged, spec.stages, unwon_last), len(staged) * len(probes)),
                summary("pool", gg.pick(m_pooled[:n_pool], spec.stages, unwon_last), n_pool * len(probes)),
                summary("stop", gg.pick(m_pooled[:n_stop], spec.stages, unwon_last), n_stop * len(probes))]
        for s in arms:
            show(s)
            totals.setdefault(s["arm"], []).append(s)

    if len(a.seeds) > 1 and totals:
        print("\ntotals over seeds:")
        for name, runs in totals.items():
            print(f"  {name:<6} {sum(r['games'] for r in runs):>5} games  "
                  f"{sum(r['stages'] for r in runs)} stages  {sum(r['levels'] for r in runs)} usable levels  "
                  f"{sum(r['unwon_last'] for r in runs)}/{len(runs)} last unwon")


if __name__ == "__main__":
    main()
