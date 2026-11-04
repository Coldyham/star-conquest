#!/usr/bin/env python3
"""Find the best score on a setup by blending the three par tools, and record how far to trust it.

    uv run python tools/par_best.py --daily tools/par_inputs/2026-11-04_puzzle.json
    uv run python tools/par_best.py --last 14                      # the last 14 days' puzzles, dealt here
    uv run python tools/par_best.py --days 2026-11-03 2026-11-04 --kind board
    uv run python tools/par_best.py --daily FILE --start-k 3 --max-k 6 --k-budget 7200

For each setup, in order:

1. **A beam** (`par_search`, strike candidates on): a good line in about a minute. It is
   only the starting bound, and it is never what the answer rests on.
2. **Launch-limited passes** (`par_exhaustive --max-launches K`): every plan of at most K
   launch orders, K = 1..``--start-k`` together, then K by K up to ``--max-k``. Each pass starts
   from the best bound so far, so it looks only for something strictly better, and each finished
   pass is exact for its family of plans. It stops when ``--patience`` passes in a row improve
   nothing, when K reaches ``--max-k``, or when a pass ends unfinished (a time budget or the
   frontier cap), since nothing after that could be called exact.

It reports the best (turns, ships lost) and what that rests on: ``exact up to K`` means no plan
of at most K launch orders is better, and says nothing about plans with more. The plain
exhaustive search is the only thing that does, and it does not finish on a board like these.

Every stage appends one JSON line to ``results/par/best/<name>.jsonl`` as it finishes, and a
rerun resumes from it, never repeating a finished pass. The best line is
``results/par/best/<name>.best.json``, a real `replay.GameLog`. ``--daily`` takes a file from
``tools/daily.py --dry-run``; ``--days``/``--last`` deal the day's setup here with the same
worker (`daily.make`), which is *not* guaranteed to be the map that was posted: the worker
stored one, and a bot with a time budget can pick another candidate. Check a dealt day against
the posted map before reading anything into it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay
from starconquest.settings import Settings, build_state
from tools import par_exhaustive as pe
from tools import par_search as ps

RESULTS = ROOT / "results" / "par" / "best"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def dealt(day: dt.date, kind: str) -> dict | None:
    """The day's setup as the daily worker would deal it (probes and all)."""
    from tools import daily

    return daily.make(day, out=lambda line: None, kind=kind)


