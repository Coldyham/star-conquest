#!/usr/bin/env python3
"""Is marshal's frontier guard a pool? A census of seen strikes, behaviour unchanged.

    uv run python tools/guard_census.py                      # the six cells, 100 seeds
    uv run python tools/guard_census.py --seeds 10 --out results/guard_census.jsonl
    uv run python tools/guard_census.py --report results/guard_census.jsonl

Step 1 of "Is the guard a pool?" (``docs/design/marshal.md``). Stock marshal duels
marshal, actuary (its default stop) and knower (Predict); the seat marshal is censused in
alternates with the seed's parity. For every rival fleet that will land on a
marshal frontier system down a lane of 2 turns or more, at the first marshal
decide that sees it, this records:

* the strike (ships, the stack it left, its lane ``L``);
* the target's ships and guard, now and the turn before the launch;
* the released guard: how far each marshal neighbour's guard fell since then;
* pooled neighbours: marshal systems next to the target on a lane shorter than
  ``L`` whose rival neighbours are all that rival's stacks next to the target;
* what marshal ordered (relief in, and how much of it was released guard; the
  target held, evacuated or consolidated);
* whether the strike's landing left the target ours, and if not whether it was
  retaken within ``RETAKE_WINDOW`` turns and by which ships.

Nothing here changes how any seat plays: marshal's own guard formula is read off
its module, and its orders off the ``TurnRecord`` each turn returns.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from starconquest.model import AiParams  # noqa: E402
from starconquest.settings import Settings  # noqa: E402

CELLS = ("random:11:11", "random:13:3", "random:18:6",
         "random:24:3", "random:24:12", "random:18:18")
OPPONENTS = {"marshal": ("marshal", 1.0), "actuary": ("actuary", 1),
             "knower": ("knower", 1)}
RETAKE_WINDOW = 4
MAX_TURNS = 600
BUDGET_SCALE = 100.0          # as tools/sweep.py and tools/bot_replay.py

_ma = None


def _marshal():
    global _ma
    if _ma is None:
        ai.load_models()
        ai.set_budget_scale(BUDGET_SCALE)
        _ma = sys.modules["sc_model_marshal"]
    return _ma


def _cell_settings(spec: str) -> Settings:
    mode, nodes, ly = spec.split(":")
    return Settings(mode=mode, nodes=int(nodes), ship_ly_per_turn=float(ly))


def _guards(state, pid) -> dict[int, int]:
    """Every marshal system's Phase 0 guard, by marshal's own formula."""
    ma = _marshal()
    out = {}
    for sid, s in state.systems.items():
        if s.owner_id != pid:
            continue
        frontier = any(state.systems[n].owner_id != pid for n in s.neighbors)
        out[sid] = (math.ceil(ma.FRONTIER_GUARD * ma._max_adjacent_enemy(state, pid, s))
                    if frontier and ma.FRONTIER_GUARD > 0 else 0)
    return out


def _rival_stacks(state, sid, rival) -> set[int]:
    return {n for n in state.systems[sid].neighbors
            if state.systems[n].owner_id == rival}


def _pooled(state, pid, target, rival, lane) -> list[int]:
    """Marshal neighbours of ``target`` closer than ``lane`` that face only
    ``rival``'s stacks that also border ``target``."""
    stacks = _rival_stacks(state, target, rival)
    out = []
    for n in state.systems[target].neighbors:
        if state.systems[n].owner_id != pid:
            continue
        if (state.travel_turns(n, target) or 99) >= lane:
            continue
        hostile = {m for m in state.systems[n].neighbors
                   if state.systems[m].owner_id not in (0, pid)}
        if hostile and hostile <= stacks:
            out.append(n)
    return out


def _fleet_key(f, turn):
    return (f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_total,
            turn - (f.turns_total - f.turns_remaining))


