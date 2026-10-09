#!/usr/bin/env python3
"""How well does actuary's model of each rival (Style: Learning) predict the
roster's launches?

    uv run python tools/learner_check.py                       # every cell, seeds 1-60
    uv run python tools/learner_check.py --cells 18n6 --seeds 1-4   # a smoke run
    uv run python tools/learner_check.py --fit-prior --seeds 1001-1040
    uv run python tools/learner_check.py --logs public          # predict people instead

Roster games are played with every seat on its own bot. Before each turn, for
every seat holding a system next to a live rival's (a contested position), four
predictors say which launches the rivals will make at that seat's systems:

* **none** — nobody launches (actuary's projection).
* **all** — every adjacent rival garrison comes, whole (actuary's risk reach).
* **prior** — the model with no memory: the prior, the same for every rival.
* **learner** — the model of each rival, from every turn it has watched.

The turn is then played and the rivals' real orders scored against each:

* **brier** — per (rival source, our target) pair, the squared gap between the
  predicted chance of a strike and whether one came. Lower is better.
* **mse** — per (rival, our target), the squared gap between the ships
  predicted to come (chance x ships) and the ships that came. Squared, since
  chance x ships is a mean: an absolute gap rewards the median, which is 0
  whenever fewer than half the pairs see a strike, so "none" would win it by
  construction.
* **waste** — ships predicted at a target nobody struck: guard held for nothing.
* **miss** — ships that came beyond the prediction.

Intervals are 95% bootstraps over games. The model never plays here; it only
watches. `--fit-prior` pools every rival's counts instead and prints the prior's
constants: `PRIOR_STRIKE` for models/actuary.py, the rest for this tool.

The strike curve is the part actuary plays from (`actuary.strike_curve`, kept in
actuary's memo tree). How big a strike is, what a frontier keeps home and
whether a doomed system leaves are read here only: actuary does not count them,
so `Watcher` counts them itself, turn by turn, from `actuary.launches`.

`--logs` replays recorded games instead: the replays a posted score made public
(read with the board's publishable key) or the local games/ dir. On every turn
the person played by hand, each bot seat next to them predicts their launches,
and a fifth predictor joins the four: **knower**, its blind plan for a human
seat (`knower._blind`), read as certain.
"""

from __future__ import annotations

import argparse
import copy
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, mapgen, replay
from tests import sim
from tools.bot_distance import lineups


# --------------------------------------------------------------------------- #
# The readings actuary does not keep: strike sizes, guard and evacuation
# --------------------------------------------------------------------------- #
ALL_IN = 0.9                # a strike sending this share of its garrison is all-in
GUARD_BINS = 21             # kept / largest adjacent enemy in tenths; the last is 2.0 and up
HABITS = ("allin", "size", "guard", "evac")
COUNTS = ("strikes", "passes", *HABITS)

# Fitted with PRIOR_STRIKE (`--fit-prior`, seeds 1001-1040).
PRIOR_ALLIN = 0.464
PRIOR_SIZE = (0.032, 0.050, 0.058, 0.043, 0.031, 0.053, 0.034, 0.023, 0.024, 0.005, 0.076,
              0.023, 0.059, 0.092, 0.061, 0.084, 0.039, 0.014, 0.013, 0.004, 0.073, 0.002,
              0.005, 0.005, 0.001, 0.007, 0.004, 0.002, 0.001, 0.000, 0.081)
PRIOR_GUARD = (0.517, 0.018, 0.019, 0.043, 0.036, 0.077, 0.068, 0.027, 0.017, 0.005, 0.059,
               0.008, 0.009, 0.006, 0.003, 0.008, 0.004, 0.002, 0.002, 0.000, 0.071)
PRIOR_EVAC = 0.531


def _actuary():
    return sys.modules["sc_model_actuary"]


def _no_habits() -> dict[str, list[float]]:
    return {"allin": [0.0, 0.0], "size": [0.0] * _actuary().RATIO_BINS,
            "guard": [0.0] * GUARD_BINS, "evac": [0.0, 0.0]}


