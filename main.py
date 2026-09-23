"""Star Conquest — entry point and pygame main loop.

    uv run python main.py                       # opens the setup menu
    uv run python main.py --players 4           # menu pre-filled from CLI args
    uv run python main.py --no-menu --autoplay  # skip the menu (scriptable demo)

CLI args pre-fill the setup menu; --no-menu starts a game straight from them.
The loop owns transient view-state and scene wiring only; all rules live in the
pure core (engine/combat/mapgen/ai), and all drawing lives in render/menu.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from typing import Optional

import pygame

from starconquest import (ai, config, engine, fog, mapgen, mapmaker, menu, paths,
                          pbp, render, replay, share, softkeyboard, turnfilm,
                          viewstate, webstore)
from starconquest import input as game_input
from starconquest.geometry import WorldView
from starconquest.menu import MenuState
from starconquest.model import GameState, Order
from starconquest.replay import GameLog
from starconquest.settings import Challenge, Settings, build_state, resolve_seed
from starconquest.viewstate import Ui

AUTOPLAY_MS = 350  # delay between auto-resolved turns in autoplay mode
PLAY_MS = 350      # delay between turns while play/pause (P) is running
# ...and while fast-forwarding: no wait at all, so the loop resolves a turn every
# frame (config.FPS turns/second) instead of one per delay above.
FAST_FORWARD_MS = 0


def build_view(state: GameState) -> WorldView:
    # Two margins, both scaled and both floored at config.node_clearance() so a
    # boundary system's circle can never be sliced by the viewport edge: a modest
    # one for the zoom-1 fit (raising it would just shrink the whole map) and a
    # roomier one for panning a zoomed-in view, which otherwise presses systems
    # right up against the clip.
    return WorldView(mapgen.map_bounds(state), config.play_rect(),
                     padding=config.map_fit_padding(),
                     pan_padding=config.map_pan_padding())


# Shown on the menu when a confirmed quit couldn't actually close the app.
CANT_CLOSE_MSG = "Close the tab or app window to exit"


def leave_app() -> bool:
    """Act on a confirmed quit. True if the main loop should end and the process go.

    Off the web that is always right. In the browser there is nothing to exit *to*:
    ending the loop runs ``pygame.quit()``, which destroys the canvas and leaves the
    player staring at a blank page that only a force-close escapes — so we ask the
    browser to close the window (which an installed PWA often can, and a plain tab
    usually can't) and report that we are still running. The caller then drops back
    to the setup menu with ``CANT_CLOSE_MSG`` rather than tearing the display down.
    """
    if not paths.is_web():
        return True
    webstore.close_window()
    return False


def _apply_shared_link(settings: Settings) -> None:
    """Web only: pre-fill ``settings`` from a shared-settings token, in place
    (same effect as CLI args pre-filling the menu).

    Prefer a ``#<token>`` in the URL (an opened shared link); failing that, fall
    back to the last token we stashed in ``localStorage``. That fallback is what
    survives *installing* the PWA: the installed app launches from the manifest's
    fixed ``start_url`` with no fragment, so the hash is gone — but same-origin
    ``localStorage`` still holds the token the browser saw before the install.
    Every call is guarded inside ``webstore`` — anything missing is a no-op."""
    if not paths.is_web():
        return
    token = webstore.url_token() or webstore.get(paths.WEB_SHARED_SETTINGS_KEY)
    if not token:
        return
    try:
        settings.copy_from(Settings.from_token(token))
    except ValueError:
        return          # stale or hand-edited link: keep the CLI/default config
    # Remember the *setup* only. Storing a challenge would make its score-to-beat
    # banner reappear on every later launch, long after the link was opened — the
    # target belongs to the session you opened it in.
    webstore.set(paths.WEB_SHARED_SETTINGS_KEY, settings.without_challenge().to_token())


def new_ui(state: GameState, autoplay: bool, settings: Optional[Settings] = None,
           seat: int = 1) -> Ui:
    """A fresh view state for ``seat``.

    ``seat`` is 1 for every single-player match — ``mapgen._make_players`` stamps
    ``is_human`` there and nothing in the setup moves it. It is a parameter for
    the games where a person sits somewhere else: a play-by-post client holds one
    seat, and which one is decided by the link they opened.
    """
    ui = Ui(view=build_view(state), human_id=seat, autoplay=autoplay)
    challenge = settings.challenge if settings is not None else None
    if challenge is not None and challenge.matches(settings):
        # Carry the target onto the Ui as plain numbers so the win overlay can say
        # whether it fell, without render needing to see a Settings. Only when the
        # setup still matches: judging a result against a target scored on a
        # different map would be worse than saying nothing.
        ui.challenge_target = (challenge.turns, challenge.lost)
        ui.challenge_by = challenge.by
    refresh_fog(state, ui)   # seed visibility from the opening position
    ui.reset_view(state)     # frame just what's seen so far, not the whole map
    return ui


def _accumulate_fog(state: GameState, human_id: int, seen: set[int],
                    intel: dict[int, tuple[int, int, float]]) -> set[int]:
    """Fold one board's sighting into a running `seen` set and rival `intel`, and
    return the systems currently in full view. Mutates `seen`/`intel` in place so
    the same helper serves both the live turn loop and replaying a resumed game.
    """
    if state.is_defeated(human_id):
        # Knocked out: there is no territory left to observe *from*, so `fog.observe`
        # returns nothing and every remembered system falls back to a grey "?" — the
        # whole map, when fog was off. A defeated player is a spectator with nothing
        # left to hide from them, so reveal the rest of the match in full (what
        # history mode already does for a finished game). Only on actual defeat,
        # which is final: revealing while a landless player still has a fleet flying
        # would leak the map into `seen` for good if they retook a system.
        everything = set(state.systems)
        seen |= everything
        intel.clear()          # every living rival is in sight: live stats, not intel
        return everything
    visible, scouted = fog.observe(state, human_id, config.FOG_SIGHT, config.FOG_SCOUT)
    seen |= visible | scouted      # scouted folds into memory; both render grey-"?"
    for pid, player in state.players.items():
        if player.is_neutral or pid == human_id:
            continue
        if any(state.systems[s].owner_id == pid for s in visible):   # currently sighted
            intel[pid] = fog.player_totals(state, pid)
    return visible


def refresh_fog(state: GameState, ui: Ui) -> None:
    """Recompute the human's fog-of-war from the current board.

    Called at game start and after each resolved turn. Folds this turn's sighting
    into the persistent `seen`/`player_intel` memory. A no-op for visibility when
    fog is off (both ranges at max), where `visible` covers the whole map.
    """
    ui.visible = _accumulate_fog(state, ui.human_id, ui.seen, ui.player_intel)


def _begin_game(settings: Settings) -> tuple[GameState, Ui, GameLog, int]:
    """Roll the seed and open a fresh match — the single start path, shared by the
    menu's Start Game and the map creator's Play so the two cannot drift apart."""
    ai.load_models()   # pick up files added since launch / named by a loaded config
    seed = resolve_seed(settings)
    state, ui, log = start_game(settings, seed, settings.autoplay)
    return state, ui, log, seed


def start_game(settings: Settings, seed: int, autoplay: bool) -> tuple[GameState, Ui, GameLog]:
    """Build a fresh match and open a replay log to record it into."""
    state = build_state(settings, seed)
    return state, new_ui(state, autoplay, settings), replay.new_log(settings, seed)


def resume_game(log: GameLog, settings: Settings, seat: int = 1) -> tuple[GameState, Ui]:
    """Rebuild a saved in-progress match and adopt its settings for the menu.

    ``log`` is then reused as the live log, so continued play appends to the very
    same file the game was resumed from. Fog-of-war memory (`seen`/`player_intel`)
    is rebuilt across every replayed turn so resuming doesn't forget places you
    explored then lost sight of, and the standing auto-forward rules come back with
    it — rewinding to a turn is meant to hand the board back as it was, and rebuilt
    routes are half the position on a big map.
    """
    seen: set[int] = set()
    intel: dict[int, tuple[int, int, float]] = {}
    replay_view = replay.reconstruct(
        log, on_turn=lambda s: _accumulate_fog(s, seat, seen, intel))
    state, loaded = replay_view
    settings.copy_from(loaded)
    # Resume *paused*, whatever the match was doing when it was recorded. You
    # rewind to a turn in order to look at it: spotting a bot's mistake one turn
    # too late and going back to it, only for the board to start moving again
    # before you can read it, is the exact thing history is for. Nothing is
    # decided by waiting — autoplay is now purely "is anything advancing", since
    # the seat is claimed by *ending* a turn by hand (`engine._claim_seat`) and
    # not by the absence of autoplay — so the choice of who plays on from here is
    # handed back to the player, either way, with the clock stopped.
    ui = new_ui(state, False, loaded, seat)   # sets ui.visible from the final board
    ui.seen |= seen                        # ...plus memory of the whole game
    intel.update(ui.player_intel)          # final-turn intel wins for live rivals
    ui.player_intel = intel
    ui.reset_view(state)      # re-frame now seen includes the whole replayed history
    ui.hand_turns = hand_turns(log)        # the log is the record of who drove
    if log.turn_count:
        # The rules the last replayed turn was played with, put through the same
        # prune that turn's resolution gave them — a rule whose system was lost on
        # it must not come back to life here.
        ui.auto_forward = log.rules_for(log.turn_count - 1)
        ui.prune_forward(state)
    return state, ui


def hand_turns(log: GameLog) -> int:
    """How many recorded turns the human decided themselves (``GameLog.hand_turns``).

    Kept as a shell-side name because that is what reads at the call sites, but
    the count itself belongs to the log: the leaderboard's verifier recomputes it
    from an uploaded replay, and the two must not be able to disagree.
    """
    return log.hand_turns


def toggle_fast_forward(state: GameState, ui: Ui) -> None:
    """Flip fast forward, where it applies (``Ui.can_fast_forward`` — the human is
    out and the match plays on); a no-op anywhere else, so the F key can't secretly
    arm it mid-game.

    Turning it on also starts playback when nothing is stepping turns yet: the
    control is 'show me the end', and on a paused board it would otherwise look
    broken. Plain play rather than autoplay, because a knocked-out seat has nothing
    left to hand over — its orders are empty either way.
    """
    if not ui.can_fast_forward(state):
        return
    ui.fast_forward = not ui.fast_forward
    if ui.fast_forward and not (ui.autoplay or ui.playing):
        ui.playing = True


def step_delay(ui: Ui, base: int) -> int:
    """How long to wait before auto-resolving the next turn: ``base`` normally,
    nothing while fast-forwarding (a turn per frame)."""
    return FAST_FORWARD_MS if ui.fast_forward else base


def carry_autoplay(ui: Ui) -> bool:
    """Should the *next* match start in autoplay, given how this one was played?

    Only out of a pure demo. Flipping autoplay on to clean up a decided game is
    normal play (see ``hand_turns``), and the player who did that wants to play
    the next map, not watch it.
    """
    return ui.autoplay and ui.hand_turns == 0


def challenge_settings(settings: Settings, state: GameState, ui: Ui,
                       seed: int, log: GameLog) -> Settings:
    """``settings`` plus the human's result on it, ready to encode as a link.

    The seed is pinned to the one actually played: a challenge whose settings say
    "roll a fresh seed" would send a different map, which is the whole point of
    fixing it. Any challenge this match itself came from is replaced by the new
    result — ``challenge_key`` ignores the field, so the stamped key describes the
    setup alone, which is what lets the recipient's menu spot a later edit.
    """
    shared = Settings()
    shared.copy_from(settings)
    shared.seed = seed
    shared.challenge = Challenge(
        turns=state.turn,
        lost=state.players[ui.human_id].ships_lost,
        hand=hand_turns(log),
        key=shared.challenge_key(),
        log=log.match_id,
    )
    return shared


def share_challenge(settings: Settings, state: GameState, ui: Ui,
                    seed: int, log: GameLog) -> str:
    """Publish this win as a challenge link. Returns a line for the overlay.

    The clipboard is the channel, not the address bar: an installed PWA has no
    address bar to read a link out of, and a challenge token must never be stored
    or left in the URL or it would be read back at the next launch and its banner
    would haunt every later session (see ``webstore.copy_link``). Only if the
    clipboard is refused do we fall back to the URL, which at least works in a
    plain browser tab — and even then nothing is persisted.

    Off the web there is no clipboard bridge or address bar at all, so the token is
    saved next to the settings files; the feature would otherwise be web-only, and
    a desktop player has just as much reason to hand a friend a link.
    """
    shared = challenge_settings(settings, state, ui, seed, log)
    token = shared.to_token()
    if webstore.copy_link(token):
        return "Challenge link copied — paste it to a friend"
    if webstore.set_url_fragment(token):
        return "Challenge link is in the address bar — copy it to share"
    path = paths.saves_dir() / f"challenge_{seed}.txt"
    try:
        paths.saves_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n")
    except OSError:
        return "Couldn't save the challenge link"
    print(f"Challenge link token ({path}):\n#{token}")
    return f"Saved to {path.name} — append it to the game URL as #<token>"


def post_to_leaderboard(settings: Settings, state: GameState, ui: Ui,
                        seed: int, log: GameLog) -> str:
    """Open the public leaderboard's entry form on this result. Returns a line for
    the overlay.

    The same token the clipboard link carries, handed over in the fragment: the
    form reads it there and prefills itself, so posting is one click rather than a
    copy, a tab, and a paste. The fragment (not a query string) both keeps the
    token out of request logs and lets the site accept a whole pasted link with
    the identical code path.

    If the tab is refused — a popup blocker, or no browser to hand — fall back to
    the clipboard so the link is still recoverable, mirroring ``share_challenge``.
    """
    submit = webstore.leaderboard_url(paths.LEADERBOARD_SUBMIT_PATH)
    if not submit:
        return "No leaderboard is configured"
    shared = challenge_settings(settings, state, ui, seed, log)
    # Send the replay first, so it is on its way before the tab steals focus —
    # and only here. Pressing this button is what consents to uploading a game;
    # `share_challenge` sends nothing, and a match merely played sends nothing.
    share.post_log(log, shared.challenge.key if shared.challenge else "")
    token = shared.to_token()
    url = f"{submit}#{token}"
    if webstore.open_url(url):
        return "Leaderboard opened — add your name to post"
    if webstore.copy_to_clipboard(url):
        return "Leaderboard link copied — open it to post"
    print(f"Leaderboard entry link:\n{url}")
    return "Couldn't open a browser — link printed to the console"


# A launch URL of the form `#log=<match id>` asks the game to watch a replay
# rather than to load a setup. Distinguished by prefix because the other kind of
# fragment is a bare settings token, which is base64url and so cannot contain '='
# anywhere but its (stripped) padding.
LOG_FRAGMENT = "log="
# Three different things can go wrong and they want different answers from the
# player, so they get different lines rather than one shrug. The console carries
# the detail (the endpoint asked, the state it came back in) — on the web build
# `print` reaches the browser console, which is where a report starts.
# Covers no answer *and* a bad one — offline, CORS, and a 500/502/503 from the
# endpoint all land here. Deliberately not "couldn't reach": the board answering
# with a failure is the commonest of these and reads nothing like a network
# problem, so naming one would send the player looking in the wrong place. The
# console warning carries the status that tells them apart.
WATCH_UNREACHABLE_MSG = "Couldn't fetch that replay from the leaderboard"
WATCH_MISSING_MSG = "That replay isn't on the leaderboard — is its score still posted?"
WATCH_UNREADABLE_MSG = "That replay downloaded but wouldn't open"
WATCH_OUTDATED_MSG = "That replay was recorded under older rules and can't be shown exactly anymore"


def replay_request() -> str:
    """The match id in a ``#log=<id>`` launch URL, or ``""``.

    Web only, like every other fragment read: off the browser there is no URL to
    carry one, and ``--watch`` is the equivalent there.
    """
    token = webstore.url_token()
    return token[len(LOG_FRAGMENT):] if token.startswith(LOG_FRAGMENT) else ""


def _decode_log(blob: str) -> Optional[GameLog]:
    """``blob`` as a replayable log, or None if it can't be one at all — an
    unreadable/truncated encoding, or nothing recorded. Shared by ``open_replay``
    and its caller, which needs the same decode a second time only to tell an
    unreadable blob apart from an outdated one for the status line.
    """
    try:
        log = replay.GameLog.decode(blob)
    except ValueError:
        return None
    return log if log.turn_count else None


def open_replay(blob: str, settings: Settings) -> tuple[GameState, Ui, GameLog] | None:
    """Rebuild a downloaded match, ready to review. None if the blob is not one,
    or if it was recorded under rules this engine has since moved past
    (``GameLog.is_current``) — reconstructing it would silently show a game other
    than the one that was actually played, rather than the one asked for.

    Goes through ``resume_game``, so a watched replay is the same object a resumed
    save is — which is what makes *rewinding* out of one work for free: fork it at
    any turn and carry on playing from there. It adopts the replay's own settings
    (``resume_game`` copies them onto ``settings``), so leaving history lands on
    that setup rather than whatever the menu happened to be showing.
    """
    log = _decode_log(blob)
    if log is None or not log.is_current:
        return None
    ai.load_models()      # a stored match may name a drop-in strategy for a seat
    state, ui = resume_game(log, settings)
    # Somebody else's game: the win overlay must not offer to post their result as
    # ours, and playing on from it must not append to their uploaded match
    # (`Ui.can_post`, `share.due`). A `Retry` or a rewind out of here builds a
    # fresh `Ui` and so starts a match that really is ours.
    ui.watched = True
    return state, ui, log


# --------------------------------------------------------------------------- #
# Play-by-post
# --------------------------------------------------------------------------- #
# How often a client asks the endpoint what a shared match is doing. A turn here
# takes hours, so five seconds is generous by a wide margin. That is 720 reads
# an hour per open tab, which is what the endpoint's separate read budget
# (`MAX_READS_PER_WINDOW` in `pbp.mjs`) is sized around; writes have their own.
PBP_POLL_MS = 5000
# A read that failed is retried sooner than that, then later and later: a blip
# (a cold function, a dropped reply) is over by the next try, while an endpoint
# that is really down should not be asked twelve times a minute.
PBP_RETRY_MS = 500
PBP_RETRY_MAX_MS = 30000

# How long to leave the endpoint alone once it has said "slow down". Its window
# is an hour, so polling on at the usual cadence would only keep the address
# throttled; a minute is long enough to stop adding to it and short enough that
# a board which has recovered is back promptly.
PBP_THROTTLED_MS = 60_000


def pbp_throttled(body: str) -> bool:
    """Whether a call was turned away by the endpoint's rate limit."""
    return bool(body) and (pbp.parse_body(body) or {}).get("error") == "slow down"

# The same taxonomy the replay fetch above uses, for the same reason: these send
# the player somewhere different. A refused *token* is the one worth telling
# apart — it is the only one where the answer is "that link isn't yours".
PBP_UNREACHABLE_MSG = "Couldn't reach the match — trying again"
PBP_MISSING_MSG = "That match isn't on the board anymore"
PBP_REFUSED_MSG = "That seat link isn't valid for this match"
PBP_UNREADABLE_MSG = "The match answered with something unreadable"
PBP_OUTDATED_MSG = "That match was started under rules this build has moved past"
PBP_SENDING_MSG = "Sending your orders..."
PBP_OPENING_MSG = "Opening the match..."
PBP_LAPSED_MSG = "The deadline passed — filing the missing turns"
PBP_UNOPENED_MSG = "Couldn't open a match — is the board reachable?"
PBP_BAD_LOG_MSG = "The match's record doesn't match its orders — can't play it on"

# What the endpoint's own refusals mean from this side of the wire. Keyed by the
# exact `error` string `pbp.mjs` sends; anything not here is relayed with a
# prefix saying it was a refusal, so it still reads as an answer rather than as
# a connection problem.
PBP_ENDPOINT_MSGS = {
    "stale turn": "The match has moved past this turn — catching up",
    "already submitted": "Your orders for this turn are already in",
    "match is over": "This match is already over",
    "turn is not ready": "Not every seat is in yet",
    "already resolved": "Another player resolved this turn first — catching up",
    "already filed": "Those missing turns are already filed",
    "slow down": "Too many requests — waiting a moment",
    "store refused": "The match server had a problem — trying again",
}
PBP_REFUSAL_MSG = "The match refused that: {}"


def pbp_poll_delay(failures: int) -> int:
    """How long to wait before the next read, after ``failures`` in a row.

    The steady cadence while reads work; after a failure, doubling from
    ``PBP_RETRY_MS`` — past ``PBP_POLL_MS`` by the fifth — up to ``PBP_RETRY_MAX_MS``.
    """
    if failures <= 0:
        return PBP_POLL_MS
    return min(PBP_RETRY_MS * 2 ** (failures - 1), PBP_RETRY_MAX_MS)


def pbp_request() -> Optional[tuple[str, str]]:
    """The ``(match id, token)`` a ``#pbp=<match>:<token>`` launch URL carries.

    Web only, like every other fragment read; ``--match`` is the desktop
    equivalent, exactly as ``--watch`` is for ``#log=``.
    """
    return pbp.parse_link(webstore.url_token())


def pbp_adopt(match: pbp.Match, seat: pbp.Seat, ui: Ui) -> None:
    """Take in what the endpoint just said about the live turn.

    The three fields the waiting overlay is built from, and the only place they
    are written outside a turn actually advancing — whether we have submitted is
    the endpoint's answer, never ours, so a submission that failed on the way out
    cannot leave the board held for a turn nobody is waiting on.
    """
    ui.pbp_match = match.match_id
    ui.pbp_waiting = tuple(match.waiting)
    ui.pbp_submitted = match.has_submitted(seat.seat)


def open_match(match: pbp.Match, seat: pbp.Seat, settings: Settings
               ) -> Optional[tuple[GameState, Ui, GameLog]]:
    """Rebuild a shared match and sit down at our own seat. None if its log is bad.

    The stored log is the whole record (``pbp.match_log``), so this is an ordinary
    resume of it: ``resume_game`` replays it for the board and for everything a
    position is besides its board — fog remembered across turns, and how much of
    it was played by hand. The standing rules are the exception, since they are
    ours and the log came from whoever resolved last; the uploaded copy carries
    none (``pbp.shareable``), and any a pre-strip log still holds are dropped.

    The roster is re-stamped afterwards because ``replay.reconstruct`` restores
    the single-seat claim a solo game records, which knows nothing of a match
    seated at two and three.
    """
    ai.load_models()      # a shared setup may name a drop-in strategy for a bot seat
    log = pbp.match_log(match)
    if log is None:
        return None
    state, ui = resume_game(log, settings, seat.seat)
    ui.auto_forward = {}
    pbp.seat_people(state, match.seats)
    pbp_adopt(match, seat, ui)
    return state, ui, log


def pbp_send(state: GameState, ui: Ui, seat: pbp.Seat) -> Optional[pbp.Request]:
    """Submit this seat's orders for the live turn.

    The board goes on hold the moment the press lands rather than when the reply
    comes back: a round trip is long enough to get another order in, and orders
    queued after a submission would never be sent. ``pbp_heard`` hands the board
    back if the submission turns out not to have landed.
    """
    orders = list(ui.pending) + auto_forward_orders(state, ui)
    request = pbp.submit(seat, state.turn, orders)
    if request is None:
        ui.pbp_msg = PBP_UNREACHABLE_MSG
        return None
    ui.pbp_submitted, ui.pbp_msg = True, PBP_SENDING_MSG
    ui.pbp_waiting = tuple(s for s in ui.pbp_waiting if s != seat.seat)
    return request


def pbp_trouble(status: str, body: str) -> str:
    """A line for a call that did not come back with what was asked for.

    Two of the four states name something the player can act on and get their own
    words. The endpoint's known refusals — "stale turn", "already submitted",
    "turn is not ready" — each name a real state of the match and are put in the
    player's terms (``PBP_ENDPOINT_MSGS``); an unknown one is relayed as a
    refusal, and only a call that came back with nothing at all falls through to
    a line about the connection.
    """
    if status == pbp.MISSING:
        return PBP_MISSING_MSG
    if status == pbp.REFUSED:
        return PBP_REFUSED_MSG
    error = (pbp.parse_body(body) or {}).get("error") if body else None
    if not isinstance(error, str) or not error:
        return PBP_UNREACHABLE_MSG
    return PBP_ENDPOINT_MSGS.get(error) or PBP_REFUSAL_MSG.format(error)


def pbp_heard(ui: Ui, status: str, body: str) -> None:
    """Take in what a write answered — a submission, or a resolved turn.

    Nothing here advances anything: every write is followed by a fresh read, and
    the read is what moves the board. This only decides what the overlay says,
    and whether the board is still on hold — a submission that was refused has to
    hand the turn back, or the player sits in front of a veil waiting on a turn
    they never actually entered.
    """
    if status == pbp.OK:
        ui.pbp_msg = ""
        # A submission's reply names who is still to move, and it is newer than
        # any read we hold — the read that would say so is a poll away.
        waiting = (pbp.parse_body(body) or {}).get("waiting")
        if isinstance(waiting, list):
            ui.pbp_waiting = tuple(s for s in waiting if isinstance(s, int))
        return
    ui.pbp_submitted = False
    ui.pbp_msg = pbp_trouble(status, body)


def pbp_opened(ui: Ui) -> None:
    """A turn has resolved: the next one is ours to play, and nobody is waited on.

    Set here rather than left to the next read, because the read is seconds away
    and the veil would sit over a board the player is already entitled to move
    on. The read then confirms it, or corrects it if our own orders are somehow
    already in.
    """
    ui.pbp_submitted, ui.pbp_waiting, ui.pbp_msg = False, (), ""


def pbp_open(settings: Settings, seed: int, seats: Optional[list[int]] = None,
            deadline_hours: int = pbp.DEADLINE_HOURS) -> tuple[str, Optional[pbp.Request]]:
    """Ask the endpoint to open a shared match on this setup.

    ``seats`` is the roster to seat a person at — any seat left out plays its
    already-configured strategy instead, exactly as ``pbp.rebuild`` decides for
    a bot seat. Left out (``None``), every seat is a person: the simplest rule
    that fits in a button, and the one a player is already holding in their head
    when they set the player count, kept as the default for anything that opens
    a match without going through the menu's own roster prompt (``menu.MenuState
    .pbp_roster``, which always includes seat 1 — the creator ends up seated
    there — and lets 2+ be turned off one at a time).

    ``deadline_hours`` is the same prompt's other knob (``menu.MenuState.
    pbp_deadline_hours``) — how long a seat's clock runs before the first miss
    holds and a second hands it to its bot. Defaulted rather than required for
    the same reason ``seats`` is: every other caller (tests, a future script)
    gets the format's own default without having to know it exists.

    The id is minted here and sent rather than handed back, so the call is
    idempotent in the only sense that matters: a retry after a lost reply opens
    a second match rather than silently rewriting the first.
    """
    match_id = replay._new_match_id()
    if seats is None:
        seats = [pid for pid in range(1, settings.players + 1)]
    return match_id, pbp.create(match_id, settings, seed, seats, deadline_hours)


def pbp_seat_links(match_id: str, tokens: dict[int, str]) -> list[tuple[int, str]]:
    """``(seat, link)`` pairs, sorted by seat — one per person seated.

    A full URL on the web, where there is a page for a link to point at. Off it
    there is none — so the fragment goes out bare, which is both what ``--match``
    takes and what a player pastes onto the address of the web build.
    """
    return [(seat, webstore.link_url(pbp.link_fragment(match_id, token))
                    or pbp.link_fragment(match_id, token))
            for seat, token in sorted(tokens.items())]


def pbp_links(match_id: str, tokens: dict[int, str]) -> list[str]:
    """One line per seat: whose it is, and the link that seats them."""
    return [f"{config.player_name(seat)}: {link}"
            for seat, link in pbp_seat_links(match_id, tokens)]


def pbp_handed_out(match_id: str, tokens: dict[int, str]) -> str:
    """Put every seat's link somewhere the player can get at it. A status line.

    All of them at once, including our own: a match is only a match once the
    other people are in it, so the thing to hand over is the whole set, and the
    one that seats us is how we get back in after closing the tab. The same
    three channels ``share_challenge`` uses and in the same order — clipboard
    first because it is the only one an installed PWA has, then a file, which is
    the only one a desktop build has.

    Deliberately not the address bar, for the reason a challenge token is kept
    out of it: a seat link left there is read back at the next launch, and this
    one would seat you in a match you had already left.
    """
    lines = pbp_links(match_id, tokens)
    if webstore.copy_to_clipboard("\n".join(lines)):
        return f"{len(lines)} seat links copied — send one to each player"
    path = paths.saves_dir() / f"match_{match_id}.txt"
    try:
        paths.saves_dir().mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        print("Seat links:\n" + "\n".join(lines))
        return "Couldn't save the seat links — printed to the console"
    print(f"Seat links ({path}):\n" + "\n".join(lines))
    return f"Seat links saved to {path.name} — send one to each player"


def pbp_lapse(state: GameState, ui: Ui, match: pbp.Match,
              seat: pbp.Seat) -> Optional[pbp.Request]:
    """File the absent seats' turns, so a match nobody has abandoned can go on.

    Sent by whichever client notices, exactly as a resolution is, and for the
    same reason: there is no cron here and no server-side engine, so the work
    happens wherever somebody is looking. Two clients noticing together is the
    same non-event — the second is told the rows are already filed.

    Never for our own seat (``skip``): somebody who opens the game two days late
    is *here*, and filing their hold the moment they arrive would take the turn
    away from the one person about to take it.

    Nothing is resolved by it. Filing the last outstanding seat merely makes the
    turn complete, and the next read takes the ordinary resolve path, so a lapsed
    turn goes through the very same gate every other turn does.
    """
    filing = pbp.lapse_orders(state, match, ai.decide, skip=seat.seat)
    request = pbp.send_lapse(seat, match.turn, filing)
    if request is not None:
        ui.pbp_msg = PBP_LAPSED_MSG
    return request


# What a read of a shared match asks the loop to do next. Decided by the turn
# numbers alone, which is what makes it a pure function worth testing on its own:
# every client watching the same match agrees on the answer.
PBP_WAIT, PBP_STEP, PBP_RESOLVE, PBP_REBUILD = "wait", "step", "resolve", "rebuild"


def pbp_verdict(match: pbp.Match, state: GameState, stale: bool = False) -> str:
    """What a client with ``state`` on screen should do about ``match``.

    * **step** — the match is exactly one turn ahead: somebody else resolved it,
      and the orders that did are in hand, so the turn is played onto the live
      board and watched like any other.
    * **rebuild** — further ahead than that. We were away; there is no single
      turn to animate, so the position is rebuilt from the opening.
    * **resolve** — level with us and every seat is in. Whoever notices runs the
      turn and uploads it; two clients noticing together is not a race, because
      they compute the same turn from the same inputs.
    * **wait** — anything else, including a match *behind* us, which is simply
      our own resolve not having landed yet.

    ``stale`` overrides all of it with **rebuild**: our own resolve was not
    accepted, so the board on screen is one the match may never have had —
    somebody else's resolution won, and its bots may have decided differently.
    """
    if stale:
        return PBP_REBUILD
    if match.turn == state.turn + 1:
        return PBP_STEP
    if match.turn > state.turn:
        return PBP_REBUILD
    if match.turn < state.turn:
        return PBP_WAIT
    return PBP_RESOLVE if match.ready else PBP_WAIT


def open_history(state: GameState, ui: Ui, log: GameLog):
    """Enter history review on ``log``, returning ``(states, fog, events, live_fog)``.

    Shared by the H key and by a watched replay, which differ only in how they
    came by the log — and must not differ in what review then looks like.
    ``live_fog`` is the board's current fog, stashed so leaving review restores it
    exactly. Returns empty lists if there is nothing to review.

    Where the scrubber lands is the one thing the two entries *should* differ on,
    because they are asking different questions. Reviewing our own game (H) opens
    on the latest turn: that is where the player is, and looking back is a step
    away from it. A watched replay opens at the opening position, because the
    question there is "how was this game played" and the answer runs forwards —
    landing on the final board instead gives away the ending and leaves the only
    way to watch it being to drag all the way back first.
    """
    history_states, history_fog, history_events = build_history(ui, log)
    if not history_states:
        return [], [], [], None
    live_fog = (set(ui.visible), set(ui.seen), dict(ui.player_intel))
    ui.history = True
    ui.playing = False
    ui.reset_selection()
    ui.reset_route()
    ui.sel_forward = None
    ui.history_max = len(history_states) - 1
    # Turn 0 is the board as generated, before anyone moved — the frame a replay
    # should start on, and the one that makes the first turn's changes visible as
    # changes rather than as a position already arrived at.
    ui.history_turn = 0 if ui.watched else ui.history_max
    ui.history_reveal = state.winner is not None
    return history_states, history_fog, history_events, live_fog


def build_history(ui: Ui, log: GameLog) -> tuple[
        list[GameState], list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]],
        list[list[turnfilm.Event]]]:
    """Reconstruct one board (and the human's fog memory) per recorded turn.

    Replays the log once, deep-copying the state at the opening position and after
    every turn into ``states`` and snapshotting cumulative fog into ``fog`` (both
    indexed by turn: 0 == opening .. len-1 == latest). History mode then scrubs by
    plain list indexing — no re-reconstruction per drag. Fog is folded with the
    same ``_accumulate_fog`` the live loop uses, so a reviewed turn shows exactly
    what the human had discovered by then; a finished game is revealed in full by
    the caller instead.
    """
    states: list[GameState] = []
    fog: list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]] = []
    seen: set[int] = set()
    intel: dict[int, tuple[int, int, float]] = {}
    events: list[turnfilm.Event] = []
    buckets: list[list[turnfilm.Event]] = []

    def capture(s: GameState) -> None:
        states.append(copy.deepcopy(s))
        # `reconstruct` calls this at the opening position and once after each
        # turn, before the next one starts, which is exactly the cut a per-turn
        # film needs — so the events since the last cut are this turn's.
        buckets.append(events.copy())
        events.clear()
        visible = _accumulate_fog(s, ui.human_id, seen, intel)
        fog.append((set(visible), set(seen), dict(intel)))

    replay.reconstruct(log, on_turn=capture, on_event=events.append)
    return states, fog, buckets


