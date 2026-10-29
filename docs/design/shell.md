# Shell design notes: layout and controls

Why the pygame shell behaves as it does. It covers text sizing and touch
targets, viewport margins, the send popup, the menu's Combat page, spectating
after a loss, and route mode (chain and rally). Headings match the rules in
`CLAUDE.md`. The turn playback, a large part of the shell, is in
[`turnfilm.md`](turnfilm.md), and the map creator is in
[`hand-maps.md`](hand-maps.md). Index: [`../README.md`](../README.md).

## Text sizing (`config.apply_ui_scale`)

`apply_ui_scale` grows the font by ~2x on a phone, so a width or row pitch
tuned at the baseline size overflows there. Concretely: labels used to spill
out of footer buttons, and the info panel's rows used to land on top of each
other, before layout was switched to measure-then-place.

## `config.touch_ui`

The one place `TOUCH_MIN_TARGET` gives way is the send popup's own height:
seven tap-floored rows can outgrow the band it's placed in on a window smaller
than the design baseline (the window fit in `main.ui_scale_for` is floored at
1x, so shrinking past the baseline doesn't shrink the UI). Because the
clamp pins an oversized panel to the top, the row that falls out of `draw`'s clip is the
destructive Delete — invisible but still live, since input hit-tests the
recorded rect.
Hence the popup shrinks its rows to their labels first, against a budget
measured from the placement band rather than the viewport.

## Phones: the compact layout (`config.compact`)

On the web build a phone's framebuffer is its viewport at device resolution
(about 2300x1080 for a 870x412 CSS px landscape phone), and the scale used to be
the 1440x960 baseline fitted into *those* pixels, times the touch boost: 1.6x,
which put a desktop's worth of HUD on a 6" screen, 18px text at 11 CSS px and
footer buttons 25 CSS px tall. The menu, letterboxed from its 1440x960 canvas,
was worse (8 CSS px). A touch browser knows its CSS pixel (`main.web_css_px`),
so its scale is now set in CSS pixels instead.

It was first a whole CSS pixel per baseline pixel (`config.TOUCH_CSS_SCALE`),
18 CSS px text. That looked right on a tablet, but a phone held landscape under
its browser's address bar is about 820x320 CSS px, so it got 320 baseline
units of height: the map was a strip of overlapping systems, and the menu's
settings panel came out narrower than the desktop's, so its two-column tabs
stacked into one, the hand-broken text rewrapped, and every tab scrolled
(Basic included) under a finger that kept landing on sliders. So the scale is
now the largest, up to that whole pixel, that leaves the screen
`TOUCH_MIN_W` x `TOUCH_MIN_H` (600x480) baseline units (`main.ui_scale_for`,
floored at `TOUCH_CSS_MIN`). 480 is what the Basic tab needs under its tabs and
600 the desktop-width panel in portrait, so that phone draws at ~0.66 (12 CSS
px text), a tablet still at 1.0, and an installed app, with no address bar,
somewhere between.

At that size a phone holds about 1250x480 baseline units (portrait 600x1150),
against the 1440x960 the HUD and menu were drawn for, so the layout itself had
to change; `config.compact` (touch only, so no desktop window changes shape)
is what switches it:

- **The footer folds rather than drops.** A strip too narrow for every button
  used to drop the least essential, which on a phone (no keyboard) lost the
  control outright; it now folds them behind a "more" button whose list offers
  them again, on a desktop window too. A compact screen folds the exits (Quit,
  Setup menu, New map) from the start, giving the room to the controls in use.
- **The send popup docks.** Floating over a map a third the size of a
  desktop's, it covered the lane it was about and outgrew the band it was
  clamped to. Landscape, it takes the side panel's place (its title repeats the
  panel's system and target, and a row under it the lane's length in ly and
  turns, which the title alone had dropped); portrait, it is a sheet over the
  controls under the map, so the map stays whole while it is open. Docked, it
  has a *Done*: closing by tapping the map is a desktop idiom a panel doesn't say.
- **Portrait moves the panel under the map.** A right-hand column on a
  portrait phone left the map a sliver. The band under the map is a fixed
  few rows (`config.PORTRAIT_PANEL_H`), so the map never moves with the
  selection: a selected system in four rows (`render._panel_system_compact`),
  its lane and rule beside it, and with nothing in focus a one-line reminder
  rather than the how-to prose, which a quarter of the screen spent on was
  noticed as wasted and which the tutorial already teaches. The regions are `config.play_rect`,
  `side_panel_rect`, `footer_rect` and `end_turn_rect`, and End Turn crosses
  the foot of the screen, under a thumb. History keeps End Turn's row (the
  scrubber takes both) so the map does not move on the way in.
