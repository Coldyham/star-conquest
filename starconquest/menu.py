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

Tabs: **Basic** (players/systems/mode/seed/autoplay), **Advanced** (curated
global balance knobs, bound to ``Settings`` fields), and **AI** (per-seat AI
tuning with copy/reset-all shortcuts). Sliders are driven by the spec tables
below so drawing and hit-routing stay data-driven.

A footer row saves/loads the whole ``Settings`` to a named JSON file under the
gitignored ``saves/`` directory (via ``settings.Settings.save``/``load``); those
clicks perform file I/O but still return ``None`` to ``main.py``, so the scene
loop needs no new action.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import pygame

from . import config
from .model import AiParams
from .settings import Settings

# -- menu chrome colours (presentation-only, kept local like render.py's) ----- #
_PANEL_BG = (18, 20, 30)
_PANEL_BORDER = (40, 44, 60)
_TROUGH = (24, 27, 40)
_BTN_FILL = (30, 34, 48)
_BTN_BORDER = (70, 78, 100)
_HL_FILL = (40, 46, 66)
_HL_BORDER = (120, 150, 210)
_START_FILL = (46, 92, 60)
_START_BORDER = (96, 190, 120)
_DISABLED_TEXT = (78, 84, 100)
_STATUS_ERR = (214, 130, 110)

_TABS = (("basic", "Basic", True), ("advanced", "Advanced", True), ("ai", "AI", True))

_CH = 34            # control height
_ROW_H = 62         # vertical pitch between Basic-tab rows
_SLIDER_H = 42      # vertical pitch between sliders
_HEADER_H = 24      # height of a section header
_SEED_MAX_LEN = 7
_FILENAME_MAX_LEN = 24
_DEFAULT_FILENAME = "starconquest_settings"
# Saved configs live in a gitignored dir beside the repo (not the cwd), so they
# never litter the tree wherever the game is launched from.
_SAVE_DIR = Path(__file__).resolve().parent.parent / "saves"

# Slider spec: (key, label, attr, lo, hi, step, is_int). Advanced sliders set a
# Settings attribute; AI sliders set an attribute on the selected seat's AiParams.
_ADV_MAP = (
    ("adv_node_jitter", "Node jitter", "node_jitter", 0.0, 1.0, 0.05, False),
    ("adv_relax", "Relax min-sep", "relax_min_sep_frac", 0.0, 1.2, 0.05, False),
    ("adv_lloyd", "Relax passes", "lloyd_passes", 0, 5, 1, True),
    ("adv_extra_edges", "Extra edges", "extra_edge_fraction", 0.0, 1.0, 0.05, False),
    ("adv_max_edge", "Max edge len", "max_edge_length_frac", 0.1, 1.0, 0.05, False),
)
_ADV_TRAVEL = (
    ("adv_ship_speed", "Ship speed (ly/turn)", "ship_ly_per_turn", 1.0, 30.0, 0.5, False),
)
_ADV_ECON = (
    ("adv_home_ships", "Home ships", "home_start_ships", 1, 50, 1, True),
    ("adv_home_prod", "Home production", "home_production", 1, 8, 1, True),
    ("adv_garr_base", "Garrison base", "garrison_base", 0, 20, 1, True),
    ("adv_garr_k", "Garrison scale", "garrison_k", 0, 40, 1, True),
    ("adv_garr_jit", "Garrison jitter", "garrison_jitter", 0, 10, 1, True),
)
_ADV_COMBAT = (
    ("adv_combat_jitter", "Combat jitter", "combat_jitter", 0.0, 0.5, 0.02, False),
)
_AI_PARAMS = (
    ("ai_reserve_frac", "Reserve fraction", "reserve_fraction", 0.0, 0.9, 0.05, False),
    ("ai_reserve_floor", "Reserve floor", "reserve_floor", 0, 20, 1, True),
    ("ai_expand", "Expand margin", "expand_margin", 1.0, 3.0, 0.1, False),
    ("ai_attack", "Attack margin", "attack_margin", 1.0, 3.0, 0.1, False),
    ("ai_reinforce", "Reinforce margin", "reinforce_margin", 0, 10, 1, True),
)