class Full:
    """One rival's model as this tool reads it: actuary's strike counts and the
    habits counted here. `actuary.strike_curve` reads it like its own `Model`."""

    def __init__(self, base, habits):
        self.strikes, self.passes, self.turns = base.strikes, base.passes, base.turns
        h = habits or _no_habits()
        self.allin, self.size = tuple(h["allin"]), tuple(h["size"])
        self.guard, self.evac = tuple(h["guard"]), tuple(h["evac"])


class Watcher:
    """One game, watched turn by turn in order. actuary's memo tree holds the
    strike counts; this holds the habits, counted on the same turns, and only
    when actuary's node for the board follows from the last board seen."""

    def __init__(self):
        _actuary().reset()
        self.prev = None
        self.habits: dict[int, dict[str, list[float]]] = {}

    def see(self, state) -> None:
        ac = _actuary()
        node = ac._node_for(state)
        if node.parent is not None and self.prev is not None and node.parent[1] == self.prev.key:
            for q, seen in _observe_habits(self.prev, node.snap, state).items():
                mine = self.habits.setdefault(q, _no_habits())
                for name, xs in seen.items():
                    mine[name] = [a + b for a, b in zip(mine[name], xs)]
        self.prev = node.snap

    def models(self, state) -> dict:
        base = _actuary().models_for(state)
        empty = _actuary().EMPTY
        return {q: Full(base.get(q, empty), self.habits.get(q))
                for q in sorted(set(base) | set(self.habits))}


def _largest_enemy(snap, state, sid: int, q: int) -> int:
    return max((snap.ships[n] for n in state.systems[sid].neighbors
                if snap.owner[n] not in (0, q)), default=0)


def _share_bin(share: float, bins: int) -> int:
    return min(bins - 1, max(0, int(share * 10)))


def _observe_habits(prev, cur, state) -> dict[int, dict[str, list[float]]]:
    """What each player's launches on the turn from `prev` to `cur` say about
    strike size, guard and evacuation, as counts."""
    ac = _actuary()
    hostile = ac._hostile_inbound(prev)
    out: dict[int, dict[str, list[float]]] = {}
    for sid, (to, unknown) in ac.launches(prev, cur, state).items():
        q, garrison = prev.owner[sid], prev.ships[sid]
        c = out.setdefault(q, _no_habits())
        for option in ac._options(prev, state, sid):
            ships = to.get(option.target, 0)
            if ships <= 0:
                continue
            if ships >= ALL_IN * garrison:
                c["allin"][0] += 1
            else:
                c["allin"][1] += 1
                c["size"][ac._ratio_bin(ships / option.eff)] += 1
        launched = sum(to.values()) + unknown
        threat = _largest_enemy(prev, state, sid, q)
        if threat > 0 and launched > 0:
            c["guard"][_share_bin((garrison - launched) / threat, GUARD_BINS)] += 1
        if hostile.get(sid, 0) > garrison * config.DEFENDER_ADVANTAGE:
            c["evac"][0 if 2 * launched >= garrison else 1] += 1
    return out


def allin_rate(model: Full) -> float:
    """The share of strikes that send all the garrison."""
    allin, sized = model.allin
    w = _actuary().PRIOR_WEIGHT
    return (allin + w * PRIOR_ALLIN) / (allin + sized + w)


def size_ratio(model: Full) -> float:
    """What a strike that is not all-in sends, against its target, on average."""
    w = _actuary().PRIOR_WEIGHT
    weights = [model.size[b] + w * PRIOR_SIZE[b] for b in range(len(PRIOR_SIZE))]
    return sum(w * (b + 0.5) / 10 for b, w in enumerate(weights)) / sum(weights)


def strike_ships(model: Full, garrison: int, eff: float) -> float:
    """The ships a strike from `garrison` at a target of effective `eff` sends,
    on average."""
    allin = allin_rate(model)
    return allin * garrison + (1.0 - allin) * min(garrison, size_ratio(model) * eff)


