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
import hashlib
import itertools
import json
import os
import random
import time
import zlib
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
    ("ship_speed_growth_pct", "SHIP_SPEED_GROWTH_PCT"),
    ("home_start_ships", "HOME_START_SHIPS"),
    ("home_production", "HOME_PRODUCTION"),
    ("garrison_base", "GARRISON_BASE"),
    ("garrison_k", "GARRISON_K"),
    ("garrison_jitter", "GARRISON_JITTER"),
    ("combat_jitter", "COMBAT_JITTER"),
    ("defender_advantage", "DEFENDER_ADVANTAGE"),
    ("neutral_produces", "NEUTRAL_PRODUCES"),
    ("in_lane_battles", "IN_LANE_BATTLES"),
    ("fog_sight", "FOG_SIGHT"),
    ("fog_scout", "FOG_SCOUT"),
)


# Token fields always emitted, even at their default value. Everything else is
# pruned and inferred by the reader, but these four *are* the identity of the
# match: pruning makes a token depend on the reader's defaults, so if a
# `config.DEFAULT_*` ever changed, a pruned link would silently describe a
# different map. `autoplay` is deliberately not here and not part of
# `challenge_key` either — it is a play-style preference, not the setup.
_TOKEN_ALWAYS = ("mode", "players", "nodes", "seed")


# Setup fields absent from older versions of `Settings`: one entry per schema
# change, newest first and cumulative (the oldest lacked the most).
# `challenge_keys` re-hashes without each, recovering the checksum that version
# would have stamped. Append here whenever a field joins `Settings` —
# `test_challenge_key_is_stable` fails until you do.
#
# Only *top-level* fields can be named here. A field joining `AiParams`, or one
# of its defaults moving, changes every seat dict inside `ai` and so moves the
# digest with nothing to drop — and `token_dict` prunes a seat dict only whole,
# never field by field, so the shared link changes shape too and the
# leaderboard's own fallback (`submit.findTwin`, which matches a stored setup
# byte for byte) cannot fold it either. The pinned test still fires; the repair
# is a one-off back-fill of the new key into stored `settings_json`, not an
# entry below. Worth knowing before adding a per-bot knob here rather than
# through `AiParams.aux`, which every seat dict already carries.
_LEGACY_KEY_DROPS: tuple[tuple[str, ...], ...] = (
    ("in_lane_battles",),
    ("in_lane_battles", "defender_advantage"),
)


def _hash_setup(data: dict) -> str:
    """The digest behind ``Settings.challenge_key`` — a setup dict, minus its
    score, canonicalised and hashed."""
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.blake2s(raw, digest_size=8).hexdigest()


