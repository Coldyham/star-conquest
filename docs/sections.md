# Sections, by file

One line per design-file section: the file, the heading (bold, shortened where
long) and what it covers or concluded. **Every line names its file**, so a grep
hit alone says where to read: `grep -in 'FEED' docs/sections.md`, then grep that
heading in that file and read from there. Numbers stay in the design files; a
line gives only the verdict. Ideas that lost are in
[`decided-against.md`](decided-against.md), work still open in
[`open.md`](open.md), and the file index is [`README.md`](README.md).

## rules.md: every CLAUDE.md rule in full
- rules.md · One section per `CLAUDE.md` heading (Architecture, Turn resolution, Animated end of turn, Persistence, Play-by-post, Key conventions, Map generation, Hand-authored maps, Gauntlets), each rule in full with the functions and tests it names.

## core.md
- core.md · **Settings tokens.** Pruned, then deflated; mode, players, nodes and seed always emitted.
- core.md · **A seat left to the seed.** `random` resolves in `build_state`, not a dispatcher bot (the oracle contract); the pick is derived; `RANDOM_POOLS` append-only by era (`POOL_ERA`); why the seed, not a `Settings` field; the win overlay reveals it.
- core.md · **Challenge links.** Score and tie rules; clipboard only; editing a challenge asks first.
- core.md · *Keys outlive the schema that made them.* `_LEGACY_KEY_DROPS`, `findTwin` folding, newest key wins, `sc_config_key`, the int/float `aux` digest leak.
- core.md · **Ship-speed growth.** At launch only, compounding; re-times from `length_ly`.
- core.md · **In-lane battles.** A geometric meeting fought pairwise; the winner only thinned; growth mostly switches it off.
- core.md · **Persistence, replay & history.** Log format v2 (orders plus dice) and what lost; the `"ai"` flag as seat claim; a resume lands paused; `replay.reseed`, so a rewind is not a re-roll.
- core.md · *Two streams.* Bots decide in parallel from the turn's start (`engine.decide_seat`); each fight rolls dice keyed to its place (`engine._Dice`); the `keyed` marker; why knower seeing the real dice is fine. *`match_id`.*
- core.md · **Star names.** Generated at build time, stamped last; collision-first labels; `starlore` and the info card.
- core.md · **Symmetric layouts** (under "Past a standard board the box grows"). Hub, ring, wheel, core; why `hub` is pinned rather than a `RULES_VERSION` bump; where `layout` is inert.
- core.md · **Rules in full:** in-lane battles; pile-up resolution; replay and history; an all-bot game has no human seat; whole-number floats don't survive the browser; past a standard board the box grows; settings, links and keys; star names.

## shell.md
- shell.md · **Text sizing** and **`config.touch_ui`.** Measured layout; the send popup is where the tap floor gives way.
- shell.md · **Phones: the compact layout (`config.compact`).** CSS-pixel scale on a touch browser, shrunk to leave a phone 600x480 units; footer folds into a list; popup docks; portrait panel under the map; the menu's own canvas and scrolling panel, and a map sketch under the title (landscape) or panel (portrait), hidden under fog; a closed send finishes its source (every screen), pinch to zoom, no text selection.
- shell.md · **Map viewport margins.** The `node_clearance` floor; clamping against the fit-padded span.
- shell.md · **Send popup / `Ui.editing_existing`.** Unwinding to IDLE on an edit; slider hit-tested before the panel drag.
- shell.md · **Forwarding rules.** A hold per system, a share per lane; adding a lane splits evenly; one rounding formula; the popup's three shapes; the hold badge.
- shell.md · **Combat rules page.** Teaches the square law from `preview_fight`: parameters not `config`, demo sliders on `MenuState`, the jitter matrix.
- shell.md · **Losing / spectator mode.** Keyed on defeat; how the reset view frames the map.
- shell.md · **Ironman and the ships-lost readout.** Ironman hides History until decided (`Ui.can_review`); `ships_lost` is the tie-break.
- shell.md · **End-of-game graphs (`stats`, `render._draw_history_graph`).** Systems, ships, income and losses per seat from the history boards; legend toggles; rivals only once revealed.
- shell.md · **Naming each seat's bot (`Ui.bot_label`).** Tooltip and legend name bot and stop; a random seat reads "Random" until decided.
- shell.md · **Route mode.** A proposal plus a confirm; owned-only paths. *Two sub-modes*: chain and rally share `flow_field`; distance in travel turns; auto-route; a plan keeps a system's hold.
- shell.md · **The web build's download.** `tools/slim_web.py` strips comments/docstrings (lines kept) and subsets the fonts: archive 938 KB to 427 KB; `sw.js` caches pygbag's CDN runtime. *Not done*: `.pyc`, lazy imports.
- shell.md · **Rules in full:** measured layout and `touch_ui`; the compact layout; browser bridges (`softkeyboard`, `webstore`, `share`, quitting); send popup and spectating; viewport margins; the Combat tab; route mode.

