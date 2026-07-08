"""All drawing. Reads GameState and Ui; never mutates them, never imports the
engine or AI. Deliberately minimalist: circles for systems, lines for lanes,
triangles for fleets, numbers for ship counts.
"""

from __future__ import annotations

import math

import pygame

from . import config
from .geometry import lerp
from .model import GameState
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
    _draw_pending(surface, state, ui)
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
        # brighten the lanes touching the selected source system
        touches_selected = ui.selected is not None and ui.selected in (lane.a, lane.b)
        color = config.COLOR_LANE_HILITE if touches_selected else config.COLOR_LANE
        pygame.draw.line(surface, color, pa, pb, 2)
        # travel-time label at the midpoint
        mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
        _text(surface, _fonts()["small"], str(lane.travel_turns), config.COLOR_TEXT_DIM, center=mid)


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
    for o in ui.pending:
        pa = ui.view.to_screen(state.systems[o.source_id].pos)
        pb = ui.view.to_screen(state.systems[o.dest_id].pos)
        color = config.player_color(ui.human_id)
        pygame.draw.line(surface, color, pa, pb, 3)
        # arrowhead near the destination
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        length = math.hypot(dx, dy) or 1.0
        u = (dx / length, dy / length)
        head = (pb[0] - u[0] * 20, pb[1] - u[1] * 20)
        _draw_triangle(surface, head, u, color)
        _text(surface, _fonts()["small"], str(o.ships), config.COLOR_TEXT,
              center=((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2 - 10))


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

        # ship count (deployable = garrison minus queued commitments)
        shown = ui.available(state, sys.id) if sys.owner_id == ui.human_id else sys.ships
        _text(surface, _fonts()["normal"], str(shown), config.COLOR_TEXT, center=pos)


def _brighten(color, amount=60):
    return tuple(min(255, c + amount) for c in color)


# --------------------------------------------------------------------------- #
# HUD
# --------------------------------------------------------------------------- #
def _draw_hud(surface, state: GameState, ui: Ui) -> None:
    w, h = surface.get_size()

    # top bar
    pygame.draw.rect(surface, (18, 20, 30), (0, 0, w, config.HUD_TOP_H))
    _text(surface, _fonts()["normal"], f"Turn {state.turn}", config.COLOR_TEXT,
          midleft=(14, config.HUD_TOP_H // 2))
    x = 130
    for pid in sorted(state.players):
        p = state.players[pid]
        if p.is_neutral:
            continue
        systems = sum(1 for s in state.systems.values() if s.owner_id == pid)
        ships = sum(s.ships for s in state.systems.values() if s.owner_id == pid)
        ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
        label = f"{p.name}: {systems}sys {ships}sh"
        tag = "" if p.alive else " (out)"
        rect = _text(surface, _fonts()["normal"], label + tag, config.player_color(pid),
                     midleft=(x, config.HUD_TOP_H // 2))
        x = rect.right + 24

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


def _hint(ui: Ui) -> str:
    if ui.autoplay:
        return "Autoplay — AI is playing all seats. Press A to take control, Esc to quit."
    if ui.mode == SELECTED:
        return "Click a highlighted neighbour to send ships  ·  right-click/Esc to cancel"
    if ui.mode == CHOOSING:
        return "Wheel: adjust count  ·  click: confirm  ·  right-click/Esc: back"
    return "Click your system to select  ·  End Turn to resolve  ·  A: autoplay"


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
    _text(surface, _fonts()["normal"], "Press R for a new map  ·  Esc to quit",
          config.COLOR_TEXT_DIM, center=(w // 2, h // 2 + 24))
