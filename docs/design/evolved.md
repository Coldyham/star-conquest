# evolved design notes

`models/evolved.py` is a blind bot whose choices come from trained weights
rather than hand-written rules. Each turn it offers every system a few priced
moves and a scorer, evolved offline by CMA-ES, picks among them; a short
look-ahead on top checks the policy's move against variants of it. Every move
and feature names the principle of war or games it encodes, and evolution
decides how much each matters. Roster-wide rules and the measurement method are
in [`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## What it is for, and its constraints

The aim was a bot whose strength comes from training over principled moves,
not from tactics reasoned out by hand, and that plays differently from the
roster. It was held to four constraints:

| Constraint | What it means here |
| --- | --- |
| **Blind** | It reads only the board. It never reads or runs another seat's code: no `IS_ORACLE`, no `ai.STRATEGIES`, no imports from other bots, no borrowed openings, no rollouts that call a rival's strategy. `tests/test_evolved.py` parses for it. Offline, the roster served as sparring partners; what ships is the weights plus a board-reading policy. |
| **Browser** | Pure Python, no numpy at runtime; the look-ahead stops at a 50 ms wall-clock guard (`LOOK_BUDGET_S`, scaled by `BUDGET_SCALE`); timed in the web build ("The look-ahead", "Cost"). |
| **Deterministic and stateless** | Nothing drawn from `state.rng` or the global `random`; ties break on ids; no memory between turns. |
| **No foresight of the dice** | The look-ahead's copies never roll the game's own dice (see "The look-ahead"), even though a copy of the board would. |

A consequence: knower on Search runs evolved's real `decide` and predicts it
exactly, as it does every blind bot, so knower:2 is a stress test, not a gate.

## The policy

**Moves.** Per owned system, per turn, a fixed menu, each priced by the square
law (`combat.edge_attacking`/`edge_defending`, jitter floored at
`TUNED_SWING`, the ask floored at the garrison plus one):

| Move | Ships sent | Principle |
| --- | --- | --- |
| strike | the ask that wins the worst roll on arrival: garrison, production on the way and its owner's landing fleets | Lanchester's square law |
| heavy | everything above the guard, when that clears the ask | overwhelming force |
| pool | several neighbours landing on one target on the same turn that clear it together where none does alone | concentration of force |
| stagger | the farthest sources launch now so nearer ones can follow and land with them | concentration across distance |
| reinforce | an owned neighbour gets the surplus | moving force to the front |
| hold | nothing; the bar every other move must clear | economy of force |
| evacuate | every ship out of a system that falls next turn whatever we do | preserve the force |
| recapture | a strike timed to land just after a capture, against the attackers' survivors | the counter-attack |

The guard kept at home is `edge_defending` times a learned mix of hostile
fleets inbound and the largest rival stack next door.

**The scorer.** Each (system, move) gets `w · φ`, with φ a few dozen size-free
features read off the board (a move kind's weights are one group of
`LAYOUT`; 447 weights in all). A small residual net, the *head* (8 tanh units
over the first 25 strike features plus the strike kind), adds to the four
strike kinds' scores. Candidates are taken best first while they clear the
hold bar, one strike per target. The features that carry a principle by name
are listed in the module docstring; among them `src_left` (don't strip the
front), `relief_in_time`/`relief_gap` (defence in depth), `emptied`
(counter-strike a base just emptied), `heart_dist` (the enemy's gate is
down), and three silent once two seats remain: `leader`, `busy` (let rivals
bleed each other) and `new_front` (avoid a war on two fronts).

**Weights** live in `models/_evolved_weights.py` between `# BEGIN WEIGHTS` /
`# END WEIGHTS`, written by `tools/evo_train.py`. Underscore-prefixed so
`ai.load_models` skips it; the bot loads it by path. A weights file carries a
comment line naming each group's features, so a file from an older layout is
read by name and carried into the current one (`evo_train --migrate`).

## The look-ahead

On by default (`aux`, *Look-ahead*: 0 Off, 1 On; an untouched seat reads 1.0).
From the policy's move it builds a few variants: each chosen non-reinforce move
banned in turn (up to 4), each near miss forced (up to 4), and the hold bar
shifted by ±0.5. Each plan is played `LOOK_TURNS` = 6 turns on a copy of the
board (`_clone`), our side continuing on the policy, and scored by 0.6 of our
production share plus 0.4 of our ship share.

**Rivals are fixed blind rules from doctrine**, never their code:

- the **most likely course of action** (MLCOA): they hold. Measured (below),
  no blind model of the roster's launches predicted them better than this;
- the **most dangerous** (MDCOA, `_danger`): every rival stack strikes all-out
  now at a system of ours it can take on arrival, then holds. Capabilities, not
  intentions.

A plan is chosen on its likely value, but vetoed if its dangerous-case value
falls below the policy's own move's by more than `LOOK_SLACK`. **All of the
gain is the veto:** planning on the likely case alone measured level.

**Dice.** Every fight in a rollout is scripted at the square law's expected
roll (`engine.end_turn(script=TurnRecord(orders, dice=[0.0] * 4096))`):
deterministic, one rollout per plan, and no foresight. The copy also carries
its own `dice_salt`: with the board's salt, seed and turn it would roll the
game's real dice, which the first measurement did by accident (+7 pts, about
three quarters of it foresight; void).

