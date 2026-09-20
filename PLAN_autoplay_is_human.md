# DRAFT — not implemented. For later consideration only.

Status: nothing in this file has been built. The shipped mechanism on this
branch (`sim._hand_over` clears `is_human`; `bot_scores.match_id`/`log`/
`rules_version` + `public_watchable_replays` store and serve an exact replay
of a winning bot run) is unchanged and remains what's live. This document
exists so the alternative below can be picked up and evaluated later without
re-deriving it from scratch.

## The idea

Don't add a new "predictable" flag. Instead: a match that *starts* in
autoplay begins with `is_human = False` on the nominal human seat, and it
only becomes `True` the first time the player actually presses **Take
control**. Every oracle-prediction code path in `models/knower.py` already
branches on `Player.is_human` exactly the way this needs — so unlike a new,
separate flag, this needs **no changes inside knower.py at all** (`_model_for`,
`_rollout_decide`'s `humans` set, the `_priv` seat-modelling loop all already
do the right thing once `is_human` itself carries the right value at the
right time).

If the live game and the offline harness (`sim.play_settings`) both end up
with `is_human = False` for that seat from the same input (`Settings.autoplay
== True`), then a live, token-driven Watch link (`botWatchSetup` +
`autoplay: true`) and the offline worker's replay would compute the *same*
game again, by construction — which is what would let the stored-replay
mechanism (schema columns, the `public_watchable_replays` view, the
`replay.mjs`/`game.mjs`/`standings.mjs` changes) be reverted, since the two
sides would no longer need a recorded log to agree.

## What's genuinely simple about it

- `settings.build_state` gains one stamp: if `settings.autoplay` is `True` at
  generation, set `is_human = False` on the seat `mapgen` would otherwise have
  flagged human. No `mapgen.py` signature changes — this is a post-generation
  stamp in the same place `ai_strategy`/`ai_params` already get stamped.
- `tools/bot_replay.py` / `tests/sim.py` need **no changes** — `sim._hand_over`
  already sets `seat.is_human = False` directly on the built `Player`, which is
  exactly this mechanism, just applied explicitly rather than derived from
  `Settings.autoplay` (a posted human setup's own `autoplay` field is almost
  never `True`, so the harness still has to force it itself either way).
- `knower.py` needs zero changes, as above — this is the main reason it looks
  smaller than the "new flag" version of this idea.

## What isn't simple — three real problems to solve first

### 1. Double-decide risk in `main.resolve_turn`

Today:
```python
human_orders = (
    ai.decide(state, ui.human_id) if ui.autoplay
    else list(ui.pending) + auto_forward_orders(state, ui)
)
```
This always computes something and passes it as `human_orders`. But
`engine._collect_orders` only skips a seat internally when `player.is_human`
is `True`. If the match started in autoplay and nobody has claimed the seat
yet, `is_human` is `False` — so `_collect_orders`'s own loop will **also**
call `decide(state, 1)` internally, on top of the externally-computed
`human_orders`. Both get merged (`_own_orders` takes `human_orders` as-is
when passed a `None` seat, since there's no `state.human()` to filter
against). Net effect: the seat's strategy gets invoked *twice* — a second,
spurious draw from `state.rng` that would desync every oracle's bit-exact
stream tracking.

Fix (small, but must not be skipped): only compute `human_orders` externally
when `state.human()` is not `None`; otherwise pass `None` and let the
internal loop decide seat 1 the ordinary way, exactly like `sim.play`'s
ladder tournament does for every seat.

```python
seat = state.human()
if seat is None:
    human_orders = None
elif ui.autoplay:
    human_orders = ai.decide(state, ui.human_id)
else:
    human_orders = list(ui.pending) + auto_forward_orders(state, ui)
```

Manual order queuing is already blocked while `ui.autoplay` is `True`
(`input._handle_left_click` returns early), so `seat is None` and
`ui.autoplay is False` can never co-occur under this design — the `else`
branch above is unreachable until a claim has happened, which is the
invariant the rest of this plan leans on.

### 2. The claim has to survive resume/rewind

