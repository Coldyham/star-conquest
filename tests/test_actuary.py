"""The ledger bot: `models/actuary.py`.

Pure/headless (no pygame). actuary reads no other seat's code, so these tests
cover the drop-in contract every model owes (legal orders, no mutation,
reproducibility, nothing drawn from `state.rng`) and then the three things its
design rests on: the projection agrees with the engine wherever it is not
deliberately pessimistic, a launch's priced gain is exactly the change in the
ledger, and the caches that keep a decide cheap change no answer. Last, the
Field stop's pull: its range, that it reads lanes rather than turns, and that a
balance pulls harder than a front nobody is contesting.

Internals are reached through `sys.modules["sc_model_actuary"]`, the synthetic
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
def ac():
    assert "actuary" in ai.load_models(), "models/actuary.py failed to import"
    return sys.modules["sc_model_actuary"]


@pytest.fixture(autouse=True)
def _restore_globals():
    jitter, advantage = config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE
    yield
    config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = jitter, advantage


def _state(seed=3, nodes=24, players=3, seat=2):
    """A generated board with one actuary seat and thinker everywhere else."""
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[seat].ai_strategy = "actuary"
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
    state.players[seat].ai_strategy = "actuary"
    return state


def _bait_state(neighbour=(0, 5, 3), sent=11):
    """T (rival, 1) emptied onto N: the rival's ``sent`` ships land on N next
    turn, and the lane back to T is 2 turns. Our 12 at S are 3 turns from T."""
    state = _board({20: (2, 1, 3), 21: neighbour, 22: (1, 12, 3)},
                   [(20, 21, 2), (22, 20, 3)])
    state.fleets.append(Fleet(owner_id=2, source_id=20, dest_id=21, ships=sent,
                              turns_total=2, turns_remaining=1))
    return state


def test_relief_in_flight_lands_from_the_turn_it_could_turn_round(ac):
    ledger = ac._Ledger(_bait_state(), 1)
    by_t, flying = ledger.relief[20]
    assert flying.get(2, 0) == 0
    assert 0 < flying[3] < 11, "the landing fight thins the relief"
    assert flying[ledger.horizon] == flying[3]


def test_relief_in_flight_needs_to_win_where_it_lands(ac):
    _, flying = ac._Ledger(_bait_state(neighbour=(0, 20, 3), sent=5), 1).relief[20]
    assert not flying


def test_relief_in_flight_counts_in_full_onto_its_own_system(ac):
    _, flying = ac._Ledger(_bait_state(neighbour=(2, 0, 3)), 1).relief[20]
    assert flying[3] == 11


def test_relief_in_flight_off_reads_nothing(ac, monkeypatch):
    monkeypatch.setattr(ac, "RELIEF_IN_FLIGHT", 0.0)
    _, flying = ac._Ledger(_bait_state(), 1).relief[20]
    assert not flying


def _boards(count=40):
    """Positions out of games the rest of the roster played, for actuary to
    decide on as seat 1 (so the set does not move when actuary does)."""
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


def _orders(ac, state, pid=1):
    return [(o.source_id, o.dest_id, o.ships) for o in ac.decide(state, pid)]


# --------------------------------------------------------------------------- #
# The drop-in contract
# --------------------------------------------------------------------------- #
def test_orders_are_legal(ac):
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


def test_actuary_does_not_mutate_state(ac):
    state = _state()
    for _ in range(12):
        engine.end_turn(state, decide=ai.decide)

    def fingerprint(s):
        return (tuple((x.id, x.owner_id, x.ships, x.prod_progress) for x in s.systems.values()),
                tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                      for f in s.fleets),
                s.turn, s.winner)

    before = fingerprint(copy.deepcopy(state))
    ac.decide(state, 2)
    assert fingerprint(state) == before


def test_decide_is_deterministic_and_draws_no_rng(ac):
    """knower predicts a seat by running its `decide` on a copy, so a bot that
    drew from `state.rng` would move every seat's dice after it."""
    state = _state()
    for _ in range(10):
        engine.end_turn(state, decide=ai.decide)
    rng_before = state.rng.getstate()
    first = _orders(ac, state, 2)
    assert state.rng.getstate() == rng_before
    assert _orders(ac, copy.deepcopy(state), 2) == first


