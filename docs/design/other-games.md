# Features from other games (proposed, not built)

A survey made on 2026-10-27 of what similar games have that Star Conquest does
not: browser daily games, Risk, Warzone (now war.app), graph-based space games
(Galcon, Neptune's Pride) and board games (Twilight Imperium). The daily
challenge has its own file ([`daily.md`](daily.md)). Nothing here is built or
measured; each idea has a line in [`open.md`](open.md).

The constraint throughout is the simple skeleton the headless sim and the
roster depend on. So ideas come in two kinds:

- **Layers**, which sit beside the engine and change no rule, as the
  leaderboard, campaign and gauntlets do.
- **Mechanics**, which change the rules. Each would be a `Settings` field off
  by default. With the default, existing outcomes don't move, so neither does
  `RULES_VERSION`, but every new field needs a `settings._LEGACY_KEY_DROPS`
  entry. The real cost of a mechanic is the roster: a bot that ignores it
  plays it badly, and the ladder and every bot's design note were measured
  without it.

## Layers

### Star tiers on every map

Warzone single-player and most puzzle games grade a win with one to three
stars. Bronze, silver and gold turn targets could be taken from numbers the
board already has: the bot column's wins on that map, or a dealt run's probe
wins. This is the stage-target machinery (`gauntlet.md`, "Stage targets")
shown on any posted map rather than gating a gauntlet. `par.md`, "Not on the
board", kept a *computed* par off the board, so take the tiers from scores
that exist and say which bot set each one.

### A rating against the bot ladder

Warzone and Galcon rank players, but here two people never meet in a scored
game: play-by-post never reaches the leaderboard. Scores against bots of known
strength could stand in. A win against marshal on a 4-seat map says more than
one against rusherplus, so each posted score is a result against a rated
field. The rating would be computed by the offline worker, like the bot
column, and served as a view. Monthly seasons would reset it. Crowns reward
records; a rating would reward playing.

### Racing the record holder's ghost

Replays are uploaded with scores, so a map could show the record holder's
systems-held curve, turn by turn, beside yours in the existing graphs. This is
the time-trial ghost of Trackmania or Mario Kart, made turn-based. The ghost's
curve would be read from the stored log by reconstruction, never by
re-deciding. The curve must not show the record holder's moves, so it needs no
spoiler handling beyond that.

### Puzzles from real positions

Chess-style "win in N turns from here". A hand-drawn map can already start
systems with mixed owners and garrisons, and `tools/position_suite.py` already
finds real mid-game positions. The gap is that fleets in flight are not
representable in a `CustomMap`. Either pick positions with nothing in flight,
or extend the recipe (a hand-maps change, under `hand-maps.md`'s rules).

### Play-by-post: chat, rematch, tournaments, groups

Warzone and Neptune's Pride live on their social layer.

- **Chat per match**: a `pbp_messages` table behind the seat token, the same
  thin-server pattern as orders.
- **Rematch**: a new match with the same roster and setup and a new seed.
- **Tournaments**: a bracket or round-robin over play-by-post matches, results
  read from the stored logs.
- **Groups or clans**: a name on the roster.

None of this resolves a turn or reaches the leaderboard. Chat is new public
text, so it needs moderation in `tools/admin.py`.

### A map gallery

Warzone's community map library is its main draw. Hand-drawn maps already
travel in challenge links and post to the board. A gallery would list those
setups with their plays, records and a like count. Likes are the one new
stored thing.

## Mechanics

### Bonus regions

Risk's continents and Warzone's bonuses. A named group of systems that pays
extra income to whoever holds all of it. This gives the map goals and
chokepoints without a new unit. It is the most valuable mechanic here. Build
it on hand-drawn maps first, where an author draws the groups, before teaching
`mapgen` to make them. Production runs before combat, so the rule is "held at
the start of production". Every bot's valuation of a system would need the
region term.

### Win conditions other than total conquest

The mop-up at the end of a decided conquest game is the dullest part.
`win-turns.md` measures how slowly even the top bots finish.

- **A turn limit with points**: Twilight Imperium's victory points, and
  Neptune's Pride's "hold half the stars". The leader at the limit wins.
- **Capitals**: lose your homeworld and you are out (Warzone's commanders).
- **King of the hill**: hold a central system for N turns.

Each changes the win check, the scoring ("turns to win" stops being the whole
score) and every bot's objective.

### Terrain

Twilight Imperium's anomalies and wormholes.

- **Fortress systems**: their own defender advantage.
- **Nebula lanes**: slower.
- **Impassable fields.**
- **Wormholes**: a lane whose travel time ignores its length.

All are easiest on hand-drawn maps. Wormholes clash with travel time being a
query of geometry (`state.travel_turns`) and must be a query too, never a baked
`Lane.travel_turns`.

### An invest order

Neptune's Pride's economy and Galcon's upgrades. Spend ships at a system to
speed its production. This adds a choice between economy and army while
keeping one ship type. It is a new `Order` kind, so it changes the wire
protocol (`docs/bot-api.md`, `botio`) as well as the engine and every bot.

### Teams and co-op

Warzone's team games, and two people against two bots. The win check would go
by team, fog would be shared between allies, and orders onto an ally's system
would need a rule. Bots would need to leave allies alone, which is a change
to every bot's target selection. It fits play-by-post best.

### Per-seat handicaps

Go's handicap stones. Extra starting ships or production for one seat, so a
person can have a fair game against knower. It must not be a new `AiParams`
field (`CLAUDE.md`); a per-seat `Settings` list is the shape. It moves keys
like any field.

### Drafted starting positions

Warzone's picks. Seats choose their homeworlds in turn before turn 1. This is
a phase before the first turn, so it touches the replay log (the picks are
orders) and every bot needs a picker.

## Not proposed

**Cards** (Risk's sets, Warzone's airlift and blockade), **tech trees**,
**voting on rule changes** (TI's agendas) and **several ship types**. Each
adds hidden or growing state that breaks the minimal skeleton and multiplies
the roster's work. These were recommended against in the survey, not decided
against by the author. Related ideas already in
[`decided-against.md`](decided-against.md): boons carried between gauntlet
stages, campaign fleets, the in-app bot maker, and enforced fog in
play-by-post.
