"""Tests for Lanchester-with-jitter combat and arrival resolution."""

from __future__ import annotations

import random
from contextlib import contextmanager

import pytest

from starconquest import combat, config
from starconquest.model import Fleet, GameState, Player, System


@contextmanager
def jitter(value: float):
    """Temporarily override the combat jitter (e.g. 0 for deterministic maths)."""
    old = config.COMBAT_JITTER
    config.COMBAT_JITTER = value
    try:
        yield
    finally:
        config.COMBAT_JITTER = old


@contextmanager
def advantage(value: float):
    """Temporarily override the defender advantage (mirrors ``jitter`` above)."""
    old = config.DEFENDER_ADVANTAGE
    config.DEFENDER_ADVANTAGE = value
    try:
        yield
    finally:
        config.DEFENDER_ADVANTAGE = old


def test_square_law_deterministic():
    rng = random.Random(0)
    with jitter(0.0):
        winner, survivors = combat.resolve_fight(rng, 1, 10, 2, 6)
    assert winner == 1
    assert survivors == 8  # sqrt(100 - 36)


def test_equal_forces_annihilate():
    rng = random.Random(0)
    with jitter(0.0):
        winner, survivors = combat.resolve_fight(rng, 1, 5, 2, 5, defender_owner=2)
    assert (winner, survivors) == (0, 0)  # mutual annihilation -> neutral


def test_larger_force_wins_strong_majority():
    rng = random.Random(123)
    wins = sum(combat.resolve_fight(rng, 1, 12, 2, 8)[0] == 1 for _ in range(400))
    assert wins > 380  # 12 vs 8 with +/-10% jitter should almost always win


def test_never_creates_ships():
    rng = random.Random(7)
    for _ in range(2000):
        a, b = rng.randint(1, 40), rng.randint(1, 40)
        winner, survivors = combat.resolve_fight(rng, 1, a, 2, b)
        winner_input = a if winner == 1 else (b if winner == 2 else max(a, b))
        assert survivors <= winner_input


def test_deterministic_from_seed():
    r1, r2 = random.Random(99), random.Random(99)
    for _ in range(50):
        assert combat.resolve_fight(r1, 1, 20, 2, 15) == combat.resolve_fight(r2, 1, 20, 2, 15)


# --- resolve_arrival --------------------------------------------------------- #
def _state():
    s = GameState.new(0)
    s.players = {}  # not needed for these unit tests
    return s


def test_reinforcement_sums():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=1, ships=4)
    combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 5, 2, 0), Fleet(1, 8, 0, 3, 2, 0)])
    assert s.systems[0].owner_id == 1
    assert s.systems[0].ships == 12  # 4 + 5 + 3, no fight


def test_attack_flips_ownership():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=2, ships=3)
    with jitter(0.0):
        combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 10, 2, 0)])
    assert s.systems[0].owner_id == 1
    assert s.systems[0].ships == round((100 - 9) ** 0.5)  # sqrt(10^2 - 3^2)


def test_capture_empty_neutral():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=0, ships=0)
    combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 6, 2, 0)])
    assert s.systems[0].owner_id == 1 and s.systems[0].ships == 6


def test_capture_resets_production_progress():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=2, ships=2, prod_progress=1)
    with jitter(0.0):
        combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 10, 2, 0)])
    assert s.systems[0].owner_id == 1
    assert s.systems[0].prod_progress == 0


# --- attrition (Player.ships_lost) ------------------------------------------- #
# The other resolve_arrival tests leave `players` empty, so the counter is skipped
# there by design; these populate it. Losses are per owner: everyone brings
# garrison + arrivals, only the final owner keeps anything.
def _peopled_state(*pids: int):
    s = GameState.new(0)
    s.players = {pid: Player(id=pid, name=f"P{pid}", color=(0, 0, 0)) for pid in pids}
    return s


def test_reinforcement_loses_nothing():
    s = _peopled_state(1)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=1, ships=4)
    combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 5, 2, 0)])
    assert s.players[1].ships_lost == 0


