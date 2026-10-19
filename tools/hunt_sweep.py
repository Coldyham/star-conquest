#!/usr/bin/env python3
"""Run hunt_bench over a list of single-constant variants, stopping before a
time cap. Each variant's ledger is results/hunter/v<block>_<NAME>=<VALUE>.jsonl; join
several constants with '+'.

    uv run python tools/hunt_sweep.py --variants RISK_WEIGHT=0.3 TAIL_TURNS=12 --seeds 50 --hours 10
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", required=True)
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--seed-base", type=int, default=9_100_000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    t0 = time.time()
    per = None
    for v in a.variants:
        left = a.hours * 3600 - (time.time() - t0)
        if per is not None and left < per * 1.3:
            print(f"stopping before {v}: {left:.0f}s left, a variant takes ~{per:.0f}s", flush=True)
            break
        s = time.time()
        subprocess.run([sys.executable, str(ROOT / "tools" / "hunt_bench.py"), "--variant", *v.split("+"),
                        "--label", f"v{a.seed_base // 100_000}_{v}", "--seeds", str(a.seeds),
                        "--seed-base", str(a.seed_base), "--jobs", str(a.jobs)],
                       check=True)
        took = time.time() - s
        per = took if per is None else max(per, took)
        print(f"{v} done in {took:.0f}s", flush=True)


if __name__ == "__main__":
    main()
