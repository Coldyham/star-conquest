"""The actuary fork that guards only against strikes that come: `models/hunter.py`.

Pure/headless (no pygame). hunter reads no other seat's code, so these tests
cover the drop-in contract every model owes (legal orders, no mutation,
reproducibility, nothing drawn from `state.rng`), then the one claim its
measurements rest on, that with every switch off it is actuary exactly, and
then each switch on a board small enough to read by eye.

Internals are reached through `sys.modules["sc_model_hunter"]`, the synthetic
name `ai.load_models` imports each model file under.
"""

from __future__ import annotations

import copy
import sys

import pytest

from starconquest import ai, config, engine, mapgen
from starconquest.model import AiParams, GameState, Player, System
from tests import sim

# hunter's changes, each set to what actuary does.
ACTUARY = {"EDGE_RISK": False, "CAPTURE_SWING": 0.0, "EDGE_GATHER_LANE": 0,
           "REINFORCE_WEIGHT": 0.3}


@pytest.fixture(scope="module")
def hu():
    loaded = ai.load_models()
    assert "hunter" in loaded and "actuary" in loaded, "a model file failed to import"
    return sys.modules["sc_model_hunter"]


@pytest.fixture(autouse=True)
def _restore_globals():
    jitter, advantage = config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE
    yield
    config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = jitter, advantage


def _state(seed=3, nodes=24, players=3, seat=2):
    """A generated board with one hunter seat and thinker everywhere else."""
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[seat].ai_strategy = "hunter"
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
    state.players[seat].ai_strategy = "hunter"
    return state


def _boards(monkeypatch):
    """Positions out of games the rest of the roster played, on fast and slow
    lanes, for hunter and actuary to decide on as seat 1."""
    ai.load_models()
    out = []
    for seed, ly in ((5, 6.0), (6, 3.0), (7, 18.0)):
        monkeypatch.setattr(config, "SHIP_LY_PER_TURN", ly)
        state = mapgen.generate(seed, "random", 18, 3)
        for pid, bot in ((1, "thinker"), (2, "marshal"), (3, "claudebot")):
            sim._hand_over(state.players[pid], bot, None)
        while state.winner is None and state.turn < 60:
            if state.turn % 4 == 0 and not state.is_defeated(1):
                out.append(copy.deepcopy(state))
            engine.end_turn(state, decide=ai.decide)
    return out


def _orders(mod, state, pid=1):
    return [(o.source_id, o.dest_id, o.ships) for o in mod.decide(state, pid)]


# --------------------------------------------------------------------------- #
# The drop-in contract
# --------------------------------------------------------------------------- #
def test_orders_are_legal(hu):
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


def test_hunter_does_not_mutate_state(hu):
    state = _state()
    for _ in range(12):
        engine.end_turn(state, decide=ai.decide)

    def fingerprint(s):
        return (tuple((x.id, x.owner_id, x.ships, x.prod_progress) for x in s.systems.values()),
                tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                      for f in s.fleets),
                s.turn, s.winner)

    before = fingerprint(copy.deepcopy(state))
    hu.decide(state, 2)
    assert fingerprint(state) == before


def test_decide_is_deterministic_and_draws_no_rng(hu):
    state = _state()
    for _ in range(10):
        engine.end_turn(state, decide=ai.decide)
    rng_before = state.rng.getstate()
    first = _orders(hu, state, 2)
    assert state.rng.getstate() == rng_before
    assert _orders(hu, copy.deepcopy(state), 2) == first


def test_game_is_reproducible_from_its_seed(hu):
    a = sim.play(7, nodes=18, players=2, strategies=["hunter", "thinker"], max_turns=300)
    b = sim.play(7, nodes=18, players=2, strategies=["hunter", "thinker"], max_turns=300)
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_hunter_is_no_oracle_and_keeps_no_memory(hu):
    """It runs no other bot and reads only the board, so an oracle may run it as
    it is: nothing marks it as a seat to be modelled instead."""
    assert not getattr(hu, "IS_ORACLE", False)
    assert not hasattr(hu, "is_oracle_seat")
    assert not hasattr(hu, "reset")


def test_beats_the_heuristic(hu):
    r = sim.play(2, nodes=18, players=2, strategies=["hunter", "heuristic"], max_turns=300)
    assert r.winner == 1


def test_declares_what_a_decide_costs(hu):
    from starconquest.settings import Settings
    small, big = Settings(nodes=18), Settings(nodes=120)
    assert 0 < ai.decide_ms("hunter", small, 2) < ai.decide_ms("hunter", big, 2)


# --------------------------------------------------------------------------- #
# Every switch off is actuary
# --------------------------------------------------------------------------- #
def test_every_switch_off_is_actuary(hu, monkeypatch):
    """Every measurement of hunter is paired against actuary on the same seeds,
    and the fork scored exactly 50% before any switch was turned on. With the
    switches back at actuary's values the two must issue the same orders."""
    actuary = sys.modules["sc_model_actuary"]
    for name, value in ACTUARY.items():
        monkeypatch.setattr(hu, name, value)
    boards = _boards(monkeypatch)
    assert len(boards) > 20
    for state in boards:
        assert _orders(hu, state) == _orders(actuary, state), f"turn {state.turn}"


def test_the_switches_change_play(hu, monkeypatch):
    """...and as shipped, they do."""
    actuary = sys.modules["sc_model_actuary"]
    assert any(_orders(hu, s) != _orders(actuary, s) for s in _boards(monkeypatch))


