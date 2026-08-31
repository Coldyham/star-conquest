"""knower — thinker, but it reads its opponents' orders before they issue them.

Every seat's strategy is public: ``Player.ai_strategy`` names a function in
``ai.STRATEGIES`` and ``Player.ai_params`` holds that seat's tuning. Turns also
resolve *simultaneously* — ``engine._collect_orders`` hands every player the same
unmutated start-of-turn state and applies nothing until all of them have decided
— so an opponent's orders **cannot** depend on ours. Those two facts together mean
a bot can clone the board, call each rival's own ``decide``, and read off exactly
what they are about to do. There is no game-theoretic fixed point to solve: one
forward pass of their real code *is* the answer.

Everything is then expressed through a single object, the **post-launch board**:
a clone of the start-of-turn state with every predicted enemy order applied (via
``engine.apply_order``, so the sequential ship-deduction clamp is the engine's own)
but *not* advanced. On that board ``systems[x].ships`` is the garrison a rival will
be left holding and ``fleets`` includes the launches nobody has seen yet, on the
same ``turns_remaining`` clock thinker's helpers already use. So the planner below
is thinker's, reading ``post`` where thinker read ``state``.

What that buys, in descending order of how much it actually wins:

  * **A turn of warning that thinker structurally cannot have.** thinker only sees
    fleets already on a lane, so a strike along an L-turn lane reaches it with L-1
    turns to spare — and a 1-turn strike is never visible *at all*, because
    ``end_turn`` launches, advances and resolves it in one call. How much that costs
    depends entirely on the Advanced menu's ship-speed slider (1-30 ly/turn), since
    ``travel_turns = ceil(length_ly / SHIP_LY_PER_TURN)``:
      - At the default 6 ly/turn no lane is shorter than 2 turns, so nothing is
        wholly invisible — but a blow landing next turn still cannot be answered,
        because the reinforcement filter ``travel_turns(sid, n) <= t_bind`` has no
        lane short enough to find. Measured over 12 thinker-vs-thinker games at 24
        nodes: 43.7% of the threats thinker detects sit at that horizon and *none*
        of them are reinforceable.
      - At 18 ly/turn, 72.5% of lanes are 1 turn; at 30, all of them are. There
        thinker cannot see most attacks until they have already landed.
    knower reads the launch on the turn it is issued, which fixes both regimes with
    the same mechanism.
  * **A guard only where it is needed.** thinker pins ``FRONTIER_GUARD`` of every
    frontier garrison against a hypothetical neighbour. knower knows which systems
    are really being attacked and how hard, so the rest of that army goes forward.
  * **Snipes.** ``apply_order`` deducts at launch, so a system that sent its army
    somewhere is genuinely empty *this turn*. ``_required`` prices a target off
    ``_garrison`` — what will actually be left defending it — so a strike thinker
    reads as hopeless is often nearly free.

Two things deliberately *not* here, both built, measured and then removed rather
than kept on the strength of the idea:

  * **Pricing three-way pile-ups.** The oracle knows who else lands on a node this
    turn, so knower can fold the pile-up with the jitter pinned against it and
    demand enough mass to survive it. Measured 71% vs 72% head-to-head over 200
    games and 33 vs 35 wins in a 3-player free-for-all — a wash, if anything worse.
    It makes knower skip attacks it cannot overpay for, and this game rewards the
    leaner strike (the same finding that set thinker's margins above).
  * **Striking perishable targets first.** A vacated garrison refills, so ordering
    targets by how much of theirs is leaving looks obviously right. It changes
    nothing: 118-45 vs 119-45 over 200 games, 35 vs 36 in the free-for-all. Pricing
    the target correctly is what wins; the order it happens in does not.

Don't re-add either without a measurement.

Against a **human** seat none of this holds — their orders come from the shell, not
from code. knower models them as another of itself one level shallower (a blind
``_plan``, i.e. thinker-strength self-play) and by default lets that model only
ever *raise* a threat: it never relaxes a guard, never believes a human vacated a
system, and never plans around a human fleet spending itself. A human who does
something unexpected therefore cannot be punished for it. See ``TRUST_HUMAN``.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it, and
deliberately draws **nothing** from ``state.rng`` — every tie-break here is
deterministic. That is not fussiness: it is what leaves the rng exactly where the
seats after us expect to find it, which is what makes their prediction bit-exact.

Measured against thinker, ladder, both seatings, 24-node random maps:

    default 6 ly/turn, 200 games   73% (119-45)
    18 ly/turn, 100 games          91% (88-9)     <- mostly 1-turn lanes
    full roster ladder             knower 143 > thinker 117 > claudebot 72
                                   > heuristic 33 > rusherplus 10

The gap between those first two rows *is* the thesis of this bot: the faster ships
are, the more of the game thinker cannot see, and the oracle scales with it.

--- Depth: how far forward to look ------------------------------------------- #

Everything above is **depth 1**. The seat's generic ``ai_params.aux`` knob (the AI
tab's slider, which ``AUX_LABEL`` below names *Search depth*) turns that into a dial:

    0   no oracle at all — the blind ``_plan``, i.e. thinker-strength
    1   the one-turn oracle described above (the default, `config.AI_AUX`)
    N   the oracle, then N-1 turns actually *played out* and scored

Depth >= 2 is a **tree search over sequences**. ``engine.end_turn`` is drivable on a
clone — the engine takes ``decide`` as a parameter and mutates nothing outside the
state handed to it — so ``_rollout`` steps one ply of one line, ``_expand`` branches
every live line across the whole candidate set, and ``_evaluate`` scores the leaves.

Branching at *every* ply is the point. An earlier version deviated for one ply and
then played the tuned default for the rest, which prices a candidate as "what if I
did this now and then went back to normal" — it cannot represent "rush now,
consolidate next turn" at all, and a posture is exactly the kind of thing you would
want to hold for three turns or not adopt at all.

The full tree is ``candidates ** turns``, ~2e9 nodes at the top of the slider, so it
is grown as a beam. Four things make it sound:

  * **The cut is per opening, not pooled** (`_prune`). This is the one that is easy
    to get wrong and expensive to get wrong. The default's line branches as widely as
    anyone else's, so a single pooled beam fills with *continuations of whichever
    opening leads on material right now* — measured, 94% of survivors descended from
    the default and a borrowed opening never survived a single cut, which made the
    whole tree a costly way to re-derive depth 1. Grouping by ply-0 move keeps every
    opening alive to the bottom, which is what the search exists to compare.
  * **Candidates come from a threaded ``Posture``, not patched globals.** Patching
    would be process-wide: it would corrupt the ``_blind`` self-model used to
    predict rivals, and every other knower seat in the game.
  * **Common random numbers.** Every node on a ply gets the same state-derived rng,
    so all of them meet the same combat jitter and a score gap reflects the plan
    rather than the dice. Free variance reduction, and it keeps
    ``replay.reconstruct`` bit-exact.
  * **Rolled turns use the blind planner for every oracle seat**, ours included
    (``_rollout_decide``). Letting them build real oracles would mean a full
    prediction sweep per rolled turn. Our own future play is understated, but
    identically for every candidate, which is all a comparison needs.

Work is *iteration*-bounded, so a game stays reproducible — and the shape of that
bound is the thing to keep in mind when touching this code:

    nodes per ply = candidates**2 x SEARCH_BEAM

**quadratic in the candidate count**, because the number of openings to keep alive is
itself the candidate count. Depth is the cheap knob and the roster is the expensive
one: going from four candidates to six costs 2.2x, which is more than depth 8 -> 12.
``SEARCH_BUDGET_S`` is a catastrophe guard and must stay one — tripping it makes the
plan depend on the wall clock, and `replay.reconstruct` re-runs `decide` — so the
roster is sized to fit inside it rather than the other way round.

--- Borrowing a move from another bot ---------------------------------------- #

``EXTERNAL_CANDIDATES`` names bots whose ``decide`` is run *for our own seat* and
offered as an opening (`_external_plan`), at every ply, so a sustained borrowed line
is reachable rather than a single borrowed turn. It is the single largest measured
win in this search, and not by a little. At depth 12 against thinker:

    with rusherplus + heuristic     97.5%  (390-10, n=400)   0 timeouts
    postures only                   89.7%  (96-11,  n=107)  13 timeouts

Without them, extra depth **stops paying entirely** — postures-only measures 92.0% at
depth 8 and 89.7% at depth 12, i.e. no better than depth 1. Rolling "knower, more or
less aggressive" forward a dozen turns only compounds a fiction; the depth converts
into wins because there is something structurally *different* in the tree to find.

How often a borrowed move actually wins the search, over 1128 **contested** decisions
(we hold a system adjacent to a live rival). Measure this on contested positions
only: during the land-grab nothing is in contact, every candidate rolls out to the
same material, and the tie-break returns the default by construction, which buries
the effect — the same 1128 decisions read 85% default if the opening phase is folded
in.

    default 54.8%   rusherplus 16.9%   heuristic 14.9%   timid 13.4%

Nearly a third of real decisions are moves knower's own four phases cannot express.
That is also why ``SEARCH_WIDTH`` is 2: the two aggressive postures it used to cover
(`all-in`, `push-for-depth`) fell to 1.3% and 0.5% once a real rusher was a
candidate, and dropping them paid for the whole depth increase and more.

--- How much depth is worth -------------------------------------------------- #

Against thinker, both seatings, 24-node maps (n = decided games):

    depth  1    90.4%   n=114     6 timeouts
    depth  5    96.6%   n=119     1
    depth  8    96.0%   n=400     0
    depth 12    97.5%   n=400     0     <- the top of the slider
    depth 16    95.8%   n=118     2

Read that curve honestly. Depth 1 -> 5 is real and large. **Everything past 5 is
inside the noise**: 12 over 8 is +1.5 pt with SE 1.25 (z = 1.20), and head-to-head
knower@12 vs knower@8 finished 63-57 (52.5%, SE 4.6) — even. Depth 12 came out ahead
in every measurement taken and behind in none, which is why the slider goes there,
but it is a mild preference and not a proven gain. Depth 16 is not better than 12.
Don't re-tune this on a hundred games; the differences here are smaller than that.

**How much depth is worth still depends on whether the opponent is an oracle.**
Against knower@1 depth 12 wins 66.1% (78-40), but against knower@8 only 52.5%.
``_rollout_decide`` plays a non-oracle seat with its *real* ``decide``, so a rollout
against thinker is a faithful simulation and stays informative for a dozen turns; it
plays an oracle seat with ``_blind``, which is a poor model of a deep knower, so a
mirror compounds a fiction and understates what depth is worth in a real game.
Useful depth tracks how well the rollout can model the opposition.

Against claudebot there is little headroom: 96.2% at depth 1, 98.8% (79-1) at 12.

Cost per decide, beam 1 and four candidates, measured untruncated:

                        24 nodes / 2 seats     40 nodes / 6 seats
    depth  1                   0.2 ms                 0.7 ms
    depth  8                  18.3 ms                34.5 ms
    depth 12                  28.0 ms                57.1 ms  (69 ms at the tail)

The right-hand column is the most expensive configuration the menu can produce, at
the top of the slider, and it fits ``SEARCH_BUDGET_S`` with better than 2x headroom —
0% of decides truncate. It is *per decide*, though: six knower seats at depth 12 is
~340 ms of turn resolution, so a full lobby of them is felt even though one is not.
The WASM build has no thread to spare and is the reason the headroom is kept this
wide rather than spent on a wider beam or a fifth candidate.

Depth also plays *faster* and less passively, not more: from depth 1 to 12 against
thinker, timeouts fall 6 -> 0 and games shorten 112 -> 100 turns.

``SEARCH_BEAM`` stays 1 — one continuation kept per opening. Wider measured no
better and costs linearly, and there is a reason to expect that: an opening's score
is the max over its surviving lines, and a max over more noisy rollouts is biased
upward by *sampling*, unevenly across openings. The comparison the search needs is
between openings, not within one.

The remaining ceilings are the evaluation and the roster. `_evaluate` reads material
off the final board, so within a single opening `_prune` can still drop a line that
gives ground early to win later; an eval integrated over the path would fix that. And
the roster is now the dominant cost term, so a fifth candidate has to beat
``candidates**2`` — the cheap direction is a *better* four, not more of them.

Note **depth 0 is thinker-*strength*, not thinker**: ``RESERVE_FLOOR`` is 0 here
against thinker's 1, ``_richness`` peeks a hop further (``BEYOND_DECAY``), and
tie-breaks are deterministic where thinker's draw from ``state.rng``. It measures
stronger than thinker (80%-20%), so the two are not interchangeable.

Forked from ``models/thinker.py`` (commit f94ff20); the four phases and the helpers
below ``_richness`` are thinker's, changed only where the oracle changes them.
"""

