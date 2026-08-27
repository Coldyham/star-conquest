# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, pygame for presentation, `uv` for dependency management.

Design rationale, history, and edge-case detail behind the rules below live in
[`docs/design-notes.md`](docs/design-notes.md), keyed by matching headings —
read it when you're actually touching that code, not as background reading.

## Commands

```sh
uv run python main.py                          # opens the setup menu
uv run python main.py --players 4 --mode symmetric   # CLI args pre-fill the menu
uv run python main.py --seed 42 --nodes 24     # pre-fill a reproducible map
uv run python main.py --no-menu --autoplay     # skip the menu; AI plays every seat (demo)

uv run pytest                         # full suite
uv run pytest tests/test_engine.py    # one file
uv run pytest tests/test_engine.py::test_production_cadence   # one test

uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --trials 200          # batch stats (winners, length, timeouts)
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
uv run python -m tests.sim --swap --trials 50    # ...or as one free-for-all
```

There is no lint step in `pyproject.toml`/CI; match the surrounding style. VSCode
runs Pylance in basic type-checking mode (`.vscode/settings.json`) — treat its
type warnings as real signal, not noise.

## Architecture

The whole point of the layout is a hard split between a **pure simulation core**
and a **thin pygame presentation shell**, so the entire game is testable
headlessly. Respect these boundaries — they are load-bearing, not stylistic:

- **Core — imports no pygame:** `model`, `geometry`, `mapgen`, `combat`,
  `engine`, `ai`, `settings`, `fog`, `replay`. This is what lets `tests/sim.py`
  and most of the suite run with no display. Do not add a pygame import to any of
  these. (`fog` is presentation-only visibility — pure hop-distance queries the
  shell reads each turn; the engine and AI never consult it. `replay` serializes a
  match to JSON and replays it back through the headless engine — see Persistence
  & replay below.)
