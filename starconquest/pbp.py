"""Play-by-post: one seat of a shared match, held by a person at their own pace.

Each player opens a link carrying a match id and their seat's token. They enter
orders for the live turn exactly as in a single-player game, submit, and wait;
when every seat is in, the turn resolves and everyone sees the new board. Nobody
keeps an appointment and nobody passes a laptop around.

**The server holds no board.** It stores each seat's orders per turn and the log
so far, and that is enough, because a board is a pure function of the settings,
the seed and every turn's orders and dice — which is exactly what
``replay.reconstruct`` rebuilds while asking no seat to decide. So the position
is rebuilt *here*, on each client, from what the server merely keeps. A
finished play-by-post match is therefore an ordinary ``GameLog``: it resumes,
reviews, scrubs and verifies like any other, with nothing taught about it.

That shape decides four things worth stating plainly:

* **Turns resolve wherever somebody is looking, once.** When the last seat
  submits, whichever client notices runs the turn locally and uploads the log.
  That log is then *the* record of the turn: every other client applies it and
  nobody decides the turn again. Two clients resolving together is settled by
  the endpoint — the first upload wins, and the second client throws its own
  result away and rebuilds from the winner's.
* **A bot decides exactly once.** ``knower`` and ``marshal`` stop searching on a
  wall clock, so the same position can yield different orders on a fast
  machine and a slow one. Re-deciding a turn on every client would fork the
  match; applying the resolver's record cannot.
* **The live turn's rng is derived, never carried** (``reseed``). A rebuilt
  board cannot know where a continuously played one's rng would stand, so the
  resolver seeds it from the seed and the turn instead.
* **Fog is honest, not enforced**, and so is the resolver — but only as far as
  it has to be. A client holds the whole log and could reconstruct any seat's
  view, and the resolver writes the bots' orders everyone applies. It cannot
  write a person's orders (stored under their own seat's token; ``match_log``
  refuses a log that files one they did not send), and it cannot write the dice:
  they are rolled after every order is fixed, from an rng derived for the turn,
  so every client re-rolls them and refuses a turn that disagrees
  (``verify_turn``).

``board_digest`` (in ``replay``) is the resolver's report of the board it landed
on, kept beside its order row — a record, and a tripwire for a log that does not
reproduce. It is emphatically not an anti-cheat measure — a client that would
lie about its digest would lie about its log.

Pure core: no pygame, and the network is somebody else's (``share``-style
``Request`` objects the shell polls once a frame, never awaited).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from . import engine, replay, webstore
from .model import GameState, Order
from .paths import (
    LEADERBOARD_PBP_PATH,
    WEB_PBP_BODY_KEY,
    WEB_PBP_SEATS_KEY,
    WEB_PBP_STATE_KEY,
    is_web,
)
from .settings import Settings, build_state

# A seat token as the endpoint mints one: 128 bits as hex.
TOKEN_CHARS = 32

# How long a seat has to take its turn before the clock runs out on it. Two days
# is what the format is for: play-by-post exists so that nobody keeps an
# appointment, and a deadline short enough to be missed by an ordinary weekend
# would put one back. The first miss only holds (see ``lapse_orders``), so what
# this really sets is how long a match waits before it is allowed to keep moving.
DEADLINE_HOURS = 48


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


def parse_link(token_text: str) -> tuple[str, str] | None:
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


def seat_for(match_id: str) -> Seat | None:
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
    # ``{seat: "hold" | "bot"}`` for the seats the clock has now run out on, and
    # empty until it has. The endpoint decides it — whose turn has lapsed and
    # what it costs them is policy, and policy lives in one place — and publishes
    # it because only a client can act on it: a bot's orders need an engine.
    lapsed: dict[int, str] = field(default_factory=dict)
    log: str = ""
    finished: bool = False
    rules_version: int = 1
    deadline_hours: int | None = None
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


def _lapsed_from(raw) -> dict[int, str]:
    """``{seat: action}`` out of the wire, keeping only actions we can carry out.

    Tolerant like every other decoder here, and deliberately closed rather than
    open: an action this build does not know is one it cannot file orders for, so
    it is dropped and the seat simply keeps waiting — which is the safe way for a
    client and an endpoint on different deploys to disagree.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for seat, action in raw.items():
        try:
            pid = int(seat)
        except (TypeError, ValueError):
            continue
        if action in ("hold", "bot"):
            out[pid] = action
    return out


def match_from_dict(data: dict) -> Match | None:
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
        lapsed=_lapsed_from(data.get("lapsed")),
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
    """Resolve the live turn onto a board, recording it into ``log``.

    The step ``resolve`` takes, and the one ``main.resolve_turn`` mirrors for the
    board on screen: every seat's orders applied in ``engine``'s fixed sequence.
    ``resolve`` passes them all in already decided (``turn_orders``); ``decide``
    is here for a caller that wants any seat left out asked instead.
    """
    record = engine.end_turn(state, seat_orders=orders, decide=decide,
                             on_event=on_event)
    log.record_turn(record)
    if state.winner is not None:
        log.mark_finished(state.winner)
    return record


