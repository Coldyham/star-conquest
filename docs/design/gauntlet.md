# Gauntlets and the tutorial

A gauntlet is an ordered string of pinned setups, each with a short briefing and
a one-line hint, played in order. The tutorial is one
(`starconquest/assets/gauntlets/tutorial.json`), read by the pure core module
`starconquest/gauntlet.py`. Players make their own on the board's gauntlet page
(`leaderboard/gauntlet.html`), which stores them and hands out a short link; see
"Gauntlets made on the board". The same file format is meant to carry a later
single-player run with branching graph navigation and score targets that gate
the way on. Stage targets and generated (linear) gauntlets are built; the
branching run is proposed below and not built, and carry-over between stages is
decided against.

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
| 11 | Long odds (hidden bonus) | an unfair start, opened by a hidden lane from The hunt | marshal, knower · Search, actuary · Field |
| 12 | No way out (hidden bonus) | a worse start, opened by beating the best bot on Long odds | marshal, knower · Search, actuary · Learning |

**The bonus stage** (added 2026-11-05) is reached only by a hidden lane from
The hunt, gated at the best human score on The hunt's setup on the board that
day: 86 turns / 72 lost, by Protokletos (met, not beaten). Its map is a
four-seat, 14-system board an author shared, with thinker swapped for actuary
(at Field) and knower at Search. You start 7 turns from actuary and 10 from marshal, while knower sits
17 or more from everyone. It isn't listed, numbered or counted until the lane
opens (`gauntlet.shown`, `gauntlet.place`), and it isn't on the tutorial's
path, so it never joins a run (`gauntlet.chain`).

**No way out** is behind a hidden lane from Long odds, gated at the best bot's
win there, to beat: **actuary, 129 turns / 108 lost**. Its map is Long odds'
knobs on seed 2350, picked from 5,000 seeds for a worse start: actuary 4 turns
from you, marshal 8, knower 16, the rivals at least 11 apart, and one system
nearer you than any rival. Actuary plays at Learning.

**How the gates were measured.** Each roster bot (knower at Council and at
Search, actuary at Hunt and at Field, marshal, evolved, thinker, heuristic,
rusherplus) played the stage from the human seat as a *black box*: the seat
stays flagged human, so knower models it as it would a person instead of
reading its orders, and the bot's orders go in as the human's. Played the
board's way (`tools/bot_replay.py`, which hands the seat over so knower reads it
exactly, `tests/sim.py` `_hand_over`), a bot is handicapped against knower in a
way a person isn't, so the board's bot column is no target for a player. Wall
clock guards were lifted 100x, as the board's worker lifts them. On Long odds
only actuary at Hunt won, in 129 turns / 108 lost; the rival knower took every
other game. On No way out no bot won (2026-11-05).

**Both play knower at Search, never Council, and actuary below Hunt.** As first
shipped, Long odds had actuary at Hunt and No way out had knower at Council and
seed 1705. Both set off the menu's "may play slowly" warning (`setup_warnings`):
knower replays actuary at every turn it looks ahead, and Hunt costs ~461 ms a
call, while Council on 14 systems costs ~650 ms a turn with any actuary. In the
browser knower stops thinking at 150 ms, so it would have looked about one turn
ahead, far weaker than measured and dependent on the device's speed.
`test_no_builtin_stage_plays_slowly` now holds every built-in stage to it. With
the cheaper bots, seed 1705 became easier than Long odds (five bots won it), so
No way out moved to seed 2350. Of the alternatives measured there, Search with
Field let one bot win (knower at Council, 128 / 93) and Search with Learning
none; 1705 with knower at Predict and actuary at Hunt was also unbeaten.

**Defender's edge runs at 1.1** (2026-11-07; it was 1.05). At 1.05 the odds
card's "5 holding it fight like 5.25" was too small a difference to notice. The
worry was rusherplus, which sends at `target.ships + 2` and never prices the
advantage, throwing ships at neutrals. Measured on the stage's map, seeds
4016-4023, with heuristic, thinker, marshal and actuary in the human's seat (32
games a setting): rusherplus's attacks on neutrals failed 12.3% of the time at
1.0, 12.7% at 1.05, 14.4% at 1.1 and 16.4% at 1.15 (69, 92, 116 and 179 ships
thrown away). The human seat won 29, 28, 32 and 31 of 32. Changing the setup
made the stage a new, uncleared one, as above.

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
- **A lane may carry a target** in its third element, in a stage target's
  keys: `[9, 10, {"turns": 86, "lost": 72}]` opens 10 only once 9's best meets
  it ("Targets on lanes", below). `"hidden": true` beside a target keeps the
  far stage off the list until it is met.
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
  lane's third element for `two_way: true`, a target and `hidden: true`, drops
  a malformed `target` (the stage or lane then has none) and `hidden` on a lane
  with no target, and clamps text to the lengths above. `gauntlet.to_dict`
  writes the canonical file back, every lane and the start spelled out.
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

