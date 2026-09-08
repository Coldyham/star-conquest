"""The wire format for bots that aren't Python: board in, orders out, as JSON.

Pure core (no pygame, and deliberately no ``subprocess``): this module only
*shapes* the messages. Spawning a bot and talking to it is the transport's job
(``tests/botproc.py``), which keeps the schema testable with no child process
anywhere near it. ``docs/bot-api.md`` is the protocol; ``docs/bot-design.md``
under "Bots that aren't Python" is why each field is here.

Two messages, and the split between them is what a bot can and cannot expect to
change: ``hello`` carries everything fixed for the match (the setup, the derived
combat arithmetic, the graph, each seat's tuning) and ``turn_payload`` carries
only what moves. Nothing here reads the wall clock, and nothing draws from
``state.rng`` — see ``decide_seed``.
"""

from __future__ import annotations

import random
from typing import Any, Iterable, Optional

from . import combat, config, settings as settings_mod
from .model import GameState, Order

# Bumped when a message's meaning changes, not when a field is added: a bot that
# ignores unknown keys keeps working, and one that needs a new field can say so
# by refusing an older protocol. Sent in `hello` and echoed by the manifest.
PROTOCOL = 1


def _lane_order(state: GameState) -> list:
    """Lanes in one canonical order, shared by ``hello`` and ``turn_payload``.

    ``lanes`` is keyed by an unordered ``frozenset``, so index-parallel arrays
    across two messages need a stated order rather than a dict's. Sorted by the
    lane's own canonical ``(a, b)``.
    """
    return sorted(state.lanes.values(), key=lambda lane: (lane.a, lane.b))


def rules(min_swing: float = 1.0) -> dict[str, Any]:
    """The combat arithmetic a Python bot reads live off ``config``/``combat``.

    Both the knobs and the two break-even multiples derived from them go on the
    wire. The redundancy is deliberate: pricing a fight off a live figure rather
    than a constant is the one thing every bot in the roster is required to do,
    and re-deriving ``(1+j)/(1-j)`` in a second language is exactly the kind of
    duplicate that drifts with nothing failing. A bot that tuned its margins at
    a particular swing still has to floor the jitter half itself — the runner
    cannot know what it was fitted at (see ``TUNED_SWING`` in the roster).
    """
    return {
        "combat_jitter": float(config.COMBAT_JITTER),
        "defender_advantage": float(config.DEFENDER_ADVANTAGE),
        # (1+j)/(1-j) on its own: edge_attacking is that times the advantage.
        "swing": combat.edge_attacking(min_swing) / max(1e-9, float(config.DEFENDER_ADVANTAGE)),
        "edge_attacking": combat.edge_attacking(min_swing),
        "edge_defending": combat.edge_defending(min_swing),
        "in_lane_battles": bool(config.IN_LANE_BATTLES),
        "neutral_produces": bool(config.NEUTRAL_PRODUCES),
        "ship_ly_per_turn": float(config.SHIP_LY_PER_TURN),
        "ship_speed_growth_pct": float(config.SHIP_SPEED_GROWTH_PCT),
    }


def setup(state: GameState) -> dict[str, Any]:
    """The whole setup, read off ``config`` rather than off a ``Settings``.

    Every global knob reaches the game by ``settings._apply_globals`` writing it
    into ``config``, so ``config`` is what is actually in force — a caller that
    built its state straight from ``mapgen`` (``tests/sim.play``) has no
    ``Settings`` at all, and one that has may have edited it since. Reusing
    ``_GLOBAL_KNOBS`` rather than restating the list means a knob added to
    ``Settings`` reaches the wire with no second edit here.

    Sent in full rather than curated because ``nodes`` and ``ship_ly_per_turn``
    together move lane length over an order of magnitude (see bot-design, "Lane
    length across the parameter space"), and a posted leaderboard map carries
    tuned knobs: a bot that cannot see which regime it is in cannot price
    anything. ``challenge``/``autoplay`` are excluded — presentation and
    play-style, not setup.
    """
    out: dict[str, Any] = {
        "mode": state.mode,
        "seed": state.seed,
        "nodes": len(state.systems),
        "players": sum(1 for p in state.players.values() if not p.is_neutral),
    }
    for attr, const in settings_mod._GLOBAL_KNOBS:
        value = getattr(config, const)
        out[attr] = bool(value) if isinstance(value, bool) else value
    return out


def _seat(player, reveal: bool) -> dict[str, Any]:
    seat: dict[str, Any] = {"id": player.id, "is_neutral": player.is_neutral}
    if player.is_neutral:
        return seat
    seat["strategy"] = player.ai_strategy if reveal else f"seat{player.id}"
    seat["params"] = {
        "reserve_fraction": player.ai_params.reserve_fraction,
        "reserve_floor": player.ai_params.reserve_floor,
        "expand_margin": player.ai_params.expand_margin,
        "attack_margin": player.ai_params.attack_margin,
        "reinforce_margin": player.ai_params.reinforce_margin,
        "aux": player.ai_params.aux,
    }
    return seat


