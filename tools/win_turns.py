#!/usr/bin/env python3
"""How many turns a bot takes to win, against bots it nearly always beats.

    uv run python tools/win_turns.py                     # the top three against the weak bots
    uv run python tools/win_turns.py --quick             # three cells, 25 seeds: a screen
    uv run python tools/win_turns.py --bot knower:3 --label base              # one bot, a named ledger
    uv run python tools/win_turns.py --bot knower:3 --label try --base base   # paired against it
    uv run python tools/win_turns.py --report            # re-print from the ledgers

Against heuristic, rusherplus and thinker the top bots win nearly every game, so
a win rate cannot tell a change to them from noise. The board ranks a result by
the win, then fewer turns, then fewer ships lost, and turns are where people beat
the bots. This measures them.

A seat is a strategy, or ``name:aux`` for a stop of its knob (``knower:3`` is
Council, ``actuary:3`` Hunt). An opponent line-up is one seat, which takes every
other seat, or one seat per other seat joined by ``+`` (``heuristic+rusherplus``
on a three-seat cell); a line-up plays only the cells it fits. Every seed is
played with the bot in each seat in turn, the line-up rotated with it, so start
position cancels. Cells are ``mode:nodes:ly:players``. Wall-clock guards are
lifted, so a game is the same on any machine.

Each arm reads, per cell and line-up: the share of games won and how many hit
``MAX_TURNS``; the turns to win at the 25th, 50th and 75th percentiles, where a
game not won counts as slower than any win (``—`` once the percentile passes the
share won); and, over the games won, medians of the turn the bot first held three
quarters of the players' income (`tools/playstyle.py`'s `shares`, neutral systems
left out), a 3:1 lead over every rival together, then the turns from 3:1 to 9:1
and from 9:1 to the win, the ships lost, and ``low``, the bot's lowest share of
the players' income on the way (a half, in a duel, is never behind).

Two arms are paired game by game (same map, same seat, same opponents): every two
bots in one run, or each bot against its own rows in another ledger (``--base``).
``beats`` scores a pair as the board ranks the two results, 1, 0.5 or 0; ``Δwon``
is the difference in games won; ``Δturns`` is the geometric mean ratio of turns
over the pairs both won, as a percentage (negative is faster). Each is averaged
per seed and z is taken over seeds. Under the table, the pairs both won's mean
difference in turns is split by phase: before 3:1, 3:1 to 9:1, 9:1 to the win.

Rows append to ``results/win_turns/<label>/<bot>.jsonl`` and a rerun plays only
the games missing. Every row carries ``rev``, a digest of the code that decides a
game (`play_rev`). The label defaults to it, so a changed tree starts a ledger of
its own, and a named ledger refuses rows from another tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, replay  # noqa: E402
from starconquest.model import AiParams  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402
from tools.playstyle import shares  # noqa: E402

RESULTS = ROOT / "results" / "win_turns"
SEED_BASE = 7_000_000
SEEDS = 50
MAX_TURNS = 600
BOTS = ("knower:3", "actuary:3", "marshal")
OPPONENTS = ("heuristic", "rusherplus", "thinker")
# tools/evo_bench.py's cells: where people play (7:6:2, 11:6:3) and the node x
# speed corners.
CELLS = (
    "random:7:6:2",
    "random:11:6:3",
    "random:13:3:2",
    "random:18:6:2",
    "random:24:3:2",
    "random:18:18:2",
)
QUICK_CELLS = ("random:7:6:2", "random:11:6:3", "random:18:6:2")
QUICK_SEEDS = 25
# Row field -> the share of the players' income the bot first held at that turn:
# a 3:1 lead over every rival together, and 9:1.
MILESTONES = {"to75": 0.75, "to90": 0.9}
# What a row holds. Folded into `play_rev`, so a ledger never mixes two shapes:
# bump it whenever a row's fields change.
SHAPE = 1
# The core modules that decide a game here, beside every `models/` file.
# `replay` is one because `replay.reseed` derives each turn's rng.
_PLAY_MODULES = ("ai", "combat", "config", "custommap", "engine", "geometry",
                 "mapgen", "model", "replay", "settings", "starnames")


@dataclass(frozen=True)
class Seat:
    name: str
    aux: float | None = None

    @classmethod
    def parse(cls, spec: str) -> Seat:
        name, _, aux = spec.partition(":")
        return cls(name, float(aux) if aux else None)

    def __str__(self) -> str:
        return self.name if self.aux is None else f"{self.name}:{self.aux:g}"

    @property
    def params(self) -> AiParams:
        return AiParams() if self.aux is None else AiParams(aux=self.aux)

    def describe(self) -> str:
        """``knower:3 (Oracle: Council)``, or the bare seat for a bot with no
        named stops."""
        spec = ai.aux_spec(self.name)
        stop = ai.aux_stop_name(self.name, config.AI_AUX if self.aux is None else self.aux)
        return f"{self} ({spec[0]}: {stop})" if spec and stop else str(self)


@dataclass(frozen=True)
class Cell:
    mode: str
    nodes: int
    ly: float
    players: int

    @classmethod
    def parse(cls, spec: str) -> Cell:
        parts = spec.split(":")
        mode, nodes, ly, players = parts + ["random", "7", "6", "2"][len(parts):]
        return cls(mode, int(nodes), float(ly), int(players))

    def __str__(self) -> str:
        return f"{self.mode}:{self.nodes}:{self.ly:g}:{self.players}"


def lineup(spec: str) -> tuple[Seat, ...]:
    return tuple(Seat.parse(s) for s in spec.split("+"))


def lineup_name(seats: tuple[Seat, ...]) -> str:
    return "+".join(map(str, seats))


def rivals(opponents: tuple[Seat, ...], cell: Cell) -> list[Seat] | None:
    """The seats a line-up fills beside the bot on ``cell``, or None if it does
    not fit there."""
    if len(opponents) == 1:
        return [opponents[0]] * (cell.players - 1)
    return list(opponents) if len(opponents) == cell.players - 1 else None


def seatings(bot: Seat, others: list[Seat]) -> list[tuple[Seat, ...]]:
    """The bot and its rivals rotated through the seats: entry ``k`` has the
    bot in seat ``k + 1``."""
    ring = [bot, *others]
    n = len(ring)
    return [tuple(ring[(i - k) % n] for i in range(n)) for k in range(n)]


@dataclass(frozen=True)
class Game:
    bot: Seat
    cell: Cell
    vs: str
    seed: int
    seats: tuple[Seat, ...]
    seat: int

    @property
    def key(self) -> tuple:
        return str(self.cell), self.vs, self.seed, self.seat


def schedule(bots: list[Seat], lineups: list[tuple[Seat, ...]], cells: list[Cell],
             seeds: range) -> list[Game]:
    """Every game a run asks for, done or not."""
    games = []
    for bot in bots:
        for cell in cells:
            for lu in lineups:
                others = rivals(lu, cell)
                if others is None:
                    continue
                for seed in seeds:
                    for k, seats in enumerate(seatings(bot, others)):
                        games.append(Game(bot, cell, lineup_name(lu), seed, seats, k + 1))
    return games


# --------------------------------------------------------------------------- #
# Playing
# --------------------------------------------------------------------------- #
def play(game: Game) -> dict:
    """One game, read from the bot's seat: the ledger row, less the tree's stamp."""
    cell = game.cell
    cfg = Settings(mode=cell.mode, nodes=cell.nodes, players=cell.players,
                   ship_ly_per_turn=cell.ly, autoplay=True)
    for i, seat in enumerate(game.seats):
        cfg.ai_strategy[i] = seat.name
        cfg.ai[i] = seat.params
    state = build_state(cfg, game.seed)
    reach: dict[str, int | None] = dict.fromkeys(MILESTONES)
    low = 1.0
    t0 = time.perf_counter()
    while True:
        income = shares(state)[game.seat][0]
        low = min(low, income)
        for mark, share in MILESTONES.items():
            if reach[mark] is None and income >= share:
                reach[mark] = state.turn
        if state.winner is not None or state.turn >= MAX_TURNS:
            break
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    return {"cell": str(cell), "vs": game.vs, "seed": game.seed, "seat": game.seat,
            "seats": [str(s) for s in game.seats], "winner": state.winner,
            "turns": state.turn, "lost": state.players[game.seat].ships_lost, **reach,
            "low": round(low, 3), "secs": round(time.perf_counter() - t0, 2)}


def play_one(game: Game, stamp: dict) -> dict:
    """`play` in a worker, at fixed work, stamped with the tree that played it."""
    if not all(ai.known(s.name) for s in game.seats):
        ai.load_models()
    # Every call: a forked worker would inherit the parent's guards.
    ai.set_budget_scale(math.inf)
    return {**play(game), **stamp}


def play_rev() -> str:
    """A digest of the code that decides a game here, `_PLAY_MODULES` and every
    `models/` file (a rival, or an adviser knower borrows), and of `SHAPE`."""
    digest = hashlib.blake2s(digest_size=4)
    digest.update(f"shape {SHAPE}\0".encode("utf-8"))
    for path in ([ROOT / "starconquest" / f"{name}.py" for name in _PLAY_MODULES]
                 + sorted((ROOT / "models").glob("*.py"))):
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def git_hint() -> str:
    """HEAD's short hash, ``+`` when `models/` or `starconquest/` has changes not
    committed; blank without git. A reader's hint, not an identity: `play_rev` is."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    try:
        dirty = git("status", "--porcelain", "--", "models", "starconquest")
        return git("rev-parse", "--short", "HEAD") + ("+" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""


# --------------------------------------------------------------------------- #
# Ledgers
# --------------------------------------------------------------------------- #
def ledger(label: str, bot: Seat) -> Path:
    return RESULTS / label / f"{str(bot).replace(':', '@')}.jsonl"


def row_key(row: dict) -> tuple:
    return row["cell"], row["vs"], row["seed"], row["seat"]


def load(path: Path) -> list[dict]:
    """A ledger's rows, the first of any game written twice."""
    if not path.exists():
        return []
    rows, seen = [], set()
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row_key(row) not in seen:
                seen.add(row_key(row))
                rows.append(row)
    return rows


def foreign_revs(rows: list[dict], rev: str) -> list[str]:
    """The revs in a ledger other than ``rev``."""
    return sorted({row.get("rev", "?") for row in rows} - {rev})


def shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def provenance(path: Path, rows: list[dict]) -> str:
    """Where a ledger is and which trees played it."""
    trees = sorted({f"rev {r.get('rev', '?')}" + (f", git {r['git']}" if r.get("git") else "")
                    for r in rows})
    return f"{shown(path)}: {len(rows)} games" + (f" ({'; '.join(trees)})" if trees else "")


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def won(row: dict) -> bool:
    return row["winner"] == row["seat"]


def standing(row: dict) -> tuple:
    """A result as the board ranks it, higher is better: a win, then fewer
    turns, then fewer ships lost. Every game not won ranks level."""
    return (1, -row["turns"], -row["lost"]) if won(row) else (0,)


def quantile(xs: list[float], q: float) -> float:
    """Linear between neighbours, and ``inf`` from the first ``inf`` (a game
    not won) on."""
    xs = sorted(xs)
    if not xs:
        return math.nan
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    if h == lo:
        return xs[lo]
    if math.isinf(xs[lo + 1]):
        return math.inf
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])