# key -> (kind, attr, lo, hi, step, is_int); kind routes the setter target.
_SLIDER_SPECS: dict[str, tuple] = {}
for _grp in (_ADV_MAP, _ADV_TRAVEL, _ADV_ECON, _ADV_COMBAT):
    for _key, _label, _attr, _lo, _hi, _step, _is_int in _grp:
        _SLIDER_SPECS[_key] = ("adv", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in _AI_PARAMS:
    _SLIDER_SPECS[_key] = ("ai", _attr, _lo, _hi, _step, _is_int)

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
    ai_seat: int = 2                          # which seat the AI tab is editing
    drag_key: Optional[str] = None            # slider currently being dragged
    filename: str = _DEFAULT_FILENAME         # save/load target (no extension)
    editing_filename: bool = False
    status: str = ""                          # transient save/load feedback
    status_ok: bool = True
    status_until: int = 0                     # ms tick after which status hides
    rects: dict[str, pygame.Rect] = field(default_factory=dict)


def _ai_seats(settings: Settings) -> list[int]:
    """Seats the AI tab can tune: opponents 2..N, plus your seat 1 in autoplay."""
    start = 1 if settings.autoplay else 2
    return list(range(start, settings.players + 1))


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
    elif ms.tab == "advanced":
        _draw_advanced(surface, ms, settings, panel)
    elif ms.tab == "ai":
        _draw_ai(surface, ms, settings, panel)

    _file_control(surface, ms, w, 596)
    _draw_start(surface, ms, w)
    if ms.status and pygame.time.get_ticks() < ms.status_until:
        _text(surface, f["small"], ms.status,
              _START_BORDER if ms.status_ok else _STATUS_ERR, center=(w // 2, 726))
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


def _draw_advanced(surface, ms: MenuState, settings: Settings, panel: pygame.Rect) -> None:
    pad = 22
    col_w = (panel.width - pad * 3) // 2
    lx = panel.x + pad
    rx = lx + col_w + pad

    y = panel.y + 16                                   # LEFT: Map + Travel
    y = _section(surface, "Map", lx, y)
    y = _sliders(surface, ms, settings, _ADV_MAP, lx, y, col_w)
    y = _section(surface, "Travel", lx, y)
    y = _sliders(surface, ms, settings, _ADV_TRAVEL, lx, y, col_w)

    y = panel.y + 16                                   # RIGHT: Economy + Combat
    y = _section(surface, "Economy", rx, y)
    y = _sliders(surface, ms, settings, _ADV_ECON, rx, y, col_w)
    y = _section(surface, "Combat", rx, y)
    y = _sliders(surface, ms, settings, _ADV_COMBAT, rx, y, col_w)
    _text(surface, _fonts()["small"], "Neutral produces", config.COLOR_TEXT_DIM,
          midleft=(rx, y + _CH // 2))
    _checkbox(surface, ms, "neutral_produces", settings.neutral_produces, rx + col_w, y)


def _draw_ai(surface, ms: MenuState, settings: Settings, panel: pygame.Rect) -> None:
    seats = _ai_seats(settings)
    if ms.ai_seat not in seats:
        ms.ai_seat = seats[0]
    x = panel.x + 24

    # seat selector chips (coloured by player)
    y = panel.y + 18
    _text(surface, _fonts()["small"], "Seat", config.COLOR_TEXT_DIM, midleft=(x, y + _CH // 2))
    cx = x + 56
    for seat in seats:
        rect = pygame.Rect(cx, y, 44, _CH)
        base = config.player_color(seat)
        selected = seat == ms.ai_seat
        fill = base if selected else tuple(c // 2 + 8 for c in base)
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        pygame.draw.rect(surface, config.COLOR_SELECT if selected else _BTN_BORDER,
                         rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], str(seat), config.text_on(fill), center=rect.center)
        ms.rects[f"seat_{seat}"] = rect
        cx += 52

    # edit-all shortcuts, pinned near the top
    y += 46
    bw = 150
    _button(surface, ms, "copy_all", pygame.Rect(x, y, bw, _CH), "Copy to all",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    _button(surface, ms, "reset_all", pygame.Rect(x + bw + 12, y, bw, _CH), "Reset all",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    name = config.player_name(ms.ai_seat)
    _text(surface, _fonts()["small"], f"editing {name}", config.player_color(ms.ai_seat),
          midleft=(x + 2 * bw + 32, y + _CH // 2))

    # per-seat param sliders
    y += 52
    params = settings.ai[ms.ai_seat - 1]
    _sliders(surface, ms, params, _AI_PARAMS, x, y, panel.width - 48)


def _section(surface, title: str, x: int, y: int) -> int:
    _text(surface, _fonts()["small"], title.upper(), _HL_BORDER, midleft=(x, y + _HEADER_H // 2))
    return y + _HEADER_H


def _sliders(surface, ms, target, specs, x: int, y: int, width: int) -> int:
    """Draw a group of sliders reading each value off ``target``; returns next y."""
    for key, label, attr, lo, hi, step, is_int in specs:
        _slider(surface, ms, key, label, getattr(target, attr), lo, hi, is_int, x, y, width)
        y += _SLIDER_H
    return y


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


def _fmt(value, is_int: bool) -> str:
    return str(int(round(value))) if is_int else f"{value:.2f}"


def _slider(surface, ms, key, label, value, lo, hi, is_int, x, y, width) -> None:
    """Two-line slider: label + value on top, a full-width track below."""
    f = _fonts()
    _text(surface, f["small"], label, config.COLOR_TEXT_DIM, midleft=(x, y + 8))
    _text(surface, f["small"], _fmt(value, is_int), config.COLOR_TEXT, midright=(x + width, y + 8))

    cy = y + 26
    pygame.draw.rect(surface, _TROUGH, pygame.Rect(x, cy - 3, width, 6), border_radius=3)
    t = 0.0 if hi == lo else max(0.0, min(1.0, (value - lo) / (hi - lo)))
    fill_w = int(width * t)
    if fill_w > 0:
        pygame.draw.rect(surface, _HL_BORDER, pygame.Rect(x, cy - 3, fill_w, 6), border_radius=3)
    hx = x + fill_w
    pygame.draw.circle(surface, config.COLOR_TEXT, (hx, cy), 7)
    pygame.draw.circle(surface, _HL_BORDER, (hx, cy), 7, 2)
    # generous hit rect (taller than the visual track) for easy grabbing
    ms.rects[key] = pygame.Rect(x, y + 14, width, 24)


def _stepper(surface, ms, key, value: str, right: int, y: int) -> None:
    bw, vw = 34, 64
    x = right - (bw + vw + bw)
    minus = pygame.Rect(x, y, bw, _CH)
    box = pygame.Rect(x + bw, y, vw, _CH)
    plus = pygame.Rect(x + bw + vw, y, bw, _CH)
    _button(surface, ms, f"{key}_dec", minus, "−", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    pygame.draw.rect(surface, _TROUGH, box, border_radius=6)
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
    pygame.draw.rect(surface, _TROUGH, field, border_radius=6)
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


def _file_control(surface, ms: MenuState, w: int, y: int) -> None:
    """Footer row: 'File [ name ] [Save] [Load]' — mirrors the seed field."""
    f = _fonts()
    lx, rx = w // 2 - 280, w // 2 + 280
    _text(surface, f["small"], "File", config.COLOR_TEXT_DIM, midleft=(lx, y + _CH // 2))

    load = pygame.Rect(rx - 90, y, 90, _CH)
    save = pygame.Rect(load.x - 10 - 90, y, 90, _CH)
    fx = lx + 56
    field = pygame.Rect(fx, y, save.x - 10 - fx, _CH)

    editing = ms.editing_filename
    pygame.draw.rect(surface, _TROUGH, field, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if editing else _BTN_BORDER, field, 2, border_radius=6)
    shown = (ms.filename + "|") if editing else (ms.filename or _DEFAULT_FILENAME)
    _text(surface, f["normal"], shown, config.COLOR_TEXT, midleft=(field.x + 10, field.centery))
    ms.rects["filename_field"] = field

    _button(surface, ms, "save_settings", save, "Save",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    _button(surface, ms, "load_settings", load, "Load",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)


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
    pygame.draw.rect(surface, _TROUGH, box, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if on else _BTN_BORDER, box, 2, border_radius=6)
    if on:
        inner = box.inflate(-14, -14)
        pygame.draw.rect(surface, _START_BORDER, inner, border_radius=3)
    ms.rects[key] = box


def _draw_start(surface, ms: MenuState, w: int) -> None:
    rect = pygame.Rect(w // 2 - 110, 654, 220, 46)
    _button(surface, ms, "start", rect, "Start Game", fill=_START_FILL, border=_START_BORDER,
            tcol=config.COLOR_TEXT, font=_fonts()["normal"])


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def handle_event(event, ms: MenuState, settings: Settings):
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return _handle_click(event.pos, ms, settings)
    if event.type == pygame.MOUSEMOTION and ms.drag_key is not None:
        _apply_slider(ms.drag_key, ms, settings, event.pos)
        return None
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        ms.drag_key = None
        return None
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

    if ms.editing_filename:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            ms.editing_filename = False        # commit / cancel are the same here
            return None
        if event.key == pygame.K_BACKSPACE:
            ms.filename = ms.filename[:-1]
        elif (event.unicode and (event.unicode.isalnum() or event.unicode in "_-.")
              and len(ms.filename) < _FILENAME_MAX_LEN):
            ms.filename += event.unicode
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
    if hit != "filename_field":
        ms.editing_filename = False

    if hit == "start":
        return "start"
    if hit is None:
        return None
    if hit in _SLIDER_SPECS:                       # grab + jump the slider
        ms.drag_key = hit
        _apply_slider(hit, ms, settings, pos)
    elif hit.startswith("tab_"):
        ms.tab = hit[len("tab_"):]
    elif hit.startswith("seat_"):
        ms.ai_seat = int(hit[len("seat_"):])
    elif hit == "copy_all":
        src = settings.ai[ms.ai_seat - 1]
        for seat in _ai_seats(settings):
            settings.ai[seat - 1] = replace(src)
    elif hit == "reset_all":
        for seat in _ai_seats(settings):
            settings.ai[seat - 1] = AiParams()
    elif hit == "neutral_produces":
        settings.neutral_produces = not settings.neutral_produces
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
    elif hit == "filename_field":
        ms.editing_filename = True
    elif hit == "save_settings":
        path = _settings_path(ms.filename)
        try:
            _SAVE_DIR.mkdir(parents=True, exist_ok=True)
            settings.save(path)
            _set_status(ms, f"Saved {path.name}", True)
        except OSError:
            _set_status(ms, f"Couldn't save {path.name}", False)
    elif hit == "load_settings":
        path = _settings_path(ms.filename)
        try:
            settings.copy_from(Settings.load(path))
            _set_status(ms, f"Loaded {path.name}", True)
        except (OSError, ValueError):
            _set_status(ms, f"Couldn't load {path.name}", False)
    return None


def _apply_slider(key: str, ms: MenuState, settings: Settings, pos) -> None:
    kind, attr, lo, hi, step, is_int = _SLIDER_SPECS[key]
    track = ms.rects.get(key)
    if track is None:
        return
    t = 0.0 if track.w == 0 else max(0.0, min(1.0, (pos[0] - track.x) / track.w))
    raw = lo + t * (hi - lo)
    snapped = round(raw / step) * step
    snapped = max(lo, min(hi, snapped))
    value = int(round(snapped)) if is_int else round(snapped, 4)
    target = settings if kind == "adv" else settings.ai[ms.ai_seat - 1]
    setattr(target, attr, value)


def _apply_seed_text(ms: MenuState, settings: Settings) -> None:
    settings.seed = int(ms.seed_text) if ms.seed_text else None


def _settings_path(name: str) -> Path:
    """The save-file path for ``name`` (blank -> default), under ``_SAVE_DIR``.

    The field's char filter excludes path separators, so the file always stays
    inside the gitignored ``saves/`` directory.
    """
    name = name.strip() or _DEFAULT_FILENAME
    if not name.endswith(".json"):
        name += ".json"
    return _SAVE_DIR / name


def _set_status(ms: MenuState, text: str, ok: bool) -> None:
    ms.status = text
    ms.status_ok = ok
    ms.status_until = pygame.time.get_ticks() + 4000


def _set_players(settings: Settings, n: int) -> None:
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, n))
    if settings.nodes < settings.min_nodes():
        settings.nodes = settings.min_nodes()


def _set_nodes(settings: Settings, n: int) -> None:
    settings.nodes = max(settings.min_nodes(), min(config.MAX_NODES, n))
