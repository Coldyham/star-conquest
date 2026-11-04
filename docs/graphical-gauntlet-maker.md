# Branching gauntlets, a gauntlet maker on the board, and roguelite runs with boons

*Working plan, kept here so the work can move between machines. Planned
2026-11-03 at `cf92eb9`. Not part of the design docs: as each phase lands, its
reasons move into `gauntlet.md` / `runs.md` (see "Docs") and this file is
deleted.*

## Context

Gauntlets today are linear in practice. The file format already carries a graph
(`stages` + `lanes` + `start`, one-way lanes), but nothing authors one: the board's
`gauntlet.html` edits order only, and the menu shows a gauntlet as a flat list of
36 px rows that cannot show a fork. That list also already overflows the 960 px
canvas past about 25 stages.

`docs/design/gauntlet.md` has three things proposed and not built:
- the roguelite run;
- targets on lanes (shortcuts and bonus stages, including hidden ones), which
  "waits for a page that draws lanes";
- boons carried between stages, decided against because a boon edits the setup,
  so it scores as a different map (*Carry-over*).

This plan builds all three, after a first step that needs none of them: a
linear **run** of an existing gauntlet, played in order within its targets with
one life (Phase 0). It is usable at once with a generated gauntlet's targets.
Then:
1. lane editing on the board's existing gauntlet page: each stage card gets a
   row of "Leads to" chips, and a lit chip carries the lane's options (two-way,
   a target, hidden). A diagram drawn by the page shows the shape;
2. a graph view in place of the game's stage list;
3. roguelite runs with simple boons.

The boons answer the old objection the way the Hint does. A boon never touches
`Settings`, so the stage keeps its challenge key. A game played with a boon is
unpostable, the same as a hinted one. Its result counts only towards the run.

Decisions taken with the user (2026-11-03):
- **the maker is the board's `gauntlet.html`, grown, not a new editor in the
  game.** The first draft put it in the game beside the map maker, only so as to
  reuse the map maker. But a gauntlet's lanes have no length and the game shows
  no geometry, so what the map maker would lend (placing, dragging, pan and zoom)
  is the part a gauntlet doesn't need. Everything a stage does need is already
  on the board page: pasted links, briefings, hints, the link's score and "Beat
  the best bot" as targets (only the board reads `bot_scores`), `checkBody`,
  Save and listing. HTML forms also edit text far better on a phone than a
  pygame sidebar with `softkeyboard`;
- **a stage's place on the graph is computed, never stored.** There is no `at`
  field. `layout` places stages by depth and order ("Layout", Phase 2), and the
  author's only say in the picture is the stages' order within a column;
- a run is a stored local record alongside bests, so any gauntlet can be played
  freely or as a run;
- the boons are extra home ships, home production −1 and scouting;
- boons are optional per stage, so a stage played without them still posts and
  still clears a best.

What is given up by not building in the game, judged acceptable:
- **Trying a stage from inside the maker.** On the board the author plays the
  stage in the game and pastes its challenge link back, which is today's flow and
  already sets the target. Each card gets a Play link to make that one click.
- **Making a gauntlet with no board** (`LEADERBOARD_ORIGIN` blank, or offline).
  Hand-written files still work on desktop, and the page's `.json` download
  covers getting a board-made one onto disk.
- **"Use menu setup" in place of Get Link.** A convenience, not a capability.

## Phase 0: a linear run (no format change, no boons, no branching)

**Built 2026-11-04 on `gauntlet-run`.** The reasons as built are in
`docs/design/gauntlet.md`, *Runs*. Where the build differs from what follows:
- **Retry and rewind after a run game is decided are not blocked.** The run
  judges only its own game, by log name, and only once (`run.finish`). A Retry
  or fork afterwards is unmarked free play, so it can't move the run.
- **While the game is undecided, the daily's ironman is reused** for "no
  rewind" (History hidden); no new gates were needed. `restart` was already
  kept out of a stage.
- **The active step's log is also kept in `webstore`** (`WEB_RUN_LOG_KEY`,
  rewritten every turn), because `games/` is MEMFS on the web build and a
  reload would otherwise lose the run's game.
