#!/usr/bin/env python3
"""Compare how a generated gauntlet deals its candidates, on the same seeds.

    uv run python tools/gauntlet_pool_bench.py --seeds 41 42 43
    uv run python tools/gauntlet_pool_bench.py --seeds 41 --report      # rerun: plays nothing

Three ways to spend probe games on one spec and seed, all picked by
``gauntletgen.pick``:

- **stage** (what shipped before rounds): ``--per`` candidates dealt per
  stage, in blocks, each block at its stage's depth; every one probed.
- **pool** (a pool sized on its own): the first ``stages + --extra``
  candidates of the round-robin order below.
- **stop** (stop early): the round-robin order dealt a round at a time,
  stopping at the end of a round once the pick is complete (every stage filled,
  the last unwon if ``unwon_last``) and either the pick shows every usable
  difficulty level or the round added no new level to the pick. Capped at ``--per`` rounds, stage's
  budget.

The round-robin order deals one candidate at each stage's depth per round, the
ends first (stage 1, 8, 2, 7, ...), so a round favours no depth and a partial
one stays balanced. stop is checked only at a round's end, so every depth has
as many candidates as every other whenever it stops: stopping early cannot
lean towards the weaker opponents of the shallow stages. pool and stop are
prefixes of the same order, read from the same games.

A probe is a bot name, ``name:aux`` for a stop of its knob (``thinker:0`` is
Claudebot), or one of ``TWEAKS`` (heuristic with its reserve at 0). Design:
docs/design/gauntlet.md, "Generated gauntlets", "Dealing fewer candidates".

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, dealer, engine, replay  # noqa: E402
from starconquest import gauntletgen as gg  # noqa: E402
from starconquest.model import AiParams  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

RESULTS = ROOT / "results" / "gauntlet_pool"
# The first run's spec (docs/design/gauntlet.md, "The first run").
SPEC = {"stages": 8,
        "rules": {"mode": {"fix": "symmetric"}, "systems": {"range": [13, 29]},
                  "ship_speed_growth_pct": {"fix": 0}, "home_production": {"fix": 2},
                  "fog": {"fix": False}},
        "opponents": {"not": ["knower"]}}


# Probes beyond a bot name: (strategy, AiParams).
TWEAKS = {"heuristic+lean": ("heuristic", AiParams(reserve_fraction=0.0, reserve_floor=0))}
PROBES = ("rusherplus", "heuristic", "heuristic+lean", "thinker:0", "thinker", "marshal")


def staged_candidates(spec: dealer.Spec, seed: int, per_stage: int) -> list[dict]:
    """The deal before rounds: ``per_stage`` candidates per stage, in blocks."""
    rng = random.Random(f"{seed}:gauntlet")
    out, seen = [], set()
    for i in range(spec.stages):
        depth = i / (spec.stages - 1) if spec.stages > 1 else 0.0
        for _ in range(per_stage):
            s = dealer.deal(spec, rng, depth)
            key = s.challenge_key()
            if key not in seen:
                seen.add(key)
                out.append({"key": key, "depth": round(depth, 4), "stage": dealer.stage_dict(s)})
    return out


def ends_first(n: int) -> list[int]:
    """0, n-1, 1, n-2, ...: a round's stage order."""
    out, lo, hi = [], 0, n - 1
    while lo <= hi:
        out.append(lo)
        if hi != lo:
            out.append(hi)
        lo, hi = lo + 1, hi - 1
    return out


def pool_candidates(spec: dealer.Spec, seed: int, rounds: int) -> list[dict]:
    """``rounds`` rounds of one candidate per stage depth, ends first."""
    rng = random.Random(f"{seed}:gauntlet-pool")
    n = spec.stages
    out, seen = [], set()
    for r in range(rounds):
        for i in ends_first(n):
            depth = i / (n - 1) if n > 1 else 0.0
            s = dealer.deal(spec, rng, depth)
            key = s.challenge_key()
            if key not in seen:
                seen.add(key)
                out.append({"key": key, "depth": round(depth, 4), "round": r,
                            "stage": dealer.stage_dict(s)})
    return out


def _level(m: dict):
    if m["wins"]:
        return m["difficulty"]
    return None if m["timeouts"] else "unwon"


def stop_at(measured: list[dict], stages: int, unwon_last: bool, levels: int) -> int:
    """How many of ``measured`` (in deal order) an early-stopping run probes:
    checked only where a round ends."""
    ends = [i + 1 for i, m in enumerate(measured)
            if i + 1 == len(measured) or measured[i + 1]["round"] != m["round"]]
    before = None
    for i in ends:
        picked = gg.pick(measured[:i], stages, unwon_last)
        complete = len(picked) == stages and (not unwon_last or not picked[-1]["wins"])
        shown = {_level(m) for m in picked}
        if complete and (len(shown - {"unwon"}) >= min(levels, stages - unwon_last) or shown == before):
            return i
        before = shown if complete else None
    return len(measured)


def play_one(key: str, stage: dict, probe: str) -> dict:
    """``gauntletgen.play_one``, with a probe that may be a tweak or a stop."""
    if not ai.STRATEGIES.keys() >= {"thinker", "marshal"}:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    if probe in TWEAKS:
        bot, params = TWEAKS[probe]
    else:
        bot, _, aux = probe.partition(":")
        params = AiParams(aux=float(aux)) if aux else AiParams()
    t0 = time.perf_counter()
    cfg = Settings.from_dict(stage)
    state = build_state(cfg, cfg.seed if cfg.seed is not None else 0)
    seat = state.human()
    seat.is_human, seat.ai_strategy, seat.ai_params = False, bot, params
    while state.winner is None and state.turn < gg.MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    timed_out = state.winner is None
    return {"key": key, "bot": probe, "won": state.winner == seat.id, "turns": state.turn,
            "lost": seat.ships_lost, "timed_out": timed_out,
            "secs": round(time.perf_counter() - t0, 2)}


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
    ap.add_argument("--per", type=int, default=gg.ROUNDS,
                    help="stage: candidates per stage; pool and stop: at most this many rounds")
    ap.add_argument("--probes", nargs="+", default=list(PROBES))
    ap.add_argument("--extra", type=int, default=4, help="pool: candidates beyond the stage count")
    ap.add_argument("--no-unwon-last", action="store_true")
    ap.add_argument("--report", action="store_true", help="play nothing; report what is recorded")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    spec = dealer.Spec.from_dict(json.loads(a.spec_json) if a.spec_json else SPEC)
    if stuck := dealer.problems(spec):
        sys.exit("\n".join(stuck))
    probes = a.probes
    unwon_last = not a.no_unwon_last
    RESULTS.mkdir(parents=True, exist_ok=True)
    ai.load_models()
    totals: dict[str, list[dict]] = {}
    probe_wins = {b: [0, 0] for b in probes}

    for seed in a.seeds:
        path = RESULTS / f"{seed}.jsonl"
        staged = staged_candidates(spec, seed, a.per)
        pooled = pool_candidates(spec, seed, a.per)
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
        for c in {c["key"]: c for c in staged + pooled}:
            for b in probes:
                r = next((r for r in rows if r["key"] == c and r["bot"] == b), None)
                if r:
                    probe_wins[b][0] += r["won"] and not r["timed_out"]
                    probe_wins[b][1] += 1
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
    print("\nprobe wins over every candidate:")
    for b, (won, played) in probe_wins.items():
        if played:
            print(f"  {b:<15} {won:>4}/{played:<4} {won / played:.0%}")


if __name__ == "__main__":
    main()
