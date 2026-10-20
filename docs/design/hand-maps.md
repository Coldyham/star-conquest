# Hand-authored map design notes (`custommap.py`, `mapmaker.py`)

Why the map creator and the recipe it writes are shaped as they are. It covers
the recipe on `Settings`, the editor's model, the geometric rules, the tools'
gestures, seats, what the menu hides, the wider box, and the blank canvas.
`CLAUDE.md`'s "Hand-authored maps" states the rules. Related:
[`core.md`](core.md) (challenge keys and `_LEGACY_KEY_DROPS`). Index:
[`../README.md`](../README.md).

## Hand-authored maps (`custommap.py`, `mapmaker.py`)

The rules are in CLAUDE.md, keyed by the matching heading. What follows is the
reasoning and the measurements behind them.

### Why the recipe lives on `Settings`

Everything a shared map has to survive — a save file, a settings link, a challenge
link, a resume, a replay, the leaderboard's `settings_json`, the offline bot
column — is already plumbed for `Settings`. Putting the map anywhere else means
building a second copy of all of it, and building it *worse*, because a replay's
reproducibility then depends on two artefacts staying in step instead of one.
The alternative considered and rejected was a map *file* referenced by name: it
makes a link un-shareable (the recipient has no such file) and a replay
un-replayable the moment the file is edited, which is the exact failure mode
format version 1 already taught us about re-running bots.

The cost is one `_LEGACY_KEY_DROPS` entry. Verified by running it rather than by
reasoning — today's chain was

    ('770ba09210f6127a', 'a61a1888857255e8', '7b989c6085320172')

and with the field added it is

    ('38c8b7ba470f6f4c', '770ba09210f6127a', 'a61a1888857255e8', '7b989c6085320172')

so every digest already in circulation is recovered, in order. A setup that *has*
a map offers exactly one key, since `custom_map` appears in every entry — correct,
because no version lacking the field could have described such a map.

### Why the editor's model is a recipe, not a `GameState`

This was the other way round in the first draft, and it is worth recording why it
flipped. `Lane.length_ly` and `Lane.travel_turns` are **stored** fields, written
by `mapgen._add_lane`, cached *again* into `GameState.adjacency` by
`rebuild_topology`, and shipped to external bots as `base_turns`. Dragging a node
in a live state therefore means recomputing every incident lane's length *and*
its turns *and* the topology cache, and missing any one of the three leaves a
board that lies about itself. With positions plus index pairs there is nothing to
maintain: the lanes follow their nodes, and the one derivation happens once, at
build time, in `_add_lane_between`.

Two more things fell out of it. `GameState.players` must hold a `Player` per owner
id, so painting seat 5 onto a 3-player state breaks `_draw_scoreboard`,
`fog.observe` and `is_defeated` — a recipe just stores the number. And undo
becomes a copy of plain data rather than a deep copy of a live simulation.

The one cost is that node identity is positional, so deleting node *i* shifts
every later lane index. That is why `CustomMap.without_node` exists and why
nothing else is allowed to open-code a deletion.

### The geometric rules, and why they can be hard blocks

Both of the editor's positional rules are `mapgen`'s own — `_crosses_any` and
`_grazes_other_node` — so a hand map is held to the standard a generated one
already meets. Measured over 40 seeds x {12,18,24,40} nodes x {random,symmetric} x
{2,4,6} players:

- **zero lane crossings and zero grazes** in every generated map, so both can be
  hard blocks without a loaded generated map ever lighting up; and
- the **tightest node pair mapgen ever produces is 53.6 world units**, which is
  what fixes `CUSTOM_MIN_NODE_SEP_FRAC` at 0.045 (45 units) — deliberately equal
  to `LANE_NODE_CLEARANCE_FRAC`, so one clearance figure governs both node-vs-node
  and node-vs-lane. An earlier draft proposed 70, which would have flagged
  violations on load. `test_mapgen.py`'s sweep is the regression guard.

