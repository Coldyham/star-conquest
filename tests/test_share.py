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

from starconquest import paths, replay, share
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
    monkeypatch.setattr(share.webstore, "leaderboard_origin",
                        lambda: "https://example.test")


MATCH = "00112233445566ff"


class _EvalWindow:
    """A stand-in JS bridge: records what it was asked to run, and runs nothing.

    Deliberately returns nothing useful — the point of the localStorage mailbox is
    that no value has to come *back* through `eval`.
    """

    def __init__(self):
        self.scripts = []

    def eval(self, script):        # noqa: A003 - the bridge's own name
        self.scripts.append(script)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Back the preference store with a tmp file, so a poll reads what the browser
    would have written without touching the developer's own store."""
    monkeypatch.setattr(share.webstore, "_file_path", lambda: tmp_path / "kv.json")


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
    monkeypatch.setattr(share.webstore, "leaderboard_origin", lambda: "")
    assert share.fetch_log(MATCH) is None


def test_the_web_fetch_stores_its_own_result_and_handles_every_ending(watchable, monkeypatch):
    """The JS is fire-and-forget, so it must leave the slot in exactly one of the
    three states whatever happens — a non-2xx is as much an end as a dead socket,
    and a poll that never sees one waits for ever."""
    window = _EvalWindow()
    _bridge(monkeypatch, window)
    assert share.fetch_log(MATCH) is not None
    script = window.scripts[0]
    assert f"?id={MATCH}" in script
    # Written into localStorage, not onto `window`: the poll reads it back through
    # `webstore.get`, the one read path the rest of the game proves every launch.
    assert "localStorage.setItem" in script
    assert paths.WEB_REPLAY_STATE_KEY in script and paths.WEB_REPLAY_BODY_KEY in script
    assert "Promise.reject" in script and ".catch(" in script
    assert f"'{share.OK}'" in script and f"'{share.ERROR}'" in script


def test_polling_reports_pending_then_the_body(watchable, monkeypatch, store):
    _bridge(monkeypatch, _EvalWindow())
    download = share.fetch_log(MATCH)
    share.webstore.set(paths.WEB_REPLAY_STATE_KEY, share.PENDING)
    assert download.poll() == (share.PENDING, "")

    share.webstore.set(paths.WEB_REPLAY_BODY_KEY, "the-encoded-log")
    share.webstore.set(paths.WEB_REPLAY_STATE_KEY, share.OK)
    assert download.poll() == (share.OK, "the-encoded-log")


def test_a_collected_result_is_cleared_so_it_cannot_be_read_twice(watchable, monkeypatch, store):
    """A stale `ok` left by an earlier session would otherwise be collected as an
    instant success carrying somebody else's replay."""
    _bridge(monkeypatch, _EvalWindow())
    download = share.fetch_log(MATCH)
    share.webstore.set(paths.WEB_REPLAY_BODY_KEY, "the-encoded-log")
    share.webstore.set(paths.WEB_REPLAY_STATE_KEY, share.OK)
    assert download.poll()[0] == share.OK
    assert download.poll() == (share.PENDING, "")
    assert share.webstore.get(paths.WEB_REPLAY_BODY_KEY) == ""


def test_a_failed_fetch_polls_as_an_error_not_an_exception(watchable, monkeypatch, store):
    _bridge(monkeypatch, _EvalWindow())
    download = share.fetch_log(MATCH)
    share.webstore.set(paths.WEB_REPLAY_STATE_KEY, share.ERROR)
    assert download.poll() == (share.ERROR, "")


def test_no_such_replay_is_its_own_answer(watchable, monkeypatch, store):
    """"The board says there is no such replay" and "the board did not answer"
    send the player to different places, so they are not the same state."""
    _bridge(monkeypatch, _EvalWindow())
    download = share.fetch_log(MATCH)
    share.webstore.set(paths.WEB_REPLAY_STATE_KEY, share.MISSING)
    assert download.poll() == (share.MISSING, "")


def test_the_web_fetch_tells_a_404_apart_from_a_dead_connection(watchable, monkeypatch):
    window = _EvalWindow()
    _bridge(monkeypatch, window)
    share.fetch_log(MATCH)
    assert f"e===404?'{share.MISSING}'" in window.scripts[0]


def test_an_empty_slot_reads_as_pending_rather_than_as_a_failure(watchable, monkeypatch, store):
    """The state between starting the fetch and the browser answering."""
    _bridge(monkeypatch, _EvalWindow())
    assert share.fetch_log(MATCH).poll() == (share.PENDING, "")


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


def test_a_desktop_404_is_missing_rather_than_an_error(watchable, monkeypatch):
    import urllib.error
    import urllib.request

    def _absent(*args, **kwargs):
        raise urllib.error.HTTPError("u", 404, "no replay", {}, None)

    monkeypatch.setattr(share, "is_web", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", _absent)
    download = share.fetch_log(MATCH)
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
    for _ in range(200):
        if download.poll()[0] != share.PENDING:
            break
        time.sleep(0.01)
    assert download.poll() == (share.ERROR, "")