- **The menu gets a canvas of the screen's shape** (`menu._draw_menu_compact`),
  in baseline units. Its settings panel keeps the desktop's 560 width
  (`_C_PANEL_W`), so every tab lays out as on the desktop; landscape gives the
  rest of the width to the title and the actions, three to a row where their
  labels fit (`_compact_columns`). That column is taller than its title and
  actions, so the buttons grow into some of the slack (up to `_C_GROW`) and a
  sketch of the setup's map fills the rest (`_compact_sketch`,
  `settings.map_sketch`, made in `pump`). Portrait puts one under the panel,
  which is as tall as its tallest tab (Combat, jitter matrix and all;
  `_tallest_tab_h`) whichever tab shows, so neither moves as the tabs change,
  and its buttons grow the same way, so turning the phone doesn't resize them.
  The status message takes the map's slot while it shows. It is the real board only with fog off; under
  fog a pinned board (a daily's, a challenge's, a hand map) is what fog keeps
  from you, so it is a "?" and "Map hidden". A seed left to chance has no board
  yet, so it is a sample, captioned so, which gives nothing away. The actions are folded to six, so no row is
  left with one (`_compact_action_items`): the board's Recently played and
  Shared matches share a Leaderboard list, as the desktop footer once did; a
  drawn map's x sits inside Edit map; and there is no Quit, which a browser tab
  can't do and a phone leaves to its own controls (the game's overflow list
  keeps it). Tutorial, Daily and Play by post each open something of their
  own, so none is nested in another. The Basic tab's rows close up a little
  (`_C_ROW_H`). Basic and Advanced fit whole; a tab that doesn't (Combat, on a
  short screen) scrolls, and a drag that starts on a slider goes by its
  direction, up or down to scroll and across to move it. The canvas is upscaled,
  so its text is softer than the game's. Drawing it at device resolution was
  built (a surface that multiplied every coordinate out and rendered text from
  a scaled font, with the desktop path untouched) and backed out: it was
  sharp, but the scrolling panel's measured height (`get_bounding_rect` over
  a layer 2.6 times the size) took a frame to ~25 ms natively, slower again
  in the browser. Measuring that height without the scan would be the way back.

Two touch-only rules came with it, from play on a phone:

- **A send is finished once made.** A compose used to keep its source selected
  after the popup closed, so another send could follow from it; on a phone that
  made the next tap on a neighbour (often one's own system, tapped to give it
  orders) a second fleet. On touch, Done or a tap on empty map lets the source
  go, and while the popup is open a tap on another system selects that system.
  Splitting a garrison means tapping the source again. Desktop keeps the old
  flow, where hover shows what a click will do.
- **Two fingers pinch and pan the map** (game, history, route mode, map
  creator). SDL turns the first finger into a mouse press, which reaches the
  board before the second finger exists, so a pinch puts back what that press
  changed (`input._pinch`, from a snapshot taken before every touch press) and
  ignores the emulated mouse until every finger is up. The canvas is
  `touch-action: none` so the browser never takes the gesture for page zoom.
- **Nothing on the page is selectable.** Every control is drawn on the canvas,
  so a long press on one used to start a text selection or the callout menu.
  `tools/pwa/inject.py` sets `user-select: none`, `-webkit-touch-callout: none`
  and no tap highlight on the page (inputs excepted, for the soft keyboard's
  hidden field), and `webstore._CANVAS_STYLE` repeats it on the canvas.

What was rejected: a "rotate your phone" prompt (a phone is mostly held upright,
and the board opens there from a link), and shrinking the desktop layout's fonts to fit (the problem was
the layout, not the font).

## Map viewport margins

The floor at `config.node_clearance()` exists because a node's circle is
drawn at a pixel radius that isn't part of the world bounds, so the fit/pan
maths can't see it — a margin below the largest node's drawn extent slices
that circle. `_clamp` compares against the fit-padded span rather than the
bare viewport because the bare-viewport comparison used to let a "centred"
offset push a boundary system back out through the margin at certain zooms
(regression test: `test_boundary_never_crosses_the_margin_at_any_zoom`).

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
else in the shell. `widgets.wrap` exists because the *font* scales with
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
so the zoom limit and the pan clamp stay keyed to the true full map regardless of
what was last framed — manually zooming out always reaches it. `resolve_turn`
compares defeat/winner state before and after `engine.end_turn` rather than
checking it plain, so a spectator fast-forwarding an already-decided match
doesn't get re-snapped every turn, only the one that actually crosses into it.

## Ironman and the ships-lost readout

