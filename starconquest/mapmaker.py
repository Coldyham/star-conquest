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

import pygame

from . import config, maker, softkeyboard, widgets
from . import paths  # noqa: F401 - the maps folder `maker.map_path` reads, named here for its tests
from .custommap import MapNode
from .geometry import WorldView, dist, point_segment_dist
from .maker import SYSTEMS  # noqa: F401 - the tool's name, for callers of this module
from .maker import LANES, OWNERS, PALETTE_RANDOM, Editor
from .settings import Settings

# The editor's model, its edits and its words are `maker`'s, read by the HTML
# build's page too; the names below are this module's own for them, kept so
# its drawing and its tests read as they always did.
_TOOLS = maker.TOOLS
_READY_TOOLS = maker.READY_TOOLS
_PALETTE_VALUES = maker.PALETTE_VALUES
_DEFAULT_FILENAME = maker.DEFAULT_FILENAME
_FILENAME_MAX_LEN = maker.FILENAME_MAX_LEN
_STATUS_MS = maker.STATUS_MS
_STATUS_ERROR_MS = maker.STATUS_ERROR_MS

_PANEL_BG = (22, 25, 36)
_PANEL_EDGE = (48, 54, 74)
_WARN = (220, 150, 90)
_LANE_ARM = (150, 200, 240)   # the armed lane source, and its rubber band
_BAD = (220, 110, 110)


# --------------------------------------------------------------------------- #
# Opening / committing
# --------------------------------------------------------------------------- #
def open_editor(settings: Settings, seed: int | None = None) -> Editor:
    """The editor for ``settings`` (`maker.open_editor`), with this shell's camera."""
    return maker.open_editor(settings, seed, _build_view())


