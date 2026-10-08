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
| [`design/shell.md`](design/shell.md) | Text sizing, touch targets, viewport margins, the send popup, the menu's Combat page, spectating, route mode |
| [`design/turnfilm.md`](design/turnfilm.md) | The animated end of turn (`turnfilm.py` and the shell side of playback) |
| [`design/hand-maps.md`](design/hand-maps.md) | The hand-drawn map recipe and the map creator |
| [`design/leaderboard.md`](design/leaderboard.md) | The offline bot column, checked scores, watching a replay, replay versioning |
| [`design/par.md`](design/par.md) | The par search, a local check: the floor, lucky and honest dice, what a rewind does to the rng, readings against human wins |
| [`design/pbp.md`](design/pbp.md) | Play-by-post |
| [`design/bots.md`](design/bots.md) | The roster as a whole: measurement method, the parameter space, real-game positions, replaying bots for the board, break-even margins, defender advantage, the bot maker, non-Python bots |
| [`design/knower.md`](design/knower.md) | knower: the oracle, the search, its horizon, its cost |
| [`design/marshal.md`](design/marshal.md) | marshal: what it was built on, **the current roster ladder**, the 2026-09 constant sweep |
| [`design/marshal-pricing.md`](design/marshal-pricing.md) | marshal: what a strike or a defence is priced against |
| [`design/marshal-flow.md`](design/marshal-flow.md) | marshal: where the surplus goes, plus the successor fixes and FEED |
| [`design/actuary.md`](design/actuary.md) | actuary: the projected ledger instead of phases, its cost, where it stands, what the measurements changed; the planned opening (Opening: Planned) |
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
- **Persistence, replay & history.** Format v2 records orders plus dice, and the alternatives that lost. Rules live in the log. The `"ai"` flag doubles as the seat claim. A resume always lands paused. A live turn's rng is derived (`replay.reseed`), so a rewind is not a re-roll.
  - *`match_id`.*
- **Star names.** Generated at build time; named last so seeds don't move. Labels are placed collision-first, with a reserved slot for marks.
- **Symmetric layouts** (under "Past a standard board the box grows"). Hub, ring, wheel and core; why `hub` is pinned rather than a `RULES_VERSION` bump; added lanes chosen on one seam and rotated; why `core` opens the middle up; where `layout` is inert.

- **Rules in full** (detail kept out of `CLAUDE.md`): in-lane battles; pile-up resolution; replay and history; an all-bot game has no human seat; whole-number floats don't survive the browser; past a standard board the box grows; settings, challenge links and keys; star names.
### design/shell.md
- **Text sizing** and **`config.touch_ui`.** Measured layout. The send popup is the one place the tap floor gives way.
- **Map viewport margins.** The `node_clearance` floor, and clamping against the fit-padded span.
- **Send popup / `Ui.editing_existing`.** Unwinding to IDLE on an edit; the slider is hit-tested before the panel drag.
- **Combat rules page.** It teaches the square law from the real code (`preview_fight`): parameters rather than `config`, corners rather than samples, demo sliders on `MenuState`, hand-broken prose, the jitter matrix.
- **Losing / spectator mode.** Keyed on defeat, plus how the reset view frames the map.
- **Route mode.** A proposal plus a confirm. Owned-only paths are forced. A plan can't contradict itself, but it can loop with existing rules.
  - *Two sub-modes.* Chain and rally share `flow_field`. Distance is travel turns, not hops. One Mode button. Ties balance by ships/turn. Auto-route. A tap always aims (two rejected shapes). `keep` handling.

- **Rules in full** (detail kept out of `CLAUDE.md`): measured layout and `touch_ui`; browser bridges (`softkeyboard`, `webstore`, `share`, quitting); send popup, queued list and spectating; viewport margins; the Combat tab; route mode.
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
  - *Through `build_state`*; *what the replayed seat is tuned to* (`REPLAY_AUX`); *a loss is a result, not a score*; *a win stores its own replay*; *`engine_rev` hashes the simulation*; *the one table the public cannot write*.
- **Checked scores.** The id rides on `Challenge`. Two consented senders. `game_logs` is private. No identity on a row. `is_current` and `rules_version`. The verifier binds a log to its setup.
  - *Watching one back* (a watched result is not ours to post); *versioning: bots are free to move, the engine is not*.
- **The grace period.** Half an hour's grace after losing a node or its neighbour, derived in `fold`, for a game started while you still had access (stamped at Start, `scores.campaign_start`). *Decided against: a cooldown between moves; an "able to capture" flag on its own*. *In the game*: a confirm before Start and a top-bar countdown, from `/api/campaign` running the same JS.
- **Campaign fleets (proposed, not built).** Real-time lanes on the meta-map: a launch locks a claim, so a neighbour stolen mid-game no longer voids it. Holders see inbound fleets, and one fleet per player paces the week. Collisions go to the better score. Identity is the open problem.

- **Rules in full** (detail kept out of `CLAUDE.md`): the game and the board are one site; crowns, the weekly campaign and embargoes; the bot column; a replay is never shown as if it still reproduced the game.
### design/par.md
- **Par search (a local check).** Why it is less random than it looks (bots are free to predict; two coupled sources of chance; a rewind resets the rng); the floor and its one inadmissible case; lucky versus honest dice (lucky is not a ceiling); the beam search; readings: it beat 5 of 6 recorded human wins, and on the live board (86 maps under 20 systems) lucky dice beat the best human score on 76 of 84; the rollout bot matters more than the beam; the `verify_scores` gap it exposes.

