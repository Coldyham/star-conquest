"""reader's memory and model (models/reader.py).

The module has no ``decide`` yet, so it is reached through ``sys.modules`` under
the name ``ai.load_models`` imports each model file under.
"""

from __future__ import annotations

import copy
import random
import sys

import pytest

from starconquest import ai, config, engine, mapgen
from starconquest.model import GameState, Order, Player, System
from tests import sim


@pytest.fixture(scope="module")
def rd():
    ai.load_models()
    assert "sc_model_reader" in sys.modules, "models/reader.py failed to import"
    return sys.modules["sc_model_reader"]


@pytest.fixture(autouse=True)
def _fresh(rd):
    speed, cap = config.SHIP_LY_PER_TURN, rd.NODE_CAP
    rd.reset()
    yield
    rd.reset()
    config.SHIP_LY_PER_TURN, rd.NODE_CAP = speed, cap


def _game(seed=3, nodes=18, bots=("marshal", "rusherplus", "thinker")):
    state = mapgen.generate(seed, "random", nodes, len(bots))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(bots))
    return state


def _play(rd, state, turns):
    """Advance `turns` turns, letting reader see every board on the way."""
    for _ in range(turns):
        rd.models_for(state)
        engine.end_turn(state, decide=ai.decide)
    return rd.models_for(state)


def _board(systems, lanes):
    """A hand-built two-seat board. ``systems`` is {sid: (owner, ships, production)}."""
    state = GameState.new(1)
    for sid, (owner, ships, production) in systems.items():
        state.systems[sid] = System(id=sid, pos=(float(sid) * 10.0, 0.0),
                                    owner_id=owner, ships=ships, production=production)
    for a, b, turns in lanes:
        state.add_lane(a, b, turns * config.SHIP_LY_PER_TURN, turns)
    state.rebuild_topology()
    state.players = {0: Player(0, "Neutral", (90, 90, 90), is_neutral=True),
                     1: Player(1, "P1", (200, 60, 60)), 2: Player(2, "P2", (60, 60, 200))}
    return state


def _scripted(orders):
    return lambda state, pid: [o for o in orders if o.owner_id == pid]


def test_reader_registers_no_strategy(rd):
    assert "reader" not in ai.STRATEGIES


# --------------------------------------------------------------------------- #
# The memo tree
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("speed", [6.0, 3.0, 18.0])
def test_a_live_game_never_starts_cold(rd, speed):
    """Every board after the first finds the one before it, including where
    one-turn lanes carry launches nobody sees."""
    config.SHIP_LY_PER_TURN = speed
    state = _game()
    while state.winner is None and state.turn < 80:
        node = rd._node_for(state)
        assert state.turn == 0 or node.parent is not None, f"cold at turn {state.turn}"
        engine.end_turn(state, decide=ai.decide)


def test_the_same_board_finds_the_same_node_and_answers_alike(rd):
    state = _game()
    _play(rd, state, 30)
    node = rd._node_for(state)
    assert rd._node_for(copy.deepcopy(state)) is node
    answers = [rd.predict(state, pid) for pid in (1, 2, 3) for _ in range(3)]
    assert answers[0:3] == [answers[0]] * 3
    assert rd.predict(copy.deepcopy(state), 1) == answers[0]


def test_two_branches_from_one_board_keep_their_own_models(rd):
    state = _game()
    _play(rd, state, 25)
    parent = rd._node_for(state)
    played, held = copy.deepcopy(state), copy.deepcopy(state)
    engine.end_turn(played, decide=ai.decide)
    engine.end_turn(held, decide=lambda s, pid: [])
    a, b = rd._node_for(played), rd._node_for(held)
    assert a is not b
    assert a.parent == b.parent == (rd._game_key(state), parent.snap.key)
    assert a.models != b.models


def test_a_rewound_board_finds_its_original_node(rd):
    state = _game()
    _play(rd, state, 10)
    saved = copy.deepcopy(state)
    node = rd._node_for(state)
    _play(rd, state, 10)
    assert rd._node_for(saved) is node


def test_another_game_on_the_same_seed_starts_cold(rd):
    state = _game()
    _play(rd, state, 6)
    other = _game(bots=("thinker", "marshal", "claudebot"))
    for _ in range(7):
        engine.end_turn(other, decide=ai.decide)
    assert rd._node_for(other).parent is None


def test_a_skipped_turn_starts_cold(rd):
    state = _game()
    _play(rd, state, 6)
    engine.end_turn(state, decide=ai.decide)
    engine.end_turn(state, decide=ai.decide)
    node = rd._node_for(state)
    assert node.parent is None and node.models == {}


def test_eviction_is_a_cold_start_and_nothing_else(rd):
    rd.NODE_CAP = 3
    state = _game()
    boards = []
    for _ in range(6):
        boards.append(copy.deepcopy(state))
        rd.models_for(state)
        engine.end_turn(state, decide=ai.decide)
    assert len(rd._NODES) == 3
    assert sum(len(keys) for keys in rd._AT.values()) == 3
    early = rd._node_for(boards[1])
    assert early.parent is None and early.models == {}
    rd.reset()
    assert rd.predict(boards[1], 1) == rd.predict(boards[1], 1, {})