def test_game_is_reproducible_from_its_seed(ac):
    a = sim.play(7, nodes=18, players=2, strategies=["actuary", "thinker"], max_turns=300)
    b = sim.play(7, nodes=18, players=2, strategies=["actuary", "thinker"], max_turns=300)
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_the_style_slider_defaults_to_field(ac):
    assert ai.aux_spec("actuary") == ("Style", 0.0, 2.0, 1.0, True)
    assert ai.aux_names("actuary") == ("Greedy", "Field", "Learning")
    assert AiParams().aux == ac.FIELD


@pytest.mark.parametrize("aux, stop", [(0.0, "GREEDY"), (1.0, "FIELD"), (2.0, "LEARNING"),
                                       (7.0, "LEARNING"), (0.5, "GREEDY"), ("junk", "FIELD")])
def test_the_style_knob_reads_tolerantly(ac, aux, stop):
    from types import SimpleNamespace
    seat = SimpleNamespace(ai_params=SimpleNamespace(aux=aux))
    assert ac._stop_of(seat) == getattr(ac, stop)


def test_actuary_is_not_an_oracle(ac):
    assert not getattr(ac, "IS_ORACLE", False)


# --------------------------------------------------------------------------- #
# The projection agrees with the engine where it is not priced against us
# --------------------------------------------------------------------------- #
def test_production_and_reinforcement_match_the_engine(ac):
    """No fight anywhere: the timeline is the engine's, turn for turn."""
    state = _board({1: (1, 4, 3), 2: (1, 9, 2), 3: (2, 6, 5), 4: (0, 5, 4)},
                   [(1, 2, 2), (2, 3, 3), (3, 4, 2)])
    state.systems[1].prod_progress = 2
    state.fleets.append(Fleet(1, 2, 1, 5, 2, 2))
    ledger = ac._Ledger(state, 1)
    board = copy.deepcopy(state)
    for t in range(1, ledger.horizon + 1):
        engine.end_turn(board, decide=lambda s, pid: [])
        for sid in state.systems:
            owners, ships = ledger.lines[sid]
            assert (owners[t], ships[t]) == (board.systems[sid].owner_id, board.systems[sid].ships), (sid, t)


def test_a_rival_pileup_folds_as_the_engine_does(ac):
    """With no jitter and nobody's fight ours, the fold is the engine's own: the
    attackers strongest-first, the survivor against the garrison last."""
    config.COMBAT_JITTER = 0.0
    state = _board({1: (1, 30, 3), 5: (2, 6, 4), 6: (3, 9, 4), 7: (4, 4, 4)},
                   [(1, 5, 4), (5, 6, 2), (5, 7, 2)], seats=4)
    state.fleets.append(Fleet(3, 6, 5, 9, 2, 2))
    state.fleets.append(Fleet(4, 7, 5, 4, 2, 2))
    ledger = ac._Ledger(state, 1)
    board = copy.deepcopy(state)
    for _ in range(2):
        engine.end_turn(board, decide=lambda s, pid: [])
    owners, ships = ledger.lines[5]
    assert (owners[2], ships[2]) == (board.systems[5].owner_id, board.systems[5].ships)


def test_a_capture_must_win_the_worst_roll(ac):
    """A launch that only wins on a good roll is not counted as a capture, and a
    wider jitter or a stronger defender asks for more."""
    def takes(ships, jitter, advantage):
        config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = jitter, advantage
        state = _board({1: (1, ships, 3), 2: (0, 10, 3)}, [(1, 2, 2)])
        ledger = ac._Ledger(state, 1)
        ledger.version = dict.fromkeys(ledger.ids, 0)
        return ledger._enough(2, ((1, 2),), ships) is not None

    assert takes(13, 0.10, 1.0) and not takes(12, 0.10, 1.0)   # 10 * 1.222 = 12.2
    assert not takes(13, 0.30, 1.0)                           # 10 * 1.857
    assert not takes(13, 0.10, 1.25)