def guard_share(model: Full) -> float:
    """What a frontier system that launched kept home, against its largest
    adjacent enemy garrison, at the median."""
    w = _actuary().PRIOR_WEIGHT
    weights = [model.guard[b] + w * PRIOR_GUARD[b] for b in range(GUARD_BINS)]
    half, run = sum(weights) / 2, 0.0
    for b, w in enumerate(weights):
        run += w
        if run >= half:
            return (b + 0.5) / 10
    return (GUARD_BINS - 0.5) / 10


def evac_rate(model: Full) -> float:
    left, stayed = model.evac
    w = _actuary().PRIOR_WEIGHT
    return (left + w * PRIOR_EVAC) / (left + stayed + w)


def _chances(model: Full, options: list, pressed: int, curves: dict) -> list[float]:
    """Each option's chance of a strike from one source, scaled to sum to at most
    one, since a source mostly strikes once."""
    ac = _actuary()
    chances = []
    for option in options:
        key = (id(model), option.kind, pressed)
        if key not in curves:
            curves[key] = ac.strike_curve(model, option.kind, pressed)
        chances.append(curves[key][ac._ratio_bin(option.ratio)])
    total = sum(chances)
    return [p / total for p in chances] if total > 1.0 else chances


def predict(state, pid: int, models: dict) -> list[tuple[int, int, int, float, float]]:
    """(rival, source, target, chance, ships if it strikes) for every launch a
    rival could make at `pid`'s systems this turn. `models` maps a rival to its
    `Full` model; ``{}`` is the prior alone."""
    ac = _actuary()
    snap = ac._Snap(state)
    mine = {sid for sid, owner in snap.owner.items() if owner == pid}
    hostile = ac._hostile_inbound(snap)
    empty = Full(ac.EMPTY, None)
    curves: dict = {}
    out = []
    for sid in sorted(snap.owner):
        q = snap.owner[sid]
        garrison = snap.ships[sid]
        if q in (0, pid) or garrison <= 0:
            continue
        if not any(n in mine for n in state.systems[sid].neighbors):
            continue
        model = models.get(q, empty)
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        options = ac._options(snap, state, sid)
        for option, p in zip(options, _chances(model, options, pressed, curves)):
            if option.target in mine:
                out.append((q, sid, option.target, p,
                            strike_ships(model, garrison, option.eff)))
    return out

CELLS = {"18n6": (18, 6.0), "24n3": (24, 3.0), "40n6": (40, 6.0), "18n18": (18, 18.0)}
ROSTER = ("claudebot", "thinker", "marshal", "actuary", "knower", "rusherplus", "heuristic")
PREDICTORS = ("none", "all", "prior", "learner")
GATE_BOTS = ("marshal", "actuary", "thinker")
WARMUP = ((0, 4), (5, 14), (15, 29), (30, 10**9))
METRICS = ("brier", "mse", "waste", "miss")
SEPARATION_TURN = 60
SEPARATION_RATIOS = (1.0, 1.5, 2.0)
HUMAN = "human"
SMALL_MAP = 11              # --logs reports maps of this many systems or fewer apart
EPS = 1e-3


def _init() -> None:
    from tools.bot_replay import BUDGET_SCALE
    ai.load_models()
    ai.set_budget_scale(BUDGET_SCALE)


def _bucket(since: int) -> str:
    for lo, hi in WARMUP:
        if lo <= since <= hi:
            return f"{lo}+" if hi > 10**8 else f"{lo}-{hi}"
    return "?"


def _contested(state, pid: int) -> bool:
    systems = state.systems
    return any(systems[n].owner_id not in (0, pid)
               for s in systems.values() if s.owner_id == pid for n in s.neighbors)


def _predictions(state, pid: int, watcher: Watcher, rival: int | None = None
                 ) -> dict[str, dict[tuple[int, int, int], tuple[float, float]]]:
    """{predictor: {(rival, source, target): (p, ships)}} over every contested pair,
    or only `rival`'s."""
    systems = state.systems
    pairs = {}
    for s in systems.values():
        q = s.owner_id
        if q in (0, pid) or (rival is not None and q != rival):
            continue
        for n in s.neighbors:
            if systems[n].owner_id == pid:
                pairs[(q, s.id, n)] = s.ships
    out = {"none": {k: (0.0, 0.0) for k in pairs},
           "all": {k: (1.0, float(g)) for k, g in pairs.items()}}
    for name, models in (("prior", {}), ("learner", watcher.models(state))):
        got = {k: (0.0, 0.0) for k in pairs}
        for q, src, dst, p, ships in predict(state, pid, models):
            if (q, src, dst) in got:
                got[(q, src, dst)] = (p, ships)
        out[name] = got
    return out