## turnfilm.md
- turnfilm.md · **Animated end of turn**, organised by bold lead sentences: why it exists; it plays back the past; events carry results; why its own module; the crossing sort key; the launch tick; production as a mark.
- turnfilm.md · (same section, continued) combat's two speeds (`linger`); marks outlive their film; fog as the union of both turns; the deferred camera snap; pause; chaining turns; `_primed`; the frame cap.
- turnfilm.md · (same section, continued) the camera and autoplay exemptions from "any press skips"; the loss label; the shared mark slot; scrubber timing; no in-game toggle; the rim clamp; stored lane tracks; what is not animated.
- turnfilm.md · **The rules in one place:** the full list behind `CLAUDE.md`'s short version.

## hand-maps.md
- hand-maps.md · **Hand-authored maps**, one subsection each: the recipe on `Settings` (and its `_LEGACY_KEY_DROPS` cost); a recipe, not a `GameState`, as the editor's model; geometric rules as hard blocks; `CUSTOM_MAX_NODES` vs `MAX_NODES`.
- hand-maps.md · (same section, continued) Auto-lanes replaces rather than merges; a crossing warns, a graze blocks; two gestures, one commit path; no deselect in Systems; seat rows as one control; auto-relane skips a drag.
- hand-maps.md · (same section, continued) seats gap-free without forced renumbering; what the menu does with a hand map; the wider box; what the editor opens onto.
- hand-maps.md · **The rules in one place:** the full list behind `CLAUDE.md`'s short version.

## gauntlet.md
- gauntlet.md · **The tutorial ladder.** Data, never `Settings`; progress from `webstore.best`; `menu.active_stage`; the stash and Leave tutorial; `gauntlet.next_stage`; the briefing card and `HINT_MAX`.
- gauntlet.md · *The stages:* the ten, and how their seeds were picked (`tools/gauntlet_check.py`).
- gauntlet.md · **The file format.** `stages`/`lanes`/`start`, one-way lanes unless `two_way`, reachability (`gauntlet.problems`, `checkBody`), text limits, user gauntlets.
- gauntlet.md · **Gauntlets made on the board.** `/api/gauntlet`, its own table never `campaigns`, the `<slug>-<hash>` id, one `checkBody` run twice, `gauntlet.remember`, `#gauntlet=`, remixing, admin listing.
- gauntlet.md · **Relation to the weekly campaign.** Uncontested, so no server; graph and bordering rule shared in spirit; gauntlet lanes run one way.
- gauntlet.md · **Generated gauntlets.** A spec (`fix`/`range`/`in`/`not`, opponents, `roll`) dealt by `dealer.deal`, probed and picked by `tools/gauntlet_gen.py`; where it runs; the first run; the last stage may be unwon by every probe.
- gauntlet.md · *In the game / on the board:* `#gauntlet-spec=`, a slice per menu frame, budgets not lifted; the generate form, `gauntlet-spec.mjs`; sharing the file, not spec and seed.
- gauntlet.md · *Dealing fewer candidates* (shipped 2026-10-24): round-robin depths; four probes (lean heuristic, thinker, `evolved:0`, marshal); `tools/gauntlet_pool_bench.py`. The web build is untimed.
- gauntlet.md · **The gauntlet: proposed, not built.** A seeded run graph (`tools/campaign.py` `_layout`/`cut_nodes`), stages dealt like `_from_family`/`_mystery`, difficulty by `LADDER_ORDER`.
- gauntlet.md · **Carry-over.** Boons via `custommap.from_state` and their cost on the board; decided against for stage targets.
- gauntlet.md · **Stage targets.** A score to win within before later stages open (`gauntlet.Target`, `gauntlet.cleared`, `Ui.stage_cleared`); set from a pasted challenge link or a dealt run's probe wins; none in the tutorial.
- gauntlet.md · *Beat the best bot:* `standings.bestBot` on the stage's setup, fixed as `{turns, lost, bot, beat}` when picked. *Later: targets on lanes* (not built).

