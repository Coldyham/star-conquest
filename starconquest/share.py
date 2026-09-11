"""Move a match's replay between the game and the leaderboard.

The one platform bridge that talks to a network (with ``webstore``,
``softkeyboard`` and the web-only paths in ``main``/``menu``), written in the same
defensive style: every call is guarded, nothing here is load-bearing, and a
failure is silent by design — a player who has just won must not be shown a
network error about a side-effect they did not ask for.

Why upload a log at all: a score in a challenge link is unsigned and
unverifiable, so the board has to take it on trust. A *log* is not — it is the
match's inputs, and replaying it through the engine either reproduces the claimed
result or does not (``tools/verify_scores.py``). And a stored replay is a real
mid-game position that a *person* reached, which is the one thing a bot roster
measured against itself can never generate for itself.

**Two things send, and both are consented to.**

* Pressing *Post to leaderboard* uploads the match behind that score. Pressing
  the button is the consent; it works whether or not the preference below is on.
* With *Share replays* ticked on the menu (``webstore.share_games``, off until
  switched on), a game also uploads as it goes — every ``CHECKPOINT_TURNS`` turns
  and again when it ends, so a match that is abandoned rather than finished is
  still recorded up to wherever it was left. That is the point of it: the games
  worth having are the lost and abandoned ones, which no score ever carries.

Nothing else sends. Sharing a challenge link does not, and neither does a pure
autoplay demo (``hand_turns == 0``) — a bot-versus-bot game is reproducible from
its seed, so uploading one adds bytes and no information.

Uploads are append-only, so a game that checkpoints repeatedly leaves several
rows and the longest is the current one; the worker prunes the rest
(``tools/verify_scores.py --prune``). Nothing identifying the player is attached
to any of it: a row is a match id, a setup key and the moves. Grouping one
person's games across sessions would need a durable client id, which is a
tracking identifier by any other name and buys nothing this needs.

Two backends, because the browser has no sockets and the desktop has no ``fetch``:

* On the web, ``platform.window`` runs one ``fetch`` and we never look at the
  promise — the game keeps its frame rate and the upload lands or doesn't.
* Off it, ``urllib`` on a daemon thread, for the same reason: a POST blocking the
  event loop would freeze the win overlay for as long as the network felt like it.

So ``post_log`` returns whether the attempt was *started*, not whether it
arrived — the same contract ``webstore.open_url`` documents.

**Fetching is the one thing here that does read a response**, because a viewer
has nothing to show without it: ``fetch_log`` starts a download and hands back a
``Download`` the caller polls once a frame. Same two backends, same guards, and
the same "a failure is a state, not an exception" rule — a poll only ever answers
pending, ok or error. Nothing awaits: the browser build's loop yields once per
frame and blocking it on a network round trip would freeze the canvas.

On the web the result comes back through *localStorage* rather than a ``window``
property. Three bridge calls are involved in this feature and only two of them
are proven by the rest of the game: writing through ``window.eval`` (how a replay
is uploaded) and reading a stored string (how every preference and shared link
already works). ``eval`` *returning* a value is the third, is needed nowhere else,
and if it misbehaves it does so as an unexplained failure — so the fetch stores
its answer and the poll reads it the way everything else does.

Pure core in the sense that matters: no pygame, and no import of anything the
headless tests can't load.
"""

from __future__ import annotations

import json
import threading

from typing import Optional

from . import webstore
from .paths import (LEADERBOARD_LOG_PATH, LEADERBOARD_REPLAY_PATH,
                    WEB_REPLAY_BODY_KEY, WEB_REPLAY_STATE_KEY, is_web)
from .replay import _MATCH_ID_RE, GameLog

# How often a shared game checkpoints, in turns. Not a `config` constant: it is
# neither balance nor aesthetics, nothing in the menu tunes it, and it belongs to
# this module the way `main.FAST_FORWARD_MS` belongs to the loop.
#
# The trade it sets is how much of an abandoned game survives against how many
# superseded rows a finished one leaves. Measured whole games run 5-14 KiB
# encoded; at 25 turns a 150-turn match uploads ~6 times and the intermediate
# rows total roughly 3x the final one, which is nothing against a 500 MB database
# and is pruned anyway.
CHECKPOINT_TURNS = 25

# The seat the player holds. `mapgen._make_players` stamps `is_human` on pid 1, so
# a log's `winner` names a human win by being this — reconstructing the whole
# match just to ask `state.human()` would be an absurd price for one boolean.
_HUMAN_SEAT = 1

# Long enough for a slow phone on a bad connection, short enough that the daemon
# thread is gone well before anyone quits the game.
_TIMEOUT = 20


def log_url() -> str:
    """The upload endpoint, resolved now rather than at import.

    Resolved per call because on the web it depends on where the page is being
    served from — a deploy preview posts to the matching preview of the board
    (``webstore.leaderboard_origin``).
    """
    return webstore.leaderboard_url(LEADERBOARD_LOG_PATH)


def configured() -> bool:
    """Whether an endpoint to upload to is set at all (see ``paths``)."""
    return bool(log_url())


