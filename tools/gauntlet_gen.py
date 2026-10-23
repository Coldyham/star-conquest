#!/usr/bin/env python3
"""Generate a gauntlet of rising difficulty from a spec: deal, probe, pick.

    uv run python tools/gauntlet_gen.py --run sym8 --spec spec.json
    uv run python tools/gauntlet_gen.py --run quick --stages 5 --seed 41 \\
        --spec-json '{"rules": {"mode": {"fix": "symmetric"}}, "opponents": {"not": ["knower"]}}'
    uv run python tools/gauntlet_gen.py --run sym8 --targets --target-slack 3   # rerun: picks again, plays nothing

Deals ``--candidates`` setups per stage from the spec (`starconquest/dealer.py`),
each with opponents for its stage's depth, then plays every candidate with each
probe bot in seat 1 (`tests.sim.play_settings`, budgets lifted, so a game is the
same on any machine). A candidate's difficulty is the share of probes that lose
it, as `tools/gauntlet_check.py` measures a stage, with the probes' mean winning
turns breaking ties. A candidate no probe won is never picked; the rest are
ordered by difficulty and one is taken at each of ``stages`` evenly spaced
places, so the gauntlet rises across the whole spread.

With ``--targets`` each stage gets a target: the slowest probe win plus
``--target-slack`` turns. Design: docs/design/gauntlet.md, "Generated gauntlets".

Every game is a line in ``results/gauntlet_gen/<run>.jsonl`` the moment it
finishes; the run's spec, seed and candidates head the file, and a rerun plays
only the games not there. The gauntlet is written to
``results/gauntlet_gen/<run>.json`` (and ``--out``), the file a desktop player
drops in the game's gauntlets folder.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, dealer, gauntlet  # noqa: E402
from starconquest.settings import Settings, random_seed  # noqa: E402
from tests import sim  # noqa: E402

RESULTS = ROOT / "results" / "gauntlet_gen"
PROBES = ("rusherplus", "heuristic", "thinker", "marshal")
# Bots slow enough to make a probe game, or every turn of a game they sit in,
# take minutes: worth saying before a run with them starts.
SLOW = ("knower", "actuary")
MAX_TURNS = 400


def _shown(path: Path) -> str:
    """``path`` relative to the repo when it is in it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def play_one(key: str, stage: dict, bot: str) -> dict:
    if bot not in ai.STRATEGIES:
        ai.load_models()
    ai.set_budget_scale(math.inf)
    cfg = Settings.from_dict(stage)
    t0 = time.perf_counter()
    res = sim.play_settings(cfg, cfg.seed, bot, max_turns=MAX_TURNS)
    return {"key": key, "bot": bot, "won": res.won and not res.timed_out, "turns": res.turns,
            "lost": res.lost, "timed_out": res.timed_out, "secs": round(time.perf_counter() - t0, 2)}


def candidates(spec: dealer.Spec, seed: int, per_stage: int) -> list[dict]:
    """Every candidate the run deals, in order: ``{key, depth, stage}``. A pure
    function of the spec and the seed, so a rerun deals the same ones."""
    rng = random.Random(f"{seed}:gauntlet")
    out, seen = [], set()
    for i in range(spec.stages):
        depth = i / (spec.stages - 1) if spec.stages > 1 else 0.0
        for _ in range(per_stage):
            s = dealer.deal(spec, rng, depth)
            key = s.challenge_key()
            if key not in seen:
                seen.add(key)
                out.append({"key": key, "depth": round(depth, 4), "stage": dealer.stage_dict(s)})
    return out


def load(path: Path) -> tuple[dict | None, list[dict]]:
    """The run's header and its game rows. A line cut short by a killed run is
    skipped."""
    head, rows = None, []
    if not path.exists():
        return head, rows
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("header"):
            head = row
        else:
            rows.append(row)
    return head, rows


def measure(cands: list[dict], rows: list[dict], probes: list[str]) -> list[dict]:
    """Each candidate with its probes' results: ``played``, ``wins`` (rows),
    ``difficulty`` (share lost) and ``win_turns`` (mean, for ties)."""
    by = {(r["key"], r["bot"]): r for r in rows}
    out = []
    for c in cands:
        games = [by[(c["key"], b)] for b in probes if (c["key"], b) in by]
        wins = [r for r in games if r["won"]]
        out.append({**c, "played": len(games), "wins": wins,
                    "difficulty": 1 - len(wins) / len(games) if games else None,
                    "win_turns": sum(r["turns"] for r in wins) / len(wins) if wins else None})
    return out


def pick(measured: list[dict], stages: int) -> list[dict]:
    """A rising sequence: the candidates some probe won, by difficulty then mean
    winning turns, one at each of ``stages`` evenly spaced places. Fewer when
    there are not that many to pick from."""
    usable = sorted((m for m in measured if m["wins"]),
                    key=lambda m: (m["difficulty"], m["win_turns"], m["key"]))
    n = len(usable)
    if n <= stages:
        return usable
    if stages == 1:
        return [usable[0]]
    return [usable[round(i * (n - 1) / (stages - 1))] for i in range(stages)]


def target(m: dict, slack: int) -> dict:
    """The slowest probe win, plus ``slack`` turns: shown possible by a bot."""
    slowest = max(m["wins"], key=lambda r: (r["turns"], r["lost"]))
    if slack:
        return {"turns": slowest["turns"] + slack}
    return {"turns": slowest["turns"], "lost": slowest["lost"]}


