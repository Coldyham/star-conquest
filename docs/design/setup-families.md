# Setup families

How the board groups setups beyond "same settings, different seed". The main
list has three levels: **By game** (one card per map), **By config** (the same
setup with the seed set aside, `sc_config_key`) and **By family** (configs
linked to the older config each was most likely branched from). Every card also
carries a **pace** chip. Code: `leaderboard/js/families.mjs` (pure), drawn by
`home.mjs`'s `familyCard` and `paceBadge`.

## The problem

Under `sc_config_key` (`leaderboard/README.md`, "Same setup, different seed"),
two setups one slider apart are unrelated, and so is the same setup against a
different bot. Most configs on the board were made by branching: play one, then
tweak generation or speed, or change systems and speed together to keep lanes
the same length. The two existing views showed none of that.

The 2026-10-14 reading of the live board: **62 configs** over 142 maps. The
family view (with first sightings derived from the games) groups them into
**28 families**, the largest of 9 configs.

## Pace

The median lane in turns at the starting speed. Systems and ship speed set it
between them, so it is the one number a "same lanes, more systems" rescale
keeps (`bots.md`, "Lane length across the parameter space"). The board cannot
run `mapgen`, so `paceOf` estimates it:

    turns = ceil(PACE_LY / sqrt(min(systems, STANDARD_MAX_NODES)) / speed)

Systems come from `format.mjs`'s `systemCount` (a symmetric map builds whole
sectors). The cap matches `config.world_side`: past 40 systems the box grows,
so the spacing holds at 40's. `PACE_LY = 77` is the median of
`median_lane_ly × √systems` over the live board's 139 generated maps. On those
maps the estimate is exact on 125 and a turn off on 14. On a 336-map grid
(6–40 systems, 3–19.5 ly/turn) any figure from 74 to 79 is exact on about 81%.
The misses are inherent: a ceil lands either side of a lane length that sits
on a turn boundary. `tests/test_leaderboard_sync.py` holds the formula to
within a turn on a 72-map grid, and to at least 75% exact.

Bands are **Fast** (1–2 turns), **Standard** (3–4) and **Long** (5+). The chip
reads `Standard · ~3t`, where the `~` marks an estimate. Its title adds "ships
speed up" when speed growth is on, since pace is taken at launch on turn 0. A
hand-drawn map gets no chip, because no formula knows its lanes.

## The family tree

`familyForest` orders configs by `first_seen` (then `config_key`). Each
config's parent is the **nearest older config** by `diff`'s cost, if that cost
is at most `MAX_EDIT` (3). Otherwise the config is a root. On a tie, the
earliest candidate wins.

Costs:

| Change | Cost |
| --- | --- |
| Players | 3 |
| Hand map added, removed or redrawn | 3 |
| Mode, or fog (both ranges count as one field) | 2 |
| Any other knob, systems (the stepper's `nodes`), layout (symmetric only) | 1 each |
| Opponents (the `bots` column) | 1 |
| Tuned AI (`ai`) | 1 |
| Systems and speed together, keeping the pace | 1, not 2, shown as "Rescaled" |

Opponents are a light edit, so a config that differs only in its bots is the
child of its base config. Systems are compared on `nodes`, the value the player
moved, so a mode switch is not also charged for the sectors it builds.

Values compare by value, never as text, and a missing key reads as
`SETUP_DEFAULTS` (`config.mjs`, pinned to `Settings()`). `settings_json` is
pruned, and a Python `1.0` must equal a browser `1` (`core.md`).

**Why "nearest older" rather than clustering.** A parent is only ever older,
so a config played later can never move an existing one to another family.
Only an admin delete or a weight change reshapes the tree, and nothing is
stored, so re-tuning the weights needs no migration.

**What it shows on the live data.** The 5-system "Bare minimum" duel heads a
family of nine. "Mini marshal" (speed 6→19.5, vs marshal) is its child, a
rescale to 7 systems hangs off that, and a 24-system duel at speed 3 is a
direct child marked "Rescaled": 5 systems at 6 ly/turn and 24 at 3 both pace
at about 6 turns.

**Where a guess can still be wrong.** The nearest older config can be a
coincidence. The "made from" stamp below would fix that for new games.

### Data and fallback

`config_summary.first_seen` is `min(first_seen_at)` over the config's scored
maps, appended as the view's last column. A board whose schema predates it
gets the same figure from `game_summary` (`listing.mjs`'s
`FIRST_SEEN_QUERY`, folded in by `withFirstSeen`) at the cost of one more read.
With neither, `last_activity` stands in, which orders badly: a config
replayed yesterday reads as new.

### The view

`?group=family` (`listing.mjs`'s `viewKind`). It reads every config at once
(`familyQueries().all`), since a tree needs all of its members, so it has no
"Show more". The filters run as the By config query cut to `config_key`
(`familyQueries().match`). A family with no matching member is dropped, and a
non-matching member stays in its tree, dimmed. Families come most recently
active first, and Order flips that. Each row links to the config's own list,
the existing drill-down. "Random setup" stays on By config.

## Decided against

- **Folding opponents into one setup on the existing views.** It was proposed
  as a first lens, as the 62 configs fold to 53 without bots. Turned down: the
  existing views stay as they are, and opponents became a light edit in the
  family tree instead.
- **A "By ruleset" view** (economy, combat and fog only: 16 rulesets, two of
  them holding 30 of 53 setups). Not taken up for the same reason.
- **A 2-D map of all setups** (MDS, t-SNE). The axes mean nothing, and every new
  config moves every point.
- **Clustering** (k-means, hierarchical). Membership shifts as data arrives, so a
  player's family would change under them.
- **A family id on `Settings`.** It would move every challenge key.
- **Grouping families on `sc_config_key` text.** The text splits on how floats
  were written, so families compare values instead.
- **Recording where a setup came from** (`scores.from_config`): the menu knows
  which setup it was opened on before the player edited it, and a stamp on the
  score (per-score metadata like `campaign_start`, never a `Settings` field)
  could override the guessed parent. Unnecessary: the parent is derived well
  enough already, and the stamp would be one more legacy case to read.
- **Storing the exact median lane per game** (the bot-replay job builds every
  map). It is exact, but needs a column and leaves new maps blank until the job
  runs. The estimate is within a turn.
