"""The setup menu apart from its drawing (`setupmenu`): what a press means, and
the view the HTML build draws. The pygame menu's own tests (`test_menu.py`)
drive the same functions through its widgets."""

from __future__ import annotations

import json

import pytest

from starconquest import ai, config, setupmenu
from starconquest.custommap import CustomMap, MapNode
from starconquest.model import AiParams
from starconquest.settings import Challenge, Settings


@pytest.fixture
def menu():
    ai.load_models()
    return setupmenu.Menu()


def _say():
    said: list[tuple[str, bool]] = []
    return said, lambda text, ok: said.append((text, ok))


def _drawn() -> Settings:
    s = Settings()
    s.custom_map = CustomMap(nodes=[MapNode(200, 200, 3, 10, 1), MapNode(600, 600, 4, 8, 2)],
                             lanes=[(0, 1)]).normalised()
    s.players, s.nodes = 2, 2
    return s


@pytest.mark.parametrize("tab", [key for key, _label in setupmenu.TABS])
@pytest.mark.parametrize("make", [Settings, _drawn])
def test_every_tab_views_as_plain_json(menu, tab, make):
    settings = make()
    menu.tab = tab
    view = setupmenu.view(menu, settings)
    assert json.loads(json.dumps(view)) == view
    assert [t["active"] for t in view["tabs"]] == [key == tab for key, _ in setupmenu.TABS]
    assert view["panel"], tab


def test_a_drawn_map_reports_what_it_decides_rather_than_offering_it(menu):
    rows = {r["label"]: r["control"] for r in setupmenu.view(menu, _drawn())["panel"] if r["kind"] == "row"}
    assert rows["Players"] == {"kind": "derived", "text": "2 (from the map)"}
    assert rows["Map type"]["kind"] == "derived"


def test_steppers_toggles_and_tabs_change_the_setup(menu):
    settings = Settings()
    said, say = _say()
    for key in ("players_inc", "nodes_inc", "mode_symmetric", "autoplay", "fog_of_war", "tab_combat"):
        assert setupmenu.press(menu, settings, key, say)
    assert settings.players == Settings().players + 1
    assert settings.mode == "symmetric" and settings.autoplay
    assert not setupmenu.fog_off(settings)
    assert menu.tab == "combat"
    assert setupmenu.tab_changed("basic", settings)
    assert not setupmenu.press(menu, settings, "start", say), "Start is the shell's"
    assert said == []


def test_a_new_strategy_starts_at_its_own_stop_and_copy_reset_reach_every_seat(menu):
    settings = Settings()
    _said, say = _say()
    menu.ai_seat = 2
    settings.ai[1] = AiParams(aux=3.0)
    i = menu.strategies.index("marshal")
    setupmenu.press(menu, settings, f"strategy_opt_{i}", say)
    assert settings.ai_strategy[1] == "marshal" and settings.ai[1].aux == config.AI_AUX
    setupmenu.press(menu, settings, "copy_all", say)
    assert all(settings.ai_strategy[s - 1] == "marshal" for s in setupmenu.ai_seats(settings))
    setupmenu.press(menu, settings, "reset_all", say)
    assert not setupmenu.tab_changed("ai", settings)


def test_a_slider_snaps_to_its_step_and_the_demo_never_touches_the_setup(menu):
    settings = Settings()
    setupmenu.apply_slider("ai_reserve_floor", menu, settings, 0.52)
    assert settings.ai[menu.ai_seat - 1].reserve_floor == 10
    before = settings.to_dict()
    setupmenu.apply_slider("preview_attacker", menu, settings, 1.0)
    assert menu.preview_attacker == config.COMBAT_PREVIEW_MAX
    assert settings.to_dict() == before
    setupmenu.apply_slider("adv_fog_sight", menu, settings, 1.0)
    fog = next(i for i in setupmenu.view(menu, settings)["panel"] if i["kind"] == "checks")["left"][1]
    assert fog["key"] == "fog_of_war" and fog["on"] is False