from __future__ import annotations

import copy
import math
import random
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace

from starconquest import ai, engine
from starconquest.model import Order

# Lets a sibling oracle bot recognise us (and us it) so two of them proxy each
# other with the blind planner instead of recursing. See `_surrogate`.
IS_ORACLE = True

# --- tunables vendored unchanged from thinker ------------------------------- #
# Kept bit-identical on purpose: a knower-vs-thinker result should measure the
# oracle, not a re-tune. See thinker.py for how these were fitted.
_EDGE = 1.1 / 0.9
DEFEND_MARGIN = _EDGE + 0.05
NEUTRAL_MARGIN = 1.3
ENEMY_NEAR = 1.3
ENEMY_FAR = 1.9
OVERWHELM = 2.0
RESERVE_FLOOR = 0
FRONTIER_GUARD = 0.3            # now applied only against seats we *can't* predict

# --- knower's own ----------------------------------------------------------- #
# thinker pads its margins because it cannot see this turn's launches. Inside the
# known horizon the oracle already has every fleet that can arrive, so the only
# thing left to cover is the combat jitter itself.
KNOWN_MARGINS = True            # False reverts to thinker's padded margins
KNOWN_ATTACK = _EDGE + 0.02
KNOWN_DEFEND = _EDGE + 0.02

# `_richness` also peeks one hop past the system it is pricing, at this weight, so a
# poor system guarding a rich one is valued as the gateway it is. Not an oracle
# change — thinker would benefit too — just not ported back without its own
# measurement.
BEYOND_DECAY = 0.35

TRUST_HUMAN = False             # True lets a human-seat prediction relax guards and
                                # justify snipes, as if they were a bot. Off by
                                # default: a real human is not obliged to comply.