- **There are two more outcomes**: `void` (won, but not by hand) and `gone`
  (the game couldn't be loaded). `abandoned` is a third, for a run given up
  from the list.
- **The run's stage is marked "run · …" in its row, not with ▶.** The menu
  font's glyph coverage is uncertain (render draws its ▶ rather than typing it).
- **`run.py` imports as `gauntlet_run` in `main`**, which has its own `run()`.

The first piece to build, because it is usable at once on what exists. Generate
a gauntlet on the board with "Stage targets" (the slowest or fastest probe win
plus slack, `gauntletgen.target`), or build one from links with "Beat the best
bot", then **run** it: every stage in order, each won within its target, with
one life. It needs no format change and no board change, and it is the start of
`run.py`, which Phase 5 grows.

- **Offered only where it means something:**
  - The gauntlet must be a chain (`gauntlet.is_chain`): one start, and from
    each stage but the last exactly one `next_stage`.
  - At least one stage must have a target.
  - The tutorial (no targets) and any branching gauntlet show no run button
    until Phase 5. A chain with targets on only some stages is still offered:
    a stage without one asks for any win.
- **The rule.** Starting from the first stage, play each stage in list order.
  Each must be won by hand (`Ui.can_post`), meeting its target with *this game's*
  score. An earlier best doesn't count, so `stage_met_before` is ignored in a
  run.
- **What ends a run:**
  - **Losing a stage** ends it.
  - **A win short of the target** ends it too. The overlay says by how much:
    "Run over: 18 turns, target 14".
  - **Winning the last stage within its target completes the run.**
- **One life, no second goes.** In a run game:
  - **Retry**, both rewinds (mid-game and after the end), `restart` and New map
    are not offered. `main.py:2599` already keeps `restart` out of a stage, and
    the same gates extend to `Ui.in_run`.
  - **The Hint** is not offered (`can_hint`). A run is a test, and a hinted win
    can't be posted anyway.
  - **History** stays available for looking back.
- **Run games are ordinary games.** They post, file bests and clear free-play
  stages exactly as now. A run adds nothing to `Settings` or to `GameLog`, so the
  verifier and the board never see a difference.
- **The record (core `run.py`, pure):**
  - It holds `{id, gauntlet_id, gauntlet_hash, started_at, status: active |
    complete | over, active_log, steps: [{stage, log, outcome: met | missed |
    lost, turns, lost}]}`.
  - `gauntlet_hash` is a hash of the kept file's canonical JSON.
  - `from_dict` is total and never raises.
  - It is stored in `webstore` under a new `paths.WEB_RUNS_KEY`: the active run
    plus the last 20 finished. `webstore` is `localStorage` on the web build and
    `kv.json` on desktop.
  - It is written when a stage starts (`active_log` set) and the moment its
    result is known, never only at the end of the run.
  - This is the one piece of gauntlet progress that is stored rather than
    derived. A run is a sequence of events, and can't be read back from bests,
    which keep only the best game on each stage.
- **Quitting mid-game keeps the run.**
  - The game is saved as usual. **Continue run** reopens `active_log` by path
    (`replay.load`) and re-applies `stage_ui` with the run marker. A resumed
    save otherwise forgets that it was a stage (`gauntlet.md`, "Next stage"),
    which is why the record holds the log.
  - The boot resume prompt (`main.py:2003-2008`, `replay.latest_log`) does the
    same when the latest log is the active run's.
  - The stage can't be started fresh while the run is active, so quitting a
    game that is going badly gains nothing.
  - If the log can no longer be loaded (cleared storage, or a moved
    `RULES_VERSION`, which `latest_log` skips), the run is over, with the
    outcome "lost its game".
- **Only one active run at a time.** Starting a run while another is active asks
  to abandon the old one. A run whose gauntlet's hash has changed (a desktop file
  edited) is over.
