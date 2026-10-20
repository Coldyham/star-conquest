"""knower's Oracle: Council.

Council is knower's Search with a learned judge and borrowed candidates that play
their own lines, so it keeps knower's duties as an oracle (leave `state.rng`
alone, never touch the real board) and adds its own: the candidates it borrows must be offered
and play their own lines, a seat stored as ``council`` must play here, and no
other stop may see any of it.

Internals are reached through `sys.modules["sc_model_knower"]`.
"""

from __future__ import annotations

import sys

import pytest

from starconquest import ai, engine, mapgen

COUNCIL = 3.0


@pytest.fixture(scope="module")
def kn():
    assert "knower" in ai.load_models(), "models/knower.py failed to import"
    return sys.modules["sc_model_knower"]


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


def _state(seed=3, nodes=18, players=3, seat=2, aux=COUNCIL, strategy="knower"):
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


def test_council_draws_nothing_from_state_rng_and_leaves_the_board(kn):
    state = _state()
    before, fingerprint = state.rng.getstate(), kn._fingerprint(state)
    ai.decide(state, 2)
    assert state.rng.getstate() == before
    assert kn._fingerprint(state) == fingerprint


def test_the_oracle_is_built_and_nothing_was_swallowed(kn):
    """The search swallows errors and falls back, so a bug looks like a weak bot."""
    state = _state()
    kn.LAST_ERROR = None
    ai.decide(state, 2)
    assert kn.LAST_ERROR is None, f"council fell back: {kn.LAST_ERROR!r}"
    assert kn.LAST_ORACLE is not None
    assert kn.LAST_ORACLE.trusted >= {0, 1, 3}


def test_a_stored_council_seat_plays_as_knower_at_council_or_search(kn, fixed_work):
    """`ai.LEGACY`: council's On is Council and its Off is Search, so a stored
    link keeps its name, and its key, and plays the stop it was folded into."""
    for seed in (3, 4):
        for old, new in ((1.0, COUNCIL), (0.0, 2.0), (7.0, COUNCIL)):
            legacy = _state(seed=seed, aux=old, strategy="council")
            folded = _state(seed=seed, aux=new)
            assert _orders(ai.decide(legacy, 2)) == _orders(ai.decide(folded, 2)), (seed, old)
            assert legacy.players[2].ai_strategy == "council", "the seat is never rewritten"


def test_each_council_candidate_is_offered_and_plays_its_own_line(kn, fixed_work, monkeypatch):
    asked, offered = [], []
    real_plan, real_score = kn._external_plan, kn._score

    def spy(state, pid, borrowed, salt):
        if salt >= kn._SALT_POLICY:
            asked.append(borrowed[0])
        return real_plan(state, pid, borrowed, salt)

    def score(state, pid):
        offered.extend(name for name, _ in kn._SOURCE.values())
        return real_score(state, pid)

    monkeypatch.setattr(kn, "_external_plan", spy)
    monkeypatch.setattr(kn, "_score", score)
    ai.decide(_state(), 2)
    for name, _ in kn.COUNCIL_CANDIDATES:
        assert name in offered, f"{name}'s opening was not offered"
        assert name in asked, f"{name}'s line was not played on by {name}"
    assert not kn._COUNCIL and not kn._SOURCE, "nothing outlives the decide"


def test_search_borrows_no_council_candidate_and_judges_by_material(kn, fixed_work, monkeypatch):
    seen = []
    real = kn._external_plan
    monkeypatch.setattr(kn, "_external_plan",
                        lambda s, p, borrowed, salt: seen.append(borrowed) or real(s, p, borrowed, salt))
    monkeypatch.setattr(kn, "_council_eval", lambda: pytest.fail("Search read the judge"))
    ai.decide(_state(aux=2.0), 2)
    assert seen and all(b in kn.EXTERNAL_CANDIDATES for b in seen)


