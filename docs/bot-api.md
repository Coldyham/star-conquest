# The external bot API (not built)

A wire protocol so a bot can be written in any language, judged in the ladder and
on the leaderboard, and never need to be Python. This file is the design: where
such a bot runs, what it is sent, what it may return, and the order to build it
in. Why it takes this shape — and why the in-app rule-builder it replaces was not
merged — is in [`bot-design.md`](bot-design.md) under "Bots that aren't Python".

Nothing here is implemented. The authoring contract that *is* live is
[`models/README.md`](../models/README.md): a Python file with a `decide`.

## Where an external bot runs, and where it cannot

**It runs in the tournament, not in the app.** `tests/sim` (`--ladder`, `--swap`)
and `tools/bot_replay.py` are the two entry points; the game itself keeps a
Python-only roster.

That is not a simplification, it is the constraint. `tools/build_web.sh` stages
`starconquest/` **and `models/`** into the bundle, so the deployed site's Strategy
dropdown *is* `models/` — and that build is CPython compiled to WASM, single
threaded, with no `fork`/`exec`. A child process cannot exist there. Nor can one
be assumed in the leaderboard Action unless the workflow installs the bot's
runtime, which is a per-bot cost the workflow has to opt into.

A competitive bot does not need to be in the dropdown. It needs to be in the
ladder and in the `bot_scores` column, both of which run on a real machine. So
`bots/` (manifests, below) sits outside `models/`, which means `build_web.sh`
excludes it for free and the browser build never learns it exists.

## Manifest

One JSON file per bot, `bots/<name>.bot.json`, committed like `models/`:

```json
{
  "name": "ferrous",
  "cmd": ["./target/release/ferrous"],
  "cwd": "bots/ferrous",
  "protocol": 1,
  "version": "0.3.1",
  "budget_ms": 150,
  "budget_scale": 100
}
```

