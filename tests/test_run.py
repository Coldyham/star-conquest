"""Runs of a gauntlet: from a start to a goal, each stage within its target,
on the gauntlet's lives, over every stage the run has opened
(`starconquest/run.py`, `docs/design/gauntlet.md`, *Runs*)."""

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


def _chain(targets=(14, None, 20), gid: str = "run-me", lives: int = 1) -> gauntlet.Gauntlet:
    stages = []
    for i, turns in enumerate(targets):
        row = {"id": i, "title": f"Stage {i}", "settings": _setup(10 + i)}
        if turns is not None:
            row["target"] = {"turns": turns}
        stages.append(row)
    g = gauntlet.Gauntlet.from_dict({"id": gid, "name": "Run me", "stages": stages, "lives": lives})
    assert g is not None
    return g


def _graph(lanes, start=(0,), targets=(), gid: str = "fork-me", n: int = 3, lives: int = 1,
           bonus=()) -> gauntlet.Gauntlet:
    stages = [{"id": i, "title": f"Stage {i}", "settings": _setup(10 + i)} for i in range(n)]
    for i, turns in targets:
        stages[i]["target"] = {"turns": turns}
    for i, on in bonus:
        stages[i]["bonus_life"] = on
    g = gauntlet.Gauntlet.from_dict({"id": gid, "name": "Fork me", "stages": stages,
                                     "lanes": lanes, "start": list(start), "lives": lives})
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


def test_a_run_needs_a_target_or_a_fork():
    assert run.offered(_chain())
    assert run.offered(_chain((None, None, 9)))       # one target is enough
    assert not run.offered(_chain((None, None, None)))
    tutorial = gauntlet.find(gauntlet.builtin(), gauntlet.TUTORIAL_ID)
    assert tutorial is not None and not run.offered(tutorial)   # its gates lead off goals
    assert run.offered(_graph([[0, 1], [0, 2]]))                    # a fork, no target
    assert run.offered(_graph([[0, 1], [0, 2, {"turns": 20}]]))     # a gated fork
    assert run.offered(_graph([[0, 2], [1, 2]], start=[0, 1]))      # two starts
    assert not run.offered(_graph([[0, 1], [1, 2]]))


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
        done = run.finish(r.id, name, run.MET, 12, 3, g)
        assert done is not None
        r = done
    assert r.status == run.COMPLETE and r.cleared == 3
    assert run.active() is None
    assert run.summary(g) == "Run complete ✓"


@pytest.mark.parametrize("outcome", [run.MISSED, run.LOST, run.VOID])
def test_with_one_life_anything_but_meeting_the_target_ends_the_run(outcome):
    g = _chain()
    r = run.begin(run.start(g), 0, _played(_log(), "game_20261104_100000_1.json"))
    r = run.finish(r.id, "game_20261104_100000_1.json", outcome, 30, 9, g)
    assert r is not None and r.status == run.OVER
    assert run.stage_to_play(r, g) is None
    assert run.summary(g) == "Best run: 0/3 stages"


def test_only_the_runs_own_game_moves_it_and_only_once():
    g = _chain()
    name = "game_20261104_100000_1.json"
    r = run.begin(run.start(g), 0, _played(_log(), name))
    # a Retry or fork of the stage is another file: free play
    assert run.finish(r.id, "game_20261104_100100_1.json", run.LOST, 5, 5, g) is None
    assert run.finish(r.id, name, run.MET, 12, 1, g) is not None
    assert run.finish(r.id, name, run.LOST, 12, 1, g) is None   # decided already
    after = run.active()
    assert after is not None and after.cleared == 1 and after.playing is None


# --------------------------------------------------------------------------- #
# A run over a graph
# --------------------------------------------------------------------------- #
def _play(r: run.Run, g: gauntlet.Gauntlet, sid: int, n: int, outcome: str = run.MET,
          turns: int = 12) -> run.Run:
    """Play ``sid`` as the run's next step, decided as ``outcome`` in ``turns`` turns."""
    name = f"game_20261112_10{n:02d}00_{n}.json"
    assert sid in run.frontier(r, g), (sid, run.frontier(r, g))
    r = run.begin(r, sid, _played(_log(), name))
    done = run.finish(r.id, name, outcome, turns, 1, g)
    assert done is not None
    return done


def _win(r: run.Run, g: gauntlet.Gauntlet, sid: int, n: int, turns: int = 12) -> run.Run:
    return _play(r, g, sid, n, run.MET, turns)


