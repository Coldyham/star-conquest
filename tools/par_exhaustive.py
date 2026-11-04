#!/usr/bin/env python3
"""Exhaustive search for the best score on one small setup (a daily puzzle).

    uv run python tools/par_exhaustive.py --daily tools/par_inputs/2026-11-04_puzzle.json
    uv run python tools/par_exhaustive.py --daily FILE --levels all      # every launch size
    uv run python tools/par_exhaustive.py --log games/game_x.json --out best.json

Where `par_search.py` is a beam (it samples a few candidate orders a turn), this
walks **every** order set in a stated model, layer by layer, on the game's real
dice (`replay.reseed` + `engine._Dice`, so a rewind offers nothing new):

* each of the human's systems holds, or sends part of its garrison down **one**
  lane a turn (``--levels``: ``coarse`` = all or nothing, ``quarters`` = also a
  quarter, half, three quarters and all-but-one, ``all`` = every count);
* boards that match (`par_search.board_key`) merge, keeping the fewest losses;
* a board whose admissible floor (`par_search.floor`) cannot beat the incumbent
  is dropped. The incumbent starts from a short `par_search` beam, then improves.

If it finishes with ``exact`` true, the reported (turns, ships lost) is the
optimum **within that order model**: no line of those orders does better. A
state whose turn would fan out past ``--max-branch`` falls back to the coarse
levels for that one board and clears ``exact``; a layer wider than
``--max-frontier`` keeps the best-floored boards and clears it too. It never
claims more than that.

Every finished layer and every new best line is appended to
``results/par/<name>.jsonl`` as it happens (the best line also goes to
``results/par/<name>.best.json`` as a real `replay.GameLog`). A rerun reads that
file and starts from the best bound it finds, so finished work is not repeated.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay
from starconquest.model import GameState, Order
from starconquest.settings import Settings
from tools import par_search as ps

LEVELS = ("coarse", "quarters", "all")


def launch_counts(ships: int, levels: str) -> list[int]:
    """The launch sizes tried from a garrison of ``ships``."""
    if ships <= 0:
        return []
    if levels == "all":
        return list(range(1, ships + 1))
    if levels == "coarse":
        return [ships]
    picks = {ships, ships - 1, ships // 2, ships // 4, (3 * ships) // 4}
    return sorted(n for n in picks if 1 <= n <= ships)


def order_sets(board: GameState, me: int, levels: str, max_branch: int):
    """Every order set for ``me`` this turn under ``levels``, and whether it had
    to fall back to coarse because the set was too large."""
    def options(levels_: str) -> list[list[list[Order]]]:
        per_source: list[list[list[Order]]] = []
        for s in board.systems.values():
            if s.owner_id != me or s.ships <= 0:
                continue
            opts: list[list[Order]] = [[]]
            for nbr in s.neighbors:
                for n in launch_counts(s.ships, levels_):
                    opts.append([Order(me, s.id, nbr, n)])
            per_source.append(opts)
        return per_source

    exact = True
    per_source = options(levels)
    size = math.prod(len(o) for o in per_source) if per_source else 1
    if size > max_branch and levels != "coarse":
        per_source, exact = options("coarse"), False
    return ([o for part in combo for o in part] for combo in itertools.product(*per_source)), exact


def settings_of(args: argparse.Namespace) -> tuple[Settings, int, str]:
    if args.daily:
        data = json.loads(Path(args.daily).read_text())
        cfg = Settings.from_dict(data["settings"])
        assert cfg.seed is not None
        return cfg, cfg.seed, f"{data['day']}_{data['kind']}"
    log = replay.load(Path(args.log))
    if not log.is_current:
        raise SystemExit(f"{args.log} was recorded under older rules")
    return Settings.from_dict(log.settings), log.seed, Path(args.log).stem


def resume_bound(path: Path) -> tuple[float, float]:
    best = (math.inf, math.inf)
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") == "best":
                best = min(best, (row["turn"], row["lost"]))
    return best


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--daily", help="a results/daily/<day>_<kind>.json from tools/daily.py --dry-run")
    src.add_argument("--log", help="the setup of this saved match")
    ap.add_argument("--levels", choices=LEVELS, default="quarters")
    ap.add_argument("--max-branch", type=int, default=4000, help="order sets per board before falling back")
    ap.add_argument("--max-frontier", type=int, default=200_000, help="boards kept per layer")
    ap.add_argument("--assume-turn", type=int,
                    help="a turn you know is reachable: prune as if a win on it (any losses) were in hand")
    ap.add_argument("--seed-budget", type=float, default=20.0, help="seconds of beam search for the first incumbent")
    ap.add_argument("--budget", type=float, default=3600.0, help="seconds in all")
    ap.add_argument("--max-turns", type=int, default=120)
    ap.add_argument("--out", help="also save the best line here")
    args = ap.parse_args(argv)

    ai.load_models()
    ai.set_budget_scale(math.inf)
    cfg, seed, name = settings_of(args)
    out_dir = ROOT / "results" / "par"
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger, best_path = out_dir / f"{name}.jsonl", out_dir / f"{name}.best.json"

    def record(row: dict) -> None:
        with ledger.open("a") as f:
            f.write(json.dumps(row) + "\n")
            f.flush()

    started = time.perf_counter()
    search = ps.Search(cfg, seed, dice="honest", width=6, budget=args.seed_budget,
                       max_turns=args.max_turns)
    me = search.me
    root_floor = ps.floor(search.root, me)
    print(f"{name}: {len(search.root.systems)} systems, floor turn {root_floor:.0f}, "
          f"levels {args.levels}", flush=True)
    best = search.run()
    bound = min((best.turn, best.lost), resume_bound(ledger))
    if args.assume_turn is not None and args.assume_turn < bound[0]:
        bound = (args.assume_turn, math.inf)
    print(f"incumbent: turn {bound[0]}, lost {bound[1]} (beam {best.turn}/{best.lost})", flush=True)
    if best.turn < math.inf:
        record({"event": "best", "turn": best.turn, "lost": best.lost, "source": "beam"})
        log = search.log()
        log.path = best_path
        log.save()

    exact = True
    incumbent = {"turn": bound[0], "lost": bound[1]}
    layer: dict[tuple, ps.Node] = {ps.board_key(search.root): ps.Node(ps.clone(search.root))}
    depth = 0
    while layer and time.perf_counter() - started < args.budget:
        depth += 1
        nxt: dict[tuple, ps.Node] = {}
        expanded = wins = 0
        for node in layer.values():
            sets, node_exact = order_sets(node.board, me, args.levels, args.max_branch)
            exact &= node_exact
            for orders in sets:
                board = ps.clone(node.board)
                rec = ps.step(board, orders)
                expanded += 1
                child = ps.Node(board, node, rec)
                lost = board.players[me].ships_lost
                if board.winner is not None:
                    if board.winner == me and (board.turn, lost) < (incumbent["turn"], incumbent["lost"]):
                        incumbent.update(turn=board.turn, lost=lost)
                        wins += 1
                        record({"event": "best", "turn": board.turn, "lost": lost, "source": "exhaustive"})
                        log = replay.GameLog(seed=seed, settings=cfg.to_dict(), strategies=dict(search.strategies))
                        for r in child.records():
                            log.record_turn(r, human_ai=False)
                        log.mark_finished(me)
                        log.path = best_path
                        log.save()
                        print(f"  best -> turn {board.turn}, lost {lost}", flush=True)
                    continue
                if board.turn >= args.max_turns:
                    continue
                bound_turn = ps.floor(board, me, int(min(incumbent["turn"], args.max_turns)))
                if bound_turn == math.inf or bound_turn > incumbent["turn"] or \
                        (bound_turn == incumbent["turn"] and lost >= incumbent["lost"]):
                    continue
                key = ps.board_key(board)
                old = nxt.get(key)
                if old is None or lost < old.board.players[me].ships_lost:
                    nxt[key] = child
        if len(nxt) > args.max_frontier:
            exact = False
            keep = sorted(nxt.items(), key=lambda kv: (ps.floor(kv[1].board, me), kv[1].board.players[me].ships_lost))
            nxt = dict(keep[: args.max_frontier])
        layer = nxt
        record({"event": "layer", "depth": depth, "expanded": expanded, "frontier": len(layer),
                "best": [incumbent["turn"], incumbent["lost"]], "exact": exact})
        print(f"layer {depth}: {expanded} expanded, {len(layer)} kept, best "
              f"{incumbent['turn']}/{incumbent['lost']}, {time.perf_counter() - started:.0f}s", flush=True)

    finished = not layer
    exact &= finished
    verdict = "optimal within the order model" if exact else "best found (search not exact)"
    print(f"result: turn {incumbent['turn']}, lost {incumbent['lost']}; floor {root_floor:.0f}; {verdict}")
    record({"event": "done", "turn": incumbent["turn"], "lost": incumbent["lost"], "exact": exact})
    if best_path.exists():
        saved = replay.load(best_path)
        won, turn, lost = ps.check_line(saved, me)
        print(f"best line {best_path}: won={won} turn {turn} lost {lost} (replays through reconstruct)")
        if args.out:
            saved.path = Path(args.out)
            saved.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
