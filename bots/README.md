# Bots in any language

A bot here is a **program**, not a Python file. It reads a JSON board on stdin
and writes JSON orders on stdout, one line each, so it can be written in
anything that can do that. Python's contract is
[`models/README.md`](../models/README.md); this is the other door, and the game
rules on that page (production, combat, the square law, pricing a fight) apply
to your bot exactly the same way.

**Where these run:** the ladder and the tournaments, not the app. The in-app
Strategy dropdown and the browser build ship `models/` only — the web build is
CPython compiled to WASM and cannot start a child process at all — so this is
the folder for competition rather than for shipping.

## Quick start

`rusherwire/` is the worked example: `models/rusherplus.py`, ported to read the
payload and importing nothing from the game. Copy it, or start from scratch.

```sh
cp -r rusherwire mybot                       # then edit mybot/main.py
cp rusherwire.bot.json mybot.bot.json        # then point "cmd"/"cwd" at yours

uv run python -m tests.sim --external --ai mybot heuristic --swap --trials 100
uv run python -m tests.sim --external --ladder --trials 20     # the whole roster
```

`--external` is required: without it, `bots/` is not registered at all.

## The conversation

One process per seat, per game. The runner speaks first and always waits for
exactly one reply line:

```
runner → {"type": "hello", ...}      once, before the first turn
bot    → {"type": "ready", "name": "mybot", "version": "1.0.0"}
runner → {"type": "turn", ...}       once per decision
bot    → {"type": "orders", "orders": [...]}
                                     ...repeated until the game ends
```

Flush after every reply, or the runner sees nothing and times out. Anything you
write to stderr is yours — the runner never parses it, and discards it unless
you are debugging. Ignore message types and fields you don't know: fields get
added, and a bot that tolerates them keeps working.

## `hello` — everything fixed for the match

Trimmed to two systems, two lanes and two seats; a real one carries them all.

```json
{
  "type": "hello",
  "protocol": 1,
  "you": 1,
  "budget_ms": 15000,
  "reveal_opponents": false,
  "setup": {
    "mode": "random", "seed": 12, "nodes": 8, "players": 2,
    "ship_ly_per_turn": 6.0, "ship_speed_growth_pct": 0.0,
    "home_start_ships": 12, "home_production": 3,
    "garrison_base": 2, "garrison_k": 12, "garrison_jitter": 3,
    "combat_jitter": 0.1, "defender_advantage": 1.0,
    "neutral_produces": false, "in_lane_battles": false,
    "node_jitter": 0.85, "relax_min_sep_frac": 0.6, "lloyd_passes": 1,
    "extra_edge_fraction": 0.4, "max_edge_length_frac": 0.5,
    "fog_sight": 8, "fog_scout": 8
  },
  "rules": {
    "combat_jitter": 0.1, "defender_advantage": 1.0, "swing": 1.2222222222222223,
    "edge_attacking": 1.2222222222222223, "edge_defending": 1.2222222222222223,
    "in_lane_battles": false, "neutral_produces": false,
    "ship_ly_per_turn": 6.0, "ship_speed_growth_pct": 0.0
  },
  "seats": [
    {"id": 0, "is_neutral": true},
    {"id": 1, "is_neutral": false, "strategy": "seat1",
     "params": {"reserve_fraction": 0.25, "reserve_floor": 2, "expand_margin": 1.3,
                "attack_margin": 1.5, "reinforce_margin": 2, "aux": 1.0}}
  ],
  "map": {
    "systems": [
      {"id": 0, "pos": [338.19, 775.84], "production": 4, "neighbors": [7]},
      {"id": 1, "pos": [232.58, 203.92], "production": 3, "neighbors": [3, 5]}
    ],
    "lanes": [
      {"a": 0, "b": 7, "length_ly": 213.4, "base_turns": 2},
      {"a": 1, "b": 3, "length_ly": 331.7, "base_turns": 3}
    ]
  }
}
```

- **`you`** is your player id. Systems whose `owner` equals it are yours; `0` is
  neutral, and neutral is a real player rather than an absence.
- **`setup`** is the whole game configuration, including knobs the host tuned.
  Two of them change everything: `nodes` and `ship_ly_per_turn` together move
  lane length from ~1 turn to ~36, so a margin that works on one setup can be
  unreachable on another. Don't hardcode distances.
- **`rules`** is the combat arithmetic, already derived. `edge_attacking` is what
  an attack must beat the garrison by to win the *worst* roll, `edge_defending`
  what a garrison must beat an incoming force by; `swing` is `(1+j)/(1-j)` alone.
  Use these rather than your own constants — they move with the host's settings,
  and clearing them is not a promise of capture: ties break to the defender and
  matched forces annihilate to neutral, so ask for at least `their ships + 1`.
  The maths behind them is under "Pricing a fight" in
  [`models/README.md`](../models/README.md).
- **`seats`** carries every seat's tuning. `params.aux` is a free per-bot knob
  (`1.0` means untuned), and it is readable on rivals as well as on you. Rival
  `strategy` names are masked to `seat2`, `seat3`… unless `reveal_opponents` is
  true: you are meant to model what an opponent *might* do, not which published
  bot you are facing.
- **`map`** is the graph and each system's `production` (turns per new ship —
  lower is richer). Neither ever changes, which is why they are here and not in
  every turn.

