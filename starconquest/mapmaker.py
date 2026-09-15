"""The map creator scene — a full-window editor for hand-authoring a star map.

Shell, not core: this is the third pygame module beside ``render``/``input`` and
``menu``, and it keeps the same draw/mutate split — ``draw`` only reads the
``Editor`` and the ``Settings``, ``handle_event`` only writes them and returns a
high-level action string for ``main`` to act on.

**Its working model is the recipe, not a live ``GameState``.** Positions plus lane
index pairs, so the lanes follow their nodes for free and undo is a copy of plain
data — see ``custommap.CustomMap`` for the full argument. The one consequence to
respect is that node identity is positional, so deletion goes through
``CustomMap.without_node`` and nowhere else.

**It draws on the real surface** (``config.ui_scale``-aware, measured layout),
following ``render`` rather than ``menu``. That is not a style preference:
``menu`` lays out on a fixed 1440x960 canvas and rounds pointer coordinates
through a float scale on the way back, and stacking that on ``WorldView.to_world``
gives two lossy inversions in series — at ``config.ZOOM_MAX`` a system would not
land where you tapped.

Fidelity with the game board comes from sharing *primitives*, not drawing
functions: ``config.node_radius``, ``config.player_color``, ``config.text_on``
and ``widgets.lane_style``. ``render._draw_systems`` is threaded through fog, film
and order state the editor has none of.

Star names are deliberately absent — they are stamped from ``state.rng`` when a
map is built and nothing is serialized, so the editor shows ids (``#7``). See
``custommap``'s module docstring before "fixing" that.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pygame

from . import (config, custommap, mapgen, paths, settings as settings_mod,
               softkeyboard, widgets)
from .custommap import CustomMap, MapNode, Problem
from .geometry import WorldView, dist, point_segment_dist
from .settings import Settings

# Tool modes. Phase 1 ships the first; the strip is drawn for all three so the
# scene's layout — and therefore the camera — never moves as they arrive.
SYSTEMS, LANES, OWNERS = "systems", "lanes", "owners"
_TOOLS = ((SYSTEMS, "Systems"), (LANES, "Lanes"), (OWNERS, "Owners"))
_READY_TOOLS = (SYSTEMS, LANES)

# The production palette: a weighted roll, then the six explicit values.
# `config.node_radius` clamps, so 1 and 2 draw the same size and 5 and 6 do too —
# the numeral in the swatch is what tells them apart, which is the same
# arrangement Advanced's home-production slider (1..8) already lives with.
PALETTE_RANDOM = "random"
_PALETTE_VALUES = (1, 2, 3, 4, 5, 6)

_DEFAULT_FILENAME = "map"
_FILENAME_MAX_LEN = 24

_STATUS_MS = 4000
_STATUS_ERROR_MS = 15000

_PANEL_BG = (22, 25, 36)
_PANEL_EDGE = (48, 54, 74)
_WARN = (220, 150, 90)
_LANE_ARM = (150, 200, 240)   # the armed lane source, and its rubber band
_BAD = (220, 110, 110)


@dataclass
class Editor:
    """Everything the creator scene holds. Analogous to ``viewstate.Ui``: purely
    transient interaction state, with the one durable thing — ``recipe`` — handed
    to ``Settings`` on commit."""

    recipe: CustomMap
    view: WorldView
    # A throwaway generator for the "roll me a system" values. Never the global
    # `random` module: the web build boots from a fixed interpreter image, so
    # `random`'s auto-seeding can hand out the same sequence on every page load.
    rng: random.Random = field(default_factory=settings_mod.fresh_rng)

    tool: str = SYSTEMS
    pick: object = PALETTE_RANDOM        # PALETTE_RANDOM, or an int production

    sel_node: Optional[int] = None
    sel_lane: Optional[int] = None
    # Lane drawing: one armed source serving both gestures. A tap leaves it armed
    # so the next press commits; a drag past the threshold commits on release.
    lane_src: Optional[int] = None
    lane_drag: bool = False
    lane_pos: tuple[int, int] = (0, 0)
    # Refuse to *draw* a crossing lane. Editor-time only, and that is the whole
    # point: `custommap` reports a crossing as a warning rather than a blocker, so
    # turning this off leaves a map that still plays and still shares.
    planar: bool = True
    drag_node: Optional[int] = None
    drag_origin: Optional[tuple[int, int]] = None   # world coords, for the snap-back
    drag_start: tuple[int, int] = (0, 0)            # screen coords the press landed at
    drag_moved: bool = False
    drag_undone: bool = False                       # is this drag's undo snapshot taken?
    pan_active: bool = False
    pan_last: tuple[int, int] = (0, 0)
    pan_button: int = 3    # which button armed the pan (Lanes allows the left one)
    drag_slider: Optional[str] = None

    # What the live validator is complaining about *right now*, so an illegal
    # drag rings amber under the cursor rather than only failing on release.
    bad_nodes: frozenset[int] = frozenset()
    bad_lanes: frozenset[int] = frozenset()

    undo_stack: list[CustomMap] = field(default_factory=list)
    redo_stack: list[CustomMap] = field(default_factory=list)

    filename: str = ""
    editing_filename: bool = False
    confirm: Optional[str] = None        # "auto_lanes" | "new" | None

    status: str = ""
    status_ok: bool = True
    status_ms: int = 0

    # Laid out by `draw`, read by `handle_event`. Cleared every frame, so a
    # control this frame did not draw simply is not here to be clicked — the
    # strongest form of "guard every rect test", with no zeroed rect to leak.
    rects: dict[str, pygame.Rect] = field(default_factory=dict)
    problem_rows: list[tuple[int, pygame.Rect]] = field(default_factory=list)

    def problems(self) -> list[Problem]:
        return self.recipe.problems()

    def can_play(self) -> bool:
        return self.recipe.is_playable()


# --------------------------------------------------------------------------- #
# Opening / committing
# --------------------------------------------------------------------------- #
def open_editor(settings: Settings, seed: Optional[int] = None) -> Editor:
    """The editor for ``settings`` — its existing hand map, or a generated one.

    Never a blank canvas on entry. A blank map has no seats and no lanes, so it
    fails the Play gate on two counts at once, and a first impression of "nothing
    you can do here works yet" is a poor one; the footer's *New* is one press away
    for anyone who wants to start from nothing.
    """
    # The lane readouts and lane styling go through `config.travel_turns_at_length`,
    # so the menu's ship-speed knob has to have reached `config` or the editor
    # quotes the previous game's travel times. `_generated` does this on the other
    # branch anyway; doing it here covers both.
    settings_mod.apply_globals(settings)
    recipe = settings.custom_map.copy() if settings.custom_map is not None else _generated(settings, seed)
    return Editor(recipe=recipe, view=_build_view())


def _generated(settings: Settings, seed: Optional[int] = None) -> CustomMap:
    """A recipe from a freshly generated map, honouring the menu's own knobs.

    Goes through ``settings.apply_globals`` rather than ``mapgen.generate``
    directly, because generation reads the map knobs live off ``config`` and that
    is the single sanctioned writer into it.
    """
    settings_mod.apply_globals(settings)
    concrete = settings_mod.resolve_seed(settings) if seed is None else seed
    state = mapgen.generate(concrete, settings.mode, settings.nodes, settings.players)
    return custommap.from_state(state)


def commit(ed: Editor, settings: Settings) -> None:
    """Write the drawn map onto ``settings``, keeping its three fields in sync.

    ``players`` and ``nodes`` are not decoration once a recipe is present: the AI
    tab lists seats ``2..players``, ``challenge_keys`` blanks seats past it, and
    the leaderboard's decoder requires both. ``from_dict`` reconciles them on the
    way back in, so letting them drift here would move the digest in between.

    Runs even for a map that fails the Play gate — a half-built map must survive a
    trip to the menu to change a setting. Only *starting a game* is gated.
    """
    recipe = ed.recipe.normalised()
    settings.custom_map = recipe
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, recipe.seats()))
    settings.nodes = max(1, len(recipe.nodes))


def reflow(ed: Editor) -> None:
    """Rebuild the camera after a window resize — the measured layout reflows on
    its own, but ``WorldView`` takes its screen rect at construction.

    The zoom level is carried over (a resize mid-edit should not throw away how
    far in you were), through ``zoom_at`` rather than by assignment: the fresh
    view sits at 1.0, so multiplying by the old zoom lands exactly on it *and*
    re-clamps for the new viewport. Writing ``.zoom`` directly skips the clamp,
    and ``reset()`` would discard the zoom it was meant to keep.
    """
    zoom = ed.view.zoom
    ed.view = _build_view()
    if zoom != 1.0:
        ed.view.zoom_at(_view_centre(), zoom)


def _build_view() -> WorldView:
    """The camera over the *full* world box, fixed for the editor's lifetime.

    Fixed rather than fitted to the nodes: ``WorldView`` takes its bounds at
    construction and never re-derives them, so a box that grew as systems were
    placed would make the map jump under the cursor — and ``mapgen.map_bounds``
    raises on an empty map anyway. Placement is allowed anywhere in the box, not
    just inside ``WORLD_MARGIN``: in play, ``main.build_view`` fits to the actual
    node bounding box, so a map drawn to the edges simply fills the viewport more
    tightly.
    """
    return WorldView(
        (0.0, 0.0, config.WORLD_SIZE, config.WORLD_SIZE),
        _view_rect(),
        padding=config.map_fit_padding(),
        pan_padding=config.map_pan_padding(),
    )


# --------------------------------------------------------------------------- #
# Layout — measured, and never dependent on the tool mode
# --------------------------------------------------------------------------- #
def _palette_h() -> int:
    """Height of the bottom swatch band: a full-size node, its numeral row and
    the caption under it, with the bar's own padding at each end.

    Measured rather than fixed, and deliberately independent of the tool mode —
    a band that resized as tools switched would move the viewport under the
    cursor and invalidate the camera with it.
    """
    return max(
        config.EDIT_PALETTE_MIN_H,
        2 * config.NODE_MAX_RADIUS + widgets.row_h("small") + 2 * config.HUD_PAD,
    )


def _footer_h() -> int:
    return widgets.tap_size(config.FOOTER_BTN_H) + 2 * config.HUD_PAD


def _view_rect() -> tuple[int, int, int, int]:
    """The map viewport. Derived here rather than from ``config.play_rect()``,
    which reserves ``HUD_RIGHT_W`` and both HUD bars for a HUD this scene has
    none of."""
    top = config.EDIT_TOP_H
    return (
        0,
        top,
        max(1, config.SCREEN_W - config.EDIT_SIDE_W),
        max(1, config.SCREEN_H - top - _palette_h() - _footer_h()),
    )


def _side_rect() -> pygame.Rect:
    return pygame.Rect(
        config.SCREEN_W - config.EDIT_SIDE_W,
        config.EDIT_TOP_H,
        config.EDIT_SIDE_W,
        config.SCREEN_H - config.EDIT_TOP_H,
    )


def _palette_rect() -> pygame.Rect:
    return pygame.Rect(
        0,
        config.SCREEN_H - _footer_h() - _palette_h(),
        config.SCREEN_W - config.EDIT_SIDE_W,
        _palette_h(),
    )


def _footer_rect() -> pygame.Rect:
    return pygame.Rect(
        0,
        config.SCREEN_H - _footer_h(),
        config.SCREEN_W - config.EDIT_SIDE_W,
        _footer_h(),
    )


def _over_map(pos) -> bool:
    vx, vy, vw, vh = _view_rect()
    return vx <= pos[0] < vx + vw and vy <= pos[1] < vy + vh


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def draw(surface, ed: Editor, settings: Settings) -> None:
    """Render the whole scene. Reads only; every rect it lays out lands in
    ``ed.rects`` for ``handle_event`` to hit-test against."""
    ed.rects.clear()
    ed.problem_rows.clear()
    surface.fill(config.COLOR_BG)

    _draw_map(surface, ed)
    _draw_zoom_controls(surface, ed)
    _draw_top(surface, ed)
    _draw_palette(surface, ed)
    _draw_side(surface, ed, settings)
    _draw_footer(surface, ed)
    if ed.confirm is not None:
        _draw_confirm(surface, ed)


def _draw_map(surface, ed: Editor) -> None:
    view_rect = _view_rect()
    prev = surface.get_clip()
    surface.set_clip(pygame.Rect(view_rect))

    pos = [ed.view.to_screen(n.pos) for n in ed.recipe.nodes]
    for li, (a, b) in enumerate(ed.recipe.lanes):
        turns = _lane_turns(ed.recipe.nodes[a], ed.recipe.nodes[b])
        width, color = widgets.lane_style(turns)
        if li in ed.bad_lanes:
            width, color = width + config.s(1), _WARN
        elif li == ed.sel_lane:
            width, color = width + config.s(2), config.COLOR_SELECT
        pygame.draw.line(surface, color, pos[a], pos[b], width)

    font = widgets.fonts()["small"]
    for i, node in enumerate(ed.recipe.nodes):
        r = config.node_radius(node.production)
        color = config.player_color(node.owner)
        pygame.draw.circle(surface, color, pos[i], r)
        pygame.draw.circle(surface, widgets.brighten(color, 40), pos[i], r, config.s(2))
        widgets.text(surface, font, str(node.ships), config.text_on(color), center=pos[i])
        if i in ed.bad_nodes:
            pygame.draw.circle(surface, _WARN, pos[i], r + config.NODE_RING_PAD, config.s(2))
        elif i == ed.lane_src:
            pygame.draw.circle(surface, _LANE_ARM, pos[i], r + config.NODE_RING_PAD, config.s(2))
        elif i == ed.sel_node:
            pygame.draw.circle(surface, config.COLOR_SELECT, pos[i], r + config.NODE_RING_PAD, config.s(2))
        label = f"#{i}"
        widgets.text(surface, font, label, config.COLOR_TEXT_DIM,
                     center=(pos[i][0], pos[i][1] + r + widgets.row_h("small") // 2 + config.s(2)))

    if ed.lane_drag and ed.lane_src is not None and ed.lane_src < len(pos):
        # Rubber band toward the cursor, with a ring on a system it could land on
        # — `render._draw_drag` is the model.
        pygame.draw.line(surface, _LANE_ARM, pos[ed.lane_src], ed.lane_pos, max(2, config.s(3)))
        pygame.draw.circle(surface, _LANE_ARM, ed.lane_pos, max(3, config.s(5)), config.s(2))
        target = _pick_node(ed, ed.lane_pos)
        if target is not None and target != ed.lane_src:
            r = config.node_radius(ed.recipe.nodes[target].production) + config.s(4)
            pygame.draw.circle(surface, config.COLOR_SELECT, pos[target], r, max(2, config.s(2)))

    surface.set_clip(prev)


def _lane_turns(a: MapNode, b: MapNode) -> int:
    """Travel time for a lane between two recipe nodes, on turn 0.

    Through ``config.travel_turns_at_length`` — the same call ``GameState.
    travel_turns`` makes — so the number authored against is the number the built
    map gives, including the menu's ship-speed slider.
    """
    return config.travel_turns_at_length(_lane_length(a, b), 0)


def _draw_zoom_controls(surface, ed: Editor) -> None:
    """The on-map camera cluster, anchored bottom-right of the viewport — the
    touch/no-wheel equivalent of scroll-to-zoom, matching ``render``'s."""
    vx, vy, vw, vh = _view_rect()
    size = config.MAP_ZOOM_BTN_SIZE
    gap, inset = config.s(8), config.s(12)
    y = vy + vh - inset - size
    font = widgets.fonts()["small"]
    accent = widgets.BTN_BLUE[1]

    plus = pygame.Rect(vx + vw - inset - size, y, size, size)
    minus = pygame.Rect(plus.x - gap - size, y, size, size)
    label = widgets.key_hint("Reset", "R")
    reset = pygame.Rect(minus.x - gap - widgets.btn_w(font, label, size), y,
                        widgets.btn_w(font, label, size), size)

    widgets.btn(surface, reset, label, *widgets.BTN_BLUE, font)
    widgets.step_button(surface, minus, "-", accent)
    widgets.step_button(surface, plus, "+", accent)
    ed.rects["zoom_reset"] = reset
    ed.rects["zoom_out"] = minus
    ed.rects["zoom_in"] = plus


