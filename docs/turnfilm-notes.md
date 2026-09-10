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
- **A one-frame snap on every chained turn, reported from play.** Even with
  combat never lingering in history, a run of animated turns still visibly
  hitched: a continuing fleet snapped back by roughly a turn's worth of progress
  for one frame at each boundary, then jumped forward again. Cause: a live End
  Turn's reel always gets a `run_to` call in the same frame it's built (`main`'s
  per-frame update runs right after the event that creates it), but
  `main._next_history_film`'s reel is built *inside* that same update as a side
  effect of the previous one finishing, so it used to sit un-advanced
  (`film_ms == 0`, nothing applied) for the one frame that draws it before the
  *next* frame's `run_to` caught it up — and a fleet's `turns_remaining` a turn
  behind reads as a whole turn's worth of progress behind, per
  `Fleet.progress_at`. Fixed by having `_next_history_film` call
  `reel.run_to(0.0)` (and `Ui.archive_marks`) on the reel itself before handing
  it back, so the launch/first-advance instant is always already applied by the
  time anything draws it.

To re-verify from a clean clone:

```sh
uv run pytest                                    # 777 tests
uv run python -m tests.sim --film --trials 80    # the playback oracle, every turn
```

## Done: the multi-owner pile-up rule change

**Decided 2026-09-09: land this on the `resolve_production_before_combat` branch as
one combined re-tune.** Both needed a `RULES_VERSION` bump and both move balance for
the whole `models/` roster, so they cost one revalidation instead of two. Landed
once the branches were merged — see the merge commit and the follow-up that moved
the production mark's timing, then this fix.

What `combat.resolve_arrival` did before: every side was **pooled per owner** (the
garrison joining its own side), sorted **strongest-first**, then folded
**pairwise**, with `defender_owner=old_owner` applying in *every* step. So the
garrison was not resolved last — it took its place in the queue purely by size.

The expectation it violated was "the incoming fleets fight each other, then the
survivor takes the garrison last". That was exactly what happened *when the
garrison was the weakest side*, which is why the rule was easy to mis-read from
the cases you happened to see.

**Now:** attackers fold pairwise, strongest-first, among themselves; the garrison
— if it's still standing, including any of its own reinforcements arriving this
turn — faces whatever survives that, **last**, regardless of its own size.
`defender_owner=old_owner` still only prices the step the garrison actually
fights, which is now always the final one. Re-measured at zero jitter and
advantage 1.0, same cells as the original table:

| garrison | attackers | before | now |
| --- | --- | --- | --- |
| 10 | 11, 6 | the **6**-ship arrival takes the system, holding 3 | the garrison holds, with **4** |
| 10 | 11, 5 | annihilates to neutral | attackers fold first (11 beats 5, ~10 left), garrison (10) then annihilates it |
| 3 | 10, 9 | attackers fight first, then the winner takes the garrison | unchanged — the garrison was already the weakest side |

Cost paid: `RULES_VERSION` stayed at 2 (the same bump the production/combat
reorder already spent, per the decision above), so every stored replay from
before either change reports `outdated` rather than `mismatch`. Dice draw order
for the *ordinary* two-sided fight also changed — the attacker's jitter is now
drawn before the defender's, always, rather than whichever side has more ships —
which flips which recorded dice produce which outcome
(`test_a_scripted_turn_refights_the_battle_on_the_recorded_dice`) without
changing aggregate win rates (both draws come from the same distribution
regardless of which is drawn first). A `sim --ladder --trials 50` re-run after
both changes lands within noise of the pre-existing roster ranking in
`bot-design.md` (marshal and knower essentially tied at the top, same order down
to rusherplus) — no roster constant needed retuning. What it did *not* cost is any
presentation work — a film is assembled from the order the engine emitted events,
so it depicts whatever the rule is.

`docs/bot-design.md`'s own fold-specific measurements (the "does high advantage
reward simultaneous arrival" study) were taken under the old rule, but every cell
in them used a garrison that was already the weakest side — the one case where old
and new agree — so only the causal explanation needed correcting there, not the
numbers.

## Open: nothing left on this file

Every polish item raised so far is resolved (below), and the pile-up rule change
above is done too, so this file is down to a record of how everything was settled
and can go.

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
  `FILM_COMBAT_MS` is 0; a `+N` over each system that finished a hull is what
  makes the tick visible (`Produced.hulls` -> `Ui.archive_marks` ->
  `Ui.fading_hulls` -> `render._film_labels`). Time was the wrong lever for
  production *unconditionally*: `Film.plays` keys off beat durations, so a flat
  dwell would have made every otherwise-quiet turn pause (measured: 0ms -> no
  film, 250ms -> a 510ms one) instead of resolving instantly. `FILM_PRODUCE_MS`
  stayed 0 for exactly that reason on this branch — see the next bullet for how
  merging with `resolve_production_before_combat` changed the calculus.
- **Merged with `resolve_production_before_combat` (below), and `FILM_PRODUCE_MS`
  stopped being unconditionally 0.** Production now runs before arrivals, so a
  hull finished this turn is in the garrison for the fight right after it in the
  very same turn — and at 0 dwell the `+1` and the fight it fed land on the same
  instant, reading as simultaneous rather than as cause and effect. `turnfilm.film`
  now spends `FILM_PRODUCE_MS` (200) only when the run right after `produce` is
  `combat`; every other turn, including a quiet tick with nothing arriving, still
  gets the 0-dwell instant the measurement above justified. The multi-owner
  pile-up item right below is unaffected and still open — this only closed the
  production/combat *film-timing* half of what this file used to flag as
  deferred.
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
