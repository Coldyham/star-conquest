# Decided against

Grep this page for the constant, function or idea you are about to touch;
don't read it whole. The file index is [`README.md`](README.md).

Each entry gives the idea, the outcome and where it is recorded. "Null" means
the measured result was indistinguishable from the baseline.

## setup families ([`design/setup-families.md`](design/setup-families.md))
- Folding opponents into one setup, and a By ruleset view: the existing views stay as they are; opponents are a light edit in the family tree. *Decided against.*
- A 2-D map of every setup (MDS, t-SNE), and clustering: points and membership move with every new config. A family id on `Settings`: moves every challenge key. Storing the exact median lane per game: a column, and blank until the job runs. *Decided against.*

## knower ([`design/knower.md`](design/knower.md))
- Pricing three-way pile-ups with the oracle: a wash, possibly worse. *Built, measured, removed.*
- Striking perishable (vacating) targets first: no change. *Built, measured, removed.*
- Branching every ply of the search, cut per opening: replaced by root-only branching, which is even at a quarter of the cost. *The depth search.*
- Rolling every line on until its fleets land: lost head-to-head. *The depth search.*
- The all-in and push-for-depth postures inside the search width: stopped paying once real rushers were candidates. *Borrowed candidates.*
- Any fixed search depth: replaced by longest lane + 5. Depth past 5 was noise. *How far to look.*
- Modelling a person with marshal or actuary (Planned) instead of `_blind`: worse forecasts (Brier +0.021, +0.025). Assuming a person holds (`GUESS_HUMAN`, built, removed): worse against bots flagged as people (z -3.33), a test that favours the guess. *Seats it cannot run.*

## council ([`design/council.md`](design/council.md))
- Synchronised strike openings: a gain that did not replicate on fresh seeds. Staggered strikes, decoys, double strikes: null or worse. *Built, measured, dropped.*
- Melee guard, denial weighting, relative melee scoring: null or worse in three-player games. *Same section.*
- Predicting a knower rival with its real Search: null at double the cost. More openings (all postures, Greedy actuary, a hold): null. *Same section.*
- Hybrids (−4.9 points) and dice shopping against the real keyed dice (−8.7). *Same section.*
- NTBEA tuning, root crossover, successive halving: null on confirmation. *Same section.*
- Retraining the judge on council's own games, search values, a deeper look-ahead or 13 more features: none picks better on the ranking test. *Same section.*

## marshal: [`design/marshal.md`](design/marshal.md)
- Baiting a rival into a relievable system, and the offensive half of "stand aside": needs a model of rivals, which is knower's territory. *What the measurements deleted.*
- Chokepoint value (betweenness): loses at every weight. Pocket-sealing: a constant offset. *Same section.*
- `FRONTIER_GUARD = 0` (ablated alone): wrong, because of its interaction with Phase 3b. *Same section.*
- Reinforceability-scaled guards: 46%. Splitting a breakthrough's surplus: null. Dropping the guard against a neighbour our fleets take this turn: 48.4-49.5%, since a capture is often retaken and the guard is what holds it. *More ideas measured and deleted.*
- A flat garrison floor (`RESERVE_FLOOR` 1 or 2): catastrophic. Opening the wedge gate at 3 players: a wash. Raising `RIVAL_WEDGE`: on its plateau. Propagating threat through neutral buffers: 26.7% in slow cells. *The 2026-09 tuning sweep.*
- `ENEMY_NEAR`/`ENEMY_FAR`/`NEAR_PAD` and the distance ramp: removed later (see *Garrisons run away*).

