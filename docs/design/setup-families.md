# Setup families (proposed, not built)

How the board could group setups beyond "same settings, different seed". The
mockup that goes with this note was built on the live board's public tables as
of 2026-10-14, and the figures below come from the same read
(`config_summary` and `game_summary` with the publishable key, the same reads
`tools/config_census.py` makes).

## The problem

A config is `sc_config_key`: every field of `settings_json` except `seed` and
`autoplay`, hashed as text (`leaderboard/README.md`, "Same setup, different
seed"). Two setups one slider apart are strangers, and so is the same setup
against a different bot. Most configs on the board were made by branching: play
one, then tweak generation or speed, or change systems and speed together to
keep lanes the same length. The board shows none of that.

The 2026-10-14 reading: **62 configs** over 142 maps. With opponents folded
together they are **53 setups**. Linked by lineage (below) they form **24
families**, the largest of 8 setups. Economy, combat and fog alone give **16
rulesets**, and two of them hold 30 of the 53 setups: the default rules, and
fog with home production 2, combat jitter 0.08 and defender advantage 1.05.

A 2-D picture of the whole space was ruled out from the start. What works is
cutting the key into layers that each mean something to a player.

## Four lenses

### Opponents fold in

A setup key is the config key with every seat's `ai_strategy` and `ai` removed.
A config row then lists its opponent lineups as chips under one setup. This
also removes a spurious split: two configs on the board differ only in seat 1's
strategy or knobs, which only matter under autoplay. Seat 1 should be dropped
along with the rest.

### The family tree

Each setup's parent is the **nearest setup first seen before it**, under a
weighted field distance, provided that distance is at most 3. Otherwise it is a
root. The weights used for the mockup are: players and hand maps 3, mode and fog
2 (fog counts as one field, not two ranges), and every other field 1. An edit
to systems and speed together that keeps the median lane in turns within half a
turn counts as one move, labelled "Rescaled".

Why "nearest older" rather than clustering: the parent is chosen among older
setups only, so a new game can never move an existing setup to another family.
Only an admin delete or a weight change reshapes the tree. Nothing is stored, so
re-tuning the weights needs no migration.

What it got right on the live data: 29 of 53 setups joined a family, 13 of them
by a single change (random to symmetric, fog on, fewer systems). The 5-system
"Bare minimum" duel heads a family of seven, including "Mini marshal" (speed
6→19.5) and a symmetric copy. What it got wrong: a 24-system duel at speed 3
hangs off the 5-system one because the player count matches and little else
does. A nearest older setup can be a coincidence. "Recording where a setup came
from" fixes that for new games.

### Rulesets

Economy (`home_*`, `garrison_*`, `neutral_produces`), combat (`combat_jitter`,
`defender_advantage`, `in_lane_battles`) and fog decide what kind of fight it is.
Shape, size, generation and speed decide where it happens. A "By ruleset" view
groups every board played under one set of rules.

### Pace

The median lane in turns folds systems and speed into one number (`bots.md`,
"Lane length across the parameter space"). Bands for display: Fast (1–2),
Standard (3–4), Long (5+). The board cannot run `mapgen`, so it would use a
fitted formula. The table in `bots.md` gives a median lane of about
78 ly / √systems (systems capped at 40, past which the box grows), divided by
ship speed. A Python test would pin the formula against real boards across the
census grid, as `test_leaderboard_sync` pins other mirrored pairs.

## Plan

1. **Fold opponents and show pace.** Board only. Add a pure `leaderboard/js/families.mjs`
   beside `setup.mjs`, tested under `leaderboard/tests/`. Compare by value,
   never as text, because Python writes `1.0` where the browser writes `1`
   (`core.md`). A field missing from the pruned `settings_json` reads as the
   default.
2. **Add the family tree and a "By family" view.** `config_summary` gains
   `first_seen` (new view columns go last; `schema.md`, "Re-pasting upgrades a
   board"). The tree is computed in the page. A family is named after its first
   named member. A config page gets a family panel: ancestry with the edit at
   each step, the same setup against other opponents, children and siblings.
3. **Record where a setup came from.** The menu knows which setup it was opened
   on (a share link, Play a new seed, the last game) before the player edited
   it. Stamp that config key on the score as a claim (`scores.from_config`),
   the same kind of per-score metadata as `campaign_start`. It must never
   become a `Settings` field, because that would move `challenge_key()` for
   every map ever shared. Where it exists it overrides the guessed parent.
4. **Rulesets as first-class (optional).** Let rulesets be named like configs
   (`configs` is first name wins) and let crowns or the campaign filter by them.

## Decided against

- **A 2-D map of all setups** (MDS, t-SNE). The axes mean nothing, and every new
  config moves every point.
- **Clustering** (k-means, hierarchical). Membership shifts as data arrives, so a
  player's family would change under them. A parent chosen among older setups
  cannot shift.
- **A family id on `Settings`.** It would move every challenge key. Use
  per-score metadata, for the same reason `Challenge` is excluded from the key.
- **Grouping families on `sc_config_key` text.** The text splits on how floats
  were written. Families compare values.
