"""Whole-game checks driven through the headless AI-vs-AI harness."""

from __future__ import annotations

import time
from collections import Counter

from starconquest import ai, settings as settings_mod
from starconquest.model import AiParams
from starconquest.settings import Settings
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


def test_bot_timeout_caps_a_slow_bot_and_survives():
    """A decide() that overruns its budget scores as no orders, not a crash —
    the whole point is capping an oracle-style bot's compute, not aborting it."""

    def _slow(state, pid):
        time.sleep(0.05)
        return []

    ai.register("_test_slow", _slow)
    try:
        r = sim.play(1, nodes=12, players=2, max_turns=200, strategies=["heuristic", "_test_slow"], bot_timeout=0.01)
    finally:
        ai.STRATEGIES.pop("_test_slow", None)
    assert r.bot_timeouts >= 1


def test_bot_timeout_is_zero_when_disabled_or_generous():
    default = sim.play(1, nodes=12, players=2, max_turns=200)
    generous = sim.play(1, nodes=12, players=2, max_turns=200, bot_timeout=1.0)
    assert default.bot_timeouts == 0
    assert generous.bot_timeouts == 0


def test_no_seat_is_systematically_doomed():
    """Peripheral starts should give every seat a real chance across many seeds."""
    wins = Counter(
        r.winner
        for r in (sim.play(s, nodes=18, players=3, max_turns=600) for s in range(120))
        if not r.timed_out
    )
    for pid in (1, 2, 3):
        assert wins[pid] >= 15  # no seat wins almost never


# --------------------------------------------------------------------------- #
# play_settings: the leaderboard's bot-replay column (tools/bot_replay.py)
# --------------------------------------------------------------------------- #
def _setup(**kw) -> Settings:
    base = dict(mode="random", players=3, nodes=16, seed=11)
    return Settings(**{**base, **kw})


def test_replay_is_reproducible_from_the_setup_alone():
    # The whole premise of caching a bot result: same setup, same seed, same
    # answer, every time and on any machine.
    cfg = _setup()
    a = sim.play_settings(cfg, 11, "heuristic")
    b = sim.play_settings(cfg, 11, "heuristic")
    assert a == b


def test_replay_lands_on_the_same_map_the_human_played():
    # mapgen is a pure function of the seed, so the board a bot inherits is
    # identical down to the star names — only the war fought on it differs.
    cfg = _setup()
    one = settings_mod.build_state(cfg, 11)
    two = settings_mod.build_state(cfg, 11)
    assert [(s.id, s.name, s.owner_id, s.ships) for s in one.systems.values()] == \
           [(s.id, s.name, s.owner_id, s.ships) for s in two.systems.values()]


def test_the_bot_actually_drives_the_human_seat():
    # engine._collect_orders skips the human seat, so a replay that forgot to
    # clear is_human would sit still and lose every time. Seat 1 must be taking
    # ground, which is only possible if `decide` ran for it.
    cfg = _setup()
    result = sim.play_settings(cfg, 11, "heuristic", max_turns=40)
    assert result.turns > 0
    assert result.lost > 0 or result.won  # it fought, or it had already won


def test_opponents_keep_the_strategies_the_setup_gave_them():
    # The bot must face the same opposition the human did — that is what makes
    # the comparison mean anything.
    ai.load_models()
    cfg = _setup()
    cfg.ai_strategy = ["heuristic", "rusherplus", "rusherplus"] + ["heuristic"] * 3
    state = settings_mod.build_state(cfg, 11)
    assert [state.players[pid].ai_strategy for pid in (2, 3)] == ["rusherplus", "rusherplus"]


def test_the_replayed_seat_ignores_the_setup_s_own_ai_params():
    # Slot 0 belongs to the human, so whatever a menu left in it says nothing
    # about how a bot should play — and letting it through would score the same
    # bot differently on two identical maps.
    tuned = _setup()
    tuned.ai[0] = AiParams(reserve_fraction=0.9, reserve_floor=40, expand_margin=9.0,
                           attack_margin=9.0, reinforce_margin=40, aux=7.0)
    assert sim.play_settings(tuned, 11, "heuristic") == sim.play_settings(_setup(), 11, "heuristic")


def test_a_bot_that_never_wins_still_reports_a_result():
    # `won` is the discriminator, not `turns`: a replay cut off by the turn cap
    # is a real answer ("no win"), not a missing one.
    result = sim.play_settings(_setup(), 11, "heuristic", max_turns=2)
    assert result.timed_out and not result.won
    assert result.turns == 2


def test_balance_knobs_from_the_setup_reach_the_generated_map():
    # build_state is the only funnel that pushes a Settings' knobs into config,
    # which is why play_settings goes through it instead of mapgen.generate.
    strong = _setup(home_start_ships=99)
    weak = _setup(home_start_ships=5)
    a = settings_mod.build_state(strong, 11)
    b = settings_mod.build_state(weak, 11)
    home_a = max(s.ships for s in a.systems.values() if s.owner_id == 1)
    home_b = max(s.ships for s in b.systems.values() if s.owner_id == 1)
    assert home_a == 99 and home_b == 5
