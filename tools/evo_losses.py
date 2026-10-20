#!/usr/bin/env python3
"""Why evolved loses, game by game: the deciding turn of each loss and what
went wrong around it.

    uv run python tools/evo_losses.py --vs marshal actuary --seeds 100 --jobs 8

Plays evolved (the shipped weights, or ``--weights FILE``) against each rival
in every seating on fresh seeds; in a cell with more than two players every
other seat is the rival, and "the rival" below means any of them. Every turn
it reads the engine's own events (``end_turn(on_event=...)``) and tallies, for
evolved:

- **lost savable / lost unsavable**: a system of ours taken by the rival. Savable
  when, at the first sight of the strike, our garrison plus its production by
  arrival plus every ship of ours that could land there in time covered the
  attackers times ``edge_defending``;
- **surprise**: the strike was first seen one turn out (no time to react);
- **stripped**: we launched ships out of a system while a visible strike was
  inbound to it, and it fell;
- **garrison lost**: ships we had in systems that fell;
- **failed attack**: an attack of ours that landed and did not take the target
  (ships lost);
- **race lost**: the rival took a neutral next to one of our systems;
- **ships traded**: ships each side lost in fights.

The deciding turn of a loss is the first turn after which our income share
never gets back to half. Each category is counted over the whole game and in a
window of ``--window`` turns either side of the deciding turn, for losses and
(over the whole game) for wins, since the side that is losing does everything
worse. Reading the rival's play is offline analysis; nothing here reaches the
bot.

With ``--control`` each seed also plays one game of the rival in every seat and
tallies one of its seats (rotating with the seed) the same way, and the report
sets evolved's losses beside the control's: a category every loser shows is a
symptom of losing, not evolved's fault.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, combat, engine, replay, turnfilm  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

CELLS = ("random:7:6:2", "random:11:6:2", "random:13:3:2")
SEED_BASE = 3_200_000
MAX_TURNS = 400
BOT = "evolved"
OUT = ROOT / "results" / "evolved" / "losses"
CATS = ("lost_savable", "lost_unsavable", "surprise", "stripped", "garrison_lost",
        "failed_attack", "failed_ships", "race_lost", "ships_lost", "ships_killed")


def _income(state, pid: int) -> float:
    return sum(1.0 / s.production for s in state.systems.values()
               if s.owner_id == pid and s.production > 0)


def play(job) -> dict:
    cell, seed, rival, seat, weights, control = job
    if not ai.known(rival) or BOT not in ai.STRATEGIES:
        ai.load_models()
    if weights:
        from tools.evo_bench import use_weights
        use_weights(weights)
    ai.set_budget_scale(math.inf)
    mode, nodes, ly, players = cell.split(":")
    state = build_state(Settings(mode=mode, nodes=int(nodes), players=int(players),
                                 ship_ly_per_turn=float(ly), autoplay=True), seed)
    me = seat
    for pid, p in state.players.items():
        if not p.is_neutral:
            p.ai_strategy = BOT if pid == me and not control else rival
    them = {pid for pid, p in state.players.items() if not p.is_neutral and pid != me}
    edge_def = combat.edge_defending(1.1 / 0.9)

    share: list[float] = []
    events: list[tuple[int, str, int]] = []      # (turn, category, amount)
    threat: dict[int, dict] = {}                  # our system -> first sight of a strike

    while state.winner is None and state.turn < MAX_TURNS:
        turn = state.turn
        systems = state.systems
        owner_before = {sid: s.owner_id for sid, s in systems.items()}
        # Strikes inbound to our systems: remember the first sight of each.
        inbound: dict[int, list] = defaultdict(list)
        for f in state.fleets:
            inbound[f.dest_id].append(f)
        for sid, s in systems.items():
            hostile = [f for f in inbound.get(sid, ()) if f.owner_id in them]
            if s.owner_id != me or not hostile:
                threat.pop(sid, None)
                continue
            if sid in threat:
                continue
            eta = min(f.turns_remaining for f in hostile)
            attack = sum(f.ships for f in hostile)
            produced = ((s.prod_progress + eta) // s.production) if s.production > 0 else 0
            help_ = sum(systems[n].ships for n in s.neighbors
                        if systems[n].owner_id == me and (state.travel_turns(n, sid) or 1) <= eta)
            help_ += sum(f.ships for f in inbound.get(sid, ())
                         if f.owner_id == me and f.turns_remaining <= eta)
            threat[sid] = {"turn": turn, "eta": eta,
                           "savable": s.ships + produced + help_ >= attack * edge_def,
                           "stripped": False}

        got: list = []
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide, on_event=got.append)

        for ev in got:
            if isinstance(ev, turnfilm.Launched) and ev.owner_id == me and ev.source_id in threat:
                threat[ev.source_id]["stripped"] = True
            if not isinstance(ev, turnfilm.Landed):
                continue
            for st in ev.steps:
                won = st.winner == st.attacker
                a_lost = st.attacker_ships - (st.survivors if won else 0)
                d_lost = st.defender_ships - (0 if won else st.survivors)
                for who, lost, other in ((st.attacker, a_lost, d_lost),
                                         (st.defender, d_lost, a_lost)):
                    if who == me:
                        events.append((turn, "ships_lost", lost))
                        events.append((turn, "ships_killed", other))
                if st.attacker == me and not won:
                    events.append((turn, "failed_attack", 1))
                    events.append((turn, "failed_ships", st.attacker_ships))
            if ev.was_owner == me and ev.owner_id in them:
                t = threat.get(ev.node_id, {"savable": False, "eta": 1, "stripped": False})
                events.append((turn, "lost_savable" if t["savable"] else "lost_unsavable", 1))
                if t["eta"] <= 1:
                    events.append((turn, "surprise", 1))
                if t["stripped"]:
                    events.append((turn, "stripped", 1))
                events.append((turn, "garrison_lost", ev.was_ships))
            if ev.was_owner == 0 and ev.owner_id in them:
                if any(owner_before[n] == me for n in state.systems[ev.node_id].neighbors):
                    events.append((turn, "race_lost", 1))

        a, b = _income(state, me), sum(_income(state, q) for q in them)
        share.append(a / (a + b) if a + b else 1.0 / (len(them) + 1))

    won = state.winner == me
    # Deciding turn: the first after which our share never gets back to a
    # fair share (a half in a duel).
    fair = 1.0 / (len(them) + 1)
    decide_at = 0
    for i, x in enumerate(share):
        if x >= fair:
            decide_at = i + 1
    return {"cell": cell, "seed": seed, "rival": rival, "seat": seat, "won": won,
            "subject": "control" if control else BOT,
            "winner": state.winner, "turns": state.turn, "decide_at": decide_at,
            "ahead": any(x > fair for x in share),
            "share10": share[min(9, len(share) - 1)] if share else 0.5,
            "share": [round(x, 3) for x in share], "events": events}


def _stats(rows, window: int) -> dict:
    """One subject's facts and per-game category counts, for its wins and losses."""
    losses = [r for r in rows if not r["won"] and r["winner"] is not None]
    wins = [r for r in rows if r["won"]]
    out = {"n": len(rows), "won": len(wins), "lost": len(losses)}
    if losses:
        out["never ahead"] = sum(1 for r in losses if not r["ahead"]) / len(losses)
        out["decided by t15"] = sum(1 for r in losses if 0 < r["decide_at"] <= 15) / len(losses)
        out["median deciding turn"] = sorted(r["decide_at"] for r in losses)[len(losses) // 2]
        out["share t10, losses"] = sum(r["share10"] for r in losses) / len(losses)

    def per_game(games, near: bool):
        tot = defaultdict(float)
        for r in games:
            for t, cat, n in r["events"]:
                if near and abs(t - r["decide_at"]) > window:
                    continue
                tot[cat] += n
        res = {c: tot[c] / max(1, len(games)) for c in CATS}
        res["kill/loss"] = res["ships_killed"] / res["ships_lost"] if res["ships_lost"] else 0.0
        return res

    out["near"], out["whole"], out["wins"] = per_game(losses, True), per_game(losses, False), per_game(wins, False)
    return out


def summarise(rows, window: int) -> None:
    for rival in sorted({r["rival"] for r in rows}):
        for cell in sorted({r["cell"] for r in rows if r["rival"] == rival}):
            rs = [r for r in rows if r["rival"] == rival and r["cell"] == cell]
            subjects = [s for s in (BOT, "control") if any(r.get("subject", BOT) == s for r in rs)]
            st = {s: _stats([r for r in rs if r.get("subject", BOT) == s], window) for s in subjects}
            print(f"\n== {cell} vs {rival}" + (f" (control: {rival} in every seat)" if "control" in st else ""))
            for s in subjects:
                x = st[s]
                print(f"  {s}: {x['won']} won, {x['lost']} lost, {x['n'] - x['won'] - x['lost']} unfinished, of {x['n']}")
            facts = ("never ahead", "decided by t15", "median deciding turn", "share t10, losses")
            print(f"{'':22}" + "".join(f"{s:>12}" for s in subjects))
            for f in facts:
                print(f"{f:22}" + "".join(f"{st[s].get(f, float('nan')):12.2f}" for s in subjects))
            print(f"{'per losing game':22}" + "".join(f"{s + ' ±' + str(window):>14}{s + ' all':>13}" for s in subjects)
                  + "".join(f"{s + ' wins':>14}" for s in subjects))
            for c in CATS + ("kill/loss",):
                line = f"{c:22}"
                for s in subjects:
                    line += f"{st[s]['near'][c]:14.2f}{st[s]['whole'][c]:13.2f}"
                for s in subjects:
                    line += f"{st[s]['wins'][c]:14.2f}"
                print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vs", nargs="*", default=["marshal", "actuary"])
    ap.add_argument("--cell", action="append", default=[])
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--label", default="k3")
    ap.add_argument("--weights", default=None,
                    help="play evolved from this weights file (the shipped one is untouched)")
    ap.add_argument("--report", action="store_true", help="re-print from the saved rows")
    ap.add_argument("--control", action="store_true",
                    help="also play the rival in every seat, one game per seed, as the baseline")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.label}.jsonl"
    if not args.report:
        weights = str(Path(args.weights).resolve()) if args.weights else None
        jobs = [(c, args.seed_base + k, rival, seat, weights, False) for rival in args.vs
                for c in (args.cell or CELLS) for k in range(args.seeds)
                for seat in range(1, int(c.split(":")[3]) + 1)]
        if args.control:
            jobs += [(c, args.seed_base + k, rival, 1 + k % int(c.split(":")[3]), None, True)
                     for rival in args.vs for c in (args.cell or CELLS) for k in range(args.seeds)]
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool, open(path, "w") as fh:
            for i, row in enumerate(pool.map(play, jobs, chunksize=4), 1):
                fh.write(json.dumps(row) + "\n")
                if i % 200 == 0:
                    print(f"  {i}/{len(jobs)}", flush=True)
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    summarise(rows, args.window)
    return 0


if __name__ == "__main__":
    sys.exit(main())
