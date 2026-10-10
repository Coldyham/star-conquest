# Docs index

The design notes in `design/` hold the *why* behind the rules in `CLAUDE.md`:
the reasoning, the measurements, and the alternatives that lost. This page says
which file holds which section and lists everything that was decided against,
so a new session can check an idea before rebuilding it.

## Using these docs

- **Before proposing a mechanism, a bot tactic or a re-tune, check
  [Decided against](#decided-against) below**, then grep `design/` for the
  constant or function you would touch. Most ideas that look obvious have
  already been built and measured, and many measured null or worse.
- **Before running a bot measurement, read "Measuring a bot" in
  [`design/bots.md`](design/bots.md).** It lists the method mistakes earlier
  sweeps made.
- `CLAUDE.md` states *what* each rule is. A design file's headings match the
  `CLAUDE.md` bullet they explain, so the heading text is the key to search for.
- Read a design file when you are changing that code, not as background.

**Adding to them.** Put a section in the file for its area, under a heading that
matches the `CLAUDE.md` bullet. Record a negative result as carefully as a win:
what was built, the numbers, why it lost. Then add its line to the section list
below, and to [Decided against](#decided-against) if it lost. Point to another
file's section by file and heading (`marshal-pricing.md, "Garrisons run
away"`), never with "above"/"below". Code comments do the same
(`see "Denying the swap" in docs/design/marshal-flow.md`). Keep each file under
~1000 lines. When one grows past that, split it by topic and update this page and
`CLAUDE.md`'s list of files.

## Files

| File | Covers |
| --- | --- |
| [`design/core.md`](design/core.md) | Settings tokens, the random seat, challenge links and keeping digests stable, ship-speed growth, in-lane battles, the replay log, star names |
| [`design/shell.md`](design/shell.md) | Text sizing, touch targets, viewport margins, the send popup, forwarding rules, the menu's Combat page, spectating, route mode |
| [`design/turnfilm.md`](design/turnfilm.md) | The animated end of turn (`turnfilm.py` and the shell side of playback) |
| [`design/hand-maps.md`](design/hand-maps.md) | The hand-drawn map recipe and the map creator |
| [`design/leaderboard.md`](design/leaderboard.md) | The offline bot column, checked scores, watching a replay, replay versioning, playstyle readings |
| [`design/par.md`](design/par.md) | The par search, a local check: the floor, lucky and honest dice, what a rewind does to the rng, readings against human wins |
| [`design/pbp.md`](design/pbp.md) | Play-by-post |
| [`design/bots.md`](design/bots.md) | The roster as a whole: measurement method, the parameter space, real-game positions, replaying bots for the board, break-even margins, defender advantage, the bot maker, non-Python bots |
| [`design/knower.md`](design/knower.md) | knower: the oracle, the search, its horizon, its cost |
| [`design/marshal.md`](design/marshal.md) | marshal: what it was built on, **the current roster ladder**, the 2026-09 constant sweep |
| [`design/marshal-pricing.md`](design/marshal-pricing.md) | marshal: what a strike or a defence is priced against |
| [`design/marshal-flow.md`](design/marshal-flow.md) | marshal: where the surplus goes, plus the successor fixes and FEED |
| [`design/actuary.md`](design/actuary.md) | actuary: the projected ledger instead of phases, its cost, where it stands, what the measurements changed; the planned opening (Opening: Planned) |
| [`design/learner.md`](design/learner.md) | learner: each rival's habits read off the board and kept turn to turn, memory keyed by the game's path, how well it predicts the roster's launches and a person's, the gate it did not pass, the bot built on actuary anyway, and the improvements proposed for it |
| [`design/learning.md`](design/learning.md) | actuary's third stop (Style: Learning): the learned strike curve in the risk term, each garrison next door priced as striking whole or not at all, and the arms tried on it |
| [`design/convoy.md`](design/convoy.md) | convoy (built, measured, not shipped; code at `7153065`): supply and demand over time, launching only what must leave now; where it stands, how differently it plays, what the measurements changed |

**Not design notes.** [`bot-api.md`](bot-api.md) (the wire protocol) and
[`bot-brief.md`](bot-brief.md) (a brief a player pastes into an AI assistant)
are written for outside developers building their own bots, and
`tests/test_bot_brief.py` checks the brief against the code. Treat them as
published documents, not working notes. [`site-merge-plan.md`](site-merge-plan.md)
is the planning brief for merging the game and board sites. That merge is
implemented, and the brief is kept for its reasoning.

## Sections, by file

### design/core.md
- **Settings tokens.** Pruned, then deflated. Mode, players, nodes and seed are always emitted.
- **A seat left to the seed.** Why `random` resolves in `build_state` rather than in a dispatcher bot (the oracle contract). The pick is derived, never drawn. The pool is a fixed list (`RANDOM_POOL`) and the log records what it dealt. The win overlay reveals it, and `Settings` keeps the placeholder.
- **Challenge links.** Score and tie rules. Links travel by clipboard only. Editing a challenge asks first rather than locking the widgets.
  - *Keys outlive the schema that made them.* `_LEGACY_KEY_DROPS`, `findTwin` folding, newest key wins, the `sc_config_key` trade-off, the int/float `aux` digest leak.
- **Ship-speed growth.** Applies at launch only, and compounds rather than growing linearly (with the reason). Re-times from `length_ly`.
- **In-lane battles.** A fight is a geometric meeting, fought pairwise; the winner is only thinned. Growth mostly switches the feature off.
- **Persistence, replay & history.** Format v2 records orders plus dice, and the alternatives that lost. Rules live in the log. The `"ai"` flag doubles as the seat claim. A resume always lands paused. A live turn's rng is derived (`replay.reseed`), so a rewind is not a re-roll. *Two streams*: bots decide in parallel from the turn's start (`engine.decide_seat`), and each fight rolls dice keyed to its place (`engine._Dice`), so seat order and unrelated moves can't move a roll; why knower seeing the real dice is fine; the `keyed` marker.
  - *`match_id`.*
- **Star names.** Generated at build time; named last so seeds don't move. Labels are placed collision-first, with a reserved slot for marks.
- **Symmetric layouts** (under "Past a standard board the box grows"). Hub, ring, wheel and core; why `hub` is pinned rather than a `RULES_VERSION` bump; added lanes chosen on one seam and rotated; why `core` opens the middle up; where `layout` is inert.

- **Rules in full** (detail kept out of `CLAUDE.md`): in-lane battles; pile-up resolution; replay and history; an all-bot game has no human seat; whole-number floats don't survive the browser; past a standard board the box grows; settings, challenge links and keys; star names.
### design/shell.md
- **Text sizing** and **`config.touch_ui`.** Measured layout. The send popup is the one place the tap floor gives way.
- **Map viewport margins.** The `node_clearance` floor, and clamping against the fit-padded span.
- **Send popup / `Ui.editing_existing`.** Unwinding to IDLE on an edit; the slider is hit-tested before the panel drag.
- **Forwarding rules.** A hold per system and a share per lane. Adding a lane splits evenly and removing one undoes it; + takes from home, then the other lanes. One rounding formula behind orders and every number shown. The popup's three shapes, and what a split sheds on a short screen. The hold badge under the system. Names fit in three steps.
- **Combat rules page.** It teaches the square law from the real code (`preview_fight`): parameters rather than `config`, corners rather than samples, demo sliders on `MenuState`, hand-broken prose, the jitter matrix.
- **Losing / spectator mode.** Keyed on defeat, plus how the reset view frames the map.
- **Route mode.** A proposal plus a confirm. Owned-only paths are forced. A plan can't contradict itself, but it can loop with existing rules.
  - *Two sub-modes.* Chain and rally share `flow_field`. Distance is travel turns, not hops. One Mode button. Ties split evenly. Auto-route. A tap always aims (two rejected shapes). A plan keeps a system's hold.

- **Rules in full** (detail kept out of `CLAUDE.md`): measured layout and `touch_ui`; browser bridges (`softkeyboard`, `webstore`, `share`, quitting); send popup and spectating; viewport margins; the Combat tab; route mode.
### design/turnfilm.md
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
### design/hand-maps.md
- **Hand-authored maps**, with subsections: the recipe on `Settings` (and its `_LEGACY_KEY_DROPS` cost); a recipe rather than a `GameState` as the editor's model; the geometric rules as hard blocks (mapgen's own figures); `CUSTOM_MAX_NODES` versus `MAX_NODES`; Auto-lanes replaces rather than merges; a crossing warns while a graze blocks; two gestures, one commit path; no deselect gesture in Systems; the seat rows as one control; auto-relane skips a drag; seats gap-free without forced renumbering; what the menu hides; the wider box; opening on a blank canvas.

- **The rules in one place**: the full rule list behind `CLAUDE.md`'s short version.
### design/leaderboard.md
- **Bot replays.** A scheduled job rather than a service.
  - *Through `build_state`*; *what the replayed seat is tuned to* (`replay_aux`, the top of each bot's slider); *a loss is a result, not a score*; *a win stores its own replay*; *`engine_rev` hashes the simulation*; *the one table the public cannot write*.
- **Checked scores.** The id rides on `Challenge`. Two consented senders. `game_logs` is private. No identity on a row. `is_current` and `rules_version`. The verifier binds a log to its setup, and re-rolls every keyed turn's dice (hand-picked rolls are a `mismatch`; turns from before keying pass unchecked).
  - *Watching one back* (a watched result is not ours to post); *versioning: bots are free to move, the engine is not*.
- **Playstyle readings.** The player page's panel, read by the worker from `public_replays` only, one row per match and never per name. Every field sums, so the page pools a name's rows, and the bots' column is the same shape, pooled from self-play winners (`playstyle-baseline.mjs`, generated). `rev` and `shape`. *Decided against: warming actuary's learner from a profile*.
- **The grace period.** Half an hour's grace after losing a node or its neighbour, derived in `fold`, for a game started while you still had access (stamped at Start, `scores.campaign_start`). *Decided against: a cooldown between moves; an "able to capture" flag on its own*. *In the game*: a confirm before Start and a top-bar countdown, from `/api/campaign` running the same JS.
- **Campaign fleets (proposed, not built).** Real-time lanes on the meta-map: a launch locks a claim, so a neighbour stolen mid-game no longer voids it. Holders see inbound fleets, and one fleet per player paces the week. Collisions go to the better score. Identity is the open problem.

- **Rules in full** (detail kept out of `CLAUDE.md`): the game and the board are one site; crowns, the weekly campaign and embargoes; the bot column; a replay is never shown as if it still reproduced the game.
### design/par.md
- **Par search (a local check).** Why it is less random than it looks (bots are free to predict; two coupled sources of chance; a rewind resets the rng); the floor and its one inadmissible case; lucky versus honest dice (lucky is not a ceiling); the beam search; readings: it beat 5 of 6 recorded human wins, and on the live board (86 maps under 20 systems) lucky dice beat the best human score on 76 of 84; the rollout bot matters more than the beam; the `verify_scores` gap it exposed, whose dice half is closed for keyed turns.

### design/pbp.md
- **Context** and *Decisions taken up front*; **Constraints that shape the design**; **What it reuses**; **The one idea everything follows from** (the stored log is the record, so a turn is decided once); **Verified against a real deploy** and *the bug that made the digest worth having*; **Testing the backend**; **Opening a match**; **How a deadline works**; **Public matches and the lobby**; **What a poll costs** (briefs, the idle floor; *decided against: a slower steady cadence*); **Traps**.

### design/bots.md
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
### design/knower.md
- **Simultaneous resolution.** Why an oracle is possible at all.
- **What the oracle buys, and where.** A turn of warning, worth more the faster ships are. Depth 0 is not thinker.
- **Each Oracle setting against thinker and marshal.** Off / Predict / Search head to head in three cells. Only Search beats marshal; the ladder's 93% over thinker was a lucky 30 seeds.
- **Built, measured, removed.** Two ideas.
- **The depth search: branch the root, play the rest on.** Why branching deeper did nothing, and why more openings beat more turns. Most of the result against marshal comes from borrowing marshal.
- **Borrowed candidates (`EXTERNAL_CANDIDATES`).** Candidate win shares, contested decisions only, and why `SEARCH_WIDTH` is 2.
- **How far to look.** The horizon is the longest lane plus `LANE_CUSHION`, which replaced a fixed depth, so Oracle is now Off / Predict / Search. Also the old depth curve.
- **Cost per decide, and where the search guard trips.** The `ply_ms` fit and `setup_warning`, which also counts rival bots that declare `decide_ms`. The browser is unmeasured; never measure on a loaded machine.

### design/actuary.md
- **The ledger and the greedy.** A timeline per system, fights priced so only the verdict is worst-case, one value in ships, risk as an expected loss, greedy commits. It is not an oracle.
- **Cost, and the caches that make it affordable.** Version-stamped caches and the `_idle` prune, both checked exact. 1.6-3.9 ms a decide, and what that does to a knower Search seat.
- **Where actuary stands.** Four map cells, free-for-all, the combat sliders. Weak at 24 nodes 3 ly/turn and at jitter 0.3.
- **As one of knower's borrowed candidates (measured, not shipped).** 10.4% of contested picks, distinct from the default 91% of the time.
- **What the measurements changed.** Survivors and threats at the nominal roll; the constants' plateau; `FRONT_BONUS` 0.5.
- **The 2026-10 sweep: off the plateau.** 58,400 duels against marshal: `MIN_GAIN` 0 (+7.4 pooled, +18.6 at 24 nodes 3 ly/turn) and `FRONT_DECAY` 0.75 (+5.6) win in every cell; risk and reinforce weights confirmed. Reproduced on fresh seeds, where the two do not stack and a horizon cap of 8 does nothing. `MIN_GAIN` 0 costs 7-13% a decide at default sizes, 51% at 80 nodes. It also wins against thinker, knower-Predict, across the advantage sliders and in free-for-all; level-to-better against knower at Search. Shipped as `MIN_GAIN` 1e-9 (exactly 0 commits float noise; level with 0 over 12,000 games), with `decide_ms` refitted to `6.4 * (nodes/40)^0.77`.
- **Behind on income, ahead on ships (measured, not built).** A switch to recklessness while a ship lead runs out against an income gap. The standing is 7% of post-contact turns, mostly a 1-2 ship lead for 2 turns; a real lead on a short clock came up 4 times in 84 actuary games. Behind on both is where seats lose.
- **The planned opening (Opening: Planned).** The default `aux` stop: it plans the land-grab as a one-player puzzle until first contact (Greedy is the ledger throughout); the clock is about twice the earliest strike; a gain on 40 and 80 nodes against marshal and knower, noise below; a side too small to plan (under 7 systems held plus region) is left to the ledger; it costs marshal as a host; the deleted first attempt (rules about contested neutrals).

### design/learner.md
- **Why the oracle flag does not make memory safe.** Every caller that runs a bot's `decide` on copies, branches, isolated positions or rewound boards, and `load_models` wiping module state.
- **The memo tree.** A board is a node found by content; its parent is a stored board it provably follows (`_follows`). What a cold start costs.
- **What a turn shows** and **the model and the prior.** Fresh fleets, one-turn launches from the garrison residual, strike records by ratio, send share, guard, evacuation; the prior fitted from roster self-play.
- **The prediction check (`tools/learner_check.py`).** Four predictors (none, all, prior, learner), Brier and squared ship error; the two plan measures replaced before the first reading. Results in four cells on two seed ranges; what the model can tell apart (guard and evacuation, not the strike curve).
- **Sizing a strike to its target, and splitting the strike from the target.** Re-read under actuary's `MIN_GAIN` 1e-9. Sizing kept: ship error down in 5 of 8 cell-runs, most at 24 and 40 nodes. The split deleted. The all-in share separates the roster.
- **Predicting people (`--logs public`).** Posted human games scored offline. Memory beats the prior on people by 3.6% of Brier; knower's blind guess at a person, read as a forecast, is worse than assuming they hold, which backs `TRUST_HUMAN` off. What the model reads off people.
- **The person's habits (`tools/human_habits.py`).** Posted games against roster self-play. 2/3 of players' ships marks a won game, and players' income crosses earlier but less reliably. The overkill is the endgame. The person empties frontier systems with relief covered 45% of the time (winning actuary 22%); a replayed log rewrites `config`. Frontier losses per system-turn.
- **As a bot.** actuary's ledger on a board with the expected launches added; the Trust knob (Off = actuary exactly, Raise); against actuary Raise is level; first on the roster ladder, mostly because the oracle flag stops knower reading it (worth ~20 points against knower to any bot that claims it). 0.08 from actuary on contested positions, closer than any two roster bots.
- **Trusting a prediction (built, measured, deleted).** Thinning a source by its expected launch (Trust) and trusting confident calls once proven per rival (Sure). The model's calibration: confident calls are rare and not sharper late in a game.
- **The gate.** Still not passed: memory wins on whether a strike comes, and the ship-error failures left are mostly intervals that cross zero. *Open:* whether that is power (a gate decision), and a strike curve that tells the bots apart.
- **Joining actuary as a stop: the gate (written, not run).** Superseded by the next section. Raise against Off (both flagged, so the model alone) through `tools/sweep.py`: against stock actuary as a null control, marshal, and knower at Predict and Search, in actuary's five 2026-10 cells, fresh seeds from 6001. What passing means (a third stop on actuary's knob, above Planned) and failing (stays on the branch). Proposed improvements, each an extra arm: the prediction as the risk term's reach (Raise charges a threat twice), the strike and no-strike outcomes priced separately in place of an expected fleet, and evacuation and guard fed into the price of a capture.

### design/learning.md
- **Why the curve goes in the risk term**, **what was measured, and what failed first**, **the stop** and **what else it changed.** Built 2026-10-09 as actuary's third Style stop. Assuming a rival will not strike is close to right, and a launch cannot be recalled, so Raise (threat only) was the wrong use. Learning scales each rival garrison in actuary's risk by min(1, `LEARN_REACH` x the rival's strike curve at the ratio to the garrison we will hold). Raise, and Raise kept to unanswerable strikes: level with actuary. A flat reach scaled by `predict`'s call: better against marshal only. The curve read at the projected garrison: `LEARN_REACH` 8, plateau 8-13; on fresh seeds better against marshal (z +2.46, all at 18 systems), a lean against knower Off, level with actuary; against knower Predict and Search, with the oracle claim held equal (Planned flagged too), a lean of z +1.5 / +0.8, not significant; the rest of that row is the claim. Slow, fast and jitter 0.06: better against marshal in all three (z +2.0 to +3.0), better against actuary in the slow cell, nowhere worse; the free-for-all leans better (3 and 4 seats). 15-20% more per decide. The knob, the per-seat oracle claim, and the readings moved to `tools/learner_check.py`.
- **Evacuation in the price of a capture (built, measured, deleted).** learner's improvement 3's evacuation half as an arm of Learning: a doomed rival garrison that sees the strike leaves at its learned rate. Worse against marshal, knower Off and Planned on 4,800 paired games; it can only take credit away from a strike that already wins.
- **Strike and no-strike priced apart (built 2026-10-09, kept).** learner's improvement 2: each rival garrison next door strikes whole or not at all at its chance, and the risk is the expected shortfall, in place of a chance-weighted fleet that never flies. `LEARN_REACH` re-selected (8 of 4, 8, 16); on fresh seeds better than Learning against marshal (z +2.26), a lean against Planned (+1.64) and knower Off (+0.41); slow and fast speed cells, knower Predict and Search, and three free-for-all lineups no worse (knower Search leans worse, z -0.88). 18% of decisions changed, ~18% more per decide. Now Learning's risk term.
- **Memory off: the prior alone (built, measured, flag removed).** Every rival read at the prior strike curve, the oracle claim held equal. On 9,600 paired games (fresh seeds 9001-9400, 18 and 40 systems) Learning beats memory-off nowhere: level against marshal (z -0.49) and knower Off (z -0.80), worse against Planned (z -2.29). Learning's gain over Planned is the prior curve read at the projected garrison, not the memory. Whether to drop the memory, and with it the oracle claim, is left to the author.
- **Five more arms, and three combinations (built, measured, deleted).** All with memory on, 4,800 paired games each on the same seeds: the pressed row (leans better everywhere, z +1.6 / +0.5 / +1.5, does not pass); strike size at the prior's all-in split, the learned guard (learner's improvement 3's other half), strikes that move together, and `PRIOR_WEIGHT` 1 (null). Every arm that thins the threat is worse against marshal. Pressed + guard, + size, + joint: all worse.

### design/convoy.md
- **The plan.** Supply per system and turn, objectives as N ships by turn t, defences then guard reservations then strikes by value per ship, launch at the last moment.
- **Where convoy stands.** Five cells against the roster: above thinker everywhere, level with knower-Predict, below marshal and actuary.
- **How differently it plays.** Distinct in the opening, close to the phase bots when contested; few strikes converge from more than one source.
- **Its opening in front of another bot.** Costs in front of marshal, no gain over actuary's Planned; not borrowed.
- **Where it loses to actuary.** Level at contact, out-traded after it.
- **What the measurements changed.** The guard against the target, `STRIKE_PAD` 1.25, evacuation; the nulls and deleted tactics.

### design/marshal.md
- **What the measurements deleted.** The square-law case for overwhelming force. The guard interacts with commitment. Chokepoints lose. Two bugs. Standing aside in a free-for-all (`_wedge`, gated on player count).
- **Where marshal stands.** **The current full roster ladder; update this table, not the docstring.** Also the results against knower's oracle, across defender advantage, and the A/B against the old marshal.
- **A stagger's nearer wave is reserved.**
- **More ideas measured and deleted.**
- **`FRONTIER_GUARD`.** How it came to be 0.40 while documented as 0.3; now 0.55.
- **The 2026-09 tuning sweep.** What was adopted (partly superseded since), the regime-bound `ENEMY_FAR`, cross-opponent checks, negative results, the empty-interior statistic, and the two methodological notes.

### design/marshal-pricing.md
- **Racing a third player for the same system.** `_rival_waves` and `_after_clash`. The contested-neutral and remnant variants, why a high defender advantage makes waiting worse, and the harness traps.
- **Garrisons run away, so the jitter premium buys almost nothing.** 86.7% evacuate, so the attack margin is the advantage multiplier alone. Dropping the advantage half too is catastrophic.
- **Two doomed neighbours, and what a retreat is worth.** `AVOID_ABANDONED`, `CONSOLIDATE`, `RETREAT_NEAREST`. Budget n in the thousands.
- **Rushing the enemy.** marshal is already a rusher, and the target sort caps this whole channel at about 2 points.
- **A 0-ship neutral.** Real waste, bounded by simultaneity and by fleets already in flight. Null.
- **Holding a doomed system to sacrifice-and-retake.** A real mechanism, null result.
- **Re-tuned for production-before-combat and the garrison-fights-last pile-up.** `_attacker_pileup` and `RISK_PARITY`. The four null tactics don't combine, and the theory that Phase 3b masks them is false.
- **The attack side of the pile-up.** Shipped for correctness at a null result.

### design/marshal-flow.md
- **Phase 3b re-flooding an already-covered target.** Neutral-only at first, because of the regression at high advantage; rival sieges are now gated at `RIVAL_REFLOOD_MIN_TURNS = 5`. Symmetric maps can't measure it. How it overlaps `DENY_SWAP`.
- **A non-oracle successor to marshal.** A: `FLOW_AVOIDS_ABANDONED`. B: `RELIEF_AWARE`. C: `FAST_GUARD_WEIGHT`. Then the measurements (60.6% combined) and the `tools/sweep.py` smoke-gate quirk.
- **The empty interior is the retreat.** The concentrating probe (spearhead) loses. `DEEP_RELIEF` is inert.
- **Denying the swap.** Surplus-only, long lanes only (`DENY_SWAP_MIN_TURNS = 4`).
- **When a doomed garrison leaves.** `EVAC_AT` was deleted.
- **What the board's human wins say (2026-09-30).** The hold test on a capture was deleted. `FEED` was shipped, gated at `FEED_MAX_TURNS = 2`. *Open: no lone trickles into a rival (not yet measured).*

## Decided against

Each entry gives the idea, the outcome and where it is recorded. "Null" means
the measured result was indistinguishable from the baseline.

### knower ([`design/knower.md`](design/knower.md))
- Pricing three-way pile-ups with the oracle: a wash, possibly worse. *Built, measured, removed.*
- Striking perishable (vacating) targets first: no change. *Built, measured, removed.*
- Branching every ply of the search, cut per opening: replaced by root-only branching, which is even at a quarter of the cost. *The depth search.*
- Rolling every line on until its fleets land: lost head-to-head. *The depth search.*
- The all-in and push-for-depth postures inside the search width: stopped paying once real rushers were candidates. *Borrowed candidates.*
- Any fixed search depth: replaced by longest lane + 5. Depth past 5 was noise. *How far to look.*

### marshal: [`design/marshal.md`](design/marshal.md)
- Baiting a rival into a relievable system, and the offensive half of "stand aside": needs a model of rivals, which is knower's territory. *What the measurements deleted.*
- Chokepoint value (betweenness): loses at every weight. Pocket-sealing: a constant offset. *Same section.*
- `FRONTIER_GUARD = 0` (ablated alone): wrong, because of its interaction with Phase 3b. *Same section.*
- Reinforceability-scaled guards: 46%. Splitting a breakthrough's surplus: null. Dropping the guard against a neighbour our fleets take this turn: 48.4-49.5%, since a capture is often retaken and the guard is what holds it. *More ideas measured and deleted.*
- A flat garrison floor (`RESERVE_FLOOR` 1 or 2): catastrophic. Opening the wedge gate at 3 players: a wash. Raising `RIVAL_WEDGE`: on its plateau. Propagating threat through neutral buffers: 26.7% in slow cells. *The 2026-09 tuning sweep.*
- `ENEMY_NEAR`/`ENEMY_FAR`/`NEAR_PAD` and the distance ramp: removed later (see *Garrisons run away*).

### marshal: [`design/marshal-pricing.md`](design/marshal-pricing.md)
- Repricing a contested neutral against the rival's survivor: worse in duels, because the price is a gate. *Racing a third player.*
- Arriving after a rival breaks a neutral (the remnant tactic): null, and worse at high advantage. Distinguishing same-turn blocs, or dropping them: a wash. *Same section.*
- Dropping the advantage half of the attack edge as well as the jitter half: 8.6% at advantage 1.5. *Garrisons run away.*
- A bonus for enemy-held targets, or rushing the enemy outright: null, and the strong form is worse. *Rushing the enemy.*
- Repricing 0-ship neutrals: null. *A 0-ship neutral.*
- Holding a doomed garrison to sacrifice and retake: null. *Holding a doomed system.*
- The four null tactics combined, or with `COMMIT_SURPLUS` off: still null; the masking theory is false. *Re-tuned for production-before-combat.*

### marshal: [`design/marshal-flow.md`](design/marshal-flow.md)
- Gating Phase 3b re-flooding of rival targets at every lane length: regressed at high advantage. Kept for lanes of 5+ turns only. *Phase 3b re-flooding.*
- Concentrating on one hammer stack (spearhead): 23-42%. `DEEP_RELIEF`: inert. *The empty interior is the retreat.*
- `DENY_SWAP` on Phase 3's priced strike: a disaster. Shipped on the surplus only. *Denying the swap.*
- Evacuating at the last moment (`EVAC_AT`): did not replicate. *When a doomed garrison leaves.*
- A hold test on captures: worse the harder it bites. `FEED_LOCAL` and `FEED_MAX_TURNS = 3`: deleted. *What the board's human wins say.*
- **Open, not rejected:** porting `_evacuate`'s defensive fixes to thinker and knower (unmeasured; see *Two doomed neighbours*), and no lone trickles into a rival (unmeasured).

### actuary ([`design/actuary.md`](design/actuary.md))
- Pricing a fight's survivors at the worst roll as well as its verdict: refused every capture at jitter 0.3 (0 of 40 against marshal). *What the measurements changed.*
- Pricing threats at the worst roll: hoarded garrisons; worse in both cells measured. *Same section.*
- Shipping it as a knower `EXTERNAL_CANDIDATES` entry: not done (cost on every Search root, effect on knower unmeasured), not rejected. *As one of knower's borrowed candidates.*
- Rules about contested neutrals (land after a rival racing for one, veto an unholdable capture and guard beside it, veto a capture that does not repay before the earliest possible arrival): worse than Greedy in every cell. *The first attempt.*
- A planned opening reaching 2 turns past the halfway line: worse at every size. *The clock is about twice the earliest strike.*
- The planned opening in front of marshal: worse in every cell. *In front of marshal it costs.*
- Handing the opening over 2 or 3 lanes from a rival: fixes small maps, gives back the 40-80 node gain. A side floor of 9-16: costs a little at 24-80 nodes. *A side too small to plan.*
- Trying several `aux` values per map in the bot column and keeping the best: mostly picks the luckier dice (one board ranges 13-165 turns on dice alone); fix the bot instead. *Same section.*
- `RISK_WEIGHT` 0.3 / 1.0, `MIN_GAIN` 0.5, `FRONT_DECAY` 0.25, `TAIL_TURNS` 14, `REINFORCE_WEIGHT` 0: all worse pooled against marshal. *The 2026-10 sweep.*
- **Open, not rejected:** clock 3 for the slow regime (+1.5 there in the 2026-10 sweep, not significant). Planned trailing Greedy a little with two seats on 10-18 nodes. *A side too small to plan.*
- Pricing strikes at the nominal roll and dropping the risk weight while behind on income but ahead on ships: not built, since a real lead on a short clock arises 4 times in 84 games (2 across the posted human games) and a real lead is already converted 87% of the time. Variance-seeking while behind on both: unmeasured. *Behind on income, ahead on ships.*
- `MIN_GAIN` 0 with `FRONT_DECAY` 0.75 or 0.85: no better than `MIN_GAIN` 0 alone. `HORIZON_MAX` 8: inert outside the slow cell, null in it. *The 2026-10 sweep.*

### learner ([`design/learner.md`](design/learner.md))
- Thinning a rival's source by its expected launch (Trust): 44% against actuary, worse than Raise; a source sends nearly all or nothing, so an expected value is the wrong thing to deduct. *Trusting a prediction.*
- Trusting confident calls (p 0.5+) against a rival once 70% have come true (Sure): indistinguishable from Raise; the gate stays shut against actuary and opening it against marshal buys nothing. *Same section.*
- Splitting a strike into "does this source strike at all" and "which target": behind the per-target curve on strike chance in all 8 cell-runs, its prior too; did not separate the bots. Deleted. *Sizing a strike to its target, and splitting the strike from the target.*

### Learning ([`design/learning.md`](design/learning.md))
- Raise (each predicted strike added as a fleet), with or without keeping only the strikes we could not answer after seeing them: level with actuary; a prediction that can only add threat only adds caution. Its file was deleted when the model joined actuary. *What was measured, and what failed first.*
- Scaling actuary's risk by `predict`'s call at today's garrison (flat K, 1-64): better against marshal, worse against knower Off and actuary; relaxing thins the garrison the call was made at. A per-rival calibration correction points the wrong way (knower's low calls strike less than predicted). *Same section.*
- A rival garrison next door counted at its chance-weighted share (n x min(1, `LEARN_REACH` x p)), Learning as first built: replaced by pricing a whole strike at its chance, better against marshal (z +2.26). *Strike and no-strike priced apart.*
- Evacuation in the price of a capture (a doomed rival garrison leaves at its learned rate, so the strike kills fewer and the evacuees threaten the capture): worse than Learning against marshal (z -4.2), knower Off (z -2.3) and Planned (z -3.0). Conditioned on a doomed garrison, it can only make strikes worth less, never cheaper. *Evacuation in the price of a capture.*
- Learning's memory of each rival, as against the prior curve alone: never better (9,600 paired games), worse against Planned (z -2.29). The flag is removed and the memory still ships, pending the author's call on the oracle claim. *Memory off: the prior alone.*
- In Learning's risk: a rival garrison's learned guard kept home (z -2.08 against marshal), a strike all-in or sized at the prior's split (-2.20, and -2.96 against Planned), a rival's garrisons next door striking together (-2.50), `PRIOR_WEIGHT` 1 (null), and pressed with guard, with guard and size, and with guard and joint (all worse). The pressed row alone leans better, but not significantly. *Five more arms, and three combinations.*

### convoy ([`design/convoy.md`](design/convoy.md))
- Keeping the guard against the struck neighbour unless the lane is one turn: 23-37% against waiving it, more timeouts. *What the measurements changed.*
- `STRIKE_PAD` 1.5 and 2.0: worse, 2.0 catastrophic. A doomed garrison staying to fight: 38%. *Same section.*
- Filling short frontier guards from the interior, staging ships at the last system before the target, spending existing ships before future hulls: all null, deleted. *Same section.*
- Defence at the nominal roll: a lean (54.8%, z ~1.5), not adopted; worth re-running. *Same section.*
- Shipping convoy as a roster bot: between thinker and marshal, distinct only in the opening. Removed from `models/`; the code is at commit `7153065`. *Built, measured, not shipped* (the file's opening paragraph).
- Borrowing convoy's opening for another bot, as surveyor's was for actuary: 32-45% in front of marshal, 47% pooled against actuary's Planned opening. *Its opening in front of another bot.*

### The roster ([`design/bots.md`](design/bots.md))
- A visual rule-based bot maker in the app: works, but tops out below thinker and serves almost nobody. Branch kept, not merged. *The in-app bot maker.*
- A `simulate` callback or oracle parity for external bots: ruled out, and rival strategy names are masked. *Bots that aren't Python.*
- Raw-count attack margins: the AI stops expanding. A defender-advantage ceiling of 2.0: hard stalemates. *Defender advantage and the AI.*
- Capping the break-even edge to win back high-jitter results: that would reintroduce the bug. *Break-even margins.*
- A shallower search for the offline bot column instead of a bigger budget: not reproducible. *Replaying a bot for the leaderboard.*
- Leaving the replayed seat flagged human to match the Watch link: a handicap. Superseded by stored replays. *Same section.*

### Core ([`design/core.md`](design/core.md))
- A `models/` dispatcher bot for the random seat: breaks the oracle contract. Drawing the pick from `state.rng`: would shift the dice. *A seat left to the seed.*
- Dealing random seats from the loaded roster: re-deals every random map whenever a bot is added, and differs between builds. A roster fingerprint in the challenge key: splits the board on every new bot. *A seat left to the seed.*
- Hashing only non-default fields for the challenge key: aliases old keys onto a different balance. Enumerating int/float `aux` subsets: unbounded. *Keys outlive the schema.*
- Challenge links in the address bar or `localStorage`: no. Locking a challenge's widgets: no. *Challenge links.*
- Re-timing fleets in flight under growth, linear growth, rescaling the baked `travel_turns`: all rejected. *Ship-speed growth.*
- Pooled lane battles and merging the survivors: produced wrong results and visible teleports. *In-lane battles.*
- Replaying by re-running the AI (format v1), re-running `decide` just to advance the rng, and snapshotting the Mersenne Twister state per turn: all rejected. Deriving the dice from `(seed, turn)` was rejected too, then built per fight on 2026-10-08 (the dice are still recorded). *Persistence, replay & history.*
- Reading star names from a data file at runtime: no, they are generated. *Star names.*

### Shell ([`design/shell.md`](design/shell.md))
- Printing the "subtract the fleets" answer for contrast; the survivor curve (replaced by the jitter matrix); five swings per axis; runtime prose wrap on the menu: all rejected. *Combat rules page.*
- A "keep N / send N%" switch on each forwarding rule: "keep N" means "send the rest", so only one lane per system could use it; replaced by a hold per system and a share per lane. Showing the hold on each lane's label: crowded a split's source. Disabling + once the shares reach 100%: dead in the default state. *Forwarding rules.*
- Rally tie-break by ships/turn toward the rally point drawing less: built, then replaced by an even split once rules could split. *Route mode.*
- A side-panel list of every queued order and rule (capped, scrolled, a × per row): built and removed once the popup opened on edit too. *Send popup and spectating.*
- Unrestricted (enemy-crossing) routes: unrepresentable. Two-stage pick-then-aim; "tap a pick to remove, anything else to aim"; a Chain/Rally button pair; rally claiming only systems nearer than a front; weighting the AI's flow by turns: all rejected. *Route mode.*

### Turn playback ([`design/turnfilm.md`](design/turnfilm.md))
- Pausing the engine between phases; an applier that re-derives outcomes; widening the crossing sort tuple (it would move stored replays); per-beat dwells summed into the film; `FILM_LAUNCH_MS` 220; flat-expiry marks, then marks fading over the film's length; an in-game animation toggle; a loss label summing both sides (or a per-side pair); lane tracks ranked by position or derived from launch turn; camera moves toward the action; a burst on every arrival: all rejected.

### Hand-drawn maps ([`design/hand-maps.md`](design/hand-maps.md))
- A map file referenced by name; a live `GameState` as the editor's model; clamping a drag to the nearest legal point; Auto-lanes merging into hand-drawn lanes; a crossing as a blocker; silent seat compaction; a palette press that only selects; relaning on every drag frame; widening `WORLD_SIZE`; scaling an adopted map to fill the box; opening on a generated board; a deselect gesture in Systems: all rejected.

### Leaderboard ([`design/leaderboard.md`](design/leaderboard.md))
- An always-on service for the bot column; honouring slot 0's `AiParams`; ranking losses by turns; a Watch link that re-decides the match live; a git SHA as `engine_rev`: all rejected. *Bot replays.*
- A JS replay viewer; a durable client id; a durable IP-based rate limit; a per-model replay floor; sealing forks of a watched replay: all rejected. A full board snapshot per turn: **set aside for now, not ruled out.** *Checked scores.*
- Warming actuary's learner (Style: Learning) from a stored per-player profile: dropped unbuilt. The strike curve it plays from reads the same for every bot and the person, and `decide` would come to depend on more than the board. The profile became the player page's Playstyle panel instead. *Playstyle readings.*
- A one-hour cooldown between campaign moves, with wins posted during it queued: built and removed, since two clocks side by side ("post within 12 min", "plays in 40") read as nonsense. *The grace period*.
- An "able to capture" flag in a campaign game's link, honoured on its own: rejected, since a kept link is a standing permit. Built instead as a stamp that only narrows the grace. *The grace period*.
- A play-by-post duel to settle two campaign fleets meeting at one node: set aside for the best-score rule, since the duel is a different game on a different map and needs both players to turn up. *Campaign fleets (proposed, not built).*

### Par search ([`design/par.md`](design/par.md))
- A par or floor per map on the board: decided against; the tool stays local. *Not on the board.*
- Lucky dice as a ceiling: withdrawn, since rival fights still roll and honest dice beat lucky in 2 of 18 pairs. *Dice modes.*

### Play-by-post ([`design/pbp.md`](design/pbp.md))
- Every client re-running every bot from the stored orders: forked matches. Replaced by "the stored log is the record". Enforced fog: not attempted; fog is convenience. Resolving a turn on a clock: never. *The one idea everything follows from*, *Traps*.
