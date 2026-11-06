#!/usr/bin/env python3
"""Find the best score on a setup by blending the par tools, and record how far to trust it.

    uv run python tools/par_best.py --daily tools/par_inputs/2026-11-04_puzzle.json
    uv run python tools/par_best.py --last 14                      # the last 14 days' puzzles, dealt here
    uv run python tools/par_best.py --days 2026-11-03 2026-11-04 --kind board
    uv run python tools/par_best.py --daily FILE --start-k 3 --max-k 6 --k-budget 7200
    uv run python tools/par_best.py --daily FILE --workers 6              # opening search on 6 cores

For each setup, in order:

1. **A beam** (`par_search`, strike candidates on): a good line in about a minute. It is
   only the starting bound, and it is never what the answer rests on.
2. **Launch-limited passes** (`par_exhaustive --max-launches K`): every plan of at most K
   launch orders, K = 1..``--start-k`` together, then K by K up to ``--max-k``. Each pass starts
   from the best bound so far, so it looks only for something strictly better, and each finished
   pass is exact for its family of plans. It stops when ``--patience`` passes in a row improve
   nothing, when K reaches ``--max-k``, or when a pass ends unfinished (a time budget or the
   frontier cap), since nothing after that could be called exact.

3. **A proof attempt**, once the first passes have set a tight bound: the plain search over every
   order set (no launch limit), then again with sends down two lanes at once, each under
   ``--proof-budget``. If one finishes, the answer is optimal *within that model*, and the K passes
   stop. On a board with a wide gap between the floor and the best line it will not finish, which
   costs only its budget (``--proof-budget 0`` skips it).

4. **Lines the passes cannot reach**, when the first passes and the proof did not settle it. A pass
   covers only plans of at most K launches, but the best human line on a day used 9 (most of them
   relays), and a beam ranks an opening badly when its payoff comes later. So `par_open` enumerates
   every ``--open-depth``-turn opening (`strike` sends) and finishes each with a cheap beam, then
   redoes the best ``--refine`` with a wide one (``--workers`` processes); `par_local` then trims the
   best line by local search over whole plans. These find lines and prove nothing. The K passes
   after them start from the better bound.

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
from tools import par_local as pl
from tools import par_open as po
from tools import par_search as ps

RESULTS = ROOT / "results" / "par" / "best"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def dealt(day: dt.date, kind: str) -> dict | None:
    """The day's setup as the daily worker would deal it (probes and all)."""
    from tools import daily

    return daily.make(day, out=lambda line: None, kind=kind)


def targets(args: argparse.Namespace) -> list[tuple[str, dict, Path | None]]:
    """``(name, daily-style file contents, the file it came from)`` for every setup asked for;
    a day dealt here has no file."""
    out: list[tuple[str, dict, Path | None]] = []
    for path in args.daily or []:
        data = json.loads(Path(path).read_text())
        out.append((f"{data['day']}_{data['kind']}", data, Path(path)))
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
                                                       "settings": row["settings"]}, None))
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
              out_dir: Path, turn_only: bool | None = None) -> list[str]:
    """The ``par_exhaustive`` command line for the pass over plans of at most ``k`` launches."""
    argv = ["--daily", str(day_file), "--levels", args.levels, "--max-launches", str(k),
            "--from-launches", str(first), "--workers", "1", "--seed-budget", "1",
            "--budget", str(args.k_budget), "--max-frontier", str(args.max_frontier),
            "--max-turns", str(args.max_turns), "--results-dir", str(out_dir)]
    if args.turn_first if turn_only is None else turn_only:
        argv.append("--turn-only")
    if bound[0] < math.inf:
        argv += ["--assume-turn", str(int(bound[0]))]
        if bound[1] < math.inf:
            argv += ["--assume-lost", str(int(bound[1]))]
    return argv