def play(cell: str, opponent: str, seed: int) -> dict:
    """One duel, censused. Returns the game's strikes and its frontier tallies."""
    _marshal()
    settings = _cell_settings(cell)
    settings_mod.apply_globals(settings)
    state = mapgen.generate(seed, settings.mode, settings.nodes, 2)
    pid = 1 + seed % 2
    rival = 3 - pid
    strat, aux = OPPONENTS[opponent]
    for p in state.players.values():
        p.is_human = False
    state.players[pid].ai_strategy = "marshal"
    state.players[pid].ai_params = AiParams()
    state.players[rival].ai_strategy = strat
    state.players[rival].ai_params = replace(AiParams(), aux=aux)

    seen: set = set()
    strikes: list[dict] = []
    open_: list[dict] = []        # not yet landed, or lost and watching for a retake
    prev_guard: dict[int, int] = {}
    front_turns = pooled_turns = 0

    while state.winner is None and state.turn < MAX_TURNS:
        t = state.turn
        sysmap = state.systems
        guard = _guards(state, pid)

        # Frontier system-turns facing the rival, and how many have a pooled neighbour.
        for sid in guard:
            stacks = _rival_stacks(state, sid, rival)
            if not stacks:
                continue
            front_turns += 1
            lane = max((state.travel_turns(s, sid) or 0) for s in stacks)
            if _pooled(state, pid, sid, rival, lane):
                pooled_turns += 1

        # Strikes seen for the first time at this decide.
        fresh = []
        for f in state.fleets:
            if f.owner_id != rival or f.turns_total < 2:
                continue
            key = _fleet_key(f, t)
            if key in seen:
                continue
            seen.add(key)
            target = sysmap[f.dest_id]
            if target.owner_id != pid or f.dest_id not in guard:
                continue
            if not any(sysmap[n].owner_id != pid for n in target.neighbors):
                continue
            nbrs = [n for n in target.neighbors if sysmap[n].owner_id == pid]
            released = {n: max(0, prev_guard.get(n, 0) - guard.get(n, 0)) for n in nbrs}
            pooled = _pooled(state, pid, f.dest_id, rival, f.turns_total)
            rec = {
                "cell": cell, "opp": opponent, "seed": seed, "turn": t,
                "ships": f.ships, "source": f.source_id, "target": f.dest_id,
                "lane": f.turns_total, "remaining": f.turns_remaining,
                "land": t + f.turns_remaining,
                "other_inbound": sum(g.ships for g in state.fleets
                                     if g.dest_id == f.dest_id and g.owner_id == rival
                                     and g is not f),
                "t_ships": target.ships, "t_prod": target.production,
                "t_guard": guard[f.dest_id], "t_guard_prev": prev_guard.get(f.dest_id),
                "t_released": max(0, prev_guard.get(f.dest_id, 0) - guard[f.dest_id]),
                "released": {str(n): v for n, v in released.items() if v},
                "pooled": {str(n): sysmap[n].ships for n in pooled},
                "relief_first": 0, "relief_released": 0, "relief_total": 0,
                "relief_from": {}, "out": 0, "out_to": {},
                "held": None, "retaken": None, "retake_by": [],
            }
            fresh.append(rec)
            strikes.append(rec)
            open_.append(rec)

        # Our ships landing on a watched target during this turn.
        arriving = defaultdict(list)
        for g in state.fleets:
            if g.owner_id == pid and g.turns_remaining <= 1:
                arriving[g.dest_id].append((g.source_id, g.ships))

        record = engine.end_turn(state, decide=ai.decide)
        mine = [o for o in record.orders if o.owner_id == pid]
        for o in mine:
            if (state.travel_turns(o.source_id, o.dest_id) or 99) == 1:
                arriving[o.dest_id].append((o.source_id, o.ships))

        for rec in fresh:
            for o in mine:
                if o.dest_id == rec["target"]:
                    rec["relief_first"] += o.ships
                    rec["relief_released"] += min(o.ships, rec["released"].get(str(o.source_id), 0))
                    rec["relief_from"][str(o.source_id)] = o.ships
                elif o.source_id == rec["target"]:
                    rec["out"] += o.ships
                    rec["out_to"][str(o.dest_id)] = o.ships
        for rec in open_:
            if rec["held"] is not None:
                continue
            for o in mine:
                if (o.dest_id == rec["target"]
                        and t + (state.travel_turns(o.source_id, o.dest_id) or 99) <= rec["land"]):
                    rec["relief_total"] += o.ships

        now = state.turn
        for rec in list(open_):
            owner = state.systems[rec["target"]].owner_id
            if rec["held"] is None:
                if now >= rec["land"]:
                    rec["held"] = owner == pid
                    if rec["held"]:
                        open_.remove(rec)
            elif owner == pid:
                rec["retaken"] = now - rec["land"]
                rec["retake_by"] = arriving.get(rec["target"], [])
                open_.remove(rec)
            elif now - rec["land"] >= RETAKE_WINDOW:
                rec["retaken"] = False
                open_.remove(rec)
        prev_guard = guard

    return {"cell": cell, "opp": opponent, "seed": seed, "pid": pid,
            "winner": state.winner, "turns": state.turn,
            "front_turns": front_turns, "pooled_turns": pooled_turns,
            "strikes": strikes}


def _job(args):
    return play(*args)


