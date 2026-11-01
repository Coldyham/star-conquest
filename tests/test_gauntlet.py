"""Gauntlets: the tutorial's file, the tolerant reader, and progress read off
local bests (`starconquest/gauntlet.py`, `docs/design/gauntlet.md`)."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

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


def test_a_lane_opens_only_its_second_stage_unless_two_way():
    """Clearing a later stage first (a challenge link of the same setup counts)
    opens the way on, never the stage before it."""
    g = _tutorial()
    ids = [s.id for s in g.stages]
    _win(g.stages[5])
    assert gauntlet.open_stages(g) == {ids[0], ids[5], ids[6]}

    g = gauntlet.Gauntlet.from_dict({
        "stages": [{"id": i, "settings": _setup(200 + i)} for i in range(3)],
        "lanes": [[0, 1, {"two_way": True}], [1, 2, {"two_way": "yes"}]],
    })
    assert g is not None
    assert g.two_way == {(0, 1)}
    _win(g.stages[1])
    assert gauntlet.open_stages(g) == {0, 1, 2}
    webstore.set(webstore.WEB_BESTS_KEY, "{}")
    _win(g.stages[2])
    assert gauntlet.open_stages(g) == {0, 2}


def _targeted(target) -> gauntlet.Gauntlet:
    g = gauntlet.Gauntlet.from_dict({
        "stages": [{"id": 0, "settings": _setup(400), "target": target},
                   {"id": 1, "settings": _setup(401)}, {"id": 2, "settings": _setup(402)}]})
    assert g is not None
    return g


def _score(stage: gauntlet.Stage, turns: int, lost: int) -> None:
    keys = gauntlet.stage_settings(stage).challenge_keys()
    webstore.record_best(keys[0], turns, lost, *keys[1:])


@pytest.mark.parametrize("raw", [5, "14", {}, {"turns": 0}, {"turns": "14"}, {"turns": True},
                                 {"turns": 14, "lost": -1}, {"turns": 14, "lost": None},
                                 {"turns": 14.0}])
def test_a_malformed_target_is_dropped_and_the_stage_kept(raw):
    g = _targeted(raw)
    assert len(g.stages) == 3 and g.stages[0].target is None


def test_a_target_is_read_with_its_bot_and_beat():
    t = _targeted({"turns": 31, "lost": 12, "bot": "thinker", "beat": True, "x": 1}).stages[0].target
    assert t == gauntlet.Target(turns=31, lost=12, bot="thinker", beat=True)
    assert t.text() == "Beat thinker: 31 turns / 12 lost"
    t = _targeted({"turns": 14, "beat": "yes"}).stages[0].target
    assert t == gauntlet.Target(turns=14) and t.text() == "Win in 14 turns or fewer"


@pytest.mark.parametrize("target, score, met", [
    ({"turns": 14}, (14, 99), True),
    ({"turns": 14}, (15, 0), False),
    ({"turns": 14, "lost": 30}, (14, 30), True),           # meeting it counts
    ({"turns": 14, "lost": 30}, (13, 90), True),           # turns first
    ({"turns": 14, "lost": 30}, (14, 31), False),
    ({"turns": 31, "lost": 12, "beat": True}, (31, 12), False),   # a bot's is beaten
    ({"turns": 31, "lost": 12, "beat": True}, (31, 11), True),
    ({"turns": 31, "beat": True}, (31, 0), False),
])
def test_a_target_is_met_as_a_best_is_scored(target, score, met):
    stage = _targeted(target).stages[0]
    assert gauntlet.met(stage, score) is met


def test_a_win_short_of_the_target_leaves_the_stage_open_but_opens_nothing():
    g = _targeted({"turns": 14})
    _score(g.stages[0], 18, 3)
    assert gauntlet.bests(g) == {0: (18, 3)}
    assert gauntlet.cleared(g) == set()
    assert gauntlet.open_stages(g) == {0}
    _score(g.stages[0], 14, 9)
    assert gauntlet.cleared(g) == {0}
    assert gauntlet.open_stages(g) == {0, 1}


def test_a_won_stage_short_of_its_target_stays_open_out_of_order():
    """Won from a challenge link before it was reached: open to try again, and
    still nothing beyond it."""
    g = gauntlet.Gauntlet.from_dict({
        "stages": [{"id": 0, "settings": _setup(410)},
                   {"id": 1, "settings": _setup(411), "target": {"turns": 5}},
                   {"id": 2, "settings": _setup(412)}]})
    assert g is not None
    _score(g.stages[1], 30, 0)
    assert gauntlet.open_stages(g) == {0, 1}


def test_a_target_added_later_is_checked_against_the_old_best():
    plain = _targeted(None)
    _score(plain.stages[0], 20, 0)
    assert gauntlet.cleared(plain) == {0}
    assert gauntlet.cleared(_targeted({"turns": 14})) == set()


def test_no_builtin_stage_has_a_target():
    """Not in the tutorial: a beginner is held up by what they have not learned
    yet, not by how fast they won."""
    assert all(s.target is None for g in gauntlet.builtin() for s in g.stages)


def test_a_stage_no_start_reaches_is_a_problem():
    def gauntlet_with(lanes, start=None):
        data = {"stages": [{"id": i, "title": f"S{i}", "settings": _setup(300 + i)} for i in range(3)],
                "lanes": lanes}
        if start is not None:
            data["start"] = start
        g = gauntlet.Gauntlet.from_dict(data)
        assert g is not None
        return g

    assert gauntlet.problems(gauntlet_with([[0, 1], [1, 2]])) == []
    assert gauntlet.problems(gauntlet_with([[0, 1], [2, 1]])) == ["stage 3 (S2) can't be reached from a start"]
    assert gauntlet.problems(gauntlet_with([[0, 1], [2, 1, {"two_way": True}]])) == []
    assert gauntlet.problems(gauntlet_with([[0, 1], [1, 2]], start=[1])) == ["stage 1 (S0) can't be reached from a start"]
    for g in gauntlet.builtin():
        assert gauntlet.problems(g) == [], g.id


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


# --------------------------------------------------------------------------- #
# Gauntlets from a link (`#g=<id>`, `#gauntlet=<payload>`)
# --------------------------------------------------------------------------- #
_FIXTURE = Path(__file__).resolve().parent.parent / "leaderboard" / "tests" / "fixtures" / "gauntlet.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _file(gid: str, seed: int = 1) -> dict:
    return {"id": gid, "name": gid, "stages": [{"id": 0, "settings": _setup(seed)}]}


def test_the_board_builds_a_gauntlet_the_game_reads_unchanged():
    """The JS builder's output (leaderboard/tests/fixtures/gauntlet.json, built
    from three real game links) is a gauntlet here with nothing dropped."""
    fx = _fixture()
    g = gauntlet.Gauntlet.from_dict(fx["body"])
    assert g is not None
    assert g.id == fx["id"] and len(g.id) <= gauntlet.NAME_MAX
    assert g.name == "Fixture run: three maps"
    assert [s.title for s in g.stages] == ["Warm up", "Stage 2", "Hand-made — café"]
    assert g.stages[0].briefing == ("Take every system.", "Neutrals first.")
    assert g.stages[0].hint == "Neutrals first."
    assert g.lanes == ((0, 1), (1, 2)) and g.start == (0,)
    assert [s.target for s in g.stages] == [
        gauntlet.Target(turns=14, lost=3),
        gauntlet.Target(turns=31, lost=12, bot="thinker", beat=True),
        None]
    for stage in g.stages:
        cfg = gauntlet.stage_settings(stage)
        assert cfg.seed is not None and cfg.challenge is None and not cfg.autoplay
        build_state(cfg, cfg.seed)
    # The stage is the setup the link it was pasted from carried.
    assert gauntlet.stage_settings(g.stages[0]).seed == 42
    assert gauntlet.stage_settings(g.stages[2]).custom_map is not None


def test_an_inline_link_decodes_to_the_same_file_and_round_trips():
    fx = _fixture()
    assert gauntlet.inline_data(gauntlet.INLINE_FRAGMENT + fx["inline"]) == fx["body"]
    fragment = gauntlet.link_fragment(fx["body"])
    assert gauntlet.inline_data(fragment) == fx["body"]


@pytest.mark.parametrize("fragment", ["", "gauntlet=", "gauntlet=!!!", "gauntlet=eyJhIjoxfQ",
                                      "log=0011223344556677", "g=hard-mode-0123456789ab"])
def test_an_inline_fragment_that_is_not_a_gauntlet_is_ignored(fragment):
    assert gauntlet.inline_data(fragment) is None


@pytest.mark.parametrize("fragment,want", [
    ("g=hard-mode-0123456789ab", "hard-mode-0123456789ab"),
    ("g= hard-mode-0123456789ab ", "hard-mode-0123456789ab"),
    ("g=Hard-Mode-0123456789ab", None),
    ("g=hard-mode-0123", None),
    ("g=a-0123456789ab&x", None),
    ("gauntlet=abc", None),
    ("pbp=x:y", None),
    ("", None),
])
def test_a_link_id_must_be_the_stored_shape(fragment, want):
    assert gauntlet.link_id(fragment) == want


def test_a_remembered_gauntlet_is_listed_newest_first_and_capped(monkeypatch):
    monkeypatch.setattr(gauntlet.paths, "is_web", lambda: True)
    for i in range(gauntlet.REMEMBER_MAX + 2):
        assert gauntlet.remember(_file(f"run-{i}-0123456789ab", i)) is not None
    ids = [g.id for g in gauntlet.remembered()]
    assert len(ids) == gauntlet.REMEMBER_MAX
    assert ids[0] == f"run-{gauntlet.REMEMBER_MAX + 1}-0123456789ab"
    # Opening one again moves it to the front without a duplicate.
    gauntlet.remember(_file(ids[-1], 99))
    again = [g.id for g in gauntlet.remembered()]
    assert again[0] == ids[-1] and len(again) == len(set(again)) == gauntlet.REMEMBER_MAX
    # The web build lists them after the built-ins.
    assert [g.id for g in gauntlet.every()][:2] == ["tutorial", ids[-1]]


def test_remember_refuses_what_is_not_a_gauntlet_and_never_shadows_a_builtin(monkeypatch):
    monkeypatch.setattr(gauntlet.paths, "is_web", lambda: True)
    assert gauntlet.remember({"stages": []}) is None
    assert gauntlet.remembered() == []
    gauntlet.remember(_file("tutorial"))
    assert [g.id for g in gauntlet.user()] == []


def test_a_corrupt_store_reads_as_nothing_kept():
    webstore.set(gauntlet.paths.WEB_GAUNTLETS_KEY, "{not json")
    assert gauntlet.remembered() == []
    webstore.set(gauntlet.paths.WEB_GAUNTLETS_KEY, '{"a": 1}')
    assert gauntlet.remembered() == []
    webstore.set(gauntlet.paths.WEB_GAUNTLETS_KEY, '[1, "x", {"stages": "no"}]')
    assert gauntlet.remembered() == []


def test_desktop_lists_remembered_gauntlets_before_its_folder(monkeypatch, tmp_path):
    folder = tmp_path / "gauntlets"
    folder.mkdir()
    (folder / "mine.json").write_text(json.dumps(_file("mine")))
    (folder / "same.json").write_text(json.dumps(_file("kept-0123456789ab", 5)))
    monkeypatch.setattr(gauntlet, "user_dir", lambda: folder)
    monkeypatch.setattr(gauntlet.paths, "is_web", lambda: False)
    gauntlet.remember(_file("kept-0123456789ab"))
    assert [g.id for g in gauntlet.user()] == ["kept-0123456789ab", "mine"]


def test_a_fetch_reply_yields_the_file_or_nothing():
    fx = _fixture()
    body = json.dumps({"id": fx["id"], "gauntlet": fx["body"], "by_name": ""})
    assert gauntlet.from_reply(body) == fx["body"]
    for junk in ("", "nope", "[]", '{"gauntlet": 3}', '{"gauntlet": {"stages": []}}', '{"error": "x"}'):
        assert gauntlet.from_reply(junk) is None


def test_a_fetch_needs_a_board_and_a_well_formed_id(monkeypatch):
    asked = []
    monkeypatch.setattr(gauntlet.pbp, "request", lambda url, body=None: asked.append(url) or "req")
    monkeypatch.setattr(gauntlet.webstore, "leaderboard_url", lambda path: f"https://site.test{path}")
    assert gauntlet.fetch("hard-mode-0123456789ab") == "req"
    assert asked == ["https://site.test/api/gauntlet?id=hard-mode-0123456789ab"]
    assert gauntlet.fetch("../etc") is None
    monkeypatch.setattr(gauntlet.webstore, "leaderboard_url", lambda path: "")
    assert gauntlet.fetch("hard-mode-0123456789ab") is None


def test_asking_for_a_listing_saves_the_file_under_the_authors_name(monkeypatch):
    sent = []
    monkeypatch.setattr(gauntlet.pbp, "request", lambda url, body=None: sent.append((url, body)) or "req")
    monkeypatch.setattr(gauntlet.webstore, "leaderboard_url", lambda path: f"https://site.test{path}")
    data = _fixture()["body"]
    assert gauntlet.ask_listing(data, "Ann") == "req"
    [(url, body)] = sent
    assert url == "https://site.test/api/gauntlet"
    assert json.loads(body) == {"gauntlet": data, "by_name": "Ann", "wants_listing": True}
    monkeypatch.setattr(gauntlet.webstore, "leaderboard_url", lambda path: "")
    assert gauntlet.ask_listing(data, "Ann") is None and len(sent) == 1
