# Plan: replace the pygame shell with one HTML frontend over the Python core

## Context

The menu and game UI are drawn by pygame: `render` 4176 lines, `menu` 3526, `main` 3067, `mapmaker` 2331, `input` 1141, `widgets`, `uifont` and `softkeyboard`. That is about 16k lines, plus about 9k lines of shell tests.

The browser build is that same pygame code running in WASM (pygbag). So dropping only the desktop GUI would remove little. There are only about 40 `is_web()` branches, and most of the shell's complexity is the touch and phone layout, which is a web concern. The gain comes from replacing the shell on both targets with HTML: SVG for the map, DOM/CSS for panels and menus. Hand-measured layout, the font cache, the soft-keyboard hack and the compact-layout pixel code then become CSS and native inputs.

**Decided with the user:**
- Replace everywhere, including `/play/`.
- The Python core runs in **Pyodide inside a Web Worker**. It stays Python because replays depend on `random.Random` and every bot and tool is Python.
- Local `uv run python main.py` serves the same static bundle on localhost and opens a browser.
- Vanilla ES modules with no build step, like `leaderboard/js/`.

**Checked facts that shape the plan:**
- `sys.platform` is `"emscripten"` under Pyodide too, so `paths.is_web()` would switch on every `platform.window` branch, which would then fail. A real host switch is needed.
- A Worker has no `localStorage`, clipboard or `document`, so today's bridges can't move into it as they are.
- The core is stdlib-only (`pygame-ce` is the only dependency), so bare Pyodide core is enough.
- `log.mjs` and `pbp.mjs` already allow `localhost:8000` and `127.0.0.1:8000` as origins, so the local server uses port 8000.
- `turnfilm.Reel._apply` only assigns values, and fleet ids are positions in `board.fleets`, so serialization must keep fleet order.

## The boundary

**Python does no I/O and never imports `js`.** JS sends commands in. Python replies with snapshots plus a list of effects, and JS carries the effects out.

New pure-Python modules, added to both core lists in `tests/test_settings.py`:

- `starconquest/app.py`: the pygame-free logic in `main.py` (lines ~68–1777), moved verbatim. That covers `resolve_turn`, `open_replay`, `open_history`, `apply_rewind`, the hint functions, the `pbp_*` helpers, the share/post functions, the daily, gauntlet and run openers, and `record_best`.
  - Functions that write into `MenuState` return `(msg, ok, payload)` instead.
  - `resolve_turn` splits into a pure `end_turn(...) -> TurnResult(events, film, was_visible, snap)`. It no longer writes `ui.film` or calls `_primed`.
- `starconquest/pbpclient.py`: `PbpClient` holds the play-by-post state machine that is now local variables in `main()` (`pbp_ident/poll/write/make`, `pbp_wait`, `pbp_failures`, ...). It reuses `pbp_poll_delay`, `pbp_idle_floor` and `pbp_verdict`.
- `starconquest/hostio.py`: `host()` returns `desktop`, `pygbag` or `worker`. It holds an effect outbox and a `request(url, body, signed) -> pbp.Request` that is filled by a `net.reply` command.
- `starconquest/serial.py`: compact JSON for:
  - the static map (systems, lanes, players), sent only when the board's identity changes
  - the board (systems as `[owner, ships, progress]`, fleets in `state.fleets` order)
  - fog, the order book (`pending`, forwarding rules, `available`, slider, `forecast_landing`, route plan) and the `can_*` flags
  - events, with derived values computed in Python
  - films (`turnfilm.film()` beats and cues)
- `starconquest/session.py`: `Session.handle(cmd_json) -> reply_json`. It owns settings, state, ui, log, the history cache, the `PbpClient` and the gauntlet-generation job.

