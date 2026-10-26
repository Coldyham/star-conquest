# Bots from outside the roster

Bots written by players rather than shipped in `models/`: the in-app bot
maker (built, measured, not merged) and bots that aren't Python (the wire
protocol's design and the four rules every external bot keeps). Split out of
[`bots.md`](bots.md); the protocol itself is published as
[`bot-api.md`](../bot-api.md).

## The in-app bot maker: built, measured, not merged

Branch `bot-maker` (PR #20) is a visual IFTTT-style rule builder, so a player who
won't write Python has something between the AI tab's five sliders and a
`models/*.py` file. Both halves work: `botlang.py` is a rule language and
interpreter (a `Program` is a flat ordered list of `WHEN … THEN …` rules over 9
conditions, 7 actions and 5 amounts, run per owned system, first *usable* rule
fires, at most one order per system per turn), and `botmaker.py` is a third
`main.py` scene editing it. It is not merged. What follows is the part worth
keeping.

**The language works, and that was the open question.** The branch's own 400-game
pairwise ladder (`--ladder --trials 20`, 18 nodes, both seatings):

| | vs `heuristic` | vs `rusherplus` | ladder share |
| --- | --- | --- | --- |
| `blockturtle` | 76% | 85% | 27% (1st of 5) |
| `blockrush` | 59% | 21% | 22% |
| `blockheuristic` | 49% | 65% | 16% |

`blockheuristic` re-expresses `ai.compute_orders` and lands at parity with it, so
a flat rule list really is enough to say what the built-in says — unsurprising in
hindsight, since `compute_orders` is structurally a three-rule program. 29/400
timeouts against a ~20% natural stalemate rate for evenly matched bots at this
size.

**And it tops out exactly where the vocabulary says it must: 0% against `thinker`
and `knower`.** One order per system per turn cannot converge waves launched from
different distances so they land together, schedule a reinforcement by when a blow
lands, evacuate a doomed system, or predict a rival. Above `heuristic`,
`rusherplus` and `claudebot`; nowhere near the top of the roster.

**Why it is not merged is the audience, not the ceiling.** The feature's user is
someone interested enough to design bot behaviour but unwilling to write Python —
and `models/README.md` already reduces Python to a 30-line `decide` with a
copy-paste example. That intersection is close to empty, and what it buys the few
who are in it is a bot that loses to half the shipped roster. Against that:
~1,400 lines of core and shell, a third scene, three more dropdown entries, and a
`src`-template-beside-evaluator pairing in every spec-table entry that has to be
kept in step forever. The branch stays unmerged and undeleted; the numbers above
are the reason not to rebuild it from scratch on a hunch.

**Three findings outlive it.**

- **A vocabulary with no enemy-attack rule cannot win a game.** `blockturtle`
  first had hold / reinforce / expand-neutral / send-to-front — a
  complete-looking defensive bot that won **0 of 300** ladder games, because
  taking every enemy system is the win condition and no rule could take one. It
  went to 77% the moment one `attack_best` rule was added. An empty-handed
  program *looks* fine, which is why the editor grew a warning banner
  (`_has_win_path`) rather than a docs note.
- **Blending the two attack margins cost 13 points.** `blockheuristic` first
  folded neutrals and enemies into one `attack_best` at a split-the-difference
  1.4 and scored 35%; giving neutrals `AI_EXPAND_MARGIN` and enemies
  `AI_ATTACK_MARGIN` as separate rules took it to 48%. The heuristic gates the
  two differently for a reason and one blended number is not a substitute.
- **Nothing written to disk survives a web reload.** pygbag 0.9.3 mounts no
  IDBFS and calls no `syncfs`, and the bundle is re-unpacked from the `.apk` each
  load, so anything under `models/`, `saves/`, `games/` or `kv.json` is RAM-backed
  there. Generated Python *runs* fine — `ai.load_models()`'s `exec_module` already
  runs on every web boot — it just cannot be saved. Any future bot-authoring
  feature has to put its source of truth in `localStorage` via `webstore`, and
  that is what killed the branch's persistence phase before it started.

## Bots that aren't Python ([`bot-api.md`](../bot-api.md))

The successor idea, and a better-aimed one: instead of a second authoring
language inside the app, accept a bot in *any* language over a documented wire
protocol. The spec is [`bot-api.md`](../bot-api.md), and the schema, transport, one
ported bot and the parity test are built; this is why it is shaped the way it
is.

**Sell it on the ceiling, not on accessibility.** Writing a JSON-over-stdio loop
plus a payload decoder in Rust or Go is *more* work than editing
`models/mybot.py`, so nobody blocked by the latter is served by the former. What
it genuinely buys is depth: Python caps how far a searcher gets inside a
browser-safe budget, and native code could search well past `knower`. That is a
real prize, and it is a different feature from the one the bot maker was.

**Oracle parity is not required, by ruling.** `knower` reads `ai.STRATEGIES` and
drives `engine.end_turn` on cloned boards, and no out-of-process bot can do either
without an RPC back into the engine — which would roughly double the protocol.
That was initially read as the API's ceiling. It isn't: a tournament entrant is
*supposed* to be ignorant of its opponents' code, so `knower` is a test of the
simultaneous-resolution property rather than a standard to meet. Modelling what an
opponent might do stays fair game and remains a worthwhile thing for an entrant to
add. So: no `simulate` callback, `ai.STRATEGIES` off the wire, and rival strategy
*names* masked by default — knowing which of a published roster you face is
counter-programming, not prediction. Rival `ai_params` stay visible, being
documented-readable tuning rather than identity.

**The payload carries more than the board, and each addition has a reason.**

- **The balance knobs and the two break-even multiples.** An external bot cannot
  call `combat.edge_attacking()`, and pricing a fight off a live figure rather
  than a constant is the one thing every bot in this roster is required to do. The
  raw knobs *and* the derived multiples both go on the wire: re-deriving
  `(1+j)/(1-j)` in a second language is exactly the sort of duplicate that drifts
  with nothing failing.
- **The full setup, not a curated subset.** `nodes` and `ship_ly_per_turn`
  together move lane length over an order of magnitude ([`bots.md`](bots.md), "Lane length across
  the parameter space"), and a posted leaderboard map carries tuned knobs. A
  bot that cannot see which regime it is in cannot tune to it, and would repeat
  the mistake that section documents.
- **A per-turn seed, derived rather than drawn.** External bots cannot draw from
  `state.rng`, so they are handed a seed instead — computed from the game seed,
  the turn and the seat, *not* drawn from the stream. Drawing would make the
  engine's dice depend on which seats happen to be external; deriving keeps a seed
  reproducing the map and every battle, which is what the house rule actually
  protects.
- **A budget cap, sent as a number.** `ai.set_budget_scale`'s 100x lift cannot
  reach another process's clock, so the runner multiplies `budget_ms` by the
  manifest's `budget_scale` and sends the product. The bot never computes it, and
  `budget_ms` is the only clock read the purity contract permits.

**Two mechanical traps found while specifying it.** `bot_replay.engine_rev()`
digests `starconquest/`'s outcome modules and `models/*.py` — a compiled binary is
in neither, so without a manifest `version` on the row a recompiled bot serves its
stale cached score forever. And the failure policy has to invert the house
default: a bad strategy name falling back to `heuristic` silently is right in the
app and wrong in a tournament, where a run containing a fallback seat is not a
result and must be flagged rather than scored.

**Cost, measured.** A 24-node payload is ~5.3 KB and 136 µs to encode. Against a
150 ms budget that is nothing, which settles the schema's style in favour of keyed
objects over positional arrays. Against the *bot* it is not nothing: an entire
`compute_orders` is 9.5 µs at 18 nodes and 12.5 µs at 24, so encoding one payload
costs about eleven of them, and roughly twice what the engine spends resolving a
whole turn for one seat (~58 µs per decide-equivalent, from 3,474 decisions across
ten 2-seat 18-node games averaging 174 turns each). Hence the rule that only
external seats ever touch the wire — routing the Python roster through it would
make every batch measurement in this file slower for nothing.

**Build the schema before the transport, and the parity test before the second
bot.** A pure `botio.py` is testable with no child process in sight, and porting
one roster bot across the wire to demand *identical* orders is the only thing that
will catch a payload quietly missing a field — the same role
`test_export_round_trips_exactly` played on the bot-maker branch, and the same
failure mode: a schema gap looks exactly like a bot that plays slightly worse.

**And mutation-check that test rather than trusting it.** `bots/rusherwire`
matching `models/rusherplus` proves nothing until the comparison is shown to
fail: emptying `fleets` from the payload breaks it, and so does reversing the
order systems are listed in — the second because `rusherplus` tie-breaks with
`state.rng` inside a `min` key, so *ordering* is part of the contract and not
merely presentation. Which is also why the port is Python. Matching the original
exactly means matching its draw sequence, and the test swaps the reference's
`state.rng` for a `Random(rng_seed)` on the payload's own seed to make the two
comparable. A bot in another language cannot reproduce `random.Random` and does
not have to: the test's job is to prove the payload sufficient, not to make
determinism a cross-language requirement.

**Measured, the port plays.** `--external --ladder --trials 4` over
rusherwire/heuristic/marshal: marshal 67%, heuristic 21%, rusherwire 12%, with
rusherwire taking 38% off heuristic head to head and 0% off marshal — which is
roughly where `rusherplus` itself sits, and the point is that the wire changed
nothing about where it sits.

## External bots: the four rules

**Bots that aren't Python are subprocesses, and they compete without shipping.**
`botio.py` is the wire format (`hello` once, `turn_payload` per decision,
`orders_from` back) and `tests/botproc.py` the transport; a bot is a
`bots/<name>.bot.json` manifest naming a command, and `docs/bot-api.md` is the
protocol. Four rules hold it together:
- **`botio` is pure core and owns no process.** No `subprocess` import in the
  package, and none in the ordinary suite either — `tests/sim` imports
  `botproc` lazily, inside `--external`.
- **`bots/` lives outside `models/`, and that is load-bearing.**
  `build_web.sh` stages `models/`, and the web build is CPython on WASM: it
  cannot fork at all. So external bots run in `tests/sim` and (once decided)
  `tools/bot_replay`, never in the app or the browser, and the in-app Strategy
  dropdown stays Python. Registration is opt-in (`sim --external`) rather than
  automatic, unlike `ai.load_models()`, because `bot_replay`'s roster is
  `ai.available_strategies()`.
- **The bot's randomness is derived, never drawn.** `botio.decide_seed(seed,
  turn, pid)` hands a seat its own stream, so an external bot cannot shift the
  engine's dice and every other seat's battles roll as they did without it. It
  is the one sanctioned exception to "all randomness flows through `state.rng`",
  and it keeps what that rule protects: a seed still reproduces every fight.
- **A degraded seat is not a result.** A timeout holds for one turn; a dead bot
  or `botproc.FORFEIT_TIMEOUTS` timeouts falls the seat back to `heuristic` and
  records it (`degraded_runs`, printed by `sim`). The app's silent fallback for
  an unknown strategy name is right there and wrong in a tournament, where a run
  containing a fallback seat must never be scored or posted.