Ironman (`webstore.ironman`, the menu's *Ironman* row) is honour-based: it takes
History away while the game is undecided, because History is where rewind
lives, and nothing records or posts that it was on. It is a local preference
for the reason `animate_turns` is: on `Settings` it would move `challenge_key()`
for every map ever shared. `main.new_ui` stamps it onto `Ui.ironman` so nothing
reads the store once a frame, and `Ui.can_review` is the one gate render's
footer button and main's `toggle_history` both ask. A spectator who has been
knocked out still can't open History, or they could rewind and play on; once
the game has a winner, *Review history* on the win overlay is offered as usual.

The top bar shows our seat's `ships_lost` beside the turn counter
(`render._draw_ships_lost`) all game, since it is a score's tie-break and
someone chasing a posted result would otherwise only learn it at the end.

## End-of-game graphs (`stats`, `render._draw_history_graph`)

History review can lay a chart over the lower part of the map, after the
Civilization games' map-replay-plus-graphs end screen: tabs for systems, ships
including in transit, income in ships/turn and cumulative ships lost, each a
line per seat across every recorded turn, plus *Lost/turn*, a bar per turn
stacked by seat (`stats.STACKED`) — as a line it is all spikes, while a
stacked bar shows at once when the big fights were and who paid for them —
and *Map share*, stacked bands that always fill 0-100% (`stats.AREA`): each
seat's share of every system, with the unclaimed share as a grey band on top.
A hidden seat leaves a gap rather than rescaling the rest, so a band always
reads as a true share. Mid-game the unclaimed band is charted on the same terms
as a rival (below): on the turns some unclaimed system was in sight. (Until
2026-10-23 it waited for review to be revealed.) A cursor marks the
turn the map is showing. *Graphs (G)* on the scrubber bar toggles it, Tab cycles the metric,
and a click or drag across the plot seeks a turn as the track does. The ^
caret (F) at its top right makes it full screen, filling everything between
the bars with the scrubber kept, and the v caret docks it again. The legend is
the trace selector: a click on a seat's row (or 1-9) shows or hides its line,
and the y scale fits the lines shown, so hiding a runaway leader makes the
rest readable.

On a landscape screen the legend is a column right of the plot, as wide as its
names (bot names included) up to 40% of the panel, past which the bot names
drop and give their room back to the plot. On a portrait one it runs under the
plot instead, full width, in as many columns as fit a whole row, and a docked
panel grows to hold it: a column beside the plot there left it half the width
and still clipped the bot names. The tabs wrap short of the caret.

The camera is as free in review as in live play: drag the map to pan, wheel or
−/+ to zoom, Reset (R) to re-frame. The Reset/−/+ cluster sits at the map's
foot, lifted above the docked chart when it is open; full screen there is no
map, so no cluster. A press on the reviewed map is always a pan, since there is
nothing to select.

The game's camera is free past the fit, by a ``room`` (`WorldView.zoom_at`,
`pan`): it zooms out until ``room`` of the map's size shows past each side, and
pans anywhere that keeps ``1 - room`` of the map on screen on each axis (of the
view, once the zoomed map is the wider, so an edge stays reachable), so a
player can't easily end up looking at nothing. Live play gets
`config.LIVE_ROOM` (0.5: the map half the view across at the most zoomed
out); review gets more, `config.REVIEW_ROOM` (0.85: about 37%), so the part
of the map under the docked chart can be brought into view without the
default squashing the map into the space above it. Reset restores the
ordinary framing either way, and leaving review calls `WorldView.settle` to
bring the camera back inside live play's room. The map maker keeps the strict
clamp (no ``room``).

- **Read off the history boards, never the log.** `stats.chart` runs once in
  `main.open_history` over the boards `build_history` already reconstructed, so
  a curve can never disagree with the map at the same turn, and nothing decides
  or rolls again. `stats` is core (no pygame), drawing nothing from `state.rng`.
- **The graphs know what the top bar knew.** A finished game charts every seat
  on every turn. Mid-game, `main.open_history` masks the chart
  (`stats.sightings`, `stats.masked`) so a rival is charted only on the turns
  the top bar showed its standings: while it held a system in that turn's
  sight (with fog off, always), and once knocked out if it was ever sighted.
  Its other turns are NaN, a gap and never a zero. A line is drawn solid
  along each run of known turns and dotted straight across a gap, since what
  happened out of sight is a guess. Bars and bands skip the unknown turns, and
  the legend reads "–" on one. `Ui.graph_seats` offers our seat and each rival
  sighted at least once; Map share's unclaimed band follows the same rule.
  Losses are cumulative, so a running total is known
  whenever the seat is. A turn's *Lost/turn* bar needs that turn and the one
  before it known; otherwise the first turn back in sight would carry the whole
  gap's losses as its own. (Until 2026-10-23 a mid-game review charted our seat
  alone, even with fog off.)
- **Open by default on a finished game**, where it is the summary review is
  for; closed in a mid-game review, where the board is the point.
- **The chart lives on `Ui` only while reviewing** and is dropped on the way out.
- Not yet: the default camera does not reframe into the space above the docked
  chart (on purpose: it would squash the map; the loose camera covers it), and
  there is no per-turn losses or kills view (a fight's dead are recorded per
  owner, not per killer).

## Naming each seat's bot (`Ui.bot_label`)

Pointing at (or tapping) a scoreboard entry in the top bar opens a tooltip
under it: the seat, who plays it, and its standings in words (or the last-known
ones, or that it is out). The graph legend names the same after each seat,
and the win line uses the same label (`render._winner_label`): "Verdant
(Knower · Predict) wins!", or "(Marshal, random)" for a seat dealt at random.

- **Labels are worked out once, in `main.new_ui`** (`seat_bot_labels`): the
  family (`ai.canonical`, so a legacy name reads as its family) and the stop its
  knob sits on (`ai.aux_stop_name`). Render imports no `ai`, so it reads them
  off `Ui.seat_bots`.
- **A random seat stays secret until the game is decided.** `Ui.random_seats`
  comes from the `Settings` (`RANDOM_STRATEGY`), since `Player` only ever holds
  the resolved bot. It reads "Random" until `Ui.bots_revealed`: a winner on the
  board, or review of a finished game. A knocked-out spectator still waits,
  because the bot is half of what the remaining seats are playing against.
- **A person's seat is never a bot**: `Player.is_human` on the board being drawn,
  so a seat claimed part-way through reads as its bot on the turns before.
