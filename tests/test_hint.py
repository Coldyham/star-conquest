"""Training help: the send popup's forecast (`combat.forecast_landing`) and the
Hint control (`main.press_hint`), which takes a game off the leaderboard."""

from __future__ import annotations

import random

from starconquest import ai, combat, config, engine
from starconquest import main as game_main
from starconquest import settings as settings_mod
from starconquest.geometry import WorldView
from starconquest.model import ForwardRule, Order
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
    assert game_main.press_hint(state, ui, log)    # the first press only asks
    assert not ui.hinted and not ui.hint_live(state)
    game_main.take_hint(state, ui, log)           # ...and the modal's Show takes it
    assert ui.hinted and ui.hint_live(state) and log.hinted_from == state.turn
    assert all(o.owner_id == 1 and o.ships > 0 for o in ui.hint_orders)
    after = (state.rng.getstate(), {sid: (n.owner_id, n.ships) for sid, n in state.systems.items()},
             state.players[1].ai_strategy)
    assert before == after
    assert not game_main.press_hint(state, ui, log)   # pressed again: hidden
    assert not ui.hint_live(state) and ui.hinted
    assert not game_main.press_hint(state, ui, log)   # and again: shown, no second ask
    assert ui.hint_live(state)


def _hinted(*hint):
    """Our system 0 (20 ships) between neighbours 1 and 2, hinted ``hint``."""
    s = make_state([(0, 1, 20, 3), (1, 0, 5, 3), (2, 0, 5, 3)], [(0, 1, 2), (0, 2, 2)])
    ui = _ui(s)
    ui.hint_turn, ui.hint_orders = s.turn, [Order(1, 0, d, n) for d, n in hint]
    return s, ui


def test_the_hint_leaves_out_what_our_orders_already_send():
    s, ui = _hinted((1, 8), (2, 6))
    ui.pending.append(Order(1, 0, 1, 8))
    assert [(o.dest_id, o.ships) for o in ui.hint_left(s)] == [(2, 6)]
    ui.pending[0].ships = 5                        # short of it: the rest is left
    assert [(o.dest_id, o.ships) for o in ui.hint_left(s)] == [(1, 3), (2, 6)]
    ui.pending.clear()
    ui.auto_forward[0] = ForwardRule({2: 100}, 0)  # a rule's sends count too
    assert [(o.dest_id, o.ships) for o in ui.hint_left(s)] == [(1, 8)]


def test_applying_queues_what_is_left_and_puts_the_hint_away():
    s, ui = _hinted((1, 8), (2, 6))
    ui.pending.append(Order(1, 0, 1, 5))
    ui.apply_hint(s)
    assert [(o.dest_id, o.ships) for o in ui.pending] == [(1, 8), (2, 6)]
    assert not ui.hint_live(s)


def test_applying_tops_up_a_lane_a_rule_thins():
    """A queued send comes out ahead of the rule, so the rule sends less down the
    lane it was topping up: the apply keeps going until the hint is met."""
    s, ui = _hinted((1, 14))
    ui.auto_forward[0] = ForwardRule({1: 50, 2: 50}, 0)
    ui.apply_hint(s)
    assert ui.planned_sends(s)[(0, 1)] >= 14
    assert sum(ui.planned_sends(s).values()) <= 20


def test_applying_never_takes_ships_promised_elsewhere():
    s, ui = _hinted((1, 15))
    ui.pending.append(Order(1, 0, 2, 12))
    ui.apply_hint(s)
    assert [(o.dest_id, o.ships) for o in ui.pending] == [(2, 12), (1, 8)]


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


# -- the footer ---------------------------------------------------------------- #
def _screen(w: int = config.SCREEN_W, h: int = config.SCREEN_H):
    import pygame
    from starconquest import render
    pygame.init()
    config.SCREEN_W, config.SCREEN_H = w, h
    config.apply_ui_scale(1.0)
    render._FONTS.clear()
    return pygame.display.set_mode((w, h))


def _footer_ui(state):
    from starconquest import mapgen
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    ui.visible = ui.seen = set(state.systems)
    return ui


def _reset_screen():
    import pygame
    from starconquest import render
    config.SCREEN_W, config.SCREEN_H = config.BASE_SCREEN_W, config.BASE_SCREEN_H
    config.apply_ui_scale(1.0)
    render._FONTS.clear()
    pygame.quit()


def test_a_shown_hint_splits_into_hide_and_apply():
    import pygame
    from starconquest import input as game_input, render
    screen = _screen()
    try:
        state, ui, _ = _game()
        ui.view = _footer_ui(state).view
        ui.hint_orders = game_main.hint_orders(state, 1)
        ui.hint_turn = state.turn
        assert ui.hint_left(state)
        render.draw(screen, state, ui)
        hide, apply = pygame.Rect(ui.hint_button_rect), pygame.Rect(ui.hint_apply_rect)
        assert hide.w and apply.w and hide.right < apply.x
        game_input.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, pos=apply.center, button=1), state, ui)
        assert not ui.hint_live(state) and ui.pending
        render.draw(screen, state, ui)
        assert ui.hint_button_rect[2] and not ui.hint_apply_rect[2]
    finally:
        _reset_screen()


def test_clear_outlasts_history_in_a_squeeze():
    """Clear is something to do, History something to look at: a narrowing strip
    folds History away first."""
    from starconquest import mapgen, render
    try:
        for w in range(480, config.BASE_SCREEN_W + 1, 40):
            screen = _screen(w, 700)
            state = mapgen.generate_random(1, num_nodes=18, num_players=3)
            state.turn = 3
            ui = _footer_ui(state)
            render.draw(screen, state, ui)
            if not ui.clear_button_rect[2]:
                assert not ui.history_button_rect[2], w
    finally:
        _reset_screen()
