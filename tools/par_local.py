#!/usr/bin/env python3
"""Local search over whole plans for the best score on one setup (a daily puzzle).

    uv run python tools/par_local.py --daily tools/par_inputs/2026-11-04_puzzle.json --seconds 600
    uv run python tools/par_local.py --daily FILE --seed-line results/par/human/2026-10-30_u1_10-9.json
    uv run python tools/par_local.py --daily FILE --seed-line results/par/live/2026-10-30_puzzle.best.json

A plan is a list of ``(turn, source, dest, count)`` launches, ``count`` ``None`` meaning "the
whole garrison at that moment", so a relay stays a relay when an earlier launch shifts. It is
scored by playing it through the engine against the live rivals, so the number of launches
costs nothing: a line of twelve launches is as cheap to try as one of three. A search starts from
a plan (a saved line, or the empty one), then repeatedly changes one thing (shifts a launch's
turn, resizes it, redirects it, drops it, adds one) and keeps the change unless the score gets
worse; restarts from the best plan so far when it stalls.

Score: a win is ``(0, turn, lost, launches)``; anything else is ``(1, rival ships and systems left, lost,
launches)`` so the search can climb toward a win and prefers the shorter plan. Every new best is appended to ``results/par/local/<name>.jsonl`` as
it is found, the best line goes to ``<name>.best.json`` (a real `replay.GameLog`), and the best plan
to ``<name>.plan.json``; a rerun starts from that plan. It finds a line but proves nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay
from starconquest.model import GameState, Order
from tools import par_exhaustive as pe
from tools import par_search as ps
from starconquest.settings import Settings

Plan = list[tuple[int, int, int, int | None]]
MAX_LAUNCHES = 30
HORIZON_SLACK = 2   # turns past the best win a trial plays on, to see near misses


def evaluate(root: GameState, me: int, plan: Plan, horizon: int):
    """Play ``plan``: ``(score, per-turn launches)``. The score is lower for a better plan."""
    board = ps.clone(root)
    played: list[tuple] = []
    by_turn: dict[int, list[tuple]] = {}
    for t, s, d, n in plan:
        by_turn.setdefault(t, []).append((s, d, n))
    while board.winner is None and board.turn < horizon:
        orders: list[Order] = []
        spent: dict[int, int] = {}
        for s, d, n in by_turn.get(board.turn, []):
            src = board.systems.get(s)
            if src is None or src.owner_id != me or d not in src.neighbors:
                continue
            free = src.ships - spent.get(s, 0)
            count = free if n is None else min(n, free)
            if count > 0:
                orders.append(Order(me, s, d, count))
                spent[s] = spent.get(s, 0) + count
        played.append(tuple((o.source_id, o.dest_id, o.ships) for o in orders))
        ps.step(board, orders)
    if board.winner == me:
        return (0, board.turn, board.players[me].ships_lost, len(plan)), played
    rival_ships = sum(sy.ships for sy in board.systems.values() if sy.owner_id not in (0, me))
    rival_ships += sum(f.ships for f in board.fleets if f.owner_id not in (0, me))
    rivals = sum(sy.owner_id not in (0, me) for sy in board.systems.values())
    return (1, rivals * 100 + rival_ships, board.players[me].ships_lost, len(plan)), played


def plan_from_log(log: replay.GameLog, me: int, root: GameState) -> Plan:
    """A saved line as a plan, a whole-garrison launch recorded as ``None``."""
    boards: list[GameState] = []
    replay.reconstruct(log, on_turn=lambda state: boards.append(ps.clone(state)))
    plan: Plan = []
    for t in range(len(log.turns)):
        for o in log.orders_for(t):
            if o.owner_id == me:
                whole = boards[t].systems[o.source_id].ships == o.ships
                plan.append((t, o.source_id, o.dest_id, None if whole else o.ships))
    return plan


def mutate(plan: Plan, root: GameState, me: int, horizon: int, rng: random.Random) -> Plan:
    new = list(plan)
    ids = sorted(root.systems)
    roll = rng.random()
    if new and roll < 0.30:
        i = rng.randrange(len(new))
        t, s, d, n = new[i]
        new[i] = (max(0, min(horizon - 1, t + rng.choice((-2, -1, 1, 2)))), s, d, n)
    elif new and roll < 0.50:
        i = rng.randrange(len(new))
        t, s, d, n = new[i]
        if n is None:
            n = rng.randint(1, 14)
        else:
            n = None if rng.random() < 0.3 else max(1, n + rng.choice((-3, -2, -1, 1, 2, 3)))
        new[i] = (t, s, d, n)
    elif new and roll < 0.62:
        i = rng.randrange(len(new))
        t, s, d, n = new[i]
        new[i] = (t, s, rng.choice(root.systems[s].neighbors), n)
    elif new and roll < 0.74:
        del new[rng.randrange(len(new))]
    elif new and roll < 0.80:
        i = rng.randrange(len(new))
        t, s, d, n = new[i]
        new.append((t + rng.choice((-1, 1, 2)), s, d, n))
    else:
        s = rng.choice(ids)
        if root.systems[s].neighbors:
            new.append((rng.randrange(horizon), s, rng.choice(root.systems[s].neighbors),
                        None if rng.random() < 0.7 else rng.randint(1, 14)))
    if rng.random() < 0.3 and len(new) > 0:
        return mutate(new, root, me, horizon, rng)
    return new if len(new) <= MAX_LAUNCHES else plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--daily", required=True, help="a setup file (tools/par_inputs/ or results/par/live_inputs/)")
    ap.add_argument("--seed-line", nargs="*", default=[], help="saved lines (replay logs) to start from")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--rng", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=14, help="turns a trial plays before it counts as no win")
    ap.add_argument("--stall", type=int, default=3000, help="trials without a new best before restarting from it")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results" / "par" / "local")
    args = ap.parse_args(argv)

    ai.load_models()
    ai.set_budget_scale(math.inf)
    data = json.loads(Path(args.daily).read_text())
    name = f"{data['day']}_{data['kind']}"
    cfg = Settings.from_dict(data["settings"])
    assert cfg.seed is not None
    seed = cfg.seed
    search = ps.Search(cfg, seed, dice="honest", width=1, budget=1, max_turns=args.horizon)
    root, me = search.root, search.me
    out = args.results_dir
    out.mkdir(parents=True, exist_ok=True)
    ledger, plan_path, best_path = out / f"{name}.jsonl", out / f"{name}.plan.json", out / f"{name}.best.json"
    rng = random.Random(args.rng)

    seeds: list[Plan] = [[]]
    for line in args.seed_line:
        log = replay.load(Path(line))
        seeds.append(plan_from_log(log, log.winner if log.winner is not None else me, root))
    if plan_path.exists():
        seeds.append([tuple(x) for x in json.loads(plan_path.read_text())["plan"]])  # type: ignore[misc]

    horizon = args.horizon
    best_plan: Plan = []
    best_score: tuple = (9, math.inf, math.inf, 0)
    for plan in seeds:
        score, _ = evaluate(root, me, plan, horizon)
        print(f"seed plan of {len(plan)} launches: {score}", flush=True)
        if score < best_score:
            best_plan, best_score = plan, score

    def save(plan: Plan, score: tuple, trials: int) -> None:
        _, played = evaluate(root, me, plan, horizon)
        path: tuple = ()
        for turn in played:
            path = (path, turn)
        log = pe.line_log(root, cfg, seed, search.strategies, me, path)
        log.path = best_path
        log.save()
        plan_path.write_text(json.dumps({"score": score, "plan": plan}))
        with ledger.open("a") as f:
            f.write(json.dumps({"event": "best", "score": score, "launches": len(plan), "trials": trials,
                                "plan": plan}) + "\n")
            f.flush()

    started = time.perf_counter()
    trials = since_best = 0
    current, current_score = best_plan, best_score
    save(best_plan, best_score, 0)
    while time.perf_counter() - started < args.seconds:
        trials += 1
        since_best += 1
        candidate = mutate(current, root, me, horizon, rng)
        score, _ = evaluate(root, me, candidate, horizon)
        if score <= current_score:
            current, current_score = candidate, score
            if score < best_score:
                best_plan, best_score, since_best = candidate, score, 0
                if score[0] == 0:
                    horizon = min(horizon, int(score[1]) + HORIZON_SLACK)
                save(best_plan, best_score, trials)
                print(f"[{time.perf_counter() - started:5.0f}s, {trials} trials] best {best_score}, "
                      f"{len(best_plan)} launches", flush=True)
        if since_best >= args.stall:
            current, current_score, since_best = best_plan, best_score, 0
            print(f"[{time.perf_counter() - started:5.0f}s] restart from best", flush=True)
    print(f"done: {trials} trials, best {best_score} with {len(best_plan)} launches; "
          f"{trials / (time.perf_counter() - started):.0f} trials/s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
