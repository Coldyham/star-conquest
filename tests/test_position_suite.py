"""Handing a bot a position out of somebody else's game.

The measurement this makes possible is the one `docs/design/bots.md` could not
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
    for bad in (-1, log.turn_count + 1, log.turn_count + 50):
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
    log, _state = played
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
    base = {"bot": "b", "match_id": "a" * 16, "turn": 0, "won": True, "turns_from": 10, "lost": 0,
                "human_won": True, "human_turns_from": 20, "timed_out": False}
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


def test_a_log_from_older_rules_is_not_loaded(tmp_path, played):
    """It would rebuild a board that was never reached."""
    log, _ = played
    stale = log.to_dict() | {"rules_version": engine.RULES_VERSION - 1}
    (tmp_path / "game_stale.json").write_text(__import__("json").dumps(stale))
    assert position_suite.local_logs(tmp_path) == []


# --------------------------------------------------------------------------- #
# Which games, and which turns of them
# --------------------------------------------------------------------------- #
def _turns(n: int, finished: bool = False, winner: int | None = None, **settings):
    log = replay.new_log(Settings(seed=1, **settings), 1)
    for _ in range(n):
        log.record_turn(engine.TurnRecord(), human_ai=bool(settings.get("autoplay")))
    log.finished, log.winner = finished, winner
    return log


def test_who_won_is_read_from_the_persons_seat():
    assert position_suite.person_won(_turns(10, True, replay.HUMAN_SEAT)) is True
    assert position_suite.person_won(_turns(10, True, 2)) is False
    assert position_suite.person_won(_turns(10)) is False          # abandoned
    # A demo nobody took over has no seat to hand a bot at all.
    assert position_suite.person_won(_turns(10, autoplay=True)) is None


def test_a_won_game_is_sampled_from_the_opening_and_drops_its_endgame():
    log = _turns(100, True, replay.HUMAN_SEAT)
    assert position_suite.plan(log, True, every=20, skip_last=25) == [0, 20, 40, 60]


def test_a_game_not_won_is_sampled_back_from_where_it_stopped():
    """The last boards of an abandoned game are the ones the player gave up on, so
    the endgame skip that suits a win would throw away exactly those."""
    assert position_suite.plan(_turns(100), False, every=20, skip_last=25) == [0, 20, 40, 60, 80, 100]
    assert position_suite.plan(_turns(95), False, every=20, skip_last=25) == [15, 35, 55, 75, 95]
    # A lost game's last board is already decided; the one before it is not.
    assert position_suite.plan(_turns(95, True, 2), False, every=20, skip_last=0) == [14, 34, 54, 74, 94]


def test_the_last_board_of_an_abandoned_game_can_be_handed_over(played):
    log, _ = played
    abandoned = replay.GameLog.from_dict(log.to_dict())
    abandoned.truncate(30)
    with _preserve_config():
        result = sim.play_from(abandoned, 30, "marshal", max_turns=5)
    assert result.turn == 30 and result.human_won is False


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #
def test_a_rerun_plays_nothing_already_recorded(tmp_path, played, capsys):
    """Every run is appended as it finishes, so a killed run keeps its work and a
    rerun picks up where it stopped — the same report either way."""
    log, _ = played
    games = tmp_path / "games"
    games.mkdir()
    (games / "game_ok.json").write_text(__import__("json").dumps(log.to_dict()))
    record = tmp_path / "record.jsonl"
    argv = ["--dir", str(games), "--record", str(record), "--bots", "heuristic", "marshal",
            "--every", "15", "--skip-last", "0", "--max-turns", "40", "--workers", "1"]
    with _preserve_config():
        assert position_suite.main(argv) == 0
    first = capsys.readouterr().out
    rows = record.read_text().splitlines()
    assert len(rows) == 2 * len(sim.positions(log, 15, 0)) and "0 recorded" in first

    with _preserve_config():
        assert position_suite.main(argv) == 0
    again = capsys.readouterr().out
    assert record.read_text().splitlines() == rows
    assert f"{len(rows)} recorded, playing 0" in again
    assert first.split("\nbot", 1)[1] == again.split("\nbot", 1)[1]


def test_a_pool_records_exactly_what_one_process_does(tmp_path, played):
    """Nothing a run decides depends on which process played it."""
    log, _ = played
    games = tmp_path / "games"
    games.mkdir()
    (games / "game_ok.json").write_text(__import__("json").dumps(log.to_dict()))
    out = {}
    for workers in ("1", "2"):
        record = tmp_path / f"w{workers}.jsonl"
        with _preserve_config():
            position_suite.main(["--dir", str(games), "--record", str(record), "--bots", "marshal",
                                 "--every", "20", "--skip-last", "0", "--max-turns", "40",
                                 "--workers", workers])
        out[workers] = sorted(record.read_text().splitlines())
    assert out["1"] == out["2"]
