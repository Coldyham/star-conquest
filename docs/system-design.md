# System design notes

Rationale, history, and edge-case war stories behind the rules in `CLAUDE.md`'s
Key Conventions, for the game core and the pygame shell. `CLAUDE.md` states
*what* the rule is; this file is *why*, for whoever is next in that code.
Headings match the corresponding bullet there. Its companion is
[`bot-design.md`](bot-design.md), which covers the AI roster and its tuning.

## Text sizing (`config.apply_ui_scale`)

`apply_ui_scale` grows the font by ~2x on a phone, so a width or row pitch
tuned at the baseline size overflows there. Concretely: labels used to spill
out of footer buttons, and the info panel's rows used to land on top of each
other, before layout was switched to measure-then-place.

## `config.touch_ui`

The one place `TOUCH_MIN_TARGET` gives way is the send popup's own height:
seven tap-floored rows can outgrow the band it's placed in on a window shrunk
after boot (the scale is probed once). Because the clamp pins an oversized
panel to the top, the row that falls out of `draw`'s clip is the destructive
Delete — invisible but still live, since input hit-tests the recorded rect.
Hence the popup shrinks its rows to their labels first, against a budget
measured from the placement band rather than the viewport.

## Settings tokens (`to_token`/`from_token`)

A token is pruned then deflated: `token_dict` drops every field the reader
would infer anyway (defaults, unused seats — `from_dict`'s tolerance is what
makes omission safe), taking a default config from ~1470 chars to under 100.
`mode`/`players`/`nodes`/`seed` are always emitted even at default, because
pruning otherwise makes a token depend on the *reader's* defaults, and those
four are the identity of the match. `from_token` sniffs `raw[:1] != b"{"` to
keep pre-compression links working.

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
because `WORLD_SIZE` is fixed — see "Lane length across the parameter space" in
[`bot-design.md`](bot-design.md) for the measured grid, and for why a margin
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

## Map viewport margins

The floor at `config.node_clearance()` exists because a node's circle is
drawn at a pixel radius that isn't part of the world bounds, so the fit/pan
maths can't see it — a margin below the largest node's drawn extent slices
that circle. `_clamp` compares against the fit-padded span rather than the
bare viewport because the bare-viewport comparison used to let a "centred"
offset push a boundary system back out through the margin at certain zooms
(regression test: `test_boundary_never_crosses_the_margin_at_any_zoom`).

## Persistence, replay & history (`replay.py`)

Format version 1 recorded the *human's* orders alone and rebuilt everything
else by re-running the AI against the same seeded `rng`. It was small, and it
was only ever as reliable as the least reproducible bot in the game.
`models/knower.py` truncates its tree search on a wall-clock budget
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

### `match_id`: the log's own identity

A log also carries a `match_id`, which is *not* part of what makes a replay
reproduce — it is how a score posted to the leaderboard names the match behind
it (**Checked scores**, below). `truncate` keeps it, since a mid-game rewind
continues the same match; `fork` mints a new one, since a finished-game rewind
starts another. A log written before the field existed, or edited by hand into a
shape `_MATCH_ID_RE` refuses, is given a fresh id on load rather than carrying
that text into an upload.

## Send popup / `Ui.editing_existing`

The popup commits immediately, so a fresh compose and a reopened order are
structurally identical by the time it's drawn — `editing_existing` is what
lets `_close_send` unwind all the way to `IDLE` on an edit. That matters
because reopening borrows `Ui.selected` to aim the popup at the order's
source; leaving it armed on close would make the next tap on a neighbour
queue a *second* fleet.

The count slider is hit-tested before the popup's own drag handling because
the panel is draggable by its background, which would otherwise swallow every
press inside it. The knob travels over the recorded row inset by
`config.SLIDER_KNOB_R` at each end — any other mapping either overhangs the
panel or drifts from the finger at the extremes.

## Combat rules page (`menu._draw_combat`)

The square law was explained nowhere player-facing, and the natural guess —
subtract the fleets — is wrong by a wide margin, which players read as the game
cheating. The page answers that by describing what *actually* happens rather
than arguing with the wrong rule: the readout states both sides' losses ("You
lose 5, they lose all 10"), because the sub-1:1 exchange **is** the square law.
An earlier draft printed the subtraction answer alongside for contrast; naming
a rule the game does not use only invites the reader to keep it in mind.