## marshal: [`design/marshal-pricing.md`](design/marshal-pricing.md)
- Repricing a contested neutral against the rival's survivor: worse in duels, because the price is a gate. *Racing a third player.*
- Arriving after a rival breaks a neutral (the remnant tactic): null by game rate, and worse at high advantage. Re-scored seed by seed it is z +3.93 in duels against marshal and +1.72 against knower, but leans worse (z -1.94) in three-player games with knower, so it ships only while two players are alive. Distinguishing same-turn blocs, or dropping them: a wash. *Same section.*
- Dropping the advantage half of the attack edge as well as the jitter half: 8.6% at advantage 1.5. *Garrisons run away.*
- A bonus for enemy-held targets, or rushing the enemy outright: null, and the strong form is worse. *Rushing the enemy.*
- Repricing 0-ship neutrals: null. *A 0-ship neutral.*
- Holding a doomed garrison to sacrifice and retake: null. *Holding a doomed system.*
- The four null tactics combined, or with `COMMIT_SURPLUS` off: still null; the masking theory is false. *Re-tuned for production-before-combat.*

## marshal: [`design/marshal-flow.md`](design/marshal-flow.md)
- Gating Phase 3b re-flooding of rival targets at every lane length: regressed at high advantage. Kept for lanes of 5+ turns only. *Phase 3b re-flooding.*
- Concentrating on one hammer stack (spearhead): 23-42%. `DEEP_RELIEF`: inert. *The empty interior is the retreat.*
- `DENY_SWAP` on Phase 3's priced strike: a disaster. Shipped on the surplus only. *Denying the swap.*
- Evacuating at the last moment (`EVAC_AT`): did not replicate. *When a doomed garrison leaves.*
- A hold test on captures: worse the harder it bites. `FEED_LOCAL` and `FEED_MAX_TURNS = 3`, at `FEED` 1.0: deleted. Three turns pays at `FEED` 2-3 (see `marshal-joint.md`, *Screening against the roster*). *What the board's human wins say.*
- A follow-up timed to hold a capture before the counter (`HOLD_FOLLOW`, kept off behind its flag): a faint lean seed by seed (z +1.36 on fresh seeds across marshal, knower and actuary) that no sample establishes. Freeing a guard when a stand-in on a no-slower lane backfills it (`BACKFILL`): null against marshal, worse against knower (z -2.37). *Holding a capture before the counter.*
- `CORNERED_EDGE` (full attack edge on a garrison with nowhere to run): 49.2%, null. `REFILL_SOURCE` (top up a struck-from source from its neighbours): 45.3%, reproduced worse; from rear systems only, 49.2%, null. `TRICKLE_GATE` (the "no lone trickles" candidate: pour on a rival target only with a same-turn wave): 50.9%, then exactly 50.0% on 300 fresh seeds including 3 ly/turn. All deleted. *See `seat-play.md`, "What was built from these games".*
- **Open, not rejected:** porting `_evacuate`'s defensive fixes to thinker and knower (unmeasured; see *Two doomed neighbours*), and no lone trickles into a rival (unmeasured).

