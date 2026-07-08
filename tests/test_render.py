"""Headless render smoke test: prove render.draw doesn't crash with no display.

Uses SDL's dummy video/audio drivers so it runs in CI with no screen. This only
checks that drawing every UI state is exception-free — not how it looks.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from starconquest import ai, config, engine, mapgen, render  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.model import Order  # noqa: E402
from starconquest.viewstate import CHOOSING, SELECTED, Ui  # noqa: E402


def _make_ui(state):
    rect = (0, config.HUD_TOP_H, config.SCREEN_W,
            config.SCREEN_H - config.HUD_TOP_H - config.HUD_BOTTOM_H)
    return Ui(view=WorldView(mapgen.map_bounds(state), rect), human_id=1)


def test_render_all_ui_states_no_crash():
    pygame.init()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)

        # plain frame
        render.draw(screen, state, ui)

        # a selection + a pending order + fleets in transit
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        ui.mode = SELECTED
        ui.selected = home
        nbr = state.systems[home].neighbors[0]
        ui.mode = CHOOSING
        ui.dest = nbr
        ui.chosen = 3
        ui.pending.append(Order(1, home, nbr, 2))
        render.draw(screen, state, ui)

        # advance a few turns so fleets exist, then draw
        for _ in range(6):
            engine.end_turn(state, decide=ai.compute_orders)
        render.draw(screen, state, ui)

        # win overlay
        state.winner = 2
        render.draw(screen, state, ui)
    finally:
        pygame.quit()
