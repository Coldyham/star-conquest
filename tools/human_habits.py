#!/usr/bin/env python3
"""How does the person play, set against the roster?

    uv run python tools/human_habits.py                 # posted games + self-play
    uv run python tools/human_habits.py --logs local    # the games/ dir instead
    uv run python tools/human_habits.py --seeds 1-20    # a smaller self-play set

Recorded human games (the replays a posted score made public, read with the
board's publishable key, or the local games/ dir) are replayed turn by turn, and
roster self-play is played alongside, since every posted game is a win and a
bot that lost to the person is no baseline. Four readings:

1. **Endgame markers.** The person's share of players' income, of players'
   ships, and of the whole board's income (neutral systems counted), turn by
   turn: when each crosses a threshold, which crosses first, and whether the
   lead later falls back below half. Self-play adds the losses: how often the
   first seat to reach a threshold wins.
2. **Waves.** Everything one side lands on a target it does not hold on one
   turn (this turn's launches and fleets already flying), over the target's
   effective garrison on arrival (`models/learner.py`'s `_effective`), by the
   side's share of ships. A whole wave, not one source's share, since several
   bots split a strike across sources.
3. **Relief.** Every voluntary frontier empty (90%+ of a garrison sent, no
   hostile fleet inbound bigger than the garrison): whether enough of the
   side's ships to beat the threat (inbound plus the largest adjacent enemy)
   can land before the earliest enemy, already flying or from a neighbour that
   gets there first; and whether the system is lost within five turns.
4. **Frontier losses**: systems lost per 100 frontier system-turns held.

The person's turns on autoplay are a bot's, so they count only where a reading
is about the person's own choices (waves, relief). docs/design/learner.md,
"The person's habits", has the first reading.
"""

from __future__ import annotations

import argparse
import copy
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, mapgen, replay, settings
from tests import sim
from tools.bot_distance import lineups
from tools.learner_check import ROSTER, _init, _seeds, load_logs

HUMAN = replay.HUMAN_SEAT
THRESHOLDS = (0.5, 0.6, 0.67, 0.75, 0.9)
MEASURES = ("players' income", "players' ships", "whole-board income")
SELF_PLAY = ((7, 2), (11, 2), (11, 3), (18, 3))    # (systems, seats), like the posted games
ALL_IN = 0.9            # a launch sending this share of a garrison empties it
LOST_WITHIN = 5         # turns after an empty in which a loss counts against it
COVER = ("inbound", "reachable", "short", "uncovered")
MAX_TURNS = 400


def _learner():
    return sys.modules["sc_model_learner"]


def shares(state) -> dict[int, tuple[float, float, float]]:
    """{pid: (share of players' income, share of players' ships, share of the
    whole board's income)}. A system's income is one over its production."""
    income, ships = defaultdict(float), defaultdict(float)
    board = 0.0
    for s in state.systems.values():
        if s.production > 0:
            board += 1.0 / s.production
        if s.owner_id == 0:
            continue
        ships[s.owner_id] += s.ships
        if s.production > 0:
            income[s.owner_id] += 1.0 / s.production
    for f in state.fleets:
        if f.owner_id != 0:
            ships[f.owner_id] += f.ships
    total_income, total_ships = sum(income.values()) or 1.0, sum(ships.values()) or 1.0
    return {pid: (income[pid] / total_income, ships[pid] / total_ships, income[pid] / (board or 1.0))
            for pid, p in state.players.items() if not p.is_neutral}


def _sent(state, orders) -> dict[int, dict[int, int]]:
    """Ships each source really launched at each destination, clamped as the
    engine clamps them."""
    sent: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for o in orders:
        src = state.systems.get(o.source_id)
        if src is not None and src.owner_id == o.owner_id and o.ships > 0:
            sent[o.source_id][o.dest_id] += min(o.ships, src.ships)
    return sent


