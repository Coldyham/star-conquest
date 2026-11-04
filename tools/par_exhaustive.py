#!/usr/bin/env python3
"""Exhaustive search for the best score on one small setup (a daily puzzle).

    uv run python tools/par_exhaustive.py --daily tools/par_inputs/2026-11-04_puzzle.json
    uv run python tools/par_exhaustive.py --daily FILE --levels all --splits   # every launch size, two-lane splits
    uv run python tools/par_exhaustive.py --daily FILE --workers 8             # all cores
    uv run python tools/par_exhaustive.py --log games/game_x.json --out best.json

Where `par_search.py` is a beam (it samples a few candidate orders a turn), this
walks **every** order set in a stated model, layer by layer, on the game's real
dice (`replay.reseed` + `engine._Dice`, so a rewind offers nothing new):

* each of the human's systems holds, sends part of its garrison down **one**
  lane a turn, or (``--splits``) down **two** lanes (``--levels``: ``coarse`` =
  all or nothing, ``quarters`` = also a quarter, half, three quarters and
  all-but-one, ``all`` = every count);
* boards that match (`par_search.board_key`) merge, keeping the fewest losses;
* a board whose admissible floor cannot beat the incumbent is dropped. The
  incumbent starts from a short `par_search` beam, then improves.

If it finishes with ``exact`` true, the reported (turns, ships lost) is the
optimum **within that order model**: no line of those orders does better. A
board whose turn would fan out past ``--max-branch`` falls back (first to no
splits, then to the coarse levels) and clears ``exact``; a layer wider than
``--max-frontier`` keeps the best-ranked boards (``--rank``) and clears it too; ``--ratio``
is a heuristic prune and clears it as well. It never claims more than that.

Speed, none of it costing exactness (`tests/test_par_exhaustive.py`):

* every child of one board shares the rivals' orders (turns resolve against the
  same start-of-turn state), so each rival decides **once per board**, not once
  per child;
* the floor is a table lookup (all-pairs lane times, built once per horizon),
  equal to `par_search.floor`;
* ``--workers`` expands a layer in parallel processes (the result does not depend
  on the count).

Every finished layer and every new best line is appended to
``results/par/<name>.jsonl`` as it happens (the best line also goes to
``results/par/<name>.best.json`` as a real `replay.GameLog`). A rerun reads that
file and starts from the best bound it finds, so finished work is not repeated.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, replay
from starconquest.model import GameState, Order
from starconquest.settings import Settings, build_state
from tools import par_search as ps

LEVELS = ("coarse", "quarters", "all")
CHUNK_BOARDS = 256   # boards per task, so a wave in flight stays small


# --------------------------------------------------------------------------- #
# Moves
# --------------------------------------------------------------------------- #
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


def _limited(per_source: list[list[list[Order]]], limit: int):
    """The order sets using at most ``limit`` orders: holding, then every choice of
    up to ``limit`` systems that launch."""
    yield []
    for r in range(1, min(limit, len(per_source)) + 1):
        for chosen in itertools.combinations(range(len(per_source)), r):
            for combo in itertools.product(*(per_source[i][1:] for i in chosen)):
                orders = [o for part in combo for o in part]
                if len(orders) <= limit:
                    yield orders


def order_sets(board: GameState, me: int, levels: str, max_branch: int, splits: bool = False,
               limit: int | None = None):
    """Every order set for ``me`` this turn under ``levels``, and whether it had
    to fall back because the set was too large. With ``splits`` a system may also
    send down two different lanes in one turn (a ships down one, b down another,
    a + b <= its garrison). With ``limit`` only sets of at most that many orders
    (a plan's remaining launch budget), which needs no fallback."""
    def options(levels_: str, splits_: bool) -> list[list[list[Order]]]:
        per_source: list[list[list[Order]]] = []
        for s in board.systems.values():
            if s.owner_id != me or s.ships <= 0:
                continue
            opts: list[list[Order]] = [[]]
            for nbr in s.neighbors:
                for n in launch_counts(s.ships, levels_):
                    opts.append([Order(me, s.id, nbr, n)])
            if splits_:
                for n1, n2 in itertools.combinations(s.neighbors, 2):
                    for a in launch_counts(s.ships, levels_):
                        for b in launch_counts(s.ships - a, levels_):
                            opts.append([Order(me, s.id, n1, a), Order(me, s.id, n2, b)])
            per_source.append(opts)
        return per_source

    def size_of(per_source: list[list[list[Order]]]) -> int:
        return math.prod(len(o) for o in per_source) if per_source else 1

    exact = True
    per_source = options(levels, splits)
    if limit is not None:
        return _limited(per_source, limit), True
    if size_of(per_source) > max_branch and splits:
        per_source, exact = options(levels, False), False
    if size_of(per_source) > max_branch and levels != "coarse":
        per_source, exact = options("coarse", False), False
    return ([o for part in combo for o in part] for combo in itertools.product(*per_source)), exact


# --------------------------------------------------------------------------- #
# Pruning
# --------------------------------------------------------------------------- #
class FloorTable:
    """`par_search.floor` as lookups. Lane times are fixed at a horizon turn and
    the lane graph never changes, so all-pairs distances are built once."""

    def __init__(self) -> None:
        self.tables: dict[int, dict[int, dict[int, int]]] = {}

    def _build(self, board: GameState, horizon: int) -> dict[int, dict[int, int]]:
        import heapq
        weight: dict[int, list[tuple[int, int]]] = {sid: [] for sid in board.systems}
        for sid, s in board.systems.items():
            for nbr in s.neighbors:
                lane = board.lanes[frozenset((sid, nbr))]
                weight[sid].append((nbr, config.travel_turns_at_length(lane.length_ly, horizon)))
        table: dict[int, dict[int, int]] = {}
        for src in board.systems:
            dist: dict[int, int] = {}
            heap = [(0, src)]
            while heap:
                d, sid = heapq.heappop(heap)
                if sid in dist:
                    continue
                dist[sid] = d
                for nbr, w in weight[sid]:
                    if nbr not in dist:
                        heapq.heappush(heap, (d + w, nbr))
            table[src] = dist
        return table

    def floor(self, board: GameState, me: int, horizon: int) -> float:
        rivals = ps.rivals_of(board, me)
        if not rivals:
            return float(board.turn)
        table = self.tables.get(horizon)
        if table is None:
            table = self.tables[horizon] = self._build(board, horizon)
        sources: list[tuple[int, int]] = [(0, s.id) for s in board.systems.values() if s.owner_id == me]
        sources += [(max(f.turns_remaining, 0), f.dest_id) for f in board.fleets if f.owner_id == me]
        if not sources:
            return math.inf
        h = 0.0
        for s in board.systems.values():
            if s.owner_id in rivals:
                h = max(h, min((t + table[src].get(s.id, math.inf) for t, src in sources), default=math.inf))
        for f in board.fleets:
            if f.owner_id in rivals:
                h = max(h, f.turns_remaining)
        return board.turn + h


def potential(board: GameState, pid: int, turns_left: int) -> float:
    """Ships on systems and in flight, plus what production adds by the deadline.
    ``production`` is turns per ship, so a system emits 1/production a turn."""
    ships = sum(s.ships for s in board.systems.values() if s.owner_id == pid)
    ships += sum(f.ships for f in board.fleets if f.owner_id == pid)
    rate = sum(1.0 / s.production for s in board.systems.values() if s.owner_id == pid)
    return ships + rate * max(turns_left, 0)


def hopeless(board: GameState, me: int, deadline: int, ratio: float) -> bool:
    """True when ``me`` has far less potential than the strongest rival.
    A heuristic: it cannot see captures, so it is not exact."""
    left = deadline - board.turn
    mine = potential(board, me, left)
    theirs = max((potential(board, r, left) for r in ps.rivals_of(board, me)), default=0.0)
    return mine < ratio * theirs


RANKS = ("ratio", "floor")


def rank_key(board: GameState, me: int, horizon: int, floors: FloorTable, mode: str) -> tuple:
    """Where a board sorts when a layer is too wide to keep (smaller is kept first).

    ``floor``: the earliest possible win, then fewest losses. It favours boards
    that have not fought, so it throws away a line that trades ships early.
    ``ratio`` (the default): material first, `potential` over the strongest
    rival's, then the floor and the losses. On the 2026-11-04 puzzle's known
    10-turn line it ranks #1 in layer 6 where ``floor`` ranks #426,307 of 536,731."""
    lost = board.players[me].ships_lost
    floor = floors.floor(board, me, horizon)
    if mode == "floor":
        return (floor, lost)
    left = horizon - board.turn
    mine = potential(board, me, left)
    theirs = max((potential(board, r, left) for r in ps.rivals_of(board, me)), default=0.0)
    return (-(mine / max(theirs, 0.1)), floor, lost)


def trim(nxt: dict, keep: int, me: int, horizon: int, floors: FloorTable, mode: str) -> dict:
    """The ``keep`` best-ranked boards of ``nxt`` (values are ``(lost, board, path)``)."""
    ranked = sorted(nxt.items(), key=lambda kv: rank_key(kv[1][1], me, horizon, floors, mode))
    return dict(ranked[:keep])


# --------------------------------------------------------------------------- #
# Expanding a layer (in this process, or in a worker)
# --------------------------------------------------------------------------- #
_W: dict = {}


def _dup(obj):
    """A shallow copy of a plain (slot-less) dataclass instance, without the
    generic machinery of `copy.copy`."""
    out = object.__new__(obj.__class__)
    out.__dict__.update(obj.__dict__)
    return out


def fast_clone(state: GameState, rng: random.Random) -> GameState:
    """`par_search.clone`, minus its per-board rng copy: ``rng`` is a scratch
    stream every board here shares, since each turn is `replay.reseed`-ed before
    anything reads it."""
    out = _dup(state)
    out.systems = {}
    for sid, s in state.systems.items():
        out.systems[sid] = _dup(s)   # `neighbors` is shared: only map generation writes it
    out.fleets = [_dup(f) for f in state.fleets]
    out.players = {}
    for pid, p in state.players.items():
        c = _dup(p)
        c.ai_params = _dup(p.ai_params)
        out.players[pid] = c
    out.rng = rng   # `adjacency` and `lanes` stay shared too
    return out


def configure(cfg_dict: dict, seed: int, me: int, levels: str, max_branch: int, splits: bool,
              ratio: float, max_turns: int) -> None:
    """Set a process up to expand boards: the rules' globals come from
    ``build_state``, as they do for the game itself."""
    build_state(Settings.from_dict(cfg_dict), seed)
    _W.clear()
    _W.update(me=me, levels=levels, max_branch=max_branch, splits=splits, ratio=ratio,
              max_turns=max_turns, floors=FloorTable())


def init_worker(*params) -> None:
    ai.load_models()
    ai.set_budget_scale(math.inf)
    configure(*params)


def _shared_decide():
    """A ``decide`` that asks each seat once and hands every child of the board
    the same orders. Valid because every order set of one board resolves against
    the same unchanged start of turn, and a seat's decision cannot see them."""
    cache: dict[int, list[Order]] = {}

    def decide(state: GameState, pid: int) -> list[Order]:
        if pid not in cache:
            cache[pid] = ai.decide(state, pid)
        return [copy.copy(o) for o in cache[pid]]
    return decide


def expand_chunk(task):
    """Expand ``boards`` by every order set. Returns the surviving children as
    ``(key, lost, board, parent position, orders)`` (equal boards already merged;
    with a launch limit, equal boards that spent the same number of launches),
    the wins that beat the incumbent, whether every board was fully enumerated,
    and how many order sets were stepped."""
    boards, inc_turn, inc_lost, max_launches = task
    me, levels = _W["me"], _W["levels"]
    floors: FloorTable = _W["floors"]
    horizon = int(min(inc_turn, _W["max_turns"]))
    kept: dict[tuple, tuple] = {}
    wins: list[tuple] = []
    expanded = 0
    exact = True
    scratch = random.Random()
    for pos, node_board in enumerate(boards):
        used = getattr(node_board, "launches", 0)
        budget = None if max_launches is None else max_launches - used
        sets, ok = order_sets(node_board, me, levels, _W["max_branch"], _W["splits"], budget)
        exact &= ok
        decide = _shared_decide()
        for orders in sets:
            board = fast_clone(node_board, scratch)
            replay.reseed(board)
            engine.end_turn(board, human_orders=orders, decide=decide)
            expanded += 1
            lost = board.players[me].ships_lost
            packed = tuple((o.source_id, o.dest_id, o.ships) for o in orders)
            if board.winner is not None:
                if board.winner == me and (board.turn, lost) < (inc_turn, inc_lost):
                    wins.append((board.turn, lost, pos, packed))
                continue
            if board.turn >= _W["max_turns"]:
                continue
            bound = floors.floor(board, me, horizon)
            if bound == math.inf or bound > inc_turn or (bound == inc_turn and lost >= inc_lost):
                continue
            if _W["ratio"] and hopeless(board, me, horizon, _W["ratio"]):
                exact = False
                continue
            key = ps.board_key(board)
            if max_launches is not None:
                spent = used + len(orders)   # a spent launch budget is part of the position
                setattr(board, "launches", spent)  # noqa: B010 - GameState has no such field
                key = (key, spent)
            old = kept.get(key)
            if old is None or lost < old[0]:
                kept[key] = (lost, board, pos, packed)
    children = [(key, lost, board, pos, packed) for key, (lost, board, pos, packed) in kept.items()]
    return children, wins, exact, expanded


# --------------------------------------------------------------------------- #
# Inputs and the ledger
# --------------------------------------------------------------------------- #
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


def line_log(root: GameState, cfg: Settings, seed: int, strategies: dict[int, str], me: int,
             path: tuple) -> replay.GameLog:
    """Replay ``path`` (a linked list of per-turn launches) from the start and
    record it as a `GameLog`."""
    turns: list[tuple] = []
    while path:
        path, packed = path
        turns.append(packed)
    board = ps.clone(root)
    log = replay.GameLog(seed=seed, settings=cfg.to_dict(), strategies=dict(strategies))
    for packed in reversed(turns):
        record = ps.step(board, [Order(me, s, d, n) for s, d, n in packed])
        log.record_turn(record, human_ai=False)
    log.mark_finished(me)
    return log


def waves(pool, tasks: list, workers: int):
    """``pool.map`` over ``tasks`` a few chunks per worker at a time, in order, so
    finished chunks are merged (and freed) instead of piling up in the queue."""
    step = max(1, workers * 2)
    for i in range(0, len(tasks), step):
        yield from pool.map(expand_chunk, tasks[i:i + step])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--daily", help="a results/daily/<day>_<kind>.json from tools/daily.py --dry-run")
    src.add_argument("--log", help="the setup of this saved match")
    ap.add_argument("--levels", choices=LEVELS, default="quarters")
    ap.add_argument("--splits", action="store_true",
                    help="also try one system sending down two different lanes in a turn")
    ap.add_argument("--max-branch", type=int, default=4000, help="order sets per board before falling back")
    ap.add_argument("--max-frontier", type=int, default=200_000, help="boards kept per layer")
    ap.add_argument("--rank", choices=RANKS, default="ratio",
                    help="which boards survive when a layer exceeds --max-frontier "
                         "(ratio: material first; floor: earliest possible win first)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1, help="processes (1 = this one)")
    ap.add_argument("--assume-turn", type=int,
                    help="a turn you know is reachable: prune as if a win on it (any losses) were in hand")
    ap.add_argument("--assume-lost", type=int,
                    help="with --assume-turn: also assume that many ships lost, so only a strictly "
                         "better line (sooner, or the same turn with fewer lost) counts")
    ap.add_argument("--max-launches", type=int, default=0,
                    help="search plans of at most K launch orders, K = 1, 2, ... in turn: waiting is free, "
                         "so a line with few launches is found in seconds. Exact per K, no cap needed")
    ap.add_argument("--ratio", type=float, default=0.0,
                    help="drop a board whose potential is under RATIO x the strongest rival's "
                         "(0 = off; 0.5 is a cautious start). Not exact.")
    ap.add_argument("--seed-budget", type=float, default=20.0, help="seconds of beam search for the first incumbent")
    ap.add_argument("--budget", type=float, default=3600.0, help="seconds in all")
    ap.add_argument("--max-turns", type=int, default=120)
    ap.add_argument("--results-dir", default=str(ROOT / "results" / "par"))
    ap.add_argument("--out", help="also save the best line here")
    args = ap.parse_args(argv)

    ai.load_models()
    ai.set_budget_scale(math.inf)
    cfg, seed, name = settings_of(args)
    out_dir = Path(args.results_dir)
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
    root = search.root
    floors = FloorTable()
    root_floor = ps.floor(root, me)
    print(f"{name}: {len(root.systems)} systems, floor turn {root_floor:.0f}, "
          f"levels {args.levels}{' +splits' if args.splits else ''}, {args.workers} worker(s)", flush=True)
    best = search.run()
    bound = min((best.turn, best.lost), resume_bound(ledger))
    if args.assume_turn is not None:
        assumed = (args.assume_turn, math.inf if args.assume_lost is None else args.assume_lost)
        bound = min(bound, assumed)
    print(f"incumbent: turn {bound[0]}, lost {bound[1]} (beam {best.turn}/{best.lost})", flush=True)
    if best.turn < math.inf:
        record({"event": "best", "turn": best.turn, "lost": best.lost, "source": "beam"})
        log = search.log()
        log.path = best_path
        log.save()

    params = (cfg.to_dict(), seed, me, args.levels, args.max_branch, args.splits, args.ratio, args.max_turns)
    pool = None
    if args.workers > 1:
        pool = multiprocessing.get_context("spawn").Pool(args.workers, init_worker, params)
    else:
        configure(*params)

    exact = True
    incumbent = {"turn": bound[0], "lost": bound[1]}
    limits: list[int | None] = list(range(1, args.max_launches + 1)) if args.max_launches else [None]

    def run_layers(limit: int | None) -> bool:
        """One pass over every board; True when it ran out of boards (not of time)."""
        nonlocal exact
        layer: list[tuple[GameState, tuple]] = [(ps.clone(root), ())]   # (board, path)
        depth = 0
        while layer and time.perf_counter() - started < args.budget:
            depth += 1
            n_chunks = max(1, min(len(layer), args.workers * 4))
            size = min(math.ceil(len(layer) / n_chunks), CHUNK_BOARDS)
            chunks = [layer[i:i + size] for i in range(0, len(layer), size)]
            tasks = [([b for b, _ in chunk], incumbent["turn"], incumbent["lost"], limit) for chunk in chunks]
            results = waves(pool, tasks, args.workers) if pool else map(expand_chunk, tasks)
            nxt: dict[tuple, tuple[int, GameState, tuple]] = {}
            expanded = 0
            best_win: tuple | None = None
            for chunk, (children, wins, ok, n) in zip(chunks, results, strict=True):
                exact &= ok
                expanded += n
                for turn, lost, pos, packed in wins:
                    if best_win is None or (turn, lost) < best_win[:2]:
                        best_win = (turn, lost, (chunk[pos][1], packed))
                for key, lost, board, pos, packed in children:
                    board.lanes = root.lanes   # one shared copy, as `ps.clone` leaves it
                    old = nxt.get(key)
                    if old is None or lost < old[0]:
                        nxt[key] = (lost, board, (chunk[pos][1], packed))
                if len(nxt) > 2 * args.max_frontier:   # never hold a whole oversized layer
                    exact = False
                    nxt = trim(nxt, args.max_frontier, me, int(min(incumbent["turn"], args.max_turns)),
                               floors, args.rank)
            if best_win and (best_win[0], best_win[1]) < (incumbent["turn"], incumbent["lost"]):
                incumbent.update(turn=best_win[0], lost=best_win[1])
                record({"event": "best", "turn": best_win[0], "lost": best_win[1],
                        "source": "exhaustive", "launches": limit})
                log = line_log(root, cfg, seed, search.strategies, me, best_win[2])
                log.path = best_path
                log.save()
                print(f"  best -> turn {best_win[0]}, lost {best_win[1]}", flush=True)
            horizon = int(min(incumbent["turn"], args.max_turns))
            if len(nxt) > args.max_frontier:
                exact = False
                nxt = trim(nxt, args.max_frontier, me, horizon, floors, args.rank)
            layer = [(b, p) for _, b, p in nxt.values()]
            record({"event": "layer", "depth": depth, "expanded": expanded, "frontier": len(layer),
                    "best": [incumbent["turn"], incumbent["lost"]], "exact": exact, "launches": limit})
            print(f"layer {depth}: {expanded} expanded, {len(layer)} kept, best "
                  f"{incumbent['turn']}/{incumbent['lost']}, {time.perf_counter() - started:.0f}s", flush=True)
        return not layer

    finished = True
    try:
        for limit in limits:
            if limit is not None:
                print(f"plans of at most {limit} launch order(s):", flush=True)
            finished &= run_layers(limit)
    finally:
        if pool:
            pool.terminate()

    exact &= finished
    model = f"plans of at most {args.max_launches} launch orders" if args.max_launches else "the order model"
    verdict = f"optimal within {model}" if exact else "best found (search not exact)"
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
