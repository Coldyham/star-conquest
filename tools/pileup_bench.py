#!/usr/bin/env python3
"""knower pricing multi-owner pile-ups by the garrison-fights-last rule, paired.

    uv run python tools/pileup_bench.py --stop 1 --seeds 200          # Predict
    uv run python tools/pileup_bench.py --stop 2 --seeds 60 --setup p5
    uv run python tools/pileup_bench.py --report

Since 2026-09-10 a pile-up folds its attackers among themselves, strongest
first, and the survivor faces the garrison last (`combat.resolve_arrival`).
knower's planner predates that in two places, and each arm changes one of them
for the seat under test only:

* ``defend``: Phase 1 sums every hostile fleet landing on one of our systems on
  a turn (`knower._enemy_arrivals`). The arm folds each turn's owners against
  each other first (`knower._fold`), so a system two rivals strike together is
  priced at the remnant it will actually face, and held rather than abandoned.
* ``attack``: Phase 3 prices a strike off the target's own defence
  (`knower._required`) and ignores a third seat landing there on the same turn.
  The arm asks for enough to win that fold, at the jitter's worst roll, and
  still have the price of the garrison left: the 2026-08-05 measurement
  (docs/design/knower.md, "Built, measured, removed") re-run under the rule.

Every other seat plays as it ships. The wrappers are keyed by the seat's pid,
so a rival knower predicting the seat under test runs the arm for it, as it
would if the arm shipped.

Two line-ups of opponents, so the oracle's two situations read apart: ``sole``,
where every rival is a bot knower runs exactly (actuary at Field, marshal,
rusherplus, heuristic), and ``rivals``, where two of them are oracle seats it
can only model by their stand-ins (knower at Predict, actuary at Learning).
A setup with fewer seats takes the first opponents of each list. The setups are
two posted configs (``SETUPS``), at fresh seeds from ``SEED_BASE``; the seat
under test turns through every seat with the seed. Each seed is played by every
arm on the same seating, and each arm is paired against stock as
`tools/win_turns.py` pairs two arms: ``beats`` (the board's ranking, a win,
then fewer turns, then fewer ships lost), the difference in games won, and the
turns over the games both won, since Council wins nearly every one of these
fields and what is left to read there is speed. Each row also keeps the turn
every seat was knocked out and the seat's finishing place (the winner first,
then the latest out, a seat alive at the turn cap ahead of every seat knocked
out, ties by systems held), since a seat that rarely wins still reads in where
it finishes.

Wall-clock guards are lifted (``ai.set_budget_scale(inf)``). Rows append to
``results/pileup/<label>.jsonl`` as each game finishes, each carrying ``rev``
(`win_turns.play_rev`); the label defaults to it, and a rerun plays only the
games missing.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, combat, config, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402
from tools.win_turns import git_hint, play_rev  # noqa: E402

RESULTS = ROOT / "results" / "pileup"
SEED_BASE = 8_100_000
MAX_TURNS = 600
ARMS = ("stock", "defend", "attack")
STOPS = {1: "Predict", 2: "Search", 3: "Council"}

# Posted configs' settings (`config_summary.settings_json`), seats and seed left out.
SETUPS = {
    "p5": {"key": "8631540e085666d9", "mode": "random", "nodes": 31, "players": 5,
           "combat_jitter": 0.08, "home_production": 2, "ship_ly_per_turn": 3,
           "defender_advantage": 1.05, "relax_min_sep_frac": 0.5,
           "extra_edge_fraction": 0.5, "max_edge_length_frac": 0.6,
           "ship_speed_growth_pct": 0.15},
    "p4": {"key": "2f484ae051214d3f", "mode": "random", "nodes": 23, "players": 4,
           "combat_jitter": 0.08, "home_production": 2, "ship_ly_per_turn": 8,
           "defender_advantage": 1.05, "relax_min_sep_frac": 0.35,
           "extra_edge_fraction": 0.5, "max_edge_length_frac": 0.6},
}
LINEUPS = {
    "sole": (("actuary", 1.0), ("marshal", None), ("rusherplus", None), ("heuristic", None)),
    "rivals": (("knower", 1.0), ("actuary", 2.0), ("marshal", None), ("rusherplus", None)),
}

# --------------------------------------------------------------------------- #
# The arms, on knower's module, once per worker
# --------------------------------------------------------------------------- #
_patched = None
_defend: frozenset[int] = frozenset()
_attack: frozenset[int] = frozenset()
_fires: dict[str, list[int]] = {}       # arm: [calls for a seat under it, calls changed]


def _patch():
    global _patched
    k = sys.modules[ai.STRATEGIES["knower"].__module__]
    if _patched is k:
        return
    enemy_arrivals, required = k._enemy_arrivals, k._required

    def _enemy_arrivals(post, pid, sid):
        out = enemy_arrivals(post, pid, sid)
        if pid not in _defend:
            return out
        by_turn: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for f in post.fleets:
            if f.dest_id == sid and f.owner_id != pid:
                by_turn[max(1, f.turns_remaining)][f.owner_id] += f.ships
        cum, folded = 0, []
        for t in sorted(by_turn):
            cum += k._fold(sorted(by_turn[t].values(), reverse=True))
            folded.append((t, cum))
        _fires["defend"][0] += 1
        _fires["defend"][1] += folded != out
        return folded

    def _required(post, orc, pid, target, dist, pos):
        need = required(post, orc, pid, target, dist, pos)
        if pid not in _attack:
            return need
        third: dict[int, int] = defaultdict(int)
        for f in post.fleets:
            if (f.dest_id == target.id and max(1, f.turns_remaining) == dist
                    and f.owner_id not in (pid, target.owner_id)):
                third[f.owner_id] += f.ships
        _fires["attack"][0] += 1
        if not third:
            return need
        others = sorted(third.values(), reverse=True)

        def left(x: int) -> int:
            if x <= others[0]:
                return 0
            cur = x
            for ships in others:
                roll = combat.preview_fight(cur, ships, config.COMBAT_JITTER, 1.0).worst
                if roll.winner != combat.ATTACKER:
                    return 0
                cur = roll.survivors
            return cur

        lo, hi = need, need + 2 * sum(others) + 2
        while left(hi) < need:
            hi *= 2
        while lo < hi:
            mid = (lo + hi) // 2
            lo, hi = (mid + 1, hi) if left(mid) < need else (lo, mid)
        _fires["attack"][1] += lo != need
        return lo

    k._enemy_arrivals, k._required = _enemy_arrivals, _required
    _patched = k


# --------------------------------------------------------------------------- #
# One game
# --------------------------------------------------------------------------- #
def seating(setup: str, lineup: str, k: int) -> tuple[int, list]:
    """Seed ``k``'s seat under test and every seat's (name, aux), by pid."""
    n = SETUPS[setup]["players"]
    test = k % n + 1
    rest = iter(LINEUPS[lineup][:n - 1])
    return test, [None if pid == test else next(rest) for pid in range(1, n + 1)]


def play_one(setup: str, lineup: str, stop: int, arm: str, k: int) -> dict:
    global _defend, _attack, _fires
    if "knower" not in ai.STRATEGIES:
        ai.load_models()
    _patch()
    ai.set_budget_scale(math.inf)
    spec = {key: v for key, v in SETUPS[setup].items() if key != "key"}
    cfg = replace(Settings.from_dict(spec), autoplay=True)
    seed = SEED_BASE + k
    state = build_state(cfg, seed)
    test, seats = seating(setup, lineup, k)
    for pid, seat in enumerate(seats, start=1):
        name, aux = ("knower", float(stop)) if seat is None else seat
        player = state.players[pid]
        player.is_human = False
        player.ai_strategy = name
        player.ai_params = replace(player.ai_params, aux=1.0 if aux is None else aux)
    _defend = frozenset({test}) if arm == "defend" else frozenset()
    _attack = frozenset({test}) if arm == "attack" else frozenset()
    _fires = {"defend": [0, 0], "attack": [0, 0]}
    out: dict[int, int] = {}
    t0 = time.perf_counter()
    while state.winner is None and state.turn < MAX_TURNS:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
        for p in state.non_neutral_players():
            if not p.alive and p.id not in out:
                out[p.id] = state.turn
    held = defaultdict(int)
    for s in state.systems.values():
        held[s.owner_id] += 1
    order = sorted((p.id for p in state.non_neutral_players()),
                   key=lambda q: (q == state.winner, -out.get(q, -MAX_TURNS - 1), held[q]),
                   reverse=True)
    return {"setup": setup, "lineup": lineup, "stop": stop, "arm": arm, "k": k,
            "seed": seed, "test": test, "winner": state.winner, "turns": state.turn,
            "won": int(state.winner == test), "place": order.index(test) + 1,
            "lost": state.players[test].ships_lost,
            "out": {str(q): t for q, t in sorted(out.items())},
            "secs": round(time.perf_counter() - t0, 2), "fires": _fires.get(arm, [0, 0])}


def _job(task):
    return play_one(*task)


# --------------------------------------------------------------------------- #
# Ledger and report
# --------------------------------------------------------------------------- #
def _key(row) -> tuple:
    return row["setup"], row["lineup"], row["stop"], row["arm"], row["k"]


def load_rows(path: Path, rev: str | None) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if rev is not None:
        foreign = {r.get("rev") for r in rows} - {rev}
        if foreign:
            raise SystemExit(f"{path} holds rows from another tree ({', '.join(sorted(foreign))}); "
                             "pass --label for a ledger of this one")
    return rows


def _mean_z(xs: list[float], fair: float = 0.0) -> tuple[float, float]:
    n = len(xs)
    if n < 2:
        return math.nan, math.nan
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, ((mean - fair) / math.sqrt(var / n) if var > 0 else 0.0)


def standing(row: dict) -> tuple:
    """A result as the board ranks it (`win_turns.standing`): a win, then fewer
    turns, then fewer ships lost; every game not won ranks level."""
    return (1, -row["turns"], -row.get("lost", 0)) if row["won"] else (0,)


def paired(got: dict[int, dict], stock: dict[int, dict]) -> str:
    """An arm against stock seed by seed, read as `tools/win_turns.py` reads two
    arms: ``beats`` (1, 0.5 or 0 as the board ranks the pair), the difference in
    games won, and the geometric mean ratio of turns over the pairs both won;
    then the finishing place (positive is better) and how often the arm acted."""
    both = sorted(set(got) & set(stock))
    beats = [1.0 if standing(got[k]) > standing(stock[k]) else
             0.5 if standing(got[k]) == standing(stock[k]) else 0.0 for k in both]
    dwon = [got[k]["won"] - stock[k]["won"] for k in both]
    ratio = [math.log(got[k]["turns"] / stock[k]["turns"]) for k in both
             if got[k]["won"] and stock[k]["won"]]
    placed = [stock[k]["place"] - got[k]["place"] for k in both
              if "place" in got[k] and "place" in stock[k]]
    moved = sum(standing(got[k]) != standing(stock[k]) or got[k]["winner"] != stock[k]["winner"]
                for k in both)
    calls = sum(got[k]["fires"][0] for k in both)
    fired = sum(got[k]["fires"][1] for k in both)
    b, bz = _mean_z(beats, 0.5)
    w, wz = _mean_z(dwon)
    out = (f"      beats {100 * b:5.1f}% (z {bz:+.2f})   Δwon {100 * w:+5.1f} pt (z {wz:+.2f},"
           f" +{sum(x > 0 for x in dwon)}/-{sum(x < 0 for x in dwon)})")
    if len(ratio) >= 2:
        r, rz = _mean_z(ratio)
        out += f"   Δturns {100 * (math.exp(r) - 1):+5.1f}% (z {rz:+.2f}, {len(ratio)} both won)"
    if len(placed) >= 2:
        pm, pz = _mean_z(placed)
        out += f"   place {pm:+.3f} (z {pz:+.2f})"
    return out + f"\n      results moved {moved}/{len(both)}   calls changed {fired}/{calls}"


def report(rows: list[dict]) -> None:
    groups: dict[tuple, dict[str, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        groups[(r["setup"], r["lineup"], r["stop"])][r["arm"]][r["k"]] = r
    for (setup, lineup, stop), arms in sorted(groups.items()):
        players = SETUPS[setup]["players"]
        print(f"\n{setup} ({players} seats, {SETUPS[setup]['key']}), {lineup}, "
              f"knower at {STOPS.get(stop, stop)}  (fair share {100 / players:.1f}%)")
        stock = arms.get("stock", {})
        for arm in ARMS:
            got = arms.get(arm, {})
            if not got:
                continue
            n = len(got)
            won = [r for r in got.values() if r["won"]]
            capped = sum(r["winner"] is None for r in got.values())
            turns = sorted(r["turns"] for r in won)
            median = f"{turns[len(turns) // 2]:4}" if turns else "   —"
            secs = sum(r["secs"] for r in got.values()) / n
            print(f"  {arm:7} {len(won):4}/{n:<4} {100 * len(won) / n:5.1f}%"
                  f"  median win {median} turns  unfinished {capped:3}  {secs:6.1f} s/game")
            if arm != "stock" and stock:
                print(paired(got, stock))


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stop", type=int, nargs="+", default=[1], choices=sorted(STOPS))
    ap.add_argument("--setup", nargs="+", default=list(SETUPS), choices=list(SETUPS))
    ap.add_argument("--lineup", nargs="+", default=list(LINEUPS), choices=list(LINEUPS))
    ap.add_argument("--arm", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--first", type=int, default=0, help="first seed index")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--label", default="")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    rev = play_rev()
    path = RESULTS / f"{args.label or rev}.jsonl"
    rows = load_rows(path, None if args.label else rev)
    if args.report:
        report(rows)
        return 0
    done = {_key(r) for r in rows}
    tasks = [(s, lu, stop, arm, k)
             for k in range(args.first, args.first + args.seeds)
             for stop in args.stop for s in args.setup for lu in args.lineup
             for arm in args.arm if (s, lu, stop, arm, k) not in done]
    print(f"{path.relative_to(ROOT)}: {len(done)} rows, {len(tasks)} games to play "
          f"on {args.jobs} workers (rev {rev}, {git_hint()})", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool, path.open("a") as out:
        futures = [pool.submit(_job, t) for t in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            row["rev"] = rev
            out.write(json.dumps(row) + "\n")
            out.flush()
            if i % 20 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)}  {time.perf_counter() - t0:7.0f} s", flush=True)
    report(load_rows(path, None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
