"""Whole-game checks driven through the headless AI-vs-AI harness."""

from __future__ import annotations

import math
import time
from collections import Counter

from starconquest import ai, settings as settings_mod
from starconquest.model import AiParams
from starconquest.settings import Settings
from tests import sim
from tools.bot_replay import REPLAY_AUX


def test_invariants_hold_and_most_games_terminate():
    results = [sim.play(seed, nodes=18, players=3, max_turns=600) for seed in range(30)]
    # sim.play calls check_invariants every turn, so reaching here means no
    # negative garrisons, phantom fleets, or unknown owners ever appeared.
    finished = [r for r in results if not r.timed_out]
    assert len(finished) / len(results) >= 0.6  # the vast majority should resolve
    for r in finished:
        assert r.winner in (0, 1, 2, 3)


def test_the_film_rebuilds_every_turns_board():
    """The playback oracle at volume: `sim.play(film=True)` asserts, every turn of
    every game, that applying that turn's events to the board it started from
    lands exactly on the board it ended on."""
    results = [sim.play(seed, nodes=16, players=3, max_turns=300, film=True)
               for seed in range(8)]
    assert results and all(r.turns > 0 for r in results)


def test_deterministic_from_seed():
    a = sim.play(5, nodes=18, players=3, max_turns=600)
    b = sim.play(5, nodes=18, players=3, max_turns=600)
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_two_player_game_resolves():
    r = sim.play(3, nodes=12, players=2, max_turns=600)
    assert not r.timed_out and r.winner in (1, 2)


def test_ladder_plays_every_pair_in_both_seatings():
    roster = ["heuristic", "heuristic", "heuristic"]   # 3 pairs, seating-agnostic
    games = sim.run_ladder(range(2), "random", 12, roster, max_turns=300)
    assert len(games) == 3 * 2 * 2            # pairs x seatings x seeds
    for g in games:
        assert len(g.assignment) == 2         # head-to-head, never a melee


def test_ladder_credits_the_winning_strategy():
    """A bot that never issues an order must lose, whichever seat it holds — which
    is exactly what the assignment-aware tally is for."""
    roster = ["heuristic", "_test_passive"]
    ai.register("_test_passive", lambda state, pid: [])
    try:
        games = sim.run_ladder(range(3), "random", 12, roster, max_turns=400)
    finally:
        ai.STRATEGIES.pop("_test_passive", None)
    wins, draws, timeouts = sim._tally(games, roster)
    assert sum(wins.values()) + draws + timeouts == len(games)
    assert wins["heuristic"] > wins["_test_passive"]


def test_avg_turns_excludes_timeouts():
    finished = sim.SwapGame(sim.SimResult(0, 1, 50, False), ["a", "b"])
    timed_out = sim.SwapGame(sim.SimResult(1, None, 600, True), ["a", "b"])
    assert sim._avg_turns([finished, timed_out]) == 50.0


def test_bot_timeout_caps_a_slow_bot_and_survives():
    """A decide() that overruns its budget scores as no orders, not a crash —
    the whole point is capping an oracle-style bot's compute, not aborting it."""

    def _slow(state, pid):
        time.sleep(0.05)
        return []

    ai.register("_test_slow", _slow)
    try:
        r = sim.play(1, nodes=12, players=2, max_turns=200, strategies=["heuristic", "_test_slow"], bot_timeout=0.01)
    finally:
        ai.STRATEGIES.pop("_test_slow", None)
    assert r.bot_timeouts >= 1


def test_bot_timeout_is_zero_when_disabled_or_generous():
    default = sim.play(1, nodes=12, players=2, max_turns=200)
    generous = sim.play(1, nodes=12, players=2, max_turns=200, bot_timeout=1.0)
    assert default.bot_timeouts == 0
    assert generous.bot_timeouts == 0


def test_no_seat_is_systematically_doomed():
    """Peripheral starts should give every seat a real chance across many seeds."""
    wins = Counter(
        r.winner
        for r in (sim.play(s, nodes=18, players=3, max_turns=600) for s in range(120))
        if not r.timed_out
    )
    for pid in (1, 2, 3):
        assert wins[pid] >= 15  # no seat wins almost never


