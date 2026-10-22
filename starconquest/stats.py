"""Per-turn metrics for the end-of-game graphs (no pygame).

History review already holds one board per recorded turn (`main.build_history`);
a chart is read straight off those boards, so it can never disagree with the
map on screen at the same turn. Nothing here touches `state.rng` or the log:
the series are a report on boards already resolved, like `turnfilm`.
"""

from __future__ import annotations

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
    """

    turns: int
    seats: tuple[int, ...]
    values: dict[str, dict[int, tuple[float, ...]]]

    def series(self, metric: str, pid: int) -> tuple[float, ...]:
        return self.values.get(metric, {}).get(pid, ())

    def peak(self, metric: str, seats: tuple[int, ...] | list[int]) -> float:
        """The largest value any of ``seats`` reaches on ``metric`` (0 if none)."""
        return max((v for pid in seats for v in self.series(metric, pid)), default=0.0)

    def stack_peak(self, metric: str, seats: tuple[int, ...] | list[int]) -> float:
        """The tallest stack of ``seats`` on ``metric`` across turns (0 if none)."""
        return max((sum(self.series(metric, pid)[t] for pid in seats) for t in range(self.turns + 1)),
                   default=0.0) if seats else 0.0


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