- The tooltip goes through the scoreboard's own fog (`_score_vis`): a rival
  never sighted has no entry to hover.

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
inward. Everything downstream (the hold-preserving `_add_hop`, cycle detection, the
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

**A tie splits evenly.** Equidistant means the ships arrive just as soon whichever
way they go, so there is nothing to choose: `_plan_rally` gives a tied system an even
split down every hop that is one lane nearer on a shortest route (`shares_even`) —
two rally points, or two equally quick routes to the same one. Before a rule could
split, a tie had to send everything one way; an arbitrary tie-break piled a whole
region onto one rally point while its neighbour idled, so the plan picked whichever
point was drawing fewer ships per turn, tracking which point each tied system's ships
really reached. Splitting makes all of that unnecessary.

Planning **nearest-first** is still what makes a hop's usefulness known: a hop is
always strictly nearer, so it has already been planned, and only hops that lead
somewhere (a rally point, or a planned system) get a share. The no-cycle guarantee is
untouched: every lane of a split steps strictly nearer, so the potential still
decreases along every edge.

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
panel's Clear forwarding buttons are suppressed too — they would mutate
`auto_forward` underneath the preview.

**A plan sends everything down one lane, and keeps any hold the system had.** A
route is a conveyor, so each hop is a 100% share; a split the plan crosses is
replaced and reported as replaced (`route_replaces`), even when one of its lanes
already agreed. The hold survives any replace, whichever way the hop now points:
it is about the system's own safety, not about a lane, so a route has no business
resetting it — and re-running a route over a conveyor that is already correct
stays idempotent rather than quietly wiping tuning. Cycle detection treats every
lane with a share as an edge (`model.cycle_nodes`): a loop down one lane of a
split traps that share as surely as a whole rule would.

**One consequence worth knowing.** With whole-path hold 0, losing a mid-chain
system leaves its upstream neighbour forwarding its entire garrison into enemy
territory every turn: `prune_forward` drops the captured system's rule, but the
upstream one is still live and `rule_is_live` doesn't care who owns the far end.
A hand-made rule has always had this property, but the player made *one*, on
purpose. Hence `_draw_forward_rules` tinting any rule aimed at a system we don't
hold — it is a real move as well as a real accident, so it is flagged rather than
prevented.

## Forwarding rules: a hold and a share per lane (`model.ForwardRule`)

A rule used to be one lane and a `keep`: send everything above N. "Keep half" set
N to half the garrison *at the moment you pressed it*, and the caption under it
("~7/turn") was the garrison at that moment too, so both went stale as the system
grew; in practice the player kept 0 and only touched `keep` for a turn or two
against a small inbound fleet. Now a rule is the system's `hold` (ships kept back,
the old keep) plus a whole-number percent per lane of whatever is left. Every old
rule converts exactly: keep N is hold N with 100%.

**The hold belongs to the system, the share to the lane.** A switch per rule
between "keep N" and "send N%" was mocked up and rejected: "keep N" really means
"send the rest", so a system could only have one such lane, every other lane had
to be a percent, and the switch had to explain that. Splitting the two quantities
by what they are about removes the switch altogether.

**Adding a lane splits evenly; removing one undoes it.** `model.shares_with` gives
the new lane an even cut of what is being sent and shrinks the others in
proportion, so 100 becomes 50/50 becomes 34/33/33, a lone 50% becomes 25/25 (the
other half still stays home), and 70/30 becomes 47/20/33. `shares_without` is its
inverse — an even split re-splits evenly, anything else hands the freed share back
in proportion — so Cancel on a lane just added, or going back to the Send tab,
leaves the others as they were. A rule sending nothing at all splits 100% when a
lane joins, or the new lane would be born idle.

**Splitting is for this turn's rule; an older one is replaced.** Adding a lane
splits only a rule set up this turn (`Ui.forward_set_on`, stamped when the popup
arms a lane or a route is confirmed). A rule from an earlier turn is what the
player set up *then*; a new lane on a later turn almost always means "send here
now", so it replaces the whole rule with that lane at 100% and hold 0. The old
rule is kept aside (`Ui.forward_replaced`) so Cancel or the Send tab puts it back.
A system whose rule was set up this turn also opens the popup on the Forward tab
(`Ui.opens_on_forward`), as an empty one does: the next lane is most likely
another split of it.

**A lane taken to 0% is deleted.** A lane sending nothing is not a rule, so when the
lane the popup is on reaches 0% — by −, the wheel or the slider — `Ui.set_share`
deletes it exactly as the bottom button would, taking the rule (and closing the
popup) with its last lane. A *different* lane emptied by another's rise stays, as a
0% row: deleting it then would make a + on one lane silently remove another.

**+ never hits a wall.** With adding defaulting to 100% split evenly, the shares
nearly always total 100, so a + that stopped at 100 would be dead most of the
time. `shares_set` takes a rise from what stays home first, then from the other
lanes in proportion; − gives back to staying home. There is no "full" state and no
message about one.

**One formula for what goes.** `forward_split` rounds by largest remainder with
what stays home as a share of its own, so the lanes sum to exactly their share of
the surplus. A ship that could go either way rotates with the turn, among the
shares that are actually sending — a 50/50 split of 5 alternates 3/2 and 2/3, and a
lone 50% of one ship sends it every other turn. `Ui.forward_this_turn` applies it
to the free ships beyond the hold, and is the only thing that does:
`main.auto_forward_orders` issues it, and every number on the popup and panel
("5 go · 7 stay", "Forwarding 50% · 5 go", "-> Alcyone (22) 50% · 5") reads it.
Those numbers are exact rather than estimates because orders resolve against the
start-of-turn garrison. Dict order is part of a rule (ties go to the earliest
lane), so the log keeps lanes in the order they were added.