- **Shell — the only pygame modules:** `render`, `input`, `menu`, and `main`.
  - `render.py` reads `GameState` + `Ui` and draws; it **never mutates them and
    never imports `engine` or `ai`**. Derived display stats (threat, inbound,
    per-player production rate) are computed with local helpers rather than
    reaching into `ai`. Its layout is measured rather than hardcoded — see the
    scaling convention under Key conventions. Its one animation (the selected
    forward rule's chevron conveyor) reads the wall clock in `_flow_phase` and
    stores nothing, so a frame stays a pure function of `GameState` + `Ui` + time.
  - `input.py` mutates **only** `Ui` (and queues human `Order`s); it never
    touches the simulation. It returns a high-level action string
    (`"end_turn"`, `"toggle_play"`, `"toggle_autoplay"`, `"toggle_history"`,
    `"rewind"`, `"restart"`, `"menu"`, `"quit"`) or `None`, and `main.py` decides
    what to do with it.
  - `menu.py` is a self-contained pre-game scene with the same draw/mutate split:
    `draw` only reads `Settings`, `handle_event` mutates `MenuState`/`Settings`
    and returns `"start"`/`"quit"`/`None`. `main.py` runs a two-scene
    (`"menu"` ⇄ `"game"`) state machine and builds the `GameState` on `"start"`.
    `menu.pump` is the second half of the mutate side: a per-frame poll `main.py`
    calls while the menu is up, for text that never reaches the SDL event queue
    (see `softkeyboard` below).
  - `softkeyboard.py` is a browser-only bridge, not a pygame module: on a touch
    browser it focuses a hidden DOM `<input>` so the mobile on-screen keyboard
    actually appears (SDL's `start_text_input` has nothing to focus there) and
    reports back what was typed. Everything is guarded — off the web build, on a
    desktop browser, or if any DOM call fails, every function is a no-op and the
    menu keeps its plain SDL text path.
  - `webstore.py` is the other browser bridge, same style: the address bar and a
    small key/value store (shared-settings tokens, personal bests). See the
    challenge-link notes under Key conventions.

### Turn resolution (engine.py)

Turns resolve **simultaneously**: `end_turn` collects every player's orders
against the *same* unchanged start-of-turn state, then applies them together, so
there is no turn-order advantage. The phase order inside `end_turn` is
deliberate and combat/production correctness depends on it: AI decisions →
advance fleets → arrivals+combat (fleets arriving at a node are grouped and
resolved together, launch-order independent) → production (after combat, so a
system captured this turn produces for its new owner) → win check → `turn += 1`.

**The engine never imports the AI.** The decision function is injected as the
`decide` parameter to `end_turn`; `main.py` and `tests/sim.py` pass `ai.decide`,
a per-seat dispatcher that routes each seat to its named strategy
(`ai.STRATEGIES`, keyed by `Player.ai_strategy`; `ai.decide` falls back to the
built-in `"heuristic"` for any unknown name, so a stale/missing strategy never
crashes). Keep this inversion — it is why the core has no AI dependency, and it is
the seam for user-written AIs (`ai.register(name, fn)`, `fn(state, pid) -> list[Order]`).

**Drop-in custom AIs.** `ai.load_models()` imports every `*.py` in the
`models/` dir (repo-anchored `ai.MODELS_DIR`, mirroring `menu._SAVE_DIR`) and
`register`s each file's `decide` under its stem; a file that fails to import or
lacks `decide` is skipped. `main.py` calls it at startup and on game start;
`ai.available_strategies()` feeds the menu's per-seat Strategy dropdown. `menu.py`
is the one shell module that imports `ai` (for discovery) — fine, since `ai` is
pure core (no pygame); the render/input prohibition on importing `ai` still holds.
`models/` is committed (not gitignored) precisely so `tools/build_web.sh` can
stage it alongside `starconquest/` and ship the same bots to the browser/PWA
build — new bots go in via commit/PR, not local drop-in only.

`apply_order` deducts ships from the source at launch, so a fleet is "off the
board" in transit (fleets on lanes never interact); order-issuing has no bearing
on outcomes.

### Persistence, replay & history (replay.py)

A match is **never snapshotted** — it is recorded as its *inputs*: `Settings`,
the concrete `seed`, and each turn's human orders (`replay.GameLog`, auto-saved
after every turn to a gitignored, repo-anchored `games/` dir, mirroring
`ai.MODELS_DIR` and `menu._SAVE_DIR`). Because all randomness flows through
`state.rng`, `replay.reconstruct(log, decide, on_turn=…)` replays those inputs
back through `engine.end_turn` to rebuild the **exact** state at any turn,
bit-identical (keeping the engine's AI inversion — `decide` is a parameter, not an
import). Under autoplay the human seat is AI-driven and draws `rng` *before*
opponents, so those turns are flagged (`"ai": true`) and `reconstruct` re-runs
`decide` for the human seat to reproduce the draw order. `main.resume_game` uses
this to offer resuming the last unfinished match from the menu.

**History mode** is a shell-only review scene (`Ui.history`, gated so it never
enters the pure core). On entry `main.build_history` runs one `reconstruct` whose
`on_turn` callback deep-copies each turn's board and folds fog (via
`_accumulate_fog`) into a per-turn snapshot, so the bottom-bar scrubber seeks by
plain list-indexing. Fog stays a `Ui`-layer concern: mid-game a past turn shows
fog *as it was then*, while a finished game is fully revealed. **Rewind** resumes
live play from the viewed turn — mid-game it truncates the same log file
(`GameLog.truncate`, confirmed first, since it discards later turns); on a
finished game it forks a new file (`GameLog.fork`) so the completed record stays
intact.

### Key conventions

- **All balance/aesthetic constants live in `config.py`.** Do not hardcode a
  magic number elsewhere — add a named constant there. Every module reads
  `config.X` *live* at call time (nothing is cached at import), so the Advanced
  menu tunes copies on a `Settings`, and `settings._apply_globals` (called by
  `build_state` just before generation) is the single writer that pushes them
  back into `config`.
- **Nothing that holds text gets a fixed pixel size.** A button's width comes
  from its measured label (`render._btn_w`, and `render._btn` draws + returns
  the hit-rect), a stacked text row's pitch from the font's own line height
  (`render._row_h`), a modal's stack is measured then centred
  (`render._draw_modal`), and help prose is reflowed to the panel it sits in
  (`render._wrap`). One-off layout literals still go through `config.s()`.
- **`config.touch_ui` is the input modality**, set beside the scale in
  `apply_ui_scale` from `main`'s single boot-time probe (Android, or a touch
  browser). On a touch build the shell drops every keyboard-only string — the
  `(Esc)`/`(R)` suffixes on button labels (`render._key_hint`,
  `render.confirm_labels`, `menu._resume_labels`), the shortcut lines in the info
  panel's help text and the win overlay, the menu's `Enter: start game` footer —
  and floors tappable controls at `config.TOUCH_MIN_TARGET` (`render._tap_size`).
