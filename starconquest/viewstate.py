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
    # standing auto-forward rules: source_id -> (dest_id, keep). Human-only QoL,
    # so it lives here rather than in the pure GameState. Each turn a rule
    # forwards (garrison - keep) ships from source to dest (see main.resolve_turn).
    auto_forward: dict[int, tuple[int, int]] = field(default_factory=dict)
    autoplay: bool = False
    show_help: bool = True
    end_turn_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    # -- ship accounting ---------------------------------------------------- #
    def committed(self, sid: int) -> int:
        """Ships already promised out of a system by queued (not yet run) orders."""
        return sum(o.ships for o in self.pending if o.source_id == sid)

    def available(self, state: GameState, sid: int) -> int:
        """Ships still free to deploy from a system this turn."""
        return state.systems[sid].ships - self.committed(sid)

    # -- selection helpers -------------------------------------------------- #
    def reset_selection(self) -> None:
        self.mode = IDLE
        self.selected = None
        self.dest = None
        self.chosen = 0

    def clear_pending(self) -> None:
        self.pending.clear()

    def clear_forward(self, sid: int) -> None:
        """Remove the standing auto-forward rule out of a system, if any."""
        self.auto_forward.pop(sid, None)
