"""Whole-game checks driven through the headless AI-vs-AI harness."""

from __future__ import annotations

from collections import Counter

from tests import sim


def test_invariants_hold_and_most_games_terminate():
    results = [sim.play(seed, nodes=18, players=3, max_turns=600) for seed in range(30)]
    # sim.play calls check_invariants every turn, so reaching here means no
    # negative garrisons, phantom fleets, or unknown owners ever appeared.
    finished = [r for r in results if not r.timed_out]
    assert len(finished) / len(results) >= 0.6  # the vast majority should resolve
    for r in finished:
        assert r.winner in (0, 1, 2, 3)


def test_deterministic_from_seed():
    a = sim.play(5, nodes=18, players=3, max_turns=600)
    b = sim.play(5, nodes=18, players=3, max_turns=600)
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_two_player_game_resolves():
    r = sim.play(3, nodes=12, players=2, max_turns=600)
    assert not r.timed_out and r.winner in (1, 2)


def test_no_seat_is_systematically_doomed():
    """Peripheral starts should give every seat a real chance across many seeds."""
    wins = Counter(
        r.winner
        for r in (sim.play(s, nodes=18, players=3, max_turns=600) for s in range(120))
        if not r.timed_out
    )
    for pid in (1, 2, 3):
        assert wins[pid] >= 15  # no seat wins almost never