def median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else math.nan


def mean_z(xs: list[float], fair: float = 0.0) -> tuple[float, float]:
    """The mean and its z against ``fair``, one value per seed."""
    n = len(xs)
    if not n:
        return math.nan, math.nan
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / max(1, n - 1)
    if var > 0:
        return m, (m - fair) / math.sqrt(var / n)
    return m, 0.0 if math.isclose(m, fair, abs_tol=1e-12) else math.copysign(math.inf, m - fair)


@dataclass
class Arm:
    title: str
    rows: list[dict]


def arm_reading(rows: list[dict]) -> dict:
    """What one arm did in one cell against one line-up."""
    wins = [r for r in rows if won(r)]
    turns = [float(r["turns"]) if won(r) else math.inf for r in rows]
    return {
        "games": len(rows),
        "won": len(wins) / len(rows) if rows else math.nan,
        "unfinished": sum(r["winner"] is None for r in rows),
        "p25": quantile(turns, 0.25), "p50": quantile(turns, 0.5), "p75": quantile(turns, 0.75),
        "open": median([r["to75"] for r in wins if r["to75"] is not None]),
        "middle": median([r["to90"] - r["to75"] for r in wins
                          if r["to75"] is not None and r["to90"] is not None]),
        "close": median([r["turns"] - r["to90"] for r in wins if r["to90"] is not None]),
        "lost": median([r["lost"] for r in wins]),
        "low": median([r["low"] for r in wins]),
    }


