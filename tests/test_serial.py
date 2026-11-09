"""`serial`: the JSON the HTML frontend draws from."""

from __future__ import annotations

import json

from starconquest import ai, engine, replay, serial, turnfilm
from starconquest.settings import Settings, build_state


def _played(turns: int = 12, **over):
    ai.load_models()
    settings = Settings.from_dict({"players": 3, "nodes": 16, **over})
    settings.autoplay = True
    state = build_state(settings, 5)
    events: list[turnfilm.Event] = []   # every turn's, in order
    last: list[turnfilm.Event] = []
    for _ in range(turns):
        last = []
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide, on_event=last.append)
        events += last
    return state, events, last


def test_everything_is_plain_json():
    state, _, last = _played()
    for blob in (serial.static(state), serial.board(state), serial.film(turnfilm.film(last)),
                 serial.fog({1, 2}, {1, 2, 3}, {3: (2, 5, 1.0)})):
        assert json.loads(json.dumps(blob)) == blob


def test_fleets_keep_the_boards_own_order():
    state, _, _ = _played()
    assert len(state.fleets) > 1
    rows = serial.board(state)["fleets"]
    assert [(r[1], r[2], r[3]) for r in rows] == [(f.source_id, f.dest_id, f.ships) for f in state.fleets]


def test_a_fight_carries_its_derived_figures():
    _, events, _ = _played(turns=30)
    fought = [e for e in events if isinstance(e, turnfilm.Landed) and e.steps]
    assert fought
    for ev in fought:
        out = serial.event(ev)
        assert out["k"] == "Landed" and out["victor"] == ev.victor and out["cost"] == ev.cost
        assert out["losses"] == [list(x) for x in ev.losses]
        assert out["steps"][0]["attacker"] == ev.steps[0].attacker


def test_the_static_map_has_every_system_and_lane():
    state, _, _ = _played(turns=0)
    blob = serial.static(state)
    assert [s["id"] for s in blob["systems"]] == sorted(state.systems)
    assert len(blob["lanes"]) == len(state.lanes)
    assert all(a < b for a, b, _ in blob["lanes"])
