# Design notes

Rationale, history, and edge-case war stories behind the rules in `CLAUDE.md`'s
Key Conventions. `CLAUDE.md` states *what* the rule is; this file is *why*, for
whoever is next in that code. Headings match the corresponding bullet there.

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

A challenge token travels by clipboard only, never the address bar or
`localStorage` — unlike a settings link, which syncs both. Two things would
break otherwise: an installed PWA has no address bar to read a link from, and
a *remembered* challenge read back at every later launch would make its
banner haunt sessions long after the link was opened.

Editing a challenge's setup asks first rather than locking the widgets,
because locking is a dead end the moment someone wants the same map with one
knob moved.

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

## Map viewport margins

The floor at `config.node_clearance()` exists because a node's circle is
drawn at a pixel radius that isn't part of the world bounds, so the fit/pan
maths can't see it — a margin below the largest node's drawn extent slices
that circle. `_clamp` compares against the fit-padded span rather than the
bare viewport because the bare-viewport comparison used to let a "centred"
offset push a boundary system back out through the margin at certain zooms
(regression test: `test_boundary_never_crosses_the_margin_at_any_zoom`).

## `models/knower.py` and simultaneous resolution

Because turns resolve simultaneously — `_collect_orders` hands every seat the
same unmutated state and applies nothing until all have decided — an
opponent's orders can't depend on yours. That means a bot can clone the
board, call each rival's own registered `decide`, and know their moves before
the engine asks for them: there's no fixed point to solve, one forward pass
of their real code *is* the answer. knower folds those predictions into a
"post-launch board" (predicted orders applied via `engine.apply_order` but
not advanced) and runs thinker's phases against it.

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
