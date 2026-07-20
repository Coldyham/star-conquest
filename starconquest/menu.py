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
tuning with copy/reset-all shortcuts, plus a **Strategy** dropdown listing the
built-in heuristic and any drop-in ``models/`` files, via ``ai.load_models``).
Both slider tabs also carry a die button that rolls their sliders to random
in-bounds values, for fun — the same roll-the-dice metaphor as the seed control.
Sliders are driven by the spec tables below so drawing and hit-routing stay
data-driven.

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

from . import ai, config, uifont
from .model import AiParams
from .paths import data_dir
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
# Saved configs live in a gitignored dir under the writable data dir (repo root
# on desktop, the app-private dir on Android), not the cwd, so they never litter
# the tree wherever the game is launched from.
_SAVE_DIR = data_dir() / "saves"

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
# Fog of war (human view). Sight bottoms out at 0 (only your own systems in full
# detail); scout floors at 1 so immediate neighbours stay visible enough to target
# (you couldn't expand otherwise). At FOG_MAX_HOPS a range means "unlimited" (off).
_ADV_FOG = (
    ("adv_fog_sight", "Sight range", "fog_sight", 0, config.FOG_MAX_HOPS, 1, True),
    ("adv_fog_scout", "Scout range", "fog_scout", 1, config.FOG_MAX_HOPS, 1, True),
)
# Every Advanced-tab slider, flattened — the "Randomise all" die walks these.
_ADV_ALL = _ADV_MAP + _ADV_TRAVEL + _ADV_ECON + _ADV_COMBAT + _ADV_FOG
_AI_PARAMS = (
    ("ai_reserve_frac", "Reserve fraction", "reserve_fraction", 0.0, 0.9, 0.05, False),
    ("ai_reserve_floor", "Reserve floor", "reserve_floor", 0, 20, 1, True),
    ("ai_expand", "Expand margin", "expand_margin", 1.0, 3.0, 0.1, False),
    ("ai_attack", "Attack margin", "attack_margin", 1.0, 3.0, 0.1, False),
    ("ai_reinforce", "Reinforce margin", "reinforce_margin", 0, 10, 1, True),
)

