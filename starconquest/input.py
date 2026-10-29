"""Event handling: translate pygame input into selection/targeting changes and
high-level actions. Mutates only the Ui (and queues human Orders); it never
touches the simulation directly — resolving a turn is main.py's job via the
engine. Returns an action string ('end_turn', 'restart', 'retry', 'quit',
'toggle_autoplay', 'toggle_play', 'toggle_fast_forward', 'toggle_history',
'toggle_route', 'rewind', 'menu', 'share', 'pbp_copy_seat', 'next_stage') or
None.
"""

from __future__ import annotations

import copy

import pygame

from . import config, starlore, stats
from .geometry import dist, point_segment_dist
from .model import GameState
from .viewstate import CHOOSING, FOOTER_RECTS, IDLE, ROUTING, SELECTED, Ui


def pick_node(state: GameState, ui: Ui, pos: tuple[int, int]) -> int | None:
    """The system under ``pos`` — the *nearest* one within reach, so overlapping
    or clustered nodes resolve to the closest rather than whichever draws first.
    Tiny systems get a minimum tap reach (``config.NODE_TAP_MIN``) so they stay
    comfortably tappable on touch screens."""
    best: int | None = None
    best_d = 0.0
    for sid, sys in state.systems.items():
        sp = ui.view.to_screen(sys.pos)
        d = dist(sp, pos)
        reach = max(config.node_radius(sys.production) + 2, config.NODE_TAP_MIN)
        if d <= reach and (best is None or d < best_d):
            best, best_d = sid, d
    return best


def _play_rect_center() -> tuple[float, float]:
    """Anchor point for the zoom +/- buttons — they have no cursor position to
    zoom toward the way a wheel scroll does, so they zoom toward the map
    viewport's centre instead."""
    x, y, w, h = config.play_rect()
    return (x + w / 2, y + h / 2)


def _seek_scrubber(ui: Ui, pos) -> None:
    """Map a click/drag x within the scrubber track to a turn index (0..max).

    The travel is the recorded rect inset by the knob's radius at each end, which
    is where ``widgets.slider`` puts the knob — invert anything else and the
    knob drifts away from the pointer toward the extremes."""
    x, _y, w, _h = ui.scrubber_rect
    travel = w - 2 * config.SLIDER_KNOB_R
    ui.clear_fading_marks()   # jumping, not stepping: a mark from wherever we
                              # were scrubbing away from would be stale here
    if travel <= 0 or ui.history_max <= 0:
        ui.history_turn = 0
        return
    t = max(0.0, min(1.0, (pos[0] - x - config.SLIDER_KNOB_R) / travel))
    ui.history_turn = round(t * ui.history_max)


def _over_graph(ui: Ui, pos) -> bool:
    """Is ``pos`` on the docked graph panel? It spans the map's width from the
    top of its tabs to the bottom bar, so the plot rect's foot and the first
    tab's top bound it."""
    if not ui.graph_tab_rects:
        return False
    _x, top, _w, _h = ui.graph_tab_rects[0]
    return top <= pos[1] < config.SCREEN_H - config.HUD_BOTTOM_H and pos[0] < config.SCREEN_W - config.HUD_RIGHT_W


def _seek_graph(ui: Ui, pos) -> None:
    """Map a click/drag x across the graph's plot to the nearest turn. The plot
    spans turn 0 at its left edge to ``history_max`` at its right, exactly as
    `render._draw_history_graph` lays the curves out."""
    x, _y, w, _h = ui.graph_plot_rect
    ui.clear_fading_marks()   # a jump, as with the scrubber
    if w <= 0 or ui.history_max <= 0:
        ui.history_turn = 0
        return
    t = max(0.0, min(1.0, (pos[0] - x) / w))
    ui.history_turn = round(t * ui.history_max)


