# Brief: writing a Star Conquest bot with an AI assistant

**If you are a person:** copy this whole page into a chat with an AI assistant
(Claude, or any capable model) and say *"I want to make a bot for this game."*
It will ask you a few questions about how you want to play, then write you a bot.
Save what it gives you as `models/<yourname>.py`, then run:

```sh
uv run python tools/check_bot.py yourname
```

That prints either `valid` plus a strength score, or a block of text to paste
straight back into the chat. Repeat until it passes. You do not need to
understand the code, but you do need to run that one command — it catches the
mistakes the game itself will not tell you about.

Everything below is written for the assistant.

---

## The game

A graph of star systems joined by spacelanes. You own some, rivals own others,
the rest are neutral (owner `0` — neutral is a real player, not an absence).

- Each system slowly builds ships: `prod_progress` counts up every turn and
  emits one ship when it reaches that system's `production` (turns per ship, so
  **lower is richer**).
- An order launches ships from one system you own along **one lane** to an
  adjacent system. Ships leave the source immediately and are untouchable in
  transit; they arrive `travel_turns` later and fight whatever is there.
- Combat is a square law with dice: the bigger force usually wins and loses
  proportionally less, a defender may hold a multiplier, and a random jitter
  moves both sides a little. **Ties break to the defender**, and near-matched
  forces annihilate each other and leave the system *neutral*.
- **You win by owning every system.** A rival with no systems and nothing in
  transit is eliminated.
- Turns resolve **simultaneously**. Every seat decides against the same
  unchanged board and nothing is applied until all have decided, so no opponent
  can react to your move this turn, and the order you list your own orders in
  never matters.

## Your job

Write `models/<name>.py` containing one function:

```python
from starconquest.model import Order

def decide(state, pid) -> list[Order]:
    """Return this turn's orders for player `pid`. Empty list is fine."""
```

`Order(owner_id, source_id, dest_id, ships)` — positional. The file is
discovered by filename, so `models/vulture.py` becomes the strategy `vulture` in
the game's AI tab. A leading underscore in the filename means it is skipped.

## Hard rules

These five are exactly what `tools/check_bot.py` verifies, because the game is
silent about all of them:

1. **Only legal orders.** `owner_id` must be `pid`, the source must be a system
   you currently own, and the destination must be one of its `neighbors` — one
   order crosses one lane, there is no multi-hop routing. An illegal order is
   **discarded without a word**, so a bot can spend a whole match issuing orders
   that never happen and merely look weak.
2. **Never modify `state`.** It is read-only. Return orders and let the engine
   apply them. Editing it corrupts the match and its replay.
3. **Randomness only from `state.rng`.** It is a seeded `random.Random` with all
   the usual methods (`state.rng.random()`, `state.rng.choice(...)`). Never use
   the `random` module, the clock, files or the network: a seed has to reproduce
   an entire match, and results are cached on that basis.
4. **Don't ask for ships you don't have.** An over-large count is clamped to the
   garrison at launch, so it "works" but not as you intended. Several orders may
   leave one system; they apply in listed order, each deducting as it goes.
5. **Handle the awkward board.** A system with no ships, no neighbours you can
   attack, a rival already eliminated, turn 0 with nothing in transit. The bot is
   asked for a decision every turn of a game that runs to a hundred turns or
   more.

## What you can read

| Access | Meaning |
| --- | --- |
| `state.systems` | `dict[int, System]`, keyed by id |
| `state.systems_of(pid)` | the systems `pid` owns |
| `state.players` | `dict[int, Player]`; `0` is neutral |
| `state.fleets` | every fleet in transit, yours and theirs |
| `state.fleets_incoming(sid)` | fleets heading to one system |
| `state.are_adjacent(a, b)` | is there a lane |
| `state.travel_turns(a, b)` | turns to cross it *if launched now*, else `None` |
| `state.turn`, `state.winner` | turn counter, winner id or `None` |
| `state.rng` | the seeded generator — the only randomness you may use |

`System`: `id`, `owner_id`, `ships`, `production`, `prod_progress`, `neighbors`,
`pos`, `name`. `Player`: `id`, `alive`, `ships_lost`, `ai_strategy`, `ai_params`.
`Fleet`: `owner_id`, `source_id`, `dest_id`, `ships`, `turns_remaining`,
`turns_total`.

Two helpers worth using rather than reinventing:

- `combat.edge_attacking()` — what an attack must beat a garrison by to win even
  the *worst* dice roll. `combat.edge_defending()` — what a garrison must beat an
  incoming force by to survive one. Both read the live settings, which the host
  can change, so **never hardcode a multiplier**. Clearing the edge is not a
  promise of capture (ties break to the defender, matched forces annihilate), so
  floor every ask at `target.ships + 1`.
- `model.flow_field(state, allowed, seeds)` — next hop from every node toward the
  nearest seed, expanding only through `allowed`. Pass your own systems as
  `allowed` and your front line as `seeds` to stream rear ships forward.

Never call `ai.load_models()` from inside a bot, and read `ai.STRATEGIES` (if at
all) inside `decide` rather than at import time — files load in sorted order, so
the registry is incomplete while yours is importing.

## A working starting point

This is a real, tested bot: **57% against the built-in `heuristic` over 200
games, both seatings**. Modify it rather than starting from nothing, and treat
the three constants as the first things to tune.