`combat.preview_fight` lives beside `resolve_fight` rather than in the menu,
sharing `_apply_advantage` / `_resolve_effective` / `_survivors` with it, so
the two run the same statements instead of two copies of the same rule. This
is the opposite call to `viewstate.threatened_systems`, which deliberately
keeps a *local* copy of `ai._threat`: there the shell is banned from importing
`ai`, and approximate agreement is fine for a suggestion. Nothing bans
importing `combat`, and a page whose job is to teach the rules must not be
allowed to drift from them. The pin is `test_preview_nominal_matches_resolve_fight_at_zero_jitter`,
which is only expressible with both in the same pure module.

`jitter`/`advantage` are parameters rather than `config` reads because the
menu previews what the player is dragging *now*; those values only reach
`config` at game start via `settings._apply_globals`, so reading config there
would show the previous game's balance.

`best`/`worst` are the corners of the jitter square, not samples. The
attacker's effective strength rises with its own roll and the defender's falls
with it, so the corners genuinely bound both who wins and how many survive —
which is what makes the band honest to print as a range. When they disagree,
the headline goes amber: with a coin-flip fight the average roll's winner is
not a fact worth asserting in 30pt type.

The demo sliders are the one group writing to `MenuState` instead of
`Settings` (the third `kind` in `_SLIDER_SPECS`). Putting them on `Settings`
would push a scratch calculation into every save file and share token, and —
worse — dragging them on a challenge link would raise the un-challenge modal,
since the setup would stop matching the score.

Prose is hand-broken rather than reflowed, which inverts the rule everywhere
else in the shell. `render._wrap` exists because the *font* scales with
`config.ui_scale`; the menu canvas is fixed at 1440x960, so the risk runs the
other way — a runtime wrap makes the page's height depend on its text, and one
added word would push the table through the panel floor unnoticed. Broken by
hand, the height is a constant. That is also why `_ADV_COMBAT` moved here: the
Advanced tab's right column had been overflowing the panel by 4px, and
`test_tab_content_stays_inside_the_panel` now guards every tab against it.

The jitter matrix (`_draw_jitter_matrix`) shows the whole jitter square at
once: the attacker's swing across, the defender's *down*, so the centre is the
average roll, the top-left corner is the attacker's worst case and the
bottom-right its best. Reading down-and-right is reading from bad luck to good,
and `test_jitter_matrix_is_monotone_down_and_right` pins that orientation.

It replaced a survivor-vs-enemy-strength curve, which could only ever show one
slice of the randomness — and jitter is exactly the part players were failing
to reason about. As a grid the win/loss boundary becomes a *shape*: a solid
block of one colour when the fight is settled, a diagonal split when it is a
coin toss. Its corners are `preview.best`/`worst` by construction
(`CombatPreview.roll`), so the picture and the band line beneath the headline
cannot disagree in front of the player.

Three swings per axis rather than five: five needs 132px of height where three
needs 96, and the extra rows only interpolate between corners that already
bound the outcome. At zero jitter the grid would be one fight repeated nine
times under three identical `0%` headers, so it collapses to a single sentence
instead. The centre cell is highlighted because it is the fight the readout
spells out in words — same number, same colour — which is what teaches the
reader how to read the other eight.

## Losing / spectator mode

Keyed on *defeat* rather than "no systems left" because revealing the map for
a landless player who still has a fleet flying would leak it into `Ui.seen`
for good if they retook a system.

The reset-view camera piggybacks on the same moment: `Ui.reset_view` frames
the whole map once `state.winner` is set or the human is defeated, and frames
just `Ui.seen` otherwise (game start, the reset button, a window resize).
`WorldView.fit_to` gets there by layering a fresh zoom/pan on top of the
existing full-map `_world_bounds`/`_fit_scale` rather than recomputing them,
so `ZOOM_MIN` and the pan clamp stay keyed to the true full map regardless of
what was last framed — manually zooming out always reaches it. `resolve_turn`
compares defeat/winner state before and after `engine.end_turn` rather than
checking it plain, so a spectator fast-forwarding an already-decided match
doesn't get re-snapped every turn, only the one that actually crosses into it.

