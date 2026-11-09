# Open work

Work proposed or measured but neither shipped nor rejected, and decisions left
to the author. Grep it like [`sections.md`](sections.md): each line starts with
the design file that holds the detail and ends with its section. When an item
ships or is dropped, delete its line here (and add it to
[`decided-against.md`](decided-against.md) if it lost).

## Decisions left to the author
- campaign.md · Whether to swap `playerHue`'s hashed hues for something that keeps one week's players apart (two names can land on near-identical colours on the campaign map); any change recolours every past week. *The week's replay.*
- leaderboard.md · A hard cutover that marks unkeyed logs posted well after the keyed-dice deploy as unverified; needs the real deploy date plus a grace period for stale service workers. *Checked scores.*
- bot-api.md · Who builds a compiled bot, and whether any bot may be slow (the baseline roster is `ladder.md`'s). *Open questions.*
- campaign.md · Whether to bring back campaign fleets once claimed names are taken up widely: opt-in identity, whether arrival needs a notification, and whether wait timers make it a chore. *Revisit if claimed names take hold.*
- campaign.md · The daily allowance of moves: whether to build it, its rate (`ceil(field / 4)` fitted to one solo player on 12 nodes), whether a home claim starts the bank, and whether a retake outside the grace costs less. *A daily allowance of moves (proposed).*
- settings / menu · Whether the setup menu exposes the fragile config (production-weights dict, world-size ratios) and cosmetics ("Pass 3"); a deliberate pause, not an oversight.

## Proposed, not built
- html-frontend.md · The HTML frontend after the parity baselines: Python seams (`app`, session, effects), the Pyodide spike and its gate, then each screen to parity at `/next/` and cutover. *How it is staged.*
- leaderboard.md · Watch links on an owner's own scores while their map is embargoed: `replay.mjs` asking Auth who calls, as `pbp.mjs` does, and serving that owner's logs past `public_replays`. *Claimed names.*
- training.md · Drill stages and a tutorial stage each for rusherplus, evolved and knower; choosing the hint's advisor. *Proposed, not built.*
- gauntlet.md · The roguelite run beyond the linear run built in *Runs*: branching, lane gates, lives and boons (`graphical-gauntlet-maker.md`), and a seeded run graph with stages dealt like the campaign's. *The gauntlet: proposed, not built.*
- shell.md · The phone's setup menu at device resolution, if the panel's height can be had without a pixel scan; the idle side panel's how-to cut off on a landscape phone; the same send-finishes-its-source rule on desktop. *Phones: the compact layout.*
- gauntlet.md · Lanes are built (*Targets on lanes*) but authored by hand only: the board page edits order and warns on a branching gauntlet, and the menu lists stages rather than drawing the graph (Phases 2-3 of `graphical-gauntlet-maker.md`); the generated-gauntlet web build is untimed on a phone. *Targets on lanes*, *In the game.*
- bots.md · duelist (the simultaneous move as a matrix game), riposte (the counter-punch). *New bot families (proposed 2026-10-05).*
- leaderboard.md · A full board snapshot per turn: set aside, not ruled out. *Checked scores.*
- daily.md · First tries: the game counts local attempts at a day's key and stamps the count on the score, and the board marks first-try wins. Also: offering `lane_turns` on the board's gauntlet form. *On the board*, *Ship speed follows the map*.
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
- par.md · A stronger bound (ships needed through neutrals), a relay as one decision, successive halving for openings, node budgets instead of time, a time budget for knower rivals. *Next, if it goes further.*
