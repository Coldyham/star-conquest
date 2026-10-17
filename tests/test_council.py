"""The advised Search bot: `models/council.py`.

council runs a private copy of knower, so it inherits knower's duties as an
oracle (leave `state.rng` alone, never touch the real board) and adds its own:
aux 0 must be knower's Search exactly, and the advisers it borrows must be
offered and play their own lines.

Internals are reached through `sys.modules["sc_model_council"]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from starconquest import ai, engine, mapgen


@pytest.fixture(scope="module")
def co():
    assert "council" in ai.load_models(), "models/council.py failed to import"
    return sys.modules["sc_model_council"]


@pytest.fixture(autouse=True)
def _clean_registry():
    before = dict(ai.STRATEGIES)
    yield
    ai.STRATEGIES.clear()
    ai.STRATEGIES.update(before)


@pytest.fixture
def fixed_work():
    """Guards lifted, so a comparison can't hinge on the wall clock."""
    ai.set_budget_scale(1000)
    try:
        yield
    finally:
        ai.set_budget_scale(1)


def _state(seed=3, nodes=18, players=3, seat=2, aux=1.0, strategy="council"):
    state = mapgen.generate_random(seed, num_nodes=nodes, num_players=players)
    for p in state.players.values():
        p.is_human = False
        if not p.is_neutral:
            p.ai_strategy = "thinker"
    state.players[seat].ai_strategy = strategy
    state.players[seat].ai_params.aux = aux
    return state


def _orders(orders):
    return sorted((o.owner_id, o.source_id, o.dest_id, o.ships) for o in orders)


def test_council_draws_nothing_from_state_rng_and_leaves_the_board(co):
    state = _state()
    before, fingerprint = state.rng.getstate(), co._kn._fingerprint(state)
    ai.decide(state, 2)
    assert state.rng.getstate() == before
    assert co._kn._fingerprint(state) == fingerprint


def test_the_oracle_is_built_and_nothing_was_swallowed(co):
    """The search swallows errors and falls back, so a bug looks like a weak bot."""
    state = _state()
    co._kn.LAST_ERROR = None
    ai.decide(state, 2)
    assert co._kn.LAST_ERROR is None, f"council fell back: {co._kn.LAST_ERROR!r}"
    assert co._kn.LAST_ORACLE is not None
    assert co._kn.LAST_ORACLE.trusted >= {0, 1, 3}


def test_off_is_knower_searching_exactly(co, fixed_work):
    for seed in (3, 4):
        council = _state(seed=seed, aux=0.0)
        knower = _state(seed=seed, aux=2.0, strategy="knower")
        assert _orders(ai.decide(council, 2)) == _orders(ai.decide(knower, 2)), seed


def test_actuary_is_offered_and_plays_its_own_line(co, fixed_work, monkeypatch):
    asked = []
    real = co._adviser_plan

    def spy(state, pid, name, salt):
        asked.append(name)
        return real(state, pid, name, salt)

    monkeypatch.setattr(co, "_adviser_plan", spy)
    ai.decide(_state(), 2)
    assert "actuary" in co._SOURCE.values(), "actuary's opening was not offered"
    assert "actuary" in asked, "actuary's line was not played on by actuary"


def test_off_borrows_no_extra_adviser(co, fixed_work):
    ai.decide(_state(aux=0.0), 2)
    assert "actuary" not in co._kn.EXTERNAL_CANDIDATES


def test_aux_is_two_named_stops_read_tolerantly(co):
    assert ai.aux_spec("council") == (co.AUX_LABEL, 0.0, 1.0, 1.0, True)
    assert ai.aux_names("council") == ("Off (knower)", "On")

    class _P:
        pass

    p = _P()
    p.ai_params = _P()
    for raw, want in ((0.0, False), (0.4, False), (1.0, True), (7.0, True), (-3.0, False)):
        p.ai_params.aux = raw
        assert co._on(p) is want, raw
    p.ai_params.aux = "nonsense"
    assert co._on(p) is True
    assert co._on(_P()) is True


