"""The ledger bot: `models/actuary.py`.

Pure/headless (no pygame). actuary reads no other seat's code, so these tests
cover the drop-in contract every model owes (legal orders, no mutation,
reproducibility, nothing drawn from `state.rng`) and then the three things its
design rests on: the projection agrees with the engine wherever it is not
deliberately pessimistic, a launch's priced gain is exactly the change in the
ledger, and the caches that keep a decide cheap change no answer. Last, the
planned opening (Opening: Planned): when it hands over, and the trade its score
makes between ships and income.

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


def test_the_style_slider_defaults_to_planned(ac):
    assert ai.aux_spec("actuary") == ("Style", 0.0, 2.0, 1.0, True)
    assert ai.aux_names("actuary") == ("Greedy", "Planned", "Learning")
    assert AiParams().aux == ac.PLANNED


@pytest.mark.parametrize("aux, stop", [(0.0, "GREEDY"), (1.0, "PLANNED"), (7.0, "PLANNED"),
                                       (0.5, "GREEDY"), ("junk", "PLANNED")])
def test_the_opening_knob_reads_tolerantly(ac, aux, stop):
    from types import SimpleNamespace
    seat = SimpleNamespace(ai_params=SimpleNamespace(aux=aux))
    assert ac._opening_of(seat) == getattr(ac, stop)


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
# The planned opening (Opening: Planned)
# --------------------------------------------------------------------------- #
def _planned(state, seat=1):
    state.players[seat].ai_params = AiParams()
    return state


@pytest.fixture
def small_side(ac, monkeypatch):
    """Let the opening run on a hand-built board. Ours are small enough to read
    by eye, which puts them under `OPENING_MIN_SIDE`."""
    monkeypatch.setattr(ac, "OPENING_MIN_SIDE", 0)


def _costly_neutral(rival_lane):
    """Our home 1 next to a costly, poor neutral 2; the rival's home 3 is
    ``rival_lane`` turns beyond a neutral 4 that sits between us."""
    return _planned(_board({1: (1, 20, 3), 2: (0, 14, 6), 3: (2, 12, 3), 4: (0, 3, 5)},
                           [(1, 2, 2), (1, 4, 2), (4, 3, rival_lane)]))


def test_greedy_never_consults_the_plan(ac, monkeypatch):
    def boom(state, pid):
        raise AssertionError("the opening ran on a Greedy seat")

    monkeypatch.setattr(ac, "opening", boom)
    state = _costly_neutral(30)
    state.players[1].ai_params = AiParams(aux=0.0)
    ac.decide(state, 1)


def test_planned_orders_are_legal_and_reproducible(ac):
    state = _state(nodes=40, players=2, seat=2)
    state.players[2].ai_params = AiParams()
    for _ in range(40):
        if state.winner is not None:
            break
        rng_before = state.rng.getstate()
        orders = ai.decide(state, 2)
        assert state.rng.getstate() == rng_before
        assert [(o.source_id, o.dest_id, o.ships) for o in ac.decide(copy.deepcopy(state), 2)] == \
            [(o.source_id, o.dest_id, o.ships) for o in orders]
        per_source: dict[int, int] = {}
        for order in orders:
            assert state.systems[order.source_id].owner_id == 2
            assert state.travel_turns(order.source_id, order.dest_id) is not None
            per_source[order.source_id] = per_source.get(order.source_id, 0) + order.ships
        for sid, total in per_source.items():
            assert 0 < total <= state.systems[sid].ships
        engine.end_turn(state, decide=ai.decide)


def test_the_opening_ends_at_contact(ac, small_side):
    """Bordering a rival, or a rival fleet heading for us, hands the seat to the
    ledger."""
    state = _costly_neutral(30)
    assert ac.opening(state, 1) is not None
    state.systems[4].owner_id = 2
    assert ac.opening(state, 1) is None
    state.systems[4].owner_id = 0
    state.fleets.append(Fleet(2, 3, 4, 5, 30, 3))
    assert ac.opening(state, 1) is not None
    state.fleets.append(Fleet(2, 4, 1, 5, 2, 1))
    assert ac.opening(state, 1) is None


def test_a_side_too_small_to_plan_is_left_to_the_ledger(ac, monkeypatch):
    """Our side counts what we hold plus the region: here home 1 and neutrals
    2 and 4, three systems."""
    state = _costly_neutral(30)
    monkeypatch.setattr(ac, "OPENING_MIN_SIDE", 3)
    assert ac.opening(state, 1) is not None
    monkeypatch.setattr(ac, "OPENING_MIN_SIDE", 4)
    assert ac.opening(state, 1) is None
    planned = _orders(ac, state)
    state.players[1].ai_params = AiParams(aux=0.0)
    assert planned == _orders(ac, state)


def test_after_contact_planned_plays_the_ledger(ac, small_side):
    state = _costly_neutral(30)
    state.systems[4].owner_id = 2
    planned = _orders(ac, state)
    state.players[1].ai_params = AiParams(aux=0.0)
    assert planned == _orders(ac, state)


def test_a_neutral_equally_near_a_rival_is_not_ours_to_take(ac):
    assert ac._Opening(_costly_neutral(2), 1).region == {2}


def test_keeps_its_ships_when_contact_is_near(ac, small_side):
    """The rival can land on us in four turns; neutral 2 costs nearly six ships
    even taken with everything, and repays a sixth of one a turn."""
    state = _costly_neutral(2)
    assert ac._Opening(state, 1).clock == round(4 * ac.OPENING_CLOCK_SCALE)
    assert not any(dst == 2 for _, dst, _ in _orders(ac, state))


def test_spends_them_when_contact_is_far(ac, small_side):
    state = _costly_neutral(30)
    assert ac._Opening(state, 1).clock > 20
    assert any(dst in (2, 4) for _, dst, _ in _orders(ac, state))


def test_moves_ships_with_nothing_to_take_towards_the_expansion(ac, small_side):
    """Home 1 is inland; 5 borders the neutrals, so 1's ships go to 5."""
    state = _planned(_board({1: (1, 20, 3), 5: (1, 0, 3), 2: (0, 4, 2), 3: (2, 12, 3)},
                            [(1, 5, 2), (5, 2, 2), (2, 3, 40)]))
    assert (1, 5, 20) in _orders(ac, state)


def test_every_plan_it_compares_is_a_different_plan(ac):
    """The policies are a cross product; on an open board they should not all
    agree, or the search is buying nothing."""
    import itertools
    plan = ac._Opening(_state(nodes=40, players=2, seat=1), 1)
    firsts = set()
    for policy in itertools.product(ac.OPENING_ORDERS, ac.OPENING_SENDS, ac.OPENING_SKIPS):
        sim_ = ac._OpeningSim(plan, policy)
        sim_.run()
        firsts.add(tuple((o.source_id, o.dest_id, o.ships) for o in sim_.first))
    assert len(firsts) > 1
