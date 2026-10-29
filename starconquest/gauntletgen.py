"""Generate a gauntlet from a spec: deal candidates, probe them, pick a rising
sequence — pure core, no pygame.

The dealing is ``dealer``'s. This is the rest of the pipeline, shared by the
game (a ``#gauntlet-spec=`` link, run a slice per frame by ``Job.step``) and by
``tools/gauntlet_gen.py`` (every game at once, in a process pool), so both
measure and pick the same way. Design: ``docs/design/gauntlet.md``, "Generated
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
import json
import random
import time
import zlib
from collections.abc import Iterator

from . import ai, dealer, engine, gauntlet, replay
from .model import AiParams
from .settings import Settings, build_state

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


def candidates(spec: dealer.Spec, seed: int, rounds: int) -> list[dict]:
    """Every candidate a run may deal, in order: ``{key, depth, round, stage}``,
    a round at a time, one at each stage's depth, the ends first. A pure
    function of the spec and the seed, so a rerun deals the same ones."""
    rng = random.Random(f"{seed}:gauntlet")
    out, seen = [], set()
    for r in range(rounds):
        for i in _ends_first(spec.stages):
            depth = i / (spec.stages - 1) if spec.stages > 1 else 0.0
            s = dealer.deal(spec, rng, depth)
            key = s.challenge_key()
            if key not in seen:
                seen.add(key)
                out.append({"key": key, "depth": round(depth, 4), "round": r,
                            "stage": dealer.stage_dict(s)})
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


def settled(measured: list[dict], stages: int, unwon_last: bool, levels: int,
            before: frozenset | None) -> tuple[bool, frozenset | None]:
    """Whether a run may stop where a round ends, and what to pass as ``before``
    at the next round's end. It may once the pick is complete (every stage
    filled, the last one unwon when ``unwon_last``) and either shows every usable
    level the ``levels`` probes can tell apart or the round added none to it.
    Checked only where a round ends, so every depth has as many candidates as
    every other when it stops: stopping early never leans towards the shallow
    stages' weaker opponents."""
    picked = pick(measured, stages, unwon_last)
    if len(picked) < stages or (unwon_last and picked[-1]["wins"]):
        return False, None
    shown = frozenset(m["difficulty"] for m in picked if m["wins"]) | (
        {"unwon"} if not picked[-1]["wins"] else set())
    usable = len(shown - {"unwon"})
    return usable >= min(levels, stages - unwon_last) or shown == before, shown


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
    score = lambda r: (r["turns"], r["lost"])       # noqa: E731
    win = max(m["wins"], key=score) if which == "slowest" else min(m["wins"], key=score)
    if slack:
        return {"turns": win["turns"] + slack}
    return {"turns": win["turns"], "lost": win["lost"]}


def build(gid: str, name: str, picked: list[dict], targets: str | None, slack: int) -> dict:
    """The gauntlet file: linear, a stage per pick, its briefing the setup in a
    line and how many probes won it. ``targets`` is one of ``TARGETS`` or None;
    a stage no probe won has no target, there being no bot score to set it."""
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
    return {"id": gid[:gauntlet.NAME_MAX], "name": name[:gauntlet.NAME_MAX], "stages": stages}


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
    default) keeps it in this browser only."""

    def __init__(self, spec: dealer.Spec, seed: int, rounds: int = ROUNDS,
                 probes=PROBES, targets: str | None = None, slack: int = 0, name: str = "",
                 unwon_last: bool = True, listing: str | None = None):
        self.spec, self.seed, self.rounds = spec, seed, rounds
        self.listing = listing
        self.probes = tuple(probes)
        self.targets, self.slack = targets, slack
        self.unwon_last = unwon_last
        self.name = name or f"Generated {seed}"
        self.cands = candidates(spec, seed, rounds)
        self.queue = [(c["key"], c["stage"], b) for c in self.cands for b in self.probes]
        self.total = len(self.queue)
        self.rows: list[dict] = []
        self._game: Iterator[None] | None = None
        self._round_of = {c["key"]: c["round"] for c in self.cands}
        self._shown: frozenset | None = None

    @property
    def gid(self) -> str:
        """``gen-<hash>`` of the spec and seed: two runs of one link are one
        gauntlet in the store, two different links never collide."""
        raw = json.dumps([self.spec.to_dict(), self.seed, self.rounds, self.probes, self.targets,
                          self.slack, self.unwon_last],
                         sort_keys=True).encode("utf-8")
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
                                    self.unwon_last, len(self.probes), self._shown)
        if stop:
            self.queue.clear()

    def result(self) -> dict | None:
        if not self.done:
            return None
        picked = pick(measure(self.cands, self.rows, self.probes), self.spec.stages, self.unwon_last)
        return build(self.gid, self.name, picked, self.targets, self.slack) if picked else None


# --------------------------------------------------------------------------- #
# The spec link
# --------------------------------------------------------------------------- #
# The board's gauntlet page builds `#gauntlet-spec=<payload>`: the spec as
# deflated JSON in base64url, as a settings token is, with the run's own options
# beside it (`candidates`, `targets`, `slack`, `name`, `unwon_last`, and
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
    return Job(spec, spec.seed if spec.seed is not None else seed,
               rounds=max(1, min(MAX_ROUNDS, rounds)),
               targets=targets, slack=slack,
               name=" ".join(name.split()) if isinstance(name, str) else "",
               unwon_last=data.get("unwon_last") is not False, listing=listing)
