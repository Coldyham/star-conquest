"""The influence-field bot: `models/fulcrum.py`.

Pure/headless (no pygame). fulcrum is actuary's ledger with a different pull on
its ships, so these tests cover the drop-in contract every model owes (legal
orders, no mutation, reproducibility, nothing drawn from `state.rng`), then pin
that relationship (with actuary's front pull put back, it plays move for move as
actuary's Greedy stop), and then the field itself: its range, that it reads
lanes rather than turns, and that a balance pulls harder than a front nobody is
contesting.

Internals are reached through `sys.modules["sc_model_fulcrum"]`, the synthetic
name `ai.load_models` imports each model file under.
"""

from __future__ import annotations

import copy
import random
import sys

import pytest

from starconquest import ai, config, engine, mapgen
from starconquest.model import AiParams, Fleet, GameState, Player, System
from tests import sim


@pytest.fixture(scope="module")
def fu():
    assert "fulcrum" in ai.load_models(), "models/fulcrum.py failed to import"
    return sys.modules["sc_model_fulcrum"]


@pytest.fixture(scope="module")
def ac():
    assert "actuary" in ai.load_models(), "models/actuary.py failed to import"
    return sys.modules["sc_model_actuary"]


@pytest.fixture(autouse=True)
def _restore_globals():
    jitter, advantage = config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE
    yield
    config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = jitter, advantage


def _state(seed=3, nodes=24, players=3, seat=2):
    """A generated board with one fulcrum seat and thinker everywhere else."""
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[seat].ai_strategy = "fulcrum"
    return state


def _board(systems, lanes, seats=2, seat=1):
    """A tiny hand-built board. ``systems`` is {sid: (owner, ships, production)}."""
    state = GameState.new(1)
    for sid, (owner, ships, production) in systems.items():
        state.systems[sid] = System(id=sid, pos=(float(sid) * 10.0, 0.0),
                                    owner_id=owner, ships=ships, production=production)
    for a, b, turns in lanes:
        state.add_lane(a, b, turns * config.SHIP_LY_PER_TURN, turns)
    state.rebuild_topology()
    state.players = {0: Player(0, "Neutral", (90, 90, 90), is_neutral=True)}
    for pid in range(1, seats + 1):
        state.players[pid] = Player(pid, f"P{pid}", (200, 60, 60))
    state.players[seat].ai_strategy = "fulcrum"
    return state


def _boards(count=40):
    """Positions out of games the rest of the roster played, for fulcrum to
    decide on as seat 1 (so the set does not move when fulcrum does)."""
    ai.load_models()
    out = []
    for seed in (5, 6):
        state = mapgen.generate(seed, "random", 18, 3)
        for pid, bot in ((1, "thinker"), (2, "marshal"), (3, "claudebot")):
            sim._hand_over(state.players[pid], bot, None)
        while state.winner is None and len(out) < count * seed // 6:
            if state.turn % 4 == 0 and not state.is_defeated(1):
                out.append(copy.deepcopy(state))
            engine.end_turn(state, decide=ai.decide)
    return out


def _orders(mod, state, pid=1):
    return [(o.source_id, o.dest_id, o.ships) for o in mod.decide(state, pid)]


# --------------------------------------------------------------------------- #
# The drop-in contract
# --------------------------------------------------------------------------- #
def test_orders_are_legal(fu):
    """Over live play: our ships, our systems, real lanes, never over-committed."""
    state = _state()
    for _ in range(30):
        if state.winner is not None:
            break
        per_source: dict[int, int] = {}
        for order in ai.decide(state, 2):
            assert order.owner_id == 2
            assert state.systems[order.source_id].owner_id == 2
            assert state.travel_turns(order.source_id, order.dest_id) is not None
            assert order.ships >= 1
            per_source[order.source_id] = per_source.get(order.source_id, 0) + order.ships
        for sid, total in per_source.items():
            assert total <= state.systems[sid].ships, f"over-committed {sid}"
        engine.end_turn(state, decide=ai.decide)


