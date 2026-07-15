"""Persistent game logs: the whole of a match as replayable JSON.

A match is fully determined by its ``Settings``, its concrete ``seed`` and the
orders the human seat submitted each turn — every other source of randomness
(map generation, combat, AI tie-breaks) flows through ``state.rng``, which the
seed fixes. So we never snapshot a ``GameState``; we record only the *inputs*:

    {settings, seed, turns: [{"ai": bool, "orders": [order, ...]}, ...], ...}

``turns[i]`` is the human seat's contribution on turn ``i``. ``reconstruct``
replays those inputs back through the pure engine to rebuild the exact state at
any point — that is what powers "resume".

Reproducing the *exact* ``state.rng`` draw sequence is what makes replay
bit-identical, and that hinges on one detail: the AI consumes ``rng`` (tie-break
jitter), so a turn's draws depend on *which* seats had a decision computed. When
the human plays by hand its orders draw nothing, so we store them and feed them
back verbatim. Under autoplay the human seat is AI-driven — computing its orders
draws ``rng`` *before* the opponents' — so we flag that turn (``"ai": true``) and
``reconstruct`` re-runs ``decide`` for the human seat to reproduce those draws in
the same order (the stored orders are kept for inspection but regenerated). The
flag is per-turn because autoplay can be toggled mid-game.

Pure core (no pygame): like ``settings``, this serializes trivially and drives
the headless engine. It keeps the engine's AI inversion — ``reconstruct`` takes
the ``decide`` function as a parameter rather than importing ``ai``.

Every game is written to its own file under a gitignored ``games/`` dir beside
the repo (mirroring ``menu._SAVE_DIR`` and ``ai.MODELS_DIR``), rewritten in place
after each turn so the file on disk always reflects the live match.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import engine
from .model import GameState, Order
from .settings import Settings, build_state

FORMAT_VERSION = 1

# Saved matches live beside the repo, not the cwd, so they never litter the tree
# wherever the game is launched from (same anchoring as saves/ and models/).
GAMES_DIR = Path(__file__).resolve().parent.parent / "games"

# A decision function, same shape the engine injects (see engine.DecideFn).
DecideFn = Callable[[GameState, int], list[Order]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _order_to_dict(o: Order) -> dict:
    return {"owner": o.owner_id, "src": o.source_id, "dest": o.dest_id, "ships": o.ships}


def _order_from_dict(d: dict) -> Optional[Order]:
    """Rebuild one Order, or None if the entry is malformed (skip, don't crash)."""
    try:
        return Order(int(d["owner"]), int(d["src"]), int(d["dest"]), int(d["ships"]))
    except (TypeError, ValueError, KeyError):
        return None


@dataclass
class GameLog:
    """The complete input history of one match — settings, seed and every turn's
    human orders — plus outcome metadata. ``path`` is the file it lives in and is
    not part of the serialized form."""

    seed: int
    settings: dict                                  # Settings.to_dict()
    turns: list[dict] = field(default_factory=list)  # {"ai": bool, "orders": [...]}
    winner: Optional[int] = None
    finished: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: int = FORMAT_VERSION
    path: Optional[Path] = field(default=None, compare=False)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def record_turn(self, orders: list[Order], human_ai: bool = False) -> None:
        """Append one turn's human contribution as issued to ``engine.end_turn``.

        ``human_ai`` records that the human seat was AI-driven this turn (autoplay),
        which ``reconstruct`` needs to reproduce the rng draw order (see module doc).
        """
        self.turns.append({
            "ai": bool(human_ai),
            "orders": [_order_to_dict(o) for o in orders],
        })
        self.updated_at = _now_iso()

    def mark_finished(self, winner: Optional[int]) -> None:
        self.winner = winner
        self.finished = True
        self.updated_at = _now_iso()

    def turn_is_ai(self, turn_index: int) -> bool:
        """Whether the human seat was AI-driven on ``turn_index`` (autoplay)."""
        entry = self.turns[turn_index]
        return bool(entry.get("ai", False)) if isinstance(entry, dict) else False

    def orders_for(self, turn_index: int) -> list[Order]:
        """The recorded human orders for turn ``turn_index`` (bad entries dropped).

        Tolerates a bare-list entry (hand-edited / older) as a manual turn.
        """
        entry = self.turns[turn_index]
        raw = entry.get("orders", []) if isinstance(entry, dict) else entry
        return [o for o in (_order_from_dict(d) for d in raw) if o is not None]

    # -- serialization ------------------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "seed": self.seed,
            "settings": self.settings,
            "turns": self.turns,
            "winner": self.winner,
            "finished": self.finished,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict, path: Optional[Path] = None) -> "GameLog":
        """Rebuild from parsed JSON, tolerantly (missing keys keep defaults)."""
        turns_raw = data.get("turns")
        turns = list(turns_raw) if isinstance(turns_raw, list) else []
        winner = data.get("winner")
        return cls(
            seed=int(data.get("seed", 0)),
            settings=data.get("settings") if isinstance(data.get("settings"), dict) else {},
            turns=turns,
            winner=int(winner) if isinstance(winner, int) and not isinstance(winner, bool) else None,
            finished=bool(data.get("finished", False)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            version=int(data.get("version", FORMAT_VERSION)),
            path=path,
        )

    def save(self) -> None:
        """Rewrite this log to ``path`` (created on first save)."""
        if self.path is None:
            self.path = _game_path(self.seed)
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp sibling then replace, so a crash mid-write can't leave a
        # half-written (unloadable) file behind.
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        tmp.replace(self.path)


def _game_path(seed: int) -> Path:
    """A fresh, sortable filename for a new match (timestamp keeps them ordered)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return GAMES_DIR / f"game_{stamp}_{seed}.json"


def new_log(settings: Settings, seed: int) -> GameLog:
    """Start a log for a new match; ``save`` writes it once the first turn lands."""
    return GameLog(seed=seed, settings=settings.to_dict(), path=_game_path(seed))


def list_logs() -> list[Path]:
    """Every saved match, newest first (by file mtime)."""
    if not GAMES_DIR.is_dir():
        return []
    files = [p for p in GAMES_DIR.glob("game_*.json") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def load(path: Path) -> GameLog:
    """Read one log (raises on missing/invalid JSON)."""
    with open(path) as fh:
        return GameLog.from_dict(json.load(fh), path=path)


def latest_log() -> Optional[GameLog]:
    """The most recently updated saved match, or None if there are none.

    Skips files that fail to load (corrupt / hand-broken) rather than crashing,
    so one bad file never blocks startup.
    """
    for path in list_logs():
        try:
            return load(path)
        except (OSError, ValueError):
            continue
    return None


def reconstruct(
    log: GameLog,
    decide: DecideFn,
    on_turn: Optional[Callable[[GameState], None]] = None,
) -> tuple[GameState, Settings]:
    """Replay a log's inputs through the engine to rebuild its current state.

    Returns the state advanced by every recorded turn, plus the ``Settings`` it
    was built from (the menu/CLI want both). Stops early if a win was already
    reached, so a finished log reconstructs to its final position.

    ``on_turn`` (if given) is invoked with the state at the opening position and
    again after each replayed turn — the shell uses it to rebuild fog-of-war
    memory across the whole game, not just the final frame.
    """
    settings = Settings.from_dict(log.settings)
    state = build_state(settings, log.seed)
    human = state.human()
    if on_turn is not None:
        on_turn(state)
    for i in range(log.turn_count):
        if state.winner is not None:
            break
        if log.turn_is_ai(i) and human is not None:
            # Autoplay turn: re-run the human seat's AI so its rng draws land in
            # the same order they did when recorded (see module docstring).
            human_orders = decide(state, human.id)
        else:
            human_orders = log.orders_for(i)
        engine.end_turn(state, human_orders=human_orders, decide=decide)
        if on_turn is not None:
            on_turn(state)
    return state, settings