# --------------------------------------------------------------------------- #
# play_settings: the leaderboard's bot-replay column (tools/bot_replay.py)
# --------------------------------------------------------------------------- #
def _setup(**kw) -> Settings:
    base = dict(mode="random", players=3, nodes=16, seed=11)
    return Settings(**{**base, **kw})


def test_replay_is_reproducible_from_the_setup_alone():
    # The whole premise of caching a bot result: same setup, same seed, same
    # answer, every time and on any machine.
    cfg = _setup()
    a = sim.play_settings(cfg, 11, "heuristic")
    b = sim.play_settings(cfg, 11, "heuristic")
    assert a == b


def test_replay_lands_on_the_same_map_the_human_played():
    # mapgen is a pure function of the seed, so the board a bot inherits is
    # identical down to the star names — only the war fought on it differs.
    cfg = _setup()
    one = settings_mod.build_state(cfg, 11)
    two = settings_mod.build_state(cfg, 11)
    assert [(s.id, s.name, s.owner_id, s.ships) for s in one.systems.values()] == \
           [(s.id, s.name, s.owner_id, s.ships) for s in two.systems.values()]


def test_the_bot_actually_drives_the_human_seat():
    # `engine._collect_orders` skips the human seat, so the harness has to drive
    # it from outside (`sim._step_seat`); a replay that forgot to would sit still
    # and lose every time. Seat 1 must be taking ground, which is only possible
    # if `decide` ran for it.
    cfg = _setup()
    result = sim.play_settings(cfg, 11, "heuristic", max_turns=40)
    assert result.turns > 0
    assert result.lost > 0 or result.won  # it fought, or it had already won


def test_opponents_keep_the_strategies_the_setup_gave_them():
    # The bot must face the same opposition the human did — that is what makes
    # the comparison mean anything.
    ai.load_models()
    cfg = _setup()
    cfg.ai_strategy = ["heuristic", "rusherplus", "rusherplus"] + ["heuristic"] * 3
    state = settings_mod.build_state(cfg, 11)
    assert [state.players[pid].ai_strategy for pid in (2, 3)] == ["rusherplus", "rusherplus"]


def test_the_replayed_seat_ignores_the_setup_s_own_ai_params():
    # Slot 0 belongs to the human, so whatever a menu left in it says nothing
    # about how a bot should play — and letting it through would score the same
    # bot differently on two identical maps.
    tuned = _setup()
    tuned.ai[0] = AiParams(reserve_fraction=0.9, reserve_floor=40, expand_margin=9.0,
                           attack_margin=9.0, reinforce_margin=40, aux=7.0)
    assert sim.play_settings(tuned, 11, "heuristic") == sim.play_settings(_setup(), 11, "heuristic")


def _watch_link_run(cfg: Settings, seed: int, bot: str, aux=None):
    """What the leaderboard's Watch link actually plays, in this process.

    `leaderboard/js/token-encode.mjs`'s `botWatchSetup` writes the bot into seat
    1's strategy, gives that seat default `AiParams` (bar `aux`) and turns
    `autoplay` on; the app then drives the human seat from outside, exactly as
    `main.resolve_turn` does. Nothing in a token can flag a seat as a bot, so
    this is the *only* shape that link can produce — which is why it, and not
    the harness, is the fixed point the column has to meet.
    """
    from dataclasses import replace as _replace

    from starconquest import engine

    watched = Settings.from_dict(cfg.to_dict())
    strategies = list(watched.ai_strategy)
    strategies[0] = bot
    watched.ai_strategy = strategies
    params = [_replace(p) for p in watched.ai]
    params[0] = AiParams() if aux is None else AiParams(aux=aux)
    watched.ai, watched.autoplay = params, True

    state = settings_mod.build_state(watched, seed)
    seat = state.human()
    while state.winner is None and state.turn < 600:
        engine.end_turn(state, human_orders=ai.decide(state, seat.id), decide=ai.decide)
    return sim.ReplayResult(bot=bot, won=state.winner == seat.id, turns=state.turn,
                            lost=seat.ships_lost, timed_out=state.winner is None)


