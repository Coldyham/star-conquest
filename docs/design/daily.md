# The daily challenge

Proposed on 2026-10-27, built on 2026-10-28. A fresh map every UTC day, the same for everyone,
dealt at random from one spec and kept only if a roster bot showed it can be
won. Browser games keep players with a daily (Wordle, the chess.com daily
puzzle, Polytopia, Slay the Spire's Daily Climb); the weekly campaign is a
week's contest, not a habit. Built: the dealer's `lane_turns` rule, the worker
(`tools/daily.py`, `tools/daily_spec.json`), the `dailies` table and
`daily_games` view, `/api/daily`, the game's side (`starconquest/daily.py`, the
menu's Daily menu, `#daily`) and `daily.html`. The daily puzzle beside it
was built on 2026-10-28 too (see "The daily puzzle"). First tries are not built
(see "On the board" and [`open.md`](open.md)).

## The idea in one paragraph

The day's challenge is **one ordinary setup**, dealt by `dealer.deal` from the
author's ranges with a seed derived from the date. Its board grows through the
week, Monday 11-14 systems and 3-4 players up to Sunday 23-26 systems and 5-6
players, never a duel. Its ship speed is chosen for the map dealt, so the median lane takes
2 to 5 turns. Opponent strength stays flat through the week. A dealt map is kept
only once a probe bot has won it from the human's seat within a turn cap that
grows with the board, 80 on Monday to 110 on Sunday. The hourly worker makes it
and stores it in a `dailies` table. The game fetches it and opens it as a setup, as
a challenge link opens. Its seed is pinned, so scores post to the board as
themselves, crowns and verification apply unchanged, and the board's daily page
is a view over scores it already has.

## The spec

The author's ranges, from the Advanced and Combat settings of a link made on
the board's generate form (2026-10-27). They are kept in
`tools/daily_spec.json` and read by `dealer.Spec.from_dict`, with
`ship_speed_growth_pct` fixed at 0 so the median lane holds all game. The
link's gauntlet options are not used: its two stages, its targets, the
`fastest`/`slack` pair and its seed. A daily is one map with no target.

```json
{"stages": 1, "roll": false,
 "rules": {"mode": {"in": ["random", "symmetric"]}, "fog": {"in": [true, false]},
           "node_jitter": {"range": [0.3, 1]}, "relax_min_sep_frac": {"range": [0.3, 1.2]},
           "lloyd_passes": {"range": [0, 2]}, "extra_edge_fraction": {"range": [0.4, 1]},
           "max_edge_length_frac": {"range": [0.5, 1]}, "ship_ly_per_turn": {"range": [6, 12]},
           "fog_sight": {"range": [1, 8]}, "fog_scout": {"range": [2, 8]},
           "home_start_ships": {"range": [10, 14]}, "home_production": {"range": [1, 4]},
           "garrison_base": {"range": [2, 4]}, "garrison_k": {"range": [9, 14]},
           "garrison_jitter": {"range": [2, 4]}, "combat_jitter": {"range": [0.04, 0.1]},
           "defender_advantage": {"range": [1, 1.1]}, "lane_turns": {"range": [2, 5]}},
 "opponents": {"in": ["rusherplus", "heuristic", "thinker:0", "thinker:1", "evolved:0", "evolved:1",
                    "marshal", "actuary:0", "actuary:2"]}}
```

`systems` and `players` are missing on purpose: the weekday sets them. `lane_turns` is new (see
"Ship speed follows the map").

### The board grows through the week

| Day | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Systems | 11-14 | 13-16 | 15-18 | 17-20 | 19-22 | 21-24 | 23-26 |
| Players | 3-4 | 3-4 | 3-5 | 3-5 | 3-6 | 4-6 | 5-6 |
| Systems per seat | 2.8-4.7 | 3.2-5.3 | 3.0-6.0 | 3.4-6.7 | 3.2-7.3 | 3.5-6.0 | 3.8-5.2 |

The systems rule is `{"range": [11 + 2w, 14 + 2w]}` for weekday `w` (Monday 0,
by the UTC date). The players rule is a table, not a formula: the fewest is 3
to Friday, then 4 and 5, and the most rises from 4 to 6 (the author,
2026-10-28). Both are added to the spec when that day is dealt. Neighbouring
days overlap by two systems, so the week rises without steps.

**Narrower, and never a duel** (the author, 2026-10-28). The first table ran
7-12 systems on Monday to 25-30 on Sunday, three more a day, with duels
allowed to Wednesday. Small maps and duels are the daily puzzle's now (see
"The daily puzzle"), so the board starts at 11 systems and 3
players, and each day's range is four systems wide, not six.

