"""All drawing. Reads GameState and Ui; never mutates them, never imports the
engine or AI. Deliberately minimalist: circles for systems, lines for lanes,
triangles for fleets, numbers for ship counts.
"""

from __future__ import annotations

import math

import pygame

from . import config, fog, uifont
from .geometry import lerp
from .model import GameState, lane_key
from .viewstate import CHOOSING, SELECTED, Ui

_FONTS: dict[str, pygame.font.Font] = {}


def _fonts() -> dict[str, pygame.font.Font]:
    if not _FONTS:
        _FONTS["small"] = uifont.load(config.FONT_SIZE_SMALL)
        _FONTS["normal"] = uifont.load(config.FONT_SIZE)
        _FONTS["big"] = uifont.load(config.FONT_SIZE_BIG, bold=True)
    return _FONTS


def _text(surface, font, s, color, center=None, topleft=None, midleft=None, midright=None):
    img = font.render(s, True, color)
    rect = img.get_rect()
    if center:
        rect.center = center
    elif topleft:
        rect.topleft = topleft
    elif midleft:
        rect.midleft = midleft
    elif midright:
        rect.midright = midright
    surface.blit(img, rect)
    return rect


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #
def draw(surface: pygame.Surface, state: GameState, ui: Ui) -> None:
    surface.fill(config.COLOR_BG)
    # zero the button rects; whichever count/popup draw runs (if any) re-records them
    ui.minus_rect = ui.plus_rect = (0, 0, 0, 0)
    ui.send_tab_rect = ui.forward_tab_rect = (0, 0, 0, 0)
    ui.send_all_rect = ui.send_half_rect = ui.cancel_rect = (0, 0, 0, 0)
    ui.clear_forward_rect = (0, 0, 0, 0)
    _draw_lanes(surface, state, ui)
    # History mode reviews a reconstructed past board: skip the live-interaction
    # overlays (queued/standing orders, the count being composed) — there is no
    # order entry while scrubbing.
    if not ui.history:
        _draw_forward_rules(surface, state, ui)     # standing auto-forward (dashed)
        _draw_pending(surface, state, ui)           # queued one-shot sends (solid)
        _draw_choosing_preview(surface, state, ui)  # the arrow you're adjusting now
    _draw_fleets(surface, state, ui)
    _draw_systems(surface, state, ui)
    if not ui.history and ui.drag_active and ui.drag_src is not None:
        _draw_drag(surface, state, ui)
    if not ui.history:
        # the active count + −/+ buttons (and the send popup) draw last of the map
        # layer so nodes/fleets never occlude them (they must stay visible/clickable)
        if ui.mode == CHOOSING:
            _draw_send_popup(surface, state, ui)
        else:
            _draw_count_controls(surface, state, ui)
    _draw_hud(surface, state, ui)
    if ui.history:
        # the scrubber owns the bottom bar; suppress the win overlay so a finished
        # game's final-turn snapshot doesn't veil the board being reviewed
        _draw_scrubber(surface, state, ui)
    elif state.winner is not None:
        _draw_win_overlay(surface, state, ui)


# --------------------------------------------------------------------------- #
# World layer
# --------------------------------------------------------------------------- #
def _fog_state(ui: Ui, sid: int) -> str:
    """Fog-of-war state of a system from the human's viewpoint: ``"visible"`` (full
    detail), ``"fogged"`` (grey "?" — currently scouted or remembered), or
    ``"hidden"`` (never seen, not drawn). With fog off, ``visible`` holds every
    system so this is always ``"visible"``."""
    if sid in ui.visible:
        return "visible"
    if sid in ui.seen:
        return "fogged"
    return "hidden"


def _draw_drag(surface, state: GameState, ui: Ui) -> None:
    """Rubber-band line while dragging from a source system toward a target, with
    a ring on a valid adjacent target under the finger."""
    if ui.drag_src not in state.systems:
        return
    a = ui.view.to_screen(state.systems[ui.drag_src].pos)
    col = config.player_color(ui.human_id)
    pygame.draw.line(surface, col, a, ui.drag_pos, max(2, config.s(3)))
    pygame.draw.circle(surface, col, ui.drag_pos, max(3, config.s(5)), 2)
    tgt = ui.hover
    if (tgt is not None and tgt != ui.drag_src and tgt in state.systems
            and state.are_adjacent(ui.drag_src, tgt)):
        tp = ui.view.to_screen(state.systems[tgt].pos)
        r = config.node_radius(state.systems[tgt].production) + config.s(4)
        pygame.draw.circle(surface, config.COLOR_SELECT, tp, r, max(2, config.s(2)))


def _draw_lanes(surface, state: GameState, ui: Ui) -> None:
    for lane in state.lanes.values():
        sa, sb = _fog_state(ui, lane.a), _fog_state(ui, lane.b)
        # a lane to a never-seen system is itself unknown — don't draw it
        if sa == "hidden" or sb == "hidden":
            continue
        pa = ui.view.to_screen(state.systems[lane.a].pos)
        pb = ui.view.to_screen(state.systems[lane.b].pos)
        # width & brightness encode travel time: slow lanes thicker+dimmer,
        # fast lanes thinner+brighter, so length variety reads at a glance.
        width, color = _lane_style(lane.travel_turns)
        # a lane touching a fogged system reads as uncertain: mute it to the fog
        # colour (its travel time — route topology — is still shown below)
        if sa != "visible" or sb != "visible":
            color = config.COLOR_FOG
        # brighten the lanes touching the selected source system
        if ui.selected is not None and ui.selected in (lane.a, lane.b):
            color = config.COLOR_LANE_HILITE
            width = max(width, 3)
        pygame.draw.line(surface, color, pa, pb, width)
        # travel time is detailed intel — only show it where an endpoint is in
        # full view; a lane between two fogged systems draws as a bare dim line
        if sa == "visible" or sb == "visible":
            mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
            _label_pill(surface, _fonts()["small"], str(lane.travel_turns),
                        config.COLOR_TEXT_DIM, mid)


def _lane_style(travel_turns: int) -> tuple[int, tuple[int, int, int]]:
    """Width (px) and colour for a lane from its travel time. Fast = thin+bright.

    Thickness tracks travel time directly (clamped) so the spread reads at a
    glance; colour brightens the quick lanes and mutes the slow ones.
    """
    width = max(2, min(6, travel_turns))       # 2px (fast) .. 6px (slow)
    f = (min(6, max(1, travel_turns)) - 1) / 5.0  # 0 fast .. 1 slow
    scale = 1.3 - 0.6 * f                        # 1.3x (bright) .. 0.7x (dim)
    color = tuple(min(255, int(c * scale)) for c in config.COLOR_LANE)
    return width, color


def _label_pill(surface, font, s: str, color, center) -> None:
    """Draw text centred on a small dark rounded rect so it reads over any line."""
    img = font.render(s, True, color)
    rect = img.get_rect(center=center)
    pill = rect.inflate(8, 4)
    pygame.draw.rect(surface, config.COLOR_BG, pill, border_radius=5)
    surface.blit(img, rect)


def _lane_offsets(state: GameState) -> dict[int, tuple[int, int]]:
    """Assign each in-transit fleet a small perpendicular offset so stacks split."""
    groups: dict[frozenset[int], list[int]] = {}
    for i, f in enumerate(state.fleets):
        groups.setdefault(frozenset((f.source_id, f.dest_id)), []).append(i)
    offset: dict[int, tuple[int, int]] = {}
    for key, idxs in groups.items():
        for rank, i in enumerate(idxs):
            offset[i] = (rank - (len(idxs) - 1) / 2, 0)  # perpendicular rank, scaled later
    return offset


def _draw_fleets(surface, state: GameState, ui: Ui) -> None:
    offsets = _lane_offsets(state)
    for i, f in enumerate(state.fleets):
        # your own fleets always show; an enemy fleet shows only where at least
        # one end of its lane is in full view, so rival movements appear only as
        # they near your space
        if (f.owner_id != ui.human_id
                and f.source_id not in ui.visible and f.dest_id not in ui.visible):
            continue
        a = state.systems[f.source_id].pos
        b = state.systems[f.dest_id].pos
        wp = lerp(a, b, f.progress())
        x, y = ui.view.to_screen(wp)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        px, py = -uy, ux  # perpendicular
        rank = offsets.get(i, (0, 0))[0]
        x += int(px * rank * 12)
        y += int(py * rank * 12)
        _draw_triangle(surface, (x, y), (ux, uy), config.player_color(f.owner_id))
        _text(surface, _fonts()["small"], str(f.ships), config.COLOR_TEXT,
              center=(x + int(px * 12), y + int(py * 12)))


