"""Move a match's replay between the game and the leaderboard.

The one platform bridge that talks to a network (with ``webstore``), written
in the same defensive style: every call is guarded, nothing here is load-bearing, and a
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
  Quick post (*Post as <name>*, ``quick_post``) is the same button posting the
  score itself, so it is the same consent.
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

Two backends, because the browser has no sockets and a tool has no ``fetch``:

* In the browser, the worker asks the page for one ``fetch`` (``hostio``) and
  never looks at the promise — the upload lands or doesn't.
* Off it, ``urllib`` on a daemon thread, for the same reason: a POST must never
  hold up the caller for as long as the network feels like it.

So ``post_log`` returns whether the attempt was *started*, not whether it
arrived — the same contract ``webstore.open_url`` documents.

**Fetching is the one thing here that does read a response**, because a viewer
has nothing to show without it: ``fetch_log`` starts a download and hands back a
``Download`` the caller polls once a frame. Same two backends, same guards, and
the same "a failure is a state, not an exception" rule — a poll only ever answers
pending, ok or error. Nothing awaits: the worker answers one message at a
time, and blocking it on a network round trip would freeze the game.
"""

from __future__ import annotations

import json
import threading

from . import hostio, pbp, webstore
from .paths import (
    LEADERBOARD_LOG_PATH,
    LEADERBOARD_POST_MODULE_PATH,
    LEADERBOARD_REPLAY_PATH,
    is_web,
)
from .replay import _MATCH_ID_RE, GameLog

# How often a shared game checkpoints, in turns. Not a `config` constant: it is
# neither balance nor aesthetics, nothing in the menu tunes it, and it belongs to
# this module.
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
#
# Scoped to the leaderboard, which is the only thing that reads it: a posted
# score is a single-player result on a fixed setup, and a match seating several
# people is never posted at all. So this stays "did the player win" for the one
# kind of match that can carry a score, rather than becoming a per-seat question.
_HUMAN_SEAT = 1

# Long enough for a slow phone on a bad connection, short enough that the daemon
# thread is gone well before anyone quits the game.
_TIMEOUT = 20


def log_url() -> str:
    """The upload endpoint, resolved now rather than at import.

    Resolved per call because on the web it depends on where the page is being
    served from — a deploy preview posts to its own functions
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
    if hostio.in_worker():
        hostio.emit("fire", url=log_url(), body=body)
        return True
    return _post_desktop(body, headers)


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
# Quick post (a score, without the entry form)
# --------------------------------------------------------------------------- #
# How much of a name the overlay's button and summary show.
_NAME_SHOWN = 16


def _quick_post_module() -> str:
    """The board's posting module's URL, or ``""`` where quick post can't run:
    off the web (the module runs in the page) or with no board configured."""
    return webstore.leaderboard_url(LEADERBOARD_POST_MODULE_PATH) if is_web() else ""


def quick_post_name() -> str:
    """The name a win can be posted under from the overlay until the signed-in
    one answers (``signed_in_name``), or ``""`` for the entry form instead.

    The name the board and the play-by-post prompt remember
    (``webstore.pbp_name``), so it is set by posting once through the form.
    """
    return webstore.pbp_name() if _quick_post_module() else ""


def signed_in_name() -> pbp.Request | None:
    """Ask the board's posting module for the claimed name the signed-in player
    owns (``post.mjs``'s ``ownName``). It answers ``""`` when signed out or
    owning none, and None means it can't be asked (``_quick_post_module``).

    The board's own lookup, so the session token stays in the page and the game
    keeps no copy of who is signed in.
    """
    module = _quick_post_module()
    if not module:
        return None
    return pbp.bridge(module, "ownName", [])


def quick_post(token: str, name: str) -> pbp.Request | None:
    """Post ``token``'s score under ``name`` with the board's own posting module,
    without opening a tab. None if it cannot be tried.

    The module is the one the entry form posts with (``leaderboard/js/post.mjs``),
    loaded from the board on the game's own origin, so the game reimplements none
    of how a score is filed. It answers ``game_summary``'s aggregate as JSON
    (``post_summary`` reads it). The caller uploads the log first, as the entry
    form's button does.
    """
    module = webstore.leaderboard_url(LEADERBOARD_POST_MODULE_PATH)
    if not module or not name:
        return None
    return pbp.bridge(module, "quickPost", [token, name])


def short_name(name: str) -> str:
    """``name`` cut to fit a button."""
    name = name.strip()
    return name if len(name) <= _NAME_SHOWN else name[:_NAME_SHOWN - 1].rstrip() + "…"


def post_summary(body: str, turns: int, lost: int, name: str) -> tuple[str, str] | None:
    """``(game key, line)`` for the overlay from ``quick_post``'s answer, or None
    if it is not one.

    Only what an embargoed map's page shows anyone (the count, and who holds the
    best), so the line reads the same whether or not the map is embargoed. A
    best on our figures is ours when the name matches or several players share
    it; otherwise our score did not count (a match posted twice) and the best is
    somebody else's.
    """
    try:
        answer = json.loads(body)
        key = answer["game_key"]
        count = int(answer.get("score_count") or 0)
        best = answer.get("best_turns")
        best = None if best is None else (int(best), int(answer.get("best_lost") or 0))
        holders = int(answer.get("best_holders") or 1)
        leader = str(answer.get("best_user_name") or "").strip()
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    if not isinstance(key, str) or not key:
        return None
    scores = f"{count} scores on this map"
    if best is None:
        return key, f"Posted  ·  {scores}" if count > 1 else "Posted"
    if best == (turns, lost) and (holders > 1 or leader.lower() == name.strip().lower()):
        if count <= 1:
            return key, "Posted  ·  the first score on this map"
        if holders > 1:
            others = holders - 1
            return key, f"Posted  ·  joint best with {others} other{'s' if others > 1 else ''}  ·  {scores}"
        return key, f"Posted  ·  you lead  ·  {scores}"
    figures = f"{best[0]} turns, {best[1]} lost"
    return key, f"Posted  ·  best is {figures} by {short_name(leader or 'anonymous')}  ·  {scores}"


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

    def __init__(self) -> None:
        self._result: list[tuple[str, str]] = []   # a one-slot mailbox

    @classmethod
    def ready(cls, body: str) -> Download:
        """A download that has already arrived: a replay read off disk."""
        download = cls()
        download._result.append((OK, body))
        return download

    def poll(self) -> tuple[str, str]:
        return self._result[0] if self._result else (PENDING, "")


def fetch_log(match_id: str) -> Download | None:
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
    if hostio.in_worker():
        download = Download()

        def deliver(state: str, text: str) -> None:
            download._result.append((OK, text) if state == OK
                                    else (MISSING if state == MISSING else ERROR, ""))

        hostio.request(url, None, False, deliver)
        return download
    return _fetch_desktop(url)


def _fetch_desktop(url: str) -> Download | None:
    """The same download on a daemon thread, posting into the mailbox when done."""
    import urllib.error
    import urllib.request

    download = Download()

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
