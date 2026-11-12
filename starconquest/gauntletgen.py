"""Generate a gauntlet from a spec: deal candidates, probe them, pick a rising
sequence — pure core, no pygame.

The dealing is ``dealer``'s. This is the rest of the pipeline, shared by the
game (a ``#gauntlet-spec=`` link, run a slice per frame by ``Job.step``) and by
``tools/gauntlet_gen.py`` (every game at once, in a process pool), so both
measure and pick the same way. Design: ``docs/design/gauntlet-gen.md``, "Generated
gauntlets".

A probe game is the leaderboard bot column's (``tests.sim.play_settings``): the
setup built through ``build_state``, the human's seat handed to the probe with
``is_human`` cleared, the turn rng reseeded each turn as a live game does. A
probe is a bot with untuned params, ``name:aux`` for a stop of its knob, or one
of ``TWEAKS``. The tool lifts the bots' wall-clock guards first
(``ai.set_budget_scale``); the game does not (never mid-session), so a probe
game with a time-budgeted bot in it can differ between the two.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import itertools
import json
import random
import time
import zlib
from collections.abc import Iterator

from . import ai, dealer, engine, gauntlet, replay
from .model import AiParams
from .settings import Settings, build_state, lane_lengths, lane_turns

# Five probes spaced by strength, all free of a wall-clock guard so a probe game
# is the same on any machine: on the first run's spec they won 15, 30, 32, 39 and
# 53% of 320 candidates ("Dealing fewer candidates").
PROBES = ("heuristic+lean", "thinker", "knower:0", "evolved:0", "marshal")
# Probes that are not a bot or a stop of its knob: (strategy, params).
# heuristic with no reserve beats stock heuristic 93.6% head to head.
TWEAKS = {"heuristic+lean": ("heuristic", AiParams(reserve_fraction=0.0, reserve_floor=0))}
MAX_TURNS = 400
# Candidates are dealt in rounds, one at each stage's depth, and a run stops at
# the end of the first round its pick has settled (``settled``); these cap the
# rounds. What a spec link may ask for, and what it gets when it asks for
# nothing: the web build is slower by a factor nobody has timed yet.
ROUNDS = 4
LINK_ROUNDS = 3
MAX_ROUNDS = 8
SPEC_FRAGMENT = "gauntlet-spec="
# Which probe win a stage's target is: the slowest (a bar every probe that won
# cleared) or the fastest (the best a probe managed).
TARGETS = ("slowest", "fastest")
# How the stages are joined (`widths`): one path, or a branching shape whose
# wide columns hold a pair of stages about as hard as each other but unlike.
SHAPES = ("line", "concertina", "cross-stitch")
# Draws the second stage of a pair may take to differ from the first.
PAIR_TRIES = 20


def widths(shape: str, columns: int) -> list[int]:
    """The stages in each column of a gauntlet of ``shape``, ``columns`` long
    (a run plays one per column). Every shape ends on one stage. A concertina
    splits and rejoins, 1, 2, 1, 2 … 1, an even count ending 2, 1, 1 so a run
    still plays exactly ``columns``; a cross-stitch is two tracks, 2, 2 … 2, 1."""
    if columns < 1:
        return []
    if shape == "concertina":
        odd = columns if columns % 2 else columns - 1
        return [1 + i % 2 for i in range(odd)] + ([1] if odd < columns else [])
    if shape == "cross-stitch":
        return [2] * (columns - 1) + [1]
    return [1] * columns


def axes(stage: dict) -> dict:
    """What a pair is told apart by, read once at deal time: systems, the
    median lane in turns at the stage's ship speed, and the mode."""
    cfg = Settings.from_dict(stage)
    turns = lane_turns(lane_lengths(cfg), cfg.ship_ly_per_turn)
    return {"systems": cfg.system_count(), "lane": turns[1] if turns else 0, "mode": cfg.mode}


def differ(a: dict, b: dict) -> str | None:
    """The first of ``dealer.AXES`` two stages' ``axes`` differ on, or None
    when they are alike on every one."""
    lo, hi = sorted((a["systems"], b["systems"]))
    if hi >= lo * dealer.SIZE_RATIO:
        return "size"
    if abs(a["lane"] - b["lane"]) >= dealer.LANE_TURNS_APART:
        return "lane length"
    if a["mode"] != b["mode"]:
        return "layout"
    return None