## leaderboard.md
- leaderboard.md · **Bot replays (`tools/bot_replay.py`).** A scheduled job, not a service. *Through `build_state`*; *what the replayed seat is tuned to* (`replay_aux`); *a loss is a result, not a score*; *a win stores its own replay*.
- leaderboard.md · (same section) *`engine_rev` hashes the simulation*; *the one table the public cannot write*; *estimated difficulty* (share of bots that lose a map; the Easy-to-Very-hard badge).
- leaderboard.md · **Checked scores.** The id rides on `Challenge`; two consented senders; `game_logs` private; `is_current`, `rules_version`; the verifier re-rolls keyed turns' dice (`mismatch`), unkeyed pass unchecked.
- leaderboard.md · *Watching one back* (a watched result is not ours to post); *versioning: bots are free to move, the engine is not*.
- leaderboard.md · **Playstyle readings.** From `public_replays` only, one row per match, never per name; fields sum; `playstyle-baseline.mjs` generated; `rev` and `shape`. *Decided against: warming actuary's learner from a profile.*
- leaderboard.md · **The game and the board are one site.** Why merge (deploy quota, one localStorage, the PWA); `/board/`. *The pages are templates*: Jinja2 via `tools/render_board.py`, one base.
- leaderboard.md · **Rules in full:** the bot column; a replay is never shown as if it still reproduced the game.

## campaign.md (split from leaderboard.md)
- campaign.md · **Crowns, the weekly campaign and embargoes** (rules in full). `counted_scores`, `crown_holders`, `crown_steals`; the 24-hour unverified grace; the campaign map, `fold`, `attemptStatus`; embargoes.
- campaign.md · **The grace period.** Half an hour after losing a node or its neighbour, derived in `fold`, for a game started with access (`scores.campaign_start`). *In the game*: confirm before Start, a top-bar countdown.
- campaign.md · *Decided against:* an "able to capture" flag on its own; a cooldown between moves.
- campaign.md · **Campaign fleets (decided against).** Real-time lanes on the meta-map: a launch locks a claim; holders see inbound fleets; collisions go to the better score; lane length; identity. The grace period answers it.

## daily.md (built 2026-10-28)
- daily.md · **The daily challenge.** One dealt setup a day from the author's ranges (`tools/daily_spec.json`); systems and players rise by weekday, opponents stay flat, each seat drawn on its own from the pool (not one depth a day, which dealt 2026-11-01 all actuary and left it empty); *Ship speed follows the map*: the dealer's `lane_turns` fits the speed so the median lane is 2-5 turns (6-12 nearly always fits on its own); *Random, but not unplayable*: the first candidate a probe wins within the day's cap, probes stopping at the first win and at the cap, up to 32; *The turn cap*: 80 on Monday to 110 on Sunday, since a flat 80 cut most slow weekend maps; re-measured on the narrower ranges, Monday keeps 26% of candidates and Sunday 21% (an empty Sunday about one in 2,000); made by `tools/daily.py` on the worker, never in the game; `dailies` readable up to today; `/api/daily`, the menu's Daily menu and `#daily`, ironman, *Copy daily result*; *The day's winner*: a day's map is embargoed until the day ends (`game_embargoes`), so the best on the day is kept (*Led at reveal*, *Best on the day*); `daily.html` (standings, a note that setups grow over the week, mendable streaks with the true streak on hover, archive); *Badges*: Daily done, Winner of the day, Full week; `tools/try_daily.py` deals and opens a weekday's map at home; first tries not built; a `RULES_VERSION` bump needs nothing from it.
- daily.md · **The daily puzzle (built 2026-10-28).** A second, independent daily, not a two-stage gauntlet: a 5-8 system random-mode duel won in the fewest turns; one opponent per weekday (heuristic, heuristic+lean, rusherplus, Claudebot, thinker, marshal, actuary); no fog; lanes aimed at no more than 4 turns with at most two 1-turn lanes, met by a 15-20 ly/turn speed range on 97.5% of 600 dealt duels rather than by new dealer rules; actuary at Field on Sunday; *The probe check and its cap*: 60 turns, a guard only (first probe win median 42.5 over four weeks, under a second a day); `dailies.kind` with key `(day, kind)`, `tools/puzzle_spec.json`, `#puzzle`, the menu's Daily ▸ Daily puzzle, `puzzle.html` as a tab of the daily page, `try_daily.py --puzzle`; `dailies.kind`, `#puzzle`, its own streaks and badges; the embargo hides the solution until the day ends.