def waves(state, sent) -> list[tuple[int, float, float]]:
    """(side, landing force / target's effective garrison, side's ship share) for
    every wave launched this turn at a target the side does not hold."""
    learner = _learner()
    snap = learner._Snap(state)
    share = shares(state)
    group: dict[tuple[int, int, int], int] = defaultdict(int)
    for src, outs in sent.items():
        me = state.systems[src].owner_id
        for dst, ships in outs.items():
            if state.systems[dst].owner_id != me:
                group[(me, dst, state.travel_turns(src, dst) or 1)] += ships
    out = []
    for (me, dst, turns), ships in sorted(group.items()):
        ships += sum(f.ships for f in state.fleets
                     if f.owner_id == me and f.dest_id == dst and f.turns_remaining == turns)
        out.append((me, ships / learner._effective(state, snap, dst, turns),
                    share.get(me, (0.0, 0.0, 0.0))[1]))
    return out


def _enemy_eta(state, sid: int, me: int) -> int | None:
    """The earliest turn an enemy can land on `sid`: a fleet already inbound, or
    a launch now from an adjacent enemy system with ships."""
    eta = None
    for f in state.fleets:
        if f.dest_id == sid and f.owner_id not in (0, me):
            eta = f.turns_remaining if eta is None else min(eta, f.turns_remaining)
    for n in state.systems[sid].neighbors:
        o = state.systems[n]
        if o.owner_id not in (0, me) and o.ships > 0:
            d = state.travel_turns(n, sid) or 1
            eta = d if eta is None else min(eta, d)
    return eta


def _cover(state, sid: int, me: int, eta: int, sources: set[int], kept: int, need: float) -> str:
    """"inbound" when what stayed plus our fleets landing by `eta` beat `need`;
    "reachable" when garrisons next door that can get there by then make it
    enough; "short" when there is some cover but not enough; else "uncovered"."""
    landing = kept + sum(f.ships for f in state.fleets
                         if f.dest_id == sid and f.owner_id == me and f.turns_remaining <= eta)
    if landing > need:
        return "inbound"
    reach = landing + sum(state.systems[n].ships for n in state.systems[sid].neighbors
                          if state.systems[n].owner_id == me and n not in sources
                          and (state.travel_turns(n, sid) or 1) <= eta)
    if reach > need:
        return "reachable"
    return "short" if reach > kept else "uncovered"


def empties(state, sent, skip=lambda pid: False) -> list[tuple[int, int, str]]:
    """(side, system, cover) for each voluntary frontier empty on this board."""
    adv = config.DEFENDER_ADVANTAGE
    out = []
    for sid, outs in sorted(sent.items()):
        s = state.systems[sid]
        me = s.owner_id
        total = sum(outs.values())
        if skip(me) or total < ALL_IN * s.ships:
            continue
        if not any(state.systems[n].owner_id not in (0, me) for n in s.neighbors):
            continue
        eta = _enemy_eta(state, sid, me)
        if eta is None:
            continue
        inbound = sum(f.ships for f in state.fleets
                      if f.dest_id == sid and f.owner_id not in (0, me))
        if inbound > s.ships * adv:
            continue                              # an evacuation, not a choice
        near = max((state.systems[n].ships for n in s.neighbors
                    if state.systems[n].owner_id not in (0, me)), default=0)
        out.append((me, sid, _cover(state, sid, me, eta, set(sent), s.ships - total,
                                    (inbound + near) / adv)))
    return out


def _outcomes(owners, events, label) -> list[tuple[str, str, bool]]:
    return [(label[me], how, any(o.get(sid) != me for o in owners[t + 1: t + 1 + LOST_WITHIN]))
            for t, me, sid, how in events]


def _frontier(owners, adjacency, label) -> tuple[Counter, Counter]:
    """Frontier system-turns held, and systems lost the next turn, by side."""
    held, lost = Counter(), Counter()
    for t in range(len(owners) - 1):
        now, after = owners[t], owners[t + 1]
        for sid, me in now.items():
            if me == 0 or not any(now[n] not in (0, me) for n in adjacency[sid]):
                continue
            held[label[me]] += 1
            lost[label[me]] += after.get(sid) != me
    return held, lost


