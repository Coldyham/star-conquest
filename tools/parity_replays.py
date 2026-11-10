"""Build the replay-parity corpus the Pyodide build must reproduce exactly.

Two kinds of case, each stored with the board digest after every turn
(`replay.digest_hex`, index 0 is the opening board):

* **live**: a seeded all-bot game played here the way `app.resolve_turn` plays
  one (`replay.reseed`, then `engine.end_turn` with `ai.decide`), every wall-clock
  guard lifted (`ai.set_budget_scale(math.inf)`), so a re-run must decide the
  same orders. Re-playing it checks the bots; reconstructing its log checks the
  engine.
* **stored**: a real log from `games/` at the current `RULES_VERSION`,
  reconstructed (`replay.reconstruct`). It checks the engine on games people
  played.

Each finished case is appended to `results/parity/replays.jsonl` as it lands and
skipped on a rerun. `--write` gathers the ledger into
`tests/fixtures/parity_replays.jsonl.gz`, which `tests/test_parity_replays.py`
checks natively and the Pyodide runner checks in the browser build.

    uv run python tools/parity_replays.py            # play/record what's missing
    uv run python tools/parity_replays.py --write    # ...then write the fixture
    uv run python tools/parity_replays.py --list     # the cases and their state
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay
from starconquest.custommap import CustomMap
from starconquest.model import AiParams
from starconquest.settings import Settings, build_state

LEDGER = ROOT / "results" / "parity" / "replays.jsonl"
FIXTURE = ROOT / "tests" / "fixtures" / "parity_replays.jsonl.gz"
GAMES = ROOT / "games"


@dataclass
class Live:
    name: str
    seed: int
    seats: list[tuple[str, float]]          # (strategy, aux) per seat, seat 1 first
    cap: int = 200                          # turns, so a stalemate can't run on
    knobs: dict = field(default_factory=dict)


LIVE: list[Live] = [
    Live("heuristic-duel", 101, [("heuristic", 0), ("heuristic", 0)], knobs={"nodes": 20}),
    Live("baselines-3p", 102, [("rusherplus", 0), ("heuristic", 0), ("marshal", 0)]),
    Live("marshal-hub-4p", 103, [("marshal", 0)] * 4, knobs={"mode": "symmetric", "layout": "hub"}),
    Live("thinker-stops-ring", 104, [("thinker", 0), ("thinker", 1), ("marshal", 0)],
         knobs={"mode": "symmetric", "layout": "ring"}),
    Live("evolved-stops-wheel", 105, [("evolved", 0), ("evolved", 1), ("heuristic", 0), ("marshal", 0)],
         knobs={"mode": "symmetric", "layout": "wheel"}),
    Live("actuary-stops-4p", 106, [("actuary", 0), ("actuary", 1), ("actuary", 2), ("actuary", 3)], cap=80,
         knobs={"nodes": 28}),
    Live("knower-off-predict", 107, [("knower", 0), ("knower", 1), ("marshal", 0)], cap=50),
    Live("knower-search", 108, [("knower", 2), ("actuary", 0)], cap=40, knobs={"nodes": 20}),
    Live("knower-council", 109, [("knower", 3), ("thinker", 1)], cap=40, knobs={"nodes": 20}),
    Live("lane-battles", 110, [("marshal", 0), ("thinker", 1), ("heuristic", 0)],
         knobs={"in_lane_battles": True}),
    Live("odd-balance", 111, [("marshal", 0), ("actuary", 0), ("rusherplus", 0), ("heuristic", 0)],
         knobs={"ship_speed_growth_pct": 10.0, "neutral_produces": True,
                "defender_advantage": 1.3, "combat_jitter": 0.3}),
    Live("big-board-5p", 112, [("marshal", 0), ("thinker", 1), ("evolved", 0), ("heuristic", 0),
                               ("rusherplus", 0)], cap=60, knobs={"nodes": 40}),
    Live("core-6p", 113, [("marshal", 0), ("heuristic", 0)] * 3, cap=60,
         knobs={"mode": "symmetric", "layout": "core"}),
    Live("custom-map", 114, [("marshal", 0), ("thinker", 1)], knobs={"custom_map": "maps/test.json"}),
]

# Real logs at RULES_VERSION 2, picked for spread: human-played turns, long
# games, every bot family the corpus has (legacy names included), symmetric.
STORED = [
    "game_20260915_124925_145576.json",   # 161 turns, 4p, human
    "game_20260922_140018_453904.json",   # 177 turns, 3p, human
    "game_20261008_131524_356012.json",    # actuary, marshal
    "game_20261008_154427_517715.json",    # learner (legacy name)
    "game_20261009_111025_648286.json",    # actuary, claudebot (legacy)
    "game_20261015_124727_95209.json",     # council (legacy)
    "game_20261019_125008_20587095.json",  # actuary, evolved
    "game_20261026_125102_20231231.json",  # symmetric 4p, knower, marshal, thinker
    "game_20261028_102432_20681768.json",  # 5p, thinker
    "game_20261104_133930_20189371.json",  # rusherplus
]


def live_settings(case: Live) -> Settings:
    s = Settings(players=len(case.seats), seed=case.seed, autoplay=True)
    for key, value in case.knobs.items():
        if key == "custom_map":
            cm = CustomMap.from_dict(json.loads((ROOT / value).read_text()))
            assert cm is not None, value
            s.custom_map = cm
        else:
            setattr(s, key, value)
    for i, (name, aux) in enumerate(case.seats):
        s.ai_strategy[i] = name
        s.ai[i] = AiParams(aux=float(aux))
    return s


def _play(settings: Settings, seed: int, cap: int) -> tuple[replay.GameLog, list[str], int | None]:
    """An all-bot match played as `app.resolve_turn` plays one, digests per turn."""
    state = build_state(settings, seed)
    log = replay.new_log(settings, seed, state)
    digests = [replay.digest_hex(state)]
    for _ in range(cap):
        if state.winner is not None:
            break
        replay.reseed(state)
        record = engine.end_turn(state, decide=ai.decide)
        log.record_turn(record, human_ai=True)
        digests.append(replay.digest_hex(state))
    if state.winner is not None:
        log.mark_finished(state.winner)
    return log, digests, state.winner


def play_live(case: Live) -> dict:
    log, digests, winner = _play(live_settings(case), case.seed, case.cap)
    data = log.to_dict()
    data.pop("path", None)
    return {"name": case.name, "kind": "live", "seed": case.seed, "cap": case.cap,
            "log": data, "digests": digests, "winner": winner}


def replay_live(row: dict) -> list[str]:
    """Play a live case again from nothing but its fixture row; the bots decide afresh.

    Needs `ai.load_models()` and `ai.set_budget_scale(math.inf)` first, as the
    recording run had.
    """
    settings = Settings.from_dict(row["log"]["settings"])
    settings.autoplay = True
    return _play(settings, row["log"]["seed"], len(row["digests"]) - 1)[1]


def load_fixture(path: Path = FIXTURE) -> list[dict]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def first_mismatch(want: list[str], got: list[str]) -> str | None:
    """Where two digest lists part, as a message, or None when they agree."""
    for i, (a, b) in enumerate(zip(want, got)):
        if a != b:
            return f"turn {i}: want {a}, got {b}"
    if len(want) != len(got):
        return f"length: want {len(want)} boards, got {len(got)}"
    return None


def reconstruct_digests(log_data: dict) -> list[str]:
    log = replay.GameLog.from_dict(copy.deepcopy(log_data))
    digests: list[str] = []
    replay.reconstruct(log, on_turn=lambda st: digests.append(replay.digest_hex(st)))
    return digests


def stored_case(filename: str) -> dict:
    data = json.loads((GAMES / filename).read_text())
    assert data.get("rules_version") == engine.RULES_VERSION, filename
    data.pop("path", None)
    return {"name": filename.removesuffix(".json"), "kind": "stored", "log": data,
            "digests": reconstruct_digests(data)}


def done_names() -> set[str]:
    if not LEDGER.exists():
        return set()
    return {json.loads(line)["name"] for line in LEDGER.read_text().splitlines() if line.strip()}


def append(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        fh.flush()


def record_missing() -> None:
    done = done_names()
    todo = [c.name for c in LIVE if c.name not in done] + \
           [f.removesuffix(".json") for f in STORED if f.removesuffix(".json") not in done]
    print(f"{len(done)} recorded, {len(todo)} to go", flush=True)
    for case in LIVE:
        if case.name in done:
            continue
        t0 = time.perf_counter()
        row = play_live(case)
        # The log must rebuild the same boards it was played as, or it is no use
        # as a fixture: check before recording.
        assert reconstruct_digests(row["log"]) == row["digests"], case.name
        append(row)
        print(f"  live   {case.name:24} {len(row['digests']) - 1:4} turns  "
              f"winner {row['winner']}  {time.perf_counter() - t0:6.1f}s", flush=True)
    for filename in STORED:
        name = filename.removesuffix(".json")
        if name in done:
            continue
        t0 = time.perf_counter()
        row = stored_case(filename)
        append(row)
        print(f"  stored {name:40} {len(row['digests']) - 1:4} turns  "
              f"{time.perf_counter() - t0:6.1f}s", flush=True)


def write_fixture() -> None:
    wanted = [c.name for c in LIVE] + [f.removesuffix(".json") for f in STORED]
    rows = {}
    for line in LEDGER.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["name"]] = row
    missing = [n for n in wanted if n not in rows]
    if missing:
        sys.exit(f"not recorded yet: {', '.join(missing)}")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(rows[n], separators=(",", ":"), sort_keys=True) + "\n" for n in wanted)
    with gzip.GzipFile(FIXTURE, "wb", mtime=0) as fh:
        fh.write(body.encode())
    print(f"wrote {FIXTURE.relative_to(ROOT)}: {len(wanted)} cases, "
          f"{FIXTURE.stat().st_size // 1024} KB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="write the test fixture from the ledger")
    ap.add_argument("--list", action="store_true", help="list the cases and whether each is recorded")
    args = ap.parse_args()
    if args.list:
        done = done_names()
        for name in [c.name for c in LIVE] + [f.removesuffix(".json") for f in STORED]:
            print(f"{'done' if name in done else '----'}  {name}")
        return
    ai.load_models()
    ai.set_budget_scale(math.inf)
    record_missing()
    if args.write:
        write_fixture()


if __name__ == "__main__":
    main()
