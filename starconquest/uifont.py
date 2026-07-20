"""Shared font loading for the pygame shell (render + menu).

Prefer the bundled DejaVu Sans Mono TTF so text renders identically on every
platform — crucially on Android, which has none of the desktop monospace families
and would otherwise fall back to whatever SDL_ttf happens to find. If the bundled
file is somehow missing, fall back to a system monospace font.
"""

from __future__ import annotations

from pathlib import Path

import pygame

_ASSETS = Path(__file__).resolve().parent / "assets"
_REGULAR = _ASSETS / "DejaVuSansMono.ttf"
_BOLD = _ASSETS / "DejaVuSansMono-Bold.ttf"
_SYS_FALLBACK = "consolas,menlo,monospace"


def load(size: int, bold: bool = False) -> pygame.font.Font:
    """A monospace font at ``size`` px: the bundled TTF if present, else a system
    fallback. Uses the dedicated bold TTF for crisper weight than synthetic bold."""
    path = _BOLD if bold else _REGULAR
    if path.exists():
        return pygame.font.Font(str(path), size)
    if _REGULAR.exists():          # bold missing but regular present: synthesise
        f = pygame.font.Font(str(_REGULAR), size)
        f.set_bold(bold)
        return f
    return pygame.font.SysFont(_SYS_FALLBACK, size, bold=bold)
