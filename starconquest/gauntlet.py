"""A gauntlet: an ordered string of pinned setups, each with a briefing and a
hint — pure core, no pygame.

The tutorial is one (``assets/gauntlets/tutorial.json``) and nothing about it is
special code: a gauntlet is a *file*, and a player can write their own in the
same format (``data_dir()/gauntlets/*.json``, desktop only). Reasons, the file
format and the branching run this shape is meant to carry later:
``docs/design/gauntlet.md``.

Three rules this module exists to keep:

**A gauntlet is data, never `Settings`.** A stage *is* its setup, identified by
``Settings.challenge_key()``. Nothing here adds a ``Settings`` field, so no
challenge key moves and a stage won posts to the leaderboard as the ordinary
game it is.

**Progress is derived, never stored.** A stage is cleared when ``webstore.best``
holds a by-hand win on its exact setup — the same gate as posting
(``Ui.can_post``). There is no progress key: a stage whose setup is edited in a
later release is a new, uncleared stage, which is the honest answer.

**The graph is the campaign's shape.** ``stages`` + ``lanes`` + ``start``, as
the weekly campaign's ``nodes`` + ``lanes`` are, so a branching gauntlet needs no
format change. The bordering rule is mirrored in spirit — a stage is open if it
is a start, is cleared, or borders a cleared one — over local bests, because a
gauntlet is uncontested: nobody can take a cleared stage back. The campaign's
own rule stays in JS behind ``/api/campaign`` and is not reimplemented here.

``from_dict`` is total and never raises, like ``CustomMap.from_dict``: it drops
a malformed stage or lane and clamps text to what the screen can show.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, webstore
from .settings import Settings

# Package-relative, like `uifont._ASSETS`, so the web build ships the files with
# the package (`tools/build_web.sh` copies `starconquest/` whole).
_ASSETS = Path(__file__).resolve().parent / "assets" / "gauntlets"

# What the screen has room for. The hint is one line under the side panel's
# heading; `test_a_longest_hint_fits_the_empty_panel` holds it. The briefing is a
# card over the board on turn 0, `BRIEFING_MAX_LINES` paragraphs at most.
HINT_MAX = 90
TITLE_MAX = 32
NAME_MAX = 32
BRIEFING_MAX_LINES = 6
BRIEFING_LINE_MAX = 240
MAX_STAGES = 64


@dataclass(frozen=True)
class Stage:
    """One setup in a gauntlet. ``settings`` is exactly what the menu's Save
    writes (``Settings.save``), so an author builds a setup in the menu, saves it
    and pastes it in."""

    id: int
    title: str
    briefing: tuple[str, ...]
    hint: str
    settings: dict = field(compare=False)


@dataclass(frozen=True)
class Gauntlet:
    id: str
    name: str
    stages: tuple[Stage, ...]
    lanes: tuple[tuple[int, int], ...]
    start: tuple[int, ...]

    @classmethod
    def from_dict(cls, data) -> Gauntlet | None:
        """Parse a gauntlet file's dict, tolerantly. Never raises; ``None`` if
        nothing playable is left.

        A malformed stage (no int ``id``, a duplicate one, no ``settings`` dict)
        is dropped, and a lane to a missing stage with it. With no ``lanes`` the
        stages are joined in list order, so a hand-written linear gauntlet can
        leave them out; with no usable ``start`` the first stage is the start.
        """
        try:
            return _from_dict(data)
        except Exception:
            return None

    def stage(self, sid: int) -> Stage | None:
        return next((s for s in self.stages if s.id == sid), None)

    def position(self, sid: int) -> int:
        """1-based place of stage ``sid`` in the list — what "3/11" counts."""
        for i, s in enumerate(self.stages):
            if s.id == sid:
                return i + 1
        return 0

    def neighbours(self, sid: int) -> set[int]:
        out = set()
        for a, b in self.lanes:
            if a == sid:
                out.add(b)
            elif b == sid:
                out.add(a)
        return out

    def depths(self) -> dict[int, int]:
        """Lanes from the nearest start, per stage (unreachable ones absent)."""
        depth = {s: 0 for s in self.start}
        queue = deque(self.start)
        while queue:
            at = queue.popleft()
            for n in sorted(self.neighbours(at)):
                if n not in depth:
                    depth[n] = depth[at] + 1
                    queue.append(n)
        return depth


def _text(value, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _from_dict(data) -> Gauntlet | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("stages")
    if not isinstance(raw, list):
        return None
    stages: list[Stage] = []
    for row in raw[:MAX_STAGES]:
        if not isinstance(row, dict):
            continue
        sid = _int(row.get("id"))
        setup = row.get("settings")
        if sid is None or not isinstance(setup, dict) or any(s.id == sid for s in stages):
            continue
        briefing = row.get("briefing")
        lines = briefing if isinstance(briefing, list) else [briefing]
        stages.append(Stage(
            id=sid,
            title=_text(row.get("title"), TITLE_MAX) or f"Stage {len(stages) + 1}",
            briefing=tuple(t for t in (_text(x, BRIEFING_LINE_MAX) for x in lines) if t)[:BRIEFING_MAX_LINES],
            hint=_text(row.get("hint"), HINT_MAX),
            settings=setup,
        ))
    if not stages:
        return None
    ids = {s.id for s in stages}

    raw_lanes = data.get("lanes")
    lanes: list[tuple[int, int]] = []
    if isinstance(raw_lanes, list):
        for pair in raw_lanes:
            if not isinstance(pair, list | tuple) or len(pair) != 2:
                continue
            a, b = _int(pair[0]), _int(pair[1])
            if a in ids and b in ids and a != b and (a, b) not in lanes and (b, a) not in lanes:
                lanes.append((a, b))     # type: ignore[arg-type]
    else:
        lanes = [(x.id, y.id) for x, y in zip(stages, stages[1:])]

    raw_start = data.get("start")
    start = [s for s in (raw_start if isinstance(raw_start, list) else []) if _int(s) in ids]
    if not start:
        start = [stages[0].id]
    gid = _text(data.get("id"), NAME_MAX) or "gauntlet"
    return Gauntlet(id=gid, name=_text(data.get("name"), NAME_MAX) or gid,
                    stages=tuple(stages), lanes=tuple(lanes), start=tuple(dict.fromkeys(start)))


def stage_settings(stage: Stage) -> Settings:
    """The setup to play. A stage never carries a score to beat, and is never a
    demo: the point is to play it."""
    out = Settings.from_dict(stage.settings)
    out.challenge = None
    out.autoplay = False
    return out


def stage_key(stage: Stage) -> str:
    return stage_settings(stage).challenge_key()


def best(stage: Stage) -> tuple[int, int] | None:
    """Your best ``(turns, lost)`` on this stage, or None before a by-hand win."""
    return webstore.best(*stage_settings(stage).challenge_keys())


def bests(g: Gauntlet) -> dict[int, tuple[int, int]]:
    """Every cleared stage's best, by stage id."""
    out = {}
    for s in g.stages:
        mine = best(s)
        if mine is not None:
            out[s.id] = mine
    return out


