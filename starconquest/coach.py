"""The post-game coach: notes on a decided game, read off its history (no pygame).

History review holds one board per recorded turn (`main.build_history`) and the
log holds every seat's orders. The coach reads both after the game is decided,
when nothing is hidden any more, and flags the turns worth a second look:

- **A failed attack.** Our ships landing together on a system they did not
  take. Below the sure count (`combat.sure_count`) for the garrison they could
  expect, it was a gamble; at or above it, the owner reinforced or a rival
  landed too.
- **Emptied, then lost.** A system we sent at least half its garrison out of
  and lost within the next three turns. When the ships sent out could not
  have held it against what landed, it was an **evacuation**, noted as such.
- **Idle ships.** A system holding enough to take a neutral next door on every
  roll, with no rival next door, sending nothing for `IDLE_TURNS` turns or more.
- **The turning point.** The turn our income (`fog.player_totals`, as the
  graphs read it) fell behind a rival's for good, or, in a win, passed every
  rival's for good.

Every note names the board to look at: the turn the decision was made on,
which is where History should jump. The coach reads; it decides nothing, draws
no rng and never changes a board, like `stats`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import combat, config, stats
from .model import GameState, Order

IDLE_TURNS = 5  # turns a system sits idle beside a sure capture before it is a note
EMPTIED_WINDOW = 2  # turns before a loss in which an emptying launch counts
EMPTIED_SHARE = 0.5  # a launch of at least this share of the garrison empties it

# Note kinds, in the order the summary counts them.
GAMBLE = "gamble"      # an attack below the sure count that failed
SURPRISE = "surprise"  # a sure-looking attack that failed anyway
EMPTIED = "emptied"    # a system sent out and then lost
EVACUATED = "evacuated"  # the same, but staying would have lost it anyway
IDLE = "idle"          # ships sitting beside a sure capture
TURN = "turn"          # the turn the game turned, for or against us


@dataclass(frozen=True)
class Note:
    """One thing worth a second look. ``turn`` is the board to jump to;
    ``systems`` are the ones to point at there; ``cost`` orders notes by how much
    they mattered (ships thrown away, ship-turns idle) where there are too many."""

    turn: int
    kind: str
    text: str
    systems: tuple[int, ...]
    cost: float = 0.0


@dataclass(frozen=True)
class Review:
    """The coach's notes on one seat's game, in turn order, and the tally the
    summary line reads."""

    notes: tuple[Note, ...]
    captures: int
    failed: int
    lost: int

    @property
    def summary(self) -> str:
        parts = [f"{self.captures} capture{'s' if self.captures != 1 else ''}",
                 f"{self.failed} failed attack{'s' if self.failed != 1 else ''}",
                 f"{self.lost} system{'s' if self.lost != 1 else ''} lost"]
        return " · ".join(parts)


def review(states: list[GameState], orders: list[list[Order]], seat: int,
           limit: int = 24) -> Review:
    """Notes on ``seat``'s game. ``states[i]`` is the board before turn ``i``
    (``states[0]`` the opening position) and ``orders[i]`` every seat's orders
    for that turn, as `replay.GameLog.orders_for` gives them. At most ``limit``
    notes, the costliest kept; the turning point is always kept."""
    if len(states) < 2 or seat not in states[0].players:
        return Review((), 0, 0, 0)
    mine = [[o for o in turn if o.owner_id == seat and o.ships > 0] for turn in orders]
    mine += [[] for _ in range(len(states) - len(mine))]
    attacks, captures, failed = _attacks(states, mine, seat)
    emptied, lost = _emptied(states, orders, mine, seat)
    notes = attacks + emptied + _idle(states, mine, seat)
    turning = _turning_point(states, seat)
    notes.sort(key=lambda n: -n.cost)
    kept = notes[:max(0, limit - (turning is not None))]
    if turning is not None:
        kept.append(turning)
    kept.sort(key=lambda n: (n.turn, n.kind))
    return Review(tuple(kept), captures, failed, lost)


def _name(state: GameState, sid: int) -> str:
    return state.systems[sid].short


def _ships(n: int) -> str:
    return f"{n} ship{'s' if n != 1 else ''}"


def _rates(a: float, b: float) -> tuple[str, str]:
    """Two incomes to as few places as tell them apart."""
    for places in (1, 2, 3):
        if f"{a:.{places}f}" != f"{b:.{places}f}":
            return f"{a:.{places}f}", f"{b:.{places}f}"
    return f"{a:.3f}", f"{b:.3f}"


def _who(state: GameState, pid: int) -> str:
    return state.players[pid].name if pid in state.players else f"Player {pid}"


def _attacks(states: list[GameState], mine: list[list[Order]], seat: int
             ) -> tuple[list[Note], int, int]:
    """Our launches grouped by where and when they land, each group judged by
    the board after it landed."""
    last = len(states) - 1
    # (dest, landing board) -> [(launch board, order)]
    groups: dict[tuple[int, int], list[tuple[int, Order]]] = defaultdict(list)
    for i, turn in enumerate(mine[:last]):
        for o in turn:
            t = states[i].travel_turns(o.source_id, o.dest_id)
            if t is None or i + t > last:
                continue
            groups[(o.dest_id, i + t)].append((i, o))
    notes: list[Note] = []
    captures = failed = 0
    jitter, advantage = float(config.COMBAT_JITTER), float(config.DEFENDER_ADVANTAGE)
    for (dest, landed), launches in sorted(groups.items(), key=lambda kv: kv[0][1]):
        if states[landed - 1].systems[dest].owner_id == seat:
            continue   # it was ours when they landed: a reinforcement
        decided = max(i for i, _o in launches)   # the latest board a launch was sized on
        if states[decided].systems[dest].owner_id == seat:
            continue
        ships = sum(o.ships for _i, o in launches)
        after = states[landed].systems[dest]
        if after.owner_id == seat:
            captures += 1
            continue
        failed += 1
        board = states[decided]
        expected = combat.garrison_after(board, dest, landed - decided)
        sure = combat.sure_count(expected, jitter, advantage)
        name = _name(board, dest)
        sources = tuple(sorted({o.source_id for _i, o in launches}))
        if ships < sure:
            outcome = ("both sides were wiped out" if after.owner_id == 0
                       and board.systems[dest].owner_id != 0 else "the attack failed")
            text = (f"{_ships(ships)} attacked {name}, which would hold "
                    f"{expected}; {sure} would have been sure. {outcome[0].upper()}{outcome[1:]}.")
            kind = GAMBLE
        else:
            text = (f"{_ships(ships)} should have taken {name} ({expected} expected), but it held: "
                    "its owner reinforced it or a rival landed too.")
            kind = SURPRISE
        notes.append(Note(decided, kind, text, sources + (dest,), float(ships)))
    return notes, captures, failed


def _emptied(states: list[GameState], orders: list[list[Order]], mine: list[list[Order]],
             seat: int) -> tuple[list[Note], int]:
    """Systems we lost soon after sending most of their garrison away.

    The note names the one launch that emptied the system, its own ships against
    its own board's garrison (a sum over several turns could outnumber any one
    board's). A loss that staying could not have stopped (everything sent in the
    window back home, against the rival force that landed, still falls on most
    rolls) was an evacuation, and is noted as one rather than as a lesson."""
    notes: list[Note] = []
    lost = 0
    jitter, advantage = float(config.COMBAT_JITTER), float(config.DEFENDER_ADVANTAGE)
    for i in range(len(states) - 1):
        before, after = states[i], states[i + 1]
        for sid, node in before.systems.items():
            if node.owner_id != seat or after.systems[sid].owner_id == seat:
                continue
            lost += 1
            window = range(max(0, i - EMPTIED_WINDOW), i + 1)
            launched = {j: sum(o.ships for o in mine[j] if o.source_id == sid) for j in window}
            emptying = [j for j in window if launched[j] > 0
                        and launched[j] >= EMPTIED_SHARE * states[j].systems[sid].ships]
            if not emptying:
                continue
            first = max(emptying, key=lambda j: (launched[j], -j))
            held = states[first].systems[sid].ships
            sent = launched[first]
            taker = after.systems[sid].owner_id
            name = _name(before, sid)
            by = f"{_who(after, taker)} took it" if taker != 0 else "it was lost"
            # What staying would have met: the strongest rival landing that turn,
            # against the garrison plus every ship sent out in the window.
            stayed = node.ships + sum(launched[j] for j in window if j < i)
            landing = _landing(before, orders[i] if i < len(orders) else [], sid, seat)
            pid, force = max(landing.items(), key=lambda kv: (kv[1], kv[0] == taker),
                             default=(taker, 0))
            rival = _who(after, pid)
            if force > 0 and combat.fight_odds(force, stayed, jitter, advantage).capture >= 0.5:
                text = (f"You pulled {sent} of {held} ships out of {name} before {rival}'s "
                        f"{force} landed; {stayed} could not have held it, so that saved "
                        f"them. {by[0].upper()}{by[1:]} on turn {i + 1}.")
                notes.append(Note(first, EVACUATED, text, (sid,), 0.0))
                continue
            took = _captured_by(states, mine[first], first, sid, seat)
            gain = f" and took {_names(before, took)} with them" if took else ""
            by = f"{_who(after, taker)} took {name}" if took and taker != 0 else by
            text = f"You sent {sent} of {held} ships out of {name}{gain}; {by} on turn {i + 1}."
            if force > 0:
                text += f" Kept home, {stayed} would likely have held {name} against {rival}'s {force}."
            notes.append(Note(first, EMPTIED, text, (sid,), float(held)))
    return notes, lost


def _captured_by(states: list[GameState], launches: list[Order], turn: int, sid: int,
                 seat: int) -> list[int]:
    """Systems the launches out of ``sid`` on ``turn`` took when they landed:
    what the emptying bought, the coach's one view of a trade."""
    took: list[int] = []
    for dest in sorted({o.dest_id for o in launches if o.source_id == sid}):
        t = states[turn].travel_turns(sid, dest)
        landed = turn + t if t is not None else len(states)
        if (landed < len(states) and states[landed - 1].systems[dest].owner_id != seat
                and states[landed].systems[dest].owner_id == seat):
            took.append(dest)
    return took


