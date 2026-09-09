"""Playback of a turn the engine has already resolved.

Presentation-only and pure of pygame, the mirror of ``fog``: the shell reads that
and the engine never consults it, while the engine *writes* into this and never
reads it back. ``engine.end_turn`` calls an ``on_event`` sink as each phase does
its work, ``film`` turns one turn's events into a `Film` — beats with durations
and cues at absolute times — and `Reel` walks a copy of the start-of-turn board
through those cues as the clock runs.

Two properties hold the whole thing together.

Every event carries an **outcome** rather than the inputs to recompute one, so
applying one is assignment and never re-simulation: a film cannot drift from the
turn it depicts, and `Reel` draws no dice and does no arithmetic. And a film is
assembled from the order the engine *emitted* events, never from a phase order
written down here, so changing the engine's phase order changes the playback with
nothing to update.

Nothing here is recorded. A film is derived per turn and discarded, so
``replay``'s format is untouched by it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from . import config
from .model import Fleet, GameState

# ---------------------------------------------------------------------------- #
# Events. Frozen, because they end up on `Ui` where `render` reads them, and
# mappings are tuples-of-pairs rather than dicts for the same reason.
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Launched:
    """A fleet left its source, which paid for it at once."""

    fleet: int
    owner_id: int
    source_id: int
    dest_id: int
    ships: int
    turns_total: int
    turns_remaining: int
    source_ships: int  # the garrison *after* the deduction


@dataclass(frozen=True)
class Advanced:
    """Every fleet in transit counted down one turn.

    Carries each fleet's new ``turns_remaining`` rather than saying "decrement",
    which would put the rule in two places.
    """

    steps: tuple[tuple[int, int], ...]  # (fleet id, new turns_remaining)


@dataclass(frozen=True)
class Clashed:
    """Two enemy fleets met in transit and fought.

    ``when`` is the fraction of the step at which their gap reached zero and
    ``at`` the lane fraction from ``low_id`` where that happened, so the fight can
    be shown exactly where the triangles are seen to touch.
    """

    low_id: int
    high_id: int
    when: float
    at: float
    a: int
    b: int
    a_ships: int
    b_ships: int
    survivor: Optional[int]  # the fleet that flew on; None on annihilation
    survivors: int
    dead: tuple[int, ...]


@dataclass(frozen=True)
class Fold:
    """One pairwise step of a multi-owner arrival.

    ``attacker`` holds the force carried into this step — the winner of the last
    one — so a chain of these is the whole of how a pile-up resolves.
    """

    attacker: int  # owner ids, not fleet ids: arrivals are pooled per owner
    attacker_ships: int
    defender: int
    defender_ships: int
    winner: int
    survivors: int


@dataclass(frozen=True)
class Landed:
    """Fleets reached a node and the fight there resolved."""

    node_id: int
    fleets: tuple[int, ...]
    was_owner: int
    was_ships: int
    owner_id: int
    ships: int
    prod_progress: int
    steps: tuple[Fold, ...]  # empty for a reinforcement or an unopposed landing


@dataclass(frozen=True)
class Produced:
    """Systems accrued toward their next hull, and some finished one."""

    ticks: tuple[tuple[int, int, int], ...]  # (system id, ships, prod_progress)


@dataclass(frozen=True)
class Ended:
    """The turn closed: the win check ran and the clock moved."""

    turn: int
    winner: Optional[int]
    alive: tuple[tuple[int, bool], ...]
    ships_lost: tuple[tuple[int, int], ...]


Event = Union[Launched, Advanced, Clashed, Landed, Produced, Ended]
EventFn = Callable[[Event], None]

# Which beat an event belongs to, and what that beat is called. `Clashed` is
# absent deliberately: it rides inside the move beat it happened during.
_KINDS: dict[type, tuple[str, str]] = {
    Launched: ("launch", "Fleets launch"),
    Advanced: ("move", "Fleets move"),
    Produced: ("produce", "Production"),
    Landed: ("combat", "Combat"),
}
_DURATIONS: dict[str, str] = {
    "launch": "FILM_LAUNCH_MS",
    "move": "FILM_MOVE_MS",
    "produce": "FILM_PRODUCE_MS",
    "combat": "FILM_COMBAT_MS",
}


# ---------------------------------------------------------------------------- #
# The timeline
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Beat:
    """One stretch of a film, named so the caption can say what is happening."""

    kind: str
    label: str
    start: float
    ms: float

    @property
    def end(self) -> float:
        return self.start + self.ms

    def holds(self, ms: float) -> bool:
        """Whether ``ms`` falls in this beat. A zero-length beat holds nothing —
        it is an instant, not a stretch."""
        return self.ms > 0 and self.start <= ms < self.end


@dataclass(frozen=True)
class Film:
    """A resolved turn as beats and timed cues. Immutable, so it is safe on `Ui`
    for `render` to read."""

    beats: tuple[Beat, ...]
    cues: tuple[tuple[float, Event], ...]
    total_ms: float

    @property
    def plays(self) -> bool:
        """Whether there is anything here to watch.

        Every beat can be an instant — a turn where only production happened, at
        `config.FILM_PRODUCE_MS` of 0 — and holding the finished board for one of
        those is dead air rather than an animation.
        """
        return any(beat.ms > 0 for beat in self.beats)

    def beat_at(self, ms: float) -> Optional[Beat]:
        for beat in self.beats:
            if beat.holds(ms):
                return beat
        return None

    def label(self, ms: float) -> str:
        beat = self.beat_at(ms)
        return beat.label if beat is not None else ""

    def travel(self, ms: float) -> float:
        """How far through this turn's step the fleets are, in [0, 1].

        1.0 everywhere outside the move beat, which is right at both ends: before
        it the board still carries pre-advance schedules, so ``progress_at(1.0)``
        is where each fleet started the step, and after it the advance has been
        applied, so the same call is where each fleet finished.
        """
        beat = self.beat_at(ms)
        if beat is None or beat.kind != "move":
            return 1.0
        return min(1.0, max(0.0, (ms - beat.start) / beat.ms))

    def flashes(self, ms: float) -> tuple[Event, ...]:
        """The fights close enough behind ``ms`` to still be showing."""
        return tuple(
            event
            for at, event in self.cues
            if isinstance(event, (Clashed, Landed)) and 0.0 <= ms - at <= config.FILM_FLASH_MS
        )


def film(events: list[Event]) -> Film:
    """Schedule one turn's events.

    Consecutive events of the same class become one beat, in emission order —
    nothing here consults a phase order, so the engine's is the one that shows.
    Events inside a beat are spread across it, which bounds a film's length by the
    duration constants however busy the turn was.
    """
    beats: list[Beat] = []
    cues: list[tuple[float, Event]] = []
    clashes: list[Clashed] = []
    at = 0.0

    # Group into runs of one class, holding clashes back until the move beat they
    # belong to has a start and a length to place them in.
    runs: list[tuple[str, str, list[Event]]] = []
    for event in events:
        if isinstance(event, Clashed):
            clashes.append(event)
            continue
        if isinstance(event, Ended):
            continue  # placed last, once the beats are laid out
        kind, label = _KINDS[type(event)]
        if runs and runs[-1][0] == kind:
            runs[-1][2].append(event)
        else:
            runs.append((kind, label, [event]))

    for kind, label, run in runs:
        ms = float(getattr(config, _DURATIONS[kind]))
        beat = Beat(kind, label, at, ms)
        beats.append(beat)
        # At the *start* of each event's slot: the advance has to land on the move
        # beat's first frame, since `travel` sweeps the schedule it applies.
        for i, event in enumerate(run):
            cues.append((at + ms * i / len(run), event))
        if kind == "move" and clashes:
            for clash in clashes:
                cues.append((at + ms * min(1.0, max(0.0, clash.when)), clash))
            clashes = []
        at = beat.end

    for clash in clashes:  # no move beat to ride in: give them the closing instant
        cues.append((at, clash))
    for event in events:
        if isinstance(event, Ended):
            cues.append((at, event))

    # Stable, and on the time alone, so cues sharing an instant — which a
    # zero-length beat guarantees — keep the order the engine emitted them in.
    cues.sort(key=lambda c: c[0])
    hold = max(config.FILM_END_MS, config.FILM_FLASH_MS) if cues else 0.0
    return Film(tuple(beats), tuple(cues), at + hold)


# ---------------------------------------------------------------------------- #
# The playhead
# ---------------------------------------------------------------------------- #


def copy_board(state: GameState) -> GameState:
    """A board a film can be played onto without touching the live one."""
    return copy.deepcopy(state)


@dataclass
class Reel:
    """A board being walked through a film.

    Strict on purpose: an id a cue names and the board does not have is a
    `KeyError` here, so a film that has fallen out of step with the engine fails
    loudly in a seeded test rather than glitching quietly on screen.
    """

    board: GameState
    film: Film
    at: int = 0
    _fleets: dict[int, Fleet] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._fleets = dict(enumerate(self.board.fleets))

    def run_to(self, ms: float) -> None:
        """Apply every cue due by ``ms``, in cue order."""
        while self.at < len(self.film.cues) and self.film.cues[self.at][0] <= ms:
            self._apply(self.film.cues[self.at][1])
            self.at += 1

    def run(self) -> None:
        self.run_to(float("inf"))

    @property
    def done(self) -> bool:
        return self.at >= len(self.film.cues)

    def _drop(self, fleet_id: int) -> None:
        fleet = self._fleets.pop(fleet_id)
        self.board.fleets = [f for f in self.board.fleets if f is not fleet]

    def _apply(self, event: Event) -> None:
        if isinstance(event, Launched):
            self.board.systems[event.source_id].ships = event.source_ships
            fleet = Fleet(
                owner_id=event.owner_id,
                source_id=event.source_id,
                dest_id=event.dest_id,
                ships=event.ships,
                turns_total=event.turns_total,
                turns_remaining=event.turns_remaining,
            )
            self.board.fleets.append(fleet)
            self._fleets[event.fleet] = fleet
        elif isinstance(event, Advanced):
            for fleet_id, remaining in event.steps:
                self._fleets[fleet_id].turns_remaining = remaining
        elif isinstance(event, Clashed):
            if event.survivor is not None:
                self._fleets[event.survivor].ships = event.survivors
            for fleet_id in event.dead:
                self._drop(fleet_id)
        elif isinstance(event, Landed):
            for fleet_id in event.fleets:
                self._drop(fleet_id)
            node = self.board.systems[event.node_id]
            node.owner_id, node.ships = event.owner_id, event.ships
            node.prod_progress = event.prod_progress
        elif isinstance(event, Produced):
            for sid, ships, progress in event.ticks:
                system = self.board.systems[sid]
                system.ships, system.prod_progress = ships, progress
        elif isinstance(event, Ended):
            self.board.turn = event.turn
            self.board.winner = event.winner
            for pid, alive in event.alive:
                self.board.players[pid].alive = alive
            for pid, lost in event.ships_lost:
                self.board.players[pid].ships_lost = lost
        else:  # pragma: no cover - the Union is closed
            raise TypeError(f"not a turn event: {event!r}")


# ---------------------------------------------------------------------------- #
# The engine's side
# ---------------------------------------------------------------------------- #


class Watch:
    """What ``engine.end_turn`` reports a turn through.

    Owns every event's construction and the turn-local fleet numbering, so the
    engine names method calls and nothing else. With no sink every method returns
    at once, which is what keeps a headless batch paying nothing for this.

    Fleets are numbered by their position in the start-of-turn list and then in
    launch order — the numbering `Reel` reproduces from a copy of that same list.
    Identity is keyed on ``id()`` with the object held alongside, so a fleet that
    dies mid-turn cannot have its address handed to a later one.
    """

    def __init__(self, sink: Optional[EventFn]) -> None:
        self._sink = sink
        self._ids: dict[int, tuple[Fleet, int]] = {}
        self._next = 0
        self._before: dict[int, tuple[int, int]] = {}

    @property
    def on(self) -> bool:
        return self._sink is not None

    def _id(self, fleet: Fleet) -> int:
        held = self._ids.get(id(fleet))
        if held is None or held[0] is not fleet:
            self._ids[id(fleet)] = (fleet, self._next)
            self._next += 1
            return self._next - 1
        return held[1]

    def open(self, state: GameState) -> None:
        """Number the fleets already in transit, before anything launches."""
        if self._sink is None:
            return
        for fleet in state.fleets:
            self._id(fleet)

    def launched(self, state: GameState, fleet: Optional[Fleet]) -> None:
        if self._sink is None or fleet is None:  # an illegal order launched nothing
            return
        self._sink(Launched(
            fleet=self._id(fleet),
            owner_id=fleet.owner_id,
            source_id=fleet.source_id,
            dest_id=fleet.dest_id,
            ships=fleet.ships,
            turns_total=fleet.turns_total,
            turns_remaining=fleet.turns_remaining,
            source_ships=state.systems[fleet.source_id].ships,
        ))

    def advanced(self, state: GameState) -> None:
        if self._sink is None:
            return
        steps = tuple((self._id(f), f.turns_remaining) for f in state.fleets)
        if steps:  # nothing in transit buys no move beat, and so no dead air
            self._sink(Advanced(steps))

    def clashed(self, crossing, lane, a: Fleet, b: Fleet, a_ships: int,
                b_ships: int, survivor: Optional[Fleet], survivors: int,
                dead: tuple[Fleet, ...]) -> None:
        if self._sink is None:
            return
        self._sink(Clashed(
            low_id=min(lane),
            high_id=max(lane),
            when=crossing.when,
            at=crossing.at,
            a=self._id(a),
            b=self._id(b),
            a_ships=a_ships,
            b_ships=b_ships,
            survivor=None if survivor is None else self._id(survivor),
            survivors=survivors,
            dead=tuple(self._id(f) for f in dead),
        ))

    def folds(self) -> Optional[list]:
        """A sink for ``combat.resolve_arrival`` to log its pairwise fold into."""
        return [] if self._sink is not None else None

    def landed(self, state: GameState, node_id: int, arrived: list[Fleet],
               was_owner: int, was_ships: int, folds: Optional[list]) -> None:
        if self._sink is None:
            return
        node = state.systems[node_id]
        self._sink(Landed(
            node_id=node_id,
            fleets=tuple(self._id(f) for f in arrived),
            was_owner=was_owner,
            was_ships=was_ships,
            owner_id=node.owner_id,
            ships=node.ships,
            prod_progress=node.prod_progress,
            steps=tuple(Fold(*step) for step in (folds or ())),
        ))

    def mark(self, state: GameState) -> None:
        """Note what production is about to change, so it can be reported as a
        diff rather than as a re-derivation of the rule."""
        if self._sink is None:
            return
        self._before = {s.id: (s.ships, s.prod_progress) for s in state.systems.values()}

    def produced(self, state: GameState) -> None:
        if self._sink is None:
            return
        ticks = tuple(
            (s.id, s.ships, s.prod_progress)
            for s in state.systems.values()
            if self._before.get(s.id) != (s.ships, s.prod_progress)
        )
        if ticks:
            self._sink(Produced(ticks))

    def ended(self, state: GameState) -> None:
        if self._sink is None:
            return
        self._sink(Ended(
            turn=state.turn,
            winner=state.winner,
            alive=tuple((p.id, p.alive) for p in state.players.values()),
            ships_lost=tuple((p.id, p.ships_lost) for p in state.players.values()),
        ))


def watcher(sink: Optional[EventFn]) -> Watch:
    return Watch(sink)