@dataclass
class Challenge:
    """A result to beat, riding along with the config that produced it.

    Attached to a shared link so the recipient plays the identical setup and is
    told the target. ``hand`` is how many turns the sender actually decided
    themselves (the rest were autoplayed) — disclosed rather than disqualifying,
    since flipping autoplay on to skip the cleanup of a decided game is normal
    play. Score is ``turns`` first, fewest ``lost`` breaking a tie.
    """

    turns: int = 0
    lost: int = 0
    hand: int = 0
    by: str = ""
    # `challenge_key()` of the setup this was scored on. Redundant by
    # construction, and that is the point: it is a checksum letting the menu spot
    # that the config has since been edited, so the target no longer compares.
    key: str = ""

    def summary(self) -> str:
        """One line: the target, as shown on the menu banner and win overlay."""
        out = f"{self.turns} turns / {self.lost} ships lost"
        if self.hand < self.turns:
            out += f" ({self.hand} by hand)"
        return out

    def matches(self, settings: "Settings") -> bool:
        """True if ``settings`` is still the setup this score was made on.

        A blank ``key`` is taken on trust — a hand-written or pre-``key`` challenge
        should read as valid rather than permanently "edited". Any of
        ``challenge_keys()`` counts, so a link stamped before a knob was added
        still reads as the setup it describes rather than as an edit.
        """
        return not self.key or self.key in settings.challenge_keys()


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
    ship_speed_growth_pct: float = config.SHIP_SPEED_GROWTH_PCT
    home_start_ships: int = config.HOME_START_SHIPS
    home_production: int = config.HOME_PRODUCTION
    garrison_base: int = config.GARRISON_BASE
    garrison_k: int = config.GARRISON_K
    garrison_jitter: int = config.GARRISON_JITTER
    combat_jitter: float = config.COMBAT_JITTER
    defender_advantage: float = config.DEFENDER_ADVANTAGE
    neutral_produces: bool = config.NEUTRAL_PRODUCES
    in_lane_battles: bool = config.IN_LANE_BATTLES
    fog_sight: int = config.FOG_SIGHT
    fog_scout: int = config.FOG_SCOUT

    # -- AI: per-seat tuning, indexed by seat-1 (seats 1..MAX_PLAYERS) -------- #
    ai: list[AiParams] = field(default_factory=lambda: [AiParams() for _ in range(config.MAX_PLAYERS)])
    # Per-seat strategy name (key into ai.STRATEGIES); "heuristic" is built in,
    # others come from drop-in files the menu discovers. Same seat-1 indexing.
    ai_strategy: list[str] = field(default_factory=lambda: ["heuristic" for _ in range(config.MAX_PLAYERS)])

    # -- A score to beat on this exact setup, or None for an ordinary config --- #
    # Presentation context only: `build_state` ignores it, and it is excluded from
    # `challenge_key` so a config and the same config-plus-a-target agree.
    challenge: Optional[Challenge] = None

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
        for f in fields(cls):  # scalar fields
            if f.name in _STRUCTURED or f.name not in data:
                continue
            setattr(out, f.name, _coerce(data[f.name], getattr(out, f.name)))

        if out.mode not in MODES:
            out.mode = "random"
        out.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, out.players))
        out.nodes = max(out.min_nodes(), min(config.MAX_NODES, out.nodes))
        # Not cosmetic like the other knobs' ranges: above this the map cannot be
        # conquered at all, so a hand-edited or pre-cap file is clamped rather
        # than loaded into an unwinnable game.
        out.defender_advantage = max(0.0, min(config.DEFENDER_ADVANTAGE_MAX, out.defender_advantage))
        seed = data.get("seed")
        out.seed = int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else None

        raw_ai = data.get("ai")
        if isinstance(raw_ai, list):
            out.ai = [_ai_from_dict(d) for d in raw_ai[: config.MAX_PLAYERS]]
        while len(out.ai) < config.MAX_PLAYERS:  # pad short lists
            out.ai.append(AiParams())

        raw_strat = data.get("ai_strategy")
        if isinstance(raw_strat, list):
            out.ai_strategy = [str(s) for s in raw_strat[: config.MAX_PLAYERS]]
        while len(out.ai_strategy) < config.MAX_PLAYERS:
            out.ai_strategy.append("heuristic")

        out.challenge = _challenge_from_dict(data.get("challenge"))
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

    def token_dict(self) -> dict:
        """``to_dict`` minus everything the reader would infer anyway.

        ``from_dict`` already defaults, clamps, truncates and pads, so *omitting* a
        key is a supported input — which makes pruning free on the reading side and
        keeps old full-fat tokens decoding unchanged. The full dict is 25 fields
        dominated by six ``AiParams`` and 15 float knobs that are nearly always at
        their defaults, so this is where most of a link's length goes.

        Note the per-seat lists can only be trimmed from the *end* (they are
        positional, indexed by seat-1), and a seat's ``ai`` is never dropped merely
        because its strategy is a drop-in rather than the built-in heuristic:
        ``ai_params`` is a documented readable field for custom bots, so their
        params may well be load-bearing. Default-equality is the only safe test.
        """
        full = self.to_dict()
        blank = Settings()
        out = {k: full[k] for k in _TOKEN_ALWAYS}
        for f in fields(self):
            if f.name in _TOKEN_ALWAYS or f.name in _STRUCTURED:
                continue
            if full[f.name] != getattr(blank, f.name):
                out[f.name] = full[f.name]

        seats = self.players  # seats beyond the player count
        ai = _trim_trailing(full["ai"][:seats], asdict(AiParams()))
        if ai:
            out["ai"] = ai
        strategies = _trim_trailing(full["ai_strategy"][:seats], "heuristic")
        if strategies:
            out["ai_strategy"] = strategies
        if self.challenge is not None:
            out["challenge"] = full["challenge"]
        return out

    def to_token(self) -> str:
        """This config as a URL-fragment-safe string — the payload behind a
        shareable settings link. Pruned (``token_dict``), deflated, then base64url
        with the ``=`` padding stripped."""
        raw = json.dumps(self.token_dict(), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> "Settings":
        """Rebuild from a ``to_token`` string, tolerantly (via ``from_dict``).

        Reads both the current deflated form and the original uncompressed one, so
        links shared before compression still work: plain JSON always starts ``{``,
        which zlib output never does.

        Raises ``ValueError`` on any malformed token, mirroring ``load``'s
        contract for bad JSON, so callers have one failure mode to catch.
        """
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            if raw[:1] != b"{":
                raw = zlib.decompress(raw)
            return cls.from_dict(json.loads(raw.decode("utf-8")))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, zlib.error, ValueError) as e:
            raise ValueError(f"invalid settings token: {e}") from e

    def without_challenge(self) -> "Settings":
        """A copy of this config with any attached score dropped.

        What gets *stored* and put in the address bar: a remembered challenge token
        would be read back at the next launch and its banner would haunt every
        later session. The score travels by clipboard instead (``webstore.copy_link``).
        """
        plain = Settings()
        plain.copy_from(self)
        plain.challenge = None
        return plain

    def challenge_key(self) -> str:
        """A stable id for "this exact setup", ignoring any attached score.

        Hashes the *full* dict, never the pruned token form, so two people whose
        links happened to omit different defaults still agree. ``challenge`` and
        ``autoplay`` are excluded: a config and the same config carrying a target
        are the same match, and whether you let the AI drive is disclosed by
        ``Challenge.hand`` instead. A seat beyond ``players`` is blanked first —
        see ``challenge_keys``.

        This is the one to *write* — the canonical id for a setup as this version
        describes it. To *test* a key that arrived from elsewhere, use
        ``challenge_keys``.
        """
        return self.challenge_keys()[0]

    def challenge_keys(self) -> tuple[str, ...]:
        """``challenge_key()``, then the same setup as older versions hashed it.

        Adding a field to ``Settings`` changes the digest of every setup, so a
        score stamped before that field existed reads as scored on some other map
        unless the older digests are recovered too — one per ``_LEGACY_KEY_DROPS``
        entry whose fields are all still at their defaults. Deduplicated and
        current-first, so ``[0]`` is the canonical key.
        """
        data = self.to_dict()
        for skip in ("challenge", "autoplay"):
            data.pop(skip, None)
        # A seat beyond `players` is inert, and `token_dict` truncates `ai`/
        # `ai_strategy` there — so it never travels in a shared link, and a
        # decode always pads it back with fresh defaults. Blank it here the same
        # way (rather than dropping it, which would change the setup's shape for
        # every *other* setup too): otherwise a sender who once configured more
        # seats, then dialed `players` back down, stamps a key over leftover AI
        # settings a recipient's decode can never reproduce, and the link fails
        # to match itself.
        data["ai"] = data["ai"][: self.players] + [asdict(AiParams())] * (len(data["ai"]) - self.players)
        data["ai_strategy"] = data["ai_strategy"][: self.players] + ["heuristic"] * (
            len(data["ai_strategy"]) - self.players
        )
        keys = [_hash_setup(data)]
        blank = Settings().to_dict()
        for drops in _LEGACY_KEY_DROPS:
            # A version without a field could not express a non-default value for
            # it, so once one is moved that version cannot be describing this setup
            # — and dropping it would blind the checksum to the edit.
            if any(data.get(f) != blank.get(f) for f in drops):
                continue
            key = _hash_setup({k: v for k, v in data.items() if k not in drops})
            if key not in keys:
                keys.append(key)
        return tuple(keys)

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
            elif f.name == "challenge":
                self.challenge = replace(other.challenge) if other.challenge else None
            else:
                setattr(self, f.name, getattr(other, f.name))


# Fields `from_dict`'s scalar loop and `token_dict`'s prune loop both skip, each
# handling them explicitly instead (nested dataclasses, per-seat lists, and `seed`
# whose Optional[int] defeats `_coerce`'s type-of-default dispatch).
_STRUCTURED = ("ai", "ai_strategy", "challenge", "seed")


def _trim_trailing(items: list, default) -> list:
    """``items`` with its trailing default-valued entries dropped.

    Only the tail can go: these lists are positional (indexed by seat-1), and
    `from_dict` pads a short one back out with defaults.
    """
    end = len(items)
    while end > 0 and items[end - 1] == default:
        end -= 1
    return items[:end]


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


def _challenge_from_dict(d) -> Optional[Challenge]:
    """A Challenge from a dict, or None if absent/malformed/scoreless.

    A zero ``turns`` is not a result anyone can beat, so it reads as "no
    challenge" — that way callers only ever test ``settings.challenge``.
    """
    if not isinstance(d, dict):
        return None
    out = Challenge()
    for f in fields(Challenge):
        if f.name in d:
            setattr(out, f.name, _coerce(d[f.name], getattr(out, f.name)))
    return out if out.turns > 0 else None


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
    except Exception:  # no OS entropy source (some sandboxed runtimes)
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
