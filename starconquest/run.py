"""A run of a gauntlet: a way from a start to a goal, each stage won within
its target, with one life — pure core, no pygame.

Offered only where a run adds something to the stage list (``offered``): a
target to meet, or a fork, where the way chosen matters because losing on it
ends the run. On the tutorial, a single path with no stage target, a run would
be the list over again. A run's games are ordinary games. They post, file bests and clear
stages as any other, and nothing about a run reaches ``Settings`` or the log,
so the board and the verifier never see one.

**A run is stored, not derived** (``paths.WEB_RUNS_KEY``), unlike a stage clear
(``gauntlet`` module docstring). It is a sequence of games, one per stage, and
bests keep only the best game on each stage. A step is written when its game
starts and again the moment it is decided, never only at the end.

**A run judges only its own game.** Each step names its game's log. Only that
game's result moves the run, and only once. A Retry, a rewind or a fork after
it is decided is free play and leaves the run alone, so nothing needs blocking
there. While the game is undecided it plays ironman (no History or Undo, so no rewind),
and the Hint is not offered.

**Quitting doesn't escape it.** The active step's log is kept in ``webstore``
as well as on disk, rewritten every turn (``keep_log``), because ``games/`` is
MEMFS on the web build. Continuing resumes that game, never a fresh one. A game
that can no longer be loaded ends the run (``GONE``).

**It moves from the stage it last won** (``ways_on``): on along any ungated
lane to a stage it hasn't played, and along a gated lane only when that game's
own score meets the lane's target, never a best. A win with no ungated way on
completes it (a goal, or a bonus stage's dead end).

Reasons and the rest of the plan: ``docs/graphical-gauntlet-maker.md``, Phases 0
and 5.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime

from . import gauntlet, paths, replay, webstore
from .replay import GameLog

# How a stage of a run went.
MET = "met"              # won within its target (or any win, with none): on to the next
MISSED = "missed"        # won, short of the target
LOST = "lost"            # lost, or knocked out
VOID = "void"            # won, but not by the player's own hand (`Ui.can_post`)
GONE = "gone"            # its game could no longer be loaded
ABANDONED = "abandoned"  # given up from the gauntlet list
OUTCOMES = (MET, MISSED, LOST, VOID, GONE, ABANDONED)

ACTIVE, COMPLETE, OVER = "active", "complete", "over"
STATUSES = (ACTIVE, COMPLETE, OVER)

# Finished runs kept, newest first, beside the active one.
KEEP_FINISHED = 20

# A step's log is a file name `replay._game_path` minted; anything else read
# back from storage is not one, and is never turned into a path.
_LOG_NAME = re.compile(r"^game_[0-9]{8}_[0-9]{6}_-?[0-9]+\.json$")


@dataclass(frozen=True)
class Step:
    stage: int
    log: str                  # the game's file name under `replay.GAMES_DIR`
    outcome: str = ""         # blank while the game is undecided
    turns: int | None = None
    lost: int | None = None


@dataclass(frozen=True)
class Run:
    id: str
    gauntlet_id: str
    gauntlet_hash: str
    started_at: str
    steps: tuple[Step, ...] = ()
    status: str = ACTIVE

    @property
    def playing(self) -> Step | None:
        """The step whose game is undecided, if any: always the last."""
        return self.steps[-1] if self.steps and not self.steps[-1].outcome else None

    @property
    def cleared(self) -> int:
        """Stages won within their target, which is also the stage being
        played (or next), counted from 0."""
        return sum(1 for s in self.steps if s.outcome == MET)

    def to_dict(self) -> dict:
        return {"id": self.id, "gauntlet_id": self.gauntlet_id, "gauntlet_hash": self.gauntlet_hash,
                "started_at": self.started_at, "status": self.status,
                "steps": [{k: v for k, v in (("stage", s.stage), ("log", s.log), ("outcome", s.outcome),
                                             ("turns", s.turns), ("lost", s.lost)) if v is not None}
                          for s in self.steps]}

    @classmethod
    def from_dict(cls, data) -> Run | None:
        """Tolerant like ``Gauntlet.from_dict``: None for anything that is not a
        run, and a malformed step drops the steps after it, since a run is only
        as long as its record is whole."""
        if not isinstance(data, dict):
            return None
        rid, gid, ghash, began = (data.get(k) for k in ("id", "gauntlet_id", "gauntlet_hash", "started_at"))
        status = data.get("status")
        if not all(isinstance(v, str) and v for v in (rid, gid, ghash, began)) or status not in STATUSES:
            return None
        steps: list[Step] = []
        raw = data.get("steps")
        for row in raw if isinstance(raw, list) else []:
            step = _step(row)
            if step is None:
                break
            steps.append(step)
        return cls(id=rid, gauntlet_id=gid, gauntlet_hash=ghash, started_at=began,  # type: ignore[arg-type]
                   steps=tuple(steps), status=status)


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _step(row) -> Step | None:
    if not isinstance(row, dict):
        return None
    stage, log, outcome = _int(row.get("stage")), row.get("log"), row.get("outcome", "")
    if stage is None or not isinstance(log, str) or not _LOG_NAME.match(log):
        return None
    if outcome not in OUTCOMES and outcome != "":
        return None
    return Step(stage=stage, log=log, outcome=outcome,
                turns=_int(row.get("turns")), lost=_int(row.get("lost")))


# --------------------------------------------------------------------------- #
# Which gauntlets, and in what order
# --------------------------------------------------------------------------- #
def forks(g: gauntlet.Gauntlet) -> bool:
    """Does a run of ``g`` choose its way: more than one start, or a stage
    reached over ungated lanes with more than one way out, gated or not. A gate
    out of a stage with no other way on (the tutorial's bonus stages) is not a
    choice, since a run ends on the stage before it."""
    main = g.depths()
    return len(g.start) > 1 or any(len(g.onward(sid)) > 1 and g.onward(sid, gated=False) for sid in main)


def offered(g: gauntlet.Gauntlet) -> bool:
    """Can ``g`` be run: one with a goal to end on, and a stage target or a
    fork to make it more than the list."""
    return bool(gauntlet.goals(g)) and (any(s.target is not None for s in g.stages) or forks(g))


def linear(g: gauntlet.Gauntlet) -> list[int] | None:
    """The stages in order when every run of ``g`` plays the same ones: a
    single path with no gated lane to leave it by. Else None, and a run counts
    its stages without a total."""
    return gauntlet.chain(g) if not g.lane_targets else None


def fingerprint(g: gauntlet.Gauntlet) -> str:
    """A hash of what a run of ``g`` plays: its stages' setups and targets, in
    path order, and its lanes once it is not a plain path. A run whose
    gauntlet no longer matches it (a desktop file edited) is over, as an edited
    stage is a new, uncleared one."""
    order = gauntlet.chain(g) or [s.id for s in g.stages]
    body: list = []
    for sid in order:
        s = g.stage(sid)
        if s is None:
            continue
        t = s.target
        body.append([sid, s.settings, None if t is None else [t.turns, t.lost, t.beat]])
    if linear(g) is None:
        body.append([[a, b, (a, b) in g.two_way, _gate(g.lane_targets.get((a, b)))] for a, b in sorted(g.lanes)])
        body.append(sorted(g.start))
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _gate(t: gauntlet.Target | None) -> list | None:
    return None if t is None else [t.turns, t.lost, t.beat]


Way = tuple[int, gauntlet.Target | None]


def ways_out(r: Run, g: gauntlet.Gauntlet, sid: int) -> tuple[Way, ...]:
    """The lanes out of ``sid`` a run could take next, as ``(stage, gate)``,
    ``gate`` None for an ungated lane: every way on to a stage the run hasn't
    already won, in the list's order."""
    won = {s.stage for s in r.steps if s.outcome == MET}
    out = {b: g.lane_targets.get(lane) for a, b, lane in g.ways() if a == sid and b not in won and b != sid}
    return tuple((b, out[b]) for b in sorted(out, key=g.position))


def ends(ways: tuple[Way, ...]) -> bool:
    """Does a win with these ways out complete the run: no ungated way on."""
    return all(gate is not None for _, gate in ways)


def open_ways(ways: tuple[Way, ...], score: tuple[int, int]) -> list[int]:
    """The stages a win scoring ``score`` may go on to: every ungated way, and
    each gated one whose target this score meets."""
    return [b for b, gate in ways if gate is None or gate.met(score)]


def ways_on(r: Run, g: gauntlet.Gauntlet) -> list[int]:
    """The stages the run may play next: a start at first, then the ways out
    of the stage it last won. Empty while a game is undecided and once the run
    is finished."""
    if r.status != ACTIVE or r.playing is not None:
        return []
    if not r.steps:
        return sorted(g.start, key=g.position)
    last = r.steps[-1]
    score = (last.turns if last.turns is not None else 0, last.lost if last.lost is not None else 0)
    return open_ways(ways_out(r, g, last.stage), score)


def stage_to_play(r: Run, g: gauntlet.Gauntlet, pick: int | None = None) -> int | None:
    """The stage the run is on: the one being played, else ``pick`` if it is a
    way on, else the only way on. None at a fork with nothing picked, and once
    the run is finished."""
    if r.status != ACTIVE:
        return None
    if r.playing is not None:
        return r.playing.stage
    ways = ways_on(r, g)
    if pick in ways:
        return pick
    return ways[0] if len(ways) == 1 else None


def position(r: Run, g: gauntlet.Gauntlet) -> tuple[int, int]:
    """``(stage number from 1, stages)`` of the stage the run is on, or the
    last it played once it is finished. ``stages`` is 0 where runs may differ
    in length (``linear``)."""
    order = linear(g)
    if order is None:
        return r.cleared + 1, 0
    return min(r.cleared + 1, len(order)), len(order)


def _of(at: int, total: int) -> str:
    return f"{at}/{total}" if total else str(at)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def runs() -> list[Run]:
    """Every stored run, the active one first, then newest first."""
    try:
        data = json.loads(webstore.get(paths.WEB_RUNS_KEY) or "[]")
    except ValueError:
        return []
    found = [r for r in (Run.from_dict(d) for d in (data if isinstance(data, list) else [])) if r is not None]
    return sorted(found, key=lambda r: r.status != ACTIVE)


def _write(rs: list[Run]) -> None:
    live = [r for r in rs if r.status == ACTIVE][:1]
    done = [r for r in rs if r.status != ACTIVE][:KEEP_FINISHED]
    webstore.set(paths.WEB_RUNS_KEY, json.dumps([r.to_dict() for r in live + done],
                                                separators=(",", ":")))


def save(r: Run) -> Run:
    """Store ``r``, newest first, in place of any earlier copy of it."""
    _write([r] + [x for x in runs() if x.id != r.id])
    return r


def active() -> Run | None:
    return next((r for r in runs() if r.status == ACTIVE), None)


def active_for(g: gauntlet.Gauntlet) -> Run | None:
    """The active run, if it is of ``g``. One whose gauntlet has changed since
    it began is ended here rather than played on."""
    r = active()
    if r is None or r.gauntlet_id != g.id:
        return None
    if r.gauntlet_hash != fingerprint(g):
        _end(r, GONE)
        return None
    return r


def summary(g: gauntlet.Gauntlet, rs: list[Run] | None = None) -> str:
    """The list's line about past runs of ``g``: "Run complete ✓" once one
    has been, else the furthest any reached, else blank."""
    mine = [r for r in (runs() if rs is None else rs)
            if r.gauntlet_id == g.id and r.gauntlet_hash == fingerprint(g) and r.status != ACTIVE]
    if any(r.status == COMPLETE for r in mine):
        return "Run complete ✓"
    if not mine:
        return ""
    best = max(r.cleared for r in mine)
    total = len(linear(g) or ())
    return f"Best run: {_of(best, total)} stage{'s' if total or best != 1 else ''}"


# --------------------------------------------------------------------------- #
# Moving a run on
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start(g: gauntlet.Gauntlet, now: str | None = None) -> Run:
    """Begin a run of ``g``, abandoning any run already under way (there is
    only ever one)."""
    old = active()
    if old is not None:
        abandon(old)
    began = now or _now()
    stamp = re.sub(r"[^0-9]", "", began)
    return save(Run(id=f"{stamp}-{g.id}", gauntlet_id=g.id, gauntlet_hash=fingerprint(g),
                    started_at=began))


def begin(r: Run, stage: int, log: GameLog) -> Run:
    """Record that the game ``log`` is the run's go at ``stage``, and keep the
    log (``keep_log``) so the run can find it again."""
    assert log.path is not None
    keep_log(log)
    return save(replace(r, steps=(*r.steps, Step(stage=stage, log=log.path.name))))


def judge(won: bool, by_hand: bool, met: bool) -> str:
    """A decided game's outcome for the run."""
    if not won:
        return LOST
    if not by_hand:
        return VOID
    return MET if met else MISSED


def finish(run_id: str, log_name: str, outcome: str, turns: int, lost: int,
           last: bool) -> Run | None:
    """Record the run's game ``log_name`` as decided, once; ``last`` when a
    win on its stage completes the run (``ends``). None (and nothing written)
    when the run is not active or that game is not its undecided one: a fork or
    a Retry of a run's stage is free play."""
    r = next((x for x in runs() if x.id == run_id), None)
    if r is None or r.status != ACTIVE or r.playing is None or r.playing.log != log_name:
        return None
    step = replace(r.playing, outcome=outcome, turns=turns, lost=lost)
    r = replace(r, steps=(*r.steps[:-1], step))
    if outcome != MET:
        r = replace(r, status=OVER)
    elif last:
        r = replace(r, status=COMPLETE)
    if r.status != ACTIVE:
        webstore.set(paths.WEB_RUN_LOG_KEY, "")
    return save(r)


def _end(r: Run, outcome: str) -> Run:
    """End ``r`` now: its undecided game, if any, gets ``outcome``."""
    steps = r.steps
    if r.playing is not None:
        steps = (*steps[:-1], replace(r.playing, outcome=outcome))
    webstore.set(paths.WEB_RUN_LOG_KEY, "")
    return save(replace(r, steps=steps, status=OVER))


def abandon(r: Run) -> Run:
    return _end(r, ABANDONED)


def lose_game(r: Run) -> Run:
    """The run's game could not be loaded again: the run is over."""
    return _end(r, GONE)


def note(outcome: str, at: int, total: int, target: gauntlet.Target | None,
         status: str = ACTIVE, ways: int = 1) -> tuple[str, bool]:
    """The win overlay's line about the run once its game is decided, and
    whether it is good news. ``status`` is the run's after it, ``ways`` how
    many stages it may go on to."""
    if outcome == MET:
        if status == COMPLETE:
            return (f"Run complete: all {total} stages" if total
                    else f"Run complete: {at} stage{'s' if at != 1 else ''}"), True
        if ways > 1:
            return f"Run: stage {_of(at, total)} done · choose the way on in the gauntlet list", True
        return f"Run: stage {_of(at, total)} done · Next stage carries it on", True
    why = {MISSED: f": {target.text()} not met" if target is not None else "",
           VOID: ": not played by hand"}.get(outcome, "")
    return f"Run over at stage {_of(at, total)}{why}", False


# --------------------------------------------------------------------------- #
# The run's game
# --------------------------------------------------------------------------- #
def keep_log(log: GameLog) -> None:
    """Keep the run's game in ``webstore``, rewritten every turn, so it outlasts
    a reload on the web build, where ``games/`` does not."""
    if log.path is None:
        return
    webstore.set(paths.WEB_RUN_LOG_KEY, json.dumps({"name": log.path.name, "log": log.to_dict()},
                                                   separators=(",", ":")))


def restore_log(name: str) -> GameLog | None:
    """The run's game ``name``, from the kept copy or else its file, or None
    if neither loads under today's rules (`GameLog.is_current`)."""
    if not _LOG_NAME.match(name):
        return None
    path = replay.GAMES_DIR / name
    log = None
    try:
        kept = json.loads(webstore.get(paths.WEB_RUN_LOG_KEY) or "{}")
        if isinstance(kept, dict) and kept.get("name") == name and isinstance(kept.get("log"), dict):
            log = GameLog.from_dict(kept["log"], path=path)
    except (ValueError, TypeError, KeyError):
        log = None
    if log is None:
        try:
            log = replay.load(path)
        except (OSError, ValueError, TypeError, KeyError):
            return None
    return log if log.is_current else None


def is_playing(log: GameLog | None) -> bool:
    """Is ``log`` the active run's undecided game (the boot resume prompt's
    question)."""
    r = active()
    return (log is not None and log.path is not None and r is not None
            and r.playing is not None and r.playing.log == log.path.name)