**Systems per seat** is the day's system count over its players: the fewest
systems over the most players, up to the most systems over the fewest, as
reached in 300 deals a day (2026-10-28). Over the week it runs from 2.8
(Monday, 11 systems for 4 players) to 7.3 (Friday, 22 systems for 3). The
middle of each day's spread is about 3.5 to 5. The first table's ran 2.3 to
9.0, the top of it a duel on 18 systems.

**Opponent strength stays flat** (the author, 2026-10-27): only the board
rises. **Each opponent's seat is drawn on its own**, evenly from every allowed
bot (`dealer.deal` with no depth; the author, 2026-10-28), so any mix can sit
on any day, rusherplus beside actuary included, and the odds are the same every
weekday. Every stop of a family is its own entry (the author, 2026-10-28), so
a day can deal thinker as Claudebot and evolved without its look-ahead.
actuary deals only Greedy and Learning: all four of its stops would put it on
four entries of eleven, about two seats in five, where two of nine is about
one in five.

**Why not one depth a day**, as a generated gauntlet deals a stage. That put
every seat within a step of one point along the pool, weakest first, with the
day's depth drawn from 0 to 1. A day's field then spanned at most three
neighbouring entries: rusherplus never met marshal or actuary, and a depth past
about 0.94 made every seat actuary. With Learning at the top of the pool, the
2026-11-01 board drew 0.94 and was left empty: 32 candidates, 160 probe games,
none won. Rows dealt that way carry a `depth` in their `spec`.

**Measured on 2026-10-28**, the week of 2026-10-26 dealt seat by seat
(`results/daily/spread-dryrun/`): every day was kept, after 1 to 7 candidates
(26 in all) and 1 to 8 seconds of probe games, 2026-11-01 on its fifth. The
probe check leans away from actuary without shutting it out: half the
candidates had an actuary seat, and 2 of those 13 were won against 5 of the 13
without, so 2 of the 7 days kept have one.

**Why players rise too.** The most rises so small boards stay roomy: with 2-6
players on the first table's Monday of 7-12 systems, about one deal in 75 was
6 players on 9 systems, 3 neutrals between them. The fewest rises because a
duel on a big board is a long opening grind of neutrals before the two seats
meet.

### Ship speed follows the map

The author's wish: the average lane should take 2 to 5 turns whatever the
board's size, though the shortest and longest lanes may fall outside that. The
average is the median, the figure the menu's lane readout shows.
Bigger boards fit more systems in the same box, so their lanes are shorter
(measured below). A fixed speed range therefore gives slow small boards and
near-instant big ones.

**Tie the speed to the dealt map, not to the system count alone.** A lane's
length also depends on players, mode, layout and four map knobs. A speed table
keyed by system count would miss whenever those pull the other way. The map
is already decided when the speed is chosen (ship speed only divides lengths:
`settings.lane_survey_key` leaves it out for that reason). So the dealer can
measure the map it just dealt and choose a speed that fits:

- **A new dealer rule, `lane_turns`**, on the median of every lane's turns at
  the dealt speed. A lane's turns are `max(1, ceil(ly / speed))`, the figure
  mapgen bakes into `Lane.travel_turns`, and the median is the middle value
  that `settings.lane_turns` already reports for the menu's readout.
- **Dealing with it**: after the seed is rolled, `settings.lane_lengths`
  measures the map. The speed is then drawn from the `ship_ly_per_turn` rule's
  values (6-12 in steps of 0.5) whose median lane turns land in the range. If
  none does, the attempt is redealt, like a setup that `setup_warnings`
  rejects. `ship_speed_growth_pct` stays 0, so the median holds all game.