# --------------------------------------------------------------------------- #
# Dealing, measuring, picking
# --------------------------------------------------------------------------- #
def _ends_first(n: int) -> list[int]:
    """0, n-1, 1, n-2, ...: the order of a round's stages, so a round cut short
    would favour no depth."""
    out, lo, hi = [], 0, n - 1
    while lo <= hi:
        out += [lo] if lo == hi else [lo, hi]
        lo, hi = lo + 1, hi - 1
    return out


def candidates(spec: dealer.Spec, seed: int, rounds: int, shape: str = "line") -> list[dict]:
    """Every candidate a run may deal, in order: ``{key, depth, round, stage}``,
    a round at a time, one at each column's depth, the ends first. A pure
    function of the spec, the seed and the shape, so a rerun deals the same ones.

    A branching shape's candidates carry their ``axes``, and a wide column
    deals two a round, **the second dealt to differ** from the first: drawn up
    to ``PAIR_TRIES`` times from the same rng until it differs on an axis,
    keeping the last draw if none does. Dealing costs next to nothing and
    probing is what a run spends its time on, so an alike deal is cheaper
    turned away here than probed and found out afterwards."""
    rng = random.Random(f"{seed}:gauntlet")
    out, seen = [], set()
    cols = widths(shape, spec.stages)

    def add(s: Settings, depth: float, r: int, ax: dict | None) -> None:
        key = s.challenge_key()
        if key not in seen:
            seen.add(key)
            row = {"key": key, "depth": round(depth, 4), "round": r, "stage": dealer.stage_dict(s)}
            out.append(row if ax is None else {**row, "axes": ax})

    for r in range(rounds):
        for i in _ends_first(len(cols)):
            depth = i / (len(cols) - 1) if len(cols) > 1 else 0.0
            s = dealer.deal(spec, rng, depth)
            if shape == "line":
                add(s, depth, r, None)
                continue
            first = axes(dealer.stage_dict(s))
            add(s, depth, r, first)
            if cols[i] == 2:
                for _ in range(PAIR_TRIES):
                    other = dealer.deal(spec, rng, depth)
                    second = axes(dealer.stage_dict(other))
                    if differ(first, second):
                        break
                add(other, depth, r, second)
    return out


def probe_profile(probe: str) -> tuple[str, AiParams]:
    """The strategy and params a probe plays with."""
    if probe in TWEAKS:
        bot, params = TWEAKS[probe]
        return bot, AiParams(**vars(params))
    bot, _, aux = probe.partition(":")
    return bot, AiParams(aux=float(aux)) if aux else AiParams()


def play(stage: dict, probe: str, max_turns: int = MAX_TURNS) -> Iterator[None]:
    """Play one probe game a turn per step; the generator's return value (its
    ``StopIteration.value``) is ``(won, turns, lost, timed_out)``. Stepwise so
    the game can spread it over frames."""
    cfg = Settings.from_dict(stage)
    state = build_state(cfg, cfg.seed if cfg.seed is not None else 0)
    seat = state.human()
    if seat is None:
        raise ValueError("this setup has no human seat to probe")
    seat.is_human = False
    seat.ai_strategy, seat.ai_params = probe_profile(probe)
    while state.winner is None and state.turn < max_turns:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
        yield
    timed_out = state.winner is None
    return (state.winner == seat.id, state.turn, seat.ships_lost, timed_out)


def play_one(key: str, stage: dict, bot: str, max_turns: int = MAX_TURNS) -> dict:
    """One probe game played out, as the row a run records."""
    t0 = time.perf_counter()
    game = play(stage, bot, max_turns)
    while True:
        try:
            next(game)
        except StopIteration as done:
            won, turns, lost, timed_out = done.value
            break
    return {"key": key, "bot": bot, "won": won and not timed_out, "turns": turns, "lost": lost,
            "timed_out": timed_out, "secs": round(time.perf_counter() - t0, 2)}


def measure(cands: list[dict], rows: list[dict], probes) -> list[dict]:
    """Each candidate with its probes' results: ``played``, ``wins`` (rows),
    ``timeouts`` (games that ran out of turns), ``difficulty`` (share lost) and
    ``win_turns`` (mean, for ties)."""
    by = {(r["key"], r["bot"]): r for r in rows}
    out = []
    for c in cands:
        games = [by[(c["key"], b)] for b in probes if (c["key"], b) in by]
        wins = [r for r in games if r["won"]]
        out.append({**c, "played": len(games), "wins": wins,
                    "timeouts": sum(1 for r in games if r.get("timed_out")),
                    "difficulty": 1 - len(wins) / len(games) if games else None,
                    "win_turns": sum(r["turns"] for r in wins) / len(wins) if wins else None})
    return out