def test_a_game_is_legal_and_reproducible(co, fixed_work):
    def play():
        state = _state(seed=5, nodes=7, players=2, seat=1)
        state.players[2].ai_strategy = "council"
        for _ in range(40):
            if state.winner is not None:
                break
            for pid in (1, 2):
                for o in ai.decide(state, pid):
                    assert o.owner_id == pid
                    assert state.systems[o.source_id].owner_id == pid
            engine.end_turn(state, decide=ai.decide)
        return co._kn._fingerprint(state), state.turn, state.winner

    assert play() == play()


def test_a_reload_starts_from_an_unpatched_knower(fixed_work):
    """`main._begin_game` reloads the roster on every start, after council's last
    decide has patched its private knower."""
    assert "council" in ai.load_models()
    first = sys.modules["sc_model_council"]
    ai.decide(_state(), 2)
    assert first._kn._evaluate is first._learned_evaluate
    assert "council" in ai.load_models()
    co = sys.modules["sc_model_council"]
    assert co._KNOWER_EXTERNALS == sys.modules["sc_model_knower"].EXTERNAL_CANDIDATES
    assert co._material_evaluate.__name__ == "_evaluate"
    assert co._knower_advance.__name__ == "_advance"
    assert co._knower_external_plan.__name__ == "_external_plan"
    council = _state(aux=0.0)
    knower = _state(aux=2.0, strategy="knower")
    assert _orders(ai.decide(council, 2)) == _orders(ai.decide(knower, 2))


def _setup(strategy, aux, **kw):
    from starconquest.model import AiParams
    from starconquest.settings import Settings
    s = Settings(**kw)
    s.ai_strategy = [strategy] * len(s.ai_strategy)
    s.ai = [AiParams(aux=aux) for _ in s.ai]
    return s


def test_the_default_setup_never_warns(co):
    for aux in (0, 1):
        s = _setup("council", aux)
        assert co.setup_warning(s, list(range(2, s.players + 1))) == []


def test_off_warns_where_knower_searching_does(co):
    kn = sys.modules["sc_model_knower"]
    for kw in ({"nodes": 24, "ship_ly_per_turn": 1.0}, {"nodes": 40, "ship_ly_per_turn": 2.0}):
        assert (bool(co.setup_warning(_setup("council", 0, players=2, **kw), [2]))
                == bool(kn.setup_warning(_setup("knower", 2, players=2, **kw), [2]))), kw


def test_on_warns_where_its_extra_cost_cuts_it_short_of_its_lanes(co):
    kw = {"nodes": 40, "players": 2, "ship_ly_per_turn": 2.0}
    assert co.setup_warning(_setup("council", 0, **kw), [2]) == []
    lines = co.setup_warning(_setup("council", 1, **kw), [2])
    assert lines[0].startswith("Council searching ") and " would need ~" in lines[0]
    assert any("turns ahead, short of its" in line for line in lines)
    assert lines[-1].startswith('Use Knower at "Predict"')


@pytest.fixture
def keep_config(monkeypatch):
    """Rebuilding a logged board writes its setup's knobs into `config`."""
    from starconquest import config, settings
    for _, const in settings._GLOBAL_KNOBS:
        monkeypatch.setattr(config, const, getattr(config, const))


def test_the_traps_rebuild_the_strikes_council_really_sent(co, keep_config):
    from tests import council_traps
    assert [sent for *_, sent in council_traps.probe()] == [7, 11, 0, 14, 15, 10]


@pytest.mark.xfail(strict=True, reason="council's rollouts do not foresee a person's relief")
def test_council_walks_into_none_of_the_traps(co, keep_config):
    from tests import council_traps
    assert all(outcome != "lost" for *_, outcome, _ in council_traps.probe())


def test_council_never_reloads_the_roster():
    """An oracle that called `ai.load_models` would wipe every bot's memory mid-game."""
    text = Path(__file__).resolve().parent.parent.joinpath("models", "council.py").read_text()
    assert "load_models(" not in text