# --- search ----------------------------------------------------------------- #
# Depth comes from the seat's generic `ai_params.aux` knob (config.AI_AUX, whose
# 1.0 default is what keeps an untuned seat on the plain one-turn oracle).
SEARCH_DEPTH_DEFAULT = 1        # what a malformed or absent `aux` falls back to
SEARCH_DEPTH_MAX = 12           # matches the menu slider's top end
SEARCH_WIDTH = 2                # candidate postures a line may branch into
SEARCH_BEAM = 1                 # continuations kept per opening at each ply

# Candidate plans borrowed whole from *other* registered bots and rolled out beside
# our own postures. `POSTURE_VARIANTS` can only ever say "knower, more or less
# aggressive"; a rival's `decide` can propose a move the four phases below
# structurally cannot express — rusherplus throws every garrison at its weakest
# neighbour at once, the heuristic hoards where knower would spend. Neither is
# usually the pick, and neither has to be: the search only takes one that
# *outscores* the default. Names, not functions, and resolved lazily in
# `_external_plan` — models/ files import in sorted filename order, so `rusherplus`
# is not in `ai.STRATEGIES` yet when this line runs.
EXTERNAL_CANDIDATES = ("rusherplus", "heuristic")

# What the AI tab's generic aux slider is called when this bot holds the seat, and
# the range/step it offers (`ai.aux_spec` reads these; see models/README.md).
AUX_LABEL = "Search depth"
AUX_RANGE = (0, SEARCH_DEPTH_MAX, 1)
AUX_INT = True

# A second budget, kept separate from ORACLE_BUDGET_S so depth 1 stays byte-exact.
# Checked *between plies*, so every line is the same depth whenever it fires and the
# comparison stays fair; there is always a whole plan to return.
#
# Tripping this costs the game its bit-reproducibility (the plan starts depending on
# the wall clock, and `replay.reconstruct` re-runs `decide`), so the candidate set is
# sized to fit *inside* it rather than the other way round: 150 ms is about as long
# as a turn may stall in a human game, so it is the fixed constraint and `_prune`'s
# `c**2 * b` node count is what gets cut to meet it. Measured worst case of the most
# expensive legal configuration — 40 nodes, 6 seats, the slider at its top — is well
# under this; see the cost table in the module docstring.
SEARCH_BUDGET_S = 0.150

# Terminal position value. Production dominates: it is the only term that compounds.
EVAL_PRODUCTION = 10.0
EVAL_SYSTEMS = 1.0
EVAL_SHIPS = 0.15
EVAL_DECIDED = 1000.0           # winning/losing outranks any amount of material

_SALT_ROLLOUT = 7               # keeps rollout rngs clear of `_priv`'s other users
_SALT_EXTERNAL = 11             # ...and one per `EXTERNAL_CANDIDATES` entry, from here


@dataclass(frozen=True)
class Posture:
    """The planner's tunables, bundled so a search can vary them per candidate.

    Patching the module globals instead would be process-wide: it would corrupt the
    `_blind` self-model used to predict rivals, and every other knower seat in the
    game. Threading a value keeps a candidate plan's aggression local to that
    candidate. Built by `_posture` from the globals *at call time*, so the globals
    above stay the real tunables and nothing is frozen at import.
    """

    reserve_floor: int
    frontier_guard: float
    defend_margin: float
    neutral_margin: float
    enemy_near: float
    enemy_far: float
    overwhelm: float
    beyond_decay: float
    known_margins: bool
    known_attack: float
    known_defend: float


def _posture() -> Posture:
    """The default posture, read live from the module globals."""
    return Posture(
        reserve_floor=RESERVE_FLOOR,
        frontier_guard=FRONTIER_GUARD,
        defend_margin=DEFEND_MARGIN,
        neutral_margin=NEUTRAL_MARGIN,
        enemy_near=ENEMY_NEAR,
        enemy_far=ENEMY_FAR,
        overwhelm=OVERWHELM,
        beyond_decay=BEYOND_DECAY,
        known_margins=KNOWN_MARGINS,
        known_attack=KNOWN_ATTACK,
        known_defend=KNOWN_DEFEND,
    )


# Candidate stances, as overrides on the default. Index 0 must stay `{}` — it is the
# tuned default, and the search only prefers another candidate that outscores it.
POSTURE_VARIANTS = (
    {},
    {"frontier_guard": 0.6, "reserve_floor": 2},                    # timid
    # --- below here is outside `SEARCH_WIDTH` and not currently searched --------- #
    # Every ply costs `candidates**2 * SEARCH_BEAM` nodes, so a candidate is not free
    # and has to earn its slot in picks. Measured over 1355 *contested* decisions at
    # depth 12 (the land-grab phase is uninformative — with nothing in contact every
    # candidate rolls out to the same material and the tie-break returns the default):
    #
    #     default 53.6%   rusherplus 17.3%   timid 14.2%   heuristic 13.1%
    #     all-in   1.3%   push-for-depth 0.5%
    #
    # The two aggressive postures are the ones that stopped paying, and it is fairly
    # clear why: `EXTERNAL_CANDIDATES` now carries a *real* rusher, which expresses
    # "commit everything" far better than a margin tweak to our own phases can.
    {"frontier_guard": 0.0, "enemy_near": 1.15, "enemy_far": 1.5},  # all-in
    {"beyond_decay": 0.8},                                          # push for depth
    {"enemy_near": 1.6, "enemy_far": 2.2},                          # only sure strikes
)

# A pathological opponent cannot be *interrupted* in pure Python (no threads, no
# signals on WASM), so the only defence is to stop asking the rest of them once the
# turn has already cost too much. Whatever we then failed to predict simply stays
# untrusted, and `_pessimistic_owners` hedges against it the way thinker would.
# Deliberately ~100x the measured cost of a real turn (knower 0.49 ms on a 24-node
# board; every other bot in the roster decides in 6-21 us), so this is dead code
# against any sane opponent. Tripping it does cost this game its bit-reproducibility,
# which beats freezing the browser tab.
ORACLE_BUDGET_S = 0.050

_TRUSTED, _UNTRUSTED = True, False

# Re-entrancy depth. Single-threaded codebase, so a module global is sound. This
# is the backstop that makes recursion impossible even between two *different*
# oracle modules, which `_surrogate`'s identity check cannot see.
_DEPTH = 0

# Last oracle built and last exception swallowed, for tests and debugging. Pure
# records — the planner reads neither. `LAST_ERROR` exists because `decide` has to
# catch everything (nothing upstream does), and a bug that silently downgrades
# knower to blind play would otherwise look exactly like a bot that is merely weak.
LAST_ORACLE = None
LAST_ERROR = None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
@dataclass
class Oracle:
    """What the rest of this turn looks like, as far as it can be known."""

    post: object                      # start-of-turn board + every predicted launch
    trusted: set[int]                 # owners whose post-launch garrisons we believe
    launched: dict[int, int]          # system id -> ships it sends away this turn
    # Pure records, for tests and debugging — the planner reads neither.
    seats: dict[int, str] = field(default_factory=dict)      # pid -> how it was predicted
    orders: dict[int, list] = field(default_factory=dict)    # pid -> predicted orders


