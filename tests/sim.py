"""Headless AI-vs-AI harness — drives the pure core with no window.

Run a single verbose game:
    uv run python -m tests.sim --seed 1 --verbose

Batch many seeds and print a summary (winner spread, average length, timeouts):
    uv run python -m tests.sim --trials 200 --nodes 18 --players 3

Pit specific models against each other (one name per seat, in seat order; each is
a ``models/`` file stem or the built-in ``heuristic``). The count of names sets
the player count when ``--players`` is omitted:
    uv run python -m tests.sim --ai rusher heuristic --trials 200

Rank a roster fairly with --swap: the --ai roster fills every seat and is rotated
through all N seatings on each seed, so every bot spends equal time in each start
location; wins are then tallied per strategy:
    uv run python -m tests.sim --ai rusher heuristic --swap --trials 200

Fog the bots with --fog-sight (and optionally --fog-scout): each seat then decides
from its own viewpoint, seeing full detail only within `sight` hops and silhouettes
out to `scout` — the same information a human gets (see fog.fogged_state):
    uv run python -m tests.sim --ai claudebot heuristic --swap --fog-sight 1 --fog-scout 3

Because the whole simulation core imports no pygame, this is also what the
pytest suite calls to assert the game actually terminates and never corrupts
its state.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from starconquest import ai, config, engine, fog, mapgen
from starconquest.model import GameState


@dataclass
class SimResult:
    seed: int
    winner: int | None  # player id, 0 for a draw, None if it timed out
    turns: int
    timed_out: bool


@dataclass
class SwapGame:
    """One --swap game: its result plus the seat->strategy line-up used, so a win
    can be credited to whichever strategy held the winning seat that game."""

    result: SimResult
    assignment: list[str]


def check_invariants(state: GameState) -> None:
    for s in state.systems.values():
        assert s.ships >= 0, f"negative garrison at system {s.id}: {s.ships}"
        assert s.owner_id in state.players, f"unknown owner {s.owner_id}"
        assert s.prod_progress >= 0
    for f in state.fleets:
        assert f.ships > 0, "a fleet with no ships is in transit"
        assert f.owner_id in state.players


def player_stats(state: GameState) -> dict[int, tuple[int, int]]:
    """player id -> (systems owned, total ships incl. in-transit)."""
    stats: dict[int, tuple[int, int]] = {}
    for pid, p in state.players.items():
        if p.is_neutral:
            continue
        systems = sum(1 for s in state.systems.values() if s.owner_id == pid)
        ships = sum(s.ships for s in state.systems.values() if s.owner_id == pid)
        ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
        stats[pid] = (systems, ships)
    return stats


def print_state(state: GameState) -> None:
    parts = []
    for pid, (systems, ships) in sorted(player_stats(state).items()):
        parts.append(f"{config.player_name(pid)}: {systems}sys/{ships}sh")
    neutral = sum(1 for s in state.systems.values() if s.owner_id == 0)
    fleets = len(state.fleets)
    print(f"turn {state.turn:>3} | " + "  ".join(parts) + f"  | neutral {neutral}, fleets {fleets}")


def _assign_strategies(state: GameState, strategies: list[str]) -> None:
    """Stamp non-neutral seats with named strategies, in seat order (1-based).

    Mirrors ``settings.build_state``: the seat with player id ``i`` takes
    ``strategies[i - 1]``; any seat past the list keeps its default
    (``"heuristic"``). Names must already be registered (``ai.load_models`` /
    ``ai.STRATEGIES``) — an unregistered name would have ``decide`` silently fall
    back to the heuristic, so the CLI validates them up front rather than here.
    """
    for player in state.players.values():
        if player.is_neutral:
            continue
        idx = player.id - 1
        if 0 <= idx < len(strategies):
            player.ai_strategy = strategies[idx]


def _rotations(strategies: list[str]) -> list[list[str]]:
    """The N cyclic seat line-ups of a roster — each bot in each seat exactly once.

    ``[A, B, C]`` -> ``[A,B,C], [B,C,A], [C,A,B]``. Rotating the seating by one is
    an exact symmetry of a ``symmetric`` map, so these N line-ups fully cancel seat
    bias there; on ``random`` maps they balance seat occupancy and the seed spread
    blurs the rest. Linear in N, not the N! of every possible permutation.
    """
    n = len(strategies)
    return [[strategies[(i + k) % n] for i in range(n)] for k in range(n)]


def play(
    seed: int,
    mode: str = "random",
    nodes: int = config.DEFAULT_NODES,
    players: int = config.DEFAULT_PLAYERS,
    max_turns: int = 600,
    verbose: bool = False,
    strategies: list[str] | None = None,
    fog_sight: int | None = None,
    fog_scout: int | None = None,
) -> SimResult:
    state = mapgen.generate(seed, mode, nodes, players)
    # AI-vs-AI: drive every slot with the AI, including the human's seat.
    for p in state.players.values():
        p.is_human = False
    if strategies:
        _assign_strategies(state, strategies)
    # Optionally fog the bots: each then decides from its own viewpoint (the same
    # visible/scouted tiers a human gets — see fog.fogged_state). Passing either
    # range turns fog on; scout defaults to sight (no silhouette ring). fog_aware
    # reads config live, so we stamp the ranges here.
    decide = ai.decide
    if fog_sight is not None or fog_scout is not None:
        config.FOG_SIGHT = fog_sight if fog_sight is not None else 0
        config.FOG_SCOUT = fog_scout if fog_scout is not None else config.FOG_SIGHT
        decide = fog.fog_aware(ai.decide)
    check_invariants(state)
    if verbose:
        print_state(state)
    while state.winner is None and state.turn < max_turns:
        engine.end_turn(state, decide=decide)
        check_invariants(state)
        if verbose:
            print_state(state)
    return SimResult(seed, state.winner, state.turn, state.winner is None)


def run_trials(seeds, mode, nodes, players, max_turns, strategies=None,
               fog_sight=None, fog_scout=None) -> list[SimResult]:
    return [play(s, mode, nodes, players, max_turns, strategies=strategies,
                 fog_sight=fog_sight, fog_scout=fog_scout) for s in seeds]


def run_swap(seeds, mode, nodes, strategies, max_turns,
             fog_sight=None, fog_scout=None) -> list[SwapGame]:
    """Play every rotation of the roster on each seed (same map, seats rotated)."""
    n = len(strategies)
    games: list[SwapGame] = []
    for seed in seeds:
        for assignment in _rotations(strategies):
            r = play(seed, mode, nodes, n, max_turns, strategies=assignment,
                     fog_sight=fog_sight, fog_scout=fog_scout)
            games.append(SwapGame(r, assignment))
    return games


def _seat_label(pid: int, strategies: list[str] | None) -> str:
    """A player's display name, annotated with its strategy in a tournament run."""
    name = config.player_name(pid)
    if strategies and 1 <= pid <= len(strategies):
        return f"{name} [{strategies[pid - 1]}]"
    return name


