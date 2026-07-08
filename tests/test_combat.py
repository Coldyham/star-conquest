"""Tests for Lanchester-with-jitter combat and arrival resolution."""

from __future__ import annotations

import random
from contextlib import contextmanager

from starconquest import combat, config
from starconquest.model import Fleet, GameState, System


@contextmanager
def jitter(value: float):
    """Temporarily override the combat jitter (e.g. 0 for deterministic maths)."""
    old = config.COMBAT_JITTER
    config.COMBAT_JITTER = value
    try:
        yield
    finally:
        config.COMBAT_JITTER = old


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


def test_three_way_pileup_single_winner():
    s = _state()
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=0, ships=4)  # neutral defender
    fleets = [Fleet(1, 9, 0, 12, 2, 0), Fleet(2, 8, 0, 9, 2, 0)]
    owner, ships = combat.resolve_arrival(s, 0, fleets)
    assert owner in (0, 1, 2)
    assert ships >= 0
    assert s.systems[0].owner_id == owner and s.systems[0].ships == ships
