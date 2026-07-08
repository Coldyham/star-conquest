"""Headless AI-vs-AI harness — drives the pure core with no window.

Run a single verbose game:
    uv run python -m tests.sim --seed 1 --verbose

Batch many seeds and print a summary (winner spread, average length, timeouts):
    uv run python -m tests.sim --trials 200 --nodes 18 --players 3

Because the whole simulation core imports no pygame, this is also what the
pytest suite calls to assert the game actually terminates and never corrupts
its state.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from starconquest import ai, config, engine, mapgen
from starconquest.model import GameState


@dataclass
class SimResult:
    seed: int
    winner: int | None       # player id, 0 for a draw, None if it timed out
    turns: int
    timed_out: bool


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


def play(
    seed: int,
    mode: str = "random",
    nodes: int = config.DEFAULT_NODES,
    players: int = config.DEFAULT_PLAYERS,
    max_turns: int = 600,
    verbose: bool = False,
) -> SimResult:
    state = mapgen.generate(seed, mode, nodes, players)
    # AI-vs-AI: drive every slot with the AI, including the human's seat.
    for p in state.players.values():
        p.is_human = False
    check_invariants(state)
    if verbose:
        print_state(state)
    while state.winner is None and state.turn < max_turns:
        engine.end_turn(state, decide=ai.compute_orders)
        check_invariants(state)
        if verbose:
            print_state(state)
    return SimResult(seed, state.winner, state.turn, state.winner is None)


def run_trials(seeds, mode, nodes, players, max_turns) -> list[SimResult]:
    return [play(s, mode, nodes, players, max_turns) for s in seeds]


def _summarise(results: list[SimResult]) -> None:
    n = len(results)
    timeouts = sum(r.timed_out for r in results)
    finished = [r for r in results if not r.timed_out]
    wins: dict[int, int] = {}
    for r in finished:
        wins[r.winner] = wins.get(r.winner, 0) + 1
    avg = sum(r.turns for r in finished) / len(finished) if finished else 0.0
    print(f"\n{n} games | finished {len(finished)} | timeouts {timeouts}")
    print(f"avg length (finished): {avg:.1f} turns")
    for pid in sorted(wins):
        label = "draw" if pid == 0 else config.player_name(pid)
        print(f"  {label}: {wins[pid]} wins")


def main() -> None:
    ap = argparse.ArgumentParser(description="Headless AI-vs-AI simulation harness")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--players", type=int, default=config.DEFAULT_PLAYERS)
    ap.add_argument("--max-turns", type=int, default=600)
    ap.add_argument("--trials", type=int, default=1, help="run seeds [seed .. seed+trials)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.trials > 1:
        seeds = range(args.seed, args.seed + args.trials)
        results = run_trials(seeds, args.mode, args.nodes, args.players, args.max_turns)
        _summarise(results)
    else:
        r = play(args.seed, args.mode, args.nodes, args.players, args.max_turns, args.verbose)
        winner = "draw" if r.winner == 0 else (config.player_name(r.winner) if r.winner else "TIMEOUT")
        print(f"\nseed {r.seed}: winner={winner} in {r.turns} turns")


if __name__ == "__main__":
    main()
