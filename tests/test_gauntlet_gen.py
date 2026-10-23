"""tools/gauntlet_gen.py: deal, probe, pick (`docs/design/gauntlet.md`,
"Generated gauntlets")."""

from __future__ import annotations

import json
import sys

import pytest

from starconquest import ai, dealer, gauntlet
from tools import gauntlet_gen as gen

SMALL = {"stages": 2, "rules": {"players": {"fix": 2}, "systems": {"range": [8, 10]}},
         "opponents": ["rusherplus", "heuristic"]}


@pytest.fixture(scope="module", autouse=True)
def _roster():
    ai.load_models()


def _m(key, lost, played=4, turns=50, depth=0.5, timeouts=0):
    wins = [{"bot": f"b{i}", "won": True, "turns": turns + i, "lost": i} for i in range(played - lost)]
    return {"key": key, "played": played, "wins": wins, "difficulty": lost / played, "depth": depth,
            "timeouts": timeouts, "win_turns": turns if wins else None, "stage": {}}


def test_candidates_are_a_pure_function_of_the_spec_and_seed():
    spec = dealer.Spec.from_dict(SMALL)
    a, b = gen.candidates(spec, 5, 3), gen.candidates(spec, 5, 3)
    assert a == b and len(a) == 6
    assert [c["depth"] for c in a] == [0.0] * 3 + [1.0] * 3
    assert a != gen.candidates(spec, 6, 3)


def test_measure_counts_losses_and_skips_unplayed():
    cands = [{"key": "a"}, {"key": "b"}]
    rows = [{"key": "a", "bot": "x", "won": True, "turns": 10, "lost": 1},
            {"key": "a", "bot": "y", "won": False, "turns": 40, "lost": 9}]
    a, b = gen.measure(cands, rows, ["x", "y"])
    assert a["difficulty"] == 0.5 and a["win_turns"] == 10 and a["played"] == 2
    assert b["difficulty"] is None and b["played"] == 0


def test_pick_rises_across_the_spread_and_takes_an_unwon_map_only_last():
    won = [_m("e1", 0), _m("m1", 2), _m("e2", 1), _m("m2", 3, turns=90), _m("m3", 3)]
    measured = [_m("hard", 4), *won]
    assert [p["key"] for p in gen.pick(measured, 3, unwon_last=False)] == ["e1", "m1", "m2"]
    assert [p["key"] for p in gen.pick(measured, 3)] == ["e1", "m2", "hard"]
    spread = gen.pick(measured, 9)[:-1]
    assert spread == sorted(spread, key=lambda m: (m["difficulty"], m["win_turns"]))
    assert all(p["key"] != "hard" for p in gen.pick(measured, 9, unwon_last=False))
    # Of the unwon maps, never one a probe ran out of turns on; the deepest first.
    measured = [_m("slow", 4, timeouts=1, depth=1.0), _m("deep", 4, depth=0.8), _m("shallow", 4, depth=0.2), *won]
    assert gen.pick(measured, 3)[-1]["key"] == "deep"
    assert gen.pick([_m("slow", 4, timeouts=1), *won], 3)[-1]["key"] == "m2"


def test_a_target_is_the_slowest_or_fastest_probe_win_plus_slack():
    m = _m("a", 1, turns=30)       # wins in 30, 31, 32 turns
    assert gen.target(m, 0) == {"turns": 32, "lost": 2}
    assert gen.target(m, 3) == {"turns": 35}
    assert gen.target(m, 0, "fastest") == {"turns": 30, "lost": 0}
    assert gen.target(m, 2, "fastest") == {"turns": 32}


def test_a_stage_no_probe_won_has_no_target():
    data = gg_build([_m("a", 0, turns=30), _m("b", 4)])
    first, last = data["stages"]
    assert first["target"] == {"turns": 30, "lost": 0} and "target" not in last
    assert "none of 4" in last["briefing"][1]


def gg_build(picked):
    from starconquest import gauntletgen
    for m in picked:
        m["stage"] = {"players": 2, "nodes": 8, "seed": 1}
    return gauntletgen.build("gen-x", "X", picked, "fastest", 0)


def test_a_run_writes_a_gauntlet_the_game_reads_and_resumes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gen, "RESULTS", tmp_path)
    argv = ["gauntlet_gen.py", "--run", "t", "--seed", "3", "--candidates", "2", "--workers", "1",
            "--probes", "heuristic", "marshal", "--targets", "--spec-json", json.dumps(SMALL)]
    monkeypatch.setattr(sys, "argv", argv)
    gen.main()
    lines = (tmp_path / "t.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["header"] and len(lines) == 1 + 4 * 2
    data = json.loads((tmp_path / "t.json").read_text())
    g = gauntlet.Gauntlet.from_dict(data)
    assert g is not None and gauntlet.problems(g) == [] and 1 <= len(g.stages) <= 2
    assert g.stages[0].target is not None

    # A rerun plays nothing more, and refuses flags that would deal differently.
    monkeypatch.setattr(sys, "argv", argv[:5] + ["--workers", "1", "--probes", "heuristic", "marshal"])
    gen.main()
    assert len((tmp_path / "t.jsonl").read_text().splitlines()) == len(lines)
    assert "0 games to play" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", argv[:5] + ["--seed", "4"])
    with pytest.raises(SystemExit):
        gen.main()
