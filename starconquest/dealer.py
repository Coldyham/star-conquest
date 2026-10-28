"""Deal gauntlet stages from a spec — pure core, no pygame.

A **spec** says how many stages a generated gauntlet has and which setups are
allowed: a knob fixed, kept in a range, drawn from a list or kept off some
values; which bots may sit in the opponents' seats; and whether a knob nothing
names is rolled or left at its default. ``deal`` turns a spec, an rng and a
depth (0 for the first stage, 1 for the last) into one ``Settings``; with no
depth, each opponent's seat is drawn on its own from every allowed bot (the
daily's board, ``tools/daily.py``).
``tools/gauntlet_gen.py`` deals candidates, plays bots on them and picks a
rising sequence. Reasons and the rest of the plan: ``docs/design/gauntlet.md``,
"Generated gauntlets".

Four rules:

**No `Settings` field is added.** A dealt stage is an ordinary setup with a
pinned seed and a named bot in every seat (never ``random``), so it posts to the
board as itself and no challenge key moves.

**Dealing is a pure function of the spec and the rng.** Every draw comes from
the rng passed in, and the seed through ``settings.roll_seed``.

**A rule on `lane_turns` is settled last.** It bounds the median lane's
travel time on the dealt map, so the map is measured once its seed is rolled
and the ship speed is drawn from the values the `ship_ly_per_turn` rule allows
that land the median in range (`_fit_speed`). A spec without it deals exactly
as it always did.

**`Spec.from_dict` never raises.** A spec will arrive in a link: a rule that
cannot be read is dropped and a range is clamped to its slider, as
``Settings.from_dict`` reads a setup. ``problems`` names a spec nothing can
satisfy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import ai, config, mapgen
from .settings import (ADV_COMBAT, ADV_ECON, ADV_FOG, ADV_MAP, ADV_TRAVEL, ROLL_FLOORS, Settings,
                       fog_off, lane_lengths, lane_turns, roll_seed, setup_warnings)

MAX_STAGES = 64
# How far a roll of `systems` goes when no rule bounds it: a standard board, as
# the campaign's "?" node rolls (`tools/campaign.py`, MYSTERY_MAX_NODES).
SYSTEMS_ROLL_MAX = config.STANDARD_MAX_NODES
MAX_ATTEMPTS = 400
# The highest median lane, in turns, a `lane_turns` rule may ask for.
LANE_TURNS_MAX = 20

# attr -> (lo, hi, step, is_int): every slider a rule may name.
KNOBS = {attr: (lo, hi, step, is_int)
         for _key, _label, attr, lo, hi, step, is_int in ADV_MAP + ADV_TRAVEL + ADV_ECON + ADV_COMBAT + ADV_FOG}
# The sliders `roll` rolls when no rule names them: the campaign's "?" node's
# (`_mystery`). Combat is rolled only when ruled.
ROLLED = tuple(spec[2] for spec in ADV_MAP + ADV_TRAVEL + ADV_ECON + ADV_FOG)
# The dealer's own names, each a choice from a fixed list.
CHOICES = {
    "mode": ("random", "symmetric"),
    "layout": mapgen.SYMMETRIC_LAYOUTS,
    "fog": (True, False),
    "neutral_produces": (True, False),
    "in_lane_battles": (True, False),
}
KINDS = ("fix", "range", "in", "not")


@dataclass(frozen=True)
class Rule:
    """One field's constraint: exactly one of ``fix``, ``range``, ``in`` or
    ``not``. ``value`` is the fixed value, the ``(lo, hi)`` pair, or the tuple of
    values."""

    kind: str
    value: object


@dataclass(frozen=True)
class Spec:
    stages: int = 8
    seed: int | None = None
    rules: dict[str, Rule] = field(default_factory=dict)
    # Bots the opponents' seats may hold, `name` or `name:stop`, as given.
    opponents: tuple[str, ...] = tuple(ai.LADDER_ORDER)
    # Off by default: every slider rolled over its whole range deals setups so
    # extreme (one home ship against garrison scale 38) that the probes measure
    # the knobs rather than the stage (docs/design/gauntlet.md, "The first run").
    roll: bool = False

    @classmethod
    def from_dict(cls, data) -> Spec:
        """Read a spec tolerantly; never raises. Anything unreadable falls back
        to the default, field by field."""
        try:
            return _from_dict(data)
        except Exception:      # noqa: BLE001 — a link's junk is never an error
            return cls()

    def to_dict(self) -> dict:
        out: dict = {"stages": self.stages, "roll": self.roll,
                     "rules": {name: {r.kind: list(r.value) if isinstance(r.value, tuple) else r.value}
                               for name, r in self.rules.items()},
                     "opponents": {"in": list(self.opponents)}}
        if self.seed is not None:
            out["seed"] = self.seed
        return out


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _bounds(name: str) -> tuple[float, float, float, bool] | None:
    """(lo, hi, step, is_int) for a numeric field, or None for a choice."""
    if name in KNOBS:
        return KNOBS[name]
    if name == "players":
        return (config.MIN_PLAYERS, config.MAX_PLAYERS, 1, True)
    if name == "systems":
        return (config.MIN_PLAYERS + 3, config.MAX_NODES, 1, True)
    if name == "lane_turns":
        return (1, LANE_TURNS_MAX, 1, True)
    return None


def _snap(name: str, value: float) -> float | int:
    lo, hi, step, is_int = _bounds(name)      # type: ignore[misc]
    snapped = max(lo, min(hi, round(value / step) * step))
    return round(snapped) if is_int else round(snapped, 4)


def _rule(name: str, raw) -> Rule | None:
    """One field's rule, or None when it cannot be read."""
    if not isinstance(raw, dict) or len(raw) != 1:
        return None
    kind, value = next(iter(raw.items()))
    if kind not in KINDS:
        return None
    if name in CHOICES:
        options = CHOICES[name]
        if kind == "range":
            return None
        if kind == "fix":
            return Rule("fix", value) if value in options and type(value) is type(options[0]) else None
        if not isinstance(value, list):
            return None
        picked = tuple(o for o in options if any(v == o and type(v) is type(o) for v in value))
        return Rule(kind, picked)
    if _bounds(name) is None:
        return None
    if kind == "fix":
        n = _num(value)
        return Rule("fix", _snap(name, n)) if n is not None else None
    if not isinstance(value, list):
        return None
    nums = [_num(v) for v in value]
    if any(n is None for n in nums):
        return None
    if kind == "range":
        if len(nums) != 2:
            return None
        lo, hi = sorted(nums)          # type: ignore[type-var]
        return Rule("range", (_snap(name, lo), _snap(name, hi)))   # type: ignore[arg-type]
    return Rule(kind, tuple(dict.fromkeys(_snap(name, n) for n in nums)))   # type: ignore[arg-type]


