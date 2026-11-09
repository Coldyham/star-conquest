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

from starconquest import mapgen, turnfilm
from starconquest.model import GameState

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
