# Core design notes: settings, links, rules and records

Why the pure core is shaped as it is, apart from the turn film and hand-drawn
maps (which have their own files). It covers settings tokens, the random seat,
challenge links and keeping digests stable, ship-speed growth, in-lane battles,
the replay log, and star names. Headings match the rules in `CLAUDE.md`, which
states *what* each rule is; this file says *why*. Related:
[`turnfilm.md`](turnfilm.md), [`hand-maps.md`](hand-maps.md),
[`leaderboard.md`](leaderboard.md) (what the board does with logs and keys) and
[`shell.md`](shell.md). Index: [`../README.md`](../README.md).

## Settings tokens (`to_token`/`from_token`)

A token is pruned then deflated: `token_dict` drops every field the reader
would infer anyway (defaults, unused seats — `from_dict`'s tolerance is what
makes omission safe), taking a default config from ~1470 chars to under 100.
`mode`/`players`/`nodes`/`seed` are always emitted even at default, because
pruning otherwise makes a token depend on the *reader's* defaults, and those
four are the identity of the match. `from_token` sniffs `raw[:1] != b"{"` to
keep pre-compression links working.

## A seat left to the seed (`settings.RANDOM_STRATEGY`)

The Strategy dropdown's last entry, `random`, is the one option that names no
decision function. `settings.resolve_strategy` turns it into a real bot inside
`build_state`, drawing from `settings.RANDOM_POOL`. The point is to play
without knowing who you are playing: a large part of this game's skill is
knowing how a given bot answers a given opening, and that is knowledge a fixed
opponent hands you before the first turn.

**Why it is resolved in `build_state` and not by a `models/` dispatcher bot.**
The obvious implementation is a model file whose `decide` looks up a choice and
forwards to it — it needs no core change and appears in the dropdown for free.
It breaks on the oracle contract. `models/knower.py` resolves each opponent seat
through `ai.STRATEGIES` and then asks the owning module `is_oracle_seat(player)`
to decide whether it may call that seat's real code or must model it blind. A
dispatcher cannot answer that question: the probe receives a `Player`, which
carries no seed, so the dispatcher has no way to know which bot this seat is
about to be. Every answer it could give is wrong somewhere — `True` makes knower
model a plain heuristic seat blind and untrusted, `False` lets knower call a
dispatcher that forwards to knower, which is the recursion the `fn is decide`
check exists to prevent. Resolving one layer earlier deletes the problem rather
than managing it: by the time anything looks at the seat, `Player.ai_strategy`
names the bot that is really deciding, and the whole roster stays eligible —
including the oracles, which are the opponents most worth not recognising.

**The pick is derived, never drawn.** `random.Random(f"{seed}:strategy:{pid}")`,
the same construction `botio.decide_seed` uses and for the same reason: taking
the pick from `state.rng` would shift every subsequent battle roll, so the same
seed would lay out the same map and then fight it differently depending on how
many seats happened to be left to chance. Derived instead, a seed reproduces the
opponents exactly as it reproduces the map — which is what makes a challenge
link on a mystery setup raceable, and a mystery match resumable. Each seat draws
from its own stream, so three random seats are three independent picks rather
than three copies of one.

**The pool is a fixed list, and the log records what it dealt.** The pick
indexes the pool by its length and position, so a pool that gains, loses or
reorders a name deals nearly every random seat a different bot. It cannot move a
stored game's *board*, because `replay.reconstruct` applies the recorded orders
and deals the recorded dice and asks no seat to decide anything (the property
`bot_replay.replay_rev` rests on). It moves everything else.

The pool was once `ai.available_strategies()`, read at build time, so a drop-in
joined it without being named anywhere. Map `ea1d2a0bf0a95ba4` (seed 626502,
four random seats) was posted twice on 2026-10-06, 23 minutes apart with no
deploy between. Re-running every bot on the recorded openings showed the two
players had faced heuristic / claudebot / claudebot / rusherplus and rusherplus /
heuristic / heuristic / heuristic. The second lineup is what a six-bot roster
deals, most likely a build the service worker had cached (stale-while-revalidate)
from before actuary joined. Every bot added to `models/` re-dealt every random
map on the board the same way, the weekly campaign's all-random maps and the
offline bot column included. Re-deriving the picks also meant a resumed match
switched bots partway through if the roster had moved, and a watched replay's
win overlay named today's pick.

Two fixes, each covering what the other cannot:

- **`settings.RANDOM_POOL` is written out**, so every build deals a seed the same
  bots whatever its `models/` holds, a stale cached build included, as long as
  the pool is unchanged. A pool name that is not registered plays as the
  heuristic (`ai.decide`'s fallback); `test_every_bot_in_the_pool_ships` keeps
  that from happening silently, and `test_the_pool_still_deals_what_it_dealt`
  pins the deal. A new bot is not dealt until someone adds it, and adding it
  re-deals every random map, so it is done on purpose, like a `RULES_VERSION`
  bump. Drop-ins are never dealt.
- **`replay.GameLog.strategies` records each seat's strategy as built**
  (`replay.seat_strategies`), and `reconstruct` stamps it back on.
  `main.start_game` records it through `new_log`. `sim.play_settings` records it
  after the hand-over, so a bot-column replay names the bot in the human's seat.
  A play-by-post log records it on its first rebuild, while it has no turns, so
  the resolver's upload carries the picks and every other client adopts them. A
  log from before the field existed has none and rebuilds with today's picks.

The alternative to a fixed pool was a roster fingerprint in the challenge key
whenever a seat is random: the same key would always mean the same lineup, but
every bot added would split every random map on the board, its scores, crowns
and campaign standings with it, and leave old challenge links racing a score
set against a different lineup.

**The win overlay reveals it.** `render._winner_label` reads `Player.ai_strategy`
— the resolved name — so a mystery match ends on "Verdant (Knower) wins!" rather
than leaving the one interesting fact about it unreadable. Disclosing it at game
over costs nothing, since there is no turn left in which to use it, and it is the
whole payoff of having played blind. It excludes a human seat (so a game claimed
part-way through, `engine.claim_seat`, is credited to the person rather than to
the strategy it opened under) and neutral, which carries a default `ai_strategy`
like every other player but never decides anything — it reaches the overlay only
on a mutual annihilation, where naming a bot would credit one that never played.

**`Settings` keeps the placeholder, so the mystery survives the link.** Only
`Player` is resolved; `Settings.ai_strategy` still reads `random`, so a saved
config, a shared setup and a challenge link all preserve it for their recipient,
and reopening the menu shows what was chosen rather than what it became. It also
decides what the leaderboard shows: `sc_bots` reads `settings_json`, so the
opponent chip on a mystery map reads `random` — the setup's rule rather than its
outcome. Two consequences worth naming. The chip's bucket is heterogeneous: a
`random` filter mixes games whose real opponents were knower and rusherplus, and
those are not the same difficulty. And the board cannot resolve it itself even
if it wanted to, since the pick comes out of Python's string seeding (SHA-512)
and JS cannot recompute it — the same wall `KEY_ALIASES` already hits with the
setup digest. If the resolved names should ever be disclosed, they go on
`Challenge`, which `challenge_key()` drops before hashing, never on `Settings`,
where a new field would move the digest of every setup ever shared.

## Challenge links (`settings.Challenge`)

Score is turns-to-win, ties broken on fewest ships lost. `hand` (how many
turns were human-decided) exists because autoplaying a *decided* game to skip
cleanup is normal play — disclosing it on the link is preferable to voiding
the score, so only a zero-hand-turn match is unshareable. `Challenge.key` is
redundant by construction (a checksum of the full setup) purely so the menu
banner can detect a since-edited config and warn, rather than locking widgets.

Two scores matching on *both* figures are a dead heat, and every place a score
is read out says so rather than falling back on arrival order: the win overlay's
verdict is three-way (`render._result_lines` — beat / matched / short of), the
leaderboard's table gives tied scores one place and skips the next
(`format.competitionRanks`), and a shared record on its homepage credits every
holder (`game_summary.best_holders`). A personal best is the exception —
`webstore.record_best` rejects a tie, since equalling your own last result is no
improvement to file.

A challenge token travels by clipboard only, never the address bar or
`localStorage` — unlike a settings link, which syncs both. Two things would
break otherwise: an installed PWA has no address bar to read a link from, and
a *remembered* challenge read back at every later launch would make its
banner haunt sessions long after the link was opened.

Editing a challenge's setup asks first rather than locking the widgets,
because locking is a dead end the moment someone wants the same map with one
knob moved.

### Keys outlive the schema that made them

The checksum is over the *full* setup dict, so adding a field to `Settings`
moves the digest of every setup that ever existed. A link shared before the
change then reads as "settings changed" against the identical map, and on the
leaderboard its scores group into a bucket of their own. The first real
instance: the defender-advantage slider, which split seed 879758 (3 players, 18
nodes) into `3e7b44384effd7b2` and `665714b9291851c6`.

Hashing only the *non-default* fields would immunise against this permanently,
and is rejected: a `config.DEFAULT_*` whose value later changed would then make
an old key silently alias onto a genuinely different balance — a wrong answer,
where the split is merely an inconvenient one. Instead `_LEGACY_KEY_DROPS`
lists, per schema change, the fields that version lacked;
`Settings.challenge_keys()` re-hashes without each and `Challenge.matches`
accepts any of the results. That is sound because a field missing from an old
dict was missing from the old game, and `from_dict` fills it with the default
that was then the only behaviour — 1.0 for defender advantage is exactly "no
bonus", which is how those matches were played. The converse is the guard on
it: a legacy digest is blind to the fields it drops, so an entry is offered only
while all of them are still at their defaults. Move the slider on an old link
and only the current key remains, and the banner warns as it should.

The list is maintained by hand, so `test_challenge_key_is_stable` pins the
default setup's digest and fails the moment a field joins `Settings` — the
prompt to append an entry rather than discover the split from a user. Only the
game can do this re-hashing; `js/token-decode.mjs` cannot recompute a Python
blake2s (the two languages disagree on integral floats), so the leaderboard
folds by lookup instead: `KEY_ALIASES` for incoming links, and
`leaderboard/fold-game-key.sql` for rows already stored.

Both of those are per-map and hand-kept, which is a step behind the game: an
alias is written only once someone reports the split, and its target is current
only until the next field joins `Settings` — the entry added for the
defender-advantage knob outlived its own target when in-lane battles landed. So
the leaderboard also folds *without* a checksum at all:
`leaderboard/js/submit.mjs` matches a submission against the stored
`settings_json` of every game row on the same mode/players/nodes/seed
(`setupIdentity` in `js/token-decode.mjs`) and posts onto the row that already
holds that map. Two versions of the game write the *same* `settings_json` for one
map — `token_dict` prunes each field still at its default, so a field added since
is absent from both — which makes this the same trade `sc_config_key` makes below,
and the reason no future schema change needs an alias at all.

It reaches two cases the alias list cannot reach even in principle. A digest can
move with no field added: `challenge_keys` changed in 2026-09 to blank a seat
beyond `players` rather than hash it, and a drop entry can only name a field, so
links stamped before that carry a key *no* build recomputes. And a player on a
stale cached build posts under the previous digest, which is a split nobody
caused. The case it shares with the alias list is a field joining `AiParams`:
`token_dict` prunes a seat dict only whole, so both the stored setup and the
digest move together and neither fold sees through it — the reason the note by
`_LEGACY_KEY_DROPS` sends a new per-bot knob to `AiParams.aux` instead.

Which key a map ends up under is settled in one direction only: the newest.
`submit.findTwin` takes the most recently created matching row and
`fold-game-key.sql` merges into it, so the site and the repair agree; a link to
the key that lost is not dead, since `game.mjs` resolves an unknown key through
`aliasFor` and forwards it.

The leaderboard's config grouping (same setup, different seed —
`leaderboard/README.md`, "Same setup, different seed") deliberately sits on the
*other* side of this trade-off. `sc_config_key` in `leaderboard/schema.sql` hashes
a stored `settings_json` with `seed` dropped, computed entirely in SQL over data
the game already sends — not a new `Settings`/`Challenge` field, specifically so
adding it never moves `Challenge.key` for a single existing map. The price is the
fragility this section spent its length rejecting for `challenge_key` itself:
`sc_config_key` is pruned-non-default by construction, so a changed
`config.DEFAULT_*` rehashes every config exactly the way a naive non-default
`challenge_key` was rejected above for doing. Worth it here and not there, because
a leaderboard grouping is cosmetic (worst case, a named config splits in two and
loses its name) where a `Challenge.key` mismatch is load-bearing (it decides
whether a target-to-beat banner is honoured or discarded).

