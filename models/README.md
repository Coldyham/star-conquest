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
- `owner_id` must be your `pid`. You command your own ships and nothing else — the
  engine discards any order you issue naming a different owner.

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
| `state.travel_turns(a, b)` | turns to cross that lane if launched now, or `None` if not adjacent |
| `state.adjacency` | `dict[int, dict[int, int]]` — `src -> {neighbour: turns}`, as baked at map generation |
| `state.rng` | the seeded RNG (use it for tie-breaks so games stay reproducible) |
| `state.turn`, `state.winner` | turn counter / winner id (or `None`) |

`System` fields: `id`, `pos`, `owner_id`, `ships`, `production` (turns per new
ship — lower is richer), `prod_progress`, `neighbors` (`list[int]` of adjacent
system ids).

`Player` fields: `id`, `name`, `is_human`, `is_neutral`, `alive`, `ships_lost`
(ships of theirs destroyed in combat so far, all match long), `ai_strategy`,
`ai_params`.

`ai_params` is a seat's tuning (`reserve_fraction`, `reserve_floor`,
`expand_margin`, `attack_margin`, `reinforce_margin`), set per seat on the **AI**
tab. Reading your own is optional — the built-in heuristic uses it, and yours may
too if you want the same knobs to steer your bot.

`ai_params.aux` is the exception: a **generic knob the core never interprets**,
free for each bot to define however it likes. It defaults to `1.0`, so treat 1.0 as
"untuned" and make that your normal behaviour — a seat that never touched the
slider must still play well. Being per-seat, it is also readable on *rivals*, so a
bot can tell a shallow opponent from a deep one.

Tell the AI tab what yours means and it gets a properly labelled slider whenever
your bot holds the seat — three optional module-level names, all read by
`ai.aux_spec`:

```python
AUX_LABEL = "Search depth"   # required to show the slider at all
AUX_RANGE = (0, 8, 1)        # (lo, hi, step); defaults to (0.0, 8.0, 1.0)
AUX_INT = True               # show/store whole numbers (default: 2dp floats)
```

Declare no `AUX_LABEL` and the slider is simply hidden for your bot, which is the
right answer if you ignore `aux`. `models/knower.py` is the worked example: it
labels it *Search depth* and reads it as exactly that (0 = no prediction, 1 =
predict one turn, N = predict then search N−1 plies forward).

`Fleet` fields: `owner_id`, `source_id`, `dest_id`, `ships`, `turns_remaining`.

## Pricing a fight

Combat swings each side by `config.COMBAT_JITTER` and multiplies whoever holds
the system by `config.DEFENDER_ADVANTAGE`. **Both are menu sliders**, so the
multiple you need to be *sure* of a fight is not a constant, and a bot that
hardcodes one plays a different game from the one the player set up. Ask instead:

```python
from starconquest import combat

combat.edge_attacking()   # x the garrison, to take a system in the worst roll
combat.edge_defending()   # x the incoming force, to hold one
```

Both read the live config, and they move in *opposite* directions: the advantage
belongs to whoever holds the system, so turning it up makes taking dearer and
holding cheaper. At the default 0.10 / 1.0 both are the familiar 1.222x.

Two things they do not promise. Ties break to the defender, so clear the multiple
*strictly*; and clearing it only means the defender loses, not that you win —
near-matched forces annihilate and the system goes neutral, which is why every
bot here also floors its ask at `target.ships + 1`.

If your margins were tuned at one jitter, pass that swing as `min_swing`
(`combat.edge_attacking(1.1 / 0.9)`) and a gentler setting can no longer thin
them below the figure you measured, while a wilder one still widens them. Every
bot in this folder does exactly that — see `TUNED_SWING` in any of them.

## Predicting the other seats

`state.players` is every seat, not just yours, and `ai_strategy` / `ai_params` /
`is_human` are readable on all of them. Since `ai.STRATEGIES` maps a strategy name
to the function that will decide that seat, you can *run your opponent's code* and
find out what they are about to do.