def test_attack_debits_both_sides():
    s = _peopled_state(1, 2)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=2, ships=3)
    with jitter(0.0):
        combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 10, 2, 0)])
    survivors = s.systems[0].ships
    assert s.players[1].ships_lost == 10 - survivors   # winner's sub-1:1 losses
    assert s.players[2].ships_lost == 3                # defender lost the lot


def test_losses_accumulate_across_battles():
    s = _peopled_state(1, 2)
    for node in (0, 1):
        s.systems[node] = System(id=node, pos=(0.0, 0.0), owner_id=2, ships=3)
        with jitter(0.0):
            combat.resolve_arrival(s, node, [Fleet(1, 9, node, 10, 2, 0)])
    assert s.players[2].ships_lost == 6   # 3 at each system, all match long


def test_pileup_conserves_ships():
    """Every ship brought is either a survivor or a loss — nothing invented."""
    s = _peopled_state(1, 2)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=2, ships=4)
    fleets = [Fleet(1, 9, 0, 12, 2, 0), Fleet(2, 8, 0, 9, 2, 0)]
    brought = 4 + 12 + 9
    combat.resolve_arrival(s, 0, fleets)
    lost = sum(p.ships_lost for p in s.players.values())
    assert lost + s.systems[0].ships == brought


def test_capturing_an_empty_neutral_costs_nothing():
    s = _peopled_state(1)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=0, ships=0)
    combat.resolve_arrival(s, 0, [Fleet(1, 9, 0, 6, 2, 0)])
    assert s.players[1].ships_lost == 0


def test_three_way_pileup_single_winner():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=0, ships=4)  # neutral defender
    fleets = [Fleet(1, 9, 0, 12, 2, 0), Fleet(2, 8, 0, 9, 2, 0)]
    owner, ships = combat.resolve_arrival(s, 0, fleets)
    assert owner in (0, 1, 2)
    assert ships >= 0
    assert s.systems[0].owner_id == owner and s.systems[0].ships == ships


# --- preview_fight (the menu's Combat page) ---------------------------------- #
# The preview exists to teach the rules, so it has to *be* the rules. These pin it
# to `resolve_fight` rather than to a restatement of the square law.
_ADVANTAGES = (0.75, 1.0, 1.25, 1.5, 2.0)


def _real_fight(rng, a: int, d: int) -> tuple[int, int]:
    """``resolve_fight`` posed as the preview's attacker-vs-defender question."""
    return combat.resolve_fight(rng, combat.ATTACKER, a, combat.DEFENDER, d, defender_owner=combat.DEFENDER)


def test_preview_nominal_matches_resolve_fight_at_zero_jitter():
    """The anti-drift test, and the reason the preview lives in this module: with
    the dice pinned to zero the engine and the preview must agree exactly."""
    rng = random.Random(0)
    for adv in _ADVANTAGES:
        with jitter(0.0), advantage(adv):
            for a in range(0, 31):
                for d in range(0, 31):
                    nominal = combat.preview_fight(a, d, 0.0, adv).nominal
                    assert (nominal.winner, nominal.survivors) == _real_fight(rng, a, d), (a, d, adv)


def test_preview_ignores_config_globals():
    """Jitter and advantage are parameters, never config reads — the menu previews
    what the player is dragging now, not the previous game's balance."""
    with jitter(0.5), advantage(2.0):
        p = combat.preview_fight(10, 6, 0.0, 1.0)
    for roll in (p.nominal, p.best, p.worst):
        assert (roll.winner, roll.survivors) == (combat.ATTACKER, 8)


def test_preview_draws_no_randomness():
    """No rng, so a preview can never perturb a seeded replay and `menu.draw`
    stays a pure read."""
    rng = random.Random(1234)
    before = rng.getstate()
    for a in range(1, 20):
        combat.preview_fight(a, 12, 0.1, 1.0)
    assert rng.getstate() == before


