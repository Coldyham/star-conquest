"""The HTML build's session: JSON commands in, JSON replies and effects out."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_an_upright_phone_folds_handing_over_but_never_while_it_runs(session):
    start, _, _ = _human_game(session)
    view = start["view"]
    assert view["phone_folds"] == ["autoplay_button_rect", "play_pause_rect"]
    shown = {b[0] for b in view["footer"]}
    assert set(view["phone_folds"]) <= shown       # folded on a phone, still offered
    view = _call(session, "game.action", name="toggle_autoplay")["result"]["view"]
    assert view["phone_folds"] == []               # Take control stays one tap away


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
    assert view["committed"] == {str(home): sending - 1}
    turn = _call(session, "turn.end")["result"]
    assert turn["board"]["turn"] == 1 and turn["view"]["pending"] == []
    # Nothing is promised once the turn resolves, so the film's counts are the
    # film's board's own, never the resolved board's.
    assert turn["view"]["committed"] == {}
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


def test_a_press_on_empty_map_drops_the_hover(session):
    """A finger leaves no pointer to clear the hover, so a press on nothing must:
    else the panel keeps the last system and hides the legend's buttons."""
    _, home, nbr = _human_game(session)
    _call(session, "ui.tap", sid=home)
    _call(session, "ui.tap", sid=nbr)
    view = _call(session, "ui.empty")["result"]["view"]   # Done: its source stays lit
    assert view["mode"] == "idle" and view["panel"]["focus"] == home
    view = _call(session, "ui.empty")["result"]["view"]
    assert view["panel"]["focus"] is None and view["panel"]["legend"] == "live"
    _call(session, "ui.hover", sid=nbr)
    view = _call(session, "ui.empty")["result"]["view"]
    assert view["panel"]["focus"] is None


def test_a_pinch_puts_back_what_its_first_finger_pressed(session):
    """The first finger's press reaches the session before a second makes it a
    pinch; ``ui.unpress`` takes that press back, once."""
    _, home, nbr = _human_game(session)
    _call(session, "ui.tap", sid=home)
    before = _call(session, "ui.tap", sid=nbr)["result"]["view"]
    assert before["mode"] == "choosing"
    view = _call(session, "ui.empty")["result"]["view"]   # the first finger: Done
    assert view["mode"] == "idle"
    view = _call(session, "ui.unpress")["result"]["view"]
    assert view["mode"] == "choosing" and view["popup"]["dest"] == nbr
    assert view["pending"] == before["pending"]
    assert _call(session, "ui.unpress")["result"]["view"]["mode"] == "choosing"   # nothing left to undo


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


def test_end_turn_stops_a_running_clock(session):
    _human_game(session)
    assert _menu(session, "game.view")["view"]["end"] == ["End Turn", "end_turn"]
    view = _call(session, "game.action", name="toggle_autoplay")["result"]["view"]
    assert view["end"] == ["Take control", "toggle_autoplay"]
    assert _menu(session, "ui.key", name="Enter")["action"] == "toggle_autoplay"
    _call(session, "game.action", name="toggle_autoplay")
    view = _call(session, "game.action", name="toggle_play")["result"]["view"]
    assert view["end"] == ["Pause", "toggle_play"]
    assert _menu(session, "ui.key", name="Enter")["action"] == "toggle_play"
    view = _call(session, "game.action", name="toggle_play")["result"]["view"]
    assert view["end"] == ["End Turn", "end_turn"]


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


def test_history_shows_the_rules_standing_on_the_board_on_screen(session):
    from starconquest.model import ForwardRule
    _, home, nbr = _human_game(session)
    _call(session, "turn.end")
    session.ui.auto_forward[home] = ForwardRule({nbr: 100}, hold=50)   # standing on turns 1-2
    _call(session, "turn.end")
    _call(session, "turn.end")
    session.ui.auto_forward.clear()   # ...and gone by the end
    _call(session, "turn.end")
    live = session.ui.auto_forward

    def srcs(view):
        return [r["src"] for r in view["rules"]]
    view = _call(session, "game.action", name="toggle_history")["result"]["view"]
    assert view["history"]["turn"] == 4 and srcs(view) == []
    assert srcs(_call(session, "history.seek", turn=2)["result"]["view"]) == [home]
    assert srcs(_call(session, "ui.key", name="ArrowLeft")["result"]["view"]) == [home]   # turn 1
    assert srcs(_call(session, "ui.key", name="ArrowLeft")["result"]["view"]) == []       # turn 0
    _call(session, "history.seek", turn=2)
    res = _call(session, "game.action", name="toggle_history")["result"]
    assert res["view"]["history"] is None and srcs(res["view"]) == []
    assert session.ui.auto_forward is live and session.ui.live_rules is None


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


def test_every_game_parity_recipe_loads_as_the_match_on_screen(session):
    recipes = json.loads((Path(__file__).parent / "fixtures" / "parity_screens.json").read_text())["screens"]
    _call(session, "boot", page=PAGE, store={})
    for r in recipes:
        if r["surface"] != "game":
            continue
        reply = _call(session, "parity.load", recipe=r)
        assert reply["ok"], (r["name"], reply.get("trace"))
        assert json.loads(json.dumps(reply["result"])) == reply["result"]


# --------------------------------------------------------------------------- #
# The setup menu (`setupmenu`, `Session.op_menu_*`)
@pytest.fixture
def games(monkeypatch, tmp_path):
    from starconquest import replay
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def _menu(s: Session, op: str, **args) -> dict:
    reply = _call(s, op, **args)
    assert reply["ok"], reply.get("trace")
    return reply["result"]


def test_the_menu_opens_prefilled_and_starts_its_own_setup(session, games):
    _call(session, "boot", page=PAGE, store={})
    res = _menu(session, "menu.open", first=True, prefill={"players": 4, "seed": 11})
    assert json.loads(json.dumps(res)) == res
    rows = {r["label"]: r["control"] for r in res["menu"]["panel"] if r["kind"] == "row"}
    assert rows["Players"]["value"] == "4" and rows["Seed"]["text"] == "11"
    assert res["stale"] and "resume" not in res
    assert not _menu(session, "menu.survey")["stale"]
    res = _menu(session, "menu.press", key="tab_combat")
    assert res["action"] is None and res["stale"] is False   # nothing moved that shapes the lanes
    res = _menu(session, "menu.press", key="players_dec")
    assert res["stale"]
    res = _menu(session, "menu.key", name="Enter")
    assert res["action"] == "start"
    game = _menu(session, "game.start")
    assert game["seed"] == 11 and len(game["static"]["players"]) == 3 + 1


