"""Generating a gauntlet a slice at a time (`starconquest/gauntletgen.py`,
`docs/design/gauntlet.md`, "Generated gauntlets")."""

from __future__ import annotations

import pytest

from starconquest import ai, dealer, gauntlet
from starconquest import gauntletgen as gg

SMALL = {"stages": 2, "seed": 9, "rules": {"players": {"fix": 2}, "systems": {"range": [8, 10]}},
         "opponents": ["rusherplus", "heuristic"]}


@pytest.fixture(scope="module", autouse=True)
def _roster():
    ai.load_models()


def _finish(job: gg.Job) -> int:
    steps = 0
    while not job.done:
        job.step(0.0)        # one turn a step: the smallest slice there is
        steps += 1
    return steps


def test_a_stepped_job_plays_the_same_games_as_playing_them_whole():
    job = gg.job_from_link({**SMALL, "candidates": 2}, seed=0)
    assert job.total == len(job.cands) * len(gg.PROBES) and job.progress() == 0.0
    steps = _finish(job)
    assert steps > job.total and job.progress() == 1.0
    whole = [gg.play_one(c["key"], c["stage"], b) for c in job.cands for b in gg.PROBES]
    strip = lambda rows: [{k: r[k] for k in ("key", "bot", "won", "turns", "lost")} for r in rows]  # noqa: E731
    assert strip(job.rows) == strip(whole)


def test_a_finished_job_is_a_gauntlet_the_game_reads():
    job = gg.job_from_link({**SMALL, "candidates": 2, "targets": True, "slack": 2, "name": "  My   run "}, seed=0)
    assert job.result() is None          # not before it is done
    _finish(job)
    data = job.result()
    g = gauntlet.Gauntlet.from_dict(data)
    assert g is not None and gauntlet.problems(g) == []
    assert g.name == "My run" and g.id == job.gid and g.id.startswith("gen-")
    assert all(s.target.lost is None for s in g.stages if s.target is not None)


def test_the_last_stage_may_be_unwon_unless_the_link_says_not():
    assert gg.job_from_link(SMALL, seed=0).unwon_last
    off = gg.job_from_link({**SMALL, "unwon_last": False}, seed=0)
    assert not off.unwon_last and off.gid != gg.job_from_link(SMALL, seed=0).gid


def test_the_id_follows_the_spec_and_seed():
    a = gg.job_from_link(SMALL, seed=0)
    assert a.seed == 9 and a.gid == gg.job_from_link(SMALL, seed=5).gid
    assert gg.job_from_link({**SMALL, "seed": 10}, seed=0).gid != a.gid
    unseeded = {k: v for k, v in SMALL.items() if k != "seed"}
    assert gg.job_from_link(unseeded, seed=4).seed == 4


@pytest.mark.parametrize("opts, per, targets, slack", [
    ({}, gg.LINK_CANDIDATES, None, 0),
    ({"candidates": 99, "targets": "yes", "slack": -1}, gg.MAX_CANDIDATES, None, 0),
    ({"candidates": True, "targets": True, "slack": 5}, gg.LINK_CANDIDATES, "slowest", 5),
    ({"targets": "fastest"}, gg.LINK_CANDIDATES, "fastest", 0),
])
def test_link_options_fall_back_when_unreadable(opts, per, targets, slack):
    job = gg.job_from_link({**SMALL, "stages": 1, **opts}, seed=0)
    assert len(job.cands) <= per and (job.targets, job.slack) == (targets, slack)


def test_a_spec_that_can_never_deal_is_refused():
    with pytest.raises(ValueError, match="no opponents"):
        gg.job_from_link({"opponents": []}, seed=0)


def test_a_spec_fragment_round_trips_and_junk_is_none():
    frag = gg.spec_fragment(SMALL)
    assert frag.startswith(gg.SPEC_FRAGMENT) and gg.spec_from_fragment(frag) == SMALL
    for junk in ("", "g=abc", "gauntlet=eNo", gg.SPEC_FRAGMENT + "!!!", gg.SPEC_FRAGMENT + "WzFd"):
        assert gg.spec_from_fragment(junk) is None
    # Not a gauntlet file or a setup: the other fragment readers pass it by.
    assert gauntlet.inline_data(frag) is None and gauntlet.link_id(frag) is None


def test_nothing_won_is_no_gauntlet():
    job = gg.Job(dealer.Spec.from_dict({**SMALL, "stages": 1}), 9, per_stage=1, probes=("heuristic",))
    job.queue.clear()
    job.rows = [{"key": job.cands[0]["key"], "bot": "heuristic", "won": False, "turns": 400,
                 "lost": 9, "timed_out": True}]
    assert job.done and job.result() is None


def test_the_boards_spec_link_is_a_run_the_game_reads_unchanged():
    """leaderboard/tests/fixtures/gauntlet-spec.json is a link the board's form
    built: the game decodes the same spec, keeps every rule, and deals within it."""
    import json
    from pathlib import Path

    fx = json.loads((Path(__file__).resolve().parent.parent / "leaderboard" / "tests" / "fixtures"
                     / "gauntlet-spec.json").read_text(encoding="utf-8"))
    data = gg.spec_from_fragment(fx["fragment"])
    assert data == fx["spec"]
    spec = dealer.Spec.from_dict(data)
    assert set(spec.rules) == set(data["rules"]) and dealer.problems(spec) == []
    job = gg.job_from_link(data, seed=0)
    assert job.seed == 12 and job.targets and len(job.cands) == 3
    for c in job.cands:
        s = gg.Settings.from_dict(c["stage"])
        assert s.mode == "symmetric" and 2 <= s.players <= 3 and 13 <= s.system_count() <= 18
        assert 6 <= s.garrison_k <= 10 and s.defender_advantage == 1.1


def test_the_boards_sliders_are_the_games():
    """The generate form's sliders (gauntlet-spec.mjs's KNOB_TABS, copied into
    the fixture) are settings.py's ADV_* tables, tab for tab: the same knobs,
    labels, ends and steps, so a pin can never ask for what the dealer clamps."""
    import json
    from pathlib import Path

    from starconquest.settings import ADV_COMBAT, ADV_ECON, ADV_FOG, ADV_MAP, ADV_TRAVEL

    fx = json.loads((Path(__file__).resolve().parent.parent / "leaderboard" / "tests" / "fixtures"
                     / "gauntlet-spec.json").read_text(encoding="utf-8"))
    tabs = {t["tab"]: [k for g in t["groups"] for k in g["knobs"]] for t in fx["knobs"]}
    board = {tab: [(k["attr"], k["label"], k["lo"], k["hi"], k["step"]) for k in knobs]
             for tab, knobs in tabs.items()}
    game = {"Advanced": ADV_MAP + ADV_TRAVEL + ADV_FOG + ADV_ECON, "Combat": ADV_COMBAT}
    for tab, specs in game.items():
        assert board[tab] == [(attr, label, lo, hi, step) for _k, label, attr, lo, hi, step, _i in specs], tab
    assert {a for _t, ks in board.items() for a, *_ in ks} == set(dealer.KNOBS)
    assert {a for a, *_ in board["Advanced"]} == set(dealer.ROLLED)
    switches = {s["attr"] for t in fx["knobs"] for g in t["groups"] for s in g["switches"]}
    assert switches == {"neutral_produces", "in_lane_battles"} <= set(dealer.CHOICES)
