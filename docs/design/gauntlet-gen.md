# Generated gauntlets

A gauntlet dealt from a spec rather than built from links: the board's generate
form makes the spec, and the game deals, probes and picks the stages in the
browser. Split out of [`gauntlet.md`](gauntlet.md) (the format, the tutorial,
targets, lanes and runs), which this builds on.

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

## Where it runs

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
  (`/play/#gauntlet-spec=<token>`) and the game deals and measures.

## The spec

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
  travel, economy and fog; combat only when ruled), with mode, players and
  systems (up to `STANDARD_MAX_NODES`) rolled too. Off by default for the
  reason "The first run" gives.
- **A symmetric stage's `layout` is always drawn**, `roll` or not: any of
  `mapgen.SYMMETRIC_LAYOUTS` alike unless ruled, as the campaign rolls it
  (`tools/campaign._symmetric_layout`). Left at its default every symmetric
  stage was a hub, since the board's form names no layout.
- **No `Settings` field is added**, so no challenge key moves, and a dealt stage
  is an ordinary setup that posts to the board as itself.
- **`dealer.problems(spec)`** names a spec nothing can satisfy (an empty range,
  `players` above what `systems` allows, no opponents left).

## Dealing

`dealer.deal(spec, rng, depth)` is a pure function of the spec, the rng and a
depth from 0 (first stage) to 1 (last). It draws each ruled field, rolls the
rest, sets `nodes` so `system_count` lands in range (rerolling a symmetric map
that rounds out of it), seeds with `settings.roll_seed`, and rerolls a setup
`setup_warnings` objects to. **Opponents follow depth**: the allowed bots are
ordered weakest first by `LADDER_ORDER` and each seat draws near
`depth * (n - 1)`, so difficulty rises by design and the benchmark checks it.
With no depth (`None`), each seat draws evenly from every allowed bot instead,
for a setup that should not rise, the daily's board (`daily.md`, "Opponent
strength stays flat").
That matters because a random map is decided by position as much as by play
(the tutorial's seed hunt, above).

## Shapes (built 2026-11-12)

A generated gauntlet may branch. The shape is a run option beside `targets`
and `unwon_last`, never a `dealer.Spec` field: the spec says which setups may
be dealt, the shape how they are joined.

| Shape | Columns (n = the spec's `stages`) | Starts |
|---|---|---|
| `line` (the default) | 1, 1, 1 … | the first; `lanes` left out |
| `concertina` | 1, 2, 1, 2 … 1 (an even count ends 2, 1, 1) | the single first stage |
| `cross-stitch` | 2, 2 … 2, 1 | both of the first column |

- **`stages` keeps meaning the run's length**, the number of columns, which is
  what the form already asked and what a run plays (`gauntletgen.widths`):
  concertina 7 is 10 stages, cross-stitch 6 is 11. A concertina of an even
  count ends on two single stages so a run still plays exactly the count
  asked; two starts would have made its opening a cross-stitch.
- **Both end on one stage**, the unwon-last map when `unwon_last` is set, so a
  run has one goal. Each stage leads to every stage in the next column. **No
  lane gates**: each stage keeps its own target; gated cross-overs would stack
  a second target on stages that already carry one.
- **The two stages in a column are about as hard as each other but differ in
  kind**, on at least one axis (`dealer.AXES`, read once at deal time into a
  candidate's `axes`): **size**, the larger at least 1.25 times the smaller's
  systems; **lane length**, medians 2 turns apart or more, at the stage's ship
  speed (`lane_lengths`, `lane_turns`, as a `lane_turns` rule reads them);
  **layout**, random against symmetric. The symmetric `layout` (hub and the
  rest) could be a fourth, but it is subtler to see on the map.
- **The second deal is steered, not filtered after probing.** A wide column
  deals two candidates a round, the second drawn up to `PAIR_TRIES` (20) times
  from the run's rng until it differs from the first on an axis, keeping the
  last draw if none does (`candidates`). Probe games are what a run spends its
  time on and dealing costs next to nothing, so an alike deal is cheaper turned
  away here than probed and found out afterwards. Every draw is in order from
  the one rng, so `candidates` stays a pure function of the spec, the seed and
  the shape; a line deals exactly as before, with no `axes`.
- **Picking a pair** (`pick_shape`): the usable candidates ranked as for a
  line, each column at its evenly spaced point; a wide column then takes the
  nearest unused candidate (difficulty first, then winning turns) that differs
  from it on an axis, else the nearest at all, and that pair is **alike**,
  named in the tool's output and its ledger (a `pick` line beside the games).
- **Settling** (`settled` with the shape's widths): every column full and,
  where the spec leaves an axis free (`dealer.free_axes`), every pair
  differing, with the usable levels shown as for a line; otherwise it stops
  when a round adds nothing, a pair turning unlike counting as something.
- **The file** lists stages column by column, a pair's "a" before its "b", so
  `gauntlet.layout` draws the pair in one column, "a" above. A pair's titles say
  how they differ, since the graph shows titles and that is where the choice is
  made: "Stage 3: more systems" / "fewer systems", "longer lanes" / "shorter
  lanes", "symmetric" / "scattered", by the first axis they differ on; an
  alike pair is "Stage 3a" / "Stage 3b". Single stages stay "Stage N", N the
  column. `start` and `lanes` are written out.
- **`gid` hashes the shape only when it isn't `line`**, so every spec link
  made before keeps its gauntlet's id, and so its bests and runs.
- **Asked for** on the board's generate form (**Shape**, written into the link
  as `shape`, the stage count it makes shown beside it, and "the two tracks may
  look alike" when `freeAxes` finds none), by `job_from_link` (anything
  unknown is a line), and by `tools/gauntlet_gen.py --shape`, kept in the
  ledger's header since it changes what is dealt.
- **Cost:** a wide column probes twice the candidates a round, so a
  concertina of 7 costs about 1.4 times a 7-stage line and a cross-stitch of 6
  about 1.8 times; minutes on desktop. A phone is untimed, as the line is
  (*In the game*); if a shaped link is too slow there, it could default to
  `LINK_ROUNDS - 1`. The second deal's retries cost no probe games.
- **Lives** ride the same way (`lives` in the link, `--lives`), written into
  the file and left out of `gid`. No shape has a dead end, so no generated
  stage grants a bonus life.

## The benchmark

1. **Deal candidates a round at a time** (`gauntletgen.candidates`, since
   2026-10-24): one at each stage's depth per round, the ends first, at most
   `--rounds` rounds (default 4; a link asks with `candidates`, default 3).
2. **Probe every candidate** with fixed probes in seat 1 (`tests.sim.
   play_settings`, budgets lifted, so the result is the same on any machine):
   heuristic with its reserve at 0, thinker, knower at Off, evolved at
   Look-ahead Off and marshal (`gauntletgen.PROBES`, spaced by strength and
   free of any wall-clock guard; "Dealing fewer candidates"). Until 2026-10-24
   they were rusherplus, heuristic, thinker and marshal. A candidate's
   difficulty is the share of probes that lose it, `tools/gauntlet_check.py`'s
   measure, with the probes' mean winning turns breaking ties.
3. **Pick a rising sequence across the spread**, not the top of a sort: the
   usable candidates (at least one probe won) ordered by difficulty, then one
   at each of `stages` evenly spaced places. The tool reports a first stage
   some probe lost or a last stage every probe won.
   **The run stops at the end of the first round its pick settles**
   (`gauntletgen.settled`): every stage filled, the last unwon (with
   `unwon_last`), and the pick either shows every usable level the probes tell
   apart or the round added none to it. Checked only where a round ends, so
   every depth has as many candidates as every other.
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
finishes, under a header line holding the spec, seed and rounds; a
rerun deals the same candidates, stops at the same round, skips the games already there, refuses flags
that would deal differently, and picks again (so `--targets` can be changed for
free).

## The first run

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

## Dealing fewer candidates (measured 2026-10-24, shipped)

The pick already pools every candidate and sorts the pool, so "per stage" sets
only the pool's size (stages × N) and each candidate's depth. Two ways to probe
fewer, compared on the first run's spec, seeds 41-45, by
`tools/gauntlet_pool_bench.py`:

- **pool**: a pool sized on its own, the first `stages + 4` candidates of a
  round-robin order: one candidate at each stage's depth per round, the ends
  first (stage 1, 8, 2, 7, ...), so even a partial round favours no depth.
- **stop**: the same order a round at a time, stopping at the end of a round
  once the pick is complete (every stage filled, the last unwon) and either
  shows every usable level or the round added none to it; capped at the
  shipped budget. **It stops only at a round's end**, so every depth has as
  many candidates as every other: stopping early cannot lean towards the
  weaker opponents of the shallow stages, as stopping inside the shipped
  per-stage blocks would.

With the four shipped probes (a first version of stop, dealing depths 0, 1,
1/2, 1/4, ... and checking after every candidate):

| Arm | Games (5 seeds) | Stages filled | Usable levels | Last unwon |
| --- | --- | --- | --- | --- |
| stage (4 per stage, shipped) | 640 | 40 of 40 | 15 | 5 of 5 |
| pool (stages + 4) | 240 | 37 of 40 | 16 | 5 of 5 |
| stop | 332 | 40 of 40 | 16 | 5 of 5 |

With six probes (the four plus heuristic with its reserve at 0, and thinker's
Claudebot stop), round-robin as above:

| Arm | Games (5 seeds) | Stages filled | Usable levels | Last unwon |
| --- | --- | --- | --- | --- |
| stage (4 per stage) | 960 | 40 of 40 | 20 | 5 of 5 |
| pool (stages + 4) | 360 | 38 of 40 | 19 | 5 of 5 |
| stop | 768 | 40 of 40 | 20 | 5 of 5 |

- **Stopping early keeps the gauntlet and saves games**: every seed filled its
  stages with as many levels as the full deal. It saved half the games with
  four probes but a fifth with six: a finer ruler keeps finding a new level, so
  the pick rarely settles after two rounds.
- **A fixed small pool comes up short.** Twelve candidates left two or three
  seeds a stage or two short: too few were won by any probe.
- **heuristic with its reserve at 0 is a real step up** from stock heuristic:
  93.6% head to head (`tools/sweep.py --arm 'lean:reserve_floor=0,reserve_fraction=0'`,
  100 seeds over three cells, both halves reproduced; floor 0 alone 64.6%).
  As a probe it won 15% of 320 candidates against heuristic's 6%.
- **Claudebot adds little.** It won 9%, beside rusherplus (8%) and heuristic
  (6%), and the three disagree on which maps they win (rusherplus won 20 maps
  heuristic lost, heuristic 12 the other way): at the bottom a win is mostly
  position. Six probes still spread the easy end (58 of 320 maps won by three
  or more, against 28 with four).
- **knower at Off (`knower:0`) is as fast as thinker** (median 0.2 s a probe
  game) **and about as strong**: it won 32% of the 320 candidates to thinker's
  30%, as "thinker-strength" in [`knower.md`](knower.md) says. They disagree on
  72 maps (39 one way, 33 the other), so as a seventh probe it adds a level
  (23 usable levels over five seeds against 20; stop 896 games against 1,120)
  but does not split thinker from marshal: 59 maps were still won by marshal
  alone of the three.
- **evolved at Look-ahead Off (`evolved:0`) fills the gap and stays fast**:
  39% of candidates won, between knower at Off (32%) and marshal (53%), at a
  median 0.30 s a probe game (thinker 0.23 s, marshal 0.23 s). Of the 59 maps
  marshal alone of thinker, knower and marshal won, it won 17. Look-ahead On
  (`evolved:1`) is marshal's strength (50%) at 2.1 s a game, seven times the
  cost for what marshal already measures.
- **Four probes, chosen for spread** (heuristic with its reserve at 0, thinker,
  `evolved:0`, marshal), read from the same games, seeds 41-45:

  | Probes | Arm | Games | Stages filled | Usable levels (most 20) |
  | --- | --- | --- | --- | --- |
  | shipped four | stage | 640 | 40 of 40 | 15 |
  | shipped four | stop | 512 | 40 of 40 | 15 |
  | chosen four | stage | 640 | 40 of 40 | 20 |
  | chosen four | stop | 384 | 40 of 40 | 20 |

  Every seed's pick used all four usable levels, for 40% fewer games than
  ships. With more probes stop saves less: a ruler of nine (adding `evolved:1`
  and the rest) settled late enough to play 1,368 games against 1,440.
- **The spread is limited at the top, not the bottom.** 121 of 320 candidates
  were won by no probe and 95 by one (mostly marshal), so most picks after the
  first few read 83% (only marshal won). A probe between thinker (30%) and
  marshal (53%) would split them; more weak probes would not.

**What shipped** (2026-10-24): rounds and stop as above, with five probes:
heuristic with its reserve at 0, thinker, knower at Off, evolved at Look-ahead
Off and marshal (15, 30, 32, 39 and 53% of candidates won). Nothing fast plays
above marshal: knower at Predict won 52% and agreed with marshal on most maps,
and runs rivals' bots inside a 50 ms wall-clock guard the game never lifts, so
the same link could measure differently on a slow phone. From the recorded
games, seeds 41-45: the old per-stage deal of four would play 800 games for 25
usable levels; stop played 600 for 23, every stage filled and the last unwon on
every seed.

## In the game

- **`#gauntlet-spec=<payload>`** carries the spec as deflated JSON in base64url,
  as a settings token does, with the run's options beside it (`candidates`, now
  the most rounds, `targets`, `slack`, `name`), which `Spec.from_dict` ignores
  (`gauntletgen.spec_from_fragment`, `job_from_link`). A link with no `seed`
  gets one rolled; the board's form always names one, so opening a link twice
  makes the same gauntlet. On desktop, `--gauntlet-spec SPEC.json` does the
  same.
- **The work is spread over menu frames.** `gauntletgen.Job` plays one probe
  game a turn at a time (`gauntletgen.play`, a generator), and `main.
  pump_generation` gives it `GAUNTLET_GEN_BUDGET_S` (30 ms) a frame while the
  menu is up, with the progress on the menu's status line ("Generating a
  gauntlet: 40% (games 13 of at most 32)"; a run that settles early jumps to
  done). It pauses while a game is played. Done, the
  file is kept (`gauntlet.remember`) and the Tutorial list opens on it, as a
  `#g=` link does.
- **The list covers the status line, so it says its own news.** What is said
  about a gauntlet while the list is open ("Generated ...", the listing save,
  Copy link) goes to a line in the list under that gauntlet
  (`menu.say_gauntlet`, `MenuState.gauntlet_note`, cleared when the list
  reopens); with the list closed it goes to the status line.
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
  yet measured; the form defaults to six stages and three candidates (72 games
  then; since rounds, six stages and at most three rounds, at most 90) and says
  at most how many games a spec will play.
- **Copy link** on the Tutorial list (web only, for a gauntlet opened from a
  link or generated here: `MenuState.kept_ids`) copies a `#gauntlet=` link
  carrying the whole file (`gauntlet.kept`), so a generated gauntlet is shared
  as it is, with no board involved.

## On the board

The generate form sits on `gauntlet.html`, one of two ways under the page's
shared name, author and listing box (Generate, or Build from map links; the
page remembers which it showed, and a `#<id>` remix opens on Build).
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

## Sharing the result

**Share the file, not the spec and seed.** Dealing is a pure function, but the
benchmark depends on how the probes play, and bots are free to move
([`leaderboard.md`](leaderboard.md), "Versioning"): the same spec and seed after
a bot changes would pick a different gauntlet. The result is an ordinary
gauntlet file, kept with `gauntlet.remember` and shared by the existing
`#gauntlet=<payload>` link, or saved to the board through `/api/gauntlet`.
Uploading from the game is a new send path (otherwise the game sends only on
*Post to leaderboard* or *Share replays*), so a copied `#gauntlet=` link, which
needs no server, came first.

**Saved to the board only when asked to be listed** (2026-10-29, the author's
call). The name, author and "Ask for it to be listed publicly" are the same
question for both ways of making a gauntlet, so the page asks them once, at the
top. A generated gauntlet is made in the game, so for its author and listing
request to mean anything the game has to send it: ticking the box is the
consent to that send, and nothing else is. Ticked, the spec link carries
`wants_listing: true` and `by_name`; unticked it carries neither, and a name
alone is never read (`job_from_link`, `Job.listing`). Once the run is done,
`main.ask_listing` saves the file through `/api/gauntlet` (`gauntlet.ask_listing`,
a `pbp.request` POST) with `wants_listing` set, and the Gauntlets list (or the
status line, once the list is closed) says whether it took. The board mints its own `<slug>-<hash>` id; the copy kept in the
browser keeps its `gen-<hash>` one, so the two are the same gauntlet under two
ids, and an admin lists it with `tools/admin.py list-gauntlet` as any other.
Opening the same seeded link again saves the same file, which is the same row.
A dealt stage already passes `checkBody` (stages carry an integer `seed`, the
briefing lines are short), so the server needs no change.
