#!/usr/bin/env python3
"""How well a hand-written rule predicts actuary's strikes, without running it.

    uv run python tools/hunt_predict.py --seeds 6

Plays actuary against actuary and, at every decide, asks each model which
systems this seat will launch at this turn, then compares that with the orders
actuary really issues from the same board. A target counts as struck if any
ships are sent at it, scored separately for rival-held and neutral targets;
"emptied" scores the sources that launch at least half their garrison (the
openings a counter-punch would hit).

Models (no other bot's code is run or imported):
  M0 holds         nothing is struck
  M1 worst roll    a target is struck if one source next door, or several landing
                   on the same turn, beat its garrison at arrival (production and
                   its own inbound fleets included) on the worst roll
  M2 + relief      M1, with the holder's garrisons next door that could land by
                   then counted at REINFORCE of their ships
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, combat, config, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

SWING_FLOOR = 1.1 / 0.9
REINFORCE = 0.3
MODELS = ("M0", "M1", "M2")
CELLS = ("7:6", "11:6", "18:6", "24:3", "18:18")


def built_by(s, turns: int) -> int:
    if s.production <= 0 or (s.owner_id == 0 and not config.NEUTRAL_PRODUCES):
        return 0
    return (s.prod_progress + turns) // s.production


def predict(state, pid, relief: float | None, sources: set | None = None) -> set[int]:
    """Targets the rule says ``pid`` strikes this turn; ``sources`` collects the
    systems that could take one alone."""
    edge = combat.edge_attacking(SWING_FLOOR)
    sysmap = state.systems
    inbound = defaultdict(list)          # dest -> [(turns, owner, ships)]
    for f in state.fleets:
        inbound[f.dest_id].append((f.turns_remaining, f.owner_id, f.ships))
    out = set()
    for tid, t in sysmap.items():
        if t.owner_id == pid:
            continue
        by_d = defaultdict(list)
        for sid in t.neighbors:
            s = sysmap[sid]
            if s.owner_id == pid and s.ships > 0:
                by_d[state.travel_turns(sid, tid)].append((s.ships, sid))
        for d, sends in by_d.items():
            mine = sum(n for w, o, n in inbound[tid] if o == pid and w <= d)
            held = t.ships + built_by(t, d) + sum(n for w, o, n in inbound[tid]
                                                  if o == t.owner_id and w <= d)
            if relief and t.owner_id != 0:
                held += relief * sum(sysmap[n].ships for n in t.neighbors
                                     if sysmap[n].owner_id == t.owner_id
                                     and state.travel_turns(n, tid) + 1 <= d)
            need = max(math.floor(held * edge) + 1, held + 1) - mine
            if sources is not None:
                sources.update(sid for n, sid in sends if n >= need)
            if max(n for n, _ in sends) >= need or (len(sends) > 1 and sum(n for n, _ in sends) >= need):
                out.add(tid)
    return out


BINS = (1.0, 1.1, 1.222, 1.5, 2.0)    # ratio bins: sent over garrison on landing


def strike_ratio(state, pid, orders, tid) -> float:
    """The best same-arrival ratio of ``pid``'s ships at ``tid`` (sent now plus
    already flying) to what holds it on landing."""
    t = state.systems[tid]
    sent = defaultdict(int)
    for o in orders:
        if o.dest_id == tid:
            sent[state.travel_turns(o.source_id, tid)] += o.ships
    best = 0.0
    for d, n in sent.items():
        mine = sum(f.ships for f in state.fleets if f.dest_id == tid and f.owner_id == pid
                   and f.turns_remaining <= d)
        held = t.ships + built_by(t, d) + sum(f.ships for f in state.fleets if f.dest_id == tid
                                             and f.owner_id == t.owner_id and f.turns_remaining <= d)
        best = max(best, (n + mine) / max(held * config.DEFENDER_ADVANTAGE, 0.5))
    return best


def play(cell: str, seed: int, bot: str = "actuary", rival: str = "actuary") -> dict:
    if "actuary" not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    nodes, ly = cell.split(":")
    state = build_state(Settings(mode="random", nodes=int(nodes), players=2,
                                 ship_ly_per_turn=float(ly), autoplay=True), seed)
    watch = 1 + seed % 2                 # the bot's seat alternates with the seed
    for pid in (1, 2):
        p = state.players[pid]
        p.ai_strategy = bot if pid == watch else rival
        p.ai_params = replace(p.ai_params, aux=1.0)
    tally = defaultdict(int)             # (model, kind, tp/fp/fn) -> count

    def decide(st, pid):
        if pid != watch:
            return ai.decide(st, pid)
        can = set()
        preds = {"M0": set(), "M1": predict(st, pid, None, can), "M2": predict(st, pid, REINFORCE)}
        orders = ai.decide(st, pid)
        sent = defaultdict(int)
        for o in orders:
            sent[o.source_id] += o.ships
        struck = {o.dest_id for o in orders if st.systems[o.dest_id].owner_id != pid}
        for tid in struck:
            if st.systems[tid].owner_id != 0:
                r = strike_ratio(st, pid, orders, tid)
                tally[("ratio", str(sum(r >= b for b in BINS)), "")] += 1
        for tid, t in st.systems.items():
            if t.owner_id == pid:
                continue
            kind = "neutral" if t.owner_id == 0 else "rival"
            for m, p in preds.items():
                a, b = tid in struck, tid in p
                if a or b:
                    tally[(m, kind, "tp" if a and b else ("fn" if a else "fp"))] += 1
        for sid, s in st.systems.items():
            if s.owner_id == pid and s.ships > 1:
                e = 2 * sent[sid] >= s.ships
                tally[("src", "emptied", "yes" if e else "no")] += 1
                if e or sid in can:
                    tally[("srcpred", "", "tp" if e and sid in can else ("fn" if e else "fp"))] += 1
                if e:
                    tally[("share", "", "")] += round(100 * sent[sid] / s.ships)
        tally[("turns", "", "")] += 1
        return orders

    while state.winner is None and state.turn < 400:
        replay.reseed(state)
        engine.end_turn(state, decide=decide)
    return dict(tally)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--seed-base", type=int, default=9_500_000)
    ap.add_argument("--cells", nargs="*", default=list(CELLS))
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--ai", default="actuary", help="the bot whose strikes are scored")
    ap.add_argument("--rival", default="actuary", help="who it plays")
    a = ap.parse_args()
    jobs = [(c, s) for c in a.cells for s in range(a.seed_base, a.seed_base + a.seeds)]
    by_cell = defaultdict(lambda: defaultdict(int))
    with ProcessPoolExecutor(a.jobs) as ex:
        n = len(jobs)
        for (c, _), t in zip(jobs, ex.map(play, *zip(*jobs), [a.ai] * n, [a.rival] * n)):
            for k, v in t.items():
                by_cell[c][k] += v
                by_cell["all"][k] += v
    for c in [*a.cells, "all"]:
        t = by_cell[c]
        src = t[("src", "emptied", "yes")] + t[("src", "emptied", "no")]
        print(f"\n{c}: {t[('turns', '', '')]} decides; sources that empty: "
              f"{t[('src', 'emptied', 'yes')]}/{src}")
        ratios = [t[("ratio", str(i), "")] for i in range(len(BINS) + 1)]
        if sum(ratios):
            edges = ("<1.0", "1.0-1.1", "1.1-1.22", "1.22-1.5", "1.5-2", "2+")
            print("  strike ratio on rival systems: " + "  ".join(
                f"{e} {100 * r / sum(ratios):.0f}%" for e, r in zip(edges, ratios)))
        tp, fp, fn = (t[("srcpred", "", x)] for x in ("tp", "fp", "fn"))
        if tp + fn:
            print(f"  emptied sources: predicted {tp + fp}, precision {tp / max(1, tp + fp):.2f}, "
                  f"recall {tp / (tp + fn):.2f}; mean share sent {t[('share', '', '')] / (tp + fn):.0f}%")
        for kind in ("rival", "neutral"):
            for m in MODELS:
                tp, fp, fn = (t[(m, kind, x)] for x in ("tp", "fp", "fn"))
                prec = tp / (tp + fp) if tp + fp else math.nan
                rec = tp / (tp + fn) if tp + fn else math.nan
                print(f"  {kind:7} {m}  struck {tp + fn:5}  predicted {tp + fp:5}  "
                      f"precision {prec:5.2f}  recall {rec:5.2f}")


if __name__ == "__main__":
    main()
