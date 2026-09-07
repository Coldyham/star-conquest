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

import base64
import json
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import engine
from .model import GameState, Order
from .paths import data_dir
from .settings import Settings, build_state, fresh_rng

FORMAT_VERSION = 2   # 1 recorded the human's orders alone; see the module doc

# Saved matches live under the writable data dir (repo root on desktop, the
# app-private dir on Android), not the cwd, so they never litter the tree wherever
# the game is launched from (same anchoring as saves/ and models/).
GAMES_DIR = data_dir() / "games"


# A match id is 16 lowercase hex digits. Validated on the way *in* as well as
# minted here: a log is uploaded verbatim (`GameLog.encoded`), so a hand-edited
# file must not be able to put arbitrary text into a request path or a database
# column that other rows are keyed against.
_MATCH_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_match_id() -> str:
    """A fresh id for one match — what a posted score points at to find its replay.

    64 bits from ``settings.fresh_rng``, never ``state.rng``: this must *not* be
    reproducible from the seed, or every player of a shared map would mint the
    same id and their logs would collide. Same reason the module is used for a
    fresh map seed, and the same reason it exists rather than plain ``random``
    (the web build can auto-seed identically on every page load).
    """
    return f"{fresh_rng().getrandbits(64):016x}"


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
    # Minted per match, so a score posted to the leaderboard can name the replay
    # it was made in (`Challenge.log`). Not derived from the seed — see
    # `_new_match_id` — and not part of what makes a replay reproduce.
    match_id: str = field(default_factory=_new_match_id)
    winner: Optional[int] = None
    finished: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: int = FORMAT_VERSION
    path: Optional[Path] = field(default=None, compare=False)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def hand_turns(self) -> int:
        """How many recorded turns the human decided themselves.

        The per-turn ``ai`` flag is what makes this honest about a game played by
        hand and then autoplayed to its conclusion — normal once a match is
        decided, and disclosed on a challenge link rather than voiding it. Lives
        here rather than in the shell because the leaderboard's verifier recomputes
        it from the log too, and both must agree on what "by hand" counts as.
        """
        return sum(1 for i in range(self.turn_count) if not self.turn_is_ai(i))

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
            "match_id": self.match_id,
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
        # A missing or malformed id is replaced rather than kept: logs written
        # before the field existed have none, and `save` stamps the new one in on
        # the next turn. Validated, not merely defaulted — see `_MATCH_ID_RE`.
        match_id = str(data.get("match_id", "") or "")
        return cls(
            seed=int(data.get("seed", 0)),
            settings=settings,
            turns=turns,
            match_id=match_id if _MATCH_ID_RE.match(match_id) else _new_match_id(),
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

    def setup_key(self) -> str:
        """``Settings.challenge_key`` of the setup this match was actually played on.

        The seed is taken from the log rather than from its stored settings, which
        may still say "roll a fresh one" — `main` resolves that at game start and
        never writes it back, so hashing the settings alone would file a random-seed
        game under a key that describes no particular map. This is the same pinning
        `main.challenge_settings` does for a link, so a checkpoint uploaded
        mid-game and the score posted at the end land on one key.
        """
        cfg = Settings.from_dict(self.settings)
        cfg.seed = self.seed
        return cfg.challenge_key()

    def encoded(self) -> str:
        """This log as one line of text: compact JSON, deflated, base64url.

        Exactly the encoding ``Settings.to_token`` uses, for the same reason and
        with the same decoder on the other side — a log is a shared link's big
        brother. The saved *file* stays indented for reading; this form is for the
        wire, where it is roughly a third the size (measured 5-14 KiB against
        39-131 KiB compact for whole games).
        """
        raw = json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, blob: str) -> "GameLog":
        """The inverse of ``encoded`` (raises ``ValueError`` on anything else).

        Tolerates the uncompressed form the same way ``Settings.from_token`` does
        — plain JSON always starts ``{``, which zlib output never does — so a log
        stored before compression, or written by hand, still reads.
        """
        try:
            raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
            if not raw[:1] == b"{":
                raw = zlib.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — one bad blob, one message
            raise ValueError(f"unreadable game log: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("unreadable game log: not an object")
        return cls.from_dict(data)


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