def test_resolve_fight_draws_exactly_two_uniforms():
    """Guards the shared-helper refactor: the draw count and order must not move,
    or every seeded replay changes."""
    driven, manual = random.Random(5), random.Random(5)
    for _ in range(10):
        _real_fight(driven, 12, 8)
        manual.uniform(-config.COMBAT_JITTER, config.COMBAT_JITTER)
        manual.uniform(-config.COMBAT_JITTER, config.COMBAT_JITTER)
    assert driven.random() == manual.random()


def test_preview_band_brackets_real_fights():
    """best/worst are the corners of the jitter square, not samples — so every
    real roll has to land inside them, or the band on screen is a lie."""
    rng = random.Random(7)
    for j in (0.02, 0.1, 0.3, 0.5):
        for adv in (0.75, 1.0, 1.3, 2.0):
            with jitter(j), advantage(adv):
                for a, d in ((10, 6), (12, 8), (20, 12), (7, 7), (30, 10), (4, 9)):
                    p = combat.preview_fight(a, d, j, adv)
                    lo, hi = p.band
                    for _ in range(60):
                        winner, ships = _real_fight(rng, a, d)
                        got = ships if winner == combat.ATTACKER else 0
                        assert lo <= got <= hi, (a, d, j, adv, got, p.band)


def test_preview_monotone_in_attackers():
    """More ships never means fewer survivors, on any corner."""
    for j in (0.0, 0.1, 0.25, 0.5):
        for adv in (0.75, 1.0, 1.5, 2.0):
            for d in (0, 5, 12, 30, 50):
                previous = [0, 0, 0]
                for a in range(0, 51):
                    p = combat.preview_fight(a, d, j, adv)
                    for i, roll in enumerate((p.nominal, p.best, p.worst)):
                        assert roll.attacker_survivors >= previous[i], (a, d, j, adv, i)
                        previous[i] = roll.attacker_survivors


def test_preview_never_creates_ships():
    """The cap at the winner's actual fleet, which bites in ordinary play: 50 v 1
    computes sqrt(2499) = 49.99 and 30 v 10's best roll computes sqrt(1008) = 31.75."""
    assert combat.preview_fight(50, 1, 0.1, 1.0).nominal.survivors == 50
    assert combat.preview_fight(30, 10, 0.1, 1.0).best.survivors == 30
    for adv in _ADVANTAGES:
        for a in range(0, 51):
            for d in (0, 3, 17, 50):
                p = combat.preview_fight(a, d, 0.3, adv)
                for roll in (p.nominal, p.best, p.worst):
                    actual = a if roll.winner == combat.ATTACKER else d
                    assert roll.survivors <= actual, (a, d, adv)


def test_matched_forces_go_neutral_but_jitter_decides():
    """The hardest rule to guess from the map: evenly matched fleets annihilate on
    the average roll, yet either side can walk away with ships on a swing."""
    for n in (5, 10):
        exact = combat.preview_fight(n, n, 0.0, 1.0)
        assert exact.nominal.winner == combat.NEUTRAL and exact.nominal.survivors == 0
        assert exact.annihilation and exact.certain

        swung = combat.preview_fight(n, n, 0.1, 1.0)
        assert swung.annihilation and not swung.certain
        assert swung.best.winner == combat.ATTACKER and swung.worst.winner == combat.DEFENDER


def test_defender_advantage_flips_a_comfortable_win():
    """The knob's whole point, and what the page is there to show: 10 v 6 is an
    8-ship win at 1.0 and a loss at 2.0."""
    assert combat.preview_fight(10, 6, 0.1, 1.0).nominal.winner == combat.ATTACKER
    assert combat.preview_fight(10, 6, 0.1, 2.0).nominal.winner == combat.DEFENDER


def test_naive_is_the_teaching_contrast():
    p = combat.preview_fight(20, 12, 0.1, 1.0)
    assert p.naive == 8 and p.nominal.survivors == 16  # sqrt(400 - 144), not 20 - 12


