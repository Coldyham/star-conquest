#!/usr/bin/env python3
"""Print a saved best line as orders you can play, with the board to expect at each turn.

    uv run python tools/par_show.py results/par/best/2026-11-01_puzzle.best.json
    uv run python tools/par_show.py LINE.json --no-board         # only the orders
    uv run python tools/par_show.py LINE.json --me 1             # a seat other than the winner

The file is a `replay.GameLog`, as `par_search --out`, `par_exhaustive` and `par_best` write
it (``results/par/best/<name>.best.json``). It is replayed through ``replay.reconstruct``, so
what is printed is what the engine did, not a new search: the map, your orders turn by turn,
the rivals' orders (the line only works while they play the same), and the board at the start
of every turn, so you can see the first turn the real game departs from it.

"Turn N" is the number shown when you give the order; a turn with no orders is End Turn
pressed with nothing sent. Counts are the exact ships to send. Pure of pygame.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, replay
from starconquest.model import GameState
from starconquest.settings import Settings, build_state


def owner_tag(state: GameState, owner_id: int, me: int) -> str:
    return "YOU" if owner_id == me else "RIVAL" if owner_id else "neutral"


def map_lines(state: GameState, me: int) -> list[str]:
    out = [f"{s.name:<16} {owner_tag(state, s.owner_id, me):<8} {s.ships:>3} ships, "
           f"1 ship per {s.production} turn(s)" for s in sorted(state.systems.values(), key=lambda s: s.id)]
    seen: set[frozenset[int]] = set()
    for a, system in sorted(state.systems.items()):
        for b in sorted(system.neighbors):
            key = frozenset((a, b))
            if key not in seen:
                seen.add(key)
                out.append(f"lane  {state.systems[a].name} - {state.systems[b].name}: {state.travel_turns(a, b)} turns")
    return out


def board_line(state: GameState, me: int) -> str:
    """One turn-start line: who holds each system with how many ships, then fleets in flight."""
    held = ", ".join(f"{s.name} {'Y' if s.owner_id == me else 'R' if s.owner_id else 'n'}{s.ships}"
                     for s in sorted(state.systems.values(), key=lambda s: s.id))
    fleets = "; ".join(f"{'Y' if f.owner_id == me else 'R'}{f.ships} {state.systems[f.source_id].name}->"
                       f"{state.systems[f.dest_id].name} ({f.turns_remaining} left)" for f in state.fleets)
    return f"T{state.turn}: {held}" + (f"   fleets: {fleets}" if fleets else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("line", help="a best-line file (a replay log)")
    ap.add_argument("--me", type=int, help="the seat to print as yours (default: the log's winner, else the human)")
    ap.add_argument("--no-board", action="store_true", help="leave out the board at each turn start")
    args = ap.parse_args(argv)

    ai.load_models()
    log = replay.load(Path(args.line))
    if not log.is_current:
        raise SystemExit(f"{args.line} was recorded under older rules and cannot be replayed")
    cfg = Settings.from_dict(log.settings)
    opening = build_state(cfg, log.seed)
    me = args.me if args.me is not None else log.winner
    if me is None:
        human = opening.human()
        if human is None:
            raise SystemExit("this log has no human seat; give --me")
        me = human.id
    names = {sid: s.name for sid, s in opening.systems.items()}

    print(f"{cfg.mode}, {len(opening.systems)} systems, seed {log.seed}; you are seat {me}; seats: "
          + ", ".join(f"{pid} {name}" for pid, name in sorted(log.strategies.items())
                      if pid in opening.players and not opening.players[pid].is_neutral))
    print("\nMAP")
    for line in map_lines(opening, me):
        print(f"  {line}")

    print("\nYOUR ORDERS")
    rivals: list[str] = []
    mine = 0
    for t in range(len(log.turns)):
        for o in log.orders_for(t):
            text = f"turn {t:>2}: send {o.ships:>2} ships  {names[o.source_id]} -> {names[o.dest_id]}"
            if o.owner_id == me:
                print(f"  {text}")
                mine += 1
            else:
                rivals.append(f"turn {t:>2}: {o.ships:>2} ships  {names[o.source_id]} -> {names[o.dest_id]}"
                              f"  (seat {o.owner_id})")
    quiet = [t for t in range(len(log.turns)) if not any(o.owner_id == me for o in log.orders_for(t))]
    print(f"  ({mine} orders; End Turn with nothing sent on turn(s) "
          f"{', '.join(map(str, quiet)) or 'none'})")

    print("\nTHE RIVALS' ORDERS IN THIS LINE (the line works while they do the same)")
    for line in rivals or ["none"]:
        print(f"  {line}")

    boards: list[str] = []
    final, _ = replay.reconstruct(log, on_turn=lambda state: boards.append(board_line(state, me)))
    if not args.no_board:
        print("\nBOARD AT THE START OF EACH TURN (Y = yours, R = rival, n = neutral; ships)")
        for line in boards:
            print(f"  {line}")
    result = "YOU WIN" if final.winner == me else f"winner: {final.winner}"
    print(f"\nresult: {result} on turn {final.turn}, ships lost {final.players[me].ships_lost}")
    return 0 if final.winner == me else 1


if __name__ == "__main__":
    raise SystemExit(main())