def decide(state, pid):
    """Plan against a forecast of every other seat. Never raises.

    Depth comes from the seat's own `ai_params.aux`: 0 is the blind planner, 1 the
    plain one-turn oracle, and N the oracle plus an N-1 ply tree search.
    """
    global _DEPTH, LAST_ERROR

    if _DEPTH:
        # Someone is predicting *us*. Answer as the blind planner: it terminates,
        # and it is the same self-model we use for seats we cannot read.
        return _plan(state, pid, None)

    depth = _seat_depth(state.players[pid]) if pid in state.players \
        else SEARCH_DEPTH_DEFAULT
    if depth <= 0:
        # No oracle at all. Kept ahead of `_build_oracle` so depth 0 costs nothing.
        return _plan(state, pid, None)

    _DEPTH += 1
    try:
        orc = _build_oracle(state, pid)
    except Exception as exc:              # noqa: BLE001 — an oracle is only a luxury
        orc, LAST_ERROR = None, exc
    finally:
        _DEPTH -= 1

    try:
        if depth > 1:
            # `_rollout_decide` resolves each seat itself and routes oracles to
            # `_blind`, so nothing here re-enters us today. The guard stays up across
            # the search anyway: it is the backstop for a rolled seat that calls back
            # into `decide`, which `_surrogate`'s identity check cannot see.
            _DEPTH += 1
            try:
                return _search(state, pid, orc, depth - 1,
                               time.perf_counter() + SEARCH_BUDGET_S)
            finally:
                _DEPTH -= 1
        return _plan(state, pid, orc)
    except Exception as exc:              # noqa: BLE001
        # Nothing upstream catches a bot (engine.py:101, main.py:300 both call it
        # bare), so a crash here would take the whole game down.
        LAST_ERROR = exc
        try:
            return _plan(state, pid, orc)
        except Exception as exc2:         # noqa: BLE001
            LAST_ERROR = exc2
            try:
                return _plan(state, pid, None)
            except Exception as exc3:     # noqa: BLE001
                LAST_ERROR = exc3
                return []


# --------------------------------------------------------------------------- #
# The oracle
# --------------------------------------------------------------------------- #
def _build_oracle(state, me):
    """Run every other seat's decision function and fold the result into a board.

    Seats are predicted in the engine's own order (`engine._collect_orders`:
    ascending pid, skipping neutral/human/dead) so that seats *after* us share one
    rng that advances exactly as the real one will — making their orders
    bit-exact. Seats *before* us have already drawn from ``state.rng`` from a
    position we cannot recover, so they get a private rng and are right except
    where they hit a genuine tie (measured: 99.6% of turns).
    """
    global LAST_ORACLE

    deadline = time.perf_counter() + ORACLE_BUDGET_S
    post = _clone(state, _priv(state, me, 1))
    orc = Oracle(post=post, trusted={0}, launched=defaultdict(int))  # neutrals never launch

    # Seats before us: right logic, right inputs, unrecoverable rng position.
    for q in [q for q in sorted(state.players) if q < me]:
        _predict_seat(state, orc, me, q, _priv(state, q, 2), "likely")

    # Seats after us: one shared rng, positioned where they will really find it.
    shared = random.Random()
    shared.setstate(state.rng.getstate())
    for q in [q for q in sorted(state.players) if q > me]:
        player = state.players[q]
        if player.is_neutral or not player.alive:
            continue
        if time.perf_counter() > deadline:
            break                     # out of budget: the rest stay untrusted
        if player.is_human:
            # The engine never calls `decide` for a human seat (engine.py:99), so
            # this prediction must not advance the shared stream.
            _predict_seat(state, orc, me, q, _priv(state, q, 4), "modelled")
            continue
        if not _predict_seat(state, orc, me, q, shared, "exact"):
            # That seat raised, mutated its board or had to be proxied, so it
            # consumed a different number of draws than the real one will. Every
            # seat after it is off-position now — still worth predicting, but the
            # bit-exact chain is over.
            shared = _priv(state, q, 3)

    LAST_ORACLE = orc
    return orc


def _predict_seat(state, orc, me, q, rng, label):
    """Predict seat ``q`` and fold its launches into ``orc``. True if faithful."""
    player = state.players[q]
    if player.is_neutral or not player.alive:
        return True                       # the engine skips it too — no draws, no orders

    fn, trustworthy = _surrogate(player)
    probe = _clone(state, rng)
    fingerprint = _fingerprint(probe)

    try:
        orders = fn(probe, q) or []
    except Exception:                     # noqa: BLE001 — a broken rival is their problem
        orc.seats[q] = "raised"
        return False

    # Out of contract (models/README.md:22). Its orders may still be sane, but it
    # has proved it can corrupt a board, so believe nothing it implies.
    mutated = _fingerprint(probe) != fingerprint
    if mutated:
        trustworthy = _UNTRUSTED

    if trustworthy:
        orc.trusted.add(q)
        orc.seats[q] = label
    else:
        orc.seats[q] = "mutated" if mutated else "modelled"
    orc.orders[q] = list(orders)

    _apply_predicted(orc, me, q, orders)
    return bool(trustworthy)


def _surrogate(player):
    """(fn, trustworthy) — what will really decide this seat, and can we believe it.

    A human seat is decided by a person, not by code, so it is modelled as another
    knower one level shallower and never trusted. Our own strategy gets the same
    treatment — under whatever filename it was registered as, and likewise any
    sibling oracle — which is what keeps two knower seats from recursing.

    The one case where the model is *exact* is a **depth-0** seat of our own module:
    its whole algorithm is `_plan(state, pid, None)`, which is precisely what
    `_blind` computes. So that seat is predicted the same way but believed, which
    frees the guard `_pessimistic_owners` would otherwise pin against it and lets
    `_garrison` price its vacated systems honestly. A depth-0 seat of a *different*
    oracle module cannot get that, because its blind planner is not ours.
    """
    if player.is_human:
        return _blind, (_TRUSTED if TRUST_HUMAN else _UNTRUSTED)
    # Resolve exactly as ai.decide does (ai.py:84), so even a stale strategy name
    # is predicted correctly: the engine will fall back to the heuristic too.
    fn = ai.STRATEGIES.get(player.ai_strategy, ai.compute_orders)
    if fn is decide:
        return _blind, (_TRUSTED if _seat_depth(player) == 0 else _UNTRUSTED)
    if _is_oracle(fn, player):
        return _blind, _UNTRUSTED
    return fn, _TRUSTED


def _is_oracle(fn, player=None):
    """Is ``fn`` an oracle that must be proxied rather than called?

    Depth is per *seat*, not per module, so a module-level ``IS_ORACLE`` cannot
    describe a game holding both a depth-0 and a depth-3 seat of the same bot. A
    module may therefore also export ``is_oracle_seat(player) -> bool``, which is
    preferred when present; the flag is the fallback for anything that doesn't.
    """
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    per_seat = getattr(module, "is_oracle_seat", None)
    if player is not None and callable(per_seat):
        try:
            return bool(per_seat(player))
        except Exception:                 # noqa: BLE001 — a broken probe is their bug
            pass
    return bool(getattr(module, "IS_ORACLE", False))


