# Glossary

These are the words players see, and the names the code, tables and docs use for
the same things. Use the player's word in anything on screen (game, board,
briefings, tutorial) and in prose about what players see. Where the code name
differs, keep it in code, never on screen. Grep this page; don't read it whole.

## The map and what is on it

| Player sees | Means | Code / tables | Never on screen |
| --- | --- | --- | --- |
| **system** | a star on the map: owned, produces ships | `state.systems`, `Settings.nodes` (the count), `--nodes` | node (except in the campaign), star, planet |
| **lane** | the connection between two systems (a gauntlet's stages are joined by lanes too: Gauntlets, below) | `Lane`, `model.lane_key` | edge, spacelane, route |
| **home system**, **home** | a seat's starting system: "Make home system" and "Auto home systems" in the map maker, "your home" in the tutorial | `HOME_PRODUCTION`, `_make_homeworld`, mapgen's sector index 0; "homeworld" in code and docs | homeworld, capital. The campaign's **home** is a node (below), so in campaign text say home system |
| **ships**, `12sh` | the one unit, counted | `ships` | troops, units |
| **fleet** | ships in flight down a lane | `Fleet` | |
| **garrison** | the ships sitting at a system | `system.ships` | |
| *(nothing)* | a ship that production has just finished this turn | `FadingHull`, `event.hulls`, "hull" in `turnfilm.md` and CLAUDE.md | hull |
| **production** | how many turns a system takes to build one ship (`3/ship`) | `production`, `prod_progress` | |
| **neutral** | a system nobody owns | player id 0 | |
| **map** (the play area) | what's drawn on screen during a game: "below the map", "Shows on the map" | `play_rect` | board |
| **Map type** | Random, Symmetric, or a drawn (custom) map | `Settings.mode` | Map (on its own as a filter label) |
| **ship speed** | light years a ship crosses per turn | `ship_ly_per_turn`, `config.ship_speed` | fleet speed |

## Setups, maps and games

Each level contains the one below it: a **family** of setups, a **setup**, a
**map** (a setup plus a seed), and **games** played on that map.

| Player sees | Means | Code / tables | Never on screen |
| --- | --- | --- | --- |
| **setup** | everything chosen in the setup menu except the seed. The board's "By setup", "134 setups", "Random setup", "Name this setup" | `Settings` minus `seed`; `configs`, `config_key`, `sc_config_key`, `configTitle`, `?group=config` | config |
| **settings** | the same as setup. Use it only on the "Change settings" button | `Settings` | |
| **tweaks** | a setup's fields changed from the default ("9 tweaks") | `setup.mjs` `tweaks()` | |
| **family** | setups linked to the older setup each was most likely branched from | `families.mjs`, `setup-families.md` | |
| **pace** | a setup's estimated speed of play, from systems and ship speed | `PACE_LY`, `paceLabel` | |
| **map** | a setup plus a seed: the thing with a leaderboard. "All maps", "Play this map", "By map", **New map** (same setup, new seed) | `Settings.challenge_key()` (Python) = `game_key` (SQL, `games` table, `game.html?key=`), `?group=game` | game, board |
| **game** | one play-through of a map: its log, history and result | `GameLog`, `GameState`, `game_logs` | |
| **the game** | the app ("Open in the game"), paired with **the board** | `starconquest/`, `/play/` | |
| **match** | a play-by-post game only: "Shared matches", seat links | `pbp_*`, `pbp.Match`, `matchnames` | (a single local game is a game) |
| **map link** | a link that opens a map | the token (`token-encode.mjs`, `Settings.to_token`) | game link |
| **challenge link** | a map link carrying a score to beat ("Challenge failed" if you miss it) | `Settings.challenge`, `Challenge` | |
| **drawn map** / **custom map** | a hand-authored map from the map maker ("Create map", "Edit map") | `CustomMap`, `custom_map`, mode `"custom"` | |

`config` on its own means `starconquest/config.py` (balance and look constants,
read live). Never write "a config" in docs when you mean a setup. The board's
code keeps `config` for setups (the `configs` table, `config_key`, URL
`?group=config`), so don't rename those: stored keys and shared links depend on
them.

## Seats and opponents

| Player sees | Means | Code / tables |
| --- | --- | --- |
| **player** | someone in the game, human or bot ("3 players") | `Player`, `Settings.players` |
| **seat** | a player's place, as numbered in play-by-post links and the map maker ("Seat links", "Renumber seats") | `seat`, `Player.id` |
| **rival** | another player, from your point of view (the docs prefer rival to opponent) | |
| **bot** | a computer player, named by family (`knower`, `actuary` …) | `ai.STRATEGIES`, `models/` |

## Orders

| Player sees | Means | Code |
| --- | --- | --- |
| **send** | a one-off launch from the send popup | `Order` |
| **route** / **forwarding rule** | a standing order that sends a system's new ships on | `forward_*`, `Ui.forward_this_turn` |
| **Auto-route** | routes picked for your threatened systems (or, with none, your border with neutral systems) | |
| **Hint** | marshal's moves for your turn, shown on the map (K); a hinted game is never posted. Not a gauntlet stage's **hint** | `Ui.hinted` |

## Gauntlets

A gauntlet's stages are joined by **lanes**, as a map's systems are; the code
and the file format call both `lanes`. The two rarely share a screen: the maker
and the gauntlet list show no systems, and only the win screen draws a run's
stages over a finished map. Never call a stage's lane a link: the maker's stage
card already has a **map link** on it.

| Player sees | Means | Code / tables | Never on screen |
| --- | --- | --- | --- |
| **gauntlet** | pinned maps played in order, each a **stage**, joined by lanes | `gauntlet.py`, `Gauntlet`, `gauntlets` | |
| **stage** | one map of a gauntlet; **cleared** when won (within its **target**, if it has one) | `Stage` | level, node |
| **stage number**, `2-1` | where a stage sits on the graph: its column, then its row when the column holds more than one ("2" alone). An untitled stage is named by it ("Stage 2-1") | `gauntlet.cell_names`, `cellNames`, `Stage.at`, a node's `place` | the stage's index or id |
| **lane** (between stages) | the connection from one stage to another: winning the first opens the second ("+ Add lane", "Leads to", "Reached from") | `Gauntlet.lanes`, the body's `lanes`, `gauntlet.ways`, `joinLane`, `sg-lane` | link (that is a map link), edge, path, connection |
| **two-way** lane | a lane either end of which opens the other; in the maker, both "Clearing A opens B" boxes ticked | `two_way` | |
| lane that **opens neither stage** | the maker's lane with both boxes unticked: drawn red, and Save refused until one is ticked or it is removed. Never stored | a draft lane's `shut`, `sg-shut` | dead lane |
| **start** | a stage open from the beginning of a run ("Open from the start"); a bar on its left | `Gauntlet.start` | |
| **★** ("ends the gauntlet") | a stage whose win finishes a run: one no ungated lane leads on from | `gauntlet.goals`; **goal** in docs | |
| **target** | the turns, and ships lost, to win a stage within ("Win in 12 turns, losing at most 30 ships"; `≤12` on the graph). A stage's target must be met before any lane out of it opens | `Stage.target`, `gauntlet.Target`, `targetText` | par (that is the puzzle's) |
| **gate** | a target on one lane: the lane opens only when the stage before it is won within it ("Gate (what opens it)", "Replay for its gate", "gate met") | a lane's `target`, `gated`, `sg-gated` | lock, requirement |
| **Gated**, **Shortcut**, **Bonus** | a gated lane's label: Bonus when the stage it opens leads to no ★, Shortcut when it skips a column, else Gated | `gauntlet.lane_label`, `laneLabel`, `BONUS`/`SHORTCUT`/`GATED` | |
| **bonus stage** | a stage reached only through a gate, off the way to any ★ | | secret stage |
| **hidden** lane | a gated lane not drawn until its gate is met ("Hidden until met") | `hidden`, `hiddenStages`, `sg-hidden` | secret lane |
| **dead end** | a stage with no lane out that is not a ★; by default it grants a bonus life | `gauntlet.dead_end` | |
| **lives**, **♥** | how many losses a run can take; a **bonus life** is one more, granted the first time a run wins a marked stage | `Gauntlet.lives`, `run.lives_left`, `Stage.bonus_life`, `gauntlet.grants_life` | hearts, health |
| **run** | a gauntlet played from a start to a ★ over any stage it has opened, on the gauntlet's lives | `run.py`, `Run` | attempt |
| **opened**, **locked** | a stage a run may play, or one it hasn't reached | `run.frontier`, `sg-locked` | unlocked |
| **the way on** | the stages a won stage's lanes open, chosen on the win screen ("choose the way on below"; **Next stage** plays the pick) | `run.next_up`, `gauntlet.onward` | |
| **briefing** | the card shown as a stage starts | `Stage.briefing` | |
| **hint** (a stage's) | the one line beside a gauntlet stage's map. Not the **Hint** (K) under Orders | `Stage.hint`, `app.stage_ui` | tip |
| **shape** | a generated gauntlet's pattern of lanes: One path, Concertina, Cross-stitch | `gauntletgen.py`, `gen-shape` | topology |
| **generated** gauntlet | one whose maps the game picks and orders, from rules set on the board | `gauntletgen.py`, `gauntlet-spec.mjs` | |

## The campaign and the daily

| Player sees | Means | Code / tables |
| --- | --- | --- |
| **campaign** | the weekly contest map of **nodes** (each node is a map) | `campaign.py`, `campaign_games` |
| **node** (campaign only) | one map on the campaign's graph: a **home** you claim, or a **field** node | `graph["nodes"]`, `kind` |
| **the daily** | one day's pair: the **daily map** ("Daily map", the Map tab, "Monday's map") and the **daily puzzle** (a small duel) | `daily.py` `BOARD`/`PUZZLE` (kind `"board"`/`"puzzle"`, link `#daily`/`#puzzle`); `dailies` |
| **Daily challenge** | the heading over both dailies on the board's daily pages | `daily.html`, `puzzle.html` |
| **par** | a puzzle's best known result, told only to players who meet it | `puzzle_pars` |
| **streak** | consecutive daily wins of one kind | |

## The board

| Player sees | Means | Code / tables |
| --- | --- | --- |
| **the board** / **leaderboard** | the scores site (`/board/`), never the map | `leaderboard/` |
| **turns**, **lost** | a score: fewest turns wins, fewest ships lost breaks a tie | `scores.turns`, `scores.lost` |
| **crown** | holding the record on a map that two or more players have posted on; a **steal** is taking one | `crowns.mjs` |
| **badge** | a derived achievement | `badges.mjs` |
| **embargo** | a map's replays hidden until a date | `game_embargoes`, `embargo_until` |
| **tag** | a word describing a setup (`#fun`) | `configs.tags` |