## Route mode (`viewstate.ROUTING`)

Everything else in the game commits on click; this doesn't. It writes a dozen
rules at once and can overwrite existing ones, which is far too much to unpick
one click at a time — so it builds a proposal and confirms it, and is the only
mode that does.

**Owned-only paths are forced, not chosen.** A rule can only exist on a system we
hold (`Ui.rule_is_live`), and `prune_forward` deletes any whose source we lose.
So a route `a -> X(enemy) -> d` would need the rule `X->d`, which cannot exist:
it would be born dormant and culled at the end of the turn. There is no
safe/unrestricted toggle to offer because unrestricted routing is
*unrepresentable*, not disallowed. The **destination** is the exception — its
incoming rule sits on the last owned system of the path — which is exactly what
lets a chain be aimed at an enemy front as an assault funnel. Choosing where to
point is therefore itself the safe-vs-assault decision.

**The plan can't contradict itself.** `model.flow_field` is a BFS seeded at the
destination, so `parent[node]` is the next hop toward it. Because `parent` is a
dict, the next hop is a function of the node alone: two selected systems whose
routes converge cannot demand different hops from the shared node. And a parent
edge always steps to a strictly shallower node, so walking it from anywhere
terminates at the destination. No conflict resolution, no cycle check *within*
the plan.

**But the plan plus surviving rules can loop.** The destination is the one
plan-adjacent node the plan gives no rule, so if it already forwards back into
the plan — directly, or down a chain of rules on systems the plan doesn't touch —
ships circulate forever. Friendly arrivals are lossless, so nothing is destroyed;
the ships simply never reach a front, which is worse than losing them because it
looks like it is working. `_detect_route_cycles` walks the merged graph and
`confirm_route` drops the closing edge, which is always a rule the plan doesn't
overwrite.

### Two sub-modes

Chain routing answers "push *these* systems at *that* place". The complementary
question — "where should *everything* flow?" — took one chain route per arm of the
empire, each boxed and aimed separately. Rally answers it in one gesture: pick the
systems ships should gather at, and everything else you hold forwards toward the
nearest of them.

They are the same feature. `model.flow_field` was already a multi-source search whose
seeds need not be traversable, so rally needed no new graph query and no new rule
shape — only a different seeding. Chain seeds the one destination and walks each
pick's path to it; rally seeds every pick at once and takes the returned field whole,
since that field *is* the plan: every owned system it reached, mapped to its next hop
inward. Everything downstream (the `keep`-preserving `_add_hop`, cycle detection, the
confirm, the preview, the End Turn slot takeover) is shared, which is the reason this
is a sub-mode toggle rather than a third top-level mode.

**"Nearest" is travel turns, not hops.** `flow_field` was a plain BFS, which counts
jumps and ignores how long each one takes — so a two-hop path down two long lanes beat
a three-hop path down three short ones, against the game's own rule that travel time
is a query (`state.travel_turns`). Route mode passes `by_turns=True` and gets Dijkstra
instead. It matters most in rally, where "the nearest rally point" is the whole
mechanic, but chain had the same bug and gets the same fix. The AI keeps the
unweighted default deliberately: `ai._flow_to_frontier` wants the nearest *frontier*,
and a frontier is a frontier however long the lane to it is — changing that would be a
balance change wearing a bug fix's clothes. On a map of uniform lanes the two agree,
which is why the hand-built test graphs needed lanes deliberately restretched
(`_lengths`) to tell them apart.

**One Mode button, not a Chain/Rally pair.** With two, whichever is lit has to be read
as "you are here" and the dim one as "go here" — and a strip where every other button
means "do this" is the wrong place to teach that distinction. One button naming the
sub-mode it is *in*, which switches when pressed, says the same thing without asking
anyone to infer a convention from a fill colour.

**A tie is a free choice, so rally spends it on balance.** Equidistant means the ships
arrive just as soon whichever point they go to, so an arbitrary-but-consistent
tie-break is pure waste: it piles a whole region onto one rally point while its
neighbour idles. `_plan_rally` sends a tied system to whichever point is drawing less.

