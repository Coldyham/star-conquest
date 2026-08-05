"""Whole-game checks driven through the headless AI-vs-AI harness."""

from __future__ import annotations

from collections import Counter

from starconquest import ai
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


def test_ladder_plays_every_pair_in_both_seatings():
    roster = ["heuristic", "heuristic", "heuristic"]   # 3 pairs, seating-agnostic
    games = sim.run_ladder(range(2), "random", 12, roster, max_turns=300)
    assert len(games) == 3 * 2 * 2            # pairs x seatings x seeds
    for g in games:
        assert len(g.assignment) == 2         # head-to-head, never a melee


def test_ladder_credits_the_winning_strategy():
    """A bot that never issues an order must lose, whichever seat it holds — which
    is exactly what the assignment-aware tally is for."""
    roster = ["heuristic", "_test_passive"]
    ai.register("_test_passive", lambda state, pid: [])
    try:
        games = sim.run_ladder(range(3), "random", 12, roster, max_turns=400)
    finally:
        ai.STRATEGIES.pop("_test_passive", None)
    wins, draws, timeouts = sim._tally(games, roster)
    assert sum(wins.values()) + draws + timeouts == len(games)
    assert wins["heuristic"] > wins["_test_passive"]


def test_avg_turns_excludes_timeouts():
    finished = sim.SwapGame(sim.SimResult(0, 1, 50, False), ["a", "b"])
    timed_out = sim.SwapGame(sim.SimResult(1, None, 600, True), ["a", "b"])
    assert sim._avg_turns([finished, timed_out]) == 50.0


def test_no_seat_is_systematically_doomed():
    """Peripheral starts should give every seat a real chance across many seeds."""
    wins = Counter(
        r.winner
        for r in (sim.play(s, nodes=18, players=3, max_turns=600) for s in range(120))
        if not r.timed_out
    )
    for pid in (1, 2, 3):
        assert wins[pid] >= 15  # no seat wins almost never
