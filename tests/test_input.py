"""Exercise the human interaction path (input.py) with synthetic pygame events.

This drives the real select -> choose -> confirm -> end-turn flow headlessly so
the interactive path is covered without a window, and confirms a queued order
actually resolves through the engine.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

import main  # noqa: E402  (repo-root entry point; pytest adds "." to sys.path)
from starconquest import config, engine, mapgen  # noqa: E402
from starconquest import input as game_input  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.viewstate import CHOOSING, IDLE, SELECTED, Ui  # noqa: E402


def _setup():
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    return state, ui


def _click(state, ui, sid, button=1):
    pos = ui.view.to_screen(state.systems[sid].pos)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    return game_input.handle_event(ev, state, ui)


def test_select_choose_confirm_flow():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        _click(state, ui, home)
        assert ui.mode == SELECTED and ui.selected == home

        _click(state, ui, nbr)
        assert ui.mode == CHOOSING and ui.dest == nbr
        assert ui.chosen == state.systems[home].ships  # defaults to all available

        # confirm with a left-click -> a queued order appears
        pos = ui.view.to_screen(state.systems[nbr].pos)
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), state, ui
        )
        assert len(ui.pending) == 1
        o = ui.pending[0]
        assert (o.owner_id, o.source_id, o.dest_id) == (1, home, nbr)

        # the queued order resolves through the engine: ships leave home as a fleet
        before = state.systems[home].ships
        engine.end_turn(state, human_orders=list(ui.pending))
        assert state.systems[home].ships < before
        assert any(f.owner_id == 1 and f.dest_id == nbr for f in state.fleets)
    finally:
        pygame.quit()


def test_cannot_select_foreign_system():
    state, ui = _setup()
    try:
        enemy = next(s.id for s in state.systems.values() if s.owner_id not in (0, 1))
        _click(state, ui, enemy)
        assert ui.mode == IDLE and ui.selected is None
    finally:
        pygame.quit()


def test_right_click_cancels():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        _click(state, ui, home)
        assert ui.mode == SELECTED
        _click(state, ui, home, button=3)  # right-click
        assert ui.mode == IDLE
    finally:
        pygame.quit()


def test_enter_key_requests_end_turn():
    state, ui = _setup()
    try:
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)
        assert game_input.handle_event(ev, state, ui) == "end_turn"
    finally:
        pygame.quit()


def test_shift_confirm_creates_forward_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        _click(state, ui, home)   # select source
        _click(state, ui, nbr)    # pick destination -> CHOOSING
        assert ui.mode == CHOOSING
        ui.chosen = 3             # keep the rest at home

        pygame.key.set_mods(pygame.KMOD_SHIFT)
        try:
            pos = ui.view.to_screen(state.systems[nbr].pos)
            game_input.handle_event(
                pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), state, ui
            )
        finally:
            pygame.key.set_mods(0)

        assert ui.auto_forward.get(home) == (nbr, state.systems[home].ships - 3)
        assert ui.pending == []   # a rule, not a one-shot send
    finally:
        pygame.quit()


def test_forward_rule_expands_into_order():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        ui.auto_forward[home] = (nbr, 4)   # keep 4, forward the surplus
        orders = main.auto_forward_orders(state, ui)
        assert len(orders) == 1
        o = orders[0]
        assert (o.owner_id, o.source_id, o.dest_id) == (1, home, nbr)
        assert o.ships == state.systems[home].ships - 4

        # keeping the whole garrison forwards nothing
        ui.auto_forward[home] = (nbr, state.systems[home].ships)
        assert main.auto_forward_orders(state, ui) == []
    finally:
        pygame.quit()


def test_x_key_clears_forward_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.selected = home
        ui.auto_forward[home] = (nbr, 2)

        game_input.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui
        )
        assert home not in ui.auto_forward
    finally:
        pygame.quit()