Load is **ships per turn**, not systems — `System.production` is turns *per* ship, so
four barren systems are a thinner stream than one rich one and counting systems gets
it backwards. It is inflow only; a rally point's own output was never something the
plan directed anywhere.

The greedy is exact rather than approximate because it assigns **nearest-first**. A
node's chosen hop is always strictly nearer, so it has already been assigned, and
`target` records which rally point that node's ships *actually* reach — not the one we
aimed them at. Without that the accounting drifts, because a tied node can hand its
ships to a neighbour that was itself tied and assigned elsewhere. The no-cycle
guarantee is untouched: balancing only ever chooses among hops that each step strictly
nearer, so the potential still decreases along every edge.

**Auto-route** picks every threatened system as a rally point in one press — the front
line as one gesture, which is the shape rally is for. "Threatened" is the AI's own
`_threat` (a rival, not neutral, holding a neighbour, or rival ships inbound), copied
into `viewstate` rather than imported, since the shell computes its derived stats
locally and `render`/`input` may not reach into `ai`. It discloses nothing fog hasn't:
a neighbour of a system we hold is one hop away, and a fleet inbound to one ends at a
system we hold, so both are in full view at any sight tier. It replaces the picks
rather than adding to them — a "do the obvious thing" button has to mean the same
whatever came before — and render leaves it out entirely when nothing is threatened,
so it is drawn exactly when it would do something.

A richer version was considered and dropped: a rally point that claims only the
systems closer to it than to a front. It is a better *idea* and a much worse control —
the set it claims moves every turn as the front does, so what you confirmed and what
you get come apart. Rally is deliberately the dumber thing, and it is dumb in a way
you can see on the map before confirming.

**Rally overwrites the whole rear, on purpose.** It rules every system it reaches, so
a confirm blows away hand-made rules the plan disagrees with. That is what "everything
flows to the nearest point" means, and the panel's `N replaced` count is the
disclosure — the same line chain mode has, doing much more work here.

**The sub-mode is sticky; the proposal is not.** `reset_route` runs every turn from
`main.resolve_turn`, so clearing `route_rally` there would drag the player back to
chain routing between one plan and the next. `set_route_rally` does clear the
proposal, because `route_sel` holds sources in one sub-mode and sinks in the other and
carrying a group across would silently invert what it means.

**Rally's only loop shape.** The plan covers every reachable owned system, so the one
node left holding a rule the plan didn't write is a rally point itself — and a sink
that forwards onward isn't a sink. `_detect_route_cycles` catches it unchanged and
`confirm_route` drops it, which is the right answer rather than a lucky one.

**A drag boxes; a tap always aims.** There is no stage and no modifier. The
gesture set is deliberately lopsided — the box adds, and a tap never does —
because that is what leaves a tap with exactly one primary meaning.

A *rally* tap toggles, which is not a second meaning conditional on the system but a
single one: membership. The rejected shape below was "aim on some systems, remove on
others" — two different kinds of action, chosen by what you happened to land on.
Toggling is one action whose effect is symmetric, and it is the only gesture rally
needs, which is what hands drag back to panning in that sub-mode.

This went through two wrong shapes first, both worth recording. It started as two
stages ("pick", then "aim"), on the reasoning that an owned system is ambiguous:
is a tap adding it or naming it as the target? That is real, but the fix was worse
than the problem — you cannot tell which stage a tap will land in without reading
the footer, and an extra button sits between you and every route.

The obvious collapse is to let the system decide: tap a picked system to remove
it, tap anything else to aim at it. That is unambiguous in the formal sense and
still wrong, because it makes a tap mean two different things depending on what it
lands on — and worse, it makes *aiming at one of your own picks* destructive. Pick
a group, aim at a member, aim somewhere else, and the member is gone. Aim at each
member in turn and the group empties completely.

So: a tap aims, always, whatever is under it. The destination is not removed from
`route_sel` — it stays picked and is skipped as a source by `route_sources` — so
re-aiming is free and reversible. Removal is the second tap on the thing you are
already pointing at, which also un-aims it. The rule is one sentence, and nothing
it does is silent.

