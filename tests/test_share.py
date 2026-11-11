"""The replay-sharing bridge: what it sends and fetches, and never raising.

``share`` is a platform bridge like ``webstore``, so the contract under test is
mostly negative — an unconfigured endpoint, a missing browser API and a refused
connection all have to read as "didn't send" rather than as an exception on the
frame that follows a win. Nothing here touches the network: both backends are
intercepted, which is also what lets the web path be inspected as the JS source
string it really is.
"""

from __future__ import annotations

import json
import time

import pytest

from starconquest import replay, share
from starconquest.model import Order
from starconquest.settings import Settings


@pytest.fixture
def configured(monkeypatch):
    """Point the board somewhere harmless, without touching the shipped constant.

    Patching the *origin* rather than a URL is the real seam now: every endpoint
    is derived from it at call time, which is what lets a deploy preview reach the
    matching preview of the board."""
    monkeypatch.setattr(share.webstore, "leaderboard_origin",
                        lambda: "https://example.test")


@pytest.fixture
def sharing(monkeypatch):
    """Opt in, without writing to the real preference store."""
    monkeypatch.setattr(share.webstore, "share_games", lambda: True)


@pytest.fixture
def sent(monkeypatch):
    """Capture what the desktop backend would post, instead of posting it."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(share, "_post_desktop",
                        lambda body, headers: calls.append((body, headers)) or True)
    return calls


def _log(turns: int = 2, *, autoplayed: bool = False) -> replay.GameLog:
    log = replay.new_log(Settings(seed=5, players=3), 5)
    for _ in range(turns):
        log.record_turn(replay.engine.TurnRecord([Order(1, 0, 1, 3)], [0.01]),
                        human_ai=autoplayed)
    return log


# --------------------------------------------------------------------------- #
# When it refuses
# --------------------------------------------------------------------------- #
def test_unconfigured_endpoint_sends_nothing(monkeypatch, sent):
    monkeypatch.setattr(share.webstore, "leaderboard_origin", lambda: "")
    assert not share.configured()
    assert share.post_log(_log(), "key") is False
    assert sent == []


def test_an_empty_log_is_not_worth_posting(configured, sent):
    """Nothing has been played, so there is nothing to check a score against."""
    assert share.post_log(_log(turns=0), "key") is False
    assert sent == []


def test_a_pure_autoplay_demo_is_not_worth_posting(configured, sent):
    """A bot-versus-bot game is reproducible from its seed, so storing one adds
    bytes and no information — and the demo mode must not quietly share."""
    assert share.worth_sending(_log(turns=30, autoplayed=True)) is False
    assert share.post_log(_log(turns=30, autoplayed=True), "key") is False
    assert sent == []


# --------------------------------------------------------------------------- #
# The checkpoint cadence (the "Share replays" opt-in)
# --------------------------------------------------------------------------- #
def test_nothing_checkpoints_until_the_preference_is_on(monkeypatch, configured):
    monkeypatch.setattr(share.webstore, "share_games", lambda: False)
    assert share.due(_log(share.CHECKPOINT_TURNS), finished=False) is False
    assert share.due(_log(3), finished=True) is False


def test_a_shared_game_checkpoints_on_the_cadence(configured, sharing):
    every = share.CHECKPOINT_TURNS
    assert share.due(_log(every), finished=False) is True
    assert share.due(_log(every * 2), finished=False) is True
    assert share.due(_log(every - 1), finished=False) is False


def test_a_decided_game_checkpoints_whatever_turn_it_ended_on(configured, sharing):
    """Otherwise the stored replay stops up to a full window short of the win."""
    assert share.due(_log(share.CHECKPOINT_TURNS - 1), finished=True) is True


def test_a_shared_demo_still_checkpoints_nothing(configured, sharing):
    every = share.CHECKPOINT_TURNS
    assert share.due(_log(every, autoplayed=True), finished=False) is False


def test_playing_on_from_a_watched_replay_checkpoints_nothing(configured, sharing):
    """That log is somebody else's match and carries their ``match_id``. Rows are
    keyed by it and the longest wins, so uploading our continuation would replace
    the replay their posted score is checked against — with a game they never
    played."""
    every = share.CHECKPOINT_TURNS
    assert share.due(_log(every), finished=False, ours=False) is False
    assert share.due(_log(every - 1), finished=True, ours=False) is False


# --------------------------------------------------------------------------- #
# What it sends
# --------------------------------------------------------------------------- #
def test_row_carries_the_match_id_turns_and_encoded_log(configured):
    log = _log(turns=3)
    row = share.row_for(log, "abc123")
    assert row["match_id"] == log.match_id
    assert row["game_key"] == "abc123"
    assert row["turns"] == 3
    assert replay.GameLog.decode(row["log"]).to_dict() == log.to_dict()


def test_row_carries_rules_version_as_a_claim(configured):
    """So the board can tell an outdated replay apart from a current one
    (`GameLog.is_current`) without decoding the blob to find out."""
    log = _log(turns=3)
    log.rules_version = 7
    assert share.row_for(log, "abc123")["rules_version"] == 7


def test_row_flags_an_unfinished_game_as_neither_finished_nor_won(configured):
    row = share.row_for(_log(turns=3), "abc123")
    assert row["finished"] is False and row["won"] is False
    assert row["hand"] == 3


def test_row_flags_a_win_only_for_the_players_own_seat(configured):
    log = _log(turns=4)
    log.mark_finished(2)                       # an AI seat took the board
    assert share.row_for(log, "k")["won"] is False
    log.mark_finished(1)                       # ...and now the human's
    row = share.row_for(log, "k")
    assert row["won"] is True and row["finished"] is True


def test_post_sends_the_row_and_no_credentials(configured, sent):
    log = _log()
    assert share.post_log(log, "abc123") is True
    body, headers = sent[0]
    assert json.loads(body) == share.row_for(log, "abc123")
    # No key of any kind: the endpoint is the leaderboard's own function, and the
    # one key that may write the table lives in that function's environment.
    assert set(headers) == {"Content-Type"}


def test_a_refused_connection_is_not_an_error(configured, monkeypatch):
    """The desktop backend runs on a daemon thread and nobody reads the result;
    a dead endpoint must not reach the caller either."""
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert share.post_log(_log(), "abc123") is True   # started, not delivered


# --------------------------------------------------------------------------- #
# Fetching a replay to watch
# --------------------------------------------------------------------------- #
@pytest.fixture
def watchable(monkeypatch):
    monkeypatch.setattr(share.webstore, "leaderboard_origin",
                        lambda: "https://example.test")


MATCH = "00112233445566ff"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Back the preference store with a tmp file, so a poll reads what the browser
    would have written without touching the developer's own store."""
    monkeypatch.setattr(share.webstore, "_file_path", lambda: tmp_path / "kv.json")


