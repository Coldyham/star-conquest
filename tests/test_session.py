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


def test_quit_and_later_screens_say_why_they_stay(session):
    _call(session, "boot", page=PAGE, store={})
    _menu(session, "menu.open", first=True)
    res = _menu(session, "menu.press", key="create_map")
    assert res["action"] is None and not res["status"]["ok"]
    res = _menu(session, "menu.key", name="Escape")
    assert res["action"] == "quit" and res["status"]["text"].startswith("Close the tab")
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
    assert tut["heading"] and tut["stages"][0]["open"] and tut["buttons"][-1]["key"] == "tutorial_close"
    first = tut["stages"][0]["key"]
    assert _menu(session, "menu.press", key="get_link")["menu"]["tutorial"] is not None   # modal: ignored
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


def test_a_daily_link_fetches_the_day_onto_the_menu(session, games):
    from starconquest.settings import Settings as _S
    _call(session, "boot", page={**PAGE, "hash": "#daily"}, store={})
    reply = _call(session, "menu.open", first=True)
    assert reply["result"]["status"]["text"].startswith("Fetching the daily")
    [http] = [e for e in reply["effects"] if e["t"] == "http" and "daily" in e["url"]]
    body = json.dumps({"day": "2026-11-10", "kind": "board",
                       "settings": _S(players=3, nodes=14, seed=4242).to_dict()})
    pushed = _menu(session, "net.reply", req=http["req"], state="ok", body=body)["pushed"]
    assert pushed["menu"]["status"]["ok"] and session.settings.seed == 4242
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
    assert tut is not None and tut["stages"][0]["title"].strip().endswith("Stage 0")
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
    assert tut is not None and len(tut["stages"]) >= 1
