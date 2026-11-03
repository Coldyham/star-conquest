# Branching gauntlets, an in-game gauntlet maker, and roguelite runs with boons

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

This plan builds all three:
1. an in-game gauntlet maker in the style of the map maker, where stages are
   nodes you drag around in 2D and lanes are one-way arrows that can carry
   targets;
2. a graph view in place of the stage list;
3. roguelite runs with simple boons.

The boons answer the old objection the way the Hint does. A boon never touches
`Settings`, so the stage keeps its challenge key. A game played with a boon is
unpostable, the same as a hinted one. Its result counts only towards the run.

Decisions taken with the user (2026-11-03):
- the maker lives in the game, beside the map maker;
- a run is a stored local record alongside bests, so any gauntlet can be played
  freely or as a run;
- the boons are extra home ships, home production −1 and scouting;
- boons are optional per stage, so a stage played without them still posts and
  still clears a best.

## Phase 1: the format grows (core `gauntlet.py`, board `gauntlet-build.mjs`)

This follows *Later: targets on lanes* in `gauntlet.md` as written. Every
addition is optional, so no existing file changes meaning.

- **A target on a lane:** `[a, b, {"turns": 12, "lost": 30, "hidden": true, "two_way": true}]`.
  - Parse with the existing `_target`.
  - `Gauntlet` gains `lane_targets: dict[(a, b), Target]` and `hidden: frozenset`.
  - A bad target means the lane has none.
- **A stage's place on the graph:** `"at": [x, y]`, ints in a 0..1000 box
  (`GRAPH_BOX`), clamped on read.
  - When it is missing, `layout(g)` places stages by depth (column) and rank
    within the depth (row). Existing linear gauntlets therefore draw as a row
    with no file change.
- **Run end stages:** `"goal": [ids]`.
  - Default: the sinks reached by ungated lanes, so a bonus dead end is never a
    goal.
- **Run settings:** `"run": {"lives": 1, "boons": ["ships", "production", "sight"]}`.
  - Optional. The defaults are one life and all three boons.
- **Rules:**
  - `open_stages` covers the starts, every won stage, and each stage a lane
    leads to whose condition the near end's best meets. An ungated lane's
    condition is the stage's own target.
  - `depths()` walks ungated lanes only, so a shortcut doesn't steal Next stage.
  - `problems()` reachability ignores targets. It also names an unreachable
    goal.
  - A stage reached only by hidden lanes is not shown until one of those lanes
    opens.
- **Derived labels, never stored.** A gated lane is a "Shortcut" when it skips
  depth, or "Bonus" when its far end leads back to no goal.
- **New `to_dict(g)`.** It writes the canonical file. The maker needs it, and
  `from_dict(to_dict(g)) == g` gets a test.
- **`checkBody` / `_from_dict` parity.** `checkBody` learns `at`, the lane keys
  `turns`/`lost`/`hidden`, `goal` and `run`. Add a shared fixture read by both
  the Python and JS suites, the way `gauntlet-spec.json` works.
- **Board remix.** `gauntlet.html`, opened on a gauntlet that has lanes, `at` or
  targets, says "open it in the game's gauntlet maker" instead of silently
  flattening it to a linear one.

Tests: extend `tests/test_gauntlet.py` (lane gates, hidden, depth over ungated,
goal default, round trip) and `leaderboard/tests/gauntlet-build.test.mjs`.

## Phase 2: the graph view replaces the stage list (`menu.py`)

`_draw_tutorial` / `_handle_tutorial` (menu.py:764-913) become a graph panel in
the same modal. The heading, gauntlet picker, notes, Leave, Copy link and Close
all stay.

- **Nodes.** Stages are drawn at `layout(g)` positions, scaled to fit the panel
  whatever the stage count, which fixes the overflow.
- **Node states:** cleared ✓, won short of target, open (pulsing), locked
  (dim). Starts and goals get a small mark.
- **Lanes** are arrows.
  - Move `render._draw_arrowhead` (render.py:355) into `widgets` so menu can use
    it, and leave a re-export in render.
  - A gated lane is drawn dashed, with its `Target.short()` label ("≤12").
  - A hidden lane, and any stage only it reaches, is not drawn until met.
