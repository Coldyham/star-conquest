# marshal design notes: what a fight is priced against

The second of marshal's three files (overview and current standing:
[`marshal.md`](marshal.md); where the surplus goes:
[`marshal-flow.md`](marshal-flow.md)). It covers `_required`,
`_enemy_margin` and Phase 1/2: third-party fleets racing for a target,
garrisons that evacuate rather than fight (and why the attack margin has no
jitter premium), doomed neighbours and retreat distance, pile-ups under the
garrison-fights-last rule, and four tactics that each measured null, alone and
combined. Roster-wide rules are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

## Racing a third player for the same system

`_required` used to price a target against its *current* owner alone: the
garrison, that owner's own inbound fleets (`_inbound`), and what it will build
before we land. A **third** player's fleet already on the lane was invisible to
it. The failure that exposes is not subtle — three seats, A holds the centre
with 8, B launches 12 at it, A evacuates. The centre now reads as 0 ships with
nobody reinforcing, so marshal in seat C prices it at 1, and Phase 3b — which
pours the whole surplus into any target the gate has opened — posts all 6 of C's
ships into a node B is holding with 12 by the time they arrive. C loses the
strike *and* the system it launched from, which B walks into next turn.

The fix is `_rival_waves` (every bloc landing on the target that belongs to
neither us nor its owner, one per owner per turn, in arrival order) folded through
`_after_clash`, so the price is set against the *survivor* of the fight the target
is about to have rather than against the garrison standing there now. The clash
estimate comes from `combat.preview_fight`, taking whichever corner of the jitter
square leaves the most standing, so it cannot drift from the battle it predicts
and errs toward caution. It is fed the live jitter and advantage rather than
`TUNED_SWING`, because it predicts a real fight instead of flooring a margin.

Paired permutation harness — every arrangement of `[variant, base, filler…]` over
the seats on each seed, which for deterministic bots makes the V/B swap a
bijection and pins the null at exactly 50.0%, the multi-seat equivalent of what
`--ladder` gives duels for free:

    cell                                   W-L      n    rate      z   timeouts
    random 18n 3p (thinker)            247-198    445   55.5%  +2.32   121/720
    random 24n 3p (thinker)            262-234    496   52.8%  +1.26    89/720
    random 40n 3p (thinker)            294-269    563   52.2%  +1.05    56/720
    random 24n 3p (knower)             239-200    439   54.4%  +1.86    44/720
    random 30n 4p (thinker+claudebot)  389-320    709   54.9%  +2.59    80/960
    ---- pooled                       1431-1221  2652   54.0%  +4.08
    random 24n 3p @ 12 ly/turn         264-269    533   49.5%  -0.22    46/720
    every 2p duel                            —      —   50.0%      —          —

The knower cell is the one that matters most: a gain that only shows against a
copy of yourself is a tuning artefact, and this one holds against the strongest
bot in the roster. **It is inert in duels by construction**, not merely by
measurement — with two players the only ships aimed at a rival's system are that
rival's own, which `_inbound` already counted, so `_rival_waves` is empty and the
600-game duel cell reads an exact 234-234. The full-roster `--ladder` table under
"Where marshal stands" ([`marshal.md`](marshal.md)) is therefore untouched by this change, and stays current.
**It is also inert at high ship speed** for the same structural reason the
`FRONTIER_GUARD` gain is: at 12 ly/turn almost every lane is one turn long, so
there is no window in which an enemy fleet is on the board and visible before it
lands. Read the 54% as a default-configuration result.