def apply_rewind(log: GameLog, settings: Settings, turn: int) -> tuple[GameState, Ui]:
    """Truncate a live match to ``turn`` (discarding later turns), persist it, and
    rebuild the game from it — the mid-game 'rewind to here'. ``log`` is mutated in
    place so continued play keeps appending to the same file."""
    log.truncate(turn)
    try:
        log.save()
    except OSError:
        pass
    return resume_game(log, settings)


def auto_forward_orders(state: GameState, ui: Ui) -> list[Order]:
    """Turn standing auto-forward rules into this turn's orders for the human.

    A rule keeps ``keep`` ships at the source and forwards the surplus onward.
    We subtract ships already promised by manually-queued orders from the same
    source so a rule cooperates with (rather than double-counts) manual sends.
    """
    orders: list[Order] = []
    for src, (dest, keep) in ui.auto_forward.items():
        # the same test that decides whether the rule is drawn, pickable, editable
        # and (at the end of this turn, in `Ui.prune_forward`) kept at all
        if not ui.rule_is_live(state, src):
            continue
        if not state.are_adjacent(src, dest):
            continue
        send = ui.available(state, src) - keep
        if send > 0:
            orders.append(Order(ui.human_id, src, dest, send))
    return orders


def resolve_turn(state: GameState, ui: Ui, log: GameLog | None = None,
                 settings: Settings | None = None,
                 seat_orders: dict[int, list[Order]] | None = None,
                 script: engine.TurnRecord | None = None
                 ) -> turnfilm.Reel | None:
    """Advance one turn. In autoplay the human seat is also driven by the AI.

    ``seat_orders`` is a play-by-post turn: every seat's submission, collected by
    the endpoint rather than here, applied verbatim. It goes through this
    function rather than beside it because everything *after* the turn resolves —
    the film, the marks, the fog, the log, the camera snap — is the same work
    whoever decided the orders, and a second copy of it would be a second place
    for a turn to be drawn differently from how it was played.

    ``script`` is a play-by-post turn somebody else already resolved: its record
    (``pbp.settled_turn``) is applied verbatim, orders and dice, and nobody here
    decides anything — a bot seat included, which is the point.

    The human orders actually applied are recorded into ``log`` and the file is
    rewritten, so the on-disk log always matches the live game (and a crash loses
    at most the turn in progress). Given ``settings``, a win the human earned is
    also filed as their best on this setup, so replaying a challenge can show it.

    Returns a `turnfilm.Reel` when the turn is to be played back — the board it
    started from, plus what happened to it — and None otherwise. The live state is
    resolved either way, and completely, before this returns: a film is a report on
    a turn already finished, never on one in progress.
    """
    was_over = state.winner is not None
    was_defeated = state.is_defeated(ui.human_id)
    # A match begun in autoplay has no human seat at all (`settings.build_state`),
    # and ending a turn under manual control is what claims it — not the press of
    # Take control, which leaves it unclaimed so that it doubles as a pause on a
    # demo nobody means to play.
    # A shared match seats its people from the roster the endpoint stores
    # (`pbp.seat_people`), which is a fact about the match rather than about who
    # is looking at it — so there is nothing here to claim, and our own orders
    # are already in `seat_orders`, sent a turn ago.
    unclaimed = state.human() is None and seat_orders is None and script is None
    claim = ui.human_id if unclaimed and not ui.autoplay else None
    if seat_orders is not None or script is not None:
        human_orders = None
    elif unclaimed and ui.autoplay:
        # Nothing to attribute orders to, so pass none and let
        # `engine._collect_orders` decide this seat in its own loop, exactly as it
        # does every other. Computing them here *as well* would run the seat's
        # strategy twice, and the spare draws from `state.rng` would desync every
        # oracle's bit-exact stream tracking.
        human_orders = None
    elif ui.autoplay:
        human_orders = ai.decide(state, ui.human_id)
    else:
        human_orders = list(ui.pending) + auto_forward_orders(state, ui)
    # Two gates, here rather than at the three call sites. `marking` is the wider
    # one: a fight's cost or a finished hull is cheap to report and worth seeing,
    # for a bot-driven turn as much as a human one, so it only excludes
    # fast-forward, the whole point of which is to skip. `filming` narrows that to
    # the animated glide itself, gated on the display preference on top. Both are
    # read once a turn, like `share.due`, and never in `render`, where reading the
    # store would cost a DOM call every frame.
    marking = not ui.fast_forward
    filming = marking and webstore.animate_turns()
    before = turnfilm.copy_board(state) if filming else None
    # What the human could see going in. `visible` is not monotone — a system lost
    # this turn drops out of it — so marks (and the film, when there is one) draw
    # the union of both turns rather than have the fight that took it play out
    # under a grey "?" (`Ui.film_visible`).
    was_visible = frozenset(ui.visible) if marking else frozenset()
    events: list[turnfilm.Event] = []
    record = engine.end_turn(state, human_orders=human_orders, decide=ai.decide,
                             on_event=events.append if marking else None,
                             claim_seat=claim, seat_orders=seat_orders,
                             script=script)
    if not ui.autoplay:
        ui.hand_turns += 1      # mirrors the log's per-turn "ai" flag; see hand_turns()
    if log is not None:
        # Every seat's orders and every combat draw, plus the standing rules that
        # were in force — recorded before `prune_forward` below drops any that this
        # turn killed, so the log holds the rules the turn was actually played with.
        log.record_turn(record, human_ai=ui.autoplay, rules=ui.auto_forward)
        if state.winner is not None:
            log.mark_finished(state.winner)
        try:
            log.save()
        except OSError:
            pass  # a save failure must never interrupt play
        # ...and, with "Share replays" on, upload it as the game goes: every
        # `share.CHECKPOINT_TURNS` turns and again on the turn it is decided. The
        # cadence is what keeps an *abandoned* game — the kind no score ever
        # carries — from being lost entirely. `due` holds every condition,
        # including the opt-in itself, so this line cannot send by accident.
        # ...and never for a shared match: "Share replays" consents to
        # publishing games of your own, and a play-by-post log is half somebody
        # else's play. Its inputs already live in `pbp_orders` for the people in
        # it; that is a different table and a different bargain.
        if share.due(log, state.winner is not None,
                     ours=not ui.watched and not ui.in_pbp):
            share.post_log(log, log.setup_key())
    if ui.can_post(state) and settings is not None:
        record_best(settings, state, ui)
    ui.clear_pending()
    ui.prune_forward(state)   # a rule dies with the system it forwarded out of
    ui.reset_selection()
    ui.reset_route()          # a plan is only valid for the ownership it was built on
    refresh_fog(state, ui)
    # Snap the camera out to the whole map right as there stops being anything left
    # to hide — but only on the turn that crosses into it, not every turn a
    # spectator keeps fast-forwarding through an already-decided match.
    snap = ((state.winner is not None and not was_over)
            or (state.is_defeated(ui.human_id) and not was_defeated))

    # Lingering (see turnfilm.film): a turn you ended by hand is worth watching
    # resolve. A *run* of turns is not — play mode, autoplay and history playback
    # (`_next_history_film`) are one behaviour from three sources, and all of them
    # have to glide straight through rather than stop-start for every fight.
    film = (turnfilm.film(events, linger=not (ui.playing or ui.autoplay))
            if before is not None else None)
    # A turn with nothing to watch isn't worth a pause, and neither is one nobody
    # asked to see: both land the snap now, exactly as before there were films.
    if film is None or not film.plays:
        if marking:
            # No glide to spread them over, so every mark this turn earned fires
            # at once — still worth it: `age_fading_marks`/`_draw_film_flashes`
            # dissolve it on their own clock regardless of whether a playback
            # ever ran. `film_visible` is set only for the archiving read (the
            # same union `filming` would have used) and dropped right after, so
            # it cannot leak into a render pass that expects no film is up.
            ui.film_visible = was_visible
            ui.archive_marks(state, events)
            ui.film_visible = frozenset()
        if snap:
            ui.reset_view(state)
        return None
    ui.film, ui.film_ms, ui.film_visible = film, 0.0, was_visible
    # ...but a film runs in the frame the player was watching it in: the reveal is
    # the last turn's ending, not its opening (`land_film`).
    ui.deferred_view_snap = snap
    return _primed(ui, turnfilm.Reel(before, film))