def test_fulcrum_does_not_mutate_state(fu):
    state = _state()
    for _ in range(12):
        engine.end_turn(state, decide=ai.decide)

    def fingerprint(s):
        return (tuple((x.id, x.owner_id, x.ships, x.prod_progress) for x in s.systems.values()),
                tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                      for f in s.fleets),
                s.turn, s.winner)

    before = fingerprint(copy.deepcopy(state))
    fu.decide(state, 2)
    assert fingerprint(state) == before


def test_decide_is_deterministic_and_draws_no_rng(fu):
    """knower predicts a seat by running its `decide` on a copy, so a bot that
    drew from `state.rng` would move every seat's dice after it."""
    state = _state()
    for _ in range(10):
        engine.end_turn(state, decide=ai.decide)
    rng_before = state.rng.getstate()
    first = _orders(fu, state, 2)
    assert state.rng.getstate() == rng_before
    assert _orders(fu, copy.deepcopy(state), 2) == first


def test_game_is_reproducible_from_its_seed(fu):
    a = sim.play(7, nodes=18, players=2, strategies=["fulcrum", "thinker"], max_turns=300)
    b = sim.play(7, nodes=18, players=2, strategies=["fulcrum", "thinker"], max_turns=300)
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_fulcrum_is_not_an_oracle(fu):
    """It predicts nobody and remembers nothing, so knower runs it like any
    other bot, and it has no knob."""
    assert not getattr(fu, "IS_ORACLE", False)
    assert not hasattr(fu, "is_oracle_seat")
    assert ai.aux_spec("fulcrum") is None


def test_declares_what_a_decide_costs(fu):
    """knower prices a Search against rival bots with this (`ai.decide_ms`)."""
    from types import SimpleNamespace
    from starconquest.settings import Settings
    small, big = Settings(nodes=18), Settings(nodes=120)
    assert 0 < ai.decide_ms("fulcrum", small, 2) < ai.decide_ms("fulcrum", big, 2)
    small.custom_map = SimpleNamespace(nodes=[None] * 120)       # a hand map's own count
    assert ai.decide_ms("fulcrum", small, 2) == ai.decide_ms("fulcrum", big, 2)


def test_beats_the_heuristic(fu):
    r = sim.play(2, nodes=18, players=2, strategies=["fulcrum", "heuristic"], max_turns=300)
    assert r.winner == 1


# --------------------------------------------------------------------------- #
# The ledger is actuary's
# --------------------------------------------------------------------------- #
def test_with_actuarys_pull_it_plays_actuarys_greedy(fu, ac, monkeypatch):
    """Everything but the pull is actuary's ledger: put actuary's front values
    back in place of the field and every order matches its Greedy stop."""
    boards = _boards(30)
    greedy = []
    for board in boards:
        board.players[1].ai_params = AiParams(aux=float(ac.GREEDY))
        greedy.append(_orders(ac, board))
    monkeypatch.setattr(fu._Ledger, "_field", ac._Ledger._front_values)
    assert [_orders(fu, b) for b in boards] == greedy
    assert any(greedy)


def test_a_priced_gain_is_exactly_the_change_in_the_ledger(fu):
    """`_gain` re-projects only what a launch touches; summing every system's
    worth and risk from scratch before and after must agree with it."""
    rng = random.Random(1)
    checked = 0
    for state in _boards(24)[::3]:
        ledger = fu._Ledger(state, 1)
        ledger.plan()
        launches = list(ledger._candidates())
        for launch in rng.sample(launches, min(4, len(launches))):
            gain = ledger._gain(launch)[0]
            after = copy.copy(ledger)
            for name in ("garrison", "arrivals", "lines", "worth", "risk", "version"):
                setattr(after, name, dict(getattr(ledger, name)))
            after.orders = list(ledger.orders)

            def total(led):
                return sum(led._worth(s, led.lines[s]) - led._risk(s) for s in led.ids)

            before = total(after)
            after._commit(launch)
            assert total(after) - before == pytest.approx(gain, abs=1e-9)
            checked += 1
    assert checked >= 10


