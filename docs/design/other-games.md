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
stars. This is the stage-target machinery (`gauntlet.md`, "Stage targets")
shown on every posted map rather than gating a gauntlet. Worked out on
2026-10-28 from the author's sketch; not built.

**The tiers.** A star is earned by a counted score (`counted_scores`) and
compared as a best is: turns first, ships lost breaking a tie. Each tier asks
for the one below it.

| Stars | For | Target |
| --- | --- | --- |
| 1 | a win | any counted score (scores are wins only) |
| 2 | beating every bot | the bot column's best winning row |
| 3 | beating the search | the honest par line (`par.md`, "Dice modes") |
| bonus, hidden | beating the optimist | the lucky par line |

**A star can never be lost.** That needs two things:

- **Other people never move it.** Every target comes from the map and the
  code, never from another person's score, so a better player arriving takes
  nothing.
- **A target is frozen when it is first set, and never changes.** Stage
  targets do the opposite (tightened later, checked again), and the bot
  column moves too: `bot_scores` is keyed `(game_key, bot)` and overwritten
  when `engine_rev` goes stale, and a new bot joins the column on every map.
  Either can beat a line that used to be the best. So a worker writes a
  map's targets once into a table of their own (`star_targets`: game key,
  tier, turns, lost, what set it, rules version, when), with no update path,
  the way a day is never remade (`daily.md`). Stars stay **derived, never
  stored**: a score against frozen targets, so a deleted or demoted score
  takes its stars with it, as crowns do. A retuned bot or a better search
  shows on the next map, not on old ones.

**Details that follow.**

- **Meeting a target counts**, as with stage targets. Seven maps have a par
  line on the floor (`par.md`, "The live board"); nobody can beat those on
  turns, only on ships, and on `b9119a724511200d` honest dice reach the floor.
- **No bot wins: the second target is any win.** Same for a search that finds
  no win (3 of 84 maps on honest dice). One win then earns two or three stars
  at once, which reads right: nothing else took that map.
- **The second target freezes once the column is whole**: every family in
  `ai.LADDER_ORDER` has a row on the map. Until then the map shows one star possible and
  the rest pending.
- **The third target is the better of honest par and the second target.**
  Honest par lost to the bot column's best on 9 of 93 maps, and it must never
  be the easier tier. "Set by" says which set it.
- **Par goes on the board.** That reverses "Not on the board" (`par.md`,
  2026-10-07), which gave no reason beyond keeping the bot column the one
  computed number. The cost is real: knower maps hit the 1200 s budget and
  20+ system maps were never searched. So it is its own job in
  `bot-replay.yml`, a budget per map, recording each map as it finishes, and a
  map it cannot reach shows no third star rather than a weak one. A budget-hit
  line is still a line; it is frozen like any other.
- **The bonus star is hidden and rare by design.** On keyed dice the lucky
  line beat all 7 human wins by a median 25 turns. People beat it on 7 maps,
  all set when a rewind re-rolled and four with a knower budget-hit, so where
  it falls it marks a short search as much as a great game. It costs a second
  par run per map.
- **Rewind fishing.** Scores posted before keyed dice (2026-10-08) could fish
  a roll, which the honest search cannot. The board still counts them, but
  the third and bonus stars should ask for a log keyed every turn
  (`replay.dice_follow`), the same dice the search met.
- **Rules versions.** A `RULES_VERSION` bump that moves a map's play leaves
  old targets describing an old game. A target carries its rules version, and
  a score is graded against targets of its own version; a map's targets for a
  new version are set afresh, once.
- **Showing it.** A star column on each map's standings, one row per person
  (their best), a profile total, and on the map page "★★ needs 31/12 (marshal)",
  so the target is never a mystery. The game itself needs nothing: it asks the
  board, like `/api/campaign`.

### A rating against the bot ladder

Warzone and Galcon rank players, but here two people never meet in a scored
game: play-by-post never reaches the leaderboard. They do meet on a map: two
people who both posted on it played the same setup and seed, and their bests
compare directly, whatever the map's difficulty or rivals. Worked out on
2026-10-28 from the author's sketch; not built.

- **A comparison per pair per map.** On each map, each pair among the posters
  (best counted score each) is a win, loss or draw: turns, then ships lost,
  equal both a draw. The map's difficulty cancels within the pair, so no
  "rated field" is needed.
- **The roster bots are players.** Each bot-column row (one per family, at the
  top of its knob) posts on every map; a losing row loses to every human win,
  and two losing rows draw. Bots have the most comparisons, so they anchor the
  list and give it a meaning: "rated between marshal and knower".
- **Fit, don't update.** Comparisons have no order worth keeping, so fit a
  Bradley-Terry model to all of them at once (minorisation-maximisation, a few
  hundred iterations at board sizes) rather than Elo, which would depend on
  posting order. A prior of one draw against an average player keeps a
  one-map player off the top, and the list shows people with five maps or
  more. Weight a map's comparisons by 1/(n-1) for n posters, so one crowded
  map doesn't count as a hundred.
- **Derived, never stored.** Inputs are `counted_scores` and `bot_scores`, both
  public, and the fit is cheap, so the board can fit it in JS at render time,
  like the standings; a nightly view only if it gets slow.
- **What it cannot see.** People post wins only, and bots always have a row,
  so a person is absent from the maps they lost and rates high against the
  bots. That is the honest reading of the data (it rates how good your wins
  are, and which maps you win), and the list should say so. Unlike stars, a
  rating moves when others post; that is what a rating is.
- **Seasons** (Warzone's monthly reset) become a filter: the same fit over
  maps first posted in a month.

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
