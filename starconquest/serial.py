"""JSON for the HTML frontend: the map, the board, fog, turn events and films.

Plain dicts and lists of ints, floats and strings, nothing the page has to
interpret beyond drawing it (``docs/design/html-frontend.md``). Rules stay in
Python: an event carries its derived figures (``losses``, ``victor``, ``cost``)
so the page does no arithmetic on a fight, and a film carries its schedule.

Two shapes are load-bearing for the page's `Reel` mirror:

* ``board["fleets"]`` keeps ``state.fleets`` order, because an event names a
  fleet by its position in the board the film starts from (`turnfilm.Reel`);
* an event's fields are the dataclass's own names, so the mirror reads
  ``ev.source_ships`` exactly where `Reel._apply` reads ``event.source_ships``.
"""

from __future__ import annotations

import dataclasses
import math

from starconquest import app, combat, hud, mapgen, paths, starlore, stats, turnfilm
from starconquest.model import GameState, lane_key
from starconquest.viewstate import CHOOSING, ROUTING, SPECTATE_ROW, Ui

# Derived figures an event's class computes, sent alongside its fields.
_DERIVED = {
    "Clashed": ("destroyed", "cost", "victor", "losses"),
    "Landed": ("sides", "destroyed", "cost", "victor", "losses"),
    "Produced": ("hulls",),
}