def test_roll_reproduces_the_named_corners():
    """`nominal`/`best`/`worst` are just named points in the jitter square, so a
    caller sampling the whole square (the menu's matrix) gets the same maths."""
    for a, d, j, adv in ((20, 12, 0.1, 1.0), (5, 5, 0.5, 1.0), (8, 20, 0.2, 1.5)):
        p = combat.preview_fight(a, d, j, adv)
        assert p.roll(0.0, 0.0) == p.nominal
        assert p.roll(1.0, -1.0) == p.best
        assert p.roll(-1.0, 1.0) == p.worst


def test_roll_is_bounded_by_the_corners_everywhere_inside():
    """Nothing in the interior can beat the corners — the property that lets the
    band be printed as a range and the matrix be read as a gradient."""
    steps = [i / 4 - 1.0 for i in range(9)]  # -1.0 .. +1.0
    for a, d, j, adv in ((20, 12, 0.1, 1.0), (11, 10, 0.3, 1.0), (30, 40, 0.5, 0.75)):
        p = combat.preview_fight(a, d, j, adv)
        lo, hi = p.band
        for sa in steps:
            for sd in steps:
                assert lo <= p.roll(sa, sd).attacker_survivors <= hi, (a, d, j, adv, sa, sd)


def test_roll_draws_no_randomness():
    rng = random.Random(99)
    before = rng.getstate()
    p = combat.preview_fight(12, 10, 0.1, 1.0)
    for sa in (-1.0, 0.0, 1.0):
        for sd in (-1.0, 0.0, 1.0):
            p.roll(sa, sd)
    assert rng.getstate() == before


# --------------------------------------------------------------------------- #
# Break-even margins
#
# These are what every bot sizes a fleet against, so they are checked against an
# actual fight rather than against their own arithmetic: clearing the multiple
# has to win the *worst* corner of the jitter square, which is the only thing
# the callers are relying on.
# --------------------------------------------------------------------------- #
def test_edges_are_symmetric_without_a_defender_bonus():
    with advantage(1.0), jitter(0.1):
        assert combat.edge_attacking() == combat.edge_defending()
        assert combat.edge_attacking() == 1.1 / 0.9


def test_the_advantage_moves_the_two_edges_oppositely():
    """It scales whoever holds the system, so it prices attack and defence apart.

    A bot applying it one-directionally would over-defend as the slider rose and
    throw armies at systems it could no longer take.
    """
    with jitter(0.1):
        swing = 1.1 / 0.9
        with advantage(1.5):
            assert combat.edge_attacking() == swing * 1.5
            assert combat.edge_defending() == swing / 1.5
            assert combat.edge_attacking() > combat.edge_defending()
        with advantage(0.5):
            assert combat.edge_attacking() < combat.edge_defending()


def test_zero_jitter_and_zero_advantage_stay_finite():
    """Both knobs reach their ends on the menu; neither may blow up a caller."""
    with jitter(0.0), advantage(1.0):
        assert combat.edge_attacking() == 1.0
    with jitter(1.0), advantage(0.0):
        assert combat.edge_attacking() > 0
        assert 0 < combat.edge_defending() < 1e6


def test_clearing_the_attack_edge_beats_the_defender_on_the_worst_roll():
    """The property every bot's margin depends on, checked against a real fight.

    Note what is guaranteed: the *defender* cannot hold. Not that we capture —
    see the annihilation floor below.
    """
    for j in (0.0, 0.1, 0.25):
        for adv in (0.75, 1.0, 1.25, 1.5):
            with jitter(j), advantage(adv):
                need = combat.edge_attacking()
            for defender in (1, 3, 10, 40):
                attacker = int(defender * need) + 1
                p = combat.preview_fight(attacker, defender, j, adv)
                assert p.worst.winner != combat.DEFENDER, (j, adv, defender, attacker)


