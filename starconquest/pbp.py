"""Play-by-post: one seat of a shared match, held by a person at their own pace.

Each player opens a link carrying a match id and their seat's token. They enter
orders for the live turn exactly as in a single-player game, submit, and wait;
when every seat is in, the turn resolves and everyone sees the new board. Nobody
keeps an appointment and nobody passes a laptop around.

**The server holds no board.** It stores each seat's orders per turn and the log
so far, and that is enough, because a board is a pure function of the settings,
the seed and every turn's orders and dice — which is exactly what
``replay.reconstruct`` rebuilds while asking no seat to decide. So the position
is rebuilt *here*, on each client, from inputs the server merely keeps. A
finished play-by-post match is therefore an ordinary ``GameLog``: it resumes,
reviews, scrubs and verifies like any other, with nothing taught about it.

That shape decides three things worth stating plainly:

* **Turns resolve wherever somebody is looking.** When the last seat submits,
  whichever client notices runs the turn locally and uploads the resulting log.
  Two clients noticing together is not a race — they compute the same turn from
  the same inputs, and the second upload is refused as stale rather than
  applied twice.
* **Order sequence is the whole of determinism.** Fleets are appended as they
  launch, arrivals are walked in that order and the combat dice are consumed
  along that walk, so two clients that applied the same orders in a different
  sequence would fight different battles. ``engine._collect_orders`` fixes the
  sequence — ascending seat id — and ``seat_orders`` is how this module hands it
  every seat's submission at once.
* **Fog is honest, not enforced.** A client holds the whole log and could
  reconstruct any seat's view. Fog still shapes play and is still worth having,
  but it is a convenience between people who chose to play each other, and the
  UI says so rather than implying a secrecy this design cannot keep.

``board_digest`` (in ``replay``) is the tripwire underneath: every client reports
what board it resolved a turn to, and the server keeps the first and compares the
rest. Honest clients agree by construction, so a mismatch means a stale build or
a real bug. It is emphatically not an anti-cheat measure — a client that would
lie about its digest would lie about its orders.

Pure core: no pygame, and the network is somebody else's (``share``-style
``Request`` objects the shell polls once a frame, never awaited).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from . import engine, replay, webstore
from .model import GameState, Order
from .paths import (LEADERBOARD_PBP_PATH, WEB_PBP_BODY_KEY, WEB_PBP_SEATS_KEY,
                    WEB_PBP_STATE_KEY, is_web)
from .settings import Settings, build_state

# A seat token as the endpoint mints one: 128 bits as hex.
TOKEN_CHARS = 32


def endpoint(action: str) -> str:
    """The pbp endpoint for ``action``, resolved now rather than at import.

    Per call because on the web it depends on where the page is served from — a
    deploy preview of the game talks to the matching preview of the board
    (``webstore.leaderboard_origin``). Blank when no origin resolves at all,
    which is how every leaderboard feature switches itself off.
    """
    base = webstore.leaderboard_url(LEADERBOARD_PBP_PATH)
    return f"{base}?action={action}" if base else ""


def configured() -> bool:
    """Whether there is an endpoint to talk to at all."""
    return bool(endpoint("state"))


def valid_token(token: str) -> bool:
    return (isinstance(token, str) and len(token) == TOKEN_CHARS
            and all(c in "0123456789abcdef" for c in token))


@dataclass
class Seat:
    """A seat this installation holds in a match: what a link grants."""

    match_id: str
    seat: int
    token: str

    def valid(self) -> bool:
        return (bool(replay._MATCH_ID_RE.match(self.match_id))
                and 1 <= self.seat <= 6 and valid_token(self.token))


# --------------------------------------------------------------------------- #
# The link
# --------------------------------------------------------------------------- #
# `#pbp=<match id>:<token>`, beside the `#log=<id>` a Watch link already uses.
# The seat is not in the link: the endpoint knows which seat a token belongs to,
# so putting it there would be a second claim to keep in step with the first.
PBP_FRAGMENT = "pbp="


def parse_link(token_text: str) -> Optional[tuple[str, str]]:
    """``(match_id, token)`` out of a ``#pbp=…`` fragment, or None.

    Tolerant in the same spirit as ``Settings.from_token``: a fragment that is
    not one of ours, or is malformed, is simply not a play-by-post request.
    """
    if not token_text.startswith(PBP_FRAGMENT):
        return None
    rest = token_text[len(PBP_FRAGMENT):]
    match_id, _, token = rest.partition(":")
    if not replay._MATCH_ID_RE.match(match_id) or not valid_token(token):
        return None
    return match_id, token


def link_fragment(match_id: str, token: str) -> str:
    """The fragment to hand a player, for the link that seats them."""
    return f"{PBP_FRAGMENT}{match_id}:{token}"


# --------------------------------------------------------------------------- #
# Remembering a seat
# --------------------------------------------------------------------------- #
def remember(seat: Seat) -> bool:
    """Keep ``seat``'s token so reopening the game returns to the match.

    Stored beside the other local preferences, never on ``Settings``: a token is
    the one thing that can move a seat's ships, and `Settings` travels in every
    shared link. Best-effort like every other store write.
    """
    if not seat.valid():
        return False
    seats = remembered()
    seats[seat.match_id] = {"seat": seat.seat, "token": seat.token}
    return webstore.set(WEB_PBP_SEATS_KEY, json.dumps(seats))


def remembered() -> dict:
    """Every seat this installation holds, by match id. ``{}`` on any failure."""
    try:
        seats = json.loads(webstore.get(WEB_PBP_SEATS_KEY) or "{}")
    except (ValueError, TypeError):
        return {}
    return seats if isinstance(seats, dict) else {}


def seat_for(match_id: str) -> Optional[Seat]:
    """The seat we hold in ``match_id``, if we have been handed one."""
    row = remembered().get(match_id)
    if not isinstance(row, dict):
        return None
    seat = Seat(match_id, int(row.get("seat", 0) or 0), str(row.get("token", "")))
    return seat if seat.valid() else None


def forget(match_id: str) -> bool:
    """Drop a match's token — a match that is over, or one we are done with."""
    seats = remembered()
    if match_id not in seats:
        return False
    del seats[match_id]
    return webstore.set(WEB_PBP_SEATS_KEY, json.dumps(seats))