def match_log(match: Match) -> replay.GameLog | None:
    """The match so far, as the endpoint stores it. None if it cannot be trusted.

    The log is the record of every resolved turn — each seat's orders and every
    combat draw — uploaded by whichever client resolved it, and it is **the**
    record: nobody else decides a resolved turn again. That is what keeps a bot on
    a wall-clock budget (``knower``, ``marshal``) from deciding a turn one way on a
    fast machine and another way on a slow one; it decides once, where the turn
    was resolved, and every other client applies what it decided.

    Trusting the resolver with the *bots'* orders and the dice is the same trust
    fog already asks for (see the module doc). Trusting it with a *person's*
    orders is not, and it does not have to be asked: those are stored, by their
    own seats, under their own tokens. So every order the log files under a seat
    with a stored row must be one that row holds. A log may carry fewer — the
    engine drops an order out of a system that was lost before it launched — but
    never one a person did not send.
    """
    if not match.log:
        if match.turn != 0:
            return None
        return replay.GameLog(seed=match.seed, settings=match.settings.to_dict(),
                              match_id=match.match_id)
    try:
        log = replay.GameLog.decode(match.log)
    except Exception:  # noqa: BLE001 — any undecodable blob is simply untrusted
        return None
    if log.turn_count != match.turn or log.seed != match.seed:
        return None
    for turn in range(log.turn_count):
        rows = match.orders_for_turn(turn)
        for seat in set(rows) | set(match.seats):
            sent = [(o.source_id, o.dest_id, o.ships) for o in rows.get(seat, [])]
            for order in log.orders_for(turn):
                if order.owner_id != seat:
                    continue
                key = (order.source_id, order.dest_id, order.ships)
                if key not in sent:
                    return None
                sent.remove(key)
    log.match_id = match.match_id
    return log


def rebuild(match: Match) -> tuple[GameState, replay.GameLog] | None:
    """The live board, and the log of the match so far. None if the log is bad.

    ``replay.reconstruct`` over the stored log: recorded orders, recorded dice,
    and no seat asked to decide anything — so every client lands on exactly the
    board the resolver did, however fast its machine is.
    """
    log = match_log(match)
    if log is None:
        return None
    state, _ = replay.reconstruct(log)
    seat_people(state, match.seats)
    return state, log


def reseed(state: GameState, seed: int) -> None:
    """Put ``state.rng`` where the live turn's decisions and dice start from.

    Derived from the seed and the turn rather than carried over from the turn
    before, because a rebuilt board cannot carry it: ``reconstruct`` deals the
    recorded dice and asks no bot to decide, so the rng never moves, and where a
    continuously played board's rng would stand depends on every draw every bot
    ever made. Deriving it makes that question go away — the rule
    ``botio.decide_seed`` and ``settings.resolve_strategy`` already follow.
    Called by every path that *resolves* a turn, and by none that replay one.
    """
    state.rng.seed(f"{seed}:pbp:{state.turn}")


def lapse_orders(state: GameState, match: Match, decide=None,
                 skip: int = 0) -> dict[int, list[dict]]:
    """The orders to file for whoever has let the clock run out.

    A **hold** is no orders at all, and the endpoint forces it empty whatever is
    sent — so what this really produces is the **bot** case, which has to come
    from a client because the endpoint has no engine and never will.

    ``skip`` is the seat at this keyboard, and leaving it out is the difference
    between a deadline that keeps a match moving and one that plays it for you:
    somebody who opens the game two days late is *here*, and filing their hold
    the moment they arrive would take the turn away from the one person who was
    about to take it. Any other client may still file it on their behalf, which
    is the whole point — but not this one, and not while they are looking at it.

    Computed on a **copy of the board**: ``decide`` is not promised to leave a
    board or its rng alone, and the live board is the one this client goes on to
    play. The copy is thrown away and only the orders travel, so a lapsed seat's
    bot decides exactly once, on one client, and everybody else applies what it
    decided — which is what the stored order rows are for.
    """
    filing: dict[int, list[dict]] = {}
    for seat, action in sorted(match.lapsed.items()):
        if seat == skip:
            continue
        if action != "bot" or decide is None:
            filing[seat] = []
            continue
        # A copy per seat rather than one for them all: `decide` is not promised
        # to leave a board alone, and a lapse is rare enough to pay for the doubt.
        orders = decide(copy.deepcopy(state), seat)
        filing[seat] = [{"src": o.source_id, "dst": o.dest_id, "ships": o.ships}
                        for o in orders]
    return filing


