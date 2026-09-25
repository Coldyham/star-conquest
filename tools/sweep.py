#!/usr/bin/env python3
"""Paired A/B harness for bot parameter tuning.

    uv run python tools/sweep.py --arm 'loose:expand_margin=1.1' \
                                --arm 'tight:expand_margin=1.6' --smoke
    uv run python tools/sweep.py --arm 'loose:expand_margin=1.1' \
                                --arm 'tight:expand_margin=1.6' --seeds 200
    uv run python tools/sweep.py --arm ... --report
    uv run python tools/sweep.py --stop
    uv run python tools/sweep.py --self-test

Every arm is duelled against one fixed **baseline**, two seats, and each seed is
played twice with the two seats swapped. With deterministic bots that mirror
cancels seat advantage *structurally* rather than on average, so an arm whose
config equals the baseline reads exactly 50.0% — which is what the built-in null
control asserts before any arm is believed. Free-for-all rotations do not cancel
at more than two seats, so this harness only ever plays duels.

A **cell** is a measurement context: map mode, node count and the speed knob.
A margin keyed off travel distance is live in part of that space and unreachable
in the rest, so an arm is measured in several cells and reported per cell as well
as pooled.

Refusals, all fatal and all before any game is played:

  * two arms resolving to the same config, or an arm equal to the baseline
  * an arm naming a global (``config.*``) knob — a global cannot differ between
    the two seats of one duel, so it is a property of a cell, not of an arm
  * an arm naming a field that is neither an ``AiParams`` field nor a constant
    its strategy module actually defines
  * a full run with no passing ``--smoke`` recorded for this exact sweep key
  * a second sweep while one is already running

Results append to ``results/ledger.jsonl``, one line per finished game, written
with a single ``os.write`` to an ``O_APPEND`` descriptor and fsynced. A kill at
any moment loses nothing and a rerun resumes rather than restarts.

Process control is by PID file (``results/sweep.pid``), never by matching process
names: a name pattern can match the harness itself, or the shell that launched
it. ``--stop`` reads the recorded pid *and* its kernel start time, so a recycled
pid is refused rather than signalled.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import NoReturn, Self

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen
from starconquest import settings as settings_mod
from starconquest.model import AiParams
from starconquest.settings import Settings
from tests import sim

RESULTS = ROOT / "results"
LEDGER = RESULTS / "ledger.jsonl"
PIDFILE = RESULTS / "sweep.pid"
MODELS = ROOT / "models"

# A smoke is exactly this many games per arm, and the null control gets the same.
SMOKE_GAMES_PER_ARM = 20

# Smoke seeds are disjoint from the measurement seeds, so a smoke can never
# contribute a game to a reported rate.
SMOKE_SEED_BASE = 900_000

Z95 = 1.959963984540054

# One record must fit in a single atomic append (PIPE_BUF is 4096 on Linux).
MAX_RECORD_BYTES = 4000

# Modules whose contents can move a game's outcome; digested into the sweep key
# so results fitted to one revision of the engine or a bot are never pooled with
# another's.
_OUTCOME_MODULES = (
    "ai", "combat", "config", "custommap", "engine", "geometry", "mapgen",
    "model", "settings", "starnames",
)

# Lifted the same 100x as tools/bot_replay.py: a bot's wall-clock guard is sized
# for the browser, and tripping one is the only thing that makes its output
# depend on the clock.
BUDGET_SCALE = 100.0

_STOPPING = False


# --------------------------------------------------------------------------- #
# Failure
# --------------------------------------------------------------------------- #
def _fatal(message: str, *detail: str) -> NoReturn:
    print(f"\nFATAL: {message}", file=sys.stderr)
    for line in detail:
        print(f"       {line}", file=sys.stderr)
    print(file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Arms and cells
# --------------------------------------------------------------------------- #
_AI_FIELDS = {f.name: f.type for f in fields(AiParams)}


@dataclass(frozen=True)
class Arm:
    """One candidate config: a strategy plus per-seat overrides.

    ``params`` are ``AiParams`` fields, which the engine stamps on a single seat.
    ``consts`` are module-level constants of the strategy's own ``models/`` file;
    those are module globals, so an arm that names one is run against a *separate
    import* of that file (see ``_resolve``) and the baseline seat keeps the
    original. Anything that cannot be made per-seat is refused.
    """

    name: str
    strategy: str
    params: tuple[tuple[str, object], ...] = ()
    consts: tuple[tuple[str, object], ...] = ()

    def config(self) -> dict:
        return {
            "strategy": self.strategy,
            "params": {k: v for k, v in self.params},
            "consts": {k: v for k, v in self.consts},
        }

    def identity(self) -> str:
        """The arm's config with its *name* left out, so two differently-named
        arms holding the same config collide."""
        return json.dumps(self.config(), sort_keys=True)

    def ai_params(self) -> AiParams:
        return replace(AiParams(), **{k: v for k, v in self.params})

    def label(self) -> str:
        bits = [f"{k}={v}" for k, v in itertools.chain(self.params, self.consts)]
        return f"{self.strategy}" + (f" {' '.join(bits)}" if bits else " (stock)")


def _parse_number(text: str) -> object:
    """int when the literal has no decimal point, else float, else the string.

    ``aux`` is the one field whose int/float form is load-bearing elsewhere in
    the codebase, so the distinction is preserved rather than widened here too.
    """
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_arm(spec: str, default_strategy: str) -> Arm:
    """``name:key=value,key=value`` -> an ``Arm``.

    A lowercase key is an ``AiParams`` field; an uppercase key is a constant of
    the strategy's module. ``strategy=`` picks the bot. Every key is validated
    here, so a typo is a refusal rather than a silently ignored override.
    """
    if ":" not in spec:
        name, body = spec, ""
    else:
        name, body = spec.split(":", 1)
    name = name.strip()
    if not name:
        _fatal(f"arm spec {spec!r} has no name", "expected  name:key=value,key=value")

    strategy = default_strategy
    params: dict[str, object] = {}
    consts: dict[str, object] = {}
    for chunk in (c.strip() for c in body.split(",")):
        if not chunk:
            continue
        if "=" not in chunk:
            _fatal(f"arm {name!r}: {chunk!r} is not key=value")
        key, _, raw = chunk.partition("=")
        key, raw = key.strip(), raw.strip()
        if key == "strategy":
            strategy = raw
            continue
        if key.startswith("config."):
            _fatal(
                f"arm {name!r} names the global knob {key!r}",
                "config.* is a module global: both seats of a duel would read the",
                "same value, so the arm and the baseline would be identical in play.",
                "Put it on a --cell instead, where it applies to the whole cell.",
            )
        value = _parse_number(raw)
        if key.isupper():
            consts[key] = value
        elif key in _AI_FIELDS:
            params[key] = _coerce_ai_field(name, key, value)
        else:
            _fatal(
                f"arm {name!r}: {key!r} is not an AiParams field",
                f"fields: {', '.join(sorted(_AI_FIELDS))}",
                "(an UPPERCASE key is read as a constant of the strategy's module)",
            )
    return Arm(name, strategy, tuple(sorted(params.items())), tuple(sorted(consts.items())))


def _coerce_ai_field(arm: str, key: str, value: object) -> object:
    """Cast to the field's declared type, except ``aux`` which keeps its literal
    form (see ``_parse_number``)."""
    if key == "aux":
        return value
    declared = _AI_FIELDS[key]
    want = declared if isinstance(declared, str) else getattr(declared, "__name__", "")
    try:
        if want == "int":
            if isinstance(value, float) and value != int(value):
                _fatal(f"arm {arm!r}: {key} is an int field, got {value}")
            return int(value)  # type: ignore[arg-type]
        if want == "float":
            return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _fatal(f"arm {arm!r}: {key}={value!r} is not a {want}")
    return value


@dataclass(frozen=True)
class Cell:
    """A measurement context. ``knobs`` are ``Settings`` global-knob fields."""

    mode: str
    nodes: int
    knobs: tuple[tuple[str, object], ...] = ()

    def key(self) -> str:
        bits = "".join(f",{k}={v}" for k, v in self.knobs)
        return f"{self.mode}:{self.nodes}{bits}"

    def settings(self) -> Settings:
        """A pristine ``Settings`` carrying only this cell's overrides.

        ``Settings`` field defaults bind to ``config`` at import time, so a fresh
        instance always restores every other knob — one cell can never leak a
        global into the next.
        """
        return Settings(mode=self.mode, nodes=self.nodes, **{k: v for k, v in self.knobs})


def parse_cell(spec: str) -> Cell:
    """``mode:nodes[:ly][,knob=value]`` -> a ``Cell``."""
    head, _, extra = spec.partition(",")
    bits = [b.strip() for b in head.split(":")]
    if len(bits) < 2:
        _fatal(f"cell spec {spec!r} needs at least mode:nodes")
    mode = bits[0]
    if mode not in settings_mod.MODES:
        _fatal(f"cell {spec!r}: unknown mode {mode!r}",
               f"known modes: {', '.join(settings_mod.MODES)}")
    try:
        nodes = int(bits[1])
    except ValueError:
        _fatal(f"cell {spec!r}: {bits[1]!r} is not a node count")
    knobs: dict[str, object] = {}
    if len(bits) > 2 and bits[2]:
        knobs["ship_ly_per_turn"] = float(bits[2])
    known = {f.name for f in fields(Settings)}
    for chunk in (c.strip() for c in extra.split(",")):
        if not chunk:
            continue
        key, _, raw = chunk.partition("=")
        key = key.strip()
        if key not in known:
            _fatal(f"cell {spec!r}: {key!r} is not a Settings field")
        knobs[key] = _parse_number(raw.strip())
    return Cell(mode, nodes, tuple(sorted(knobs.items())))


# --------------------------------------------------------------------------- #
# Validation — everything here runs before a single game
# --------------------------------------------------------------------------- #
def validate(arms: list[Arm], baseline: Arm, cells: list[Cell]) -> None:
    if not arms:
        _fatal("no arms given", "pass at least one --arm name:key=value")
    if not cells:
        _fatal("no cells given")

    seen_names: dict[str, Arm] = {}
    for arm in arms:
        if arm.name in seen_names:
            _fatal(f"two arms are both named {arm.name!r}")
        if arm.name == baseline.name:
            _fatal(f"arm {arm.name!r} collides with the baseline's name")
        seen_names[arm.name] = arm

    by_identity: dict[str, str] = {baseline.identity(): f"the baseline ({baseline.name})"}
    for arm in arms:
        ident = arm.identity()
        if ident in by_identity:
            _fatal(
                f"arm {arm.name!r} is the same config as {by_identity[ident]}",
                f"config: {ident}",
                "A degenerate arm measures the harness, not the knob. The null",
                "control (baseline against itself) is built in and runs in --smoke,",
                "so a hand-written copy of the baseline buys nothing.",
            )
        by_identity[ident] = f"arm {arm.name!r}"

    for arm in arms + [baseline]:
        if arm.strategy not in ai.STRATEGIES:
            _fatal(f"arm {arm.name!r}: unknown strategy {arm.strategy!r}",
                   f"registered: {', '.join(ai.available_strategies())}")
        if arm.consts:
            path = MODELS / f"{arm.strategy}.py"
            if not path.is_file():
                _fatal(f"arm {arm.name!r} overrides constants of {arm.strategy!r},",
                       f"but {path} does not exist (built-ins have no module to patch)")
            module = _load_module(f"sc_sweep_probe_{arm.strategy}", path)
            for key, _value in arm.consts:
                if not hasattr(module, key):
                    _fatal(f"arm {arm.name!r}: {arm.strategy}.py defines no {key!r}")


def _load_module(alias: str, path: Path):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        _fatal(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def _resolve(arm: Arm) -> str:
    """The strategy name to stamp on this arm's seat.

    An arm overriding module constants gets its own import of the file, so its
    globals are genuinely per-seat and the baseline seat still reads the stock
    values out of the original module.
    """
    if not arm.consts:
        return arm.strategy
    alias = f"__sweep_{arm.name}"
    if alias in ai.STRATEGIES:
        return alias
    module = _load_module(f"sc_sweep_{arm.name}", MODELS / f"{arm.strategy}.py")
    for key, value in arm.consts:
        setattr(module, key, value)
    fn = getattr(module, "decide", None)
    if not callable(fn):
        _fatal(f"arm {arm.name!r}: {arm.strategy}.py has no callable decide")
    ai.register(alias, fn)
    return alias


def sweep_key(arms: list[Arm], baseline: Arm, cells: list[Cell], max_turns: int) -> str:
    """Identity of the whole experiment, including the code that will run it.

    A smoke pass is recorded against this, so touching an arm, a cell, the engine
    or a bot invalidates it and the full run refuses until it is re-smoked.
    """
    blob = json.dumps({
        "arms": [[a.name, a.config()] for a in arms],
        "baseline": [baseline.name, baseline.config()],
        "cells": [c.key() for c in cells],
        "max_turns": max_turns,
        "rev": code_rev(),
    }, sort_keys=True)
    return hashlib.blake2s(blob.encode(), digest_size=8).hexdigest()


def code_rev() -> str:
    digest = hashlib.blake2s(digest_size=8)
    paths = [ROOT / "starconquest" / f"{n}.py" for n in _OUTCOME_MODULES]
    paths += sorted(MODELS.glob("*.py"))
    for path in paths:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# The ledger — append-only, one finished game per line
# --------------------------------------------------------------------------- #
class Ledger:
    """Append-only JSONL. Each record is one ``os.write`` to an ``O_APPEND``
    descriptor and under ``MAX_RECORD_BYTES``, which is what makes a concurrent
    writer's line interleave-free, and is fsynced, which is what makes a machine
    crash lose nothing."""

    def __init__(self, path: Path = LEDGER):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd: int | None = None

    def open(self) -> Ledger:
        self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        return self

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def append(self, record: dict) -> None:
        if self._fd is None:
            raise RuntimeError("ledger is not open")
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(line) > MAX_RECORD_BYTES:
            raise ValueError(f"ledger record is {len(line)} bytes, over the atomic limit")
        os.write(self._fd, line)
        os.fsync(self._fd)

    def read(self) -> list[dict]:
        """Every well-formed record. A torn final line (a kill mid-write) is
        skipped rather than fatal, which is the whole point of one-line records."""
        if not self.path.is_file():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


# --------------------------------------------------------------------------- #
# Process control — PID file, never a name pattern
# --------------------------------------------------------------------------- #
def _start_token(pid: int) -> str | None:
    """The kernel's start time for ``pid``, so a recycled pid is not mistaken for
    the process that wrote the file. ``None`` when the pid does not exist."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    # comm may contain spaces and parens; everything after the last ')' is fixed.
    tail = stat.rsplit(")", 1)[-1].split()
    return tail[19] if len(tail) > 19 else None