# --------------------------------------------------------------------------- #
# A match, as the endpoint describes it
# --------------------------------------------------------------------------- #
@dataclass
class Match:
    """What ``?action=state`` says about a match, parsed and checked.

    ``turns`` is every *resolved* turn's submissions; ``submitted`` is who has
    sent orders for the live one. The board is not here and never will be — it is
    rebuilt from ``settings``/``seed`` plus those turns.
    """

    match_id: str
    settings: Settings
    seed: int
    seats: list[int]
    turn: int
    submitted: list[int]
    turns: list[dict] = field(default_factory=list)
    log: str = ""
    finished: bool = False
    rules_version: int = 1
    deadline_hours: Optional[int] = None
    turn_opened_at: str = ""

    @property
    def waiting(self) -> list[int]:
        """Seats the live turn is still waiting on."""
        return [s for s in self.seats if s not in self.submitted]

    @property
    def ready(self) -> bool:
        """Whether every seat is in and the turn can be resolved."""
        return not self.finished and not self.waiting

    def has_submitted(self, seat: int) -> bool:
        return seat in self.submitted

    def orders_for_turn(self, turn: int) -> dict[int, list[Order]]:
        """``{seat: orders}`` for a resolved turn, shaped for ``end_turn``.

        The owner is stamped here, from the seat the row is filed under — the
        wire carries none, exactly as it does not for a bot (``botio``).
        """
        out: dict[int, list[Order]] = {}
        for row in self.turns:
            if row.get("turn") != turn:
                continue
            seat = int(row.get("seat", 0) or 0)
            orders = []
            for item in row.get("orders_json") or []:
                try:
                    orders.append(Order(seat, int(item["src"]), int(item["dst"]),
                                        int(item["ships"])))
                except (KeyError, TypeError, ValueError):
                    continue        # a malformed row is dropped, never coerced
            out[seat] = orders
        return out


def match_from_dict(data: dict) -> Optional[Match]:
    """Parse ``?action=state``. None if it cannot describe a match at all.

    Tolerant like the rest of this codebase's decoders — a field missing or of
    the wrong type leaves a default rather than raising — but a match with no
    usable id, setup or roster is not a match, and pretending otherwise would
    put a broken board in front of somebody.
    """
    if not isinstance(data, dict):
        return None
    match_id = str(data.get("match_id", ""))
    if not replay._MATCH_ID_RE.match(match_id):
        return None
    raw_settings = data.get("settings_json")
    if not isinstance(raw_settings, dict):
        return None
    seats = [int(s) for s in data.get("seats") or [] if isinstance(s, int)]
    if not seats:
        return None
    try:
        settings = Settings.from_dict(raw_settings)
    except (ValueError, TypeError):
        return None
    return Match(
        match_id=match_id,
        settings=settings,
        seed=int(data.get("seed", 0) or 0),
        seats=sorted(seats),
        turn=int(data.get("turn", 0) or 0),
        submitted=sorted(int(s) for s in data.get("submitted") or []),
        turns=[row for row in (data.get("turns") or []) if isinstance(row, dict)],
        log=str(data.get("log", "") or ""),
        finished=bool(data.get("finished", False)),
        rules_version=int(data.get("rules_version", 1) or 1),
        deadline_hours=data.get("deadline_hours"),
        turn_opened_at=str(data.get("turn_opened_at", "") or ""),
    )