def _primed(ui: Ui, reel: turnfilm.Reel) -> turnfilm.Reel:
    """Apply whatever fires at a film's very first instant — a launch, the first
    `Advanced` — before anything draws it, and hand the reel back.

    A reel can reach `render.draw` in the same frame it was built: a chained turn
    (either kind) is created *inside* the per-frame update, past the point where a
    running film is advanced. Without this a continuing fleet is drawn one frame at
    last turn's un-advanced ``turns_remaining`` — a visible snap back by a whole
    turn's worth of progress before the next frame's `run_to` catches it up.
    """
    ui.archive_marks(reel.board, reel.run_to(0.0))
    return reel


def _carry_into(ui: Ui, reel: turnfilm.Reel, carry: float) -> None:
    """Start a chained playback ``carry`` ms in rather than at zero.

    A frame steps the clock by a whole frame's worth, so the frame that lands a
    film nearly always overruns its end — and the next turn's glide is meant to
    continue that one without a seam (see the chaining in the loop, and
    `turnfilm`'s "a run of turns chains one animated turn straight into the
    next"). Dropping the overrun instead stalls every fleet for the remainder of
    that frame at every single join, which is a stutter the film's own length is
    never responsible for and which no amount of tuning `FILM_MOVE_MS` can
    remove. Paying it forward keeps the glide at one speed across the join.

    Sized in the same currency `config.MAX_FRAME_MS` caps, so the debt a slow
    frame can hand on is bounded by exactly what that frame was allowed to spend.
    Clamped to the new film's own length as well, so a frame longer than a whole
    turn's playback lands it on the next frame rather than running past its end.
    """
    if carry <= 0:
        return
    ui.film_ms = min(carry, reel.film.total_ms)
    ui.archive_marks(reel.board, reel.run_to(ui.film_ms))


