"""The positional bot: `models/marshal.py`.

Pure/headless (no pygame). marshal is a non-oracle strategy — it reads no other
seat and predicts nothing — so these tests cover the drop-in contract every model
owes (legal orders, no mutation, reproducibility) plus the three things marshal
does that its ancestors don't: it reads the combat jitter live rather than
hardcoding it, it commits its surplus into a strike already going in, and it
reserves a stagger's nearer wave for the target that is counting on it.

Internals are reached through `sys.modules["sc_model_marshal"]`, the synthetic
name `ai.load_models` imports each model file under.
"""

from __future__ import annotations

import copy
import math
import sys

import pytest

from starconquest import ai, combat, config, engine, mapgen
from starconquest.model import Fleet, GameState, Order, Player, System
from tests import sim


@pytest.fixture(scope="module")
def ma():
    """The marshal module itself, so the tests can poke at its internals."""
    assert "marshal" in ai.load_models(), "models/marshal.py failed to import"
    return sys.modules["sc_model_marshal"]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Drop any strategy a test registered, so tests can't leak into each other."""
    before = dict(ai.STRATEGIES)
    yield
    ai.STRATEGIES.clear()
    ai.STRATEGIES.update(before)


@pytest.fixture(autouse=True)
def _restore_globals(ma):
    """Several tests tune module constants; none of them may leak."""
    names = ("FRONTIER_GUARD", "COMMIT_SURPLUS", "RESERVE_PINCER")
    before = {n: getattr(ma, n) for n in names}
    jitter = config.COMBAT_JITTER
    advantage = config.DEFENDER_ADVANTAGE
    yield
    for n, v in before.items():
        setattr(ma, n, v)
    config.COMBAT_JITTER = jitter
    config.DEFENDER_ADVANTAGE = advantage


def _state(seed=3, nodes=24, players=3, seat=2):
    """A generated board with one marshal seat and thinker everywhere else."""
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False                    # headless: the engine drives every seat
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[seat].ai_strategy = "marshal"
    return state


def _board(systems, lanes, seats=3, seat=2):
    """A tiny hand-built board. ``systems`` is {sid: (owner, ships, production)}."""
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
    state.players[seat].ai_strategy = "marshal"
    return state


def _totals(orders):
    """Ships committed per destination, so a plan can be compared to another."""
    out: dict[int, int] = {}
    for o in orders:
        out[o.dest_id] = out.get(o.dest_id, 0) + o.ships
    return out


# --------------------------------------------------------------------------- #
# The drop-in contract
# --------------------------------------------------------------------------- #
def test_orders_are_legal():
    """Over live play: our ships, our systems, real lanes, never over-committed."""
    state = _state()
    for _ in range(20):
        if state.winner is not None:
            break
        for pid in (2,):
            per_source: dict[int, int] = {}
            for order in ai.decide(state, pid):
                assert order.owner_id == pid
                src = state.systems[order.source_id]
                assert src.owner_id == pid
                assert state.travel_turns(order.source_id, order.dest_id) is not None
                assert order.ships >= 1
                per_source[order.source_id] = per_source.get(order.source_id, 0) + order.ships
            for sid, total in per_source.items():
                assert total <= state.systems[sid].ships, f"over-committed {sid}"
        engine.end_turn(state, decide=ai.decide)


def test_marshal_does_not_mutate_state():
    """models/README.md:22 — `state` is read-only."""
    state = _state()

    def fingerprint(s):
        return (
            tuple((x.id, x.owner_id, x.ships, x.prod_progress)
                  for x in s.systems.values()),
            tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                  for f in s.fleets),
            s.turn, s.winner,
        )

    before = fingerprint(copy.deepcopy(state))
    ai.decide(state, 2)
    assert fingerprint(state) == before


