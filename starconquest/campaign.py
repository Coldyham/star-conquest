"""Is the setup about to be played one of this week's campaign nodes?

The weekly campaign (``leaderboard/campaign.html``) is a meta-map of
challenges, and a win on a node is a campaign move only under its rules: next
to a node you hold, or held within the last half hour. Those rules live in
one place, ``leaderboard/js/campaign.mjs``, and this module never repeats
them. It asks the site (``/api/campaign``, which runs that same code) what a
win here would do for this player right now, then shows the answer: a confirm
before Start (``menu``) and a reminder in the top bar (``render``).

The one timer, the grace after losing a neighbour, arrives as an absolute time
on the *server's* clock, alongside the server's own ``now``, and is counted
down here from the moment the answer landed (``time.monotonic``), so a phone
with its clock set wrong still counts the right number of minutes.

Nothing here is load-bearing, in the same way as ``share``: off the web, or
with the board unreachable, there is simply no status, and the game plays
exactly as it would have. A lookup never blocks a frame or a Start press.
Like ``share``, it only reads: a lookup sends the setup's own token and the
name this browser already gave the board (``webstore.pbp_name``), and
nothing else.

Pure core: no pygame.
"""

from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from . import pbp, webstore
from .paths import LEADERBOARD_CAMPAIGN_PATH, is_web
from .settings import Settings

# How long a setup must sit unchanged on the menu before it is looked up, so
# stepping through seeds doesn't fire a request per press.
SETTLE_S = 0.6
# How often a node already found is asked about again, menu or game, so a
# neighbour lost mid-game starts its grace countdown without a reload.
REFRESH_S = 120.0


@dataclass(frozen=True)
class Status:
    """What the board said about one node, for one player, at one moment."""

    node_id: int
    kind: str                      # "home" | "field"
    why: str                       # attemptStatus's reason, see campaign.mjs
    can: bool                      # would a win posted now be a move?
    server_now: float              # the server's clock when it answered, ms
    received: float                # time.monotonic() when the answer landed
    grace_until: float | None = None   # server ms: post by then, or it isn't a move
    beat: tuple[int, int, str] | None = None   # (turns, lost, holder) to beat

    @property
    def name(self) -> str:
        return f"{'Home' if self.kind == 'home' else 'Node'} {self.node_id}"

    def left_ms(self, target: float | None, now: float | None = None) -> float | None:
        """Milliseconds until ``target`` (server ms), counted on our own clock
        from when the answer landed; None for no target."""
        if target is None:
            return None
        now = time.monotonic() if now is None else now
        return target - (self.server_now + (now - self.received) * 1000)


