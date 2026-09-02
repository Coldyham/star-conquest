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

Tabs: **Basic** (players/systems/mode/seed/autoplay), **Combat** (the square law
explained, with a live demo fight and the two combat knobs beside it),
**Advanced** (curated global balance knobs, bound to ``Settings`` fields), and
**AI** (per-seat AI tuning with copy/reset-all shortcuts, plus a **Strategy**
dropdown listing the built-in heuristic and any drop-in ``models/`` files, via
``ai.load_models``; the bot-defined ``aux`` knob is labelled by the selected
strategy, or absent if it declares no meaning for it — see ``ai.aux_spec``).
The Advanced and AI tabs each carry a die button that rolls their sliders to
random in-bounds values, for fun — the same roll-the-dice metaphor as the seed
control. Sliders are driven by the spec tables below so drawing and hit-routing
stay data-driven; each spec's ``kind`` says which object it writes
(``Settings``, a seat's ``AiParams``, or — for the Combat demo alone —
``MenuState``).

A footer row saves/loads the whole ``Settings`` to a named JSON file under the
gitignored ``saves/`` directory (via ``settings.Settings.save``/``load``); those
clicks perform file I/O but still return ``None`` to ``main.py``, so the scene
loop needs no new action.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import pygame

from . import ai, combat, config, softkeyboard, uifont, webstore
from .model import AiParams
from .paths import is_web, saves_dir
from .settings import Settings, fresh_rng, random_seed

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
_WARN = (214, 172, 92)  # amber: a challenge whose settings no longer match

# Event types that can change a Settings — the ones worth snapshotting before.
# MOUSEMOTION is excluded deliberately: it only mutates mid-slider-drag, which the
# opening click already snapshotted, and it fires far too often to copy a config on.
_MUTATING_EVENTS = (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.TEXTINPUT, pygame.KEYDOWN)

_TABS = (
    ("basic", "Basic", True),
    ("combat", "Combat", True),
    ("advanced", "Advanced", True),
    ("ai", "AI", True),
)

_CH = 34  # control height
_ROW_H = 62  # vertical pitch between Basic-tab rows
_SLIDER_H = 42  # vertical pitch between sliders
_HEADER_H = 24  # height of a section header
_PROSE_H = 22  # pitch between wrapped `normal` prose lines (Combat tab)
_NOTE_H = 18  # ...and between `small` note / table rows
_MATRIX_N = 3  # jitter matrix: swings per axis, spanning ∓jitter; keep it odd
_MATRIX_ROW_H = 24  # ...its row pitch, and
_TLABEL_W = 120  # ...the gutter its row labels sit in
_SEED_MAX_LEN = 7
_FILENAME_MAX_LEN = 24
_DEFAULT_FILENAME = "starconquest_settings"
# Saved configs live in a gitignored dir under the writable data dir (repo root
# on desktop, the app-private dir on Android), not the cwd, so they never litter
# the tree wherever the game is launched from.
_SAVE_DIR = saves_dir()

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
    ("adv_ship_speed", "Ship speed (ly/turn)", "ship_ly_per_turn", 1.0, config.SHIP_SPEED_MAX, 0.5, False),
    ("adv_speed_growth", "Speed gain %/turn", "ship_speed_growth_pct", 0.0, 2.0, 0.05, False),
)
_ADV_ECON = (
    ("adv_home_ships", "Home ships", "home_start_ships", 1, 50, 1, True),
    ("adv_home_prod", "Home production", "home_production", 1, 8, 1, True),
    ("adv_garr_base", "Garrison base", "garrison_base", 0, 20, 1, True),
    ("adv_garr_k", "Garrison scale", "garrison_k", 0, 40, 1, True),
    ("adv_garr_jit", "Garrison jitter", "garrison_jitter", 0, 10, 1, True),
)
# Drawn on the Combat tab (beside the demo they govern), not on Advanced — but
# still `adv_`-keyed and still writing `Settings`, since the prefix tracks the
# namespace written to, not the tab drawn on.
_ADV_COMBAT = (
    ("adv_combat_jitter", "Combat jitter", "combat_jitter", 0.0, 0.5, 0.02, False),
    # Topped out at 1.5, not 2.0: past there the knob stops being a balance
    # setting and becomes a stalemate. Both sides produce symmetrically, so a
    # fortress bonus that large grows the defence as fast as any assault can be
    # massed — at 2.0 only 3 of 40 sim games ever finish, and the rest do not
    # resolve at a 3000-turn cap either. See design notes for the measurements.
    ("adv_defender_adv", "Defender advantage", "defender_advantage", 0.75, config.DEFENDER_ADVANTAGE_MAX, 0.05, False),
)
# The Combat tab's demo. The one slider group that does *not* touch `Settings`:
# these are transient view state on `MenuState`, so playing with them never lands
# in a save file or a share token, and never trips the un-challenge confirm modal.
# Hence the third `kind` in `_SLIDER_SPECS`. `attr` deliberately matches the
# MenuState field name, as it does for the other two kinds.
_PREVIEW = (
    ("preview_attacker", "Attacker ships", "preview_attacker", 1, config.COMBAT_PREVIEW_MAX, 1, True),
    ("preview_defender", "Defender ships", "preview_defender", 1, config.COMBAT_PREVIEW_MAX, 1, True),
)
# Fog of war (human view). Sight bottoms out at 0 (only your own systems in full
# detail); scout floors at 1 so immediate neighbours stay visible enough to target
# (you couldn't expand otherwise). At FOG_MAX_HOPS a range means "unlimited" (off).
_ADV_FOG = (
    ("adv_fog_sight", "Sight range", "fog_sight", 0, config.FOG_MAX_HOPS, 1, True),
    ("adv_fog_scout", "Scout range", "fog_scout", 1, config.FOG_MAX_HOPS, 1, True),
)
# Every Advanced-tab slider, flattened — the "Randomise all" die walks these.
# `_ADV_COMBAT` is deliberately absent: it lives on the Combat tab now, and a die
# should only roll what you can see (silently changing an off-screen knob would
# also raise the un-challenge modal for an edit you can't point at).
_ADV_ALL = _ADV_MAP + _ADV_TRAVEL + _ADV_ECON + _ADV_FOG
# The five knobs the built-in heuristic reads. The sixth, `aux`, is bot-defined and
# so has no fixed label or range — see `_ai_specs`.
_AI_PARAMS = (
    ("ai_reserve_frac", "Reserve fraction", "reserve_fraction", 0.0, 0.9, 0.05, False),
    ("ai_reserve_floor", "Reserve floor", "reserve_floor", 0, 20, 1, True),
    ("ai_expand", "Expand margin", "expand_margin", 1.0, 3.0, 0.1, False),
    ("ai_attack", "Attack margin", "attack_margin", 1.0, 3.0, 0.1, False),
    ("ai_reinforce", "Reinforce margin", "reinforce_margin", 0, 10, 1, True),
)
_AUX_KEY = "ai_aux"