def test_decide_is_deterministic_given_the_state():
    """No clock, no id(), no PYTHONHASHSEED — and no draw from state.rng."""
    state = _state()
    rng_before = state.rng.getstate()
    first = _totals(ai.decide(state, 2))
    assert state.rng.getstate() == rng_before, "marshal drew from state.rng"
    assert _totals(ai.decide(state, 2)) == first


def test_game_is_reproducible_from_its_seed():
    a = sim.play(11, "random", 24, 3, max_turns=400,
                 strategies=["marshal", "thinker", "marshal"])
    b = sim.play(11, "random", 24, 3, max_turns=400,
                 strategies=["marshal", "thinker", "marshal"])
    assert (a.winner, a.turns) == (b.winner, b.turns)


def test_marshal_works_as_the_autoplayed_human_seat():
    """`main.resolve_turn` calls `ai.decide` for the human seat, out of pid order."""
    state = _state(seat=1)
    state.players[1].is_human = True
    for _ in range(5):
        orders = ai.decide(state, 1)
        assert all(o.owner_id == 1 for o in orders)
        engine.end_turn(state, human_orders=orders, decide=ai.decide)
    assert state.turn == 5


def test_marshal_declares_no_aux_slider():
    """marshal has no tunable knob, so the AI tab must show it none."""
    assert ai.aux_spec("marshal") is None


def test_marshal_is_not_an_oracle(ma):
    """It predicts nobody, so knower must call it for real rather than proxy it."""
    assert not getattr(ma, "IS_ORACLE", False)
    assert not hasattr(ma, "is_oracle_seat")


# --------------------------------------------------------------------------- #
# The margins track the live jitter
# --------------------------------------------------------------------------- #
def test_margins_are_built_on_the_shared_edges(ma):
    """marshal must price fights from `combat`, not from a constant of its own.

    The edges themselves are tested in tests/test_combat.py; what matters here is
    that marshal's margins actually move with them.
    """
    config.DEFENDER_ADVANTAGE = 1.0
    config.COMBAT_JITTER = 0.10
    assert ma._defend_margin() == pytest.approx(combat.edge_defending() + ma.DEFEND_PAD)

    config.COMBAT_JITTER = 0.30
    assert ma._defend_margin() == pytest.approx(combat.edge_defending() + ma.DEFEND_PAD)
    assert ma._neutral_margin() >= combat.edge_attacking()
    assert ma._enemy_margin(1) >= combat.edge_attacking()


def test_advantage_makes_taking_dearer_and_holding_cheaper(ma):
    """The edges are only worth splitting if the planner's margins follow them."""
    config.COMBAT_JITTER = 0.10
    state = _board({1: (2, 40, 3), 2: (1, 20, 3)}, [(1, 2, 1)])
    target = state.systems[2]

    config.DEFENDER_ADVANTAGE = 1.0
    take_flat, hold_flat = ma._required(state, 2, target, 1), ma._defend_margin()
    config.DEFENDER_ADVANTAGE = 1.5
    take_adv, hold_adv = ma._required(state, 2, target, 1), ma._defend_margin()

    assert take_adv > take_flat, "a fortified target must cost more to take"
    assert hold_adv < hold_flat, "our own systems must get cheaper to hold"


def test_required_rises_with_jitter(ma):
    """A wilder swing must buy a bigger margin, not the hardcoded 1.222 one."""
    state = _board({1: (2, 40, 3), 2: (1, 10, 3)}, [(1, 2, 1)])
    target = state.systems[2]

    config.DEFENDER_ADVANTAGE = 1.0
    config.COMBAT_JITTER = 0.10
    at_default = ma._required(state, 2, target, 1)
    config.COMBAT_JITTER = 0.30
    at_wild = ma._required(state, 2, target, 1)
    config.COMBAT_JITTER = 0.0
    at_none = ma._required(state, 2, target, 1)

    assert at_wild > at_default, "a wilder swing must buy a bigger margin"
    assert at_none == at_default, "below default jitter the tuned absolute floors it"
    assert at_none >= target.ships + 1, "a tie hands the system to nobody"


