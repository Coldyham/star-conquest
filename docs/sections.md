# Sections, by file

Each design file's sections, one line apiece. Grep this page for a constant,
function or topic to find which file and heading to read; don't read it whole.
The file index is [`README.md`](README.md).

## design/rules.md
- One section per `CLAUDE.md` heading (Architecture, Turn resolution, Animated end of turn, Persistence, Play-by-post, Key conventions, Map generation, Hand-authored maps), each rule in its full wording.

## design/core.md
- **Settings tokens.** Pruned, then deflated. Mode, players, nodes and seed are always emitted.
- **A seat left to the seed.** Why `random` resolves in `build_state` rather than in a dispatcher bot (the oracle contract). The pick is derived, never drawn. The pools are fixed lists, append-only (`RANDOM_POOLS`), the seed's era (seed // `POOL_ERA`) naming the one it deals from, so appending a pool re-deals no map; why the seed and not a `Settings` field; the menu's seed cap and stale builds. The log records what it dealt. The win overlay reveals it, and `Settings` keeps the placeholder.
- **Challenge links.** Score and tie rules. Links travel by clipboard only. Editing a challenge asks first rather than locking the widgets.
  - *Keys outlive the schema that made them.* `_LEGACY_KEY_DROPS`, `findTwin` folding, newest key wins, the `sc_config_key` trade-off, the int/float `aux` digest leak.
- **Ship-speed growth.** Applies at launch only, and compounds rather than growing linearly (with the reason). Re-times from `length_ly`.
- **In-lane battles.** A fight is a geometric meeting, fought pairwise; the winner is only thinned. Growth mostly switches the feature off.
- **Persistence, replay & history.** Format v2 records orders plus dice, and the alternatives that lost. Rules live in the log. The `"ai"` flag doubles as the seat claim. A resume always lands paused. A live turn's rng is derived (`replay.reseed`), so a rewind is not a re-roll. *Two streams*: bots decide in parallel from the turn's start (`engine.decide_seat`), and each fight rolls dice keyed to its place (`engine._Dice`), so seat order and unrelated moves can't move a roll; why knower seeing the real dice is fine; the `keyed` marker.
  - *`match_id`.*
- **Star names.** Generated at build time; named last so seeds don't move. Labels are placed collision-first, with a reserved slot for marks.
- **Symmetric layouts** (under "Past a standard board the box grows"). Hub, ring, wheel and core; why `hub` is pinned rather than a `RULES_VERSION` bump; added lanes chosen on one seam and rotated; why `core` opens the middle up; where `layout` is inert.

- **Rules in full** (detail kept out of `CLAUDE.md`): in-lane battles; pile-up resolution; replay and history; an all-bot game has no human seat; whole-number floats don't survive the browser; past a standard board the box grows; settings, challenge links and keys; star names.
## design/shell.md
- **Text sizing** and **`config.touch_ui`.** Measured layout. The send popup is the one place the tap floor gives way.
- **Map viewport margins.** The `node_clearance` floor, and clamping against the fit-padded span.
- **Send popup / `Ui.editing_existing`.** Unwinding to IDLE on an edit; the slider is hit-tested before the panel drag.
- **Forwarding rules.** A hold per system and a share per lane. Adding a lane splits evenly and removing one undoes it; + takes from home, then the other lanes. One rounding formula behind orders and every number shown. The popup's three shapes, and what a split sheds on a short screen. The hold badge under the system. Names fit in three steps.
- **Combat rules page.** It teaches the square law from the real code (`preview_fight`): parameters rather than `config`, corners rather than samples, demo sliders on `MenuState`, hand-broken prose, the jitter matrix.
- **Losing / spectator mode.** Keyed on defeat, plus how the reset view frames the map.
- **Ironman and the ships-lost readout.** Ironman is a local preference that hides History (and so rewind) until a game is decided (`Ui.can_review`). The top bar shows our `ships_lost`, the score's tie-break.
- **Route mode.** A proposal plus a confirm. Owned-only paths are forced. A plan can't contradict itself, but it can loop with existing rules.
  - *Two sub-modes.* Chain and rally share `flow_field`. Distance is travel turns, not hops. One Mode button. Ties split evenly. Auto-route. A tap always aims (two rejected shapes). A plan keeps a system's hold.