def test_the_column_plays_the_game_its_watch_link_replays():
    """A bot's row and the Watch link beside it must be the same run.

    They diverged, and the cause was a seat flag: the harness used to clear
    `is_human` on the seat it took over (the shortest way to make the engine
    decide it), which an oracle opponent reads as "that seat is a bot I can
    resolve through `ai.STRATEGIES` and simulate exactly" rather than "a person
    I have to guess at" (`models/knower.py`). So the cached row was a game whose
    opponents knew which bot was standing in, and the link — which can only ever
    turn `autoplay` on — played one whose opponents did not. Measured on a map
    with knower opponents, that was worth flipping marshal from a loss to a win.

    The opponent here is that difference in one line rather than a real oracle,
    which would cost a minute of search to say the same thing.
    """
    def peeker(state, pid):
        faces_human = any(p.is_human for p in state.players.values()
                          if p.id != pid and not p.is_neutral)
        return ai.compute_orders(state, pid) if faces_human else []

    ai.register("_peeker", peeker)
    try:
        cfg = _setup()
        cfg.ai_strategy = ["heuristic", "_peeker", "_peeker"] + ["heuristic"] * 3
        assert sim.play_settings(cfg, 11, "heuristic") == \
               _watch_link_run(cfg, 11, "heuristic")
    finally:
        ai.STRATEGIES.pop("_peeker", None)


def test_the_replayed_seat_is_still_the_human_seat():
    """The flag itself, pinned: `play_settings` hands a seat over without
    pretending a person has left the chair. Everything above rests on it, and it
    is a one-character regression to make."""
    cfg = _setup()
    seen: list[bool] = []

    def watcher(state, pid):
        seen.append(state.players[1].is_human)
        return []

    ai.register("_watcher", watcher)
    try:
        cfg.ai_strategy = ["heuristic", "_watcher", "heuristic"] + ["heuristic"] * 3
        sim.play_settings(cfg, 11, "heuristic", max_turns=3)
    finally:
        ai.STRATEGIES.pop("_watcher", None)
    assert seen and all(seen)


def test_a_bot_that_never_wins_still_reports_a_result():
    # `won` is the discriminator, not `turns`: a replay cut off by the turn cap
    # is a real answer ("no win"), not a missing one.
    result = sim.play_settings(_setup(), 11, "heuristic", max_turns=2)
    assert result.timed_out and not result.won
    assert result.turns == 2


def test_balance_knobs_from_the_setup_reach_the_generated_map():
    # build_state is the only funnel that pushes a Settings' knobs into config,
    # which is why play_settings goes through it instead of mapgen.generate.
    strong = _setup(home_start_ships=99)
    weak = _setup(home_start_ships=5)
    a = settings_mod.build_state(strong, 11)
    b = settings_mod.build_state(weak, 11)
    home_a = max(s.ships for s in a.systems.values() if s.owner_id == 1)
    home_b = max(s.ships for s in b.systems.values() if s.owner_id == 1)
    assert home_a == 99 and home_b == 5


def test_aux_reaches_the_replayed_seat_and_changes_how_it_plays():
    # models/knower.py reads aux as search depth, and the leaderboard replays it
    # at 12 (bot_replay.REPLAY_AUX) because that is its own slider's top end and
    # the setting its measurements favour. If aux stopped reaching the seat the
    # board would quietly be showing the depth-1 bot instead, which is a
    # materially weaker player — so assert the two actually differ.
    ai.load_models()
    cfg = _setup()
    default = sim.play_settings(cfg, 11, "knower")
    deep = sim.play_settings(cfg, 11, "knower", aux=12)
    assert (default.turns, default.lost) != (deep.turns, deep.lost)


def test_aux_none_is_the_documented_untuned_profile():
    # config.AI_AUX is 1.0 and that is what a stale token deserialises to, so
    # "no aux given" and "aux=1.0" must be the same replay.
    ai.load_models()
    cfg = _setup()
    assert sim.play_settings(cfg, 11, "knower") == sim.play_settings(cfg, 11, "knower", aux=1.0)