def _draw_triangle(surface, center, direction, color) -> None:
    ux, uy = direction
    px, py = -uy, ux
    s = config.FLEET_SIZE
    tip = (center[0] + ux * s, center[1] + uy * s)
    left = (center[0] - ux * s + px * s * 0.7, center[1] - uy * s + py * s * 0.7)
    right = (center[0] - ux * s - px * s * 0.7, center[1] - uy * s - py * s * 0.7)
    pygame.draw.polygon(surface, color, [tip, left, right])


def _draw_pending(surface, state: GameState, ui: Ui) -> None:
    for i, o in enumerate(ui.pending):
        pa = ui.view.to_screen(state.systems[o.source_id].pos)
        pb = ui.view.to_screen(state.systems[o.dest_id].pos)
        # the order being edited is drawn in the select colour, brighter+thicker
        selected = i == ui.sel_order
        color = config.COLOR_SELECT if selected else config.player_color(ui.human_id)
        pygame.draw.line(surface, color, pa, pb, 5 if selected else 3)
        # arrowhead near the destination
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy) or 1.0
        u = (dx / length, dy / length)
        head = (pb[0] - u[0] * 20, pb[1] - u[1] * 20)
        _draw_triangle(surface, head, u, color)
        # place the count 40% of the way toward the destination, not the midpoint,
        # so two opposite-direction orders on the same lane don't overlap labels
        # the count for the order being edited (selected) is drawn later, on top,
        # by _draw_count_controls (with −/+ buttons); others get a plain label here
        if not selected:
            lx = int(pa[0] + (pb[0] - pa[0]) * 0.4)
            ly = int(pa[1] + (pb[1] - pa[1]) * 0.4)
            _text(surface, _fonts()["small"], str(o.ships), config.COLOR_TEXT, center=(lx, ly - 10))


def _draw_forward_rules(surface, state: GameState, ui: Ui) -> None:
    """Standing auto-forward rules as persistent dashed arrows (human colour).

    The rule being edited is drawn in the select colour, brighter+thicker —
    same convention as `_draw_pending`'s selected queued order.
    """
    for src, (dest, keep) in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id or dest not in state.systems:
            continue
        selected = src == ui.sel_forward
        color = config.COLOR_SELECT if selected else config.player_color(ui.human_id)
        pa = ui.view.to_screen(s.pos)
        pb = ui.view.to_screen(state.systems[dest].pos)
        _draw_dashed_line(surface, color, pa, pb, width=3 if selected else 2)
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy) or 1.0
        u = (dx / length, dy / length)
        _draw_triangle(surface, (pb[0] - u[0] * 20, pb[1] - u[1] * 20), u, color)
        # the selected rule's keep is drawn later (on top, with −/+ buttons) by
        # _draw_count_controls; others get a plain dim label here
        if not selected:
            _label_pill(surface, _fonts()["small"], f"keep {keep}", config.COLOR_TEXT_DIM,
                        ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 + 10))


def _draw_choosing_preview(surface, state: GameState, ui: Ui) -> None:
    """The move being composed right now: a bright arrow. The live count and its
    −/+ buttons are drawn separately (on top) by _draw_count_controls.
    """
    if ui.mode != CHOOSING or ui.selected is None or ui.dest is None:
        return
    pa = ui.view.to_screen(state.systems[ui.selected].pos)
    pb = ui.view.to_screen(state.systems[ui.dest].pos)
    color = config.COLOR_SELECT
    pygame.draw.line(surface, color, pa, pb, 3)
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dx, dy) or 1.0
    u = (dx / length, dy / length)
    _draw_triangle(surface, (pb[0] - u[0] * 20, pb[1] - u[1] * 20), u, color)


def _draw_count_controls(surface, state: GameState, ui: Ui) -> None:
    """Draw the active ship-count label + −/+ buttons on top of the map layer so
    nodes and fleets never occlude them. Handles the queued order being edited
    via a lane/list click, and the standing rule's keep being edited; records the
    button hit-rects. (The active send in CHOOSING mode gets the fuller popup
    instead — see _draw_send_popup.)"""
    if ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
        o = ui.pending[ui.sel_order]
        pa = ui.view.to_screen(state.systems[o.source_id].pos)
        pb = ui.view.to_screen(state.systems[o.dest_id].pos)
        lx = int(pa[0] + (pb[0] - pa[0]) * 0.4)
        ly = int(pa[1] + (pb[1] - pa[1]) * 0.4)
        _draw_count_stepper(surface, (lx, ly - 10), o.ships, config.COLOR_SELECT, ui)
    elif ui.sel_forward is not None and ui.sel_forward in ui.auto_forward:
        src = ui.sel_forward
        dest, keep = ui.auto_forward[src]
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id or dest not in state.systems:
            return  # a dormant/unowned rule isn't drawn, so it gets no buttons
        pa = ui.view.to_screen(s.pos)
        pb = ui.view.to_screen(state.systems[dest].pos)
        center = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 + 10)
        _draw_count_stepper(surface, center, keep, config.COLOR_SELECT, ui, label=f"keep {keep}")


