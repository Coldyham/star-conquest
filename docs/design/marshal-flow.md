# marshal design notes: where the surplus goes

The third of marshal's three files (overview and current standing:
[`marshal.md`](marshal.md); pricing a fight:
[`marshal-pricing.md`](marshal-pricing.md)). It covers Phase 3b and Phase 4:
re-flooding a target that is already covered, the three successor fixes folded
back into marshal (`FLOW_AVOIDS_ABANDONED`, `RELIEF_AWARE`,
`FAST_GUARD_WEIGHT`), the "empty interior" that turned out to be the retreat,
`DENY_SWAP`, when a doomed garrison should leave, and `FEED`, which came from
studying the board's human wins. It also notes the one idea not yet measured.
Roster-wide rules are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

## Phase 3b re-flooding an already-covered target, and a settled dead end's stranded surplus

Found by inspection of a real game, not a sweep: Phase 3's horizon search sets
`struck[target.id]` the moment `inbound + committable >= req` for *some* horizon,
with no check on whether `inbound` (fleets already dispatched on an earlier turn)
covers it on its own. A neutral several turns down an otherwise-empty branch,
already sent enough to take, stays in `targets` (still neutral) and in `struck`
every turn until the wave lands — and Phase 3b, seeing it `struck`, was pouring
*every* neighbouring source's entire remaining budget into it again, every one of
those turns, on the theory that "the target was already priced and is already
being attacked." True the turn the wave launches; false on every turn after,
where nothing further is needed from anywhere. A tiny board makes the bug
obvious: a 40-ship system one lane from a 6-ship neutral, 8 ships already
in flight and sufficient — the unfixed bot sends the *other 32* into the same
target, next turn, for no reason (`test_commitment_does_not_re_flood_an_already_
covered_target`).

`shortfall = req - inbound` was already computed for exactly this — it is the
gap beyond what is already inbound, before this turn's commitment — so the first
cut gated `struck`/`pincer_held` on `shortfall > 0` for *every* target, rival or
neutral, rather than on `chosen_h` merely being found.

**The same information exposes a second, distinct waste.** A frontier system's
own leftover budget has always stayed home — `frontier` systems are
unconditionally skipped in Phase 4's flow-to-front, reasoning that a front might
need its own reserve for its own next strike. That reasoning does not hold for a
system whose *only* non-owned neighbour is a neutral that is now `settled`
(covered, per above): nothing behind a settled neutral can ever threaten or need
reinforcing, so there is nothing left to hold a reserve *against*. Recording
which neutral targets settle this way in the same Phase 3 pass and excluding a
frontier system from the "keep reserve" set once every one of its non-owned
neighbours has settled lets that surplus leapfrog to a real front instead —
still deferring to any neighbour that borders a live rival or an unresourced
neutral, which keeps its reserve exactly as before
(`test_a_settled_dead_end_frontier_flows_its_surplus_onward`).

**The first cut regressed hard at high `DEFENDER_ADVANTAGE`, and the two fixes
above are not why.** Paired against a copy with both fixes reverted:

    duel (run_ladder)                         W-L        n     rate      z
    default combat, 18 nodes                402-366     768    52.3%  +1.30
    default combat, 12 nodes                383-363     746    51.3%  +0.73
    default combat, 30 nodes                272-292     564    48.2%  -0.84
    DEFENDER_ADVANTAGE 1.5, 18 nodes        334-388     722    46.3%  -2.01
    DEFENDER_ADVANTAGE 1.25, 18 nodes       409-447     856    47.8%  -1.30

Splitting the two fixes apart (each alone against the reverted copy, at
`DEFENDER_ADVANTAGE 1.5`) pinned it on one of them cleanly: the frontier-flow fix
read a clean null (450-446, 50.2%, z=+0.13, n=896) while the re-flood gate alone
read **394-480, 45.1%, z=-2.91, n=874** — worse than the combined reading, and
unambiguous.