def score_log(log):
    """One recorded game, read from the person's side."""
    owners, series, events, wave = [], [], [], []
    label: dict[int, str] = {}

    def on_turn(state):
        t = state.turn
        owners.append({sid: s.owner_id for sid, s in state.systems.items()})
        series.append((t, shares(state).get(HUMAN, (0.0, 0.0, 0.0))))
        if not label:
            label.update({pid: ("person" if pid == HUMAN else p.ai_strategy)
                          for pid, p in state.players.items() if not p.is_neutral})
        if state.winner is not None or t >= log.turn_count:
            return
        sent = _sent(state, log.orders_for(t))
        by_hand = not log.turn_is_ai(t)
        wave.extend((r, share) for me, r, share in waves(state, sent) if me == HUMAN and by_hand)
        events.extend((t, me, sid, how) for me, sid, how in
                      empties(state, sent, skip=lambda pid: pid == HUMAN and not by_hand))

    state, _settings = replay.reconstruct(log, on_turn=on_turn)
    adjacency = {sid: list(s.neighbors) for sid, s in state.systems.items()}
    held, lost = _frontier(owners, adjacency, label)
    return {"turns": state.turn, "won": state.winner == HUMAN, "series": series,
            "wave": wave, "relief": _outcomes(owners, events, label),
            "held": held, "lost": lost}


def play_game(job):
    """One roster self-play game, read from every seat's side; the winner's
    readings are labelled "(won)"."""
    seed, nodes, lineup = job
    # A replayed log writes its own balance knobs into `config` (`build_state`),
    # and this worker may have replayed one: self-play is at the defaults.
    settings._apply_globals(settings.Settings())
    state = mapgen.generate(seed, "random", nodes, len(lineup))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(lineup))
    adjacency = {sid: list(s.neighbors) for sid, s in state.systems.items()}
    series, owners, events, wave = [], [], [], []
    while state.winner is None and state.turn < MAX_TURNS:
        series.append((state.turn, shares(state)))
        owners.append({sid: s.owner_id for sid, s in state.systems.items()})
        issued: dict[int, list] = {}

        def recording(st, pid, issued=issued):
            issued[pid] = ai.decide(st, pid) or []
            return issued[pid]

        before = copy.deepcopy(state)
        engine.end_turn(state, decide=recording)
        sent = _sent(before, [o for orders in issued.values() for o in orders])
        wave.extend(waves(before, sent))
        events.extend((before.turn, me, sid, how) for me, sid, how in empties(before, sent))
    owners.append({sid: s.owner_id for sid, s in state.systems.items()})
    label = {pid: p.ai_strategy + (" (won)" if pid == state.winner else "")
             for pid, p in state.players.items() if not p.is_neutral}
    held, lost = _frontier(owners, adjacency, label)
    return {"winner": state.winner, "turns": state.turn, "series": series,
            "seats": sorted(label), "wave": [(label[me], r, share) for me, r, share in wave],
            "relief": _outcomes(owners, events, label), "held": held, "lost": lost}


# --------------------------------------------------------------------------- #
# Reading the results
# --------------------------------------------------------------------------- #
def _first(series, idx, x):
    return next((t for t, share in series if share[idx] >= x), None)


def markers(games, plays) -> None:
    print("\n== 1. the person's games: median turn each share first reaches x (as a share of "
          "the game), games where it later falls below half, games that never reach x")
    print("   x    " + "".join(f"{m:>30}" for m in MEASURES))
    for x in THRESHOLDS:
        cells = []
        for idx in range(len(MEASURES)):
            at, slip, never = [], 0, 0
            for g in games:
                t = _first(g["series"], idx, x)
                if t is None:
                    never += 1
                    continue
                at.append(t / max(1, g["turns"]))
                slip += any(u > t and share[idx] < 0.5 for u, share in g["series"])
            cells.append(f"{statistics.median(at):.2f}, falls {slip:>3}, never {never:>3}"
                         if at else "-")
        print(f"  {x:.2f}  " + "".join(f"{c:>30}" for c in cells))
    print("  which reaches x first (ties to the earlier column):")
    for x in THRESHOLDS:
        first = Counter()
        for g in games:
            ts = [_first(g["series"], idx, x) for idx in range(len(MEASURES))]
            if None not in ts:
                first[MEASURES[min(range(len(MEASURES)), key=lambda k: (ts[k], k))]] += 1
        print(f"    {x:.2f}  " + ", ".join(f"{m} {first[m]}" for m in MEASURES))

    print("\n== 1b. self-play: how often the first seat to reach x wins, games, median turns left")
    print("   x    " + "".join(f"{m:>30}" for m in MEASURES))
    for x in THRESHOLDS:
        cells = []
        for idx in range(len(MEASURES)):
            res = []
            for p in plays:
                if p["winner"] is None:
                    continue
                for t, share in p["series"]:
                    hit = [q for q in p["seats"] if share.get(q, (0.0, 0.0, 0.0))[idx] >= x]
                    if hit:
                        res.append((hit[0] == p["winner"], p["turns"] - t))
                        break
            cells.append(f"{sum(w for w, _ in res) / len(res):.3f}, n {len(res):>3}, "
                         f"left {statistics.median(n for _, n in res):>3.0f}" if res else "-")
        print(f"  {x:.2f}  " + "".join(f"{c:>30}" for c in cells))


