"""Game-setup values chosen before a match begins.

Pure data — imports no pygame — so it is headlessly testable and serializes
trivially (see the save/import note in the menu design). The menu edits a
``Settings`` in place; this module turns one into a fresh ``GameState``. Kept
distinct from ``viewstate.Ui`` (per-match interaction state) and from
``GameState`` (the simulation itself).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Optional

from . import config, mapgen
from .model import AiParams, GameState

MODES = ("random", "symmetric")

# (Settings attr, config constant) for every global balance knob the menu tunes.
# `_apply_globals` writes these into the `config` module before generation; since
# config is read live everywhere, that is all it takes for them to bite. This is
# the single source of truth pairing settings fields to config constants.
_GLOBAL_KNOBS = (
    ("node_jitter", "NODE_JITTER"),
    ("relax_min_sep_frac", "RELAX_MIN_SEP_FRAC"),
    ("lloyd_passes", "LLOYD_PASSES"),
    ("extra_edge_fraction", "EXTRA_EDGE_FRACTION"),
    ("max_edge_length_frac", "MAX_EDGE_LENGTH_FRAC"),
    ("ship_ly_per_turn", "SHIP_LY_PER_TURN"),
    ("home_start_ships", "HOME_START_SHIPS"),
    ("home_production", "HOME_PRODUCTION"),
    ("garrison_base", "GARRISON_BASE"),
    ("garrison_k", "GARRISON_K"),
    ("garrison_jitter", "GARRISON_JITTER"),
    ("combat_jitter", "COMBAT_JITTER"),
    ("neutral_produces", "NEUTRAL_PRODUCES"),
)


@dataclass
class Settings:
    """A game configuration. ``seed is None`` means "roll a fresh seed at start"."""

    mode: str = "random"
    players: int = config.DEFAULT_PLAYERS
    nodes: int = config.DEFAULT_NODES
    seed: Optional[int] = None
    autoplay: bool = False

    # -- Advanced: global balance knobs (defaults mirror config) ------------- #
    node_jitter: float = config.NODE_JITTER
    relax_min_sep_frac: float = config.RELAX_MIN_SEP_FRAC
    lloyd_passes: int = config.LLOYD_PASSES
    extra_edge_fraction: float = config.EXTRA_EDGE_FRACTION
    max_edge_length_frac: float = config.MAX_EDGE_LENGTH_FRAC
    ship_ly_per_turn: float = config.SHIP_LY_PER_TURN
    home_start_ships: int = config.HOME_START_SHIPS
    home_production: int = config.HOME_PRODUCTION
    garrison_base: int = config.GARRISON_BASE
    garrison_k: int = config.GARRISON_K
    garrison_jitter: int = config.GARRISON_JITTER
    combat_jitter: float = config.COMBAT_JITTER
    neutral_produces: bool = config.NEUTRAL_PRODUCES

    # -- AI: per-seat tuning, indexed by seat-1 (seats 1..MAX_PLAYERS) -------- #
    ai: list[AiParams] = field(
        default_factory=lambda: [AiParams() for _ in range(config.MAX_PLAYERS)]
    )

    @classmethod
    def defaults(cls) -> "Settings":
        return cls()

    @classmethod
    def from_args(cls, args) -> "Settings":
        """Build from the argparse namespace so CLI flags pre-fill the menu.

        Only the Basic fields come from argv; the Advanced/AI fields keep their
        config-derived defaults.
        """
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

    def seat_params(self, seat: int) -> AiParams:
        """The AiParams for a 1-based seat id (defaults if out of range)."""
        idx = seat - 1
        return self.ai[idx] if 0 <= idx < len(self.ai) else AiParams()


def resolve_seed(settings: Settings) -> int:
    """The concrete seed to generate with: the chosen one, or a fresh random."""
    return settings.seed if settings.seed is not None else random.randrange(1_000_000)


def _apply_globals(settings: Settings) -> None:
    """Push the global balance knobs into the `config` module (the single writer).

    Honours "constants live in config.py": the menu edits a Settings, and this is
    the one place those choices flow back into config, right before generation.
    """
    for attr, const in _GLOBAL_KNOBS:
        setattr(config, const, getattr(settings, attr))


def build_state(settings: Settings, seed: int) -> GameState:
    """Generate a game from a settings object and a concrete seed.

    The single funnel from menu/CLI to a GameState: apply the global knobs, build
    the map, then stamp each non-neutral seat with its own AI params (a copy, so
    later menu edits don't reach into a live game).
    """
    _apply_globals(settings)
    state = mapgen.generate(seed, settings.mode, settings.nodes, settings.players)
    for player in state.players.values():
        if not player.is_neutral:
            player.ai_params = replace(settings.seat_params(player.id))
    return state
