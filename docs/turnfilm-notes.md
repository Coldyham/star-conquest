# Animated end of turn — working notes

**Temporary.** These are open items for the `animated_turn_resolution` branch, kept
in the repo only so the work travels between machines. Move them into gitignored
notes once the feature is finished, and delete this file. Nothing in `CLAUDE.md`
references it, so removing it is a single deletion.

Settled design lives in the two permanent places instead:
[`CLAUDE.md`](../CLAUDE.md) under *Animated end of turn (turnfilm.py)* for the
rules that must hold, and [`system-design.md`](system-design.md) under the matching
heading for why they hold.

## Where it stands

Landed on this branch, across these commits:

- **Prep.** `Fleet.progress_at` as the one sub-turn position formula,
  `engine._lane_span` refactored onto it, and `engine._lane_crossings` returning
  the crossing instant it used to discard — behind an *explicit* sort key. No
  behaviour change: 120 seeded games with in-lane battles on, at 0% and 1% speed
  growth, hash byte-identically against the previous code.
- **The feature.** `turnfilm.py` (events, `Film`/`Beat`/`Reel`, the watcher),
  `engine.end_turn(on_event=…)`, `combat.resolve_arrival(on_step=…)`, the render
  layers, the skip, the local preference, and history playback.
- **Two fixes the playback exposed**: lane slots are claimed rather than shared
  out, and a burst marks a fight rather than any arrival.
- **Three of the polish items**, each with its own tests: the map's fog during a
  playback is now the union of both turns (`Ui.sees`), the camera reveal on the
  deciding turn waits for the film to land (`main.land_film`), and a burst is
  labelled with what the fight cost (`turnfilm.Clashed.destroyed`,
  `turnfilm.Landed.destroyed`).
- **A smoothing pass**, once the feature had been lived with a while: `FILM_LAUNCH_MS`
  dropped to 0 (folded into the move beat, matching `FILM_PRODUCE_MS`), a mark
  faded across the rest of the turn instead of expiring on a fixed `FILM_FLASH_MS`
  window, history playback chained an animated turn straight into the next one
  instead of pacing every transition at `PLAY_MS` (`main._next_history_film`), and
  Play/Pause actually paused a running film instead of silently skipping it like
  every other key (`Ui.film_paused`, `input._toggles_play`,
  `main.apply_toggle_play`).
- **A second smoothing pass**, once the first one still stuttered: movement still
  paused for every fight to be read, because a mark's fade was tied to the film's
  own `total_ms`, and that meant the film had to stay "current" for as long as a
  mark needed to be visible. A mark now lives entirely off `Film` —
  `Ui.fading_fights`/`fading_hulls`, populated from whatever `Reel.run_to` just
  applied (`Ui.archive_marks`) and aged every frame regardless of what's playing
  (`Ui.age_fading_marks`), fully visible for `FILM_FLASH_MS` then fading over a new
  `FILM_FADE_MS`, both counted from when it fired rather than from any turn's
  length. That let `FILM_COMBAT_MS` join launch and production at 0 (a fight no
  longer needs a held beat to be read at all) and let `film()` drop its trailing
  pad entirely (`total_ms` now ends the instant its last beat does) — so the next
  turn's move beat can start on the very next frame, with the previous turn's
  marks still dissolving on top of it. `Reel.run_to`/`run` now hand back what they
  just applied, which is how `archive_marks` gets at newly-fired events without
  rescanning `film.cues`.
- **Combat got its dwell back, but only where it was never the problem.** The
  second pass's "every fight pops at once" turned out to be one speed too few:
  a live End Turn is worth watching resolve (that pause was the point), while a
  history replay must never stop for one (that pause was the bug). `turnfilm.film`
  takes a `linger` flag now — off by default (`FILM_COMBAT_MS` 0, no trailing
  pad), and `main.resolve_turn` passes `linger=True` for a live turn, which swaps
  in `FILM_LINGER_COMBAT_MS` (300, the old stagger) and adds a trailing
  `FILM_LINGER_HOLD_MS` (250). `main._next_history_film` never lingers. Nothing
  about `Ui.fading_fights`/`fading_hulls` changes either way — a mark's own
  lifetime was already independent of the film, so `linger` only changes how long
  *the film* holds the board, never how long a mark stays visible on it.

To re-verify from a clean clone:

```sh
uv run pytest                                    # 776 tests
uv run python -m tests.sim --film --trials 80    # the playback oracle, every turn
```

## Open: the multi-owner pile-up rule change

**Decided 2026-09-09: land this on the `resolve_production_before_combat` branch as
one combined re-tune.** Both need a `RULES_VERSION` bump and both move balance for
the whole `models/` roster, so they should cost one revalidation instead of two.
Deferred deliberately — not forgotten, and not a bug.