def _draw_top(surface, ed: Editor) -> None:
    bar = pygame.Rect(0, 0, config.SCREEN_W, config.EDIT_TOP_H)
    pygame.draw.rect(surface, _PANEL_BG, bar)
    pygame.draw.line(surface, _PANEL_EDGE, (0, bar.bottom - 1), (bar.right, bar.bottom - 1))

    font = widgets.fonts()["small"]
    x = config.HUD_PAD
    h = widgets.tap_size(config.EDIT_TOP_H - config.s(12))
    y = (config.EDIT_TOP_H - h) // 2
    for key, label in _TOOLS:
        ready = key in _READY_TOOLS
        w = widgets.btn_w(font, label)
        rect = pygame.Rect(x, y, w, h)
        if not ready:
            # Drawn so the strip's shape is final, but recording no rect: an inert
            # control is better made inert by construction than by a disabled flag
            # some later branch forgets to check.
            widgets.btn(surface, rect, label, (30, 32, 44), (56, 60, 78), font, config.COLOR_TEXT_DIM)
        else:
            palette = widgets.BTN_ACTIVE if ed.tool == key else widgets.BTN_BLUE
            widgets.btn(surface, rect, label, *palette, font)
            ed.rects[f"tool_{key}"] = rect
        x = rect.right + config.BTN_GAP

    if ed.status:
        color = config.COLOR_TEXT if ed.status_ok else _BAD
        widgets.text(surface, font, ed.status, color,
                     midright=(config.SCREEN_W - config.HUD_PAD, config.EDIT_TOP_H // 2))


_TOOL_HINTS = {
    LANES: ("Drag from one system to another to lay a lane —",
            "or tap one, then tap the other. Tap a lane to select it."),
}


def _draw_palette(surface, ed: Editor) -> None:
    """The bottom band: production swatches in the Systems tool, a hint in tools
    that have no palette. Its *height* never varies (see ``_palette_h``) — only
    what is drawn in it."""
    band = _palette_rect()
    pygame.draw.rect(surface, _PANEL_BG, band)
    pygame.draw.line(surface, _PANEL_EDGE, (0, band.top), (band.right, band.top))

    font = widgets.fonts()["small"]
    if ed.tool in _TOOL_HINTS:
        y = band.centery - widgets.row_h("small") // 2
        for line in _TOOL_HINTS[ed.tool]:
            widgets.text(surface, font, line, config.COLOR_TEXT_DIM, center=(band.centerx, y))
            y += widgets.row_h("small")
        return

    entries: list[tuple[object, str, int]] = [(PALETTE_RANDOM, "Random (2-5)", config.HOME_PRODUCTION)]
    entries += [(p, f"{p}/ship", p) for p in _PALETTE_VALUES]

    swatch = widgets.tap_size(2 * config.NODE_MAX_RADIUS + config.s(8))
    wanted = max(swatch, max(font.size(caption)[0] for _v, caption, _p in entries) + config.s(8))
    gaps = config.BTN_GAP * (len(entries) - 1)
    # The row has to fit the band whatever the UI scale, so the cell is capped at
    # its share of the room. When that cap bites, the captions are what go: the
    # numeral inside each swatch already tells the values apart, which is the same
    # reasoning that lets 1 and 2 draw at the same radius.
    room = max(1, (band.w - 2 * config.HUD_PAD - gaps) // len(entries))
    cell = min(wanted, room)
    captioned = cell >= wanted

    total = cell * len(entries) + gaps
    x = band.x + max(config.HUD_PAD, (band.w - total) // 2)
    cy = band.y + config.HUD_PAD + config.NODE_MAX_RADIUS

    for value, caption, shown_production in entries:
        rect = pygame.Rect(x, band.y + config.HUD_PAD, cell, band.h - 2 * config.HUD_PAD)
        r = config.node_radius(shown_production)
        centre = (rect.centerx, cy)
        fill = config.COLOR_NEUTRAL
        pygame.draw.circle(surface, fill, centre, r)
        pygame.draw.circle(surface, widgets.brighten(fill, 40), centre, r, config.s(2))
        glyph = "?" if value is PALETTE_RANDOM else str(value)
        widgets.text(surface, font, glyph, config.text_on(fill), center=centre)
        if ed.pick == value:
            pygame.draw.circle(surface, config.COLOR_SELECT, centre, r + config.NODE_RING_PAD, config.s(2))
        if captioned:
            widgets.text(surface, font, caption, config.COLOR_TEXT_DIM,
                         center=(rect.centerx, cy + config.NODE_MAX_RADIUS + widgets.row_h("small") // 2))
        ed.rects[f"pal_{value}"] = rect
        x = rect.right + config.BTN_GAP


def _draw_side(surface, ed: Editor, settings: Settings) -> None:
    panel = _side_rect()
    pygame.draw.rect(surface, _PANEL_BG, panel)
    pygame.draw.line(surface, _PANEL_EDGE, (panel.x, panel.y), (panel.x, panel.bottom))

    f = widgets.fonts()
    pad = config.PANEL_PAD
    x = panel.x + pad
    inner_w = panel.w - 2 * pad
    y = panel.y + pad

    if ed.tool == LANES:
        y = _draw_lane_selection(surface, ed, f, x, y, inner_w)
        y = _draw_lane_controls(surface, ed, settings, f, x, y, inner_w)
    else:
        y = _draw_selection(surface, ed, f, x, y, inner_w)
        y = _draw_sliders(surface, ed, settings, f, x, y, inner_w,
                          "New system rolls", econ_specs())
    _draw_problems(surface, ed, f, x, y, inner_w, panel.bottom - pad)


def _draw_selection(surface, ed: Editor, f, x: int, y: int, w: int) -> int:
    node = _selected(ed)
    row = widgets.row_h()
    if node is None:
        widgets.text(surface, f["small"], "Click empty space to place a system",
                     config.COLOR_TEXT_DIM, topleft=(x, y))
        return y + row + config.ROW_GAP

    widgets.text(surface, f["normal"], f"System #{ed.sel_node}", config.COLOR_TEXT, topleft=(x, y))
    y += widgets.row_h("normal")

    y = _stepper_row(surface, ed, f, x, y, w, "prod", "Production", f"{node.production}/ship")
    y = _stepper_row(surface, ed, f, x, y, w, "ships", "Garrison", str(node.ships))
    owner = "Neutral" if node.owner == 0 else config.player_name(node.owner)
    y = _stepper_row(surface, ed, f, x, y, w, "owner", "Owner", owner,
                     swatch=config.player_color(node.owner))

    bh = widgets.tap_size(config.FOOTER_BTN_H)
    bw = (w - config.BTN_GAP) // 2
    reroll = pygame.Rect(x, y, bw, bh)
    delete = pygame.Rect(x + bw + config.BTN_GAP, y, w - bw - config.BTN_GAP, bh)
    ed.rects["reroll"] = _btn_rect(surface, reroll, "Reroll", widgets.BTN_BLUE, f["small"])
    ed.rects["delete"] = _btn_rect(surface, delete, "Delete", widgets.BTN_RED, f["small"])
    return y + bh + config.ROW_GAP * 2


def _draw_lane_selection(surface, ed: Editor, f, x: int, y: int, w: int) -> int:
    """The selected lane: how far it runs, how long it takes, and Delete."""
    lane = _selected_lane(ed)
    if lane is None:
        widgets.text(surface, f["small"], "Tap a lane to select it",
                     config.COLOR_TEXT_DIM, topleft=(x, y))
        return y + widgets.row_h() + config.ROW_GAP

    a, b = lane
    na, nb = ed.recipe.nodes[a], ed.recipe.nodes[b]
    length = _lane_length(na, nb)
    turns = _lane_turns(na, nb)

    widgets.text(surface, f["normal"], f"Lane #{a} - #{b}", config.COLOR_TEXT, topleft=(x, y))
    y += widgets.row_h("normal")
    for label, value in (("Length", f"{length:g} ly"),
                         ("Travel", f"{turns} turn" + ("s" if turns != 1 else ""))):
        widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM, topleft=(x, y))
        widgets.text(surface, f["small"], value, config.COLOR_TEXT,
                     midright=(x + w, y + widgets.row_h("small") // 2))
        y += widgets.row_h("small")
    if config.SHIP_SPEED_GROWTH_PCT > 0:
        # The figure above is turn 0's, which is the one worth authoring against —
        # but with growth on it is a ceiling, not the whole story.
        for line in widgets.wrap(f["small"], "Ships speed up each turn, so this is turn 0.", w):
            widgets.text(surface, f["small"], line, _WARN, topleft=(x, y))
            y += widgets.row_h("small")

    y += config.ROW_GAP
    bh = widgets.tap_size(config.FOOTER_BTN_H)
    ed.rects["delete_lane"] = _btn_rect(
        surface, pygame.Rect(x, y, w, bh), "Delete lane", widgets.BTN_RED, f["small"])
    return y + bh + config.ROW_GAP * 2


def _draw_lane_controls(surface, ed: Editor, settings: Settings, f, x: int, y: int, w: int) -> int:
    """The two lane-generation knobs, the Planar toggle, and the two map-wide
    lane actions."""
    y = _draw_sliders(surface, ed, settings, f, x, y, w, "Auto-lanes", lane_specs())

    box = widgets.tap_size(config.STEPPER_SIZE)
    row = max(box, widgets.row_h())
    rect = pygame.Rect(x, y, w, row)
    mark = pygame.Rect(x, y + (row - box) // 2, box, box)
    accent = widgets.BTN_BLUE[1]
    pygame.draw.rect(surface, config.COLOR_BG, mark, border_radius=config.s(4))
    pygame.draw.rect(surface, accent, mark, config.s(1), border_radius=config.s(4))
    if ed.planar:
        inner = mark.inflate(-box // 2, -box // 2)
        pygame.draw.rect(surface, accent, inner, border_radius=config.s(2))
    widgets.text(surface, f["small"], "Planar (refuse crossings)", config.COLOR_TEXT_DIM,
                 midleft=(mark.right + config.BTN_GAP, y + row // 2))
    ed.rects["planar"] = rect
    y += row + config.ROW_GAP

    bh = widgets.tap_size(config.FOOTER_BTN_H)
    bw = (w - config.BTN_GAP) // 2
    ed.rects["auto_lanes"] = _btn_rect(
        surface, pygame.Rect(x, y, bw, bh), "Auto", widgets.BTN_VIOLET, f["small"])
    ed.rects["clear_lanes"] = _btn_rect(
        surface, pygame.Rect(x + bw + config.BTN_GAP, y, w - bw - config.BTN_GAP, bh),
        "Clear all", widgets.BTN_AMBER, f["small"])
    return y + bh + config.ROW_GAP * 2


def _btn_rect(surface, rect, label, palette, font) -> pygame.Rect:
    """``widgets.btn`` returning a Rect rather than a tuple, since ``ed.rects``
    holds Rects — ``collidepoint`` is what every hit test here wants."""
    widgets.btn(surface, rect, label, *palette, font)
    return rect


def _stepper_row(surface, ed: Editor, f, x: int, y: int, w: int,
                 key: str, label: str, value: str, swatch=None) -> int:
    """One "label  −  value  +" row, recording both step rects."""
    size = widgets.tap_size(config.STEPPER_SIZE)
    row = max(size, widgets.row_h())
    minus = pygame.Rect(x + w - 2 * size - config.BTN_GAP, y + (row - size) // 2, size, size)
    plus = pygame.Rect(x + w - size, y + (row - size) // 2, size, size)
    accent = widgets.BTN_BLUE[1]

    widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM, midleft=(x, y + row // 2))
    vx = minus.x - config.BTN_GAP
    if swatch is not None:
        dot = config.s(6)
        pygame.draw.circle(surface, swatch, (vx - dot, y + row // 2), dot)
        vx -= 2 * dot + config.s(4)
    widgets.text(surface, f["small"], value, config.COLOR_TEXT, midright=(vx, y + row // 2))

    widgets.step_button(surface, minus, "-", accent)
    widgets.step_button(surface, plus, "+", accent)
    ed.rects[f"{key}_minus"] = minus
    ed.rects[f"{key}_plus"] = plus
    return y + row + config.ROW_GAP


def _draw_sliders(surface, ed: Editor, settings: Settings, f, x: int, y: int, w: int,
                  heading: str, specs) -> int:
    """A titled block of ``Settings``-writing sliders.

    Both groups the creator owns are drawn by this: the Economy knobs, which
    govern what the *next* placement rolls, and the two lane knobs Auto-lanes
    reads. Both moved here from the menu's Advanced tab because on a custom map
    they only bite at the moment the creator uses them.
    """
    widgets.text(surface, f["small"], heading, config.COLOR_TEXT_DIM, topleft=(x, y))
    y += widgets.row_h("small") + config.ROW_GAP

    for key, label, attr, lo, hi, _step, is_int in specs:
        value = getattr(settings, attr)
        shown = f"{int(value)}" if is_int else f"{value:g}"
        widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM, topleft=(x, y))
        widgets.text(surface, f["small"], shown, config.COLOR_TEXT,
                     midright=(x + w, y + widgets.row_h("small") // 2))
        y += widgets.row_h("small")
        track = pygame.Rect(x, y, w, widgets.tap_size(config.SLIDER_KNOB_R * 2))
        t = 0.0 if hi == lo else (value - lo) / (hi - lo)
        widgets.slider(surface, track, t, *widgets.BTN_BLUE)
        ed.rects[key] = track
        y += track.h + config.ROW_GAP
    return y + config.ROW_GAP


def lane_specs():
    """The two lane-generation knobs, from the menu's own list (see
    ``econ_specs`` for why it is imported lazily)."""
    from .menu import lane_sliders
    return lane_sliders()


def econ_specs():
    """The Economy slider specs, taken from the menu's own list so the two cannot
    drift — the sliders moved scene, not namespace, and they still write
    ``Settings``.

    Imported lazily inside the call: ``menu`` imports ``ai`` for strategy
    discovery, and the editor has no reason to pull that in at import time.
    """
    from .menu import econ_sliders
    return econ_sliders()


def _draw_problems(surface, ed: Editor, f, x: int, y: int, w: int, bottom: int) -> None:
    """The live validator's output, one clickable row each.

    Clickable because a warning you cannot find is a warning you ignore: a press
    selects the offender and centres the camera on it.
    """
    problems = ed.problems()
    heading = "Ready to play" if not problems else f"{len(problems)} problem(s)"
    widgets.text(surface, f["small"], heading,
                 config.COLOR_TEXT_DIM if not problems else _WARN, topleft=(x, y))
    y += widgets.row_h("small") + config.ROW_GAP

    for index, problem in enumerate(problems):
        lines = widgets.wrap(f["small"], problem.text, w)
        block_h = widgets.row_h("small") * len(lines)
        if y + block_h > bottom:
            widgets.text(surface, f["small"], "...", config.COLOR_TEXT_DIM, topleft=(x, y))
            return
        rect = pygame.Rect(x, y, w, block_h)
        color = _BAD if problem.blocks else _WARN
        for line in lines:
            widgets.text(surface, f["small"], line, color, topleft=(x, y))
            y += widgets.row_h("small")
        ed.problem_rows.append((index, rect))
        y += config.ROW_GAP


_DEAD_PALETTE = ((38, 42, 52), (78, 84, 98))


# (key, label, fill, edge, squeeze rank, cluster). The filename field carries no
# label of its own but takes part in the same measure-and-squeeze pass, so a
# narrow window sheds it in rank order like everything else.
def _footer_specs(ed: Editor) -> list[tuple[str, str, tuple, tuple, int, str]]:
    return [
        ("back", widgets.key_hint("Menu", "Esc"), *widgets.BTN_BLUE, 90, "left"),
        ("undo", "Undo", *widgets.BTN_BLUE, 70, "left"),
        ("redo", "Redo", *widgets.BTN_BLUE, 65, "left"),
        ("new", "New", *widgets.BTN_AMBER, 40, "left"),
        ("generate", "Generate", *widgets.BTN_AMBER, 35, "left"),
        ("filename", "", (0, 0, 0), (0, 0, 0), 45, "right"),
        ("open", "Open", *widgets.BTN_BLUE, 50, "right"),
        ("save", "Save", *widgets.BTN_BLUE, 55, "right"),
        ("play", "Play", *(widgets.BTN_GREEN if ed.can_play() else _DEAD_PALETTE), 100, "right"),
    ]


def _draw_footer(surface, ed: Editor) -> None:
    """Measure, squeeze and draw the footer strip.

    Same drop-lowest-rank-while-overflowing loop ``render._lay_out_footer`` uses,
    so a narrow window degrades by shedding the least essential control rather
    than by overlapping labels. The filename field is measured like a button so it
    takes part in the same squeeze.
    """
    bar = _footer_rect()
    pygame.draw.rect(surface, _PANEL_BG, bar)
    pygame.draw.line(surface, _PANEL_EDGE, (0, bar.top), (bar.right, bar.top))

    font = widgets.fonts()["small"]
    specs = _footer_specs(ed)
    widths = {s[0]: widgets.btn_w(font, s[1]) for s in specs}
    widths["filename"] = widgets.btn_w(font, "M" * 10)   # a field is sized, not labelled

    avail = bar.w - 2 * config.HUD_PAD

    def row_w(items) -> int:
        return sum(widths[s[0]] for s in items) + config.BTN_GAP * (len(items) - 1)

    shown = list(specs)
    while shown and row_w(shown) > avail:
        shown.remove(min(shown, key=lambda s: s[4]))

    bh = widgets.tap_size(config.FOOTER_BTN_H)
    y = bar.y + (bar.h - bh) // 2

    x = bar.x + config.HUD_PAD
    for key, label, fill, edge, _rank, group in shown:
        if group != "left":
            continue
        rect = pygame.Rect(x, y, widths[key], bh)
        widgets.btn(surface, rect, label, fill, edge, font)
        ed.rects[key] = rect
        x = rect.right + config.BTN_GAP

    right = bar.right - config.HUD_PAD
    for key, label, fill, edge, _rank, group in reversed(shown):
        if group != "right":
            continue
        rect = pygame.Rect(right - widths[key], y, widths[key], bh)
        if key == "filename":
            _text_field(surface, font, rect, ed.filename or _DEFAULT_FILENAME, ed.editing_filename)
        else:
            widgets.btn(surface, rect, label, fill, edge, font)
        ed.rects[key] = rect
        right = rect.x - config.BTN_GAP


def _text_field(surface, font, rect: pygame.Rect, text: str, editing: bool) -> None:
    """A text box with a caret while ``editing``, clipped and end-anchored so a
    long name runs off the left rather than over the buttons beside it — the same
    behaviour as the menu's field."""
    pygame.draw.rect(surface, (14, 16, 24), rect, border_radius=config.s(6))
    pygame.draw.rect(surface, widgets.BTN_ACTIVE[1] if editing else widgets.BTN_BLUE[1],
                     rect, config.s(2), border_radius=config.s(6))
    img = font.render(text + ("|" if editing else ""), True, config.COLOR_TEXT)
    pad = config.s(8)
    inner = rect.inflate(-2 * pad, -config.s(4))
    prev = surface.get_clip()
    surface.set_clip(inner)
    surface.blit(img, (min(inner.x, inner.right - img.get_width()),
                       inner.centery - img.get_height() // 2))
    surface.set_clip(prev)


_CONFIRMS = {
    "auto_lanes": ("Replace every lane?",
                   "Auto-lanes rebuilds the whole network, so any bottleneck you "
                   "drew by hand goes with it."),
    "clear_lanes": ("Remove every lane?",
                    "The systems stay where they are; only the network goes."),
    "new": ("Start from a blank canvas?", "The map you have drawn will be discarded."),
}


def _draw_confirm(surface, ed: Editor) -> None:
    title, detail = _CONFIRMS[ed.confirm or "new"]
    yes_label, no_label = widgets.confirm_labels(
        {"auto_lanes": "Replace", "clear_lanes": "Remove"}.get(ed.confirm or "", "Discard"))
    widgets.draw_modal(surface, title, detail,
                       ((yes_label, *widgets.BTN_DANGER), (no_label, *widgets.BTN_BLUE)))
    yes, no = widgets.modal_buttons(surface, (yes_label, no_label))
    ed.rects["confirm_yes"], ed.rects["confirm_no"] = yes, no


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def handle_event(event, ed: Editor, settings: Settings) -> Optional[str]:
    """Mutate ``ed`` (and, for the sliders, ``settings``); return a high-level
    action for ``main`` — ``"play"``, ``"menu"``, or ``None``."""
    if ed.confirm is not None:
        _handle_confirm(event, ed, settings)
        return None

    if event.type == pygame.TEXTINPUT:
        _handle_text_input(event.text, ed)
        return None
    if event.type == pygame.KEYDOWN:
        return _handle_key(event, ed, settings)
    if event.type == pygame.MOUSEWHEEL:
        pos = pygame.mouse.get_pos()
        if _over_map(pos):
            ed.view.zoom_at(pos, config.ZOOM_WHEEL_STEP ** event.y)
        return None
    if event.type == pygame.MOUSEBUTTONDOWN:
        return _handle_press(event, ed, settings)
    if event.type == pygame.MOUSEMOTION:
        _handle_motion(event, ed, settings)
        return None
    if event.type == pygame.MOUSEBUTTONUP:
        _handle_release(event, ed)
        return None
    return None


def _handle_press(event, ed: Editor, settings: Settings) -> Optional[str]:
    pos = event.pos
    if event.button == 3:
        # Right-drag pans. Left-drag cannot: in the Systems tool a press on empty
        # space always means "place", so there is no free left gesture — and
        # making it conditional on legality would give one press two meanings,
        # the trap route mode's tap already documents.
        if _over_map(pos):
            ed.pan_active, ed.pan_last = True, pos
        return None
    if event.button != 1:
        return None

    # Chrome is tested first and everywhere, because some of it — the on-map zoom
    # cluster — sits *inside* the viewport. Testing the map first would place a
    # system under the + button.
    hit = _hit(ed, pos)
    if hit is not None:
        return _handle_chrome(hit, pos, ed, settings)
    if not _over_map(pos):
        _stop_editing_filename(ed)
        for index, rect in ed.problem_rows:
            if rect.collidepoint(pos):
                _focus_problem(ed, index)
                return None
        return None

    _stop_editing_filename(ed)
    if ed.tool == LANES:
        _press_lane(ed, pos)
        return None

    node = _pick_node(ed, pos)
    if node is not None:
        _arm_move(ed, node, pos)
        return None
    _place(ed, settings, pos)
    return None


def _press_lane(ed: Editor, pos) -> None:
    """A press on the map in the Lanes tool.

    Systems are tested before lanes: a lane's endpoint sits inside its system's
    tap reach, and "start a lane here" has to win there. One armed source serves
    both gestures — a tap leaves it armed so the next press commits, a drag
    commits on release.
    """
    node = _pick_node(ed, pos)
    if node is not None:
        ed.sel_lane = None
        if ed.lane_src is None:
            ed.lane_src, ed.lane_drag, ed.lane_pos = node, False, pos
            ed.drag_start = pos
        elif ed.lane_src == node:
            ed.lane_src = None              # tapping the armed system again disarms
        else:
            _add_lane(ed, ed.lane_src, node)
            ed.lane_src = None
        return

    ed.lane_src = None
    ed.sel_lane = _pick_lane(ed, pos)
    if ed.sel_lane is None:
        # Nothing under the press at all, so left-drag is free here — unlike the
        # Systems tool, where it always means "place".
        ed.pan_active, ed.pan_last, ed.pan_button = True, pos, 1


def _hit(ed: Editor, pos) -> Optional[str]:
    """The chrome control under ``pos``, if any.

    The single hit-tester, and the single place the "guard every rect test on its
    width" rule is enforced — a zero-sized rect can never be inside anything here,
    however it got recorded.
    """
    return next((key for key, rect in ed.rects.items()
                 if rect.w > 0 and rect.h > 0 and rect.collidepoint(pos)), None)


def _arm_move(ed: Editor, node: int, pos) -> None:
    """Select a system and arm a move-drag on it. No undo snapshot yet: a tap that
    only selects is not an edit, and pushing one here would clear the redo stack
    every time you looked at a system."""
    ed.sel_node = node
    ed.drag_node = node
    ed.drag_origin = (ed.recipe.nodes[node].x, ed.recipe.nodes[node].y)
    ed.drag_start = pos
    ed.drag_moved = False
    ed.drag_undone = False


def _handle_chrome(hit: str, pos, ed: Editor, settings: Settings) -> Optional[str]:
    """A press on a laid-out control: the toolbar, palette, sidebar or footer."""
    if hit != "filename":
        _stop_editing_filename(ed)

    if hit.startswith("pal_"):
        value = hit[4:]
        ed.pick = PALETTE_RANDOM if value == PALETTE_RANDOM else int(value)
        _retype_selection(ed, settings)
        return None
    if hit.startswith("tool_"):
        ed.tool = hit[5:]
        return None
    if hit in _SLIDER_KEYS():
        ed.drag_slider = hit
        _set_slider(ed, settings, hit, pos[0])
        return None

    return _handle_action(hit, ed, settings)


def _handle_action(hit: str, ed: Editor, settings: Settings) -> Optional[str]:
    if hit == "zoom_in":
        ed.view.zoom_at(_view_centre(), config.ZOOM_BUTTON_STEP)
    elif hit == "zoom_out":
        ed.view.zoom_at(_view_centre(), 1 / config.ZOOM_BUTTON_STEP)
    elif hit == "zoom_reset":
        ed.view.reset()
    elif hit in ("prod_minus", "prod_plus"):
        _step_selected(ed, "production", 1 if hit.endswith("plus") else -1)
    elif hit in ("ships_minus", "ships_plus"):
        _step_selected(ed, "ships", 1 if hit.endswith("plus") else -1)
    elif hit in ("owner_minus", "owner_plus"):
        _step_selected(ed, "owner", 1 if hit.endswith("plus") else -1)
    elif hit == "reroll":
        _reroll_selected(ed, settings)
    elif hit == "delete":
        _delete_selected(ed)
    elif hit == "delete_lane":
        _delete_lane(ed)
    elif hit == "planar":
        ed.planar = not ed.planar
    elif hit == "clear_lanes":
        ed.confirm = "clear_lanes"
    elif hit == "undo":
        _undo(ed)
    elif hit == "redo":
        _redo(ed)
    elif hit == "auto_lanes":
        ed.confirm = "auto_lanes"
    elif hit == "new":
        ed.confirm = "new"
    elif hit == "generate":
        _push_undo(ed)
        _adopt(ed, _generated(settings))
        _set_status(ed, "Generated a fresh map to edit", True)
    elif hit == "open":
        _open_file(ed)
    elif hit == "save":
        _save_file(ed)
    elif hit == "filename":
        ed.editing_filename = True
        softkeyboard.open(ed.filename)
        _start_text_input()
    elif hit == "play":
        return _play(ed, settings)
    elif hit == "back":
        commit(ed, settings)
        return "menu"
    return None


def _play(ed: Editor, settings: Settings) -> Optional[str]:
    """Commit and start, or refuse with the first blocker said out loud."""
    blockers = ed.recipe.blockers()
    if blockers:
        _focus_problem(ed, 0)
        _set_status(ed, blockers[0].text, False)
        return None
    commit(ed, settings)
    return "play"


def _handle_motion(event, ed: Editor, settings: Settings) -> None:
    if ed.drag_slider is not None and event.buttons[0]:
        _set_slider(ed, settings, ed.drag_slider, event.pos[0])
        return
    if ed.pan_active and event.buttons[ed.pan_button - 1]:
        ed.view.pan(event.pos[0] - ed.pan_last[0], event.pos[1] - ed.pan_last[1])
        ed.pan_last = event.pos
        return
    if ed.lane_src is not None and event.buttons[0]:
        if dist(event.pos, ed.drag_start) > config.DRAG_THRESHOLD:
            ed.lane_drag = True
        ed.lane_pos = event.pos
        return
    if ed.drag_node is None or not event.buttons[0]:
        return
    # Measured from where the press landed, never from the node: once the node is
    # following the cursor the two coincide, so a threshold against the node's own
    # position would never fire again after the first pixel.
    if not ed.drag_moved and dist(event.pos, ed.drag_start) <= config.DRAG_THRESHOLD:
        return
    if not ed.drag_moved:
        ed.drag_moved = True
        if not ed.drag_undone:
            _push_undo(ed)          # the first real movement is the edit
            ed.drag_undone = True
    # The node follows the cursor the whole way, legal or not: rubber-banding it
    # away mid-drag reads as the drag being broken. The refusal shows as amber
    # rings on the offenders, and is paid on release.
    _move_node(ed, ed.drag_node, event.pos)
    _refresh_bad(ed, ed.drag_node)


def _handle_release(event, ed: Editor) -> None:
    if event.button == 3:
        ed.pan_active = False
        return
    if event.button != 1:
        return
    ed.pan_active = False
    ed.drag_slider = None
    if ed.lane_drag:
        # A drag that landed on nothing cancels the arming rather than leaving it
        # set — the gesture said where it meant to end.
        target = _pick_node(ed, event.pos)
        if target is not None and ed.lane_src is not None and target != ed.lane_src:
            _add_lane(ed, ed.lane_src, target)
        ed.lane_src, ed.lane_drag = None, False
        return
    node = ed.drag_node
    ed.drag_node = None
    if node is not None and ed.drag_moved and _illegal(ed, node) and ed.drag_origin is not None:
        # Snapped back rather than clamped to "the nearest legal point": with
        # several constraints live at once that point is ill-defined, and it
        # silently puts the system somewhere nobody asked for.
        ed.recipe.nodes[node].x, ed.recipe.nodes[node].y = ed.drag_origin
        _set_status(ed, "That spot is blocked — the system snapped back.", False)
    ed.drag_origin = None
    ed.drag_moved = ed.drag_undone = False
    ed.bad_nodes = ed.bad_lanes = frozenset()


def _handle_key(event, ed: Editor, settings: Settings) -> Optional[str]:
    if ed.editing_filename:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            _stop_editing_filename(ed)    # commit and cancel are the same here
        elif event.key == pygame.K_BACKSPACE:
            ed.filename = ed.filename[:-1]
        return None

    ctrl = event.mod & pygame.KMOD_CTRL
    if ctrl and event.key == pygame.K_z:
        _undo(ed)
    elif ctrl and event.key in (pygame.K_y, pygame.K_r):
        _redo(ed)
    elif event.key == pygame.K_r:
        ed.view.reset()
    elif event.key == pygame.K_ESCAPE:
        commit(ed, settings)
        return "menu"
    elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
        _delete_lane(ed) if ed.tool == LANES else _delete_selected(ed)
    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
        return _play(ed, settings)
    return None


def _handle_confirm(event, ed: Editor, settings: Settings) -> None:
    answer: Optional[bool] = None
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        for key, value in (("confirm_yes", True), ("confirm_no", False)):
            rect = ed.rects.get(key)
            if rect is not None and rect.w > 0 and rect.collidepoint(event.pos):
                answer = value
    elif event.type == pygame.KEYDOWN:
        if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
            answer = True
        elif event.key in (pygame.K_n, pygame.K_ESCAPE):
            answer = False
    if answer is None:
        return
    which, ed.confirm = ed.confirm, None
    if not answer:
        return
    if which == "auto_lanes":
        _auto_lanes(ed, settings)
    elif which == "clear_lanes":
        _clear_lanes(ed)
    elif which == "new":
        _push_undo(ed)
        _adopt(ed, CustomMap())
        _set_status(ed, "Blank canvas", True)


def _handle_text_input(text: str, ed: Editor) -> None:
    if not ed.editing_filename:
        return
    for ch in text:
        if (ch.isalnum() or ch in "_-.") and len(ed.filename) < _FILENAME_MAX_LEN:
            ed.filename += ch


def pump(ed: Editor) -> None:
    """Per-frame poll of the mobile browser's on-screen keyboard, mirroring
    ``menu.pump``: soft-keyboard typing produces no SDL events at all, so the text
    is read back out of the hidden DOM field instead."""
    if not ed.editing_filename:
        return
    raw = softkeyboard.value(ed.filename)
    text = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")[:_FILENAME_MAX_LEN]
    if text != ed.filename:
        ed.filename = text


# --------------------------------------------------------------------------- #
# Editing operations — every committed mutation goes through `_push_undo` first
# --------------------------------------------------------------------------- #
def _push_undo(ed: Editor) -> None:
    ed.undo_stack.append(ed.recipe.copy())
    del ed.undo_stack[:-config.EDIT_UNDO_DEPTH]
    ed.redo_stack.clear()


def _undo(ed: Editor) -> None:
    if not ed.undo_stack:
        _set_status(ed, "Nothing to undo", False)
        return
    ed.redo_stack.append(ed.recipe.copy())
    _adopt(ed, ed.undo_stack.pop())


def _redo(ed: Editor) -> None:
    if not ed.redo_stack:
        _set_status(ed, "Nothing to redo", False)
        return
    ed.undo_stack.append(ed.recipe.copy())
    _adopt(ed, ed.redo_stack.pop())


def _adopt(ed: Editor, recipe: CustomMap) -> None:
    """Swap in a whole recipe, dropping anything that pointed into the old one.

    A selection is an index, and an index into a map that has been replaced is at
    best meaningless and at worst the wrong system.
    """
    ed.recipe = recipe
    ed.sel_node = ed.sel_lane = None
    ed.lane_src = None
    ed.lane_drag = False
    ed.drag_node = ed.drag_origin = None
    ed.bad_nodes = ed.bad_lanes = frozenset()


def _place(ed: Editor, settings: Settings, pos) -> None:
    """Place a system at ``pos``, if the rules allow it there."""
    if len(ed.recipe.nodes) >= config.MAX_NODES:
        _set_status(ed, f"That's the limit — {config.MAX_NODES} systems.", False)
        return
    wx, wy = ed.view.to_world(pos)
    node = MapNode(x=int(round(wx)), y=int(round(wy)),
                   production=_rolled_production(ed), ships=0).clamped()
    node.ships = _rolled_garrison(ed, settings, node)

    candidate = CustomMap(nodes=ed.recipe.nodes + [node], lanes=list(ed.recipe.lanes))
    index = len(candidate.nodes) - 1
    if _offenders(candidate, index, ed.planar)[0]:
        _set_status(ed, "Too close to another system or a lane.", False)
        return

    _push_undo(ed)
    ed.recipe.nodes.append(node)
    # Placing also arms the move-drag, so place-then-position is one gesture —
    # and one undo. `drag_undone` is already True, so nudging it into place does
    # not push a second snapshot on top of the placement's.
    _arm_move(ed, index, pos)
    ed.drag_undone = True
    _set_status(ed, "", True)


def _rolled_production(ed: Editor) -> int:
    if isinstance(ed.pick, int):        # PALETTE_RANDOM is the string, so this splits them
        return ed.pick
    values = list(config.PRODUCTION_WEIGHTS)
    weights = [config.PRODUCTION_WEIGHTS[v] for v in values]
    return ed.rng.choices(values, weights=weights, k=1)[0]


def _rolled_garrison(ed: Editor, settings: Settings, node: MapNode) -> int:
    """The garrison a freshly placed system starts with.

    Concrete from the moment it is placed — nothing stored is a sentinel resolved
    later, which is what lets the seed stop shaping a custom map. The Economy
    sliders in the sidebar are what govern this roll, so they have to reach
    ``config`` first.
    """
    settings_mod.apply_globals(settings)
    if node.owner != 0:
        return config.HOME_START_SHIPS
    return config.garrison_for(node.production, ed.rng.randint(0, max(0, config.GARRISON_JITTER)))


def _retype_selection(ed: Editor, settings: Settings) -> None:
    """Picking a swatch retypes the selected system as well as arming the next
    placement — otherwise choosing "3/ship" while a system is selected looks like
    it did nothing at all."""
    node = _selected(ed)
    if node is None:
        return
    _push_undo(ed)
    node.production = _rolled_production(ed)
    if node.owner == 0:
        node.ships = _rolled_garrison(ed, settings, node)


def _reroll_selected(ed: Editor, settings: Settings) -> None:
    node = _selected(ed)
    if node is None:
        return
    _push_undo(ed)
    node.production = _rolled_production(ed)
    node.ships = _rolled_garrison(ed, settings, node)


def _step_selected(ed: Editor, attr: str, delta: int) -> None:
    node = _selected(ed)
    if node is None:
        return
    hi = {"production": config.CUSTOM_MAX_PRODUCTION,
          "ships": config.CUSTOM_MAX_SHIPS,
          "owner": config.MAX_PLAYERS}[attr]
    value = max(0, min(hi, getattr(node, attr) + delta))
    if value == getattr(node, attr):
        return
    _push_undo(ed)
    setattr(node, attr, value)


def _delete_selected(ed: Editor) -> None:
    if ed.sel_node is None:
        return
    _push_undo(ed)
    ed.recipe = ed.recipe.without_node(ed.sel_node)
    ed.sel_node = None
    ed.drag_node = ed.drag_origin = None
    ed.bad_nodes = ed.bad_lanes = frozenset()


def _add_lane(ed: Editor, a: int, b: int) -> None:
    """Draw one lane, if the rules allow it there. The single commit path, shared
    by the tap-tap and drag gestures."""
    key = (min(a, b), max(a, b))
    if key in ed.recipe.lanes:
        _set_status(ed, f"#{key[0]} and #{key[1]} are already linked.", False)
        return

    candidate = CustomMap(nodes=ed.recipe.nodes, lanes=sorted(ed.recipe.lanes + [key]))
    index = candidate.lanes.index(key)
    if _lane_offenders(candidate, index, ed.planar)[1]:
        _set_status(ed, "That lane would run under a system."
                    if not ed.planar else
                    "That lane would run under a system, or cross another.", False)
        return

    _push_undo(ed)
    ed.recipe.lanes = candidate.lanes
    ed.sel_lane = index
    _set_status(ed, "", True)


def _delete_lane(ed: Editor) -> None:
    lane = _selected_lane(ed)
    if lane is None:
        return
    _push_undo(ed)
    ed.recipe.lanes = [pair for pair in ed.recipe.lanes if pair != lane]
    ed.sel_lane = None


def _clear_lanes(ed: Editor) -> None:
    if not ed.recipe.lanes:
        return
    _push_undo(ed)
    ed.recipe.lanes = []
    ed.sel_lane = None
    _set_status(ed, "Every lane removed", True)


def _auto_lanes(ed: Editor, settings: Settings) -> None:
    """Rebuild the whole lane network with ``mapgen``'s own planar edge builder.

    Replaces rather than merges, which is why it is behind a confirm:
    ``_planar_edges`` always builds a full MST, so merging it into a hand-drawn
    set would silently bury a deliberate bottleneck. Its output is planar and
    graze-free by construction, so it can never produce a map the manual rules
    would then refuse.

    ``_planar_edges`` reads ``EXTRA_EDGE_FRACTION``/``MAX_EDGE_LENGTH_FRAC`` live
    off ``config``, so the knobs go in through ``settings.apply_globals`` — the
    single sanctioned writer. This module never ``setattr``s ``config`` itself.
    """
    if len(ed.recipe.nodes) < 2:
        _set_status(ed, "Place at least two systems first.", False)
        return
    settings_mod.apply_globals(settings)
    _push_undo(ed)
    positions = [n.pos for n in ed.recipe.nodes]
    ed.recipe.lanes = sorted(
        (min(a, b), max(a, b)) for a, b in mapgen._planar_edges(positions)
    )
    ed.sel_lane = None
    _set_status(ed, f"{len(ed.recipe.lanes)} lanes drawn", True)


# --------------------------------------------------------------------------- #
# Geometry & validation helpers — all of them defer to `CustomMap.problems`
# --------------------------------------------------------------------------- #
# The positional rules, the ones a move or a placement can break. A seat gap or a
# disconnected graph is equally a blocker, but neither is the drag's fault and
# neither should snap a system back. "crossing" joins them only while the Planar
# toggle is on — it is a warning in the validator, not a blocker, so with the
# toggle off a crossing must not refuse anything.
_PLACEMENT_CODES = frozenset({"too_close", "graze"})


def _codes(planar: bool) -> frozenset[str]:
    return _PLACEMENT_CODES | {"crossing"} if planar else _PLACEMENT_CODES


def _offenders(recipe: CustomMap, index: int, planar: bool = True
               ) -> tuple[frozenset[int], frozenset[int]]:
    """The nodes and lanes a positional rule is complaining about *around* the
    system ``index`` — one validator, filtered, rather than a second copy of the
    geometry that could drift from what the Play gate enforces."""
    return _filter(recipe, planar, lambda problem: index in problem.nodes)


def _lane_offenders(recipe: CustomMap, lane: int, planar: bool
                    ) -> tuple[frozenset[int], frozenset[int]]:
    """The same, for a lane rather than a system — what a newly drawn lane is
    tested against."""
    return _filter(recipe, planar, lambda problem: lane in problem.lanes)


def _filter(recipe: CustomMap, planar: bool, touches) -> tuple[frozenset[int], frozenset[int]]:
    codes = _codes(planar)
    nodes: set[int] = set()
    lanes: set[int] = set()
    for problem in recipe.problems():
        if problem.code not in codes or not touches(problem):
            continue
        nodes.update(problem.nodes)
        lanes.update(problem.lanes)
    return frozenset(nodes), frozenset(lanes)


def _refresh_bad(ed: Editor, index: int) -> None:
    ed.bad_nodes, ed.bad_lanes = _offenders(ed.recipe, index, ed.planar)


def _illegal(ed: Editor, index: int) -> bool:
    return bool(_offenders(ed.recipe, index, ed.planar)[0])


def _move_node(ed: Editor, index: int, pos) -> None:
    wx, wy = ed.view.to_world(pos)
    moved = MapNode(x=int(round(wx)), y=int(round(wy))).clamped()
    ed.recipe.nodes[index].x, ed.recipe.nodes[index].y = moved.x, moved.y


def _pick_node(ed: Editor, pos) -> Optional[int]:
    """The system under ``pos``, nearest first, with the tap floor every small
    system on the board already gets."""
    best, best_d = None, None
    for i, node in enumerate(ed.recipe.nodes):
        reach = max(config.node_radius(node.production), config.NODE_TAP_MIN)
        d = dist(pos, ed.view.to_screen(node.pos))
        if d <= reach and (best_d is None or d < best_d):
            best, best_d = i, d
    return best


def _screen_of(ed: Editor, index: int) -> tuple[int, int]:
    return ed.view.to_screen(ed.recipe.nodes[index].pos)


def _selected_lane(ed: Editor) -> Optional[tuple[int, int]]:
    """The selected lane as its node pair, or None. Returns the *pair* rather than
    the index, because an index into a list that has since been edited is at best
    meaningless and at worst the wrong lane."""
    if ed.sel_lane is None or not 0 <= ed.sel_lane < len(ed.recipe.lanes):
        return None
    return ed.recipe.lanes[ed.sel_lane]


def _lane_length(a: MapNode, b: MapNode) -> float:
    return round(dist(a.pos, b.pos) * config.LY_PER_WORLD_UNIT, 1)


def _pick_lane(ed: Editor, pos) -> Optional[int]:
    """The lane under ``pos``, nearest first — with a repeat press cycling through
    overlapping candidates rather than always grabbing the same one, the same
    shape ``input._pick_lane`` uses."""
    hits: list[tuple[float, int]] = []
    for i, (a, b) in enumerate(ed.recipe.lanes):
        d = point_segment_dist(pos, ed.view.to_screen(ed.recipe.nodes[a].pos),
                               ed.view.to_screen(ed.recipe.nodes[b].pos))
        if d <= config.LANE_PICK_DIST:
            hits.append((d, i))
    if not hits:
        return None
    order = [i for _d, i in sorted(hits)]
    if ed.sel_lane in order:
        return order[(order.index(ed.sel_lane) + 1) % len(order)]
    return order[0]


def _selected(ed: Editor) -> Optional[MapNode]:
    if ed.sel_node is None or not 0 <= ed.sel_node < len(ed.recipe.nodes):
        return None
    return ed.recipe.nodes[ed.sel_node]


def _focus_problem(ed: Editor, index: int) -> None:
    problems = ed.problems()
    if not 0 <= index < len(problems):
        return
    targets = [i for i in problems[index].nodes if 0 <= i < len(ed.recipe.nodes)]
    if not targets:
        return
    ed.sel_node = targets[0]
    ed.view.fit_to([ed.recipe.nodes[i].pos for i in targets])


def _view_centre() -> tuple[int, int]:
    vx, vy, vw, vh = _view_rect()
    return (vx + vw // 2, vy + vh // 2)


# --------------------------------------------------------------------------- #
# Sliders, text entry and the map library
# --------------------------------------------------------------------------- #
def _slider_specs():
    """Every slider the creator can draw, whichever tool is up. One lookup, so
    `_set_slider` cannot go looking in the wrong group."""
    return tuple(econ_specs()) + tuple(lane_specs())


def _SLIDER_KEYS() -> frozenset[str]:
    return frozenset(spec[0] for spec in _slider_specs())


def _set_slider(ed: Editor, settings: Settings, key: str, px: int) -> None:
    """Write a slider's value from a pointer x.

    Inverted through ``widgets.slider_fraction``, which is the exact inverse of
    where ``widgets.slider`` puts the knob — re-deriving the inset here is how a
    knob ends up drifting away from the finger at the extremes.
    """
    spec = next((s for s in _slider_specs() if s[0] == key), None)
    rect = ed.rects.get(key)
    if spec is None or rect is None:
        return
    _key, _label, attr, lo, hi, step, is_int = spec
    t = widgets.slider_fraction(rect, px)
    value = lo + t * (hi - lo)
    value = round(value / step) * step if step else value
    value = max(lo, min(hi, value))
    setattr(settings, attr, int(round(value)) if is_int else round(value, 4))


def _stop_editing_filename(ed: Editor) -> None:
    if not ed.editing_filename:
        return
    ed.editing_filename = False
    softkeyboard.close()
    try:
        pygame.key.stop_text_input()
    except pygame.error:
        pass


def _start_text_input() -> None:
    try:
        pygame.key.start_text_input()
    except pygame.error:
        pass


def _map_path(name: str) -> Path:
    """The file for ``name`` under the gitignored maps dir. The field's character
    filter excludes path separators, so it can never escape that directory."""
    name = name.strip() or _DEFAULT_FILENAME
    if not name.endswith(".json"):
        name += ".json"
    return paths.maps_dir() / name


def _save_file(ed: Editor) -> None:
    path = _map_path(ed.filename)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(ed.recipe.normalised().to_dict(), fh, indent=2)
        _set_status(ed, f"Saved {path.name}", True)
    except OSError:
        _set_status(ed, f"Couldn't save {path.name}", False)


def _open_file(ed: Editor) -> None:
    path = _map_path(ed.filename)
    try:
        with open(path) as fh:
            recipe = CustomMap.from_dict(json.load(fh))
    except (OSError, ValueError):
        _set_status(ed, f"Couldn't open {path.name}", False)
        return
    if recipe is None:
        _set_status(ed, f"{path.name} isn't a usable map", False)
        return
    _push_undo(ed)
    _adopt(ed, recipe)
    _set_status(ed, f"Opened {path.name}", True)


def _set_status(ed: Editor, text: str, ok: bool) -> None:
    ed.status, ed.status_ok = text, ok
    ed.status_ms = _STATUS_MS if ok else _STATUS_ERROR_MS


def age_status(ed: Editor, dt: int) -> None:
    """Expire the status line. A failure holds far longer than a success, for the
    reason ``menu``'s does: an error is the only account of something that did
    *not* happen, and one that clears before it can be read is worse than none."""
    if not ed.status:
        return
    ed.status_ms -= dt
    if ed.status_ms <= 0:
        ed.status, ed.status_ms = "", 0