def phases(row: dict) -> tuple[int, int, int] | None:
    """A win's turns before the 3:1 lead, from 3:1 to 9:1, and from 9:1 on."""
    lead, rout = row.get("to75"), row.get("to90")
    if lead is None or rout is None:
        return None
    return lead, rout - lead, row["turns"] - rout


@dataclass
class Paired:
    """Two arms' games in one group, as per-seed means over the games both
    played, and each pair both won's difference in every phase."""
    pairs: int = 0
    both_won: int = 0
    beats: list[float] = field(default_factory=list)
    dwon: list[float] = field(default_factory=list)
    log_ratio: list[float] = field(default_factory=list)
    phase_gaps: list[tuple[int, ...]] = field(default_factory=list)

    def merge(self, other: Paired) -> None:
        self.pairs += other.pairs
        self.both_won += other.both_won
        self.beats += other.beats
        self.dwon += other.dwon
        self.log_ratio += other.log_ratio
        self.phase_gaps += other.phase_gaps


def pair(a: list[dict], b: list[dict]) -> dict[tuple[str, str], Paired]:
    """``a`` against ``b`` game by game, grouped by (cell, line-up)."""
    other = {row_key(r): r for r in b}
    per_seed: dict[tuple[str, str], dict[int, tuple[list[float], list[float], list[float]]]] = \
        defaultdict(lambda: defaultdict(lambda: ([], [], [])))
    out: dict[tuple[str, str], Paired] = defaultdict(Paired)
    for r in a:
        s = other.get(row_key(r))
        if s is None:
            continue
        group = (r["cell"], r["vs"])
        beats, dwon, log_ratio = per_seed[group][r["seed"]]
        mine, theirs = standing(r), standing(s)
        beats.append(1.0 if mine > theirs else 0.5 if mine == theirs else 0.0)
        dwon.append(float(won(r)) - float(won(s)))
        out[group].pairs += 1
        if won(r) and won(s):
            log_ratio.append(math.log(r["turns"] / s["turns"]))
            out[group].both_won += 1
            mine_phases, their_phases = phases(r), phases(s)
            if mine_phases is not None and their_phases is not None:
                out[group].phase_gaps.append(
                    tuple(x - y for x, y in zip(mine_phases, their_phases)))
    for group, seeds in per_seed.items():
        p = out[group]
        for beats, dwon, log_ratio in seeds.values():
            p.beats.append(sum(beats) / len(beats))
            p.dwon.append(sum(dwon) / len(dwon))
            if log_ratio:
                p.log_ratio.append(sum(log_ratio) / len(log_ratio))
    return dict(out)


