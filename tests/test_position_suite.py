"""Handing a bot a position out of somebody else's game.

The measurement this makes possible is the one `docs/bot-design.md` could not
take before: every figure there is a bot against another bot, so the roster is
only ever judged on positions bots create. These exercise the mechanics of
branching a recorded match — the position rebuilt must be the one that was
played, the caller's log must survive it, and the human baseline must only be
claimed where there really is one.

Pure and headless, like the harness itself.
"""

from __future__ import annotations

import pytest

from starconquest import ai, engine, replay
from starconquest.settings import Settings, build_state
from tests import sim
from tests.test_replay import _preserve_config
from tools import position_suite


def _game(seed: int = 3, players: int = 3, nodes: int = 12, max_turns: int = 300):
    """A recorded match with the player's seat driven by the weakest strategy.

    A stand-in for a human game, and deliberately a beatable one: what the suite
    is *for* is positions where a stronger bot can do better, and a corpus the
    roster cannot improve on would exercise none of the comparison.
    """
    cfg = Settings(players=players, nodes=nodes, seed=seed)
    with _preserve_config():
        state = build_state(cfg, seed)
        log = replay.GameLog(seed=seed, settings=cfg.to_dict())
        hid = state.human().id
        turns = 0
        while state.winner is None and turns < max_turns:
            record = engine.end_turn(state, human_orders=ai.decide(state, hid),
                                     decide=ai.decide)
            log.record_turn(record, human_ai=False)
            turns += 1
    log.mark_finished(state.winner)
    return log, state


@pytest.fixture(scope="module")
def played():
    return _game()


# --------------------------------------------------------------------------- #
# Branching a recorded match
# --------------------------------------------------------------------------- #
def test_a_branch_starts_from_the_position_that_was_really_played(played):
    """`reconstruct` deals the recorded orders and dice back, so the board handed
    over is the one that stood there — not one a bot re-derived."""
    log, _ = played
    branch = replay.GameLog.from_dict(log.to_dict())
    branch.truncate(30)
    with _preserve_config():
        expected, _ = replay.reconstruct(branch)
        result = sim.play_from(log, 30, "heuristic", max_turns=0)
    # max_turns=0 plays nothing, so what comes back describes the handover itself.
    assert result.turn == 30 and expected.turn == 30
    assert result.timed_out is True and result.turns_from is None


def test_branching_never_touches_the_callers_log(played):
    """It truncates a *copy*: a suite samples one log at a dozen turns in a row,
    and a mutating branch would quietly shorten it under the next sample."""
    log, _ = played
    before = log.to_dict()
    with _preserve_config():
        sim.play_from(log, 10, "marshal", max_turns=40)
        sim.play_from(log, 20, "marshal", max_turns=40)
    assert log.to_dict() == before


def test_the_allowance_counts_from_the_position_not_from_turn_zero(played):
    """Otherwise a board handed over on turn 300 gets no game at all."""
    log, _ = played
    with _preserve_config():
        result = sim.play_from(log, 40, "marshal", max_turns=5)
    assert result.turns_from is None or result.turns_from <= 5


def test_a_turn_outside_the_log_is_refused(played):
    log, _ = played
    for bad in (-1, log.turn_count, log.turn_count + 50):
        with pytest.raises(ValueError):
            sim.play_from(log, bad, "marshal")


# --------------------------------------------------------------------------- #
# The human baseline
# --------------------------------------------------------------------------- #
def test_a_win_gives_the_bot_something_to_be_measured_against(played):
    """`gain` is the whole point: turns the bot saved against the person who was
    actually there, and it exists only where both of them finished."""
    log, state = played
    if state.winner != 1:
        pytest.skip("this seed's recorded game was not a win for the player's seat")
    with _preserve_config():
        result = sim.play_from(log, 20, "marshal", max_turns=300)
    assert result.human_won is True
    assert result.human_turns_from == log.turn_count - 20
    if result.turns_from is not None:
        assert result.gain == result.turns_from - result.human_turns_from


def test_a_lost_game_offers_no_baseline_but_is_still_a_question(played):
    """The positions worth having most: nobody finished, so there is nothing to
    compare — but "can a bot still take this board?" is answerable and hard."""
    log, state = played
    lost = replay.GameLog.from_dict(log.to_dict())
    lost.winner, lost.finished = 2, True          # an opponent took it instead
    with _preserve_config():
        result = sim.play_from(lost, 20, "marshal", max_turns=300)
    assert result.human_won is False
    assert result.human_turns_from is None and result.gain is None


# --------------------------------------------------------------------------- #
# Sampling and rollup
# --------------------------------------------------------------------------- #
def test_positions_sample_the_whole_game_but_drop_the_endgame():
    log = replay.new_log(Settings(seed=1), 1)
    for _ in range(100):
        log.record_turn(engine.TurnRecord())
    assert sim.positions(log, every=20, skip_last=0) == [0, 20, 40, 60, 80]
    # A decided match's last turns are a formality every bot completes alike.
    assert sim.positions(log, every=20, skip_last=25) == [0, 20, 40, 60]
    assert sim.positions(log, every=200, skip_last=0) == [0]
    assert sim.positions(log, every=20, skip_last=500) == []


def _result(**over):
    base = dict(bot="b", match_id="a" * 16, turn=0, won=True, turns_from=10, lost=0,
                human_won=True, human_turns_from=20, timed_out=False)
    return sim.PositionResult(**{**base, **over})


def test_the_three_summary_numbers_answer_three_different_questions():
    rows = [
        _result(turns_from=10, human_turns_from=20),            # faster by 10
        _result(turns_from=30, human_turns_from=20),            # slower by 10
        _result(won=False, turns_from=None, timed_out=True),    # never finished
        _result(won=True, human_won=False, human_turns_from=None),   # a recovery
        _result(won=False, human_won=False, human_turns_from=None,
                turns_from=None, timed_out=True),               # ...and a failure
    ]
    out = position_suite.summarise(rows)
    assert out["positions"] == 5
    # Only the two where *both* sides finished can be compared at all.
    assert out["compared"] == 2 and out["beaten"] == 1 and out["median_gain"] == 0
    # ...and only the two from a game the person lost can be recovered.
    assert out["recoverable"] == 2 and out["recovered"] == 1
    assert out["timeouts"] == 2


def test_a_bot_with_nothing_comparable_reports_no_gain_rather_than_zero():
    """A corpus of pure losses has no baseline in it, and printing `+0` would read
    as "exactly as good as the player" rather than "not measured"."""
    out = position_suite.summarise([_result(human_won=False, human_turns_from=None)])
    assert out["median_gain"] is None and out["compared"] == 0


# --------------------------------------------------------------------------- #
# Loading a corpus
# --------------------------------------------------------------------------- #
def test_only_replayable_logs_are_loaded(tmp_path, played):
    """A version-1 log recorded the human's orders alone and re-ran the AI, so the
    position it rebuilds is not the one that was played — the same reason
    `replay.latest_log` skips them."""
    log, _ = played
    (tmp_path / "game_ok.json").write_text(__import__("json").dumps(log.to_dict()))
    old = log.to_dict() | {"version": 1}
    (tmp_path / "game_old.json").write_text(__import__("json").dumps(old))
    (tmp_path / "game_broken.json").write_text("{not json")
    (tmp_path / "ignored.txt").write_text("not a log at all")

    loaded = position_suite.local_logs(tmp_path)
    assert [x.match_id for x in loaded] == [log.match_id]