A digest can also move without the *setup* changing at all, because the checksum
is over JSON and JSON distinguishes `12` from `12.0`. `AiParams.aux` is declared
a float, the AI tab's aux slider stores an int for a strategy that declares
`AUX_INT`, and `from_dict` used to widen it back — so a link stamped over a live
`Settings` hashed `"aux":12` while every decode of that same link hashed
`"aux":12.0`, and the recipient's menu raised the un-challenge modal the moment
the link opened. Seed 749187 (5 players, 33 nodes, a knower at search depth 12)
is the instance that surfaced it, stamped `109ffcf1cf4d4260`.

No legacy drop can recover that. A drop names a field, and here the field is
present on both sides; worse, a live `Settings` holds a *mixture* — the four
untouched seats were floats and only the dragged one an int — so accepting the
stamped digest by re-hashing would mean enumerating the int/float subsets of
every seat, `2^n` forms per drop entry, for a rule with no upper bound. The fix
is at the reader instead: `_ai_from_dict` keeps whichever of int/float `aux`
arrived as, alone among the fields. That is also the truer type, since a knob
declaring `AUX_INT` *is* a whole number, and it makes the digest survive a token
or save round-trip for any mixture without moving a single key already stamped.
The remaining seam is cosmetic and downstream: two players who reach the same
knob value by different routes — one dragging the slider to 12, one opening a
link that carries `12.0` — still stamp different keys, which is a board split of
exactly the kind `submit.findTwin` folds.

One consumer has to drop the distinction rather than keep it, and must go on
doing so: Postgres jsonb normalises `12.0` to `12`, so a `games` row reads back
with integer `aux` values whatever the game sent, while an uploaded log is plain
JSON and keeps the float. `verify_scores.same_setup` compares exactly those two,
so it widens both through `_aux_widened` first. Without that every posted score
whose setup carries an `aux` verifies as `mismatch` — the log's own map read as
somebody else's.

Widening any *other* float field on the way in stays correct, and deliberately
so: doing the same for `defender_advantage` would let a hand-edited `1` and a
slider's `1.0` hash apart, which is the split above with nothing to gain, since
no slider writes an int there.

## Ship-speed growth

`config.SHIP_SPEED_GROWTH_PCT` (Advanced → Travel, 0 by default) models tech
progression: ships get slightly faster every turn, so a map that opens at
3-4 turns a lane closes at 1-2. It applies **only at launch** — a fleet
already in transit keeps the schedule it was given. That was chosen over
re-timing fleets live because it needs no new mutable state anywhere: the
effective speed is a pure function of `state.turn` and two constants, which
keeps a seed bit-reproducible and leaves `Fleet` untouched.

**Growth compounds rather than adding a flat ly/turn**, for two reasons.
What a player perceives is travel *turns*, `L / v` — under linear growth
that is a hyperbola, so the step-downs bunch at the start: a 6-turn lane
tuned to reach 1 turn by turn 150 loses its first turn on turn 6 and its
last on turn 150, gaps of 6, 9, 15, 30, 90. Compounding spreads the same
five steps over gaps of 15, 19, 24, 34, 58. Second, linear growth made
*waiting* pay: delaying a launch shortens the trip when
`trip_turns > 1 / ln(1+r)`, and under linear growth that threshold is
`v / g`, which at the speed slider's 1.0 floor drops inside ordinary lane
lengths. Compounding makes it a constant ~50 turns at the gain slider's 2%
top, independent of base speed and past any lane on any map — which is what
sets that 2%.

How far a lane actually is depends on the node count as much as on the speed,
because `WORLD_SIZE` is fixed (up to a standard 40-system board; larger maps
grow the box to keep that spacing) — see "Lane length across the parameter space" in
[`bots.md`](bots.md) for the measured grid, and for why a margin
tuned at the default speed is tuned in only one of three regimes.

