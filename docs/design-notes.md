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
