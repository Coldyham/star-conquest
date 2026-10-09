# marshal design notes: overview and standing

`models/marshal.py` is the strongest bot that predicts nobody. Its notes span
three files, in the order the work happened:

- **This file:** what marshal was built on, what the first measurements
  deleted, the current ladder ("Where marshal stands", which is the table to
  update), and the 2026-09 sweep of its constants.
- [`marshal-pricing.md`](marshal-pricing.md): what a strike or a defence is
  priced against. Covers third-party fleets, garrisons that retreat, doomed
  neighbours, pile-ups, and four tactics that measured null.
- [`marshal-flow.md`](marshal-flow.md): where the surplus goes. Covers Phase 3b
  re-flooding, the successor fixes folded back in (A-C), the empty interior,
  denying the swap, when to evacuate, and feeding captures (from the board's
  human wins).

Later sections often supersede earlier ones. Look for a **Superseded** note
before relying on a constant's history. Roster-wide rules and the measurement
checklist are in [`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## `models/marshal.py` and what the measurements deleted

marshal was commissioned around three ideas: bait an opponent into a system a
neighbour can relieve, value chokepoints, and stop sending "just enough". Only the
third survived contact with a ladder, and the other two are worth recording so
nobody re-derives them.

**Overwhelming force is provable, not a preference.** Combat is Lanchester's square
law, so the ships an attack consumes are `A - sqrt(A^2 - B^2)`, which *decreases*
in `A` and tends to `B^2/2A`. Concentration is therefore rewarded twice: the strike
costs fewer ships, and the capture is held by a stack big enough to keep. What
thinker's own tuning sweep rejected was raising the *threshold* to attack — leaner,
sooner strikes beat over-massing — and that is a different question from what to do
with ships that have no other job this turn. marshal strikes at exactly thinker's
price and then pours the remainder in behind it, worth 54%/72%/80% against the
planner it forks at 24 nodes, 40 nodes and 18 ly/turn respectively. The size of
what was being parked: measured over 705 player-turns, thinker keeps 64.3% of its
army sitting at frontier systems and only 23.1% of it in transit.

**The standing frontier guard interacts with commitment, and ablating one at a
time hides it.** Swept *alone* against the blind planner, `FRONTIER_GUARD = 0.3`
looks worthless: 85-85 on a 24-node mirror, and negative at 40 nodes and at
18 ly/turn. marshal was built with it at 0.0 on that evidence and it was a
mistake — with Phase 3b on, the guard is what makes committing survivable, since
a system that just emptied itself into an attack is precisely the one that needs
cover. Sweeping it again with 3b enabled, against knower at depth 0:

    guard     18n   24n   30n   40n   mean   timeouts   turns
    0.0       38%   48%   62%   66%    53%         --      --
    0.30      56%   59%   66%   70%    63%         50     144
    0.45      60%   60%   72%   69%    65%         59     162
    0.55      60%   65%   74%   66%    66%         80     182
    0.70      59%   60%   77%   71%    67%        114     195

Two lessons. The obvious one is that one-at-a-time ablation is not enough when
mechanics interact; the guard reads as dead weight until something else spends
the ships it was hoarding. The subtler one is that the win rate above is not the
whole objective — past 0.3 it is flat while games stretch 35% longer and timeouts
more than double, which is the stalemate failure mode `models/README.md` warns
about. marshal holds thinker's exact 0.3, which also keeps its margin
attributable to mechanisms rather than to a re-tune.

The original zero-guard result was also *size*-blind. marshal at guard 0.0 beat
the blind planner 66% at 40 nodes but lost 38-40% at 12-18 nodes, crossing over
around 26 — and 18 is the default. Any bot result quoted at a single map size
should be treated as provisional.

**Chokepoints lose.** Normalised Brandes betweenness over the lane graph, cached
per topology and folded into `_richness`. The measure itself is sound and cheap —
these maps are planar and sparse (average degree 2.5-2.7) yet 34-49% of nodes are
cut vertices, so degree says nothing while betweenness separates cleanly (top 1.00,
median 0.21, 2.3 ms at 24 nodes, computed once). It still loses at every weight
tried: 48%/48%/50% at 0.10/0.20/0.40, negative at 40 nodes, and consistently the
longest games and most timeouts in the whole sweep. The bot buys corridors instead
of winning. Production compounds and topology doesn't, which is the short version.
Pocket-sealing — valuing a capture by how much frontier it removes — fails for a
duller reason: 67% of candidate targets score identically and only 6.7% seal at
all, so it mostly adds a constant. 47% either way.

**Two bugs fixed on the way past**, both of which the removed guard used to mask.
`_EDGE = 1.1 / 0.9` hardcoded `COMBAT_JITTER = 0.10`, which is a menu knob, so every
margin in thinker, knower and claudebot silently dropped below break-even when the
jitter slider moved. marshal fixed it for itself first, reading the knob live and
flooring the tuned absolutes over it; the fix has since moved into
`combat.edge_attacking`/`edge_defending` (see [`bots.md`](bots.md), "Break-even margins") and the rest of the roster
reads it too, so this is now a roster property rather than a thing marshal alone
gets right. And Phase 1 sized relief for the worst arrival horizon but scheduled
it for *that* horizon's turn, while 16.1% of real deficits bind later than the
first arrival — the standing guard used to absorb the early wave. marshal sizes
for the worst horizon and requires delivery by the earliest.

**Standing aside in a free-for-all.** The one idea here that came from watching a
human play rather than from reading the code: when you are boxed between two
rivals, the node that joins them is worth less than its production says. Take it
and you have replaced a border *they* were contesting with two borders they
contest with you — a bad trade for as long as your income trails their combined
income. `_wedge` prices a target by how many rivals *past the first* it borders,
so the wall position is discounted and the rivals are left adjacent and busy with
each other.

The payoff is sharply non-monotonic in the size of the field, which is why the
term is gated on `WEDGE_MIN_PLAYERS`. Measured by pairing marshal against a copy
of itself with the term off, both seats in the *same* game, rotated through every
position so map and luck are shared:

    3 players, 30 nodes    48% (126-137)   gate off, so the two are identical
    4 players, 30 nodes    64% (160-89)
    4 players, 40 nodes    62% (168-101)
    5 players, 40 nodes    62% (190-117)

The three-player row is a **null cell** and worth keeping for that alone: the gate
makes both variants emit identical orders, so whatever it reads is the harness's
own noise. It reads 48%, and that is what licenses reading 62-64% as real — an
earlier 40-seed sweep put the same null at 43%, which would have made a 58% result
look like a finding. Any future bot experiment here should build itself a null
cell the same way.

Why it fails at three players: with a single pair of rivals there is no fight to
stand aside from, so declining the node just feeds whichever of them takes it, and
the lost income beats the diplomacy. Why it fades past five: the board is crowded
enough that nearly every target borders two rivals, so the term stops
discriminating and becomes a constant offset.

Only the defensive half of the human strategy is implemented. The other half —
*abandoning* a system specifically to bait two rivals into contesting it — needs a
model of what those rivals value, which is knower's territory rather than a blind
bot's.

## Where marshal stands

Relocated from the module docstring, which carried ~100 lines duplicating this
section — including a second, drifted copy of the wedge table below. One home.

Against **knower at search depth 0** — the blind planner marshal forks, and the
honest baseline for a non-oracle bot. Ladder, both seatings, 120 games a cell;
the paired null (that planner against itself) reads 50%:

    nodes      18    24    30    40
    marshal   56%   59%   66%   70%

Against knower's **oracle**, marshal has no answer, and the search depth is not
what does it — the prediction itself is the wall. 80 games a cell:

    knower depth    24 nodes   40 nodes
         0              52%        71%    <- no oracle: marshal is ahead
         1              41%        39%
         2              42%        45%
         4              29%        35%
         8              20%        38%

Switching the oracle *on* costs 11 points at 24 nodes and 32 at 40. Deepening it
then buys knower much less, and on the larger board nothing at all past depth 2,
which matches knower's own finding that its rollout plateaus. No heuristic buys
back a rival that reads your orders before you issue them; that needs prediction
of its own, or deliberate unpredictability.

Across the **defender-advantage** knob, 60 games a cell at jitter 0.10 — a check
that the margins hold up off their tuned point, not a tuning:

                          vs thinker   vs claudebot
    DEFENDER_ADVANTAGE 1.0        86%          98%
    DEFENDER_ADVANTAGE 1.25       68%          91%
    DEFENDER_ADVANTAGE 1.5        76%          98%

Weakest in the middle rather than at either end, and never below 68%.

**Full roster ladder** (`uv run python -m tests.sim --ladder --trials 30`, 18
nodes, default settings — 1680 games, 72 timed out and are excluded from the
percentages). **This table is the current one** — update it, not the module
docstring, the next time marshal or the roster's pricing changes. Re-run
2026-10-08 with learner added ([`learner.md`](learner.md), "As a bot"). Every cell
moved from the run before it, not only learner's: since then each seat decides
from its own derived rng and combat rolls keyed dice (`engine._Dice`), and
actuary's `MIN_GAIN` dropped to 1e-9 ([`actuary.md`](actuary.md)). The previous
reading, actuary at its planned opening, was actuary 310, marshal 274, knower
270, thinker 160, claudebot 91, heuristic 50, rusherplus 28, with actuary beating
knower 66% and marshal 71%:

    learner 357 (22%), actuary 343 (21%), marshal 307 (19%), knower 262 (16%),
    thinker 166 (10%), claudebot 84 (5%), heuristic 52 (3%), rusherplus 37 (2%)

    head-to-head (row's win rate vs column)
                knower  actuar  marsha  thinke  claude  heuris  rusher  learne
      knower         —     30%     36%     89%     95%    100%    100%      4%
      actuary      70%       —     64%     97%    100%    100%    100%     53%
      marshal      64%     36%       —     98%     98%    100%    100%     30%
      thinker      11%      3%      2%       —     89%     96%    100%      3%
      claudebot     5%      0%      2%     11%       —     66%     75%      0%
      heuristic     0%      0%      0%      4%     34%       —     61%      0%
      rusherplus    0%      0%      0%      0%     25%     39%       —      0%
      learner      96%     47%     70%     97%    100%    100%    100%       —

learner tops it on the strength of knower. It loses to actuary, and most of its
96% against knower is the oracle flag, which keeps knower from running it; see
[`learner.md`](learner.md), "As a bot".

knower here is at its default, Predict. With `--aux knower=0` (Off) the same
ladder read actuary 321, marshal 291, knower 209, thinker 172, actuary beating
knower-0 98% (actuary at Greedy, not re-run). For all three Oracle settings against marshal and thinker over
more seeds, see [`knower.md`](knower.md), "Each Oracle setting against thinker
and marshal".

The same ladder in two other cells, since this one cannot see anything keyed on
long lanes. 30 seeds each; the symmetric one re-run with actuary (1260 games),
the slow one not yet (`tests.sim` has no ship-speed flag; actuary's
head-to-heads there are in `actuary.md`, and marshal beats it 68%):

    symmetric, 18 nodes, 6 ly/turn     269 timeouts
    actuary 247 (25%), marshal 237 (24%), knower 227 (23%), thinker 120 (12%),
    claudebot 99 (10%), heuristic 41 (4%), rusherplus 20 (2%)
    marshal vs knower 75%, actuary vs marshal 47%, actuary vs knower 64%

    random, 24 nodes, 3 ly/turn        96 timeouts, 900 games, before actuary
    marshal 270 (34%), knower 224 (28%), thinker 157 (20%),
    claudebot 84 (10%), heuristic 64 (8%), rusherplus 5 (1%)
    marshal vs knower 76%

Among the six older bots the order is the same in all three, but marshal leads
knower by a much wider margin in both. That gap is not the gate: the symmetric cell has no 5-turn lane either.
Head to head it is 60 games a pair less timeouts, so read it as a direction.

The previous reading, before `DENY_SWAP`, was marshal 251 / knower 248 with the
head-to-head at 51% and 58 timeouts. The same caveat as the paragraph below
applies: 60 games a pair cannot resolve a few points, so this says nothing
regressed, not that the knower cell moved.

Re-run after the retreat fixes ([`marshal-pricing.md`](marshal-pricing.md), "Two doomed neighbours") and it did not move: 251/248 against
251/249, the knower head-to-head 51% against 50%, one more timeout. That is not
a null result about those fixes — this cell is 60 games a pair against opponents
marshal already beats 89-100% of the time, so it has no resolution at the
1-2 point scale their own paired sweep measures them at. It is here as a
regression check, and what it says is that nothing regressed.

marshal took the top of the ladder here, and level with knower head-to-head, on
the strength of dropping the jitter premium from its attack margin — see
"Garrisons run away" in [`marshal-pricing.md`](marshal-pricing.md). The previous reading of this table had it second at
219 against knower's 255, losing the head-to-head 35%. Both the 251/249 gap and
the 50% cell are within noise of a tie; what is not noise is that a bot which
predicts nobody now matches the oracle, having been 30 points behind it.

An earlier reading of this table had marshal's win *share* fall 247→219 from a
pre-back-port table, which read like the 2026-09 re-tune regressing it — but
that table's opponents were still pricing fights off the stale `1.1/0.9`
hardcode, not
`combat.edge_attacking()`/`edge_defending()`, so it measures old marshal against
a weaker roster rather than against this one. Isolated with a direct A/B — the
pre-re-tune `marshal.py` dropped into the *current*, back-ported roster as a
seventh strategy, same seeds, same 900-game methodology, so it fights the same
thinker/claudebot/knower the re-tuned marshal above does:

    knower 291 (25%), marshal 250 (22%), marshal_old 241 (21%), thinker 187
    (16%), claudebot 99 (9%), heuristic 46 (4%), rusherplus 42 (4%)

    marshal vs marshal_old: 60%/40%       marshal vs knower:     35%
    marshal_old vs knower:  29%           marshal vs thinker:    75%
    marshal_old vs thinker: 74%           marshal vs claudebot:  92%

New marshal beats old marshal head-to-head and is at least as good against
every real opponent (clearly ahead against knower, a wash against thinker and
claudebot). So the re-tune is a net improvement; the roster-wide share drop is
the back-port making thinker/claudebot/knower stronger, not marshal getting
weaker. Don't re-litigate this from the win-count columns alone — they aren't
comparable across the back-port; a fresh regression claim needs its own A/B
against the current roster, the same way.

## A stagger's nearer wave is reserved

Phase 3 picks an arrival horizon, launches the far sources, and *relies* on the
nearer ones firing next turn to converge on the same target. Their budget was
never reserved, so a later, poorer target could spend it and the pincer silently
failed to materialise — and once Phase 3b existed, 3b could eat its own second
wave. Reserving it is what makes a staggered strike a plan rather than a hope.

## More ideas measured and deleted

Each built against the configuration of its day and removed rather than kept on
the strength of the idea.

**Reinforceability-scaled guards.** Relax a frontier guard wherever a neighbour
could genuinely relieve the system inside its warning window — a fleet down an
L-turn lane is first visible with L-1 turns to spare, so relief R turns away
arrives iff `R <= L-1`. It frees a great deal: 3.5:1, and 52.8% of assignments
cost nothing at all. But in the moments the guard was actually load-bearing, the
relief was out of range 76.8% of the time. Measured 46% against simply setting
the guard to zero, which is the same idea taken to its limit and needs no
machinery at all.

**Splitting a breakthrough's surplus.** After Phase 3 has priced three weak
systems in front of a system holding an army, the leftover all rides with the
richest strike — 4/6/18 rather than 8/8/12, so two of the three are taken at
exactly their price and hold only 3 and 4 ships afterwards. Spreading it in
proportion to richness looks obviously safer and is not: 63%/64%/63% at spread
0/0.5/1.0 against knower's depth-0 planner, 140 games a cell across four map
sizes. The square law barely punishes overkill on a weak garrison, so total
survivors are the same either way (24 against 25 on the traced board) and only
their distribution moves. *Taking* all three was never at stake — Phase 3
launches at every affordable target before 3b touches the remainder.

**Dropping the guard against a system already falling (2026-10).** A frontier
system's guard is sized against the largest rival garrison next door, so when
our fleets landing this turn are sure to take that neighbour, the guard looks
spent: next turn the system is no longer a front, and its ships could leave a
turn sooner. Built three ways behind flags: (1) ignore such a neighbour when
sizing the guard; (2) the same, and stop counting the system as a front in
Phase 4 so the freed ships flow forward; (3) as (2), but only once the falling
neighbour is the system's last non-owned one. "Sure to take" was priced exactly
as Phase 3 prices a strike: fleets landing this turn at or above `_required` at
a one-turn horizon. It fires on 27% of decides at 24 nodes / 6 ly-per-turn
(13% at 12) and frees about one guard ship per decide.

    tools/sweep.py-style mirrored duels vs stock marshal, 1000 seeds,
    random 18/6, 24/6, 30/6, 18/9, 24/12 (10000 games an arm)
    null (flags off)                        50.0%   exactly, every cell
    (1) guard only                          49.5%   z = -0.87
    (2) guard + flow                        49.1%   z = -1.72
    (3) last neighbour only, + flow         48.4%   z = -3.04   (47.3% at 24/12)

Timeouts rose with every arm (920 → 1018/1059/1095). The prediction is not
the problem: 99.8% of the systems flagged were taken that turn (98.2% at 12
ly/turn). What it overlooks is that the front does not end with the capture.
A system just taken from a rival usually borders the garrison that fled it
(`_shortfalls`; "Garrisons run away" in `marshal-pricing.md`), and 13.7% of these captures were retaken
within four turns at 6 ly/turn, 38.9% at 12. The guard is exactly the reserve
that holds or retakes them, which is also why `FEED` pays. Not run against
knower, whose oracle would only add the same-turn snipe into the vacated
system on top of a loss already measured against marshal. Deleted.

## `FRONTIER_GUARD`: how it came to be 0.40, documented as 0.3

Worth recording because the argument and the value came apart. The docstring
asserted twice that the guard "stays at thinker's 0.3", and that holding
thinker's exact value "keeps every point of the margin attributable to a
mechanism rather than to a re-tune" — while the constant had been `0.40`.
thinker and knower are both still at `0.3`. So marshal shipped a re-tune
documented as not being one, at a value appearing in **none** of the swept rows
(0.0/0.30/0.45/0.55/0.70): it sat between the two nearest the sweep measured.

Resolved by measurement rather than by restoring 0.3 — the guard is now `0.55`
on the sweep below, and the attributability argument is formally dropped: three
of marshal's margins are its own figures now, not thinker's, and the reason to
prefer any of them is the measurement rather than its provenance.

## The 2026-09 tuning sweep

A re-sweep of every marshal constant, paired against unmodified marshal. Results
first, then the two methodological points, which matter more than any single row.

**Adopted from it:** `ENEMY_NEAR` 1.3 -> 1.15, `ENEMY_FAR` 1.9 -> 1.5,
`FRONTIER_GUARD` 0.40 -> 0.55. Every other constant measured null or worse and
was left where it was.

> **Superseded.** `ENEMY_NEAR`, `ENEMY_FAR` and `NEAR_PAD` no longer exist:
> marshal's attack margin is now the defender-advantage multiplier alone, with no
> pad, absolute or distance ramp. See "Garrisons run away, so the jitter premium
> buys almost nothing" in [`marshal-pricing.md`](marshal-pricing.md). This section is kept as the record of how the ramp
> was tuned and of the two methodological points at the end, which still stand —
> and note that the sweep's own finding that `ENEMY_NEAR` had a *plateau from 1.0
> to 1.2* was the first sign of what the removal later confirmed: the level barely
> mattered because the fight it was priced for mostly does not happen.
> `FRONTIER_GUARD` and the guard sweep are untouched.

**Only one combination beat the stock tuning.** 40 seeds over random 18n/24n/40n
duels plus 30n/4p, at the default 6 ly/turn, `n` decided games, `z` against 50%:

    variant                        W-L      n    rate      z   timeouts
    FRONTIER_GUARD .55 + NEAR 1.15   267-174    441   60.5%   4.43     39
    ENEMY_NEAR 1.1                   249-203    452   55.1%   2.16     28
    FRONTIER_GUARD 0.55              238-199    437   54.5%   1.87     43
    ENEMY_NEAR 1.2                   244-207    451   54.1%   1.74     29
    FRONTIER_GUARD 0.45              242-206    448   54.0%   1.70     32
    ramp slope 0.0 (from 0.1)        240-211    451   53.2%   1.37     29
    neutral-aware guard x1.0         230-214    444   51.8%   0.76     36
    FRONTIER_GUARD 0.6               218-203    421   51.8%   0.73     59
    null (marshal vs marshal)        229-229    458   50.0%   0.00     22
    flat garrison floor of 1         178-261    439   40.5%  -3.96     41

The pair is superadditive — 54.5% and 55.1% alone, 60.5% together — and win
*share* over all games rises with it (56% against the null's 46%), so it is not a
timeout artefact. `FRONTIER_GUARD` is a plateau from 0.45 to 0.55 and falls off
at 0.6; `ENEMY_NEAR` is a plateau from 1.0 to 1.2, and flattening the ramp slope
to zero does the same job — so the operative quantity is the ramp's *level at
typical distance*, not its shape or its endpoints.

**`ENEMY_FAR` was mistuned, and only measurable at the bottom of the speed
slider.** At 6 ly/turn, 1.6 and 2.4 are bit-identical to the null: the cap needs
`dist >= 7` and nothing plans that far ahead. Pooling only the cells where it is
live (1 and 3 ly/turn, and 12 nodes at 3), `ENEMY_FAR = 1.5` reads **62.1%
(64-39, z = 2.46)** against 1.9, while 2.4 reads 48.1%. It also unblocks the slow
regime: timeouts fall 56 -> 47 at 1 ly/turn and 18 -> 11 at 3, with median game
length 307 -> 280 and 290 -> 239. See "Lane length across the parameter space"
in [`bots.md`](bots.md) for why the three terms of `_enemy_margin` are each live in one band only.

**The frontier-guard gain does not survive the speed knob.** `FRONTIER_GUARD =
0.55` reads 58.9% at the default and **45.4% (79-95) pooled across 3, 12 and 30
ly/turn and a 12-node map** — 40.7% at 12 ly/turn alone. The combination is
neutral off-default (50.3%, 91-90) rather than negative, so its 60.5% should be
read as a default-configuration result, not a global one.

**Against the rest of the roster** (30 seeds, 24n + 40n duels), which is what
catches a tuning fitted to your own copy:

    variant                    vs thinker      vs knower
    marshal (base)          95.5% (106-5)   39.8% (43-65)
    FRONTIER_GUARD 0.55     95.4% (104-5)   43.3% (45-59)
    ENEMY_NEAR 1.15         88.7% (102-13)  41.1% (44-63)
    the pair                94.5% (104-6)   47.2% (51-57)
    the pair + neutral gd   95.5% (106-5)   48.6% (51-54)

`ENEMY_NEAR` alone gains 5 points in self-play and **loses 7 against thinker**,
which is the signature of tuning to a copy of yourself: only the pair is safe.
The pair also takes marshal from 39.8% to 47.2% against knower, the same
direction as the self-play result. claudebot and raider are at 100% for every
variant — a ceiling, carrying no information.

**Negative results worth not re-deriving.** A *flat* garrison floor is
catastrophic and worsens with map size: `RESERVE_FLOOR = 1` scores 34.8%/25.0%/
6.9% at 18/24/40 nodes, and `= 2` scores 12.7%/6.3%/1.4%. So marshal's `0` is
strongly correct, and no fix for the exposure below can take that shape.
`BEYOND_DECAY` and `OVERWHELM` show no signal either side of their tuned values.
And **opening the wedge gate at three players is a wash**: at 100 seeds it reads
51.4% (304-288) on random and 52.1% (137-126) on symmetric, both z = 0.68. A
20-seed run had read 52.5% and 60.3%, which is what a 263-game null of 50% turns
into at n=58 — the earlier figure was noise, and gating the change on
`state.mode` buys nothing over the flat version because on symmetric maps the two
are identical by construction.

**The wedge penalty is already on its plateau.** `RIVAL_WEDGE` was swept at
1.25/1.5/1.75/2.0 against its tuned 1.0, 60 seeds over 4- and 5-player games on
both map modes — about 1240 decided games a variant. Nothing separates: 50.2%,
49.6%, 50.8%, 51.2%, largest z = 0.82, and non-monotonic in the knob, which is
what noise looks like. 2.0's 51.2% *did* replicate a 20-seed reading of 51.2%
exactly, so a ~1-point effect may well be real; pooling both runs (831-793) still
gives z = 0.97, so it cannot be established and is not worth having. Neither of
the wedge's two constants has anywhere to go.

**The wedge itself is worth much more on symmetric maps than random ones.**
Switching it off costs ~4 points at 4 and 5 players on random maps and ~15 on
symmetric (33.3% and 34.8% with it off). That follows from the generator: a
symmetric map rotates one sector about a shared contested centre, so every seat is
structurally guaranteed to border two rivals near the middle, where on a random
map it is an accident of layout. Every symmetric cell is timeout-dominated
though — the 3-player null timed out in 342 of 600 games — so those rates are
"of the games that ended", a biased sample of a stalemate-prone configuration.

**Marshal's territory is mostly empty, and that is where it loses systems.**
Instrumented per turn over 20 games a cell, for marshal's own seat:

    24 nodes, vs marshal      share of its systems holding nothing
      bordering an enemy                              7.7%
      bordering only neutrals                        19.1%
      interior (borders nothing it does not own)      46.8%
    systems it lost that were empty when the turn began   35.8% (485/1356)

The guard works where it applies; everything behind it is open. `_max_adjacent_
enemy` skips neutrals ("neutrals never attack" — true, but a neutral one lane
from a rival is a system that rival can be standing in next turn), and `_incoming`
only watches fleets aimed at systems marshal already owns, so a fleet crossing
toward the buffer is invisible until it has taken it. Between a fifth and a third
of everything marshal loses was undefended at the start of the turn. A candidate
fix — threat propagating one hop through a neutral buffer, priced with
`combat._survivors` and discounted — measured 51.8% (z = 0.76) in self-play and
**26.7% (8-22) at 12 nodes and 3 ly/turn**, where holding ships back against a
threat many turns away is ruinous. Not shipped. A probe written to exploit it
deliberately (cheapest non-owned neighbour first, never consolidate) lost 240
games out of 240, which left open whether the exposure was worth closing.

> **Since measured, and the exposure is mostly not one.** Those empty systems are
> overwhelmingly *evacuated* ones — the doomed branch doing its job — and a
> concentrating probe built to punish the rest loses to stock marshal in every
> cell. See "The empty interior is the retreat" in
> [`marshal-flow.md`](marshal-flow.md) before building anything against it.

**Two methodological notes.** The harness paired every line-up with its exact
mirror (the two variants swapped between seats) on the same seed, so with
deterministic bots a seat advantage cancels *structurally* — every null above
reads exactly 50.0%, not approximately. That is worth rebuilding rather than
re-deriving: the free-for-all rotations used for the older tables do not cancel
at more than two seats, which is why their null cells read 43%, 48% and 52% in
three different runs. A 55% against a null that could be sitting at 43% means
much less than a 55% against a null pinned at 50%. Second, **cell choice can
erase a result**: symmetric two-player duels time out in 64 of 80 games and
1 ly/turn in 56 of 60, so both are unusable as measurement cells however
interesting they are to play.
