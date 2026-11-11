"""The game's application logic, beneath any display: starting, resuming and
resolving a match, history review, hints, sharing and posting, replays, and the
play-by-post client's helpers.

Moved out of the pygame shell's ``main`` so that the HTML frontend could drive
the same code (``docs/design/html-frontend.md``); ``session`` drives it now.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

from starconquest import (
    ai,
    campaign,
    coach,
    config,
    daily,
    engine,
    fog,
    gauntlet,
    gauntletgen,
    mapgen,
    paths,
    pbp,
    replay,
    share,
    stats,
    turnfilm,
    webstore,
)
from starconquest import run as gauntlet_run
from starconquest.geometry import WorldView
from starconquest.model import AiParams, GameState, Order
from starconquest.replay import GameLog
from starconquest.settings import (RANDOM_STRATEGY, Challenge, Settings, apply_globals, build_state,
                                   random_seed, resolve_seed)
from starconquest.viewstate import Ui

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
    if settings.challenge is not None and not settings.challenge.matches(settings):
        # The setup and its score arrived together, so a key that no longer
        # matches is an outdated checksum, not an edit: re-stamp it, or the menu
        # asks to "change the challenge setup" on the very first click.
        settings.challenge.key = settings.challenge_key()
    # Remember the *setup* only. Storing a challenge would make its score-to-beat
    # banner reappear on every later launch, long after the link was opened — the
    # target belongs to the session you opened it in.
    webstore.set(paths.WEB_SHARED_SETTINGS_KEY, settings.without_challenge().to_token())


def new_ui(state: GameState, autoplay: bool, settings: Settings | None = None,
           seat: int = 1) -> Ui:
    """A fresh view state for ``seat``.

    ``seat`` is 1 for every single-player match — ``mapgen._make_players`` stamps
    ``is_human`` there and nothing in the setup moves it. It is a parameter for
    the games where a person sits somewhere else: a play-by-post client holds one
    seat, and which one is decided by the link they opened.
    """
    ui = Ui(view=build_view(state), human_id=seat, autoplay=autoplay,
            ironman=webstore.ironman())
    if settings is not None and seat == 1:
        # The daily follows the local ironman preference like any game: a puzzle
        # is exactly where a player wants to take a move back (Undo, U).
        ui.daily_day, ui.daily_kind = daily.daily_of(settings)
    ui.seat_bots = seat_bot_labels(state)
    if settings is not None:
        ui.random_seats = frozenset(pid for pid in state.players if not state.players[pid].is_neutral
                                    and settings.seat_strategy(pid) == RANDOM_STRATEGY)
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


def seat_bot_labels(state: GameState) -> dict[int, str]:
    """``{pid: "Knower · Search"}`` for every seat: the bot it is set to play
    (a legacy name read as its family), and the stop its knob sits on when the
    knob names stops and the stop is not just the family's own name. Capitalised rather than title-cased, as `render`'s win
    line does, so a drop-in keeps the shape its author gave its filename."""
    labels: dict[int, str] = {}
    for p in state.non_neutral_players():
        if not p.ai_strategy:
            continue
        family, aux = ai.canonical(p.ai_strategy, p.ai_params.aux)
        name = family[:1].upper() + family[1:]
        stop = ai.aux_stop_name(family, aux)
        # a stop that only repeats the family ("Thinker · Thinker") says nothing
        labels[p.id] = f"{name} · {stop}" if stop and stop.lower() != family.lower() else name
    return labels


def _accumulate_fog(state: GameState, human_id: int, seen: set[int],
                    intel: dict[int, tuple[int, int, float]]) -> set[int]:
    """Fold one board's sighting into a running `seen` set and rival `intel`, and
    return the systems currently in full view. Mutates `seen`/`intel` in place so
    the same helper serves both the live turn loop and replaying a resumed game.
    """
    if human_id == pbp.SPECTATOR or state.is_defeated(human_id):
        # A spectator holds no seat to see *from* (`pbp.SPECTATOR` is neutral's
        # id), and is only let in on a match with fog off, where every seat sees
        # the whole board anyway (`pbp.Match.watchable`).
        #
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


def _begin_game(settings: Settings, stage: tuple[gauntlet.Gauntlet, gauntlet.Stage] | None = None
                ) -> tuple[GameState, Ui, GameLog, int]:
    """Roll the seed and open a fresh match — the single start path, shared by the
    menu's Start Game and the map creator's Play so the two cannot drift apart."""
    ai.load_models()   # pick up files added since launch / named by a loaded config
    seed = resolve_seed(settings)
    state, ui, log = start_game(settings, seed, settings.autoplay, stage)
    return state, ui, log, seed


def _briefing_again() -> str:
    return "Tap here to read the briefing again" if config.touch_ui else "B: read the briefing again"


def stage_ui(ui: Ui, stage: tuple[gauntlet.Gauntlet, gauntlet.Stage] | None,
             briefing: bool = True) -> None:
    """Copy a gauntlet stage onto ``ui`` as plain values, so render never sees a
    `Gauntlet`. ``stage`` is what `menu.active_stage` found for the setup being
    played: None for an ordinary match, which leaves every stage field blank.
    A Retry or a rewind keeps the stage but not the briefing, which was read
    the first time round (B or the hint brings it back). Whether an earlier best
    already meets the target is read now, so the win overlay can tell this
    game's result from the stage's."""
    if stage is None:
        return
    g, s = stage
    ui.stage_label = f"{g.name} {gauntlet.place(g, s.id)} · {s.title}"
    ui.stage_target = s.target
    ui.stage_met_before = gauntlet.met(s, gauntlet.best(s))
    ui.stage_title = s.title
    ui.briefing = s.briefing
    ui.briefing_open = briefing and bool(s.briefing)
    # A stage with no hint of its own (a generated one, or one made on the board
    # with the hint left blank) still needs a line in the panel to tap back to
    # its briefing.
    ui.stage_hint = s.hint or (_briefing_again() if s.briefing else None)
    ui.has_next_stage = gauntlet.next_stage(g, s.id) is not None
    # Gated ways out (a hidden bonus stage, say), and whether a best already
    # meets each, so the win overlay can say what this win unlocks.
    mine = gauntlet.best(s)
    ui.stage_gates = tuple((label, title, target, mine is not None and target.met(mine))
                           for label, title, target in gauntlet.gates_out(g, s.id))


def run_ui(ui: Ui, r: gauntlet_run.Run, g: gauntlet.Gauntlet) -> None:
    """Mark ``ui`` as the run's own game, after `stage_ui`. It plays ironman, so
    History and Undo (both rewinds) wait until the game is decided; the Hint is not offered (`Ui.can_hint`); and the top bar says it is
    a run."""
    ui.run_at, ui.run_total = gauntlet_run.position(r, g)
    ui.run_id, ui.run_note, ui.run_ok = r.id, "", True
    ui.ironman = True
    if ui.stage_label is not None:
        ui.stage_label += " · run"


def record_run_step(state: GameState, ui: Ui, log: GameLog) -> None:
    """Judge the run's game the turn it is decided for the human (won, lost, or
    knocked out while the bots play on) and write the step (`gauntlet_run.finish`).
    Once: `run_note` is set by it. If the run has already ended some other way,
    this game is free play from here."""
    me = ui.human_id
    if log.path is None or (state.winner is None and not state.is_defeated(me)):
        return
    outcome = gauntlet_run.judge(state.winner == me, ui.can_post(state), ui.met_target(state))
    done = gauntlet_run.finish(ui.run_id, log.path.name, outcome, state.turn,
                      state.players[me].ships_lost, ui.run_total)
    if done is None:
        ui.run_id = ""
        return
    ui.run_note, ui.run_ok = gauntlet_run.note(outcome, ui.run_at, ui.run_total, ui.stage_target)


def campaign_tick(ui: Ui, settings: Settings, watch: campaign.Watcher) -> None:
    """Keep the side panel's campaign reminder current for the game being played.

    The menu's own watcher is reused, so a setup it already looked up shows at
    once, and the lookup is refreshed every `campaign.REFRESH_S` while the
    game runs: a node lost mid-game starts its grace countdown here
    without a reload. Nothing is shown for a watched replay or a play-by-post
    match, neither of which can be posted as a campaign move.
    """
    if ui.watched or ui.in_pbp:
        ui.campaign_label = None
        return
    watch.pump(settings, start=ui.campaign_start)
    ui.campaign_label = campaign.label(watch.status)


def stamp_campaign(ui: Ui, watch: campaign.Watcher) -> None:
    """Record what the campaign said about this setup as a match begins (Start,
    or a Retry or fork that plays it again from now), for the grace. A rewind
    within a match keeps the stamp it began with; a resumed save has none, and
    neither has a match forked out of a watched replay, since nothing looked
    its setup up while it was being watched (`campaign_tick`)."""
    ui.campaign_start = campaign.stamp(watch.status)


def start_game(settings: Settings, seed: int, autoplay: bool,
               stage: tuple[gauntlet.Gauntlet, gauntlet.Stage] | None = None
               ) -> tuple[GameState, Ui, GameLog]:
    """Build a fresh match and open a replay log to record it into."""
    state = build_state(settings, seed)
    ui = new_ui(state, autoplay, settings)
    stage_ui(ui, stage)
    return state, ui, replay.new_log(settings, seed, state)


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
    ui.hinted = log.hinted_from is not None  # ...and of whether a bot helped
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
        campaign=ui.campaign_start,
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


def copy_daily_result(settings: Settings, state: GameState, ui: Ui) -> str:
    """Copy a daily win's spoiler-free result line. Returns a line for the
    overlay. The clipboard only, as a challenge link: the line names no setup,
    and its link opens today's daily, not this one."""
    text = daily.result_line(ui.daily_day, state.turn,
                             state.players[ui.human_id].ships_lost, ui.daily_kind or daily.BOARD)
    if webstore.copy_to_clipboard(text):
        return "Result copied — paste it anywhere"
    print(text)
    return "Couldn't reach the clipboard — the result is in the console"


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