def test_a_tuned_replay_is_still_reproducible():
    # The whole premise of caching, so the depth is read from the worker rather
    # than chosen here: the configuration worth asserting about is the one that
    # actually gets cached. The search is iteration-bounded and plans the same way
    # twice at *any* depth — but only with its two wall-clock catastrophe guards
    # out of the way, which is the state the worker caches from
    # (`tools/bot_replay.BUDGET_SCALE`). Lifted to inf rather than the worker's
    # 100x: a guard that *cannot* fire keeps this an assertion about the search,
    # where one that merely has headroom is an assertion about how busy the
    # machine is. Headroom at scale 1 is under 2x — a decide measures ~86 ms
    # against the 150 ms guard — so a loaded runner trips it on one run of the
    # pair and not the other, which is a flake rather than a finding.
    #
    # Played out in full deliberately. `ReplayResult` compares two numbers, and a
    # capped game fixes one of them: at 20 turns the *squeezed* run below lands on
    # the same (turns, lost) as the base, so a cap buys seconds by making a
    # genuinely different plan indistinguishable.
    ai.load_models()
    cfg = _setup()
    depth = REPLAY_AUX["knower"]
    try:
        ai.set_budget_scale(math.inf)
        assert sim.play_settings(cfg, 11, "knower", aux=depth) == \
               sim.play_settings(cfg, 11, "knower", aux=depth)
    finally:
        ai.set_budget_scale(1)  # process-wide; never leave it raised for other tests


def test_budget_scale_is_wired_and_only_matters_when_it_bites():
    # knower's wall-clock guards are the one part of it that is not
    # iteration-bounded, so tripping one makes a cached replay unreproducible.
    # The worker lifts them (ai.set_budget_scale) rather than searching less deep.
    # Squeezing the scale must change the answer — that is what proves the knob
    # reaches the guard at all — while raising it must not, since a guard that
    # never fires cannot influence anything.
    #
    # Depth 12 is load-bearing here rather than borrowed from the worker: the
    # squeeze only bites from depth 4 up on this board, since below that the whole
    # search finishes inside the 3 ms guard and the knob has nothing to cut. That
    # floor rises with machine speed, so the depth-12 decide's ~69 ms is the margin
    # keeping the assertion true on a fast runner.
    ai.load_models()
    cfg = _setup()
    try:
        # Both ends of the comparison are taken with no guard able to fire: at the
        # 1.0 default one *can* (see the test above), so a `base` measured there
        # would be the loaded machine's answer rather than the search's.
        ai.set_budget_scale(math.inf)
        base = sim.play_settings(cfg, 11, "knower", aux=12)
        ai.set_budget_scale(0.02)
        assert sim.play_settings(cfg, 11, "knower", aux=12) != base, \
            "a 3ms guard changed nothing — BUDGET_SCALE is not reaching the search"
        ai.set_budget_scale(100)   # the worker's own lift: 15 s and 5 s guards
        assert sim.play_settings(cfg, 11, "knower", aux=12) == base
    finally:
        ai.set_budget_scale(1)  # process-wide; never leave it raised for other tests


def test_set_budget_scale_only_touches_bots_that_declare_one():
    # Opt-in and declarative, like aux_spec: a bot with no wall-clock guard has
    # nothing to scale and must be left alone.
    ai.load_models()
    try:
        assert ai.set_budget_scale(50) == ["knower"]
    finally:
        ai.set_budget_scale(1)


def test_budget_scale_is_one_in_an_ordinary_game():
    # Declaring the knob must not change how the bot plays for anyone else — a
    # browser, a desktop game, the rest of this suite.
    ai.load_models()
    import sys
    assert sys.modules["sc_model_knower"].BUDGET_SCALE == 1.0


def test_a_hand_authored_map_plays_a_whole_game():
    """The cheapest proof the whole custom-map funnel holds: a recipe on a
    `Settings`, through `build_state`, into a real game that reaches a result with
    `check_invariants` running every turn."""
    from starconquest import custommap, mapgen

    cfg = Settings()
    cfg.custom_map = custommap.from_state(mapgen.generate(4, "random", 16, 3))
    cfg.players, cfg.nodes = cfg.custom_map.seats(), len(cfg.custom_map.nodes)

    result = sim.play_settings(cfg, seed=4, bot="heuristic", max_turns=600)
    assert result.turns > 0
    assert result.won or result.timed_out or result.lost >= 0