def _turns(x: float) -> str:
    return "—" if math.isinf(x) else "-" if math.isnan(x) else f"{x:.0f}" if x == int(x) else f"{x:.1f}"


def _share(x: float) -> str:
    return "-" if math.isnan(x) else f"{x:.0%}"


def _z(z: float) -> str:
    return "-" if math.isnan(z) else f"{z:+.1f}"


def _group(group: tuple[str, str]) -> str:
    return f"{group[0]} vs {group[1]}"


def print_arms(arms: list[Arm], groups: list[tuple[str, str]]) -> None:
    title_w = max(len(a.title) for a in arms)
    print(f"\n{'':<{title_w + 2}}{'games':>6}{'won':>8}{'unf':>5}"
          f"{'p25':>6}{'med':>6}{'p75':>6}{'to 3:1':>8}{'3:1-9:1':>9}{'9:1-win':>9}{'lost':>6}{'low':>6}")
    for group in groups:
        print(_group(group))
        for arm in arms:
            rows = [r for r in arm.rows if (r["cell"], r["vs"]) == group]
            if not rows:
                print(f"  {arm.title:<{title_w}}{'-':>6}")
                continue
            a = arm_reading(rows)
            print(f"  {arm.title:<{title_w}}{a['games']:>6}{a['won']:>8.1%}{a['unfinished']:>5}"
                  f"{_turns(a['p25']):>6}{_turns(a['p50']):>6}{_turns(a['p75']):>6}"
                  f"{_turns(a['open']):>8}{_turns(a['middle']):>9}{_turns(a['close']):>9}"
                  f"{_turns(a['lost']):>6}{_share(a['low']):>6}")


