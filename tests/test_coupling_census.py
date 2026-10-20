"""The coupling census: `tools/coupling_census.py`.

It patches a bot's module constants in place around each `decide`, so the one
thing it must never do is leave a constant patched. Past that, its arithmetic is
checked on a board small enough to work out by hand.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from starconquest import ai
from starconquest.model import GameState, Player, System
from tools import coupling_census as cc


@pytest.fixture(scope="module")
def ma():
    """marshal as the board below was worked out for: ``SPARE_TARGET`` off (with
    it the 15-ship stack is affordable from 20, ``_front_board``), the guard at
    0.55 and the neutral margin at 1.3."""
    assert "marshal" in ai.load_models(), "models/marshal.py failed to import"
    module = sys.modules["sc_model_marshal"]
    pinned = {"SPARE_TARGET": False, "FRONTIER_GUARD": 0.55, "NEUTRAL_MARGIN": 1.3}
    before = {name: getattr(module, name) for name in pinned}
    for name, value in pinned.items():
        setattr(module, name, value)
    yield module
    for name, value in before.items():
        setattr(module, name, value)


def _front_board():
    """One marshal front (system 1, 20 ships) between a rival stack of 15 and a
    neutral of 2, both two turns away.

    Stock marshal strikes the neutral at its price (3) and pours after it what
    its pour floor leaves, 20 - 3 - ceil(0.85 * 15) = 4: 7 ships. The rival costs
    16 and is never affordable. A pour floor at the guard's 0.55 keeps
    ceil(0.55 * 15) = 9 instead and pours 8, 11 in all, and a lower guard alone
    changes nothing, since the pour floor sits above it; with the pour off the
    strike is the price alone, 3, whatever the floor.
    """
    state = GameState.new(1)
    for sid, owner, ships in ((1, 2, 20), (2, 1, 15), (3, 0, 2)):
        state.systems[sid] = System(id=sid, pos=(float(sid) * 10.0, 0.0),
                                    owner_id=owner, ships=ships, production=3)
    state.add_lane(1, 2, 12.0, 2)
    state.add_lane(1, 3, 12.0, 2)
    state.rebuild_topology()
    state.players = {0: Player(0, "Neutral", (90, 90, 90), is_neutral=True),
                     1: Player(1, "P1", (200, 60, 60)),
                     2: Player(2, "P2", (60, 60, 200))}
    state.players[2].ai_strategy = "marshal"
    return state


def test_the_board_plays_as_worked_out(ma):
    state = _front_board()
    assert cc._decide(ma, state, 2, {}) == {(1, 3): 7}
    assert cc._decide(ma, state, 2, {"POUR_GUARD": 0.55}) == {(1, 3): 11}
    assert cc._decide(ma, state, 2, {"FRONTIER_GUARD": 0.35}) == {(1, 3): 7}
    assert cc._decide(ma, state, 2, {"COMMIT_SURPLUS": False}) == {(1, 3): 3}


def test_a_pair_that_shares_the_pour_interacts_and_masks(ma):
    """Lowering the pour floor frees ships only the pour spends, so with the pour
    off the floor changes nothing: 3 - 11 - 3 + 7 = -4, and the pour masks it."""
    perturb = (("POUR_GUARD", 0.55), ("COMMIT_SURPLUS", False))
    singles, pairs = cc.census_position(ma, _front_board(), 2, perturb)
    assert singles == [(True, 4), (True, 4)]
    interacts, ships, j_masks_i, j_enables_i, i_masks_j, i_enables_j, both = pairs[(0, 1)]
    assert interacts and ships == 4
    assert j_masks_i and not j_enables_i
    assert not i_masks_j and not i_enables_j
    assert both


def test_a_pair_on_one_constant_is_never_crossed(ma):
    perturb = (("FRONTIER_GUARD", 0.35), ("FRONTIER_GUARD", 0.75))
    _singles, pairs = cc.census_position(ma, _front_board(), 2, perturb)
    assert pairs == {}


def test_the_census_puts_every_constant_back(ma):
    before = (ma.FRONTIER_GUARD, ma.COMMIT_SURPLUS)
    cc.census_position(ma, _front_board(), 2, cc.MARSHAL_PERTURB[:6] + (
        ("COMMIT_SURPLUS", False),))
    assert (ma.FRONTIER_GUARD, ma.COMMIT_SURPLUS) == before


def test_a_constant_is_restored_even_when_decide_raises():
    def decide(_state, _pid):
        raise RuntimeError("boom")
    module = SimpleNamespace(GUARD=0.55, decide=decide)
    with pytest.raises(RuntimeError):
        cc._decide(module, None, 1, {"GUARD": 0.1})
    assert module.GUARD == 0.55


def test_every_default_perturbation_names_a_marshal_constant(ma):
    for const, value in cc.MARSHAL_PERTURB:
        assert hasattr(ma, const), const
        assert getattr(ma, const) != value, f"{const}={value} is its shipped value"


def test_groups_join_constants_through_shared_pairs():
    best = {("A", "B"): (0.05,), ("B", "C"): (0.03,), ("D", "E"): (0.01,)}
    groups = cc._groups(best, 0.02)
    assert [members for members, _links in groups] == [{"A", "B", "C"}]


def test_a_perturbation_reads_as_sweep_reads_an_arm():
    assert cc._parse_perturb("FEED=1.5") == ("FEED", 1.5)
    assert cc._parse_perturb("DENY_SWAP_MIN_TURNS=3") == ("DENY_SWAP_MIN_TURNS", 3)
    assert cc._parse_perturb("COMMIT_SURPLUS=false") == ("COMMIT_SURPLUS", False)
    with pytest.raises(SystemExit):
        cc._parse_perturb("FEED")
