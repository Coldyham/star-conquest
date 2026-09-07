# Bot design notes

Rationale and measurements behind the AI: the `models/` roster, the margins every
bot prices a fight with, and the parameter space a tuning is only valid inside.
`CLAUDE.md` states *what* each rule is; this file is *why*. Its companion is
[`system-design.md`](system-design.md), which covers the game and shell itself.

**Every number here was measured, and the negatives are recorded as carefully as
the wins.** Don't re-add a deleted idea or re-tune a constant without a
measurement of your own, and read the note on paired null cells under "The
2026-09 tuning sweep" before running one.

## Lane length across the parameter space

`WORLD_SIZE` is a constant, so the map is always laid out in the same box and
the *node count* sets how far apart systems are: fewer nodes means physically
longer lanes, not a smaller board. `config.SHIP_LY_PER_TURN` (1-30 on the
slider, 6 by default) then divides all of it. The two together move lane length
over more than an order of magnitude — measured over three seeds a cell,
min/median/max lane in turns:

    ly/turn      12 nodes    18 nodes    24 nodes    40 nodes
       1        14/22/36    11/18/29     9/16/26     7/13/18
       3          5/8/12      4/6/10       3/6/9       3/5/6
       6           3/4/6       2/3/5       2/3/5       2/3/3
      12           2/2/3       1/2/3       1/2/3       1/2/2
      30           1/1/2       1/1/1       1/1/1       1/1/1

**The consequence for tuning: a constant keyed off travel distance is live in
part of that space and unreachable in the rest, and the default sits in exactly
one regime.** `marshal._enemy_margin` is the worked example — it is
`max(edge_attacking + NEAR_PAD, min(ENEMY_FAR, ENEMY_NEAR + 0.1 * (dist - 1)))`,
so the cap needs `dist >= 7` and the break-even floor needs `dist == 1`. Which
of the three terms actually decides a strike, as a share of every strike Phase 3
priced:

    ly/turn    ENEMY_FAR cap    ramp interior    edge floor
       1              100%               --            --
       2            61-100%           0-39%            --
       3             0-60%           40-100%           --
       6                --              100%            --
      12                --            75-96%          4-25%
      18                --            0-72%          28-100%
      30                --            0-27%          73-100%

At the default 6 ly/turn only the middle term is ever selected, so a sweep there
reads both ends as dead code — and a margin fitted there is fitted for one third
of the slider. At the bottom end `ENEMY_FAR` *is* the margin; at the top the
ramp is inert and the live figure is whatever `combat.edge_attacking` returns.

So sweep `--nodes` and the speed knob before concluding that a constant does
nothing, or that a margin is tuned. This has produced a wrong "unreachable
constant" reading before.

## Positions from real games (`tools/position_suite.py`)

**Every other measurement in this file is a bot against another bot**, and that
is a real limit rather than a stylistic one. A roster playing itself only ever
visits positions bots create; whatever a bot is systematically bad at, its
opponents are bad at reaching, so the sweep never asks the question. The
2026-09 sweep's own warning about tuning to a copy of yourself
(`ENEMY_NEAR` gaining 5 points in self-play and losing 7 against thinker) is the
same failure one level up.

Stored replays answer it. The game keeps a match as its inputs, so
`replay.reconstruct` rebuilds the exact board after N turns of somebody's real
game — a position a *person* built, with a person's mistakes in it. Hand the seat
to a bot from there and it plays out the rest. `sim.play_from` is the mechanic,
`sim.positions` picks the turns, and `tools/position_suite.py` runs a whole
corpus (the local `games/` dir by default, or `--supabase` for the shared one —
see system-design, "Checked scores", for how games get there).

**The comparison is paired, which is what makes it worth more than a win rate.**
We know exactly how long the player took from that same board, so each position
is a matched trial rather than an independent sample. One recorded game becomes
dozens of them.

Three numbers come out, and they must not be blurred together:

    faster      of positions where BOTH finished, how often the bot was quicker
    median gain turns saved against the person, over those same positions
    recovered   of positions from games the person LOST, how often a bot won