QUICK_POST_FAILED_MSG = "Couldn't post — press again for the entry form"


def quick_post(settings: Settings, state: GameState, ui: Ui,
               seed: int, log: GameLog) -> pbp.Request | None:
    """Post this result under ``ui.post_as`` without the entry form, and answer
    the post to poll. Falls back to the form (``post_to_leaderboard``) when it
    cannot be tried, as this is still the button press."""
    shared = challenge_settings(settings, state, ui, seed, log)
    request = share.quick_post(shared.to_token(), ui.post_as)
    if request is None:
        ui.post_state = "failed"
        ui.share_msg = post_to_leaderboard(settings, state, ui, seed, log)
        return None
    # The same upload, and the same consent, as the form's button: the score
    # names the log and the two may land in either order.
    share.post_log(log, shared.challenge.key if shared.challenge else "")
    ui.post_state = "posting"
    ui.share_msg = ""
    return request


def land_quick_post(ui: Ui, figures: tuple[int, int], status: str, body: str) -> None:
    """Show how a quick post went. A failure leaves the button on the entry form.
    ``figures`` is the posted ``(turns, lost)``."""
    summary = share.post_summary(body, *figures, ui.post_as) if status == pbp.OK else None
    if summary is None:
        print(f"quick post ended in state {status!r}: {body[:200]!r}")
        ui.post_state = "failed"
        ui.share_msg = QUICK_POST_FAILED_MSG
        return
    ui.post_state = "posted"
    ui.posted_key, ui.share_msg = summary


