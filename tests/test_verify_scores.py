"""The leaderboard's score verifier, end to end against a real match.

A posted score is a claim; the log behind it is evidence. Every case here plays
an actual game headlessly, encodes its log the way the game uploads it, and asks
``verify`` what it makes of a score — so the test exercises the whole path a real
submission takes (``GameLog.encoded`` -> the ``game_logs`` row -> ``reconstruct``)
rather than a hand-built fixture that could agree with a bug.

No network: ``verify`` takes the rows it judges as plain dicts, which is what
keeps the decision logic testable at all.
"""

from __future__ import annotations

import pytest

from starconquest import ai, engine, replay
from starconquest.settings import Settings, build_state
from tests.test_replay import _preserve_config
from tools import verify_scores


def _won_match(max_turns: int = 400):
    """Play seeds until the human's seat takes the board, and return that match.

    The verifier only ever sees wins — the game offers the submit button after
    one — so a loss would be testing a path the board cannot reach.
    """
    for seed in range(1, 40):
        settings = Settings(players=3, nodes=14, seed=seed, autoplay=True)
        with _preserve_config():
            state = build_state(settings, seed)
            log = replay.new_log(settings, seed)
            hid = state.human().id
            turns = 0
            while state.winner is None and turns < max_turns:
                record = engine.end_turn(state, human_orders=ai.decide(state, hid),
                                         decide=ai.decide)
                # Claim a couple of turns as hand-played: `hand` is disclosure, and
                # the verifier recomputes it, so it has to be a real number here.
                log.record_turn(record, human_ai=turns >= 2)
                turns += 1
        if state.winner == hid:
            log.mark_finished(state.winner)
            return settings, state, log, hid
    pytest.skip("no seed in range produced a win for the human seat")


@pytest.fixture(scope="module")
def match():
    return _won_match()


@pytest.fixture
def posted(match):
    """The score, the log blob and the setup, exactly as the three tables hold
    them: the score row from the game's own token, the log from the upload, and
    settings_json from the game row the submit page created."""
    settings, state, log, hid = match
    score = {
        "id": 1,
        "game_key": settings.challenge_key(),
        "turns": state.turn,
        "lost": state.players[hid].ships_lost,
        "hand": log.hand_turns,
        "match_id": log.match_id,
    }
    return score, log.encoded(), settings.to_dict()


# --------------------------------------------------------------------------- #
# The verdicts
# --------------------------------------------------------------------------- #
def test_an_honest_score_verifies(posted):
    score, blob, setup = posted
    with _preserve_config():
        assert verify_scores.verify(score, blob, setup).verdict == "verified"


@pytest.mark.parametrize("field", ["turns", "lost", "hand"])
def test_a_score_that_the_replay_contradicts_is_a_mismatch(posted, field):
    """Flattering any one of the three numbers has to be caught — turns is the
    score, lost breaks a tie, and hand is the disclosure that stops an autoplayed
    game passing as a played one."""
    score, blob, setup = posted
    score = {**score, field: score[field] - 1}
    with _preserve_config():
        check = verify_scores.verify(score, blob, setup)
    assert check.verdict == "mismatch" and "replay gives" in check.detail


def test_a_replay_of_another_map_cannot_back_a_score(posted):
    """The attack the setup check exists for: an easy map's log attached to a hard
    map's score would otherwise replay perfectly and verify."""
    score, blob, _ = posted
    other = Settings(players=3, nodes=14, seed=999).to_dict()
    with _preserve_config():
        check = verify_scores.verify(score, blob, other)
    assert check.verdict == "mismatch" and "different setup" in check.detail


def test_a_board_setup_that_lost_its_trailing_zeros_is_still_the_same_setup(posted):
    """Postgres jsonb normalises ``12.0`` to ``12``, so `settings_json` reads back
    with integer ``aux`` values whatever the game sent, while the uploaded log is
    plain JSON and keeps the float. One setup, two reprs — and `challenge_key`
    hashes JSON, so the comparison has to drop the distinction (`_aux_widened`)
    or every posted score carrying an aux reads as somebody else's map."""
    score, blob, setup = posted
    as_jsonb = {**setup, "ai": [{**seat, "aux": int(seat["aux"])} for seat in setup["ai"]]}
    assert any(isinstance(seat["aux"], float) for seat in setup["ai"])  # a real difference
    with _preserve_config():
        assert verify_scores.verify(score, blob, as_jsonb).verdict == "verified"


