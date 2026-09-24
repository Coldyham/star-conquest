# Play-by-post design notes

Rationale and edge cases behind play-by-post (`pbp.py`, the `pbp_*` tables and
`leaderboard/netlify/functions/pbp.mjs`): one seat of a shared match, held by a
person at their own pace. `CLAUDE.md` states *what* each rule is; this file is
*why*. Its companions are [`system-design.md`](system-design.md) for the core
and shell, and [`bot-design.md`](bot-design.md) for the AI roster.

## Context

Star Conquest has no multiplayer of any kind. Two people who want to play the
same map today either share a challenge link and race the same setup
independently (never actually interacting), or sit at one machine. There is no
hotseat mode either — the engine has exactly one human seat, by construction.

The ask: a per-seat URL. Visit it, and if you haven't entered orders for the live
turn you play it exactly as a singleplayer turn. Submit, and you get a *waiting
for other players* overlay. When every seat is in, the turn resolves and everyone
sees the new board. Asynchronous, no appointment to keep, no passing a laptop
around.

The reason this fits Star Conquest unusually well is that **turns already resolve
simultaneously**. `engine.end_turn` collects every seat's orders against the same
unchanged start-of-turn board and applies them together, so there is no
turn-order advantage and nothing about the rules needs redesigning. Play-by-post
is the delivery mechanism the rules were already written for.

### Decisions taken up front

These came before any code was written, and the rest of the design follows from them.

1. **Thin server; clients resolve.** The server stores each seat's orders per
   turn and the growing log. Clients reconstruct locally. `replay.reconstruct`
   already rebuilds any board from settings + seed + per-turn orders + dice while
   asking no seat to decide, so the server never needs a board or a Python
   runtime. A Netlify function plus Supabase tables, following
   `leaderboard/netlify/functions/log.mjs`.
2. **Fog is kept, and is honest convenience rather than enforcement.** A thin
   server means a client holds the whole log and could reconstruct the full
   board. Fog stays because it shapes play, but the UI and docs must say plainly
   that it is not a secrecy guarantee. No enforced fog is designed here.

   **Board coherence is checked, cheaply.** The server holds no board and cannot
   referee one, but it does not need to: the board is a pure function of settings
   + seed + the orders and dice it already stores, so every honest client lands on
   the same state by construction. What the server *can* do is catch it when they
   don't. Each client sends a short **board digest** for the turn it just
   resolved. `tests/sim.py:212`'s `board_digest` is the right shape and already
   proven as a film oracle (systems, fleets incl. `lane_slot`, turn, winner,
   per-player alive and `ships_lost`); it lives in `tests/` so it is not
   importable from the shipped package, and wants lifting into the core —
   `replay.py` is the natural home — then hashing to a short hex string for the
   wire. `tests/test_turnfilm.py:17` has a near-twin that omits `lane_slot`;
   lifting one implementation and having both call it is the point.

   The function stores the first digest per `(match, turn)` and compares every
   later submission against it; a mismatch flags the match rather than letting it
   silently diverge. That is a tripwire against a stale build, a hand-edited
   client or a genuine nondeterminism bug — not an anti-cheat mechanism, and this
   plan should not claim it is one. A client that lies about its digest is exactly
   a client that could lie about its orders, and the answer to both is that a
   play-by-post match is between people who chose to play each other.

   Dice are the reason this is nearly free: they come from `state.rng`, seeded by
   the match seed, so given the same order sequence every client draws the same
   numbers. The digest turns "should agree" into "demonstrably agreed".
3. **Separate `pbp_*` tables, same Supabase project, function-only writer.** The
   existing board's append-only, no-identity guarantees stay literally true and
   untouched. A seat token is per-match, per-seat, random, and dies with the
   match — scoped to a match, never to a person.
4. **Multiplayer matches do not appear on the regular leaderboard.** Its score
   is turns-to-win against a fixed setup — a singleplayer measure that means
   nothing for a match between people. Play-by-post games are **link-invite only**
   for now: you get a seat URL and you send it to someone.

   Deliberately left open for later: a lobby page listing in-progress games with
   **open seats**, where the first person to click one is handed a seat token.
   That is a natural second step and the schema below should not foreclose it —
   hence `pbp_matches.seats` recording which seats are claimed, so an unclaimed
   seat is already a representable thing. It is not built in these phases.