A drag shows its refusal rather than preventing it: the node follows the cursor
the whole way and the offenders ring amber, and an illegal release **snaps back**
to where the drag started. Not clamped to "the nearest legal point" — with several
constraints live at once that point is ill-defined, and it silently puts the
system somewhere nobody asked for.

### `CUSTOM_MAX_NODES` is not `MAX_NODES`

`generate_symmetric` rounds the node count up to a whole number per sector and
adds the shared centre, returning `players * round((nodes-1)/players) + 1` — **41
systems at 40 nodes**, which is a board the game generates and plays today.
Capping the recipe parser at `MAX_NODES` would therefore make a perfectly ordinary
generated map un-importable into the creator. The two ceilings are different
things and now say so: `CUSTOM_MAX_NODES` bounds a malformed blob, while
`MAX_NODES` is the *authoring* ceiling the editor enforces on placement.

### Why Auto-lanes replaces rather than merges

`mapgen._planar_edges` always builds a full MST, so merging its output into a
hand-drawn set would quietly reconnect a bottleneck the author put there on
purpose — the one structural decision a hand map exists to express. Replacing is
honest about what it does, which is why it is behind a confirm. Its output is
planar and graze-free by construction, so it can never produce a map the manual
rules would then refuse. It reads `EXTRA_EDGE_FRACTION` and `MAX_EDGE_LENGTH_FRAC`
live off `config`, so the button goes through `settings.apply_globals` first —
`mapmaker` never writes `config` itself.

### Why a crossing warns and a graze blocks

The decision table said "block both", and Phase 1 shipped both as blockers — then
Phase 2's Planar toggle made the inconsistency obvious. A checkbox that lets you
draw a map the Play gate then refuses is worse than no checkbox, so one of the two
had to move, and the plan's own ripple analysis had already settled which: "a hand
map may carry crossing lanes ... no rule cares about planarity."

So the two rules are now separated by what they actually cost:

- **A lane under a third system** misrepresents the graph — it renders as if
  hidden behind that system, so you read the map wrong. Blocks, always.
- **Two lanes crossing in open space** costs nothing but tidiness. The engine, the
  AI, `fog`, `turnfilm` and every bot read the graph and never the geometry.
  Warns.

The **Planar** toggle (default on) then does its job at *draw* time, refusing to
lay a crossing lane, rather than at validation time. Default-on means the common
path never produces one; turning it off leaves a map that still plays, still
parses and still shares, which is the only arrangement where the toggle is
coherent. `problems()` sorts blockers ahead of warnings so the Play gate's
`blockers()[0]` is never buried under one.

### Two gestures, one commit path

Lane drawing offers a tap-then-tap and a drag, because neither alone is right for
both input modalities — a drag is natural with a mouse and awkward on a phone at
zoom, a tap pair is the reverse. They share one armed source (`Editor.lane_src`)
and one `_add_lane`, so they cannot diverge: a test asserts both gestures produce
byte-identical lanes.

Systems are picked before lanes, and that ordering is load-bearing rather than
arbitrary: a lane's endpoint is *inside* its system's tap reach by construction,
so testing lanes first would make it impossible to start a lane at a system that
already has one.

Left-drag pans here but not in the Systems tool. That asymmetry is deliberate —
in Systems a press on empty space always means "place", so there is no free
gesture to spend, and route mode already records what happens when one press is
given a second meaning conditional on the target.

### Why Systems still has no deselect gesture

Lanes and Owners both drop `sel_node` on a press that misses every system — added
once testing turned up that neither tool had *any* other press that could clear a
selection carried in from Systems, so the ring (and the sidebar block behind it)
had nowhere to go. Systems was left out on purpose rather than by oversight: a
press on empty space there always means "place" (see above), so giving it a
second meaning conditional on where it lands is exactly the trap route mode's tap
already documents, and it isn't needed anyway — placing a system selects it, so
the ring there is never inherited stale from another tool the way it can be after
switching tabs.

