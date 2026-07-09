"""Game-setup values chosen before a match begins.

Pure data — imports no pygame — so it is headlessly testable and serializes
trivially (see the save/import note in the menu design). The menu edits a
``Settings`` in place; this module turns one into a fresh ``GameState``. Kept
distinct from ``viewstate.Ui`` (per-match interaction state) and from
``GameState`` (the simulation itself).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from . import config, mapgen
from .model import GameState

MODES = ("random", "symmetric")


@dataclass
class Settings:
    """A game configuration. ``seed is None`` means "roll a fresh seed at start"."""

    mode: str = "random"
    players: int = config.DEFAULT_PLAYERS
    nodes: int = config.DEFAULT_NODES
    seed: Optional[int] = None
    autoplay: bool = False

    @classmethod
    def defaults(cls) -> "Settings":
        return cls()

    @classmethod
    def from_args(cls, args) -> "Settings":
        """Build from the argparse namespace so CLI flags pre-fill the menu."""
        return cls(
            mode=args.mode,
            players=args.players,
            nodes=args.nodes,
            seed=args.seed,
            autoplay=args.autoplay,
        )

    def min_nodes(self) -> int:
        """Fewest systems this player count allows (mapgen enforces the same floor)."""
        return self.players + 3


def resolve_seed(settings: Settings) -> int:
    """The concrete seed to generate with: the chosen one, or a fresh random."""
    return settings.seed if settings.seed is not None else random.randrange(1_000_000)


def build_state(settings: Settings, seed: int) -> GameState:
    """Generate a game from a settings object and a concrete seed.

    The single place seed/mode/size wiring lives, so both the menu and
    ``--no-menu`` startup go through it. Pass 2 grows this to push global
    balance knobs into ``config`` and assign per-player AI params.
    """
    return mapgen.generate(seed, settings.mode, settings.nodes, settings.players)
