# Run lives, and generated gauntlets that branch

*Working plan, kept here so the work can move between machines. Planned
2026-11-12 at `296af51`. Not part of the design docs: as each part lands, its
reasons move into `gauntlet.md` (*Runs*) and `gauntlet-gen.md`, and this file is
deleted. It replaces the lives in Phase 5 of
[`graphical-gauntlet-maker.md`](graphical-gauntlet-maker.md); boons stay
there, parked (see "Boons", below).*

There are two parts. Each can be built on its own, but each gives the other
more point:

1. **Lives, and a run that may play any stage it has unlocked.** A loss costs
   a life rather than ending the run. The next go can be the same stage again
   (it was close) or another unlocked stage (the other track looked easier).
2. **Branching shapes for the generator.** *Concertina* (1 → 2 → 1 → 2 … → 1)
   and *cross-stitch* (2 → 2 → … → 1, each stage leading to both of the next).
   The two stages in a column are about as hard as each other, but differ in
   kind: in at least one of size, lane length or layout.

Build part 1 first. On today's gauntlets it is useful on its own: the
tutorial's bonus stage, a board-made fork. It is also what turns part 2's
second track into a real choice rather than a coin toss at the start.

## Part 1: lives, and moving over everything unlocked

### What changes from the run built in *Runs* and *Forks*

Today a run moves only from the stage it last won (`run.ways_on`), and anything
but a win within target ends it (`run.finish`). From now on:

- **The run's frontier is every stage it has unlocked and not yet won**
  (`run.frontier`, which replaces `ways_on`). A stage is unlocked if:
  - it is a start;
  - an ungated lane leads to it from a stage the run won; or
  - a gated lane leads to it from a stage the run won *with a game whose own
    score met the gate*. Bests still never count.
- **A stage is behind the run once a stage it leads to is won.** It leaves the
  frontier. In a concertina, the unplayed half of a pair closes once you win
  the waist after it, so a run never wanders back. A dead end (a bonus stage
  with no lane out) is never behind anything, so it stays on the frontier until
  it is played. That's the point of it: by default a dead end grants a bonus
  life (see "The format: lives and bonus lives").
- **A missed gate can be tried again for a life.** A won stage with a gated
  lane out that the run hasn't met stays on the frontier, even once it is
  behind, since the gate is a bonus and worth a life to some players. A replay
  is judged against the gate rather than the stage target. A game that meets
  the gate opens the far stage, and anything else costs a life, whether a loss
  or a win short of the gate. Otherwise a player could keep winning it and
  missing the gate for free. Today a missed gate is lost for good.
- **A loss costs a life.** LOST, MISSED (won, short of the target) and VOID
  (not won by hand) each cost one. The stage stays on the frontier. GONE and
  ABANDONED still end the run outright, since restarting would reward a reload
  or a quit.
- **Only running out of lives ends the run** (beside GONE and ABANDONED). With
  a missed gate replayable, the frontier can't empty before a goal is won: an
  ungated way always leads on to a goal (`gauntlet.goals`), and anything else
  left can be replayed for a life. So no state is a dead end, and what comes
  next is always the player's choice.
- **Winning a goal completes the run** (`gauntlet.goals`, already derived).
  This replaces `run.ends`' "a win with no ungated way on". Once any stage can
  be played, a won dead end mustn't end the run while other tracks are still
  open.

Everything else in *Runs* stands:
- run games are ordinary games that post and file bests;
- ironman while undecided, and no Hint;
- the run judges only its own game;
- Continue run resumes the step's own log;
- one run at a time;
- an edited gauntlet ends its run.

### The record (`run.py`)

- **`Run.lives`**: the lives the run started with, copied from the
  gauntlet's `lives` when it starts, so a later edit to the gauntlet never
  changes a run already under way. Written only when it isn't 1. A record
  without it reads as 1, so a run started before this ships finishes under the
  rules it began with.
- **`lives_left` is derived** from the steps: `lives`, plus one for each first
  MET win on a stage that grants a bonus life, minus each decided step whose
  outcome costs one. The steps are already the complete record (memory:
  complete records beat compact ones), so a stored count could drift. Which
  stages grant a bonus is read from the gauntlet, which the fingerprint pins
  (below).
- **`finish`** sets the run to OVER only when no lives are left, and to COMPLETE when the stage is in `goals(g)`. It needs
  the gauntlet, or the goals passed in. `app.record_run_step` already has `g`
  to hand through `run_ui`.
