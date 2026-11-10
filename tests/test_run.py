"""Runs of a gauntlet: every stage in order, each within its target, one life
(`starconquest/run.py`, `docs/graphical-gauntlet-maker.md`, Phase 0)."""

from __future__ import annotations

import json

import pytest

from starconquest import gauntlet, paths, replay, run, webstore
from starconquest.settings import Settings


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path / "games")


def _setup(seed: int) -> dict:
    return Settings(players=2, nodes=8, seed=seed).to_dict()


def _chain(targets=(14, None, 20), gid: str = "run-me") -> gauntlet.Gauntlet:
    stages = []
    for i, turns in enumerate(targets):
        row = {"id": i, "title": f"Stage {i}", "settings": _setup(10 + i)}
        if turns is not None:
            row["target"] = {"turns": turns}
        stages.append(row)
    g = gauntlet.Gauntlet.from_dict({"id": gid, "name": "Run me", "stages": stages})
    assert g is not None
    return g


def _log(seed: int = 5) -> replay.GameLog:
    log = replay.new_log(Settings(players=2, nodes=8, seed=seed), seed)
    assert log.path is not None
    return log


def _played(log: replay.GameLog, name: str) -> replay.GameLog:
    log.path = replay.GAMES_DIR / name
    return log


# --------------------------------------------------------------------------- #
# Which gauntlets can be run
# --------------------------------------------------------------------------- #
def test_a_chain_is_played_in_lane_order():
    g = gauntlet.Gauntlet.from_dict({"stages": [{"id": i, "settings": _setup(i)} for i in range(3)],
                                     "lanes": [[2, 0], [0, 1]], "start": [2]})
    assert g is not None
    assert gauntlet.chain(g) == [2, 0, 1]


def test_a_fork_or_a_shortcut_is_not_a_chain():
    stages = [{"id": i, "settings": _setup(i)} for i in range(3)]
    fork = gauntlet.Gauntlet.from_dict({"stages": stages, "lanes": [[0, 1], [0, 2]]})
    shortcut = gauntlet.Gauntlet.from_dict({"stages": stages, "lanes": [[0, 1], [1, 2], [0, 2]]})
    two_starts = gauntlet.Gauntlet.from_dict({"stages": stages, "lanes": [[0, 1], [1, 2]], "start": [0, 1]})
    for g in (fork, shortcut, two_starts):
        assert g is not None and gauntlet.chain(g) is None


def test_a_two_way_lane_keeps_a_chain():
    g = gauntlet.Gauntlet.from_dict({"stages": [{"id": i, "settings": _setup(i)} for i in range(3)],
                                     "lanes": [[0, 1, {"two_way": True}], [1, 2]]})
    assert g is not None and gauntlet.chain(g) == [0, 1, 2]


def test_a_run_needs_a_chain_with_a_target():
    assert run.offered(_chain())
    assert run.offered(_chain((None, None, 9)))       # one target is enough
    assert not run.offered(_chain((None, None, None)))
    tutorial = gauntlet.find(gauntlet.builtin(), gauntlet.TUTORIAL_ID)
    assert tutorial is not None and not run.offered(tutorial)


# --------------------------------------------------------------------------- #
# A run, step by step
# --------------------------------------------------------------------------- #
def test_a_run_goes_stage_by_stage_and_completes():
    g = _chain()
    r = run.start(g, now="2026-11-04T10:00:00")
    assert run.stage_to_play(r, g) == 0 and run.position(r, g) == (1, 3)
    for sid, name in ((0, "game_20261104_100000_1.json"), (1, "game_20261104_100500_2.json"),
                      (2, "game_20261104_101000_3.json")):
        r = run.begin(r, sid, _played(_log(), name))
        assert r.playing is not None and run.stage_to_play(r, g) == sid
        done = run.finish(r.id, name, run.MET, 12, 3, total=3)
        assert done is not None
        r = done
    assert r.status == run.COMPLETE and r.cleared == 3
    assert run.active() is None
    assert run.summary(g) == "Run complete ✓"