**Why:** `_enemy_margin()` (marshal's price for attacking a *rival*-owned
target) deliberately carries no jitter cushion of its own — see "Garrisons run
away" in [`marshal-pricing.md`](marshal-pricing.md); a beatable garrison usually flees, so paying for the dice buys
almost nothing. The old bug was accidentally supplying that missing cushion for
free, every time it kept re-flooding an "already covered" siege — and that
cushion turns out to matter exactly in the ~13% of cases the garrison *doesn't*
flee, a share this file already measured as rising sharply with
`DEFENDER_ADVANTAGE` ("a high advantage is exactly the setting at which a
defender *can* hold and therefore does"). A neutral target has no such gap to
begin with: `_neutral_margin()` prices its own jitter cushion honestly, since a
neutral can't flee or bluff. So the fix is scoped to neutral targets only —
`if shortfall > 0 or target.owner_id != 0`, leaving a rival-held target's
behaviour exactly as it was before either fix existed.

**Re-measured** with that scope, against the same reverted copy:

    duel (run_ladder), default combat, 18n   382-381     763    50.1%  +0.04
    DEFENDER_ADVANTAGE 1.5, 18 nodes         358-370     728    49.2%  -0.44
    DEFENDER_ADVANTAGE 1.25, 18n, seeds 1-500  418-447    873    47.9%  -1.25
    DEFENDER_ADVANTAGE 1.25, 18n, seeds 501-1000 433-422  855    50.6%  +0.38
    DEFENDER_ADVANTAGE 1.25 pooled           851-877    1728    49.3%  -0.62

    melee (run_swap)                        marshal   defenseonly   others         finished
    3p: + knower, 24n                          204         192      knower 185        581
    4p: + knower/rusherplus, 18n               220         179      knower 185, r. 3  587
    speed=2.0 (long lanes), 24n duel           187         162                        349 (57.7%→53.6%, z=+1.34)

The regression is gone (18n default duel is now an honest 50.1% null, both
elevated-advantage cells settle near 50% once the second seed batch is pooled —
the first adv-1.25 batch alone (47.9%) is the same false alarm [`marshal.md`](marshal.md) already
warns about under "The 2026-09 tuning sweep": don't trust a single high-timeout,
high-advantage reading without doubling it). The trade-off is real, not free:
narrowing the scope to neutrals-only gave back some of what the unscoped version
measured, in duels (52.3% → 50.1%) and in the long-lane cell (57.7% → 53.6%,
since a distant rival siege can be just as long-lived as a neutral chain and no
longer gets the same treatment). What survives is smaller but unambiguous and
regression-free: the melees still show a clear, repeatable gap (204 vs 192, 220
vs 179), and the reported bug — a several-hop dead-end neutral branch soaking up
reinforcements that could have gone to a real front — is fixed exactly as
reported, with nothing borrowed from a mechanism that needed to stay put. Not a
re-tune of a margin, so no `RISK_PARITY`-style constant to float against
`DEFENDER_ADVANTAGE` here; the fix is a bookkeeping correction, scoped to
exactly the case that has no jitter cushion to lose.

**Re-applied to rival sieges on long lanes, it pays** (`RIVAL_REFLOOD_MIN_TURNS
= 5`). The regression above was measured at the default speed, where lanes run
2-4 turns; the long-lane cell (57.7% -> 53.6%) is what the scoping gave back.
So the gate now also covers a rival-held target once the strike's horizon is at
least `RIVAL_REFLOOD_MIN_TURNS` turns. The neutral-only "settled" rule for
Phase 4 is unchanged: a rival can reinforce, so a source facing a covered rival
siege stays a live front and keeps its reserve. Measured against marshal with
`DENY_SWAP` shipped (see "Denying the swap" below), since both touch the same
Phase 3b pour into a rival:

    tools/sweep.py, 200 seeds       K = 4           K = 5           K = 8
    24n, 2 ly/turn                  56.0% +2.20     56.0% +2.20     56.2% +2.29
    24n, 3 ly/turn                  52.2%           54.2% +1.56     51.4%
    18n, 6 ly/turn                  48.3% -0.67     50.3%           50.0% (inert)
    18n, 6 ly/turn, adv 1.5         49.6%           50.0% (inert)   50.0% (inert)

K = 4 reaches the default-speed lanes and is the only arm leaning below 50%
there, so K = 5 shipped: effectively inert wherever the advantage regression
was measured. Fresh seeds confirm the gain and show no high-advantage cost on
long lanes:

    K = 5 over DENY_SWAP alone            W-L        n     rate      z
    24n, 2 ly/turn, seeds 1-500         452-345     797    56.7%  +3.79
    24n, 2 ly/turn, seeds 2001-2300     281-215     496    56.7%  +2.96
    24n, 3 ly/turn, seeds 1001-1300     270-248     518    52.1%  +0.97
    24n, 3 ly/turn, seeds 2001-2300     294-240     534    55.1%  +2.34
    18n, 3 ly/turn, seeds 1001-1300     279-227     506    55.1%  +2.31
    18n, 3 ly/turn, seeds 2001-2300     275-236     511    53.8%  +1.73
    24n, 3 ly/turn, adv 1.25            222-206     428    51.9%  +0.77
    24n, 3 ly/turn, adv 1.5             148-145     293    50.5%  +0.18
    melee 3p + knower, 24n, 3 ly/turn   233-228    (knower 111 of 600)   null

**Symmetric maps cannot measure it.** Gate on against gate off, 300 symmetric
seeds a cell: 85-94% of slow-lane games time out even at 1500 turns (51-91
decided games per cell, 51.0-55.3%, all null), and the default-speed cell reads
exactly 50% (inert). Against knower instead, on the same 150 seeds, the two
arms read 90% / 88%, 80% / 77% and 88% / 85% of only 17-57 decided games.
A perfectly fair start on long lanes is a stalemate between these bots, so
there is nothing to read in either direction.

**It and `DENY_SWAP` overlap but are not the same fix.** Each alone, against
neither, and against each other, on the same seeds (2001-2300), pooled over
24n at 2 and 3 ly/turn and 18n at 3:

    comparison                       W-L        n     rate      z
    DENY_SWAP vs neither           886-675    1561    56.8%  +5.34
    gate vs neither                873-665    1538    56.8%  +5.30
    gate vs DENY_SWAP              790-755    1545    51.1%  +0.89
    both vs DENY_SWAP              850-691    1541    55.2%  +4.05
    both vs gate                   788-724    1512    52.1%  +1.65

Equally strong alone and level head to head, so both are cutting the same
waste: surplus poured after a rival siege that no longer needs it, leaving a
long-exposed source. They are partly additive. The gate adds about five points
on top of `DENY_SWAP` (reproduced over three seed batches), while `DENY_SWAP`
adds about two on top of the gate (not significant). If only one were kept, the
gate is the simpler one: a skip where `DENY_SWAP` has hold arithmetic. Both
shipped, since the pair beats either.

## A non-oracle successor to marshal

Three more fixes, built and measured on a byte-for-byte fork (`models/test.py`,
forked at `c227288`, deleted once folded back in — everything below is folded
into `models/marshal.py` directly, constants after its `RETREAT_NEAREST` line)
using a new paired A/B harness, `tools/sweep.py` (mirrored duels against a fixed
baseline, so a config identical to the baseline reads exactly 50.0% before
anything else is trusted; see the tool's own docstring for the full protocol).
Strict non-oracle throughout: every new term reads board facts only — whose
systems are calm, how far they are — never a rival's decision rule or strategy
name.

**Stage 0 — regime map of marshal's own existing constants**
(`results/stage0.txt`, 18,200 games, 7 cells spanning 12-40 nodes and 3/6/12
ly/turn): swept `FRONTIER_GUARD`, `BEYOND_DECAY`, `OVERWHELM`, `RISK_PARITY`,
`NEUTRAL_MARGIN`, `DEFEND_PAD`. No board-size or speed-regime gate needed for
any of them — every arm read NOT SIGNIFICANT in every cell except `nocommit`
(the sanity anchor, confirms `COMMIT_SURPLUS` still matters a lot: 29.4%) and a
small reproduced negative on `NEUTRAL_MARGIN=1.2` (47.1% — confirms the current
1.3 is fine). The one regime effect that *is* real is the per-lane one the third
mechanism below targets.

**Where marshal's ships actually die** (per player per game, 24 nodes / 6
ly-per-turn, ~195 lost): 38 in Phase 4 flow sent into a system the same turn's
Phase 2 is abandoning; 29 in full-price strikes that met a garrison grown since
launch (81-84% of lost strikes); 22 in relief/flow landing on a system captured
in flight (honest, unfixable blind — the strike and the capture are decided
against the same shared, unmutated start-of-turn state); 16 in forward-steps that
met reinforcement; 10 in cornered sorties. At 12 ly/turn add 14/game of guards
dying in place to 1-turn strikes (73% of them at or under their own guard).

### A. `FLOW_AVOIDS_ABANDONED` — Phase 4 must not feed a system Phase 2 is giving up

`live_frontier` was every frontier system with an unsettled non-owned neighbour,
and `_flow_to_front` seeded from all of them with nothing removing the
`doomed - saved` set Phase 1/2 had just built. A rear system routinely flowed its
surplus toward a doomed front whose garrison was retreating — often to that very
rear system — and the two crossed in flight, landing the surplus on the enemy.
Fix: `giving_up = frozenset(sid for sid in doomed if sid not in saved)`, built
independently of `AVOID_ABANDONED` (which only governs where `_evacuate` itself
retreats to), subtracted from both `live_frontier` and the `owned` set the BFS
routes through.

### B. `RELIEF_AWARE` — a visible strike is priced against the relief that could reach the garrison

`_required` priced a rival target against its own garrison, the owner's
in-flight reinforcements and production over the flight — never against a calm
neighbour the owner could still move in during the turns the strike stays
visible. A `dist`-turn strike is visible for `dist - 1` turns before it lands (a
1-turn strike is never visible at all), which is exactly the window a calm
neighbour has to help. `_relief_capacity(state, target, warning)` sums the
garrisons of the target owner's systems reachable within that window and prices
in `ceil(RELIEF_AWARE * capacity)`, board facts only.

**The user's prior going in:** a similar-shaped feature had previously made
marshal too cautious (the 46% guard result under "The 2026-09 tuning sweep"
in [`marshal.md`](marshal.md)), and the payoff was expected to depend on the opponent — knower, marshal
and a human reinforce a threatened system, claudebot and rusherplus do not, so
against the second group the term is pure over-pricing. Checked directly rather
than assumed: swept against **thinker** (reinforces, `results/stage1b_thinker.txt`)
and **claudebot** (never reinforces, `results/stage1b_claudebot.txt`), 19,200
games total. Against thinker, stock marshal already wins 92.9%; `RELIEF_AWARE`
pushes it to 94.7-95.8%, a further, significant improvement — correctly
anticipating real relief pays off. Against claudebot, stock marshal already wins
98.5%; every weight (0.25/0.5/1.0) reads statistically indistinguishable from
stock marshal (98.6-98.7%, all NOT SIGNIFICANT) — no measurable harm, because the
margin over claudebot is already so large a slightly bigger ask costs nothing.
Weights showed no significant difference from each other in three independent
tables; 0.5 was the pre-registered choice and reads numerically highest in all
three, so it shipped.

### C. `FAST_GUARD_WEIGHT` — a guard across a 1-turn lane is a sunk cost, not a deterrent

`_max_adjacent_enemy` sized the frontier guard off the largest adjacent rival
garrison regardless of lane length. Across a ≥2-turn lane the guard sees the
strike coming and Phase 1/2 can reinforce or evacuate; across a 1-turn lane the
strike is invisible — it lands the turn it launches — so a guard sized against
it is pure sunk cost. 73% of garrisons that died to a 1-turn strike were at or
under their own guard (14 ships/player/game at 24 nodes / 12 ly-per-turn). Fix:
weight a rival garrison reachable in exactly one turn by `FAST_GUARD_WEIGHT`
(0.0 ships it entirely). This *is* the regime gate Stage 0 was checking for,
keyed per lane on `state.travel_turns` rather than on board size or a global
speed setting, so it tracks the speed slider and mid-game lane-length growth on
its own. Bit-identical to the unweighted figure wherever no adjacent lane is 1
turn, and therefore INERT (not measurable) at 6 ly/turn on 18-40 nodes, where no
lane is that short — it only bites once `ship_ly_per_turn` or node density
pushes a lane down to 1 turn.

### Measurement: the combination, ablations, and cross-opponent checks

Six cells throughout: `random:18:6`, `random:24:6`, `random:40:6`, `random:24:12`
(keeps C out of the INERT gate), `random:18:3` (gives A and B the most flight
time), `random:24:6,defender_advantage=1.25` (the historical killer cell for
past marshal ideas).

**Batch 1a** (`results/stage1a.txt`, 33,600 games, 400 seeds): every mechanism
solo beats marshal, and the combination (`full`: A+B+C together) is the
strongest row — **60.6% pooled [59.1, 62.0], REPRODUCED, better than baseline in
all 6 cells**, including the advantage-1.25 cell (64.9%). `only_fast` pools to a
NOT-SIGNIFICANT ~51% because 5 of 6 cells have no 1-turn lanes at all
(structurally inert there — see C above), but at the one cell that does have
them (`random:24:6,ship_ly_per_turn=12.0`), `only_fast` (weight 0.0) reads
**57.4%, z=4.07, REPRODUCED-strength**, judged per-cell rather than pooled for
exactly the reason C is INERT elsewhere.

**Batch 1c — leave-one-out** (sweep key `b1b9fa9af40b66fa`, 4 arms × 6 cells ×
400 seeds = 19,200 games): confirms each of A/B/C actually contributes to
`full`'s 60.6% rather than one mechanism carrying the other two. Each `drop_X`
arm sat below `full`, so nothing was simplified out.

**Stage 2 — acceptance** (`results/stage2.txt`, 4800 games, 400 seeds): `final`
(the combination, now the shipped defaults) vs stock marshal — **60.6% pooled
[59.1, 62.0], z=14.01, REPRODUCED** (half A 60.8%, half B 60.3%), every cell
"better than baseline", no cell timeout-dominated (worst: 15% at the
advantage-1.25 cell, confirmed by direct check to be a true ~17% rate, not the
smoke harness's small-sample artefact — see the harness-quirk note below).

**Stage 3 — cross-opponent and roster regression.** Against **knower**
(`results/stage3_knower.txt`, oracle, 2400 games): `final` 61.9% vs stock
marshal 54.0% pooled — pairwise "separated", `final` does not sit below marshal.
Against **thinker** (`results/stage3_thinker.txt`, reinforcing, 4800 games):
`final` 97.4% vs stock marshal 92.9% pooled — separated in every cell. 5-bot
ladder (`--ladder --trials 30 --nodes 24`): `final` ranked 1st (33% wins) ahead
of stock marshal 2nd (30%), head-to-head 63%-37%. `tools/position_suite.py
--bots marshal test` (directional, 26 positions from real games): `final` won
69.2% of positions vs marshal's 65.4%, faster more often (8.7% vs 4.3% of
shared-finish positions), same recovery rate. No veto condition was hit
anywhere, so nothing was dropped and nothing needed re-tuning.

**Decision: A-C already clear the acceptance bar on their own, so a fourth
candidate mechanism (`DENY_SWAP`, capping a source's commitment against a rival
by what the rival's own garrison would need to step into it — piloted at
81-91% for the stepper but never built) was left out of scope.** Building an
unvalidated, unpiloted mechanism when the goal is already measurably met would
be exactly the unmeasured complexity this file's own convention argues against.
If pushing the win rate higher later becomes the goal rather than clearing the
bar, this paragraph used to point at the "empty interior" weakness as the
better-motivated target. It has since been measured and is not one (next
section), which left `DENY_SWAP` — since built, and shipped in a narrower form
than the pilot described; see "Denying the swap" below.

**Folded into `models/marshal.py` directly** rather than shipped as a second
bot (`models/test.py` deleted, `tests/test_test.py` deleted, its mechanism
tests ported into `tests/test_marshal.py`): the improvement was not a different
playstyle worth keeping side by side, only a strictly better version of the same
one, so a second roster entry would just be two names for the same strategy at
different vintages.

**A harness quirk worth knowing before running more `tools/sweep.py` sweeps.**
When a sweep has many cells (5-8), the smoke gate's automatic seed count is only
`ceil(20 / (cells * 2))` — as few as 2-4 seeds per cell — and `SMOKE_SEED_BASE`
is fixed, so a single genuinely-rare stalemate seed landing in a cell's tiny
smoke sample **deterministically** fails that cell's smoke check on every re-run,
even though the cell's true timeout rate is well under the 50% threshold. Hit on
the advantage-1.25 cell here: the smoke's 4-game sample read 100% timed out,
while a direct 30-seed check read 17%. Diagnose with a direct 20-40-seed check
before dropping a cell, and if reordering the cell list to land it on both smoke
seeds instead of being starved to a handful after a bad one fixes it (as it did
here), that is a legitimate workaround, not a fudge.

## The empty interior is the retreat: a concentrating probe, and a fix with nothing to fix

"The 2026-09 tuning sweep" ([`marshal.md`](marshal.md)) left one exposure open: 35.8% of the systems marshal
loses were empty when the turn began, and the only probe aimed at it had lost 240
of 240 by spreading itself thin. Two things were needed — a breakdown of *where*
those losses happen, and a probe that concentrates rather than spreads.

**Where marshal loses systems.** A duel against itself, 24 nodes, 6 ly/turn, 40
games, every loss classed by what the system bordered when the turn began:

    bordering a rival       2317 lost   (84%)   900 of them empty at turn start
    bordering only neutrals    7 lost   (0.3%)    3 empty
    interior                 423 lost   (15%)   176 empty

The neutral buffer the earlier fix propagated threat through is where marshal
loses **seven systems in forty games** — the candidate fix was aimed at a case
that effectively does not occur. And every one of the 423 interior losses (423 of
423) is a fleet *already on the lane* when the turn began, landing that same
turn, with no neighbour lane short enough to relieve it. The fleet was launched
at a frontier system from a rival system marshal has since taken behind it — the
swap — which is what makes the target read as "interior" by the time it falls.

**They are evacuations, not an undefended rear.** Tracking each hostile fleet from
the turn it is first visible: 410 of the 423 interior losses (97%) were out-shipped
at first sight, and in 307 (73%) the garrison left while the fleet was on its way.
Over *every* system lost while empty, 1027 of 1079 (95%) emptied while the
attacking fleet was visible. The empty-at-start statistic is overwhelmingly
Phase 2 moving a doomed garrison out ahead of a strike it cannot hold — which
"Garrisons run away" measured as worth having and "Two doomed neighbours" tuned
(both in [`marshal-pricing.md`](marshal-pricing.md)).
At 12 ly/turn the residue is 66 empty losses in 40 games, every one a 1-turn
strike, which is the trade `FAST_GUARD_WEIGHT = 0` already makes deliberately.

**The concentrating probe** (`models/spearhead.py`, measured at `b4f7839` and
then deleted — a probe, not a roster bot) is marshal itself plus one overlay, so
it is exactly as competent everywhere else and any gap is the overlay's; an arm
with the overlay switched off came back bit-identical to stock marshal in every
smoke game, which is the check that licenses that claim:

  * **One hammer.** Each turn, the largest garrison it holds that borders a rival
    is the hammer. If that stack can take a rival neighbour at marshal's own
    `_required` price it goes in, at the target that opens the most: its
    production rate plus that of the rival systems past it the remainder could
    still take (`DEPTH_WEIGHT`). Next turn the stack sits on the capture, is
    still the largest garrison, now borders whatever lies behind, and goes
    again. Nothing is remembered; the hammer is re-derived from the board.
  * **Everything feeds it** (`FEED_HAMMER`). Every own-to-own move marshal
    emits that is not relief for a threatened system is rerouted one hop along
    the shortest owned path to the hammer, so the economy converges on one
    point instead of marshal's flow to every front.
  * **All in, or over the guard** (`LEAVE_GUARD`). The first version sent the
    hammer's whole garrison; the second keeps marshal's own frontier guard at
    home and swings only the rest.

`tools/sweep.py` against stock marshal, 5 cells (18/24/40 nodes at 6 ly/turn, 24
at 12 and at 3), every row REPRODUCED across both seed halves:

    arm                                       W-L        n    rate       z
    all in, fed (150 seeds)              336-1106     1442   23.3% -20.28
      ...unfed                           375-1070     1445   26.0% -18.28
      ...no depth term                   309-1123     1432   21.6% -21.51
    over the guard, fed (100 seeds)       324-535      859   37.7%  -7.20
      ...unfed                            367-501      868   42.3%  -4.55

Worst at 3 ly/turn (9.2% all in, 24.6% over the guard) and least bad at
12 ly/turn (42.4% and 44.0%, the latter not significant), where lanes are one turn
and nothing sees a strike coming for either side. Feeding the hammer is worth
nothing or less; leaving the source empty is what cost the first version most of
its games, because marshal walks into the vacated source behind the stack.

**It does get into the interior — and it does not matter.** Interior losses per
game it inflicts on marshal, against what other opponents inflict:

    opponent            24 nodes   40 nodes
    marshal                 10.4       14.0
    spearhead (guard)       15.2       19.2
    thinker                  4.9        7.3
    knower                   1.8        2.5

The probe penetrates half as often again as marshal does, and still loses 38%
to 62. The most dangerous bot on the roster, knower, penetrates least of all. A
system taken in marshal's rear is retaken at the same one-ship price it fell for,
and the stack that took it is not fighting anywhere that matters. Concentration
pays through the square law only in a fight, and against a bot that evacuates
what it cannot hold there is rarely a fight to win.

**The travel-time-scoped fix, built and inert.** The best-motivated closure was
`DEEP_RELIEF`: let Phase 1 draw relief from owned systems *past* the threatened
system's neighbours, by owned-path travel time within the deadline — holding
nothing back in advance, reacting only to fleets already visible, which is what
the neutral-buffer attempt got wrong. It came back bit-identical to stock marshal
in every smoke game, and instrumenting Phase 1 says why: of 6575 threatened-system
decisions at 24 nodes and 6 ly/turn, **none** had a two-hop source inside the
deadline (1 in 10047 at 3 ly/turn). The deadline is the earliest arrival, and two
lanes never fit inside the one-lane warning a strike gives. Not shipped, since it
does nothing.

So the exposure is closed as a question rather than as a patch: what looks like
an undefended interior is the retreat rule leaving systems empty on purpose, the
residue is either a 1-turn strike no guard can see or a swap nobody could
relieve, and the strongest probe built against it is a worse bot than the one it
probes.

## Denying the swap: shipped for long lanes, and only on the surplus

**The swap is common.** Every seat decides against the same start-of-turn board,
so a strike from S on a rival's T and that rival's own launch from T into S go out
on the same turn, neither side seeing the other's order, and both systems change
hands. Instrumented over 20 duel seeds at 24 nodes and 6 ly/turn: **1440 of 6942**
of marshal's strikes on a rival (21%) met a same-turn launch out of the target into
the source against itself, 1878 of 8154 (23%) against knower, 475 of 3128 (15%)
against thinker.

**`DENY_SWAP`** caps a source's commitment so what stays behind can hold against
the target's entire garrison stepping in: `ceil(DENY_SWAP * T.ships *
_defend_margin())`, less what S will build over the lane back. Rival targets only
— a neutral does not move.

**Applied to Phase 3's priced strike it is a disaster**, and monotonically so.
`tools/sweep.py` against stock marshal, six cells (18/24/40 nodes at 6 ly/turn,
24 at 12 and at 3, and advantage 1.25), 200 seeds, every row REPRODUCED:

    DENY_SWAP        W-L        n    rate       z
       0.5        993-1085    2078   47.8%   -2.02
       1.0        588-1175    1763   33.4%  -13.98
       1.5        278-1253    1531   18.2%  -24.92

with timeouts climbing 322 -> 637 -> 869 — the stalemate signature. Tracing it:
the cap mostly *declines strikes* rather than protecting sources (strikes on a
rival fell from ~10,000 to ~5,500 over the same games, and the per-launch
step-in rate barely moved, 12.8% to 12.0%). A strike the cap prices out is a
target the rival keeps.

**Surplus-only is the version that works** (`DENY_SWAP_SURPLUS_ONLY`): Phase 3
strikes at its price exactly as before, and the cap applies to Phase 3b's pour
alone. Pooled over the same six cells it is a null — 50.3% at 0.5 and 51.4% at
1.0 — but one cell stood out: **56.8% at 3 ly/turn** (z = +2.39). One cell of
twelve readings is what a false positive looks like, so it was re-run alone on
400 fresh seeds at 18 and 24 nodes: **56.8% again (706-537, z = +4.79),
REPRODUCED**, and a win *share* of all games of 44.1% against 33.6%, so not a
timeout artefact. At 12 ly/turn it read 48.5% — not significant, but the wrong
side.

**So it is a long-lane mechanism, gated per lane** (`DENY_SWAP_MIN_TURNS`), the
same shape as `FAST_GUARD_WEIGHT`: keyed on `state.travel_turns` rather than on
a global speed setting. Lane lengths by cell: 3 ly/turn is almost all 4-8 turns,
6 ly/turn 2-4 (a third to a half at 4), 12 ly/turn 1-2. A third, fresh seed
batch, 300 seeds:

    gate          default speed + 12 ly/turn    3 ly/turn + adv 1.25
    K = 4         50.8% (1129-1095)  null       55.1% (772-628)  z = +3.85
    K = 5         50.2%              null       54.1%            z = +3.09
    K = 6         bit-identical (inert)         53.5%            z = +2.60

K = 4 keeps the whole slow-lane gain (57.0% and 57.7% in its two 3-ly cells), is
an honest null at the default speed, reads **exactly** 50.0% at 12 ly/turn
(structurally inert — no lane there is 4 turns) and 50.6% at advantage 1.25. It
shipped at `DENY_SWAP = 1.0`, surplus-only, `K = 4`.

**Against the rest of the roster** (200 fresh seeds, 24 and 18 nodes at 3 ly/turn
plus 24 at 6, stock marshal and the shipped version each against the same
opponent): against **knower** 72.5% -> 74.9% (78.2 -> 80.9 and 75.7 -> 79.4 in the
two slow cells, level at 6 ly/turn) — not separated pairwise, but the same
direction as self-play and nowhere below stock. Against **thinker** 97.3% ->
96.9%, a ceiling carrying no information. The full `--ladder --trials 30` at 18
nodes is a regression check at that sample, not a measurement: marshal 258
(31%), knower 249, knower head-to-head 57%, 64 timeouts (from 251/248/51%/58).

Why only on long lanes is not established. The arithmetic of the hold points the
other way — the source builds for longer before a slow step-in lands, so the hold
is *smaller* on a long lane — so the likelier reading is how long a source sits
exposed: on a long lane the emptied system is open for many turns to anything
nearby, and the surplus kept home is what keeps it.

## When a doomed garrison leaves: as soon as it is doomed, and it barely matters

Phase 2 evacuates the turn a system is judged doomed. The alternative is to hold
until the last turn out: the garrison keeps building (and those hulls leave with
it instead of being lost), and the wait can open a capture for `_evacuate`'s
step-forward branch, or a relief, or a diverted attacker. Against that, a
garrison that leaves early is back in play sooner.

There is room to wait. Turns between the doomed call and the blow, over every
abandon decision in 20 mirror duels at 24 nodes (repeat decisions on an
already-emptied system included, so this overstates volume):

    ly/turn    1 turn   2    3    4    5+
    6            51%   38%  11%   0%
    3            23%   23%  22%  16%  17%
    12          100%    —    —    —    —       (structurally inert)

Built as `EVAC_AT` (hold until the blow is due within N turns) with
`EVAC_STEP_EARLY` (while holding, still take a capture the moment one opens),
and swept paired against stock marshal, 300 seeds:

    arm                               default speed        3 ly/turn + adv 1.25
    last turn, capture if one opens   50.7% (836-812)      52.3% (691-629)
    last turn, no early capture       50.9% (827-799)      51.3% (671-638)
    two turns to spare                50.3% (836-825)      51.7% (700-653)

Null in every cell, but all twelve readings sat at or above 50%, which is what a
real one-to-two-point lean toward waiting would look like — so the strongest arm
was re-run on 400 fresh seeds against the bot as shipped (`DENY_SWAP` on): 50.2%
(1087-1080) at the default speed and **48.6% (932-986)** in the slow cells, 46.2%
at 24 nodes and 3 ly/turn. The lean did not replicate; if anything it turned.
Deleted rather than kept at zero.

The two effects roughly cancel, which is the same shape as holding a doomed
system to sacrifice-and-retake ([`marshal-pricing.md`](marshal-pricing.md)): the hulls a waiting garrison saves are a
turn or two of one system's production, and the turns it spends waiting are
turns the whole garrison is out of the game. A capture that opens during the wait
is rare, because the step-forward branch has already looked for one on the turn
the system was doomed.

## What the board's human wins say (2026-09-30)

The leaderboard holds human wins on maps the bot column could not win, and
those are always posted scores, so their replays are public (`public_replays`,
readable with the anon key in `leaderboard/js/config.mjs` — no secret needed).
42 of them are still reproducible under the current rules. Each was rebuilt with
`replay.reconstruct` and set against marshal in seat 1 on the same setup via
`sim.play_settings`, then measured identically on both sides. Marshal lost 22 of
those setups and won 20; the 20 are the control.

Read it for what it is. In most of the 22, **marshal is also the opponent**
(seat 2, three-player 11-node maps and 7-node duels), so this is how a person
beats marshal, set against marshal's own mirror on that seed — which is partly a
coin flip. The maps are few, often replayed several times, and a person gets
retries.

    seat 1, the 22 setups marshal lost            human    marshal
    median length (turns)                            57       167   (3 hit 600)
    systems owned at turn 40                        51%       29%
    income share at turns 20 / 40 / 60         43/67/88  34/34/26
    systems lost per game                           3.5        25
    attack landings that failed                      7%       20%
    launched ships that were own-to-own transfers   64%       42%

**Captures stick.** Call a rival capture *safe* when the ships that landed,
plus our garrisons next to it, outnumber what can strike back — the adjacent
rival stacks, rival fleets inbound, and the garrison that just fled. 76% of the
person's are safe and 87% of those are still theirs ten turns on. Marshal's are
safe about half the time (51% in seat 1, 54% as the opponent) and kept 58%/46%;
its unsafe ones flip back 82% of the time. Nearly every system *any* bot loses
is one it had just evacuated (86-95%), so marshal against marshal is a loop —
evacuate, the other side takes it empty, the evacuee takes it back — at about
25 systems lost per seat per game, against the person's 4.

**Where the loop comes from** — every marshal launch tagged by the branch that
issued it, mirror duels at 24 nodes / 6 ly-per-turn, 60 seeds (a strike carrying
surplus is tagged by its Phase 3b pour):

    branch                        rival captures/seat/game   kept 10 turns
    Phase 3b pour                         43.8                   52%
    _evacuate (a) step forward            19.6                   18%
    Phase 3 fresh strike                   4.3                   25%
    _evacuate (c) cornered sortie          3.7                   11%
    Phase 3 follow-up wave                 1.8                   46%

### Built, measured, deleted: a hold test on the capture

`_holds(target, force, kept)` asked whether a capture survives the counter in
both outcomes: the garrison stands (keep the survivors, face the largest rival
stack next door) or flees (keep the whole force, face a neighbour plus the
returning garrison), with our adjacent garrisons counted in at `HOLD_SUPPORT`.
Board facts only. Duels against stock marshal, `tools/sweep.py`, 200 seeds, four
cells (random 18/24/30 nodes at 6 ly/turn, 24 at 12), each seed both seatings:

    arm                                              pooled     verdict
    Phase 3 fresh rival strikes, weight 1.0           49.1%     null
      ...weight 0.75 / 0.5                       49.2 / 49.3%   null
      ...weight 1.0, no support counted               41.9%     REPRODUCED worse
    _evacuate step-forward, weight 1.0                46.7%     REPRODUCED worse
      ...weight 0.5                                   48.9%     null
    both at 1.0                                       45.9%     REPRODUCED worse
    step-forward, also counting the force that        42.2%     REPRODUCED worse
      takes the system being left, weight 1.0
      ...weight 0.5                                   46.4%     REPRODUCED worse

Free-for-all rotations (the variant, stock marshal and fillers, 60 seeds, share
of the wins the two marshals took between them) agree: 46-56% for the Phase 3
gate, 47-51% for the step-forward gate, and 42-48% once it counts the force
behind it (11 nodes 3p, 18 nodes 3p, 21 nodes 4p with thinker, 31 nodes 5p with
thinker and knower). The same variant on the 22 lost human setups won 2 against
stock's 1.

Monotone in how hard the loop is suppressed — 48.9, 46.7, 46.4, 42.2 — and for
the strongest arm worse in every 6 ly/turn cell (38.9-41.3%), null only at 12
ly/turn (47.7%), where most lanes are a single turn and the counter lands before
any of this can matter. No cell to gate it into.

The mechanism works and the result does not follow it. The step-forward gate
halved that branch's captures (19.6 to 10.7); counting the force behind it
brought all rival captures from about 73 to 19 per seat per game, and the ones
still made held longer (60% for a Phase 3b pour). Win rate went the other way.
A capture that is lost again in three turns is not wasted: it is a doomed
garrison spent taking a system the rival then has to spend a turn and a fleet
retaking, rather than a garrison that retreats and waits. The person keeps what
they take by feeding it afterwards, not by declining to take it — which is
what the next subsection builds.

### Shipped: feed what was just taken (`FEED`, `FEED_FRONT`, `FEED_MAX_TURNS = 2`)

The person's forwarding rules run on about 90% of turns and 64% of the ships
they launch go own-to-own; their frontier garrisons sit near parity with the
largest rival stack next door (median ratio 1.0, marshal 0.87), and fewer of
them are out-stacked (45% against 57-67%). Phase 4 seeded `_flow_to_front` on
every live frontier system, and a frontier system's leftover stayed home.

`_shortfalls` lists the live frontier systems whose garrison — after this turn's
sends and counting our fleets inbound — is under `FEED` times their largest
rival neighbour. While any are, Phase 4 seeds the rear's flow on those alone,
and with `FEED_FRONT` a frontier system's leftover budget first tops up a short
neighbour, never past its shortfall. Not a `FRONTIER_GUARD` change: nothing is
held back that was being spent before, only where the surplus goes. In the
mirror at 24 nodes / 6 ly-per-turn it cut rival captures from about 73 to 41
per seat per game without making them stick much better (a Phase 3b pour kept
56% against 52%) — what it buys is not visible in that table.

Duels against stock marshal, `tools/sweep.py`:

    arm (200 seeds, the four cells above)             pooled     verdict
    FEED 1.0, rear flow only                           50.7%     null
    FEED 1.0 + FEED_FRONT                              52.3%     null (z = 1.68)
    FEED 0.75 + FEED_FRONT                             50.8%     null
    FEED 1.0 + FEED_FRONT + Phase 3 hold test          52.3%     null (z = 1.72)
    FEED 1.0 + Phase 3 hold test                       50.3%     null
    FEED 1.0 + FEED_FRONT + step-forward hold 0.5      51.4%     null
    FEED 1.0 + FEED_FRONT + both hold tests            49.6%     null

    replicated on 400 fresh seeds, plus random 18 nodes at 12 ly/turn
    FEED 1.0 + FEED_FRONT                              52.9%     REPRODUCED (z = 3.46)
    FEED 1.0 + FEED_FRONT + Phase 3 hold test          51.3%     null

    FEED 1.0 + FEED_FRONT, by cell      median lane    rate
      random 18 / 6 ly                       3.5       51.1%
      random 24 / 6 ly                       3         50.6%
      random 30 / 6 ly                       3         52.5%
      random 24 / 12 ly                      2         53.8%   better
      random 18 / 12 ly                      2         56.2%   better
      random 18 / 9 ly                       2.5       56.9%   better   (300 fresh seeds)
      random 21 / 8.5 ly                     2         52.0%            (300 fresh seeds)
      random 30 / 12 ly                      2         53.5%            (300 fresh seeds)
      random 24 / 18 ly                      1         56.3%   better   (300 fresh seeds)
      symmetric 18 / 12 ly                   2         57.5%   better   (53% timeouts)

At or above 50% in every cell, and significant wherever the median lane is 2.5
turns or less. The hold test above adds nothing on top of it. Feeding does rescue
the hold test — both gates together go from 45.9% to 49.6% — but only back to
null, so it was deleted rather than shipped alongside.

Against knower at depth 0 (200 seeds): 87.0% of decided games against stock
marshal's 86.2%, with fewer losses (91 against 103), but at 24 nodes / 6
ly-per-turn it doubles the timeouts (72 against 36), so it takes fewer boards
outright — 72% of games against 80%. At 18 nodes / 12 ly-per-turn it is level or
better on every count. Free-for-alls (60 seeds, share of the two marshals' wins)
read 48-54% at 6 ly/turn and 48-56% at 12 ly/turn, pooled 53.0% (z = 1.47) at
12. On the 42 human setups it won 23 against stock's 20 (7 of the 22 stock lost,
across five distinct setups at 3-18 ly/turn) — too few maps, and knower's clock
moves stock between runs by two or three wins.

`FEED_MAX_TURNS = 2` gates it to boards whose median lane (re-timed each turn)
is at most two turns, where every measure agrees: 54.0% on the 300 fresh seeds
above (REPRODUCED), identical to ungated wherever the median is 2 or less, but
52.7% against 56.9% at the 2.5-turn boundary, and 22 against 23 on the human
setups. The gate buys back only the knower stalemates at 6 ly/turn — a
timeout is a board the bot column records as not taken — at the price of the
6 ly/turn gain against marshal itself, which the fresh seeds below show was not
there.

Two more gates, against the ungated and two-turn versions on 300 fresh seeds.
`FEED_MAX_TURNS = 3`, and `FEED_LOCAL`, which compares the lane a feed travels
along with the lanes a counter would come along: a front's top-up, or a rear
system's whole route to the short system, must arrive within `FEED_LOCAL` times
that system's fastest rival lane, or the rear system flows to the nearest front
as before.

    vs stock marshal           pooled   18/6   24/6   30/6   18/9   24/12
    ungated                     51.6%   48.2   50.9   51.5   52.9   54.3
    FEED_MAX_TURNS 2            51.1%   50.0   50.0   50.0   51.3   54.3
    FEED_MAX_TURNS 3            51.7%   48.7   50.9   51.5   52.9   54.3
    FEED_LOCAL 1.0              52.1%   50.2   52.9   52.0   52.6   52.5   REPRODUCED
    FEED_LOCAL 2.0              51.9%   49.8   51.9   52.3   51.9   53.2

    vs knower depth 0, share of all games won (stock marshal 78.5%)
    ungated 75.5%   FEED_MAX_TURNS 3 75.5%   FEED_LOCAL 1.0 76.6%   2.0 75.5%

On fresh seeds the 6 ly/turn cells are null for the ungated version (the 52.9%
above was carried by the fast cells). A three-turn cap gates nothing, since
those boards' median lane *is* three. `FEED_LOCAL 1.0` does what it says — best
at 6 ly/turn against marshal, fewer knower stalemates than ungated — but is
still below stock against knower there and two points short at 12 ly/turn, so
it trades one regime for the other. The two-turn cap is the only arm that costs
nothing anywhere: off where feeding is null against marshal and slightly
negative against knower, on where it clearly wins.

Shipped with the two-turn cap: `FEED = 1.0`, `FEED_FRONT = True`,
`FEED_MAX_TURNS = 2`. `FEED_LOCAL` was deleted rather than kept at zero. The
constants are in `models/marshal.py` after `RIVAL_REFLOOD_MIN_TURNS`.

### Not yet measured: no lone trickles into a rival

40% of marshal's failed attack landings are one or two ships landing alone
(the person's: 33% of a much smaller number). Of 340 failures in the 42
setups, 162 met a garrison that grew after launch, 92 a reinforcement landing
the same turn, 57 were launched at or under the garrison they could see (a
stagger's far wave whose nearer wave never followed) and 26 were pile-ups. The
candidate: a Phase 3b top-up on a rival target leaves only if it lands on the
same turn as a wave already inbound, so the siege's own production stops
arriving one hull at a time after the main strike has resolved. Gating *every*
rival re-flood measured 45.1% at `DEFENDER_ADVANTAGE 1.5` (see "Phase 3b
re-flooding" above), because the trickle was supplying the jitter cushion
`_enemy_margin` leaves out; if it reads the same way there, gate it off at high
advantage rather than drop it, since that setting is rarely played.