def _handle_history_event(event, state: GameState, ui: Ui) -> str | None:
    """Modal history-review input: scrub the turn, rewind, or exit, and look
    around the map as in live play (drag to pan, wheel or −/+ to zoom, R to
    reset). Mutates only `Ui`; enter/exit and rewind are returned to main."""
    if event.type == pygame.KEYDOWN:
        if event.key in (pygame.K_ESCAPE, pygame.K_h):
            return "toggle_history"
        if event.key == pygame.K_p:
            return "toggle_play"  # play/pause the replay at sim speed
        if event.key == pygame.K_r:
            ui.reset_view(state)  # as in live play: the camera is free here too
            return None
        if event.key == pygame.K_g and ui.history_chart is not None:
            ui.history_graph = not ui.history_graph
            ui.dragging_graph = False
            return None
        if event.key == pygame.K_TAB and ui.history_graph:
            step = -1 if event.mod & pygame.KMOD_SHIFT else 1
            ui.graph_metric = (ui.graph_metric + step) % len(stats.METRICS)
            return None
        if event.key == pygame.K_f and ui.history_graph:
            ui.graph_full = not ui.graph_full
            return None
        if ui.history_graph and pygame.K_1 <= event.key <= pygame.K_9:
            # the legend's rows, top to bottom
            seats = ui.graph_seats()
            n = event.key - pygame.K_1
            if n < len(seats):
                ui.toggle_trace(seats[n])
            return None
        if event.key == pygame.K_LEFT:
            ui.history_turn = max(0, ui.history_turn - 1)
            ui.playing = False
            ui.clear_fading_marks()   # jumping, not stepping: they'd be stale here
        elif event.key == pygame.K_RIGHT:
            ui.history_turn = min(ui.history_max, ui.history_turn + 1)
            ui.playing = False
            ui.clear_fading_marks()
        elif event.key == pygame.K_HOME:
            ui.history_turn = 0
            ui.playing = False
            ui.clear_fading_marks()
        elif event.key == pygame.K_END:
            ui.history_turn = ui.history_max
            ui.playing = False
            ui.clear_fading_marks()
        return None
    if event.type == pygame.MOUSEMOTION:
        # hover still drives the detail panel, wherever the map is showing
        if ui.history_graph and (ui.graph_full or _over_graph(ui, event.pos)):
            ui.hover = None
        else:
            ui.hover = pick_node(state, ui, event.pos)
        if ui.dragging_scrubber:
            _seek_scrubber(ui, event.pos)
        elif ui.dragging_graph:
            _seek_graph(ui, event.pos)
        elif ui.pan_active and event.buttons[0]:
            ui.view.pan(event.pos[0] - ui.pan_last[0], event.pos[1] - ui.pan_last[1], room=config.REVIEW_ROOM)
            ui.pan_last = event.pos
        return None
    if event.type == pygame.MOUSEWHEEL:
        # the map's wheel zoom, wherever the map is what's under the pointer
        pos = pygame.mouse.get_pos()
        if not _over_side_panel(pos) and not (ui.history_graph and (ui.graph_full or _over_graph(ui, pos))):
            ui.view.zoom_at(pos, config.ZOOM_WHEEL_STEP**event.y, room=config.REVIEW_ROOM)
        return None
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        if ui.exit_history_rect[2] and _point_in_rect(event.pos, ui.exit_history_rect):
            return "toggle_history"
        if _press_camera(state, ui, event.pos):
            return None
        if ui.graph_button_rect[2] and _point_in_rect(event.pos, ui.graph_button_rect):
            ui.history_graph = not ui.history_graph
            ui.dragging_graph = False
            return None
        if ui.history_graph:
            if ui.graph_full_rect[2] and _point_in_rect(event.pos, ui.graph_full_rect):
                ui.graph_full = not ui.graph_full
                return None
            for i, rect in enumerate(ui.graph_tab_rects):
                if rect[2] and _point_in_rect(event.pos, rect):
                    ui.graph_metric = i
                    return None
            for pid, rect in ui.graph_trace_rects:
                if rect[2] and _point_in_rect(event.pos, rect):
                    ui.toggle_trace(pid)
                    return None
            if ui.graph_all_rect[2] and _point_in_rect(event.pos, ui.graph_all_rect):
                ui.graph_hidden.clear()
                return None
            if ui.graph_plot_rect[2] and _point_in_rect(event.pos, ui.graph_plot_rect):
                ui.dragging_graph = True
                ui.playing = False  # same as grabbing the scrubber
                _seek_graph(ui, event.pos)
                return None
        if ui.play_pause_rect[2] and _point_in_rect(event.pos, ui.play_pause_rect):
            return "toggle_play"
        if ui.rewind_button_rect[2] and _point_in_rect(event.pos, ui.rewind_button_rect):
            return "rewind"
        if ui.history_prev_rect[2] and _point_in_rect(event.pos, ui.history_prev_rect):
            ui.history_turn = max(0, ui.history_turn - 1)  # touch/mouse equivalent of Left
            ui.playing = False
            ui.clear_fading_marks()
            return None
        if ui.history_next_rect[2] and _point_in_rect(event.pos, ui.history_next_rect):
            ui.history_turn = min(ui.history_max, ui.history_turn + 1)  # ...and Right
            ui.playing = False
            ui.clear_fading_marks()
            return None
        if ui.scrubber_rect[2] and _point_in_rect(event.pos, ui.scrubber_rect):
            ui.dragging_scrubber = True
            ui.playing = False  # grabbing the scrubber pauses playback
            _seek_scrubber(ui, event.pos)
            return None
        if _over_map(event.pos) and not (ui.history_graph and (ui.graph_full or _over_graph(ui, event.pos))):
            # nothing to select in a review, so every press on the map is a pan
            _arm_pan(ui, event.pos)
        return None
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        ui.dragging_scrubber = False
        ui.dragging_graph = False
        ui.pan_active = False
    return None


def _press_camera(state: GameState, ui: Ui, pos) -> bool:
    """A press on the on-map camera cluster (Reset view, zoom −/+): moves the
    camera (as freely as the scene allows, `_room`) and reports it handled.
    Each rect is guarded on its width, so a scene that doesn't draw the
    cluster (render zeroes it) never matches."""
    if ui.reset_view_rect[2] and _point_in_rect(pos, ui.reset_view_rect):
        ui.reset_view(state)
    elif ui.zoom_minus_rect[2] and _point_in_rect(pos, ui.zoom_minus_rect):
        ui.view.zoom_at(_play_rect_center(), 1 / config.ZOOM_BUTTON_STEP, room=_room(ui))
    elif ui.zoom_plus_rect[2] and _point_in_rect(pos, ui.zoom_plus_rect):
        ui.view.zoom_at(_play_rect_center(), config.ZOOM_BUTTON_STEP, room=_room(ui))
    else:
        return False
    return True


def _room(ui: Ui) -> float:
    """How far the camera may stray (`WorldView.zoom_at`): more in history
    review, whose docked graphs cover the map's foot, than in live play."""
    return config.REVIEW_ROOM if ui.history else config.LIVE_ROOM


def _over_map(pos) -> bool:
    """Is ``pos`` inside the map viewport (`config.play_rect`)?"""
    return _point_in_rect(pos, config.play_rect())


def _point_in_rect(pos, rect) -> bool:
    x, y, w, h = rect
    return x <= pos[0] <= x + w and y <= pos[1] <= y + h


def _over_side_panel(pos) -> bool:
    """Is ``pos`` in the right-hand info panel column (rather than over the map)?"""
    return pos[0] >= config.SCREEN_W - config.HUD_RIGHT_W


def _toggles_play(ui: Ui, event) -> bool:
    """Is ``event`` the Play/Pause control (the P key, or a click on its button)?

    The one press during a running film that must not skip it — see the note in
    `handle_event`. Checked ahead of the blanket "any press skips" rule so pausing
    can freeze a playback in place instead of discarding it.
    """
    if event.type == pygame.KEYDOWN:
        return event.key == pygame.K_p
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return bool(ui.play_pause_rect[2]) and _point_in_rect(event.pos, ui.play_pause_rect)
    return False


