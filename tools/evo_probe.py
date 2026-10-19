#!/usr/bin/env python3
"""Quick probes for evolved, run before training or shipping a change.

    uv run python tools/evo_probe.py moves --bias stage=2 converge=2 --seeds 25
    uv run python tools/evo_probe.py noise --weights results/evolved/k3_weights.py
    uv run python tools/evo_probe.py identity --rev HEAD

``moves``: plays evolved against a rival in both seatings and counts the moves
it chooses by kind, its wins and its decide time. ``--bias GROUP=X`` adds X to
a weight group's bias (a new move starts switched off; this is how to see how
often it is available and what switching it on costs). Seeds from 2,900,000:
counts only, never a gate.

``noise``: one genome's training fitness evaluated 12 times exactly as
``evo_train`` does (fresh maps each time), and its standard deviation. A
training run whose candidates differ by less than about this much is
selecting at random.

``identity``: plays the working tree's evolved and the one at ``--rev``
(each with its own weights file) on the same boards, and counts decisions
whose orders differ. A refactor or a new switched-off move must show 0.
``--on`` also switches the untrained moves on (+3 bias) in both.

None of these touches the shipped weights file.
"""
from __future__ import annotations

import argparse
import importlib.util
import multiprocessing
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402
from tools import evo_train  # noqa: E402

UNTRAINED = ("stage", "converge", "evacuate", "recapture")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vec(weights: str | None) -> list[float]:
    if not weights:
        return list(evo_train._module()._load_weights())
    named = evo_train.read_named(Path(weights))
    return [named.get((g, f), 0.0) for g, feats in evo_train._layout() for f in feats]


def _biased(vec, layout, bias: dict[str, float]) -> list[float]:
    out, i = list(vec), 0
    for g, feats in layout:
        if g in bias:
            out[i] += bias[g]
        i += len(feats)
    return out


def _new_state(cell: str, seed: int, seats: dict[int, str]):
    mode, nodes, ly, players = cell.split(":")
    state = build_state(Settings(mode=mode, nodes=int(nodes), players=int(players),
                                 ship_ly_per_turn=float(ly), autoplay=True), seed)
    for pid, p in state.players.items():
        if not p.is_neutral:
            p.ai_strategy = seats.get(pid, seats[0])
    return state


def _moves_game(job):
    vec, cell, seed, seat, rival = job
    if not ai.known(rival):
        ai.load_models()
    policy = evo_train._module().make_policy(vec)
    kinds: Counter = Counter()
    times: list[float] = []

    def decide(state, pid):
        t0 = time.perf_counter()
        orders, chosen = policy(state, pid)
        times.append((time.perf_counter() - t0) * 1000)
        kinds.update(k[0] for k in chosen)
        return orders

    ai.register("evo_probe", decide)
    state = _new_state(cell, seed, {seat: "evo_probe", 0: rival})
    while state.winner is None and state.turn < 400:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    return state.winner == seat, kinds, times


