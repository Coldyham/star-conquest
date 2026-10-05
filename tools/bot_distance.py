#!/usr/bin/env python3
"""How differently do two bots play the same position?

    uv run python tools/bot_distance.py                        # self-play corpus, 18 nodes
    uv run python tools/bot_distance.py --nodes 40 --players 2 --speed 3
    uv run python tools/bot_distance.py --bots actuary marshal knower
    uv run python tools/bot_distance.py --local                # positions from games/
    uv run python tools/bot_distance.py --supabase             # the shared corpus
    uv run python tools/bot_distance.py --csv out.csv          # every pair on every position

Every bot is handed the same board, seat by seat, and asked for its orders; no
game is played on them, so nothing here is a win rate. What comes back is how far
apart the answers are, measured three ways:

* **distance** — where each of the seat's garrison ships goes this turn (held at
  home, or down a lane), as a share of the seat's whole garrison; half the L1
  gap between two bots' shares. 0 means the same orders to the ship, 1 means no
  ship is sent the same way.
* **kappa** — per owned system, which kind of move its biggest send is (hold,
  to our own system, at a neutral, at a rival), and how often two bots agree,
  corrected for chance (Cohen's kappa). Coarser than distance, but blind to a
  one-ship difference in an otherwise identical turn.
* **fingerprint** — per bot, the share of ships it launches and where they go,
  how often an order empties its source, and what a decide costs.

Positions are split by phase. **contested** means the seat holds a system next to
a live rival's; **opening** means it does not yet. During the opening most bots
play the land-grab and a measure pooled across both phases hides that, while a
bot whose difference is all in its opening never shows on contested ones.

Bots are measured at the leaderboard's profile (`tools.bot_replay.REPLAY_AUX`,
`BUDGET_SCALE`), as `tools/position_suite.py` does, so a wall-clock guard cannot
make a bot's answer depend on the machine. `--null` asks every bot twice on
separate copies; anything above 0 there is how far a bot disagrees with itself.
"""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, mapgen, replay
from starconquest.model import GameState
from tests import sim

CONTESTED = "contested"
OPENING = "opening"
PHASES = (CONTESTED, OPENING)

HOLD, OWN, NEUTRAL, RIVAL = "hold", "own", "neutral", "rival"
ACTIONS = (HOLD, OWN, NEUTRAL, RIVAL)


@dataclass
class Position:
    state: GameState
    pid: int
    phase: str
    source: str          # e.g. "seed 4 t20" or a match id prefix


@dataclass
class Answer:
    """One bot's legal orders on one position, as ships per (source, dest).

    A ship that stays home is booked as (source, source), so holding is a move
    like any other and two bots that both sit tight agree.
    """

    moves: dict[tuple[int, int], int]
    actions: dict[int, str]
    launched: dict[str, int]          # ships sent, by destination kind
    orders: int
    emptied: int                      # orders that took every ship the source had
    ms: float
    failed: bool = False


@dataclass
class Tally:
    distance: dict[tuple[str, str], list[float]] = field(default_factory=lambda: defaultdict(list))
    pairs: dict[tuple[str, str], Counter] = field(default_factory=lambda: defaultdict(Counter))
    answers: dict[str, list[Answer]] = field(default_factory=lambda: defaultdict(list))
    null: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    positions: int = 0


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #
def phase_of(state: GameState, pid: int) -> str:
    for sys_ in state.systems_of(pid):
        for n in sys_.neighbors:
            owner = state.systems[n].owner_id
            if owner not in (0, pid) and state.players[owner].alive:
                return CONTESTED
    return OPENING


def _seats(state: GameState) -> list[int]:
    return [p.id for p in state.non_neutral_players() if p.alive and state.systems_of(p.id)]


def _snapshot(state: GameState, label: str) -> list[Position]:
    board = copy.deepcopy(state)
    return [Position(board, pid, phase_of(board, pid), label) for pid in _seats(board)]


