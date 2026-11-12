"""`tools/check_bot.py` over every shipped bot, and the order checks it makes.

A bot in `models/` passes the same checks an outside author is told to run
(`docs/bot-brief.md`, "Hard rules"), so one written without its own tests is
still held to them. Its strength is not tested here: that is the ladder's job.
"""

from __future__ import annotations

import pytest

from starconquest import ai
from starconquest.model import Order
from tools import check_bot

SHIPPED = [path.stem for path in sorted(ai.MODELS_DIR.glob("*.py"))
           if not path.stem.startswith("_")]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_bot_passes_check_bot(name, capsys):
    status = check_bot.check(name, games=2, opponent="heuristic")
    assert status == 0, capsys.readouterr().out


def _greedy(state, pid):
    """Every owned system asks for more than it holds, once fleets are out."""
    return [Order(pid, s.id, s.neighbors[0], s.ships + (2 if state.fleets else 0))
            for s in state.systems.values() if s.owner_id == pid and s.ships > 1]


def test_an_order_for_more_than_the_garrison_fails(monkeypatch, capsys):
    monkeypatch.setitem(ai.STRATEGIES, "greedy_test", _greedy)
    assert check_bot.check("greedy_test", games=1, opponent="heuristic") == 1
    out = capsys.readouterr().out
    assert "FAIL  asks only for ships it has" in out
    assert "PASS  issues legal orders" in out
    assert ai.STRATEGIES["greedy_test"] is _greedy


def test_orders_from_one_system_share_its_garrison():
    state = check_bot._board()
    home = next(s for s in state.systems.values() if s.owner_id == check_bot.SEAT)
    a = b = home.neighbors[0]
    half = home.ships // 2
    fits = [Order(1, home.id, a, half), Order(1, home.id, b, home.ships - half)]
    assert check_bot._order_problems(state, fits, 1) == ([], [])
    over = [Order(1, home.id, a, half), Order(1, home.id, b, home.ships)]
    assert check_bot._order_problems(state, over, 1) == ([], [(over[1], home.ships - half)])
    empty = [Order(1, home.id, a, home.ships), Order(1, home.id, b, 1)]
    dropped, oversized = check_bot._order_problems(state, empty, 1)
    assert [o for o, _ in dropped] == [empty[1]] and oversized == []
