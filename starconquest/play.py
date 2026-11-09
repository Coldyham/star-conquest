"""What a press on the play screen means, apart from where it landed.

The pygame shell (`input`) and the HTML frontend (`session`) both hit-test in
their own pixels, then hand the result here: a system id, the orders and rules
under a lane press (nearest first), a drag's two ends, or a key by its
``KeyboardEvent.key`` name. Every function mutates only the `Ui` and returns an
action string for the caller to act on, or None, as `input` always has.
"""

from __future__ import annotations

import copy

from . import starlore, stats
from .model import GameState
from .viewstate import CHOOSING, IDLE, ROUTING, SELECTED, Ui

# A queued order or a standing rule's lane, as ``("order", index, -1)`` or
# ``("rule", source_id, dest_id)``.
LaneTag = tuple[str, int, int]

# What a press can change that a pinch must be able to put back: the selection
# and the popup, and the orders and rules a tap can create.
PRESS_FIELDS = ("mode", "selected", "dest", "chosen", "sel_order", "sel_forward",
                "forward_armed", "forward_replaced", "editing_existing", "popup_pos",
                "route_sel", "route_dest", "lore_sid", "odds_open")


def press_snapshot(ui: Ui) -> tuple:
    """What `press_restore` needs to undo the press about to be handled. A
    finger reaches the board before a second one turns the touch into a pinch,
    so each shell takes this before a touch press and puts it back on a pinch."""
    return ({name: copy.copy(getattr(ui, name)) for name in PRESS_FIELDS},
            list(ui.pending), copy.deepcopy(ui.auto_forward))


def press_restore(ui: Ui, snapshot: tuple) -> None:
    """Put back the selection, popup, orders and rules from `press_snapshot`."""
    fields, pending, rules = snapshot
    for name, value in fields.items():
        setattr(ui, name, value)
    ui.pending[:] = pending
    ui.auto_forward.clear()
    ui.auto_forward.update(rules)


def open_lore(state: GameState, ui: Ui, sid: int | None) -> None:
    """Put up the star info card for system ``sid``, if the catalogue has its name."""
    if sid in state.systems and state.systems[sid].name in starlore.LORE:
        ui.lore_sid = sid


def tap_system(state: GameState, ui: Ui, node: int, shift: bool = False) -> None:
    """A press on system ``node`` in live play."""
    ui.sel_order = None  # selecting a system is composing, not editing an order/rule
    ui.sel_forward = None
    if ui.selected is not None and ui.mode in (SELECTED, CHOOSING):
        if node == ui.selected:
            ui.reset_selection()  # deselect the source
        elif ui.mode == CHOOSING and node == ui.dest:
            return  # already targeting it; adjust via popup
        elif ui.source(state) is not None and state.are_adjacent(ui.selected, node):
            # commit a send-all to this neighbour and open the popup. Arm it as a
            # forward rule when Shift is held (desktop), when the source has no
            # ships to send right now — forwarding future production is then the
            # only useful action, and it's the mobile-friendly default (no Shift) —
            # or when it already forwards by a rule set up this turn.
            forward = shift or ui.opens_on_forward(state, ui.selected)
            ui.begin_send(state, node, forward=forward)
        else:
            ui.reset_selection()
            ui.selected = node
            ui.mode = SELECTED
        return

    # IDLE
    ui.mode = SELECTED
    ui.selected = node


def lane_pick(ui: Ui, hits: list[LaneTag]) -> LaneTag | None:
    """Which of ``hits`` (everything in reach of the press, nearest first, an
    order before a rule at the same distance) a lane press takes.

    Orders and rules can share one lane (including opposite directions), so when
    more than one is in range a repeat press cycles through them all rather than
    always grabbing the same one — this is the only way in to any of them.
    """
    if not hits:
        return None
    if ui.sel_order is not None:
        current = ("order", ui.sel_order, -1)
    elif ui.mode == CHOOSING and ui.forward_armed and ui.selected is not None and ui.dest is not None:
        current = ("rule", ui.selected, ui.dest)
    else:
        current = None
    if current in hits:  # cycle to the next item on this lane
        return hits[(hits.index(current) + 1) % len(hits)]
    return hits[0]


def tap_lane(state: GameState, ui: Ui, tag: LaneTag) -> None:
    """Open the order or rule ``tag`` names in the popup."""
    if tag[0] == "order":
        ui.edit_order(state, tag[1])
    else:
        ui.edit_forward(state, tag[1], tag[2])