### Why the seat rows became one control rather than the tap becoming select-only

Testing turned up the same friction from the opposite end: with a system already
selected, pressing the Owners palette band appeared to do nothing to it, so the
only way to recolour it was the sidebar's own row — or painting the *next* system
the wrong seat and fixing it up. Two designs were on the table. One left the map
tap alone and made a press on the band merely *select* whatever it's pointed at
(closer to how a tool palette often behaves elsewhere); the other made both rows
stamp the selection, matching what the production palette already does one
column over. The second was already proven code: `pal_*` sets `ed.pick` **and**
calls `_retype_selection`, precisely because a swatch that leaves a selected
system looking untouched reads as broken, not indifferent. Doing anything else for
seats — the one tool where two identical rows sit side by side — would have made
"press a swatch" mean two different things depending on which one your hand
reached for. The one-meaning-per-tap rule this book keeps citing (route mode,
Owners' own map tap) is about a *map* press choosing between two competing
interpretations at the same coordinate; a press on chrome that already has a
single, stated job was never in tension with it, so unifying the rows costs
nothing that rule was protecting. Neutral is still exempt from the toggle on
either row, for the same reason it always was: the swatch already exists, so a
second meaning on the seat a system already holds is one meaning too many.

### Why auto-relane skips a drag

`Editor.auto_relane` re-runs the network on a system placed or removed, but
deliberately not on one dragged into place. A drag is live and continuous —
`_handle_motion` moves the node every frame the press is held, legal or not, and
shows the refusal as amber rings rather than blocking the motion (see the
snap-back rule above) — so rebuilding the whole lane set on every frame of that
would fight the rubber band the node is already following, and would burn through
`mapgen._planar_edges` far more than the gesture needs. A placement or a deletion
is discrete: one edit, one rebuild, folded into that edit's own undo step exactly
the way `_add_lane` already folds into a chained placement's. The lanes a drag
leaves behind are not wrong, either — they're the pre-drag network, exactly as
stale (or as current) as they were before the system moved, and the next add or
remove catches them up.

### Seats: gap-free by construction, never by force

The owner palette offers `n + 1` seats — one more than are currently held, floored
at two — which makes the ordinary path incapable of leaving a gap: the next seat
is always reachable and the one past it never is. It does not make a gap
*impossible*, since you can paint seat 3 and then clear seat 2, and that is where
the interesting decision was.

Silent compaction was considered and rejected. Renumbering changes a seat's colour
without being asked, and on a map where you have deliberately given red the two
systems behind the ridge, the colour is part of what you authored. So the gap gets
a blocker that says exactly what is wrong and a one-press **Renumber seats** that
fixes it — undoable, like every other edit.

The same instinct governs painting: a seat colour never rewrites the numbers.
**Make homeworld** is the explicit version, and it lives in the Owners sidebar so
the common "give this one a real garrison" case is not a round trip back to the
Systems tool.

### What the menu does with a hand-drawn map

`Settings.players` and `Settings.nodes` stop being inputs once a recipe is set —
they are derived from it, and `from_dict` reconciles them on the way back in. The
menu therefore *reports* them instead of offering them, and does it by **not
drawing the control at all**: `_draw_menu` clears `ms.rects` every frame, so an
undrawn control is inert by construction rather than by a disabled flag some later
branch forgets to check.

`_set_players`/`_set_nodes` are interlocked on top of that, which is belt and
braces on purpose — the steppers are gone, so only a caller that is *not* the
stepper can reach them, and the cost of one getting through is a setup whose
digest no longer matches its own map until the next decode quietly reconciles it.

Advanced's Map and Economy groups go the same way, for a reason specific to this
design: every production and garrison on a hand map is concrete, rolled at the
moment a system is placed, so those knobs only bite inside the creator — which is
where they now are. Had garrisons stayed sentinels resolved at build time, the
sliders would have had to stay live on the menu.

**Seed stays**, and that is worth stating because the original sketch had it as
redundant. With the layout fully concrete the seed no longer shapes the map — but
it still drives every combat roll and every star name, so it is as load-bearing as
it ever was.

### Why a hand map gets a wider box than a generated one

`WORLD_SIZE` is square and `mapgen._place_nodes` jitters its grid inside it, so
every generated board is square — which the creator, fitting that box
aspect-preserved into a 16:9 window, faithfully rendered as a small square with a
third of the screen dead either side.

Widening `WORLD_SIZE` itself was the obvious fix and is the expensive one: every
seed would lay out a different board, which is a `RULES_VERSION` bump and makes
every stored replay, every posted score and every cached `bot_scores` row
unverifiable. The thing that makes the cheap version possible is that a recipe
stores **concrete coordinates** and never goes through `_play_bounds` at all. So
`CUSTOM_WORLD_W` (1600) is a second box that only hand maps live in:
`MapNode.clamped` enforces it, `mapmaker._build_view` is the camera over exactly
it, and nothing generated moves. At play time `main.build_view` fits the node
bounding box rather than any world box, so a map drawn to these bounds simply
fills the window.

*Generate* then hands back a square map inside a wider canvas, so `_centred`
translates it into the middle. A translation and nothing else: scaling it to fill
the width would stretch every lane and quote travel times the seed never gave.

### What the editor opens onto

A blank canvas — the opposite of what Phase 1 shipped, and worth recording why it
flipped. The original argument was that a blank map fails the Play gate on two
counts at once (no seats, no lanes) and that "nothing here works yet" is a poor
first impression. What that overlooked is that *Create map* is a request to
create: opening onto a generated board makes the first act picking someone else's
map apart, which is a different task from the one that was asked for, and the
validator's problem list already says exactly what a blank map is missing. Both
other starting points are one press away on the footer — *Generate* rolls a board
to edit, *Open* loads a saved one.

Two consequences had to be paid for, both because `mapgen.generate_custom` is the
strict builder and **asserts** on a recipe with blockers:

- `commit` writes `custom_map = None` for an *empty* recipe rather than the empty
  recipe itself. It is not a half-built map — it carries nothing to preserve and
  is indistinguishable in intent from having no hand map — and writing it would
  pin the menu into "Edit map" over a setup that cannot start, which opening the
  creator and pressing Esc would otherwise now do.
- `menu._start` refuses a hand map with blockers, saying the first one. A
  genuinely half-built map (systems but no lanes) must still survive a trip back
  to the menu to change a setting, so `commit` still writes it; the gate belongs
  at Start. This hole predated the blank default — deleting every system and
  leaving would reach the same assert — it just stopped being obscure.

## The rules in one place

A board has a **third source**: a recipe drawn by hand. `custommap.CustomMap` is
the serializable form — systems with concrete positions, production, garrisons and
owners, plus lanes as index pairs — and it rides on `Settings.custom_map`, which is
the whole trick: save/load, share and challenge links, resume, replay, the
leaderboard and the offline bot column all carry it with no new plumbing.
`mapgen.generate_custom` builds the board; `settings.build_state` branches to it.

- **A hand map is not a `mode`.** `GameState.mode` is stamped `"custom"` (its one
  non-test reader is `botio.setup`), but `"custom"` must **never** join
  `settings.MODES`: `leaderboard/schema.sql` is `check (mode in
  ('random','symmetric'))`, so a token carrying it would be refused by the
  database on submit. The setup keeps the mode it had; the recipe overrides it.
- **Every stored value is concrete, so the seed no longer shapes the layout** —
  it drives combat dice and star names only. That is why the menu keeps its Seed
  row, and why the Economy sliders move into the creator's sidebar: garrisons are
  rolled *at the moment a system is placed*, so placement is the only point at
  which they still bite. Nothing stored is a sentinel.
- **A hand map lives in a wider box than a generated one.** `config.WORLD_SIZE`
  is square and `mapgen._place_nodes` rolls inside it, so every generated board is
  square; a recipe stores concrete coordinates and never goes through
  `_play_bounds`, so it doesn't have to be. `config.CUSTOM_WORLD_W` x
  `WORLD_SIZE` is the hand-map box — `custommap.MapNode.clamped` enforces it and
  `mapmaker._build_view` is the camera over exactly it, so the canvas *is* the
  region a system may occupy. Widening `WORLD_SIZE` itself instead would re-roll
  every seed: a `RULES_VERSION` bump, and every stored replay and posted score
  unverifiable. `mapmaker._generated` **centres** what it adopts, since a square
  map in a wider canvas would otherwise open hugging the left; a translation only,
  because scaling to fill the width would stretch every lane and quote travel
  times the seed never gave.
- **The editor opens on a blank canvas, and that costs two guards.**
  `mapgen.generate_custom` asserts on a recipe with blockers, so a map that cannot
  build must never reach it. `mapmaker.commit` writes `custom_map = None` for an
  *empty* recipe (not a half-built map — it carries nothing, and writing it pins
  the menu into "Edit map" over a setup that cannot start), and `menu._start` — the
  one funnel all three `"start"` returns go through — refuses a hand map with
  blockers and says the first one. A genuinely half-built map is still committed:
  it must survive a trip to the menu to change a setting, so the gate is at Start,
  not at commit.
- **One tolerant gate, one strict builder.** `CustomMap.from_dict` is total and
  never raises — it pads short rows, clamps out-of-range numbers and drops junk
  lanes — and returns `None` for anything that cannot describe a playable map.
  `generate_custom` asserts. Repair only what is local and bounded: auto-linking a
  disconnected graph would invent structure the author never drew and present it
  as theirs, so that is a rejection. A rejection is never silent even though
  nothing is raised — `challenge_key()` stops matching the sender's stamp, so the
  menu's existing "this setup has been edited" banner fires with nothing added.
- **`normalised()` must stay idempotent, and `to_dict` emits it.** `challenge_key`
  hashes what `to_dict` writes, so a form the reader would normalise differently
  makes a sender's own link read as edited the moment it is opened — the trap
  `_ai_from_dict` documents for an int `aux`. Coordinates are **integers** for the
  same family of reasons: `verify_scores.same_setup` hashes a browser-written
  side (where `100.0` is `100`) against the game's own JSON, and `_aux_widened`
  already papers over that for one field. Don't make it two.
- **Node identity is positional**, so deleting node *i* shifts every later lane
  index. `CustomMap.without_node` is the single implementation; don't open-code it.
- **`mapmaker` draws on the real surface, not `menu`'s fixed canvas.**
  `menu._to_canvas_event` rounds pointer coords through a float scale, and stacking
  that on `WorldView.to_world` gives two lossy inversions in series — at
  `config.ZOOM_MAX` a system would not land where you tapped. It shares *primitives*
  with `render` (`widgets`, `config.node_radius`/`player_color`/`text_on`) rather
  than drawing functions, which are threaded through fog, film and order state the
  editor has none of.
- **Its working model is the recipe, never a live `GameState`.** `Lane.length_ly`
  and `travel_turns` are stored fields, cached again in `adjacency` and shipped to
  bots as `base_turns`, so moving a node in a live state means recomputing all
  three — miss one and the board lies. With positions plus index pairs the lanes
  follow for free and undo is a copy of plain data.
- **The validator has one implementation with three callers.** `problems()` gates
  Play, renders live in the sidebar, *and* is what a drag's or a new lane's
  legality is filtered from — never a second copy of the geometry that could
  drift from what Play enforces.
- **A crossing lane is a *warning*; a lane under a system is a blocker.** The
  engine, the AI and every bot are indifferent to planarity, so two lanes crossing
  in open space only looks busier — but a lane hidden beneath a third system
  misrepresents the graph. The creator's **Planar** toggle (default on) is what
  keeps the common path clean, by refusing to *draw* a crossing; it is editor-time
  only, so turning it off leaves a map that still plays and still shares. Making
  crossing a blocker instead would mean a map you can draw is a map you cannot
  play, which is what the toggle exists to avoid.
- **Lane drawing is two gestures through one `_add_lane`.** A tap arms the source
  and the next press commits; a drag past `DRAG_THRESHOLD` commits on release. One
  armed source (`Editor.lane_src`) serves both, so they cannot produce different
  work. Systems are picked *before* lanes — a lane's endpoint sits inside its
  system's tap reach, and "start a lane here" has to win there — and a repeat press
  on overlapping lanes cycles, the same shape `input._pick_lane` uses.
- **Shift-click chains, in the Systems tool only.** Held down, a click both places
  (or links) and keeps building from what it just touched — the anchor is always
  `Editor.sel_node`, and `_arm_move` already makes whatever was just placed or
  clicked the new one, so a run of shift-clicks chains and a shift-click off to
  the side branches from wherever you're pointing. `mapmaker._lane_candidate` is
  the validation half split out of `_add_lane`, so a placement that also links
  validates the new lane against the recipe with the new node already in it and
  takes one undo snapshot for both halves rather than two; a refused link never
  refuses the placement, it only leaves the status saying which half failed. Off
  while Auto-lanes' re-run toggle is on (below) — the network it rebuilds would
  overwrite the very lane a chained click just drew.
- **Left-drag pans in the Lanes tool but not the Systems tool.** In Systems a
  press on empty space always means "place", so there is no free left gesture, and
  making it conditional on legality would give one press two meanings — the trap
  route mode's tap documents. Right-drag and the on-map cluster pan in both.
  `pan_button` records *which* button armed the pan (Lanes' left-drag sets it to
  1), and both branches that can arm one write it — the right-button press
  restates 3, not just leaves whatever the last pan left behind, or one left-drag
  pan in Lanes leaves every later right-drag pan, in any tool, dead for the rest
  of the session.
- **An empty-space press deselects, in Lanes and Owners.** Neither tool has any
  other press that can clear `Editor.sel_node` — Owners paints or arms a box on a
  miss, Lanes disarms `lane_src`/`sel_lane` on one — so without this the ring (and
  the sidebar block that follows it) would sit on the map for the rest of the
  session once carried in from Systems. Systems is deliberately exempt: a press on
  empty space there always means "place", and placing selects the new system, so
  the ring is never stale there anyway.
- **Adding `custom_map` cost a `_LEGACY_KEY_DROPS` entry** and moved the default
  digest to `38c8b7ba470f6f4c`; all three previous digests are recovered in order.
  `tools/bot_replay._OUTCOME_MODULES` gained `custommap` — miss that and a change
  to the recipe parser leaves every cached `bot_scores` row falsely fresh.
  `tools/setup_sweep` refuses a hand-authored setup outright: its whole method is
  reseeding, and no seed re-rolls a hand map.
- **The three tools share one scene and never discard each other's work.**
  Systems places and edits; Lanes draws and picks; Owners paints seats. The
  viewport rect must **not** depend on the tool (`_palette_h` is measured but
  fixed), or switching tools moves the map under the cursor. `ed.rects` is one
  namespace cleared every frame, so a control that isn't drawn is inert by
  construction — but two controls sharing a key means the later-drawn one wins,
  silently.
- **Auto-lanes can re-run itself, on a placement or a deletion only.**
  `Editor.auto_relane` (a preference like `planar` — `_adopt` leaves both alone)
  re-runs `mapmaker._relane` — `_auto_lanes` without the confirm or its own undo
  snapshot — folded into that edit's single undo step. A drag is deliberately
  exempt: it's continuous, and relaning mid-drag would fight the rubber band a
  system follows while an illegal spot is still being tried. Hand-drawn lanes stay
  legal while it's on; they simply last until the next system is added or
  removed, and the Lanes tool is never locked. Turning it on doesn't relane on the
  spot — that would be a destructive rewrite with no confirm — it only takes hold
  from the next change.
- **The seat palette offers `n + 1` seats, floored at 2.** That makes the common
  path gap-free by construction. It does not *prevent* a gap (paint seat 3, then
  clear seat 2), so that case gets a blocker and a one-press **Renumber seats**
  rather than a silent compaction — renumbering changes a seat's colour without
  being asked, and the colour is part of what an author intended.
  `mapmaker._seat_entries` is that list, with two readers: the Owners palette band
  and the selected system's owner row in the sidebar (a row of swatches, not a
  stepper — a seat is a colour, so it is pointed at). They must not disagree about
  which seats exist, or one offers a seat the other calls a gap. Both rows are one
  control (`mapmaker._pick_seat`): a swatch arms the seat a map tap paints *and*
  stamps it on the selected system, the same way the production palette's `pal_*`
  already retypes a selected system rather than looking inert
  (`_retype_selection`). Neither row has a toggle-to-neutral second meaning:
  pressing the seat a system already holds arms the pick and stops there — Neutral
  is its own swatch in both rows, so that stays the map tap's job, where there is
  nothing else to press.
