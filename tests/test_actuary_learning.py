"""actuary's Learning stop (Style 2): its memory of each rival, the strike curve it
reads, and how the ledger prices a risk with it (models/actuary.py). The readings
only the tool keeps (strike sizes, guard, evacuation, `predict`) are covered here
too, from tools/learner_check.py, since they ride on the same memo.

Internals are reached through ``sys.modules["sc_model_actuary"]``, the name
``ai.load_models`` imports the model file under.
"""

from __future__ import annotations

import copy
import random
import sys

import pytest

from starconquest import ai, config, engine, mapgen
from starconquest.model import AiParams, GameState, Order, Player, System
from tests import sim
from tools import learner_check


@pytest.fixture(scope="module")
def ac():
    assert "actuary" in ai.load_models(), "models/actuary.py failed to import"
    return sys.modules["sc_model_actuary"]


@pytest.fixture(autouse=True)
def _fresh(ac):
    speed, cap, reach = config.SHIP_LY_PER_TURN, ac.NODE_CAP, ac.LEARN_REACH
    ac.reset()
    yield
    ac.reset()
    config.SHIP_LY_PER_TURN, ac.NODE_CAP, ac.LEARN_REACH = speed, cap, reach


def _game(seed=3, nodes=18, bots=("marshal", "rusherplus", "thinker")):
    state = mapgen.generate(seed, "random", nodes, len(bots))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(bots))
    return state


def _play(ac, state, turns):
    """Advance `turns` turns, letting the memo see every board on the way."""
    for _ in range(turns):
        ac.models_for(state)
        engine.end_turn(state, decide=ai.decide)
    return ac.models_for(state)


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


# --------------------------------------------------------------------------- #
# The memo tree
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("speed", [6.0, 3.0, 18.0])
def test_a_live_game_never_starts_cold(ac, speed):
    """Every board after the first finds the one before it, including where
    one-turn lanes carry launches nobody sees."""
    config.SHIP_LY_PER_TURN = speed
    state = _game()
    while state.winner is None and state.turn < 80:
        node = ac._node_for(state)
        assert state.turn == 0 or node.parent is not None, f"cold at turn {state.turn}"
        engine.end_turn(state, decide=ai.decide)


def test_the_same_board_finds_the_same_node_and_answers_alike(ac):
    state = _game()
    _play(ac, state, 30)
    node = ac._node_for(state)
    assert ac._node_for(copy.deepcopy(state)) is node
    assert ac._curves(copy.deepcopy(state)) == ac._curves(state)


def test_two_branches_from_one_board_keep_their_own_models(ac):
    state = _game()
    _play(ac, state, 25)
    parent = ac._node_for(state)
    played, held = copy.deepcopy(state), copy.deepcopy(state)
    src, dst = next((s.id, n) for s in sorted(state.systems.values(), key=lambda s: s.id)
                    if s.owner_id == 1 and s.ships > 0
                    for n in sorted(s.neighbors) if state.systems[n].owner_id != 1)
    engine.end_turn(played, decide=_scripted([Order(1, src, dst, 1)]))
    engine.end_turn(held, decide=lambda s, pid: [])
    a, b = ac._node_for(played), ac._node_for(held)
    assert a is not b
    assert a.parent == b.parent == (ac._game_key(state), parent.snap.key)
    def counts(node):
        return {q: (m.strikes, m.passes) for q, m in node.models.items()}

    assert counts(a) != counts(b)


def test_a_rewound_board_finds_its_original_node(ac):
    state = _game()
    _play(ac, state, 10)
    saved = copy.deepcopy(state)
    node = ac._node_for(state)
    _play(ac, state, 10)
    assert ac._node_for(saved) is node


def test_another_game_on_the_same_seed_starts_cold(ac):
    state = _game()
    _play(ac, state, 6)
    other = _game(bots=("thinker", "marshal", "claudebot"))
    for _ in range(7):
        engine.end_turn(other, decide=ai.decide)
    assert ac._node_for(other).parent is None


