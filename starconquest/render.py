"""All drawing. Reads GameState and Ui; never mutates them, never imports the
engine or AI. Deliberately minimalist: circles for systems, lines for lanes,
triangles for fleets, numbers for ship counts.
"""

from __future__ import annotations

import math

import pygame

from . import config
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


def _text(surface, font, s, color, center=None, topleft=None, midleft=None):
    img = font.render(s, True, color)
    rect = img.get_rect()
    if center:
        rect.center = center
    elif topleft:
        rect.topleft = topleft
    elif midleft:
        rect.midleft = midleft
    surface.blit(img, rect)
    return rect


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #
def draw(surface: pygame.Surface, state: GameState, ui: Ui) -> None:
    surface.fill(config.COLOR_BG)
    _draw_lanes(surface, state, ui)
    _draw_forward_rules(surface, state, ui)     # standing auto-forward (dashed)
    _draw_pending(surface, state, ui)           # queued one-shot sends (solid)
    _draw_choosing_preview(surface, state, ui)  # the count you're adjusting now
    _draw_fleets(surface, state, ui)
    _draw_systems(surface, state, ui)
    _draw_hud(surface, state, ui)
    if state.winner is not None:
        _draw_win_overlay(surface, state)


# --------------------------------------------------------------------------- #
# World layer
# --------------------------------------------------------------------------- #
def _draw_lanes(surface, state: GameState, ui: Ui) -> None:
    for lane in state.lanes.values():
        pa = ui.view.to_screen(state.systems[lane.a].pos)
        pb = ui.view.to_screen(state.systems[lane.b].pos)
        # width & brightness encode travel time: slow lanes thicker+dimmer,
        # fast lanes thinner+brighter, so length variety reads at a glance.
        width, color = _lane_style(lane.travel_turns)
        # brighten the lanes touching the selected source system
        if ui.selected is not None and ui.selected in (lane.a, lane.b):
            color = config.COLOR_LANE_HILITE
            width = max(width, 3)
        pygame.draw.line(surface, color, pa, pb, width)
        # travel-time label at the midpoint, on a dark pill so it stays legible
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
        lx = int(pa[0] + (pb[0] - pa[0]) * 0.4)
        ly = int(pa[1] + (pb[1] - pa[1]) * 0.4)
        _text(surface, _fonts()["small"], str(o.ships), config.COLOR_TEXT, center=(lx, ly - 10))


def _draw_forward_rules(surface, state: GameState, ui: Ui) -> None:
    """Standing auto-forward rules as persistent dashed arrows (human colour)."""
    color = config.player_color(ui.human_id)
    for src, (dest, keep) in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id or dest not in state.systems:
            continue
        pa = ui.view.to_screen(s.pos)
        pb = ui.view.to_screen(state.systems[dest].pos)
        _draw_dashed_line(surface, color, pa, pb, width=2)
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy) or 1.0
        u = (dx / length, dy / length)
        _draw_triangle(surface, (pb[0] - u[0] * 20, pb[1] - u[1] * 20), u, color)
        _label_pill(surface, _fonts()["small"], f"keep {keep}", config.COLOR_TEXT_DIM,
                    ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 + 10))


