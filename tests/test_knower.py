"""The oracle bot: `models/knower.py`.

Pure/headless (no pygame). knower is the one strategy that reads *other* seats —
it clones the board and runs their registered `decide` to learn their orders before
the engine asks for them — so these tests guard the two things that makes safe:
it must leave `state.rng` exactly where the seats after it expect to find it, and
it must survive any rival that raises, mutates its board, or is a knower itself.

Internals are reached through `sys.modules["sc_model_knower"]`, the synthetic name
`ai.load_models` imports each model file under (ai.py:109).
"""

from __future__ import annotations

import copy
import sys

import pytest

from starconquest import ai, engine, mapgen
from starconquest.model import GameState, Order, Player, System
from tests import sim


@pytest.fixture(scope="module")
def kn():
    """The knower module itself, so the tests can poke at its internals."""
    assert "knower" in ai.load_models(), "models/knower.py failed to import"
    return sys.modules["sc_model_knower"]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Drop any strategy a test registered, so tests can't leak into each other."""
    before = dict(ai.STRATEGIES)
    yield
    ai.STRATEGIES.clear()
    ai.STRATEGIES.update(before)


def _state(seed=3, nodes=24, players=3, knower_seat=2):
    """A generated board with one knower seat and thinker everywhere else."""
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False                    # headless: the engine drives every seat
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[knower_seat].ai_strategy = "knower"
    return state


def _board(systems, lanes, seats=3, knower_seat=2):
    """A tiny hand-built board. ``systems`` is {sid: (owner, ships, production)}.

    ``knower_seat`` is stamped with the strategy, so `ai.decide` actually routes
    there rather than falling through to the default heuristic.
    """
    state = GameState.new(1)
    for sid, (owner, ships, production) in systems.items():
        state.systems[sid] = System(id=sid, pos=(float(sid) * 10.0, 0.0),
                                    owner_id=owner, ships=ships, production=production)
    for a, b, turns in lanes:
        state.add_lane(a, b, turns * 6.0, turns)
    state.rebuild_topology()
    state.players = {0: Player(0, "Neutral", (90, 90, 90), is_neutral=True)}
    for pid in range(1, seats + 1):
        state.players[pid] = Player(pid, f"P{pid}", (200, 60, 60))
    state.players[knower_seat].ai_strategy = "knower"
    return state


def _totals(orders):
    """Ships committed per destination, so a plan can be compared to another."""
    out: dict[int, int] = {}
    for o in orders:
        out[o.dest_id] = out.get(o.dest_id, 0) + o.ships
    return out


# --------------------------------------------------------------------------- #
# The invariants that make the oracle sound
# --------------------------------------------------------------------------- #
def test_knower_draws_nothing_from_state_rng(kn):
    """The load-bearing one: drawing would shift the stream for every later seat.

    Prediction is only bit-exact because the seats after us find `state.rng` where
    they expect it. A single stray draw here silently invalidates that.
    """
    state = _state()
    before = state.rng.getstate()
    ai.decide(state, 2)
    assert state.rng.getstate() == before


def test_the_oracle_is_actually_built(kn):
    """`decide` has to swallow everything, so a bug in it looks like a weak bot.

    Without this, a typo inside `_build_oracle` silently downgrades knower to blind
    thinker and every benchmark still "passes", just worse. (It happened.)
    """
    state = _state()
    kn.LAST_ERROR = None
    ai.decide(state, 2)
    assert kn.LAST_ERROR is None, f"knower fell back to blind play: {kn.LAST_ERROR!r}"
    assert kn.LAST_ORACLE is not None
    assert kn.LAST_ORACLE.trusted >= {0, 1, 3}, "no seat was successfully predicted"


def test_knower_does_not_mutate_state(kn):
    """The models/README.md:22 contract — and knower hands the state to rivals."""
    state = _state()
    original = copy.deepcopy(state)
    ai.decide(state, 2)
    assert kn._fingerprint(state) == kn._fingerprint(original)
    assert state.turn == original.turn and state.winner == original.winner


def test_prediction_of_a_later_seat_is_exact(kn):
    """A seat that decides after us is predicted order-for-order, every turn."""
    state = _state()
    real: dict[int, list[Order]] = {}

    def spy(st, pid):
        orders = ai.STRATEGIES[st.players[pid].ai_strategy](st, pid)
        real[pid] = orders
        return orders

    checked = 0
    for _ in range(30):
        if state.winner is not None:
            break
        real.clear()
        ai.decide(state, 2)                      # builds the oracle
        predicted = kn.LAST_ORACLE.orders.get(3)
        engine.end_turn(state, decide=spy)
        if 3 in real and state.players[3].alive:
            assert predicted == real[3], f"seat 3 mispredicted on turn {state.turn}"
            checked += 1
    assert checked > 10, "the test never actually exercised a prediction"


