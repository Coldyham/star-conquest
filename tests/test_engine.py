"""Tests for the turn engine: production cadence, fleet timing, combat, win check."""

from __future__ import annotations

import random
from contextlib import contextmanager

import pytest

from starconquest import config, engine
from starconquest.model import Fleet, GameState, Order, Player, System


@contextmanager
def no_jitter():
    old = config.COMBAT_JITTER
    config.COMBAT_JITTER = 0.0
    try:
        yield
    finally:
        config.COMBAT_JITTER = old


@contextmanager
def in_lane_battles():
    old = config.IN_LANE_BATTLES
    config.IN_LANE_BATTLES = True
    try:
        yield
    finally:
        config.IN_LANE_BATTLES = old


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


def test_a_hull_finished_this_turn_defends_its_own_system():
    """Production resolves before arrivals, so a ship completing on the turn its
    system is attacked is in the garrison for the fight. Sized against the garrison
    the attacker can see rather than the one it meets: 4 v 3 takes the system, 4 v 4
    annihilates to neutral."""
    with no_jitter():
        s = make_state([(0, 1, 4, 100), (1, 2, 3, 2)], [(0, 1, 1)])
        s.systems[1].prod_progress = 1        # one turn from its next ship
        engine.apply_order(s, Order(1, 0, 1, 4))
        engine.end_turn(s)
        assert (s.systems[1].owner_id, s.systems[1].ships) == (0, 0)


def test_a_system_that_falls_this_turn_accrues_from_the_next_one():
    """The flip side of the phase order: capture resets the progress bar, and the
    turn's accrual has already happened, so a captor starts from zero."""
    with no_jitter():
        s = make_state([(0, 1, 10, 100), (1, 2, 1, 2)], [(0, 1, 1)])
        s.systems[1].prod_progress = 1
        engine.apply_order(s, Order(1, 0, 1, 10))
        engine.end_turn(s)
        assert s.systems[1].owner_id == 1     # the extra hull wasn't enough
        assert s.systems[1].prod_progress == 0


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


def _stage(state, owner, src, dst, ships, total, rem_after):
    """Put a fleet on a lane positioned so that, once ``end_turn`` advances it, it
    has ``rem_after`` turns left of a ``total``-turn crossing.

    Lane battles are decided by where fleets actually are, so these tests need to
    place them rather than launch them — and different ``total``s on one lane is
    exactly what ship-speed growth produces in a real game.
    """
    state.fleets.append(Fleet(owner_id=owner, source_id=src, dest_id=dst,
                              ships=ships, turns_total=total,
                              turns_remaining=rem_after + 1))
    return state.fleets[-1]


def test_lane_battle_waits_until_the_paths_actually_cross():
    """Sharing a lane is not enough. Head-on on a 3-turn lane, the fleets are
    still approaching after one turn and only meet in the middle of the second."""
    s = make_state([(0, 1, 10, 100), (1, 2, 6, 100)], [(0, 1, 3)])
    with no_jitter(), in_lane_battles():
        engine.apply_order(s, Order(1, 0, 1, 10))   # 0 -> 1
        engine.apply_order(s, Order(2, 1, 0, 6))    # 1 -> 0 (head-on)
        engine.end_turn(s)
        assert len(s.fleets) == 2                   # 1/3 vs 2/3 along: no contact
        engine.end_turn(s)                          # they cross mid-lane
    assert len(s.fleets) == 1


def test_lane_battle_winner_keeps_its_own_heading_and_schedule():
    """The survivor is thinned where it stands — never merged into another fleet,
    never moved to the meeting point, never re-timed."""
    s = make_state([(0, 1, 10, 100), (1, 2, 6, 100)], [(0, 1, 3)])
    with no_jitter(), in_lane_battles():
        engine.apply_order(s, Order(1, 0, 1, 10))
        engine.apply_order(s, Order(2, 1, 0, 6))
        engine.end_turn(s)
        engine.end_turn(s)
    survivor = s.fleets[0]
    assert survivor.owner_id == 1
    assert survivor.dest_id == 1                    # kept its original heading
    assert survivor.ships == round((10 ** 2 - 6 ** 2) ** 0.5)   # Lanchester: 8
    assert survivor.turns_total == 3                # ...and its own speed
    assert survivor.turns_remaining == 1            # advanced twice, not reset


