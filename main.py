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

import pygame

from starconquest import ai, config, engine, fog, mapgen, menu, paths, render, replay
from starconquest import input as game_input
from starconquest.geometry import WorldView
from starconquest.menu import MenuState
from starconquest.model import GameState, Order
from starconquest.replay import GameLog
from starconquest.settings import Settings, build_state, resolve_seed
from starconquest.viewstate import Ui

AUTOPLAY_MS = 350  # delay between auto-resolved turns in autoplay mode
PLAY_MS = 350      # delay between turns while play/pause (P) is running


def build_view(state: GameState) -> WorldView:
    return WorldView(mapgen.map_bounds(state), config.play_rect(), padding=50)


def _web_is_touch() -> bool:
    """True on a touch-capable browser (phone/tablet), so the web build gets the
    same finger-sized hit targets as Android. Desktop browsers report 0 touch
    points and stay compact. pygbag augments the stdlib ``platform`` module with the
    JS ``window``; guard everything so a missing attr just means "not a touch device".
    """
    import platform as _platform

    try:
        return int(_platform.window.navigator.maxTouchPoints) > 0
    except Exception:
        return False


def _apply_shared_link(settings: Settings) -> None:
    """Web only: if the URL carries a ``#<token>`` shared-settings fragment,
    decode it over ``settings`` in place (same effect as CLI args pre-filling the
    menu). Guarded like ``_web_is_touch`` — a missing/malformed hash is a no-op,
    never a crash."""
    if not paths.is_web():
        return
    import platform as _platform

    try:
        token = str(_platform.window.location.hash).lstrip("#")
        if token:
            settings.copy_from(Settings.from_token(token))
    except Exception:
        pass


def new_ui(state: GameState, autoplay: bool) -> Ui:
    ui = Ui(view=build_view(state), human_id=1, autoplay=autoplay)
    refresh_fog(state, ui)   # seed visibility from the opening position
    return ui


def _accumulate_fog(state: GameState, human_id: int, seen: set[int],
                    intel: dict[int, tuple[int, int, float]]) -> set[int]:
    """Fold one board's sighting into a running `seen` set and rival `intel`, and
    return the systems currently in full view. Mutates `seen`/`intel` in place so
    the same helper serves both the live turn loop and replaying a resumed game.
    """
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
    return state, new_ui(state, autoplay), replay.new_log(settings, seed)


def resume_game(log: GameLog, settings: Settings) -> tuple[GameState, Ui]:
    """Rebuild a saved in-progress match and adopt its settings for the menu.

    ``log`` is then reused as the live log, so continued play appends to the very
    same file the game was resumed from. Fog-of-war memory (`seen`/`player_intel`)
    is rebuilt across every replayed turn so resuming doesn't forget places you
    explored then lost sight of.
    """
    seen: set[int] = set()
    intel: dict[int, tuple[int, int, float]] = {}
    replay_view = replay.reconstruct(
        log, ai.decide, on_turn=lambda s: _accumulate_fog(s, 1, seen, intel))
    state, loaded = replay_view
    settings.copy_from(loaded)
    ui = new_ui(state, loaded.autoplay)   # sets ui.visible from the final board
    ui.seen |= seen                        # ...plus memory of the whole game
    intel.update(ui.player_intel)          # final-turn intel wins for live rivals
    ui.player_intel = intel
    return state, ui


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

    replay.reconstruct(log, ai.decide, on_turn=capture)
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
        sys = state.systems.get(src)
        if sys is None or sys.owner_id != ui.human_id:
            continue  # dormant while the system isn't ours (may resume if recaptured)
        if not state.are_adjacent(src, dest):
            continue
        send = ui.available(state, src) - keep
        if send > 0:
            orders.append(Order(ui.human_id, src, dest, send))
    return orders


def resolve_turn(state: GameState, ui: Ui, log: GameLog | None = None) -> None:
    """Advance one turn. In autoplay the human seat is also driven by the AI.

    The human orders actually applied are recorded into ``log`` and the file is
    rewritten, so the on-disk log always matches the live game (and a crash loses
    at most the turn in progress).
    """
    human_orders = (
        ai.decide(state, ui.human_id)
        if ui.autoplay
        else list(ui.pending) + auto_forward_orders(state, ui)
    )
    engine.end_turn(state, human_orders=human_orders, decide=ai.decide)
    if log is not None:
        log.record_turn(human_orders, human_ai=ui.autoplay)
        if state.winner is not None:
            log.mark_finished(state.winner)
        try:
            log.save()
        except OSError:
            pass  # a save failure must never interrupt play
    ui.clear_pending()
    ui.reset_selection()
    refresh_fog(state, ui)


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
    # sizes, so this must run before any draw.
    sw, sh = pygame.display.get_surface().get_size()
    fit = min(sw / config.BASE_SCREEN_W, sh / config.BASE_SCREEN_H)
    touch = paths.is_android() or (paths.is_web() and _web_is_touch())
    boost = config.TOUCH_UI_SCALE if touch else 1.0
    config.apply_ui_scale(max(1.0, fit) * boost)
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
        for event in pygame.event.get():
            # Android hardware/gesture Back arrives as K_AC_BACK; normalise it to
            # Esc so every existing "cancel / back out" handler below just works.
            if event.type == pygame.KEYDOWN and event.key == pygame.K_AC_BACK:
                event = pygame.event.Event(
                    pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b", mod=0)
            if confirm_quit:
                # Modal: swallow all other input until the user answers.
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                        running = False
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        confirm_quit = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    quit_r, cancel_r = render.confirm_quit_buttons(screen)
                    if quit_r.collidepoint(event.pos):
                        running = False
                    elif cancel_r.collidepoint(event.pos):
                        confirm_quit = False
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
            elif action == "end_turn" and not ui.autoplay:
                resolve_turn(state, ui, log)
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
                ui.clear_pending()
                auto_accum = 0
            elif action == "restart":
                current_seed += 1
                state, ui, log = start_game(settings, current_seed, ui.autoplay)
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
                        ui.sel_order = ui.sel_forward = None
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
                    and not confirm_quit and not confirm_rewind and not ui.history):
                if ui.autoplay:
                    auto_accum += dt
                    if auto_accum >= AUTOPLAY_MS:
                        auto_accum = 0
                        resolve_turn(state, ui, log)
                elif ui.playing:
                    play_accum += dt
                    if play_accum >= PLAY_MS:
                        play_accum = 0
                        resolve_turn(state, ui, log)

        if scene == "menu":
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