def cleared(g: Gauntlet) -> set[int]:
    return set(bests(g))


def open_stages(g: Gauntlet, done: set[int] | None = None) -> set[int]:
    """The starts, every cleared stage (cleared stays replayable) and every
    neighbour of one. Everything else is locked. On a path this is "the next one"."""
    done = cleared(g) if done is None else done
    out = set(g.start) | done
    for sid in done:
        out |= g.neighbours(sid)
    return out


def next_stage(g: Gauntlet, sid: int) -> int | None:
    """Where a win on ``sid`` leads: its one neighbour further from the start.

    None at the end, and None at a fork — more than one way on sends the player
    to the list to choose. Judged by depth rather than by what is still uncleared,
    so replaying a stage you have already cleared still offers the next one.
    """
    depth = g.depths()
    if sid not in depth:
        return None
    deeper = [n for n in g.neighbours(sid) if depth.get(n, -1) > depth[sid]]
    return deeper[0] if len(deeper) == 1 else None


def stage_of(g: Gauntlet, settings: Settings) -> int | None:
    """The stage ``settings`` still is, by challenge key, or None once any knob
    has moved — the way ``Challenge.matches`` judges a link."""
    keys = settings.challenge_keys()
    return next((s.id for s in g.stages if stage_key(s) in keys), None)


def _load_dir(where: Path) -> list[Gauntlet]:
    out = []
    try:
        files = sorted(where.glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            g = Gauntlet.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if g is not None:
            out.append(g)
    return out


_BUILTIN: list[Gauntlet] | None = None


def builtin() -> list[Gauntlet]:
    """The gauntlets that ship with the game, the tutorial first."""
    global _BUILTIN
    if _BUILTIN is None:
        found = _load_dir(_ASSETS)
        found.sort(key=lambda g: g.id != "tutorial")
        _BUILTIN = found
    return list(_BUILTIN)


def user_dir() -> Path:
    return paths.data_dir() / "gauntlets"


def user() -> list[Gauntlet]:
    """A player's own gauntlets. Desktop only: the web build's files live in
    MEMFS, which does not survive a reload. Read fresh each call, so a file
    dropped in while the game runs shows the next time the list opens."""
    if paths.is_web():
        return []
    taken = {g.id for g in builtin()}
    return [g for g in _load_dir(user_dir()) if g.id not in taken]


def every() -> list[Gauntlet]:
    return builtin() + user()


def find(gauntlets: list[Gauntlet], gid: str) -> Gauntlet | None:
    return next((g for g in gauntlets if g.id == gid), None)
