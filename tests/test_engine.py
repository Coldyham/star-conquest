"""Tests for the turn engine: production cadence, fleet timing, combat, win check."""

from __future__ import annotations

import random
from contextlib import contextmanager

from starconquest import config, engine
from starconquest.model import GameState, Order, Player, System


@contextmanager
def no_jitter():
    old = config.COMBAT_JITTER
    config.COMBAT_JITTER = 0.0
    try:
        yield
    finally:
        config.COMBAT_JITTER = old


def make_state(specs, lanes, human=None):
    """specs: (id, owner, ships, production);  lanes: (a, b, travel_turns)."""
    s = GameState.new(0)
    for sid, owner, ships, prod in specs:
        s.systems[sid] = System(id=sid, pos=(sid * 10.0, 0.0), owner_id=owner,
                                ships=ships, production=prod)
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    for owner in {o for _, o, _, _ in specs if o != 0}:
        s.players[owner] = Player(owner, f"P{owner}", (0, 0, 0), is_human=(owner == human))
    for a, b, t in lanes:
        s.add_lane(a, b, float(t) * config.SHIP_LY_PER_TURN, t)
    s.rebuild_topology()
    return s


def test_production_cadence():
    # production=3 -> exactly one ship every three turns
    s = make_state([(0, 1, 0, 3), (1, 2, 5, 100)], [(0, 1, 5)])
    for _ in range(2):
        engine.end_turn(s)
    assert s.systems[0].ships == 0        # not yet
    engine.end_turn(s)
    assert s.systems[0].ships == 1        # after 3 turns
    for _ in range(3):
        engine.end_turn(s)
    assert s.systems[0].ships == 2        # after 6 turns


def test_fleet_arrives_after_exactly_travel_turns():
    # a second player keeps the game alive so the win check doesn't freeze it
    s = make_state([(0, 1, 10, 100), (1, 1, 0, 100), (2, 2, 5, 100)],
                   [(0, 1, 3), (1, 2, 5)])
    engine.apply_order(s, Order(1, 0, 1, 5))
    assert s.systems[0].ships == 5        # deducted at launch
    engine.end_turn(s)
    engine.end_turn(s)
    assert len(s.fleets) == 1 and s.systems[1].ships == 0   # still flying after 2
    engine.end_turn(s)
    assert len(s.fleets) == 0 and s.systems[1].ships == 5   # arrived on turn 3


@contextmanager
def speed_growth(pct_per_turn: float):
    old = config.SHIP_SPEED_GROWTH_PCT
    config.SHIP_SPEED_GROWTH_PCT = pct_per_turn
    try:
        yield
    finally:
        config.SHIP_SPEED_GROWTH_PCT = old


def test_speed_growth_shortens_later_launches():
    s = make_state([(0, 1, 40, 100), (1, 1, 0, 100), (2, 2, 5, 100)],
                   [(0, 1, 4), (1, 2, 5)])
    with speed_growth(1.0):
        early = engine.apply_order(s, Order(1, 0, 1, 5))
        assert early is not None and early.turns_total == 4   # turn 0: full price
        for _ in range(70):                                   # 1%/turn doubles by ~70
            engine.end_turn(s)
        late = engine.apply_order(s, Order(1, 0, 1, 5))
        assert late is not None and late.turns_total == 2     # same lane, twice the speed


def test_growth_never_re_times_a_fleet_in_flight():
    s = make_state([(0, 1, 40, 100), (1, 1, 0, 100), (2, 2, 5, 100)],
                   [(0, 1, 4), (1, 2, 5)])
    with speed_growth(40.0):   # by turn 2 a fresh launch would only take 2 turns
        engine.apply_order(s, Order(1, 0, 1, 5))
        for _ in range(3):
            engine.end_turn(s)
        assert len(s.fleets) == 1 and s.systems[1].ships == 0
        engine.end_turn(s)
        assert s.systems[1].ships == 5   # arrives on its launch-time schedule


def test_reinforcement_via_engine():
    s = make_state([(0, 1, 10, 100), (1, 1, 4, 100)], [(0, 1, 1)])
    engine.apply_order(s, Order(1, 0, 1, 6))
    engine.end_turn(s)
    assert s.systems[1].owner_id == 1 and s.systems[1].ships == 10


def test_attack_flips_ownership():
    s = make_state([(0, 1, 20, 100), (1, 2, 3, 100)], [(0, 1, 1)])
    with no_jitter():
        engine.apply_order(s, Order(1, 0, 1, 15))
        engine.end_turn(s)
    assert s.systems[1].owner_id == 1
    assert s.systems[1].ships == round((15 ** 2 - 3 ** 2) ** 0.5)


def test_win_check_declares_winner():
    s = make_state([(0, 1, 10, 100), (1, 2, 2, 100)], [(0, 1, 1)])
    with no_jitter():
        engine.apply_order(s, Order(1, 0, 1, 8))
        engine.end_turn(s)
    assert s.winner == 1
    assert s.players[2].alive is False


def test_end_turn_is_noop_after_win():
    s = make_state([(0, 1, 10, 100), (1, 2, 2, 100)], [(0, 1, 1)])
    with no_jitter():
        engine.apply_order(s, Order(1, 0, 1, 8))
        engine.end_turn(s)
    turn_at_win = s.turn
    engine.end_turn(s)
    assert s.turn == turn_at_win