def pick(measured: list[dict], stages: int, unwon_last: bool = True) -> list[dict]:
    """A rising sequence: the candidates some probe won, by difficulty then mean
    winning turns, one at each of ``stages`` evenly spaced places. Fewer when
    there are not that many to pick from.

    With ``unwon_last`` the last stage may be a map no probe won, as half the
    maps people post wins on are for the bots up to marshal: one where every
    probe game finished (a timeout may mean nobody can finish it), dealt at the
    deepest depth, so it is the last stage by its opponents too, not a weak
    field on a map the probes lost by position."""
    unwon = sorted((m for m in measured if m["played"] and not m["wins"] and not m.get("timeouts")),
                   key=lambda m: (-m["depth"], m["key"]))
    if unwon_last and unwon:
        return _spread(measured, stages - 1) + [unwon[0]]
    return _spread(measured, stages)


def _near(m: dict, others: list[dict]) -> list[dict]:
    """``others`` nearest ``m`` in difficulty, then in winning turns."""
    return sorted(others, key=lambda o: (abs(o["difficulty"] - m["difficulty"]),
                                         abs((o["win_turns"] or 0) - (m["win_turns"] or 0)), o["key"]))


def pick_shape(measured: list[dict], cols: list[int],
               unwon_last: bool = True) -> tuple[list[list[dict]], list[int]]:
    """``pick`` for a branching shape: a list of columns, each of as many
    candidates as ``cols`` says, and the columns whose pair is alike.

    The usable candidates are ranked as ``_spread`` ranks them and each column
    takes its evenly spaced point on the ranking. A wide column then takes the
    nearest unused candidate that differs from it on an axis (``differ``), so
    the two are about as hard as each other but unlike; failing that, the
    nearest unused at all, and the pair is alike. The unwon last stage, as
    ``pick`` deals it, is the final single column. Columns stay short when there
    are too few candidates to fill them."""
    unwon = sorted((m for m in measured if m["played"] and not m["wins"] and not m.get("timeouts")),
                   key=lambda m: (-m["depth"], m["key"]))
    last = unwon[0] if unwon_last and unwon and cols and cols[-1] == 1 else None
    body = cols[:-1] if last is not None else cols
    usable = sorted((m for m in measured if m["wins"]),
                    key=lambda m: (m["difficulty"], m["win_turns"], m["key"]))
    n, k = len(usable), len(body)
    used: set[str] = set()
    out: list[list[dict]] = []
    alike: list[int] = []
    for c, width in enumerate(body):
        left = [m for m in usable if m["key"] not in used]
        if not left:
            out.append([])
            continue
        point = usable[round(c * (n - 1) / (k - 1))] if k > 1 else usable[0]
        head = point if point["key"] not in used else _near(point, left)[0]
        used.add(head["key"])
        column = [head]
        if width == 2:
            rest = _near(head, [m for m in usable if m["key"] not in used])
            unlike = [m for m in rest if head.get("axes") and m.get("axes") and differ(head["axes"], m["axes"])]
            mate = unlike[0] if unlike else rest[0] if rest else None
            if mate is not None:
                used.add(mate["key"])
                column.append(mate)
                if not unlike:
                    alike.append(c)
        out.append(column)
    if last is not None:
        out.append([last])
    return out, alike


def settled(measured: list[dict], stages: int, unwon_last: bool, levels: int,
            before: frozenset | None, cols: list[int] | None = None,
            free: bool = True) -> tuple[bool, frozenset | None]:
    """Whether a run may stop where a round ends, and what to pass as ``before``
    at the next round's end. It may once the pick is complete (every stage
    filled, the last one unwon when ``unwon_last``) and either shows every usable
    level the ``levels`` probes can tell apart or the round added none to it.
    Checked only where a round ends, so every depth has as many candidates as
    every other when it stops: stopping early never leans towards the shallow
    stages' weaker opponents.

    With ``cols`` (a branching shape's ``widths``) every column must be full
    and, where the spec leaves an axis ``free``, every pair must differ; a
    round that makes a pair differ counts as one that added something."""
    if cols is not None and any(w > 1 for w in cols):
        return _settled_shape(measured, cols, unwon_last, levels, before, free)
    picked = pick(measured, stages, unwon_last)
    if len(picked) < stages or (unwon_last and picked[-1]["wins"]):
        return False, None
    shown = frozenset(m["difficulty"] for m in picked if m["wins"]) | (
        {"unwon"} if not picked[-1]["wins"] else set())
    usable = len(shown - {"unwon"})
    return usable >= min(levels, stages - unwon_last) or shown == before, shown


