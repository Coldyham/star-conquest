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
    return Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)


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


def test_production_rate_sums_inverse_production():
    state = mapgen.generate_random(2, num_nodes=18, num_players=3)
    for pid in (1, 2, 3):
        expected = sum(1.0 / s.production for s in state.systems.values()
                       if s.owner_id == pid and s.production > 0)
        assert render._production_rate(state, pid) == expected
    # a lone homeworld (production 3) makes 1/3 of a ship per turn
    home = next(s for s in state.systems.values() if s.owner_id == 1)
    assert abs(1.0 / home.production - 1.0 / config.HOME_PRODUCTION) < 1e-9
