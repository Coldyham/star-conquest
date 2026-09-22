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
from .paths import LEADERBOARD_PBP_PATH, WEB_PBP_SEATS_KEY
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
def rebuild(match: Match) -> tuple[GameState, replay.GameLog]:
    """The live board, and a log of the match so far.

    Every resolved turn is replayed through the engine from the stored orders,
    which is the same thing ``replay.reconstruct`` does for a saved game — except
    the dice are *drawn* here rather than replayed, because the server never
    stored any. It does not need to: ``state.rng`` is seeded from the match seed,
    so the same orders in the same sequence draw the same numbers on every
    client. That is the determinism the whole design rests on, and
    ``replay.digest_hex`` is what checks it held.
    """
    state = build_state(match.settings, match.seed)
    log = replay.GameLog(seed=match.seed, settings=match.settings.to_dict(),
                         match_id=match.match_id)
    for turn in range(match.turn):
        if state.winner is not None:
            break
        record = engine.end_turn(state, seat_orders=match.orders_for_turn(turn))
        log.record_turn(record)
    if state.winner is not None:
        log.mark_finished(state.winner)
    return state, log


def resolve(match: Match) -> tuple[GameState, replay.GameLog, str]:
    """Play the live turn out, returning the new board, log and board digest.

    Only meaningful once ``match.ready``; the caller checks that. The digest is
    what goes back to the server beside the log, so a second client resolving the
    same turn can be told it agreed.
    """
    state, log = rebuild(match)
    record = engine.end_turn(state, seat_orders=match.orders_for_turn(match.turn))
    log.record_turn(record)
    if state.winner is not None:
        log.mark_finished(state.winner)
    return state, log, replay.digest_hex(state)