def _settled_shape(measured: list[dict], cols: list[int], unwon_last: bool, levels: int,
                   before: frozenset | None, free: bool) -> tuple[bool, frozenset | None]:
    columns, alike = pick_shape(measured, cols, unwon_last)
    if (len(columns) < len(cols) or any(len(c) < w for c, w in zip(columns, cols, strict=True))
            or (unwon_last and columns[-1][0]["wins"])):
        return False, None
    picked = [m for c in columns for m in c]
    shown = frozenset(m["difficulty"] for m in picked if m["wins"]) | (
        {"unwon"} if not columns[-1][0]["wins"] else set())
    usable = len(shown - {"unwon"})
    tracks = frozenset({("alike", len(alike))})
    ok = usable >= min(levels, len(cols) - unwon_last) and (not alike or not free)
    return ok or (shown | tracks) == before, shown | tracks


def _spread(measured: list[dict], stages: int) -> list[dict]:
    if stages < 1:
        return []
    usable = sorted((m for m in measured if m["wins"]),
                    key=lambda m: (m["difficulty"], m["win_turns"], m["key"]))
    n = len(usable)
    if n <= stages:
        return usable
    if stages == 1:
        return [usable[0]]
    return [usable[round(i * (n - 1) / (stages - 1))] for i in range(stages)]


def target(m: dict, slack: int, which: str = "slowest") -> dict:
    """The slowest or the fastest probe win (``which``), plus ``slack`` turns:
    shown possible by a bot."""
    score = lambda r: (r["turns"], r["lost"])
    win = max(m["wins"], key=score) if which == "slowest" else min(m["wins"], key=score)
    if slack:
        return {"turns": win["turns"] + slack}
    return {"turns": win["turns"], "lost": win["lost"]}


# A pair's titles by the first axis its stages differ on (`differ`): the word
# for the stage with more of it, then for the one with less.
PAIR_WORDS = {"size": ("more systems", "fewer systems"),
              "lane length": ("longer lanes", "shorter lanes"),
              "layout": ("symmetric", "scattered")}


def _more(axis: str, m: dict, other: dict) -> bool:
    a, b = m.get("axes") or {}, other.get("axes") or {}
    if axis == "size":
        return a.get("systems", 0) > b.get("systems", 0)
    if axis == "lane length":
        return a.get("lane", 0) > b.get("lane", 0)
    return a.get("mode") == "symmetric"


def titles(columns: list[list[dict]]) -> list[str]:
    """Each stage's title, column by column: "Stage N" for a single stage, N
    being its column, and for a pair the way they differ, "Stage 3: more
    systems" / "Stage 3: fewer systems", or "Stage 3a" / "Stage 3b" when alike.
    The graph shows titles, and that is where the choice is made."""
    out = []
    for n, column in enumerate(columns, 1):
        if len(column) < 2:
            out += [f"Stage {n}"] * len(column)
            continue
        a, b = column
        axis = differ(a.get("axes") or {}, b.get("axes") or {}) if a.get("axes") and b.get("axes") else None
        if axis is None:
            out += [f"Stage {n}a", f"Stage {n}b"]
        else:
            more, less = PAIR_WORDS[axis]
            out += [f"Stage {n}: {more if _more(axis, m, o) else less}" for m, o in ((a, b), (b, a))]
    return out


def build_shape(gid: str, name: str, columns: list[list[dict]], targets: str | None, slack: int,
                lives: int = 1) -> dict:
    """``build`` for a branching shape: the stages listed column by column (a
    pair's "a" stage first, so ``gauntlet.layout`` draws it above "b"), each
    joined to every stage in the next column, the first column the starts, a
    pair's titles saying how its stages differ (``titles``)."""
    columns = [c for c in columns if c]
    out = build(gid, name, [m for c in columns for m in c], targets, slack, lives)
    ids, at = [], 0
    for c in columns:
        ids.append(list(range(at, at + len(c))))
        at += len(c)
    for row, title in zip(out["stages"], titles(columns), strict=True):
        row["title"] = title
    out["start"] = ids[0] if ids else []
    out["lanes"] = [[a, b] for here, there in itertools.pairwise(ids) for a in here for b in there]
    return out