def _moves_camera(ui: Ui, event) -> bool:
    """Is ``event`` a camera-only control (the R key, or the on-map Reset / -/+)?

    The other exemption from the blanket "any press skips a film", and for a
    plainer reason than Play/Pause's: where the camera is pointed changes nothing
    about the turn being played back, so a look around has no business discarding
    it. The wheel is already exempt by falling outside the press types the rule
    names at all, and this brings the buttons and the key that do the same job
    into line with it.

    It matters most under autoplay, where films chain back to back with no gap
    between them (see `main`'s chaining): every one of these presses would land on
    a running film, be spent skipping it, and leave the whole cluster looking
    dead — the next turn's film starts immediately, so the second press meets one
    too. Each rect test is guarded on its width, so the cluster is simply absent
    (full-screen graphs zero all three) rather than matching a stale hit-box.
    """
    if event.type == pygame.KEYDOWN:
        return event.key == pygame.K_r
    if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
        return False
    return any(rect[2] and _point_in_rect(event.pos, rect)
               for rect in (ui.reset_view_rect, ui.zoom_minus_rect, ui.zoom_plus_rect))


def _toggles_autoplay(ui: Ui, event) -> bool:
    """Is ``event`` the Autoplay / Take control control (the A key, or a click
    on its button)?

    A third exemption from the blanket "any press skips a film", and the one
    that most needs it: under autoplay, films chain with no gap between them
    (see `main`'s chaining), so a press that only skips would spend itself on
    whichever turn happens to be playing and never reach `main`'s
    ``"toggle_autoplay"`` handler at all — the next turn's film is already up
    by the time a second press arrives. Play/Pause is hidden under autoplay
    (it would be a no-op — see the footer's own comment), which makes this the
    *only* way to stop the automatic advance, and it has to work in one press
    or there is none.

    Nothing here needs freezing the way Play/Pause does: turning autoplay off
    doesn't need the running film to stop, only the *next* one not to start —
    `main`'s chaining re-reads `ui.autoplay` fresh at the moment each film
    lands, so the turn already showing plays out to the end exactly as it
    would have, and control is back the instant it does.
    """
    if event.type == pygame.KEYDOWN:
        return event.key == pygame.K_a
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return bool(ui.autoplay_button_rect[2]) and _point_in_rect(event.pos, ui.autoplay_button_rect)
    return False


def _handle_invite_event(event, ui: Ui) -> str | None:
    """Answer the play-by-post invite overlay: copy one seat's link, or dismiss
    it for good.

    The clipboard write is main's job — this module never reaches `webstore` —
    so a Copy row only names the seat (`ui.pbp_copy_seat`) and returns the
    action that asks for it. Continue needs nothing from main at all: clearing
    `ui.pbp_invite` is a plain `Ui` mutation, so it happens right here.
    """
    if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
        return None
    if ui.pbp_invite_close_rect[2] and _point_in_rect(event.pos, ui.pbp_invite_close_rect):
        ui.pbp_invite = ()
        ui.pbp_invite_rects = {}
        ui.pbp_invite_close_rect = (0, 0, 0, 0)
        ui.pbp_invite_copied = 0
        return None
    for seat, rect in ui.pbp_invite_rects.items():
        if rect[2] and _point_in_rect(event.pos, rect):
            ui.pbp_copy_seat = seat
            return "pbp_copy_seat"
    return None


def _track_score_hover(event, ui: Ui) -> None:
    """Note which scoreboard entry is under the pointer, for its tooltip. A
    press counts too, so a tap reaches it where there is no hover; nothing is
    consumed, whatever scene the event goes on to."""
    if event.type not in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
        return
    ui.score_hover = next((pid for pid, rect in ui.score_rects if _point_in_rect(event.pos, rect)), None)


# What a press can change that a pinch must be able to put back (`_pinch`): the
# selection and the popup, and the orders and rules a tap can create.
_PRESS_FIELDS = ("mode", "selected", "dest", "chosen", "sel_order", "sel_forward",
                 "forward_armed", "forward_replaced", "editing_existing", "popup_pos",
                 "route_sel", "route_dest", "lore_sid")


def _press_snapshot(ui: Ui) -> tuple:
    return ({name: copy.copy(getattr(ui, name)) for name in _PRESS_FIELDS},
            list(ui.pending), copy.deepcopy(ui.auto_forward))


def _pinch(event, state: GameState, ui: Ui) -> bool:
    """Two fingers zoom and pan the map. True if ``event`` was spent on it.

    SDL turns the *first* finger into a mouse press, which reached the board
    before the second finger landed: it may have picked a system, opened the
    popup or queued a send. So a pinch starts by putting back what that press
    changed (`press_undo`, taken before every press on a touch screen), and the
    emulated mouse is ignored until every finger is up again.
    """
    if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
        pos = (event.x * config.SCREEN_W, event.y * config.SCREEN_H)
        if event.type == pygame.FINGERUP:
            ui.fingers.pop(event.finger_id, None)
            if len(ui.fingers) < 2:
                ui.pinch_last = None
            if not ui.fingers:
                ui.pinch_lock = False
            return True
        ui.fingers[event.finger_id] = pos
        if len(ui.fingers) != 2:
            return True
        (x0, y0), (x1, y1) = ui.fingers.values()
        spread = max(1.0, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        mid = ((x0 + x1) / 2, (y0 + y1) / 2)
        if ui.pinch_last is None:
            if not ui.pinch_lock and ui.press_undo is not None:
                fields, pending, rules = ui.press_undo
                for name, value in fields.items():
                    setattr(ui, name, value)
                ui.pending[:] = pending
                ui.auto_forward.clear()
                ui.auto_forward.update(rules)
            ui.press_undo = None
            ui.pinch_lock = True
            ui.drag_src, ui.drag_active, ui.pan_active = None, False, False
            ui.dragging_slider = ui.dragging_popup = ui.dragging_scrubber = ui.dragging_graph = False
            ui.route_press = ui.route_box = False
        else:
            last_spread, last_mid = ui.pinch_last
            ui.view.pan(mid[0] - last_mid[0], mid[1] - last_mid[1], room=_room(ui))
            ui.view.zoom_at((round(mid[0]), round(mid[1])), spread / last_spread, room=_room(ui))
        ui.pinch_last = (spread, mid)
        return True
    if ui.pinch_lock and event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                                        pygame.MOUSEMOTION):
        return True
    if event.type == pygame.MOUSEBUTTONDOWN and config.touch_ui:
        ui.press_undo = _press_snapshot(ui)
    return False


