# The external bot API

A wire protocol so a bot can be written in any language, judged in the ladder,
and never need to be Python. Why it takes this shape — and why the in-app
rule-builder it replaces was not merged — is in
[`bot-design.md`](bot-design.md) under "Bots that aren't Python".

**Built:** the schema (`starconquest/botio.py`, pure core), the transport
(`tests/botproc.py`), a worked example (`bots/rusherwire`, a port of
`models/rusherplus.py`), and the parity test that keeps the two honest
(`tests/test_botio.py`). `uv run python -m tests.sim --external --ladder` runs
external bots in either tournament.

**Not built:** the leaderboard column (see "What is left", below). The in-app
Strategy dropdown stays Python — [`models/README.md`](../models/README.md) is
that contract, and nothing here changes it.

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

`name` is the strategy name, as a filename stem is for a drop-in model. `cwd` is
relative to the manifest, and a manifest that will not parse is skipped with a
warning rather than being fatal — the same tolerance `ai.load_models()` shows a
drop-in file that fails to import.
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
log — discarded by default, never parsed. Process-per-`decide` is out; a 2-seat
18-node game averages ~174 turns and ten of them ask for ~3,500 decisions.

A process is keyed on `(seed, seat)` and re-handshaken when the turn counter goes
backwards, which is how a ladder playing game after game in one runner — and
checking each seating both ways round on the same seed — gets a fresh bot per
game without the runner having to announce game boundaries. Replies are read on a
thread rather than with `select`, because a pipe is not selectable on Windows
(`tests/sim` already carries one such scar).

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

`botio.orders_from` validates the **shape** and nothing else: non-integer ids or
counts are dropped rather than coerced, so a malformed reply cannot reach the
engine as something subtly wrong. Whether a source is held, a destination
adjacent, or a count affordable is left to `engine.apply_order`, which decides it
for every seat alike — an unknown or unheld source and a non-adjacent destination
are dropped there, and `ships` is clamped to the garrison at launch. A second
copy of those rules on this side would be a second thing to keep in step for no
change in outcome. Several orders may leave one system; they apply in the order
listed, each deducting as it goes.

## When a bot misbehaves

The house rule is that nothing crashes and a bad strategy name falls back to
`heuristic`. A tournament needs the same tolerance and the opposite silence,
since a result computed with a fallback seat is not a result:

- **Malformed reply, or none inside `budget_ms`** — that turn's orders are empty
  and the seat holds.
- **Process dead, or `botproc.FORFEIT_TIMEOUTS` (3) timeouts** — the seat falls
  back to `heuristic` for the remainder, and the run is flagged: `botproc.
  degraded_runs()` collects it and `sim` prints it at exit. A degraded run is
  never scored as that bot's and never posted to `bot_scores`.
- **A slow start is not a slow bot** — the handshake gets its own
  `botproc.HANDSHAKE_MS` (10 s), since a cold interpreter or a warming JIT is not
  the bot being slow at deciding.

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

## The parity test

`bots/rusherwire` is `models/rusherplus.py` with one thing changed: it reads the
payload instead of a `GameState`, importing nothing from `starconquest`.
`test_wire_bot_matches_in_process_original` asks both, on the same board and
turn, for the same seat, and demands **identical** orders — because a payload
quietly missing a field does not look like a bug, it looks like a bot that plays
slightly worse. It is the same job `test_export_round_trips_exactly` did on the
bot-maker branch, and the same reason: two implementations of one algorithm are
only trustworthy while something compares them.

Tie-breaks are what make that possible. `rusherplus` draws from `state.rng`
inside a `min` key, so the test swaps the reference's `state.rng` for a
`Random(rng_seed)` on the seed the payload carries — the two then draw the same
stream in the same order, and a port that consults its randomness differently
fails. The port is Python for exactly this reason; a bot in another language
cannot reproduce `random.Random` and does not need to. The test proves the
payload is *sufficient*, not that determinism crosses languages.

It was mutation-checked rather than assumed: emptying `fleets` from the payload,
and reversing the order systems are listed in, each fail it. Both are changes a
plausible refactor could make, and neither is visible any other way.

## What is left

**The leaderboard column.** `bot_replay` last, and only once `version` is settled
against `engine_rev` per the manifest note above. `bot_scores` also has no column
for "this run was degraded", which it would need before an external bot could
post at all.

**Registration is opt-in**, which is a deliberate departure from the original
plan of "`--ladder` needs no new arguments": `--external` is required. A default
ladder silently spawning child processes is a surprise, and `bot_replay` takes
its roster from `ai.available_strategies()` — without the flag it would have
started posting external bots to the leaderboard the moment one appeared in
`bots/`.

**Nothing enforces purity.** A bot that reads the clock, the network or its own
files is unreproducible and uncacheable, and today that is a contract rather
than a check.

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