`recovered` is the one no leaderboard score can ever pose, because only wins are
postable — and it is the most interesting, since a lost or abandoned game is
precisely a position the player could not solve. It has no baseline, so it is a
raw rate rather than a comparison; never rank it against `faster`.

**Read the output as a direction, not a verdict.** The sample is whatever games
happen to exist, on whatever setups whoever played them chose — which, per "Lane
length across the parameter space" above, is very likely one regime of three. A
gap found here is a hypothesis; confirming it still wants a paired sweep with a
z-score, the same as everything else in this file.

## `models/knower.py` and simultaneous resolution

Because turns resolve simultaneously — `_collect_orders` hands every seat the
same unmutated state and applies nothing until all have decided — an
opponent's orders can't depend on yours. That means a bot can clone the
board, call each rival's own registered `decide`, and know their moves before
the engine asks for them: there's no fixed point to solve, one forward pass
of their real code *is* the answer. knower folds those predictions into a
"post-launch board" (predicted orders applied via `engine.apply_order` but
not advanced) and runs thinker's phases against it.

## Replaying a bot for the leaderboard (`bot_replay.REPLAY_AUX`, `BUDGET_SCALE`)

The board's bot column replays each `models/` bot through the human's seat on a
posted map (`tools/bot_replay.py`; the infrastructure is in
[`system-design.md`](system-design.md), "Bot replays"). Two questions about the
roster fall out of that, and both were measured.

**Which version of a bot goes on the board.** Its best one, not its menu default.
`REPLAY_AUX` names the exceptions and today holds one: `knower` at search depth
12. That is the top of knower's own slider (`SEARCH_DEPTH_MAX`) and the setting
its own measurements favour — "ahead in every measurement taken and behind in
none". It is not a small difference. On a 16-node map, same seed, same opponents:

    knower @ 1 (default)     336 turns, 346 ships lost
    knower @ 12             121 turns, 137 ships lost

There is no point putting a deliberately hobbled version of the best bot on a
board whose whole purpose is to give a human score something to be measured
against. The cost is wall clock: depth 12 is roughly 100x depth 1, taking a
40-node six-seat game from well under a second to tens of seconds. The worker's
`--limit` and deadline exist for that, and it banks each result as it goes.

**Why the offline runner gets a bigger time budget, not a shallower search.**
knower's search is *iteration*-bounded, so the same board plans the same way —
except for `SEARCH_BUDGET_S`, a 150 ms per-decide catastrophe guard whose own
comment notes that tripping it makes the plan depend on the wall clock. knower's
cost table measured better than 2x headroom at depth 12. On a CI-class container
the largest configuration the menu can build — 40 nodes, 6 seats — measured only
**~1.1x**, and that is not a margin to cache results against. Shrinking
`BUDGET_SCALE` proportionally simulates a slower machine, and a runner just 2x
slower produced a different answer on the same map:

    scale 1.00  (as shipped)        123 turns, 311 lost
    scale 0.50  (~2x slower)        117 turns, 272 lost   <- guard tripped
    scale 0.25  (~4x slower)        113 turns, 206 lost   <- guard tripped
    scale 100   (the worker)        123 turns, 311 lost

Both of knower's guards exist because the WASM build is single-threaded and the
alternative to giving up mid-search is freezing the browser tab — a constraint a
background job does not have. So the worker lifts them 100x via
`ai.set_budget_scale`, turning 150 ms into 15 s against a measured worst case of
~131 ms. Read that as the opposite of a loosening: those guards are the only part
of the bot that is *not* iteration-bounded, so a run that can never trip one is
strictly more reproducible. It remains a guard — a wedged bot is stopped long
before the workflow's own `timeout-minutes` has to — and `BUDGET_SCALE` stays 1.0
for every ordinary caller, so a real game is untouched.

A bot that wants the same treatment declares `BUDGET_SCALE = 1.0` and multiplies
its own budgets by it at call time; see `models/README.md`. Bots without it are
left alone.

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
`combat.edge_attacking`/`edge_defending` (see below) and the rest of the roster
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