def test_apply_order_validation():
    s = make_state([(0, 1, 10, 100), (1, 2, 5, 100), (2, 1, 4, 100)],
                   [(0, 1, 2), (1, 2, 2)])
    assert engine.apply_order(s, Order(1, 1, 0, 3)) is None    # foreign source
    assert engine.apply_order(s, Order(1, 0, 2, 3)) is None    # 0 and 2 not adjacent
    assert engine.apply_order(s, Order(1, 0, 1, 0)) is None    # zero ships
    fleet = engine.apply_order(s, Order(1, 0, 1, 999))         # clamps to available
    assert fleet is not None and fleet.ships == 10 and s.systems[0].ships == 0


def test_decide_callback_runs_for_ai_only():
    # player 1 human, player 2 AI. decide should only be called for player 2.
    s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 1)], human=1)
    called = []

    def decide(state, pid):
        called.append(pid)
        return []

    engine.end_turn(s, decide=decide)
    assert called == [2]


def test_a_seat_cannot_command_another_players_ships():
    """`apply_order` only checks the *declared* owner holds the source.

    So the rule that a seat commands its own ships and nothing else has to be
    enforced where orders are attributed to a seat. Without it a drop-in AI could
    launch a rival's fleet — or the human's — by naming them as the owner.
    """
    s = make_state([(0, 1, 10, 100), (1, 2, 10, 100), (2, 3, 10, 100)],
                   [(0, 1, 2), (1, 2, 2)], human=1)

    # Seat 2 tries to move seat 1's garrison (system 0) and seat 3's (system 2),
    # alongside a legitimate order of its own.
    def greedy(state, pid):
        if pid != 2:
            return []
        return [Order(1, 0, 1, 10),      # the human's ships
                Order(3, 2, 1, 10),      # another AI's ships
                Order(2, 1, 0, 4)]       # its own — must still go through

    engine.end_turn(s, decide=greedy)
    assert s.systems[0].ships == 10, "the human's garrison was launched"
    assert s.systems[2].ships == 10, "another seat's garrison was launched"
    assert s.systems[1].ships == 6, "seat 2's own order should still go through"
    assert [(f.owner_id, f.source_id) for f in s.fleets] == [(2, 1)]


def test_human_orders_are_confined_to_the_human_seat():
    """Under autoplay these come from `ai.decide`, so they need the same guard."""
    s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 2)], human=1)
    engine.end_turn(s, human_orders=[Order(2, 1, 0, 10), Order(1, 0, 1, 3)])
    assert s.systems[1].ships == 10, "an AI seat's garrison was launched"
    assert s.systems[0].ships == 7, "the human's own order should still go through"
    assert [(f.owner_id, f.source_id) for f in s.fleets] == [(1, 0)]


# --------------------------------------------------------------------------- #
# The turn record: what `replay` logs, and hands back to replay a turn
# --------------------------------------------------------------------------- #
def test_end_turn_returns_every_seats_orders_as_applied():
    s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 2)], human=1)
    record = engine.end_turn(s, human_orders=[Order(1, 0, 1, 3)],
                             decide=lambda st, pid: [Order(2, 1, 0, 4)])
    assert [(o.owner_id, o.source_id, o.ships) for o in record.orders] == [
        (1, 0, 3), (2, 1, 4)]


def test_a_scripted_turn_replays_without_asking_any_seat():
    """`replay.reconstruct` drives the engine this way: the orders go in verbatim
    and no bot is consulted, so a strategy that cannot repeat itself can't make
    the replay disagree with the game that was played."""
    def play(script=None, decide=None):
        s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 2)], human=1)
        return s, engine.end_turn(s, script=script, decide=decide)

    live, record = play(decide=lambda st, pid: [Order(2, 1, 0, 4)])
    replayed, _ = play(script=record, decide=_never_called)
    assert [(f.owner_id, f.source_id, f.ships) for f in replayed.fleets] == \
           [(f.owner_id, f.source_id, f.ships) for f in live.fleets]
    assert {sid: sys.ships for sid, sys in replayed.systems.items()} == \
           {sid: sys.ships for sid, sys in live.systems.items()}


def test_a_scripted_turn_refights_the_battle_on_the_recorded_dice():
    """Combat's swing is drawn from `state.rng`, which a replay leaves in a place
    it never was live (nobody decided anything on the way there). So the recorded
    draws are dealt back in its place, and they — not this run's rng — settle the
    fight: the same 10-vs-10 attack goes either way on the dice it is given."""
    def fight(dice):
        s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 1)], human=1)
        s.rng = random.Random(7)      # would roll its own swing, if it were asked
        engine.end_turn(s, script=engine.TurnRecord([Order(1, 0, 1, 10)], dice))
        return s.systems[1].owner_id

    # The draws are dealt in the order the fight asks for them: the side holding
    # the node first (it is folded in as the incumbent), then the attacker.
    assert fight([+0.9, -0.9]) == 2, "the dice favoured the defender; it should hold"
    assert fight([-0.9, +0.9]) == 1, "and favouring the attacker should flip it"


def test_end_turn_records_the_draws_a_live_fight_made():
    s = make_state([(0, 1, 10, 100), (1, 2, 10, 100)], [(0, 1, 1)], human=1)
    s.rng = random.Random(7)
    record = engine.end_turn(s, human_orders=[Order(1, 0, 1, 10)])
    assert len(record.dice) == 2      # one swing per side of the one fight


def _never_called(state, pid):
    raise AssertionError("a replayed turn must not consult a seat")
