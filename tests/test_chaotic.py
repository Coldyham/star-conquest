"""Chaotic: `models/chaotic.py`, a different pool bot each turn."""

from __future__ import annotations

import copy
import sys

import pytest

from starconquest import ai, mapgen
from tests import sim


@pytest.fixture(scope="module")
def ch():
    names = ai.load_models()
    assert "chaotic" in names and "knower" in names
    return sys.modules["sc_model_chaotic"]


def test_every_pool_entry_plays_and_predicts_nothing(ch):
    for entry in ch.POOL:
        name, _, stop = entry.partition(":")
        fn = ai.STRATEGIES.get(name)
        assert fn is not None and fn is not ch.decide, entry
        player = copy.copy(mapgen.generate(1, "random", 12, 2).players[1])
        player.ai_params = copy.copy(player.ai_params)
        if stop:
            player.ai_params.aux = float(stop)
        assert not sys.modules[fn.__module__].__dict__.get("IS_ORACLE", False), entry
        probe = getattr(sys.modules[fn.__module__], "is_oracle_seat", None)
        assert probe is None or not probe(player), entry


def test_the_pick_is_derived_and_changes_turn_to_turn(ch):
    picks = [ch.pick(42, turn, 1) for turn in range(40)]
    assert picks == [ch.pick(42, turn, 1) for turn in range(40)]
    assert len(set(picks)) > 3
    assert picks != [ch.pick(42, turn, 2) for turn in range(40)]


def test_the_pick_draws_nothing_and_the_seat_is_put_back(ch, monkeypatch):
    state = mapgen.generate(7, "random", 12, 2)
    player = state.players[1]
    held = player.ai_strategy, player.ai_params
    seen = []

    def spy(st, pid):
        seen.append((st.players[pid].ai_strategy, st.players[pid].ai_params.aux))
        return []

    name, aux = ch.pick(state.seed, state.turn, 1)
    monkeypatch.setitem(ai.STRATEGIES, name, spy)
    before = state.rng.getstate()
    assert ch.decide(state, 1) == []
    assert state.rng.getstate() == before
    assert seen[0][0] == name and (aux is None or seen[0][1] == aux)
    assert (player.ai_strategy, player.ai_params) == held


def test_knower_runs_a_chaotic_seat_as_itself(ch):
    kn = sys.modules["sc_model_knower"]
    player = mapgen.generate(3, "random", 12, 2).players[2]
    player.is_human, player.ai_strategy = False, "chaotic"
    fn, params, trusted = kn._surrogate(player)
    assert fn is ch.decide and params is None and trusted


def test_a_whole_game_against_knower(ch):
    result = sim.play(5, nodes=12, players=2, max_turns=300,
                      strategies=["chaotic", "knower"])
    assert result.turns > 0
