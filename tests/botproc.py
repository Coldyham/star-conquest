"""Transport for external bots: one child process per seat, JSON per line.

The other half of ``starconquest.botio`` (the schema), kept out of the package
on purpose. The core imports no ``subprocess``, and the browser build *cannot*
fork at all — pygbag is CPython on WASM, single threaded — so an external bot is
a tournament participant rather than a shipped one. Living here beside
``sim.py`` puts it in the layer that already serves both the suite and
``tools/bot_replay.py``, and keeps ``bots/`` outside ``models/`` so
``tools/build_web.sh`` (which stages ``models/``) never sees it.

Registration is opt-in (``sim --external``), unlike ``ai.load_models()``. A
default ``--ladder`` picking up a subprocess bot would be a surprise, and
``bot_replay``'s roster is ``ai.available_strategies()`` — it would start
posting external bots to the leaderboard before that has been decided.

``docs/bot-api.md`` is the protocol this speaks.
"""

from __future__ import annotations

import atexit
import json
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from starconquest import ai, botio
from starconquest.model import GameState, Order
from starconquest.paths import data_dir

# Repo-anchored, mirroring ai.MODELS_DIR and menu._SAVE_DIR.
BOTS_DIR = data_dir() / "bots"

# A dead or hopeless bot is not an error the ladder should crash on, but nor is
# it a result: after this many timeouts the seat plays `heuristic` for the rest
# of the game and the run is flagged degraded (never scored, never posted).
FORFEIT_TIMEOUTS = 3

# Process start plus the handshake, independent of the per-decide budget: a cold
# interpreter or a JIT warming up is not the bot being slow at deciding.
HANDSHAKE_MS = 10_000


