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
    """Point the module at an endpoint, without touching the shipped constants."""
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_URL", "https://example.test/rest/v1/game_logs")
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_KEY", "publishable-key")


@pytest.fixture
def sent(monkeypatch):
    """Capture what the desktop backend would post, instead of posting it."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(upload, "is_web", lambda: False)
    monkeypatch.setattr(upload, "_post_desktop",
                        lambda body, headers: calls.append((body, headers)) or True)
    return calls


def _log(turns: int = 2) -> replay.GameLog:
    log = replay.new_log(Settings(seed=5, players=3), 5)
    for _ in range(turns):
        log.record_turn(replay.engine.TurnRecord([Order(1, 0, 1, 3)], [0.01]))
    return log


# --------------------------------------------------------------------------- #
# When it refuses
# --------------------------------------------------------------------------- #
def test_unconfigured_endpoint_sends_nothing(monkeypatch, sent):
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_URL", "")
    assert not upload.configured()
    assert upload.post_log(_log(), "key") is False
    assert sent == []


def test_missing_key_sends_nothing(monkeypatch, sent):
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_URL", "https://example.test/x")
    monkeypatch.setattr(upload, "LEADERBOARD_LOG_KEY", "")
    assert upload.post_log(_log(), "key") is False
    assert sent == []


def test_an_empty_log_is_not_worth_posting(configured, sent):
    """Nothing has been played, so there is nothing to check a score against."""
    assert upload.post_log(_log(turns=0), "key") is False
    assert sent == []


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


def test_post_sends_one_row_with_the_key_in_both_headers(configured, sent):
    log = _log()
    assert upload.post_log(log, "abc123") is True
    body, headers = sent[0]
    assert json.loads(body) == [upload.row_for(log, "abc123")]
    assert headers["apikey"] == "publishable-key"
    assert headers["Authorization"] == "Bearer publishable-key"
    # PostgREST echoes an inserted row back in full unless told not to, which for
    # a log means every byte we just uploaded.
    assert headers["Prefer"] == "return=minimal"


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
    assert json.dumps(json.dumps([upload.row_for(log, "abc123")])) in script
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
