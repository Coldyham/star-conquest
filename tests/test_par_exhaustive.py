"""`tools/par_exhaustive.py`: its speedups must not change what the search sees."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from starconquest import ai, engine, replay
from starconquest.model import Order
from starconquest.settings import Settings, build_state
from tools import par_exhaustive as pe
from tools import par_search as ps

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _models():
    ai.load_models()
    ai.set_budget_scale(math.inf)


def _setup(nodes: int = 5, seed: int = 7) -> Settings:
    cfg = Settings(mode="random", players=2, nodes=nodes, seed=seed)
    cfg.ai_strategy[1] = "rusherplus"
    return cfg


def _random_orders(board, me, rng):
    orders = []
    for s in board.systems.values():
        if s.owner_id == me and s.ships > 0 and s.neighbors and rng.random() < 0.7:
            orders.append(Order(me, s.id, rng.choice(s.neighbors), rng.randint(1, s.ships)))
    return orders


def test_floor_table_equals_the_floor():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    table, rng = pe.FloorTable(), random.Random(1)
    for _ in range(12):
        for horizon in (15, 60):
            assert table.floor(board, me, horizon) == ps.floor(board, me, horizon)
        if board.winner is not None:
            break
        ps.step(board, _random_orders(board, me, rng))


def test_fast_clone_steps_like_clone():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    scratch, rng = random.Random(), random.Random(2)
    for _ in range(12):
        if board.winner is not None:
            break
        orders = _random_orders(board, me, rng)
        a, b = ps.clone(board), pe.fast_clone(board, scratch)
        ps.step(a, orders)
        ps.step(b, orders)
        assert ps.board_key(a) == ps.board_key(b)
        assert a.players[me].ships_lost == b.players[me].ships_lost
        assert a.winner == b.winner
        board = a


def test_shared_rival_decide_matches_deciding_per_child():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    rng = random.Random(3)
    for _ in range(10):
        if board.winner is not None:
            break
        shared = pe._shared_decide()
        for _ in range(4):
            orders = _random_orders(board, me, rng)
            fresh, cached = ps.clone(board), ps.clone(board)
            ps.step(fresh, orders)
            replay.reseed(cached)
            engine.end_turn(cached, human_orders=orders, decide=shared)
            assert ps.board_key(fresh) == ps.board_key(cached)
        board = fresh


def test_splits_add_two_lane_sends_and_nothing_else():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    plain, _ = pe.order_sets(board, me, "quarters", 10**9)
    split, ok = pe.order_sets(board, me, "quarters", 10**9, splits=True)
    plain = {tuple((o.source_id, o.dest_id, o.ships) for o in s) for s in plain}
    split = {tuple((o.source_id, o.dest_id, o.ships) for o in s) for s in split}
    assert ok and plain < split
    home = next(s for s in board.systems.values() if s.owner_id == me)
    for orders in split - plain:
        assert sum(n for s, _, n in orders if s == home.id) <= home.ships
        lanes = [d for s, d, _ in orders if s == home.id]
        assert len(lanes) == len(set(lanes))   # never two orders down one lane


def test_a_too_large_board_falls_back_and_says_so():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    _, ok = pe.order_sets(board, me, "all", 1, splits=True)
    assert not ok


def _run(tmp_path, workers: int) -> list[dict]:
    day = tmp_path / "day.json"
    day.write_text(json.dumps({"day": "t", "kind": "k", "settings": _setup(4, 3).to_dict()}))
    out = tmp_path / f"w{workers}"
    pe.main(["--daily", str(day), "--levels", "coarse", "--seed-budget", "1", "--budget", "60",
             "--max-turns", "11", "--workers", str(workers), "--results-dir", str(out)])
    rows = [json.loads(line) for line in (out / "t_k.jsonl").read_text().splitlines()]
    return [r for r in rows if r["event"] in ("layer", "done")]


def test_worker_count_does_not_change_the_search(tmp_path):
    one, two = _run(tmp_path, 1), _run(tmp_path, 2)
    assert one == two and one[-1]["event"] == "done" and sum(r["event"] == "layer" for r in one) >= 3


def test_ratio_rank_keeps_the_stronger_board_and_floor_rank_the_earlier_one():
    cfg = _setup()
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    floors = pe.FloorTable()
    stronger = ps.clone(board)
    next(s for s in stronger.systems.values() if s.owner_id == me).ships += 8
    nxt = {1: (0, board, ()), 2: (0, stronger, ())}
    assert list(pe.trim(nxt, 1, me, 10, floors, "ratio")) == [2]
    assert pe.rank_key(stronger, me, 10, floors, "ratio") < pe.rank_key(board, me, 10, floors, "ratio")
    assert set(pe.trim(nxt, 5, me, 10, floors, "floor")) == {1, 2}   # under the cap: nothing dropped


def _puzzle_board_after(turns: int):
    """2026-11-04's puzzle on turn ``turns`` of the known line: home sends 6 on turn 2, 5 more on turn 6."""
    data = json.loads((ROOT / "tools" / "par_inputs" / "2026-11-04_puzzle.json").read_text())
    cfg = Settings.from_dict(data["settings"])
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    plan = {2: [Order(me, 0, 4, 6)], 6: [Order(me, 0, 4, 5), Order(me, 4, 3, 3)]}
    for t in range(turns):
        ps.step(board, plan.get(t, []))
    return board, me


def _as_sets(sets) -> set[tuple]:
    return {tuple(sorted((o.source_id, o.dest_id, o.ships) for o in s)) for s in sets}


def test_launch_budget_keeps_exactly_the_small_order_sets():
    board, me = _puzzle_board_after(6)   # I hold home and Dust-Ball, both with ships
    assert sum(1 for s in board.systems.values() if s.owner_id == me and s.ships > 0) == 2
    everything, _ = pe.order_sets(board, me, "quarters", 10**9, splits=True)
    everything = _as_sets(everything)
    for limit in (0, 1, 2, 3):
        limited, ok = pe.order_sets(board, me, "quarters", 10**9, splits=True, limit=limit)
        assert ok and _as_sets(limited) == {s for s in everything if len(s) <= limit}
    assert () in _as_sets(pe.order_sets(board, me, "all", 10, limit=0)[0])   # holding is always allowed


def test_three_launches_reach_ten_turns_on_the_daily_puzzle(tmp_path):
    """The line a person found, reached by counting launches instead of turns (~25 s)."""
    out = tmp_path / "res"
    pe.main(["--daily", str(ROOT / "tools" / "par_inputs" / "2026-11-04_puzzle.json"), "--levels", "all",
             "--max-launches", "3", "--assume-turn", "11", "--seed-budget", "1", "--budget", "280",
             "--workers", "1", "--results-dir", str(out)])
    rows = [json.loads(line) for line in (out / "2026-11-04_puzzle.jsonl").read_text().splitlines()]
    done = next(r for r in rows if r["event"] == "done")
    assert (done["turn"], done["lost"], done["exact"]) == (10, 3, True)
