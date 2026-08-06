# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, pygame for presentation, `uv` for dependency management.

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

There is no linter configured; match the surrounding style.

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
    scaling convention under Key conventions.
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
  magic number elsewhere — add a named constant there. Tuning the game means
  editing that one file. Every module reads `config.X` *live* at call time
  (nothing is cached at import), so the Advanced menu tunes copies on a
  `Settings`, and `settings._apply_globals` (called by `build_state` just before
  generation) is the single writer that pushes them back into `config`.
- **Nothing that holds text gets a fixed pixel size.** `config.apply_ui_scale`
  grows the font by ~2x on a phone, so a width or row pitch tuned at the baseline
  size overflows there — which is how labels used to spill out of footer buttons
  and how the info panel's rows used to land on top of each other. Instead: a
  button's width comes from its measured label (`render._btn_w`, and `render._btn`
  draws + returns the hit-rect), a stacked text row's pitch from the font's own
  line height (`render._row_h`), a modal's stack is measured then centred
  (`render._draw_modal`), and help prose is reflowed to the panel it sits in
  (`render._wrap`). One-off layout literals still go through `config.s()`.
- **`config.touch_ui` is the input modality**, set beside the scale in
  `apply_ui_scale` from `main`'s single boot-time probe (Android, or a touch
  browser). On a touch build the shell drops every keyboard-only string — the
  `(Esc)`/`(R)` suffixes on button labels (`render._key_hint`,
  `render.confirm_labels`, `menu._resume_labels`), the shortcut lines in the info
  panel's help text and the win overlay, the menu's `Enter: start game` footer —
  and floors tappable controls at `config.TOUCH_MIN_TARGET` (`render._tap_size`),
  which is what keeps the send popup's −/+ and preset rows finger-sized. Those
  hints are both useless without a keyboard *and* the thing that made the labels
  too wide, so dropping them fixes the layout and the copy together.
- **`settings.Settings` is the pure, serializable pre-game config** (players,
  map, seed, global knobs, per-seat AI); `menu.MenuState` holds transient menu
  interaction state (analogous to `Ui`). `settings.build_state(settings, seed)`
  is the one funnel from menu/CLI to a `GameState`. `to_dict`/`from_dict` back
  both the JSON file Save/Load (menu footer, gitignored `saves/`) and a
  `to_token`/`from_token` pair that encodes a whole config into a URL fragment.
  On the web build the menu's "Get Link" button writes that token to
  `location.hash` (and best-effort to the clipboard) so a setup can be shared as
  a link, and `main._apply_shared_link` decodes a `#<token>` back onto `Settings`
  at boot — same effect as CLI args pre-filling the menu. Because an installed
  PWA launches from the manifest's fixed `start_url` (no fragment), the token is
  also mirrored to `localStorage` (key `paths.WEB_SHARED_SETTINGS_KEY`) and read
  back as a fallback, so a shared config survives installation.
  `from_token`/`from_dict` are deliberately tolerant (clamp, default, pad), so a
  stale or hand-edited token still loads to a playable config.
  - A token is **pruned then deflated**: `token_dict` drops every field the
    reader would infer anyway (defaults, unused seats — `from_dict`'s tolerance
    is what makes omission safe), which takes a default config from ~1470 chars
    to under 100. `mode`/`players`/`nodes`/`seed` are always emitted even at
    default, because pruning otherwise makes a token depend on the *reader's*
    defaults and those four are the identity of the match. `from_token` sniffs
    `raw[:1] != b"{"` to keep pre-compression links working. Never prune a
    non-heuristic seat's `ai_params`: it is a documented readable field for
    drop-in bots (`models/README.md`), so default-equality is the only safe test.
