"""Event handling: translate pygame input into selection/targeting changes and
high-level actions. Mutates only the Ui (and queues human Orders); it never
touches the simulation directly — resolving a turn is main.py's job via the
engine. Returns an action string ('end_turn', 'restart', 'quit',
'toggle_autoplay', 'menu') or None.
"""

from __future__ import annotations

from typing import Optional

import pygame

from .geometry import dist, point_segment_dist
from . import config
from .model import GameState, Order
from .viewstate import CHOOSING, IDLE, SELECTED, Ui


def pick_node(state: GameState, ui: Ui, pos: tuple[int, int]) -> Optional[int]:
    for sid, sys in state.systems.items():
        sp = ui.view.to_screen(sys.pos)
        if dist(sp, pos) <= config.node_radius(sys.production) + 2:
            return sid
    return None


def _point_in_rect(pos, rect) -> bool:
    x, y, w, h = rect
    return x <= pos[0] <= x + w and y <= pos[1] <= y + h


def handle_event(event, state: GameState, ui: Ui) -> Optional[str]:
    # Game over: only restart / back-to-menu / quit.
    if state.winner is not None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                return "restart"
            if event.key == pygame.K_m:
                return "menu"
            if event.key == pygame.K_ESCAPE:
                return "quit"
        return None

    if event.type == pygame.MOUSEMOTION:
        ui.hover = pick_node(state, ui, event.pos)
        return None

    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ui)

    if event.type == pygame.MOUSEWHEEL:
        if ui.mode == CHOOSING and ui.selected is not None:
            avail = ui.available(state, ui.selected)
            ui.chosen = max(1, min(avail, ui.chosen + event.y))
        elif ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
            # adjust a queued order in place; its cap is its own ships plus
            # whatever is still free at the source (available already nets it out)
            o = ui.pending[ui.sel_order]
            cap = ui.available(state, o.source_id) + o.ships
            o.ships = max(1, min(cap, o.ships + event.y))
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
    if event.key == pygame.K_a:
        return "toggle_autoplay"
    if event.key in (pygame.K_x, pygame.K_BACKSPACE, pygame.K_DELETE):
        if ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
            del ui.pending[ui.sel_order]  # remove the highlighted queued order
            ui.sel_order = None
        elif ui.selected is not None:
            ui.clear_forward(ui.selected)  # drop the selected system's forward rule
        return None
    if event.key == pygame.K_r:
        return "restart"
    if event.key == pygame.K_m:
        return "menu"
    if event.key == pygame.K_ESCAPE:
        if ui.mode != IDLE or ui.sel_order is not None:
            _cancel(ui)
            return None
        return "quit"
    return None


def _handle_left_click(state: GameState, ui: Ui, pos, shift: bool = False) -> Optional[str]:
    if _point_in_rect(pos, ui.end_turn_rect):
        return "end_turn"
    if ui.autoplay:
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

    # Confirm the current source->dest choice. Shift makes it a standing
    # auto-forward rule instead of a one-shot send; a plain click sends once.
    if ui.mode == CHOOSING and ui.selected is not None and ui.dest is not None:
        if shift:
            # keep the un-sent remainder at home; forward the surplus every turn
            keep = max(0, state.systems[ui.selected].ships - ui.chosen)
            ui.auto_forward[ui.selected] = (ui.dest, keep)
        elif ui.chosen > 0:
            ui.pending.append(Order(ui.human_id, ui.selected, ui.dest, ui.chosen))
        # stay on the source so more fleets can be queued from it
        remaining = ui.available(state, ui.selected)
        ui.dest, ui.chosen = None, 0
        ui.mode = SELECTED if remaining > 0 else IDLE
        if remaining <= 0:
            ui.selected = None
        return None

    node = pick_node(state, ui, pos)
    if node is None:
        # Empty space near a queued order's lane selects that order (else cancel).
        oi = _pick_pending_lane(state, ui, pos)
        if oi is not None:
            ui.select_order(oi)
        else:
            _cancel(ui)
        return None

    ui.sel_order = None  # selecting a system is composing, not editing an order
    sys = state.systems[node]
    if ui.mode == SELECTED and ui.selected is not None:
        if node == ui.selected:
            ui.reset_selection()
        elif state.are_adjacent(ui.selected, node) and ui.available(state, ui.selected) > 0:
            ui.mode = CHOOSING
            ui.dest = node
            ui.chosen = ui.available(state, ui.selected)
        elif sys.owner_id == ui.human_id and ui.available(state, node) > 0:
            ui.selected = node  # reselect a different owned system
        else:
            ui.reset_selection()
        return None

    # IDLE
    if sys.owner_id == ui.human_id and ui.available(state, node) > 0:
        ui.mode = SELECTED
        ui.selected = node
    return None


def _pick_pending_lane(state: GameState, ui: Ui, pos) -> Optional[int]:
    """Index of a queued order whose lane is under ``pos``, or None.

    Several orders can share one lane (including opposite directions), so when
    more than one is in range a repeat click cycles through them rather than
    always grabbing the same one — the list panel can still target any directly.
    """
    hits = []
    for i, o in enumerate(ui.pending):
        a = ui.view.to_screen(state.systems[o.source_id].pos)
        b = ui.view.to_screen(state.systems[o.dest_id].pos)
        d = point_segment_dist(pos, a, b)
        if d <= config.LANE_PICK_DIST:
            hits.append((d, i))
    if not hits:
        return None
    order = [i for _, i in sorted(hits)]     # nearest first, then by index
    if ui.sel_order in order:                # cycle to the next order on this lane
        return order[(order.index(ui.sel_order) + 1) % len(order)]
    return order[0]


def _cancel(ui: Ui) -> None:
    if ui.sel_order is not None:
        ui.sel_order = None      # deselect a highlighted queued order
    elif ui.mode == CHOOSING:
        ui.mode = SELECTED
        ui.dest = None
        ui.chosen = 0
    else:
        ui.reset_selection()