def test_a_skipped_turn_starts_cold(ac):
    state = _game()
    _play(ac, state, 6)
    engine.end_turn(state, decide=ai.decide)
    engine.end_turn(state, decide=ai.decide)
    node = ac._node_for(state)
    assert node.parent is None and node.models == {}


def test_eviction_is_a_cold_start_and_nothing_else(ac):
    ac.NODE_CAP = 3
    state = _game()
    boards = []
    for _ in range(6):
        boards.append(copy.deepcopy(state))
        ac.models_for(state)
        engine.end_turn(state, decide=ai.decide)
    assert len(ac._NODES) == 3
    assert sum(len(keys) for keys in ac._AT.values()) == 3
    early = ac._node_for(boards[1])
    assert early.parent is None and early.models == {}
    ac.reset()
    prior = ac.strike_curve(ac.EMPTY, ac.PLAYER, 0)
    assert all(curve == prior for curve in ac._curves(boards[1]).values())


# --------------------------------------------------------------------------- #
# What one turn shows
# --------------------------------------------------------------------------- #
def test_observe_counts_every_strike_a_turn_made(ac):
    """Each (source, non-own target) pair that got ships is one strike, and every
    other non-own neighbour of a garrisoned system is one pass. The tool's habits
    count each strike once more, all-in or sized."""
    state = _game()
    _play(ac, state, 20)
    before = copy.deepcopy(state)
    issued: dict[int, list[Order]] = {}

    def recording(st, pid):
        issued[pid] = ai.decide(st, pid) or []
        return issued[pid]

    engine.end_turn(state, decide=recording)
    prev, cur = ac._Snap(before), ac._Snap(state)
    seen = ac.observe(prev, cur, state)
    habits = learner_check._observe_habits(prev, cur, state)
    for pid, orders in issued.items():
        struck = {(o.source_id, o.dest_id) for o in orders
                  if before.systems[o.dest_id].owner_id != pid and o.ships > 0}
        pairs = sum(1 for s in before.systems.values() if s.owner_id == pid and s.ships > 0
                    for n in s.neighbors if before.systems[n].owner_id != pid)
        model = seen.get(pid, ac.EMPTY)
        h = habits.get(pid, learner_check._no_habits())
        assert sum(model.strikes) == sum(h["allin"]) == len(struck)
        assert sum(h["size"]) == h["allin"][1]
        assert sum(model.strikes) + sum(model.passes) == pairs


def test_a_one_turn_launch_is_recovered_from_the_garrison(ac):
    """A one-turn fleet lands inside the turn it left, so it is never on the
    board. The ships it took are still missing from its source."""
    state = _board({1: (1, 10, 0), 2: (0, 3, 0), 3: (2, 5, 0)},
                   [(1, 2, 1), (1, 3, 3)])
    before = copy.deepcopy(state)
    engine.end_turn(state, decide=_scripted([Order(1, 1, 2, 8)]))
    assert not state.fleets
    prev, cur = ac._Snap(before), ac._Snap(state)
    assert ac.launches(prev, cur, state)[1] == ({2: 8}, 0)
    model = ac.observe(prev, cur, state)[1]
    neutral = ac.NEUTRAL * 2 * ac.RATIO_BINS
    assert model.strikes[neutral + ac._ratio_bin(10 / 3)] == 1 and sum(model.strikes) == 1
    assert sum(model.passes) == 1             # the rival next door, not struck
    h = learner_check._observe_habits(prev, cur, state)[1]
    assert h["allin"] == [0.0, 1.0]           # 8 of 10 is sized, not all-in
    assert h["size"][ac._ratio_bin(8 / 3)] == 1


def test_the_production_mirror_matches_the_engine(ac):
    state = _game()
    _play(ac, state, 15)
    state.fleets = []
    snap = ac._Snap(state)
    for turns in range(1, 6):
        after = copy.deepcopy(state)
        for _ in range(turns):
            engine.end_turn(after, decide=lambda s, pid: [])
        for sid, s in state.systems.items():
            assert after.systems[sid].ships - s.ships == ac._hulls(state, snap, sid, turns), \
                (sid, turns)


