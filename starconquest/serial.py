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

from starconquest import app, hud, mapgen, paths, starlore, turnfilm
from starconquest.model import GameState, lane_key
from starconquest.viewstate import CHOOSING, Ui

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
        "visible": sorted(ui.visible),
        "seen": sorted(ui.seen),
        "popup": popup(state, ui) if ui.mode == CHOOSING else None,
        "panel": panel(state, ui),
        "scores": scores(state, ui),
        "footer": [list(b) for b in hud.footer(state, ui)],
        "lost": me.ships_lost if me is not None and not ui.spectating else None,
        "autoplay": ui.autoplay,
        "playing": ui.playing,
        "fast_forward": ui.fast_forward,
        "step_ms": app.step_delay(ui, app.PLAY_MS),
        "over": over(state, ui) if state.winner is not None and not ui.history else None,
        "can": {
            "end_turn": state.winner is None and not ui.awaiting_others(state) and not ui.spectating,
            "undo": ui.can_undo(state),
            "retry": ui.can_retry,
            "hint": ui.can_hint(state),
            "route": ui.can_route(state),
            "fast_forward": ui.can_fast_forward(state),
            "review": ui.can_review(state),
        },
    }


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
                "clear": clear}
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

