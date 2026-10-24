# Gauntlets and the tutorial

A gauntlet is an ordered string of pinned setups, each with a short briefing and
a one-line hint, played in order. The tutorial is one
(`starconquest/assets/gauntlets/tutorial.json`), read by the pure core module
`starconquest/gauntlet.py`. Players make their own on the board's gauntlet page
(`leaderboard/gauntlet.html`), which stores them and hands out a short link; see
"Gauntlets made on the board". The same file format is meant to carry a later
single-player run with branching graph navigation, carry-over between stages
and score targets that gate the way on; that part is proposed below and not
built.

Planned on 2026-10-21; the plan's decisions are recorded here with their
reasons.

## The tutorial ladder

New players met the full setup menu and a bot with nothing to ease them in. The
tutorial is ten stages, easiest to hardest, each bringing in one idea. Teaching
is a briefing card when the stage opens and a hint in the side panel, and
nothing else. Popups fired by game events were judged too obnoxious.

**A gauntlet is data, never `Settings`.** Stage identity never becomes a
`Settings` field. A stage *is* its setup, identified by
`Settings.challenge_key()`. So no challenge key moves, there is no
`_LEGACY_KEY_DROPS` entry, and a stage won by hand posts to the leaderboard as
the ordinary game it is: posting needed no change at all.

**Progress is derived, never stored.** A stage is cleared when
`webstore.best(*stage_settings.challenge_keys())` is not None: a by-hand win on
that exact setup, filed by `main.record_best` on the same `Ui.can_post` gate as
posting. There is no new webstore key. Editing a stage's setup in a later release
makes it a new, uncleared stage, which is the honest answer: the old best was
scored on a different map.

**Where progress lives.** Bests are `localStorage` on the web build
(`webstore.get`/`set`) and `kv.json` under `data_dir()` elsewhere, never
pygbag's MEMFS, so tutorial progress survives a reload and a restart. What it
does not survive is cleared site data, a private window, or a move to another
browser or device, which the stage list says on the web build (the same
storage remembers a play-by-post name, `webstore.pbp_name`). A board page for gauntlets, like the campaign's, would carry
progress across all of those, but only by posting scores, and a beginner should
not have to publish a game to keep a tutorial. If carrying progress between
devices is wanted later, the middle ground is an opt-in "restore from my posted
scores", read from verified `scores` by name, which leaves the local reading as
the default.

**A stage is active only while the menu's settings still match it**, judged by
challenge key (`gauntlet.stage_of`, read through `menu.active_stage`), the way
`Challenge.matches` judges a link. Moving one knob means you are no longer
playing the stage. The banner warns; it does not lock, for the reason the
challenge banner gives (locking is a dead end the moment someone wants the same
map with one knob moved). A challenge link's setup is never a stage, even one
that happens to match, because its banner owns the subtitle slot.

**Built-in stages pin every `Settings` field**, so a later change to a `config`
default cannot quietly change the tutorial. The file is what
`Settings.from_dict` gives back, field for field
(`test_every_builtin_stage_pins_every_settings_field`).

**Picking a stage stashes your own setup once** (`MenuState.stash`), since
`copy_from` overwrites every field. Leave tutorial puts it back if the menu is
still on a stage; if you have already changed the stage into something else,
what is there stays, since that is the setup you were last working on.

**A stage's seed is pinned, so New map is not offered in one.** The live footer
drops it and N does nothing. On the win overlay its place is **Next stage (N)**
when the win cleared the stage (`Ui.offers_next_stage`), and empty after the last
stage. Retry stays. A Retry or a rewind keeps the stage, without the briefing;
a resumed save has none, since nothing remembers that it was one.

**Next stage is the one neighbour further from the start** (`gauntlet.next_stage`,
by depth), not "the one open, uncleared neighbour" the plan named: judged that
way, replaying a stage you had already cleared would offer no way on. More than
one deeper neighbour is a fork, and sends the player to the list to choose.

**The briefing card** is drawn over the board on turn 0 while
`Ui.briefing_open`, measured and centred on the play area. Any press closes it,
and is spent doing so, so the press that dismisses it cannot also select a
system or end the turn behind it. B, or a tap on the hint, brings it back. The
card says so in its footer, because the side panel has no row to spare for it
on the touch build.

**The hint** sits above the legend whenever nothing is in focus, at most
`gauntlet.HINT_MAX` (90) characters. On the touch build an empty panel holds 23
rows, the legend takes 18 and a 90-character hint four, so the hint carries no
second line of its own (`test_a_stage_hint_fits_the_empty_panel`). A stage
with a briefing but no hint (every generated stage, or a board stage left
blank) gets one from `main.stage_ui`, "Tap here to read the briefing again",
so the card can always be brought back.

### The stages