**The same fix applied to neutral targets measures worse, and that is the
interesting half.** The obvious companion — a neutral with a rival's fleet
inbound is about to stop being neutral, so price it at the enemy margin against
what that fleet leaves standing — costs about two points in duels (440-492,
n=932, 47.2%, z = -1.70 at 24 nodes) while adding nothing in 3- and 4-player
games (54.2% against this version's 54.0%, indistinguishable at n≈2700 each).
The reason is Phase 3b: **the price is a gate, not the size of the strike.**
Under-pricing a contested neutral opens the gate and the entire surplus goes in,
which usually wins the race outright; pricing it honestly closes the gate and
cedes the node to the rival for nothing. Shutting the gate only pays where the
surplus would have lost anyway — which is the rival-held case above, where the
node is defended by a stack that already beat its garrison. So `_required` keeps
the static neutral branch untouched, deliberately, and
`test_a_contested_neutral_is_deliberately_left_static` pins it that way.

**Arriving *after* the rival breaks the door, measured and not shipped.** The
sharper form of the same idea: rivals send "just enough", so a contested neutral
is *cheaper* after their strike lands than before it. Three 12-ship homes around
a neutral 9 — if two of them launch on the same turn neither takes it, but
whoever lands alone holds it with about 8. So rather than joining the race, wait
a turn and fight the remnant. Reported as most valuable on small "puzzle" maps,
which is a regime none of the tables above measure: `WORLD_SIZE` is fixed, so a
6-node map has median 6-turn lanes at the default speed and plays as a
slow-motion crawl. What makes a small map a *puzzle* is short lanes, i.e. a high
`SHIP_LY_PER_TURN` — 6 to 12 nodes at 18 ly/turn gives median 2-turn lanes, and
a fleet visible on the board for a turn before it lands.

Three implementations, each paired against the shipped bot:

    variant                                W-L      n    rate      z
    contested neutral -> post-clash    2032-2031   4063   50.0%  +0.02
      (pooled 6/9/12n at 12-24 ly/turn)
    opportunistic half only                  —      —    bit-identical
    ...with the nominal remnant         1774-1733   3507   50.6%  +0.69
      (pooled 9/12/18n at 18 ly/turn, 24n 3p and 30n 4p at 6)

**Why the second one is bit-identical is the finding worth keeping.**
`_after_clash` takes the corner of the jitter square that leaves the *most*
standing, which is right for a requirement — but `_enemy_margin` is *already* a
jitter-safe edge over whatever it returns, so using the pessimistic corner as
well prices the same dice twice. A bot sending `1.25x` at a garrison leaves
`0.75x` nominally and `1.04x` on its luckiest roll, so under the pessimistic
corner a broken node only ever looks *dearer* afterwards, never cheaper: the
opportunity was erased before the search could see it, firing 6 times in 9507
neutral price lookups (0.06%). Switching that one branch to the nominal remnant
makes the mechanism live — 240 of 9422 lookups are a "they land first"
opportunity, 98 of them genuinely cheaper, about half a chance per game — and it
still measures null over 3507 decided games, with a per-arm run on identical maps
reading 294 wins against 296. Real, correctly priced, and worth about nothing:
the survivor's defender advantage and its production regrowth roughly cancel the
ships saved by not racing. Held to the same bar that rejected `RIVAL_WEDGE = 2.0`
at a replicated 51.2%, it is not worth having.

**Raising `DEFENDER_ADVANTAGE` makes waiting *worse*, not better, and the knob's
own range straddles the break-even.** The advantage lands on the survivor's side
twice: it shrinks the remnant, because the rival's attack has to beat an
advantaged garrison — but then it squares up again when that remnant defends the
node against us. And the rival compensates for the knob by sending more, since
`combat.edge_attacking` reads it live. Priced through the real combat code, for a
neutral 12 that a rival hits with exactly `edge_attacking`:

    advantage   rival sends   remnant   cost before   cost after   waiting costs
        0.75            11          6            11            6            55%
        1.00            15          9            15           11            73%
        1.10            17         11            17           15            88%
        1.25            19         12            19           19           100%
        1.50            22         13            22           24           109%

Break-even is at **1.25**, well inside the slider's 0.75-1.5. So the tactic runs
*opposite* to the knob — worth a third off below 1.0, worth nothing at 1.25, a
penalty at the ceiling. Measured in play, the same shape: paired against the
shipped bot at 24n 3p it reads 51.7% (z = +0.73) at 1.0, 51.0% (+0.42) at 1.25
and 50.8% (+0.29) at 1.5, shrinking monotonically, plus 50.1% on the 12n
18-ly/turn puzzle cell at 1.5. Null throughout, and the residue points the way
the arithmetic says it should.

**Nor does a high advantage reward *simultaneous* arrival — it never did, and the
reason is the fold, not the multiplier.** `combat.resolve_arrival` folds attackers
pairwise against each other first — strongest-first among themselves, with the
advantage applied to neither, since neither holds the ground — and whatever
survives then meets the still-advantaged garrison last. (Measured back when the
garrison was merely folded in by size rather than fixed last by rule; it was the
weakest side in every cell below, so the numbers read the same either way — see
`CLAUDE.md`'s note on `combat.resolve_arrival`, under Animated end of turn, for
when that stopped being a coincidence.) Two "just enough" forces of 15 converging on a neutral 12, 4000 dice
a cell, asking how often the second one ends up holding it:

    advantage   both land together   one waits a turn
        0.75                  1.5%             100.0%
        1.00                  0.0%             100.0%
        1.25                  0.0%             100.0%
        1.50                  0.0%              50.8%

Simultaneity is not a trade-off at any setting, it is a mutual kill — which is
exactly the standoff this whole idea starts from. (At 1.5 the *first* attack also
fails, since 15 no longer beats an advantaged 12, so the node stays neutral and
the waiter faces a coin flip instead of a remnant.)

**Which finally explains why none of it moves marshal.** Repeat that table with
the second player committing 30 instead of 15, and arriving together wins 100% of
the time at every advantage setting — it just ends with fewer ships (22.8 against
28.3 at 1.0, 18.3 against 26.0 at 1.5). The tactic is worth a fortune to a bot
that sends *just enough* and almost nothing to one that commits its surplus, and
Phase 3b makes marshal the latter: it is the big bloc that wins the pile-up
anyway. Same "the price is a gate" conclusion as above, reached from the other
end.

> **Tested since, and this explanation does not survive it.** The arithmetic
> above is arithmetic and stands; the *inference* — that the tactic is null on
> marshal because Phase 3b masks it — predicts it should pay once the surplus is
> no longer committed. Setting `COMMIT_SURPLUS = False` makes marshal exactly the
> "sends just enough" bot the claim describes, and the tactic still reads
> **50.1% (z = +0.04) over 815 decided games** in that regime. See "The
> combination" below: it is null on both sides of the knob it was supposed to be
> hiding behind.

**The shipped third-party fold does survive the knob**, which is the check worth
having after all that: paired against the pre-fix bot at 24n 3p it reads 53.3%
(z = +1.41) at advantage 1.25 and 55.4% (z = +1.92) at 1.5, alongside its 54.0%
at the default. Unlike the waiting tactic, that one is not fighting the
multiplier — it declines strikes that are doomed at *any* advantage.

**A harness trap that cost two bogus readings here.** `ai.decide` falls back to
the built-in heuristic for an unknown strategy name (deliberately — a stale save
must never crash), so a scratch model file that has been cleaned up turns an A/B
silently into "A versus heuristic" and reads **91.8% and 96.3%, at z = +17**. An
effect that large in this game is a bug, never a discovery. Any measurement
harness must assert every roster name is actually in `ai.STRATEGIES` before it
plays a single game.

**And a warning about where that idea appears to pay.** Small *symmetric* maps at
the default speed read 70.2% and 77.3% for the first variant — and both are
artefacts. Those cells time out in 85-94% of games (1073 of 1200; raising the cap
to 4000 turns leaves 314 of 360), so the rate is computed over the ~6% that
finish, which is not a random 6%. Running each arm separately over the *same*
maps, so the timeout rate becomes a per-arm number instead of a shared one,
settles it: 15 wins and 84.8% timeouts for the variant against 17 wins and 84.2%
for the base. It does not break the deadlock and it does not win more; the
paired figure was reading which of two bots in the same stuck game happened to
come out of it. When a cell times out more than about half the time, run the arms
separately before believing anything it says.

Two variants measured and dropped along the way. Distinguishing a bloc that lands
*before* us (fold it) from one landing *with* us (add it to what we must beat, as
`combat.resolve_arrival` totals them) is a wash — 50.3%, z = +0.14, n = 481
head-to-head against folding both alike — and folding everything is both simpler
and closer to what the engine does, since a pooled sum overstates two sides that
will in fact grind each other down first. Dropping the same-turn blocs entirely
is also a wash (54.6% vs 53.8% on the same cell). Neither distinction is worth
carrying, so there isn't one.

The term is rare rather than hot: instrumented over 120 games it changed 1.5% of
price lookups, and about one strike per game went from affordable to unaffordable.
That is the shape of the whole result — a small number of decisions, each of them
a whole army.

## Garrisons run away, so the jitter premium buys almost nothing

The single largest gain ever measured on this bot, and it comes from *deleting*
three tuned constants. Instrumenting `combat.resolve_arrival` over ~12k hostile
arrivals, split by whether the incoming force actually out-matched what the
defender could muster:

    defender      out-matched   evacuated before impact   probed   evacuated
    knower               2477                     97.9%     1293        8.0%
    thinker              2365                     95.0%      987        2.6%
    marshal              3885                     94.9%     2195        7.6%
    rusherplus           1050                     70.2%      313       17.3%
    claudebot             717                      0.0%      236        1.3%
    ---- pooled                                   86.7%                 7.0%

**86.7% of the time, out-shipping a garrison means the garrison is not there when
you arrive.** Every bot with a doomed/evacuate phase runs — and claudebot, the one
that has none, stands 100% of the time. So a margin over the break-even edge is
insurance against losing a fight that, in seven cases out of eight, never
happens; and it is not cheap insurance, since it is roughly a quarter of every
fleet, every strike.

`_enemy_margin` is therefore now the **advantage multiplier alone** — no
`NEAR_PAD`, no `ENEMY_NEAR`, no `ENEMY_FAR`, no distance ramp — with `_required`
flooring the count at `defence + 1` because an exact tie breaks to the defender.
Paired against the previous bot, every cell positive:

    cell                                   W-L      n    rate      z
    random 18n 3p (thinker)            263-193    456   57.7%  +3.28
    random 24n 3p (thinker)            241-192    433   55.7%  +2.35
    random 40n 3p (thinker)            265-226    491   54.0%  +1.76
    random 24n 3p (knower)             207-163    370   55.9%  +2.29
    random 24n 3p (claudebot)          290-223    513   56.5%  +2.96
    random 30n 4p (thinker+claudebot)  347-300    647   53.6%  +1.85
    ---- pooled                       1613-1297   2910   55.4%  +5.86
    random 24n 3p, advantage 1.25      227-178    405   56.0%  +2.43
    random 24n 3p, advantage 1.5       163-118    281   58.0%  +2.68
    random 24n 3p at 3 ly/turn         248-145    393   63.1%  +5.20
    random 24n 3p at 18 ly/turn        238-225    463   51.4%  +0.60

Three of those cells are the ones that could have killed it and did not.
**claudebot**, the only bot that never evacuates, is where dropping the insurance
should hurt most — it reads 56.5%, because claudebot is being out-shipped 17 to 10
on average and loses the fight it stands for anyway. **knower** is the
tuning-to-a-copy check. And **3 ly/turn** is the regime where `ENEMY_FAR` was the
only live term at all, so removing the ramp changes the most there — it is the
best cell in the table at 63.1%, because a ramp climbing to 1.5 on a long lane
was making marshal decline strikes against garrisons that would have run.

**Dropping the advantage half as well is the worst result ever measured here.**
Going the whole way to `defence + 1`, with no multiplier of any kind, reads 55.9%
at advantage 1.0 (indistinguishable from the above) and **8.6%, z = -12.35** at
advantage 1.5. The two halves of the edge are not the same kind of thing: the
jitter half is a premium against the dice, and the dice are usually never rolled,
but the advantage half is a premium against *the ground*, and it lands in full
whenever a garrison does stand. A high advantage is precisely the setting at
which a defender can hold and therefore does — the evacuate rate falls from 72%
to 55% between advantage 1.0 and 1.5, and the number of arrivals that out-match
anything nearly halves. Keeping the multiplier reads 58.0% there.

The margin is recovered from the public edges rather than read off `config`, so it
still cannot drift from the combat code: `edge_attacking(1) = adv * swing` and
`edge_defending(1) = swing / adv`, so their ratio is `adv**2`. `_defend_margin`
is untouched and still prices the jitter in full, which is the right asymmetry —
our own garrison cannot decline the engagement.

This is what took marshal to the top of the ladder and level with knower
head-to-head; see the table under "Where marshal stands" in [`marshal.md`](marshal.md).

## Two doomed neighbours, and what a retreat is worth

Reported from a real game rather than found in a sweep: a human attacked two
adjacent marshal systems on the same turn, and both garrisons "retreated" — into
each other, down the one lane between them, passing in mid-flight. Both systems
sat empty in front of fleets that were already on their way, and both fell. Held
where they stood the pair would at least have made the attacker pay for them;
pooled into one of the two they would have held it outright, since the relief
lane was three turns and the attack was four.

Two faults in the doomed branch, and a third found while measuring them.

**Nothing in the doomed branch knew what else was being abandoned.** Branch (b)
ranks refuges by garrison size, and for each of two doomed neighbours the biggest
friendly garrison in reach is the other one — so the swap is not bad luck, it is
what the rule says to do. Instrumented over 25 games (24 nodes, 3 players,
thinker opponents): 2603 doomed-branch decisions, **62 retreats into a system
that was itself being abandoned that turn (2.4%), 20 of them mutual swaps** —
about one swap and 2.5 wasted retreats a game. Phase 2 now computes the set that
is really being given up (the doomed, minus whatever consolidation has just
saved) and passes it to `_evacuate`, which will not retreat into it.

**A garrison that cannot save its own system is often exactly what the one next
door is short of.** Phase 1 sizes relief out of the rear only — its helper pool
excludes every threatened system, since a garrison holding off its own siege is
not spare — and gives a system up the moment that pool falls short. But a system
it has *already given up* is not holding anything off: those ships are lost where
they stand, so they are free. `_consolidate` runs before the evacuations, richest
first, and holds whichever doomed system the pooled garrison covers by the
deadline Phase 1 measured; a system that donates can never also receive, so the
swap cannot come back as a pooling decision. Phase 1's leftover rear budget is
topped up on the end, since holding a system beats leaving those ships for Phase
3 to spend on a strike.

It fires rarely, and the reason is worth recording: over 40 games, marshal hits
**10.2 situations a game where a doomed system has a doomed neighbour, but 8.6 of
them the neighbour cannot reach in time** — the deadline is the *earliest*
horizon showing a deficit, and by the time a blind bot can see the fleets, it is
usually one turn. Of the 1.6 that are in time, **0.4-0.7 a game are covered** and
become a system held instead of two lost. The reported game was the long-lane
case, which is where the mechanism lives.

**And a retreat had no notion of distance at all**, which turned out to be worth
more than either fix. Ranking refuges by garrison size sends a doomed garrison to
the biggest stack we own however far away it is: over 25 games, of 471 retreats
with a real choice of refuge, **the old rule took the long way 43.1% of the
time**, 3.11 turns against 2.57 for the nearest — half a turn per retreat, on
whole garrisons. Unlike a strike, a retreat is racing nothing: the ships are
simply out of the game until they land. Branch (b) now leads on travel turns and
keeps garrison size and front pull as the tie-breaks.

Measured paired — both variants in the *same* game, rotated through every seat,
so map and luck are shared — against a copy of the bot with the flags off:

    cell                                    W-L        n     rate      z
    null (base vs base) 24n 3p          458-457      915    50.1%  +0.03
    avoid-abandoned only                456-457      913    49.9%  -0.03
    consolidate only                    463-447      910    50.9%  +0.53
    both                                461-452      913    50.5%  +0.30
    ---- the same two cells, ten times the sample ----
    null (base vs base)                4652-4578    9230    50.4%  +0.77
    both                               4796-4487    9283    51.7%  +3.21
    nearest-first retreat (on top)     3623-3344    6967    52.0%  +3.34

The first four rows are the lesson. At n≈900 the two defensive fixes are
invisible — 50.5% is the null cell to within noise — and on that evidence they
would have been written up as another entry in the null-results list in [`marshal.md`](marshal.md). Read
against its own null at ten times the sample they are worth about **1.3 points
(51.7% against a 50.4% null, so ~z = 1.8 on the difference)**: real by direction
and consistent with the four whole-package cells below, but on its own that pair
of fixes is suggestive rather than settled. The retreat-distance rule is the
solid one, and it is also the one with volume behind it.

Two procedural notes, both of which nearly went the wrong way here. Budget n in
the thousands on this branch of the bot before reading anything into a result;
and keep a null cell *at the same sample size*, because the null is not 50% —
it reads 50.1% at n≈900 and 50.4% at n≈9200, and the methodological notes in [`marshal.md`](marshal.md) record it
drifting between 43% and 52% on smaller sweeps. Quoting the 51.7% against a
nominal 50% would have overstated the fix by a third.

All three shipped together, against the bot before them, swept over the node
count, the field size and the speed knob — all three rules are keyed off travel
time, and the speed knob decides how much of that a map has:

    cell                                    W-L        n     rate      z
    random 24n 3p (thinker)            3134-2690    5824    53.8%  +5.82
    random 30n 4p (thinker x2)         1122-972     2094    53.6%  +3.28
    random 40n 5p (thinker x3)          724-675     1399    51.8%  +1.31
    random 18n 2p duel                 1497-1352    2849    52.5%  +2.72
    random 24n 3p, 2 ly/turn            842-735     1577    53.4%  +2.69
    random 24n 3p, 18 ly/turn          1009-925     1934    52.2%  +1.91

Positive in all six, and the speed knob says which of the three is carrying it.
Fire rates per game, 40 games a row at 24 nodes and 3 players:

    ly/turn   doomed decisions   pooled a system   refuge ruled out   long way avoided
    2                    233.6              0.33              11.38              24.32
    6 (default)           74.2              0.38               1.98               6.33
    18                     2.7              0.03               0.05               0.53

The distance rule is the one with volume in every regime, and it is the only one
still firing at 18 ly/turn, where a lane is almost always a single turn and a
siege is over before a neighbour can be told about it — so the 52.2% there is
essentially that rule alone. The two defensive fixes are long-lane mechanisms:
at 2 ly/turn the avoid-abandoned rule strikes a refuge off the candidate list
eleven times a game (against a measured 2.4% of doomed decisions actually
retreating into one at the default speed), which is the end of the knob the
reported game sat at. Pooling never gets past half a system a game
anywhere, which is what its 51.7%-against-a-50.4%-null deserves.

Not a stalemate trade either, which the guard sweep in [`marshal.md`](marshal.md) is a reminder to
check: 150 mirror matches a side at 24 nodes and 3 players, every seat the same
bot, come back at 8 timeouts and 147.6 turns for the new one against 7 and 155.3
for the old. Shorter games, the same timeout rate.

The flags (`CONSOLIDATE`, `AVOID_ABANDONED`, `RETREAT_NEAREST`) are kept because
turning all three off has to be *exactly* the old bot, and that is checkable
rather than assertable: 1174 consecutive decisions across ten games, order for
order identical to the file before the change.

`models/thinker.py` and `models/knower.py` have the same `_evacuate` and the same
two defensive faults; neither is ported and neither has been measured. knower's
copy is threaded through `Posture`, so the port is not a copy-paste.

## Rushing the enemy: right about the game, inert on this bot

The reasoning is sound and both of its premises check out. Taking a neutral is a
net +1; taking a rival's system is a net +2, since it also costs them one. And
early on a rival's systems really are the cheaper target — instrumented over
~110k target evaluations, mean garrison on what marshal could reach:

    turns     neutral targets  mean ships   enemy targets  mean ships   enemy cost
    1-20                 9333         7.3            983         4.8        0.66x
    21-50                8621         7.7          15616         6.5        0.84x
    51-120               1559         6.9          49680        11.6        1.67x
    121+                  104         0.5          26981        19.9       36.38x

Cheaper for about the first fifty turns and dearer after, which is when the
neutrals left are the stripped leftovers nobody wanted. Add the retreat rate
above (86.7%) and an early strike on a rival often costs nothing at all.

`_richness` has no term for who owns a target, so this looks like a clear
omission. It measures null at every weighting and every seat count:

    variant                            cell         rate      z
    base x1.5 when enemy-held      24n 3p          49.7%  -0.14
    base x2.0 when enemy-held      24n 3p          49.8%  -0.09
    base +1.0 when enemy-held      24n 3p          50.0%  +0.00
    base +3.0 when enemy-held      24n 3p          50.1%  +0.05
    enemy ranked ahead of every neutral, flat      50.3%  +0.14
    base x2.0 when enemy-held      30n 4p          50.5%  +0.28
    enemy ranked first             30n 4p          50.2%  +0.12
    base x2.0 when enemy-held      40n 5p          51.2%  +0.68

**Because marshal is already a rusher — it just never looked like one.** It takes
whatever is in front of it, and its capture mix tracks availability to within a
point at every phase of the game:

    turns     enemy share of what was adjacent   enemy share of what it took
    1-20                                  6.9%                          5.7%
    21-50                                61.9%                         61.3%
    51-120                               96.6%                         96.5%
    121+                                 99.7%                          99.3%

There is no economy-first bias to correct. In the opening it takes neutrals
because 93% of what it can reach *is* neutral, not out of preference. The advice
is aimed at a strategy this bot never had.

**The strong form is actively worse.** Declining neutral expansion entirely while
any enemy target is reachable — the literal "rush the enemy" — reads **45.1%
(z = -2.06)**. Preferring the +2 over the +1 is only worth something when you must
choose, and Phase 3 usually does not have to: it strikes at *every* affordable
target. Skipping the neutral just forfeits the +1 and buys nothing.

**And there is a ceiling on this whole channel, which is the part worth keeping.**
Target priority binds rarely: of the targets affordable on their own budget, only
**10.4%** are lost to another target having spent it first. Inverting the sort to
the worst possible order — poorest and biggest first — costs just 49.2% (z = -0.33)
at 24 nodes and 46.2% (-1.63) at 18. So the *target sort* is worth at most about
two points in a duel-like field, and **no preference expressed through it can be
worth more than that.** Note this bounds the sort, not `_richness` as a whole:
`_wedge` reads 62-64% at four and five players through the same function, because
it also steers `_front_pull` and the `_flow_to_front` seeding, and because what it
discriminates is a position rather than an owner. Before weighting a new term into
`_richness`, check which of those two channels it is actually meant to act on.

## A 0-ship neutral: real waste, correctly diagnosed, still not worth fixing

Late-game neutrals are almost all mutual-annihilation husks rather than
untouched garrisons — instrumented alongside the phase table above, by whether
the target's garrison is exactly 0:

    turns     zero-ship neutrals   nonzero neutrals
    1-20                    0.0%           9053
    21-50                   0.4%           7236
    51-120                  6.5%           1503
    121+                   94.7%              4

A 0-ship neutral is exactly the case the third-party fix's exclusion (above)
declines to reprice, and here the exclusion's own reasoning does not hold: the
rejected general reprice cost points because it shut a *gate* on a real garrison
Phase 3b could otherwise overrun outright, and a 0-ship node has no garrison to
give that discount against — pricing it against a converging rival is not
ceding a winnable fight, there is no fight to cede. Confirmed as real waste, not
a hypothetical: tracing `combat.resolve_arrival` for exactly this pattern (a
neutral at 0, marshal *and* a rival both landing on it) over ~600 games —

    races for an empty neutral      49
    marshal lost the race           25 (51%)
    ships thrown away               43

— marshal walks its static 1-ship price into a race it loses half the time,
for nothing. Repricing only the `ships == 0` branch through the same
`_rival_waves`/`_after_clash`/`_enemy_margin` machinery as the rival-held case
is a two-line change and correctly raises the price whenever the rival's fleet
was visible at decide time.

**It does not stop the waste, and the reason is what actually bounds this
idea.** Re-running the same trace with the fix in place: still 51 races, still
25 lost, 40 ships thrown away — no improvement. Splitting the 51 races by
whether the rival's fleet could have been seen at all: only **27 of 51** had a
non-empty `_rival_waves` at decide time; the other **24** are races where
marshal's own fleet was *already in flight*, launched on an earlier turn when
the node still had a real garrison, before it was fought over and reduced to 0
by someone else. A launched order can't be recalled, so no repricing done
*this* turn touches those. And the largest single case within the reachable 27
is two players independently deciding to strike the same freshly-emptied node
*on the same turn* — invisible to both by construction, since `decide()` runs
for every seat against one shared, unmutated start-of-turn state (`engine.py`'s
simultaneous-resolution rule), so neither order exists in `state.fleets` when
the other seat is priced. The fix can only ever reach the strict remainder:
already-in-flight fleets from a rival who committed on a *prior* turn — a
narrower case than "a race for an empty neutral" makes it sound.

Paired against the current bot, null in exactly the range this rarity predicts:

    cell                              W-L        n     rate      z
    random 24n 3p (thinker)        692-688     1380    50.1%  +0.11
    random 18n 3p (knower)         610-603     1213    50.3%  +0.20
    random 30n 4p                  910-912     1822    49.9%  -0.05

Also not inert in a duel the way the rival-held reprice is — a neutral's
"owner" is seat 0, so `_rival_waves` does not exclude a sole opponent's own
fleet the way it excludes a rival-held target's owner, and the duel ladder
reads 50/50 only by measurement (274-272 over 600 games), not by the same
structural guarantee. Not shipped: a real, correctly-diagnosed two-ship-per-
hundred-games leak, bounded on one side by decision simultaneity and on the
other by orders already committed, too narrow to clear the noise floor at any
cell tried.

## Holding a doomed system to sacrifice-and-retake: a real mechanism, a null result

The corollary to "garrisons run away" (above): if the *attacker's* best play against
an out-matched garrison is usually to flee rather than pay the jitter premium, is
the *defender's* best play, when it can't scrape together enough in time to hold
for sure, ever to stand anyway — soak the attack with a garrison that's going to
die either way, and have a trailing force already in flight retake the weakened
remnant a turn or two later? The case for it: our own garrison, unlike an
attacker's target, gets `DEFENDER_ADVANTAGE` on the way down, so it doesn't die
for nothing — it thins whatever survives. And the retake is cheap relative to the
alternative, because the enemy has only just taken the system: no time to dig in,
one turn of its production instead of however long a full remass takes, and *we*
get the defender-advantage-free version of the fight (well, the enemy gets the
advantage on the retake since they now hold it — but only against the thinned
remnant, not the original attack).

Instrumented directly against `models/marshal.py`'s own Phase 1/Phase 2 boundary
(`_probe_doomed`, on a throwaway copy): whenever a threatened system can't be
saved in time and falls to `_evacuate` (the "doomed" branch), check whether a
trailing force — reachable within a few turns past the defence deadline, the same
`helpers` pool Phase 1 already builds — could clear `_enemy_margin()` against the
predicted post-clash survivor count (`_after_clash`, the same pessimistic corner
`_required` prices a rival-held target with). Over 240 games (4 configs, 60 seeds
each): **15.2%** of doomed-branch decisions qualified on the pessimistic estimate,
16.8% on the nominal one — not rare. (The count overstates *distinct incidents*
the same way item h's did: a besieged system re-enters the doomed branch every
turn it's re-threatened, so this is a rate over decision points, not over unique
sieges.)

Built the actual policy (`_hold_and_retake`, scratch `marshal_hold.py`): when the
doomed branch is reached, price the retake the same way, and if a helper pool
clears it, hold the garrison in place (no evacuation order) and commit exactly
`_enemy_margin()`'s worth of the helper budget toward the doomed system now,
instead of leaving it for Phase 3 to spend elsewhere. Falls back to the ordinary
`_evacuate` whenever the retake doesn't price out.

Paired against an identical `marshal_base`, null everywhere it was tried:

    cell                                  W-L        n     rate      z
    random 24n 3p (thinker), adv 1.0   131-139      270    48.5%  -0.49
    random 18n 3p (knower),  adv 1.0   117-129      246    47.6%  -0.77
    random 30n 4p, thinker+knower      305-319      624    48.9%  -0.56
    random 10n 3p (thinker), adv 1.0   128-125      253    50.6%  +0.19
    random 10n 2p duel,      adv 1.0    84-73       157    53.5%  +0.88

DEFENDER_ADVANTAGE swept too, since the mechanism is priced *by* that knob and the
remnant tactic (above) is knob-sensitive in the other direction — a first pass at
1.5 read 57.2% (z=+2.05, but 31% timeouts, so not to be trusted on its own); a
follow-up at double the sample size (120 seeds, 720 games, 448 decided) came back
dead even, **224-224, z=+0.00**. 1.25 and 2.0 were also tried and were weaker
still (the latter at 71% timeouts, unreliable on its face).

**Why a real, non-rare mechanism still nets zero:** the two policies are closer in
total ship cost than the framing suggests. Evacuating preserves the garrison
alive in friendly territory; holding spends it as casualties but saves the
identical-sized commitment `_hold_and_retake` would otherwise have sent to
`_evacuate`'s destination or left for Phase 3. `_enemy_margin()` already prices
the retake tightly (it's the same margin Phase 3 uses to strike anywhere), so
"cheap because they haven't dug in" isn't a discount over what an ordinary attack
already assumes — undefended targets are already marshal's default assumption,
per "Garrisons run away" above. What holding actually buys is the casualties the
dying garrison inflicts on the attacker; what evacuating buys is a garrison that
survives to be spent on a *different*, self-chosen fight instead of a forced one.
Over enough games those roughly cancel. Not shipped.

## Re-tuned for production-before-combat and the garrison-fights-last pile-up

Two engine rules moved after the section above was written: production now runs
*before* combat (so a hull finishing this turn is in the garrison for the fight
right after it), and a multi-owner pile-up now folds attackers strongest-first
*among themselves* before the survivor faces the garrison last, rather than
folding the garrison in wherever its size placed it in the queue. Phase 1's
threat math predates both and was measured stale against them in two distinct
ways, fixed together as one `models/marshal.py` change (no `RISK_PARITY` /
`_attacker_pileup` split — both moved balance for the whole doomed-system
decision and are cheaper to revalidate once).

**The pile-up half.** `_enemy_arrivals` priced every hostile fleet landing on a
turn as one combined sum regardless of owner — correct under the old rule, where
the garrison queued by size rather than going last, but wrong under the new one:
two rivals arriving together fight *each other* first. Garrison 10, rivals 11 and
10 landing the same turn, used to price as 21 incoming and evacuate; the actual
fight leaves the 11 a ~5-ship remnant after it beats the 10, which our garrison
comfortably holds. `_attacker_pileup` folds a turn's arrivals the same way
`combat.resolve_arrival` does — strongest-first, no `DEFENDER_ADVANTAGE` between
two attackers, the same pessimistic jitter corner `_after_clash` already uses —
before the existing across-turn cumulative logic ever sees the number. Inert in a
duel by construction: with one live rival, a turn's arrivals can only ever have
one owner, so `_attacker_pileup` on a single-element list is a no-op. That also
means the duel cells below measure the *other* half in isolation.

**The parity half.** Phase 1 only ever asked whether a system cleared the safe,
jitter-padded margin (`_defend_margin`); short of that, it evacuated
unconditionally, even when the garrison — with no help borrowed from anywhere —
already matched or beat every horizon's incoming total on the raw count alone.
Evacuating is a certain loss of the system; standing at bare parity is a fight
that is no worse than even (ties already break to the defender, before
`DEFENDER_ADVANTAGE` even applies), and it costs nothing borrowed from a
neighbour. `RISK_PARITY = 1.0` is that literal raw-count bar — deliberately not
folding `DEFENDER_ADVANTAGE` in further to loosen it below parity, per "only take
the risk if the count is already at least even," which is the more conservative
reading and the one measured below. It fires only as a *last resort*: recorded
in Phase 1 as `risk_ok` but not acted on there, so Phase 2's consolidation — the
deliberately shipped, measured "richer of two doomed neighbours" pooling above —
still gets first claim on the garrison. Inside `_evacuate` itself, branch (a)'s
opportunistic capture of something else still outranks it too; `risk_ok` only
turns a would-be retreat or cornered stand-off into a deliberate hold once
nothing better was already going to happen to that garrison.

**Measured**, paired against an unmodified copy (`marshal_base`) via
`tests.sim.run_ladder`/`run_swap`, seeds 1..n:

    duel (run_ladder), default combat unless noted   W-L        n     rate      z
    18 nodes                                       502-452     954    52.6%  +1.62
    12 nodes                                       495-464     959    51.6%  +1.00
    30 nodes                                       300-260     560    53.6%  +1.69
    18 nodes, DEFENDER_ADVANTAGE 1.5                222-222    444    50.0%  +0.00
    18 nodes, DEFENDER_ADVANTAGE 1.25               278-263    541    51.4%  +0.64
    ---- pooled                                   1797-1661   3458    52.0%  +2.31

A duel only ever exercises the parity half (see above), and it is small but real
once pooled — every cell sits at or above 50%, none below, and the null only
shows up at `DEFENDER_ADVANTAGE 1.5` (exact 222-222), where the safe margin is
already so cheap to clear that few systems ever reach the doomed-and-parity-ok
branch at all. A free-for-all (`run_swap`, wins per strategy, same seeds run
once per roster) is where the pile-up half actually gets exercised, since it
needs a genuine third owner converging on the same system:

    melee (run_swap)                                marshal   marshal_base   others           finished
    3p: marshal / marshal_base / knower, 24n           210          191      knower 179           580
    4p: + rusherplus, 30n                              228          124      knower 231, rusher 2  585
    4p: + thinker, 12n (long lanes)                    203          177      knower 94, thinker 69 543

New marshal clears old marshal in every melee cell, by a wide margin in the
4-player ones (228 vs 124; 203 vs 177) — the regime the pile-up fix exists for.
`marshal_base` (unmodified) is the weakest of the three real competitors in the
`+rusherplus` cell despite otherwise being the same bot that leads the ladder
(see "Where marshal stands" in [`marshal.md`](marshal.md)), which is the clearest sign the old pile-up pricing
was actively costing it once a third player is actually on the board.

Four ideas above each measure null on their own — the remnant tactic (waiting for
a rival to break a contested neutral), the 0-ship neutral reprice, hold-and-
retake, and an enemy-held bonus in `_richness`. Two of them come with a written
explanation for *why* they are null, and both explanations blame the same thing:
Phase 3b. The gate argument says honest pricing of a contested neutral only cedes
a node the committed surplus would have taken anyway; the remnant argument says
the tactic pays for a bot that sends "just enough" and marshal is not one. If
that is right, then (a) they should combine, since each supplies what the other
lacks — the reprice declines a race it would lose, and the remnant tactic gives
the declined node a follow-up — and (b) they should come alive with
`COMMIT_SURPLUS` off. Both halves are testable.

Built as one flag-composable copy of the bot rather than four, so the
combinations are the same code (`NEUTRAL_FOLD`, `ZERO_FOLD`, `HOLD_RETAKE`,
`RUSH_BONUS`, over the existing `COMMIT_SURPLUS`). With every flag at its shipped
default the copy is behaviourally identical to `models/marshal.py` — 30 whole
games, same winner and same turn count — so a measured difference is the flags
and nothing else. The neutral fold uses the *nominal* remnant and is applied only
when it makes a target **cheaper** (the pessimistic corner double-prices the
dice, per above), except for the 0-ship case, which is meant to raise. That
resolution matters: it keeps the gate open where Phase 3b wants it open while
still taking the discount when a rival lands first, so this is the sensible
composition rather than a naive one.

**They do not combine.** All three sequence ideas at once, against the identical
baseline:

    cell                                  W-L        n     rate      z
    random 24n 3p (thinker)            193-210      403    47.9%  -0.85
    random 30n 4p (thinker+knower)     276-303      579    47.7%  -1.12
    random 24n 2p duel                 283-289      572    49.5%  -0.25
    ---- pooled                        752-802     1554    48.4%  -1.27

Adding the enemy-held bonus on top (`RUSH_BONUS = 2.0`) reads 49.6% (z = -0.15,
n = 411). Each idea alone sat at about 50%; together they sit slightly *below* it.
Not significantly so, but the direction is mild mutual interference rather than
synergy — which is what you would expect of four separate ways to spend the same
budget on second-order timing when the first-order rule (strike at every
affordable target, pour the rest into a strike already going in) is already what
wins.

**And the masking theory is wrong.** Turning Phase 3b off costs marshal a great
deal on its own — **40.4% (z = -3.77)** against the shipped bot, a useful
reminder of how much that one rule is worth — but it does make marshal the
"sends just enough" bot the remnant argument describes. Measured *within* that
regime, so both arms carry the same handicap:

    variant (base: COMMIT_SURPLUS off)     W-L        n     rate      z
    + neutral fold only                 408-407      815    50.1%  +0.04
    + all three sequence ideas          411-403      814    50.5%  +0.28

Null on both sides of the knob they were supposed to be hiding behind. A first
pass at n≈370 read 51.5% and looked like the predicted effect appearing; doubling
the sample settled it at 50.5%, the same lesson as the 1.5-advantage reading in
the hold-and-retake section above. So Phase 3b is not what suppresses these
ideas — they are simply worth nothing, and the tidy mechanism story that made
them *feel* suppressed was itself untested. The gate argument survives as a
description of why honest pricing of a contested neutral is actively *worse*
(that one was measured, at 47.2% in duels); what does not survive is the
inference that removing the gate would let the converse pay.

Nothing shipped. The value of the exercise is the correction: four null results
with one shared explanation, and the explanation was checkable and false.

## The attack side of the pile-up: shipped for correctness, measured null

The re-tune above moved Phase 1 onto the garrison-fights-last rule; `_required`
never followed it. It still folds every third-party bloc from `_rival_waves` into
the garrison through `_after_clash`, including a bloc landing on **the same turn
as our strike**, and under the current rule that one never meets the garrison
first. Attackers fold among themselves at parity and the garrison fights
whatever survives, so a same-turn bloc is fought by *us*. The old fold prices it
as a discount (a 16 landing with us on a rival 20 drops the price from 21 to
about 18), where the fight actually costs us roughly `sqrt(need^2 + R^2)`. The
"distinguishing a bloc that lands *with* us is a wash" result in "Racing a third
player" was measured under the old by-size fold, so it did not settle this.

The fix folds earlier-turn blocs per turn through
`_attacker_pileup` and then against the garrison. Same-turn blocs were priced by
the smallest strike that still carries the rival-margin requirement out of an
engine-order attacker fold, taking our unlucky jitter corner in each clash.
At zero jitter it matches `combat.resolve_arrival` exactly
(`test_pileup_survivors_matches_the_engine_fold`). It is inert in a duel by
construction, so it was measured, behind a temporary flag, with the paired
multi-seat harness
(variant and stock marshal placed in every ordered seat pair per seed, fillers
in the remaining seats):

    cell                                        V-B        n     rate      z   timeouts
    symmetric 24n 3p (thinker)               493-501     994    49.6%  -0.25   739/1800
    symmetric 30n 4p (thinker+knower)        467-474     941    49.6%  -0.23   582/1800
    symmetric 30n 4p (thinker+claudebot)     331-328     659    50.2%  +0.12   490/1200
    symmetric 30n 4p, advantage 1.25         197-196     393    50.1%  +0.05   692/1200
    random 30n 4p @ 3 ly/turn (th+kn)        382-387     769    49.7%  -0.18   187/1200
    random 24n 3p @ 3 ly/turn (thinker)      405-400     805    50.3%  +0.18   275/1200
    random 36n 5p (th+kn+claudebot)          261-257     518    50.4%  +0.18    30/800
    ---- pooled                            2536-2543    5079    49.9%  -0.10

**Why it is null.** Instrumented over 20 games, the price differed in 14 of
18,103 rival-target lookups on symmetric 24n 3p, and 128 of 20,068 on random
30n 4p. Every change was upward: a same-turn bloc has to be visible *and* due to
land on exactly our horizon. The earlier-turn per-owner fold never changed a
price at all, because two third parties landing on one rival system on the same
earlier turn essentially never happens. A same-turn pile-up on a *neutral* is
rarer still (3 lookups in 20 symmetric games), so the neutral branch keeps its
measured static price. Symmetric maps are the regime this was aimed at, and they
only make it rarer: their long stalemates are the 40-60% timeouts above, not
races for the centre.

**Shipped anyway, and unflagged.** This is not a tuning knob but the bot's
estimate of a fight agreeing with the engine's rule. At a null it costs nothing,
and a bot whose prices are right is easier to reason about and to build on than
one carrying a known-wrong fold only because the error is rare. That makes it a
deliberate exception to the usual "measured null, deleted" rule above. It applies
to correctness fixes, never to new tactics: the remnant tactic and the neutral
fold were *choices* about what to do, and a null there still means delete. The
legacy fold was removed rather than kept behind a flag, and duels are untouched
by construction, so the `--ladder` table under "Where marshal stands" ([`marshal.md`](marshal.md)) stays
current.