def play_game(job):
    """One game: per-predictor sums by (rival bot, warm-up bucket), the fitted
    parameters of every rival at the separation turn, and the raw counts."""
    seed, cell, lineup, max_turns = job
    nodes, speed = CELLS[cell]
    config.SHIP_LY_PER_TURN = speed
    watcher = Watcher()
    state = mapgen.generate(seed, "random", nodes, len(lineup))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(lineup))
    bot_of = {pid: p.ai_strategy for pid, p in state.players.items() if not p.is_neutral}
    first_contact: dict[int, int] = {}
    sums: dict = defaultdict(lambda: defaultdict(float))
    separation: list = []

    while state.winner is None and state.turn < max_turns:
        watcher.see(state)
        preds = {}
        for pid in sorted(bot_of):
            if state.is_defeated(pid) or not _contested(state, pid):
                continue
            first_contact.setdefault(pid, state.turn)
            preds[pid] = _predictions(state, pid, watcher)
        if state.turn == SEPARATION_TURN:
            separation += _describe(watcher.models(state), bot_of)
        owner_before = {sid: s.owner_id for sid, s in state.systems.items()}
        actual: dict[int, list] = {}

        def recording(st, pid, actual=actual):
            orders = ai.decide(st, pid) or []
            actual[pid] = orders
            return orders

        turn = state.turn
        engine.end_turn(state, decide=recording)

        for pid, by_predictor in preds.items():
            came = _came(actual, owner_before, pid)
            _score(sums, by_predictor, came, bot_of, _bucket(turn - first_contact[pid]))

    watcher.see(state)
    raw = [(bot_of[q], m) for q, m in watcher.models(state).items() if q in bot_of]
    return {"seed": seed, "cell": cell, "turns": state.turn,
            "sums": {k: dict(v) for k, v in sums.items()},
            "separation": separation,
            "raw": [(bot, {name: getattr(m, name) for name in COUNTS})
                    for bot, m in raw]}


def _came(actual: dict[int, list], owner_before: dict[int, int], pid: int):
    """Ships each rival really sent from a system it held at our system, per
    (rival, source, target)."""
    came: dict[tuple[int, int, int], int] = defaultdict(int)
    for q, orders in actual.items():
        if q == pid:
            continue
        for o in orders:
            if owner_before.get(o.source_id) == q and owner_before.get(o.dest_id) == pid:
                came[(q, o.source_id, o.dest_id)] += max(0, o.ships)
    return came


def _score(sums, by_predictor, came, label_of, bucket) -> None:
    for name, pairs in by_predictor.items():
        per_target: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for key, (p, ships) in pairs.items():
            q, _src, dst = key
            y = 1.0 if came.get(key, 0) > 0 else 0.0
            s = sums[(name, label_of[q], bucket)]
            s["pairs"] += 1
            s["strikes"] += y
            s["brier"] += (p - y) ** 2
            s["logloss"] -= math.log(max(EPS, p if y else 1.0 - p))
            per_target[(q, dst)][0] += p * ships
            per_target[(q, dst)][1] += came.get(key, 0)
        for (q, _dst), (want, got) in per_target.items():
            s = sums[(name, label_of[q], bucket)]
            s["targets"] += 1
            s["mse"] += (want - got) ** 2
            s["waste"] += want if got == 0 else 0.0
            s["miss"] += max(0.0, got - want)