- **Rules in full** (detail kept out of `CLAUDE.md`): measured layout and `touch_ui`; browser bridges (`softkeyboard`, `webstore`, `share`, quitting); send popup and spectating; viewport margins; the Combat tab; route mode.
## design/turnfilm.md
**Animated end of turn** is the reasoning, organised by bold lead sentences. In
order: why it exists (legibility); it plays back the past; events carry results;
why it is its own module; the crossing sort key; the launch tick; leads rather
than dwells; production as a mark; combat's two speeds (`linger`); marks outlive
their film; marks with no film at all; fog as the union of both turns; the
deferred camera snap; pause freezes; chaining runs of turns; `_primed`; carrying
the overrun and the frame cap; the camera and autoplay exemptions from "any
press skips"; the loss label; the shared mark slot; one mark per system;
scrubber timing; no in-game toggle; the rim clamp; stored lane tracks; a burst
only for a fight; what is deliberately not animated.

- **The rules in one place**: the full rule list behind `CLAUDE.md`'s short version.
## design/hand-maps.md
- **Hand-authored maps**, with subsections: the recipe on `Settings` (and its `_LEGACY_KEY_DROPS` cost); a recipe rather than a `GameState` as the editor's model; the geometric rules as hard blocks (mapgen's own figures); `CUSTOM_MAX_NODES` versus `MAX_NODES`; Auto-lanes replaces rather than merges; a crossing warns while a graze blocks; two gestures, one commit path; no deselect gesture in Systems; the seat rows as one control; auto-relane skips a drag; seats gap-free without forced renumbering; what the menu hides; the wider box; opening on a blank canvas.

