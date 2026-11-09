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


def test_a_forward_rule_is_armed_shown_and_reopened(session):
    _, home, nbr = _human_game(session)
    _call(session, "ui.tap", sid=home)
    view = _call(session, "ui.tap", sid=nbr, shift=True)["result"]["view"]
    pop = view["popup"]
    assert pop["forward"] and pop["hold"] == {"value": 0, "smart": False} and pop["split"] is None
    assert view["editing_rule"] == home and pop["forecast"] is None
    view = _call(session, "ui.call", method="step_hold", args=[2])["result"]["view"]
    assert view["popup"]["hold"]["value"] == 2
    view = _call(session, "ui.empty")["result"]["view"]
    assert view["rules"] == [{"src": home, "live": True, "badge": "hold 2", "lanes": [[nbr, 100, True]]}]
    assert view["panel"]["clear"]["all"].startswith("Clear all forwarding")
    view = _call(session, "ui.lane", hits=[["rule", home, nbr]])["result"]["view"]
    assert view["editing_rule"] == home and view["popup"]["discard"] == "Delete rule"
    assert view["panel"]["rule"][0][0] == "Auto-forward"
    view = _call(session, "ui.call", method="clear_all_forward")["result"]["view"]
    assert view["rules"] == []


def test_flow_actions_undo_retry_and_restart(session):
    start, _, _ = _human_game(session)
    for _ in range(2):
        _call(session, "turn.end")
    res = _call(session, "game.action", name="undo")["result"]
    assert res["fresh"] and res["board"]["turn"] == 1
    res = _call(session, "game.action", name="retry")["result"]
    assert res["fresh"] and res["board"]["turn"] == 0 and res["seed"] == start["seed"]
    assert res["board"] == start["board"]
    res = _call(session, "game.action", name="restart")["result"]
    assert res["fresh"] and res["seed"] == start["seed"] + 1
    view = _call(session, "game.action", name="toggle_autoplay")["result"]["view"]
    assert view["autoplay"] and view["step_ms"] == 350
    view = _call(session, "game.action", name="toggle_play")["result"]["view"]
    assert view["playing"]


def test_a_decided_game_shows_its_veil_and_answers_its_keys(session):
    _call(session, "boot", page=PAGE, store={})
    _call(session, "game.start", settings={"players": 2, "nodes": 8, "autoplay": True}, seed=3)
    for _ in range(300):
        res = _call(session, "turn.end")["result"]
        if res["board"]["winner"] is not None:
            break
    over = res["view"]["over"]
    assert over is not None and over["headline"][0].endswith("wins!") and over["share"] is None
    assert not res["view"]["can"]["end_turn"]
    assert _call(session, "ui.key", name="t")["result"]["action"] == "retry"
    assert _call(session, "ui.key", name="Escape")["result"]["action"] == "menu"


def test_route_mode_plans_a_chain_and_confirms_it(session):
    _, home, nbr = _human_game(session)
    view = _call(session, "game.action", name="toggle_route")["result"]["view"]
    assert view["mode"] == "routing" and view["route"]["rally"] and view["route"]["confirm"] == ["Rally", False]
    view = _call(session, "ui.key", name="Tab")["result"]["view"]
    assert not view["route"]["rally"] and view["route"]["confirm"] == ["Route", False]
    _call(session, "ui.call", method="add_route_sids", args=[[home]])
    view = _call(session, "ui.tap", sid=nbr)["result"]["view"]
    route = view["route"]
    assert route["dest"] == nbr and route["confirm"][1] and route["plan"][0]["src"] == home
    assert _call(session, "ui.empty")["result"]["view"]["route"]["dest"] == nbr   # a stray tap keeps it
    view = _call(session, "ui.key", name="Enter")["result"]["view"]
    assert view["mode"] != "routing" and view["rules"][0]["src"] == home


