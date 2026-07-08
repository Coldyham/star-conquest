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
uv run python main.py                          # random map, 3 players (you are blue)
uv run python main.py --mode symmetric --players 4
uv run python main.py --seed 42 --nodes 24     # reproducible map
uv run python main.py --autoplay               # AI plays every seat (a demo)
```

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
| New map | `R` |

Your queued fleets show as arrows; a system's number is the ships you can still
deploy this turn. Press **End Turn** to resolve everyone's moves at once.

## Development

The code is split into a pure, pygame-free simulation core and a thin
presentation shell, so the whole game is testable headlessly.

```
starconquest/
  config.py      # every balance/aesthetic constant
  model.py       # dataclasses (GameState, System, Lane, Fleet, Order, Player)
  geometry.py    # distance, segment-crossing, world->screen transform
  mapgen.py      # random + symmetric generation
  combat.py      # Lanchester-with-jitter resolution
  engine.py      # simultaneous turn resolution (pure)
  ai.py          # heuristic AI (compute_orders)
  render.py      # drawing (pygame)
  input.py       # event handling (pygame)
  viewstate.py   # transient UI state
main.py          # entry point + main loop
tests/           # pytest suite + sim.py headless AI-vs-AI harness
```

```sh
uv run pytest                          # full test suite
uv run python -m tests.sim --verbose   # watch one AI-vs-AI game in the terminal
uv run python -m tests.sim --trials 200   # batch stats (winners, length, timeouts)
```

## Roadmap (not in the MVP)

Tech tree & ship-speed upgrades (the intended late-game pacing mechanism),
race/empire customisation, selectable AI personalities / difficulty sliders,
animations & sound, camera pan/zoom, multi-hop fleet routing, fog-of-war.
