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
    sel_forward: Optional[int] = None   # source id of the rule being edited, if any
    # Fog of war (human-only, so it lives here not in GameState). Recomputed each
    # turn by main.refresh_fog from the human's territory; render reads these.
    #   visible      — systems in full detail this turn (owner + ship counts)
    #   seen         — every system ever perceived; the rest of `seen` (minus
    #                  `visible`) renders as a grey "?" silhouette, memory included
    #   player_intel — last-known (systems, ships, prod) per rival ever sighted,
    #                  for the fogged scoreboard's frozen rows
    visible: set[int] = field(default_factory=set)
    seen: set[int] = field(default_factory=set)
    player_intel: dict[int, tuple[int, int, float]] = field(default_factory=dict)
    autoplay: bool = False
    # Play/pause: while True, main steps turns repeatedly (as if tapping Enter),
    # toggled by P or the play/pause button. Distinct from autoplay, which hands
    # the human seat to the AI; here the human's own orders still run each step.
    playing: bool = False
    show_help: bool = True
    # History mode: a modal review scene for scrubbing back through the recorded
    # match. While active, main draws a reconstructed past board (not the live
    # `state`) and input suppresses board interaction. `history_turn` is the
    # viewed turn (0 == opening position .. `history_max` == latest recorded);
    # `history_max` mirrors the log's turn count so input can map a scrubber
    # click to a turn without needing the log. `history_reveal` lifts fog for a
    # finished game (see behind the fog of war). `dragging_scrubber` tracks a
    # held mouse-drag on the scrubber track.
    history: bool = False
    history_turn: int = 0
    history_max: int = 0
    history_reveal: bool = False
    dragging_scrubber: bool = False
    end_turn_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Play/pause button hit-rect, rebuilt by render each frame (zeroed while
    # autoplay drives turns itself); tested by input, like end_turn_rect.
    play_pause_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
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
    # Send-popup placement. By default render auto-anchors it to the side of the
    # lane that covers the fewest system nodes; once the user drags it, popup_pos
    # pins the top-left (cleared on a new target so auto-placement resumes).
    # popup_rect is the full panel rect (render writes it; input hit-tests it to
    # start a drag on the background — away from the buttons).
    popup_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    popup_pos: Optional[tuple[int, int]] = None
    dragging_popup: bool = False
    popup_drag_off: tuple[int, int] = (0, 0)
    # Drag-to-target gesture (touch-friendly alternative to tap-source-then-tap-
    # dest): a press on an owned system arms `drag_src`; dragging past a threshold
    # sets `drag_active` and `drag_pos` follows the finger; releasing over an
    # adjacent system commits the send/rule. Render draws a line while active.
    drag_src: Optional[int] = None
    drag_active: bool = False
    drag_start: tuple[int, int] = (0, 0)
    drag_pos: tuple[int, int] = (0, 0)
    # Persistent side-panel button to clear every standing forward rule at once;
    # drawn (and hit-tested) only while any rule exists.
    clear_forward_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for auto-forward rule rows, listed below the queued orders:
    # (source_id, row_rect, delete_rect) tuples.
    forward_hitboxes: list[tuple[int, tuple[int, int, int, int], tuple[int, int, int, int]]] = (
        field(default_factory=list)
    )
    # History-mode hit-rects, rebuilt by render each frame and tested by input
    # (same store-rect-then-test handoff as end_turn_rect). `history_button_rect`
    # is the bottom-bar (and game-over overlay) toggle; the others are live only
    # while `history` is on. `scrubber_rect` is the draggable track.
    history_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    scrubber_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    rewind_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    exit_history_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Game-over overlay buttons (touch-reachable equivalents of the R/M keys),
    # rebuilt by render each frame and tested by input like the rects above.
    restart_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    menu_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Live-play bottom-bar buttons that are touch equivalents of keyboard-only
    # actions (A: autoplay, R: new map). menu_button_rect above is shared with
    # the game-over overlay — the two scenes never draw at the same time, so
    # whichever last ran render.draw owns the current value.
    autoplay_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    restart_live_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Quit (Esc) and Clear/cancel (X) touch equivalents. quit_button_rect is
    # shared between the live footer and the game-over overlay, like
    # menu_button_rect above; clear_button_rect is live-play only.
    quit_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    clear_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Camera pan: a press on empty space (no node/lane/button under it) arms
    # this instead of the drag-to-target gesture, so panning and drag-to-send
    # never fight over the same press. `pan_last` is the previous motion-event
    # position, used to turn each MOUSEMOTION into a `view.pan()` delta.
    pan_active: bool = False
    pan_last: tuple[int, int] = (0, 0)
    # On-map camera control hit-rects (reset view, zoom +/-), rebuilt by render
    # each frame and tested by input (same store-rect-then-test handoff as
    # end_turn_rect); live-play only.
    reset_view_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    zoom_minus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    zoom_plus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    # -- ship accounting ---------------------------------------------------- #
    def committed(self, sid: int) -> int:
        """Ships already promised out of a system by queued (not yet run) orders."""
        return sum(o.ships for o in self.pending if o.source_id == sid)

    def available(self, state: GameState, sid: int) -> int:
        """Ships still free to deploy from a system this turn."""
        return state.systems[sid].ships - self.committed(sid)

    def count_adjust_active(self) -> bool:
        """True while the mouse wheel should nudge a ship count (``step_count``
        below would do something) rather than zoom the camera — mirrors
        ``step_count``'s own dispatch conditions exactly, so the two can't
        drift out of sync."""
        return (
            (self.mode == CHOOSING and (self.forward_armed or self.selected is not None))
            or (self.sel_order is not None and 0 <= self.sel_order < len(self.pending))
            or (self.sel_forward is not None and self.sel_forward in self.auto_forward)
        )

    def step_count(self, state: GameState, delta: int) -> None:
        """Nudge the ship count being adjusted by ``delta`` — the shared logic
        behind both the mouse wheel and the on-lane −/+ buttons. Applies to the
        active send (CHOOSING — send count or forward keep), the queued order
        being edited, or the standing auto-forward rule being edited."""
        if not self.count_adjust_active():
            return
        if self.mode == CHOOSING and self.forward_armed:
            self.set_keep(state, self.keep + delta)          # forward: adjust keep
        elif self.mode == CHOOSING and self.selected is not None:
            self.set_send_count(state, self.chosen + delta)  # send: adjust count
        elif self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            # cap is the order's own ships plus whatever is still free at the source
            o = self.pending[self.sel_order]
            cap = self.available(state, o.source_id) + o.ships
            o.ships = max(1, min(cap, o.ships + delta))
        elif self.sel_forward is not None and self.sel_forward in self.auto_forward:
            # adjust a standing rule's `keep` in place; it ranges over the
            # source's whole garrison (0 keeps nothing, all forwards nothing)
            src = self.sel_forward
            dest, keep = self.auto_forward[src]
            cap = state.systems[src].ships if src in state.systems else keep
            self.auto_forward[src] = (dest, max(0, min(cap, keep + delta)))

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
        via the popup). ``forward`` arms it as a standing rule from the start —
        allowed even from an empty system (the rule forwards future production),
        whereas a one-shot send needs ships free right now."""
        if self.selected is None:
            return
        avail = self.available(state, self.selected)
        if avail <= 0 and not forward:
            return
        self.dest = dest
        self.mode = CHOOSING
        self.forward_armed = False
        self.chosen = max(0, avail)
        self.keep = 0
        self.popup_pos = None            # fresh target -> auto-place the popup
        self.dragging_popup = False
        if avail > 0:
            self.pending.append(Order(self.human_id, self.selected, dest, avail))
            self.sel_order = len(self.pending) - 1
        else:
            self.sel_order = None   # nothing to send now; forward-only rule
        if forward:
            self.toggle_forward(state)

    def set_send_count(self, state: GameState, count: int) -> None:
        """Set the active one-shot send's ship count (clamped) on its order.
        Send-tab only — forwarding is sized by `keep` (see set_keep)."""
        if self.mode != CHOOSING or self.forward_armed or self.selected is None:
            return
        cap = self._active_cap(state)
        lo = 1 if cap > 0 else 0          # an empty source sends nothing (0), not 1
        self.chosen = max(lo, min(cap, count))
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
            cap = self._active_cap(state)
            if cap > 0:
                self.chosen = max(1, min(cap, self.chosen))
                self.pending.append(Order(self.human_id, self.selected, self.dest, self.chosen))
                self.sel_order = len(self.pending) - 1
            else:
                self.chosen = 0          # empty source: no one-shot order to queue
                self.sel_order = None

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
        self.popup_pos = None
        self.dragging_popup = False
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
        self.popup_pos = None
        self.dragging_popup = False

    def select_order(self, i: int) -> None:
        """Pick a queued order to edit (scroll adjusts it, X removes it).

        Editing an existing order and composing a new one are mutually
        exclusive, so this drops any in-progress source/destination selection.
        """
        self.reset_selection()
        self.sel_forward = None
        self.sel_order = i

    def select_forward(self, sid: int) -> None:
        """Pick a standing auto-forward rule to edit (scroll adjusts its
        `keep`, X removes it) — same in-place-edit pattern as `select_order`.
        """
        self.reset_selection()
        self.sel_order = None
        self.sel_forward = sid

    def clear_pending(self) -> None:
        self.pending.clear()
        self.sel_order = None

    def clear_forward(self, sid: int) -> None:
        """Remove the standing auto-forward rule out of a system, if any."""
        self.auto_forward.pop(sid, None)
        if self.sel_forward == sid:
            self.sel_forward = None