def hello(state: GameState, pid: int, budget_ms: int,
          reveal_opponents: bool = False) -> dict[str, Any]:
    """Everything fixed for the match. Sent once, before the first turn.

    ``reveal_opponents`` is a policy switch, not a fact. Off (the tournament
    default) a rival's ``ai_strategy`` is replaced by an opaque per-seat label:
    an entrant is meant to be ignorant of its opponents' *code*, and knowing
    which of a published roster it faces is counter-programming rather than
    prediction. On, names pass through, which is what lets a Python bot ported
    across the wire be compared order-for-order with the original
    (``test_botio.py``). Rival ``params`` are sent either way — documented as
    readable on every seat, and ``aux`` in particular exists to tell a shallow
    opponent from a deep one, which is tuning rather than identity.

    ``production`` and the graph are here rather than in ``turn_payload``
    because neither is ever written again after generation.
    """
    return {
        "type": "hello",
        "protocol": PROTOCOL,
        "you": pid,
        "budget_ms": int(budget_ms),
        "reveal_opponents": bool(reveal_opponents),
        "setup": setup(state),
        "rules": rules(),
        "seats": [_seat(p, reveal_opponents) for p in state.players.values()],
        "map": {
            "systems": [{"id": s.id, "pos": [s.pos[0], s.pos[1]],
                         "production": s.production, "neighbors": list(s.neighbors)}
                        for s in state.systems.values()],
            "lanes": [{"a": lane.a, "b": lane.b, "length_ly": lane.length_ly,
                       "base_turns": lane.travel_turns} for lane in _lane_order(state)],
        },
    }


def decide_seed(seed: int, turn: int, pid: int) -> int:
    """The bot's randomness for one decision: derived, never drawn.

    An external bot cannot draw from ``state.rng``, so it is handed a seed — and
    computing one from the match seed, the turn and the seat rather than drawing
    it means an external seat never shifts the engine's dice stream. Swap a bot
    in or out and every other seat's battles roll exactly as they did. A seed
    still reproduces the map and every fight, which is what the house rule
    protects.

    ``Random`` seeded with a string goes through SHA-512, so this is stable
    across runs, platforms and ``PYTHONHASHSEED`` — unlike ``hash()``.
    """
    return random.Random(f"{seed}:{turn}:{pid}").getrandbits(64)


def turn_payload(state: GameState, pid: int, budget_ms: int,
                 rng_seed: Optional[int] = None) -> dict[str, Any]:
    """What moved since the handshake, plus this decision's seed and budget.

    ``lane_turns`` rides along **only** when ship-speed growth is switched on,
    in ``_lane_order``'s index order; absent means the handshake's ``base_turns``
    still stand. Travel time is a query rather than a stored value
    (``config.travel_turns_at_length``), and this is how that survives the wire.

    The board is unfogged, which is what a Python bot gets: ``fog`` is
    presentation-only and neither the engine nor ``ai`` consults it.
    """
    if rng_seed is None:
        rng_seed = decide_seed(state.seed, state.turn, pid)
    payload: dict[str, Any] = {
        "type": "turn",
        "turn": state.turn,
        "you": pid,
        "rng_seed": rng_seed,
        "budget_ms": int(budget_ms),
        "systems": [{"id": s.id, "owner": s.owner_id, "ships": s.ships,
                     "prod_progress": s.prod_progress} for s in state.systems.values()],
        "fleets": [{"owner": f.owner_id, "src": f.source_id, "dst": f.dest_id,
                    "ships": f.ships, "left": f.turns_remaining, "total": f.turns_total}
                   for f in state.fleets],
        "players": [{"id": p.id, "alive": p.alive, "ships_lost": p.ships_lost}
                    for p in state.players.values() if not p.is_neutral],
    }
    if config.SHIP_SPEED_GROWTH_PCT:
        payload["lane_turns"] = [config.travel_turns_at_length(lane.length_ly, state.turn)
                                 for lane in _lane_order(state)]
    return payload


def _as_int(value: Any) -> Optional[int]:
    """An exact integer, or None. Rejects bools, floats with a fraction, and NaN."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def orders_from(reply: Any, pid: int) -> list[Order]:
    """Turn one ``{"type": "orders", ...}`` reply into ``Order``s for ``pid``.

    Shape only, and the omission is deliberate: whether a source is held, a
    destination adjacent, or a count affordable is ``engine.apply_order``'s to
    decide, and it decides it for every seat alike — a second copy of those
    rules here is a second thing to keep in step, for no change in outcome (an
    over-large count is clamped to the garrison at launch either way). What this
    does enforce is that a malformed reply cannot reach the engine as something
    subtly wrong: non-integer ids or counts are dropped rather than coerced.

    There is no owner on the wire at all, which is the protocol's answer to
    ``engine._own_orders``: a seat commands its own ships and nothing else, so
    the owner is stamped here and a foreign order is not something a bot can
    express. A reply that isn't an orders message yields nothing, the same as a
    bot that had no move.
    """
    if not isinstance(reply, dict) or reply.get("type") != "orders":
        return []
    raw = reply.get("orders")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return []
    orders: list[Order] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        src, dst, ships = (_as_int(item.get("src")), _as_int(item.get("dst")),
                           _as_int(item.get("ships")))
        if src is None or dst is None or ships is None or ships <= 0:
            continue
        orders.append(Order(pid, src, dst, ships))
    return orders