The board's gauntlet page builds a gauntlet from map links: one pasted link
per stage (the menu's Get Link, or a challenge link with its score dropped),
each with a title, briefing paragraphs and a hint. Saving stores it and gives
the link `/play/#g=<id>`. A `.json` download of the same file is for desktop.

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
  or fixing one is open, edit, save. Its lanes, starts, lane targets and hidden
  marks come with it (*Lanes on the board*, below), and **Open a .json file**
  loads a desktop or generated gauntlet the board never saw the same way.
- **Listing is an admin's decision at the author's request.** The author can tick
  "Ask for it to be listed publicly" (`wants_listing`); `tools/admin.py
  list-gauntlet` sets `listed`, and only a listed row is readable with the
  publishable key. `gauntlets.html` lists those rows newest first, each
  opening in the game by `#g=<id>` or in the builder to remix, and the player
  card's Gauntlet runner badge counts clears of them. The menu has one
  Gauntlets entry (the public page); tabs on both pages move between Public
  and Make new, to keep the menu short.

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

Moved to [`gauntlet-gen.md`](gauntlet-gen.md): the spec, dealing and probing,
where it runs, the first run, dealing fewer candidates, and the game's and the
board's halves.

## Runs (built 2026-11-04)

A **run** plays a gauntlet's stages from a start to a goal, each won within
its target, with one life. It is the first step towards the roguelite run
below: forks (built 2026-11-12, *Forks* below), but no lives or boons. It needs no format change, so a gauntlet already made
can be run, and in particular a generated one with stage targets (the form's
"Stage targets", from the probes' wins) or one built on the board with "Beat
the best bot". The working plan for the rest is
[`graphical-gauntlet-maker.md`](../graphical-gauntlet-maker.md).

- **Offered only where it adds something** (`run.offered`). The gauntlet must
  have a goal to end on, and either a stage target or a fork (`run.forks`:
  two starts, or a stage on the main path with more than one way out, gated or
  not). At a fork the choice is the point: a run that takes a way it can't win
  is over. On the tutorial, a single path with no target whose gates lead only
  off its last stage, a run would be the stage list again, so the list shows
  no run there.
- **Only this game's score counts.** A run game's Next stage asks
  `can_post` and `met_target` on this game, never `stage_met_before`, so a fast
  free-play win on a stage doesn't carry a run over it
  (`Ui.offers_next_stage`). A loss, a win short of the target, or a win not
  played by hand ends the run (`run.judge`), and so does being knocked out
  while the bots play on.
- **Run games are ordinary games.** They post, file bests and clear stages as
  any other. Nothing reaches `Settings` or the log, so the board and the
  verifier never see a run.
- **A run is stored, not derived** (`paths.WEB_RUNS_KEY`). This is the one
  exception to "Progress is derived, never stored". A run is a sequence of
  games, one per stage, and bests keep only the best game on each stage, so
  no read of them could say how far a run got. A step is written when its game
  starts and again the moment that game is decided. The store keeps the active
  run and the last 20 finished.
- **A run judges only its own game.** Each step names its game's log file.
  Only that game moves the run, and only once (`run.finish`). A Retry, a rewind
  or a fork after the game is decided is free play: `main.run_ui` marks only
  the run's own game, and the run has already recorded the result. So none of
  them is blocked. They just aren't the run. While the game is undecided it
  plays ironman (no History or Undo, so no rewind), and the Hint is
  not offered (`Ui.can_hint`).
- **Quitting doesn't escape a bad game.** Continue run resumes the step's own
  log, never a fresh copy of the stage (`main.open_run`), and so does the boot
  resume prompt when the latest game is the run's (`run.is_playing`). On the
  web build `games/` is MEMFS and doesn't survive a reload, so the active
  step's log is also kept in `webstore` (`paths.WEB_RUN_LOG_KEY`, rewritten
  every turn by `run.keep_log`). A game that can no longer be loaded, or whose
  rules have moved on, ends the run (`GONE`) rather than restarting the stage,
  since restarting would reward a reload.
- **One run at a time.** Starting another gauntlet's run abandons the one
  under way, after a second press. Abandon run also takes two.
- **An edited gauntlet ends its run.** A run stores a hash of its stages' setups
  and targets (`run.fingerprint`). A desktop file changed under a run makes it
  a different gauntlet, as an edited stage is a new one (`run.active_for`).
- **In the list:** a line under the stages ("Best run: 5/8 stages", "Run
  complete ✓", or what a run is), then **Start run**, or **Continue run** and
  **Abandon run**. The stage the run is on reads "run · …" in its row. On
  the overlay, the run's line ("Run: stage 2/8 done · Next stage carries it
  on", "Run over at stage 3/8: Win in 14 turns or fewer not met") takes the
  stage verdict's place.

### Forks (built 2026-11-12)

The first build offered runs only on a single path. That left out exactly the
gauntlets where a run means most: at a fork, the way chosen can end the run.
This is the movement half of Phase 5 of the plan, without lives or boons.

- **A run moves from the stage it last won** (`run.ways_on`), never from bests.
  At first its ways on are the starts. After a win they are each ungated lane
  out of that stage to a stage the run hasn't won (so a two-way lane never
  leads back), and each gated lane whose target **this game's** score meets.
- **A win with no ungated way on completes the run** (`run.ends`): a goal, or a
  bonus stage's dead end. A gated lane out of a goal (the tutorial's bonus
  stages) is not taken, as the plan's "winning a goal wins the run" says.
