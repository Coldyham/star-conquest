"""A gauntlet: an ordered string of pinned setups, each with a briefing and a
hint — pure core, no pygame.

The tutorial is one (``assets/gauntlets/tutorial.json``) and nothing about it is
special code: a gauntlet is a *file*. A player can write their own in the same
format (``data_dir()/gauntlets/*.json``, desktop), or build one on the board's
gauntlet page, which stores it and hands out a ``#g=<id>`` link; the game fetches
that once and keeps the file (``remember``). Reasons, the file format, storage
and the branching run this shape is meant to carry later:
``docs/design/gauntlet.md``.

Three rules this module exists to keep:

**A gauntlet is data, never `Settings`.** A stage *is* its setup, identified by
``Settings.challenge_key()``. Nothing here adds a ``Settings`` field, so no
challenge key moves and a stage won posts to the leaderboard as the ordinary
game it is.

**Progress is derived, never stored.** A stage is cleared when ``webstore.best``
holds a by-hand win on its exact setup — the same gate as posting
(``Ui.can_post``) — that meets the stage's target, if it has one (``Target``).
There is no progress key: a stage whose setup is edited in a later release is a
new, uncleared stage, and a target added or tightened later is checked against
every best again, which are the honest answers.

**The graph is the campaign's shape.** ``stages`` + ``lanes`` + ``start``, as
the weekly campaign's ``nodes`` + ``lanes`` are, so a branching gauntlet needs no
format change. The bordering rule is mirrored in spirit — a stage is open if it
is a start, is cleared, or a lane leads to it from a cleared one — over local
bests, because a gauntlet is uncontested: nobody can take a cleared stage back.
Unlike the campaign's, a lane runs one way, from its first stage to its second,
unless the file marks it two-way. The campaign's own rule stays in JS behind
``/api/campaign`` and is not reimplemented here.

``from_dict`` is total and never raises, like ``CustomMap.from_dict``: it drops
a malformed stage or lane and clamps text to what the screen can show.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, pbp, webstore
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
BOT_MAX = 24


@dataclass(frozen=True)
class Target:
    """A score a stage must be won within before the stages beyond it open:
    gauntlet data, never `Settings`, so the stage stays the ordinary map it is.

    Scored as a best is, turns first and ships lost breaking a tie; with no
    ``lost`` only turns count. An author's target is met (at or under it); one
    copied off the board's bot column is beaten (``beat``: strictly under it),
    and ``bot`` names that bot, for showing only."""

    turns: int
    lost: int | None = None
    bot: str = ""
    beat: bool = False

    def met(self, score: tuple[int, int]) -> bool:
        mine = score if self.lost is not None else score[:1]
        want = (self.turns, self.lost) if self.lost is not None else (self.turns,)
        return mine < want if self.beat else mine <= want

    def score_text(self) -> str:
        """"14 turns", or "14 turns / 30 lost"."""
        return f"{self.turns} turns" + (f" / {self.lost} lost" if self.lost is not None else "")

    def text(self) -> str:
        """The whole condition, for the briefing card and the win overlay:
        "Win in 14 turns or fewer", "Beat thinker: 31 turns / 12 lost"."""
        if self.beat:
            return f"Beat {self.bot or 'the target'}: {self.score_text()}"
        if self.lost is None:
            return f"Win in {self.turns} turns or fewer"
        return f"Win within {self.score_text()}"

    def short(self) -> str:
        """For a tight row (the stage list, the top bar): "target 14"."""
        return ("beat " if self.beat else "target ") + (
            f"{self.turns}/{self.lost}" if self.lost is not None else str(self.turns))


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
    target: Target | None = None


@dataclass(frozen=True)
class Gauntlet:
    id: str
    name: str
    stages: tuple[Stage, ...]
    lanes: tuple[tuple[int, int], ...]
    start: tuple[int, ...]
    # The lanes that also run from their second stage back to their first.
    two_way: frozenset[tuple[int, int]] = frozenset()

    @classmethod
    def from_dict(cls, data) -> Gauntlet | None:
        """Parse a gauntlet file's dict, tolerantly. Never raises; ``None`` if
        nothing playable is left.

        A malformed stage (no int ``id``, a duplicate one, no ``settings`` dict)
        is dropped, and a lane to a missing stage with it. With no ``lanes`` the
        stages are joined in list order, so a hand-written linear gauntlet can
        leave them out; with no usable ``start`` the first stage is the start.
        A lane's optional third element is read only for ``two_way``; anything
        else there leaves it one-way. A malformed ``target`` is dropped, and the
        stage then has none.
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

    def onward(self, sid: int) -> set[int]:
        """The stages a lane leads to from ``sid``, the way each lane runs."""
        out = set()
        for a, b in self.lanes:
            if a == sid:
                out.add(b)
            elif b == sid and (a, b) in self.two_way:
                out.add(a)
        return out

    def depths(self) -> dict[int, int]:
        """Lanes from the nearest start, per stage, the way lanes run. A stage
        no start reaches is absent (``problems`` names it)."""
        depth = {s: 0 for s in self.start}
        queue = deque(self.start)
        while queue:
            at = queue.popleft()
            for n in sorted(self.onward(at)):
                if n not in depth:
                    depth[n] = depth[at] + 1
                    queue.append(n)
        return depth