def _minutes(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min" if seconds < 5400 else f"{seconds / 3600:.1f} h"


def verdict(summary: dict, k_rows: list[dict], proof_rows: list[dict], args: argparse.Namespace,
            setup: Path) -> dict:
    """What the answer rests on, what it does not cover, and what to run next to be surer.

    ``headline`` says whether it is exact; ``constraints`` is the model it is exact in;
    ``not_covered`` is what that model leaves out; ``next`` is a list of {why, command, est}."""
    best = f"{summary['turn']:.0f}/{summary['lost']:.0f}" if summary["turn"] < math.inf else "no win"
    proof = summary["proved"]
    k_to = summary["exact_up_to"]
    constraints = [f"the setup in {setup}, as dealt (not compared with the posted map: no network here)",
                   "rivals decide as their bots do; dice are the game's keyed dice (a rewind offers none)"]
    not_covered: list[str] = []
    nxt: list[dict] = []
    shown = setup.relative_to(ROOT) if setup.is_relative_to(ROOT) else setup
    cmd = f"uv run python tools/par_best.py --daily {shown}"
    if args.results_dir != RESULTS:
        cmd += f" --results-dir {args.results_dir}"   # the same ledger, so a rerun resumes

    if args.levels != "all":
        constraints.append(f"launch sizes limited to --levels {args.levels} (not every count)")
        nxt.append({"why": "try every launch size, not only the coarse ones", "est": "far slower",
                    "command": f"{cmd} --levels all"})
    else:
        constraints.append("every launch size, one launch down one lane per system per turn")

    turns_only = bool(summary.get("turn_only"))
    if turns_only:
        constraints.append(f"exact for the turn only: ships lost at turn {summary['turn']:.0f} are the fewest "
                           "found, not proved fewest (--tiebreak checks that)")
    if proof:
        headline = (f"EXACT on turns: no win before turn {summary['turn']:.0f} within the model below "
                    f"({best} is the best found)" if turns_only
                    else f"EXACT: {best} is optimal within the model below")
        constraints.append("every order set of that kind, no launch limit, no frontier cap hit"
                           + (", plus two lanes at once from one system" if proof == "two-lane splits" else ""))
        if proof == "single-lane":
            not_covered.append("a system sending down two lanes in the same turn")
            nxt.append({"why": "also allow two lanes at once from one system",
                        "est": "took 16x the single-lane search on the one board measured",
                        "command": f"{cmd} --proof-budget {max(int(args.proof_budget) * 4, 1200)}"})
        not_covered.append("three or more lanes at once from one system (only matters where a system has three lanes)")
        not_covered.append("anything about the posted map, if it differs from the dealt one")
    else:
        if k_to:
            headline = (f"EXACT only for plans of at most {k_to} launch orders: none of them wins before "
                        f"turn {summary['turn']:.0f}. Not proved optimal overall" if turns_only
                        else f"EXACT only for plans of at most {k_to} launch orders: nothing there beats {best}. "
                        "Not proved optimal overall")
            constraints.append(f"plans of at most {k_to} launch orders (the passes each started from the best so far)")
            not_covered.append(f"plans with {k_to + 1} or more launch orders, which may be better")
        else:
            headline = f"NOT exact: {best} is the best line found, nothing is proved about it"
            not_covered.append("everything: no pass finished")
        if proof_rows and not any(r["exact"] for r in proof_rows):
            not_covered.append("the full search over every order set did not finish in its budget")
        unfinished = [r for r in k_rows if not r["exact"]]
        if unfinished:
            not_covered.append(f"the pass for K = {unfinished[0]['k']} stopped before it finished (time or frontier cap)")
            nxt.append({"why": "give the unfinished pass more time and a larger frontier",
                        "est": f"at least {_minutes(args.k_budget * 2)}",
                        "command": f"{cmd} --k-budget {int(args.k_budget * 3)} --max-frontier {args.max_frontier * 2}"})
        elif k_to:
            seconds = [r["seconds"] for r in k_rows if r["first"] == r["k"]]
            ratio = max(seconds[-1] / seconds[-2], 2.0) if len(seconds) >= 2 and seconds[-2] > 0.5 else 6.0
            last = seconds[-1] if seconds else sum(r["seconds"] for r in k_rows)
            nxt.append({"why": f"cover plans of {k_to + 1} launch orders too",
                        "est": f"about {_minutes(last * ratio)} (each launch added has cost x{ratio:.0f})",
                        "command": f"{cmd} --max-k {k_to + 1} --patience 5"})
        if not proof_rows and args.proof_budget > 0 and "floor" in summary:
            not_covered.append(f"no proof attempted: the best line is {summary['turn'] - summary['floor']:.0f} turns "
                               f"over the floor of {summary['floor']:.0f} (more than --proof-gap {args.proof_gap})")
        if args.proof_budget <= 0:
            nxt.append({"why": "try the full search for a proof (it only costs its budget if it cannot finish)",
                        "est": "5 min", "command": f"{cmd} --proof-budget 300"})
        elif proof_rows and not any(r["exact"] for r in proof_rows):
            nxt.append({"why": "let the full search run longer (needs memory; it may still not finish)",
                        "est": f"up to {_minutes(args.proof_budget * 6)}",
                        "command": f"{cmd} --proof-budget {int(args.proof_budget * 6)} --max-frontier 2000000"})
    gap = summary["beam"][0] - summary["turn"] if summary["beam"][0] < math.inf else None
    if not proof and (gap is None or gap > 0):
        nxt.append({"why": "a wider beam, in case a better line has a start no pass reaches",
                    "est": "5 to 15 min",
                    "command": f"{cmd} --width 1500 --beam-budget 600"})
    if summary.get("improved_by") and not proof:
        not_covered.append("the best line came from the " + " and ".join(summary["improved_by"])
                           + " search, which proves nothing: it may have more launches than any pass covers")
    if not summary.get("replays", True):
        headline += "  (THE SAVED BEST LINE DOES NOT REPLAY: do not trust this result)"
    return {"headline": headline, "constraints": constraints, "not_covered": not_covered, "next": nxt}


def proof_args(day_file: Path, splits: bool, bound: tuple[float, float], args: argparse.Namespace,
               out_dir: Path) -> list[str]:
    """The ``par_exhaustive`` command line for the plain search over every order set (no launch
    limit), with or without sends down two lanes at once."""
    argv = ["--daily", str(day_file), "--levels", args.levels, "--workers", "1", "--seed-budget", "1",
            "--budget", str(args.proof_budget), "--max-frontier", str(args.max_frontier),
            "--max-turns", str(args.max_turns), "--results-dir", str(out_dir)]
    if splits:
        argv += ["--splits", "--max-branch", "100000000"]
    if args.turn_first:
        argv.append("--turn-only")
    if bound[0] < math.inf:
        argv += ["--assume-turn", str(int(bound[0]))]
        if bound[1] < math.inf:
            argv += ["--assume-lost", str(int(bound[1]))]
    return argv


def settled(row: dict | None, budget: float, max_frontier: int) -> bool:
    """A recorded pass stands unless it was cut off and this run allows it more time or a larger
    frontier. Rows from before the limits were recorded count their seconds as the budget."""
    if row is None:
        return False
    if row["exact"]:
        return True
    return row.get("budget", row["seconds"]) >= budget and row.get("max_frontier", 1_000_000) >= max_frontier


def latest(rows: list[dict], stage: str, key) -> list[dict]:
    """The last row of each pass of ``stage``: a pass run again supersedes its cut-off row."""
    return list({key(r): r for r in rows if r["stage"] == stage}.values())


def run_pass(name: str, argv: list[str], pass_dir: Path, best: tuple[float, float], out_dir: Path,
             header: str) -> dict:
    """Run one ``par_exhaustive`` pass (its output to the setup's log) and fold what it found
    into the best so far: {turn, lost, exact, improved, seconds}."""
    started = time.perf_counter()
    if not any(pass_dir.glob("*.ckpt.pkl")):   # a checkpoint means the pass was cut off: carry on
        shutil.rmtree(pass_dir, ignore_errors=True)
    with (out_dir / f"{name}.log").open("a") as log, contextlib.redirect_stdout(log):
        print(f"=== {header}", flush=True)
        pe.main(argv)
    done = next((r for r in reversed(read_rows(pass_dir / f"{name}.jsonl")) if r["event"] == "done"),
                {"turn": best[0], "lost": best[1], "exact": False})
    result = (done["turn"], done["lost"])
    now = min(best, result)
    if result < best and (pass_dir / f"{name}.best.json").exists():
        shutil.copyfile(pass_dir / f"{name}.best.json", out_dir / f"{name}.best.json")
    return {"turn": now[0], "lost": now[1], "exact": bool(done["exact"]), "improved": result < best,
            "seconds": round(time.perf_counter() - started, 1)}


def _fold_line(found: tuple[float, float], line: Path, best: tuple[float, float], best_path: Path) -> bool:
    """Keep ``line`` as the setup's best line when ``found`` beats ``best``."""
    if found < best and line.exists():
        shutil.copyfile(line, best_path)
        return True
    return False


def open_stage(name: str, day_file: Path, work: Path, best: tuple[float, float], best_path: Path,
               out_dir: Path, args: argparse.Namespace) -> dict:
    """The opening search (`par_open`): a cheap beam from every opening, then a wide one from the best
    few. Resumes inside itself (one ledger line per opening). {turn, lost, improved, seconds}."""
    started = time.perf_counter()
    folder = work / "open"
    common = ["--daily", str(day_file), "--depth", str(args.open_depth), "--workers", str(args.workers),
              "--max-turns", str(min(args.max_turns, 30)), "--results-dir", str(folder)]
    now = best
    with (out_dir / f"{name}.log").open("a") as log, contextlib.redirect_stdout(log):
        print(f"=== opening search: every {args.open_depth}-turn opening, then the best {args.refine} widely", flush=True)
        for suffix, extra in (("", ["--width", str(args.open_width), "--budget", str(args.open_budget)]),
                              (".refine", ["--refine", str(args.refine), "--width", str(args.refine_width),
                                           "--budget", str(args.refine_budget)])):
            if suffix and (args.refine <= 0 or not (folder / f"{name}.jsonl").exists()):
                continue
            po.main([*common, *extra])
            rows = read_rows(folder / f"{name}{suffix}.jsonl")
            found = min(((r["turn"], r["lost"]) for r in rows), default=(math.inf, math.inf))
            if _fold_line(found, folder / f"{name}{suffix}.best.json", now, out_dir / f"{name}.best.json"):
                now = found
    return {"turn": now[0], "lost": now[1], "improved": now < best,
            "seconds": round(time.perf_counter() - started, 1)}


def local_stage(name: str, day_file: Path, work: Path, best: tuple[float, float], best_path: Path,
                out_dir: Path, args: argparse.Namespace) -> dict:
    """Local search over whole plans from the best line (`par_local`): trims a line's launches and
    ships lost, and sometimes finds a turn."""
    started = time.perf_counter()
    folder = work / "local"
    argv = ["--daily", str(day_file), "--seconds", str(args.local_seconds), "--results-dir", str(folder),
            "--horizon", str(int(min(best[0] + 3, 20)) if best[0] < math.inf else 14)]
    if best_path.exists():
        argv += ["--seed-line", str(best_path)]
    with (out_dir / f"{name}.log").open("a") as log, contextlib.redirect_stdout(log):
        print(f"=== local search for {args.local_seconds:.0f}s from the best line", flush=True)
        pl.main(argv)
    rows = [r for r in read_rows(folder / f"{name}.jsonl") if r.get("event") == "best" and r["score"][0] == 0]
    found = min(((r["score"][1], r["score"][2]) for r in rows), default=(math.inf, math.inf))
    now = best
    if _fold_line(found, folder / f"{name}.best.json", best, out_dir / f"{name}.best.json"):
        now = found
    return {"turn": now[0], "lost": now[1], "improved": now < best,
            "seconds": round(time.perf_counter() - started, 1)}


def solve(name: str, data: dict, args: argparse.Namespace, source: Path | None = None) -> dict:
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
    spent = {(r["first"], r["k"]): r for r in rows if r["stage"] == "k"}
    for r in rows:
        if r["stage"] in ("beam", "k", "open", "local"):
            best = min(best, (r["turn"], r["lost"]))
        if r["stage"] == "k" and r["exact"]:
            exact_up_to = max(exact_up_to, r["k"])

    if beam_row is None:
        turn, lost, secs = beam_stage(cfg, cfg.seed, args.width, args.beam_budget, best_path, args.max_turns)
        beam_row = {"stage": "beam", "turn": turn, "lost": lost, "seconds": round(secs, 1), "width": args.width}
        append(ledger, beam_row)
        best = min(best, (turn, lost))
        print(f"{name}: beam (width {args.width}, strikes) {turn}/{lost} in {secs:.0f}s", flush=True)

    proofs = {(r.get("when", "early"), r["splits"]): r for r in rows if r["stage"] == "proof"}
    opening = build_state(cfg, cfg.seed)
    player = opening.human()
    assert player is not None
    root_floor = ps.floor(opening, player.id)
    for r in proofs.values():
        best = min(best, (r["turn"], r["lost"]))
    proved = ""

    def worth_proving() -> bool:
        """A proof only finishes when the best line is near the floor; elsewhere it spends its budget."""
        return args.proof_budget > 0 and best[0] - root_floor <= args.proof_gap

    def prove(when: str) -> str:
        """The plain search, then with two-lane sends: what the first stage that finished proves.
        ``when`` is "early" (after the first passes) or "late" (after the opening and local searches
        tightened the bound)."""
        nonlocal best
        got = ""
        for splits in ([False, True] if args.proof_splits else [False]):
            row = proofs.get((when, splits))
            if not settled(row, args.proof_budget, args.max_frontier):
                kind = "with two-lane splits" if splits else "one lane per system per turn"
                row = {"stage": "proof", "splits": splits, "when": when, "turn_only": args.turn_first,
                       "budget": args.proof_budget, "max_frontier": args.max_frontier,
                       **run_pass(name, proof_args(day_file, splits, best, args, work / f"proof{int(splits)}"),
                                  work / f"proof{int(splits)}", best, out_dir,
                                  f"every order set, {kind}")}
                append(ledger, row)
                print(f"{name}: all orders{', two-lane splits' if splits else ''}: {row['turn']}/{row['lost']}"
                      f"{' (better)' if row['improved'] else ''}"
                      f"{', EXACT' if row['exact'] else ', not finished'} in {row['seconds']:.0f}s", flush=True)
            assert row is not None
            best = min(best, (row["turn"], row["lost"]))
            if not row["exact"]:
                break
            got = "two-lane splits" if splits else "single-lane"
        return got

    ks = [(1, args.start_k)] + [(k, k) for k in range(args.start_k + 1, args.max_k + 1)]
    quiet = 0
    stopped = ""
    improved_by: list[str] = [r["stage"] for r in rows if r["stage"] in ("open", "local") and r.get("improved")]
    for index, (first, k) in enumerate(ks):
        row = spent.get((first, k))
        if not settled(row, args.k_budget, args.max_frontier):
            pass_dir = work / f"k{k}"
            row = {"stage": "k", "k": k, "first": first, "turn_only": args.turn_first,
                   "budget": args.k_budget, "max_frontier": args.max_frontier,
                   **run_pass(name, pass_args(day_file, k, first, best, args, pass_dir), pass_dir, best,
                              out_dir, f"plans of at most {k} launch orders (from {first})")}
            append(ledger, row)
            print(f"{name}: K = {k}: {row['turn']}/{row['lost']}"
                  f"{' (better)' if row['improved'] else ''}"
                  f"{', exact' if row['exact'] else ', NOT exact'} in {row['seconds']:.0f}s", flush=True)
        assert row is not None
        best = min(best, (row["turn"], row["lost"]))
        if row["exact"]:
            exact_up_to = max(exact_up_to, k)
        else:
            stopped = f"the pass for K = {k} did not finish"
            break
        quiet = 0 if row["improved"] else quiet + 1
        if index == 0 and worth_proving():   # the bound is tight by now: try for a full proof
            proved = prove("early")
            if proved:
                stopped = f"proved optimal ({proved})"
                break
        if index == 0 and not proved:   # the exact stages could not settle it: look for lines they cannot reach
            for stage, run, on in (("open", open_stage, args.open_depth > 0 and args.workers > 0),
                                   ("local", local_stage, args.local_seconds > 0)):
                done_row = next((r for r in rows if r["stage"] == stage), None)
                if done_row is None and on:
                    done_row = {"stage": stage, **run(name, day_file, work, best, best_path, out_dir, args)}
                    append(ledger, done_row)
                    print(f"{name}: {stage} search: {done_row['turn']}/{done_row['lost']}"
                          f"{' (better)' if done_row['improved'] else ''} in {done_row['seconds']:.0f}s", flush=True)
                if done_row is not None:
                    best = min(best, (done_row["turn"], done_row["lost"]))
                    if done_row["improved"]:
                        improved_by.append(stage)
        if index == 0 and not proved and improved_by and worth_proving():
            proved = prove("late")
            if proved:
                stopped = f"proved optimal ({proved})"
                break
        if quiet >= args.patience:
            stopped = f"{args.patience} passes in a row improved nothing"
            break
    tiebreak = next((r for r in rows if r["stage"] == "tiebreak" and r["k"] == exact_up_to), None)
    if tiebreak is None and args.tiebreak and args.turn_first and exact_up_to and not proved:
        pass_dir = work / f"tiebreak{exact_up_to}"
        tiebreak = {"stage": "tiebreak", "k": exact_up_to,
                    **run_pass(name, pass_args(day_file, exact_up_to, exact_up_to, best, args, pass_dir, turn_only=False),
                               pass_dir, best, out_dir, f"fewest ships lost, plans of at most {exact_up_to} launch orders")}
        append(ledger, tiebreak)
        print(f"{name}: tiebreak, K = {exact_up_to}: {tiebreak['turn']}/{tiebreak['lost']}"
              f"{', exact' if tiebreak['exact'] else ', NOT exact'} in {tiebreak['seconds']:.0f}s", flush=True)
    if tiebreak is not None:
        best = min(best, (tiebreak["turn"], tiebreak["lost"]))
    deciding = [r for r in read_rows(ledger) if r["stage"] in ("k", "proof") and r["exact"]]
    summary = {"stage": "done", "name": name, "turn": best[0], "lost": best[1],
               "turn_only": any(r.get("turn_only") for r in deciding)
               and not (tiebreak is not None and tiebreak["exact"]),
               "beam": [beam_row["turn"], beam_row["lost"]], "exact_up_to": exact_up_to,
               "improved_by": improved_by, "floor": root_floor, "proved": proved, "stopped": stopped or f"reached K = {args.max_k}"}
    if best_path.exists():
        human = build_state(cfg, cfg.seed).human()
        assert human is not None
        won, turn, lost = ps.check_line(replay.load(best_path), human.id)
        summary["replays"] = bool(won and (turn, lost) == (best[0], best[1]))
    now = read_rows(ledger)
    summary["verdict"] = verdict(summary, latest(now, "k", lambda r: (r["first"], r["k"])),
                                 latest(now, "proof", lambda r: (r.get("when", "early"), r["splits"])),
                                 args, source or day_file)
    last = next((r for r in reversed(rows) if r["stage"] == "done"), None)
    if last != summary:   # a rerun that changed nothing leaves the ledger alone
        append(ledger, summary)
    return summary


def parser() -> argparse.ArgumentParser:
    """The command line, also read by ``tools/puzzle_par.py`` for its budgets."""
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
    ap.add_argument("--proof-budget", type=float, default=300.0,
                    help="seconds for each full-search proof attempt after the first passes (0: skip it)")
    ap.add_argument("--no-turn-first", dest="turn_first", action="store_false",
                    help="make the passes and proofs minimise ships lost as well (slower); by default they "
                         "look only for a win on an earlier turn, since turns come first")
    ap.add_argument("--tiebreak", action="store_true",
                    help="after the passes, one more for the fewest ships lost at the best turn, "
                         "over plans of at most the exact K")
    ap.add_argument("--proof-gap", type=int, default=2,
                    help="try a proof only when the best line is at most this many turns over the floor")
    ap.add_argument("--no-proof-splits", dest="proof_splits", action="store_false",
                    help="do not follow a finished proof with one that also allows two-lane sends")
    ap.add_argument("--open-depth", type=int, default=3,
                    help="turns enumerated by the opening search (0: skip it); run when no proof settled the setup")
    ap.add_argument("--open-width", type=int, default=30, help="beam width for each opening, first pass")
    ap.add_argument("--open-budget", type=float, default=2.0, help="seconds for each opening, first pass")
    ap.add_argument("--refine", type=int, default=100, help="the best N openings are then redone widely (0: skip)")
    ap.add_argument("--refine-width", type=int, default=100)
    ap.add_argument("--refine-budget", type=float, default=40.0)
    ap.add_argument("--workers", type=int, default=1, help="processes for the opening search (the K passes use one)")
    ap.add_argument("--local-seconds", type=float, default=60.0, help="local search from the best line (0: skip)")
    ap.add_argument("--max-frontier", type=int, default=1_000_000, help="boards kept per layer in a pass")
    ap.add_argument("--max-turns", type=int, default=120)
    ap.add_argument("--results-dir", type=Path, default=RESULTS)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if not (args.daily or args.days or args.last):
        ap.error("give --daily FILE, --days DAY or --last N")

    ai.load_models()
    ai.set_budget_scale(math.inf)
    found = targets(args)
    summaries = [solve(name, data, args, source) for name, data, source in found]
    print("\nname                  beam      best      exact up to   proved           stopped")
    for s in summaries:
        beam = f"{s['beam'][0]:.0f}/{s['beam'][1]:.0f}" if s["beam"][0] < math.inf else "-"
        best = f"{s['turn']:.0f}/{s['lost']:.0f}" if s["turn"] < math.inf else "none"
        print(f"{s['name']:<21} {beam:<9} {best:<9} K = {s['exact_up_to']:<8} {s['proved'] or '-':<16} {s['stopped']}"
              f"{'' if s.get('replays', True) else '  (BEST LINE DOES NOT REPLAY)'}")
    for s in summaries:
        v = s["verdict"]
        print(f"\n{s['name']}: {v['headline']}")
        print("  rests on:")
        for line in v["constraints"]:
            print(f"    - {line}")
        if v["not_covered"]:
            print("  not covered:")
            for line in v["not_covered"]:
                print(f"    - {line}")
        if v["next"]:
            print("  to be surer, run next:")
            for i, option in enumerate(v["next"], 1):
                print(f"    ({i}) {option['why']}  [{option['est']}]\n        {option['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
