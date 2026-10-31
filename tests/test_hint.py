"""Training help: the send popup's forecast (`combat.forecast_landing`) and the
Hint control (`main.take_hint`), which takes a game off the leaderboard."""

from __future__ import annotations

import random

from starconquest import ai, combat, config, engine
from starconquest import main as game_main
from starconquest import settings as settings_mod
from starconquest.geometry import WorldView
from starconquest.model import Order
from starconquest.replay import GameLog
from starconquest.viewstate import Ui
from tests.test_engine import make_state


def _ui(state, seat: int = 1) -> Ui:
    ui = Ui(view=WorldView((0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 800.0, 600.0)), human_id=seat)
    ui.visible = set(state.systems)
    return ui


# -- odds -------------------------------------------------------------------- #
def test_odds_agree_with_the_dice():
    """The odds are the share of rolls `resolve_fight` wins, not a rule of thumb."""
    j, adv = 0.1, 1.0
    rng = random.Random(5)
    for attacker, defender in ((8, 8), (9, 8), (12, 11), (7, 5)):
        old = config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE
        config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = j, adv
        try:
            trials = 4000
            wins = sum(combat.resolve_fight(rng, 1, attacker, 2, defender, defender_owner=2)[0] == 1
                       for _ in range(trials))
        finally:
            config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE = old
        assert abs(combat.fight_odds(attacker, defender, j, adv).capture - wins / trials) < 0.04


def test_sure_means_the_worst_roll_wins():
    for d in (1, 5, 8, 13, 30):
        for adv in (1.0, 1.2):
            a = combat.sure_count(d, 0.1, adv)
            assert combat.fight_odds(a, d, 0.1, adv).sure
            assert not combat.fight_odds(a - 1, d, 0.1, adv).sure
            assert combat.preview_fight(a, d, 0.1, adv).worst.winner == combat.ATTACKER


def test_an_empty_garrison_is_sure_and_no_ships_is_not():
    assert combat.fight_odds(3, 0, 0.1, 1.0).sure
    assert combat.fight_odds(0, 3, 0.1, 1.0).capture == 0.0


# -- the garrison a fleet will meet ------------------------------------------ #
def test_garrison_after_matches_production():
    """Every hull built on the way is met, the landing turn's included."""
    s = make_state([(0, 1, 0, 3), (1, 2, 5, 3)], [(0, 1, 5)])
    s.systems[1].prod_progress = 2
    predicted = [combat.garrison_after(s, 1, t) for t in range(1, 8)]
    seen = []
    for _ in range(7):
        engine._production(s)
        seen.append(s.systems[1].ships)
    assert predicted == seen


def test_a_neutral_does_not_grow():
    s = make_state([(0, 1, 9, 3), (1, 0, 6, 2)], [(0, 1, 3)])
    assert combat.garrison_after(s, 1, 10) == 6


def test_the_forecast_is_the_fight_that_happens():
    """A sure forecast is a capture, with production counted on the way."""
    s = make_state([(0, 1, 11, 100), (1, 2, 8, 2)], [(0, 1, 3)])
    fc = combat.forecast_landing(s, 1, 1, 3, 11)
    assert fc.garrison == 9 and not fc.reinforce
    assert fc.odds is not None and not fc.odds.sure and fc.sure_at == combat.sure_count(9, config.COMBAT_JITTER,
                                                                                         config.DEFENDER_ADVANTAGE)
    s2 = make_state([(0, 1, fc.sure_at, 100), (1, 2, 8, 2)], [(0, 1, 3)])
    engine.apply_order(s2, Order(1, 0, 1, fc.sure_at))
    for _ in range(3):
        engine.end_turn(s2)
    assert s2.systems[1].owner_id == 1


def test_landing_counts_everything_arriving_together():
    s = make_state([(0, 1, 10, 3), (2, 1, 10, 3), (1, 0, 9, 2)], [(0, 1, 2), (2, 1, 2)])
    ui = _ui(s)
    ui.pending = [Order(1, 0, 1, 6), Order(1, 2, 1, 4)]
    assert ui.landing_at(s, 1, 2) == 10
    assert ui.landing_at(s, 1, 3) == 0


def test_reinforcing_is_not_a_fight():
    s = make_state([(0, 1, 10, 3), (1, 1, 4, 2)], [(0, 1, 2)])
    fc = combat.forecast_landing(s, 1, 1, 2, 5)
    assert fc.reinforce and fc.odds is None


# -- the hint ---------------------------------------------------------------- #
def _game(seed: int = 3):
    ai.load_models()
    state = settings_mod.build_state(settings_mod.Settings(), seed)
    return state, _ui(state), GameLog(seed=seed, settings={})


def test_a_hint_leaves_the_game_alone(tmp_path, monkeypatch):
    state, ui, log = _game()
    monkeypatch.setattr(GameLog, "save", lambda self: None)
    before = (state.rng.getstate(), {sid: (n.owner_id, n.ships) for sid, n in state.systems.items()},
              state.players[1].ai_strategy)
    game_main.take_hint(state, ui, log)      # the first press only asks
    assert ui.hint_armed and not ui.hinted and not ui.hint_live(state)
    game_main.take_hint(state, ui, log)
    assert ui.hinted and ui.hint_live(state) and log.hinted_from == state.turn
    assert all(o.owner_id == 1 and o.ships > 0 for o in ui.hint_orders)
    after = (state.rng.getstate(), {sid: (n.owner_id, n.ships) for sid, n in state.systems.items()},
             state.players[1].ai_strategy)
    assert before == after
    game_main.take_hint(state, ui, log)      # pressed again: hidden, no second ask
    assert not ui.hint_live(state) and ui.hinted and not ui.hint_armed


def test_a_hinted_win_is_never_posted():
    state, ui, _ = _game()
    state.winner = 1
    ui.hand_turns = 3
    assert ui.can_post(state)
    ui.hinted = True
    assert not ui.can_post(state)


def test_no_hint_in_a_shared_match_or_under_autoplay():
    state, ui, _ = _game()
    assert ui.can_hint(state)
    ui.autoplay = True
    assert not ui.can_hint(state)
    ui.autoplay, ui.pbp_match = False, "m"
    assert not ui.can_hint(state)


def test_the_log_keeps_a_hint_only_on_the_branch_that_saw_it():
    log = GameLog(seed=1, settings={}, turns=[{} for _ in range(6)], hinted_from=4)
    assert GameLog.from_dict(log.to_dict()).hinted_from == 4
    assert "hinted_from" not in GameLog(seed=1, settings={}).to_dict()
    assert log.fork(4).hinted_from == 4
    assert log.fork(3).hinted_from is None
    log.truncate(2)
    assert log.hinted_from is None
