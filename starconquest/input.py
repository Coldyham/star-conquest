"""Event handling: translate pygame input into selection/targeting changes and
high-level actions. Mutates only the Ui (and queues human Orders); it never
touches the simulation directly — resolving a turn is main.py's job via the
engine. Returns an action string ('end_turn', 'restart', 'quit',
'toggle_autoplay') or None.
"""

from __future__ import annotations

from typing import Optional

import pygame

from .geometry import dist
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
    # Game over: only restart / quit.
    if state.winner is not None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                return "restart"
            if event.key == pygame.K_ESCAPE:
                return "quit"
        return None

    if event.type == pygame.MOUSEMOTION:
        ui.hover = pick_node(state, ui, event.pos)
        return None

    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ui)

    if event.type == pygame.MOUSEWHEEL and ui.mode == CHOOSING and ui.selected is not None:
        avail = ui.available(state, ui.selected)
        ui.chosen = max(1, min(avail, ui.chosen + event.y))
        return None

    if event.type == pygame.MOUSEBUTTONDOWN:
        if event.button == 1:
            return _handle_left_click(state, ui, event.pos)
        if event.button == 3:
            _cancel(ui)
    return None


def _handle_key(event, ui: Ui) -> Optional[str]:
    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        return "end_turn"
    if event.key == pygame.K_a:
        return "toggle_autoplay"
    if event.key == pygame.K_r:
        return "restart"
    if event.key == pygame.K_ESCAPE:
        if ui.mode != IDLE:
            _cancel(ui)
            return None
        return "quit"
    return None


def _handle_left_click(state: GameState, ui: Ui, pos) -> Optional[str]:
    if _point_in_rect(pos, ui.end_turn_rect):
        return "end_turn"
    if ui.autoplay:
        return None

    # Confirm a pending send: any left-click while choosing commits it.
    if ui.mode == CHOOSING and ui.selected is not None and ui.dest is not None:
        if ui.chosen > 0:
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
        _cancel(ui)
        return None

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


def _cancel(ui: Ui) -> None:
    if ui.mode == CHOOSING:
        ui.mode = SELECTED
        ui.dest = None
        ui.chosen = 0
    else:
        ui.reset_selection()
