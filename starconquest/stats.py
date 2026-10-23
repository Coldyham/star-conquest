"""Per-turn metrics for the end-of-game graphs (no pygame).

History review already holds one board per recorded turn (`main.build_history`);
a chart is read straight off those boards, so it can never disagree with the
map on screen at the same turn. Nothing here touches `state.rng` or the log:
the series are a report on boards already resolved, like `turnfilm`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from . import fog
from .model import GameState

# (key, tab label, legend unit). Order is the tab order.
METRICS: tuple[tuple[str, str, str], ...] = (
    ("systems", "Systems", "systems"),
    ("share", "Map share", "% of systems"),
    ("ships", "Ships", "ships"),
    ("income", "Income", "ships/turn"),
    ("losses", "Losses", "ships lost"),
    ("lost_turn", "Lost/turn", "ships lost"),
)

# Metrics drawn as a bar per turn, stacked by seat, rather than a line per seat:
# a turn's bar is everything destroyed that turn, split by whose it was.
STACKED: frozenset[str] = frozenset({"lost_turn"})

# Metrics drawn as stacked bands that always fill 0-100%: a seat's band is its
# share of the map, with the unclaimed (neutral, `NEUTRAL`) share on top.
AREA: frozenset[str] = frozenset({"share"})
NEUTRAL = 0


@dataclass(frozen=True)
class Chart:
    """Every seat's metrics, one value per board (index == turn).

    ``values[metric][pid]`` has ``turns + 1`` entries: turn 0 is the opening
    position. ``losses`` is cumulative (`Player.ships_lost`), the figure a
    score's tie-break uses; ``lost_turn`` is the same count turn by turn (0 at
    the opening position). ``share`` is a percentage of every system on the
    map, and alone carries a `NEUTRAL` series too: what nobody holds.

    A chart `masked` to what one seat saw holds NaN for every value it didn't:
    a gap, never a zero.
    """

    turns: int
    seats: tuple[int, ...]
    values: dict[str, dict[int, tuple[float, ...]]]

    def series(self, metric: str, pid: int) -> tuple[float, ...]:
        return self.values.get(metric, {}).get(pid, ())

    def peak(self, metric: str, seats: tuple[int, ...] | list[int]) -> float:
        """The largest value any of ``seats`` reaches on ``metric`` (0 if none)."""
        return max((v for pid in seats for v in self.series(metric, pid) if not math.isnan(v)), default=0.0)

    def stack_peak(self, metric: str, seats: tuple[int, ...] | list[int]) -> float:
        """The tallest stack of ``seats`` on ``metric`` across turns (0 if none)."""
        return max((sum(known_or_zero(self.series(metric, pid)[t]) for pid in seats) for t in range(self.turns + 1)),
                   default=0.0) if seats else 0.0

    def sighted(self, pid: int) -> bool:
        """Is any of ``pid``'s values known? (False for a masked-out rival.)"""
        return any(not math.isnan(v) for v in self.series("systems", pid))


def known_or_zero(v: float) -> float:
    """``v``, or 0 for an unknown (NaN) value — for summing a stack."""
    return 0.0 if math.isnan(v) else v


def chart(states: list[GameState]) -> Chart:
    """Read a `Chart` off ``states``, the history boards in turn order."""
    if not states:
        return Chart(0, (), {key: {} for key, _, _ in METRICS})
    seats = tuple(sorted(p.id for p in states[0].non_neutral_players()))
    cols: dict[str, dict[int, list[float]]] = {key: {pid: [] for pid in seats} for key, _, _ in METRICS}
    cols["share"][NEUTRAL] = []
    for s in states:
        total = max(1, len(s.systems))
        held = {pid: 0 for pid in seats}
        for sys in s.systems.values():
            if sys.owner_id in held:
                held[sys.owner_id] += 1
        for pid in seats:
            cols["share"][pid].append(100.0 * held[pid] / total)
        cols["share"][NEUTRAL].append(100.0 * (total - sum(held.values())) / total)
        for pid in seats:
            systems, ships, income = fog.player_totals(s, pid)
            player = s.players.get(pid)
            cols["systems"][pid].append(float(systems))
            cols["ships"][pid].append(float(ships))
            cols["income"][pid].append(income)
            cols["losses"][pid].append(float(player.ships_lost if player else 0))
    for pid in seats:
        losses = cols["losses"][pid]
        cols["lost_turn"][pid] = [0.0] + [b - a for a, b in zip(losses, losses[1:])]
    values = {key: {pid: tuple(v) for pid, v in by_seat.items()} for key, by_seat in cols.items()}
    return Chart(len(states) - 1, seats, values)


UNKNOWN = math.nan


def sightings(states: list[GameState], visible: Iterable[set[int]], me: int) -> dict[int, tuple[bool, ...]]:
    """For each seat, which turns ``me`` knew its standings, as the top bar does
    (`render._score_vis`): our own always; a rival's while it holds a system in
    that turn's ``visible`` set (with fog off, every system), and once knocked
    out, if it was ever sighted before. ``visible`` runs parallel to ``states``."""
    if not states:
        return {}
    seats = tuple(sorted(p.id for p in states[0].non_neutral_players()))
    known: dict[int, list[bool]] = {pid: [] for pid in seats}
    ever: set[int] = set()
    for s, vis in zip(states, visible):
        owners = {s.systems[sid].owner_id for sid in vis if sid in s.systems}
        for pid in seats:
            player = s.players.get(pid)
            out = player is not None and not player.alive
            seen_now = pid == me or pid in owners
            known[pid].append(seen_now or (out and pid in ever))
            if seen_now:
                ever.add(pid)
    return {pid: tuple(v) for pid, v in known.items()}


def masked(c: Chart, known: dict[int, tuple[bool, ...]]) -> Chart:
    """``c`` with every value its seat wasn't known on (`sightings`) made
    `UNKNOWN`. A turn's losses (``lost_turn``) need both it and the turn before
    known, else a gap's losses would land on the turn after it. Map share's
    unclaimed series is left whole: render only shows it once review is revealed."""
    values: dict[str, dict[int, tuple[float, ...]]] = {}
    for key, by_seat in c.values.items():
        values[key] = {}
        for pid, series in by_seat.items():
            seen = known.get(pid)
            if seen is None:
                values[key][pid] = series
                continue
            if key in STACKED:
                ok = [False] + [a and b for a, b in zip(seen, seen[1:])]
                ok[0] = seen[0]
            else:
                ok = list(seen)
            values[key][pid] = tuple(v if k else UNKNOWN for v, k in zip(series, ok))
    return Chart(c.turns, c.seats, values)