def handle_event(event, state: GameState, ui: Ui) -> str | None:
    _track_score_hover(event, ui)
    if _pinch(event, state, ui):
        return None
    # Play-by-post's invite overlay is modal too, and ahead of everything below:
    # a match this fresh (right after we created it) can be running no film and
    # holds no history or win to check first.
    if ui.pbp_invite:
        return _handle_invite_event(event, ui)

    # A star's info card is up over the board: any press puts it away and is spent
    # doing so, like the briefing card's below.
    if ui.lore_sid is not None and not ui.history and state.winner is None:
        if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            ui.lore_sid = None
        if event.type != pygame.MOUSEMOTION:
            return None

    # A turn playback is running: any press skips it — except Play/Pause, which
    # must freeze it in place rather than lose it (`main` does the freezing; this
    # is only about not discarding the film here), the camera controls, which
    # only decide where you are looking from (`_moves_camera`), and Autoplay /
    # Take control, the one way to stop autoplay's automatic advance at all
    # (`_toggles_autoplay`). Checked before everything else, and the two scenes
    # differ on purpose for every other press.
    #
    # In live play the press is *consumed*. The footer strip is still drawn during
    # a film (the film board's winner stays None until the turn closes), so a
    # press that fell through could resolve a second turn underneath a running
    # one. In history the press falls through instead, because there the film is
    # a transition between turns and the controls are a scrubber: swallowing it
    # would mean a drag never started.
    if (ui.film is not None and event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN)
            and not _toggles_play(ui, event) and not _moves_camera(ui, event)
            and not _toggles_autoplay(ui, event)):
        ui.stop_film()
        if not ui.history:
            return None

    # A gauntlet stage's briefing card is up over the opening board: any press
    # puts it away and is spent doing so, so the press that dismisses it can't
    # also select a system or end the turn behind it.
    if ui.briefing_open and ui.in_stage and not ui.history and state.winner is None:
        if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            ui.briefing_open = False
        if event.type != pygame.MOUSEMOTION:
            return None

    # History mode is a modal review scene (entered mid-game or after a win):
    # scrub / rewind / exit only, no board interaction. Checked first so it wins
    # over the game-over branch when reviewing a finished match.
    if ui.history:
        return _handle_history_event(event, state, ui)

    # Game over: restart / back-to-menu / quit, plus entering history to review
    # the finished game behind the fog of war.
    if state.winner is not None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_t:
                return "retry"
            if event.key == pygame.K_n:
                # In a stage N is Next stage, or nothing after the last one.
                if ui.in_stage:
                    return "next_stage" if ui.offers_next_stage(state) else None
                return "restart"
            if event.key == pygame.K_m:
                return "menu"
            if event.key == pygame.K_h:
                return "toggle_history"
            if event.key == pygame.K_c:
                return "share"
            if event.key == pygame.K_l:
                return "leaderboard"
            if event.key == pygame.K_ESCAPE:
                return "quit"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if ui.share_button_rect[2] and _point_in_rect(event.pos, ui.share_button_rect):
                return "share"
            if ui.leaderboard_button_rect[2] and _point_in_rect(event.pos, ui.leaderboard_button_rect):
                return "leaderboard"
            if ui.retry_button_rect[2] and _point_in_rect(event.pos, ui.retry_button_rect):
                return "retry"
            if ui.restart_button_rect[2] and _point_in_rect(event.pos, ui.restart_button_rect):
                return "restart"
            if ui.next_stage_rect[2] and _point_in_rect(event.pos, ui.next_stage_rect):
                return "next_stage"
            if ui.menu_button_rect[2] and _point_in_rect(event.pos, ui.menu_button_rect):
                return "menu"
            if ui.history_button_rect[2] and _point_in_rect(event.pos, ui.history_button_rect):
                return "toggle_history"
            if ui.quit_button_rect[2] and _point_in_rect(event.pos, ui.quit_button_rect):
                return "quit"
        return None

    # Route mode is its own interaction scene: a group selection and a destination
    # being composed into a plan, with nothing committed until it is confirmed. It
    # takes the whole event stream so the live-play ladder below — every branch of
    # which assumes one selected system — is simply not reachable while it is on.
    # Placed after the game-over branch, not before: a finished match keeps its
    # overlay controls, and the mode can't be entered or held there at all.
    if ui.mode == ROUTING:
        return _handle_route_event(event, state, ui)

    if event.type == pygame.MOUSEMOTION:
        if ui.dragging_slider:  # dragging the popup's count slider
            # first, so a slider drag can never also move the popup it sits on
            ui.set_slider_from_x(state, event.pos[0])
        elif ui.dragging_popup:  # dragging the send popup by its background
            ui.popup_pos = (event.pos[0] - ui.popup_drag_off[0], event.pos[1] - ui.popup_drag_off[1])
        elif ui.drag_src is not None and event.buttons[0]:
            # a press on an owned system is being dragged toward a target
            if dist(event.pos, ui.drag_start) > config.DRAG_THRESHOLD:
                ui.drag_active = True
            ui.drag_pos = event.pos
            ui.hover = pick_node(state, ui, event.pos)  # highlight the drag target
        elif ui.pan_active and event.buttons[0]:
            # a press on empty space is panning the camera
            dx = event.pos[0] - ui.pan_last[0]
            dy = event.pos[1] - ui.pan_last[1]
            ui.view.pan(dx, dy, room=config.LIVE_ROOM)
            ui.pan_last = event.pos
        else:
            ui.hover = pick_node(state, ui, event.pos)
        return None

    if event.type == pygame.KEYDOWN:
        return _handle_key(event, state, ui)

    if event.type == pygame.MOUSEWHEEL:
        # MOUSEWHEEL carries no position, so ask pygame for the cursor's current
        # one — same idea as _handle_key reading key mods.
        pos = pygame.mouse.get_pos()
        if ui.count_adjust_active():
            ui.step_count(state, event.y)
        elif not _over_side_panel(pos):  # zooming the map from off-map is disorienting
            ui.view.zoom_at(pos, config.ZOOM_WHEEL_STEP**event.y, room=config.LIVE_ROOM)
        return None

    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        ui.dragging_popup = False
        ui.dragging_slider = False
        if ui.drag_active and ui.drag_src is not None:
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            _commit_drag(state, ui, event.pos, shift)
        ui.drag_src = None
        ui.drag_active = False
        ui.pan_active = False
        return None

    if event.type == pygame.MOUSEBUTTONDOWN:
        if event.button == 1:
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            action = _handle_left_click(state, ui, event.pos, shift)
            _arm_drag(state, ui, event.pos)
            return action
        if event.button == 3:
            _cancel(ui)
    return None