def test_the_strike_curve_never_falls(ac):
    assert ac._monotone([0.1, 0.5, 0.2, 0.6], [1, 1, 1, 1]) == [0.1, 0.35, 0.35, 0.6]
    state = _game()
    models = _play(ac, state, 60)
    for model in [ac.EMPTY, *models.values()]:
        for kind in (ac.NEUTRAL, ac.PLAYER):
            for pressed in (0, 1):
                curve = ac.strike_curve(model, kind, pressed)
                assert all(a <= b + 1e-12 for a, b in zip(curve, curve[1:]))
                assert all(0.0 <= p <= 1.0 for p in curve)


# --------------------------------------------------------------------------- #
# The tool's readings (tools/learner_check.py)
# --------------------------------------------------------------------------- #
def _watched(ac, state, turns):
    watcher = learner_check.Watcher()
    for _ in range(turns):
        watcher.see(state)
        engine.end_turn(state, decide=ai.decide)
    watcher.see(state)
    return watcher


def test_a_source_strikes_once_at_most(ac):
    state = _game()
    watcher = _watched(ac, state, 40)
    models = watcher.models(state)
    assert any(sum(m.allin) for m in models.values()), "the watcher counted no strikes"
    for pid in (1, 2, 3):
        per_source: dict[int, float] = {}
        predicted = learner_check.predict(state, pid, models)
        for q, src, dst, p, ships in predicted:
            assert state.systems[dst].owner_id == pid
            assert state.systems[src].owner_id == q != pid
            assert 0.0 <= ships <= state.systems[src].ships
            per_source[src] = per_source.get(src, 0.0) + p
        assert all(total <= 1.0 + 1e-9 for total in per_source.values())


def test_predict_leaves_the_state_alone_and_draws_nothing(ac, monkeypatch):
    state = _game()
    watcher = _watched(ac, state, 30)

    def board(s):
        return (tuple((x.id, x.owner_id, x.ships, x.prod_progress) for x in s.systems.values()),
                tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                      for f in s.fleets), s.turn, s.winner)

    def drew(*_args, **_kwargs):
        raise AssertionError("the model drew from the random module")

    for name in ("random", "uniform", "randint", "randrange", "choice", "shuffle", "sample"):
        monkeypatch.setattr(random, name, drew)
    before, rng = board(state), state.rng.getstate()
    for pid in (1, 2, 3):
        learner_check.predict(state, pid, watcher.models(state))
        learner_check.predict(state, pid, {})
    assert board(state) == before
    assert state.rng.getstate() == rng


# --------------------------------------------------------------------------- #
# The stop
# --------------------------------------------------------------------------- #
def _orders(fn, state, pid):
    return [(o.source_id, o.dest_id, o.ships) for o in fn(state, pid)]


def _seat(state, pid, aux):
    state.players[pid].ai_strategy = "actuary"
    state.players[pid].ai_params.aux = aux
    return state


def test_the_style_knob_has_three_stops(ac):
    assert ai.aux_spec("actuary") == ("Style", 0.0, 2.0, 1.0, True)
    assert ai.aux_names("actuary") == ("Greedy", "Planned", "Learning")
    for aux, stop in ((0.0, ac.GREEDY), (0.5, ac.GREEDY), (1.0, ac.PLANNED), (1.9, ac.PLANNED),
                      (2.0, ac.LEARNING), (9.0, ac.LEARNING), ("junk", ac.PLANNED)):
        seat = Player(1, "P1", (0, 0, 0), ai_params=AiParams(aux=aux))
        assert ac._stop_of(seat) == stop, aux
        assert ac.is_oracle_seat(seat) == (stop == ac.LEARNING)
    assert not getattr(ac, "IS_ORACLE", False), "only a Learning seat is an oracle's"


def test_learning_counting_every_garrison_in_full_plays_as_planned(ac):
    """With every learned chance read as certain, the risk is actuary's own, so
    Learning plays exactly as Planned; only the reach in the risk differs."""
    ac.LEARN_REACH = 1e9
    for nodes in (12, 18):
        state = _seat(_game(nodes=nodes), 1, float(ac.LEARNING))
        for _ in range(60):
            if state.winner is not None:
                break
            planned = copy.deepcopy(state)
            planned.players[1].ai_params.aux = float(ac.PLANNED)
            assert _orders(ac.decide, state, 1) == _orders(ac.decide, planned, 1)
            engine.end_turn(state, decide=ai.decide)


