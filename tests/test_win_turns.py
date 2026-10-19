"""How many turns a bot takes to win (`tools/win_turns.py`).

The bench reads a result as the board ranks it, counts a game not won as slower
than any win, and pairs two arms game by game, so these check the reading on
hand-built rows, one real game read from the bot's seat, and the ledger rule
that keeps one tree's games apart from another's.
"""

from __future__ import annotations

import json
import math
import shutil

import pytest

from starconquest import ai
from tests.test_replay import _preserve_config
from tools import win_turns as wt


def _row(seed: int, seat: int = 1, winner: int | None = 1, turns: int = 40, lost: int = 10,
         cell: str = "random:7:6:2", vs: str = "heuristic") -> dict:
    return {"cell": cell, "vs": vs, "seed": seed, "seat": seat, "winner": winner,
            "turns": turns, "lost": lost, "to75": None, "to90": None, "low": 0.5}


# --------------------------------------------------------------------------- #
# Reading a result
# --------------------------------------------------------------------------- #
def test_a_win_outranks_any_loss_then_fewer_turns_then_fewer_ships():
    slow_win = wt.standing(_row(1, turns=90, lost=50))
    assert slow_win > wt.standing(_row(1, winner=2, turns=10, lost=0))
    assert wt.standing(_row(1, turns=30)) > wt.standing(_row(1, turns=31))
    assert wt.standing(_row(1, turns=30, lost=5)) > wt.standing(_row(1, turns=30, lost=6))


def test_every_game_not_won_ranks_level():
    """A loss, a draw and a game left at the turn limit are all not a win."""
    lost, drawn, unfinished = _row(1, winner=2), _row(1, winner=0), _row(1, winner=None)
    assert wt.standing(lost) == wt.standing(drawn) == wt.standing(unfinished)


def test_a_game_not_won_counts_slower_than_any_win():
    inf = math.inf
    assert wt.quantile([30.0, 10.0, 20.0], 0.5) == 20
    assert wt.quantile([10.0, 20.0, 30.0, 40.0], 0.5) == 25
    assert wt.quantile([10.0, 20.0, inf], 0.5) == 20
    assert wt.quantile([10.0, 20.0, inf, inf], 0.5) == inf
    assert wt.quantile([10.0, inf, inf], 0.25) == inf
    assert wt.quantile([inf, 10.0, 20.0, 30.0, 40.0], 0.75) == 40


def test_a_percentile_on_the_last_win_is_that_win():
    """Five wins and four games not won put the median exactly on the fifth
    win, with nothing to interpolate towards (not `0 * inf`)."""
    xs = [10.0, 20.0, 30.0, 40.0, 50.0] + [math.inf] * 4
    assert wt.quantile(xs, 0.5) == 50


def test_z_is_infinite_only_when_every_seed_agrees_off_the_fair_share():
    assert wt.mean_z([1.0, 1.0, 1.0], 0.5) == (1.0, math.inf)
    assert wt.mean_z([0.5, 0.5], 0.5) == (0.5, 0.0)
    m, z = wt.mean_z([0.0, 1.0, 1.0, 1.0], 0.5)
    assert m == 0.75 and 0 < z < math.inf


# --------------------------------------------------------------------------- #
# Who sits where
# --------------------------------------------------------------------------- #
def test_every_seed_puts_the_bot_in_every_seat_once():
    bot, h, r = wt.Seat.parse("knower:3"), wt.Seat("heuristic"), wt.Seat("rusherplus")
    rotations = wt.seatings(bot, [h, r])
    assert [seats.index(bot) for seats in rotations] == [0, 1, 2]
    for seats in rotations:
        k = seats.index(bot)
        assert (seats[(k + 1) % 3], seats[(k + 2) % 3]) == (h, r), "the line-up keeps its order"


def test_a_line_up_plays_the_cells_it_fits():
    duel, three = wt.Cell.parse("random:7:6:2"), wt.Cell.parse("random:11:6:3")
    one, two = wt.lineup("heuristic"), wt.lineup("heuristic+rusherplus")
    assert wt.rivals(one, three) == [wt.Seat("heuristic")] * 2
    assert wt.rivals(two, duel) is None
    games = wt.schedule([wt.Seat("marshal")], [one, two], [duel, three], range(5, 8))
    by_group = {(str(g.cell), g.vs) for g in games}
    assert by_group == {("random:7:6:2", "heuristic"), ("random:11:6:3", "heuristic"),
                        ("random:11:6:3", "heuristic+rusherplus")}
    assert len(games) == 3 * (2 + 3 + 3)
    assert len({g.key for g in games}) == len(games)