def test_earlier_seat_prediction_is_close_but_not_guaranteed(kn):
    """A seat that already drew is predicted from an unrecoverable rng position.

    Its orders are still right except where it hit a genuine tie, so this asserts
    the useful property — the prediction exists and is usually right — rather than
    exactness we cannot honestly claim.
    """
    state = _state(knower_seat=3)                 # seat 1 and 2 decide before us
    ai.decide(state, 3)
    assert kn.LAST_ORACLE.seats[2] == "likely"
    assert 2 in kn.LAST_ORACLE.trusted


def test_predicting_a_human_seat_does_not_break_the_chain(kn):
    """The engine skips human seats, so predicting one must not advance the stream."""
    state = _state(knower_seat=2, players=4)
    state.players[3].is_human = True
    ai.decide(state, 2)
    orc = kn.LAST_ORACLE
    assert orc.seats[3] == "modelled" and 3 not in orc.trusted
    assert orc.seats[4] == "exact" and 4 in orc.trusted   # chain survived seat 3


# --------------------------------------------------------------------------- #
# Recursion and hostile rivals
# --------------------------------------------------------------------------- #
def test_two_knower_seats_terminate(kn):
    """Each models the other with the blind planner, so the recursion bottoms out."""
    result = sim.play(5, "random", 24, 2, max_turns=400,
                      strategies=["knower", "knower"])
    assert result.winner is not None or result.timed_out
    assert kn._DEPTH == 0, "the depth guard leaked"


def test_sibling_oracle_is_proxied_not_recursed(kn):
    """A different module flagged IS_ORACLE is modelled blind, not called."""
    calls = []

    def other(state, pid):
        calls.append(pid)
        return []

    other.__module__ = "sc_model_fake_oracle"
    fake = type(sys)("sc_model_fake_oracle")
    fake.IS_ORACLE = True
    sys.modules["sc_model_fake_oracle"] = fake
    try:
        ai.register("fake_oracle", other)
        state = _state()
        state.players[3].ai_strategy = "fake_oracle"
        ai.decide(state, 2)
        assert calls == [], "the sibling oracle was called instead of proxied"
        assert 3 not in kn.LAST_ORACLE.trusted
    finally:
        del sys.modules["sc_model_fake_oracle"]


def test_raising_opponent_does_not_crash(kn):
    """Nothing upstream catches a bot (engine.py:101), so knower must absorb it."""
    def boom(state, pid):
        raise RuntimeError("this bot is broken")

    ai.register("boom_test", boom)
    state = _state()
    state.players[3].ai_strategy = "boom_test"
    orders = ai.decide(state, 2)                  # must not raise
    assert isinstance(orders, list)
    assert kn.LAST_ORACLE.seats[3] == "raised"
    assert 3 not in kn.LAST_ORACLE.trusted        # believe nothing it implies


def test_mutating_opponent_cannot_corrupt_the_real_state(kn):
    """A rival that ignores the read-only contract only ever wrecks its own clone."""
    def vandal(state, pid):
        for s in state.systems.values():
            s.owner_id, s.ships = pid, 999
        state.fleets.clear()
        return []

    ai.register("vandal_test", vandal)
    state = _state()
    state.players[3].ai_strategy = "vandal_test"
    original = copy.deepcopy(state)
    ai.decide(state, 2)
    assert kn._fingerprint(state) == kn._fingerprint(original)
    assert kn.LAST_ORACLE.seats[3] == "mutated"
    assert 3 not in kn.LAST_ORACLE.trusted


def test_spoofed_order_cannot_drain_our_systems(kn):
    """A rival naming us as owner must not move our ships on the planning board.

    The engine refuses such an order too (`_own_orders`), so this is defence in
    depth — but it is also *fidelity*: honouring it here would have knower plan
    around a fleet that never launches.
    """
    state = _state()
    ours = next(sid for sid, s in state.systems.items() if s.owner_id == 2 and s.ships > 1)
    target = state.systems[ours].neighbors[0]

    def thief(st, pid):
        return [Order(2, ours, target, st.systems[ours].ships)]

    ai.register("thief_test", thief)
    state.players[3].ai_strategy = "thief_test"
    ai.decide(state, 2)
    assert kn.LAST_ORACLE.post.systems[ours].ships == state.systems[ours].ships


# --------------------------------------------------------------------------- #
# Determinism (a seed must still reproduce a whole match — CLAUDE.md)
# --------------------------------------------------------------------------- #
def test_decide_is_deterministic_given_the_state(kn):
    """No clock, no `id()`, no PYTHONHASHSEED — every private rng is state-derived."""
    state = _state()
    first = ai.decide(copy.deepcopy(state), 2)
    second = ai.decide(copy.deepcopy(state), 2)
    assert first == second


def test_game_is_reproducible_from_its_seed(kn):
    a = sim.play(11, "random", 24, 3, max_turns=400,
                 strategies=["knower", "thinker", "claudebot"])
    b = sim.play(11, "random", 24, 3, max_turns=400,
                 strategies=["knower", "thinker", "claudebot"])
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_orders_are_legal(kn):
    """Same contract every model owes: own the source, reach the dest, spend once."""
    state = _state()
    for _ in range(20):
        if state.winner is not None:
            break
        spent: dict[int, int] = {}
        for order in ai.decide(state, 2):
            assert order.owner_id == 2
            assert state.systems[order.source_id].owner_id == 2
            assert state.travel_turns(order.source_id, order.dest_id) is not None
            assert order.ships >= 1
            spent[order.source_id] = spent.get(order.source_id, 0) + order.ships
        for sid, ships in spent.items():
            assert ships <= state.systems[sid].ships, f"system {sid} over-committed"
        engine.end_turn(state, decide=ai.decide)