- **`settings.Settings` is the pure, serializable pre-game config** (players,
  map, seed, global knobs, per-seat AI); `menu.MenuState` holds transient menu
  interaction state (analogous to `Ui`). `settings.build_state(settings, seed)`
  is the one funnel from menu/CLI to a `GameState`. `to_dict`/`from_dict` back
  both the JSON file Save/Load (menu footer, gitignored `saves/`) and a
  `to_token`/`from_token` pair that encodes a whole config into a URL fragment
  (mirrored to `localStorage` under `paths.WEB_SHARED_SETTINGS_KEY` so an
  installed PWA, which launches from a fixed `start_url`, still sees it).
  `from_token`/`from_dict` are deliberately tolerant (clamp, default, pad), so a
  stale or hand-edited token still loads to a playable config. Never prune a
  non-heuristic seat's `ai_params` when writing a token: it is a documented
  readable field for drop-in bots (`models/README.md`).
- **Challenge links carry a score to beat.** `settings.Challenge`
  (`turns`, `lost`, `hand`, `by`, `key`) is an optional field on `Settings`, so it
  rides all of the above with no new plumbing; `build_state` ignores it. Score is
  turns-to-win, ties broken on fewest ships lost (`Player.ships_lost`, written in
  `combat.resolve_arrival`). A challenge token travels by clipboard only
  (`webstore.copy_link`) — never the address bar or `localStorage`, unlike a
  settings link (`webstore.share_token`). Editing a challenge's setup asks first
  (`menu._draw_unchallenge`); `Settings.without_challenge()` is what persists a
  "change it anyway".
- **`webstore` is the third browser bridge** (with `softkeyboard` and the
  web-only paths in `main`/`menu`): `get`/`set` are `localStorage` on the web and
  a JSON file under `data_dir()` elsewhere. The rest is genuinely web-only and
  no-ops off it: `link_url`, `set_url_fragment`, `copy_to_clipboard` and
  `url_token` are the primitives, and `sync_settings` / `share_token` /
  `copy_link` the compositions callers use. Same defensive style as
  `softkeyboard`: local `import platform`, every DOM call guarded, storage
  failure never load-bearing.
- **Quitting is a desktop concept; the web has nothing to exit to.** Every
  confirmed quit goes through `main.leave_app()`: off the web it returns True
  and the loop ends, while on the web it asks the browser to close the window
  (`webstore.close_window`) and returns False, falling back to the setup menu
  with `main.CANT_CLOSE_MSG` via `menu.set_status`. Never end the loop
  (`pygame.quit()`) directly on the web build.
- **The map viewport has two margins, both floored at `config.node_clearance()`.**
  `config.map_fit_padding()` sizes the zoom-1 fit and `config.map_pan_padding()`
  is what the pan clamp keeps past the outermost system once zoomed in —
  `geometry.WorldView` takes them as `padding` and `pan_padding`.
- **AI is per-seat and pluggable.** Each `Player` carries `ai_strategy` (a key
  into `ai.STRATEGIES`) and `ai_params` (`model.AiParams`, defaults mirroring
  the `config.AI_*` constants). `ai.compute_orders` reads the seat's params, so
  seats can play to different profiles; the menu's AI tab edits them per seat.
  `Settings` mirrors both per-seat lists (`ai: list[AiParams]`, `ai_strategy:
  list[str]`, indexed by seat-1), and `build_state` stamps each non-neutral
  `Player` with its `seat_strategy(...)` and a copy of its `seat_params(...)`.
  - Those fields are readable for *every* seat — see `models/knower.py` for
    what that makes possible. Any such bot must keep three rules: never call
    `ai.load_models()` from inside a model (it re-`exec_module`s every file,
    including yours, unguarded); read `ai.STRATEGIES` *lazily* inside `decide`,
    since files load in sorted order and the registry is incomplete at your
    import time; and draw **nothing** from `state.rng` (`tests/test_knower.py`
    asserts both of the latter).
  - **`AiParams.aux` is the one bot-defined knob.** The core never interprets it
    (only the AI tab's generic "Custom (bot-defined)" slider writes it); each
    strategy assigns its own meaning. `config.AI_AUX` is `1.0` and that is the
    documented "untuned" value, so a bot's default behaviour must be what it does
    at 1.0 — a stale token or save with no `aux` key deserialises to it.
    `models/knower.py` reads it as search depth. Add per-bot knobs here rather
    than growing `AiParams` one field per strategy.
  - **A predicting bot advertises itself** with `IS_ORACLE = True` and, when
    prediction is per-seat rather than per-module, `is_oracle_seat(player)` —
    which callers prefer over the flag (`knower.is_oracle_seat` is "depth ≥ 1", so
    its depth-0 seats are predicted for real, and trusted, instead of approximated).
  - **A seat commands its own ships and nothing else.** `apply_order` only
    checks the *declared* owner holds the source, so `_collect_orders` filters
    every seat's orders (including the human's, under autoplay) through
    `engine._own_orders`. Without it any drop-in bot could launch a rival's
    fleet, or the human's.