`replay.reconstruct` rebuilds a state via `build_state` (which would apply
the new `is_human = False` stamp from the log's stored `settings.autoplay`),
then replays every recorded turn via `engine.end_turn(state, script=...)` —
which **never calls `decide` for any seat**, by design (that's what makes a
replay immune to a bot's own non-determinism). Whatever engine-level
mechanism flips `is_human` back to `True` on Take Control would, if it only
fires as a side effect of a *live* decision-collection pass, never fire
during a scripted replay. Resuming a match after a mid-game Take Control
would silently un-claim the seat, exposing it to oracle prediction again on
the turns *after* the resume point — even though a real person demonstrably
took over once already.

The fix has to live in `reconstruct` itself: detect the claim from the
existing per-turn `"ai"` flag (already recorded — `human_ai=ui.autoplay` at
the time) transitioning from `True` to `False` for the first time, and at
that exact point in the replay, apply the same `is_human = True` flip the
live game applied. This is new core logic in a part of the codebase
(`replay.py`) this project is deliberately careful about — worth building
under its own tests (`tests/test_replay.py`), not folded into some other
change.

Where to actually own the flip mechanically: probably not a direct
`main.py` mutation of `state.players[...]` (no existing precedent for the
shell touching a `Player` field outside `engine`/`mapgen`/`combat` — see
CLAUDE.md's "hard split", which this would be the first exception to).
Cleaner: thread it through `engine.end_turn` as an explicit, named effect
(e.g. a `claim_seat: Optional[int]` parameter, applied as the very first
phase, before AI decisions, so the same turn's oracle predictions already
see the corrected flag) — mirroring how `Ui.auto_forward` standing rules
already become engine-turn-time effects rather than direct state edits.

### 3. A genuine Pause/Play-autoplay control, separate from Take Control

This was the piece raised at the start of this idea and it still stands:
without it, someone watching an autoplay demo (via a Watch link, or a real
player who ticked the menu's Autoplay checkbox to preview a map) who wants
to freeze the clock and look — with no intention of actually taking over —
has only Take Control available, which would (a) hand them the seat to
manually play from then on and (b) burn the claim permanently, so if they
resume autoplay afterward the rest of that session no longer matches what
the offline worker computed for the same setup. Needed:

- A new `Ui` field (e.g. `autoplay_paused: bool`) and footer button, active
  only while `ui.autoplay` is `True`, that freezes the per-frame auto-advance
  without touching `is_human` and without disabling `ui.autoplay` itself.
- `main`'s per-frame pacing (the `auto_accum >= step_delay(...)` branch) gated
  on this too.
- Take Control (`toggle_autoplay` going off) stays the *only* thing that
  claims the seat.

## Residual, accepted-if-shipped behavior change

A real player who checks the menu's **Autoplay** checkbox for an ordinary
game (not a leaderboard replay) and has an oracle bot (currently only
`knower`) seated as an opponent would have their early, autoplay-driven moves
readable by that opponent exactly, for as long as autoplay stays on and
unclaimed — narrower than it sounds, since:
- `carry_autoplay`'s own docstring already frames "start the next match in
  autoplay" as "only out of a pure demo", i.e. the intended use of that
  checkbox already isn't "let the AI play my first few turns before I take
  over".
- The moment the player unchecks Autoplay (Take Control), the flag flips
  `True` permanently and every subsequent turn is exactly as conservative as
  today.
- It only bites at all if an opponent seat is explicitly set to a strategy
  that does prediction (`IS_ORACLE`/`is_oracle_seat`) — today, only `knower`.

Worth restating to whoever picks this up: is this residual an acceptable
trade for removing the `bot_scores` schema/storage additions, or should the
stored-replay mechanism just stay (it already works and is tested)?

## What reverting the stored-log mechanism would touch, if this ships

- `leaderboard/schema.sql`: drop `bot_scores.match_id`/`rules_version`/`log`
  and their index; drop the `public_watchable_replays` view; restore the
  `service_role` grant on `public_replays` (the one `replay.mjs` used before).
- `leaderboard/netlify/functions/replay.mjs`: query `public_replays` again,
  not the union view.
- `leaderboard/js/game.mjs` / `standings.mjs`: drop `botWatchCell`/
  `botWatchKind`, restore the plain `botWatchLink` (build the reconstruction
  token unconditionally for every row).
- `leaderboard/js/token-encode.mjs`: `botWatchSetup` goes back to being the
  only mechanism, not a "legacy fallback" — undo that docstring edit.
- `tools/bot_replay.py` / `tests/sim.py`: drop the `log` parameter, `_row_for`,
  the `replay.new_log`/`log.record_turn` calls — back to a plain
  `won`/`turns`/`lost`/`bot_timeouts` row.
- Test files: `tests/test_bot_replay.py` (drop or repurpose), the
  reconstruct-exactness tests in `tests/test_sim.py`, the `botWatchKind` tests
  in `standings.test.mjs`, the `public_watchable_replays` pins in
  `test_schema_grants.py`.
- Docs: CLAUDE.md, `docs/bot-design.md`, `docs/system-design.md`,
  `leaderboard/README.md` all currently describe the stored-log mechanism at
  length and would need rewriting to describe this one instead.

## Bottom line for whoever reopens this

The AI-prediction side is a small, clean change (one line in
`settings.build_state`, zero changes to `knower.py`). The cost moved, it
didn't shrink: it now sits in (a) a small but easy-to-miss double-decide trap
in `main.resolve_turn`, (b) genuinely new replay/reconstruct logic to keep a
mid-match claim resume-safe, which needs its own careful tests, and (c) a new
UI control. Against that: it saves a few hundred KB, worst case, of Supabase
storage and three small schema columns the stored-log mechanism already added
and already works. Re-evaluate with fresh eyes before committing to either
direction — this file is deliberately just the plan, not a recommendation.
