# Turn playback design notes (`turnfilm.py`)

Why the animated end of turn works as it does: it plays back a turn that has
already resolved. It covers the beat and lead model, the marks that outlive
their film, fog during playback, chaining a run of turns, which presses skip,
pause or pass through a film, lane tracks, and what is deliberately not
animated. `CLAUDE.md`'s "Animated end of turn" states the rules; this file is
the reasoning, with every shape that was tried and dropped. Related:
[`core.md`](core.md) (in-lane battles, whose crossing solve the film reuses)
and [`shell.md`](shell.md). Index: [`../README.md`](../README.md).

## Animated end of turn (`turnfilm.py`)

The turn phase order is deliberate and load-bearing, and it was invisible. That is
the whole motivation: a change of real consequence — moving `_production` ahead of
`_resolve_arrivals`, so a hull finished this turn defends the system it was built
at — left no trace on screen, and nothing distinguished "fleets moved, then fought"
from "everything happened at once". So this is a legibility feature, not eye candy,
and every decision below follows from that.

**It animates the past.** `end_turn` still resolves a turn atomically and the live
board is always fully resolved; the film is a playback onto a deep copy. That was
chosen over interpolating the engine mid-phase because it gives away nothing: no
state is observable mid-flight, so no invariant can be caught broken, skipping is
trivially correct at any instant, and `RULES_VERSION` never moves. The alternative
— pausing the engine between phases — would have put a wall clock inside the pure
core and made every seeded test's timing load-bearing.

**Events carry results, not rules.** `Advanced` carries each fleet's *new*
`turns_remaining` rather than meaning "decrement", and `Landed` carries the node's
new owner and garrison rather than the forces that fought for it. The alternative
is an applier that re-derives outcomes, i.e. the rules written down twice, and the
first version of anything like that drifts. Because applying is pure assignment,
`Reel` can assert something much stronger than "looks right": it never touches
`GameState.rng`, which is checked directly, so a film cannot invent a fight.

**Why its own module rather than more of `engine.py`.** The applier has to be in
the pure core, because it is the one piece of code both the shell animates with and
the tests assert on — in `main.py` the oracle test would need pygame, and
`tests/sim` is deliberately display-free. `fog` is the precedent in the other
direction: presentation-only, pure, and never consulted by the engine. `turnfilm`
is the same deal reversed — the engine writes into it and never reads it back.

**The riskiest line in the feature is a sort key.** `_lane_crossings` already
solved for the sub-turn instant two fleets meet and used it only to order the
fights; surfacing it costs nothing. But it appended `(when, a_i, b_i)` and called
`crossings.sort()`, so ties broke on fleet order — order of launch. Widening that
tuple to carry the crossing *position* would have reordered simultaneous crossings,
and since order decides which fight is dealt the turn's dice first, that silently
moves every stored replay with nothing prompting a version bump. Disjoint
simultaneous pairs make no difference to who wins, which is exactly why it would
have gone unnoticed: the damage is to the dice stream, not the outcome. Hence an
explicit `key=lambda c: (c.when, c.a, c.b)`, and a test that pins it.