def wants_par(state: GameState, ui: Ui) -> bool:
    """Whether this win asks for the puzzle's par: a daily puzzle the player won
    with every turn played by hand (a hint already takes it off the board,
    ``Ui.can_post``)."""
    return (ui.daily_kind == daily.PUZZLE and bool(ui.daily_day) and ui.can_post(state)
            and ui.hand_turns >= state.turn)


def land_par(ui: Ui, figures: tuple[int, int], status: str, body: str) -> None:
    """Put the par's congratulation on the win overlay when the board says the
    result ``figures`` met it; say nothing otherwise, failures included."""
    par = daily.par_from_reply(body) if status == pbp.OK else None
    ui.par_note = daily.par_note(par, *figures) if par is not None else ""


def land_post_name(ui: Ui, status: str, body: str) -> None:
    """Put the signed-in player's claimed name on the quick post button, if the
    board answered one. Too late once a post has started: the answer it lands on
    names who it was posted as."""
    name = body.strip() if status == pbp.OK else ""
    if name and not ui.post_state:
        ui.post_as = name


def open_map_page(key: str) -> str:
    """Open the board's page for the map ``key``. Returns a line for the overlay
    when it could not, else ``""``."""
    page = webstore.leaderboard_url(paths.LEADERBOARD_GAME_PATH)
    if not page:
        return "No leaderboard is configured"
    url = f"{page}?key={quote(key, safe='')}"
    if webstore.open_url(url):
        return ""
    if webstore.copy_to_clipboard(url):
        return "Map page link copied"
    print(f"Map page:\n{url}")
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


# A `#g=<id>` launch URL opens a gauntlet built on the board's gauntlet page
# (`gauntlet.LINK_FRAGMENT`); `#gauntlet=<payload>` carries the whole file. Both
# end on the menu's Tutorial list with that gauntlet picked.
GAUNTLET_OPENING_MSG = "Opening gauntlet..."
GAUNTLET_UNREACHABLE_MSG = "Couldn't fetch that gauntlet from the leaderboard"
GAUNTLET_MISSING_MSG = "That gauntlet isn't on the leaderboard any more"
GAUNTLET_UNREADABLE_MSG = "That gauntlet downloaded but wouldn't open"


def gauntlet_request() -> tuple[str | None, dict | None]:
    """``(id, None)`` for a ``#g=<id>`` launch URL, ``(None, file)`` for an
    inline ``#gauntlet=`` one, else ``(None, None)``. Web only, like every other
    fragment read."""
    fragment = webstore.url_token()
    return gauntlet.link_id(fragment), gauntlet.inline_data(fragment)


# A `#daily` or `#puzzle` launch URL (or the menu's Daily and Puzzle buttons)
# fetches a day's daily of that kind from the board and applies it to the menu
# as a setup, as a pasted challenge link applies one (`daily`,
# docs/design/daily.md). The messages name the board's; a puzzle's say
# "daily puzzle" (`_daily_msg`).
DAILY_OPENING_MSG = "Fetching the daily challenge..."
DAILY_UNREACHABLE_MSG = "Couldn't fetch the daily challenge from the leaderboard"
DAILY_MISSING_MSG = "There's no daily challenge for that day"
DAILY_UNREADABLE_MSG = "The daily challenge downloaded but wouldn't open"


def _daily_msg(msg: str, kind: str) -> str:
    return msg.replace(daily.noun(daily.BOARD), daily.noun(kind))


# A `#gauntlet-spec=` launch URL, built on the board's gauntlet page, generates a
# gauntlet here (`gauntletgen`): every probe game played a slice per menu frame,
# then the result kept and opened on the Tutorial list, as a `#g=` one is.
GAUNTLET_GEN_MSG = "Generating a gauntlet: {pct:.0%} (games {done} of at most {total})"
GAUNTLET_GEN_REFUSED_MSG = "Can't generate that gauntlet: {why}"
GAUNTLET_GEN_EMPTY_MSG = "No generated map was won by any test bot: try a looser setup"
# Asked for on the board's form ("Ask for it to be listed publicly"), and only
# then: the generated file is saved to the board for an admin to list.
GAUNTLET_LISTING_MSG = "Generated {name}: sending it to the board to ask for a listing..."
GAUNTLET_LISTED_MSG = "Generated {name}: sent to the board, asking for it to be listed"
GAUNTLET_LISTING_FAILED_MSG = "Generated {name}, but couldn't send it to the board"
# How long the generator may hold a menu frame. A probe turn is a few ms for
# most bots, so this keeps the menu answering while it works.
GAUNTLET_GEN_BUDGET_S = 0.03