## `turn` — what moved, and one decision's seed

```json
{
  "type": "turn",
  "turn": 1,
  "you": 1,
  "rng_seed": 17575449127662792549,
  "budget_ms": 15000,
  "systems": [
    {"id": 0, "owner": 0, "ships": 5, "prod_progress": 0},
    {"id": 1, "owner": 1, "ships": 12, "prod_progress": 1}
  ],
  "fleets": [
    {"owner": 2, "src": 5, "dst": 7, "ships": 9, "left": 4, "total": 5}
  ],
  "players": [
    {"id": 1, "alive": true, "ships_lost": 0},
    {"id": 2, "alive": true, "ships_lost": 0}
  ]
}
```

- **`fleets`** are ships in transit — `left` is turns until arrival, `total` the
  length of the trip. Everyone's are visible: the board you get is unfogged,
  which is exactly what the Python bots get. The one above is a rival's nine
  ships, four turns from system 7.
- **`prod_progress`** counts up each turn and emits a ship when it reaches that
  system's `production`, so it tells you when the next one lands.
- **`lane_turns`** appears *only* when `ship_speed_growth_pct` is non-zero: an
  array parallel to `map.lanes`, giving each lane's crossing time on this turn.
  Absent means `base_turns` still stands. Growth applies at launch, so a fleet
  already flying keeps the time it left with.
- **`rng_seed`** is your randomness for this decision — see below.
- Turns resolve **simultaneously**. Every seat decides against this same board
  and nothing is applied until all have, so no opponent's move can depend on
  yours, and the order you list your own orders in never matters.

## Your reply

```json
{"type": "orders", "orders": [{"src": 1, "dst": 3, "ships": 12}]}
```

One order launches ships along one lane, so `dst` must be a neighbour of `src`.
There is no owner field: you command your own ships and nothing else, so it
cannot be expressed. Send `{"type": "orders", "orders": []}` to do nothing.

The engine, not the protocol, has the last word on legality — an order from a
system you don't hold, or to a system that isn't adjacent, is dropped, and
`ships` is clamped to what the source actually has at launch. Several orders may
leave one system; they apply in the order you list them, each deducting as it
goes. Ships leave the source the moment they launch, so a garrison you emptied
is empty until something arrives.

## Randomness, and the one thing you must not do

Use `rng_seed` — seed a generator with it and draw as much as you like. Do not
use your own entropy, the clock, the network or files on disk: a match is
reproducible from its seed, results are cached on the strength of that, and a
bot that isn't a pure function of what it was sent cannot be ranked. Reading the
clock to honour `budget_ms` is the one exception.

`rng_seed` is derived from the match seed, the turn and your seat, so it is
stable across runs and different for every seat and turn.

## Budget

`budget_ms` is how long you have for *this* decision, already scaled for
whichever runner is asking (the batch ladder lifts it far above the in-game
figure). Reply late and that turn's orders are dropped as if you had sent none;
do it three times, or crash, and your seat is played by the built-in heuristic
for the rest of the game and the whole run is flagged — a run with a fallback
seat is never reported as your bot's score, win or lose.

Being slow to *start* is fine and separately budgeted: the handshake gets 10
seconds, so a cold interpreter or a warming JIT is not held against you.

## The manifest

`<name>.bot.json`, beside your bot's folder. `name` is what you pass to `--ai`.

```json
{
  "name": "rusherwire",
  "cmd": ["python3", "main.py"],
  "cwd": "rusherwire",
  "protocol": 1,
  "version": "1.0.0",
  "budget_ms": 150,
  "budget_scale": 100
}
```

`cmd` is run with `cwd` as its working directory (relative to this folder).
`budget_ms` is what you want in a real game — keep it small enough that a
browser tab wouldn't stutter, since that is the standard the Python bots are
held to — and `budget_scale` is your opt-in to having that multiplied for
offline batches, where nothing is waiting. Keep `version` current when you
change the bot: the leaderboard column for external bots isn't built yet, and
this is what will key its cache — it digests `starconquest/` and `models/*.py`
to decide when a cached score is stale, and a compiled binary is in neither.

## Checking it

```sh
uv run python tools/check_bot.py mybot
```

Works on an external bot as well as a Python one: it starts your process, checks
that the orders it sends are legal and that it answers the same way twice, plays
a few games, and prints a paste-ready block for anything wrong. If you drafted
the bot with an AI assistant, [`docs/bot-brief.md`](../docs/bot-brief.md) is the
page to give it — the game and the strategy notes there apply to any language.

## Measuring it

Everything under "Benchmark it before you submit" in
[`models/README.md`](../models/README.md) applies — with `--external` added:

```sh
uv run python -m tests.sim --external --ai mybot heuristic --swap --trials 100
uv run python -m tests.sim --external --ladder --trials 50
```

`--ladder` is the one to trust: every pair head-to-head, both seatings, with a
matchup grid. Watch the timeout count, and watch for a `DEGRADED` line at the
end — that means your bot stopped answering and the numbers above it are not
yours.

Contribute a bot by commit or PR, the same as `models/`.
[`docs/bot-api.md`](../docs/bot-api.md) is the design record behind all of this:
what is built, what is left, and why each field is on the wire.