def test_the_attack_edge_alone_does_not_guarantee_a_capture():
    """Clearing it can still annihilate both sides and vacate the system.

    Survivors are ``sqrt(W^2 - L^2)`` on the *effective* strengths and round to
    zero when the two are close, which at small garrisons a bare edge-clearing
    attack always is: 1 ship into 1 at advantage 0.75 fights 0.90 against 0.825
    and leaves nobody. This is exactly why `_required` in the bots floors at
    ``target.ships + 1`` rather than trusting the multiple.
    """
    p = combat.preview_fight(1, 1, 0.1, 0.75)
    assert p.worst.winner == combat.NEUTRAL
    assert p.worst.attacker_survivors == 0

    # With real mass behind it the same multiple does take the system.
    for defender in (10, 40):
        with jitter(0.1), advantage(1.0):
            attacker = int(defender * combat.edge_attacking()) + 1
        q = combat.preview_fight(attacker, defender, 0.1, 1.0)
        assert q.worst.winner == combat.ATTACKER
        assert q.certain


def test_falling_short_of_the_attack_edge_is_not_certain():
    """...and the margin is tight: one ship under, the worst roll no longer wins."""
    for j in (0.1, 0.25):
        for adv in (1.0, 1.5):
            with jitter(j), advantage(adv):
                need = combat.edge_attacking()
            for defender in (10, 40):
                attacker = int(defender * need)      # one short of the strict clear
                p = combat.preview_fight(attacker, defender, j, adv)
                assert p.worst.winner != combat.ATTACKER, (j, adv, defender, attacker)


def test_clearing_the_defence_edge_holds_the_worst_roll():
    for j in (0.0, 0.1, 0.25):
        for adv in (1.0, 1.5):
            with jitter(j), advantage(adv):
                need = combat.edge_defending()
            for attacker in (3, 10, 40):
                garrison = int(attacker * need) + 1
                p = combat.preview_fight(attacker, garrison, j, adv)
                assert p.worst.winner == combat.DEFENDER, (j, adv, attacker, garrison)


def test_min_swing_floors_the_jitter_half_only():
    """A margin fitted at one jitter must not be thinned by a gentler one.

    The floor is on the swing, never on the advantage — otherwise a bot keeping
    its tuned cushion would stop getting the full benefit of holding ground.
    """
    tuned = 1.1 / 0.9

    with jitter(0.0), advantage(1.0):
        assert combat.edge_attacking(tuned) == tuned
        assert combat.edge_defending(tuned) == tuned
        assert combat.edge_attacking() == 1.0, "the 1.0 default is no floor at all"

    # Below the tuned jitter the floor holds the edge steady...
    with advantage(1.0):
        with jitter(0.0):
            floor_cold = combat.edge_attacking(tuned)
        with jitter(0.05):
            floor_mid = combat.edge_attacking(tuned)
        with jitter(0.10):
            floor_at = combat.edge_attacking(tuned)
        assert floor_cold == floor_mid == pytest.approx(floor_at)
        # ...and above it, a wilder jitter still widens it.
        with jitter(0.25):
            assert combat.edge_attacking(tuned) > floor_at


def test_min_swing_does_not_floor_the_advantage():
    """Explicitly: holding must still get cheaper as the advantage rises."""
    tuned = 1.1 / 0.9
    with jitter(0.0):                      # floor fully engaged, so only adv moves
        with advantage(1.0):
            flat = combat.edge_defending(tuned)
        with advantage(1.5):
            strong = combat.edge_defending(tuned)
        with advantage(0.75):
            weak = combat.edge_defending(tuned)
    assert strong < flat < weak, "the floor swallowed the defender advantage"
    assert strong == pytest.approx(tuned / 1.5)

    with jitter(0.0):
        with advantage(1.0):
            take_flat = combat.edge_attacking(tuned)
        with advantage(1.5):
            take_strong = combat.edge_attacking(tuned)
    assert take_strong > take_flat, "taking must still get dearer"
    assert take_strong == pytest.approx(tuned * 1.5)
