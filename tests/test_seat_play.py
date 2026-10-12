"""tools/seat_play.py: a seat played by hand from the command line."""

from __future__ import annotations

import json

import pytest

from starconquest import replay
from starconquest.settings import Settings
from tools import seat_play


@pytest.fixture
def game(tmp_path, monkeypatch, capsys):
    """A fresh 3-seat game in a scratch games dir; returns a runner for the CLI."""
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    cfg = Settings(players=3, nodes=12, seed=4242, fog_sight=1, fog_scout=2)
    cfg.ai_strategy = ["heuristic", "heuristic", "heuristic"]

    def run(*argv: str) -> str:
        seat_play.main(list(argv))
        return capsys.readouterr().out

    run("new", "--token", cfg.to_token())
    return run


def _log() -> replay.GameLog:
    return replay.load(seat_play.find_log(None))


def test_turns_land_in_a_log_that_replays_to_the_same_board(game):
    _, state, _, _, _ = seat_play.rebuild(seat_play.find_log(None))
    home = next(s for s in state.systems.values() if s.owner_id == 1)
    game("turn", f"{home.id}>{home.neighbors[0]}:{home.ships}", "--why", "all in")
    game("wait", "3", "--why", "in flight")
    log = _log()
    assert 1 <= log.turn_count <= 4
    assert log.hand_turns == log.turn_count          # every turn is the seat's own
    assert log.turns[0]["orders"][0]["ships"] == home.ships
    first, _ = replay.reconstruct(log)
    second, _ = replay.reconstruct(replay.load(log.path))
    assert replay.board_digest(first) == replay.board_digest(second)


def test_each_decision_is_noted_beside_the_log(game):
    game("turn", "--why", "hold")
    notes = [json.loads(line) for line in
             seat_play.notes_path(_log().path).read_text().splitlines()]
    assert notes == [{"turn": 0, "orders": [], "why": "hold"}]


def test_a_bad_order_costs_a_retype_not_a_turn(game):
    _, state, _, _, _ = seat_play.rebuild(seat_play.find_log(None))
    home = next(s for s in state.systems.values() if s.owner_id == 1)
    rival = next(s for s in state.systems.values() if s.owner_id == 2)
    for order in (f"{rival.id}>{rival.neighbors[0]}:1",              # not ours
                  f"{home.id}>{home.id}:1",                          # no such lane
                  f"{home.id}>{home.neighbors[0]}:{home.ships + 1}",  # too many
                  "garbage"):
        with pytest.raises(SystemExit):
            game("turn", order)
    assert _log().turn_count == 0


def test_the_view_hides_what_the_seat_cannot_see(game):
    out = game("show")
    _, state, _, _, _ = seat_play.rebuild(seat_play.find_log(None))
    rival = next(s for s in state.systems.values() if s.owner_id == 2)
    assert "never sighted" in out
    assert f"  {rival.id:3d}  P2" not in out
