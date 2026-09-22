# Play-by-post multiplayer — design and handover

**Status: work in progress on branch `play-by-post`.** Not merged, not deployed
to production. This document is the working record: what was decided and why,
what is built, and what is left.

It lives in `docs/` rather than `notes/` deliberately — `notes/` is gitignored,
and this needs to travel between machines.

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
5. **Phased, PR-preview-first.** Deploy previews on a PR are effectively
   unlimited; only publishing `main` costs credits (~20/month shared across both
   Netlify sites). Every phase must be verifiable without spending a publish.

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
  pid, but nothing ever passes one but 1). Phase 2 makes it reachable, which is
  exactly why the ascending-pid rule goes in at Phase 1, before any seat can move.
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

## What already exists and should be reused

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

---

# Handover

## State: phases 1-3 done, phase 4 part-done

| Phase | What | Commit |
|---|---|---|
| 1 | N human seats in the core | `f33b4bd` |
| 2 | A seat-agnostic shell | `c0a12dc` |
| 3 | Backend: two tables + one function | `f3e89ec` |
| 4a | Client rebuild/resolve | `d1960da` |
| 4b | Live-orders visibility fix | `f5b01a9` |
| 4c | Wire + waiting overlay | `ae302e5` |

**Tests: 1102 passing, 1 skipped** (baseline on `main` was 1040), plus 9 JS
files. Run `uv run pytest` and `node --test leaderboard/tests/*.test.mjs`.

## The one idea everything follows from

The server holds **no board** and runs no engine. A board is a pure function of
the settings, the seed and every turn's orders — which is what
`replay.reconstruct` already rebuilds while asking no seat to decide. So the
server stores inputs, and each client rebuilds the position. Dice come from
`state.rng`, seeded by the match seed, so the same orders in the same sequence
draw the same numbers everywhere.

Consequences worth knowing before touching any of it:

* **A finished play-by-post match is an ordinary `GameLog`** — it resumes,
  reviews, scrubs and verifies with nothing taught about it.
* **Order sequence is the whole of determinism.** `engine._collect_orders` fixes
  it at ascending seat id. Do not make it depend on submission order.
* **`RULES_VERSION` did not move**, and must not for any of this. Verified by
  replaying 240 whole games through the old and new collection rules: identical.

## What works, verified against the live preview

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

## Where it stops

**Nothing is wired into `main.py` yet.** Every piece exists and is tested, but
the loop does not yet open a match, poll it, or submit from it. Remaining:

1. **`main.py` launch path** — read `#pbp=<match>:<token>` (`pbp.parse_link`)
   beside the existing `#log=`, fetch the match, `pbp.rebuild` it, seat the `Ui`
   at the token's seat, and remember the seat (`pbp.remember`).
2. **Poll on an accumulator** — the idiom is at `main.py`'s `play_accum`. Only
   while a turn is outstanding; every few seconds is generous for play-by-post.
3. **Submit on End Turn** — instead of resolving locally: send `ui.pending` +
   `auto_forward_orders` through `pbp.submit`, set `ui.pbp_submitted`.
4. **Resolve when ready** — on a poll where `match.ready`, call `pbp.resolve`
   and `pbp.send_resolved`. Serialize against films: never mid-playback.
5. **Deadlines** — `deadlinePassed`/`lapsedAction` exist and are tested but have
   no caller. Policy (settled, see plan): 48h default, first miss **holds**,
   second consecutive miss falls to the seat's bot. The next client to connect
   resolves a lapsed turn; no cron, no server-side Python.
6. **Creating a match from the menu** — nothing calls `?action=create` yet except
   curl. Needs a menu affordance and somewhere to show the seat links.

## Traps for whoever picks this up

* **`main.py` must not resolve a play-by-post turn locally on End Turn.** That is
  the whole difference from a single-player game: the turn advances when the last
  seat submits, not when anyone presses a key. `input.handle_event` already
  returns `None` for End Turn while `ui.awaiting_others(state)`.
* **Desktop resolves the leaderboard origin to *production*.** There is no page
  host to derive a sibling from off the web (`paths.sibling_host`), so testing a
  preview from a desktop build means overriding `webstore.leaderboard_origin`.
  This cost ten minutes of confusion; it is not a bug.
* **`tests/test_seat_agnostic.py` has one skipped test.** It checks the overlay
  does not overflow the map at 2.5x UI scale. The overflow was real, was found by
  looking at a screenshot, and **is fixed** — the test passes alone and fails in
  the full suite, so it is picking up global state another file leaves behind
  (`config.apply_ui_scale`, or a font cache keyed on it). Fault in the test, not
  the overlay.
* **A new relation needs a grant** or `tests/test_schema_grants.py` fails. Its
  scrape was widened to see `pbp.mjs`'s helper-style calls — it was passing
  *vacuously* before that, which is the exact failure it exists to prevent.

## Screenshots

Rendered at 1.0x and 2.5x while building the overlay. Not committed (scratch),
but reproducible: seat a `Ui`, set `pbp_match`/`pbp_submitted`/`pbp_waiting`, and
call `render.draw` on a surface.