def _seat_depth(player) -> int:
    """This seat's search depth, from its generic ``ai_params.aux`` knob.

    0 = no oracle (the blind planner), 1 = the plain one-turn oracle, N = the oracle
    plus N-1 turns of rollout. Tolerant by design: a hand-edited token or a seat with
    no params at all must never take a bot down, so anything unreadable is depth
    ``SEARCH_DEPTH_DEFAULT``.
    """
    try:
        return max(0, min(SEARCH_DEPTH_MAX, int(player.ai_params.aux)))
    except Exception:                     # noqa: BLE001
        return SEARCH_DEPTH_DEFAULT


def is_oracle_seat(player) -> bool:
    """Public: does this seat actually run an oracle? False at depth 0.

    Read by sibling oracle bots (and by our own `_is_oracle`) in preference to the
    module-level ``IS_ORACLE`` flag. Part of the drop-in contract — see
    models/README.md.
    """
    return _seat_depth(player) >= 1


def _blind(state, pid):
    """The self-model: knower with the oracle removed, i.e. thinker-strength."""
    return _plan(state, pid, None)


def _apply_predicted(orc, me, seat, orders):
    """Fold one seat's predicted orders into the post-launch board.

    Uses ``engine.apply_order`` rather than reimplementing it, so the validation and
    the running per-source clamp (engine.py:40-50) are the engine's own — a bot that
    over-commits one garrison is clamped here exactly as it will be for real.

    Orders naming anyone but ``seat`` as owner are dropped, mirroring the engine's
    ``_own_orders``: a seat commands its own ships and nothing else, so honouring
    them here would predict a fleet the engine is about to refuse. The redundant
    "never a system we own" check is deliberate belt-and-braces — the cost is nil and
    silently losing our own garrison off the planning board would be a bad failure.
    """
    for order in orders:
        try:
            if order.owner_id != seat or order.owner_id == me:
                continue
            src = orc.post.systems.get(order.source_id)
            if src is None or src.owner_id == me:
                continue
            before = src.ships
            if engine.apply_order(orc.post, order) is not None:
                orc.launched[src.id] += before - src.ships
        except Exception:                 # noqa: BLE001 — malformed order object
            continue


def _clone(state, rng):
    """A private copy of the board, cheap enough to make one per prediction.

    Per-object ``copy.copy`` rather than ``copy.deepcopy`` — measured 0.10 ms
    against 1.34 ms at 24 nodes, ~14x, which matters on the WASM build — with every
    mutable container rebuilt so a rival cannot reach the real state through one.
    ``lanes`` is shared by reference: it is immutable in practice, and nothing reads
    it but ``rebuild_topology``.
    """
    clone = copy.copy(state)
    clone.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in clone.systems.values():
        s.neighbors = list(s.neighbors)
    clone.fleets = [copy.copy(f) for f in state.fleets]
    clone.players = {pid: copy.copy(p) for pid, p in state.players.items()}
    for p in clone.players.values():
        p.ai_params = copy.copy(p.ai_params)
    clone.adjacency = {sid: dict(nbrs) for sid, nbrs in state.adjacency.items()}
    clone.rng = rng
    return clone


def _fingerprint(state):
    """Everything a prediction is allowed to leave untouched."""
    return (
        tuple((s.id, s.owner_id, s.ships, s.prod_progress) for s in state.systems.values()),
        tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
              for f in state.fleets),
    )


def _priv(state, pid, salt):
    """A private rng derived from the state alone.

    Never the clock, never ``id()``, never ``hash()`` of a string — all three
    would make a game unreproducible from its seed and break
    ``replay.reconstruct``, which re-runs ``decide`` (replay.py:259-264).
    """
    return random.Random((state.seed * 1000003 + state.turn * 9176 + pid * 31 + salt) & 0x7FFFFFFF)


# --------------------------------------------------------------------------- #
# The search — roll candidate plans forward and keep the best
# --------------------------------------------------------------------------- #
def _material(state, pid) -> tuple[int, int, float]:
    """``(systems, ships incl. in-transit, production in ships/turn)`` for ``pid``.

    Deliberately a local helper rather than `fog.player_totals`, which computes the
    same thing: `fog` is presentation-only and the AI is documented never to consult
    it (CLAUDE.md). Cheap enough that duplicating eight lines beats crossing that
    boundary.
    """
    systems = ships = 0
    prod = 0.0
    for s in state.systems.values():
        if s.owner_id == pid:
            systems += 1
            ships += s.ships
            if s.production > 0:
                prod += 1.0 / s.production
    ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
    return systems, ships, prod


def _evaluate(state, pid) -> float:
    """How good this position is for ``pid``, against the strongest rival.

    A differential rather than an absolute, so it still means something in a
    free-for-all: being twice the size of a two-player board is not the same as being
    twice the size of the best of four. Production carries the most weight because it
    is the only term that compounds.
    """
    if state.winner is not None:
        return EVAL_DECIDED if state.winner == pid else -EVAL_DECIDED

    def score(q):
        systems, ships, prod = _material(state, q)
        return EVAL_PRODUCTION * prod + EVAL_SYSTEMS * systems + EVAL_SHIPS * ships

    mine = score(pid)
    rivals = [score(p.id) for p in state.players.values()
              if not p.is_neutral and p.id != pid and p.alive]
    return mine - (max(rivals) if rivals else 0.0)


def _rollout_decide(state, q, humans=frozenset()):
    """The `decide` a rollout runs its seats with.

    Oracle seats — ours included — are played by the cheap blind planner. Letting
    them build real oracles would mean a full prediction sweep on *every* rolled
    turn, which is where the cost would run away. Understating our own future play is
    fine: it is understated identically for every candidate, and a search only needs
    the comparison to be fair.

    ``humans`` are the seats a person really holds, modelled with `_blind` for the
    same reason the oracle does it — there is no code to run for them.
    """
    if q in humans:
        return _blind(state, q)
    player = state.players[q]
    fn = ai.STRATEGIES.get(player.ai_strategy, ai.compute_orders)
    if fn is decide or _is_oracle(fn, player):
        return _blind(state, q)
    return fn(state, q)


def _rollout(state, pid, plan, humans, rng):
    """Play ``plan`` for our seat on a clone of ``state``; return the stepped board.

    One ply, not a whole line — the search grows a tree, so each node is stepped
    separately and kept. ``rng`` is the *ply's* stream, handed identically to every
    node on that ply (common random numbers), so all of them meet the same combat
    jitter and a score gap between siblings reflects the plan rather than the dice.
    It keeps ``replay.reconstruct`` bit-exact for the same reason.

    Note the board is *advanced* here, unlike the oracle's static post-launch board —
    so `state.travel_turns` re-times lanes as `state.turn` climbs under
    `SHIP_SPEED_GROWTH_PCT`. That is correct, not a bug.
    """
    board = _clone(state, rng)

    # A rollout is headless, so every seat has to be driven through `decide`.
    # `_collect_orders` skips a human seat entirely (engine.py:101), which would drop
    # our *own* plan on the floor whenever knower is the autoplayed human — leaving
    # every candidate scoring the same do-nothing board. Clear the flag on the clone
    # and model the seats a person really holds with `_blind` instead. Idempotent, so
    # a board cloned from an already-cleared parent simply rewrites False over False.
    if humans:
        for p in board.players.values():
            p.is_human = False

    def stepped(s, q):
        return plan if q == pid else _rollout_decide(s, q, humans)

    engine.end_turn(board, decide=stepped)
    return board