def _text(value, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _target(raw) -> Target | None:
    """A stage's ``target`` field, or None for anything that is not one: turns a
    positive int, lost (optional) an int from 0, beat only ``true``."""
    if not isinstance(raw, dict):
        return None
    turns, lost = _int(raw.get("turns")), _int(raw.get("lost"))
    if turns is None or turns < 1 or ("lost" in raw and (lost is None or lost < 0)):
        return None
    return Target(turns=turns, lost=lost, bot=_text(raw.get("bot"), BOT_MAX),
                  beat=raw.get("beat") is True)


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
            target=_target(row.get("target")),
        ))
    if not stages:
        return None
    ids = {s.id for s in stages}

    raw_lanes = data.get("lanes")
    lanes: list[tuple[int, int]] = []
    two_way: set[tuple[int, int]] = set()
    if isinstance(raw_lanes, list):
        for pair in raw_lanes:
            if not isinstance(pair, list | tuple) or len(pair) not in (2, 3):
                continue
            a, b = _int(pair[0]), _int(pair[1])
            if a in ids and b in ids and a != b and (a, b) not in lanes and (b, a) not in lanes:
                lanes.append((a, b))     # type: ignore[arg-type]
                if len(pair) == 3 and isinstance(pair[2], dict) and pair[2].get("two_way") is True:
                    two_way.add((a, b))     # type: ignore[arg-type]
    else:
        lanes = [(x.id, y.id) for x, y in zip(stages, stages[1:])]

    raw_start = data.get("start")
    start = [s for s in (raw_start if isinstance(raw_start, list) else []) if _int(s) in ids]
    if not start:
        start = [stages[0].id]
    gid = _text(data.get("id"), NAME_MAX) or "gauntlet"
    return Gauntlet(id=gid, name=_text(data.get("name"), NAME_MAX) or gid,
                    stages=tuple(stages), lanes=tuple(lanes), start=tuple(dict.fromkeys(start)),
                    two_way=frozenset(two_way))


def problems(g: Gauntlet) -> list[str]:
    """What stops a player getting through ``g``: each stage no start can reach
    following the lanes the way they run. Empty when every stage can be played.
    ``from_dict`` keeps such a gauntlet (the stage just never opens); this is the
    check to run before one is shipped or stored, mirrored by the board's
    ``checkBody``."""
    depth = g.depths()
    return [f"stage {g.position(s.id)} ({s.title}) can't be reached from a start"
            for s in g.stages if s.id not in depth]


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


def met(stage: Stage, score: tuple[int, int] | None) -> bool:
    """Does ``score`` clear ``stage``: any win when it has no target, else one
    that meets the target."""
    return score is not None and (stage.target is None or stage.target.met(score))


def cleared(g: Gauntlet, found: dict[int, tuple[int, int]] | None = None) -> set[int]:
    """The stages whose best meets their target, from ``found`` (``bests``) when
    it is in hand. A win short of a target leaves the stage open and replayable,
    but not cleared, so nothing beyond it opens."""
    found = bests(g) if found is None else found
    return {s.id for s in g.stages if met(s, found.get(s.id))}


def open_stages(g: Gauntlet, done: set[int] | None = None,
                won: set[int] | None = None) -> set[int]:
    """The starts, every cleared stage (cleared stays replayable), every ``won``
    one short of its target (to try again) and every stage a lane leads to from
    a cleared one. Everything else is locked. On a path this is "the next one",
    and never the one before: clearing a later stage first (from a challenge
    link of the same setup, say) does not open the earlier ones."""
    if done is None:
        found = bests(g)
        done, won = cleared(g, found), set(found)
    out = set(g.start) | done | (won or set())
    for sid in done:
        out |= g.onward(sid)
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
    deeper = [n for n in g.onward(sid) if depth.get(n, -1) > depth[sid]]
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
# The built-in tutorial's id: first in the list, and the one the menu calls
# "the tutorial" (any other is "a gauntlet").
TUTORIAL_ID = "tutorial"


def builtin() -> list[Gauntlet]:
    """The gauntlets that ship with the game, the tutorial first."""
    global _BUILTIN
    if _BUILTIN is None:
        found = _load_dir(_ASSETS)
        found.sort(key=lambda g: g.id != TUTORIAL_ID)
        _BUILTIN = found
    return list(_BUILTIN)


def user_dir() -> Path:
    return paths.data_dir() / "gauntlets"


