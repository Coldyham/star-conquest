"""Transient view/UI state — everything that is about *interacting* with the
game rather than the game itself. Kept out of GameState so the simulation core
stays pure. Shared by input.py (mutates it) and render.py (reads it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geometry import WorldView
from .model import GameState, Order

# interaction modes
IDLE = "idle"            # nothing selected
SELECTED = "selected"    # a source system is selected, awaiting a destination
# a destination is picked: the send is committed and the on-map popup is open
# to retune / forward / cancel it
CHOOSING = "choosing"


@dataclass
class Ui:
    view: WorldView
    human_id: int = 1
    mode: str = IDLE
    selected: Optional[int] = None      # source system id
    hover: Optional[int] = None         # system under the cursor
    dest: Optional[int] = None          # chosen destination system id
    chosen: int = 0                     # ships the active one-shot send commits
    keep: int = 0                       # ships held back by the active forward rule
    # In CHOOSING the send is already committed: as a one-shot order at
    # `sel_order` (when forward_armed is False, sized by `chosen`) or as the
    # standing rule out of `selected` in `auto_forward` (when forward_armed is
    # True, holding back `keep` and forwarding the rest). The popup edits
    # whichever is live.
    forward_armed: bool = False         # active send is a standing forward rule
    pending: list[Order] = field(default_factory=list)
    sel_order: Optional[int] = None     # index into `pending` being edited, if any
    # standing auto-forward rules: source_id -> (dest_id, keep). Human-only QoL,
    # so it lives here rather than in the pure GameState. Each turn a rule
    # forwards (garrison - keep) ships from source to dest (see main.resolve_turn).
    auto_forward: dict[int, tuple[int, int]] = field(default_factory=dict)
    autoplay: bool = False
    show_help: bool = True
    end_turn_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for the queued-orders panel, rebuilt by render each frame and
    # tested by input (same store-rect-then-test handoff as end_turn_rect).
    # Parallel to `pending`: entry i is (row_rect, delete_rect), each (x,y,w,h).
    order_hitboxes: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = field(
        default_factory=list
    )
    # Hit-rects for the −/+ ship-count buttons flanking the active count label on
    # the map (composing or editing an order). Rebuilt by render each frame; zeroed
    # when no count is being adjusted (same handoff as end_turn_rect).
    minus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    plus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for the send popup's action buttons (CHOOSING mode), rebuilt by
    # render each frame and zeroed otherwise (same handoff as end_turn_rect).
    send_tab_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    forward_tab_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    send_all_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    send_half_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    cancel_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Persistent side-panel button to clear every standing forward rule at once;
    # drawn (and hit-tested) only while any rule exists.
    clear_forward_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    # -- ship accounting ---------------------------------------------------- #
    def committed(self, sid: int) -> int:
        """Ships already promised out of a system by queued (not yet run) orders."""
        return sum(o.ships for o in self.pending if o.source_id == sid)

    def available(self, state: GameState, sid: int) -> int:
        """Ships still free to deploy from a system this turn."""
        return state.systems[sid].ships - self.committed(sid)

    def step_count(self, state: GameState, delta: int) -> None:
        """Nudge the ship count being adjusted by ``delta`` — the shared logic
        behind both the mouse wheel and the on-lane −/+ buttons. Applies to the
        active send (CHOOSING) or the queued order being edited."""
        if self.mode == CHOOSING and self.forward_armed:
            self.set_keep(state, self.keep + delta)          # forward: adjust keep
        elif self.mode == CHOOSING and self.selected is not None:
            self.set_send_count(state, self.chosen + delta)  # send: adjust count
        elif self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            # cap is the order's own ships plus whatever is still free at the source
            o = self.pending[self.sel_order]
            cap = self.available(state, o.source_id) + o.ships
            o.ships = max(1, min(cap, o.ships + delta))

    # -- active send (CHOOSING) --------------------------------------------- #
    def _active_cap(self, state: GameState) -> int:
        """The most ships the active send can commit from ``selected``."""
        if self.selected is None:
            return 0
        if self.forward_armed:
            # a rule forwards (garrison - keep); its ceiling is the whole garrison
            return state.systems[self.selected].ships
        cap = self.available(state, self.selected)
        if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            cap += self.pending[self.sel_order].ships  # the send's own committed ships
        return cap

    def begin_send(self, state: GameState, dest: int, forward: bool = False) -> None:
        """Commit a send-all order to ``dest`` and open the adjust popup. No
        confirmation click: the order is live immediately (retune or cancel it
        via the popup). ``forward`` arms it as a standing rule from the start."""
        if self.selected is None:
            return
        avail = self.available(state, self.selected)
        if avail <= 0:
            return
        self.dest = dest
        self.mode = CHOOSING
        self.forward_armed = False
        self.chosen = avail
        self.keep = 0
        self.pending.append(Order(self.human_id, self.selected, dest, avail))
        self.sel_order = len(self.pending) - 1
        if forward:
            self.toggle_forward(state)

    def set_send_count(self, state: GameState, count: int) -> None:
        """Set the active one-shot send's ship count (clamped) on its order.
        Send-tab only — forwarding is sized by `keep` (see set_keep)."""
        if self.mode != CHOOSING or self.forward_armed or self.selected is None:
            return
        self.chosen = max(1, min(self._active_cap(state), count))
        if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            self.pending[self.sel_order].ships = self.chosen

    def set_keep(self, state: GameState, keep: int) -> None:
        """Set how many ships the active forward rule holds back each turn
        (0 == forward everything). Forward-tab only."""
        if self.mode != CHOOSING or not self.forward_armed or self.selected is None:
            return
        self.keep = max(0, min(state.systems[self.selected].ships, keep))
        self.auto_forward[self.selected] = (self.dest, self.keep)

    def send_all(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state))

    def send_half(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state) // 2)

    def keep_none(self, state: GameState) -> None:
        self.set_keep(state, 0)                      # forward everything

    def keep_half(self, state: GameState) -> None:
        if self.selected is not None:
            self.set_keep(state, state.systems[self.selected].ships // 2)

    def toggle_forward(self, state: GameState) -> None:
        """Flip the active send between a one-shot order (sized by `chosen`) and
        a standing rule that holds back `keep` and forwards the rest each turn.
        Arming defaults to keep 0 (forward everything)."""
        if self.mode != CHOOSING or self.selected is None or self.dest is None:
            return
        self.forward_armed = not self.forward_armed
        if self.forward_armed:
            if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
                del self.pending[self.sel_order]
            self.sel_order = None
            self.keep = 0
            self.auto_forward[self.selected] = (self.dest, self.keep)
        else:
            self.auto_forward.pop(self.selected, None)
            self.chosen = max(1, min(self._active_cap(state), self.chosen))
            self.pending.append(Order(self.human_id, self.selected, self.dest, self.chosen))
            self.sel_order = len(self.pending) - 1

    def set_forward_mode(self, state: GameState, armed: bool) -> None:
        """Switch the popup's Send/Forward tab explicitly (idempotent)."""
        if self.mode == CHOOSING and self.forward_armed != armed:
            self.toggle_forward(state)

    def clear_all_forward(self) -> None:
        """Remove every standing forward rule at once."""
        self.auto_forward.clear()

    def cancel_send(self) -> None:
        """Discard the active send entirely, keeping just the source selected."""
        if self.forward_armed:
            if self.selected is not None:
                self.auto_forward.pop(self.selected, None)
        elif self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            del self.pending[self.sel_order]
        self._close_send()

    def close_send(self) -> None:
        """Leave the popup but keep the committed send (order or rule) in place."""
        self._close_send()

    def _close_send(self) -> None:
        self.sel_order = None
        self.forward_armed = False
        self.dest = None
        self.chosen = 0
        self.keep = 0
        self.mode = SELECTED if self.selected is not None else IDLE

    # -- selection helpers -------------------------------------------------- #
    def reset_selection(self) -> None:
        """Drop all selection/composition state. Committed orders/rules survive
        (they live in `pending`/`auto_forward`), so this only closes the popup."""
        self.mode = IDLE
        self.selected = None
        self.dest = None
        self.chosen = 0
        self.keep = 0
        self.sel_order = None
        self.forward_armed = False

    def select_order(self, i: int) -> None:
        """Pick a queued order to edit (scroll adjusts it, X removes it).

        Editing an existing order and composing a new one are mutually
        exclusive, so this drops any in-progress source/destination selection.
        """
        self.reset_selection()
        self.sel_order = i

    def clear_pending(self) -> None:
        self.pending.clear()
        self.sel_order = None

    def clear_forward(self, sid: int) -> None:
        """Remove the standing auto-forward rule out of a system, if any."""
        self.auto_forward.pop(sid, None)