- **`cleared`, `position`, `summary`**: still counted in MET steps. "Best run"
  keeps meaning stages won. A step that cost a life is listed in the run's
  history but doesn't advance it.
- **`stage_to_play(r, g, pick)`**: `pick` if it is on the frontier, else the
  only stage on the frontier, else None (the list chooses). This is the same
  rule as today, applied to the frontier.
- **`fingerprint`** adds the set of stages that grant a bonus life, but only
  when that set isn't empty, so a gauntlet with no bonus stage hashes as
  before. A gauntlet that already has a dead end gains a bonus by default, so
  its hash moves once and any run under way on it ends. Today no gauntlet
  that offers a run has a dead end; the tutorial does, but it offers no run.
  The gauntlet's `lives` stays out of the hash, because the run keeps its own
  copy.

### The format: lives and bonus lives

- **`lives`** is a top-level field holding the lives a run starts with: an int
  from 1 to `gauntlet.LIVES_MAX` (9), defaulting to 1, which is today's run.
  `from_dict` clamps it, and `to_dict` writes it only when it isn't 1.
- **`bonus_life`** is a per-stage flag: winning the stage within its target
  in a run grants one more life, once per run. **Its default follows the
  graph**: on for a dead end (a stage with no lane out that isn't a goal,
  such as the tutorial's "Long odds" behind its gate), off otherwise. The file
  writes the flag only when it differs from that default. So adding a lane out
  of a dead end turns the bonus off on its own, unless the author ticked it
  explicitly. `gauntlet.grants_life(g, sid)` is the one answer to "does this
  stage grant a life", and the board mirrors it in `gauntlet-graph.mjs`, which
  already works out goals for the ★. A shared fixture pins the two, as
  `gauntlet-layout.json` pins the layout.
- **On the board** (`gauntlet.html`): a **Lives** number input beside the
  name, and on each stage card a **Grants a bonus life** checkbox, ticked by
  default for dead ends. `checkBody` checks both ranges. The diagram and the
  game's list mark a bonus stage with ♥.
- **In the generator**, `lives` is an option on the generate form and
  `--lives` on `tools/gauntlet_gen.py`. It defaults to 1, is written into the
  file, and is left out of `gid`, because it doesn't change which stages are
  picked. None of the shapes has a dead end, so no generated stage grants a
  bonus.
- A dead end's bonus is only worth having if the dead end can be reached, so
  the move rules above (the frontier keeps a dead end until it's played, and a
  missed gate can be replayed) are what make it work.

### In the game

- **The list** marks every frontier stage "run", as it marks the ways on
  today. The run line under the stages gains the lives: "Run: 2 stages won · ♥♥
  left". Continue run plays the selected stage, naming it on the button.
- **The overlay after a loss** (lives left): "Run: lost · 2 lives left". It
  offers **Try again**, which starts a fresh game of the same stage as a new
  step, and **Choose a stage**, which goes to the list. The two are
  equivalent: the life is already spent when the loss is recorded, and Try
  again is only a shortcut for picking the same stage in the list. Going to
  the list to think it over costs nothing more. Free-play Retry is still
  absent from a run game: it would be a fresh game that the run doesn't record.
- **After the last life:** "Run over: no lives left · 3 stages won".
- **After a win:** as today. Next stage appears when the frontier holds exactly
  one stage, otherwise "choose the way on in the gauntlet list".
- `hud` has the words, and `app.run_ui` sets `Ui.run_lives` beside
  `run_at`/`run_total`. The page only draws them.

### Offered where?

`run.offered` stays as it is: a goal, plus a stage target or a fork. With lives,
a linear gauntlet with no targets could become a run ("finish within three
losses"), but that's a different challenge. It is left as a decision rather than
slipped in here.

### Tests (`tests/test_run.py`, `tests/test_app_run.py` or wherever `record_run_step` is tested)

- frontier: starts; ungated lanes from won stages; a gate met by the run's
  game but not by a best; a stage behind a won one leaves; a dead end stays;
- a loss costs a life and keeps the stage on the frontier; with `lives: 3`
  the third loss ends the run, and with the default of 1 the first does;
- a bonus stage's first MET win adds a life, and a MISSED win or a second win
  adds none; `grants_life` defaults on for a dead end and off for a goal or a
  stage with a lane out, and an explicit flag overrides either way;
- `lives` is clamped and written only when it isn't 1; `bonus_life` is written
  only when it differs from the default; the JS mirror agrees on the fixture;
- the fingerprint is unchanged for a gauntlet with no bonus stage;
- MISSED and VOID cost a life; GONE and ABANDONED end the run;
- a won dead end doesn't complete the run; a won goal does;
- a won stage with an unmet gate stays on the frontier, even when behind; a
  replay that meets the gate opens it; a replay that wins short of the gate
  costs a life;
- only the last life (or GONE/ABANDONED) ends the run; Try again and picking
  the same stage from the list make the same step;
- an old record with no `lives` reads as 1 and ends on its first loss, as it
  would have;
- Try again begins a new step on the same stage; a free-play Retry is still
  not the run.

## Part 2: generated gauntlets that branch

### The shapes

A run option beside `targets` and `unwon_last`, not a `dealer.Spec` field: the
spec says which setups may be dealt, and the shape says how they are joined.

| Shape | Columns (n = the spec's `stages`) | Starts | Lanes |
|---|---|---|---|
| `line` (today, the default) | 1, 1, 1 … | first | list order, `lanes` left out |
| `concertina` | 1, 2, 1, 2 … 1 (an even count ends 2, 1, 1) | the single first stage | each stage to every stage in the next column |
| `cross-stitch` | 2, 2 … 2, 1 | both of the first column | each stage to every stage in the next column |

- **`stages` keeps meaning the run's length**, the number of columns, which is
  what the form already asks for and what a run plays. The stage count follows
  from it and the form shows it beside the choice: concertina 7 → 10 stages,
  cross-stitch 6 → 11.
- **Both shapes end on one final stage.** This is the unwon-last map when
  `unwon_last` is set, so a run has one goal and the end reads as the end. A
  concertina with an even count ends on two single stages in a row (…2, 1, 1),
  so a run plays exactly the count asked for: 6 → 1, 2, 1, 2, 1, 1, which is 8
  stages. Two starts were the other option, but they would make the opening
  a cross-stitch.
- **No lane gates.** Each stage keeps its own stage target, as today. Gated
  cross-overs ("switching tracks costs a target") are a possible later option,
  but they would stack a second target on stages that already carry one.
- **`gid` hashes the shape only when it isn't `line`**, so every existing
  spec link keeps its gauntlet's id, and so its bests and runs.
- **`gauntlet.layout` places it as is.** Depth over ungated lanes is the
  column, and within a column the list order is the row. So the file lists
  stages column by column, the "a" stage of a pair before the "b" stage.

### Dealing: deal the pair to differ

At the moment a round deals one candidate per column depth (`candidates`). A
wide column now gets two per round, and **the second one is dealt to differ
from the first** before anything is probed. Probe games are what generation
spends its time on, and dealing costs next to nothing, so it is cheaper to
reject an alike deal here than to probe it and find out afterwards.

- **The axes.** Each is read once at deal time and stored on the candidate:
  - **size**: `Settings.system_count()`; differs when the larger is at least
    1.25× the smaller;
  - **lane length**: the median lane's travel time in turns (`lane_lengths` at
    the stage's ship speed through `lane_turns`, which the dealer already uses
    for a `lane_turns` rule); differs at 2 turns apart or more;
  - **layout**: `mode`, `random` against `symmetric`.
- **The second deal** draws up to `PAIR_TRIES` (20) times from the same rng
  until it differs from the first on at least one axis. If none does, it keeps
  the last draw. Every draw comes from the run's rng in order, so `candidates`
  stays a pure function of the spec, the seed and the shape.
- **A spec can rule some axes out.** If it fixes `mode`, pins `systems` and
  bounds `lane_turns` narrowly, only what is left can differ, and if nothing
  is left the pair will be alike. The form says so ahead of time:
  `dealer.free_axes(spec)` names the axes a spec leaves free, and the form
  shows "the two tracks may look alike: this spec fixes size, layout and lane
  length" when there are none.

### Picking: a pair at each wide column

`pick` now has a shaped sibling, `pick_shape(measured, widths, unwon_last)`:

- The usable candidates (some probe won) are ranked as `_spread` ranks them:
  by difficulty, then mean winning turns.
- Each column takes its point on the ranking as now, evenly spaced. A single
  column takes the candidate at its point.
- A wide column takes the candidate at its point, then **the nearest-ranked
  unused candidate that differs from it on an axis**. Nearest is measured by
  difficulty first, then winning turns, so the two are about equally hard.
  Failing that, it takes the nearest unused candidate at all, and the pair is
  marked `alike` in the tool's output and the run's ledger.
- The unwon last stage, as today, is the final single column.

`settled` stops a run at a round's end once every column is filled, every pair
differs (or the spec leaves no axis free), and the usable levels show as they
must today. Otherwise it stops when a round adds nothing.

### Writing the file (`build`)

- Stages are listed column by column; `start` and `lanes` are written out.
- **A pair's titles say how they differ**, because the graph shows titles and
  that's where the choice is made: "Stage 3: more systems" / "Stage 3: fewer
  systems", "longer lanes" / "shorter lanes", "symmetric" / "scattered". The
  first axis they differ on, in the order above, names the pair. An alike pair
  is "Stage 3a" / "Stage 3b". The briefing's first line stays
  `dealer.describe(cfg)`, which already gives the full setup.
- Single stages stay "Stage N", N being the column.

### Where it is asked for

- **The board's generate form** (`gauntlet-gen-page.mjs`, `gauntlet-spec.mjs`)
  adds **Shape**: "One path", "Concertina: splits and rejoins", "Cross-stitch:
  two tracks, switch at any stage". It is written into the link's data beside
  `targets` as `shape`, along with the stage count it makes.
- **`job_from_link`** reads `shape`. Anything unknown means `line`.
- **`tools/gauntlet_gen.py --shape concertina|cross-stitch`**, recorded in the
  run's ledger with the rest of its options, so a rerun picks again without
  replaying.

### Cost

A wide column deals and probes twice as many candidates per round. A concertina
of 7 columns costs about 1.4× a 7-stage line, and a cross-stitch of 6 about
1.8×. On desktop this is minutes. On a phone it is unmeasured, and so is the
line (`open.md`, gauntlet-gen.md *In the game*). **Time a cross-stitch link in
the web build on a phone before the form offers the shapes there.** If it's
too slow, a shaped link could default to `LINK_ROUNDS - 1`. The second deal's
retries cost no probe games.

### Tests (`tests/test_gauntletgen.py`, the board's JS suite)

- `widths` for each shape and count; the file's `start` and `lanes`; every
  stage reachable (`gauntlet.problems` empty);
- `layout` puts a pair in one column, "a" above "b";
- the second deal differs on an axis when the spec leaves one free;
  `candidates` is stable for spec, seed and shape;
- `pick_shape` prefers a differing pair over a nearer alike one, and marks an
  alike pair;
- `gid` of a `line` job is unchanged from today's (pin one);
- titles name the axis;
- the JS: the form writes `shape`; an unknown shape in a link reads as `line`.

## Boons

Parked, not decided. Targets and forks already give a run its choices, and
lives (part 1) give it the forgiveness boons would have bought, without
touching the log, the verifier or postability. Phase 4 of
`graphical-gauntlet-maker.md` stays as written if they're wanted later. If
lives prove enough, move boons to `decided-against.md`.

## Decisions (defaults taken; confirm before building)

- **Lives and bonus lives** (decided 2026-11-12): a `lives` field on the
  gauntlet, defaulting to 1, and a per-stage bonus life, on by default for
  dead ends ("The format: lives and bonus lives"). Still open:
  - meeting a gated lane doesn't earn a life; only a bonus stage's win does;
  - there is no cap on lives earned beyond the start;
  - the cap of 9 on `lives` is arbitrary.
- **Checkpoints: not built.** With lives and any-unlocked movement, a
  checkpoint (go back to the last waist after a loss) adds nothing a life
  doesn't already give. A concertina's waists would be the natural place if
  wanted.
- **A run on a linear gauntlet with no targets**: still not offered.
- **Similarity thresholds**: 1.25× systems, 2 turns of median lane, mode. The
  symmetric `layout` (hub and the rest) could be a fourth axis, but it is left
  out because it's subtler to see on the map.

## Docs, when each part lands

- Part 1: `gauntlet.md` *Runs* and *Forks* become "the run moves over
  everything unlocked; lives". Drop the lives from `graphical-gauntlet-maker.md`
  Phase 5. In `open.md`'s gauntlet line, lives come out. In `CLAUDE.md`'s
  Gauntlets paragraph, "one life" becomes "lives", and "a gated lane opens on
  the run's own score" stays. `gauntlet.md` *The file format* gains `lives` and
  `bonus_life`, and so does `rules.md` *Gauntlets*. The board's
  `leaderboard/README.md` gets the new fields if it lists the format.
- Part 2: in `gauntlet-gen.md`, add *Shapes*: the pair rule, the axes, and why
  the second deal is steered rather than filtered after probing.
- `sections.md` for the new sections. Delete this file once both parts have
  landed.
