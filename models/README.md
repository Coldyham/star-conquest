# Custom AIs

Drop a Python file in this folder and it becomes a selectable AI strategy. Each
`.py` file here is discovered at launch (and whenever you open the **Strategy**
dropdown on the menu's **AI** tab); the strategy's name is the filename without
`.py`. Every seat defaults to the built-in `heuristic`.

Files in this folder are **committed to the repo**: `tools/build_web.sh` bundles
them into the browser/PWA build too, so whatever's here is what's playable on
the deployed site's Strategy dropdown. To add a bot, commit it (or open a PR)
rather than just dropping it in locally.

## The contract

A model file must define a top-level function:

```python
def decide(state, pid) -> list[Order]:
    ...
```

- `state` is the current `GameState` (read-only — don't mutate it).
- `pid` is your player id (an `int`). Your home systems are the ones whose
  `owner_id == pid`.
- Return a list of `Order`s. Each order launches ships along one lane:
  `Order(owner_id, source_id, dest_id, ships)` — built **positionally**.
  Ships are deducted from the source at launch; you may issue at most the ships
  a system currently has.

Turns resolve simultaneously, so you decide against the start-of-turn state and
launch order never matters. A file that fails to import or has no `decide` is
skipped (it just won't appear in the dropdown), and any strategy name that isn't
loaded falls back to `heuristic` — nothing crashes.

## What you can read off `state`

Import what you need from `starconquest.model`:

```python
from starconquest.model import Order          # to build orders
```

Systems, players, and fleets:

| Access | Meaning |
| --- | --- |
| `state.systems` | `dict[int, System]` — every node, keyed by id |
| `state.systems_of(pid)` | `list[System]` you own |
| `state.players` | `dict[int, Player]` (player `0` is neutral) |
| `state.non_neutral_players()` | real players (you + rivals) |
| `state.fleets` | `list[Fleet]` currently in transit |
| `state.fleets_incoming(dest_id)` | fleets (yours and enemies') heading to a node |
| `state.are_adjacent(a, b)` | `bool` — is there a lane between two systems |
| `state.travel_turns(a, b)` | hops along that lane, or `None` if not adjacent |
| `state.adjacency` | `dict[int, dict[int, int]]` — `src -> {neighbour: turns}` |
| `state.rng` | the seeded RNG (use it for tie-breaks so games stay reproducible) |
| `state.turn`, `state.winner` | turn counter / winner id (or `None`) |

`System` fields: `id`, `pos`, `owner_id`, `ships`, `production` (turns per new
ship — lower is richer), `prod_progress`, `neighbors` (`list[int]` of adjacent
system ids).

`Player` fields: `id`, `name`, `is_human`, `is_neutral`, `alive`, `ai_strategy`,
`ai_params`.

`Fleet` fields: `owner_id`, `source_id`, `dest_id`, `ships`, `turns_remaining`.

## Example: copy this into `models/rusher.py`

```python
"""A simple aggressive AI: every system attacks the weakest thing next door."""

from starconquest.model import Order


def decide(state, pid):
    orders = []
    for sys in state.systems.values():
        if sys.owner_id != pid or sys.ships <= 1:
            continue
        # Adjacent systems we don't already own.
        targets = [state.systems[n] for n in sys.neighbors
                   if state.systems[n].owner_id != pid]
        if not targets:
            continue
        target = min(targets, key=lambda s: (s.ships, state.rng.random()))
        send = sys.ships - 1                 # leave one ship to hold the system
        if send > target.ships:              # only commit when we'd likely win
            orders.append(Order(pid, sys.id, target.id, send))
    return orders
```

Save that, open the game, go to the **AI** tab, and pick **rusher** for any seat.
For the full built-in strategy to study, see `starconquest/ai.py` (`compute_orders`).