# --------------------------------------------------------------------------- #
# Rebuilding the board
# --------------------------------------------------------------------------- #
def seat_people(state: GameState, seats: list[int]) -> None:
    """Mark exactly ``seats`` as held by people, and every other seat as not.

    A match's roster is who is *playing it by post*; any other live seat is a bot
    and is decided for. The distinction has to be made identically on every
    client — it decides which seats ``engine._collect_orders`` asks ``decide``
    about, and which ones an oracle opponent guesses at rather than simulates —
    so it is derived here from the roster the server stores, never from who
    happens to be looking.

    Set outright rather than claimed one seat at a time (``engine._claim_seat``,
    which only ever adds), because in a shared match the roster is the whole
    truth about who the people are: ``mapgen`` stamps seat 1 regardless, and a
    match seated at 2 and 3 would otherwise carry a phantom person at 1 who held
    every turn forever. It is the same reason ``build_state`` clears that stamp
    for an all-bot game.
    """
    roster = set(seats)
    for pid, player in state.players.items():
        if not player.is_neutral:
            player.is_human = pid in roster


def seat_board(match: Match) -> GameState:
    """The opening position of ``match``, with its roster seated."""
    state = build_state(match.settings, match.seed)
    seat_people(state, match.seats)
    return state


def advance(state: GameState, log: replay.GameLog, orders: dict[int, list[Order]],
            decide=None, on_event=None) -> engine.TurnRecord:
    """Play one settled turn onto a live board, recording it into ``log``.

    The single step every path through this module takes — rebuilding the match
    from its opening, resolving the live turn, and (in the shell) advancing the
    board a player is already looking at. One implementation because the sequence
    of orders *is* the determinism: a second copy that collected them differently
    would fight different battles on the same inputs.
    """
    record = engine.end_turn(state, seat_orders=orders, decide=decide,
                             on_event=on_event)
    log.record_turn(record)
    if state.winner is not None:
        log.mark_finished(state.winner)
    return record


def rebuild(match: Match, decide=None) -> tuple[GameState, replay.GameLog]:
    """The live board, and a log of the match so far.

    Every resolved turn is replayed through the engine from the stored orders,
    which is the same thing ``replay.reconstruct`` does for a saved game — except
    the dice are *drawn* here rather than replayed, because the server never
    stored any. It does not need to: ``state.rng`` is seeded from the match seed,
    so the same orders in the same sequence draw the same numbers on every
    client. That is the determinism the whole design rests on, and
    ``replay.digest_hex`` is what checks it held.

    ``decide`` is how a seat *not* in the roster gets played: a match may seat two
    people against two bots, and a bot still has to take its turn. It is injected
    rather than imported for the reason the engine's own is (``engine.end_turn``)
    — this module is pure core and must not depend on ``ai``. Left out, every
    such seat simply holds; pass ``ai.decide`` on any client that can rebuild a
    match with bots in it, and pass it on *all* of them, because whether a bot
    moved is part of the position.
    """
    state = seat_board(match)
    log = replay.GameLog(seed=match.seed, settings=match.settings.to_dict(),
                         match_id=match.match_id)
    for turn in range(match.turn):
        if state.winner is not None:
            break
        advance(state, log, match.orders_for_turn(turn), decide)
    if state.winner is not None:
        log.mark_finished(state.winner)
    return state, log


def resolve(match: Match, decide=None,
            on_event=None) -> tuple[GameState, replay.GameLog, str]:
    """Play the live turn out, returning the new board, log and board digest.

    Only meaningful once ``match.ready``; the caller checks that. The digest is
    what goes back to the server beside the log, so a second client resolving the
    same turn can be told it agreed.

    ``on_event`` reports only the live turn, never the rebuild that precedes it:
    what a player watches is the turn that just happened, not the history they
    already saw.
    """
    state, log = rebuild(match, decide)
    advance(state, log, match.orders_for_turn(match.turn), decide, on_event)
    return state, log, replay.digest_hex(state)


