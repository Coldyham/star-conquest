"""knower pricing pile-ups by the garrison-fights-last rule (`tools/pileup_bench.py`).

The bench's reading rests on its stock arm playing exactly as knower ships, and
on each arm changing only what it says: the defensive fold only where two rivals
land on one of our systems on the same turn, the attack price only where a third
seat lands on the target with us, and never downwards. These check that, the
seating, the scoring on hand-built rows, and the ledger rule that keeps one
tree's games apart from another's.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace

import pytest

from starconquest import ai, replay
from starconquest.model import Fleet
from starconquest.settings import Settings, build_state
from tests.test_replay import _preserve_config
from tools import pileup_bench as pb


@pytest.fixture
def knower(monkeypatch):
    """knower's module with the bench's wrappers undone after the test, and every
    knob `play_one` moves put back."""
    if "knower" not in ai.STRATEGIES:
        ai.load_models()
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    for name in ("_enemy_arrivals", "_required"):
        monkeypatch.setattr(k, name, getattr(k, name))
    for fn in set(ai.STRATEGIES.values()):
        module = sys.modules.get(fn.__module__)
        if isinstance(getattr(module, "BUDGET_SCALE", None), float):
            monkeypatch.setattr(module, "BUDGET_SCALE", module.BUDGET_SCALE)
    for name in ("_patched", "_defend", "_attack", "_fires", "MAX_TURNS"):
        monkeypatch.setattr(pb, name, getattr(pb, name))
    with _preserve_config():
        yield k


def _board(k):
    """A four-seat board with no fleets, the bench's wrappers on, and a system of
    pid 1's next to one of pid 2's."""
    spec = {key: v for key, v in pb.SETUPS["p4"].items() if key != "key"}
    state = build_state(Settings.from_dict(spec), pb.SEED_BASE)
    state.fleets.clear()
    pb._patch()
    pb._fires = {"defend": [0, 0], "attack": [0, 0]}
    ours = next(s for s in state.systems.values() if s.owner_id == 1)
    theirs = state.systems[ours.neighbors[0]]
    theirs.owner_id, theirs.ships = 2, 8
    return state, ours, theirs


def _land(state, owner, dest, ships, turns):
    state.fleets.append(Fleet(owner, dest, dest, ships, turns, turns))


# --------------------------------------------------------------------------- #
# Seating and scoring
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("setup", list(pb.SETUPS))
def test_the_seat_under_test_turns_through_every_seat(setup):
    n = pb.SETUPS[setup]["players"]
    for lineup, opponents in pb.LINEUPS.items():
        tests = []
        for k in range(n):
            test, seats = pb.seating(setup, lineup, k)
            tests.append(test)
            assert seats[test - 1] is None
            assert [s for s in seats if s is not None] == list(opponents[:n - 1])
        assert sorted(tests) == list(range(1, n + 1))


def test_beats_ranks_a_pair_as_the_board_does():
    def row(won, turns, lost=0):
        return {"won": won, "turns": turns, "lost": lost}
    assert pb.standing(row(1, 90)) > pb.standing(row(1, 100)) > pb.standing(row(0, 50))
    assert pb.standing(row(1, 90, 5)) > pb.standing(row(1, 90, 6))
    assert pb.standing(row(0, 50)) == pb.standing(row(0, 600))


def test_a_ledger_refuses_rows_from_another_tree(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text(json.dumps({"rev": "aaaa"}) + "\n")
    assert pb.load_rows(path, "aaaa") == [{"rev": "aaaa"}]
    with pytest.raises(SystemExit):
        pb.load_rows(path, "bbbb")
    assert pb.load_rows(path, None) == [{"rev": "aaaa"}]


# --------------------------------------------------------------------------- #
# The arms
# --------------------------------------------------------------------------- #
def test_stock_plays_exactly_as_knower_ships(knower, monkeypatch):
    """The wrappers pass straight through for a seat under no arm."""
    monkeypatch.setattr(pb, "MAX_TURNS", 30)
    boards: list[str] = []
    real = replay.reseed

    def reseed(state):
        boards.append(hashlib.sha1(repr((
            sorted((s.id, s.owner_id, s.ships) for s in state.systems.values()),
            sorted((f.owner_id, f.source_id, f.dest_id, f.ships) for f in state.fleets),
        )).encode()).hexdigest())
        return real(state)
    monkeypatch.setattr(replay, "reseed", reseed)

    enemy_arrivals, required = knower._enemy_arrivals, knower._required
    wrapped = pb.play_one("p4", "sole", 1, "stock", 0)
    played = list(boards)
    assert knower._enemy_arrivals is not enemy_arrivals
    boards.clear()
    knower._enemy_arrivals, knower._required = enemy_arrivals, required
    pb._patched = knower                # so `play_one` leaves them unwrapped
    plain = pb.play_one("p4", "sole", 1, "stock", 0)
    assert boards == played and (plain["winner"], plain["turns"]) == (wrapped["winner"], wrapped["turns"])


def test_defend_folds_two_rivals_landing_together_and_nothing_else(knower):
    state, ours, _ = _board(knower)
    _land(state, 2, ours.id, 10, 2)
    _land(state, 3, ours.id, 6, 2)
    _land(state, 2, ours.id, 4, 3)
    pb._defend = frozenset()
    assert knower._enemy_arrivals(state, 1, ours.id) == [(2, 16), (3, 20)]
    pb._defend = frozenset({1})
    folded = knower._fold([10, 6])
    assert 0 < folded < 10
    assert knower._enemy_arrivals(state, 1, ours.id) == [(2, folded), (3, folded + 4)]
    assert pb._fires["defend"] == [1, 1]

    state.fleets.pop(1)                 # one rival a turn: nothing to fold
    assert knower._enemy_arrivals(state, 1, ours.id) == [(2, 10), (3, 14)]
    assert pb._fires["defend"] == [2, 1]


def test_attack_pays_for_a_third_seat_landing_with_us_and_never_less(knower):
    state, ours, theirs = _board(knower)
    pos = knower._posture()
    dist = state.travel_turns(ours.id, theirs.id)
    stock = knower._required(state, None, 1, theirs, dist, pos)
    pb._attack = frozenset({1})
    assert knower._required(state, None, 1, theirs, dist, pos) == stock

    _land(state, 3, theirs.id, 5, dist + 1)    # lands a turn after us
    _land(state, 2, theirs.id, 5, dist)        # the target's own reinforcement
    pb._attack = frozenset()
    base = knower._required(state, None, 1, theirs, dist, pos)
    pb._attack = frozenset({1})
    assert knower._required(state, None, 1, theirs, dist, pos) == base

    _land(state, 3, theirs.id, 6, dist)        # lands with us
    pb._attack = frozenset()
    assert knower._required(state, None, 1, theirs, dist, pos) == base
    pb._attack = frozenset({1})
    need = knower._required(state, None, 1, theirs, dist, pos)
    assert need > max(base, 6)
    assert pb._fires["attack"][1] == 1


def test_a_game_records_every_seat_out_and_the_seat_s_place(knower, monkeypatch):
    monkeypatch.setattr(pb, "MAX_TURNS", 60)
    row = pb.play_one("p4", "sole", 1, "attack", 1)
    assert row["test"] == 2 and 1 <= row["place"] <= 4
    assert row["won"] == (row["place"] == 1 and row["winner"] == 2)
    assert all(0 < t <= row["turns"] for t in row["out"].values())
    assert row["lost"] >= 0 and row["fires"][0] > 0
