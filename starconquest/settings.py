"""Game-setup values chosen before a match begins.

Pure data — imports no pygame — so it is headlessly testable and serializes
trivially: ``to_dict``/``from_dict`` (JSON-friendly) and ``save``/``load`` round
-trip a whole config to a file, and ``copy_from`` overwrites one in place (the
menu's Save/Load buttons use these). The menu edits a ``Settings`` in place; this
module turns one into a fresh ``GameState``. Kept distinct from ``viewstate.Ui``
(per-match interaction state) and from ``GameState`` (the simulation itself).
"""

from __future__ import annotations

import base64
import binascii
import itertools
import json
import os
import random
import time
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Optional

from . import config, mapgen
from .model import AiParams, GameState

MODES = ("random", "symmetric")

# Bumped by every `fresh_rng()` call so two rolls in the same clock tick differ.
_roll_count = itertools.count()

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
    ("fog_sight", "FOG_SIGHT"),
    ("fog_scout", "FOG_SCOUT"),
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
    fog_sight: int = config.FOG_SIGHT
    fog_scout: int = config.FOG_SCOUT

    # -- AI: per-seat tuning, indexed by seat-1 (seats 1..MAX_PLAYERS) -------- #
    ai: list[AiParams] = field(
        default_factory=lambda: [AiParams() for _ in range(config.MAX_PLAYERS)]
    )
    # Per-seat strategy name (key into ai.STRATEGIES); "heuristic" is built in,
    # others come from drop-in files the menu discovers. Same seat-1 indexing.
    ai_strategy: list[str] = field(
        default_factory=lambda: ["heuristic" for _ in range(config.MAX_PLAYERS)]
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

    def seat_strategy(self, seat: int) -> str:
        """The strategy name for a 1-based seat id (heuristic if out of range)."""
        idx = seat - 1
        return self.ai_strategy[idx] if 0 <= idx < len(self.ai_strategy) else "heuristic"

    # -- save / load (see module docstring) ---------------------------------- #
    def to_dict(self) -> dict:
        """A plain, JSON-serialisable dict (``ai`` becomes a list of dicts)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Rebuild from a plain dict (e.g. parsed JSON), tolerantly.

        Unknown keys are ignored, missing keys keep their default, values are
        coerced to each field's type, and structural fields are clamped to the
        same bounds the menu enforces — so a hand-edited or older file still
        loads to a playable config rather than crashing.
        """
        if not isinstance(data, dict):
            return cls()
        out = cls()
        for f in fields(cls):                       # scalar fields
            if f.name in ("ai", "ai_strategy", "seed") or f.name not in data:
                continue
            setattr(out, f.name, _coerce(data[f.name], getattr(out, f.name)))

        if out.mode not in MODES:
            out.mode = "random"
        out.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, out.players))
        out.nodes = max(out.min_nodes(), min(config.MAX_NODES, out.nodes))
        seed = data.get("seed")
        out.seed = int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else None

        raw_ai = data.get("ai")
        if isinstance(raw_ai, list):
            out.ai = [_ai_from_dict(d) for d in raw_ai[: config.MAX_PLAYERS]]
        while len(out.ai) < config.MAX_PLAYERS:     # pad short lists
            out.ai.append(AiParams())

        raw_strat = data.get("ai_strategy")
        if isinstance(raw_strat, list):
            out.ai_strategy = [str(s) for s in raw_strat[: config.MAX_PLAYERS]]
        while len(out.ai_strategy) < config.MAX_PLAYERS:
            out.ai_strategy.append("heuristic")
        return out

    def save(self, path) -> None:
        """Write this config to ``path`` as indented JSON."""
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path) -> "Settings":
        """Read a config from ``path`` (raises on missing/invalid JSON)."""
        with open(path) as fh:
            return cls.from_dict(json.load(fh))

    def to_token(self) -> str:
        """This config as a URL-fragment-safe string (compact JSON, base64url,
        no ``=`` padding) — the payload behind a shareable settings link."""
        raw = json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> "Settings":
        """Rebuild from a ``to_token`` string, tolerantly (via ``from_dict``).

        Raises ``ValueError`` on any malformed token, mirroring ``load``'s
        contract for bad JSON, so callers have one failure mode to catch.
        """
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            return cls.from_dict(json.loads(raw.decode("utf-8")))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"invalid settings token: {e}") from e

    def copy_from(self, other: "Settings") -> None:
        """Overwrite every field from ``other`` in place (copying its lists).

        Lets a caller holding this instance (e.g. ``main.py``) adopt a loaded
        config without rebinding its reference.
        """
        for f in fields(self):
            if f.name == "ai":
                self.ai = [replace(p) for p in other.ai]
            elif f.name == "ai_strategy":
                self.ai_strategy = list(other.ai_strategy)
            else:
                setattr(self, f.name, getattr(other, f.name))


def _coerce(value, default):
    """Best-effort convert a loaded value to the type of ``default``."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        if isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return default


def _ai_from_dict(d) -> AiParams:
    """One seat's AiParams from a dict, coercing each field (defaults elsewhere)."""
    out = AiParams()
    if isinstance(d, dict):
        for f in fields(AiParams):
            if f.name in d:
                setattr(out, f.name, _coerce(d[f.name], getattr(out, f.name)))
    return out


def resolve_seed(settings: Settings) -> int:
    """The concrete seed to generate with: the chosen one, or a fresh random."""
    return settings.seed if settings.seed is not None else random_seed()


def fresh_rng() -> random.Random:
    """A throwaway RNG seeded from live entropy, for the *unreproducible* rolls
    (a new seed, the menu's dice buttons) — never for anything a seed must
    reproduce, which always goes through ``state.rng``.

    Deliberately not the global ``random`` module: the browser build boots from a
    fixed interpreter image and its ``os.urandom`` may be stubbed, so ``random``
    can auto-seed identically on every page load and hand out the same sequence
    of "random" seeds each session. Mixing the wall clock, a per-call counter (in
    case the clock is coarse — browsers deliberately blunt it) and OS entropy
    where there is any keeps every roll distinct on the web as well as desktop.
    """
    mix = time.time_ns() ^ (next(_roll_count) * 0x9E3779B97F4A7C15)
    try:
        mix ^= int.from_bytes(os.urandom(8), "big")
    except Exception:      # no OS entropy source (some sandboxed runtimes)
        pass
    return random.Random(mix)


def random_seed() -> int:
    """A fresh, unpredictable map seed."""
    return fresh_rng().randrange(config.SEED_MAX)


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
    the map, then stamp each non-neutral seat with its own strategy and AI params
    (params a copy, so later menu edits don't reach into a live game).
    """
    _apply_globals(settings)
    state = mapgen.generate(seed, settings.mode, settings.nodes, settings.players)
    for player in state.players.values():
        if not player.is_neutral:
            player.ai_strategy = settings.seat_strategy(player.id)
            player.ai_params = replace(settings.seat_params(player.id))
    return state