5. **Verifiable on a PR preview.** Deploy previews on a PR are effectively
   unlimited; only publishing `main` costs credits (~20/month shared across both
   Netlify sites). Every change here must be checkable without spending a publish
   — hence the stub-PostgREST handler tests and `tools/check_pbp.py` below.

## Constraints that shape the design

These are verified against the code, and violating any of them breaks something
load-bearing:

- **`RULES_VERSION` must not move** — and it doesn't have to.
  `engine.py:40-53` says to bump it when a change alters "how the dice are drawn
  or consumed", and order *sequence* does exactly that: `apply_order` appends
  fleets in sequence, `_resolve_arrivals` iterates an insertion-ordered
  `defaultdict`, and `_Dice` is consumed in that walk. A bump would make every
  posted score read `outdated` in `tools/verify_scores.py`, remove every existing
  *Watch* link (`game.mjs` `watchableIds`), and need a hand-bump of
  `leaderboard/js/config.mjs`'s `CURRENT_RULES_VERSION`.

  **The generalisation is sequence-identical, verified empirically.** The human
  is always pid 1 — `mapgen._make_players` stamps `is_human=(pid == 1)`
  (`mapgen.py:395`), `main.py:96` is the only `Ui` construction site and passes
  `human_id=1`, and `replay.HUMAN_SEAT` mirrors it. So today's "human first, then
  AI seats in `sorted(state.players)`" and a plain "**all non-neutral seats in
  ascending pid**" emit the same sequence. Checked across players 2-6 × both map
  modes, plus no-human autoplay, a dead AI seat and a dead human seat: **13 cases,
  0 mismatches.** The new rule needs no special case for humans at all.

  The one case that *would* diverge — a human seat that is not the lowest live
  pid, e.g. `[3,1,2,4]` — is unreachable today (`engine._claim_seat` accepts any
  pid, but nothing ever passed one but 1). Play-by-post makes it reachable, which
  is exactly why the ascending-pid rule went in before any seat could move.
- **The log format does not need a version bump.** `TurnRecord` already carries
  *every* seat's orders with `owner_id`, and `GameLog.script_for` needs only
  `orders` + `dice`. Only `"ai"` (one bool) and `"rules"` (one seat's
  `auto_forward`) in `record_turn` are single-seat, and both are shell-side
  metadata that replay does not consult. A play-by-post match therefore replays,
  resumes, verifies and is watchable on today's format.
- **No secret may reach the game's own Netlify site.** `netlify.toml:10-36` — a
  fork's PR preview runs the fork's own build script. Secrets live only in the
  leaderboard site's functions or in Actions.
- **`window.eval` returning a value is not trusted.** The web fetch parks its
  result in `localStorage` and the poll reads it back through `webstore.get`
  (`share.py:54-60`). Any new networking must keep that shape.
- **Adding a top-level `Settings` field moves `challenge_key()` for every setup
  ever shared** and needs a `_LEGACY_KEY_DROPS` entry. The seat token must not
  live there. `Challenge.log` is the precedent for cheap metadata —
  `challenge_keys()` drops `challenge` before hashing.
- **`tests/test_schema_grants.py`** scrapes `schema.sql` grants against callers
  and fails on a new relation without matching grants.
- Films/reels hold the board while running (`main.py:1150-1156`); a networked
  resolution must serialize against that, not race it.

## What it reuses

- `share.Download` (`share.py:261-302`) — a non-blocking, poll-once-a-frame
  request with both backends already written. Needs generalising from GET-only
  to carry a POST result, but the shape is right.
- `_own_orders` (`engine.py:220-238`) — already enforces "a seat commands its own
  ships and nothing else".
- `botio.orders_from` (`botio.py:207-238`) — validates shape only and **stamps
  the seat itself, so a foreign order is not expressible on the wire**. Exactly
  the property a submission endpoint needs.