**Cost.** Native CPython, 75th percentile of a decide, the worse of two and
five seats: 8 ms at 11 systems, 12 at 18, 21 at 31, 34 at 40 (`decide_ms`,
fitted `34 × (nodes / 40) ** 1.1`; 0 when Off). **In the browser** (the pygbag
WebAssembly build, Apple-silicon Mac, 2026-10-17) it ran at native speed on the
same positions: 11:6:2 p50 5.7 ms, p95 17.6 (native 6.0, 17.8); 31:6:5 p50
10.6, p95 40.3 (native 11.3, 40.6). With the guard on, 2 decides in 300 went
past 50 ms, the largest 53.6: the guard is checked between plans, so the plan
in hand finishes. A slower device meets the guard more often, which only
shortens the look-ahead. Simulating a browser at half speed (guard scale 0.5)
cost nothing measurable either: +2.51 pts over Off against +2.69 unguarded.

## Where it stands

The 8-cell headline (`tools/evo_bench.py`, 200 seeds from 2,700,000, both
seatings, the bot's win share; fair share in multi-seat cells is 1/players):

| Cell | vs marshal | vs actuary | vs thinker |
| --- | --- | --- | --- |
| random:7:6:2 | 41.1% | 21.9% | 81.1% |
| random:11:6:2 | 43.0% | 18.9% | 83.2% |
| random:11:6:3 | 23.8% | 10.1% | 45.4% |
| random:13:3:2 | 49.2% | 21.6% | 87.4% |
| random:18:6:2 | 56.5% | 23.4% | 92.2% |
| random:24:3:2 | 45.0% | 17.0% | 89.5% |
| random:18:18:2 | 61.3% | 44.5% | 95.8% |
| random:31:6:5 | 17.2% | 6.2% | 40.6% |
| **all** | **36.1%** | **17.1%** | **68.4%** |

The look-ahead adds +3.17 pts over the plain policy across those cells (z
+7.00, 19 of 24 cells up), most on bigger, slower maps (24:3:2: marshal
+13.1, actuary +7.1).

- **Duels: marshal level** (mean 49% over the six duel cells, 41–61%), well
  behind actuary.
- **Multi-seat: short** (11:6:3 23.8% against a fair 33%; 31:6:5 17.2%
  against 20%). See "Why multi-seat is short".
- **It plays unlike the roster.** `tools/bot_distance.py` (11 systems):
  evolved's nearest roster bot is claudebot at 0.38 in duels and 0.34 in three
  seats, while each roster bot but rusherplus has a neighbour within 0.12–0.25.
  It launches 55% of its garrison a turn (actuary 15%, marshal 10%), 79% of it
  to its own systems.

## Training it

`tools/evo_train.py` runs CMA-ES (`uv run --with cma`). A genome's fitness is
its mean result over paired seatings on fresh maps each generation (seed
`SEED_BASE + 1000 g + k`, the same maps for every candidate in a generation):
half its production share at turn 40, half the result, with a game still
running at `--cap` scored by share and one decided early adjudicated (share at
0.9 or 0.1 for five turns: about 13% fewer turns, and the call matched every
finished game in a 96-game check). The default panel is thinker, marshal ×2,
actuary ×2, claudebot and `evo:shipped` (the committed weights, a league of
one); cells 7:6:2, 11:6:2 and 11:6:3. `--only` evolves named groups or
`group.feature`s and freezes the rest; `--start` begins from any weights file;
`--cand-aux 1` trains with the look-ahead on (about 6–11× a game). A run lives
in `results/evolved/<run>/` (`es.pkl`, `log.jsonl`) and resumes anywhere;
`--write --out FILE` writes the mean of the last generations (`--from-log-mean`).

**Noise.** With few seeds the fitness was mostly noise: the first runs' SD was
0.05 per evaluation, larger than most differences between genomes. 160 paired
seeds, the turn-40 share and the panel above bring it to about 0.012 (measured
with `tools/evo_probe.py noise`). Everything below was trained that way.

**How the shipped weights came about.** (Labels name training runs: p1–p4
the first policies, k1 onward the later ones; the weights file names the run
it ships.) The first trained policies (p1–p4)
beat thinker about 75–80% of the time but marshal only 21–36% and actuary
10–21%. The first gains were the moves and principles, not the
tuning: ending stalled games (p1), "don't strip the front" and the relief
features (k3, +3.1 pts, headline z +9.9), the head trained on top of k3 (k6,
+2.6 headline), the melee features (k16bcore, +2.2 in three-seat cells), and
evacuate/recapture trained on their own (k16k13, +0.5 headline). The
look-ahead then added +3.2.

## Measuring it

`tools/evo_bench.py` is the gate: the bot against fixed opponents on fresh
seeds, both seatings of each duel map (a seed scores 1, 0.5 or 0, z over
seeds), every rotation of a multi-seat map against copies of the opponent.
Guards are lifted (fixed work, the same on any machine); `--budget-scale 1`
or `0.5` restores them for a timing-faithful run (ledgers under
`results/evolved/bench/clock/`). `--weights FILE` benches a weights file
without touching the shipped one. Use `evolved:0` for the plain policy.

The method that worked: a 50-seed screen on the quick cells, a 200-seed
confirm on a fresh block, then the 8-cell headline, which must not lose. With
200 seeds a cell's standard error is about 2.5 pts and the nine quick cells
pool to about 1 pt, so **an idea worth chasing has to show +4 on the screen**:
the +1–2 arms that passed confirms one by one could never close a gap of tens
of points. Measure a direction's ceiling (an unconstrained, ugly version)
before building it.

`tools/evo_losses.py` explains losses game by game (the deciding turn, systems
lost, failed attacks, the exchange rate); with `--control` it plays the rival
in every seat as a baseline, since a symptom every losing seat shows is not
evolved's fault. `tools/evo_probe.py` has `moves` (which moves it picks,
`--bias GROUP=X` to switch one on), `noise` and `identity` (decision by
decision against a git revision; must be 0 for a refactor).