def test_council_is_the_fourth_named_stop_and_past_it_is_search(kn):
    assert ai.aux_spec("knower") == (kn.AUX_LABEL, 0.0, 3.0, 1.0, True)
    assert ai.aux_names("knower") == ("Off", "Predict", "Search", "Council")
    assert ai.aux_names("council") == ai.aux_names("knower")

    class _P:
        pass

    p = _P()
    for raw, want in ((0.0, 0), (1.0, 1), (2.0, 2), (2.7, 2), (3.0, 3), (3.5, 2), (12.0, 2),
                      (-3.0, 0)):
        p.aux = raw
        assert kn._stop_of(p) == want, raw
    p.aux = "nonsense"
    assert kn._stop_of(p) == kn.SEARCH_DEPTH_DEFAULT
    assert kn._stop_of(None) == kn.SEARCH_DEPTH_DEFAULT
    assert ai.aux_stop_name("knower", 12.0) == "Search"
    assert ai.aux_stop_name("knower", 3.0) == "Council"


def test_council_is_an_oracle_modelled_blind_by_another(kn):
    from starconquest.model import AiParams, Player
    seat = Player(id=2, name="", color=(0, 0, 0), ai_strategy="knower",
                  ai_params=AiParams(aux=COUNCIL))
    assert kn.is_oracle_seat(seat)
    assert kn.stand_in_params(seat) is None
    seat.ai_params = AiParams(aux=2.0)
    assert kn.stand_in_params(seat).aux == 0.0


def test_a_game_is_legal_and_reproducible(kn, fixed_work):
    def play():
        state = _state(seed=5, nodes=7, players=2, seat=1)
        state.players[2].ai_strategy = "knower"
        state.players[2].ai_params.aux = COUNCIL
        for _ in range(40):
            if state.winner is not None:
                break
            for pid in (1, 2):
                for o in ai.decide(state, pid):
                    assert o.owner_id == pid
                    assert state.systems[o.source_id].owner_id == pid
            engine.end_turn(state, decide=ai.decide)
        return kn._fingerprint(state), state.turn, state.winner

    assert play() == play()


def _setup(strategy, aux, **kw):
    from starconquest.model import AiParams
    from starconquest.settings import Settings
    s = Settings(**kw)
    s.ai_strategy = [strategy] * len(s.ai_strategy)
    s.ai = [AiParams(aux=aux) for _ in s.ai]
    return s


def test_the_default_setup_never_warns(kn):
    s = _setup("knower", COUNCIL)
    assert kn.setup_warning(s, list(range(2, s.players + 1))) == []


def test_council_warns_where_its_extra_cost_cuts_it_short_of_its_lanes(kn):
    kw = {"nodes": 40, "players": 2, "ship_ly_per_turn": 2.0}
    lines = kn.setup_warning(_setup("knower", COUNCIL, **kw), [2])
    assert lines[0].startswith("Knower at Council searching ") and " would need ~" in lines[0]
    assert any("turns ahead, short of its" in line for line in lines)
    assert lines[-1].startswith('Set Knower\'s Oracle to "Predict"')


def test_a_stored_council_seat_is_warned_about_as_council(kn):
    from starconquest.settings import setup_warnings
    s = _setup("council", 1.0, nodes=40, players=2, ship_ly_per_turn=2.0)
    assert setup_warnings(s, people={1})[0].startswith("Knower at Council searching ")


@pytest.fixture
def keep_config(monkeypatch):
    """Rebuilding a logged board writes its setup's knobs into `config`."""
    from starconquest import config, settings
    for _, const in settings._GLOBAL_KNOBS:
        monkeypatch.setattr(config, const, getattr(config, const))


def test_the_traps_rebuild_the_strikes_council_really_sent(kn, keep_config):
    from tests import council_traps
    assert [sent for *_, sent in council_traps.probe()] == [7, 11, 0, 14, 15, 10]


@pytest.mark.xfail(strict=True, reason="council's rollouts do not foresee a person's relief")
def test_council_walks_into_none_of_the_traps(kn, keep_config):
    from tests import council_traps
    assert all(outcome != "lost" for *_, outcome, _ in council_traps.probe())
