#!/usr/bin/env python3
"""Quick checks of four multiplayer tactics on marshal, paired against stock.

    uv run python tools/mp_tactics.py --seeds 400
    uv run python tools/mp_tactics.py --arms stock loss loss_w --seeds 400
    uv run python tools/mp_tactics.py --report

Each arm is a private import of ``models/marshal.py`` with two functions
wrapped: ``_richness`` (the order Phase 3 tries targets in, and which front
Phase 4 feeds) and ``_max_adjacent_enemy`` (the guard a front keeps). A rival's
standing is its income over the mean income of the players alive; the leader is
the richest rival while it is at least as rich as us, and the weak rival the
poorest while under ``WEAK_BELOW`` of the mean.

* ``leader1``/``leader3``: +1 / +3 richness on a target the leader holds.
* ``weak1``/``weak3``: the same on a target the weak rival holds.
* ``loss``: each rival stack next door counts at its standing, clamped to
  0.5-1.5, in the guard; ``loss_w`` clamps to 0.25-1.0, so a weak rival is
  guarded against less and nobody more.
* ``cede5``/``cede0``: a front bordering two or more rivals, with four or more
  players alive, keeps half its guard or none, to let them meet there.

Every arm plays as stock in a duel. The seat under test turns with the seed;
every arm plays the same seed in the same seat against the same fillers, so a
pair differs only in that seat. Each game is appended to
``results/mp_tactics/games.jsonl`` as it finishes, and a rerun plays only what
is missing. Results: ``docs/design/marshal.md``, "Multiplayer tactics: four
quick checks".
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from tools import sweep  # noqa: E402

OUT = ROOT / "results" / "mp_tactics"
SEED_BASE = 8_300_000
CAP = 600
OPP_AUX = {"knower": 1.0, "actuary": 1.0}    # Predict, Field

GROUPS = {
    "spread4": ("random:18:8", ["rusherplus", "heuristic", "marshal"]),
    "strong4": ("random:18:8", ["marshal", "actuary", "knower"]),
    "spread5": ("random:31:3", ["rusherplus", "heuristic", "marshal", "thinker"]),
    "strong5": ("random:31:3", ["marshal", "actuary", "knower", "marshal"]),
}

ARMS = {
    "stock": {},
    "leader1": {"LEADER": 1.0},
    "leader3": {"LEADER": 3.0},
    "weak1": {"WEAK": 1.0},
    "weak3": {"WEAK": 3.0},
    "loss": {"LOSS": (0.5, 1.5)},
    "loss_w": {"LOSS": (0.25, 1.0)},
    "cede5": {"CEDE": 0.5},
    "cede0": {"CEDE": 0.0},
}
WEAK_BELOW = 0.6


def incomes(state) -> dict[int, float]:
    inc = {p.id: 0.0 for p in state.players.values() if not p.is_neutral and p.alive}
    for s in state.systems.values():
        if s.owner_id in inc and s.production > 0:
            inc[s.owner_id] += 1.0 / s.production
    return inc


def standing(state, pid):
    """``(leader, weak, owner -> income / mean income)``; ``(None, None, {})``
    with fewer than three players alive."""
    inc = incomes(state)
    if len(inc) < 3:
        return None, None, {}
    mean = sum(inc.values()) / len(inc) or 1.0
    st = {q: v / mean for q, v in inc.items()}
    rivals = [q for q in inc if q != pid]
    lead = max(rivals, key=lambda q: (inc[q], -q))
    weak = min(rivals, key=lambda q: (inc[q], q))
    return (lead if inc[lead] >= inc[pid] else None,
            weak if st[weak] < WEAK_BELOW else None, st)


def make_arm(alias: str, knobs: dict):
    """A private import of marshal with ``knobs`` wrapped in."""
    spec = importlib.util.spec_from_file_location(alias, ROOT / "models" / "marshal.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    if not knobs:
        return mod
    base_rich = mod._richness
    base_adj = mod._max_adjacent_enemy

    def richness(state, pid, system, max_prod):
        v = base_rich(state, pid, system, max_prod)
        if system.owner_id in (0, pid):
            return v
        lead, weak, _st = standing(state, pid)
        if system.owner_id == lead:
            v += knobs.get("LEADER", 0.0)
        if system.owner_id == weak:
            v += knobs.get("WEAK", 0.0)
        return v

    def adjacent(state, pid, sysobj, skip=None):
        if "LOSS" in knobs:
            lo, hi = knobs["LOSS"]
            st = standing(state, pid)[2]
            best = 0.0
            for n in sysobj.neighbors:
                o = state.systems[n]
                if o.owner_id != pid and o.owner_id != 0 and n != skip:
                    w = min(hi, max(lo, st[o.owner_id])) if st else 1.0
                    if (mod.FAST_GUARD_WEIGHT != 1.0
                            and (state.travel_turns(sysobj.id, n) or 99) == 1):
                        w *= mod.FAST_GUARD_WEIGHT
                    best = max(best, o.ships * w)
            return best
        threat = base_adj(state, pid, sysobj, skip)
        if "CEDE" in knobs:
            rivals = {state.systems[n].owner_id for n in sysobj.neighbors} - {0, pid}
            live = sum(1 for p in state.players.values() if not p.is_neutral and p.alive)
            if len(rivals) >= 2 and live >= 4:
                threat *= knobs["CEDE"]
        return threat

    mod._richness = richness
    mod._max_adjacent_enemy = adjacent
    return mod


_arms: dict[str, str] = {}
_fired: dict[str, list[int]] = {}


def _key(orders):
    return sorted((o.source_id, o.dest_id, o.ships) for o in orders)


def setup() -> None:
    """Register every arm as ``__mp_<name>``, counting the decides whose orders
    differ from stock's on the same board."""
    if _arms:
        return
    ai.load_models()
    ai.set_budget_scale(sweep.BUDGET_SCALE)
    stock = make_arm("mp_stock_ref", {})
    for name, knobs in ARMS.items():
        mod = make_arm(f"mp_arm_{name}", knobs)

        def decide(state, pid, _mod=mod, _name=name):
            orders = _mod.decide(state, pid)
            if ARMS[_name]:
                f = _fired[_name]
                f[0] += 1
                f[1] += _key(orders) != _key(stock.decide(state, pid))
            return orders

        ai.register(f"__mp_{name}", decide)
        _arms[name] = f"__mp_{name}"
        _fired[name] = [0, 0]