- **One way on: Next stage carries it on**, as before. The game works this out
  from the ways out it was given (`Ui.run_ways`, set by `app.run_ui`) and this
  game's score, in `app.record_run_step`.
- **More than one: the list chooses.** The overlay says "choose the way on in
  the gauntlet list". The list marks every way on "run", and Continue run plays
  the selected stage, naming it on the button ("Continue run: Nebula Gate").
  With nothing chosen it asks for a stage marked run. Two starts work the same
  way for Start run.
- **No total where runs differ in length** (`run.linear`: a single path with no
  gated lane). The list and overlay say "stage 3", not "stage 3 of 8", and a
  finished run "Run complete: 4 stages".
- **The fingerprint covers the lanes** of any gauntlet that isn't a plain path,
  so a lane edited under a run ends it. A plain path hashes as before.

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

**Decided against (2026-10-26).** Stage targets give a run its pressure, and
boons get complicated. What was proposed:

A roguelite run wants boons carried between stages: extra
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

Stage targets (below) give a run its pressure without carrying anything
between stages, so the run is built on them instead.

## Stage targets

Proposed and built on 2026-10-23, beat the best bot included, and a dealt
run's targets with it (`tools/gauntlet_gen.py --targets`, "Generated
gauntlets"); targets on lanes followed on 2026-11-05 ("Targets on lanes"). An author can give a stage a score to
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
- **The briefing card, the side panel and the stage list show the target**, so it
  is never a surprise on the win overlay: a note at the card's foot ("Target:
  Win in 14 turns or fewer", one of `render._draw_card`'s notes, which always show),
  " · target 14" after the stage label heading the side panel, and "18 turns / 3 lost ·
  target 14" on a won row short of it. It is kept out of the panel's hint
  itself, so `HINT_MAX` keeps its row budget.
- **A win short of the target leaves the stage open**: `open_stages` takes the
  won stages as well as the cleared ones, so a stage won out of order from a
  challenge link stays open to try again, but opens nothing beyond.
- **Set on the board from the author's own game.** The page's per-stage Target
  choice offers a pasted challenge link's score (`linkTarget`) as the stage's
  target. An author plays their own
  gauntlet, pastes the links, and gets targets they have shown can be met.
  A third choice, Custom, takes typed-in turns (and ships lost), as a lane's
  gate does, suggesting the link's score and the best bot's beside it; a
  target stricter than both is warned about ("may be impossible",
  `meetsTarget`), never refused. A saved body keeps a target's score but never
  the challenge link it came from, so a stored gauntlet or a `.json` opened on
  the page rebuilds plain links and opens such a target as Custom, holding its
  numbers (`draftFromBody`).
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
  learned yet, not by how fast they won. The tutorial's one gated lane only
  opens a hidden bonus stage past its end, so nothing a beginner needs waits on
  a score.
