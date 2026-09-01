"""Drive the bot maker headlessly with synthetic pygame events.

Mirrors test_menu.py: SDL dummy drivers, no real window. Widgets store their
rects during draw(), so each test draws first, then clicks a rect's centre.
"""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from starconquest import botmaker, config, render  # noqa: E402
from starconquest.botlang import ACTIONS, AMOUNTS, CONDITIONS, STARTERS, Cond, Program, Rule  # noqa: E402


def _setup():
    pygame.init()
    render._FONTS.clear()  # rebuild fonts under this session (an earlier test quit)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    bms = botmaker.new_state(2, Program("custom2", ()))
    return screen, bms


def _click_key(screen, bms, key):
    """Draw (to lay out rects), then left-click the centre of widget `key`."""
    botmaker.draw(screen, bms)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=bms.rects[key].center, button=1)
    return botmaker.handle_event(ev, bms)


def _click_option(screen, bms, dropdown_key, option_key):
    """Open `dropdown_key`, redraw so its options exist, then click `option_key`."""
    _click_key(screen, bms, dropdown_key)
    assert bms.open_dropdown == dropdown_key
    botmaker.draw(screen, bms)
    options = botmaker._dropdown_options(bms, dropdown_key)
    idx = next(i for i, (k, _label) in enumerate(options) if k == option_key)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                            pos=bms.rects[f"{dropdown_key}_opt_{idx}"].center, button=1)
    return botmaker.handle_event(ev, bms)


def _row_rect(screen, bms, index, which):
    """`which` in {'row', 'up', 'down', 'del'} for the drawn row at `index`."""
    botmaker.draw(screen, bms)
    for idx, row, up_r, down_r, del_r in bms.rule_rows:
        if idx == index:
            return {"row": row, "up": up_r, "down": down_r, "del": del_r}[which]
    raise AssertionError(f"rule row {index} was not drawn")


def _click_row(screen, bms, index, which):
    rect = _row_rect(screen, bms, index, which)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1)
    return botmaker.handle_event(ev, bms)


# --------------------------------------------------------------------------- #
# Rule list: add / select / reorder / delete
# --------------------------------------------------------------------------- #
def test_add_rule_appends_a_hold_and_selects_it():
    screen, bms = _setup()
    _click_key(screen, bms, "add_rule")
    assert bms.program.rules == (Rule((), "hold"),)
    assert bms.selected == 0


def test_click_row_selects_it():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"), Rule((), "hold")))
    _click_row(screen, bms, 1, "row")
    assert bms.selected == 1


def test_move_rule_up_swaps_with_the_previous_row_and_follows_selection():
    screen, bms = _setup()
    r0, r1 = Rule((), "attack_best"), Rule((), "hold")
    bms.program = replace(bms.program, rules=(r0, r1))
    bms.selected = 1
    _click_row(screen, bms, 1, "up")
    assert bms.program.rules == (r1, r0)
    assert bms.selected == 0


def test_move_rule_down_swaps_with_the_next_row():
    screen, bms = _setup()
    r0, r1 = Rule((), "attack_best"), Rule((), "hold")
    bms.program = replace(bms.program, rules=(r0, r1))
    _click_row(screen, bms, 0, "down")
    assert bms.program.rules == (r1, r0)


def test_delete_rule_removes_it_and_clears_selection():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"),))
    bms.selected = 0
    _click_row(screen, bms, 0, "del")
    assert bms.program.rules == ()
    assert bms.selected is None


def test_deleting_a_row_before_the_selection_shifts_it_down():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"), Rule((), "attack_best")))
    bms.selected = 1
    _click_row(screen, bms, 0, "del")
    assert bms.program.rules == (Rule((), "attack_best"),)
    assert bms.selected == 0


def test_rule_list_scrolls_when_it_overflows():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=tuple(Rule((), "hold") for _ in range(40)))
    botmaker.draw(screen, bms)
    assert bms.scroll_max > 0
    _click_key(screen, bms, "scroll_down")
    assert bms.scroll == 1


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #
def test_condition_options_exclude_always_and_already_used_kinds():
    rule = Rule((Cond("frontier"),), "hold")
    keys = [k for k, _label in botmaker._condition_options(rule)]
    assert "always" not in keys
    assert "frontier" not in keys
    assert "threatened" in keys


def test_add_condition_uses_its_spec_default_value():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"),))
    bms.selected = 0
    _click_option(screen, bms, "add_condition", "ships_at_least")
    when = bms.program.rules[0].when
    assert len(when) == 1
    assert when[0].kind == "ships_at_least"
    assert when[0].value == CONDITIONS["ships_at_least"].default


def test_remove_condition_via_its_x_button():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((Cond("frontier"),), "hold"),))
    bms.selected = 0
    _click_key(screen, bms, "cond_del_frontier")
    assert bms.program.rules[0].when == ()