- **The same rule serves the gauntlet maker.** `lane_turns` is a dealer name like
  `systems`, read from any spec. The board's form does not offer it yet (a pair
  of pins on "Average lane, turns" would). Whether a range can be reached
  depends on the map, so `dealer.problems` cannot name one that never is;
  `deal` raises, as for any spec that cannot deal
  (`test_a_lane_turns_no_speed_can_reach_never_deals`).
- **It is drawn after everything else**, from the same rng, so a spec without it
  deals exactly as before and one with it changes only the speed
  (`test_lane_turns_is_drawn_after_everything_else`).

Measured on 2026-10-27, dealing 150 maps a day from the spec above (with the
day's players) with the speed rule off:

| Day | Median lane, ly (p10-p90) | Speeds that fit, median map | Untied 6-12 draw misses 2-5 |
| --- | --- | --- | --- |
| Mon 7-12 | 26.8 (21.3-31.4) | 6-28 | 3% |
| Tue 10-15 | 22.2 (17.8-26.6) | 4.5-22 | 0% |
| Wed 13-18 | 19.7 (16.3-22.8) | 4.5-20 | 0% |
| Thu 16-21 | 17.6 (14.0-20.9) | 4-18 | 0% |
| Fri 19-24 | 16.9 (13.9-19.5) | 3.5-17 | 0% |
| Sat 22-27 | 15.5 (12.8-17.9) | 3.5-15.5 | 0% |
| Sun 25-30 | 14.9 (12.8-17.0) | 3.5-15 | 0% |

- **On the median, the author's 6-12 range nearly always fits by itself.** Every
  one of the 1,050 maps had a fitting speed in 6-12, and a plain draw missed on
  at most 3% (long Monday lanes drawn slow, short Sunday ones drawn fast).
- **The rule is a guarantee, not a large correction.** It costs one lane survey
  per deal, and it holds if the spec's ranges change later.
- **The mean was stricter.** Measured on the mean of lane turns, an untied draw
  missed on 20% of Saturday maps and 26% of Sunday ones (2-6 players every
  day), so the median was the easier average to hold.

## Random, but not unplayable

A daily is played once by many people who did not choose it, so a map nobody
has shown winnable is the wrong bet. Each dealt candidate is played by the
generated-gauntlet probes (`gauntletgen.PROBES`, through `gauntletgen.play`)
from the human's seat:

- **The first candidate a probe wins within the day's turn cap is the day's
  map.** The cap keeps a daily to a sitting (see "The turn cap"). It is not the
  easiest of several, which is what `gauntletgen.pick` would choose with one
  stage. Taking the first win keeps the day random among winnable maps.
- **Probes play weakest first and stop at the first win**, and each probe game
  stops at the cap (`gauntletgen.play_one`'s `max_turns`), since a win after it
  does not count. A candidate with no probe win inside the cap is dropped and
  the next is taken from the same day's rng.
- **At most 32 candidates a day** (`MAX_CANDIDATES`; raised from 24 by the
  author, 2026-10-28). After that the day is left empty and the job fails
  loudly. Sunday is the hardest day to fill: a quarter of its candidates
  survive (10 of 40, below), so it needs four on average, and 32 straight
  misses is about one Sunday in 10,000. Every other day is rarer still. At
  about 9 probe-seconds a Sunday candidate, the limit costs nothing until it is
  needed. A first dry run (2026-11-01, a Sunday) took five candidates.

### The turn cap

**The cap grows with the board** (the author, 2026-10-28): `80 + 5w` turns for
weekday `w`. It was measured on the first table's ranges (7-12 systems on
Monday to 25-30 on Sunday, duels to Wednesday), below; it has not been
re-measured on the narrower ranges.

| Day | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cap, turns | 80 | 85 | 90 | 95 | 100 | 105 | 110 |

The author first proposed 80 turns every day, guessing it would cover games up
to 30 systems at 6 ly/turn. Measured on 2026-10-27, it does not at the weekend.
Forty candidates were dealt per weekday under this file's rules, with speed
fitted, opponents at a random depth, and all five probes played on each with
budgets lifted. Monday to Wednesday is from the first run (1,400 games).
Thursday to Sunday was played again on 2026-10-28 with the day's players
(800 games), since more seats make slower games. Both caps are counted from
the same games:

| Day | Players | Won by a probe | Fastest probe win p50 / p90 / max | Kept under a flat 80 | Kept under the growing cap |
| --- | --- | --- | --- | --- | --- |
| Mon | 2-3 | 25 / 40 | 50 / 83 / 97 | 22 / 40 | 22 / 40 (80) |
| Tue | 2-4 | 32 / 40 | 59 / 136 / 217 | 23 / 40 | 23 / 40 (85) |
| Wed | 2-5 | 26 / 40 | 72 / 120 / 205 | 15 / 40 | 19 / 40 (90) |
| Thu | 3-6 | 24 / 40 | 98 / 161 / 182 | 8 / 40 | 12 / 40 (95) |
| Fri | 3-6 | 29 / 40 | 93 / 151 / 297 | 10 / 40 | 17 / 40 (100) |
| Sat | 4-6 | 19 / 40 | 94 / 149 / 166 | 6 / 40 | 11 / 40 (105) |
| Sun | 5-6 | 18 / 40 | 100 / 150 / 217 | 4 / 40 | 10 / 40 (110) |

- **More seats, fewer and slower wins.** With 2-6 players every day, Thursday
  to Sunday had kept 18-20 of 40 under the growing cap. Raising the fewest
  players halves that at the weekend: fewer candidates are won by any probe,
  and the fastest win's median is 93-100 turns against 75-95.
- **A flat 80 lost most slow weekend boards** (first run, 2-6 players). On
  Saturday and Sunday at 6-8 ly/turn the fastest probe win had a median of 100
  turns, and 15 of 19 were over 80. With the day's players, the growing cap
  keeps 10 of 20 at 6-8 ly/turn, 3 of 7 at 8.5-10 and 8 of 10 at 10.5-12.
- **Posted games agree.** On the board's posted maps of 19-24 systems (none of
  25-30 is posted yet), people's best win had a median of 77 turns, and 6 of 16
  were over 80; the two at 6 ly/turn took 93 and 121. On 13-18 systems the
  fastest win in the bot column was over 80 on 18 of 52 maps. People's best
  and the fastest bot win run close on the same sizes (medians 33 and 39 on
  7-12 systems, 77 and 78 on 19-24), so the probes are a fair stand-in for a
  good player's pace.