def land_film(state: GameState, ui: Ui) -> None:
    """Finish a playback, however it ended: drop the film and pay what it deferred.

    The one place both endings meet — the clock running out and a press skipping it
    (`input` calls `Ui.stop_film`, which leaves the debt deliberately unpaid) — so
    the camera reveal a decided turn owes cannot be lost by skipping it. Idempotent,
    and a no-op when nothing was deferred.
    """
    ui.stop_film()
    if ui.deferred_view_snap:
        ui.deferred_view_snap = False
        ui.reset_view(state)


def _next_history_film(ui: Ui, history_states: list[GameState],
                       history_fog: list, history_events: list) -> Optional[turnfilm.Reel]:
    """Build the reel for the history turn after ``ui.history_turn``, if there is
    anything in it worth animating.

    Returns None when the next turn has nothing to show — no recorded events, or a
    film that turns out to be all instants — in which case the caller falls back to
    stepping it on its own, paced by `PLAY_MS`. With turn animation off, that is
    every turn: this still archives that turn's marks directly (mirroring
    `resolve_turn`'s own no-glide branch) before returning None, so a fight's cost
    or a finished hull keeps showing up even though nothing here glides.

    Shared by the two moments history playback advances: right after a film lands
    (so back-to-back animated turns chain immediately, with no dead gap between
    them) and the `PLAY_MS`-paced step for whenever the turn after that turns out
    to be quiet.
    """
    nxt = ui.history_turn + 1
    if not (nxt <= ui.history_max and nxt < len(history_events) and history_events[nxt]):
        return None
    if not webstore.animate_turns():
        # Same union as the reel path below (`film_visible`, set then dropped
        # right after the read) so a fight that cost us the system it happened at
        # still gets its mark rather than playing out under a grey "?".
        ui.film_visible = frozenset(history_fog[ui.history_turn][0])
        ui.archive_marks(history_states[nxt], history_events[nxt])
        ui.film_visible = frozenset()
        return None
    # Not lingering: a run of animated turns here must glide continuously, never
    # stop-start for a fight to be read (that's what a live End Turn is for).
    film = turnfilm.film(history_events[nxt])
    if not film.plays:
        return None
    ui.film, ui.film_ms, ui.film_paused = film, 0.0, False
    # the same union as live play: the turn's own fog on top of the one it lands in
    ui.film_visible = frozenset(history_fog[ui.history_turn][0])
    # a copy, so scrubbing back to this turn still finds the board it really was
    reel = turnfilm.Reel(turnfilm.copy_board(history_states[ui.history_turn]), film)
    return _primed(ui, reel)


