# Tutorial ladder, built as a data-driven gauntlet

Status: **implemented.** The reasoning now lives in
`docs/design/gauntlet.md`, which is the file to read; this plan is kept as the
brief it was built from. Two things changed on the way: Next stage is the one
neighbour further from the start rather than the one open, uncleared neighbour
(so replaying a cleared stage still offers a way on), and the tutorial has ten
stages. It was written on 2026-10-21 against `main` at `0f85960`.

## Context

New players face the full setup menu and a bot with nothing to ease them in.
The idea is a tutorial: a fixed string of setups from easiest to hardest,
played in order. Each one brings in one new idea, explained by a short briefing
card when it opens and a one-line reminder in the side panel. Event-triggered
popups were judged too obnoxious, so there are none.

The same mechanism has to carry a later **gauntlet**: a single-player run with
FTL / Slay-the-Spire graph navigation and roguelite carry-over. So the tutorial
is not special code. It is a **gauntlet file**, an ordered list of stages, each
holding a pinned setup plus a title, a briefing and a hint. A user can write
their own gauntlet with explainers before each setup in the same format. This
plan builds the linear ladder and the tutorial content. The branching and
carry-over gauntlet only goes into a design doc.

Decisions already made:
- Stages are pinned `Settings` with fixed seeds.
- Wins post to the leaderboard as normal games. A stage is an ordinary setup, so
  posting needs no change.
- Teaching is a per-stage briefing card and a side-panel hint, and nothing else.