def parse(text: str, received: float) -> Status | None:
    """The endpoint's JSON, or None for anything that isn't a node answer.

    Tolerant like every reader of an untrusted body here: a shape it doesn't
    recognise is "no status", never an exception on a frame.
    """
    try:
        body = json.loads(text)
        node, status = body["node"], body["status"]
        beat = status.get("beat")

        def ms(value):
            return float(value) if isinstance(value, (int, float)) else None

        return Status(
            node_id=int(node["id"]),
            kind=str(node.get("kind", "field")),
            why=str(status.get("why", "")),
            can=bool(status.get("can")),
            server_now=float(body["now"]),
            received=received,
            grace_until=ms(status.get("graceUntil")),
            beat=(int(beat["turns"]), int(beat["lost"]), str(beat.get("name", "")))
            if isinstance(beat, dict) else None,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def minutes(ms: float) -> str:
    """'34 min', '1 h 05 min', rounded up, the board's own `waitLabel`."""
    total = max(1, math.ceil(ms / 60000))
    if total < 60:
        return f"{total} min"
    return f"{total // 60} h {total % 60:02d} min"


def _live(status: Status, target: float | None, now: float | None) -> float | None:
    left = status.left_ms(target, now)
    return left if left is not None and left > 0 else None


# Why a win here would not be a move, in the confirm's words.
_NOT_A_MOVE = {
    "home-taken": "This home is already claimed, and homes can't be taken.",
    "has-home": "You already have a home this week.",
    "no-home": "You haven't claimed a home this week, and a node is only taken "
               "from next to one you hold.",
    "not-adjacent": "You don't hold a node next to this one.",
}


def confirm(status: Status | None, now: float | None = None) -> tuple[str, list[str]] | None:
    """(title, lines) for the confirm before Start, or None when nothing needs
    saying: not a node, a plain open move, your own node, or no name to judge by.
    """
    if status is None or status.why in ("no-name", "own", "own-home"):
        return None
    title = f"Campaign {status.name.lower()}"
    if not status.can:
        reason = _NOT_A_MOVE.get(status.why)
        if reason is None:
            return None
        return title, [
            "A win here won't be a campaign move.",
            reason,
            "It still posts as an ordinary score.",
        ]
    if status.why != "grace":
        return None
    grace = _live(status, status.grace_until, now)
    if grace is None:
        return title, ["A win here won't be a campaign move.",
                       "The half hour after losing the node next to it is up."]
    lines = [f"Post a win within {minutes(grace)} or it won't be a move.",
             "You've lost the node next to this one, and a win beside it",
             "only counts for half an hour after."]
    if status.beat is not None:
        turns, lost, holder = status.beat
        lines.append(f"Held by {holder}: beat {turns} turns, {lost} lost.")
    return title, lines


def label(status: Status | None, now: float | None = None) -> str | None:
    """The top bar's one-line campaign reminder, or None for no node."""
    if status is None:
        return None
    name = status.name
    if status.why in ("own", "own-home"):
        return f"{name}: yours"
    if status.why == "no-name":
        return f"Campaign {name.lower()}"
    if not status.can:
        return f"{name}: not a move"
    if status.why == "grace":
        grace = _live(status, status.grace_until, now)
        if grace is None:
            return f"{name}: grace over"
        if status.beat is not None:
            turns, lost, _holder = status.beat
            return f"{name}: post within {minutes(grace)}, beat {turns}t / {lost} lost"
        return f"{name}: post within {minutes(grace)}"
    if status.beat is not None:
        turns, lost, holder = status.beat
        who = f"{holder}'s " if holder else ""
        return f"{name}: beat {who}{turns}t / {lost} lost"
    return f"{name}: open"


def enabled() -> bool:
    """Only the web build asks. It shares the board's origin and the name the
    board knows the player by; a desktop build has neither by default."""
    return is_web() and bool(webstore.leaderboard_url(LEADERBOARD_CAMPAIGN_PATH))


def lookup_token(settings: Settings) -> str | None:
    """The token to ask about, or None for a setup that can't be a node: a
    campaign node always has a concrete seed and is never hand-drawn."""
    if settings.seed is None or settings.custom_map is not None:
        return None
    return settings.to_token()


def fetch(token: str, name: str) -> pbp.Request | None:
    """Start one lookup. None if it can't be attempted."""
    endpoint = webstore.leaderboard_url(LEADERBOARD_CAMPAIGN_PATH)
    if not endpoint:
        return None
    return pbp.request(f"{endpoint}?{urlencode({'token': token, 'name': name})}")


class Watcher:
    """Keeps ``status`` current for whatever setup it is pumped with.

    Pumped once a frame by the menu (with the setup being edited) and by the
    game (with the one being played). A changed setup drops the old status at
    once, so a confirm is never raised from the previous setup's answer; it is
    looked up once it has settled, then refreshed every ``REFRESH_S``.

    A frame costs a field-by-field comparison with the setup last seen, nothing
    more: the token (deflated and base64'd) is only rebuilt when that differs,
    and ``enabled`` is asked once, since neither the platform nor the board's
    origin changes while the page is open.
    """

    def __init__(self) -> None:
        self.status: Status | None = None
        self._token: str | None = None
        self._request: pbp.Request | None = None
        self._changed = 0.0
        self._asked: float | None = None
        self._enabled: bool | None = None
        self._seen: Settings | None = None

    def pump(self, settings: Settings, now: float | None = None) -> None:
        if self._enabled is None:
            self._enabled = enabled()
        if not self._enabled:
            return
        now = time.monotonic() if now is None else now
        if settings != self._seen:
            # A copy, because the menu edits its Settings in place.
            self._seen = copy.deepcopy(settings)
            token = lookup_token(settings)
            if token != self._token:
                self._token, self.status, self._request = token, None, None
                self._changed, self._asked = now, None
        if self._request is not None:
            state, body = self._request.poll()
            if state == pbp.PENDING:
                return
            self._request = None
            if state == pbp.OK:
                self.status = parse(body, now)
            elif state == pbp.MISSING:
                self.status = None     # not a node (any more): nothing to show
            # Any other failure keeps the last answer rather than flickering it.
            return
        if self._token is None:
            return
        due = (now - self._changed >= SETTLE_S) if self._asked is None else (now - self._asked >= REFRESH_S)
        if due:
            self._asked = now
            self._request = fetch(self._token, webstore.pbp_name())