def resolve(match: Match, decide=None, on_event=None
            ) -> tuple[GameState, replay.GameLog, str] | None:
    """Play the live turn out, returning the new board, log and board digest.

    Only meaningful once ``match.ready``; the caller checks that. None if the
    stored log cannot be trusted. This is the one place a bot seat decides.

    ``decide`` is how a seat *not* in the roster gets played; it is injected
    rather than imported for the reason the engine's own is — this module is
    pure core and must not depend on ``ai``. Left out, every such seat holds.

    ``on_event`` reports only the live turn, never the rebuild that precedes it:
    what a player watches is the turn that just happened, not the history they
    already saw.
    """
    rebuilt = rebuild(match)
    if rebuilt is None:
        return None
    state, log = rebuilt
    reseed(state, match.seed)
    advance(state, log, turn_orders(state, match, decide), None, on_event)
    return state, log, replay.digest_hex(state)


def turn_orders(state: GameState, match: Match, decide=None) -> dict[int, list[Order]]:
    """Every seat's orders for the live turn: the stored rows, plus each bot's.

    The bots decide on a **scratch copy**, in ascending seat order — the very
    sequence ``engine._collect_orders`` would have asked them in, sharing one
    board and one rng stream between them, so an oracle that models the draws
    of the seats before it still models them right. What the copy spares is the
    live rng, which is left exactly where ``reseed`` put it. The turn is then run
    with every seat's orders already fixed, so its dice are a pure function of
    the seed, the turn and the orders — which is what lets every other client
    check them (``verify_turn``). Only the bots' own orders stay unverifiable,
    and must while they decide on a clock.
    """
    orders = match.orders_for_turn(state.turn)
    if decide is None:
        return orders
    scratch = copy.deepcopy(state)
    for pid in sorted(scratch.players):
        player = scratch.players[pid]
        if player.is_neutral or player.is_human or not player.alive or pid in orders:
            continue
        orders[pid] = decide(scratch, pid)
    return orders


def verify_turn(state: GameState, record: engine.TurnRecord, seed: int) -> bool:
    """Whether ``record`` is what its orders really roll on ``state``.

    Re-runs the turn on a copy with the recorded orders and freshly derived dice
    and compares the dice. The resolver is trusted with the bots' orders — they
    decide on a clock, so nothing could check them — but not with the dice: a
    log that rolled its own would stop here instead of being applied.
    """
    scratch = copy.deepcopy(state)
    reseed(scratch, seed)
    rolled = engine.end_turn(scratch, script=engine.TurnRecord(list(record.orders), []))
    return rolled.dice == list(record.dice)


def settled_turn(match: Match, turn: int) -> engine.TurnRecord | None:
    """The record of ``turn`` as resolved elsewhere, for playing onto a live board."""
    log = match_log(match)
    if log is None or turn >= log.turn_count:
        return None
    return log.script_for(turn)


def shareable(log: replay.GameLog) -> replay.GameLog:
    """``log`` as it goes to the endpoint: without our standing forwarding rules.

    Rules are a local convenience (``Ui.auto_forward``) the log records per turn
    so a solo game resumes with them; in a shared match they are one player's
    plan, and every other client opens from this log.
    """
    out = copy.deepcopy(log)
    for entry in out.turns:
        if isinstance(entry, dict):
            entry.pop("rules", None)
    return out


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

# How many web mailboxes calls rotate through. Each call gets its own, because a
# poll and a submission are routinely in flight together and one shared slot
# handed whichever landed first to whichever was collected first — a
# submission's reply read as a match is "unreadable". Rotated rather than unique
# so a call abandoned mid-flight (leaving a match) strands a bounded number of
# keys in the store, and wide enough that no slot comes round again while its
# last call could still be out.
_WEB_SLOTS = 16
_web_calls = 0


def _web_keys(slot: int) -> tuple[str, str]:
    """The ``(state, body)`` localStorage keys of one mailbox."""
    return f"{WEB_PBP_STATE_KEY}:{slot}", f"{WEB_PBP_BODY_KEY}:{slot}"


class Request:
    """One in-flight call to the endpoint, polled once a frame.

    ``share.Download``'s shape, generalised to carry a POST body and to hand back
    the response either way — a submission's answer says who else is still to
    move, so unlike a replay upload this is a request whose *result* is the
    point. Deliberately not a promise, a coroutine or a callback: the game loop
    is a frame loop and the one thing it must never do is wait.

    ``poll`` answers ``(state, body)``.
    """

    def __init__(self, web: bool, slot: int = 0) -> None:
        self._web = web
        self._slot = slot                           # which web mailbox is ours
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
        state_key, body_key = _web_keys(self._slot)
        state = webstore.get(state_key)
        if state in (OK, ERROR, MISSING, REFUSED):
            body = webstore.get(body_key) or ""
            _clear_web_slot(self._slot)
            return state, body
        return PENDING, ""