def test_a_fork_waits_for_a_pick_and_goes_the_way_picked():
    g = _graph([[0, 1], [0, 2]])
    r = _win(run.start(g), g, 0, 1)
    assert r.status == run.ACTIVE
    assert run.frontier(r, g) == [1, 2]
    assert run.stage_to_play(r, g) is None          # nothing picked: the list asks
    assert run.stage_to_play(r, g, pick=0) is None   # won, and no gate out: not on it
    assert run.stage_to_play(r, g, pick=2) == 2
    r = _win(r, g, 2, 2)
    assert r.status == run.COMPLETE and r.cleared == 2
    assert run.summary(g) == "Run complete ✓"
    assert run.position(r, g) == (3, 0)          # no total: runs may differ in length


def test_the_frontier_holds_every_stage_opened_and_not_gone_past():
    # two tracks that rejoin: 0 -> 1 -> 3, 0 -> 2 -> 3, 3 -> 4
    g = _graph([[0, 1], [0, 2], [1, 3], [2, 3], [3, 4]], n=5)
    r = _win(run.start(g), g, 0, 1)
    assert run.frontier(r, g) == [1, 2]
    r = _win(r, g, 1, 2)
    assert run.frontier(r, g) == [3]             # 2 leads only to 3, open already: spent
    r = _win(r, g, 3, 3)
    assert run.frontier(r, g) == [4]             # 2 leads to 3, won: behind the run
    assert run.next_up(r, g) == 4


def test_a_stage_that_would_open_nothing_new_leaves_the_frontier():
    # a cross-stitch: 0, 1 -> 2, 3 -> 4, 5 -> 6
    lanes = [[a, b] for col in ((0, 1), (2, 3), (4, 5)) for a in col for b in (col[0] + 2, col[1] + 2)
             if b < 6] + [[4, 6], [5, 6]]
    g = _graph(lanes, start=(0, 1), n=7)
    r = run.start(g)
    assert run.frontier(r, g) == [0, 1]
    r = _win(r, g, 0, 1)
    assert run.frontier(r, g) == [2, 3]          # 1 would open only 2 and 3
    r = _win(r, g, 3, 2)
    assert run.frontier(r, g) == [4, 5]


def test_a_stage_that_opens_something_new_stays():
    # 0 -> 2, 1 -> 2, 1 -> 3, 2 -> 3 (goal)
    g = _graph([[0, 2], [1, 2], [1, 3], [2, 3]], start=(0, 1), n=4)
    r = _win(run.start(g), g, 0, 1)
    assert run.frontier(r, g) == [1, 2]          # 1 still opens 3


def test_a_spent_stage_that_grants_a_life_stays():
    g = _graph([[0, 2], [1, 2], [2, 3]], start=(0, 1), n=4, bonus=[(1, True)])
    r = _win(run.start(g), g, 0, 1)
    assert run.frontier(r, g) == [1, 2]


def test_a_gated_lane_opens_only_on_the_runs_own_score():
    g = _graph([[0, 1], [0, 2, {"turns": 10}], [1, 3], [2, 3]], n=4)
    slow = _win(run.start(g), g, 0, 1, turns=15)
    assert run.unlocked(slow, g) == {0, 1}
    assert run.frontier(slow, g) == [0, 1]           # 0 kept: its gate can still be met
    assert run.next_up(slow, g) == 1
    assert run.stage_to_play(slow, g) == 1           # nothing picked: the one stage not won
    fast = _win(run.start(g), g, 0, 2, turns=10)
    assert run.frontier(fast, g) == [1, 2]


def test_a_best_never_opens_a_gate_for_a_run(monkeypatch):
    g = _graph([[0, 1], [0, 2, {"turns": 10}], [1, 3], [2, 3]], n=4)
    monkeypatch.setattr(gauntlet, "best", lambda stage: (1, 0))
    r = _win(run.start(g), g, 0, 1, turns=15)
    assert 2 not in run.unlocked(r, g)


def test_a_missed_gate_is_replayed_for_a_life():
    g = _graph([[0, 1], [1, 2], [0, 3, {"turns": 10}]], n=4, lives=3)
    r = _win(run.start(g), g, 0, 1, turns=15)
    assert run.gates_left(r, g, 0) and run.frontier(r, g) == [0, 1]
    r = _win(r, g, 1, 2)
    assert run.frontier(r, g) == [0, 2]              # behind, but its gate is still worth a life
    r = _play(r, g, 0, 3, run.MET, turns=14)         # a win short of the gate costs a life
    assert r.steps[-1].outcome == run.MISSED and run.lives_left(r, g) == 2
    assert r.cleared == 2                            # a replay wins no stage
    r = _play(r, g, 0, 4, run.MISSED, turns=9)       # judged on the gate, not the stage target
    assert r.steps[-1].outcome == run.MET and run.lives_left(r, g) == 2
    assert 3 in run.unlocked(r, g) and 0 not in run.frontier(r, g)


