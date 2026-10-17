#!/usr/bin/env python3
"""evolved's gate: one bot against a fixed set of opponents, paired by seed.

    uv run python tools/evo_bench.py --cell random:7:6:2 --cell random:11:6:2 --seeds 200
    uv run python tools/evo_bench.py --bot evolved --vs actuary --seeds 200
    uv run python tools/evo_bench.py --null --seeds 50      # the 50% control

Every duel seed is played twice with the seats swapped on the same map, so
first-position advantage cancels per seed; a seed scores 1, 0.5 or 0 and the z
is taken over seed scores, not games. A free-for-all cell (``players`` > 2)
plays every rotation of the bot plus opponents on each seed and scores the
bot's win share against a fair share of ``1/players``.

The default cells are frozen on purpose: the setups people post most (2
seats on 7 nodes, 3 on 11) plus the node x speed
corners `docs/design/bots.md` asks every margin to be swept across. Seeds start
at ``SEED_BASE`` so they never overlap the ladder runs in the design docs.
By default every wall-clock guard is lifted (``ai.set_budget_scale(inf)``), so a
bot does its fixed amount of work — candidates times horizon — and the result is
the same on any machine, core or load, under CPython or PyPy. Whether that work
fits the browser's budget is a separate check, timed in the web build.
``--budget-scale 1`` restores the in-game guards; those ledgers live apart, in
``results/evolved/bench/clock/``.

``--weights FILE`` benches evolved with a weights file (any layout, carried
over by name) instead of the shipped one, which is never touched; ``--label``
names the ledger ``<bot>_<label>.jsonl``.

Results append to ``results/evolved/bench/<bot>.jsonl``, one finished game per line,
and a rerun skips games already there.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

RESULTS = ROOT / "results" / "evolved" / "bench"
SEED_BASE = 2_800_000
MAX_TURNS = 600

# mode:nodes:ly:players. The first two are where public games are played.
CELLS = (
    "random:7:6:2",
    "random:11:6:3",
    "random:13:3:2",
    "random:18:6:2",
    "random:24:3:2",
    "random:18:18:2",
)
# The quick screen: the three cheapest cells (about a quarter of a full run's
# CPU), for deciding which ideas earn a full run. A screen reuses the full run's
# seeds, so a screened arm is paired against any ledger that has them; the full
# run on fresh seeds (``--seed-base``) is the validation, since a screen that
# picks the best of many ideas on one seed set will flatter the winner.
QUICK_CELLS = ("random:7:6:2", "random:11:6:3", "random:18:6:2")
QUICK_SEEDS = 25
OPPONENTS = ("marshal", "actuary", "thinker")


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


@dataclass(frozen=True)
class Cell:
    mode: str
    nodes: int
    ly: float
    players: int

    @classmethod
    def parse(cls, spec: str) -> Cell:
        mode, nodes, ly, players = (spec.split(":") + ["6", "2"])[:4]
        return cls(mode, int(nodes), float(ly), int(players))

    def __str__(self) -> str:
        return f"{self.mode}:{self.nodes}:{self.ly:g}:{self.players}"


_loaded_weights: str | None = None


def use_weights(path: str | None) -> None:
    """In this worker, play evolved from ``path`` (its weights by name, carried
    into the current layout) in place of the shipped weights file."""
    global _loaded_weights
    if not path or path == _loaded_weights:
        return
    from tools import evo_train
    named = evo_train.read_named(Path(path))
    vec = [named.get((g, f), 0.0) for g, feats in evo_train._layout() for f in feats]
    ai.register("evolved", evo_train._module().make_decide(vec))
    _loaded_weights = path


def play_one(cell: Cell, seed: int, seats: tuple[Seat, ...], scale: float,
             weights: str | None = None) -> dict:
    """One game; ``seats[i]`` holds pid ``i + 1``. Returns the ledger row."""
    if not ai.STRATEGIES.get(seats[0].name) or "thinker" not in ai.STRATEGIES:
        ai.load_models()
    use_weights(weights)
    # Every call, not only on first load: a forked worker (Linux) inherits the
    # parent's loaded models with their guards untouched, and would play under
    # the wall clock. That once made a game differ between two machines.
    ai.set_budget_scale(scale)
    cfg = Settings(mode=cell.mode, nodes=cell.nodes, players=cell.players,
                   ship_ly_per_turn=cell.ly, autoplay=True)
    state = build_state(cfg, seed)
    for pid, seat in enumerate(seats, start=1):
        player = state.players[pid]
        player.ai_strategy = seat.name
        player.ai_params = replace(player.ai_params,
                                   aux=1.0 if seat.aux is None else seat.aux)
    t0 = time.perf_counter()
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    return {"cell": str(cell), "seed": seed, "seats": [str(s) for s in seats],
            "winner": state.winner, "turns": state.turn,
            "secs": round(time.perf_counter() - t0, 2)}


def schedule(bot: Seat, opp: Seat, cell: Cell, seeds: range):
    """Every (seed, seating) this pairing plays in this cell."""
    for seed in seeds:
        if cell.players == 2:
            seatings = [(bot, opp), (opp, bot)]
        else:
            # The bot against (players - 1) copies of the opponent, in each seat.
            seatings = [tuple(bot if i == k else opp for i in range(cell.players))
                        for k in range(cell.players)]
        for seats in seatings:
            yield seed, seats


def _key(cell: str, seed: int, seats) -> str:
    return f"{cell}|{seed}|{','.join(map(str, seats))}"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(rows: list[dict], bot: str, opp: str, cell: Cell) -> tuple[float, float, int, int]:
    """``(rate, z, seeds, unfinished)`` for ``bot`` against ``opp`` in ``cell``.

    A seed's score is the bot's mean result over its seatings, a win 1, a game
    left unfinished at ``MAX_TURNS`` a half; z is against the fair share, taken
    over seeds so the pairing on one map is respected.
    """
    by_seed: dict[int, list[float]] = {}
    unfinished = 0
    for r in rows:
        if r["cell"] != str(cell) or set(r["seats"]) != {bot, opp}:
            continue
        if r["winner"] is None:
            unfinished += 1
            result = 1.0 / cell.players
        else:
            result = 1.0 if r["seats"][r["winner"] - 1] == bot else 0.0
        by_seed.setdefault(r["seed"], []).append(result)
    full = cell.players if cell.players > 2 else 2
    per = [sum(v) / len(v) for v in by_seed.values() if len(v) == full]
    if not per:
        return float("nan"), float("nan"), 0, unfinished
    n = len(per)
    mean = sum(per) / n
    var = sum((x - mean) ** 2 for x in per) / max(1, n - 1)
    fair = 1.0 / cell.players
    z = (mean - fair) / math.sqrt(var / n) if var > 0 else (0.0 if mean == fair else math.inf)
    return mean, z, n, unfinished


def report(rows: list[dict], bot: Seat, opps: list[Seat], cells: list[Cell]) -> None:
    print(f"\n{bot} — seed-paired win share (fair = 1/players), z over seeds")
    head = f"{'cell':<18}" + "".join(f"{str(o):>22}" for o in opps)
    print(head)
    for cell in cells:
        line = f"{str(cell):<18}"
        for opp in opps:
            rate, z, n, unf = score(rows, str(bot), str(opp), cell)
            cellval = "—" if n == 0 else f"{rate:5.1%} z{z:+5.1f} n{n}" + (f" u{unf}" if unf else "")
            line += f"{cellval:>22}"
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bot", default="evolved", help="name[:aux] of the bot under test")
    ap.add_argument("--vs", nargs="*", default=list(OPPONENTS), help="opponents, name[:aux]")
    ap.add_argument("--cell", action="append", default=[], help="mode:nodes:ly:players")
    ap.add_argument("--seeds", type=int, default=None, help="default 50, or 25 with --quick")
    ap.add_argument("--quick", action="store_true",
                    help="screen on QUICK_CELLS only; rerun with more --seeds to extend")
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--budget-scale", type=float, default=math.inf,
                    help="wall-clock guard multiplier; inf (default) = fixed work")
    ap.add_argument("--null", action="store_true",
                    help="control: the first opponent against itself (must read 50%%)")
    ap.add_argument("--report", action="store_true", help="re-print from the ledger only")
    ap.add_argument("--weights", default=None,
                    help="play evolved from this weights file (the shipped one is untouched)")
    ap.add_argument("--label", default=None, help="ledger <bot>_<label>.jsonl")
    args = ap.parse_args()
    if args.weights:
        args.weights = str(Path(args.weights).resolve())
        if not Path(args.weights).exists():
            raise SystemExit(f"no weights file {args.weights}")

    ai.load_models()
    bot = Seat.parse(args.bot)
    opps = [Seat.parse(s) for s in args.vs]
    if args.null:
        bot = opps[0]
        opps = [Seat(opps[0].name + "", opps[0].aux)]
    if args.seeds is None:
        args.seeds = QUICK_SEEDS if args.quick else 50
    cells = [Cell.parse(c) for c in (args.cell or (QUICK_CELLS if args.quick else CELLS))]
    for s in [bot, *opps]:
        if s.name not in ai.STRATEGIES:
            raise SystemExit(f"unknown bot {s.name!r}; known: {', '.join(sorted(ai.STRATEGIES))}")

    folder = RESULTS if math.isinf(args.budget_scale) else RESULTS / "clock"
    folder.mkdir(parents=True, exist_ok=True)
    stem = str(bot).replace(':', '_') + (f"_{args.label}" if args.label else "")
    ledger = folder / f"{stem}.jsonl"
    if args.null:
        ledger = folder / f"null_{str(bot).replace(':', '_')}.jsonl"
    rows = load(ledger)
    if not args.report:
        done = {_key(r["cell"], r["seed"], r["seats"]) for r in rows}
        seeds = range(args.seed_base, args.seed_base + args.seeds)
        jobs = [(c, s, seats) for c in cells for o in opps
                for s, seats in schedule(bot, o, c, seeds)
                if _key(str(c), s, seats) not in done]
        print(f"{len(jobs)} games to play ({len(done)} already in {ledger.name}), "
              f"{args.jobs} workers")
        t0 = time.perf_counter()
        with ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context("spawn")) as pool, \
                open(ledger, "a") as fh:
            futures = [pool.submit(play_one, c, s, seats, args.budget_scale, args.weights) for c, s, seats in jobs]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                rows.append(row)
                if i % 50 == 0 or i == len(futures):
                    print(f"  {i}/{len(futures)}  {time.perf_counter() - t0:.0f}s", flush=True)
    if args.null:
        # Both seats are the same bot, so score seat 1 against seat 2 instead.
        for cell in cells:
            sub = [r for r in rows if r["cell"] == str(cell)]
            wins = sum(1 for r in sub if r["winner"] == 1)
            print(f"{str(cell):<18} seat 1 won {wins}/{len(sub)}")
        return
    report(rows, bot, opps, cells)


if __name__ == "__main__":
    main()