def user() -> list[Gauntlet]:
    """A player's own gauntlets: the ones opened from a link (``remembered``),
    newest first, then on desktop the files in ``user_dir()``. The web build has
    no folder to read, its files living in MEMFS, which does not survive a
    reload. Read fresh each call, so a file dropped in while the game runs shows
    the next time the list opens. A built-in's id is never shadowed."""
    taken = {g.id for g in builtin()}
    out: list[Gauntlet] = []
    for g in remembered() + ([] if paths.is_web() else _load_dir(user_dir())):
        if g.id not in taken:
            taken.add(g.id)
            out.append(g)
    return out


def every() -> list[Gauntlet]:
    return builtin() + user()


def find(gauntlets: list[Gauntlet], gid: str) -> Gauntlet | None:
    return next((g for g in gauntlets if g.id == gid), None)


# --------------------------------------------------------------------------- #
# Gauntlets from a link
# --------------------------------------------------------------------------- #
# A gauntlet built on the board's gauntlet page is stored by
# `leaderboard/netlify/functions/gauntlet.mjs` and opened with `#g=<id>`; the id
# is a name slug plus a hash of the file, minted there. `#gauntlet=<payload>`
# carries the whole file instead (deflated JSON, base64url, as a settings token
# is), for a build with no board to ask and for tests. Either way the file is
# kept (`remember`), so the Tutorial list still has it after a relaunch without
# the fragment, and offline. Rows never change, so a kept copy never goes stale.
LINK_FRAGMENT = "g="
INLINE_FRAGMENT = "gauntlet="
# `gauntlets.gauntlet_id`'s check in schema.sql (and gauntlet-build.mjs).
_ID_RE = re.compile(r"^[a-z0-9-]{1,19}-[0-9a-f]{12}$")
# How many opened gauntlets the store keeps. Up to 64 KiB each, so a few hundred
# KiB at most, well inside a browser's localStorage.
REMEMBER_MAX = 8


def link_id(fragment: str) -> str | None:
    """The stored id a ``#g=<id>`` fragment names, or None for anything else."""
    if not fragment.startswith(LINK_FRAGMENT):
        return None
    gid = fragment[len(LINK_FRAGMENT):].strip()
    return gid if _ID_RE.match(gid) else None


def inline_data(fragment: str) -> dict | None:
    """The gauntlet file a ``#gauntlet=<payload>`` fragment carries, or None.
    Tolerant like ``Settings.from_token``: plain JSON or deflated, and anything
    that will not decode to a gauntlet is simply not one."""
    if not fragment.startswith(INLINE_FRAGMENT):
        return None
    token = fragment[len(INLINE_FRAGMENT):].strip()
    try:
        raw = base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode("ascii"))
        if raw[:1] != b"{":
            raw = zlib.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError, zlib.error):
        return None
    return data if Gauntlet.from_dict(data) is not None else None


def link_fragment(data: dict) -> str:
    """``data`` as an inline ``#gauntlet=`` fragment (without the ``#``)."""
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return INLINE_FRAGMENT + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def _kept() -> list[dict]:
    try:
        data = json.loads(webstore.get(paths.WEB_GAUNTLETS_KEY) or "[]")
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def kept(gid: str) -> dict | None:
    """The file of gauntlet ``gid`` as it was kept (``remember``), or None: what
    a Copy link sends on, the whole file in a ``#gauntlet=`` link."""
    return next((d for d in _kept() if _text(d.get("id"), NAME_MAX) == gid), None)


def remembered() -> list[Gauntlet]:
    """The gauntlets opened from links, newest first."""
    return [g for g in (Gauntlet.from_dict(d) for d in _kept()) if g is not None]


def remember(data: dict) -> Gauntlet | None:
    """Keep a gauntlet file opened from a link, newest first, and answer it as a
    ``Gauntlet`` (None, and nothing kept, if it is not one). Opening one already
    kept moves it to the front."""
    g = Gauntlet.from_dict(data)
    if g is None:
        return None
    kept = [d for d in _kept() if _text(d.get("id"), NAME_MAX) != g.id]
    webstore.set(paths.WEB_GAUNTLETS_KEY, json.dumps([data] + kept[:REMEMBER_MAX - 1],
                                                     separators=(",", ":")))
    return g


def fetch(gid: str) -> pbp.Request | None:
    """Start fetching a stored gauntlet. None when there is no board to ask."""
    endpoint = webstore.leaderboard_url(paths.LEADERBOARD_GAUNTLET_PATH)
    if not endpoint or not _ID_RE.match(gid):
        return None
    return pbp.request(f"{endpoint}?id={gid}")


def from_reply(body: str) -> dict | None:
    """The gauntlet file in ``/api/gauntlet``'s answer, or None."""
    try:
        reply = json.loads(body)
    except ValueError:
        return None
    data = reply.get("gauntlet") if isinstance(reply, dict) else None
    return data if isinstance(data, dict) and Gauntlet.from_dict(data) is not None else None