- **All randomness flows through `state.rng`** (a seeded `random.Random`). A
  seed fully reproduces a map *and* every battle. Never call the global `random`
  module in core code, and keep new map-gen / combat code deterministic given
  the seed (`test_mapgen.py` asserts this). The *unreproducible* rolls go
  through `settings.random_seed()` / `settings.fresh_rng()`, which mix the
  clock and a per-call counter into a throwaway RNG rather than using the
  global `random` (the web build boots from a fixed interpreter image, so
  `random`'s auto-seeding can hand out the same "random" seeds on every load).
- **Travel time is a query, not a stored value.** `Lane.travel_turns` (and
  `adjacency`) hold the mapgen-time figure; with `config.SHIP_SPEED_GROWTH_PCT`
  on, ships compound faster each turn, so always ask `state.travel_turns(a, b)` — it
  re-times through `config.travel_turns_at` for the *current* turn. Growth bites
  at launch only: a fleet in transit keeps its `turns_total`.
- **Everything is keyed by integer id.** Systems are `dict[int, System]`; lanes
  use a canonical order-independent `frozenset` key (`model.lane_key`). Neutral
  is a real player with `id == 0`.
- **The send popup is the *only* ship-count editor.** Composing a new send opens
  it (`Ui.begin_send`), and so does reopening an already-queued order or
  standing rule — `Ui.edit_order` / `Ui.edit_forward` put the popup back into
  `CHOOSING` aimed at that subject. `Ui.editing_existing` records that the
  subject *predates* the popup, and drives the bottom button (Cancel vs
  "Delete order"/"Delete rule") and `_close_send`'s unwind to `IDLE` on edit.
  - **A dormant rule highlights but never opens it** (`Ui.rule_is_live`): the
    popup reads the source's garrison and destination unguarded, and aiming it
    at a system we no longer hold would let the Send tab queue an order out of
    enemy territory. Dormancy only lasts the turn — `Ui.prune_forward`
    (`main.resolve_turn`) deletes a rule whose source was taken, so it can never
    come silently back to life on recapture.
  - **The count slider must be claimed before the popup's drag fallthrough** —
    `slider_rect` is hit-tested first, and `dragging_slider` checked ahead of
    `dragging_popup` in the MOUSEMOTION chain. Both halves tolerate `lo == hi`
    (an empty source, the touch default) and a zeroed rect (popup closed
    mid-drag).
- **The side panel's queued list is capped and scrolled, not truncated.** It
  takes at most half the panel, and what doesn't fit is reached with
  `ui.order_scroll` (▲/▼ buttons, or the wheel while over the panel). Each
  drawn row carries **its own index** into `pending` (`ui.order_hitboxes` is
  `(index, row, delete)`) — a positional mapping would silently delete the
  wrong order once only a window of the list is on screen.
- **Losing makes the human a spectator, not a blind one.** `fog.observe`
  returns empty for a landless player, so `main._accumulate_fog` reveals the
  whole board (dropping frozen `player_intel`) once
  `GameState.is_defeated(human_id)` — fixing history mode and a resumed game
  for free since both fold fog through that one helper. Its companion is
  **fast forward** (`Ui.can_fast_forward`, `main.step_delay`, the F key /
  footer button): `main.FAST_FORWARD_MS` replaces the autoplay/play delay so
  the rest of a lost match resolves a turn per frame. Offered only while
  spectating.
- **`viewstate.Ui` holds all transient interaction state**, including human-only
  quality-of-life features (e.g. `auto_forward` standing rules) that must stay
  out of the pure `GameState`. `main.resolve_turn` expands such UI state into
  `Order`s at end-of-turn.
- **`tests/sim.py` is both a demo harness and a test fixture.** Because it drives
  the pure core headlessly, the suite uses it to assert games actually terminate
  and never corrupt state (`check_invariants`). After changing `ai.py` or
  travel/combat balance, run a `--trials` batch and watch the timeout rate.
  It also hosts the two bot tournaments, sharing `_tally`/`_avg_turns`: `--swap`
  is a free-for-all (whole roster in one game, rotated through every seat via
  the cyclic `_rotations`), `--ladder` is a pairwise round-robin (`run_ladder`:
  every pair, both seatings, plus a head-to-head grid). Both default their
  roster to `ai.available_strategies()`, so a whole-`models/` ranking needs no
  arguments.

Map generation (`mapgen.py`) has two modes: `random` (jittered-grid placement +
light relaxation + a Euclidean MST for connectivity, which is planar so edges
don't cross, plus a few crossing-rejected extra edges for loops) and `symmetric`
(one base sector rotated N times about a shared contested centre for a perfectly
fair start). Both must stay connected and planar-ish — `test_mapgen.py` guards
both.