| # | Stage | Idea | Against |
| --- | --- | --- | --- |
| 1 | First contact | select, send, take neutrals (8 systems, no fog) | heuristic |
| 2 | Supply lines | forwarding rules and route mode, early because they help new players most | heuristic |
| 3 | Caught in flight | travel time (ships at 4 ly/turn): ships in flight leave home open | rusherplus |
| 4 | Defender's edge | defender advantage, the Combat tab preview | rusherplus |
| 5 | Into the dark | the default fog (sight 1, scout 3), and the other settings worth trying | rusherplus |
| 6 | Three's a crowd | three seats, pile-ups, starting between two rivals: push one way, guard the other | heuristic, rusherplus |
| 7 | Full house | five seats on 32 systems | heuristic and rusherplus, two each |
| 8 | Thinker | a bot that prices its fights | thinker |
| 9 | Marshal | one of the strongest bots | marshal |
| 10 | The hunt | actuary at its strongest stop, Hunt | actuary · Hunt |

**How the seeds were chosen.** Each stage's setup was played on 24 candidate
seeds by marshal, evolved, thinker, heuristic and rusherplus in the human's seat
(`tests.sim.play_settings`), and a seed picked per stage for a rising loss count:
none of the five losing stages 1-5, then one, two, three and four. A random map
is decided by position as much as by play: on stage 1's first seed, four of the
five bots lost to the heuristic. `tools/gauntlet_check.py` over the whole roster
then reads, as the share of seven bots that lose each stage: 0, 0, 0, 0, 0, 14,
29, 43, 86, 86%. A bot in the human's seat is a crude proxy for a beginner, so
the check is that the order never falls back, not that the numbers mean
anything to a person.

## The file format

For authors. A gauntlet is one JSON file:

```json
{"id": "tutorial", "name": "Tutorial", "start": [0],
 "stages": [
   {"id": 0, "title": "First contact", "briefing": ["…", "…"], "hint": "…", "settings": {…}},
   …],
 "lanes": [[0, 1], [1, 2], …]}
```

- A stage's `settings` is exactly what the menu's Save writes
  (`Settings.save`): build the setup in the menu, save it, paste it in. Give it a
  fixed `seed`, or the stage changes every time it is played and can never be
  cleared twice.