def _draw_choosing_preview(surface, state: GameState, ui: Ui) -> None:
    """The move being composed right now: a bright arrow with the live count.

    This is what makes the mouse wheel visibly do something — the count here
    tracks ui.chosen as you scroll.
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
    _label_pill(surface, _fonts()["normal"], str(ui.chosen), color,
                ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 - 12))


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
        pos = ui.view.to_screen(sys.pos)
        radius = config.node_radius(sys.production)
        color = config.player_color(sys.owner_id)

        # selection / targeting rings
        if sys.id == ui.selected:
            pygame.draw.circle(surface, config.COLOR_SELECT, pos, radius + 6, 3)
        elif sys.id in valid_dests:
            pygame.draw.circle(surface, config.COLOR_LANE_HILITE, pos, radius + 4, 2)
        elif sys.id == ui.hover:
            pygame.draw.circle(surface, config.COLOR_TEXT_DIM, pos, radius + 4, 2)

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
    _draw_scoreboard(surface, state, w)

    # bottom bar
    by = h - config.HUD_BOTTOM_H
    pygame.draw.rect(surface, (18, 20, 30), (0, by, w, config.HUD_BOTTOM_H))
    _text(surface, _fonts()["small"], _hint(ui), config.COLOR_TEXT_DIM,
          midleft=(14, by + config.HUD_BOTTOM_H // 2))

    # end-turn button
    bw, bh = 150, 32
    br = pygame.Rect(w - bw - 12, by + (config.HUD_BOTTOM_H - bh) // 2, bw, bh)
    ui.end_turn_rect = (br.x, br.y, br.w, br.h)
    pygame.draw.rect(surface, (46, 92, 60), br, border_radius=6)
    pygame.draw.rect(surface, (96, 190, 120), br, 2, border_radius=6)
    label = "AUTO" if ui.autoplay else "End Turn ⏎"
    _text(surface, _fonts()["normal"], label, config.COLOR_TEXT, center=br.center)


def _draw_scoreboard(surface, state: GameState, w: int) -> None:
    """Per-player standings in the top bar: swatch, systems, ships, production.

    Laid out left-to-right by measured width so it never runs off-screen; names
    are shown when the whole row fits, but a full six-player table drops them in
    favour of the colour swatch (identity is colour-coded everywhere else too).
    """
    seats = [pid for pid in sorted(state.players) if not state.players[pid].is_neutral]
    if not seats:
        return
    font = _fonts()["small"]
    cy = config.HUD_TOP_H // 2
    gap, sw_w = 22, 18

    def label(pid: int, with_name: bool) -> str:
        p = state.players[pid]
        if not p.alive:
            return f"{p.name} out" if with_name else "out"
        systems, ships, prod = _player_stats(state, pid)
        name = f"{p.name} " if with_name else ""
        return f"{name}{systems}s {ships}sh {prod:.1f}/t"

    def row_width(with_name: bool) -> int:
        return sum(sw_w + font.size(label(pid, with_name))[0] + gap for pid in seats)

    with_name = row_width(True) <= (w - 120)
    x = 120
    for pid in seats:
        p = state.players[pid]
        color = config.player_color(pid) if p.alive else (92, 96, 110)
        pygame.draw.rect(surface, color, pygame.Rect(x, cy - 6, 12, 12), border_radius=3)
        txt = label(pid, with_name)
        _text(surface, font, txt, color, midleft=(x + sw_w, cy))
        x += sw_w + font.size(txt)[0] + gap


def _player_stats(state: GameState, pid: int) -> tuple[int, int, float]:
    """(systems owned, ships including in transit, production in ships/turn)."""
    systems = ships = 0
    for s in state.systems.values():
        if s.owner_id == pid:
            systems += 1
            ships += s.ships
    ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
    return systems, ships, _production_rate(state, pid)


def _production_rate(state: GameState, pid: int) -> float:
    """Long-run ships/turn: each owned system emits one ship per `production` turns."""
    return sum(1.0 / s.production for s in state.systems.values()
               if s.owner_id == pid and s.production > 0)


def _hint(ui: Ui) -> str:
    if ui.autoplay:
        return "Autoplay — AI is playing all seats. A: take control  ·  M: setup menu  ·  Esc: quit."
    if ui.sel_order is not None:
        return "Editing queued order  ·  wheel: ship count  ·  X: remove  ·  right-click/Esc: done"
    if ui.mode == SELECTED:
        return "Click a highlighted neighbour to send  ·  X: clear forward rule  ·  right-click/Esc: cancel"
    if ui.mode == CHOOSING:
        return "Wheel: count  ·  click: send once  ·  Shift+click: auto-forward rule  ·  right-click/Esc: back"
    return ("Click your system to select  ·  click a queued lane/list row to edit  ·  "
            "End Turn to resolve  ·  A: autoplay  ·  M: menu")


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
    # orders can be reviewed / removed); details fill the space above it.
    _draw_order_list(surface, state, ui)

    x, y = px + 14, py + 14
    editing = _editing_order(ui)
    focus = editing.source_id if editing else (ui.selected if ui.selected is not None else ui.hover)
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
    """Bottom-of-panel list of queued orders. Records a (row, delete) hit-rect
    per order on ``ui.order_hitboxes`` (parallel to ``ui.pending``) for input."""
    ui.order_hitboxes = []
    w, h = surface.get_size()
    px = w - config.HUD_RIGHT_W
    bottom = h - config.HUD_BOTTOM_H
    if not ui.pending:
        return

    x = px + 12
    row_w = config.HUD_RIGHT_W - 24
    top_limit = config.HUD_TOP_H + 8
    title_h = 22
    # cap visible rows so a long queue never swallows the whole panel
    max_rows = max(1, (bottom - 8 - top_limit - title_h) // _ORDER_ROW_H)
    n = len(ui.pending)
    overflow = n > max_rows
    shown = min(n, max_rows - 1) if overflow else n

    block_h = title_h + (shown + (1 if overflow else 0)) * _ORDER_ROW_H
    top = bottom - 8 - block_h
    pygame.draw.line(surface, (40, 44, 60), (px + 8, top - 6),
                     (px + config.HUD_RIGHT_W - 8, top - 6), 1)
    _text(surface, _fonts()["small"], f"Queued orders ({n})", config.COLOR_TEXT_DIM, topleft=(x, top))

    y = top + title_h
    hcolor = config.player_color(ui.human_id)
    for i in range(shown):
        o = ui.pending[i]
        row = (x, y, row_w, _ORDER_ROW_H - 2)
        selected = i == ui.sel_order
        if selected:
            pygame.draw.rect(surface, (40, 46, 66), pygame.Rect(*row), border_radius=4)
        dr = (x + row_w - 18, y + 1, 16, _ORDER_ROW_H - 4)
        _draw_x_button(surface, dr)
        label = f"{o.source_id}→{o.dest_id}   {o.ships} sh"
        _text(surface, _fonts()["small"], label, config.COLOR_SELECT if selected else hcolor,
              midleft=(x + 6, y + (_ORDER_ROW_H - 2) // 2))
        ui.order_hitboxes.append((row, dr))
        y += _ORDER_ROW_H
    if overflow:
        _text(surface, _fonts()["small"], f"+{n - shown} more (edit via lanes)",
              config.COLOR_TEXT_DIM, midleft=(x + 6, y + (_ORDER_ROW_H - 2) // 2))


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
    y = _row(surface, x, y, "wheel: count · X: remove", config.COLOR_TEXT_DIM)
    return y


def _row(surface, x, y, text, color) -> int:
    if text:
        _text(surface, _fonts()["small"], text, color, topleft=(x, y))
    return y + _ROW_H


def _panel_system(surface, state: GameState, ui: Ui, x, y, sys) -> int:
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
    y = _row(surface, x, y, f"Target: {config.player_name(d.owner_id)} · {d.ships}sh",
             config.player_color(d.owner_id))
    if ui.mode == CHOOSING:
        y = _row(surface, x, y, f"Sending: {ui.chosen}", config.COLOR_SELECT)
    return y


def _panel_rule(surface, ui: Ui, x, y, src) -> int:
    dest, keep = ui.auto_forward[src]
    _text(surface, _fonts()["normal"], "Auto-forward",
          config.player_color(ui.human_id), topleft=(x, y))
    y += 26
    y = _row(surface, x, y, f"-> System {dest}, keep {keep}", config.COLOR_TEXT)
    y = _row(surface, x, y, "press X to clear", config.COLOR_TEXT_DIM)
    return y


def _panel_legend(surface, x, y) -> int:
    _text(surface, _fonts()["normal"], "Star Conquest", config.COLOR_TEXT, topleft=(x, y))
    y += 28
    for line in (
        "Click a system for details.",
        "",
        "Select -> click neighbour",
        "Wheel: set ship count",
        "Click: send once",
        "Shift+click: forward rule",
        "X: clear forward rule",
        "Click queued lane: edit",
        "Enter / Space: end turn",
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
        (quit_r, "Quit (Y/⏎)", (120, 46, 52), (200, 96, 104)),
        (cancel_r, "Cancel (N/Esc)", (46, 92, 60), (96, 190, 120)),
    ):
        pygame.draw.rect(surface, fill, rect, border_radius=6)
        pygame.draw.rect(surface, edge, rect, 2, border_radius=6)
        _text(surface, _fonts()["normal"], label, config.COLOR_TEXT, center=rect.center)


def _draw_win_overlay(surface, state: GameState) -> None:
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 180))
    surface.blit(veil, (0, 0))
    if state.winner == 0:
        msg, color = "Mutual annihilation — draw", config.COLOR_TEXT
    else:
        msg = f"{config.player_name(state.winner)} wins!"
        color = config.player_color(state.winner)
    _text(surface, _fonts()["big"], msg, color, center=(w // 2, h // 2 - 16))
    _text(surface, _fonts()["normal"], "R: new map  ·  M: setup menu  ·  Esc: quit",
          config.COLOR_TEXT_DIM, center=(w // 2, h // 2 + 24))