def _from_dict(data) -> Spec:
    if not isinstance(data, dict):
        return Spec()
    stages = _int(data.get("stages"))
    seed = _int(data.get("seed"))
    rules = {}
    raw_rules = data.get("rules")
    if isinstance(raw_rules, dict):
        for name, raw in raw_rules.items():
            rule = _rule(name, raw) if isinstance(name, str) else None
            if rule is not None:
                rules[name] = rule
    opponents = _opponents(data.get("opponents"))
    roll = data.get("roll")
    return Spec(stages=max(1, min(MAX_STAGES, stages)) if stages is not None else Spec.stages,
                seed=seed if seed is not None and seed >= 0 else None,
                rules=rules, opponents=opponents,
                roll=roll if isinstance(roll, bool) else Spec.roll)


def _opponents(raw) -> tuple[str, ...]:
    """The opponents a spec allows. A list is the same as ``{"in": list}``;
    ``{"not": [...]}`` takes names off the ladder. A name may carry a stop
    (``actuary:3``); a bare name matches every stop of it in ``not``."""
    roster = tuple(ai.LADDER_ORDER)
    if isinstance(raw, list):
        raw = {"in": raw}
    if not isinstance(raw, dict) or len(raw) != 1:
        return roster
    kind, value = next(iter(raw.items()))
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return roster
    names = tuple(dict.fromkeys(v.strip() for v in value))
    if kind == "in":
        return tuple(n for n in names if _stop(n) is not False)
    if kind == "not":
        return tuple(n for n in roster if n not in names and n.partition(":")[0] not in names)
    return roster


