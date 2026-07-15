"""Headless render smoke test: prove render.draw doesn't crash with no display.

Uses SDL's dummy video/audio drivers so it runs in CI with no screen. This only
checks that drawing every UI state is exception-free — not how it looks.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from starconquest import ai, config, engine, fog, mapgen, render  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.model import Order  # noqa: E402
from starconquest.viewstate import CHOOSING, SELECTED, Ui  # noqa: E402


def _make_ui(state):
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    # mirror main.new_ui: with fog off (the default) the whole map is visible
    ui.visible = set(state.systems)
    ui.seen = set(state.systems)
    return ui


def test_render_all_ui_states_no_crash():
    pygame.init()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)

        # plain frame
        render.draw(screen, state, ui)

        # a selection + a pending order + a choosing preview + a standing rule
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        ui.mode = SELECTED
        ui.selected = home
        nbr = state.systems[home].neighbors[0]
        ui.mode = CHOOSING
        ui.dest = nbr
        ui.chosen = 3
        ui.hover = nbr
        ui.pending.append(Order(1, home, nbr, 2))
        ui.auto_forward[home] = (nbr, 2)   # exercise dashed rule arrow + panel rule section
        render.draw(screen, state, ui)

        # editing the standing rule: the on-map −/+ stepper is drawn, recording
        # clickable button rects just like a queued order's
        ui.mode = SELECTED
        ui.dest = None
        ui.sel_forward = home
        render.draw(screen, state, ui)
        assert ui.minus_rect[2] > 0 and ui.plus_rect[2] > 0
        ui.sel_forward = None

        # advance a few turns so fleets exist, then draw
        for _ in range(6):
            engine.end_turn(state, decide=ai.compute_orders)
        render.draw(screen, state, ui)

        # win overlay
        state.winner = 2
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_scoreboard_full_table_and_eliminated():
    """Six seats (drops names) plus an eliminated player exercise both label paths."""
    pygame.init()
    render._FONTS.clear()   # rebuild fonts under this session (prev test quit pygame)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=34, num_players=6)
        state.players[3].alive = False   # force the "out" branch
        render.draw(screen, state, _make_ui(state))
    finally:
        pygame.quit()


def test_render_fogged_states_no_crash():
    """Fogged silhouettes, hidden systems, a "?" panel, and a frozen scoreboard row
    all draw without a crash."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(3, num_nodes=18, num_players=3)
        ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
        ui.visible = {s.id for s in state.systems.values() if s.owner_id == 1}
        ui.seen = set(sorted(state.systems)[:10])         # some fogged, the rest hidden
        ui.player_intel = {2: fog.player_totals(state, 2)}  # a frozen (last-known) row
        ui.hover = next(iter(ui.seen - ui.visible), None)   # hover a fogged system
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_production_rate_sums_inverse_production():
    state = mapgen.generate_random(2, num_nodes=18, num_players=3)
    for pid in (1, 2, 3):
        expected = sum(1.0 / s.production for s in state.systems.values()
                       if s.owner_id == pid and s.production > 0)
        assert fog.player_totals(state, pid)[2] == expected
    # a lone homeworld (production 3) makes 1/3 of a ship per turn
    home = next(s for s in state.systems.values() if s.owner_id == 1)
    assert abs(1.0 / home.production - 1.0 / config.HOME_PRODUCTION) < 1e-9
