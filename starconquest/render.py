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
from .viewstate import CHOOSING, ROUTING, Ui

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
# Layout helpers
#
# Everything that holds text is measured rather than given a fixed pixel size: a
# constant width or row pitch is only ever right at one font size, and the UI font
# grows with config.ui_scale (up to ~2x on a phone). The helpers below are what keep
# labels inside their buttons and rows clear of each other at any scale — plus the
# two that adapt to a touch build (see config.touch_ui).
# --------------------------------------------------------------------------- #
# HUD button palette — (fill, edge) pairs, kept local like the scrubber's below.
_BTN_BLUE = ((40, 52, 78), (110, 140, 200))  # ordinary action
_BTN_ACTIVE = ((92, 70, 46), (190, 150, 96))  # a toggle that is currently on
_BTN_AMBER = ((120, 86, 46), (200, 150, 96))  # new map / rewind
_BTN_VIOLET = ((52, 46, 78), (150, 130, 200))  # history / review
_BTN_RED = ((92, 46, 52), (200, 96, 104))  # quit
_BTN_DANGER = ((120, 46, 52), (200, 96, 104))  # ...and its brighter modal confirm
_BTN_GREEN = ((46, 92, 60), (96, 190, 120))  # end turn / confirm
_BTN_TEAL = ((44, 62, 74), (120, 180, 200))  # share a challenge


def _row_h(kind: str = "small") -> int:
    """Pitch for one line of stacked text in ``kind``'s font: the font's own line
    height plus a gap. Derived, so rows stay legibly apart instead of colliding
    once the font outgrows a hardcoded pitch."""
    return _fonts()[kind].get_height() + config.ROW_GAP


def _btn_w(font, label: str, min_w: int = 0) -> int:
    """Width of a button that has to fit ``label``: measured text plus padding."""
    return max(min_w, font.size(label)[0] + 2 * config.BTN_PAD_X)


def _tap_size(px: int) -> int:
    """``px``, raised to ``config.TOUCH_MIN_TARGET`` on a touch build so a control
    that is merely small with a mouse doesn't become un-tappable with a finger."""
    return max(px, config.TOUCH_MIN_TARGET) if config.touch_ui else px


def _btn(surface, rect: pygame.Rect, label: str, fill, edge, font=None, color=None) -> tuple[int, int, int, int]:
    """Draw a filled, outlined, centre-labelled button; return its hit-rect tuple
    for storing on ``ui`` (the store-rect-then-test handoff input relies on).
    Every HUD button comes through here so they share one look."""
    radius = config.s(6)
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    pygame.draw.rect(surface, edge, rect, config.s(2), border_radius=radius)
    _text(surface, font or _fonts()["normal"], label, color or config.COLOR_TEXT, center=rect.center)
    return (rect.x, rect.y, rect.w, rect.h)


def _key_hint(label: str, key: str) -> str:
    """``label`` with its keyboard shortcut appended. Dropped on a touch build:
    there is no key to press there, and the suffix is both noise and the thing
    that pushes these labels out of their buttons at touch scale."""
    return label if config.touch_ui else f"{label} ({key})"


def _wrap(font, text: str, width: int) -> list[str]:
    """``text`` broken into lines that each fit ``width`` px in ``font``.

    Paragraphs are separated by a blank line and kept as one blank line in the
    output; line breaks *within* a paragraph are just whitespace, so the source can
    be written at whatever width reads well and still reflow to the real panel.
    """
    lines: list[str] = []
    for para in text.strip().split("\n\n"):
        if lines:
            lines.append("")
        line = ""
        for word in para.split():
            trial = f"{line} {word}" if line else word
            if line and font.size(trial)[0] > width:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #
def draw(surface: pygame.Surface, state: GameState, ui: Ui) -> None:
    surface.fill(config.COLOR_BG)
    # zero the popup's rects; _draw_send_popup re-records them if it runs. The
    # slider's matters most: input maps a drag onto whatever rect is recorded, and a
    # stale one would keep steering a count after the popup had gone.
    ui.minus_rect = ui.plus_rect = ui.slider_rect = (0, 0, 0, 0)
    ui.popup_rect = (0, 0, 0, 0)
    ui.send_tab_rect = ui.forward_tab_rect = (0, 0, 0, 0)
    ui.send_all_rect = ui.send_half_rect = ui.cancel_rect = (0, 0, 0, 0)
    ui.clear_forward_rect = (0, 0, 0, 0)
    # route mode's confirm; _draw_hud re-records it once the plan has something in it
    ui.route_confirm_rect = (0, 0, 0, 0)
    # The map layer can now be panned/zoomed past config.play_rect()'s edges
    # (unlike the old fixed fit-to-bounds view, which always fit inside it by
    # construction), so clip it to the viewport here rather than let it bleed
    # into the HUD/side panel — same set_clip/set_clip(None) idiom as
    # _hatch_rect uses elsewhere in this file.
    surface.set_clip(config.play_rect())
    _draw_lanes(surface, state, ui)
    # History mode reviews a reconstructed past board: skip the live-interaction
    # overlays (queued/standing orders, the count being composed) — there is no
    # order entry while scrubbing.
    if not ui.history:
        _draw_forward_rules(surface, state, ui)  # standing auto-forward (chevron flow)
        _draw_pending(surface, state, ui)  # queued one-shot sends (solid)
        _draw_choosing_preview(surface, state, ui)  # the arrow you're adjusting now
        _draw_route_preview(surface, state, ui)  # the route plan being composed
    _draw_fleets(surface, state, ui)
    _draw_systems(surface, state, ui)
    _draw_node_names(surface, state, ui)
    if not ui.history and ui.drag_active and ui.drag_src is not None:
        _draw_drag(surface, state, ui)
    if not ui.history:
        _draw_route_overlay(surface, state, ui)
    if not ui.history:
        # the send popup draws last of the map layer so nodes/fleets never occlude
        # it (it must stay visible and clickable); it early-outs when closed
        _draw_send_popup(surface, state, ui)
    surface.set_clip(None)
    if not ui.history:
        _draw_zoom_controls(surface, ui)
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
    if tgt is not None and tgt != ui.drag_src and tgt in state.systems and state.are_adjacent(ui.drag_src, tgt):
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
        turns = state.travel_turns(lane.a, lane.b) or lane.travel_turns
        width, color = _lane_style(turns)
        # a lane touching a fogged system reads as uncertain: mute it to the fog
        # colour (its travel time — route topology — is still shown below)
        if sa != "visible" or sb != "visible":
            color = config.COLOR_FOG
        # brighten the lanes touching the selected source system (or, in route
        # mode, any of the selected group)
        if (ui.selected is not None and ui.selected in (lane.a, lane.b)) or (
                ui.route_sel & {lane.a, lane.b}):
            color = config.COLOR_LANE_HILITE
            width = max(width, config.s(3))
        pygame.draw.line(surface, color, pa, pb, width)
        # travel time is detailed intel — only show it where an endpoint is in
        # full view; a lane between two fogged systems draws as a bare dim line
        if sa == "visible" or sb == "visible":
            mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
            _label_pill(surface, _fonts()["small"], str(turns), config.COLOR_TEXT_DIM, mid)


def _lane_style(travel_turns: int) -> tuple[int, tuple[int, int, int]]:
    """Width (px) and colour for a lane from its travel time. Fast = thin+bright.

    Thickness tracks travel time directly (clamped) so the spread reads at a
    glance; colour brightens the quick lanes and mutes the slow ones.
    """
    width = config.s(max(2, min(6, travel_turns)))  # 2px (fast) .. 6px (slow)
    f = (min(6, max(1, travel_turns)) - 1) / 5.0  # 0 fast .. 1 slow
    scale = 1.3 - 0.6 * f  # 1.3x (bright) .. 0.7x (dim)
    r, g, b = config.COLOR_LANE
    color = (min(255, int(r * scale)), min(255, int(g * scale)), min(255, int(b * scale)))
    return width, color


def _lane_unit(pa, pb) -> tuple[float, float, float]:
    """Unit vector from ``pa`` toward ``pb``, plus the lane's on-screen length."""
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length, length


def _rule_label_center(pa, pb, font) -> tuple[int, int]:
    """Where an auto-forward rule's label sits on its lane: just past the *sending*
    system, offset clear of the line itself. "keep N" is a fact about that garrison,
    so it belongs at the end it constrains — and it leaves the lane's own
    travel-time pill (centred on the midpoint) readable, which is the other number
    you judge a rule by (see also ``_panel_lane``).

    Clamped to a fraction of the lane so a short one still puts the label on its own
    half, and offset perpendicular to the lane rather than straight down, so the
    label clears the source node on a steep lane as well as a flat one. On a lane
    too short for that clamp to clear the node, the perpendicular offset grows to
    make up the difference — the label leans out into space rather than onto the
    system it belongs to.
    """
    ux, uy, length = _lane_unit(pa, pb)
    px, py = (-uy, ux) if ux >= 0 else (uy, -ux)   # perpendicular, biased screen-down
    clear = config.node_clearance() + config.RULE_LABEL_GAP
    along = min(clear, length * config.RULE_LABEL_MAX_FRAC)
    off = (_fonts()["small"].get_height() + font.get_height()) // 2 + config.s(8)
    off = max(off, math.sqrt(max(0.0, clear * clear - along * along)))
    return (int(pa[0] + ux * along + px * off), int(pa[1] + uy * along + py * off))


def _pill_rect(font, s: str, center) -> pygame.Rect:
    """Bounds of the plate ``_label_pill`` would draw. Measured separately because
    the star-name pass has to treat these labels as occupied space."""
    rect = pygame.Rect((0, 0), font.size(s))
    rect.center = center
    return rect.inflate(config.s(8), config.s(4))


def _label_pill(surface, font, s: str, color, center) -> None:
    """Draw text centred on a small dark rounded rect so it reads over any line."""
    img = font.render(s, True, color)
    rect = img.get_rect(center=center)
    pygame.draw.rect(surface, config.COLOR_BG, _pill_rect(font, s, center), border_radius=config.s(5))
    surface.blit(img, rect)


def _lane_offsets(state: GameState) -> dict[int, tuple[int, int]]:
    """Assign each in-transit fleet a small perpendicular offset so stacks split."""
    groups: dict[frozenset[int], list[int]] = {}
    for i, f in enumerate(state.fleets):
        groups.setdefault(frozenset((f.source_id, f.dest_id)), []).append(i)
    offset: dict[int, tuple[int, int]] = {}
    for idxs in groups.values():
        for rank, i in enumerate(idxs):
            offset[i] = (rank - (len(idxs) - 1) / 2, 0)  # perpendicular rank, scaled later
    return offset


