"""A run of a gauntlet: from a start to a goal, each stage won within its
target, on the lives the gauntlet gives — pure core, no pygame.

Offered only where a run adds something to the stage list (``offered``): a
target to meet, or a fork, where the way chosen matters because losing on it
costs a life. On the tutorial, a single path with no stage target, a run would
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

**It may play any stage it has unlocked** (``frontier``): a start, a stage an
ungated lane leads to from a stage it won, or one a gated lane leads to from a
stage it won with a game whose own score met the gate, never a best. A stage is
behind the run, and leaves the frontier, once a stage it leads to is won; a won
stage with a gate out still unmet stays, to be replayed for the gate. A loss
costs a life and keeps the stage; only the last life (or a lost or abandoned
game) ends the run. Winning a goal (``gauntlet.goals``) completes it, and
winning a stage that grants a bonus life (``gauntlet.grants_life``) the first
time adds one. The lives left are derived from the steps, never stored.

Reasons: ``docs/design/gauntlet.md``, *Runs*.
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

# The outcomes that cost a life. GONE and ABANDONED end the run outright.
COSTS = (MISSED, LOST, VOID)

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
    # The lives it started with, copied from the gauntlet when it began, so an
    # edit to the gauntlet never changes a run under way. A record without it
    # (one begun before runs had lives) reads as 1.
    lives: int = 1

    @property
    def playing(self) -> Step | None:
        """The step whose game is undecided, if any: always the last."""
        return self.steps[-1] if self.steps and not self.steps[-1].outcome else None

    @property
    def won(self) -> set[int]:
        """The stages it has won within their target."""
        return {s.stage for s in self.steps if s.outcome == MET}

    @property
    def cleared(self) -> int:
        """Stages won within their target, which is also the stage being
        played (or next), counted from 0. A replay for a gate wins no stage."""
        return len(self.won)

    def to_dict(self) -> dict:
        out = {"id": self.id, "gauntlet_id": self.gauntlet_id, "gauntlet_hash": self.gauntlet_hash,
               "started_at": self.started_at, "status": self.status,
               "steps": [{k: v for k, v in (("stage", s.stage), ("log", s.log), ("outcome", s.outcome),
                                            ("turns", s.turns), ("lost", s.lost)) if v is not None}
                         for s in self.steps]}
        if self.lives != 1:
            out["lives"] = self.lives
        return out

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
        lives = _int(data.get("lives"))
        return cls(id=rid, gauntlet_id=gid, gauntlet_hash=ghash, started_at=began,  # type: ignore[arg-type]
                   steps=tuple(steps), status=status,
                   lives=1 if lives is None else max(1, min(gauntlet.LIVES_MAX, lives)))


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
    path order, its lanes once it is not a plain path, and the stages that
    grant a life once there are any. The gauntlet's ``lives`` stays out: the run
    keeps its own copy. A run whose
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
    bonus = sorted(s.id for s in g.stages if gauntlet.grants_life(g, s.id))
    if bonus:
        body.append(["bonus", bonus])
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _gate(t: gauntlet.Target | None) -> list | None:
    return None if t is None else [t.turns, t.lost, t.beat]


def _score(step: Step) -> tuple[int, int]:
    return (step.turns if step.turns is not None else 0, step.lost if step.lost is not None else 0)


def unlocked(r: Run, g: gauntlet.Gauntlet) -> set[int]:
    """Every stage the run has opened: the starts, each stage an ungated lane
    leads to from a stage it won, and each a gated lane leads to from a stage
    it won with a game whose own score met the gate. A best never counts."""
    out = set(g.start)
    for step in r.steps:
        if step.outcome != MET:
            continue
        for a, b, lane in g.ways():
            gate = g.lane_targets.get(lane)
            if a == step.stage and (gate is None or gate.met(_score(step))):
                out.add(b)
    return out


def behind(g: gauntlet.Gauntlet, sid: int, won: set[int]) -> bool:
    """Has the run gone past ``sid``: a one-way lane leads from it to a stage
    won. A two-way lane leads back as well, so it never puts a stage behind."""
    return any(a == sid and b in won and lane not in g.two_way for a, b, lane in g.ways())


def spent(g: gauntlet.Gauntlet, sid: int, won: set[int], opened: set[int]) -> bool:
    """Would winning ``sid`` give the run nothing: it leads somewhere, is no
    goal and grants no life, and every stage it leads to is open, won or gone
    past already. In a cross-stitch, the other stage of a pair once the one
    beside it is won, since that opened both stages after them."""
    if sid in gauntlet.goals(g) or gauntlet.grants_life(g, sid):
        return False
    ahead = g.onward(sid)
    return bool(ahead) and all(b in opened or b in won or behind(g, b, won) for b in ahead)


def gates_left(r: Run, g: gauntlet.Gauntlet, sid: int) -> list[gauntlet.Target]:
    """The gates out of ``sid`` a replay of it could still meet: each gated
    lane to a stage the run has neither opened nor gone past, and that would
    not be spent once opened. A replay of a won stage is judged against these,
    not the stage's own target."""
    won, opened = r.won, unlocked(r, g)
    return [g.lane_targets[lane] for a, b, lane in g.ways()
            if a == sid and lane in g.lane_targets and b not in opened and b not in won
            and not behind(g, b, won) and not spent(g, b, won, opened)]


