"""Six strikes council lost to a person's relief, as a probe.

Each position is the board in a person-vs-council duel on the turn council
launched a strike the person then covered. None of council's self-play
measurements faces a seat that relieves like a person, so this is where its
rollout stand-in for a person (`knower._blind`) shows.

`play` rebuilds the board, lets council (or any ``decide``) choose, and plays
any strike at the target forward against the person's recorded orders.

    uv run python -m tests.council_traps
"""

from __future__ import annotations

import json
from pathlib import Path

from starconquest import ai, engine, replay

_LOGS = Path(__file__).resolve().parent / "data" / "council_traps.json"

# (game seed, turn index council launched on, target system)
POSITIONS = (
    ("800967", 14, 6),     # returning fleet
    ("95207", 7, 5),
    ("95210", 19, 0),
    ("95208", 16, 4),      # covered evacuation
    ("95209", 40, 1),
    ("95208", 25, 0),
)

COUNCIL = 2
PERSON = 1


def _logs() -> dict[str, dict]:
    return json.loads(_LOGS.read_text())


def board(raw: dict, index: int):
    log = replay.GameLog.from_dict(json.loads(json.dumps(raw)))
    log.truncate(index)
    return replay.reconstruct(log)[0], replay.GameLog.from_dict(raw)


def play(seed: str, index: int, target: int, decide=None) -> tuple[str, int]:
    """("held" | "took" | "lost", ships sent at ``target``) for one position."""
    decide = decide or ai.decide
    state, log = board(_logs()[seed], index)
    first = decide(state, COUNCIL)
    strike = [o for o in first if o.dest_id == target]
    if not strike:
        return "held", 0
    sent = sum(o.ships for o in strike)
    land = index + max(state.travel_turns(o.source_id, o.dest_id) for o in strike)
    start = state.turn
    for k in range(index, land):
        if state.winner is not None:
            break
        person = [o for o in log.orders_for(k) if o.owner_id == PERSON] if k < log.turn_count else []
        engine.end_turn(state, seat_orders={PERSON: person},
                        decide=lambda s, q: first if s.turn == start else decide(s, q))
    took = state.systems[target].owner_id == COUNCIL
    return ("took" if took else "lost"), sent


def probe(decide=None) -> list[tuple[str, int, int, str, int]]:
    ai.set_budget_scale(1000)
    try:
        return [(seed, index, target, *play(seed, index, target, decide))
                for seed, index, target in POSITIONS]
    finally:
        ai.set_budget_scale(1)


if __name__ == "__main__":
    ai.load_models()
    rows = probe()
    for seed, index, target, outcome, sent in rows:
        print(f"{seed} t{index} -> {target}: {outcome} ({sent} sent)")
    print(f"lost {sum(r[3] == 'lost' for r in rows)} of {len(rows)}")
