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

from starconquest import (ai, config, engine, fog, mapgen, menu, paths, render,
                          replay, softkeyboard, upload, viewstate, webstore)
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


def new_ui(state: GameState, autoplay: bool, settings: Optional[Settings] = None) -> Ui:
    ui = Ui(view=build_view(state), human_id=1, autoplay=autoplay)
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


def start_game(settings: Settings, seed: int, autoplay: bool) -> tuple[GameState, Ui, GameLog]:
    """Build a fresh match and open a replay log to record it into."""
    state = build_state(settings, seed)
    return state, new_ui(state, autoplay, settings), replay.new_log(settings, seed)


def resume_game(log: GameLog, settings: Settings) -> tuple[GameState, Ui]:
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
        log, on_turn=lambda s: _accumulate_fog(s, 1, seen, intel))
    state, loaded = replay_view
    settings.copy_from(loaded)
    ui = new_ui(state, loaded.autoplay, loaded)   # sets ui.visible from the final board
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
    if not paths.LEADERBOARD_SUBMIT_URL:
        return "No leaderboard is configured"
    shared = challenge_settings(settings, state, ui, seed, log)
    # Send the replay first, so it is on its way before the tab steals focus —
    # and only here. Pressing this button is what consents to uploading a game;
    # `share_challenge` sends nothing, and a match merely played sends nothing.
    upload.post_log(log, shared.challenge.key if shared.challenge else "")
    token = shared.to_token()
    url = f"{paths.LEADERBOARD_SUBMIT_URL}#{token}"
    if webstore.open_url(url):
        return "Leaderboard opened — add your name to post"
    if webstore.copy_to_clipboard(url):
        return "Leaderboard link copied — open it to post"
    print(f"Leaderboard entry link:\n{url}")
    return "Couldn't open a browser — link printed to the console"