def test_a_cell_fills_in_speed_and_seats():
    assert str(wt.Cell.parse("random:7")) == "random:7:6:2"
    assert str(wt.Cell.parse("random:13:3")) == "random:13:3:2"
    assert str(wt.Cell.parse("symmetric:12:6:3")) == "symmetric:12:6:3"


# --------------------------------------------------------------------------- #
# Pairing two arms
# --------------------------------------------------------------------------- #
def test_an_arm_against_itself_is_level():
    """Seat 1 wins every seed and seat 2 loses it, so half the pairs are both won."""
    rows = [_row(s, seat=k, turns=30 + s + k) for s in range(4) for k in (1, 2)]
    p = wt.pair(rows, rows)[("random:7:6:2", "heuristic")]
    assert p.pairs == 8 and p.both_won == 4
    assert wt.mean_z(p.beats, 0.5) == (0.5, 0.0)
    assert wt.mean_z(p.dwon) == (0.0, 0.0)
    assert wt.mean_z(p.log_ratio) == (0.0, 0.0)


def test_a_pair_scores_by_seed_over_the_games_both_played():
    """Seed 1: a win in 30 against 40, and a loss where the base won. Seed 2: a
    win in 20 against 20 with fewer ships lost. Seat 2 of seed 2 the base never
    played, so it is no pair at all."""
    a = [_row(1, seat=1, turns=30), _row(1, seat=2, winner=1),
         _row(2, seat=1, turns=20, lost=3), _row(2, seat=2, turns=10)]
    b = [_row(1, seat=1, turns=40), _row(1, seat=2, winner=2, turns=50),
         _row(2, seat=1, turns=20, lost=4)]
    p = wt.pair(a, b)[("random:7:6:2", "heuristic")]
    assert p.pairs == 3 and p.both_won == 2
    assert sorted(p.beats) == [0.5, 1.0]
    assert sorted(p.dwon) == [-0.5, 0.0]
    assert sorted(p.log_ratio) == pytest.approx([math.log(30 / 40), 0.0])


def test_a_pairs_gap_splits_by_phase_and_adds_up():
    """10 turns to the 3:1 lead, 10 more to 9:1 and 10 to the win, against 15, 7
    and 18: five turns saved before the lead, three lost pressing it, eight saved
    at the end."""
    a = {**_row(1, turns=30), "to75": 10, "to90": 20}
    b = {**_row(1, turns=40), "to75": 15, "to90": 22}
    gaps = wt.pair([a], [b])[("random:7:6:2", "heuristic")].phase_gaps
    assert gaps == [(-5, 3, -8)] and sum(gaps[0]) == a["turns"] - b["turns"]
    assert wt.pair([_row(1, turns=30)], [b])[("random:7:6:2", "heuristic")].phase_gaps == [], \
        "a row without its marks splits nothing"


def test_the_reading_counts_unfinished_games_apart_from_losses():
    rows = [_row(1, turns=30), _row(2, turns=50), _row(3, winner=2, turns=60),
            _row(4, winner=None, turns=wt.MAX_TURNS)]
    a = wt.arm_reading(rows)
    assert a["games"] == 4 and a["won"] == 0.5 and a["unfinished"] == 1
    assert a["p25"] == 45 and a["p50"] == math.inf


# --------------------------------------------------------------------------- #
# A real game
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def loaded():
    assert "marshal" in ai.load_models(), "models/marshal.py failed to import"


def test_a_game_reads_the_same_twice_and_its_marks_fall_in_order(loaded):
    cell = wt.Cell.parse("random:7:6:2")
    seats = (wt.Seat("heuristic"), wt.Seat("marshal"))
    game = wt.Game(wt.Seat("marshal"), cell, "heuristic", 7, seats, 2)
    with _preserve_config():
        first, second = wt.play(game), wt.play(game)
    del first["secs"], second["secs"]
    assert first == second
    assert first["seats"] == ["heuristic", "marshal"] and first["seat"] == 2
    assert wt.won(first), "marshal takes this map from heuristic"
    assert first["to75"] <= first["to90"] <= first["turns"], \
        "a win holds every player's system, so it reaches every mark"
    assert 0 < first["low"] <= 0.5