def _popup_anchor(surface, state: GameState, ui: Ui, mid, w: int, h: int) -> tuple[int, int]:
    """Auto-place the popup: try the four sides of the lane midpoint (preferring
    above, then right/left/below), clamp each into the play area, and pick the one
    covering the fewest system nodes so it stays clear of useful info. The user can
    still drag it elsewhere (see ui.popup_pos)."""
    sw, sh = surface.get_size()
    x_lo, x_hi = 0, sw - config.HUD_RIGHT_W - w
    y_lo, y_hi = config.HUD_TOP_H + 2, sh - config.HUD_BOTTOM_H - h
    mx, my = mid
    m = 14
    candidates = (
        (mx - w // 2, my - h - m),    # above (preferred — matches the old anchor)
        (mx + m, my - h // 2),        # right
        (mx - w - m, my - h // 2),    # left
        (mx - w // 2, my + m),        # below
    )
    nodes = [(ui.view.to_screen(s.pos), config.node_radius(s.production) + 6)
             for s in state.systems.values()]
    best, best_score = (x_lo, y_lo), None
    for cx, cy in candidates:
        x = _clamp(cx, x_lo, x_hi)
        y = _clamp(cy, y_lo, y_hi)
        rect = pygame.Rect(x, y, w, h)
        score = sum(1 for (px, py), r in nodes
                    if rect.inflate(2 * r, 2 * r).collidepoint(px, py))
        if best_score is None or score < best_score:
            best, best_score = (x, y), score
            if score == 0:
                break                 # a fully clear spot — take it
    return best


def _draw_send_popup(surface, state: GameState, ui: Ui) -> None:
    """The on-map action panel opened when a destination is picked. Send/Forward
    tabs choose between a one-shot send (auto-committed, default send-all, retuned
    with −/+, Half, All) and a standing forward rule (−/+ sets ships to keep). A
    Cancel button at the bottom discards the active send/rule on either tab.
    Records every button's hit-rect on ``ui`` for input (store-rect-then-test)."""
    if ui.selected is None or ui.dest is None:
        return
    pa = ui.view.to_screen(state.systems[ui.selected].pos)
    pb = ui.view.to_screen(state.systems[ui.dest].pos)
    mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)

    dest = state.systems[ui.dest]
    garrison = state.systems[ui.selected].ships
    font = _fonts()["small"]
    pad, gap, bh = config.SEND_POPUP_PAD, config.SEND_POPUP_GAP, config.SEND_POPUP_BTN_H
    w = config.SEND_POPUP_W
    # Fixed layout — the same six rows on either tab so the box never resizes:
    # tabs, title, effect caption, stepper, two presets, cancel.
    rows = 6
    h = pad * 2 + bh * rows + gap * (rows - 1)

    # placement: honour a user-dragged position (clamped to stay reachable),
    # else auto-anchor to whichever side of the lane covers the fewest nodes
    sw, sh = surface.get_size()
    x_lo, x_hi = 0, sw - config.HUD_RIGHT_W - w
    y_lo, y_hi = config.HUD_TOP_H + 2, sh - config.HUD_BOTTOM_H - h
    if ui.popup_pos is not None:
        x = _clamp(ui.popup_pos[0], x_lo, x_hi)
        y = _clamp(ui.popup_pos[1], y_lo, y_hi)
    else:
        x, y = _popup_anchor(surface, state, ui, mid, w, h)
    ui.popup_rect = (x, y, w, h)

    panel = pygame.Rect(x, y, w, h)
    pygame.draw.rect(surface, (20, 24, 36), panel, border_radius=8)
    accent = _forward_accent() if ui.forward_armed else config.COLOR_SELECT
    pygame.draw.rect(surface, accent, panel, 1, border_radius=8)

    inner = x + pad
    iw = w - pad * 2
    tw = (iw - gap) // 2                              # half-width for paired buttons
    cy = y + pad

    # tab row: Send | Forward — drawn as tabs (active one blends into the body
    # below; a separator line under the row is broken beneath the active tab)
    send_tab = pygame.Rect(inner, cy, tw, bh)
    fwd_tab = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
    _draw_tab(surface, send_tab, "Send", not ui.forward_armed, config.COLOR_SELECT)
    _draw_tab(surface, fwd_tab, "Forward", ui.forward_armed, _forward_accent())
    active = send_tab if not ui.forward_armed else fwd_tab
    line_y = cy + bh
    pygame.draw.line(surface, (70, 80, 104), (inner, line_y), (inner + iw, line_y), 1)
    pygame.draw.line(surface, (20, 24, 36), (active.left, line_y), (active.right, line_y), 1)
    ui.send_tab_rect = (send_tab.x, send_tab.y, send_tab.w, send_tab.h)
    ui.forward_tab_rect = (fwd_tab.x, fwd_tab.y, fwd_tab.w, fwd_tab.h)
    cy += bh + gap

    # title: source -> destination on the left, destination garrison right-aligned
    _text(surface, font, f"Sys {ui.selected} -> {ui.dest}", accent,
          midleft=(inner, cy + bh // 2))
    ships_lbl = f"{dest.ships}sh"
    _text(surface, font, ships_lbl, config.player_color(dest.owner_id),
          midleft=(inner + iw - font.size(ships_lbl)[0], cy + bh // 2))
    cy += bh + gap

    # effect caption: what this actually does, in plain words (dim)
    if ui.forward_armed:
        effect = f"~{max(0, garrison - ui.keep)}/turn  ·  keep {ui.keep}"
    else:
        effect = f"send {ui.chosen}  ·  {max(0, garrison - ui.chosen)} home"
    _text(surface, font, effect, config.COLOR_TEXT_DIM, center=(inner + iw // 2, cy + bh // 2))
    cy += bh + gap

    # stepper row: [−]  value  [+]  — the send count, or (Forward) how many to keep
    s = config.STEPPER_SIZE
    minus = pygame.Rect(inner, cy + (bh - s) // 2, s, s)
    plus = pygame.Rect(inner + iw - s, cy + (bh - s) // 2, s, s)
    _draw_step_button(surface, minus, "-", accent)
    _draw_step_button(surface, plus, "+", accent)
    label = f"keep {ui.keep}" if ui.forward_armed else str(ui.chosen)
    _text(surface, _fonts()["normal"], label, config.COLOR_TEXT,
          center=(inner + iw // 2, cy + bh // 2))
    ui.minus_rect = (minus.x, minus.y, minus.w, minus.h)
    ui.plus_rect = (plus.x, plus.y, plus.w, plus.h)
    cy += bh + gap

    # preset row: Half/All (Send) or Keep half/Keep 0 (Forward) — left, right map
    # to send_half_rect / send_all_rect on both tabs (input reads the mode). The
    # preset matching the current value lights up in the mode accent.
    left = pygame.Rect(inner, cy, tw, bh)
    right = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
    if ui.forward_armed:
        half_keep = garrison // 2
        _draw_popup_button(surface, left, "Keep half", ui.keep == half_keep, accent=accent)
        _draw_popup_button(surface, right, "Keep 0", ui.keep == 0, accent=accent)
    else:
        cap = ui._active_cap(state)
        _draw_popup_button(surface, left, "Half", ui.chosen == max(1, cap // 2), accent=accent)
        _draw_popup_button(surface, right, "All", ui.chosen == cap, accent=accent)
    ui.send_half_rect = (left.x, left.y, left.w, left.h)
    ui.send_all_rect = (right.x, right.y, right.w, right.h)
    cy += bh + gap

    # Cancel row (both tabs): discard the active send / forward rule
    cancel = pygame.Rect(inner, cy, iw, bh)
    _draw_popup_button(surface, cancel, "Cancel", danger=True)
    ui.cancel_rect = (cancel.x, cancel.y, cancel.w, cancel.h)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v)) if hi >= lo else lo


def _draw_popup_button(surface, rect: pygame.Rect, label: str,
                       active: bool = False, danger: bool = False,
                       accent=config.COLOR_SELECT) -> None:
    """A small labelled button in the send popup. ``active`` lights it up in the
    mode ``accent`` (the preset matching the current value); ``danger`` tints it
    red (Cancel); otherwise it's a plain recessed button."""
    if active:
        fill = tuple(c * 3 // 10 for c in accent)     # darkened accent wash
        edge, col = accent, config.COLOR_TEXT
    elif danger:
        fill, edge, col = (58, 38, 42), (170, 96, 104), config.COLOR_TEXT
    else:
        fill, edge, col = (30, 36, 52), (70, 80, 104), config.COLOR_TEXT_DIM
    pygame.draw.rect(surface, fill, rect, border_radius=5)
    pygame.draw.rect(surface, edge, rect, 1, border_radius=5)
    _text(surface, _fonts()["small"], label, col, center=rect.center)


def _forward_accent() -> tuple[int, int, int]:
    """Accent colour for Forward mode — a teal, distinct from the send yellow."""
    return (110, 205, 195)


def _draw_tab(surface, rect: pygame.Rect, label: str, active: bool, accent) -> None:
    """A tab in the send popup's Send/Forward switcher. The active tab takes the
    panel-body fill (so it reads as connected to the body) with a bright top
    accent in its mode colour; inactive tabs are darker and dim-labelled."""
    if active:
        pygame.draw.rect(surface, (20, 24, 36), rect,
                         border_top_left_radius=6, border_top_right_radius=6)
        pygame.draw.line(surface, accent,
                         (rect.left + 2, rect.top + 1), (rect.right - 2, rect.top + 1), 2)
        col = accent
    else:
        pygame.draw.rect(surface, (12, 14, 22), rect,
                         border_top_left_radius=6, border_top_right_radius=6)
        col = config.COLOR_TEXT_DIM
    _text(surface, _fonts()["small"], label, col, center=rect.center)


def _draw_count_stepper(surface, center, value, color, ui: Ui, label: str | None = None) -> None:
    """A count label flanked by clickable −/+ buttons, for adjusting the count
    without a mouse wheel. ``label`` overrides the shown text (e.g. "keep 3" for a
    rule); it defaults to the bare number. Records the button rects on ``ui`` so
    input can hit-test them (same store-rect-then-test handoff as end_turn_rect)."""
    font = _fonts()["normal"]
    img = font.render(label if label is not None else str(value), True, color)
    lbl = img.get_rect(center=center)
    pill = lbl.inflate(10, 4)
    pygame.draw.rect(surface, config.COLOR_BG, pill, border_radius=5)
    surface.blit(img, lbl)

    s = config.STEPPER_SIZE
    gap = 4
    top = center[1] - s // 2
    minus = pygame.Rect(pill.left - gap - s, top, s, s)
    plus = pygame.Rect(pill.right + gap, top, s, s)
    _draw_step_button(surface, minus, "-", color)
    _draw_step_button(surface, plus, "+", color)
    ui.minus_rect = (minus.x, minus.y, minus.w, minus.h)
    ui.plus_rect = (plus.x, plus.y, plus.w, plus.h)


def _draw_step_button(surface, rect: pygame.Rect, sign: str, color) -> None:
    """A small filled −/+ button glyph inside ``rect``."""
    pygame.draw.rect(surface, config.COLOR_BG, rect, border_radius=4)
    pygame.draw.rect(surface, color, rect, 1, border_radius=4)
    cx, cy = rect.center
    r = rect.w // 4
    pygame.draw.line(surface, color, (cx - r, cy), (cx + r, cy), 2)   # − (and +'s bar)
    if sign == "+":
        pygame.draw.line(surface, color, (cx, cy - r), (cx, cy + r), 2)


def _draw_return_glyph(surface, rect, color) -> None:
    """A drawn ⏎ return/enter arrow inside ``rect`` (x, y, w, h). Drawn rather
    than typed because the monospace font lacks the ⏎ glyph on many platforms."""
    x, y, w, h = rect
    by = y + int(h * 0.72)                       # baseline of the horizontal stroke
    a = max(3, h // 4)                            # arrowhead arm length
    # down-stroke on the right, then left along the baseline to the arrow tip
    pygame.draw.lines(surface, color, False, [(x + w, y), (x + w, by), (x, by)], 2)
    # arrowhead pointing left
    pygame.draw.lines(surface, color, False,
                      [(x + a, by - a), (x, by), (x + a, by + a)], 2)


def _draw_dashed_line(surface, color, a, b, width=2, dash=10, gap=8) -> None:
    x1, y1 = a
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    s = 0.0
    while s < length:
        e = min(s + dash, length)
        pygame.draw.line(surface, color, (x1 + ux * s, y1 + uy * s),
                         (x1 + ux * e, y1 + uy * e), width)
        s += dash + gap


def _draw_systems(surface, state: GameState, ui: Ui) -> None:
    valid_dests = set()
    if ui.selected is not None:
        valid_dests = set(state.systems[ui.selected].neighbors)

    for sys in state.systems.values():
        state_fog = _fog_state(ui, sys.id)
        if state_fog == "hidden":       # never seen — off the map entirely
            continue
        pos = ui.view.to_screen(sys.pos)
        radius = config.node_radius(sys.production)

        # selection / targeting rings — drawn even when fogged so a "?" system you
        # can still route a fleet to reads as selected / a valid destination
        if sys.id == ui.selected:
            pygame.draw.circle(surface, config.COLOR_SELECT, pos, radius + 6, 3)
        elif sys.id in valid_dests:
            pygame.draw.circle(surface, config.COLOR_LANE_HILITE, pos, radius + 4, 2)
        elif sys.id == ui.hover:
            pygame.draw.circle(surface, config.COLOR_TEXT_DIM, pos, radius + 4, 2)

        if state_fog == "fogged":       # position known, contents not: hatched "?"
            pygame.draw.circle(surface, config.COLOR_FOG, pos, radius)
            pygame.draw.circle(surface, _brighten(config.COLOR_FOG), pos, radius, 2)
            _draw_hatch(surface, pos, radius, _brighten(config.COLOR_FOG))
            _text(surface, _fonts()["normal"], "?", config.COLOR_TEXT, center=pos)
            continue

        # -- visible: full detail --
        color = config.player_color(sys.owner_id)
        pygame.draw.circle(surface, color, pos, radius)
        pygame.draw.circle(surface, _brighten(color), pos, radius, 2)

        # production-progress ring
        if sys.production > 0 and (sys.owner_id != 0 or config.NEUTRAL_PRODUCES):
            frac = sys.prod_progress / sys.production
            if frac > 0:
                rect = pygame.Rect(0, 0, radius * 2 + 8, radius * 2 + 8)
                rect.center = pos
                pygame.draw.arc(surface, config.COLOR_TEXT_DIM, rect,
                                math.pi / 2, math.pi / 2 + 2 * math.pi * frac, 2)

        # ship count (deployable = garrison minus queued commitments), drawn in
        # whichever of dark/light text contrasts best with this owner's colour
        shown = ui.available(state, sys.id) if sys.owner_id == ui.human_id else sys.ships
        _text(surface, _fonts()["normal"], str(shown), config.text_on(color), center=pos)


def _brighten(color, amount=60):
    return tuple(min(255, c + amount) for c in color)


def _draw_hatch(surface, center, radius, color, step=6) -> None:
    """Fill a node circle with diagonal hatching — marks a fogged system so it
    reads as 'uncertain' and never looks like a solid neutral disc. Each 45° line
    is chord-clipped to the disc of ``radius`` about ``center``."""
    cx, cy = center
    r = max(1, radius - 1)
    lim = r * math.sqrt(2)
    k = -lim
    while k <= lim:
        disc = 2 * r * r - k * k
        if disc > 0:
            root = math.sqrt(disc) / 2
            mid = -k / 2
            u1, u2 = mid - root, mid + root
            pygame.draw.line(surface, color,
                             (int(cx + u1), int(cy + u1 + k)),
                             (int(cx + u2), int(cy + u2 + k)), 1)
        k += step


def _hatch_rect(surface, rect: pygame.Rect, color, step=4) -> None:
    """Diagonal hatching across a small rect (a stale scoreboard swatch), clipped
    to the rect so it never bleeds into the neighbouring text."""
    surface.set_clip(rect)
    x = rect.left - rect.height
    while x < rect.right:
        pygame.draw.line(surface, color, (x, rect.bottom), (x + rect.height, rect.top), 1)
        x += step
    surface.set_clip(None)


# --------------------------------------------------------------------------- #
# HUD
# --------------------------------------------------------------------------- #
def _draw_hud(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()

    _draw_side_panel(surface, state, ui)

    # top bar
    pygame.draw.rect(surface, (18, 20, 30), (0, 0, w, config.HUD_TOP_H))
    _text(surface, _fonts()["normal"], f"Turn {state.turn}", config.COLOR_TEXT,
          midleft=(14, config.HUD_TOP_H // 2))
    _draw_scoreboard(surface, state, ui, w)

    # bottom bar
    by = h - config.HUD_BOTTOM_H
    pygame.draw.rect(surface, (18, 20, 30), (0, by, w, config.HUD_BOTTOM_H))
    if ui.history:
        # the scrubber (drawn by _draw_scrubber, after the HUD) owns the bottom
        # bar in history mode — zero the live buttons so no stale click resolves.
        ui.end_turn_rect = ui.play_pause_rect = ui.history_button_rect = (0, 0, 0, 0)
        ui.autoplay_button_rect = ui.restart_live_button_rect = ui.menu_button_rect = (0, 0, 0, 0)
        ui.quit_button_rect = ui.clear_button_rect = (0, 0, 0, 0)
        return

    # End-turn button: the single biggest, easiest touch target in the HUD — the
    # full width of the right info panel, reaching up above the ordinary bottom
    # bar (_draw_side_panel/_draw_order_list reserve the same config.END_TURN_H
    # so the queued-orders list never draws underneath it).
    ebw, ebh = config.HUD_RIGHT_W, config.END_TURN_H
    br = pygame.Rect(w - ebw, h - ebh, ebw, ebh)
    ui.end_turn_rect = (br.x, br.y, br.w, br.h)
    pygame.draw.rect(surface, (46, 92, 60), br, border_radius=config.s(8))
    pygame.draw.rect(surface, (96, 190, 120), br, config.s(3), border_radius=config.s(8))
    if ui.autoplay:
        _text(surface, _fonts()["big"], "AUTO", config.COLOR_TEXT, center=br.center)
    else:
        # "End Turn" + a drawn return-arrow icon: the monospace font has no ⏎
        # glyph (renders as tofu), so we draw the shortcut hint instead.
        font = _fonts()["big"]
        tw = font.size("End Turn")[0]
        gw, gap = config.s(26), config.s(10)
        left = br.centerx - (tw + gap + gw) // 2
        _text(surface, font, "End Turn", config.COLOR_TEXT, midleft=(left, br.centery))
        _draw_return_glyph(surface, (left + tw + gap, br.centery - config.s(9), gw, config.s(18)),
                           config.COLOR_TEXT)

    # Remaining bottom-bar buttons live in the map's footer strip, left of the
    # sidebar / End Turn column — touch equivalents of the P / A / H / R / M / X
    # keys (plus Quit/Esc), so every keyboard-only live-play action is reachable
    # on a touchscreen.
    fbh = config.FOOTER_BTN_H
    fy = by + (config.HUD_BOTTOM_H - fbh) // 2
    gap = config.s(10)
    right = w - config.HUD_RIGHT_W - config.s(12)

    # play/pause — hidden while autoplay, which drives turns on its own timer
    # and would make this a no-op.
    if ui.autoplay:
        ui.play_pause_rect = (0, 0, 0, 0)
    else:
        pw = config.s(120)
        pr = pygame.Rect(right - pw, fy, pw, fbh)
        ui.play_pause_rect = (pr.x, pr.y, pr.w, pr.h)
        fill = (92, 70, 46) if ui.playing else (40, 52, 78)
        edge = (190, 150, 96) if ui.playing else (110, 140, 200)
        pygame.draw.rect(surface, fill, pr, border_radius=config.s(6))
        pygame.draw.rect(surface, edge, pr, config.s(2), border_radius=config.s(6))
        plabel = "Pause (P)" if ui.playing else "Play (P)"
        _text(surface, _fonts()["normal"], plabel, config.COLOR_TEXT, center=pr.center)
        right = pr.x

    # autoplay toggle — hands the human seat's decisions to the AI, or (while
    # autoplay is on) takes control back. Shown either way, unlike play/pause.
    aw = config.s(140)
    ar = pygame.Rect(right - gap - aw, fy, aw, fbh)
    ui.autoplay_button_rect = (ar.x, ar.y, ar.w, ar.h)
    afill = (92, 70, 46) if ui.autoplay else (40, 52, 78)
    aedge = (190, 150, 96) if ui.autoplay else (110, 140, 200)
    pygame.draw.rect(surface, afill, ar, border_radius=config.s(6))
    pygame.draw.rect(surface, aedge, ar, config.s(2), border_radius=config.s(6))
    alabel = "Take control (A)" if ui.autoplay else "Autoplay (A)"
    _text(surface, _fonts()["small"], alabel, config.COLOR_TEXT, center=ar.center)
    right = ar.x

    # history — only meaningful once a turn has been recorded to scrub through.
    if state.turn > 0:
        hw = config.s(118)
        hr = pygame.Rect(right - gap - hw, fy, hw, fbh)
        ui.history_button_rect = (hr.x, hr.y, hr.w, hr.h)
        pygame.draw.rect(surface, (52, 46, 78), hr, border_radius=config.s(6))
        pygame.draw.rect(surface, (150, 130, 200), hr, config.s(2), border_radius=config.s(6))
        _text(surface, _fonts()["normal"], "History (H)", config.COLOR_TEXT, center=hr.center)
        right = hr.x
    else:
        ui.history_button_rect = (0, 0, 0, 0)

    # new map — touch equivalent of R, which reseeds mid-game too (not just at
    # game end); no confirmation, matching the keyboard shortcut exactly.
    nw = config.s(128)
    nr = pygame.Rect(right - gap - nw, fy, nw, fbh)
    ui.restart_live_button_rect = (nr.x, nr.y, nr.w, nr.h)
    pygame.draw.rect(surface, (120, 86, 46), nr, border_radius=config.s(6))
    pygame.draw.rect(surface, (200, 150, 96), nr, config.s(2), border_radius=config.s(6))
    _text(surface, _fonts()["normal"], "New map (R)", config.COLOR_TEXT, center=nr.center)
    right = nr.x

    # menu — touch equivalent of M, back to the setup menu.
    mw = config.s(110)
    mr = pygame.Rect(right - gap - mw, fy, mw, fbh)
    ui.menu_button_rect = (mr.x, mr.y, mr.w, mr.h)
    pygame.draw.rect(surface, (40, 52, 78), mr, border_radius=config.s(6))
    pygame.draw.rect(surface, (110, 140, 200), mr, config.s(2), border_radius=config.s(6))
    _text(surface, _fonts()["normal"], "Menu (M)", config.COLOR_TEXT, center=mr.center)
    right = mr.x

    # clear/cancel — touch equivalent of X (and Backspace/Delete): discards the
    # send being adjusted, or drops whichever queued order/forward rule is
    # highlighted. A no-op tap when nothing is selected, same as the key.
    cw_ = config.s(96)
    cr = pygame.Rect(right - gap - cw_, fy, cw_, fbh)
    ui.clear_button_rect = (cr.x, cr.y, cr.w, cr.h)
    pygame.draw.rect(surface, (40, 52, 78), cr, border_radius=config.s(6))
    pygame.draw.rect(surface, (110, 140, 200), cr, config.s(2), border_radius=config.s(6))
    _text(surface, _fonts()["normal"], "Clear (X)", config.COLOR_TEXT, center=cr.center)
    right = cr.x

    # quit — the only touch equivalent of Esc's quit; without it a touch/web
    # player without a keyboard has no way to leave the app at all. Opens the
    # same confirm-quit modal as Esc, not an immediate quit.
    qw = config.s(100)
    qr = pygame.Rect(right - gap - qw, fy, qw, fbh)
    ui.quit_button_rect = (qr.x, qr.y, qr.w, qr.h)
    pygame.draw.rect(surface, (92, 46, 52), qr, border_radius=config.s(6))
    pygame.draw.rect(surface, (200, 96, 104), qr, config.s(2), border_radius=config.s(6))
    _text(surface, _fonts()["normal"], "Quit (Esc)", config.COLOR_TEXT, center=qr.center)


_DEAD_COLOR = (92, 96, 110)


def _draw_scoreboard(surface, state: GameState, ui: Ui, w: int) -> None:
    """Per-player standings in the top bar: swatch, systems, ships, production.

    Laid out left-to-right by measured width so it never runs off-screen; names
    are shown when the whole row fits, but a full six-player table drops them in
    favour of the colour swatch (identity is colour-coded everywhere else too).

    Fogged (per the human's visibility): a rival is **live** while any of its
    systems is in sight, **frozen** (last-known stats, hatched swatch + "?") once
    seen but no longer in sight, and **omitted entirely** until first sighted — so
    with heavy fog you may not even know how many rivals are out there. With fog
    off every living rival is in sight, so this matches the classic scoreboard.
    """
    font = _fonts()["small"]
    cy = config.HUD_TOP_H // 2
    gap, sw_w = 22, 18

    def vis(pid: int) -> str:
        """live | frozen | out | hidden."""
        p = state.players[pid]
        live = pid == ui.human_id or any(state.systems[s].owner_id == pid for s in ui.visible)
        if not (live or pid in ui.player_intel):
            return "hidden"          # never sighted — you don't know it exists
        if not p.alive:
            return "out"
        return "live" if live else "frozen"

    seats = [pid for pid in sorted(state.players)
             if not state.players[pid].is_neutral and vis(pid) != "hidden"]
    if not seats:
        return

    def label(pid: int, v: str, with_name: bool) -> str:
        p = state.players[pid]
        if v == "out":
            return f"{p.name} out" if with_name else "out"
        systems, ships, prod = _player_stats(state, pid) if v == "live" else ui.player_intel[pid]
        name = f"{p.name} " if with_name else ""
        mark = "" if v == "live" else " ?"        # stale, last-known intel
        return f"{name}{systems}s {ships}sh {prod:.1f}/t{mark}"

    def row_width(with_name: bool) -> int:
        return sum(sw_w + font.size(label(pid, vis(pid), with_name))[0] + gap for pid in seats)

    with_name = row_width(True) <= (w - 120)
    x = 120
    for pid in seats:
        v = vis(pid)
        color = _DEAD_COLOR if v == "out" else config.player_color(pid)
        sw = pygame.Rect(x, cy - 6, 12, 12)
        pygame.draw.rect(surface, color, sw, border_radius=3)
        if v == "frozen":
            _hatch_rect(surface, sw, _brighten(color))
        txt = label(pid, v, with_name)
        tcolor = config.COLOR_TEXT_DIM if v == "frozen" else color
        _text(surface, font, txt, tcolor, midleft=(x + sw_w, cy))
        x += sw_w + font.size(txt)[0] + gap


def _player_stats(state: GameState, pid: int) -> tuple[int, int, float]:
    """(systems owned, ships including in transit, production in ships/turn)."""
    return fog.player_totals(state, pid)


# --------------------------------------------------------------------------- #
# Info panel (right column)
# --------------------------------------------------------------------------- #
_ROW_H = 20


def _draw_side_panel(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    py = config.HUD_TOP_H
    # The panel's own footer is the big End Turn button (see _draw_hud), not the
    # ordinary bottom bar under the map — it reserves config.END_TURN_H instead.
    # History mode never draws that button (the scrubber owns the bottom bar
    # there instead), so it falls back to the plain HUD_BOTTOM_H reservation.
    footer_h = config.HUD_BOTTOM_H if ui.history else config.END_TURN_H
    ph = h - config.HUD_TOP_H - footer_h
    pygame.draw.rect(surface, (16, 18, 28), (px, py, config.HUD_RIGHT_W, ph))
    pygame.draw.line(surface, (40, 44, 60), (px, py), (px, py + ph - 1), 1)

    # queued-orders list occupies the bottom of the panel (always visible so
    # orders can be reviewed / removed); details fill the space above it. Hidden
    # in history mode — those orders belong to the live turn, not the past board.
    if ui.history:
        ui.order_hitboxes = []
        ui.forward_hitboxes = []
    else:
        _draw_order_list(surface, state, ui)

    x, y = px + 14, py + 14
    # persistent "clear all forwarding" button, shown whenever any rule exists
    if ui.auto_forward:
        y = _draw_clear_forward_button(surface, ui, px, py + 10)
    editing = _editing_order(ui)
    editing_rule = ui.sel_forward if ui.sel_forward in ui.auto_forward else None
    if editing is not None:
        focus = editing.source_id
    elif editing_rule is not None:
        focus = editing_rule
    else:
        focus = ui.selected if ui.selected is not None else ui.hover
    if focus is None or focus not in state.systems:
        _panel_legend(surface, x, y)
        return

    y = _panel_system(surface, state, ui, x, y, state.systems[focus])

    if editing is not None:
        if editing.dest_id in state.systems:
            y = _panel_lane(surface, state, ui, x, y + 8, focus, editing.dest_id)
        _panel_editing(surface, ui, x, y + 8, editing)
        return

    dest = _panel_lane_target(state, ui, focus)
    if dest is not None:
        y = _panel_lane(surface, state, ui, x, y + 8, focus, dest)

    if focus in ui.auto_forward:
        _panel_rule(surface, ui, x, y + 8, focus)


def _draw_clear_forward_button(surface, ui: Ui, px: int, y: int) -> int:
    """A slim panel-top button that clears every standing forward rule; records
    its hit-rect on ``ui``. Returns the y below it for the details that follow."""
    n = len(ui.auto_forward)
    r = pygame.Rect(px + 12, y, config.HUD_RIGHT_W - 24, 24)
    pygame.draw.rect(surface, (58, 38, 42), r, border_radius=5)
    pygame.draw.rect(surface, (170, 96, 104), r, 1, border_radius=5)
    _text(surface, _fonts()["small"], f"Clear all forwarding ({n})",
          config.COLOR_TEXT, center=r.center)
    ui.clear_forward_rect = (r.x, r.y, r.w, r.h)
    return y + 24 + 10


def _editing_order(ui: Ui):
    """The queued order currently selected for editing, or None. Suppressed in
    CHOOSING mode: there the send popup (not the side panel) is the editor, and
    the panel already shows the lane's live 'Sending' count."""
    if ui.mode == CHOOSING:
        return None
    if ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
        return ui.pending[ui.sel_order]
    return None


_ORDER_ROW_H = 22        # normal row pitch
_ORDER_ROW_SEL_H = 36    # a selected row grows, so its × delete button is easy to hit


def _draw_order_list(surface, state: GameState, ui: Ui) -> None:
    """Bottom-of-panel list of queued orders, followed by standing auto-forward
    rules. Records a (row, delete) hit-rect per order on ``ui.order_hitboxes``
    (parallel to ``ui.pending``) and a (source_id, row, delete) hit-rect per
    rule on ``ui.forward_hitboxes``, both for input to test clicks against. The
    currently-selected row is drawn taller with a bigger delete button, so it can
    be removed by tap on touch (where the keyboard X shortcut isn't available)."""
    ui.order_hitboxes = []
    ui.forward_hitboxes = []
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    # the panel's footer is the big End Turn button, not the ordinary bottom bar
    bottom = h - config.END_TURN_H
    rules = sorted(ui.auto_forward.items())  # stable order across frames
    if not ui.pending and not rules:
        return

    x = px + config.s(12)
    row_w = config.HUD_RIGHT_W - config.s(24)
    top_limit = config.HUD_TOP_H + config.s(8)
    title_h = config.s(22)
    gap = config.s(2)
    rh, rh_sel = config.s(_ORDER_ROW_H), config.s(_ORDER_ROW_SEL_H)

    # orders first, then rules, so order_hitboxes indices stay aligned with pending
    entries = [("order", i) for i in range(len(ui.pending))] + [("rule", src) for src, _ in rules]
    n = len(entries)

    def selected_of(kind, key) -> bool:
        return key == (ui.sel_order if kind == "order" else ui.sel_forward)

    def row_h(kind, key) -> int:
        return rh_sel if selected_of(kind, key) else rh

    # Fit rows from the top; reserve one row for a "+N more" line if they overflow.
    avail = bottom - config.s(8) - top_limit - title_h
    if sum(row_h(*e) for e in entries) <= avail:
        shown, overflow = entries, 0
    else:
        shown, used = [], 0
        for e in entries:
            if used + row_h(*e) + rh > avail:   # keep room for the "+N more" line
                break
            shown.append(e)
            used += row_h(*e)
        overflow = n - len(shown)

    block_h = title_h + sum(row_h(*e) for e in shown) + (rh if overflow else 0)
    top = bottom - config.s(8) - block_h
    pygame.draw.line(surface, (40, 44, 60), (px + config.s(8), top - config.s(6)),
                     (px + config.HUD_RIGHT_W - config.s(8), top - config.s(6)), 1)
    _text(surface, _fonts()["small"], f"Queued ({n})", config.COLOR_TEXT_DIM, topleft=(x, top))

    y = top + title_h
    hcolor = config.player_color(ui.human_id)
    for kind, key in shown:
        selected = selected_of(kind, key)
        this_h = rh_sel if selected else rh
        row = (x, y, row_w, this_h - gap)
        if kind == "order":
            o = ui.pending[key]
            label = f"{o.source_id}->{o.dest_id}   {o.ships} sh"
        else:
            dest, keep = ui.auto_forward[key]
            label = f"{key}->{dest}   keep {keep}"
        if selected:
            pygame.draw.rect(surface, (40, 46, 66), pygame.Rect(*row), border_radius=4)
        dsz = config.s(26) if selected else config.s(16)
        dr = (x + row_w - dsz - config.s(2), y + (this_h - gap - dsz) // 2, dsz, dsz)
        _draw_x_button(surface, dr, boxed=selected)
        _text(surface, _fonts()["small"], label, config.COLOR_SELECT if selected else hcolor,
              midleft=(x + config.s(6), y + (this_h - gap) // 2))
        if kind == "order":
            ui.order_hitboxes.append((row, dr))
        else:
            ui.forward_hitboxes.append((key, row, dr))
        y += this_h
    if overflow:
        _text(surface, _fonts()["small"], f"+{overflow} more", config.COLOR_TEXT_DIM,
              midleft=(x + config.s(6), y + rh // 2))


def _draw_x_button(surface, rect, boxed: bool = False) -> None:
    """A × delete glyph inside ``rect`` (x, y, w, h). ``boxed`` draws a framed
    background so an enlarged (selected-row) delete target reads as a button."""
    rx, ry, rw, rh = rect
    if boxed:
        pygame.draw.rect(surface, (60, 40, 46), pygame.Rect(rx, ry, rw, rh), border_radius=4)
        pygame.draw.rect(surface, (150, 90, 96), pygame.Rect(rx, ry, rw, rh), 1, border_radius=4)
    pad = max(3, rw // 4)
    lw = max(2, rw // 8)
    col = config.COLOR_TEXT if boxed else config.COLOR_TEXT_DIM
    pygame.draw.line(surface, col, (rx + pad, ry + pad), (rx + rw - pad, ry + rh - pad), lw)
    pygame.draw.line(surface, col, (rx + rw - pad, ry + pad), (rx + pad, ry + rh - pad), lw)


def _panel_editing(surface, ui: Ui, x, y, o) -> int:
    _text(surface, _fonts()["normal"], "Editing order", config.COLOR_SELECT, topleft=(x, y))
    y += 26
    y = _row(surface, x, y, f"Sending: {o.ships}", config.COLOR_SELECT)
    y = _row(surface, x, y, "wheel or −/+ buttons · X: remove", config.COLOR_TEXT_DIM)
    return y


def _row(surface, x, y, text, color) -> int:
    if text:
        _text(surface, _fonts()["small"], text, color, topleft=(x, y))
    return y + _ROW_H


def _panel_system(surface, state: GameState, ui: Ui, x, y, sys) -> int:
    if sys.id not in ui.visible:
        # fogged system: we know where it is, not who holds it or how strong it is
        _text(surface, _fonts()["normal"], f"System {sys.id}", config.COLOR_TEXT_DIM,
              topleft=(x, y))
        y += 26
        y = _row(surface, x, y, "Owner: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "Ships: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "(out of sight)", config.COLOR_TEXT_DIM)
        return y
    _text(surface, _fonts()["normal"], f"System {sys.id}",
          config.player_color(sys.owner_id), topleft=(x, y))
    y += 26
    y = _row(surface, x, y, f"Owner: {config.player_name(sys.owner_id)}",
             config.player_color(sys.owner_id))
    if sys.owner_id == ui.human_id:
        y = _row(surface, x, y, f"Ships: {ui.available(state, sys.id)} free / {sys.ships} total",
                 config.COLOR_TEXT)
    else:
        y = _row(surface, x, y, f"Ships: {sys.ships}", config.COLOR_TEXT)
    if sys.production > 0:
        y = _row(surface, x, y, f"Production: {sys.production} turns/ship", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, f"  building: {sys.prod_progress}/{sys.production}",
                 config.COLOR_TEXT_DIM)
    else:
        y = _row(surface, x, y, "Production: none", config.COLOR_TEXT_DIM)
    y = _row(surface, x, y, f"Threat (adj): {_adjacent_enemy_strength(state, sys.id, sys.owner_id)}",
             config.COLOR_TEXT_DIM)
    fin, ein = _inbound_summary(state, sys.id, sys.owner_id)
    if fin or ein:
        y = _row(surface, x, y, f"Inbound: +{fin}f / {ein}e", config.COLOR_TEXT_DIM)
    y = _row(surface, x, y, f"Neighbours: {len(sys.neighbors)}", config.COLOR_TEXT_DIM)
    return y


def _panel_lane(surface, state: GameState, ui: Ui, x, y, src, dest) -> int:
    lane = state.lanes.get(lane_key(src, dest))
    if lane is None:
        return y
    _text(surface, _fonts()["normal"], f"Lane -> System {dest}", config.COLOR_TEXT, topleft=(x, y))
    y += 26
    y = _row(surface, x, y, f"{lane.length_ly} ly  ·  {lane.travel_turns} turns", config.COLOR_TEXT)
    d = state.systems[dest]
    if dest in ui.visible:
        y = _row(surface, x, y, f"Target: {config.player_name(d.owner_id)} · {d.ships}sh",
                 config.player_color(d.owner_id))
    else:
        y = _row(surface, x, y, "Target: ? · ?sh", config.COLOR_TEXT_DIM)
    if ui.mode == CHOOSING and ui.forward_armed:
        y = _row(surface, x, y, f"Forwarding · keep {ui.keep}", config.COLOR_SELECT)
    elif ui.mode == CHOOSING:
        y = _row(surface, x, y, f"Sending: {ui.chosen}", config.COLOR_SELECT)
    return y


def _panel_rule(surface, ui: Ui, x, y, src) -> int:
    dest, keep = ui.auto_forward[src]
    editing = src == ui.sel_forward
    color = config.COLOR_SELECT if editing else config.player_color(ui.human_id)
    _text(surface, _fonts()["normal"], "Auto-forward", color, topleft=(x, y))
    y += 26
    y = _row(surface, x, y, f"-> System {dest}, keep {keep}",
             config.COLOR_SELECT if editing else config.COLOR_TEXT)
    hint = "wheel or −/+ buttons: keep  ·  X: remove" if editing else "click to edit  ·  X: clear"
    y = _row(surface, x, y, hint, config.COLOR_TEXT_DIM)
    return y


def _panel_legend(surface, x, y) -> int:
    _text(surface, _fonts()["normal"], "Star Conquest", config.COLOR_TEXT, topleft=(x, y))
    y += 28
    for line in (
        "Click a system for details.",
        "",
        "Select -> click neighbour",
        "  = sends all at once",
        "Popup tabs: Send / Forward",
        "  Half / All  ·  −/+ adjust",
        "Forward: standing rule",
        "Shift+click: forward now",
        "Click queued lane/rule: edit",
        "X: clear forward rule",
        "Enter / Space: end turn",
        "P: play / pause",
    ):
        y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
    return y


def _panel_lane_target(state: GameState, ui: Ui, focus: int):
    """Neighbour whose lane stats to show: the chosen dest, or a hovered neighbour."""
    if ui.mode == CHOOSING and ui.dest is not None:
        return ui.dest
    if (ui.selected is not None and ui.hover is not None and ui.hover != ui.selected
            and state.are_adjacent(ui.selected, ui.hover)):
        return ui.hover
    return None


def _adjacent_enemy_strength(state: GameState, sid: int, owner: int) -> int:
    """Largest garrison of a non-friendly, non-neutral neighbour."""
    best = 0
    for n in state.systems[sid].neighbors:
        o = state.systems[n]
        if o.owner_id not in (owner, 0):
            best = max(best, o.ships)
    return best


def _inbound_summary(state: GameState, sid: int, owner: int) -> tuple[int, int]:
    """(friendly, enemy) ships currently inbound to a system."""
    friendly = enemy = 0
    for f in state.fleets_incoming(sid):
        if f.owner_id == owner:
            friendly += f.ships
        else:
            enemy += f.ships
    return friendly, enemy


def confirm_quit_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(quit, cancel) button rects — shared by the drawer and the hit-tester."""
    w, h = surface.get_size()
    cx, cy = w // 2, h // 2
    bw, bh, gap = config.s(200), config.s(42), config.s(12)
    quit_r = pygame.Rect(cx - bw - gap, cy + config.s(24), bw, bh)
    cancel_r = pygame.Rect(cx + gap, cy + config.s(24), bw, bh)
    return quit_r, cancel_r


def draw_confirm_quit(surface) -> None:
    """Modal 'are you sure?' veil, overlaid on whichever scene is beneath it."""
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    _text(surface, _fonts()["big"], "Quit Star Conquest?", config.COLOR_TEXT,
          center=(w // 2, h // 2 - 36))
    quit_r, cancel_r = confirm_quit_buttons(surface)
    for rect, label, fill, edge in (
        (quit_r, "Quit (Y/Enter)", (120, 46, 52), (200, 96, 104)),
        (cancel_r, "Cancel (N/Esc)", (46, 92, 60), (96, 190, 120)),
    ):
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        pygame.draw.rect(surface, edge, rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], label, config.COLOR_TEXT, center=rect.center)


def _draw_win_overlay(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 180))
    surface.blit(veil, (0, 0))
    if state.winner == 0:
        msg, color = "Mutual annihilation — draw", config.COLOR_TEXT
    else:
        msg = f"{config.player_name(state.winner)} wins!"
        color = config.player_color(state.winner)
    _text(surface, _fonts()["big"], msg, color, center=(w // 2, h // 2 - config.s(60)))
    _text(surface, _fonts()["normal"], "R: new map  ·  M: setup menu  ·  Esc: quit",
          config.COLOR_TEXT_DIM, center=(w // 2, h // 2 - config.s(24)))
    # Tappable buttons (touch equivalents of the R/M/H/Esc keys). Restart and Menu
    # sit side by side; Review-history enters history mode to scrub the finished
    # game with fog fully lifted (see what was happening behind the fog of war);
    # Quit sits beside it, opening the same confirm-quit modal as Esc.
    bw, bh, gap = config.s(180), config.s(40), config.s(12)
    row_y = h // 2 + config.s(8)
    rr = pygame.Rect(w // 2 - bw - gap // 2, row_y, bw, bh)
    mr = pygame.Rect(w // 2 + gap // 2, row_y, bw, bh)
    ui.restart_button_rect = (rr.x, rr.y, rr.w, rr.h)
    ui.menu_button_rect = (mr.x, mr.y, mr.w, mr.h)
    pygame.draw.rect(surface, (46, 68, 52), rr, border_radius=6)
    pygame.draw.rect(surface, (110, 180, 130), rr, 2, border_radius=6)
    _text(surface, _fonts()["normal"], "New map (R)", config.COLOR_TEXT, center=rr.center)
    pygame.draw.rect(surface, (40, 52, 78), mr, border_radius=6)
    pygame.draw.rect(surface, (110, 140, 200), mr, 2, border_radius=6)
    _text(surface, _fonts()["normal"], "Setup menu (M)", config.COLOR_TEXT, center=mr.center)

    hr = pygame.Rect(w // 2 - bw - gap // 2, row_y + bh + gap, bw, bh)
    ui.history_button_rect = (hr.x, hr.y, hr.w, hr.h)
    pygame.draw.rect(surface, (52, 46, 78), hr, border_radius=6)
    pygame.draw.rect(surface, (150, 130, 200), hr, 2, border_radius=6)
    _text(surface, _fonts()["normal"], "Review history (H)", config.COLOR_TEXT,
          center=hr.center)

    qr = pygame.Rect(w // 2 + gap // 2, row_y + bh + gap, bw, bh)
    ui.quit_button_rect = (qr.x, qr.y, qr.w, qr.h)
    pygame.draw.rect(surface, (92, 46, 52), qr, border_radius=6)
    pygame.draw.rect(surface, (200, 96, 104), qr, 2, border_radius=6)
    _text(surface, _fonts()["normal"], "Quit (Esc)", config.COLOR_TEXT, center=qr.center)


# Scrubber palette — a cool track with a bright fill/knob, echoing the play button.
_SCRUB_TROUGH = (40, 44, 60)
_SCRUB_FILL = (110, 140, 200)


def _draw_scrubber(surface, state: GameState, ui: Ui) -> None:
    """Bottom-bar turn scrubber for history mode: an Exit button, a draggable
    track (0 .. ``ui.history_max`` turns), a turn/mode label, and a Rewind button
    (only when viewing a turn before the latest). Records hit-rects on ``ui`` for
    input, mirroring the store-rect-then-test handoff used across the HUD."""
    w, h = surface.get_size()
    by = h - config.HUD_BOTTOM_H
    cy = by + config.HUD_BOTTOM_H // 2
    font = _fonts()["small"]

    # Exit button (far left).
    ex = pygame.Rect(12, by + (config.HUD_BOTTOM_H - 28) // 2, 92, 28)
    ui.exit_history_rect = (ex.x, ex.y, ex.w, ex.h)
    pygame.draw.rect(surface, (40, 52, 78), ex, border_radius=6)
    pygame.draw.rect(surface, (110, 140, 200), ex, 2, border_radius=6)
    _text(surface, font, "Exit (Esc)", config.COLOR_TEXT, center=ex.center)

    # Play/pause button — auto-advances the scrubber at sim speed (main's play
    # timer). Reuses ui.play_pause_rect (the HUD's live play button, zeroed while
    # in history) and the same "toggle_play" action; colours match it too.
    pp = pygame.Rect(ex.right + 8, ex.y, 104, 28)
    ui.play_pause_rect = (pp.x, pp.y, pp.w, pp.h)
    pfill = (92, 70, 46) if ui.playing else (40, 52, 78)
    pedge = (190, 150, 96) if ui.playing else (110, 140, 200)
    pygame.draw.rect(surface, pfill, pp, border_radius=6)
    pygame.draw.rect(surface, pedge, pp, 2, border_radius=6)
    _text(surface, font, "Pause (P)" if ui.playing else "Play (P)",
          config.COLOR_TEXT, center=pp.center)

    # Rewind button (far right). Its footprint is reserved even when hidden so the
    # track width and label position stay fixed as you scrub — the button only
    # appears for turns before the latest (when there is something to leave behind).
    rw = pygame.Rect(w - 172 - 12, by + (config.HUD_BOTTOM_H - 28) // 2, 172, 28)
    right_limit = rw.x - 14
    if ui.history_turn >= ui.history_max:
        ui.rewind_button_rect = (0, 0, 0, 0)
    else:
        ui.rewind_button_rect = (rw.x, rw.y, rw.w, rw.h)
        pygame.draw.rect(surface, (120, 86, 46), rw, border_radius=6)
        pygame.draw.rect(surface, (200, 150, 96), rw, 2, border_radius=6)
        _text(surface, font, "Rewind to here", config.COLOR_TEXT, center=rw.center)

    # Turn / fog-mode label, right-aligned just left of the rewind button. Its
    # slot is sized to the widest label this session could show (max digits, the
    # longer "revealed" tag) so a changing turn number never nudges the track.
    tag = "revealed" if ui.history_reveal else "as seen"
    label = f"Turn {state.turn}/{ui.history_max}  ·  {tag}"
    widest = f"Turn {ui.history_max}/{ui.history_max}  ·  revealed"
    label_x = right_limit - font.size(widest)[0]
    _text(surface, font, label, config.COLOR_TEXT_DIM, midright=(right_limit, cy))

    # Track fills the space between the play button and the label slot.
    track_x = pp.right + 16
    track_w = max(1, label_x - 16 - track_x)
    ui.scrubber_rect = (track_x, by + 6, track_w, config.HUD_BOTTOM_H - 12)
    pygame.draw.rect(surface, _SCRUB_TROUGH,
                     pygame.Rect(track_x, cy - 3, track_w, 6), border_radius=3)
    t = 0.0 if ui.history_max <= 0 else ui.history_turn / ui.history_max
    fill_w = int(track_w * t)
    if fill_w > 0:
        pygame.draw.rect(surface, _SCRUB_FILL,
                         pygame.Rect(track_x, cy - 3, fill_w, 6), border_radius=3)
    hx = track_x + fill_w
    pygame.draw.circle(surface, config.COLOR_TEXT, (hx, cy), 7)
    pygame.draw.circle(surface, _SCRUB_FILL, (hx, cy), 7, 2)


def confirm_rewind_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(rewind, cancel) button rects — shared by the drawer and the hit-tester."""
    w, h = surface.get_size()
    cx, cy = w // 2, h // 2
    bw, bh, gap = config.s(220), config.s(42), config.s(12)
    rewind_r = pygame.Rect(cx - bw - gap, cy + config.s(24), bw, bh)
    cancel_r = pygame.Rect(cx + gap, cy + config.s(24), bw, bh)
    return rewind_r, cancel_r


def draw_confirm_rewind(surface, turn: int) -> None:
    """Modal confirm for a destructive mid-game rewind (discards later turns)."""
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    _text(surface, _fonts()["big"], f"Rewind to turn {turn}?", config.COLOR_TEXT,
          center=(w // 2, h // 2 - 40))
    _text(surface, _fonts()["small"], "All turns after this one will be discarded.",
          config.COLOR_TEXT_DIM, center=(w // 2, h // 2 - 6))
    rewind_r, cancel_r = confirm_rewind_buttons(surface)
    for rect, label, fill, edge in (
        (rewind_r, "Rewind (Y/Enter)", (120, 86, 46), (200, 150, 96)),
        (cancel_r, "Cancel (N/Esc)", (46, 92, 60), (96, 190, 120)),
    ):
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        pygame.draw.rect(surface, edge, rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], label, config.COLOR_TEXT, center=rect.center)