@dataclass
class Manifest:
    """``bots/<name>.bot.json``. See docs/bot-api.md for the field meanings."""

    name: str
    cmd: list[str]
    cwd: Path
    protocol: int = botio.PROTOCOL
    version: str = ""
    budget_ms: int = 150
    # The bot's opt-in to having its budget lifted by a batch runner, exactly as
    # a model's module-level BUDGET_SCALE is (see ai.set_budget_scale).
    budget_scale: float = 1.0

    @classmethod
    def load(cls, path: Path) -> Optional["Manifest"]:
        """Parse one manifest, or None if it is unreadable — a bad file is
        skipped with a warning, the way a drop-in model that fails to import is.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cmd = [str(part) for part in data["cmd"]]
            if not cmd:
                raise ValueError("empty cmd")
            cwd = (path.parent / data.get("cwd", ".")).resolve()
            return cls(name=str(data.get("name") or path.name.split(".")[0]),
                       cmd=cmd, cwd=cwd,
                       protocol=int(data.get("protocol", botio.PROTOCOL)),
                       version=str(data.get("version", "")),
                       budget_ms=int(data.get("budget_ms", 150)),
                       budget_scale=float(data.get("budget_scale", 1.0)))
        except Exception as exc:                                  # noqa: BLE001
            print(f"botproc: skipping {path.name}: {exc}", file=sys.stderr)
            return None


def load_manifests(directory: Path = BOTS_DIR) -> list[Manifest]:
    """Every readable ``*.bot.json`` in ``directory``, sorted by name."""
    if not directory.is_dir():
        return []
    found = [Manifest.load(p) for p in sorted(directory.glob("*.bot.json"))]
    return [m for m in found if m is not None]


class _Session:
    """One live child process, mid-match, for one seat."""

    def __init__(self, manifest: Manifest, budget_ms: int, stderr_to):
        self.manifest = manifest
        self.budget_ms = budget_ms
        self.timeouts = 0
        self.degraded = ""
        self.last_turn = -1
        self.proc = subprocess.Popen(
            manifest.cmd, cwd=str(manifest.cwd), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr_to, text=True, bufsize=1,
        )
        # A reader thread rather than select(): a pipe is not selectable on
        # Windows, and tests/sim already carries one such portability scar.
        self._lines: queue.Queue[Optional[str]] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        try:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._lines.put(line)
        except Exception:                                         # noqa: BLE001
            pass
        finally:
            self._lines.put(None)     # EOF sentinel: the process is finished

    def ask(self, message: dict, timeout_ms: int) -> Optional[Any]:
        """Send one message, return its parsed reply, or None on any failure."""
        if self.proc.poll() is not None:
            return None
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            return None
        while True:
            try:
                line = self._lines.get(timeout=timeout_ms / 1000.0)
            except queue.Empty:
                return None
            if line is None:          # process exited
                return None
            line = line.strip()
            if not line:              # blank lines are not a protocol violation
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except Exception:                                     # noqa: BLE001
                pass
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@dataclass
class Strategy:
    """A manifest, registered as a strategy: ``(state, pid) -> list[Order]``.

    One process per ``(seed, seat)``, handshaken on first use and re-handshaken
    when the turn counter goes backwards — a ladder plays game after game in one
    process, and a repeated seed is how it checks a seating both ways round.
    """

    manifest: Manifest
    budget_ms: int
    reveal_opponents: bool = False
    stderr_to: Any = subprocess.DEVNULL
    sessions: dict[tuple[int, int], _Session] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)

    def _session(self, state: GameState, pid: int) -> Optional[_Session]:
        key = (state.seed, pid)
        session = self.sessions.get(key)
        if session is not None and state.turn <= session.last_turn:
            session.close()                # a new game on the same seed and seat
            session = None
        if session is None:
            session = _Session(self.manifest, self.budget_ms, self.stderr_to)
            hello = botio.hello(state, pid, self.budget_ms, self.reveal_opponents)
            hello["protocol"] = self.manifest.protocol
            if session.ask(hello, HANDSHAKE_MS) is None:
                session.close()
                self._degrade(pid, "no handshake")
                return None
            self.sessions[key] = session
        session.last_turn = state.turn
        return session

    def _degrade(self, pid: int, why: str) -> None:
        """Record that this seat stopped being the bot. Loud, unlike the app's
        silent fall back to ``heuristic`` for an unknown strategy name: in the
        app that keeps a game playable, but a *ladder* result containing a
        fallback seat is not a result and must not be scored as one.
        """
        note = f"{self.manifest.name} seat {pid}: {why}"
        if note not in self.degraded:
            self.degraded.append(note)
            print(f"botproc: {note} — seat falls back to heuristic", file=sys.stderr)

    def __call__(self, state: GameState, pid: int) -> list[Order]:
        session = self._session(state, pid)
        if session is None or session.degraded:
            return ai.compute_orders(state, pid)
        payload = botio.turn_payload(state, pid, self.budget_ms)
        reply = session.ask(payload, self.budget_ms)
        if reply is None:
            session.timeouts += 1
            if session.timeouts >= FORFEIT_TIMEOUTS or session.proc.poll() is not None:
                session.degraded = "timed out" if session.proc.poll() is None else "died"
                self._degrade(pid, session.degraded)
                return ai.compute_orders(state, pid)
            return []                  # one bad turn: the seat simply holds
        return botio.orders_from(reply, pid)

    def close(self) -> None:
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()


_registered: list[Strategy] = []


def register_external(names: Optional[list[str]] = None, batch: bool = True,
                      reveal_opponents: bool = False,
                      directory: Path = BOTS_DIR,
                      stderr_to: Any = subprocess.DEVNULL) -> list[str]:
    """Register every manifest (or just ``names``) as a strategy. Returns the
    names registered.

    ``batch`` multiplies each bot's ``budget_ms`` by its own ``budget_scale``,
    which is what ``ai.set_budget_scale`` does for Python bots and for the same
    reason: nothing is waiting on an offline run, and a guard that can never
    trip makes the result *more* reproducible, not less. The product is sent —
    a bot never computes its own budget.
    """
    registered: list[str] = []
    for manifest in load_manifests(directory):
        if names is not None and manifest.name not in names:
            continue
        budget = int(manifest.budget_ms * (manifest.budget_scale if batch else 1.0))
        strategy = Strategy(manifest, budget, reveal_opponents, stderr_to)
        ai.register(manifest.name, strategy)
        _registered.append(strategy)
        registered.append(manifest.name)
    return registered


def degraded_runs() -> list[str]:
    """Every seat that stopped being its bot this process. Non-empty means no
    result from this run may be reported as that bot's."""
    return [note for strategy in _registered for note in strategy.degraded]


def shutdown() -> None:
    for strategy in _registered:
        strategy.close()


atexit.register(shutdown)
