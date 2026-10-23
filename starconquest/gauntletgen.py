"""Generate a gauntlet from a spec: deal candidates, probe them, pick a rising
sequence — pure core, no pygame.

The dealing is ``dealer``'s. This is the rest of the pipeline, shared by the
game (a ``#gauntlet-spec=`` link, run a slice per frame by ``Job.step``) and by
``tools/gauntlet_gen.py`` (every game at once, in a process pool), so both
measure and pick the same way. Design: ``docs/design/gauntlet.md``, "Generated
gauntlets".

A probe game is the leaderboard bot column's (``tests.sim.play_settings``): the
setup built through ``build_state``, the human's seat handed to the probe bot
with untuned params and ``is_human`` cleared, the turn rng reseeded each turn as
a live game does. The tool lifts the bots' wall-clock guards first
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

PROBES = ("rusherplus", "heuristic", "thinker", "marshal")
MAX_TURNS = 400
CANDIDATES = 4
# What a spec link may ask for, and what it gets when it asks for nothing: the
# web build is slower by a factor nobody has timed yet, so it deals fewer.
LINK_CANDIDATES = 3
MAX_CANDIDATES = 8
SPEC_FRAGMENT = "gauntlet-spec="


# --------------------------------------------------------------------------- #
# Dealing, measuring, picking
# --------------------------------------------------------------------------- #
def candidates(spec: dealer.Spec, seed: int, per_stage: int) -> list[dict]:
    """Every candidate a run deals, in order: ``{key, depth, stage}``. A pure
    function of the spec and the seed, so a rerun deals the same ones."""
    rng = random.Random(f"{seed}:gauntlet")
    out, seen = [], set()
    for i in range(spec.stages):
        depth = i / (spec.stages - 1) if spec.stages > 1 else 0.0
        for _ in range(per_stage):
            s = dealer.deal(spec, rng, depth)
            key = s.challenge_key()
            if key not in seen:
                seen.add(key)
                out.append({"key": key, "depth": round(depth, 4), "stage": dealer.stage_dict(s)})
    return out


def play(stage: dict, bot: str, max_turns: int = MAX_TURNS) -> Iterator[None]:
    """Play one probe game a turn per step; the generator's return value (its
    ``StopIteration.value``) is ``(won, turns, lost, timed_out)``. Stepwise so
    the game can spread it over frames."""
    cfg = Settings.from_dict(stage)
    state = build_state(cfg, cfg.seed if cfg.seed is not None else 0)
    seat = state.human()
    if seat is None:
        raise ValueError("this setup has no human seat to probe")
    seat.is_human = False
    seat.ai_strategy = bot
    seat.ai_params = AiParams()
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
    ``difficulty`` (share lost) and ``win_turns`` (mean, for ties)."""
    by = {(r["key"], r["bot"]): r for r in rows}
    out = []
    for c in cands:
        games = [by[(c["key"], b)] for b in probes if (c["key"], b) in by]
        wins = [r for r in games if r["won"]]
        out.append({**c, "played": len(games), "wins": wins,
                    "difficulty": 1 - len(wins) / len(games) if games else None,
                    "win_turns": sum(r["turns"] for r in wins) / len(wins) if wins else None})
    return out


def pick(measured: list[dict], stages: int) -> list[dict]:
    """A rising sequence: the candidates some probe won, by difficulty then mean
    winning turns, one at each of ``stages`` evenly spaced places. Fewer when
    there are not that many to pick from."""
    usable = sorted((m for m in measured if m["wins"]),
                    key=lambda m: (m["difficulty"], m["win_turns"], m["key"]))
    n = len(usable)
    if n <= stages:
        return usable
    if stages == 1:
        return [usable[0]]
    return [usable[round(i * (n - 1) / (stages - 1))] for i in range(stages)]


def target(m: dict, slack: int) -> dict:
    """The slowest probe win, plus ``slack`` turns: shown possible by a bot."""
    slowest = max(m["wins"], key=lambda r: (r["turns"], r["lost"]))
    if slack:
        return {"turns": slowest["turns"] + slack}
    return {"turns": slowest["turns"], "lost": slowest["lost"]}


def build(gid: str, name: str, picked: list[dict], targets: bool, slack: int) -> dict:
    """The gauntlet file: linear, a stage per pick, its briefing the setup in a
    line and how many probes won it."""
    stages = []
    for i, m in enumerate(picked):
        cfg = Settings.from_dict(m["stage"])
        row = {"id": i, "title": f"Stage {i + 1}",
               "briefing": [dealer.describe(cfg),
                            f"Measured: {len(m['wins'])} of {m['played']} test bots won this map."],
               "hint": "", "settings": m["stage"]}
        if targets:
            row["target"] = target(m, slack)
        stages.append(row)
    return {"id": gid[:gauntlet.NAME_MAX], "name": name[:gauntlet.NAME_MAX], "stages": stages}


# --------------------------------------------------------------------------- #
# A run spread over frames
# --------------------------------------------------------------------------- #
class Job:
    """A whole run, played a slice at a time: ``step`` plays turns until its
    budget is spent, ``progress`` says how far along it is, and ``result`` is the
    gauntlet file once every probe game is done (None if no candidate was won)."""

    def __init__(self, spec: dealer.Spec, seed: int, per_stage: int = CANDIDATES,
                 probes=PROBES, targets: bool = False, slack: int = 0, name: str = ""):
        self.spec, self.seed = spec, seed
        self.probes = tuple(probes)
        self.targets, self.slack = targets, slack
        self.name = name or f"Generated {seed}"
        self.cands = candidates(spec, seed, per_stage)
        self.queue = [(c["key"], c["stage"], b) for c in self.cands for b in self.probes]
        self.total = len(self.queue)
        self.rows: list[dict] = []
        self._game: Iterator[None] | None = None

    @property
    def gid(self) -> str:
        """``gen-<hash>`` of the spec and seed: two runs of one link are one
        gauntlet in the store, two different links never collide."""
        raw = json.dumps([self.spec.to_dict(), self.seed, self.probes, self.targets, self.slack],
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
            if time.perf_counter() >= deadline:
                return

    def result(self) -> dict | None:
        if not self.done:
            return None
        picked = pick(measure(self.cands, self.rows, self.probes), self.spec.stages)
        return build(self.gid, self.name, picked, self.targets, self.slack) if picked else None


# --------------------------------------------------------------------------- #
# The spec link
# --------------------------------------------------------------------------- #
# The board's gauntlet page builds `#gauntlet-spec=<payload>`: the spec as
# deflated JSON in base64url, as a settings token is, with the run's own options
# beside it (`candidates`, `targets`, `slack`, `name`), which `Spec.from_dict`
# ignores. A spec is a recipe, not a result: it is never kept or shared on, the
# gauntlet it makes is (`gauntlet.remember`).
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
    per = data.get("candidates")
    per = per if isinstance(per, int) and not isinstance(per, bool) else LINK_CANDIDATES
    slack = data.get("slack")
    slack = slack if isinstance(slack, int) and not isinstance(slack, bool) and 0 <= slack <= 99 else 0
    name = data.get("name")
    return Job(spec, spec.seed if spec.seed is not None else seed,
               per_stage=max(1, min(MAX_CANDIDATES, per)),
               targets=data.get("targets") is True, slack=slack,
               name=" ".join(name.split()) if isinstance(name, str) else "")