@pytest.mark.parametrize("outcome", [run.MISSED, run.LOST, run.VOID])
def test_anything_but_meeting_the_target_ends_the_run(outcome):
    g = _chain()
    r = run.begin(run.start(g), 0, _played(_log(), "game_20261104_100000_1.json"))
    r = run.finish(r.id, "game_20261104_100000_1.json", outcome, 30, 9, total=3)
    assert r is not None and r.status == run.OVER
    assert run.stage_to_play(r, g) is None
    assert run.summary(g) == "Best run: 0/3 stages"


def test_only_the_runs_own_game_moves_it_and_only_once():
    g = _chain()
    name = "game_20261104_100000_1.json"
    r = run.begin(run.start(g), 0, _played(_log(), name))
    # a Retry or fork of the stage is another file: free play
    assert run.finish(r.id, "game_20261104_100100_1.json", run.LOST, 5, 5, total=3) is None
    assert run.finish(r.id, name, run.MET, 12, 1, total=3) is not None
    assert run.finish(r.id, name, run.LOST, 12, 1, total=3) is None   # decided already
    after = run.active()
    assert after is not None and after.cleared == 1 and after.playing is None


def test_judging_a_decided_game():
    assert run.judge(won=True, by_hand=True, met=True) == run.MET
    assert run.judge(won=True, by_hand=True, met=False) == run.MISSED
    assert run.judge(won=True, by_hand=False, met=True) == run.VOID
    assert run.judge(won=False, by_hand=True, met=True) == run.LOST


def test_starting_a_run_abandons_the_one_under_way():
    first, second = _chain(gid="first"), _chain(gid="second")
    old = run.begin(run.start(first), 0, _played(_log(), "game_20261104_100000_1.json"))
    new = run.start(second)
    kept = {r.id: r for r in run.runs()}
    assert kept[old.id].status == run.OVER and kept[old.id].steps[-1].outcome == run.ABANDONED
    assert run.active() == new


def test_a_changed_gauntlet_ends_its_run():
    g = _chain()
    run.start(g)
    edited = _chain((13, None, 20))     # a target tightened in a desktop file
    assert run.active_for(edited) is None
    assert run.active() is None


def test_the_store_keeps_the_active_run_and_the_last_finished():
    g = _chain()
    for i in range(run.KEEP_FINISHED + 5):
        run.start(g, now=f"2026-11-04T10:{i // 60:02d}:{i % 60:02d}")
    stored = run.runs()
    assert stored[0].status == run.ACTIVE
    assert len(stored) == 1 + run.KEEP_FINISHED


def test_a_corrupt_or_tampered_store_reads_tolerantly():
    webstore.set(paths.WEB_RUNS_KEY, "not json")
    assert run.runs() == []
    good = run.start(_chain()).to_dict()
    bad_log = dict(good, id="x", steps=[{"stage": 0, "log": "../../etc/passwd"}])
    webstore.set(paths.WEB_RUNS_KEY, json.dumps([None, 3, {"id": "y"}, bad_log, good]))
    found = {r.id: r for r in run.runs()}
    assert set(found) == {"x", good["id"]}
    assert found["x"].steps == ()      # a step naming no game file is not kept


# --------------------------------------------------------------------------- #
# The run's game survives a reload
# --------------------------------------------------------------------------- #
def test_the_runs_game_comes_back_from_the_kept_copy():
    log = _log(7)
    assert log.path is not None
    run.keep_log(log)
    back = run.restore_log(log.path.name)
    assert back is not None and back.seed == 7 and back.path == log.path
    assert run.restore_log("game_20261104_100000_99.json") is None   # neither kept nor on disk
    assert run.restore_log("../kv.json") is None


def test_a_run_over_lets_its_kept_game_go():
    g = _chain()
    log = _played(_log(), "game_20261104_100000_1.json")
    r = run.begin(run.start(g), 0, log)
    assert webstore.get(paths.WEB_RUN_LOG_KEY)
    run.finish(r.id, "game_20261104_100000_1.json", run.LOST, 3, 3, total=3)
    assert not webstore.get(paths.WEB_RUN_LOG_KEY)