- **A dealt run has no author to set them.** The answer is a roster bot's
  score on the stage plus some slack, measured once when the run is dealt. As
  built, it is the slowest (or fastest) probe win plus `--target-slack`, from
  the probe games the dealer plays anyway ("Generated gauntlets", "The
  benchmark"). This is not the par that was kept off the board
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
- **A dealt run uses the plain shape.** A dealt setup is on no board, so its
  "roster bot's score plus slack" above is measured locally when the run is
  dealt. `gauntletgen.target` writes it as an ordinary met target, `turns` and
  `lost` (or `turns` alone with slack), not with `bot` and `beat`.

### Targets on lanes (built 2026-11-05)

A target on a stage gates every way out of it alike. A target on a lane gates
one way out, and that is what branching wants: any win opens the main path, and
a tighter score opens a shortcut that skips stages, a bonus stage off the path,
or an alternate route to the end. Built as Phase 1 of
`graphical-gauntlet-maker.md`; the board's page still edits order only.

- **The format grows, it does not change.** A lane's third element already
  carried `two_way`; a target joins it, `[a, b, {"turns": 12}]`, in a stage
  target's keys (`turns`, `lost`, `bot`, `beat`), parsed by the same `_target`.
  A lane without one opens on a clear, as before, so no existing file changes
  meaning, and `from_dict` reads a bad target as none (`Gauntlet.lane_targets`).
- **A gated lane opens its second stage when the first's best meets its
  target**, and a `two_way` gated lane the first from the second likewise.
  `open_stages` is: the starts, every won stage, and each stage a lane leads to
  whose condition the near end meets (cleared, for an ungated lane). It reads
  the bests (`found`); given only what is cleared, every gated lane stays shut.
- **Depth is measured over ungated lanes only** (`Gauntlet.depths`). Walking
  every lane, a shortcut from stage 2 to stage 5 would make 5 as shallow as 4
  and take Next stage away from 4. `next_stage` follows ungated lanes, and with
  the bests in hand (`main.stage_ui`, `menu.next_stage`) a met gated lane out
  of the stage makes it a fork, so the player chooses on the list.
- **Reachability ignores targets** (`Gauntlet.reachable`). `problems` asks
  whether a stage can be reached at all, so a bonus stage behind a tight target
  passes. Whether the target can be met is the author's to show, by playing it.
- **A run's path is the ungated lanes** (`gauntlet.chain`): a shortcut is not
  taken, and a stage reached only through a gated lane is not on it, so a
  linear gauntlet with a bonus stage can still be run.
- **Ends and labels are derived, never stored.** A goal (`gauntlet.goals`) is a
  reachable stage with no ungated lane out that is a start or has an ungated
  lane in, so a bonus dead end behind a gate is never one. A gated lane is a
  "Bonus" when its far end reaches no goal, else a "Shortcut" when it skips a
  depth, else "Gated" (`gauntlet.lane_label`).
- **The list names what a locked way needs**: a stage reached only through
  gated lanes reads "locked · Bonus: target 86/72" (`gauntlet.way_in`).
- **A lane may be hidden** (`{"turns": 12, "hidden": true}`, ignored without a
  target): a stage every way into which is hidden (`Gauntlet.hidden_stages`)
  is not listed until one opens (`gauntlet.shown`), as Mario Kart unlocks a
  staff ghost for a fast enough time trial. Banners number and count only the
  stages a player can know about (`gauntlet.place`: "3/10", or "bonus").
  Proposed 2026-10-28, in place of a hidden star on board maps
  (`other-games.md`, "Star tiers on every map").
- **The board stores what the game reads.** `checkBody` takes the same lane
  keys and refuses what `from_dict` would drop (a hidden lane with no target, a
  bad target); `leaderboard/tests/fixtures/gauntlet-lanes.json` is read by both
  suites. The board's page now edits lanes (*Lanes on the board*).

### Lanes on the board (built 2026-11-11)

The board's gauntlet page edits the graph: Phase 3 of
`graphical-gauntlet-maker.md`. Nothing in the game changed.

- **The maker is the board page, grown, not an editor in the game.** A
  gauntlet's lanes have no length and the game draws no geometry, so what the
  map maker would lend (placing, dragging, pan and zoom) is what a gauntlet
  doesn't need, while everything a stage does need is already on the board:
  pasted links, briefings, the link's score and the best bot as targets (only
  the board reads `bot_scores`), `checkBody`, Save and listing. HTML forms also
  edit text better on a phone.
- **Lanes join stages by a draft key, never by place.** Each draft stage gets a
  `key` from the draft's `next_key` counter when it is made, so ↑/↓ and Remove
  never rewire a lane. `draft.lanes` is `{from, to, two_way, aim, target,
  hidden}` by key, `draft.start` a list of keys (`gauntlet-build.mjs`).
- **A linear gauntlet saves exactly the body it saved before**, so its id holds:
  `buildBody` numbers stages in list order and writes `lanes` and `start` only
  when they differ from what `from_dict` assumes without them. A new draft and
  **+ Add a stage** join the new stage from the last one, so an author who never
  touches a lane gets today's gauntlet. A draft kept before lanes reads back as
  that chain (`readDraft`).