## marshal: [`design/marshal-joint.md`](design/marshal-joint.md)
- The guard split into a strike floor of 0 and a pour floor of 0.55: +6.0 against stock marshal on fresh seeds, level against knower, -6.2 (z -8.5) against actuary. A best response to stock marshal's stalemates, not a better bot. Not shipped. *Against the roster, the optimum moves*.
- Feeding before the pour (Phase 3b stops at `FEED`'s own level, read raw, on boards `FEED` runs on): -1.4 points (z -4.7) against marshal, up to -5 on short lanes. The shipped order, pour then feed, stays. *One guard, two floors*.
- A higher pour floor on its own (0.75, 1.0) at the shipped guard against marshal: null to worse. A lower floor for relief alone: null; for the front top-ups alone: +0.7. *Same section*. Against the roster 0.85 adds: +1.65 weighted in the factorial, +0.85 at D. *Screening against the roster*.
- The guard moved as one number, up or down (0.40-1.0, every floor): it trades actuary against marshal and knower (1.0: +4.3 against actuary, -8.1 against marshal, -5.8 against knower), so no level survives the weighted screen. A strike floor above the pour (0.85 / 0.55): -7.6 against marshal. A long-lane guard weight (0.5, 1.5): worse. `RELIEF_AWARE` 0.35 (-3.0 against actuary) or 1.0 (-3.2 against marshal). `FEED_FRONT` off at `FEED` 3: -3.4. *Screening against the roster*.
- **Open, not rejected:** the guard at 0.45 under the shipped 0.85 pour floor (ends a quarter of the stalemates against marshal, mixed against knower); D at defender advantage 1.5, its one losing cell (-2.0 against knower); a pour floor shaped by lane length. *Screening against the roster*, *Against the roster*.

## actuary ([`design/actuary.md`](design/actuary.md))
- Pricing a fight's survivors at the worst roll as well as its verdict: refused every capture at jitter 0.3 (0 of 40 against marshal). *What the measurements changed.*
- Pricing threats at the worst roll: hoarded garrisons; worse in both cells measured. *Same section.*
- Shipping it as a knower `EXTERNAL_CANDIDATES` entry: not done (cost on every Search root, effect on knower unmeasured), not rejected. *As one of knower's borrowed candidates.*
- Rules about contested neutrals (land after a rival racing for one, veto an unholdable capture and guard beside it, veto a capture that does not repay before the earliest possible arrival): worse than Greedy in every cell. *The first attempt.*
- A planned opening reaching 2 turns past the halfway line: worse at every size. *The clock is about twice the earliest strike.*
- The planned opening in front of marshal: worse in every cell. *In front of marshal it costs.*
- Handing the opening over 2 or 3 lanes from a rival: fixes small maps, gives back the 40-80 node gain. A side floor of 9-16: costs a little at 24-80 nodes. *A side too small to plan.*
- Trying several `aux` values per map in the bot column and keeping the best: mostly picks the luckier dice (one board ranges 13-165 turns on dice alone); fix the bot instead. *Same section.*
- `RISK_WEIGHT` 0.3 / 1.0, `MIN_GAIN` 0.5, `FRONT_DECAY` 0.25, `TAIL_TURNS` 14, `REINFORCE_WEIGHT` 0: all worse pooled against marshal. *The 2026-10 sweep.*
- `SELF_RELIEF` 1.0 (count all of our neighbouring garrisons as relief in the risk term): 44.6%, reproduced worse, and again on fresh seeds. 0.5 reproduced *better* (seed pairs z +2.38, and z +3.64 on fresh seeds), and the lane-aware form (`SELF_RELIEF_REACTS`) was then measured in full (2026-10-13): 0.8 is better than stock against marshal in every cell on fresh seeds (z +7.0), at Greedy, Planned and Learning, against knower Predict (z +5.6), across the advantage sliders and in free-for-all, for 16-36% more CPU. Shipped 2026-10-13 with the gated opening (*The gate, shipped*: +226/-110 over the old bot against marshal). Not decided against. *A front stack pins the whole side.*
- `SELF_RELIEF_SHARED` (a neighbour that only the threatening stacks can pin counted at 1.0, the rest at 0.8, 0.5 or 0; also neighbours on the front only): null to worse against actuary and marshal (best +0.4 points, z +1.13; worst -2.6, z -5.15). *Relief only the same stack can pin.*
- Netting same-turn crossing transfers between our own systems (`NET_CROSSINGS`): 48.7%, seed pairs z -1.64. Deleted. *Crossing transfers.*
- Pricing a neutral capture below the worst roll (`NEUTRAL_SWING` 1.10 / 1.15): reproduced worse at 1.10 (seed pairs z -2.10), null at 1.15. Deleted. *Neutrals at less than the worst roll.*
- **Open, not rejected:** clock 3 for the slow regime (+1.5 there in the 2026-10 sweep, not significant). Planned trailing Greedy a little with two seats on 10-18 nodes. *A side too small to plan.*
- Pricing strikes at the nominal roll and dropping the risk weight while behind on income but ahead on ships: not built, since a real lead on a short clock arises 4 times in 84 games (2 across the posted human games) and a real lead is already converted 87% of the time. Variance-seeking while behind on both: unmeasured. *Behind on income, ahead on ships.*
- `MIN_GAIN` 0 with `FRONT_DECAY` 0.75 or 0.85: no better than `MIN_GAIN` 0 alone. `HORIZON_MAX` 8: inert outside the slow cell, null in it. *The 2026-10 sweep.*

## learner ([`design/learner.md`](design/learner.md))
- Thinning a rival's source by its expected launch (Trust): 44% against actuary, worse than Raise; a source sends nearly all or nothing, so an expected value is the wrong thing to deduct. *Trusting a prediction.*
- Trusting confident calls (p 0.5+) against a rival once 70% have come true (Sure): indistinguishable from Raise; the gate stays shut against actuary and opening it against marshal buys nothing. *Same section.*
- Splitting a strike into "does this source strike at all" and "which target": behind the per-target curve on strike chance in all 8 cell-runs, its prior too; did not separate the bots. Deleted. *Sizing a strike to its target, and splitting the strike from the target.*

## Learning ([`design/learning.md`](design/learning.md))
- Raise (each predicted strike added as a fleet), with or without keeping only the strikes we could not answer after seeing them: level with actuary; a prediction that can only add threat only adds caution. Its file was deleted when the model joined actuary. *What was measured, and what failed first.*
- Scaling actuary's risk by `predict`'s call at today's garrison (flat K, 1-64): better against marshal, worse against knower Off and actuary; relaxing thins the garrison the call was made at. A per-rival calibration correction points the wrong way (knower's low calls strike less than predicted). *Same section.*
- A rival garrison next door counted at its chance-weighted share (n x min(1, `LEARN_REACH` x p)), Learning as first built: replaced by pricing a whole strike at its chance, better against marshal (z +2.26). *Strike and no-strike priced apart.*
- Evacuation in the price of a capture (a doomed rival garrison leaves at its learned rate, so the strike kills fewer and the evacuees threaten the capture): worse than Learning against marshal (z -4.2), knower Off (z -2.3) and Planned (z -3.0). Conditioned on a doomed garrison, it can only make strikes worth less, never cheaper. *Evacuation in the price of a capture.*
- Learning's memory of each rival, as against the prior curve alone: never better (9,600 paired games), worse against Planned (z -2.29). The flag is removed and the memory still ships, pending the author's call on the oracle claim. *Memory off: the prior alone.*
- In Learning's risk: a rival garrison's learned guard kept home (z -2.08 against marshal), a strike all-in or sized at the prior's split (-2.20, and -2.96 against Planned), a rival's garrisons next door striking together (-2.50), `PRIOR_WEIGHT` 1 (null), and pressed with guard, with guard and size, and with guard and joint (all worse). The pressed row alone leans better, but not significantly. *Five more arms, and three combinations.*
- The counter-strike: a reserve next to a rival valued by that rival's learned empty rate. It failed confirmation on fresh seeds and did no better than the prior rate against the roster. Against people it is untested. *The counter-strike.*