_generated = maker.generated
_centred = maker.centred
commit = maker.commit


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
    """The camera over the *full* hand-map box, fixed for the editor's lifetime.

    Fixed rather than fitted to the nodes: ``WorldView`` takes its bounds at
    construction and never re-derives them, so a box that grew as systems were
    placed would make the map jump under the cursor — and ``mapgen.map_bounds``
    raises on an empty map anyway. Placement is allowed anywhere in the box, not
    just inside ``WORLD_MARGIN``: in play, ``main.build_view`` fits to the actual
    node bounding box, so a map drawn to the edges simply fills the viewport more
    tightly.

    The box is ``config.CUSTOM_WORLD_W`` x ``config.WORLD_SIZE`` — wider than the
    square ``mapgen`` rolls into, and the same bounds ``MapNode.clamped`` enforces,
    so the canvas is exactly the region a system may occupy.
    """
    return WorldView(
        (0.0, 0.0, config.CUSTOM_WORLD_W, config.WORLD_SIZE),
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


def _footer_rows() -> int:
    """How many rows the footer strip takes: one, or two where every control
    won't fit across one (a phone) — the map-replacing and history pair on the
    first, the file cluster and *Play now* on the second. Measured from the
    labels alone, which don't change with the editor's state."""
    font = widgets.fonts()["small"]
    widths = [widgets.btn_w(font, "M" * 10) if key == "filename" else widgets.btn_w(font, label)
              for key, label, *_rest in _footer_specs(None)]
    avail = _strip_w() - 2 * config.HUD_PAD
    return 1 if sum(widths) + config.BTN_GAP * (len(widths) - 1) <= avail else 2


def _strip_w() -> int:
    """Width of the bands under the map (palette, footer): the map's own, beside
    the sidebar, or the screen's in portrait, where the sidebar is a band too."""
    return config.SCREEN_W if config.portrait else config.SCREEN_W - config.EDIT_SIDE_W


def _footer_h() -> int:
    rows = _footer_rows()
    return rows * widgets.tap_size(config.FOOTER_BTN_H) + (rows - 1) * config.BTN_GAP + 2 * config.HUD_PAD


# The sidebar's share of a portrait screen, where it is a band under the palette.
_PORTRAIT_SIDE_FRAC = 0.3


def _view_rect() -> tuple[int, int, int, int]:
    """The map viewport. Derived here rather than from ``config.play_rect()``,
    which reserves ``HUD_RIGHT_W`` and both HUD bars for a HUD this scene has
    none of."""
    top = config.EDIT_TOP_H
    side_h = _side_rect().h if config.portrait else 0
    return (
        0,
        top,
        max(1, _strip_w()),
        max(1, config.SCREEN_H - top - _palette_h() - _footer_h() - side_h),
    )


def _side_rect() -> pygame.Rect:
    if config.portrait:
        h = round(config.SCREEN_H * _PORTRAIT_SIDE_FRAC)
        return pygame.Rect(0, config.SCREEN_H - _footer_h() - h, config.SCREEN_W, h)
    return pygame.Rect(
        config.SCREEN_W - config.EDIT_SIDE_W,
        config.EDIT_TOP_H,
        config.EDIT_SIDE_W,
        config.SCREEN_H - config.EDIT_TOP_H,
    )


def _palette_rect() -> pygame.Rect:
    bottom = _side_rect().y if config.portrait else config.SCREEN_H - _footer_h()
    return pygame.Rect(0, bottom - _palette_h(), _strip_w(), _palette_h())


def _footer_rect() -> pygame.Rect:
    return pygame.Rect(
        0,
        config.SCREEN_H - _footer_h(),
        _strip_w(),
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

    if ed.box_active:
        box = pygame.Rect(_clip_to_view(_box_rect(ed)))
        if box.w > 0 and box.h > 0:
            pygame.draw.rect(surface, config.COLOR_SELECT, box, config.s(1))

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

    # *Menu* rides in the tool strip rather than the footer: drawing a map is a
    # setup step, and going back to the seats, strategies, fog and seed behind it
    # is the stage after the three tools, not a sibling of Undo. Green for the same
    # reason it always was — it is the way on, not the way out.
    label = widgets.key_hint("Menu", "Esc")
    back = pygame.Rect(x + config.BTN_GAP, y, widgets.btn_w(font, label), h)
    widgets.btn(surface, back, label, *widgets.BTN_GREEN, font)
    ed.rects["back"] = back

    if ed.status:
        color = config.COLOR_TEXT if ed.status_ok else _BAD
        # Clipped to the room the strip has left, so a long message runs off to the
        # left rather than over the buttons — the same end-anchoring `_text_field`
        # gives the filename box.
        room = pygame.Rect(back.right + config.BTN_GAP, 0,
                           max(0, config.SCREEN_W - config.HUD_PAD - back.right - config.BTN_GAP),
                           config.EDIT_TOP_H)
        prev = surface.get_clip()
        surface.set_clip(room.clip(bar))
        widgets.text(surface, font, ed.status, color,
                     midright=(config.SCREEN_W - config.HUD_PAD, config.EDIT_TOP_H // 2))
        surface.set_clip(prev)


_TOOL_HINTS = maker.TOOL_HINTS


def _draw_palette(surface, ed: Editor) -> None:
    """The bottom band: production swatches in the Systems tool, a hint in tools
    that have no palette. Its *height* never varies (see ``_palette_h``) — only
    what is drawn in it."""
    band = _palette_rect()
    pygame.draw.rect(surface, _PANEL_BG, band)
    pygame.draw.line(surface, _PANEL_EDGE, (0, band.top), (band.right, band.top))

    font = widgets.fonts()["small"]
    if ed.tool == OWNERS:
        _draw_seat_palette(surface, ed, band, font)
        return
    if ed.tool in _TOOL_HINTS:
        y = band.centery - widgets.row_h("small") // 2
        for line in _TOOL_HINTS[ed.tool]:
            widgets.text(surface, font, line, config.COLOR_TEXT_DIM, center=(band.centerx, y))
            y += widgets.row_h("small")
        return

    entries = maker.production_entries()

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


_seat_entries = maker.seat_entries


def _draw_seat_palette(surface, ed: Editor, band: pygame.Rect, font) -> None:
    """Neutral, then the seats — one more than are currently held, floored at two.

    Offering ``n + 1`` is what makes the common path gap-free by construction: you
    can always reach the next seat and never one past it. It does not *prevent* a
    gap (paint seat 3, then clear seat 2), which is why that case gets a blocker
    with a one-press fix rather than a silent renumber — renumbering changes a
    seat's colour without being asked, and the colour is part of what an author
    intended.

    A press here goes through ``_pick_seat``, the same as the sidebar's owner row
    (``_owner_row``) below — one control in two places, so a system already
    selected recolours the moment you press a different swatch here, rather than
    only from the sidebar.
    """
    entries = _seat_entries(ed.recipe)
    captions = {pid: maker.seat_caption(ed.recipe, pid) for pid in entries}
    swatch = widgets.tap_size(2 * config.NODE_MAX_RADIUS + config.s(8))
    wanted = max(swatch, max(font.size(c)[0] for c in captions.values()) + config.s(8))
    gaps = config.BTN_GAP * (len(entries) - 1)
    room = max(1, (band.w - 2 * config.HUD_PAD - gaps) // len(entries))
    cell = min(wanted, room)
    captioned = cell >= wanted

    x = band.x + max(config.HUD_PAD, (band.w - (cell * len(entries) + gaps)) // 2)
    cy = band.y + config.HUD_PAD + config.NODE_MAX_RADIUS
    for pid in entries:
        rect = pygame.Rect(x, band.y + config.HUD_PAD, cell, band.h - 2 * config.HUD_PAD)
        colour = config.player_color(pid)
        centre = (rect.centerx, cy)
        r = config.node_radius(config.HOME_PRODUCTION)
        pygame.draw.circle(surface, colour, centre, r)
        pygame.draw.circle(surface, widgets.brighten(colour, 40), centre, r, config.s(2))
        glyph = maker.seat_glyph(pid)
        widgets.text(surface, font, glyph, config.text_on(colour), center=centre)
        if ed.owner_pick == pid:
            pygame.draw.circle(surface, config.COLOR_SELECT, centre,
                               r + config.NODE_RING_PAD, config.s(2))
        if captioned:
            widgets.text(surface, font, captions[pid], config.COLOR_TEXT_DIM,
                         center=(rect.centerx, cy + config.NODE_MAX_RADIUS + widgets.row_h("small") // 2))
        ed.rects[f"seat_{pid}"] = rect
        x = rect.right + config.BTN_GAP


def _draw_side(surface, ed: Editor, settings: Settings) -> None:
    panel = _side_rect()
    pygame.draw.rect(surface, _PANEL_BG, panel)
    if config.portrait:   # a band under the palette
        pygame.draw.line(surface, _PANEL_EDGE, (panel.x, panel.y), (panel.right, panel.y))
    else:
        pygame.draw.line(surface, _PANEL_EDGE, (panel.x, panel.y), (panel.x, panel.bottom))
    if not config.compact:
        _draw_side_body(surface, ed, settings, panel)
        return
    # A phone's sidebar holds more than it has room for, so it scrolls: drawn
    # shifted up by the scroll, cut at its edges, and whatever it recorded
    # outside them dropped, so a tap lands on what shows and nothing scrolled
    # off it. `_side_scroll_event` moves it.
    ed.side_scroll = max(0, min(ed.side_scroll, ed.side_content_h - panel.h))
    before = dict(ed.rects)
    rows = len(ed.problem_rows)
    surface.set_clip(panel)
    try:
        bottom = _draw_side_body(surface, ed, settings, panel, -ed.side_scroll)
    finally:
        surface.set_clip(None)
    ed.side_content_h = bottom + ed.side_scroll - panel.y + config.PANEL_PAD
    for key, rect in list(ed.rects.items()):
        if before.get(key) is not rect:
            cut = rect.clip(panel)
            if cut.w and cut.h:
                ed.rects[key] = cut
            else:
                del ed.rects[key]
    ed.problem_rows[rows:] = [(i, r.clip(panel)) for i, r in ed.problem_rows[rows:] if r.colliderect(panel)]
    room = ed.side_content_h - panel.h
    if room > 0:
        track = pygame.Rect(panel.right - config.s(5), panel.y + config.s(6), config.s(3), panel.h - config.s(12))
        thumb_h = max(config.s(24), track.h * panel.h // ed.side_content_h)
        thumb = pygame.Rect(track.x, track.y + (track.h - thumb_h) * ed.side_scroll // room, track.w, thumb_h)
        pygame.draw.rect(surface, _PANEL_EDGE, track)
        pygame.draw.rect(surface, widgets.BTN_BLUE[1], thumb)


def _pinch(event, ed: Editor) -> bool:
    """Two fingers zoom and pan the canvas; True if ``event`` was spent on it.
    The first finger's emulated press has already been handled (in the Systems
    tool it places a system), so a pinch puts back the recipe and selection from
    before it, and the emulated mouse is ignored until every finger is up. The
    same gesture `input._pinch` reads in the game."""
    if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
        pos = (event.x * config.SCREEN_W, event.y * config.SCREEN_H)
        if event.type == pygame.FINGERUP:
            ed.fingers.pop(event.finger_id, None)
            if len(ed.fingers) < 2:
                ed.pinch_last = None
            if not ed.fingers:
                ed.pinch_lock = False
            return True
        ed.fingers[event.finger_id] = pos
        if len(ed.fingers) != 2:
            return True
        (x0, y0), (x1, y1) = ed.fingers.values()
        spread = max(1.0, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        mid = ((x0 + x1) / 2, (y0 + y1) / 2)
        if ed.pinch_last is None:
            if not ed.pinch_lock:
                maker.unpress(ed)
            ed.press_undo = None
            ed.pinch_lock = True
            ed.drag_node = ed.drag_slider = ed.lane_src = None
            ed.pan_active = ed.box_press = ed.box_active = ed.lane_drag = False
        else:
            last_spread, last_mid = ed.pinch_last
            ed.view.pan(mid[0] - last_mid[0], mid[1] - last_mid[1])
            ed.view.zoom_at((round(mid[0]), round(mid[1])), spread / last_spread)
        ed.pinch_last = (spread, mid)
        return True
    if ed.pinch_lock and event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
                                        pygame.MOUSEMOTION):
        return True
    if event.type == pygame.MOUSEBUTTONDOWN and config.touch_ui:
        maker.snapshot(ed)
    return False


def _side_scroll_event(event, ed: Editor) -> bool:
    """Scroll a compact sidebar: the wheel over it, or a drag that starts on its
    blank space (every control in it keeps its own press). True if spent."""
    if not config.compact:
        return False
    panel = _side_rect()
    if event.type == pygame.MOUSEWHEEL:
        if not panel.collidepoint(pygame.mouse.get_pos()):
            return False
        ed.side_scroll = max(0, ed.side_scroll - event.y * config.s(40))
        return True
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        if (not panel.collidepoint(event.pos) or _hit(ed, event.pos) is not None
                or any(r.collidepoint(event.pos) for _i, r in ed.problem_rows)):
            return False
        ed.side_drag = (event.pos[1], ed.side_scroll)
        return True
    if event.type == pygame.MOUSEMOTION and ed.side_drag is not None:
        y0, s0 = ed.side_drag
        ed.side_scroll = max(0, s0 - (event.pos[1] - y0))
        return True
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and ed.side_drag is not None:
        ed.side_drag = None
        return True
    return False


def _draw_side_body(surface, ed: Editor, settings: Settings, panel: pygame.Rect,
                    shift: int = 0) -> int:
    """The sidebar's content, ``shift`` px down (a scrolled one's is negative).
    Returns the y it ran to."""

    f = widgets.fonts()
    pad = config.PANEL_PAD
    x = panel.x + pad
    inner_w = panel.w - 2 * pad
    y = panel.y + pad + shift

    if ed.tool == LANES:
        y = _draw_lane_selection(surface, ed, f, x, y, inner_w)
        y = _draw_lane_controls(surface, ed, settings, f, x, y, inner_w)
    elif ed.tool == OWNERS:
        y = _draw_selection(surface, ed, f, x, y, inner_w)
        y = _draw_owner_controls(surface, ed, f, x, y, inner_w)
    else:
        y = _draw_selection(surface, ed, f, x, y, inner_w)
        if ed.sel_node is None and not config.touch_ui:
            # Only in the room the "Click empty space..." placeholder already
            # spends nothing past — the selected-system block right above is
            # already the tight case at high UI scale, and this has no touch
            # equivalent to be worth the squeeze there anyway (dropped the same
            # way `render._key_hint` drops the `(Esc)`/`(R)` suffixes).
            for line in widgets.wrap(f["small"], maker.SHIFT_TIP, inner_w):
                widgets.text(surface, f["small"], line, config.COLOR_TEXT_DIM,
                            topleft=(x, y))
                y += widgets.row_h("small")
            y += config.ROW_GAP
        y = _draw_sliders(surface, ed, settings, f, x, y, inner_w,
                          maker.ECON_HEADING, econ_specs())
    # scrolled, the list runs its full length; fixed, it stops at the floor
    floor = 10 ** 6 if config.compact else panel.bottom - pad
    return _draw_problems(surface, ed, f, x, y, inner_w, floor)


def _draw_selection(surface, ed: Editor, f, x: int, y: int, w: int) -> int:
    node = _selected(ed)
    if node is None:
        for line in widgets.wrap(f["small"], maker.PLACE_HINT, w):
            widgets.text(surface, f["small"], line, config.COLOR_TEXT_DIM, topleft=(x, y))
            y += widgets.row_h("small")
        return y + config.ROW_GAP

    widgets.text(surface, f["normal"], f"System #{ed.sel_node}", config.COLOR_TEXT, topleft=(x, y))
    y += widgets.row_h("normal")

    y = _stepper_row(surface, ed, f, x, y, w, "prod", "Production", f"{node.production}/ship")
    y = _stepper_row(surface, ed, f, x, y, w, "ships", "Garrison", str(node.ships))
    y = _garrison_slider_row(surface, ed, f, x, y, w, node)
    y = _owner_row(surface, ed, f, x, y, w, node)

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
        widgets.text(surface, f["small"], maker.LANE_HINT,
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
        for line in widgets.wrap(f["small"], maker.GROWTH_NOTE, w):
            widgets.text(surface, f["small"], line, _WARN, topleft=(x, y))
            y += widgets.row_h("small")

    y += config.ROW_GAP
    bh = widgets.tap_size(config.FOOTER_BTN_H)
    ed.rects["delete_lane"] = _btn_rect(
        surface, pygame.Rect(x, y, w, bh), "Delete lane", widgets.BTN_RED, f["small"])
    return y + bh + config.ROW_GAP * 2


def _draw_checkbox(surface, ed: Editor, f, x: int, y: int, w: int,
                   key: str, checked: bool, label: str) -> int:
    """A labelled checkbox row, registering ``ed.rects[key]`` over the whole row
    so the label is as clickable as the box. Shared by Planar and Re-run — the
    Lanes sidebar's only two toggles."""
    box = widgets.tap_size(config.STEPPER_SIZE)
    row = max(box, widgets.row_h())
    rect = pygame.Rect(x, y, w, row)
    mark = pygame.Rect(x, y + (row - box) // 2, box, box)
    accent = widgets.BTN_BLUE[1]
    pygame.draw.rect(surface, config.COLOR_BG, mark, border_radius=config.s(4))
    pygame.draw.rect(surface, accent, mark, config.s(1), border_radius=config.s(4))
    if checked:
        inner = mark.inflate(-box // 2, -box // 2)
        pygame.draw.rect(surface, accent, inner, border_radius=config.s(2))
    widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM,
                 midleft=(mark.right + config.BTN_GAP, y + row // 2))
    ed.rects[key] = rect
    return y + row + config.ROW_GAP


def _draw_lane_controls(surface, ed: Editor, settings: Settings, f, x: int, y: int, w: int) -> int:
    """The two lane-generation knobs, the Planar and Re-run toggles, and the two
    map-wide lane actions."""
    y = _draw_sliders(surface, ed, settings, f, x, y, w, maker.LANES_HEADING, lane_specs())

    y = _draw_checkbox(surface, ed, f, x, y, w, "planar", ed.planar,
                       maker.PLANAR_LABEL)
    y = _draw_checkbox(surface, ed, f, x, y, w, "auto_relane", ed.auto_relane,
                       maker.RELANE_LABEL)

    bh = widgets.tap_size(config.FOOTER_BTN_H)
    bw = (w - config.BTN_GAP) // 2
    ed.rects["auto_lanes"] = _btn_rect(
        surface, pygame.Rect(x, y, bw, bh), "Auto", widgets.BTN_VIOLET, f["small"])
    ed.rects["clear_lanes"] = _btn_rect(
        surface, pygame.Rect(x + bw + config.BTN_GAP, y, w - bw - config.BTN_GAP, bh),
        "Clear all", widgets.BTN_AMBER, f["small"])
    return y + bh + config.ROW_GAP * 2


def _draw_owner_controls(surface, ed: Editor, f, x: int, y: int, w: int) -> int:
    """Make home system and the seat-gap fix, then the map-wide auto-placement block.

    Laid out the way the Lanes sidebar is: what the selection can do first, the
    knob-plus-Auto block that rewrites the whole map last.
    """
    bh = widgets.tap_size(config.FOOTER_BTN_H)
    if _selected(ed) is not None:
        # Explicit, never implicit: painting a colour must not silently rewrite
        # numbers you set, but "give this one a real garrison" shouldn't be a trip
        # back to the Systems tool either.
        ed.rects["make_home"] = _btn_rect(
            surface, pygame.Rect(x, y, w, bh), "Make home system", widgets.BTN_BLUE, f["small"])
        y += bh + config.ROW_GAP

    if any(p.code == "seat_gap" for p in ed.problems()):
        ed.rects["renumber"] = _btn_rect(
            surface, pygame.Rect(x, y, w, bh), "Renumber seats", widgets.BTN_AMBER, f["small"])
        y += bh + config.ROW_GAP

    # The seat count lives here rather than staying on the menu's Basic tab: with
    # a recipe set, `commit` derives `settings.players` from the map, so the
    # number that decides how many seats play *is* how many homeworlds get placed.
    widgets.text(surface, f["small"], maker.SEATS_HEADING, config.COLOR_TEXT_DIM, topleft=(x, y))
    y += widgets.row_h("small") + config.ROW_GAP
    y = _stepper_row(surface, ed, f, x, y, w, "seats", "Seats", str(ed.seat_target()))
    ed.rects["auto_owners"] = _btn_rect(
        surface, pygame.Rect(x, y, w, bh), "Auto", widgets.BTN_VIOLET, f["small"])
    return y + bh + config.ROW_GAP * 2


def _btn_rect(surface, rect, label, palette, font) -> pygame.Rect:
    """``widgets.btn`` returning a Rect rather than a tuple, since ``ed.rects``
    holds Rects — ``collidepoint`` is what every hit test here wants."""
    widgets.btn(surface, rect, label, *palette, font)
    return rect


def _stepper_row(surface, ed: Editor, f, x: int, y: int, w: int,
                 key: str, label: str, value: str) -> int:
    """One "label  −  value  +" row, recording both step rects."""
    size = widgets.tap_size(config.STEPPER_SIZE)
    row = max(size, widgets.row_h())
    minus = pygame.Rect(x + w - 2 * size - config.BTN_GAP, y + (row - size) // 2, size, size)
    plus = pygame.Rect(x + w - size, y + (row - size) // 2, size, size)
    accent = widgets.BTN_BLUE[1]

    widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM, midleft=(x, y + row // 2))
    widgets.text(surface, f["small"], value, config.COLOR_TEXT,
                 midright=(minus.x - config.BTN_GAP, y + row // 2))

    widgets.step_button(surface, minus, "-", accent)
    widgets.step_button(surface, plus, "+", accent)
    ed.rects[f"{key}_minus"] = minus
    ed.rects[f"{key}_plus"] = plus
    return y + row + config.ROW_GAP


def _owner_row(surface, ed: Editor, f, x: int, y: int, w: int, node: MapNode) -> int:
    """Owner as a row of seat swatches, sized and shaped like the -/+ steppers above.

    A seat *is* a colour, so pointing at one beats stepping through them: the
    stepper this replaces made picking seat 4 four presses, and never showed what
    the seats were. The list is ``_seat_entries`` — exactly what the Owners tool's
    palette band offers — and the current owner is still named on the label row, so
    nothing is conveyed by colour alone.

    A press goes through ``_pick_seat`` — the same as the palette band above,
    which is what lets either row recolour the selected system. Pressing the
    swatch a system already holds is still inert: Neutral is its own swatch here,
    so a toggle-to-neutral would be a second meaning on a press that already has
    one, and that stays the map tap's job.
    """
    entries = _seat_entries(ed.recipe)
    name = "Neutral" if node.owner == 0 else config.player_name(node.owner)
    widgets.text(surface, f["small"], "Owner", config.COLOR_TEXT_DIM, topleft=(x, y))
    widgets.text(surface, f["small"], name, config.COLOR_TEXT,
                 midright=(x + w, y + widgets.row_h("small") // 2))
    y += widgets.row_h("small")

    gap = max(1, config.BTN_GAP // 2)
    # Capped at its share of the row, the same squeeze the palette bands take: the
    # list grows as seats are painted, and the sidebar's width does not.
    room = max(1, (w - gap * (len(entries) - 1)) // len(entries))
    cell = min(widgets.tap_size(config.STEPPER_SIZE), room)
    radius = config.s(4)
    for i, pid in enumerate(entries):
        rect = pygame.Rect(x + i * (cell + gap), y, cell, cell)
        colour = config.player_color(pid)
        edge = config.COLOR_SELECT if pid == node.owner else widgets.brighten(colour, 40)
        pygame.draw.rect(surface, colour, rect, border_radius=radius)
        pygame.draw.rect(surface, edge, rect, config.s(2), border_radius=radius)
        widgets.text(surface, f["small"], "-" if pid == 0 else str(pid),
                     config.text_on(colour), center=rect.center)
        ed.rects[f"own_{pid}"] = rect
    return y + cell + config.ROW_GAP


def _garrison_slider_row(surface, ed: Editor, f, x: int, y: int, w: int, node: MapNode) -> int:
    """A coarse drag for Garrison, under the stepper row it doesn't replace.

    Tops out at `config.CUSTOM_GARRISON_SLIDER_MAX` for a comfortable drag range;
    the steppers above stay the way to reach a bigger garrison than the slider
    can, up to `config.CUSTOM_MAX_SHIPS`. A value already past the slider's top
    just shows the knob pinned at the right end.
    """
    track = pygame.Rect(x, y, w, widgets.tap_size(config.SLIDER_KNOB_R * 2))
    t = min(1.0, node.ships / config.CUSTOM_GARRISON_SLIDER_MAX)
    widgets.slider(surface, track, t, *widgets.BTN_BLUE)
    ed.rects["ships_slider"] = track
    return y + track.h + config.ROW_GAP


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

    # A portrait band is the screen's width and a fraction of its height: two
    # sliders to a row there.
    cols = 2 if config.portrait else 1
    gap = config.s(16)
    cw = (w - gap * (cols - 1)) // cols
    for i, (key, label, attr, lo, hi, _step, is_int) in enumerate(specs):
        cx = x + (i % cols) * (cw + gap)
        value = getattr(settings, attr)
        shown = f"{int(value)}" if is_int else f"{value:g}"
        widgets.text(surface, f["small"], label, config.COLOR_TEXT_DIM, topleft=(cx, y))
        widgets.text(surface, f["small"], shown, config.COLOR_TEXT,
                     midright=(cx + cw, y + widgets.row_h("small") // 2))
        track = pygame.Rect(cx, y + widgets.row_h("small"), cw, widgets.tap_size(config.SLIDER_KNOB_R * 2))
        t = 0.0 if hi == lo else (value - lo) / (hi - lo)
        widgets.slider(surface, track, t, *widgets.BTN_BLUE)
        ed.rects[key] = track
        if i % cols == cols - 1 or i == len(specs) - 1:
            y = track.bottom + config.ROW_GAP
    return y + config.ROW_GAP


lane_specs = maker.lane_specs
econ_specs = maker.econ_specs


def _draw_problems(surface, ed: Editor, f, x: int, y: int, w: int, bottom: int) -> int:
    """The live validator's output, one clickable row each.

    Clickable because a warning you cannot find is a warning you ignore: a press
    selects the offender and centres the camera on it.
    """
    problems = ed.problems()
    heading = maker.problems_heading(problems)
    widgets.text(surface, f["small"], heading,
                 config.COLOR_TEXT_DIM if not problems else _WARN, topleft=(x, y))
    y += widgets.row_h("small") + config.ROW_GAP

    for index, problem in enumerate(problems):
        lines = widgets.wrap(f["small"], problem.text, w)
        block_h = widgets.row_h("small") * len(lines)
        if y + block_h > bottom:
            widgets.text(surface, f["small"], "...", config.COLOR_TEXT_DIM, topleft=(x, y))
            return y + widgets.row_h("small")
        rect = pygame.Rect(x, y, w, block_h)
        color = _BAD if problem.blocks else _WARN
        for line in lines:
            widgets.text(surface, f["small"], line, color, topleft=(x, y))
            y += widgets.row_h("small")
        ed.problem_rows.append((index, rect))
        y += config.ROW_GAP
    return y


_DEAD_PALETTE = ((38, 42, 52), (78, 84, 98))


_FOOTER_PALETTES = {"amber": widgets.BTN_AMBER, "blue": widgets.BTN_BLUE,
                    "dead": _DEAD_PALETTE, "field": ((0, 0, 0), (0, 0, 0))}


def _footer_specs(ed: Editor | None) -> list[tuple[str, str, tuple, tuple, int, str]]:
    """The strip's controls (`maker.footer`) as (key, label, fill, edge, squeeze
    rank, cluster); ``ed`` only colours *Play now* (None: measuring)."""
    return [(key, label, *_FOOTER_PALETTES[palette], rank, cluster)
            for key, label, palette, rank, cluster in maker.footer(ed)]


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

    # Two rows (`_footer_rows`) put each cluster on its own; either way a row
    # still too narrow sheds its least essential control.
    rows = _footer_rows()
    groups = [specs] if rows == 1 else [[s for s in specs if s[5] == "left"],
                                        [s for s in specs if s[5] == "right"]]
    shown = []
    for group in groups:
        group = list(group)
        while group and row_w(group) > avail:
            group.remove(min(group, key=lambda s: s[4]))
        shown += group

    bh = widgets.tap_size(config.FOOTER_BTN_H)
    y = bar.y + config.HUD_PAD
    y_right = y + (bh + config.BTN_GAP if rows == 2 else 0)

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
        rect = pygame.Rect(right - widths[key], y_right, widths[key], bh)
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


_CONFIRMS = maker.CONFIRMS


def _draw_confirm(surface, ed: Editor) -> None:
    title, detail, yes = maker.confirm_words(ed.confirm)
    yes_label, no_label = widgets.confirm_labels(yes)
    widgets.draw_modal(surface, title, detail,
                       ((yes_label, *widgets.BTN_DANGER), (no_label, *widgets.BTN_BLUE)))
    yes, no = widgets.modal_buttons(surface, (yes_label, no_label))
    ed.rects["confirm_yes"], ed.rects["confirm_no"] = yes, no


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def handle_event(event, ed: Editor, settings: Settings) -> str | None:
    """Mutate ``ed`` (and, for the sliders, ``settings``); return a high-level
    action for ``main`` — ``"play"``, ``"menu"``, or ``None``."""
    action = _dispatch(event, ed, settings)
    _apply_fit(ed)
    return action


def _apply_fit(ed: Editor) -> None:
    """Frame what a press asked the camera to (`maker.focus_problem`)."""
    if ed.fit and ed.view is not None:
        ed.view.fit_to(ed.fit)
    ed.fit = None


def _dispatch(event, ed: Editor, settings: Settings) -> str | None:
    if ed.confirm is not None:
        _handle_confirm(event, ed, settings)
        return None
    if _pinch(event, ed):
        return None
    if _side_scroll_event(event, ed):
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


def _handle_press(event, ed: Editor, settings: Settings) -> str | None:
    pos = event.pos
    if event.button == 3:
        # Right-drag pans. Left-drag cannot: in the Systems tool a press on empty
        # space always means "place", so there is no free left gesture — and
        # making it conditional on legality would give one press two meanings,
        # the trap route mode's tap already documents.
        #
        # `pan_button` is restated here, not just in `_press_lane`: that one sets
        # it to 1, and a pan armed by the right button while `_handle_motion` is
        # still watching button 1 never moves at all.
        if _over_map(pos):
            ed.pan_active, ed.pan_last, ed.pan_button = True, pos, 3
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
    if ed.tool == OWNERS:
        _press_owner(ed, pos)
        return None

    # Shift chains: held down, a click both places (or links) *and* keeps
    # building from what it just touched. Off while Auto-lanes' "re-run after
    # every change" is on, since the network it rebuilds would overwrite the
    # very lane a chained click just drew.
    shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
    node = _pick_node(ed, pos)
    if node is not None:
        maker.press_system(ed, node, shift)
        ed.drag_start = pos
        return None
    _place(ed, settings, pos, link=ed.sel_node if maker.chains(ed, shift) else None)
    return None


def _press_owner(ed: Editor, pos) -> None:
    """A press on the map in the Owners tool (`maker.press_owner`): paint the
    system under it, or arm a box from it."""
    node = _pick_node(ed, pos)
    maker.press_owner(ed, node)
    if node is not None:
        return
    ed.box_press, ed.box_active = True, False
    ed.box_from = ed.box_to = pos
    ed.drag_start = pos


def _press_lane(ed: Editor, pos) -> None:
    """A press on the map in the Lanes tool (`maker.press_lane_tool`): a system
    under it first, else the lanes near it; on nothing at all, a left-drag pans."""
    node = _pick_node(ed, pos)
    if node is not None:
        maker.press_lane_tool(ed, node)
        if ed.lane_src == node:
            ed.lane_pos = pos
            ed.drag_start = pos
        return
    if maker.press_lane_tool(ed, None, _lanes_near(ed, pos)):
        ed.pan_active, ed.pan_last, ed.pan_button = True, pos, 1


def _hit(ed: Editor, pos) -> str | None:
    """The chrome control under ``pos``, if any.

    The single hit-tester, and the single place the "guard every rect test on its
    width" rule is enforced — a zero-sized rect can never be inside anything here,
    however it got recorded.
    """
    return next((key for key, rect in ed.rects.items()
                 if rect.w > 0 and rect.h > 0 and rect.collidepoint(pos)), None)


_chain_lane = maker.chain_lane


def _arm_move(ed: Editor, node: int, pos) -> None:
    """Select a system and arm a move-drag on it from the press at ``pos``."""
    maker.arm_move(ed, node)
    ed.drag_start = pos


def _handle_chrome(hit: str, pos, ed: Editor, settings: Settings) -> str | None:
    """A press on a laid-out control: the toolbar, palette, sidebar or footer."""
    if hit != "filename":
        _stop_editing_filename(ed)
    if hit == "ships_slider":
        if _selected(ed) is not None:
            _push_undo(ed)
            ed.drag_slider = hit
            _set_garrison_slider(ed, pos[0])
        return None
    if hit in _SLIDER_KEYS():
        ed.drag_slider = hit
        _set_slider(ed, settings, hit, pos[0])
        return None
    return _handle_action(hit, ed, settings)


def _handle_action(hit: str, ed: Editor, settings: Settings) -> str | None:
    """The camera's controls and the filename field here; anything else is
    `maker.control`'s."""
    if hit == "zoom_in":
        ed.view.zoom_at(_view_centre(), config.ZOOM_BUTTON_STEP)
    elif hit == "zoom_out":
        ed.view.zoom_at(_view_centre(), 1 / config.ZOOM_BUTTON_STEP)
    elif hit == "zoom_reset":
        ed.view.reset()
    elif hit == "filename":
        ed.editing_filename = True
        softkeyboard.open(ed.filename)
        _start_text_input()
    else:
        return maker.control(ed, settings, hit)
    return None


_play = maker.play


def _handle_motion(event, ed: Editor, settings: Settings) -> None:
    if ed.drag_slider == "ships_slider" and event.buttons[0]:
        _set_garrison_slider(ed, event.pos[0])
        return
    if ed.drag_slider is not None and event.buttons[0]:
        _set_slider(ed, settings, ed.drag_slider, event.pos[0])
        return
    if ed.pan_active and event.buttons[ed.pan_button - 1]:
        ed.view.pan(event.pos[0] - ed.pan_last[0], event.pos[1] - ed.pan_last[1])
        ed.pan_last = event.pos
        return
    if ed.box_press and event.buttons[0]:
        if dist(event.pos, ed.drag_start) > config.DRAG_THRESHOLD:
            ed.box_active = True
        ed.box_to = event.pos
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
    maker.drag_to(ed, ed.view.to_world(event.pos))


def _handle_release(event, ed: Editor) -> None:
    if event.button == 3:
        ed.pan_active = False
        return
    if event.button != 1:
        return
    ed.pan_active = False
    ed.drag_slider = None
    if ed.box_press:
        # Only a box that actually became one paints; a press that never moved was
        # a tap on empty space, which means nothing here.
        if ed.box_active:
            _paint(ed, _nodes_in_box(ed, _box_rect(ed)))
        ed.box_press = ed.box_active = False
        return
    if ed.lane_drag:
        maker.lane_release(ed, _pick_node(ed, event.pos))
        return
    maker.release_drag(ed)


# The keys the editor answers, by their `KeyboardEvent.key` names (`maker.press_key`).
_KEY_NAMES = {pygame.K_z: "z", pygame.K_y: "y", pygame.K_r: "r", pygame.K_ESCAPE: "Escape",
              pygame.K_DELETE: "Delete", pygame.K_BACKSPACE: "Backspace",
              pygame.K_RETURN: "Enter", pygame.K_KP_ENTER: "Enter"}


def _handle_key(event, ed: Editor, settings: Settings) -> str | None:
    if ed.editing_filename:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            _stop_editing_filename(ed)    # commit and cancel are the same here
        elif event.key == pygame.K_BACKSPACE:
            ed.filename = ed.filename[:-1]
        return None
    name = _KEY_NAMES.get(event.key)
    if name is None:
        return None
    action = maker.press_key(ed, settings, name, bool(event.mod & pygame.KMOD_CTRL))
    if action == "reset_view":
        ed.view.reset()
        return None
    return action


def _handle_confirm(event, ed: Editor, settings: Settings) -> None:
    answer: bool | None = None
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
    if answer is not None:
        maker.answer_confirm(ed, settings, answer)


def _handle_text_input(text: str, ed: Editor) -> None:
    maker.type_filename(ed, text)


def pump(ed: Editor) -> None:
    """Per-frame poll of the mobile browser's on-screen keyboard, mirroring
    ``menu.pump``: soft-keyboard typing produces no SDL events at all, so the text
    is read back out of the hidden DOM field instead."""
    if not ed.editing_filename:
        return
    text = maker.filename_text(softkeyboard.value(ed.filename))
    if text != ed.filename:
        ed.filename = text


# --------------------------------------------------------------------------- #
# Editing operations (`maker`'s), by this module's names for them
# --------------------------------------------------------------------------- #
_push_undo = maker.push_undo
_undo = maker.undo
_redo = maker.redo
_adopt = maker.adopt
_rolled_production = maker.rolled_production
_weighted_production = maker.weighted_production
_rolled_garrison = maker.rolled_garrison
_retype_selection = maker.retype_selection
_reroll_selected = maker.reroll_selected
_step_selected = maker.step_selected
_set_owner = maker.set_owner
_pick_seat = maker.pick_seat
_delete_selected = maker.delete_selected
_paint = maker.paint
_make_homeworld = maker.make_homeworld
_auto_owners = maker.auto_owners
_renumber_seats = maker.renumber_seats


def _place(ed: Editor, settings: Settings, pos, link: int | None = None) -> None:
    """Place a system under the press at ``pos`` (`maker.place`), and arm its
    move-drag from there, so place-then-position is one gesture."""
    index = maker.place(ed, settings, ed.view.to_world(pos), link)
    if index is not None:
        ed.drag_start = pos


def _box_rect(ed: Editor) -> tuple[int, int, int, int]:
    (x0, y0), (x1, y1) = ed.box_from, ed.box_to
    return (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))


def _clip_to_view(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Intersect a screen rect with the map viewport — the editor's own
    ``viewstate._clip_to_play``, against ``_view_rect`` rather than
    ``config.play_rect``."""
    rx, ry, rw, rh = rect
    vx, vy, vw, vh = _view_rect()
    x0, y0 = max(rx, vx), max(ry, vy)
    x1, y1 = min(rx + rw, vx + vw), min(ry + rh, vy + vh)
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _nodes_in_box(ed: Editor, rect: tuple[int, int, int, int]) -> list[int]:
    """Every system inside a dragged screen box.

    Clipped to the viewport first, because ``to_screen`` projects *every* system
    including ones panned out under the sidebar or the palette band — only the
    drawing is clipped, so an unclipped box dragged to the edge would quietly pick
    up systems that are not on screen at all.
    """
    bx, by, bw, bh = _clip_to_view(rect)
    if bw <= 0 or bh <= 0:
        return []
    out = []
    for i, node in enumerate(ed.recipe.nodes):
        sx, sy = ed.view.to_screen(node.pos)
        if bx <= sx <= bx + bw and by <= sy <= by + bh:
            out.append(i)
    return out


_lane_candidate = maker.lane_candidate
_add_lane = maker.add_lane
_delete_lane = maker.delete_lane
_clear_lanes = maker.clear_lanes
_relane = maker.relane
_auto_lanes = maker.auto_lanes
_codes = maker.codes
_offenders = maker.offenders
_lane_offenders = maker.lane_offenders
_refresh_bad = maker.refresh_bad
_illegal = maker.illegal
_selected = maker.selected
_selected_lane = maker.selected_lane
_lane_length = maker.lane_length
_lane_turns = maker.lane_turns
_set_status = maker.set_status
age_status = maker.age_status


def _move_node(ed: Editor, index: int, pos) -> None:
    maker.move_node(ed, index, ed.view.to_world(pos))


def _pick_node(ed: Editor, pos) -> int | None:
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


def _lanes_near(ed: Editor, pos) -> list[int]:
    """The lanes within reach of ``pos``, nearest first."""
    hits: list[tuple[float, int]] = []
    for i, (a, b) in enumerate(ed.recipe.lanes):
        d = point_segment_dist(pos, ed.view.to_screen(ed.recipe.nodes[a].pos),
                               ed.view.to_screen(ed.recipe.nodes[b].pos))
        if d <= config.LANE_PICK_DIST:
            hits.append((d, i))
    return [i for _d, i in sorted(hits)]


def _pick_lane(ed: Editor, pos) -> int | None:
    """The lane under ``pos``, nearest first — a repeat press cycling through
    overlapping candidates (`maker.pick_lane`)."""
    return maker.pick_lane(ed, _lanes_near(ed, pos))


def _focus_problem(ed: Editor, index: int) -> None:
    """Select problem ``index``'s offender and centre the camera on it."""
    maker.focus_problem(ed, index)
    _apply_fit(ed)


def _view_centre() -> tuple[int, int]:
    vx, vy, vw, vh = _view_rect()
    return (vx + vw // 2, vy + vh // 2)


# --------------------------------------------------------------------------- #
# Sliders, text entry and the map library
# --------------------------------------------------------------------------- #
_slider_specs = maker.slider_specs
_SLIDER_KEYS = maker.slider_keys


def _set_slider(ed: Editor, settings: Settings, key: str, px: int) -> None:
    """Write a slider's value from a pointer x, inverted through
    ``widgets.slider_fraction`` — the exact inverse of where ``widgets.slider``
    puts the knob."""
    rect = ed.rects.get(key)
    if key not in _SLIDER_KEYS() or rect is None:
        return
    maker.set_slider(settings, key, widgets.slider_fraction(rect, px))


def _set_garrison_slider(ed: Editor, px: int) -> None:
    """Write the Garrison slider's value onto the selected node (`maker.set_garrison`)."""
    rect = ed.rects.get("ships_slider")
    if _selected(ed) is None or rect is None:
        return
    maker.set_garrison(ed, widgets.slider_fraction(rect, px))


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


_map_path = maker.map_path
_save_file = maker.save_file
_open_file = maker.open_file