def targets(args: argparse.Namespace) -> list[tuple[str, dict]]:
    """``(name, daily-style file contents)`` for every setup asked for."""
    out: list[tuple[str, dict]] = []
    for path in args.daily or []:
        data = json.loads(Path(path).read_text())
        out.append((f"{data['day']}_{data['kind']}", data))
    days: list[dt.date] = [dt.date.fromisoformat(d) for d in args.days or []]
    if args.last:
        yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
        days += [yesterday - dt.timedelta(days=i) for i in reversed(range(args.last))]
    for day in days:
        row = dealt(day, args.kind)
        if row is None:
            print(f"{day} {args.kind}: no candidate was won, skipped", file=sys.stderr)
            continue
        out.append((f"{day.isoformat()}_{args.kind}", {"day": row["day"], "kind": row["kind"],
                                                       "settings": row["settings"]}))
    return out


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #
def read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def append(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()


# --------------------------------------------------------------------------- #
# One setup
# --------------------------------------------------------------------------- #
def beam_stage(cfg: Settings, seed: int, width: int, budget: float, best_path: Path,
               max_turns: int) -> tuple[float, float, float]:
    """The beam with strike candidates: (turns, lost, seconds), saving its line."""
    started = time.perf_counter()
    search = ps.Search(cfg, seed, dice="honest", width=width, budget=budget,
                       max_turns=max_turns, strikes=True)
    best = search.run()
    if best.turn < math.inf:
        log = search.log()
        log.path = best_path
        log.save()
    return best.turn, best.lost, time.perf_counter() - started


def pass_args(day_file: Path, k: int, first: int, bound: tuple[float, float], args: argparse.Namespace,
              out_dir: Path) -> list[str]:
    """The ``par_exhaustive`` command line for the pass over plans of at most ``k`` launches."""
    argv = ["--daily", str(day_file), "--levels", args.levels, "--max-launches", str(k),
            "--from-launches", str(first), "--workers", "1", "--seed-budget", "1",
            "--budget", str(args.k_budget), "--max-frontier", str(args.max_frontier),
            "--max-turns", str(args.max_turns), "--results-dir", str(out_dir)]
    if bound[0] < math.inf:
        argv += ["--assume-turn", str(int(bound[0]))]
        if bound[1] < math.inf:
            argv += ["--assume-lost", str(int(bound[1]))]
    return argv


def solve(name: str, data: dict, args: argparse.Namespace) -> dict:
    """Run the stages for one setup (resuming from its ledger) and return its summary."""
    out_dir = args.results_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger, best_path = out_dir / f"{name}.jsonl", out_dir / f"{name}.best.json"
    work = out_dir / name
    work.mkdir(exist_ok=True)
    day_file = work / "setup.json"
    day_file.write_text(json.dumps(data))
    cfg = Settings.from_dict(data["settings"])
    assert cfg.seed is not None
    rows = read_rows(ledger)
    best: tuple[float, float] = (math.inf, math.inf)
    exact_up_to = 0
    beam_row = next((r for r in rows if r["stage"] == "beam"), None)
    spent = {(r["first"], r["k"]) for r in rows if r["stage"] == "k"}
    for r in rows:
        if r["stage"] in ("beam", "k"):
            best = min(best, (r["turn"], r["lost"]))
        if r["stage"] == "k" and r["exact"]:
            exact_up_to = max(exact_up_to, r["k"])

    if beam_row is None:
        turn, lost, secs = beam_stage(cfg, cfg.seed, args.width, args.beam_budget, best_path, args.max_turns)
        beam_row = {"stage": "beam", "turn": turn, "lost": lost, "seconds": round(secs, 1), "width": args.width}
        append(ledger, beam_row)
        best = min(best, (turn, lost))
        print(f"{name}: beam (width {args.width}, strikes) {turn}/{lost} in {secs:.0f}s", flush=True)

    ks = [(1, args.start_k)] + [(k, k) for k in range(args.start_k + 1, args.max_k + 1)]
    quiet = 0
    stopped = ""
    for first, k in ks:
        if (first, k) in spent:
            row = next(r for r in rows if r["stage"] == "k" and (r["first"], r["k"]) == (first, k))
        else:
            started = time.perf_counter()
            pass_dir = work / f"k{k}"
            shutil.rmtree(pass_dir, ignore_errors=True)
            with (out_dir / f"{name}.log").open("a") as log, contextlib.redirect_stdout(log):
                print(f"=== plans of at most {k} launch orders (from {first})", flush=True)
                pe.main(pass_args(day_file, k, first, best, args, pass_dir))
            done = next((r for r in reversed(read_rows(pass_dir / f"{name}.jsonl")) if r["event"] == "done"),
                        {"turn": best[0], "lost": best[1], "exact": False})
            result = (done["turn"], done["lost"])
            now = min(best, result)
            row = {"stage": "k", "k": k, "first": first, "turn": now[0], "lost": now[1],
                   "exact": bool(done["exact"]), "improved": result < best,
                   "seconds": round(time.perf_counter() - started, 1)}
            if row["improved"] and (pass_dir / f"{name}.best.json").exists():
                shutil.copyfile(pass_dir / f"{name}.best.json", best_path)
            append(ledger, row)
            print(f"{name}: K = {k}: {row['turn']}/{row['lost']}"
                  f"{' (better)' if row['improved'] else ''}"
                  f"{', exact' if row['exact'] else ', NOT exact'} in {row['seconds']:.0f}s", flush=True)
        best = min(best, (row["turn"], row["lost"]))
        if row["exact"]:
            exact_up_to = max(exact_up_to, k)
        else:
            stopped = f"the pass for K = {k} did not finish"
            break
        quiet = 0 if row["improved"] else quiet + 1
        if quiet >= args.patience:
            stopped = f"{args.patience} passes in a row improved nothing"
            break
    summary = {"stage": "done", "name": name, "turn": best[0], "lost": best[1],
               "beam": [beam_row["turn"], beam_row["lost"]], "exact_up_to": exact_up_to,
               "stopped": stopped or f"reached K = {args.max_k}"}
    if best_path.exists():
        human = build_state(cfg, cfg.seed).human()
        assert human is not None
        won, turn, lost = ps.check_line(replay.load(best_path), human.id)
        summary["replays"] = bool(won and (turn, lost) == (best[0], best[1]))
    last = next((r for r in reversed(rows) if r["stage"] == "done"), None)
    if last != summary:   # a rerun that changed nothing leaves the ledger alone
        append(ledger, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--daily", nargs="+", help="files from tools/daily.py --dry-run")
    ap.add_argument("--days", nargs="+", help="days (YYYY-MM-DD) to deal here")
    ap.add_argument("--last", type=int, default=0, help="the last N days before today, dealt here")
    ap.add_argument("--kind", choices=("puzzle", "board"), default="puzzle", help="for --days and --last")
    ap.add_argument("--width", type=int, default=300, help="beam width for the starting bound")
    ap.add_argument("--beam-budget", type=float, default=90.0, help="seconds for the beam")
    ap.add_argument("--levels", choices=pe.LEVELS, default="all",
                    help="launch sizes tried (all: every count; coarse: all or nothing, far cheaper)")
    ap.add_argument("--start-k", type=int, default=3, help="passes for K = 1..this run together")
    ap.add_argument("--max-k", type=int, default=5, help="the largest number of launch orders tried")
    ap.add_argument("--patience", type=int, default=2, help="stop after this many passes without improvement")
    ap.add_argument("--k-budget", type=float, default=3600.0, help="seconds for one pass")
    ap.add_argument("--max-frontier", type=int, default=1_000_000, help="boards kept per layer in a pass")
    ap.add_argument("--max-turns", type=int, default=120)
    ap.add_argument("--results-dir", type=Path, default=RESULTS)
    args = ap.parse_args(argv)
    if not (args.daily or args.days or args.last):
        ap.error("give --daily FILE, --days DAY or --last N")

    ai.load_models()
    ai.set_budget_scale(math.inf)
    found = targets(args)
    summaries = [solve(name, data, args) for name, data in found]
    print("\nname                  beam      best      exact up to   stopped")
    for s in summaries:
        beam = f"{s['beam'][0]:.0f}/{s['beam'][1]:.0f}" if s["beam"][0] < math.inf else "-"
        best = f"{s['turn']:.0f}/{s['lost']:.0f}" if s["turn"] < math.inf else "none"
        print(f"{s['name']:<21} {beam:<9} {best:<9} K = {s['exact_up_to']:<8} {s['stopped']}"
              f"{'' if s.get('replays', True) else '  (BEST LINE DOES NOT REPLAY)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