**The popup has three shapes.** The Send tab is unchanged. A Forward tab with one
lane edits that lane's share the way the Send tab edits a count — slider, −/+
(`config.FORWARD_STEP_PCT`), 50%/100% — plus a hold row. With two or more lanes it
becomes the system's split panel (`config.SPLIT_POPUP_W`): a row per lane with its
own −/+, what stays home, the split as a bar, Even split and Delete all. The row
for `ui.dest` is outlined; it is what the bottom button, the wheel and the map
highlight act on, and a tap on another row (or another lane on the map) moves it.
The panel is not a fixed size any more — a split grows with its lanes — so the
anchor is chosen for the taller single-lane shape, and Send/Forward flips on one
lane don't make it jump. On a screen too short for a split even at the label-height
floor, it sheds the bar, then the "stays" row, then lanes, keeping a window round
the one being edited with a "+N more · tap a lane" row; the rest are a tap on their
lane away.

**On the map, the hold sits under the system.** Each lane is labelled with its
share when that is less than 100%, near the source; a held system gets one
"hold N" badge under its disc, where its star name would go, and the name moves
down a slot (`_hold_badge`, `name_floor` in `_draw_node_names`). Putting the hold
on the lane labels instead repeated it on every lane of a split, where the labels
crowded round the source and one lane's "hold 2" ran into the next lane's "50%".
A share label that would land on the badge tries the lane's other side, then
further along it.

**Names fit by the same three steps everywhere they compete for room.** "Vega (7)"
where it fits, "Vega" where only that does, "7" otherwise: `_fit_route` for the
popup title (both ends together, so a title never mixes forms), `_fit_names` for
the split panel's column (the whole column at once, so "Bodu (14)" never sits over
a bare "Alcyone").

## The web build's download (`tools/slim_web.py`, `sw.js`)

Before the first frame a visitor downloads pygbag's runtime (CPython and
pygame-ce compiled to WebAssembly, from the pygame-web CDN under a versioned
path) and the game's own archive (`/game/starconquest.tar.gz`), then the
"Loading, please wait" box stays up while Python compiles the game from source.

**The archive was mostly things the game never reads.** Measured on the deployed
build (2026-10-28): 938 KB gzipped, of which the two DejaVu Sans Mono files were
404 KB and comments and docstrings about half of the 538 KB of Python.
`tools/slim_web.py` runs on the stage dir before pygbag packs it, and takes the
archive to 427 KB:

- **Comments and docstrings go, every line stays where it was.** A docstring
  becomes an empty string continued over its lines, so a browser traceback still
  names the repository's line numbers. Each file's syntax tree must equal the
  original's with docstrings emptied, or the file ships unstripped (with a
  warning); `tests/test_slim_web.py` holds every shipped file to that, and the
  full suite passed against a stripped copy of the tree. Nothing reads
  `__doc__`. Compile time is unchanged (measured natively): the gain is the
  download only.
- **The fonts keep Latin, Greek, Cyrillic and common punctuation and arrows,
  plus every character in the shipped source, with their hinting** (203 KB to
  about 66 KB gzipped each). Kept glyphs render byte-identically; a character
  outside the set draws as the font's box, so a new non-ASCII label is covered
  automatically, and a new script someone may type belongs in `KEEP_RANGES`.
- `models/README.md` is not shipped: it is for bot authors.

**The service worker caches the runtime too** (`RUNTIME` in `tools/pwa/sw.js`):
the same stale-while-revalidate as the game's own files, CORS responses only.
Before, every cross-origin request passed through, so a returning player waited
on the CDN to revalidate each runtime file (GitHub Pages allows ten minutes)
and the game could not start offline. A pygame-web fix published in place
under the same version reaches a player one visit late, as a redeploy of the
game already does. Checked in Chromium with a stand-in CDN: the runtime loads
from the cache with the CDN down.

**Not done.** The runtime's own size is pygbag's, not ours. Shipping `.pyc` to
skip compiling: pygbag's packer drops `.pyc` (`filtering.SKIP_EXT`). Importing
modules lazily to start sooner: the whole game imports and loads its bots in
about 0.2 s natively, small beside fetching and compiling the runtime.

## Measured layout and `config.touch_ui`: the rules in full