@dataclass
class _Line:
    """One line of play under consideration, as far as the search has grown it.

    ``root`` is the only part that ever reaches the engine — the orders we would
    actually issue *this* turn. Everything past it exists to price that opening, and
    ``origin`` is which opening it was, which is what `_prune` groups on.
    """

    root: list                  # the plan at ply 0, i.e. what winning this search means
    origin: int                 # index of that plan in the ply-0 candidate list
    board: object               # the position the line has reached
    score: float                # `_evaluate` of that position


def _search(state, pid, orc, turns: int, deadline):
    """Grow a beam of lines ``turns`` plies deep; play the best one's opening.

    Every surviving line branches into the full candidate set at every ply, so the
    search reasons about *sequences* — "rush now, consolidate next turn" is a line it
    can hold, which a one-ply deviation followed by default play structurally could
    not express. The tree that describes is ``len(candidates) ** turns``, which at the
    slider's top end is ~2e9 nodes, so it is grown as a beam: each ply is expanded in
    full and then cut back by `_prune`. Work is linear in depth, not exponential.

    Candidate 0 is the tuned default and `_prune` keeps every opening alive to the
    bottom, so the plan we would otherwise have played is always still in the beam;
    ties are broken toward it, since openings are kept in candidate order. A search
    that finds nothing better is therefore exactly depth-1 knower.
    """
    base = _plan(state, pid, orc)
    if turns <= 0:
        return base

    # Read once, from the real board: `_rollout` clears the flag on every clone.
    humans = frozenset(q for q, p in state.players.items() if p.is_human)

    lines = []
    for i, plan in enumerate(_candidates(state, pid, orc, base)):
        board = _rollout(state, pid, plan, humans, _priv(state, pid, _SALT_ROLLOUT))
        lines.append(_Line(plan, i, board, _evaluate(board, pid)))

    for _ in range(1, turns):
        if time.perf_counter() > deadline:
            break            # every line is the same depth, so stopping here is fair
        if all(line.board.winner is not None for line in lines):
            break            # nothing left to learn; the verdicts are already in
        lines = _prune(_expand(lines, pid, humans))

    best = lines[0]          # origin 0, so an exact tie keeps the tuned default
    for line in lines[1:]:
        if line.score > best.score:
            best = line
    return best.root


def _expand(lines, pid, humans):
    """One ply: branch every live line across the whole candidate set.

    A line that has already reached a decided board is carried through untouched
    rather than dropped — its verdict is the score, and stepping a finished game
    would only churn. The ply's rng is derived from the *parent* board, whose seed,
    turn and pid are shared by every node on the ply, so siblings and cousins alike
    meet identical dice.
    """
    out = []
    for line in lines:
        if line.board.winner is not None:
            out.append(line)
            continue
        # No oracle past ply 0: building one per node would mean a full prediction
        # sweep on every rolled turn, which is exactly where the cost would run away.
        blind = _plan(line.board, pid, None)
        for plan in _candidates(line.board, pid, None, blind):
            board = _rollout(line.board, pid, plan, humans,
                             _priv(line.board, pid, _SALT_ROLLOUT))
            out.append(_Line(line.root, line.origin, board, _evaluate(board, pid)))
    return out


def _prune(lines):
    """Cut each *opening* back to its own best `SEARCH_BEAM` continuations.

    Grouped by ply-0 move rather than pooled, and that is the whole point of the
    search. A single pooled beam fills with continuations of whichever opening leads
    on material *right now*, so the alternative openings — the only thing being
    compared — are gone after one ply, and a candidate that gives ground early to win
    later never gets to prove it. Measured on the pooled version: 94% of surviving
    lines descended from the default and a borrowed move never survived a single cut,
    which made the whole tree an expensive way to re-derive depth 1.

    Per opening, the cut is still greedy on `_evaluate`, which is the honest limit
    here: within one opening a sacrifice line can still be dropped before it pays.
    `sorted` is stable, so an exact tie keeps whichever line was generated first and
    the search stays reproducible.
    """
    groups: dict[int, list] = {}
    for line in lines:
        groups.setdefault(line.origin, []).append(line)
    out = []
    for origin in sorted(groups):
        ranked = sorted(groups[origin], key=lambda line: -line.score)
        out.extend(ranked[:SEARCH_BEAM])
    return out


def _candidates(state, pid, orc, base):
    """Every plan a line can branch into, best-understood first.

    A generator, and deliberately so: the external candidates are the expensive half
    — each runs a whole rival planner — so a caller that stops early must not have
    paid to build what it never scored.
    """
    default = _posture()
    yield base
    for overrides in POSTURE_VARIANTS[1:SEARCH_WIDTH]:
        yield _plan(state, pid, orc, replace(default, **overrides))
    for i, name in enumerate(EXTERNAL_CANDIDATES):
        plan = _external_plan(state, pid, name, _SALT_EXTERNAL + i)
        if plan is not None:
            yield plan


def _external_plan(state, pid, name, salt):
    """Another registered bot's orders *for our own seat*, or None if unusable.

    The oracle runs rival code to learn what they will do; this runs it to borrow
    what it would do in our chair. Same three precautions as `_predict_seat`, for the
    same reasons: a private clone (a rival is not obliged to honour the read-only
    contract), a state-derived rng (both of the bots named above tie-break through
    ``state.rng``, and advancing the real one would leave every seat after us
    predicted off-position), and everything caught (a broken candidate may cost
    itself its slot in the search and nothing more).

    ``ai.STRATEGIES`` is read *here* rather than at import: models/ files load in
    sorted filename order, so anything after `knower` is missing from the registry
    until well after this module's top level has run.
    """
    fn = ai.STRATEGIES.get(name)
    if fn is None or fn is decide or _is_oracle(fn):
        return None            # unregistered, or ourselves — nothing new to propose
    probe = _clone(state, _priv(state, pid, salt))
    try:
        orders = fn(probe, pid) or []
        # Mirrors `engine._own_orders`: a seat commands its own ships and nothing
        # else, so an order naming anyone else would be dropped by the engine anyway.
        return [o for o in orders if getattr(o, "owner_id", None) == pid]
    except Exception:                     # noqa: BLE001 — a broken bot is their bug
        return None