**Protocol:** `{id, op, args}` goes in; `{id, ok, result, view, turn, effects[]}` comes back.
- Ops are grouped: `boot`, `settings.*`, `game.*`, `turn.end`, `hint.*`, `history.*`, `share.*`, `pbp.*`, `daily/gauntlet/gen/run.*`, `custommap.*`.
- `ui.call{method, args}` runs one of an allow-listed set of `Ui` methods: `select`, `begin_send`, `set_send_count`, `toggle_forward`, the `step_*` methods, `route_tap`, `confirm_route`, `clear_*` and so on.
- Effects: `http`, `fire`, `bridge` (an allow-list calling `/board/js/post.mjs`, replacing `pbp.settle`'s `eval`), `store.set`, `clipboard`, `navigate`, `fragment`, `timer`, `fs.sync`, `status`.
- Messages go per command or per turn, never per frame.

**State split:**
- **Stays in Python:** everything rule-bearing or recorded. That is orders, forwarding arithmetic (`Ui.forward_this_turn` is the one formula), the route plan, fog folding, forecasts, `preview_fight`, `can_post`/`hinted`, history, coach and stats, and the stage, run, daily and pbp fields.
- **Moves to JS:** everything per-frame. That is hover, popup position, pan and zoom (a `WorldView` port), the film clock and a `Reel` mirror (assignment only), fading marks, the fog union while a film plays, history scrubbing, menu widgets, and the `animate_turns` preference.

**Small `Ui` refactors:**
- `reset_view` becomes a snap intent instead of mutating `WorldView`.
- `add_route_box` takes system ids.
- Slider pixel mapping moves to the caller.
- After these, `Ui` never sees screen pixels.

**Bridges:**
- `webstore` gets a mirror backend. `boot` passes in localStorage's `sc_*` keys, and writes go out as `store.set`.
- Network for `pbp`, `share`, `daily` and `campaign` goes through `hostio`; JS does the fetch and adds the `Authorization` header from `sc_auth`. The auth token never passes through Python. The localStorage mailboxes are deleted.
- Clipboard: JS builds a `ClipboardItem` inside the click handler, for Safari.
- Files (`saves/`, `maps/`, `games/`, user gauntlets): IDBFS mounted at `/data` in the worker.
- Deleted: `softkeyboard`, `uifont`, `widgets`.

## Phases (strangler: pygbag `/play/` stays live until phase 8)

**Freeze new pygame features once phase 1 starts (bug fixes only), or parity keeps moving.**

- **P. Parity baselines, captured first, from the untouched pygame build** (on `html-frontend`, before any code moves):
  - **Screens:** `tools/parity_shots.py` drives the pygame shell headlessly (SDL dummy driver, the way `tools/pwa/make_screenshots.py` does). It renders every screen from a scripted list into `results/parity/baseline/<name>@<size>.png`, at a desktop size and a phone size.
    - The screens: each menu tab, the play screen, the send popup, forwarding, route mode, the odds card, history and its graphs, the coach, game over, the briefing, the pbp invite and waiting views, the map maker, and so on.
    - Each screen's setup (settings, seed, turns played, `Ui` actions) goes to `tests/fixtures/parity_screens.json`. The new build replays the same list through Playwright into `results/parity/next/`, and `tools/parity_sheet.py` makes side-by-side contact sheets for review.
    - Pygame and HTML pixels will never match exactly, so this is a human visual review plus a check that every listed screen exists in both. It is not a pixel diff.
  - **Replays:** `tools/parity_replays.py` picks a corpus of stored logs from `games/` across seeds, modes, bots and fog/lane-battle settings, keeping only logs at the current `RULES_VERSION`. It writes `tests/fixtures/parity_replays.json`, holding each log's per-turn `replay.digest_hex` from native `replay.reconstruct`.
    - `tests/test_parity_replays.py` checks native against that file now.
    - In phase 0 a Node + Pyodide runner checks the same file. **The gate is that every turn's digest is identical.**
    - Bots also run live in Pyodide: a handful of seeded bot-vs-bot games (every ladder bot) are played natively and in Pyodide, and their final digests must match. That catches a bot whose decisions differ under WASM, which a stored replay can't, because replays never decide again.

- **A. Python seams.** Ships while pygbag is live, with no visible change. Run the full suite at each step.
  1. Extract `app.py`; `main.py` imports from it.
  2. Add `hostio` plus the webstore mirror backend, and route `pbp`/`share` transport through it.
  3. Extract `pbpclient.py`.
  4. Add `serial.py`, plus `tools/dump_film_fixtures.py`, which writes `webapp/tests/fixtures/films.json`.
  5. The `Ui` refactors above.
- **0. Spike at `/next/`, local first.**
  - `webapp/worker.mjs` loads self-hosted, pinned Pyodide (Python 3.12 line; `tools/fetch_pyodide.py` with a checksum) and a slimmed `starconquest.zip` (with `.pyc`), then plays bot against bot. A bare SVG board plays the reels.
  - **Measure** against pygbag and native, appending to `results/next-spike/` as it goes:
    - transfer bytes and time to first screen, cold and warm, on desktop and phone
    - per-turn ms for every ladder bot at 12, 24 and 40 nodes
    - reply sizes
    - Pyodide-under-Node `replay.digest_hex` on stored logs against native
  - **Exit:** startup no worse than pygbag, per-turn time within about 1.2× pygbag, identical digests. If it fails, stop and report before going further.
- **1. Play screen (solo parity).**
  - SVG map; side panel; footer that folds into a "more" list; End Turn.
  - Send popup with forecast and odds card; forwarding (hold, smart hold, splits); route mode.
  - Film plus marks; fog union; autoplay, play and fast forward; game over; retry, new map, undo.
  - Hint; history (scrubber, graphs, coach, rewind); lore.
  - Keyboard shortcuts; the compact layout in CSS (the docked popup becomes a bottom sheet).
- **2. Menu:** Basic, Combat, Advanced and AI tabs through `settings.patch`; sketch and lane survey (debounced); seed; saves; warnings; preferences; resume prompt; Daily and Leaderboard lists.
- **3. Sharing and leaderboard:** challenge link, Post, quick post and signed-in name, Share replays, `record_best`, par, campaign, `#log=` replays.
- **4. Play-by-post:** create, invite and copy, seat and spectate links, polling as `timer` effects, submit, resolve and lapse. Hold a poll while a reel plays.
- **5. Daily, gauntlets and runs:** briefings, the stage list, `#g=` links, generated gauntlets (`gen.pump` with progress, listing consent).
- **6. Map maker:** a JS editor on the `CustomMap` dict. `problems()`, `normalised()` and `without_node` stay the only validators.
- **7. Local launcher and PWA.**
  - `starconquest/serve.py`: stdlib `ThreadingHTTPServer` on `127.0.0.1:8000`, serving `/play/` (`webapp/`), `/board/js/`, `/pyodide/` and a hashed zip.
  - A local-only `POST` that writes finished logs into `games/`, so local records are kept.
  - It opens a browser. CLI arguments become a URL fragment.
  - `sw.js` v5: cache-first for the versioned Pyodide and zip; **`/api/` untouched**.
- **8. Cutover.**
  - `/next/` becomes `/play/`, with pygbag kept at `/play-classic/` for a few weeks.
  - Delete `render`, `input`, `menu`, `mapmaker`, `widgets`, `uifont`, `softkeyboard`, the `main` loop and the pygbag tooling (`mirror_runtime.py`, `runtime_files.txt`, `inject.py`).
  - Drop `pygame-ce` and `pygbag` from `pyproject.toml`; the project then has no runtime dependencies.
  - Rewrite CLAUDE.md's Shell section, `docs/design/shell.md`, `turnfilm.md`, `README.md` and `docs/sections.md`.

Track parity as one checklist line per phase in `docs/open.md`.

## Tests

- **Deleted at cutover:** `test_render.py`, `test_compact.py`, `test_mirror_runtime.py`. A few phone-layout checks become Playwright checks.
- **Split:**
  - `test_input.py`: what each action means moves to `tests/test_session.py`; gesture mechanics move to `webapp/tests/controller.test.mjs` on a pure `(state, event) -> (state, commands)` controller.
  - `test_menu.py`: the settings and roster rules move to session tests.
  - `test_mapmaker.py`: the rules move to `test_custommap.py`.
- **Kept, retargeted from `main.` to `app.`, `session` or `pbpclient`:** `test_route`, `test_hint`, `test_forwarding`, `test_coach`, `test_stats`, `test_seat_*`, `test_pbp_client`, `test_pbp_spectate`, `test_run`, `test_campaign_status`, `test_webstore`, `test_web_build`, `test_slim_web`.
- **New, Python:**
  - `test_session.py`, at the protocol level:
    - no `http`/`fire` effect except on Post or with Share replays ticked
    - a hinted game and an autoplay demo are never posted
    - challenge links go out only as `clipboard` effects
    - pbp flows driven by fake replies
  - `test_serial.py`: fleet order is kept.
  - `test_webapp_sync.py`: the fixtures are current.
- **New, Node** (`webapp/tests/*.test.mjs`):
  - `reel.test.mjs`: the history-path rule in JS (turn *i*'s film on board *i-1* lands on board *i*, from fixtures).
  - Plus tests for progress, marks, camera, controller and protocol.
  - An optional Pyodide digest test, skipped when Pyodide isn't cached.
- **Playwright smoke tests (about 6):** boot, start, send, End Turn landing, history, phone viewport, offline reload.

**Rules that carry over** (reworded for JS where needed):
- A view draws from snapshot plus view state plus time, and never calls the session from a draw path.
- `FILM_COMBAT_MS` stays 0; events carry results; every reel is primed; marks outlive their film; fog is a union; deferred work happens only in `land_film`; don't widen `_lane_crossings`.
- `forward_this_turn` is the one formula; the popup is the only ship-count editor; route mode doesn't commit as you go.
- `can_post` gating; hinted games are never posted; sharing happens only on an explicit action; challenge links travel by clipboard only; `sw.js` never touches `/api/`.

**Rules that become obsolete:** `config.s()`/`ui_scale` font keying; the 1440x960 menu canvas; `touch_ui`/`compact` as Python flags (they become CSS media queries, with the same tap-size and no-keyboard-text principles); the `*_rect` regions; `leave_app`; `test_tab_content_stays_inside_the_panel`.

## Risks

- **Startup and size:** Pyodide against pygbag. Phase 0 is the gate. Self-host, use cache-first on versioned paths, and start the worker while the HTML menu is drawing.
- **Long bot turns block the worker:** show a "Thinking" state. To abort, terminate the worker and rebuild from the `GameLog` via `replay.reconstruct`; determinism makes that safe.
- **Determinism:** pin Pyodide and treat an upgrade as a gated change (digest test). Keep fleet order. Note that actuary's `LOOK_BUDGET_S` makes play strength depend on device speed.
- **Version skew:** an `api_version` handshake in `boot`, a hashed zip name, and a reload on mismatch.
- **Netlify quota (about 20 deploys a month):** `/next/` rides along in existing deploys; iterate locally and on previews; batch merges.
- **Lost:** the python-for-android build, the fullscreen key, `--phone`. The PWA covers Android.
- **Local sign-in:** the localhost origin has its own localStorage, so board sign-in doesn't follow (as on desktop today).
- **Fog:** JS receives the whole board. This matches "fog is convenience, not secrecy", and pygbag's memory can be inspected today anyway.

## Verification

- **Phase A:** full `uv run pytest` plus `uvx ruff check .`; `tests.sim --trials 50 --film`; and `main.py` still plays under pygame.
- **Phase 0:** the numbers in `results/next-spike/` against the exit gate; the Pyodide digests match native on every stored log.
- **Each later phase:**
  - `node --test leaderboard/tests/*.test.mjs webapp/tests/*.test.mjs`, the `test_session` cases for that phase, and Playwright smoke.
  - Manual play at `/next/` on desktop and on a phone (deploy preview), ticking that phase's checklist in `docs/open.md`.
- **Cutover:** the full suite with no pygame installed (`uv sync` after dropping the dependency); a stored-log digest sweep; a test that no `starconquest/` module imports `pygame` or `js`; then deploy and check `/play/`, `/play-classic/`, the board and `/api/`.