def test_a_learned_rival_thins_the_risk_and_a_thin_garrison_brings_it_back(ac):
    """Against a rival that never strikes, Learning prices no risk where Planned
    prices some. Against one that strikes only from some ratio up, a garrison
    above that ratio is safe, and the same garrison thinned below it is not."""
    rival = 9
    state = _board({1: (1, 4, 0), 2: (2, rival, 0)}, [(1, 2, 2)])
    assert ac._Ledger(state, 1).risk[1] > 0.0
    assert ac._Ledger(state, 1, {2: [0.0] * ac.RATIO_BINS}).risk[1] == 0.0

    def ratio_bin(garrison):
        return ac._ratio_bin(rival / (garrison * config.DEFENDER_ADVANTAGE))

    assert ratio_bin(2) > ratio_bin(4)
    from_thin = [0.0] * ratio_bin(2) + [1.0] * (ac.RATIO_BINS - ratio_bin(2))
    assert ac._Ledger(state, 1, {2: from_thin}).risk[1] == 0.0
    state.systems[1].ships = 2
    assert ac._Ledger(state, 1, {2: from_thin}).risk[1] > 0.0


def test_learning_prices_a_whole_strike_at_its_chance(ac):
    """A garrison next door strikes whole or not at all: the risk is the chance
    times the shortfall against all of it. A 9-ship garrison at a quarter is
    not a 2-ship strike, which 4 ships would hold off."""
    state = _board({1: (1, 4, 0), 2: (2, 9, 0)}, [(1, 2, 2)])
    full = ac._Ledger(state, 1).risk[1]
    chance = 0.25
    curve = [chance / ac.LEARN_REACH] * ac.RATIO_BINS
    assert full > 0.0
    assert ac._Ledger(state, 1, {2: curve}).risk[1] == pytest.approx(chance * full)


def test_learning_takes_each_source_whole_and_independent(ac):
    """Two garrisons, either of which alone is held off and both together not:
    only the both-strike outcome is short."""
    state = _board({1: (1, 6, 0), 2: (2, 5, 0), 3: (2, 5, 0)}, [(1, 2, 2), (1, 3, 2)])
    both = ac._Ledger(state, 1).risk[1]
    assert both > 0.0
    ledger = ac._Ledger(state, 1, {2: [0.5 / ac.LEARN_REACH] * ac.RATIO_BINS})
    assert ledger.risk[1] == pytest.approx(0.25 * both)


def test_decide_leaves_the_state_alone_and_draws_nothing(ac):
    state = _seat(_game(), 1, float(ac.LEARNING))
    _play(ac, state, 30)
    before = copy.deepcopy(state)
    rng = state.rng.getstate()
    first = _orders(ac.decide, state, 1)
    assert state.rng.getstate() == rng
    assert [(s.id, s.owner_id, s.ships) for s in state.systems.values()] == \
        [(s.id, s.owner_id, s.ships) for s in before.systems.values()]
    assert len(state.fleets) == len(before.fleets)
    assert _orders(ac.decide, copy.deepcopy(state), 1) == first


def test_knower_never_runs_a_learning_seat(ac, monkeypatch):
    """An oracle models a Learning seat instead of running it, so nothing it does
    on its copies of the board reaches the memo."""
    calls = []
    real = ac._node_for
    monkeypatch.setattr(ac, "_node_for", lambda state: calls.append(state.turn) or real(state))
    state = _seat(_game(bots=("knower", "actuary", "marshal")), 2, float(ac.LEARNING))
    knower = sys.modules["sc_model_knower"]
    for aux in (1.0, 2.0):
        state.players[1].ai_params.aux = aux
        for _ in range(10):
            knower.decide(copy.deepcopy(state), 1)
            engine.end_turn(state, decide=lambda st, pid: [] if pid == 2 else ai.decide(st, pid))
    assert not calls
