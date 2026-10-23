#!/usr/bin/env python3
"""Does a gauntlet get harder as it goes? Every roster bot plays every stage.

    uv run python tools/gauntlet_check.py                 # the tutorial, whole roster
    uv run python tools/gauntlet_check.py --quick         # skip the expensive bots
    uv run python tools/gauntlet_check.py --gauntlet my_run --bots heuristic thinker

Each stage is replayed with a bot in seat 1 (`tests.sim.play_settings`, the
leaderboard bot column's own funnel), and a stage's difficulty is the share of
the roster that loses it — the board's own measure. A stage easier than any
before it is flagged. A gauntlet with a stage no start can reach
(`gauntlet.problems`) is refused before anything is played. A stage's seed is pinned, so a (stage, bot) pair is one
game; wall-clock guards are lifted (`ai.set_budget_scale`) so it is the same
game on any machine.

Rows append to ``results/gauntlet_check.jsonl`` as each game finishes, keyed by
the stage's setup key, and a rerun skips pairs already there — so editing one
stage replays only that stage.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, gauntlet  # noqa: E402
from tests import sim  # noqa: E402

RESULTS = ROOT / "results" / "gauntlet_check.jsonl"
EXPENSIVE = ("knower", "actuary")
MAX_TURNS = 400


def play_one(gid: str, sid: int, bot: str) -> dict:
    if bot not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    g = gauntlet.find(gauntlet.every(), gid)
    assert g is not None
    stage = g.stage(sid)
    assert stage is not None
    cfg = gauntlet.stage_settings(stage)
    t0 = time.perf_counter()
    res = sim.play_settings(cfg, cfg.seed, bot, max_turns=MAX_TURNS)
    return {"gauntlet": gid, "stage": sid, "key": cfg.challenge_key(), "bot": bot,
            "won": res.won, "turns": res.turns, "lost": res.lost,
            "timed_out": res.timed_out, "secs": round(time.perf_counter() - t0, 1)}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue       # a line cut short by a killed run
    return rows


def report(g: gauntlet.Gauntlet, rows: list[dict], bots: list[str]) -> list[int]:
    """Print each stage's loss share and return the stage ids that are easier
    than some stage before them (the order should never fall back)."""
    by = {(r["key"], r["bot"]): r for r in rows}
    print(f"\n{g.name}: share of {len(bots)} bots that lose each stage")
    prev, dips = -1.0, []
    for stage in g.stages:
        key = gauntlet.stage_key(stage)
        games = [by[(key, b)] for b in bots if (key, b) in by]
        if not games:
            print(f"  {g.position(stage.id):>2} {stage.title:<20} (not played)")
            continue
        lost = [r["bot"] for r in games if not r["won"]]
        share = len(lost) / len(games)
        flag = ""
        if share < prev:
            flag = "  <- easier than an earlier stage"
            dips.append(stage.id)
        prev = max(prev, share)
        beaten_by = ", ".join(lost) if lost else "-"
        print(f"  {g.position(stage.id):>2} {stage.title:<20} {share:5.0%}  lost: {beaten_by}{flag}")
    return dips


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gauntlet", default="tutorial", help="gauntlet id (default: tutorial)")
    ap.add_argument("--bots", nargs="*", help="bots to play (default: the whole roster)")
    ap.add_argument("--quick", action="store_true", help=f"skip {', '.join(EXPENSIVE)}")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    g = gauntlet.find(gauntlet.every(), a.gauntlet)
    if g is None:
        sys.exit(f"no gauntlet {a.gauntlet!r}")
    if stuck := gauntlet.problems(g):
        sys.exit("\n".join(stuck))
    ai.load_models()
    bots = a.bots or ai.available_strategies()
    if a.quick:
        bots = [b for b in bots if b not in EXPENSIVE]

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["key"], r["bot"]) for r in load(RESULTS)}
    jobs = [(g.id, s.id, b) for s in g.stages for b in bots
            if (gauntlet.stage_key(s), b) not in done]
    print(f"{len(jobs)} games to play ({len(done)} already in {RESULTS.relative_to(ROOT)})", flush=True)
    t0 = time.time()
    with open(RESULTS, "a") as out, ProcessPoolExecutor(a.workers) as pool:
        futures = [pool.submit(play_one, *job) for job in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"  {i}/{len(jobs)} {row['bot']} on stage {row['stage']}: "
                  f"{'won' if row['won'] else 'lost'} in {row['turns']}t ({time.time() - t0:.0f}s)",
                  flush=True)
    dips = report(g, load(RESULTS), bots)
    if dips:
        print(f"\n{len(dips)} stage(s) out of order: {dips}")


if __name__ == "__main__":
    main()
