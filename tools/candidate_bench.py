#!/usr/bin/env python3
"""A bot tried as one more of knower's borrowed candidates, paired by seed.

    uv run python tools/candidate_bench.py --borrow evolved:0               # Search, head to head
    uv run python tools/candidate_bench.py --borrow evolved:0 --stop 3      # ...at Council
    uv run python tools/candidate_bench.py --borrow evolved:0 --vs marshal actuary
    uv run python tools/candidate_bench.py --borrow evolved:0 --report
    uv run python tools/candidate_bench.py --borrow evolved:0 --time --seeds 3   # its cost

knower at ``--stop`` (2 Search, 3 Council) with the bot ``--borrow`` names
(``name:aux`` borrows a stop of its knob) appended after every other borrowed
opening (`knower._externals`), for the seats under test only. Appended last, it
changes nothing until its line outscores every other one, so a seat whose new
candidate is never picked plays exactly as knower does. Every other seat in the
game is untouched, a knower included, and models the seat under test as it
models any knower seat (`knower._surrogate`). At Council the candidate's line is
played on by the bot that proposed it, as every adviser's is
(`knower._policy_advance`).

``--vs knower`` (the default) plays that seat against knower at the same stop.
Two identical deterministic bots on every seating of a map score exactly the
fair share, so every point off it is the candidate's. Any other ``--vs`` plays
two arms on the same seeds and seatings, knower with the candidate and without,
and reports the difference seed by seed.

Each decide of a seat under test also records which line the search picked, by
phase (`bot_distance.phase_of`: contested when we hold a system next to a live
rival's); whether the candidate's opening differed from the default's, and from
every other candidate's; whether its line changed the move played; and whether a
new opening of its tied the pick, since a candidate listed last loses every tie
(docs/design/bots.md, "How differently two bots play"). Each seat's decide time
is kept too, a rough ratio from a loaded machine. ``--time`` is the cost figure:
positions from knower-only games at the stop, every ``TIME_EVERY`` turns, each
live seat timed in CPU on the same position with the candidate and without, in
one process; run it on a quiet machine.

Wall-clock guards are lifted (``ai.set_budget_scale(inf)``), so the result is the
same on any machine. Rows append to ``results/candidates/<label>.jsonl`` and a
rerun skips games already there.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402
from tools.bot_distance import PHASES, phase_of  # noqa: E402
from tools.evo_bench import CELLS, Cell, Seat, load  # noqa: E402

RESULTS = ROOT / "results" / "candidates"
SEED_BASE = 4_400_000
MAX_TURNS = 600
TIME_EVERY = 10                 # `--time`: a position every this many turns
VARIANT, BASE = "variant", "base"
# knower's own openings by line index, ahead of the borrowed ones: the names
# `POSTURE_VARIANTS` gives them in its comments.
POSTURES = ("default", "timid", "all-in", "push-for-depth")

# --------------------------------------------------------------------------- #
# The patch on knower's module, once per worker
# --------------------------------------------------------------------------- #
_patched = None                 # the knower module the wrappers below sit on
_under_test: frozenset[int] = frozenset()
_borrow: tuple[str, float | None] = ("", None)
_deciding: int | None = None    # the seat under test whose search is running
_rec: dict | None = None        # that search's openings, their labels, its last lines
_tally: dict = {}               # this game's counts, by phase


def _patch():
    """Wrap knower's `_externals` (the candidate, for a seat under test) and,
    as pass-throughs, the four functions a search's record is read from."""
    global _patched
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    if _patched is k:
        return
    externals, search, candidates = k._externals, k._search, k._candidates
    external_plan, advance, policy_advance = k._external_plan, k._advance, k._policy_advance

    def _externals():
        return externals() + ((_borrow,) if _deciding is not None else ())

    def _search(state, pid, orc, turns, deadline):
        global _deciding, _rec
        if pid not in _under_test:
            return search(state, pid, orc, turns, deadline)
        _deciding, _rec = pid, {"plans": [], "labels": {}, "lines": None, "root": True}
        try:
            root = search(state, pid, orc, turns, deadline)
            try:
                _record(state, pid, root, _rec)
            except Exception:             # noqa: BLE001 — a count must never move a move
                _tally.setdefault("errors", 0)
                _tally["errors"] += 1
            return root
        finally:
            _deciding, _rec = None, None

    def _candidates(state, pid, orc, base):
        for plan in candidates(state, pid, orc, base):
            if _rec is not None:
                _rec["plans"].append(plan)
            yield plan

    def _external_plan(state, pid, borrowed, salt):
        plan = external_plan(state, pid, borrowed, salt)
        if _rec is not None and _rec["root"] and plan is not None:
            _rec["labels"][id(plan)] = borrowed
        return plan

    def _stepped(fn):
        def step(lines, pid, humans):
            if _rec is not None:
                _rec["root"] = False
            out = fn(lines, pid, humans)
            if _rec is not None:
                _rec["lines"] = out
            return out
        return step

    k._externals, k._search, k._candidates = _externals, _search, _candidates
    k._external_plan = _external_plan
    k._advance, k._policy_advance = _stepped(advance), _stepped(policy_advance)
    _patched = k