def _stop(entry: str) -> float | None | bool:
    """An entry's stop: None for a bare name, the number after ``:``, or False
    when that is not a number."""
    _name, sep, stop = entry.partition(":")
    if not sep:
        return None
    try:
        return float(stop)
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Checking a spec
# --------------------------------------------------------------------------- #
def _allowed(name: str, rule: Rule | None) -> list | None:
    """The values a choice field may take under ``rule``, or None for numeric."""
    if name not in CHOICES:
        return None
    options = list(CHOICES[name])
    if rule is None:
        return options
    if rule.kind == "fix":
        return [rule.value]
    if rule.kind == "in":
        return list(rule.value)       # type: ignore[arg-type]
    return [o for o in options if o not in rule.value]      # type: ignore[operator]


def _span(name: str, rule: Rule | None) -> tuple[float, float]:
    """The narrowest (lo, hi) a numeric field's rule allows."""
    lo, hi, _step, _is_int = _bounds(name)      # type: ignore[misc]
    if rule is None or rule.kind == "not":
        return lo, hi
    if rule.kind == "fix":
        return rule.value, rule.value       # type: ignore[return-value]
    if rule.kind == "range":
        return rule.value       # type: ignore[return-value]
    return min(rule.value), max(rule.value)       # type: ignore[call-overload]


def problems(spec: Spec) -> list[str]:
    """What stops ``spec`` dealing anything: a choice or a list ruled empty, no
    opponents, or more players than the systems allowed can hold."""
    out = []
    for name, rule in spec.rules.items():
        allowed = _allowed(name, rule)
        if allowed is not None and not allowed:
            out.append(f"{name} has no value left to deal")
        if allowed is None and rule.kind == "in" and not rule.value:
            out.append(f"{name} has no value left to deal")
        if allowed is None and rule.kind == "not":
            lo, hi, step, _ = _bounds(name)       # type: ignore[misc]
            if int(round((hi - lo) / step)) + 1 <= len(rule.value):       # type: ignore[arg-type]
                out.append(f"{name} has no value left to deal")
    if not spec.opponents:
        out.append("no opponents are allowed")
    if "fog" in spec.rules and spec.rules["fog"].kind == "fix" and spec.rules["fog"].value is True:
        for knob in ("fog_sight", "fog_scout"):
            if knob in spec.rules and _span(knob, spec.rules[knob])[0] >= config.FOG_MAX_HOPS:
                out.append(f"fog is on but {knob} only allows no fog")
    players_lo, _ = _span("players", spec.rules.get("players"))
    _, systems_hi = _span("systems", spec.rules.get("systems"))
    if players_lo + 3 > systems_hi:
        out.append(f"{int(players_lo)} players need at least {int(players_lo) + 3} systems")
    return out


# --------------------------------------------------------------------------- #
# Dealing
# --------------------------------------------------------------------------- #
def _draw_number(name: str, rule: Rule | None, rng: random.Random, lo: float | None = None,
                 hi: float | None = None) -> float | int:
    """A value for numeric field ``name`` under ``rule``, within any extra
    ``lo``/``hi`` the caller narrows it to. Raises ValueError when nothing fits."""
    blo, bhi, step, is_int = _bounds(name)      # type: ignore[misc]
    lo = blo if lo is None else max(blo, lo)
    hi = bhi if hi is None else min(bhi, hi)
    if rule is not None and rule.kind in ("fix", "in"):
        values = [rule.value] if rule.kind == "fix" else list(rule.value)       # type: ignore[arg-type]
        values = [v for v in values if lo <= v <= hi]
        if not values:
            raise ValueError(name)
        return rng.choice(values)
    if rule is not None and rule.kind == "range":
        lo, hi = max(lo, rule.value[0]), min(hi, rule.value[1])       # type: ignore[index]
    lo = max(lo, ROLL_FLOORS.get(name, lo)) if rule is None else lo
    if lo > hi:
        raise ValueError(name)
    banned = rule.value if rule is not None and rule.kind == "not" else ()
    for _ in range(50):
        value = _snap(name, rng.uniform(lo, hi)) if not is_int else rng.randint(int(lo), int(hi))
        if lo <= value <= hi and value not in banned:       # type: ignore[operator]
            return value
    raise ValueError(name)


