# Brief: writing a Star Conquest bot with an AI assistant

**If you are a person:** copy this whole page into a chat with an AI assistant
(Claude, or any capable model) and say *"I want to make a bot for this game."*
It will ask you how you want to play before it writes anything — those answers
are the whole point, so answer them your way rather than the way you think the
game wants. Save what it gives you as `models/<yourname>.py`, then run:

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
  **lower is richer**). That happens *before* fleets land, so a system with
  `prod_progress + 1 >= production` defends with one more ship than it currently
  shows — size an attack against that, not against `ships` alone.
- An order launches ships from one system you own along **one lane** to an
  adjacent system. Ships leave the source immediately and are untouchable in
  transit; they arrive `travel_turns` later and fight whatever is there.
- Combat is a square law with dice: the bigger force usually wins and loses
  proportionally less, a defender may hold a multiplier, and a random jitter
  moves both sides a little. **Ties break to the defender**, and near-matched
  forces annihilate each other and leave the system *neutral*.
- **You win by owning every system.** A rival with no systems and nothing in
  transit is eliminated. There is no other victory condition, no points and no
  draw — which is why a bot that never attacks a rival cannot win at all. One
  that only held, reinforced and took neutrals won **0 of 300** games here.
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
5. **Handle the empty board state.** Nearly every crash here is `min()` or
   `max()` over an empty sequence, or a division by a count that turned out to
   be zero. All of these really occur: a system you own with **0 ships** (you
   emptied it, or spent everything capturing it), an **interior system** whose
   every neighbour is already yours (so the target list is empty), a **rival
   still in `state.players` but eliminated** (`alive=False`, owns nothing, and
   still not `is_neutral`), and **turn 0, where `state.fleets` is empty**. On one
   24-node board the first two happened together by turn 4. `decide` runs every
   turn of a game lasting a hundred turns or more, so a board arising 1% of the
   time is a certainty.

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


## Start by asking

Ask before writing anything. The person you are working for wants *their* bot,
and the roster already has six bots that all think alike (see the next section)
— your job is to get an idea out of them, not to fit them to the template at the
bottom of this page.

1. **Aggression.** Attack as soon as the odds are even, or wait for a
   comfortable margin?
2. **Expansion.** Take neutral systems first, or go straight at the nearest
   rival?
3. **Defence.** Garrison everything, or leave the rear bare and keep it all at
   the front?
4. **A doomed system.** Reinforce it, evacuate its ships to a neighbour, or
   spend them on one last attack?
5. **Target choice.** The weakest neighbour, the richest (lowest `production`),
   or the one that opens the most lanes?
6. **A signature idea** — the important one. Anything they want the bot to be
   *known* for. If they have none, offer some of these, none of which any
   existing bot does:

   - **Solve the whole board at once.** Every bot in the roster decides system
     by system, in isolation. Treat a turn as one pool of ships against every
     target and allocate it globally — an assignment or flow problem rather than
     a loop. (`marshal` needed a whole extra phase to recover leftovers a global
     solve would never have created.)
   - **Play the graph, not the fight.** Aim at articulation points and cut the
     enemy's territory in two; value a system for what it disconnects rather than
     for its garrison.
   - **Play the economy.** `production` is the only long-run resource. Rank
     strictly by output per ship invested, concede ground early, win late. Every
     current bot is military-first.
   - **Know the score.** Every margin in the roster is a worst-case break-even,
     and nothing in it knows whether it is winning. Take coin-flips when behind
     and only certainties when ahead.
   - **Feint.** The built-in bots defend in proportion to the threat they can
     see, so a visible build-up at one front pulls ships away from another. You
     may model what an opponent is *likely* to do; you may not read another
     bot's code.
   - **Bring a trained policy.** Nothing stops weights learned offline from
     being baked into the file as constants — only reading files or the network
     *at decision time* is forbidden.

   Say honestly whether their idea is reachable in one `decide` call that sees
   only the current board: a plan spanning turns has to be re-derived from the
   board each turn, because nothing is remembered between calls.

