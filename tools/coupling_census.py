#!/usr/bin/env python3
"""Which of a bot's constants touch the same decisions? A census, behaviour unchanged.

    uv run python tools/coupling_census.py                  # marshal, six cells, 8 seeds
    uv run python tools/coupling_census.py --seeds 2 --top 20
    uv run python tools/coupling_census.py --perturb FRONTIER_GUARD=0.35 --perturb FEED=0.5
    uv run python tools/coupling_census.py --report results/coupling_census.json

Tuning one constant up and down finds the best value *given the others*. When two
constants act on the same decision, that best value moves with the other one, and
a one-at-a-time sweep can sit on a ridge it never sees. This finds those pairs
without playing a single extra game: stock duels supply the positions, and at each
sampled position the bot's ``decide`` runs under stock, under each perturbation
alone, and under every pair of perturbations on different constants.

A pair **interacts** at a position when its joint change to the orders is not the
sum of its two single changes: ``s11 - s10 - s01 + s00 != 0``, with ``s`` the
ships sent per (source, destination). The report gives, per pair of constants
(the strongest pair of directions), how often that happens on contested
positions, pooled and per cell, and when one constant *masks* the other (it
changes the orders alone, and nothing once the other is on) or *enables* it (it
changes nothing alone, and does once the other is on). Constants joined by a
pair at or above ``--threshold`` are printed as groups: tune a group together.

This is a screen, not a result. A pair that never interacts here can still meet
over several turns (one constant changes the board the other later reads), and
a pair that interacts often may not move a game. Joint sweeps decide, and see
"Which constants move together" in ``docs/design/marshal-joint.md`` for how they
were run on marshal.

The bot must keep no module state and read its constants live at call time
(marshal's contract), since the census patches them in place around each call
and puts them back before the game moves on. Nothing here changes how any seat
plays.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from starconquest.settings import Settings  # noqa: E402

CELLS = ("random:11:11", "random:13:3", "random:18:6",
         "random:24:3", "random:24:12", "random:18:18")
OPPONENTS = ("marshal", "actuary")
MAX_TURNS = 600
BUDGET_SCALE = 100.0          # as tools/sweep.py and tools/bot_replay.py

# marshal's constants, each moved one step either way from its shipped value (a
# flag is switched off). Steps are big enough to change a decision somewhere and
# small enough to stay a variant of the same bot.
MARSHAL_PERTURB = (
    ("FRONTIER_GUARD", 0.35), ("FRONTIER_GUARD", 0.75),
    ("POUR_GUARD", 0.55), ("POUR_GUARD", 1.0),
    ("FAST_GUARD_WEIGHT", 1.0),
    ("DEFEND_PAD", -0.10), ("DEFEND_PAD", 0.20),
    ("RISK_PARITY", 0.8), ("RISK_PARITY", 1.2),
    ("NEUTRAL_MARGIN", 1.15), ("NEUTRAL_MARGIN", 1.5),
    ("OVERWHELM", 1.5), ("OVERWHELM", 3.0),
    ("BEYOND_DECAY", 0.0), ("BEYOND_DECAY", 0.7),
    ("RELIEF_AWARE", 0.4), ("RELIEF_AWARE", 0.9),
    ("RELIEF_IN_FLIGHT", 0.25), ("RELIEF_IN_FLIGHT", 1.0),
    ("DENY_SWAP", 0.5), ("DENY_SWAP", 1.5),
    ("DENY_SWAP_MIN_TURNS", 3), ("DENY_SWAP_MIN_TURNS", 5),
    ("RIVAL_REFLOOD_MIN_TURNS", 3), ("RIVAL_REFLOOD_MIN_TURNS", 0),
    ("FEED", 1.5), ("FEED", 6.0),
    ("FEED_MAX_TURNS", 2), ("FEED_MAX_TURNS", 4),
    ("HOLD_FOLLOW", 1.0),
    ("COMMIT_SURPLUS", False), ("RESERVE_PINCER", False), ("CONSOLIDATE", False),
    ("AVOID_ABANDONED", False), ("RETREAT_NEAREST", False),
    ("FLOW_AVOIDS_ABANDONED", False), ("DENY_SWAP_SURPLUS_ONLY", False),
    ("FEED_FRONT", False), ("REMNANT", False),
)

_bot = None


def _module(bot: str):
    """The bot's own loaded module, the one ``ai.decide`` routes its seats to."""
    global _bot
    if _bot is None:
        ai.load_models()
        ai.set_budget_scale(BUDGET_SCALE)
        _bot = sys.modules[f"sc_model_{bot}"]
    return _bot


def _cell_settings(spec: str) -> Settings:
    mode, nodes, ly = spec.split(":")
    return Settings(mode=mode, nodes=int(nodes), ship_ly_per_turn=float(ly))


