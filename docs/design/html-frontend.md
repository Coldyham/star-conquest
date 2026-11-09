# The HTML frontend (in progress)

The pygame shell (`render`, `menu`, `main`'s loop, `mapmaker`, `input`,
`widgets`, `softkeyboard`) is being replaced by one HTML frontend: SVG for the
map, DOM and CSS for panels and menus, vanilla ES modules with no build step,
like `leaderboard/js/`. The Python core stays Python and runs in Pyodide in a
Web Worker. Locally, `main.py` will serve the same bundle on `localhost:8000`
and open a browser.

## Why

The web build is pygame too: pygbag runs CPython and pygame in WASM. So
dropping only the desktop GUI would remove about 40 `is_web()` branches and
little else. Most of the shell is laid out, measured and wrapped by hand, and
it has a phone layout of its own (`shell.md`, "Phones: the compact layout").
A browser does that with CSS and native inputs. Python stays for the core
because stored replays depend on `random.Random`'s stream and every bot and
tool is Python.

## How it is staged

The pygame build stays live at `/play/` until the new one reaches parity at
`/next/`. The order:

1. The parity baselines below, taken from the untouched pygame build.
2. Python seams: the pygame-free logic out of `main.py` into an `app` module,
   with a JSON session the worker exposes.
3. A Pyodide spike, gated on startup time, per-turn bot time and identical
   replay digests.
4. Screen by screen to parity, then cutover.

Python does no I/O in the new build. It returns effects (fetch, storage,
clipboard), and the page carries them out. pygame gets bug fixes only once
work on the play screen starts.

## Parity checks

Two checks hold the new build to the old one.

**Replays reproduce exactly.**
- `tools/parity_replays.py` records 14 seeded all-bot games into
  `tests/fixtures/parity_replays.jsonl.gz`. They cover every ladder bot and
  stop, the symmetric layouts, lane battles, off-default balance and a custom
  map, all played with the wall-clock guards lifted
  (`ai.set_budget_scale(math.inf)`).
- The same file holds 10 stored `games/` logs at the current `RULES_VERSION`.
- Each case keeps its board digest after every turn.
- `tests/test_parity_replays.py` checks the native build against the fixture
  two ways:
  - every log reconstructs to the recorded boards;
  - every bot game, played again from its settings alone, makes the same
    boards, with the bots deciding afresh.
- The Pyodide build must pass the same two checks. The second is the one a
  stored replay can't make, because a replay never asks a bot to decide again.

**Every screen looks the same.**
- `tools/parity_shots.py` draws 42 screens of the pygame shell headlessly
  (98 images) as the web build's variant, at a desktop size and on a phone
  held both ways. The screens are each menu tab and list, the play screen
  with its popups, forwarding, route mode, the hint and the odds card, fog,
  game over, history and its graphs, the coach, the confirm modals, a gauntlet
  stage, the play-by-post overlays, and the map maker.
- Each screen is a recipe: settings, a seed, then steps (`Ui` calls, field
  sets). The resolved recipes are committed as
  `tests/fixtures/parity_screens.json`, and `tests/test_parity_screens.py`
  keeps them in step with the tool.
- The HTML build will replay the same steps through its session and shoot
  `results/parity/next/`. `tools/parity_sheet.py` then sets each pair side by
  side and flags a screen missing from either side.
- Pygame and HTML pixels will never match, so this is a review by eye plus a
  check that every screen exists in both builds. It is not a pixel diff.

The images live under `results/parity/baseline/` (gitignored, about 12 MB).
Drawing is deterministic: two full runs come out byte-identical, with the clock
pinned and preferences read from an empty store. So any commit before pygame
is deleted can redraw them exactly. The first baseline was drawn at the commit
that added these tools.

## The seams (phase A)

- `app`: `main`'s pygame-free logic, moved verbatim. The nine functions that
  wrote into `MenuState` answer an `app.MenuNote` instead, and `main` applies it.
- `pbpclient.PbpClient`: the play-by-post machine. `step()` returns an
  `Outcome` for the frontend to apply.
- `hostio`: the worker host. It keeps the page's address and a storage mirror,
  queues effects (`http`, `fire`, `bridge`, `store.set`, `clipboard`,
  `navigate`, `fragment`), and takes replies.
- `serial`: JSON shapes for the map, the board, fog, events and films. Board
  fleets keep `state.fleets` order.
- `webapp/js/reel.mjs` mirrors `turnfilm.Reel`; `webapp/tests/reel.test.mjs`
  holds it to the history-path rule.
- `Ui`'s three pixel-reading methods each gained a pure half (`snap_points`,
  `set_slider_fraction`, `add_route_sids`).

Every step left the full suite passing and the 98 parity screens
byte-identical.

## The Pyodide spike (phase 0)

`uv run python -m starconquest.serve` serves `/next/` locally: an all-bot game
on an SVG board, each turn's film played by the JS `Reel` and checked against
the board Python returns. `tools/build_web.sh` stages the same tree at
`web/next/` for deploy previews, never failing the build over it. Pyodide
0.27.7 (CPython 3.12.7) is self-hosted, downloaded by `tools/fetch_pyodide.py`
and checked against its published SHA-256.

**The gate, measured 2026-11-09** (`tools/spike_bench.py`,
`tools/spike_pygbag.py`; ledgers and `report.md` in `results/next-spike/`):

- **Replays: pass.** In Pyodide all 24 corpus games reconstruct, and the 14 bot
  games replay, to identical boards: 2,326 boards, none different. Every timing
  case also ended on the same board natively, in Pyodide under Node, in Pyodide
  in Chrome, and in pygbag in Chrome.
- **Bot time: pass.** Pyodide's decide time in Chrome is a median 1.03× pygbag's
  (range 0.98–1.05×) over the cases above 0.5 ms. Both are about 2.1× native.
  The costliest case is knower at Council: 57 ms a turn at 40 systems.
- **Startup: pass.** In headless Chrome, served locally, Python is ready 1.21 s
  after navigation cold and 0.98 s warm. pygbag reached a bench script's
  `main` at 1.58 s cold, before the real game would import its shell, and
  1.23–1.52 s warm.
- **Download: about half.** 6.0 MB gzipped (Pyodide 5.6 MB plus the 227 KB game
  zip), against pygbag's 10.6 MB runtime plus its 427 KB archive.

**Not measured:** a real phone (the phone screenshot is an emulated viewport on
desktop CPU), and download time over a real network. Both sides were served
from localhost.

**Caveat:** the service worker (scope `/`) caches `/next/` like any other path.
That is safe, because the zip's name is its hash, but `/next/` gets its own
rules in phase 7.