def _arm_drag(state: GameState, ui: Ui, pos) -> None:
    """After a left-press, arm a drag-to-target if it landed on (and selected) an
    owned system — the source. A tap that opened the popup (CHOOSING) or hit a UI
    element doesn't arm one, so drag never fights those interactions."""
    node = pick_node(state, ui, pos)
    if (
        node is not None
        and ui.mode == SELECTED
        and ui.selected == node
        and not ui.dragging_popup
        and not ui.dragging_slider
        and state.systems[node].owner_id == ui.human_id
    ):
        ui.drag_src = node
        ui.drag_start = pos
        ui.drag_pos = pos
        ui.drag_active = False
    else:
        ui.drag_src = None
        ui.drag_active = False


def _arm_pan(ui: Ui, pos) -> None:
    """A press that hits nothing at all (no node, no lane, no button — the
    same empty-space press ``_cancel`` already claims for deselection) arms a
    camera-pan drag instead. No competing tap meaning exists there once
    ``_cancel`` has run, so it's a free gesture, and it works identically for
    mouse and touch (unlike a modifier-key or alternate-button pan would)."""
    ui.pan_active = True
    ui.pan_last = pos


def _commit_drag(state: GameState, ui: Ui, pos, shift: bool) -> None:
    """Release of a drag over a system: if it's an adjacent neighbour of the
    source, open the send/forward popup for that lane (same as tapping it)."""
    src = ui.drag_src
    target = pick_node(state, ui, pos)
    if src is None or target is None or target == src:
        return
    if not state.are_adjacent(src, target):
        return
    ui.reset_selection()
    ui.sel_forward = None
    ui.selected = src
    ui.mode = SELECTED
    forward = shift or ui.opens_on_forward(state, src)
    ui.begin_send(state, target, forward=forward)


def _clear_selected(ui: Ui) -> None:
    """Context-sensitive cancel/clear — shared by the X/Backspace/Delete keys and
    their footer button: discard whatever the popup is editing (a composed send, or
    the queued order / standing rule it was reopened on), else drop a dormant rule
    highlighted without a popup, else drop the selected system's own forward rule.
    A no-op when none of those apply."""
    if ui.mode == CHOOSING:
        ui.cancel_send()
    elif ui.sel_forward is not None:
        ui.clear_forward(ui.sel_forward)
    elif ui.selected is not None:
        ui.clear_forward(ui.selected)


def _handle_key(event, state: GameState, ui: Ui) -> str | None:
    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        # A play-by-post turn we have already submitted is not ours to end again:
        # the turn advances when the last seat is in, not when anyone presses a
        # key. Suppressed rather than swallowed further up so every other control
        # — panning, looking, opening history — keeps working while you wait.
        # A spectator has no turn to end at all.
        return None if ui.awaiting_others(state) or ui.spectating else "end_turn"
    if event.key in (pygame.K_p, pygame.K_a):
        # Play and autoplay both resolve turns on a clock of their own, which a
        # shared match has no room for: its turns advance when the last seat
        # submits. Dead here exactly as their footer buttons are (`render`), so
        # the key and the button cannot disagree about it.
        if ui.in_pbp:
            return None
        return "toggle_play" if event.key == pygame.K_p else "toggle_autoplay"
    if event.key == pygame.K_f:
        # main gates this on the human actually being knocked out (see
        # Ui.can_fast_forward); from here it is just another action string.
        return "toggle_fast_forward"
    if event.key in (pygame.K_x, pygame.K_BACKSPACE, pygame.K_DELETE):
        _clear_selected(ui)
        return None
    if event.key == pygame.K_n:
        # a stage's seed is pinned: a new map would leave it (Retry stays)
        return None if ui.in_stage else "restart"
    if event.key == pygame.K_b and ui.in_stage:
        ui.briefing_open = True
        return None
    if event.key == pygame.K_i:
        _open_lore(state, ui, ui.selected if ui.selected is not None else ui.hover)
        return None
    if event.key == pygame.K_r:
        ui.reset_view(state)
        return None
    if event.key == pygame.K_m:
        return "menu"
    if event.key == pygame.K_h:
        return "toggle_history"
    if event.key == pygame.K_g:
        return "toggle_route"
    if event.key == pygame.K_ESCAPE:
        if ui.mode != IDLE or ui.sel_order is not None or ui.sel_forward is not None:
            _cancel(ui)
            return None
        return "quit"
    return None


