"""Four multiplayer tactics on marshal (`tools/mp_tactics.py`).

The bench reads each arm against stock on the same seat and seed, so it rests on
every arm playing exactly as marshal ships wherever its tactic has nothing to
act on: in a duel, for all of them.
"""

from __future__ import annotations

import pytest

from starconquest import ai, engine, mapgen
from starconquest import settings as settings_mod
from starconquest.settings import Settings
from tests.test_replay import _preserve_config
from tools import mp_tactics as mp


@pytest.mark.parametrize("nodes,ly", [(18, None), (7, 30.0)])
def test_every_arm_plays_as_stock_in_a_duel(nodes, ly):
    """At 7 systems and 30 ly/turn a rival is one turn away, where marshal's
    guard ignores it (`FAST_GUARD_WEIGHT`)."""
    if "marshal" not in ai.STRATEGIES:
        ai.load_models()
    stock = mp.make_arm("mp_test_stock", {})
    arms = {name: mp.make_arm(f"mp_test_{name}", knobs)
            for name, knobs in mp.ARMS.items() if knobs}
    knobs = {} if ly is None else {"ship_ly_per_turn": ly}
    with _preserve_config():
        settings_mod.apply_globals(Settings(mode="random", nodes=nodes, **knobs))
        state = mapgen.generate(mp.SEED_BASE, "random", nodes, 2)
        for p in state.players.values():
            p.is_human = False
            p.ai_strategy = "marshal"
        checked = 0
        while state.winner is None and state.turn < 40:
            for pid in (1, 2):
                if not state.players[pid].alive:
                    continue
                want = mp._key(stock.decide(state, pid))
                for name, mod in arms.items():
                    assert mp._key(mod.decide(state, pid)) == want, (name, state.turn, pid)
                checked += 1
            engine.end_turn(state, decide=ai.decide)
    assert checked > 10


def test_standing_names_the_leader_and_the_weak_rival():
    with _preserve_config():
        settings_mod.apply_globals(Settings(mode="random", nodes=18))
        state = mapgen.generate(mp.SEED_BASE, "random", 18, 4)
    assert mp.standing(state, 1)[2]           # four alive: every seat has a standing
    owned = {pid: [s for s in state.systems.values() if s.owner_id == pid] for pid in (1, 2, 3, 4)}
    rich = next(s for s in state.systems.values() if s.owner_id == 0)
    rich.owner_id, rich.production = 2, 2     # pid 2 the leader
    lead, weak, st = mp.standing(state, 1)
    assert lead == 2 and st[2] > 1.0
    assert weak is None                       # 3 and 4 at 0.73 of the mean, not < 0.6
    for s in owned[4]:
        s.production = 50                     # 4 is nearly broke
    assert mp.standing(state, 1)[1] == 4
