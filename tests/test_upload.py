"""The replay upload bridge: what it sends, when it refuses, and never raising.

``upload`` is a platform bridge like ``webstore``, so the contract under test is
mostly negative — an unconfigured endpoint, a missing browser API and a refused
connection all have to read as "didn't send" rather than as an exception on the
frame that follows a win. Nothing here touches the network: both backends are
intercepted, which is also what lets the web path be inspected as the JS source
string it really is.
"""

from __future__ import annotations

import json

import pytest

from starconquest import replay, upload
from starconquest.model import Order
from starconquest.settings import Settings


@pytest.fixture
def configured(monkeypatch):
    """Point the module at an endpoint, without touching the shipped constant."""
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_URL", "https://example.test/api/log")


@pytest.fixture
def sharing(monkeypatch):
    """Opt in, without writing to the real preference store."""
    monkeypatch.setattr(upload.webstore, "share_games", lambda: True)


@pytest.fixture
def sent(monkeypatch):
    """Capture what the desktop backend would post, instead of posting it."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(upload, "is_web", lambda: False)
    monkeypatch.setattr(upload, "_post_desktop",
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
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_URL", "")
    assert not upload.configured()
    assert upload.post_log(_log(), "key") is False
    assert sent == []


def test_an_empty_log_is_not_worth_posting(configured, sent):
    """Nothing has been played, so there is nothing to check a score against."""
    assert upload.post_log(_log(turns=0), "key") is False
    assert sent == []


def test_a_pure_autoplay_demo_is_not_worth_posting(configured, sent):
    """A bot-versus-bot game is reproducible from its seed, so storing one adds
    bytes and no information — and the demo mode must not quietly upload."""
    assert upload.worth_sending(_log(turns=30, autoplayed=True)) is False
    assert upload.post_log(_log(turns=30, autoplayed=True), "key") is False
    assert sent == []


# --------------------------------------------------------------------------- #
# The checkpoint cadence (the "Share replays" opt-in)
# --------------------------------------------------------------------------- #
def test_nothing_checkpoints_until_the_preference_is_on(monkeypatch, configured):
    monkeypatch.setattr(upload.webstore, "share_games", lambda: False)
    assert upload.due(_log(upload.CHECKPOINT_TURNS), finished=False) is False
    assert upload.due(_log(3), finished=True) is False


def test_a_shared_game_checkpoints_on_the_cadence(configured, sharing):
    every = upload.CHECKPOINT_TURNS
    assert upload.due(_log(every), finished=False) is True
    assert upload.due(_log(every * 2), finished=False) is True
    assert upload.due(_log(every - 1), finished=False) is False


def test_a_decided_game_checkpoints_whatever_turn_it_ended_on(configured, sharing):
    """Otherwise the stored replay stops up to a full window short of the win."""
    assert upload.due(_log(upload.CHECKPOINT_TURNS - 1), finished=True) is True


def test_a_shared_demo_still_checkpoints_nothing(configured, sharing):
    every = upload.CHECKPOINT_TURNS
    assert upload.due(_log(every, autoplayed=True), finished=False) is False


# --------------------------------------------------------------------------- #
# What it sends
# --------------------------------------------------------------------------- #
def test_row_carries_the_match_id_turns_and_encoded_log(configured):
    log = _log(turns=3)
    row = upload.row_for(log, "abc123")
    assert row["match_id"] == log.match_id
    assert row["game_key"] == "abc123"
    assert row["turns"] == 3
    assert replay.GameLog.decode(row["log"]).to_dict() == log.to_dict()


def test_row_flags_an_unfinished_game_as_neither_finished_nor_won(configured):
    row = upload.row_for(_log(turns=3), "abc123")
    assert row["finished"] is False and row["won"] is False
    assert row["hand"] == 3


def test_row_flags_a_win_only_for_the_players_own_seat(configured):
    log = _log(turns=4)
    log.mark_finished(2)                       # an AI seat took the board
    assert upload.row_for(log, "k")["won"] is False
    log.mark_finished(1)                       # ...and now the human's
    row = upload.row_for(log, "k")
    assert row["won"] is True and row["finished"] is True


def test_post_sends_the_row_and_no_credentials(configured, sent):
    log = _log()
    assert upload.post_log(log, "abc123") is True
    body, headers = sent[0]
    assert json.loads(body) == upload.row_for(log, "abc123")
    # No key of any kind: the endpoint is the leaderboard's own function, and the
    # one key that may write the table lives in that function's environment.
    assert set(headers) == {"Content-Type"}


def test_web_backend_hands_javascript_a_quoted_payload(configured, monkeypatch):
    """The fetch is built as JS source, so the log must land inside a *string
    literal* — every value goes through json.dumps for exactly that reason."""
    scripts: list[str] = []

    class _Window:
        def eval(self, script):        # noqa: A003 - the JS bridge's own name
            scripts.append(script)

    monkeypatch.setattr(upload, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": _Window()}))
    log = _log()
    assert upload.post_log(log, "abc123") is True
    script = scripts[0]
    assert script.startswith("fetch(")
    # A rejected promise on a page that is still mid-game must not surface as an
    # uncaught error in the console.
    assert ".catch(" in script
    # The body reaches JS as one quoted literal — JSON inside JSON, so the log's
    # own quotes arrive escaped and nothing in it can close the string early.
    assert json.dumps(json.dumps(upload.row_for(log, "abc123"))) in script
    assert log.encoded() in script      # ...and the payload really is in there


def test_a_broken_bridge_is_not_an_error(configured, monkeypatch):
    class _Window:
        def eval(self, script):        # noqa: A003
            raise RuntimeError("no DOM here")

    monkeypatch.setattr(upload, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": _Window()}))
    assert upload.post_log(_log(), "abc123") is False


def test_a_refused_connection_is_not_an_error(configured, monkeypatch):
    """The desktop backend runs on a daemon thread and nobody reads the result;
    a dead endpoint must not reach the caller either."""
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(upload, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert upload.post_log(_log(), "abc123") is True   # started, not delivered
