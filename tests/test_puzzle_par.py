"""`tools/puzzle_par.py`: a puzzle's par is stored only when its line replays, and a
stored par is only ever improved."""

from __future__ import annotations

import json
import math
from pathlib import Path

from starconquest import ai, engine, replay
from starconquest.settings import Settings
from tools import par_search as ps
from tools import puzzle_par as pp

ROOT = Path(__file__).resolve().parent.parent


def _summary(**over) -> dict:
    base = {"turn": 10, "lost": 3, "proved": "", "turn_only": True, "exact_up_to": 4, "floor": 7,
            "replays": True, "verdict": {"headline": "EXACT only for plans of at most 4 launch orders"}}
    return {**base, **over}


def _row(turns=10, lost=3, status=pp.LIKELY, **over) -> dict:
    return {"day": "2026-11-04", "turns": turns, "lost": lost, "status": status, "turns_only": False,
            "exact_up_to": 4, "floor": 7, "rests_on": "x", "line": {}, "rules_version": engine.RULES_VERSION,
            **over}


def test_a_par_is_stored_only_for_a_win_whose_line_replays():
    row = pp.par_row("2026-11-04", _summary(), {"turns": []})
    assert row is not None
    assert (row["turns"], row["lost"], row["status"], row["turns_only"]) == (10, 3, pp.LIKELY, False)
    assert row["rules_version"] == engine.RULES_VERSION and row["exact_up_to"] == 4
    proved = pp.par_row("2026-11-04", _summary(proved="two-lane splits"), {})
    assert proved is not None and (proved["status"], proved["turns_only"]) == (pp.PROVED, True)
    assert pp.par_row("2026-11-04", _summary(replays=False), {}) is None
    assert pp.par_row("2026-11-04", _summary(turn=math.inf), {}) is None
    assert pp.par_row("2026-11-04", _summary(), None) is None


def test_a_stored_par_is_only_ever_improved():
    old = _row()
    assert pp.better(_row(11, 0), old) is None                       # later
    assert pp.better(_row(10, 4), old) is None                       # same turn, more lost
    assert pp.better(_row(10, 3), old) is None                       # nothing new
    assert pp.better(_row(9, 9), old)["turns"] == 9                   # sooner
    assert pp.better(_row(10, 3, pp.PROVED), old)["status"] == pp.PROVED
    assert pp.better(_row(10, 3, exact_up_to=5), old)["exact_up_to"] == 5
    assert pp.better(_row(10, 3), _row(10, 3, pp.PROVED)) is None     # never unproves
    kept = pp.better(_row(10, 2), _row(10, 3, pp.PROVED))            # a proved turn stays proved
    assert (kept["lost"], kept["status"], kept["turns_only"]) == (2, pp.PROVED, True)
    assert pp.better(_row(12, 9), _row(rules_version=engine.RULES_VERSION - 1))["turns"] == 12
    assert pp.better(_row(), None) is not None


def test_par_bests_options_pass_through_and_the_ledger_is_its_own():
    args, best = pp.parse_args(["--days", "2026-11-04", "--", "--k-budget", "60", "--max-k", "4"])
    assert args.days == ["2026-11-04"] and best.k_budget == 60 and best.max_k == 4
    assert best.results_dir == pp.RESULTS
    settings = Settings(mode="random", players=2, nodes=5, seed=3).to_dict()
    assert pp.work_dir(pp.RESULTS, settings) == pp.RESULTS / Settings.from_dict(settings).challenge_key()


def test_a_stored_puzzle_is_solved_and_its_row_replays(tmp_path):
    """2026-11-05's puzzle: the beam sits on the par (5/3), which the proof settles in seconds."""
    ai.load_models()
    ai.set_budget_scale(math.inf)
    data = json.loads((ROOT / "tools" / "par_inputs" / "2026-11-05_puzzle.json").read_text())
    _args, best = pp.parse_args(["--", "--width", "30", "--beam-budget", "20", "--start-k", "1", "--max-k", "1",
                                 "--proof-budget", "60", "--no-proof-splits", "--open-depth", "0",
                                 "--local-seconds", "0", "--results-dir", str(tmp_path)])
    row = pp.solve(data["day"], data["settings"], best)
    assert row is not None and (row["turns"], row["lost"]) == (5, 3) and row["status"] == pp.PROVED
    log = replay.GameLog.from_dict(json.loads(json.dumps(row["line"])))
    assert ps.check_line(log, 1) == (True, 5, 3)
