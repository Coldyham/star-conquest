"""What a press on the play screen means, apart from where it landed.

The pygame shell (`input`) and the HTML frontend (`session`) both hit-test in
their own pixels, then hand the result here: a system id, the orders and rules
under a lane press (nearest first), a drag's two ends, or a key by its
``KeyboardEvent.key`` name. Every function mutates only the `Ui` and returns an
action string for the caller to act on, or None, as `input` always has.
"""

from __future__ import annotations

from . import starlore
from .model import GameState
from .viewstate import CHOOSING, IDLE, SELECTED, Ui

# A queued order or a standing rule's lane, as ``("order", index, -1)`` or
# ``("rule", source_id, dest_id)``.
LaneTag = tuple[str, int, int]


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