# --------------------------------------------------------------------------- #
# The planner — thinker's four phases, reading the post-launch board
# --------------------------------------------------------------------------- #
def _plan(state, pid, orc, pos: Posture | None = None):
    """thinker's plan. With ``orc`` it reads the future; with ``None`` it is thinker.

    ``pos`` is the stance to plan at; ``None`` means the tuned default. A search
    passes a variant here rather than patching the globals, which would leak into
    every other seat and into the `_blind` self-model.
    """
    if pos is None:
        pos = _posture()
    post = orc.post if orc is not None else state
    sysmap = post.systems
    owned = [sid for sid, s in sysmap.items() if s.owner_id == pid]
    if not owned:
        return []

    max_prod = max(s.production for s in sysmap.values())
    frontier = {sid for sid in owned
                if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors)}
    guarded_against = _pessimistic_owners(post, orc, pid)

    orders: list[Order] = []

    # --- Phase 0: base budgets — spendable ships after each system's guard --- #
    # The blind hedge now only covers seats we could not read. With every rival
    # predicted that set is empty, and the standing guard — measured at 17.6% of
    # thinker's army, nearly all of it never contested — is freed for Phase 3.
    budget: dict[int, int] = {}
    for sid in owned:
        s = sysmap[sid]
        if sid in frontier:
            guard = math.ceil(
                pos.frontier_guard * _max_adjacent_enemy(post, orc, s, guarded_against))
            budget[sid] = max(0, s.ships - max(pos.reserve_floor, guard))
        else:
            budget[sid] = max(0, s.ships - pos.reserve_floor)

    # --- Phase 1: arrival-aware defence -------------------------------------- #
    # A system hit along a long lane can amass defence over several turns, so we
    # schedule reinforcements by *when* the blow lands rather than only pulling
    # one-hop help. Threatened systems that even that can't save are marked doomed.
    #
    # This is where the oracle pays for itself. Blind, a 2-turn strike is detected
    # with one turn to go and no lane is short enough to answer it; here the strike
    # is already in `post.fleets` at t=2, so `helpers` can actually reach it.
    threatened = [sid for sid in owned if _incoming(post, sid, pid, hostile=True) > 0]
    threatened_set = set(threatened)
    threatened.sort(key=lambda sid: (_first_strike(post, pid, sid),
                                     -_incoming(post, sid, pid, hostile=True)))
    doomed: list[int] = []
    for sid in threatened:
        s = sysmap[sid]
        known = _known_horizon(post, orc, sid)
        # The garrison we must have present, and the turn that demand binds.
        worst, t_bind = 0, 1
        for t, ecum in _enemy_arrivals(post, pid, sid):
            margin = (pos.known_defend if (pos.known_margins and t <= known)
                      else pos.defend_margin)
            deficit = (math.ceil(ecum * margin)
                       - _production_by(s, t) - _inbound(post, sid, pid, t))
            if deficit > worst:
                worst, t_bind = deficit, t

        if worst <= 0:
            continue  # production + ships already inbound cover it — keep base guard
        if worst <= s.ships:
            budget[sid] = min(budget.get(sid, 0), max(0, s.ships - worst))
            continue

        # Need outside help that can *arrive by* the binding strike. Pull from
        # neighbours within that range, never from a neighbour also under threat.
        need = worst - s.ships
        helpers = sorted(
            (n for n in s.neighbors
             if sysmap[n].owner_id == pid and n not in threatened_set
             and budget.get(n, 0) > 0 and (post.travel_turns(sid, n) or 99) <= t_bind),
            key=lambda n: (-budget[n], n),
        )
        if sum(budget[n] for n in helpers) < need:
            doomed.append(sid)        # can't be saved in time — abandon it below
            continue
        budget[sid] = 0               # hold the whole garrison and pull the rest
        for h in helpers:
            if need <= 0:
                break
            send = min(budget[h], need)
            orders.append(Order(pid, h, sid, send))
            budget[h] -= send
            need -= send

    # --- Phase 2: abandon the doomed ----------------------------------------- #
    # On the post-launch board this quietly gains its best move: the attacker's own
    # home is now visibly empty, so a system about to be overrun steps *into* it.
    for sid in doomed:
        order = _evacuate(post, orc, pid, sysmap[sid], max_prod, pos)
        if order is not None:
            orders.append(order)
        budget[sid] = 0  # whether it retreated or holds to inflict casualties, don't drain it

    # --- Phase 3: focus fire with staggered pincers -------------------------- #
    targets = [sysmap[n] for n in
               {n for sid in frontier for n in sysmap[sid].neighbors
                if sysmap[n].owner_id != pid}]
    targets.sort(key=lambda t: (-_richness(post, pid, t, max_prod, pos), t.ships, t.id))

    for target in targets:
        # Our budgeted systems adjacent to the target, and how far off each is.
        nbrs = [(post.travel_turns(sid, target.id), sid) for sid in owned
                if target.id in sysmap[sid].neighbors and budget.get(sid, 0) > 0]
        if not nbrs:
            continue

        # Soonest arrival horizon H at which committed force (plus fleets already
        # inbound by then) overwhelms the target. Nearer distances are tried
        # first, so we strike as early as we can and only stagger when massing
        # demands the farther systems too.
        chosen_h, shortfall = None, 0
        for h in sorted({d for d, _ in nbrs}):
            req = _required(post, orc, pid, target, h, pos)
            inbound = _inbound(post, target.id, pid, h)
            committable = sum(budget[sid] for d, sid in nbrs if d <= h)
            if inbound + committable < req:
                continue
            chosen_h, shortfall = h, req - inbound
            break
        if chosen_h is None:
            continue  # can't crack it even at full stretch — leave the ships to mass

        # Launch only the far wave (dist == H) now, covering the part the nearer
        # waves won't; those nearer waves launch on later turns and converge,
        # because next turn this fleet shows up in the target's inbound tally.
        nearer = sum(budget[sid] for d, sid in nbrs if d < chosen_h)
        need = max(0, shortfall - nearer)
        for sid in sorted((sid for d, sid in nbrs if d == chosen_h),
                          key=lambda s: (-budget[s], s)):
            if need <= 0:
                break
            send = min(budget[sid], need)
            orders.append(Order(pid, sid, target.id, send))
            budget[sid] -= send
            need -= send

    # --- Phase 4: leapfrog / flow to the richest front ----------------------- #
    parent = _flow_to_front(post, set(owned), frontier, pid, max_prod, pos)
    for sid in sorted(owned):
        if sid in frontier:
            continue  # the front's leftover stays home as the standing reserve
        b = budget.get(sid, 0)
        if b > 0 and sid in parent:
            orders.append(Order(pid, sid, parent[sid], b))
            budget[sid] = 0

    return orders


# --------------------------------------------------------------------------- #
# Oracle-aware helpers
# --------------------------------------------------------------------------- #
def _pessimistic_owners(post, orc, pid):
    """Rivals whose intentions we do *not* know, and must therefore hedge against.

    Blind, that is everyone — which is exactly thinker's standing guard. With a
    full oracle it is empty. In between sit human seats and anything that raised,
    mutated its board or had to be modelled, and those keep the blind hedge.
    """
    rivals = {p.id for p in post.players.values() if not p.is_neutral and p.id != pid}
    if orc is None:
        return rivals
    return rivals - orc.trusted


def _trust(orc, owner):
    """Do we believe what the post-launch board says about ``owner``'s systems?"""
    return orc is not None and owner in orc.trusted


def _garrison(orc, target):
    """Defenders to expect at ``target`` — post-launch only where that is trusted.

    This is the whole of the "a prediction may only raise a threat" rule on the
    offensive side. For a human seat (or anything that raised, mutated its board or
    had to be modelled) the launches we predicted are added *back*, so knower prices
    the target as if it never moved. A human who does something we didn't foresee
    therefore cannot be punished for it, and a bot that we read exactly is.
    """
    if _trust(orc, target.owner_id):
        return target.ships
    return target.ships + (orc.launched.get(target.id, 0) if orc is not None else 0)


