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
    """Point the module at an endpoint, without touching the shipped constant."""
    monkeypatch.setattr(share, "LEADERBOARD_LOG_URL", "https://example.test/api/log")


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
    monkeypatch.setattr(share, "LEADERBOARD_LOG_URL", "")
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


def test_web_backend_hands_javascript_a_quoted_payload(configured, monkeypatch):
    """The fetch is built as JS source, so the log must land inside a *string
    literal* — every value goes through json.dumps for exactly that reason."""
    scripts: list[str] = []

    class _Window:
        def eval(self, script):        # noqa: A003 - the JS bridge's own name
            scripts.append(script)

    monkeypatch.setattr(share, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": _Window()}))
    log = _log()
    assert share.post_log(log, "abc123") is True
    script = scripts[0]
    assert script.startswith("fetch(")
    # A rejected promise on a page that is still mid-game must not surface as an
    # uncaught error in the console.
    assert ".catch(" in script
    # The body reaches JS as one quoted literal — JSON inside JSON, so the log's
    # own quotes arrive escaped and nothing in it can close the string early.
    assert json.dumps(json.dumps(share.row_for(log, "abc123"))) in script
    assert log.encoded() in script      # ...and the payload really is in there


def test_a_broken_bridge_is_not_an_error(configured, monkeypatch):
    class _Window:
        def eval(self, script):        # noqa: A003
            raise RuntimeError("no DOM here")

    monkeypatch.setattr(share, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": _Window()}))
    assert share.post_log(_log(), "abc123") is False


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
    monkeypatch.setattr(share, "LEADERBOARD_REPLAY_URL", "https://example.test/api/replay")


MATCH = "00112233445566ff"


class _EvalWindow:
    """A stand-in JS bridge: records what was evaluated, answers what it is told to."""

    def __init__(self, answers=None):
        self.scripts = []
        self.answers = answers or {}

    def eval(self, script):        # noqa: A003 - the bridge's own name
        self.scripts.append(script)
        for needle, value in self.answers.items():
            if needle in script:
                return value
        return ""


def _bridge(monkeypatch, window):
    monkeypatch.setattr(share, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": window}))


def test_an_id_that_the_game_could_not_have_minted_is_never_requested(watchable):
    """It arrives from a URL fragment somebody else may have written, so it is
    checked before it reaches a query string."""
    for bad in ("", "nope", MATCH.upper(), f"{MATCH}0", "../../etc", "a&b=c"):
        assert share.fetch_log(bad) is None


def test_no_endpoint_means_nothing_to_watch(monkeypatch):
    monkeypatch.setattr(share, "LEADERBOARD_REPLAY_URL", "")
    assert share.fetch_log(MATCH) is None


def test_the_web_fetch_parks_its_own_result_and_handles_every_ending(watchable, monkeypatch):
    window = _EvalWindow()
    _bridge(monkeypatch, window)
    assert share.fetch_log(MATCH) is not None
    script = window.scripts[0]
    assert f"?id={MATCH}" in script
    # All three endings must be written, or a poll would hang on `pending` for
    # the rest of the session: a non-2xx is as much an error as a dead socket.
    assert "Promise.reject()" in script and ".catch(" in script
    assert f"'{share.OK}'" in script and f"'{share.ERROR}'" in script


def test_polling_reports_pending_then_the_body(watchable, monkeypatch):
    window = _EvalWindow({"_state": share.PENDING})
    _bridge(monkeypatch, window)
    download = share.fetch_log(MATCH)
    assert download.poll() == (share.PENDING, "")
    window.answers = {"_state": share.OK, "_body": "the-encoded-log"}
    assert download.poll() == (share.OK, "the-encoded-log")


def test_a_failed_fetch_polls_as_an_error_not_an_exception(watchable, monkeypatch):
    window = _EvalWindow({"_state": share.ERROR})
    _bridge(monkeypatch, window)
    assert share.fetch_log(MATCH).poll() == (share.ERROR, "")


def test_a_bridge_that_breaks_mid_download_is_an_error_too(watchable, monkeypatch):
    """Starting the fetch worked; reading its result does not. A poll answers
    with a state, never by raising into the frame that called it."""
    class _Broken(_EvalWindow):
        def eval(self, script):    # noqa: A003
            if "fetch(" not in script:      # ...so only the *poll* fails
                raise RuntimeError("bridge went away")
            return ""

    window = _Broken()
    _bridge(monkeypatch, window)
    download = share.fetch_log(MATCH)
    assert download is not None
    assert download.poll() == (share.ERROR, "")


def test_a_bridge_that_is_not_there_at_all_never_starts_a_download(watchable, monkeypatch):
    class _Absent(_EvalWindow):
        def eval(self, script):    # noqa: A003
            raise RuntimeError("no DOM here")

    _bridge(monkeypatch, _Absent())
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
    for _ in range(200):                      # the thread is real; give it a moment
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.OK, "the-encoded-log")


def test_a_refused_download_polls_as_an_error(watchable, monkeypatch):
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    download = share.fetch_log(MATCH)
    for _ in range(200):
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.ERROR, "")