- **Nothing that holds text gets a fixed pixel size.** The measured-layout kit
  lives in `widgets.py` and is shared by every scene that draws on the real
  surface: a button's width comes from its measured label (`widgets.btn_w`, and
  `widgets.btn` draws + returns the hit-rect), a stacked text row's pitch from
  the font's own line height (`widgets.row_h`), a modal's stack is measured then
  centred (`widgets.draw_modal`), and help prose is reflowed to the panel it sits
  in (`widgets.wrap`). One-off layout literals still go through `config.s()`.
  `render` binds these to its own `_`-prefixed module globals rather than calling
  them qualified, and that is load-bearing: the body resolves them as bare names
  at call time, which is what lets a test swap one out (`render._text = spy`) and
  see the drawing code use it, and what keeps `render._FONTS` the same dict a
  test clears. **`menu` deliberately does not use the kit** — it lays out on a
  fixed 1440x960 canvas (a compact screen's own shape, see "The compact
  layout") and scales the canvas, so its fonts must be *unscaled*
  (`config.FONT_SIZE*` would scale twice) and it keeps its own `_fonts`/`_text`/
  `_button`.
  **The scale follows the window and the player.** `main.ui_scale_for` fits the
  design baseline into the surface (floored at 1x, so shrinking the window past
  the baseline does not shrink the UI), then applies the touch boost or, off
  touch, the player's UI size (`webstore.ui_size`, a percentage stepped from
  `webstore.UI_SIZES`; the menu's bottom-right stepper and Ctrl +/−/0 in every
  scene set it through `menu.set_ui_size`, and `MenuState.ui_size` is the copy
  `main` reads each frame). `main` calls `apply_ui_scale` before the first frame
  and again from the loop's reflow whenever the surface size or that scale
  moves; the reflow rewrites `config.SCREEN_W/H`, rebuilds the `WorldView` and
  resets the camera, and reflows the map creator. The scale used to be fixed at
  boot, which left the board at 1x in a window opened at the baseline and then
  maximised, while the letterboxed menu grew with it. A UI size is a local
  preference, not a `Settings` field, for the same reason `animate_turns` is.
  Cache off a font size only if it is keyed on `config.ui_scale`
  (`widgets.fonts`, `menu._modal_fonts` both are), since the scale can now move
  mid-run.
- **`config.touch_ui` is the input modality**, set beside the scale in
  `apply_ui_scale` from `main`'s single boot-time probe (Android, or a touch
  browser). On a touch build the shell drops every keyboard-only string — the
  `(Esc)`/`(R)` suffixes on button labels (`render._key_hint`,
  `render.confirm_labels`, `menu._resume_labels`), the shortcut lines in the info
  panel's help text and the win overlay, the menu's `Enter: start game` footer —
  and floors tappable controls at `config.TOUCH_MIN_TARGET` (`render._tap_size`).

## The compact layout: the rules in full

- **`config.compact` is set by `apply_ui_scale`**, from the surface it is
  given: a touch build whose screen is under `COMPACT_MAX_W` x `COMPACT_MAX_H`
  baseline units. Never on a desktop window. `portrait` is compact and taller
  than wide. The `COMPACT_*` sizes (smaller systems, a shorter End Turn) are
  swapped in there, scaled like every other constant.
- **A touch browser's scale is in CSS pixels**: `main.ui_scale_for` takes
  `web_css_px`, the framebuffer's device pixels per CSS pixel. Off the web it
  is None and the old fit-the-device-pixels rule stands (Android). It is at
  most `TOUCH_CSS_SCALE`, and less where that would leave the screen under
  `TOUCH_MIN_W` x `TOUCH_MIN_H` baseline units.
- **The compact menu's settings panel is the desktop's width** wherever the
  screen has it, so no tab stacks its columns; the narrow single-column
  fallbacks (`menu._narrow`) are for a screen below the scale's floor. The
  Basic tab fits without a scroll (`test_the_basic_and_advanced_tabs_need_no_scroll`).
  In a panel that scrolls, a press on a slider waits (`MenuState.slider_press`)
  until it moves past `_C_SLOP`: up or down scrolls, across drags, and a
  release where it began is a tap.
- **The game screen's regions come from `config`**: `play_rect`,
  `side_panel_rect`, `footer_rect`, `end_turn_rect`. Nothing in `render` or
  `input` derives one from `HUD_RIGHT_W` or the bars directly.
- **The footer never drops a control**: `render._lay_out_footer` folds what
  doesn't fit behind `Ui.more_button_rect`, and an open list records each
  folded button under its own attribute (`viewstate.FOOTER_RECTS`), so input
  tests it as on the strip. Any press closes the list (`input._more_press`).