`GameState.travel_turns` re-times from the lane's real `length_ly`
(`config.travel_turns_at_length`), not by rescaling the already-rounded-up
baked `Lane.travel_turns` — that rescale-based approach (`config.travel_turns_at`,
kept around for hand-built states with no real length, e.g. some test
fixtures) double-rounds and can overstate the true time by up to a turn,
which read as a bug once it was visible next to the lane's length and the
current speed in the side panel (10.7 ly at 7.0 ly/turn showing 3 turns
instead of 2). Nothing outside mapgen should read `Lane.travel_turns`
directly regardless — every AI ETA estimate and the lane labels in `render`
pick up the current-turn value for free by going through the query.
Growth climbs towards `SHIP_SPEED_MAX`, which is also the ship-speed slider's
top — one declared ceiling rather than two, so however long a game runs a
fleet is never faster than the base speed alone could have made it. It is
belt-and-braces either way: travel already floors at one turn.

## In-lane battles (`engine._lane_crossings`)

Off by default, and deliberately so: `apply_order` deducting ships at launch is
what makes order-issuing have no bearing on outcomes, and a fleet that can be
intercepted mid-lane is the one place that stops being true.

The first cut fought **every** fleet sharing a lane, as one pooled force per
owner, and moved the survivors onto the winning side's most advanced fleet. Both
halves produced outcomes nobody wanted. Pooling meant an 8-ship fleet running a
lane held by three separate 4-ship fleets met a single 12-ship wall — at a point
none of the three occupied — and lost, when meeting them one at a time it wins
(8 beats 4 leaving 7, 7 beats 4 leaving 6, 6 beats 4 leaving 4). Merging meant
survivors teleported: `min(turns_remaining)` picks the *soonest arrival*, which
is only the *furthest along* while every fleet on the lane shares a speed, so
under ship-speed growth a fleet 80% of the way down a lane (2 turns left of 10)
could be folded into one only 50% along (1 turn left of 2) and visibly jump
backwards. And position was never consulted at all, so fleets fought from
opposite ends of a lane: measured across 30 games, 9% of clashes were between
fleets that never passed each other, a mean 30% of the lane apart.

So a fight is now a **meeting**, decided by geometry. Each fleet covers a
straight line along its lane over a turn, so the gap between two of them is
linear across the step: they meet exactly when that gap is zero at either end or
changes sign in between — reaching the same position if they share a speed,
passing through each other if they don't. `1 - (turns_remaining + 1) / turns_total`
is where a fleet was when the step began, and the fleets are measured from a
single end of the lane (`min(lane_key)`) so two running opposite ways sit on one
ruler. Solving for the zero gives the crossing point, which is what orders the
fights, and simultaneous crossings break on fleet order — order of launch —
so a replay fights them in the sequence the live game did.

Fights are strictly pairwise, and the winner is only ever *thinned*: no merging,
no re-timing, no moving. That is what keeps the rule checkable from the map, and
it is the invariant worth keeping if this is ever touched again — every fight is
between two fleets whose gap actually reached zero.

**Ship-speed growth mostly switches the feature off**, which is worth knowing
before tuning either knob. A fleet is exposed to the lane-battle phase exactly
`turns_total` times, so as growth drives lanes toward a single turn the window
for an interception collapses to the launch turn alone — both sides must enter
the same lane on the same turn. Measured over 30 four-player games, as
(share of launches that are single-turn hops, fights): 0% growth → (0%, 136);
0.5% → (49%, 58); 1% → (79%, 36); 2% → (95%, 13). The two settings are close to
mutually exclusive at the top of the growth slider.

## Persistence, replay & history (`replay.py`)

Format version 1 recorded the *human's* orders alone and rebuilt everything
else by re-running the AI against the same seeded `rng`. It was small, and it
was only ever as reliable as the least reproducible bot in the game.
`models/knower.py` truncates its search on a wall-clock budget
(`SEARCH_BUDGET_S`), so on a busy frame it plans one thing and on the replay's
tight headless loop another — and from that turn on the "reconstruction" is a
different match. It shows up as history not matching the game you played and a
resumed game handing you the wrong board. One of the saved games in the wild
records winner 5 and rebuilt to winner 4.

Nothing about a drop-in bot's determinism is enforceable, so version 2 stopped
depending on it: `engine.end_turn` returns a `TurnRecord` of every seat's
orders (in the sequence they were applied — `apply_order` clamps a
double-spent garrison as it goes, so the sequence matters) and the log feeds it
straight back as `script`. No seat is asked to decide anything on a replay.

The dice are in the record for the same reason, and it is worth being explicit
about why they have to be: skipping the AI means nobody draws the tie-break
jitter the bots drew live, so `state.rng` is at a different position by the time
combat asks it for a swing. Same orders, different battles, divergence anyway.
`engine._Dice` therefore keeps every draw a live turn deals, and deals them back
on a replay. Two alternatives were rejected: re-running `decide` purely to
advance the rng (fragile — it re-does knower's whole search on every history
build, seconds of it, and only works while every bot happens to be
deterministic), and snapshotting the Mersenne Twister state per turn (~5 KB a
turn, and the whole file is rewritten after *every* turn, so a long game would
spend tens of MB of writes on it).

Deriving combat's dice from `(seed, turn)` instead would have been free, but a
clone made by `knower._clone` shares both, so a rollout would meet the same
jitter the real turn is about to — handing the search the actual dice. The
recorded-draws route keeps rollouts rolling their own.

Version-1 logs can no longer be replayed faithfully, so `latest_log` skips them
rather than offering a resume that quietly rebuilds a different game.

