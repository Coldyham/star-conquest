"""All drawing. Reads GameState and Ui; never mutates them, never imports the
engine or AI. Deliberately minimalist: circles for systems, lines for lanes,
triangles for fleets, numbers for ship counts.
"""

from __future__ import annotations

import math

import pygame

from . import config, fog
from .geometry import lerp
from .model import GameState, lane_key
from .viewstate import CHOOSING, SELECTED, Ui

_FONTS: dict[str, pygame.font.Font] = {}


def _fonts() -> dict[str, pygame.font.Font]:
    if not _FONTS:
        _FONTS["small"] = pygame.font.SysFont("consolas,menlo,monospace", config.FONT_SIZE_SMALL)
        _FONTS["normal"] = pygame.font.SysFont("consolas,menlo,monospace", config.FONT_SIZE)
        _FONTS["big"] = pygame.font.SysFont("consolas,menlo,monospace", config.FONT_SIZE_BIG, bold=True)
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
    # zero the −/+ button rects; whichever count draw runs (if any) re-records them
    ui.minus_rect = ui.plus_rect = (0, 0, 0, 0)
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
    if not ui.history:
        # the active count + −/+ buttons draw last of the map layer so nodes/fleets
        # never occlude them (they must stay visible and clickable)
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
        # a fleet shows only where at least one end of its lane is in full view
        # (your own systems always are, so your fleets stay visible while you hold
        # their endpoints; enemy movements appear only as they near your space)
        if f.source_id not in ui.visible and f.dest_id not in ui.visible:
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
    """Draw the active count label + −/+ buttons on top of the map layer so nodes
    and fleets never occlude them. Handles the move being composed (CHOOSING), the
    queued order being edited, and the standing rule's keep being edited; records
    the button hit-rects."""
    if ui.mode == CHOOSING and ui.selected is not None and ui.dest is not None:
        pa = ui.view.to_screen(state.systems[ui.selected].pos)
        pb = ui.view.to_screen(state.systems[ui.dest].pos)
        center = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 - 12)
        _draw_count_stepper(surface, center, ui.chosen, config.COLOR_SELECT, ui)
    elif ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
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
        return
    _text(surface, _fonts()["small"], _hint(ui), config.COLOR_TEXT_DIM,
          midleft=(14, by + config.HUD_BOTTOM_H // 2))

    # end-turn button
    bw, bh = 150, 32
    br = pygame.Rect(w - bw - 12, by + (config.HUD_BOTTOM_H - bh) // 2, bw, bh)
    ui.end_turn_rect = (br.x, br.y, br.w, br.h)
    pygame.draw.rect(surface, (46, 92, 60), br, border_radius=6)
    pygame.draw.rect(surface, (96, 190, 120), br, 2, border_radius=6)
    if ui.autoplay:
        _text(surface, _fonts()["normal"], "AUTO", config.COLOR_TEXT, center=br.center)
    else:
        # "End Turn" + a drawn return-arrow icon: the monospace font has no ⏎
        # glyph (renders as tofu), so we draw the shortcut hint instead.
        font = _fonts()["normal"]
        tw = font.size("End Turn")[0]
        gw, gap = 15, 8
        left = br.centerx - (tw + gap + gw) // 2
        _text(surface, font, "End Turn", config.COLOR_TEXT, midleft=(left, br.centery))
        _draw_return_glyph(surface, (left + tw + gap, br.centery - 6, gw, 12),
                           config.COLOR_TEXT)

    # play/pause button — sits left of End Turn; hidden while autoplay, which
    # drives turns on its own timer, would make it a no-op.
    leftmost = br.x
    if ui.autoplay:
        ui.play_pause_rect = (0, 0, 0, 0)
    else:
        pw, ph = 120, 32
        pr = pygame.Rect(br.x - pw - 10, br.y, pw, ph)
        ui.play_pause_rect = (pr.x, pr.y, pr.w, pr.h)
        fill = (92, 70, 46) if ui.playing else (40, 52, 78)
        edge = (190, 150, 96) if ui.playing else (110, 140, 200)
        pygame.draw.rect(surface, fill, pr, border_radius=6)
        pygame.draw.rect(surface, edge, pr, 2, border_radius=6)
        plabel = "Pause (P)" if ui.playing else "Play (P)"
        _text(surface, _fonts()["normal"], plabel, config.COLOR_TEXT, center=pr.center)
        leftmost = pr.x

    # history button — sits left of Play/Pause (or End Turn under autoplay).
    # Only meaningful once a turn has been recorded to scrub back through.
    if state.turn > 0:
        hw = 118
        hr = pygame.Rect(leftmost - hw - 10, br.y, hw, 32)
        ui.history_button_rect = (hr.x, hr.y, hr.w, hr.h)
        pygame.draw.rect(surface, (52, 46, 78), hr, border_radius=6)
        pygame.draw.rect(surface, (150, 130, 200), hr, 2, border_radius=6)
        _text(surface, _fonts()["normal"], "History (H)", config.COLOR_TEXT, center=hr.center)
    else:
        ui.history_button_rect = (0, 0, 0, 0)


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


def _hint(ui: Ui) -> str:
    if ui.autoplay:
        return "Autoplay — AI is playing all seats. A: take control  ·  M: setup menu  ·  Esc: quit."
    if ui.sel_order is not None:
        return "Editing queued order  ·  wheel or −/+ buttons: ship count  ·  X: remove  ·  right-click/Esc: done"
    if ui.sel_forward is not None:
        return "Editing auto-forward rule  ·  wheel or −/+ buttons: keep  ·  X: remove  ·  right-click/Esc: done"
    if ui.mode == SELECTED:
        return "Click a highlighted neighbour to send  ·  X: clear forward rule  ·  right-click/Esc: cancel"
    if ui.mode == CHOOSING:
        return "Wheel or −/+ buttons: count  ·  click: send once  ·  Shift+click: auto-forward rule  ·  right-click/Esc: back"
    return ("Click your system to select  ·  click a lane to edit orders  ·  "
            "Space/Enter: End Turn  ·  P: play/pause  ·  A: autoplay  ·  M: menu")


# --------------------------------------------------------------------------- #
# Info panel (right column)
# --------------------------------------------------------------------------- #
_ROW_H = 20


def _draw_side_panel(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    py = config.HUD_TOP_H
    ph = h - config.HUD_TOP_H - config.HUD_BOTTOM_H
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


def _editing_order(ui: Ui):
    """The queued order currently selected for editing, or None."""
    if ui.sel_order is not None and 0 <= ui.sel_order < len(ui.pending):
        return ui.pending[ui.sel_order]
    return None


_ORDER_ROW_H = 22


def _draw_order_list(surface, state: GameState, ui: Ui) -> None:
    """Bottom-of-panel list of queued orders, followed by standing auto-forward
    rules. Records a (row, delete) hit-rect per order on ``ui.order_hitboxes``
    (parallel to ``ui.pending``) and a (source_id, row, delete) hit-rect per
    rule on ``ui.forward_hitboxes``, both for input to test clicks against."""
    ui.order_hitboxes = []
    ui.forward_hitboxes = []
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    bottom = h - config.HUD_BOTTOM_H
    rules = sorted(ui.auto_forward.items())  # stable order across frames
    if not ui.pending and not rules:
        return

    x = px + 12
    row_w = config.HUD_RIGHT_W - 24
    top_limit = config.HUD_TOP_H + 8
    title_h = 22
    # cap visible rows so a long queue never swallows the whole panel
    max_rows = max(1, (bottom - 8 - top_limit - title_h) // _ORDER_ROW_H)
    # orders first, then rules, so existing order_hitboxes indices stay aligned
    entries = [("order", i) for i in range(len(ui.pending))] + [("rule", src) for src, _ in rules]
    n = len(entries)
    overflow = n > max_rows
    shown = min(n, max_rows - 1) if overflow else n

    block_h = title_h + (shown + (1 if overflow else 0)) * _ORDER_ROW_H
    top = bottom - 8 - block_h
    pygame.draw.line(surface, (40, 44, 60), (px + 8, top - 6),
                     (px + config.HUD_RIGHT_W - 8, top - 6), 1)
    _text(surface, _fonts()["small"], f"Queued ({n})", config.COLOR_TEXT_DIM, topleft=(x, top))

    y = top + title_h
    hcolor = config.player_color(ui.human_id)
    for kind, key in entries[:shown]:
        row = (x, y, row_w, _ORDER_ROW_H - 2)
        if kind == "order":
            o = ui.pending[key]
            selected = key == ui.sel_order
            label = f"{o.source_id}->{o.dest_id}   {o.ships} sh"
        else:
            dest, keep = ui.auto_forward[key]
            selected = key == ui.sel_forward
            label = f"{key}->{dest}   keep {keep}"
        if selected:
            pygame.draw.rect(surface, (40, 46, 66), pygame.Rect(*row), border_radius=4)
        dr = (x + row_w - 18, y + 1, 16, _ORDER_ROW_H - 4)
        _draw_x_button(surface, dr)
        _text(surface, _fonts()["small"], label, config.COLOR_SELECT if selected else hcolor,
              midleft=(x + 6, y + (_ORDER_ROW_H - 2) // 2))
        if kind == "order":
            ui.order_hitboxes.append((row, dr))
        else:
            ui.forward_hitboxes.append((key, row, dr))
        y += _ORDER_ROW_H
    if overflow:
        _text(surface, _fonts()["small"], f"+{n - shown} more", config.COLOR_TEXT_DIM,
              midleft=(x + 6, y + (_ORDER_ROW_H - 2) // 2))


def _draw_x_button(surface, rect) -> None:
    """A small × delete glyph inside ``rect`` (x, y, w, h)."""
    rx, ry, rw, rh = rect
    pad = 4
    col = config.COLOR_TEXT_DIM
    pygame.draw.line(surface, col, (rx + pad, ry + pad), (rx + rw - pad, ry + rh - pad), 2)
    pygame.draw.line(surface, col, (rx + rw - pad, ry + pad), (rx + pad, ry + rh - pad), 2)


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
    if ui.mode == CHOOSING:
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
        "Wheel or −/+ buttons: count",
        "Click: send once",
        "Shift+click: forward rule",
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
    bw, bh = 200, 42
    quit_r = pygame.Rect(cx - bw - 12, cy + 24, bw, bh)
    cancel_r = pygame.Rect(cx + 12, cy + 24, bw, bh)
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
    _text(surface, _fonts()["big"], msg, color, center=(w // 2, h // 2 - 40))
    _text(surface, _fonts()["normal"], "R: new map  ·  M: setup menu  ·  Esc: quit",
          config.COLOR_TEXT_DIM, center=(w // 2, h // 2))
    # Review-history button: enter history mode to scrub the finished game with
    # fog fully lifted (see what was happening behind the fog of war).
    bw, bh = 260, 40
    hr = pygame.Rect(w // 2 - bw // 2, h // 2 + 34, bw, bh)
    ui.history_button_rect = (hr.x, hr.y, hr.w, hr.h)
    pygame.draw.rect(surface, (52, 46, 78), hr, border_radius=6)
    pygame.draw.rect(surface, (150, 130, 200), hr, 2, border_radius=6)
    _text(surface, _fonts()["normal"], "Review history (H)", config.COLOR_TEXT,
          center=hr.center)


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

    # Track fills the space between the exit button and the label slot.
    track_x = ex.right + 16
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
    bw, bh = 220, 42
    rewind_r = pygame.Rect(cx - bw - 12, cy + 24, bw, bh)
    cancel_r = pygame.Rect(cx + 12, cy + 24, bw, bh)
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