def test_default_jitter_keeps_thinkers_tuned_margins(ma):
    """At 0.10 the tuned absolutes still win, so no measured baseline moves."""
    config.COMBAT_JITTER = 0.10
    config.DEFENDER_ADVANTAGE = 1.0
    assert ma._enemy_margin(1) == pytest.approx(1.3)      # ENEMY_NEAR
    assert ma._enemy_margin(9) == pytest.approx(1.9)      # ENEMY_FAR
    assert ma._neutral_margin() == pytest.approx(1.3)     # NEUTRAL_MARGIN


# --------------------------------------------------------------------------- #
# Commitment: the square law rewards the bigger strike
# --------------------------------------------------------------------------- #
def test_surplus_goes_in_with_the_wave(ma):
    """A big garrison next to a token enemy sends the army, not the entrance fee.

    `_required` prices a 1-ship enemy at 2. thinker sends exactly that and parks
    the other 28 — which is the behaviour this bot exists to fix.
    """
    state = _board({1: (2, 30, 3), 2: (1, 1, 3)}, [(1, 2, 1)])
    ma.COMMIT_SURPLUS = False
    lean = _totals(ai.decide(state, 2))[2]
    ma.COMMIT_SURPLUS = True
    committed = _totals(ai.decide(state, 2))[2]

    guard = math.ceil(ma.FRONTIER_GUARD * 1)          # one enemy ship next door
    assert lean == ma._required(state, 2, state.systems[2], 1), "baseline sent the price"
    assert committed == 30 - guard, "everything above the guard should go"
    assert committed > lean


def test_commitment_only_ever_adds(ma):
    """Phase 3b is a top-up. It must never reduce or redirect a planned strike.

    Not "every strike >= its price": a staggered far wave is deliberately under
    price on the turn it launches, and converges with the nearer wave later.
    """
    state = _state()
    for _ in range(12):
        if state.winner is not None:
            break
        ma.COMMIT_SURPLUS = False
        lean = _totals(ai.decide(state, 2))
        ma.COMMIT_SURPLUS = True
        committed = _totals(ai.decide(state, 2))
        for dest, ships in lean.items():
            assert committed.get(dest, 0) >= ships, f"3b took ships off {dest}"
        engine.end_turn(state, decide=ai.decide)


def test_commitment_does_not_touch_an_unstruck_target(ma):
    """Too strong to crack: the ships mass at home rather than feeding it."""
    state = _board({1: (2, 5, 3), 2: (1, 60, 3)}, [(1, 2, 1)])
    assert ai.decide(state, 2) == [], "must not trickle into a hopeless attack"


def test_commitment_respects_the_defensive_hold(ma):
    """Phase 1 clamps a threatened garrison to what it can spare. 3b must obey it."""
    #  3 strikes our 1 with 10; 1 holds, but only 7 of its 20 ships are spare.
    state = _board({1: (2, 20, 3), 2: (1, 1, 3), 3: (1, 40, 3)},
                   [(1, 2, 2), (1, 3, 2)])
    state.fleets.append(Fleet(owner_id=1, source_id=3, dest_id=1, ships=10,
                              turns_total=2, turns_remaining=1))
    needed = math.ceil(10 * ma._defend_margin())
    spare = 20 - needed
    assert 0 < spare < 20, "test board should leave a real but partial surplus"

    sent = sum(o.ships for o in ai.decide(state, 2) if o.source_id == 1)
    assert sent <= spare, f"spent {sent} of a garrison that can spare {spare}"