# --------------------------------------------------------------------------- #
# What the oracle actually buys — each pins one claim from the docstring
# --------------------------------------------------------------------------- #
def test_guard_is_freed_when_no_attack_is_coming(kn):
    """thinker pins a hedge against an idle neighbour; knower knows it is idle.

    Sized so the hedge is exactly what makes the strike unaffordable: our 20 ships
    can pay knower's known-horizon price for system 2 but not once thinker's
    30%-of-14 guard is deducted.
    """
    ai.register("idle_test", lambda state, pid: [])
    state = _board({1: (2, 20, 3), 2: (3, 14, 3)}, [(1, 2, 2)])
    state.players[3].ai_strategy = "idle_test"

    knower_orders = ai.decide(state, 2)
    thinker_orders = ai.STRATEGIES["thinker"](state, 2)

    assert _totals(knower_orders).get(2, 0) > 0, "knower should strike an idle neighbour"
    assert _totals(thinker_orders).get(2, 0) == 0, "thinker's blind hedge blocks it"


def test_vacated_source_is_sniped(kn):
    """System 2 sends its army to 3, so it is defenceless — for this turn only."""
    ai.register("allin_test", lambda state, pid: [Order(3, 2, 3, 19)])
    state = _board({1: (2, 12, 3), 2: (3, 20, 3), 3: (3, 1, 3)},
                   [(1, 2, 2), (2, 3, 2)])
    for pid in (3,):
        state.players[pid].ai_strategy = "allin_test"

    assert _totals(ai.decide(state, 2)).get(2, 0) > 0, "the empty system was not taken"
    assert _totals(ai.STRATEGIES["thinker"](state, 2)).get(2, 0) == 0


def test_a_human_seats_vacated_system_is_not_sniped(kn):
    """Defence-only: we never assume a human emptied anything (TRUST_HUMAN=False)."""
    ai.register("allin_test", lambda state, pid: [Order(3, 2, 3, 19)])
    systems, lanes = ({1: (2, 12, 3), 2: (3, 20, 3), 3: (3, 1, 3)},
                      [(1, 2, 2), (2, 3, 2)])

    bot = _board(systems, lanes)
    bot.players[3].ai_strategy = "allin_test"
    assert _totals(ai.decide(bot, 2)).get(2, 0) > 0        # a bot: snipe it

    human = _board(systems, lanes)
    human.players[3].ai_strategy = "allin_test"
    human.players[3].is_human = True
    assert _totals(ai.decide(human, 2)).get(2, 0) == 0     # a person: don't


def test_a_human_seats_attack_still_raises_the_guard(kn):
    """The other half of defence-only: their predicted strike is believed."""
    ai.register("strike_test", lambda state, pid: [Order(3, 2, 1, 18)])
    systems, lanes = ({1: (2, 10, 3), 2: (3, 18, 3), 3: (2, 8, 3)},
                      [(1, 2, 2), (1, 3, 2)])

    quiet = _board(systems, lanes)
    ai.register("idle_test", lambda state, pid: [])
    quiet.players[3].ai_strategy = "idle_test"

    attacked = _board(systems, lanes)
    attacked.players[3].ai_strategy = "strike_test"
    attacked.players[3].is_human = True

    # System 3 is our rear system; under attack on 1 it should send help there.
    assert _totals(ai.decide(attacked, 2)).get(1, 0) >= _totals(ai.decide(quiet, 2)).get(1, 0)


def test_knower_works_as_the_autoplayed_human_seat(kn):
    """`main.resolve_turn` drives the human seat via `ai.decide` before `end_turn`.

    That is the one path where knower runs for a seat the engine will *skip*
    (main.py:298-304), and it runs before every AI seat rather than in pid order.
    Since knower draws nothing from `state.rng`, the later seats are still exactly
    where it predicted them.
    """
    state = _state(knower_seat=1)                 # seat 1 is the human seat
    state.players[1].is_human = True

    for _ in range(15):
        if state.winner is not None:
            break
        kn.LAST_ERROR = None
        human_orders = ai.decide(state, 1)        # exactly what main.py does
        assert kn.LAST_ERROR is None, kn.LAST_ERROR
        assert all(o.owner_id == 1 for o in human_orders)
        engine.end_turn(state, human_orders=human_orders, decide=ai.decide)
    assert state.turn > 0


def test_knower_beats_thinker_head_to_head(kn):
    """The whole point. Both seatings, so start-position luck cancels."""
    roster = ["knower", "thinker"]
    games = sim.run_ladder(range(1, 13), "random", 24, roster, 500)
    wins, _draws, _timeouts = sim._tally(games, roster)
    assert wins["knower"] > wins["thinker"], wins
