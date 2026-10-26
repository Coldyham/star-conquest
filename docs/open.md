# Open work

Work proposed or measured but neither shipped nor rejected, and decisions left
to the author. Grep it like [`sections.md`](sections.md): each line starts with
the design file that holds the detail and ends with its section. When an item
ships or is dropped, delete its line here (and add it to
[`decided-against.md`](decided-against.md) if it lost).

## Decisions left to the author
- leaderboard.md · A hard cutover that marks unkeyed logs posted well after the keyed-dice deploy as unverified; needs the real deploy date plus a grace period for stale service workers. *Checked scores.*
- learning.md · Whether to drop Learning's memory (and with it the oracle claim): it beats the prior alone nowhere against bots; untested against people. *Memory off: the prior alone.*
- gauntlet.md · Whether a stage changed by a boon may score as a different setup; decide before boons are built. *Carry-over.*
- bot-api.md · Who builds a compiled bot, the ladder's fixed baseline roster, and whether any bot may be slow. *Open questions.*
- settings / menu · Whether the setup menu exposes the fragile config (production-weights dict, world-size ratios) and cosmetics ("Pass 3"); a deliberate pause, not an oversight.

## Proposed, not built
- gauntlet.md · The roguelite run: a seeded run graph, stages dealt like the campaign's, difficulty by depth. *The gauntlet: proposed, not built.*
- gauntlet.md · Targets on lanes, and a roster bot's score as a dealt run's targets; the generated-gauntlet web build is untimed. *Stage targets*, *Generated gauntlets.*
- campaign.md · Campaign fleets: real-time lanes on the meta-map; identity is the open problem. *Campaign fleets (proposed, not built).*
- setup-families.md · Stamping the config a setup was opened from on the score (`scores.from_config`). *Proposed, not built.*
- bots.md · duelist (the simultaneous move as a matrix game), apprentice (a learned evaluator), riposte (the counter-punch). *New bot families (proposed 2026-10-05).*
- leaderboard.md · A full board snapshot per turn: set aside, not ruled out. *Checked scores.*
- external-bots.md · The in-app bot maker: built on branch `bot-maker`, not merged. *The in-app bot maker.*

## Bot research
- ladder.md · The slow cell (24 nodes, 3 ly/turn) predates actuary; `tests.sim` has no ship-speed flag to re-run it. *Re-running it.*
- knower.md · Bait openings at Search (a wall between two rivals emptied): a lean on screening seeds; confirm on fresh seeds and read at Council. *Baiting in a free-for-all.*
- knower.md · Not built: the remnant strike, and a person's launch folded in as uncertain. *Pile-ups under the garrison-fights-last rule.*
- knower.md / evolved.md · evolved as a borrowed candidate at Search (Council already borrows it). *evolved as a borrowed candidate (2026-10-19).*
- council.md · actuary at Hunt in Field's place: stronger, but far past the browser's search guard; Hunt and evolved together untested. *actuary at Hunt in Field's place.*
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
- learning.md · The counter-strike's people-profile question: people sit far from the prior on emptying frontiers. *The counter-strike.*
- par.md · Wider beams, candidates that aren't some bot's idea, a time budget for knower rivals. *Next, if it goes further.*