What `combat.resolve_arrival` does today, none of which is visible in play: every
side is **pooled per owner** (the garrison joining its own side), sorted
**strongest-first**, then folded **pairwise**, with `defender_owner=old_owner`
applying in *every* step. So the garrison is not resolved last — it takes its place
in the queue purely by size.

The expectation it violates is "the incoming fleets fight each other, then the
survivor takes the garrison last". That is exactly what happens *when the garrison
is the weakest side*, which is why the rule is easy to mis-read from the cases you
happen to see.

Why it is worth changing: the weakest arrival is often the best seat, because it
fights whatever is left. Measured at zero jitter and advantage 1.0 —

| garrison | attackers | outcome |
| --- | --- | --- |
| 10 | 11, 6 | the **6**-ship arrival takes the system, holding 3 |
| 10 | 11, 5 | annihilates to neutral |
| 3 | 10, 9 | the attackers fight first, then the winner takes the garrison |

What a change would cost: a `RULES_VERSION` bump, so every stored replay reports
`outdated` (unverifiable) rather than `mismatch` from `tools/verify_scores.py`; and
a re-measure of the roster (`sim --ladder`). What it would *not* cost is any
presentation work — a film is assembled from the order the engine emitted events,
so it depicts whatever the rule is.

## Open: nothing on the polish list

Every polish item raised so far is resolved (below). What is left on the feature
is the one deferred rule change above, which belongs on another branch by
decision, so this file is down to a record of how the open items were settled and
can go with the branch.

## Done: the polish list

Kept here only until this file goes, since each moved a rule into `CLAUDE.md` or
`system-design.md` under the matching heading:

- **Fog during a playback is both turns'.** `Ui.film_visible` + `Ui.sees`, unioned
  at the map layer and additive, so nothing is swapped and nothing needs putting
  back. The HUD still reads `visible`.
- **The camera reveal waits for the film.** `Ui.deferred_view_snap`, paid by
  `main.land_film` — the one place the clock running out and a skip both pass
  through. `Ui.stop_film` leaves the debt alone on purpose.
- **Production (and now combat) is marked rather than dwelt on.**
  `FILM_PRODUCE_MS`/`FILM_COMBAT_MS` are both 0; a `+N` over each system that
  finished a hull is what makes the tick visible (`Produced.hulls` ->
  `Ui.archive_marks` -> `Ui.fading_hulls` -> `render._film_labels`). Time was the
  wrong lever for production specifically: `Film.plays` keys off beat durations,
  so a dwell would have made every otherwise-quiet turn pause (measured: 0ms -> no
  film, 250ms -> a 510ms one) instead of resolving instantly.
- **A lane track is held rather than ranked.** `Fleet.lane_slot`, handed out by
  `model.free_lane_slot` at launch — which is what the notes said would need
  per-fleet identity, and it does; the field *is* the identity. Reported from play:
  a fleet arriving re-packed the ranks behind it, so the fleets still strung down
  the lane jumped sideways as the leader landed. `turnfilm.Launched` carries it,
  and both film oracles' digests now compare it.
- **A burst says what the fight cost its winner.** `cost`/`victor` on `Clashed`
  and `Landed`, labelled in the victor's colour; `destroyed` (everyone's losses)
  stays as the oracle, since summed over a turn's events it equals what
  `combat._record_losses` charged the players.
- **Dwells reduced, history chains, and Play/Pause actually pauses.**
  `FILM_LAUNCH_MS` is 0, matching `FILM_PRODUCE_MS` — a launched fleet already
  starts its glide at progress 0, so the held beat only bought a stutter before
  movement began, and it mattered more watched turn after turn than in isolation.
  `main._next_history_film` is tried immediately once a film lands, so an animated
  turn chains straight into the next one instead of waiting out `PLAY_MS`
  regardless. And `Ui.film_paused` (with `input._toggles_play` exempting
  Play/Pause from the blanket skip rule) means pausing freezes a playback in place
  instead of silently discarding it — the docs had already claimed "P pauses one"
  before this; it just wasn't true.
- **A mark outlives the film that made it, so combat could go to 0 too and every
  turn's motion could finally be continuous.** Fading a mark against the film's
  own `total_ms` (previous bullet's era) still meant the film had to stay current
  for as long as a mark needed reading — `Ui.fading_fights`/`fading_hulls` move
  that entirely off `Film`, populated by `Ui.archive_marks` from whatever
  `Reel.run_to` just applied and aged every frame by `Ui.age_fading_marks`
  regardless of what's playing. Fully visible for `FILM_FLASH_MS`, then fading
  over a new `FILM_FADE_MS`, on their own clock. That let `FILM_COMBAT_MS` join
  the others at 0 and let `film()` drop its trailing pad outright, so the next
  turn's move beat starts on the very next frame with the previous turn's marks
  still dissolving on top of it — the actual continuous glide across turns that
  the previous pass only approximated.