def _alive(pid: int, token: str | None) -> bool:
    current = _start_token(pid)
    if current is None:
        return False
    return token is None or current == token


class PidFile:
    def __init__(self, path: Path = PIDFILE):
        self.path = path
        self.held = False

    def acquire(self, sweep: str, argv: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            try:
                prior = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                prior = None
            if prior and _alive(int(prior.get("pid", -1)), prior.get("start")):
                _fatal(
                    f"a sweep is already running (pid {prior['pid']}, "
                    f"started {time.ctime(prior.get('launched', 0))})",
                    f"sweep key {prior.get('sweep')}",
                    "stop it with:  uv run python tools/sweep.py --stop",
                )
        pid = os.getpid()
        self.path.write_text(json.dumps({
            "pid": pid,
            "start": _start_token(pid),
            "launched": time.time(),
            "sweep": sweep,
            "argv": argv,
        }, indent=2))
        self.held = True

    def release(self) -> None:
        if not self.held:
            return
        try:
            prior = json.loads(self.path.read_text())
            if int(prior.get("pid", -1)) == os.getpid():
                self.path.unlink()
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        self.held = False


def stop_running() -> int:
    """Signal the pid the file names, and nothing else.

    No process-name matching anywhere: a pattern broad enough to find the sweep
    is broad enough to find this very command, or the shell that started it.
    """
    if not PIDFILE.is_file():
        print(f"no pid file at {PIDFILE} — nothing to stop")
        return 0
    try:
        record = json.loads(PIDFILE.read_text())
        pid = int(record["pid"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        _fatal(f"{PIDFILE} is unreadable; delete it by hand if no sweep is running")

    if pid in (os.getpid(), os.getppid()):
        _fatal(f"the pid file names this process tree (pid {pid}); refusing to signal it")
    if not _alive(pid, record.get("start")):
        print(f"pid {pid} is gone (or was recycled) — clearing the stale pid file")
        PIDFILE.unlink(missing_ok=True)
        return 0

    print(f"stopping sweep {record.get('sweep')} (pid {pid}) ...")
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not _alive(pid, record.get("start")):
            print("stopped cleanly")
            PIDFILE.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    print("did not exit on SIGTERM; sending SIGKILL", file=sys.stderr)
    os.kill(pid, signal.SIGKILL)
    PIDFILE.unlink(missing_ok=True)
    return 1


def _install_signals() -> None:
    def _handle(signum, _frame):
        global _STOPPING
        _STOPPING = True
        print(f"\n[signal {signum}] finishing the current game, then stopping "
              f"(the ledger is already complete up to it)", flush=True)
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


# --------------------------------------------------------------------------- #
# Playing
# --------------------------------------------------------------------------- #
@dataclass
class Seat:
    strategy: str
    params: AiParams


def play_duel(cell: Cell, seed: int, seats: tuple[Seat, Seat], max_turns: int) -> dict:
    """One two-seat game. ``seats[i]`` takes player id ``i + 1``."""
    settings_mod.apply_globals(cell.settings())
    state = mapgen.generate(seed, cell.mode, cell.nodes, 2)
    for player in state.players.values():
        player.is_human = False
    for pid, seat in enumerate(seats, start=1):
        state.players[pid].ai_strategy = seat.strategy
        state.players[pid].ai_params = replace(seat.params)
    sim.check_invariants(state)
    while state.winner is None and state.turn < max_turns:
        engine.end_turn(state, decide=ai.decide)
        sim.check_invariants(state)
    return {
        "winner": state.winner,
        "turns": state.turn,
        "timed_out": state.winner is None,
        "digest": hashlib.blake2s(repr(sim.board_digest(state)).encode(),
                                  digest_size=8).hexdigest(),
    }


NULL = "__null__"


def _seats(arm: Arm, baseline: Arm, mirror: int) -> tuple[tuple[Seat, Seat], int]:
    """The two seats and which player id the arm holds.

    ``mirror`` 0 puts the arm in seat 1, 1 puts it in seat 2. Playing both on the
    same seed is what cancels seat advantage.
    """
    arm_seat = Seat(_resolve(arm), arm.ai_params())
    base_seat = Seat(_resolve(baseline), baseline.ai_params())
    if mirror == 0:
        return (arm_seat, base_seat), 1
    return (base_seat, arm_seat), 2


def jobs(cells: list[Cell], arms: list[Arm], seeds: list[int]) -> list[tuple]:
    """Seed-outermost, so a run stopped early still holds a balanced sample."""
    out = []
    for seed in seeds:
        for cell in cells:
            for arm in arms:
                for mirror in (0, 1):
                    out.append((cell, arm, seed, mirror))
    return out


def run(jobs_list: list[tuple], ledger: Ledger, done: set, *, sweep: str, stage: str,
        baseline: Arm, max_turns: int, rev: str, quiet: bool = False) -> int:
    """Play every job not already in the ledger. Returns games played."""
    played = 0
    total = len(jobs_list)
    started = time.perf_counter()
    for index, (cell, arm, seed, mirror) in enumerate(jobs_list, start=1):
        if _STOPPING:
            print(f"stopping after {played} games this session", flush=True)
            break
        key = (cell.key(), arm.name, seed, mirror)
        if key in done:
            continue
        if arm.name == NULL:
            seats, arm_seat = _seats(baseline, baseline, mirror)
        else:
            seats, arm_seat = _seats(arm, baseline, mirror)
        result = play_duel(cell, seed, seats, max_turns)
        winner = result["winner"]
        ledger.append({
            "kind": "game",
            "sweep": sweep,
            "stage": stage,
            "rev": rev,
            "cell": cell.key(),
            "arm": arm.name,
            "seed": seed,
            "mirror": mirror,
            "arm_seat": arm_seat,
            "winner": winner,
            "arm_won": winner == arm_seat,
            "decided": winner is not None and winner != 0,
            "turns": result["turns"],
            "timed_out": result["timed_out"],
            "digest": result["digest"],
            "ts": round(time.time(), 3),
        })
        done.add(key)
        played += 1
        if not quiet and (played % 50 == 0 or index == total):
            rate = played / max(1e-9, time.perf_counter() - started)
            print(f"  {index}/{total} jobs | {played} played this session "
                  f"| {rate:.0f} games/s", flush=True)
    return played


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def wilson(wins: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score interval — behaves at small n and at rates near 0 or 1,
    where the normal approximation does not."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half) / denom, (centre + half) / denom)


def z_against(wins: int, n: int, p0: float = 0.5) -> float:
    if n <= 0:
        return 0.0
    return (wins - n * p0) / math.sqrt(n * p0 * (1 - p0))


@dataclass
class Tally:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    timeouts: int = 0
    turns: list[int] = field(default_factory=list)

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def games(self) -> int:
        return self.decided + self.draws + self.timeouts

    @property
    def rate(self) -> float:
        return self.wins / self.decided if self.decided else 0.0

    def ci(self) -> tuple[float, float]:
        return wilson(self.wins, self.decided)

    def add(self, row: dict) -> None:
        if row["timed_out"]:
            self.timeouts += 1
            return
        self.turns.append(row["turns"])
        if not row["decided"]:
            self.draws += 1
        elif row["arm_won"]:
            self.wins += 1
        else:
            self.losses += 1


def tally(rows: list[dict], *, arm: str | None = None, cell: str | None = None,
          seeds: set[int] | None = None) -> Tally:
    out = Tally()
    for row in rows:
        if arm is not None and row["arm"] != arm:
            continue
        if cell is not None and row["cell"] != cell:
            continue
        if seeds is not None and row["seed"] not in seeds:
            continue
        out.add(row)
    return out


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _verdict(t: Tally) -> str:
    if t.decided == 0:
        return "NO DATA"
    lo, hi = t.ci()
    if lo <= 0.5 <= hi:
        return "NOT SIGNIFICANT"
    return "better than baseline" if t.rate > 0.5 else "worse than baseline"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _row(label: str, t: Tally) -> str:
    """One arm's line. ``share`` is the guard against a timeout artefact: a turtling
    arm stalls games, and a stalled game leaves the ``rate`` denominator, so a rate
    can rise purely by converting the baseline's wins into timeouts. ``share`` counts
    every game played, timeouts included, and an arm that is really winning more has
    to beat the baseline on both."""
    lo, hi = t.ci()
    total = max(1, t.games)
    return (f"  {label:<44} {t.wins:>4}-{t.losses:<4} {t.decided:>5} "
            f"{t.rate * 100:>6.1f}%  [{lo * 100:>5.1f}, {hi * 100:>5.1f}]  "
            f"{z_against(t.wins, t.decided):>6.2f}  "
            f"{t.wins / total * 100:>5.1f}/{t.losses / total * 100:<5.1f} "
            f"{t.timeouts:>4}  {_verdict(t)}")


def _header() -> str:
    return (f"  {'arm':<44} {'W-L':>9} {'n':>5} {'rate':>7}  {'95% CI':>14}  "
            f"{'z':>6}  {'share a/b':>11} {'t/o':>4}  verdict")


def report(rows: list[dict], arms: list[Arm], cells: list[Cell], baseline: Arm,
           sweep: str) -> None:
    """Every number the run is allowed to be quoted from."""
    rows = [r for r in rows if r.get("kind") == "game" and r.get("sweep") == sweep
            and r.get("stage") == "full"]
    if not rows:
        print("\nNo measurement games in the ledger for this sweep yet.")
        return

    seeds = sorted({r["seed"] for r in rows})
    print(f"\n{'=' * 100}")
    print(f"SWEEP {sweep}   baseline: {baseline.name} — {baseline.label()}")
    print(f"{len(rows)} games | {len(seeds)} seeds | {len(cells)} cells "
          f"| mirrored duels (each seed played both seatings)")
    print(f"{'=' * 100}")

    print("\nPOOLED OVER ALL CELLS")
    print(_header())
    pooled = {}
    for arm in arms:
        t = tally(rows, arm=arm.name)
        pooled[arm.name] = t
        print(_row(f"{arm.name}  [{arm.label()}]", t))

    print("\nPAIRWISE (95% CI overlap — a conservative test: non-overlapping proves a\n"
          "          difference, overlapping does not prove there is none)")
    any_pair = False
    for a, b in itertools.combinations(arms, 2):
        ta, tb = pooled[a.name], pooled[b.name]
        if ta.decided == 0 or tb.decided == 0:
            continue
        any_pair = True
        mark = "NOT SIGNIFICANT" if _overlap(ta.ci(), tb.ci()) else "separated"
        print(f"  {a.name} ({ta.rate * 100:.1f}%) vs {b.name} ({tb.rate * 100:.1f}%): {mark}")
    if not any_pair:
        print("  (only one arm)")

    print("\nPER CELL")
    for cell in cells:
        ckey = cell.key()
        sub = [r for r in rows if r["cell"] == ckey]
        if not sub:
            continue
        t_all = Tally()
        for r in sub:
            t_all.add(r)
        share = t_all.timeouts / max(1, t_all.games)
        note = "  <-- TIMEOUT-DOMINATED, rates here are a biased sample" if share > 0.5 else ""
        print(f"\n  cell {ckey}  ({t_all.games} games, {share * 100:.0f}% timed out){note}")
        print(_header())
        for arm in arms:
            print(_row(arm.name, tally(sub, arm=arm.name, cell=ckey)))

    _replication(rows, arms, seeds)


def _replication(rows: list[dict], arms: list[Arm], seeds: list[int]) -> None:
    """Does each arm say the same thing at two sample sizes?

    Three readings per arm: the first half of the seeds, the second half (an
    independent sample of the same size), and everything pooled. A finding counts
    as reproduced only when both halves point the same way *and* the pooled
    interval excludes 50%.
    """
    if len(seeds) < 4:
        print("\nREPLICATION: not enough seeds to split (need 4+)")
        return
    cut = len(seeds) // 2
    first, second = set(seeds[:cut]), set(seeds[cut:])
    print(f"\nREPLICATION — half A ({len(first)} seeds) vs half B ({len(second)} seeds) "
          f"vs pooled ({len(seeds)} seeds)")
    print(f"  {'arm':<20} {'half A':>16} {'half B':>16} {'pooled':>16}   status")
    for arm in arms:
        ta = tally(rows, arm=arm.name, seeds=first)
        tb = tally(rows, arm=arm.name, seeds=second)
        tp = tally(rows, arm=arm.name)
        if min(ta.decided, tb.decided, tp.decided) == 0:
            status = "NO DATA"
        else:
            signs = {_sign(ta.rate), _sign(tb.rate), _sign(tp.rate)}
            lo, hi = tp.ci()
            if lo <= 0.5 <= hi:
                status = "NOT SIGNIFICANT (pooled interval spans 50%)"
            elif len(signs) > 1 or 0 in signs:
                status = "NOT REPRODUCED (the halves disagree in direction)"
            else:
                status = "REPRODUCED"
        print(f"  {arm.name:<20} {_cell(ta):>16} {_cell(tb):>16} {_cell(tp):>16}   {status}")
    print("\n  Only a REPRODUCED row may be quoted as a tuning conclusion.")


def _sign(rate: float) -> int:
    if rate > 0.5:
        return 1
    return -1 if rate < 0.5 else 0


def _cell(t: Tally) -> str:
    return f"{t.rate * 100:5.1f}% ({t.wins}-{t.losses})" if t.decided else "     - (0-0)"


# --------------------------------------------------------------------------- #
# Smoke — the gate every full run has to pass through
# --------------------------------------------------------------------------- #
def smoke(arms: list[Arm], baseline: Arm, cells: list[Cell], *, sweep: str,
          max_turns: int, max_timeout_rate: float, ledger: Ledger,
          done: set) -> bool:
    """20 games per arm, plus a null control of the same size, plus the checks
    that say whether any of it can be believed."""
    print(f"\n{'=' * 100}\nSMOKE  sweep {sweep}\n{'=' * 100}")

    # Enough seeds that every (cell, arm) gets SMOKE_GAMES_PER_ARM jobs, then the
    # list is truncated to exactly that many per arm.
    per_seed = len(cells) * 2
    need = math.ceil(SMOKE_GAMES_PER_ARM / per_seed)
    seeds = [SMOKE_SEED_BASE + i for i in range(need)]

    control = Arm(NULL, baseline.strategy, baseline.params, baseline.consts)
    all_arms = [control] + arms
    trimmed: list[tuple] = []
    counts: dict[str, int] = {a.name: 0 for a in all_arms}
    for job in jobs(cells, all_arms, seeds):
        arm = job[1]
        if counts[arm.name] >= SMOKE_GAMES_PER_ARM:
            continue
        counts[arm.name] += 1
        trimmed.append(job)

    print(f"{len(trimmed)} games: {SMOKE_GAMES_PER_ARM} per arm across "
          f"{len(cells)} cell(s), plus the null control")
    rev = code_rev()
    run(trimmed, ledger, done, sweep=sweep, stage="smoke", baseline=baseline,
        max_turns=max_turns, rev=rev, quiet=True)
    if _STOPPING:
        print("\nSmoke was interrupted — nothing recorded as passing.")
        return False

    rows = [r for r in ledger.read()
            if r.get("kind") == "game" and r.get("sweep") == sweep
            and r.get("stage") == "smoke"]
    ok = True

    # 1. The null control must read exactly 50.0%: with one config in both seats
    #    the two mirrors are the same game, so one credits each side. Anything
    #    else means a bot is not deterministic or the seat crediting is wrong.
    null_rows = [r for r in rows if r["arm"] == NULL]
    null = tally(null_rows)
    print(f"\n[1] null control (baseline vs itself): {null.wins}-{null.losses} "
          f"= {null.rate * 100:.1f}% of {null.decided} decided")
    if null.decided == 0:
        print("    FAIL: the null control decided no games — no usable measurement here")
        ok = False
    elif abs(null.rate - 0.5) > 1e-9:
        print("    FAIL: the mirror did not cancel. Either a bot draws from "
              "outside state.rng,\n          or the seat crediting is wrong. "
              "Do not trust any arm from this run.")
        ok = False
    else:
        print("    pass — the mirror cancels seat advantage exactly")

    # 2. Determinism: replay one game and demand the same board.
    print("\n[2] determinism (one game replayed)")
    probe = trimmed[0]
    cell, arm, seed, mirror = probe
    seats, _ = _seats(baseline if arm.name == NULL else arm, baseline, mirror)
    again = play_duel(cell, seed, seats, max_turns)
    recorded = next((r for r in rows if r["cell"] == cell.key() and r["arm"] == arm.name
                     and r["seed"] == seed and r["mirror"] == mirror), None)
    if recorded is None:
        print("    FAIL: the probe game is not in the ledger")
        ok = False
    elif again["digest"] != recorded["digest"]:
        print(f"    FAIL: replaying {arm.name} seed {seed} gave a different board "
              f"({again['digest']} != {recorded['digest']})")
        ok = False
    else:
        print(f"    pass — {arm.name} seed {seed} rebuilt board {again['digest']}")

    # 3. Every arm must actually change the game. An arm whose every board is
    #    the null's is inert *in these cells* — the knob is unreachable here, and
    #    a 50% reading from it would be a measurement of nothing.
    print("\n[3] arms are live (boards differ from the null's)")
    null_by_key = {(r["cell"], r["seed"]): r["digest"] for r in null_rows}
    for arm in arms:
        mine = [r for r in rows if r["arm"] == arm.name]
        differing = sum(1 for r in mine
                        if null_by_key.get((r["cell"], r["seed"])) != r["digest"])
        if not mine:
            print(f"    FAIL: {arm.name} played no smoke games")
            ok = False
        elif differing == 0:
            print(f"    FAIL: {arm.name} is INERT — all {len(mine)} boards are "
                  f"bit-identical to the null.\n          The knob does nothing in "
                  f"these cells; measuring it here can only return noise.")
            ok = False
        else:
            print(f"    pass — {arm.name}: {differing}/{len(mine)} boards differ")

    # 4. A cell that mostly times out is not a measurement cell.
    print(f"\n[4] cells decide games (timeout rate under {max_timeout_rate * 100:.0f}%)")
    for cell in cells:
        sub = [r for r in rows if r["cell"] == cell.key()]
        if not sub:
            continue
        t = Tally()
        for r in sub:
            t.add(r)
        share = t.timeouts / max(1, t.games)
        # Tested on the interval, not the point estimate: a smoke is ~40 games a
        # cell, where a true 30% rate reads anywhere from 17% to 47%. A cell only
        # fails when the whole interval clears the threshold; one that merely
        # might be over it is flagged for the report to re-check at full size.
        lo, _hi = wilson(t.timeouts, t.games)
        if lo > max_timeout_rate:
            print(f"    FAIL: {cell.key()} timed out in {t.timeouts}/{t.games} games "
                  f"({share * 100:.0f}%, 95% CI from {lo * 100:.0f}%).\n          Rates "
                  f"from a cell like this are 'of the games that ended', a biased sample.")
            ok = False
        elif share > max_timeout_rate:
            print(f"    warn — {cell.key()}: {t.timeouts}/{t.games} timed out "
                  f"({share * 100:.0f}%), but the 95% CI reaches down to {lo * 100:.0f}% "
                  f"—\n           too few games to call it. The per-cell report re-checks "
                  f"this at full size.")
        else:
            print(f"    pass — {cell.key()}: {t.timeouts}/{t.games} timed out "
                  f"({share * 100:.0f}%)")

    # 5. No process-name matching anywhere in this file.
    print("\n[5] no process-name matching in the harness")
    offenders = forbidden_process_patterns()
    if offenders:
        print(f"    FAIL: {', '.join(offenders)}")
        ok = False
    else:
        print("    pass — process control is by PID file only")

    if ok:
        ledger.append({"kind": "smoke_pass", "sweep": sweep, "rev": rev,
                       "games": len(rows), "ts": round(time.time(), 3)})
        print(f"\nSMOKE PASSED — sweep {sweep} is cleared for a full run.\n")
    else:
        print("\nSMOKE FAILED — no full run will be allowed for this sweep.\n")
    return ok


def forbidden_process_patterns() -> list[str]:
    """Names this file must never contain: any of them could match the harness's
    own process, or the shell that launched it. Assembled from fragments so the
    check does not trip over itself."""
    banned = ["pg" + "rep", "pk" + "ill", "kill" + "all", "kill -9"]
    source = Path(__file__).read_text().splitlines()
    guard = [i for i, line in enumerate(source) if "banned = [" in line]
    hits = []
    for index, line in enumerate(source):
        if index in guard:
            continue
        for word in banned:
            if word in line:
                hits.append(f"line {index + 1} contains {word!r}")
    return hits


def smoke_passed(rows: list[dict], sweep: str) -> bool:
    return any(r.get("kind") == "smoke_pass" and r.get("sweep") == sweep for r in rows)


# --------------------------------------------------------------------------- #
# Self-test — the harness measured against itself, no game engine involved
# --------------------------------------------------------------------------- #
def self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, passed, detail))

    lo, hi = wilson(50, 100)
    check("wilson brackets the point estimate", lo < 0.5 < hi, f"[{lo:.4f}, {hi:.4f}]")
    check("wilson narrows with n", (wilson(500, 1000)[1] - wilson(500, 1000)[0])
          < (hi - lo))
    check("wilson survives n=0", wilson(0, 0) == (0.0, 1.0))
    check("wilson survives a 0% rate", 0.0 <= wilson(0, 30)[0] <= wilson(0, 30)[1] <= 1.0)
    check("z of a dead heat is 0", abs(z_against(50, 100)) < 1e-12)
    check("z of 60/100 is 2.0", abs(z_against(60, 100) - 2.0) < 1e-9)
    check("overlapping intervals detected", _overlap((0.4, 0.6), (0.55, 0.7)))
    check("disjoint intervals detected", not _overlap((0.4, 0.5), (0.55, 0.7)))

    t = Tally()
    for row in [{"timed_out": False, "decided": True, "arm_won": True, "turns": 10},
                {"timed_out": False, "decided": True, "arm_won": False, "turns": 12},
                {"timed_out": False, "decided": False, "arm_won": False, "turns": 9},
                {"timed_out": True, "decided": False, "arm_won": False, "turns": 600}]:
        t.add(row)
    check("tally splits wins/losses/draws/timeouts",
          (t.wins, t.losses, t.draws, t.timeouts) == (1, 1, 1, 1))
    check("a draw is excluded from the denominator", t.decided == 2)

    check("verdict labels a spanning interval", _verdict(t) == "NOT SIGNIFICANT")
    big = Tally(wins=700, losses=300)
    check("verdict labels a separated interval", _verdict(big) == "better than baseline")

    a = parse_arm("x:expand_margin=1.1", "heuristic")
    b = parse_arm("y:expand_margin=1.1", "heuristic")
    c = parse_arm("z:expand_margin=1.2", "heuristic")
    check("same config, different name -> same identity", a.identity() == b.identity())
    check("different config -> different identity", a.identity() != c.identity())
    check("int fields stay int", parse_arm("q:reserve_floor=3", "heuristic").ai_params()
          .reserve_floor == 3)
    check("aux keeps an int literal", parse_arm("q:aux=12", "heuristic")
          .ai_params().aux == 12
          and isinstance(parse_arm("q:aux=12", "heuristic").ai_params().aux, int))
    check("aux keeps a float literal",
          isinstance(parse_arm("q:aux=1.5", "heuristic").ai_params().aux, float))

    key_a = sweep_key([a], parse_arm("base:", "heuristic"), [Cell("random", 18)], 600)
    key_b = sweep_key([c], parse_arm("base:", "heuristic"), [Cell("random", 18)], 600)
    key_c = sweep_key([a], parse_arm("base:", "heuristic"), [Cell("random", 24)], 600)
    check("sweep key moves with the arms", key_a != key_b)
    check("sweep key moves with the cells", key_a != key_c)

    check("a cell restores every other global",
          Cell("random", 18, (("ship_ly_per_turn", 30.0),)).settings().defender_advantage
          == Settings().defender_advantage)

    check("no process-name matching in this file", not forbidden_process_patterns(),
          "; ".join(forbidden_process_patterns()))

    check("a stopped pid is not alive", not _alive(2 ** 22 - 1, None))
    check("this process is alive", _alive(os.getpid(), _start_token(os.getpid())))
    check("a start token exists on this platform", _start_token(os.getpid()) is not None)

    order = jobs([Cell("random", 18), Cell("random", 24)], [a, c], [1, 2])
    check("jobs are seed-outermost", {j[2] for j in order[:8]} == {1})
    check("jobs cover every combination", len(order) == 2 * 2 * 2 * 2)

    width = 100
    print(f"\n{'=' * width}\nSELF-TEST (no games played)\n{'=' * width}")
    failed = 0
    for name, passed, detail in checks:
        mark = "pass" if passed else "FAIL"
        if not passed:
            failed += 1
        print(f"  [{mark}] {name}" + (f"   {detail}" if detail and not passed else ""))
    print(f"\n{len(checks) - failed}/{len(checks)} passed\n")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEFAULT_CELLS = ["random:18:6", "random:24:6", "random:24:12"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arm", action="append", default=[], metavar="NAME:K=V,...",
                   help="a candidate config, e.g. 'loose:expand_margin=1.1'. Repeatable.")
    p.add_argument("--baseline", default="baseline:", metavar="NAME:K=V,...",
                   help="the config every arm is duelled against (default: stock).")
    p.add_argument("--strategy", default="heuristic",
                   help="the bot every arm tunes, unless an arm says strategy=.")
    p.add_argument("--cell", action="append", default=[], metavar="MODE:NODES[:LY]",
                   help=f"a measurement cell. Repeatable. Default: {' '.join(DEFAULT_CELLS)}")
    p.add_argument("--seeds", type=int, default=0,
                   help="how many seeds to measure (each seed is 2 games per arm).")
    p.add_argument("--seed-base", type=int, default=0, help="first measurement seed.")
    p.add_argument("--max-turns", type=int, default=600)
    p.add_argument("--max-timeout-rate", type=float, default=0.5,
                   help="a smoke cell timing out above this fraction is unusable.")
    p.add_argument("--smoke", action="store_true",
                   help=f"run the {SMOKE_GAMES_PER_ARM}-game gate and the harness checks.")
    p.add_argument("--report", action="store_true", help="re-print from the ledger only.")
    p.add_argument("--self-test", action="store_true", help="check the harness, play nothing.")
    p.add_argument("--stop", action="store_true", help="stop the running sweep by PID file.")
    p.add_argument("--ledger", type=Path, default=LEDGER)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.stop:
        return stop_running()
    if args.self_test:
        return self_test()

    ai.load_models()
    ai.set_budget_scale(BUDGET_SCALE)

    baseline = parse_arm(args.baseline, args.strategy)
    arms = [parse_arm(spec, args.strategy) for spec in args.arm]
    cells = [parse_cell(spec) for spec in (args.cell or DEFAULT_CELLS)]
    validate(arms, baseline, cells)

    key = sweep_key(arms, baseline, cells, args.max_turns)
    ledger = Ledger(args.ledger)
    existing = ledger.read()

    if args.report:
        report(existing, arms, cells, baseline, key)
        return 0

    if not args.smoke and args.seeds <= 0:
        _fatal("nothing to do", "pass --smoke, or --seeds N for a full run, or --report")

    done = {(r["cell"], r["arm"], r["seed"], r["mirror"]) for r in existing
            if r.get("kind") == "game" and r.get("sweep") == key}

    pid = PidFile()
    pid.acquire(key, sys.argv)
    _install_signals()
    try:
        with ledger:
            if args.smoke:
                passed = smoke(arms, baseline, cells, sweep=key,
                               max_turns=args.max_turns,
                               max_timeout_rate=args.max_timeout_rate,
                               ledger=ledger, done=done)
                if not passed:
                    return 1
                existing = ledger.read()

            if args.seeds > 0:
                if not smoke_passed(existing, key):
                    _fatal(
                        f"no passing smoke recorded for sweep {key}",
                        "A full run is only allowed behind a smoke that passed for this",
                        "exact set of arms, cells and code revision. Run:",
                        "  uv run python tools/sweep.py ...same flags... --smoke",
                    )
                seeds = list(range(args.seed_base, args.seed_base + args.seeds))
                todo = jobs(cells, arms, seeds)
                pending = sum(1 for j in todo
                              if (j[0].key(), j[1].name, j[2], j[3]) not in done)
                print(f"\nFULL RUN  sweep {key}")
                print(f"{len(todo)} games planned | {len(todo) - pending} already in the "
                      f"ledger | {pending} to play")
                run(todo, ledger, done, sweep=key, stage="full", baseline=baseline,
                    max_turns=args.max_turns, rev=code_rev())
    finally:
        pid.release()

    report(ledger.read(), arms, cells, baseline, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