# --------------------------------------------------------------------------- #
# What one turn shows
# --------------------------------------------------------------------------- #
def test_observe_counts_every_strike_a_turn_made(rd):
    """Each (source, non-own target) pair that got ships is one strike, all-in or
    sized, and every other non-own neighbour of a garrisoned system is one pass."""
    state = _game()
    _play(rd, state, 20)
    before = copy.deepcopy(state)
    issued: dict[int, list[Order]] = {}

    def recording(st, pid):
        issued[pid] = ai.decide(st, pid) or []
        return issued[pid]

    engine.end_turn(state, decide=recording)
    seen = rd.observe(rd._Snap(before), rd._Snap(state), state)
    for pid, orders in issued.items():
        struck = {(o.source_id, o.dest_id) for o in orders
                  if before.systems[o.dest_id].owner_id != pid and o.ships > 0}
        pairs = sum(1 for s in before.systems.values() if s.owner_id == pid and s.ships > 0
                    for n in s.neighbors if before.systems[n].owner_id != pid)
        model = seen.get(pid, rd.EMPTY)
        assert sum(model.strikes) == sum(model.allin) == len(struck)
        assert sum(model.size) == model.allin[1]
        assert sum(model.strikes) + sum(model.passes) == pairs


def test_a_one_turn_launch_is_recovered_from_the_garrison(rd):
    """A one-turn fleet lands inside the turn it left, so it is never on the
    board. The ships it took are still missing from its source."""
    state = _board({1: (1, 10, 0), 2: (0, 3, 0), 3: (2, 5, 0)},
                   [(1, 2, 1), (1, 3, 3)])
    before = copy.deepcopy(state)
    engine.end_turn(state, decide=_scripted([Order(1, 1, 2, 8)]))
    assert not state.fleets
    model = rd.observe(rd._Snap(before), rd._Snap(state), state)[1]
    neutral = rd.NEUTRAL * 2 * rd.RATIO_BINS
    assert model.strikes[neutral + rd._ratio_bin(10 / 3)] == 1 and sum(model.strikes) == 1
    assert sum(model.passes) == 1             # the rival next door, not struck
    assert model.allin == (0.0, 1.0)          # 8 of 10 is sized, not all-in
    assert model.size[rd._ratio_bin(8 / 3)] == 1


def test_the_production_mirror_matches_the_engine(rd):
    state = _game()
    _play(rd, state, 15)
    state.fleets = []
    snap = rd._Snap(state)
    for turns in range(1, 6):
        after = copy.deepcopy(state)
        for _ in range(turns):
            engine.end_turn(after, decide=lambda s, pid: [])
        for sid, s in state.systems.items():
            assert after.systems[sid].ships - s.ships == rd._hulls(state, snap, sid, turns), \
                (sid, turns)


# --------------------------------------------------------------------------- #
# Reading a model
# --------------------------------------------------------------------------- #
def test_the_strike_curve_never_falls(rd):
    assert rd._monotone([0.1, 0.5, 0.2, 0.6], [1, 1, 1, 1]) == [0.1, 0.35, 0.35, 0.6]
    state = _game()
    models = _play(rd, state, 60)
    for model in [rd.EMPTY, *models.values()]:
        for kind in (rd.NEUTRAL, rd.PLAYER):
            for pressed in (0, 1):
                curve = rd.strike_curve(model, kind, pressed)
                assert all(a <= b + 1e-12 for a, b in zip(curve, curve[1:]))
                assert all(0.0 <= p <= 1.0 for p in curve)


def test_a_source_strikes_once_at_most(rd):
    state = _game()
    _play(rd, state, 40)
    for pid in (1, 2, 3):
        per_source: dict[int, float] = {}
        for t in rd.predict(state, pid):
            assert state.systems[t.target].owner_id == pid
            assert state.systems[t.source].owner_id == t.rival != pid
            per_source[t.source] = per_source.get(t.source, 0.0) + t.p
        assert all(total <= 1.0 + 1e-9 for total in per_source.values())
        assert all(0.0 <= t.ships <= state.systems[t.source].ships for t in rd.predict(state, pid))


def test_predict_leaves_the_state_alone_and_draws_nothing(rd, monkeypatch):
    state = _game()
    _play(rd, state, 30)
    engine.end_turn(state, decide=ai.decide)

    def board(s):
        return (tuple((x.id, x.owner_id, x.ships, x.prod_progress) for x in s.systems.values()),
                tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                      for f in s.fleets), s.turn, s.winner)

    def drew(*_args, **_kwargs):
        raise AssertionError("reader drew from the random module")

    for name in ("random", "uniform", "randint", "randrange", "choice", "shuffle", "sample"):
        monkeypatch.setattr(random, name, drew)
    before, rng = board(state), state.rng.getstate()
    for pid in (1, 2, 3):
        rd.predict(state, pid)
        rd.predict(state, pid, {})
    assert board(state) == before
    assert state.rng.getstate() == rng
