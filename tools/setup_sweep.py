#!/usr/bin/env python3
"""Does the roster's ranking change on the setup people actually play?

    uv run python tools/setup_sweep.py                    # most-played setup vs defaults
    uv run python tools/setup_sweep.py --seeds 300
    uv run python tools/setup_sweep.py --config-key <key> # a particular board config
    uv run python tools/setup_sweep.py --csv out.csv

Every margin in ``models/`` was fitted at ``config.py``'s defaults, and the map
people play was arrived at separately, by playing it. Neither reference point
knows about the other, so the gap between them is deliberate on both sides and
its size is a measurement rather than a bug. This measures it.

Three arms, all derived from a real posted setup (``tools/config_census.py``
finds it, off the public tables, no credentials), so there are no invented
numbers here to go stale:

* **defaults** — that setup's mode and seat count, everything else at ``config``'s
  defaults. The regime the roster was fitted in.
* **map** — plus its node count, and nothing else. Map size on its own.
* **played** — plus every balance knob it carries. The board as played.

Each arm runs every bot through the human's seat against the opponents that
setup names, via ``sim.play_settings`` — the same funnel as the leaderboard's bot
column, so a row here and a row there mean the same thing. Arms are *not* paired:
a changed node count or edge fraction generates a different map from the same
seed, so the seeds are a common sample rather than matched pairs, and the z below
is the two-proportion kind for independent groups.

Read the z column with the arm count in mind: six bots across three arm pairs is
eighteen comparisons, so roughly one |z| ≥ 1.96 is expected from chance alone.
A single starred row is not a finding; a bot that moves in the same direction
across both halves of the split is worth a closer look.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from starconquest.settings import Settings  # noqa: E402
from tests import sim  # noqa: E402 — the shared headless harness
from tools import config_census  # noqa: E402

# Lifted the same 100x and for the same reason as tools/bot_replay.py: a bot's
# wall-clock guard is sized for the browser, and tripping one is the only thing
# that makes its output depend on the clock.
BUDGET_SCALE = 100.0

# The knob fields, from the pairing settings.py already keeps. Private, but the
# alternative is a second copy of the list that rots the next time one is added.
KNOBS = [field for field, _const in settings_mod._GLOBAL_KNOBS]

SIGNIFICANT = 1.96


def _identity(cfg: Settings) -> str:
    """A setup's identity with the seed taken out, so one config reseeded is one
    setup rather than several. The same grouping the board does in SQL
    (``sc_config_key``), recomputed here because these rows are already local."""
    data = {k: v for k, v in cfg.to_dict().items()
            if k not in ("seed", "challenge", "autoplay")}
    return json.dumps(data, sort_keys=True, default=str)


def configs(rows: list[dict]) -> list[dict]:
    """Board rows folded to one entry per setup, most-played first.

    Ranked on distinct games ahead of scores: a config played across six seeds is
    a setup somebody keeps choosing, while one map carrying eleven scores is
    eleven attempts at a single board — and reseeding a saved config is exactly
    how a setup other than the default gets kept at all.
    """
    groups: dict[str, dict] = {}
    for row in rows:
        entry = groups.setdefault(_identity(row["config"]),
                                  {"games": 0, "scores": 0, "rows": []})
        entry["games"] += 1
        entry["scores"] += row["scores"]
        entry["rows"].append(row)
    folded = [{**entry, "row": max(entry["rows"], key=lambda r: r["scores"])}
              for entry in groups.values()]
    folded.sort(key=lambda entry: (-entry["games"], -entry["scores"]))
    return folded


def most_played(rows: list[dict], config_key: str = "") -> dict:
    """The setup to build the arms from, and what else was in the running."""
    if config_key:
        for row in rows:
            if row["game_key"] == config_key:
                return {"games": 1, "scores": row["scores"], "row": row}
        raise SystemExit(f"No game on the board has the key {config_key!r}.")
    folded = configs(rows)
    for entry in folded[:3]:
        cfg = entry["row"]["config"]
        print(f"  candidate: {cfg.players}p {cfg.nodes}n {cfg.mode:<9} "
              f"{entry['games']:>2} games {entry['scores']:>3} scores")
    return folded[0]


def arms(played: Settings, opponents: list[str]) -> dict[str, Settings]:
    """The three setups, each a strict superset of the last."""
    def build(nodes: int, with_knobs: bool) -> Settings:
        cfg = Settings(mode=played.mode, players=played.players)
        cfg.nodes = max(cfg.min_nodes(), nodes)
        if with_knobs:
            for knob in KNOBS:
                setattr(cfg, knob, getattr(played, knob))
        cfg.ai_strategy = list(cfg.ai_strategy)
        for seat, bot in enumerate(opponents, start=2):     # seat 1 is the bot on trial
            if seat - 1 < len(cfg.ai_strategy):
                cfg.ai_strategy[seat - 1] = bot
        return cfg

    setups = {
        "defaults": build(Settings().nodes, False),
        "map": build(played.nodes, False),
        "played": build(played.nodes, True),
    }
    # A setup at every default makes "played" a copy of "map": two identical arms
    # is a third of the runtime spent proving 0.0, so say so and drop it.
    if all(getattr(played, knob) == getattr(Settings(), knob) for knob in KNOBS):
        print("  this setup carries no non-default knob; dropping the 'played' arm")
        del setups["played"]
    return setups


def run(setups: dict[str, Settings], bots: list[str], seeds: list[int],
        max_turns: int) -> list[dict]:
    started = time.time()
    rows: list[dict] = []
    for arm, cfg in setups.items():
        for bot in bots:
            for seed in seeds:
                result = sim.play_settings(cfg, seed, bot, max_turns=max_turns)
                rows.append({"arm": arm, "bot": bot, "seed": seed, "won": result.won,
                             "turns": result.turns, "lost": result.lost,
                             "timed_out": result.timed_out})
            print(f"  {arm:<9} {bot:<12} {len(rows):>5} games  "
                  f"{time.time() - started:6.1f}s", flush=True)
    return rows


def _cell(rows: list[dict], arm: str, bot: str) -> list[dict]:
    return [row for row in rows if row["arm"] == arm and row["bot"] == bot]


def two_proportion_z(wins_a: int, n_a: int, wins_b: int, n_b: int) -> float:
    """Independent-groups z for one bot's win rate between two arms."""
    if not n_a or not n_b:
        return 0.0
    pooled = (wins_a + wins_b) / (n_a + n_b)
    se = (pooled * (1 - pooled) * (1 / n_a + 1 / n_b)) ** 0.5
    return ((wins_a / n_a) - (wins_b / n_b)) / se if se else 0.0