# --------------------------------------------------------------------------- #
# Pricing and the greedy
# --------------------------------------------------------------------------- #
def test_a_priced_gain_is_exactly_the_change_in_the_ledger(ac):
    """`_gain` re-projects only what a launch touches; summing every system's
    worth and risk from scratch before and after must agree with it."""
    rng = random.Random(1)
    checked = 0
    for state in _boards(24)[::3]:
        ledger = ac._Ledger(state, 1)
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


def test_the_caches_change_no_answer(ac, monkeypatch):
    """Stamped caches and the idle prune are only shortcuts: with every stamp
    unique (nothing ever reused) and nothing pruned, the plan is the same."""
    boards = _boards(30)
    cached = [_orders(ac, b) for b in boards]
    monkeypatch.setattr(ac._Ledger, "_stamp", lambda self, sids: object())
    monkeypatch.setattr(ac._Ledger, "_idle", lambda self, src, dst: False)
    assert [_orders(ac, b) for b in boards] == cached
    assert any(cached)


def test_relieves_a_system_that_would_fall(ac):
    """A rival strike that would take system 1 is answered from next door,
    with nothing in the code saying "defend"."""
    state = _board({1: (1, 4, 3), 2: (1, 30, 3), 3: (2, 2, 3)},
                   [(1, 2, 1), (1, 3, 3), (2, 3, 4)])
    state.fleets.append(Fleet(2, 3, 1, 12, 3, 2))
    orders = _orders(ac, state)
    assert sum(x for src, dst, x in orders if (src, dst) == (2, 1)) >= 8


def test_takes_a_neutral_it_can_take(ac):
    state = _board({1: (1, 20, 3), 2: (0, 5, 2), 3: (2, 3, 3)},
                   [(1, 2, 2), (2, 3, 6)])
    assert any(dst == 2 for _, dst, _ in _orders(ac, state))


def test_declares_what_a_decide_costs(ac):
    """knower prices a Search against rival bots with this (`ai.decide_ms`)."""
    from types import SimpleNamespace
    from starconquest.settings import Settings
    small, big = Settings(nodes=18), Settings(nodes=120)
    assert 0 < ai.decide_ms("actuary", small, 2) < ai.decide_ms("actuary", big, 2)
    small.custom_map = SimpleNamespace(nodes=[None] * 120)       # a hand map's own count
    assert ai.decide_ms("actuary", small, 2) == ai.decide_ms("actuary", big, 2)


def test_beats_the_heuristic(ac):
    r = sim.play(2, nodes=18, players=2, strategies=["actuary", "heuristic"], max_turns=300)
    assert r.winner == 1


# --------------------------------------------------------------------------- #
# The field (Style: Field and Learning)
# --------------------------------------------------------------------------- #
def _front(distance=2):
    """Ours: 1 at home, 2 and 3 on two fronts. Front 2 faces a rival stack of
    about our size (a balance); front 3 faces a rival garrison our side swamps.
    ``distance`` scales every lane."""
    d = distance
    return _board({1: (1, 4, 3), 2: (1, 20, 3), 3: (1, 30, 3),
                   4: (2, 22, 3), 5: (2, 2, 3), 6: (2, 25, 3)},
                  [(1, 2, d), (1, 3, d), (2, 4, d), (3, 5, d), (4, 6, d), (5, 6, 3 * d)])


def _at(state, stop):
    state.players[1].ai_params = AiParams(aux=float(stop))
    return state


def test_field_differs_from_greedy_only_in_the_pull(ac, monkeypatch):
    """Put Greedy's front values back in place of the field and every Field
    order matches Greedy's."""
    boards = _boards(30)
    greedy = [_orders(ac, _at(b, ac.GREEDY)) for b in boards]
    field = [_orders(ac, _at(b, ac.FIELD)) for b in boards]
    assert field != greedy, "the field should move some orders"
    monkeypatch.setattr(ac._Ledger, "_field", ac._Ledger._front_values)
    assert [_orders(ac, _at(b, ac.FIELD)) for b in boards] == greedy
    assert any(greedy)