# --------------------------------------------------------------------------- #
# Recorded human games (--logs)
# --------------------------------------------------------------------------- #
def load_logs(source: str, min_hand: int) -> list[replay.GameLog]:
    """Current-rules logs with at least `min_hand` turns played by hand: the
    replays a posted score made public (`public_replays`, the publishable key),
    or the local games/ dir."""
    if source == "local":
        from tools.position_suite import local_logs
        logs = local_logs()
    else:
        from tools.config_census import public_rows
        logs = []
        for row in public_rows("public_replays", "select=match_id,log&order=match_id"):
            try:
                logs.append(replay.GameLog.decode(row["log"]))
            except ValueError:
                continue
    return [log for log in logs if log.is_current and log.hand_turns >= min_hand]


def _borders(state, pid: int, rival: int) -> bool:
    systems = state.systems
    return any(systems[n].owner_id == rival
               for s in systems.values() if s.owner_id == pid for n in s.neighbors)


def _knower_guess(state, rival: int) -> dict[tuple[int, int, int], tuple[float, float]]:
    """What knower expects a person to launch: its own blind plan for their seat
    (`knower._surrogate` hands a human seat `_blind`), taken as certain."""
    knower = sys.modules["sc_model_knower"]
    out: dict[tuple[int, int, int], int] = defaultdict(int)
    for o in knower._blind(copy.deepcopy(state), rival) or []:
        if state.systems[o.source_id].owner_id == rival and o.ships > 0:
            out[(rival, o.source_id, o.dest_id)] += o.ships
    return {k: (1.0, float(v)) for k, v in out.items()}


def score_log(log: replay.GameLog):
    """One recorded game: on every turn the person played by hand, each bot seat
    next to them predicts their launches at it, scored against the log."""
    watcher = Watcher()
    human = replay.HUMAN_SEAT
    label_of = {human: HUMAN}
    first_contact: dict[int, int] = {}
    sums: dict = defaultdict(lambda: defaultdict(float))

    def on_turn(state):
        watcher.see(state)
        i = state.turn
        if (state.winner is not None or i >= log.turn_count or log.turn_is_ai(i)
                or human not in state.players or state.is_defeated(human)):
            return
        owner_before = {sid: s.owner_id for sid, s in state.systems.items()}
        actual = {human: [o for o in log.orders_for(i) if o.owner_id == human]}
        guess = None
        for pid in sorted(state.players):
            if (pid == human or state.players[pid].is_neutral or state.is_defeated(pid)
                    or not _borders(state, pid, human)):
                continue
            first_contact.setdefault(pid, i)
            preds = _predictions(state, pid, watcher, rival=human)
            if guess is None:
                guess = _knower_guess(state, human)
            preds["knower"] = {k: guess.get(k, (0.0, 0.0)) for k in preds["none"]}
            _score(sums, preds, _came(actual, owner_before, pid), label_of,
                   _bucket(i - first_contact[pid]))

    state, settings = replay.reconstruct(log, on_turn=on_turn)
    seats = {pid: (HUMAN if pid == human else p.ai_strategy)
             for pid, p in state.players.items() if not p.is_neutral}
    return {"seed": log.match_id, "cell": "small" if settings.nodes <= SMALL_MAP else "large",
            "turns": state.turn, "sums": {k: dict(v) for k, v in sums.items()},
            "separation": _describe(watcher.models(state), seats), "raw": []}


def report_humans(games) -> None:
    predictors = (*PREDICTORS, "knower")
    for label, group in (("all maps", games),
                         (f"{SMALL_MAP} systems or fewer", [g for g in games if g["cell"] == "small"]),
                         (f"more than {SMALL_MAP}", [g for g in games if g["cell"] == "large"])):
        if not group:
            continue
        rs = {p: _rates(_total(group, p)) for p in predictors}
        print(f"\n=== {label}: {len(group)} games, {rs['learner']['pairs']:.0f} pairs, "
              f"strike rate {rs['learner']['rate']:.3f}")
        print("  " + " " * 8 + "".join(f"{p:>9}" for p in predictors))
        for metric, fmt in (("brier", "{:>9.4f}"), ("mse", "{:>9.1f}"),
                            ("waste", "{:>9.2f}"), ("miss", "{:>9.2f}")):
            print(f"  {metric:<8}" + "".join(fmt.format(rs[p][metric]) for p in predictors))
        print("  warm-up, brier prior / learner / knower, since first contact:")
        for lo, _hi in WARMUP:
            b = _bucket(lo)
            r = {p: _rates(_total(group, p, None, b)) for p in ("prior", "learner", "knower")}
            print(f"    {b:>6}  {r['prior']['brier']:.4f} / {r['learner']['brier']:.4f} / "
                  f"{r['knower']['brier']:.4f}   ({r['learner']['pairs']:.0f} pairs)")
        print("  learner minus each, 95% interval over games (negative is better):")
        for metric in ("brier", "mse"):
            print(f"    {metric:<6}" + "  ".join(
                _interval(base, *_diff_ci(group, "learner", base, metric), metric)
                for base in ("none", "all", "prior", "knower")))