- **Challenge links carry a score to beat.** `settings.Challenge`
  (`turns`, `lost`, `hand`, `by`, `key`) is an optional field on `Settings`, so it
  rides all of the above with no new plumbing; `build_state` ignores it. Score is
  turns-to-win, ties broken on fewest ships lost (`Player.ships_lost`, written in
  `combat.resolve_arrival` — the one place ships die). `hand` is how many turns
  the human actually decided (`main.hand_turns` reads the log's per-turn `"ai"`
  flag): autoplaying a *decided* game to skip the cleanup is normal play, so it is
  disclosed on the link rather than voiding the score — only a match with zero
  hand-played turns is unshareable. `Challenge.key` is a `challenge_key()`
  checksum of the setup, redundant by construction so the menu banner can spot
  that the config has since been edited and warn (rather than locking widgets).
  `challenge_key` hashes the *full* dict minus `challenge`/`autoplay`, so two
  people agree regardless of what their tokens pruned.
  - **A challenge token is never stored or left in the URL.** It travels by
    clipboard (`webstore.copy_link`), unlike a settings link
    (`webstore.share_token`, which syncs the address bar *and* `localStorage`).
    Both halves of that matter: an installed PWA has no address bar to read a link
    out of, and a *remembered* challenge gets read back at every later launch, so
    its banner haunts sessions long after the link was opened. Hence
    `Settings.without_challenge()` — what `main._apply_shared_link` persists, and
    what the menu re-syncs when a challenge is dropped.
  - **Editing a challenge's setup asks first** (`menu._draw_unchallenge`, raised
    from `handle_event` once `Challenge.matches` goes false and any slider drag has
    been released). "Change it anyway" clears `Settings.challenge` and rewrites the
    address bar so a reload can't resurrect it; "Keep the challenge" restores
    `MenuState.challenge_snapshot`, the last config the score still applied to.
    Locking the widgets instead is a dead end the moment someone wants the same map
    with one knob moved. `main.new_ui` independently refuses to carry a
    `challenge_target` onto the `Ui` unless the challenge still matches, so a
    finished game can never report "short of" a target from another setup.
- **`webstore` is the third browser bridge** (with `softkeyboard` and the
  web-only paths in `main`/`menu`): `get`/`set` are `localStorage` on the web and
  a JSON file under `data_dir()` elsewhere — so personal bests work on desktop
  too. The rest is genuinely web-only and no-ops off it: `link_url`,
  `set_url_fragment`, `copy_to_clipboard` and `url_token` are the primitives, and
  `sync_settings` / `share_token` / `copy_link` the compositions callers use (see
  the challenge notes above for why the last two must stay distinct). Same
  defensive style as `softkeyboard`: local `import platform`, every DOM call
  guarded, storage failure never load-bearing.
- **Quitting is a desktop concept; the web has nothing to exit to.** Ending the
  main loop runs `pygame.quit()`, which on the web destroys the canvas and strands
  the player on a blank page only a force-close escapes. So every confirmed quit
  goes through `main.leave_app()`: off the web it returns True and the loop ends,
  while on the web it asks the browser to close the window
  (`webstore.close_window` — often possible in an installed PWA, usually not in a
  tab) and returns False, and the caller falls back to the setup menu with
  `main.CANT_CLOSE_MSG` via `menu.set_status`. Never end the loop on the web
  build. (`menu.set_status` is public for exactly this: it is the menu's one line
  for telling the player what just happened.)
- **The map viewport has two margins, both floored at `config.node_clearance()`.**
  `config.map_fit_padding()` sizes the zoom-1 fit (raising it just shrinks the
  whole map) and `config.map_pan_padding()` is what the pan clamp keeps past the
  outermost system once zoomed in — `geometry.WorldView` takes them as `padding`
  and `pan_padding`. The floor exists because a node's circle is drawn at a pixel
  radius that is *not* part of the world bounds, so the fit/pan maths cannot see
  it; a margin below the largest node's drawn extent slices that circle. Related:
  `_clamp` centres an axis only while its content still fits the *fit-padded*
  span, since that is what `_center_axis` divides up — comparing against the bare
  viewport let the "centred" offset push a boundary system back out through the
  margin at certain zooms (`test_boundary_never_crosses_the_margin_at_any_zoom`).
- **AI is per-seat and pluggable.** Each `Player` carries `ai_strategy` (a key
  into `ai.STRATEGIES`) and `ai_params` (`model.AiParams`, defaults mirroring
  the `config.AI_*` constants). `ai.compute_orders` reads the seat's params, so
  seats can play to different profiles; the menu's AI tab edits them per seat.
  `Settings` mirrors both per-seat lists (`ai: list[AiParams]`, `ai_strategy:
  list[str]`, indexed by seat-1), and `build_state` stamps each non-neutral
  `Player` with its `seat_strategy(...)` and a copy of its `seat_params(...)`.
  - **Those fields are readable for *every* seat, and `models/knower.py` is what
    that makes possible.** Because turns resolve simultaneously — `_collect_orders`
    hands every seat the same unmutated state and applies nothing until all have
    decided — an opponent's orders cannot depend on yours, so a bot can clone the
    board, call each rival's own registered `decide`, and know their moves before
    the engine asks for them. There is no fixed point to solve; one forward pass of
    their real code *is* the answer. knower folds those predictions into a
    "post-launch board" (a clone with the predicted orders applied via
    `engine.apply_order` but not advanced) and runs thinker's phases against it.
    Three rules any such bot must keep: never `ai.load_models()` from inside a
    model (it re-`exec_module`s every file, including yours, unguarded); read
    `ai.STRATEGIES` *lazily* inside `decide`, since files load in sorted order and
    the registry is incomplete at your import time; and draw **nothing** from
    `state.rng`, because leaving the stream where the later seats expect it is
    exactly what makes their prediction bit-exact (`tests/test_knower.py` asserts
    both).
  - **A seat commands its own ships and nothing else.** `apply_order` cannot enforce
    that — it only checks the *declared* owner holds the source, so an `Order` naming
    another player is valid on its own terms. So `_collect_orders` filters every
    seat's orders through `engine._own_orders` (and the human's, which under autoplay
    also come from `ai.decide`). Without it any drop-in bot could launch a rival's
    fleet, or the human's. knower mirrors the same rule when building its board, both
    as defence in depth and for fidelity — predicting a fleet the engine will refuse
    is just a wrong prediction.