def _label(borrowed) -> str:
    name, aux = borrowed
    return name if aux is None else f"{name}:{aux:g}"


def _sig(plan):
    """An opening as what it sends where: one ship's difference is a different
    opening."""
    sent: Counter = Counter()
    for o in plan:
        sent[(o.source_id, o.dest_id)] += o.ships
    return tuple(sorted((k, v) for k, v in sent.items() if v > 0))


def _value(score) -> float:
    return score.get() if hasattr(score, "get") else float(score)


def _record(state, pid, root, rec) -> None:
    """Count one search of a seat under test into this game's tally."""
    plans = rec["plans"]
    if not plans:
        return                  # a zero-ply search returns the default unsearched
    labels = [_label(rec["labels"][id(p)]) if id(p) in rec["labels"] else POSTURES[i]
              for i, p in enumerate(plans)]
    chosen = next((i for i, p in enumerate(plans) if p is root), None)
    phase = phase_of(state, pid)
    t = _tally.setdefault(phase, {"decides": 0, "picks": Counter(), "borrow": Counter()})
    t["decides"] += 1
    t["picks"][labels[chosen] if chosen is not None else "?"] += 1
    b = len(plans) - 1          # appended last, when it proposed anything
    c = t["borrow"]
    if labels[b] != _label(_borrow):
        c["absent"] += 1
        return
    c["offered"] += 1
    sigs = [_sig(p) for p in plans]
    novel = sigs[b] not in sigs[:b]
    c["differs"] += sigs[b] != sigs[0]
    c["novel"] += novel
    c["chosen"] += chosen == b
    lines = rec["lines"]
    if lines is None or len(lines) != len(plans):
        c["unscored"] += 1
        return
    values = [_value(line.score) for line in lines]
    # What the search plays without the candidate: the first best of the rest.
    without = max(range(b), key=lambda i: (values[i], -i))
    c["changed"] += chosen == b and sigs[b] != sigs[without]
    c["tied"] += novel and chosen != b and values[b] >= values[chosen]


# --------------------------------------------------------------------------- #
# Games
# --------------------------------------------------------------------------- #
def play_one(cell: Cell, seed: int, seats: tuple[str, ...], stop: int,
             borrow: tuple[str, float | None], opp: Seat | None) -> dict:
    """One game; ``seats[i]`` holds pid ``i + 1`` and is VARIANT, BASE or the
    opponent's label. Returns the ledger row."""
    global _under_test, _borrow, _tally
    if "knower" not in ai.STRATEGIES or borrow[0] not in ai.STRATEGIES:
        ai.load_models()
    _patch()
    ai.set_budget_scale(math.inf)       # every call: a forked worker inherits guards
    cfg = Settings(mode=cell.mode, nodes=cell.nodes, players=cell.players,
                   ship_ly_per_turn=cell.ly, autoplay=True)
    state = build_state(cfg, seed)
    for pid, seat in enumerate(seats, start=1):
        name, aux = ("knower", float(stop)) if seat in (VARIANT, BASE) else (opp.name, opp.aux)
        player = state.players[pid]
        player.ai_strategy = name
        player.ai_params = replace(player.ai_params, aux=1.0 if aux is None else aux)
    _under_test = frozenset(pid for pid, seat in enumerate(seats, start=1) if seat == VARIANT)
    _borrow, _tally = borrow, {}
    ms: dict[int, float] = defaultdict(float)
    calls: Counter = Counter()

    def timed(st, q):
        t = time.perf_counter()
        try:
            return ai.decide(st, q)
        finally:
            ms[q] += (time.perf_counter() - t) * 1000
            calls[q] += 1

    t0 = time.perf_counter()
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=timed)
    return {"cell": str(cell), "seed": seed, "seats": list(seats),
            "winner": state.winner, "turns": state.turn,
            "secs": round(time.perf_counter() - t0, 2),
            "ms": {str(q): [calls[q], round(ms[q], 1)] for q in sorted(calls)},
            "tally": {ph: v if ph == "errors" else
                      {"decides": v["decides"], "picks": dict(v["picks"]),
                       "borrow": dict(v["borrow"])} for ph, v in _tally.items()}}