def test_a_hint_asks_first_then_draws_its_sends(session):
    _human_game(session)
    res = _call(session, "game.action", name="hint")["result"]
    assert res["modal"]["kind"] == "hint" and res["view"]["hint"] is None
    view = _call(session, "game.action", name="take_hint")["result"]["view"]
    assert view["hint"] is not None and view["hint"]["caption"]["keys"].startswith("Hint")
    assert "modal" not in _call(session, "game.action", name="hint")["result"]   # a second press hides it
    res = _call(session, "game.action", name="hint")["result"]
    assert "modal" not in res and res["view"]["hint"] is not None   # asked once per game


def test_cards_open_and_any_key_puts_them_away(session):
    _, home, nbr = _human_game(session)
    _call(session, "ui.tap", sid=home)
    _call(session, "ui.tap", sid=nbr)
    view = _call(session, "ui.call", method="open_odds")["result"]["view"]
    assert view["card"]["kind"] == "odds" and view["card"]["items"][0][0] == "row"
    assert view["card"]["label"] == view["popup"]["forecast"]["forms"][0]
    reply = _call(session, "ui.key", name="Enter")["result"]
    assert reply["action"] is None and reply["view"]["card"] is None   # spent putting it away


def test_history_scrubs_plays_back_and_rewinds(session):
    _human_game(session)
    for _ in range(4):
        _call(session, "turn.end")
    res = _call(session, "game.action", name="toggle_history")["result"]
    h = res["view"]["history"]
    assert h["turn"] == h["max"] == 4 and not h["reveal"] and not res["view"]["can"]["end_turn"]
    assert _call(session, "ui.key", name="Home")["result"]["view"]["history"]["turn"] == 0
    res = _call(session, "history.seek", turn=2)["result"]
    assert res["board"]["turn"] == 2 and res["view"]["history"]["label"].startswith("Turn 2/4")
    assert res["held"] is None or isinstance(res["held"]["events"], list)
    assert _call(session, "history.step")["result"]["board"]["turn"] == 2   # paused: stays put
    _call(session, "game.action", name="toggle_play")
    step = _call(session, "history.step")["result"]
    assert step["board"]["turn"] == 3 and step["view"]["history"]["turn"] == 3
    res = _call(session, "game.action", name="rewind")["result"]
    assert res["modal"]["kind"] == "rewind" and res["modal"]["then"] == "rewind_confirmed"
    res = _call(session, "game.action", name="rewind_confirmed")["result"]
    assert res["fresh"] and res["board"]["turn"] == 3 and res["view"]["history"] is None
    _call(session, "game.action", name="toggle_history")
    res = _call(session, "game.action", name="toggle_history")["result"]
    assert res["view"]["history"] is None and res["board"]["turn"] == 3


def test_a_decided_games_history_opens_on_its_graphs_and_coach(session):
    _call(session, "boot", page=PAGE, store={})
    _call(session, "game.start", settings={"players": 2, "nodes": 8}, seed=3)
    _call(session, "game.action", name="toggle_autoplay")
    for _ in range(300):
        if _call(session, "turn.end")["result"]["board"]["winner"] is not None:
            break
    view = _call(session, "game.action", name="toggle_history")["result"]["view"]
    g = view["graph"]
    assert view["history"]["reveal"] and g is not None and g["tabs"][-1] == "Coach"
    assert json.loads(json.dumps(view)) == view
    view = _call(session, "ui.call", method="graph_tab", args=[2])["result"]["view"]
    assert view["graph"]["metric"] == "ships" and view["graph"]["coach"] is None
    assert len(view["graph"]["series"]["1"]) == view["graph"]["turns"] + 1
    view = _call(session, "ui.call", method="graph_tab", args=[len(g["tabs"]) - 1])["result"]["view"]
    assert view["graph"]["coach"]["summary"]
    view = _call(session, "ui.call", method="toggle_trace", args=[2])["result"]["view"]
    assert [r[4] for r in view["graph"]["rows"] if r[0] == 2] == [True]