def _names(state: GameState, sids: list[int]) -> str:
    names = [_name(state, sid) for sid in sids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _landing(state: GameState, turn_orders: list[Order], sid: int, seat: int) -> dict[int, int]:
    """Rival ships landing on ``sid`` during the turn played from ``state``, by
    owner: fleets a step out, and this turn's launches one turn away."""
    force: dict[int, int] = defaultdict(int)
    for f in state.fleets:
        if f.dest_id == sid and f.owner_id != seat and f.turns_remaining <= 1:
            force[f.owner_id] += f.ships
    for o in turn_orders:
        if (o.dest_id == sid and o.owner_id != seat and o.ships > 0
                and state.travel_turns(o.source_id, sid) == 1):
            force[o.owner_id] += o.ships
    return dict(force)


def _idle(states: list[GameState], mine: list[list[Order]], seat: int) -> list[Note]:
    """Streaks of turns a system sat on a sure capture of a neutral next door."""
    jitter, advantage = float(config.COMBAT_JITTER), float(config.DEFENDER_ADVANTAGE)
    notes: list[Note] = []
    streak: dict[int, tuple[int, int, int, int]] = {}   # sid -> (start, target, ships, sure)

    def close(sid: int, end: int) -> None:
        start, target, ships, sure = streak.pop(sid)
        if end - start >= IDLE_TURNS:
            board = states[start]
            g = combat.garrison_after(board, target, board.travel_turns(sid, target) or 0)
            text = (f"Turns {start}-{end - 1}: {_name(board, sid)} sat on {ships} ships beside "
                    f"{_name(board, target)} (neutral, {g}); {sure} would have taken it.")
            notes.append(Note(start, IDLE, text, (sid, target), float(end - start) * ships / 4))

    for i in range(len(states) - 1):
        board = states[i]
        sending = {o.source_id for o in mine[i]}
        for sid, node in board.systems.items():
            best = None
            if node.owner_id == seat and sid not in sending and not any(
                    board.systems[n].owner_id not in (0, seat) for n in node.neighbors):
                for n in node.neighbors:
                    if board.systems[n].owner_id != 0:
                        continue
                    t = board.travel_turns(sid, n)
                    if t is None:
                        continue
                    sure = combat.sure_count(combat.garrison_after(board, n, t), jitter, advantage)
                    if node.ships >= sure and (best is None or sure < best[1]):
                        best = (n, sure)
            if best is None:
                if sid in streak:
                    close(sid, i)
            elif sid not in streak:
                streak[sid] = (i, best[0], node.ships, best[1])
    for sid in list(streak):
        close(sid, len(states) - 1)
    return notes


def _turning_point(states: list[GameState], seat: int) -> Note | None:
    """The turn the income race was settled: from a loss, the last time a rival
    went ahead of us for good; from a win, the first time we led for good. Income
    is the graphs' own reading (`stats.chart`), so the note and the Income tab agree."""
    chart = stats.chart(states)
    ours = chart.series("income", seat)
    rivals = [pid for pid in chart.seats if pid != seat]
    if not ours or not rivals:
        return None
    last = len(ours) - 1
    final = states[-1]

    def best_rival(t: int) -> tuple[float, int]:
        return max((chart.series("income", pid)[t], pid) for pid in rivals)

    won = final.winner == seat
    t = last
    if won:
        while t > 0 and ours[t - 1] > best_rival(t - 1)[0]:
            t -= 1
        if t <= 0 or ours[t] <= best_rival(t)[0]:
            return None
        rate, _pid = best_rival(t)
        a, b = _rates(ours[t], rate)
        text = (f"Your income passed every rival's ({a} vs {b} ships a turn) "
                "and stayed ahead: the game turned here.")
    else:
        while t > 0 and ours[t - 1] < best_rival(t - 1)[0]:
            t -= 1
        if t <= 0 or ours[t] >= best_rival(t)[0]:
            return None
        rate, pid = best_rival(t)
        a, b = _rates(rate, ours[t])
        text = (f"{_who(final, pid)}'s income passed yours ({a} vs {b} ships a turn) "
                "and stayed ahead: the game turned here.")
    return Note(t, TURN, text, (), 0.0)