`"rules"` (the human's `Ui.auto_forward`) is in the log because rewinding is
meant to hand back the position as it was, and on a big map the standing routes
*are* half the position. It is recorded before `main.resolve_turn`'s
`prune_forward`, i.e. the rules the turn was actually played with, and
`resume_game` re-prunes them against the rebuilt board so a rule whose system
was lost on that turn doesn't come back to life.

The per-turn `"ai"` flag carries a second job now. It is still disclosure first
(`GameLog.hand_turns`), but it is also the record of *when a person took the
seat over*: a match begun in autoplay has no human seat at all, and the first
turn flagged as hand-played is what claims it (see CLAUDE.md's "An all-bot game
has no human seat"). `reconstruct` re-applies that claim at the same turn, since
a scripted turn asks no seat to decide and nothing else would ever flip the flag
back — a match somebody demonstrably took over would otherwise come back exposed
to oracle prediction on every turn after the resume point. Deriving it from a
flag the log already carried, rather than storing a claim of its own, is also
what makes rewinding land right without a line of extra code: `truncate` drops
the flags along with the turns, so rewinding past every hand-played turn returns
the match to the all-bot game it was, and rewinding to any turn after one keeps
the seat claimed.

**A resume always lands paused**, whatever the match was doing when it was
recorded — a resume from the menu, a mid-game rewind, a fork out of a finished
game and a watched replay alike (`resume_game` builds its `Ui` with autoplay
off). You go back to a turn in order to look at it, and spotting a bot's blunder
one turn too late, rewinding to it and having the board start moving again
before it can be read is precisely what history exists to prevent. It costs
nothing to stop there because the claim is no longer tangled up with it: autoplay
is now purely "is anything advancing", so waiting decides nothing and who plays
on from here is a choice handed back with the clock stopped.

### `match_id`: the log's own identity

A log also carries a `match_id`, which is *not* part of what makes a replay
reproduce — it is how a score posted to the leaderboard names the match behind
it (**Checked scores**, in [`leaderboard.md`](leaderboard.md)). `truncate` keeps it, since a mid-game rewind
continues the same match; `fork` mints a new one, since a finished-game rewind
starts another. A log written before the field existed, or edited by hand into a
shape `_MATCH_ID_RE` refuses, is given a fresh id on load rather than carrying
that text into an upload.

## Star names (`starnames.py`, `System.name`)

Flavour with no mechanical weight: systems are still keyed by integer id
everywhere, and every mechanical display (queued-order rows, the sim's logs,
saved tokens) stays numeric. `System.name` is written once by mapgen and read
only by `render`.

`starnames.NAMES` is generated from the IAU Working Group on Star Names
catalogue by `tools/gen_starnames.py`, not read at runtime: the web build ships
only `starconquest/` plus `models/`, and a data file loaded at import would have
to be staged and fetched. Regenerating is `uv run python
tools/gen_starnames.py` after replacing `tools/iau-star-names.csv` with a fresh
export.

`_name_systems` runs last in both generators, after every roll that shapes a
map, so a seed lays out exactly the board it did before names existed — which
is what keeps saved setups, shared tokens and recorded games (whose replays
re-run generation) playing identically. Names come out of `state.rng` like
everything else, so a replay reproduces them for free and nothing about them is
serialized.

Labels are laid out collision-first (`render._draw_node_names`): a second pass
over the nodes, drawn after the circles, placing a name below its system and
dropping any that would land on a node, on another name, or on a label that
carries actual information (a lane's travel time, a rule's "keep N"; hence
`_pill_rect` being split out of `_label_pill`). The space *above* a system is
held for the numbers a playback writes there — a fight's cost, a finished hull's
`+N`, and the row the second of those stacks into (`render._mark_slot`) —
reserved on every visible node whether or not anything is showing in it. Reserving
it only while a mark was up meant a name could occupy the gap between fights and
then be shoved off the map the moment one fired, which reads as the name
flickering rather than as the number arriving. With the slot held, the fallback
above a node is effectively closed at every node size the map draws, and a name
that cannot fit below is simply dropped — measured at one name lost on a 24-node
map, which is the price of the labels that remain staying put. A
crowded map therefore thins out to the names that fit and zooming in brings the
rest back, rather than turning into mush. The selection and the hover are
placed first so what you are looking at is what keeps its name. A name whose
node has been panned off the viewport is skipped: clamping it into the clip
would leave a label floating at the edge with no system under it.

Panel headings hold a name we don't control the length of, so `_head_named`
drops to the small font when the normal one would overrun the panel (every
catalogue name fits at that size), `_rows_named` reflows a row containing one,
and the send popup — too narrow for two names plus a garrison — measures its
title and falls back to `Sys 4 -> 9` when the names don't fit.

## In-lane battles: the rule in full

**In-lane battles** (`config.IN_LANE_BATTLES`, off by default, on the menu's
Combat tab) are the one thing that breaks "fleets on lanes never interact". Two
*enemy* fleets fight only on the turn their paths touch or cross — sharing a lane
is not enough — and they fight **pairwise, in crossing order**, so a strong fleet
running a defended lane picks its opponents off one at a time and carries its
losses into each next fight. Nothing is pooled and nothing is moved: a winner is
thinned in place and keeps its own heading, speed and arrival turn, so a fleet is
only ever drawn where it really is. `engine._lane_span` measures both fleets from
one end of the lane and mirrors `Fleet.progress` (what `render` draws), so a fight
happens exactly where the triangles are seen to touch. Siting the phase *after*
`_advance_fleets` but *before* `_resolve_arrivals` is what lets a single-turn hop
still be intercepted — it is on the board, at `turns_remaining == 0`, for exactly
one lane-battle check. `combat.resolve_lane_clash` passes no `defender_owner`:
nobody holds open space, so `DEFENDER_ADVANTAGE` applies to neither side and an
exact tie annihilates rather than breaking to a defender — but the jitter, which
belongs to the dice rather than the ground, still applies.

## Pile-up resolution (`combat.resolve_arrival`)

`combat.resolve_arrival` takes an optional `on_step` list purely so a multi-owner
pile-up can be shown step by step — those intermediate values are locals and
unknowable from outside. Every side is pooled per owner (a reinforcement from the
defender's own side joins the garrison's bucket, which is exactly why it can
defend the fight it arrives for); attackers then fold **pairwise, strongest-first
among themselves**, and whoever survives that faces the defender **last**,
regardless of the defender's own size — the defender is not just another side in
the size-ranked queue, it holds the ground, which is what `defender_owner=old_owner`
prices on every step it actually participates in. Nothing else in `combat` changed
for this.

## Replay and history: the rules in one place

A match is **never snapshotted** — it is recorded as its *inputs*: `Settings`,
the concrete `seed`, and per turn **every seat's orders plus the combat draws**
(`engine.TurnRecord`, logged by `replay.GameLog`, auto-saved after every turn to a
gitignored, repo-anchored `games/` dir, mirroring `ai.MODELS_DIR` and
`menu._SAVE_DIR`). `replay.reconstruct(log, on_turn=…)` feeds each turn back
through `engine.end_turn(state, script=…)` to rebuild the **exact** state at any
turn — applying the orders verbatim and dealing the recorded dice to combat, so
**no seat is ever asked to decide again**. `main.resume_game` uses this to offer
resuming the last unfinished match from the menu, and restores that turn's
standing auto-forward rules (`"rules"`, `Ui.auto_forward`) with it.

**A replay must never depend on a bot repeating itself** — that is format
version 2, and why the orders and the dice are both in the log (version 1 stored
the human's orders alone and re-ran the AI; a bot on a wall-clock budget replayed
into a *different match*, silently). The corollary is load-bearing now that
replays are kept: **retuning, rewriting or deleting a `models/` bot cannot move a
single stored game**, so no per-model "replay floor" is needed and none should be
added (`test_a_replay_does_not_consult_a_bot_even_a_deleted_one` pins it, and
`bot_replay.replay_rev` is `engine_rev` minus `ai` and `models/` for the same
reason). What *can* move one is the engine itself — phase order, how a fight
resolves, how a map is drawn from a seed — which is what `engine.RULES_VERSION`
is for: bumped by hand with such a change, stamped on every log, and read by the
verifier to report `outdated` (unverifiable) rather than `mismatch` (wrong). Version-1 logs can't be replayed faithfully
and `latest_log` skips them. A turn still carries `"ai"` (was the human seat
autoplayed) — not for replay, but for `GameLog.hand_turns` (which `main.hand_turns`
delegates to, and the leaderboard's verifier recomputes). A log also carries a
`match_id`, minted per match from `settings.fresh_rng` and likewise not part of
what makes a replay reproduce: it is how a posted score names its replay
(`GameLog.encoded` is the wire form — the token's own deflate+base64url). A
rewind (`truncate`) keeps that id; a fork mints a new one.

**History mode** is a shell-only review scene (`Ui.history`, gated so it never
enters the pure core). On entry `main.build_history` runs one `reconstruct` whose
`on_turn` callback deep-copies each turn's board and folds fog (via
`_accumulate_fog`) into a per-turn snapshot, so the bottom-bar scrubber seeks by
plain list-indexing. Fog stays a `Ui`-layer concern: mid-game a past turn shows
fog *as it was then*, while a finished game is fully revealed. **Rewind** resumes
live play from the viewed turn — mid-game it truncates the same log file
(`GameLog.truncate`, confirmed first, since it discards later turns); on a
finished game it forks a new file (`GameLog.fork`) so the completed record stays
intact.

## An all-bot game has no human seat

- **An all-bot game has no human seat, and the seat is claimed by *playing*,
  not by pressing a button.** `settings.build_state` clears the `is_human`
  `mapgen` stamps on pid 1 whenever `Settings.autoplay` is set, so a match
  begun as a demo has no preferred seat — otherwise seat 1 is the one seat
  every oracle guesses blind at while simulating all the others exactly, which
  is both a handicap no other bot carries and why the app and the offline bot
  column used to play the same setup differently. `engine.end_turn`'s
  `claim_seat` is the way back: `main.resolve_turn` passes it on the first turn
  *ended under manual control*, never on the Take control press itself, so Take
  control doubles as a pause on a demo nobody means to play. It is a turn phase
  rather than a shell-side edit because it has to replay — a scripted turn asks
  no seat to decide, so `replay.reconstruct` re-applies it from the per-turn
  `"ai"` flag already in the log, at the same turn, and runs it *first* so that
  turn's own predictions already see the corrected flag. Deriving it from those
  flags rather than storing a claim of its own is what makes a rewind land
  right for free: `truncate` drops the flags with the turns, so rewinding past
  every hand-played turn returns the match to an all-bot game, while rewinding
  to any turn after one keeps the seat claimed.
  - **`resolve_turn` must not compute `human_orders` for an unclaimed seat.**
    `_collect_orders` skips a seat only when `is_human`, so passing orders in
    *and* leaving the seat unflagged runs its strategy twice — spare draws from
    `state.rng` that desync every oracle's stream tracking. Pass `None` and let
    the engine's own loop decide it, exactly as `sim.play`'s tournaments do.
  - **Resuming, rewinding or watching always lands paused** (`main.resume_game`
    builds its `Ui` with autoplay off regardless of what the log was doing).
    You go back to a turn to *look* at it, and a board that starts moving again
    before it can be read is the thing history exists to prevent. It costs
    nothing now that the claim is separate: waiting decides nothing, so who
    plays on is handed back with the clock stopped.

## Whole-number floats don't survive the browser

- **Whole-number floats don't survive the browser, so never compare setups as
  text across writers.** Python writes `0.0`, `1.0`, `18.0`; the leaderboard's
  JavaScript has one number type, so everything it writes (`games.settings_json`,
  a token it re-encodes, `pbp_*` rows) says `0`, `1`, `18`. Postgres jsonb keeps
  whichever text it was sent, so a Python-written row and a browser-written one
  can hold the same setup and differ as text — and `json.dumps`, `md5(…::text)`
  (`sc_config_key`) or `JSON.stringify` then call them different. This has
  bitten four times: the int `aux` a decode keeps, integer hand-map coordinates,
  `verify_scores._aux_widened`, and `campaign_games`, whose node settings come
  from `tools/campaign.py` (every campaign node sat unclaimable until the match
  moved to jsonb `=`). The rules:
  - **Compare by value, or after `Settings.from_dict`.** `from_dict` coerces every
    field back to its type, `aux` alone excepted (`_ai_from_dict`), so two
    decoded setups agree however they travelled — pinned by
    `test_a_browser_round_trip_moves_the_challenge_key_only_through_aux`, which
    fails if a new field escapes the coercion. In SQL, jsonb `=` compares numbers
    by value; a digest of the text does not.
  - **`sc_config_key` and `setupIdentity` are text identities, safe only among
    browser-written rows** — every `games` row is. A Python-written setup (a
    worker's, a log's) is matched to them by value, never by those.
  - **`aux` is the one digest leak left.** A board-made link (*Play this map* on
    an unscored map, *Play a new seed*, the campaign's *Play*, a legacy bot
    *Watch*) reaches the game as ints, so a customised seat's default `1.0`
    becomes `1` and `challenge_key` moves. `submit.findTwin` rehomes the score
    onto the right row and `_aux_widened` covers the verifier; what is left is a
    personal best filed under two keys.

## Past a standard board the box grows

Map generation (`mapgen.py`) has two modes: `random` (jittered-grid placement +
light relaxation + a Euclidean MST for connectivity, which is planar so edges
don't cross, plus a few crossing-rejected extra edges for loops) and `symmetric`
(one base sector rotated N times about a shared contested centre for a perfectly
fair start). Both must stay connected and planar-ish — `test_mapgen.py` guards
both. Note `symmetric` rounds the node count up to a whole number per sector and
adds the shared centre, so it can return **more than it was asked for** (41 at 40
nodes) — which is why `config.CUSTOM_MAX_NODES` exists separately.

**A symmetric map's layout says what joins its sectors.** The original board ran
every sector into one shared hub and nothing else, so every symmetric game was
the same shape: a race for the middle, with no border between neighbours.
`Settings.layout` picks one of `mapgen.SYMMETRIC_LAYOUTS`:

- `hub` — the original: each sector's innermost system links to one centre.
- `ring` — no centre; each sector links to both neighbours across its seams, so
  play is about borders, and a seat has two fronts and no shared prize.
- `wheel` — the hub and the ring together: more routes, and the centre can be
  bypassed.
- `core` — one contested system on every seam, near the middle, linked to the
  two sectors either side of it and to its neighbouring core systems. Each prize
  is shared by exactly two seats rather than all of them.

The rules that keep it fair and keep old games replaying:

- **`hub` is pinned.** A stored symmetric game replays by regenerating its map,
  and `RULES_VERSION` did not move when layouts joined, because `hub` draws
  exactly what it always drew (`test_the_hub_layout_is_the_board_it_always_was`,
  fingerprinted against the pre-layout generator). Every layout makes the same
  sector draws in the same order; the only difference upstream of the sectors is
  the per-sector count (`round((nodes - shared) / players)`, shared being 1, 0 or
  N) and, for `core`, a wider inner radius (below).
- **Added lanes are chosen once and rotated, and draw nothing.** `_seam_link`
  picks the shortest sector-0 → sector-1 pair that crosses nothing and grazes
  nothing (a homeworld only when no clean pair avoids one, which happens at two
  systems per sector), and `_spoke_source` picks each seam's spoke to its core
  system the same way, never from the homeworld. Rotation carries crossings with
  it, so a lane clean on one seam is clean on all of them.
  `test_every_layout_turns_onto_itself` checks the result maps onto itself.
- **`core` opens the middle up.** Core systems sit on the seams at
  `max(0.6 * r_inner, gap / (2 sin(pi/N)))` with `gap = 2.5 * node_clearance()`,
  so they stay a gap apart at six seats, and the sectors' inner radius moves out
  to a gap behind them. At the old radius, six-seat core maps put systems on top
  of each other in every seed.
- **`layout` is inert off a generated symmetric map** (`Settings.layout_inert`:
  a random map, or any hand map). Then `challenge_keys` hashes it as `hub` and
  `token_dict` leaves it out, so a random map's key does not depend on what the
  layout stepper was last left at. It joined `_LEGACY_KEY_DROPS`, so every
  pre-layout link still matches. The menu's Basic tab gained a ninth row for it
  (`_ROW_H` 58 → 52, the most that fits the 496px panel), shown as a read-only
  note where it is inert rather than hidden, so rows below it do not jump.

**Past a standard board the box grows; below it nothing moves.** `config.world_side(n)`
is exactly `WORLD_SIZE` up to `config.STANDARD_MAX_NODES` (40, the old cap) and grows
with `sqrt(n)` past it, up to `config.MAX_NODES` (120), so a big map keeps a full
standard board's spacing and lane lengths instead of just getting denser. Every
seed a map could be shared on before lays out identically, so `RULES_VERSION` did
not move, and `nodes` was already a `Settings` field, so no digest moved either.
Only the box scales: lane clearance and the extra-edge cap stay in `WORLD_SIZE`
units, since spacing is what is being preserved. The map creator stays at a
standard board (its canvas is sized for one): it refuses a 41st system and adopts
a generated map at `min(nodes, STANDARD_MAX_NODES)`. The menu's Advanced tab
reports the setup's lane spread in turns (`settings.lane_lengths`/`lane_turns`),
surveyed in `menu.pump` — never `draw` — keyed by `settings.lane_survey_key`
(which leaves ship speed out, so that slider just re-times the cached lengths),
held off mid-drag, and restoring `config` after it generates.

## Settings, challenge links and keys: the rules in full

- **All balance/aesthetic constants live in `config.py`.** Do not hardcode a
  magic number elsewhere — add a named constant there. Every module reads
  `config.X` *live* at call time (nothing is cached at import), so the Advanced
  menu tunes copies on a `Settings`, and `settings._apply_globals` (called by
  `build_state` just before generation) is the single writer that pushes them
  back into `config`.

- **`settings.Settings` is the pure, serializable pre-game config** (players,
  map, seed, global knobs, per-seat AI); `menu.MenuState` holds transient menu
  interaction state (analogous to `Ui`). `settings.build_state(settings, seed)`
  is the one funnel from menu/CLI to a `GameState`. `to_dict`/`from_dict` back
  both the JSON file Save/Load (menu footer, gitignored `saves/`) and a
  `to_token`/`from_token` pair that encodes a whole config into a URL fragment
  (mirrored to `localStorage` under `paths.WEB_SHARED_SETTINGS_KEY` so an
  installed PWA, which launches from a fixed `start_url`, still sees it).
  `from_token`/`from_dict` are deliberately tolerant (clamp, default, pad), so a
  stale or hand-edited token still loads to a playable config. Never prune a
  non-heuristic seat's `ai_params` when writing a token: it is a documented
  readable field for drop-in bots (`models/README.md`).
- **Challenge links carry a score to beat.** `settings.Challenge`
  (`turns`, `lost`, `hand`, `by`, `key`) is an optional field on `Settings`, so it
  rides all of the above with no new plumbing; `build_state` ignores it. Score is
  turns-to-win, ties broken on fewest ships lost (`Player.ships_lost`, written in
  `combat.resolve_arrival`). A challenge token travels by clipboard only
  (`webstore.copy_link`) — never the address bar or `localStorage`, unlike a
  settings link (`webstore.share_token`). Editing a challenge's setup asks first
  (`menu._draw_unchallenge`); `Settings.without_challenge()` is what persists a
  "change it anyway".

- **Adding a field to `Settings` invalidates every key already shared.**
  `challenge_key()` hashes the full setup dict, so a new field moves the digest
  of every map that ever existed and links from before it read as edited.
  `settings._LEGACY_KEY_DROPS` lists per schema change what that version
  lacked; `challenge_keys()` re-hashes without each and `Challenge.matches`
  (and `webstore.best`) accept any of them. Append an entry whenever a field
  joins `Settings` — `test_challenge_key_is_stable` pins the default digest and
  fails until you do. Only `challenge_keys()[0]` is ever *written*. The
  leaderboard folds by lookup instead (`KEY_ALIASES` in
  `leaderboard/js/token-decode.mjs`, `leaderboard/fold-game-key.sql`), since JS
  cannot recompute the Python digest — plus, for the splits nobody has reported
  yet, `submit.findTwin`, which posts onto whichever game row already stores
  this exact setup (`setupIdentity`, matched against `settings_json`) rather
  than opening a second page under the new digest. A split settles on the
  *newest* key — `findTwin` and `fold-game-key.sql` both move that way, and
  `game.mjs` forwards a link to a folded-away key through `aliasFor`. For the same reason, the
  leaderboard's same-setup-different-seed grouping (`sc_config_key` in
  `leaderboard/schema.sql`) is computed in SQL from stored `settings_json`
  rather than added as a field here — that would move `challenge_key()` for
  every map instead of only the config grouping.

## Star names: the rule in full

- **Star names (`starnames.py`) are flavour on top of that, never a key.**
  `System.name` is a cosmetic IAU star name (`System.label` is `"Vega (7)"`,
  `System.short` the name alone); mapgen's `_name_systems` stamps one per system
  **last**, after every roll that shapes the map, so a seed still lays out the
  board it always did and a replay recreates the names from `state.rng` with
  nothing serialized. `NAMES` is generated from `tools/iau-star-names.csv` by
  `tools/gen_starnames.py` — regenerate, don't hand-edit. On the map,
  `render._draw_node_names` places labels collision-first and drops what doesn't
  fit (see "Star names" above) — including the fixed slot above every system that a
  playback writes its numbers into (`render._mark_slot`), reserved whether or not
  one is showing, so a name sits below its system or nowhere rather than
  flickering out the moment a `+1` or a fight's cost appears. Ids stay on the
  mechanical readouts — the queued list, `tests/sim` logs, tokens.
