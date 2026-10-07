#!/usr/bin/env python3
"""Search for the best score a player could reach on one setup.

    uv run python tools/par_search.py --nodes 13 --players 3 --ai marshal actuary
    uv run python tools/par_search.py --log games/game_x.json   # a played match
    uv run python tools/par_search.py --game-key 06a74fc834bdf656  # a live board map
    uv run python tools/par_search.py --seed 42 --dice honest --width 4 --budget 300
    uv run python tools/par_search.py ... --out best.json       # keep the winning line

Plays the human seat as a person would, knowing exactly what every bot will do,
and reports three numbers for the setup:

* the **floor**: no line can win before this turn (`floor`), whatever the dice;
* the **best line found** by a beam search over turns, scored by rollouts;
* with ``--log``, the turn the recorded player actually won on; with
  ``--game-key``, the board's best counted score and best bot-column win.

``--dice lucky`` resolves every fight the searcher is in at its best roll, for a
player who rewinds until a fight goes their way. It is not a ceiling: rival
fights still roll, and fall differently. ``--dice honest`` draws the dice, and
also tries each turn as if the player had just rewound to it (`_RESET`), which
is the re-roll the game really offers. What each mode measures and what the
runs found are in docs/design/par.md.

The winning line is a real `replay.GameLog`: it is replayed through
`replay.reconstruct` before it is reported, and ``--out`` saves it.
"""

from __future__ import annotations

import argparse
import copy
import heapq
import math
import random
import sys
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, combat, config, engine, replay  # noqa: E402
from starconquest.model import GameState, Order  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

CANDIDATES = ("marshal", "actuary", "thinker", "heuristic", "rusherplus", "claudebot")
ROLLOUT_POLICY = "marshal"
DROP_ONE_CAP = 3          # drop-one-strike variants per node, from the first candidate
MAX_TURNS = 400

_CONTINUE = "continue"
_RESET = "reset"
_SALT_PROBE = 101


# --------------------------------------------------------------------------- #
# Boards
# --------------------------------------------------------------------------- #
def clone(state: GameState, rng: random.Random | None = None) -> GameState:
    """A private copy of ``state`` with its own rng (a copy of ``state.rng`` by
    default), cheap enough to make one per search node."""
    out = copy.copy(state)
    out.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in out.systems.values():
        s.neighbors = list(s.neighbors)
    out.fleets = [copy.copy(f) for f in state.fleets]
    out.players = {pid: copy.copy(p) for pid, p in state.players.items()}
    for p in out.players.values():
        p.ai_params = copy.copy(p.ai_params)
    out.adjacency = {sid: dict(nbrs) for sid, nbrs in state.adjacency.items()}
    if rng is None:
        rng = random.Random()
        rng.setstate(state.rng.getstate())
    out.rng = rng
    return out


