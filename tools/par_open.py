#!/usr/bin/env python3
"""Find the best score on one setup by searching its openings, then finishing each with a beam.

    uv run python tools/par_open.py --daily results/par/live_inputs/2026-10-30_puzzle.json --workers 6
    uv run python tools/par_open.py --daily FILE --depth 3 --width 50 --budget 4 --workers 6

The beam ranks a board by how a bot would finish from it, which undervalues an opening whose
payoff comes later (a first strike sized to a neutral's garrison, then a relay), so a plain beam
from the start settles on a worse line. Here the first ``--depth`` turns are enumerated instead:
every order set `par_search.candidates` offers (every bot's orders, all-in sends, strike sizes
and holding), boards that match merged. Each distinct board at that depth is finished with a short
beam. The best of all is kept. It finds a line and proves nothing.

Every finished opening appends one JSON line to ``results/par/open/<name>.jsonl`` as it completes
and a rerun skips them. The best line is ``<name>.best.json`` (a real `replay.GameLog`).
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai
from starconquest.model import GameState, Order
from starconquest.settings import Settings
from tools import par_exhaustive as pe
from tools import par_search as ps

_W: dict = {}


def _pack(orders: list[Order]) -> tuple:
    return tuple(sorted((o.source_id, o.dest_id, o.ships) for o in orders))


def after(root: GameState, me: int, turns: list[tuple]) -> GameState:
    board = ps.clone(root)
    for packed in turns:
        ps.step(board, [Order(me, s, d, n) for s, d, n in packed])
    return board


def enumerate_openings(root: GameState, me: int, depth: int, horizon: int,
                       model: str = "strike") -> list[list[tuple]]:
    """Distinct openings of ``depth`` turns (each a list of per-turn packed launches). ``strike``
    offers every combination across systems of `par_exhaustive`'s strike sends; ``candidates``
    only the beam's own order sets."""
    layer: list[tuple[GameState, list[tuple]]] = [(root, [])]
    for _ in range(depth):
        nxt: dict[tuple, tuple[GameState, list[tuple]]] = {}
        for board, path in layer:
            sets = pe.order_sets(board, me, "strike", 10**9)[0] if model == "strike" else ps.candidates(board, me)
            for orders in sets:
                child = ps.clone(board)
                ps.step(child, orders)
                if child.winner is not None or child.turn >= horizon:
                    continue
                key = ps.board_key(child)
                if key not in nxt:
                    nxt[key] = (child, [*path, _pack(orders)])
        layer = list(nxt.values())
        print(f"  depth {len(layer[0][1]) if layer else 0}: {len(layer)} distinct boards", flush=True)
    return [path for _, path in layer]


def init_worker(cfg_dict: dict, seed: int, width: int, budget: float, max_turns: int) -> None:
    ai.load_models()
    ai.set_budget_scale(math.inf)
    _W.update(cfg=Settings.from_dict(cfg_dict), seed=seed, width=width, budget=budget, max_turns=max_turns)


def finish(opening: list[tuple]) -> dict:
    """Run a short beam from the board the opening leads to."""
    search = ps.Search(_W["cfg"], _W["seed"], dice="honest", width=_W["width"], budget=_W["budget"],
                       max_turns=_W["max_turns"], strikes=True)
    search.root = after(search.root, search.me, opening)
    best = search.run()
    row: dict = {"opening": [list(map(list, t)) for t in opening], "turn": best.turn, "lost": best.lost}
    if best.turn < math.inf:
        log = search.log()
        me = search.me
        row["rest"] = [[[o.source_id, o.dest_id, o.ships] for o in log.orders_for(t) if o.owner_id == me]
                       for t in range(len(log.turns))]
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--daily", required=True)
    ap.add_argument("--depth", type=int, default=3, help="turns enumerated before the beam takes over")
    ap.add_argument("--model", choices=("strike", "candidates"), default="strike")
    ap.add_argument("--refine", type=int, default=0,
                    help="instead, re-finish the N best openings of an earlier run with this run's "
                         "--width and --budget (its ledger is <name>.refine.jsonl)")
    ap.add_argument("--width", type=int, default=50, help="beam width for each finish")
    ap.add_argument("--budget", type=float, default=4.0, help="seconds of beam for each finish")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--horizon", type=int, default=14, help="openings still undecided by this turn are dropped")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results" / "par" / "open")
    args = ap.parse_args(argv)

    ai.load_models()
    ai.set_budget_scale(math.inf)
    data = json.loads(Path(args.daily).read_text())
    name = f"{data['day']}_{data['kind']}"
    cfg = Settings.from_dict(data["settings"])
    assert cfg.seed is not None
    args.results_dir.mkdir(parents=True, exist_ok=True)
    first_ledger = args.results_dir / f"{name}.jsonl"
    suffix = ".refine" if args.refine else ""
    ledger, best_path = args.results_dir / f"{name}{suffix}.jsonl", args.results_dir / f"{name}{suffix}.best.json"

    done: set[str] = set()
    best: tuple[float, float] = (math.inf, math.inf)
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            done.add(json.dumps(row["opening"]))
            best = min(best, (row["turn"], row["lost"]))
    print(f"{name}: depth {args.depth}, width {args.width}, {args.budget}s per finish; "
          f"{len(done)} openings already done, best so far {best}", flush=True)

    search = ps.Search(cfg, cfg.seed, dice="honest", width=1, budget=1, max_turns=args.max_turns)
    root, me = search.root, search.me
    started = time.perf_counter()
    if args.refine:
        cheap = sorted((json.loads(line) for line in first_ledger.read_text().splitlines()),
                       key=lambda r: (r["turn"], r["lost"]))[:args.refine]
        openings = [[tuple(map(tuple, t)) for t in r["opening"]] for r in cheap]
    else:
        openings = enumerate_openings(root, me, args.depth, args.horizon, args.model)
    todo = [o for o in openings if json.dumps([list(map(list, t)) for t in o]) not in done]
    print(f"{len(openings)} openings, {len(todo)} to finish ({time.perf_counter() - started:.0f}s to enumerate)",
          flush=True)

    pool = multiprocessing.get_context("spawn").Pool(
        args.workers, init_worker, (cfg.to_dict(), cfg.seed, args.width, args.budget, args.max_turns))
    count = 0
    try:
        for row in pool.imap_unordered(finish, todo):
            count += 1
            with ledger.open("a") as f:
                f.write(json.dumps(row) + "\n")
                f.flush()
            score = (row["turn"], row["lost"])
            if score < best:
                best = score
                packed = [tuple(map(tuple, t)) for t in row["opening"]] + [tuple(map(tuple, t)) for t in row["rest"]]
                path: tuple = ()
                for turn in packed:
                    path = (path, turn)
                log = pe.line_log(root, cfg, cfg.seed, search.strategies, me, path)
                log.path = best_path
                log.save()
                print(f"[{time.perf_counter() - started:5.0f}s, {count}/{len(todo)}] best {best[0]:.0f}/{best[1]:.0f}",
                      flush=True)
            elif count % 50 == 0:
                print(f"[{time.perf_counter() - started:5.0f}s] {count}/{len(todo)} finished, best {best}", flush=True)
    finally:
        pool.terminate()
    print(f"done: best {best[0]:.0f}/{best[1]:.0f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