def worth_sending(log: GameLog) -> bool:
    """Whether ``log`` is a game anyone would want stored.

    An empty log has nothing in it, and a pure autoplay demo is a bot-versus-bot
    game that its seed already describes — the human seat has to have decided at
    least one turn for the replay to be evidence of anything.
    """
    return log.turn_count > 0 and log.hand_turns > 0


def due(log: GameLog, finished: bool, *, ours: bool = True) -> bool:
    """Whether a shared game should checkpoint now.

    Called once per resolved turn, so this is the cadence itself: every
    ``CHECKPOINT_TURNS`` turns, and again on the turn the match is decided so the
    stored replay ends where the game did rather than up to 24 turns short.

    ``ours`` is False while playing on from a *watched* replay (``Ui.watched``).
    That log carries somebody else's ``match_id``, and rows are keyed by it — so
    uploading would file our continuation as a longer version of their match, and
    the verifier, which takes the longest row for a match, would then replay ours
    against their posted score and call it a mismatch.

    Tested cheapest-first on purpose. Fast forward resolves a turn per *frame*,
    and the preference lives in a file off the web — so the store is consulted
    only on the turns a checkpoint could actually happen, rather than sixty times
    a second while a decided game plays itself out.
    """
    if not (finished or log.turn_count % CHECKPOINT_TURNS == 0):
        return False
    return ours and worth_sending(log) and webstore.share_games()


def row_for(log: GameLog, game_key: str) -> dict:
    """The ``game_logs`` row for ``log``, as the endpoint expects it.

    ``game_key`` is the setup's own checksum (``Settings.challenge_key``), stored
    so a replay can be found by the map it was played on without decoding every
    blob to ask. ``finished``/``won``/``hand`` are the same kind of index — a
    caller can pick out completed games, or wins, or games actually played by
    hand, without opening them. ``rules_version`` is one more: the board can
    decide whether a replay is still watchable exactly (``GameLog.is_current``,
    same rule the game itself uses to gate ``Watch``/resume) without decoding the
    blob to find out. All of them are claims by the client and the schema says
    so; the verifier trusts none of them, it replays the log.
    """
    return {
        "match_id": log.match_id,
        "game_key": game_key,
        "turns": log.turn_count,
        "finished": bool(log.finished),
        "won": bool(log.finished and log.winner == _HUMAN_SEAT),
        "hand": log.hand_turns,
        "rules_version": log.rules_version,
        "log": log.encoded(),
    }


def post_log(log: GameLog, game_key: str) -> bool:
    """Upload ``log``. True means the attempt was started, not that it arrived.

    Never raises: an unconfigured endpoint, a missing browser API, a refused
    connection and a rejected row all read the same way to the caller, because
    there is nothing useful for it to do about any of them.
    """
    if not configured() or not worth_sending(log):
        return False
    try:
        body = json.dumps(row_for(log, game_key))
    except (TypeError, ValueError):
        return False
    # No API key: the endpoint is the leaderboard's own function, and the key that
    # may actually write `game_logs` lives in that function's environment. There
    # is nothing to authenticate as from here.
    headers = {"Content-Type": "application/json"}
    return _post_web(body, headers) if is_web() else _post_desktop(body, headers)


def _post_web(body: str, headers: dict[str, str]) -> bool:
    """One fire-and-forget ``fetch`` through pygbag's JS bridge.

    Built as a JS source string because marshalling a nested object literal across
    the bridge is far more fragile than handing JavaScript the JSON it already
    speaks. Every value goes through ``json.dumps``, so the payload is a *quoted
    string literal* wherever it lands — a JSON string is a valid JS string, and
    base64url plus JSON escaping leaves nothing that could close the quote.

    The rejection handler is not optional: an unhandled promise rejection (an
    offline tab, a CORS refusal) surfaces as a console error on a page that is
    still mid-game, and looks like the game crashed.

    Two things this leans on, both worth knowing before changing them. ``eval`` is
    available because pygbag's own bridge needs it — a Content-Security-Policy
    strict enough to block it would stop the game booting long before this line,
    and neither ``netlify.toml`` sets one. And the request is cross-origin (the
    game's site to the leaderboard's), which is why the function answers the
    preflight and names the game's origin in its CORS headers.
    """
    import platform as _platform

    try:
        _platform.window.eval(
            "fetch(%s,{method:'POST',headers:%s,body:%s}).catch(function(){})"
            % (json.dumps(log_url()), json.dumps(headers), json.dumps(body))
        )
        return True
    except Exception:  # noqa: BLE001 — a bridge that isn't there is not an error
        return False