def board_key(state: GameState) -> tuple:
    """Everything about a position the rules read, the rng aside."""
    return (
        state.turn,
        tuple((s.owner_id, s.ships, s.prod_progress) for _, s in sorted(state.systems.items())),
        tuple(sorted((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                     for f in state.fleets)),
    )


def rivals_of(state: GameState, me: int) -> set[int]:
    return {p.id for p in state.non_neutral_players() if p.id != me and p.alive}


# --------------------------------------------------------------------------- #
# The floor
# --------------------------------------------------------------------------- #
def floor(state: GameState, me: int, horizon: int = MAX_TURNS) -> float:
    """The earliest turn ``me`` could possibly have won by, from ``state``.

    Every rival-held system has to be reached and every rival fleet has to land,
    so the win is no sooner than the latest of: the shortest travel from ``me``'s
    systems and fleets to each rival system, and each rival fleet's arrival.
    Lanes are timed at ``horizon`` so ship-speed growth can only make the real
    trip longer. ``inf`` once ``me`` has nothing left.
    """
    rivals = rivals_of(state, me)
    if not rivals:
        return float(state.turn)
    dist: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    for s in state.systems.values():
        if s.owner_id == me:
            heap.append((0, s.id))
    for f in state.fleets:
        if f.owner_id == me:
            heap.append((max(f.turns_remaining, 0), f.dest_id))
    if not heap:
        return math.inf
    heapq.heapify(heap)
    while heap:
        d, sid = heapq.heappop(heap)
        if sid in dist:
            continue
        dist[sid] = d
        for nbr in state.systems[sid].neighbors:
            if nbr not in dist:
                lane = state.lanes[frozenset((sid, nbr))]
                heapq.heappush(heap, (d + config.travel_turns_at_length(lane.length_ly, horizon), nbr))
    h = 0.0
    for s in state.systems.values():
        if s.owner_id in rivals:
            h = max(h, dist.get(s.id, math.inf))
    for f in state.fleets:
        if f.owner_id in rivals:
            h = max(h, f.turns_remaining)
    return state.turn + h


# --------------------------------------------------------------------------- #
# Lucky dice
# --------------------------------------------------------------------------- #
class _Fixed:
    """Deals ``rolls`` in order, appending each to ``dice.drawn`` when ``dice`` is
    the engine's recorder, so the turn's record carries what was dealt."""

    def __init__(self, dice, rolls: list[float]) -> None:
        self._dice = dice
        self._rolls = list(rolls)

    def uniform(self, a: float, b: float) -> float:
        value = self._rolls.pop(0)
        drawn = getattr(self._dice, "drawn", None)
        if drawn is not None:
            drawn.append(value)
        return value


@contextmanager
def lucky(me: int) -> Iterator[None]:
    """Every fight ``me`` is in rolls ``+jitter`` for ``me`` and ``-jitter`` for the
    other side; every other fight rolls as usual. Process-local, for the search
    only: a replay must deal its recorded dice, so never hold this round
    `replay.reconstruct`."""
    real = combat.resolve_fight

    def fight(rng, a_owner, a_ships, b_owner, b_ships, defender_owner=None):
        if me not in (a_owner, b_owner):
            return real(rng, a_owner, a_ships, b_owner, b_ships, defender_owner)
        j = config.COMBAT_JITTER
        rolls = [j if a_owner == me else -j, j if b_owner == me else -j]
        return real(_Fixed(rng, rolls), a_owner, a_ships, b_owner, b_ships, defender_owner)

    combat.resolve_fight = fight
    try:
        yield
    finally:
        combat.resolve_fight = real


# --------------------------------------------------------------------------- #
# Candidate orders
# --------------------------------------------------------------------------- #
def _probe_rng(state: GameState, me: int, salt: int) -> random.Random:
    return random.Random((state.seed * 1000003 + state.turn * 9176 + me * 31 + salt) & 0x7FFFFFFF)


def borrowed(state: GameState, me: int, name: str, salt: int = 0) -> list[Order] | None:
    """``name``'s orders for ``me``'s seat, decided on a private copy with an rng of
    its own, so asking never moves the real board or its dice."""
    fn = ai.STRATEGIES.get(name)
    if fn is None:
        return None
    probe = clone(state, _probe_rng(state, me, _SALT_PROBE + salt))
    probe.players[me].ai_params.aux = config.AI_AUX
    try:
        orders = fn(probe, me) or []
    except Exception:  # noqa: BLE001 — a broken bot just proposes nothing
        return None
    return [o for o in orders if getattr(o, "owner_id", None) == me]


def _normalised(orders: list[Order]) -> tuple:
    merged: dict[tuple[int, int], int] = {}
    for o in orders:
        if o.ships > 0:
            merged[(o.source_id, o.dest_id)] = merged.get((o.source_id, o.dest_id), 0) + o.ships
    return tuple(sorted((s, d, n) for (s, d), n in merged.items()))


def _all_in(state: GameState, orders: list[Order]) -> list[Order]:
    """``orders`` with every single-order source sending its whole garrison."""
    per_source: dict[int, int] = {}
    for o in orders:
        per_source[o.source_id] = per_source.get(o.source_id, 0) + 1
    return [Order(o.owner_id, o.source_id, o.dest_id,
                  state.systems[o.source_id].ships if per_source[o.source_id] == 1 else o.ships)
            for o in orders]


def candidates(state: GameState, me: int, names: tuple[str, ...] = CANDIDATES) -> list[list[Order]]:
    """Distinct order sets worth trying for ``me`` this turn: each borrowed bot's,
    each sent all-in, holding, and the first bot's with one strike dropped."""
    found: list[list[Order]] = []
    seen: set[tuple] = set()

    def add(orders: list[Order]) -> None:
        key = _normalised(orders)
        if key not in seen:
            seen.add(key)
            found.append(orders)

    first: list[Order] | None = None
    for i, name in enumerate(names):
        orders = borrowed(state, me, name, i)
        if orders is None:
            continue
        first = orders if first is None else first
        add(orders)
        add(_all_in(state, orders))
    add([])
    if first:
        strikes = [o for o in first if state.systems[o.dest_id].owner_id != me]
        for o in strikes[:DROP_ONE_CAP]:
            add([x for x in first if x is not o])
    return found


# --------------------------------------------------------------------------- #
# The search
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    board: GameState
    parent: Node | None = None
    record: engine.TurnRecord | None = None

    def records(self) -> list[engine.TurnRecord]:
        out: list[engine.TurnRecord] = []
        node: Node | None = self
        while node is not None and node.record is not None:
            out.append(node.record)
            node = node.parent
        return out[::-1]


@dataclass
class Best:
    turn: float = math.inf
    lost: float = math.inf
    records: list[engine.TurnRecord] = field(default_factory=list)
    source: str = ""

    def beats(self, turn: float, lost: float) -> bool:
        return (turn, lost) < (self.turn, self.lost)

    def cannot_beat(self, floor_turn: float, lost: float) -> bool:
        return floor_turn > self.turn or (floor_turn == self.turn and lost >= self.lost)


@dataclass
class Stats:
    expanded: int = 0
    children: int = 0
    pruned: int = 0
    merged: int = 0
    rollouts: int = 0
    rollout_turns: int = 0
    layers: int = 0


def step(board: GameState, orders: list[Order]) -> engine.TurnRecord:
    """One turn on ``board`` with ``orders`` for the human seat and every bot seat
    deciding for itself."""
    return engine.end_turn(board, human_orders=orders, decide=ai.decide)


class Search:
    def __init__(self, cfg: Settings, seed: int, dice: str = "lucky", width: int = 6,
                 names: tuple[str, ...] = CANDIDATES, policy: str = ROLLOUT_POLICY,
                 budget: float = 600.0, max_turns: int = MAX_TURNS,
                 resets: bool | None = None, verbose: bool = False,
                 strategies: dict[int, str] | None = None) -> None:
        self.cfg, self.seed = cfg, seed
        self.root = build_state(cfg, seed)
        for pid, name in (strategies or {}).items():
            if pid in self.root.players and not self.root.players[pid].is_neutral:
                self.root.players[pid].ai_strategy = name
        self.strategies = replay.seat_strategies(self.root)
        human = self.root.human()
        if human is None:
            raise ValueError("this setup has no human seat to search for")
        self.me = human.id
        self.reset_state = self.root.rng.getstate()
        self.dice, self.width, self.names, self.policy = dice, width, names, policy
        self.budget, self.max_turns, self.verbose = budget, max_turns, verbose
        self.resets = (dice == "honest") if resets is None else resets
        self.best = Best()
        self.stats = Stats()
        self.started = 0.0
        self.alone: dict[str, tuple[bool, int, int]] = {}

    def out_of_time(self) -> bool:
        return time.perf_counter() - self.started > self.budget

    def horizon(self) -> int:
        return int(min(self.best.turn, self.max_turns))

    def lost(self, board: GameState) -> int:
        return board.players[self.me].ships_lost

    def _consider(self, board: GameState, records: list[engine.TurnRecord], source: str) -> None:
        if board.winner == self.me and self.best.beats(board.turn, self.lost(board)):
            self.best = Best(board.turn, self.lost(board), list(records), source)
            if self.verbose:
                print(f"  best -> turn {board.turn}, lost {self.lost(board)} ({source})")

    def rollout(self, board: GameState, policy: str | None = None,
                cutoff: bool = True) -> tuple[GameState, list[engine.TurnRecord]]:
        """Play ``board`` on with ``policy`` in the human seat until it is decided,
        hits ``max_turns``, or (with ``cutoff``) can no longer beat the best."""
        policy = policy or self.policy
        board = clone(board)
        records: list[engine.TurnRecord] = []
        self.stats.rollouts += 1
        while board.winner is None and board.turn < self.max_turns:
            if cutoff and self.best.cannot_beat(floor(board, self.me, self.horizon()), self.lost(board)):
                break
            records.append(step(board, borrowed(board, self.me, policy) or []))
            self.stats.rollout_turns += 1
        return board, records

    def expand(self, node: Node) -> Iterator[Node]:
        self.stats.expanded += 1
        variants = [_CONTINUE] + ([_RESET] if self.resets else [])
        for orders in candidates(node.board, self.me, self.names):
            for variant in variants:
                rng = None
                if variant == _RESET:
                    rng = random.Random()
                    rng.setstate(self.reset_state)
                board = clone(node.board, rng)
                record = step(board, orders)
                yield Node(board, node, record)

    def run(self) -> Best:
        self.started = time.perf_counter()
        with self._dice():
            root = Node(clone(self.root))
            for name in self.names:
                if name not in ai.STRATEGIES:
                    continue
                end, records = self.rollout(root.board, name, cutoff=False)
                self.alone[name] = (end.winner == self.me, end.turn, self.lost(end))
                self._consider(end, records, f"{name} alone")
            layer = [root]
            seen: dict[tuple, int] = {}
            while layer and not self.out_of_time():
                self.stats.layers += 1
                scored: list[tuple[tuple, int, Node]] = []
                for node in layer:
                    if self.out_of_time():
                        break
                    for child in self.expand(node):
                        self.stats.children += 1
                        board = child.board
                        if board.winner is not None:
                            self._consider(board, child.records(), "search")
                            continue
                        if board.turn >= self.max_turns:
                            continue
                        lost = self.lost(board)
                        bound = floor(board, self.me, self.horizon())
                        if bound == math.inf or self.best.cannot_beat(bound, lost):
                            self.stats.pruned += 1
                            continue
                        key = board_key(board)
                        if seen.get(key, math.inf) <= lost:
                            self.stats.merged += 1
                            continue
                        seen[key] = lost
                        end, records = self.rollout(board)
                        self._consider(end, child.records() + records, "search+rollout")
                        won = end.winner == self.me
                        rank = (0, end.turn, self.lost(end)) if won else (1, -_evaluate(end, self.me), lost)
                        scored.append((rank, len(scored), child))
                scored.sort(key=lambda item: item[:2])
                layer = [child for _, _, child in scored[: self.width]]
                if self.verbose:
                    turn = layer[0].board.turn if layer else "-"
                    print(f"layer {self.stats.layers} (turn {turn}): {len(scored)} scored, "
                          f"best {self.best.turn}/{self.best.lost}, "
                          f"{time.perf_counter() - self.started:.0f}s")
        return self.best

    @contextmanager
    def _dice(self) -> Iterator[None]:
        if self.dice == "lucky":
            with lucky(self.me):
                yield
        else:
            yield

    def log(self) -> replay.GameLog:
        out = replay.GameLog(seed=self.seed, settings=self.cfg.to_dict(),
                             strategies=dict(self.strategies))
        for record in self.best.records:
            out.record_turn(record, human_ai=False)
        out.mark_finished(self.me)
        return out


def _evaluate(state: GameState, me: int) -> float:
    """Production, systems and ships for ``me`` against the strongest rival."""
    def score(pid: int) -> float:
        systems = ships = 0
        prod = 0.0
        for s in state.systems.values():
            if s.owner_id == pid:
                systems += 1
                ships += s.ships
                prod += 1.0 / s.production if s.production > 0 else 0.0
        ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
        return 20.0 * prod + 2.0 * systems + 0.5 * ships

    rivals = [score(pid) for pid in rivals_of(state, me)]
    return score(me) - (max(rivals) if rivals else 0.0)


def check_line(log: replay.GameLog, me: int) -> tuple[bool, int, int]:
    """Replay ``log`` with nothing re-decided: (``me`` won, turn, ships lost)."""
    state, _ = replay.reconstruct(log)
    return state.winner == me, state.turn, state.players[me].ships_lost


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def board_setup(game_key: str) -> tuple[Settings, int, tuple[int, int] | None, str | None]:
    """A live board map's setup and seed, its best counted (turn, lost), and its
    best bot-column win as text. Public tables only, read with the board's own
    publishable key, so no credentials are needed."""
    from tools.bot_replay import Supabase, _settings_for
    from tools.config_census import anon_credentials

    api = Supabase(*anon_credentials())
    key = urllib.parse.quote(game_key, safe="")
    rows = api.select("games", f"select=game_key,seed,settings_json&game_key=eq.{key}")
    built = _settings_for(rows[0]) if rows else None
    if built is None:
        raise SystemExit(f"no replayable board map with key {game_key}")
    scores = api.select("counted_scores", f"select=turns,lost&game_key=eq.{key}"
                                          "&order=turns.asc,lost.asc&limit=1")
    bots = api.select("bot_scores", f"select=bot,turns,lost&game_key=eq.{key}&won=is.true"
                                    "&order=turns.asc,lost.asc&limit=1")
    best = (scores[0]["turns"], scores[0]["lost"]) if scores else None
    bot = f"{bots[0]['bot']} turn {bots[0]['turns']}, lost {bots[0]['lost']}" if bots else None
    return built[0], built[1], best, bot


def _setup(args: argparse.Namespace) -> tuple[Settings, int, tuple[int, int] | None, dict[int, str]]:
    """The setup to search, its seed, the recorded player's (turn, lost) when
    ``--log`` names a match they won or ``--game-key`` a map with a counted
    score, and the bots a ``--log`` match was dealt."""
    if args.game_key:
        cfg, seed, best, bot = board_setup(args.game_key)
        print(f"board: best bot column {bot or 'no win'}")
        return cfg, seed, best, {}
    if args.log:
        log = replay.load(Path(args.log))
        if not log.is_current:
            raise SystemExit(f"{args.log} was recorded under older rules")
        cfg = Settings.from_dict(log.settings)
        state, _ = replay.reconstruct(log)
        human = state.human()
        played = None
        if human is not None and state.winner == human.id:
            played = (state.turn, human.ships_lost)
        return cfg, log.seed, played, dict(log.strategies)
    cfg = Settings(mode=args.mode, players=args.players, nodes=args.nodes, seed=args.seed)
    for i, name in enumerate(args.ai or []):
        cfg.ai_strategy[i + 1] = name
    return cfg, args.seed, None, {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", help="search the setup of this saved match")
    ap.add_argument("--game-key", help="search this live leaderboard map")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--nodes", type=int, default=13)
    ap.add_argument("--players", type=int, default=3)
    ap.add_argument("--mode", default="random")
    ap.add_argument("--ai", nargs="*", help="strategies for seats 2.. (default heuristic)")
    ap.add_argument("--dice", choices=("lucky", "honest"), default="lucky")
    ap.add_argument("--resets", action=argparse.BooleanOptionalAction, default=None,
                    help="also try each turn as just rewound to (default: on for honest)")
    ap.add_argument("--width", type=int, default=6, help="beam width")
    ap.add_argument("--candidates", nargs="*", default=list(CANDIDATES))
    ap.add_argument("--policy", default=ROLLOUT_POLICY, help="rollout bot for the human seat")
    ap.add_argument("--budget", type=float, default=600.0, help="seconds")
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--out", help="save the best line as a game log here")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ai.load_models()
    ai.set_budget_scale(math.inf)
    cfg, seed, played, strategies = _setup(args)
    search = Search(cfg, seed, dice=args.dice, width=args.width,
                    names=tuple(args.candidates), policy=args.policy, budget=args.budget,
                    max_turns=args.max_turns, resets=args.resets, verbose=args.verbose,
                    strategies=strategies)
    rivals = [search.root.players[p].ai_strategy for p in sorted(rivals_of(search.root, search.me))]
    root_floor = floor(search.root, search.me)
    print(f"setup: {cfg.mode}, {len(search.root.systems)} systems, {cfg.players} players, "
          f"seed {seed}, rivals {' '.join(rivals)}, dice {args.dice}")
    print(f"floor: turn {root_floor:.0f}")
    best = search.run()
    for name, (won, turn, lost) in search.alone.items():
        print(f"  {name:<11} alone: " + (f"won turn {turn}, lost {lost}" if won else f"no win (turn {turn})"))
    s = search.stats
    print(f"searched {s.layers} turns deep: {s.expanded} expanded, {s.children} children, "
          f"{s.pruned} pruned by the floor, {s.merged} merged, "
          f"{s.rollouts} rollouts ({s.rollout_turns} turns), "
          f"{time.perf_counter() - search.started:.0f}s")
    if played is not None:
        label = "board best" if args.game_key else "recorded player"
        print(f"{label}: turn {played[0]}, lost {played[1]}")
    if best.turn == math.inf:
        print("best: no winning line found")
        return 1
    log = search.log()
    won, turn, lost = check_line(log, search.me)
    status = "replays" if (won, turn, lost) == (True, best.turn, best.lost) else \
        f"DOES NOT REPLAY (won={won}, turn {turn}, lost {lost})"
    print(f"best: turn {best.turn}, lost {best.lost} ({best.source}); "
          f"gap to floor {best.turn - root_floor:.0f}; {status}")
    if args.out:
        log.path = Path(args.out)
        log.save()
        print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
