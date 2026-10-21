"""Gauntlets: the tutorial's file, the tolerant reader, and progress read off
local bests (`starconquest/gauntlet.py`, `docs/design/gauntlet.md`)."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from starconquest import gauntlet, webstore
from starconquest.settings import Settings, build_state


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")


def _tutorial() -> gauntlet.Gauntlet:
    g = gauntlet.find(gauntlet.builtin(), "tutorial")
    assert g is not None
    return g


def _win(stage: gauntlet.Stage) -> None:
    keys = gauntlet.stage_settings(stage).challenge_keys()
    webstore.record_best(keys[0], 40, 5, *keys[1:])


def _setup(seed: int) -> dict:
    return Settings(players=2, nodes=8, seed=seed).to_dict()


def test_the_tutorial_ships_and_comes_first():
    g = gauntlet.builtin()[0]
    assert g.id == "tutorial"
    assert len(g.stages) >= 8
    assert g.start == (g.stages[0].id,)


def test_from_dict_never_raises_on_junk():
    for junk in (None, 3, "x", [], {}, {"stages": "no"}, {"stages": [None, 1, "x"]},
                 {"stages": [{"id": "a", "settings": {}}]}, {"stages": [{"id": 0}]},
                 {"stages": [{"id": 0, "settings": {}}], "lanes": [[0, 9], "x", [0], [True, 0]]},
                 {"stages": [{"id": 0, "settings": {}, "briefing": 5, "hint": ["no"]}]}):
        gauntlet.Gauntlet.from_dict(junk)     # never raises


def test_from_dict_drops_bad_stages_and_lanes_and_clamps_text():
    g = gauntlet.Gauntlet.from_dict({
        "id": "mine", "name": "Mine",
        "stages": [
            {"id": 0, "title": "One", "settings": _setup(1), "hint": "h" * 500,
             "briefing": ["a", "", "b"] + ["c"] * 20},
            {"id": "two", "settings": _setup(2)},          # no int id
            {"id": 1, "settings": "no"},                   # no settings dict
            {"id": 0, "settings": _setup(3)},              # duplicate id
            {"id": 2, "settings": _setup(4)},
        ],
        "lanes": [[0, 2], [0, 7], [2, 2], [2, 0]],
    })
    assert g is not None
    assert [s.id for s in g.stages] == [0, 2]
    assert g.lanes == ((0, 2),)
    first = g.stages[0]
    assert len(first.hint) == gauntlet.HINT_MAX
    assert first.briefing[:2] == ("a", "b")
    assert len(first.briefing) == gauntlet.BRIEFING_MAX_LINES
    assert g.stages[1].title == "Stage 2"


def test_missing_lanes_join_the_stages_in_list_order():
    g = gauntlet.Gauntlet.from_dict({"stages": [{"id": i, "settings": _setup(i)} for i in (5, 3, 9)]})
    assert g is not None
    assert g.lanes == ((5, 3), (3, 9))
    assert g.start == (5,)
    assert gauntlet.next_stage(g, 5) == 3
    assert gauntlet.next_stage(g, 3) == 9
    assert gauntlet.next_stage(g, 9) is None


def test_every_builtin_stage_pins_every_settings_field():
    """A later change to a `config` default cannot quietly change the tutorial:
    every field is written out, and the file is what `from_dict` gives back."""
    names = {f.name for f in fields(Settings)}
    for g in gauntlet.builtin():
        for stage in g.stages:
            assert set(stage.settings) >= names, f"{stage.title}: unpinned {names - set(stage.settings)}"
            assert Settings.from_dict(stage.settings).to_dict() == stage.settings, stage.title


def test_every_builtin_stage_has_a_fixed_seed_and_builds():
    for g in gauntlet.builtin():
        keys = set()
        for stage in g.stages:
            cfg = gauntlet.stage_settings(stage)
            assert isinstance(cfg.seed, int), stage.title
            assert cfg.challenge is None and not cfg.autoplay
            assert cfg.custom_map is None or not cfg.custom_map.blockers()
            state = build_state(cfg, cfg.seed)
            assert state.human() is not None
            keys.add(cfg.challenge_key())
            assert stage.hint and len(stage.hint) <= gauntlet.HINT_MAX
            assert stage.briefing
        assert len(keys) == len(g.stages), "two stages are the same setup"


def test_the_tutorial_file_is_the_format_documented():
    """What `docs/design/gauntlet.md` tells an author to write: the reader keeps
    every stage of the shipped file, so nothing in it is being silently dropped."""
    raw = json.loads((gauntlet._ASSETS / "tutorial.json").read_text())
    assert len(_tutorial().stages) == len(raw["stages"])
    assert len(_tutorial().lanes) == len(raw["lanes"])


def test_a_win_on_a_stage_opens_the_next_and_no_further():
    g = _tutorial()
    ids = [s.id for s in g.stages]
    assert gauntlet.open_stages(g) == {ids[0]}
    _win(g.stages[0])
    _win(g.stages[1])
    assert gauntlet.cleared(g) == {ids[0], ids[1]}
    assert gauntlet.open_stages(g) == {ids[0], ids[1], ids[2]}
    assert gauntlet.bests(g)[ids[1]] == (40, 5)


def test_a_best_on_some_other_setup_clears_nothing():
    g = _tutorial()
    cfg = gauntlet.stage_settings(g.stages[0])
    cfg.home_production += 1
    webstore.record_best(cfg.challenge_key(), 10, 0)
    assert gauntlet.cleared(g) == set()


def test_a_fork_opens_both_branches_and_has_no_single_next_stage():
    g = gauntlet.Gauntlet.from_dict({
        "id": "fork", "start": [0],
        "stages": [{"id": i, "settings": _setup(100 + i)} for i in range(4)],
        "lanes": [[0, 1], [0, 2], [1, 3], [2, 3]],
    })
    assert g is not None
    assert gauntlet.next_stage(g, 0) is None
    assert gauntlet.next_stage(g, 1) == 3
    _win(g.stages[0])
    assert gauntlet.open_stages(g) == {0, 1, 2}
    _win(g.stages[2])
    assert gauntlet.open_stages(g) == {0, 1, 2, 3}


def test_next_stage_is_offered_when_replaying_a_cleared_stage():
    g = _tutorial()
    first, second = g.stages[0], g.stages[1]
    _win(first)
    _win(second)
    assert gauntlet.next_stage(g, first.id) == second.id


def test_stage_of_matches_by_key_and_lets_go_after_one_knob():
    g = _tutorial()
    stage = g.stages[3]
    cfg = gauntlet.stage_settings(stage)
    assert gauntlet.stage_of(g, cfg) == stage.id
    cfg.autoplay = True                      # not part of the setup's key
    assert gauntlet.stage_of(g, cfg) == stage.id
    cfg.garrison_base += 1
    assert gauntlet.stage_of(g, cfg) is None


def test_a_user_gauntlet_is_read_on_desktop_only(monkeypatch, tmp_path):
    folder = tmp_path / "gauntlets"
    folder.mkdir()
    (folder / "mine.json").write_text(json.dumps(
        {"id": "mine", "name": "Mine", "stages": [{"id": 0, "settings": _setup(7)}]}))
    (folder / "broken.json").write_text("{not json")
    (folder / "clash.json").write_text(json.dumps(
        {"id": "tutorial", "stages": [{"id": 0, "settings": _setup(8)}]}))
    monkeypatch.setattr(gauntlet, "user_dir", lambda: folder)
    monkeypatch.setattr(gauntlet.paths, "is_web", lambda: False)
    assert [g.id for g in gauntlet.user()] == ["mine"]
    assert [g.id for g in gauntlet.every()][0] == "tutorial"
    monkeypatch.setattr(gauntlet.paths, "is_web", lambda: True)
    assert gauntlet.user() == []
