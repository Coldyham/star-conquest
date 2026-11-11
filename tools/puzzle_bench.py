#!/usr/bin/env python3
"""Turns to win on daily puzzles: a bot through the player's seat, paired by puzzle.

    uv run python tools/puzzle_bench.py --bot knower:3 --stored --dealt 100 --label fast
    uv run python tools/puzzle_bench.py --bot knower:3 --stored --dealt 100 --label base --tree ../main-tree
    uv run python tools/puzzle_bench.py --bot knower:3 --label fast --base base --report

The puzzle is won in the fewest turns, so this reads what the board reads: the
win, then turns, then ships lost. ``--stored`` takes every puzzle the board has
stored (read with the publishable key, kept in ``results/puzzle_bench/stored.json``
so a rerun plays the same ones); ``--dealt N`` deals N more off the puzzle spec for
days from 2030-01-01 (``daily.candidates``' first candidate, unprobed), kept in
``dealt.json``. Guards are lifted, so a game is the same on any machine.

``--tree DIR`` plays the code in another checkout (a ``git worktree`` of the base),
so two trees are compared on the same puzzles. Rows append to
``results/puzzle_bench/<label>/<bot>.jsonl`` as each game finishes; a rerun plays
only what is missing. ``--base`` pairs each puzzle both labels won and reports
the difference in turns, and the wins either side lost.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import multiprocessing
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "puzzle_bench"
DEALT_FROM = dt.date(2030, 1, 1)
MAX_TURNS = 200


def stored_puzzles() -> dict[str, dict]:
    path = OUT / "stored.json"
    sys.path.insert(0, str(ROOT / "tools"))
    from config_census import public_rows
    have = json.loads(path.read_text()) if path.exists() else {}
    for row in public_rows("dailies", "select=day,settings&kind=eq.puzzle&order=day.asc"):
        have.setdefault(row["day"], row["settings"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(have, indent=1))
    return have


def dealt_puzzles(n: int) -> dict[str, dict]:
    path = OUT / "dealt.json"
    have = json.loads(path.read_text()) if path.exists() else {}
    missing = [DEALT_FROM + dt.timedelta(days=i) for i in range(n)
               if f"dealt:{DEALT_FROM + dt.timedelta(days=i)}" not in have]
    if missing:
        sys.path.insert(0, str(ROOT))
        from starconquest import dealer
        from tools import daily
        for day in missing:
            settings = daily.candidates(day, limit=1, kind="puzzle")[0]
            have[f"dealt:{day}"] = dealer.stage_dict(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(have, indent=1))
    return {k: v for k, v in have.items()
            if k < f"dealt:{DEALT_FROM + dt.timedelta(days=n)}"}


def play(tree: str, key: str, stage: dict, bot: str, aux: float | None) -> dict:
    sys.path.insert(0, tree)
    from starconquest import ai, settings as S
    from starconquest.settings import Settings
    from tests import sim
    ai.load_models(Path(tree) / "models")
    assert Path(S.__file__).is_relative_to(tree), S.__file__
    ai.set_budget_scale(math.inf)
    cfg = Settings.from_dict(stage)
    state = S.build_state(cfg, cfg.seed)
    me = state.human().id
    rival = next(p.ai_strategy for p in state.players.values() if not p.is_neutral and p.id != me)
    res = sim.play_settings(cfg, cfg.seed, bot, aux=aux, max_turns=MAX_TURNS)
    return {"key": key, "won": res.won, "turns": res.turns, "lost": res.lost, "rival": rival}


def ledger(label: str, bot: str) -> Path:
    return OUT / label / f"{bot.replace(':', '@')}.jsonl"


def load(label: str, bot: str) -> dict[str, dict]:
    path = ledger(label, bot)
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows[row["key"]] = row
    return rows


def run(args, puzzles: dict[str, dict]) -> None:
    tree = str(Path(args.tree).resolve()) if args.tree else str(ROOT)
    for bot in args.bot:
        name, _, aux = bot.partition(":")
        done = load(args.label, bot)
        todo = [(k, v) for k, v in puzzles.items() if k not in done]
        print(f"{bot} [{args.label}]: {len(todo)} of {len(puzzles)} to play", flush=True)
        path = ledger(args.label, bot)
        path.parent.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context("spawn")) as ex, path.open("a") as f:
            futs = {ex.submit(play, tree, k, v, name, float(aux) if aux else None): k for k, v in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                row = fut.result()
                f.write(json.dumps(row) + "\n")
                f.flush()
                if i % 10 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}", flush=True)


def rank(row: dict) -> tuple:
    return (0, row["turns"], row["lost"]) if row["won"] else (1, 0, 0)


def report(args) -> None:
    for bot in args.bot:
        mine = load(args.label, bot)
        print(f"\n{bot} [{args.label}]: {len(mine)} puzzles, {sum(r['won'] for r in mine.values())} won")
        if not args.base:
            continue
        base = load(args.base, bot)
        keys = sorted(set(mine) & set(base))
        both = [k for k in keys if mine[k]["won"] and base[k]["won"]]
        diff = [mine[k]["turns"] - base[k]["turns"] for k in both]
        better = sum(rank(mine[k]) < rank(base[k]) for k in keys)
        worse = sum(rank(mine[k]) > rank(base[k]) for k in keys)
        print(f"  vs [{args.base}] on {len(keys)} puzzles: better {better}, worse {worse}, "
              f"same {len(keys) - better - worse}")
        print(f"  wins lost: {sum(base[k]['won'] and not mine[k]['won'] for k in keys)}, "
              f"wins gained: {sum(mine[k]['won'] and not base[k]['won'] for k in keys)}")
        if diff:
            ratio = math.exp(statistics.mean(math.log(mine[k]["turns"] / base[k]["turns"]) for k in both))
            sd = statistics.stdev(diff) if len(diff) > 1 else 0.0
            print(f"  both won {len(both)}: turns {sum(base[k]['turns'] for k in both)} -> "
                  f"{sum(mine[k]['turns'] for k in both)}, mean diff {statistics.mean(diff):+.2f} "
                  f"(z {statistics.mean(diff) / (sd / math.sqrt(len(diff))) if sd else 0:+.2f}), "
                  f"median {statistics.median(diff):+.1f}, geometric ratio {ratio - 1:+.1%}")
            by_rival: dict[str, list[int]] = {}
            for k, d in zip(both, diff):
                by_rival.setdefault(mine[k]["rival"], []).append(d)
            for rival, ds in sorted(by_rival.items()):
                print(f"    vs {rival:11s} n={len(ds):3d} mean {statistics.mean(ds):+.2f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot", nargs="+", required=True, help="name[:aux]")
    ap.add_argument("--stored", action="store_true", help="the board's stored puzzles")
    ap.add_argument("--dealt", type=int, default=0, help="puzzles dealt off the spec")
    ap.add_argument("--label", required=True)
    ap.add_argument("--tree", help="the checkout whose code plays (default: this one)")
    ap.add_argument("--base", help="pair against this label's rows")
    ap.add_argument("--jobs", type=int, default=22)
    ap.add_argument("--report", action="store_true", help="report from the ledgers only")
    args = ap.parse_args(argv)
    if not args.report:
        puzzles = {**(stored_puzzles() if args.stored else {}), **dealt_puzzles(args.dealt)}
        run(args, puzzles)
    report(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
