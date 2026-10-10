#!/usr/bin/env python3
"""How one seat plays one game: the readings behind the board's Playstyle panel.

    uv run python tools/playstyle.py games/game_<stamp>_<seed>.json   # one log's readings
    uv run python tools/playstyle.py --baseline                      # regenerate the roster column
    uv run python tools/playstyle.py --baseline --seeds 1-10         # ...on a smaller set

`readings(log)` replays a recorded game (asking no seat to decide) and reads it
from the person's seat. `tools/playstyle_worker.py` stores one row per posted
replay in `playstyle_readings`; `tools/human_habits.py` prints the same readings
as tables. Every field sums across games, so the board pools a player's rows by
adding them (`leaderboard/js/playstyle.mjs`, `pool`), and the roster column is
one pooled record, `leaderboard/js/playstyle-baseline.mjs`, written by
`--baseline` from the winning seat of each self-play game. Regenerate that file;
don't hand-edit it.

A record (`SHAPE` says which layout; the board reads no other):

    games, turns, hand_turns    counts; `turns` is a list, one entry per game
    waves       [band][bin]: waves landed on a target the side does not hold, by
                force / the target's effective garrison on arrival in tenths
                (the last bin is WAVE_TOP and up), by the side's share of ships
                (SHARE_BANDS); hand turns only
    relief      {cover: [empties, lost within LOST_WITHIN turns]} for voluntary
                frontier empties; hand turns only
    frontier    [frontier system-turns held, of them lost the next turn]
    reach       {measure: [[share of the game when it first reached x, ...] per x
                in THRESHOLDS]}; a game that never reached x adds nothing there,
                and `never` counts it
    never       {measure: [games that never reached x, per x]}
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config, engine, mapgen, replay, settings

HUMAN = replay.HUMAN_SEAT
SHAPE = 1
THRESHOLDS = (0.5, 0.6, 0.67, 0.75, 0.9)
MEASURES = ("players' income", "players' ships", "whole-board income")
MEASURE_KEYS = ("income", "ships", "board")
SHARE_BANDS = (0.5, 0.67)   # under 0.5 / 0.5-0.67 / 0.67 and over
WAVE_TOP = 4.0
WAVE_BINS = int(WAVE_TOP * 10) + 1
ALL_IN = 0.9                # a launch sending this share of a garrison empties it
LOST_WITHIN = 5             # turns after an empty in which a loss counts against it
COVER = ("inbound", "reachable", "short", "uncovered")
SELF_PLAY = ((7, 2), (11, 2), (11, 3), (18, 3))    # (systems, seats), like the posted games
MAX_TURNS = 400
BASELINE = ROOT / "leaderboard" / "js" / "playstyle-baseline.mjs"
_REV_SOURCES = (Path(__file__).resolve(), ROOT / "models" / "actuary.py")


def reading_rev() -> str:
    """Digest of the code a reading depends on: this file, and actuary's
    `_Snap`/`_effective`, which `waves` prices a garrison with."""
    h = hashlib.blake2b(digest_size=8)
    for path in _REV_SOURCES:
        h.update(path.read_bytes())
    return h.hexdigest()


def _actuary():
    return sys.modules["sc_model_actuary"]


def load() -> None:
    """Register the roster, actuary among it, before any reading."""
    if "sc_model_actuary" not in sys.modules:
        ai.load_models()


# --------------------------------------------------------------------------- #
# What one board shows
# --------------------------------------------------------------------------- #
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
    ac = _actuary()
    snap = ac._Snap(state)
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
        out.append((me, ships / ac._effective(state, snap, dst, turns),
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


def _first(series, idx, x):
    return next((t for t, share in series if share[idx] >= x), None)


# --------------------------------------------------------------------------- #
# One game, every seat at once
# --------------------------------------------------------------------------- #
class Reader:
    """Every seat's readings, fed each board with what was launched from it."""

    def __init__(self) -> None:
        self.owners: list[dict[int, int]] = []
        self.series: list[tuple[int, dict]] = []
        self.events: list[tuple[int, int, int, str]] = []
        self.waves: list[tuple[int, float, float]] = []

    def see(self, state, sent=None, by_hand=lambda pid: True) -> None:
        """One board; `sent` is what left it this turn (None for the last), and
        `by_hand(pid)` whether that seat's launches were its own choice."""
        self.owners.append({sid: s.owner_id for sid, s in state.systems.items()})
        self.series.append((state.turn, shares(state)))
        if sent is None:
            return
        self.waves.extend(w for w in waves(state, sent) if by_hand(w[0]))
        self.events.extend((state.turn, me, sid, how) for me, sid, how in
                           empties(state, sent, skip=lambda pid: not by_hand(pid)))

    def reading(self, state, pid: int) -> dict:
        """The record for one seat, once the game is over."""
        adjacency = {sid: list(s.neighbors) for sid, s in state.systems.items()}
        label = {q: q for q in state.players}
        held, lost = _frontier(self.owners, adjacency, label)
        bands = [[0] * WAVE_BINS for _ in range(len(SHARE_BANDS) + 1)]
        for me, ratio, share in self.waves:
            if me == pid:
                band = sum(share >= edge for edge in SHARE_BANDS)
                bands[band][min(WAVE_BINS - 1, int(ratio * 10))] += 1
        relief = {how: [0, 0] for how in COVER}
        for who, how, gone in _outcomes(self.owners, self.events, label):
            if who == pid:
                relief[how][0] += 1
                relief[how][1] += gone
        turns = max(1, state.turn)
        mine = [(t, share.get(pid, (0.0, 0.0, 0.0))) for t, share in self.series]
        reach, never = {}, {}
        for idx, key in enumerate(MEASURE_KEYS):
            firsts = [_first(mine, idx, x) for x in THRESHOLDS]
            reach[key] = [[] if t is None else [round(t / turns, 3)] for t in firsts]
            never[key] = [int(t is None) for t in firsts]
        return {"shape": SHAPE, "games": 1, "turns": [state.turn], "hand_turns": 0,
                "waves": bands, "relief": relief, "frontier": [held[pid], lost[pid]],
                "reach": reach, "never": never}


