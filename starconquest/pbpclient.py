"""The play-by-post client: one shared match's calls, read cadence and verdicts.

What used to be a dozen locals in `main`'s frame loop, moved here unchanged so
the HTML build's session drives the same machine (`docs/design/html-frontend.md`).
Three calls can be in flight over a match's life and each has its own slot,
because they answer different questions and arrive out of order: ``ident``
asks which seat a link holds (once, ever), ``poll`` is the read that moves the
board, ``write`` is whatever submission or resolution is on its way out.
``make`` is ``?action=create``.

The frontend calls `open_link` at launch, `create` from the menu, `send` on End
Turn and `step` once a frame (or on a timer), and applies the `Outcome` each
returns: a line for the menu, a match opened or rebuilt, a turn to watch.
Nothing here waits; every call is a polled `pbp.Request`.
"""

from __future__ import annotations

from dataclasses import dataclass

from starconquest import ai, engine, pbp, replay, turnfilm, viewstate
from starconquest.app import (
    PBP_BAD_LOG_MSG,
    PBP_OPENING_MSG,
    PBP_OUTDATED_MSG,
    PBP_POLL_MS,
    PBP_REBUILD,
    PBP_RESOLVE,
    PBP_STEP,
    PBP_THROTTLED_MS,
    PBP_UNOPENED_MSG,
    PBP_UNREACHABLE_MSG,
    PBP_UNREADABLE_MSG,
    PBP_WAIT,
    open_match,
    pbp_adopt,
    pbp_handed_out,
    pbp_heard,
    pbp_idle_floor,
    pbp_lapse,
    pbp_open,
    pbp_opened,
    pbp_poll_delay,
    pbp_seat_links,
    pbp_send,
    pbp_spectate_link,
    pbp_spectator_floor,
    pbp_throttled,
    pbp_trouble,
    pbp_unwatchable,
    pbp_verdict,
    resolve_turn,
)
from starconquest.model import GameState
from starconquest.replay import GameLog
from starconquest.settings import Settings, fog_off
from starconquest.viewstate import Ui


@dataclass
class Outcome:
    """What one call did that the frontend has to apply."""

    status: tuple[str, bool] | None = None      # a line for the setup menu: text, ok
    game: tuple[GameState, Ui, GameLog] | None = None   # the match's board, newly opened or rebuilt
    opened: bool = False         # ...opened: go to the game scene, on `seed`
    rebuilt: bool = False        # ...rebuilt mid-match: drop any history review built on the old one
    seed: int | None = None
    reel: turnfilm.Reel | None = None   # a turn played onto the board, to watch
    played: bool = False         # a turn resolved: restart the play cadences
    created: bool = False        # a match was created: drop the resume prompt