def print_pairs(a: Arm, b: Arm, groups: list[tuple[str, str]]) -> None:
    width = max(len(_group(g)) for g in groups)
    print(f"\n{a.title} against {b.title}, paired by map, seat and line-up")
    print(f"  {'':<{width}}{'pairs':>6}{'beats':>8}{'z':>6}{'Δwon':>8}{'z':>6}"
          f"{'both':>6}{'Δturns':>8}{'z':>6}")
    paired = pair(a.rows, b.rows)
    pooled = Paired()

    def line(name: str, p: Paired) -> None:
        beats, zb = mean_z(p.beats, 0.5)
        dwon, zw = mean_z(p.dwon)
        log_ratio, zt = mean_z(p.log_ratio)
        dturns = "-" if math.isnan(log_ratio) else f"{math.expm1(log_ratio):+.1%}"
        dw = "-" if math.isnan(dwon) else f"{100 * dwon:+.1f}pt"
        bt = "-" if math.isnan(beats) else f"{beats:.1%}"
        print(f"  {name:<{width}}{p.pairs:>6}{bt:>8}{_z(zb):>6}{dw:>8}{_z(zw):>6}"
              f"{p.both_won:>6}{dturns:>8}{_z(zt):>6}")

    for group in groups:
        if group in paired:
            line(_group(group), paired[group])
            pooled.merge(paired[group])
    line("pooled", pooled)
    if pooled.phase_gaps:
        gaps = [sum(col) / len(pooled.phase_gaps) for col in zip(*pooled.phase_gaps)]
        print(f"  mean turns over the {len(pooled.phase_gaps)} pairs both won: {gaps[0]:+.1f} before 3:1, "
              f"{gaps[1]:+.1f} from 3:1 to 9:1, {gaps[2]:+.1f} from 9:1 to the win = {sum(gaps):+.1f}")


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bot", nargs="+", default=list(BOTS), help="bots under test, name[:aux]")
    ap.add_argument("--vs", nargs="+", default=list(OPPONENTS),
                    help="opponent line-ups: name[:aux] for every other seat, or one per seat joined by +")
    ap.add_argument("--cell", action="append", default=[], help="mode:nodes:ly:players; repeatable")
    ap.add_argument("--quick", action="store_true",
                    help=f"screen on {', '.join(QUICK_CELLS)} at {QUICK_SEEDS} seeds")
    ap.add_argument("--seeds", type=int, default=None, help=f"default {SEEDS}, or {QUICK_SEEDS} with --quick")
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--label", default=None, help="ledger name (default: this tree's rev)")
    ap.add_argument("--base", default=None, help="pair each bot against its rows in this ledger")
    ap.add_argument("--report", action="store_true", help="re-print from the ledgers only")
    args = ap.parse_args(argv)

    ai.load_models()
    bots = [Seat.parse(s) for s in args.bot]
    lineups = [lineup(s) for s in args.vs]
    cells = [Cell.parse(c) for c in (args.cell or (QUICK_CELLS if args.quick else CELLS))]
    count = args.seeds if args.seeds is not None else QUICK_SEEDS if args.quick else SEEDS
    seeds = range(args.seed_base, args.seed_base + count)
    unknown = sorted({s.name for s in [*bots, *(s for lu in lineups for s in lu)] if not ai.known(s.name)})
    if unknown:
        ap.error(f"unknown bot {', '.join(unknown)}; known: {', '.join(ai.available_strategies())}")
    if len(set(map(str, bots))) < len(bots):
        ap.error("a bot is named twice")
    for lu in lineups:
        if all(rivals(lu, c) is None for c in cells):
            ap.error(f"line-up {lineup_name(lu)} fits none of the cells")

    rev = play_rev()
    label = args.label or rev
    games = schedule(bots, lineups, cells, seeds)
    wanted = {str(b): {g.key for g in games if g.bot == b} for b in bots}
    rows = {str(b): load(ledger(label, b)) for b in bots}
    if not args.report:
        for b in bots:
            foreign = foreign_revs(rows[str(b)], rev)
            if foreign:
                ap.error(f"{ledger(label, b)} holds games played by rev {', '.join(foreign)}, and "
                         f"this tree is rev {rev}: name a new --label, or delete the ledger")
    base: dict[str, list[dict]] = {}
    if args.base:
        for b in bots:
            path = ledger(args.base, b)
            if not path.exists():
                ap.error(f"no ledger {path}")
            base[str(b)] = load(path)

    stamp = {"rev": rev, "git": git_hint()}
    print(f"this tree: rev {rev}" + (f", git {stamp['git']}" if stamp["git"] else "")
          + f" · seeds {seeds.start}+{len(seeds)}")
    for b in bots:
        print(f"  {b.describe():<28} {provenance(ledger(label, b), rows[str(b)])}")
        if args.base:
            print(f"  {'':<28} {provenance(ledger(args.base, b), base[str(b)])}")

    if not args.report:
        done = {str(b): {row_key(r) for r in rows[str(b)]} for b in bots}
        todo = [g for g in games if g.key not in done[str(g.bot)]]
        print(f"{len(todo)} games to play ({len(games) - len(todo)} in the ledgers), {args.jobs} workers")
        if todo:
            (RESULTS / label).mkdir(parents=True, exist_ok=True)
            handles = {str(b): ledger(label, b).open("a") for b in bots}
            secs: dict[str, list[float]] = defaultdict(list)
            t0 = time.perf_counter()
            try:
                with ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
                    futures = {pool.submit(play_one, g, stamp): g for g in todo}
                    for i, fut in enumerate(as_completed(futures), 1):
                        bot = str(futures[fut].bot)
                        row = fut.result()
                        handles[bot].write(json.dumps(row) + "\n")
                        handles[bot].flush()
                        rows[bot].append(row)
                        secs[bot].append(row["secs"])
                        if i % 100 == 0 or i == len(todo):
                            print(f"  {i}/{len(todo)}  {time.perf_counter() - t0:.0f}s", flush=True)
            finally:
                for fh in handles.values():
                    fh.close()
            for bot, xs in secs.items():
                print(f"  {bot}: {len(xs)} games, median {median(xs):.2f} s, {sum(xs):.0f} CPU s")

    titled = bool(args.base)
    arms: list[Arm] = []
    pairs: list[tuple[Arm, Arm]] = []
    for b in bots:
        mine = Arm(f"{b} [{label}]" if titled else str(b),
                   [r for r in rows[str(b)] if row_key(r) in wanted[str(b)]])
        arms.append(mine)
        if args.base:
            theirs = Arm(f"{b} [{args.base}]", [r for r in base[str(b)] if row_key(r) in wanted[str(b)]])
            arms.append(theirs)
            pairs.append((mine, theirs))
    if not args.base:
        pairs = [(arms[i], arms[j]) for i in range(len(arms)) for j in range(i + 1, len(arms))]
    if not any(arm.rows for arm in arms):
        print(f"\nno games in {shown(RESULTS / label)}/ for this run: play them first"
              + ("" if args.label else ", or name a ledger with --label"))
        return 1

    groups = [(str(c), lineup_name(lu)) for c in cells for lu in lineups if rivals(lu, c) is not None]
    print_arms(arms, groups)
    for a, b in pairs:
        print_pairs(a, b, groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