def _draw_fleets(surface, state: GameState, ui: Ui) -> None:
    offsets = _lane_offsets(state)
    for i, f in enumerate(state.fleets):
        # your own fleets always show; an enemy fleet shows only where at least
        # one end of its lane is in full view, so rival movements appear only as
        # they near your space
        if f.owner_id != ui.human_id and f.source_id not in ui.visible and f.dest_id not in ui.visible:
            continue
        a = state.systems[f.source_id].pos
        b = state.systems[f.dest_id].pos
        wp = lerp(a, b, f.progress())
        x, y = ui.view.to_screen(wp)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        px, py = -uy, ux  # perpendicular
        # split stacked fleets, and sit the count clear of the triangle — both
        # offsets scale, so the label never lands on the glyph on a big display
        spread = config.FLEET_SIZE + config.s(3)
        rank = offsets.get(i, (0, 0))[0]
        x += int(px * rank * spread)
        y += int(py * rank * spread)
        _draw_triangle(surface, (x, y), (ux, uy), config.player_color(f.owner_id))
        _text(surface, _fonts()["small"], str(f.ships), config.COLOR_TEXT, center=(x + int(px * spread), y + int(py * spread)))


def _draw_triangle(surface, center, direction, color) -> None:
    ux, uy = direction
    px, py = -uy, ux
    s = config.FLEET_SIZE
    tip = (center[0] + ux * s, center[1] + uy * s)
    left = (center[0] - ux * s + px * s * 0.7, center[1] - uy * s + py * s * 0.7)
    right = (center[0] - ux * s - px * s * 0.7, center[1] - uy * s - py * s * 0.7)
    pygame.draw.polygon(surface, color, [tip, left, right])


def _draw_arrowhead(surface, tip, direction, color, width: int, size: int | None = None) -> None:
    """An open chevron pointing ``direction``, drawn in the line's own weight — the
    head of a queued order at full size, one link of a rule's conveyor at a small one.

    Deliberately not `_draw_triangle`'s filled wedge — that means ships actually on
    the lane, and an order or rule is only an intention.
    """
    ux, uy = direction
    px, py = -uy, ux
    size = config.ARROWHEAD_SIZE if size is None else size
    back = (tip[0] - ux * size, tip[1] - uy * size)
    wing = size * config.ARROW_WING
    pygame.draw.line(surface, color, (back[0] + px * wing, back[1] + py * wing), tip, width)
    pygame.draw.line(surface, color, (back[0] - px * wing, back[1] - py * wing), tip, width)


def _flow_phase() -> float:
    """Where a rule's conveyor sits in its cycle, 0..1, off the wall clock.

    Presentation only — nothing in the simulation is timed off this, and it is read
    (never stored), so a frame drawn in a test simply gets whatever phase it gets.
    """
    period = max(1, config.RULE_FLOW_MS)
    return (pygame.time.get_ticks() % period) / period


def _draw_planned(surface, pa, pb, dest_r: int, color, width: int) -> None:
    """A queued one-shot send: a solid lane line with a chevron head. The head stops
    at the destination's edge rather than under the node, which is drawn over this
    layer."""
    pygame.draw.line(surface, color, pa, pb, width)
    ux, uy, _ = _lane_unit(pa, pb)
    inset = dest_r + config.ARROW_GAP
    _draw_arrowhead(surface, (pb[0] - ux * inset, pb[1] - uy * inset), (ux, uy), color, width)


def _rule_chevron_dists(length: float, src_r: int, dest_r: int, phase: float) -> list[float]:
    """Distances along a rule's lane at which to draw its conveyor chevrons.

    The run fills the whole gap between the two systems: `config.RULE_CHEVRON_GAP` is
    a target pitch, and the actual spacing is the span divided by however many
    chevrons that asks for, so no remainder is left idling short of the destination.
    `phase` (0..1) advances every chevron by one spacing over a full cycle and wraps
    the leading one back to the source — because the spacing divides the span evenly,
    that wrap is a chevron arriving at the destination as another leaves the source,
    which is what makes the flow read as continuous.
    """
    start = src_r + config.ARROW_GAP + config.RULE_CHEVRON_SIZE
    span = length - (dest_r + config.ARROW_GAP) - start
    if span <= config.RULE_CHEVRON_SIZE:
        return [length / 2]   # nose-to-nose systems: no room for a run, so mark the lane
    count = max(1, round(span / max(1, config.RULE_CHEVRON_GAP)))
    step = span / count
    return [start + ((i + phase) % count) * step for i in range(count)]


def _draw_rule_flow(surface, pa, pb, src_r: int, dest_r: int, color, width: int,
                    animate: bool) -> None:
    """A standing forward rule's lane: a conveyor of small chevrons running toward
    the destination, so the way its ships will go reads from anywhere along the lane
    rather than only its far end — and nothing on it resembles a fleet in transit.

    Only the selected rule crawls (`animate`, one spacing per `config.RULE_FLOW_MS`)
    — a board full of standing rules would otherwise shimmer.
    """
    ux, uy, length = _lane_unit(pa, pb)
    phase = _flow_phase() if animate else 0.0
    for d in _rule_chevron_dists(length, src_r, dest_r, phase):
        _draw_arrowhead(surface, (pa[0] + ux * d, pa[1] + uy * d), (ux, uy),
                        color, width, config.RULE_CHEVRON_SIZE)


def _draw_pending(surface, state: GameState, ui: Ui) -> None:
    for i, o in enumerate(ui.pending):
        pa = ui.view.to_screen(state.systems[o.source_id].pos)
        pb = ui.view.to_screen(state.systems[o.dest_id].pos)
        # the order being edited is drawn in the select colour, brighter+thicker
        selected = i == ui.sel_order
        color = config.COLOR_SELECT if selected else config.player_color(ui.human_id)
        _draw_planned(surface, pa, pb, config.node_radius(state.systems[o.dest_id].production),
                      color, config.s(5 if selected else 3))
        # place the count 40% of the way toward the destination, not the midpoint,
        # so two opposite-direction orders on the same lane don't overlap labels
        lx = int(pa[0] + (pb[0] - pa[0]) * 0.4)
        ly = int(pa[1] + (pb[1] - pa[1]) * 0.4)
        _text(surface, _fonts()["small"], str(o.ships), config.COLOR_TEXT, center=(lx, ly - config.s(10)))


def _draw_forward_rules(surface, state: GameState, ui: Ui) -> None:
    """Standing auto-forward rules as a persistent chevron flow (human colour).

    The rule being edited is drawn in the select colour, brighter+thicker, and is
    the only one whose chevrons crawl — same convention as `_draw_pending`'s
    selected queued order.

    A rule pointing at a system we *don't* hold is tinted as dangerous. Nothing
    stops one (`rule_is_live` doesn't care who owns the far end, and aiming a rule
    at an enemy on purpose is a real move), but it means the whole surplus charges
    into enemy guns every turn — and a route chain that loses a middle system
    leaves its upstream neighbour doing exactly that without anyone choosing to.
    """
    for src, (dest, keep) in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id or dest not in state.systems:
            continue
        selected = src == ui.sel_forward
        hostile = state.systems[dest].owner_id != ui.human_id
        if selected:
            color = config.COLOR_SELECT
        elif hostile:
            color = _BTN_DANGER[1]
        else:
            color = config.player_color(ui.human_id)
        pa = ui.view.to_screen(s.pos)
        pb = ui.view.to_screen(state.systems[dest].pos)
        _draw_rule_flow(surface, pa, pb, config.node_radius(s.production),
                        config.node_radius(state.systems[dest].production),
                        color, config.s(3 if selected else 2), animate=selected)
        if keep:  # 0 is the default; the chevron flow already says a rule is there
            small = _fonts()["small"]
            _label_pill(surface, small, f"keep {keep}", config.COLOR_TEXT if selected else config.COLOR_TEXT_DIM, _rule_label_center(pa, pb, small))


def _draw_choosing_preview(surface, state: GameState, ui: Ui) -> None:
    """The move the popup is editing right now: a bright arrow — the chevron flow on
    the Forward tab, matching how standing rules are drawn everywhere else, since the
    popup opens on existing rules too and a solid line would misread as a one-shot.
    The count itself lives in the popup (drawn on top, last of the map layer).
    """
    if ui.mode != CHOOSING or ui.selected is None or ui.dest is None:
        return
    pa = ui.view.to_screen(state.systems[ui.selected].pos)
    pb = ui.view.to_screen(state.systems[ui.dest].pos)
    dest_r = config.node_radius(state.systems[ui.dest].production)
    if ui.forward_armed:
        _draw_rule_flow(surface, pa, pb, config.node_radius(state.systems[ui.selected].production),
                        dest_r, config.COLOR_SELECT, config.s(3), animate=True)
    else:
        _draw_planned(surface, pa, pb, dest_r, config.COLOR_SELECT, config.s(3))


def _draw_route_preview(surface, state: GameState, ui: Ui) -> None:
    """Route mode's proposal: the selection, the chain it would lay, and what
    confirming would cost.

    Everything here is uncommitted, so it is drawn in `config.COLOR_ROUTE` — the
    one hue no seat uses — and *every* hop crawls. That matches
    `_draw_choosing_preview`, which animates the send being composed for the same
    reason; only committed rules hold still (and then only the unselected ones, so
    a board full of them doesn't shimmer).

    A rule the plan would overwrite is drawn underneath in a muted danger tint, so
    the overwrite is visible before it happens rather than after.
    """
    if ui.mode != ROUTING:
        return
    accent = config.COLOR_ROUTE

    # 1. rules about to be overwritten, under everything else
    for src in ui.route_replaces:
        old = ui.auto_forward.get(src)
        if old is None or src not in state.systems or old[0] not in state.systems:
            continue
        pa = ui.view.to_screen(state.systems[src].pos)
        pb = ui.view.to_screen(state.systems[old[0]].pos)
        _draw_rule_flow(surface, pa, pb, config.node_radius(state.systems[src].production),
                        config.node_radius(state.systems[old[0]].production),
                        _BTN_DANGER[1], config.s(2), animate=False)

    # 2. the planned chain
    for src, (dest, _keep) in ui.route_plan.items():
        if src not in state.systems or dest not in state.systems:
            continue
        pa = ui.view.to_screen(state.systems[src].pos)
        pb = ui.view.to_screen(state.systems[dest].pos)
        _draw_rule_flow(surface, pa, pb, config.node_radius(state.systems[src].production),
                        config.node_radius(state.systems[dest].production),
                        accent, config.s(3), animate=True)

    # 3. rings on the selected group, and markers on the ones that can't be served
    for sid in ui.route_sel:
        sys = state.systems.get(sid)
        if sys is None:
            continue
        sp = ui.view.to_screen(sys.pos)
        r = config.node_radius(sys.production) + config.NODE_RING_PAD
        bad = sid in ui.route_unroutable
        pygame.draw.circle(surface, _BTN_DANGER[1] if bad else accent, sp, r, max(2, config.s(3)))

    # 4. the destination ring (labels and the drag box go on top of the nodes,
    #    in _draw_route_overlay)
    if ui.route_dest is not None and ui.route_dest in state.systems:
        dp = ui.view.to_screen(state.systems[ui.route_dest].pos)
        dr = config.node_radius(state.systems[ui.route_dest].production)
        pygame.draw.circle(surface, accent, dp, dr + config.s(10), max(2, config.s(2)))
        pygame.draw.circle(surface, accent, dp, dr + config.NODE_RING_PAD, max(2, config.s(3)))