def test_an_id_that_the_game_could_not_have_minted_is_never_requested(watchable):
    """It arrives from a URL fragment somebody else may have written, so it is
    checked before it reaches a query string."""
    for bad in ("", "nope", MATCH.upper(), f"{MATCH}0", "../../etc", "a&b=c"):
        assert share.fetch_log(bad) is None


def test_no_endpoint_means_nothing_to_watch(monkeypatch):
    monkeypatch.setattr(share.webstore, "leaderboard_origin", lambda: "")
    assert share.fetch_log(MATCH) is None


def test_the_desktop_fetch_returns_the_body_off_the_loop(watchable, monkeypatch):
    """A frame loop must never wait on a socket, so the answer arrives through a
    poll rather than a return value."""
    import urllib.request

    class _Response:
        def read(self):
            return b"the-encoded-log"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response())
    download = share.fetch_log(MATCH)
    assert download is not None
    for _ in range(200):                      # the thread is real; give it a moment
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.OK, "the-encoded-log")


def test_a_desktop_404_is_missing_rather_than_an_error(watchable, monkeypatch):
    import email.message
    import urllib.error
    import urllib.request

    def _absent(*args, **kwargs):
        raise urllib.error.HTTPError("u", 404, "no replay", email.message.Message(), None)

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _absent)
    download = share.fetch_log(MATCH)
    assert download is not None
    for _ in range(200):
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.MISSING, "")