def test_the_seed_field_takes_digits_up_to_the_newest_era(menu):
    settings = Settings()
    assert setupmenu.set_seed_text(menu, settings, "12a3") == "123" and settings.seed == 123
    assert setupmenu.set_seed_text(menu, settings, "") == "" and settings.seed is None
    top = str(setupmenu.seed_top())
    assert int(setupmenu.set_seed_text(menu, settings, "9" * len(top))) <= setupmenu.seed_top()


def test_a_refused_preference_says_so(menu, monkeypatch):
    settings = Settings()
    said, say = _say()
    monkeypatch.setitem(setupmenu.PREFERENCES, "share_games",
                        (lambda: False, lambda on: False, "nope"))
    setupmenu.press(menu, settings, "share_games", say)
    assert said == [("nope", False)]


def test_moving_off_a_challenge_asks_and_keeping_it_puts_the_setup_back(menu):
    settings = Settings(seed=5)
    settings.challenge = Challenge(turns=9, lost=1, key=settings.challenge_key())
    _said, say = _say()
    setupmenu.before_edit(menu, settings)
    setupmenu.press(menu, settings, "players_inc", say)
    setupmenu.after_edit(menu, settings)
    assert menu.confirm_unchallenge
    assert setupmenu.modal(menu, settings, touch=True)["stay"]["label"] == "Keep the challenge"
    assert setupmenu.answer_unchallenge(menu, settings, False, say) is None
    assert settings.players == Settings().players and settings.challenge is not None


def test_a_slow_setup_asks_once_then_plays(menu, monkeypatch):
    settings = Settings()
    _said, say = _say()
    monkeypatch.setattr(setupmenu, "setup_warnings", lambda s, people=None: ["Seat 2 is slow."])
    assert setupmenu.start(menu, settings, "start", say) is None
    assert setupmenu.modal(menu, settings, touch=False)["title"] == "This setup may play slowly"
    assert setupmenu.answer_slow(menu, True) == "start"
    assert setupmenu.start(menu, settings, "start", say) == "start", "asked once per warning"


def test_survey_measures_once_and_waits_for_a_drag(menu):
    settings = Settings()
    assert not setupmenu.surveyed(menu, settings)
    menu.drag_key = "adv_ship_speed"
    assert not setupmenu.survey(menu, settings)
    menu.drag_key = None
    assert setupmenu.survey(menu, settings, sketch=True)
    assert setupmenu.surveyed(menu, settings, sketch=True)
    assert not setupmenu.survey(menu, settings, sketch=True)
    view = setupmenu.view(menu, settings)
    assert view["sketch"]["kind"] == "sample" and view["sketch"]["homes"]


# --------------------------------------------------------------------------- #
# The gauntlet list's graph (`graph_view`, `pick_view`, `step_pick`)
# --------------------------------------------------------------------------- #
def _forked(bests: dict[int, tuple[int, int]]):
    """0 → 1 → 3, 0 → 2 → 3 (a fork that rejoins), and a hidden bonus 3 → 4
    behind 10 turns; ``bests`` stands in for the player's wins."""
    from starconquest import gauntlet
    g = gauntlet.Gauntlet.from_dict({"id": "fork", "name": "Fork", "stages": [
        {"id": i, "title": f"S{i}", "settings": Settings(players=2, nodes=8, seed=40 + i).to_dict()}
        for i in range(5)],
        "lanes": [[0, 1], [0, 2], [1, 3], [2, 3], [3, 4, {"turns": 10, "hidden": True}]]})
    ms = setupmenu.Menu()
    ms.gauntlets, ms.tutorial_open = [g], True
    ms.stage_bests = {"fork": bests}
    return ms, g