def cancel(ui: Ui) -> None:
    """A press on empty map, Escape, or the popup's Done."""
    if ui.mode == CHOOSING:
        source = ui.selected
        ui.close_send()  # close the popup, keeping the committed send
        # ...and finish with its source, so the next press on its neighbour selects
        # that system instead of sending it a second fleet. It stays lit as a hover,
        # and in the side panel, until the pointer moves or the next tap.
        ui.reset_selection()
        ui.hover = source
    elif ui.sel_forward is not None:
        ui.sel_forward = None  # deselect a highlighted (dormant) auto-forward rule
    else:
        ui.reset_selection()


def drag_send(state: GameState, ui: Ui, src: int | None, target: int | None, shift: bool = False) -> None:
    """Release of a drag from ``src`` over ``target``: if it's an adjacent
    neighbour of the source, open the send/forward popup for that lane (same as
    tapping it)."""
    if src is None or target is None or target == src:
        return
    if not state.are_adjacent(src, target):
        return
    ui.reset_selection()
    ui.sel_forward = None
    ui.selected = src
    ui.mode = SELECTED
    forward = shift or ui.opens_on_forward(state, src)
    ui.begin_send(state, target, forward=forward)


def clear_selected(ui: Ui) -> None:
    """Context-sensitive cancel/clear — shared by the X/Backspace/Delete keys and
    their footer button: discard whatever the popup is editing (a composed send, or
    the queued order / standing rule it was reopened on), else drop a dormant rule
    highlighted without a popup, else drop the selected system's own forward rule.
    A no-op when none of those apply."""
    if ui.mode == CHOOSING:
        ui.cancel_send()
    elif ui.sel_forward is not None:
        ui.clear_forward(ui.sel_forward)
    elif ui.selected is not None:
        ui.clear_forward(ui.selected)


def key(state: GameState, ui: Ui, name: str, shift: bool = False) -> str | None:
    """A key in live play, by its ``KeyboardEvent.key`` name (letters lower case).

    ``"reset_view"`` asks the caller to fit the camera to the map; everything
    else is an action for `main` (or the session) or None.
    """
    if name in ("Enter", " "):
        # A play-by-post turn we have already submitted is not ours to end again:
        # the turn advances when the last seat is in, not when anyone presses a
        # key. Suppressed rather than swallowed further up so every other control
        # — panning, looking, opening history — keeps working while you wait.
        # A spectator has no turn to end at all.
        return None if ui.awaiting_others(state) or ui.spectating else "end_turn"
    if name in ("p", "a"):
        # Play and autoplay both resolve turns on a clock of their own, which a
        # shared match has no room for: its turns advance when the last seat
        # submits. Dead here exactly as their footer buttons are (`render`), so
        # the key and the button cannot disagree about it.
        if ui.in_pbp:
            return None
        return "toggle_play" if name == "p" else "toggle_autoplay"
    if name == "f":
        # main gates this on the human actually being knocked out (see
        # Ui.can_fast_forward); from here it is just another action string.
        return "toggle_fast_forward"
    if name in ("x", "Backspace", "Delete"):
        clear_selected(ui)
        return None
    if name == "n":
        # a stage's seed is pinned: a new map would leave it (Retry stays)
        return None if ui.in_stage else "restart"
    if name == "b" and ui.in_stage:
        ui.briefing_open = True
        return None
    if name == "i":
        open_lore(state, ui, ui.selected if ui.selected is not None else ui.hover)
        return None
    if name == "r":
        return "reset_view"
    if name == "m":
        return "menu"
    if name == "t":
        return "retry" if ui.can_retry else None
    if name == "u":
        return "undo" if ui.can_undo(state) else None
    if name == "h":
        return "toggle_history"
    if name == "g":
        return "toggle_route"
    if name == "k":
        if shift:
            ui.apply_hint(state)   # a no-op unless the hint is up, as its button is
            return None
        # main gates this on `Ui.can_hint`, the same call that draws the button
        return "hint"
    if name == "Escape":
        if ui.mode != IDLE or ui.sel_order is not None or ui.sel_forward is not None:
            cancel(ui)
            return None
        return "menu"
    return None


def over_key(state: GameState, ui: Ui, name: str) -> str | None:
    """A key on the game-over veil: retry, a new map (or the next stage),
    the menu, history, and the two ways to share."""
    if name == "t":
        return "retry"
    if name == "n":
        # In a stage N is Next stage, or nothing after the last one.
        if ui.in_stage:
            return "next_stage" if ui.offers_next_stage(state) else None
        return "restart"
    if name in ("m", "Escape"):
        return "menu"
    if name == "h":
        return "toggle_history"
    if name == "c":
        return "share"
    if name == "l":
        return "leaderboard"
    return None


def route_clear(state: GameState, ui: Ui) -> None:
    """Clear route mode's group but stay in the mode, as Clear does elsewhere."""
    ui.route_sel = set()
    ui.route_dest = None
    ui.route_press = False
    ui.route_box = False
    ui.recompute_route(state)