def lineups(roster: list[str], players: int, games: int) -> list[list[str]]:
    """Who plays each self-play game: every `players`-sized group of the roster in
    turn, the seating rotated each time the list wraps, so no bot always sits in
    seat 1."""
    groups = list(itertools.combinations(roster, players))
    out = []
    for i in range(games):
        group = list(groups[i % len(groups)])
        shift = (i // len(groups)) % players
        out.append(group[shift:] + group[:shift])
    return out


def selfplay_positions(roster, seeds, mode, nodes, players, every, max_turns, aux_for):
    """Positions out of games the roster plays among itself. Yielded game by game
    so a caller decides on each before the next game moves `config`."""
    for seed, lineup in zip(seeds, lineups(roster, players, len(seeds))):
        state = mapgen.generate(seed, mode, nodes, players)
        for p in state.players.values():
            p.is_human = False
        sim._assign_strategies(state, lineup, {b: aux_for(b) for b in lineup})
        found: list[Position] = []
        while state.winner is None and state.turn < max_turns:
            if state.turn % every == 0:
                found += _snapshot(state, f"seed {seed} t{state.turn}")
            engine.end_turn(state, decide=ai.decide)
        yield f"seed {seed} {'/'.join(lineup)}", found


def log_positions(logs: list[replay.GameLog], every: int, max_turns: int):
    """Positions out of recorded matches, every live seat at every `every`th turn.
    `reconstruct` deals the recorded orders and dice back, so these are the boards
    that were really played; no seat decides anything to reach them."""
    for log in logs:
        found: list[Position] = []
        label = (log.match_id or "log")[:8]

        def on_turn(state, found=found, label=label):
            if state.winner is None and state.turn < max_turns and state.turn % every == 0:
                found.extend(_snapshot(state, f"{label} t{state.turn}"))

        try:
            replay.reconstruct(log, on_turn=on_turn)
        except Exception as err:  # noqa: BLE001 — one bad log, not a dead run
            print(f"  {label}: skipped ({err})")
            continue
        yield label, found


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #
def _kind(state: GameState, pid: int, dest: int) -> str:
    owner = state.systems[dest].owner_id
    return OWN if owner == pid else NEUTRAL if owner == 0 else RIVAL


def answer(pos: Position, bot: str, aux: float) -> Answer:
    """`bot`'s orders for the seat, on a private copy, kept to what `apply_order`
    would actually launch from the original board."""
    board = copy.deepcopy(pos.state)
    sim._hand_over(board.players[pos.pid], bot, aux)
    started = time.perf_counter()
    try:
        orders = ai.decide(board, pos.pid) or []
        failed = False
    except Exception:  # noqa: BLE001 — a crashing bot holds, as it would in a game
        orders, failed = [], True
    ms = (time.perf_counter() - started) * 1000

    state, pid = pos.state, pos.pid
    left = {s.id: s.ships for s in state.systems_of(pid)}
    moves: dict[tuple[int, int], int] = defaultdict(int)
    launched = Counter()
    count = emptied = 0
    for o in orders:
        if o.owner_id != pid or o.source_id not in left:
            continue
        if state.travel_turns(o.source_id, o.dest_id) is None:
            continue
        ships = min(o.ships, left[o.source_id])
        if ships <= 0:
            continue
        if ships == left[o.source_id] == state.systems[o.source_id].ships:
            emptied += 1
        left[o.source_id] -= ships
        moves[(o.source_id, o.dest_id)] += ships
        launched[_kind(state, pid, o.dest_id)] += ships
        count += 1
    biggest: dict[int, tuple[int, int]] = {}
    for (src, dst), ships in moves.items():
        if ships > biggest.get(src, (0, -1))[0]:
            biggest[src] = (ships, dst)
    for sid, ships in left.items():
        if ships:
            moves[(sid, sid)] += ships
    actions = {sid: (_kind(state, pid, biggest[sid][1]) if sid in biggest else HOLD)
               for sid in left if state.systems[sid].ships > 0}
    return Answer(dict(moves), actions, dict(launched), count, emptied, ms, failed)


def distance(a: Answer, b: Answer) -> float:
    total = sum(a.moves.values())
    if not total:
        return 0.0
    keys = a.moves.keys() | b.moves.keys()
    return 0.5 * sum(abs(a.moves.get(k, 0) - b.moves.get(k, 0)) for k in keys) / total


def kappa(table: Counter) -> float | None:
    """Cohen's kappa over a (row action, column action) -> count table."""
    n = sum(table.values())
    if not n:
        return None
    observed = sum(c for (x, y), c in table.items() if x == y) / n
    rows = Counter()
    cols = Counter()
    for (x, y), c in table.items():
        rows[x] += c
        cols[y] += c
    expected = sum(rows[k] * cols[k] for k in ACTIONS) / (n * n)
    return 1.0 if expected >= 1 else (observed - expected) / (1 - expected)


def measure(positions: list[Position], roster: list[str], aux_for, tallies: dict[str, Tally],
            null: bool, rows: list[dict] | None) -> None:
    for pos in positions:
        if not sum(s.ships for s in pos.state.systems_of(pos.pid)):
            continue
        tally = tallies[pos.phase]
        tally.positions += 1
        got = {bot: answer(pos, bot, aux_for(bot)) for bot in roster}
        for bot, ans in got.items():
            tally.answers[bot].append(ans)
            if null:
                tally.null[bot].append(distance(ans, answer(pos, bot, aux_for(bot))))
        for a, b in itertools.combinations(roster, 2):
            d = distance(got[a], got[b])
            tally.distance[(a, b)].append(d)
            for sid, act in got[a].actions.items():
                tally.pairs[(a, b)][(act, got[b].actions[sid])] += 1
            if rows is not None:
                rows.append({"source": pos.source, "seat": pos.pid, "phase": pos.phase,
                             "a": a, "b": b, "distance": round(d, 4)})


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _matrix(roster, cell, width=9) -> None:
    short = [b[:width - 1] for b in roster]
    print(" " * 12 + "".join(f"{s:>{width}}" for s in short))
    for a in roster:
        line = f"  {a:<10}"
        for b in roster:
            v = None if a == b else cell(a, b)
            line += f"{'—' if v is None else f'{v:.2f}':>{width}}"
        print(line)


def report(tally: Tally, roster: list[str], phase: str, null: bool) -> None:
    if not tally.positions:
        print(f"\n{phase}: no positions")
        return

    def mean_d(a, b):
        vals = tally.distance.get((a, b)) or tally.distance.get((b, a))
        return statistics.fmean(vals) if vals else None

    def kap(a, b):
        if (a, b) in tally.pairs:
            return kappa(tally.pairs[(a, b)])
        flipped = Counter({(y, x): c for (x, y), c in tally.pairs[(b, a)].items()})
        return kappa(flipped)

    print(f"\n=== {phase}: {tally.positions} positions ===")
    print("\ndistance (0 = same orders to the ship, 1 = nothing sent the same way)")
    _matrix(roster, mean_d)
    print("\nkappa on each system's move kind (1 = always agree, 0 = chance)")
    _matrix(roster, kap)

    print("\nnearest neighbour")
    for bot in roster:
        others = [(mean_d(bot, o), o) for o in roster if o != bot]
        d, o = min(others)
        floor = f"   self {statistics.fmean(tally.null[bot]):.3f}" if null else ""
        print(f"  {bot:<12}{o:<12}{d:.2f}{floor}")

    print(f"\n  {'fingerprint':<12}{'sent':>7}{'own':>7}{'neutr':>7}{'rival':>7}"
          f"{'orders':>8}{'empties':>9}{'ms p50':>8}{'ms p99':>8}{'fail':>6}")
    for bot in roster:
        ans = tally.answers[bot]
        garrison = sum(sum(a.moves.values()) for a in ans) or 1
        sent = Counter()
        for a in ans:
            sent.update(a.launched)
        total = sum(sent.values()) or 1
        orders = sum(a.orders for a in ans)
        ms = sorted(a.ms for a in ans)
        p99 = ms[min(len(ms) - 1, int(0.99 * len(ms)))]
        print(f"  {bot:<12}{sum(sent.values()) / garrison:>7.0%}"
              + "".join(f"{sent[k] / total:>7.0%}" for k in (OWN, NEUTRAL, RIVAL))
              + f"{orders / len(ans):>8.1f}{(sum(a.emptied for a in ans) / orders if orders else 0):>9.0%}"
              f"{statistics.median(ms):>8.1f}{p99:>8.1f}{sum(a.failed for a in ans):>6}")
    print("  sent = share of the garrison launched; own/neutr/rival = where launched "
          "ships went;\n  orders = per position; empties = orders that took every ship "
          "the source had")


# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bots", nargs="+", default=None,
                        help="strategies to compare (default: every registered one)")
    parser.add_argument("--play-with", nargs="+", default=None,
                        help="strategies that play the self-play games the positions "
                             "come from (default: --bots)")
    parser.add_argument("--games", type=int, default=21,
                        help="self-play games, or at most this many logs (default 21)")
    parser.add_argument("--seed", type=int, default=1, help="first self-play seed")
    parser.add_argument("--mode", default="random")
    parser.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    parser.add_argument("--players", type=int, default=2)
    parser.add_argument("--speed", type=float, default=None,
                        help="ship speed in ly/turn for self-play maps "
                             f"(default {config.SHIP_LY_PER_TURN:g})")
    parser.add_argument("--every", type=int, default=5,
                        help="sample a position every N turns (default 5)")
    parser.add_argument("--max-turns", type=int, default=300,
                        help="sample no position past this turn (default 300)")
    parser.add_argument("--phase", choices=(*PHASES, "both"), default="both")
    parser.add_argument("--local", action="store_true",
                        help="positions from the local games/ dir instead of self-play")
    parser.add_argument("--dir", type=Path, default=None,
                        help="with --local, read logs from this dir")
    parser.add_argument("--supabase", action="store_true",
                        help="positions from the shared corpus instead of self-play")
    parser.add_argument("--aux", nargs="*", default=[], metavar="BOT=VALUE",
                        help="override a bot's aux (default: tools.bot_replay.REPLAY_AUX)")
    parser.add_argument("--null", action="store_true",
                        help="ask every bot twice, to show how far it disagrees with itself")
    parser.add_argument("--csv", type=Path, default=None,
                        help="write the distance of every pair on every position here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from tools.bot_replay import BUDGET_SCALE, replay_aux

    args = parse_args(argv)
    try:
        overrides = {bot: float(v) for bot, v in (s.split("=", 1) for s in args.aux)}
    except ValueError:
        print(f"--aux wants BOT=VALUE pairs, got: {' '.join(args.aux)}", file=sys.stderr)
        return 2

    ai.load_models()
    roster = args.bots or ai.available_strategies()
    players = args.play_with or roster
    unknown = [b for b in {*roster, *players} if b not in ai.STRATEGIES]
    if unknown:
        print(f"not registered: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if len(roster) < 2:
        print("need at least two bots to compare", file=sys.stderr)
        return 2
    aux_for = lambda bot: replay_aux(bot, overrides)
    ai.set_budget_scale(BUDGET_SCALE)

    if args.local or args.supabase:
        from tools.position_suite import local_logs, supabase_logs
        logs = supabase_logs(args.games) if args.supabase else local_logs(args.dir)[: args.games]
        source = log_positions(logs, args.every, args.max_turns)
        what = f"{len(logs)} recorded games"
    else:
        if args.players > len(players):
            print(f"--players {args.players} needs that many bots to play", file=sys.stderr)
            return 2
        if args.speed is not None:
            config.SHIP_LY_PER_TURN = args.speed
        seeds = list(range(args.seed, args.seed + args.games))
        source = selfplay_positions(players, seeds, args.mode, args.nodes, args.players,
                                    args.every, args.max_turns, aux_for)
        what = (f"{args.games} self-play games, {args.mode} {args.nodes} nodes, "
                f"{args.players} seats, {config.SHIP_LY_PER_TURN:g} ly/turn")
    print(f"{what} · every {args.every} turns · bots: {', '.join(roster)}")
    profile = ", ".join(f"{b}@{aux_for(b):g}" for b in roster if aux_for(b) != 1.0)
    if profile:
        print(f"profile: {profile}")

    tallies = {phase: Tally() for phase in PHASES}
    rows: list[dict] | None = [] if args.csv else None
    started = time.time()
    try:
        for i, (label, found) in enumerate(source, 1):
            if args.phase != "both":
                found = [p for p in found if p.phase == args.phase]
            measure(found, roster, aux_for, tallies, args.null, rows)
            print(f"  [{i}] {label}: {len(found)} positions · {time.time() - started:.0f}s",
                  end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nstopped — reporting on what finished")
    print(" " * 78, end="\r")

    for phase in PHASES:
        if args.phase in (phase, "both"):
            report(tallies[phase], roster, phase, args.null)

    if args.csv and rows:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