def test_lane_battle_charges_both_sides_ships_lost():
    """Ships killed in open space count toward the challenge tie-break, exactly as
    ships killed at a system do — the winner's sub-1:1 losses included."""
    s = make_state([(0, 1, 10, 100), (1, 2, 6, 100)], [(0, 1, 3)])
    with no_jitter(), in_lane_battles():
        engine.apply_order(s, Order(1, 0, 1, 10))
        engine.apply_order(s, Order(2, 1, 0, 6))
        engine.end_turn(s)
        engine.end_turn(s)
    assert s.players[1].ships_lost == 10 - s.fleets[0].ships   # kept 8, so lost 2
    assert s.players[2].ships_lost == 6                        # wiped out entirely


def test_lane_annihilation_charges_everyone_in_full():
    # matched forces meeting mid-lane: nobody holds open space, so the tie breaks
    # to no one and both fleets are spent.
    s = make_state([(0, 1, 8, 100), (1, 2, 8, 100)], [(0, 1, 3)])
    with no_jitter(), in_lane_battles():
        engine.apply_order(s, Order(1, 0, 1, 8))
        engine.apply_order(s, Order(2, 1, 0, 8))
        engine.end_turn(s)
        engine.end_turn(s)
    assert s.fleets == []
    assert s.players[1].ships_lost == 8
    assert s.players[2].ships_lost == 8


def test_fleets_that_never_meet_never_fight():
    """Same heading at the same speed: the gap between them never closes, so they
    ride the whole lane together without a shot, however long the game runs."""
    s = make_state([(0, 1, 1, 100), (1, 0, 0, 100)], [(0, 1, 6)])
    with no_jitter(), in_lane_battles():
        _stage(s, 1, 0, 1, 5, 6, 3)
        _stage(s, 2, 0, 1, 5, 6, 2)   # one step behind, identical speed
        engine.end_turn(s)
        engine.end_turn(s)
    assert len(s.fleets) == 2


def test_a_strong_fleet_beats_a_defended_lane_one_fleet_at_a_time():
    """The whole point of pairwise, crossing-ordered fights.

    An 8-ship fleet sweeps past three 4-ship fleets in one turn. It meets them
    nearest-first and carries its losses into each next fight — 8 beats 4, then 7
    beats 4, then 6 beats 4 — and comes out alive. Pooled into one 12-ship force
    at a point none of them occupied, it would have lost instead.
    """
    s = make_state([(0, 1, 1, 100), (1, 2, 1, 100)], [(0, 1, 10)])
    with no_jitter(), in_lane_battles():
        strong = _stage(s, 1, 0, 1, 8, 2, 1)      # sweeps 0.0 -> 0.5 of the lane
        for rem in (2, 3, 4):                     # strung out at 0.2, 0.3, 0.4
            _stage(s, 2, 1, 0, 4, 10, rem)
        engine.end_turn(s)
    assert s.fleets == [strong]
    # 8 v 4 -> 7, 7 v 4 -> 6, 6 v 4 -> 4
    assert strong.ships == 4
    assert s.players[1].ships_lost == 4
    assert s.players[2].ships_lost == 12


def test_fleets_at_different_speeds_meet_when_they_pass():
    """A 6-turn crossing and a 5-turn one, launched under different ship speeds,
    never share a position — they pass through each other mid-step, and that is
    the turn they fight."""
    s = make_state([(0, 1, 1, 100), (1, 2, 1, 100)], [(0, 1, 6)])
    with no_jitter(), in_lane_battles():
        _stage(s, 1, 0, 1, 9, 6, 3)   # progress 2/6 -> 3/6
        _stage(s, 2, 1, 0, 5, 5, 2)   # progress 2/5 -> 3/5, the other way
        engine.end_turn(s)
    assert len(s.fleets) == 1
    assert s.fleets[0].owner_id == 1
    assert s.fleets[0].turns_total == 6   # untouched by the fleet it destroyed