def test_a_rules_change_sets_a_score_aside_rather_than_accusing_it(posted, monkeypatch):
    """The one thing that can legitimately break an old replay is the *engine*
    moving — never a bot. A score played under the old rules that no longer
    reproduces is unverifiable, not wrong, and must be reported as such."""
    score, blob, setup = posted
    score = {**score, "turns": score["turns"] - 1}      # ...now it disagrees
    # A bump raises the *current* version; the stored log keeps the one it was
    # played under, which is what makes it older.
    monkeypatch.setattr(verify_scores.engine, "RULES_VERSION",
                        verify_scores.engine.RULES_VERSION + 1)
    with _preserve_config():
        check = verify_scores.verify(score, blob, setup)
    assert check.verdict == "outdated"
    assert "rules v" in check.detail and "replay gives" in check.detail


def test_a_rules_change_does_not_excuse_a_score_that_still_reproduces(posted, monkeypatch):
    """A bump disturbs some games and not others; the ones it leaves alone go on
    verifying on their own merits, or one engine change would blank the board."""
    score, blob, setup = posted
    monkeypatch.setattr(verify_scores.engine, "RULES_VERSION",
                        verify_scores.engine.RULES_VERSION + 1)
    with _preserve_config():
        assert verify_scores.verify(score, blob, setup).verdict == "verified"


def test_a_wrong_score_played_under_current_rules_is_still_a_mismatch(posted):
    score, blob, setup = posted
    score = {**score, "lost": score["lost"] + 7}
    with _preserve_config():
        assert verify_scores.verify(score, blob, setup).verdict == "mismatch"


def test_a_score_with_no_uploaded_log_is_missing_not_suspect(posted):
    score, _, setup = posted
    assert verify_scores.verify(score, None, setup).verdict == "missing"
    assert verify_scores.verify(score, "", setup).verdict == "missing"


def test_an_undecodable_blob_is_unreadable(posted):
    score, _, setup = posted
    assert verify_scores.verify(score, "not a log!!", setup).verdict == "unreadable"


def test_a_log_the_human_did_not_win_is_a_mismatch(match):
    """Only a win is postable, so a truncated log — the same match stopped before
    it was decided — must not pass as one."""
    settings, state, log, hid = match
    short = replay.GameLog.from_dict(log.to_dict())
    short.truncate(max(1, log.turn_count - 3))
    score = {"id": 2, "game_key": settings.challenge_key(), "turns": state.turn,
             "lost": state.players[hid].ships_lost, "hand": log.hand_turns,
             "match_id": log.match_id}
    with _preserve_config():
        check = verify_scores.verify(score, short.encoded(), settings.to_dict())
    assert check.verdict == "mismatch"


def test_a_setup_the_worker_cannot_read_is_never_taken_as_a_match(posted):
    """`Settings.from_dict` is tolerant by design and answers a non-dict with a
    default 18-node map — which would silently verify a score against the wrong
    setup. Same trap `bot_replay._settings_for` documents."""
    score, blob, _ = posted
    for junk in (None, "nonsense", []):
        with _preserve_config():
            assert verify_scores.verify(score, blob, junk).verdict == "mismatch"


# --------------------------------------------------------------------------- #
# Work selection
# --------------------------------------------------------------------------- #
def _scores(*ids):
    return [{"id": i} for i in ids]


def test_a_decided_score_is_not_rechecked():
    done = {1: {"verdict": "verified", "engine_rev": "abc"}}
    assert verify_scores.pending(_scores(1, 2), done, "abc") == [{"id": 2}]


def test_missing_is_always_retried_without_a_flag():
    """The one verdict that changes on its own: an upload can still arrive."""
    done = {1: {"verdict": "missing", "engine_rev": "abc"}}
    assert verify_scores.pending(_scores(1), done, "abc") == [{"id": 1}]


def test_stale_picks_up_rows_decided_by_older_code():
    done = {1: {"verdict": "verified", "engine_rev": "old"}}
    assert verify_scores.pending(_scores(1), done, "new") == []
    assert verify_scores.pending(_scores(1), done, "new", stale=True) == [{"id": 1}]


def test_recheck_takes_everything():
    done = {1: {"verdict": "verified", "engine_rev": "abc"}}
    assert verify_scores.pending(_scores(1), done, "abc", recheck=True) == [{"id": 1}]


def test_the_longest_upload_of_a_match_is_the_current_one():
    """Uploads are append-only, so a match posted twice has two rows; the later
    one is longer, and the earlier is its own history."""
    rows = [{"id": 1, "match_id": "aa" * 8, "turns": 20},
            {"id": 2, "match_id": "aa" * 8, "turns": 31},
            {"id": 3, "match_id": "bb" * 8, "turns": 5},
            {"id": 4, "match_id": "", "turns": 99}]
    best = verify_scores.best_logs(rows)
    assert best["aa" * 8]["id"] == 2
    assert best["bb" * 8]["id"] == 3
    assert "" not in best