def build(gid: str, name: str, picked: list[dict], targets: str | None, slack: int,
          lives: int = 1) -> dict:
    """The gauntlet file: linear, a stage per pick, its briefing the setup in a
    line and how many probes won it. ``targets`` is one of ``TARGETS`` or None;
    a stage no probe won has no target, there being no bot score to set it.
    ``lives`` is what a run of it starts with, written when it isn't 1."""
    stages = []
    for i, m in enumerate(picked):
        cfg = Settings.from_dict(m["stage"])
        measured = (f"Measured: {len(m['wins'])} of {m['played']} test bots won this map." if m["wins"]
                    else f"Measured: none of {m['played']} test bots won this map. People can.")
        row = {"id": i, "title": f"Stage {i + 1}", "briefing": [dealer.describe(cfg), measured],
               "hint": "", "settings": m["stage"]}
        if targets and m["wins"]:
            row["target"] = target(m, slack, targets)
        stages.append(row)
    out = {"id": gid[:gauntlet.NAME_MAX], "name": name[:gauntlet.NAME_MAX], "stages": stages}
    if lives != 1:
        out["lives"] = max(1, min(gauntlet.LIVES_MAX, lives))
    return out


# --------------------------------------------------------------------------- #
# A run spread over frames
# --------------------------------------------------------------------------- #
class Job:
    """A whole run, played a slice at a time: ``step`` plays turns until its
    budget is spent, ``progress`` says how far along it is (``total`` is the most
    games it may play: it stops at the first round its pick settles), and
    ``result`` is the gauntlet file once it is done (None if no candidate was
    won). ``listing`` is the author's name when the link asked for the result
    to be listed on the board, the one consent to send it there; None (the
    default) keeps it in this browser only. ``lives`` is written into the file
    and left out of ``gid``: it changes nothing about which stages are picked."""

    def __init__(self, spec: dealer.Spec, seed: int, rounds: int = ROUNDS,
                 probes=PROBES, targets: str | None = None, slack: int = 0, name: str = "",
                 unwon_last: bool = True, listing: str | None = None, lives: int = 1,
                 shape: str = "line"):
        self.spec, self.seed, self.rounds = spec, seed, rounds
        self.listing, self.lives = listing, lives
        self.shape = shape if shape in SHAPES else "line"
        self.cols = widths(self.shape, spec.stages)
        self.free = bool(dealer.free_axes(spec))
        self.probes = tuple(probes)
        self.targets, self.slack = targets, slack
        self.unwon_last = unwon_last
        self.name = name or f"Generated {seed}"
        self.cands = candidates(spec, seed, rounds, self.shape)
        self.queue = [(c["key"], c["stage"], b) for c in self.cands for b in self.probes]
        self.total = len(self.queue)
        self.rows: list[dict] = []
        self._game: Iterator[None] | None = None
        self._round_of = {c["key"]: c["round"] for c in self.cands}
        self._shown: frozenset | None = None

    @property
    def gid(self) -> str:
        """``gen-<hash>`` of the spec and seed: two runs of one link are one
        gauntlet in the store, two different links never collide. The shape is
        hashed only when it isn't a line, so every link made before shapes
        keeps its gauntlet's id, and so its bests and runs."""
        parts = [self.spec.to_dict(), self.seed, self.rounds, self.probes, self.targets,
                 self.slack, self.unwon_last]
        if self.shape != "line":
            parts.append(self.shape)
        raw = json.dumps(parts, sort_keys=True).encode("utf-8")
        return "gen-" + hashlib.blake2s(raw, digest_size=6).hexdigest()

    @property
    def done(self) -> bool:
        return not self.queue and self._game is None

    def progress(self) -> float:
        return len(self.rows) / self.total if self.total else 1.0

    def step(self, budget_s: float) -> None:
        """Play on until ``budget_s`` seconds have passed (at least one turn)."""
        deadline = time.perf_counter() + budget_s
        while not self.done:
            if self._game is None:
                _key, stage, bot = self.queue[0]
                self._game = play(stage, bot)
            try:
                next(self._game)
            except StopIteration as finished:
                key, _stage, bot = self.queue.pop(0)
                won, turns, lost, timed_out = finished.value
                self.rows.append({"key": key, "bot": bot, "won": won and not timed_out,
                                  "turns": turns, "lost": lost, "timed_out": timed_out})
                self._game = None
                if not self.queue or self._round_of[self.queue[0][0]] != self._round_of[key]:
                    self._round_ended(self._round_of[key])
            if time.perf_counter() >= deadline:
                return

    def _round_ended(self, r: int) -> None:
        dealt = [c for c in self.cands if c["round"] <= r]
        stop, self._shown = settled(measure(dealt, self.rows, self.probes), self.spec.stages,
                                    self.unwon_last, len(self.probes), self._shown, self.cols, self.free)
        if stop:
            self.queue.clear()

    def result(self) -> dict | None:
        if not self.done:
            return None
        measured = measure(self.cands, self.rows, self.probes)
        if self.shape != "line":
            columns, _alike = pick_shape(measured, self.cols, self.unwon_last)
            if not any(columns):
                return None
            return build_shape(self.gid, self.name, columns, self.targets, self.slack, self.lives)
        picked = pick(measured, self.spec.stages, self.unwon_last)
        return build(self.gid, self.name, picked, self.targets, self.slack, self.lives) if picked else None