- `fog.observe(state, pid, …)` and `main._accumulate_fog(state, seat, …)` — both
  already fully seat-parameterised.
- `Ui.human_id` — a field, read by the whole shell, so a reseated `Ui` largely
  works already.
- `log.mjs` — the template for a rate-limited, size-bounded, CORS-by-shape,
  validate-everything function holding the only key that may write its table.
- The `#log=<id>` launch path (`main.replay_request` → `open_replay`) — the
  precedent for a fragment-driven entry point with a proper error taxonomy.

## The one idea everything follows from

The server holds **no board** and runs no engine. A board is a pure function of
the settings, the seed and every turn's orders — which is what
`replay.reconstruct` already rebuilds while asking no seat to decide. So the
server stores inputs, and each client rebuilds the position.

**The stored log is the record, and a turn is decided once.** Whoever resolves a
turn uploads its log; every other client applies that log (`pbp.match_log`,
`pbp.settled_turn`) and nobody decides the turn again. This replaced an earlier
version in which every client re-ran every bot from the stored order rows, which
forked matches two ways:

* `knower` and `marshal` stop searching on a wall clock, so a fast machine and a
  slow one (or a background tab, or WASM vs desktop) can decide the same
  position differently. Re-deciding on every client forks the match the first
  time a bot runs short on one of them; even a one-human match could change
  under you on reload.
* A rebuilt board's `state.rng` is not where a continuously played board's is:
  `reconstruct` deals recorded dice and asks no bot to decide, so its rng never
  moves. The live turn's rng is therefore *derived* (`pbp.reseed`, seeded from
  the seed and the turn) by whichever client resolves, the same
  derive-don't-draw rule as `botio.decide_seed`.

The trust this adds is bounded to the bots' orders, which is the same honesty
fog already asks for and cannot be checked while bots decide on a clock. It
cannot write a person's orders: `match_log` refuses a log that files an order
under a seat that the seat's stored row does not hold. And it cannot write the
dice: `pbp.turn_orders` has the bots decide on a scratch copy (in the ascending
sequence `_collect_orders` uses, one shared board and rng between them), so the
turn itself runs with every order already fixed and rolls from the rng
`reseed` put there — a pure function of seed, turn and orders. Every stepping
client re-rolls it (`pbp.verify_turn`) and refuses a turn that disagrees. This
only checks turns as they are stepped; a client rebuilding from the opening
applies the older turns unchecked, since turns resolved before this existed
rolled after their bots and would not verify. A log may carry *fewer* — the
engine drops an order out of a system lost before launch. The uploaded copy has
the resolver's standing forwarding rules stripped (`pbp.shareable`); every
other client opens from it, and a route plan is one player's own. Two clients
resolving at once: the endpoint keeps the first upload, and the loser rebuilds
from it (`pbp_stale` in `main`).

Consequences worth knowing before touching any of it:

* **A finished play-by-post match is an ordinary `GameLog`** — it resumes,
  reviews, scrubs and verifies with nothing taught about it.
* **Order sequence is the whole of determinism.** `engine._collect_orders` fixes
  it at ascending seat id. Do not make it depend on submission order.
* **`RULES_VERSION` did not move**, and must not for any of this. Verified by
  replaying 240 whole games through the old and new collection rules: identical.

## Verified against a real deploy

Against `deploy-preview-60--star-conquest-leaderboard.netlify.app`, with the SQL
run on the real Supabase project:

* A four-turn match played end to end: submit -> gate -> resolve -> advance.
* Forged and malformed tokens refused 403; double submission refused 409 by the
  DB constraint; stale turn refused 409 and told the real turn.
