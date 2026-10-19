"""actuary — prices every launch against a projected ledger of the whole board.

Every other bot in the roster from claudebot up is a sequence of phases over the
systems it owns: budget each one, defend, strike, flow the rest to the front.
actuary has no phases and no per-system target loop. It keeps one number, the
board's worth in ships, and asks of each possible launch how much that number
moves.

  * **A forecast, not a snapshot.** Each system gets a timeline: owner and garrison
    for every turn up to a horizon, from the fleets already flying, production
    and the engine's own fold order at a pile-up. Fights in it are priced
    through `combat.edge_attacking`/`edge_defending`: a launch only counts as a
    capture if it wins the worst roll, and then keeps what the nominal roll
    would leave it. Lanes never interact, so
    a system's timeline depends only on what lands there: a candidate launch
    re-projects its source and destination and nothing else.
  * **One ledger.** At the horizon every ship we hold is worth one ship (a little
    more the nearer it stands to a front), every system we hold is worth
    `TAIL_TURNS` turns of its income, and a rival's are worth the same against
    us. Income before the horizon is already in the garrisons it built. A rival's
    garrison next door is a risk to each system we hold: the shortfall against
    everything it could land, priced as a share of what that system is worth.
  * **Greedy on the margin.** Candidates are single launches (all the spare
    ships, half, or exactly enough to change who holds the destination) and
    same-arrival coalitions on a target no single source can take. The one that
    raises the ledger most is committed and the projection updated, until no
    candidate pays. Defence, evacuation, strikes and logistics are not written
    anywhere; they are what the margin chooses.

The seat's ``ai_params.aux`` knob (``AUX_LABEL``: *Style*) has four stops.

  * *Greedy* pulls its ships to every front alike: a ship on a system touching
    anything not ours is worth 1 + `FRONT_BONUS`, falling by `FRONT_DECAY` per
    turn of travel back.
  * *Field*, the default, pulls them to where the balance of force is closest
    (`_Ledger._field`). Every garrison, and every fleet in flight at the system
    it will land on, projects its ships over what it could reach, keeping
    `FIELD_DECAY` of them per median lane out to `FIELD_REACH` lanes. A system's
    stake, `TAIL_TURNS` of its income plus a rival garrison on it, counts for
    exp(-x^2), x the gap between our influence and the rivals' over
    `CONTEST_WIDTH` of their sum; our interior has none. A ship at one of our
    systems is worth 1 + `FRONT_BONUS` times the contested stake within its
    reach, decayed the same way, as a share of the most any system has. A front
    nobody contests stops holding a stack, and the ships go where a few more of
    them tip a system. Where the median lane is `SLOW_LANE` turns or more the
    pull is half the field and half Greedy's front.
  * *Learning* is Field reading each rival's habits into the risk term
    (`_learned_shortfall`, docs/design/learning.md). It remembers what it has
    seen, so it is the one stop an oracle must model rather than run.
  * *Hunt* guards only against the strikes a rival would launch, and looks a few
    turns ahead (docs/design/hunter.md):

      - **Edge risk.** The threat next door is only a strike a rival launches by
        actuary's own rule: one system next door, or several whose lanes take
        the same time, that beats what we hold on landing *on the worst roll*,
        launched now or on any later turn of the horizon (`_edge_risk`).
        actuary's launches meet that rule 98-100% of the time, so a garrison
        above it is safe from actuary, and the ships it would keep go to the
        front. The shortfall share costs `EDGE_RISK_WEIGHT` of the stake.
      - **Gathering, on fast boards.** Where the median lane is at most
        `EDGE_GATHER_LANE` turns, a rival system next door also counts the
        rival garrisons beside it that could have joined it by the launch turn.
      - **Softer strikes, on slow boards.** Where the median lane is at least
        `CAPTURE_LANE` turns, a strike on a rival-held system is priced at the
        nominal roll (`CAPTURE_SWING`): actuary guards only to it next door.
      - Relief a little heavier (`HUNT_REINFORCE_WEIGHT`), none from fleets in
        flight (`HUNT_RELIEF_IN_FLIGHT`), and on slow lanes Greedy's front
        alone (`HUNT_SLOW_FRONT`).
      - **Look-ahead.** The plan and the ledger at other postures are each
        played `LOOK_PLIES` turns on a private board with its own dice, every
        rival on Hunt's plain plan, and the best final ledger wins (`_look`).
        Bounded by work (`LOOK_WORK`), so it runs on boards up to about 40
        systems and plays the same on any machine.

    It runs no other bot's code and keeps no memory, so it is its own stand-in.

A stored value past Hunt reads as Learning (``AUX_OVER``): Hunt is reached only
by its own value.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and draws nothing from ``state.rng``: every tie breaks on system id.
Measurements behind the constants: docs/design/actuary.md.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import math
import random
import time
from collections import Counter, OrderedDict, defaultdict
from typing import NamedTuple

from starconquest import combat, config, engine
from starconquest.model import Order

# --- tunables -------------------------------------------------------------- #
TUNED_SWING = 1.1 / 0.9     # the +/-10% swing the margins were fitted at: a floor
                            # under the live edge's jitter half, never an answer
HORIZON_PAD = 3             # turns projected past the longest lane on the board
HORIZON_MAX = 40            # an iteration bound for a pathological hand map
TAIL_TURNS = 24.0           # a held system is worth this many turns of its income
FRONT_BONUS = 0.5           # a ship standing on a front is worth 1 + this...
FRONT_DECAY = 0.5           # ...falling by this factor per turn of travel back
RISK_WEIGHT = 0.6           # share of a system's worth lost to a full shortfall
REINFORCE_WEIGHT = 0.3      # share of a rival's adjacent ships assumed to relieve
                            # a system we strike, if they can land in time
RELIEF_IN_FLIGHT = 0.3      # ...and of a rival's fleet landing next door that
                            # could turn round and land in time
MIN_GAIN = 1e-9             # a candidate must move the ledger by more than this; any
                            # floor costs, most in the slow regime ("The 2026-10 sweep")
MAX_COMMITS = 64            # an iteration bound on the greedy
SELF_RELIEF = 0.8           # share of our own neighbouring garrisons counted as
                            # able to relieve a system the risk term reads (see
                            # "A front stack pins the whole side" in actuary.md)
SELF_RELIEF_REACTS = True   # ...counting a neighbour only if its lane is shorter than
                            # the shortest rival lane into the system, so relief
                            # launched after the strike is seen still lands first

# What one decide costs (`decide_ms`): 75th-percentile CPU ms on native CPython,
# fitted to a grid of 18-120 systems, 3-18 ly/turn and 2-5 seats (see "Cost, and
# the caches that make it affordable" in docs/design/actuary.md). Seats and ship
# speed barely move it, so only the system count is in the fit. Refitted for
# MIN_GAIN 0, which commits more launches on big maps ("What it costs").
COST_REF_NODES = 40
COST_DECIDE_MS = 8.3
COST_NODES_EXP = 0.77

# --- the Style knob ---------------------------------------------------------- #
GREEDY, FIELD, LEARNING, HUNT = 0, 1, 2, 3  # the Style knob's stops; anything
                                            # unreadable is FIELD

# --- the field (Style: Field and Learning; "The field" in docs/design/actuary.md)
FIELD_DECAY = 0.45          # influence kept per median lane of travel
FIELD_REACH = 3.0           # ...counted out to this many median lanes
CONTEST_WIDTH = 0.5         # the gap, as a share of both sides' influence, at
                            # which a system's stake falls to 1/e
SLOW_LANE = 5               # a median lane this many turns or more is slow: there
                            # the pull is half the field and half Greedy's front
                            # ("Slow lanes: half the front" in docs/design/field.md)

# --- Style: Hunt (docs/design/hunter.md); every switch off is Field ---------- #
EDGE_RISK = True            # price a threat as actuary launches one: a source next door,
                            # or several landing on the same turn, that wins the worst
                            # roll (tools/hunt_predict.py: recall 0.98-1.00)...
EDGE_LOOK = 40              # ...launched this turn or up to this many turns later (1
                            # measured -10 pts: a front thins faster than it refills)...
EDGE_RISK_WEIGHT = 1.0      # ...a full shortfall costing this share of the stake (2.5
                            # and the fast-board 1.5 measured worse)
EDGE_SWING = 0.0            # >0: the swing a rival's strike must clear in place of the
                            # worst roll's (1.0 = the nominal roll); 1.0 and 1.1 were
                            # within noise against four bots
EDGE_GATHER_LANE = 2        # on a board whose median lane is at most this many turns, a
                            # rival system next door also counts, by launch turn k, the
                            # rival garrisons beside it that could have joined it by
                            # then (18:18 +8.2 pts; on every board -3.2); 0 = never
CAPTURE_SWING = 1.0         # >0: the swing our strike on a rival-held system is priced
                            # at (1.0 = it wins the nominal roll) in place of the worst
                            # roll: actuary guards only to the nominal roll next door...
CAPTURE_LANE = 6            # ...on a board whose median lane is at least this many turns
                            # (13:3 +7.8 pts; on 7:6 it cost 7-13); 0 = every board
HUNT_REINFORCE_WEIGHT = 0.45  # `REINFORCE_WEIGHT` at Hunt (0.45-0.7 is a plateau, ~+2
                              # pts against actuary)...
HUNT_RELIEF_IN_FLIGHT = 0.0   # ...`RELIEF_IN_FLIGHT` (0 is Hunt as measured)...
HUNT_SLOW_FRONT = 1.0       # ...and Greedy's front's share of the pull on slow lanes
                            # (Field 0.5; 1, the front alone, measured +6.9 pts over
                            # the field, 0.5 null and 0 -1.9: on slow boards the
                            # look-ahead wants the front)

LOOKAHEAD = True            # roll each candidate plan forward and keep the best
LOOK_PLIES = 6              # turns each candidate is played on before it is scored
LOOK_WEIGHTS = (0.4, 2.5)   # candidate postures besides ours: edge risk weight times these...
LOOK_CAPTURE = True         # ...and our plan with the nominal-roll strike on every board
LOOK_DECIDED = 1e6          # a rollout that ends the game scores this, won or lost
LOOK_WORK = 4000            # work per decide: a rollout ply costs systems ** 1.3 x seats
                            # (how a ledger decide grows); a line that would pass it is not
                            # rolled. Work, not time, so a seat plays the same on any machine
LOOK_BUDGET_S = 1.0         # a catastrophe guard on the wall clock, as knower's: never the
                            # budget that decides. The base plan is rolled first, so running
                            # out of either only ever falls back to the plain plan
BUDGET_SCALE = 1.0          # `ai.set_budget_scale` multiplies LOOK_BUDGET_S by this

# What a Hunt decide costs (`decide_ms`), fitted as above against actuary over the
# same grid (tools/hunt_cost.py; docs/design/hunter.md, "What it costs"): the plain
# ledger, and COST_LOOK_MS per unit of the work the look-ahead is allowed.
HUNT_COST_DECIDE_MS = 10.2
HUNT_COST_NODES_EXP = 1.28
COST_LOOK_MS = 0.025

# --- reading the rivals (Style: Learning) ------------------------------------ #
LEARN_REACH = 8.0           # a rival garrison next door strikes whole at min(1, this times
                            # its learned chance of striking a garrison of ours that thin)
NODE_CAP = 512              # boards remembered, least recently used dropped first
RATIO_BINS = 31             # a ratio in tenths; the last bin is 3.0 and up
PRIOR_WEIGHT = 4.0          # observations the prior is worth, per bin
NEUTRAL, PLAYER = 0, 1      # target kinds
_ROWS = 4                   # (kind, pressed) pairs, row = kind * 2 + pressed

# Pooled over the roster in self-play, all four cells, seeds 1001-1040
# (`tools/learner_check.py --fit-prior`). A rival nobody has watched yet is
# assumed to play like the average of the roster.
PRIOR_STRIKE = (
    # neutral, unpressed
    (0.004, 0.007, 0.009, 0.012, 0.018, 0.018, 0.020, 0.020, 0.020, 0.023, 0.023,
     0.024, 0.038, 0.192, 0.207, 0.290, 0.290, 0.290, 0.324, 0.333, 0.333, 0.333,
     0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333),
    # neutral, pressed
    (0.008, 0.008, 0.017, 0.017, 0.017, 0.017, 0.017, 0.017, 0.023, 0.023, 0.023,
     0.023, 0.036, 0.091, 0.091, 0.091, 0.167, 0.167, 0.250, 0.379, 0.379, 0.379,
     0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379),
    # player, unpressed
    (0.034, 0.034, 0.034, 0.035, 0.035, 0.040, 0.040, 0.040, 0.040, 0.040, 0.061,
     0.061, 0.100, 0.128, 0.128, 0.155, 0.212, 0.224, 0.254, 0.254, 0.278, 0.278,
     0.325, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329),
    # player, pressed
    (0.057, 0.077, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.131,
     0.131, 0.168, 0.177, 0.218, 0.286, 0.286, 0.297, 0.297, 0.336, 0.375, 0.375,
     0.375, 0.375, 0.375, 0.375, 0.375, 0.426, 0.426, 0.426, 0.445),
)

# What the AI tab's generic aux slider is called when this bot holds the seat (read
# by `ai.aux_spec`/`ai.aux_names`; see models/README.md). The default 1.0 is Field.
# ``AUX_OVER`` is the stop a stored value past the top reads as (`ai.aux_over`).
AUX_LABEL = "Style"
AUX_RANGE = (GREEDY, HUNT, 1)
AUX_INT = True
AUX_NAMES = ("Greedy", "Field", "Learning", "Hunt")
AUX_OVER = LEARNING


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    stop = _stop_of(state.players[pid])
    if stop == HUNT:
        return _look(state, pid) if LOOKAHEAD else _Ledger(state, pid, field=True, hunt=True).plan()
    # Learning reads every board into its memory, the opening's included, so the
    # model of each rival is warm by contact.
    curves = _curves(state) if stop == LEARNING else None
    return _Ledger(state, pid, curves, field=stop != GREEDY).plan()


def _stop_of(player) -> int:
    """This seat's Style stop. Anything at or past a stop is that stop, past Hunt
    is Learning (``AUX_OVER``), and anything unreadable is the default, so a
    hand-edited token cannot take the bot down."""
    return _params_stop(getattr(player, "ai_params", None))


def _params_stop(params) -> int:
    try:
        aux = float(params.aux)
    except Exception:                     # noqa: BLE001
        return FIELD
    if aux > HUNT:
        return AUX_OVER
    if aux >= HUNT:
        return HUNT
    if aux >= LEARNING:
        return LEARNING
    return FIELD if aux >= FIELD else GREEDY


def is_oracle_seat(player) -> bool:
    """A Learning seat decides from the boards it has seen, so an oracle has to
    model it rather than run it. Greedy and Field read only the board."""
    return _stop_of(player) == LEARNING


def stand_in_params(player):
    """The params an oracle runs a Learning seat at instead: Field, the same bot
    with no memory. Greedy, Field and Hunt are their own."""
    params = copy.copy(player.ai_params)
    params.aux = float(FIELD) if _stop_of(player) == LEARNING else params.aux
    return params


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide on this setup (`ai.decide_ms`). knower runs a
    rival bot's decide on every turn it looks ahead, so this is what lets its
    menu warning count us. knower never runs a Learning seat (`is_oracle_seat`),
    but runs Field in its place (`stand_in_params`), so this prices Greedy, Field
    and Hunt."""
    drawn = getattr(settings, "custom_map", None)
    nodes = max(1, len(drawn.nodes) if drawn is not None else settings.nodes)
    seat_params = getattr(settings, "seat_params", None)
    if seat_params is not None and _params_stop(seat_params(seat)) == HUNT:
        plain = HUNT_COST_DECIDE_MS * (nodes / COST_REF_NODES) ** HUNT_COST_NODES_EXP
        return plain + COST_LOOK_MS * _look_work(nodes, max(2, settings.players))
    return COST_DECIDE_MS * (nodes / COST_REF_NODES) ** COST_NODES_EXP


