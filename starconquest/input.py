"""Event handling: translate pygame input into selection/targeting changes and
high-level actions. Mutates only the Ui (and queues human Orders); it never
touches the simulation directly — resolving a turn is main.py's job via the
engine. Returns an action string ('end_turn', 'restart', 'quit',
'toggle_autoplay', 'toggle_play', 'toggle_history', 'rewind', 'menu') or None.
"""

from __future__ import annotations

from typing import Optional

import pygame

from .geometry import dist, point_segment_dist
from . import config
from .model import GameState
from .viewstate import CHOOSING, IDLE, SELECTED, Ui


def pick_node(state: GameState, ui: Ui, pos: tuple[int, int]) -> Optional[int]:
    for sid, sys in state.systems.items():
        sp = ui.view.to_screen(sys.pos)
        if dist(sp, pos) <= config.node_radius(sys.production) + 2:
            return sid
    return None


def _seek_scrubber(ui: Ui, pos) -> None:
    """Map a click/drag x within the scrubber track to a turn index (0..max)."""
    x, _y, w, _h = ui.scrubber_rect
    if w <= 0 or ui.history_max <= 0:
        ui.history_turn = 0
        return
    t = max(0.0, min(1.0, (pos[0] - x) / w))
    ui.history_turn = round(t * ui.history_max)


def _handle_history_event(event, state: GameState, ui: Ui) -> Optional[str]:
    """Modal history-review input: scrub the turn, rewind, or exit. Mutates only
    the scrub position / drag flag; enter/exit and rewind are returned to main."""
    if event.type == pygame.KEYDOWN:
        if event.key in (pygame.K_ESCAPE, pygame.K_h):
            return "toggle_history"
        if event.key == pygame.K_p:
            return "toggle_play"   # play/pause the replay at sim speed
        if event.key == pygame.K_LEFT:
            ui.history_turn = max(0, ui.history_turn - 1)
            ui.playing = False
        elif event.key == pygame.K_RIGHT:
            ui.history_turn = min(ui.history_max, ui.history_turn + 1)
            ui.playing = False
        elif event.key == pygame.K_HOME:
            ui.history_turn = 0
            ui.playing = False
        elif event.key == pygame.K_END:
            ui.history_turn = ui.history_max
            ui.playing = False
        return None
    if event.type == pygame.MOUSEMOTION:
        ui.hover = pick_node(state, ui, event.pos)  # hover still drives the detail panel
        if ui.dragging_scrubber:
            _seek_scrubber(ui, event.pos)
        return None
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        if ui.exit_history_rect[2] and _point_in_rect(event.pos, ui.exit_history_rect):
            return "toggle_history"
        if ui.play_pause_rect[2] and _point_in_rect(event.pos, ui.play_pause_rect):
            return "toggle_play"
        if ui.rewind_button_rect[2] and _point_in_rect(event.pos, ui.rewind_button_rect):
            return "rewind"
        if ui.scrubber_rect[2] and _point_in_rect(event.pos, ui.scrubber_rect):
            ui.dragging_scrubber = True
            ui.playing = False   # grabbing the scrubber pauses playback
            _seek_scrubber(ui, event.pos)
        return None
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        ui.dragging_scrubber = False
    return None


def _point_in_rect(pos, rect) -> bool:
    x, y, w, h = rect
    return x <= pos[0] <= x + w and y <= pos[1] <= y + h


def handle_event(event, state: GameState, ui: Ui) -> Optional[str]:
    # History mode is a modal review scene (entered mid-game or after a win):
    # scrub / rewind / exit only, no board interaction. Checked first so it wins
    # over the game-over branch when reviewing a finished match.
    if ui.history:
        return _handle_history_event(event, state, ui)

    # Game over: restart / back-to-menu / quit, plus entering history to review
    # the finished game behind the fog of war.
    if state.winner is not None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                return "restart"
            if event.key == pygame.K_m:
                return "menu"
            if event.key == pygame.K_h:
                return "toggle_history"
            if event.key == pygame.K_ESCAPE:
                return "quit"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if ui.history_button_rect[2] and _point_in_rect(event.pos, ui.history_button_rect):
                return "toggle_history"
        return None

    if event.type == pygame.MOUSEMOTION:
        ui.hover = pick_node(state, ui, event.pos)
        return None

    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ui)

    if event.type == pygame.MOUSEWHEEL:
        ui.step_count(state, event.y)
        return None

    if event.type == pygame.MOUSEBUTTONDOWN:
        if event.button == 1:
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            return _handle_left_click(state, ui, event.pos, shift)
        if event.button == 3:
            _cancel(ui)
    return None


