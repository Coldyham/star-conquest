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
than the design baseline (`config.apply_ui_scale` runs once at boot and is
floored at 1x, so shrinking past the baseline doesn't shrink the UI — a resize
reflows the measured layout but never re-fits the font scale). Because the
clamp pins an oversized panel to the top, the row that falls out of `draw`'s clip is the
destructive Delete — invisible but still live, since input hit-tests the
recorded rect.
Hence the popup shrinks its rows to their labels first, against a budget
measured from the placement band rather than the viewport.

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
  fixed 1440x960 canvas and letterbox-blits it, so its fonts must be *unscaled*
  (`config.FONT_SIZE*` would scale twice) and it keeps its own `_fonts`/`_text`/
  `_button`.
  **The scale is, however, fixed for the run:** `config.apply_ui_scale` is called
  exactly once, inline at boot (`main.py`, after `set_mode`), floored at the
  design baseline — so shrinking the window past the baseline does not shrink the
  UI. A resize rewrites `config.SCREEN_W/H` and rebuilds the `WorldView`, so
  measured layout reflows, but the font size does not move. Cache off a font size
  anyway only if it is keyed on `config.ui_scale` (`widgets.fonts`,
  `menu._modal_fonts` both are) — the boot-time call happens before the first
  frame, but a cache built at import time would still be wrong.
- **`config.touch_ui` is the input modality**, set beside the scale in
  `apply_ui_scale` from `main`'s single boot-time probe (Android, or a touch
  browser). On a touch build the shell drops every keyboard-only string — the
  `(Esc)`/`(R)` suffixes on button labels (`render._key_hint`,
  `render.confirm_labels`, `menu._resume_labels`), the shortcut lines in the info
  panel's help text and the win overlay, the menu's `Enter: start game` footer —
  and floors tappable controls at `config.TOUCH_MIN_TARGET` (`render._tap_size`).

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

## Send popup, queued list and spectating: the rules in full

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
    (an empty source, the touch default) and a zeroed rect (popup closed
    mid-drag).
- **The side panel's queued list is capped and scrolled, not truncated.** It
  takes at most half the panel, and what doesn't fit is reached with
  `ui.order_scroll` (▲/▼ buttons, or the wheel while over the panel). Each
  drawn row carries **its own index** into `pending` (`ui.order_hitboxes` is
  `(index, row, delete)`) — a positional mapping would silently delete the
  wrong order once only a window of the list is on screen.
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
    guards every tab's rects against that 560x496 box.

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
    default. Rally splits a genuine tie toward whichever point is drawing less,
    measured in ships/turn (`1 / production`, as `fog.player_totals` reports),
    assigning nearest-first so each node's real destination is already known.
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