- **What the cap still does to the weekend.** It keeps the maps that play
  fastest, so Saturday and Sunday lean slightly toward faster ships (mean 8.6
  to 9.0 ly/turn among probe-won maps) and barely toward fewer players (5.1 to
  5.0), since the players range is narrow there. The median lane stays in
  2-5, since the speed is fitted first.

## Where it is made: the worker, not the game

- **Everyone must play the same map.** Which candidate survives depends on how
  the probes play, and bots are free to move (`gauntlet.md`, "Sharing the
  result"). Two builds, say one behind a stale service worker, would pick two
  different maps from the same date. Storing the setup once makes it the
  record, as the campaign stores its week's graph.
- **actuary is an allowed opponent, and it is time-budgeted.** The game never
  lifts budgets (`gauntlet.md`, "In the game"), so a probe game against
  actuary can come out differently on a phone and a desktop. The worker lifts
  them (`ai.set_budget_scale`), as `tools/gauntlet_gen.py` does.
- **Cost.** Up to 32 candidates times five probes, some with evolved or actuary in
  them: seconds natively, untimed in WASM, and paid by every player every day
  if the game made it.

**`tools/daily.py`**, a new job on `.github/workflows/bot-replay.yml` beside
`campaign`, modelled on `tools/campaign.py`:

- Runs hourly. It makes **today's and tomorrow's** rows if missing and does
  nothing once both exist, so the day rolls over at 00:00 UTC with the row
  already there and a missed hour never skips a day.
- **The day's rng is derived from the date**: `random.Random(f"daily:{day}")`.
  It deals the candidates, each opponent seat drawn on its own. Each map's game seed
  still comes from `settings.roll_seed` on that rng, as the dealer always rolls
  one, so no new stream reaches the engine (`rules.md`, "The leaderboard").
- **It records as it goes** (`CLAUDE.md`): each probe game is appended to
  `results/daily/<day>.jsonl` and a rerun resumes from it. That only helps a
  local run, since an Actions runner's `results/` dies with the job, but the
  job is short and a lost run is simply remade next hour.
- `--day YYYY-MM-DD` and `--dry-run`, as `campaign.py` takes `--week`. A dry
  run needs no credentials. It runs on the workflow's bare Python, as every
  worker does: the core and `models/` import nothing off PyPI.

**Trying a weekday at home**: `tools/try_daily.py sat` deals a map from
Saturday's spec as the worker would and opens it in the desktop game's menu
(`--no-menu` starts it), so the author can feel a weekday and tune it. It reads
`daily_spec.json` and `daily.py`'s `PLAYERS`, `systems` and `turn_cap` live, so
a change shows on the next run. Each run deals from a fresh trial stream,
`daily:try:<weekday>:<seed>`, never the day's own (`--seed` deals one again,
on any week); a plain deal skips the probes, so it can be a map the worker
would throw away, and `--probe` keeps dealing until a probe wins one inside
the cap, recording under `results/daily/try/`. `--day` probes and opens a
real day's daily, recording under the day's own file, which the worker then
reuses; `--week` prints one map per weekday and opens none. The setup reaches
the game as an empty game log, which `main.py --settings-from` already opens.