def test_condition_knob_slider_edits_only_that_condition():
    screen, bms = _setup()
    rule = Rule((Cond("frontier"), Cond("ships_at_least", 5)), "hold")
    bms.program = replace(bms.program, rules=(rule,))
    bms.selected = 0
    botmaker.draw(screen, bms)
    rect = bms.rects["cond_ships_at_least"]
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(rect.x, rect.centery), button=1)
    botmaker.handle_event(ev, bms)
    when = {c.kind: c for c in bms.program.rules[0].when}
    assert when["frontier"].value == 0.0  # untouched
    assert when["ships_at_least"].value == CONDITIONS["ships_at_least"].lo


# --------------------------------------------------------------------------- #
# Action / amount
# --------------------------------------------------------------------------- #
def test_selecting_an_attack_action_resets_margin_to_its_own_default():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"),))
    bms.selected = 0
    _click_option(screen, bms, "action", "attack_best")
    rule = bms.program.rules[0]
    assert rule.action == "attack_best"
    assert rule.margin == ACTIONS["attack_best"].default
    botmaker.draw(screen, bms)
    assert "margin" in bms.slider_specs
    assert "amount" in bms.rects  # non-hold actions show the amount section


def test_hold_action_hides_margin_and_amount_widgets():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "attack_best", 1.4, "surplus"),))
    bms.selected = 0
    botmaker.draw(screen, bms)
    assert "amount" in bms.rects
    assert "margin" in bms.slider_specs

    _click_option(screen, bms, "action", "hold")
    botmaker.draw(screen, bms)
    assert bms.program.rules[0].action == "hold"
    assert "amount" not in bms.rects
    assert "margin" not in bms.slider_specs


def test_selecting_an_amount_with_a_knob_shows_its_slider():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "attack_best", 1.4, "surplus"),))
    bms.selected = 0
    _click_option(screen, bms, "amount", "all_but")
    rule = bms.program.rules[0]
    assert rule.amount == "all_but"
    assert rule.amount_value == AMOUNTS["all_but"].default
    botmaker.draw(screen, bms)
    assert "amount_value" in bms.slider_specs


def test_margin_slider_drag_clamps_to_range_and_snaps_to_step():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "attack_best", 1.4, "surplus"),))
    bms.selected = 0
    botmaker.draw(screen, bms)
    rect = bms.rects["margin"]
    spec = ACTIONS["attack_best"]

    press = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(rect.x, rect.centery), button=1)
    botmaker.handle_event(press, bms)
    assert bms.program.rules[0].margin == spec.lo
    assert bms.drag_key == "margin"

    drag = pygame.event.Event(pygame.MOUSEMOTION, pos=(rect.x + rect.width * 2, rect.centery))
    botmaker.handle_event(drag, bms)
    assert bms.program.rules[0].margin == spec.hi  # dragged past the end -> clamps

    release = pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(rect.right, rect.centery), button=1)
    botmaker.handle_event(release, bms)
    assert bms.drag_key is None


# --------------------------------------------------------------------------- #
# Templates, win-path warning, done/cancel
# --------------------------------------------------------------------------- #
def test_template_button_loads_starter_rules_but_keeps_its_own_name():
    screen, bms = _setup()
    _click_key(screen, bms, "tmpl_blockturtle")
    assert bms.program.rules == STARTERS["blockturtle"].rules
    assert bms.program.name == "custom2"
    assert bms.selected is None


def test_has_win_path_requires_an_attack_action():
    no_attack = Program("x", (Rule((), "hold"), Rule((), "expand_neutral")))
    with_attack = Program("x", (Rule((), "attack_weakest"),))
    assert not botmaker._has_win_path(no_attack)
    assert botmaker._has_win_path(with_attack)


def test_done_and_cancel_return_their_action_strings():
    screen, bms = _setup()
    assert _click_key(screen, bms, "done") == "done"
    assert _click_key(screen, bms, "cancel") == "cancel"


def test_escape_closes_an_open_dropdown_before_cancelling():
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "hold"),))
    bms.selected = 0
    _click_key(screen, bms, "action")
    assert bms.open_dropdown == "action"

    esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
    assert botmaker.handle_event(esc, bms) is None
    assert bms.open_dropdown is None

    assert botmaker.handle_event(esc, bms) == "cancel"


def test_a_click_inside_an_open_dropdown_never_reaches_a_rule_row_underneath():
    """The rule list is drawn on the left of the open dropdown's own panel, but
    the swallow-everything-while-open rule (see `_handle_click`) must still hold
    even for a click that lands over the list rather than the dropdown."""
    screen, bms = _setup()
    bms.program = replace(bms.program, rules=(Rule((), "attack_best"), Rule((), "hold")))
    bms.selected = 0
    _click_key(screen, bms, "action")
    row = _row_rect(screen, bms, 1, "row")
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=row.center, button=1)
    botmaker.handle_event(ev, bms)
    assert bms.open_dropdown is None  # closed by the click...
    assert bms.selected == 0          # ...but did not also select row 1
