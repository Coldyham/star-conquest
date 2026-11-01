"""All drawing. Reads GameState and Ui; never mutates them, never imports the
engine or AI. Deliberately minimalist: circles for systems, lines for lanes,
triangles for fleets, numbers for ship counts.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import pygame

from . import config, fog, matchnames, model, paths, starlore, stats, widgets
from .geometry import lerp
from .model import Fleet, GameState, lane_key
from .viewstate import CHOOSING, FOOTER_RECTS, ROUTING, SPECTATE_ROW, Ui

# The measured-layout kit lives in `widgets` now that the map creator draws with
# it too. Bound to module-level names here rather than called qualified, because
# this module's body resolves them as bare globals at call time — which is what
# lets a test swap one out (`render._text = spy`) and see the drawing code use it.
# `_FONTS` is the same dict object, so `render._FONTS.clear()` still works.
_FONTS = widgets._FONTS
_fonts = widgets.fonts
_text = widgets.text
_row_h = widgets.row_h
_btn_w = widgets.btn_w
_tap_size = widgets.tap_size
_btn = widgets.btn
_key_hint = widgets.key_hint
_wrap = widgets.wrap
_draw_slider = widgets.slider
_draw_step_button = widgets.step_button
_lane_style = widgets.lane_style
_pill_rect = widgets.pill_rect
_label_pill = widgets.label_pill
_brighten = widgets.brighten
_modal_buttons = widgets.modal_buttons
_draw_modal = widgets.draw_modal
confirm_labels = widgets.confirm_labels

_BTN_BLUE = widgets.BTN_BLUE
_BTN_ACTIVE = widgets.BTN_ACTIVE
_BTN_AMBER = widgets.BTN_AMBER
_BTN_VIOLET = widgets.BTN_VIOLET
_BTN_RED = widgets.BTN_RED
_BTN_DANGER = widgets.BTN_DANGER
_BTN_GREEN = widgets.BTN_GREEN
_BTN_TEAL = widgets.BTN_TEAL
_BTN_GOLD = widgets.BTN_GOLD

# Challenge verdict, on the win overlay: target beaten, dead-heated, missed.
_VERDICT_BEAT = (130, 200, 150)
_VERDICT_TIE = (190, 205, 225)
_VERDICT_MISS = (214, 172, 92)


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
    ui.send_all_rect = ui.send_half_rect = ui.cancel_rect = ui.done_rect = (0, 0, 0, 0)
    ui.hold_minus_rect = ui.hold_plus_rect = (0, 0, 0, 0)
    ui.even_split_rect = ui.delete_all_rect = (0, 0, 0, 0)
    ui.share_rows = []
    ui.clear_forward_rect = (0, 0, 0, 0)
    ui.clear_dangerous_rect = (0, 0, 0, 0)
    # route mode's confirm; _draw_hud re-records it once the plan has something in it
    ui.route_confirm_rect = (0, 0, 0, 0)
    # the stage hint and Next stage; the panel and the win overlay re-record them
    ui.stage_hint_rect = ui.next_stage_rect = (0, 0, 0, 0)
    # the star info card's button; the panel re-records it beside a selected system
    ui.lore_button_rect = (0, 0, 0, 0)
    # the history graph's; _draw_history_graph re-records them while it is open
    ui.graph_plot_rect = ui.graph_full_rect = ui.graph_all_rect = (0, 0, 0, 0)
    ui.graph_tab_rects = []
    ui.graph_trace_rects = []
    if ui.history and ui.history_graph and ui.graph_full:
        # the graphs fill everything between the bars: no map to draw under them
        _draw_hud(surface, state, ui)
        _draw_scrubber(surface, state, ui)
        _draw_history_graph(surface, state, ui)
        _draw_score_tooltip(surface, state, ui)
        return  # no map, so no camera cluster: _draw_hud zeroed its rects
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
    _draw_film_flashes(surface, state, ui)
    _draw_node_names(surface, state, ui)
    if not ui.history and ui.drag_active and ui.drag_src is not None:
        _draw_drag(surface, state, ui)
    if not ui.history:
        _draw_route_overlay(surface, state, ui)
    if not ui.history and not _popup_docked(ui):
        # the send popup draws last of the map layer so nodes/fleets never occlude
        # it (it must stay visible and clickable); it early-outs when closed
        _draw_send_popup(surface, state, ui)
    _draw_film_caption(surface, ui)
    surface.set_clip(None)
    sheet = config.portrait and _popup_docked(ui)
    if not ui.history and not sheet:
        _draw_zoom_controls(surface, ui)
    _draw_hud(surface, state, ui)
    if _popup_docked(ui):
        _draw_send_popup(surface, state, ui)   # over the panel it stands in for
    if sheet:
        # the portrait sheet covers the map's foot: the camera cluster rides on
        # it, as on history's docked graphs
        _draw_zoom_controls(surface, ui, bottom=ui.popup_rect[1] if ui.popup_rect[3] else None)
    if ui.history:
        # the scrubber owns the bottom bar; suppress the win overlay so a finished
        # game's final-turn snapshot doesn't veil the board being reviewed
        _draw_scrubber(surface, state, ui)
        graph = _draw_history_graph(surface, state, ui) if ui.history_graph else None
        # the camera cluster sits on the map, so above the docked graphs
        _draw_zoom_controls(surface, ui, bottom=graph.y if graph else None)
    elif state.winner is not None:
        _draw_win_overlay(surface, state, ui)
    elif ui.briefing_open and ui.in_stage:
        _draw_briefing(surface, ui)
    elif ui.lore_sid in state.systems:
        _draw_lore(surface, state, ui)
    elif ui.pbp_invite:
        _draw_invite_overlay(surface, ui)
    elif ui.awaiting_others(state):
        _draw_waiting_overlay(surface, state, ui)
    elif ui.in_pbp and ui.pbp_msg:
        _draw_pbp_notice(surface, ui)
    elif ui.spectating:
        _draw_spectator_notice(surface, state, ui)
    _draw_score_tooltip(surface, state, ui)


# --------------------------------------------------------------------------- #
# World layer
# --------------------------------------------------------------------------- #
def _fog_state(ui: Ui, sid: int) -> str:
    """Fog-of-war state of a system from the human's viewpoint: ``"visible"`` (full
    detail), ``"fogged"`` (grey "?" — currently scouted or remembered), or
    ``"hidden"`` (never seen, not drawn). With fog off, ``visible`` holds every
    system so this is always ``"visible"``.

    Asks `Ui.sees` rather than reading `visible`, so a turn playback also shows
    what was visible when that turn began — the map layer's one rule for this,
    shared with the fleets and the fight bursts."""
    if ui.sees(sid):
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


def _fleet_shown(ui: Ui, f: Fleet) -> bool:
    """Whether the map draws fleet ``f``: your own always, an enemy only where at
    least one end of its lane is in full view, so rival movements appear only as
    they near your space."""
    return f.owner_id == ui.human_id or ui.sees(f.source_id) or ui.sees(f.dest_id)


def _draw_lanes(surface, state: GameState, ui: Ui) -> None:
    # a fleet on the map shows the lane it travels, even where the far end was
    # never seen (sight at or past scout leaves no fogged ring to draw it to)
    carrying = {lane_key(f.source_id, f.dest_id) for f in state.fleets if _fleet_shown(ui, f)}
    source = ui.source(state)
    for key, lane in state.lanes.items():
        sa, sb = _fog_state(ui, lane.a), _fog_state(ui, lane.b)
        # a lane to a never-seen system is itself unknown — don't draw it
        if (sa == "hidden" or sb == "hidden") and key not in carrying:
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
        if (source is not None and source in (lane.a, lane.b)) or (
                ui.route_sel & {lane.a, lane.b}):
            color = config.COLOR_LANE_HILITE
            width = max(width, config.s(3))
        pygame.draw.line(surface, color, pa, pb, width)
        # travel time is detailed intel — only show it where an endpoint is in
        # full view; a lane between two fogged systems draws as a bare dim line
        if sa == "visible" or sb == "visible":
            mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
            _label_pill(surface, _fonts()["small"], str(turns), config.COLOR_TEXT_DIM, mid)


def _lane_unit(pa, pb) -> tuple[float, float, float]:
    """Unit vector from ``pa`` toward ``pb``, plus the lane's on-screen length."""
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length, length


def _rule_label_center(pa, pb, font, avoid: pygame.Rect | None = None, text: str = "") -> tuple[int, int]:
    """Where an auto-forward rule's share label sits on its lane: just past the
    *sending* system, offset clear of the line itself. A share is cut from that
    garrison, so it belongs at the end it constrains — and it leaves the lane's own
    travel-time pill (centred on the midpoint) readable, which is the other number
    you judge a rule by (see also ``_panel_lane``).

    Clamped to a fraction of the lane so a short one still puts the label on its own
    half, and offset perpendicular to the lane rather than straight down, so the
    label clears the source node on a steep lane as well as a flat one. On a lane
    too short for that clamp to clear the node, the perpendicular offset grows to
    make up the difference — the label leans out into space rather than onto the
    system it belongs to.

    ``avoid`` is the source's hold badge, if it has one: the label (``text``) tries
    the lane's other side, then further along it, before settling on top of it.
    """
    ux, uy, length = _lane_unit(pa, pb)
    px, py = (-uy, ux) if ux >= 0 else (uy, -ux)   # perpendicular, biased screen-down
    clear = config.node_clearance() + config.RULE_LABEL_GAP
    base = min(clear, length * config.RULE_LABEL_MAX_FRAC)
    lift = (_fonts()["small"].get_height() + font.get_height()) // 2 + config.s(8)

    def at(along: float, side: int) -> tuple[int, int]:
        off = max(lift, math.sqrt(max(0.0, clear * clear - along * along)))
        return (int(pa[0] + ux * along + side * px * off), int(pa[1] + uy * along + side * py * off))

    first = at(base, 1)
    if avoid is None:
        return first
    for along in (base, base * 1.6, base * 2.2):
        for side in (1, -1):
            spot = at(min(along, length / 2), side)
            if not _pill_rect(font, text, spot).colliderect(avoid):
                return spot
    return first


def _travel(ui: Ui) -> float:
    """How far through the turn's step to draw the fleets.

    1.0 unless a film is mid-move, which is what makes this a no-op for the live
    board and for a history snapshot: both already carry the schedule they mean.
    """
    if ui.film is None:
        return 1.0
    return ui.film.travel(ui.film_ms)


def _fleet_at(state: GameState, ui: Ui, f: Fleet,
              travel: float) -> tuple[float, float, float, float]:
    """Where a fleet is drawn this frame and which way it points, in screen
    space: ``(x, y, ux, uy)``, its lane track already applied.

    Split out of the drawing so the approach can be checked on its own — an
    arriving fleet has to close on its destination and stop, never reach the
    centre and be pushed back off it.
    """
    a = state.systems[f.source_id].pos
    b = state.systems[f.dest_id].pos
    x, y = ui.view.to_screen(lerp(a, b, f.progress_at(travel)))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    # A fleet that reaches its destination sits on the node and would cover its
    # garrison count, so its approach stops at the rim. Clamped over the last few
    # pixels rather than applied once it has landed: the triangle closes on the
    # centre for the whole glide, so subtracting the gap only at the end jumps it
    # backwards by a whole radius on the move beat's last frame. Only a film ever
    # draws an arrived fleet — the engine takes one off the board the same turn.
    if f.turns_remaining <= 0:
        bx, by = ui.view.to_screen(b)
        gap = (config.node_radius(state.systems[f.dest_id].production)
               + config.FILM_ARRIVAL_GAP)
        if math.hypot(bx - x, by - y) < gap:
            x, y = bx - ux * gap, by - uy * gap
    # Hold each fleet off the lane's centre line by the track it was given at
    # launch (`model.free_lane_slot`) — read off the fleet rather than ranked
    # here, so a lane-mate launching or arriving cannot shift it.
    spread = config.FLEET_SIZE + config.s(3)
    return x - uy * f.lane_slot * spread, y + ux * f.lane_slot * spread, ux, uy


