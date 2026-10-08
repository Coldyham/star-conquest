#!/usr/bin/env python3
"""How well does reader's model predict the roster's launches?

    uv run python tools/reader_check.py                       # every cell, seeds 1-60
    uv run python tools/reader_check.py --cells 18n6 --seeds 1-4   # a smoke run
    uv run python tools/reader_check.py --fit-prior --seeds 1001-1040

Roster games are played with every seat on its own bot. Before each turn, for
every seat holding a system next to a live rival's (a contested position), four
predictors say which launches the rivals will make at that seat's systems:

* **none** — nobody launches (actuary's projection).
* **all** — every adjacent rival garrison comes, whole (actuary's risk reach).
* **prior** — reader's model with no memory: the prior, the same for every rival.
* **reader** — reader's model of each rival, from every turn it has watched.

The turn is then played and the rivals' real orders scored against each:

* **brier** — per (rival source, our target) pair, the squared gap between the
  predicted chance of a strike and whether one came. Lower is better.
* **mse** — per (rival, our target), the squared gap between the ships
  predicted to come (chance x ships) and the ships that came. Squared, since
  chance x ships is a mean: an absolute gap rewards the median, which is 0
  whenever fewer than half the pairs see a strike, so "none" would win it by
  construction.
* **waste** — ships predicted at a target nobody struck: guard held for nothing.
* **miss** — ships that came beyond the prediction.

Intervals are 95% bootstraps over games. reader never plays; it only watches.
`--fit-prior` pools every rival's counts instead and prints the prior's
constants for models/reader.py.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, mapgen
from tests import sim
from tools.bot_distance import lineups

CELLS = {"18n6": (18, 6.0), "24n3": (24, 3.0), "40n6": (40, 6.0), "18n18": (18, 18.0)}
ROSTER = ("claudebot", "thinker", "marshal", "actuary", "knower", "rusherplus", "heuristic")
PREDICTORS = ("none", "all", "prior", "reader")
GATE_BOTS = ("marshal", "actuary", "thinker")
WARMUP = ((0, 4), (5, 14), (15, 29), (30, 10**9))
METRICS = ("brier", "mse", "waste", "miss")
SEPARATION_TURN = 60
SEPARATION_RATIOS = (1.0, 1.5, 2.0)
EPS = 1e-3


def _reader():
    return sys.modules["sc_model_reader"]


def _init() -> None:
    from tools.bot_replay import BUDGET_SCALE
    ai.load_models()
    ai.set_budget_scale(BUDGET_SCALE)


def _bucket(since: int) -> str:
    for lo, hi in WARMUP:
        if lo <= since <= hi:
            return f"{lo}+" if hi > 10**8 else f"{lo}-{hi}"
    return "?"


def _contested(state, pid: int) -> bool:
    systems = state.systems
    return any(systems[n].owner_id not in (0, pid)
               for s in systems.values() if s.owner_id == pid for n in s.neighbors)


def _predictions(state, pid: int, reader) -> dict[str, dict[tuple[int, int, int], tuple[float, float]]]:
    """{predictor: {(rival, source, target): (p, ships)}} over every contested pair."""
    systems = state.systems
    pairs = {}
    for s in systems.values():
        q = s.owner_id
        if q in (0, pid):
            continue
        for n in s.neighbors:
            if systems[n].owner_id == pid:
                pairs[(q, s.id, n)] = s.ships
    out = {"none": {k: (0.0, 0.0) for k in pairs},
           "all": {k: (1.0, float(g)) for k, g in pairs.items()}}
    for name, models in (("prior", {}), ("reader", None)):
        got = {k: (0.0, 0.0) for k in pairs}
        for t in reader.predict(state, pid, models):
            got[(t.rival, t.source, t.target)] = (t.p, t.ships)
        out[name] = got
    return out


def play_game(job):
    """One game: per-predictor sums by (rival bot, warm-up bucket), the fitted
    parameters of every rival at the separation turn, and the raw counts."""
    seed, cell, lineup, max_turns = job
    nodes, speed = CELLS[cell]
    config.SHIP_LY_PER_TURN = speed
    reader = _reader()
    reader.reset()
    state = mapgen.generate(seed, "random", nodes, len(lineup))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(lineup))
    bot_of = {pid: p.ai_strategy for pid, p in state.players.items() if not p.is_neutral}
    first_contact: dict[int, int] = {}
    sums: dict = defaultdict(lambda: defaultdict(float))
    separation: list = []

    while state.winner is None and state.turn < max_turns:
        reader.models_for(state)
        preds = {}
        for pid in sorted(bot_of):
            if state.is_defeated(pid) or not _contested(state, pid):
                continue
            first_contact.setdefault(pid, state.turn)
            preds[pid] = _predictions(state, pid, reader)
        if state.turn == SEPARATION_TURN:
            separation += _describe(reader.models_for(state), bot_of, reader)
        owner_before = {sid: s.owner_id for sid, s in state.systems.items()}
        actual: dict[int, list] = {}

        def recording(st, pid, actual=actual):
            orders = ai.decide(st, pid) or []
            actual[pid] = orders
            return orders

        turn = state.turn
        engine.end_turn(state, decide=recording)

        for pid, by_predictor in preds.items():
            bucket = _bucket(turn - first_contact[pid])
            came: dict[tuple[int, int, int], int] = defaultdict(int)
            for q, orders in actual.items():
                if q == pid:
                    continue
                for o in orders:
                    if owner_before.get(o.source_id) == q and owner_before.get(o.dest_id) == pid:
                        came[(q, o.source_id, o.dest_id)] += max(0, o.ships)
            for name, pairs in by_predictor.items():
                per_target: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
                for key, (p, ships) in pairs.items():
                    q, _src, dst = key
                    y = 1.0 if came.get(key, 0) > 0 else 0.0
                    s = sums[(name, bot_of[q], bucket)]
                    s["pairs"] += 1
                    s["strikes"] += y
                    s["brier"] += (p - y) ** 2
                    s["logloss"] -= math.log(max(EPS, p if y else 1.0 - p))
                    per_target[(q, dst)][0] += p * ships
                    per_target[(q, dst)][1] += came.get(key, 0)
                for (q, _dst), (want, got) in per_target.items():
                    s = sums[(name, bot_of[q], bucket)]
                    s["targets"] += 1
                    s["mse"] += (want - got) ** 2
                    s["waste"] += want if got == 0 else 0.0
                    s["miss"] += max(0.0, got - want)

    raw = [(bot_of[q], m) for q, m in reader.models_for(state).items() if q in bot_of]
    return {"seed": seed, "cell": cell, "turns": state.turn,
            "sums": {k: dict(v) for k, v in sums.items()},
            "separation": separation,
            "raw": [(bot, (m.strikes, m.passes, m.send, m.guard, m.evac)) for bot, m in raw]}


def _describe(models, bot_of, reader):
    out = []
    for q, bot in sorted(bot_of.items()):
        m = models.get(q, reader.EMPTY)
        player = reader.strike_curve(m, reader.PLAYER, 0)
        neutral = reader.strike_curve(m, reader.NEUTRAL, 0)
        out.append((bot, m.turns, *(player[reader._ratio_bin(r)] for r in SEPARATION_RATIOS),
                    neutral[reader._ratio_bin(1.5)],
                    reader.send_share(m), reader.guard_share(m), reader.evac_rate(m)))
    return out


# --------------------------------------------------------------------------- #
# Reading the results
# --------------------------------------------------------------------------- #
def _total(games, predictor, bots=None, bucket=None):
    t = defaultdict(float)
    for g in games:
        for (name, bot, b), s in g["sums"].items():
            if name != predictor or (bots and bot not in bots) or (bucket and b != bucket):
                continue
            for k, v in s.items():
                t[k] += v
    return t


def _rates(t) -> dict[str, float]:
    pairs, targets = t.get("pairs", 0), t.get("targets", 0)
    return {"brier": t.get("brier", 0) / pairs if pairs else float("nan"),
            "logloss": t.get("logloss", 0) / pairs if pairs else float("nan"),
            "mse": t.get("mse", 0) / targets if targets else float("nan"),
            "waste": t.get("waste", 0) / targets if targets else float("nan"),
            "miss": t.get("miss", 0) / targets if targets else float("nan"),
            "rate": t.get("strikes", 0) / pairs if pairs else float("nan"),
            "pairs": pairs}


def _diff_ci(games, a, b, metric, bots=None, draws=1000):
    """95% bootstrap interval, over games, of metric(a) - metric(b)."""
    rng = random.Random(12345)
    per_game = [(_rates(_total([g], a, bots)), _rates(_total([g], b, bots)),
                 _total([g], a, bots), _total([g], b, bots)) for g in games]
    per_game = [x for x in per_game if x[2].get("pairs", 0)]
    if not per_game:
        return float("nan"), float("nan"), float("nan")
    denom = "pairs" if metric in ("brier", "logloss") else "targets"

    def stat(sample):
        na = sum(x[2].get(denom, 0) for x in sample)
        nb = sum(x[3].get(denom, 0) for x in sample)
        if not na or not nb:
            return float("nan")
        return (sum(x[2].get(metric, 0) for x in sample) / na
                - sum(x[3].get(metric, 0) for x in sample) / nb)

    point = stat(per_game)
    boots = sorted(stat([per_game[rng.randrange(len(per_game))] for _ in per_game])
                   for _ in range(draws))
    return point, boots[int(0.025 * draws)], boots[int(0.975 * draws) - 1]


def report(games, cell) -> dict[str, bool]:
    games = [g for g in games if g["cell"] == cell]
    print(f"\n=== {cell}: {len(games)} games, "
          f"{statistics.mean(g['turns'] for g in games):.0f} turns on average")
    print(f"  {'rival':<11}{'pairs':>8}{'rate':>7}   " +
          "".join(f"{p:>8}" for p in PREDICTORS) + "    brier, then mse")
    bots = sorted({bot for g in games for (_n, bot, _b) in g["sums"]})
    for bot in [*bots, None]:
        label = bot or "all rivals"
        rs = {p: _rates(_total(games, p, {bot} if bot else None)) for p in PREDICTORS}
        print(f"  {label:<11}{rs['reader']['pairs']:>8.0f}{rs['reader']['rate']:>7.3f}   "
              + "".join(f"{rs[p]['brier']:>8.4f}" for p in PREDICTORS)
              + "   " + "".join(f"{rs[p]['mse']:>7.1f}" for p in PREDICTORS))
    rs = {p: _rates(_total(games, p)) for p in PREDICTORS}
    print("  all rivals  waste " + " ".join(f"{p} {rs[p]['waste']:.2f}" for p in PREDICTORS))
    print("              miss  " + " ".join(f"{p} {rs[p]['miss']:.2f}" for p in PREDICTORS))

    print("  warm-up (all rivals), brier prior / reader, since first contact:")
    for lo, hi in WARMUP:
        b = _bucket(lo)
        pr, rd = (_rates(_total(games, p, None, b)) for p in ("prior", "reader"))
        print(f"    {b:>6}  {pr['brier']:.4f} / {rd['brier']:.4f}   ({rd['pairs']:.0f} pairs)")

    verdicts = {}
    print("  reader minus each baseline, 95% interval (negative is better):")
    for bot in GATE_BOTS:
        ok = True
        for metric in ("brier", "mse"):
            parts = []
            for base in ("none", "all", "prior"):
                point, lo, hi = _diff_ci(games, "reader", base, metric, {bot})
                ok = ok and hi < 0
                parts.append(f"{base} {point:+.4f} [{lo:+.4f},{hi:+.4f}]" if metric == "brier"
                             else f"{base} {point:+.2f} [{lo:+.2f},{hi:+.2f}]")
            print(f"    {bot:<9}{metric:<6}" + "  ".join(parts))
        verdicts[bot] = ok
        print(f"    {bot:<9}{'clears every baseline' if ok else 'does not clear'}")
    return verdicts


def separation(games) -> None:
    rows = defaultdict(list)
    for g in games:
        for row in g["separation"]:
            rows[row[0]].append(row[1:])
    if not rows:
        return
    print(f"\n=== fitted parameters at turn {SEPARATION_TURN}, median over rivals "
          "(chance of a strike on a player at ratio 1.0 / 1.5 / 2.0, on a neutral at 1.5; "
          "send; guard; evac):")

    def med(xs):
        xs = [x for x in xs if x is not None]
        return f"{statistics.median(xs):.2f}" if xs else "  - "

    for bot in sorted(rows):
        r = rows[bot]
        print(f"  {bot:<11} n={len(r):<4} " + "  ".join(
            med([x[i] for x in r]) for i in range(1, 8)))


def fit_prior(games, reader) -> None:
    rows = reader._ROWS * reader.RATIO_BINS
    strikes, passes = [0.0] * rows, [0.0] * rows
    send, guard, evac = [0.0] * reader.SEND_BINS, [0.0] * reader.GUARD_BINS, [0.0, 0.0]
    for g in games:
        for _bot, (s, p, sd, gd, ev) in g["raw"]:
            strikes = [a + b for a, b in zip(strikes, s)]
            passes = [a + b for a, b in zip(passes, p)]
            send = [a + b for a, b in zip(send, sd)]
            guard = [a + b for a, b in zip(guard, gd)]
            evac = [a + b for a, b in zip(evac, ev)]
    curves = []
    for row in range(reader._ROWS):
        base = row * reader.RATIO_BINS
        hit = strikes[base:base + reader.RATIO_BINS]
        total = [h + m for h, m in zip(hit, passes[base:base + reader.RATIO_BINS])]
        rates = [(h + 0.5) / (n + 1.0) for h, n in zip(hit, total)]
        curves.append(reader._monotone(rates, [n + 1.0 for n in total]))
    print("\nPRIOR_STRIKE = (")
    for row, curve in enumerate(curves):
        kind = "neutral" if row // 2 == reader.NEUTRAL else "player"
        print(f"    # {kind}, {'pressed' if row % 2 else 'unpressed'}")
        print("    (" + ", ".join(f"{v:.3f}" for v in curve) + "),")
    print(")")
    for name, xs in (("PRIOR_SEND", send), ("PRIOR_GUARD", guard)):
        total = sum(xs) or 1.0
        print(f"{name} = (" + ", ".join(f"{x / total:.3f}" for x in xs) + ")")
    print(f"PRIOR_EVAC = {(evac[0] + 0.5) / (sum(evac) + 1.0):.3f}")
    print(f"# from {len(games)} games, {sum(strikes):.0f} strikes, {sum(passes):.0f} passes")


def _seeds(text: str) -> list[int]:
    lo, _, hi = text.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cells", nargs="+", default=["all"],
                        help=f"cells to play: {', '.join(CELLS)} or all")
    parser.add_argument("--seeds", default="1-60", help="seed range, e.g. 1-60")
    parser.add_argument("--players", type=int, default=3)
    parser.add_argument("--bots", nargs="+", default=list(ROSTER))
    parser.add_argument("--max-turns", type=int, default=300)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--fit-prior", action="store_true",
                        help="pool every rival's counts and print the prior's constants")
    args = parser.parse_args(argv)

    cells = list(CELLS) if "all" in args.cells else args.cells
    unknown = [c for c in cells if c not in CELLS]
    if unknown:
        print(f"unknown cells: {', '.join(unknown)}", file=sys.stderr)
        return 2
    _init()
    missing = [b for b in args.bots if b not in ai.STRATEGIES]
    if missing or "sc_model_reader" not in sys.modules:
        print(f"not loaded: {', '.join(missing) or 'models/reader.py'}", file=sys.stderr)
        return 2

    seeds = _seeds(args.seeds)
    jobs = [(seed, cell, tuple(lineup), args.max_turns)
            for cell in cells
            for seed, lineup in zip(seeds, lineups(args.bots, args.players, len(seeds)))]
    started = time.time()
    games = []
    with ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as pool:
        for i, game in enumerate(pool.map(play_game, jobs), 1):
            games.append(game)
            print(f"  {i}/{len(jobs)} games · {time.time() - started:.0f}s", end="\r", flush=True)
    print(f"{len(games)} games in {time.time() - started:.0f}s, {args.players} seats, "
          f"bots: {', '.join(args.bots)}")

    if args.fit_prior:
        fit_prior(games, _reader())
        return 0
    passed = sum(all(report(games, cell).values()) for cell in cells)
    separation(games)
    print(f"\ngate: reader clears every baseline against {', '.join(GATE_BOTS)} "
          f"in {passed} of {len(cells)} cells (needs 3 of 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
