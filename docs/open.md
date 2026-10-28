# Open work

Work proposed or measured but neither shipped nor rejected, and decisions left
to the author. Grep it like [`sections.md`](sections.md): each line starts with
the design file that holds the detail and ends with its section. When an item
ships or is dropped, delete its line here (and add it to
[`decided-against.md`](decided-against.md) if it lost).

## Decisions left to the author
- leaderboard.md · A hard cutover that marks unkeyed logs posted well after the keyed-dice deploy as unverified; needs the real deploy date plus a grace period for stale service workers. *Checked scores.*
- bot-api.md · Who builds a compiled bot, and whether any bot may be slow (the baseline roster is `ladder.md`'s). *Open questions.*
- settings / menu · Whether the setup menu exposes the fragile config (production-weights dict, world-size ratios) and cosmetics ("Pass 3"); a deliberate pause, not an oversight.
- daily.md · The daily: Wednesday's players assumed 2-5; remaking an unplayed day after a `RULES_VERSION` bump. *Decisions left to the author.*

## Proposed, not built
- gauntlet.md · The roguelite run: a seeded run graph, stages dealt like the campaign's, difficulty by depth. *The gauntlet: proposed, not built.*
- gauntlet.md · Targets on lanes; the generated-gauntlet web build is untimed on a phone. *Later: targets on lanes*, *In the game.*
- bots.md · duelist (the simultaneous move as a matrix game), riposte (the counter-punch). *New bot families (proposed 2026-10-05).*
- leaderboard.md · A full board snapshot per turn: set aside, not ruled out. *Checked scores.*
- daily.md · The daily challenge: one setup a day, systems and players rising Mon (7-12, 2-3) to Sun (25-30, 2-6), opponents flat, ship speed fitted so the median lane is 2-5 turns (a new dealer rule `lane_turns`), the first candidate a probe wins within a cap growing from 80 turns on Monday to 110 on Sunday; made by `tools/daily.py` on the hourly worker, stored in `dailies` (readable up to today), fetched by `/api/daily` and `#daily`; first tries, streaks, a copied result line. *The idea in one paragraph.*
- other-games.md · Star tiers on every map from the bot column or probe wins. *Star tiers on every map.*
- other-games.md · A rating against the bot ladder, with monthly seasons, computed offline. *A rating against the bot ladder.*
- other-games.md · Racing the record holder's ghost: their systems-held curve beside yours. *Racing the record holder's ghost.*
- other-games.md · Puzzles from real positions as hand-drawn maps; fleets in flight are not representable. *Puzzles from real positions.*
- other-games.md · Play-by-post chat, rematch, tournaments and groups. *Play-by-post: chat, rematch, tournaments, groups.*
- other-games.md · A map gallery of hand-drawn maps with plays, records and likes. *A map gallery.*
- other-games.md · Bonus regions (Risk continents), hand-drawn maps first. *Bonus regions.*
- other-games.md · Win conditions besides conquest: a turn limit with points, capitals, king of the hill. *Win conditions other than total conquest.*
- other-games.md · Terrain: fortress systems, nebula lanes, impassable fields, wormholes. *Terrain.*
- other-games.md · An invest order to speed a system's production (a new `Order` kind). *An invest order.*
- other-games.md · Teams and co-op against bots. *Teams and co-op.*
- other-games.md · Per-seat handicaps, never an `AiParams` field. *Per-seat handicaps.*
- other-games.md · Drafted starting positions. *Drafted starting positions.*

## Bot research
- ladder.md · The slow cell (24 nodes, 3 ly/turn) predates actuary; `tests.sim` has no ship-speed flag to re-run it. *Re-running it.*
- knower.md · Bait openings at Search (a wall between two rivals emptied): a lean on screening seeds; confirm on fresh seeds and read at Council. *Baiting in a free-for-all.*
- knower.md · Not built: the remnant strike, and a person's launch folded in as uncertain. *Pile-ups under the garrison-fights-last rule.*
- council.md · Closing the middle-game gap to people on the posted maps. *Against the posted maps and the people who posted them.*
- hunter.md · Where Hunt does not win (knower at Search); why the field costs on slow boards with the look-ahead; hunter's knobs not re-swept on the field. *Open.*
- field.md · A capture's income discounted by the balance: level with the shipped field at 60 seeds. *Built, measured, dropped.*
- marshal-pricing.md · A higher `RELIEF_IN_FLIGHT` weight against people, and council/knower's `_blind` model of a person. *A fleet landing next door is relief too.*
- marshal-pricing.md · Porting `_evacuate`'s defensive fixes to thinker and knower (unmeasured). *Two doomed neighbours, and what a retreat is worth.*
- marshal-joint.md · The guard at 0.45 under the 0.85 pour floor; D at defender advantage 1.5 (its one losing cell); a pour floor shaped by lane length. *Screening against the roster.*
- marshal-cma.md · A second CMA run from what shipped (it had not converged; `DEFEND_PAD` below 0, `OVERWHELM` and both pads on their bounds); free-for-alls and defender-advantage cells in training. *What this says about tuning marshal.*
- win-turns.md · marshal: the land-grab relay, a counter-punch the turn after a garrison leaves, three-seat stalls; `FEED` 20 if the roster moves; the full ladder not re-run. *What is left*, *A second round, nothing shipped.*
- actuary.md · Variance-seeking while behind on both income and ships (unmeasured). *Behind on income, ahead on ships.*
- learner.md · Whether the failed gate is a power problem, and a strike curve that tells the bots apart. *The gate.*
- learning.md · Improving Learning's memory against people (it stays; dropping it is decided against). Blocked on data: too few posted games against actuary at Learning to measure. *Memory off: the prior alone.*
- learning.md · The counter-strike's people-profile question: people sit far from the prior on emptying frontiers. *The counter-strike.*
- par.md · Wider beams, candidates that aren't some bot's idea, a time budget for knower rivals. *Next, if it goes further.*
