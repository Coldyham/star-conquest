"""How far apart two bots play: `tools/bot_distance.py`.

The arithmetic (distance and kappa), which orders count, which positions are
contested, and that a deterministic bot asked twice is at distance 0 from itself,
which is the floor every other reading is quoted against.
"""

from __future__ import annotations

from collections import Counter

import pytest

from starconquest import ai, config, mapgen
from starconquest.model import GameState, Order, Player, System
from tools import bot_distance as bd


def _board(systems, lanes, seats=2):
    state = GameState.new(1)
    for sid, (owner, ships) in systems.items():
        state.systems[sid] = System(id=sid, pos=(float(sid) * 10.0, 0.0),
                                    owner_id=owner, ships=ships, production=3)
    for a, b, turns in lanes:
        state.add_lane(a, b, turns * config.SHIP_LY_PER_TURN, turns)
    state.rebuild_topology()
    state.players = {0: Player(0, "Neutral", (90, 90, 90), is_neutral=True)}
    for pid in range(1, seats + 1):
        state.players[pid] = Player(pid, f"P{pid}", (200, 60, 60))
    return state


@pytest.fixture
def scripted():
    """Register a bot whose orders a test sets, and unregister it after."""
    ai.load_models()
    script: list[Order] = []
    ai.register("scripted_test_bot", lambda state, pid: list(script))
    yield script
    ai.STRATEGIES.pop("scripted_test_bot", None)


def _answer(moves):
    return bd.Answer(moves, {}, {}, 0, 0, 0.0)


def test_distance_runs_from_identical_to_disjoint():
    held = _answer({(1, 1): 10})
    assert bd.distance(held, _answer({(1, 1): 10})) == 0
    assert bd.distance(held, _answer({(1, 2): 10})) == 1
    assert bd.distance(held, _answer({(1, 1): 5, (1, 2): 5})) == 0.5


def test_kappa_is_one_for_agreement_and_zero_for_chance():
    assert bd.kappa(Counter({("hold", "hold"): 3, ("rival", "rival"): 3})) == 1
    assert bd.kappa(Counter({("hold", "hold"): 1, ("hold", "rival"): 1,
                             ("rival", "hold"): 1, ("rival", "rival"): 1})) == 0


def test_only_orders_the_engine_would_launch_count(scripted):
    """Foreign sources, missing lanes and overspends are what `apply_order`
    drops or clips, and a bot is measured on what it really does."""
    state = _board({1: (1, 10), 2: (0, 3), 3: (2, 5)}, [(1, 2, 2), (2, 3, 2)])
    scripted += [Order(1, 1, 2, 15),                        # clipped to the 10 there are
                 Order(1, 1, 2, 1),                         # nothing left to send
                 Order(1, 3, 2, 5),                         # not our system
                 Order(1, 1, 3, 1)]                         # no lane
    ans = bd.answer(bd.Position(state, 1, bd.OPENING, "t"), "scripted_test_bot", 1.0)
    assert ans.moves == {(1, 2): 10}
    assert ans.actions == {1: bd.NEUTRAL}
    assert ans.orders == 1 and ans.emptied == 1


def test_holding_is_a_move(scripted):
    state = _board({1: (1, 10), 2: (0, 3)}, [(1, 2, 2)])
    ans = bd.answer(bd.Position(state, 1, bd.OPENING, "t"), "scripted_test_bot", 1.0)
    assert ans.moves == {(1, 1): 10} and ans.actions == {1: bd.HOLD}


def test_contact_with_a_live_rival_is_contested():
    state = _board({1: (1, 10), 2: (0, 3), 3: (2, 5)}, [(1, 2, 2), (2, 3, 2)])
    assert bd.phase_of(state, 1) == bd.OPENING
    state.add_lane(1, 3, 2 * config.SHIP_LY_PER_TURN, 2)
    state.rebuild_topology()
    assert bd.phase_of(state, 1) == bd.CONTESTED
    state.players[2].alive = False
    assert bd.phase_of(state, 1) == bd.OPENING


def test_every_bot_takes_every_seat():
    seats = {(i, bot) for lineup in bd.lineups(["a", "b", "c"], 2, 6)
             for i, bot in enumerate(lineup)}
    assert seats == {(i, b) for i in range(2) for b in "abc"}


def test_a_deterministic_bot_is_at_distance_zero_from_itself():
    ai.load_models()
    tallies = {phase: bd.Tally() for phase in bd.PHASES}
    for _, found in bd.selfplay_positions(["claudebot", "thinker"], [3], "random", 12, 2,
                                          every=10, max_turns=60, aux_for=lambda b: 1.0):
        bd.measure(found, ["claudebot", "heuristic"], lambda b: 1.0, tallies, True, None)
    floors = [d for t in tallies.values() for v in t.null.values() for d in v]
    assert floors and max(floors) == 0