def wave_table(games, plays) -> None:
    print("\n== 2. waves: landing force / target's effective garrison, median (count), "
          "by the side's share of ships")

    def line(who, rows):
        def med(xs):
            return f"{statistics.median(xs):.2f} ({len(xs)})" if xs else "-"
        bands = ([r for r, s in rows if s < 0.5], [r for r, s in rows if 0.5 <= s < 0.67],
                 [r for r, s in rows if s >= 0.67])
        print(f"  {who:<17} all {med([r for r, _ in rows]):<14} under 0.50 {med(bands[0]):<14}"
              f" 0.50-0.67 {med(bands[1]):<14} over 0.67 {med(bands[2])}")

    line("person", [x for g in games for x in g["wave"]])
    rows = defaultdict(list)
    for p in plays:
        for who, r, share in p["wave"]:
            rows[who].append((r, share))
    for who in sorted(rows):
        line(who, rows[who])


def relief_table(rows, title) -> None:
    print(title)
    print(f"  {'side':<17}{'empties':>8}   cover: {' / '.join(COVER)}"
          f"      lost within {LOST_WITHIN}: {' / '.join(COVER)}")
    by = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for who, how, lost in rows:
        by[who][how][0] += 1
        by[who][how][1] += lost
    for who in sorted(by, key=lambda w: (w != "person", w)):
        c = by[who]
        n = sum(v[0] for v in c.values())
        cover = " / ".join(f"{c[h][0] / n:>4.0%}" for h in COVER)
        lost = " / ".join(f"{c[h][1] / c[h][0]:>4.0%}" if c[h][0] else "   -" for h in COVER)
        print(f"  {who:<17}{n:>8}   {cover}          {lost}")


def frontier_table(games, plays) -> None:
    print("\n== 4. frontier systems lost per 100 frontier system-turns held")
    for name, rows in (("the person's games", games), ("self-play", plays)):
        held, lost = Counter(), Counter()
        for g in rows:
            held.update(g["held"])
            lost.update(g["lost"])
        print(f"  {name}: " + ", ".join(f"{who} {lost[who] / held[who] * 100:.1f}"
                                       for who in sorted(held, key=lambda w: (w != "person", w))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", choices=("public", "local"), default="public",
                        help="posted scores' public replays (default), or the local games/ dir")
    parser.add_argument("--min-hand", type=int, default=10,
                        help="skip a recorded game with fewer turns played by hand")
    parser.add_argument("--seeds", default="1-60", help="self-play seeds per cell")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)

    _init()
    if "sc_model_learner" not in sys.modules:
        print("not loaded: models/learner.py", file=sys.stderr)
        return 2
    logs = load_logs(args.logs, args.min_hand)
    seeds = _seeds(args.seeds)
    jobs = [(seed, nodes, tuple(lineup)) for nodes, seats in SELF_PLAY
            for seed, lineup in zip(seeds, lineups(list(ROSTER), seats, len(seeds)))]
    with ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as pool:
        games = list(pool.map(score_log, logs))
        plays = list(pool.map(play_game, jobs))
    print(f"{len(games)} {args.logs} logs with {args.min_hand}+ hand turns "
          f"({sum(g['won'] for g in games)} won by the person); {len(plays)} self-play games "
          f"on {', '.join(f'{n} systems/{s} seats' for n, s in SELF_PLAY)} "
          f"({sum(p['winner'] is None for p in plays)} hit {MAX_TURNS} turns)")
    markers(games, plays)
    wave_table(games, plays)
    relief_table([r for g in games for r in g["relief"]],
                 "\n== 3. voluntary frontier empties, the person's games")
    relief_table([r for p in plays for r in p["relief"]],
                 "\n== 3b. the same in self-play (\"(won)\": the seat that won that game)")
    frontier_table(games, plays)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