### design/pbp.md
- **Context** and *Decisions taken up front*; **Constraints that shape the design**; **What it reuses**; **The one idea everything follows from** (the stored log is the record, so a turn is decided once); **Verified against a real deploy** and *the bug that made the digest worth having*; **Testing the backend**; **Opening a match**; **How a deadline works**; **Public matches and the lobby**; **What a poll costs** (briefs, the idle floor; *decided against: a slower steady cadence*); **Traps**.

### design/bots.md
- **Measuring a bot: what earlier sweeps got wrong.** The method checklist, with a pointer to the evidence for each item.
- **How differently two bots play (`tools/bot_distance.py`).** Distance, kappa and fingerprints on shared positions, split into contested and opening; why it replaces the candidate share as the first check; the first reading.
- **Lane length across the parameter space.** Node count and ship speed move lane length over more than an order of magnitude, so a constant keyed off travel time is live in one regime of three.
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
- **The 2026-10 sweep: off the plateau.** 58,400 duels against marshal: `MIN_GAIN` 0 (+7.4 pooled, +18.6 at 24 nodes 3 ly/turn) and `FRONT_DECAY` 0.75 (+5.6) win in every cell; risk and reinforce weights confirmed. Reproduced on fresh seeds, where the two do not stack and a horizon cap of 8 does nothing. `MIN_GAIN` 0 costs 7-13% a decide at default sizes, 51% at 80 nodes. It also wins against thinker, knower-Predict, across the advantage sliders and in free-for-all; level-to-better against knower at Search. Shipped, with `decide_ms` refitted to `6.4 * (nodes/40)^0.77`.
- **The planned opening (Opening: Planned).** The default `aux` stop: it plans the land-grab as a one-player puzzle until first contact (Greedy is the ledger throughout); the clock is about twice the earliest strike; a gain on 40 and 80 nodes against marshal and knower, noise below; a side too small to plan (under 7 systems held plus region) is left to the ledger; it costs marshal as a host; the deleted first attempt (rules about contested neutrals).

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
- `MIN_GAIN` 0 with `FRONT_DECAY` 0.75 or 0.85: no better than `MIN_GAIN` 0 alone. `HORIZON_MAX` 8: inert outside the slow cell, null in it. *The 2026-10 sweep.*

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
- Replaying by re-running the AI (format v1), re-running `decide` just to advance the rng, snapshotting the Mersenne Twister state per turn, and deriving the dice from `(seed, turn)`: all rejected. *Persistence, replay & history.*
- Reading star names from a data file at runtime: no, they are generated. *Star names.*

### Shell ([`design/shell.md`](design/shell.md))
- Printing the "subtract the fleets" answer for contrast; the survivor curve (replaced by the jitter matrix); five swings per axis; runtime prose wrap on the menu: all rejected. *Combat rules page.*
- Unrestricted (enemy-crossing) routes: unrepresentable. Two-stage pick-then-aim; "tap a pick to remove, anything else to aim"; a Chain/Rally button pair; rally claiming only systems nearer than a front; weighting the AI's flow by turns: all rejected. *Route mode.*

### Turn playback ([`design/turnfilm.md`](design/turnfilm.md))
- Pausing the engine between phases; an applier that re-derives outcomes; widening the crossing sort tuple (it would move stored replays); per-beat dwells summed into the film; `FILM_LAUNCH_MS` 220; flat-expiry marks, then marks fading over the film's length; an in-game animation toggle; a loss label summing both sides (or a per-side pair); lane tracks ranked by position or derived from launch turn; camera moves toward the action; a burst on every arrival: all rejected.

### Hand-drawn maps ([`design/hand-maps.md`](design/hand-maps.md))
- A map file referenced by name; a live `GameState` as the editor's model; clamping a drag to the nearest legal point; Auto-lanes merging into hand-drawn lanes; a crossing as a blocker; silent seat compaction; a palette press that only selects; relaning on every drag frame; widening `WORLD_SIZE`; scaling an adopted map to fill the box; opening on a generated board; a deselect gesture in Systems: all rejected.

### Leaderboard ([`design/leaderboard.md`](design/leaderboard.md))
- An always-on service for the bot column; honouring slot 0's `AiParams`; ranking losses by turns; a Watch link that re-decides the match live; a git SHA as `engine_rev`: all rejected. *Bot replays.*
- A JS replay viewer; a durable client id; a durable IP-based rate limit; a per-model replay floor; sealing forks of a watched replay: all rejected. A full board snapshot per turn: **set aside for now, not ruled out.** *Checked scores.*
- A one-hour cooldown between campaign moves, with wins posted during it queued: built and removed, since two clocks side by side ("post within 12 min", "plays in 40") read as nonsense. *The grace period*.
- An "able to capture" flag in a campaign game's link, honoured on its own: rejected, since a kept link is a standing permit. Built instead as a stamp that only narrows the grace. *The grace period*.
- A play-by-post duel to settle two campaign fleets meeting at one node: set aside for the best-score rule, since the duel is a different game on a different map and needs both players to turn up. *Campaign fleets (proposed, not built).*

### Par search ([`design/par.md`](design/par.md))
- A par or floor per map on the board: decided against; the tool stays local. *Not on the board.*
- Lucky dice as a ceiling: withdrawn, since rival fights still roll and honest dice beat lucky in 2 of 18 pairs. *Dice modes.*

### Play-by-post ([`design/pbp.md`](design/pbp.md))
- Every client re-running every bot from the stored orders: forked matches. Replaced by "the stored log is the record". Enforced fog: not attempted; fog is convenience. Resolving a turn on a clock: never. *The one idea everything follows from*, *Traps*.