def test_a_gate_to_a_stage_gone_past_is_not_worth_a_replay():
    g = _graph([[0, 1], [1, 2], [0, 3, {"turns": 10}], [3, 1]], n=4)
    r = _win(_win(run.start(g), g, 0, 1, turns=15), g, 1, 2)
    assert run.frontier(r, g) == [2]             # 3 leads to 1, won


def test_a_gate_to_a_stage_that_would_open_nothing_new_is_not_worth_a_replay():
    g = _graph([[0, 1], [0, 2, {"turns": 10}], [2, 1]])
    r = _win(run.start(g), g, 0, 1, turns=15)
    assert not run.gates_left(r, g, 0) and run.frontier(r, g) == [1]


def test_a_gate_to_a_stage_already_open_is_not_worth_a_replay():
    g = _graph([[0, 1], [1, 2], [0, 2, {"turns": 10}]])
    r = _win(_win(run.start(g), g, 0, 1, turns=15), g, 1, 2)
    assert run.frontier(r, g) == [2]


def test_a_loss_costs_a_life_and_keeps_the_stage():
    g = _chain(lives=3)
    r = run.start(g)
    assert r.lives == 3 and run.lives_left(r, g) == 3
    r = _play(r, g, 0, 1, run.LOST)
    assert r.status == run.ACTIVE and run.lives_left(r, g) == 2 and run.frontier(r, g) == [0]
    r = _play(r, g, 0, 2, run.VOID)
    assert r.status == run.ACTIVE and run.lives_left(r, g) == 1
    r = _play(r, g, 0, 3, run.MISSED)
    assert r.status == run.OVER and run.lives_left(r, g) == 0
    assert run.note(r, g, 1, 3, None) == ("Run over: no lives left · 0 stages won", False)


@pytest.mark.parametrize("outcome", [run.MISSED, run.LOST, run.VOID])
def test_each_loss_costs_one_life(outcome):
    g = _chain(lives=2)
    r = _play(run.start(g), g, 0, 1, outcome)
    assert r.status == run.ACTIVE and run.lives_left(r, g) == 1


def test_gone_and_abandoned_end_the_run_whatever_its_lives():
    g = _chain(lives=5)
    r = run.begin(run.start(g), 0, _played(_log(), "game_20261104_100000_1.json"))
    assert run.lose_game(r).status == run.OVER
    r = run.begin(run.start(g), 0, _played(_log(), "game_20261104_100100_1.json"))
    assert run.abandon(r).status == run.OVER


def test_try_again_and_picking_the_stage_from_the_list_make_the_same_step():
    g = _chain(lives=2)
    r = _play(run.start(g), g, 0, 1, run.LOST)
    assert run.stage_to_play(r, g) == 0 == run.stage_to_play(r, g, pick=0)
    r = run.begin(r, 0, _played(_log(), "game_20261112_100200_2.json"))
    assert r.playing is not None and r.playing.stage == 0 and len(r.steps) == 2


def test_a_bonus_stage_grants_a_life_once():
    # 0 -> 1 (goal); a gate from 0 to the dead end 2, which grants a life by default
    g = _graph([[0, 1], [0, 2, {"turns": 10}]], lives=2)
    assert gauntlet.grants_life(g, 2) and not gauntlet.grants_life(g, 1)
    r = _win(run.start(g), g, 0, 1, turns=10)
    assert run.frontier(r, g) == [1, 2]
    r = _play(r, g, 2, 2, run.MISSED)                # a missed target grants nothing
    assert run.lives_left(r, g) == 1
    r = _win(r, g, 2, 3)
    assert run.lives_left(r, g) == 2 and r.status == run.ACTIVE   # a dead end won doesn't complete it
    assert run.note(r, g, 2, 0, None)[0] == "Run: stage 2 done · bonus life · ♥♥ left · Next stage carries it on"
    assert run.frontier(r, g) == [1]
    r = _win(r, g, 1, 4)
    assert r.status == run.COMPLETE


def test_a_dead_end_stays_on_the_frontier_until_played():
    g = _graph([[0, 1], [1, 2], [0, 3, {"turns": 10}]], n=4)
    r = _win(_win(run.start(g), g, 0, 1, turns=9), g, 1, 2)
    assert run.frontier(r, g) == [2, 3]


def test_a_won_goal_completes_the_run_with_tracks_still_open():
    g = _graph([[0, 1], [0, 2], [2, 3]], n=4)
    r = _win(_win(run.start(g), g, 0, 1), g, 1, 2)
    assert r.status == run.COMPLETE