def test_the_caches_change_no_answer(fu, monkeypatch):
    """Stamped caches and the idle prune are only shortcuts: with every stamp
    unique (nothing ever reused) and nothing pruned, the plan is the same."""
    boards = _boards(30)
    cached = [_orders(fu, b) for b in boards]
    monkeypatch.setattr(fu._Ledger, "_stamp", lambda self, sids: object())
    monkeypatch.setattr(fu._Ledger, "_idle", lambda self, src, dst: False)
    assert [_orders(fu, b) for b in boards] == cached
    assert any(cached)


# --------------------------------------------------------------------------- #
# The field
# --------------------------------------------------------------------------- #
def _front(distance=2):
    """Ours: 1 at home, 2 and 3 on two fronts. Front 2 faces a rival stack of
    about our size (a balance); front 3 faces a rival garrison our side swamps.
    ``distance`` scales every lane."""
    d = distance
    return _board({1: (1, 4, 3), 2: (1, 20, 3), 3: (1, 30, 3),
                   4: (2, 22, 3), 5: (2, 2, 3), 6: (2, 25, 3)},
                  [(1, 2, d), (1, 3, d), (2, 4, d), (3, 5, d), (4, 6, d), (5, 6, 3 * d)])


def test_the_pull_runs_from_one_to_the_full_bonus(fu):
    """Every system's pull is between 1 and 1 + FRONT_BONUS; the strongest of
    ours has the whole bonus, and a landing on a system we do not hold keeps it,
    as in actuary."""
    for board in _boards(16):
        ledger = fu._Ledger(board, 1)
        mine = [s for s in ledger.ids if board.systems[s].owner_id == 1]
        for sid, value in ledger.front_value.items():
            assert 1.0 <= value <= 1.0 + fu.FRONT_BONUS + 1e-12
            if board.systems[sid].owner_id != 1:
                assert value == 1.0 + fu.FRONT_BONUS
        if mine and max(ledger.front_value[s] for s in mine) > 1.0:
            assert max(ledger.front_value[s] for s in mine) == pytest.approx(1.0 + fu.FRONT_BONUS)


def test_a_balance_pulls_harder_than_a_front_nobody_contests(fu):
    ledger = fu._Ledger(_front(), 1)
    assert ledger.front_value[2] > ledger.front_value[3]


def test_a_lane_behind_a_balance_pulls_harder_than_a_quiet_front(fu):
    """Home touches no rival, so it has no stake of its own, but it is a lane
    from the balance at 2: it pulls less than 2 and more than the front at 3,
    which faces a garrison our side swamps. actuary pulls 2 and 3 alike and home
    least of all."""
    pull = fu._Ledger(_front(), 1).front_value
    assert pull[3] < pull[1] < pull[2]


def test_the_field_reads_lanes_not_turns(fu):
    """Distance is counted in median lanes, so a board with every lane twice as
    long, fleets included, pulls exactly as the original does."""
    short, long = _front(2), _front(4)
    short.fleets.append(Fleet(2, 6, 4, 9, 2, 1))
    long.fleets.append(Fleet(2, 6, 4, 9, 4, 2))
    assert fu._Ledger(short, 1).front_value == pytest.approx(fu._Ledger(long, 1).front_value)


def test_a_board_with_nothing_to_contest_has_no_pull(fu):
    """No rival and nothing unowned left: every system of ours is interior."""
    state = _board({1: (1, 5, 3), 2: (1, 5, 3)}, [(1, 2, 2)])
    ledger = fu._Ledger(state, 1)
    assert ledger.front_value == {1: 1.0, 2: 1.0}
    assert fu.decide(state, 1) == []