# --------------------------------------------------------------------------- #
# Edge risk: a threat is a strike that wins the worst roll
# --------------------------------------------------------------------------- #
def _front(rival_ships, lane=2):
    """Our 10 at system 1, a rival's ``rival_ships`` at 3 next door, nothing else."""
    return _board({1: (1, 10, 9), 3: (2, rival_ships, 9)}, [(1, 3, lane)])


def test_a_garrison_above_the_worst_roll_is_safe(hu):
    """At jitter 0.1 a rival needs more than 10 x 1.222 = 12.2 to be sure of our
    10: 12 next door prices no risk, 13 does. actuary prices both."""
    actuary = sys.modules["sc_model_actuary"]
    assert hu._Ledger(_front(12), 1).risk[1] == 0.0
    assert hu._Ledger(_front(13), 1).risk[1] > 0.0
    assert actuary._Ledger(_front(12), 1).risk[1] > 0.0


def test_strikes_on_different_lanes_do_not_add(hu):
    """Two rival garrisons whose lanes land on different turns are two strikes,
    each too small; the same two on equal lanes are one that wins."""
    split = _board({1: (1, 10, 9), 3: (2, 7, 9), 4: (2, 7, 9)}, [(1, 3, 2), (1, 4, 3)])
    joint = _board({1: (1, 10, 9), 3: (2, 7, 9), 4: (2, 7, 9)}, [(1, 3, 2), (1, 4, 2)])
    assert hu._Ledger(split, 1).risk[1] == 0.0
    assert hu._Ledger(joint, 1).risk[1] > 0.0


def test_gathering_and_soft_strikes_follow_the_lanes(hu):
    """Gathering is read on fast boards only, the nominal-roll strike on slow ones."""
    fast, slow = hu._Ledger(_front(5, lane=1), 1), hu._Ledger(_front(5, lane=7), 1)
    assert fast.gather and not fast.capture
    assert slow.capture == hu.CAPTURE_SWING and not slow.gather


def test_on_slow_lanes_a_strike_need_only_win_the_nominal_roll(hu):
    """Our 11 against a rival's 10: enough on 7-turn lanes, not on 2-turn ones."""
    def takes(lane):
        state = _board({1: (1, 11, 9), 2: (2, 10, 9)}, [(1, 2, lane)])
        ledger = hu._Ledger(state, 1)
        ledger.version = dict.fromkeys(ledger.ids, 0)
        return ledger._enough(2, ((1, 2),), 11) is not None

    assert takes(7) and not takes(2)


# --------------------------------------------------------------------------- #
# The Style knob
# --------------------------------------------------------------------------- #
def test_the_style_knob_has_two_stops(hu):
    assert hu.AUX_NAMES == ("Greedy", "Planned")
    params = AiParams()
    assert hu._stop_of(type("P", (), {"ai_params": params})()) == hu.PLANNED


@pytest.mark.parametrize("aux, stop", [(0.0, 0), (1.0, 1), (2.0, 1), (7.0, 1), ("x", 1)])
def test_the_knob_reads_tolerantly(hu, aux, stop):
    """actuary's Learning stop and anything past it read as Planned."""
    seat = type("P", (), {"ai_params": type("A", (), {"aux": aux})()})()
    assert hu._stop_of(seat) == stop


# --------------------------------------------------------------------------- #
# Look-ahead
# --------------------------------------------------------------------------- #
def test_look_ahead_is_legal_read_only_and_reproducible(hu, monkeypatch):
    monkeypatch.setattr(hu, "LOOKAHEAD", True)
    monkeypatch.setattr(hu, "BUDGET_SCALE", float("inf"))
    state = _state(nodes=18, players=2, seat=1)
    for _ in range(25):
        engine.end_turn(state, decide=ai.decide)
    before = (tuple((s.owner_id, s.ships) for s in state.systems.values()), len(state.fleets),
              state.rng.getstate(), state.dice_salt)
    first = _orders(hu, state, 1)
    assert (tuple((s.owner_id, s.ships) for s in state.systems.values()), len(state.fleets),
            state.rng.getstate(), state.dice_salt) == before
    assert _orders(hu, copy.deepcopy(state), 1) == first
    for src, dst, n in first:
        assert state.systems[src].owner_id == 1 and n >= 1


def test_rollouts_never_roll_the_games_dice(hu):
    """A rollout's board rolls its own family of dice: no line foresees a real fight."""
    state = _state(nodes=18, players=2, seat=1)
    clone = hu._clone(state)
    assert clone.dice_salt != state.dice_salt
    assert clone.dice_salt.startswith(state.dice_salt)
    assert clone.rng is not state.rng
    assert all(not p.is_human for p in clone.players.values())


def test_without_a_second_plan_the_look_ahead_is_plain_hunter(hu, monkeypatch):
    """With every posture the same as ours there is nothing to roll."""
    monkeypatch.setattr(hu, "LOOK_WEIGHTS", ())
    monkeypatch.setattr(hu, "LOOK_CAPTURE", False)
    state = _state(nodes=18, players=2, seat=1)
    for _ in range(15):
        engine.end_turn(state, decide=ai.decide)
    plain = _orders(hu, state, 1)
    monkeypatch.setattr(hu, "LOOKAHEAD", True)
    assert _orders(hu, state, 1) == plain