## convoy ([`design/convoy.md`](design/convoy.md))
- Keeping the guard against the struck neighbour unless the lane is one turn: 23-37% against waiving it, more timeouts. *What the measurements changed.*
- `STRIKE_PAD` 1.5 and 2.0: worse, 2.0 catastrophic. A doomed garrison staying to fight: 38%. *Same section.*
- Filling short frontier guards from the interior, staging ships at the last system before the target, spending existing ships before future hulls: all null, deleted. *Same section.*
- Defence at the nominal roll: a lean (54.8%, z ~1.5), not adopted; worth re-running. *Same section.*
- Shipping convoy as a roster bot: between thinker and marshal, distinct only in the opening. Removed from `models/`; the code is at commit `7153065`. *Built, measured, not shipped* (the file's opening paragraph).
- Borrowing convoy's opening for another bot, as surveyor's was for actuary: 32-45% in front of marshal, 47% pooled against actuary's Planned opening. *Its opening in front of another bot.*

## evolved ([`design/evolved.md`](design/evolved.md))
- The first look-ahead, rivals modelled by evolved's own policy (7 arms), and a learned value: null; the self-model imagined 2-4x the attacks real rivals make. *Decided against.*
- Full-genome retrains, the head trained longer (-2.15), more expansion, relief retuned: level or worse; the linear policy is at its ceiling for its inputs. *Same section.*
- Two-turn moves (`stage`, `converge`), defeat in detail / cutting / culmination / miai (-2.07), beyond defeat and honte (never confirmed), the opening as its own game (-1.28): removed. *Same section.*
- Opponent learning from the board: no blind model predicts roster launches better than "the rival holds". *Same section.*
- A deeper, wider look-ahead with no guard: -0.5 against actuary; dice averaged per plan: below expected dice. *Same section.*
- Fewer own-to-own moves (-1.2 to -3.1) and pricing a third seat's fleet into the ask (-0.18), the two remedies the multi-seat diagnoses named. *Why multi-seat is short.*
- Retraining the move-kind biases with the look-ahead on (-0.92), and a separate multi-seat weight set (+0.67, null). *Decided against.*

## The roster ([`design/bots.md`](design/bots.md))
- A visual rule-based bot maker in the app: works, but tops out below thinker and serves almost nobody. Branch kept, not merged. *The in-app bot maker.*
- A `simulate` callback or oracle parity for external bots: ruled out, and rival strategy names are masked. *Bots that aren't Python.*
- Raw-count attack margins: the AI stops expanding. A defender-advantage ceiling of 2.0: hard stalemates. *Defender advantage and the AI.*
- Capping the break-even edge to win back high-jitter results: that would reintroduce the bug. *Break-even margins.*
- A shallower search for the offline bot column instead of a bigger budget: not reproducible. *Replaying a bot for the leaderboard.*
- Leaving the replayed seat flagged human to match the Watch link: a handicap. Superseded by stored replays. *Same section.*

## Core ([`design/core.md`](design/core.md))
- A `models/` dispatcher bot for the random seat: breaks the oracle contract. Drawing the pick from `state.rng`: would shift the dice. *A seat left to the seed.*
- Dealing random seats from the loaded roster: re-deals every random map whenever a bot is added, and differs between builds. A roster fingerprint in the challenge key: splits the board on every new bot. *A seat left to the seed.*
- Hashing only non-default fields for the challenge key: aliases old keys onto a different balance. Enumerating int/float `aux` subsets: unbounded. *Keys outlive the schema.*
- Challenge links in the address bar or `localStorage`: no. Locking a challenge's widgets: no. *Challenge links.*
- Re-timing fleets in flight under growth, linear growth, rescaling the baked `travel_turns`: all rejected. *Ship-speed growth.*
- Pooled lane battles and merging the survivors: produced wrong results and visible teleports. *In-lane battles.*
- Replaying by re-running the AI (format v1), re-running `decide` just to advance the rng, and snapshotting the Mersenne Twister state per turn: all rejected. Deriving the dice from `(seed, turn)` was rejected too, then built per fight on 2026-10-08 (the dice are still recorded). *Persistence, replay & history.*
- Reading star names from a data file at runtime: no, they are generated. *Star names.*

## Shell ([`design/shell.md`](design/shell.md))
- Printing the "subtract the fleets" answer for contrast; the survivor curve (replaced by the jitter matrix); five swings per axis; runtime prose wrap on the menu: all rejected. *Combat rules page.*
- A "keep N / send N%" switch on each forwarding rule: "keep N" means "send the rest", so only one lane per system could use it; replaced by a hold per system and a share per lane. Showing the hold on each lane's label: crowded a split's source. Disabling + once the shares reach 100%: dead in the default state. *Forwarding rules.*
- Rally tie-break by ships/turn toward the rally point drawing less: built, then replaced by an even split once rules could split. *Route mode.*
- A side-panel list of every queued order and rule (capped, scrolled, a × per row): built and removed once the popup opened on edit too. *Send popup and spectating.*
- Unrestricted (enemy-crossing) routes: unrepresentable. Two-stage pick-then-aim; "tap a pick to remove, anything else to aim"; a Chain/Rally button pair; rally claiming only systems nearer than a front; weighting the AI's flow by turns: all rejected. *Route mode.*

## Turn playback ([`design/turnfilm.md`](design/turnfilm.md))
- Pausing the engine between phases; an applier that re-derives outcomes; widening the crossing sort tuple (it would move stored replays); per-beat dwells summed into the film; `FILM_LAUNCH_MS` 220; flat-expiry marks, then marks fading over the film's length; an in-game animation toggle; a loss label summing both sides (or a per-side pair); lane tracks ranked by position or derived from launch turn; camera moves toward the action; a burst on every arrival: all rejected.

## Hand-drawn maps ([`design/hand-maps.md`](design/hand-maps.md))
- A map file referenced by name; a live `GameState` as the editor's model; clamping a drag to the nearest legal point; Auto-lanes merging into hand-drawn lanes; a crossing as a blocker; silent seat compaction; a palette press that only selects; relaning on every drag frame; widening `WORLD_SIZE`; scaling an adopted map to fill the box; opening on a generated board; a deselect gesture in Systems: all rejected.

## Leaderboard ([`design/leaderboard.md`](design/leaderboard.md))
- An always-on service for the bot column; honouring slot 0's `AiParams`; ranking losses by turns; a Watch link that re-decides the match live; a git SHA as `engine_rev`: all rejected. *Bot replays.*
- A JS replay viewer; a durable client id; a durable IP-based rate limit; a per-model replay floor; sealing forks of a watched replay: all rejected. A full board snapshot per turn: **set aside for now, not ruled out.** *Checked scores.*
- Warming actuary's learner (Style: Learning) from a stored per-player profile: dropped unbuilt. The strike curve it plays from reads the same for every bot and the person, and `decide` would come to depend on more than the board. The profile became the player page's Playstyle panel instead. *Playstyle readings.*
- A one-hour cooldown between campaign moves, with wins posted during it queued: built and removed, since two clocks side by side ("post within 12 min", "plays in 40") read as nonsense. *The grace period*.
- An "able to capture" flag in a campaign game's link, honoured on its own: rejected, since a kept link is a standing permit. Built instead as a stamp that only narrows the grace. *The grace period*.
- Counting every score but a `mismatch` toward crowns and the campaign: replaced from 2026-10-15 by verified only, with 24 hours' grace, since anyone can insert a score; older scores keep the old rule. `schema.md`, *counted_scores and the crowns*.
- `outdated` as a fifth verdict: split into a column, since a score can be verified and outdated at once. `schema.md`, *score_checks*.
- A play-by-post duel to settle two campaign fleets meeting at one node: set aside for the best-score rule, since the duel is a different game on a different map and needs both players to turn up. *Campaign fleets (proposed, not built).*

## Par search ([`design/par.md`](design/par.md))
- A par or floor per map on the board: decided against; the tool stays local. *Not on the board.*
- Lucky dice as a ceiling: withdrawn, since rival fights still roll and honest dice beat lucky in 2 of 18 pairs. *Dice modes.*

## Play-by-post ([`design/pbp.md`](design/pbp.md))
- Every client re-running every bot from the stored orders: forked matches. Replaced by "the stored log is the record". Enforced fog: not attempted; fog is convenience. Resolving a turn on a clock: never. *The one idea everything follows from*, *Traps*.