def spec_request(path: str) -> str:
    """The launch's spec fragment: the address bar's on the web, or a
    ``--gauntlet-spec`` file's contents on desktop."""
    if not path:
        return webstore.url_token()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"--gauntlet-spec {path!r}: {err}")
        return ""
    return gauntletgen.spec_fragment(data) if isinstance(data, dict) else ""


def replay_request() -> str:
    """The match id in a ``#log=<id>`` launch URL, or ``""``.

    Web only, like every other fragment read: off the browser there is no URL to
    carry one, and ``--watch`` is the equivalent there.
    """
    token = webstore.url_token()
    return token[len(LOG_FRAGMENT):] if token.startswith(LOG_FRAGMENT) else ""


def _decode_log(blob: str) -> GameLog | None:
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
# How often a client asks the endpoint what a shared match is doing. That is 720
# reads an hour per open tab, which is what the endpoint's separate read budget
# (`MAX_READS_PER_WINDOW` in `pbp.mjs`) is sized around; writes have their own.
# Most of them are briefs (`pbp.with_brief`), which carry no log.
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
PBP_FOGGED_MSG = "That match has fog of war on, so it can't be spectated"

# What the endpoint's own refusals mean from this side of the wire. Keyed by the
# exact `error` string `pbp.mjs` sends; anything not here is relayed with a
# prefix saying it was a refusal, so it still reads as an answer rather than as
# a connection problem.
# `pbp.mjs`'s CLAIMED_NAME: the "Your name" typed for seat 1 is somebody's
# claimed name. A 403 like a bad token, so it is matched before that one.
PBP_CLAIMED_ERROR = "name is claimed: sign in on the board as its owner, or pick another"
PBP_CLAIMED_MSG = ("That name is claimed — sign in on the leaderboard (Menu > You)"
                   " as its owner, or pick another")
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


# A tab nobody has touched reads less often: never sooner than a tenth of the
# time since the last input, up to a minute. Below fifty seconds that floor is
# under `PBP_POLL_MS`, so somebody using the board sees the ordinary cadence, and
# their first input after a lull drops the floor at once and lets an overdue
# read go out on that frame.
PBP_IDLE_SHARE = 10
PBP_IDLE_MAX_MS = 60_000


