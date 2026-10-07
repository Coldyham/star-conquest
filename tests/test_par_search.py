"""The par search's two promises: the floor never exceeds a real win, and a lucky
fight is never worse for the searcher than a real roll."""

from __future__ import annotations

import math
import random

from starconquest import ai, combat, config, engine
from starconquest.settings import Settings, build_state
from tests.test_replay import _preserve_config
from tools import par_search


def test_floor_never_exceeds_the_real_win():
    ai.load_models()
    with _preserve_config():
        for seed in (1, 2, 3, 4):
            state = build_state(Settings(players=3, nodes=12, seed=seed), seed)
            for p in state.players.values():
                p.is_human = False
            floors: dict[int, list[float]] = {pid: [] for pid in state.players if pid}
            while state.winner is None and state.turn < 300:
                for pid in floors:
                    if state.players[pid].alive:
                        floors[pid].append(par_search.floor(state, pid))
                engine.end_turn(state, decide=ai.decide)
            if state.winner:
                assert max(floors[state.winner]) <= state.turn


def test_lucky_is_never_worse_than_a_real_roll():
    rng = random.Random(0)
    with _preserve_config():
        config.COMBAT_JITTER = 0.1
        for _ in range(300):
            a, b = rng.randint(1, 40), rng.randint(1, 40)
            with par_search.lucky(1):
                lucky_won, lucky_left = combat.resolve_fight(rng, 1, a, 2, b, defender_owner=2)
            real_won, real_left = combat.resolve_fight(rng, 1, a, 2, b, defender_owner=2)
            if real_won == 1:
                assert lucky_won == 1 and lucky_left >= real_left
            if lucky_won != 1:
                assert real_won != 1


def test_best_line_replays():
    ai.load_models()
    ai.set_budget_scale(math.inf)
    try:
        with _preserve_config():
            search = par_search.Search(Settings(players=2, nodes=8, seed=3), 3,
                                       width=2, budget=30)
            best = search.run()
            assert best.turn < math.inf
            assert par_search.floor(search.root, search.me) <= best.turn
            assert par_search.check_line(search.log(), search.me) == (True, best.turn, best.lost)
    finally:
        ai.set_budget_scale(1.0)


def test_the_beam_stops_at_max_turns():
    ai.load_models()
    with _preserve_config():
        search = par_search.Search(Settings(players=3, nodes=12, seed=1), 1,
                                   width=2, budget=30, max_turns=5)
        search.run()
        assert search.stats.layers <= 5


def test_a_step_rolls_the_turn_the_game_would_roll():
    """`step` seeds the turn as the game does (`replay.reseed`), so whatever rng a
    board carries in, the turn comes out the same."""
    ai.load_models()
    with _preserve_config():
        root = build_state(Settings(players=3, nodes=12, seed=2), 2)
        records = []
        for salt in (1, 2):
            board = par_search.clone(root, random.Random(salt))
            records.append([par_search.step(board, []) for _ in range(40)])
        assert records[0] == records[1]
        assert any(r.dice for r in records[0])


def test_an_honest_line_replays():
    ai.load_models()
    ai.set_budget_scale(math.inf)
    try:
        with _preserve_config():
            search = par_search.Search(Settings(players=2, nodes=8, seed=3), 3,
                                       dice="honest", width=2, budget=30)
            best = search.run()
            assert best.turn < math.inf
            assert par_search.check_line(search.log(), search.me) == (True, best.turn, best.lost)
    finally:
        ai.set_budget_scale(1.0)