# key -> (kind, attr, lo, hi, step, is_int); kind routes the setter target.
_SLIDER_SPECS: dict[str, tuple] = {}
for _grp in (_ADV_MAP, _ADV_TRAVEL, _ADV_ECON, _ADV_COMBAT, _ADV_FOG):
    for _key, _label, _attr, _lo, _hi, _step, _is_int in _grp:
        _SLIDER_SPECS[_key] = ("adv", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in _AI_PARAMS:
    _SLIDER_SPECS[_key] = ("ai", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in _PREVIEW:
    _SLIDER_SPECS[_key] = ("preview", _attr, _lo, _hi, _step, _is_int)
# The aux knob is registered with the generic range so hit-routing knows the key;
# whether it is drawn at all, and under what label and range, is the selected
# strategy's call (`_ai_specs`, resolved live in `_slider_spec`).
_SLIDER_SPECS[_AUX_KEY] = ("ai", "aux", *ai.AUX_RANGE_DEFAULT, False)

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
    seed_text: str = ""  # edit buffer, live only while editing
    ai_seat: int = 2  # which seat the AI tab is editing
    # The Combat tab's demo fight. View state, not setup: it describes no match, so
    # it stays off `Settings` and out of every save file, token and challenge key.
    # Defaults to the fight the page's own prose works through.
    preview_attacker: int = 12
    preview_defender: int = 10
    drag_key: Optional[str] = None  # slider currently being dragged
    filename: str = _DEFAULT_FILENAME  # save/load target (no extension)
    editing_filename: bool = False
    status: str = ""  # transient save/load feedback
    status_ok: bool = True
    status_until: int = 0  # ms tick after which status hides
    strategy_open: bool = False  # is the AI-seat strategy dropdown open
    strategies: list[str] = field(  # dropdown options, refreshed on open
        default_factory=lambda: ["heuristic"]
    )
    # Un-challenge confirm modal: raised when an edit has just made the loaded
    # challenge's score incomparable. `challenge_snapshot` is the last config that
    # still matched it, so "keep the challenge" can put the setup back.
    confirm_unchallenge: bool = False
    challenge_snapshot: Optional[dict] = None
    rects: dict[str, pygame.Rect] = field(default_factory=dict)
    # Transform from real-screen coords to the fixed menu canvas, set by draw() and
    # inverted by handle_event so clicks land on the widget rects (in canvas space).
    canvas_scale: float = 1.0
    canvas_offset: tuple[int, int] = (0, 0)


def _ai_specs(ms: MenuState, settings: Settings) -> tuple:
    """Slider specs for the seat the AI tab is editing: the heuristic's five, plus
    the ``aux`` knob under whatever the seat's strategy calls it. A strategy that
    declares no meaning for ``aux`` gets no sixth slider."""
    spec = ai.aux_spec(settings.seat_strategy(ms.ai_seat))
    if spec is None:
        return _AI_PARAMS
    label, lo, hi, step, is_int = spec
    return _AI_PARAMS + ((_AUX_KEY, label, "aux", lo, hi, step, is_int),)


def _ai_seats(settings: Settings) -> list[int]:
    """Seats the AI tab can tune: opponents 2..N, plus your seat 1 in autoplay."""
    start = 1 if settings.autoplay else 2
    return list(range(start, settings.players + 1))


def _fog_off(settings: Settings) -> bool:
    """True when both fog ranges sit at max — full visibility. The Basic-tab
    "Fog of war" checkbox is the inverse of this, derived live from the sliders so
    editing them on the Advanced tab flips the checkbox automatically."""
    return settings.fog_sight >= config.FOG_MAX_HOPS and settings.fog_scout >= config.FOG_MAX_HOPS


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
    if is_web():
        # The web framebuffer (config.WEB_FB_W/H, 16:9) is a wider aspect than
        # this menu's fixed 3:2 design canvas, so the plain letterboxed fit above
        # pillarboxes a chunk of the frame on a phone. Filling more of the frame
        # crops only the canvas's blank top/bottom margin (above the title, below
        # the Start/Quit row) rather than any widget, so boost it a bit for a
        # visibly bigger, more touch-friendly menu. Capped at the full-width fit
        # so it can never overflow sideways.
        scale = min(scale * config.WEB_MENU_BOOST, sw / cw)
    dw, dh = round(cw * scale), round(ch * scale)
    ox, oy = (sw - dw) // 2, (sh - dh) // 2
    ms.canvas_scale, ms.canvas_offset = scale, (ox, oy)
    if (dw, dh) == (cw, ch):
        surface.blit(canvas, (ox, oy))
    else:
        surface.fill(config.COLOR_BG)  # letterbox bars
        surface.blit(pygame.transform.smoothscale(canvas, (dw, dh)), (ox, oy))


def _draw_menu(surface: pygame.Surface, ms: MenuState, settings: Settings) -> None:
    surface.fill(config.COLOR_BG)
    ms.rects.clear()
    w = surface.get_width()
    f = _fonts()

    _text(surface, f["title"], "STAR CONQUEST", config.COLOR_TEXT, center=(w // 2, 92))
    if settings.challenge is not None:
        _draw_challenge(surface, settings, w)
    else:
        _text(surface, f["small"], "configure your galaxy, then conquer it", config.COLOR_TEXT_DIM, center=(w // 2, 130))

    _draw_tabs(surface, ms, w)

    panel = pygame.Rect(w // 2 - 280, 208, 560, 496)
    pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=10)
    pygame.draw.rect(surface, _PANEL_BORDER, panel, 1, border_radius=10)

    if ms.tab == "basic":
        _draw_basic(surface, ms, settings, panel)
    elif ms.tab == "combat":
        _draw_combat(surface, ms, settings, panel)
    elif ms.tab == "advanced":
        _draw_advanced(surface, ms, settings, panel)
    elif ms.tab == "ai":
        _draw_ai(surface, ms, settings, panel)

    _file_control(surface, ms, w, 720)
    _draw_start(surface, ms, w)
    # Two separate lines below the Start row: the transient save/load status, then
    # the keyboard hint (dropped on touch, where there are no keys to press — and
    # where it used to be drawn straight on top of the status).
    if ms.status and pygame.time.get_ticks() < ms.status_until:
        _text(surface, f["small"], ms.status, _START_BORDER if ms.status_ok else _STATUS_ERR, center=(w // 2, 852))
    if not config.touch_ui:
        _text(surface, f["small"], "Enter: start game   ·   Esc: quit", config.COLOR_TEXT_DIM, center=(w // 2, 886))
    if ms.confirm_unchallenge:  # last, so the modal veils every widget above
        _draw_unchallenge(surface, ms, settings, w, surface.get_height())


def _draw_challenge(surface, settings: Settings, w: int) -> None:
    """Two lines where the subtitle normally sits: the score to beat, then the
    setup it was scored on.

    A challenge *is* the most important fact about the session, so it takes the
    subtitle's slot rather than competing for space elsewhere — the band between
    the title (y 92) and the tab row (y 162) is the only room there is.

    Editing any setting invalidates the comparison, which the challenge's own
    ``key`` checksum detects. That warns rather than locking the widgets: locking
    is a dead end the moment someone wants to try the same map with one knob
    moved, and it would make the seed field unusable.
    """
    f = _fonts()
    ch = settings.challenge
    valid = ch.matches(settings)

    head = f"CHALLENGE — beat {ch.summary()}"
    if ch.by:
        head += f"   ·   from {ch.by}"
    _text(surface, f["small"], head, _START_BORDER if valid else _WARN, center=(w // 2, 124))

    if not valid:
        _text(surface, f["small"], "settings changed — your score won't compare", _WARN, center=(w // 2, 144))
        return

    bots = [settings.seat_strategy(s) for s in range(2, settings.players + 1)]
    bits = [f"seed {settings.seed}", f"{settings.nodes} nodes", settings.mode]
    if bots:
        bits.append("vs " + ", ".join(bots))
    mine = webstore.best(*settings.challenge_keys())
    if mine is not None:
        bits.append(f"your best: {mine[0]} turns / {mine[1]} lost")
    _text(surface, f["small"], "   ·   ".join(bits), config.COLOR_TEXT_DIM, center=(w // 2, 144))


def _unchallenge_labels() -> tuple[str, str]:
    """(change-anyway, keep-the-challenge) labels; key hints dropped on a touch
    build, where there is no Y/N to press — as `_resume_labels` does."""
    if config.touch_ui:
        return ("Change it anyway", "Keep the challenge")
    return ("Change it anyway (Y)", "Keep the challenge (Esc)")


def _draw_unchallenge(surface, ms: MenuState, settings: Settings, w: int, h: int) -> None:
    """Modal: this edit would make the challenge's score meaningless — confirm.

    Asking beats the alternatives. Locking the widgets is a dead end the moment
    someone wants the same map with one knob moved, and silently letting the edit
    through leaves a score-to-beat on screen that no longer means anything (which
    is how a finished game ended up reporting "short of" a target from a different
    setup). Measured and centred rather than positioned, like `_draw_modal` in
    render — the labels grow on a phone.
    """
    f = _fonts()
    change, keep = _unchallenge_labels()
    ch = settings.challenge
    lines = [
        ("Change the challenge setup?", f["normal"], config.COLOR_TEXT),
        ("Your result won't compare to the score on the link", f["small"], _WARN),
        # `summary` brings its own parenthetical, so don't wrap it in more.
        (f"Target: {ch.summary()}" if ch is not None else "", f["small"], config.COLOR_TEXT_DIM),
    ]

    pad, gap, row = 28, 14, 34
    bw = max(f["normal"].size(s)[0] for s in (change, keep)) + 2 * 18
    bh = 40
    text_w = max(font.size(text)[0] for text, font, _ in lines)
    pw = max(text_w, 2 * bw + gap) + 2 * pad
    ph = pad + len(lines) * row + gap + bh + pad
    panel = pygame.Rect((w - pw) // 2, (h - ph) // 2, pw, ph)

    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=10)
    pygame.draw.rect(surface, _WARN, panel, 2, border_radius=10)

    y = panel.y + pad + row // 2
    for text, font, color in lines:
        _text(surface, font, text, color, center=(panel.centerx, y))
        y += row

    by = panel.bottom - pad - bh
    _button(
        surface, ms, "unchallenge_change", pygame.Rect(panel.centerx - bw - gap // 2, by, bw, bh), change, fill=_BTN_FILL, border=_WARN, tcol=config.COLOR_TEXT
    )
    _button(surface, ms, "unchallenge_keep", pygame.Rect(panel.centerx + gap // 2, by, bw, bh), keep, fill=_HL_FILL, border=_HL_BORDER, tcol=config.COLOR_TEXT)


def _draw_tabs(surface, ms: MenuState, w: int) -> None:
    # 126 rather than the old 132: four tabs at 132 span 552px inside the 560px
    # panel below, which reads as flush-by-accident. At 126 they span 528 and sit
    # a clean 16px inside it, still leaving 19px either side of "Advanced".
    tw, th, gap = 126, 34, 8
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
    _segmented(
        surface,
        ms,
        right,
        y,
        [
            ("mode_random", "Random", settings.mode == "random"),
            ("mode_symmetric", "Symmetric", settings.mode == "symmetric"),
        ],
    )
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

    y = panel.y + 16  # LEFT: Map + Travel
    y = _section(surface, "Map", lx, y)
    y = _sliders(surface, ms, settings, _ADV_MAP, lx, y, col_w)
    y = _section(surface, "Travel", lx, y)
    y = _sliders(surface, ms, settings, _ADV_TRAVEL, lx, y, col_w)
    _text(surface, _fonts()["small"], "Randomise all", config.COLOR_TEXT_DIM, midleft=(lx, y + _CH // 2))
    _die_button(surface, ms, "randomise_adv", pygame.Rect(lx + col_w - _CH, y, _CH, _CH))

    y = panel.y + 16  # RIGHT: Visibility + Economy
    y = _section(surface, "Visibility", rx, y)
    y = _sliders(surface, ms, settings, _ADV_FOG, rx, y, col_w)
    y = _section(surface, "Economy", rx, y)
    y = _sliders(surface, ms, settings, _ADV_ECON, rx, y, col_w)
    _text(surface, _fonts()["small"], "Neutral produces", config.COLOR_TEXT_DIM, midleft=(rx, y + _CH // 2))
    _checkbox(surface, ms, "neutral_produces", settings.neutral_produces, rx + col_w, y)


# --------------------------------------------------------------------------- #
# Combat tab
#
# The rules, plus a live demonstration of them. Nothing player-facing explained
# the square law before this page, and the natural guess (subtract the fleets) is
# wrong by roughly a factor of two, so the page's whole job is to put the real
# number next to the guessed one.
#
# The prose is hand-broken rather than reflowed. Every other measured-layout rule
# in this project exists because the *font* scales; the menu canvas is fixed, so
# here the risk runs the other way — a runtime wrap makes the page's height
# depend on its text, and one added word would silently push the table through
# the panel floor. Broken by hand, the height is a constant that can be checked.
# Content width is 516px in a mono font: 46 chars at `normal`, 64 at `small`.
# --------------------------------------------------------------------------- #
_COMBAT_PROSE = (
    "Battles use Lanchester's square law.",
    "The winner keeps sqrt(W² − L²) ships",
    "e.g. 12 attacking 10 leaves 6 or 7 survivors",
)


def _draw_combat(surface, ms: MenuState, settings: Settings, panel: pygame.Rect) -> None:
    pad = 22
    col_w = (panel.width - pad * 3) // 2
    lx = panel.x + pad
    rx = lx + col_w + pad
    full = col_w * 2 + pad
    f = _fonts()

    y = _section(surface, "How it works", lx, panel.y + 12)
    for line in _COMBAT_PROSE:
        _text(surface, f["normal"], line, config.COLOR_TEXT, midleft=(lx, y + _PROSE_H // 2))
        y += _PROSE_H

    y += 16
    _section(surface, "The fight", lx, y)
    y = _section(surface, "The rules", rx, y)
    # `ms` is both the rect store and the value target here — the demo sliders read
    # and write MenuState, unlike every other group on every other tab.
    _sliders(surface, ms, ms, _PREVIEW, lx, y, col_w)
    y = _sliders(surface, ms, settings, _ADV_COMBAT, rx, y, col_w)

    preview = combat.preview_fight(
        ms.preview_attacker, ms.preview_defender, settings.combat_jitter, settings.defender_advantage
    )
    y = _draw_fight_readout(surface, preview, lx, y + 10, full)
    y = _section(surface, "Jitter matrix — your swing across, theirs down", lx, y + 10)
    _draw_jitter_matrix(surface, preview, lx, y, full)


def _readout_lines(preview: combat.CombatPreview) -> tuple[str, tuple[int, int, int], str, str]:
    """(headline, headline colour, detail, band) for one previewed fight.

    Pure and separate from the drawing so the three lines can be checked against
    each other for every slider position — they describe the same fight from
    three angles and must never disagree.
    """
    roll = preview.nominal
    a, d, s = preview.attacker, preview.defender, roll.survivors

    if roll.winner == combat.ATTACKER:
        headline, color = f"{a} vs {d} → you keep {s}", config.player_color(1)
    elif roll.winner == combat.DEFENDER:
        headline, color = f"{a} vs {d} → they keep {s}", config.player_color(2)
    else:
        headline, color = f"{a} vs {d} → both wiped out", _DISABLED_TEXT
    # Never assert a winner the dice don't guarantee: an uncertain fight goes amber
    # whoever the average roll favours.
    if not preview.certain:
        color = _WARN

    # What each side actually loses. The sub-1:1 exchange *is* the square law, so
    # stating both losses teaches it without reaching for a counterfactual.
    if preview.annihilation:
        detail = "Both fleets are spent — the system goes neutral."
    elif roll.winner == combat.ATTACKER:
        lost = f"You lose {a - s}" if s < a else "You lose nothing"
        detail = f"{lost}, they lose all {d}."
    else:
        lost = f"They lose {d - s}" if s < d else "They lose nothing"
        detail = f"{lost}, you lose all {a}."

    pct = int(round(preview.jitter * 100))
    if preview.jitter <= 0:
        band = "Jitter off: this result is exact."
    elif not preview.certain:
        # Name the likely side — the corners disagree, but the average roll still
        # favours one — then give both ends, so "uncertain" comes with numbers.
        likely = {combat.ATTACKER: "yours", combat.DEFENDER: "theirs"}.get(roll.winner, "a wipe-out")
        band = (
            f"Jitter ±{pct}%: likely {likely}, you keep {preview.best.attacker_survivors}"
            f" to them {preview.worst.defender_survivors}."
        )
    elif preview.best.winner == combat.ATTACKER:
        band = "Jitter ±{}%: you keep {}–{}.".format(pct, *preview.band)
    elif preview.best.winner == combat.DEFENDER:
        # certain the other way: the defender's best roll is the attacker's worst
        band = f"Jitter ±{pct}%: they keep {preview.best.defender_survivors}–{preview.worst.defender_survivors}."
    else:  # both corners annihilate — tiny matched fleets, where no roll saves either
        band = f"Jitter ±{pct}%: no roll leaves a survivor."

    return headline, color, detail, band


def _draw_fight_readout(surface, preview: combat.CombatPreview, x: int, y: int, width: int) -> int:
    """The headline outcome, the contrast with what subtraction would say, and how
    far jitter can move it. Returns the y below the box."""
    f = _fonts()
    box = pygame.Rect(x, y, width, 88)
    pygame.draw.rect(surface, _TROUGH, box, border_radius=8)
    pygame.draw.rect(surface, _PANEL_BORDER, box, 1, border_radius=8)

    headline, color, detail, band = _readout_lines(preview)
    _text(surface, f["big"], headline, color, midleft=(x + 14, y + 25))
    _text(surface, f["small"], detail, config.COLOR_TEXT, midleft=(x + 14, y + 52))
    _text(surface, f["small"], band, config.COLOR_TEXT_DIM, midleft=(x + 14, y + 70))
    return box.bottom


def _draw_jitter_matrix(surface, preview: combat.CombatPreview, x: int, y: int, width: int) -> int:
    """Every corner of the jitter square at once: the attacker's swing across, the
    defender's down, so the centre cell is the average roll and the top-left /
    bottom-right corners are the attacker's worst and best cases.

    A single curve can only ever show one slice of the randomness. Laid out as a
    grid the win/loss boundary becomes a *shape* — a solid block of colour when
    the fight is settled, a diagonal split when it is a coin toss — which is the
    thing players were failing to get from a lone number.

    The centre cell is the fight the readout above spells out in words, and is
    highlighted to say so: it is the same figure in the same colour, which is
    what teaches the rest of the grid to be read.
    """
    f = _fonts()
    gutter = _TLABEL_W
    cell_w = (width - gutter) // _MATRIX_N
    # Each axis runs -1 .. +1 as a fraction of jitter. Keep the count odd so the
    # middle sample is exactly 0.0 — that centre cell is the fight the readout
    # describes, and is matched by identity below.
    swings = tuple(-1.0 + 2.0 * i / (_MATRIX_N - 1) for i in range(_MATRIX_N))

    if preview.jitter <= 0:  # every cell would be the same fight, and every
        # header would read "0%" — a grid that says nothing, three times over
        _text(surface, f["small"], "Jitter is off, so every battle plays out exactly like this.",
              config.COLOR_TEXT_DIM, midleft=(x, y + _NOTE_H // 2))
        return y + _NOTE_H

    def label(swing: float) -> str:
        pct = swing * preview.jitter * 100
        return "0%" if not pct else f"{'+' if pct > 0 else '−'}{abs(pct):.0f}%"

    def centre(i: int) -> int:
        return x + gutter + cell_w * i + cell_w // 2

    # Attacker's swing rises left to right; the defender's *falls* top to bottom,
    # so the attacker's worst case sits top-left and its best bottom-right —
    # reading down-right is reading from bad luck to good.
    for i, swing in enumerate(swings):
        _text(surface, f["small"], label(swing), config.COLOR_TEXT_DIM, center=(centre(i), y + _NOTE_H // 2))
    y += _NOTE_H
    pygame.draw.line(surface, _PANEL_BORDER, (x, y), (x + width, y), 1)

    for row, d_swing in enumerate(reversed(swings)):
        cy = y + _MATRIX_ROW_H * row + _MATRIX_ROW_H // 2
        _text(surface, f["small"], label(d_swing), config.COLOR_TEXT_DIM, midright=(x + gutter - 14, cy))
        for i, a_swing in enumerate(swings):
            if a_swing == 0.0 and d_swing == 0.0:  # the fight the readout describes
                box = pygame.Rect(centre(i) - cell_w // 2, cy - _MATRIX_ROW_H // 2, cell_w, _MATRIX_ROW_H)
                pygame.draw.rect(surface, _HL_FILL, box, border_radius=4)
            roll = preview.roll(a_swing, d_swing)
            if roll.winner == combat.ATTACKER:
                text, color = f"you {roll.survivors}", config.player_color(1)
            elif roll.winner == combat.DEFENDER:
                text, color = f"them {roll.survivors}", config.player_color(2)
            else:
                text, color = "wipe-out", _DISABLED_TEXT
            _text(surface, f["small"], text, color, center=(centre(i), cy))
    return y + _MATRIX_ROW_H * _MATRIX_N


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
        pygame.draw.rect(surface, config.COLOR_SELECT if selected else _BTN_BORDER, rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], str(seat), config.text_on(fill), center=rect.center)
        ms.rects[f"seat_{seat}"] = rect
        cx += 52

    # edit-all shortcuts, pinned near the top
    y += 44
    bw = 150
    _button(surface, ms, "copy_all", pygame.Rect(x, y, bw, _CH), "Copy to all", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    _button(surface, ms, "reset_all", pygame.Rect(x + bw + 12, y, bw, _CH), "Reset all", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    dice = pygame.Rect(x + 2 * (bw + 12), y, _CH, _CH)
    _die_button(surface, ms, "randomise_ai", dice)
    name = config.player_name(ms.ai_seat)
    _text(surface, _fonts()["small"], f"editing {name}", config.player_color(ms.ai_seat), midleft=(dice.right + 12, y + _CH // 2))

    # strategy dropdown (built-in heuristic + any drop-in models/)
    y += 44
    _text(surface, _fonts()["small"], "Strategy", config.COLOR_TEXT_DIM, midleft=(x, y + _CH // 2))
    _dropdown(surface, ms, "strategy", settings.seat_strategy(ms.ai_seat), ms.strategies, ms.strategy_open, x + 100, y, panel.width - 48 - 100)

    # per-seat param sliders (hidden while the dropdown is open so its options,
    # which overlay this region, own the hit-test — no slider rect underneath)
    y += 46
    if not ms.strategy_open:
        params = settings.ai[ms.ai_seat - 1]
        _sliders(surface, ms, params, _ai_specs(ms, settings), x, y, panel.width - 48)


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
    if editing:
        shown, color = ms.seed_text, config.COLOR_TEXT
    elif settings.seed is None:
        shown, color = "random", config.COLOR_TEXT_DIM
    else:
        shown, color = str(settings.seed), config.COLOR_TEXT
    _text_field(surface, _fonts()["normal"], field, shown, editing, color)
    ms.rects["seed_field"] = field

    # A drawn die (not an emoji glyph — the monospace font has no colour emoji).
    pygame.draw.rect(surface, _BTN_FILL, dice, border_radius=6)
    pygame.draw.rect(surface, _BTN_BORDER, dice, 2, border_radius=6)
    _draw_die(surface, dice)
    ms.rects["seed_random"] = dice


def _file_control(surface, ms: MenuState, w: int, y: int) -> None:
    """Footer row: '[ name ] [Save] [Load]' — mirrors the seed field. On the web
    build a leading '[Get Link]' shares the whole config as a URL.

    The field takes whatever width the buttons leave (no "File" label: with four
    controls on the row on web, the name field had less room than the default name
    needs, and Save/Load say plainly enough what the row is for)."""
    f = _fonts()
    lx, rx = w // 2 - 280, w // 2 + 280

    load = pygame.Rect(rx - 90, y, 90, _CH)
    save = pygame.Rect(load.x - 10 - 90, y, 90, _CH)
    fx = lx
    if is_web():
        link = pygame.Rect(fx, y, 110, _CH)
        _button(surface, ms, "get_link", link, "Get Link", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
        fx = link.right + 10
    else:
        ms.rects.pop("get_link", None)
    field = pygame.Rect(fx, y, save.x - 10 - fx, _CH)

    _text_field(surface, f["normal"], field, ms.filename or _DEFAULT_FILENAME, ms.editing_filename, config.COLOR_TEXT)
    ms.rects["filename_field"] = field

    _button(surface, ms, "save_settings", save, "Save", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)
    _button(surface, ms, "load_settings", load, "Load", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT)


def _text_field(surface, font, rect: pygame.Rect, text: str, editing: bool, color) -> None:
    """A text box holding ``text``, with a caret while ``editing``.

    The content is clipped to the box and anchored to its *end*, so a name longer
    than the field runs off the left rather than out over the buttons beside it —
    and the caret you are typing at stays in view.
    """
    pygame.draw.rect(surface, _TROUGH, rect, border_radius=6)
    pygame.draw.rect(surface, _HL_BORDER if editing else _BTN_BORDER, rect, 2, border_radius=6)
    shown = text + "|" if editing else text
    pad = 10
    img = font.render(shown, True, color)
    inner = rect.inflate(-2 * pad, -4)
    prev = surface.get_clip()
    surface.set_clip(inner)
    if img.get_width() <= inner.width:
        surface.blit(img, img.get_rect(midleft=(inner.left, rect.centery)))
    else:
        surface.blit(img, img.get_rect(midright=(inner.right, rect.centery)))
    surface.set_clip(prev)


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
    for px, py in ((cx - o, cy - o), (cx + o, cy - o), (cx, cy), (cx - o, cy + o), (cx + o, cy + o)):
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
    _button(surface, ms, "start", rect, "Start Game", fill=_START_FILL, border=_START_BORDER, tcol=config.COLOR_TEXT, font=_fonts()["normal"])
    # Touch/web equivalent of Esc's quit — there's no keyboard on a phone, so
    # without this a touch user has no way to leave the app at all.
    quit_rect = pygame.Rect(rect.right + 14, rect.y, 110, rect.height)
    _button(surface, ms, "quit", quit_rect, "Quit", fill=_BTN_FILL, border=_BTN_BORDER, tcol=config.COLOR_TEXT_DIM, font=_fonts()["normal"])


# --------------------------------------------------------------------------- #
# Resume prompt — a boot-time modal offered when the last game was left unfinished
# --------------------------------------------------------------------------- #
def _resume_labels() -> tuple[str, str]:
    """(resume, new-game) button labels; key hints dropped on a touch build, where
    there is no Y/N to press — the same wording rule the game's modals use."""
    if config.touch_ui:
        return ("Resume", "New game")
    return ("Resume (Y/Enter)", "New game (N/Esc)")


def resume_prompt_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(resume, new-game) button rects — shared by the drawer and the hit-tester.

    Drawn on the real surface rather than the fixed canvas the widgets above use,
    so this is the one part of the menu that scales through ``config.s``; the boxes
    are measured to fit their labels, which at a touch scale are wider than any
    fixed width would allow for.
    """
    w, h = surface.get_size()
    font = _modal_fonts()["normal"]
    bw = max(font.size(s)[0] + 2 * config.BTN_PAD_X for s in _resume_labels())
    bw = max(bw, config.s(200))
    bh = max(config.s(42), font.get_height() + config.s(16))
    gap, y = config.s(12), h // 2 + config.s(24)
    return (pygame.Rect(w // 2 - bw - gap, y, bw, bh), pygame.Rect(w // 2 + gap, y, bw, bh))


def draw_resume_prompt(surface, log) -> None:
    """Modal veil offering to resume ``log`` (an in-progress match), over the menu.

    Stacked upward from the button row so the title and the detail line always
    clear it, whatever the fonts scale to.
    """
    w, h = surface.get_size()
    f = _modal_fonts()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))

    st = log.settings
    detail = f"turn {log.turn_count} · {st.get('players', '?')} players · {st.get('mode', 'random')} map"
    detail_h = f["small"].get_height() + config.ROW_GAP
    y = h // 2 - config.s(12) - detail_h - f["big"].get_height()
    _text(surface, f["big"], "Resume last game?", config.COLOR_TEXT, center=(w // 2, y + f["big"].get_height() // 2))
    y += f["big"].get_height() + config.ROW_GAP
    _text(surface, f["small"], detail, config.COLOR_TEXT_DIM, center=(w // 2, y + f["small"].get_height() // 2))

    for rect, label, fill, edge in zip(resume_prompt_buttons(surface), _resume_labels(), (_START_FILL, _BTN_FILL), (_START_BORDER, _BTN_BORDER)):
        pygame.draw.rect(surface, fill, rect, border_radius=config.s(6))
        pygame.draw.rect(surface, edge, rect, config.s(2), border_radius=config.s(6))
        _text(surface, f["normal"], label, config.COLOR_TEXT, center=rect.center)


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def handle_event(event, ms: MenuState, settings: Settings):
    # Map pointer coords from the real screen back into canvas space (the widget
    # rects live in canvas space), then dispatch. Also raise/dismiss the on-screen
    # keyboard as a text field gains/loses focus (a no-op on desktop).
    event = _to_canvas_event(event, ms)
    if ms.confirm_unchallenge:  # modal: swallows everything until answered
        _handle_unchallenge(event, ms, settings)
        return None
    if event.type in _MUTATING_EVENTS and _comparable(settings):
        # Remember the last setup the challenge's score still applied to, before
        # this event gets a chance to change it. A slider drag is covered by the
        # snapshot its opening MOUSEBUTTONDOWN took.
        ms.challenge_snapshot = settings.to_dict()
    before = _editing_field(ms)
    result = _dispatch(event, ms, settings)
    _sync_text_input(ms, before)
    if settings.challenge is not None and ms.drag_key is None and not settings.challenge.matches(settings):
        # Edited away from the challenge's setup. Ask rather than silently
        # invalidating the score — and wait for a slider to be released first, so
        # a drag isn't interrupted on its very first pixel.
        ms.confirm_unchallenge = True
    return result


def _comparable(settings: Settings) -> bool:
    """Is there a challenge whose score still applies to this exact setup?"""
    return settings.challenge is not None and settings.challenge.matches(settings)


def _handle_unchallenge(event, ms: MenuState, settings: Settings) -> None:
    """Answer the un-challenge modal: keep the edit and drop the score, or put the
    setup back the way the link had it."""
    keep_edit: Optional[bool] = None
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        for key, answer in (("unchallenge_change", True), ("unchallenge_keep", False)):
            rect = ms.rects.get(key)
            if rect is not None and rect.collidepoint(event.pos):
                keep_edit = answer
    elif event.type == pygame.KEYDOWN:
        if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
            keep_edit = True
        elif event.key in (pygame.K_n, pygame.K_ESCAPE):
            keep_edit = False
    if keep_edit is None:
        return

    ms.confirm_unchallenge = False
    if keep_edit:
        settings.challenge = None
        ms.challenge_snapshot = None
        # Also drop it from the address bar and the remembered token, or a reload
        # would resurrect the banner we were just asked to get rid of.
        webstore.sync_settings(settings.to_token())
        set_status(ms, "Challenge cleared — this is your own setup now", True)
    elif ms.challenge_snapshot is not None:
        settings.copy_from(Settings.from_dict(ms.challenge_snapshot))
        # The seed field keeps its own edit buffer, so resync it or the box would
        # still show the rejected number.
        ms.seed_text = "" if settings.seed is None else str(settings.seed)


def pump(ms: MenuState, settings: Settings) -> None:
    """Per-frame poll of the mobile browser's on-screen keyboard; a no-op
    everywhere else. Called by ``main.py`` while the menu scene is up.

    Typing on a soft keyboard produces no SDL events at all, so the text arrives
    by reading the hidden DOM field back (``softkeyboard``) and filtering it into
    the same edit buffers ``_handle_text_input`` fills. This is the mutate half of
    the scene, alongside ``handle_event`` — ``draw`` still only reads."""
    field_name = _editing_field(ms)
    if field_name is None:
        return
    if field_name == "seed":
        raw = softkeyboard.value(ms.seed_text)
        text = _filter(raw, str.isdigit, _SEED_MAX_LEN)
        if text != ms.seed_text:
            ms.seed_text = text
            _apply_seed_text(ms, settings)
    else:
        raw = softkeyboard.value(ms.filename)
        text = _filter(raw, lambda c: c.isalnum() or c in "_-.", _FILENAME_MAX_LEN)
        ms.filename = text
    if text != raw:
        # Only on a rejected character: writing back every frame would drag the
        # caret to the end and stop the player editing mid-string.
        softkeyboard.set_value(text)
    if softkeyboard.dismissed():  # Done/Go, or the keyboard swiped away
        ms.editing_seed = ms.editing_filename = False
        softkeyboard.close()


def _filter(text: str, allowed, limit: int) -> str:
    """``text`` reduced to the characters a field accepts, capped at ``limit``."""
    return "".join(ch for ch in text if allowed(ch))[:limit]


def _editing_field(ms: MenuState) -> Optional[str]:
    """Which text field has the caret, if any — ``"seed"``, ``"file"`` or None."""
    if ms.editing_seed:
        return "seed"
    return "file" if ms.editing_filename else None


def _to_canvas_event(event, ms: MenuState):
    """Return ``event`` with any pointer position mapped from screen to canvas
    space (inverse of draw()'s fit transform). Non-pointer events pass through."""
    if event.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
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
    if event.type == pygame.TEXTINPUT:  # characters (soft keyboard or physical)
        return _handle_text_input(event.text, ms, settings)
    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ms, settings)
    return None


def _sync_text_input(ms: MenuState, before: Optional[str]) -> None:
    """Raise/dismiss the on-screen keyboard as a text field gains/loses focus (or
    the caret moves between the two fields).

    Two mechanisms, since no single one covers every platform: SDL's IME call
    shows Android's native keyboard and toggles TEXTINPUT delivery on desktop,
    while the browser needs a focused DOM field (``softkeyboard``). Both are
    guarded, so a headless/uninitialised video subsystem or a non-web build just
    skips the one that doesn't apply. The focus happens inside this tap's
    handling, which is what lets the browser accept it as a user gesture."""
    now = _editing_field(ms)
    if now == before:
        return
    try:
        pygame.key.start_text_input() if now else pygame.key.stop_text_input()
    except pygame.error:
        pass
    if now is None:
        softkeyboard.close()
    else:
        softkeyboard.open(ms.seed_text if now == "seed" else ms.filename)


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
            ms.editing_filename = False  # commit / cancel are the same here
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
        ms.strategy_open = False  # click anywhere else closes the dropdown

    if hit == "start":
        return "start"
    if hit == "quit":
        return "quit"
    if hit is None:
        return None
    if hit in _SLIDER_SPECS:  # grab + jump the slider
        ms.drag_key = hit
        _apply_slider(hit, ms, settings, pos)
    elif hit.startswith("tab_"):
        ms.tab = hit[len("tab_") :]
    elif hit.startswith("seat_"):
        ms.ai_seat = int(hit[len("seat_") :])
    elif hit == "strategy":  # toggle the dropdown, rescanning models/
        if not ms.strategy_open:
            ai.load_models()
            ms.strategies = ai.available_strategies()
        ms.strategy_open = not ms.strategy_open
    elif hit.startswith("strategy_opt_"):
        idx = int(hit[len("strategy_opt_") :])
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
        _randomise_sliders(settings.ai[ms.ai_seat - 1], _ai_specs(ms, settings))
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
        settings.seed = random_seed()
        ms.seed_text = str(settings.seed)
    elif hit == "autoplay":
        settings.autoplay = not settings.autoplay
    elif hit == "fog_of_war":
        if _fog_off(settings):  # off -> on: apply the fog preset
            settings.fog_sight = config.FOG_ON_SIGHT
            settings.fog_scout = config.FOG_ON_SCOUT
        else:  # on -> off: full visibility
            settings.fog_sight = settings.fog_scout = config.FOG_MAX_HOPS
    elif hit == "filename_field":
        ms.editing_filename = True
    elif hit == "save_settings":
        path = _settings_path(ms.filename)
        try:
            _SAVE_DIR.mkdir(parents=True, exist_ok=True)
            settings.save(path)
            set_status(ms, f"Saved {path.name}", True)
        except OSError:
            set_status(ms, f"Couldn't save {path.name}", False)
    elif hit == "load_settings":
        path = _settings_path(ms.filename)
        try:
            settings.copy_from(Settings.load(path))
            set_status(ms, f"Loaded {path.name}", True)
        except (OSError, ValueError):
            set_status(ms, f"Couldn't load {path.name}", False)
    elif hit == "get_link":
        ok, copied = webstore.share_token(settings.to_token())
        if copied:
            set_status(ms, "Link copied — paste to share", True)
        elif ok:
            set_status(ms, "Link updated — copy it from the address bar", True)
        else:
            set_status(ms, "Couldn't create link", False)
    return None


def _slider_spec(key: str, ms: MenuState, settings: Settings) -> Optional[tuple]:
    """``_SLIDER_SPECS[key]``, except the aux knob, whose range belongs to the
    strategy the edited seat is running (None if it declares no aux knob)."""
    if key == _AUX_KEY:
        for k, _label, attr, lo, hi, step, is_int in _ai_specs(ms, settings):
            if k == key:
                return ("ai", attr, lo, hi, step, is_int)
        return None
    return _SLIDER_SPECS.get(key)


def _apply_slider(key: str, ms: MenuState, settings: Settings, pos) -> None:
    spec = _slider_spec(key, ms, settings)
    track = ms.rects.get(key)
    if spec is None or track is None:
        return
    kind, attr, lo, hi, step, is_int = spec
    t = 0.0 if track.w == 0 else max(0.0, min(1.0, (pos[0] - track.x) / track.w))
    raw = lo + t * (hi - lo)
    snapped = round(raw / step) * step
    snapped = max(lo, min(hi, snapped))
    value = int(round(snapped)) if is_int else round(snapped, 4)
    if kind == "adv":
        target = settings
    elif kind == "preview":
        target = ms  # transient: never reaches Settings, so no un-challenge prompt
    else:
        target = settings.ai[ms.ai_seat - 1]
    setattr(target, attr, value)


def _randomise_sliders(target, specs) -> None:
    """Scramble every slider in ``specs`` to a random in-bounds, step-snapped value
    on ``target`` — the Advanced/AI 'roll' buttons, just for fun. Shares the
    snap-and-clamp logic with ``_apply_slider``."""
    rng = fresh_rng()
    for _key, _label, attr, lo, hi, step, is_int in specs:
        snapped = round(rng.uniform(lo, hi) / step) * step
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


def set_status(ms: MenuState, text: str, ok: bool) -> None:
    """Show ``text`` under the Start row for a few seconds. Public because
    ``main`` posts here too — it is the menu's one line for telling the player
    what just happened (see the web quit fallback in ``main.leave_app``)."""
    ms.status = text
    ms.status_ok = ok
    ms.status_until = pygame.time.get_ticks() + 4000


def _set_players(settings: Settings, n: int) -> None:
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, n))
    if settings.nodes < settings.min_nodes():
        settings.nodes = settings.min_nodes()


def _set_nodes(settings: Settings, n: int) -> None:
    settings.nodes = max(settings.min_nodes(), min(config.MAX_NODES, n))
