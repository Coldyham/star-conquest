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