def frontier(r: Run, g: gauntlet.Gauntlet) -> list[int]:
    """The stages the run may play next, in the list's order: every stage it
    has opened and neither won, gone past nor spent, and every won stage with
    a gate out it may still meet. A dead end leads nowhere, so it is never
    behind or spent and stays until it is played. Empty while a game is
    undecided and once the run is finished."""
    if r.status != ACTIVE or r.playing is not None:
        return []
    won, opened = r.won, unlocked(r, g)
    out = {sid for sid in opened
           if sid not in won and not behind(g, sid, won) and not spent(g, sid, won, opened)}
    out |= {sid for sid in won if gates_left(r, g, sid)}
    return sorted((sid for sid in out if g.stage(sid) is not None), key=g.position)


def next_up(r: Run, g: gauntlet.Gauntlet) -> int | None:
    """The one stage the run hasn't won on its frontier, if there is just one:
    where Next stage goes. A won stage kept for its gate is a replay, never
    the next stage."""
    fresh = [sid for sid in frontier(r, g) if sid not in r.won]
    return fresh[0] if len(fresh) == 1 else None


def stage_to_play(r: Run, g: gauntlet.Gauntlet, pick: int | None = None) -> int | None:
    """The stage the run is on: the one being played, else ``pick`` if it is on
    the frontier, else the only stage there, else the only one not yet won
    (``next_up``). None where the list must choose, and once the run is
    finished."""
    if r.status != ACTIVE:
        return None
    if r.playing is not None:
        return r.playing.stage
    front = frontier(r, g)
    if pick in front:
        return pick
    return front[0] if len(front) == 1 else next_up(r, g)


def lives_left(r: Run, g: gauntlet.Gauntlet) -> int:
    """The lives the run has left, derived from its steps: those it began
    with, one more for each first win of a stage that grants one, one fewer
    for each game that cost one (``COSTS``)."""
    left, seen = r.lives, set()
    for step in r.steps:
        if step.outcome == MET and step.stage not in seen:
            seen.add(step.stage)
            left += gauntlet.grants_life(g, step.stage)
        elif step.outcome in COSTS:
            left -= 1
    return left


def counts_lives(r: Run, g: gauntlet.Gauntlet) -> bool:
    """Are lives worth showing for this run: it began with more than one, or
    a stage may grant one. Else it is a one-life run, said as one."""
    return r.lives > 1 or any(gauntlet.grants_life(g, s.id) for s in g.stages)


def hearts(n: int) -> str:
    """Lives as hearts, "♥♥♥", or a count past the most a run can start with."""
    return "♥" * n if 0 < n <= gauntlet.LIVES_MAX else f"♥×{n}"


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
                    started_at=began, lives=g.lives))


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
           g: gauntlet.Gauntlet) -> Run | None:
    """Record the run's game ``log_name`` as decided, once. A replay of a stage
    already won is judged against the gates still left out of it
    (``gates_left``): meeting one is MET, any other win MISSED. A game that
    costs a life ends the run only on its last life, and a win on a goal of
    ``g`` completes it. None (and nothing written) when the run is not active or
    that game is not its undecided one: a fork or a Retry of a run's stage is
    free play."""
    r = next((x for x in runs() if x.id == run_id), None)
    if r is None or r.status != ACTIVE or r.playing is None or r.playing.log != log_name:
        return None
    sid = r.playing.stage
    if sid in r.won and outcome in (MET, MISSED):
        outcome = MET if any(gate.met((turns, lost)) for gate in gates_left(r, g, sid)) else MISSED
    step = replace(r.playing, outcome=outcome, turns=turns, lost=lost)
    r = replace(r, steps=(*r.steps[:-1], step))
    if outcome in COSTS and lives_left(r, g) <= 0:
        r = replace(r, status=OVER)
    elif outcome == MET and sid in gauntlet.goals(g):
        r = replace(r, status=COMPLETE)
    elif not frontier(r, g):
        r = replace(r, status=OVER)    # nowhere left to go: never so on a gauntlet with a goal
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


def note(r: Run, g: gauntlet.Gauntlet, at: int, total: int,
         target: gauntlet.Target | None) -> tuple[str, bool]:
    """The win overlay's line about the run once its last game is decided, and
    whether it is good news. ``at``/``total`` are the stage's place in the run
    as the game began (``position``), ``target`` the stage's own."""
    step = r.steps[-1]
    outcome, replay = step.outcome, step.stage in {s.stage for s in r.steps[:-1] if s.outcome == MET}
    left = lives_left(r, g)
    lives = f" · {hearts(left)} left" if counts_lives(r, g) and r.status == ACTIVE else ""
    if outcome == MET:
        if r.status == COMPLETE:
            return (f"Run complete: all {total} stages" if total
                    else f"Run complete: {at} stage{'s' if at != 1 else ''}"), True
        if r.status == OVER:
            return "Run over: nowhere left to go", False
        done = "gate met" if replay else f"stage {_of(at, total)} done"
        if gauntlet.grants_life(g, step.stage) and not replay:
            done += " · bonus life"
        on = " · Next stage carries it on" if next_up(r, g) is not None else " · choose the way on in the gauntlet list"
        return f"Run: {done}{lives}{on}", True
    if replay and outcome == MISSED:
        why = ": gate not met"
    else:
        why = {MISSED: f": {target.text()} not met" if target is not None else "",
               VOID: ": not played by hand", LOST: ": lost"}.get(outcome, "")
    if r.status == ACTIVE:
        return f"Run{why}{lives}", False
    if not counts_lives(r, g):
        return f"Run over at stage {_of(at, total)}{why}", False
    won = r.cleared
    return f"Run over: no lives left · {won} stage{'s' if won != 1 else ''} won", False


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