# key -> (kind, attr, lo, hi, step, is_int); kind routes the setter target.
_SLIDER_SPECS: dict[str, tuple] = {}
for _grp in (_ADV_MAP, _ADV_TRAVEL, _ADV_ECON, _ADV_COMBAT, _ADV_FOG):
    for _key, _label, _attr, _lo, _hi, _step, _is_int in _grp:
        _SLIDER_SPECS[_key] = ("adv", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in _AI_PARAMS:
    _SLIDER_SPECS[_key] = ("ai", _attr, _lo, _hi, _step, _is_int)

_FONTS: dict[str, pygame.font.Font] = {}
_MODAL_FONTS: dict[str, object] = {}


def _fonts() -> dict[str, pygame.font.Font]:
    # Baseline (design-resolution) sizes: the menu is laid out on a fixed
    # BASE_SCREEN canvas that `draw` then scales to the real screen, so these must
    # NOT use the DPI-scaled config.FONT_SIZE* (that would scale twice).
    if not _FONTS:
        _FONTS["title"] = uifont.load(44, bold=True)
        _FONTS["big"] = uifont.load(30, bold=True)
        _FONTS["normal"] = uifont.load(18)
        _FONTS["small"] = uifont.load(14)
    return _FONTS


def _modal_fonts() -> dict[str, pygame.font.Font]:
    """Fonts for the real-screen boot modal (resume prompt), which is drawn onto
    the actual surface rather than the scaled canvas — so these DO use the
    DPI-scaled sizes to stay legible on a phone. Rebuilt if the scale changes."""
    if _MODAL_FONTS.get("_scale") != config.ui_scale:
        _MODAL_FONTS.clear()
        _MODAL_FONTS["big"] = uifont.load(config.FONT_SIZE_BIG, bold=True)
        _MODAL_FONTS["normal"] = uifont.load(config.FONT_SIZE)
        _MODAL_FONTS["small"] = uifont.load(config.FONT_SIZE_SMALL)
        _MODAL_FONTS["_scale"] = config.ui_scale
    return _MODAL_FONTS  # type: ignore[return-value]


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
    strategy_open: bool = False               # is the AI-seat strategy dropdown open
    strategies: list[str] = field(            # dropdown options, refreshed on open
        default_factory=lambda: ["heuristic"]
    )
    rects: dict[str, pygame.Rect] = field(default_factory=dict)
    # Transform from real-screen coords to the fixed menu canvas, set by draw() and
    # inverted by handle_event so clicks land on the widget rects (in canvas space).
    canvas_scale: float = 1.0
    canvas_offset: tuple[int, int] = (0, 0)


def _ai_seats(settings: Settings) -> list[int]:
    """Seats the AI tab can tune: opponents 2..N, plus your seat 1 in autoplay."""
    start = 1 if settings.autoplay else 2
    return list(range(start, settings.players + 1))


def _fog_off(settings: Settings) -> bool:
    """True when both fog ranges sit at max — full visibility. The Basic-tab
    "Fog of war" checkbox is the inverse of this, derived live from the sliders so
    editing them on the Advanced tab flips the checkbox automatically."""
    return (settings.fog_sight >= config.FOG_MAX_HOPS
            and settings.fog_scout >= config.FOG_MAX_HOPS)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
_CANVAS: Optional[pygame.Surface] = None


def _get_canvas() -> pygame.Surface:
    """The fixed-size surface the menu is always laid out on (design resolution)."""
    global _CANVAS
    size = (config.BASE_SCREEN_W, config.BASE_SCREEN_H)
    if _CANVAS is None or _CANVAS.get_size() != size:
        _CANVAS = pygame.Surface(size)
    return _CANVAS


def draw(surface: pygame.Surface, ms: MenuState, settings: Settings) -> None:
    """Render the menu on a fixed design-resolution canvas, then scale it to fit
    the real surface (letterboxed, aspect-preserved). One transform keeps the whole
    menu on-screen and correctly proportioned at any resolution/DPI without
    per-widget scaling; `handle_event` inverts it so clicks hit the right widgets."""
    canvas = _get_canvas()
    _draw_menu(canvas, ms, settings)
    sw, sh = surface.get_size()
    cw, ch = canvas.get_size()
    scale = min(sw / cw, sh / ch)
    dw, dh = round(cw * scale), round(ch * scale)
    ox, oy = (sw - dw) // 2, (sh - dh) // 2
    ms.canvas_scale, ms.canvas_offset = scale, (ox, oy)
    if (dw, dh) == (cw, ch):
        surface.blit(canvas, (ox, oy))
    else:
        surface.fill(config.COLOR_BG)   # letterbox bars
        surface.blit(pygame.transform.smoothscale(canvas, (dw, dh)), (ox, oy))


def _draw_menu(surface: pygame.Surface, ms: MenuState, settings: Settings) -> None:
    surface.fill(config.COLOR_BG)
    ms.rects.clear()
    w, h = surface.get_size()
    f = _fonts()

    _text(surface, f["title"], "STAR CONQUEST", config.COLOR_TEXT, center=(w // 2, 92))
    _text(surface, f["small"], "configure your galaxy, then conquer it",
          config.COLOR_TEXT_DIM, center=(w // 2, 130))

    _draw_tabs(surface, ms, w)

    panel = pygame.Rect(w // 2 - 280, 208, 560, 496)
    pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=10)
    pygame.draw.rect(surface, _PANEL_BORDER, panel, 1, border_radius=10)

    if ms.tab == "basic":
        _draw_basic(surface, ms, settings, panel)
    elif ms.tab == "advanced":
        _draw_advanced(surface, ms, settings, panel)
    elif ms.tab == "ai":
        _draw_ai(surface, ms, settings, panel)

    _file_control(surface, ms, w, 720)
    _draw_start(surface, ms, w)
    if ms.status and pygame.time.get_ticks() < ms.status_until:
        _text(surface, f["small"], ms.status,
              _START_BORDER if ms.status_ok else _STATUS_ERR, center=(w // 2, 850))
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
    y += _ROW_H

    # Fog of war: a one-click on/off here; the Advanced tab has the fine ranges.
    # State is derived from the sliders, so it tracks Advanced edits automatically.
    _row_label(surface, "Fog of war", left, y)
    _checkbox(surface, ms, "fog_of_war", not _fog_off(settings), right, y)


def _draw_advanced(surface, ms: MenuState, settings: Settings, panel: pygame.Rect) -> None:
    pad = 22
    col_w = (panel.width - pad * 3) // 2
    lx = panel.x + pad
    rx = lx + col_w + pad

    y = panel.y + 16                                   # LEFT: Map + Travel + Visibility
    y = _section(surface, "Map", lx, y)
    y = _sliders(surface, ms, settings, _ADV_MAP, lx, y, col_w)
    y = _section(surface, "Travel", lx, y)
    y = _sliders(surface, ms, settings, _ADV_TRAVEL, lx, y, col_w)
    y = _section(surface, "Visibility", lx, y)
    y = _sliders(surface, ms, settings, _ADV_FOG, lx, y, col_w)
    _text(surface, _fonts()["small"], "Randomise all", config.COLOR_TEXT_DIM,
          midleft=(lx, y + _CH // 2))
    _die_button(surface, ms, "randomise_adv", pygame.Rect(lx + col_w - _CH, y, _CH, _CH))

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
    y += 44
    bw = 150
    _button(surface, ms, "copy_all", pygame.Rect(x, y, bw, _CH), "Copy to all",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    _button(surface, ms, "reset_all", pygame.Rect(x + bw + 12, y, bw, _CH), "Reset all",
            fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    dice = pygame.Rect(x + 2 * (bw + 12), y, _CH, _CH)
    _die_button(surface, ms, "randomise_ai", dice)
    name = config.player_name(ms.ai_seat)
    _text(surface, _fonts()["small"], f"editing {name}", config.player_color(ms.ai_seat),
          midleft=(dice.right + 12, y + _CH // 2))

    # strategy dropdown (built-in heuristic + any drop-in models/)
    y += 44
    _text(surface, _fonts()["small"], "Strategy", config.COLOR_TEXT_DIM, midleft=(x, y + _CH // 2))
    _dropdown(surface, ms, "strategy", settings.seat_strategy(ms.ai_seat),
              ms.strategies, ms.strategy_open, x + 100, y, panel.width - 48 - 100)

    # per-seat param sliders (hidden while the dropdown is open so its options,
    # which overlay this region, own the hit-test — no slider rect underneath)
    y += 46
    if not ms.strategy_open:
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


def _die_button(surface, ms, key, rect: pygame.Rect) -> None:
    """A button whose face is a die — the 'roll these sliders to random' action,
    reusing the seed control's roll-the-dice look."""
    pygame.draw.rect(surface, _BTN_FILL, rect, border_radius=6)
    pygame.draw.rect(surface, _BTN_BORDER, rect, 2, border_radius=6)
    _draw_die(surface, rect)
    ms.rects[key] = rect


def _fmt(value, is_int: bool) -> str:
    return str(int(round(value))) if is_int else f"{value:.2f}"


def _slider(surface, ms, key, label, value, lo, hi, is_int, x, y, width) -> None:
    """Two-line slider: label + value on top, a full-width track below."""
    f = _fonts()
    _text(surface, f["small"], label, config.COLOR_TEXT_DIM, midleft=(x, y + 8))
    # a fog range at its max means "unlimited" — read it as "All", not a bare number
    vtext = "All" if key.startswith("adv_fog_") and value >= hi else _fmt(value, is_int)
    _text(surface, f["small"], vtext, config.COLOR_TEXT, midright=(x + width, y + 8))

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


def _dropdown(surface, ms: MenuState, key, current, options, open_, x, y, width) -> None:
    """A closed trigger showing ``current``; when ``open_``, a list of options.

    Trigger records ``ms.rects[key]``; each open option records
    ``ms.rects[f"{key}_opt_{i}"]`` (store-rect-then-test, like every widget here).
    """
    f = _fonts()
    trigger = pygame.Rect(x, y, width, _CH)
    pygame.draw.rect(surface, _BTN_FILL, trigger, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if open_ else _BTN_BORDER, trigger, 2, border_radius=6)
    _text(surface, f["normal"], current, config.COLOR_TEXT, midleft=(trigger.x + 10, trigger.centery))
    _draw_caret(surface, pygame.Rect(trigger.right - _CH, y, _CH, _CH), open_)
    ms.rects[key] = trigger
    if not open_:
        return

    rh = 30
    panel = pygame.Rect(x, y + _CH + 2, width, rh * len(options))
    pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER, panel, 1, border_radius=6)
    for i, opt in enumerate(options):
        row = pygame.Rect(x, panel.y + i * rh, width, rh)
        if opt == current:
            pygame.draw.rect(surface, _HL_FILL, row.inflate(-4, -4), border_radius=4)
        _text(surface, f["small"], opt, config.COLOR_TEXT, midleft=(row.x + 12, row.centery))
        ms.rects[f"{key}_opt_{i}"] = row


def _draw_caret(surface, rect: pygame.Rect, up: bool) -> None:
    """A small ▲/▼ triangle (the mono font has no arrow glyph)."""
    cx, cy = rect.center
    s = 5
    if up:
        pts = [(cx - s, cy + 3), (cx + s, cy + 3), (cx, cy - 4)]
    else:
        pts = [(cx - s, cy - 3), (cx + s, cy - 3), (cx, cy + 4)]
    pygame.draw.polygon(surface, config.COLOR_TEXT_DIM, pts)


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
    rect = pygame.Rect(w // 2 - 110, 780, 220, 46)
    _button(surface, ms, "start", rect, "Start Game", fill=_START_FILL, border=_START_BORDER,
            tcol=config.COLOR_TEXT, font=_fonts()["normal"])


# --------------------------------------------------------------------------- #
# Resume prompt — a boot-time modal offered when the last game was left unfinished
# --------------------------------------------------------------------------- #
def resume_prompt_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(resume, new-game) button rects — shared by the drawer and the hit-tester.
    Drawn on the real surface (not the canvas), so sizes scale via config.s."""
    w, h = surface.get_size()
    cx, cy = w // 2, h // 2
    bw, bh, gap = config.s(200), config.s(42), config.s(12)
    resume_r = pygame.Rect(cx - bw - gap, cy + config.s(24), bw, bh)
    new_r = pygame.Rect(cx + gap, cy + config.s(24), bw, bh)
    return resume_r, new_r


def draw_resume_prompt(surface, log) -> None:
    """Modal veil offering to resume ``log`` (an in-progress match), over the menu."""
    w, h = surface.get_size()
    f = _modal_fonts()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    _text(surface, f["big"], "Resume last game?", config.COLOR_TEXT,
          center=(w // 2, h // 2 - config.s(52)))
    st = log.settings
    detail = (f"turn {log.turn_count} · {st.get('players', '?')} players · "
              f"{st.get('mode', 'random')} map")
    _text(surface, f["small"], detail, config.COLOR_TEXT_DIM,
          center=(w // 2, h // 2 - config.s(18)))

    resume_r, new_r = resume_prompt_buttons(surface)
    for rect, label, fill, edge in (
        (resume_r, "Resume (Y/Enter)", _START_FILL, _START_BORDER),
        (new_r, "New game (N/Esc)", _BTN_FILL, _BTN_BORDER),
    ):
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        pygame.draw.rect(surface, edge, rect, 2, border_radius=6)
        _text(surface, f["normal"], label, config.COLOR_TEXT, center=rect.center)


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def handle_event(event, ms: MenuState, settings: Settings):
    # Map pointer coords from the real screen back into canvas space (the widget
    # rects live in canvas space), then dispatch. Also toggle the Android soft
    # keyboard as a text field gains/loses focus (a harmless SDL no-op on desktop).
    event = _to_canvas_event(event, ms)
    was_editing = ms.editing_seed or ms.editing_filename
    result = _dispatch(event, ms, settings)
    _sync_text_input(ms, was_editing)
    return result


def _to_canvas_event(event, ms: MenuState):
    """Return ``event`` with any pointer position mapped from screen to canvas
    space (inverse of draw()'s fit transform). Non-pointer events pass through."""
    if event.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                          pygame.MOUSEMOTION):
        return event
    ox, oy = ms.canvas_offset
    scale = ms.canvas_scale or 1.0
    x, y = event.pos
    data = dict(event.dict)
    data["pos"] = (round((x - ox) / scale), round((y - oy) / scale))
    return pygame.event.Event(event.type, data)


def _dispatch(event, ms: MenuState, settings: Settings):
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return _handle_click(event.pos, ms, settings)
    if event.type == pygame.MOUSEMOTION and ms.drag_key is not None:
        _apply_slider(ms.drag_key, ms, settings, event.pos)
        return None
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        ms.drag_key = None
        return None
    if event.type == pygame.TEXTINPUT:      # characters (soft keyboard or physical)
        return _handle_text_input(event.text, ms, settings)
    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ms, settings)
    return None


def _sync_text_input(ms: MenuState, was_editing: bool) -> None:
    """Raise/dismiss the on-screen keyboard as a text field gains/loses focus.
    On Android SDL shows the IME; on desktop it just toggles TEXTINPUT delivery.
    Guarded so a headless/uninitialised video subsystem never raises."""
    now_editing = ms.editing_seed or ms.editing_filename
    if now_editing == was_editing:
        return
    try:
        pygame.key.start_text_input() if now_editing else pygame.key.stop_text_input()
    except pygame.error:
        pass


def _handle_text_input(text: str, ms: MenuState, settings: Settings):
    """Character entry from the OS — physical keys and the Android soft keyboard
    both arrive here as TEXTINPUT. Control keys stay in ``_handle_key``."""
    if ms.editing_seed:
        for ch in text:
            if ch.isdigit() and len(ms.seed_text) < _SEED_MAX_LEN:
                ms.seed_text += ch
        _apply_seed_text(ms, settings)
    elif ms.editing_filename:
        for ch in text:
            if (ch.isalnum() or ch in "_-.") and len(ms.filename) < _FILENAME_MAX_LEN:
                ms.filename += ch
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
        return None

    if ms.editing_filename:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            ms.editing_filename = False        # commit / cancel are the same here
            return None
        if event.key == pygame.K_BACKSPACE:
            ms.filename = ms.filename[:-1]
        return None

    if ms.strategy_open:
        if event.key == pygame.K_ESCAPE:
            ms.strategy_open = False
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
    if not (hit == "strategy" or (hit or "").startswith("strategy_opt_")):
        ms.strategy_open = False           # click anywhere else closes the dropdown

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
    elif hit == "strategy":                        # toggle the dropdown, rescanning models/
        if not ms.strategy_open:
            ai.load_models()
            ms.strategies = ai.available_strategies()
        ms.strategy_open = not ms.strategy_open
    elif hit.startswith("strategy_opt_"):
        idx = int(hit[len("strategy_opt_"):])
        if 0 <= idx < len(ms.strategies):
            settings.ai_strategy[ms.ai_seat - 1] = ms.strategies[idx]
        ms.strategy_open = False
    elif hit == "copy_all":
        src = settings.ai[ms.ai_seat - 1]
        src_strat = settings.ai_strategy[ms.ai_seat - 1]
        for seat in _ai_seats(settings):
            settings.ai[seat - 1] = replace(src)
            settings.ai_strategy[seat - 1] = src_strat
    elif hit == "reset_all":
        for seat in _ai_seats(settings):
            settings.ai[seat - 1] = AiParams()
            settings.ai_strategy[seat - 1] = "heuristic"
    elif hit == "randomise_ai":
        _randomise_sliders(settings.ai[ms.ai_seat - 1], _AI_PARAMS)
    elif hit == "randomise_adv":
        _randomise_sliders(settings, _ADV_ALL)
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
    elif hit == "fog_of_war":
        if _fog_off(settings):                     # off -> on: apply the fog preset
            settings.fog_sight = config.FOG_ON_SIGHT
            settings.fog_scout = config.FOG_ON_SCOUT
        else:                                      # on -> off: full visibility
            settings.fog_sight = settings.fog_scout = config.FOG_MAX_HOPS
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


def _randomise_sliders(target, specs) -> None:
    """Scramble every slider in ``specs`` to a random in-bounds, step-snapped value
    on ``target`` — the Advanced/AI 'roll' buttons, just for fun. Shares the
    snap-and-clamp logic with ``_apply_slider``."""
    for _key, _label, attr, lo, hi, step, is_int in specs:
        snapped = round(random.uniform(lo, hi) / step) * step
        snapped = max(lo, min(hi, snapped))
        setattr(target, attr, int(round(snapped)) if is_int else round(snapped, 4))


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