- **Each card**: *Open from the start*; a row of **Leads to** chips, one per
  other stage, lit where a lane runs (past 16 stages only the lit ones show,
  the rest behind "+ Lane to…"); a "Reached from" note, so either end explains
  a lane; **+ After** (a stage below, with a lane from this one) and **+
  Branch** (a stage beside it, with this one's lanes in, so a fork). Where the
  other end already has a one-way lane back, the chip offers "↔ make two-way":
  the format keeps a pair's first lane and drops the second.
- **A lit chip opens the lane's options**: two-way; a gate (none, turns typed
  in, the near stage's link score, or the best bot on it: the stage target's
  choices plus one, through the same `applyAim`); hidden, only when gated; the
  derived label (`laneLabel`) as it is set; remove. A bot gate is read again on
  Save, as a stage's is.
- **A stage's place is computed, never stored** (`gauntlet.layout`, mirrored by
  `gauntlet-graph.mjs`): the column is ungated depth, so the main path reads
  left to right and a shortcut is a long arrow; a stage reached only through
  gated lanes is one column past its shallowest source, walked level by level
  so list order never moves it; an unreached stage takes a last column. The
  row is list order, then one pass sorting each column by its parents' mean
  row, not iterated, so the author's order (↑/↓) decides most of the picture
  and a small edit never reshuffles it. Hidden stages are laid out too, so
  revealing one moves nothing. `leaderboard/tests/fixtures/gauntlet-layout.json`
  is read by both suites. A stored position (an `at` per stage) was decided
  against: the author would be placing what `layout` already places.
- **The diagram above the cards is read-only**, drawn by the game's own drawer
  (*The graph view*, below), so it is the picture the gauntlet list will show:
  a bar on starts and ★ on goals, dashed gated lanes with their short target,
  dotted hidden ones; a stage no start reaches is red with a note, and a gated
  lane that is a stage's only way in is amber (a warning: that is what a bonus
  stage is). *As a new player sees it* drops hidden lanes and the stages only
  they reach, leaving the gap the game will leave. Clicking a stage scrolls to
  its card; hovering a chip lights its lane. It scrolls sideways in its own box.

### The graph view (built 2026-11-11)

The game's gauntlet list draws the graph in place of a list of rows: Phase 2 of
`graphical-gauntlet-maker.md`, re-planned once the pygame menu had gone.

- **One drawer for the game and the board**: `webapp/js/stagegraph.mjs`. It
  decides nothing: handed stages at their `gauntlet.layout` cells and the lanes
  between them, it draws them. The game's view comes from
  `setupmenu.graph_view`, the board's from `gauntlet-graph.mjs`. It lives in
  the game, and the board imports it as `/play/js/stagegraph.mjs`, because the
  game must work offline (a kept gauntlet opens with no network) and the
  service worker only falls back to its cache for `/board/`.
- **Layout is CSS.** Each stage is a button in a CSS grid at its cell, sized by
  its text; the lanes are an SVG layer drawn from where the buttons landed and
  redrawn when the grid resizes (a lane to the next column runs edge to edge, a
  shortcut arcs over what it skips, a lane back dips below). The box scrolls,
  so the wheel, touch drag and scroll bars are the browser's; nothing keeps an
  offset or hit-tests. The page keeps the box's scroll and focus across the
  redraw each reply makes.
- **What is shown is `stage_rows`'**: open, cleared, won short of a target,
  locked with what its way in needs, the run marker, and a stage behind only
  hidden lanes left out until it opens. **A hidden lane is drawn only once
  met** (its near stage's best meets its gate), so the graph never gives a
  bonus away, and the strip never names one before then either.
- **A tap selects, a second tap (or Play) plays.** At a fork the player should
  see what each way needs before committing, and a node is too small to say
  it. The selection is `MenuState.stage_pick`, settled by
  `setupmenu.picked_stage` (the stage being played, else the run's, else the
  first open stage not yet cleared, else a start). A locked stage can be
  selected, to read what opens it, but not played. The strip under the graph
  (`pick_view`) shows the stage's progress, its target, where it leads and
  each gated way on with what it needs.
- **Keys**: the arrows move the selection by cell (`setupmenu.step_pick`, the
  nearest stage that way), Enter plays it, Esc closes.
- **Edit on the board** opens a gauntlet the board stored on its maker as a
  draft (`gauntlet.on_board`: the board's id shape, not a generated
  `gen-<hash>`; `paths.LEADERBOARD_MAKER_PATH`).
- **Every gauntlet draws as a graph, linear ones too**: a chain is one row that
  scrolls sideways, opened on the stage to play next.