Then write the bot, keep their answers as named constants at the top, and say in
a line or two what each does — they will want to tune them.

## What the incumbents already do — and where they are blind

Six bots ship with the game, and five of them are the same idea: *walk my
systems, score the neighbours, launch when a margin is cleared.* Three are
literally forks of one another. The measurements below are real and worth
respecting — but they are measurements of **that** frame, and the frame is not
the game.

The evidence is `knower`, the strongest bot here, which searches by trying
several candidate plans and keeping the best. Over 1128 contested decisions, the
winner was its own four-phase plan only 54.8% of the time; the rest came from
other, cruder policies it had borrowed as candidates. Its own docstring: *nearly
a third of real decisions are moves knower's own four phases cannot express.*
A third of the best available moves are outside the frame that every bot here
shares.

So take these as the state of the art to beat, not as instructions:

- **Send the surplus, not the break-even count.** The starter below wins 1 game
  in 10 against the built-in if it launches exactly the price of a capture, and 6
  in 10 if it sends everything spare — the square law barely punishes overkill,
  and a system taken with two survivors is handed straight back.
- **Neutrals and rivals want different margins.** Folding both into one
  split-the-difference number cost 13 percentage points when tried. A neutral
  garrison does not grow and nothing reinforces it; a rival's does both.
- **Most out-matched garrisons run away** — 86.7% of them evacuate rather than
  stand, so a large premium against bad dice on an *attack* buys a fight that
  mostly never happens. Your own garrisons cannot decline, so price *defence* in
  full. This asymmetry was the single biggest measured gain on any bot here.
- **Distances vary hugely between setups.** Node count and fleet speed together
  move a lane from ~1 turn to ~36. Never hardcode a distance or a "far away"
  threshold; read `state.travel_turns` and scale.
- **Stalemate is the failure mode of caution.** Both sides produce
  symmetrically, so a careful bot can hold forever without winning. Around 20%
  unfinished games is normal between evenly matched bots; much more means yours
  is not committing.

## One frame, worked: a starting point

This is a real, tested bot — 57% against the built-in `heuristic` over 200 games,
both seatings. It is the *common* frame, written out plainly, so treat it as
something to depart from once you have the person's answers. If their idea is one
of the six above, most of this file is the wrong shape for it and you should
write theirs instead; if their answers are refinements of aggression, reserve and
target choice, start here and change the constants.

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

## Verify, then measure

```sh
uv run python tools/check_bot.py yourname          # valid? legal? reproducible?
uv run python -m tests.sim --ai yourname heuristic --swap --trials 100
uv run python -m tests.sim --ladder --trials 20    # against the whole roster
```

`check_bot` answers *is it valid*; only the ladder answers *is it good*. Use
`--swap` or `--ladder` rather than a single game: both play every seating, so a
result is not merely a report on which corner of the map is stronger. Beating
`heuristic` is the first bar. The roster above it, weakest to strongest, is
`rusherplus`, `claudebot`, `thinker`, `marshal`, `knower`.

**Read the head-to-head grid, not just the ranking.** `--ladder` prints who beat
whom, and a bot that finishes fifth overall while taking games off `marshal` is a
more interesting result than one that finishes third by playing like everything
above it. If the bot you have written is a genuinely new idea, that grid is where
it will show up first — an overall ranking will hide it for a long time.

If a check fails, `check_bot` prints a ready-made block — take it literally, fix
the bot, run it again.

## Another language?

A bot can instead be any program that reads a JSON board on stdin and writes
JSON orders on stdout, in any language. That guide is
[`bots/README.md`](../bots/README.md), and everything on this page about the game
and about strategy applies unchanged. Those bots compete in the ladder but do not
run inside the app, which cannot start a child process in the browser.