def pbp_idle_floor(idle_ms: int) -> int:
    """The soonest the next read may go, ``idle_ms`` after the last input."""
    return min(idle_ms // PBP_IDLE_SHARE, PBP_IDLE_MAX_MS)


# A spectator's screen is one nobody touches by design (a TV across the room), so
# the idle floor above, which keys on input, would slow it from the start. It
# keys on the match instead: while a turn has resolved or a seat has submitted
# within the last half hour, it reads at the ordinary cadence and steps the
# moment the last seat is in. Past that the table has gone quiet (or the tab was
# left open on a match that runs for days) and it drops to the idle ceiling, a
# twelfth of the reads. Any input brings it straight back, as it does for a player.
PBP_SPECTATOR_QUIET_MS = 30 * 60_000


def pbp_spectator_floor(quiet_ms: int, idle_ms: int) -> int:
    """The soonest a spectator's next read may go: none while the match moved, or
    the screen was touched, within ``PBP_SPECTATOR_QUIET_MS``; else the idle
    ceiling."""
    return 0 if min(quiet_ms, idle_ms) < PBP_SPECTATOR_QUIET_MS else PBP_IDLE_MAX_MS


def pbp_poll_delay(failures: int) -> int:
    """How long to wait before the next read, after ``failures`` in a row.

    The steady cadence while reads work; after a failure, doubling from
    ``PBP_RETRY_MS`` — past ``PBP_POLL_MS`` by the fifth — up to ``PBP_RETRY_MAX_MS``.
    """
    if failures <= 0:
        return PBP_POLL_MS
    return min(PBP_RETRY_MS * 2 ** (failures - 1), PBP_RETRY_MAX_MS)


def pbp_request() -> tuple[str, str] | None:
    """The ``(match id, token)`` a ``#pbp=<match>:<token>`` launch URL carries.

    Web only, like every other fragment read; ``--match`` is the desktop
    equivalent, exactly as ``--watch`` is for ``#log=``.
    """
    return pbp.parse_link(webstore.url_token())


def pbp_spectate_request() -> str | None:
    """The match id a ``#spectate=<match>`` launch URL carries. ``--spectate``
    is the desktop equivalent."""
    return pbp.parse_spectate_link(webstore.url_token())


def pbp_unwatchable(match: pbp.Match) -> str:
    """Why a spectator link may not open ``match``, or "" when it may."""
    return "" if match.watchable else PBP_FOGGED_MSG


def pbp_adopt(match: pbp.Match, seat: pbp.Seat, ui: Ui) -> None:
    """Take in what the endpoint just said about the live turn.

    The three fields the waiting overlay is built from, and the only place they
    are written outside a turn actually advancing — whether we have submitted is
    the endpoint's answer, never ours, so a submission that failed on the way out
    cannot leave the board held for a turn nobody is waiting on.
    """
    ui.pbp_match = match.match_id
    ui.pbp_title = match.title
    ui.pbp_waiting = tuple(match.waiting)
    ui.pbp_submitted = match.has_submitted(seat.seat)


def open_match(match: pbp.Match, seat: pbp.Seat, settings: Settings
               ) -> tuple[GameState, Ui, GameLog] | None:
    """Rebuild a shared match and sit down at our own seat. None if its log is bad.

    The stored log is the whole record (``pbp.match_log``), so this is an ordinary
    resume of it: ``resume_game`` replays it for the board and for everything a
    position is besides its board — fog remembered across turns, and how much of
    it was played by hand. The standing rules are the exception, since they are
    ours and the log came from whoever resolved last; the uploaded copy carries
    none (``pbp.shareable``), and any a pre-strip log still holds are dropped.
    Ours come from this device's own copy instead (``pbp.remembered_rules``),
    minus any whose source system has been lost since they were saved.

    The roster is re-stamped afterwards because ``replay.reconstruct`` restores
    the single-seat claim a solo game records, which knows nothing of a match
    seated at two and three.
    """
    ai.load_models()      # a shared setup may name a drop-in strategy for a bot seat
    log = pbp.match_log(match)
    if log is None:
        return None
    state, ui = resume_game(log, settings, seat.seat)
    # A spectator holds no rules; any this device keeps for the match are a
    # seat's, and stay where they are for when that seat is opened here again.
    if not seat.spectating:
        ui.auto_forward = pbp.remembered_rules(match.match_id)
        ui.prune_forward(state)
    pbp.seat_people(state, match.seats)
    pbp_adopt(match, seat, ui)
    return state, ui, log


def pbp_send(state: GameState, ui: Ui, seat: pbp.Seat) -> pbp.Request | None:
    """Submit this seat's orders for the live turn.

    The board goes on hold the moment the press lands rather than when the reply
    comes back: a round trip is long enough to get another order in, and orders
    queued after a submission would never be sent. ``pbp_heard`` hands the board
    back if the submission turns out not to have landed.
    """
    orders = list(ui.pending) + auto_forward_orders(state, ui)
    # The rules as they stood when the turn was sent, so closing the game before
    # it resolves still reopens with them (see `pbp.remember_rules`).
    pbp.remember_rules(seat.match_id, ui.auto_forward)
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
    error = (pbp.parse_body(body) or {}).get("error") if body else None
    if error == PBP_CLAIMED_ERROR:
        return PBP_CLAIMED_MSG
    if status == pbp.MISSING:
        return PBP_MISSING_MSG
    if status == pbp.REFUSED:
        return PBP_REFUSED_MSG
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


def pbp_open(settings: Settings, seed: int, seats: list[int] | None = None,
            deadline_hours: int = pbp.DEADLINE_HOURS,
            public: bool = False, title: str = "",
            name: str = "") -> tuple[str, pbp.Request | None]:
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

    ``public`` (``menu.MenuState.pbp_public``) lists the match on the
    leaderboard's lobby page, where its other seats are claimed; the reply then
    carries seat 1's token alone.

    ``title`` and ``name`` are the prompt's optional display text for the lobby
    (``menu.MenuState.pbp_title``/``pbp_name``); the name is remembered for the
    next prompt, blank included, so clearing it sticks.

    The id is minted here and sent rather than handed back, so the call is
    idempotent in the only sense that matters: a retry after a lost reply opens
    a second match rather than silently rewriting the first.
    """
    match_id = replay._new_match_id()
    if seats is None:
        seats = [pid for pid in range(1, settings.players + 1)]
    webstore.set_pbp_name(name)
    return match_id, pbp.create(match_id, settings, seed, seats, deadline_hours, public,
                                title, name)


def pbp_seat_links(match_id: str, tokens: dict[int, str]) -> list[tuple[int, str]]:
    """``(seat, link)`` pairs, sorted by seat — one per person seated.

    A full URL on the web, where there is a page for a link to point at. Off it
    there is none — so the fragment goes out bare, which is both what ``--match``
    takes and what a player pastes onto the address of the web build.
    """
    return [(seat, webstore.link_url(pbp.link_fragment(match_id, token))
                    or pbp.link_fragment(match_id, token))
            for seat, token in sorted(tokens.items())]


def pbp_spectate_link(match_id: str) -> str:
    """The spectator link to ``match_id``: a full URL on the web, the bare
    fragment off it, as ``pbp_seat_links`` does for a seat."""
    fragment = pbp.spectate_fragment(match_id)
    return webstore.link_url(fragment) or fragment


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
              seat: pbp.Seat) -> pbp.Request | None:
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
    # Each board's standing rules, the latest's being the live ones
    # (`Ui.show_history_rules`). Not in a shared match: its log carries no
    # rules (`pbp.shareable`), and a turn can resolve, and save the seat's
    # rules, while it is being reviewed.
    if not ui.in_pbp:
        ui.live_rules = ui.auto_forward
        ui.history_rules = [log.rules_for(i) for i in range(log.turn_count)] + [ui.auto_forward]
        ui.show_history_rules()
    ui.history_reveal = state.winner is not None
    # The end-of-game graphs: read once off the boards just built, and open by
    # default on a finished game, where they are the summary the review is for.
    ui.history_chart = stats.chart(history_states)
    if not ui.history_reveal:
        # mid-game, a rival is charted only for the turns the top bar showed it
        known = stats.sightings(history_states, [f[0] for f in history_fog], ui.human_id)
        ui.history_chart = stats.masked(ui.history_chart, known)
    ui.history_graph = ui.history_reveal
    # The coach: only on a decided game, when nothing is hidden any more, and
    # opened first when it has something to say beyond the turning point, which
    # every decided game has. A spectator holds no seat.
    ui.coach_review = None
    ui.coach_open, ui.coach_sel = False, -1
    if ui.history_reveal and ui.human_id in state.players and ui.human_id != 0:
        ui.coach_review = coach.review(history_states,
                                       [log.orders_for(i) for i in range(log.turn_count)],
                                       ui.human_id)
        ui.coach_open = any(n.kind != coach.TURN for n in ui.coach_review.notes)
    ui.graph_full = False
    ui.graph_hidden = set()
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


HINT_STRATEGY = "marshal"  # the advisor: strong, fast, deterministic, and reads only the board


def hint_orders(state: GameState, seat: int, strategy: str = HINT_STRATEGY) -> list[Order]:
    """What ``strategy`` would send for ``seat`` from this board, ignoring the
    player's own queued orders: the start of the turn, as every seat decides.

    Asked of a deep copy with the seat re-dealt to the advisor at its default
    knob, so nothing the bot reads or keeps can reach the live game, and through
    `engine.decide_seat`, so it draws nothing from the rng the turn will use. An
    advisor that is not loaded falls back to the heuristic, as any seat would
    (`ai.decide`). Like every bot it reads the whole board, fog or not: a hint
    is training help, which is why taking one takes the game off the board.
    """
    board = copy.deepcopy(state)
    player = board.players[seat]
    player.ai_strategy, player.ai_params = strategy, AiParams()
    orders = engine.decide_seat(board, seat, ai.decide)
    return [o for o in orders if o.owner_id == seat and o.ships > 0]


def press_hint(state: GameState, ui: Ui, log: GameLog | None) -> bool:
    """The Hint control (K): show the advisor's sends, or hide them when up.

    True when the press must ask first (`main`'s hint modal, then `take_hint`):
    in a game not yet hinted, since a hinted result is never posted
    (`Ui.can_post`), and that is said before it happens, not after.
    """
    if not ui.can_hint(state):
        return False
    if ui.hint_live(state):
        ui.hide_hint()
        return False
    if not ui.hinted:
        return True
    take_hint(state, ui, log)
    return False


def take_hint(state: GameState, ui: Ui, log: GameLog | None) -> None:
    """Show the advisor's sends for this turn as arrows. The hint is stamped into
    the log at once (`hinted_from`), so resuming the game keeps it hinted."""
    if not ui.can_hint(state):
        return
    ui.hint_orders = hint_orders(state, ui.human_id)
    ui.hint_turn = state.turn
    ui.hinted = True
    if log is not None and log.hinted_from is None:
        log.hinted_from = state.turn
        try:
            log.save()
        except OSError:
            pass  # a save failure must never interrupt play


def auto_forward_orders(state: GameState, ui: Ui) -> list[Order]:
    """Turn standing auto-forward rules into this turn's orders for the human
    (`Ui.forward_orders`, which the hint also reads to see what we already send)."""
    return ui.forward_orders(state)


def forget_unplayed_bot(state: GameState, ui: Ui, log: GameLog, settings: Settings | None) -> None:
    """A match whose first turn was ended by hand is a person's game from the
    start, so the bot the human seat was set up with is no part of it: put the
    seat back to the default bot and autoplay off, in the log, the live board and
    ``settings``, so the match keys to the map it was opened as.

    Without this, a seat-1 bot (only on the AI tab while Autoplay is on) moves
    ``challenge_key`` though it never decided a turn: watch Council play a
    campaign node, rewind to the start, win it by hand, and the win posts to a
    different map. Safe for the record because a claimed seat is never asked to
    decide, and a predicting opponent models a person's seat without reading its
    bot (``knower._model_for``); claiming it from the start, as ``autoplay`` off
    now does on a rebuild, is what this first turn's claim already did. Once the
    seat's bot has played a turn it stays, as it really did play.
    """
    seat = ui.human_id
    index = seat - 1
    recorded = Settings.from_dict(log.settings)
    plain = Settings()
    if (not recorded.autoplay and recorded.ai_strategy[index] == plain.ai_strategy[index]
            and recorded.ai[index] == plain.ai[index]):
        return
    # A copy, not an edit in place: a fork shares its parent's dict (`GameLog.fork`).
    data = copy.deepcopy(log.settings)
    data["autoplay"] = False
    data["ai_strategy"][index] = plain.ai_strategy[index]
    data["ai"][index] = asdict(plain.ai[index])
    log.settings = data
    if settings is not None:
        settings.autoplay = False
        settings.ai_strategy[index] = plain.ai_strategy[index]
        settings.ai[index] = AiParams()
    player = state.players.get(seat)
    if player is not None:
        player.ai_strategy, player.ai_params = plain.ai_strategy[index], AiParams()
        if seat in log.strategies:
            log.strategies[seat] = player.ai_strategy
    ui.seat_bots = seat_bot_labels(state)


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
    # A turn is played with the live rules, never a reviewed board's
    # (`Ui.show_history_rules`); review blocks End Turn, so this is a backstop.
    ui.leave_history_rules()
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
    if seat_orders is None and script is None:
        # Before any seat decides, the human seat's own `ai.decide` under autoplay
        # included. A shared match derives its own (`pbp.reseed`, at the caller).
        replay.reseed(state)
    if seat_orders is not None or script is not None:
        human_orders = None
    elif unclaimed and ui.autoplay:
        # Nothing to attribute orders to, so pass none and let
        # `engine._collect_orders` decide this seat in its own loop, exactly as it
        # does every other. Computing them here *as well* would run the seat's
        # strategy twice.
        human_orders = None
    elif ui.autoplay:
        human_orders = engine.decide_seat(state, ui.human_id, ai.decide)
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
        if log.turn_count == 1 and not ui.autoplay and seat_orders is None and script is None:
            forget_unplayed_bot(state, ui, log, settings)
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
        if ui.run_id and not ui.run_note:
            gauntlet_run.keep_log(log)   # the copy that outlasts a reload on the web build
            record_run_step(state, ui, log)
    if ui.can_post(state) and settings is not None:
        record_best(settings, state, ui)
        # Read here, once the result is ours, never in `render`.
        ui.post_as = share.quick_post_name()
        ui.post_name_due = True
        if not was_over and wants_par(state, ui):
            ui.par_due = (state.turn, state.players[ui.human_id].ships_lost)
    ui.clear_pending()
    ui.prune_forward(state)   # a rule dies with the system it forwarded out of
    if ui.in_pbp and not ui.spectating:
        # A shared match's log goes up without our rules, so this device keeps its
        # own copy — the one a solo game gets from `log.record_turn` above.
        pbp.remember_rules(ui.pbp_match, ui.auto_forward)
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


# --------------------------------------------------------------------------- #
# The setup menu's side of gauntlets, runs and dailies. Each answers what it
# did plus a `MenuNote` for the menu to show; `main` applies the note to its
# `MenuState`, and another frontend applies it to its own.
# --------------------------------------------------------------------------- #
@dataclass
class MenuNote:
    """What the setup menu should show after a call, applied in field order."""

    status: tuple[str, bool] | None = None      # the status line: text, ok
    gauntlet: str | None = None                 # open the gauntlet list on this id
    said: tuple[str, str, bool] | None = None   # (gauntlet id, text, ok), said against it


Found = tuple[gauntlet.Gauntlet, gauntlet.Stage]


def open_run(settings: Settings, fresh: bool, picked: gauntlet.Gauntlet | None,
             gauntlets: list[gauntlet.Gauntlet], enter: Callable[[gauntlet.Gauntlet, int], Found | None]
             ) -> tuple[tuple[GameState, Ui, GameLog, int] | None, MenuNote]:
    """Start a run of the gauntlet picked in the list (``fresh``), or carry on
    the active run: resume its undecided game if it has one, never a fresh copy,
    else begin its next stage. None, with a word in the list, when there is
    nothing to play. ``enter`` puts the menu on a stage, as picking it does."""
    if fresh:
        g = picked
        if g is None or not gauntlet_run.offered(g):
            return None, MenuNote()
        r: gauntlet_run.Run | None = gauntlet_run.start(g)
    else:
        r = gauntlet_run.active()
        g = gauntlet.find(gauntlets, r.gauntlet_id) if r is not None else None
        if g is None:
            return None, MenuNote()
        r = gauntlet_run.active_for(g)
        if r is None:
            return None, MenuNote(said=(g.id, "The gauntlet has changed since the run began, so the run is over",
                                        False))
    assert r is not None
    sid = gauntlet_run.stage_to_play(r, g)
    found = enter(g, sid) if sid is not None else None
    if found is None:
        return None, MenuNote()
    if r.playing is not None:
        log = gauntlet_run.restore_log(r.playing.log)
        if log is None:
            gauntlet_run.lose_game(r)
            return None, MenuNote(said=(g.id, "The run's game couldn't be loaded, so the run is over", False))
        ai.load_models()   # a resumed game may name a drop-in strategy
        state, ui = resume_game(log, settings)
        stage_ui(ui, found, briefing=log.turn_count == 0)
        run_ui(ui, r, g)
        return (state, ui, log, log.seed), MenuNote()
    state, ui, log, seed = _begin_game(settings, found)
    run_ui(ui, gauntlet_run.begin(r, found[1].id, log), g)
    return (state, ui, log, seed), MenuNote()


def open_linked_gauntlet(gid: str | None, data: dict | None) -> tuple[pbp.Request | None, MenuNote]:
    """Act on a gauntlet link at launch: open one already in hand, or start the
    fetch and answer it for the loop to poll. A stored gauntlet never changes,
    so one kept from an earlier visit opens straight away, offline included."""
    if data is not None:
        g = gauntlet.remember(data)
        return None, MenuNote(gauntlet=g.id if g is not None else None)
    if gid is None:
        return None, MenuNote()
    if any(g.id == gid for g in gauntlet.remembered()):
        return None, MenuNote(gauntlet=gid)
    pending = gauntlet.fetch(gid)
    if pending is None:
        print(f"cannot fetch gauntlet {gid!r}: no endpoint is configured")
        return None, MenuNote(status=(GAUNTLET_UNREACHABLE_MSG, False))
    return pending, MenuNote(status=(GAUNTLET_OPENING_MSG, True))


def land_gauntlet(state: str, body: str) -> MenuNote:
    """The gauntlet fetch answered: keep it and open the list, or say why not."""
    data = gauntlet.from_reply(body) if state == pbp.OK else None
    g = gauntlet.remember(data) if data is not None else None
    if g is not None:
        return MenuNote(status=("", True), gauntlet=g.id)
    print(f"gauntlet fetch ended in state {state!r}: {body[:120]!r}")
    return MenuNote(status=(GAUNTLET_MISSING_MSG if state == pbp.MISSING
                            else GAUNTLET_UNREADABLE_MSG if state == pbp.OK
                            else GAUNTLET_UNREACHABLE_MSG, False))


def open_daily(day: str = "", kind: str = daily.BOARD) -> tuple[pbp.Request | None, MenuNote]:
    """Start fetching ``day``'s daily of ``kind`` (today's when blank) and
    answer the request for the loop to poll, or say why there is none."""
    pending = daily.fetch(day, kind)
    if pending is None:
        return None, MenuNote(status=(_daily_msg(DAILY_UNREACHABLE_MSG, kind), False))
    return pending, MenuNote(status=(_daily_msg(DAILY_OPENING_MSG, kind), True))


def land_daily(settings: Settings, state: str, body: str, kind: str = daily.BOARD) -> MenuNote:
    """The daily fetch for ``kind`` answered: put its setup on the menu, or say
    why not. The setup is stashed as a shared link's is, so an installed app
    relaunched without the fragment still has it."""
    got = daily.from_reply(body) if state == pbp.OK else None
    if got is None:
        print(f"daily fetch ended in state {state!r}: {body[:120]!r}")
        return MenuNote(status=(_daily_msg(
            DAILY_MISSING_MSG if state == pbp.MISSING
            else DAILY_UNREADABLE_MSG if state == pbp.OK
            else DAILY_UNREACHABLE_MSG, kind), False))
    day, kind, setup = got
    settings.copy_from(setup)
    daily.remember(day, settings, kind)
    webstore.set(paths.WEB_SHARED_SETTINGS_KEY, settings.to_token())
    return MenuNote()


def start_generation(fragment: str) -> tuple[gauntletgen.Job | None, MenuNote]:
    """Start generating from a ``#gauntlet-spec=`` fragment, or None when the
    fragment is not one (or the spec can never deal, which the status says)."""
    data = gauntletgen.spec_from_fragment(fragment)
    if data is None:
        return None, MenuNote()
    ai.load_models()
    try:
        job = gauntletgen.job_from_link(data, random_seed())
    except ValueError as err:
        return None, MenuNote(status=(GAUNTLET_GEN_REFUSED_MSG.format(why=err), False))
    return job, MenuNote(status=(GAUNTLET_GEN_MSG.format(pct=0.0, done=0, total=job.total), True))


def pump_generation(settings: Settings, job: gauntletgen.Job,
                    budget_s: float = GAUNTLET_GEN_BUDGET_S) -> tuple[gauntletgen.Job | None, MenuNote]:
    """Play the generator on for one slice of ``budget_s``; once done, keep the
    gauntlet and open the Tutorial list on it. Returns the job while it runs.

    A probe game goes through ``build_state``, which pushes its setup's knobs
    into ``config``; the menu's own setup is pushed back after every slice, so
    nothing the menu draws reads a probe's balance."""
    try:
        job.step(budget_s)
    finally:
        apply_globals(settings)
    if not job.done:
        return job, MenuNote(status=(GAUNTLET_GEN_MSG.format(
            pct=job.progress(), done=len(job.rows), total=job.total), True))
    data = job.result()
    g = gauntlet.remember(data) if data is not None else None
    if g is None:
        return None, MenuNote(status=(GAUNTLET_GEN_EMPTY_MSG, False))
    return None, MenuNote(gauntlet=g.id, said=(g.id, f"Generated {g.name}: {len(g.stages)} stages", True))


def ask_listing(job: gauntletgen.Job) -> tuple[tuple[str, str, pbp.Request] | None, MenuNote]:
    """A finished run whose link ticked "Ask for it to be listed publicly":
    start saving it to the board under the author's name, and answer the
    gauntlet's id and name with the save for the loop to poll. Nothing is sent for any
    other run, or when there is no board."""
    data = job.result() if job.done and job.listing is not None else None
    if data is None:
        return None, MenuNote()
    pending = gauntlet.ask_listing(data, job.listing or "")
    if pending is None:
        return None, MenuNote()
    return ((data["id"], data["name"], pending),
            MenuNote(said=(data["id"], GAUNTLET_LISTING_MSG.format(name=data["name"]), True)))


def land_listing(gid: str, name: str, state: str) -> MenuNote:
    """The board answered the listing save: say whether it took, in the
    Gauntlets list if it is still open."""
    if state == pbp.OK:
        return MenuNote(said=(gid, GAUNTLET_LISTED_MSG.format(name=name), True))
    return MenuNote(said=(gid, GAUNTLET_LISTING_FAILED_MSG.format(name=name), False))