def _label(const: str, value) -> str:
    return f"{const}={value}"


def _sends(orders) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = defaultdict(int)
    for o in orders:
        out[(o.source_id, o.dest_id)] += o.ships
    return dict(out)


def _minus(a: dict, b: dict) -> dict:
    out = {}
    for k in a.keys() | b.keys():
        d = a.get(k, 0) - b.get(k, 0)
        if d:
            out[k] = d
    return out


def _decide(module, state, pid, patch: dict) -> dict:
    """The bot's sends with ``patch`` applied to its module, then put back."""
    saved = {k: getattr(module, k) for k in patch}
    try:
        for k, v in patch.items():
            setattr(module, k, v)
        return _sends(module.decide(state, pid))
    finally:
        for k, v in saved.items():
            setattr(module, k, v)


def _contested(state, pid) -> bool:
    """A rival system borders ours, or a rival fleet is bound for one of ours."""
    for s in state.systems.values():
        if s.owner_id == pid and any(state.systems[n].owner_id not in (pid, 0)
                                     for n in s.neighbors):
            return True
    return any(f.owner_id != pid and state.systems[f.dest_id].owner_id == pid
               for f in state.fleets)


def census_position(module, state, pid, perturb):
    """``(singles, pairs)`` at one position.

    ``singles[i]`` is ``(changed, ships)``: whether perturbation ``i`` alone
    changes the orders, and by how many ships (L1). ``pairs[(i, j)]`` is
    ``(interacts, ships, j_masks_i, j_enables_i, i_masks_j, i_enables_j,
    both_change_alone)``.
    """
    base = _decide(module, state, pid, {})
    alone = [_decide(module, state, pid, {c: v}) for c, v in perturb]
    singles = []
    for sends in alone:
        d = _minus(sends, base)
        singles.append((bool(d), sum(abs(v) for v in d.values())))
    pairs = {}
    for i, j in itertools.combinations(range(len(perturb)), 2):
        (ci, vi), (cj, vj) = perturb[i], perturb[j]
        if ci == cj:
            continue
        both = _decide(module, state, pid, {ci: vi, cj: vj})
        keys = both.keys() | alone[i].keys() | alone[j].keys() | base.keys()
        inter = [both.get(k, 0) - alone[i].get(k, 0) - alone[j].get(k, 0) + base.get(k, 0)
                 for k in keys]
        i_on_j = bool(_minus(both, alone[j]))     # what i still changes once j is on
        j_on_i = bool(_minus(both, alone[i]))
        fi, fj = singles[i][0], singles[j][0]
        pairs[(i, j)] = (any(inter), sum(abs(v) for v in inter),
                         fi and not i_on_j, (not fi) and i_on_j,
                         fj and not j_on_i, (not fj) and j_on_i, fi and fj)
    return singles, pairs


def play(job) -> dict:
    """One stock duel, censused at every ``every``-th turn up to ``cap`` positions."""
    bot, perturb, cell, opponent, seed, every, cap = job
    module = _module(bot)
    settings = _cell_settings(cell)
    settings_mod.apply_globals(settings)
    state = mapgen.generate(seed, settings.mode, settings.nodes, 2)
    pid = 1 + seed % 2
    for p in state.players.values():
        p.is_human = False
    state.players[pid].ai_strategy = bot
    state.players[3 - pid].ai_strategy = opponent

    n = len(perturb)
    singles = {scope: [[0, 0] for _ in range(n)] for scope in ("all", "contested")}
    pairs = {scope: defaultdict(lambda: [0] * 7) for scope in ("all", "contested")}
    positions = {"all": 0, "contested": 0}
    while state.winner is None and state.turn < MAX_TURNS:
        if (state.turn % every == 0 and positions["all"] < cap
                and any(s.owner_id == pid for s in state.systems.values())):
            s1, p1 = census_position(module, state, pid, perturb)
            scopes = ("all", "contested") if _contested(state, pid) else ("all",)
            for scope in scopes:
                positions[scope] += 1
                for i, (changed, ships) in enumerate(s1):
                    singles[scope][i][0] += changed
                    singles[scope][i][1] += ships
                for key, rec in p1.items():
                    tgt = pairs[scope][key]
                    for k, v in enumerate(rec):
                        tgt[k] += v
        engine.end_turn(state, decide=ai.decide)
    return {"cell": cell, "positions": positions, "singles": singles,
            "pairs": {scope: {f"{i},{j}": v for (i, j), v in d.items()}
                      for scope, d in pairs.items()}}