def _handle_key(event, ui: Ui) -> Optional[str]:
    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        return "end_turn"
    if event.key == pygame.K_p:
        return "toggle_play"
    if event.key == pygame.K_a:
        return "toggle_autoplay"
    if event.key in (pygame.K_x, pygame.K_BACKSPACE, pygame.K_DELETE):
        if ui.mode == CHOOSING:
            ui.cancel_send()  # discard the send being adjusted in the popup
        elif ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
            del ui.pending[ui.sel_order]  # remove the highlighted queued order
            ui.sel_order = None
        elif ui.sel_forward is not None:
            ui.clear_forward(ui.sel_forward)  # drop the highlighted rule
        elif ui.selected is not None:
            ui.clear_forward(ui.selected)  # drop the selected system's forward rule
        return None
    if event.key == pygame.K_r:
        return "restart"
    if event.key == pygame.K_m:
        return "menu"
    if event.key == pygame.K_h:
        return "toggle_history"
    if event.key == pygame.K_ESCAPE:
        if ui.mode != IDLE or ui.sel_order is not None or ui.sel_forward is not None:
            _cancel(ui)
            return None
        return "quit"
    return None


def _handle_left_click(state: GameState, ui: Ui, pos, shift: bool = False) -> Optional[str]:
    # Tested before the autoplay early-out so History stays clickable under autoplay.
    if ui.history_button_rect[2] and _point_in_rect(pos, ui.history_button_rect):
        return "toggle_history"
    if _point_in_rect(pos, ui.end_turn_rect):
        return "end_turn"
    if _point_in_rect(pos, ui.play_pause_rect):
        return "toggle_play"
    if ui.autoplay:
        return None

    # Persistent side-panel button: clear every standing forward rule at once.
    if ui.clear_forward_rect[2] and _point_in_rect(pos, ui.clear_forward_rect):
        ui.clear_all_forward()
        return None

    # On-lane −/+ buttons: a scroll-wheel-free way to change the active count.
    # Tested before node-picking so a button click adjusts the send rather than
    # (re)targeting. Zero-width rects (no count being adjusted) never match.
    if ui.minus_rect[2] and _point_in_rect(pos, ui.minus_rect):
        ui.step_count(state, -1)
        return None
    if ui.plus_rect[2] and _point_in_rect(pos, ui.plus_rect):
        ui.step_count(state, 1)
        return None

    # Send popup (CHOOSING): Send/Forward tabs pick the mode, Half/All retune the
    # count, and Cancel discards the active send/forward rule.
    if ui.mode == CHOOSING and ui.selected is not None and ui.dest is not None:
        if ui.send_tab_rect[2] and _point_in_rect(pos, ui.send_tab_rect):
            ui.set_forward_mode(state, False)
            return None
        if ui.forward_tab_rect[2] and _point_in_rect(pos, ui.forward_tab_rect):
            ui.set_forward_mode(state, True)
            return None
        # the two preset buttons: Half/All (Send) or Keep-half/Keep-0 (Forward)
        if ui.send_half_rect[2] and _point_in_rect(pos, ui.send_half_rect):
            ui.keep_half(state) if ui.forward_armed else ui.send_half(state)
            return None
        if ui.send_all_rect[2] and _point_in_rect(pos, ui.send_all_rect):
            ui.keep_none(state) if ui.forward_armed else ui.send_all(state)
            return None
        if ui.cancel_rect[2] and _point_in_rect(pos, ui.cancel_rect):
            ui.cancel_send()
            return None

    # Clicks in the queued-orders panel take priority: a delete button removes
    # its order, a row selects it for editing (scroll adjusts, X removes).
    for i, (row, delete) in enumerate(ui.order_hitboxes):
        if i >= len(ui.pending):
            break
        if _point_in_rect(pos, delete):
            del ui.pending[i]
            ui.sel_order = None
            return None
        if _point_in_rect(pos, row):
            ui.select_order(i)
            return None

    # Same panel, standing auto-forward rules: a delete button clears the
    # rule, a row selects it for editing (scroll adjusts `keep`, X removes).
    for src, row, delete in ui.forward_hitboxes:
        if src not in ui.auto_forward:
            continue
        if _point_in_rect(pos, delete):
            ui.clear_forward(src)
            return None
        if _point_in_rect(pos, row):
            ui.select_forward(src)
            return None

    node = pick_node(state, ui, pos)
    if node is None:
        # Empty space near a lane selects the order or rule drawn there; repeat
        # clicks cycle through everything sharing the lane (else cancel).
        hit = _pick_lane(state, ui, pos)
        if hit is None:
            _cancel(ui)
        elif hit[0] == "order":
            ui.select_order(hit[1])
        else:
            ui.select_forward(hit[1])
        return None

    ui.sel_order = None    # selecting a system is composing, not editing an order/rule
    ui.sel_forward = None
    sys = state.systems[node]
    if ui.selected is not None and ui.mode in (SELECTED, CHOOSING):
        if node == ui.selected:
            ui.reset_selection()          # deselect the source
        elif ui.mode == CHOOSING and node == ui.dest:
            return None                   # already targeting it; adjust via popup
        elif state.are_adjacent(ui.selected, node) and (shift or ui.available(state, ui.selected) > 0):
            # commit a send-all to this neighbour and open the popup; Shift arms
            # it as a forward rule from the outset — allowed even from an empty
            # system, since a rule forwards future production
            ui.begin_send(state, node, forward=shift)
        elif sys.owner_id == ui.human_id:
            # reselect a different owned system — even with zero ships right
            # now, it may still be worth viewing.
            ui.reset_selection()
            ui.selected = node
            ui.mode = SELECTED
        else:
            ui.reset_selection()
        return None

    # IDLE
    if sys.owner_id == ui.human_id:
        ui.mode = SELECTED
        ui.selected = node
    return None