def _post_desktop(body: str, headers: dict[str, str]) -> bool:
    """The same POST on a daemon thread, so the game loop never waits on it.

    Daemon so a hung connection cannot keep the process alive after the window
    closes; the upload is worth strictly less than quitting promptly.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        log_url(), data=body.encode("utf-8"), method="POST")
    for name, value in headers.items():
        request.add_header(name, value)

    def _send() -> None:
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT):
                pass
        except Exception:  # noqa: BLE001 — best-effort, and nobody is listening
            pass

    try:
        threading.Thread(target=_send, daemon=True, name="log-upload").start()
        return True
    except RuntimeError:  # thread creation refused; not worth doing inline
        return False


# --------------------------------------------------------------------------- #
# Fetching (a replay to watch)
# --------------------------------------------------------------------------- #
# A poll answers with one of these. MISSING is split out from ERROR because the
# two send the player somewhere different: the plumbing worked and the row is not
# there, versus the plumbing did not work.
PENDING, OK, ERROR, MISSING = "pending", "ok", "error", "missing"


class Download:
    """An in-flight replay fetch, polled once a frame.

    ``poll`` returns ``(state, body)``: ``PENDING`` while it is still going, then
    ``OK`` with the text, ``MISSING`` if the board answered "no such replay", or
    ``ERROR`` for anything else. Deliberately not a promise, a coroutine or a
    callback — the game loop is a frame loop, and the one thing it must never do
    is wait.
    """

    def __init__(self, web: bool) -> None:
        self._web = web
        self._result: list[tuple[str, str]] = []   # a one-slot mailbox for the thread

    def poll(self) -> tuple[str, str]:
        if self._web:
            return self._poll_web()
        return self._result[0] if self._result else (PENDING, "")

    def _poll_web(self) -> tuple[str, str]:
        """Collect whatever the fetch has parked in localStorage.

        Read with ``webstore.get`` rather than by evaluating JS, because that is
        the read path the rest of the game already proves works every launch (the
        shared-settings token, the personal bests). Handing JavaScript a string to
        *store* and reading it back through the accessor we trust avoids depending
        on ``window.eval`` returning a value across the bridge, which nothing else
        here needs and which fails as a bare "error" if it misbehaves.

        The slot is cleared as soon as it is collected: a stale ``ok`` left by an
        earlier session would otherwise be read as an instant success carrying
        somebody else's replay.
        """
        state = webstore.get(WEB_REPLAY_STATE_KEY)
        if state == OK:
            body = webstore.get(WEB_REPLAY_BODY_KEY)
            _clear_web_slot()
            return OK, body
        if state in (ERROR, MISSING):
            _clear_web_slot()
            return state, ""
        return PENDING, ""


def _clear_web_slot() -> None:
    """Empty the download mailbox. Best-effort like every other store write."""
    webstore.set(WEB_REPLAY_STATE_KEY, "")
    webstore.set(WEB_REPLAY_BODY_KEY, "")


def fetch_log(match_id: str) -> Optional[Download]:
    """Start fetching the replay named ``match_id``. None if it cannot be tried.

    The id is checked here rather than trusted: it arrives from a URL fragment, so
    it goes into a request only once it looks like something the game itself
    minted.
    """
    endpoint = webstore.leaderboard_url(LEADERBOARD_REPLAY_PATH)
    if not endpoint or not _MATCH_ID_RE.match(match_id):
        return None
    url = f"{endpoint}?id={match_id}"
    # Printed rather than kept quiet: on the web this reaches the browser console,
    # and "which endpoint did it actually ask?" is the first question whenever a
    # replay will not open — the URL is derived from the page's own host, so it
    # differs between production, a branch deploy and a preview.
    print(f"fetching replay: {url}")
    return _fetch_web(url) if is_web() else _fetch_desktop(url)


def _fetch_web(url: str) -> Optional[Download]:
    """Kick off a ``fetch`` that parks its own result on ``window``.

    The handlers are written to leave the slot in exactly one of the three states
    whatever happens — a non-2xx is as much an error as a dead connection, and a
    promise with no rejection handler would surface in the console as a crash.
    """
    import platform as _platform

    state, body = json.dumps(WEB_REPLAY_STATE_KEY), json.dumps(WEB_REPLAY_BODY_KEY)
    try:
        _platform.window.eval(
            f"localStorage.setItem({state},'{PENDING}');localStorage.removeItem({body});"
            f"fetch({json.dumps(url)}).then(function(r)"
            "{return r.ok?r.text():Promise.reject(r.status)}).then(function(t)"
            f"{{localStorage.setItem({body},t);localStorage.setItem({state},'{OK}')}})"
            f".catch(function(e){{console.warn('replay fetch failed',e);"
            f"localStorage.setItem({state},e===404?'{MISSING}':'{ERROR}')}})"
        )
        return Download(web=True)
    except Exception:  # noqa: BLE001
        return None


def _fetch_desktop(url: str) -> Optional[Download]:
    """The same download on a daemon thread, posting into the mailbox when done."""
    import urllib.error
    import urllib.request

    download = Download(web=False)

    def _get() -> None:
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
                body = response.read().decode("utf-8")
            download._result.append((OK, body))
        except urllib.error.HTTPError as err:
            # 404 is the endpoint working and saying there is no such replay,
            # which is a different thing to tell the player than "no answer".
            download._result.append((MISSING if err.code == 404 else ERROR, ""))
        except Exception:  # noqa: BLE001 — anything else is simply "no answer"
            download._result.append((ERROR, ""))

    try:
        threading.Thread(target=_get, daemon=True, name="log-download").start()
        return download
    except RuntimeError:
        return None
