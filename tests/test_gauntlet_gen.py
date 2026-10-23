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


def _m(key, lost, played=4, turns=50):
    wins = [{"bot": f"b{i}", "won": True, "turns": turns + i, "lost": i} for i in range(played - lost)]
    return {"key": key, "played": played, "wins": wins, "difficulty": lost / played,
            "win_turns": turns if wins else None, "stage": {}}


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


def test_pick_rises_across_the_spread_and_never_takes_an_unwon_map():
    measured = [_m("hard", 4), _m("e1", 0), _m("m1", 2), _m("e2", 1), _m("m2", 3, turns=90), _m("m3", 3)]
    picked = gen.pick(measured, 3)
    assert [p["key"] for p in picked] == ["e1", "m1", "m2"]
    assert gen.pick(measured, 9) == sorted(gen.pick(measured, 9), key=lambda m: (m["difficulty"], m["win_turns"]))
    assert all(p["key"] != "hard" for p in gen.pick(measured, 9))


def test_a_target_is_the_slowest_probe_win_plus_slack():
    m = _m("a", 1, turns=30)       # wins in 30, 31, 32 turns
    assert gen.target(m, 0) == {"turns": 32, "lost": 2}
    assert gen.target(m, 3) == {"turns": 35}


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
    assert all(s.target is not None for s in g.stages)

    # A rerun plays nothing more, and refuses flags that would deal differently.
    monkeypatch.setattr(sys, "argv", argv[:5] + ["--workers", "1", "--probes", "heuristic", "marshal"])
    gen.main()
    assert len((tmp_path / "t.jsonl").read_text().splitlines()) == len(lines)
    assert "0 games to play" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", argv[:5] + ["--seed", "4"])
    with pytest.raises(SystemExit):
        gen.main()