Seed blocks 2,000,000 to 5,400,000 were used by this work; start new
measurements at 6,000,000.

## Why multi-seat is short

Three diagnoses against a control (the rival in every seat), 11:6:3, look-ahead
on, all on fresh seeds:

- **The opening** (first 15 turns): evolved's losses look like any loser's.
  Production share at turns 10 and 15, neutrals taken, failed neutral attacks
  and the turn it is first hit all match the control's losses. It loses more
  often, not differently.
- **Around the deciding turn** (`evo_losses --control`, ±5 turns): one
  difference, **failed attacks twice the control's** against both marshal and
  actuary, and small (about 3 ships each against 5–9). A likely cause was a
  third seat's fleet landing at the same target; pricing that pile-up into the
  ask screened null (below), because the fleets that kill those strikes are
  mostly launched the same turn and cannot be seen.
- **Drawing fire.** Even in its wins, rivals aim 37% of their attacks at it
  (marshal; the control's winners 18%). The trait that differs: half its
  ships are in transit at any time (the control 29–43%), which marshal reads as
  the cheaper target (60% of turns against 43%). Cutting its own-to-own moves
  screened worse (below).

No remedy that a blind bot can apply has been found. The prediction
study ("Opponent learning" under "Decided against") is the wall underneath: whether a rival strikes is predictable, its size
and timing are not.

## Decided against

Each built and measured; paired against the bot of the day on fresh seeds.

| Idea | Result |
| --- | --- |
| **The first look-ahead** (14 Oct): rivals modelled by evolved's own policy, 7 arms, plus a learned value | null. evolved launches 2–4× more ships a turn than the roster, so the self-model imagined far more attacks than real rivals make (prediction study, below). Removed, then rebuilt with doctrine models. |
| **A learned value** for rollout leaves | null against the share heuristic |
| **Full-genome retrains** (all weights, k1/k2, k9, k14) | level, or trades thinker wins for marshal/actuary wins (k9 +1.05, z 1.54, not confirmed). The linear policy is at its ceiling for its inputs. |
| **The head trained longer** (k12) | −2.15 pts, z −2.99 |
| **More expansion** (neutral bias +1 to +3; `race`/`backed`/`forgone` at first) | worse; the expansion gap is not a lever |
| **Relief retuned** (`relief_gap` ±2) | changes no game |
| **Two-turn moves**: `stage` (a strike relayed over two hops), `converge` (strikes timed from several sides, nearer sources reserved), `awaiting` | −0.15 pts (k7); null with hold retuned (k8). Removed. |
| **Pot odds and Lanchester's linear law** as strike features (`exchange`, `piecemeal`) | −0.38 (k10); kept at their later-trained weights, not a gain on their own |
| **Fitness blended with the exchange rate** (`--trade 0.2`) | +0.76, null; evolution learned caution |
| **Arm A: defeat in detail, cutting lines, the culminating point, miai** (`detail`, `cut`, `culminate`, `fork`) | −2.07 (k15). Removed. |
| **Beyond defeat / honte** (`src_mass`, `mass_gap`, `rear_gap`: rival ships that could gather on a system) | screen +1.07, confirm +0.38 (k17), +0.91 z 1.74 with one seat's mass (k17m); never passed. Removed. |
| **The opening as its own game** (`opening`, `open_neutral`, a posture for the first 15 turns) | −1.28 with the look-ahead on (k22). Removed. |
| **Melee features trained in melees only** (k21) | −1.07 with the look-ahead on |
| **Opponent learning** (in-game, from the board) | no blind model beat "the rival holds" at predicting roster launches: own-policy variants 2–4× worse, ten 3:1-rule threat models no better, an online pick 1.03–1.11 of "holds", the best single model in hindsight 0.89. As prediction, no-go. |
| **Look-ahead variants**: the likely case alone; 4 dice samples averaged per plan; a blend of likely and dangerous | −0.07 / +2.13 (against +2.69 for expected dice) / measured only with the voided real dice, not pursued |
| **The look-ahead's ceiling**: a 10-turn horizon, twice the plans, wider postures, no guard | −0.5 against actuary (mode 6 +0.5 on the same seeds). More search buys nothing on this policy. |
| **Fewer own-to-own moves** (reinforce bias −1/−2/−4) | −1.7 / −1.2 / −3.1 |
| **Pricing a third seat's fleet into the ask** (the pile-up fold) | −0.18; duels identical, since the fleets involved launch the same turn |
| **Retraining with the look-ahead on** (the ten move-kind biases and guard parameters, `--cand-aux 1`, 68 generations) | −0.92 (18:6:2 against marshal −9.5): the weights tuned without it were not mismatched |
| **A separate weight set for multi-seat** (the same ten, trained on 11:6:3 only, to ship by seat count) | +0.67 on 11:6:3, null |

**Why evolution does not reach actuary.** It tunes weights over a fixed menu
and fixed features; it cannot add a calculation the policy does not do.
actuary projects every system's garrison forward from production, fleets in
flight and the pile-up rules, and commits launches one at a time against that
projection. Early in evolved's lost games 17% of actuary's attacking ships went
on attacks smaller than any evolved considers (`ask` prices the worst roll on
today's board); no weight can choose a move the menu does not offer. Closing
that gap means giving the scorer a projected board, which moves the design
toward actuary's.
