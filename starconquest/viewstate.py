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
CHOOSING = "choosing"    # destination picked, adjusting the ship count


@dataclass
class Ui:
    view: WorldView
    human_id: int = 1
    mode: str = IDLE
    selected: Optional[int] = None      # source system id
    hover: Optional[int] = None         # system under the cursor
    dest: Optional[int] = None          # chosen destination system id
    chosen: int = 0                     # ships to send in CHOOSING mode
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
    # Same idea for auto-forward rule rows, listed below the queued orders:
    # (source_id, row_rect, delete_rect) tuples.
    forward_hitboxes: list[tuple[int, tuple[int, int, int, int], tuple[int, int, int, int]]] = (
        field(default_factory=list)
    )

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
        move being composed (CHOOSING), the queued order being edited, or the
        standing auto-forward rule being edited."""
        if self.mode == CHOOSING and self.selected is not None:
            avail = self.available(state, self.selected)
            self.chosen = max(1, min(avail, self.chosen + delta))
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

    # -- selection helpers -------------------------------------------------- #
    def reset_selection(self) -> None:
        self.mode = IDLE
        self.selected = None
        self.dest = None
        self.chosen = 0

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