def moves(args) -> None:
    bias = {k: float(v) for k, v in (b.split("=") for b in args.bias)}
    vec = _biased(_vec(args.weights), evo_train._layout(), bias)
    players = int(args.cell.split(":")[3])
    jobs = [(vec, args.cell, args.seed_base + k, seat, args.vs)
            for k in range(args.seeds) for seat in range(1, players + 1)]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool:
        results = list(pool.map(_moves_game, jobs))
    wins = sum(w for w, _, _ in results)
    kinds = sum((k for _, k, _ in results), Counter())
    times = sorted(t for _, _, ts in results for t in ts)
    print(f"{args.cell} vs {args.vs}, bias {bias or 'none'}: won {wins}/{len(jobs)}")
    print("  moves: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    if times:
        print(f"  decide ms: median {statistics.median(times):.2f}  "
              f"p95 {times[int(0.95 * len(times))]:.2f}  max {times[-1]:.2f}")


def noise(args) -> None:
    vec = _vec(args.weights)
    panel = args.panel or list(evo_train.PANEL)
    cells = args.cell or list(evo_train.CELLS)
    ctx = multiprocessing.get_context("spawn")
    fits, t0 = [], time.perf_counter()
    with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool:
        for g in range(900, 912):
            games = evo_train.schedule(g, args.seeds, panel, cells)
            chunk = max(1, len(games) // (2 * args.jobs))
            futs = [pool.submit(evo_train.play_batch, vec, games[i:i + chunk], 250, args.mid,
                                args.trade) for i in range(0, len(games), chunk)]
            fits.append(sum(sum(f.result()) for f in futs) / len(games))
    print(f"panel {' '.join(panel)}; cells {' '.join(cells)}; {len(games)} games an evaluation")
    print(f"  mean {statistics.mean(fits):.3f}  SD {statistics.stdev(fits):.4f}  "
          f"range {min(fits):.3f}-{max(fits):.3f}  ({time.perf_counter() - t0:.0f}s)")


def identity(args) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = Path(tmp)
        for name in ("evolved.py", "_evolved_weights.py"):
            text = subprocess.run(["git", "show", f"{args.rev}:models/{name}"], cwd=ROOT,
                                  capture_output=True, text=True, check=True).stdout
            (old_dir / name).write_text(text)
        new = _load("evo_probe_new", ROOT / "models" / "evolved.py")
        new_vec = list(new._load_weights())
        sys.modules.pop("sc_evolved_weights", None)
        old = _load("evo_probe_old", old_dir / "evolved.py")
        old_vec = list(old._load_weights())
    arms = [(old_vec, new_vec)]
    if args.on:
        on = {g: 3.0 for g in UNTRAINED}
        arms.append((_biased(old_vec, old.LAYOUT, on), _biased(new_vec, new.LAYOUT, on)))
    ai.load_models()
    turns = differ = 0
    for ov, nv in arms:
        po, pn = old.make_policy(ov), new.make_policy(nv)

        def decide(state, pid, po=po, pn=pn):
            nonlocal turns, differ
            a, b = po(state, pid)[0], pn(state, pid)[0]
            turns += 1
            differ += ([(o.source_id, o.dest_id, o.ships) for o in a]
                       != [(o.source_id, o.dest_id, o.ships) for o in b])
            return b

        ai.register("evo_probe_id", decide)
        for cell in ("random:11:6:2", "random:18:6:2", "random:31:6:5"):
            for seed in range(3_300_000, 3_300_000 + args.seeds):
                state = _new_state(cell, seed, {1: "evo_probe_id", 0: args.vs})
                while state.winner is None and state.turn < 300:
                    replay.reseed(state)
                    engine.end_turn(state, decide=ai.decide)
    print(f"working tree against {args.rev}: {turns} decisions, {differ} differ")
    if differ:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("moves")
    m.add_argument("--weights")
    m.add_argument("--bias", nargs="*", default=[], help="GROUP=X, added to the group's bias")
    m.add_argument("--cell", default="random:11:6:2")
    m.add_argument("--vs", default="marshal")
    m.add_argument("--seeds", type=int, default=25)
    m.add_argument("--seed-base", type=int, default=2_900_000)
    m.add_argument("--jobs", type=int, default=8)
    n = sub.add_parser("noise")
    n.add_argument("--weights")
    n.add_argument("--panel", nargs="*", default=None)
    n.add_argument("--cell", action="append", default=[])
    n.add_argument("--seeds", type=int, default=160)
    n.add_argument("--mid", type=int, default=40)
    n.add_argument("--trade", type=float, default=0.0)
    n.add_argument("--jobs", type=int, default=8)
    i = sub.add_parser("identity")
    i.add_argument("--rev", default="HEAD")
    i.add_argument("--on", action="store_true", help="also with the untrained moves switched on")
    i.add_argument("--vs", default="marshal")
    i.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()
    {"moves": moves, "noise": noise, "identity": identity}[args.cmd](args)


if __name__ == "__main__":
    main()