def test_the_graph_draws_each_shown_stage_at_its_cell_and_hides_an_unmet_bonus():
    ms, g = _forked({0: (20, 5)})
    view = setupmenu.graph_view(ms, Settings(), g)
    assert json.loads(json.dumps(view)) == view
    cells = {n["key"]: (n["col"], n["row"]) for n in view["nodes"]}
    assert cells == {"stage_0": (0, 0), "stage_1": (1, 0), "stage_2": (1, 1), "stage_3": (2, 0)}
    # The corner reads column-row, the row only where a column holds more than one.
    assert [n["place"] for n in view["nodes"]] == ["1", "2-1", "2-2", "3"]
    assert [(n["key"], n["open"]) for n in view["nodes"]] == [
        ("stage_0", True), ("stage_1", True), ("stage_2", True), ("stage_3", False)]
    assert "start" in view["nodes"][0]["marks"] and "goal" in view["nodes"][3]["marks"]
    assert {(lane["from"], lane["to"]) for lane in view["lanes"]} == {
        ("stage_0", "stage_1"), ("stage_0", "stage_2"), ("stage_1", "stage_3"), ("stage_2", "stage_3")}
    # Selected: the first open stage not yet cleared.
    assert view["focus"] == "stage_1" and "picked" in view["nodes"][1]["marks"]


def test_a_hidden_lane_is_drawn_once_met():
    ms, g = _forked({0: (20, 5), 1: (20, 5), 3: (12, 0)})
    view = setupmenu.graph_view(ms, Settings(), g)
    assert not any(lane["to"] == "stage_4" for lane in view["lanes"])     # 12 turns: not met
    assert "stage_4" not in {n["key"] for n in view["nodes"]}
    ms, g = _forked({0: (20, 5), 1: (20, 5), 3: (9, 0)})
    view = setupmenu.graph_view(ms, Settings(), g)
    [bonus] = [lane for lane in view["lanes"] if lane["to"] == "stage_4"]
    assert [n["place"] for n in view["nodes"]][-1] == "4"
    assert bonus["gated"] and bonus["hidden"] and bonus["short"] == "target 10"
    ms.stage_pick = 3
    lines = [line["text"] for line in setupmenu.pick_view(ms, Settings(), g, web=True)["lines"]]
    assert "Bonus to S4: Win in 10 turns or fewer" in lines


def test_a_tap_selects_and_a_second_plays_but_never_a_locked_stage():
    ms, g = _forked({0: (20, 5)})
    began: list[str] = []
    settings = Settings()
    press = lambda key: setupmenu.tutorial_press(ms, settings, key, True, _say()[1],
                                                 lambda action: began.append(action) or action)
    assert press("stage_3") is None and ms.stage_pick == 3 and not began   # locked: selected only
    assert setupmenu.pick_view(ms, settings, g, True)["play"]["enabled"] is False
    assert press("stage_3") is None and ms.tutorial_open
    assert press("stage_2") is None and ms.stage_pick == 2
    assert press("stage_2") == "start" and began == ["start"] and not ms.tutorial_open
    assert ms.stage_at == ("fork", 2)


def test_the_arrows_step_by_cell_and_stop_at_an_edge():
    ms, g = _forked({0: (20, 5)})
    settings = Settings()
    ms.stage_pick = 0
    for name, want in [("ArrowRight", 1), ("ArrowDown", 2), ("ArrowDown", 2), ("ArrowRight", 3),
                       ("ArrowRight", 3), ("ArrowLeft", 1), ("ArrowLeft", 0), ("ArrowUp", 0)]:
        setupmenu.step_pick(ms, settings, g, *setupmenu.STEPS[name])
        assert ms.stage_pick == want, name


def test_only_a_gauntlet_the_board_stored_offers_edit_on_the_board():
    from starconquest import gauntlet
    stored = gauntlet.Gauntlet.from_dict({"id": "hard-mode-0123456789ab", "stages": [{"id": 0, "settings": {}}]})
    made = gauntlet.Gauntlet.from_dict({"id": "gen-0123456789ab", "stages": [{"id": 0, "settings": {}}]})
    assert gauntlet.on_board(stored) and not gauntlet.on_board(made)
    assert setupmenu.board_draft(stored).endswith("/board/gauntlet.html#hard-mode-0123456789ab")
    assert setupmenu.board_draft(made) == ""
    ms, g = _forked({})
    assert setupmenu.pick_view(ms, Settings(), g, True)["edit"] is None