Drag is spent on the box, so panning is right-drag on a mouse and the on-map
Reset / −/+ cluster on touch. That costs less than it sounds: `ZOOM_MIN` is the
fit-all view, so at zoom 1 the whole map is on screen and there is nothing to pan
to until you have zoomed in — and Reset undoes that in one tap.

**Nothing from live play stays live.** `_handle_route_event` takes the whole event
stream, so the ordinary `_handle_left_click` ladder — every branch of which
assumes a single `Ui.selected` — is unreachable rather than audited. It is
dispatched *after* the game-over branch, not beside the history one, or it would
swallow the win screen's own controls. On the render side the mode swaps the
footer strip wholesale and `_lay_out_footer`'s shared zeroing loop retires every
live-play rect for free; the confirm takes the End Turn button's slot, which makes
ending a turn mid-plan impossible by construction instead of by a guard. The
panel's queued/rule lists are suppressed too — their × buttons would mutate
`auto_forward` underneath the preview.

**`keep` is 0 on a new rule** (the Forward tab's own default), but a replaced rule
that already pointed at the same next hop keeps its `keep` — so re-running a route
over an existing conveyor is idempotent rather than quietly resetting tuning that
was already correct.

**One consequence worth knowing.** With whole-path `keep = 0`, losing a mid-chain
system leaves its upstream neighbour forwarding its entire garrison into enemy
territory every turn: `prune_forward` drops the captured system's rule, but the
upstream one is still live and `rule_is_live` doesn't care who owns the far end.
A hand-made rule has always had this property, but the player made *one*, on
purpose. Hence `_draw_forward_rules` tinting any rule aimed at a system we don't
hold — it is a real move as well as a real accident, so it is flagged rather than
prevented.

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
over the nodes, drawn after the circles, placing a name below its system or —
failing that — above it, and dropping any that would land on a node, on another
name, or on a label that carries actual information (a lane's travel time, a
rule's "keep N"; hence `_pill_rect` being split out of `_label_pill`). A
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

## Bot replays (`tools/bot_replay.py`)

The leaderboard's "how would each bot have done?" column. `leaderboard/README.md`
carried it under **Not built yet** for a long time with the note that it "needs
the real Python engine, so it wants its own small service". The service turned
out to be the wrong shape.

A replay's result is a pure function of three things — the stored `Settings`, the
seed, and the code — so it only ever has to be computed *once*. There is nothing
to serve live, and an always-on host would spend most of its life idle waiting to
recompute answers it already had. What the feature actually needs is a cache and
something to fill it, which is a scheduled job: `.github/workflows/bot-replay.yml`
writes into `public.bot_scores` on a schedule and leaves no service to keep up.
Netlify was never a candidate either way — its Functions run JavaScript and Go,
and there is no Python runtime to put the engine in.

The cadence is set by Actions minutes rather than by how fresh the column needs
to be. This repository is **private**, so runs bill against the account's monthly
allowance (2,000 minutes on the Free plan; public repositories are unmetered).
GitHub rounds every job up to the whole minute, so an hourly schedule spends
730+ minutes a month doing nothing but asking whether there is work — over a
third of the allowance before a single replay runs. Every six hours costs ~120
and is still far more often than a leaderboard needs, with `workflow_dispatch`
for when it is wanted sooner. It also comfortably covers the other thing the
schedule buys: a free Supabase project pauses after about a week idle, and any
run touches the REST API.

The one capability given up is on-demand compute: a visitor cannot ask for a bot
that hasn't been run yet and watch it appear. That only starts to matter if the
column ever grows into something interactive — picking a bot and watching the
replay animate, or re-running it at a different `aux`. That would want a real
service; a cached table does not.

### Why it goes through `settings.build_state`

`tests/sim.play` takes mode/nodes/players and calls `mapgen.generate` directly.
That is the wrong door here: a posted setup carries tuned balance knobs, and
`build_state` is the only funnel that pushes them into `config` before generation
(`settings._apply_globals`). Replaying a 99-ships-per-homeworld map through
`generate` would quietly hand every bot the *default* map instead — the same
class of bug as `Settings.from_dict` returning a default `Settings()` when handed
something that isn't a dict, which is why `bot_replay._settings_for` checks the
shape rather than leaning on that function's usual tolerance. Tolerance is right
for a stranger's link and wrong for a worker that will post confident numbers
under a real map's key.

The map a bot inherits is identical to the human's, down to the star names
mapgen rolls last. The battles are not: once orders diverge so do the draws
taken from `state.rng`. That is the point — same board, its own war.

### What the replayed seat is tuned to

Slot 0 of `Settings.ai` belongs to the human, so whatever a menu left in it says
nothing about how a bot ought to play, and honouring it would let the same bot
score differently on two otherwise identical maps. The seat therefore gets a
default `AiParams` — with one exception.

That exception is `aux`, the one bot-defined knob. Every other field belongs to
the built-in heuristic's own tuning and says nothing about a drop-in's identity,
but `aux` is whatever that strategy decides it is, so "this bot at its best" is a
statement only the caller can make. `bot_replay.REPLAY_AUX` is where the board
makes it, and today it holds one entry: `knower` at search depth 12. Opponent
seats keep both the strategy and the params the setup gave them — those *are* the
map's difficulty, and changing them would answer a different question.

The `aux` in force is stored on the row, not implied by the code that happened to
be running. A reader comparing the board against a game they played from the menu
— where knower's seat defaults to depth 1 — is owed that, and `pending` reads it
back to notice when the policy has moved: an `aux` mismatch refills on an ordinary
run with no flag, because such a row answers a *different question* rather than
merely an older one. That is the distinction between it and `engine_rev`, where
`--stale` stays opt-in.

`bot_replay.BUDGET_SCALE` is the companion knob, lifting the bots' own per-decide
wall-clock guards 100x for a caller with nobody waiting on it. Both choices are
measured rather than assumed — see [`bot-design.md`](bot-design.md), "Replaying a
bot for the leaderboard", for the numbers and why a bigger budget is the *more*
reproducible option rather than a looser one.

### A loss is a result, not a score

`bot_scores.won` is the discriminator, never `turns`. A bot that never took the
board still reports how long it lasted and what it spent, which is worth showing,
but 600 turns of stalemate is not a better result than dying on turn 40 — and a
quick death is certainly not better than a slow victory. So `standings.botOrder`
puts every winner first, ranked by the game's own rule, and leaves the failures
after them in plain name order, which claims nothing. `bestBot` and `humanVsBots`
consider only winners for the same reason.

The verdict line measures a person against the *best* bot rather than counting
how many they beat. On a board of high scores the interesting question is whether
anyone outplayed the best machine answer to that map, not whether they placed
mid-table among six of them.

### `engine_rev` hashes the simulation, not the commit

Each row records a digest of what produced it, so `--stale` can find rows the
code has moved past. A git SHA would be the obvious choice and is the wrong one:
it moves on every commit, so a CSS change would invalidate the entire board. The
digest covers the outcome-determining core modules plus every `models/*.py`, and
nothing else. `starnames` is in that list despite being cosmetic — `_name_systems`
draws from `state.rng`, so changing the name list shifts every roll taken after
it (see **Star names** above).

Nothing invalidates automatically. A changed bot leaves stale rows until someone
runs `--stale` on purpose, which is the same "a fix is a deliberate act" trade
`configs` already makes with its first-name-wins rule.

### The one table the public cannot write

`bot_scores` has a read policy and no insert policy, and no insert grant. The
worker's `service_role` key bypasses RLS entirely, which makes it the only
writer. A human score carries no proof in itself — the token format is public and
unsigned, which is what **Checked scores** below answers — so it would be strange
to let the machine column be posted by hand too. It also means a rerun can *replace* a row, which is why this table is not
append-only the way the rest of the board is.

A per-decision wall-clock budget (`--bot-timeout`) is off by default, because a
blown budget forfeits that turn's orders and the result would then depend on how
fast the runner was that day. Where one is used, the count lands in
`bot_scores.bot_timeouts` and the page marks the row rather than presenting it as
reproducible alongside the others.

## Checked scores (`share.py`, `tools/verify_scores.py`)

The board's other pure-function-of-the-inputs job, and the answer to the oldest
entry under `leaderboard/README.md`'s **Known limitations**: a score in a
challenge link is a *claim*, since the token is public and unsigned and a
hand-crafted impossible result posts exactly like a real one.

A replay is not a claim. `replay.py` already records a match as its inputs —
settings, seed, and per turn every seat's orders plus the combat draws — and
`reconstruct` feeds them back through the engine without asking a single seat to
decide anything. So the evidence for a score already existed; it just never left
the player's machine. Uploading it is the whole feature.

**The id rides on `Challenge`, and that is what makes it free.** `challenge_keys()`
pops `challenge` before hashing (it is the score attached to a setup, not part of
the setup), so a field added there moves no digest and needs no
`_LEGACY_KEY_DROPS` entry — where the same field on `Settings` would have
invalidated every challenge link ever shared. `GameLog.match_id` is minted per
match from `settings.fresh_rng`, never `state.rng`: it must *not* be reproducible
from the seed, or every player of one shared map would mint the same id.

**Two things send, and both are consented to.** Pressing *Post to leaderboard*
uploads the match behind that score. Ticking *Share replays* on the menu
(`webstore.share_games`, off until switched on) also uploads a game as it goes —
every `share.CHECKPOINT_TURNS` turns and again when it ends. The cadence is the
whole point of the second one: a match that is *abandoned* never reaches an end,
and abandoned and lost games are exactly what a score can never carry and what a
bot is worth measuring against. Nothing else sends — `share_challenge` does not,
and neither does a pure autoplay demo (`hand_turns == 0`), which is reproducible
from its seed and so is bytes without information.

The preference is a local one (`paths.WEB_SHARE_GAMES_KEY`) rather than a
`Settings` field, for two independent reasons: it belongs to an installation and
not to a game setup, so it has no business in a save file or a shared link — and
a new `Settings` field would move `challenge_key()` for every map that has ever
existed.

`share.py` is a platform bridge in the `webstore`/`softkeyboard` style — guarded
everywhere, silent on failure, fire-and-forget on both sides (a `fetch` whose
promise is never read on the web, a daemon thread off it) so a POST can never
stall the frame. It keys a row by `GameLog.setup_key()`, which pins the seed
actually played: `main` resolves "roll a fresh seed" at game start and never
writes it back, so hashing the live `Settings` would file a random-seed game
under a key describing no particular map.

**`game_logs` is the one table the public can neither read nor write.** RLS is on
and it has no policies and no anon grants at all — every other table here takes a
row from anyone, and for a hundred-byte score that is a fine trade. A replay is
5-14 KiB, so an open insert path is a storage bill rather than a nuisance. Writes
go through the leaderboard site's own function (`netlify/functions/log.mjs`),
which validates the row, caps its size and rate-limits the caller, and holds the
service_role key that is the table's only writer. Reads are the worker's alone,
so uploading a game does not publish it.

The rate limit is honest about itself: Netlify functions run on ephemeral,
parallel instances, so an in-memory window stops a runaway loop and not an
adversary. What actually bounds the table is the size cap, the check constraint
behind it, and the fact that dropping the table is one statement in the SQL
editor. A durable limit would mean storing everyone's IP, which is a worse thing
to own than the abuse it prevents.

**Nothing identifying is attached.** A row is a match id, a setup key and the
moves. Grouping one person's games across sessions would need a durable client
id — a tracking identifier by any other name, and it buys nothing here.

Rows are append-only like the rest of the board, so a game that checkpoints
repeatedly lands several times and the *longest* row is the current one
(`verify_scores.best_logs`); `--prune` clears what it supersedes, using the one
delete path the public does not have.

**The verifier binds the replay to the setup.** Without `same_setup`, an easy
map's log could be attached to a hard map's score and would replay perfectly.
Both setups are hashed in Python by the same code, so a key the *site* folded
(`KEY_ALIASES`) or computed itself for a hand-written link never has to be
reproduced in JS. `Settings.from_dict` is deliberately tolerant and answers a
non-dict with a default 18-node map, which here would verify a score against the
wrong map entirely — the same trap `bot_replay._settings_for` documents, and it
is checked for the same way.

The four verdicts land in `score_checks`, worker-written and publicly readable.
`missing` — no log was ever uploaded — is stored rather than inferred from
absence, because "nobody has looked yet" and "we looked, and there is nothing to
check" are different facts about a score, and it is the one verdict that is
retried without a flag: an upload can still arrive.

What none of this proves is that a *human* played the game. A bot driving the
seat produces a log that verifies like any other. That is what `hand` is for, and
the verifier recomputes it from the log's own per-turn autoplay flags rather than
trusting the number in the link.

### Watching one back

A posted score names its replay, so the board can offer *Watch* — and the link
goes back into the **game**, not into a player written here. The reviewer already
exists: `reconstruct` rebuilds every turn, `build_history` folds the fog as it
stood, and the scrubber walks it. The engine is Python, so a JS viewer would be a
second engine to keep in step with the first, forever.

`#log=<match id>` is the launch URL (`--watch` off the web), and it is
distinguishable from a settings token by prefix alone: a token is base64url,
which cannot contain `=` except as the padding the encoder strips.
`main.open_replay` decodes the blob and goes through `resume_game`, so a watched
replay is the same object a resumed save is — which is what makes *rewinding out
of one* work for nothing: fork it at any turn and carry on playing from there. It
adopts the replay's own settings, so leaving review lands on that setup rather
than whatever the menu was showing.

`open_history` is the entry the H key and a watched replay share. They differ
only in how they came by the log, and must not differ in what review looks like.

**The download is polled, never awaited.** `share.fetch_log` starts it and hands
back a `Download` the frame loop asks once per frame; the browser build yields a
frame at a time, so blocking on a round trip would freeze the canvas before
anything had been drawn. Every failure is a *state* — pending, ok, error — rather
than an exception arriving on some arbitrary frame.

**What is watchable is decided in SQL.** `public_replays` is `game_logs` joined
to the scores that point at it, and it is the one view here deliberately *not*
`security_invoker`: the others exist so a tightened policy still binds their
callers, while this one exists to lend out a subset of a table nobody may read,
which only owner rights can do. Its `where` clause is the whole consent boundary,
so posting a score publishes that game and a checkpointed one stays private. The
submit page says so at the point the decision is made, which is the only place
saying it is worth anything.

### Versioning: bots are free to move, the engine is not

Keeping replays around raises an obvious worry — if the bots change, do the old
games still replay? — and the answer is that the question has the wrong subject.

**A stored log never consults a bot.** `end_turn(script=…)` applies the recorded
orders and deals the recorded dice; `decide` is not called. So retuning marshal,
rewriting knower or deleting a `models/` file outright cannot move a single
stored game. This is not an inference: `test_a_replay_does_not_consult_a_bot_even
_a_deleted_one` replaces every strategy with one that issues nonsense, removes one
from the registry, and asserts the reconstruction is unchanged. It is also exactly
why format version 2 exists — version 1 re-ran the AI, and a bot on a wall-clock
budget replayed into a different match.

So there is no case for a per-model "replay floor" gating which games are still
watchable, and adding one would cost the feature most of its value in exchange for
nothing. `bot_replay.replay_rev` is the same statement in code: `engine_rev` minus
`ai` and every `models/*.py`, so tuning a bot cannot mark a score verdict stale.

**The engine is the axis that does move a stored game**, and it is versioned by
hand. `engine.RULES_VERSION` is bumped in the same commit as a change to the phase
order, to how a fight resolves or how a map is drawn from a seed, and every log
carries the version it was played under. When a replay then fails to reproduce its
score, `verify_scores` reports `outdated` rather than `mismatch` — unverifiable,
not wrong. The check happens *after* the replay, so the many old games a bump does
not actually disturb keep verifying on their own merits; only the ones it broke
are set aside, and set aside rather than accused.

**Rewinding needs no versioning at all**, because it does not claim to reproduce
anything. A rewind replays the prefix exactly, then plays *on* from there — a
counterfactual by construction, which is why `fork` mints a new `match_id`. Both
rewinds re-stamp `rules_version`: the kept prefix has just been replayed under
today's rules to find that turn, so the log would be describing itself as older
than it is.