def test_same_owner_fleets_are_never_pooled():
    """Only the fleet that is actually met does the fighting.

    Two 5-ship fleets share the lane, but the enemy's 6 crosses just one of them.
    Pooled they would be 10 and would win; met singly, 5 loses and the enemy sails
    on with 3 — while the fleet it never reached is untouched.
    """
    s = make_state([(0, 1, 1, 100), (1, 2, 1, 100)], [(0, 1, 8)])
    with no_jitter(), in_lane_battles():
        met = _stage(s, 1, 0, 1, 5, 8, 4)      # mid-lane, meets the enemy
        astern = _stage(s, 1, 0, 1, 5, 8, 7)   # far behind, never gets there
        _stage(s, 2, 1, 0, 6, 8, 4)            # crosses `met` and nothing else
        engine.end_turn(s)
    owners = [(f.owner_id, f.ships) for f in s.fleets]
    assert met not in s.fleets
    assert astern in s.fleets and astern.ships == 5   # never engaged
    assert (2, 3) in owners      # 6 beat 5 (sqrt(36-25) -> 3), not 6 against 10
    assert s.players[1].ships_lost == 5
    assert s.players[2].ships_lost == 3


def test_lane_battles_off_by_default_fleets_coexist():
    # with the toggle off, opposing fleets pass each other untouched (default).
    s = make_state([(0, 1, 10, 100), (1, 2, 6, 100)], [(0, 1, 3)])
    with no_jitter():
        engine.apply_order(s, Order(1, 0, 1, 10))
        engine.apply_order(s, Order(2, 1, 0, 6))
        engine.end_turn(s)
        engine.end_turn(s)
    assert len(s.fleets) == 2


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


def test_progress_at_is_the_one_formula():
    """``Fleet.progress_at`` is what both the map and a lane battle measure with,
    so its two ends must be the positions the engine used to compute by hand."""
    f = Fleet(owner_id=1, source_id=0, dest_id=1, ships=3,
              turns_total=8, turns_remaining=3)
    assert f.progress_at(1.0) == f.progress()
    assert f.progress_at(0.0) == 1.0 - (f.turns_remaining + 1) / f.turns_total
    assert f.progress_at(0.5) == 1.0 - (f.turns_remaining + 0.5) / f.turns_total
    # a fleet that launched this turn starts on its source and ends one step along
    fresh = Fleet(owner_id=1, source_id=0, dest_id=1, ships=3,
                  turns_total=4, turns_remaining=3)
    assert fresh.progress_at(0.0) == 0.0
    # degenerate schedules stay pinned at the destination, unmirrored
    stuck = Fleet(owner_id=1, source_id=1, dest_id=0, ships=1,
                  turns_total=0, turns_remaining=0)
    assert stuck.progress_at(0.0) == stuck.progress_at(1.0) == 1.0
    assert engine._lane_span(stuck, 0) == (1.0, 1.0)


def test_lane_span_measures_both_headings_from_one_end():
    """The span is `progress_at`'s two ends, mirrored for a fleet running the
    other way so two opposing fleets sit on a single ruler."""
    down = Fleet(owner_id=1, source_id=0, dest_id=1, ships=1,
                 turns_total=10, turns_remaining=4)
    up = Fleet(owner_id=2, source_id=1, dest_id=0, ships=1,
               turns_total=10, turns_remaining=4)
    assert engine._lane_span(down, 0) == (0.5, 0.6)
    assert engine._lane_span(up, 0) == (0.5, 0.4)


def test_a_crossing_is_recorded_where_both_fleets_are():
    """`at` is the point the two fleets share at `when`, so a fight can be shown
    exactly where the triangles are seen to touch."""
    s = make_state([(0, 1, 1, 100), (1, 2, 1, 100)], [(0, 1, 10)])
    a = _stage(s, 1, 0, 1, 5, 10, 5)   # 0.4 -> 0.5 of the lane
    b = _stage(s, 2, 1, 0, 5, 10, 4)   # 0.5 -> 0.4, the other way
    engine._advance_fleets(s)          # crossings are measured after the step
    crossings = engine._lane_crossings(s, 0, [0, 1])
    assert len(crossings) == 1
    cross = crossings[0]
    assert cross.when == 0.5
    assert cross.at == pytest.approx(a.progress_at(cross.when))
    # ...and the same point measured off the fleet running the other way
    assert cross.at == pytest.approx(1.0 - b.progress_at(cross.when))


