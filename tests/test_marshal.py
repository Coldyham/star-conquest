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
    names = ("FRONTIER_GUARD", "COMMIT_SURPLUS", "RESERVE_PINCER",
             "CONSOLIDATE", "AVOID_ABANDONED", "RISK_PARITY")
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

    # The *enemy* margin deliberately does not: it carries the advantage half of
    # the edge and none of the jitter half, because a beatable garrison usually
    # evacuates rather than fighting. See `_enemy_margin` and bot-design.
    config.DEFENDER_ADVANTAGE = 1.4
    assert ma._enemy_margin() == pytest.approx(1.4)


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


def test_holding_prices_the_jitter_but_taking_does_not(ma):
    """A wilder swing must buy a bigger margin where a fight is *certain* — our
    own garrison, which cannot decline the engagement — and must buy nothing where
    it is not. 86.7% of out-matched garrisons evacuate, so a premium against the
    dice on the attacking side is paid on a fight that mostly never happens.
    """
    state = _board({1: (2, 40, 3), 2: (1, 10, 3)}, [(1, 2, 1)])
    target = state.systems[2]
    config.DEFENDER_ADVANTAGE = 1.0

    config.COMBAT_JITTER = 0.10
    hold_default, take_default = ma._defend_margin(), ma._required(state, 2, target, 1)
    config.COMBAT_JITTER = 0.30
    hold_wild, take_wild = ma._defend_margin(), ma._required(state, 2, target, 1)

    assert hold_wild > hold_default, "defending must price a wilder swing"
    assert take_wild == take_default, "attacking must not pay for the dice"

def test_the_enemy_margin_is_the_advantage_and_nothing_else(ma):
    """No pad, no absolute, no distance ramp — `ENEMY_NEAR`, `ENEMY_FAR` and
    `NEAR_PAD` are gone, and the margin is recovered from the two public edges
    (their ratio is `advantage**2`) rather than read off `config`.
    """
    config.COMBAT_JITTER = 0.10
    for advantage in (0.75, 1.0, 1.25, 1.5):
        config.DEFENDER_ADVANTAGE = advantage
        assert ma._enemy_margin() == pytest.approx(max(1.0, advantage))
    for name in ("ENEMY_NEAR", "ENEMY_FAR", "NEAR_PAD"):
        assert not hasattr(ma, name), f"{name} is dead — delete it, don't strand it"

    config.DEFENDER_ADVANTAGE = 1.0
    assert ma._neutral_margin() == pytest.approx(1.3)     # NEUTRAL_MARGIN, untouched


# --------------------------------------------------------------------------- #
# The race for someone else's target
# --------------------------------------------------------------------------- #
def test_an_evacuated_system_is_priced_against_the_fleet_taking_it(ma):
    """Three seats: P1 abandons the centre, P3's stack is a turn from landing on
    it, and marshal is two turns away. The centre reads as 0 ships with nobody
    reinforcing it, so the old price was 1 and Phase 3b posted the whole garrison
    into a node that would be held by 12 ships by the time it arrived — losing the
    strike and leaving its own system empty for the counter.
    """
    state = _board({1: (1, 9, 4), 2: (1, 0, 3), 3: (2, 6, 4), 4: (3, 9, 4)},
                   [(2, 1, 1), (2, 3, 2), (2, 4, 1)])
    state.fleets.append(Fleet(owner_id=3, source_id=4, dest_id=2, ships=12,
                              turns_total=1, turns_remaining=1))

    assert ma._required(state, 2, state.systems[2], 2) > 12, "priced as if empty"
    assert ai.decide(state, 2) == [], "walked 6 ships into a 12-ship garrison"


def test_a_contested_neutral_is_deliberately_left_static(ma):
    """The rival-held case is repriced; the neutral one is knowingly not.

    Measured, not overlooked: the price is a *gate*, and Phase 3b sends far more
    than it. Under-pricing a contested neutral opens the gate and the surplus
    usually wins the race, where honest pricing cedes the node — see "Racing a
    third player for the same system" in `docs/bot-design.md`.
    """
    lanes = [(1, 2, 2), (2, 3, 1)]
    quiet = _board({1: (2, 40, 3), 2: (0, 4, 3), 3: (3, 20, 4)}, lanes)
    contested = _board({1: (2, 40, 3), 2: (0, 4, 3), 3: (3, 20, 4)}, lanes)
    contested.fleets.append(Fleet(owner_id=3, source_id=3, dest_id=2, ships=20,
                                  turns_total=1, turns_remaining=1))

    static = math.ceil(4 * ma._neutral_margin())
    assert ma._required(quiet, 2, quiet.systems[2], 2) == static
    assert ma._required(contested, 2, contested.systems[2], 2) == static


def test_the_reprice_is_inert_in_a_duel(ma):
    """With two players, the only ships aimed at a rival's system are its own, and
    `_inbound` already counts those — so a duel is bit-identical to the pricing
    that predates this. That is what keeps the fix confined to the case it was
    measured on.
    """
    state = _board({1: (2, 40, 3), 2: (1, 5, 3), 3: (1, 20, 4)},
                   [(1, 2, 2), (2, 3, 1)], seats=2)
    state.fleets.append(Fleet(owner_id=1, source_id=3, dest_id=2, ships=7,
                              turns_total=1, turns_remaining=1))
    assert ma._rival_waves(state, 2, 2, 2) == []


