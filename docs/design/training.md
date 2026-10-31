# Training help: the send forecast and the hint

Two aids for a player learning the game, built 2026-10-31 on a player's
request ("I'm not very good at this game and I'm finding it hard to get
better"). The forecast is arithmetic the player could already do on the
Combat page, so it is on everywhere. The hint is a bot's move, so taking one
takes the game off the leaderboard.

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
  `config.NEUTRAL_PRODUCES`. Rival moves are not guessed: the forecast is what
  happens if nobody else acts.
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
- The row takes the Send tab to the same height as the one-lane Forward tab
  (which has its hold row there), so flipping tabs does not move the panel.

## The hint (`main.take_hint`, `Ui.can_hint`, `GameLog.hinted_from`)

The footer's Hint button (K) shows what `main.HINT_STRATEGY` (marshal) would
send for the human seat this turn: dashed near-white arrows
(`config.COLOR_HINT`, no seat's hue), with the count 60% along the lane (a queued
order's label sits at 40%), and a caption at the top of the map. Press again to
hide.

- **It never touches the game.** The advisor runs on a deep copy with the seat
  re-dealt to marshal at default `AiParams`, through `engine.decide_seat`, so it
  draws nothing from the turn's rng and no bot memory reaches the live board.
  It decides from the start of the turn and ignores the player's queued orders,
  as every seat does.
- **It reads the whole board**, as every bot does (fog is presentation only).
  That is acceptable for training help, and is part of why a hinted game is not
  posted.
- **A hinted game is never posted** (`Ui.can_post` adds `not hinted`). The first
  press in a game not yet hinted only asks ("Hint: no posting?", amber); any
  other key or click disarms it (`main`'s event loop). The second press takes
  the hint.
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

## Proposed, not built

Listed in `docs/open.md`:

- **A post-game coach.** After the game is decided (so Ironman is fine), replay
  the log and flag turns by rule: a system emptied next to a rival and lost; a
  launch under the sure count that lost or annihilated; ships idle for more than
  5 turns with neutrals in reach; income falling behind. Each flag jumps into
  History.
- **Drill and per-bot tutorial stages.** Small gauntlet stages (data only): hold
  a border against rusherplus, time an arrival against production, converge two
  lanes on one turn, counter-strike an emptied system, a three-way pile-up. Also
  a stage each for rusherplus, evolved and knower.
- **Choosing the advisor.** `HINT_STRATEGY` is fixed at marshal. Actuary or
  knower would advise more strongly but run slower in the web build.