def test_a_slider_drag_measures_the_lanes_only_once_let_go(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    _menu(session, "menu.survey")
    assert _menu(session, "menu.slide", key="adv_extra_edges", t=0.9, phase="start")["menu"]["dragging"]
    res = _menu(session, "menu.slide", key="adv_extra_edges", t=0.95, phase="move")
    assert res["stale"]
    res = _menu(session, "menu.slide", key="adv_extra_edges", t=1.0, phase="end")
    assert res["menu"]["dragging"] is None and res["stale"]
    assert not _menu(session, "menu.survey")["stale"]


def test_the_seed_field_filters_what_is_typed(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    res = _menu(session, "menu.seed", text="4x2")
    seed = next(r for r in res["menu"]["panel"] if r["kind"] == "row" and r["label"] == "Seed")
    assert seed["control"]["text"] == "42" and session.settings.seed == 42
    _menu(session, "menu.seed", text="", done=True)
    assert session.settings.seed is None


def test_a_preference_is_written_through_to_the_page(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    reply = _call(session, "menu.press", key="share_games")
    assert {"t": "store.set", "key": "sc_share_games", "value": "1"} in reply["effects"]


def test_an_unknown_menu_control_is_refused(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    reply = _call(session, "menu.press", key="no_such_control")
    assert not reply["ok"]


def test_get_link_puts_the_setup_in_the_address_and_the_clipboard(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    reply = _call(session, "menu.press", key="get_link")
    kinds = {e["t"] for e in reply["effects"]}
    assert {"fragment", "clipboard"} <= kinds
    assert reply["result"]["status"]["ok"]


def test_an_unfinished_match_is_offered_at_boot_and_resumes(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True, prefill={"autoplay": True})
    _menu(session, "game.start", seed=3)
    for _ in range(3):
        _menu(session, "turn.end")
    fresh = Session()
    _call(fresh, "boot", page=PAGE, store={})
    res = _menu(fresh, "menu.open", first=True)
    assert res["resume"]["detail"].startswith("turn 3")
    game = _menu(fresh, "menu.resume", accept=True)["game"]
    assert game["seed"] == 3 and game["board"]
    assert "resume" not in _menu(fresh, "menu.open")


def test_every_menu_parity_recipe_loads(session, games):
    recipes = json.loads((Path(__file__).parent / "fixtures" / "parity_screens.json").read_text())["screens"]
    _call(session, "boot", page=PAGE, store={})
    for r in recipes:
        if r["surface"] not in ("menu", "resume"):
            continue
        res = _menu(session, "parity.menu", recipe=r)
        assert json.loads(json.dumps(res)) == res
        if r["name"] == "menu-slow-confirm":
            assert res["menu"]["modal"]["title"] == "This setup is slow"
        if r["surface"] == "resume":
            assert res["resume"]


# --------------------------------------------------------------------------- #
# The board (sharing): the win overlay's two buttons, answers that land on it
# later, the campaign's lookup and a watched replay
def _won(session) -> None:
    """A human game a few turns in, then decided for the human by fiat."""
    _human_game(session)
    for _ in range(2):
        _call(session, "turn.end")
    session.state.winner = session.ui.human_id


def _effects(reply: dict, kind: str) -> list[dict]:
    return [e for e in reply["effects"] if e["t"] == kind]


def test_a_win_is_shared_as_a_challenge_link(session):
    _won(session)
    over = _menu(session, "game.view")["view"]["over"]
    assert over["share"] == ["Challenge a friend", "C"] and over["board"][0] == "Post on leaderboard"
    reply = _call(session, "game.action", name="share")
    [clip] = _effects(reply, "clipboard")
    assert clip["text"].startswith(PAGE["origin"] + "/next/#")
    assert reply["result"]["view"]["over"]["notes"].startswith("Challenge link copied")


def test_the_entry_form_opens_on_the_result_and_its_log_goes_up(session):
    _won(session)
    reply = _call(session, "game.action", name="leaderboard")
    [fire] = _effects(reply, "fire")
    from starconquest import paths
    assert fire["url"].endswith(paths.LEADERBOARD_LOG_PATH)
    assert json.loads(fire["body"])["match_id"] == session.log.match_id
    [nav] = _effects(reply, "navigate")
    assert "#" in nav["url"]
    assert reply["result"]["view"]["over"]["notes"].startswith("Leaderboard opened")


def test_a_quick_post_lands_its_summary_and_then_views_the_map(session):
    _won(session)
    session.ui.post_as = "Ann"
    assert _menu(session, "game.view")["view"]["over"]["board"] == ["Post as Ann", "L"]
    reply = _call(session, "game.action", name="leaderboard")
    [bridge] = _effects(reply, "bridge")
    assert bridge["fn"] == "quickPost" and bridge["args"][1] == "Ann"
    assert reply["result"]["view"]["over"]["board"][0] == "Posting…"
    turns, lost = session.state.turn, session.state.players[1].ships_lost
    body = json.dumps({"game_key": "k1", "score_count": 1, "best_turns": turns, "best_lost": lost,
                       "best_user_name": "Ann"})
    pushed = _menu(session, "net.reply", req=bridge["req"], state="ok", body=body)["pushed"]
    over = pushed["view"]["over"]
    assert over["board"] == ["View on board", "L"] and over["notes"] == "Posted  ·  the first score on this map"
    reply = _call(session, "game.action", name="leaderboard")
    [nav] = _effects(reply, "navigate")
    assert nav["url"].endswith("?key=k1")


def test_the_signed_in_name_lands_on_the_post_button(session):
    _won(session)
    session.ui.post_name_due = True
    reply = _call(session, "game.view")
    [bridge] = _effects(reply, "bridge")
    assert bridge["fn"] == "ownName"
    pushed = _menu(session, "net.reply", req=bridge["req"], state="ok", body="Bea")["pushed"]
    assert pushed["view"]["over"]["board"] == ["Post as Bea", "L"]
    # An answer that changes nothing on screen pushes nothing.
    assert "pushed" not in _menu(session, "net.reply", req=999, state="ok", body="")


def test_a_watched_game_offers_neither_button(session):
    _won(session)
    session.ui.watched = True
    view = _menu(session, "game.view")["view"]
    assert view["over"]["share"] is None
    assert not _effects(_call(session, "game.action", name="share"), "clipboard")


def test_the_match_notes_head_the_panel(session):
    _human_game(session)
    session.ui.challenge_target, session.ui.challenge_by = (12, 3), "Ann"
    session.ui.campaign_label = "Node 4: open"
    notes = _menu(session, "game.view")["view"]["notes"]
    assert notes == [["Node 4: open", "campaign"], ["Beat Ann's 12t / 3 lost", "teal"]]


def test_the_campaign_is_asked_about_before_start_and_stamped_on_the_match(session, games, monkeypatch):
    from starconquest import campaign
    monkeypatch.setattr(campaign, "SETTLE_S", 0.0)
    _call(session, "boot", page=PAGE, store={})
    reply = _call(session, "menu.open", first=True, prefill={"players": 2, "seed": 5})
    [http] = [e for e in _effects(reply, "http") if "campaign" in e["url"]]
    assert "starting=1" in http["url"]
    body = json.dumps({"node": {"id": 4, "kind": "field"}, "now": 0,
                       "status": {"why": "not-adjacent", "can": False}})
    _menu(session, "net.reply", req=http["req"], state="ok", body=body)
    res = _menu(session, "menu.press", key="start")
    assert res["action"] is None
    modal = res["menu"]["modal"]
    assert modal["title"] == "Campaign node 4" and "won't be a campaign move" in json.dumps(modal["lines"])
    assert _menu(session, "menu.press", key="slow_go")["action"] == "start"
    game = _menu(session, "game.start")
    assert session.ui.campaign_start == "not-adjacent"
    assert game["view"]["notes"] == [["Node 4: not a move", "campaign"]]


def _played_log() -> tuple[str, str]:
    s = Session()
    _call(s, "boot", page=PAGE, store={})
    _call(s, "game.start", settings={"players": 2, "nodes": 8, "autoplay": True}, seed=3)
    for _ in range(4):
        _call(s, "turn.end")
    return s.log.encoded(), s.log.match_id


def test_a_log_link_fetches_the_replay_and_opens_it_in_history(session, games):
    blob, match_id = _played_log()
    hostio.reset()
    _call(session, "boot", page={**PAGE, "hash": f"#log={match_id}"}, store={})
    reply = _call(session, "menu.open", first=True)
    assert reply["result"]["status"]["text"] == "Loading replay..."
    [http] = [e for e in _effects(reply, "http") if f"id={match_id}" in e["url"]]
    game = _menu(session, "net.reply", req=http["req"], state="ok", body=blob)["pushed"]["game"]
    assert game["view"]["history"]["turn"] == 0 and game["legends"]
    assert session.ui.watched


def test_a_replay_that_is_not_there_says_so_on_the_menu(session, games):
    _call(session, "boot", page={**PAGE, "hash": "#log=0123456789abcdef"}, store={})
    reply = _call(session, "menu.open", first=True)
    [http] = _effects(reply, "http")
    pushed = _menu(session, "net.reply", req=http["req"], state="missing", body="")["pushed"]
    assert pushed["menu"]["status"]["text"].startswith("That replay isn't on the leaderboard")


# --------------------------------------------------------------------------- #
# Play by post: the roster prompt, a match created, links opened, a turn sent
# and a settled one played onto the board. `hostio` stands in for the endpoint.
PBP_MATCH = "00112233445566ff"
PBP_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
PBP_PAGE = {"origin": "https://star-conquest.netlify.app", "hostname": "star-conquest.netlify.app",
            "pathname": "/next/", "hash": ""}


@pytest.fixture
def quick_reads(monkeypatch):
    """Reads come on every tick rather than the cadence's seconds apart."""
    from starconquest import pbpclient
    monkeypatch.setattr(pbpclient, "pbp_idle_floor", lambda idle: 0)
    monkeypatch.setattr(pbpclient, "pbp_poll_delay", lambda failures: 0)


def _pbp_payload(turn=0, submitted=(), turns=(), seats=(1, 2), **over):
    from starconquest import engine
    from starconquest.settings import Settings
    payload = {"match_id": PBP_MATCH, "settings_json": Settings(mode="random", players=2, nodes=16, seed=7).to_dict(),
               "seed": 7, "seats": list(seats), "turn": turn, "submitted": list(submitted), "turns": list(turns),
               "log": "", "finished": False, "rules_version": engine.RULES_VERSION, "deadline_hours": 48,
               "turn_opened_at": "2026-09-22T12:00:00+00:00"}
    payload.update(over)
    return payload


def _answer(s: Session, effect: dict, state: str, body) -> dict:
    return _menu(s, "net.reply", req=effect["req"], state=state,
                 body=body if isinstance(body, str) else json.dumps(body))


def _http(reply: dict, needle: str) -> dict:
    [effect] = [e for e in reply["effects"] if e["t"] == "http" and needle in e["url"]]
    return effect


def test_the_roster_prompt_creates_a_match_and_opens_on_its_invite(session, games, quick_reads):
    _call(session, "boot", page=PBP_PAGE, store={})
    _menu(session, "menu.open", first=True, prefill={"players": 2, "nodes": 16, "seed": 7})
    res = _menu(session, "menu.press", key="play_by_post")
    prompt = res["menu"]["pbp_prompt"]
    assert prompt["title"] == "Who's playing?" and [s["checked"] for s in prompt["seats"]] == [True, True]
    assert prompt["seats"][0]["you"] and prompt["deadline"] == "2 days"
    assert _menu(session, "menu.press", key="start")["menu"]["pbp_prompt"]   # modal: Start is not its own
    prompt = _menu(session, "menu.press", key="pbp_deadline_inc")["menu"]["pbp_prompt"]
    assert prompt["deadline"] == "3 days"
    _menu(session, "menu.pbp_text", which="title", text="Friday\x07 game")
    reply = _call(session, "menu.press", key="pbp_confirm")
    assert reply["result"]["menu"]["pbp_prompt"] is None and reply["result"]["action"] is None
    create = _http(reply, "action=create")
    sent = json.loads(create["body"])
    assert sent["deadline_hours"] == 72 and sent["title"] == "Friday game"
    _answer(session, create, "ok", {"match_id": sent["match_id"], "tokens": {"1": PBP_TOKEN, "2": PBP_TOKEN[::-1]}})
    tick = _call(session, "tick")
    assert tick["result"]["pushed"]["menu"]["status"]["text"].startswith("Match created")
    read = _http(tick, "action=state")
    _answer(session, read, "ok", _pbp_payload(match_id=sent["match_id"]))
    game = _menu(session, "tick")["pushed"]["game"]
    pbp_view = game["view"]["pbp"]
    assert pbp_view["you"] == ["You are " + session.state.players[1].name, 1]
    invite = pbp_view["overlay"]
    assert invite["kind"] == "invite" and [row[0] for row in invite["rows"]] == [1, 2, -1]   # fog off: watchable
    reply = _call(session, "pbp.copy", seat=2)
    [clip] = _effects(reply, "clipboard")
    assert "#pbp=" in clip["text"] and reply["result"]["view"]["pbp"]["overlay"]["rows"][1][3] == "Copied"
    assert _menu(session, "ui.key", name="Enter")["action"] is None   # the invite is modal
    view = _menu(session, "ui.call", method="close_invite")["view"]
    assert view["pbp"]["overlay"] is None and view["can"]["end_turn"]


def _seated(session) -> dict:
    """Open a seat link to an open match at turn 0; answers the reply that
    opened it, which already carries the next read."""
    _call(session, "boot", page={**PBP_PAGE, "hash": f"#pbp={PBP_MATCH}:{PBP_TOKEN}"}, store={})
    reply = _call(session, "menu.open", first=True)
    assert reply["result"]["status"]["text"] == "Opening match..." and "resume" not in reply["result"]
    _answer(session, _http(reply, "action=seat"), "ok", {"seat": 1})
    read = _http(_call(session, "tick"), "action=state")
    _answer(session, read, "ok", _pbp_payload())
    opened = _call(session, "tick")
    assert opened["result"]["pushed"]["game"]["view"]["pbp"]["overlay"] is None
    return opened


def test_end_turn_in_a_shared_match_sends_and_waits(session, games, quick_reads):
    _seated(session)
    reply = _call(session, "turn.end")
    assert reply["result"]["film"] is None
    submit = _http(reply, "action=submit")
    view = reply["result"]["view"]
    assert view["pbp"]["overlay"]["kind"] == "waiting" and view["pbp"]["end_word"] == "Orders sent"
    assert not view["can"]["end_turn"]
    tick = _call(session, "tick")   # the submission is still out: nothing moves
    assert "pushed" not in tick["result"]
    _answer(session, submit, "ok", {"turn": 0, "submitted": [1], "waiting": [2]})
    pushed = _menu(session, "tick").get("pushed", {})
    overlay = pushed["view"]["pbp"]["overlay"] if "view" in pushed else view["pbp"]["overlay"]
    assert overlay["kind"] == "waiting" and "Waiting for" in overlay["headline"]


def test_a_turn_settled_elsewhere_is_played_onto_the_board_with_its_film(session, games, quick_reads):
    from starconquest import pbp

    from tests.test_pbp_client import _a_move
    opened = _seated(session)
    match = pbp.match_from_dict(_pbp_payload())
    rows = [_a_move(session.state, 1, 2), _a_move(session.state, 2, 2)]
    resolved = pbp.resolve(pbp.match_from_dict(_pbp_payload(submitted=[1, 2], turns=rows)))
    assert match is not None and resolved is not None
    log = pbp.shareable(resolved[1]).encoded()
    read = _http(opened, "action=state")
    _answer(session, read, "ok", _pbp_payload(turn=1, turns=rows, log=log))
    turn = _menu(session, "tick")["pushed"]["turn"]
    assert turn["board"]["turn"] == 1 and turn["film"] is not None
    assert session.state.turn == 1 and turn["view"]["can"]["end_turn"]


def test_a_spectate_link_watches_from_no_seat(session, games, quick_reads):
    _call(session, "boot", page={**PBP_PAGE, "hash": f"#spectate={PBP_MATCH}"}, store={})
    reply = _call(session, "menu.open", first=True)
    _answer(session, _http(reply, "action=state"), "ok", _pbp_payload(watchable=True))
    view = _menu(session, "tick")["pushed"]["game"]["view"]
    assert view["pbp"]["you"] == ["Spectating", "text"] and view["pbp"]["end_word"] == "Spectating"
    assert view["pbp"]["overlay"]["kind"] == "notice" and not view["can"]["end_turn"]
    assert not _effects(_call(session, "turn.end"), "http")   # a spectator sends nothing


# --------------------------------------------------------------------------- #
# The daily and gauntlets (`setupmenu`'s Tutorial list, `app`'s menu side)
def _chain_data(gid: str = "run-me") -> dict:
    from starconquest.settings import Settings as _S
    return {"id": gid, "name": "Run me", "stages": [
        {"id": i, "title": f"Stage {i}", "settings": _S(players=2, nodes=8, seed=10 + i).to_dict(),
         "target": {"turns": 60}, "briefing": [f"Brief {i}"]} for i in range(3)]}


def test_the_tutorial_list_starts_a_stage_with_its_briefing(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    tut = _menu(session, "menu.press", key="tutorial")["menu"]["tutorial"]
    nodes = tut["graph"]["nodes"]
    assert tut["heading"] and nodes[0]["open"] and tut["buttons"][-1]["key"] == "tutorial_close"
    first = nodes[0]["key"]
    assert tut["graph"]["focus"] == first and tut["pick"]["play"]["enabled"]   # the first stage, selected
    assert _menu(session, "menu.press", key="get_link")["menu"]["tutorial"] is not None   # modal: ignored
    # A tap on another stage selects it; a second tap on the selected one plays it.
    tut = _menu(session, "menu.press", key=nodes[1]["key"])["menu"]["tutorial"]
    assert tut["graph"]["focus"] == nodes[1]["key"] and not tut["pick"]["play"]["enabled"]   # locked: shown, not played
    assert _menu(session, "menu.press", key=nodes[1]["key"])["menu"]["tutorial"] is not None
    _menu(session, "menu.press", key=first)
    res = _menu(session, "menu.press", key=first)
    if res["action"] is None:   # a slow-setup or campaign ask first
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert res["action"] == "start" and res["menu"]["tutorial"] is None
    assert res["menu"]["subtitle"][0]["text"].startswith("TUTORIAL")
    view = _menu(session, "game.start")["view"]
    assert session.ui.in_stage and view["notes"][0][1] == "teal"
    if session.ui.briefing:
        assert view["card"]["kind"] == "briefing" and view["card"]["paras"]
        view = _menu(session, "ui.key", name="x")["view"]
        assert view["card"] is None
    assert view["panel"]["stage_hint"]
    view = _menu(session, "ui.call", method="open_briefing")["view"]
    assert view["card"] is None or view["card"]["kind"] == "briefing"
    assert _menu(session, "ui.key", name="n")["action"] is None   # no New map in a stage


def test_next_stage_moves_the_menu_on_and_starts_it(session, games, monkeypatch):
    from starconquest import gauntlet
    from starconquest.viewstate import Ui
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    g = gauntlet.Gauntlet.from_dict(_chain_data("chain"))
    session.menu.gauntlets = [g]
    assert session.menu.stage_at is None
    from starconquest import setupmenu
    setupmenu.load_stage(session.menu, session.settings, g, 0)
    _menu(session, "game.start")
    assert session.ui.stage_title == "Stage 0"
    monkeypatch.setattr(Ui, "offers_next_stage", lambda self, state: True)
    res = _menu(session, "game.action", name="next_stage")
    assert res["fresh"] and session.ui.stage_title == "Stage 1" and session.menu.stage_at == ("chain", 1)
    assert res["view"]["card"]["kind"] == "briefing"


def test_a_run_starts_from_the_list_and_its_game_comes_to_the_page(session, games):
    from starconquest import gauntlet
    from starconquest import run as gauntlet_run
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    _menu(session, "menu.press", key="tutorial")
    g = gauntlet.Gauntlet.from_dict(_chain_data())
    session.menu.gauntlets, session.menu.gauntlet_pick = [g], 0
    tut = _menu(session, "menu.press", key="gauntlet_pick_inc")["menu"]["tutorial"]
    assert tut["run"]["buttons"][0]["key"] == "run_start"
    res = _menu(session, "menu.press", key="run_start")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert res["game"]["view"] and session.ui.run_id
    assert gauntlet_run.active() is not None and session.ui.stage_label.endswith("· run")
    # Back on the menu the list says the run is under way, and Continue resumes it.
    _menu(session, "menu.open")
    session.menu.gauntlets = [g]
    session.menu.tutorial_open, session.menu.runs = True, gauntlet_run.runs()
    tut = _menu(session, "menu.survey")["menu"]["tutorial"]
    assert tut["run"]["text"].startswith("Run under way")
    res = _menu(session, "menu.press", key="run_continue")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert res["game"] and session.ui.run_id



def test_the_footer_and_each_lists_foot_open_their_board_page(session, games):
    from starconquest import paths

    def opens(key: str) -> str:
        reply = _call(session, "menu.press", key=key)
        [nav] = _effects(reply, "navigate")
        return nav["url"]

    _call(session, "boot", page=PBP_PAGE, store={})
    actions = _menu(session, "menu.open", first=True)["menu"]["actions"]
    assert actions["leaderboard"] == "Leaderboard"
    assert opens("leaderboard").endswith(paths.LEADERBOARD_RECENT_PATH)
    prompt = _menu(session, "menu.press", key="play_by_post")["menu"]["pbp_prompt"]
    assert opens(prompt["link"]["key"]).endswith(paths.LEADERBOARD_LOBBY_PATH)
    assert session.menu.pbp_prompt   # the prompt stays open behind it
    _menu(session, "menu.press", key="pbp_cancel")
    tut = _menu(session, "menu.press", key="tutorial")["menu"]["tutorial"]
    assert opens(tut["link"]["key"]).endswith(paths.LEADERBOARD_GAUNTLETS_PATH)
    assert session.menu.tutorial_open

def test_a_forked_run_asks_for_the_way_on_and_plays_the_stage_picked(session, games):
    from starconquest import gauntlet
    from starconquest import run as gauntlet_run
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    _menu(session, "menu.press", key="tutorial")
    data = {**_chain_data("fork-me"), "lanes": [[0, 1], [0, 2]]}
    for row in data["stages"]:
        row.pop("target")                      # a fork alone makes it a run
    g = gauntlet.Gauntlet.from_dict(data)
    assert g is not None
    session.menu.gauntlets, session.menu.gauntlet_pick = [g], 0
    _menu(session, "menu.press", key="gauntlet_pick_inc")
    res = _menu(session, "menu.press", key="run_start")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert session.ui.run_id and session.menu.stage_at == ("fork-me", 0)
    r = gauntlet_run.active()
    assert r is not None and r.playing is not None
    gauntlet_run.finish(r.id, r.playing.log, gauntlet_run.MET, 20, 3, g)
    # Back on the list both ways on are marked, and Continue names the pick.
    _menu(session, "menu.open")
    session.menu.gauntlets = [g]
    session.menu.tutorial_open, session.menu.runs = True, gauntlet_run.runs()
    session.menu.stage_pick = 0
    tut = _menu(session, "menu.survey")["menu"]["tutorial"]
    marked = [n["key"] for n in tut["graph"]["nodes"] if "run" in n["marks"]]
    assert marked == ["stage_1", "stage_2"] and "choose a stage marked run" in tut["run"]["text"]
    assert tut["run"]["buttons"][0]["label"] == "Continue run"
    res = _menu(session, "menu.press", key="run_continue")      # nothing picked: it asks
    assert "game" not in res and session.menu.gauntlet_note[1].startswith("Select a stage marked run")
    tut = _menu(session, "menu.press", key="stage_2")["menu"]["tutorial"]
    assert tut["run"]["buttons"][0]["label"] == "Continue run: Stage 2"
    res = _menu(session, "menu.press", key="run_continue")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert res["game"] and session.ui.run_id and session.menu.stage_at == ("fork-me", 2)


def test_the_menu_opens_on_the_run_and_says_what_is_free_play(session, games, monkeypatch):
    from starconquest import app, gauntlet, replay, setupmenu
    from starconquest import run as gauntlet_run
    from starconquest.settings import Settings
    _call(session, "boot", page=PAGE, store={})
    data = {**_chain_data("fork-me"), "lanes": [[0, 1], [0, 2]]}
    g = gauntlet.Gauntlet.from_dict(data)
    assert g is not None
    monkeypatch.setattr(gauntlet, "every", lambda: [g])
    assert _menu(session, "menu.open", first=True)["menu"]["tutorial"] is None   # no run: no list
    r = gauntlet_run.start(g)
    log = replay.new_log(Settings(players=2, nodes=8, seed=5), 5)
    r = gauntlet_run.begin(r, 0, log)
    assert log.path is not None
    gauntlet_run.finish(r.id, log.path.name, gauntlet_run.MET, 20, 3, g)
    tut = _menu(session, "menu.open")["menu"]["tutorial"]
    assert tut is not None and tut["run"]["text"].startswith("Run under way")
    # The run's stages play in the run; any other is free play, and says so.
    assert tut["graph"]["focus"] == "stage_1" and tut["pick"]["play"]["label"] == "Play in run"
    tut = _menu(session, "menu.press", key="stage_0")["menu"]["tutorial"]
    assert tut["pick"]["play"]["label"] == setupmenu.FREE_PLAY and tut["pick"]["play"]["tone"] == "go"
    assert any(line["text"] == setupmenu.FREE_PLAY_NOTE for line in tut["pick"]["lines"])
    res = _menu(session, "menu.press", key="stage_0")             # a second tap only selects it
    assert "game" not in res and res["menu"]["tutorial"] is not None
    assert _menu(session, "menu.press", key="stage_play")["action"] == "start"   # the button plays it, free
    _menu(session, "game.start")
    assert session.ui is not None and not session.ui.run_id
    assert session.ui.stage_label.endswith(app.FREE_PLAY_TAG)
    menu = _menu(session, "menu.open")["menu"]
    assert menu["subtitle"][-1]["text"].startswith("Start is free play")
    _menu(session, "menu.press", key="stage_2")
    res = _menu(session, "menu.press", key="stage_2")              # a second tap on a run stage: the run
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert res["game"] and session.ui.run_id and session.ui.stage_label.endswith("· run")


def test_the_overlay_shows_where_the_run_goes_and_next_stage_plays_the_pick(session, games, monkeypatch):
    from starconquest import app, gauntlet, serial
    from starconquest import run as gauntlet_run
    from starconquest.viewstate import Ui
    _call(session, "boot", page=PAGE, store={})
    g = gauntlet.Gauntlet.from_dict({**_chain_data("fork-me"), "lanes": [[0, 1], [0, 2]]})
    assert g is not None
    monkeypatch.setattr(gauntlet, "every", lambda: [g])
    _menu(session, "menu.open", first=True)
    _menu(session, "menu.press", key="tutorial")
    res = _menu(session, "menu.press", key="run_start")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    state, ui = session.state, session.ui
    assert ui.run_id
    monkeypatch.setattr(Ui, "can_post", lambda self, state: True)
    state.winner = ui.human_id
    app.record_run_step(state, ui, session.log)
    assert ui.run_on and ui.run_choices == (1, 2) and ui.run_note.endswith("choose the way on below")
    over = serial.over(state, ui)
    graph = over["run_graph"]
    assert [n["key"] for n in graph["nodes"]] == ["stage_0", "stage_1", "stage_2"]
    assert [(n["col"], "run" in n["marks"]) for n in graph["nodes"]] == [(0, False), (1, True), (1, True)]
    assert [n["place"] for n in graph["nodes"]] == ["1", "2-1", "2-2"]
    assert graph["focus"] == "stage_1" and over["next_stage"] and not over["choose_stage"]
    assert _menu(session, "game.action", name="stage_0")["view"]["over"]["run_graph"]["focus"] == "stage_1"
    view = _menu(session, "game.action", name="stage_2")["view"]     # a tap picks
    assert view["over"]["run_graph"]["focus"] == "stage_2" and session.ui is ui
    picked = [n for n in view["over"]["run_graph"]["nodes"] if "picked" in n["marks"]]
    assert [n["key"] for n in picked] == ["stage_2"]
    res = _menu(session, "game.action", name="stage_2")               # a second tap plays it
    assert res["fresh"] and session.ui is not ui and session.ui.run_id == ui.run_id
    r = gauntlet_run.active()
    assert r is not None and r.playing is not None and r.playing.stage == 2


def test_a_run_with_lives_offers_try_again_and_the_list_after_a_loss(session, games, monkeypatch):
    from starconquest import gauntlet, serial, setupmenu
    from starconquest import run as gauntlet_run
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    _menu(session, "menu.press", key="tutorial")
    g = gauntlet.Gauntlet.from_dict({**_chain_data("lives"), "lives": 2})
    assert g is not None
    monkeypatch.setattr(gauntlet, "every", lambda: [g])
    session.menu.gauntlets, session.menu.gauntlet_pick = [g], 0
    _menu(session, "menu.press", key="gauntlet_pick_inc")
    res = _menu(session, "menu.press", key="run_start")
    if "game" not in res and res["menu"]["modal"]:
        res = _menu(session, "menu.press", key=res["menu"]["modal"]["go"]["key"])
    assert session.ui.run_id and session.ui.run_lives == 2 and session.ui.stage_label.endswith("· run ♥♥")
    first = gauntlet_run.active()
    assert first is not None and first.lives == 2
    # Lose the run's game: knocked out by hand.
    state, ui = session.state, session.ui
    state.winner = next(pid for pid in state.players if pid not in (0, ui.human_id))
    from starconquest import app
    app.record_run_step(state, ui, session.log)
    assert ui.run_note == "Run: lost · ♥ left" and ui.run_again and ui.run_on
    over = serial.over(state, ui)
    assert over["try_again"] and over["choose_stage"] and over["retry"] is False and not over["next_stage"]
    assert _menu(session, "ui.key", name="t")["action"] == "run_retry"
    res = _menu(session, "game.action", name="retry")              # no free-play Retry here
    assert session.ui is ui
    res = _menu(session, "game.action", name="run_retry")
    assert res["fresh"] and session.ui is not ui and session.ui.run_id == first.id
    again = gauntlet_run.active()
    assert again is not None and len(again.steps) == 2 and again.steps[-1].stage == 0
    # The same loss again from here is the last life; Choose a stage only while it goes on.
    session.ui.run_on = True
    res = _menu(session, "game.action", name="run_list")
    assert res["menu"]["menu"]["tutorial"] is not None and session.ui is None
    assert setupmenu.picked_gauntlet(session.menu).id == "lives"


def test_a_daily_link_fetches_the_day_onto_the_menu(session, games):
    from starconquest.settings import Settings as _S
    _call(session, "boot", page={**PAGE, "hash": "#daily"}, store={})
    reply = _call(session, "menu.open", first=True)
    assert reply["result"]["status"]["text"].startswith("Fetching the daily")
    [http] = [e for e in reply["effects"] if e["t"] == "http" and "daily" in e["url"]]
    body = json.dumps({"day": "2026-11-10", "kind": "board",
                       "settings": _S(players=3, nodes=14, seed=4242).to_dict()})
    pushed = _menu(session, "net.reply", req=http["req"], state="ok", body=body)["pushed"]
    assert pushed["menu"]["status"] is None and session.settings.seed == 4242
    assert pushed["menu"]["menu"]["subtitle"][0]["text"] == "DAILY MAP #14 · Tue 10 Nov"
    _menu(session, "game.start")
    assert session.ui.daily_day == "2026-11-10"


def test_the_daily_buttons_fetch_and_a_missing_day_says_so(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    reply = _call(session, "menu.press", key="puzzle")
    [http] = [e for e in reply["effects"] if e["t"] == "http"]
    assert "puzzle" in reply["result"]["status"]["text"]
    pushed = _menu(session, "net.reply", req=http["req"], state="missing", body="")["pushed"]
    assert not pushed["menu"]["status"]["ok"]


def test_a_gauntlet_link_is_fetched_kept_and_opened_on_the_list(session, games):
    _call(session, "boot", page={**PAGE, "hash": "#g=mine-0123456789ab"}, store={})
    reply = _call(session, "menu.open", first=True)
    assert "resume" not in reply["result"]
    [http] = [e for e in reply["effects"] if e["t"] == "http"]
    body = json.dumps({"gauntlet": _chain_data("mine-0123456789ab")})
    pushed = _menu(session, "net.reply", req=http["req"], state="ok", body=body)["pushed"]
    tut = pushed["menu"]["menu"]["tutorial"]
    assert tut is not None and tut["graph"]["nodes"][0]["title"] == "Stage 0"
    assert tut["pick"]["edit"]["key"] == "stage_edit"     # stored on the board: it can be edited there
    assert any(b["key"] == "tutorial_copy" for b in tut["buttons"])
    reply = _call(session, "menu.press", key="tutorial_copy")
    assert any(e["t"] == "clipboard" and "#gauntlet=" in e["text"] for e in reply["effects"])


def test_a_spec_link_generates_a_gauntlet_a_slice_at_a_time(session, games):
    from starconquest import gauntletgen
    spec = {"stages": 2, "seed": 9, "candidates": 2, "rules": {"players": {"fix": 2}, "systems": {"range": [8, 10]}},
            "opponents": ["rusherplus", "heuristic"]}
    _call(session, "boot", page={**PAGE, "hash": "#" + gauntletgen.spec_fragment(spec)}, store={})
    res = _menu(session, "menu.open", first=True)
    assert res["generating"] and res["status"]["text"].startswith("Generating a gauntlet")
    for _ in range(2000):
        res = _menu(session, "menu.generate")
        if not res.get("generating"):
            break
    assert not res.get("generating")
    tut = res["menu"]["tutorial"]
    assert tut is not None and len(tut["graph"]["nodes"]) >= 1
    assert tut["pick"]["edit"] is None      # generated here, never stored on the board


# --------------------------------------------------------------------------- #
# The map maker (`maker`, `Session.op_maker_*`)
def _maker(session) -> dict:
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    res = _menu(session, "menu.press", key="create_map")
    assert res["action"] == "create_map"
    return res["maker"]


def test_create_map_opens_a_blank_canvas_as_plain_json(session):
    view = _maker(session)
    assert json.loads(json.dumps(view)) == view
    assert view["nodes"] == [] and view["tool"] == "systems" and not view["can_play"]
    assert view["palette"]["kind"] == "production" and view["palette"]["entries"][0]["glyph"] == "?"
    assert [b[0] for b in view["footer"]][-1] == "play" and view["footer"][-1][2] == "dead"
    assert view["problems"]["heading"].endswith("problems") or view["problems"]["heading"].endswith("problem")


def test_systems_are_placed_linked_moved_and_deleted_with_undo(session):
    _maker(session)
    res = _menu(session, "maker.place", x=300, y=300)
    assert res["armed"] == 0 and len(res["maker"]["nodes"]) == 1
    # shift chains a lane from the system placed last
    res = _menu(session, "maker.place", x=600, y=300, shift=True)
    view = res["maker"]
    assert res["armed"] == 1 and view["lanes"] and view["lanes"][0][:2] == [0, 1]
    assert view["sel_node"] == 1
    # too close to another system: refused, said
    res = _menu(session, "maker.place", x=605, y=305)
    assert res["armed"] is None and not res["maker"]["status"]["ok"]
    # a move-drag is one undo, and Delete removes the system and its lane
    _menu(session, "maker.node", node=1)
    _menu(session, "maker.drag", x=700, y=400)
    view = _menu(session, "maker.release")["maker"]
    assert view["nodes"][1][:2] == [700, 400]
    view = _menu(session, "maker.key", name="Delete")["maker"]
    assert len(view["nodes"]) == 1 and view["lanes"] == []
    view = _menu(session, "maker.key", name="z", ctrl=True)["maker"]
    assert len(view["nodes"]) == 2
    view = _menu(session, "maker.control", key="undo")["maker"]
    assert view["nodes"][1][:2] == [600, 300]
    view = _menu(session, "maker.control", key="redo")["maker"]
    assert view["nodes"][1][:2] == [700, 400]


def test_the_lanes_tool_lays_and_picks_lanes(session):
    _maker(session)
    for x in (300, 600, 900):
        _menu(session, "maker.place", x=x, y=500)
    _menu(session, "maker.control", key="tool_lanes")
    _menu(session, "maker.node", node=0)          # arm
    view = _menu(session, "maker.node", node=1)["maker"]   # tap-tap lays it
    assert [0, 1] == view["lanes"][0][:2] and view["lane_src"] is None
    _menu(session, "maker.node", node=1)
    view = _menu(session, "maker.lane", target=2)["maker"]  # a drag let go over #2
    assert len(view["lanes"]) == 2
    res = _menu(session, "maker.miss", lanes=[1])
    assert res["maker"]["sel_lane"] == 1 and not res["pan"]
    assert _menu(session, "maker.miss", lanes=[])["pan"]
    assert any(b["kind"] == "check" for b in res["maker"]["side"])


def test_owners_paint_and_a_problem_blocks_play_until_fixed(session):
    _maker(session)
    for x, y in ((300, 300), (700, 300), (500, 700)):
        _menu(session, "maker.place", x=x, y=y)
    _menu(session, "maker.control", key="tool_owners")
    view = _menu(session, "maker.node", node=0)["maker"]
    assert view["nodes"][0][4] == 1 and view["palette"]["kind"] == "seats"
    # no lanes, one seat: Play refuses, says why and frames the offender
    res = _menu(session, "maker.control", key="play")
    assert "maker" in res and not res["maker"]["status"]["ok"]
    assert _menu(session, "maker.miss")["box"]   # off every system: drops the selection, arms a box
    _menu(session, "maker.control", key="seat_2")
    _menu(session, "maker.box", nodes=[1])
    _menu(session, "maker.control", key="auto_lanes")
    view = _menu(session, "maker.key", name="y")["maker"]   # the confirm's yes
    assert view["lanes"] and view["confirm"] is None
    assert view["can_play"] and view["footer"][-1][2] == "blue"
    res = _menu(session, "maker.control", key="play")
    game = res["game"]
    assert len(game["static"]["players"]) == 3 and len(game["static"]["systems"]) == 3
    assert session.settings.custom_map is not None and session.maker is None


def test_menu_keeps_the_map_and_a_slider_writes_the_setup(session):
    _maker(session)
    _menu(session, "maker.place", x=300, y=300)
    _menu(session, "maker.slide", key="adv_home_ships", t=1.0, phase="end")
    assert session.settings.home_start_ships == 50
    _menu(session, "maker.node", node=0)
    _menu(session, "maker.slide", key="ships_slider", t=0.5, phase="start")
    view = _menu(session, "maker.slide", key="ships_slider", t=1.0, phase="end")["maker"]
    assert view["nodes"][0][3] == 50
    view = _menu(session, "maker.key", name="z", ctrl=True)["maker"]   # the whole drag is one undo
    assert view["nodes"][0][3] != 50
    res = _menu(session, "maker.key", name="Escape")
    assert "back" in res and session.settings.custom_map is not None
    assert len(session.settings.custom_map.nodes) == 1


def test_a_pinchs_first_finger_is_put_back(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True, touch=True)
    _menu(session, "menu.press", key="create_map")
    _menu(session, "maker.place", x=300, y=300)
    _menu(session, "maker.place", x=600, y=300)
    view = _menu(session, "maker.unpress")["maker"]
    assert len(view["nodes"]) == 1
    view = _menu(session, "maker.key", name="z", ctrl=True)["maker"]
    assert view["nodes"] == []


def test_every_maker_parity_recipe_loads(session):
    _call(session, "boot", page=PAGE, store={})
    recipes = json.loads((Path(__file__).parent / "fixtures" / "parity_screens.json").read_text())["screens"]
    for recipe in (r for r in recipes if r["surface"] == "maker"):
        view = _menu(session, "parity.maker", recipe=recipe)["maker"]
        assert (len(view["nodes"]) > 0) == (recipe["name"] == "maker-map")


# --------------------------------------------------------------------------- #
# Files, quitting and links opened in an open page (phase 7)
def test_the_setup_is_saved_as_a_file_without_its_challenge(session, games):
    from starconquest import files
    from starconquest.settings import Challenge
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True, prefill={"players": 3, "seed": 9})
    session.settings.challenge = Challenge(turns=12, lost=3, hand=12, key=session.settings.challenge_key(), log="")
    assert _menu(session, "menu.press", key="files_menu")["menu"]["lists"]["files"]["open"]
    reply = _call(session, "menu.press", key="save_settings")
    [dl] = _effects(reply, "download")
    assert dl["name"] == "starconquest_settings.json" and dl["type"] == "application/json"
    saved = json.loads(dl["text"])
    assert saved["players"] == 3 and saved["seed"] == 9 and not saved.get("challenge")
    assert reply["result"]["status"]["text"] == "Saved starconquest_settings.json"
    assert not reply["result"]["menu"]["lists"]["files"]["open"]
    assert files.read(dl["text"]).kind == "setup"


def test_a_setup_file_loads_onto_the_menu(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    text = json.dumps({"players": 5, "nodes": 30, "seed": 77})
    res = _menu(session, "file.open", name="mine.json", text=text)
    rows = {r["label"]: r["control"] for r in res["menu"]["panel"] if r["kind"] == "row"}
    assert rows["Players"]["value"] == "5" and rows["Seed"]["text"] == "77"
    assert res["status"]["text"] == "Loaded mine.json"
    bad = _menu(session, "file.open", name="x.json", text="[1, 2]")
    assert bad["status"]["text"] == "Couldn't open x.json: not a Star Conquest file" and not bad["status"]["ok"]


def test_the_last_match_is_saved_and_opens_again_to_watch(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    assert _menu(session, "menu.press", key="save_game")["status"]["text"] == "No match to save yet"
    _menu(session, "game.start", settings={"players": 2, "nodes": 8, "autoplay": True}, seed=3)
    for _ in range(3):
        _menu(session, "turn.end")
    _menu(session, "menu.open")
    [dl] = _effects(_call(session, "menu.press", key="save_game"), "download")
    assert dl["name"] == f"game_{session_log_id(games)}.json"
    res = _menu(session, "file.open", name=dl["name"], text=dl["text"])
    assert res["game"]["view"]["history"]["turn"] == 0 and session.ui.watched


def session_log_id(games) -> str:
    [path] = list(games.glob("game_*.json"))
    return json.loads(path.read_text())["match_id"]


def test_a_map_file_opens_on_the_menu_and_in_the_maker(session, games):
    from starconquest.custommap import CustomMap, MapNode
    recipe = CustomMap(nodes=[MapNode(100, 100, 2, 10, 1), MapNode(400, 100, 2, 10, 2),
                              MapNode(250, 300, 1, 5, 0)], lanes=[(0, 1), (1, 2), (0, 2)])
    text = json.dumps(recipe.to_dict())
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    res = _menu(session, "file.open", name="tri.json", text=text)
    assert res["menu"]["actions"]["create"] == "Edit map" and session.settings.custom_map is not None
    view = _menu(session, "menu.press", key="create_map")["maker"]
    assert len(view["nodes"]) == 3
    assert [f[0] for f in view["footer"]][-3:] == ["export", "import", "play"]
    reply = _call(session, "maker.control", key="export")
    [dl] = _effects(reply, "download")
    assert dl["name"] == "map.json" and json.loads(dl["text"]) == recipe.to_dict()
    _menu(session, "maker.control", key="new")
    _menu(session, "maker.confirm", yes=True)
    view = _menu(session, "file.open", name="tri.json", text=text, where="maker")["maker"]
    assert len(view["nodes"]) == 3 and view["status"]["text"] == "Opened tri.json"
    view = _menu(session, "file.open", name="s.json", text=json.dumps({"players": 2}), where="maker")["maker"]
    assert "isn't a map" in view["status"]["text"]


def test_quit_asks_first_then_asks_the_browser_to_close(session, games):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    res = _menu(session, "menu.key", name="Escape")
    modal = res["menu"]["modal"]
    assert modal["kind"] == "quit" and modal["title"] == "Quit Star Conquest?"
    assert _menu(session, "menu.key", name="Escape")["menu"]["modal"] is None   # Cancel
    _menu(session, "menu.press", key="quit")
    reply = _call(session, "menu.press", key="quit_yes")
    assert _effects(reply, "close") and reply["result"]["action"] == "quit"
    assert reply["result"]["status"]["text"].startswith("Close the tab")


def test_the_quit_confirm_over_a_game_leaves_it_for_the_menu(session, games):
    _human_game(session)
    modal = _menu(session, "game.action", name="quit")["modal"]
    assert modal["title"] == "Quit Star Conquest?" and modal["then"] == "quit_confirmed" and modal["tone"] == "red"
    reply = _call(session, "game.action", name="quit_confirmed")
    assert _effects(reply, "close") and reply["result"]["quit"]
    assert reply["result"]["menu"]["status"]["text"].startswith("Close the tab") and session.state is None


def test_a_link_opened_in_an_open_page_is_read_as_at_boot(session, games):
    from starconquest.settings import Settings
    blob, match_id = _played_log()
    hostio.reset()
    _human_game(session)
    assert _menu(session, "page.hash", hash="") == {}
    assert session.state is not None   # a blank fragment leaves the game alone
    reply = _call(session, "page.hash", hash=f"#log={match_id}")
    assert session.state is None and reply["result"]["menu"]["status"]["text"] == "Loading replay..."
    [http] = [e for e in _effects(reply, "http") if f"id={match_id}" in e["url"]]
    game = _menu(session, "net.reply", req=http["req"], state="ok", body=blob)["pushed"]["game"]
    assert game["view"]["history"]["turn"] == 0
    token = Settings.from_dict({"players": 4, "seed": 123}).to_token()
    res = _menu(session, "page.hash", hash=f"#{token}")["menu"]
    rows = {r["label"]: r["control"] for r in res["menu"]["panel"] if r["kind"] == "row"}
    assert rows["Players"]["value"] == "4" and rows["Seed"]["text"] == "123"


def test_a_phones_share_or_save_list_is_get_link_and_the_files_list(session, games):
    """The phone grid folds Get Link into Files (`setupmenu.SHARE_SAVE_ITEMS`),
    opened and closed by the one Files press."""
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    m = _menu(session, "menu.press", key="files_menu")["menu"]
    assert m["actions"]["share_save"] == "Share or save"
    keys = [i["key"] for i in m["lists"]["share_save"]["items"]]
    assert keys == ["get_link"] + [i["key"] for i in m["lists"]["files"]["items"]]
    assert m["lists"]["share_save"]["open"]
    reply = _call(session, "menu.press", key="get_link")
    assert not reply["result"]["menu"]["lists"]["share_save"]["open"]
