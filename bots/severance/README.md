# severance

An external bot for Star Conquest, in Rust, speaking the wire protocol in
[`docs/bot-api.md`](../../docs/bot-api.md). It plays the graph rather than the
fight: **a system is worth what its loss costs the rival's territory, not what
its garrison is worth.**

Rust for two reasons, and neither is speed for its own sake. The first is that
the bot answers a connectivity question per candidate per turn — remove this
node from that rival's induced subgraph, how much of their territory falls into
separate pieces — and recomputes all of it from scratch every turn, because an
articulation point is a property of the current ownership and ownership is
exactly what changed. The second is the manifest: it names a compiled binary,
and [`bot-api.md`](../../docs/bot-api.md) leaves *who builds the bot* open, so
this one has **no dependencies at all** — JSON reader, graph routines and
Dijkstra are in `src/`, and `build.sh` works on a fresh machine with no
registry and no network.

## Build and run

```sh
./build.sh                                          # or: cargo build --release
cargo test --release                                # 9 unit tests, no deps

uv run python tools/check_bot.py severance          # legal, read-only, reproducible
uv run python -m tests.sim --external --ai severance marshal --swap --trials 100
uv run python -m tests.sim --external --ladder --trials 25
```

`--external` is required, as it is for any `bots/` entrant. `bots/severance.bot.json`
is the manifest; `budget_ms` is 150 and `budget_scale` 100.

## What it does each turn

1. **Prices the threat as a schedule, not a total.** Inbound fleets are folded
   into per-turn waves and the garrison is checked against them in order, with
   the production and the friendly arrivals that land before each one. A binary
   search returns the fewest ships that must stay; everything above that is
   spare. Summing every inbound fleet into one number instead — which is the
   obvious thing to write — locks a whole garrison against a threat six turns
   away, and the bot stops attacking for the rest of the game.
2. **Values every reachable target.** Its own output, plus what its loss
   *separates* for the rival (weighted fragmentation of their induced subgraph
   with that node removed), plus what it welds together on our side, the lanes
   it opens, and whether it is a staging post aimed at us.
3. **Spends the turn as one pool, not system by system.** Targets are ranked by
   value per ship and bought best-first. Sources whose lanes are the *same
   length* combine on one target, since arrivals on the same turn are resolved
   together — two systems that cannot each afford a target can afford it
   between them. Our own fleets already in the air and landing on that turn are
   counted toward the price.
4. **Dumps the remainder.** A second pass sends every still-spare ship to a
   committed rival target it can land *with*. Never to a neutral: a neutral
   garrison neither grows nor counterattacks, so overkill there is a ship
   parked in the wrong system for four turns.
5. **Moves the rest toward a front that is short of a capture** — a Dijkstra
   flow field over our own systems, weighted in travel turns, seeded on the
   fronts that are saving toward something. A doomed system evacuates instead.

Everything tunable is a named constant at the top of `src/main.rs`.

## Pricing

The runner sends `edge_attacking`/`edge_defending` and the two knobs behind
them. Dividing the advantage out of `edge_attacking` gives the *dice* half of
the edge, which is floored at `TUNED_DICE_EDGE` — the value it had at the 0.10
jitter this bot was fitted at — so a host lowering the jitter can never lower a
margin below what was measured. Nothing is hardcoded that the host can move.

Attacking a rival pays the ground and **not** the dice: most out-matched
garrisons evacuate rather than stand, so a premium against bad dice buys a
fight that mostly does not happen. Defence pays the dice in full, because our
own garrisons cannot decline. Neutrals get their own number again — they always
stand, and they never grow.

## Determinism and purity

The only inputs are the handshake and the turn payload: no clock, no files, no
network, no environment. Ties among equally-ranked targets break on a SplitMix64
hash of the payload's own `rng_seed`, which is derived rather than drawn, so an
external seat never shifts the engine's dice. `check_bot.py` confirms
reproducibility.

`cargo build --features trace` adds a per-turn decision dump on stderr, which
the runner discards. It is off by default and reads nothing.

## Measured

`--ladder --trials 25` (1400 games, every pair, both seatings), with
`rusherwire` and `severance` registered via `--external`:

| | knower | marshal | thinker | claudebot | heuristic | rusherplus | rusherwire |
| --- | --- | --- | --- | --- | --- | --- | --- |
| severance | 16% | 26% | 52% | 98% | 100% | 98% | 90% |

Third of eight overall (18% of all wins, behind knower and marshal at 22%), and
it takes the head-to-head off `thinker`. No bot timeouts in any run at the
150 ms budget.

## What was tried and did not work

Kept here because the numbers are the only reason to believe any of the rest.
All against `marshal`, `--swap`, 200 games, where ±6pp is noise.

- **Reinforcing a doomed system** (pull ships from neighbours that can beat the
  wave to it): 22% → 13%. It feeds a fight that is already lost, exactly as
  bot-brief's "garrisons run away" measurement predicts. The evacuation stands.
- **Topping up a front that is a few ships short** from its neighbours:
  22% → 18%. It drains the systems that were about to buy their own targets.
- **Marching everything to one or two rally points** instead of letting each
  front save: 30% → 18%. A front already close to its target should not walk
  away from it.
- **A timed rendezvous** — the far system launching on spec so the near ones can
  land with it later: 22% → 20–21%. Neutral at best; removed. The half that
  survives is counting our own in-flight fleets toward a target's price, which
  makes the same thing assemble on its own when it is genuinely worth it.
- **The cut term itself is honestly neutral so far.** It fires about 2.5 times a
  turn with a weight comparable to the economy term, and it does reorder
  targets — but `SEVER_WEIGHT` at 0, 6, 15 and 24 all measure the same, against
  marshal, against thinker, and in a three-way. The bot is ship-constrained
  rather than choice-constrained: most turns it can afford one target or none,
  and a value function cannot rank a list of one. Making the graph pay is the
  open problem, not making it work.

## Known gaps

- `in_lane_battles` is read and ignored: the bot does not route around an enemy
  fleet it would cross. It is off by default.
- A third party attacking a target we are pricing is not modelled; the garrison
  is over-estimated instead, which costs ships rather than systems.
- The rally is per-front. Genuine multi-front concentration is unsolved — every
  version measured so far has been worse than leaving each front alone.