# --------------------------------------------------------------------------- #
# Talking to the endpoint, without ever making the frame wait
# --------------------------------------------------------------------------- #
# The states a poll can answer with, mirroring ``share``: the plumbing worked and
# there is a body, the plumbing worked and there is nothing there, or it did not
# work. Kept as separate values because they send the player somewhere different.
#
# ``REFUSED`` is this module's own addition to that set, and it earns its place:
# a 403 is the endpoint saying "not with that token", which is the one failure
# here a player can actually do something about — check the link they were sent.
# Off the web the body would say so too, but a browser ``fetch`` that rejects
# hands the handler a status and nothing else, so the distinction has to survive
# in the state or it does not survive at all.
PENDING, OK, ERROR, MISSING, REFUSED = (
    "pending", "ok", "error", "missing", "refused")

# Long enough for a slow phone, short enough that a daemon thread is gone well
# before anyone quits. Same figure ``share`` uses, for the same reason.
_TIMEOUT = 20


class Request:
    """One in-flight call to the endpoint, polled once a frame.

    ``share.Download``'s shape, generalised to carry a POST body and to hand back
    the response either way — a submission's answer says who else is still to
    move, so unlike a replay upload this is a request whose *result* is the
    point. Deliberately not a promise, a coroutine or a callback: the game loop
    is a frame loop and the one thing it must never do is wait.

    ``poll`` answers ``(state, body)``.
    """

    def __init__(self, web: bool) -> None:
        self._web = web
        self._result: list[tuple[str, str]] = []   # a one-slot mailbox

    def poll(self) -> tuple[str, str]:
        if self._web:
            return self._poll_web()
        return self._result[0] if self._result else (PENDING, "")

    def _poll_web(self) -> tuple[str, str]:
        """Collect whatever the fetch parked in localStorage.

        Read through ``webstore.get`` rather than by evaluating JS, because that
        is the read path the rest of the game proves works every launch. Handing
        JavaScript a string to *store* and reading it back through the accessor
        we already trust avoids depending on ``window.eval`` returning a value,
        which nothing else here needs (``share`` documents the same reasoning).
        """
        state = webstore.get(WEB_PBP_STATE_KEY)
        if state == OK:
            body = webstore.get(WEB_PBP_BODY_KEY)
            _clear_web_slot()
            return OK, body
        if state in (ERROR, MISSING, REFUSED):
            _clear_web_slot()
            return state, ""
        return PENDING, ""


def _clear_web_slot() -> None:
    """Empty the mailbox, so a stale answer is never read as a fresh one."""
    webstore.set(WEB_PBP_STATE_KEY, "")
    webstore.set(WEB_PBP_BODY_KEY, "")


def call(action: str, payload: Optional[dict] = None, **params) -> Optional[Request]:
    """Start a call to ``action``. None if it cannot be attempted at all.

    ``payload`` makes it a POST. Nothing is awaited and nothing blocks; the
    caller polls the returned ``Request`` once a frame.
    """
    url = endpoint(action)
    if not url:
        return None
    for key, value in params.items():
        url += f"&{key}={value}"
    body = json.dumps(payload) if payload is not None else None
    return _call_web(url, body) if is_web() else _call_desktop(url, body)


def _call_web(url: str, body: Optional[str]) -> Optional[Request]:
    """A ``fetch`` that parks its own result where the poll can collect it.

    The handlers leave the slot in exactly one of the three states whatever
    happens: a non-2xx is as much an answer as a dead connection, and a promise
    with no rejection handler surfaces in the console as a crash.
    """
    import platform as _platform

    state, slot = json.dumps(WEB_PBP_STATE_KEY), json.dumps(WEB_PBP_BODY_KEY)
    init = ("{method:'POST',headers:{'Content-Type':'application/json'},body:"
            f"{json.dumps(body)}}}") if body is not None else "{}"
    try:
        _platform.window.eval(
            f"localStorage.setItem({state},'{PENDING}');localStorage.removeItem({slot});"
            f"fetch({json.dumps(url)},{init}).then(function(r)"
            "{return r.ok?r.text():Promise.reject(r.status)}).then(function(t)"
            f"{{localStorage.setItem({slot},t);localStorage.setItem({state},'{OK}')}})"
            f".catch(function(e){{console.warn('pbp call failed',e);"
            f"localStorage.setItem({state},e===404?'{MISSING}'"
            f":e===403?'{REFUSED}':'{ERROR}')}})"
        )
        return Request(web=True)
    except Exception:  # noqa: BLE001
        return None