def _describe(models, bot_of):
    ac = _actuary()
    out = []
    for q, bot in sorted(bot_of.items()):
        m = models.get(q) or Full(ac.EMPTY, None)
        player = ac.strike_curve(m, ac.PLAYER, 0)
        neutral = ac.strike_curve(m, ac.NEUTRAL, 0)
        out.append((bot, m.turns, *(player[ac._ratio_bin(r)] for r in SEPARATION_RATIOS),
                    neutral[ac._ratio_bin(1.5)],
                    allin_rate(m), size_ratio(m), guard_share(m), evac_rate(m)))
    return out


# --------------------------------------------------------------------------- #
# Reading the results
# --------------------------------------------------------------------------- #
def _total(games, predictor, bots=None, bucket=None):
    t = defaultdict(float)
    for g in games:
        for (name, bot, b), s in g["sums"].items():
            if name != predictor or (bots and bot not in bots) or (bucket and b != bucket):
                continue
            for k, v in s.items():
                t[k] += v
    return t


def _rates(t) -> dict[str, float]:
    pairs, targets = t.get("pairs", 0), t.get("targets", 0)
    return {"brier": t.get("brier", 0) / pairs if pairs else float("nan"),
            "logloss": t.get("logloss", 0) / pairs if pairs else float("nan"),
            "mse": t.get("mse", 0) / targets if targets else float("nan"),
            "waste": t.get("waste", 0) / targets if targets else float("nan"),
            "miss": t.get("miss", 0) / targets if targets else float("nan"),
            "rate": t.get("strikes", 0) / pairs if pairs else float("nan"),
            "pairs": pairs}


def _diff_ci(games, a, b, metric, bots=None, draws=1000):
    """95% bootstrap interval, over games, of metric(a) - metric(b)."""
    rng = random.Random(12345)
    per_game = [(_rates(_total([g], a, bots)), _rates(_total([g], b, bots)),
                 _total([g], a, bots), _total([g], b, bots)) for g in games]
    per_game = [x for x in per_game if x[2].get("pairs", 0)]
    if not per_game:
        return float("nan"), float("nan"), float("nan")
    denom = "pairs" if metric in ("brier", "logloss") else "targets"

    def stat(sample):
        na = sum(x[2].get(denom, 0) for x in sample)
        nb = sum(x[3].get(denom, 0) for x in sample)
        if not na or not nb:
            return float("nan")
        return (sum(x[2].get(metric, 0) for x in sample) / na
                - sum(x[3].get(metric, 0) for x in sample) / nb)

    point = stat(per_game)
    boots = sorted(stat([per_game[rng.randrange(len(per_game))] for _ in per_game])
                   for _ in range(draws))
    return point, boots[int(0.025 * draws)], boots[int(0.975 * draws) - 1]