* An order carrying `owner` has it stripped — the wire cannot express a foreign
  order (`botio.orders_from`'s property, moved a layer earlier).
* Neither the plaintext token nor its hash appears in a public `state` read.
* Two clients resolving the same turn produce an identical board digest.

### The bug that made the digest worth having

`?action=state` sent only *resolved* turns, so a client resolving the live one
applied an empty order set and played the turn **as though nobody had moved**.
The turn advanced, the log uploaded, and two clients agreed with each other
because both were equally wrong. Only comparing a rebuild against the stored log
exposed it.

The fix is complete-or-nothing (`visibleOrders`): the live turn's orders are
released only once every seat is in. Any earlier and a player still composing
orders could read everyone else's; any later and the resolve is empty. Regression
tests on both sides, and the Python one verified to fail against the old
behaviour.

## Testing the backend

`tools/check_pbp.py` has played a two-seat match through four turns against a
real preview (`deploy-preview-60--star-conquest-leaderboard.netlify.app`) and
the real Supabase project. Every client agreed on every board digest, and
forged, stale and foreign-owner submissions were all refused. Note the site is
`star-conquest-leaderboard`, not `star-conquest`: that is the repo's other
Netlify site. Real PostgREST agrees with the stub about status codes, and the
conditional `PATCH` behaves. The one thing nothing tests end to end is a deadline
running out in real wall-clock time. The tests only simulate it by backdating the
clock.

Two layers of coverage, cheapest first:

* `leaderboard/tests/pbp-handler.test.mjs` runs the **real handlers** over a stub
  PostgREST — so the query strings, the token hashing and the conditional update
  that makes two clients resolving together a non-race are all exercised, in CI,
  with no deploy. The stub is strict on purpose: an unknown column throws, which
  is how a query that has drifted from `schema.sql` is caught. Both guarantees
  were checked by breaking them — a mistyped column and an unconditional update
  each fail it.
* `tools/check_pbp.py` is the real-deploy version: it plays a throwaway match out
  against an origin you give it and asserts the same properties end to end. Run
  it against production, or against a PR's deploy preview before merging one
  that touches the backend. Previews and production share one Supabase project,
  so either way the run leaves a throwaway match in the live `pbp_*` tables:

  ```sh
  uv run python tools/check_pbp.py --origin https://star-conquest-leaderboard.netlify.app
  uv run python tools/check_pbp.py --origin https://deploy-preview-<PR>--star-conquest-leaderboard.netlify.app
  ```

## Opening a match

* **A shared match can seat a roster smaller than the table.** `menu`'s "Play by
  post" button opens a roster prompt (`MenuState.pbp_prompt`/`pbp_roster`) rather
  than the match itself — every seat starts checked ("every player is a person"
  is still the default), seat 1 has no checkbox at all (the creator ends up
  seated there regardless), and unchecking any other seat leaves it to play
  whatever strategy the AI tab already has it set to. Confirming calls
  `main.pbp_open(settings, seed, seats=sorted(ms.pbp_roster))`. The endpoint
  and `pbp.rebuild` needed nothing for it: both were seat-agnostic from the start.
* **Seat links are copied individually, not as one clipboard blob.** The moment
  the match we created opens, `Ui.pbp_invite` carries `(seat, link)` for every
  *other* seat and `render._draw_invite_overlay` shows one row per seat with its
  own Copy button. `input._handle_invite_
  event` never touches `webstore` itself (it only names the seat on `ui.pbp_copy_
  seat` and returns `"pbp_copy_seat"`); `main.py` is where the clipboard write
  actually happens, same as every other share path. Continue clears the overlay
  for good; it never reappears once dismissed, and following someone else's
  invite link never sets it in the first place.
* **The deadline is on the same prompt, not fixed at 48h.** A stepper below the
  roster (`MenuState.pbp_deadline_hours`, reset to `pbp.DEADLINE_HOURS` whenever
  the prompt opens) moves a day at a time between the endpoint's own 1h/336h
  bounds — clamped in the UI to 24h/336h, since the format's own unit is days
  ("two days is what play-by-post exists for") rather than raw hours. Confirming
  threads it straight through: `main.pbp_open(..., deadline_hours=ms.pbp_
  deadline_hours)` -> `pbp.create` -> the endpoint's existing `deadline_hours`
  column, unchanged.

## How a deadline works

The policy is Diplomacy's and it is about people rather than rules: the first
miss **holds**, which is already a legal turn — production ticks, garrisons
defend, nothing is thrown away — so somebody who is simply a day late loses a
tempo and not their position. Only a second consecutive miss hands the seat to
its bot, by which point the alternative is a match that has stopped.

Four things make it work without a cron or a server-side engine:

- **The endpoint owns the judgement; a client owns the orders.** Whose turn has
  lapsed and what it costs them is decided in `lapsedSeats`, from the stored
  clock, and published on `?action=state` so a client knows what to send. It is
  recomputed in `handleLapse` rather than believed. What the client supplies is
  the one thing the endpoint cannot: a bot's actual orders, which need an engine.
  A **hold** is forced empty there whatever arrives.
- **Misses are read off the `source` column**, which is already the record of
  them: a row filed under a seat's own token is `human` and anything else was
  filed on its behalf. Nothing to keep in step, and a turn a seat genuinely
  played resets the run by being there.
- **A lapsed bot's orders are computed on a copy of the board.** `decide` is not
  promised to leave a board or its rng alone, and the live board is the one this
  client goes on to play. So the copy is thrown away, only the orders travel,
  and every other client applies what was stored. `test_filing_a_bots_turn_leaves_the_live_dice_exactly_where_
  they_were` asserts on the rng directly, because a turn with no fight in it
  draws nothing and a board digest would agree for the wrong reason.
- **A client never files its own lapse** (`lapse_orders(skip=…)`). Somebody who
  opens the game two days late is *here*, and filing their hold the moment they
  arrive would take the turn away from the one person about to take it. Any
  other client still may, which is the whole point.

Filing resolves nothing. It makes the turn complete, and the next read takes the
ordinary resolve path — so a lapsed turn goes through the very same gate every
other turn does.

## Public matches and the lobby

A match opened with **Publicly joinable** ticked is listed on the leaderboard's
lobby page, `pbp.html`. The game's **Shared matches** footer button (web only)
opens it. It shows three groups: open invitations, the matches this browser
holds a seat in, and finished results. A stranger's match that is under way is
not listed at all: it offers nothing to do, and with no accounts nobody can tell
from it whether they are in it. `?action=list` returns only public rows. A
private match is reachable only by knowing its id, and a list of every id would
undo that. `?action=claim` answers a private match with the same 404 it gives a
missing one, for the same reason.

**A match has a name, and the name is derived.** `matchnames.phrase` turns the
id's first three bytes into an adjective and two nouns, `amber-boulder-comet`.
The same word lists live in `leaderboard/js/matchnames.mjs`, and
`tests/test_leaderboard_sync.py` pins the two together. Nothing stores it and
nothing looks a match up by it: the 16-hex id is still the key everywhere,
exactly as a star name sits on top of a system id. The id keeps its full 64
bits, so collisions in the name (24 bits) cost only a confusing label.

**Titles and seat names are self-declared display text.** The creator may give
a title and their own name in the create prompt, and a joiner may give a name
when claiming. `names` is `{seat: name}`, and a seat's entry is only ever
written by whoever holds that seat, at create or claim. That is the same footing
a posted score's user name has. The game remembers the last name typed
(`webstore.pbp_name`), and the lobby pre-fills from its own last one or the
board's posting name. There is no later rename yet. A seat handed out as a
private link, rather than claimed, stays unnamed.

**The winner is a claim, like `finished`.** The server holds no board, so it
cannot know who won. The resolving client sends `winner` with the final turn,
and it is trusted exactly as far as `finished` beside it. 0 is a draw. Any
client can check it against the stored log.

**"Yours" is this browser's, from two sources.** The lobby keeps
`{id: {seat, link}}` in its own localStorage (`sc_pbp_mine`). A claim made there
adds the seat with its full link: that token was minted on the lobby, so keeping
it there is no weaker than the game's own `WEB_PBP_SEATS_KEY`. The game's button
hands over `#mine=<id>,<id>` (`pbp.lobby_fragment`), ids only and never a token,
and the lobby looks them up with `?action=list&ids=`, which returns a private
match too. Knowing the id is already what `state` requires. *Open in game* uses
the stored link where there is one, and otherwise a bare `#pbp=<id>`, which the
game resolves against the seats it remembers (`pbp.bare_match`, `main`'s launch
path) and refuses plainly when it holds none.

The two stores are separate origins, and on iOS a home-screen web app's storage
is separate from Safari's too. So the lobby's copy is only a cache: each hand-off
re-supplies it, and a match the lookup no longer finds drops out of it. A bare
`#pbp=<id>` opened in a browser that is not the one holding the seat is the case
that says "open it with your seat link". A claimed seat's link is shown so it can
be copied to another device.

**The setup goes up pruned.** `pbp.create` sends `Settings.token_dict()`, which
`from_dict` reads back whole, so the lobby's knob list (`setup.mjs`'s `tweaks`,
which assumes a pruned dict) reads a match exactly as it reads a map. Matches
created before this stored the full `to_dict()` form, and their pages list every
knob.