What already exists (nothing tutorial-like does):
- `Settings.from_dict` is tolerant.
- `challenge_keys()` gives a stable setup id.
- `webstore.best()` / `record_best()` already file a by-hand win per setup. It
  is gated on `Ui.can_post` in `main.resolve_turn`
  ([main.py:1035](starconquest/main.py#L1035)).
- The challenge banner shows how to put session context in the menu subtitle
  slot and onto `Ui` as plain values ([menu.py:487](starconquest/menu.py#L487),
  `new_ui` at [main.py:115](starconquest/main.py#L115)).

## Design rules (these go in CLAUDE.md, in short)

- **A gauntlet is data, never `Settings`.** Stage identity never becomes a
  `Settings` field, so no challenge key moves and there is no
  `_LEGACY_KEY_DROPS` entry. A stage *is* its setup: it is identified by
  `settings.challenge_key()`.
- **Progress is derived, never stored.** A stage counts as cleared when
  `webstore.best(*stage_settings.challenge_keys())` is not None, which means a
  by-hand win on that exact setup, the same gate as posting. No new webstore
  key. Editing a stage's setup in a later release makes it a new, uncleared
  stage, which is the honest answer.
- **A stage is active only while the menu's settings still match it**, judged
  by challenge key, the way `Challenge.matches` works. Moving one knob means
  you are no longer playing the stage; the banner warns, it does not lock.
- **Built-in stages pin every `Settings` field**, so a later change to a
  `config` default cannot quietly change the tutorial. A test enforces this.

## Relation to the weekly campaign

**Nothing database-side is shared, deliberately. What is shared is the shape
and the bordering rule.**

What the campaign has server-side:
- a `campaigns` row per week holding the graph
  (`{nodes: [{id, kind, x, y, settings, …}], lanes}`), written by
  `tools/campaign.py`;
- who holds what, derived by `fold` in `leaderboard/js/campaign.mjs`, which
  replays everyone's verified scores;
- `attemptStatus`, which answers "may I move here": a home, a neighbour of a
  node you hold, or within the grace. The game only asks `/api/campaign`.

It needs a server because it is **contested**: other people take your nodes,
so holding is a fold over everyone's scores.

A gauntlet is **uncontested and single-player**. Nobody can take a cleared
stage back, so "may I attempt this" depends only on your own wins, and those
are already on the device (`webstore.best`). A server would add identity,
which has no answer on the board ("no identity on a row"), plus quota and a
failure mode, and buy nothing.

What to share, so the two stay recognisably the same idea:
- **The graph shape.** The gauntlet file uses the campaign's
  `nodes` + `lanes` form, plus a `start` list. The tutorial is a path graph
  (0–1, 1–2, …). A later branching gauntlet then needs no format change, and
  the campaign's `_layout` / `cut_nodes` can draw and check one.
- **The bordering rule, mirrored in spirit.** A node is open if it is a start
  or borders a cleared node; otherwise it is locked ("not next to anything
  you've cleared yet"). Cleared nodes stay replayable. It is a few lines of
  Python over local bests, not a port of `attemptStatus`. The campaign's rule
  stays in JS behind `/api/campaign` and is never reimplemented.
- **"Derived, never stored"**: holdings are a fold of scores there, and
  clearings are a read of bests here.

What would make a server worth it later is shared or verified gauntlet
progress (a gauntlet column on the board, crowns for a run). That would derive
from verified `scores` the way `campaign_games` does, matching setups by seed
and jsonb equality, and it is out of scope here.

## Changes

### 1. Core: `starconquest/gauntlet.py` (new, pure, no pygame)
- `Stage(id, title, briefing: tuple[str, ...], hint: str, settings: dict)` and
  `Gauntlet(id, name, stages, lanes, start)`, frozen dataclasses. `lanes` and
  `start` follow the campaign graph shape; see "Relation to the weekly
  campaign".
- `Gauntlet.from_dict(d) -> Gauntlet | None`, total and never raising, like
  `CustomMap.from_dict`:
  - drops a malformed stage;
  - clamps `hint` to a max length so it fits the side panel;
  - caps briefing lines.
- `stage_settings(stage) -> Settings` via `Settings.from_dict`, with
  `challenge=None` and `autoplay=False`.
- `cleared(g) -> set[int]` (from `webstore.best`) and
  `open_stages(g) -> set[int]`: `start` ∪ the neighbours of cleared stages ∪
  cleared. Everything else is locked. On the tutorial's path this is "the
  next one".
- `next_stage(g, i) -> int | None`: the single open, uncleared neighbour after
  a win, or None when there is none or more than one. For a branching
  gauntlet, more than one sends the player to the list to choose.
- `stage_of(g, settings) -> int | None` matches by challenge key.
- `builtin() -> list[Gauntlet]` loads `starconquest/assets/gauntlets/*.json`
  (package-relative like `uifont._ASSETS`, so the web build ships it).
- `user() -> list[Gauntlet]` loads `data_dir()/gauntlets/*.json` on desktop
  only. Web MEMFS does not persist, so the web build has no user gauntlets in
  v1.
- Register `gauntlet` in `test_no_core_module_imports_pygame`'s list and in
  CLAUDE.md's core module list.

File format, documented for authors. A stage's `settings` is exactly what the
menu's Save writes (`Settings.save`), so an author builds a setup in the menu,
saves it and pastes it in:
```json
{"id": "tutorial", "name": "Tutorial", "start": [0],
 "stages": [
   {"id": 0, "title": "First contact", "briefing": ["…", "…"], "hint": "…", "settings": {…}},
   …],
 "lanes": [[0, 1], [1, 2], …]}
```
If `lanes` is missing, the stages are joined in list order, so a hand-written
linear gauntlet can leave it out. `from_dict` drops a lane to a missing stage.

### 2. Tutorial content: `starconquest/assets/gauntlets/tutorial.json`
About 11 stages, one new idea each. Exact maps and seeds get tuned during
implementation against `tools/gauntlet_check.py`:
1. **First contact**: 2 seats, about 8 systems, no fog, **vs heuristic**. It is
   the most passive bot and won't punish an all-in send. Select, send, take
   neutrals.
2. **Supply lines**: forwarding rules and **route mode**. They come early
   because they help new players most. A wider map, still vs heuristic.
3. **Caught in flight**: longer lanes and travel time. **vs rusherplus**, which
   shows that ships in flight leave home open.
4. **Defender's edge**: the Combat tab preview and defender advantage, vs
   rusherplus.
5. **Into the dark**: the **default fog (sight 1, scout 3)**,
   `FOG_ON_SIGHT` / `FOG_ON_SCOUT`. The briefing suggests trying sight 1 /
   scout 2 for a more claustrophobic game, or sight 1–2 / scout 4 for one that
   is more open but still hidden.
6. **Three's a crowd**: 3 seats, pile-ups, and starting between two rivals:
   push one way and guard the other rather than fight on two fronts.
7. **Full house**: a 5–6 seat game on a larger map.
8. **thinker**, then 9. **marshal**, then 10. **actuary at Hunt**, its
   strongest stop, as the closing stages.

### 3. Menu ([menu.py](starconquest/menu.py))
- **Tutorial button** next to the file control row (y≈720). It opens a stage
  list modal built on `_draw_menu_modal` / `_draw_pbp_prompt`'s measured-rows
  pattern. Each row shows the stage number, title, ✓ plus best
  `turns / lost` if cleared, and greyed if locked. The default gauntlet is the
  tutorial; a gauntlet picker row appears only when user gauntlets exist.
- Picking an open stage:
  1. stashes the player's own setup once (`ms.stash_settings`), since
     `copy_from` overwrites every field;
  2. `copy_from(stage_settings)`;
  3. sets `ms.gauntlet = (id, index)` and returns `"start"` through the usual
     `_start` / `_unless_slow` path.
- **Subtitle banner** while a stage is active and matching: `TUTORIAL 3/10 —
  Distance · your best …`, drawn in the challenge slot. A mismatch warns
  "settings changed — this isn't the stage any more". A **Leave tutorial**
  control restores the stash and clears `ms.gauntlet`.

### 4. Game view: `viewstate.Ui`, `main`, `render`, `input`
- `Ui` gains plain values:
  - `stage_label: str | None`
  - `briefing: tuple[str, ...]`
  - `briefing_open: bool`
  - `stage_hint: str | None`
  - `has_next_stage: bool`
  - `next_stage_rect`

  The existing dead `show_help` field stays untouched.
- `main.new_ui` / `start_game` take an optional `Stage`. `main` resolves it from
  `menu_state.gauntlet` + `stage_of(...)` and sets the fields. Render never
  sees a `Gauntlet`. A retry or fork keeps the stage; a resumed save has none.
- **Briefing card**: render draws it over the board on turn 0 while
  `briefing_open`, using `widgets.draw_modal` and `config.s()` sizing, with
  nothing fixed in pixels. `input` closes it on any press (mutating only `Ui`).
  It can be reopened from the hint line.
- **Side-panel hint**: `_panel_legend` adds `stage_hint` as one extra line.
  Extend `test_help_text_fits_the_empty_panel_whole` to cover the longest
  allowed hint.
- **Win overlay** ([render.py:2403](starconquest/render.py#L2403)): when
  `has_next_stage` and the human won, **Next stage (N)** takes New map's place.
  A stage's seed is fixed, so New map would leave the stage. `input` maps it to
  `"next_stage"`. `main` advances `menu_state.gauntlet`, loads that stage's
  settings into `settings`, then calls `start_game`. Challenge/Post are
  unchanged.
- In a stage, `"restart"` (New map, N) is not offered. Retry (T) stays.

### 5. Difficulty check: `tools/gauntlet_check.py` (new)
For each stage, plays each roster bot in seat 1 via `tests.sim.play_settings`
and reports the share that loses, the board's difficulty measure. It warns
where the order is not non-decreasing. Per CLAUDE.md it:
- appends one JSON line per finished game to `results/gauntlet_check.jsonl`;
- prints progress as it runs;
- resumes from that file when rerun.

`--quick` skips the expensive bots.

### 6. Docs
- New `docs/design/gauntlet.md`. Its sections:
  - **The tutorial ladder**: the rules above, with reasons.
  - **The file format**: for authors.
  - **The gauntlet: proposed, not built**: a seeded run graph laid out with the
    campaign's `tools/campaign.py` `_layout` / `cut_nodes`; stages dealt like
    `_from_family` / `_mystery` with `randomise_knobs`; difficulty rising by
    depth through `LADDER_ORDER` stops.
  - **Carry-over**: the open problem. No per-seat economy fields exist, so a
    boon (extra home ships or production, a scouted node, an extra starting
    system) would be applied by turning the generated map into a `custom_map`
    via `custommap.from_state` and editing it. That keeps `Settings` fields
    unchanged and the result replayable, but a boon-modified stage is a
    different setup on the board.
- Update `docs/README.md`, `docs/sections.md`, `docs/design/rules.md` (a
  "Gauntlets" heading) and CLAUDE.md (one short block plus the design-file
  list).

## Tests (new `tests/test_gauntlet.py` plus edits)
- `from_dict` never raises on junk and drops bad stages.
- Every built-in stage:
  - pins every `Settings.to_dict()` field;
  - has a fixed seed;
  - passes `build_state`;
  - has a hint that fits.
- Progress derivation:
  - `record_best` on stage 2's key opens stage 3; stages past it are locked.
  - On a small branching fixture, clearing a fork node opens both branches and
    `next_stage` returns None.
  - Missing `lanes` defaults to a path.
- `stage_of` matches by key and goes None after one knob changes.
- `test_menu.py`: picking a stage stashes and restores; the banner shows.
- `test_render.py`: a briefing card and a win overlay with Next stage stay
  inside the screen; the hint-length test.
- `test_input.py`: any press closes the briefing; N gives `"next_stage"` in a
  stage.
- Core pygame-import test includes `gauntlet`.

## Verification
- `uv run pytest tests/test_gauntlet.py tests/test_menu.py tests/test_render.py tests/test_input.py tests/test_settings.py`,
  then the full `uv run pytest`, since `main` and `viewstate` change.
- `uv run python tools/gauntlet_check.py --quick` confirms the stage order rises
  in difficulty.
- `uv run python main.py`, then Tutorial → stage 1:
  1. the briefing shows and closes;
  2. the hint is in the side panel;
  3. win by hand, then Next stage opens stage 2 with its briefing;
  4. back in the menu, stage 1 shows ✓ and best, and stage 3 is locked;
  5. Leave tutorial restores your own setup.
- `tools/build_web.sh` and the web build test confirm
  `assets/gauntlets/tutorial.json` ships.