def _pick_lane(state: GameState, ui: Ui, pos) -> Optional[tuple[str, int]]:
    """The queued order or standing rule whose lane is under ``pos``, tagged as
    ``("order", index)`` / ``("rule", source_id)`` — or None.

    Orders and rules can share one lane (including opposite directions), so when
    more than one is in range a repeat click cycles through them all rather than
    always grabbing the same one — the list panel can still target any directly.
    """
    hits: list[tuple[float, tuple[str, int]]] = []
    for i, o in enumerate(ui.pending):
        a = ui.view.to_screen(state.systems[o.source_id].pos)
        b = ui.view.to_screen(state.systems[o.dest_id].pos)
        d = point_segment_dist(pos, a, b)
        if d <= config.LANE_PICK_DIST:
            hits.append((d, ("order", i)))
    for src, (dest, _keep) in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id or dest not in state.systems:
            continue  # only the rules render (and so are pickable)
        a = ui.view.to_screen(s.pos)
        b = ui.view.to_screen(state.systems[dest].pos)
        d = point_segment_dist(pos, a, b)
        if d <= config.LANE_PICK_DIST:
            hits.append((d, ("rule", src)))
    if not hits:
        return None
    order = [tag for _, tag in sorted(hits)]     # nearest first, then order-before-rule
    current = ("order", ui.sel_order) if ui.sel_order is not None else (
        ("rule", ui.sel_forward) if ui.sel_forward is not None else None)
    if current in order:                         # cycle to the next item on this lane
        return order[(order.index(current) + 1) % len(order)]
    return order[0]


def _cancel(ui: Ui) -> None:
    if ui.mode == CHOOSING:
        ui.close_send()          # close the popup, keeping the committed send
    elif ui.sel_order is not None:
        ui.sel_order = None      # deselect a highlighted queued order
    elif ui.sel_forward is not None:
        ui.sel_forward = None    # deselect a highlighted auto-forward rule
    else:
        ui.reset_selection()
