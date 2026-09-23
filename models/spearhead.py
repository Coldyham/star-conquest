"""spearhead — a measurement probe for marshal's empty interior, not a roster bot.

marshal leaves most of its territory holding nothing: 46.8% of its systems are
interior and empty, and 35.8% of what it loses was empty when the turn began (see
"The 2026-09 tuning sweep" in `docs/bot-design.md`). The one probe written against
that before — cheapest non-owned neighbour first, never consolidate — lost 240 of
240, which says the probe was weak, not that the hole is closed. This one is
marshal itself plus one overlay, so it is exactly as competent everywhere else
and any gap in a paired duel is the overlay's:

  * **One hammer.** Each turn the largest garrison we hold that borders a rival is
    the hammer. If it can take a rival neighbour at marshal's own price
    (``_required``), it goes in with *everything* — no guard left behind — at
    the target that opens the most: its production plus that of the rival
    systems past it the stack could still take afterwards (``DEPTH_WEIGHT``).
    Next turn the stack is sitting on the capture, still the largest garrison,
    now bordering the empty interior, and goes again. Nothing is remembered —
    the hammer is re-derived from the board every turn.

  * **Everything feeds it** (``FEED_HAMMER``). marshal's Phase 4 flows rear
    surplus toward whichever front is richest, which spreads it across every
    front. Here every own-to-own move that is not relief for a threatened
    system is rerouted one hop along the shortest owned path toward the hammer,
    so the whole economy converges on one point.

Stateless and read-only, draws nothing from ``state.rng``. It runs a *private*
import of ``marshal.py`` so a sweep arm that patches marshal's constants cannot
reach into this probe, and this probe's base play cannot drift from stock.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict, deque
from pathlib import Path

from starconquest.model import Order

HAMMER = True            # drive the largest rival-facing stack in, all in
FEED_HAMMER = True       # reroute non-relief flow toward the hammer
DEPTH_WEIGHT = 1.0       # weight on the takeable rival production past a target


def _private_marshal():
    name = "sc_probe_spearhead_marshal"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent / "marshal.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_M = _private_marshal()


def _rival(state, pid, sid) -> bool:
    o = state.systems[sid].owner_id
    return o != pid and o != 0


def _rate(s) -> float:
    """Ships per turn — ``production`` is a build interval, lower is faster."""
    return 1.0 / s.production if s.production > 0 else 0.0


def _hammer(state, pid):
    """Our largest garrison bordering a rival, or ``None``."""
    best = None
    for sid, s in state.systems.items():
        if s.owner_id != pid or s.ships <= 0:
            continue
        if not any(_rival(state, pid, n) for n in s.neighbors):
            continue
        if best is None or (s.ships, -sid) > (best.ships, -best.id):
            best = s
    return best


def _target(state, pid, hammer):
    """The rival neighbour the whole stack should go through, or ``None``."""
    best, best_key = None, None
    for n in sorted(hammer.neighbors):
        if not _rival(state, pid, n):
            continue
        t = state.systems[n]
        dist = state.travel_turns(hammer.id, n) or 1
        req = _M._required(state, pid, t, dist)
        if hammer.ships < req:
            continue
        left = hammer.ships - req
        behind = [m for m in t.neighbors
                  if m != hammer.id and state.systems[m].owner_id == t.owner_id
                  and state.systems[m].ships < left]
        value = _rate(t) + DEPTH_WEIGHT * sum(_rate(state.systems[m]) for m in behind)
        key = (value, -dist, -t.ships, -n)
        if best_key is None or key > best_key:
            best, best_key = t, key
    return best


def _toward(state, pid, root) -> dict[int, int]:
    """Owned system -> next hop on the shortest owned path to ``root``."""
    parent: dict[int, int] = {}
    seen = {root}
    queue = deque([root])
    while queue:
        cur = queue.popleft()
        for n in sorted(state.systems[cur].neighbors):
            if n not in seen and state.systems[n].owner_id == pid:
                seen.add(n)
                parent[n] = cur
                queue.append(n)
    return parent


def decide(state, pid):
    base = _M.decide(state, pid)
    if not HAMMER:
        return base
    hammer = _hammer(state, pid)
    if hammer is None:
        return base
    target = _target(state, pid, hammer)

    sends: dict[tuple[int, int], int] = defaultdict(int)
    parent = _toward(state, pid, hammer.id) if FEED_HAMMER else {}
    for o in base:
        if target is not None and o.source_id == hammer.id:
            continue                         # the hammer's own ships all go below
        dst = state.systems[o.dest_id]
        relief = _M._incoming(state, o.dest_id, pid, hostile=True) > 0
        if (FEED_HAMMER and dst.owner_id == pid and not relief
                and o.source_id != hammer.id and o.source_id in parent):
            sends[(o.source_id, parent[o.source_id])] += o.ships
        else:
            sends[(o.source_id, o.dest_id)] += o.ships
    if target is not None:
        sends[(hammer.id, target.id)] += hammer.ships
    return [Order(pid, s, d, n) for (s, d), n in sorted(sends.items()) if n > 0]
