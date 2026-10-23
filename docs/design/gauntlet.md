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
second line of its own (`test_a_stage_hint_fits_the_empty_panel`).

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
  lane's third element only for `two_way: true`, and clamps text to the lengths
  above.
- A player's own gauntlets come from two places (`gauntlet.user`): the ones
  opened from a link, kept in `webstore` under `paths.WEB_GAUNTLETS_KEY`
  (`gauntlet.remembered`, newest first, the last eight), and on desktop the
  files in `data_dir()/gauntlets/*.json`. The web build has no folder to read:
  its files are MEMFS and do not survive a reload. One whose `id` is a
  built-in's is ignored. The Tutorial list shows a picker once there is more
  than one.

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

## Stage targets (proposed, not built)

Proposed on 2026-10-23. An author can give a stage a score to win within, and
the stages beyond it open only once you have. This gives a gauntlet, and a later
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
- **The target is what "cleared" means.** `gauntlet.cleared` would keep only
  the stages whose best meets their target. `open_stages` already takes that
  set, so the bordering rule does not change: the stages beyond open once the
  target is met. A win short of it leaves the stage open and replayable, and
  the list shows both numbers ("18 turns · target 14").
- **Still derived, never stored.** A target added or tightened later is checked
  against every best again: a 20-turn win on a stage that gains a 14-turn target
  no longer opens the way on. This is the same honest answer as an edited stage.
  A stored board gauntlet never changes, so this only happens to a desktop file
  or a built-in.
- **Next stage follows the target.** `Ui.offers_next_stage` asks only
  `can_post` today. It would also need the stage cleared after this win (this
  game, or an earlier best, meets the target). `main.stage_ui` copies the target
  onto `Ui` as plain values, since render never sees a `Gauntlet`. The win
  overlay says which it was: "14 turns: target met", or "18 turns, target 14:
  not yet", with Retry where Next stage would be.
- **The briefing card and the stage list show the target**, so it is never a
  surprise on the win overlay. It is drawn beside the hint rather than written
  into it, so `HINT_MAX` keeps its row budget.
- **Set on the board from the author's own game.** `gauntlet-build.mjs` drops a
  pasted challenge link's score today ("no score to beat"). Instead the page
  would offer that score as the stage's target. An author plays their own
  gauntlet, pastes the links, and gets targets they have shown can be met.
  `checkBody` refuses a malformed target; `from_dict` drops one (the stage then
  has none) and never raises. The id hashes the body, so adding a target is a
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