def test_a_run_ends_on_a_goal_with_a_bonus_beyond_it():
    g = _graph([[0, 1], [0, 2], [1, 3, {"turns": 99}]], n=4)
    r = _win(_win(run.start(g), g, 0, 1), g, 1, 2)
    assert r.status == run.COMPLETE
    assert run.frontier(r, g) == []


def test_a_two_way_lane_never_leads_back_to_a_stage_won():
    g = _graph([[0, 1, {"two_way": True}], [0, 2]])
    r = run.start(g)
    assert run.frontier(r, g) == [0]
    r = _win(r, g, 0, 1)
    assert 0 not in run.frontier(r, g)           # won 0 is not behind 1, only won
    assert run.behind(g, 1, r.won) is False      # a two-way lane puts nothing behind
    assert run.frontier(r, g) == [2]             # 1 leads back to 0, so it is no goal, and opens nothing
    assert _win(r, g, 2, 3).status == run.COMPLETE


def test_a_two_way_lane_to_a_stage_not_open_keeps_its_stage():
    # 0 <-> 1 <-> 2, 0 -> 3: 1 is open on 0's win and still opens 2
    g = _graph([[0, 1, {"two_way": True}], [1, 2, {"two_way": True}], [0, 3]], n=4)
    r = _win(run.start(g), g, 0, 1)
    assert run.frontier(r, g) == [1, 3]


def test_two_starts_wait_for_a_pick():
    g = _graph([[0, 2], [1, 2]], start=[0, 1])
    r = run.start(g)
    assert run.frontier(r, g) == [0, 1]
    assert run.stage_to_play(r, g) is None and run.stage_to_play(r, g, pick=1) == 1


def test_a_changed_lane_ends_a_forked_run():
    g = _graph([[0, 1], [0, 2]])
    run.start(g)
    assert run.active_for(_graph([[0, 1], [0, 2], [1, 2]])) is None


def test_the_run_keeps_its_own_lives():
    g = _chain(lives=3)
    r = run.start(g)
    assert run.active_for(_chain(lives=1)) == r      # lives are out of the hash
    assert r.to_dict()["lives"] == 3 and "lives" not in run.start(_chain(gid="x")).to_dict()
    old = {k: v for k, v in r.to_dict().items() if k != "lives"}   # a record from before lives
    back = run.Run.from_dict(old)
    assert back is not None and back.lives == 1
    assert run.Run.from_dict({**r.to_dict(), "lives": 99}).lives == gauntlet.LIVES_MAX


def test_an_old_record_ends_on_its_first_loss():
    g = _chain(lives=3)
    r = run.start(g)
    run.save(run.Run.from_dict({k: v for k, v in r.to_dict().items() if k != "lives"}))
    r = _play(run.active(), g, 0, 1, run.LOST)
    assert r.status == run.OVER


def test_the_fingerprint_moves_only_for_a_bonus_stage():
    plain = _graph([[0, 1], [0, 2]])
    body = [[sid, plain.stage(sid).settings, None] for sid in (0, 1, 2)]
    body.append([[a, b, False, None] for a, b in sorted(plain.lanes)])
    body.append([0])
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib
    assert run.fingerprint(plain) == hashlib.sha256(raw.encode()).hexdigest()[:16]   # as before lives
    ticked = _graph([[0, 1], [0, 2]], bonus=[(1, True)])
    assert run.fingerprint(ticked) != run.fingerprint(plain)


def test_the_overlay_line():
    g = _graph([[0, 1], [0, 2]])
    r = _win(run.start(g), g, 0, 1)
    assert run.note(r, g, 1, 0, None) == ("Run: stage 1 done · choose the way on in the gauntlet list", True)
    r = _win(r, g, 1, 2)
    assert run.note(r, g, 2, 0, None) == ("Run complete: 2 stages", True)
    c = _chain()
    r = _win(_win(_win(run.start(c), c, 0, 1), c, 1, 2), c, 2, 3)
    assert run.note(r, c, 3, 3, None) == ("Run complete: all 3 stages", True)
    r = _play(run.start(c), c, 0, 4, run.LOST)
    assert run.note(r, c, 1, 3, None) == ("Run over at stage 1/3: lost", False)
    lives = _chain(lives=3)
    r = _play(run.start(lives), lives, 0, 5, run.LOST)
    assert run.note(r, lives, 1, 3, None) == ("Run: lost · ♥♥ left", False)
    r = _play(r, lives, 0, 6, run.MISSED)
    target = lives.stage(0).target
    assert target is not None
    assert run.note(r, lives, 1, 3, target) == (f"Run: {target.text()} not met · ♥ left", False)


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
    run.finish(r.id, "game_20261104_100000_1.json", run.LOST, 3, 3, g)
    assert not webstore.get(paths.WEB_RUN_LOG_KEY)