def seatings(cell: Cell, arm: str, other: str):
    """Every seating of ``arm`` against ``other`` in ``cell``: both ways round in
    a duel, ``arm`` in each seat against copies of ``other`` in a free-for-all."""
    if cell.players == 2:
        return [(arm, other), (other, arm)]
    return [tuple(arm if i == k else other for i in range(cell.players))
            for k in range(cell.players)]


def schedule(cell: Cell, opp: str, seeds: range):
    for seed in seeds:
        if opp == "knower":
            for seats in seatings(cell, VARIANT, BASE):
                yield seed, seats
        else:
            for arm in (VARIANT, BASE):
                for seats in seatings(cell, arm, opp):
                    yield seed, seats


def _key(cell: str, seed: int, seats) -> str:
    return f"{cell}|{seed}|{','.join(seats)}"


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _mean_z(xs: list[float], fair: float) -> tuple[float, float, int]:
    n = len(xs)
    if not n:
        return math.nan, math.nan, 0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / max(1, n - 1)
    z = (mean - fair) / math.sqrt(var / n) if var > 0 else 0.0
    return mean, z, n


def per_seed(rows: list[dict], cell: Cell, arm: str, other: str) -> dict[int, float]:
    """``{seed: arm's mean result over every seating}`` where all were played; a
    win 1, a game left unfinished at ``MAX_TURNS`` the fair share."""
    want = {tuple(s) for s in seatings(cell, arm, other)}
    by: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if r["cell"] != str(cell) or tuple(r["seats"]) not in want:
            continue
        w = r["winner"]
        by[r["seed"]].append(1.0 / cell.players if w is None else float(r["seats"][w - 1] == arm))
    return {s: sum(v) / len(v) for s, v in by.items() if len(v) == len(want)}


def _ms_per_decide(rows: list[dict], arm: str) -> float:
    n = total = 0.0
    for r in rows:
        for pid, seat in enumerate(r["seats"], start=1):
            if seat == arm and str(pid) in r["ms"]:
                c, m = r["ms"][str(pid)]
                n, total = n + c, total + m
    return total / n if n else math.nan


