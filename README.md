# Star Conquest

A minimalist sci-fi turn-based strategy game. Map conquest boiled down to its
bare minimum: a graph star map where **systems are nodes** and **spacelanes are
edges**. One ship type. Take every system to win.

- **Systems** have a *production* rate (turns-per-ship — lower is richer, drawn
  bigger) and a garrison of ships.
- **Spacelanes** have a length in light-years that becomes a travel time in
  turns (shown on each edge). Fleets in transit pass through each other freely —
  combat only happens when a fleet reaches a system.
- **Combat** is Lanchester's square law with a slight random swing, so
  concentrating your fleet wins decisively (10 vs 6 leaves ~8, not 4).
- **Turns resolve simultaneously**: every player commits orders against the same
  board, then all orders execute together — no turn-order advantage.
- **Maps** are procedurally generated, either **random** (planar, evenly spread,
  peripheral starts so no seat is boxed in) or **symmetric** (rotationally
  identical sectors for a perfectly fair start).

## Run it

```sh
uv run python main.py                          # opens the setup menu
uv run python main.py --players 4 --mode symmetric   # CLI args pre-fill the menu
uv run python main.py --seed 42 --nodes 24     # pre-fill a reproducible map
uv run python main.py --no-menu --autoplay     # skip the menu; AI plays every seat
```

Launch drops you into a **setup menu** with three tabs — **Basic** (players,
systems, map type, seed, autoplay), **Advanced** (map spread, economy, combat,
ship speed), and **AI** (per-seat opponent tuning, with copy/reset-all, and a
**Strategy** dropdown per seat). CLI flags pre-fill it; `--no-menu` starts a game
straight from them. The footer's **Save**/**Load** buttons write the whole
configuration to a named `.json` file under a gitignored `saves/` folder so a
tuned galaxy can be reused.

### Custom AIs

Drop a Python file defining `decide(state, pid) -> list[Order]` into the
gitignored `models/` folder and it becomes a strategy you can assign to any seat
from the AI tab's dropdown (the built-in `heuristic` is the default). See
[`models/README.md`](models/README.md) for the authoring contract, the read-only
`GameState` API a bot can use, and a copy-paste example.

## Controls

| Action | Input |
|---|---|
| Select one of your systems | left-click it |
| Choose a destination | left-click a highlighted neighbour |
| Adjust ships to send | mouse wheel |
| Confirm the fleet | left-click |
| Cancel / back | right-click or `Esc` |
| End the turn (resolve) | `End Turn` button, `Enter`, or `Space` |
| Toggle autoplay | `A` |
| New map (same settings) | `R` |
| Back to the setup menu | `M` |

Your queued fleets show as arrows; a system's number is the ships you can still
deploy this turn. The top bar scores each player by systems, ships, and
production (ships/turn). Press **End Turn** to resolve everyone's moves at once.

## Development

The code is split into a pure, pygame-free simulation core and a thin
presentation shell, so the whole game is testable headlessly.

```
starconquest/
  config.py      # every balance/aesthetic constant
  settings.py    # pure pre-game config (Settings) -> build_state
  model.py       # dataclasses (GameState, System, Lane, Fleet, Order, Player, AiParams)
  geometry.py    # distance, segment-crossing, world->screen transform
  mapgen.py      # random + symmetric generation
  combat.py      # Lanchester-with-jitter resolution
  engine.py      # simultaneous turn resolution (pure)
  ai.py          # heuristic AI + per-seat params + strategy registry (decide/register)
  render.py      # drawing (pygame)
  input.py       # event handling (pygame)
  menu.py        # pre-game setup screen (pygame)
  viewstate.py   # transient in-game UI state
  paths.py       # where writable data lives (repo root, or app-private on Android)
  uifont.py      # bundled-font loader (falls back to a system monospace)
  assets/        # bundled DejaVu Sans Mono TTF (+ licence)
main.py          # entry point + menu/game scene loop
tests/           # pytest suite + sim.py headless AI-vs-AI harness
```

```sh
uv run pytest                          # full test suite
uv run python -m tests.sim --verbose   # watch one AI-vs-AI game in the terminal
uv run python -m tests.sim --trials 200   # batch stats (winners, length, timeouts)
```

## Android (sideload)

The same source builds an Android APK via [Buildozer] (python-for-android). The
pure core is untouched; only the pygame shell adapts — touch input, a DPI/scale
layer, a bundled font, and writing saves to app-private storage (see
[`paths.py`](starconquest/paths.py)). `pygame-ce` is a drop-in for `pygame` with a
working p4a recipe, so no game code changes between desktop and phone.

```sh
pipx install buildozer          # or: pip install --user buildozer
sudo apt install -y openjdk-17-jdk autoconf libtool pkg-config \
    zlib1g-dev libncurses-dev libtinfo6 cmake libffi-dev libssl-dev   # p4a build deps
buildozer android debug         # first run downloads the Android SDK/NDK (slow)
adb install -r bin/starconquest-*-debug.apk
```

The build is configured in [`buildozer.spec`](buildozer.spec) (landscape,
fullscreen, arm64-v8a + armeabi-v7a, no permissions — saves live in internal
app-private storage). On-device: tap a system then a neighbour to send (a popup
tunes the count with −/+ and Half/All), the on-screen **End turn** / **History**
buttons and the hardware **Back** key replace the keyboard shortcuts, and tapping
a text field raises the soft keyboard. Drop-in `models/` AIs still work — push a
`.py` into the app's `models/` dir. The autoplay/demo `tests/sim` harness is
desktop-only and isn't bundled.

[Buildozer]: https://buildozer.readthedocs.io/

## Roadmap (not yet built)

Tech tree & ship-speed upgrades (the intended late-game pacing mechanism),
race/empire customisation, animations & sound, camera pan/zoom, multi-hop fleet
routing, fog-of-war. Nearer term: exposing the remaining config knobs. **Drop-in
user AIs** now work (`models/` + the per-seat Strategy dropdown); still wanted for
full **head-to-head competition** is a documented public state/query API and a
headless sim harness that assigns a strategy per seat.

Per-seat AI tuning and selectable difficulty via the AI tab are already here.