def apply_toggle_play(ui: Ui) -> None:
    """What the "toggle_play" action does: flip `Ui.playing`, and freeze or leave
    alone whatever film is currently running.

    In history mode this drives the replay scrubber; starting it while parked on
    the final frame replays from the opening position.

    `input` no longer skips a playback on this particular press (see the note
    there), so pausing an *already running* playthrough must freeze the film
    itself (`Ui.film_paused`) rather than only stop the next turn from starting —
    and starting one from a standstill must leave a manually-triggered film that
    is already running alone, which is why the trigger is the button's own state
    going in (``was_playing``) rather than whether a film merely happens to exist.
    """
    if ui.history and not ui.playing and ui.history_turn >= ui.history_max:
        ui.history_turn = 0
    was_playing = ui.playing
    ui.playing = not ui.playing
    if ui.film is not None:
        ui.film_paused = was_playing


def record_best(settings: Settings, state: GameState, ui: Ui) -> None:
    """File the human's win as their best on this setup, if it beats the last one.

    Keyed on the setup rather than the seed alone, so the same map with different
    bots or balance knobs is a different challenge. Deliberately keyed on what was
    *played* — ``challenge_key`` ignores any attached target, so an edited
    challenge files under its own setup rather than the sender's. Storage is
    best-effort by design (see ``webstore``): a failure must never touch the game.
    """
    current, *legacy = settings.challenge_keys()
    webstore.record_best(current, state.turn,
                         state.players[ui.human_id].ships_lost, *legacy)


