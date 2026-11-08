# Glossary

These are the words players see, and the names the code, tables and docs use for
the same things. Use the player's word in anything on screen (game, board,
briefings, tutorial) and in prose about what players see. Where the code name
differs, keep it in code, never on screen. Grep this page; don't read it whole.

## The map and what is on it

| Player sees | Means | Code / tables | Never on screen |
| --- | --- | --- | --- |
| **system** | a star on the map: owned, produces ships | `state.systems`, `Settings.nodes` (the count), `--nodes` | node (except in the campaign), star, planet |
| **lane** | the link between two systems | `Lane`, `model.lane_key` | edge, spacelane, route |
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
| **Auto-route** | routes picked for your threatened systems | |
| **hint** | marshal's moves for your turn, shown on the map; a hinted game is never posted | `Ui.hinted` |

## Gauntlets, the campaign and the daily

| Player sees | Means | Code / tables |
| --- | --- | --- |
| **gauntlet** | pinned maps played in order, each a **stage** | `gauntlet.py`, `gauntlets` |
| **stage** | one map of a gauntlet; **cleared** when won (within its **target**, if it has one) | `Stage` |
| **run** | a gauntlet played in order, with one life | `run.py` |
| **campaign** | the weekly contest map of **nodes** (each node is a map) | `campaign.py`, `campaign_games` |
| **node** (campaign only) | one map on the campaign's graph: a **home** you claim, or a **field** node | `graph["nodes"]`, `kind` |
| **the daily** | one day's pair of dailies: the daily map (**"Daily board"**, still to be renamed) and the **puzzle** (a small duel) | `daily.py` `BOARD`, `PUZZLE`; `dailies` |
| **Daily challenge** | the board's heading for the daily page; renamed along with "Daily board" | |
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