def _draw_choice(name: str, rule: Rule | None, rng: random.Random):
    allowed = _allowed(name, rule)
    if not allowed:
        raise ValueError(name)
    return rng.choice(allowed)


def ladder(names: tuple[str, ...]) -> list[str]:
    """``names`` weakest first: by `ai.LADDER_ORDER` reversed, an unranked bot
    before every ranked one (the ladder ranks it below the lot), and one bot's
    stops in ascending order."""
    order = list(reversed(ai.LADDER_ORDER))

    def key(entry: str):
        name, _, _ = entry.partition(":")
        stop = _stop(entry)
        rank = order.index(name) + 1 if name in order else 0
        return (rank, name, -1.0 if stop is None or stop is False else stop)

    return sorted(names, key=key)


def _seat_bot(pool: list[str], depth: float, rng: random.Random) -> str:
    """A bot near ``depth`` along ``pool`` (weakest first), a step either way
    now and then so the seats are not all one bot."""
    centre = depth * (len(pool) - 1)
    at = round(centre + rng.choice((-1, 0, 0, 1)))
    return pool[max(0, min(len(pool) - 1, at))]


def deal(spec: Spec, rng: random.Random, depth: float | None = 0.0) -> Settings:
    """One setup ``spec`` allows, drawn from ``rng``, its opponents picked for
    ``depth`` (0 the first stage, 1 the last), or with ``depth`` None each seat
    drawn evenly from every allowed bot, so any mix can be dealt. Raises
    ValueError when ``MAX_ATTEMPTS`` draws find none (``problems`` names a spec
    that can never deal; this is the rest)."""
    depth = None if depth is None else max(0.0, min(1.0, depth))
    pool = ladder(spec.opponents)
    if not pool:
        raise ValueError("no opponents are allowed")
    for _ in range(MAX_ATTEMPTS):
        try:
            settings = _attempt(spec, rng, depth, pool)
        except ValueError:
            continue
        if settings is not None and not setup_warnings(settings):
            return settings
    raise ValueError("no setup fits this spec")


def _attempt(spec: Spec, rng: random.Random, depth: float | None, pool: list[str]) -> Settings | None:
    rules = spec.rules
    settings = Settings.defaults()
    if spec.roll:
        for attr in ROLLED:
            if attr not in rules:
                setattr(settings, attr, _draw_number(attr, None, rng))
    for attr in KNOBS:
        if attr in rules:
            setattr(settings, attr, _draw_number(attr, rules[attr], rng))

    for name in ("neutral_produces", "in_lane_battles"):
        if name in rules:
            setattr(settings, name, _draw_choice(name, rules[name], rng))
    if "fog" in rules:
        if _draw_choice("fog", rules["fog"], rng):
            if not spec.roll and "fog_sight" not in rules and "fog_scout" not in rules:
                # Fog with nothing else said about it is the Basic menu's "Fog of war".
                settings.fog_sight, settings.fog_scout = config.FOG_ON_SIGHT, config.FOG_ON_SCOUT
            elif fog_off(settings):
                # Fog is on, so at least one range must be short of unlimited.
                settings.fog_sight = _draw_number("fog_sight", rules.get("fog_sight"), rng,
                                                  hi=config.FOG_MAX_HOPS - 1)
        else:
            settings.fog_sight = settings.fog_scout = config.FOG_MAX_HOPS

    if "mode" in rules or spec.roll:
        settings.mode = _draw_choice("mode", rules.get("mode"), rng)
    if settings.mode == "symmetric":
        # Always drawn, `roll` or not: left alone it is the default hub, and a
        # run would never show the other layouts (as `tools/campaign.py` rolls it).
        settings.layout = _draw_choice("layout", rules.get("layout"), rng)

    if "players" in rules or spec.roll:
        settings.players = int(_draw_number("players", rules.get("players"), rng))
    floor = settings.min_nodes()
    rule = rules.get("systems")
    cap = int(_span("systems", rule)[1]) if rule is not None else SYSTEMS_ROLL_MAX
    if rule is not None or spec.roll:
        want = int(_draw_number("systems", rule, rng, lo=floor, hi=cap))
    else:
        want = max(floor, settings.nodes)
        cap = max(cap, want)
    if not _fit_systems(settings, want, rule, cap):
        return None

    for seat in range(2, settings.players + 1):
        entry = rng.choice(pool) if depth is None else _seat_bot(pool, depth, rng)
        name, _, stop = entry.partition(":")
        settings.ai_strategy[seat - 1] = name
        if stop:
            settings.ai[seat - 1].aux = float(stop)
    settings.seed = roll_seed(rng)
    if "lane_turns" in rules and not _fit_speed(settings, rules["lane_turns"], rules.get("ship_ly_per_turn"), rng):
        return None
    return settings