def readings(log: replay.GameLog) -> dict:
    """The person's record for one recorded game. Turns their seat spent on
    autoplay are a bot's, so they count for nothing chosen (waves, relief)."""
    load()
    reader = Reader()

    def on_turn(state):
        t = state.turn
        if state.winner is not None or t >= log.turn_count:
            reader.see(state)
            return
        by_hand = not log.turn_is_ai(t)
        reader.see(state, _sent(state, log.orders_for(t)),
                   by_hand=lambda pid: pid != HUMAN or by_hand)

    state, _ = replay.reconstruct(log, on_turn=on_turn)
    out = reader.reading(state, HUMAN)
    out["hand_turns"] = log.hand_turns
    return out


def pool(records) -> dict:
    """Records added together; `leaderboard/js/playstyle.mjs` does the same."""
    out: dict | None = None
    for r in records:
        if out is None:
            out = copy.deepcopy(r)
            continue
        out["games"] += r["games"]
        out["turns"] += r["turns"]
        out["hand_turns"] += r["hand_turns"]
        for band, row in enumerate(r["waves"]):
            out["waves"][band] = [a + b for a, b in zip(out["waves"][band], row)]
        for how, pair in r["relief"].items():
            out["relief"][how] = [a + b for a, b in zip(out["relief"][how], pair)]
        out["frontier"] = [a + b for a, b in zip(out["frontier"], r["frontier"])]
        for key in MEASURE_KEYS:
            out["reach"][key] = [a + b for a, b in zip(out["reach"][key], r["reach"][key])]
            out["never"][key] = [a + b for a, b in zip(out["never"][key], r["never"][key])]
    return out or {}


# --------------------------------------------------------------------------- #
# The roster column
# --------------------------------------------------------------------------- #
def play_game(job) -> dict | None:
    """One roster self-play game; the winner's record, or None if nobody won."""
    seed, nodes, lineup = job
    from tests import sim
    settings._apply_globals(settings.Settings())
    state = mapgen.generate(seed, "random", nodes, len(lineup))
    for p in state.players.values():
        p.is_human = False
    sim._assign_strategies(state, list(lineup))
    reader = Reader()
    while state.winner is None and state.turn < MAX_TURNS:
        issued: dict[int, list] = {}

        def recording(st, pid, issued=issued):
            issued[pid] = ai.decide(st, pid) or []
            return issued[pid]

        before = copy.deepcopy(state)
        engine.end_turn(state, decide=recording)
        reader.see(before, _sent(before, [o for orders in issued.values() for o in orders]))
    reader.see(state)
    if state.winner is None:
        return None
    return reader.reading(state, state.winner)


def _init() -> None:
    from tools.bot_replay import BUDGET_SCALE
    ai.load_models()
    ai.set_budget_scale(BUDGET_SCALE)


def baseline(seeds: list[int], jobs: int) -> dict:
    from tools.bot_distance import lineups
    from tools.learner_check import ROSTER
    work = [(seed, nodes, tuple(lineup)) for nodes, seats in SELF_PLAY
            for seed, lineup in zip(seeds, lineups(list(ROSTER), seats, len(seeds)))]
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init) as pool_:
        records = [r for r in pool_.map(play_game, work) if r is not None]
    return pool(records)


def write_baseline(record: dict, seeds: list[int]) -> None:
    from tools.learner_check import ROSTER
    cells = ", ".join(f"{n} systems/{s} seats" for n, s in SELF_PLAY)
    BASELINE.write_text(
        "// Generated by `uv run python tools/playstyle.py --baseline`: regenerate, don't edit.\n"
        f"// The winning seat of each roster self-play game ({', '.join(ROSTER)}),\n"
        f"// seeds {seeds[0]}-{seeds[-1]} on {cells}, pooled.\n"
        f"export const BASELINE_REV = {json.dumps(reading_rev())};\n"
        f"export const BASELINE = {json.dumps(record, separators=(',', ':'))};\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="*", help="game log files to read")
    parser.add_argument("--baseline", action="store_true",
                        help=f"regenerate {BASELINE.relative_to(ROOT)} from roster self-play")
    parser.add_argument("--seeds", default="1-60", help="self-play seeds per cell")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)

    if args.baseline:
        from tools.learner_check import _seeds
        seeds = _seeds(args.seeds)
        record = baseline(seeds, args.jobs)
        write_baseline(record, seeds)
        print(f"{record.get('games', 0)} won games pooled into {BASELINE.relative_to(ROOT)}")
        return 0
    if not args.logs:
        parser.error("give log files, or --baseline")
    for path in args.logs:
        log = replay.load(Path(path))
        print(json.dumps(readings(log)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
