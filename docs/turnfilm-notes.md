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

Landed on this branch, in four commits:

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

To re-verify from a clean clone:

```sh
uv run pytest                                    # 742 tests
uv run python -m tests.sim --film --trials 50    # the playback oracle, every turn
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

## Open: polish, both small and both optional

Two of the five are left, and both are arguments for leaving them alone as much as
for doing them.

- **Give production a dwell.** `config.FILM_PRODUCE_MS` is 0, so production lands
  at its true point with no pause. The progress ring in `render._draw_systems`
  already animates the tick, so this constant only adds a moment on it. Note that
  `FILM_END_MS` currently carries the job of making that tick readable, since in
  the present phase order it is the last thing to change; if production moves ahead
  of the arrivals, that pressure goes away. Deliberately still 0: the case for a
  dwell is weakest while production genuinely is the last thing that happens.
- **Lane slots re-pack when a fleet arrives.** `render._lane_slot` ranks from the
  oldest fleet on the lane, which is what makes *launching* free — the case that
  actually looked wrong. The remaining case is that a fleet arriving shifts its
  lane-mates one slot inward. Ranking from the newest instead would simply trade
  one for the other; stable in both directions needs per-fleet identity, which
  `Fleet` has none of (no id, unhashable, and two equal fleets compare equal).
  Adding one is a core model change for a presentation artifact that coincides
  with a fleet leaving the board, which is why it has not been done.

## Done: the other three polish items

Kept here only until this file goes, since each moved a rule into `CLAUDE.md` or
`system-design.md` under the matching heading:

- **Fog during a playback is both turns'.** `Ui.film_visible` + `Ui.sees`, unioned
  at the map layer and additive, so nothing is swapped and nothing needs putting
  back. The HUD still reads `visible`.
- **The camera reveal waits for the film.** `Ui.deferred_view_snap`, paid by
  `main.land_film` — the one place the clock running out and a skip both pass
  through. `Ui.stop_film` leaves the debt alone on purpose.
- **A burst says what the fight cost.** `destroyed` on `Clashed` and `Landed`,
  drawn by `render._draw_loss`. Oracle: summed over a turn's events it equals what
  `combat._record_losses` charged the players.