def run(cells, opponents, seeds, out: Path, workers: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            g = json.loads(line)
            done.add((g["cell"], g["opp"], g["seed"]))
    jobs = [(c, o, s) for s in seeds for c in cells for o in opponents
            if (c, o, s) not in done]
    with out.open("a") as fh, ProcessPoolExecutor(workers) as pool:
        for i, game in enumerate(pool.map(_job, jobs, chunksize=1), start=1):
            fh.write(json.dumps(game) + "\n")
            fh.flush()
            if i % 50 == 0:
                print(f"{i}/{len(jobs)}", file=sys.stderr, flush=True)


def _bucket(rec) -> str:
    rel = rec["relief_released"]
    if rel <= 0:
        return "none"
    return "enough" if rec["t_ships"] + rec["relief_first"] >= rec["ships"] else "some"


def _action(rec) -> str:
    if rec["t_ships"] > 0 and rec["out"] >= rec["t_ships"]:
        return "evacuated"
    if rec["relief_first"] > 0:
        return "relieved"
    if rec["out"] > 0:
        return "part-out"
    return "stood"


def report(path: Path) -> None:
    games = [json.loads(line) for line in path.read_text().splitlines()]
    groups: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        groups[g["cell"]].append(g)
        groups[f"vs {g['opp']}"].append(g)
        groups["ALL"].append(g)

    def pct(a, b):
        return f"{100 * a / b:5.1f}%" if b else "    -"

    print(f"{len(games)} games\n")
    head = (f"{'group':<14}{'games':>6}{'pooled':>8}{'strikes':>8}{'guarded':>8}"
            f"{'rel>0':>8}{'relG>0':>8}{'evac':>7}{'relvd':>7}{'stood':>7}"
            f"{'hold':>7}{'retake':>8}")
    print(head)
    for name, gs in groups.items():
        recs = [r for g in gs for r in g["strikes"] if r["held"] is not None]
        guarded = [r for r in recs if (r["t_guard_prev"] or 0) > 0]
        acts = defaultdict(int)
        for r in guarded:
            acts[_action(r)] += 1
        lost = [r for r in guarded if not r["held"]]
        print(f"{name:<14}{len(gs):>6}"
              f"{pct(sum(g['pooled_turns'] for g in gs), sum(g['front_turns'] for g in gs)):>8}"
              f"{len(recs):>8}{len(guarded):>8}"
              f"{pct(sum(r['relief_first'] > 0 for r in guarded), len(guarded)):>8}"
              f"{pct(sum(r['relief_released'] > 0 for r in guarded), len(guarded)):>8}"
              f"{pct(acts['evacuated'], len(guarded)):>7}{pct(acts['relieved'], len(guarded)):>7}"
              f"{pct(acts['stood'], len(guarded)):>7}"
              f"{pct(sum(r['held'] for r in guarded), len(guarded)):>7}"
              f"{pct(sum(bool(r['retaken'] is not False and r['retaken'] is not None) for r in lost), len(lost)):>8}")

    print("\nGuarded targets: hold rate by released-guard relief at the first decide")
    print(f"{'group':<14}" + "".join(f"{b:>18}" for b in ("none", "some", "enough")))
    for name, gs in groups.items():
        guarded = [r for g in gs for r in g["strikes"]
                   if r["held"] is not None and (r["t_guard_prev"] or 0) > 0]
        cells = []
        for b in ("none", "some", "enough"):
            rs = [r for r in guarded if _bucket(r) == b]
            cells.append(f"{pct(sum(r['held'] for r in rs), len(rs))} of {len(rs):<5}")
        print(f"{name:<14}" + "".join(f"{c:>18}" for c in cells))

    print("\nGuarded targets by action: hold rate, and of those lost, retaken in "
          f"{RETAKE_WINDOW}")
    allg = [r for g in games for r in g["strikes"]
            if r["held"] is not None and (r["t_guard_prev"] or 0) > 0]
    for act in ("evacuated", "relieved", "part-out", "stood"):
        rs = [r for r in allg if _action(r) == act]
        lost = [r for r in rs if not r["held"]]
        back = [r for r in lost if r["retaken"] not in (None, False)]
        print(f"  {act:<10}{len(rs):>6}  hold {pct(sum(r['held'] for r in rs), len(rs))}"
              f"  retaken {pct(len(back), len(lost))}")

    print("\nWho retakes a lost guarded target (ships landing on the retake turn)")
    by = defaultdict(int)
    for r in allg:
        if r["held"] or r["retaken"] in (None, False):
            continue
        evac = set(r["out_to"])
        pooled = set(r["pooled"])
        for src, ships in r["retake_by"]:
            kind = ("evacuee-host" if str(src) in evac else
                    "pooled" if str(src) in pooled else "other")
            by[kind] += ships
    tot = sum(by.values())
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<14}{v:>7} ships  {pct(v, tot)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cell", action="append", default=[])
    p.add_argument("--opp", action="append", default=[], choices=sorted(OPPONENTS))
    p.add_argument("--seeds", type=int, default=100)
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", type=Path, default=ROOT / "results" / "guard_census.jsonl")
    p.add_argument("--report", type=Path)
    a = p.parse_args(argv)
    if a.report:
        report(a.report)
        return 0
    seeds = list(range(a.seed_base, a.seed_base + a.seeds))
    run(a.cell or list(CELLS), a.opp or list(OPPONENTS), seeds, a.out, a.workers)
    report(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