def _look_work(systems: int, seats: int) -> float:
    """The work units one Hunt look-ahead decide spends on this board: whole
    lines, up to one per posture, while they fit LOOK_WORK, and none unless two
    do."""
    if not LOOKAHEAD:
        return 0.0
    line = LOOK_PLIES * seats * systems ** 1.3
    lines = min(1 + len(LOOK_WEIGHTS) + (1 if LOOK_CAPTURE and CAPTURE_SWING > 0 else 0),
                int(LOOK_WORK // line))
    return lines * line if lines >= 2 else 0.0


class _Ledger:
    """The projection and its value, with candidate launches tried against it."""

    def __init__(self, state, pid, curves=None, field=False, hunt=False, weight=None,
                 capture=None):
        self.state, self.pid = state, pid
        self.curves = curves        # Learning: each rival's strike curve on a player
        self.hunt = hunt            # Hunt's switches; ``weight`` and ``capture`` are
                                    # its look-ahead's postures
        sysmap = state.systems
        self.ids = sorted(sysmap)
        # The two edges are adv * swing and swing / adv, so both halves come back
        # out of them: no constant stands in for either slider.
        attack, defend = combat.edge_attacking(TUNED_SWING), combat.edge_defending(TUNED_SWING)
        self.swing, self.advantage = math.sqrt(attack * defend), math.sqrt(attack / defend)
        self.rivals = [p.id for p in state.players.values()
                       if not p.is_neutral and p.id != pid and p.alive]
        longest = max((state.travel_turns(a, b) for a in self.ids
                       for b in sysmap[a].neighbors), default=1)
        self.horizon = max(2, min(HORIZON_MAX, longest + HORIZON_PAD))
        self.reinforce = HUNT_REINFORCE_WEIGHT if hunt else REINFORCE_WEIGHT
        self.relief_in_flight = HUNT_RELIEF_IN_FLIGHT if hunt else RELIEF_IN_FLIGHT
        self.capture, self.gather, self.edge_weight = 0.0, False, EDGE_RISK_WEIGHT
        if hunt:
            lanes = sorted(state.travel_turns(a, b) for a in self.ids
                           for b in sysmap[a].neighbors if a < b)
            median = (lanes[len(lanes) // 2] + lanes[(len(lanes) - 1) // 2]) / 2 if lanes else 0
            self.capture = CAPTURE_SWING if CAPTURE_SWING > 0 and median >= CAPTURE_LANE else 0.0
            if capture is not None:
                self.capture = capture
            if weight is not None:
                self.edge_weight = weight
            self.gather = EDGE_GATHER_LANE > 0 and median <= EDGE_GATHER_LANE
        self.travel = {a: {b: state.travel_turns(a, b) for b in sorted(sysmap[a].neighbors)}
                       for a in self.ids}
        self.income = {sid: (1.0 / sysmap[sid].production if sysmap[sid].production > 0 else 0.0)
                       for sid in self.ids}

        # Arrivals already in the air: arrivals[sid][t] = {owner: ships}.
        self.arrivals = {sid: {} for sid in self.ids}
        for f in state.fleets:
            t = max(1, min(self.horizon, f.turns_remaining))
            slot = self.arrivals[f.dest_id].setdefault(t, {})
            slot[f.owner_id] = slot.get(f.owner_id, 0) + f.ships

        self.garrison = {sid: sysmap[sid].ships for sid in self.ids}
        self.front_value = self._pull() if field else self._front_values()
        self.relief = self._relief()
        self.lines = {sid: self._project(sid, self.garrison[sid], self.arrivals[sid])
                      for sid in self.ids}
        self.worth = {sid: self._worth(sid, self.lines[sid]) for sid in self.ids}
        self.risk = {sid: self._risk(sid) for sid in self.ids}
        self.orders: list[Order] = []

    # --- the plan ---------------------------------------------------------- #
    def plan(self):
        """Commit the best-paying launch until none pays.

        A commit changes the timelines of the systems it touches and nothing else,
        so every cache below is stamped with the versions of the systems its entry
        read, and only entries near the last commit are ever priced again. The
        answer is the same as re-pricing everything every round.
        """
        self.version = dict.fromkeys(self.ids, 0)
        self._near = {sid: self._reach(sid) for sid in self.ids}
        self._pairs, self._coalitions, self._prices, self._deps = {}, {}, {}, {}
        for _ in range(MAX_COMMITS):
            best, best_gain = None, MIN_GAIN
            for launch in self._candidates():
                gain = self._price(launch)
                if gain > best_gain:
                    best, best_gain = launch, gain
            if best is None:
                break
            self._commit(best)
        return self.orders

    def _reach(self, sid):
        """``sid``, its neighbours and theirs: what a launch touching ``sid`` reads
        (its worth, and the risk next door, which reads one hop further)."""
        out = {sid}
        for nbr in self.travel[sid]:
            out.add(nbr)
            out.update(self.travel[nbr])
        return out

    def _pair_deps(self, src, dst):
        key = (src, dst)
        deps = self._deps.get(key)
        if deps is None:
            deps = self._deps[key] = sorted(self._near[src] | self._near[dst])
        return deps

    def _stamp(self, sids):
        return tuple(self.version[sid] for sid in sids)

    def _price(self, launch):
        deps = (self._pair_deps(launch[0][0], launch[0][1]) if len(launch) == 1
                else sorted(set().union(*(self._near[x] for src, dst, _ in launch
                                          for x in (src, dst)))))
        stamp = self._stamp(deps)
        hit = self._prices.get(launch)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        gain = self._gain(launch)[0]
        self._prices[launch] = (stamp, gain)
        return gain

    def _spare(self, sid):
        """Ships ``sid`` can still launch this turn."""
        if self.state.systems[sid].owner_id != self.pid:
            return 0
        return self.garrison[sid]

    def _candidates(self):
        """Every launch worth pricing, as tuples of (source, dest, ships)."""
        seen = set()
        for src in self.ids:
            if self._spare(src) <= 0:
                continue
            for dst in self.travel[src]:
                stamp = self._stamp(self._pair_deps(src, dst))
                hit = self._pairs.get((src, dst))
                if hit is None or hit[0] != stamp:
                    hit = (stamp, self._singles(src, dst))
                    self._pairs[(src, dst)] = hit
                for launch in hit[1]:
                    if launch not in seen:
                        seen.add(launch)
                        yield launch

        # Same-arrival coalitions on a target nobody can take alone.
        for dst in self.ids:
            stamp = self._stamp([dst, *self.travel[dst]])
            hit = self._coalitions.get(dst)
            if hit is None or hit[0] != stamp:
                hit = (stamp, self._coalitions_on(dst))
                self._coalitions[dst] = hit
            for launch in hit[1]:
                if launch not in seen:
                    seen.add(launch)
                    yield launch

    def _singles(self, src, dst):
        if self._idle(src, dst):
            return []
        spare = self._spare(src)
        amounts = {spare, max(1, spare // 2)}
        need = self._enough(dst, ((src, self.travel[src][dst]),), spare)
        if need is not None:
            amounts.add(need)
        return [((src, dst, x),) for x in sorted(amounts)]

    def _idle(self, src, dst):
        """True when no launch from ``src`` to ``dst`` can pay: ``dst`` is ours
        throughout, nothing hostile lands there and nothing threatens it, and it
        stands no nearer a front. Then each ship moved is worth at most what it
        was where it stood, and the source can only lose by it. (A garrison that
        will be attacked is never idle: by the square law a bigger stack keeps
        more than the ships added to it.)"""
        if SELF_RELIEF > 0 and any(self.risk[n] for x in (src, dst) for n in self.travel[x]):
            return False        # moving them changes the relief a neighbour counts on
        return (self.front_value[dst] <= self.front_value[src]
                and self._quiet(src) and self._quiet(dst))

    def _quiet(self, sid):
        """Ours at every turn, nothing hostile landing, no risk priced: a garrison
        whose horizon count moves one for one with the ships it holds."""
        pid = self.pid
        return (self.risk[sid] == 0.0
                and all(o == pid for o in self.lines[sid][0])
                and all(who == pid for slot in self.arrivals[sid].values() for who in slot))

    def _coalitions_on(self, dst):
        if self.lines[dst][0][self.horizon] == self.pid:
            return []
        by_turns: dict[int, list[int]] = {}
        for src, d in self.travel[dst].items():
            if self._spare(src) > 0:
                by_turns.setdefault(d, []).append(src)
        out = []
        for d, srcs in sorted(by_turns.items()):
            if len(srcs) < 2:
                continue
            srcs.sort(key=lambda s: (-self._spare(s), s))
            launch = self._coalition(dst, d, srcs)
            if launch is not None:
                out.append(launch)
        return out

    def _enough(self, dst, legs, cap):
        """Fewest ships, at most ``cap``, that the last of ``legs`` must add for us
        to hold ``dst`` at the horizon (the earlier legs send their spare), or None
        when even ``cap`` is not enough or it is ours either way."""
        H, pid = self.horizon, self.pid
        if self.lines[dst][0][H] == pid:
            return None
        base = sum(self._spare(s) for s, _ in legs[:-1])
        d = legs[-1][1]

        def holds(x):
            arr = _with(self.arrivals[dst], d, pid, base + x)
            return self._project(dst, self.garrison[dst], arr)[0][H] == pid

        if not holds(cap):
            return None
        lo, hi = 1, cap
        while lo < hi:
            mid = (lo + hi) // 2
            if holds(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _coalition(self, dst, d, srcs):
        total = 0
        for i, src in enumerate(srcs):
            spare = self._spare(src)
            if total + spare <= 0:
                continue
            need = self._enough(dst, tuple((s, d) for s in srcs[:i + 1]), spare)
            if need is not None:
                if i == 0:
                    return None          # one source suffices: a single launch already covers it
                return tuple((s, dst, self._spare(s)) for s in srcs[:i]) + ((src, dst, need),)
            total += spare
        return None

    # --- pricing ----------------------------------------------------------- #
    def _gain(self, launch):
        """How much ``launch`` moves the ledger, and the state it would leave: the
        changed garrisons, arrivals and timelines (only those), and every system
        whose risk it re-read."""
        garrison, arrivals = {}, {}
        for src, dst, x in launch:
            garrison[src] = garrison.get(src, self.garrison[src]) - x
            arr = arrivals.get(dst, self.arrivals[dst])
            arrivals[dst] = _with(arr, self.travel[src][dst], self.pid, x)
        touched = set(garrison) | set(arrivals)
        lines = {sid: self._project(sid, garrison.get(sid, self.garrison[sid]),
                                    arrivals.get(sid, self.arrivals[sid]))
                 for sid in touched}
        gain = 0.0
        for sid in touched:
            gain += self._worth(sid, lines[sid]) - self.worth[sid]
        # A neighbour's risk reads only the rival-held part of these timelines, so
        # it can move only if a rival holds the touched system at some turn, before
        # the launch or after it.
        exposed = set(touched)
        rivals = self.rivals
        for sid in touched:
            if any(o in rivals for o in self.lines[sid][0]) or any(o in rivals for o in lines[sid][0]) \
                    or (SELF_RELIEF > 0 and self.pid in self.lines[sid][0]):
                exposed.update(self.travel[sid])
        for sid in exposed:
            gain -= self._risk(sid, lines) - self.risk[sid]
        return gain, garrison, arrivals, lines, exposed

    def _commit(self, launch):
        _, garrison, arrivals, lines, exposed = self._gain(launch)
        self.garrison.update(garrison)
        self.arrivals.update(arrivals)
        self.lines.update(lines)
        for sid in exposed:
            self.worth[sid] = self._worth(sid, self.lines[sid])
            self.risk[sid] = self._risk(sid)
        for src, dst, x in launch:
            self.orders.append(Order(self.pid, src, dst, x))
            self.version[src] += 1
            self.version[dst] += 1

    def _worth(self, sid, line):
        """What ``sid`` is worth to us at the horizon, in ships."""
        owners, ships = line
        H, owner = self.horizon, owners[self.horizon]
        if owner == 0:
            return 0.0
        value = ships[H] + TAIL_TURNS * self.income[sid]
        if owner == self.pid:
            return ships[H] * self.front_value[sid] + TAIL_TURNS * self.income[sid]
        return -value

    def _risk(self, sid, lines=None):
        """Expected loss at ``sid`` to the worst a single rival could land there:
        the shortfall, as a share of what the system is worth, times RISK_WEIGHT.
        Priced at the nominal roll with the advantage ours: a threat is ships that
        *could* come, and demanding the worst roll against all of them at once
        hoards every garrison when the jitter is wide.
        Learning prices each rival garrison next door as striking whole or not at
        all, at min(1, `LEARN_REACH` times the rival's learned chance of striking
        at the ratio of it to our garrison then), and takes the expected
        shortfall, so thinning ours raises the threat it sees again.
        Hunt prices only the strikes a rival would launch (`_edge_risk`).
        ``lines`` overrides some timelines (a launch being priced)."""
        lines = lines or {}
        owners, ships = lines.get(sid) or self.lines[sid]
        pid, H = self.pid, self.horizon
        if pid not in owners:
            return 0.0
        if self.hunt and EDGE_RISK:
            return self._edge_risk(sid, owners, ships, lines)
        worst = 0.0
        for rival in self.rivals:
            curve = self.curves.get(rival) if self.curves is not None else None
            if curve is not None:
                worst = max(worst, self._learned_shortfall(sid, owners, ships, rival, curve, lines))
                continue
            reach = None
            for nbr, d in self.travel[sid].items():
                n_owners, n_ships = lines.get(nbr) or self.lines[nbr]
                if rival not in n_owners:
                    continue
                if reach is None:
                    reach = [0] * (H + 1)
                for t in range(d, H + 1):
                    if n_owners[t - d] == rival:
                        reach[t] += n_ships[t - d]
            if reach is None:
                continue
            if SELF_RELIEF > 0:
                quickest = min(d for nbr, d in self.travel[sid].items()
                               if rival in (lines.get(nbr) or self.lines[nbr])[0])
                own = self._own_relief(sid, lines, quickest if SELF_RELIEF_REACTS else None)
            else:
                own = None
            for t in range(1, H + 1):
                if owners[t] != pid or reach[t] <= 0:
                    continue
                need = reach[t] / self.advantage
                held = ships[t] + (own[t] if own else 0.0)
                if need > held:
                    worst = max(worst, (need - held) / need)
        if worst <= 0.0:
            return 0.0
        stake = ships[H] * self.front_value[sid] + 2 * TAIL_TURNS * self.income[sid]
        return RISK_WEIGHT * worst * stake

    def _edge_risk(self, sid, owners, ships, lines):
        """Hunt's risk: the shortfall share against the strongest strike a rival
        would launch by actuary's rule: launched at turn k <= EDGE_LOOK from one
        system next door, or several whose lanes take the same time, only if it
        beats what we hold on landing on the worst roll. Our garrisons next door
        count as relief (SELF_RELIEF) only if they land before it."""
        pid, H = self.pid, self.horizon
        edge = self.advantage * (EDGE_SWING if EDGE_SWING > 0 else self.swing)
        travel, base = self.travel, self.lines
        near = [(nbr, d) + (lines.get(nbr) or base[nbr]) for nbr, d in travel[sid].items()]
        last_k = min(EDGE_LOOK, H - 1)
        relief: dict = {}                 # quickest rival lane -> our relief per turn
        worst = 0.0
        for rival in self.rivals:
            theirs = [x for x in near if rival in x[2]]     # systems it ever holds
            if not theirs:
                continue
            for k in range(0, last_k + 1):
                by_d: dict[int, int] = {}
                for nbr, d, n_owners, n_ships in theirs:
                    if k + d > H:
                        continue
                    if n_owners[k] == rival and n_ships[k] > 0:
                        gathered = n_ships[k]
                        if self.gather and k > 0:
                            for far, e in travel[nbr].items():
                                if far == sid or e > k:
                                    continue
                                f_owners, f_ships = lines.get(far) or base[far]
                                if f_owners[0] == rival:
                                    gathered += f_ships[0]
                        by_d[d] = by_d.get(d, 0) + gathered
                if not by_d:
                    continue
                own = None
                if SELF_RELIEF > 0:
                    quickest = min(by_d) if SELF_RELIEF_REACTS else None
                    own = relief.get(quickest)
                    if own is None:
                        own = relief[quickest] = self._own_relief(sid, lines, quickest)
                for d, sent in by_d.items():
                    t = k + d
                    if owners[t] != pid:
                        continue
                    held = ships[t] + (own[t] if own else 0.0)
                    if sent > held * edge and sent > held:
                        worst = max(worst, 1.0 - held * edge / sent)
        if worst <= 0.0:
            return 0.0
        stake = ships[H] * self.front_value[sid] + 2 * TAIL_TURNS * self.income[sid]
        return self.edge_weight * worst * stake

    def _own_relief(self, sid, lines, under=None):
        """Per turn, ``SELF_RELIEF`` of what our garrisons next door could have
        landed on ``sid`` by then."""
        H, pid = self.horizon, self.pid
        own = [0.0] * (H + 1)
        for nbr, d in self.travel[sid].items():
            if under is not None and d >= under:
                continue
            n_owners, n_ships = lines.get(nbr) or self.lines[nbr]
            for t in range(d, H + 1):
                if n_owners[t - d] == pid:
                    own[t] += SELF_RELIEF * n_ships[t - d]
        return own

    def _learned_shortfall(self, sid, owners, ships, rival, curve, lines):
        """The worst turn's expected shortfall share against ``rival``, with each
        of its garrisons next door striking whole or not at all, independently,
        at min(1, `LEARN_REACH` x its curve at the ratio to our garrison then)."""
        pid, H = self.pid, self.horizon
        sources = []
        for nbr, d in self.travel[sid].items():
            n_owners, n_ships = (lines.get(nbr) or self.lines[nbr])[:2]
            if rival not in n_owners:
                continue
            sources.append((d, n_owners, n_ships))
        own = None
        if SELF_RELIEF > 0 and sources:
            quickest = min(d for d, _, _ in sources)
            own = self._own_relief(sid, lines, quickest if SELF_RELIEF_REACTS else None)
        worst = 0.0
        for t in range(1, H + 1):
            if owners[t] != pid:
                continue
            held = max(ships[t] * config.DEFENDER_ADVANTAGE, 0.5)
            stands = ships[t] + (own[t] if own else 0.0)
            items = []
            for d, n_owners, n_ships in sources:
                if t >= d and n_owners[t - d] == rival and n_ships[t - d] > 0:
                    n = n_ships[t - d]
                    p = min(1.0, LEARN_REACH * curve[_ratio_bin(n / held)])
                    if p > 0.0:
                        items.append((n, p))
            if not items or sum(n for n, _ in items) / self.advantage <= stands:
                continue
            sums = {0: 1.0}
            for n, p in items:
                nxt: dict[int, float] = {}
                for total, q in sums.items():
                    nxt[total + n] = nxt.get(total + n, 0.0) + q * p
                    if p < 1.0:
                        nxt[total] = nxt.get(total, 0.0) + q * (1.0 - p)
                sums = nxt
            expected = 0.0
            for total, q in sums.items():
                need = total / self.advantage
                if need > stands:
                    expected += q * (need - stands) / need
            worst = max(worst, expected)
        return worst

    # --- the projection ---------------------------------------------------- #
    def _project(self, sid, garrison, arrivals):
        """(owners, ships) for ``sid`` at turns 0..horizon, under the engine's phase
        order: production first, then whatever lands that turn."""
        node = self.state.systems[sid]
        owner, ships, progress = node.owner_id, garrison, node.prod_progress
        rate, H = node.production, self.horizon
        owners, counts = [owner], [ships]
        t = 0
        for land in sorted(arrivals):
            # Quiet turns up to and including the landing turn's production, in
            # closed form: k turns of progress build (progress + k) // rate ships.
            k = land - t
            if rate > 0 and (owner != 0 or config.NEUTRAL_PRODUCES):
                counts.extend(ships + (progress + i) // rate for i in range(1, k + 1))
                ships += (progress + k) // rate
                progress = (progress + k) % rate
            else:
                counts.extend([ships] * k)
            owners.extend([owner] * k)
            was = owner
            owner, ships = self._land(sid, land, owner, ships, arrivals[land])
            if owner != was:
                progress = 0
            owners[-1], counts[-1] = owner, ships
            t = land
        k = H - t
        if rate > 0 and (owner != 0 or config.NEUTRAL_PRODUCES):
            counts.extend(ships + (progress + i) // rate for i in range(1, k + 1))
        else:
            counts.extend([ships] * k)
        owners.extend([owner] * k)
        return owners, counts

    def _land(self, sid, t, owner, ships, landing):
        """The engine's pile-up fold: garrison pooled with its own side's arrivals,
        attackers folded strongest-first, the survivor against the garrison last."""
        forces = {owner: ships}
        for who, n in landing.items():
            forces[who] = forces.get(who, 0) + n
        if owner not in (0, self.pid) and self.pid in landing:
            # A rival we strike may be relieved before we land.
            by_t, flying = self.relief[sid]
            forces[owner] += int(self.reinforce * by_t.get(t, 0)
                                 + self.relief_in_flight * flying.get(t, 0))
        sides = [(o, n) for o, n in forces.items() if n > 0]
        if not sides:
            return 0, 0
        if len(sides) == 1:
            return sides[0]
        garrison = next((s for s in sides if s[0] == owner), None)
        attackers = sorted((s for s in sides if s[0] != owner), key=lambda s: (-s[1], s[0]))
        cur = attackers[0]
        for other in attackers[1:]:
            cur = self._fight(cur, other, defender=None)
        if garrison is not None:
            cur = self._fight(cur, garrison, defender=owner)
        return cur

    def _fight(self, a, b, defender):
        """``a`` against ``b``. Who wins is priced against us: our side fights at
        the low roll and theirs at the high one, the holder of the system gets the
        defender advantage, and we must outnumber a garrison we attack. What we
        keep when we win is the nominal roll's: the worst roll decides whether a
        launch is safe, not what it is worth, and pricing both at the worst roll
        makes every capture look like a loss once the jitter is wide. Survivors by
        the square law, capped at the winner's ships."""
        (ao, an), (bo, bn) = a, b
        pid, swing, adv = self.pid, self.swing, self.advantage
        a_nom, b_nom = float(an), float(bn)
        if bo == defender:
            b_nom *= adv
        a_eff, b_eff = a_nom, b_nom
        if ao == pid:
            if bo == defender and an <= bn:
                return b
            b_eff *= self.capture if self.capture > 0 and bo in self.rivals else swing
        elif bo == pid:
            a_eff *= swing
        if a_eff > b_eff:
            won, w_eff, l_eff, actual = ao, a_eff, b_eff, an
            if ao == pid:
                w_eff, l_eff = a_nom, b_nom
        elif b_eff > a_eff or bo == defender:
            won, w_eff, l_eff, actual = bo, b_eff, a_eff, bn
            if bo == pid:
                w_eff, l_eff = b_nom, a_nom
        else:
            won, w_eff, l_eff, actual = ao, a_eff, b_eff, an
        left = min(actual, int(math.sqrt(max(0.0, w_eff * w_eff - l_eff * l_eff))))
        return (won, left) if left > 0 else (0, 0)

    # --- board readings ---------------------------------------------------- #
    def _front_values(self):
        """1 + FRONT_BONUS on a system touching anything not ours, decaying by
        FRONT_DECAY per turn of travel back through our own systems."""
        sysmap, pid = self.state.systems, self.pid
        dist = {}
        for sid in self.ids:
            if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors) \
                    or sysmap[sid].owner_id != pid:
                dist[sid] = 0
        frontier = sorted(dist)
        # Dijkstra over our own systems, by travel turns.
        heap = [(0, sid) for sid in frontier]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist.get(sid, math.inf):
                continue
            for nbr, t in self.travel[sid].items():
                if sysmap[nbr].owner_id != pid:
                    continue
                nd = d + t
                if nd < dist.get(nbr, math.inf):
                    dist[nbr] = nd
                    heapq.heappush(heap, (nd, nbr))
        return {sid: 1.0 + FRONT_BONUS * FRONT_DECAY ** dist.get(sid, 99) for sid in self.ids}

    def _pull(self):
        """The pull at Field, Learning and Hunt: the field, or on a board whose
        median lane is `SLOW_LANE` turns or more, the mean of the field and
        Greedy's front (at Hunt, the two mixed by `HUNT_SLOW_FRONT`)."""
        lanes = sorted(d for a in self.ids for b, d in self.travel[a].items() if a < b)
        field = self._field()
        if not lanes or SLOW_LANE <= 0 or lanes[len(lanes) // 2] < SLOW_LANE:
            return field
        front = self._front_values()
        if self.hunt:
            return {sid: (1 - HUNT_SLOW_FRONT) * field[sid] + HUNT_SLOW_FRONT * front[sid]
                    for sid in self.ids}
        return {sid: 0.5 * (field[sid] + front[sid]) for sid in self.ids}

    def _field(self):
        """The pull on a ship at each system, 1 to 1 + `FRONT_BONUS`: see the
        module docstring. Reads the board as it stands; the greedy then prices
        every launch against these values without recomputing them."""
        sysmap, pid = self.state.systems, self.pid
        rivals = set(self.rivals)
        lanes = sorted(d for a in self.ids for b, d in self.travel[a].items() if a < b)
        unit = float(lanes[len(lanes) // 2]) if lanes else 1.0
        near = {sid: self._within(sid, FIELD_REACH * unit, unit) for sid in self.ids}

        mine, foe = defaultdict(float), defaultdict(float)
        for w in self.ids:
            node = sysmap[w]
            side = mine if node.owner_id == pid else foe if node.owner_id in rivals else None
            if side is None or node.ships <= 0:
                continue
            for s, d in near[w].items():
                side[s] += node.ships * FIELD_DECAY ** d
        for f in self.state.fleets:
            side = mine if f.owner_id == pid else foe if f.owner_id in rivals else None
            if side is None:
                continue
            for s, d in near[f.dest_id].items():
                side[s] += f.ships * FIELD_DECAY ** (d + f.turns_remaining / unit)

        stake = {}
        for s in self.ids:
            node = sysmap[s]
            if node.owner_id == pid and all(sysmap[n].owner_id == pid for n in node.neighbors):
                continue                    # our interior: nothing to contest
            standing = node.ships if node.owner_id in rivals else 0
            x = (mine[s] - foe[s]) / (CONTEST_WIDTH * max(1.0, mine[s] + foe[s]))
            stake[s] = (TAIL_TURNS * self.income[s] + standing) * math.exp(-x * x)

        pull = {}
        for w in self.ids:
            if sysmap[w].owner_id == pid:
                pull[w] = sum(stake[s] * FIELD_DECAY ** near[w][s] for s in stake if s in near[w])
        top = max(pull.values(), default=0.0)
        return {sid: (1.0 + FRONT_BONUS * pull[sid] / top if top > 0 else 1.0)
                if sid in pull else 1.0 + FRONT_BONUS
                for sid in self.ids}

    def _within(self, src, radius, unit):
        """Median lanes from ``src`` to every system within ``radius`` turns."""
        dist = {src: 0}
        heap = [(0, src)]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist[sid]:
                continue
            for nbr, t in self.travel[sid].items():
                nd = d + t
                if nd <= radius and nd < dist.get(nbr, math.inf):
                    dist[nbr] = nd
                    heapq.heappush(heap, (nd, nbr))
        return {sid: d / unit for sid, d in dist.items()}

    def _relief(self):
        """relief[sid][t]: ships the holder of ``sid`` could land there by turn t,
        launching no sooner than next turn, as (from garrisons next door, from
        fleets landing next door) so each can carry its own weight."""
        sysmap = self.state.systems
        out = {}
        for sid in self.ids:
            owner = sysmap[sid].owner_id
            by_t, flying = {}, {}
            if owner not in (0, self.pid):
                for nbr, d in self.travel[sid].items():
                    if sysmap[nbr].owner_id == owner:
                        for t in range(d + 1, self.horizon + 1):
                            by_t[t] = by_t.get(t, 0) + sysmap[nbr].ships
                if self.relief_in_flight > 0:
                    for t0, ships in self._relief_in_flight(sid, owner):
                        for t in range(t0, self.horizon + 1):
                            flying[t] = flying.get(t, 0) + ships
            out[sid] = (by_t, flying)
        return out

    def _relief_in_flight(self, sid, owner):
        """(turn, ships) for each of ``owner``'s pools landing next door to
        ``sid`` that could take its landing system if it must and come back: a
        pool landing on turn r relaunches then and reaches ``sid`` at r + d. A
        neighbour ``owner`` does not hold costs the pool its fight there, priced
        by `_fight` against its garrison now."""
        sysmap = self.state.systems
        pools = defaultdict(int)
        for f in self.state.fleets:
            if f.owner_id == owner and f.dest_id != sid and f.dest_id in self.travel[sid]:
                pools[(f.dest_id, max(1, f.turns_remaining))] += f.ships
        out = []
        for (nbr, r), ships in sorted(pools.items()):
            t0 = r + self.travel[sid][nbr]
            if t0 > self.horizon:
                continue
            held = sysmap[nbr]
            if held.owner_id != owner:
                won, ships = self._fight((owner, ships), (held.owner_id, held.ships),
                                         defender=held.owner_id)
                if won != owner:
                    continue
            out.append((t0, ships))
        return out


def _with(arrivals, t, owner, ships):
    """A copy of ``arrivals`` with ``ships`` more of ``owner``'s landing at ``t``."""
    out = dict(arrivals)
    slot = dict(out.get(t, {}))
    slot[owner] = slot.get(owner, 0) + ships
    out[t] = slot
    return out


# --------------------------------------------------------------------------- #
# Hunt's look-ahead
#
# Our candidate plans are Hunt's own and the same ledger at other postures.
# Each is played on a private board for LOOK_PLIES turns, every seat after the
# first ply (and every rival from the first) playing Hunt's plain plan from its
# own seat: no other bot's code is run, so this is a self-model, not an oracle.
# The copy rolls its own dice (`dice_salt` + ":hunter-rollout"), so no line
# foresees the game's. Every line rolls the same dice, so a score gap is the
# plan's, not luck's. The score is the ledger's value of the final board from
# our seat, which already projects every fleet still flying.
# --------------------------------------------------------------------------- #
def _look(state, pid):
    deadline = time.perf_counter() + LOOK_BUDGET_S * BUDGET_SCALE
    seats = sum(1 for p in state.players.values() if not p.is_neutral and p.alive)
    line = LOOK_PLIES * seats * len(state.systems) ** 1.3     # the work one line costs
    if 2 * line > LOOK_WORK:
        return _hunt(state, pid).plan()   # not two lines' worth: nothing to compare
    plans, seen = [], set()
    for weight, capture in _postures():
        orders = _hunt(state, pid, weight, capture).plan()
        key = tuple(sorted((o.source_id, o.dest_id, o.ships) for o in orders))
        if key not in seen:
            seen.add(key)
            plans.append(orders)
        if time.perf_counter() > deadline:
            break
    if len(plans) == 1:
        return plans[0]
    # Every rival decides its first turn from this same board in every line, so
    # its orders there are worked out once, not once per line.
    first = {q: _hunt(state, q).plan() for q, p in state.players.items()
             if q != pid and not p.is_neutral and any(x.owner_id == q for x in state.systems.values())}
    lines = []
    for i, orders in enumerate(plans):
        if (i + 1) * line > LOOK_WORK and i > 0:
            break                         # the next line would pass the work budget
        lines.append(_HuntLine(state, pid, orders, first))
    best, best_score = plans[0], None
    for ln in lines:
        score = ln.play(LOOK_PLIES, deadline)
        if score is None:
            break                         # out of time mid-line: keep the best scored
        if best_score is None or score > best_score:
            best, best_score = ln.orders, score
    return best


def _hunt(state, pid, weight=None, capture=None):
    """Hunt's plain ledger for ``pid``, at a posture of the look-ahead's."""
    return _Ledger(state, pid, field=True, hunt=True, weight=weight, capture=capture)


def _postures():
    yield None, None
    for factor in LOOK_WEIGHTS:
        yield EDGE_RISK_WEIGHT * factor, None
    if LOOK_CAPTURE and CAPTURE_SWING > 0:
        yield None, CAPTURE_SWING


class _HuntLine:
    """One candidate played forward on a private board, a few turns at a time."""

    def __init__(self, state, pid, orders, first_orders=None):
        self.board, self.pid, self.orders = _clone(state), pid, orders
        self.first_orders, self.plies = first_orders, 0

    def _decide(self, s, q):
        if self.plies == 0:
            if q == self.pid:
                return self.orders
            if self.first_orders is not None:
                return self.first_orders.get(q, [])
        if not any(x.owner_id == q for x in s.systems.values()):
            return []
        return _hunt(s, q).plan()

    def play(self, upto, deadline=math.inf):
        """The line's score once it has played ``upto`` turns in all, or ``None``
        if the clock ran out first (checked before each, so an overrun is one ply)."""
        board, pid = self.board, self.pid
        while self.plies < upto and board.winner is None:
            if time.perf_counter() > deadline:
                return None
            engine.end_turn(board, decide=self._decide)
            self.plies += 1
        if board.winner is not None:
            return LOOK_DECIDED if board.winner == pid else -LOOK_DECIDED
        if not any(x.owner_id == pid for x in board.systems.values()):
            return -LOOK_DECIDED
        return sum(_hunt(board, pid).worth.values())


def _rollout(state, pid, orders, deadline=math.inf, first_orders=None):
    """The score of ``orders`` after LOOK_PLIES turns, or ``None`` if the clock ran
    out before the last of them."""
    return _HuntLine(state, pid, orders, first_orders).play(LOOK_PLIES, deadline)


def _clone(state):
    """A private board: every mutable container rebuilt (as knower's `_clone`),
    its own rng, every seat driven by `decide`, and its own dice."""
    clone = copy.copy(state)
    clone.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in clone.systems.values():
        s.neighbors = list(s.neighbors)
    clone.fleets = [copy.copy(f) for f in state.fleets]
    clone.players = {q: copy.copy(p) for q, p in state.players.items()}
    for p in clone.players.values():
        p.ai_params = copy.copy(p.ai_params)
        p.is_human = False
    clone.adjacency = {sid: dict(n) for sid, n in state.adjacency.items()}
    clone.rng = random.Random(0)
    clone.dice_salt = f"{state.dice_salt}:hunter-rollout"
    return clone


def configure(overrides: dict) -> None:
    """Set module constants by name, for the bench's variants (tools/hunt_bench.py)."""
    g = globals()
    for name, value in overrides.items():
        if name not in g:
            raise KeyError(name)
        if isinstance(g[name], tuple):
            g[name] = tuple(value) if isinstance(value, (tuple, list)) else (value,)
        else:
            g[name] = type(g[name])(value) if isinstance(g[name], (int, float, bool)) else value


# --------------------------------------------------------------------------- #
# Reading the rivals (Style: Learning)
#
# Every rival decides on the board we saw last turn, since turns resolve
# simultaneously, and what it decided is on the board we see now: its fresh
# fleets, and the ships a one-turn lane carried off unseen. So each turn pairs
# what a rival saw with what it did. For each of its systems and each neighbour
# it does not hold, we count the ratio of its garrison to the neighbour's
# effective garrison on arrival and whether it struck, per ratio bin, split by
# neutral or player target and by whether its system had hostile ships inbound.
# `strike_curve` fits that monotone over a prior.
#
# Memory is keyed by the game's path, never by "the current game": `decide`
# also runs on copies, branches, rewinds and isolated positions. Each board is a
# node in a memo tree, found by content: a node's parent is a stored board one
# turn earlier that this board provably follows from (`_follows`), and its
# counts are the parent's plus what that turn showed. A board with no known
# parent starts from the prior. docs/design/learner.md has why, and the
# measurements.
# --------------------------------------------------------------------------- #
class Model:
    """What one rival has been seen to do: raw counts, the prior added on read."""

    __slots__ = ("strikes", "passes", "turns")
    COUNTS = __slots__[:-1]

    def __init__(self, strikes, passes, turns):
        self.strikes = strikes      # per (kind, pressed, target ratio bin): struck
        self.passes = passes        # ...and not struck
        self.turns = turns

    def plus(self, other: Model) -> Model:
        return Model(*(tuple(x + y for x, y in zip(getattr(self, name), getattr(other, name)))
                       for name in self.COUNTS), self.turns + other.turns)


EMPTY = Model((0.0,) * (_ROWS * RATIO_BINS), (0.0,) * (_ROWS * RATIO_BINS), 0)


class _Snap:
    """A board as the turn began: everything a rival could have read off it."""

    __slots__ = ("turn", "owner", "ships", "progress", "fleets", "key")

    def __init__(self, state):
        sids = sorted(state.systems)
        systems = state.systems
        self.turn = state.turn
        self.owner = {sid: systems[sid].owner_id for sid in sids}
        self.ships = {sid: systems[sid].ships for sid in sids}
        self.progress = {sid: systems[sid].prod_progress for sid in sids}
        self.fleets = tuple(sorted((f.owner_id, f.source_id, f.dest_id, f.ships,
                                    f.turns_total, f.turns_remaining)
                                   for f in state.fleets))
        self.key = (self.turn,
                    tuple((sid, self.owner[sid], self.ships[sid], self.progress[sid])
                          for sid in sids),
                    self.fleets)


class _Node:
    __slots__ = ("snap", "parent", "models")

    def __init__(self, snap, parent, models):
        self.snap = snap
        self.parent = parent
        self.models = models


_NODES: OrderedDict = OrderedDict()     # (game key, board key) -> _Node
_AT: dict = {}                          # (game key, turn) -> [board key, ...]


def reset() -> None:
    """Forget every board."""
    _NODES.clear()
    _AT.clear()


def _game_key(state) -> bytes:
    """The map and the rules a board is played under. Boards on different maps,
    or under different combat or speed settings, never share a path."""
    topology = (tuple(sorted((sid, tuple(sorted(nbrs.items())))
                             for sid, nbrs in state.adjacency.items())),
                tuple(sorted((sid, s.production) for sid, s in state.systems.items())),
                config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE, config.NEUTRAL_PRODUCES,
                config.IN_LANE_BATTLES, config.SHIP_SPEED_GROWTH_PCT)
    return hashlib.blake2b(repr(topology).encode(), digest_size=16).digest()


def _node_for(state) -> _Node:
    """This board's node, made (and its parent found) if it is new."""
    game = _game_key(state)
    snap = _Snap(state)
    key = (game, snap.key)
    node = _NODES.get(key)
    if node is not None:
        _NODES.move_to_end(key)
        return node
    parent = None
    for board in sorted(_AT.get((game, snap.turn - 1), ())):
        candidate = _NODES[(game, board)]
        if _follows(candidate.snap, snap, state):
            parent = candidate
            break
    if parent is None:
        node = _Node(snap, None, {})
    else:
        node = _Node(snap, (game, parent.snap.key),
                     _fold(parent.models, observe(parent.snap, snap, state)))
    _NODES[key] = node
    _AT.setdefault((game, snap.turn), []).append(snap.key)
    while len(_NODES) > NODE_CAP:
        (old_game, old_board), old = _NODES.popitem(last=False)
        boards = _AT[(old_game, old.snap.turn)]
        boards.remove(old_board)
        if not boards:
            del _AT[(old_game, old.snap.turn)]
    return node


def models_for(state) -> dict[int, Model]:
    """Every player's model as of this board (a player never seen is absent:
    read it as `EMPTY`, which is the prior alone)."""
    return _node_for(state).models


def _curves(state) -> dict[int, list[float]]:
    """Each player's strike curve on a player-held target, unpressed, as of this
    board."""
    models = models_for(state)
    return {q: strike_curve(models.get(q, EMPTY), PLAYER, 0) for q in sorted(state.players)}


def _fold(models: dict[int, Model], seen: dict[int, Model]) -> dict[int, Model]:
    out = dict(models)
    for pid, obs in seen.items():
        out[pid] = out.get(pid, EMPTY).plus(obs)
    return out


def _one_turn_lane(state, sid: int) -> bool:
    return any(state.travel_turns(sid, n) == 1 for n in state.systems[sid].neighbors)


def _hulls(state, snap: _Snap, sid: int, turns: int) -> int:
    """Ships `sid` builds in the next `turns` turns if nobody takes it, as
    `engine._production` builds them."""
    production = state.systems[sid].production
    if production <= 0 or (snap.owner[sid] == 0 and not config.NEUTRAL_PRODUCES):
        return 0
    return (snap.progress[sid] + turns) // production


def _follows(prev: _Snap, cur: _Snap, state) -> bool:
    """Whether `cur` is the board one `end_turn` makes from `prev`: every fleet
    still flying moved one step, every fresh fleet left a system its owner held
    with the ships to send, and a system no fleet could have reached unseen kept
    its owner and changed by exactly its production less its launches."""
    if cur.turn != prev.turn + 1:
        return False
    lane_battles = config.IN_LANE_BATTLES
    expected: Counter = Counter()
    landing: set[int] = set()
    for owner, src, dst, ships, total, left in prev.fleets:
        if left > 1:
            expected[(owner, src, dst, total, left - 1) if lane_battles
                     else (owner, src, dst, ships, total, left - 1)] += 1
        else:
            landing.add(dst)
    seen: Counter = Counter()
    launched: Counter = Counter()
    for owner, src, dst, ships, total, left in cur.fleets:
        if left == total - 1:
            if prev.owner.get(src) != owner:
                return False
            launched[src] += ships
        else:
            seen[(owner, src, dst, total, left) if lane_battles
                 else (owner, src, dst, ships, total, left)] += 1
    if lane_battles:
        if any(count > expected[k] for k, count in seen.items()):
            return False
    elif seen != expected:
        return False
    if any(ships > prev.ships[src] for src, ships in launched.items()):
        return False
    for sid in prev.owner:
        if sid in landing or _one_turn_lane(state, sid):
            continue
        if cur.owner.get(sid) != prev.owner[sid]:
            return False
        if cur.ships[sid] != prev.ships[sid] + _hulls(state, prev, sid, 1) - launched[sid]:
            return False
    return True


class _Option(NamedTuple):
    target: int
    kind: int
    turns: int
    eff: float          # the target's effective garrison on arrival
    ratio: float        # the source's garrison over it


def _ratio_bin(ratio: float) -> int:
    return min(RATIO_BINS - 1, max(0, int(ratio * 10)))


def _effective(state, snap: _Snap, sid: int, turns: int) -> float:
    """What a strike landing on `sid` in `turns` turns has to beat: the garrison,
    the hulls it builds by then and its holder's fleets landing by then, with the
    defender's advantage. Never below half a ship, so a ratio stays finite."""
    owner = snap.owner[sid]
    ships = snap.ships[sid] + _hulls(state, snap, sid, turns)
    for f_owner, _src, dst, f_ships, _total, left in snap.fleets:
        if dst == sid and f_owner == owner and left <= turns:
            ships += f_ships
    return max(ships * config.DEFENDER_ADVANTAGE, 0.5)


def _hostile_inbound(snap: _Snap) -> dict[int, int]:
    """Ships flying at each system from anyone but its holder."""
    out: dict[int, int] = defaultdict(int)
    for owner, _src, dst, ships, _total, _left in snap.fleets:
        if owner != snap.owner[dst]:
            out[dst] += ships
    return out


def _options(snap: _Snap, state, sid: int) -> list[_Option]:
    """Every neighbour of `sid` its holder does not hold, priced as a target."""
    q = snap.owner[sid]
    garrison = snap.ships[sid]
    out = []
    for n in sorted(state.systems[sid].neighbors):
        holder = snap.owner[n]
        if holder == q:
            continue
        turns = state.travel_turns(sid, n) or 1
        eff = _effective(state, snap, n, turns)
        out.append(_Option(n, NEUTRAL if holder == 0 else PLAYER, turns, eff, garrison / eff))
    return out


def launches(prev: _Snap, cur: _Snap, state) -> dict[int, tuple[dict[int, int], int]]:
    """What each held, garrisoned system of `prev` launched on the turn to `cur`:
    ships per target, and ships that left on a one-turn lane with more than one
    candidate target (so unseen and unplaced). A one-turn launch never shows as a
    fleet; it is recovered from the garrison. `state` is read for the map alone."""
    sent: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for _owner, src, dst, ships, total, left in cur.fleets:
        if left == total - 1:
            sent[src][dst] += ships
    landing = {dst for _o, _s, dst, _n, _t, left in prev.fleets if left <= 1}
    out = {}
    for sid in sorted(prev.owner):
        q = prev.owner[sid]
        garrison = prev.ships[sid]
        if q == 0 or garrison <= 0:
            continue
        to = dict(sent.get(sid, {}))
        unknown = 0
        one_turn = sorted(n for n in state.systems[sid].neighbors
                          if state.travel_turns(sid, n) == 1)
        if one_turn and sid not in landing and cur.owner.get(sid) == q:
            unseen = (garrison + _hulls(state, prev, sid, 1) - sum(to.values())
                      - cur.ships[sid])
            if unseen > 0:
                if len(one_turn) == 1:
                    to[one_turn[0]] = to.get(one_turn[0], 0) + unseen
                else:
                    unknown = unseen
        out[sid] = (to, unknown)
    return out


def observe(prev: _Snap, cur: _Snap, state) -> dict[int, Model]:
    """What each player did on the turn from `prev` to `cur`, as counts. `state`
    is read for the map alone."""
    hostile = _hostile_inbound(prev)
    counts: dict[int, dict] = {}
    for sid, (to, _unknown) in launches(prev, cur, state).items():
        q = prev.owner[sid]
        c = counts.setdefault(q, {name: list(getattr(EMPTY, name)) for name in Model.COUNTS})
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        for option in _options(prev, state, sid):
            cell = (option.kind * 2 + pressed) * RATIO_BINS + _ratio_bin(option.ratio)
            c["strikes" if to.get(option.target, 0) > 0 else "passes"][cell] += 1
    return {q: Model(*(tuple(c[name]) for name in Model.COUNTS), 1)
            for q, c in counts.items()}


def _monotone(values: list[float], weights: list[float]) -> list[float]:
    """The non-decreasing fit closest to `values` (pool adjacent violators)."""
    blocks: list[list[float]] = []
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks[-1]
            blocks[-1] = [(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2]
    out: list[float] = []
    for v, _w, c in blocks:
        out += [v] * int(c)
    return out


def strike_curve(model: Model, kind: int, pressed: int) -> list[float]:
    """Chance one target is struck, per bin of its own ratio, never falling as
    the ratio rises."""
    row = kind * 2 + pressed
    base = row * RATIO_BINS
    prior = PRIOR_STRIKE[row]
    rates, weights = [], []
    for b in range(RATIO_BINS):
        hit = model.strikes[base + b] + PRIOR_WEIGHT * prior[b]
        total = model.strikes[base + b] + model.passes[base + b] + PRIOR_WEIGHT
        rates.append(hit / total)
        weights.append(total)
    return _monotone(rates, weights)