def report(games, cell) -> dict[str, bool]:
    games = [g for g in games if g["cell"] == cell]
    print(f"\n=== {cell}: {len(games)} games, "
          f"{statistics.mean(g['turns'] for g in games):.0f} turns on average")
    print(f"  {'rival':<11}{'pairs':>8}{'rate':>7}   " +
          "".join(f"{p:>8}" for p in PREDICTORS) + "    brier, then mse")
    bots = sorted({bot for g in games for (_n, bot, _b) in g["sums"]})
    for bot in [*bots, None]:
        label = bot or "all rivals"
        rs = {p: _rates(_total(games, p, {bot} if bot else None)) for p in PREDICTORS}
        print(f"  {label:<11}{rs['learner']['pairs']:>8.0f}{rs['learner']['rate']:>7.3f}   "
              + "".join(f"{rs[p]['brier']:>8.4f}" for p in PREDICTORS)
              + "   " + "".join(f"{rs[p]['mse']:>7.1f}" for p in PREDICTORS))
    rs = {p: _rates(_total(games, p)) for p in PREDICTORS}
    print("  all rivals  waste " + " ".join(f"{p} {rs[p]['waste']:.2f}" for p in PREDICTORS))
    print("              miss  " + " ".join(f"{p} {rs[p]['miss']:.2f}" for p in PREDICTORS))

    print("  warm-up (all rivals), brier prior / learner, since first contact:")
    for lo, hi in WARMUP:
        b = _bucket(lo)
        pr, rd = (_rates(_total(games, p, None, b)) for p in ("prior", "learner"))
        print(f"    {b:>6}  {pr['brier']:.4f} / {rd['brier']:.4f}   ({rd['pairs']:.0f} pairs)")

    verdicts = {}
    print("  learner minus each baseline, 95% interval (negative is better):")
    for bot in GATE_BOTS:
        ok = True
        for metric in ("brier", "mse"):
            parts = []
            for base in ("none", "all", "prior"):
                point, lo, hi = _diff_ci(games, "learner", base, metric, {bot})
                ok = ok and hi < 0
                parts.append(_interval(base, point, lo, hi, metric))
            print(f"    {bot:<9}{metric:<6}" + "  ".join(parts))
        verdicts[bot] = ok
        print(f"    {bot:<9}{'clears every baseline' if ok else 'does not clear'}")
    return verdicts


def _interval(label, point, lo, hi, metric) -> str:
    if metric == "brier":
        return f"{label} {point:+.4f} [{lo:+.4f},{hi:+.4f}]"
    return f"{label} {point:+.2f} [{lo:+.2f},{hi:+.2f}]"


def separation(games, when: str = f"at turn {SEPARATION_TURN}") -> None:
    rows = defaultdict(list)
    for g in games:
        for row in g["separation"]:
            rows[row[0]].append(row[1:])
    if not rows:
        return
    print(f"\n=== fitted parameters {when}, median over rivals "
          "(chance of a strike on a player at ratio 1.0 / 1.5 / 2.0, on a neutral at 1.5; "
          "all-in share; sized strike / target; guard; evac):")

    def med(xs):
        xs = [x for x in xs if x is not None]
        return f"{statistics.median(xs):.2f}" if xs else "  - "

    for bot in sorted(rows):
        r = rows[bot]
        print(f"  {bot:<11} n={len(r):<4} " + "  ".join(
            med([x[i] for x in r]) for i in range(1, 9)))


def _literal(name: str, values, indent: int = 0) -> str:
    """A tuple constant, wrapped at 11 values a line, ready to paste."""
    pad = " " * (indent + len(name) + 4) if name else " " * (indent + 1)
    lines, row = [], []
    for v in values:
        row.append(f"{v:.3f}")
        if len(row) == 11:
            lines.append(", ".join(row))
            row = []
    if row:
        lines.append(", ".join(row))
    head = f"{name} = (" if name else " " * indent + "("
    return head + (",\n" + pad).join(lines) + ")"