def _known_horizon(post, orc, sid):
    """Last arrival turn at ``sid`` whose fleets the oracle already holds in full.

    Everything launched this turn is in ``post.fleets``. A fleet launched *next*
    turn must still cross a whole lane from a direct neighbour, so it cannot land
    before ``1 + shortest lane into sid``. Every horizon up to that shortest lane
    is therefore complete, and needs no padding for surprises — only for jitter.
    """
    if orc is None:
        return 0
    lanes = [post.travel_turns(n, sid) or 99 for n in post.systems[sid].neighbors]
    return min(lanes) if lanes else 0


# --------------------------------------------------------------------------- #
# Helpers vendored from thinker — identical but for the oracle hooks, the threaded
# `Posture`, and `_richness`'s one-hop lookahead (see ``BEYOND_DECAY``).
# --------------------------------------------------------------------------- #
def _richness(post, pid, system, max_prod: int, pos: Posture) -> float:
    """Value of a system: its own output, plus a discounted peek at the richest
    non-owned neighbour past it. A poor system that opens onto a rich one is
    worth more than its own production alone says — pricing it that way is what
    pushes knower *through* a weak front instead of stalling on it.
    """
    base = max_prod - system.production + 1
    beyond = max((max_prod - post.systems[n].production + 1
                  for n in system.neighbors if post.systems[n].owner_id != pid),
                 default=0)
    return base + pos.beyond_decay * beyond


def _incoming(post, sid, pid, hostile: bool) -> int:
    """Ships inbound to ``sid``: hostile (owner != pid) or friendly (owner == pid)."""
    return sum(f.ships for f in post.fleets
               if f.dest_id == sid and (f.owner_id != pid) == hostile)


def _first_strike(post, pid, sid) -> int:
    """Turns until the first enemy fleet reaches ``sid`` (large if none is coming)."""
    return min((f.turns_remaining for f in post.fleets
                if f.dest_id == sid and f.owner_id != pid), default=99)


def _enemy_arrivals(post, pid, sid) -> list[tuple[int, int]]:
    """Enemy ships reaching ``sid`` by each strike turn, as (turn, cumulative)."""
    by_turn: dict[int, int] = defaultdict(int)
    for f in post.fleets:
        if f.dest_id == sid and f.owner_id != pid:
            by_turn[max(1, f.turns_remaining)] += f.ships
    cum, out = 0, []
    for t in sorted(by_turn):
        cum += by_turn[t]
        out.append((t, cum))
    return out


def _inbound(post, dest, owner, within) -> int:
    """Ships owned by ``owner`` reaching ``dest`` within ``within`` turns."""
    return sum(f.ships for f in post.fleets
               if f.dest_id == dest and f.owner_id == owner and f.turns_remaining <= within)


def _production_by(s, turns: int) -> int:
    """Ships ``s`` will build over ``turns`` turns at its current progress."""
    if s.production <= 0 or turns <= 0:
        return 0
    return (s.prod_progress + turns) // s.production


def _max_adjacent_enemy(post, orc, sysobj, owners) -> int:
    """Largest garrison next door belonging to a seat in ``owners``.

    thinker hedges against every non-neutral neighbour; ``owners`` narrows that to
    the seats whose orders we could not read. Neutrals never attack, and they are
    never in ``owners``. The garrison is the *pre*-launch one — for an unreadable
    seat we don't get to assume the army we're hedging against has gone somewhere.
    """
    best = 0
    for n in sysobj.neighbors:
        o = post.systems[n]
        if o.owner_id in owners:
            best = max(best, _garrison(orc, o))
    return best


def _required(post, orc, pid, target, dist: int, pos: Posture) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns.

    ``target.ships`` is read off the post-launch board, so a system that has just
    sent its army away is priced at what it will actually be defending with. Inside
    the known horizon the margin drops to the bare jitter edge: thinker's ramp
    exists to cover reinforcements it cannot see, and here there are none left to
    see.
    """
    known = _known_horizon(post, orc, target.id)
    ships = _garrison(orc, target)
    if target.owner_id == 0:  # static neutral garrison — no production, no reinforcement
        return max(ships + 1, math.ceil(ships * pos.neutral_margin))
    # Enemy: fold in the reinforcements and production that land before we arrive,
    # and pad more the later we strike (more time for the enemy to react).
    reinforcements = _inbound(post, target.id, target.owner_id, dist)
    defence = ships + reinforcements + _production_by(target, dist)
    if pos.known_margins and dist <= known:
        margin = pos.known_attack
    else:
        margin = min(pos.enemy_far, pos.enemy_near + 0.1 * (dist - 1))
    return max(ships + 1, math.ceil(defence * margin))


def _evacuate(post, orc, pid, s, max_prod: int, pos: Posture):
    """Route a doomed system's whole garrison to the most useful place — or hold."""
    sysmap = post.systems
    ships = s.ships
    if ships <= 0:
        return None

    # (a) Step forward into the richest system we can still take with what's here.
    #     On the post-launch board that includes the home of whoever is attacking
    #     us, which they have just emptied to do it.
    caps = []
    for n in s.neighbors:
        o = sysmap[n]
        if o.owner_id != pid:
            dist = post.travel_turns(s.id, n) or 1
            if ships >= _required(post, orc, pid, o, dist, pos):
                caps.append((_richness(post, pid, o, max_prod, pos), -o.ships, n))
    if caps:
        caps.sort(reverse=True)
        return Order(pid, s.id, caps[0][2], ships)

    # (b) Retreat to the most defensible friend (biggest garrison, richest front).
    friends = [n for n in s.neighbors if sysmap[n].owner_id == pid]
    if friends:
        friends.sort(
            key=lambda n: (sysmap[n].ships, _front_pull(post, pid, n, max_prod, pos), -n),
            reverse=True,
        )
        return Order(pid, s.id, friends[0], ships)

    # (c) Cornered. Only sortie if truly overwhelmed; otherwise hold — a garrison
    #     kills more attackers than a doomed strike on a weak neighbour would.
    enemy_in = _incoming(post, s.id, pid, hostile=True)
    friend_in = _incoming(post, s.id, pid, hostile=False)
    if enemy_in > pos.overwhelm * (ships + friend_in):
        enemies = [n for n in s.neighbors if sysmap[n].owner_id != pid]
        if enemies:
            weakest = min(enemies, key=lambda n: (sysmap[n].ships, n))
            return Order(pid, s.id, weakest, ships)
    return None


def _front_pull(post, pid, sid, max_prod: int, pos: Posture) -> float:
    """How rich a prize the front at ``sid`` faces — its richest non-owned neighbour."""
    best = 0.0
    for n in post.systems[sid].neighbors:
        o = post.systems[n]
        if o.owner_id != pid:
            best = max(best, _richness(post, pid, o, max_prod, pos))
    return best


def _flow_to_front(post, owned, frontier, pid, max_prod, pos: Posture) -> dict[int, int]:
    """Multi-source BFS over owned territory: rear node -> next hop toward the front.

    Seeds are ordered by the richness of the prize each frontier faces, so a rear
    node adjacent to two fronts flows toward the *richer* one. Sorted throughout,
    so a rear system's next hop stays stable while the frontier does, rather than
    oscillating turn to turn.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    seeds = sorted(frontier,
                   key=lambda sid: (-_front_pull(post, pid, sid, max_prod, pos), sid))
    queue = deque(seeds)
    while queue:
        cur = queue.popleft()
        for nbr in sorted(post.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur
                queue.append(nbr)
    return parent