**A week at once** is possible: on Monday, deal the week's seven maps in one run,
as the campaign makes its week. It buys nothing over a day at a time, though,
since the weekday ranges make the week rise without the gauntlet maker's
ascending pick. It would also hold six days of spoilers in the table.

## Storage

```sql
create table if not exists public.dailies (
  day           date not null,
  kind          text not null default 'board',   -- or 'puzzle' (see "The daily puzzle")
  settings      jsonb not null,   -- the setup, as dealer.stage_dict writes a stage
  spec          jsonb not null,   -- the spec it was dealt from, the day's systems and players included
  probes        jsonb not null,   -- the probe results that cleared it
  rules_version integer not null,
  created_at    timestamptz not null default now(),
  constraint dailies_pkey primary key (day, kind)
);
```

- **No public write**, as `campaigns`: only the worker's secret key inserts.
  A row never changes.
- **Public read only up to today**: the row-level policy is
  `day <= (now() at time zone 'utc')::date`. Tomorrow's map exists an hour or
  more early but cannot be practised. `campaigns` has no such need, since a
  week is published as it starts.
- **Matched to `games` by value**, seed and jsonb equality on `settings`, as
  `campaign_games` matches campaign nodes, never by `sc_config_key`. A view
  `daily_games (day, game_key)` is the one join.

## In the game

- **`GET /api/daily[?day=YYYY-MM-DD]`** returns `{day, now, settings,
  rules_version}`; the game reads `settings` with `Settings.from_dict`, so no
  token is minted in JavaScript. It is read-only and uses the
  publishable key with no secret, as `campaign.mjs` does. It is a function only
  because the game has no PostgREST client. A 404 means no daily that day,
  and the game shows nothing.
- **A Daily entry on the menu** (web, or wherever `paths.LEADERBOARD_ORIGIN` is
  set; blank disables it) and a **`#daily` launch link** (`#daily=<day>` for a
  past one). Either fetches the day (`main.open_daily`, polled like a gauntlet
  fetch, `main.land_daily`) and applies it to the menu as a setup, as a pasted
  challenge link does, stashing it as a shared link's setup is. The status line
  reads "Daily · Wed 28 Oct: Random · 3 players · 15 systems · vs marshal". A
  best on it is `webstore.best` on its key, derived as every best is.
- **Known by its key** (`daily.remember`, `WEB_DAILIES_KEY`, a fortnight of
  keys): `main.new_ui` stamps `Ui.daily_day` on a game whose setup is a
  remembered daily.
- **A fetch is not a send.** The game sends only on *Post to leaderboard* or
  *Share replays*; the daily is a GET of public rows, as the campaign lookup is.
- **Ironman on the daily.** A daily plays ironman whatever the local
  preference (`webstore.ironman`), so History stays hidden until the game is
  decided. It is a courtesy, not a check: like every local best, it is trusted.