def run(bot, perturb, cells, opponents, seeds, every, cap, workers) -> dict:
    """Every job, merged per cell: raw counts, so ``report`` can re-read them."""
    jobs = [(bot, perturb, c, o, s, every, cap)
            for c in cells for o in opponents for s in seeds]
    merged: dict = {}
    with ProcessPoolExecutor(workers) as pool:
        for done, out in enumerate(pool.map(play, jobs), start=1):
            cell = merged.setdefault(out["cell"], {
                "positions": {"all": 0, "contested": 0},
                "singles": {s: [[0, 0] for _ in perturb] for s in ("all", "contested")},
                "pairs": {"all": {}, "contested": {}}})
            for scope in ("all", "contested"):
                cell["positions"][scope] += out["positions"][scope]
                for i, rec in enumerate(out["singles"][scope]):
                    cell["singles"][scope][i][0] += rec[0]
                    cell["singles"][scope][i][1] += rec[1]
                for key, rec in out["pairs"][scope].items():
                    tgt = cell["pairs"][scope].setdefault(key, [0] * 7)
                    for k, v in enumerate(rec):
                        tgt[k] += v
            print(f"  {done}/{len(jobs)} games censused", end="\r", flush=True)
    print()
    return {"bot": bot, "perturb": [[c, v] for c, v in perturb], "cells": merged}


def _pooled(data: dict) -> dict:
    """Every cell's counts summed into one more cell, ``pooled``."""
    n = len(data["perturb"])
    out = {"positions": {"all": 0, "contested": 0},
           "singles": {s: [[0, 0] for _ in range(n)] for s in ("all", "contested")},
           "pairs": {"all": {}, "contested": {}}}
    for cell in data["cells"].values():
        for scope in ("all", "contested"):
            out["positions"][scope] += cell["positions"][scope]
            for i, rec in enumerate(cell["singles"][scope]):
                out["singles"][scope][i][0] += rec[0]
                out["singles"][scope][i][1] += rec[1]
            for key, rec in cell["pairs"][scope].items():
                tgt = out["pairs"][scope].setdefault(key, [0] * 7)
                for k, v in enumerate(rec):
                    tgt[k] += v
    return out


def _constant_pairs(cell: dict, perturb, scope: str) -> dict:
    """Per pair of constants, the pair of directions that interacts most often:
    ``(rate, share, ships, i, j, raw)``. ``share`` is the interacting positions
    over those where either perturbation changes anything."""
    n = max(1, cell["positions"][scope])
    best: dict = {}
    for key, rec in cell["pairs"][scope].items():
        i, j = (int(x) for x in key.split(","))
        union = (cell["singles"][scope][i][0] + cell["singles"][scope][j][0]
                 - rec[6])
        rate = rec[0] / n
        pair = tuple(sorted((perturb[i][0], perturb[j][0])))
        val = (rate, rec[0] / max(1, union), rec[1] / n, i, j, rec)
        if pair not in best or rate > best[pair][0]:
            best[pair] = val
    return best