This works because turns resolve **simultaneously**: the engine hands every player
the same unmutated start-of-turn state and applies nothing until all of them have
decided, so an opponent's orders cannot depend on yours. There is no circularity to
untangle — one forward pass of their real code is the answer, and it comes with
their `ai_params` applied for free, because their own function reads them.

`models/knower.py` is the worked example. If you write another, four rules:

- **Clone the state first.** `decide` is contractually read-only, but you cannot
  assume a rival honours that, and you need your own `rng` anyway (below).
- **Draw nothing from `state.rng`.** Seats decide in ascending player id, so the
  seats after you will find the stream exactly where you leave it. Leave it alone
  and their orders are reproducible bit-for-bit; draw once and they aren't. Use a
  private `random.Random` seeded from `state.seed`/`state.turn` — never the clock,
  or a seed will stop reproducing its match.
- **Read `ai.STRATEGIES` lazily, inside `decide`.** Model files are imported in
  sorted filename order, so at *your* import time the registry is still incomplete.
- **Never call `ai.load_models()` from a model.** It imports every file in this
  folder with no re-entry guard — including yours, which would call it again.

Two things you cannot predict: a **human** seat (their orders come from the UI, not
from code — knower models them with a weaker copy of itself and only ever lets that
*raise* a threat estimate), and **another predicting bot**, which will recurse
unless you model it with something simpler.

To make that last one detectable, a predicting bot should advertise itself:

- **`IS_ORACLE = True`** — a module-level flag meaning "this file predicts other
  seats". Anyone seeing it on your module will proxy your seats with a simpler model
  instead of calling you, which is what stops two predictors recursing forever.
- **`is_oracle_seat(player) -> bool`** — optional, and preferred when present, since
  prediction may be a per-*seat* setting that one module-level flag cannot express.
  knower defines it as "depth ≥ 1", so its depth-0 seats correctly report `False`
  and get predicted for real rather than approximated. Keep the flag as well, for
  anything that only checks it.

Recursion is still your own responsibility: guard re-entry into your `decide` (a
module-level depth counter is enough in this single-threaded codebase) rather than
trusting every rival to check your flags.

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

The bots already here, weakest to strongest: `rusherplus.py` (the above, plus
arrival arithmetic), `claudebot.py` (focus fire, one turn deep), `thinker.py`
(scheduled defence and staggered pincers), `marshal.py` (thinker's phases, but it
commits its surplus instead of parking it), `knower.py` (the oracle — it runs every
rival's own `decide` before the engine asks for it).

`marshal.py` is worth reading for its docstring as much as its code: it records
three plausible ideas that were built, measured and then *deleted* for not beating
the configuration without them. That is the bar here — measure before you keep.

## Benchmark it before you submit

`tests/sim.py` runs games headlessly, so you can measure a bot properly instead of
eyeballing a couple of matches. Start by checking you beat the built-in:

```sh
uv run python -m tests.sim --ai rusherplus heuristic --swap --trials 100
```

`--swap` rotates the roster through every seat so start-position luck cancels out —
without it a result mostly tells you which corner of the map is stronger. Then rank
yourself against every bot in this folder. Both tournament flags default their
roster to all registered strategies, so neither needs `--ai`:

```sh
uv run python -m tests.sim --ladder --trials 50   # pairwise: every pair head-to-head
uv run python -m tests.sim --swap --trials 50     # free-for-all: everyone in one game
```

`--ladder` is the one to trust for "is my bot good": it plays each pair on its own,
both seatings, and prints a head-to-head grid, so a bot that ranks mid-table but
beats the leader is visible rather than averaged away. `--swap` answers the
different question of who survives a crowded map. Watch the **timeout** count in
either — a bot that stalls into 600-turn games is usually failing to commit, and
timeouts are excluded from the average length.

Useful extras: `--mode symmetric` (every seat starts from an identical sector, so
seat bias is exactly zero rather than merely balanced), `--nodes`/`--players` to
size the map, and `--seed 1 --verbose` to watch a single game turn by turn.