- **In the gauntlet list (today's list, no graph needed):**
  - Beside Leave and Copy link: **Start run**. While a run is active: **Continue
    run · stage 3/8** and **Abandon run** (with a confirm).
  - A line under the heading: "Best run: 5/8", or "Run complete ✓". It is read
    from the records, as the furthest any run of this gauntlet reached.
  - A ▶ marks the run's current stage row.
- **In the game:**
  - The top bar's stage label gains " · run".
  - **On a win within the target, Next stage (N) continues the run.**
    `Ui.offers_next_stage` in a run is `can_post and met_target(state)`.
  - On the last stage the win says "Run complete".
  - **A missed target or a loss** shows "Run over · reached stage 3/8" in place
    of the stage verdict, with **Back to the gauntlet** (to the list).
- **`Ui` and main.**
  - `stage_ui(..., run=run_id)` sets `Ui.in_run`.
  - `main.record_run_step` writes the step on the same end-of-game path that
    files a best (`main.py:1474`).
  - The Next stage path (`main.py:2603-2608`) also writes the next step's
    `active_log` when in a run.

Tests:
- `tests/test_run.py` (pure):
  - the offered rule (the tutorial: no; a chain with a target: yes; a fork: no);
  - each outcome, completion, and "over" on a hash change;
  - a step is recorded at both start and end;
  - `from_dict` is tolerant;
  - the best-run summary.
- `tests/test_viewstate.py`: in a run, an earlier best doesn't offer Next stage,
  and the Hint is not offered.
- A main test: no Retry or rewind in a run game; Continue run resumes the
  active log.
- `tests/test_menu*.py`: the buttons per case.

Size: about 450 lines of code (`run.py` ~150, menu ~120, main ~120, `viewstate`,
render and input ~60) and about 350 of tests. Run the full suite, since it
touches `main` and `viewstate`.

## Phase 1: the format grows (core `gauntlet.py`, board `gauntlet-build.mjs`)

This follows *Later: targets on lanes* in `gauntlet.md` as written. Every
addition is optional, so no existing file changes meaning.

- **A target on a lane:** `[a, b, {"turns": 12, "lost": 30, "hidden": true, "two_way": true}]`.
  - Parse with the existing `_target`, so the bot shape
    (`{"turns", "lost", "bot", "beat": true}`) works on a lane as on a stage.
  - `Gauntlet` gains `lane_targets: dict[(a, b), Target]` and `hidden: frozenset`.
  - A bad target means the lane has none. `hidden` on an ungated lane is
    dropped: there is nothing to reveal it.
- **Run end stages are derived, never stored** (`gauntlet.goals`). A goal is a
  reachable stage with no ungated lane out that is a start or has an ungated lane
  in. A bonus dead end behind a gate is therefore never a goal. There is no
  `goal` field: the board page has no control to set one, and the rule gives the
  right ends for every shape in the mockup. If a shape ever needs a goal the
  rule can't give, an explicit field can be added then; `checkBody` would learn
  it at that point.
- **Run settings are config defaults, not file fields:** `config.RUN_LIVES` (1)
  and `config.BOON_KINDS` (all three). A per-gauntlet `run` field waits for a
  page that sets it, and would be a format addition when it comes.
- **Rules:**
  - `open_stages` covers the starts, every won stage, and each stage a lane
    leads to whose condition the near end's best meets. An ungated lane's
    condition is the stage's own target.
  - `depths()` walks ungated lanes only, so a shortcut doesn't steal Next stage.
  - `problems()` reachability ignores targets.
  - A stage reached only by hidden lanes is not shown until one of those lanes
    opens.
- **Derived labels, never stored.** A gated lane is a "Bonus" when its far end
  reaches no goal, or else a "Shortcut" when it skips a column ("Layout",
  Phase 2), or otherwise plain "Gated". The game's stage detail strip and the
  board's lane options use the same three words.
- **New `to_dict(g)`.** It writes the canonical file, and
  `from_dict(to_dict(g)) == g` gets a test.
- **`checkBody` / `_from_dict` parity.** `checkBody` learns the lane keys
  `turns`/`lost`/`bot`/`beat`/`hidden` (today it refuses any lane key but
  `two_way`). The top-level keys are unchanged. Add a shared
  fixture read by both the Python and JS suites, the way `gauntlet-spec.json`
  works.
- **Until Phase 3 lands**, `gauntlet.html` opened on a gauntlet with lanes says
  "this gauntlet branches; saving here would make it linear" instead of
  silently flattening it. Phase 3 replaces the warning with real editing.

Tests: extend `tests/test_gauntlet.py` (lane gates, hidden, depth over ungated,
derived goals and labels, round trip) and
`leaderboard/tests/gauntlet-build.test.mjs`.

## Phase 2: the graph view replaces the stage list (`menu.py`)

`_draw_tutorial` / `_handle_tutorial` (menu.py:764-913) become a graph panel in
the same modal. The heading, gauntlet picker, notes, Leave, Copy link and Close
all stay.

### Layout (core `gauntlet.layout`, mirrored by the board)

Positions are computed from the graph, never stored, so the game and the board
must agree on them. `gauntlet.layout(g, shown=None)` returns a
`{stage id: (column, row)}` grid, pure and deterministic:

- **Column is depth over ungated lanes** (`depths()`), so the main path reads
  left to right and a shortcut is a long arrow, not a stage moved forward.
- **A stage reached only by gated lanes** has no ungated depth. Its column comes
  from one breadth-first walk over *every* lane, seeded with all the ungated
  depths at once, each lane adding one column. The stage therefore lands one
  column past its shallowest placed source, and a chain of them (A gated→X
  gated→Y) is placed the same way whatever the list order. The mockup's
  repeated passes in list order are the thing to avoid: they can place such a
  chain differently depending on which stage comes first in the list.
- **A stage no start reaches** (a `problems()` blocker) goes in a last column of
  its own, so the board can draw it red rather than drop it.
- **Row is the stages' list order within the column**, then one pass of
  crossing reduction: each column after the first is sorted by the average row
  of its parents (ties keep list order). One pass, not iterated, so the author's
  order still decides most of the picture and a small edit never reshuffles it.
- **Hidden stages are laid out, then left undrawn.** The grid is computed over
  the whole graph, so revealing a bonus stage moves nothing else. The cost is a
  gap where it will appear; the board's diagram nudges the author to put such a
  stage last in its column, so the gap is at the edge.
- **Two-way lanes count in both directions for depth**, as `Gauntlet.onward()`
  and `depths()` already walk them (and as the mockup does). They are drawn as a
  double-headed arrow.

`leaderboard/js/gauntlet-graph.mjs` implements the same function for the board.
A shared fixture, `tests/fixtures/gauntlet-layout.json` (graphs and their
expected grids and derived goals and labels: linear, a fork that rejoins, a
shortcut, a gated bonus, a hidden bonus, a chain of gated-only stages listed
out of order, an unreachable stage, a two-way lane reached only backwards), is
read by
`tests/test_gauntlet.py` and `leaderboard/tests/gauntlet-graph.test.mjs`, so the
two cannot drift.

### The view

- **Nodes.** Stages are drawn at their `layout` cell, the grid scaled to fit the
  panel whatever the stage count, which fixes the overflow.
- **Node states:** cleared ✓, won short of target, open (pulsing), locked
  (dim). Starts and goals get a small mark.
- **Lanes** are arrows.
  - Move `render._draw_arrowhead` (render.py:355) into `widgets` so menu can use
    it, and leave a re-export in render.
  - A gated lane is drawn dashed, with its `Target.short()` label ("≤12").
  - A hidden lane, and any stage only it reaches, is not drawn until met.
- **The drawer is its own shell module**, `stagegraph.py`, so the run view
  (Phase 5) draws with it too. It knows nothing of the menu. It takes the
  surface, a rect, and a list of stage nodes (cell, label, state) and lanes
  (ends, gated, label, hidden), draws them and returns a rect per node for
  hit-testing. It imports neither `engine` nor `ai`, and never mutates what it
  is given. Add it to CLAUDE.md's shell module list.
- **The detail strip.** Tapping a node selects it, and a strip under the graph
  shows its title, best, target and each way on with what it needs ("Shortcut:
  win in 12"). It has **Play** (the existing `_load_stage` + `_start`) and, in a
  run, the run controls (Phase 5). A gauntlet stored on the board (`#g=`) also
  gets **Edit on the board**, which opens `gauntlet.html#<id>` as a draft.
- **Keyboard.** Arrow keys move between nodes and Enter plays, so it stays
  usable without a mouse.
- **Compact.** It keeps working through `_fit_modal`, and the touch targets are
  floored at `config.TOUCH_MIN_TARGET`.
- **Win overlay.** Next stage (N) is unchanged: it uses `gauntlet.next_stage`,
  and a fork sends the player to the graph.

Tests:
- `tests/test_gauntlet.py`: the layout fixture.
- `tests/test_menu*.py`: every node's rect is inside the panel at 1, 25 and 64
  stages and at scale 1, 1.5 and 2. Hidden stages are absent, and a locked node
  is not clickable.
- `tests/test_stagegraph.py` (headless): every state draws, the returned rects
  match the cells, and a hidden lane draws nothing.

## Phase 3: lanes on the board's gauntlet page (`gauntlet.html`, `gauntlet-page.mjs`, `gauntlet-build.mjs`, new `gauntlet-graph.mjs`)

The page keeps its stage cards: one pasted link per stage, title, briefing,
hint, target, ↑/↓, Remove. Phase 3 adds the graph to them. Nothing in the game
changes. A working mockup of the cards, chips, lane options and diagram, with
the body Save would store: https://claude.ai/artifact/QbeXvScWNfVzj5XK5rubGB
(private to the author).

### The draft

- **Lanes join stages by a draft key, not by index.** A draft stage gets a
  `key` (a counter, kept in the stored draft) when it is made, so reordering
  and removing never rewire a lane. `draft.lanes` is a list of
  `{from, to, two_way?, gate?, hidden?}` by key, and `draft.start` a list of
  keys.
- **`buildBody` numbers stages 0.. in list order, as now, and maps keys to
  ids.** It writes `lanes` and `start` only when they differ from what
  `from_dict` assumes (the list-order chain, the first stage open), so a linear
  gauntlet saves exactly the body it saves today and keeps its id.
- **Defaults keep the linear case free.** A new draft and **+ Stage** (at the
  bottom, as now) join the new stage from the last one. An author who never
  touches a chip gets today's linear gauntlet.
- **Remove** drops the stage's lanes and its start mark. A stage left with no
  way in is then flagged by the diagram, not repaired behind the author's back.
- **`readDraft` keeps the graph.** Opening `gauntlet.html#<id>` on a branching
  gauntlet loads its lanes, start, lane targets and hidden marks; Phase 1's
  "would make it linear" warning goes. **Open a file** (a file input, read with
  `FileReader`) loads a `.json` gauntlet the same way, for desktop files and
  generated gauntlets the board never saw.

### Each stage card gains

- **Leads to:** a row of chips, one per other stage, labelled by its title (or
  "Stage N"). Lit means a lane from this stage to that one. Tapping an unlit
  chip adds the lane; tapping a lit chip opens its options. The far stage's own
  card shows the lane too, as a small "from Stage 2" note, so either end
  explains it.
  - **A pair joined the other way.** When B→A already exists, A's chip for B
    reads "↔ make two-way" rather than adding a second lane, since the format
    keeps a pair's first lane and drops the second.
  - **Many stages.** Up to 16 stages every chip shows. Past that, the lit chips
    show and the rest sit behind "+ Lane to…", a select, so a 64-stage card is
    not 63 chips.
- **Open from the start:** a tick per card, writing `start`. The first stage's
  is ticked by default.
- **+ After** adds a stage directly below this card with a lane from this one.
  **+ Branch** adds a stage with a lane from each of this stage's parents (from
  nothing, and ticked as a start, if this stage is a start), so it lands beside
  this stage in the same column. These are how most forks get made; the chips
  are for joining and fixing.
- **↑/↓** move the stage in list order as now. Since a column's rows are the
  list order (Phase 2, "Layout"), this is how the author untangles a crossing.
- **Play** opens the stage's pasted link in the game, so trying a stage and
  pasting back its challenge link is one round trip.

### The lane's options (shown under the card when a lit chip is tapped)

- **Two-way** (rare, kept for completeness).
- **Gate:** *None: any clear of this stage opens it*; *Win in N turns* (and
  optionally *N ships lost*) on number inputs; *Your score from this stage's
  link* (`linkTarget` on the near stage's pasted challenge link); *Beat the best
  bot on the board* (`botRows`/`botTarget` for the near stage's setup). These
  are the stage target's choices, so `applyAim` and its notes are reused, aimed
  at a lane.
- **Hidden until met:** enabled only when gated.
- **The derived label** shows as you set it, from the same rule as the game
  (Phase 1): "Bonus" when its far end reaches no goal, "Shortcut" when the lane
  skips a column, otherwise "Gated".
- **Remove lane.**

### The diagram

A read-only SVG above the cards, drawn from `gauntlet-graph.mjs`'s `layout`
(Phase 2), so it is the picture the game will show.

- Columns by depth, rows by order; one-way arrows; gated lanes dashed with their
  short label ("≤12"); hidden lanes dotted with an eye mark; starts and goals
  marked.
- **Problems in place:** a stage no start reaches is drawn red in the last
  column, a gated lane that is a stage's only way in is amber (the warning from
  Phase 1's rules), and Save says what blocks it, as `draftProblem` does today.
- **As the author sees it / As a new player sees it.** A toggle above the
  diagram. The player view drops hidden lanes and the stages only they reach,
  leaving the gap the game leaves (the grid is computed over the whole graph,
  "Layout"). It is how the author checks that a hidden bonus really is hidden,
  and sees where the gap falls.
- Clicking a node scrolls to its card and flashes it; hovering a chip lights its
  lane in the diagram.
- It scrolls sideways in its own container past the page width, so 64 stages
  never widen the page.
- No dragging, and the diagram never edits. A later step could let a click on A
  then B toggle a lane, as a shortcut over the chips; it needs no new model.

### Checks

`checkBody` (Phase 1) already refuses what the game would drop. The page adds
nothing the store doesn't check, and runs `checkBody` before offering Save as
now. The id hashes the body, so adding a lane is a new row and the old link keeps
opening the old gauntlet.

Tests (`node --test leaderboard/tests/*.test.mjs`):
- `gauntlet-graph.test.mjs`: the shared layout fixture, and the derived labels.
- `gauntlet-build.test.mjs`:
  - a draft whose chips were never touched builds the same body, byte for byte,
    as today's linear builder, so its id doesn't move;
  - keys survive reorder and remove; a removed stage's lanes go;
  - the reverse-pair chip sets `two_way` and never adds a second lane;
  - `readDraft` of a branching body round-trips through `buildBody`;
  - a lane gate from a link, and from bot rows, has the stage target's shape.

## Phase 4: boons in the game log (core, no `Settings` field)

- **New core `boons.py` (pure).**
  - `Boons` (frozen) holds `ships: int`, `production: int` (how many −1 steps)
    and `sight: int` (extra fog hops).
  - Its `from_dict` is total and never raises.
  - `apply(state, pid, boons)` runs after `build_state`:
    - it adds `config.BOON_SHIPS × ships` to the human's home node;
    - it sets `production = max(1, production − n)` on the human's home node,
      because `Node.production` is turns per ship (`model.py:209`), so lower is
      richer;
    - it draws nothing from `state.rng`.
  - `sight` is presentation only: `fog` is never read by the engine or the AI.
- **The log.**
  - `GameLog.boons` (a dict, written only when non-empty, like `hinted_from`) is
    threaded through `to_dict`/`from_dict`, `new_log`, and `fork` and
    `truncate`, which keep it because it applies from turn 0.
  - `replay.reconstruct` applies it right after `build_state` (replay.py:630),
    next to the `strategies` stamp, so resume, rewind, history, the film and
    watched replays all rebuild the same board.
  - `RULES_VERSION` does not move, because a log without boons reconstructs as
    before.
- **Starting a game.** `main.start_game` applies the boons and stamps the log.
- **Fog.** `main.py:200` reads `config.FOG_SIGHT/SCOUT + ui.boons.sight`,
  capped at `FOG_MAX_HOPS`.
- **Unpostable, like the Hint.** `Ui.boosted` (set from `log.boons`, sticky on
  resume) joins `can_post` (viewstate.py:831), so a boosted game loses posting,
  challenge sharing, filing a best, and a free-play stage clear.
  - `tools/verify_scores.py` refuses a log that carries `boons`, as it does
    `hinted_from` (verify_scores.py:348).
  - `share.due` skips a boosted log. Nothing would ever verify it.
- **What the player sees.** The top bar shows the boons in play ("+10 ships ·
  prod −1 · sight +1"). The win overlay says "Boons used: not postable" where
  the post buttons would be. Unlike the Hint, nothing is silently hidden.
- **Config.** `BOON_SHIPS` (start at 5) and the offer sizes go in `config.py`.
  Read the actual starting-ships constant and tune against it.

Tests: `tests/test_boons.py`:
- `apply`, including the production floor;
- the log round trip, and reconstruct reproducing a boosted game from its log;
- `fork` keeps boons;
- `can_post` is false;
- the verifier refuses a boosted log.

Then run the full suite, since this touches `replay` and `main`.

## Phase 5: runs (core `run.py`, menu graph overlay, main)

**This grows Phase 0's run rather than starting one.** The record, its storage,
Continue run through `active_log`, the boot prompt, no Retry or rewind, no Hint,
and one active run all stay as Phase 0 built them. What Phase 5 adds:
- movement over a graph, which lifts `is_chain` from the offered rule;
- lane gates judged on the run's own score;
- lives;
- boons, and with them the boon-pick card and the Use boons switch.

The record's new fields (`seed`, `lives_left`, `boons_in_play` and `boon_taken`
per step) are read tolerantly, so a Phase 0 record stays readable.

**The record (pure, `run.py`).** A run is a complete record, not derived
progress (memory: complete records beat compact ones).
- It holds:
  - `gauntlet_id` and a hash of the gauntlet file;
  - a `seed` rolled with `settings.random_seed()`;
  - `lives_left`;
  - `steps`, where each step is `{stage, boons_in_play, outcome, turns, lost,
    log_name, boon_taken}`.
- What it can answer:
  - **held boons:** the fold of the steps' `boon_taken`;
  - **status:** active, won or over;
  - **position:** the stage last won.
- **Moving.** Roguelite movement is from the current stage only:
  - the next choices are the starts at the beginning, then the lanes out of the
    position whose condition this run's win meets (`Target.met` on this step's
    score, not on bests);
  - a hidden lane shows only once met.
- **Boon offers** come from a derived stream, `random.Random(f"{seed}:{step}")`.
  - It offers `config.BOON_OFFER` (3) choices drawn from `config.BOON_KINDS`.
  - The player picks one or skips.
  - Add it to CLAUDE.md's list of sanctioned derived streams.
- **Losses and endings:**
  - A run starts with `config.RUN_LIVES` lives. A loss costs a life. With lives
    left, the same stage is replayed fresh.
  - Winning a goal stage (`gauntlet.goals`, derived) wins the run.
  - A run game offers no Retry and no rewind, because otherwise a run can't be
    lost. Resuming a saved run game continues it.
- **Storage.**
  - Runs are kept in `webstore` under a new `paths.WEB_RUNS_KEY`: the last 20,
    plus the one that is active.
  - The record is written the moment a step finishes, as each game ends, and
    never only at the end of the run.
  - A gauntlet whose file hash changed marks its active run stale. The run still
    shows, but nothing more is played in it.

**Playing a run.**
- **The graph modal:**
  - **Start run** (or **Continue run** / **Abandon run**);
  - the path taken is drawn bold;
  - the current choices pulse;
  - a strip shows held boons and lives.
- **Before a stage, the Use boons switch.** It is on by default when any boons
  are held, and saying "off: this game can be posted" when off. The choice is
  made per stage, as the user asked.
- **Stage identity.** The run's stage is carried on `Ui` by `main` (`Ui.run_step`)
  rather than re-found by `menu.active_stage`. Boons don't touch `Settings`, so
  the key would match anyway, but the run must not depend on that.
- **Win overlay in a run:**
  - **Continue run** replaces Next stage, and opens the boon pick card.
  - It then goes to the graph to choose the way on. If there is only one way, it
    goes straight there.
  - When the win is boonless and postable, Post and Challenge stay alongside.
- **Loss overlay in a run:** "Run over · reached stage 6 · 3 boons". With lives
  left: "Lives left: 1 · Try again".
- **Free play and runs stay apart.** A run never clears a free-play stage except
  through an ordinary boonless win filing its best as now, and free-play bests
  never advance a run.

Tests: `tests/test_run.py`:
- movement from the position only;
- lane gates on the run's own score;
- hidden reveal;
- lives;
- goal detection;
- the derived boon offers are stable for a seed and step;
- stale on a hash change;
- the record is written per step.

Main wiring tests:
- a run win offers Continue run and not Next stage;
- Retry is absent in a run game.

## Docs (same PRs as the code)

- **`gauntlet.md`** (811 lines). Move *Generated gauntlets* out to a new
  `gauntlet-gen.md` to stay under about 1000 lines. Then:
  - turn *Later: targets on lanes* into a built section;
  - add *The graph view* (with *Layout*, and why positions are computed) and
    *Lanes on the board* (and why the maker is the board page, not an editor
    in the game).
- **New `docs/design/runs.md`:**
  - runs, and the record and why it is stored;
  - boons, and why they are unpostable rather than a setup edit;
  - lives, and no Retry in a run.
- **`decided-against.md`.** Amend the Carry-over line: "boons as a setup edit".
  The unpostable boons replace that idea, and `runs.md` carries the reason. Add
  two lines: an in-game gauntlet maker built on the map maker (geometry a
  gauntlet doesn't use; the board already edits the rest), and a stored `at`
  position per stage (the author would place what `layout` already places).
- **`open.md`.** Remove the two gauntlet entries that are now built.
- **`sections.md`** and **`docs/README.md`**: add the new files and sections.
- **`rules.md`** *Gauntlets*, and **CLAUDE.md**:
  - the Gauntlets paragraph: lane targets, the maker, runs, and "a boon game is
    unpostable; boons live in `GameLog.boons`, never `Settings`";
  - the core module list: `boons`, `run`;
  - the shell list: `stagegraph`;
  - the Gauntlets paragraph also gets "a stage's place is computed
    (`gauntlet.layout`, mirrored by `gauntlet-graph.mjs`), never stored";
  - the derived-stream list: run offers.

## Order and PR shape

There are six PRs, each usable on its own:
0. a linear run, first: no format, board or replay change, and immediately
   playable on generated gauntlets with targets;
1. the format;
2. the graph view, `stagegraph` and the shared layout, which are useful at once
   for existing gauntlets;
3. lanes on the board's gauntlet page;
4. boons in the log;
5. runs.

Phases 1 and 2 alone already give branching gauntlets with shortcuts and hidden
bonus stages, written by hand, and they unblock the board's hidden-lane idea.
Phase 3 needs only Phase 1 to save and Phase 2's layout fixture to draw, so it
can be built alongside Phase 2. Phase 4 has no UI entry point until phase 5,
but it is tested headless.

### Size estimate

These are estimates scaled from code already in the repo, not counts from a
design.

| Phase | New or changed code | Tests | Size | Main reuse |
|---|---|---|---|---|
| 0. Linear run | ~450 (`run.py` ~150, menu ~120, main ~120, the rest ~60) | ~350 | Small-medium; touches `main` (run the full suite) | the stage list, `Target`, Next stage, `stage_ui`, `replay.load`, `webstore` |
| 1. Format | ~230 (`gauntlet.py` ~170, `checkBody` ~60) | ~250 | Small | `Target`, `_target`, the existing `from_dict` and `checkBody` |
| 2. Graph view, `stagegraph`, `layout` | ~520 (replaces ~150 in menu; `layout` ~70) | ~250 | Medium | `_fit_modal`, `widgets`, the arrowhead helper, `open_stages`/`cleared` unchanged |
| 3. Lanes on the board | ~550 (`gauntlet-graph.mjs` ~150, page ~300, build ~50, CSS ~50) | ~250 | Medium | the stage cards, `applyAim`, `linkTarget`/`botTarget`, `checkBody`, `readDraft` |
| 4. Boons in the log | ~250 | ~200 | Small, but it touches replay (run the full suite) | the Hint's unpostable path, the `strategies` stamp in `reconstruct` |
| 5. Runs, grown | ~700 (`run.py` +150, run view and boon pick ~400, wiring ~150) | ~350 | Medium | Phase 0's record and wiring, the Phase 2 graph view |
| Docs | ~400 lines of prose | n/a | Small | mostly moving *proposed* sections to *built* |

The total is about 2,700 lines of code and 1,650 of tests. Phase 0 costs about
250 lines over building runs once at the end, because Phase 5 then extends a
record and wiring that already exist instead of writing them. Against the in-game
maker this replaces (Phase 3a, the `editkit` extraction, ~750; Phase 3b, the
editor and `GauntletDraft`, ~1,100 plus ~900 of tests), the maker is about a
third of the size, and mapmaker is not touched at all. The parts most likely to
grow are the lane options (the gate choices reuse the stage target's, but each
needs its notes and its async bot lookup) and the main wiring for runs.

## Verification

- **Per phase:** the tests named above, `uvx ruff check .`, and `uvx pyright`
  on touched files. Run `node --test leaderboard/tests/*.test.mjs` in phase 1.
  Run it again in phases 2 and 3 (the layout fixture).
  Run the full `uv run pytest` after phases 4 and 5 (they touch `replay`,
  `main` and `viewstate`).
- **End to end** (a deploy preview of the board, and `uv run python main.py`):
  1. On `gauntlet.html`, build a seven-stage graph:
     - a main path 1→2→3→4, by + After;
     - a fork 1→2b by + Branch, rejoining at 3 by a chip;
     - a shortcut 2→4 gated "win in 12";
     - a hidden bonus 3→B gated "win in 10";
     - Play a stage from its card, paste the challenge link back, and set a
       lane's gate from that link's score.
     The diagram shows the shape at each step; removing 2 turns 2b's lane into
     3's only way in and nothing goes red, removing 1 turns the rest red.
  2. Save, open `#g=` in the game, and check the graph matches the page's
     diagram cell for cell, the dashed gated lanes, and that B is absent.
     Reopen the board page on the id (Edit on the board) and see the same
     draft, lanes included.
  3. Free play: clear 1 and 2 fast. The shortcut opens and the graph shows it.
  4. Start a run:
     - win 1 and take +ships;
     - play 2 with boons on, and see the top bar note, the win overlay's "Boons
       used: not postable", and no Post button;
     - continue to the branching choice;
     - lose a stage and see the run end.
  5. Resume mid-run from a saved game and rewind a boosted game's history. The
     board must match: reconstruct applies the boons.
  6. Copy link, open the `#gauntlet=` link in a fresh profile, and see the same
     graph.
  7. Repeat the graph view on the phone layout (compact), and run
     `tools/build_web.sh` for the web build.
- **Phase 1 interop:** a file with lane targets saved through `/api/gauntlet` on
  a deploy preview reloads by `#g=`.

## Defaults taken without asking (confirm before building)

- **The board page's details** (Phase 3): every chip shows up to 16 stages, past
  that a "+ Lane to…" select; + Branch copies the stage's parents; one pass of
  crossing reduction, not iterated; a lane's gate offers the same four choices
  as a stage target.
- **One life (`config.RUN_LIVES`), and no Retry or rewind in a run game.**
  Without that a run cannot be lost. Lives and the boon pool are the same for
  every gauntlet until the board page gets a "Played as a run" section and the
  format a `run` field.
- **Goals are derived only** (Phase 1), with no `goal` field until a shape needs
  one.
- **Boons are permanent for the run and stack**: ships add up, production steps
  down to a floor of 1, and sight adds hops up to `FOG_MAX_HOPS`.
- **The Use boons switch is all held boons or none.** It is not a per-boon
  choice.
- **`BOON_SHIPS = 5` and `BOON_OFFER = 3`** are placeholders. Tune them against
  the real home start ships.
- **A run's maps are the gauntlet's pinned seeds**, so its variety comes from
  path and boon choices. A dealt run graph (`gauntlet.md`, *The gauntlet:
  proposed, not built*: `tools/campaign.py` `_layout`, `cut_nodes`,
  `_from_family`, `_mystery`) is a later step. On the board it could become
  a stage card that holds a spec instead of a link, dealt by the game as the
  generate form's gauntlets are (the board has no engine).
- **The sight boon does nothing on a no-fog stage.** It is still offered there.
  Whether to filter offers by the stages ahead is open.
- **A boosted replay still uploading under Share replays**: the hint's log
  does (`share.due` never checks `hinted`). The plan skips boosted logs. Check
  whether hinted logs should be skipped too.

## Code map from the planning search (line numbers at `cf92eb9`)

### Hint, the unpostable precedent
- The gate is `Ui.can_post` at `viewstate.py:805-832`, and `Ui.hinted` is at
  `:435`.
  - `stage_cleared` (`:926`) and `offers_next_stage` (`:932`) both go through
    `can_post`, so an unpostable game never clears a free-play stage.
- How a hint is taken:
  - The first K press goes to `main.press_hint` (`main.py:1333`), which opens a
    confirm modal (`render.draw_confirm_hint`, `render.py:4107-4122`).
  - Show calls `main.take_hint` (`:1351`). It sets `ui.hinted` and
    `log.hinted_from` and saves the log at once (`:1358-1364`).
  - On resume, `ui.hinted = log.hinted_from is not None` (`main.py:324`).
- In `replay.py`:
  - `GameLog.hinted_from` is at `:238`, written only when set (`:395`) and read
    tolerantly (`:410`, `:422`).
  - `truncate` drops it (`:310`), and `fork` keeps it only up to n (`:335`).
  - Retry is `fork(0)` (`main.py:2618`), so it clears the hint unless the hint
    was taken on board 0. Boons must not behave this way: they apply from
    turn 0.
- The gates in `main.py`:
  - `record_best` runs only behind `can_post` (`main.py:1474`, defined at
    `:1654`);
  - share (challenge link) is gated at `:2552`, post at `:2558`.
- On the win overlay:
  - the buttons are hidden by `share = ui.can_post(state)` (`render.py:3342`);
  - the stage verdict is hidden at `:3579`.
  - After the modal nothing says the game is hinted. Boons should say so instead.
- The verifier refuses hinted logs at `tools/verify_scores.py:348-350`.
- The design doc is `docs/design/training.md:89-107`.

### Reconstruct and starting a game
- `replay.reconstruct` is at `replay.py:590-649`.
  - `build_state` is at `:630`, and the `strategies` stamp at `:631-634` is the
    only precedent for a change applied after `build_state`. Apply boons there.
- A new `GameLog` field must be threaded through:
  - `to_dict`/`from_dict` (`:381-428`);
  - `fork` (`:319`), which rebuilds the log field by field;
  - `new_log` (`:513`).
  - `pbp.py:489` calls `build_state` directly. Boons never reach play-by-post.
- Everything that rebuilds through `reconstruct`:
  - `resume_game` (`main.py:306`);
  - `apply_rewind` (`:1300`);
  - `build_history` with the film's `on_event` (`:1296`);
  - `open_replay` (`:807`);
  - `pbp.py:566`, the verifier (`verify_scores.py:332`), and many tools.
- New matches:
  - `start_game` is at `main.py:284-291`, reached through `_begin_game`
    (`:220`).
  - `_begin_game` is called from menu Start (`:2485`), map maker Play
    (`:2523`), Next stage (`:2603-2608`), New map (`:2601`) and the CLI
    (`:1880`).
- Stage wiring:
  - `main.stage_ui` (`main.py:234-256`) copies the label, the target,
    `stage_met_before`, the briefing and `has_next_stage` onto `Ui`.
  - Rewind and Retry re-apply it with `briefing=False` (`:2405`, `:2628`,
    `:2679`).
- Fog:
  - `fog.observe(state, pid, sight, scout)` is at `fog.py:19`.
  - Its only caller is `main.py:200`, with `config.FOG_SIGHT/FOG_SCOUT`. Add
    the sight boon there.
- Production: `Node.production` is turns per ship (`model.py:209`), and the
  menu's range is 1-8 (`settings.py:113`).

### The gauntlet list in the menu (Phase 2 replaces it)
- **State:** `MenuState` gauntlet fields are at `menu.py:336-359`: `stage_at`,
  `stash`, `tutorial_open`, `gauntlets`, `gauntlet_pick`, `stage_bests`,
  `kept_ids` and `gauntlet_note`.
- **Opening and messages:**
  - the button label is `_gauntlets_label` (`:722`), drawn at `:1982`
    (desktop) and `:2305` (compact);
  - the list opens through `_open_tutorial` (`:686`) and `open_gauntlet`
    (`:700`);
  - in-list news goes through `say_gauntlet` (`:706`).
- **Drawing:** `_draw_tutorial` is at `:764-881`. Its row height is
  `_STAGE_ROW_H = 36` (`:759`), and it has no scroll, so 64 stages overflow the
  960 px canvas.
- **Handling:**
  - `_handle_tutorial` is at `:884-913` and reads only `_TUTORIAL_KEYS`
    (`:760`). The dispatch is at `:2904`.
  - Picking a stage runs `_load_stage` (`:618`), then `_start`.
  - `active_stage` is at `:585` and `next_stage` at `:604`.
- **Phone:** `_draw_menu_compact` is at `:2163`, and `_fit_modal` (`:2568`)
  scales a design-size modal down to fit.
- **Next stage on the win overlay:**
  - `render.py:3335-3336` labels it, and `:3389-3395` shows Retry alone in a
    stage;
  - `_stage_verdict` is at `render.py:3600`;
  - input handles N at `input.py:499-500` and the rect at `:521`.

### The board's builder
- `leaderboard/js/gauntlet-build.mjs`:
  - `LIMITS` is at `:23`, `buildBody` at `:152` (always linear), and `checkBody`
    at `:170-233`, shared with `netlify/functions/gauntlet.mjs`.
  - Only `name`, `stages`, `lanes` and `start` are kept; other top-level keys
    are dropped silently. A lane's third element may only say `two_way`.
  - `unreachable` is at `:242`.
- `leaderboard/js/gauntlet-build.mjs` also holds the draft: `emptyStage`
  (`:331`), `emptyDraft` (`:345`), `readDraft` (`:384`, flattens a branching
  gauntlet today), `draftProblem` (`:419`) and `moveStage` (`:433`).
- `leaderboard/js/gauntlet-page.mjs`: `renderStage` (`:219`) builds a card (the
  ↑/↓/Remove tools at `:225-247`, the target select and `applyAim` after it);
  `keep()` (`:55`) stores the draft in `localStorage`; `#<id>` loads a stored
  gauntlet (`:394`, `:477`).
- `leaderboard/gauntlet.html` is a Jinja template on `templates/base.html`
  (`tools/render_board.py`), styled by `leaderboard/css/style.css` (the dark
  arcade cabinet; pixel face for chrome, monospace for prose).