def report(rows: list[dict], setups: dict[str, Settings], bots: list[str]) -> None:
    for arm in setups:
        print(f"\n=== {arm} ===")
        table = []
        for bot in bots:
            cell = _cell(rows, arm, bot)
            wins = [row for row in cell if row["won"]]
            # Never rank a lost game against a won one on turns: unwon cells sort
            # last on win rate and print no mean at all.
            table.append((len(wins) / len(cell) if cell else 0.0,
                          statistics.mean([w["turns"] for w in wins]) if wins else 0.0,
                          bot, len(wins), len(cell)))
        table.sort(key=lambda entry: (-entry[0], entry[1] or float("inf")))
        for rank, (rate, turns, bot, wins, total) in enumerate(table, 1):
            pace = f"{turns:5.1f}" if wins else "    -"
            print(f"  {rank}. {bot:<12} win {rate * 100:5.1f}% ({wins}/{total})  turns {pace}")

    names = list(setups)
    pairs = [(names[0], names[-1]), *zip(names, names[1:])]
    starred = 0
    for before, after in pairs:
        print(f"\n=== {before} -> {after}: change in win rate ===")
        for bot in bots:
            cell_b, cell_a = _cell(rows, before, bot), _cell(rows, after, bot)
            wins_b = sum(row["won"] for row in cell_b)
            wins_a = sum(row["won"] for row in cell_a)
            z = two_proportion_z(wins_a, len(cell_a), wins_b, len(cell_b))
            star = "  *" if abs(z) >= SIGNIFICANT else ""
            starred += bool(star)
            print(f"  {bot:<12} {wins_b / len(cell_b) * 100:5.1f}% -> "
                  f"{wins_a / len(cell_a) * 100:5.1f}%   z = {z:+6.2f}{star}")

    tests = len(pairs) * len(bots)
    print(f"\n{starred} of {tests} comparisons at |z| >= {SIGNIFICANT}; "
          f"{tests * 0.05:.1f} expected from chance alone.")


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=150, help="games per bot per arm")
    parser.add_argument("--start", type=int, default=1000, help="first seed")
    parser.add_argument("--max-turns", type=int, default=600)
    parser.add_argument("--bots", nargs="*", default=None,
                        help="roster to rank (default: every registered strategy)")
    parser.add_argument("--opponents", nargs="*", default=None,
                        help="override the seats the setup names")
    parser.add_argument("--config-key", default="",
                        help="a particular game_key instead of the most-played setup")
    parser.add_argument("--csv", type=Path, default=None, help="write every game here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ai.load_models()
    ai.set_budget_scale(BUDGET_SCALE)

    url, key = config_census.anon_credentials()
    games, scores = config_census.fetch(url, key)
    if not games:
        print("The board has no games to take a setup from.", file=sys.stderr)
        return 1
    entry = most_played(config_census.census(games, scores, with_maps=False),
                        args.config_key)
    chosen = entry["row"]
    played = chosen["config"]
    opponents = args.opponents if args.opponents is not None else list(chosen["bots"])

    bots = args.bots or ai.available_strategies()
    seeds = list(range(args.start, args.start + args.seeds))
    setups = arms(played, opponents)

    knobs = [k for k in KNOBS if getattr(played, k) != getattr(Settings(), k)]
    print(f"\nSetup {chosen['game_key']} ({entry['games']} games, {entry['scores']} "
          f"scores): {played.mode}, {played.players} players, {played.nodes} nodes "
          f"vs {', '.join(opponents)}")
    if knobs:
        print("  knobs: " + ", ".join(f"{k}={getattr(played, k):g}" for k in knobs))
    print(f"{len(setups)} arms x {len(bots)} bots x {len(seeds)} seeds = "
          f"{len(setups) * len(bots) * len(seeds)} games\n")

    rows = run(setups, bots, seeds, args.max_turns)
    report(rows, setups, bots)
    if args.csv:
        write_csv(rows, args.csv)
        print(f"Wrote {args.csv} ({len(rows)} rows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
