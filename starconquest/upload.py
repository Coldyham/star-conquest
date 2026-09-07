"""Post a finished match's replay to the leaderboard, best-effort.

The fourth of the platform bridges (with ``webstore``, ``softkeyboard`` and the
web-only paths in ``main``/``menu``), written in the same defensive style: every
call is guarded, nothing here is load-bearing, and a failure is silent by design
— a player who has just won and pressed *Post to leaderboard* must not be shown a
network error about a side-effect they did not ask for. The score itself travels
in the link, exactly as it always did; this only adds the evidence behind it.

Why the game uploads a log at all: a score in a challenge link is unsigned and
unverifiable, so the board has to take it on trust. A *log* is not — it is the
match's inputs, and replaying it through the engine either reproduces the claimed
result or does not (``tools/verify_scores.py``). Posting the replay alongside the
score is what lets the board say "checked" instead of "claimed".

**Uploading happens only when the player posts a score.** Pressing that button is
the consent: nothing is sent for a game you merely played, abandoned or lost.

Two backends, because the browser has no sockets and the desktop has no ``fetch``:

* On the web, ``platform.window`` runs one ``fetch`` and we never look at the
  promise — the game keeps its frame rate and the upload lands or doesn't.
* Off it, ``urllib`` on a daemon thread, for the same reason: a POST blocking the
  event loop would freeze the win overlay for as long as the network felt like it.

So ``post_log`` returns whether the attempt was *started*, not whether it
arrived — the same contract ``webstore.open_url`` documents.

Pure core in the sense that matters: no pygame, and no import of anything the
headless tests can't load.
"""

from __future__ import annotations

import json
import threading

from .paths import LEADERBOARD_LOG_KEY, LEADERBOARD_LOG_URL, is_web
from .replay import GameLog

# Long enough for a slow phone on a bad connection, short enough that the daemon
# thread is gone well before anyone quits the game.
_TIMEOUT = 20


def configured() -> bool:
    """Whether an endpoint to upload to is set at all (see ``paths``)."""
    return bool(LEADERBOARD_LOG_URL and LEADERBOARD_LOG_KEY)


def row_for(log: GameLog, game_key: str) -> dict:
    """The ``game_logs`` row for ``log``, as the schema expects it.

    ``game_key`` is the setup's own checksum (``Settings.challenge_key``), stored
    so the worker can find a log without first knowing which match a score names.
    It carries no foreign key: the log is posted *before* the player reaches the
    submit form, so the ``games`` row it refers to may not exist yet — and may
    never, if they close the tab instead of posting.
    """
    return {
        "match_id": log.match_id,
        "game_key": game_key,
        "turns": log.turn_count,
        "log": log.encoded(),
    }


def post_log(log: GameLog, game_key: str) -> bool:
    """Upload ``log``. True means the attempt was started, not that it arrived.

    Never raises: an unconfigured endpoint, a missing browser API, a refused
    connection and a rejected row all read the same way to the caller, because
    there is nothing useful for it to do about any of them.
    """
    if not configured() or not log.turn_count:
        return False
    try:
        body = json.dumps([row_for(log, game_key)])
    except (TypeError, ValueError):
        return False
    headers = {
        "apikey": LEADERBOARD_LOG_KEY,
        "Authorization": f"Bearer {LEADERBOARD_LOG_KEY}",
        "Content-Type": "application/json",
        # Nothing here reads the response, and PostgREST returns the inserted row
        # in full unless told otherwise — which for a log means echoing back every
        # byte we just sent.
        "Prefer": "return=minimal",
    }
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
    game's site to Supabase's), which works because PostgREST answers the
    preflight these headers provoke — the same crossing the leaderboard's own
    pages already make.
    """
    import platform as _platform

    try:
        _platform.window.eval(
            "fetch(%s,{method:'POST',headers:%s,body:%s}).catch(function(){})"
            % (json.dumps(LEADERBOARD_LOG_URL), json.dumps(headers), json.dumps(body))
        )
        return True
    except Exception:  # noqa: BLE001 — a bridge that isn't there is not an error
        return False


def _post_desktop(body: str, headers: dict[str, str]) -> bool:
    """The same POST on a daemon thread, so the game loop never waits on it.

    Daemon so a hung connection cannot keep the process alive after the window
    closes; the upload is worth strictly less than quitting promptly.
    """
    import urllib.request

    request = urllib.request.Request(
        LEADERBOARD_LOG_URL, data=body.encode("utf-8"), method="POST")
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