def _draw_route_overlay(surface, state: GameState, ui: Ui) -> None:
    """Route mode's labels and selection box, drawn *after* the systems — a node
    would otherwise sit on top of the box edge crossing it, and the box is the
    whole feedback for the gesture in progress. Same slot `_draw_drag` uses, for
    the same reason.
    """
    if ui.mode != ROUTING:
        return
    small = _fonts()["small"]
    for sid in ui.route_unroutable:
        sys = state.systems.get(sid)
        if sys is None:
            continue
        sp = ui.view.to_screen(sys.pos)
        _label_pill(surface, small, "no route", _BTN_DANGER[1],
                    (sp[0], sp[1] - config.node_radius(sys.production) - config.s(15)))
    for sid in ui.route_cycles:
        sys = state.systems.get(sid)
        if sys is None:
            continue
        sp = ui.view.to_screen(sys.pos)
        _label_pill(surface, small, "loop", _BTN_DANGER[1],
                    (sp[0], sp[1] + config.node_radius(sys.production) + config.s(11)))
    if ui.route_box:
        x0, y0 = ui.drag_start
        x1, y1 = ui.drag_pos
        box = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        pygame.draw.rect(surface, config.COLOR_ROUTE, box, config.s(2),
                         border_radius=config.s(3))


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
        (mx - w // 2, my - h - m),  # above (preferred — matches the old anchor)
        (mx + m, my - h // 2),  # right
        (mx - w - m, my - h // 2),  # left
        (mx - w // 2, my + m),  # below
    )
    nodes = [(ui.view.to_screen(s.pos), config.node_radius(s.production) + config.s(6)) for s in state.systems.values()]
    best, best_score = (x_lo, y_lo), None
    for cx, cy in candidates:
        x = _clamp(cx, x_lo, x_hi)
        y = _clamp(cy, y_lo, y_hi)
        rect = pygame.Rect(x, y, w, h)
        score = sum(1 for (px, py), r in nodes if rect.inflate(2 * r, 2 * r).collidepoint(px, py))
        if best_score is None or score < best_score:
            best, best_score = (x, y), score
            if score == 0:
                break  # a fully clear spot — take it
    return best


def _draw_send_popup(surface, state: GameState, ui: Ui) -> None:
    """The on-map action panel: the one editor for a ship count, opened both when a
    destination is picked and when an already-queued order or standing rule is
    reopened (see Ui.edit_order / Ui.edit_forward). Send/Forward tabs choose between
    a one-shot send (auto-committed, default send-all, retuned with the slider, −/+,
    Half, All) and a standing forward rule (the same controls set ships to keep). The
    button at the bottom discards whichever is live — labelled Cancel while composing,
    Delete when the popup was opened on something that already existed.
    Records every control's hit-rect on ``ui`` for input (store-rect-then-test)."""
    if ui.mode != CHOOSING or ui.selected not in state.systems or ui.dest not in state.systems:
        return
    pa = ui.view.to_screen(state.systems[ui.selected].pos)
    pb = ui.view.to_screen(state.systems[ui.dest].pos)
    mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)

    dest = state.systems[ui.dest]
    src = state.systems[ui.selected]
    garrison = src.ships
    font = _fonts()["small"]
    pad, gap = config.SEND_POPUP_PAD, config.SEND_POPUP_GAP
    # rows are tall enough to hold their own label, and to be tapped on a phone
    bh = _tap_size(max(config.SEND_POPUP_BTN_H, font.get_height() + config.ROW_GAP))
    w = config.SEND_POPUP_W
    # Fixed layout — the same seven rows on either tab so the box never resizes:
    # tabs, title, effect caption, stepper, slider, two presets, cancel.
    rows = 7
    sw, sh = surface.get_size()
    # On a short screen at touch scale the stack can outgrow the band it is placed
    # in, and since the clamp below pins an oversized panel to the top, what falls
    # off the bottom (and out of draw's clip) is the destructive Delete row — still
    # live, because input hit-tests the recorded rect, not what survived clipping.
    # So give up the tap floor before giving up a row, down to what the labels need.
    # The budget is the placement band itself, not the viewport, so the two agree.
    y_lo = config.HUD_TOP_H + 2
    budget = sh - config.HUD_BOTTOM_H - y_lo
    if pad * 2 + bh * rows + gap * (rows - 1) > budget:
        bh = max(font.get_height() + config.ROW_GAP, (budget - pad * 2 - gap * (rows - 1)) // rows)
    h = pad * 2 + bh * rows + gap * (rows - 1)

    # placement: honour a user-dragged position (clamped to stay reachable),
    # else auto-anchor to whichever side of the lane covers the fewest nodes
    x_lo, x_hi = 0, sw - config.HUD_RIGHT_W - w
    y_hi = sh - config.HUD_BOTTOM_H - h
    if ui.popup_pos is not None:
        x = _clamp(ui.popup_pos[0], x_lo, x_hi)
        y = _clamp(ui.popup_pos[1], y_lo, y_hi)
    else:
        x, y = _popup_anchor(surface, state, ui, mid, w, h)
    ui.popup_rect = (x, y, w, h)

    panel = pygame.Rect(x, y, w, h)
    pygame.draw.rect(surface, (20, 24, 36), panel, border_radius=config.s(8))
    accent = _forward_accent() if ui.forward_armed else config.COLOR_SELECT
    pygame.draw.rect(surface, accent, panel, config.s(1), border_radius=config.s(8))

    inner = x + pad
    iw = w - pad * 2
    tw = (iw - gap) // 2  # half-width for paired buttons
    cy = y + pad

    # tab row: Send | Forward — drawn as tabs (active one blends into the body
    # below; a separator line under the row is broken beneath the active tab)
    send_tab = pygame.Rect(inner, cy, tw, bh)
    fwd_tab = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
    _draw_tab(surface, send_tab, "Send", not ui.forward_armed, config.COLOR_SELECT)
    _draw_tab(surface, fwd_tab, "Forward", ui.forward_armed, _forward_accent())
    active = send_tab if not ui.forward_armed else fwd_tab
    line_y = cy + bh
    lw = config.s(1)
    pygame.draw.line(surface, (70, 80, 104), (inner, line_y), (inner + iw, line_y), lw)
    pygame.draw.line(surface, (20, 24, 36), (active.left, line_y), (active.right, line_y), lw)
    ui.send_tab_rect = (send_tab.x, send_tab.y, send_tab.w, send_tab.h)
    ui.forward_tab_rect = (fwd_tab.x, fwd_tab.y, fwd_tab.w, fwd_tab.h)
    cy += bh + gap

    # title: source -> destination on the left, destination garrison right-aligned.
    # Star names where they fit the box beside that garrison, ids where they don't —
    # the flavour is worth a line only while it stays inside the panel.
    ships_lbl = f"{dest.ships}sh"
    title = f"{src.short} -> {dest.short}"
    if font.size(title)[0] > iw - font.size(ships_lbl)[0] - gap:
        title = f"Sys {ui.selected} -> {ui.dest}"
    _text(surface, font, title, accent, midleft=(inner, cy + bh // 2))
    _text(surface, font, ships_lbl, config.player_color(dest.owner_id), midleft=(inner + iw - font.size(ships_lbl)[0], cy + bh // 2))
    cy += bh + gap

    # effect caption: what this actually does, in plain words (dim)
    if ui.forward_armed:
        effect = f"~{max(0, garrison - ui.keep)}/turn  ·  keep {ui.keep}"
    else:
        effect = f"send {ui.chosen}  ·  {max(0, garrison - ui.chosen)} home"
    _text(surface, font, effect, config.COLOR_TEXT_DIM, center=(inner + iw // 2, cy + bh // 2))
    cy += bh + gap

    # stepper row: [−]  value  [+]  — the send count, or (Forward) how many to keep
    s = _tap_size(config.STEPPER_SIZE)
    minus = pygame.Rect(inner, cy + (bh - s) // 2, s, s)
    plus = pygame.Rect(inner + iw - s, cy + (bh - s) // 2, s, s)
    _draw_step_button(surface, minus, "-", accent)
    _draw_step_button(surface, plus, "+", accent)
    label = f"keep {ui.keep}" if ui.forward_armed else str(ui.chosen)
    _text(surface, _fonts()["normal"], label, config.COLOR_TEXT, center=(inner + iw // 2, cy + bh // 2))
    ui.minus_rect = (minus.x, minus.y, minus.w, minus.h)
    ui.plus_rect = (plus.x, plus.y, plus.w, plus.h)
    cy += bh + gap

    # slider row: the coarse move −/+ can't make. The whole row is the grab target
    # (a dead zone at either end would fall through to the popup-drag underneath),
    # and it is recorded from this frame's panel position, so it follows the popup
    # wherever the player drags it.
    track = pygame.Rect(inner, cy, iw, bh)
    lo, hi, value = ui.slider_range(state)
    t = 0.0 if hi <= lo else max(0.0, min(1.0, (value - lo) / (hi - lo)))
    _draw_slider(surface, track, t, accent, config.COLOR_TEXT)
    ui.slider_rect = (track.x, track.y, track.w, track.h)
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

    # Discard row (both tabs): drop the active send / forward rule. It reads
    # "Cancel" only while composing — reopened on something that was already on the
    # board, the same press is a deletion, and should say so.
    cancel = pygame.Rect(inner, cy, iw, bh)
    if ui.editing_existing:
        discard = "Delete rule" if ui.forward_armed else "Delete order"
    else:
        discard = "Cancel"
    _draw_popup_button(surface, cancel, discard, danger=True)
    ui.cancel_rect = (cancel.x, cancel.y, cancel.w, cancel.h)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v)) if hi >= lo else lo


# Slider track colour, shared by the popup's count slider and the scrubber — kept
# local to the drawing module like the HUD button palette above.
_SLIDER_TROUGH = (40, 44, 60)


def _draw_slider(surface, rect: pygame.Rect, t: float, fill_col, knob_col) -> None:
    """A horizontal slider filling ``rect``: trough, filled portion, round knob at
    fraction ``t``. Shared by the send popup's count slider and history mode's turn
    scrubber, which are the same widget at different sizes.

    The knob *travels* over ``rect`` inset by its own radius at each end, so it
    never overhangs the box it sits in — and callers mapping a pointer x back to a
    value must invert exactly that (see ``Ui.set_slider_from_x``), or the knob
    drifts away from the finger at the extremes.
    """
    thick = config.SLIDER_TRACK_H
    knob = config.SLIDER_KNOB_R
    cy = rect.centery
    travel = max(0, rect.w - 2 * knob)
    trough = pygame.Rect(rect.x + knob, cy - thick // 2, travel, thick)
    pygame.draw.rect(surface, _SLIDER_TROUGH, trough, border_radius=max(1, thick // 2))
    fill_w = int(travel * max(0.0, min(1.0, t)))
    if fill_w > 0:
        pygame.draw.rect(surface, fill_col, pygame.Rect(trough.x, trough.y, fill_w, thick), border_radius=max(1, thick // 2))
    hx = trough.x + fill_w
    pygame.draw.circle(surface, knob_col, (hx, cy), knob)
    pygame.draw.circle(surface, fill_col, (hx, cy), knob, config.s(2))


def _draw_popup_button(surface, rect: pygame.Rect, label: str, active: bool = False, danger: bool = False, accent=config.COLOR_SELECT) -> None:
    """A small labelled button in the send popup. ``active`` lights it up in the
    mode ``accent`` (the preset matching the current value); ``danger`` tints it
    red (Cancel); otherwise it's a plain recessed button."""
    if active:
        fill = tuple(c * 3 // 10 for c in accent)  # darkened accent wash
        edge, col = accent, config.COLOR_TEXT
    elif danger:
        fill, edge, col = (58, 38, 42), (170, 96, 104), config.COLOR_TEXT
    else:
        fill, edge, col = (30, 36, 52), (70, 80, 104), config.COLOR_TEXT_DIM
    pygame.draw.rect(surface, fill, rect, border_radius=config.s(5))
    pygame.draw.rect(surface, edge, rect, config.s(1), border_radius=config.s(5))
    _text(surface, _fonts()["small"], label, col, center=rect.center)


def _forward_accent() -> tuple[int, int, int]:
    """Accent colour for Forward mode — a teal, distinct from the send yellow."""
    return (110, 205, 195)


def _draw_tab(surface, rect: pygame.Rect, label: str, active: bool, accent) -> None:
    """A tab in the send popup's Send/Forward switcher. The active tab takes the
    panel-body fill (so it reads as connected to the body) with a bright top
    accent in its mode colour; inactive tabs are darker and dim-labelled."""
    radius = config.s(6)
    if active:
        pygame.draw.rect(surface, (20, 24, 36), rect, border_top_left_radius=radius, border_top_right_radius=radius)
        top = rect.top + config.s(1)
        pygame.draw.line(surface, accent, (rect.left + config.s(2), top), (rect.right - config.s(2), top), config.s(2))
        col = accent
    else:
        pygame.draw.rect(surface, (12, 14, 22), rect, border_top_left_radius=radius, border_top_right_radius=radius)
        col = config.COLOR_TEXT_DIM
    _text(surface, _fonts()["small"], label, col, center=rect.center)


def _draw_step_button(surface, rect: pygame.Rect, sign: str, color) -> None:
    """A small filled −/+ button glyph inside ``rect``."""
    pygame.draw.rect(surface, config.COLOR_BG, rect, border_radius=config.s(4))
    pygame.draw.rect(surface, color, rect, config.s(1), border_radius=config.s(4))
    cx, cy = rect.center
    r = rect.w // 4
    lw = config.s(2)
    pygame.draw.line(surface, color, (cx - r, cy), (cx + r, cy), lw)  # − (and +'s bar)
    if sign == "+":
        pygame.draw.line(surface, color, (cx, cy - r), (cx, cy + r), lw)


def _draw_return_glyph(surface, rect, color) -> None:
    """A drawn ⏎ return/enter arrow inside ``rect`` (x, y, w, h). Drawn rather
    than typed because the monospace font lacks the ⏎ glyph on many platforms."""
    x, y, w, h = rect
    by = y + int(h * 0.72)  # baseline of the horizontal stroke
    a = max(3, h // 4)  # arrowhead arm length
    lw = config.s(2)
    # down-stroke on the right, then left along the baseline to the arrow tip
    pygame.draw.lines(surface, color, False, [(x + w, y), (x + w, by), (x, by)], lw)
    # arrowhead pointing left
    pygame.draw.lines(surface, color, False, [(x + a, by - a), (x, by), (x + a, by + a)], lw)


def _draw_systems(surface, state: GameState, ui: Ui) -> None:
    valid_dests = set()
    if ui.selected is not None:
        valid_dests = set(state.systems[ui.selected].neighbors)

    for sys in state.systems.values():
        state_fog = _fog_state(ui, sys.id)
        if state_fog == "hidden":  # never seen — off the map entirely
            continue
        pos = ui.view.to_screen(sys.pos)
        radius = config.node_radius(sys.production)

        # selection / targeting rings — drawn even when fogged so a "?" system you
        # can still route a fleet to reads as selected / a valid destination
        # config.node_clearance() budgets for the widest of these (the selection
        # ring), so the map's padding always keeps a boundary node's circle whole.
        if sys.id == ui.selected:
            pygame.draw.circle(surface, config.COLOR_SELECT, pos, radius + config.NODE_RING_PAD, config.s(3))
        elif sys.id in valid_dests:
            pygame.draw.circle(surface, config.COLOR_LANE_HILITE, pos, radius + config.s(4), config.s(2))
        elif sys.id == ui.hover:
            pygame.draw.circle(surface, config.COLOR_TEXT_DIM, pos, radius + config.s(4), config.s(2))

        if state_fog == "fogged":  # position known, contents not: hatched "?"
            pygame.draw.circle(surface, config.COLOR_FOG, pos, radius)
            pygame.draw.circle(surface, _brighten(config.COLOR_FOG), pos, radius, config.s(2))
            _draw_hatch(surface, pos, radius, _brighten(config.COLOR_FOG))
            _text(surface, _fonts()["normal"], "?", config.COLOR_TEXT, center=pos)
            continue

        # -- visible: full detail --
        color = config.player_color(sys.owner_id)
        pygame.draw.circle(surface, color, pos, radius)
        pygame.draw.circle(surface, _brighten(color), pos, radius, config.s(2))

        # production-progress ring
        if sys.production > 0 and (sys.owner_id != 0 or config.NEUTRAL_PRODUCES):
            frac = sys.prod_progress / sys.production
            if frac > 0:
                ring = radius * 2 + config.s(8)
                rect = pygame.Rect(0, 0, ring, ring)
                rect.center = pos
                pygame.draw.arc(surface, config.COLOR_TEXT_DIM, rect, math.pi / 2, math.pi / 2 + 2 * math.pi * frac, config.s(2))

        # ship count (deployable = garrison minus queued commitments), drawn in
        # whichever of dark/light text contrasts best with this owner's colour
        shown = ui.available(state, sys.id) if sys.owner_id == ui.human_id else sys.ships
        _text(surface, _fonts()["normal"], str(shown), config.text_on(color), center=pos)


def _draw_node_names(surface, state: GameState, ui: Ui) -> None:
    """Star names beneath the systems — flavour, so it yields to everything else.

    A second pass over the nodes (rather than a line inside _draw_systems) so every
    circle is already down: a label is placed only where it hits neither a node nor
    a label already drawn, which means a dense cluster quietly goes unlabelled and
    zooming in gives the names back. What you're looking at gets first refusal —
    the selection, then the hover, then the biggest systems.
    """
    if not config.SHOW_NODE_NAMES:
        return
    font = _fonts()["small"]
    clip = surface.get_clip() or surface.get_rect()

    shown = [s for s in state.systems.values() if s.name and _fog_state(ui, s.id) != "hidden"]
    taken: list[pygame.Rect] = []
    for sys in shown:  # every node's disc is an obstacle, labelled or not
        pos = ui.view.to_screen(sys.pos)
        r = config.node_radius(sys.production)
        taken.append(pygame.Rect(pos[0] - r, pos[1] - r, r * 2, r * 2))
    # ...and so is every label already on the map: a name landing on a lane's
    # travel time or a rule's "keep N" makes both of them unreadable, and those
    # carry information a name doesn't.
    for lane in state.lanes.values():
        if _fog_state(ui, lane.a) == "hidden" or _fog_state(ui, lane.b) == "hidden":
            continue
        pa, pb = ui.view.to_screen(state.systems[lane.a].pos), ui.view.to_screen(state.systems[lane.b].pos)
        turns = state.travel_turns(lane.a, lane.b) or lane.travel_turns
        taken.append(_pill_rect(font, str(turns), ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)))
    if not ui.history:
        for src, (dest, keep) in ui.auto_forward.items():
            if not keep:  # nothing drawn for the default — see _draw_forward_rules
                continue
            a, b = state.systems.get(src), state.systems.get(dest)
            if a is None or b is None or a.owner_id != ui.human_id:
                continue
            pa, pb = ui.view.to_screen(a.pos), ui.view.to_screen(b.pos)
            taken.append(_pill_rect(font, f"keep {keep}", _rule_label_center(pa, pb, font)))

    def rank(sys) -> tuple[int, int]:
        if sys.id == ui.selected:
            return (0, 0)
        if sys.id == ui.hover:
            return (1, 0)
        return (2, -config.node_radius(sys.production))

    for sys in sorted(shown, key=rank):
        pos = ui.view.to_screen(sys.pos)
        if not clip.collidepoint(pos):  # node itself panned off the map: a bare
            continue                    # name floating at the edge reads as noise
        r = config.node_radius(sys.production)
        pad = config.NODE_LABEL_PAD
        placement = None
        # below the node by preference, above it when that side is taken or off-map
        for side in (1, -1):
            rect = pygame.Rect((0, 0), font.size(sys.name))
            edge = pos[1] + side * (r + config.NODE_LABEL_GAP)
            rect.midtop = (pos[0], edge) if side > 0 else (pos[0], edge - rect.h)
            rect.clamp_ip(clip)  # a name at the map's edge slides in rather than being cut
            if clip.contains(rect) and rect.inflate(pad * 2, pad * 2).collidelist(taken) == -1:
                placement = rect
                break
        if placement is None:
            continue
        rect = placement
        taken.append(rect.inflate(pad * 2, pad * 2))
        if sys.id == ui.selected:
            color = config.COLOR_SELECT
        elif sys.id == ui.hover:
            color = config.COLOR_TEXT
        else:
            color = config.COLOR_TEXT_DIM
        _text(surface, font, sys.name, color, topleft=rect.topleft)


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
            pygame.draw.line(surface, color, (int(cx + u1), int(cy + u1 + k)), (int(cx + u2), int(cy + u2 + k)), 1)
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


def _draw_zoom_controls(surface, ui: Ui) -> None:
    """On-map camera cluster — Reset view and zoom −/+, the touch/no-wheel
    equivalent of scroll-to-zoom. Anchored to the map viewport's bottom-right
    corner, just above the HUD bottom bar (drawn after the map clip is lifted
    in ``draw()``, so it's never affected by it)."""
    px, py, pw, ph = config.play_rect()
    s = config.MAP_ZOOM_BTN_SIZE
    gap = config.s(8)
    inset = config.s(12)
    y = py + ph - inset - s
    font = _fonts()["small"]
    accent = _BTN_BLUE[1]

    plus = pygame.Rect(px + pw - inset - s, y, s, s)
    minus = pygame.Rect(plus.x - gap - s, y, s, s)
    reset_w = _btn_w(font, "Reset", s)
    reset = pygame.Rect(minus.x - gap - reset_w, y, reset_w, s)

    ui.zoom_plus_rect = (plus.x, plus.y, plus.w, plus.h)
    ui.zoom_minus_rect = (minus.x, minus.y, minus.w, minus.h)
    ui.reset_view_rect = _btn(surface, reset, "Reset", *_BTN_BLUE, font)
    _draw_step_button(surface, minus, "-", accent)
    _draw_step_button(surface, plus, "+", accent)


# --------------------------------------------------------------------------- #
# HUD
# --------------------------------------------------------------------------- #
def _draw_hud(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()

    _draw_side_panel(surface, state, ui)

    # top bar: turn counter, then the scoreboard starting clear of it (measured,
    # not a fixed column — "Turn 137" in a scaled-up font is far wider than one).
    pygame.draw.rect(surface, (18, 20, 30), (0, 0, w, config.HUD_TOP_H))
    turn = _text(surface, _fonts()["normal"], f"Turn {state.turn}", config.COLOR_TEXT, midleft=(config.HUD_PAD, config.HUD_TOP_H // 2))
    _draw_scoreboard(surface, state, ui, w, turn.right + config.HUD_PAD * 2)

    # bottom bar
    by = h - config.HUD_BOTTOM_H
    pygame.draw.rect(surface, (18, 20, 30), (0, by, w, config.HUD_BOTTOM_H))
    if ui.history:
        # the scrubber (drawn by _draw_scrubber, after the HUD) owns the bottom
        # bar in history mode — zero the live buttons so no stale click resolves.
        ui.end_turn_rect = ui.play_pause_rect = ui.history_button_rect = (0, 0, 0, 0)
        ui.autoplay_button_rect = ui.restart_live_button_rect = ui.menu_button_rect = (0, 0, 0, 0)
        ui.quit_button_rect = ui.clear_button_rect = (0, 0, 0, 0)
        ui.reset_view_rect = ui.zoom_minus_rect = ui.zoom_plus_rect = (0, 0, 0, 0)
        ui.route_button_rect = ui.route_cancel_rect = (0, 0, 0, 0)
        return

    # End-turn button: the single biggest, easiest touch target in the HUD — the
    # full width of the right info panel, reaching up above the ordinary bottom
    # bar (_draw_side_panel/_draw_order_list reserve the same config.END_TURN_H
    # so the queued-orders list never draws underneath it).
    ebw, ebh = config.HUD_RIGHT_W, config.END_TURN_H
    br = pygame.Rect(w - ebw, h - ebh, ebw, ebh)
    fill, edge = _BTN_GREEN

    # Route mode borrows this slot for its confirm. Taking the button away (rather
    # than guarding the action) is what makes ending the turn mid-plan impossible:
    # there is no end_turn_rect to hit, and the confirm inherits the biggest, most
    # obvious target in the HUD. A plan with nothing in it records no rect at all,
    # the same way every other dead control here goes to zero.
    if ui.mode == ROUTING:
        ui.end_turn_rect = (0, 0, 0, 0)
        if ui.route_plan:
            pygame.draw.rect(surface, fill, br, border_radius=config.s(8))
            pygame.draw.rect(surface, edge, br, config.s(3), border_radius=config.s(8))
            ui.route_confirm_rect = (br.x, br.y, br.w, br.h)
            label = f"Confirm {len(ui.route_plan)}"
        else:
            pygame.draw.rect(surface, (24, 28, 40), br, border_radius=config.s(8))
            pygame.draw.rect(surface, (40, 46, 66), br, config.s(3), border_radius=config.s(8))
            ui.route_confirm_rect = (0, 0, 0, 0)
            label = "Route" if not ui.route_sel else "Pick a target"
        colour = config.COLOR_TEXT if ui.route_plan else config.COLOR_TEXT_DIM
        _text(surface, _fonts()["big" if ui.route_plan else "normal"], label, colour,
              center=br.center)
        _draw_footer_buttons(surface, state, ui, by)
        return

    ui.end_turn_rect = (br.x, br.y, br.w, br.h)
    pygame.draw.rect(surface, fill, br, border_radius=config.s(8))
    pygame.draw.rect(surface, edge, br, config.s(3), border_radius=config.s(8))
    if ui.autoplay:
        _text(surface, _fonts()["big"], "AUTO", config.COLOR_TEXT, center=br.center)
    elif config.touch_ui:
        # no keyboard to press Enter on, so no glyph to hint at it
        _text(surface, _fonts()["big"], "End Turn", config.COLOR_TEXT, center=br.center)
    else:
        # "End Turn" + a drawn return-arrow icon: the monospace font has no ⏎
        # glyph (renders as tofu), so we draw the shortcut hint instead.
        font = _fonts()["big"]
        tw = font.size("End Turn")[0]
        gw, gap = config.s(26), config.s(10)
        left = br.centerx - (tw + gap + gw) // 2
        _text(surface, font, "End Turn", config.COLOR_TEXT, midleft=(left, br.centery))
        _draw_return_glyph(surface, (left + tw + gap, br.centery - config.s(9), gw, config.s(18)), config.COLOR_TEXT)

    _draw_footer_buttons(surface, state, ui, by)


# Every hit-rect the footer strip can record, so a frame can zero the lot before
# drawing whichever survive — a stale rect would otherwise still answer clicks.
_FOOTER_RECTS = (
    "quit_button_rect",
    "clear_button_rect",
    "menu_button_rect",
    "restart_live_button_rect",
    "history_button_rect",
    "autoplay_button_rect",
    "play_pause_rect",
    "fast_forward_rect",
    "route_button_rect",
    "route_cancel_rect",
)


def _draw_footer_buttons(surface, state: GameState, ui: Ui, by: int) -> None:
    """The bottom bar's button strip, left of the sidebar / End Turn column: touch
    equivalents of the P / A / H / R / M / X keys plus Quit/Esc, so every live-play
    action is reachable without a keyboard.

    Each button is sized to its own measured label and the row is laid out
    right-to-left. Where the strip is too narrow to hold them all — a portrait
    phone, or a shrunken desktop window — the least essential are dropped (rects
    zeroed) rather than drawn overlapping. The ranking matters because a touch
    player has no keys to fall back on: Menu survives longest, since it reaches the
    setup screen and Quit from there, and Clear goes first, since the popup's own
    Cancel and the × on a queued row already do its job. Fast forward ranks just
    below those two, because it is only ever offered in the one situation it is the
    point of the screen — watching a match you are out of.

    Route mode gets a strip of its own rather than extra buttons on this one — see
    the branch below.
    """
    font = _fonts()["normal"]
    fbh = config.FOOTER_BTN_H
    y = by + (config.HUD_BOTTOM_H - fbh) // 2

    # (ui attribute, label, fill, edge, how long it survives a squeeze), in visual
    # order — left to right. play/pause is hidden under autoplay, which drives turns
    # on its own timer and would make it a no-op; history needs a recorded turn to
    # scrub through.
    specs: list[tuple[str, str, tuple, tuple, int]] = []

    # Route mode replaces the strip wholesale rather than adding to it: the shared
    # zeroing loop below then takes every live-play rect out of service for free,
    # so nothing from the ordinary strip can be clicked under an open plan.
    if ui.mode == ROUTING:
        specs.append(("route_cancel_rect", _key_hint("Cancel", "Esc"), *_BTN_RED, 4))
        specs.append(("menu_button_rect", _key_hint("Menu", "M"), *_BTN_BLUE, 2))
        _lay_out_footer(surface, ui, specs, y, fbh, font)
        return

    # quit is the only touch equivalent of Esc — without it a player with no
    # keyboard has no way out. Opens the confirm modal, like Esc; on the web that
    # can only ask the browser to close the window (see main.leave_app).
    specs.append(("quit_button_rect", _key_hint("Quit", "Esc"), *_BTN_RED, 8))
    # clear/cancel mirrors X (and Backspace/Delete): discards the send being
    # adjusted, or drops the highlighted order/rule; a no-op when nothing is.
    specs.append(("clear_button_rect", _key_hint("Clear", "X"), *_BTN_BLUE, 1))
    specs.append(("menu_button_rect", _key_hint("Menu", "M"), *_BTN_BLUE, 9))
    # new map reseeds mid-game too (not just at game end), with no confirmation —
    # matching the R key exactly.
    specs.append(("restart_live_button_rect", _key_hint("New map", "R"), *_BTN_AMBER, 2))
    if state.turn > 0:
        specs.append(("history_button_rect", _key_hint("History", "H"), *_BTN_VIOLET, 3))
    # autoplay hands the human seat's decisions to the AI, or takes control back;
    # shown either way, unlike play/pause.
    specs.append(("autoplay_button_rect", _key_hint("Take control" if ui.autoplay else "Autoplay", "A"), *(_BTN_ACTIVE if ui.autoplay else _BTN_BLUE), 4))
    # Route mode: multi-select a group of systems and forward them all to one
    # destination. Gated on the same predicate as the G key, so the button is
    # drawn exactly when the mode means something.
    if ui.can_route(state):
        specs.append(("route_button_rect", _key_hint("Route", "G"), *_BTN_TEAL, 5))
    if not ui.autoplay:
        specs.append(("play_pause_rect", _key_hint("Pause" if ui.playing else "Play", "P"), *(_BTN_ACTIVE if ui.playing else _BTN_BLUE), 6))
    # Fast forward: only while the human is knocked out and the match plays on, so
    # the rest of it can be watched at speed rather than a turn every 350ms. Same
    # gate the F key goes through, so the button is drawn exactly when it means
    # something.
    if ui.can_fast_forward(state):
        specs.append(
            ("fast_forward_rect", _key_hint("Normal speed" if ui.fast_forward else "Fast forward", "F"), *(_BTN_ACTIVE if ui.fast_forward else _BTN_BLUE), 7)
        )

    _lay_out_footer(surface, ui, specs, y, fbh, font)


def _lay_out_footer(surface, ui: Ui, specs, y: int, fbh: int, font) -> None:
    """Measure, squeeze and draw one footer strip, right-to-left.

    Every rect the strip can ever record is zeroed first, so whichever buttons this
    call *doesn't* draw stop answering clicks — that is what lets route mode swap
    the strip out and know nothing from the live-play one is left live.
    """
    w = surface.get_width()
    widths = {spec[0]: _btn_w(font, spec[1]) for spec in specs}
    avail = w - config.HUD_RIGHT_W - config.HUD_PAD * 2

    def row_w(items) -> int:
        return sum(widths[s[0]] for s in items) + config.BTN_GAP * (len(items) - 1)

    shown = list(specs)
    while shown and row_w(shown) > avail:
        shown.remove(min(shown, key=lambda s: s[4]))

    for attr in _FOOTER_RECTS:
        setattr(ui, attr, (0, 0, 0, 0))
    right = w - config.HUD_RIGHT_W - config.HUD_PAD
    for attr, label, fill, edge, _keep in reversed(shown):
        rect = pygame.Rect(right - widths[attr], y, widths[attr], fbh)
        setattr(ui, attr, _btn(surface, rect, label, fill, edge, font))
        right = rect.x - config.BTN_GAP


_DEAD_COLOR = (92, 96, 110)


def _draw_scoreboard(surface, state: GameState, ui: Ui, w: int, x0: int) -> None:
    """Per-player standings in the top bar: swatch, systems, ships, production.

    Starts at ``x0`` (clear of the turn counter) and runs left-to-right by measured
    width so it never runs off-screen; names are shown when the whole row fits, but
    a full six-player table drops them in favour of the colour swatch (identity is
    colour-coded everywhere else too).

    Fogged (per the human's visibility): a rival is **live** while any of its
    systems is in sight, **frozen** (last-known stats, hatched swatch + "?") once
    seen but no longer in sight, and **omitted entirely** until first sighted — so
    with heavy fog you may not even know how many rivals are out there. With fog
    off every living rival is in sight, so this matches the classic scoreboard.
    """
    font = _fonts()["small"]
    cy = config.HUD_TOP_H // 2
    sw_size = config.s(12)  # colour swatch side
    gap, sw_w = config.s(22), sw_size + config.s(6)

    def vis(pid: int) -> str:
        """live | frozen | out | hidden."""
        p = state.players[pid]
        live = pid == ui.human_id or any(state.systems[s].owner_id == pid for s in ui.visible)
        if not (live or pid in ui.player_intel):
            return "hidden"  # never sighted — you don't know it exists
        if not p.alive:
            return "out"
        return "live" if live else "frozen"

    seats = [pid for pid in sorted(state.players) if not state.players[pid].is_neutral and vis(pid) != "hidden"]
    if not seats:
        return

    def label(pid: int, v: str, with_name: bool) -> str:
        p = state.players[pid]
        if v == "out":
            return f"{p.name} out" if with_name else "out"
        systems, ships, prod = _player_stats(state, pid) if v == "live" else ui.player_intel[pid]
        name = f"{p.name} " if with_name else ""
        mark = "" if v == "live" else " ?"  # stale, last-known intel
        return f"{name}{systems}s {ships}sh {prod:.1f}/t{mark}"

    def row_width(with_name: bool) -> int:
        return sum(sw_w + font.size(label(pid, vis(pid), with_name))[0] + gap for pid in seats)

    with_name = row_width(True) <= (w - x0 - config.HUD_PAD)
    x = x0
    for pid in seats:
        v = vis(pid)
        color = _DEAD_COLOR if v == "out" else config.player_color(pid)
        sw = pygame.Rect(x, cy - sw_size // 2, sw_size, sw_size)
        pygame.draw.rect(surface, color, sw, border_radius=config.s(3))
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
    pygame.draw.line(surface, (40, 44, 60), (px, py), (px, py + ph - 1), config.s(1))

    # queued-orders list occupies the bottom of the panel (always visible so
    # orders can be reviewed / removed); details fill the space above it. Hidden
    # in history mode — those orders belong to the live turn, not the past board.
    # `content_bottom` is where the details above have to stop: the top of the
    # queued-orders block when there is one, else the panel's own floor.
    # Route mode suppresses them for a different reason than history: their × and
    # row buttons would mutate `auto_forward` underneath the plan being previewed,
    # and the panel's space is better spent on what confirming would do.
    if ui.history or ui.mode == ROUTING:
        ui.order_hitboxes = []
        ui.forward_hitboxes = []
        ui.order_up_rect = ui.order_down_rect = (0, 0, 0, 0)
        content_bottom = py + ph - config.s(8)
    else:
        content_bottom = _draw_order_list(surface, state, ui)

    x, y = px + config.PANEL_PAD, py + config.PANEL_PAD
    if ui.mode == ROUTING:
        ui.clear_forward_rect = (0, 0, 0, 0)
        _panel_route(surface, state, ui, x, y, content_bottom)
        return
    # persistent "clear all forwarding" button, shown whenever any rule exists
    if ui.auto_forward:
        y = _draw_clear_forward_button(surface, ui, px, py + config.s(10))
    # A highlighted rule wins the panel's focus over a plain hover; while the popup
    # is open `ui.selected` is that rule's source anyway, so the two agree.
    editing_rule = ui.sel_forward if ui.sel_forward in ui.auto_forward else None
    if editing_rule is not None:
        focus = editing_rule
    else:
        focus = ui.selected if ui.selected is not None else ui.hover
    if focus is None or focus not in state.systems:
        _panel_legend(surface, x, y, content_bottom)
        return

    y = _panel_system(surface, state, ui, x, y, state.systems[focus])
    gap = config.ROW_GAP * 2  # breathing space between sections

    dest = _panel_lane_target(state, ui, focus)
    if dest is not None:
        y = _panel_lane(surface, state, ui, x, y + gap, focus, dest)

    if focus in ui.auto_forward:
        _panel_rule(surface, state, ui, x, y + gap, focus)


def _draw_clear_forward_button(surface, ui: Ui, px: int, y: int) -> int:
    """A slim panel-top button that clears every standing forward rule; records
    its hit-rect on ``ui``. Returns the y below it for the details that follow."""
    font = _fonts()["small"]
    label = f"Clear all forwarding ({len(ui.auto_forward)})"
    h = font.get_height() + config.s(10)
    r = pygame.Rect(px + config.s(12), y, config.HUD_RIGHT_W - config.s(24), h)
    pygame.draw.rect(surface, (58, 38, 42), r, border_radius=config.s(5))
    pygame.draw.rect(surface, (170, 96, 104), r, config.s(1), border_radius=config.s(5))
    _text(surface, font, label, config.COLOR_TEXT, center=r.center)
    ui.clear_forward_rect = (r.x, r.y, r.w, r.h)
    return y + h + config.s(10)


_ORDER_ROW_H = 22  # normal row pitch
_ORDER_ROW_SEL_H = 36  # a selected row grows, so its × delete button is easy to hit


def _draw_order_list(surface, state: GameState, ui: Ui) -> int:
    """Bottom-of-panel list of queued orders, followed by standing auto-forward
    rules. Records an (index, row, delete) hit-rect per order on
    ``ui.order_hitboxes`` and a (source_id, row, delete) hit-rect per rule on
    ``ui.forward_hitboxes``, both for input to test clicks against. The
    currently-selected row is drawn taller with a bigger delete button, so it can
    be removed by tap on touch (where the keyboard X shortcut isn't available).

    The block is capped at part of the panel so the system details above it are
    never pushed off the top, and scrolls (``ui.order_scroll``, driven by the ▲/▼
    buttons or the wheel over the panel) when there is more than fits — every entry
    stays reachable, which a bare "+N more" line could not promise.

    Returns the y this block starts at — the floor for whatever the panel draws
    above it, so the help text can't run down into the list."""
    ui.order_hitboxes = []
    ui.forward_hitboxes = []
    ui.order_up_rect = ui.order_down_rect = (0, 0, 0, 0)
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    # the panel's footer is the big End Turn button, not the ordinary bottom bar
    bottom = h - config.END_TURN_H
    floor = bottom - config.s(8)
    rules = sorted(ui.auto_forward.items())  # stable order across frames
    if not ui.pending and not rules:
        ui.order_scroll_max = 0
        return floor

    font = _fonts()["small"]
    x = px + config.s(12)
    row_w = config.HUD_RIGHT_W - config.s(24)
    gap = config.s(2)
    # a row is at least tall enough for its own text, whatever the scale does
    rh = max(config.s(_ORDER_ROW_H), font.get_height() + config.ROW_GAP)
    rh_sel = max(config.s(_ORDER_ROW_SEL_H), rh + config.s(10))
    arrow = _tap_size(config.s(18))
    title_h = max(_row_h(), arrow + config.s(4))

    # orders first, then rules; each order carries its own index into `pending`, so
    # a scrolled window still deletes and selects the right one.
    entries = [("order", i) for i in range(len(ui.pending))] + [("rule", src) for src, _ in rules]
    n = len(entries)

    def selected_of(kind, key) -> bool:
        return key == (ui.sel_order if kind == "order" else ui.sel_forward)

    def row_h(kind, key) -> int:
        return rh_sel if selected_of(kind, key) else rh

    # Half the panel at most: the details above are about whatever you have
    # selected right now, and used to get overrun by a long list.
    panel_h = bottom - config.HUD_TOP_H
    avail = max(rh, panel_h // 2 - title_h - config.s(8))

    def page_from(start: int) -> list:
        """The entries that fit when the window starts at ``start``."""
        out, used = [], 0
        for e in entries[start:]:
            if used + row_h(*e) > avail:
                break
            out.append(e)
            used += row_h(*e)
        return out

    # Furthest useful scroll: the first start whose page still reaches the last
    # entry, so scrolling can't strand you on a part-empty window past the end.
    max_scroll = max(0, n - 1)
    for start in range(n):
        if start + len(page_from(start)) >= n:
            max_scroll = start
            break
    ui.order_scroll_max = max_scroll
    first = max(0, min(ui.order_scroll, max_scroll))  # tolerate a stale offset
    shown = page_from(first)

    block_h = title_h + sum(row_h(*e) for e in shown)
    top = bottom - config.s(8) - block_h
    rule_y = top - config.s(6)
    pygame.draw.line(surface, (40, 44, 60), (px + config.s(8), rule_y), (px + config.HUD_RIGHT_W - config.s(8), rule_y), config.s(1))
    title = f"Queued ({n})" if max_scroll == 0 else f"Queued ({first + 1}-{first + len(shown)}/{n})"
    _text(surface, font, title, config.COLOR_TEXT_DIM, midleft=(x, top + title_h // 2))
    if max_scroll > 0:
        down = pygame.Rect(x + row_w - arrow, top + (title_h - arrow) // 2, arrow, arrow)
        up = pygame.Rect(down.x - arrow - config.s(4), down.y, arrow, arrow)
        _draw_arrow_button(surface, up, up=True, enabled=first > 0)
        _draw_arrow_button(surface, down, up=False, enabled=first < max_scroll)
        ui.order_up_rect = (up.x, up.y, up.w, up.h)
        ui.order_down_rect = (down.x, down.y, down.w, down.h)

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
            pygame.draw.rect(surface, (40, 46, 66), pygame.Rect(*row), border_radius=config.s(4))
        dsz = config.s(26) if selected else config.s(16)
        dr = (x + row_w - dsz - config.s(2), y + (this_h - gap - dsz) // 2, dsz, dsz)
        _draw_x_button(surface, dr, boxed=selected)
        _text(surface, font, label, config.COLOR_SELECT if selected else hcolor, midleft=(x + config.s(6), y + (this_h - gap) // 2))
        if kind == "order":
            ui.order_hitboxes.append((key, row, dr))
        else:
            ui.forward_hitboxes.append((key, row, dr))
        y += this_h
    return rule_y - config.s(6)


def _draw_arrow_button(surface, rect: pygame.Rect, up: bool, enabled: bool) -> None:
    """A ▲/▼ scroll button for the queued list — drawn rather than typed, since the
    monospace font has no arrow glyph (same reason as ``_draw_return_glyph``). A
    disabled one still draws, dimmed, so the pair doesn't jump about as you scroll.
    """
    color = config.COLOR_TEXT_DIM if enabled else (58, 62, 78)
    pygame.draw.rect(surface, config.COLOR_BG, rect, border_radius=config.s(4))
    pygame.draw.rect(surface, color, rect, config.s(1), border_radius=config.s(4))
    cx, cy = rect.center
    r = max(2, rect.w // 5)
    pts = [(cx - r, cy + r // 2), (cx + r, cy + r // 2), (cx, cy - r)] if up else [(cx - r, cy - r // 2), (cx + r, cy - r // 2), (cx, cy + r)]
    pygame.draw.polygon(surface, color, pts)


def _draw_x_button(surface, rect, boxed: bool = False) -> None:
    """A × delete glyph inside ``rect`` (x, y, w, h). ``boxed`` draws a framed
    background so an enlarged (selected-row) delete target reads as a button."""
    rx, ry, rw, rh = rect
    if boxed:
        radius = config.s(4)
        pygame.draw.rect(surface, (60, 40, 46), pygame.Rect(rx, ry, rw, rh), border_radius=radius)
        pygame.draw.rect(surface, (150, 90, 96), pygame.Rect(rx, ry, rw, rh), config.s(1), border_radius=radius)
    pad = max(3, rw // 4)
    lw = max(2, rw // 8)
    col = config.COLOR_TEXT if boxed else config.COLOR_TEXT_DIM
    pygame.draw.line(surface, col, (rx + pad, ry + pad), (rx + rw - pad, ry + rh - pad), lw)
    pygame.draw.line(surface, col, (rx + rw - pad, ry + pad), (rx + pad, ry + rh - pad), lw)


def _row(surface, x, y, text, color) -> int:
    """One line of small panel text; returns the y for the line below it."""
    if text:
        _text(surface, _fonts()["small"], text, color, topleft=(x, y))
    return y + _row_h()


def _head(surface, x, y, text, color) -> int:
    """A panel section heading, in the larger font; returns the y below it."""
    _text(surface, _fonts()["normal"], text, color, topleft=(x, y))
    return y + _row_h("normal")


def _panel_w() -> int:
    """Width available to panel text: the panel minus its two inner margins."""
    return config.HUD_RIGHT_W - config.PANEL_PAD * 2


def _head_named(surface, x, y, text, color) -> int:
    """A heading whose length we don't control, because a star name is in it: the
    normal font where it fits the panel, the small one where it doesn't (every
    catalogue name fits at that size, so nothing is ever cut)."""
    kind = "normal" if _fonts()["normal"].size(text)[0] <= _panel_w() else "small"
    _text(surface, _fonts()[kind], text, color, topleft=(x, y))
    return y + _row_h(kind)


def _rows_named(surface, x, y, text, color) -> int:
    """``_row`` for a line with a star name in it: reflowed to the panel width, so a
    long name wraps onto a second row instead of spilling past the panel edge."""
    for line in _wrap(_fonts()["small"], text, _panel_w()):
        y = _row(surface, x, y, line, color)
    return y


def _panel_system(surface, state: GameState, ui: Ui, x, y, sys) -> int:
    if sys.id not in ui.visible:
        # fogged system: we know where it is, not who holds it or how strong it is
        y = _head_named(surface, x, y, sys.label, config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "Owner: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "Ships: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "(out of sight)", config.COLOR_TEXT_DIM)
        return y
    y = _head_named(surface, x, y, sys.label, config.player_color(sys.owner_id))
    y = _row(surface, x, y, f"Owner: {config.player_name(sys.owner_id)}", config.player_color(sys.owner_id))
    if sys.owner_id == ui.human_id:
        y = _row(surface, x, y, f"Ships: {ui.available(state, sys.id)} free / {sys.ships} total", config.COLOR_TEXT)
    else:
        y = _row(surface, x, y, f"Ships: {sys.ships}", config.COLOR_TEXT)
    if sys.production > 0:
        y = _row(surface, x, y, f"Production: {sys.production} turns/ship", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, f"  building: {sys.prod_progress}/{sys.production}", config.COLOR_TEXT_DIM)
    else:
        y = _row(surface, x, y, "Production: none", config.COLOR_TEXT_DIM)
    y = _row(surface, x, y, f"Threat (adj): {_adjacent_enemy_strength(state, sys.id, sys.owner_id)}", config.COLOR_TEXT_DIM)
    fin, ein = _inbound_summary(state, sys.id, sys.owner_id)
    if fin or ein:
        y = _row(surface, x, y, f"Inbound: +{fin}f / {ein}e", config.COLOR_TEXT_DIM)
    y = _row(surface, x, y, f"Neighbours: {len(sys.neighbors)}", config.COLOR_TEXT_DIM)
    return y


def _panel_lane(surface, state: GameState, ui: Ui, x, y, src, dest) -> int:
    lane = state.lanes.get(lane_key(src, dest))
    if lane is None:
        return y
    d = state.systems[dest]
    y = _head_named(surface, x, y, f"Lane -> {d.label}", config.COLOR_TEXT)
    turns = state.travel_turns(src, dest) or lane.travel_turns
    y = _row(surface, x, y, f"{lane.length_ly} ly  ·  {turns} turns", config.COLOR_TEXT)
    if config.SHIP_SPEED_GROWTH_PCT > 0:
        y = _row(surface, x, y, f"Fleet speed: {config.ship_speed(state.turn):.1f} ly/turn", config.COLOR_TEXT_DIM)
    if dest in ui.visible:
        y = _row(surface, x, y, f"Target: {config.player_name(d.owner_id)} · {d.ships}sh", config.player_color(d.owner_id))
    else:
        y = _row(surface, x, y, "Target: ? · ?sh", config.COLOR_TEXT_DIM)
    if ui.mode == CHOOSING and ui.forward_armed:
        y = _row(surface, x, y, f"Forwarding · keep {ui.keep}", config.COLOR_SELECT)
    elif ui.mode == CHOOSING:
        y = _row(surface, x, y, f"Sending: {ui.chosen}", config.COLOR_SELECT)
    return y


def _panel_rule(surface, state: GameState, ui: Ui, x, y, src) -> int:
    dest, keep = ui.auto_forward[src]
    editing = src == ui.sel_forward
    color = config.COLOR_SELECT if editing else config.player_color(ui.human_id)
    y = _head(surface, x, y, "Auto-forward", color)
    # a rule can outlive its destination for a frame (the system was taken and the
    # map rebuilt); name it when it is still there, fall back to the bare id when not
    d = state.systems.get(dest)
    y = _rows_named(surface, x, y, f"-> {d.label if d else f'System {dest}'}, keep {keep}", config.COLOR_SELECT if editing else config.COLOR_TEXT)
    if config.touch_ui:
        hint = "−/+ sets keep  ·  × removes" if editing else "tap to edit"
    else:
        hint = "wheel or −/+: keep  ·  X: remove" if editing else "click to edit  ·  X: clear"
    y = _row(surface, x, y, hint, config.COLOR_TEXT_DIM)
    return y


# What the panel says when nothing is selected: how the game is actually played,
# as prose that `_wrap` reflows to the panel's width. The touch build gets the same
# rules in tap language and none of the keyboard shortcuts — there is no keyboard to
# use them from, and every one of them has a button in the bottom bar.
_LEGEND_TOUCH = """Take every system to win.

Tap a system to inspect it.

Tap one of yours, then a neighbour, to send its garrison down that lane — or drag
between the two.

Then: the slider, −/+, Half or All retune the count. The Forward tab makes it a standing rule
instead — everything past 'keep' flows on, every turn.

Tap a queued arrow, or its row below, to change it.

Route sets up many rules at once: pick a group of your systems (drag a box, or tap
them), choose a destination, and every system along the way forwards toward it.

Drag to pan, −/+ to zoom."""

_LEGEND_KEYS = """Take every system to win.

Click a system to inspect it.

Click one of yours, then a neighbour, to send its garrison down that lane — or
drag between the two.

Then: the slider, −/+, Half or All retune the count. The Forward tab makes it a standing rule
instead — everything past 'keep' flows on, every turn. Shift+click arms one
directly.

Click a queued arrow, or its row below, to change it; X clears it.

G opens Route, which sets up many rules at once: pick a group of your systems
(drag a box, or click them), choose a destination, and every system along the way
forwards toward it.

Drag to pan, wheel to zoom. Enter ends the turn, P plays on."""


def _panel_route(surface, state: GameState, ui: Ui, x, y, bottom: int) -> int:
    """What route mode is holding, and what confirming it would do.

    Counts rather than a list of hops: the map already shows every one of them, and
    the numbers are the part that isn't obvious from looking — how many rules this
    replaces, and how much of the selection can't actually be served.
    """
    y = _head(surface, x, y, "Route", config.COLOR_TEXT)
    font = _fonts()["small"]
    width = config.HUD_RIGHT_W - config.PANEL_PAD * 2
    dest = ui.route_dest
    lines: list[tuple[str, tuple[int, int, int]]] = [
        (f"{len(ui.route_sources())} selected", config.COLOR_ROUTE),
    ]
    if dest is None:
        lines.append(("no destination yet", config.COLOR_TEXT_DIM))
    elif dest in state.systems:
        sys = state.systems[dest]
        lines.append((f"to {sys.short}", config.player_color(sys.owner_id)))
        lines.append((f"{len(ui.route_plan)} rules", config.COLOR_TEXT))
    if ui.route_replaces:
        lines.append((f"{len(ui.route_replaces)} replaced", _BTN_AMBER[1]))
    if ui.route_unroutable:
        lines.append((f"{len(ui.route_unroutable)} unreachable", _BTN_DANGER[1]))
    if ui.route_cycles:
        lines.append((f"{len(ui.route_cycles)} would loop", _BTN_DANGER[1]))
    for text, colour in lines:
        if y + _row_h() > bottom:
            break
        y = _row(surface, x, y, text, colour)

    hint = ("Drag a box to pick a group. Tap a system to aim at it — tap it again to "
            "un-aim, and to drop it from the group.")
    y += config.ROW_GAP * 2
    for line in _wrap(font, hint, width):
        if y + _row_h() > bottom:
            break
        y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
    return y


def _panel_legend(surface, x, y, bottom: int) -> int:
    """How to play, shown whenever no system is in focus. Reflowed to the panel's
    width and cut off at ``bottom`` rather than spilling under the End Turn button
    — at a big UI scale the panel simply hasn't room for every line."""
    y = _head(surface, x, y, "Star Conquest", config.COLOR_TEXT)
    font = _fonts()["small"]
    width = config.HUD_RIGHT_W - config.PANEL_PAD * 2
    for line in _wrap(font, _LEGEND_TOUCH if config.touch_ui else _LEGEND_KEYS, width):
        if y + _row_h() > bottom:  # a whole row, so the returned y clears it too
            break
        y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
    return y


def _panel_lane_target(state: GameState, ui: Ui, focus: int):
    """Neighbour whose lane stats to show: the chosen dest, a hovered neighbour, or
    — failing both — the destination of a standing rule on the focused system.

    That last case is why a rule shows its lane at all: on the map the rule's own
    'keep' label sits on the lane it uses, so the panel is where you read that
    lane's length and travel time. (A queued order reopened for editing takes the
    first case instead — the popup sets ``ui.dest`` to its destination.)
    """
    if ui.mode == CHOOSING and ui.dest is not None:
        return ui.dest
    if ui.selected is not None and ui.hover is not None and ui.hover != ui.selected and state.are_adjacent(ui.selected, ui.hover):
        return ui.hover
    rule = ui.auto_forward.get(focus)
    if rule is not None and rule[0] in state.systems:
        return rule[0]
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


def _modal_buttons(surface, labels: tuple[str, str]) -> tuple[pygame.Rect, pygame.Rect]:
    """The (confirm, cancel) rects for a two-button modal, sized to fit the wider
    of ``labels`` — shared by every modal's drawer and its hit-tester, so the two
    can never disagree about where the buttons are."""
    w, h = surface.get_size()
    font = _fonts()["normal"]
    bw = max(_btn_w(font, s, config.s(200)) for s in labels)
    bh = max(config.s(42), font.get_height() + config.s(16))
    gap = config.s(12)
    y = h // 2 + config.s(24)
    return (pygame.Rect(w // 2 - bw - gap, y, bw, bh), pygame.Rect(w // 2 + gap, y, bw, bh))


def _draw_modal(surface, title: str, detail: str, buttons) -> None:
    """A confirm modal: veil, title, an optional detail line, and two buttons.
    ``buttons`` is ((label, fill, edge), (label, fill, edge)) — confirm then cancel.
    """
    w, h = surface.get_size()
    big, normal = _fonts()["big"], _fonts()["normal"]
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    # Stacked upward from the button row so the title and detail always clear it.
    detail_h = _row_h() if detail else 0
    y = h // 2 - config.s(12) - detail_h - big.get_height()
    _text(surface, big, title, config.COLOR_TEXT, center=(w // 2, y + big.get_height() // 2))
    if detail:
        y += _row_h("big")
        _text(surface, _fonts()["small"], detail, config.COLOR_TEXT_DIM, center=(w // 2, y + _fonts()["small"].get_height() // 2))
    for rect, (label, fill, edge) in zip(_modal_buttons(surface, (buttons[0][0], buttons[1][0])), buttons):
        _btn(surface, rect, label, fill, edge, normal)


def confirm_labels(confirm: str) -> tuple[str, str]:
    """(confirm, cancel) labels for a two-button modal, with the Y/N key hints
    dropped on a touch build. Shared with ``menu``'s resume prompt so every modal
    in the game words its answers the same way."""
    if config.touch_ui:
        return (confirm, "Cancel")
    return (f"{confirm} (Y/Enter)", "Cancel (N/Esc)")


def confirm_quit_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(quit, cancel) button rects — shared by the drawer and the hit-tester."""
    return _modal_buttons(surface, confirm_labels("Quit"))


def draw_confirm_quit(surface) -> None:
    """Modal 'are you sure?' veil, overlaid on whichever scene is beneath it."""
    quit_label, cancel = confirm_labels("Quit")
    _draw_modal(
        surface,
        "Quit Star Conquest?",
        "",
        (
            (quit_label, *_BTN_DANGER),
            (cancel, *_BTN_GREEN),
        ),
    )


def _draw_win_overlay(surface, state: GameState, ui: Ui) -> None:
    """The game-over veil: who won, the score, and the four (or five) things you
    can do next. The whole stack is measured and then centred, so a hidden hint or
    a missing verdict line tightens it up rather than leaving a gap or a collision.
    """
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 180))
    surface.blit(veil, (0, 0))

    big, normal = _fonts()["big"], _fonts()["normal"]
    if state.winner == 0:
        msg, color = "Mutual annihilation — draw", config.COLOR_TEXT
    else:
        msg = f"{config.player_name(state.winner)} wins!"
        color = config.player_color(state.winner)

    # Tappable buttons — touch equivalents of the T/R/M/H/Esc keys. Every box takes
    # the width of the widest label in the grid, so the rows stay square and no
    # label can spill into its neighbour once the font scales up.
    labels = [_key_hint("Retry", "T"), _key_hint("New map", "R"), _key_hint("Setup menu", "M"), _key_hint("Review history", "H")]
    quit_label = _key_hint("Quit", "Esc")
    share_label = _key_hint("Challenge a friend", "C")
    share = _shareable(state, ui)
    bw = max(_btn_w(normal, s) for s in labels + [quit_label])
    bh = max(config.s(40), normal.get_height() + config.s(16))
    gap = config.s(12)
    pad = config.s(12)

    result = _result_lines(state, ui)
    rows = 4 if share else 3
    stack = (
        _row_h("big")
        + sum(_row_h(kind) for kind, _t, _c in result)
        + pad
        + rows * (bh + gap)
        - gap
        + (_row_h() if (share and ui.share_msg) else 0)
    )
    y = (h - stack) // 2

    _text(surface, big, msg, color, center=(w // 2, y + big.get_height() // 2))
    y += _row_h("big")
    for kind, text, col in result:
        font = _fonts()[kind]
        _text(surface, font, text, col, center=(w // 2, y + font.get_height() // 2))
        y += _row_h(kind)
    y += pad

    # Retry replays this exact match from the opening position (a fork of the
    # finished log, so the completed record stays intact) — the main way back
    # in after a loss, so it leads and sits beside New map's fresh seed.
    # Setup menu and Review history follow; Challenge a friend (when offered)
    # comes next; Quit is always last, opening the same confirm modal as Esc.
    left, right = w // 2 - bw - gap // 2, w // 2 + gap // 2
    ui.retry_button_rect = _btn(surface, pygame.Rect(left, y, bw, bh), labels[0], *_BTN_AMBER)
    ui.restart_button_rect = _btn(surface, pygame.Rect(right, y, bw, bh), labels[1], *_BTN_GREEN)
    y += bh + gap
    ui.menu_button_rect = _btn(surface, pygame.Rect(left, y, bw, bh), labels[2], *_BTN_BLUE)
    ui.history_button_rect = _btn(surface, pygame.Rect(right, y, bw, bh), labels[3], *_BTN_VIOLET)
    y += bh + gap

    if share:
        sw = _btn_w(normal, share_label, bw)
        button_y = y
        ui.share_button_rect = _btn(surface, pygame.Rect(w // 2 - sw // 2, button_y, sw, bh), share_label, *_BTN_TEAL)
        y += bh + gap
        if ui.share_msg:
            _text(surface, _fonts()["small"], ui.share_msg, config.COLOR_TEXT_DIM, center=(w // 2, button_y + bh + _row_h() // 2))
            y += _row_h()
    else:
        ui.share_button_rect = (0, 0, 0, 0)

    ui.quit_button_rect = _btn(surface, pygame.Rect(w // 2 - bw // 2, y, bw, bh), quit_label, *_BTN_RED)


def _shareable(state: GameState, ui: Ui) -> bool:
    """Whether this result can become a challenge link: the human won it, and
    decided at least one turn themselves (a pure autoplay demo is not a score)."""
    return state.winner == ui.human_id and ui.hand_turns > 0


def _result_lines(state: GameState, ui: Ui) -> list[tuple[str, str, tuple[int, int, int]]]:
    """(font kind, text, colour) for the score under the win message: turns first,
    ships lost as the tiebreak, then the verdict against any challenge.

    Only for the human's own result — there is nothing to boast about, or to
    compare against a challenge, in watching two bots fight. When the match came
    from a challenge link, say outright whether the target fell. Returned rather
    than drawn so the overlay can measure the block before placing it.
    """
    if state.winner != ui.human_id:
        if ui.challenge_target is not None:
            return [("normal", "Challenge failed", (214, 130, 110))]
        return []

    lost = state.players[ui.human_id].ships_lost
    line = f"Conquered in {state.turn} turns  ·  {lost} ships lost"
    if ui.hand_turns < state.turn:
        line += f"  ·  {ui.hand_turns} played by hand"
    lines = [("normal", line, config.COLOR_TEXT)]

    if ui.challenge_target is None:
        return lines
    # Same ordering the score uses: fewer turns wins, ties broken on losses.
    target = ui.challenge_target
    beaten = (state.turn, lost) < target
    who = f" {ui.challenge_by}'s" if ui.challenge_by else ""
    verdict = f"Beat{who} {target[0]} turns / {target[1]} lost" if beaten else f"Short of{who} {target[0]} turns / {target[1]} lost"
    lines.append(("small", verdict, (130, 200, 150) if beaten else (214, 172, 92)))
    return lines


# Scrubber fill — a bright blue on the shared _SLIDER_TROUGH, echoing the play button.
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
    bh = config.FOOTER_BTN_H
    y = by + (config.HUD_BOTTOM_H - bh) // 2
    gap = config.BTN_GAP

    # Exit button (far left).
    exit_label = _key_hint("Exit", "Esc")
    ex = pygame.Rect(config.HUD_PAD, y, _btn_w(font, exit_label), bh)
    ui.exit_history_rect = _btn(surface, ex, exit_label, *_BTN_BLUE, font)

    # Play/pause button — auto-advances the scrubber at sim speed (main's play
    # timer). Reuses ui.play_pause_rect (the HUD's live play button, zeroed while
    # in history) and the same "toggle_play" action; colours match it too.
    play_label = _key_hint("Pause" if ui.playing else "Play", "P")
    # sized to the wider of the two states so the track doesn't jump on toggle
    pp = pygame.Rect(ex.right + gap, y, max(_btn_w(font, _key_hint(s, "P")) for s in ("Play", "Pause")), bh)
    ui.play_pause_rect = _btn(surface, pp, play_label, *(_BTN_ACTIVE if ui.playing else _BTN_BLUE), font)

    # Rewind button (far right). Its footprint is reserved even when hidden so the
    # track width and label position stay fixed as you scrub — the button only
    # appears for turns before the latest (when there is something to leave behind).
    rewind_label = "Rewind to here"
    rww = _btn_w(font, rewind_label)
    rw = pygame.Rect(w - rww - config.HUD_PAD, y, rww, bh)
    right_limit = rw.x - config.HUD_PAD
    if ui.history_turn >= ui.history_max:
        ui.rewind_button_rect = (0, 0, 0, 0)
    else:
        ui.rewind_button_rect = _btn(surface, rw, rewind_label, *_BTN_AMBER, font)

    # Turn / fog-mode label, right-aligned just left of the rewind button. Its
    # slot is sized to the widest label this session could show (max digits, the
    # longer "revealed" tag) so a changing turn number never nudges the track.
    tag = "revealed" if ui.history_reveal else "as seen"
    label = f"Turn {state.turn}/{ui.history_max}  ·  {tag}"
    widest = f"Turn {ui.history_max}/{ui.history_max}  ·  revealed"
    label_x = right_limit - font.size(widest)[0]
    _text(surface, font, label, config.COLOR_TEXT_DIM, midright=(right_limit, cy))

    # Track fills the space between the play button and the label slot.
    track_x = pp.right + config.HUD_PAD
    track_w = max(1, label_x - config.HUD_PAD - track_x)
    track = pygame.Rect(track_x, y, track_w, bh)
    ui.scrubber_rect = (track.x, track.y, track.w, track.h)
    t = 0.0 if ui.history_max <= 0 else ui.history_turn / ui.history_max
    _draw_slider(surface, track, t, _SCRUB_FILL, config.COLOR_TEXT)


def confirm_rewind_buttons(surface) -> tuple[pygame.Rect, pygame.Rect]:
    """(rewind, cancel) button rects — shared by the drawer and the hit-tester."""
    return _modal_buttons(surface, confirm_labels("Rewind"))


def draw_confirm_rewind(surface, turn: int) -> None:
    """Modal confirm for a destructive mid-game rewind (discards later turns)."""
    rewind, cancel = confirm_labels("Rewind")
    _draw_modal(
        surface,
        f"Rewind to turn {turn}?",
        "All turns after this one will be discarded.",
        (
            (rewind, *_BTN_AMBER),
            (cancel, *_BTN_GREEN),
        ),
    )
