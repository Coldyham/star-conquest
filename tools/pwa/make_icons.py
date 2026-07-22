#!/usr/bin/env python3
"""Generate the Star Conquest PWA / home-screen icon set into tools/pwa/.

Draws a small star-map "constellation" motif — nodes joined by spacelanes with a
bright central capital star — in the game's own palette (config.py), so the
home-screen icon reads as the game rather than pygbag's default logo.

The committed PNGs are what the web build ships (tools/build_web.sh copies them);
this script only needs re-running if the artwork changes. Rendered at 4x and
smoothscaled down so the shapes get anti-aliasing pygame.draw won't give directly.

    uv run python tools/pwa/make_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

import pygame

from starconquest import config

OUT = Path(__file__).resolve().parent
SS = 4  # supersample factor

# Palette pulled from the game so the icon matches the running app.
BG_TOP = (16, 19, 32)
BG_BOTTOM = (7, 8, 15)
GLOW = (46, 78, 150)
LANE = (74, 84, 116)
HERO = config.PLAYER_COLORS[1]          # human blue
SPARK = config.COLOR_SELECT             # highlight yellow

# Constellation laid out in a unit square [0,1]; kept inside the maskable safe
# zone (central ~72%) so it survives Android's circle/squircle crop. Each node is
# (x, y, radius-as-fraction-of-size, colour).
_NODES = [
    (0.50, 0.46, 0.150, HERO),               # capital (hero)
    (0.24, 0.28, 0.058, config.PLAYER_COLORS[2]),   # red
    (0.78, 0.30, 0.062, config.PLAYER_COLORS[3]),   # green
    (0.80, 0.72, 0.055, config.PLAYER_COLORS[4]),   # orange
    (0.26, 0.74, 0.070, config.PLAYER_COLORS[5]),   # purple
    (0.50, 0.84, 0.048, config.COLOR_NEUTRAL),      # neutral
]
_LANES = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 4), (2, 3)]


def _draw(size: int) -> pygame.Surface:
    """Render the icon at `size` px (full-bleed square, opaque)."""
    n = size * SS
    surf = pygame.Surface((n, n))

    # Vertical background gradient.
    for y in range(n):
        t = y / (n - 1)
        surf.fill(
            tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)),
            (0, y, n, 1),
        )

    # Soft radial glow behind the capital.
    glow = pygame.Surface((n, n), pygame.SRCALPHA)
    cx, cy = int(_NODES[0][0] * n), int(_NODES[0][1] * n)
    for r in range(int(0.42 * n), 0, -max(1, n // 400)):
        a = int(70 * (1 - r / (0.42 * n)) ** 2)
        pygame.draw.circle(glow, (*GLOW, a), (cx, cy), r)
    surf.blit(glow, (0, 0))

    px = [(int(x * n), int(y * n)) for x, y, _, _ in _NODES]

    # Spacelanes.
    for a, b in _LANES:
        pygame.draw.line(surf, LANE, px[a], px[b], max(2, int(0.012 * n)))

    # Nodes (outer dark rim + fill), hero gets a bright ring and a sparkle.
    for i, (_, _, rad, col) in enumerate(_NODES):
        r = int(rad * n)
        pygame.draw.circle(surf, BG_BOTTOM, px[i], r + max(2, int(0.012 * n)))
        pygame.draw.circle(surf, col, px[i], r)
        if i == 0:
            pygame.draw.circle(surf, SPARK, px[i], r, max(2, int(0.010 * n)))
            _sparkle(surf, px[i], int(rad * 1.7 * n))

    return pygame.transform.smoothscale(surf, (size, size))


def _sparkle(surf: pygame.Surface, center: tuple[int, int], reach: int) -> None:
    """A crisp 4-point star highlight over the capital."""
    cx, cy = center
    w = max(2, reach // 12)
    for dx, dy in ((1, 0), (0, 1)):
        pygame.draw.polygon(
            surf,
            SPARK,
            [
                (cx + dx * reach, cy + dy * reach),
                (cx + dy * w, cy + dx * w),
                (cx - dx * reach, cy - dy * reach),
                (cx - dy * w, cy - dx * w),
            ],
        )
    pygame.draw.circle(surf, (255, 255, 255), center, max(2, reach // 8))


def main() -> None:
    pygame.init()
    pygame.display.set_mode((1, 1), pygame.HIDDEN)
    for name, size in [
        ("icon-192.png", 192),
        ("icon-512.png", 512),
        ("icon-maskable-512.png", 512),  # same art; content already in safe zone
        ("apple-touch-icon.png", 180),
        ("favicon.png", 96),
    ]:
        pygame.image.save(_draw(size), str(OUT / name))
        print("wrote", OUT / name)
    pygame.quit()


if __name__ == "__main__":
    main()
