"""`tools/par_show.py`: a saved line printed as orders and boards, from the engine's own replay."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from starconquest import ai, replay
from starconquest.model import Order
from starconquest.settings import Settings, build_state
from tools import par_search as ps
from tools import par_show

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _models():
    ai.load_models()
    ai.set_budget_scale(math.inf)


def _known_line(tmp_path) -> Path:
    """2026-11-04's puzzle: 6 ships home to Dust-Ball on turn 2, 5 more and a forward on turn 6."""
    data = json.loads((ROOT / "tools" / "par_inputs" / "2026-11-04_puzzle.json").read_text())
    cfg = Settings.from_dict(data["settings"])
    board = build_state(cfg, cfg.seed)
    me = board.human().id
    log = replay.GameLog(seed=cfg.seed, settings=cfg.to_dict(), strategies=replay.seat_strategies(board))
    plan = {2: [Order(me, 0, 4, 6)], 6: [Order(me, 0, 4, 5), Order(me, 4, 3, 3)]}
    for t in range(10):
        log.record_turn(ps.step(board, plan.get(t, [])), human_ai=False)
    log.mark_finished(me)
    log.path = tmp_path / "line.best.json"
    log.save()
    return log.path


def test_it_prints_the_orders_by_name_and_the_result(tmp_path, capsys):
    assert par_show.main([str(_known_line(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "turn  2: send  6 ships" in out and "turn  6: send  5 ships" in out and "turn  6: send  3 ships" in out
    assert "Uñallamacha -> Dust-Ball" in out and "Dust-Ball -> Asterope" in out
    assert "End Turn with nothing sent on turn(s) 0, 1, 3, 4, 5, 7, 8, 9" in out
    assert "THE RIVALS' ORDERS" in out and "9 ships  Asterope -> Dust-Ball" in out   # rusherplus's opening send
    assert "result: YOU WIN on turn 10, ships lost 3" in out
    assert "T0: " in out and "T10: " in out   # a board for every turn start, and the last


def test_no_board_leaves_the_checkpoints_out(tmp_path, capsys):
    par_show.main([str(_known_line(tmp_path)), "--no-board"])
    out = capsys.readouterr().out
    assert "BOARD AT THE START" not in out and "YOUR ORDERS" in out