# --------------------------------------------------------------------------- #
# The stagger's nearer wave
# --------------------------------------------------------------------------- #
def test_pincer_reserves_the_nearer_wave(ma):
    """A source promised to next turn's converging wave can't fund another target.

    System 1 is 1 turn from both targets; system 2 is 2 turns from target 4.
    Target 4 is richer and too strong for system 1 alone, so it is massed at
    horizon 2 and system 1 is its nearer wave. Target 3 must not spend it.
    """
    state = _board({1: (2, 12, 3), 2: (2, 30, 3), 3: (1, 2, 5), 4: (1, 10, 2)},
                   [(1, 3, 1), (1, 4, 1), (2, 4, 2)])
    ma.RESERVE_PINCER = True
    held = _totals(ai.decide(state, 2))
    ma.RESERVE_PINCER = False
    loose = _totals(ai.decide(state, 2))

    assert loose.get(3, 0) > 0, "test board should tempt the poorer target"
    assert held.get(3, 0) == 0, "spent a source promised to the pincer"
    assert held.get(4, 0) >= loose.get(4, 0)


# --------------------------------------------------------------------------- #
# Standing aside: don't become the wall between two rivals
# --------------------------------------------------------------------------- #
def test_wedge_is_inert_in_a_duel(ma):
    """Two players can never put a second rival past the first."""
    state = _board({1: (2, 20, 3), 2: (1, 3, 3), 3: (0, 3, 3)},
                   [(1, 2, 1), (1, 3, 1), (2, 3, 1)], seats=2)
    assert ma._wedge(state, 2, state.systems[2]) == 0
    assert ma._wedge(state, 2, state.systems[3]) == 0


def test_wedge_counts_rivals_past_the_first(ma):
    """A node touching two rivals would make us their wall; one is just a border."""
    #  target 4 borders rivals 1 and 3; target 5 borders only rival 1.
    state = _board({1: (1, 5, 3), 2: (2, 20, 3), 3: (3, 5, 3),
                    4: (0, 3, 3), 5: (0, 3, 3)},
                   [(2, 4, 1), (2, 5, 1), (1, 4, 1), (3, 4, 1), (1, 5, 1)],
                   seats=4)  # the term is gated on a crowd; see the next test
    assert ma._wedge(state, 2, state.systems[4]) == 1
    assert ma._wedge(state, 2, state.systems[5]) == 0


def test_wedge_needs_a_crowd(ma):
    """Gated on the field: it measured negative at three players, positive at four."""
    state = _board({1: (1, 5, 3), 2: (2, 20, 3), 3: (3, 5, 3), 4: (0, 3, 3)},
                   [(2, 4, 1), (1, 4, 1), (3, 4, 1)], seats=3)
    assert ma._wedge(state, 2, state.systems[4]) == 0, "3 players: must stay off"

    state.players[4] = state.players[3].__class__(4, "P4", (60, 60, 200))
    assert ma._wedge(state, 2, state.systems[4]) == 1, "4 players: must switch on"


def test_wedge_deprioritises_the_wall(ma):
    """The penalty has to actually reach target selection, not just exist."""
    state = _board({1: (1, 5, 3), 2: (2, 20, 3), 3: (3, 5, 3),
                    4: (0, 3, 3), 5: (0, 3, 3)},
                   [(2, 4, 1), (2, 5, 1), (1, 4, 1), (3, 4, 1), (1, 5, 1)],
                   seats=4)
    wedged = ma._richness(state, 2, state.systems[4], 5)
    clear = ma._richness(state, 2, state.systems[5], 5)
    assert clear > wedged, "equal production, but 4 walls us between two rivals"


def test_a_dead_rival_stops_counting(ma):
    """`alive` gates the field, so a knocked-out seat can't keep the term on."""
    state = _board({1: (1, 5, 3), 2: (2, 20, 3), 3: (3, 5, 3), 4: (0, 3, 3)},
                   [(2, 4, 1), (1, 4, 1), (3, 4, 1)], seats=4)
    assert ma._wedge(state, 2, state.systems[4]) == 1
    state.players[4].alive = False
    assert ma._wedge(state, 2, state.systems[4]) == 0
