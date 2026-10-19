#!/usr/bin/env python3
"""Prove a bot folded into another as an aux stop still plays exactly as it did.

    uv run python tools/fold_check.py                       # every pair, base 0689b0a
    uv run python tools/fold_check.py --pairs claudebot     # just the pairs named so
    uv run python tools/fold_check.py --base REV --games 12 --every 4

Each pair is an old profile (a strategy name and aux, as the code at ``--base``
plays it) and the new profile that replaces it (``knower`` at 3 for ``council`` at
1, say). Positions come from roster games played here; at each one the seat under
test is handed the old profile in a checkout of ``--base`` and the new profile in
this tree, and the two order lists must be identical. The old side runs in its
own process on a ``git worktree``, so it loads the bots, the registry and the
models/ directory it was written against: a folded bot looked up by name in the
new tree would only be found through `ai.LEGACY`, which is part of what is under
test.

Positions are pickled boards plus the `Settings` that built them (balance knobs
live in `config`, applied before each decide), so both sides see the same board,
the same rng start and the same globals. Wall-clock guards are lifted
(`ai.set_budget_scale`), the one thing that would make a search depend on the
machine.
"""

from __future__ import annotations

import argparse
import copy
import json
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# (label, old name, old aux, new name, new aux). The new side may name a legacy
# strategy too, which proves `ai.canonical` routes it.
PAIRS = [
    ("council-on", "council", 1.0, "knower", 3.0),
    ("council-off", "council", 0.0, "knower", 2.0),
    ("council-legacy", "council", 1.0, "council", 1.0),
    ("hunter", "hunter", 1.0, "actuary", 3.0),
    ("claudebot", "claudebot", 1.0, "thinker", 0.0),
    ("claudebot-legacy", "claudebot", 1.0, "claudebot", 1.0),
    ("thinker", "thinker", 1.0, "thinker", 1.0),
    ("knower-0", "knower", 0.0, "knower", 0.0),
    ("knower-1", "knower", 1.0, "knower", 1.0),
    ("knower-2", "knower", 2.0, "knower", 2.0),
    ("knower-12", "knower", 12.0, "knower", 12.0),
    ("actuary-0", "actuary", 0.0, "actuary", 0.0),
    ("actuary-1", "actuary", 1.0, "actuary", 1.0),
    ("actuary-2", "actuary", 2.0, "actuary", 2.0),
    ("actuary-12", "actuary", 12.0, "actuary", 12.0),
]

# Line-ups the positions are played out of: every family that exists on both
# sides, oracles among the rivals so a search meets someone to predict, and the
# legacy names as rivals (the new side reaches them through `ai.LEGACY`).
LINEUPS = [
    [("actuary", 1.0), ("marshal", 1.0)],
    [("knower", 1.0), ("actuary", 1.0)],
    [("thinker", 1.0), ("knower", 2.0)],
    [("marshal", 1.0), ("council", 1.0)],
    [("actuary", 2.0), ("claudebot", 1.0), ("knower", 1.0)],
    [("heuristic", 1.0), ("rusherplus", 1.0), ("marshal", 1.0)],
]
CELLS = [(18, 6.0), (24, 12.0), (24, 3.0), (30, 6.0)]


def _boot(root: Path):
    sys.path.insert(0, str(root))
    from starconquest import ai
    ai.load_models(root / "models")
    ai.set_budget_scale(float("inf"))
    return ai