- A stage's optional `target` is a score to win within before the stages
  beyond it open: `{"turns": 14}`, `{"turns": 14, "lost": 30}`, or a bot's to
  beat, `{"turns": 31, "lost": 12, "bot": "thinker", "beat": true}` ("Stage
  targets", below). `turns` is a positive int, `lost` an int from 0, `bot` at
  most 24 characters.
- `briefing` is a list of paragraphs, at most six of 240 characters each.
  `hint` is one line of at most 90. `title` is at most 32 characters.
- `lanes` joins stages by `id`, as the weekly campaign's graph does. If `lanes`
  is missing, the stages are joined in list order, so a linear gauntlet can
  leave it out. `start` lists the stages open from the beginning (default: the
  first).
- **A lane runs one way**, from its first stage to its second: `[3, 4]` opens 4
  once 3 is cleared, never 3 once 4 is. `[3, 4, {"two_way": true}]` runs both
  ways. The default lanes run in list order, so a linear gauntlet only ever
  opens forwards. A pair joined twice keeps its first lane, so `[4, 3]` after
  `[3, 4]` is dropped, not read as the way back: say `two_way`.
- **Every stage must be reachable**: some start leads to it, following the lanes
  the way they run. `gauntlet.problems` names each stage that is not. The board
  refuses to store such a gauntlet (`checkBody`), `tools/gauntlet_check.py`
  refuses to play one, and every built-in is held to it
  (`test_a_stage_no_start_reaches_is_a_problem`). The game still opens one; the
  stranded stage just stays locked.
- `Gauntlet.from_dict` never raises: it drops a stage with no int `id`, a
  duplicate `id` or no `settings` dict, drops a lane to a missing stage, reads a
  lane's third element only for `two_way: true`, drops a malformed `target`
  (the stage then has none), and clamps text to the lengths above.
- A player's own gauntlets come from two places (`gauntlet.user`): the ones
  opened from a link, kept in `webstore` under `paths.WEB_GAUNTLETS_KEY`
  (`gauntlet.remembered`, newest first, the last eight), and on desktop the
  files in `data_dir()/gauntlets/*.json`. The web build has no folder to read:
  its files are MEMFS and do not survive a reload. One whose `id` is a
  built-in's is ignored. The Tutorial list shows a picker once there is more
  than one, and the menu's button that opens it reads "Gauntlets" instead of
  "Tutorial" (`menu._gauntlets_label`; `MenuState.gauntlets` starts as
  `gauntlet.every()` so the button knows before the list is opened).

`tools/gauntlet_check.py --gauntlet ID` checks that a gauntlet's stages rise in
difficulty.

## Gauntlets made on the board

The board's gauntlet page builds a gauntlet from game links: one pasted link
per stage (the menu's Get Link, or a challenge link with its score dropped),
each with a title, briefing paragraphs and a hint. Saving stores it and gives
the link `/game/#g=<id>`. A `.json` download of the same file is for desktop.

- **Stored, not carried in the link.** A whole gauntlet fits in a fragment (the
  ten-stage tutorial is about 3 KB deflated), but storing it keeps the link short,
  keeps an author's work if a link is lost, and makes a public list of
  gauntlets a flag rather than a redesign. Storage holds what an author wrote,
  never progress: clears stay a read of local bests, as below.
- **Its own table, never `campaigns`.** `campaign_games` and `game_embargoes`
  read every `campaigns` row's nodes, so a gauntlet stored there would act as a
  phantom campaign week and could embargo its stages' replays. Schema notes:
  [`schema.md`](schema.md), "gauntlets".
- **The id is `<slug>-<12 hex>`**: a slug of the name (at most 19 characters)
  and a hash of the canonical file, minted by `/api/gauntlet`. 32 characters at
  most, so it survives `from_dict`'s clamp of `id` intact. Saving the same
  gauntlet twice is the same row; an edit is a new row, and its old link keeps
  opening the old version.
- **One check, run twice.** `leaderboard/js/gauntlet-build.mjs` `checkBody` is
  what the page runs before it offers Save and what the function runs before it
  stores. It refuses what `from_dict` would clamp (title 32, hint 90, six
  briefing paragraphs of 240, 64 stages, 64 KiB), counting as the game counts:
  whitespace collapsed, code points. A stage must carry an integer `seed`.
- **Stages are stored pruned**, as the link carried them, not with every field
  pinned as the tutorial's are: the board has no table of `Settings` defaults.
  If a default moves, the stage becomes a new, uncleared stage, the answer the
  module docstring calls honest. Pruned is also what `games.settings_json`
  holds, so a later `gauntlet_games` view can match stages to scores by jsonb
  equality, as `campaign_games` does.
- **The game fetches once, then keeps the file.** `#g=<id>` asks
  `/api/gauntlet?id=` through `pbp.request`, polled once a frame from `main`;
  `gauntlet.remember` keeps the answer, and the menu opens the Tutorial list on
  it. A kept gauntlet opens without asking again, offline included, and the
  installed app (which relaunches without the fragment) still lists it. Rows
  never change, so a kept copy never goes stale. Nothing is fetched when
  `LEADERBOARD_ORIGIN` is blank.
- **`#gauntlet=<payload>` carries the whole file** (deflated JSON, base64url,
  as a settings token), read by `gauntlet.inline_data`. The page never offers
  it; it is for a build with no board and for tests.
- **Opening `gauntlet.html#<id>` loads a stored gauntlet as a draft.** Remixing
  or fixing one is open, edit, save. A branching gauntlet loads as a linear
  one: the page edits order only, and `lanes` waits for a page that draws them.
- **Listing is an admin's decision at the author's request.** The author can tick
  "Ask for it to be listed publicly" (`wants_listing`); `tools/admin.py
  list-gauntlet` sets `listed`, and only a listed row is readable with the
  publishable key. Nothing reads `listed` yet: it is there for a public
  gauntlets page, so asking needs no migration then.

## Relation to the weekly campaign

No campaign table or rule is shared, deliberately. What is shared is the shape
and the bordering rule. (Stored gauntlets above keep only what an author wrote,
in their own table: no progress, so the argument below still holds.)

The campaign has server state because it is **contested**: other people take
your nodes, so holding is a fold over everyone's verified scores (`fold` in
`leaderboard/js/campaign.mjs`), and "may I move here" is `attemptStatus`, asked
through `/api/campaign`.

A gauntlet is **uncontested and single-player**. Nobody can take a cleared stage
back, so "may I attempt this" depends only on your own wins, and those are
already on the device. A server would add identity, which has no answer on the
board ("no identity on a row"), plus quota and a failure mode, and buy nothing.

What is shared, so the two stay recognisably one idea:

- **The graph shape.** `stages` + `lanes` + `start`, as the campaign's `nodes` +
  `lanes`, so a branching gauntlet needs no format change.
- **The bordering rule, mirrored in spirit.** A stage is open if it is a start,
  is cleared, or a lane leads to it from a cleared stage
  (`gauntlet.open_stages`); otherwise it is locked. Cleared stages stay
  replayable. It is a few lines over local bests, not a port of
  `attemptStatus`, whose rule stays in JS and is never reimplemented.
- **Except that a lane runs one way.** A campaign lane is a border, crossed
  either way. A gauntlet's first read the same, and that surprised in play
  (2026-10-23): stage 6 of the tutorial, won from a board challenge link of the
  same setup, cleared it as it should, and then opened stage 5 as well as 7.
  The lock is soft by design (any win on the setup counts, however it was
  reached), so the fix was to the direction, not the clearing: a lane opens
  only its second stage unless it is marked `two_way`. Clearing 6 first now
  opens 7 and leaves 5 locked until 4 is cleared.
- **Derived, never stored.** Holdings there are a fold of scores; clearings
  here are a read of bests.

What would make a server worth it later is shared or verified gauntlet progress
(a gauntlet column on the board, crowns for a run). That would derive from
verified `scores` the way `campaign_games` does, matching setups by seed and
jsonb equality.

## Generated gauntlets

Proposed on 2026-10-23: a linear gauntlet of rising difficulty dealt from a
**spec** (how many stages, and which setups are allowed) rather than written by
hand. Two parts: a dealer that turns a spec and a seed into setups, and a
benchmark that plays bots on the candidates and picks a rising sequence. It is
the run above, linear and with an author's constraints, and the first step
toward it.

Build order: (1) the spec and dealer in core, (2) an offline
`tools/gauntlet_gen.py`, (3) time it in the web build, then generate in the
game from a spec link, (4) a form on the board that builds the spec link. All
four are built (`starconquest/dealer.py`, `starconquest/gauntletgen.py`,
`tools/gauntlet_gen.py`, the generate form on `gauntlet.html`), except that the
web build was never timed before the game generated in it: the first deploy
preview is that measurement ("In the game", below).

### Where it runs

- **Dealing can run anywhere; measuring needs the engine.** The board has no
  engine and cannot play a game, and its bot column (`bot_scores`) exists only
  for setups someone has posted: a freshly dealt seed has none. A
  `bot_replay`-style queue (store a request, wait for the hourly worker, store
  the answer) would buy an hour's wait and new server state for what the game
  can do itself.
- **The game can measure.** It ships the engine and the roster. Measured on
  2026-10-23 (3 players, default opponents, one bot in seat 1, budgets lifted
  with `ai.set_budget_scale`), a game took 0.01-0.11 s for rusherplus,
  heuristic, thinker and marshal at 13-36 systems, and 1-2.5 s for evolved.
  knower and actuary are far slower and are never probes. Ten stages at four
  candidates each and four probes is 160 games, 10-15 s natively.
- **The web build is unmeasured.** Python in WASM is slower by a factor not
  yet timed, and single-threaded, so in the game the work is spread across
  frames behind a progress bar. Timing it decides how many candidates and
  probes the web can afford, and is step 3's first job.
- **The form belongs on the board.** Checkboxes and ranges are cheap in HTML
  and costly on the menu's fixed pygame canvas. The page builds a spec link
  (`/game/#gauntlet-spec=<token>`) and the game deals and measures.

### The spec

A dict, so it travels as a token like a setup does, read tolerantly
(`dealer.Spec.from_dict` never raises; a bad rule is dropped, a range clamped
to its slider):

```json
{"stages": 8, "seed": 41,
 "rules": {"mode": {"fix": "symmetric"}, "systems": {"range": [13, 29]},
           "ship_speed_growth_pct": {"fix": 0}, "home_production": {"fix": 2},
           "fog": {"fix": false}},
 "opponents": {"not": ["knower"]}}
```

- **A rule is `fix`, `range`, `in` or `not`**, on a `Settings` knob by its
  attribute name (every Advanced and Combat slider, `neutral_produces`,
  `in_lane_battles`) or on one of the dealer's own names: `mode`, `layout`,
  `players`, `systems` (what `Settings.system_count` shows, never raw `nodes`)
  and `fog` (`false` is both ranges at `FOG_MAX_HOPS`; `true` is the Basic
  menu checkbox's preset, `FOG_ON_SIGHT`/`FOG_ON_SCOUT`, unless the ranges
  are ruled or `roll` is on, when one range is drawn short of unlimited).
- **`opponents`** is `in` or `not` over bot names; `name:stop` deals the stop,
  as `RANDOM_POOLS` entries do. Default: `ai.LADDER_ORDER`. Every seat is a
  named bot, never `random`: a stage pins its setup.
- **`roll`** (default false): a knob no rule names is left at its default, or
  with `roll` true rolled over its whole slider as `_mystery` rolls them (map,
  travel, economy and fog; combat only when ruled), with mode, layout, players
  and systems (up to `STANDARD_MAX_NODES`) rolled too. Off by default for the
  reason "The first run" gives.
- **No `Settings` field is added**, so no challenge key moves, and a dealt stage
  is an ordinary setup that posts to the board as itself.
- **`dealer.problems(spec)`** names a spec nothing can satisfy (an empty range,
  `players` above what `systems` allows, no opponents left).

### Dealing

`dealer.deal(spec, rng, depth)` is a pure function of the spec, the rng and a
depth from 0 (first stage) to 1 (last). It draws each ruled field, rolls the
rest, sets `nodes` so `system_count` lands in range (rerolling a symmetric map
that rounds out of it), seeds with `settings.roll_seed`, and rerolls a setup
`setup_warnings` objects to. **Opponents follow depth**: the allowed bots are
ordered weakest first by `LADDER_ORDER` and each seat draws near
`depth * (n - 1)`, so difficulty rises by design and the benchmark checks it.
That matters because a random map is decided by position as much as by play
(the tutorial's seed hunt, above).

### The benchmark

1. **Deal more candidates than stages**: `--candidates` per stage (default 4),
   each at its stage's depth.
2. **Probe every candidate** with fixed bots in seat 1 (`tests.sim.
   play_settings`, budgets lifted, so the result is the same on any machine):
   rusherplus, heuristic, thinker and marshal by default. A candidate's
   difficulty is the share of probes that lose it, `tools/gauntlet_check.py`'s
   measure, with the probes' mean winning turns breaking ties.
3. **Pick a rising sequence across the spread**, not the top of a sort: the
   usable candidates (at least one probe won) ordered by difficulty, then one
   at each of `stages` evenly spaced places. The tool reports a first stage
   some probe lost or a last stage every probe won.
   **The last stage may be one no probe won** (`unwon_last`, on by default;
   `--no-unwon-last`, or the form's checkbox, turns it off). On the board in
   October 2026, of 163 maps with a posted win (scores are wins) and bot
   replays, 82 were won by none of rusherplus, heuristic, thinker, evolved and
   marshal; actuary won 35 of those and actuary or knower 46. So a top stage
   past marshal is ordinary for people, not a broken map. It is taken from the
   unwon candidates whose probe games all finished (a timeout may mean nobody
   can finish the map), the one dealt deepest, so it is the last stage by its
   opponents too. The shallowest was tried first and, on a smoke run, put a
   map lost to heuristic and rusherplus (by position) after two against
   knower. Every stage before it still has a probe win.
4. **Targets come free** (`--targets`): the slowest probe win, or with
   `--targets fastest` (the form's "Stage targets") the fastest, plus
   `--target-slack` turns, is the "roster bot's score plus slack" the dealt run
   above asks for, measured in games already played. The slowest is a bar
   every probe that won cleared; the fastest is the best any did. A stage no
   probe won has no target, there being no bot score to set it.

Every game is a line in `results/gauntlet_gen/<run>.jsonl` the moment it
finishes, under a header line holding the spec, seed and candidate count; a
rerun deals the same candidates, skips the games already there, refuses flags
that would deal differently, and picks again (so `--targets` can be changed for
free).

### The first run

2026-10-23, seed 41, eight stages, four candidates each, the four default
probes, on the example constraints from the request: symmetric, 13-29 systems,
no speed gain, home production 2, no knower. 128 games, about three minutes of
CPU on the container (actuary sat in some of them).

- **Rolling every slider made the probes measure the knobs.** With `roll` on,
  a depth-0 candidate against rusherplus alone could deal one home ship against
  garrison scale 38: 21 of 128 probe games were won, 17 ran out the 400-turn
  cap, and only marshal won much. `roll` is now off by default.
- **With it off, depth tracks difficulty.** Depth-0 candidates were lost by 25
  to 50% of the probes, depth 1 by all of them; the picked eight rose from 25%
  to 75%.
- **Four probes are a coarse ruler.** They give five levels, so picks bunch at
  one level and ties fall to mean winning turns, which measures length more
  than difficulty. rusherplus and heuristic won 2 of 32 games each, so on
  anything but the easiest maps they add nothing; thinker won 10 and marshal 12.
  Better resolution is the next thing to try: more or graded probes, or
  marshal's winning margin.
- **Some maps run long.** Winning probes took up to 323 turns, and 22 games hit
  the cap and count as losses. A player will not play 300 turns, so a cap on
  turns, or a filter on the probes' winning turns, belongs in the pick.
- **No stage was won by every probe.** The first stage lost by one probe in
  four, so a "won by all" floor would have left the run without a first stage.

### Dealing fewer candidates (measured 2026-10-24, not shipped)

The pick already pools every candidate and sorts the pool, so "per stage" sets
only the pool's size (stages × N) and each candidate's depth. Two ways to probe
fewer, compared on the first run's spec, seeds 41-45, by
`tools/gauntlet_pool_bench.py`:

- **pool**: a pool sized on its own, `stages + 4`, its depths spread evenly
  (dealt 0, 1, 1/2, 1/4, 3/4, ..., so any prefix is spread evenly too).
- **stop**: the same order dealt one at a time, stopping once the pick is
  complete (every stage filled, the last unwon) and either all four usable
  levels are seen or the last `stages` candidates added no new one; capped at
  the shipped budget.

| Arm | Games (5 seeds) | Stages filled | Usable levels | Last unwon |
| --- | --- | --- | --- | --- |
| stage (4 per stage, shipped) | 640 | 40 of 40 | 15 | 5 of 5 |
| pool (stages + 4) | 240 | 37 of 40 | 16 | 5 of 5 |
| stop | 332 | 40 of 40 | 16 | 5 of 5 |

- **Stopping early halves the games for the same gauntlet quality.** Every seed
  filled its eight stages and spanned as many levels as the shipped deal; it
  stopped after 18 candidates on four seeds and 11 on the fifth (which saw all
  four levels).
- **A fixed small pool comes up short.** Twelve candidates left seeds 41 and 43
  one and two stages short: too few were won by any probe. Stopping early
  sizes the pool to what the seed needs.
- **The ruler, not the pool, limits the spread.** Every arm reads three usable
  levels (25, 50, 75%) on most seeds; more candidates only add ties. More or
  graded probes ("The first run") are what would spread the stages further.

### In the game

- **`#gauntlet-spec=<payload>`** carries the spec as deflated JSON in base64url,
  as a settings token does, with the run's options beside it (`candidates`,
  `targets`, `slack`, `name`), which `Spec.from_dict` ignores
  (`gauntletgen.spec_from_fragment`, `job_from_link`). A link with no `seed`
  gets one rolled; the board's form always names one, so opening a link twice
  makes the same gauntlet. On desktop, `--gauntlet-spec SPEC.json` does the
  same.
- **The work is spread over menu frames.** `gauntletgen.Job` plays one probe
  game a turn at a time (`gauntletgen.play`, a generator), and `main.
  pump_generation` gives it `GAUNTLET_GEN_BUDGET_S` (30 ms) a frame while the
  menu is up, with the progress on the menu's status line ("Generating a
  gauntlet: 40% (games 13 of 32)"). It pauses while a game is played. Done, the
  file is kept (`gauntlet.remember`) and the Tutorial list opens on it, as a
  `#g=` link does.
- **A probe's balance never leaks into the menu.** A probe game goes through
  `build_state`, which pushes its setup's knobs into `config`;
  `pump_generation` pushes the menu's own setup back after every slice
  (`settings.apply_globals`).
- **Budgets are not lifted in the game**: `ai.set_budget_scale` is
  process-wide and never set mid-session. A probe game with a time-budgeted bot
  in it (actuary as an opponent) can therefore come out differently in the
  browser than in the tool. The default probes carry no budget.
- **One probe path.** The tool and the game both play `gauntletgen.play`,
  which matches `tests.sim.play_settings` game for game (checked on 20 games
  when it was written); `test_a_stepped_job_plays_the_same_games_as_playing_them_whole`
  holds the stepped job to the whole one.
- **Measured on desktop** (2026-10-23): a four-stage, two-candidate run, 32
  games, took 617 menu frames, about 18 s. A phone is slower by a factor not
  yet measured; the form defaults to six stages and three candidates (72 games)
  and says how many games a spec will play.
- **Copy link** on the Tutorial list (web only, for a gauntlet opened from a
  link or generated here: `MenuState.kept_ids`) copies a `#gauntlet=` link
  carrying the whole file (`gauntlet.kept`), so a generated gauntlet is shared
  as it is, with no board involved.

### On the board

The generate form sits on `gauntlet.html`, folded under the builder's lede.
`leaderboard/js/gauntlet-spec.mjs` turns it into a spec (`buildSpec`, pure,
`leaderboard/tests/gauntlet-spec.test.mjs`) and `gauntlet-gen-page.mjs` is the
DOM around it. Each choice becomes one dealer rule: map type (either, random,
symmetric), players and systems as ranges, opponents as an `in` list over the
ladder (knower unticked to start), fog off, on or either, and `roll` for the
Advanced sliders left on their default. Every Advanced and Combat slider is a
pair of pins, least and most (`KNOB_TABS`, folded by tab and by the menu's
headings, each saying how many it sets): both on the game's default is no rule,
together elsewhere a `fix`, apart a `range`. A pin dragged past the other
pushes it along. The two switches (neutral produces, in-lane battles) are Off,
On or Either. The Visibility pins count only when fog can be on. The table is
settings.py's `ADV_*` tables copied by hand; the fixture carries it and
`test_the_boards_sliders_are_the_games` holds the two together. "Either" is an
`in` rule rather than no rule, since with `roll` off an unruled field stays at
its default. The form says what it cannot deal before it offers a link, and
how many probe games the link will cost. What it builds, the game reads
unchanged (`test_the_boards_spec_link_is_a_run_the_game_reads_unchanged`, over
`leaderboard/tests/fixtures/gauntlet-spec.json`).

### Sharing the result

**Share the file, not the spec and seed.** Dealing is a pure function, but the
benchmark depends on how the probes play, and bots are free to move
([`leaderboard.md`](leaderboard.md), "Versioning"): the same spec and seed after
a bot changes would pick a different gauntlet. The result is an ordinary
gauntlet file, kept with `gauntlet.remember` and shared by the existing
`#gauntlet=<payload>` link, or saved to the board through `/api/gauntlet`.
Uploading from the game would be a new send path (the game sends only on *Post
to leaderboard* or *Share replays*), so a copied `#gauntlet=` link, which needs
no server, comes first.

## The gauntlet: proposed, not built

A seeded single-player run on a graph, FTL / Slay-the-Spire style: you choose
your path through branching stages toward an end node.

- **The run graph** would be laid out with the campaign's `tools/campaign.py`
  `_layout` and checked with `cut_nodes`, seeded so a run is reproducible and a
  run seed can be shared.
- **Stages dealt like the campaign's**: `_from_family` / `_mystery` with
  `settings.randomise_knobs`, so a run's stages are varied setups rather than a
  fixed list.
- **Difficulty rising by depth** through `ai.LADDER_ORDER` and each bot's knob
  stops: shallow stages against the weak end of the ladder, the end node against
  its top.
- The format above already carries it: a dealt run is a gauntlet file written
  at the start of the run, and `open_stages` / `next_stage` are already graph
  rules.

## Carry-over

The open problem. A roguelite run wants boons carried between stages: extra
home ships or production, a scouted node, an extra starting system. There are no
per-seat economy fields to put them in, and adding one would be a `Settings`
field (a key move for every shared map) that only this mode reads.

The candidate is to turn the stage's generated map into a hand map
(`Settings.custom_map`, via `custommap.from_state`) and edit the recipe: more
ships on the home node, a second owned node, a different production. That keeps
`Settings` unchanged and the result replayable through every existing path. The
cost is that a boon-modified stage is a different setup on the board, so it
scores separately from the same stage without the boon, and two players with
different boons never compare. Whether that is acceptable is the decision to
make before boons are built.

It no longer blocks the run. Stage targets (below) give a run its pressure
without carrying anything between stages, so the run can be built on them, and
boons can follow later or never.

## Stage targets

Proposed and built on 2026-10-23, beat the best bot included; targets on lanes
and a dealt run's targets are not built. An author can give a stage a score to
win within, and the stages beyond it open only once you have. This gives a gauntlet, and a later
run, the pressure to play well rather than just win, and it leaves the setup
alone: the stage stays the ordinary map it is, scores on it still compare on the
board, and no key moves. Boons cannot do that.

- **A target is gauntlet data, never `Settings`.** It is an optional field on a
  stage, `"target": {"turns": 14}` or `{"turns": 14, "lost": 30}`, scored as a
  best is: turns first, ships lost breaking a tie. It is called a target, not a
  par, because [`par.md`](par.md)'s par is a computed number and this one is the
  author's.
- **Meeting it counts.** A best at or under the target clears the stage, so an
  author can set their own score as the target. A stage with no target is
  cleared by any by-hand win, as now.
- **The target is what "cleared" means.** `gauntlet.cleared` keeps only the
  stages whose best meets their target (`gauntlet.met`). `open_stages` already
  took that set, so the bordering rule does not change: the stages beyond open
  once the target is met.
- **Still derived, never stored.** A target added or tightened later is checked
  against every best again: a 20-turn win on a stage that gains a 14-turn target
  no longer opens the way on. This is the same honest answer as an edited stage.
  A stored board gauntlet never changes, so this only happens to a desktop file
  or a built-in.
- **Next stage follows the target.** `Ui.offers_next_stage` asks
  `Ui.stage_cleared`: `can_post`, and this game or an earlier best meets the
  target. `main.stage_ui` copies the `Target` (a plain value) onto `Ui` with
  `stage_met_before`, read as the match starts, since render never sees a
  `Gauntlet`. The win overlay says which it was ("Win in 14 turns or fewer: met
  · Stage cleared", "…: not yet", or "…: not this time · your best clears it"),
  with Retry alone where Next stage would be when it is not cleared.
- **The briefing card, the top bar and the stage list show the target**, so it
  is never a surprise on the win overlay: a note at the card's foot ("Target:
  Win in 14 turns or fewer", one of `render._draw_card`'s notes, which always show),
  " · target 14" after the top bar's stage label, and "18 turns / 3 lost ·
  target 14" on a won row short of it. It is kept out of the side panel's hint,
  so `HINT_MAX` keeps its row budget.
- **A win short of the target leaves the stage open**: `open_stages` takes the
  won stages as well as the cleared ones, so a stage won out of order from a
  challenge link stays open to try again, but opens nothing beyond.
- **Set on the board from the author's own game.** The page's per-stage Target
  choice offers a pasted challenge link's score (`linkTarget`) as the stage's
  target. An author plays their own
  gauntlet, pastes the links, and gets targets they have shown can be met.
  `checkBody` refuses a malformed target (`checkTarget`); `from_dict` drops one
  (the stage then has none) and never raises. The id hashes the body, so adding a target is a
  new row and the old link keeps opening the old gauntlet.
- **Trusted, as clearing is.** It reads local bests, which nothing verifies. A
  gauntlet is uncontested, so a player who edits their own store only cheats
  themselves. The campaign is different: there, you can attempt a node only
  next to one you hold, and holding means having the best *verified* score
  among everyone. That rule is relative and contested, so it lives behind
  `/api/campaign`. A target is a fixed number the author picks, so it stays on
  the device, for the reasons "Relation to the weekly campaign" gives.
- **Not in the tutorial.** A beginner should be held up by what they have not
  learned yet, not by how fast they won.
- **A dealt run has no author to set them.** The cheap answer is a roster bot's
  score on the stage plus some slack, measured once when the run is dealt
  (marshal is fast and draws nothing, so the target is reproducible from the run
  seed). This is not the par that was kept off the board
  ([`par.md`](par.md), "Not on the board"): that was a computed number shown on
  every map. A run's target is local and only gates its own stages.

### Beat the best bot

A second kind of target: the stage is cleared by a win better than the best bot
on the board managed on the same setup. It is the figure a map's page already
heads its bot column with ("31 turns, by thinker", `standings.bestBot`), and it
asks an author for no number of their own.

- **Read on the board when the gauntlet is saved, then fixed.** The page reads
  the setup's `bot_scores` rows when "Beat the best bot" is picked and again on
  Save (`gauntlet-page.mjs` `botRows`), and writes the winner (`botTarget`) into
  the stage as an ordinary target. The rows are found by the link's game key
  (`token-decode.gameKeyFor`) and, since a Get Link carries no stamped key and
  hashes to a `j:` key no bot row has, also by matching the setup to its `games`
  row by jsonb equality (`cs` and `cd` together). The target is marked with
  where it came from:
  `"target": {"turns": 31, "lost": 12, "bot": "thinker", "beat": true}`. The
  game reads it like any target. `bot` is only for showing ("Beat thinker:
  31 turns / 12 lost"), and `beat` makes the comparison strict.
- **Not looked up live**, for three reasons. A stored gauntlet never changes
  and the game keeps the file after one fetch, so a target that needed asking
  again would break offline play and desktop files. The bot column moves (bots
  are free to move, [`leaderboard.md`](leaderboard.md), "Versioning"; a new bot
  joins the roster), so a live target would tighten under a player and quietly
  un-clear a stage they had cleared. And the game reads no `bot_scores` today:
  every read of it is the board's. Saving again picks up a better bot, as a new
  row.
- **Beat means strictly better**: fewer turns, or the same turns with fewer
  ships lost. An author's own target is met; a bot's is beaten, as the option's
  name says.
- **Only a win counts as the bot's score.** A loss is listed but never ranked
  (`standings.botOrder`). If no bot won the stage there is nothing to beat, and
  the page says that any win will clear it, writing no target.
- **No bot rows yet, no target.** The column exists only for maps on the board:
  `tools/bot_replay.py` works through `games`, which gets a row when a score is
  posted on the setup. A new stage has none until someone posts a game on it
  and the hourly job has run. The page says so ("No bot scores for this setup
  yet: post a game on it and they arrive within the hour") and does not offer
  to save the target. The author posting their own win on the stage is enough.
- **The best bot can be out of reach.** The column includes bots that read the
  other seats (`knower`, at its top stop). The author sees the number before
  saving. A later step would let them pick from the column ("Beat marshal")
  rather than take the best; that needs no new shape, since `bot` already names
  which one.
- **A dealt run uses the same shape.** A dealt setup is on no board, so its
  "roster bot's score plus slack" above is measured locally when the run is
  dealt and written with `bot` and, with no slack, `beat`.

### Later: targets on lanes

A target on a stage gates every way out of it alike. A target on a lane gates
one way out, and that is what branching wants: any win opens the main path, and
a tighter score opens a shortcut that skips stages, a bonus stage off the path,
or an alternate route to the end.

- **The format grows, it does not change.** A lane's third element already
  carries `two_way`; a target joins it, `[a, b, {"turns": 12}]`. A lane without
  one opens on a clear, as now, so no existing file changes meaning, and
  `from_dict` reads a bad target as none. `checkBody` refuses any key but
  `two_way` today, so it learns `turns` and `lost` with this.
- **A gated lane opens its second stage when the first's best meets its
  target**, and a `two_way` gated lane the first from the second likewise.
  `open_stages` becomes: the starts, every won stage, and each stage a lane
  leads to whose condition the near end's best meets (the stage's own target
  for an ungated lane).
- **Depth is measured over ungated lanes only.** `Gauntlet.depths` walks every
  lane the way it runs, so a shortcut from stage 2 to stage 5 would make 5 as
  shallow as 4 and take Next stage away from 4. Measured over the ungated
  lanes, the main path keeps its depths. A met gated lane out of a stage makes
  it a fork, so Next stage sends the player to the list to choose.
- **Reachability ignores targets.** `problems` asks whether a stage can be
  reached at all, so a bonus stage behind a tight target passes. Whether the
  target can be met is the author's to show, by playing it.
- **The list names each locked way on and what it needs** ("Shortcut: win in
  12"), so a player can see a route is there before they can take it.
- **It waits for a page that draws lanes.** The board's gauntlet page edits
  order only, so lanes and their targets would be written by hand or dealt
  until it draws the graph.