def _speeds(rule: Rule | None) -> list[float]:
    """Every ship speed ``rule`` allows, on the slider's steps."""
    lo, hi, step, _is_int = KNOBS["ship_ly_per_turn"]
    if rule is not None and rule.kind in ("fix", "in"):
        return sorted([rule.value] if rule.kind == "fix" else list(rule.value))       # type: ignore[arg-type]
    if rule is not None and rule.kind == "range":
        lo, hi = rule.value       # type: ignore[misc]
    banned = rule.value if rule is not None and rule.kind == "not" else ()
    values = (round(lo + i * step, 4) for i in range(int(round((hi - lo) / step)) + 1))
    return [v for v in values if v not in banned]       # type: ignore[operator]


def _fit_speed(settings: Settings, rule: Rule, speed_rule: Rule | None, rng: random.Random) -> bool:
    """Draw ``settings``' ship speed from those ``speed_rule`` allows whose
    median lane, in turns, ``rule`` allows on this map. False when none does."""
    lengths = lane_lengths(settings)
    fits = [v for v in _speeds(speed_rule)
            if (turns := lane_turns(lengths, v)) is not None and _fits(turns[1], rule)]
    if not fits:
        return False
    settings.ship_ly_per_turn = rng.choice(fits)
    return True


def _fit_systems(settings: Settings, want: int, rule: Rule | None, cap: int) -> bool:
    """Set ``nodes`` so ``system_count`` is ``want`` or, on a symmetric map
    whose whole sectors round it up, the nearest count under that the rule (and
    ``cap``) still allows. False when none does."""
    for nodes in range(want, settings.min_nodes() - 1, -1):
        settings.nodes = nodes
        got = settings.system_count()
        if got <= cap and _fits(got, rule):
            return True
    return False


def _fits(value: int, rule: Rule | None) -> bool:
    if rule is None:
        return True
    if rule.kind == "fix":
        return value == rule.value
    if rule.kind == "range":
        return rule.value[0] <= value <= rule.value[1]       # type: ignore[index]
    if rule.kind == "in":
        return value in rule.value       # type: ignore[operator]
    return value not in rule.value       # type: ignore[operator]


def stage_dict(settings: Settings) -> dict:
    """A dealt setup as a gauntlet stage stores it: pruned, as the board stores
    a pasted link's (`gauntlet-build.mjs` `stageSetup`), never a demo and never
    a score to beat."""
    out = settings.token_dict()
    out.pop("challenge", None)
    out.pop("autoplay", None)
    return out


def describe(settings: Settings) -> str:
    """A stage's setup in a line, for a generated stage's briefing: "Symmetric ·
    3 players · 24 systems · vs thinker, marshal"."""
    bots = [settings.seat_strategy(seat) for seat in range(2, settings.players + 1)]
    mode = "Symmetric" if settings.mode == "symmetric" else "Random"
    fog = " · fog" if settings.fog_sight < config.FOG_MAX_HOPS or settings.fog_scout < config.FOG_MAX_HOPS else ""
    return (f"{mode} · {settings.players} players · {settings.system_count()} systems{fog}"
            f" · vs {', '.join(dict.fromkeys(bots))}")