**The garrison ticking up at the start is the lesson, not a glitch — and it is now
shown without a beat of its own.** `_draw_systems` shows deployable ships (garrison
minus `Ui.committed`) for your own systems, and `resolve_turn` clears the pending
orders, so a film frame always has an empty queue and shows the real garrison. The
last live frame therefore reads `12 − 5 = 7` and the film opens on `12`, ticking
down to `7` as the launch applies. That step *up* was initially read as a bug and
then kept: the deduction at launch is one of the rules the animation exists to
show. What changed is *when*: `FILM_LAUNCH_MS` was 220 at first — a held beat
before movement began, so the drop was its own visible moment — and is 0 now, the
retreat this constant was named for from the start. Watched one turn in isolation
the held beat read fine; watched turn after turn, particularly in history's own
"play" (`main`'s `ui.playing`), it was a stutter before every glide, and continuous
movement mattered more than a paused view of the deduction. The tick is still
there — a launch still visibly costs the source — but at zero length every launch
this turn lands on the same instant now, the same way `Produced`'s ticks already
do: several launches read as one drop to the post-launch garrison rather than a
visible countdown, because there is no longer a beat wide enough to spread them
across.

**Only movement spends time, and everything else borrows from it.** The first cut
laid beats end to end: each one's constant was a *dwell*, and a turn's film was
their sum. That is why every beat but `move` ended up tuned to 0 — any dwell
anywhere stopped the board, and a stopped board is what the feature exists to
remove — which left the layout doing nothing but adding zeroes together, and the
one beat that still needed a moment of its own (production, below) buying it as a
stall in the middle of the glide. The model now reads the same numbers the other
way round: the move beat is the film, and every other beat is an instant whose
constant is a *lead* — how far ahead of whatever follows it fires, taken out of
the stretch it lands in rather than added to the film. Production lands 200ms
before the fight it fed *while the fleets are still gliding*; a fight lands on the
film's closing instant, which is the very frame the next turn's glide starts on.
A lead is clamped to the room actually there (never past the stretch it borrows
from, never past the beat already placed ahead of it), so a turn with no movement
at all collapses to a single instant and does not play — the property that keeps a
quiet production tick from costing a pause. `linger` is the one thing that still
adds time, and only where a pause is the point (below).

Two consequences are worth stating plainly. `FILM_COMBAT_MS` has to stay 0 in this
model, not as tuning but as truth: a lead on the combat beat would show a fight
before the fleets that fought it arrived. And a film's length is now `FILM_MOVE_MS`
and nothing else, so chained playback (`main._next_history_film`) is continuous by
construction rather than by each beat happening to be tuned to zero — the fleets
of turn *n+1* start moving on the frame turn *n*'s fights fire.

**Production is a mark, not a dwell — except for the one moment it is worth a
breath.** `FILM_PRODUCE_MS` lands at 0 by default, so a finished hull lands at its
true point in the sequence with no pause of its own; what makes it legible is a
`+N` over the map instead, sharing the spot above a system that a fight's `−N`
uses — and a mark is not the film's problem to keep visible (below), which is the
part that changed twice. This was the first beat to go to 0, and for a reason
worth keeping on its own: `Film.plays` is "does any beat have a duration", and a
turn where nothing launches or moves still emits `Produced` on almost every turn
— any system with production left is ticking — so at 0 such a turn has no
playback and End Turn stays instant, while any *unconditional* dwell makes nearly
every turn cost time for a tick nobody needed watching (measured: 0ms gives
`plays` False, 250ms gave a 510ms film).

Production moving *before* arrivals (see `CLAUDE.md`, "Turn resolution") reopened
the question: a hull finished this turn is now in the garrison for the fight that
follows it in the very same turn, and landing on the same instant the `+1` and the
fight it fed read as one indistinguishable flash rather than as cause and effect.
The first fix spent `FILM_PRODUCE_MS` (200) as a real dwell, and only when the run
right after the produce beat was a combat run — the exception carved out purely to
protect the measurement above, since most turns have no combat beat to butt up
against and an unconditional dwell would have made nearly every turn cost time.
The lead model retires the exception: the same 200 is taken out of the glide
instead of stopping it, so the `+1` always lands that far ahead of whatever comes
next and a turn is never longer for it. A turn with no movement to borrow from
spends nothing, which is the old measurement's conclusion arrived at without a
special case.

**Combat is the one beat with two speeds, because watching one turn resolve and
watching a run of them go past want different things.** Once a mark stopped
needing the film's own time to be read at all (below), the instinct was to drop
`FILM_COMBAT_MS` to 0 like everything else — it used to stagger several fights one
node after another over 300ms, purely so each could be read before the next came
up, and a mark surviving its film made that stagger redundant. That held for
history playback, where continuity is the whole point: a run of animated turns
must glide, and stopping for every fight to be read is exactly the stutter the
feature exists to remove now. It did not hold for a single live End Turn, where
the opposite is true — watching your own move resolve is worth a pause, and an
instant fight with no dwell at all read as the game rushing past the one moment
that mattered. So `turnfilm.film(events, linger=True)` is the second speed:
`FILM_LINGER_COMBAT_MS` (300, restoring the old stagger) for the combat beat, plus
a trailing `FILM_LINGER_HOLD_MS` (250) so the resolved board holds a beat before
control returns. `main.resolve_turn` passes `linger=not ui.playing` and
`main._next_history_film` never lingers at all, so the line falls where the
difference actually is: a turn you ended by hand pauses, and a run of turns —
live play or history playback — never does. Nothing about a mark's own lifetime changes either way — `Ui.fading_
fights`/`fading_hulls` keep a fight or a tick up on their own clock regardless
of which speed the film that made it used (below), so lingering only changes how
long *the film* holds the board, never how long the mark on it stays visible.

Only ticks that finished something are marked. `Produced.ticks` reports every
system whose ships or progress moved, which is most of the map, and the progress
ring `_draw_systems` already draws is what shows the rest inching along —
labelling those would bury the board. That is why the event carries the hull count
as a *delta* (`Produced.hulls`): it is the one figure here unrecoverable
afterwards, since the count the ship was added to is gone by the time anything
draws.

**A mark outlives the film that made it, which is what finally let the dwells go
to zero without losing anything.** Two things were tried before this one stuck.
First, the loss label and the hull mark simply expired on a flat `FILM_FLASH_MS`
(260ms) after the cue that made them — fine for a single fight read in isolation,
but a number visibly vanished mid-turn on a busy combat beat, and a system that
both fought and produced only stacked its two marks when timing happened to make
the windows overlap. Second, that was replaced with a dissolve stretched across
whatever was left of the *film's own* `total_ms` — better, but it meant a film had
to keep `total_ms` open long enough for its marks to be read, which was still a
pause however short, and it could not survive the very next idea: once history
playback stopped needing combat to hold the film open at all (its own dwell
having gone to 0, below), a mark firing right at a film's end had almost no
runway left to fade in, and the *next* turn's playback had nowhere to put a
still-fading mark once the film that made it was replaced.

So a mark was moved off `Film` entirely. `Ui.fading_fights`/`fading_hulls` hold
every still-showing fight or finished hull as plain data
(`viewstate.FadingFight`/`FadingHull`) — populated by `Ui.archive_marks(board,
events)` from whatever `Reel.run_to` just applied (which is why `run_to`/`run`
hand that list back instead of nothing) and aged every frame by
`Ui.age_fading_marks(dt)` regardless of whether a playback is currently running at
all. Each is fully visible for `config.FILM_FLASH_MS`, then fades over
`config.FILM_FADE_MS` (`render._mark_fade`) — both counted from when it fired, on
its own clock, never from any film's length. `render._faded` does the blending
(there is no per-pixel alpha on the main surface, and a pill's own backing is
already `COLOR_BG`, so its text sinks into its own backing rather than shifting
hue), and it is what a burst's stroke fades with too; `config.FILM_FLASH_MS` also
still times a burst's own pop-in geometry (its rings settle and its spokes retract
over that same window), which is why the two coincide rather than needing a third
constant. Visibility (`Ui.sees`) is checked once, at archive time, rather than
every frame a mark is drawn: a fight you saw fire keeps fading regardless of what
fog does afterward, instead of blinking out mid-dissolve because the *next*
turn's own fog happens to differ. And because a mark's life is no longer any
film's business, a "jump" rather than a step through the turns — entering or
leaving history, scrubbing, rewinding — calls `Ui.clear_fading_marks()`
explicitly: a mark belongs to a specific point in a specific playback, and
jumping away from it makes it stale rather than merely old.

The payoff is what let every beat stop spending time of its own without losing
legibility anywhere: `film()` no longer pads `total_ms` (it used to outlive its
last cue by `max(FILM_END_MS, FILM_FLASH_MS)`, which both constants existed for)
— a film now ends the instant its last beat does, and the *next* turn's move beat
starts on the very next frame, with whatever marks the previous turn produced
still dissolving on top of it. That is what makes a run of
animated turns in history playback (`main._next_history_film`, chained the
instant a film lands rather than waiting out `PLAY_MS`) glide continuously
instead of visibly stopping for every fight to be read.

"By default" is doing real work in that sentence: a live End Turn wants the
opposite, and gets it through `linger` rather than a special case bolted onto
this mechanism — see "Combat is the one beat with two speeds" above. Because a
mark's own visibility never depended on the film either way, lingering costs
nothing but the pause it is *for*.

**A mark no longer needs a `Film` to exist at all, which is what let the
preference default on without forcing the glide on with it.** Once the animated
end of turn became the default rather than an opt-in, the question turned around:
was the glide itself worth losing for someone who only wants to *read* what a turn
did? `archive_marks(board, events)` already took a plain event list rather than
anything shaped like a film — it was written that way so `_primed` could call it
on a reel's very first instant, before any beat had actually played — and that
turned out to be the whole answer: with turn animation off, `main.resolve_turn`
and `main._next_history_film` call it directly on the turn's full, ungrouped event
list (no `turnfilm.film()` beat-grouping, no `Reel`), so every mark the turn earned
fires at once instead of in sequence, then fades on the same clock a glide's marks
would have. The gate that used to be one boolean is now two: `marking` (excludes
only autoplay and fast-forward — a fight is cheap to report and something is always
watching a turn that isn't either of those) and `filming` (`marking` narrowed by
the animate-turns preference, which now governs only the glide). `film_visible` is
still the fog union the glide path uses, set for the archiving call and dropped
right after so a render pass that runs with no film up never sees it.

**Fog is both turns', and that is not the same as either one.** The destination
turn's alone was the first cut, and right about the direction: holding the *earlier*
fog would have an inbound fleet pop into existence halfway down its lane, which is
precisely the discontinuity the feature exists to remove. A film is a report on a
turn that has already happened, drawn with the fog of the board you are about to be
handed. But `visible` is not monotone, so the destination turn on its own drew a
system lost this turn as a grey "?" *while the fight that took it played out* —
needing `sight = 1` and a frontier system with no surviving owned neighbour, so it
could not arise at the fog-off default, but wrong wherever it could.

The fix is a union rather than a swap. `Ui.film_visible` carries what was visible
when the played-back turn began, `Ui.sees` is `visible` plus that, and the map layer
— nodes, fleets, bursts — asks it instead of reading `visible`. Additive is the
whole point: `visible` is never overwritten, so there is nothing to put back when a
film ends or is skipped, and a missed restore cannot leak a system you can no longer
see into the board you play the next turn on. The HUD keeps reading `visible`
directly, since the scoreboard and the info panel describe the position you are
being handed rather than the one being drawn.

**The reveal on the deciding turn waits for the film.** `resolve_turn` snaps the
camera out to the whole map on the turn the game is decided or the human seat is
knocked out, and doing that *before* the playback meant the last turn — the one
worth watching — played out on a map that had already given its ending away, with
the frame yanked out from under the fight as well. So the snap is deferred:
`Ui.deferred_view_snap` records the debt and `main.land_film` pays it. What makes
one function enough is that both endings pass through it — the clock running out and
a press skipping (`input` calls `Ui.stop_film`, which deliberately leaves the debt
unpaid, precisely so a skip cannot lose the reveal — Play/Pause is the one press
that does something else instead, see below, and it never reaches this deferral at
all since pausing does not end the playback). A turn whose film has nothing in it
(`Film.plays` false, e.g. a decided board with nothing left in transit) snaps
immediately, exactly as before films existed.

**Pausing freezes a film; it does not skip it.** The blanket "any press skips a
playback" rule existed because a film has nothing worth keeping once you have
looked away — but Play/Pause is pressed *to keep looking*, and skipping on it read
exactly like the bug it was: the turn's marks and mid-flight fleets vanished,
replaced by the plain fully-resolved board, the moment you tried to pause on them.
`input._toggles_play` is the one exemption from the skip rule (the P key, or a
click on the shared Play/Pause button), and `Ui.film_paused` is what a paused frame
actually rests on: the main loop's per-frame `film_ms += dt` is skipped while it is
set, so the board stays exactly where it was — mid-glide, mid-fade, whatever was on
screen — rather than jumping anywhere. It is deliberately not the same flag as
`Ui.playing`: `playing` decides whether *further* turns start once this one ends,
and a manually-triggered film (a single End Turn press, outside any "play through
turns" mode) runs with `playing` False the whole time it plays, so gating the
freeze on that would pause it on its very first frame. `main.apply_toggle_play`
reads the button's own state going in (`was_playing`) to tell the two cases apart:
pausing an *already running* sequence freezes the film in front of you, while
starting one from a standstill — the button reads "Play", not "Pause" — leaves a
film already in flight alone and only arms auto-advance for whatever comes next.

**Both kinds of run chain an animated turn straight into the next one.** Turn
resolution itself is instant; only the film is timed, so once launch and the old
per-turn hold stopped costing anything (above), the one dead stretch left in a
back-to-back review was the gap `main` inserted *between* two turns' playbacks —
`PLAY_MS` (350ms) of nothing, paced for stepping through turns with no animation at
all, applied unconditionally regardless of what came next. `main._next_history_film`
is now tried the instant a film lands, before that pacing ever gets a chance to
run, so a turn that also animates starts moving immediately and a run of them
glides as one continuous playback rather than a stutter of holds. The `PLAY_MS`
step still exists and still matters — it is what paces a *quiet* turn (nothing to
animate, or the preference off), where there is no film to chain into and a human
still needs long enough to read the board before it moves on.

Live play (`ui.playing`, the P key) is the same behaviour from a different source
and now takes the same path: when a film lands there the loop calls `resolve_turn`
immediately, on exactly the terms the `PLAY_MS` branch below it would have
resolved on (not decided, not in route mode). Reviewing a recorded run and
watching a live one are the same activity — a run of turns going past — and only
the *source* of the next turn differs, so they had no business feeling different.
The distinction that does survive is between a run and a single turn: `linger` is
now `not ui.playing` in `resolve_turn`, so ending a turn by hand still gets the
pause worth having (see "Combat is the one beat with two speeds") and a run never
does.

**Chaining hid a one-frame snap, because the new reel wasn't advanced before it
was drawn.** A reel built from the *event* handler gets a `run_to` call in the
very frame it's built — `main`'s per-frame update runs right after the handler
that calls `resolve_turn`, in the same pass through the loop — so by the time
that frame draws, whatever fires at the very first instant (a launch, and the
turn's first `Advanced`) has already been applied. A *chained* reel is built
inside that same per-frame update, as a side effect of the previous reel
finishing, so there is no second pass through it left this frame to apply
anything: it used to sit there with `film_ms == 0` and nothing yet applied until
the next frame. For a fleet that only continues (never launches or lands this
turn), that is a real, visible regression: its `turns_remaining` is still last
turn's value for that one frame, and `Fleet.progress_at(0)` with the old value
reads a whole turn *behind* where the previous turn's last frame just left it — a
snap backward by exactly `1 / turns_total`, corrected again the very next frame
once `run_to` caught up.

`main._primed` is the fix and now every reel goes through it, `resolve_turn`'s
included: it applies whatever is due at `0.0` (and archives whatever marks that
makes) before handing the reel back. Putting it on the one path that needed it
would have left the same trap set for the next caller — which is exactly what
happened when live play started chaining too, since `resolve_turn`'s own reel is
built in that branch of the update rather than from the event handler.

**A join also has to carry the previous film's overrun, and cap what a slow frame
may spend.** Both are about the same thing — the loop's clock is frames, and a
frame is not a fixed slice of wall clock — and both were only ever visible under
autoplay, which is why it looked choppier than history playback of the very same
turns.

*Overrun.* A film lands on the first frame whose `film_ms` reaches `total_ms`,
which is almost never exactly its end: the step is a whole frame, so on average
half a frame lands past it. Starting the chained film at `0.0` throws that away,
so every single join holds the fleets still for the remainder of a frame. One
frame in sixty is not much; sixty of them, one per turn, is the difference between
a glide and a shimmer. `main._carry_into` pays the overrun into the new film
instead (clamped to that film's own length, so a carry larger than a whole
playback lands it next frame rather than leaving the playhead past its end), which
is what makes a chained run advance at exactly one film-length per film.

*Frame cap.* Resolving a turn is the loop's one genuinely expensive step — every
seat's `decide`, plus rewriting the log — and under autoplay or play mode it
happens in the very frame that lands one film and starts the next. `clock.tick`
hands that whole stretch to the frame *after*, and with a search bot on the board
it can run into the hundreds of milliseconds: charged in full to the film that
just started, the fleets teleport a third of the way down their lanes before the
glide takes over. `config.MAX_FRAME_MS` caps what one frame may hand on (three
frames at the target rate), so a long frame makes the animation fall behind the
wall clock rather than jump — the right trade for a playback that is a
fixed-length glide synchronised to nothing. History playback resolves nothing and
so never met either problem, which is exactly why the two looked different.

**The camera controls are the second exemption from the skip rule.** Reset view,
the on-map zoom `−`/`+` and the `R` key change where you are looking from and
nothing about the turn being played back, so `input._moves_camera` lets them past
the blanket "any press skips a film" the same way `_toggles_play` does (the wheel
was already past it, by not being one of the press types the rule names). Under
autoplay that is the difference between working and not: films chain back to back
with no gap, so the press would be spent skipping one, and the next turn's film is
already up by the time a second press arrives — the whole cluster reads as dead.

**Autoplay / Take control is the third, and the sharpest case of it.** Play/Pause
is hidden under autoplay — the footer's own comment says why: turns advance on
`AUTOPLAY_MS` regardless of `ui.playing` there, so pausing would be a no-op — which
makes Take control the *only* way to stop the automatic advance at all. Without
`input._toggles_autoplay` exempting it (the A key, or its footer button), the same
chaining that broke the camera cluster broke this control outright: a press during
a running film was spent skipping it, and by the time a second one landed the next
turn's film was already up, so there was no way to ever actually take control back
while autoplay was running. Nothing here needs freezing the way Play/Pause does —
the film already showing plays out exactly as it would have, and control is simply
back the instant it lands, since `main`'s chaining re-reads `ui.autoplay` fresh at
that point rather than caching the value from when the film started.

**The loss label is the victor's own, in the victor's colour.** Both sides'
losses together was the first cut and it was the wrong number: 9 ships taking a
6-ship system read `−8`, which is almost entirely the defender's garrison — wiped
out by definition, and visible anyway as the count on the node changing — while
burying the 2 the attacker actually paid, which is the figure the square law makes
hard to guess. So `cost` is what the fight cost whoever came out of it holding the
ground (or still flying), and it is drawn in that player's colour, since colour is
the one language this map already uses for whose something is. Nobody's, on
annihilation: matched forces leave no victor to charge, and the emptied board says
it. A per-side pair of numbers was the other candidate; it doubles the label's width
at a node for a second figure the board already shows, and a multi-owner pile-up
would need three.

The accounting figure survives beside it as `destroyed`, because it is the test:
what everyone brought less what the winner kept is `combat._record_losses` read from
the other end, so summed over a turn's events it must equal what the scoreboard
charged every player. The name pass's share of this is scoped by the same generator that draws the
labels (`_flash_marks`), which yields nothing without a film — and there is no film
unless turn animation is on. So a star name only ever yields ground for the moment
a burst is actually up, and with the feature off names are placed exactly as they
always were. Sharing the generator is the point: the space reserved and the label
drawn cannot come apart.

`Landed.sides` is what makes both derivable — arrivals are
pooled per owner before anything fights, so every side appears in `steps` exactly
once (the strongest as the first step's carried force, the others as each step's
defender), which is the per-side detail the pooling in `_record_losses` used to
lose. And a `Clashed` has to carry `survivor_owner` outright: a fleet id cannot be
resolved to a player from a board the fleet has already left.

**The two marks share one spot, and one of them moves.** A fight's cost and a
finished hull are both written a fixed step above the system, which collides by
design rather than by accident: production now runs *before* combat, so a hull
finished this turn is credited to whoever held the system going in — the
defender, per `Ui.archive_marks`' own rule that a `Produced` mark's colour reads
the board *at the time*, before any later `Landed` gets a chance to change who
owns it — and if the extra ship still isn't enough, the very same system earns a
combat mark moments later. The gain stacks a row above the cost — by the font's
own line height, per the no-fixed-pixel-sizes rule — and `render._film_labels`
returns both already placed, so the star-name pass reserves the space they
actually occupy without re-deriving it.

The two never fire at the same instant, which is what production's `FILM_PRODUCE_MS`
lead is for (above): a `+1` sharing its instant with the fight it just fed would
read as one indistinguishable flash rather than as cause and effect. Since a mark
lives on `Ui`, not `Film` (below), the gap does not have to be small for the marks
to still stack: as long as it lands within the fight mark's own ~760ms life
(`FILM_FLASH_MS + FILM_FADE_MS`), which 200ms comfortably does, a single arrival
that both survives a hull's completion and then loses the system anyway still
shows both, one above the other, for as long as either is still up.

**A second mark at one system supersedes the first.** A mark outliving its film
(below) is what makes a chained playback continuous, and it is also what makes one
node collect them: a turn is `FILM_MOVE_MS` long and a mark lives ~760ms, so a
system fought over — or finishing a hull — two turns running is still showing the
older mark when the newer one fires. Two bursts at one centre with two numbers in
one slot read as a single garbled figure rather than as two events, so
`Ui.archive_marks` drops any fight still fading at that node when a new fight
lands there, and likewise for hulls. Per system, not globally: a mark dissolving
somewhere else is unrelated. Open-space clashes are exempt, since they are placed
at the lane fraction they happened at rather than on a node.

**The scrubber advances at a transition's end, not its start.** The top bar reads
the board being drawn, so a leading playhead would have the scrubber and the turn
counter disagree for a second at a time. A position readout that clicks over on
arrival is the better of the two, and it also means `history_states[i]` is never
mutated — a transition gets its own copy.

**No in-game toggle.** `_draw_footer_buttons` is squeeze-ranked, dropping the least
essential controls when the strip is narrow, so a once-set display preference would
rank below Clear and vanish exactly on the phone where it matters most — while
costing a rank slot for every button that stayed. The menu checkbox is enough, and
the immediate needs are already served: any press skips a playback, and P (or its
footer button) actually pauses one now — freezing it in place rather than
discarding it, which is what `Ui.film_paused` and `input._toggles_play` are for.
Fitting that eighth Basic row is what took `_ROW_H` from 62 to 58; at 62 it
hung 6px out of the fixed 560x496 panel and failed
`test_tab_content_stays_inside_the_panel`.

**A fleet's approach stops at the rim rather than snapping back off the centre.**
An arrived fleet is held `node_radius + FILM_ARRIVAL_GAP` clear of its
destination so it cannot cover the garrison count underneath it — but the glide
draws it closing on the node's *centre*, so applying that offset only once the
fleet had landed (`progress_at(travel) >= 1.0`, true on the move beat's last frame
alone) jumped the triangle backwards by a whole radius, one frame before it
vanished into the fight. It reads as the fleet bouncing off the system it just
reached, and it was the only place a playback ever moved a fleet the wrong way.
`render._fleet_at` clamps instead: for a fleet arriving this step
(`turns_remaining <= 0`) the drawn point is never closer to the destination than
that gap, so the last few pixels of the approach compress into a stop at the rim.
Splitting the placement out of `_draw_fleets` is what makes it checkable —
"distance to the destination never increases" is a property of a list of numbers,
not of a screenshot — and it puts the lane track (below) in the same one place.

The one thing the clamp gives up: an in-lane clash is flashed at the crossing
fraction `engine._lane_crossings` solved for, so a fight resolved inside those
last few pixels can be marked up to a gap away from where the clamped triangle
is drawn. Only a fleet landing this step is ever clamped, and a clash that close
to a node is a fight the arrival was about to end regardless, so the divergence
is bounded by `node_radius + FILM_ARRIVAL_GAP` in the one case it can occur —
against a backwards jump of exactly that size on every single arrival.

**A lane track is held, not ranked.** `_lane_offsets` used to spread a lane's
fleets symmetrically across however many were currently on it, so launching one
more re-centred the group and every fleet already in flight stepped sideways —
invisible when the board jumped a whole turn at a time, obvious once they glide.
Claiming slots outward from the centre line *in launch order* fixed the launch and
left the mirror case: a fleet **arriving** re-packed the ranks behind it, so the
fleets still strung down the lane jumped sideways as the leader landed. Ranking
from the newest would only have traded one for the other.

Both go away once a fleet is *given* a track and keeps it: `Fleet.lane_slot`,
handed out by `model.free_lane_slot` at launch and read straight off the fleet by
`render`. A track is freed only by the fleet holding it leaving the board, so no
two fleets on a lane ever share one, and the next launch reuses the freed centre.
Both directions draw from one pool, since fleets running opposite ways pass
through each other and that is the case the separation exists for.

Two things this cost, both accepted deliberately. A fleet whose lane-mates have
gone keeps flying one step off the centre line instead of sliding back onto it —
a stationary offset rather than a jump, and it heals as soon as anything launches
into the freed centre. And it is a stored field on a core dataclass whose only
reader is `render`: the alternative was deriving a track from the launch turn,
which `turn - (turns_total - turns_remaining)` recovers exactly, but turn playback
applies those two at different cues (`Advanced`, then `Ended`), so every fleet
would shift a track mid-animation. Storing it also means `turnfilm.Launched` has
to carry it, or a film's own fleets would jump sideways at the moment it ended —
which is why `lane_slot` is in the board digest both film oracles compare.

**A burst marks a fight, and `Landed.steps` is the test for one.** The first
version marked every arrival, which flashed combat over your own fleets
reinforcing your own system. `steps` holds the engagements that actually happened
and is empty precisely when nothing fought — a reinforcement, or a walk into an
empty system. Neither draws a mark; the garrison count changing is the whole of
what happened. Note that this deliberately covers the bloodless capture too:
taking an undefended neutral system is not combat, and the node changing colour
says it.

**What is deliberately not animated.** The AI's decision phase (invisible by
nature; the orders show up as launches). Camera moves toward the action, which would
fight `ui.view` — the one thing `input` owns — and put mutation in the film path.
And `Player.ships_lost` rides on the closing event as a whole-table snapshot rather
than per-engagement deltas, which is what keeps `_record_losses` and the rest of
`combat` out of this entirely — the loss labels are derived from the fold instead,
which is why they cost `combat` nothing.