- **Painting a seat never rewrites the numbers.** *Make homeworld* is the explicit
  version, stamping `HOME_PRODUCTION`/`HOME_START_SHIPS` in one press, so the
  common "give this one a real garrison" case isn't a two-tool round trip.
- **The Owners tool's *Auto* is `mapgen.peripheral_starts`, not a second copy of
  it.** That function was lifted out of `mapgen._peripheral_starts` to take a
  `{id: pos}` map and an rng, so the editor can seat a recipe that is not a board
  — the wrapper keeps the same ids, the same order and the same single rng draw,
  so a seed still lays out the board it always did. It replaces rather than
  merges (one start per angular sector is the whole property) and demotes a
  system it unseats back to an ordinary roll, but only one carrying the exact
  homeworld stamp. Its seat count is `Editor.auto_seats`, **not**
  `settings.players`: with a recipe set, `commit` derives `players` from
  `recipe.seats()`, so placing the homeworlds *is* how the seat count is chosen —
  and `None` there means "as many as the map already has", which is what keeps
  the readout honest and why `_adopt` resets it.
- **Box-paint copies route mode's two-flag arming** (`box_press` on the press,
  `box_active` only past the threshold, so a tap that never moves paints nothing)
  and **clips the box to the viewport first** — `to_screen` projects every system,
  including ones panned out under the sidebar, and only the drawing is clipped.
  A box paints as one group: if every system in it already holds the pick it
  clears them all, otherwise it paints them all, so a box never half-toggles.
- **The menu hides what a hand map decides, by not drawing it.** Basic's Players,
  Systems and Map type become read-only derived values (Players is chosen in the
  creator instead, by *Auto* above or by painting); Advanced's Map and Economy
  groups become a note, since those knobs now live in the creator and only bite
  there. `menu._set_players`/`_set_nodes` are additionally *interlocked* while a
  recipe is set — a nudge from any other path would desync them from it until the
  next `from_dict` reconciled them back, moving the digest in between. Seed stays:
  it still drives combat dice and star names. Dropping the recipe (the *x* beside
  *Edit map*) is behind a confirm, answered inside `_dispatch` rather than ahead
  of it, so the clear still falls through to the un-challenge check every other
  edit trips.
- **Star names are deliberately absent from a recipe.** `mapgen._name_systems`
  stamps them from `state.rng` last and serializes nothing, so they are recreated
  for free from the seed; the editor shows ids (`#7`).
