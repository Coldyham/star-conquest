"""The par search's two promises: the floor never exceeds a real win, and a lucky
fight is never worse for the searcher than a real roll."""

from __future__ import annotations

import math
import random

import pytest

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
                for pid, floor in floors.items():
                    if state.players[pid].alive:
                        floor.append(par_search.floor(state, pid))
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


def test_a_child_keeps_its_best_rollout(monkeypatch):
    """Each rollout bot plays the child on, and the child ranks by the best of them."""
    ai.load_models()
    with _preserve_config():
        search = par_search.Search(Settings(players=2, nodes=8, seed=3), 3,
                                   policies=("marshal", "actuary"))
        child = par_search.Node(par_search.clone(search.root))
        ends = {"marshal": (None, 0), "actuary": (search.me, 30)}
        played = []

        def rollout(board, policy, cutoff=True):
            played.append(policy)
            end = par_search.clone(board)
            end.winner, end.turn = ends[policy]
            return end, []

        monkeypatch.setattr(search, "rollout", rollout)
        assert search.score(child)[:2] == (0, 30)
        assert played == ["marshal", "actuary"]


def test_an_unknown_rollout_bot_is_refused():
    ai.load_models()
    with _preserve_config():
        search = par_search.Search(Settings(players=2, nodes=8, seed=3), 3,
                                   policies=("marshal", "no-such-bot"), budget=1)
        with pytest.raises(ValueError, match="no-such-bot"):
            search.run()


def test_lane_sends_offer_all_in_or_every_count():
    with _preserve_config():
        state = build_state(Settings(players=2, nodes=8, seed=3), 3)
        human = state.human()
        assert human is not None
        home = next(s for s in state.systems.values() if s.owner_id == human.id)
        sends = {(o.dest_id, o.ships) for o in par_search._lane_sends(state, human.id, False)}
        assert sends == {(nbr, home.ships) for nbr in home.neighbors}
        every = {(o.dest_id, o.ships) for o in par_search._lane_sends(state, human.id, True)}
        assert every == {(nbr, n) for nbr in home.neighbors for n in range(1, home.ships + 1)}