def test_a_refused_download_polls_as_an_error(watchable, monkeypatch):
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    download = share.fetch_log(MATCH)
    assert download is not None
    for _ in range(200):
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.ERROR, "")


# --------------------------------------------------------------------------- #
# Quick post
# --------------------------------------------------------------------------- #


def test_quick_post_is_offered_only_on_the_web_with_a_name(configured, monkeypatch):
    monkeypatch.setattr(share.webstore, "pbp_name", lambda: "Ada")
    monkeypatch.setattr(share, "is_web", lambda: False)
    assert share.quick_post_name() == ""          # desktop: the entry form
    monkeypatch.setattr(share, "is_web", lambda: True)
    assert share.quick_post_name() == "Ada"
    monkeypatch.setattr(share.webstore, "leaderboard_origin", lambda: "")
    assert share.quick_post_name() == ""          # no board at all


def _answer(**fields) -> str:
    return json.dumps({"game_key": "abc", **fields})


def test_a_lone_score_is_the_first_on_its_map():
    got = share.post_summary(_answer(score_count=1, best_turns=30, best_lost=4,
                                     best_user_name="Ada", best_holders=1), 30, 4, "ada")
    assert got == ("abc", "Posted  ·  the first score on this map")


def test_the_best_on_our_figures_and_name_is_ours():
    got = share.post_summary(_answer(score_count=7, best_turns=30, best_lost=4,
                                     best_user_name="Ada", best_holders=1), 30, 4, "Ada")
    assert got == ("abc", "Posted  ·  you lead  ·  7 scores on this map")


def test_a_shared_best_says_how_many_share_it():
    got = share.post_summary(_answer(score_count=7, best_turns=30, best_lost=4,
                                     best_user_name="Bob", best_holders=3), 30, 4, "Ada")
    assert got == ("abc", "Posted  ·  joint best with 2 others  ·  7 scores on this map")


def test_behind_names_the_best_and_its_losses_only_on_level_turns():
    behind = share.post_summary(_answer(score_count=7, best_turns=21, best_lost=4,
                                        best_user_name="Bob"), 30, 4, "Ada")
    assert behind == ("abc", "Posted  ·  best is 21 turns, 4 lost by Bob  ·  7 scores on this map")
    level = share.post_summary(_answer(score_count=7, best_turns=30, best_lost=2,
                                       best_user_name="Bob"), 30, 4, "Ada")
    assert level == ("abc", "Posted  ·  best is 30 turns, 2 lost by Bob  ·  7 scores on this map")


def test_a_best_on_our_figures_under_another_name_is_theirs():
    """Our score did not count (a match posted twice), so the best is Bob's."""
    got = share.post_summary(_answer(score_count=7, best_turns=30, best_lost=4,
                                     best_user_name="Bob", best_holders=1), 30, 4, "Ada")
    assert got == ("abc", "Posted  ·  best is 30 turns, 4 lost by Bob  ·  7 scores on this map")


def test_a_posted_score_without_a_summary_still_says_posted():
    """The module answers the key alone when the summary could not be read."""
    assert share.post_summary(_answer(), 30, 4, "Ada") == ("abc", "Posted")
    assert share.post_summary(_answer(score_count=5), 30, 4, "Ada") == (
        "abc", "Posted  ·  5 scores on this map")


def test_a_long_leader_name_is_cut():
    got = share.post_summary(_answer(score_count=2, best_turns=21, best_lost=0,
                                     best_user_name="A" * 40), 30, 4, "Ada")
    assert got is not None and ("A" * 15 + "…") in got[1] and "A" * 16 not in got[1]


def test_an_answer_that_is_not_one_is_none():
    for body in ("", "nope", "[]", "{}", '{"game_key": ""}', '{"game_key": 5}',
                 _answer(score_count="many")):
        assert share.post_summary(body, 30, 4, "Ada") is None