def build(run: str, name: str, picked: list[dict], probes: list[str], targets: bool, slack: int) -> dict:
    """The gauntlet file: linear, a stage per pick, its briefing the setup in a
    line and how many probes won it."""
    stages = []
    for i, m in enumerate(picked):
        cfg = Settings.from_dict(m["stage"])
        won = len(m["wins"])
        row = {"id": i, "title": f"Stage {i + 1}",
               "briefing": [dealer.describe(cfg),
                            f"Measured: {won} of {m['played']} test bots won this map."],
               "hint": "", "settings": m["stage"]}
        if targets:
            row["target"] = target(m, slack)
        stages.append(row)
    return {"id": f"gen-{run}"[:gauntlet.NAME_MAX], "name": name[:gauntlet.NAME_MAX], "stages": stages}


def report(measured: list[dict], picked: list[dict], probes: list[str]) -> None:
    chosen = {m["key"] for m in picked}
    print(f"\n{len(measured)} candidates, {len(probes)} probes ({', '.join(probes)})")
    for m in sorted(measured, key=lambda m: (m["depth"], m["key"])):
        if m["difficulty"] is None:
            continue
        mark = "*" if m["key"] in chosen else " "
        turns = f"{m['win_turns']:.0f}t" if m["win_turns"] is not None else "-"
        print(f" {mark} depth {m['depth']:.2f}  {m['difficulty']:4.0%} lost  {turns:>5}  "
              f"{dealer.describe(Settings.from_dict(m['stage']))}")
    print(f"\nPicked {len(picked)}:")
    for i, m in enumerate(picked):
        print(f"  {i + 1:>2}  {m['difficulty']:4.0%} lost  {m['win_turns']:.0f}t  "
              f"{dealer.describe(Settings.from_dict(m['stage']))}")
    if picked and picked[0]["difficulty"] > 0:
        print("  note: some probe lost the first stage")
    if picked and picked[-1]["difficulty"] == 0:
        print("  note: every probe won the last stage")


def read_spec(a) -> dict:
    if a.spec_json:
        return json.loads(a.spec_json)
    if a.spec:
        return json.loads(Path(a.spec).read_text(encoding="utf-8"))
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="name of this run (its results file)")
    ap.add_argument("--spec", help="a spec .json file")
    ap.add_argument("--spec-json", help="a spec as JSON text")
    ap.add_argument("--stages", type=int, help="overrides the spec's stage count")
    ap.add_argument("--seed", type=int, help="overrides the spec's seed (default: rolled, then kept)")
    ap.add_argument("--candidates", type=int, default=4, help="candidates dealt per stage (default 4)")
    ap.add_argument("--probes", nargs="*", default=list(PROBES), help="bots that measure difficulty")
    ap.add_argument("--name", help="the gauntlet's name (default: Generated <seed>)")
    ap.add_argument("--targets", action="store_true", help="give each stage the slowest probe win as a target")
    ap.add_argument("--target-slack", type=int, default=0, help="turns added to each target")
    ap.add_argument("--out", help="also write the gauntlet file here")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{a.run}.jsonl"
    head, rows = load(path)
    ai.load_models()
    if head is not None:
        # A rerun deals what the first run dealt; flags that would change it are refused.
        raw, seed, per = head["spec"], head["seed"], head["candidates"]
        changed = (a.spec, a.spec_json, a.stages is not None, a.seed not in (None, seed),
                   a.candidates not in (ap.get_default("candidates"), per))
        if any(changed):
            sys.exit(f"{_shown(path)} already holds a run: drop the spec flags or use a new --run")
    else:
        raw = read_spec(a)
        if a.stages is not None:
            raw = {**raw, "stages": a.stages}
        seed = a.seed if a.seed is not None else raw.get("seed")
        if not isinstance(seed, int):
            seed = random_seed()
        per = max(1, a.candidates)
    spec = dealer.Spec.from_dict(raw)
    if stuck := dealer.problems(spec):
        sys.exit("\n".join(stuck))
    if slow := [b for b in SLOW if b in a.probes or any(o.partition(":")[0] == b for o in spec.opponents)]:
        print(f"note: {', '.join(slow)} may sit in these games and are slow: expect minutes a game")

    cands = candidates(spec, seed, per)
    if head is None:
        with open(path, "a") as out:
            out.write(json.dumps({"header": True, "spec": spec.to_dict(), "seed": seed, "candidates": per}) + "\n")
    stage_of = {c["key"]: c["stage"] for c in cands}
    done = {(r["key"], r["bot"]) for r in rows}
    jobs = [(c["key"], b) for c in cands for b in a.probes if (c["key"], b) not in done]
    print(f"seed {seed}: {len(cands)} candidates, {len(jobs)} games to play "
          f"({len(done)} already in {_shown(path)})", flush=True)
    t0 = time.time()
    if jobs:
        with open(path, "a") as out, ProcessPoolExecutor(a.workers) as pool:
            futures = [pool.submit(play_one, key, stage_of[key], bot) for key, bot in jobs]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                out.write(json.dumps(row) + "\n")
                out.flush()
                rows.append(row)
                print(f"  {i}/{len(jobs)} {row['bot']}: {'won' if row['won'] else 'lost'} "
                      f"in {row['turns']}t ({time.time() - t0:.0f}s)", flush=True)

    measured = measure(cands, rows, a.probes)
    picked = pick(measured, spec.stages)
    report(measured, picked, a.probes)
    if len(picked) < spec.stages:
        print(f"  note: only {len(picked)} candidates were won by any probe; "
              f"deal more with a new --run and a larger --candidates")
    data = build(a.run, a.name or f"Generated {seed}", picked, a.probes, a.targets, a.target_slack)
    g = gauntlet.Gauntlet.from_dict(data)
    if g is None or gauntlet.problems(g):
        sys.exit("the picked gauntlet does not read back: nothing written")
    for where in [RESULTS / f"{a.run}.json"] + ([Path(a.out)] if a.out else []):
        where.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {where}")


if __name__ == "__main__":
    main()