async def _kick_web_resize() -> None:
    """Web only: fire a synthetic browser resize shortly after being scheduled.

    Called twice — once at boot, once off the main loop's first input event (see
    the comments at both call sites) — always as a fire-and-forget background
    task so it never holds up a frame.
    """
    await asyncio.sleep(0.3)
    webstore.trigger_resize()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Star Conquest")
    ap.add_argument("--seed", type=int, default=None, help="map seed (random if omitted)")
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--players", type=int, default=config.DEFAULT_PLAYERS)
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--autoplay", action="store_true", help="AI plays all seats")
    ap.add_argument("--watch", default="", metavar="MATCH_ID",
                    help="download a posted replay and open it in history review "
                         "(the desktop equivalent of a #log=<id> link)")
    ap.add_argument("--match", default="", metavar="MATCH:TOKEN",
                    help="open a play-by-post seat (the desktop equivalent of a "
                         "#pbp=<match>:<token> link)")
    ap.add_argument("--no-menu", action="store_true",
                    help="skip the setup menu and start straight away with these args")
    # On Android the launcher may pass argv the parser doesn't recognise, and a
    # parse error would sys.exit() before anything renders. There are no CLI args
    # on a phone anyway, so force defaults there.
    args = ap.parse_args([] if paths.is_android() else None)

    settings = Settings.from_args(args)
    _apply_shared_link(settings)   # web only: pre-fill from a #<token> in the URL
    ai.load_models()          # register any drop-in models/ strategies up front

    pygame.init()
    if paths.is_android():
        # Let SDL create a surface at the real device resolution; passing a fixed
        # size can misbehave on mobile. The loop reads the actual size below.
        pygame.display.set_mode((0, 0))
    elif paths.is_web():
        # Match pygbag's canvas framebuffer exactly so nothing is clipped; the
        # browser scales this surface to fill the window/phone.
        pygame.display.set_mode((config.WEB_FB_W, config.WEB_FB_H))
        # The canvas can land squashed to the wrong aspect ratio on first paint
        # (pygbag's own resize handler is what fits it, and that only runs on a
        # genuine `resize` event, never proactively) — nudge it once, after
        # yielding a beat for the browser's layout to settle. This alone isn't
        # enough on a phone: the address bar/toolbar often only collapses (changing
        # the real viewport height) off the player's first tap/click on the *page
        # itself* — the one that dismisses pygbag's own "Ready to start!" gate — and
        # that collapse can lag the gesture by more than this beat. So the main loop
        # below fires a second, identical nudge off its own first input event, which
        # is the earliest point simulation code can observe that tap having landed.
        asyncio.ensure_future(_kick_web_resize())
    else:
        # Resizable: pygame grows the surface with the window, so we never re-call
        # set_mode (a redundant call fights the WM on X11 and snaps it back).
        pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("Star Conquest")
    # Scale the whole UI to the real surface before the first frame: fit the
    # baseline design size into the actual screen. Touch devices (Android, and
    # touch browsers) additionally boost hit targets to stay finger-sized; the web
    # framebuffer is now high-res (WEB_FB_*), so `fit` already scales the UI up for
    # crispness and only *touch* browsers need the extra boost — desktop browsers
    # report no touch points and stay compact. Fonts are built lazily from these
    # sizes, so this must run before any draw. The same probe also tells the shell
    # to drop keyboard-only labels/hints (config.touch_ui).
    sw, sh = pygame.display.get_surface().get_size()
    fit = min(sw / config.BASE_SCREEN_W, sh / config.BASE_SCREEN_H)
    touch = paths.is_android() or softkeyboard.is_touch_web()
    boost = config.TOUCH_UI_SCALE if touch else 1.0
    config.apply_ui_scale(max(1.0, fit) * boost, touch=touch)
    clock = pygame.time.Clock()

    # Three scenes share the one window: the setup menu, the map creator and the
    # game board. The menu builds `state`/`ui` on "start"; pressing M in-game
    # drops back to it, and Create/Edit map opens the creator between the two.
    menu_state = MenuState()
    editor: mapmaker.Editor | None = None      # live only while scene == "maker"
    state: GameState | None = None
    ui: Ui | None = None
    log: GameLog | None = None       # replay log of the live match (None while in menu)
    current_seed = 0
    scene = "game" if args.no_menu else "menu"
    if args.no_menu:
        current_seed = resolve_seed(settings)
        state, ui, log = start_game(settings, current_seed, settings.autoplay)

    # A `#log=<id>` link (or --watch) asks to watch a posted replay. The download
    # runs alongside the menu and is polled once a frame: the browser loop yields
    # per frame, so waiting on a round trip here would freeze the canvas before
    # anything had been drawn.
    wanted = args.watch.strip() or replay_request()
    pending_replay = share.fetch_log(wanted) if wanted else None
    if wanted and pending_replay is None:
        print(f"cannot fetch replay {wanted!r}: no endpoint, or not a match id")
        menu.set_status(menu_state, WATCH_UNREACHABLE_MSG, False)
    elif pending_replay is not None:
        menu.set_status(menu_state, "Loading replay...", True)

    # A `#pbp=<match>:<token>` link (or --match) seats us in a shared match. Same
    # shape as the replay fetch above — started here, polled once a frame, never
    # awaited — and mutually exclusive with it, since a launch URL carries one
    # fragment. Three calls can be in flight over a match's life and each gets its
    # own slot, because they answer different questions and arrive out of order:
    # `pbp_ident` asks which seat a link holds (once, ever), `pbp_poll` is the read
    # that moves the board, `pbp_write` is whatever submission or resolution is on
    # its way out.
    pbp_seat: pbp.Seat | None = None
    pbp_ident: pbp.Request | None = None
    pbp_poll: pbp.Request | None = None
    pbp_write: pbp.Request | None = None
    # ?action=create, and the id it was asked to open under. Minted here and sent
    # rather than handed back, so a retry after a lost reply opens a second match
    # instead of quietly rewriting the first.
    pbp_make: pbp.Request | None = None
    pbp_making = ""
    pbp_accum = 0
    pbp_wait = PBP_POLL_MS        # how long `pbp_accum` runs before the next read
    pbp_failures = 0              # reads in a row that came back with no match
    pbp_read_line = ""            # the line the last failed read put up, if any
    pbp_resolving = False         # whether `pbp_write` is our own resolved turn
    pbp_stale = False             # our resolve was refused: rebuild on the next read
    # Every other seat's link, from a match *we* just created — handed to `Ui`
    # (`pbp_invite`) the moment there is one to hand it to, so the invite overlay
    # opens on the very first frame of the match rather than a status line that
    # beat it there.
    pending_invite_links: list[tuple[int, str]] = []
    invite = (pbp.parse_link(pbp.PBP_FRAGMENT + args.match.strip())
              if args.match.strip() else pbp_request())
    if invite is not None and pending_replay is None:
        known = pbp.seat_for(invite[0])
        if known is not None and known.token == invite[1]:
            # Seated here before. A seat never moves, so the endpoint is asked
            # which one it is exactly once per link and the answer is kept.
            pbp_seat = known
            pbp_poll = pbp.fetch_state(invite[0])
        else:
            pbp_ident = pbp.identify(*invite)
        if pbp_poll is None and pbp_ident is None:
            print(f"cannot open match {invite[0]!r}: no endpoint is configured")
            menu.set_status(menu_state, PBP_UNREACHABLE_MSG, False)
            invite = None
        else:
            menu.set_status(menu_state, "Opening match...", True)
    elif args.match.strip() and invite is None:
        print(f"--match {args.match.strip()!r} is not a seat link")
        menu.set_status(menu_state, PBP_REFUSED_MSG, False)

    # If the last saved match was left unfinished, offer to resume it on the menu.
    resume_prompt: GameLog | None = None
    if not args.no_menu and pending_replay is None and invite is None:
        candidate = replay.latest_log()
        if candidate is not None and not candidate.finished and candidate.turn_count > 0:
            resume_prompt = candidate

    running = True
    confirm_quit = False   # showing the "are you sure?" modal; gates every quit path
    confirm_rewind = False  # showing the destructive mid-game rewind confirm modal
    # History-review snapshots, built on entering history mode and dropped on exit;
    # `live_fog` stashes the live fog triple so it survives a history visit intact.
    reel: turnfilm.Reel | None = None   # the turn being played back, if any
    history_states: list[GameState] = []
    history_fog: list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]] = []
    history_events: list[list[turnfilm.Event]] = []   # index-aligned with the above
    live_fog: tuple[set[int], set[int], dict[int, tuple[int, int, float]]] | None = None
    auto_accum = 0
    play_accum = 0
    fullscreen = False
    windowed_size = (config.SCREEN_W, config.SCREEN_H)  # restored when leaving fullscreen
    # Web only: re-armed below to fire the second resize kick off the loop's first
    # real input event; already "spent" (never fires) off the web build, so nothing
    # here needs its own is_web() check.
    resize_kicked = not paths.is_web()
    while running:
        # Capped, not raw: the frame that resolves a turn can run far longer than
        # a frame, and handing that whole stretch to the film it just started
        # would teleport the glide rather than advance it (`config.MAX_FRAME_MS`).
        dt = min(clock.tick(config.FPS), config.MAX_FRAME_MS)
        if pending_replay is not None:
            status, body = pending_replay.poll()
            if status != share.PENDING:
                opened = open_replay(body, settings) if status == share.OK else None
                pending_replay = None
                if status != share.OK:
                    # `missing` is the board answering "no such replay" — the
                    # plumbing worked, so the player is told to look at the score,
                    # not at their connection. Anything else is no answer at all:
                    # offline, CORS, an unconfigured or undeployed endpoint. The
                    # console carries the URL that was actually asked.
                    print(f"replay fetch ended in state {status!r}")
                    menu.set_status(menu_state,
                                    WATCH_MISSING_MSG if status == share.MISSING
                                    else WATCH_UNREACHABLE_MSG, False)
                elif opened is None:
                    # It answered, and what came back either isn't a replay at all
                    # (an error page, a truncated body, a log with no turns in it)
                    # or is one recorded under rules the engine has since moved
                    # past — decoded again here only to tell the two apart for the
                    # status line; `open_replay` already made the same call.
                    stale = _decode_log(body)
                    if stale is not None and not stale.is_current:
                        print(f"replay was recorded under rules v{stale.rules_version}, "
                              f"this build plays v{engine.RULES_VERSION}")
                        menu.set_status(menu_state, WATCH_OUTDATED_MSG, False)
                    else:
                        print(f"replay body was not a readable log ({len(body)} bytes): "
                              f"{body[:120]!r}")
                        menu.set_status(menu_state, WATCH_UNREADABLE_MSG, False)
                else:
                    state, ui, log = opened
                    current_seed = log.seed
                    history_states, history_fog, history_events, live_fog = open_history(state, ui, log)
                    # Nothing to review means nothing to watch: fall back to the
                    # menu rather than dropping into a board with no history.
                    scene = "game" if history_states else "menu"
        # Play-by-post, all three calls in one place. Reads are held off while a
        # film runs: `Request.poll` empties its own mailbox, so an answer
        # collected mid-playback is an answer thrown away — and, more to the
        # point, a turn must never resolve out from under one being drawn.
        if pbp_make is not None:
            status, body = pbp_make.poll()
            if status != pbp.PENDING:
                pbp_make = None
                tokens = (pbp.tokens_from(pbp.parse_body(body) or {}, pbp_making)
                          if status == pbp.OK else {})
                if not tokens:
                    print(f"opening match {pbp_making!r} ended in state {status!r}")
                    menu.set_status(menu_state, pbp_trouble(status, body), False)
                elif 1 not in tokens:
                    # We asked for seat 1 and did not get it. Rather than guess
                    # which seat is ours out of a roster we no longer recognise,
                    # hand the links over and let a link seat us like anyone else.
                    menu.set_status(menu_state,
                                    pbp_handed_out(pbp_making, tokens), True)
                else:
                    # Every seat's link, including our own, goes on the invite
                    # overlay the moment there is a `Ui` to hang it off (see
                    # `opening` below) rather than a status line and a clipboard
                    # blob. Ours is there too and not just remembered locally —
                    # `pbp.remember`'s token lives in this browser's storage
                    # alone, so a reload, a cleared profile or opening on
                    # another device has nothing else to recover the seat from.
                    # Then the ordinary opening path takes over, exactly as it
                    # would for somebody following the link we just sent them.
                    pbp_seat = pbp.Seat(pbp_making, 1, tokens[1])
                    pbp.remember(pbp_seat)
                    invite = (pbp_seat.match_id, pbp_seat.token)
                    pbp_poll = pbp.fetch_state(pbp_seat.match_id)
                    resume_prompt = None
                    pending_invite_links = pbp_seat_links(pbp_making, tokens)
                    menu.set_status(menu_state, "Match created — copy each seat's link", True)
        if pbp_ident is not None and invite is not None:
            status, body = pbp_ident.poll()
            if status != pbp.PENDING:
                pbp_ident = None
                seat = (pbp.seat_from(pbp.parse_body(body) or {}, *invite)
                        if status == pbp.OK else None)
                if seat is None:
                    print(f"seat lookup for {invite[0]!r} ended in state {status!r}")
                    menu.set_status(menu_state, pbp_trouble(status, body), False)
                    invite = None
                else:
                    pbp_seat = seat
                    pbp.remember(seat)   # a later launch then skips this round trip
                    pbp_poll = pbp.fetch_state(seat.match_id)
        if pbp_write is not None:
            status, body = pbp_write.poll()
            if status != pbp.PENDING:
                pbp_write = None
                if ui is not None and ui.in_pbp:
                    pbp_heard(ui, status, body)
                pbp_accum = 0
                if status == pbp.OK and pbp_resolving:
                    # Our own turn, accepted: the match now stands where our board
                    # does, so a read would only say so. The next turn is ours to
                    # play meanwhile, and the ordinary cadence picks up the rest.
                    pbp_wait = PBP_POLL_MS
                else:
                    # Otherwise the match has moved or it has not, and only a read
                    # can tell us which. Ask on the next frame rather than in five
                    # seconds' time — unless we were told to slow down.
                    pbp_wait = PBP_THROTTLED_MS if pbp_throttled(body) else 0
                    # A resolve that did not land leaves a board the match may
                    # never have had; the stored log is the one that counts.
                    pbp_stale = pbp_stale or pbp_resolving
                pbp_resolving = False
        if pbp_poll is not None and reel is None and pbp_seat is not None:
            status, body = pbp_poll.poll()
            if status != pbp.PENDING:
                pbp_poll = None
                pbp_accum = 0
                match = (pbp.match_from_dict(pbp.parse_body(body) or {})
                         if status == pbp.OK else None)
                opening = state is None or ui is None or not ui.in_pbp
                pbp_failures = pbp_failures + 1 if match is None else 0
                pbp_wait = pbp_poll_delay(pbp_failures)
                if pbp_throttled(body):
                    pbp_wait = max(pbp_wait, PBP_THROTTLED_MS)
                if match is None:
                    line = pbp_trouble(status, body) if status != pbp.OK \
                        else PBP_UNREADABLE_MSG
                    print(f"match read for {pbp_seat.match_id!r} ended in "
                          f"state {status!r} ({len(body)} bytes)"
                          + ("" if opening else f"; retrying in {pbp_wait} ms"))
                    if opening:
                        menu.set_status(menu_state, line, False)
                        invite, pbp_seat = None, None
                    else:
                        ui.pbp_msg = pbp_read_line = line
                elif opening and match.rules_version != engine.RULES_VERSION:
                    # The same call `open_replay` makes about a stored log, for the
                    # same reason: rebuilding it under today's rules would show a
                    # match other than the one being played, with nothing on screen
                    # to say so. Checked only on the way in — a match's rules
                    # cannot change under it once we are in it.
                    print(f"match plays rules v{match.rules_version}, "
                          f"this build plays v{engine.RULES_VERSION}")
                    menu.set_status(menu_state, PBP_OUTDATED_MSG, False)
                    invite, pbp_seat = None, None
                elif opening and (opened := open_match(match, pbp_seat,
                                                       settings)) is None:
                    menu.set_status(menu_state, PBP_BAD_LOG_MSG, False)
                    invite, pbp_seat = None, None
                elif opening:
                    state, ui, log = opened
                    current_seed = match.seed
                    if pending_invite_links:
                        ui.pbp_invite = tuple(pending_invite_links)
                        pending_invite_links = []
                    scene = "game"
                    auto_accum = 0
                else:
                    assert state is not None and ui is not None and log is not None
                    if pbp_read_line and ui.pbp_msg == pbp_read_line:
                        ui.pbp_msg = ""   # the read that failed has since worked
                    pbp_read_line = ""
                    pbp_adopt(match, pbp_seat, ui)
                    verdict = pbp_verdict(match, state, pbp_stale)
                    if verdict == PBP_WAIT and match.lapsed and pbp_write is None:
                        # The clock has run out on somebody. Filing their turn is
                        # what keeps a match from stopping dead because one person
                        # stopped answering; it does not resolve anything, so the
                        # next read still finds an ordinary complete turn.
                        pbp_write = pbp_lapse(state, ui, match, pbp_seat)
                    elif verdict == PBP_REBUILD:
                        # More than a turn behind: there is no single turn to
                        # animate, so the position is rebuilt from the opening.
                        # The standing rules come across by hand because they are
                        # the one part of the position the endpoint does not
                        # store — they are a human convenience that never reaches
                        # `GameState`, so a rebuild is the only thing here that
                        # could quietly throw a route plan away.
                        rules = ui.auto_forward
                        opened = open_match(match, pbp_seat, settings)
                        if opened is None:
                            ui.pbp_msg = PBP_BAD_LOG_MSG
                        else:
                            state, ui, log = opened
                            ui.auto_forward = rules
                            ui.prune_forward(state)   # ...minus any system lost since
                            history_states, history_fog, history_events = [], [], []
                            live_fog = None
                            pbp_stale = False
                    elif (verdict == PBP_STEP
                          and pbp.settled_turn(match, state.turn) is None):
                        ui.pbp_msg = PBP_BAD_LOG_MSG
                    elif verdict in (PBP_STEP, PBP_RESOLVE):
                        # One turn played onto the live board and watched like any
                        # other. Stepping applies the record the resolver stored,
                        # decisions and dice both, so no bot here decides again;
                        # resolving is the one place one does, from an rng derived
                        # for this turn (`pbp.reseed`) rather than carried over.
                        turn = state.turn
                        if verdict == PBP_STEP:
                            reel = resolve_turn(state, ui, log, settings,
                                                script=pbp.settled_turn(match, turn))
                        else:
                            pbp.reseed(state, match.seed)
                            reel = resolve_turn(
                                state, ui, log, settings,
                                seat_orders=match.orders_for_turn(turn))
                        pbp_opened(ui)
                        play_accum = auto_accum = 0
                        if verdict == PBP_RESOLVE:
                            # ...and we are the one who noticed, so we are the one
                            # who reports it. A second client doing the same is
                            # told the turn already moved, and rebuilds from the
                            # log that won (`pbp_stale`).
                            pbp_write = pbp.send_resolved(
                                pbp_seat, turn, log, replay.digest_hex(state),
                                state.winner is not None)
                            pbp_resolving = pbp_write is not None
        if ui is not None and not ui.in_pbp:
            # Left the match — back to the menu, a new map, a retry. Nothing in
            # flight is about it anymore, and the seat itself stays remembered so
            # the link still works.
            pbp_seat, pbp_poll, pbp_write, pbp_ident = None, None, None, None
            pbp_failures, pbp_wait, pbp_read_line = 0, PBP_POLL_MS, ""
            pbp_resolving = pbp_stale = False
        elif (ui is not None and pbp_seat is not None and pbp_poll is None
                and pbp_ident is None and pbp_write is None and state is not None
                and state.winner is None):
            pbp_accum += dt
            if pbp_accum >= pbp_wait:
                pbp_accum = 0
                pbp_poll = pbp.fetch_state(ui.pbp_match)

        # Reflow to fill the window whenever its size changes.
        screen = pygame.display.get_surface()
        if screen.get_size() != (config.SCREEN_W, config.SCREEN_H):
            config.SCREEN_W, config.SCREEN_H = screen.get_size()
            if state is not None and ui is not None:
                ui.view = build_view(state)
                ui.reset_view(state)   # re-frame for the new size, not the whole map
            if editor is not None:
                mapmaker.reflow(editor)
        for event in pygame.event.get():
            # Web only, once: the first tap/click/key this session sees is the
            # earliest point simulation code can observe that the player has
            # actually landed on the page (touch arrives as MOUSEBUTTONDOWN — see
            # the boot-time kick's comment for why this second nudge matters, and
            # why a fixed delay from boot alone isn't a substitute for it).
            if not resize_kicked and event.type in (
                    pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                resize_kicked = True
                asyncio.ensure_future(_kick_web_resize())
            # Android hardware/gesture Back arrives as K_AC_BACK; normalise it to
            # Esc so every existing "cancel / back out" handler below just works.
            if event.type == pygame.KEYDOWN and event.key == pygame.K_AC_BACK:
                event = pygame.event.Event(
                    pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b", mod=0)
            if confirm_quit:
                # Modal: swallow all other input until the user answers.
                do_quit = False
                if event.type == pygame.QUIT:
                    running = False       # the OS/WM is closing us; not our choice
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        do_quit = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_quit = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    quit_r, cancel_r = render.confirm_quit_buttons(screen)
                    if quit_r.collidepoint(event.pos):
                        do_quit = True
                    elif cancel_r.collidepoint(event.pos):
                        confirm_quit = False
                if do_quit:
                    if leave_app():
                        running = False
                    else:
                        # web: the browser wouldn't close the window, so fall back to
                        # the setup menu and say so — never to a dead black canvas.
                        confirm_quit = False
                        scene = "menu"
                        state, ui, log = None, None, None
                        menu.set_status(menu_state, CANT_CLOSE_MSG, False)
                continue

            if confirm_rewind:
                # Modal: confirm a destructive mid-game rewind (discards later turns).
                do_rewind = False
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        do_rewind = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_rewind = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    rewind_r, cancel_r = render.confirm_rewind_buttons(screen)
                    if rewind_r.collidepoint(event.pos):
                        do_rewind = True
                    elif cancel_r.collidepoint(event.pos):
                        confirm_rewind = False
                if do_rewind:
                    assert log is not None and ui is not None   # confirm_rewind ⇒ in-game
                    state, ui = apply_rewind(log, settings, ui.history_turn)
                    history_states, history_fog, history_events, live_fog = [], [], [], None
                    confirm_rewind = False
                    auto_accum = 0
                continue

            if resume_prompt is not None:
                # Boot modal: resume the last unfinished game, or dismiss to the menu.
                accept = False
                if event.type == pygame.QUIT:
                    confirm_quit = True
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        accept = True
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        resume_prompt = None
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    resume_r, new_r = menu.resume_prompt_buttons(screen)
                    if resume_r.collidepoint(event.pos):
                        accept = True
                    elif new_r.collidepoint(event.pos):
                        resume_prompt = None
                if accept:
                    assert resume_prompt is not None
                    ai.load_models()   # a resumed game may name a drop-in strategy
                    state, ui = resume_game(resume_prompt, settings)
                    current_seed = resume_prompt.seed
                    log = resume_prompt
                    scene = "game"
                    auto_accum = 0
                    resume_prompt = None
                continue

            if event.type == pygame.QUIT:
                confirm_quit = True
                continue

            if (event.type == pygame.KEYDOWN and event.key == pygame.K_F11
                    and not paths.is_android()):
                # Desktop-only: mobile is already fullscreen and re-calling
                # set_mode mid-run is fragile on Android's SDL.
                fullscreen = not fullscreen
                if fullscreen:
                    windowed_size = (config.SCREEN_W, config.SCREEN_H)
                    pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                else:
                    pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
                continue

            if scene == "menu":
                action = menu.handle_event(event, menu_state, settings)
                if action == "start":
                    state, ui, log, current_seed = _begin_game(settings)
                    scene = "game"
                    auto_accum = 0
                elif action == "play_by_post":
                    # The same setup, opened as a shared match — for the roster
                    # and deadline the menu's own prompt just confirmed (seat 1
                    # is always in the roster; a seat left off plays whatever
                    # strategy it already carries). The seed is resolved now and
                    # sent with it: "roll a fresh seed" has to mean one seed for
                    # the whole table, not one each.
                    if pbp_make is None:
                        current_seed = resolve_seed(settings)
                        pbp_making, pbp_make = pbp_open(
                            settings, current_seed, sorted(menu_state.pbp_roster) or None,
                            menu_state.pbp_deadline_hours)
                        if pbp_make is None:
                            menu.set_status(menu_state, PBP_UNOPENED_MSG, False)
                        else:
                            menu.set_status(menu_state, PBP_OPENING_MSG, True)
                elif action == "create_map":
                    editor = mapmaker.open_editor(settings)
                    scene = "maker"
                elif action == "quit":
                    confirm_quit = True
                continue

            if scene == "maker":
                assert editor is not None
                action = mapmaker.handle_event(event, editor, settings)
                if action == "play":
                    state, ui, log, current_seed = _begin_game(settings)
                    scene, editor = "game", None
                elif action == "menu":
                    scene, editor = "menu", None
                continue

            # Past the menu and modal handlers, so scene == "game": state/ui/log are live.
            assert state is not None and ui is not None and log is not None
            action = game_input.handle_event(event, state, ui)
            if action == "quit":
                confirm_quit = True
            elif action == "menu":
                scene = "menu"
                state, ui, log = None, None, None
            elif action == "pbp_copy_seat":
                # The invite overlay named the seat (`input` never touches the
                # clipboard); the link is whichever row it was drawn from.
                link = next((link for seat, link in ui.pbp_invite
                            if seat == ui.pbp_copy_seat), "")
                if link and webstore.copy_to_clipboard(link):
                    ui.pbp_invite_copied = ui.pbp_copy_seat
                ui.pbp_copy_seat = 0
            elif action == "share":
                # Game-over only (input only returns this there), and only for a
                # result that is ours to publish — render gates the button on the
                # very same call, so the C key cannot reach what the overlay
                # declines to draw.
                if ui.can_post(state):
                    ui.share_msg = share_challenge(settings, state, ui, current_seed, log)
            elif action == "leaderboard":
                # Same gate as "share": the two buttons offer one result by two
                # channels, so neither may fire on a result that isn't yours.
                if ui.can_post(state):
                    ui.share_msg = post_to_leaderboard(settings, state, ui, current_seed, log)
            elif action == "end_turn" and not ui.autoplay:
                if ui.in_pbp:
                    # The one thing that makes a shared match different: the turn
                    # advances when the last seat submits, not when anybody
                    # presses a key. So this sends ours and waits (`input` already
                    # declines a second press while we are waiting).
                    if pbp_seat is not None and pbp_write is None:
                        pbp_write = pbp_send(state, ui, pbp_seat)
                        # A read already in flight was asked before our orders
                        # existed, and landing after them it would put us back on
                        # the list of seats still to move. The write's own
                        # follow-up read replaces it.
                        pbp_poll = None
                else:
                    reel = resolve_turn(state, ui, log, settings)
                play_accum = 0   # re-time the play cadence from this step
            elif action == "toggle_play":
                apply_toggle_play(ui)
                play_accum = 0   # first step after PLAY_MS, then every PLAY_MS
            elif action == "toggle_autoplay":
                ui.autoplay = not ui.autoplay
                ui.playing = False
                ui.reset_selection()
                ui.reset_route()
                ui.clear_pending()
                auto_accum = 0
            elif action == "toggle_fast_forward":
                toggle_fast_forward(state, ui)   # spectating only; else a no-op
                auto_accum = play_accum = 0
            elif action == "restart":
                current_seed += 1
                state, ui, log = start_game(settings, current_seed,
                                            carry_autoplay(ui))
            elif action == "retry":
                # Replay this exact match from the opening position: fork the
                # log at turn 0 so the completed record stays intact, then
                # resume live play in the new file (same recipe as rewinding
                # to turn 0 from history, minus the scrubbing).
                assert log is not None
                log = log.fork(0)
                try:
                    log.save()
                except OSError:
                    pass
                current_seed = log.seed
                state, ui = resume_game(log, settings)
                history_states, history_fog, history_events, live_fog = [], [], [], None
                auto_accum = 0
            elif action == "toggle_route":
                # Route mode: multi-select a group of systems and forward them all
                # toward one destination. Leaving discards the plan — it is only
                # ever committed by its own confirm.
                if ui.mode == viewstate.ROUTING:
                    ui.reset_route()
                elif ui.can_route(state):
                    ui.begin_route()
            elif action == "toggle_history":
                if ui.history:
                    # leave review: drop snapshots and restore the live fog exactly
                    # (the live board is untouched while reviewing, so no recompute).
                    ui.history = False
                    ui.dragging_scrubber = False
                    ui.playing = False   # replay playback must not carry into live play
                    ui.clear_fading_marks()   # they belonged to a review that just ended
                    history_states, history_fog, history_events = [], [], []
                    if live_fog is not None:
                        ui.visible, ui.seen, ui.player_intel = live_fog
                        live_fog = None
                elif log is not None and log.turn_count > 0:
                    ui.clear_fading_marks()   # entering fresh: nothing from live play carries in
                    history_states, history_fog, history_events, entered = open_history(state, ui, log)
                    if entered is not None:
                        live_fog = entered
            elif action == "rewind" and not ui.in_pbp:
                # Never reachable from a shared match — render withholds the button
                # (see `_draw_scrubber`) — but the check is repeated here rather
                # than trusted, since rewinding forks the view away from a match
                # the server and every other seat still think is at a later turn.
                if ui.history_reveal:
                    # finished game — fork a new save so the completed record stays
                    # intact, then resume live play in the new file.
                    log = log.fork(ui.history_turn)
                    try:
                        log.save()
                    except OSError:
                        pass
                    current_seed = log.seed
                    state, ui = resume_game(log, settings)
                    history_states, history_fog, history_events, live_fog = [], [], [], None
                    auto_accum = 0
                else:
                    confirm_rewind = True   # mid-game rewind is destructive: confirm

        if scene == "game":
            assert state is not None and ui is not None and log is not None
            # A mark keeps fading whether or not a playback is currently running
            # (that's the whole point — see `Ui.age_fading_marks`), so this runs
            # unconditionally, every frame, regardless of what `reel` is doing.
            ui.age_fading_marks(dt)
            # A playback holds the board for its duration, and nothing below may
            # resolve or seek while it runs — under play mode the next turn would
            # otherwise be resolved out from under the one still being drawn.
            # `ui.film` and `reel` are kept in lockstep, so the skip that clears
            # the film (in `input`) also drops the board it was playing onto.
            if reel is not None and ui.film is None:
                reel = None                 # skipped: input cleared the film
                land_film(state, ui)        # ...which still leaves the snap owed
            elif reel is None and ui.film is not None:
                land_film(state, ui)        # unreachable, but a stranded film would
                                            # hide the End Turn button for good
            elif reel is not None and not confirm_quit and not confirm_rewind:
                # Frozen rather than advanced while paused (see `Ui.film_paused`),
                # so pausing keeps showing what the turn did instead of losing it.
                if not ui.film_paused:
                    ui.film_ms += dt
                    ui.archive_marks(reel.board, reel.run_to(ui.film_ms))
                if ui.film_ms >= ui.film.total_ms:
                    # By how much this frame's step overran the film. A run of
                    # turns is one continuous glide (see the chaining below), so
                    # it belongs to the turn that follows rather than on the
                    # floor — `_carry_into` pays it there.
                    carry = ui.film_ms - ui.film.total_ms
                    if ui.history:
                        # The scrubber moves at the film's *end*, so it and the top
                        # bar's turn counter (which reads the board being drawn)
                        # never disagree mid-transition.
                        ui.history_turn = min(ui.history_max, ui.history_turn + 1)
                        if ui.history_turn >= ui.history_max:
                            ui.playing = False
                    land_film(state, ui)
                    reel = None
                    play_accum = 0
                    auto_accum = 0
                    if ui.history and ui.playing:
                        # Chain straight into the next turn's film with no gap, so
                        # a run of animated turns glides rather than stuttering —
                        # the PLAY_MS pacing below is only for a quiet turn with
                        # nothing to chain into.
                        reel = _next_history_film(ui, history_states, history_fog,
                                                  history_events)
                    elif (ui.playing and not ui.history and state.winner is None
                            and ui.mode != viewstate.ROUTING):
                        # ...and live play chains the same way, on the same terms
                        # as the PLAY_MS branch below it would have resolved on.
                        reel = resolve_turn(state, ui, log, settings)
                    elif (ui.autoplay and not ui.history and state.winner is None
                            and ui.mode != viewstate.ROUTING):
                        # ...and so does autoplay, on the same terms as the
                        # AUTOPLAY_MS branch below it would have resolved on — a bot
                        # game gets the same continuous glide a human's does.
                        reel = resolve_turn(state, ui, log, settings)
                    if reel is not None:
                        _carry_into(ui, reel, carry)

            if (reel is None and ui.history and ui.playing
                    and not confirm_quit and not confirm_rewind):
                # Replay playback: one turn per PLAY_MS, stopping when it reaches
                # the final recorded turn (like a video reaching the end). With
                # turn animation on, the step becomes a film and the scrubber
                # advances when that finishes instead of here — and the turn after
                # that one chains immediately (just above), so this pacing is only
                # ever felt on a turn with nothing to animate.
                play_accum += dt
                if play_accum >= PLAY_MS:
                    play_accum = 0
                    reel = _next_history_film(ui, history_states, history_fog, history_events)
                    if reel is None:
                        nxt = ui.history_turn + 1
                        ui.history_turn = min(ui.history_max, nxt)
                        if ui.history_turn >= ui.history_max:
                            ui.playing = False
            elif (reel is None and state.winner is None
                    and not confirm_quit and not confirm_rewind and not ui.history
                    and ui.mode != viewstate.ROUTING):
                if ui.autoplay:
                    # Same shape as play mode just below: with turn animation on,
                    # one animated turn chains into the next as it lands (above),
                    # so this pacing is only ever felt on a turn with nothing to
                    # animate (or with the preference off).
                    auto_accum += dt
                    if auto_accum >= step_delay(ui, AUTOPLAY_MS):
                        auto_accum = 0
                        reel = resolve_turn(state, ui, log, settings)
                elif ui.playing:
                    # Same shape as history's playback above: with turn animation
                    # on, one animated turn chains into the next as it lands, so
                    # this pacing is only ever felt on a turn with nothing to
                    # animate (or with the preference off).
                    play_accum += dt
                    if play_accum >= step_delay(ui, PLAY_MS):
                        play_accum = 0
                        reel = resolve_turn(state, ui, log, settings)

        if scene == "menu":
            # Soft-keyboard typing arrives outside the SDL event queue, so the
            # menu needs a per-frame poll as well as its event handler.
            menu.pump(menu_state, settings)
            menu.draw(screen, menu_state, settings)
            if resume_prompt is not None:
                menu.draw_resume_prompt(screen, resume_prompt)
        elif scene == "maker":
            assert editor is not None
            mapmaker.pump(editor)          # same soft-keyboard poll, for its filename field
            mapmaker.age_status(editor, dt)
            mapmaker.draw(screen, editor, settings)
        else:
            assert state is not None and ui is not None   # scene == "game"
            if ui.history and history_states:
                # Draw the reconstructed past board with the fog for that turn — or, for
                # a finished game, everything revealed. `render` just draws the state and
                # fog it's handed; it neither knows nor cares the board is historical.
                #
                # Mid-transition it is handed the film's board instead, and the fog of
                # the turn being moved *to*: a film is a report on a turn that has
                # already happened, and holding the earlier fog would have an inbound
                # fleet pop into existence halfway down its lane.
                i = max(0, min(ui.history_turn, len(history_states) - 1))
                view_state = history_states[i]
                if reel is not None:
                    view_state = reel.board
                    i = min(i + 1, len(history_fog) - 1)
                if ui.history_reveal:
                    all_ids = set(view_state.systems)
                    ui.visible, ui.seen, ui.player_intel = all_ids, set(all_ids), {}
                else:
                    ui.visible, ui.seen, ui.player_intel = history_fog[i]
                render.draw(screen, view_state, ui)
            else:
                # Live play, and the same substitution: `resolve_turn` has already
                # refreshed the fog, so a film runs under the fog of the board it is
                # about to hand back — the same choice as above.
                render.draw(screen, reel.board if reel is not None else state, ui)
            if confirm_rewind:   # only ever set in-game, so ui is live here
                render.draw_confirm_rewind(screen, ui.history_turn)
        if confirm_quit:
            render.draw_confirm_quit(screen)
        pygame.display.flip()
        # Yield to the browser event loop each frame (required by pygbag/Emscripten;
        # a harmless near-instant await on desktop).
        await asyncio.sleep(0)

    pygame.quit()


def _crash_screen(exc_text: str) -> None:
    """Last-resort on-device error display. A phone has no console or logcat to
    hand, so on an unhandled exception we draw the traceback to the screen (and
    save it to the data dir) so it can be read or screenshotted. Best-effort: if
    the display itself is what failed, this just returns."""
    try:
        (paths.data_dir() / "last_crash.txt").write_text(exc_text)
    except Exception:
        pass
    try:
        if not pygame.get_init():
            pygame.init()
        screen = pygame.display.get_surface() or pygame.display.set_mode((0, 0))
        w, h = screen.get_size()
        font = pygame.font.SysFont("monospace", max(14, w // 60))
        char_w = max(1, font.size("M")[0])
        margin, line_h = 16, font.get_height() + 2
        cols = max(20, (w - 2 * margin) // char_w)
        lines: list[str] = []
        for raw in exc_text.splitlines():
            lines.extend([raw[i:i + cols] for i in range(0, len(raw), cols)] or [""])
        screen.fill((12, 12, 18))
        for row, ln in enumerate(lines[: (h - 2 * margin) // line_h]):
            screen.blit(font.render(ln, True, (240, 130, 130)),
                        (margin, margin + row * line_h))
        pygame.display.flip()
        clock = pygame.time.Clock()
        waiting = True
        while waiting:
            for ev in pygame.event.get():
                if ev.type in (pygame.QUIT, pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                    waiting = False
            clock.tick(30)
    except Exception:
        pass


if __name__ == "__main__":
    # pygbag patches asyncio.run to drive the browser event loop; on desktop this
    # is the ordinary asyncio entry point. (In the browser, code after this line
    # may run before the game finishes, so all shutdown lives inside main().)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pygame.quit()
    except BaseException as exc:   # noqa: BLE001 - surface *anything*, incl. SystemExit
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise                 # a clean exit is not a crash
        import traceback
        traceback.print_exc()     # -> terminal on desktop, browser console on web
        if not paths.is_web():
            _crash_screen(traceback.format_exc())
        pygame.quit()
    print("\nGoodbye!")