def fit_prior(games) -> None:
    ac = _actuary()
    empty = Full(ac.EMPTY, None)
    total = {name: [0.0] * len(getattr(empty, name)) for name in COUNTS}
    for g in games:
        for _bot, counts in g["raw"]:
            for name, xs in counts.items():
                total[name] = [a + b for a, b in zip(total[name], xs)]
    print("\n# models/actuary.py")
    print("PRIOR_STRIKE = (")
    for row in range(ac._ROWS):
        base = row * ac.RATIO_BINS
        hit = total["strikes"][base:base + ac.RATIO_BINS]
        seen = [h + m for h, m in zip(hit, total["passes"][base:base + ac.RATIO_BINS])]
        rates = [(h + 0.5) / (n + 1.0) for h, n in zip(hit, seen)]
        curve = ac._monotone(rates, [n + 1.0 for n in seen])
        print(f"    # {'player' if row // 2 else 'neutral'}, "
              f"{'pressed' if row % 2 else 'unpressed'}")
        print(_literal("", curve, 4) + ",")
    print(")")
    print("# tools/learner_check.py")
    allin, sized = total["allin"]
    print(f"PRIOR_ALLIN = {(allin + 0.5) / (allin + sized + 1.0):.3f}")
    for name, xs in (("PRIOR_SIZE", total["size"]), ("PRIOR_GUARD", total["guard"])):
        norm = sum(xs) or 1.0
        print(_literal(name, [x / norm for x in xs]))
    left, stayed = total["evac"]
    print(f"PRIOR_EVAC = {(left + 0.5) / (left + stayed + 1.0):.3f}")
    print(f"# from {len(games)} games: {sum(total['strikes']):.0f} strikes, "
          f"{sum(total['passes']):.0f} passes")


def _seeds(text: str) -> list[int]:
    lo, _, hi = text.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cells", nargs="+", default=["all"],
                        help=f"cells to play: {', '.join(CELLS)} or all")
    parser.add_argument("--seeds", default="1-60", help="seed range, e.g. 1-60")
    parser.add_argument("--players", type=int, default=3)
    parser.add_argument("--bots", nargs="+", default=list(ROSTER))
    parser.add_argument("--max-turns", type=int, default=300)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--fit-prior", action="store_true",
                        help="pool every rival's counts and print the prior's constants")
    parser.add_argument("--logs", choices=("public", "local"), default=None,
                        help="score predictions of the person in recorded games instead: "
                             "posted scores' public replays, or the local games/ dir")
    parser.add_argument("--min-hand", type=int, default=10,
                        help="with --logs, skip a game with fewer turns played by hand")
    args = parser.parse_args(argv)
    if args.logs:
        return main_logs(args)

    cells = list(CELLS) if "all" in args.cells else args.cells
    unknown = [c for c in cells if c not in CELLS]
    if unknown:
        print(f"unknown cells: {', '.join(unknown)}", file=sys.stderr)
        return 2
    _init()
    missing = [b for b in args.bots if b not in ai.STRATEGIES]
    if missing or "sc_model_actuary" not in sys.modules:
        print(f"not loaded: {', '.join(missing) or 'models/actuary.py'}", file=sys.stderr)
        return 2

    seeds = _seeds(args.seeds)
    jobs = [(seed, cell, tuple(lineup), args.max_turns)
            for cell in cells
            for seed, lineup in zip(seeds, lineups(args.bots, args.players, len(seeds)))]
    started = time.time()
    games = []
    with ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as pool:
        for i, game in enumerate(pool.map(play_game, jobs), 1):
            games.append(game)
            print(f"  {i}/{len(jobs)} games · {time.time() - started:.0f}s", end="\r", flush=True)
    print(f"{len(games)} games in {time.time() - started:.0f}s, {args.players} seats, "
          f"bots: {', '.join(args.bots)}")

    if args.fit_prior:
        fit_prior(games)
        return 0
    passed = sum(all(report(games, cell).values()) for cell in cells)
    separation(games)
    print(f"\ngate: learner clears every baseline against {', '.join(GATE_BOTS)} "
          f"in {passed} of {len(cells)} cells (needs 3 of 4)")
    return 0


def main_logs(args) -> int:
    _init()
    if "sc_model_actuary" not in sys.modules or "sc_model_knower" not in sys.modules:
        print("not loaded: models/actuary.py or models/knower.py", file=sys.stderr)
        return 2
    logs = load_logs(args.logs, args.min_hand)
    started = time.time()
    games = []
    with ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as pool:
        for i, game in enumerate(pool.map(score_log, logs), 1):
            games.append(game)
            print(f"  {i}/{len(logs)} logs · {time.time() - started:.0f}s", end="\r", flush=True)
    print(f"{len(games)} {args.logs} logs with {args.min_hand}+ hand turns, "
          f"in {time.time() - started:.0f}s")
    report_humans(games)
    separation(games, "at the end of each log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
