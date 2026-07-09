"""The pre-game setup screen: a self-contained pygame scene.

A sibling of the game board rather than part of it, so ``render.py`` /
``input.py`` (which are about playing) stay untouched. Like the game, it keeps a
strict split *within* this module: ``draw`` only reads (never mutates
``Settings``), ``handle_event`` only mutates ``MenuState``/``Settings`` and
returns a high-level action string (``"start"``, ``"quit"``) or ``None``.

Widgets are immediate-mode: each draws itself and records its screen rect(s) in
``MenuState.rects`` under a string key; ``handle_event`` hit-tests the click
against those rects — the same store-rect-then-test pattern the End-Turn button
uses in render/input. Layout is deterministic, so the rects drawn last frame are
valid for this frame's events.

Pass 1 implements the Basic tab; the Advanced and AI tabs are drawn disabled so
the scaffold exists for later passes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pygame

from . import config
from .settings import Settings

# -- menu chrome colours (presentation-only, kept local like render.py's) ----- #
_PANEL_BG = (18, 20, 30)
_PANEL_BORDER = (40, 44, 60)
_BTN_FILL = (30, 34, 48)
_BTN_BORDER = (70, 78, 100)
_HL_FILL = (40, 46, 66)
_HL_BORDER = (120, 150, 210)
_START_FILL = (46, 92, 60)
_START_BORDER = (96, 190, 120)
_DISABLED_TEXT = (78, 84, 100)

_TABS = (("basic", "Basic", True), ("advanced", "Advanced", False), ("ai", "AI", False))

_CH = 34          # control height
_ROW_H = 62       # vertical pitch between Basic-tab rows
_SEED_MAX_LEN = 7

_FONTS: dict[str, pygame.font.Font] = {}


def _fonts() -> dict[str, pygame.font.Font]:
    if not _FONTS:
        name = "consolas,menlo,monospace"
        _FONTS["title"] = pygame.font.SysFont(name, 44, bold=True)
        _FONTS["big"] = pygame.font.SysFont(name, config.FONT_SIZE_BIG, bold=True)
        _FONTS["normal"] = pygame.font.SysFont(name, config.FONT_SIZE)
        _FONTS["small"] = pygame.font.SysFont(name, config.FONT_SIZE_SMALL)
    return _FONTS


def _text(surface, font, s, color, center=None, midleft=None, midright=None):
    img = font.render(s, True, color)
    rect = img.get_rect()
    if center:
        rect.center = center
    elif midleft:
        rect.midleft = midleft
    elif midright:
        rect.midright = midright
    surface.blit(img, rect)
    return rect


@dataclass
class MenuState:
    """Transient interaction state for the menu (analogous to viewstate.Ui)."""

    tab: str = "basic"
    editing_seed: bool = False
    seed_text: str = ""                       # edit buffer, live only while editing
    rects: dict[str, pygame.Rect] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def draw(surface: pygame.Surface, ms: MenuState, settings: Settings) -> None:
    surface.fill(config.COLOR_BG)
    ms.rects.clear()
    w, h = surface.get_size()
    f = _fonts()

    _text(surface, f["title"], "STAR CONQUEST", config.COLOR_TEXT, center=(w // 2, 92))
    _text(surface, f["small"], "configure your galaxy, then conquer it",
          config.COLOR_TEXT_DIM, center=(w // 2, 130))

    _draw_tabs(surface, ms, w)

    panel = pygame.Rect(w // 2 - 280, 208, 560, 372)
    pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=10)
    pygame.draw.rect(surface, _PANEL_BORDER, panel, 1, border_radius=10)

    if ms.tab == "basic":
        _draw_basic(surface, ms, settings, panel)

    _draw_start(surface, ms, w)

    _text(surface, f["small"], "Advanced & AI tabs coming soon", config.COLOR_TEXT_DIM,
          center=(w // 2, panel.bottom + 96))
    _text(surface, f["small"], "Enter: start game   ·   Esc: quit",
          config.COLOR_TEXT_DIM, center=(w // 2, h - 28))


def _draw_tabs(surface, ms: MenuState, w: int) -> None:
    tw, th, gap = 132, 34, 8
    total = len(_TABS) * tw + (len(_TABS) - 1) * gap
    x = w // 2 - total // 2
    y = 162
    for key, label, enabled in _TABS:
        rect = pygame.Rect(x, y, tw, th)
        if enabled and ms.tab == key:
            fill, border, tcol = _HL_FILL, _HL_BORDER, config.COLOR_TEXT
        elif enabled:
            fill, border, tcol = _BTN_FILL, _BTN_BORDER, config.COLOR_TEXT
        else:
            fill, border, tcol = (22, 24, 34), None, _DISABLED_TEXT
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        if border:
            pygame.draw.rect(surface, border, rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], label, tcol, center=rect.center)
        if enabled:
            ms.rects[f"tab_{key}"] = rect
        x += tw + gap


def _draw_basic(surface, ms: MenuState, settings: Settings, panel: pygame.Rect) -> None:
    left = panel.x + 34
    right = panel.right - 34
    y = panel.y + 34

    _row_label(surface, "Players", left, y)
    _stepper(surface, ms, "players", str(settings.players), right, y)
    y += _ROW_H

    _row_label(surface, "Systems", left, y)
    _stepper(surface, ms, "nodes", str(settings.nodes), right, y)
    y += _ROW_H

    _row_label(surface, "Map type", left, y)
    _segmented(surface, ms, right, y, [
        ("mode_random", "Random", settings.mode == "random"),
        ("mode_symmetric", "Symmetric", settings.mode == "symmetric"),
    ])
    y += _ROW_H

    _row_label(surface, "Seed", left, y)
    _seed_control(surface, ms, settings, right, y)
    y += _ROW_H

    _row_label(surface, "Autoplay", left, y)
    _checkbox(surface, ms, "autoplay", settings.autoplay, right, y)


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
def _row_label(surface, text: str, x: int, y: int) -> None:
    _text(surface, _fonts()["normal"], text, config.COLOR_TEXT, midleft=(x, y + _CH // 2))


def _button(surface, ms, key, rect, label, *, fill, border, tcol, font=None) -> None:
    pygame.draw.rect(surface, fill, rect, border_radius=6)
    if border:
        pygame.draw.rect(surface, border, rect, 2, border_radius=6)
    _text(surface, font or _fonts()["normal"], label, tcol, center=rect.center)
    if key is not None:
        ms.rects[key] = rect


def _stepper(surface, ms, key, value: str, right: int, y: int) -> None:
    bw, vw = 34, 64
    x = right - (bw + vw + bw)
    minus = pygame.Rect(x, y, bw, _CH)
    box = pygame.Rect(x + bw, y, vw, _CH)
    plus = pygame.Rect(x + bw + vw, y, bw, _CH)
    _button(surface, ms, f"{key}_dec", minus, "−", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    pygame.draw.rect(surface, (24, 27, 40), box, border_radius=6)
    _text(surface, _fonts()["normal"], value, config.COLOR_TEXT, center=box.center)
    _button(surface, ms, f"{key}_inc", plus, "+", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)


def _segmented(surface, ms, right: int, y: int, options) -> None:
    ow, gap = 118, 6
    total = len(options) * ow + (len(options) - 1) * gap
    x = right - total
    for key, label, active in options:
        rect = pygame.Rect(x, y, ow, _CH)
        if active:
            _button(surface, ms, key, rect, label, fill=_HL_FILL, border=_HL_BORDER, tcol=config.COLOR_TEXT)
        else:
            _button(surface, ms, key, rect, label, fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT_DIM)
        x += ow + gap


def _seed_control(surface, ms: MenuState, settings: Settings, right: int, y: int) -> None:
    dice = pygame.Rect(right - _CH, y, _CH, _CH)
    fw = 200
    field = pygame.Rect(dice.x - 8 - fw, y, fw, _CH)

    editing = ms.editing_seed
    pygame.draw.rect(surface, (24, 27, 40), field, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if editing else _BTN_BORDER, field, 2, border_radius=6)
    if editing:
        shown, color = ms.seed_text + "|", config.COLOR_TEXT
    elif settings.seed is None:
        shown, color = "random", config.COLOR_TEXT_DIM
    else:
        shown, color = str(settings.seed), config.COLOR_TEXT
    _text(surface, _fonts()["normal"], shown, color, midleft=(field.x + 10, field.centery))
    ms.rects["seed_field"] = field

    # A drawn die (not an emoji glyph — the monospace font has no colour emoji).
    pygame.draw.rect(surface, _BTN_FILL, dice, border_radius=6)
    pygame.draw.rect(surface, _BTN_BORDER, dice, 2, border_radius=6)
    _draw_die(surface, dice)
    ms.rects["seed_random"] = dice


def _draw_die(surface, rect: pygame.Rect) -> None:
    """A small five-pip die face, drawn to signal 'roll a random seed'."""
    face = pygame.Rect(0, 0, 20, 20)
    face.center = rect.center
    pygame.draw.rect(surface, config.COLOR_TEXT, face, border_radius=4)
    cx, cy, o, r = face.centerx, face.centery, 5, 2
    for px, py in ((cx - o, cy - o), (cx + o, cy - o), (cx, cy),
                   (cx - o, cy + o), (cx + o, cy + o)):
        pygame.draw.circle(surface, _PANEL_BG, (px, py), r)


def _checkbox(surface, ms, key, on: bool, right: int, y: int) -> None:
    box = pygame.Rect(right - _CH, y, _CH, _CH)
    pygame.draw.rect(surface, (24, 27, 40), box, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if on else _BTN_BORDER, box, 2, border_radius=6)
    if on:
        inner = box.inflate(-14, -14)
        pygame.draw.rect(surface, _START_BORDER, inner, border_radius=3)
    ms.rects[key] = box


def _draw_start(surface, ms: MenuState, w: int) -> None:
    rect = pygame.Rect(w // 2 - 110, 616, 220, 46)
    _button(surface, ms, "start", rect, "Start Game", fill=_START_FILL, border=_START_BORDER,
            tcol=config.COLOR_TEXT, font=_fonts()["normal"])


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def handle_event(event, ms: MenuState, settings: Settings):
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return _handle_click(event.pos, ms, settings)
    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ms, settings)
    return None


def _handle_key(event, ms: MenuState, settings: Settings):
    if ms.editing_seed:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            ms.editing_seed = False
            return "start"
        if event.key == pygame.K_ESCAPE:
            ms.editing_seed = False
            return None
        if event.key == pygame.K_BACKSPACE:
            ms.seed_text = ms.seed_text[:-1]
            _apply_seed_text(ms, settings)
        elif event.unicode.isdigit() and len(ms.seed_text) < _SEED_MAX_LEN:
            ms.seed_text += event.unicode
            _apply_seed_text(ms, settings)
        return None

    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
        return "start"
    if event.key == pygame.K_ESCAPE:
        return "quit"
    return None


def _handle_click(pos, ms: MenuState, settings: Settings):
    hit = next((key for key, rect in ms.rects.items() if rect.collidepoint(pos)), None)
    if hit != "seed_field":
        ms.editing_seed = False

    if hit == "start":
        return "start"
    if hit is None:
        return None
    if hit.startswith("tab_"):
        ms.tab = hit[len("tab_"):]
    elif hit == "players_dec":
        _set_players(settings, settings.players - 1)
    elif hit == "players_inc":
        _set_players(settings, settings.players + 1)
    elif hit == "nodes_dec":
        _set_nodes(settings, settings.nodes - 1)
    elif hit == "nodes_inc":
        _set_nodes(settings, settings.nodes + 1)
    elif hit == "mode_random":
        settings.mode = "random"
    elif hit == "mode_symmetric":
        settings.mode = "symmetric"
    elif hit == "seed_field":
        ms.editing_seed = True
        ms.seed_text = "" if settings.seed is None else str(settings.seed)
    elif hit == "seed_random":
        settings.seed = random.randrange(1_000_000)
        ms.seed_text = str(settings.seed)
    elif hit == "autoplay":
        settings.autoplay = not settings.autoplay
    return None


def _apply_seed_text(ms: MenuState, settings: Settings) -> None:
    settings.seed = int(ms.seed_text) if ms.seed_text else None


def _set_players(settings: Settings, n: int) -> None:
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, n))
    if settings.nodes < settings.min_nodes():
        settings.nodes = settings.min_nodes()


def _set_nodes(settings: Settings, n: int) -> None:
    settings.nodes = max(settings.min_nodes(), min(config.MAX_NODES, n))