**An open seat is a missing hash, not a flag.** Tokens are stored only as
hashes, so no token can be shown twice. A public match therefore mints only the
creator's token (`claimed: [1]` on create). Each other seat's token is minted
when somebody takes the seat on the match's lobby page. That reply is the one
time it exists in the clear, and the lobby keeps it in the browser that asked. Minting the token is what claims the seat, so there
is no second field to keep in step with the hashes.

The claim rewrites the whole `seats` column. It is conditional on `updated_at`
not having moved since the read, so of two claims at once, the second is told to
retry rather than landing on top of the first's hash. A resolve in between also
trips that condition, and costs only a retry.

An open seat behaves like any outstanding seat. With a deadline set, it holds
and then falls to its bot like any other seat. With no deadline, the match waits
for it.

The `public` column defaults to false. A one-off, commented-out
`update ... set public = true` in `schema.sql` flags the matches that predate
the column, so there is something to test against. Nothing needs switching off
before a deploy.

## Traps

* **Nothing may resolve a play-by-post turn on a clock of its own.** That is the
  whole difference from a single-player game: the turn advances when the last
  seat submits. End Turn *submits* (`main.pbp_send`) and `input.handle_event`
  returns `None` for a second press while `ui.awaiting_others(state)`; play and
  autoplay are taken away outright, key and footer button alike, because both
  exist to run turns on a timer.