def report(rows: list[dict], cells: list[Cell], opps: list[str], title: str) -> None:
    print(f"\n{title}")
    for opp in opps:
        if opp == "knower":
            print(f"\nhead to head against knower at the same stop "
                  f"(win share, fair = 1/players; points above fair, z over seeds)")
            pooled = []
            for cell in cells:
                ps = per_seed(rows, cell, VARIANT, BASE)
                fair = 1.0 / cell.players
                m, z, n = _mean_z(list(ps.values()), fair)
                pooled += [x - fair for x in ps.values()]
                sub = [r for r in rows if r["cell"] == str(cell)
                       and set(r["seats"]) == {VARIANT, BASE}]
                unf = sum(r["winner"] is None for r in sub)
                cost = _ms_per_decide(sub, VARIANT) / _ms_per_decide(sub, BASE)
                print(f"  {str(cell):<16} {m:6.1%}  {100 * (m - fair):+5.1f} pts  z {z:+5.2f}"
                      f"  n {n:3d}" + (f"  unfinished {unf}" if unf else "")
                      + f"   ms/decide x{cost:.2f}")
            m, z, n = _mean_z(pooled, 0.0)
            print(f"  {'pooled':<16} {'':6}  {100 * m:+5.1f} pts  z {z:+5.2f}  n {n:3d}")
            shares([r for r in rows if set(r["seats"]) == {VARIANT, BASE}], cells)
            continue
        print(f"\nagainst {opp}: knower without and with the candidate, paired by seed")
        diffs = []
        for cell in cells:
            v = per_seed(rows, cell, VARIANT, opp)
            b = per_seed(rows, cell, BASE, opp)
            d = [v[s] - b[s] for s in v if s in b]
            bm, _, _ = _mean_z([b[s] for s in v if s in b], 0.0)
            vm, _, _ = _mean_z([v[s] for s in v if s in b], 0.0)
            dm, dz, n = _mean_z(d, 0.0)
            diffs += d
            sub = [r for r in rows if r["cell"] == str(cell) and opp in r["seats"]]
            cost = _ms_per_decide(sub, VARIANT) / _ms_per_decide(sub, BASE)
            print(f"  {str(cell):<16} {bm:6.1%} -> {vm:6.1%}  {100 * dm:+5.1f} pts  z {dz:+5.2f}"
                  f"  n {n:3d}   ms/decide x{cost:.2f}")
        dm, dz, n = _mean_z(diffs, 0.0)
        print(f"  {'pooled':<16} {'':16}  {100 * dm:+5.1f} pts  z {dz:+5.2f}  n {n:3d}")
        shares([r for r in rows if opp in r["seats"]], cells)


def shares(rows: list[dict], cells: list[Cell]) -> None:
    """The search's picks and the candidate's counts over every seat under test."""
    for phase in PHASES:
        picks: Counter = Counter()
        borrow: Counter = Counter()
        decides = 0
        for r in rows:
            if r["cell"] not in {str(c) for c in cells}:
                continue
            t = r.get("tally", {}).get(phase)
            if t:
                decides += t["decides"]
                picks.update(t["picks"])
                borrow.update(t["borrow"])
        if not decides:
            continue
        print(f"\n{phase} decisions: {decides}")
        print("  picked  " + "  ".join(f"{k} {v / decides:.1%}" for k, v in picks.most_common()))
        offered = borrow["offered"]
        if offered:
            print(f"  candidate offered {offered / decides:.1%}; of those its opening differs "
                  f"from the default's {borrow['differs'] / offered:.1%}, from every earlier "
                  f"line's {borrow['novel'] / offered:.1%}")
            print(f"  its line picked {borrow['chosen'] / offered:.1%}, changing the move "
                  f"played {borrow['changed'] / offered:.1%}; a new opening level with the "
                  f"pick, lost on list order {borrow['tied'] / offered:.1%}"
                  + (f"; unscored {borrow['unscored']}" if borrow["unscored"] else ""))
        if borrow["absent"]:
            print(f"  candidate absent (it raised, or is not registered) {borrow['absent']}")
    errors = sum(r.get("tally", {}).get("errors", 0) for r in rows)
    if errors:
        print(f"\n  {errors} searches not counted: the record raised")


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #
def positions(cell: Cell, seed: int, stop: int) -> list:
    """Boards from a game every seat of which is knower at ``stop``, each as its
    turn began, every ``TIME_EVERY`` turns."""
    cfg = Settings(mode=cell.mode, nodes=cell.nodes, players=cell.players,
                   ship_ly_per_turn=cell.ly, autoplay=True)
    state = build_state(cfg, seed)
    for player in state.non_neutral_players():
        player.ai_strategy = "knower"
        player.ai_params = replace(player.ai_params, aux=float(stop))
    out = []
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        if state.turn % TIME_EVERY == 0:
            out.append(copy.deepcopy(state))
        engine.end_turn(state, decide=ai.decide)
    return out


def _cpu_ms(board, pid: int, under: frozenset[int]) -> float:
    global _under_test
    board = copy.deepcopy(board)
    _under_test = under
    t = time.process_time()
    engine.decide_seat(board, pid, ai.decide)
    return (time.process_time() - t) * 1000