def _clear_web_slot(slot: int) -> None:
    """Empty the mailbox, so a stale answer is never read as a fresh one."""
    for key in _web_keys(slot):
        webstore.set(key, "")


def call(action: str, payload: dict | None = None, **params) -> Request | None:
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


def _call_web(url: str, body: str | None) -> Request | None:
    """A ``fetch`` that parks its own result where the poll can collect it.

    The handlers leave the slot in exactly one of the three states whatever
    happens: a non-2xx is as much an answer as a dead connection, and a promise
    with no rejection handler surfaces in the console as a crash. A non-2xx keeps
    its body, as the desktop path does: "stale turn" is the endpoint answering,
    and dropping it would tell the player the match could not be reached.
    """
    import platform as _platform

    global _web_calls
    mailbox = _web_calls % _WEB_SLOTS
    _web_calls += 1
    state, slot = (json.dumps(key) for key in _web_keys(mailbox))
    init = ("{method:'POST',headers:{'Content-Type':'application/json'},body:"
            f"{json.dumps(body)}}}") if body is not None else "{}"
    try:
        _platform.window.eval(
            f"localStorage.setItem({state},'{PENDING}');localStorage.removeItem({slot});"
            f"fetch({json.dumps(url)},{init}).then(function(r)"
            "{return r.text().then(function(t){"
            f"localStorage.setItem({slot},t);"
            f"localStorage.setItem({state},r.ok?'{OK}':r.status===404?'{MISSING}'"
            f":r.status===403?'{REFUSED}':'{ERROR}')}})}})"
            f".catch(function(e){{console.warn('pbp call failed',e);"
            f"localStorage.setItem({state},'{ERROR}')}})"
        )
        return Request(web=True, slot=mailbox)
    except Exception:  # noqa: BLE001
        return None


def _call_desktop(url: str, body: str | None) -> Request | None:
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


def parse_body(text: str) -> dict | None:
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
           deadline_hours: int | None = DEADLINE_HOURS,
           public: bool = False) -> Request | None:
    """Open a match, seating a person at each of ``seats``.

    A ``public`` match is listed on the leaderboard's lobby page and mints only
    seat 1's token here; every other seat's is minted when somebody claims it
    there (``?action=claim``), so the reply carries the creator's link alone.

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
        "public": public,
        "claimed": [1] if public else sorted(set(seats)),
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


def identify(match_id: str, token: str) -> Request | None:
    """Ask the endpoint which seat ``token`` holds.

    The seat is not in the link (see ``link_fragment``), so a client opening one
    has to be told once. It is asked once and then remembered (``remember``): a
    seat does not move, so every later launch reads it locally.
    """
    if not replay._MATCH_ID_RE.match(match_id) or not valid_token(token):
        return None
    return call("seat", {"match_id": match_id, "token": token})


def seat_from(body: dict, match_id: str, token: str) -> Seat | None:
    """The seat a ``?action=seat`` reply names, or None if it named none."""
    try:
        seat = Seat(match_id, int(body.get("seat", 0) or 0), token)
    except (TypeError, ValueError):
        return None
    return seat if seat.valid() else None


def fetch_state(match_id: str) -> Request | None:
    """Ask for a match's current state."""
    if not replay._MATCH_ID_RE.match(match_id):
        return None
    return call("state", match=match_id)


def submit(seat: Seat, turn: int, orders: list[Order],
           digest: str = "") -> Request | None:
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


def send_lapse(seat: Seat, turn: int,
               filing: dict[int, list[dict]]) -> Request | None:
    """File ``filing`` for the seats the clock has run out on.

    The one call that writes orders under somebody else's seat, which is why the
    endpoint recomputes the whole judgement rather than believing any of this:
    whether the deadline really passed, whether each named seat really is
    outstanding, and whether it holds or falls to its bot. What it takes from
    here is only the part it cannot work out — the orders themselves.
    """
    if not seat.valid() or not filing:
        return None
    return call("lapse", {
        "match_id": seat.match_id,
        "token": seat.token,
        "turn": turn,
        "seats": {str(pid): orders for pid, orders in sorted(filing.items())},
    })


def send_resolved(seat: Seat, turn: int, log: replay.GameLog, digest: str,
                  finished: bool) -> Request | None:
    """Hand back the turn this client just played out."""
    if not seat.valid():
        return None
    return call("resolve", {
        "match_id": seat.match_id,
        "token": seat.token,
        "turn": turn,
        "log": shareable(log).encoded(),
        "board_digest": digest,
        "finished": finished,
    })