### Where marshal stands

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
nodes, default settings — 900 games, 57 timed out and are excluded from the
percentages). **This table is the current one** — update it, not the module
docstring, the next time marshal or the roster's pricing changes:

    marshal 251 (30%), knower 249 (30%), thinker 167 (20%),
    claudebot 92 (11%), heuristic 46 (5%), rusherplus 38 (5%)

    head-to-head (row's win rate vs column)
                heuris  rusher  claude  thinke  knower  marsha
      heuristic      —     64%     19%      2%      0%      0%
      rusherplus   36%       —     27%      3%      0%      0%
      claudebot    81%     73%       —     12%      5%      2%
      thinker      98%     97%     88%       —     16%     11%
      knower      100%    100%     95%     84%       —     50%
      marshal     100%    100%     98%     89%     50%       —

marshal took the top of the ladder here, and level with knower head-to-head, on
the strength of dropping the jitter premium from its attack margin — see
"Garrisons run away" below. The previous reading of this table had it second at
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

### A stagger's nearer wave is reserved

Phase 3 picks an arrival horizon, launches the far sources, and *relies* on the
nearer ones firing next turn to converge on the same target. Their budget was
never reserved, so a later, poorer target could spend it and the pincer silently
failed to materialise — and once Phase 3b existed, 3b could eat its own second
wave. Reserving it is what makes a staggered strike a plan rather than a hope.

### Two more ideas measured and deleted

Both built against the configuration above and removed rather than kept on the
strength of the idea. With the chokepoint and pocket-sealing results above, that
is four.

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

### `FRONTIER_GUARD`: how it came to be 0.40, documented as 0.3

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

### The 2026-09 tuning sweep

A re-sweep of every marshal constant, paired against unmodified marshal. Results
first, then the two methodological points, which matter more than any single row.

**Adopted from it:** `ENEMY_NEAR` 1.3 -> 1.15, `ENEMY_FAR` 1.9 -> 1.5,
`FRONTIER_GUARD` 0.40 -> 0.55. Every other constant measured null or worse and
was left where it was.

> **Superseded.** `ENEMY_NEAR`, `ENEMY_FAR` and `NEAR_PAD` no longer exist:
> marshal's attack margin is now the defender-advantage multiplier alone, with no
> pad, absolute or distance ramp. See "Garrisons run away, so the jitter premium
> buys almost nothing" below. This section is kept as the record of how the ramp
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
for why the three terms of `_enemy_margin` are each live in one band only.

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
threat many turns away is ruinous. Not shipped. The exposure is real and no bot
in the roster punishes it; a probe written to exploit it deliberately (cheapest
non-owned neighbour first, never consolidate) lost 240 games out of 240, so
whether closing it is worth anything is **still unmeasured** and needs a probe
that concentrates force to break one point rather than attacking everywhere.

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

### Racing a third player for the same system

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
"Where marshal stands" is therefore untouched by this change, and stays current.
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
reason is the fold, not the multiplier.** `combat.resolve_arrival` sorts the
sides by actual ships and folds them pairwise with `defender_owner` fixed, so two
attackers landing on the same turn are folded **against each other first, with
the advantage applied to neither**, and whatever survives then meets the
still-advantaged garrison. Two "just enough" forces of 15 converging on a neutral
12, 4000 dice a cell, asking how often the second one ends up holding it:

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

### Garrisons run away, so the jitter premium buys almost nothing

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
head-to-head; see the table under "Where marshal stands".

### Rushing the enemy: right about the game, inert on this bot

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

### A 0-ship neutral: real waste, correctly diagnosed, still not worth fixing

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

### Holding a doomed system to sacrifice-and-retake: a real mechanism, a null result

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

### The combination: four null ideas together, and the masking theory tested

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

## Break-even margins (`combat.edge_attacking`/`edge_defending`) and the roster back-port

Started as a marshal-only fix (above) and generalised: `combat.edge_attacking`/
`edge_defending` are now the one place a fight's break-even multiple is computed,
and thinker, claudebot and knower all price their margins off it instead of a
private `1.1 / 0.9` constant. Motivation was a real bug, not tidiness — with
`DEFENDER_ADVANTAGE` a live menu slider, every bot but marshal was pricing fights
against a defender bonus that no longer existed, or under-pricing one that had
grown past 1.0.

Two properties the API has to hold for a bot pulling it in:

* **A knob can only ever *raise* a margin above the figure the bot was tuned at,
  never thin it.** `min_swing` floors the jitter half of the edge (not the
  advantage half) at the swing a margin was fitted against; every bot in
  `models/` passes its own `TUNED_SWING = 1.1 / 0.9`. Skipping this is a real
  regression, not a theoretical one: measured pre-floor, claudebot at
  `COMBAT_JITTER = 0.0` scored 8% (2-24, 34 unresolved) against its own
  pre-back-port self, 60 games, 24 nodes, both seatings — a gentler-than-default
  jitter thinned every margin below what the bot's absolutes were tuned to cover.
  With the floor, that cell and the jitter-0.10 default are both exact 50% nulls
  for all three bots.
* **Clearing the edge is not a promise of capture.** It only guarantees the
  defender loses the worst roll; near-matched forces can still round to zero
  survivors on both sides and hand the system to nobody. `preview_fight(1, 1,
  0.1, 0.75)` clears `edge_attacking()` (0.90 vs 0.825) and still annihilates.
  Every caller floors its ask at `target.ships + 1` for exactly this reason.

**The honest margin costs something above default jitter.** Re-measured after the
floor, thinker/claudebot/knower vs their pre-back-port selves (60 games a cell, 24
nodes, both seatings, `DEFENDER_ADVANTAGE` fixed at 1.0):

    COMBAT_JITTER   thinker   claudebot   knower
    0.10 (default)     50%        50%        50%    (exact nulls, by construction)
    0.15               61%        56%        49%
    0.25               33%        40%        46%
    0.50                0%         4%        14%

The old `1.1 / 0.9` hardcode was, by accident, a *gambling* policy: at high jitter
it kept sending at a margin that was no longer statistically safe, and won more
than a bot pricing the real odds does. This is why the finding belongs here and
not as a reason to cap the edge — the fix is correct, the number above is the
honest price of correctness, and a future change chasing that regression back
would be re-introducing the original bug. `DEFENDER_ADVANTAGE` moves the other,
unambiguous way for all three bots (0.75: 53/66/63%, 1.25: 69/94/79%, 1.5:
100/100/100%, same harness) — that direction was never in question, only whether
the bot was pricing it at all.

marshal's own head-to-head numbers move too, now that its rivals are no longer
handicapped by the stale constant — see the re-measured full-roster ladder under
"Where marshal stands" above.

## Defender advantage and the AI (`ai._frontier_order`)

`config.DEFENDER_ADVANTAGE` multiplies the defender's strength in combat, so
the AI's `expand_margin`/`attack_margin` have to be measured against the
*effective* garrison (`n.ships * DEFENDER_ADVANTAGE`), not the raw ship count.
Against the raw count the margins understate every target, and the AI simply
stops expanding — it waits forever for a surplus it already has. At 1.0 the
multiply is an exact identity, so nothing about a default game moves.

That fix is necessary but not sufficient, which is why the slider stops at
`config.DEFENDER_ADVANTAGE_MAX = 1.5` rather than the 2.0 first drafted.
Measured over `tests/sim` (40 seeds, 18 nodes, 3 players): 0.75 finishes 38/40,
1.0 → 36/40, 1.25 → 26/40, 1.5 → 19/40, 2.0 → **3/40** — and the 2.0 failures
are *hard* stalemates, not slow games, unresolved even at a 3000-turn cap. Both
sides produce symmetrically, so a fortress bonus that large grows the defence as
fast as any assault can be massed against it. One AI variant (an additive rather
than compounding cushion) moved 2.0 from 2/24 to 7/24 finished, which is not
enough to call it a tuning problem.

`Settings.from_dict` clamps the field to that ceiling, unlike the other balance
knobs, whose out-of-range values are merely odd rather than unplayable.