- **A docked popup owns its presses** (`render._popup_docked`): the panel
  under it draws nothing and records nothing, a portrait sheet takes End Turn
  and the strip away (and lifts the zoom buttons onto its top edge, as
  history's graphs do), and input tests the popup's controls before End Turn
  (`input._popup_press`), since a landscape panel too short for it lets it run
  on down over End Turn.
- **On touch, closing the popup lets its source go** (`input._cancel`), and a
  tap on another system while it is open selects that system rather than
  sending to it. Desktop is unchanged.
- **A press goes to the topmost control.** The compact layouts draw things over
  other controls (the menu's Daily list over Start Game; in portrait, the
  docked popup over the map's zoom buttons), so `menu._handle_click` tests its
  rects newest-drawn first, and input tests a docked popup before the HUD.
  The desktop menu has no overlapping rects, so the order changes nothing there.
- **A pinch undoes its first finger's press** (`Ui.press_undo`, `Editor.press_undo`)
  and holds the emulated mouse off (`pinch_lock`) until every finger is up.
- **The compact menu draws widgets as the desktop one does**, through the same
  drawers on a smaller canvas; a tab draws onto a layer whose used height is
  measured, and its rects are moved and clipped to the window
  (`menu._compact_settings`). Modals are drawn on a design-sized layer and
  scaled to fit (`menu._fit_modal`). The map creator's sidebar scrolls the same
  way (`mapmaker._draw_side`), and its footer takes a second row before it
  sheds a control.

## Browser bridges (`softkeyboard`, `webstore`, `share`)

- `softkeyboard.py` is a browser-only bridge, not a pygame module: on a touch
  browser it focuses a hidden DOM `<input>` so the mobile on-screen keyboard
  actually appears (SDL's `start_text_input` has nothing to focus there) and
  reports back what was typed. Everything is guarded — off the web build, on a
  desktop browser, or if any DOM call fails, every function is a no-op and the
  menu keeps its plain SDL text path.
- `webstore.py` is the other browser bridge, same style: the address bar and a
  small key/value store (shared-settings tokens, personal bests). See the
  challenge-link notes in `CLAUDE.md`, Key conventions.
- `share.py` is the one bridge that talks to a network, and the one that is
  not browser-only: it posts a finished match's replay to the leaderboard's
  `/api/log` (same-origin on the web) so a
  posted score can be checked against the game that produced it. Same
  defensive style — guarded everywhere, silent on failure,
  fire-and-forget on both backends (a `fetch` whose promise
  is never read on the web, a daemon thread off it) so it can never stall the
  frame. Pure of pygame, and it sends **only** when the player presses *Post to
  leaderboard* or the player has ticked *Share replays*. Its other half
  *fetches* a replay to watch (`fetch_log` -> a `Download` the loop polls once a
  frame, never awaited), which is the one thing here that reads a response. See
  "Checked scores" in [`leaderboard.md`](leaderboard.md).

- **`webstore` is the third browser bridge** (with `softkeyboard`, the web-only
  paths in `main`/`menu`, and `upload`, which is the one that also runs off the
  web): `get`/`set` are `localStorage` on the web and
  a JSON file under `data_dir()` elsewhere. The rest is genuinely web-only and
  no-ops off it: `link_url`, `set_url_fragment`, `copy_to_clipboard` and
  `url_token` are the primitives, and `sync_settings` / `share_token` /
  `copy_link` the compositions callers use. Same defensive style as
  `softkeyboard`: local `import platform`, every DOM call guarded, storage
  failure never load-bearing.
- **Quitting is a desktop concept; the web has nothing to exit to.** Every
  confirmed quit goes through `main.leave_app()`: off the web it returns True
  and the loop ends, while on the web it asks the browser to close the window
  (`webstore.close_window`) and returns False, falling back to the setup menu
  with `main.CANT_CLOSE_MSG` via `menu.set_status`. Never end the loop
  (`pygame.quit()`) directly on the web build.

## Send popup and spectating: the rules in full

- **The send popup is the *only* ship-count editor.** Composing a new send opens
  it (`Ui.begin_send`), and so does reopening an already-queued order or
  standing rule — `Ui.edit_order` / `Ui.edit_forward` put the popup back into
  `CHOOSING` aimed at that subject. `Ui.editing_existing` records that the
  subject *predates* the popup, and drives the bottom button (Cancel vs
  "Delete order"/"Delete rule") and `_close_send`'s unwind to `IDLE` on edit.
  - **A dormant rule highlights but never opens it** (`Ui.rule_is_live`): the
    popup reads the source's garrison and destination unguarded, and aiming it
    at a system we no longer hold would let the Send tab queue an order out of
    enemy territory. Dormancy only lasts the turn — `Ui.prune_forward`
    (`main.resolve_turn`) deletes a rule whose source was taken, so it can never
    come silently back to life on recapture.
  - **The count slider must be claimed before the popup's drag fallthrough** —
    `slider_rect` is hit-tested first, and `dragging_slider` checked ahead of
    `dragging_popup` in the MOUSEMOTION chain. Both halves tolerate `lo == hi`
    (an empty source on the Send tab) and a zeroed rect (popup closed
    mid-drag). On the Forward tab it sizes a share, 0-100% snapped to
    `config.FORWARD_SNAP_PCT`, whatever the garrison.
- **A forwarding rule is a hold plus a share per lane** (`model.ForwardRule`),
  and the share arithmetic is `model`'s alone (`shares_with`, `shares_without`,
  `shares_set`, `shares_even`, `forward_split`). The shell never adds percentages
  up itself.
  - **`Ui.forward_this_turn` is the one formula** for what a rule sends. Orders
    (`main.auto_forward_orders`) and every number the popup and panel show come
    from it.
  - **Arming the Forward tab adds a lane with `shares_with`; Cancel, Delete and
    the Send tab take it out with `shares_without`**, so trying the tab and
    leaving puts the other lanes back. A system's last lane takes its rule with
    it (`Ui._drop_lane`).
  - **A pick on the map names a lane** (`("rule", (src, dest))`), and
    `Ui.edit_forward(sid, dest)` reopens that lane; on a system already open it
    only moves the focus.
  - **Split rows record `ui.share_rows`** as `(dest, row, minus, plus)`; −/+ on a
    row focus that lane before stepping it.
- **Orders and rules are reached from the map, not from a list.** A click near
  a lane picks what is drawn on it, and repeat clicks cycle through everything
  sharing the lane (`input._pick_lane`); the popup it opens holds the Delete
  button. The side panel used to end in a list of every queued order and rule,
  capped and scrolled. It went once the popup opened on edit as well as on
  create: the popup already showed everything a row did and put Delete in easy
  reach, and the list was noise that still ran into the system details above
  it. What nothing replaces is a glance at orders queued off-screen; a count
  under End Turn would cover that if it is ever missed.
- **Losing makes the human a spectator, not a blind one.** `fog.observe`
  returns empty for a landless player, so `main._accumulate_fog` reveals the
  whole board (dropping frozen `player_intel`) once
  `GameState.is_defeated(human_id)` — fixing history mode and a resumed game
  for free since both fold fog through that one helper. Its companion is
  **fast forward** (`Ui.can_fast_forward`, `main.step_delay`, the F key /
  footer button): `main.FAST_FORWARD_MS` replaces the autoplay/play delay so
  the rest of a lost match resolves a turn per frame. Offered only while
  spectating.

## Map viewport margins: the rule

- **The map viewport has two margins, both floored at `config.node_clearance()`.**
  `config.map_fit_padding()` sizes the zoom-1 fit and `config.map_pan_padding()`
  is what the pan clamp keeps past the outermost system once zoomed in —
  `geometry.WorldView` takes them as `padding` and `pan_padding`.

## Combat tab: the rules in full

- **The menu's Combat tab teaches the square law from the real code.**
  `combat.preview_fight` sits beside `resolve_fight` and shares its
  `_apply_advantage`/`_resolve_effective`/`_survivors` helpers, so the page
  cannot drift from the fight it predicts (pinned by a zero-jitter equivalence
  test). It takes `jitter`/`advantage` as **parameters and reads no `config`** —
  those only reach `config` at game start via `settings._apply_globals`, so
  reading them would preview the previous game's balance — and it **draws no
  rng**, keeping `menu.draw` a pure read. `best`/`worst` are the corners of the
  jitter square, not samples, so they really do bound the outcome.
  - **The demo sliders are the one group that writes `MenuState`, not
    `Settings`** — the third `kind` in `_SLIDER_SPECS`, routed in
    `_apply_slider`. A scratch calculation has no business in a save file or a
    share token, and on a challenge link it would raise the un-challenge modal.
    `_ADV_COMBAT` moved tab but *not* namespace: still `adv_`-keyed, still
    writing `Settings`, just drawn beside the demo it governs.
  - **This one page hand-breaks its prose instead of reflowing it.** The measured-layout rule
    ("Measured layout" above) exists because the *font* scales; the menu canvas is fixed, so a
    runtime wrap would instead make the page's height depend on its text and
    silently overflow the panel. `test_tab_content_stays_inside_the_panel`
    guards every tab's rects against that 560x496 box. The exception is a
    phone's panel, which scrolls (its height is free) and is narrower than the
    hand-broken lines: there a line that doesn't fit is reflowed.

## Route mode: the rules in full

- **Route mode (`viewstate.ROUTING`) is the one control that doesn't commit as
  you go.** It builds a *proposal* — `route_sel` (plus `route_dest` in chain
  mode), recomputed by `Ui.recompute_route` into `route_plan` — which
  `confirm_route` writes into `auto_forward` in one go.
  `input._handle_route_event` takes the whole event stream (placed after the
  game-over branch), and render swaps the footer strip and the End Turn button,
  so nothing from live play stays clickable under an open plan.
  - **Two sub-modes, one plan** (`Ui.route_rally`, the footer's Mode button and
    Tab). Both seed the same `model.flow_field` search (the one
    `ai._flow_to_frontier` also delegates to) over our own territory and share
    `_add_hop`, `_detect_route_cycles` and the confirm, so they differ *only* in
    the seeding: **chain** seeds the one destination and `_plan_chain` walks each
    selected system's path to it; **rally** seeds every pick at once and
    `_plan_rally` takes the returned field whole, so every owned system it
    reaches forwards toward its nearest rally point. Both measure "nearest" in
    **travel turns**, not hops (`flow_field(by_turns=True)` / `flow_costs`) — the
    same `state.travel_turns` rule the rest of the game follows. The AI keeps the
    unweighted default, which is why the flag exists rather than a changed
    default. Rally splits a genuine tie evenly down every equally quick hop,
    planning nearest-first so a hop's own route is already known.
    `Ui.auto_rally` (rally's Auto-route button, `T`) picks every
    `threatened_systems` — the shell's own local copy of the AI's threat maths,
    per the render/input rule against importing `ai`. Every hop of every path gets
    a rule, not just the selected systems. Owned-only is *forced*, not chosen: a
    rule can only live on a system we hold, so a path through enemy space cannot
    be expressed. The **sinks** are exempt (`flow_field` seeds need not be in
    `allowed`), which is what lets either sub-mode be aimed at an enemy front.
    The sub-mode is a preference, so `reset_route` leaves it alone while clearing
    everything else; `set_route_rally` drops the proposal, since `route_sel`
    means sources in one and sinks in the other.
  - **A drag boxes a group; a tap always aims** (`Ui.route_tap`, chain mode).
    Aiming is never destructive — the destination stays in `route_sel` and is
    merely skipped as a source (`Ui.route_sources`), so re-aiming hands it
    straight back. Removing is the *second* tap on whatever you are already
    pointing at. Never give a tap a second primary meaning conditional on the
    system: that is what made aiming at one of your own picks silently drop it.
    A rally tap is a plain membership toggle, which is one meaning rather than
    two, and frees drag for panning.