- **Copy daily result** takes *Challenge a friend*'s place on a daily's win
  overlay (same button, same C key), since everyone already has the map. It
  puts a spoiler-free line on the clipboard (`main.copy_daily_result`,
  `daily.result_line`; the console when the clipboard refuses), never the
  address bar:

  ```
  Star Conquest daily · Mon 27 Oct · 9 systems
  Won in 14 turns, 6 ships lost
  https://star-conquest.netlify.app/game/#daily
  ```

  The line carries no setup and no moves. The link opens today's daily, not
  the poster's game.

## On the board

`daily.html` (joins `render_board.PAGES`), on `base.html` like every page:

- **Today**: the setup in a line, the opponents, a Play link (`../game/#daily`)
  and a link to its map page once somebody has posted. While the day runs the
  map is embargoed (see "The day's winner"), so the page shows what game.html
  shows of an embargoed map: who is ahead, by how many turns, and how many
  scores are posted (`game_summary`), and it never fetches that day's turns or
  ships lost.
- **A past day**: every counted win (`js/daily.mjs`'s `standings`: each name's
  best with a turn played by hand), the best posted on the day marked *Best on
  the day*, and any posted after it marked *later*. Verification, the 24-hour
  grace and the first-post-of-a-match rule apply unchanged.
- **First tries (not built).** People will replay, and posting is opt-in, so
  the board cannot know a first attempt. Proposed: the game counts local attempts at the
  day's key and stamps the count on the score as per-score metadata
  (`Challenge`, with its column joining the grant if it gets one), and the
  board marks "first try" wins. It is trusted, as local bests are, and ranking
  stays by the best score, so cheating earns no more than it does on any map.
- **Streaks are mendable** (the author, 2026-10-28). A streak is consecutive
  UTC days on each of which a name has a counted win on the day's map, ending
  today or yesterday (a streak not yet extended today is still alive). A missed
  day counts once it is won later from the archive, so a streak can be mended.
  The **true streak** counts only days won on the day itself, while the map was
  new and embargoed; it is the figure's title, so hovering or tapping shows it
  (tip.mjs), and a mended streak reads "3 days (1 true)". Both are derived from
  `counted_scores` through `daily_games` over the last 60 days (`streaks`),
  never stored, like crowns. Names are not authenticated, so a streak is as
  trustworthy as a crown. A day mended after it falls out of the 60-day window
  no longer extends a streak.
- **The week** is a short note in the lede, that setups get bigger over the
  week, Monday's smallest and Sunday's biggest (the author, 2026-10-28: a
  full table of each day's range was more than a player needs). **The
  archive**: a day navigator (`daily.html?day=`, never a
  day not yet started), whose Play opens `#daily=<day>`. A past day stays
  playable and scores normally, and winning it mends a streak.
- **The bot column comes free.** Once anyone posts on a day's map, the hourly
  `bot_replay` gives it a bot column, so "the best bot did 31 turns" shows
  there too.

## The day's winner

**A day's map is embargoed until the day ends** (the author, 2026-10-28):
replays and every score but the leader stay hidden until the next midnight
UTC, as a live campaign node's are until its week ends (`campaign.md`,
"Crowns, the weekly campaign and embargoes").

- **Derived, never stored.** `game_embargoes` takes the end of a live day the
  map is the daily of (`daily_games`), beside the stored reveal and a live
  campaign week, so `public_replays`, `game_summary` and every page that
  honours an embargo honour this one with no code of its own. A day's games
  row is created by whoever posts first, after the day was made, so the
  embargo cannot be stamped on it.
- **It gives the day a winner for free.** Nobody can watch the leader's replay
  until the day is over, so the best score posted on the day was made blind,
  and it stays the day's result after the replays come out. The map page names
  it *Led at reveal* (`standings.embargoLiftedAt`'s `dailyDay`, read off
  `daily_games`), and daily.html marks it *Best on the day* (`bestOnTheDay`).
  Later scores still rank on the standings, marked *later*.
- **Tested against Postgres** (`tests/test_embargo_sql.py`, opt-in with
  `SC_TEST_PG`): today's daily is embargoed to midnight, yesterday's is not,
  and its replays are public again.


## Badges