def _summarise(results: list[SimResult], strategies: list[str] | None = None) -> None:
    n = len(results)
    timeouts = sum(r.timed_out for r in results)
    finished = [r for r in results if not r.timed_out]
    wins: dict[int, int] = {}
    for r in finished:
        w = r.winner
        if w is None:            # excluded by the filter above; this narrows the type
            continue
        wins[w] = wins.get(w, 0) + 1
    avg = sum(r.turns for r in finished) / len(finished) if finished else 0.0
    print(f"\n{n} games | finished {len(finished)} | timeouts {timeouts}")
    print(f"avg length (finished): {avg:.1f} turns")
    for pid in sorted(wins):
        label = "draw" if pid == 0 else _seat_label(pid, strategies)
        print(f"  {label}: {wins[pid]} wins")


def _summarise_swap(games: list[SwapGame], roster: list[str], seeds: int) -> None:
    """Rank a --swap run by wins per strategy (each bot played every game)."""
    wins: dict[str, int] = dict.fromkeys(roster, 0)
    draws = timeouts = 0
    for g in games:
        winner = g.result.winner     # None (timeout) / 0 (draw) / winning seat id
        if winner is None:
            timeouts += 1
        elif winner == 0:
            draws += 1
        else:
            wins[g.assignment[winner - 1]] += 1
    total = len(games)
    finished = total - timeouts
    print(f"\n{total} games | {len(roster)} rotations x {seeds} seeds | draws {draws} | timeouts {timeouts}")
    for strat, w in sorted(wins.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100 * w / finished if finished else 0.0
        print(f"  {strat}: {w} wins ({pct:.0f}%)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Headless AI-vs-AI simulation harness")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--players", type=int, default=None, help=f"seat count (defaults to the number of --ai names, else {config.DEFAULT_PLAYERS})")
    ap.add_argument(
        "--ai",
        nargs="+",
        metavar="NAME",
        help="strategy per seat, in order: a models/ file stem or 'heuristic'; sets the player count when --players is omitted",
    )
    ap.add_argument("--swap", action="store_true", help="rotate the --ai roster through every seat (cancels start bias) and rank by total wins per strategy")
    ap.add_argument("--max-turns", type=int, default=600)
    ap.add_argument("--trials", type=int, default=1, help="run seeds [seed .. seed+trials)")
    ap.add_argument("--fog-sight", type=int, default=None, metavar="HOPS",
                    help="fog the bots: each sees full detail only within this many lane hops of its own systems (0 = own systems only). Omit for full information.")
    ap.add_argument("--fog-scout", type=int, default=None, metavar="HOPS",
                    help="silhouette range for fogged bots (position + richness, not owner/ships); defaults to --fog-sight")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.fog_scout is not None and args.fog_sight is None:
        args.fog_sight = 0  # scout without sight: own systems in detail, the rest as silhouettes

    strategies = args.ai
    if strategies:
        ai.load_models()  # register drop-in models/ strategies before we name them
        unknown = [n for n in strategies if n not in ai.STRATEGIES]
        if unknown:
            ap.error(f"unknown strategy: {', '.join(unknown)}. available: {', '.join(ai.available_strategies())}")

    if args.swap:
        if not strategies:
            ap.error("--swap needs --ai to define the roster of bots to rotate")
        if args.players is not None and args.players != len(strategies):
            ap.error(f"--swap fills every seat from --ai; omit --players (it is forced to {len(strategies)})")
        seeds = range(args.seed, args.seed + args.trials)
        games = run_swap(seeds, args.mode, args.nodes, strategies, args.max_turns,
                         fog_sight=args.fog_sight, fog_scout=args.fog_scout)
        _summarise_swap(games, strategies, args.trials)
        return

    if args.players is not None:
        players = args.players
    elif strategies:
        players = len(strategies)
    else:
        players = config.DEFAULT_PLAYERS

    if args.trials > 1:
        seeds = range(args.seed, args.seed + args.trials)
        results = run_trials(seeds, args.mode, args.nodes, players, args.max_turns, strategies,
                             fog_sight=args.fog_sight, fog_scout=args.fog_scout)
        _summarise(results, strategies)
    else:
        r = play(args.seed, args.mode, args.nodes, players, args.max_turns, args.verbose, strategies,
                 fog_sight=args.fog_sight, fog_scout=args.fog_scout)
        if r.winner == 0:
            winner = "draw"
        elif r.winner is None:
            winner = "TIMEOUT"
        else:
            winner = _seat_label(r.winner, strategies)
        print(f"\nseed {r.seed}: winner={winner} in {r.turns} turns")


if __name__ == "__main__":
    main()