## other-games.md (proposed, not built)
- other-games.md · **Features from other games** (survey 2026-10-27). Layers: star tiers and a pairwise rating (both set aside 2026-10-28, with the board's numbers), the record holder's ghost, puzzles, play-by-post chat/rematch/tournaments, a map gallery. Mechanics: bonus regions, other win conditions, terrain, an invest order, teams, handicaps, drafted starts. Not proposed: cards, tech, voting, ship types.

## setup-families.md
- setup-families.md · **The problem**; **Pace** (median-lane estimate, `PACE_LY`, the bands, ~81% exact as ceiling); **The family tree** (parent = nearest older config within cost 3; *data and fallback*; *the view*).
- setup-families.md · **Decided against:** folding opponents, By ruleset, a 2-D map, clustering, a family id on `Settings`, recording where a setup came from (`scores.from_config`).

## schema.md
- schema.md · **The trust model** (append-only by absent policies; worker-only and closed tables); **re-pasting upgrades a board** (view columns go last; the `public_replays.rules_version` incident).
- schema.md · Per relation: games and `game_embargoes`; scores; game_logs; score_checks; playstyle_readings; `sc_config_key`/`sc_bots`; configs and config_tags; bot_scores; campaigns; gauntlets; public_replays; config_summary; counted_scores.
- schema.md · **The grant trap.** `service_role` skips policies, not grants; three incidents; `tests/test_schema_grants.py`.
- schema.md · **Play-by-post tables.** Kept apart; inputs, not a board; seat tokens; the deadline; `board_digest`.

## par.md
- par.md · **Par search (a local check).** Why it is less random than it looks; the floor; lucky vs honest dice (lucky is no ceiling); the beam search; rollouts with marshal and actuary.
- par.md · *Readings (2026-10-14, keyed dice):* honest dice match the best human score on the live board, but lose to wins played on keyed dice.
- par.md · **A gap this exposes in `verify_scores`.** The dice half closed for keyed turns; the bot-orders half open by design.
- par.md · **Next, if it goes further.** Wider beams, candidates that aren't a bot's idea, a budget for knower rivals.

## seat-play.md
- seat-play.md · **What decided the games.** Bots evacuate into the system you struck from; covering every exit kills stacks; dead ends bottle a stack; a free-for-all goes to the swap war.
- seat-play.md · **Readings over the logs.** Strikes on systems emptied at launch: people refill, bots don't; marshal prices a garrison that runs; actuary crossing its own transfers.
- seat-play.md · **What a person should do** (the brief, pinning actuary). **What was built from these games:** four marshal arms (deleted), actuary's `SELF_RELIEF`.

## pbp.md
- pbp.md · **Context**, *Decisions taken up front*; **Constraints that shape the design**; **What it reuses**; **The one idea everything follows from** (the stored log is the record; a turn is decided once).
- pbp.md · **Verified against a real deploy** (*the bug that made the digest worth having*); **Testing the backend**; **Opening a match**; **How a deadline works**.
- pbp.md · **Public matches and the lobby** (open invitations, your seats, results; `pbp.html`). **Spectators** (a seat nobody holds; fog off only; backing-off polls).
- pbp.md · **What a poll costs** (briefs, the idle floor; *decided against: a slower steady cadence*); **Traps**.

## bots.md: the roster as a whole
- bots.md · **Measuring a bot: what earlier sweeps got wrong.** The method checklist, with the evidence for each item. Read before any measurement.
- bots.md · **How differently two bots play (`tools/bot_distance.py`).** Distance, kappa, fingerprints on shared positions, contested vs opening; replaces the candidate share as the first check.
- bots.md · **New bot families (proposed 2026-10-05).** Six designs: surveyor, convoy and learner's model built; duelist (matrix game) and riposte (counter-punch) not; apprentice (learned evaluator) not to be, as evolved covers it. Two measurement ideas.
- bots.md · **Lane length across the parameter space.** Nodes and ship speed move lane length over an order of magnitude; a constant keyed to travel time is live in one regime of three.
- bots.md · **The person's habits** (pointer to `learner.md`). **Positions from real games** (`position_suite`: `faster`, `median gain`, `recovered`, never blurred).
- bots.md · **The first census and setup sweep off the live board (2026-09).** People mostly play the middle regime.
- bots.md · **Replaying a bot for the leaderboard.** Best profile, not menu default; guards lifted 100x (`BUDGET_SCALE`); the seat-flag incident.
- bots.md · **Break-even margins and the roster back-port.** `min_swing` floors; clearing the edge is not a capture; the honest margin's cost at high jitter is correct.
- bots.md · **Defender advantage and the AI** (`ai._frontier_order`). Margins against the effective garrison; the slider caps at 1.5 for stalemates.
- bots.md · **Rules in full:** `AiParams.aux`, the one bot-defined knob; the `tests/sim` harness; per-seat AI and pricing a fight.

## external-bots.md (split from bots.md)
- external-bots.md · **The in-app bot maker: built, measured, not merged.** Branch `bot-maker`, PR #20; why it is decided against.
- external-bots.md · **Bots that aren't Python.** Why the wire protocol (`bot-api.md`) is shaped as it is: the full setup, not a subset; subprocesses outside `models/`.
- external-bots.md · **External bots: the four rules** (rules in full): `botio.decide_seed`, `degraded_runs` never scored or posted.

## ladder.md: the roster ladder
- ladder.md · **Re-running it.** The `tests.sim --ladder` command and cell; claudebot as thinker's second stop; Council left out; evolved added 2026-10-26; workers and a record keyed by `play_rev`, resumed; re-run after any roster, stop-default or combat change, beside the bot before it on the same seeds.
- ladder.md · **`LADDER_ORDER` is not the grid.** `LADDER_ORDER` (`ai.py`, `standings.mjs`) places each family by its top stop; the grid seats default stops.
- ladder.md · **The current reading (2026-10-26).** First with evolved (third, above knower at Predict); the 18-node and symmetric grids; the slow cell (old).
- ladder.md · **Earlier readings.** 2026-10-20 marshal's CMA ship and the bot before marshal's CMA ship; 2026-10-17/16; other stops (`--aux`); the six older bots before `DENY_SWAP`.

## win-turns.md
- win-turns.md · **The question.** People beat council on turns on most posted maps; can a bot win sooner by playing closer to losing?
- win-turns.md · **How it measures.** Cells and line-ups; every seed in every seat; turns to win (a loss slower than any win); turns to 3:1 and 9:1 income leads; `low`; paired `beats` (gate on it), Δwon, Δturns; the 7,000,000 seed block.
- win-turns.md · **Ledgers and revs.** `results/win_turns/<label>/<bot>.jsonl`; `play_rev` and `SHAPE`; `--base`; `bot_replay.engine_rev` leaves `replay.py` out.
- win-turns.md · **First readings (2026-10-19).** Council saturated and fastest; Hunt nearly saturated; marshal not (stalls to turn 600); most of every win comes before the 3:1 lead.
- win-turns.md · **marshal: sparing the target and feeding on every board.** Stalemates were the guard against the target's own stack. Shipped `SPARE_TARGET` and `FEED_MAX_TURNS` 0. *What is left* (open).
- win-turns.md · **marshal: a second round, nothing shipped.** Where marshal still loses (three seats, Council's emptied-system takes); 30 arms incl. the relay; `FEED` 20 the one candidate standing; counter-punch not built.
- win-turns.md · **Reading it.** Bots, not the posted maps; read `beats` first; one |z| of 2 in 18 groups is chance; what a run costs.

## bot-families.md
- bot-families.md · **The families.** knower (Off, Predict, Search, Council), actuary (Greedy, Field, Learning, Hunt), thinker (Claudebot, Thinker), evolved (Off, On); marshal, heuristic, rusherplus have no stops.
- bot-families.md · **Each fold plays exactly as the bot it replaced** (`tools/fold_check.py`). **Legacy names (`ai.LEGACY`)**: `ai.canonical`, `ai.strategy_fn`, `ai.known`.
- bot-families.md · **A value past a new top stop (`AUX_OVER`).** **A random seat deals the stop too** (`settings.resolve_profile`).
- bot-families.md · **On the leaderboard.** One row per family at its top stop (`fold-bots.sql`); `sc_bot_profile`; two-part chips; `bot-stops.mjs`. **Decided against** (rewriting names, a row per stop, ...).

## knower.md
- knower.md · **Simultaneous resolution** (why an oracle is possible). **What the oracle buys, and where** (a turn of warning, worth more on fast boards).
- knower.md · **Each Oracle setting against thinker and marshal.** Only Search beats marshal.
- knower.md · **Built, measured, removed.** Two ideas.
- knower.md · **The depth search: branch the root, play the rest on.** Deeper branching did nothing; more openings beat more turns; most of the result comes from borrowing marshal.
- knower.md · **Borrowed candidates (`EXTERNAL_CANDIDATES`).** Win shares on contested decisions; why `SEARCH_WIDTH` is 2.
- knower.md · **evolved as a borrowed candidate (2026-10-19).** `tools/candidate_bench.py`; shipped at Council only (`COUNCIL_CANDIDATES`, `COST_COUNCIL_LINES`); actuary would gain more at Search but is not borrowed there. New developments go on the top stop.
- knower.md · **How far to look.** Horizon = longest lane + `LANE_CUSHION`, replacing a fixed depth; Oracle became Off / Predict / Search.
- knower.md · **Cost per decide, and where the search guard trips.** The `ply_ms` fit, `setup_warning`, rivals' `decide_ms`; the browser unmeasured.
- knower.md · **Seats it cannot run: stand-ins and people.** `stand_in_params` (actuary Learning runs as Planned), trusted; people stay on `_blind`; `GUESS_HUMAN` removed.
- knower.md · **A doomed garrison leaves whole (2026-10-19).** `_flee`: shipped on Search; null on Council, not applied there.
- knower.md · **Pile-ups under the garrison-fights-last rule (2026-10-21).** `tools/pileup_bench.py`; folding rival arrivals in defence and attack: null at every stop.
- knower.md · **Baiting in a free-for-all (2026-10-21).** `tools/bait_bench.py`: a lean on screening seeds; fresh-seed and Council confirmation open.

## council.md
- council.md · **What council changes, and what each change bought.** actuary and evolved as borrowed candidates, the learned judge, each candidate playing its own line (the biggest gain); `aux` 0 is knower at Search.
- council.md · **How it borrows knower.** A private patched copy of `knower.py`; oracle rules kept.
- council.md · **The measurements.** Against knower:2, actuary, marshal; real-game positions; unbeaten posted maps; five players.
- council.md · **Against the posted maps and the people who posted them.** People beat it in the middle game, where it strips frontiers it can't relieve. What to try next.
- council.md · **evolved as one more borrowed candidate** (shipped 2026-10-20). Details in knower.md.
- council.md · **actuary at Hunt in Field's place (measured, not shipped).** Stronger but far past the browser's search guard, so decided against; without its look-ahead only a lean.
- council.md · **Stripped frontiers are trades, not mistakes.** `_cover` and `LOSS_PENALTY` both lose or null; the habit is style.
- council.md · **What it costs.** Browser timing, guards on slow devices, the gentler-jitter test exemption.
- council.md · **Built, measured, dropped.** Strikes, melee ideas, more openings, hybrids, dice shopping, tuning, crossover, successive halving, retrained judges.

## actuary.md
- actuary.md · **The ledger and the greedy.** A timeline per system; only the verdict worst-case; one value in ships; risk as expected loss; greedy commits. Not an oracle.
- actuary.md · **Cost, and the caches.** Version-stamped caches, the `_idle` prune; ms per decide.
- actuary.md · **Where actuary stands.** Four cells, free-for-all, combat sliders; weak at 24 nodes 3 ly/turn and jitter 0.3.
- actuary.md · **As one of knower's borrowed candidates (measured, not shipped).** Strong at Search head to head.
- actuary.md · **What the measurements changed.** Survivors and threats at the nominal roll; the plateau; `FRONT_BONUS` 0.5.
- actuary.md · **The 2026-10 sweep: off the plateau.** `MIN_GAIN` 1e-9 and `FRONT_DECAY` 0.75 shipped; `decide_ms` refitted.
- actuary.md · **Behind on income, ahead on ships (measured, not built).** A recklessness switch; the standing is too rare to matter.
- actuary.md · **The planned opening (Opening: Planned; retired 2026-10-18).** The land-grab as a one-player puzzle until contact; lost to the ledger opening except on fast boards; replaced by Field.
- actuary.md · **A fleet landing next door is relief too (`RELIEF_IN_FLIGHT`).** Counted at 0.3; measurements in marshal-pricing.md.
- actuary.md · **Relief only the same stack can pin (`SELF_RELIEF_SHARED`, deleted).** Null to worse; the double count is not why `SELF_RELIEF` 1.0 lost.

## hunter.md: actuary's Style: Hunt
- hunter.md · **How it was measured.** `tools/hunt_bench.py`: paired duels, seats swapped, the seed blocks.
- hunter.md · **Which strikes actuary launches.** `tools/hunt_predict.py`: a worst-roll rule catches nearly all; the roster's strike ratios.
- hunter.md · **Edge risk (`EDGE_RISK`).** A threat counts only if a rival would launch it.
- hunter.md · **Gathering on fast boards (`EDGE_GATHER_LANE`)** and **softer strikes on slow boards (`CAPTURE_SWING`, `CAPTURE_LANE`).**
- hunter.md · **REINFORCE_WEIGHT.** A plateau at 0.45-0.7; the 43-variant single-constant sweep.
- hunter.md · **Look-ahead (`LOOKAHEAD`).** Postures rolled six turns, bounded by work not time; the main gain over actuary.
- hunter.md · **The field, by lane length (`FIELD`, `SLOW_LANE`, `SLOW_FRONT`).** Field's pull below 5-turn lanes, Greedy's front above; the planned opening retired.
- hunter.md · **Where it stands** (vs Field, marshal, thinker, knower). **What it costs.**
- hunter.md · **Built, measured, removed.** Predicted counter-punch, per-rival thresholds, start-of-turn trigger, stalemate breaker, endgame closer, Style: Learning.
- hunter.md · **Open.** knower at Search; the field on slow boards with the look-ahead; hunter's knobs not re-swept on the field.

## learner.md
- learner.md · **Why the oracle flag does not make memory safe.** Callers that run `decide` on copies or rewound boards; `load_models` wipes module state.
- learner.md · **The memo tree.** A board found by content; its parent a stored board it provably follows (`_follows`); cold-start cost.
- learner.md · **What a turn shows** and **the model and the prior.** Launches, strike records by ratio, send share, guard, evacuation; prior fitted from roster self-play.
- learner.md · **The prediction check (`tools/learner_check.py`).** Four predictors, Brier and ship error; the model separates guard and evacuation, not the strike curve.
- learner.md · **Sizing a strike to its target** (kept), **and splitting the strike from the target** (deleted).
- learner.md · **Predicting people (`--logs public`).** Memory beats the prior on people; `TRUST_HUMAN` backed off.
- learner.md · **The person's habits (`tools/human_habits.py`).** Posted games vs roster self-play; people empty frontiers with relief covered far more than bots.
- learner.md · **As a bot.** actuary's ledger plus expected launches; Trust knob; its ladder lead is mostly the oracle flag.
- learner.md · **Trusting a prediction (deleted).** Trust and Sure; confident calls are rare.
- learner.md · **The gate.** Not passed. *Open:* whether that is power, and a strike curve that tells the bots apart.
- learner.md · **Joining actuary as a stop: the gate (written, not run).** Superseded by learning.md; the three proposed improvements.

## learning.md: actuary's Style: Learning
- learning.md · **Why the curve goes in the risk term**, **what was measured, and what failed first**, **the stop**, **what else it changed.** `LEARN_REACH` x the rival's strike curve; Raise was the wrong use.
- learning.md · **Evacuation in the price of a capture (deleted).** Worse; it can only take credit from strikes that already win.
- learning.md · **Strike and no-strike priced apart (kept).** Each rival garrison strikes whole or not at all; now Learning's risk term.
- learning.md · **Memory off: the prior alone (flag removed).** Learning beats memory-off nowhere against bots; the memory stays, to be improved against people.
- learning.md · **Five more arms, and three combinations (deleted).** Pressed row, strike size, learned guard, joint strikes, `PRIOR_WEIGHT` 1.
- learning.md · **The counter-strike (deleted).** An option value from a rival's learned empty rate; failed on fresh seeds; the people-profile question open.

## convoy.md (built, not shipped)
- convoy.md · **The plan.** Supply per system and turn; objectives as N ships by turn t; launch at the last moment.
- convoy.md · **Where convoy stands** (above thinker, below marshal and actuary); **how differently it plays**; **its opening in front of another bot** (not borrowed).
- convoy.md · **Where it loses to actuary** (out-traded after contact). **What the measurements changed** (`STRIKE_PAD` 1.25, the nulls).

## evolved.md
- evolved.md · **What it is for, and its constraints.** Blind, browser-sized, deterministic.
- evolved.md · **The policy.** Priced moves per system naming their principle; a linear scorer plus a residual head.
- evolved.md · **The look-ahead.** Six turns at the expected roll; rivals hold or strike all-out; the gain is the veto.
- evolved.md · **Where it stands.** Third of six; weaker multi-seat; borrowed by Council since 2026-10-20.
- evolved.md · **Training it** (CMA-ES, the turn-40 share); **measuring it** (the +4 bar); **why multi-seat is short** (no blind remedy).

## field.md: actuary's Style: Field (once fulcrum)
- field.md · **The middle game the roster plays.** Half of strikes land on empty systems; evacuees step into the striker's source; defensive losses separate winner from loser.
- field.md · **Where a ledger's moves come from.** Traps, full relief, new phase bots dropped; `FRONT_BONUS` 0 collapses actuary; pulls that steer; pinning.
- field.md · **The field.** Influence per median lane, stake weighted by balance, pull as a share of the strongest; *how it was tuned*.
- field.md · **Where Field stands**, **how differently it plays**, **what it costs**.
- field.md · **Built, measured, dropped.** Traps, full relief, rival reinforcement, value systems, pinning, the field per turn, a transit cost, a field against the strongest rival.
- field.md · **Folded into actuary.** Field replaces Planned as stop 1; Learning rebuilt on it.
- field.md · **Slow lanes: half the front** (`SLOW_LANE` 5).
- field.md · **Backyard neutrals (`BACKYARD`, `BACKYARD_MATURE`).** A neutral only we border gets a floor once the front is settled; ends stalemates at no cost.

## marshal.md
- marshal.md · **What the measurements deleted.** The square-law case for overwhelming force; chokepoints lose; standing aside in a free-for-all (`_wedge`).
- marshal.md · **Where marshal stands.** Against knower at depth 0 and its oracle, across defender advantage, the old-vs-new marshal A/B; the roster ladder moved to ladder.md.
- marshal.md · **A stagger's nearer wave is reserved.** **More ideas measured and deleted.**
- marshal.md · **`FRONTIER_GUARD`.** How it came to be 0.40 documented as 0.3; now 0.55.
- marshal.md · **The 2026-09 tuning sweep.** What was adopted, the regime-bound `ENEMY_FAR`, negative results, the empty-interior statistic.
- marshal.md · **Is the guard a pool? (a reserve).** `tools/guard_census.py`: released guard rarely relieves; `STICKY_GUARD`, `ISOLATED_GUARD` not run.
- marshal.md · **Multiplayer tactics: four quick checks (2026-10-21).** `tools/mp_tactics.py`: leader, weak player, standing-scaled guard, blind bait: none better.

## marshal-pricing.md
- marshal-pricing.md · **Racing a third player for the same system.** `_rival_waves`, `_after_clash`; `REMNANT` shipped for two players alive (`REMNANT_MAX_PLAYERS`).
- marshal-pricing.md · **Garrisons run away, so the jitter premium buys almost nothing.** Most evacuate; the attack margin is the advantage multiplier alone.
- marshal-pricing.md · **Two doomed neighbours, and what a retreat is worth.** `AVOID_ABANDONED`, `CONSOLIDATE`, `RETREAT_NEAREST`.
- marshal-pricing.md · **Rushing the enemy** (capped near 2 points). **A 0-ship neutral** (null). **Holding a doomed system to sacrifice-and-retake** (null).
- marshal-pricing.md · **Re-tuned for production-before-combat and the garrison-fights-last pile-up.** `_attacker_pileup`, `RISK_PARITY`. **The attack side of the pile-up** (shipped for correctness).
- marshal-pricing.md · **A fleet landing next door is relief too (`RELIEF_IN_FLIGHT`).** 0.5 marshal, 0.3 actuary. *Open:* a higher weight against people.

## marshal-flow.md
- marshal-flow.md · **Phase 3b re-flooding an already-covered target.** Rival sieges gated at `RIVAL_REFLOOD_MIN_TURNS` 5.
- marshal-flow.md · **A non-oracle successor to marshal.** `FLOW_AVOIDS_ABANDONED`, `RELIEF_AWARE`, `FAST_GUARD_WEIGHT`; the `tools/sweep.py` smoke-gate quirk.
- marshal-flow.md · **The empty interior is the retreat.** Spearhead loses; `DEEP_RELIEF` inert. **Denying the swap** (`DENY_SWAP_MIN_TURNS` 4).
- marshal-flow.md · **When a doomed garrison leaves** (`EVAC_AT` deleted).
- marshal-flow.md · **What the board's human wins say (2026-09-30).** Hold test deleted; `FEED` shipped (re-tuned in marshal-joint.md); `TRICKLE_GATE` null.
- marshal-flow.md · **Holding a capture before the counter.** `HOLD_FOLLOW` kept off behind its flag; `BACKFILL` deleted.

## marshal-joint.md
- marshal-joint.md · **Which constants move together (2026-10-16).** The decision order couples them; *the census* (`tools/coupling_census.py`); *one guard, two floors*; the optimum moves against the roster; what to tune together.
- marshal-joint.md · **Screening against the roster (2026-10-17).** The weighted objective; the factorial; `FEED` is a ratio that stops binding; candidate D shipped (pour floor 0.85, `FEED` 3.0, `FEED_MAX_TURNS` 3, `RELIEF_AWARE` 0.65).
- marshal-joint.md · **Tuning by CMA-ES** (pointer to marshal-cma.md).

## marshal-cma.md
- marshal-cma.md · **Tuning by CMA-ES (2026-10-20).** `tools/const_train.py`; the run; the first candidate fails on knower; *the ablation*; *candidate B* shipped as nine constants; what this says about tuning marshal.
