"""Persistent game logs: the whole of a match as replayable JSON.

A match is fully determined by its ``Settings``, its concrete ``seed`` and, per
turn, the orders every seat issued plus the combat draws they produced. So we
never snapshot a ``GameState``; we record those inputs:

    {settings, seed, turns: [{"ai": bool, "orders": [...], "dice": [...],
                              "rules": {src: [dest, keep]}}, ...], ...}

``reconstruct`` feeds each turn straight back through ``engine.end_turn`` as a
``TurnRecord``, so the board is rebuilt without asking a single seat to decide
anything — that is what powers "resume" and history review.

Recording *only* the human's orders (format version 1) was smaller, and rebuilt
the rest by re-running the AI against the same seeded ``rng``. That is exactly as
reliable as the weakest bot in the game: a strategy that consults the clock —
``models/knower.py`` truncates its search on a wall-clock budget — decides
differently on the replay than it did in the match, and from that turn on the
reconstruction is a *different game* than the one that was played. Nothing about
a bot's determinism is enforceable, so the log stopped depending on it: the dice
are recorded for the same reason, since skipping the AI leaves ``state.rng``
somewhere other than where the battle found it. Version-1 logs can no longer be
replayed faithfully and are skipped by ``latest_log``.

``rules`` is the human's standing auto-forward rules as they stood that turn —
shell state (``Ui.auto_forward``), not simulation, and carried so that resuming
or rewinding hands the player back the routes they set up rather than an empty
board (``main.resume_game``).

Pure core (no pygame): like ``settings``, this serializes trivially and drives
the headless engine. Replay needs no AI at all now, which is a stronger form of
the engine's AI inversion than passing ``decide`` in was.

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
from .paths import data_dir
from .settings import Settings, build_state

FORMAT_VERSION = 2   # 1 recorded the human's orders alone; see the module doc

# Saved matches live under the writable data dir (repo root on desktop, the
# app-private dir on Android), not the cwd, so they never litter the tree wherever
# the game is launched from (same anchoring as saves/ and models/).
GAMES_DIR = data_dir() / "games"


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
    orders, dice and standing rules — plus outcome metadata. ``path`` is the file
    it lives in and is not part of the serialized form."""

    seed: int
    settings: dict  # Settings.to_dict()
    turns: list[dict] = field(default_factory=list)  # see the module docstring
    winner: Optional[int] = None
    finished: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: int = FORMAT_VERSION
    path: Optional[Path] = field(default=None, compare=False)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def record_turn(self, record: engine.TurnRecord, human_ai: bool = False,
                    rules: Optional[dict[int, tuple[int, int]]] = None) -> None:
        """Append one resolved turn exactly as ``engine.end_turn`` played it.

        ``human_ai`` records that the human seat was AI-driven this turn (autoplay)
        — not needed to replay it, but it is how ``main.hand_turns`` stays honest
        about a game that was finished on autoplay. ``rules`` is the human's
        standing auto-forward rules in force for the turn.
        """
        self.turns.append(
            {
                "ai": bool(human_ai),
                "orders": [_order_to_dict(o) for o in record.orders],
                "dice": list(record.dice),
                "rules": {str(src): [dest, keep] for src, (dest, keep) in (rules or {}).items()},
            }
        )
        self.updated_at = _now_iso()

    def mark_finished(self, winner: Optional[int]) -> None:
        self.winner = winner
        self.finished = True
        self.updated_at = _now_iso()

    def truncate(self, n: int) -> None:
        """Drop every turn after ``n`` and re-open the match (mid-game rewind).

        Keeps ``turns[:n]`` (turn ``n`` == the board after ``n`` recorded turns,
        so ``truncate(0)`` rewinds to the opening position) and clears the
        finished/winner outcome so continued play appends to the same file.
        """
        self.turns = self.turns[:n]
        self.winner = None
        self.finished = False
        self.updated_at = _now_iso()

    def fork(self, n: int) -> "GameLog":
        """A fresh log branching from turn ``n`` (finished-game rewind).

        Same seed and settings, ``turns[:n]``, outcome cleared, and a brand-new
        file ``path`` (a new timestamp) so the original — typically a finished
        match one wants to keep — is left untouched. The caller ``save()``s it and
        continues play into the new file.
        """
        return GameLog(
            seed=self.seed,
            settings=self.settings,
            turns=list(self.turns[:n]),
            version=self.version,   # the turns come with it, so the format does too
            path=_game_path(self.seed),
        )

    def turn_is_ai(self, turn_index: int) -> bool:
        """Whether the human seat was AI-driven on ``turn_index`` (autoplay)."""
        entry = self.turns[turn_index]
        return bool(entry.get("ai", False)) if isinstance(entry, dict) else False

    def orders_for(self, turn_index: int) -> list[Order]:
        """Every seat's recorded orders for turn ``turn_index``, in the sequence
        they were applied (bad entries dropped).

        Tolerates a bare-list entry (hand-edited / older) as a manual turn.
        """
        entry = self.turns[turn_index]
        raw = entry.get("orders", []) if isinstance(entry, dict) else entry
        return [o for o in (_order_from_dict(d) for d in raw) if o is not None]

    def dice_for(self, turn_index: int) -> list[float]:
        """The combat draws that turn ``turn_index`` was fought with."""
        entry = self.turns[turn_index]
        raw = entry.get("dice", []) if isinstance(entry, dict) else []
        return [float(d) for d in raw if isinstance(d, (int, float))]

    def script_for(self, turn_index: int) -> engine.TurnRecord:
        """Turn ``turn_index`` in the form ``engine.end_turn`` replays."""
        return engine.TurnRecord(self.orders_for(turn_index), self.dice_for(turn_index))

    def rules_for(self, turn_index: int) -> dict[int, tuple[int, int]]:
        """The human's standing auto-forward rules in force on ``turn_index``.

        Shaped for ``Ui.auto_forward``; malformed entries are dropped rather than
        raising, in keeping with the rest of this format.
        """
        entry = self.turns[turn_index]
        raw = entry.get("rules", {}) if isinstance(entry, dict) else {}
        rules: dict[int, tuple[int, int]] = {}
        for src, rule in (raw or {}).items():
            try:
                dest, keep = rule
                rules[int(src)] = (int(dest), int(keep))
            except (TypeError, ValueError):
                continue
        return rules

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
        settings_raw = data.get("settings")
        settings = settings_raw if isinstance(settings_raw, dict) else {}
        winner = data.get("winner")
        return cls(
            seed=int(data.get("seed", 0)),
            settings=settings,
            turns=turns,
            winner=int(winner) if isinstance(winner, int) and not isinstance(winner, bool) else None,
            finished=bool(data.get("finished", False)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            version=int(data.get("version", 1)),   # unversioned files predate the field
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
    """The most recently updated saved match that can still be replayed exactly.

    Skips files that fail to load (corrupt / hand-broken) rather than crashing, so
    one bad file never blocks startup, and skips older formats: a version-1 log
    holds no record of what the AI seats did, so resuming one would silently hand
    the player a different game than the one they left (see the module doc).
    """
    for path in list_logs():
        try:
            log = load(path)
        except (OSError, ValueError):
            continue
        if log.version >= FORMAT_VERSION:
            return log
    return None


def reconstruct(
    log: GameLog,
    on_turn: Optional[Callable[[GameState], object]] = None,
) -> tuple[GameState, Settings]:
    """Replay a log's turns through the engine to rebuild its current state.

    Returns the state advanced by every recorded turn, plus the ``Settings`` it
    was built from (the menu/CLI want both). Stops early if a win was already
    reached, so a finished log reconstructs to its final position.

    No seat is asked to decide anything: each turn is fed back as the record of
    what was actually played, so this is exact however the AIs behaved and costs
    nothing but the engine (a deep-searching bot's game used to be re-thought from
    scratch here, seconds of it, every time history opened).

    ``on_turn`` (if given) is invoked with the state at the opening position and
    again after each replayed turn — the shell uses it to rebuild fog-of-war
    memory across the whole game, not just the final frame.
    """
    settings = Settings.from_dict(log.settings)
    state = build_state(settings, log.seed)
    if on_turn is not None:
        on_turn(state)
    for i in range(log.turn_count):
        if state.winner is not None:
            break
        engine.end_turn(state, script=log.script_for(i))
        if on_turn is not None:
            on_turn(state)
    return state, settings