`name` is the strategy name, as a filename stem is for a drop-in model.
`budget_ms` is the in-game figure the bot wants — the same thing a model's own
`SEARCH_BUDGET_S` is — and `budget_scale` is its opt-in to having that lifted by
a batch runner, exactly as a module-level `BUDGET_SCALE` is (see
`ai.set_budget_scale`, and bot-design's "Replaying a bot for the leaderboard").
The runner sends the *product*; the bot never computes it.

`version` is load-bearing and not decoration. `bot_replay.engine_rev()` digests
`starconquest/`'s outcome modules plus `models/*.py`, and a compiled binary is in
neither — so without a version on the row, recompiling a bot serves its stale
cached score forever. Either `version` joins the digest or it is stored on the
row and compared like `aux` is.

## Transport

One long-lived child process per seat per run, JSON per line: the runner writes a
message to stdin, reads exactly one reply line from stdout. stderr is the bot's
log — captured, never parsed. Process-per-`decide` is out; a 2-seat 18-node game
averages ~174 turns and ten of them ask for ~3,500 decisions.

## Handshake

Sent once, before turn 0:

```json
{"type": "hello", "protocol": 1, "you": 1, "seats": [1, 2, 3],
 "setup": {...}, "rules": {...}, "map": {...},
 "budget_ms": 15000, "reveal_opponents": false}
```

The bot replies `{"type": "ready", "name": "ferrous", "version": "0.3.1"}` and is
sent nothing further until turn 0.

**`setup`** is the whole `Settings` dict (`Settings.to_dict()`) less `challenge`
and `autoplay`, which are presentation and play-style rather than setup. Sending
all of it rather than a curated subset is deliberate: a posted leaderboard map
carries tuned knobs, `nodes` and `ship_ly_per_turn` together move lane length
over an order of magnitude (bot-design, "Lane length across the parameter
space"), and a bot that cannot see which regime it is in cannot price anything.

**`rules`** is the derived arithmetic a Python bot reads live off `config` and
`combat`, computed once and handed over:

| Field | Source |
| --- | --- |
| `combat_jitter`, `defender_advantage` | `config`, post-`_apply_globals` |
| `swing` | `(1+j)/(1-j)`, the worst roll |
| `edge_attacking`, `edge_defending` | `combat.edge_attacking()` / `edge_defending()` at `min_swing=1.0` |
| `in_lane_battles`, `neutral_produces` | `config` |
| `ship_ly_per_turn`, `ship_speed_growth_pct` | `config` |

The knobs and the two break-even multiples derived from them are both sent, and
the redundancy is the point. Pricing a fight off a live figure rather than a
constant is the one thing `CLAUDE.md` insists every bot does, and re-deriving
`(1+j)/(1-j)` in a second language is precisely the kind of duplicate that drifts
without anything failing. Floor the jitter half at the swing you tuned at, as the
roster's bots do with `TUNED_SWING` — the runner cannot do it for you, since it
does not know what you fitted.

**`map`** is everything static for the match: `systems` as `id`, `pos`,
`production`, `neighbors`, and `lanes` in a fixed index order as `a`, `b`,
`length_ly`, `base_turns`. Production is stamped at generation and never written
again, and the graph never changes, so neither belongs in a per-turn message.

## Per turn

```json
{"type": "turn", "turn": 42, "rng_seed": 8134…, "budget_ms": 15000,
 "systems": [{"id": 3, "owner": 1, "ships": 12, "prod_progress": 2}, …],
 "fleets":  [{"owner": 2, "src": 7, "dst": 3, "ships": 9, "left": 2, "total": 4}, …],
 "players": [{"id": 1, "alive": true, "ships_lost": 31}, …],
 "lane_turns": [2, 1, 3, …]}
```

`lane_turns` is in the handshake's lane index order and is sent **only** when
`ship_speed_growth_pct` is non-zero; absent means the handshake's `base_turns`
still stand. Travel time is a query rather than a stored value
(`config.travel_turns_at`), and this is how that survives the wire.

The board is sent **unfogged**, which is what a Python bot gets: `fog` is
presentation-only and neither the engine nor `ai` consults it. If fog ever
reaches bots, it has to reach both lanes on the same turn or the ladder stops
comparing like with like.

Keys, not positional arrays. Measured, a 24-node payload is ~5.3 KB and 136 µs to
encode against a 150 ms budget, so nothing here is worth trading legibility for —
but never route the *Python* bots through it: encoding one payload costs an order
of magnitude more than the built-in heuristic's entire decision (`compute_orders`
is 9.5 µs at 18 nodes, 12.5 µs at 24), and roughly twice what the engine spends
resolving a whole turn per seat.

## Reply

```json
{"type": "orders", "orders": [{"src": 3, "dst": 7, "ships": 12}]}
```

No owner field. A seat commands its own ships and nothing else
(`engine._own_orders`); the runner stamps `pid` itself, so a foreign order is not
something the protocol can express rather than something it filters.

Validation mirrors `engine.apply_order` exactly, because that is what will see
these: an unknown or unheld source, a non-adjacent destination, or a non-positive
count is dropped silently, and `ships` is clamped to the garrison at launch.
Several orders may leave one system; they are applied in the order listed, each
deducting as it goes.

## When a bot misbehaves

The house rule is that nothing crashes and a bad strategy name falls back to
`heuristic`. A tournament needs the same tolerance and the opposite silence,
since a result computed with a fallback seat is not a result:

- **Malformed reply, or none inside `budget_ms`** — that turn's orders are empty
  and the seat holds. Recorded on the run.
- **Process dead, or a third timeout** — the seat falls back to `heuristic` for
  the remainder and the run is flagged `degraded`. A degraded run is reported and
  never posted to `bot_scores`.

An overrun count belongs on the leaderboard row for the same reason
`bot_replay` stores the `aux` in force: it is the one thing that makes the
answer depend on the machine.

## Determinism

`rng_seed` is a fresh 64-bit integer each turn, and it is **not drawn from
`state.rng`**: it is a stable function of the game seed, the turn and the seat
(`random.Random(f"{seed}:{turn}:{pid}").getrandbits(64)` — `Random` seeded with a
string goes through SHA-512 and is reproducible across runs and platforms).
Deriving it instead of drawing it means an external seat does not shift the
engine's dice stream, so swapping a bot in or out leaves every other seat's
combat rolls where they were. A seed still reproduces the map and every battle,
which is what the house rule protects.

The contract on the bot's side follows from that: **be a pure function of the
payload.** The clock is readable only to honour `budget_ms`; the environment,
the filesystem and the network are not. A bot that fails this cannot be cached
by `bot_scores` and cannot be ranked.

## Opponent information (`reveal_opponents`)

Entrants are ignorant of each other's code by design, so the protocol never
offers a way to run a rival's `decide` — no `simulate` call back into the engine,
and `ai.STRATEGIES` is not on the wire. Predicting what an opponent *might* do is
fair game and a bot is welcome to model the rules itself; reading what one
actually did this turn is not on offer to anyone, since turns resolve
simultaneously (bot-design, "`models/knower.py` and simultaneous resolution").

That leaves rival identity, which is a policy switch rather than a fact:

- `reveal_opponents: false` (**tournament default**) — rival `ai_strategy` names
  are replaced with opaque, per-game-stable labels (`"seat2"`). Knowing the
  roster and which of it you are facing is counter-programming, not prediction.
- `reveal_opponents: true` (local development) — names as they are, so a Python
  bot ported across the wire sees exactly what it saw in-process. The parity test
  below needs this.

Rival `ai_params` are sent either way. They are documented as readable on every
seat, and `aux` in particular is meant to distinguish a shallow opponent from a
deep one — that is tuning, not identity.

## Build order

1. **`starconquest/botio.py`** — pure core, no pygame and no subprocess:
   `hello(settings, state, pid, budget_ms, reveal)`, `turn_payload(state, pid,
   rng_seed, budget_ms)`, and an `orders_from(reply, pid, state)` validator that
   mirrors `apply_order`'s rules. Pure means the schema is testable without a
   child process anywhere near it.
2. **The parity test, before the second bot exists.** Port one roster bot
   (`rusherplus` — real arithmetic, small) to read the payload, run it as a
   subprocess, and demand orders identical to the in-process original over
   several turns of several games with `reveal_opponents: true`. The bot-maker
   branch's equivalent (`test_export_round_trips_exactly`, interpreter against
   generated module) is the only reason its two halves could be trusted to mean
   the same thing, and a schema quietly missing a field looks exactly like a bot
   that plays slightly worse.
3. **The adapter** — manifest discovery plus process management, living beside
   `tests/sim` rather than in `models/`, registering each manifest through
   `ai.register` so `--ladder` and `--swap` need no new arguments and no new
   roster plumbing.
4. **The leaderboard column** — `bot_replay` last, and only once `version` is
   settled against `engine_rev` per the manifest note above.

## Open questions

- **Who builds the bot?** A manifest naming a compiled binary is not
  reproducible on a fresh CI runner. A `build` command in the manifest, a
  container per bot, or committed binaries are the three answers, and none of
  them is free.
- **What is the ladder's baseline?** `heuristic` is the historical yardstick, but
  an entrant beating it says little now that marshal exists. A published ladder
  needs a fixed reference roster and a stated trial count, or scores from
  different weeks are not comparable.
- **Does anything get to be slow?** `budget_scale` lifts a guard 100x for a batch
  today because the answer stays reproducible. A native searcher with no guard at
  all is a different proposition for a runner that has to finish.