def route_key(state: GameState, ui: Ui, name: str) -> str | None:
    """Keys in route mode. Enter confirms rather than ending the turn — the turn
    can't be ended from here at all, so the most obvious key keeps pointing at
    the most obvious action."""
    if name in ("Enter", " "):
        if ui.route_plan:
            ui.confirm_route(state)
        return None
    if name in ("g", "Escape"):
        ui.reset_route()
        return None
    if name == "Tab":
        # switch sub-mode, the keyboard half of the footer's Mode button
        ui.set_route_rally(state, not ui.route_rally)
        return None
    if name == "t" and ui.route_rally:
        ui.auto_rally(state)
        return None
    if name in ("x", "Backspace", "Delete"):
        route_clear(state, ui)
        return None
    if name == "m":
        return "menu"
    if name == "n":
        return None if ui.in_stage else "restart"
    if name == "r":
        return "reset_view"
    return None


def tap(state: GameState, ui: Ui, node: int, shift: bool = False) -> None:
    """A press on system ``node`` in whichever scene is up: route mode's pick,
    else live play's."""
    if ui.mode == ROUTING:
        ui.route_tap(state, node)
    else:
        tap_system(state, ui, node, shift)


def history_key(state: GameState, ui: Ui, name: str, shift: bool = False) -> str | None:
    """A key in history review: scrub the turn, play it back, the graphs and the
    coach, or leave. ``"reset_view"`` asks the caller to fit the camera."""
    if name in ("Escape", "h"):
        return "toggle_history"
    if name == "p":
        return "toggle_play"  # play/pause the replay at sim speed
    if name == "r":
        return "reset_view"   # as in live play: the camera is free here too
    if name == "g" and ui.history_chart is not None:
        ui.history_graph = not ui.history_graph
        ui.dragging_graph = False
        return None
    if name == "Tab" and ui.history_graph:
        # the chart tabs, then Coach (when there is one), round and round
        step = -1 if shift else 1
        n = len(stats.METRICS) + (ui.coach_review is not None)
        at = len(stats.METRICS) if ui.coach_open else ui.graph_metric % len(stats.METRICS)
        at = (at + step) % n
        ui.coach_open = at == len(stats.METRICS)
        if not ui.coach_open:
            ui.graph_metric = at
        return None
    if name in ("ArrowUp", "ArrowDown") and ui.history_graph and ui.coach_open:
        ui.select_note(ui.coach_sel + (1 if name == "ArrowDown" else -1)
                       if ui.coach_sel >= 0 else 0)
        return None
    if name == "f" and ui.history_graph:
        ui.graph_full = not ui.graph_full
        return None
    if ui.history_graph and not ui.coach_open and len(name) == 1 and "1" <= name <= "9":
        # the legend's rows, top to bottom
        seats = ui.graph_seats()
        n = int(name) - 1
        if n < len(seats):
            ui.toggle_trace(seats[n])
        return None
    turn = {"ArrowLeft": ui.history_turn - 1, "ArrowRight": ui.history_turn + 1,
            "Home": 0, "End": ui.history_max}.get(name)
    if turn is not None:
        seek(ui, turn)
    return None


def graph_tab(ui: Ui, i: int) -> None:
    """Pick the graphs' tab ``i``: a metric, or Coach after them."""
    ui.coach_open = ui.coach_review is not None and i >= len(stats.METRICS)
    if not ui.coach_open:
        ui.graph_metric = i % len(stats.METRICS)


def seek(ui: Ui, turn: int) -> None:
    """Show history turn ``turn`` (clamped): a jump, not a step, so playback
    stops and the marks of the turn left behind go."""
    ui.history_turn = max(0, min(ui.history_max, turn))
    ui.playing = False
    ui.clear_fading_marks()   # jumping, not stepping: they'd be stale here


def card_up(state: GameState, ui: Ui) -> bool:
    """Is a card over the board that any press puts away: a star's info card,
    or the forecast's breakdown?"""
    if not ui.odds_card_up():
        ui.odds_open = False
    lore = ui.lore_sid is not None and not ui.history and state.winner is None
    return lore or ui.odds_open


def dismiss_card(ui: Ui) -> None:
    """Put away whichever card is up; the press that does it is spent on it."""
    ui.lore_sid = None
    ui.odds_open = False


def press_key(state: GameState, ui: Ui, name: str, shift: bool = False) -> str | None:
    """A key in whichever scene is up: a card (which any key puts away),
    history, the game-over veil, route mode, or live play."""
    if card_up(state, ui):
        dismiss_card(ui)
        return None
    if ui.history:
        return history_key(state, ui, name, shift)
    if state.winner is not None:
        return over_key(state, ui, name)
    if ui.mode == ROUTING:
        return route_key(state, ui, name)
    return key(state, ui, name, shift)