def test_a_rival_landing_with_us_is_folded_too(ma):
    """`combat.resolve_arrival` totals every owner landing this turn, so a bloc
    arriving *with* us is one more side of the fight — not a softening-up we get
    for free. Folded like any other wave rather than distinguished by arrival turn.
    """
    lanes = [(1, 2, 2), (2, 3, 2)]
    alone = _board({1: (2, 40, 3), 2: (1, 5, 3), 3: (3, 20, 4)}, lanes)
    shared = _board({1: (2, 40, 3), 2: (1, 5, 3), 3: (3, 20, 4)}, lanes)
    shared.fleets.append(Fleet(owner_id=3, source_id=3, dest_id=2, ships=30,
                               turns_total=2, turns_remaining=2))

    assert (ma._required(shared, 2, shared.systems[2], 2)
            > ma._required(alone, 2, alone.systems[2], 2))


def test_the_owners_own_reinforcements_are_not_counted_twice(ma):
    """`_inbound` already prices the target owner's own fleets, so `_rival_waves`
    must exclude them — counting a garrison's reinforcement as a hostile bloc
    besieging it would inflate every price on the board.
    """
    state = _board({1: (2, 40, 3), 2: (1, 5, 3), 3: (1, 20, 4)},
                   [(1, 2, 2), (2, 3, 1)])
    state.fleets.append(Fleet(owner_id=1, source_id=3, dest_id=2, ships=7,
                              turns_total=1, turns_remaining=1))

    assert ma._rival_waves(state, 2, 2, 2) == []
    defence = 5 + 7 + ma._production_by(state.systems[2], 2)
    assert ma._required(state, 2, state.systems[2], 2) == max(
        defence + 1, math.ceil(defence * ma._enemy_margin()))


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


# --------------------------------------------------------------------------- #
# Two doomed neighbours
# --------------------------------------------------------------------------- #
def _besieged(lane=3, ours=(6, 7), incoming=(9, 9), prods=(3, 2)):
    """Our 1 and 2 side by side, each with a stack inbound that out-guns it.

    Enemy 3 and 4 sit behind 20-ship garrisons, so `_evacuate` can never step
    forward and the choice really is between holding, retreating and pooling.
    """
    a, b = ours
    pa, pb = prods
    state = _board({1: (2, a, pa), 2: (2, b, pb), 3: (1, 20, 3), 4: (1, 20, 3)},
                   [(1, 2, lane), (1, 3, 4), (2, 4, 4)])
    for dest, (src, ships) in zip((1, 2), zip((3, 4), incoming)):
        state.fleets.append(Fleet(owner_id=1, source_id=src, dest_id=dest,
                                  ships=ships, turns_total=4, turns_remaining=4))
    return state


def test_two_doomed_neighbours_do_not_trade_garrisons(ma):
    """The bug this phase exists for, kept as the null case beside the fix.

    `_evacuate` retreats to the friend with the biggest garrison, which for each
    of a pair of doomed neighbours is the other one — so both empty into the lane
    between them and both systems are taken by fleets that were already on their
    way.
    """
    ma.CONSOLIDATE = False
    ma.AVOID_ABANDONED = False
    ma.RISK_PARITY = 1e9   # isolate the bug from the unrelated hold-at-parity path
    swapped = {(o.source_id, o.dest_id) for o in ai.decide(_besieged(), 2)}
    assert swapped == {(1, 2), (2, 1)}, "the board no longer reproduces the bug"

    ma.RISK_PARITY = 1.0
    ma.CONSOLIDATE = True
    ma.AVOID_ABANDONED = True
    orders = ai.decide(_besieged(), 2)
    moves = {(o.source_id, o.dest_id) for o in orders}
    assert (1, 2) not in moves or (2, 1) not in moves, f"still trading: {moves}"


def test_the_richer_of_two_doomed_neighbours_is_the_one_held(ma):
    """Pooled, the pair holds one of the two systems; separately it holds neither.

    1 produces faster, so 1 is the system kept and 2 is the garrison spent on it.
    """
    state = _besieged()
    need, deadline = 11 - 6, 4          # ceil(9 * defend margin) - production - ships
    assert ma._defend_margin() > 1.0 and state.systems[2].ships >= need
    assert (state.travel_turns(1, 2) or 99) <= deadline

    orders = ai.decide(state, 2)
    assert [(o.source_id, o.dest_id, o.ships) for o in orders] == [(2, 1, 7)]


def test_consolidation_will_not_send_ships_that_arrive_too_late(ma):
    """Relief has to land by the deadline Phase 1 measured, or it is not relief.

    Same board, but the lane between the two runs longer than the siege does.
    Neither can help the other, and neither may retreat into the other either.
    """
    orders = ai.decide(_besieged(lane=5), 2)
    assert not [o for o in orders if {o.source_id, o.dest_id} == {1, 2}], orders


def test_a_retreat_never_goes_into_a_system_being_abandoned(ma):
    """Consolidation off: the garrison still has to leave, but not into the pair.

    3 is a small rear system and 2 is a big doomed one. The old rule ranked
    refuges by garrison size alone and picked 2, which was emptying itself.
    """
    ma.CONSOLIDATE = False
    state = _board({1: (2, 3, 3), 2: (2, 20, 3), 3: (2, 2, 3), 4: (1, 40, 3)},
                   [(1, 2, 1), (1, 3, 1), (1, 4, 1), (2, 4, 1)])
    for dest, ships in ((1, 20), (2, 40)):
        state.fleets.append(Fleet(owner_id=1, source_id=4, dest_id=dest, ships=ships,
                                  turns_total=1, turns_remaining=1))

    ma.AVOID_ABANDONED = False
    assert (1, 2) in {(o.source_id, o.dest_id) for o in ai.decide(state, 2)}, \
        "the board no longer reproduces the bug"

    ma.AVOID_ABANDONED = True
    moves = {(o.source_id, o.dest_id) for o in ai.decide(state, 2)}
    assert (1, 3) in moves and (1, 2) not in moves, f"retreated into the rout: {moves}"