def _call_desktop(url: str, body: Optional[str]) -> Optional[Request]:
    """The same call on a daemon thread, posting into the mailbox when done."""
    import threading
    import urllib.error
    import urllib.request

    request = Request(web=False)

    def _go() -> None:
        try:
            req = urllib.request.Request(
                url, data=body.encode() if body is not None else None,
                headers={"Content-Type": "application/json"},
                method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                request._result.append((OK, response.read().decode("utf-8")))
        except urllib.error.HTTPError as err:
            # "No such match" and "not with that token" are different things to
            # tell the player than "no answer at all", and only they get their own
            # state. Everything else — a refused submission, a stale turn, a turn
            # somebody else resolved first — comes back as a body worth reading.
            body_text = ""
            try:
                body_text = err.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                pass
            if err.code == 404:
                request._result.append((MISSING, body_text))
            elif err.code == 403:
                request._result.append((REFUSED, body_text))
            else:
                request._result.append((ERROR, body_text))
        except Exception:  # noqa: BLE001
            request._result.append((ERROR, ""))

    try:
        threading.Thread(target=_go, daemon=True, name="pbp-call").start()
        return request
    except RuntimeError:
        return None


def parse_body(text: str) -> Optional[dict]:
    """A reply's JSON object, or None. Never raises.

    Every answer from the endpoint arrives as an untrusted string — a body that
    is not JSON at all is an error page, a proxy's interstitial or a truncated
    read, none of which should reach a caller as an exception on a frame.
    """
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def create(match_id: str, settings: Settings, seed: int, seats: list[int],
           deadline_hours: Optional[int] = None) -> Optional[Request]:
    """Open a match, seating a person at each of ``seats``.

    The reply is the one and only time the seat tokens exist in the clear —
    nothing stores them, here or there, so whoever opened the match is who hands
    them out. ``rules_version`` rides along so a client on a later engine can say
    the match predates it rather than silently playing a different game.
    """
    if not replay._MATCH_ID_RE.match(match_id) or not seats:
        return None
    return call("create", {
        "match_id": match_id,
        "settings_json": settings.to_dict(),
        "seed": seed,
        "seats": sorted(set(seats)),
        "rules_version": engine.RULES_VERSION,
        "deadline_hours": deadline_hours,
    })


def tokens_from(body: dict, match_id: str) -> dict[int, str]:
    """``{seat: token}`` out of a ``?action=create`` reply, checked.

    A token we could not use is worse than none: it would be handed to a player
    as a link that cannot submit. So each one is validated exactly as a link's
    own is, and a seat whose token fails is simply absent.
    """
    if body.get("match_id") != match_id:
        return {}
    out: dict[int, str] = {}
    for seat, token in (body.get("tokens") or {}).items():
        try:
            pid = int(seat)
        except (TypeError, ValueError):
            continue
        if Seat(match_id, pid, str(token)).valid():
            out[pid] = str(token)
    return out


def identify(match_id: str, token: str) -> Optional[Request]:
    """Ask the endpoint which seat ``token`` holds.

    The seat is not in the link (see ``link_fragment``), so a client opening one
    has to be told once. It is asked once and then remembered (``remember``): a
    seat does not move, so every later launch reads it locally.
    """
    if not replay._MATCH_ID_RE.match(match_id) or not valid_token(token):
        return None
    return call("seat", {"match_id": match_id, "token": token})


def seat_from(body: dict, match_id: str, token: str) -> Optional[Seat]:
    """The seat a ``?action=seat`` reply names, or None if it named none."""
    try:
        seat = Seat(match_id, int(body.get("seat", 0) or 0), token)
    except (TypeError, ValueError):
        return None
    return seat if seat.valid() else None


def fetch_state(match_id: str) -> Optional[Request]:
    """Ask for a match's current state."""
    if not replay._MATCH_ID_RE.match(match_id):
        return None
    return call("state", match=match_id)


def submit(seat: Seat, turn: int, orders: list[Order],
           digest: str = "") -> Optional[Request]:
    """Send ``seat``'s orders for ``turn``.

    The owner is left off every order: the endpoint stamps the seat from the
    token, so a foreign order is not something this can express.
    """
    if not seat.valid():
        return None
    return call("submit", {
        "match_id": seat.match_id,
        "token": seat.token,
        "turn": turn,
        "orders": [{"src": o.source_id, "dst": o.dest_id, "ships": o.ships}
                   for o in orders],
        "board_digest": digest,
    })


def send_resolved(seat: Seat, turn: int, log: replay.GameLog, digest: str,
                  finished: bool) -> Optional[Request]:
    """Hand back the turn this client just played out."""
    if not seat.valid():
        return None
    return call("resolve", {
        "match_id": seat.match_id,
        "token": seat.token,
        "turn": turn,
        "log": log.encoded(),
        "board_digest": digest,
        "finished": finished,
    })
