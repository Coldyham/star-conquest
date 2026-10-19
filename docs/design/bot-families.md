# Bot families: one bot per family, its variants as stops

On 2026-10-19 three bots that were really variants of another were folded into
it as a stop of its `aux` knob, the way surveyor, learner and fulcrum had been
folded into actuary before them. Roster-wide rules are in
[`bots.md`](bots.md); each stop keeps its own design file. Index:
[`../README.md`](../README.md).

## The families

| Family | Knob | Stops (default **bold**) | Folded in |
| --- | --- | --- | --- |
| knower | Oracle | Off · **Predict** · Search · Council | council ([`council.md`](council.md)) |
| actuary | Style | Greedy · **Field** · Learning · Hunt | hunter ([`hunter.md`](hunter.md)) |
| thinker | Style | Claudebot · **Thinker** | claudebot |
| evolved | Look-ahead | Off · **On** | (its own family, [`evolved.md`](evolved.md)) |
| marshal, heuristic, rusherplus | none | | |

marshal stays its own bot: it began as a copy of knower's plan, but its code is
its own now, and a stop is for a variant the family's code already carries.
The knob carries the distinction, the menu lists one entry per family, and the
leaderboard's bot column has one row per family, at its top stop.

## Each fold plays exactly as the bot it replaced

`tools/fold_check.py` plays the old bot in a `git worktree` at the commit
before the fold (`0689b0a`) and the new stop in this tree, on the same
positions out of roster games, every seat of each, in separate processes so
each side loads its own registry. 120 positions (12 games, every sixth turn,
four cells), 269 seat decisions each:

| Old | New | Orders |
| --- | --- | --- |
| council, aux 1 | knower at Council (3) | the same |
| hunter, Field | actuary at Hunt (3) | the same |
| claudebot | thinker at Claudebot (0) | the same |
| thinker; knower 0, 1, 2, 12; actuary 0, 1, 2, 12 | unchanged | the same |
| council, aux 0 | knower at Search (2) | 10 of 120 differ |

council at Off was knower's Search run from a private copy of knower, and to
that copy a knower seat was another bot's, run at its stand-in (Off, the blind
plan) and so **believed**. knower believes only a knower seat at Off. Council
keeps the rule (`_surrogate`, under `_COUNCIL`), so Council is exact; Search
does not take it, since Search's own play is the older and more stored of the
two. With the rule on at Search too the 10 positions agree, so it is the whole
of the difference. A stored council seat at aux 0 (the control setting) now
plays as Search, a little differently against a knower rival.

hunter's switches are gated on the ledger's `hunt` flag, so the other three
stops cannot see them, and `tests/test_hunter.py` keeps the claim its
measurements rest on: with every switch at actuary's value, Hunt is Field. The
regenerated playstyle baseline is identical at `0689b0a` and after the fold.

## Legacy names (`ai.LEGACY`)

A setup stores a seat's strategy by name, the name is hashed into every link's
key, and a pool deals names, so a folded name cannot be rewritten. It stays as
a **legacy name**: `ai.LEGACY` maps it to its family and stop (council's aux 0
and 1 to knower's 2 and 3, claudebot to thinker's 0), and `ai.canonical` is the
one place a stored name becomes a family and stop.

- **A legacy name is never registered.** `ai.decide` (through
  `ai.strategy_fn`) runs the family with the seat shown as the family at its
  stop for the length of the call, then puts the stored name and params back.
  Anything that looks a seat up by name goes through `ai.canonical`, `ai.family`
  or `ai.known` (knower's `_surrogate` and `_rivals`, the setup warnings, the
  tools), never `ai.STRATEGIES` alone, which would play it as the heuristic.
- **The menu shows it as family · stop** and keeps the stored name until the
  seat's stop is edited (`menu._strategy_label`); editing the stop or rolling
  the dice makes it a seat of the family.
- **A stored log is never re-decided**, so a replay of a council game needs
  nothing; a new game from an old link plays the stop.

## A value past a new top stop (`AUX_OVER`)

knower's knob was once a depth slider, 0-12, and the menu carried a seat's aux
across a change of strategy, so stored seats hold 12 often: about 50 knower
seats (Search) and 13 actuary seats (Learning) in the 2026-10-19 census of 374
stored bot seats. Clamping them to the new top would quietly have made them
Council and Hunt. A bot that adds a stop above its old top declares `AUX_OVER`,
the old top, and a value past the new top reads as that (`ai.aux_over`,
`ai.aux_reads_as`, each bot's `_stop_of`, the menu's slider, the SQL mirror): a
new top stop is reached only by its own value. No thinker seat held an aux
below 1, so Claudebot at 0 changed none. The menu now resets aux to
`config.AI_AUX` on a change of strategy, so new 12s stop appearing.

## A random seat deals the stop too

`RANDOM_POOLS[1]` had not shipped, so it was rewritten rather than appended:
every stop of every family as a profile, `name:stop`
(`settings.resolve_profile`), which `build_state` stamps with its stop, so a
carried aux never reaches a random seat dealt from it. The first pool's bare
names keep the seat's own aux, as they always did, and its `claudebot` plays
through `ai.LEGACY`.

## On the leaderboard

- **One row per family, at its top stop** (`bot_replay.replay_aux`): knower at
  Council, actuary at Hunt, thinker at Thinker, evolved On. `fold-bots.sql`
  renames council rows to knower at Council (the same play, so no re-run),
  drops knower at Search and claudebot, and leaves actuary to refill at Hunt
  on the next run, which `pending()` does for a row whose aux moved.
- **A seat is a profile.** `sc_bots` lists each opponent's family and, for a
  bot with named stops, its `family:stop` (`sc_bot_profile`), so `?bot=knower`
  finds every knower seat and `?bot=knower:3` Council alone. `botChips` shows a
  bot off its default stop as two links, "knower · council". `bots.mjs` reads
  a setup the same way for the play-by-post roster and setup diffs, from the
  generated `bot-stops.mjs` (`tools/gen_bot_stops.py`); `test_leaderboard_sync`
  pins the file and the SQL's two tables to `ai.py`. The SQL was run against
  each bot's own reading in a local Postgres: it agrees except on a fractional
  evolved aux, which evolved rounds and the mirror floors.
- **Difficulty bands** are cut for 7 bots: Hard is a map only knower and
  actuary take, Medium one marshal, evolved or thinker take too, Easy one the
  heuristic or rusherplus take too (`format.mjs`, `DIFFICULTY_LEVELS`).

## Decided against

- **Rewriting a stored seat to its family** (`council` to `knower` at 3): the
  name is hashed into `challenge_key()`, so every link carrying it would read
  as a different map, and a log's `strategies` would stop matching its setup.
  A legacy name costs one table.
- **Keeping hunter's own Greedy stop** (its pull to every front alike): nothing
  stored named it, and Greedy is actuary's first stop already.
- **marshal as a stop of thinker or knower**: it began from knower's plan, but
  its code is its own; a stop is for a variant the family's code carries.
- **A row for every stop in the bot column**: fifteen rows a map, most of them
  weaker versions of the row above. One row per family at its top stop; a
  stop's games are what the chip filter (`?bot=knower:2`) is for.
- **Clamping a stored value past the old top into the new top stop**: 50 stored
  knower seats at 12 would have become Council, 13 actuary seats Hunt
  (`AUX_OVER`).
