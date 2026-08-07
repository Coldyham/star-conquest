"""Tests for the turn engine: production cadence, fleet timing, combat, win check."""

from __future__ import annotations

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
        s.add_lane(a, b, float(t), t)
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
def speed_growth(per_turn: float):
    old = config.SHIP_SPEED_GROWTH
    config.SHIP_SPEED_GROWTH = per_turn
    try:
        yield
    finally:
        config.SHIP_SPEED_GROWTH = old


def test_speed_growth_shortens_later_launches():
    s = make_state([(0, 1, 40, 100), (1, 1, 0, 100), (2, 2, 5, 100)],
                   [(0, 1, 4), (1, 2, 5)])
    with speed_growth(0.06):
        early = engine.apply_order(s, Order(1, 0, 1, 5))
        assert early is not None and early.turns_total == 4   # turn 0: full price
        for _ in range(100):
            engine.end_turn(s)
        late = engine.apply_order(s, Order(1, 0, 1, 5))
        assert late is not None and late.turns_total == 2     # same lane, twice the speed


def test_growth_never_re_times_a_fleet_in_flight():
    s = make_state([(0, 1, 40, 100), (1, 1, 0, 100), (2, 2, 5, 100)],
                   [(0, 1, 4), (1, 2, 5)])
    with speed_growth(3.0):   # by turn 2 a fresh launch would only take 2 turns
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