# --------------------------------------------------------------------------- #
# Ledgers
# --------------------------------------------------------------------------- #
def test_the_rev_moves_with_the_code_that_decides_a_game(tmp_path, monkeypatch):
    for sub in ("starconquest", "models"):
        shutil.copytree(wt.ROOT / sub, tmp_path / sub,
                        ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(wt, "ROOT", tmp_path)
    rev = wt.play_rev()
    (tmp_path / "starconquest" / "render.py").write_text("# drawn differently\n")
    assert wt.play_rev() == rev, "the shell decides nothing"
    reseed = tmp_path / "starconquest" / "replay.py"
    reseed.write_text(reseed.read_text() + "\n")
    moved = wt.play_rev()
    assert moved != rev, "replay.reseed derives every turn's rng"
    model = tmp_path / "models" / "thinker.py"
    model.write_text(model.read_text() + "\n")
    rival = wt.play_rev()
    assert rival != moved, "a rival is part of the game"
    monkeypatch.setattr(wt, "SHAPE", wt.SHAPE + 1)
    assert wt.play_rev() != rival, "a row of another shape is another ledger"


def test_a_named_ledger_refuses_another_trees_games(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wt, "RESULTS", tmp_path)
    path = tmp_path / "base" / "heuristic.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({**_row(wt.SEED_BASE), "rev": "0000beef"}) + "\n")
    with pytest.raises(SystemExit):
        wt.main(["--bot", "heuristic", "--vs", "heuristic", "--cell", "random:7:6:2",
                 "--seeds", "1", "--label", "base", "--jobs", "1"])
    assert "0000beef" in capsys.readouterr().err
    assert len(path.read_text().splitlines()) == 1, "nothing was played into it"


def test_a_report_reads_another_trees_ledger_and_plays_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wt, "RESULTS", tmp_path)
    path = tmp_path / "base" / "heuristic.jsonl"
    path.parent.mkdir()
    rows = [{**_row(wt.SEED_BASE, seat=k, winner=1, turns=40), "rev": "0000beef"} for k in (1, 2)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert wt.main(["--bot", "heuristic", "--vs", "heuristic", "--cell", "random:7:6:2",
                    "--seeds", "1", "--label", "base", "--report"]) == 0
    out = capsys.readouterr().out
    assert "rev 0000beef" in out and "50.0%" in out
    assert len(path.read_text().splitlines()) == 2


def test_a_base_pairs_each_bot_against_its_own_rows(tmp_path, monkeypatch, capsys):
    """Two trees' ledgers for one bot: the new one wins seat 1 sooner and loses
    seat 2, so it beats the base once and loses once, and is 25% faster on the
    one game both won."""
    monkeypatch.setattr(wt, "RESULTS", tmp_path)
    played = {"try": [_row(wt.SEED_BASE, seat=1, turns=30), _row(wt.SEED_BASE, seat=2, winner=1)],
              "base": [_row(wt.SEED_BASE, seat=1, turns=40), _row(wt.SEED_BASE, seat=2, winner=2)]}
    for label, rows in played.items():
        path = tmp_path / label / "marshal.jsonl"
        path.parent.mkdir()
        path.write_text("".join(json.dumps({**r, "rev": label}) + "\n" for r in rows))
    assert wt.main(["--bot", "marshal", "--vs", "heuristic", "--cell", "random:7:6:2", "--seeds", "1",
                    "--label", "try", "--base", "base", "--report"]) == 0
    out = capsys.readouterr().out
    assert "marshal [try] against marshal [base]" in out
    pooled = next(line for line in out.splitlines() if line.strip().startswith("pooled")).split()
    pairs, beats, _, dwon, _, both, dturns, _ = pooled[1:]
    assert (pairs, beats, dwon, both, dturns) == ("2", "50.0%", "-50.0pt", "1", "-25.0%")


def test_a_missing_base_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wt, "RESULTS", tmp_path)
    with pytest.raises(SystemExit):
        wt.main(["--bot", "marshal", "--vs", "heuristic", "--cell", "random:7:6:2", "--seeds", "1",
                 "--base", "nowhere", "--report"])
    assert "no ledger" in capsys.readouterr().err


def test_a_report_with_nothing_played_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wt, "RESULTS", tmp_path)
    assert wt.main(["--bot", "heuristic", "--vs", "heuristic", "--cell", "random:7:6:2",
                    "--seeds", "1", "--report"]) == 1
    assert "no games" in capsys.readouterr().out