- **All randomness flows through `state.rng`** (a seeded `random.Random`). A
  seed fully reproduces a map *and* every battle. Never call the global `random`
  module in core code, and keep new map-gen / combat code deterministic given
  the seed (`test_mapgen.py` asserts this). The *unreproducible* rolls — picking
  a fresh seed, the menu's dice buttons — go through `settings.random_seed()` /
  `settings.fresh_rng()`, which mix the clock and a per-call counter into a
  throwaway RNG rather than using the global `random`: the web build boots from a
  fixed interpreter image, so `random`'s auto-seeding can hand out the same
  "random" seeds on every page load.
- **Everything is keyed by integer id.** Systems are `dict[int, System]`; lanes
  use a canonical order-independent `frozenset` key (`model.lane_key`). Neutral
  is a real player with `id == 0`.
- **The side panel's queued list is capped and scrolled, not truncated.** It may
  take at most half the panel so the system/lane/rule details above it are never
  pushed off, and what doesn't fit is reached with `ui.order_scroll` (the ▲/▼
  buttons, or the wheel while the pointer is over the panel — where the wheel no
  longer zooms the map). `render` records how far it may scroll in
  `ui.order_scroll_max` and tolerates a stale offset by clamping locally when it
  draws; `Ui.scroll_orders` re-clamps before applying a delta. Each drawn row
  carries **its own index** into `pending` (`ui.order_hitboxes` is
  `(index, row, delete)`): a positional mapping silently deletes the wrong order
  once only a window of the list is on screen.
- **Losing makes the human a spectator, not a blind one.** Fog is measured from
  the systems you hold, so a knocked-out player has nothing to observe *from*:
  `fog.observe` returns empty and every remembered system falls back to a grey "?"
  — the whole map, when fog was off. So `main._accumulate_fog` reveals the board
  (and drops the frozen `player_intel`) once `GameState.is_defeated(human_id)`,
  which fixes history mode and a resumed game for free since both fold fog through
  that one helper. Keyed on *defeat*, which is final: revealing for a landless
  player who still has a fleet flying would leak the map into `Ui.seen` for good if
  they retook a system. Its companion is **fast forward** (`Ui.can_fast_forward`,
  `main.step_delay`, the F key / footer button): `main.FAST_FORWARD_MS` replaces the
  autoplay/play delay so the rest of a lost match resolves a turn per frame. Offered
  only while spectating — and it starts playback itself if nothing is stepping turns
  yet, or the button would look broken on a paused board.
- **`viewstate.Ui` holds all transient interaction state**, including human-only
  quality-of-life features (e.g. `auto_forward` standing rules) that must stay
  out of the pure `GameState`. `main.resolve_turn` expands such UI state into
  `Order`s at end-of-turn.
- **`tests/sim.py` is both a demo harness and a test fixture.** Because it drives
  the pure core headlessly, the suite uses it to assert games actually terminate
  and never corrupt state (`check_invariants`). After changing `ai.py` or
  travel/combat balance, run a `--trials` batch and watch the timeout rate.
  It also hosts the two bot tournaments, which answer different questions and
  share `_tally`/`_avg_turns`: `--swap` is a free-for-all (whole roster in one
  game, rotated through every seat via the cyclic `_rotations`), `--ladder` is a
  pairwise round-robin (`run_ladder`: every pair, both seatings, plus a
  head-to-head grid). Both default their roster to `ai.available_strategies()`,
  so a whole-`models/` ranking needs no arguments.

Map generation (`mapgen.py`) has two modes: `random` (jittered-grid placement +
light relaxation + a Euclidean MST for connectivity, which is planar so edges
don't cross, plus a few crossing-rejected extra edges for loops) and `symmetric`
(one base sector rotated N times about a shared contested centre for a perfectly
fair start). Both must stay connected and planar-ish — `test_mapgen.py` guards
both.