def _plain(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def static(state: GameState) -> dict:
    """What does not change during a match: systems, lanes, seats, the world box."""
    x0, y0, x1, y1 = mapgen.map_bounds(state)
    return {
        "world": [x0, y0, x1, y1],
        "systems": [{"id": s.id, "x": s.pos[0], "y": s.pos[1], "prod": s.production,
                     "name": s.name, "nbrs": sorted(s.neighbors)}
                    for _, s in sorted(state.systems.items())],
        "lanes": sorted([min(lane.a, lane.b), max(lane.a, lane.b), round(lane.length_ly, 3)]
                        for lane in state.lanes.values()),
        "players": [{"id": p.id, "name": p.name, "color": list(p.color), "neutral": p.is_neutral,
                     "human": p.is_human, "bot": p.ai_strategy}
                    for _, p in sorted(state.players.items())],
    }


def board(state: GameState) -> dict:
    """What moves each turn. ``fleets`` keeps the board's own order (see above).

    A fleet's ``turns_total`` is its schedule, kept rather than asked of
    ``state.travel_turns``: ship speed can grow, so two fleets on one lane can
    be on different schedules.
    """
    return {
        "turn": state.turn,
        "winner": state.winner,
        "sys": [[sid, s.owner_id, s.ships, s.prod_progress] for sid, s in sorted(state.systems.items())],
        "fleets": [[f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_total, f.turns_remaining,
                    f.lane_slot] for f in state.fleets],
        "players": [[pid, p.alive, p.ships_lost] for pid, p in sorted(state.players.items())],
    }


def travel(state: GameState) -> list[int]:
    """Each lane's travel time if launched now, in ``static``'s lane order.

    Sent with every board rather than in ``static``: with ship-speed growth on
    it shortens as the game runs (`GameState.travel_turns`).
    """
    pairs = sorted((min(lane.a, lane.b), max(lane.a, lane.b)) for lane in state.lanes.values())
    return [state.travel_turns(a, b) for a, b in pairs]


def fog(visible, seen, intel) -> dict:
    """One seat's fog: what it sees now, what it has ever seen, and its last
    look at each system it no longer sees (``Ui.player_intel``)."""
    return {"visible": sorted(visible), "seen": sorted(seen),
            "intel": {str(k): _plain(v) for k, v in sorted(intel.items())}}


def event(ev: turnfilm.Event) -> dict:
    """One turn event: its kind, its fields, and the figures its class derives."""
    kind = type(ev).__name__
    out = {"k": kind, **_plain(ev)}
    for name in _DERIVED.get(kind, ()):
        out[name] = _plain(getattr(ev, name))
    return out


def film(f: turnfilm.Film) -> dict:
    """A film's schedule: its beats, and each cue's time and event."""
    return {
        "beats": [[b.kind, b.label, b.start, b.ms] for b in f.beats],
        "cues": [[ms, event(ev)] for ms, ev in f.cues],
        "total": f.total_ms,
    }


def view(state: GameState, ui: Ui) -> dict:
    """Everything the play screen draws from `Ui`: the selection and the popup,
    the orders queued, what each of our systems can still send, fog, the side
    panel and scoreboard (worded by `hud`), and which controls are live."""
    me = state.players.get(ui.human_id)
    return {
        "human": ui.human_id,
        "mode": ui.mode,
        "selected": ui.selected,
        "dest": ui.dest,
        "hover": ui.hover,
        "source": ui.source(state),
        "sel_order": ui.sel_order,
        "sel_forward": ui.sel_forward,
        "pending": [[o.source_id, o.dest_id, o.ships] for o in ui.pending],
        "rules": rules(state, ui),
        "editing_rule": ui.selected if ui.mode == CHOOSING and ui.forward_armed else ui.sel_forward,
        "available": {str(sid): ui.available(state, sid) for sid, s in sorted(state.systems.items())
                      if s.owner_id == ui.human_id},
        # What the queue already promises out of each of our systems: the board
        # shows a system's ships less this (`render._draw_systems`), from the
        # board it is drawing, so a film's counts are the film's own.
        "committed": {str(sid): n for sid in sorted(state.systems) if (n := ui.committed(sid))},
        "visible": sorted(ui.visible),
        "seen": sorted(ui.seen),
        "snap": ui.snap_points(state),
        "popup": popup(state, ui) if ui.mode == CHOOSING else None,
        "panel": panel(state, ui),
        "notes": [list(n) for n in hud.match_notes(ui)],
        "pbp": pbp(state, ui),
        "scores": scores(state, ui),
        "footer": [list(b) for b in hud.footer(state, ui)],
        "more_open": ui.more_open,
        "more_labels": {attr: list(pair) for attr, pair in hud.MORE_LABELS.items()},
        "lost": me.ships_lost if me is not None and not ui.spectating else None,
        "end": list(hud.end_turn_button(state, ui)),
        "autoplay": ui.autoplay,
        "playing": ui.playing,
        "fast_forward": ui.fast_forward,
        "step_ms": app.step_delay(ui, app.PLAY_MS),
        "over": over(state, ui) if state.winner is not None and not ui.history else None,
        "route": route(state, ui) if ui.mode == ROUTING else None,
        "hint": hint(state, ui) if ui.hint_live(state) else None,
        "card": card(state, ui),
        "history": {"turn": ui.history_turn, "max": ui.history_max, "reveal": ui.history_reveal,
                    "label": hud.scrubber_label(ui, state.turn),
                    "label_short": hud.scrubber_label(ui, state.turn, short=True),
                    "graphs": ui.history_chart is not None,
                    "graph_on": ui.history_graph,
                    "rewind": ui.history_turn < ui.history_max and not ui.in_pbp} if ui.history else None,
        "graph": graph(state, ui) if ui.history and ui.history_graph and ui.history_chart is not None else None,
        "can": {
            "end_turn": (state.winner is None and not ui.awaiting_others(state) and not ui.spectating
                         and not ui.history),
            "undo": ui.can_undo(state),
            "retry": ui.can_retry,
            "hint": ui.can_hint(state),
            "route": ui.can_route(state),
            "fast_forward": ui.can_fast_forward(state),
            "review": ui.can_review(state),
        },
    }


def pbp(state: GameState, ui: Ui) -> dict | None:
    """A shared match: the side panel's head (`hud.you_are`), the word where
    End Turn stands once there is no turn of ours to end, and the overlay on
    the map, in `render`'s order under the win veil, a briefing and the cards:
    the invite (each seat's link to copy), the waiting veil, the last call's
    line, or a spectator's line."""
    if not ui.in_pbp and not ui.pbp_invite:
        return None
    (text, role), label = hud.you_are(ui)
    overlay = None
    covered = (state.winner is not None or ui.history or (ui.briefing_open and ui.in_stage)
               or ui.lore_sid in state.systems or ui.odds_card_up())
    if covered:
        pass
    elif ui.pbp_invite:
        overlay = {"kind": "invite", "title": hud.INVITE_TITLE, "subtitle": hud.INVITE_SUBTITLE,
                   "rows": [[seat, hud.invite_label(ui, seat), "dim" if seat == SPECTATE_ROW else seat,
                             hud.invite_copy(ui, seat), ui.pbp_invite_copied == seat]
                            for seat, _link in ui.pbp_invite],
                   "done": hud.INVITE_DONE}
    elif ui.awaiting_others(state):
        headline, detail = hud.waiting_lines(state, ui)
        overlay = {"kind": "waiting", "headline": headline, "detail": detail, "msg": ui.pbp_msg}
    elif ui.in_pbp and ui.pbp_msg:
        overlay = {"kind": "notice", "text": ui.pbp_msg}
    elif ui.spectating:
        overlay = {"kind": "notice", "text": hud.spectator_line(state, ui)}
    return {"you": [text, role] if ui.in_pbp else None, "label": label if ui.in_pbp else "",
            "end_word": hud.end_turn_word(state, ui), "overlay": overlay}


def popup(state: GameState, ui: Ui) -> dict | None:
    """The send popup's content: the lane, the count or share being edited and
    its slider, the presets, the forecast, and the discard button's word."""
    if ui.selected not in state.systems or ui.dest not in state.systems:
        return None
    src, dest = state.systems[ui.selected], state.systems[ui.dest]
    rule = ui.edited_rule()
    lane = state.lanes.get(lane_key(src.id, dest.id))
    seen = ui.sees(dest.id)
    line = None if ui.forward_armed else hud.forecast(state, ui)
    lo, hi, value = ui.slider_range(state)
    hold = split = None
    if rule is not None:
        hold = {"value": ui.hold_of(state, src.id), "smart": rule.smart}
    if rule is not None and len(rule.shares) > 1:
        split = {"lanes": [[d, pct, ui.lane_is_hostile(state, d)] for d, pct in rule.shares.items()
                           if d in state.systems],
                 "stays": max(0, 100 - rule.total()), "even": hud.even_split(ui)}
    return {
        "src": src.id,
        "dest": dest.id,
        # the title's wordings, longest first (`render._fit_route`); a split's is its source's
        "route": [f"{src.label} -> {dest.label}", f"{src.short} -> {dest.short}", f"{src.id} -> {dest.id}"],
        "splits": [f"{src.label} splits", f"{src.short} splits", f"{src.id} splits"],
        "forward": ui.forward_armed,
        "editing": ui.editing_existing,
        "garrison": src.ships,
        "dest_ships": dest.ships if seen else None,
        "dest_owner": dest.owner_id if seen else None,
        "lane": [lane.length_ly, state.travel_turns(src.id, dest.id)] if lane is not None else None,
        "effect": hud.popup_effect(state, ui),
        "forecast": {"forms": line[0], "tone": line[1], "odds": seen} if line is not None else None,
        "value": f"send {ui.edited_share()}%" if ui.forward_armed else str(ui.chosen),
        "slider": [lo, hi, value],
        "presets": [list(p) for p in hud.popup_presets(state, ui)],
        "discard": hud.popup_discard(ui),
        "hold": hold,
        "split": split,
    }


def panel(state: GameState, ui: Ui) -> dict:
    """The side panel: the system in focus and the lane it is looking down, or
    the legend's scene when nothing is in focus."""
    editing_rule = ui.sel_forward if ui.sel_forward in ui.auto_forward else None
    focus = editing_rule if editing_rule is not None else (ui.selected if ui.selected is not None else ui.hover)
    clear = {"all": None, "dangerous": None}
    if ui.auto_forward and not ui.history:
        dangerous = sum(1 for sid in ui.auto_forward if ui.rule_is_hostile(state, sid))
        clear = {"all": hud.clear_forward_label(ui),
                 "dangerous": hud.clear_dangerous_label(dangerous) if dangerous else None}
    if focus not in state.systems:
        return {"focus": None, "legend": "history" if ui.history else "spectating" if ui.spectating else "live",
                "clear": clear, "portrait": hud.portrait_hint(ui),
                "stage_hint": ui.stage_hint if ui.stage_hint and not ui.history else None}
    sys = state.systems[focus]
    dest = hud.lane_target(state, ui, focus)
    ruled = focus in ui.auto_forward
    return {
        "focus": focus,
        "clear": clear,
        "rule": [list(r) for r in hud.rule_rows(state, ui, focus, False)] if ruled else [],
        "rule_touch": [list(r) for r in hud.rule_rows(state, ui, focus, True)] if ruled else [],
        "system": [list(r) for r in hud.system_rows(state, ui, sys)],
        "compact": [list(r) for r in hud.system_rows_compact(state, ui, sys)],
        "lane": [list(r) for r in hud.lane_rows(state, ui, focus, dest)] if dest is not None else [],
        "lore": focus == ui.selected and sys.name in starlore.LORE and not ui.history,
    }


def over(state: GameState, ui: Ui) -> dict:
    """The game-over veil: the headline, the result lines, and which buttons it
    offers (`render._draw_win_overlay`)."""
    text, role = hud.headline(state, ui)
    share = ui.can_post(state)
    return {
        "headline": [text, role],
        "lines": [list(line) for line in hud.result_lines(state, ui)],
        "next_stage": ui.offers_next_stage(state),
        "in_stage": ui.in_stage,
        "share": list(hud.share_label(ui)) if share else None,
        "board": list(hud.board_label(ui)) if share and paths.LEADERBOARD_ORIGIN else None,
        "notes": ui.share_msg if share else "",
    }


def route(state: GameState, ui: Ui) -> dict:
    """Route mode's proposal: the picks, the sinks, the rules it would lay and
    the ones it would replace or clear, the smart holds it would arm (each with
    what it keeps), what can't be served or would loop, and the
    panel's words and the confirm button's label."""
    def lanes(plan) -> list[dict]:
        return [{"src": src, "lanes": [[d, pct] for d, pct in rule.shares.items() if d in state.systems]}
                for src, rule in plan.items() if src in state.systems]
    label, live = hud.route_confirm(ui)
    return {
        "rally": ui.route_rally,
        "sel": sorted(ui.route_sel),
        "dest": ui.route_dest,
        "plan": lanes(ui.route_plan),
        "replaces": lanes({src: ui.auto_forward[src] for src in ui.route_replaces | ui.route_clears
                           if src in ui.auto_forward}),
        "smart": [[sid, combat.smart_hold(state, sid, ui.human_id)] for sid in sorted(ui.route_newly_smart())
                  if sid in state.systems],
        "unroutable": sorted(ui.route_unroutable),
        "cycles": sorted(ui.route_cycles),
        "title": "Rally" if ui.route_rally else "Route",
        "lines": [list(line) for line in hud.route_lines(state, ui)],
        "hint": hud.route_hint(ui),
        "confirm": [label, live],
    }


def hint(state: GameState, ui: Ui) -> dict:
    """The advisor's sends our orders don't already make, each with ``plus``
    where we already send some down that lane, and the caption."""
    planned = ui.planned_sends(state)
    return {"sends": [[o.source_id, o.dest_id, o.ships, bool(planned.get((o.source_id, o.dest_id)))]
                      for o in ui.hint_left(state) if o.source_id in state.systems and o.dest_id in state.systems],
            "caption": {"keys": hud.hint_caption(state, ui, False), "touch": hud.hint_caption(state, ui, True)}}


def card(state: GameState, ui: Ui) -> dict | None:
    """The card over the board, if any: a gauntlet stage's briefing, a star's
    lore, or the forecast's breakdown (`render._draw_briefing`, `_draw_lore`,
    `_draw_odds_card`)."""
    if ui.history or state.winner is not None:
        return None
    if ui.briefing_open and ui.in_stage:
        return {"kind": "briefing", "title": ui.stage_title, "sub": ui.stage_label or "",
                "paras": list(ui.briefing), "notes": list(hud.briefing_notes(ui)),
                "close": {"keys": hud.briefing_close(False, False), "touch": hud.briefing_close(True, False),
                          "portrait": hud.briefing_close(True, True)}}
    if ui.lore_sid in state.systems:
        name = state.systems[ui.lore_sid].name
        lore = starlore.LORE.get(name)
        if lore is None:
            return None
        paras, close, notes = hud.lore_card(lore, False)
        return {"kind": "lore", "title": name, "sub": lore.language, "paras": list(paras), "notes": list(notes),
                "close": {"keys": close, "touch": hud.lore_card(lore, True)[1]}}
    if ui.odds_card_up():
        odds = hud.odds_card(state, ui)
        line = hud.forecast(state, ui)
        if odds is None or line is None:
            return None
        return {"kind": "odds", "title": odds[0], "label": line[0][0], "tone": line[1],
                "items": [list(item) for item in odds[1]]}
    return None


def graph(state: GameState, ui: Ui) -> dict:
    """History's graphs (`render._draw_history_graph`): the tabs, the metric on
    show with each drawn seat's series (None for a turn it was out of sight),
    the legend's rows with their figures at the turn on screen, and the
    coach's notes when its tab is open."""
    chart = ui.history_chart
    assert chart is not None
    seats, traces = ui.graph_seats(), ui.graph_traces()
    key, _title, unit = stats.METRICS[ui.graph_metric % len(stats.METRICS)]
    turn = max(0, min(ui.history_turn, chart.turns))

    def at(pid: int) -> float:
        series = chart.series(key, pid)
        return series[min(turn, len(series) - 1)] if series else 0.0

    def plain(series) -> list:
        return [None if math.isnan(v) else round(v, 3) for v in series]   # NaN: out of sight

    review = ui.coach_review
    shown = list(traces) + ([stats.NEUTRAL] if key in stats.AREA else [])
    return {
        "full": ui.graph_full,
        "tabs": [label for _k, label, _u in stats.METRICS] + (["Coach"] if review is not None else []),
        "tab": len(stats.METRICS) if ui.coach_open and review is not None else ui.graph_metric % len(stats.METRICS),
        "note": {"keys": hud.graph_note(ui, False), "touch": hud.graph_note(ui, True)},
        "metric": key, "unit": unit, "stacked": key in stats.STACKED, "area": key in stats.AREA,
        "turns": chart.turns, "turn": turn,
        "series": {str(pid): plain(chart.series(key, pid)) for pid in shown},
        "colors": {str(pid): list(state.players[pid].color) if pid in state.players else [70, 74, 90]
                   for pid in shown},
        "rows": [[pid, state.players[pid].name if pid in state.players else str(pid), hud.seat_who(state, ui, pid),
                  hud.graph_value(key, at(pid)), pid in ui.graph_hidden] for pid in seats],
        "unclaimed": hud.graph_value(key, at(stats.NEUTRAL)) if key in stats.AREA else None,
        "coach": {"summary": review.summary, "sel": ui.coach_sel,
                  "notes": [[n.turn, hud.COACH_ROLE.get(n.kind, "text"), n.text] for n in review.notes]}
        if review is not None and ui.coach_open else None,
    }


def rules(state: GameState, ui: Ui) -> list[dict]:
    """Our standing rules as the map draws them: each source's lanes with their
    share and whether they charge a system we don't hold, its hold badge, and
    whether it will fire (only a live rule is picked off its lanes)."""
    out = []
    for src, rule in ui.auto_forward.items():
        s = state.systems.get(src)
        if s is None or s.owner_id != ui.human_id:
            continue
        out.append({"src": src, "live": ui.rule_is_live(state, src), "badge": hud.hold_badge(state, ui, src),
                    "lanes": [[d, pct, ui.lane_is_hostile(state, d)] for d, pct in rule.shares.items()
                              if d in state.systems]})
    return out


def scores(state: GameState, ui: Ui) -> list[dict]:
    """The scoreboard: each seat the human knows of, how much it knows, its entry
    at each level of detail (with its name, then shedding the rest), and its
    tooltip."""
    out = []
    for pid in sorted(state.players):
        if state.players[pid].is_neutral:
            continue
        v = hud.score_vis(state, ui, pid)
        if v == "hidden":
            continue
        forms = [hud.score_label(state, ui, pid, v, True)]
        forms += [hud.score_label(state, ui, pid, v, False, terse) for terse in range(4)]
        out.append({"id": pid, "vis": v, "forms": forms, "tip": hud.score_tip(state, ui, pid)})
    return out


def legends() -> dict:
    """The empty panel's legend for each scene and input, sent once."""
    return {"live": {"touch": hud.LEGEND_TOUCH, "keys": hud.LEGEND_KEYS, "compact": hud.LEGEND_COMPACT_TOUCH},
            "history": {"touch": hud.LEGEND_HISTORY_TOUCH, "keys": hud.LEGEND_HISTORY_KEYS,
                        "compact": hud.LEGEND_HISTORY_COMPACT_TOUCH},
            "spectating": {"touch": hud.LEGEND_SPECTATING_TOUCH, "keys": hud.LEGEND_SPECTATING_KEYS,
                           "compact": hud.LEGEND_SPECTATING_TOUCH}}