- **The detail strip.** Tapping a node selects it, and a strip under the graph
  shows its title, best, target and each way on with what it needs ("Shortcut:
  win in 12"). It has **Play** (the existing `_load_stage` + `_start`) and, in a
  run, the run controls (Phase 5).
- **Keyboard.** Arrow keys move between nodes and Enter plays, so it stays
  usable without a mouse.
- **Compact.** It keeps working through `_fit_modal`, and the touch targets are
  floored at `config.TOUCH_MIN_TARGET`.
- **Win overlay.** Next stage (N) is unchanged: it uses `gauntlet.next_stage`,
  and a fork sends the player to the graph.

Tests: `tests/test_menu*.py`. Every node's rect is inside the panel at 1, 25 and
64 stages and at scale 1, 1.5 and 2. Hidden stages are absent, and a locked node
is not clickable.

## Phase 3: the gauntlet maker (new shell `gauntletmaker.py`, core `GauntletDraft`)

**Model (core, in `gauntlet.py` or a new `gauntletdraft.py`, added to the core
list in `test_no_core_module_imports_pygame` and in CLAUDE.md).** It follows the
`custommap` pattern:
- `GauntletDraft` holds the stages (id, at, title, briefing, hint, settings,
  target), the lanes with their attributes, start, goal, name and run.
- `without_stage()` is the only way a stage is deleted.
- `normalised()` is idempotent.
- `problems()` is the one validator. It returns `Problem`s that block (no stage,
  an unreachable stage, a stage with no setup or no int seed, text over the
  limits, more than `MAX_STAGES`) and ones that warn (a goal-less run, a gated
  lane to a stage with no other way in).
- `to_gauntlet()` goes through `Gauntlet.from_dict`, and the result must pass
  `checkBody` unchanged.

**Editor (shell).** It is mapmaker-shaped and reuses `geometry.WorldView` for
pan and zoom. It copies mapmaker's small pieces rather than refactoring mapmaker
(114 tests depend on its private functions): the rect registry with `_hit`,
`_pick_node`/`_pick_lane`, press/drag/release, the whole-draft undo/redo
snapshots, the confirm modal, status ageing, `_pinch` and sidebar scroll. If the
copy turns out large, extract only the pure geometry pickers into a shared helper
in a follow-up.

- **Tools:**
  - **Stages:** tap empty space to add a stage, drag to move, tap to select.
  - **Lanes:** drag from one stage to another to make a one-way lane; tap a lane
    to select it.
  - **Ends:** tap to toggle a start or a goal.
- **Sidebar for a selected stage:**
  - title, hint and briefing paragraphs (add or remove), through the mapmaker
    filename field and `softkeyboard`;
  - **Setup:**
    - *Use menu setup*: the editor stays alive in `main` while the author steps
      back to the menu and returns, like `editor` today.
    - *Paste link*: `Settings.from_token`, dropping the challenge.
    - Either way an int seed is pinned, rolled with `settings.roll_seed` when
      the setup has none.
  - **Target:** none; a number of turns (and ships lost) on steppers; or *from
    my best on this stage*, read from `gauntlet.best`, which is the in-game
    version of the board's `linkTarget`;
  - **Try stage:** plays it and returns to the maker afterwards.
- **Sidebar for a selected lane:** two-way, gated (turns/lost steppers), hidden,
  delete.
- **Save:**
  - Desktop writes `user_dir()/<id>.json`. The web build uses
    `gauntlet.remember`.
  - The id is `made-<12 hex of to_dict>`, like `gen-<hash>`.
  - **Copy link** gives `#gauntlet=`.
  - **Ask to list on the board** goes through `gauntlet.ask_listing`. It is a
    separate button press, which is the consent, as the generate form's tickbox
    is.
  - Edit (from the graph view) opens a copy. Saving gives a new id, because
    stored rows never change.
- **Entry and exit.** `main` gets a scene `"gmaker"` beside `"maker"`
  (main.py:2512-2527, frame pump around 2798, `reflow` on resize). The graph
  modal gets **Make new** and **Edit copy** buttons, and the editor returns
  `"menu"` / `"try"`.
- **Layout.** The top strip, sidebar and footer are measured with `config.s()`.
  Portrait moves the sidebar to the bottom, and compact scrolls the sidebar.
  Nothing has a fixed pixel size.

Tests: `tests/test_gauntletdraft.py` (pure) and `tests/test_gauntletmaker.py`,
headless in the same way as `test_mapmaker.py`:
- placing, dragging, a lane, gating a lane, undo;
- the save round trip through `from_dict` and the shared `checkBody` fixture;
- the Play gate on blockers;
- every rect on screen at each tool and scale.

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
  - It offers `config.BOON_OFFER` (3) choices drawn from the gauntlet's `run.boons`.
  - The player picks one or skips.
  - Add it to CLAUDE.md's list of sanctioned derived streams.
- **Losses and endings:**
  - A loss costs a life. With lives left, the same stage is replayed fresh.
  - Winning a goal stage wins the run.
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
  - add *The graph view* and *The gauntlet maker*.
- **New `docs/design/runs.md`:**
  - runs, and the record and why it is stored;
  - boons, and why they are unpostable rather than a setup edit;
  - lives, and no Retry in a run.
- **`decided-against.md`.** Amend the Carry-over line: "boons as a setup edit".
  The unpostable boons replace that idea, and `runs.md` carries the reason.
- **`open.md`.** Remove the two gauntlet entries that are now built.
- **`sections.md`** and **`docs/README.md`**: add the new files and sections.
- **`rules.md`** *Gauntlets*, and **CLAUDE.md**:
  - the Gauntlets paragraph: lane targets, the maker, runs, and "a boon game is
    unpostable; boons live in `GameLog.boons`, never `Settings`";
  - the core module list: `boons`, `run`, `gauntletdraft`;
  - the shell list: `gauntletmaker`;
  - the derived-stream list: run offers.

## Order and PR shape

There are five PRs, each usable on its own:
1. the format;
2. the graph view, which is useful at once for existing gauntlets;
3. the maker;
4. boons in the log;
5. runs.

Phases 1 and 2 unblock the board's hidden-lane idea as well. Phase 4 has no UI
entry point until phase 5, but it is tested headless.

## Verification

- **Per phase:** the tests named above, `uvx ruff check .`, and `uvx pyright`
  on touched files. Run `node --test leaderboard/tests/*.test.mjs` in phase 1.
  Run the full `uv run pytest` after phases 4 and 5 (they touch `replay`,
  `main` and `viewstate`).
- **End to end** (`uv run python main.py`):
  1. In the gauntlet maker, build a seven-stage graph:
     - a main path 1→2→3→4;
     - a shortcut 2→4 gated "win in 12";
     - a hidden bonus 3→B gated "win in 10";
     - set setups from the menu;
     - Try a stage, and set its target from that best.
  2. Save, reopen it from the Gauntlets graph, and check the layout, the dashed
     gated lanes and that B is absent.
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

- **One life by default, and no Retry or rewind in a run game.** Without that a
  run cannot be lost. A file can raise `run.lives`.
- **Boons are permanent for the run and stack**: ships add up, production steps
  down to a floor of 1, and sight adds hops up to `FOG_MAX_HOPS`.
- **The Use boons switch is all held boons or none.** It is not a per-boon
  choice.
- **`BOON_SHIPS = 5` and `BOON_OFFER = 3`** are placeholders. Tune them against
  the real home start ships.
- **A run's maps are the gauntlet's pinned seeds**, so its variety comes from
  path and boon choices. A dealt run graph (`gauntlet.md`, *The gauntlet:
  proposed, not built*: `tools/campaign.py` `_layout`, `cut_nodes`,
  `_from_family`, `_mystery`) is a later step. In the maker it could become
  "deal this stage from a spec" through `dealer.deal`.
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

### Map maker (the model for Phase 3)
- **Model and tools:**
  - `Editor` is at `mapmaker.py:73-177`, and its tools are at `:48-50`.
  - `draw` is at `:392`; it clears `ed.rects` every frame.
  - `handle_event` is at `:1238` and returns `"play"`/`"menu"`/`None`.
- **Pressing and dragging:**
  - `_press_lane` (`:1347`) draws lanes, by tap-tap or drag with a rubber band.
  - `_arm_move` (`:1406`) arms a node drag; the drag pushes its undo entry only
    once it moves (`:1562`), and an illegal drop snaps back (`:1599`).
  - `_place` (`:1725`) adds a node.
  - `_hit` (`:1380`) hit-tests chrome rects, and `_handle_chrome`/`_handle_action`
    (`:1418`/`:1459`) dispatch on string keys.
- **Undo:** `_push_undo` (`:1687`), `_undo` (`:1693`), `_redo` (`:1701`) and
  `_adopt` (`:1709`). Each entry is a whole-recipe snapshot, capped at
  `config.EDIT_UNDO_DEPTH`.
- **Pickers:** `_pick_node` is at `:2146` (with a minimum tap size) and
  `_pick_lane` at `:2175` (repeat presses cycle through overlapping lanes).
- **Gestures:** `_pinch` is at `:705` and `_side_scroll_event` at `:749`.
- **Confirm modal:** `_CONFIRMS` is at `:1211` and `_handle_confirm` at `:1635`.
- **Layout:**
  - the layout code is at `:303-386`; the footer wraps (`:317`);
  - portrait moves the sidebar to the bottom (`:358`);
  - compact scrolls the sidebar (`:665-703`);
  - the zoom cluster is at `:469`.
- **Saving and playing:** save and open are at `:2290-2314`, `commit` at `:234`,
  and `_play` at `:1523`.
- **Scene wiring in `main.py`:**
  - the editor variable is at `:1872`;
  - the `create_map` action leads to `open_editor` and the `"maker"` scene
    (`:2512-2514`);
  - events go through `:2519-2527`;
  - each frame calls pump, age_status and draw (`:2798-2802`);
  - `reflow` runs on resize (`:2337`).
- **Reusable as is:** `geometry.WorldView` (`to_screen`, `to_world`, `zoom_at`,
  `pan`, `fit_to`), `geometry.point_segment_dist` and `segments_intersect`, and
  `render._draw_arrowhead` (`render.py:355`).
- **Specific to maps, not reused:** `CUSTOM_WORLD_W`/`WORLD_SIZE`, the seats and
  palette (`:547-662`), and the spacing and planarity rules (`:2098-2136`).
- **Tests:** `tests/test_mapmaker.py` has 114 tests. Its `scene` fixture (`:28`)
  runs headless on the dummy SDL driver at `apply_ui_scale(1.0)`. The helpers
  draw first so the rects exist, then post synthetic mouse events at a rect's
  centre. The layout test (`:665`) checks every rect is on screen at scales 1,
  1.5 and 2.

### `custommap.py` (the pattern for `GauntletDraft`)
- `Problem(severity, code, text, nodes, lanes)` with `.blocks` is at `:54-72`.
- `without_node` (`:159`) is the only way to delete.
- `normalised` (`:176`) is idempotent, and `to_dict` always writes the
  normalised form.
- `problems` (`:195`) is built from small rule functions at `:344-467`.
- `from_dict` (`:238`) never raises.
- `from_state` is at `:470`.
- For one-way lanes, keep `(a, b)` ordered: don't canonicalise to `a<b`.

### The board's builder
- `leaderboard/js/gauntlet-build.mjs`:
  - `LIMITS` is at `:23`, `buildBody` at `:152` (always linear), and `checkBody`
    at `:170-233`, shared with `netlify/functions/gauntlet.mjs`.
  - Only `name`, `stages`, `lanes` and `start` are kept; other top-level keys
    are dropped silently. A lane's third element may only say `two_way`.
  - `unreachable` is at `:242`.
- `gauntlet.html` loads a branching gauntlet as a linear one, which is why
  Phase 1 has the remix guard.