def _handle_route_event(event, state: GameState, ui: Ui) -> str | None:
    """Route mode's whole event stream: build a proposal, confirm it or drop it.

    No stages: a tap on a system means exactly one thing given what is on screen
    (see ``Ui.route_tap``), and a tap commits on press, as everywhere else in the
    game. The two sub-modes differ only in what a drag from empty space does.

    In **chain** mode drag is spent on the selection box, so panning is right-drag
    on a mouse and the on-map Reset / −/+ cluster on touch. That costs little: zoom
    1 already fits the whole map, so there is nothing to pan to until you have
    zoomed in, and Reset undoes that in one tap. **Rally** mode has no box — its
    picks are all taps — so there left-drag pans like it does in live play.
    """
    if event.type == pygame.MOUSEMOTION:
        if ui.route_press and event.buttons[0]:
            if dist(event.pos, ui.drag_start) > config.DRAG_THRESHOLD:
                ui.route_box = True
            ui.drag_pos = event.pos
        elif ui.pan_active and event.buttons[0]:
            ui.view.pan(event.pos[0] - ui.pan_last[0], event.pos[1] - ui.pan_last[1], room=config.LIVE_ROOM)
            ui.pan_last = event.pos
        ui.hover = pick_node(state, ui, event.pos)
        return None

    if event.type == pygame.KEYDOWN:
        return _handle_route_key(event, state, ui)

    if event.type == pygame.MOUSEWHEEL:
        pos = pygame.mouse.get_pos()
        if not _over_side_panel(pos):  # the panel holds the route summary, not the map
            ui.view.zoom_at(pos, config.ZOOM_WHEEL_STEP**event.y, room=config.LIVE_ROOM)
        return None

    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        if ui.route_box:
            x0, y0 = ui.drag_start
            x1, y1 = event.pos
            ui.add_route_box(state, (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        ui.route_press = False
        ui.route_box = False
        ui.pan_active = False
        return None

    if event.type == pygame.MOUSEBUTTONUP and event.button == 3:
        ui.pan_active = False
        return None

    if event.type == pygame.MOUSEBUTTONDOWN:
        if event.button == 3:  # right-drag pans, on a mouse
            _arm_pan(ui, event.pos)
            return None
        if event.button == 1:
            return _handle_route_click(state, ui, event.pos)
    return None


def _handle_route_click(state: GameState, ui: Ui, pos) -> str | None:
    """A left-press in route mode: a control, a system, or empty space."""
    handled, action = _more_press(state, ui, pos)
    if not handled:
        handled, action = _handle_global_buttons(state, ui, pos)
    if handled:
        return action
    if ui.route_confirm_rect[2] and _point_in_rect(pos, ui.route_confirm_rect):
        ui.confirm_route(state)
        return None
    if ui.route_cancel_rect[2] and _point_in_rect(pos, ui.route_cancel_rect):
        ui.reset_route()
        return None
    if ui.route_mode_rect[2] and _point_in_rect(pos, ui.route_mode_rect):
        ui.set_route_rally(state, not ui.route_rally)
        return None
    if ui.route_auto_rect[2] and _point_in_rect(pos, ui.route_auto_rect):
        ui.auto_rally(state)
        return None
    node = pick_node(state, ui, pos)
    if node is not None:
        ui.route_tap(state, node)
        return None

    # Empty space: a box in chain mode, a pan in rally mode, which has no box to
    # drag. Neither clears the group — this mode confirms and cancels explicitly,
    # so a stray tap must not undo the work.
    if ui.route_rally:
        _arm_pan(ui, pos)
        return None
    ui.route_press = True
    ui.route_box = False
    ui.drag_start = pos
    ui.drag_pos = pos
    return None


def _handle_route_key(event, state: GameState, ui: Ui) -> str | None:
    """Keys in route mode. Enter confirms rather than ending the turn — the turn
    can't be ended from here at all (render records no end_turn_rect), so the most
    obvious key keeps pointing at the most obvious action."""
    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        if ui.route_plan:
            ui.confirm_route(state)
        return None
    if event.key in (pygame.K_g, pygame.K_ESCAPE):
        ui.reset_route()
        return None
    if event.key == pygame.K_TAB:
        # switch sub-mode, the keyboard half of the footer's Mode button
        ui.set_route_rally(state, not ui.route_rally)
        return None
    if event.key == pygame.K_t and ui.route_rally:
        ui.auto_rally(state)
        return None
    if event.key in (pygame.K_x, pygame.K_BACKSPACE, pygame.K_DELETE):
        # clear the group but stay in the mode, mirroring Clear elsewhere
        ui.route_sel = set()
        ui.route_dest = None
        ui.route_press = False
        ui.route_box = False
        ui.recompute_route(state)
        return None
    if event.key == pygame.K_m:
        return "menu"
    if event.key == pygame.K_n:
        return None if ui.in_stage else "restart"
    if event.key == pygame.K_r:
        ui.reset_view(state)
        return None
    return None


def _open_lore(state: GameState, ui: Ui, sid: int | None) -> None:
    """Put up the star info card for system ``sid``, if the catalogue has its name."""
    if sid in state.systems and state.systems[sid].name in starlore.LORE:
        ui.lore_sid = sid


def _handle_global_buttons(state: GameState, ui: Ui, pos) -> tuple[bool, str | None]:
    """The bar/on-map buttons that mean the same thing in every interaction scene.

    Returns ``(handled, action)``. Shared by live play and route mode rather than
    duplicated: each test is guarded on its rect having a width, and render zeroes
    the rect of anything it didn't draw, so a button simply goes inert in a scene
    whose strip leaves it out. Route mode gets Menu and the camera cluster this
    way, and gets the live-play toggles excluded, without either side listing the
    other's buttons.
    """
    if ui.history_button_rect[2] and _point_in_rect(pos, ui.history_button_rect):
        return True, "toggle_history"
    if ui.autoplay_button_rect[2] and _point_in_rect(pos, ui.autoplay_button_rect):
        return True, "toggle_autoplay"
    if ui.fast_forward_rect[2] and _point_in_rect(pos, ui.fast_forward_rect):
        return True, "toggle_fast_forward"
    if ui.restart_live_button_rect[2] and _point_in_rect(pos, ui.restart_live_button_rect):
        return True, "restart"
    if ui.menu_button_rect[2] and _point_in_rect(pos, ui.menu_button_rect):
        return True, "menu"
    if ui.quit_button_rect[2] and _point_in_rect(pos, ui.quit_button_rect):
        return True, "quit"
    if ui.clear_button_rect[2] and _point_in_rect(pos, ui.clear_button_rect):
        _clear_selected(ui)
        return True, None
    if ui.stage_hint_rect[2] and _point_in_rect(pos, ui.stage_hint_rect):
        ui.briefing_open = True
        return True, None
    if ui.lore_button_rect[2] and _point_in_rect(pos, ui.lore_button_rect):
        _open_lore(state, ui, ui.selected)
        return True, None
    if _press_camera(state, ui, pos):
        return True, None
    if ui.route_button_rect[2] and _point_in_rect(pos, ui.route_button_rect):
        return True, "toggle_route"
    return False, None


def _more_press(state: GameState, ui: Ui, pos) -> tuple[bool, str | None]:
    """The footer's overflow button and its list (`render._lay_out_footer`).

    Returns ``(handled, action)`` like `_handle_global_buttons`; a press on a
    row is left unhandled, for the caller's usual tests to take. The button
    toggles the list. While it is open, any press closes it: one on a row is that
    button's (the row recorded its rect under the button's own attribute, so the
    caller's tests find it), and any other press — between rows, or off the list
    onto the map — only closes it, as tapping away from an open menu does.
    """
    if ui.more_button_rect[2] and _point_in_rect(pos, ui.more_button_rect):
        ui.more_open = not ui.more_open
        return True, None
    if not ui.more_open:
        return False, None
    ui.more_open = False
    on_row = any(_point_in_rect(pos, getattr(ui, attr)) for attr in FOOTER_RECTS
                 if getattr(ui, attr)[2])
    if ui.more_menu_rect[2] and _point_in_rect(pos, ui.more_menu_rect) and on_row:
        return False, None   # the caller's own tests find the row's button
    return True, None


def _popup_press(state: GameState, ui: Ui, pos) -> bool:
    """A press on the send popup's controls (or its background, which drags it).
    True if it was one."""
    # On-lane −/+ buttons: a scroll-wheel-free way to change the active count.
    # Tested before node-picking so a button click adjusts the send rather than
    # (re)targeting. Zero-width rects (no count being adjusted) never match.
    if ui.minus_rect[2] and _point_in_rect(pos, ui.minus_rect):
        ui.step_count(state, -1)
        return True
    if ui.plus_rect[2] and _point_in_rect(pos, ui.plus_rect):
        ui.step_count(state, 1)
        return True

    # Send popup (CHOOSING): Send/Forward tabs pick the mode, the presets retune
    # the count, and Cancel discards the active send or forwarding lane.
    if ui.mode == CHOOSING and ui.selected is not None and ui.dest is not None:
        if ui.send_tab_rect[2] and _point_in_rect(pos, ui.send_tab_rect):
            ui.set_forward_mode(state, False)
            return True
        if ui.forward_tab_rect[2] and _point_in_rect(pos, ui.forward_tab_rect):
            ui.set_forward_mode(state, True)
            return True
        # the two preset buttons: Half/All (Send) or 50%/100% (Forward, one lane)
        if ui.send_half_rect[2] and _point_in_rect(pos, ui.send_half_rect):
            ui.set_share(50) if ui.forward_armed else ui.send_half(state)
            return True
        if ui.send_all_rect[2] and _point_in_rect(pos, ui.send_all_rect):
            ui.set_share(100) if ui.forward_armed else ui.send_all(state)
            return True
        if ui.hold_minus_rect[2] and _point_in_rect(pos, ui.hold_minus_rect):
            ui.step_hold(-1)
            return True
        if ui.hold_plus_rect[2] and _point_in_rect(pos, ui.hold_plus_rect):
            ui.step_hold(1)
            return True
        # a split system's lanes: −/+ retune that lane; anywhere else on its row
        # makes it the lane the popup (and the map highlight) is about
        for dest, row, minus, plus in ui.share_rows:
            if _point_in_rect(pos, minus) or _point_in_rect(pos, plus):
                ui.focus_lane(dest)
                ui.step_share(-1 if _point_in_rect(pos, minus) else 1)
                return True
            if _point_in_rect(pos, row):
                ui.focus_lane(dest)
                return True
        if ui.even_split_rect[2] and _point_in_rect(pos, ui.even_split_rect):
            ui.even_split()
            return True
        if ui.delete_all_rect[2] and _point_in_rect(pos, ui.delete_all_rect):
            ui.delete_all_forward()
            return True
        if ui.cancel_rect[2] and _point_in_rect(pos, ui.cancel_rect):
            ui.cancel_send()
            return True
        if ui.done_rect[2] and _point_in_rect(pos, ui.done_rect):
            _cancel(ui)   # what a tap on empty map does: the send stands, the source is let go
            return True
        # the count slider: grab it and jump to where the press landed. Must be
        # tested before the popup-background drag below, which would otherwise
        # claim the press and move the whole panel instead.
        if ui.slider_rect[2] and _point_in_rect(pos, ui.slider_rect):
            ui.dragging_slider = True
            ui.set_slider_from_x(state, pos[0])
            return True
        # a click on the popup background (none of the buttons above) grabs it to
        # drag — lets the user move it off anything it's covering
        if ui.popup_rect[2] and _point_in_rect(pos, ui.popup_rect):
            px, py = ui.popup_rect[0], ui.popup_rect[1]
            ui.dragging_popup = True
            ui.popup_drag_off = (pos[0] - px, pos[1] - py)
            ui.popup_pos = (px, py)
            return True
    return False


def _handle_left_click(state: GameState, ui: Ui, pos, shift: bool = False) -> str | None:
    handled, action = _more_press(state, ui, pos)
    if handled:
        return action
    # A docked popup (a compact screen, `render._popup_docked`) is drawn over the
    # HUD and can cover the map's zoom buttons in portrait, so it is tested
    # first; a floating one sits under them, in the map layer, and waits its turn.
    if (config.compact and ui.mode == CHOOSING and ui.popup_rect[2]
            and _point_in_rect(pos, ui.popup_rect) and not ui.autoplay and not ui.spectating):
        _popup_press(state, ui, pos)
        return None
    # Tested before the autoplay early-out so these stay clickable under autoplay
    # (mirroring the keyboard, where H/A/R/M all work regardless of autoplay).
    handled, action = _handle_global_buttons(state, ui, pos)
    if handled:
        return action
    # The popup's controls first: docked on a compact screen it can lie over End
    # Turn (`render._draw_send_popup`). Anywhere else the two never meet.
    if not ui.autoplay and not ui.spectating:
        handled = _popup_press(state, ui, pos)
        if handled:
            return None
    # Guarded on the width like every other rect test: render zeroes both of these
    # (route mode borrows the End Turn block, a running film takes it away), and a
    # zeroed rect still contains the point (0, 0).
    if ui.end_turn_rect[2] and _point_in_rect(pos, ui.end_turn_rect):
        return None if ui.awaiting_others(state) else "end_turn"
    if ui.play_pause_rect[2] and _point_in_rect(pos, ui.play_pause_rect):
        return "toggle_play"
    if ui.autoplay:
        return None
    if ui.spectating:
        # Nothing on the board is a spectator's to send, so a press on a system
        # only puts it in the side panel, as pointing at it does; on touch there is
        # no pointing. Hover rather than a selection: `Ui.source` would take a
        # neutral system for the spectator's own. Empty space clears it and pans.
        ui.hover = pick_node(state, ui, pos)
        if ui.hover is None:
            _arm_pan(ui, pos)
        return None

    # Persistent side-panel button: clear every standing forward rule at once.
    if ui.clear_forward_rect[2] and _point_in_rect(pos, ui.clear_forward_rect):
        ui.clear_all_forward()
        return None

    # Below it: clear only the rules currently pointed at a system we don't hold.
    if ui.clear_dangerous_rect[2] and _point_in_rect(pos, ui.clear_dangerous_rect):
        ui.clear_dangerous_forward(state)
        return None

    node = pick_node(state, ui, pos)
    if node is None:
        # Empty space near a lane selects the order or rule drawn there; repeat
        # clicks cycle through everything sharing the lane (else cancel).
        hit = _pick_lane(state, ui, pos)
        if hit is None:
            _cancel(ui)
            _arm_pan(ui, pos)
        elif hit[0] == "order":
            ui.edit_order(state, hit[1])
        else:
            ui.edit_forward(state, hit[1], hit[2])
        return None

    ui.sel_order = None  # selecting a system is composing, not editing an order/rule
    ui.sel_forward = None
    if ui.selected is not None and ui.mode in (SELECTED, CHOOSING):
        if node == ui.selected:
            ui.reset_selection()  # deselect the source
        elif ui.mode == CHOOSING and node == ui.dest:
            return None  # already targeting it; adjust via popup
        elif ui.source(state) is not None and state.are_adjacent(ui.selected, node):
            # commit a send-all to this neighbour and open the popup. Arm it as a
            # forward rule when Shift is held (desktop), when the source has no
            # ships to send right now — forwarding future production is then the
            # only useful action, and it's the mobile-friendly default (no Shift) —
            # or when it already forwards by a rule set up this turn.
            forward = shift or ui.opens_on_forward(state, ui.selected)
            ui.begin_send(state, node, forward=forward)
        else:
            ui.reset_selection()
            ui.selected = node
            ui.mode = SELECTED
        return None

    # IDLE
    ui.mode = SELECTED
    ui.selected = node
    return None


def _pick_lane(state: GameState, ui: Ui, pos) -> tuple[str, int, int] | None:
    """The queued order or standing-rule lane under ``pos``, tagged as
    ``("order", index, -1)`` / ``("rule", source_id, dest_id)`` — or None.

    Orders and rules can share one lane (including opposite directions), so when
    more than one is in range a repeat click cycles through them all rather than
    always grabbing the same one — this is the only way in to any of them.
    """
    hits: list[tuple[float, tuple[str, int, int]]] = []
    for i, o in enumerate(ui.pending):
        a = ui.view.to_screen(state.systems[o.source_id].pos)
        b = ui.view.to_screen(state.systems[o.dest_id].pos)
        d = point_segment_dist(pos, a, b)
        if d <= config.LANE_PICK_DIST:
            hits.append((d, ("order", i, -1)))
    for src, rule in ui.auto_forward.items():
        if not ui.rule_is_live(state, src):
            continue  # only the rules that render are pickable
        a = ui.view.to_screen(state.systems[src].pos)
        for dest in rule.shares:
            b = ui.view.to_screen(state.systems[dest].pos)
            d = point_segment_dist(pos, a, b)
            if d <= config.LANE_PICK_DIST:
                hits.append((d, ("rule", src, dest)))
    if not hits:
        return None
    order = [tag for _, tag in sorted(hits, key=lambda h: h[0])]  # nearest first, then order-before-rule
    if ui.sel_order is not None:
        current = ("order", ui.sel_order, -1)
    elif ui.mode == CHOOSING and ui.forward_armed and ui.selected is not None and ui.dest is not None:
        current = ("rule", ui.selected, ui.dest)
    else:
        current = None
    if current in order:  # cycle to the next item on this lane
        return order[(order.index(current) + 1) % len(order)]
    return order[0]


def _cancel(ui: Ui) -> None:
    if ui.mode == CHOOSING:
        source = ui.selected
        ui.close_send()  # close the popup, keeping the committed send
        # ...and finish with its source, so the next press on its neighbour selects
        # that system instead of sending it a second fleet. It stays lit as a hover,
        # and in the side panel, until the pointer moves or the next tap.
        ui.reset_selection()
        ui.hover = source
    elif ui.sel_forward is not None:
        ui.sel_forward = None  # deselect a highlighted (dormant) auto-forward rule
    else:
        ui.reset_selection()