def cmd_positions(args) -> None:
    ai = _boot(HERE)
    from starconquest import engine, replay, settings
    out = []
    for g in range(args.games):
        lineup = LINEUPS[g % len(LINEUPS)]
        nodes, speed = CELLS[(g // len(LINEUPS)) % len(CELLS)]
        cfg = settings.Settings(players=len(lineup), nodes=nodes, ship_ly_per_turn=speed)
        for i, (name, aux) in enumerate(lineup):
            cfg.ai_strategy[i] = name
            cfg.ai[i].aux = aux
        seed = 9100 + g
        state = settings.build_state(cfg, seed)
        for p in state.players.values():
            p.is_human = False
        while state.winner is None and state.turn < args.turns:
            replay.reseed(state)
            if state.turn % args.every == 0 and state.turn > 0:
                out.append((cfg.to_dict(), pickle.dumps(state)))
            engine.end_turn(state, decide=ai.decide)
        print(f"game {g + 1}/{args.games}: {[n for n, _ in lineup]} {nodes}:{speed:g}, "
              f"{len(out)} positions", file=sys.stderr)
    Path(args.out).write_bytes(pickle.dumps(out))


def cmd_orders(args) -> None:
    root = Path(args.root)
    ai = _boot(root)
    from starconquest import settings
    positions = pickle.loads(Path(args.positions).read_bytes())
    profiles = json.loads(args.profiles)
    result: dict[str, list] = {label: [] for label, _, _ in profiles}
    for i, (cfg_dict, blob) in enumerate(positions):
        cfg = settings.Settings.from_dict(cfg_dict)
        settings.apply_globals(cfg)
        base = pickle.loads(blob)
        for label, name, aux in profiles:
            rows = []
            for pid, player in sorted(base.players.items()):
                if player.is_neutral or not player.alive:
                    continue
                state = copy.deepcopy(base)
                state.players[pid].ai_strategy = name
                state.players[pid].ai_params.aux = aux
                orders = ai.decide(state, pid)
                rows.append([pid, sorted((o.source_id, o.dest_id, o.ships) for o in orders)])
            result[label].append(rows)
        if (i + 1) % 25 == 0:
            print(f"  {root.name}: {i + 1}/{len(positions)}", file=sys.stderr)
    Path(args.out).write_text(json.dumps(result))


def cmd_check(args) -> int:
    pairs = [p for p in PAIRS if not args.pairs or any(k in p[0] for k in args.pairs)]
    work = Path(tempfile.mkdtemp(prefix="fold_check_"))
    positions = work / "positions.pkl"
    me = str(Path(__file__).resolve())
    subprocess.run([sys.executable, me, "positions", "--out", str(positions),
                    "--games", str(args.games), "--every", str(args.every),
                    "--turns", str(args.turns)], check=True)
    tree = work / "base"
    subprocess.run(["git", "-C", str(HERE), "worktree", "add", "-q", "--detach",
                    str(tree), args.base], check=True)
    try:
        old = [(label, name, aux) for label, name, aux, _, _ in pairs]
        new = [(label, name, aux) for label, _, _, name, aux in pairs]
        runs = [(tree, old, work / "old.json"), (HERE, new, work / "new.json")]
        procs = [subprocess.Popen([sys.executable, me, "orders", "--root", str(root),
                                   "--positions", str(positions), "--profiles", json.dumps(prof),
                                   "--out", str(out)]) for root, prof, out in runs]
        if any(p.wait() for p in procs):
            print("an orders run failed", file=sys.stderr)
            return 2
    finally:
        subprocess.run(["git", "-C", str(HERE), "worktree", "remove", "--force", str(tree)])
    a = json.loads((work / "old.json").read_text())
    b = json.loads((work / "new.json").read_text())
    n = len(pickle.loads(positions.read_bytes()))
    bad = 0
    for label, oname, oaux, nname, naux in pairs:
        diff = [i for i, (x, y) in enumerate(zip(a[label], b[label])) if x != y]
        seats = sum(len(rows) for rows in a[label])
        status = "same" if not diff else f"DIFFERENT at {len(diff)} positions (first {diff[:5]})"
        print(f"{label:18} {oname}@{oaux:g} -> {nname}@{naux:g}: {n} positions, "
              f"{seats} seat decisions, {status}")
        bad += bool(diff)
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("positions")
    p.add_argument("--out", required=True)
    for a in (p,):
        a.add_argument("--games", type=int, default=24)
        a.add_argument("--every", type=int, default=5)
        a.add_argument("--turns", type=int, default=90)
    o = sub.add_parser("orders")
    o.add_argument("--root", required=True)
    o.add_argument("--positions", required=True)
    o.add_argument("--profiles", required=True)
    o.add_argument("--out", required=True)
    ap.add_argument("--base", default="0689b0a")
    ap.add_argument("--pairs", nargs="*")
    ap.add_argument("--games", type=int, default=24)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--turns", type=int, default=90)
    args = ap.parse_args(argv)
    if args.cmd == "positions":
        cmd_positions(args)
        return 0
    if args.cmd == "orders":
        cmd_orders(args)
        return 0
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