def _groups(best: dict, threshold: float) -> list[tuple[set, list]]:
    """Constants joined by a pair interacting at ``threshold`` or more."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = [(pair, val) for pair, val in best.items() if val[0] >= threshold]
    for (a, b), _ in edges:
        parent[find(a)] = find(b)
    groups: dict[str, tuple[set, list]] = {}
    for (a, b), val in edges:
        root = find(a)
        members, links = groups.setdefault(root, (set(), []))
        members.update((a, b))
        links.append(((a, b), val))
    return sorted(groups.values(), key=lambda g: -len(g[0]))


def report(data: dict, *, scope: str = "contested", top: int = 30,
           threshold: float = 0.02) -> None:
    perturb = [tuple(p) for p in data["perturb"]]
    labels = [_label(c, v) for c, v in perturb]
    cells = sorted(data["cells"])
    pooled = _pooled(data)
    short = [c.split(":", 1)[1] for c in cells]
    counts = ", ".join(f"{s} {data['cells'][c]['positions'][scope]}"
                       for s, c in zip(short, cells))
    print(f"\n{data['bot']}: {pooled['positions'][scope]} {scope} positions ({counts})")

    print(f"\nALONE: share of {scope} positions whose orders change, by cell (ly/turn)")
    width = max(len(lb) for lb in labels) + 2
    print(f"  {'perturbation':<{width}}" + "".join(f"{s:>8}" for s in short)
          + "   pooled  ships")
    for i, lb in enumerate(labels):
        row = f"  {lb:<{width}}"
        for c in cells:
            cell = data["cells"][c]
            row += f"{100 * cell['singles'][scope][i][0] / max(1, cell['positions'][scope]):7.1f}%"
        n = max(1, pooled["positions"][scope])
        row += (f"  {100 * pooled['singles'][scope][i][0] / n:6.1f}%"
                f"  {pooled['singles'][scope][i][1] / n:5.2f}")
        print(row)

    best = _constant_pairs(pooled, perturb, scope)
    per_cell = {c: _constant_pairs(data["cells"][c], perturb, scope) for c in cells}
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    print(f"\nPAIRS: share of {scope} positions where the pair's joint change is not "
          "the sum of its two\n       (the strongest pair of directions), then the "
          "same share by cell")
    print(f"  {'constants':<50}{'inter':>7}{'share':>7}{'ships':>7}  "
          + "".join(f"{s:>8}" for s in short))
    for pair, val in ranked[:top]:
        row = (f"  {pair[0] + ' x ' + pair[1]:<50}{100 * val[0]:6.1f}%"
               f"{100 * val[1]:6.1f}%{val[2]:7.2f}  ")
        for c in cells:
            v = per_cell[c].get(pair)
            row += f"{100 * v[0] if v else 0:7.1f}%"
        print(row)

    print("\nMASKS AND ENABLES, pooled (positions): 'B masks A' means A changes the "
          "orders alone\n  and nothing once B is on; 'B enables A' means A changes "
          "nothing alone and does once B is on")
    for pair, val in ranked[:top]:
        i, j, rec = val[3], val[4], val[5]
        if rec[2] + rec[3] + rec[4] + rec[5] == 0:
            continue
        a, b = labels[i], labels[j]
        print(f"  {a} / {b}: interacts {rec[0]}; {b} masks {rec[2]}, enables {rec[3]}; "
              f"{a} masks {rec[4]}, enables {rec[5]}")

    print(f"\nGROUPS: constants joined by a pair interacting on {100 * threshold:.0f}% "
          f"of {scope} positions or more")
    for members, links in _groups(best, threshold):
        print(f"  {{{', '.join(sorted(members))}}}")
        for (a, b), val in sorted(links, key=lambda kv: -kv[1][0]):
            print(f"      {a} x {b}: {100 * val[0]:.1f}%")
    joined = {m for members, _ in _groups(best, threshold) for m in members}
    alone = sorted({c for c, _ in perturb} - joined)
    if alone:
        print(f"  joined to nothing at this threshold: {', '.join(alone)}")


def _parse_perturb(spec: str) -> tuple[str, object]:
    """``CONSTANT=VALUE``, the value read as a bool, an int or a float, in that
    order, as ``tools/sweep.py`` reads an arm's."""
    const, _, raw = spec.partition("=")
    raw = raw.strip()
    if not const or not raw:
        raise SystemExit(f"--perturb {spec!r}: expected CONSTANT=VALUE")
    if raw.lower() in ("true", "false"):
        return const.strip(), raw.lower() == "true"
    for cast in (int, float):
        try:
            return const.strip(), cast(raw)
        except ValueError:
            pass
    raise SystemExit(f"--perturb {spec!r}: {raw!r} is not a number or a bool")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--bot", default="marshal")
    p.add_argument("--perturb", action="append", default=[], metavar="CONSTANT=VALUE",
                   help="a perturbation; repeatable. Default: marshal's table.")
    p.add_argument("--cell", action="append", default=[], metavar="MODE:NODES:LY")
    p.add_argument("--opp", action="append", default=[])
    p.add_argument("--seeds", type=int, default=8)
    p.add_argument("--seed-base", type=int, default=31000)
    p.add_argument("--every", type=int, default=5, help="census every Nth turn")
    p.add_argument("--cap", type=int, default=50, help="positions per game at most")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--scope", choices=("contested", "all"), default="contested")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--threshold", type=float, default=0.02)
    p.add_argument("--out", type=Path, default=ROOT / "results" / "coupling_census.json")
    p.add_argument("--report", type=Path, help="re-print a saved census, play nothing")
    a = p.parse_args(argv)

    if a.report:
        report(json.loads(a.report.read_text()), scope=a.scope, top=a.top,
               threshold=a.threshold)
        return 0
    if a.perturb:
        perturb = tuple(_parse_perturb(s) for s in a.perturb)
    elif a.bot == "marshal":
        perturb = MARSHAL_PERTURB
    else:
        raise SystemExit(f"no default perturbations for {a.bot!r}: pass --perturb")
    module = _module(a.bot)
    for const, _value in perturb:
        if not hasattr(module, const):
            raise SystemExit(f"{a.bot} defines no {const!r}")
    for opp in a.opp or OPPONENTS:
        if opp not in ai.STRATEGIES:
            raise SystemExit(f"unknown opponent {opp!r}")
    seeds = list(range(a.seed_base, a.seed_base + a.seeds))
    data = run(a.bot, perturb, a.cell or list(CELLS), a.opp or list(OPPONENTS),
               seeds, a.every, a.cap, a.workers)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data))
    print(f"wrote {a.out}")
    report(data, scope=a.scope, top=a.top, threshold=a.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