def build_history(ui: Ui, log: GameLog) -> tuple[
        list[GameState], list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]]]:
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

    def capture(s: GameState) -> None:
        states.append(copy.deepcopy(s))
        visible = _accumulate_fog(s, ui.human_id, seen, intel)
        fog.append((set(visible), set(seen), dict(intel)))

    replay.reconstruct(log, on_turn=capture)
    return states, fog


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
                 settings: Settings | None = None) -> None:
    """Advance one turn. In autoplay the human seat is also driven by the AI.

    The human orders actually applied are recorded into ``log`` and the file is
    rewritten, so the on-disk log always matches the live game (and a crash loses
    at most the turn in progress). Given ``settings``, a win the human earned is
    also filed as their best on this setup, so replaying a challenge can show it.
    """
    was_over = state.winner is not None
    was_defeated = state.is_defeated(ui.human_id)
    human_orders = (
        ai.decide(state, ui.human_id)
        if ui.autoplay
        else list(ui.pending) + auto_forward_orders(state, ui)
    )
    record = engine.end_turn(state, human_orders=human_orders, decide=ai.decide)
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
        # `upload.CHECKPOINT_TURNS` turns and again on the turn it is decided. The
        # cadence is what keeps an *abandoned* game — the kind no score ever
        # carries — from being lost entirely. `due` holds every condition,
        # including the opt-in itself, so this line cannot send by accident.
        if upload.due(log, state.winner is not None):
            upload.post_log(log, log.setup_key())
    if (state.winner == ui.human_id and ui.hand_turns > 0 and settings is not None):
        record_best(settings, state, ui)
    ui.clear_pending()
    ui.prune_forward(state)   # a rule dies with the system it forwarded out of
    ui.reset_selection()
    ui.reset_route()          # a plan is only valid for the ownership it was built on
    refresh_fog(state, ui)
    # Snap the camera out to the whole map right as there stops being anything left
    # to hide — but only on the turn that crosses into it, not every turn a
    # spectator keeps fast-forwarding through an already-decided match.
    if (state.winner is not None and not was_over) or (
            state.is_defeated(ui.human_id) and not was_defeated):
        ui.reset_view(state)


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
    """Web only: fire a synthetic browser resize shortly after boot.

    See the comment at the call site in ``main`` — this is a fire-and-forget
    background task so it doesn't hold up the first frame.
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
        # yielding a beat for the browser's layout to settle.
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

    # Two scenes share the one window: the setup menu and the game board. The
    # menu builds `state`/`ui` on "start"; pressing M in-game drops back to it.
    menu_state = MenuState()
    state: GameState | None = None
    ui: Ui | None = None
    log: GameLog | None = None       # replay log of the live match (None while in menu)
    current_seed = 0
    scene = "game" if args.no_menu else "menu"
    if args.no_menu:
        current_seed = resolve_seed(settings)
        state, ui, log = start_game(settings, current_seed, settings.autoplay)

    # If the last saved match was left unfinished, offer to resume it on the menu.
    resume_prompt: GameLog | None = None
    if not args.no_menu:
        candidate = replay.latest_log()
        if candidate is not None and not candidate.finished and candidate.turn_count > 0:
            resume_prompt = candidate

    running = True
    confirm_quit = False   # showing the "are you sure?" modal; gates every quit path
    confirm_rewind = False  # showing the destructive mid-game rewind confirm modal
    # History-review snapshots, built on entering history mode and dropped on exit;
    # `live_fog` stashes the live fog triple so it survives a history visit intact.
    history_states: list[GameState] = []
    history_fog: list[tuple[set[int], set[int], dict[int, tuple[int, int, float]]]] = []
    live_fog: tuple[set[int], set[int], dict[int, tuple[int, int, float]]] | None = None
    auto_accum = 0
    play_accum = 0
    fullscreen = False
    windowed_size = (config.SCREEN_W, config.SCREEN_H)  # restored when leaving fullscreen
    while running:
        dt = clock.tick(config.FPS)
        # Reflow to fill the window whenever its size changes.
        screen = pygame.display.get_surface()
        if screen.get_size() != (config.SCREEN_W, config.SCREEN_H):
            config.SCREEN_W, config.SCREEN_H = screen.get_size()
            if state is not None and ui is not None:
                ui.view = build_view(state)
                ui.reset_view(state)   # re-frame for the new size, not the whole map
        for event in pygame.event.get():
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
                    history_states, history_fog, live_fog = [], [], None
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
                    ai.load_models()   # pick up files added since launch / named by a loaded config
                    current_seed = resolve_seed(settings)
                    state, ui, log = start_game(settings, current_seed, settings.autoplay)
                    scene = "game"
                    auto_accum = 0
                elif action == "quit":
                    confirm_quit = True
                continue

            # Past the menu and modal handlers, so scene == "game": state/ui/log are live.
            assert state is not None and ui is not None and log is not None
            action = game_input.handle_event(event, state, ui)
            if action == "quit":
                confirm_quit = True
            elif action == "menu":
                scene = "menu"
                state, ui, log = None, None, None
            elif action == "share":
                # Game-over only (input only returns this there), and only for a
                # result worth sending — render gates the button the same way.
                if state.winner == ui.human_id and ui.hand_turns > 0:
                    ui.share_msg = share_challenge(settings, state, ui, current_seed, log)
            elif action == "leaderboard":
                # Same gate as "share": the two buttons offer one result by two
                # channels, so neither may fire on a result that isn't yours.
                if state.winner == ui.human_id and ui.hand_turns > 0:
                    ui.share_msg = post_to_leaderboard(settings, state, ui, current_seed, log)
            elif action == "end_turn" and not ui.autoplay:
                resolve_turn(state, ui, log, settings)
                play_accum = 0   # re-time the play cadence from this step
            elif action == "toggle_play":
                # In history mode this drives the replay scrubber; starting it while
                # parked on the final frame replays from the opening position.
                if ui.history and not ui.playing and ui.history_turn >= ui.history_max:
                    ui.history_turn = 0
                ui.playing = not ui.playing
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
                history_states, history_fog, live_fog = [], [], None
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
                    history_states, history_fog = [], []
                    if live_fog is not None:
                        ui.visible, ui.seen, ui.player_intel = live_fog
                        live_fog = None
                elif log is not None and log.turn_count > 0:
                    history_states, history_fog = build_history(ui, log)
                    if history_states:
                        live_fog = (set(ui.visible), set(ui.seen), dict(ui.player_intel))
                        ui.history = True
                        ui.playing = False
                        ui.reset_selection()
                        ui.reset_route()
                        ui.sel_forward = None
                        ui.history_max = len(history_states) - 1
                        ui.history_turn = ui.history_max
                        ui.history_reveal = state.winner is not None
            elif action == "rewind":
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
                    history_states, history_fog, live_fog = [], [], None
                    auto_accum = 0
                else:
                    confirm_rewind = True   # mid-game rewind is destructive: confirm

        if scene == "game":
            assert state is not None and ui is not None and log is not None
            if (ui.history and ui.playing
                    and not confirm_quit and not confirm_rewind):
                # Replay playback: step the scrubber one turn per PLAY_MS, stopping when
                # it reaches the final recorded turn (like a video reaching the end).
                play_accum += dt
                if play_accum >= PLAY_MS:
                    play_accum = 0
                    ui.history_turn = min(ui.history_max, ui.history_turn + 1)
                    if ui.history_turn >= ui.history_max:
                        ui.playing = False
            elif (state.winner is None
                    and not confirm_quit and not confirm_rewind and not ui.history
                    and ui.mode != viewstate.ROUTING):
                if ui.autoplay:
                    auto_accum += dt
                    if auto_accum >= step_delay(ui, AUTOPLAY_MS):
                        auto_accum = 0
                        resolve_turn(state, ui, log, settings)
                elif ui.playing:
                    play_accum += dt
                    if play_accum >= step_delay(ui, PLAY_MS):
                        play_accum = 0
                        resolve_turn(state, ui, log, settings)

        if scene == "menu":
            # Soft-keyboard typing arrives outside the SDL event queue, so the
            # menu needs a per-frame poll as well as its event handler.
            menu.pump(menu_state, settings)
            menu.draw(screen, menu_state, settings)
            if resume_prompt is not None:
                menu.draw_resume_prompt(screen, resume_prompt)
        else:
            assert state is not None and ui is not None   # scene == "game"
            if ui.history and history_states:
                # Draw the reconstructed past board with the fog for that turn — or, for
                # a finished game, everything revealed. `render` just draws the state and
                # fog it's handed; it neither knows nor cares the board is historical.
                i = max(0, min(ui.history_turn, len(history_states) - 1))
                view_state = history_states[i]
                if ui.history_reveal:
                    all_ids = set(view_state.systems)
                    ui.visible, ui.seen, ui.player_intel = all_ids, set(all_ids), {}
                else:
                    ui.visible, ui.seen, ui.player_intel = history_fog[i]
                render.draw(screen, view_state, ui)
            else:
                render.draw(screen, state, ui)
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
