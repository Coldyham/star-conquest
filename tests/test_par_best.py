"""`tools/par_best.py`: the blend records each stage once, resumes, and never claims a line it cannot replay."""

from __future__ import annotations

import json
import math

import pytest

from starconquest import ai
from starconquest.settings import Settings
from tools import par_best


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
                          "--max-k", "2", "--levels", "coarse", "--k-budget", "60", "--max-turns", "40",
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


def test_a_pass_looks_only_for_something_better_than_the_best_so_far(tmp_path):
    args = par_best.argparse.Namespace(levels="all", k_budget=50.0, max_frontier=1000, max_turns=60)
    argv = par_best.pass_args(tmp_path / "s.json", 4, 4, (10, 3), args, tmp_path / "k4")
    assert argv[argv.index("--assume-turn") + 1] == "10" and argv[argv.index("--assume-lost") + 1] == "3"
    assert argv[argv.index("--max-launches") + 1] == "4" and argv[argv.index("--from-launches") + 1] == "4"
    nothing = par_best.pass_args(tmp_path / "s.json", 1, 1, (math.inf, math.inf), args, tmp_path / "k1")
    assert "--assume-turn" not in nothing   # no line yet: nothing to beat