def time_cost(cells: list[Cell], seeds: range, stop: int, borrow) -> None:
    """CPU ms of a decide with the candidate and without, on the same positions,
    which go first alternating, in this process."""
    global _borrow
    _patch()
    ai.set_budget_scale(math.inf)
    _borrow = borrow
    print(f"CPU ms a decide, knower at stop {stop}, without and with {_label(borrow)}; "
          f"load average {os.getloadavg()[0]:.2f}")
    for cell in cells:
        base, more = [], []
        for seed in seeds:
            for k, board in enumerate(positions(cell, seed, stop)):
                for player in board.non_neutral_players():
                    if not player.alive or not board.systems_of(player.id):
                        continue
                    arms = (frozenset(), frozenset({player.id}))
                    first, second = arms if k % 2 == 0 else arms[::-1]
                    a, b = _cpu_ms(board, player.id, first), _cpu_ms(board, player.id, second)
                    base.append(a if k % 2 == 0 else b)
                    more.append(b if k % 2 == 0 else a)
        if not base:
            continue
        p75 = lambda xs: sorted(xs)[int(0.75 * (len(xs) - 1))]  # noqa: E731
        extra = [m - b for b, m in zip(base, more)]
        print(f"  {str(cell):<16} n {len(base):4d}   median {statistics.median(base):6.1f} -> "
              f"{statistics.median(more):6.1f}   p75 {p75(base):6.1f} -> {p75(more):6.1f}   "
              f"x{sum(more) / sum(base):.2f} in total, +{statistics.median(extra):.1f} ms median")
    print(f"  load average {os.getloadavg()[0]:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--borrow", required=True, help="name[:aux] of the bot to borrow")
    ap.add_argument("--stop", type=int, default=2, choices=(2, 3),
                    help="knower's Oracle stop: 2 Search (default), 3 Council")
    ap.add_argument("--vs", nargs="*", default=["knower"],
                    help="knower (head to head, the default) or opponents, name[:aux]")
    ap.add_argument("--cell", action="append", default=[], help="mode:nodes:ly:players")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--label", default=None, help="ledger name (default: from --borrow and --stop)")
    ap.add_argument("--report", action="store_true", help="re-print from the ledger only")
    ap.add_argument("--time", action="store_true",
                    help="time the candidate on matched positions instead (try --seeds 3)")
    args = ap.parse_args()

    ai.load_models()
    borrow = Seat.parse(args.borrow)
    for name in [borrow.name, *(Seat.parse(s).name for s in args.vs)]:
        if name not in ai.STRATEGIES:
            raise SystemExit(f"unknown bot {name!r}; known: {', '.join(sorted(ai.STRATEGIES))}")
    cells = [Cell.parse(c) for c in (args.cell or CELLS)]
    if args.time:
        time_cost(cells, range(args.seed_base, args.seed_base + args.seeds), args.stop,
                  (borrow.name, borrow.aux))
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    label = args.label or f"{str(borrow).replace(':', '@')}_stop{args.stop}"
    ledger = RESULTS / f"{label}.jsonl"
    rows = load(ledger)
    seeds = range(args.seed_base, args.seed_base + args.seeds)
    if not args.report:
        done = {_key(r["cell"], r["seed"], r["seats"]) for r in rows}
        jobs = [(c, s, seats, Seat.parse(o) if o != "knower" else None)
                for c in cells for o in args.vs for s, seats in schedule(c, o, seeds)
                if _key(str(c), s, seats) not in done]
        print(f"{len(jobs)} games to play ({len(done)} already in {ledger.name}), "
              f"{args.jobs} workers")
        t0 = time.perf_counter()
        with ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context("spawn")) as pool, \
                open(ledger, "a") as fh:
            futures = [pool.submit(play_one, c, s, seats, args.stop, (borrow.name, borrow.aux), o)
                       for c, s, seats, o in jobs]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                rows.append(row)
                if i % 50 == 0 or i == len(futures):
                    print(f"  {i}/{len(futures)}  {time.perf_counter() - t0:.0f}s", flush=True)
    rows = [r for r in rows if r["seed"] in seeds]
    report(rows, cells, args.vs, f"knower at stop {args.stop} + {borrow}, "
                                 f"seeds {args.seed_base}+{args.seeds}")


if __name__ == "__main__":
    main()