class PbpClient:
    """One device's side of a shared match."""

    def __init__(self) -> None:
        self.seat: pbp.Seat | None = None
        self.invite: tuple[str, str] | None = None   # (match id, token) being opened or played
        self.ident: pbp.Request | None = None
        self.poll: pbp.Request | None = None
        self.write: pbp.Request | None = None
        # Minted here and sent rather than handed back, so a retry after a lost
        # reply opens a second match instead of quietly rewriting the first.
        self.make: pbp.Request | None = None
        self.making = ""
        self.watchable = False       # ...and whether its setup can be spectated
        self.accum = 0
        self.wait = PBP_POLL_MS      # how long `accum` runs before the next read
        self.failures = 0            # reads in a row that came back with no match
        self.read_line = ""          # the line the last failed read put up, if any
        self.resolving = False       # whether `write` is our own resolved turn
        self.stale = False           # our resolve was refused: rebuild on the next read
        self.last: pbp.Match | None = None   # the latest read, which a brief merges into
        self.idle = 0                # ms since the last input, which stretches the wait
        self.quiet = 0               # ms since the match last moved (a spectator's floor)
        self.moved: tuple | None = None   # (turn, seats waited on) when it last moved
        # Every other seat's link, from a match *we* just created — handed to `Ui`
        # (`pbp_invite`) the moment there is one, so the invite overlay opens on the
        # very first frame of the match rather than a status line that beat it there.
        self.invite_links: list[tuple[int, str]] = []

    # ------------------------------------------------------------------ #
    # Starting
    # ------------------------------------------------------------------ #
    def open_link(self, invite: tuple[str, str] | None, watching: str | None) -> tuple[str, bool] | None:
        """A launch's ``#pbp=`` seat link or ``#spectate=`` link: start the first
        call, and answer the menu's line for it (None when there is no link)."""
        if invite is not None:
            known = pbp.seat_for(invite[0])
            if known is not None and known.token == invite[1]:
                # Seated here before. A seat never moves, so the endpoint is asked
                # which one it is exactly once per link and the answer is kept.
                self.seat = known
                self.poll = pbp.fetch_state(invite[0])
            else:
                self.ident = pbp.identify(*invite)
            if self.poll is None and self.ident is None:
                print(f"cannot open match {invite[0]!r}: no endpoint is configured")
                return PBP_UNREACHABLE_MSG, False
            self.invite = invite
            return "Opening match...", True
        if watching is not None:
            # From no seat: the same read as a seat's, with nothing to identify
            # and nothing ever written.
            self.seat = pbp.spectator(watching)
            self.poll = pbp.fetch_state(watching)
            if self.poll is None:
                print(f"cannot watch match {watching!r}: no endpoint is configured")
                self.seat = None
                return PBP_UNREACHABLE_MSG, False
            self.invite = (watching, "")
            return "Opening match...", True
        return None

    def create(self, settings: Settings, seed: int, roster: list[int] | None, deadline_hours: int,
               public: bool, title: str, name: str) -> tuple[str, bool] | None:
        """Open the setup as a shared match. None while one is already being made."""
        if self.make is not None:
            return None
        self.making, self.make = pbp_open(settings, seed, roster, deadline_hours, public, title, name)
        self.watchable = fog_off(settings)
        if self.make is None:
            return PBP_UNOPENED_MSG, False
        return PBP_OPENING_MSG, True

    def send(self, state: GameState, ui: Ui) -> None:
        """End Turn in a shared match: send our orders, and wait for the rest."""
        if self.seat is not None and not self.seat.spectating and self.write is None:
            self.write = pbp_send(state, ui, self.seat)
            # A read already in flight was asked before our orders existed, and
            # landing after them it would put us back on the list of seats still
            # to move. The write's own follow-up read replaces it.
            self.poll = None

    def leave(self) -> None:
        """Left the match — back to the menu, a new map, a retry. Nothing in
        flight is about it anymore, and the seat itself stays remembered so
        the link still works."""
        self.seat, self.poll, self.write, self.ident = None, None, None, None
        self.failures, self.wait, self.read_line = 0, PBP_POLL_MS, ""
        self.resolving = self.stale = False
        self.last, self.moved, self.quiet = None, None, 0

    # ------------------------------------------------------------------ #
    # Each frame
    # ------------------------------------------------------------------ #
    def step(self, dt: int, state: GameState | None, ui: Ui | None, log: GameLog | None,
             settings: Settings, filming: bool) -> Outcome:
        """Collect whatever answered, act on it, and start the next read when due.

        Reads are held off while a film runs (``filming``): `Request.poll` empties
        its own mailbox, so an answer collected mid-playback is an answer thrown
        away — and, more to the point, a turn must never resolve out from under
        one being drawn.
        """
        out = Outcome()
        if self.make is not None:
            self._collect_make(out)
        if self.ident is not None and self.invite is not None:
            self._collect_ident(out)
        if self.write is not None:
            self._collect_write(ui)
        if self.poll is not None and not filming and self.seat is not None:
            state, ui, log = self._collect_poll(out, state, ui, log, settings)
        if ui is not None and not ui.in_pbp:
            self.leave()
        elif (ui is not None and self.seat is not None and self.poll is None
                and self.ident is None and self.write is None and state is not None
                and state.winner is None):
            self.accum += dt
            # A turn resolving or a seat submitting is the match moving, which
            # is what keeps a spectator's reads quick (`pbp_spectator_floor`).
            moved = (state.turn, ui.pbp_waiting)
            self.quiet = 0 if moved != self.moved else self.quiet + dt
            self.moved = moved
            idle_floor = (pbp_spectator_floor(self.quiet, self.idle) if ui.spectating
                          else pbp_idle_floor(self.idle))
            if self.accum >= max(self.wait, idle_floor):
                self.accum = 0
                # A brief is only asked for when the board stands where our last
                # read did; a board ahead of it (our own resolve) or a stale one
                # needs the whole match.
                have = (self.last.turn if self.last is not None and not self.stale
                        and self.last.turn == state.turn else None)
                self.poll = pbp.fetch_state(ui.pbp_match, have)
        return out

    def _collect_make(self, out: Outcome) -> None:
        assert self.make is not None
        status, body = self.make.poll()
        if status == pbp.PENDING:
            return
        self.make = None
        tokens = (pbp.tokens_from(pbp.parse_body(body) or {}, self.making)
                  if status == pbp.OK else {})
        if not tokens:
            print(f"opening match {self.making!r} ended in state {status!r}")
            out.status = (pbp_trouble(status, body), False)
        elif 1 not in tokens:
            # We asked for seat 1 and did not get it. Rather than guess which seat
            # is ours out of a roster we no longer recognise, hand the links over
            # and let a link seat us like anyone else.
            out.status = (pbp_handed_out(self.making, tokens), True)
        else:
            # Every seat's link, including our own, goes on the invite overlay the
            # moment there is a `Ui` to hang it off. Ours is there too and not
            # just remembered locally — `pbp.remember`'s token lives in this
            # browser's storage alone, so a reload, a cleared profile or opening
            # on another device has nothing else to recover the seat from. Then
            # the ordinary opening path takes over, exactly as it would for
            # somebody following the link we just sent them.
            self.seat = pbp.Seat(self.making, 1, tokens[1])
            pbp.remember(self.seat)
            self.invite = (self.seat.match_id, self.seat.token)
            self.poll = pbp.fetch_state(self.seat.match_id)
            out.created = True
            self.invite_links = pbp_seat_links(self.making, tokens)
            if self.watchable:
                self.invite_links.append((viewstate.SPECTATE_ROW, pbp_spectate_link(self.making)))
            out.status = ("Match created — copy each seat's link", True)

    def _collect_ident(self, out: Outcome) -> None:
        assert self.ident is not None and self.invite is not None
        status, body = self.ident.poll()
        if status == pbp.PENDING:
            return
        self.ident = None
        seat = (pbp.seat_from(pbp.parse_body(body) or {}, *self.invite)
                if status == pbp.OK else None)
        if seat is None:
            print(f"seat lookup for {self.invite[0]!r} ended in state {status!r}")
            out.status = (pbp_trouble(status, body), False)
            self.invite = None
        else:
            self.seat = seat
            pbp.remember(seat)   # a later launch then skips this round trip
            self.poll = pbp.fetch_state(seat.match_id)

    def _collect_write(self, ui: Ui | None) -> None:
        assert self.write is not None
        status, body = self.write.poll()
        if status == pbp.PENDING:
            return
        self.write = None
        if ui is not None and ui.in_pbp:
            pbp_heard(ui, status, body)
        self.accum = 0
        if status == pbp.OK and self.resolving:
            # Our own turn, accepted: the match now stands where our board does,
            # so a read would only say so. The next turn is ours to play
            # meanwhile, and the ordinary cadence picks up the rest.
            self.wait = PBP_POLL_MS
        else:
            # Otherwise the match has moved or it has not, and only a read can
            # tell us which. Ask on the next frame rather than in five seconds'
            # time — unless we were told to slow down.
            self.wait = PBP_THROTTLED_MS if pbp_throttled(body) else 0
            # A resolve that did not land leaves a board the match may never
            # have had; the stored log is the one that counts.
            self.stale = self.stale or self.resolving
        self.resolving = False

    def _collect_poll(self, out: Outcome, state: GameState | None, ui: Ui | None,
                      log: GameLog | None, settings: Settings
                      ) -> tuple[GameState | None, Ui | None, GameLog | None]:
        assert self.poll is not None and self.seat is not None
        status, body = self.poll.poll()
        if status == pbp.PENDING:
            return state, ui, log
        self.poll = None
        self.accum = 0
        match = None
        if status == pbp.OK:
            read = pbp.parse_body(body) or {}
            match = (pbp.with_brief(self.last, read)
                     if self.last is not None and read.get("brief")
                     else pbp.match_from_dict(read))
            self.last = match or self.last
        opening = state is None or ui is None or not ui.in_pbp
        self.failures = self.failures + 1 if match is None else 0
        self.wait = pbp_poll_delay(self.failures)
        if pbp_throttled(body):
            self.wait = max(self.wait, PBP_THROTTLED_MS)
        if match is None:
            line = pbp_trouble(status, body) if status != pbp.OK else PBP_UNREADABLE_MSG
            print(f"match read for {self.seat.match_id!r} ended in "
                  f"state {status!r} ({len(body)} bytes)"
                  + ("" if opening else f"; retrying in {self.wait} ms"))
            if opening:
                out.status = (line, False)
                self.invite, self.seat = None, None
            else:
                assert ui is not None
                ui.pbp_msg = self.read_line = line
            return state, ui, log
        if opening and match.rules_version != engine.RULES_VERSION:
            # The same call `open_replay` makes about a stored log, for the same
            # reason: rebuilding it under today's rules would show a match other
            # than the one being played, with nothing on screen to say so.
            # Checked only on the way in — a match's rules cannot change under
            # it once we are in it.
            print(f"match plays rules v{match.rules_version}, "
                  f"this build plays v{engine.RULES_VERSION}")
            out.status = (PBP_OUTDATED_MSG, False)
            self.invite, self.seat = None, None
            return state, ui, log
        if opening and self.seat.spectating and pbp_unwatchable(match):
            # Checked on the way in, from what the endpoint says about the match,
            # rather than trusted from the link.
            out.status = (pbp_unwatchable(match), False)
            self.invite, self.seat = None, None
            return state, ui, log
        if opening:
            opened = open_match(match, self.seat, settings)
            if opened is None:
                out.status = (PBP_BAD_LOG_MSG, False)
                self.invite, self.seat = None, None
                return state, ui, log
            state, ui, log = opened
            if self.invite_links:
                ui.pbp_invite = tuple(self.invite_links)
                self.invite_links = []
            out.game, out.opened, out.seed = opened, True, match.seed
            return state, ui, log
        assert state is not None and ui is not None and log is not None
        if self.read_line and ui.pbp_msg == self.read_line:
            ui.pbp_msg = ""   # the read that failed has since worked
        self.read_line = ""
        pbp_adopt(match, self.seat, ui)
        verdict = pbp_verdict(match, state, self.stale)
        if self.seat.spectating and verdict == PBP_RESOLVE:
            # Resolving writes the log every seat will be held to, which takes a
            # seat's token. A spectator waits for one of the players' clients to
            # do it, then steps.
            verdict = PBP_WAIT
        if (verdict == PBP_WAIT and match.lapsed and self.write is None
                and not self.seat.spectating):   # ...nor files a lapse
            # The clock has run out on somebody. Filing their turn is what keeps a
            # match from stopping dead because one person stopped answering; it
            # does not resolve anything, so the next read still finds an ordinary
            # complete turn.
            self.write = pbp_lapse(state, ui, match, self.seat)
        elif verdict == PBP_REBUILD:
            # More than a turn behind: there is no single turn to animate, so the
            # position is rebuilt from the opening. The standing rules come across
            # by hand because they are the one part of the position the endpoint
            # does not store — they are a human convenience that never reaches
            # `GameState`, so a rebuild is the only thing here that could quietly
            # throw a route plan away.
            rules = ui.auto_forward
            opened = open_match(match, self.seat, settings)
            if opened is None:
                ui.pbp_msg = PBP_BAD_LOG_MSG
            else:
                state, ui, log = opened
                ui.auto_forward = rules
                ui.prune_forward(state)   # ...minus any system lost since
                out.game, out.rebuilt = opened, True
                self.stale = False
        elif verdict == PBP_STEP and not (
                (script := pbp.settled_turn(match, state.turn))
                and pbp.verify_turn(state, script, match)):
            # A record we cannot trust — missing, filing an order a person never
            # sent, or rolling dice its orders do not roll. The board stays where
            # it was, and says why.
            ui.pbp_msg = PBP_BAD_LOG_MSG
        elif verdict in (PBP_STEP, PBP_RESOLVE):
            # One turn played onto the live board and watched like any other.
            # Stepping applies the record the resolver stored, checked above, so
            # no bot here decides again; resolving is the one place one does, on
            # a scratch copy, from an rng derived for the turn (`pbp.reseed`).
            turn = state.turn
            if verdict == PBP_STEP:
                out.reel = resolve_turn(state, ui, log, settings,
                                        script=pbp.settled_turn(match, turn))
            else:
                pbp.reseed(state, match.seed)
                out.reel = resolve_turn(state, ui, log, settings,
                                        seat_orders=pbp.turn_orders(state, match, ai.decide))
            pbp_opened(ui)
            out.played = True
            if verdict == PBP_RESOLVE:
                # ...and we are the one who noticed, so we are the one who reports
                # it. A second client doing the same is told the turn already
                # moved, and rebuilds from the log that won (`stale`).
                self.write = pbp.send_resolved(
                    self.seat, turn, log, replay.digest_hex(state),
                    state.winner is not None, state.winner)
                self.resolving = self.write is not None
        return state, ui, log