```python
"""Starter bot: hold what is threatened, take what is affordable."""

from starconquest import combat
from starconquest.model import Order, flow_field

AGGRESSION = 1.15      # how far above break-even before committing to a fight
RESERVE = 2            # ships always left behind to hold a system
EXPAND_FIRST = True    # neutrals before rivals when both are adjacent


def _incoming(state, sid, pid):
    hostile = sum(f.ships for f in state.fleets_incoming(sid) if f.owner_id != pid)
    friendly = sum(f.ships for f in state.fleets_incoming(sid) if f.owner_id == pid)
    return hostile, friendly


def decide(state, pid):
    orders = []
    attack_edge = combat.edge_attacking() * AGGRESSION
    owned = {s.id for s in state.systems_of(pid)}
    front = {sid for sid in owned
             if any(state.systems[n].owner_id != pid for n in state.systems[sid].neighbors)}
    toward_front = flow_field(state, owned, front)
    for sys in state.systems_of(pid):
        hostile, friendly = _incoming(state, sys.id, pid)
        if hostile > 0 and sys.ships + friendly < hostile * combat.edge_defending():
            continue          # this system is probably lost: don't feed it
        spare = sys.ships - RESERVE - max(0, hostile - friendly)
        if spare <= 0:
            continue
        targets = [state.systems[n] for n in sys.neighbors
                   if state.systems[n].owner_id != pid]
        if not targets:
            # Nothing to attack from here: push the surplus toward the fighting.
            hop = toward_front.get(sys.id)
            if hop is not None:
                orders.append(Order(pid, sys.id, hop, spare))
            continue
        neutral = [t for t in targets if t.owner_id == 0]
        enemy = [t for t in targets if t.owner_id != 0]
        ranked = (neutral + enemy) if EXPAND_FIRST else (enemy + neutral)
        for target in sorted(ranked, key=lambda t: (t.ships, state.rng.random())):
            need = max(target.ships + 1, int(target.ships * attack_edge) + 1)
            if spare >= need:
                # Everything spare, not just the break-even count: the square law
                # barely punishes overkill on a weak garrison, and a system taken
                # with two survivors is a system handed straight back.
                orders.append(Order(pid, sys.id, target.id, spare))
                break
    return orders
```

## What measurably works

Every figure here was measured in this repo, most of it the hard way. Full
detail and method in [`bot-design.md`](bot-design.md).

- **A bot that never attacks a rival cannot win.** An earlier experiment with
  hold / reinforce / take-neutrals / send-to-the-front rules — a
  complete-looking defensive bot — won **0 of 300** games, because taking every
  enemy system is the win condition. It reached 77% the moment one attack rule
  was added. If the person you are working for describes a purely defensive bot,
  build it *and* tell them this.
- **Send the surplus, not the break-even count.** Removing that one line from the
  starter above takes it from 6/10 against the heuristic to **0/10**: it still
  captures systems, then loses them straight back. The square law barely punishes
  overkill on a weak garrison, so concentration is cheap and hesitation is not.
- **Neutrals and rivals deserve different margins.** Folding both into one
  split-the-difference number cost 13 percentage points when it was tried. A
  neutral garrison does not grow (unless `neutral_produces` is on) and nothing
  reinforces it; a rival's does both.
- **Most out-matched garrisons run away.** 86.7% of them evacuate rather than
  stand, so paying a large premium against bad dice on an *attack* buys a fight
  that mostly never happens — that premium's removal was the single biggest
  measured gain on the strongest hand-written bot in the roster. Your own
  garrisons cannot decline a fight, so price *defence* in full.
- **Distances vary hugely between setups.** Node count and fleet speed together
  move a lane from ~1 turn to ~36. Never hardcode a distance or a
  "far away" threshold; read `state.travel_turns` and scale.
- **Watch out for stalemate.** Both sides produce symmetrically, so a cautious
  bot can hold forever without winning. Around 20% unfinished games is normal for
  two evenly matched bots; much more than that means yours is not committing.

## Ask first

Before writing anything, ask the person about four or five of these — the
answers are what make their bot theirs rather than a copy of the starter:

1. **Aggression.** Attack as soon as the odds are even, or wait for a
   comfortable margin? (Sets `AGGRESSION`.)
2. **Expansion.** Grab neutral systems first, or go straight at the nearest
   rival? (Sets `EXPAND_FIRST`.)
3. **Defence.** Garrison every system, or leave the rear bare and keep
   everything at the front? (Sets `RESERVE`, and whether rear systems forward.)
4. **A doomed system.** Reinforce it, evacuate its ships to a neighbour, or
   spend them on one last attack?
5. **Target choice.** The weakest neighbour, the richest (lowest `production`),
   or the one that opens the most lanes?
6. **A signature idea.** Anything they want the bot to be *known* for — feint at
   one front while massing on another, always take the map's centre, never fight
   two rivals at once. Say honestly whether it is reachable in one `decide` call
   that sees only the current board.

Then write the bot, keep their choices as named constants at the top, and say in
one or two lines what each does — they will want to tune them.

## Verify, then measure

```sh
uv run python tools/check_bot.py yourname          # valid? legal? reproducible?
uv run python -m tests.sim --ai yourname heuristic --swap --trials 100
uv run python -m tests.sim --ladder --trials 20    # against the whole roster
```

`check_bot` answers *is it valid*; only the ladder answers *is it good*. Use
`--swap` or `--ladder` rather than a single game: both play every seating, so a
result is not just a report on which corner of the map is stronger. Beating
`heuristic` is the first bar. The roster above it, weakest to strongest, is
`rusherplus`, `claudebot`, `thinker`, `marshal`, `knower`.

If a check fails, `check_bot` prints a ready-made block — take it literally, fix
the bot, run it again.

## Another language?

A bot can instead be any program that reads a JSON board on stdin and writes
JSON orders on stdout, in any language. That guide is
[`bots/README.md`](../bots/README.md), and everything on this page about the game
and about strategy applies unchanged. Those bots compete in the ladder but do not
run inside the app, which cannot start a child process in the browser.