def play(task) -> dict:
    group, seed, arm = task
    setup()
    cell_spec, fillers = GROUPS[group]
    cell = sweep.parse_cell(cell_spec)
    settings_mod.apply_globals(cell.settings())
    n = 1 + len(fillers)
    state = mapgen.generate(seed, cell.mode, cell.nodes, n)
    seat = 1 + seed % n
    rest = iter(fillers)
    for pid, p in sorted(state.players.items()):
        if p.is_neutral:
            continue
        p.is_human = False
        strategy = _arms[arm] if pid == seat else next(rest)
        p.ai_strategy = strategy
        p.ai_params = replace(p.ai_params, aux=OPP_AUX.get(strategy, p.ai_params.aux))
    _fired[arm][:] = [0, 0]
    t0 = time.perf_counter()
    died = None
    while state.winner is None and state.turn < CAP:
        engine.end_turn(state, decide=ai.decide)
        if died is None and not state.players[seat].alive:
            died = state.turn
    inc = incomes(state)
    share = inc.get(seat, 0.0) / (sum(inc.values()) or 1.0)
    return {"group": group, "seed": seed, "arm": arm, "seat": seat,
            "won": int(state.winner == seat), "winner": state.winner,
            "turns": state.turn, "died": died, "share": round(share, 4),
            "decides": _fired[arm][0], "changed": _fired[arm][1],
            "secs": round(time.perf_counter() - t0, 3)}


def _z(xs):
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0
    m = sum(xs) / n
    se = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)
    return m, se, (m / se if se else 0.0)


def report(rows) -> None:
    by = {(r["group"], r["seed"], r["arm"]): r for r in rows}
    arms = [a for a in ARMS if a != "stock" and any(k[2] == a for k in by)]
    for g in GROUPS:
        seeds = sorted(k[1] for k in by if k[0] == g and k[2] == "stock")
        if not seeds:
            continue
        won = sum(by[(g, s, "stock")]["won"] for s in seeds)
        secs = sum(by[(g, s, "stock")]["secs"] for s in seeds) / len(seeds)
        print(f"\n{g} {GROUPS[g][0]} vs {'+'.join(GROUPS[g][1])}: stock won {won}/{len(seeds)}"
              f" ({100 * won / len(seeds):.1f}%, fair {100 / (1 + len(GROUPS[g][1])):.0f}%),"
              f" {secs:.1f} s/game")
        print(f"  {'arm':9} {'n':>4} {'won%':>6} {'Δwon pt':>8} {'z':>6} {'Δshare':>7}"
              f" {'diff games':>10} {'orders changed':>14}")
        for a in arms:
            pairs = [(by[(g, s, "stock")], by[(g, s, a)]) for s in seeds if (g, s, a) in by]
            if not pairs:
                continue
            m, _se, z = _z([100 * (v["won"] - b["won"]) for b, v in pairs])
            dsh = _z([100 * (v["share"] - b["share"]) for b, v in pairs])[0]
            diff = sum((b["turns"], b["winner"], b["share"]) != (v["turns"], v["winner"], v["share"])
                       for b, v in pairs)
            dec = sum(v["decides"] for _b, v in pairs)
            chg = sum(v["changed"] for _b, v in pairs)
            wv = sum(v["won"] for _b, v in pairs)
            print(f"  {a:9} {len(pairs):4d} {100 * wv / len(pairs):6.1f} {m:+8.1f} {z:+6.2f}"
                  f" {dsh:+7.1f} {100 * diff / len(pairs):9.0f}% {100 * chg / max(1, dec):13.1f}%")
    print("\npooled over the groups")
    for a in arms:
        d = [100 * (v["won"] - by[(g, s, "stock")]["won"])
             for (g, s, x), v in by.items() if x == a and (g, s, "stock") in by]
        m, se, z = _z(d)
        print(f"  {a:9} n {len(d):5d}  Δwon {m:+.2f} ± {se:.2f}  z {z:+.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=400)
    ap.add_argument("--jobs", type=int, default=22)
    ap.add_argument("--groups", nargs="*", default=list(GROUPS))
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "games.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    if args.report:
        report(rows)
        return 0
    done = {(r["group"], r["seed"], r["arm"]) for r in rows}
    tasks = [(g, SEED_BASE + i, a) for i in range(args.seeds) for g in args.groups
             for a in args.arms if (g, SEED_BASE + i, a) not in done]
    print(f"{len(tasks)} games to play ({len(done)} already done)", flush=True)
    t0 = time.time()
    with open(path, "a") as fh, ProcessPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(play, t) for t in tasks]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            rows.append(r)
            if i % 200 == 0 or i == len(tasks):
                print(f"{i}/{len(tasks)}  {time.time() - t0:.0f}s", flush=True)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