# --------------------------------------------------------------------------- #
# The spec link
# --------------------------------------------------------------------------- #
# The board's gauntlet page builds `#gauntlet-spec=<payload>`: the spec as
# deflated JSON in base64url, as a settings token is, with the run's own options
# beside it (`candidates`, `targets`, `slack`, `name`, `unwon_last`, `lives`, `shape`, and
# `wants_listing` with `by_name`), which `Spec.from_dict` ignores.
# `candidates` is the most rounds to deal (it once counted candidates per stage,
# which a round deals one of). A spec is a recipe, not a result: it is never kept
# or shared on, the gauntlet it makes is (`gauntlet.remember`, and
# `gauntlet.ask_listing` when the link asked for it to be listed).
def spec_from_fragment(fragment: str) -> dict | None:
    """The spec a ``#gauntlet-spec=`` fragment carries, or None."""
    if not fragment.startswith(SPEC_FRAGMENT):
        return None
    token = fragment[len(SPEC_FRAGMENT):].strip()
    try:
        raw = base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode("ascii"))
        if raw[:1] != b"{":
            raw = zlib.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError, zlib.error):
        return None
    return data if isinstance(data, dict) else None


def spec_fragment(data: dict) -> str:
    """``data`` as a ``#gauntlet-spec=`` fragment (without the ``#``)."""
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return SPEC_FRAGMENT + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def job_from_link(data: dict, seed: int) -> Job:
    """A run for a spec link. ``seed`` is used when the link names none (the
    caller rolls it); a bad option falls back. Raises ValueError for a spec
    that can never deal (``dealer.problems``)."""
    spec = dealer.Spec.from_dict(data)
    if stuck := dealer.problems(spec):
        raise ValueError(stuck[0])
    rounds = data.get("candidates")
    rounds = rounds if isinstance(rounds, int) and not isinstance(rounds, bool) else LINK_ROUNDS
    slack = data.get("slack")
    slack = slack if isinstance(slack, int) and not isinstance(slack, bool) and 0 <= slack <= 99 else 0
    name = data.get("name")
    targets = data.get("targets")
    targets = "slowest" if targets is True else targets if targets in TARGETS else None
    # Only a ticked "ask for it to be listed" sends the result anywhere; the
    # name rides with it, clipped as the board would refuse it past the limit.
    by_name = data.get("by_name")
    by_name = " ".join(by_name.split())[:gauntlet.BY_NAME_MAX] if isinstance(by_name, str) else ""
    listing = by_name if data.get("wants_listing") is True else None
    lives = data.get("lives")
    lives = lives if isinstance(lives, int) and not isinstance(lives, bool) else 1
    return Job(spec, spec.seed if spec.seed is not None else seed,
               rounds=max(1, min(MAX_ROUNDS, rounds)),
               targets=targets, slack=slack,
               name=" ".join(name.split()) if isinstance(name, str) else "",
               unwon_last=data.get("unwon_last") is not False, listing=listing,
               lives=max(1, min(gauntlet.LIVES_MAX, lives)),
               shape=data.get("shape") if data.get("shape") in SHAPES else "line")
