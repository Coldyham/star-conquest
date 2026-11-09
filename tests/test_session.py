"""The HTML build's session: JSON commands in, JSON replies and effects out."""

from __future__ import annotations

import json

import pytest

from starconquest import hostio
from starconquest.session import API_VERSION, Session

PAGE = {"origin": "http://127.0.0.1:8000", "hostname": "127.0.0.1", "pathname": "/next/", "hash": ""}


@pytest.fixture
def session():
    s = Session()
    yield s
    hostio.reset()


def _call(s: Session, op: str, **args) -> dict:
    reply = json.loads(s.handle(json.dumps({"id": 7, "op": op, "args": args})))
    assert reply["id"] == 7
    return reply


def test_boot_answers_the_api_and_the_roster(session):
    reply = _call(session, "boot", page=PAGE, store={})
    assert reply["ok"] and reply["result"]["api"] == API_VERSION
    assert {"marshal", "knower", "heuristic"} <= set(reply["result"]["bots"])
    assert hostio.in_worker()


def test_an_all_bot_game_plays_turn_by_turn_with_films(session):
    _call(session, "boot", page=PAGE, store={})
    start = _call(session, "game.start", settings={"players": 3, "nodes": 14, "autoplay": True}, seed=4)
    assert start["ok"]
    res = start["result"]
    assert res["seed"] == 4 and len(res["static"]["systems"]) == len(res["board"]["sys"])
    assert len(res["travel"]) == len(res["static"]["lanes"])
    filmed = 0
    for turn in range(1, 6):
        reply = _call(session, "turn.end")
        assert reply["ok"], reply.get("trace")
        assert reply["result"]["board"]["turn"] == turn
        filmed += reply["result"]["film"] is not None
        assert not [e for e in reply["effects"] if e["t"] in ("http", "fire")]   # nothing is sent
    assert filmed


def test_the_same_seed_plays_the_same_game(session):
    def boards():
        s = Session()
        _call(s, "boot", page=PAGE, store={})
        _call(s, "game.start", settings={"players": 2, "nodes": 12, "autoplay": True}, seed=9)
        return [_call(s, "turn.end")["result"]["board"] for _ in range(6)]
    assert boards() == boards()


def test_a_failing_command_answers_rather_than_raising(session):
    reply = _call(session, "turn.end")
    assert not reply["ok"] and "AssertionError" in reply["error"] and reply["trace"]
    assert not _call(session, "no.such")["ok"]
    assert not json.loads(session.handle("{oops"))["ok"]


def _human_game(session, seed=11):
    _call(session, "boot", page=PAGE, store={})
    start = _call(session, "game.start", settings={"players": 2, "nodes": 12}, seed=seed)["result"]
    view = start["view"]
    home = int(next(iter(view["available"])))
    nbrs = next(s["nbrs"] for s in start["static"]["systems"] if s["id"] == home)
    return start, home, nbrs[0]


def test_a_send_is_composed_by_taps_and_resolved_by_end_turn(session):
    start, home, nbr = _human_game(session)
    assert start["view"]["mode"] == "idle" and start["view"]["panel"]["legend"] == "live"
    view = _call(session, "ui.tap", sid=home)["result"]["view"]
    assert view["mode"] == "selected" and view["selected"] == home
    assert view["panel"]["focus"] == home and view["panel"]["system"][0][2] == "head"
    view = _call(session, "ui.tap", sid=nbr)["result"]["view"]
    pop = view["popup"]
    assert view["mode"] == "choosing" and pop["src"] == home and pop["dest"] == nbr
    assert pop["discard"] == "Cancel" and [p[0] for p in pop["presets"]] == ["Half", "All"]
    assert pop["forecast"]["forms"] and pop["slider"][2] == int(pop["value"])
    sending = int(pop["value"])
    view = _call(session, "ui.call", method="step_count", args=[-1])["result"]["view"]
    assert int(view["popup"]["value"]) == sending - 1
    view = _call(session, "ui.empty")["result"]["view"]
    assert view["mode"] == "idle" and view["pending"] == [[home, nbr, sending - 1]]
    assert view["available"][str(home)] == 1
    turn = _call(session, "turn.end")["result"]
    assert turn["board"]["turn"] == 1 and turn["view"]["pending"] == []
    assert any(f[1:4] == [home, nbr, sending - 1] for f in turn["board"]["fleets"])


def test_a_lane_press_reopens_the_order_on_it(session):
    _, home, nbr = _human_game(session)
    _call(session, "ui.tap", sid=home)
    _call(session, "ui.tap", sid=nbr)
    _call(session, "ui.empty")
    view = _call(session, "ui.lane", hits=[["order", 0, -1]])["result"]["view"]
    assert view["sel_order"] == 0 and view["popup"]["discard"] == "Delete order"
    view = _call(session, "ui.call", method="cancel_send")["result"]["view"]
    assert view["pending"] == []


def test_keys_answer_actions_and_only_listed_ui_methods_are_callable(session):
    _, home, _ = _human_game(session)
    assert _call(session, "ui.key", name="Enter")["result"]["action"] == "end_turn"
    assert _call(session, "ui.key", name="Escape")["result"]["action"] == "menu"
    _call(session, "ui.tap", sid=home)
    reply = _call(session, "ui.key", name="Escape")["result"]
    assert reply["action"] is None and reply["view"]["mode"] == "idle"
    assert not _call(session, "ui.call", method="reset_view")["ok"]
    assert not _call(session, "ui.call", method="__class__")["ok"]


def test_the_view_is_plain_json_through_a_game(session):
    _human_game(session)
    for _ in range(5):
        res = _call(session, "turn.end")["result"]
        assert json.loads(json.dumps(res["view"])) == res["view"]
        assert {s["id"] for s in res["view"]["scores"]} == {1, 2}
