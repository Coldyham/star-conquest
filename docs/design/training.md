# Training help: the send forecast, the hint and the coach

Three aids for a player learning the game, built 2026-10-31 on a player's
request ("I'm not very good at this game and I'm finding it hard to get
better"). The forecast is arithmetic the player could already do on the
Combat page, so it is on everywhere. The hint is a bot's move, so taking one
takes the game off the leaderboard. The coach reads a decided game and
points at the turns worth a second look.

## The send forecast (`combat.forecast_landing`, `render._forecast_line`)

A row in the send popup's Send tab, under "send N · M home":

    9 vs 8: 91% · 10 sure        (amber: likely, not certain)
    12 vs 4: sure, ~11 left      (green: no roll loses it)
    lands in 3 turns · garrison unseen

- **What lands.** `Ui.landing_at`: every queued send to the target whose lane
  takes exactly as long as this one, plus our fleets already that far out. Ships
  landing on the same turn pool into one force (`combat.resolve_arrival`), so
  the forecast teaches converging on a turn.
- **What it meets.** `combat.garrison_after`: the garrison now plus every hull it
  builds on the way, the landing turn's included, because production runs before
  arrivals (`engine._production`). A neutral grows only under
  `config.NEUTRAL_PRODUCES`. Plus the holder's fleets already flying in that
  land on or before our turn: `resolve_arrival` pools an owner's arrivals with
  its garrison, and they are on the map (the forecast only reads a system in full
  view, where every fleet to it is drawn). A third seat's fleets flying in are
  listed in the breakdown, not counted: they fight whoever holds it when they
  land. Orders given this turn are not guessed: the forecast is what happens if
  nobody else acts.
- **The odds.** `combat.fight_odds` is the share of the jitter square the attacker
  wins, measured on a 40x40 grid of the same `_preview_roll` that the Combat page
  draws. It is cached per (ships, garrison, jitter, advantage), so a redraw costs
  nothing. `tests/test_hint.py` checks it against `resolve_fight`'s own dice.
  "Sure" is the worst corner capturing (`CombatPreview.worst`), and `sure_count`
  is the fewest ships that make it so. Percentages are clamped to 1-99 when not
  sure, so a roll that can still lose never reads "100%".
- **Fog.** A target out of full view (`Ui.sees`) shows "garrison unseen", the
  same rule as the popup title's "?sh". The forecast never reads what the map
  hides.
- **Reinforcing** one of our own systems is not a fight: the row shows what will
  be there.
- **The breakdown** (`render._draw_odds_card`, `Ui.odds_open`). The row is a
  button, marked with an i like the star info card's, wherever it reads the
  target. It opens a card over the board: your send, anything landing with it,
  what you land with; the garrison now, what it builds before you land, the
  holder's fleets landing by then, what it will hold; the verdict in words; who
  else is flying in; then how a fight is rolled, from the Combat tab, with this
  game's jitter and defender advantage ("6 holding it fight like 9"). Any press
  closes it, as the info card. A phone held sideways puts the dice in a second
  column. The row was asked for as "your send: a, currently at target: b +
  produced over the next n turns: c + enemy reinforcements already en route: d
  = a vs e"; counting d is what moved the forecast to read fleets in flight.
- The row takes the Send tab to the same height as the one-lane Forward tab
  (which has its hold row there), so flipping tabs does not move the panel.

## The hint (`main.press_hint`, `Ui.can_hint`, `GameLog.hinted_from`)

The footer's Hint button (K) shows what `main.HINT_STRATEGY` (marshal) would
send for the human seat this turn: dashed near-white arrows
(`config.COLOR_HINT`, no seat's hue), with the count 60% along the lane (a queued
order's label sits at 40%), and a caption at the top of the map. While it is up
the button splits into ✗ (K, hide) and ✓ (Shift+K, apply); glyphs only, so the
pair is no wider than the button, and the caption names the keys.

- **It shows only what our orders don't already do** (`Ui.hint_left`). Each
  lane's hinted ships less what our queued sends and live rules put down it
  this turn (`Ui.planned_sends`, the same `Ui.forward_orders` that end of turn
  issues), so a backline the rules already forward drops out. A lane we send
  some of shows the rest as "+N". The caption counts what is left and what is
  "already yours". It is recomputed every frame, so editing an order moves it.
  It does not flag our sends the advisor would not make.
- **Apply** (`Ui.apply_hint`) queues what is left as our own sends (merged into
  a queued send on the same lane), then hides the hint. A queued send comes out
  ahead of the source's rule, so topping up one lane can thin the rule's send
  down it; the apply repeats until the hint is met or the source has nothing
  free. Ships already promised elsewhere are never taken back: a send is cut
  to what is free. ✓ is drawn only while something is left.

- **It never touches the game.** The advisor runs on a deep copy with the seat
  re-dealt to marshal at default `AiParams`, through `engine.decide_seat`, so it
  draws nothing from the turn's rng and no bot memory reaches the live board.
  It decides from the start of the turn and ignores the player's queued orders,
  as every seat does.
- **It reads the whole board**, as every bot does (fog is presentation only).
  That is acceptable for training help, and is part of why a hinted game is not
  posted.
- **A hinted game is never posted** (`Ui.can_post` adds `not hinted`). The first
  press in a game not yet hinted opens a modal (`render.draw_confirm_hint`)
  saying what the hint shows and that the game can then not be posted (or
  clear its stage, in a gauntlet); Show takes it (`main.take_hint`). The ask
  first lived on the button itself ("Hint: no posting?", pressed twice),
  which said the cost but not what a hint is.
- **The log remembers.** `GameLog.hinted_from` is the board the first hint was
  taken on, written and saved immediately (not with the turn), so quitting
  before End Turn does not shed it. A resume reads it back into `Ui.hinted`. It
  is in the file only when set, so an unhinted log is byte-for-byte what it
  always was. `truncate(n)` drops it if the hint was on a later board; `fork(n)`
  keeps it if the hint was on board `n` or before. The verifier rejects a
  hinted score as a mismatch (`tools/verify_scores.py`). `RULES_VERSION` does
  not move: the field is not read to replay.
- **Where it is offered.** `Ui.can_hint`: our own live, undecided turn, not
  under autoplay, while reviewing history, in a watched replay, or in
  play-by-post (a bot's advice against friends is not training). A gauntlet
  stage or a daily may be hinted, and then is not cleared or posted, because
  `stage_cleared` goes through `can_post`.

## The post-game coach (`coach.review`, the Coach tab)

A pure core module that reads the history boards (`main.build_history`) and the
log's orders (`GameLog.orders_for`) once, when History opens on a **decided**
game (`Ui.history_reveal`), and never mid-game, where it would give away what
fog hid. Ironman is unaffected: History is closed until the game is decided
anyway. The win overlay's button reads "History & coach".

Its notes, each naming the board to jump to (the turn the decision was made
on) and the systems to ring there:

- **Gamble** (red): our ships landing together on a system they did not take,
  fewer than `combat.sure_count` of the garrison they could expect
  (`garrison_after` from the latest launch's board). Ships landing on the same
  board from any lane or turn are one group, as they fight as one.
- **Surprise** (amber): the same, at or above the sure count. The owner
  reinforced, or a rival landed too.
- **Emptied** (red): a system lost within `EMPTIED_WINDOW` turns of our sending at
  least `EMPTIED_SHARE` of its garrison out. The note names one launch, its
  ships against its own board's garrison (a sum over the window once read "27
  of 24"), and says what staying would have met.
- **Evacuated** (teal): the same loss, but everything sent in the window, kept
  home, would still have fallen on most rolls to the strongest rival force that
  landed. Pulling out was right, so it is noted as such, at no cost (the first
  dropped past the cap).
- **Idle** (amber): `IDLE_TURNS`+ turns of a system holding the sure count for a
  neutral next door and sending nothing, with no rival next door (holding a
  border is not idleness). One note per streak.
- **The turning point** (violet): from a loss, the first board of the last
  stretch a rival's income stayed above ours; from a win, the first board of the
  stretch ours stayed above every rival's. Income is `stats.chart`'s, so the note
  agrees with the Income tab.

At most 24 notes, the costliest kept (ships thrown away, ship-turns idle), and
the turning point always. They are shown in turn order under a tally ("12
captures · 3 failed attacks · 4 systems lost").

The Coach tab sits after the chart tabs in the graph panel, opens first when
it has a note besides the turning point (which every decided game has), and takes the panel's body in place of the plot and legend.
Click a note, or press Up/Down, to select it (`Ui.select_note`): History jumps
to its board and rings its systems (`render._draw_coach_marks`). The rows are a
window kept around the selected note, so a long list needs no scrollbar. Tab
cycles the charts and then Coach. A watched replay is coached for its human seat
too. A spectator holds no seat and gets no coach.

## Proposed, not built

Listed in `docs/open.md`:
- **Drill and per-bot tutorial stages.** Small gauntlet stages (data only): hold
  a border against rusherplus, time an arrival against production, converge two
  lanes on one turn, counter-strike an emptied system, a three-way pile-up. Also
  a stage each for rusherplus, evolved and knower.
- **Choosing the advisor.** `HINT_STRATEGY` is fixed at marshal. Actuary or
  knower would advise more strongly but run slower in the web build.
