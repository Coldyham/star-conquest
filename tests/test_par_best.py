"""`tools/par_best.py`: the blend records each stage once, resumes, and never claims a line it cannot replay."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from starconquest import ai
from starconquest.settings import Settings
from tools import par_best

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _models():
    ai.load_models()
    ai.set_budget_scale(math.inf)


def _setup_file(tmp_path):
    cfg = Settings(mode="random", players=2, nodes=3, seed=3)
    cfg.ai_strategy[1] = "rusherplus"
    path = tmp_path / "setup.json"
    path.write_text(json.dumps({"day": "t", "kind": "k", "settings": cfg.to_dict()}))
    return path


def _run(tmp_path, setup):
    return par_best.main(["--daily", str(setup), "--width", "6", "--beam-budget", "1", "--start-k", "2",
                          "--max-k", "2", "--levels", "coarse", "--proof-budget", "0", "--k-budget", "60", "--max-turns", "40",
                          "--open-depth", "0", "--local-seconds", "0",
                          "--results-dir", str(tmp_path / "out")])


def test_each_stage_is_recorded_once_and_a_rerun_repeats_nothing(tmp_path):
    setup = _setup_file(tmp_path)
    _run(tmp_path, setup)
    ledger = tmp_path / "out" / "t_k.jsonl"
    first = ledger.read_text()
    rows = [json.loads(line) for line in first.splitlines()]
    assert [r["stage"] for r in rows] == ["beam", "k", "done"]
    assert rows[1]["k"] == 2 and rows[1]["first"] == 1 and rows[1]["exact"] is True
    assert rows[2]["exact_up_to"] == 2
    _run(tmp_path, setup)   # resumes: nothing run again, nothing appended
    assert ledger.read_text() == first


def test_a_cut_off_pass_runs_again_only_when_given_more_time_or_room():
    cut = {"exact": False, "seconds": 302.2, "budget": 300.0, "max_frontier": 1_000_000}
    assert par_best.settled(cut, 300.0, 1_000_000)
    assert not par_best.settled(cut, 1800.0, 1_000_000)
    assert not par_best.settled(cut, 300.0, 2_000_000)
    assert par_best.settled({**cut, "exact": True}, 1800.0, 2_000_000)
    assert not par_best.settled(None, 300.0, 1_000_000)
    old = {"exact": False, "seconds": 302.2}   # recorded before the limits were
    assert par_best.settled(old, 300.0, 1_000_000) and not par_best.settled(old, 1800.0, 1_000_000)
    rows = [{"stage": "k", "first": 4, "k": 4, "exact": False}, {"stage": "k", "first": 4, "k": 4, "exact": True}]
    assert par_best.latest(rows, "k", lambda r: (r["first"], r["k"])) == [rows[1]]


def test_a_pass_looks_only_for_something_better_than_the_best_so_far(tmp_path):
    args = par_best.argparse.Namespace(levels="all", k_budget=50.0, max_frontier=1000, max_turns=60,
                                       turn_first=True)
    argv = par_best.pass_args(tmp_path / "s.json", 4, 4, (10, 3), args, tmp_path / "k4")
    assert argv[argv.index("--assume-turn") + 1] == "10" and argv[argv.index("--assume-lost") + 1] == "3"
    assert argv[argv.index("--max-launches") + 1] == "4" and argv[argv.index("--from-launches") + 1] == "4"
    assert "--turn-only" in argv   # turns come first: a pass looks only for an earlier win
    assert "--turn-only" not in par_best.pass_args(tmp_path / "s.json", 4, 4, (10, 3), args, tmp_path / "t",
                                                   turn_only=False)
    nothing = par_best.pass_args(tmp_path / "s.json", 1, 1, (math.inf, math.inf), args, tmp_path / "k1")
    assert "--assume-turn" not in nothing   # no line yet: nothing to beat


def test_a_board_the_search_can_finish_is_proved_and_the_passes_stop(tmp_path):
    """2026-11-02's puzzle: the beam already sits on the floor, so the plain search is narrow
    enough to finish (about 7 s), and `par_best` stops there instead of running more passes."""
    setup = ROOT / "tools" / "par_inputs" / "2026-11-02_puzzle.json"
    par_best.main(["--daily", str(setup), "--width", "50", "--beam-budget", "10", "--start-k", "3",
                   "--max-k", "5", "--proof-budget", "120", "--no-proof-splits",
                   "--results-dir", str(tmp_path)])
    rows = [json.loads(line) for line in (tmp_path / "2026-11-02_puzzle.jsonl").read_text().splitlines()]
    assert [r["stage"] for r in rows] == ["beam", "k", "proof", "done"]
    assert rows[2]["splits"] is False and rows[2]["exact"] is True
    done = rows[3]
    assert (done["turn"], done["lost"]) == (6, 7)   # the floor is 6, so no win is sooner
    assert done["proved"] == "single-lane" and "proved" in done["stopped"] and done["replays"] is True


def _args(**over):
    base = {"levels": "all", "k_budget": 600.0, "max_frontier": 1_000_000, "proof_budget": 300.0,
            "results_dir": par_best.RESULTS}
    base.update(over)
    return par_best.argparse.Namespace(**base)


def _summary(**over):
    base = {"name": "x", "turn": 10, "lost": 3, "beam": [11, 3], "exact_up_to": 4, "proved": "",
            "stopped": "", "replays": True}
    base.update(over)
    return base


def test_the_verdict_says_what_is_exact_and_what_to_run_next(tmp_path):
    setup = tmp_path / "s.json"
    passes = [{"stage": "k", "k": 3, "first": 1, "exact": True, "seconds": 20.0},
              {"stage": "k", "k": 4, "first": 4, "exact": True, "seconds": 130.0}]
    tried = [{"stage": "proof", "splits": False, "exact": False}]
    v = par_best.verdict(_summary(), passes, tried, _args(), setup)
    assert v["headline"].startswith("EXACT only for plans of at most 4 launch orders")
    assert any("5 or more launch orders" in line for line in v["not_covered"])
    plus_one = next(n for n in v["next"] if "5 launch orders" in n["why"])
    assert "--max-k 5" in plus_one["command"] and "x6" in plus_one["est"]   # 130 s / 20 s, floored by the real ratio
    assert any("--proof-budget" in n["command"] for n in v["next"])
    proved = par_best.verdict(_summary(proved="two-lane splits"), passes, tried, _args(), setup)
    assert proved["headline"].startswith("EXACT: 10/3 is optimal") and not proved["next"]
    unfinished = [{"stage": "k", "k": 4, "first": 4, "exact": False, "seconds": 600.0}]
    v = par_best.verdict(_summary(exact_up_to=3), unfinished, [], _args(), setup)
    assert any("--k-budget" in n["command"] for n in v["next"])
    assert any("did not finish" in line or "stopped before" in line for line in v["not_covered"])


def test_the_suggested_commands_keep_the_results_dir_so_a_rerun_resumes(tmp_path):
    v = par_best.verdict(_summary(), [{"stage": "k", "k": 4, "first": 4, "exact": True, "seconds": 9.0}], [],
                         _args(results_dir=tmp_path / "out"), tmp_path / "s.json")
    assert all(f"--results-dir {tmp_path / 'out'}" in n["command"] for n in v["next"])


def test_the_opening_and_local_stages_run_once_and_resume(tmp_path):
    setup = _setup_file(tmp_path)
    argv = ["--daily", str(setup), "--width", "6", "--beam-budget", "1", "--start-k", "2", "--max-k", "2",
            "--levels", "coarse", "--proof-budget", "0", "--k-budget", "60", "--max-turns", "40",
            "--open-depth", "2", "--open-budget", "1", "--open-width", "6", "--refine", "2", "--refine-width", "6",
            "--refine-budget", "1", "--workers", "1", "--local-seconds", "2", "--results-dir", str(tmp_path / "out")]
    par_best.main(argv)
    ledger = tmp_path / "out" / "t_k.jsonl"
    first = ledger.read_text()
    rows = [json.loads(line) for line in first.splitlines()]
    assert [r["stage"] for r in rows] == ["beam", "k", "open", "local", "done"]
    assert rows[-1]["turn"] <= rows[0]["turn"]
    par_best.main(argv)
    assert ledger.read_text() == first
