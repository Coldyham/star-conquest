#!/usr/bin/env python3
"""Render PWA install-UI screenshots into tools/pwa/ from the real game.

Chrome's richer install UI wants manifest `screenshots`: at least one
`form_factor: "wide"` (desktop) and at least one non-wide (mobile). Rather than
mock these up, we drive the actual engine + renderer headlessly so the store
listing shows a genuine mid-game board.

The committed PNGs are what the web build ships (tools/build_web.sh copies them);
re-run this only if the look changes.

    uv run python tools/pwa/make_screenshots.py
"""

from __future__ import annotations

from pathlib import Path

import pygame

import main  # reuse new_ui/refresh_fog (fog seeding); import is guarded, no side effects
from starconquest import ai, config, engine, render
from starconquest.settings import Settings, build_state

OUT = Path(__file__).resolve().parent

# (filename, width, height, turns to auto-play into a developed board). Seed and
# turn count chosen for a lively board — several owned systems, fleets in flight —
# without anyone having won yet.
# The map layout is inherently landscape, so both shots are landscape (the game
# runs fullscreen-landscape on a phone too); only the manifest form_factor differs
# — "wide" marks the desktop shot, the other is omitted so Chrome files it under
# mobile. A portrait render just leaves the lower half empty.
SHOTS = [
    ("screenshot-wide.png", 1600, 900, 16),      # desktop / form_factor: wide
    ("screenshot-mobile.png", 1280, 800, 14),    # phone / non-wide (form_factor omitted)
]
SEED = 7


def _render(w: int, h: int, turns: int) -> pygame.Surface:
    # Layout reads config.SCREEN_W/H live; match them to this surface, then scale
    # the whole UI to fit (mirrors main's boot path) and rebuild fonts, which are
    # cached per-scale so must be cleared between differently-sized shots.
    config.SCREEN_W, config.SCREEN_H = w, h
    fit = min(w / config.BASE_SCREEN_W, h / config.BASE_SCREEN_H)
    config.apply_ui_scale(max(1.0, fit))
    render._FONTS.clear()

    state = build_state(Settings(mode="random", players=4, nodes=28, seed=SEED), SEED)
    for _ in range(turns):
        if state.winner is not None:
            break
        engine.end_turn(state, decide=ai.decide)

    ui = main.new_ui(state, autoplay=False)  # seeds the human's fog at this board
    surface = pygame.Surface((w, h))
    render.draw(surface, state, ui)
    return surface


def main_() -> None:
    pygame.init()
    pygame.display.set_mode((1, 1), pygame.HIDDEN)  # font/render need a video ctx
    for name, w, h, turns in SHOTS:
        pygame.image.save(_render(w, h, turns), str(OUT / name))
        print("wrote", OUT / name, f"({w}x{h})")
    pygame.quit()


if __name__ == "__main__":
    main_()
