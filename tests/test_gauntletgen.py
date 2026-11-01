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
    assert steps > len(job.rows) and len(job.rows) % len(gg.PROBES) == 0
    whole = [gg.play_one(r["key"], next(c["stage"] for c in job.cands if c["key"] == r["key"]), r["bot"])
             for r in job.rows]
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


def test_only_a_ticked_listing_box_gives_the_run_an_author_to_send_under():
    assert gg.job_from_link(SMALL, seed=0).listing is None
    # A name alone is not consent: it rides only beside a ticked box.
    assert gg.job_from_link({**SMALL, "by_name": "Ann"}, seed=0).listing is None
    assert gg.job_from_link({**SMALL, "by_name": "Ann", "wants_listing": "yes"}, seed=0).listing is None
    asked = gg.job_from_link({**SMALL, "by_name": "  Ann   Lee " + "x" * 40, "wants_listing": True}, seed=0)
    assert asked.listing == ("Ann Lee " + "x" * 40)[:gauntlet.BY_NAME_MAX]
    assert gg.job_from_link({**SMALL, "wants_listing": True}, seed=0).listing == ""
    # Asking for a listing is not a different gauntlet.
    assert asked.gid == gg.job_from_link(SMALL, seed=0).gid


def test_the_id_follows_the_spec_and_seed():
    a = gg.job_from_link(SMALL, seed=0)
    assert a.seed == 9 and a.gid == gg.job_from_link(SMALL, seed=5).gid
    assert gg.job_from_link({**SMALL, "seed": 10}, seed=0).gid != a.gid
    unseeded = {k: v for k, v in SMALL.items() if k != "seed"}
    assert gg.job_from_link(unseeded, seed=4).seed == 4


@pytest.mark.parametrize("opts, rounds, targets, slack", [
    ({}, gg.LINK_ROUNDS, None, 0),
    ({"candidates": 99, "targets": "yes", "slack": -1}, gg.MAX_ROUNDS, None, 0),
    ({"candidates": True, "targets": True, "slack": 5}, gg.LINK_ROUNDS, "slowest", 5),
    ({"targets": "fastest"}, gg.LINK_ROUNDS, "fastest", 0),
])
def test_link_options_fall_back_when_unreadable(opts, rounds, targets, slack):
    job = gg.job_from_link({**SMALL, "stages": 1, **opts}, seed=0)
    assert job.rounds == rounds and (job.targets, job.slack) == (targets, slack)


def test_candidates_come_a_round_at_a_time_the_ends_first():
    spec = dealer.Spec.from_dict({**SMALL, "stages": 5})
    cands = gg.candidates(spec, 9, 2)
    assert cands == gg.candidates(spec, 9, 2)
    assert [c["round"] for c in cands] == [0] * 5 + [1] * 5
    assert [c["depth"] for c in cands[:5]] == [0.0, 1.0, 0.25, 0.75, 0.5]


def test_probes_are_bots_stops_and_tweaks():
    assert gg.probe_profile("marshal") == ("marshal", gg.AiParams())
    assert gg.probe_profile("evolved:0") == ("evolved", gg.AiParams(aux=0.0))
    bot, params = gg.probe_profile("heuristic+lean")
    assert bot == "heuristic" and (params.reserve_floor, params.reserve_fraction) == (0, 0.0)
    params.reserve_floor = 9                 # a copy: the table is never written through
    assert gg.TWEAKS["heuristic+lean"][1].reserve_floor == 0
    assert all(gg.probe_profile(p)[0] in ai.STRATEGIES for p in gg.PROBES)


def _m(key, lost, played=4, depth=0.5, timeouts=0):
    wins = [{"bot": f"b{i}", "won": True, "turns": 50 + i, "lost": i} for i in range(played - lost)]
    return {"key": key, "played": played, "wins": wins, "difficulty": lost / played, "depth": depth,
            "timeouts": timeouts, "win_turns": 50 if wins else None, "stage": {}}


def test_a_run_settles_once_its_pick_is_complete_and_stops_rising():
    rising = [_m("a", 1), _m("b", 2), _m("c", 3), _m("hard", 4, depth=1.0)]
    # Every usable level the probes can show, the last unwon: settled at once.
    assert gg.settled(rising, 4, True, 4, None)[0]
    # Short of a level: not until a round adds nothing to what the pick shows.
    flat = [_m("a", 1), _m("b", 1), _m("c", 3), _m("hard", 4, depth=1.0)]
    stop, shown = gg.settled(flat, 4, True, 4, None)
    assert not stop and gg.settled(flat + [_m("d", 3)], 4, True, 4, shown)[0]
    assert gg.settled(flat + [_m("d", 2)], 4, True, 4, shown)[0]     # a level that fills it
    # A round that adds a level short of every one goes on.
    six = [_m("a", 0, 5), _m("b", 0, 5), _m("c", 2, 5), _m("d", 4, 5), _m("hard", 5, 5, depth=1.0)]
    stop, shown = gg.settled(six, 5, True, 5, None)
    assert not stop and not gg.settled(six + [_m("e", 3, 5)], 5, True, 5, shown)[0]
    # Incomplete: a stage short, or no unwon map for the last.
    assert gg.settled(rising[:3], 4, True, 4, None) == (False, None)
    assert gg.settled(rising[:3] + [_m("d", 3)], 4, True, 4, None) == (False, None)


def test_a_job_stops_at_the_end_of_a_round_that_settles(monkeypatch):
    calls = []
    real = gg.settled
    monkeypatch.setattr(gg, "settled", lambda *a: calls.append(len(a[0])) or (True, real(*a)[1]))
    job = gg.job_from_link({**SMALL, "candidates": 3}, seed=0)
    _finish(job)
    per_round = len([c for c in job.cands if c["round"] == 0])
    assert calls == [per_round] and len(job.rows) == per_round * len(gg.PROBES)
    assert job.result() is None or job.result()["stages"]


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
    job = gg.Job(dealer.Spec.from_dict({**SMALL, "stages": 1}), 9, rounds=1, probes=("heuristic",))
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