* **The roster is the truth about who the people are, not the `is_human` flags a
  rebuild leaves behind.** `mapgen` stamps seat 1 regardless and
  `replay.reconstruct` restores that stamp from the log's single-seat `"ai"`
  flag, so `pbp.seat_people` sets the whole thing outright — a match seated at 2
  and 3 would otherwise carry a phantom person at seat 1 holding every turn
  forever. `main.open_match` re-stamps after `resume_game` for exactly this.
* **A settled turn goes through `main.resolve_turn`, not beside it.** Everything
  after the engine returns — the film, the marks, the fog, the log, the camera
  snap — is the same work whoever collected the orders. `tests/test_pbp_client.py`
  pins the join: the board the shell steps to and the board another client
  rebuilds from the stored log must be the same board. A step passes the
  resolver's record as `script`; only a resolve passes `seat_orders` and lets a
  bot decide.
* **Desktop resolves the leaderboard origin to *production*.** There is no page
  host to derive a sibling from off the web (`paths.sibling_host`), so testing a
  preview from a desktop build means overriding `webstore.leaderboard_origin`.
  This cost ten minutes of confusion; it is not a bug.
* **A render test here must re-init pygame itself.** These files open the
  display at import time, and other files (`test_render`, …) call `pygame.quit()`
  before they run — no display, font module down, and `render._FONTS` holding
  dead fonts. Call `pygame.init()`, clear `render._FONTS`, and draw to your own
  `pygame.Surface`. This is what the once-skipped 2.5x overlay test in
  `test_seat_agnostic.py` and the refusal-pill test in `test_pbp_client.py`
  tripped over; it was never UI-scale state.
* **A new relation needs a grant** or `tests/test_schema_grants.py` fails. Its
  scrape was widened to see `pbp.mjs`'s helper-style calls — it was passing
  *vacuously* before that, which is the exact failure it exists to prevent.
