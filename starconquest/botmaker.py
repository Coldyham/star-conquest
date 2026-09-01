"""The bot maker: a third pygame scene (menu / game / botmaker) for building a
``botlang.Program`` visually — the editor half of the rule language that
``botlang.py`` and ``docs/bot-maker.md`` describe as still unbuilt.

Same split as every other shell scene: ``draw`` never touches the ``Program``
itself (only rebuilds ``BotMakerState``'s per-frame rect cache and scroll
clamp, the same as ``menu.draw`` rebuilding ``MenuState.rects`` or
``render.draw`` rebuilding ``Ui.order_hitboxes``); ``handle_event`` is what
mutates the program, and returns
``"done"`` / ``"cancel"`` / ``None`` for ``main.py`` to act on. Unlike
``menu.py`` this draws at **real** screen resolution with ``config.s()`` rather
than a fixed design canvas — it needs a scrolling list and has no fixed tab
panel to fit inside (see the "cannot be a menu tab" constraint in
``docs/bot-maker.md``) — so it reuses ``render.py``'s measured-layout helpers
(``_btn``, ``_btn_w``, ``_row_h``, ``_wrap``, ``_tap_size``, ``_fonts``, ``_text``)
directly rather than menu's fixed-baseline ones.

Deliberately depends on ``botlang`` alone, not ``ai``: this is a presentation
layer over the rule-language data model, the same shape as ``menu`` depending on
``ai`` only for strategy *discovery*. Registering the finished program as a live
``ai.STRATEGIES`` entry is left to ``main.py``, which already imports both.

Every field on a rule is edited **immediately** (no separate save/cancel per
field, unlike the game's send popup) — the same idiom as the menu's AI-tab
sliders. ``Rule``/``Cond``/``Program`` are frozen dataclasses, so every edit
rebuilds via ``dataclasses.replace`` rather than mutating in place.

No free-text naming here: the working program is auto-named from the seat
(``custom{seat}``). Naming/saving is a step-2 (persistence) concern — this pass
never writes anything to disk, so a typed name would have nowhere to go yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import pygame

from . import config
from . import render
from .botlang import ACTIONS, AMOUNTS, CONDITIONS, STARTERS, Cond, Program, Rule, describe

# Actions that can actually eliminate a rival (expand_neutral only ever targets
# owner_id == 0). engine._check_win ends the game once every non-neutral rival
# is gone, so a program with none of these can never win — the exact trap
# docs/bot-maker.md records blockturtle falling into before gaining one.
_WIN_ACTIONS = frozenset({"attack_best", "attack_weakest", "attack_richest"})

_WARN = (214, 172, 92)
_HL_FILL = (40, 46, 66)
_HL_BORDER = (120, 150, 210)
_BTN_FILL = (30, 34, 48)
_BTN_BORDER = (70, 78, 100)
_PANEL_BG = (18, 20, 30)
_PANEL_BORDER = (40, 44, 60)
_TROUGH = (24, 27, 40)
_DANGER = (200, 96, 104)
_START_FILL = (46, 92, 60)
_START_BORDER = (96, 190, 120)

_ROW_H = 32          # rule-list row pitch (uniform: every row carries the same buttons)
_SLIDER_H = 40        # pitch of a two-line label+track slider
_DROPDOWN_OPT_H = 28  # pitch of one open-dropdown option row


def _has_win_path(program: Program) -> bool:
    """Can this program ever eliminate a rival? See ``_WIN_ACTIONS`` above."""
    return any(r.action in _WIN_ACTIONS for r in program.rules)


@dataclass
class BotMakerState:
    """Transient interaction state (analogous to ``menu.MenuState``/``viewstate.Ui``)."""

    seat: int
    program: Program
    selected: Optional[int] = None  # index into program.rules being edited
    open_dropdown: Optional[str] = None  # "action" | "amount" | "add_condition" | None
    scroll: int = 0  # first rule-list row drawn
    scroll_max: int = 0
    drag_key: Optional[str] = None  # slider currently being dragged
    rects: dict[str, pygame.Rect] = field(default_factory=dict)
    # (lo, hi, step, is_int) per slider key currently drawn, rebuilt every frame —
    # a rule's sliders (margin/amount/condition knobs) depend on the selected
    # action/amount/condition, so there is no static table to key off like the
    # menu's `_SLIDER_SPECS`.
    slider_specs: dict[str, tuple[float, float, float, bool]] = field(default_factory=dict)
    # (index, row, up, down, delete) per drawn rule-list row — each carries its
    # own index, the same reason `Ui.order_hitboxes` does: only a scrolled window
    # is drawn, so a positional mapping would act on the wrong rule.
    rule_rows: list[tuple[int, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect]] = field(
        default_factory=list)
    # The rule-list panel's bounds, for routing the mouse wheel to it. Kept off
    # `rects` deliberately: that dict is scanned for the *first* matching rect on
    # a click, and this panel-sized rect would shadow every smaller button nested
    # inside it (scroll arrows, Add rule) if it were in there too.
    rule_list_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))


def new_state(seat: int, program: Program) -> BotMakerState:
    return BotMakerState(seat=seat, program=program)


# --------------------------------------------------------------------------- #
# Program edits — every widget below funnels into one of these. `Rule`/`Cond`/
# `Program` are frozen, so each rebuilds the tuple(s) it touches rather than
# mutating in place.
# --------------------------------------------------------------------------- #
def _update_rule(bms: BotMakerState, **kwargs) -> None:
    if bms.selected is None or not (0 <= bms.selected < len(bms.program.rules)):
        return
    rules = list(bms.program.rules)
    rules[bms.selected] = replace(rules[bms.selected], **kwargs)
    bms.program = replace(bms.program, rules=tuple(rules))


def _selected_rule(bms: BotMakerState) -> Optional[Rule]:
    if bms.selected is None or not (0 <= bms.selected < len(bms.program.rules)):
        return None
    return bms.program.rules[bms.selected]


def _add_condition(bms: BotMakerState, kind: str) -> None:
    rule = _selected_rule(bms)
    spec = CONDITIONS.get(kind)
    if rule is None or spec is None or any(c.kind == kind for c in rule.when):
        return
    _update_rule(bms, when=rule.when + (Cond(kind, spec.default),))


def _remove_condition(bms: BotMakerState, kind: str) -> None:
    rule = _selected_rule(bms)
    if rule is None:
        return
    _update_rule(bms, when=tuple(c for c in rule.when if c.kind != kind))


def _set_condition_value(bms: BotMakerState, kind: str, value: float) -> None:
    rule = _selected_rule(bms)
    if rule is None:
        return
    when = tuple(replace(c, value=value) if c.kind == kind else c for c in rule.when)
    _update_rule(bms, when=when)


def _set_action(bms: BotMakerState, action: str) -> None:
    spec = ACTIONS.get(action)
    if spec is None:
        return
    # Reset margin to the new action's own default: its meaning (force ratio vs
    # exposure gap) changes with the action, so carrying the old number over
    # would silently misconfigure the rule.
    _update_rule(bms, action=action, margin=spec.default)


def _set_amount(bms: BotMakerState, amount: str) -> None:
    spec = AMOUNTS.get(amount)
    if spec is None:
        return
    _update_rule(bms, amount=amount, amount_value=spec.default)


def _add_rule(bms: BotMakerState) -> None:
    bms.program = replace(bms.program, rules=bms.program.rules + (Rule((), "hold"),))
    bms.selected = len(bms.program.rules) - 1


def _delete_rule(bms: BotMakerState, i: int) -> None:
    rules = list(bms.program.rules)
    if not (0 <= i < len(rules)):
        return
    del rules[i]
    bms.program = replace(bms.program, rules=tuple(rules))
    if bms.selected == i:
        bms.selected = None
    elif bms.selected is not None and bms.selected > i:
        bms.selected -= 1


def _move_rule(bms: BotMakerState, i: int, delta: int) -> None:
    j = i + delta
    rules = list(bms.program.rules)
    if not (0 <= i < len(rules) and 0 <= j < len(rules)):
        return
    rules[i], rules[j] = rules[j], rules[i]
    bms.program = replace(bms.program, rules=tuple(rules))
    if bms.selected == i:
        bms.selected = j
    elif bms.selected == j:
        bms.selected = i


def _load_template(bms: BotMakerState, name: str) -> None:
    template = STARTERS.get(name)
    if template is None:
        return
    bms.program = replace(bms.program, rules=template.rules)
    bms.selected = None


# --------------------------------------------------------------------------- #
# Dropdown option lists — always recomputed from current state rather than
# cached, so there is nothing to keep in sync with `bms.program`.
# --------------------------------------------------------------------------- #
def _action_options() -> list[tuple[str, str]]:
    return [(k, spec.label) for k, spec in ACTIONS.items()]


def _amount_options() -> list[tuple[str, str]]:
    return [(k, spec.label) for k, spec in AMOUNTS.items()]


def _condition_options(rule: Rule) -> list[tuple[str, str]]:
    """Unused ``CONDITIONS`` keys, excluding ``"always"`` — an empty `when`
    already reads as "always" (`botlang.describe_cond`'s own convention), so
    it's never offered as an addable condition alongside real ones."""
    used = {c.kind for c in rule.when}
    return [(k, spec.label) for k, spec in CONDITIONS.items() if k != "always" and k not in used]


def _dropdown_options(bms: BotMakerState, key: str) -> list[tuple[str, str]]:
    if key == "action":
        return _action_options()
    if key == "amount":
        return _amount_options()
    if key == "add_condition":
        rule = _selected_rule(bms)
        return _condition_options(rule) if rule is not None else []
    return []


def _apply_dropdown_choice(bms: BotMakerState, key: str, choice: str) -> None:
    if key == "action":
        _set_action(bms, choice)
    elif key == "amount":
        _set_amount(bms, choice)
    elif key == "add_condition":
        _add_condition(bms, choice)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def draw(surface: pygame.Surface, bms: BotMakerState) -> None:
    surface.fill(config.COLOR_BG)
    bms.rects.clear()
    bms.slider_specs.clear()
    bms.rule_rows = []
    w, h = surface.get_size()
    f = render._fonts()
    pad = config.s(20)

    title = f"Bot Maker — editing {config.player_name(bms.seat)} (Seat {bms.seat})"
    render._text(surface, f["big"], title, config.player_color(bms.seat), topleft=(pad, pad))
    y = pad + f["big"].get_height() + config.s(6)

    y = _draw_templates_row(surface, bms, pad, y)

    if not _has_win_path(bms.program):
        msg = "No rule can capture an enemy system — this bot can never win a game."
        render._text(surface, f["small"], msg, _WARN, topleft=(pad, y))
        y += render._row_h("small")

    footer_h = config.s(56)
    list_top = y + config.s(12)
    list_bottom = h - footer_h - config.s(10)

    col_gap = config.s(24)
    left_w = max(config.s(380), round(w * 0.36))
    left_rect = pygame.Rect(pad, list_top, left_w, max(config.s(80), list_bottom - list_top))
    right_x = left_rect.right + col_gap
    right_h = max(config.s(80), list_bottom - list_top)
    right_rect = pygame.Rect(right_x, list_top, max(config.s(200), w - pad - right_x), right_h)

    _draw_rule_list(surface, bms, left_rect)
    _draw_rule_editor(surface, bms, right_rect)
    _draw_footer(surface, bms, w, h, footer_h)


def _draw_templates_row(surface, bms: BotMakerState, x: int, y: int) -> int:
    f = render._fonts()["small"]
    label = "Start from:"
    render._text(surface, f, label, config.COLOR_TEXT_DIM, topleft=(x, y + config.s(6)))
    cx = x + f.size(label)[0] + config.s(10)
    h = render._tap_size(config.s(26))
    for name in sorted(STARTERS):
        rect = pygame.Rect(cx, y, render._btn_w(f, name, config.s(70)), h)
        render._btn(surface, rect, name, _BTN_FILL, _BTN_BORDER, font=f)
        bms.rects[f"tmpl_{name}"] = rect
        cx = rect.right + config.s(8)
    return y + h + config.s(10)


def _clip_text(surface, font, text: str, color, rect: pygame.Rect) -> None:
    """Left-anchored text clipped to ``rect`` — for the rule list's one-line
    descriptions, which can easily outrun the row (unlike a typed field, there's
    nothing worth showing from the tail end, so this clips the right, not the
    left the way menu._text_field does for in-progress typing)."""
    img = font.render(text, True, color)
    prev = surface.get_clip()
    surface.set_clip(rect)
    surface.blit(img, img.get_rect(midleft=(rect.x, rect.centery)))
    surface.set_clip(prev)


def _draw_rule_list(surface, bms: BotMakerState, rect: pygame.Rect) -> None:
    f = render._fonts()["small"]
    pygame.draw.rect(surface, _PANEL_BG, rect, border_radius=config.s(8))
    pygame.draw.rect(surface, _PANEL_BORDER, rect, config.s(1), border_radius=config.s(8))
    bms.rule_list_rect = rect

    pad = config.s(10)
    x, y = rect.x + pad, rect.y + pad
    row_w = rect.width - 2 * pad
    header = f"Rules ({len(bms.program.rules)})"
    render._text(surface, f, header, config.COLOR_TEXT_DIM, topleft=(x, y))
    hy = y + render._row_h("small")

    arrow = render._tap_size(config.s(18))
    rows_top = hy + config.s(4)
    rows_bottom = rect.bottom - pad - render._tap_size(config.s(28)) - config.s(8)
    row_h = render._tap_size(config.s(_ROW_H))
    visible = max(1, (rows_bottom - rows_top) // row_h)

    n = len(bms.program.rules)
    bms.scroll_max = max(0, n - visible)
    first = max(0, min(bms.scroll, bms.scroll_max))
    bms.scroll = first
    shown = range(first, min(n, first + visible))

    if bms.scroll_max > 0:
        down = pygame.Rect(x + row_w - arrow, y, arrow, arrow)
        up = pygame.Rect(down.x - arrow - config.s(4), y, arrow, arrow)
        _draw_arrow(surface, up, up=True, enabled=first > 0)
        _draw_arrow(surface, down, up=False, enabled=first < bms.scroll_max)
        bms.rects["scroll_up"] = up
        bms.rects["scroll_down"] = down

    ry = rows_top
    for i in shown:
        rule = bms.program.rules[i]
        row = pygame.Rect(x, ry, row_w, row_h - config.s(3))
        selected = bms.selected == i
        pygame.draw.rect(surface, _HL_FILL if selected else _TROUGH, row, border_radius=config.s(5))
        if selected:
            pygame.draw.rect(surface, _HL_BORDER, row, config.s(2), border_radius=config.s(5))

        down_r = pygame.Rect(row.right - arrow, row.y + (row.height - arrow) // 2, arrow, arrow)
        del_r = pygame.Rect(down_r.x - arrow - config.s(4), down_r.y, arrow, arrow)
        up_r = pygame.Rect(del_r.x - arrow - config.s(4), down_r.y, arrow, arrow)
        _draw_arrow(surface, up_r, up=True, enabled=i > 0)
        _draw_arrow(surface, down_r, up=False, enabled=i < n - 1)
        pygame.draw.rect(surface, _DANGER, del_r, config.s(1), border_radius=config.s(4))
        render._text(surface, f, "×", _DANGER, center=del_r.center)

        text_rect = pygame.Rect(row.x + config.s(8), row.y, up_r.x - row.x - config.s(12), row.height)
        _clip_text(surface, f, f"{i + 1}. {describe(rule)}", config.COLOR_TEXT, text_rect)

        bms.rule_rows.append((i, row, up_r, down_r, del_r))
        ry += row_h

    add_h = render._tap_size(config.s(28))
    add_rect = pygame.Rect(x, rect.bottom - pad - add_h, row_w, add_h)
    render._btn(surface, add_rect, "+ Add rule", _BTN_FILL, _BTN_BORDER, font=f)
    bms.rects["add_rule"] = add_rect


def _draw_arrow(surface, rect: pygame.Rect, up: bool, enabled: bool) -> None:
    color = config.COLOR_TEXT if enabled else config.COLOR_TEXT_DIM
    pygame.draw.rect(surface, _BTN_FILL, rect, border_radius=config.s(4))
    pygame.draw.rect(surface, _BTN_BORDER, rect, config.s(1), border_radius=config.s(4))
    cx, cy = rect.center
    s = max(3, rect.width // 4)
    pts = [(cx - s, cy + s // 2), (cx + s, cy + s // 2), (cx, cy - s // 2)] if up else \
          [(cx - s, cy - s // 2), (cx + s, cy - s // 2), (cx, cy + s // 2)]
    pygame.draw.polygon(surface, color, pts)


def _section(surface, title: str, x: int, y: int) -> int:
    render._text(surface, render._fonts()["small"], title.upper(), _HL_BORDER, topleft=(x, y))
    return y + render._row_h("small") + config.s(2)


def _slider(surface, bms: BotMakerState, key: str, label: str, value: float,
            lo: float, hi: float, step: float, is_int: bool, x: int, y: int, width: int) -> int:
    f = render._fonts()["small"]
    vtext = str(int(round(value))) if is_int else f"{value:g}"
    render._text(surface, f, label, config.COLOR_TEXT_DIM, midleft=(x, y + f.get_height() // 2))
    render._text(surface, f, vtext, config.COLOR_TEXT, midright=(x + width, y + f.get_height() // 2))

    cy = y + f.get_height() + config.s(10)
    track_h = config.s(6)
    pygame.draw.rect(surface, _TROUGH, pygame.Rect(x, cy - track_h // 2, width, track_h), border_radius=track_h // 2)
    t = 0.0 if hi <= lo else max(0.0, min(1.0, (value - lo) / (hi - lo)))
    fill_w = int(width * t)
    if fill_w > 0:
        pygame.draw.rect(surface, _HL_BORDER, pygame.Rect(x, cy - track_h // 2, fill_w, track_h), border_radius=track_h // 2)
    hx = config.s(7)
    pygame.draw.circle(surface, config.COLOR_TEXT, (x + fill_w, cy), hx)
    pygame.draw.circle(surface, _HL_BORDER, (x + fill_w, cy), hx, config.s(2))

    hit = pygame.Rect(x, y, width, config.s(_SLIDER_H) - config.s(4))
    bms.rects[key] = hit
    bms.slider_specs[key] = (lo, hi, step, is_int)
    return y + config.s(_SLIDER_H)


def _dropdown(surface, bms: BotMakerState, key: str, current_key: Optional[str], current_label: str,
              options: list[tuple[str, str]], x: int, y: int, width: int) -> int:
    f = render._fonts()["normal"]
    h = render._tap_size(config.s(30))
    trigger = pygame.Rect(x, y, width, h)
    open_ = bms.open_dropdown == key
    pygame.draw.rect(surface, _BTN_FILL, trigger, border_radius=config.s(6))
    pygame.draw.rect(surface, _HL_BORDER if open_ else _BTN_BORDER, trigger, config.s(2), border_radius=config.s(6))
    render._text(surface, f, current_label, config.COLOR_TEXT, midleft=(trigger.x + config.s(10), trigger.centery))
    bms.rects[key] = trigger

    if open_ and options:
        rh = render._tap_size(config.s(_DROPDOWN_OPT_H))
        panel = pygame.Rect(x, trigger.bottom + config.s(2), width, rh * len(options))
        pygame.draw.rect(surface, _PANEL_BG, panel, border_radius=config.s(6))
        pygame.draw.rect(surface, _HL_BORDER, panel, config.s(1), border_radius=config.s(6))
        for i, (opt_key, label) in enumerate(options):
            row = pygame.Rect(x, panel.y + i * rh, width, rh)
            if opt_key == current_key:
                pygame.draw.rect(surface, _HL_FILL, row.inflate(-config.s(4), -config.s(4)), border_radius=config.s(4))
            render._text(surface, f, label, config.COLOR_TEXT, midleft=(row.x + config.s(12), row.centery))
            bms.rects[f"{key}_opt_{i}"] = row
    return trigger.bottom + config.s(10)


def _draw_rule_editor(surface, bms: BotMakerState, rect: pygame.Rect) -> None:
    f = render._fonts()
    pygame.draw.rect(surface, _PANEL_BG, rect, border_radius=config.s(8))
    pygame.draw.rect(surface, _PANEL_BORDER, rect, config.s(1), border_radius=config.s(8))

    rule = _selected_rule(bms)
    pad = config.s(16)
    x, y = rect.x + pad, rect.y + pad
    width = rect.width - 2 * pad

    if rule is None:
        hint = "Select a rule on the left, or add one." if bms.program.rules else \
               "No rules yet — add one, or start from a template above."
        render._text(surface, f["normal"], hint, config.COLOR_TEXT_DIM, topleft=(x, y))
        return

    for line in render._wrap(f["small"], describe(rule), width):
        render._text(surface, f["small"], line, config.COLOR_TEXT_DIM, topleft=(x, y))
        y += render._row_h("small")
    y += config.s(8)

    # -- WHEN ---------------------------------------------------------------- #
    y = _section(surface, "When", x, y)
    for cond in rule.when:
        spec = CONDITIONS.get(cond.kind)
        if spec is None:
            continue
        del_rect = pygame.Rect(x + width - config.s(24), y, config.s(24), config.s(24))
        pygame.draw.rect(surface, _DANGER, del_rect, config.s(1), border_radius=config.s(4))
        render._text(surface, f["small"], "×", _DANGER, center=del_rect.center)
        bms.rects[f"cond_del_{cond.kind}"] = del_rect
        row_w = width - config.s(32)
        if spec.knob is None:
            render._text(surface, f["small"], spec.label, config.COLOR_TEXT, midleft=(x, del_rect.centery))
            y += config.s(28)
        else:
            y = _slider(surface, bms, f"cond_{cond.kind}", f"{spec.label} — {spec.knob}",
                        cond.value, spec.lo, spec.hi, spec.step, spec.is_int, x, y, row_w)

    add_options = _condition_options(rule)
    if add_options or bms.open_dropdown == "add_condition":
        y = _dropdown(surface, bms, "add_condition", None, "+ Condition", add_options, x, y, width)
        if bms.open_dropdown == "add_condition":
            return  # its open option list overlays everything below — draw nothing there

    y += config.s(6)

    # -- THEN ------------------------------------------------------------------ #
    y = _section(surface, "Then", x, y)
    action_spec = ACTIONS.get(rule.action)
    action_label = action_spec.label if action_spec else rule.action
    y = _dropdown(surface, bms, "action", rule.action, action_label, _action_options(), x, y, width)
    if bms.open_dropdown == "action":
        return
    if action_spec is not None and action_spec.margin_label:
        y = _slider(surface, bms, "margin", action_spec.margin_label, rule.margin,
                    action_spec.lo, action_spec.hi, action_spec.step, action_spec.is_int, x, y, width)

    # -- AMOUNT (meaningless for hold, which never reads it) ------------------- #
    if rule.action != "hold":
        y += config.s(6)
        y = _section(surface, "Sending", x, y)
        amount_spec = AMOUNTS.get(rule.amount)
        amount_label = amount_spec.label if amount_spec else rule.amount
        y = _dropdown(surface, bms, "amount", rule.amount, amount_label, _amount_options(), x, y, width)
        if bms.open_dropdown == "amount":
            return
        if amount_spec is not None and amount_spec.knob:
            _slider(surface, bms, "amount_value", amount_spec.knob, rule.amount_value,
                    amount_spec.lo, amount_spec.hi, amount_spec.step, amount_spec.is_int, x, y, width)


def _draw_footer(surface, bms: BotMakerState, w: int, h: int, footer_h: int) -> None:
    f = render._fonts()["normal"]
    y = h - footer_h + config.s(8)
    bh = footer_h - config.s(16)
    done_w = render._btn_w(f, "Use this bot", config.s(160))
    done = pygame.Rect(w - config.s(20) - done_w, y, done_w, bh)
    render._btn(surface, done, "Use this bot", _START_FILL, _START_BORDER, font=f)
    bms.rects["done"] = done

    cancel_w = render._btn_w(f, "Cancel", config.s(120))
    cancel = pygame.Rect(done.x - config.s(10) - cancel_w, y, cancel_w, bh)
    render._btn(surface, cancel, "Cancel", _BTN_FILL, _BTN_BORDER, font=f)
    bms.rects["cancel"] = cancel


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def handle_event(event, bms: BotMakerState) -> Optional[str]:
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return _handle_click(event.pos, bms)
    if event.type == pygame.MOUSEMOTION and bms.drag_key is not None:
        _apply_slider_value(bms, bms.drag_key, event.pos[0])
        return None
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        bms.drag_key = None
        return None
    if event.type == pygame.MOUSEWHEEL:
        if bms.rule_list_rect.collidepoint(pygame.mouse.get_pos()):
            bms.scroll = max(0, min(bms.scroll_max, bms.scroll - event.y))
        return None
    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
        if bms.open_dropdown is not None:
            bms.open_dropdown = None
            return None
        return "cancel"
    return None


def _handle_click(pos, bms: BotMakerState) -> Optional[str]:
    # A dropdown swallows every click until answered: an option choice, its own
    # trigger (toggle closed), or elsewhere (just closes it) — mirrors
    # menu.py's `strategy_open`, except it fully swallows rather than falling
    # through, since an option row can sit over a rule row that must not also
    # react to the same click.
    if bms.open_dropdown is not None:
        _handle_dropdown_click(pos, bms)
        return None

    for idx, row, up_r, down_r, del_r in bms.rule_rows:
        if del_r.collidepoint(pos):
            _delete_rule(bms, idx)
            return None
        if up_r.collidepoint(pos):
            _move_rule(bms, idx, -1)
            return None
        if down_r.collidepoint(pos):
            _move_rule(bms, idx, 1)
            return None
        if row.collidepoint(pos):
            bms.selected = idx
            return None

    hit = next((k for k, r in bms.rects.items() if r.collidepoint(pos)), None)
    if hit is None:
        return None
    if hit == "done":
        return "done"
    if hit == "cancel":
        return "cancel"
    if hit == "add_rule":
        _add_rule(bms)
        return None
    if hit == "scroll_up":
        bms.scroll = max(0, bms.scroll - 1)
        return None
    if hit == "scroll_down":
        bms.scroll = min(bms.scroll_max, bms.scroll + 1)
        return None
    if hit.startswith("tmpl_"):
        _load_template(bms, hit[len("tmpl_"):])
        return None
    if hit.startswith("cond_del_"):
        _remove_condition(bms, hit[len("cond_del_"):])
        return None
    if hit in ("action", "amount", "add_condition"):
        bms.open_dropdown = hit
        return None
    if hit in bms.slider_specs:
        bms.drag_key = hit
        _apply_slider_value(bms, hit, pos[0])
        return None
    return None


def _handle_dropdown_click(pos, bms: BotMakerState) -> None:
    key = bms.open_dropdown
    if key is None:
        return
    for i, (opt_key, _label) in enumerate(_dropdown_options(bms, key)):
        rect = bms.rects.get(f"{key}_opt_{i}")
        if rect is not None and rect.collidepoint(pos):
            _apply_dropdown_choice(bms, key, opt_key)
            break
    bms.open_dropdown = None


def _apply_slider_value(bms: BotMakerState, key: str, x: int) -> None:
    rect = bms.rects.get(key)
    spec = bms.slider_specs.get(key)
    if rect is None or spec is None:
        return
    lo, hi, step, is_int = spec
    t = 0.0 if rect.width == 0 else max(0.0, min(1.0, (x - rect.x) / rect.width))
    raw = lo + t * (hi - lo)
    snapped = max(lo, min(hi, round(raw / step) * step)) if step > 0 else raw
    value = int(round(snapped)) if is_int else round(snapped, 4)
    if key == "margin":
        _update_rule(bms, margin=value)
    elif key == "amount_value":
        _update_rule(bms, amount_value=value)
    elif key.startswith("cond_"):
        _set_condition_value(bms, key[len("cond_"):], value)