def _draw_fleets(surface, state: GameState, ui: Ui) -> None:
    travel = _travel(ui)
    for f in state.fleets:
        if not _fleet_shown(ui, f):
            continue
        fx, fy, ux, uy = _fleet_at(state, ui, f, travel)
        x, y = int(fx), int(fy)
        _draw_triangle(surface, (x, y), (ux, uy), config.player_color(f.owner_id))
        # The count sits clear of the triangle; both offsets scale, so the label
        # never lands on the glyph on a big display.
        spread = config.FLEET_SIZE + config.s(3)
        _text(surface, _fonts()["small"], str(f.ships), config.COLOR_TEXT,
              center=(x - int(uy * spread), y + int(ux * spread)))


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

    The run spans the whole gap between the two systems: `config.RULE_CHEVRON_GAP` is
    a target pitch, and the spacing actually used is the span divided by however many
    chevrons that asks for (at least two), so no remainder is left idling short of the
    destination. At rest both ends carry a chevron; `phase` (0..1) walks the run one
    spacing forward over a cycle, retiring the leading chevron as it reaches the
    destination and bringing it back at the source as the cycle turns over.
    """
    start = src_r + config.ARROW_GAP + config.RULE_CHEVRON_SIZE
    span = length - (dest_r + config.ARROW_GAP) - start
    if span <= config.RULE_CHEVRON_SIZE:
        return [length / 2]   # nose-to-nose systems: no room for a run, so mark the lane
    count = max(2, round(span / max(1, config.RULE_CHEVRON_GAP)))
    step = span / count
    return [start + (i + phase) * step for i in range(count + 1)
            if (i + phase) <= count]


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
    """Standing auto-forward rules as a persistent chevron flow (human colour), one
    per lane labelled with its share where that is less than everything, and a
    "hold N" badge under a system that keeps ships back — under the system, since
    the hold is the system's, whichever lanes it splits down.

    The system being edited is drawn in the select colour, brighter+thicker, and
    only the lane the popup is on crawls — same convention as `_draw_pending`'s
    selected queued order.

    A lane pointing at a system we *don't* hold is tinted as dangerous. Nothing
    stops one (`rule_is_live` doesn't care who owns the far end, and aiming a rule
    at an enemy on purpose is a real move), but it means that share charges into
    enemy guns every turn — and a route chain that loses a middle system leaves its
    upstream neighbour doing exactly that without anyone choosing to.
    """
    editing = ui.selected if ui.mode == CHOOSING and ui.forward_armed else ui.sel_forward
    small = _fonts()["small"]
    for src, rule in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id:
            continue
        selected = src == editing
        pa = ui.view.to_screen(s.pos)
        badge = _hold_badge(small, state, ui, src)
        avoid = _pill_rect(small, *badge) if badge is not None else None
        for dest, pct in rule.shares.items():
            if dest not in state.systems:
                continue
            focused = selected and (dest == ui.dest or ui.mode != CHOOSING)
            if selected:
                color = config.COLOR_SELECT
            elif ui.lane_is_hostile(state, dest):
                color = _BTN_DANGER[1]
            else:
                color = config.player_color(ui.human_id)
            pb = ui.view.to_screen(state.systems[dest].pos)
            _draw_rule_flow(surface, pa, pb, config.node_radius(s.production),
                            config.node_radius(state.systems[dest].production),
                            color, config.s(3 if focused else 2), animate=focused)
            if pct < 100:  # all of it is the default; the chevrons already say so
                _label_pill(surface, small, f"{pct}%", config.COLOR_TEXT if selected else config.COLOR_TEXT_DIM,
                            _rule_label_center(pa, pb, small, avoid, f"{pct}%"))
        if badge is not None:
            _label_pill(surface, small, badge[0], config.COLOR_TEXT if selected else _forward_accent(), badge[1])


def _hold_badge(font, state: GameState, ui: Ui, sid: int) -> tuple[str, tuple[int, int]] | None:
    """Text and centre of a holding system's "hold N" badge, just under its disc —
    where its star name would go, so `_draw_node_names` moves the name down a slot.
    None for a system holding nothing (or not ours)."""
    rule = ui.auto_forward.get(sid)
    sys = state.systems.get(sid)
    if rule is None or not rule.hold or sys is None or sys.owner_id != ui.human_id:
        return None
    text = f"hold {rule.hold}"
    pos = ui.view.to_screen(sys.pos)
    half = _pill_rect(font, text, (0, 0)).h // 2
    return text, (pos[0], pos[1] + config.node_radius(sys.production) + config.NODE_LABEL_GAP + half)


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
    """Route mode's proposal: the selection, the rules it would lay, and what
    confirming would cost.

    Both sub-modes share the accent: they are never on screen together, so rally
    needs no second uncommitted hue. What differs is the rings — a chain pick is a
    *source* and gets one ring, a rally pick is a *sink* and gets the same double
    ring a chain destination does.

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
        if old is None or src not in state.systems:
            continue
        pa = ui.view.to_screen(state.systems[src].pos)
        for dest in old.shares:
            if dest not in state.systems:
                continue
            pb = ui.view.to_screen(state.systems[dest].pos)
            _draw_rule_flow(surface, pa, pb, config.node_radius(state.systems[src].production),
                            config.node_radius(state.systems[dest].production),
                            _BTN_DANGER[1], config.s(2), animate=False)

    # 2. the planned chain
    small = _fonts()["small"]
    for src, rule in ui.route_plan.items():
        if src not in state.systems:
            continue
        pa = ui.view.to_screen(state.systems[src].pos)
        for dest, pct in rule.shares.items():
            if dest not in state.systems:
                continue
            pb = ui.view.to_screen(state.systems[dest].pos)
            _draw_rule_flow(surface, pa, pb, config.node_radius(state.systems[src].production),
                            config.node_radius(state.systems[dest].production),
                            accent, config.s(3), animate=True)
            if pct < 100:  # a tie split evenly — say so, as a committed rule would
                _label_pill(surface, small, f"{pct}%", config.COLOR_TEXT, _rule_label_center(pa, pb, small))

    # 3. one ring per source pick, plus one on anything that can't be served. In
    #    chain mode the unservable are always picks, so that second loop is empty
    #    and nothing about chain drawing changes; in rally mode it is what marks a
    #    pocket no rally point can reach, which the player never picked.
    sources = set() if ui.route_rally else ui.route_sel
    for sid in sources | ui.route_unroutable:
        sys = state.systems.get(sid)
        if sys is None:
            continue
        sp = ui.view.to_screen(sys.pos)
        r = config.node_radius(sys.production) + config.NODE_RING_PAD
        bad = sid in ui.route_unroutable
        pygame.draw.circle(surface, _BTN_DANGER[1] if bad else accent, sp, r, max(2, config.s(3)))

    # 4. the double ring on every sink — the destination in chain mode, each rally
    #    point in rally mode (labels and the drag box go on top of the nodes, in
    #    _draw_route_overlay)
    dest = ui.route_dest
    sinks = ui.route_sel if ui.route_rally else ({dest} if dest is not None else set())
    for sid in sinks:
        sys = state.systems.get(sid)
        if sys is None:
            continue
        dp = ui.view.to_screen(sys.pos)
        dr = config.node_radius(sys.production)
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
    vx, vy, vw, vh = config.play_rect()
    x_lo, x_hi = vx, vx + vw - w
    y_lo, y_hi = vy + 2, vy + vh - h
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
    Half, All) and a lane of the source's standing rule.

    The Forward tab has two shapes. With one lane it edits that lane's share the way
    the Send tab edits a count (slider, −/+, 50%/100%), plus the system's hold. Once
    the system forwards down two or more lanes it becomes the system's split panel:
    one row per lane with its own −/+, what stays home, the split as a bar, and Even
    split / Delete all. The row for `ui.dest` is the lane the bottom button, the
    wheel and the map highlight are about; tapping another row moves it.

    The button at the bottom discards whichever is live — labelled Cancel while
    composing, Delete when the popup was opened on something that already existed.
    Records every control's hit-rect on ``ui`` for input (store-rect-then-test)."""
    if ui.mode != CHOOSING or ui.selected not in state.systems or ui.dest not in state.systems:
        return
    pa = ui.view.to_screen(state.systems[ui.selected].pos)
    pb = ui.view.to_screen(state.systems[ui.dest].pos)
    mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)

    dest = state.systems[ui.dest]
    src = state.systems[ui.selected]
    garrison = src.ships
    rule = ui.edited_rule()
    split = rule is not None and len(rule.shares) > 1
    font = _fonts()["small"]
    pad, gap = config.SEND_POPUP_PAD, config.SEND_POPUP_GAP
    # rows are tall enough to hold their own label, and to be tapped on a phone
    bh = _tap_size(max(config.SEND_POPUP_BTN_H, font.get_height() + config.ROW_GAP))
    w = config.SPLIT_POPUP_W if split else config.SEND_POPUP_W
    # Docked (a compact screen): in the side panel's place, at its width, or in
    # portrait a sheet across the foot of the map rising from the panel's floor.
    docked = _popup_docked(ui)
    dock = pygame.Rect(config.side_panel_rect())
    if docked:
        w = dock.w
    # Send: tabs, title, effect, stepper, slider, presets, cancel. One forwarding
    # lane adds the hold row; a split swaps stepper and slider for a row per lane,
    # what stays home and the bar. Docked, a lane's popup covers the side panel's
    # lane readout, so it carries the lane's length under its title.
    lane_row = docked and not split
    single_rows = 8 + lane_row
    sh = surface.get_height()
    # On a short screen at touch scale the stack can outgrow the band it is placed
    # in, and since the clamp below pins an oversized panel to the top, what falls
    # off the bottom (and out of draw's clip) is the destructive Delete row — still
    # live, because input hit-tests the recorded rect, not what survived clipping.
    # So give up the tap floor before giving up a row, down to what the labels need.
    # The budget is the placement band itself, not the viewport, so the two agree.
    vx, vy, vw, vh = config.play_rect()
    y_lo = vy + 2
    budget = vy + vh - y_lo
    if docked:
        # landscape: the panel's column; portrait: a sheet over the controls
        # under the map (panel, strip, End Turn), which it closes back onto
        y_lo = dock.y if not config.portrait else vy + config.s(48)
        budget = (dock.bottom if not config.portrait else sh) - y_lo
    bh_min = font.get_height() + config.ROW_GAP

    def fits(n_rows: int) -> bool:
        return pad * 2 + bh_min * n_rows + gap * (n_rows - 1) <= budget

    if docked and not config.portrait and not fits(single_rows):
        # a panel too short even for label-high rows: run on down over End Turn,
        # which input tests after the popup's own controls
        budget = sh - y_lo

    layout = _SplitLayout([], True, True)
    if rule is None:
        rows = 7 + lane_row
    elif split:
        # A split that still doesn't fit sheds what the numbers already say — the
        # bar, then the "stays" row — and then whole lanes, keeping a window round
        # the one being edited; the rest are a tap on their lane away.
        lanes = list(rule.shares)
        shown = len(lanes)
        bar = stays = True

        def split_rows() -> int:
            return 6 + shown + bar + stays + (shown < len(lanes))

        while not fits(split_rows()):
            if bar:
                bar = False
            elif stays:
                stays = False
            elif shown > 1:
                shown -= 1
            else:
                break
        at = lanes.index(ui.dest) if ui.dest in lanes else 0
        first = max(0, min(at - shown // 2, len(lanes) - shown))
        layout = _SplitLayout(lanes[first:first + shown], bar, stays)
        rows = split_rows()
    else:
        rows = single_rows
    if pad * 2 + bh * rows + gap * (rows - 1) > budget:
        bh = max(bh_min, (budget - pad * 2 - gap * (rows - 1)) // rows)
    h = pad * 2 + bh * rows + gap * (rows - 1)

    # placement: honour a user-dragged position (clamped to stay reachable),
    # else auto-anchor to whichever side of the lane covers the fewest nodes. The
    # anchor is chosen for the taller of the two single-lane shapes, so flipping
    # between Send and Forward doesn't make the panel jump to another side.
    x_lo, x_hi = vx, vx + vw - w
    y_hi = vy + vh - h
    if docked:
        # nothing to drag it off: it covers the panel, not the map
        x, y = dock.x, (sh - h if config.portrait else dock.y)
    elif ui.popup_pos is not None:
        x = _clamp(ui.popup_pos[0], x_lo, x_hi)
        y = _clamp(ui.popup_pos[1], y_lo, y_hi)
    else:
        h_anchor = max(h, pad * 2 + bh * single_rows + gap * (single_rows - 1))
        x, y = _popup_anchor(surface, state, ui, mid, w, h_anchor)
        y = _clamp(y, y_lo, y_hi)
    ui.popup_rect = (x, y, w, h)

    panel = pygame.Rect(x, y, w, h)
    accent = _forward_accent() if ui.forward_armed else config.COLOR_SELECT
    if docked:
        # square-cornered: it is the panel for now, not a card over the map
        pygame.draw.rect(surface, (20, 24, 36), dock if not config.portrait else panel)
        pygame.draw.rect(surface, accent, dock if not config.portrait else panel, config.s(2))
    else:
        pygame.draw.rect(surface, (20, 24, 36), panel, border_radius=config.s(8))
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

    # title: what the panel is about on the left, a garrison right-aligned — the
    # destination's for a lane, the source's own for a whole split system
    if split:
        ships_lbl = f"{garrison}sh"
        room = iw - font.size(ships_lbl)[0] - gap
        title = f"{_fit_name(font, src, room - font.size(' splits')[0])} splits"
        ships_col = config.player_color(src.owner_id)
    else:
        # a destination out of full view keeps its garrison and owner hidden,
        # the same as the map's "?" for it
        if ui.sees(dest.id):
            ships_lbl = f"{dest.ships}sh"
            ships_col = config.player_color(dest.owner_id)
        else:
            ships_lbl = "?sh"
            ships_col = config.COLOR_FOG
        title = _fit_route(font, src, dest, iw - font.size(ships_lbl)[0] - gap)
    _text(surface, font, title, accent, midleft=(inner, cy + bh // 2))
    _text(surface, font, ships_lbl, ships_col, midleft=(inner + iw - font.size(ships_lbl)[0], cy + bh // 2))
    cy += bh + gap

    if lane_row:
        lane = state.lanes.get(lane_key(src.id, dest.id))
        if lane is not None:
            trip = f"{lane.length_ly} ly  ·  {state.travel_turns(src.id, dest.id)} turns"
            _text(surface, font, trip, config.COLOR_TEXT_DIM, center=(inner + iw // 2, cy + bh // 2))
        cy += bh + gap

    s = _tap_size(config.STEPPER_SIZE)
    if rule is not None:
        # hold row: ships the system keeps back before any lane takes its share. It
        # belongs to the system, so on a split it covers every row below it.
        label = "holds"
        row = pygame.Rect(inner, cy, iw, bh)
        pygame.draw.rect(surface, (12, 14, 22), row, border_radius=config.s(5))
        ui.hold_minus_rect, ui.hold_plus_rect = _draw_mini_stepper(
            surface, font, row, s, label, str(rule.hold), config.COLOR_TEXT_DIM,
            config.COLOR_TEXT if rule.hold else config.COLOR_TEXT_DIM, accent)
        cy += bh + gap

    this_turn = ui.forward_this_turn(state, ui.selected) if rule is not None else {}
    stay = max(0, ui.available(state, ui.selected) - sum(this_turn.values()))

    if split and rule is not None:
        cy = _draw_split_rows(surface, font, ui, state, rule, layout, inner, cy, iw, bh, gap, s, accent)
        effect = f"{sum(this_turn.values())} go  ·  {stay} stay"
        _text(surface, font, effect, config.COLOR_TEXT_DIM, center=(inner + iw // 2, cy + bh // 2))
        cy += bh + gap
        left = pygame.Rect(inner, cy, tw, bh)
        right = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
        _draw_popup_button(surface, left, "Even split", rule.shares == model.shares_even(rule.shares), accent=accent)
        _draw_popup_button(surface, right, "Delete all", danger=True)
        ui.even_split_rect = (left.x, left.y, left.w, left.h)
        ui.delete_all_rect = (right.x, right.y, right.w, right.h)
        cy += bh + gap
    else:
        # effect caption: what this actually does this turn, in plain words (dim)
        if ui.forward_armed:
            effect = f"{this_turn.get(ui.dest, 0)} go  ·  {stay} stay"
        else:
            effect = f"send {ui.chosen}  ·  {max(0, garrison - ui.chosen)} home"
        _text(surface, font, effect, config.COLOR_TEXT_DIM, center=(inner + iw // 2, cy + bh // 2))
        cy += bh + gap

        # stepper row: [−]  value  [+]  — the send count, or (Forward) the lane's share
        minus = pygame.Rect(inner, cy + (bh - s) // 2, s, s)
        plus = pygame.Rect(inner + iw - s, cy + (bh - s) // 2, s, s)
        _draw_step_button(surface, minus, "-", accent)
        _draw_step_button(surface, plus, "+", accent)
        label = f"send {ui.edited_share()}%" if ui.forward_armed else str(ui.chosen)
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

        # preset row: Half/All (Send) or 50%/100% (Forward) — left, right map to
        # send_half_rect / send_all_rect on both tabs (input reads the mode). The
        # preset matching the current value lights up in the mode accent.
        left = pygame.Rect(inner, cy, tw, bh)
        right = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
        if ui.forward_armed:
            share = ui.edited_share()
            _draw_popup_button(surface, left, "50%", share == 50, accent=accent)
            _draw_popup_button(surface, right, "100%", share == 100, accent=accent)
        else:
            cap = ui._active_cap(state)
            _draw_popup_button(surface, left, "Half", ui.chosen == max(1, cap // 2), accent=accent)
            _draw_popup_button(surface, right, "All", ui.chosen == cap, accent=accent)
        ui.send_half_rect = (left.x, left.y, left.w, left.h)
        ui.send_all_rect = (right.x, right.y, right.w, right.h)
        cy += bh + gap

    # Discard row: drop the active send, or the forwarding lane being edited. It
    # reads "Cancel" only while composing — reopened on something that was already
    # on the board, the same press is a deletion, and should say so.
    cancel = pygame.Rect(inner, cy, iw, bh)
    if ui.editing_existing:
        discard = ("Delete lane" if split else "Delete rule") if ui.forward_armed else "Delete order"
    else:
        discard = "Cancel"
    if docked:
        # Docked, the popup is a panel rather than a card on the map, and closing
        # it by tapping the map isn't something a panel says; Done says it.
        cancel.w = tw
        done = pygame.Rect(inner + tw + gap, cy, iw - tw - gap, bh)
        _draw_popup_button(surface, done, "Done", True, accent=_BTN_GREEN[1])
        ui.done_rect = (done.x, done.y, done.w, done.h)
    _draw_popup_button(surface, cancel, discard, danger=True)
    ui.cancel_rect = (cancel.x, cancel.y, cancel.w, cancel.h)


def _draw_mini_stepper(surface, font, row: pygame.Rect, s: int, label: str, value: str,
                       label_col, value_col, accent) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """``label`` on the left of ``row`` and ``[−] value [+]`` on its right, the
    value column wide enough for "100%" so a column of these lines up. Returns the
    −/+ hit-rects."""
    vw = font.size("100%")[0]
    inset = config.s(4)
    plus = pygame.Rect(row.right - inset - s, row.y + (row.h - s) // 2, s, s)
    minus = pygame.Rect(plus.x - 2 * inset - vw - s, plus.y, s, s)
    _text(surface, font, label, label_col, midleft=(row.x + config.s(6), row.centery))
    _draw_step_button(surface, minus, "-", accent)
    _draw_step_button(surface, plus, "+", accent)
    _text(surface, font, value, value_col, center=((minus.right + plus.left) // 2, row.centery))
    return (minus.x, minus.y, minus.w, minus.h), (plus.x, plus.y, plus.w, plus.h)


class _SplitLayout(NamedTuple):
    """What a split panel has room for: the lanes it lists (a window round the one
    being edited, when not all fit), and whether the bar and "stays" rows made it."""
    lanes: list[int]
    bar: bool
    stays: bool


def _draw_split_rows(surface, font, ui: Ui, state: GameState, rule, layout: _SplitLayout,
                     inner: int, cy: int, iw: int, bh: int, gap: int, s: int, accent) -> int:
    """A split system's lanes, one row each with its own −/+, then what stays home
    and the split as a bar in the rows' own colours. The row for ``ui.dest`` is
    outlined: it is the lane the bottom button, the wheel and the map highlight act
    on. Lanes the panel had no room for are counted in a row of their own. Records
    ``ui.share_rows``; returns the y below the last row."""
    colour = dict(zip(rule.shares, _lane_swatches(len(rule.shares))))
    sw = config.s(8)
    name_room = iw - config.s(6) - sw - config.s(6) - (2 * s + 4 * config.s(4) + font.size("100%")[0])
    ui.share_rows = []
    names = _fit_names(font, [state.systems[d] for d in layout.lanes], name_room)
    for dest, name in zip(layout.lanes, names):
        pct, col = rule.shares[dest], colour[dest]
        row = pygame.Rect(inner, cy, iw, bh)
        if dest == ui.dest:
            pygame.draw.rect(surface, tuple(c * 2 // 10 for c in accent), row, border_radius=config.s(5))
            pygame.draw.rect(surface, accent, row, config.s(1), border_radius=config.s(5))
        swatch = pygame.Rect(row.x + config.s(6), row.centery - sw // 2, sw, sw)
        pygame.draw.rect(surface, col, swatch, border_radius=config.s(2))
        name_col = _BTN_DANGER[1] if ui.lane_is_hostile(state, dest) else config.COLOR_TEXT
        label_row = pygame.Rect(swatch.right, row.y, row.w - (swatch.right - row.x), row.h)
        minus, plus = _draw_mini_stepper(surface, font, label_row, s, name, f"{pct}%",
                                         name_col, config.COLOR_TEXT, accent)
        ui.share_rows.append((dest, (row.x, row.y, row.w, row.h), minus, plus))
        cy += bh + gap

    hidden = len(rule.shares) - len(layout.lanes)
    if hidden:
        _text(surface, font, f"+{hidden} more · tap a lane", config.COLOR_TEXT_DIM,
              center=(inner + iw // 2, cy + bh // 2))
        cy += bh + gap

    if layout.stays:
        home = max(0, 100 - rule.total())
        row = pygame.Rect(inner, cy, iw, bh)
        swatch = pygame.Rect(row.x + config.s(6), row.centery - sw // 2, sw, sw)
        pygame.draw.rect(surface, _SPLIT_HOME, swatch, border_radius=config.s(2))
        _text(surface, font, "stays", config.COLOR_TEXT_DIM, midleft=(swatch.right + config.s(6), row.centery))
        vw = font.size("100%")[0]
        _text(surface, font, f"{home}%", config.COLOR_TEXT_DIM,
              center=(row.right - config.s(4) - s - config.s(4) - vw // 2, row.centery))
        cy += bh + gap

    if layout.bar:
        bar = pygame.Rect(inner, cy + bh // 4, iw, bh // 2)
        pygame.draw.rect(surface, _SPLIT_HOME, bar, border_radius=config.s(4))
        bx = bar.x
        for dest, pct in rule.shares.items():
            seg = bar.w * pct // 100
            if seg > 0:
                pygame.draw.rect(surface, colour[dest], pygame.Rect(bx, bar.y, seg, bar.h))
            bx += seg
        cy += bh + gap
    return cy


_SPLIT_HOME = (42, 48, 68)  # what stays home, in the split panel's rows and bar


def _lane_swatches(n: int) -> list[tuple[int, int, int]]:
    """One colour per lane of a split, from a light to a dark teal: told apart by
    lightness, not just hue, so they survive any colour vision."""
    light, dark = (190, 240, 234), (40, 112, 106)
    if n <= 1:
        return [_forward_accent()]

    def mix(a: int, b: int, t: float) -> int:
        return round(a + (b - a) * t)

    return [(mix(light[0], dark[0], i / (n - 1)), mix(light[1], dark[1], i / (n - 1)),
             mix(light[2], dark[2], i / (n - 1))) for i in range(n)]


def _fit_name(font, sys, width: int) -> str:
    """A system's name for a slot ``width`` wide: name and id ("Vega (7)") where
    that fits, the name alone where only it does, else the bare id."""
    for text in (sys.label, sys.short):
        if font.size(text)[0] <= width:
            return text
    return str(sys.id)


def _fit_names(font, systems, width: int) -> list[str]:
    """``_fit_name`` for a column: the richest form every row fits, so a list never
    mixes "Vega (7)" with a bare name."""
    for form in (lambda s: s.label, lambda s: s.short):
        names = [form(s) for s in systems]
        if all(font.size(n)[0] <= width for n in names):
            return names
    return [str(s.id) for s in systems]


def _fit_route(font, a, b, width: int) -> str:
    """``a -> b`` for a slot ``width`` wide, by the same steps as ``_fit_name``
    taken by both ends together, so a title never mixes the two forms."""
    for text in (f"{a.label} -> {b.label}", f"{a.short} -> {b.short}"):
        if font.size(text)[0] <= width:
            return text
    return f"{a.id} -> {b.id}"


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v)) if hi >= lo else lo


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


def _faded(color: tuple[int, int, int], fade: float) -> tuple[int, int, int]:
    """``color`` blended toward the background by ``fade`` (0 untouched, 1 gone).

    A dissolve rather than true alpha: the main surface has no per-pixel alpha, and
    a pill's own background is already `config.COLOR_BG`, so fading its text toward
    the same colour reads as it sinking into its own backing rather than a colour
    shift. Used for a burst's stroke too, where it reads as sinking into the map.
    """
    bg = config.COLOR_BG
    return tuple(round(c + (b - c) * fade) for c, b in zip(color, bg))


def _draw_burst(surface, center, color, phase: float) -> None:
    """A fight, marked where it happened.

    Neither of this file's two established glyphs: a filled wedge means real ships
    and an open chevron means an intention, so a fight is an expanding open ring
    with radial spokes — it cannot be mistaken for either, nor for a node.
    ``phase`` runs 0..1 over `config.FILM_FLASH_MS`, so it thins as it grows.
    """
    reach = config.FILM_BURST_R * (0.35 + 0.65 * phase)
    width = max(1, round(config.FILM_BURST_W * (1.0 - phase * 0.5)))
    cx, cy = center
    pygame.draw.circle(surface, color, (int(cx), int(cy)), int(reach), width)
    for i in range(config.FILM_BURST_SPOKES):
        angle = math.tau * i / config.FILM_BURST_SPOKES + phase
        ux, uy = math.cos(angle), math.sin(angle)
        pygame.draw.line(
            surface, color,
            (cx + ux * reach, cy + uy * reach),
            (cx + ux * (reach + config.FILM_BURST_R * 0.4),
             cy + uy * (reach + config.FILM_BURST_R * 0.4)), width)


def _mark_center(center) -> tuple[int, int]:
    """Where a playback writes a number about a place: a fixed step above it.

    Fixed rather than measured off the burst's current reach, which grows over the
    flash — a label riding that outward would read as a second moving thing.
    """
    return (int(center[0]),
            int(center[1]) - config.FILM_BURST_R - config.FILM_LOSS_GAP)


def _mark_slot(font, center) -> pygame.Rect:
    """The space over a system a playback writes numbers into: a mark's own row
    and the row a second number stacks into above it (`_film_labels`).

    Sized off a representative label rather than a real one, because the star-name
    pass reserves this whether or not anything is showing there right now. A name
    allowed into the gap between fights would be pushed off the map the moment one
    fired, which reads as the name flickering rather than as the number arriving.
    """
    base = _pill_rect(font, "+00", _mark_center(center))
    return base.union(base.move(0, -_row_h("small")))


class _Mark(NamedTuple):
    """One fight a playback is currently showing, placed on screen."""

    center: tuple[float, float]
    color: tuple[int, int, int]
    phase: float
    fade: float                 # 0 fresh, 1 dissolved into the background
    losses: tuple[tuple[int, int], ...]   # (owner, ships lost), in resolution order
    node_id: int | None      # the system it happened at, if it was not in open space


def _mark_phase(age_ms: float) -> float:
    """A burst's own pop-in geometry: rings settle and spokes retract over
    `config.FILM_FLASH_MS`, counted from when the mark fired."""
    return min(1.0, max(0.0, age_ms / max(1, config.FILM_FLASH_MS)))


def _mark_fade(age_ms: float) -> float:
    """How far a mark has dissolved toward invisible, 0 fresh, 1 gone.

    Fully visible for `config.FILM_FLASH_MS` (matching how long a burst took to
    settle before this cut ever faded anything), then fades over the next
    `config.FILM_FADE_MS` — both counted from when the mark fired and neither
    tied to any film's own length, which is what lets one keep dissolving on top
    of whatever the next turn's glide is doing.
    """
    if age_ms <= config.FILM_FLASH_MS:
        return 0.0
    return min(1.0, (age_ms - config.FILM_FLASH_MS) / max(1, config.FILM_FADE_MS))


def _flash_marks(state: GameState, ui: Ui):
    """Every fading fight, as `_Mark`s.

    The one place `Ui.shown_marks` (a film's fading fights, or history's held
    ones) is turned into places on screen, so the
    bursts, the labels over them and the star-name pass all agree about where
    they are. A fight is never dropped early, only faded toward invisible
    (`fade`) — pruning is `Ui.age_fading_marks`' job, not this one's, and it runs
    whether or not a playback is currently active.
    """
    for f in ui.shown_marks()[0]:
        phase, fade = _mark_phase(f.age_ms), _mark_fade(f.age_ms)
        if f.node_id is None:
            a = state.systems[f.low_id].pos
            b = state.systems[f.high_id].pos
            center = ui.view.to_screen(lerp(a, b, f.at))
            yield _Mark(center, f.burst_color, phase, fade, f.losses, None)
        else:
            center = ui.view.to_screen(state.systems[f.node_id].pos)
            yield _Mark(center, f.burst_color, phase, fade, f.losses, f.node_id)


def _film_labels(state: GameState, ui: Ui) -> list[tuple[tuple[int, int], str, tuple[int, int, int]]]:
    """Every number a playback writes over the map this frame, already placed:
    ``(centre, text, colour)``.

    Two kinds share that spot above a system. A fight writes each side's own
    losses, one ``−N`` per side in its owner's colour — never summed, which would
    bury the victor's cost (the figure the square law makes hard to guess) in the
    beaten side's — stacked in the order the fold beat them, the victor's last.
    The beaten side's figure matters most once its ships have gone from the board,
    as in history, where only the result is on screen. And a hull finished this turn is
    ``+N`` in its owner's colour, which is what makes production visible at all
    without ever stopping the board to say it: `config.FILM_PRODUCE_MS` is a lead
    borrowed from the glide (`turnfilm.film`), not a dwell. Each fades toward the
    background on its own fixed clock (`_mark_fade`) rather than being cut off, or
    tied to how long any particular turn's film runs.

    They can still collide, on purpose: production now runs *before* combat, so a
    hull finished this turn defends the system it was built at and its ``+N``
    credits whoever held the system going in — the defender, not whoever ends up
    with it. When the extra ship still isn't enough the system falls right after,
    and the two marks land moments apart rather than the same instant, but neither
    expires early, so the two reliably stack whenever both are still up, in the
    order they resolved, read top down: the gain above the cost. One list, because the star-name pass has to treat these as
    occupied space (the same rule that already keeps names off lane times and a
    rule's share) and must not have to re-derive where they went.
    """
    labels: list[tuple[tuple[int, int], str, tuple[int, int, int]]] = []
    # A system's numbers stack in resolution order, read top down: production
    # comes before combat, so a gain sits above a cost, and the last to resolve
    # is on the mark's own row.
    stacks: dict[int | tuple[float, float], list[tuple[str, tuple[int, int, int]]]] = {}
    for h in ui.shown_marks()[1]:
        color = _faded(config.player_color(h.owner_id), _mark_fade(h.age_ms))
        stacks.setdefault(h.node_id, []).append((f"+{h.hulls}", color))
    for mark in _flash_marks(state, ui):
        # A clash in open space has no system to share, so it stacks on its own.
        stack = stacks.setdefault(mark.center if mark.node_id is None else mark.node_id, [])
        for owner, lost in mark.losses:
            stack.append((f"−{lost}", _faded(config.player_color(owner), mark.fade)))
    for key, items in stacks.items():
        at = ui.view.to_screen(state.systems[key].pos) if isinstance(key, int) else key
        x, y = _mark_center(at)
        for above, (text, color) in enumerate(reversed(items)):
            labels.append(((x, y - above * _row_h("small")), text, color))
    return labels


def _draw_film_flashes(surface, state: GameState, ui: Ui) -> None:
    """Mark every still-fading fight, and write the numbers that go with them
    (plus any hull finished — see `_film_labels`). Independent of whether a film
    is currently playing: a mark keeps showing on top of live/history play alike
    until `Ui.age_fading_marks` prunes it."""
    for mark in _flash_marks(state, ui):
        _draw_burst(surface, mark.center, _faded(mark.color, mark.fade), mark.phase)
    for center, text, color in _film_labels(state, ui):
        _label_pill(surface, _fonts()["small"], text, color, center)


def _draw_film_caption(surface, ui: Ui) -> None:
    """Name the phase being shown. A gliding fleet does not say which part of the
    turn it belongs to, and that is the whole point of the animation.

    On the map layer rather than the top bar: a caption there would shift the
    measured scoreboard in and out as its width changed.
    """
    if ui.film is None:
        return
    label = ui.film.label(ui.film_ms)
    if not label:
        return
    x, y, w, h = config.play_rect()
    _label_pill(surface, _fonts()["small"], label, config.COLOR_TEXT_DIM,
                (x + w // 2, y + h - config.FILM_CAPTION_GAP))


def _draw_systems(surface, state: GameState, ui: Ui) -> None:
    source = ui.source(state)
    valid_dests = set(state.systems[source].neighbors) if source is not None else set()

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
        taken.append(_mark_slot(font, pos))   # ...and so is the space a playback
                                              # writes its numbers into, always
    # ...and so is every label already on the map: a name landing on a lane's
    # travel time or a rule's share makes both of them unreadable, and those
    # carry information a name doesn't.
    for lane in state.lanes.values():
        if _fog_state(ui, lane.a) == "hidden" or _fog_state(ui, lane.b) == "hidden":
            continue
        pa, pb = ui.view.to_screen(state.systems[lane.a].pos), ui.view.to_screen(state.systems[lane.b].pos)
        turns = state.travel_turns(lane.a, lane.b) or lane.travel_turns
        taken.append(_pill_rect(font, str(turns), ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)))
    name_floor: dict[int, int] = {}  # a held system's name goes under its badge
    if not ui.history:
        for src, rule in ui.auto_forward.items():
            a = state.systems.get(src)
            if a is None or a.owner_id != ui.human_id:
                continue
            badge = _hold_badge(font, state, ui, src)
            avoid = _pill_rect(font, *badge) if badge is not None else None
            if avoid is not None:
                taken.append(avoid)
                name_floor[src] = avoid.bottom
            for dest, pct in rule.shares.items():
                b = state.systems.get(dest)
                if b is None or pct >= 100:  # nothing drawn — see _draw_forward_rules
                    continue
                pa, pb = ui.view.to_screen(a.pos), ui.view.to_screen(b.pos)
                taken.append(_pill_rect(font, f"{pct}%", _rule_label_center(pa, pb, font, avoid, f"{pct}%")))
    # ...and a playback's own numbers where they are not over a system at all: an
    # open-space clash is marked at the lane fraction it happened at, which no
    # node's reserved slot covers.
    for center, text, _color in _film_labels(state, ui):
        taken.append(_pill_rect(font, text, center))

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
        # Below the node, which is where a name belongs: the space above is held
        # for a playback's numbers (`_mark_slot`), so the fallback above only
        # opens up if that slot is ever tuned smaller than the label gap.
        for side in (1, -1):
            rect = pygame.Rect((0, 0), font.size(sys.name))
            edge = pos[1] + side * (r + config.NODE_LABEL_GAP)
            if side > 0 and sys.id in name_floor:
                edge = name_floor[sys.id] + pad + config.NODE_LABEL_GAP
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


def _draw_zoom_controls(surface, ui: Ui, bottom: int | None = None) -> None:
    """On-map camera cluster — Reset view and zoom −/+, the touch/no-wheel
    equivalent of scroll-to-zoom. Anchored to the map viewport's bottom-right
    corner, just above the HUD bottom bar — or above ``bottom``, the top of
    whatever covers the map's foot: history's docked graphs, or the send
    popup's portrait sheet (drawn after the map clip is lifted in ``draw()``, so
    it's never affected by it)."""
    px, py, pw, ph = config.play_rect()
    s = config.MAP_ZOOM_BTN_SIZE
    gap = config.s(8)
    inset = config.s(12)
    y = (py + ph if bottom is None else bottom) - inset - s
    font = _fonts()["small"]
    accent = _BTN_BLUE[1]

    plus = pygame.Rect(px + pw - inset - s, y, s, s)
    minus = pygame.Rect(plus.x - gap - s, y, s, s)
    reset_label = _key_hint("Reset", "R")
    reset_w = _btn_w(font, reset_label, s)
    reset = pygame.Rect(minus.x - gap - reset_w, y, reset_w, s)

    ui.zoom_plus_rect = (plus.x, plus.y, plus.w, plus.h)
    ui.zoom_minus_rect = (minus.x, minus.y, minus.w, minus.h)
    ui.reset_view_rect = _btn(surface, reset, reset_label, *_BTN_BLUE, font)
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
    turn = _draw_ships_lost(surface, state, ui, turn)
    scoreboard_right = _draw_stage_label(
        surface, ui, w, _draw_campaign_label(surface, ui, w, _draw_challenge_target(surface, ui, w)))
    _draw_scoreboard(surface, state, ui, scoreboard_right, turn.right + config.HUD_PAD * 2)

    # bottom bar (in portrait, the strip and the End Turn row under it)
    by = config.footer_rect(ui.history)[1]
    pygame.draw.rect(surface, (18, 20, 30), (0, by, w, h - by))
    if ui.history:
        # the scrubber (drawn by _draw_scrubber, after the HUD) owns the bottom
        # bar in history mode — zero the live buttons (the whole footer strip,
        # since _draw_footer_buttons never runs here to do it itself) so no
        # stale click resolves and no leftover rect fools the overlap test.
        ui.end_turn_rect = (0, 0, 0, 0)
        for attr in _FOOTER_RECTS:
            setattr(ui, attr, (0, 0, 0, 0))
        ui.more_button_rect = ui.more_menu_rect = (0, 0, 0, 0)
        ui.more_open = False
        # the camera cluster too: `draw` redraws it over a review's map, and
        # full-screen graphs leave no map to point it at
        ui.reset_view_rect = ui.zoom_minus_rect = ui.zoom_plus_rect = (0, 0, 0, 0)
        return

    # End-turn button: the single biggest, easiest touch target in the HUD — the
    # full width of the right info panel, reaching up above the ordinary bottom
    # bar (_draw_side_panel reserves the same config.END_TURN_H so the panel's
    # details never draw underneath it).
    if config.portrait and _popup_docked(ui):
        # the popup's sheet covers End Turn and the strip (`_draw_send_popup`):
        # take them away, as route mode and a film do, so nothing under it answers
        ui.end_turn_rect = ui.more_button_rect = ui.more_menu_rect = (0, 0, 0, 0)
        ui.more_open = False
        for attr in _FOOTER_RECTS:
            setattr(ui, attr, (0, 0, 0, 0))
        return

    br = pygame.Rect(config.end_turn_rect())
    if config.portrait:
        # a full-width bar at the screen's foot: off the very edges, where a
        # phone's rounded corners and gesture bar sit
        br = br.inflate(-2 * config.HUD_PAD, -config.HUD_PAD).move(0, -config.HUD_PAD // 4)
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
            if not ui.route_sel:
                label = "Rally" if ui.route_rally else "Route"
            elif ui.route_rally:
                # picked, but the field is empty: rallying on the only system we
                # hold, or on a pocket with nothing behind it
                label = "Nothing to route"
            else:
                label = "Pick a target"
        colour = config.COLOR_TEXT if ui.route_plan else config.COLOR_TEXT_DIM
        _text(surface, _fonts()["big" if ui.route_plan else "normal"], label, colour,
              center=br.center)
        _draw_footer_buttons(surface, state, ui, by)
        return

    # A film is a playback of a turn already resolved, so the button that would
    # resolve the next one goes away for its duration — the same take-it-away
    # rather than guard-it treatment route mode gets just above. A play-by-post
    # turn we have already submitted is the same case for a different reason: the
    # turn advances when the last seat is in, and nothing anyone presses here
    # brings that forward.
    # A spectator has no turn to end at all, so the block only says so.
    _gone = ui.film is not None or ui.awaiting_others(state) or ui.spectating
    ui.end_turn_rect = (0, 0, 0, 0) if _gone else (br.x, br.y, br.w, br.h)
    if _gone and (ui.awaiting_others(state) or ui.spectating):
        _text(surface, _fonts()["normal"],
              "Spectating" if ui.spectating else "Orders sent", config.COLOR_TEXT_DIM,
              center=br.center)
        _draw_footer_buttons(surface, state, ui, by)
        return
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


_FOOTER_RECTS = FOOTER_RECTS


def _draw_footer_buttons(surface, state: GameState, ui: Ui, by: int) -> None:
    """The bottom bar's button strip, left of the sidebar / End Turn column: touch
    equivalents of the P / A / H / N / G / F / M / X keys plus Quit/Esc, so every
    live-play action is reachable without a keyboard.

    Each button is sized to its own measured label. The strip is two independently
    anchored clusters rather than one row: the buttons that leave this game (Quit,
    Menu, New map) are pinned to the left edge, and the buttons that hand control
    to the AI or a review mode (History, Clear, Route, Autoplay, Play, Fast
    forward) are packed to the right, against the side panel. Pulling the exit
    group away from the rest means there's normally a gap between them, so an
    idle tap near the control cluster can't land on Quit by accident — the two
    clusters only butt up when the strip is squeezed too narrow to keep them
    apart. Where the strip is too narrow to hold every button — a portrait phone,
    or a shrunken desktop window — the least essential are dropped (rects zeroed)
    rather than drawn overlapping. The ranking matters because a touch player has
    no keys to fall back on: Menu survives longest, since it reaches the setup
    screen and Quit from there, and Clear goes first, since the popup's own Cancel
    and Delete already do its job. Fast forward ranks just below
    those two, because it is only ever offered in the one situation it is the
    point of the screen — watching a match you are out of.

    Route mode gets a strip of its own rather than extra buttons on this one — see
    the branch below.
    """
    font = _fonts()["normal"]
    fbh = config.FOOTER_BTN_H
    y = by + (config.footer_rect()[3] - fbh) // 2

    # (ui attribute, label, fill, edge, how long it survives a squeeze, which
    # cluster it's pinned to), in visual order within its cluster — left to right.
    # play/pause is hidden under autoplay, which drives turns on its own timer and
    # would make it a no-op; history needs a recorded turn to scrub through, and
    # under ironman a decided game (`Ui.can_review`).
    specs: list[tuple[str, str, tuple, tuple, int, str]] = []

    # Route mode replaces the strip wholesale rather than adding to it: the shared
    # zeroing loop below then takes every live-play rect out of service for free,
    # so nothing from the ordinary strip can be clicked under an open plan.
    if ui.mode == ROUTING:
        # One button rather than a Chain/Rally pair: with two, whichever is lit has
        # to be read as "you are here" and the other as "go here", and a strip of
        # buttons that all mean "do this" is the wrong place to learn that. This
        # one names the sub-mode it is *in* and switches when pressed. Teal ties it
        # to the live-play Route button it came from, and it outranks Cancel in a
        # squeeze — Cancel still has Esc and the confirm slot behind it, whereas
        # losing this makes the sub-mode unreachable on a touch build.
        mode_label = _key_hint("Mode: Rally" if ui.route_rally else "Mode: Chain", "Tab")
        specs.append(("route_mode_rect", mode_label, *_BTN_TEAL, 6, "right"))
        # Rally only, and only when there is a front to pick: drawn exactly when it
        # would do something, the same gate `can_fast_forward` earns its button by.
        if ui.route_rally and ui.threatened_systems(state):
            specs.append(("route_auto_rect", _key_hint("Auto-route", "T"), *_BTN_VIOLET, 5, "right"))
        specs.append(("route_cancel_rect", _key_hint("Cancel", "Esc"), *_BTN_RED, 4, "right"))
        specs.append(("menu_button_rect", _key_hint("Menu", "M"), *_BTN_BLUE, 2, "right"))
        _lay_out_footer(surface, ui, specs, y, fbh, font)
        return

    # quit is the only touch equivalent of Esc — without it a player with no
    # keyboard has no way out. Opens the confirm modal, like Esc; on the web that
    # can only ask the browser to close the window (see main.leave_app). Pinned
    # left with the rest of the "leaves this game" group, away from the
    # control-cluster taps on the right.
    specs.append(("quit_button_rect", _key_hint("Quit", "Esc"), *_BTN_RED, 8, "left"))
    specs.append(("menu_button_rect", _key_hint("Menu", "M"), *_BTN_BLUE, 9, "left"))
    # new map reseeds mid-game too (not just at game end), with no confirmation —
    # matching the N key exactly. (R is reset-view, and has no button here: the
    # on-map zoom cluster carries it.)
    # Not in a gauntlet stage: its seed is pinned, so a new map would leave it.
    if not ui.in_stage:
        specs.append(("restart_live_button_rect", _key_hint("New map", "N"), *_BTN_AMBER, 2, "left"))
    if ui.can_review(state):
        specs.append(("history_button_rect", _key_hint("History", "H"), *_BTN_VIOLET, 3, "right"))
    # clear/cancel mirrors X (and Backspace/Delete): discards the send being
    # adjusted, or drops the highlighted order/rule; a no-op when nothing is.
    if not ui.spectating:   # a spectator has nothing to clear
        specs.append(("clear_button_rect", _key_hint("Clear", "X"), *_BTN_BLUE, 1, "right"))
    # Route mode: multi-select a group of systems and forward them all to one
    # destination. Gated on the same predicate as the G key, so the button is
    # drawn exactly when the mode means something.
    if ui.can_route(state):
        specs.append(("route_button_rect", _key_hint("Route", "G"), *_BTN_TEAL, 5, "right"))
    # autoplay hands the human seat's decisions to the AI, or takes control back;
    # shown either way, unlike play/pause. Neither is offered in a shared match:
    # a play-by-post turn advances when the last seat submits, so there is no
    # clock here for either control to run, and a client that ran one anyway
    # would resolve a turn the others had not agreed to. Taken away rather than
    # guarded, the same treatment End Turn gets while we are waiting — and
    # `_FOOTER_RECTS` zeroes both, so neither answers a tap either.
    if not ui.in_pbp:
        specs.append(("autoplay_button_rect", _key_hint("Take control" if ui.autoplay else "Autoplay", "A"), *(_BTN_ACTIVE if ui.autoplay else _BTN_BLUE), 4, "right"))
        if not ui.autoplay:
            specs.append(("play_pause_rect", _key_hint("Pause" if ui.playing else "Play", "P"), *(_BTN_ACTIVE if ui.playing else _BTN_BLUE), 6, "right"))
    # Fast forward: only while the human is knocked out and the match plays on, so
    # the rest of it can be watched at speed rather than a turn every 350ms. Same
    # gate the F key goes through, so the button is drawn exactly when it means
    # something.
    if ui.can_fast_forward(state):
        specs.append(
            ("fast_forward_rect", _key_hint("Normal speed" if ui.fast_forward else "Fast forward", "F"), *(_BTN_ACTIVE if ui.fast_forward else _BTN_BLUE), 7, "right")
        )

    _lay_out_footer(surface, ui, specs, y, fbh, font)


def _lay_out_footer(surface, ui: Ui, specs, y: int, fbh: int, font) -> None:
    """Measure, squeeze and draw the footer strip's two clusters.

    Every rect the strip can ever record is zeroed first, so whichever buttons this
    call *doesn't* draw stop answering clicks — that is what lets route mode swap
    the strip out and know nothing from the live-play one is left live.

    The "left" cluster is pinned to the strip's left edge and the "right" cluster
    packed against its right end (the side panel); what's between them is empty
    space, not a shared gap, so the two clusters drift apart when there's room
    and only meet once the strip is squeezed down to needing every pixel.
    Squeezing treats every button as one list: the least essential (lowest keep
    rank) folds first, from either cluster, behind a "more" button at the left
    end, whose list (`_draw_more_menu`) offers them again — so a narrow strip
    loses room, never a control. A compact screen folds the left cluster, the
    ways out of this game, from the start: they are rarely pressed, and the room
    goes to the controls that are.
    """
    fx, _fy, fw, _fh = config.footer_rect()
    widths = {spec[0]: _btn_w(font, spec[1]) for spec in specs}
    avail = fw - config.HUD_PAD * 2
    more_w = fbh

    def row_w(items) -> int:
        return sum(widths[s[0]] for s in items) + config.BTN_GAP * (len(items) - 1)

    shown = list(specs)
    folded = [s for s in shown if s[5] == "left"] if config.compact else []
    shown = [s for s in shown if s not in folded]

    def need(items) -> int:
        return row_w(items) + (more_w + config.BTN_GAP if folded else 0)

    while shown and need(shown) > avail:
        least = min(shown, key=lambda s: s[4])
        shown.remove(least)
        folded.append(least)

    for attr in _FOOTER_RECTS:
        setattr(ui, attr, (0, 0, 0, 0))

    x = fx + config.HUD_PAD
    if folded:
        more = pygame.Rect(x, y, more_w, fbh)
        _draw_more_button(surface, more, ui.more_open)
        ui.more_button_rect = (more.x, more.y, more.w, more.h)
        x = more.right + config.BTN_GAP
    else:
        ui.more_button_rect = ui.more_menu_rect = (0, 0, 0, 0)
        ui.more_open = False
    for attr, label, fill, edge, _keep, group in shown:
        if group != "left":
            continue
        rect = pygame.Rect(x, y, widths[attr], fbh)
        setattr(ui, attr, _btn(surface, rect, label, fill, edge, font))
        x = rect.right + config.BTN_GAP

    right = fx + fw - config.HUD_PAD
    for attr, label, fill, edge, _keep, group in reversed(shown):
        if group != "right":
            continue
        rect = pygame.Rect(right - widths[attr], y, widths[attr], fbh)
        setattr(ui, attr, _btn(surface, rect, label, fill, edge, font))
        right = rect.x - config.BTN_GAP

    if folded and ui.more_open:
        # in the strip's own order, so the list reads as the strip would
        _draw_more_menu(surface, ui, [s for s in specs if s in folded], more, font)
    else:
        ui.more_menu_rect = (0, 0, 0, 0)


# The strip's Menu means the setup screen; in a list under a menu button of its
# own, it needs saying which menu.
_MORE_LABELS = {"menu_button_rect": ("Menu", "Setup menu")}


def _draw_more_button(surface, rect: pygame.Rect, lit: bool) -> None:
    """The overflow button: three bars, drawn (the font has no ☰ glyph)."""
    fill, edge = _BTN_ACTIVE if lit else _BTN_BLUE
    pygame.draw.rect(surface, fill, rect, border_radius=config.s(6))
    pygame.draw.rect(surface, edge, rect, config.s(2), border_radius=config.s(6))
    bw = rect.w * 9 // 20
    t = max(2, config.s(2.5))
    for k in (-1, 0, 1):
        cy = rect.centery + k * rect.h // 5
        pygame.draw.rect(surface, config.COLOR_TEXT, (rect.centerx - bw // 2, cy - t // 2, bw, t),
                         border_radius=t // 2)


def _draw_more_menu(surface, ui: Ui, items, anchor: pygame.Rect, font) -> None:
    """The folded buttons as a list rising from the "more" button, one full-width
    row each. Input closes the list on any press; one on a row is that button's,
    and anything else on or off the list does nothing more (`input._more_press`)."""
    pad = config.s(6)
    row_h = anchor.h
    labels = []
    for attr, label, *_rest in items:
        old, new = _MORE_LABELS.get(attr, (None, None))
        labels.append(label.replace(old, new, 1) if old else label)
    w = max(_btn_w(font, lab) for lab in labels) + config.s(24)
    h = pad * 2 + row_h * len(items) + pad * (len(items) - 1)
    box = pygame.Rect(anchor.x, anchor.y - config.BTN_GAP - h, w, h)
    box.y = max(config.HUD_TOP_H, box.y)
    pygame.draw.rect(surface, (20, 24, 36), box, border_radius=config.s(8))
    pygame.draw.rect(surface, _BTN_BLUE[1], box, config.s(1), border_radius=config.s(8))
    y = box.y + pad
    for (attr, _label, fill, edge, *_rest), lab in zip(items, labels):
        rect = pygame.Rect(box.x + pad, y, box.w - 2 * pad, row_h)
        setattr(ui, attr, _btn(surface, rect, lab, fill, edge, font))
        y += row_h + pad
    ui.more_menu_rect = (box.x, box.y, box.w, box.h)


_DEAD_COLOR = (92, 96, 110)


def _draw_ships_lost(surface, state: GameState, ui: Ui, turn: pygame.Rect) -> pygame.Rect:
    """Our seat's ships lost so far, just right of the turn counter: the score's
    tie-break (`Player.ships_lost`, the number the win overlay and a posted score
    report), so someone chasing a posted result can see where they stand on it
    without waiting for the end. Our own losses are never fogged.

    Returns the rect the scoreboard must start clear of, in place of the turn
    counter's.
    """
    player = state.players.get(ui.human_id)
    if player is None or ui.spectating:   # a spectator sits at neutral's id
        return turn
    label = _text(surface, _fonts()["small"], f"{player.ships_lost} lost", config.COLOR_TEXT_DIM,
                  midleft=(turn.right + config.HUD_PAD, config.HUD_TOP_H // 2))
    return turn.union(label)


def _draw_challenge_target(surface, ui: Ui, w: int) -> int:
    """The score to beat, pinned in the top bar's right corner for as long as the
    match runs — not just on the win overlay at the end of it, since the point of
    a persistent reminder is not having to hold the number in your head (or reopen
    a finished game to recall it). Teal ties it to the same accent as the
    "Challenge a friend" button that made this a challenge in the first place.

    Returns the x the scoreboard must stay clear of (``w`` when there is nothing
    to draw), the same measured-not-hardcoded handoff the turn counter gives it.
    """
    if ui.challenge_target is None:
        return w
    turns, lost = ui.challenge_target
    who = f"{ui.challenge_by}'s " if ui.challenge_by else ""
    text = f"Beat {who}{turns}t / {lost} lost"
    label = _text(surface, _fonts()["small"], text, _BTN_TEAL[1],
                  midright=(w - config.HUD_PAD, config.HUD_TOP_H // 2))
    return label.left - config.HUD_PAD


def _draw_campaign_label(surface, ui: Ui, w: int, right: int) -> int:
    """The weekly campaign's reminder ("Node 3: post within 12 min"), just left of
    the challenge target, in gold like the campaign page's timers. `main` writes
    the text once a frame, so the countdown moves without this module reading
    any clock or knowing the rules.

    Returns the x the scoreboard must stay clear of, the same handoff
    `_draw_challenge_target` makes.
    """
    if not ui.campaign_label:
        return right
    edge = w - config.HUD_PAD if right >= w else right
    label = _text(surface, _fonts()["small"], ui.campaign_label, config.COLOR_CAMPAIGN,
                  midright=(edge, config.HUD_TOP_H // 2))
    return label.left - config.HUD_PAD


def _draw_stage_label(surface, ui: Ui, w: int, right: int) -> int:
    """Which gauntlet stage this is ("Tutorial 3/10 · Defender's edge"), and its
    target if it has one (" · target 14"), left of the campaign reminder, with
    the same measured handoff to the scoreboard."""
    if not ui.stage_label:
        return right
    edge = w - config.HUD_PAD if right >= w else right
    text = ui.stage_label + (f" · {ui.stage_target.short()}" if ui.stage_target is not None else "")
    label = _text(surface, _fonts()["small"], text, _BTN_TEAL[1],
                  midright=(edge, config.HUD_TOP_H // 2))
    return label.left - config.HUD_PAD


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
    ui.score_rects = []

    def vis(pid: int) -> str:
        return _score_vis(state, ui, pid)

    seats = [pid for pid in sorted(state.players) if not state.players[pid].is_neutral and vis(pid) != "hidden"]
    if not seats:
        return

    def label(pid: int, v: str, with_name: bool, terse: int = 0) -> str:
        p = state.players[pid]
        # A shared match can seat a person at any colour, so the scoreboard is the
        # one place that says outright which swatch is yours — a solo game has no
        # such ambiguity (it is always the seat you are looking at), hence `in_pbp`.
        you = " (you)" if ui.in_pbp and pid == ui.human_id else ""
        if v == "out":
            return f"{p.name} out{you}" if with_name else f"out{you}"
        systems, ships, prod = _player_stats(state, pid) if v == "live" else ui.player_intel[pid]
        name = f"{p.name} " if with_name else ""
        mark = "" if v == "live" else " ?"  # stale, last-known intel
        stats_ = (f"{systems}s {ships}sh {prod:.1f}/t", f"{systems}s {ships}sh", f"{ships}sh",
                  f"{ships}")[terse]
        return f"{name}{stats_}{mark}{you}"

    def row_width(with_name: bool, terse: int = 0) -> int:
        return sum(sw_w + font.size(label(pid, vis(pid), with_name, terse))[0] + gap for pid in seats)

    # Shed detail until the row fits: names, then (a phone's top bar) production,
    # then systems, then the unit — ships, the number a fight turns on, stays. Tapping a seat's
    # entry still opens its full tooltip (`_draw_score_tooltip`).
    room = w - x0 - config.HUD_PAD
    with_name = row_width(True) <= room
    terse = 0
    while not with_name and terse < 3 and row_width(False, terse) > room:
        terse += 1
    if terse:
        gap = config.s(14)
    x = x0
    for pid in seats:
        v = vis(pid)
        color = _DEAD_COLOR if v == "out" else config.player_color(pid)
        sw = pygame.Rect(x, cy - sw_size // 2, sw_size, sw_size)
        pygame.draw.rect(surface, color, sw, border_radius=config.s(3))
        if v == "frozen":
            _hatch_rect(surface, sw, _brighten(color))
        txt = label(pid, v, with_name, terse)
        tcolor = config.COLOR_TEXT_DIM if v == "frozen" else color
        _text(surface, font, txt, tcolor, midleft=(x + sw_w, cy))
        ui.score_rects.append((pid, (x, 0, sw_w + font.size(txt)[0], config.HUD_TOP_H)))
        x += sw_w + font.size(txt)[0] + gap


def _score_vis(state: GameState, ui: Ui, pid: int) -> str:
    """How much the scoreboard knows of ``pid``: live | frozen | out | hidden."""
    p = state.players[pid]
    live = pid == ui.human_id or any(state.systems[s].owner_id == pid for s in ui.visible)
    if not (live or pid in ui.player_intel):
        return "hidden"  # never sighted — you don't know it exists
    if not p.alive:
        return "out"
    return "live" if live else "frozen"


def _seat_who(state: GameState, ui: Ui, pid: int) -> str:
    """Who is playing ``pid``, for a tooltip or a key: its bot (`Ui.bot_label`),
    "you", or "a person" for someone else's seat in a shared match."""
    bot = ui.bot_label(state, pid)
    if bot is not None:
        return bot
    if pid == ui.human_id:
        return "you"
    return "a person"


def _draw_score_tooltip(surface, state: GameState, ui: Ui) -> None:
    """The hovered scoreboard entry spelled out under the top bar: who plays the
    seat, then its standings in words (or the last-known ones, or that it is out).
    Drawn last of the frame, so nothing below the bar covers it."""
    pid = ui.score_hover
    rect = next((r for p, r in ui.score_rects if p == pid), None)
    if pid is None or rect is None or pid not in state.players:
        return
    v = _score_vis(state, ui, pid)
    if v == "hidden":
        return
    p = state.players[pid]
    lines = [f"{p.name} · {_seat_who(state, ui, pid)}"]
    if v == "out":
        lines.append("Knocked out")
    else:
        systems, ships, prod = _player_stats(state, pid) if v == "live" else ui.player_intel[pid]
        stats_line = f"{systems} systems · {ships} ships · {prod:.1f} ships/turn"
        lines.append(stats_line if v == "live" else f"Last seen: {stats_line}")
    font = _fonts()["small"]
    pad = config.s(8)
    w = max(font.size(line)[0] for line in lines) + 2 * pad
    h = len(lines) * _row_h() + 2 * pad - (_row_h() - font.get_height())
    x = max(config.HUD_PAD, min(rect[0], surface.get_width() - w - config.HUD_PAD))
    box = pygame.Rect(x, config.HUD_TOP_H + config.s(4), w, h)
    pygame.draw.rect(surface, (24, 26, 38), box, border_radius=config.s(6))
    pygame.draw.rect(surface, config.player_color(pid), box, config.s(1), border_radius=config.s(6))
    y = box.y + pad
    for i, line in enumerate(lines):
        _text(surface, font, line, config.COLOR_TEXT if i == 0 else config.COLOR_TEXT_DIM, topleft=(box.x + pad, y))
        y += _row_h()


def _player_stats(state: GameState, pid: int) -> tuple[int, int, float]:
    """(systems owned, ships including in transit, production in ships/turn)."""
    return fog.player_totals(state, pid)


# --------------------------------------------------------------------------- #
# Info panel (right column)
# --------------------------------------------------------------------------- #
def _popup_docked(ui: Ui) -> bool:
    """Does the send popup sit in the side panel's place rather than over the map?
    On a compact screen there is no map to spare for it (`config.compact`)."""
    return config.compact and ui.mode == CHOOSING and not ui.history


def _draw_side_panel(surface, state: GameState, ui: Ui) -> None:
    # The panel's own footer is the big End Turn button (see _draw_hud), not the
    # ordinary bottom bar under the map — it reserves config.END_TURN_H instead.
    # History mode never draws that button (the scrubber owns the bottom bar
    # there instead), so it falls back to the plain HUD_BOTTOM_H reservation.
    px, py, pw, ph = config.side_panel_rect(ui.history)
    pygame.draw.rect(surface, (16, 18, 28), (px, py, pw, ph))
    if config.portrait:
        pygame.draw.line(surface, (40, 44, 60), (px, py), (px + pw - 1, py), config.s(1))
    else:
        pygame.draw.line(surface, (40, 44, 60), (px, py), (px, py + ph - 1), config.s(1))
    if _popup_docked(ui):
        return   # the popup draws in this space (`_draw_send_popup`), after the HUD

    # Whatever runs long is cut at the panel's floor rather than drawn over the
    # bars, which a portrait panel, a fraction of the screen tall, would reach.
    surface.set_clip((px, py, pw, ph))
    try:
        _draw_side_panel_body(surface, state, ui, px, py, pw, ph)
    finally:
        surface.set_clip(None)


def _draw_side_panel_body(surface, state: GameState, ui: Ui, px, py, pw, ph) -> None:
    global _PANEL_COLUMN_W
    # `content_bottom` is where the details have to stop: the panel's own floor.
    content_bottom = py + ph - config.s(8)

    x, y = px + config.PANEL_PAD, py + config.PANEL_PAD
    if ui.in_pbp:
        y = _panel_you_are(surface, ui, x, y)
    if ui.mode == ROUTING:
        ui.clear_forward_rect = (0, 0, 0, 0)
        ui.clear_dangerous_rect = (0, 0, 0, 0)
        _panel_route(surface, state, ui, x, y, content_bottom)
        return
    # persistent "clear all forwarding" button, shown whenever any rule exists.
    # Not while reviewing history: `input._handle_history_event` is modal and never
    # hit-tests them, so drawing them there offers a destructive press that does
    # nothing — the same reason route mode zeroes them just above, arrived at from
    # the other side (there they must not fire, here they cannot).
    # A highlighted rule wins the panel's focus over a plain hover; while the popup
    # is open `ui.selected` is that rule's source anyway, so the two agree.
    editing_rule = ui.sel_forward if ui.sel_forward in ui.auto_forward else None
    if editing_rule is not None:
        focus = editing_rule
    else:
        focus = ui.selected if ui.selected is not None else ui.hover
    focused = focus is not None and focus in state.systems
    dangerous = [] if ui.history else [sid for sid in ui.auto_forward if ui.rule_is_hostile(state, sid)]
    if config.portrait:
        # A short band: the clear buttons share one row, and only while nothing
        # is in focus, so a selected system's readout gets the whole band.
        if not focused and not ui.history and ui.auto_forward:
            y = _draw_clear_row(surface, ui, px, y, pw, len(dangerous))
    else:
        if ui.auto_forward and not ui.history:
            y = _draw_clear_forward_button(surface, ui, px, y)
        # below it, a narrower button for just the rules currently tinted dangerous
        # (pointed at a system we don't hold) — shown only while at least one exists
        if dangerous:
            y = _draw_clear_dangerous_button(surface, ui, px, y, len(dangerous))
    if not focused:
        if ui.stage_hint and not ui.history:
            y = _panel_stage_hint(surface, ui, x, y, content_bottom)
            if config.compact:
                # The hint is a phone panel's one job in a stage: with it, no
                # legend fits whole, and the briefing has taught the rules.
                return
        if config.portrait:
            # The rules in full are the tutorial's to teach; the band keeps a
            # reminder, so the map gets the room.
            for line in _wrap(_fonts()["small"], _portrait_hint(ui), _panel_w()):
                y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
            return
        _panel_legend(surface, x, y, content_bottom, history=ui.history,
                      spectating=ui.spectating)
        return

    gap = config.ROW_GAP * 2  # breathing space between sections
    dest = _panel_lane_target(state, ui, focus)
    if config.portrait:
        # A full-width band a few rows tall: the system on the left, its lane
        # and rule on the right, rather than one column running off the foot.
        half = pw // 2
        _PANEL_COLUMN_W = half
        outer = surface.get_clip()
        try:
            surface.set_clip(outer.clip((px, py, half, ph)))
            _panel_system_compact(surface, state, ui, x, y, state.systems[focus])
            surface.set_clip(outer.clip((px + half, py, pw - half, ph)))
            y2 = y - gap
            if dest is not None:
                y2 = _panel_lane(surface, state, ui, px + half + config.PANEL_PAD, y2 + gap, focus, dest)
            if focus in ui.auto_forward:
                _panel_rule(surface, state, ui, px + half + config.PANEL_PAD, y2 + gap, focus)
        finally:
            _PANEL_COLUMN_W = None
            surface.set_clip(outer)
        return

    y = _panel_system(surface, state, ui, x, y, state.systems[focus])
    if dest is not None:
        y = _panel_lane(surface, state, ui, x, y + gap, focus, dest)

    if focus in ui.auto_forward:
        _panel_rule(surface, state, ui, x, y + gap, focus)


def _portrait_hint(ui: Ui) -> str:
    """The portrait band's line while nothing is in focus."""
    if ui.history:
        return "Reviewing played turns. Drag the scrubber or step a turn; tap a system to see it."
    if ui.spectating:
        return "Watching: tap a system to see it. Drag to pan, pinch to zoom."
    return "Tap one of your systems, then a neighbour, to send its ships. Drag to pan, pinch to zoom."


def _draw_clear_row(surface, ui: Ui, px: int, y: int, pw: int, dangerous: int) -> int:
    """Portrait's clear buttons: "Clear all forwarding" and, while any rule is
    pointed at a system we don't hold, "Clear dangerous", side by side in one
    row. Records the same rects the landscape column does."""
    font = _fonts()["small"]
    h = _tap_size(font.get_height() + config.s(10))
    inset, gap = config.s(12), config.s(8)
    w = pw - 2 * inset
    items = [(f"Clear forwarding ({ui.forward_lanes()})", (58, 38, 42), (170, 96, 104), "clear_forward_rect")]
    if dangerous:
        items.append((f"Clear dangerous ({dangerous})", *_BTN_DANGER, "clear_dangerous_rect"))
    bw = (w - gap * (len(items) - 1)) // len(items)
    for i, (label, fill, edge, attr) in enumerate(items):
        r = pygame.Rect(px + inset + i * (bw + gap), y, bw, h)
        pygame.draw.rect(surface, fill, r, border_radius=config.s(5))
        pygame.draw.rect(surface, edge, r, config.s(1), border_radius=config.s(5))
        _text(surface, font, label, config.COLOR_TEXT, center=r.center)
        setattr(ui, attr, (r.x, r.y, r.w, r.h))
    return y + h + config.s(10)


def _panel_system_compact(surface, state: GameState, ui: Ui, x, y, sys) -> int:
    """`_panel_system` in the portrait band's few rows: the name, then who holds
    it and its ships, what it builds, and the pressure on it."""
    width = _panel_w()
    if sys.id == ui.selected and sys.name in starlore.LORE and not ui.history:
        width -= _draw_lore_button(surface, ui, x + _panel_w(), y)
    if sys.id not in ui.visible:
        y = _head_named(surface, x, y, sys.label, config.COLOR_TEXT_DIM, width)
        return _row(surface, x, y, "Out of sight", config.COLOR_TEXT_DIM)
    colour = config.player_color(sys.owner_id)
    y = _head_named(surface, x, y, sys.label, colour, width)
    who = config.player_name(sys.owner_id)
    if sys.owner_id == ui.human_id and not ui.spectating:
        y = _row(surface, x, y, f"{who} · {ui.available(state, sys.id)}/{sys.ships} free", colour)
    else:
        y = _row(surface, x, y, f"{who} · {sys.ships} ships", colour)
    if sys.production > 0:
        y = _row(surface, x, y, f"1 per {sys.production}t · {sys.prod_progress}/{sys.production}",
                 config.COLOR_TEXT_DIM)
    else:
        y = _row(surface, x, y, "Builds nothing", config.COLOR_TEXT_DIM)
    threat = _adjacent_enemy_strength(state, sys.id, sys.owner_id)
    fin, ein = _inbound_summary(state, sys.id, sys.owner_id)
    tail = f" · in +{fin}/{ein}" if fin or ein else f" · {len(sys.neighbors)} lanes"
    return _row(surface, x, y, f"Threat {threat}{tail}", config.COLOR_TEXT_DIM)


def _draw_clear_forward_button(surface, ui: Ui, px: int, y: int) -> int:
    """A slim panel-top button that clears every standing forward rule; records
    its hit-rect on ``ui``. Returns the y below it for the details that follow."""
    font = _fonts()["small"]
    label = f"Clear all forwarding ({ui.forward_lanes()})"
    h = font.get_height() + config.s(10)
    r = pygame.Rect(px + config.s(12), y, _panel_w() + config.PANEL_PAD * 2 - config.s(24), h)
    pygame.draw.rect(surface, (58, 38, 42), r, border_radius=config.s(5))
    pygame.draw.rect(surface, (170, 96, 104), r, config.s(1), border_radius=config.s(5))
    _text(surface, font, label, config.COLOR_TEXT, center=r.center)
    ui.clear_forward_rect = (r.x, r.y, r.w, r.h)
    return y + h + config.s(10)


def _draw_clear_dangerous_button(surface, ui: Ui, px: int, y: int, count: int) -> int:
    """A slim panel-top button, below "Clear all forwarding", that clears only the
    rules currently tinted dangerous (`_draw_forward_rules`) — pointed at a system
    we don't hold. Records its hit-rect on ``ui``. Returns the y below it."""
    font = _fonts()["small"]
    label = f"Clear dangerous ({count})"
    h = font.get_height() + config.s(10)
    r = pygame.Rect(px + config.s(12), y, _panel_w() + config.PANEL_PAD * 2 - config.s(24), h)
    pygame.draw.rect(surface, _BTN_DANGER[0], r, border_radius=config.s(5))
    pygame.draw.rect(surface, _BTN_DANGER[1], r, config.s(1), border_radius=config.s(5))
    _text(surface, font, label, config.COLOR_TEXT, center=r.center)
    ui.clear_dangerous_rect = (r.x, r.y, r.w, r.h)
    return y + h + config.s(10)


def _draw_h_arrow_button(surface, rect: pygame.Rect, left: bool, enabled: bool) -> None:
    """A ◀/▶ step button for the history scrubber — drawn rather than typed, since
    the monospace font has no arrow glyph (same reason as ``_draw_return_glyph``).
    A disabled one (at either end of the history) still draws, dimmed, so the
    pair doesn't shift as you scrub."""
    color = config.COLOR_TEXT_DIM if enabled else (58, 62, 78)
    pygame.draw.rect(surface, config.COLOR_BG, rect, border_radius=config.s(4))
    pygame.draw.rect(surface, color, rect, config.s(1), border_radius=config.s(4))
    cx, cy = rect.center
    r = max(2, rect.h // 5)
    pts = ([(cx + r // 2, cy - r), (cx + r // 2, cy + r), (cx - r, cy)] if left
           else [(cx - r // 2, cy - r), (cx - r // 2, cy + r), (cx + r, cy)])
    pygame.draw.polygon(surface, color, pts)


def _row(surface, x, y, text, color) -> int:
    """One line of small panel text; returns the y for the line below it."""
    if text:
        _text(surface, _fonts()["small"], text, color, topleft=(x, y))
    return y + _row_h()


def _head(surface, x, y, text, color) -> int:
    """A panel section heading, in the larger font; returns the y below it."""
    _text(surface, _fonts()["normal"], text, color, topleft=(x, y))
    return y + _row_h("normal")


def _panel_you_are(surface, ui: Ui, x, y) -> int:
    """'You are {seat}', in that seat's own colour — the one line that says
    outright which colour is yours. Only drawn in play-by-post (`in_pbp`): a
    shared match can seat a person at any colour, but a solo game has no such
    ambiguity, since it is always the one seat you are looking at. A
    spectator holds no seat, and the line says that instead. Under it,
    the match's title, or its pass-phrase name where it has none — whichever the
    lobby headlines it by."""
    if ui.spectating:
        y = _row(surface, x, y, "Spectating", config.COLOR_TEXT)
    else:
        y = _row(surface, x, y, f"You are {config.player_name(ui.human_id)}",
                 config.player_color(ui.human_id))
    label = ui.pbp_title or matchnames.phrase(ui.pbp_match)
    return _rows_named(surface, x, y, label, config.COLOR_TEXT_DIM) if label else y


# Set while a portrait panel draws one of its two columns, so every drawer that
# reflows to the panel reflows to that column instead (see _draw_side_panel).
_PANEL_COLUMN_W: int | None = None


def _panel_w() -> int:
    """Width available to panel text: the panel (or the column of it being drawn)
    minus its two inner margins."""
    if _PANEL_COLUMN_W is not None:
        return _PANEL_COLUMN_W - config.PANEL_PAD * 2
    return config.side_panel_rect()[2] - config.PANEL_PAD * 2


def _head_named(surface, x, y, text, color, width: int | None = None) -> int:
    """A heading whose length we don't control, because a star name is in it: the
    normal font where it fits ``width`` (the panel's, by default), the small one
    where it doesn't (every catalogue name fits at that size, so nothing is ever
    cut)."""
    kind = "normal" if _fonts()["normal"].size(text)[0] <= (width or _panel_w()) else "small"
    _text(surface, _fonts()[kind], text, color, topleft=(x, y))
    return y + _row_h(kind)


def _rows_named(surface, x, y, text, color) -> int:
    """``_row`` for a line with a star name in it: reflowed to the panel width, so a
    long name wraps onto a second row instead of spilling past the panel edge."""
    for line in _wrap(_fonts()["small"], text, _panel_w()):
        y = _row(surface, x, y, line, color)
    return y


def _panel_system(surface, state: GameState, ui: Ui, x, y, sys) -> int:
    width = _panel_w()
    if sys.id == ui.selected and sys.name in starlore.LORE and not ui.history:
        width -= _draw_lore_button(surface, ui, x + _panel_w(), y)
    if sys.id not in ui.visible:
        # fogged system: we know where it is, not who holds it or how strong it is
        y = _head_named(surface, x, y, sys.label, config.COLOR_TEXT_DIM, width)
        y = _row(surface, x, y, "Owner: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "Ships: ?", config.COLOR_TEXT_DIM)
        y = _row(surface, x, y, "(out of sight)", config.COLOR_TEXT_DIM)
        return y
    y = _head_named(surface, x, y, sys.label, config.player_color(sys.owner_id), width)
    y = _row(surface, x, y, f"Owner: {config.player_name(sys.owner_id)}", config.player_color(sys.owner_id))
    if sys.owner_id == ui.human_id and not ui.spectating:
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


def _draw_lore_button(surface, ui: Ui, right: int, y: int) -> int:
    """The i at the right end of a selected system's heading that opens its star
    info card; records its hit-rect on ``ui``. Returns the heading width it takes."""
    d = _fonts()["normal"].get_height()
    r = pygame.Rect(right - d, y, d, d)
    pygame.draw.circle(surface, config.COLOR_TEXT_DIM, r.center, d // 2, config.s(1))
    _text(surface, _fonts()["small"], "i", config.COLOR_TEXT_DIM, center=r.center)
    grow = _tap_size(d) - d
    hit = r.inflate(grow, grow)
    ui.lore_button_rect = (hit.x, hit.y, hit.w, hit.h)
    return d + config.s(6)


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
        go = ui.forward_this_turn(state, src).get(dest, 0)
        y = _rows_named(surface, x, y, f"Forwarding {ui.edited_share()}% · {go} go", config.COLOR_SELECT)
    elif ui.mode == CHOOSING:
        y = _row(surface, x, y, f"Sending: {ui.chosen}", config.COLOR_SELECT)
    return y


def _panel_rule(surface, state: GameState, ui: Ui, x, y, src) -> int:
    """The focused system's standing rule: its hold, then each lane with its share
    and the ships that share sends this turn. The lane the popup is on is picked
    out in the select colour."""
    rule = ui.auto_forward[src]
    editing = src == ui.sel_forward or (ui.mode == CHOOSING and ui.forward_armed and src == ui.selected)
    color = config.COLOR_SELECT if editing else config.player_color(ui.human_id)
    y = _head(surface, x, y, "Auto-forward", color)
    if rule.hold:
        y = _row(surface, x, y, f"holds {rule.hold}", _forward_accent())
    this_turn = ui.forward_this_turn(state, src)
    for dest, pct in rule.shares.items():
        # a rule can outlive its destination for a frame (the system was taken and
        # the map rebuilt); name it when it is still there, fall back to the bare id
        d = state.systems.get(dest)
        line = f"-> {d.label if d else f'System {dest}'}  {pct}% · {this_turn.get(dest, 0)}"
        lit = editing and (dest == ui.dest or ui.mode != CHOOSING)
        y = _rows_named(surface, x, y, line, config.COLOR_SELECT if lit else config.COLOR_TEXT)
    if config.touch_ui:
        hint = "−/+: share · Delete: remove" if editing else "tap a lane to edit"
    else:
        hint = "wheel: share · X: remove" if editing else "click a lane · X: clear"
    y = _rows_named(surface, x, y, hint, config.COLOR_TEXT_DIM)
    return y


# What the panel says when nothing is selected: how the game is actually played,
# as prose that `_wrap` reflows to the panel's width. The touch build gets the same
# rules in tap language and none of the keyboard shortcuts — there is no keyboard to
# use them from, and every one of them has a button in the bottom bar.
#
# Kept short on purpose, and measured rather than guessed. `_panel_legend` cuts
# this off at `content_bottom` rather than spilling, with no ellipsis and no
# scroll, so every line past the panel's floor is prose nobody ever reads the end
# of. An empty panel holds 23 rows on the touch build and 28-35 on a desktop or
# browser one, and the touch text is the binding case, which is the wrong way
# round — it is the build with no keyboard shortcuts to fall back on. So only what has to be taught lives here (the goal, the send, the
# standing rule), ordered with the most droppable line last, and anything a
# control already explains at the point of use stays there instead: route mode's
# two sub-modes are described by `_panel_route` once it is open, and the actions
# with a footer button (History, Autoplay, Fast forward, Menu, New map) name
# their own key on the button itself via `_key_hint`.
# `test_help_text_fits_the_empty_panel_whole` is what holds the length.
_LEGEND_TOUCH = """Take every system to win.

Tap one of your systems, then a neighbour, to send its garrison down that lane
— or drag between the two.

The popup retunes the count, or makes it a standing rule (Forward). Done
finishes with that system.

Route plans a whole front's worth of rules at once.

Drag to pan, pinch to zoom."""

_LEGEND_KEYS = """Take every system to win.

Click one of your systems, then a neighbour, to send its garrison down that
lane — or drag between the two.

The popup retunes the count; its Forward tab turns the send into a standing
rule instead, and Shift+click arms one without opening it.

Route (G) plans a whole front's worth of rules at once.

Drag to pan, wheel to zoom."""

# A landscape phone (`config.compact`) has a narrower, shorter panel: about 14
# rows of 24 characters against the touch build's 23 rows, so the two scenes
# whose legend runs longer get a cut of their own. What goes is what a control
# already says at the point of use: Done, route mode, the Exit button.
_LEGEND_COMPACT_TOUCH = """Take every system to win.

Tap your system, then a neighbour, to send ships, or drag between them.

The popup sets the count; Forward makes it a standing rule.

Drag to pan, pinch to zoom."""

_LEGEND_HISTORY_COMPACT_TOUCH = """Reviewing turns already played: nothing here changes the game.

Scrub or ◀/▶ to step a turn; Play runs it back. Graphs charts every seat.

Rewind to here replays from this turn."""

# A spectator has nothing to send, so none of the live legend's verbs are theirs:
# only what the screen is for, and how to look around it.
_LEGEND_SPECTATING_TOUCH = """Watching from no seat: the whole board, and nobody's orders.

Each turn plays out here once every seat is in. The line at the top says who the
turn is waiting on.

Tap a system to see it. Drag to pan, pinch to zoom."""

_LEGEND_SPECTATING_KEYS = """Watching from no seat: the whole board, and nobody's orders.

Each turn plays out here once every seat is in. The line at the top says who the
turn is waiting on.

Point at a system to see it. Drag to pan, wheel or −/+ to zoom."""

# History review is a different scene with different controls, so it gets its own
# pair rather than the live-play text above: none of the live legend's verbs work
# while scrubbing (there is no turn to end and no order to give —
# `input._handle_history_event` is modal and answers nothing else), and none of the controls that *do* work here are named there.
_LEGEND_HISTORY_TOUCH = """Reviewing turns already played — nothing here changes the game.

Drag the scrubber, or the ◀/▶ buttons, to step a turn. Play runs it back.
Graphs charts every seat's systems, ships, income and losses.

Drag the map to pan, pinch to zoom.

Rewind to here restarts play from the turn on screen. Exit returns to the live
board."""

_LEGEND_HISTORY_KEYS = """Reviewing turns already played — nothing here changes the game.

Drag the scrubber or press ←/→ to step a turn; Home/End jump to either end. P
plays it back.

Point at a system to see it as it was then; drag to pan, wheel to zoom. G
shows graphs of every seat's systems, ships, income and losses; Tab changes
graph, F makes it full screen.

Rewind to here restarts play from the turn on screen. Esc returns to the live
board."""


def _panel_route(surface, state: GameState, ui: Ui, x, y, bottom: int) -> int:
    """What route mode is holding, and what confirming it would do.

    Counts rather than a list of hops: the map already shows every one of them, and
    the numbers are the part that isn't obvious from looking — how many rules this
    replaces, and how much of the selection can't actually be served.
    """
    rally = ui.route_rally
    y = _head(surface, x, y, "Rally" if rally else "Route", config.COLOR_TEXT)
    font = _fonts()["small"]
    width = _panel_w()
    dest = ui.route_dest
    lines: list[tuple[str, tuple[int, int, int]]] = []
    if rally:
        n = len(ui.route_sel)
        lines.append((f"{n} rally point{'' if n == 1 else 's'}", config.COLOR_ROUTE))
        if not n:
            lines.append(("tap systems to gather at", config.COLOR_TEXT_DIM))
        else:
            lines.append((f"{len(ui.route_plan)} rules", config.COLOR_TEXT))
    else:
        lines.append((f"{len(ui.route_sources())} selected", config.COLOR_ROUTE))
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

    if rally:
        hint = ("Tap the systems ships should gather at — tap again to drop one. "
                "Everything else you hold forwards to the nearest of them. "
                "Auto-route picks your threatened systems.")
    else:
        hint = ("Drag a box to pick a group. Tap a system to aim at it — tap it again to "
                "un-aim, and to drop it from the group.")
    y += config.ROW_GAP * 2
    for line in _wrap(font, hint, width):
        if y + _row_h() > bottom:
            break
        y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
    return y


def _legend_text(history: bool, spectating: bool = False) -> str:
    """The legend for the scene and the input modality — the one place the
    variants are chosen between, so the test that measures whether a legend fits
    the panel measures the same string the panel draws."""
    if history:
        if config.compact:
            return _LEGEND_HISTORY_COMPACT_TOUCH
        return _LEGEND_HISTORY_TOUCH if config.touch_ui else _LEGEND_HISTORY_KEYS
    if spectating:
        return _LEGEND_SPECTATING_TOUCH if config.touch_ui else _LEGEND_SPECTATING_KEYS
    if config.compact:
        return _LEGEND_COMPACT_TOUCH
    return _LEGEND_TOUCH if config.touch_ui else _LEGEND_KEYS


def _panel_legend(surface, x, y, bottom: int, history: bool = False,
                  spectating: bool = False) -> int:
    """How to play, shown whenever no system is in focus. Reflowed to the panel's
    width and cut off at ``bottom`` rather than spilling under the End Turn button
    — at a big UI scale the panel simply hasn't room for every line.

    ``history`` swaps in the review scene's own controls. It is a parameter rather
    than a `Ui` read because this is the one panel drawer that needs nothing else
    off the state, and the test that measures the reflow has no `Ui` to hand.
    """
    y = _head(surface, x, y, "History" if history else "Star Conquest", config.COLOR_TEXT)
    font = _fonts()["small"]
    width = _panel_w()
    for line in _wrap(font, _legend_text(history, spectating), width):
        if y + _row_h() > bottom:  # a whole row, so the returned y clears it too
            break
        y = _row(surface, x, y, line, config.COLOR_TEXT_DIM)
    return y


def _stage_hint_lines(hint: str) -> list[str]:
    """The stage hint reflowed to the panel, the one place it is measured, so the
    test holding the panel's length counts what is drawn."""
    return _wrap(_fonts()["small"], hint, _panel_w())


def _panel_stage_hint(surface, ui: Ui, x, y, bottom: int) -> int:
    """A gauntlet stage's reminder, above the legend whenever nothing is in focus.

    No line of its own saying it can be tapped: on the touch build the panel has
    no row to spare once the legend is in (`test_a_stage_hint_fits_the_empty_panel`),
    so the briefing card says so instead. The block is the tap target that
    brings the card back (`ui.stage_hint_rect`; B does the same).
    """
    top = y
    for line in _stage_hint_lines(ui.stage_hint or ""):
        if y + _row_h() > bottom:
            break
        y = _row(surface, x, y, line, _BTN_TEAL[1])
    ui.stage_hint_rect = (x, top, _panel_w(), y - top)
    return y + config.ROW_GAP * 2


def _draw_briefing(surface, ui: Ui) -> None:
    """A gauntlet stage's briefing card, over the board on turn 0 until any press:
    the stage's title, which stage it is, then the briefing's paragraphs, and its
    target as a note, which the card always shows, so the target is never a
    surprise on the win overlay."""
    if config.touch_ui:
        # The hint sits in the side panel: under the map in portrait.
        where = "below the map" if config.portrait else "at the right"
        close = f"Tap anywhere to begin · tap the hint {where} to read this again"
    else:
        close = "Press any key to begin · B shows this again"
    notes = (f"Target: {ui.stage_target.text()}",) if ui.stage_target is not None else ()
    _draw_card(surface, ui.stage_title, ui.stage_label or "", _BTN_TEAL[1], ui.briefing, close, notes)


def _draw_lore(surface, state: GameState, ui: Ui) -> None:
    """A star's info card, over the board until any press: its name, the language
    the name comes from, where it comes from, and the catalogue's source for that."""
    name = state.systems[ui.lore_sid].name
    lore = starlore.LORE.get(name)
    if lore is None:
        return
    _draw_card(surface, name, lore.language, _BTN_BLUE[1], *_lore_card(lore))


def _lore_card(lore: starlore.Lore) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    """A star info card's paragraphs, close line and notes, as `_draw_card` takes them."""
    origin = lore.origin or "The IAU catalogue gives no origin for this name."
    notes = (f"Source: {lore.reference}",) if lore.reference else ()
    close = "Tap anywhere to close" if config.touch_ui else "Press any key to close"
    return (origin,), close, notes


class _CardLayout(NamedTuple):
    """How `_draw_card` sets out a card: the paragraphs' font and lines, the
    notes' and close line's lines, the card's size, and whether every paragraph
    line fits it."""
    kind: str
    lines: list[list[str]]
    tail: list[str]
    foot: list[str]
    width: int
    height: int
    fits: bool


def _card_area() -> tuple[int, int, int, int]:
    """Where a card is centred and must fit: over the map — or, on a compact
    screen, where the map is a fraction of a phone, the whole screen (the card
    is modal, and its veil covers the bars too)."""
    if config.compact:
        return (0, 0, config.SCREEN_W, config.SCREEN_H)
    return config.play_rect()


def _card_layout(paras, close: str, notes=()) -> _CardLayout:
    """Lay a card out on the map. Where the paragraphs would run off it the card
    widens to the map's width, then drops them to the small font; ``fits`` is
    False only if even that doesn't hold them."""
    widest = _card_area()[2] - 2 * config.s(16)
    lay = _card_at(paras, close, notes, "normal", min(widest, config.s(620)))
    for kind in ("normal", "small"):
        if lay.fits:
            break
        lay = _card_at(paras, close, notes, kind, widest)
    return lay


def _card_at(paras, close: str, notes, kind: str, width: int) -> _CardLayout:
    """`_card_layout` with the paragraphs in font ``kind`` on a card ``width`` wide."""
    small = _fonts()["small"]
    pad, gap = config.s(20), config.s(10)
    inner = width - 2 * pad
    foot = _wrap(small, close, inner)
    tail = [line for note in notes for line in _wrap(small, note, inner)]
    tail_h = len(tail) * _row_h() + gap if tail else 0
    lines = [_wrap(_fonts()[kind], text, inner) for text in paras]
    body_h = sum(len(p) * _row_h(kind) for p in lines) + gap * max(0, len(lines) - 1)
    need = pad + _row_h("big") + _row_h() + gap + body_h + gap + tail_h + len(foot) * _row_h() + pad
    most = _card_area()[3] - 2 * config.s(8)
    return _CardLayout(kind, lines, tail, foot, width, min(need, most), need <= most)


def _draw_card(surface, title: str, label: str, accent, paras, close: str, notes=()) -> None:
    """A card centred on the map over a veil: ``title`` with ``label`` under it in
    ``accent``, then ``paras`` reflowed to the card (`_card_layout`), then
    ``notes`` small and dim, and the ``close`` line at its foot.

    Measured, like the other overlays, so nothing is a fixed pixel size. A
    paragraph that would still run off the card's floor is cut there rather than
    drawn under the HUD; the notes and the close line always show.
    """
    px, py, pw, ph = _card_area()
    big, small = _fonts()["big"], _fonts()["small"]
    pad, gap = config.s(20), config.s(10)
    lay = _card_layout(paras, close, notes)
    card = pygame.Rect(px + (pw - lay.width) // 2, py + (ph - lay.height) // 2, lay.width, lay.height)
    foot_h = len(lay.foot) * _row_h()
    tail_h = len(lay.tail) * _row_h() + gap if lay.tail else 0

    veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 150))
    surface.blit(veil, (0, 0))
    pygame.draw.rect(surface, (16, 18, 28), card, border_radius=config.s(10))
    pygame.draw.rect(surface, accent, card, config.s(2), border_radius=config.s(10))

    x, y = card.x + pad, card.y + pad
    _text(surface, big, title, config.COLOR_TEXT, topleft=(x, y))
    y += _row_h("big")
    _text(surface, small, label, accent, topleft=(x, y))
    y += _row_h() + gap
    font = _fonts()[lay.kind]
    floor = card.bottom - pad - foot_h - tail_h - gap
    for para in lay.lines:
        for line in para:
            if y + _row_h(lay.kind) > floor:
                break
            _text(surface, font, line, config.COLOR_TEXT, topleft=(x, y))
            y += _row_h(lay.kind)
        y += gap
    y = card.bottom - pad - foot_h - tail_h
    for line in lay.tail:
        _text(surface, small, line, config.COLOR_TEXT_DIM, topleft=(x, y))
        y += _row_h()
    y = card.bottom - pad - foot_h
    for line in lay.foot:
        _text(surface, small, line, config.COLOR_TEXT_DIM, center=(card.centerx, y + _row_h() // 2))
        y += _row_h()


def _panel_lane_target(state: GameState, ui: Ui, focus: int):
    """Neighbour whose lane stats to show: the chosen dest, a hovered neighbour, or
    — failing both — the destination of a standing rule on the focused system.

    That last case is why a rule shows its lane at all: on the map the rule's own
    label sits on the lane it uses, so the panel is where you read that lane's
    length and travel time (a split shows its first lane). (A queued order reopened for editing takes the
    first case instead — the popup sets ``ui.dest`` to its destination.)
    """
    if ui.mode == CHOOSING and ui.dest is not None:
        return ui.dest
    if ui.selected is not None and ui.hover is not None and ui.hover != ui.selected and state.are_adjacent(ui.selected, ui.hover):
        return ui.hover
    rule = ui.auto_forward.get(focus)
    first = next(iter(rule.shares), None) if rule is not None else None
    if first is not None and first in state.systems:
        return first
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


def _draw_invite_overlay(surface, ui: Ui) -> None:
    """Play-by-post: the match we just created, one row per seat — including our
    own — and its own Copy button: 'a modal listing the seats with a Copy button
    each', replacing a status line over a clipboard blob nobody thought to check.

    Our own row is in it too, not just remembered in this browser's local
    storage: a reload, a cleared profile or opening on another device has
    nothing else to recover the seat from, so it needs to be copyable exactly
    like everyone else's.

    Shown exactly once (`ui.pbp_invite`, set only by the creation path); the
    clipboard write itself is main's job (`ui.pbp_copy_seat` names the row,
    `input` sets it and this only reads `pbp_invite_copied` back for the
    row's own feedback). Continue is the only way to dismiss it, and it never
    reappears once dismissed.
    """
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 190))
    surface.blit(veil, (0, 0))

    big, normal, small = _fonts()["big"], _fonts()["normal"], _fonts()["small"]
    title = "Match created — copy each seat's link"
    # The one and only time these links are ever shown: `main.pbp_open` mints
    # the tokens and hands them over right here, and nothing stores them again
    # (`pbp.create`'s own docstring — "the reply is the one and only time the
    # seat tokens exist in the clear"). A missed one has no second chance.
    subtitle = "Save these somewhere — they won't be shown again"
    copy_w = _btn_w(small, "Copy", config.s(64))
    copy_h = max(config.s(28), small.get_height() + config.s(10))
    sw = config.s(14)   # colour swatch side
    row_gap = config.s(8)
    row_h = max(normal.get_height(), copy_h) + config.s(4)

    def seat_label(seat: int) -> str:
        if seat == SPECTATE_ROW:
            return "Spectators"
        return f"{config.player_name(seat)} (you)" if seat == ui.human_id else config.player_name(seat)

    name_w = max((normal.size(seat_label(seat))[0] for seat, _ in ui.pbp_invite), default=0)

    pad = config.s(24)
    inner_w = sw + config.s(10) + name_w + config.s(28) + copy_w
    # The title can run longer than any row — measured and wrapped in the same
    # font it is drawn in (a mismatch here is exactly what let it overflow the
    # panel before), and capped so the panel never claims more width than the
    # window actually has to give it.
    cap = min(max(inner_w, config.s(280)), w - 2 * pad - config.s(48))
    title_lines = _wrap(big, title, cap)
    subtitle_lines = _wrap(small, subtitle, cap)
    title_w = max((big.size(line)[0] for line in title_lines), default=0)
    subtitle_w = max((small.size(line)[0] for line in subtitle_lines), default=0)
    pw = max(inner_w, title_w, subtitle_w) + 2 * pad
    done_w = _btn_w(normal, "Continue", config.s(140))
    done_h = copy_h + config.s(6)
    title_h = len(title_lines) * _row_h("big")
    subtitle_h = len(subtitle_lines) * _row_h("small")
    ph = (pad + title_h + config.s(6) + subtitle_h + config.s(18)
          + len(ui.pbp_invite) * (row_h + row_gap)
          + config.s(10) + done_h + pad)
    panel = pygame.Rect((w - pw) // 2, (h - ph) // 2, pw, ph)
    pygame.draw.rect(surface, (18, 20, 30), panel, border_radius=config.s(10))
    pygame.draw.rect(surface, (60, 66, 90), panel, config.s(2), border_radius=config.s(10))

    y = panel.y + pad
    for line in title_lines:
        _text(surface, big, line, config.COLOR_TEXT, center=(panel.centerx, y + big.get_height() // 2))
        y += _row_h("big")
    y += config.s(6)
    for line in subtitle_lines:
        _text(surface, small, line, _VERDICT_MISS, center=(panel.centerx, y + small.get_height() // 2))
        y += _row_h("small")
    y += config.s(18)

    ui.pbp_invite_rects = {}
    for seat, link in ui.pbp_invite:
        # The spectator row has no colour of its own: it is nobody's seat.
        color = config.COLOR_TEXT_DIM if seat == SPECTATE_ROW else config.player_color(seat)
        swatch = pygame.Rect(panel.x + pad, y + (row_h - sw) // 2, sw, sw)
        pygame.draw.rect(surface, color, swatch, border_radius=config.s(3))
        _text(surface, normal, seat_label(seat), color,
              midleft=(swatch.right + config.s(10), y + row_h // 2))
        r = pygame.Rect(panel.right - pad - copy_w, y + (row_h - copy_h) // 2, copy_w, copy_h)
        copied = ui.pbp_invite_copied == seat
        fill, edge = _BTN_ACTIVE if copied else _BTN_BLUE
        ui.pbp_invite_rects[seat] = _btn(surface, r, "Copied" if copied else "Copy",
                                         fill, edge, small)
        y += row_h + row_gap

    y += config.s(10)
    done = pygame.Rect(panel.centerx - done_w // 2, y, done_w, done_h)
    ui.pbp_invite_close_rect = _btn(surface, done, "Continue", *_BTN_GREEN, normal)


def _draw_waiting_overlay(surface, state: GameState, ui: Ui) -> None:
    """Play-by-post: our orders are in, and the turn is waiting on somebody else.

    A veil rather than a modal — the board stays readable underneath, because the
    position is exactly what you want to look at while you wait, and there is
    nothing here to answer. The whole stack is measured and then centred, in the
    same shape ``_draw_win_overlay`` uses, so a line that is not drawn tightens it
    up rather than leaving a hole.
    """
    w, h = surface.get_size()
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    # Lighter than the win overlay's: that one ends a game, this one is a pause in
    # the middle of it and the board underneath still has to be legible.
    veil.fill((5, 6, 12, 140))
    surface.blit(veil, (0, 0))

    # Centred on the *map*, not the window, and wrapped to it. The side panel is
    # still being drawn and still has to be readable, and at a large UI scale a
    # centred line long enough to say two players' names runs straight across it.
    px, py, pw, ph = config.play_rect()
    cx = px + pw // 2
    width = pw - config.s(48)

    big, normal, small = _fonts()["big"], _fonts()["normal"], _fonts()["small"]
    names = [_seat_name(state, seat) for seat in ui.pbp_waiting]
    if not names:
        headline, detail = "Resolving the turn...", ""
    elif len(names) == 1:
        headline, detail = "Waiting for " + names[0], "Your orders are in."
    else:
        headline = f"Waiting for {len(names)} players"
        detail = ", ".join(names)

    # Measured, then centred — a line that is not drawn tightens the stack up
    # rather than leaving a hole, and one too long for the map wraps rather than
    # spilling out of it.
    stack: list[tuple[pygame.font.Font, str, tuple[int, int, int]]] = []
    for font, line, color in ((big, headline, config.COLOR_TEXT),
                              (normal, detail, config.COLOR_TEXT_DIM),
                              (small, ui.pbp_msg, config.COLOR_TEXT_DIM)):
        if not line:
            continue
        stack.extend((font, part, color) for part in _wrap(font, line, width))

    total = sum(font.get_height() + config.s(6) for font, _, _ in stack)
    y = py + ph // 2 - total // 2
    for font, line, color in stack:
        _text(surface, font, line, color, center=(cx, y + font.get_height() // 2))
        y += font.get_height() + config.s(6)


def _draw_pbp_notice(surface, ui: Ui) -> None:
    """What the last call to a shared match said, once there is no veil to say it.

    A refused submission hands the board back, which drops the waiting overlay
    in the same frame — so the reason it was refused needs somewhere of its own,
    or the press reads as having done nothing at all. Top of the map, clear of
    the film caption at the bottom.
    """
    x, y, w, _ = config.play_rect()
    _label_pill(surface, _fonts()["small"], ui.pbp_msg, config.COLOR_TEXT,
                (x + w // 2, y + config.FILM_CAPTION_GAP))


def _draw_spectator_notice(surface, state: GameState, ui: Ui) -> None:
    """Who the live turn is waiting on, for a spectator's shared screen.

    The players' own veil (``_draw_waiting_overlay``) only goes up once their
    orders are in; a spectator has none to send, and what the room wants from
    the screen is which of them is holding the turn up. A pill rather than a
    veil, in the notice's place, so the board stays the thing on the wall.
    """
    names = [_seat_name(state, seat) for seat in ui.pbp_waiting]
    line = ("Waiting for " + ", ".join(names)) if names else "Resolving the turn..."
    x, y, w, _ = config.play_rect()
    _label_pill(surface, _fonts()["small"], line,
                config.COLOR_TEXT, (x + w // 2, y + config.FILM_CAPTION_GAP))


def _seat_name(state: GameState, seat: int) -> str:
    """What to call a seat we are waiting on. Its player name, which is already
    how every other readout names a side."""
    player = state.players.get(seat)
    return player.name if player is not None else f"Seat {seat}"


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
        msg = f"{_winner_label(state, ui)} wins!"
        color = config.player_color(state.winner)

    # Tappable buttons — touch equivalents of the T/R/M/H/Esc keys. Every box takes
    # the width of the widest label in the grid, so the rows stay square and no
    # label can spill into its neighbour once the font scales up.
    # In a gauntlet stage New map's place is Next stage's, or empty after the
    # last one: the stage's seed is pinned, so a new map would leave the stage.
    nxt = ui.offers_next_stage(state)
    second = _key_hint("Next stage", "N") if nxt else _key_hint("New map", "N")
    labels = [_key_hint("Retry", "T"), second, _key_hint("Setup menu", "M"), _key_hint("Review history", "H")]
    quit_label = _key_hint("Quit", "Esc")
    # A daily's result goes out as a line, not a link: everyone has its map.
    share_label = _key_hint("Copy daily result" if ui.daily_day else "Challenge a friend", "C")
    board_label = _key_hint("Enter on leaderboard", "L")
    share = ui.can_post(state)
    # The constant, not a resolved URL: a blank one disables the feature
    # everywhere (see `paths`), and resolving one costs a DOM read that this must
    # not be doing once a frame.
    board = share and bool(paths.LEADERBOARD_ORIGIN)
    bw = max(_btn_w(normal, s) for s in labels + [quit_label])
    bh = max(config.s(40), normal.get_height() + config.s(16))
    gap = config.s(12)
    pad = config.s(12)

    # A portrait phone is narrower than the headline and the score line: the
    # headline steps down a size, and the result lines wrap.
    room = w - 2 * config.HUD_PAD
    head_kind = "big" if big.size(msg)[0] <= room else "normal"
    result = [(kind, line, col) for kind, text, col in _result_lines(state, ui)
              for line in (_wrap(_fonts()[kind], text, room) or [""])]
    # the share pair side by side, or stacked where two won't fit across
    share_w = max(_btn_w(normal, share_label, bw), _btn_w(normal, board_label, bw))
    stack_share = board and 2 * share_w + gap > room
    rows = (4 + stack_share) if share else 3
    stack = (
        _row_h(head_kind)
        + sum(_row_h(kind) for kind, _t, _c in result)
        + pad
        + rows * (bh + gap)
        - gap
        + (_row_h() if (share and ui.share_msg) else 0)
    )
    y = (h - stack) // 2

    head_font = _fonts()[head_kind]
    _text(surface, head_font, msg, color, center=(w // 2, y + head_font.get_height() // 2))
    y += _row_h(head_kind)
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
    ui.restart_button_rect = (0, 0, 0, 0)
    if nxt:
        ui.retry_button_rect = _btn(surface, pygame.Rect(left, y, bw, bh), labels[0], *_BTN_AMBER)
        ui.next_stage_rect = _btn(surface, pygame.Rect(right, y, bw, bh), labels[1], *_BTN_TEAL)
    elif ui.in_stage:
        ui.retry_button_rect = _btn(surface, pygame.Rect(w // 2 - bw // 2, y, bw, bh), labels[0], *_BTN_AMBER)
    else:
        ui.retry_button_rect = _btn(surface, pygame.Rect(left, y, bw, bh), labels[0], *_BTN_AMBER)
        ui.restart_button_rect = _btn(surface, pygame.Rect(right, y, bw, bh), labels[1], *_BTN_GREEN)
    y += bh + gap
    ui.menu_button_rect = _btn(surface, pygame.Rect(left, y, bw, bh), labels[2], *_BTN_BLUE)
    ui.history_button_rect = _btn(surface, pygame.Rect(right, y, bw, bh), labels[3], *_BTN_VIOLET)
    y += bh + gap

    if share:
        # Both publish this same result, so they pair on one row and take a common
        # width from the wider label — the clipboard link for sending to one
        # person, the leaderboard for posting where anyone can see it.
        button_y = y
        if board and stack_share:
            sw = share_w
            ui.share_button_rect = _btn(surface, pygame.Rect(w // 2 - sw // 2, y, sw, bh), share_label, *_BTN_TEAL)
            y += bh + gap
            button_y = y
            ui.leaderboard_button_rect = _btn(surface, pygame.Rect(w // 2 - sw // 2, y, sw, bh), board_label, *_BTN_GOLD)
        elif board:
            sw = share_w
            ui.share_button_rect = _btn(surface, pygame.Rect(w // 2 - sw - gap // 2, y, sw, bh), share_label, *_BTN_TEAL)
            ui.leaderboard_button_rect = _btn(surface, pygame.Rect(w // 2 + gap // 2, y, sw, bh), board_label, *_BTN_GOLD)
        else:
            sw = _btn_w(normal, share_label, bw)
            ui.share_button_rect = _btn(surface, pygame.Rect(w // 2 - sw // 2, y, sw, bh), share_label, *_BTN_TEAL)
            ui.leaderboard_button_rect = (0, 0, 0, 0)
        y += bh + gap
        if ui.share_msg:
            _text(surface, _fonts()["small"], ui.share_msg, config.COLOR_TEXT_DIM, center=(w // 2, button_y + bh + _row_h() // 2))
            y += _row_h()
    else:
        ui.share_button_rect = (0, 0, 0, 0)
        ui.leaderboard_button_rect = (0, 0, 0, 0)

    ui.quit_button_rect = _btn(surface, pygame.Rect(w // 2 - bw // 2, y, bw, bh), quit_label, *_BTN_RED)


def _winner_label(state: GameState, ui: Ui) -> str:
    """"Verdant (Knower · Predict)" — the winning seat, and the bot that was
    driving it, as the top bar's tooltip and the graph legend name it
    (`Ui.seat_bots`). A seat left to random says so: "(Marshal, random)".

    Named only for a seat no person was playing: a human seat says who won and
    stops there, and a seat claimed part-way through (``engine.claim_seat``) is a
    human seat by then, so a game somebody took over is credited to them rather
    than to the strategy it opened under. The whole board is a bot's under
    autoplay, which is exactly when this is worth reading.

    Neutral is excluded explicitly. It is a real player (``id == 0``) carrying the
    default ``ai_strategy`` like any other, but nothing ever asks it to decide —
    and it reaches here on the one path that can name it, a mutual annihilation,
    where "Neutral (Heuristic)" would credit a bot that never played.

    A random seat is revealed here — which is the point, and costs nothing, since
    there is no turn left to play with the knowledge. A seat `Ui.seat_bots` has
    no label for falls back to ``Player.ai_strategy`` itself, capitalised rather
    than title-cased, so a drop-in keeps the shape its author gave its filename
    (``claudeBot`` stays ``ClaudeBot``).
    """
    name = config.player_name(state.winner)
    player = state.players.get(state.winner)
    if player is None or player.is_neutral or player.is_human or not player.ai_strategy:
        return name
    raw = player.ai_strategy
    bot = ui.seat_bots.get(state.winner) or f"{raw[:1].upper()}{raw[1:]}"
    if state.winner in ui.random_seats:
        bot += ", random"
    return f"{name} ({bot})"


def _result_lines(state: GameState, ui: Ui) -> list[tuple[str, str, tuple[int, int, int]]]:
    """(font kind, text, colour) for the score under the win message: turns first,
    ships lost as the tiebreak, then the verdict against any challenge.

    Only for the human's own result — there is nothing to boast about, or to
    compare against a challenge, in watching two bots fight. When the match came
    from a challenge link, say outright whether the target fell. Returned rather
    than drawn so the overlay can measure the block before placing it.

    A watched replay says whose result this is, because it is otherwise an overlay
    identical to our own win with the two sharing buttons missing from it — which
    reads as a broken feature rather than as the rule it is (``Ui.can_post``). It
    leads rather than trails: it qualifies the "X wins!" above it, and leaving the
    verdict last keeps it the line callers read off the end.
    """
    note = ([("small", "Someone else's game, replayed", config.COLOR_TEXT_DIM)]
            if ui.watched else [])
    # A spectator sits at neutral's id, which is also a mutual annihilation's
    # winner: no result here is theirs.
    if ui.spectating or state.winner != ui.human_id:
        if ui.challenge_target is not None:
            return note + [("normal", "Challenge failed", (214, 130, 110))]
        return note

    lost = state.players[ui.human_id].ships_lost
    line = f"Conquered in {state.turn} turns  ·  {lost} ships lost"
    if ui.hand_turns < state.turn:
        line += f"  ·  {ui.hand_turns} played by hand"
    lines = [("normal", line, config.COLOR_TEXT)]
    if ui.in_stage and ui.can_post(state):
        lines.append(_stage_verdict(state, ui))

    if ui.challenge_target is None:
        return note + lines
    # Same ordering the score uses: fewer turns wins, ties broken on losses, and
    # equal on both is a dead heat.
    target = ui.challenge_target
    mine = (state.turn, lost)
    who = f" {ui.challenge_by}'s" if ui.challenge_by else ""
    against = f"{target[0]} turns / {target[1]} lost"
    if mine < target:
        verdict, colour = f"Beat{who} {against}", _VERDICT_BEAT
    elif mine == target:
        verdict, colour = f"Matched{who} {against}", _VERDICT_TIE
    else:
        verdict, colour = f"Short of{who} {against}", _VERDICT_MISS
    lines.append(("small", verdict, colour))
    return note + lines


def _stage_verdict(state: GameState, ui: Ui) -> tuple[str, str, tuple[int, int, int]]:
    """The win overlay's stage line: cleared, or which way the target went. A
    win short of it says so, and says too when an earlier best already cleared
    the stage, since Next stage is then still offered."""
    cleared = "Stage cleared" if ui.has_next_stage else "Final stage cleared"
    target = ui.stage_target
    if target is None:
        return ("small", cleared, _BTN_TEAL[1])
    if ui.met_target(state):
        return ("small", f"{target.text()}: met  ·  {cleared}", _BTN_TEAL[1])
    if ui.stage_met_before:
        return ("small", f"{target.text()}: not this time  ·  your best clears it", _VERDICT_TIE)
    return ("small", f"{target.text()}: not yet", _VERDICT_MISS)


# Scrubber fill — a bright blue on the shared slider trough, echoing the play button.
_SCRUB_FILL = (110, 140, 200)


def _draw_scrubber(surface, state: GameState, ui: Ui) -> None:
    """Bottom-bar turn scrubber for history mode: an Exit button, a draggable
    track (0 .. ``ui.history_max`` turns) flanked by ◀/▶ step buttons, a turn/mode
    label, and a Rewind button (only when viewing a turn before the latest).
    Records hit-rects on ``ui`` for input, mirroring the store-rect-then-test
    handoff used across the HUD."""
    w, h = surface.get_size()
    _fx, by, _fw, fh = config.footer_rect(history=True)
    font = _fonts()["small"]
    bh = config.FOOTER_BTN_H
    gap = config.BTN_GAP
    # A portrait phone has two rows for it (the strip's and End Turn's): the
    # track and the turn on top, the buttons under them. Otherwise it is one row.
    two_rows = fh >= 2 * config.HUD_BOTTOM_H
    row = config.HUD_BOTTOM_H
    y = by + (row - bh) // 2 + (row if two_rows else 0)     # the buttons
    ty = by + (row - bh) // 2                                # the track
    cy = ty + bh // 2

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

    # Graphs toggle, lit while the overlay is open.
    graph_label = _key_hint("Graphs", "G")
    gb = pygame.Rect(pp.right + gap, y, _btn_w(font, graph_label), bh)
    if ui.history_chart is None:
        gb.w = 0
        ui.graph_button_rect = (0, 0, 0, 0)
    else:
        ui.graph_button_rect = _btn(surface, gb, graph_label,
                                    *(_BTN_ACTIVE if ui.history_graph else _BTN_VIOLET), font)

    # Rewind button (far right). Its footprint is reserved even when hidden so the
    # track width and label position stay fixed as you scrub — the button only
    # appears for turns before the latest (when there is something to leave behind).
    rewind_label = "Rewind" if two_rows else "Rewind to here"
    rww = _btn_w(font, rewind_label)
    rw = pygame.Rect(w - rww - config.HUD_PAD, y, rww, bh)
    right_limit = (w if two_rows else rw.x) - config.HUD_PAD
    # Never offered in a shared match: rewinding rebuilds a fresh, un-networked
    # `Ui` (`resume_game`), which would quietly fork the local view away from a
    # match the server and every other seat still think is at a later turn.
    if ui.history_turn >= ui.history_max or ui.in_pbp:
        ui.rewind_button_rect = (0, 0, 0, 0)
    else:
        ui.rewind_button_rect = _btn(surface, rw, rewind_label, *_BTN_AMBER, font)

    # Turn / fog-mode label, right-aligned just left of the rewind button. Its
    # slot is sized to the widest label this session could show (max digits, the
    # longer "revealed" tag) so a changing turn number never nudges the track.
    tag = "revealed" if ui.history_reveal else "as seen"
    label = f"Turn {state.turn}/{ui.history_max}  ·  {tag}"
    widest = f"Turn {ui.history_max}/{ui.history_max}  ·  revealed"
    if two_rows:
        label = f"{state.turn}/{ui.history_max} · {tag}"
        widest = f"{ui.history_max}/{ui.history_max} · revealed"
    label_x = right_limit - font.size(widest)[0]
    _text(surface, font, label, config.COLOR_TEXT_DIM, midright=(right_limit, cy))

    # Step-one-turn buttons flank the track — the mouse/touch equivalent of the
    # Left/Right arrow keys, so picking an exact turn doesn't rely on a fiddly drag
    # once a match has run long. Square, sized to the bar's own button height.
    left_end = config.HUD_PAD - gap if two_rows else (gb.right if gb.w else pp.right)
    prev = pygame.Rect(left_end + gap, ty, bh, bh)
    next_ = pygame.Rect(label_x - config.HUD_PAD - bh, ty, bh, bh)
    _draw_h_arrow_button(surface, prev, left=True, enabled=ui.history_turn > 0)
    _draw_h_arrow_button(surface, next_, left=False, enabled=ui.history_turn < ui.history_max)
    ui.history_prev_rect = (prev.x, prev.y, prev.w, prev.h)
    ui.history_next_rect = (next_.x, next_.y, next_.w, next_.h)

    # Track fills the space between the two step buttons.
    track_x = prev.right + gap
    track_w = max(1, next_.x - gap - track_x)
    track = pygame.Rect(track_x, ty, track_w, bh)
    ui.scrubber_rect = (track.x, track.y, track.w, track.h)
    t = 0.0 if ui.history_max <= 0 else ui.history_turn / ui.history_max
    _draw_slider(surface, track, t, _SCRUB_FILL, config.COLOR_TEXT)


# The graph overlay's palette: a dark panel over the map, faint gridlines.
_GRAPH_BG = (14, 16, 26)
_GRAPH_EDGE = (60, 66, 90)
_GRAPH_GRID = (40, 44, 60)
_GRAPH_CURSOR = (200, 206, 220)


def _nice_ceil(v: float) -> float:
    """``v`` rounded up to 1, 2 or 5 times a power of ten — an axis top a reader
    can divide by eye. Never below 1, so an all-zero series still has an axis."""
    if v <= 1:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 2, 5, 10):
        if v <= step * mag:
            return step * mag
    return 10 * mag


def _graph_value(metric: str, v: float) -> str:
    if math.isnan(v):
        return "–"   # a rival out of sight that turn (`stats.masked`)
    if metric == "income":
        return f"{v:.1f}"
    if metric in stats.AREA:
        return f"{v:.0f}%"
    return str(int(round(v)))


def _turn_ticks(turns: int, width: int, label_w: int) -> list[int]:
    """Turn numbers to label along the plot's foot: a 1/2/5 step chosen so the
    labels (each about ``label_w`` px) never touch, always including turn 0."""
    if turns <= 0:
        return [0]
    room = max(1, width // max(1, label_w * 2))
    step = max(1, int(_nice_ceil(turns / room)))
    return list(range(0, turns + 1, step))


def _draw_caret(surface, rect: pygame.Rect, up: bool) -> None:
    """A ^ or v chevron button, drawn rather than typed like the scrubber's
    arrows (`_draw_h_arrow_button`), so it reads the same in every font."""
    pygame.draw.rect(surface, _BTN_BLUE[0], rect, border_radius=config.s(6))
    pygame.draw.rect(surface, _BTN_BLUE[1], rect, config.s(2), border_radius=config.s(6))
    cx, cy = rect.center
    half = max(config.s(4), rect.w // 5)
    rise = half // 2 if up else -(half // 2)
    pts = [(cx - half, cy + rise), (cx, cy - rise), (cx + half, cy + rise)]
    pygame.draw.lines(surface, config.COLOR_TEXT, False, pts, config.s(2))


def _draw_stacked_area(surface, chart: stats.Chart, key: str, bands, plot: pygame.Rect,
                       top: float, color_of) -> None:
    """Bands stacked from the axis up in ``bands`` order, each the region between
    the running total before and after it, drawn as one polygon a band. Filled
    dimmed with a full-colour top edge, so neighbours stay distinct."""
    if chart.turns <= 0 or top <= 0:
        return

    def x_at(t: int) -> int:
        return plot.x + round(t / chart.turns * plot.w)

    def y_at(v: float) -> int:
        return plot.bottom - round(min(v, top) / top * plot.h)

    base = [0.0] * (chart.turns + 1)
    for pid in bands:
        series = chart.series(key, pid)
        if len(series) != len(base):
            continue
        upper = [b + stats.known_or_zero(v) for b, v in zip(base, series)]
        lower_pts = [(x_at(t), y_at(v)) for t, v in enumerate(base)]
        upper_pts = [(x_at(t), y_at(v)) for t, v in enumerate(upper)]
        color = color_of(pid)
        pygame.draw.polygon(surface, tuple(c * 3 // 5 for c in color), upper_pts + lower_pts[::-1])
        pygame.draw.lines(surface, color, False, upper_pts, config.s(1))
        base = upper


def _draw_stacked_bars(surface, chart: stats.Chart, key: str, traces, turn: int,
                       plot: pygame.Rect, top: float, color_of) -> None:
    """A bar per turn, centred where the line charts put that turn, stacked from
    the axis up in seat order. The viewed turn's bar is drawn at full colour and
    the rest dimmed, so the cursor reads on a chart with no curves to dot."""
    if chart.turns <= 0 or top <= 0:
        return
    step = plot.w / chart.turns
    bar_w = max(1, int(step * 0.8))
    for t in range(1, chart.turns + 1):
        cx = plot.x + round(t / chart.turns * plot.w)
        x0 = max(plot.x, cx - bar_w // 2)
        x1 = min(plot.right, x0 + bar_w)
        base = 0.0
        for pid in traces:
            v = chart.series(key, pid)[t]
            if math.isnan(v) or v <= 0:
                continue
            y0 = plot.bottom - round(min(base, top) / top * plot.h)
            base += v
            y1 = plot.bottom - round(min(base, top) / top * plot.h)
            color = color_of(pid)
            if t != turn:
                color = tuple(c * 3 // 5 for c in color)
            pygame.draw.rect(surface, color, (x0, y1, max(1, x1 - x0), max(1, y0 - y1)))


def _draw_known_line(surface, color, series, at, width: int) -> None:
    """A seat's curve over the turns it was known (`stats.masked`): solid
    along each run of known turns, dotted straight across a gap between two
    (what it did out of sight is a guess, and looks like one), and a lone known
    turn a dot."""
    runs: list[list[tuple[int, int]]] = []
    prev = None
    for t, v in enumerate(series):
        if math.isnan(v):
            prev = None
            continue
        p = at(t, v)
        if prev is None:
            runs.append([])
        runs[-1].append(p)
        prev = p
    for run in runs:
        if len(run) >= 2:
            pygame.draw.lines(surface, color, False, run, width)
        else:
            pygame.draw.circle(surface, color, run[0], width)
    for before, after in zip(runs, runs[1:]):
        _draw_dotted(surface, color, before[-1], after[0], max(1, width // 2))


def _draw_dotted(surface, color, a, b, r: int) -> None:
    """Dots of radius ``r`` from ``a`` to ``b``, spaced a few radii apart."""
    gap = max(3, r * 4)
    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // gap))
    for i in range(n + 1):
        f = i / n
        pygame.draw.circle(surface, color, (round(a[0] + (b[0] - a[0]) * f), round(a[1] + (b[1] - a[1]) * f)), r)


def _draw_history_graph(surface, state: GameState, ui: Ui) -> pygame.Rect | None:
    """The end-of-game graphs: one metric (`stats.METRICS`) per tab, a line per
    seat across every recorded turn (a stacked bar per turn for
    `stats.STACKED`, stacked 0-100% bands for `stats.AREA`), and a cursor on
    the turn the map shows.

    Docked over the lower part of the map, so the replay plays on above it — the
    Civilization-style map-and-graphs pairing — or, with ``ui.graph_full`` (the
    ^/v caret, F), filling everything between the bars. The scrubber stays
    either way. The legend doubles as the trace selector: a click on a seat's
    row shows or hides its line, and the y scale fits the lines shown. It sits
    right of the plot on a landscape screen; on a portrait one, where a column
    beside the plot would leave it half the width and still clip the bot names,
    it runs under the plot, full width, and a docked panel grows to hold it.
    Records every hit-rect on ``ui``; input maps a click or drag across the plot
    to a turn. Returns the panel's rect, or None with no chart to draw.
    """
    chart = ui.history_chart
    if chart is None:
        return None
    seats = ui.graph_seats()
    traces = ui.graph_traces()
    key, _title, unit = stats.METRICS[ui.graph_metric % len(stats.METRICS)]
    font = _fonts()["small"]
    pad = config.s(10)
    w, h = surface.get_size()
    turn = max(0, min(ui.history_turn, chart.turns))

    def value_at(pid: int) -> float:
        series = chart.series(key, pid)
        return series[min(turn, len(series) - 1)] if series else 0.0

    def color_of(pid: int):
        return state.players[pid].color if pid in state.players else config.player_color(pid)

    # The legend's measures come first: below the plot, they decide the panel's height.
    rows = [(pid, state.players[pid].name if pid in state.players else config.player_name(pid),
             _graph_value(key, value_at(pid)))
            for pid in seats]
    # who played each seat, dim after its name — where the row has room for it
    who = {pid: f" · {_seat_who(state, ui, pid)}" for pid in seats}
    sw = config.s(12)
    # Sized for every tab's widest figure, so switching metric never moves the plot.
    val_w = max([font.size(_graph_value(k, chart.peak(k, seats)))[0] for k, _t, _u in stats.METRICS]
                + [font.size(u)[0] for _k, _t, u in stats.METRICS])
    bare_w = max([font.size(n)[0] for _p, n, _v in rows] + [font.size("Unclaimed")[0]])
    name_w = max([font.size(n + who[p])[0] for p, n, _v in rows] + [bare_w])
    chrome = sw + config.s(6) + config.s(12) + val_w   # swatch, gaps, value column
    row_h = _tap_size(_row_h())
    col_gap = config.s(24)
    # the unclaimed band's row, always counted so a tab change never resizes the panel
    items = len(rows) + 1

    vx, vy, vw, vh = config.play_rect()
    tab_h = _tap_size(font.get_height() + config.s(10))
    head_h = _tap_size(font.get_height() + config.s(8))   # unit label, Show all
    if ui.graph_full:
        box = pygame.Rect(0, config.HUD_TOP_H, w, h - config.HUD_TOP_H - config.HUD_BOTTOM_H)
        below = box.h > box.w
    else:
        below = vh > vw
        box = pygame.Rect(vx + pad, 0, vw - 2 * pad, 0)
    inner_w = box.w - 2 * pad
    # Tabs flow left to right, wrapping short of the caret (a narrow portrait
    # panel can't fit all six on one line).
    tab_gap = config.s(6)
    tab_room = inner_w - tab_h - pad
    tabs, tx, line = [], 0, 0
    for _k, label, _u in stats.METRICS:
        tw = _btn_w(font, label)
        if tx and tx + tw > tab_room:
            tx, line = 0, line + 1
        tabs.append((tx, line, tw, label))
        tx += tw + tab_gap
    tabs_h = (line + 1) * tab_h + line * tab_gap
    # A phone's panel is short as well as narrow: columns sized to the bare names
    # (a row drops its bot name when its cell is too narrow) and this tab's own
    # figures leave the plot room, at the price of the legend moving on a tab
    # change.
    col_chrome, col_name_w = chrome, name_w
    if config.compact:
        col_name_w = bare_w
        col_chrome = chrome - val_w + max(font.size(_graph_value(key, chart.peak(key, seats)))[0],
                                          font.size(unit)[0])
    cols = max(1, min(items, (inner_w + col_gap) // (col_chrome + col_name_w + col_gap))) if below else 1
    legend_h = head_h + -(-items // cols) * row_h if below else 0
    if not ui.graph_full:
        box_h = max(config.s(180), min(int(vh * 0.42), config.s(320))) + legend_h + tabs_h - tab_h
        box_h = min(box_h, int(vh * 0.8))
        box.y, box.h = vy + vh - box_h - pad, box_h
        pygame.draw.rect(surface, _GRAPH_BG, box, border_radius=config.s(8))
        pygame.draw.rect(surface, _GRAPH_EDGE, box, config.s(1), border_radius=config.s(8))
    else:
        pygame.draw.rect(surface, _GRAPH_BG, box)

    # Tabs along the top, the caret at the far right.
    ty = box.y + pad
    rects = []
    for i, (x, line, tw, label) in enumerate(tabs):
        r = pygame.Rect(box.x + pad + x, ty + line * (tab_h + tab_gap), tw, tab_h)
        on = i == ui.graph_metric % len(stats.METRICS)
        rects.append(_btn(surface, r, label, *(_BTN_ACTIVE if on else _BTN_BLUE), font))
    ui.graph_tab_rects = rects
    caret = pygame.Rect(box.right - pad - tab_h, ty, tab_h, tab_h)
    _draw_caret(surface, caret, up=not ui.graph_full)
    ui.graph_full_rect = (caret.x, caret.y, caret.w, caret.h)
    note = ("" if ui.history_reveal else "Rivals only while in sight")
    if not note and not config.touch_ui:
        note = "Tab: next graph  ·  1-9 or click a seat: show/hide  ·  F: " + (
            "dock" if ui.graph_full else "full screen")
    if note and line == 0 and box.x + pad + tx + font.size(note)[0] <= caret.x - pad:
        _text(surface, font, note, config.COLOR_TEXT_DIM, midright=(caret.x - pad, ty + tab_h // 2))

    body_top = ty + tabs_h + pad
    label_room = font.get_height() + config.s(4)   # the turn numbers under the plot
    if below:
        # under the plot: a header line (Show all, the unit over the values),
        # then the rows in as many columns as fit a full one side by side
        legend_top = max(body_top + config.s(60), box.bottom - pad - legend_h)
        body_bottom = legend_top - config.s(6) - label_room
        cell_w = (inner_w - (cols - 1) * col_gap) // cols
        cells = [pygame.Rect(box.x + pad + (i % cols) * (cell_w + col_gap),
                             legend_top + head_h + (i // cols) * row_h, cell_w, row_h)
                 for i in range(items)]
        unit_xy = (box.right - pad - font.size(unit)[0], legend_top + (head_h - font.get_height()) // 2)
        all_at = (box.x + pad, legend_top + (head_h - _tap_size(font.get_height() + config.s(8))) // 2)
        plot_right = box.right - pad
        floor = box.bottom
    else:
        body_bottom = box.bottom - pad - label_room
        # beside the plot: as wide as its names, bot names included, up to 40% of
        # the panel; past that the bot names drop, and so does their room
        legend_w = chrome + name_w
        if legend_w > int(box.w * 0.4):
            legend_w = min(int(box.w * 0.4), chrome + bare_w)
        lx = box.right - pad - legend_w
        cells = [pygame.Rect(lx, body_top + _row_h() + i * row_h, legend_w, row_h) for i in range(items + 1)]
        unit_xy = (box.right - pad - font.size(unit)[0], body_top)
        all_at = None   # after the last row drawn
        plot_right = lx - config.s(16)
        floor = body_bottom + font.get_height()

    # Each row: swatch, seat name, value at the viewed turn — and the trace
    # selector: a hidden seat's row keeps its place, its swatch hollow.
    _text(surface, font, unit, config.COLOR_TEXT_DIM, topleft=unit_xy)
    trace_rects = []
    shown_rows = 0
    for (pid, name, val), cell in zip(rows, cells):
        if cell.bottom > floor:
            break
        shown = pid in traces
        cy = cell.centery
        swatch = pygame.Rect(cell.x, cy - sw // 2, sw, sw)
        if shown:
            pygame.draw.rect(surface, color_of(pid), swatch, border_radius=config.s(2))
        else:
            pygame.draw.rect(surface, color_of(pid), swatch, config.s(1), border_radius=config.s(2))
        ink = config.COLOR_TEXT if shown else config.COLOR_TEXT_DIM
        drawn = _text(surface, font, name, ink, midleft=(cell.x + sw + config.s(6), cy))
        if font.size(name + who[pid])[0] <= cell.w - chrome:
            _text(surface, font, who[pid], config.COLOR_TEXT_DIM, midleft=(drawn.right, cy))
        _text(surface, font, val, ink, midright=(cell.right, cy))
        trace_rects.append((pid, (cell.x, cell.y, cell.w, cell.h)))
        shown_rows += 1
    ui.graph_trace_rects = trace_rects
    spare = cells[shown_rows:]
    if key in stats.AREA and spare and spare[0].bottom <= floor:
        # the unclaimed band's row: always drawn, so not a trace to toggle
        cell = spare.pop(0)
        cy = cell.centery
        pygame.draw.rect(surface, config.COLOR_NEUTRAL, (cell.x, cy - sw // 2, sw, sw), border_radius=config.s(2))
        _text(surface, font, "Unclaimed", config.COLOR_TEXT_DIM, midleft=(cell.x + sw + config.s(6), cy))
        val = _graph_value(key, value_at(stats.NEUTRAL))
        _text(surface, font, val, config.COLOR_TEXT_DIM, midright=(cell.right, cy))
    ui.graph_all_rect = (0, 0, 0, 0)
    if all_at is None and spare and spare[0].bottom <= floor:
        all_at = (spare[0].x, spare[0].y + config.s(4))
    if len(traces) < len(seats) and all_at is not None:
        r = pygame.Rect(*all_at, _btn_w(font, "Show all"), _tap_size(font.get_height() + config.s(8)))
        ui.graph_all_rect = _btn(surface, r, "Show all", *_BTN_BLUE, font)

    # The plot, left of or above the legend, its scale fitted to the lines shown.
    stacked = key in stats.STACKED
    area = key in stats.AREA
    peak = chart.stack_peak(key, traces) if stacked else chart.peak(key, traces)
    # a whole-number metric never gets a fractional gridline: its axis starts at 5;
    # a share always spans the whole map
    top = 100.0 if area else _nice_ceil(peak if key == "income" else max(peak, 5))
    # 1x and 5x tops split into fifths, 2x into quarters: every line a round figure
    parts = 4 if f"{top:g}".lstrip("0.")[:1] == "2" else 5
    labels = [_graph_value(key, top * i / parts) for i in range(parts + 1)]
    axis_w = max(font.size(t)[0] for t in labels) + config.s(6)
    plot = pygame.Rect(box.x + pad + axis_w, body_top + font.get_height() // 2,
                       max(1, plot_right - (box.x + pad + axis_w)),
                       max(1, body_bottom - body_top - font.get_height() // 2))
    ui.graph_plot_rect = (plot.x, plot.y, plot.w, plot.h)
    for i, label in enumerate(labels):
        gy = plot.bottom - round(plot.h * i / parts)
        pygame.draw.line(surface, _GRAPH_GRID, (plot.x, gy), (plot.right, gy), config.s(1))
        _text(surface, font, label, config.COLOR_TEXT_DIM, midright=(plot.x - config.s(6), gy))

    def at(t: int, v: float) -> tuple[int, int]:
        fx = 0.0 if chart.turns <= 0 else t / chart.turns
        return (plot.x + round(fx * plot.w), plot.bottom - round(min(v, top) / top * plot.h))

    label_y = plot.bottom + config.s(4)
    widest = font.size(f"Turn {chart.turns}")[0]
    for t in _turn_ticks(chart.turns, plot.w, widest):
        tick = "Turn 0" if t == 0 else str(t)
        x = at(t, 0)[0]
        pygame.draw.line(surface, _GRAPH_EDGE, (x, plot.bottom), (x, plot.bottom + config.s(3)), config.s(1))
        tw = font.size(tick)[0]
        _text(surface, font, tick, config.COLOR_TEXT_DIM,
              topleft=(min(max(plot.x, x - tw // 2), plot.right - tw), label_y))

    if area:
        # unclaimed on top, known on the turns some of it was in sight, as a
        # rival is (`stats.sightings`)
        bands = list(traces) + [stats.NEUTRAL]
        _draw_stacked_area(surface, chart, key, bands, plot, top,
                           lambda pid: config.COLOR_NEUTRAL if pid == stats.NEUTRAL else color_of(pid))
    if stacked:
        _draw_stacked_bars(surface, chart, key, traces, turn, plot, top, color_of)
    if area or stacked:
        cx = at(turn, 0)[0]
        pygame.draw.line(surface, _GRAPH_CURSOR, (cx, plot.y), (cx, plot.bottom), config.s(1))
        return box

    # Ours last, and thicker, so it is never buried under a rival's curve.
    order = sorted(traces, key=lambda pid: pid == ui.human_id)
    for pid in order:
        width = config.s(3) if pid == ui.human_id else config.s(2)
        _draw_known_line(surface, color_of(pid), chart.series(key, pid), at, width)

    # The cursor: the turn on the map, with a dot where each curve crosses it.
    cx = at(turn, 0)[0]
    pygame.draw.line(surface, _GRAPH_CURSOR, (cx, plot.y), (cx, plot.bottom), config.s(1))
    for pid in order:
        series = chart.series(key, pid)
        if turn < len(series) and not math.isnan(series[turn]):
            pygame.draw.circle(surface, color_of(pid), at(turn, series[turn]), config.s(4))
    return box


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
