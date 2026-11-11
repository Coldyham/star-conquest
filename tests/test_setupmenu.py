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