@pytest.mark.parametrize("stop", ["GREEDY", "FIELD"])
def test_its_orders_never_depend_on_what_a_rival_runs(ac, stop):
    """Greedy and Field are no oracle in behaviour as well as in their flags:
    they read the board alone, so a rival said to run another bot at another
    knob changes no order, and deciding the positions in the other order
    changes none either."""
    boards = [_at(b, getattr(ac, stop)) for b in _boards(12)]
    first = [_orders(ac, b) for b in boards]
    for b in boards:
        for pid, p in b.players.items():
            if pid != 1 and not p.is_neutral:
                p.ai_strategy, p.ai_params = "knower", AiParams(aux=2.0)
    assert [_orders(ac, b) for b in reversed(boards)][::-1] == first
    assert not ac.is_oracle_seat(boards[0].players[1])


def test_the_pull_runs_from_one_to_the_full_bonus(ac):
    """Every system's pull is between 1 and 1 + FRONT_BONUS; the strongest of
    ours has the whole bonus, and a landing on a system we do not hold keeps it,
    as on Greedy."""
    for board in _boards(16):
        ledger = ac._Ledger(board, 1, field=True)
        mine = [s for s in ledger.ids if board.systems[s].owner_id == 1]
        for sid, value in ledger.front_value.items():
            assert 1.0 <= value <= 1.0 + ac.FRONT_BONUS + 1e-12
            if board.systems[sid].owner_id != 1:
                assert value == 1.0 + ac.FRONT_BONUS
        if mine and max(ledger.front_value[s] for s in mine) > 1.0:
            assert max(ledger.front_value[s] for s in mine) == pytest.approx(1.0 + ac.FRONT_BONUS)


def test_a_balance_pulls_harder_than_a_front_nobody_contests(ac):
    ledger = ac._Ledger(_front(), 1, field=True)
    assert ledger.front_value[2] > ledger.front_value[3]


def test_a_lane_behind_a_balance_pulls_harder_than_a_quiet_front(ac):
    """Home touches no rival, so it has no stake of its own, but it is a lane
    from the balance at 2: it pulls less than 2 and more than the front at 3,
    which faces a garrison our side swamps. Greedy pulls 2 and 3 alike and home
    least of all."""
    pull = ac._Ledger(_front(), 1, field=True).front_value
    assert pull[3] < pull[1] < pull[2]
    greedy = ac._Ledger(_front(), 1).front_value
    assert greedy[2] == greedy[3] > greedy[1]


def test_the_field_reads_lanes_not_turns(ac):
    """Distance is counted in median lanes, so a board with every lane twice as
    long, fleets included, pulls exactly as the original does."""
    short, long = _front(2), _front(4)
    short.fleets.append(Fleet(2, 6, 4, 9, 2, 1))
    long.fleets.append(Fleet(2, 6, 4, 9, 4, 2))
    assert ac._Ledger(short, 1, field=True).front_value == \
        pytest.approx(ac._Ledger(long, 1, field=True).front_value)


def test_slow_lanes_pull_half_the_field_and_half_the_front(ac):
    """From `SLOW_LANE` turns a median lane is slow, and Field's pull there is
    the mean of the field and Greedy's front; below it, the field alone."""
    fast, slow = _front(ac.SLOW_LANE - 1), _front(ac.SLOW_LANE)
    assert ac._Ledger(fast, 1, field=True).front_value == ac._Ledger(fast, 1)._field()
    ledger = ac._Ledger(slow, 1, field=True)
    field, front = ledger._field(), ledger._front_values()
    assert field != front
    assert ledger.front_value == pytest.approx({s: (field[s] + front[s]) / 2 for s in field})


def test_a_board_with_nothing_to_contest_has_no_pull(ac):
    """No rival and nothing unowned left: every system of ours is interior."""
    state = _at(_board({1: (1, 5, 3), 2: (1, 5, 3)}, [(1, 2, 2)]), ac.FIELD)
    assert ac._Ledger(state, 1, field=True).front_value == {1: 1.0, 2: 1.0}
    assert ac.decide(state, 1) == []
