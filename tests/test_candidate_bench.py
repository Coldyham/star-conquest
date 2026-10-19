"""A bot tried as one more of knower's candidates (`tools/candidate_bench.py`).

The bench's reading rests on one property: a seat under test whose candidate
proposes nothing plays exactly as knower does, so every point off the fair share
is the candidate's. These check that on real games at Search and Council, that
the candidate is offered last and (at Council) played on by its own bot, the
scoring on hand-built rows, and the ledger rule that keeps one tree's games apart
from another's.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import replace

import pytest

from starconquest import ai, engine, replay
from starconquest.settings import Settings, build_state
from tests.test_replay import _preserve_config
from tools import candidate_bench as cb

DUEL, THREE = cb.Cell.parse("random:7:6:2"), cb.Cell.parse("random:11:6:3")
WRAPPED = ("_externals", "_search", "_candidates", "_external_plan", "_advance",
           "_policy_advance")


def _row(seed: int, seats, winner: int | None, cell=DUEL) -> dict:
    return {"cell": str(cell), "seed": seed, "seats": list(seats), "winner": winner,
            "turns": 40, "secs": 1.0, "ms": {}, "tally": {}}


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_every_seating_puts_the_arm_in_every_seat_once():
    assert cb.seatings(DUEL, cb.VARIANT, cb.BASE) == [(cb.VARIANT, cb.BASE), (cb.BASE, cb.VARIANT)]
    three = cb.seatings(THREE, cb.VARIANT, "marshal")
    assert [s.index(cb.VARIANT) for s in three] == [0, 1, 2]
    assert all(s.count("marshal") == 2 for s in three)


def test_head_to_head_plays_each_seating_once_and_arms_play_both():
    h2h = list(cb.schedule(DUEL, "knower", range(5, 7)))
    assert len(h2h) == 4 and {s for _, s in h2h} == set(cb.seatings(DUEL, cb.VARIANT, cb.BASE))
    arms = list(cb.schedule(THREE, "marshal", range(5, 7)))
    assert len(arms) == 2 * 2 * 3
    assert {seat for _, seats in arms for seat in seats} == {cb.VARIANT, cb.BASE, "marshal"}


def test_a_seed_scores_the_arms_mean_over_every_seating():
    """Seed 1 is won from either seat, seed 2 only from seat 1, seed 3 runs out
    of turns both times (the fair share), and seed 4 lacks a seating."""
    v, b = cb.VARIANT, cb.BASE
    rows = [_row(1, (v, b), 1), _row(1, (b, v), 2),
            _row(2, (v, b), 1), _row(2, (b, v), 1),
            _row(3, (v, b), None), _row(3, (b, v), None),
            _row(4, (v, b), 1)]
    assert cb.per_seed(rows, DUEL, v, b) == {1: 1.0, 2: 0.5, 3: 0.5}
    three = [_row(9, s, 1, THREE) for s in cb.seatings(THREE, v, b)]
    assert cb.per_seed(three, THREE, v, b) == {9: pytest.approx(1 / 3)}


def test_z_is_infinite_only_when_every_seed_agrees_off_the_fair_share():
    assert cb._mean_z([1.0, 1.0], 0.5) == (1.0, math.inf, 2)
    assert cb._mean_z([0.0, 0.0], 0.5) == (0.0, -math.inf, 2)
    assert cb._mean_z([0.5, 0.5], 0.5) == (0.5, 0.0, 2)
    m, z, n = cb._mean_z([0.0, 1.0, 1.0], 0.5)
    assert n == 3 and 0 < z < math.inf


# --------------------------------------------------------------------------- #
# Real games
# --------------------------------------------------------------------------- #
@pytest.fixture
def knower(monkeypatch):
    """knower's module with the bench's wrappers undone after the test, and every
    knob `play_one` moves put back."""
    if "knower" not in ai.STRATEGIES or "evolved" not in ai.STRATEGIES:
        ai.load_models()
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    for name in WRAPPED:
        monkeypatch.setattr(k, name, getattr(k, name))
    for fn in set(ai.STRATEGIES.values()):
        module = sys.modules.get(fn.__module__)
        if isinstance(getattr(module, "BUDGET_SCALE", None), float):
            monkeypatch.setattr(module, "BUDGET_SCALE", module.BUDGET_SCALE)
    for name in ("_patched", "_under_test", "_borrow", "_tally", "MAX_TURNS"):
        monkeypatch.setattr(cb, name, getattr(cb, name))
    with _preserve_config():
        yield k


def _trace(monkeypatch) -> list[str]:
    """Every board a game reaches, as each turn begins."""
    boards: list[str] = []
    real = replay.reseed

    def reseed(state):
        boards.append(hashlib.sha1(repr((
            sorted((s.id, s.owner_id, s.ships) for s in state.systems.values()),
            sorted((f.owner_id, f.source_id, f.dest_id, f.ships) for f in state.fleets),
        )).encode()).hexdigest())
        return real(state)
    monkeypatch.setattr(replay, "reseed", reseed)
    return boards


@pytest.mark.parametrize("stop", [2, 3])
def test_a_seat_whose_candidate_proposes_nothing_plays_as_knower(knower, monkeypatch, stop):
    monkeypatch.setattr(cb, "MAX_TURNS", 30)
    boards = _trace(monkeypatch)
    nothing = ("no_such_bot", None)
    base = cb.play_one(DUEL, 7, (cb.BASE, cb.BASE), stop, nothing, None)
    played = list(boards)
    boards.clear()
    tried = cb.play_one(DUEL, 7, (cb.VARIANT, cb.BASE), stop, nothing, None)
    assert boards == played and (tried["winner"], tried["turns"]) == (base["winner"], base["turns"])
    searches = sum(t["decides"] for t in tried["tally"].values())
    assert searches > 0
    assert sum(t["borrow"].get("absent", 0) for t in tried["tally"].values()) == searches
    assert base["tally"] == {}, "no seat is under test, so nothing is counted"


def test_the_candidate_is_offered_on_every_search_and_its_picks_are_counted(knower, monkeypatch):
    monkeypatch.setattr(cb, "MAX_TURNS", 30)
    row = cb.play_one(DUEL, 7, (cb.VARIANT, cb.BASE), 2, ("evolved", 0.0), None)
    tally = [t for ph, t in row["tally"].items() if ph != "errors"]
    assert "errors" not in row["tally"]
    searches = sum(t["decides"] for t in tally)
    assert searches > 0 and sum(t["borrow"]["offered"] for t in tally) == searches
    labels = {label for t in tally for label in t["picks"]}
    assert labels <= {"default", "timid", "rusherplus", "heuristic", "marshal", "thinker:0",
                      "evolved:0"}
    picked = sum(t["picks"].get("evolved:0", 0) for t in tally)
    assert sum(t["borrow"].get("chosen", 0) for t in tally) == picked


@pytest.mark.parametrize("stop,rolled", [(2, False), (3, True)])
def test_at_council_the_candidates_line_is_played_on_by_its_bot(knower, monkeypatch, stop, rolled):
    """Search asks the candidate for its opening once; Council asks again on every
    turn its line is rolled forward."""
    real = ai.STRATEGIES["evolved"]
    asked: list[int] = []

    def counting(state, pid):
        asked.append(state.turn)
        return real(state, pid)
    monkeypatch.setitem(ai.STRATEGIES, "evolved", counting)
    state = build_state(Settings(mode="random", nodes=7, players=2, ship_ly_per_turn=6,
                                 autoplay=True), 7)
    for pid in (1, 2):
        player = state.players[pid]
        player.ai_strategy, player.ai_params = "knower", replace(player.ai_params, aux=float(stop))
    cb._patch()
    ai.set_budget_scale(math.inf)
    monkeypatch.setattr(cb, "_under_test", frozenset({1}))
    monkeypatch.setattr(cb, "_borrow", ("evolved", 0.0))
    replay.reseed(state)
    engine.decide_seat(state, 2, ai.decide)
    assert asked == [], "a seat not under test never borrows it"
    engine.decide_seat(state, 1, ai.decide)
    assert asked[0] == state.turn
    assert (len(asked) > 1) is rolled and asked == sorted(asked)


# --------------------------------------------------------------------------- #
# Ledgers
# --------------------------------------------------------------------------- #
def _write(path, rows) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_a_named_ledger_refuses_another_trees_games(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cb, "RESULTS", tmp_path)
    path = cb.ledger("base", cb.Seat.parse("evolved:0"), 2)
    _write(path, [{**_row(cb.SEED_BASE, (cb.VARIANT, cb.BASE), 1), "rev": "0000beef"}])
    with pytest.raises(SystemExit):
        cb.main(["--borrow", "evolved:0", "--cell", str(DUEL), "--seeds", "1",
                 "--label", "base", "--jobs", "1"])
    assert "0000beef" in capsys.readouterr().err
    assert len(path.read_text().splitlines()) == 1, "nothing was played into it"


def test_a_report_reads_another_trees_ledger_and_plays_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cb, "RESULTS", tmp_path)
    path = cb.ledger("base", cb.Seat.parse("evolved:0"), 2)
    v, b = cb.VARIANT, cb.BASE
    _write(path, [{**_row(cb.SEED_BASE, (v, b), 1), "rev": "0000beef"},
                  {**_row(cb.SEED_BASE, (b, v), 1), "rev": "0000beef"}])
    assert cb.main(["--borrow", "evolved:0", "--cell", str(DUEL), "--seeds", "1",
                    "--label", "base", "--report"]) == 0
    assert "50.0%" in capsys.readouterr().out
    assert len(path.read_text().splitlines()) == 2


def test_a_report_with_nothing_played_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cb, "RESULTS", tmp_path)
    assert cb.main(["--borrow", "evolved:0", "--cell", str(DUEL), "--seeds", "1",
                    "--report"]) == 1
    assert "no games" in capsys.readouterr().out