- **The rules in one place**: the full rule list behind `CLAUDE.md`'s short version.
## design/leaderboard.md
- **Bot replays.** A scheduled job rather than a service.
  - *Through `build_state`*; *what the replayed seat is tuned to* (`replay_aux`, the top of each bot's slider); *a loss is a result, not a score*; *a win stores its own replay*; *`engine_rev` hashes the simulation*; *the one table the public cannot write*; *estimated difficulty* (the share of bots that lose a map, as a sort and an Easy-to-Very-hard badge).
- **Checked scores.** The id rides on `Challenge`. Two consented senders. `game_logs` is private. No identity on a row. `is_current` and `rules_version`. The verifier binds a log to its setup, and re-rolls every keyed turn's dice (hand-picked rolls are a `mismatch`; turns from before keying pass unchecked).
  - *Watching one back* (a watched result is not ours to post); *versioning: bots are free to move, the engine is not*.
- **Playstyle readings.** The player page's panel, read by the worker from `public_replays` only, one row per match and never per name. Every field sums, so the page pools a name's rows, and the bots' column is the same shape, pooled from self-play winners (`playstyle-baseline.mjs`, generated). `rev` and `shape`. *Decided against: warming actuary's learner from a profile*.
- **The grace period.** Half an hour's grace after losing a node or its neighbour, derived in `fold`, for a game started while you still had access (stamped at Start, `scores.campaign_start`). *Decided against: a cooldown between moves; an "able to capture" flag on its own*. *In the game*: a confirm before Start and a top-bar countdown, from `/api/campaign` running the same JS.
- **Campaign fleets (proposed, not built).** Real-time lanes on the meta-map: a launch locks a claim, so a neighbour stolen mid-game no longer voids it. Holders see inbound fleets, and one fleet per player paces the week. Collisions go to the better score. Identity is the open problem.

- **Rules in full** (detail kept out of `CLAUDE.md`): the game and the board are one site; crowns, the weekly campaign and embargoes; the bot column; a replay is never shown as if it still reproduced the game.
## design/setup-families.md
- **Setup families.** The problem (62 configs, 28 families on 2026-10-14); **Pace** (the median lane estimate, `PACE_LY` and how it was fitted, why about 81% exact is the ceiling, the bands); **The family tree** (parent = nearest older config within a cost of 3, the cost table, opponents as a light edit, rescales, why not clustering, what it shows on live data; *data and fallback*; *the view*); *proposed, not built*: a "made from" stamp on the score; *decided against*: folding opponents, a By ruleset view, a 2-D map, clustering, a family id on `Settings`, grouping on `sc_config_key` text, storing the exact median lane.

## design/schema.md
- **The trust model** (append-only by the absence of policies; worker-only and closed tables) and **re-pasting upgrades a board** (added columns, drop/add checks, view columns go last, and the `public_replays.rules_version` incident).
- Per relation: **games and its embargo** (first insert wins; the moves, never the score; why `game_embargoes` is derived); **scores**; **game_logs** (the size bound, claims stored as claims); **score_checks**; **playstyle_readings**; **sc_config_key and sc_bots** (the search_path lint); **configs and config_tags** (first name wins, distinct players, legacy tags at weight 1); **bot_scores**; **campaigns**; **public_replays and public_watchable_replays** (owner rights, joining the embargo through `scores.game_key`); **config_summary**; **counted_scores and the crowns**.
- **The grant trap.** `service_role` skips policies, not grants; three incidents, the last through a `security_invoker` view chain; `tests/test_schema_grants.py`.
- **Play-by-post tables.** Kept apart from the board; inputs, not a board; seat tokens; the deadline; `board_digest` as a tripwire.

## design/par.md
- **Par search (a local check).** Why it is less random than it looks (bots are free to predict; two sources of chance, uncoupled since keyed dice; a rewind is not a re-roll, and what it was before); the floor and its one inadmissible case; lucky versus honest dice (lucky is not a ceiling); the beam search; readings retaken 2026-10-14 under keyed dice: it beat 7 of 9 recorded human wins on honest dice, and on the live board (103 maps under 20 systems) honest dice match the best human score (median 1 turn sooner) but beat 6 of 7 wins played on keyed dice by a median 18 turns; the rollout bot matters more than the beam, so each child now rolls out with marshal and actuary and keeps the best (better on 28 of 36, at 2.4x the time); the `verify_scores` gap it exposed, whose dice half is closed for keyed turns.

## design/seat-play.md
- **What decided the games.** Both bots evacuate into the system you struck from; covering every exit (a pincer whose sources keep their garrisons, or relief timed onto an evacuee's landing) is what killed stacks; a dead end bottles a stack; actuary punishes a thinned garrison the next turn; opening tempo; a free-for-all goes to the swap war.
- **Readings over the logs.** Strikes on a system emptied at launch succeed 43-47% against people and 67-76% against bots (public replays); marshal's price is for a garrison that runs; actuary crossing its own transfers; actuary waiting for the worst roll on neutrals. The last two were built and measured in `actuary.md`.
- **What a person should do.** The brief version, including pinning actuary.
- **What was built from these games.** Four marshal arms (all deleted) and actuary's `SELF_RELIEF`.

## design/pbp.md
- **Context** and *Decisions taken up front*; **Constraints that shape the design**; **What it reuses**; **The one idea everything follows from** (the stored log is the record, so a turn is decided once); **Verified against a real deploy** and *the bug that made the digest worth having*; **Testing the backend**; **Opening a match**; **How a deadline works**; **Public matches and the lobby**; **What a poll costs** (briefs, the idle floor; *decided against: a slower steady cadence*); **Traps**.

## design/bots.md
- **Measuring a bot: what earlier sweeps got wrong.** The method checklist, with a pointer to the evidence for each item.
- **How differently two bots play (`tools/bot_distance.py`).** Distance, kappa and fingerprints on shared positions, split into contested and opening; why it replaces the candidate share as the first check; the first reading.
- **New bot families (proposed 2026-10-05).** Six designs to play differently from the roster. surveyor (now actuary's planned opening) and convoy were built, and learner's model (stage 1, `learner.md`); duelist (the simultaneous move as a matrix game), apprentice (a learned evaluator) and riposte (the counter-punch) are not. Also two measurement ideas not yet built.
- **Lane length across the parameter space.** Node count and ship speed move lane length over more than an order of magnitude, so a constant keyed off travel time is live in one regime of three.
- **The person's habits.** A pointer to `learner.md`.
- **Positions from real games.** `position_suite`: `faster`, `median gain` and `recovered`, and why they must not be blurred together.
- **The first census and setup sweep off the live board (2026-09).** People mostly play the middle regime. The first position-suite numbers.
- **Replaying a bot for the leaderboard.** Best profile, not menu default. Guards lifted 100x (`inf` in tests). The seat-flag incident, and how a stored replay and the autoplay fix resolved it.
- **Break-even margins and the roster back-port.** `min_swing` floors; clearing the edge is not a capture; the honest margin costs something at high jitter, and that cost is correct.
- **Defender advantage and the AI.** Margins are priced against the effective garrison; the slider caps at 1.5 because of stalemates.
- **The in-app bot maker: built, measured, not merged.** Branch `bot-maker`, PR #20.
- **Bots that aren't Python.** Why the wire protocol is shaped as it is.

- **Rules in full** (detail kept out of `CLAUDE.md`): external bots' four rules; `AiParams.aux`; the `tests/sim` harness; per-seat AI and pricing a fight.
## design/knower.md
- **Simultaneous resolution.** Why an oracle is possible at all.
- **What the oracle buys, and where.** A turn of warning, worth more the faster ships are. Depth 0 is not thinker.
- **Each Oracle setting against thinker and marshal.** Off / Predict / Search head to head in three cells. Only Search beats marshal; the ladder's 93% over thinker was a lucky 30 seeds.
- **Built, measured, removed.** Two ideas.
- **The depth search: branch the root, play the rest on.** Why branching deeper did nothing, and why more openings beat more turns. Most of the result against marshal comes from borrowing marshal.
- **Borrowed candidates (`EXTERNAL_CANDIDATES`).** Candidate win shares, contested decisions only, and why `SEARCH_WIDTH` is 2.
- **How far to look.** The horizon is the longest lane plus `LANE_CUSHION`, which replaced a fixed depth, so Oracle is now Off / Predict / Search. Also the old depth curve.
- **Cost per decide, and where the search guard trips.** The `ply_ms` fit and `setup_warning`, which also counts rival bots that declare `decide_ms`. The browser is unmeasured; never measure on a loaded machine.
- **Seats it cannot run: stand-ins and people.** A predicting module's `stand_in_params` (actuary: Learning runs as Planned), run in the forecast and the rollouts and trusted. Against actuary-Learning, untrusted is null at Predict (z +0.95) and large at Search (z +9.50); trust adds z +6.54 / +3.88. People stay on `_blind`: a stronger bot is a worse forecast of them, and assuming they hold loses to bots flagged as people (z -3.33).

## design/council.md
- **What council changes, and what each change bought.** actuary as an adviser, the learned judge, each adviser playing its own line (the biggest gain). The judge's features and fit. `aux` 0 is knower at Search.
- **How it borrows knower.** A private copy of `knower.py`, patched; the oracle rules kept.
- **The measurements.** Untouched seeds against knower:2, actuary and marshal; positions from real games; the unbeaten posted maps; five players.
- **What it costs.** Browser timing, the guards on slow devices, the missing `setup_warning`, the gentler-jitter test exemption.
- **Built, measured, dropped.** Strikes, melee ideas, more openings, hybrids, dice shopping, tuning, crossover, successive halving, retrained judges.

## design/actuary.md
- **The ledger and the greedy.** A timeline per system, fights priced so only the verdict is worst-case, one value in ships, risk as an expected loss, greedy commits. It is not an oracle.
- **Cost, and the caches that make it affordable.** Version-stamped caches and the `_idle` prune, both checked exact. 1.6-3.9 ms a decide, and what that does to a knower Search seat.
- **Where actuary stands.** Four map cells, free-for-all, the combat sliders. Weak at 24 nodes 3 ly/turn and at jitter 0.3.
- **As one of knower's borrowed candidates (measured, not shipped).** 10.4% of contested picks, distinct from the default 91% of the time.
- **What the measurements changed.** Survivors and threats at the nominal roll; the constants' plateau; `FRONT_BONUS` 0.5.
- **The 2026-10 sweep: off the plateau.** 58,400 duels against marshal: `MIN_GAIN` 0 (+7.4 pooled, +18.6 at 24 nodes 3 ly/turn) and `FRONT_DECAY` 0.75 (+5.6) win in every cell; risk and reinforce weights confirmed. Reproduced on fresh seeds, where the two do not stack and a horizon cap of 8 does nothing. `MIN_GAIN` 0 costs 7-13% a decide at default sizes, 51% at 80 nodes. It also wins against thinker, knower-Predict, across the advantage sliders and in free-for-all; level-to-better against knower at Search. Shipped as `MIN_GAIN` 1e-9 (exactly 0 commits float noise; level with 0 over 12,000 games), with `decide_ms` refitted to `6.4 * (nodes/40)^0.77`.
- **Behind on income, ahead on ships (measured, not built).** A switch to recklessness while a ship lead runs out against an income gap. The standing is 7% of post-contact turns, mostly a 1-2 ship lead for 2 turns; a real lead on a short clock came up 4 times in 84 actuary games. Behind on both is where seats lose.
- **The planned opening (Opening: Planned).** The default `aux` stop: it plans the land-grab as a one-player puzzle until first contact (Greedy is the ledger throughout); the clock is about twice the earliest strike; a gain on 40 and 80 nodes against marshal and knower, noise below; a side too small to plan (under 7 systems held plus region) is left to the ledger; it costs marshal as a host; the deleted first attempt (rules about contested neutrals). *Planned against Greedy again (2026-10-13)*: on fresh seeds the ledger opening is level or better at 3 and 6 ly/turn at every size and at both stops (Learning pooled z +2.40), and the plan wins only at 12 ly/turn on 24-40 nodes; a gate on lane speed is proposed.
- **A fleet landing next door is relief too (`RELIEF_IN_FLIGHT`, 2026-10-16).** A struck rival's fleet landing next door that can turn round in time counts as relief at 0.3. Bit-identical at 0; the measurements are in `marshal-pricing.md`.
- **Relief only the same stack can pin (`SELF_RELIEF_SHARED`, built, measured, deleted 2026-10-16).** Pooled defence, from the author's triangle: a neighbour that only the threatening stacks border, counted in full, and the rest at 0.8 or less. Null to worse against actuary and marshal in every form, the front-only one included. The double count is not why `SELF_RELIEF` 1.0 lost.

## design/learner.md
- **Why the oracle flag does not make memory safe.** Every caller that runs a bot's `decide` on copies, branches, isolated positions or rewound boards, and `load_models` wiping module state.
- **The memo tree.** A board is a node found by content; its parent is a stored board it provably follows (`_follows`). What a cold start costs.
- **What a turn shows** and **the model and the prior.** Fresh fleets, one-turn launches from the garrison residual, strike records by ratio, send share, guard, evacuation; the prior fitted from roster self-play.
- **The prediction check (`tools/learner_check.py`).** Four predictors (none, all, prior, learner), Brier and squared ship error; the two plan measures replaced before the first reading. Results in four cells on two seed ranges; what the model can tell apart (guard and evacuation, not the strike curve).
- **Sizing a strike to its target, and splitting the strike from the target.** Re-read under actuary's `MIN_GAIN` 1e-9. Sizing kept: ship error down in 5 of 8 cell-runs, most at 24 and 40 nodes. The split deleted. The all-in share separates the roster.
- **Predicting people (`--logs public`).** Posted human games scored offline. Memory beats the prior on people by 3.6% of Brier; knower's blind guess at a person, read as a forecast, is worse than assuming they hold, which backs `TRUST_HUMAN` off. marshal and actuary (Planned) are worse guesses still. What the model reads off people.
- **The person's habits (`tools/human_habits.py`).** Posted games against roster self-play. 2/3 of players' ships marks a won game, and players' income crosses earlier but less reliably. The overkill is the endgame. The person empties frontier systems with relief covered 45% of the time (winning actuary 22%); a replayed log rewrites `config`. Frontier losses per system-turn.
- **As a bot.** actuary's ledger on a board with the expected launches added; the Trust knob (Off = actuary exactly, Raise); against actuary Raise is level; first on the 2026-10-08 roster ladder, mostly because the oracle flag stopped knower reading it (worth ~20 points against knower to any bot that claims it without `stand_in_params`; knower now runs Learning as Planned). 0.08 from actuary on contested positions, closer than any two roster bots.
- **Trusting a prediction (built, measured, deleted).** Thinning a source by its expected launch (Trust) and trusting confident calls once proven per rival (Sure). The model's calibration: confident calls are rare and not sharper late in a game.
- **The gate.** Still not passed: memory wins on whether a strike comes, and the ship-error failures left are mostly intervals that cross zero. *Open:* whether that is power (a gate decision), and a strike curve that tells the bots apart.
- **Joining actuary as a stop: the gate (written, not run).** Superseded by the next section. Raise against Off (both flagged, so the model alone) through `tools/sweep.py`: against stock actuary as a null control, marshal, and knower at Predict and Search, in actuary's five 2026-10 cells, fresh seeds from 6001. What passing means (a third stop on actuary's knob, above Planned) and failing (stays on the branch). Proposed improvements, each an extra arm: the prediction as the risk term's reach (Raise charges a threat twice), the strike and no-strike outcomes priced separately in place of an expected fleet, and evacuation and guard fed into the price of a capture.

## design/learning.md
- **Why the curve goes in the risk term**, **what was measured, and what failed first**, **the stop** and **what else it changed.** Built 2026-10-09 as actuary's third Style stop. Assuming a rival will not strike is close to right, and a launch cannot be recalled, so Raise (threat only) was the wrong use. Learning scales each rival garrison in actuary's risk by min(1, `LEARN_REACH` x the rival's strike curve at the ratio to the garrison we will hold). Raise, and Raise kept to unanswerable strikes: level with actuary. A flat reach scaled by `predict`'s call: better against marshal only. The curve read at the projected garrison: `LEARN_REACH` 8, plateau 8-13; on fresh seeds better against marshal (z +2.46, all at 18 systems), a lean against knower Off, level with actuary; against knower Predict and Search, with the oracle claim held equal (Planned flagged too), a lean of z +1.5 / +0.8, not significant; the rest of that row is the claim. Slow, fast and jitter 0.06: better against marshal in all three (z +2.0 to +3.0), better against actuary in the slow cell, nowhere worse; the free-for-all leans better (3 and 4 seats). 15-20% more per decide. The knob, the per-seat oracle claim, and the readings moved to `tools/learner_check.py`.
- **Evacuation in the price of a capture (built, measured, deleted).** learner's improvement 3's evacuation half as an arm of Learning: a doomed rival garrison that sees the strike leaves at its learned rate. Worse against marshal, knower Off and Planned on 4,800 paired games; it can only take credit away from a strike that already wins.
- **Strike and no-strike priced apart (built 2026-10-09, kept).** learner's improvement 2: each rival garrison next door strikes whole or not at all at its chance, and the risk is the expected shortfall, in place of a chance-weighted fleet that never flies. `LEARN_REACH` re-selected (8 of 4, 8, 16); on fresh seeds better than Learning against marshal (z +2.26), a lean against Planned (+1.64) and knower Off (+0.41); slow and fast speed cells, knower Predict and Search, and three free-for-all lineups no worse (knower Search leans worse, z -0.88). 18% of decisions changed, ~18% more per decide. Now Learning's risk term.
- **Memory off: the prior alone (built, measured, flag removed).** Every rival read at the prior strike curve, the oracle claim held equal. On 9,600 paired games (fresh seeds 9001-9400, 18 and 40 systems) Learning beats memory-off nowhere: level against marshal (z -0.49) and knower Off (z -0.80), worse against Planned (z -2.29). Against the roster (on whom the prior was fitted), Learning's gain over Planned is the prior curve read at the projected garrison, not the memory. Against people it is untested. Whether to drop the memory, and with it the oracle claim, is left to the author.
- **Five more arms, and three combinations (built, measured, deleted).** All with memory on, 4,800 paired games each on the same seeds: the pressed row (leans better everywhere, z +1.6 / +0.5 / +1.5, does not pass); strike size at the prior's all-in split, the learned guard (learner's improvement 3's other half), strikes that move together, and `PRIOR_WEIGHT` 1 (null). Every arm that thins the threat is worse against marshal. Pressed + guard, + size, + joint: all worse.
- **The counter-strike (built, measured, deleted).** An option value on a reserve next to a rival, by that rival's learned rate of voluntary frontier empties (Playstyle's reading, which does separate the roster: actuary 0.24 to heuristic 0). Passed on the selection seeds (z +2.75 against Planned), failed on fresh ones (+1.35 marshal, +0.61 Planned, -1.94 knower Off). The memory-off twin did as well, so against the roster the learned rate adds nothing. People sit far from the prior on that trait (posted games: four players at 0.02 to 0.16 against the roster's 0.088), On the person's own positions the learned rate covers more of their real empties than the prior (z +3.77), but it does the same against bots by a wider margin, where it won no more games. So it is not evidence of winning, and the profile question stays open.

## design/convoy.md
- **The plan.** Supply per system and turn, objectives as N ships by turn t, defences then guard reservations then strikes by value per ship, launch at the last moment.
- **Where convoy stands.** Five cells against the roster: above thinker everywhere, level with knower-Predict, below marshal and actuary.
- **How differently it plays.** Distinct in the opening, close to the phase bots when contested; few strikes converge from more than one source.
- **Its opening in front of another bot.** Costs in front of marshal, no gain over actuary's Planned; not borrowed.
- **Where it loses to actuary.** Level at contact, out-traded after it.
- **What the measurements changed.** The guard against the target, `STRIKE_PAD` 1.25, evacuation; the nulls and deleted tactics.

## design/field.md
- **The middle game the roster plays.** actuary-marshal duels read through `combat.resolve_arrival`: half the strikes land on an empty system, a doomed garrison steps into its striker's source (marshal 46%, actuary 31% of evacuees), 49-ship front stacks over interiors holding 10% of the ships, and defensive losses, not attacking ones, separating the winner from the loser.
- **Where a ledger's moves come from.** Traps, full relief and two new phase bots measured and dropped; a stripped copy of actuary's ledger as an exact paired baseline; actuary's value system hard to improve on, except that `FRONT_BONUS` 0 collapses it (6%); pulls that steer the front (a re-tune, 0.08 from actuary) and pinning (distinct, weaker).
- **The field.** Influence per median lane, the stake of a system weighted by how close the balance is, the pull as a share of the strongest; what it does on positions. *How it was tuned*: per turn collapses at 3 ly/turn, locality decides the rest, a neutral's garrison out of the stake, the nulls and the two open variants.
- **Where Field stands.** As fulcrum, fresh seeds, five cells: 55.4% against actuary (ahead in three cells, level at 40 nodes, behind at 3 ly/turn), 72.0% against marshal (+10.8 paired over actuary); the rest of the roster, knower at Search (43.5%), council (6.7%), the ladder, an outside bot; the combat sliders and symmetric maps (ahead of actuary in every cell, marshal beats both at jitter 0.3); free-for-alls, level with actuary, and the strongest-rival field that did not help there.
- **How differently it plays.** `bot_distance.py`: the most distinct opening of any strong bot (0.26 from actuary, 0.37 from marshal), a middle game 0.17 from actuary that strikes more and keeps no stack on a quiet front. *In its own games*: contact three turns sooner and a fifth fewer ships lost defending than actuary against marshal, with the empty landings and evacuations unchanged.
- **What it costs.** About actuary's cost; `decide_ms` reuses actuary's fit.
- **Built, measured, dropped.** Traps, full relief, pricing the rival's reinforcement, value systems, pulls that only steer, pinning, the field per turn, leave-one-out, a transit cost, a field against the strongest rival; open: a capture discounted by the balance, half actuary's front.
- **Folded into actuary.** Field replaces Planned as stop 1, Learning is rebuilt on it. Against marshal, Field +8.8 and Learning +6.0 over Planned, both together +12.5: additive within noise (interaction -2.3, z -1.07). New Learning beats old Learning 54.2% but 38.5% at 3 ly/turn.

## design/marshal.md
- **What the measurements deleted.** The square-law case for overwhelming force. The guard interacts with commitment. Chokepoints lose. Two bugs. Standing aside in a free-for-all (`_wedge`, gated on player count).
- **Where marshal stands.** **The current full roster ladder (re-run 2026-10-18 when actuary's default became Field, beside the 2026-10-17 run after the pour floor and `FEED` re-tune shipped, with the bot before that as a same-seed A/B); update this table, not the docstring.** Also the results against knower's oracle, across defender advantage, and the A/B against the old marshal.
- **A stagger's nearer wave is reserved.**
- **More ideas measured and deleted.**
- **`FRONTIER_GUARD`.** How it came to be 0.40 while documented as 0.3; now 0.55.
- **The 2026-09 tuning sweep.** What was adopted (partly superseded since), the regime-bound `ENEMY_FAR`, cross-opponent checks, negative results, the empty-interior statistic, and the two methodological notes.
- **Is the guard a pool? (run 2026-10-16: a reserve).** Whether 0.55 per system stands in for a pooled guard: a stack's launch shrinks the guards beside it, and Phase 1 sends what that frees. Step 0 (a hand-board test) shows the mechanism exists. The Step 1 census (`tools/guard_census.py`, 1,800 games) finds released guard in the relief for 1.4% of seen strikes on guarded targets, against a 25% bar, with 65% evacuated. So `STICKY_GUARD` and `ISOLATED_GUARD` were not run.

## design/marshal-pricing.md
- **Racing a third player for the same system.** `_rival_waves` and `_after_clash`. The contested-neutral and remnant variants, why a high defender advantage makes waiting worse, and the harness traps. The remnant tactic re-measured seed by seed (2026-10-12): significant in duels, leaning worse in three-player games with knower, so `REMNANT` ships gated to two players alive (`REMNANT_MAX_PLAYERS`).
- **Garrisons run away, so the jitter premium buys almost nothing.** 86.7% evacuate, so the attack margin is the advantage multiplier alone. Dropping the advantage half too is catastrophic.
- **Two doomed neighbours, and what a retreat is worth.** `AVOID_ABANDONED`, `CONSOLIDATE`, `RETREAT_NEAREST`. Budget n in the thousands.
- **Rushing the enemy.** marshal is already a rusher, and the target sort caps this whole channel at about 2 points.
- **A 0-ship neutral.** Real waste, bounded by simultaneity and by fleets already in flight. Null.
- **Holding a doomed system to sacrifice-and-retake.** A real mechanism, null result.
- **Re-tuned for production-before-combat and the garrison-fights-last pile-up.** `_attacker_pileup` and `RISK_PARITY`. The four null tactics don't combine, and the theory that Phase 3b masks them is false.
- **The attack side of the pile-up.** Shipped for correctness at a null result.
- **A fleet landing next door is relief too (`RELIEF_IN_FLIGHT`, 2026-10-16).** From a trap every bot walked into: a fleet that lands next door and turns round onto the target before the strike. Counted in marshal and actuary at their garrison-relief weights (0.5, 0.3): +1.0 to +2.5 points against each other and themselves, reproduced on fresh seeds, null to better against knower, no cost. The motivating position is still struck at those weights; 1.0 refuses it but measures weaker against bots. *Open:* a higher weight against people, and council/knower's `_blind` model of a person.

## design/marshal-flow.md
- **Phase 3b re-flooding an already-covered target.** Neutral-only at first, because of the regression at high advantage; rival sieges are now gated at `RIVAL_REFLOOD_MIN_TURNS = 5`. Symmetric maps can't measure it. How it overlaps `DENY_SWAP`.
- **A non-oracle successor to marshal.** A: `FLOW_AVOIDS_ABANDONED`. B: `RELIEF_AWARE`. C: `FAST_GUARD_WEIGHT`. Then the measurements (60.6% combined) and the `tools/sweep.py` smoke-gate quirk.
- **The empty interior is the retreat.** The concentrating probe (spearhead) loses. `DEEP_RELIEF` is inert.
- **Denying the swap.** Surplus-only, long lanes only (`DENY_SWAP_MIN_TURNS = 4`).
- **When a doomed garrison leaves.** `EVAC_AT` was deleted.
- **What the board's human wins say (2026-09-30).** The hold test on a capture was deleted. `FEED` was shipped, gated at `FEED_MAX_TURNS = 2` (re-tuned to `FEED` 3.0 at three turns on 2026-10-17, in `marshal-joint.md`). *Open: no lone trickles into a rival (not yet measured).*
- **Holding a capture before the counter (2026-10-12).** From a hand-played game (`tools/seat_play.py`): `HOLD_FOLLOW`, a follow-up timed to land between the capture and the earliest counter, and `BACKFILL`, a guard joining a strike when a stand-in on a no-slower lane replaces it. Why each fires as often as it does, the sweeps against marshal, knower and actuary. `BACKFILL` deleted; `HOLD_FOLLOW` re-measured seed by seed (a faint lean, z +1.36 on fresh seeds) and kept off behind its flag.

## design/marshal-joint.md
- **Which constants move together (2026-10-16).** *The decision order is what couples them*: one floor spent in order, the price as a gate with the pour setting the size, one reading used twice; when each constant was last swept and with what missing; the regime gates. *The census* (`tools/coupling_census.py`): the guard is the hub, three groups share decisions (the strike gate, the long-lane pour, the short-lane front), most constants are separable, and decision-level coupling is necessary but not sufficient. *One guard, two floors*: against stock marshal the guard's two floors want different values (the priced strike none, the pour 0.55), a joint move worth 6.0 points on fresh seeds that no sweep of `FRONTIER_GUARD` alone can reach; the relief weight stays put. *Against the roster, the optimum moves*: the same split is level against knower and loses 6.2 (z -8.5) against actuary, where every lower guard loses and the whole guard wants raising (1.0: +3.0 scored as draws, +0.7 as losses), so the split is a best response to stock marshal's stalemates, not a better bot. *The long-lane pour*: `DENY_SWAP` and the reflood gate are substitutes. *The short-lane front*: `FEED` 1.5 is better there and was never tried above 1.0; `FAST_GUARD_WEIGHT` 0 confirmed. *The price*: the two relief weights add. *A timeout is a board not taken*. *What to tune together*, and how to sweep a group (against the roster, not a copy of the bot).
- **Screening against the roster (2026-10-17).** *The objective*: points against marshal, knower and actuary, weighted 1 / 1 / 1.25 with a veto at -4 against any one, a timeout scored 0.1 toward whoever was ahead. *Five quick passes* (138,240 games): the guard's level only trades one opponent for another; what survives acts once the guard is set. *The factorial*: the pour floor, the relief weight, `FEED` and `FEED_MAX_TURNS` mostly add; `FEED` and `FEED_MAX_TURNS` are complements (three turns is null at `FEED` 1.0, the level it was deleted at, and pays at 2.0); the pour floor and the relief weight are substitutes; the relief weight depends on the opponent (+3.7 against actuary, -1.8 against marshal). *Levels around the corner*: `FEED` rises to 3 and is flat to 6, relief 0.65, `FEED_FRONT` worth 3.4, the long-lane pour group unmoved. *`FEED` is a ratio that stops binding*: past 2 nearly every front facing a rival is short. *Confirmation* (pre-registered; 57,600 duels and two free-for-alls): D, the pour floor 0.85 with `FEED` 3.0, `FEED_MAX_TURNS` 3 and `RELIEF_AWARE` 0.65, is +4.7 weighted (z +15.1; marshal +2.9, knower +3.8, actuary +6.8; +3.7 scored as losses), and better in a three-seat field at the default setup (+4.5) and a four-seat one (+3.7); the three constants alone are +3.8 in duels, and the pour floor is worth +2.2 in each field. *Shipped (2026-10-17)*: D, board for board as measured.
