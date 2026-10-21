"""knower at Search offered bait openings (`tools/bait_bench.py`).

The bench reads the bait arm against stock on the same seat and seed, so it
rests on the stock arm's search being knower's own, and on a bait opening being
the default plan with only the wall's leftover garrison moved, and moved
somewhere friendly that is not a wall too.
"""

from __future__ import annotations

import sys

import pytest

from starconquest import ai
from starconquest.model import Order
from starconquest.settings import Settings, build_state
from tests.test_replay import _preserve_config
from tools import bait_bench as bb


@pytest.fixture
def knower(monkeypatch):
    if "knower" not in ai.STRATEGIES:
        ai.load_models()
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    for name in ("_candidates", "_search"):
        monkeypatch.setattr(k, name, getattr(k, name))
    for name in ("_patched", "_bait", "_fires"):
        monkeypatch.setattr(bb, name, getattr(bb, name))
    with _preserve_config():
        yield k


def _wall_board():
    """Four seats; one of pid 1's systems made a wall between pids 2 and 3, with
    one other neighbour of pid 1's own."""
    spec = {key: v for key, v in bb.SETUPS["p4"].items() if key != "key"}
    state = build_state(Settings.from_dict(spec), bb.SEED_BASE)
    state.fleets.clear()
    wall, a, b, c = next(
        (s, a, b, c) for s in state.systems.values() if s.owner_id == 0
        for c in s.neighbors for a in s.neighbors for b in s.neighbors
        if len({a, b, c}) == 3 and a < b
        and not {a, b} & set(state.systems[c].neighbors))
    for n in wall.neighbors + state.systems[c].neighbors:
        state.systems[n].owner_id = 0
    wall.owner_id, wall.ships = 1, 9
    state.systems[c].owner_id, state.systems[c].ships = 1, 2
    state.systems[a].owner_id, state.systems[a].ships = 2, 5
    state.systems[b].owner_id, state.systems[b].ships = 3, 7
    return state, wall, c


def test_a_bait_moves_only_the_leftover_garrison_to_a_friendly_non_wall(knower):
    state, wall, home = _wall_board()
    assert wall.id in bb.walls(state, 1)
    assert home not in bb.walls(state, 1)
    base = [Order(1, wall.id, home, 3)]
    plan = bb.bait_plan(state, 1, base, wall.id, frozenset(bb.walls(state, 1)))
    assert plan == [Order(1, wall.id, home, 9)]           # merged, the whole 9 gone
    assert bb.bait_plan(state, 1, [Order(1, wall.id, home, 9)], wall.id,
                        frozenset({wall.id})) is None      # nothing left to move
    assert bb.bait_plan(state, 1, [], wall.id, frozenset({wall.id, home})) is None


def test_the_stock_arm_searches_as_knower_does(knower):
    state, wall, _home = _wall_board()
    stock_k = list(knower._candidates(state, 1, None, []))
    bb._patch()
    bb._bait = frozenset()
    bb._fires = {"searches": 0, "offered": 0, "lines": 0, "picked": 0, "errors": 0}
    assert list(knower._candidates(state, 1, None, [])) == stock_k
    bb._bait = frozenset({1})
    got = list(knower._candidates(state, 1, None, []))
    assert got[:len(stock_k)] == stock_k
    assert got[len(stock_k):] == [[Order(1, wall.id, _home, 9)]]
    assert bb._fires["offered"] == 1 and bb._fires["errors"] == 0