Three badges on the player card (`leaderboard/README.md`, "Badges"; the
author, 2026-10-28), derived like the rest (`badges.mjs`'s `dailyRecord`):

- **Daily done**: a counted win on any day's daily, whenever posted.
- **Winner of the day**: the best score posted on a day (`bestOnTheDay`), once
  that day has ended. Later scores never take it.
- **Full week**: every day of one Monday-to-Sunday week won. A day won later
  from the archive counts, as it mends a streak.

A win autoplayed through (`hand` 0) counts for none, as on the standings. A
board whose schema predates `daily_games` shows the three as unknown.

## The daily puzzle

Proposed and built on 2026-10-28. A second daily beside the board: a small duel a day,
where the challenge is not winning but winning in the fewest turns. The two are
**independent**, not one two-stage gauntlet (the author, 2026-10-28). A gauntlet
would need one of them to count for the day, and gating the board behind a duel
holds back the player who only wants the board. Its gate would be trusted and
local anyway, since the day's setup is a public read. A warm-up order costs
nothing without one: a player can open either, in either order.

### What a puzzle is

- **A duel on 5-8 systems, random mode only, no fog.** Symmetric plays oddly
  at small system counts and leads to stalemates, and a puzzle is played on
  the whole board (the author, 2026-10-28).
- **Easy to win is fine.** The challenge is the most efficient win, so the
  ranking is turns and then ships lost, as on any map. The probe check stays
  only as a guard against a map nobody can finish.
- **One opponent per weekday**, the same every week (the author, 2026-10-28):

  | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
  | --- | --- | --- | --- | --- | --- | --- |
  | heuristic | heuristic+lean | rusherplus | thinker:0 (Claudebot) | thinker | marshal | actuary |

  heuristic+lean is heuristic with no reserve (`gauntletgen.TWEAKS`): a
  heuristic seat whose `reserve_fraction` and `reserve_floor` are 0, which a
  setup's per-seat `ai` already carries. The dealer's `opponents` cannot name
  a tweak, so the worker seats the day's opponent itself
  (`daily.PUZZLE_OPPONENTS`, through `gauntletgen.probe_profile`). actuary
  plays its default stop, Field (the author, 2026-10-28). The game and the
  page name the opponent with its stop ("Thinker · Claudebot") and a lean
  heuristic as "heuristic+lean".
- **Short lanes, few of them instant**: the aim is no lane longer than 4
  turns and at most two 1-turn lanes (the author, 2026-10-28). Short lanes are
  what make a small map a puzzle (`marshal-pricing.md`, "Arriving after the
  rival breaks the door"): a fleet is on the board for a turn before it lands.
  It is an aim, not a rule: a ship speed of 15-20 ly/turn, drawn like any
  range, nearly always meets it (below), so the dealer needs nothing new and
  the board's `lane_turns` rule is left out.

### A speed range, not lane rules

The author, 2026-10-28: pick a speed range that generally gives those lanes
rather than make them hard rules. Measured the same day on 600 random-mode
duels of 5-8 systems from the board's spec, fog off, each map's lanes timed at
every speed of a range in steps of 0.5:

| Speeds | Meets both | A lane over 4 turns | Three or more 1-turn lanes | Median lane 2-3 |
| --- | --- | --- | --- | --- |
| 10-20 | 78% | 22% | 0.2% | 79% |
| 12-20 | 88% | 12% | 0.3% | 90% |
| 14-20 | 95% | 4.3% | 0.4% | 96% |
| **15-20** | **97.5%** | **2.0%** | **0.4%** | **98%** |
| 16-20 | 98.6% | 0.8% | 0.5% | 99% |
| 15-24 | 96% | 1.2% | 3.3% | 99% |

- **Below 15 the long lanes run over**: a duel's longest lane is about twice
  its shortest (median 2.0, p10-p90 1.5-3.0), so a slow ship stretches the
  longest past 4 turns. **Above 20 the short lanes go instant.**
- **15-20 is the pick**: one map in forty misses, nearly always by a single
  5-turn lane, and it keeps a wider spread of speeds than 16-20 for about one
  map in a hundred. It is well above the board's 6-12, as short lanes need.

### The probe check and its cap

A puzzle is kept, like a board, once a probe wins it within a turn cap, here
**60 turns every day** (`PUZZLE_TURN_CAP`). The cap is only a guard against a
map nobody can finish; a probe game past it counts as no win, so the next
probe, then the next candidate, is tried. Measured on 2026-10-28, four weeks of
puzzle days with the cap lifted to 200 (`results/daily/puzzle/`), probes
weakest first as the worker plays them:

- **Monday to Friday, every candidate was won by some probe.** Saturday
  (marshal) had 4 of 6 won and Sunday (actuary) 4 of 8, so the weekend deals
  a few more candidates, well inside 32.
- **The first probe to win took 14-172 turns, median 42.5**: 22 of 28 inside
  60, 24 inside 80. Probes are not efficient players, so a long probe game
  says little about a person's best; a person's best on the day is the
  puzzle's result.
- **Cheap**: each puzzle day took under a second of probe games.

### Kept apart from the board

- **Storage**: `dailies` has `kind` (`board` or `puzzle`, `dailies_kind_shape`),
  primary key `(day, kind)`, and `daily_games` carries it. Rows from before
  the puzzle are `board`. The schema's migration drops and re-adds the key, so
  paste `schema.sql` before deploying the worker or the site.
- **The worker**: `tools/daily.py` makes both kinds each hour, from their own
  spec files (`tools/puzzle_spec.json`), the puzzle from its own stream
  (`daily:puzzle:<day>`, a trial's `daily:puzzle:try:<weekday>:<trial>`) and
  recording under `results/daily/puzzle/`. `--kind` makes one kind only. A
  puzzle's row's `spec` names its `opponent`.
- **The game**: `/api/daily?kind=puzzle` (no `kind` is the board, so an older
  game still gets its board), a Daily puzzle item under the menu's Daily button, and
  `#puzzle` (`#puzzle=<day>`). `daily.remember` keeps each key's day and kind
  (a day alone, from before the puzzle, is a board), a fortnight of both.
  Ironman and *Copy daily result* as for the board, with the line naming the
  puzzle and its opponent:

  ```
  Star Conquest daily puzzle · Wed 28 Oct · 6 systems vs Marshal
  Won in 11 turns, 3 ships lost
  https://star-conquest.netlify.app/game/#puzzle
  ```

- **The board**: `puzzle.html`, a tab beside `daily.html` (the two share one
  menu entry, as the gauntlet pages do), run by the same `daily-page.mjs` with
  `#today`'s `data-kind`.
- **`tools/try_daily.py --puzzle`** deals and opens a weekday's puzzle.
- **The embargo matters more here.** A small map's best replay is its
  solution, so it stays hidden until the day ends, as the board's does.
- **Streaks and badges are per kind** (the author, 2026-10-28): a puzzle
  streak and true streak of its own, and Puzzle done, Puzzle winner of the day
  and Full puzzle week beside the board's three (`badges.mjs`, group "Daily
  puzzle"; `dailyRecord` is run once per kind).
- **The board starts bigger** (the author, 2026-10-28): 11 systems and 3
  players on Monday, never a duel, so the two never overlap (see "The board
  grows through the week").

## Interactions

- **No `Settings` field, no key moves.** A daily is an ordinary dealt setup
  (`dealer.py`'s first rule). `lane_turns` is a dealer rule, not a setting:
  what it decides is stored as an ordinary `ship_ly_per_turn`.
- **`RULES_VERSION`**: a bump needs nothing from the daily. A row stores only a
  setup and a seed, so a game played after the bump runs under the new rules,
  is stamped with them, and verifies as any game does. A game played before it
  is outdated, as on any map. Only the probe check is stale: it was played under
  the old rules. A combat or phase-order change leaves the map alone and moves
  its pace a little. A change to map generation from a seed would deal a
  different board from the same seed, which is still a legal setup but one no
  probe has played. The worker stamps `rules_version` so the archive can say
  which rules a day was checked under. A day is not remade: at most today's and
  tomorrow's rows were checked under the old rules.
- **The campaign**: none. The campaign picks seeds nobody has played, and a
  daily's seed simply becomes a played one.
- **Play-by-post**: none. A daily is single-player.