def test_simultaneous_crossings_still_break_on_launch_order():
    """Two disjoint pairs meeting at the same instant, further along the lane
    first. Order decides which fight is dealt the turn's dice first, so it must
    stay fleet order — order of launch — and never where the fleets met.
    """
    s = make_state([(0, 1, 1, 100), (1, 2, 1, 100)], [(0, 1, 10)])
    _stage(s, 1, 0, 1, 5, 10, 1)   # pair one, meeting at 0.85
    _stage(s, 2, 1, 0, 5, 10, 8)
    _stage(s, 1, 0, 1, 5, 10, 5)   # pair two, meeting at 0.45
    _stage(s, 2, 1, 0, 5, 10, 4)
    engine._advance_fleets(s)
    crossings = engine._lane_crossings(s, 0, [0, 1, 2, 3])
    assert [(c.a, c.b) for c in crossings] == [(0, 1), (2, 3)]
    # ...and the test really is exercising the hazard: sorting on where they met
    # would have swapped those two rows.
    assert [c.when for c in crossings] == [0.5, 0.5]
    assert [round(c.at, 10) for c in crossings] == [0.85, 0.45]


# --------------------------------------------------------------------------- #
# Lane tracks (cosmetic, but they have to hold still)
# --------------------------------------------------------------------------- #


def _slots(state):
    return [f.lane_slot for f in state.fleets]


def test_a_fleet_is_given_the_lowest_free_track_on_its_lane():
    """Tracks run outward from the centre line, so the first fleet onto an empty
    lane flies straight down it and later ones flank it."""
    s = make_state([(0, 1, 30, 100), (1, 2, 1, 100)], [(0, 1, 5)])
    for _ in range(5):
        engine.apply_order(s, Order(1, 0, 1, 2))
    assert _slots(s) == [0, -1, 1, -2, 2]


def test_launching_does_not_shove_the_fleets_already_flying():
    """Spreading a lane's fleets symmetrically across however many are currently
    on it made every one of them step sideways whenever another set off."""
    s = make_state([(0, 1, 30, 100), (1, 2, 1, 100)], [(0, 1, 5)])
    engine.apply_order(s, Order(1, 0, 1, 3))
    before = _slots(s)
    engine.apply_order(s, Order(1, 0, 1, 3))
    engine.apply_order(s, Order(1, 0, 1, 3))
    assert _slots(s)[:1] == before == [0]


def test_a_fleet_arriving_does_not_shove_the_ones_behind_it():
    """The case that showed up in play: three fleets strung down one lane, the
    leader lands, and the two behind it must not jump sideways as the rank they
    were drawn from re-packs. A track is held, not recomputed.

    Launched a turn apart, since fleets that set off together share a speed and so
    land together — the string down the lane is the point.
    """
    # a third system, held by the rival, purely so the win check doesn't end the
    # match the moment player 1 is the last one standing
    s = make_state([(0, 1, 30, 100), (1, 0, 1, 100), (2, 2, 5, 100)],
                   [(0, 1, 3), (1, 2, 5)])
    with no_jitter():
        engine.apply_order(s, Order(1, 0, 1, 9))   # launched first, so lands first
        engine.end_turn(s)
        engine.apply_order(s, Order(1, 0, 1, 1))
        engine.end_turn(s)
        engine.apply_order(s, Order(1, 0, 1, 1))
        assert _slots(s) == [0, -1, 1]
        engine.end_turn(s)                         # ...on which the leader lands

    assert [f.ships for f in s.fleets] == [1, 1], "only the leader should have landed"
    assert s.systems[1].owner_id == 1, "...and it took the system"
    assert _slots(s) == [-1, 1], "the fleets behind it held their tracks"

    # ...and the track the leader vacated is what the next launch gets, so no two
    # fleets on a lane are ever drawn on top of each other
    engine.apply_order(s, Order(1, 0, 1, 1))
    assert _slots(s) == [-1, 1, 0]


def test_fleets_running_opposite_ways_get_their_own_tracks():
    """The reason tracks exist at all: two fleets can be at the same point on one
    lane heading opposite ways, so both directions draw from one pool."""
    s = make_state([(0, 1, 20, 100), (1, 2, 20, 100)], [(0, 1, 5)])
    engine.apply_order(s, Order(1, 0, 1, 4))
    engine.apply_order(s, Order(2, 1, 0, 4))
    assert len(set(_slots(s))) == 2, "one lane, both directions: they must not overlap"


def test_a_track_is_only_shared_with_a_different_lane():
    """Tracks are per lane — two fleets on unrelated lanes both fly down the
    middle, which is what makes the common case look right."""
    s = make_state([(0, 1, 20, 100), (1, 0, 1, 100), (2, 0, 1, 100)],
                   [(0, 1, 5), (0, 2, 5)])
    engine.apply_order(s, Order(1, 0, 1, 4))
    engine.apply_order(s, Order(1, 0, 2, 4))
    assert _slots(s) == [0, 0]
