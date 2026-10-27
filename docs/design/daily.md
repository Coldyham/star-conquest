# The daily challenge (proposed, not built)

Proposed on 2026-10-27. A fresh map every UTC day, the same for everyone,
dealt at random from one spec and kept only if a roster bot showed it can be
won. Browser games keep players with a daily (Wordle, the chess.com daily
puzzle, Polytopia, Slay the Spire's Daily Climb); the weekly campaign is a
week's contest, not a habit. Nothing here is built. Open decisions are at the
end and in [`open.md`](open.md).

## The idea in one paragraph

The day's challenge is **one ordinary setup**, dealt by `dealer.deal` from the
author's ranges with a seed derived from the date. Its board grows through the
week, Monday 7-12 systems up to Sunday 25-30. Its ship speed is chosen for the
map dealt, so the average lane takes 2 to 5 turns. A dealt map is kept only once
a probe bot has won it from the human's seat. The hourly worker makes it and
stores it in a `dailies` table. The game fetches it and opens it as a setup, as
a challenge link opens. Its seed is pinned, so scores post to the board as
themselves, crowns and verification apply unchanged, and the board's daily page
is a view over scores it already has.

## The spec

The author's ranges, from the Advanced and Combat settings of a link made on
the board's generate form (2026-10-27). They are kept in the repository
(proposed: `tools/daily_spec.json`) and read by `dealer.Spec.from_dict`. The
link's gauntlet options are not used: its two stages, its targets, the
`fastest`/`slack` pair and its seed. A daily is one map with no target.

```json
{"stages": 1, "roll": false,
 "rules": {"players": {"range": [2, 6]},
           "mode": {"in": ["random", "symmetric"]}, "fog": {"in": [true, false]},
           "node_jitter": {"range": [0.3, 1]}, "relax_min_sep_frac": {"range": [0.3, 1.2]},
           "lloyd_passes": {"range": [0, 2]}, "extra_edge_fraction": {"range": [0.4, 1]},
           "max_edge_length_frac": {"range": [0.5, 1]}, "ship_ly_per_turn": {"range": [6, 12]},
           "fog_sight": {"range": [1, 8]}, "fog_scout": {"range": [2, 8]},
           "home_start_ships": {"range": [10, 14]}, "home_production": {"range": [1, 4]},
           "garrison_base": {"range": [2, 4]}, "garrison_k": {"range": [9, 14]},
           "garrison_jitter": {"range": [2, 4]}, "combat_jitter": {"range": [0.04, 0.1]},
           "defender_advantage": {"range": [1, 1.1]}, "lane_turns": {"range": [2, 5]}},
 "opponents": {"in": ["rusherplus", "heuristic", "thinker", "evolved", "marshal", "actuary"]}}
```

`systems` is missing on purpose: the weekday sets it. `lane_turns` is new (see
"Ship speed follows the map").

### The board grows through the week

| Day | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Systems | 7-12 | 10-15 | 13-18 | 16-21 | 19-24 | 22-27 | 25-30 |

The rule is `{"range": [7 + 3w, 12 + 3w]}` for weekday `w` (Monday 0, by the
UTC date), added to the spec when that day is dealt. Neighbouring days overlap
by three systems, so the week rises without steps.

**Only the board size rises.** Whether the opponents should rise too is left
open (see the end). By default each day's opponents are drawn at a random
depth: `dealer.deal` places every seat near one depth along the allowed
bots, weakest first, and a lone stage would otherwise always deal at depth 0,
which is rusherplus and heuristic every day.

**Small boards with many players.** With 2-6 players on Monday's 7-12 systems,
the dealer gives 6 players on 9 systems (3 neutrals) about one deal in 75 (300
dealt). `setup_warnings` accepts these. Whether players should scale with
systems is open.

### Ship speed follows the map

The author's wish: the average lane should take 2 to 5 turns whatever the
board's size, though the shortest and longest lanes may fall outside that.
Bigger boards fit more systems in the same box, so their lanes are shorter
(measured below). A fixed speed range therefore gives slow small boards and
near-instant big ones.

**Tie the speed to the dealt map, not to the system count alone.** A lane's
length also depends on players, mode, layout and four map knobs. A speed table
keyed by system count would miss whenever those pull the other way. The map
is already decided when the speed is chosen (ship speed only divides lengths:
`settings.lane_survey_key` leaves it out for that reason). So the dealer can
measure the map it just dealt and choose a speed that fits:

- **A new dealer rule, `lane_turns`**, on the mean of every lane's turns at the
  dealt speed. That is `max(1, ceil(ly / speed))` per lane, the figure mapgen
  bakes into `Lane.travel_turns` and `settings.lane_turns` reports.
- **Dealing with it**: after the seed is rolled, `settings.lane_lengths`
  measures the map. The speed is then drawn from the `ship_ly_per_turn` rule's
  values (6-12 in steps of 0.5) whose mean lane turns land in the range. If
  none does, the attempt is redealt, like a setup that `setup_warnings`
  rejects. `ship_speed_growth_pct` stays 0, so the mean holds all game.
- **The same rule serves the gauntlet maker.** `lane_turns` is a dealer name like
  `systems`. The board's form can offer it later (a pair of pins on
  "Average lane, turns"), and `dealer.problems` should name a range that no
  allowed speed can reach.
- **The mean, not the median.** The author asked for the average. The menu's
  lane readout shows the median (`settings.lane_turns`). If the readout
  should match, use the median instead.

Measured on 2026-10-27, dealing 150 maps a day from the spec above with the
speed rule off:

| Day | Mean lane, ly (p10-p90) | Speeds that fit, median map | Untied 6-12 draw misses 2-5 |
| --- | --- | --- | --- |
| Mon 7-12 | 25.3 (17.2-32.9) | 6-17.5 | 6% |
| Tue 10-15 | 22.2 (17.1-26.6) | 5-15.75 | 2% |
| Wed 13-18 | 20.0 (16.4-23.8) | 4.5-14 | 3% |
| Thu 16-21 | 18.1 (15.1-20.9) | 4.5-12.5 | 7% |
| Fri 19-24 | 17.1 (14.6-19.6) | 4-12 | 9% |
| Sat 22-27 | 15.7 (13.3-18.0) | 3.5-11 | 20% |
| Sun 25-30 | 15.0 (12.6-16.9) | 3.5-10 | 26% |

- **The 6-12 range always leaves a speed that fits.** Every one of the 1,050
  maps had one, so the tie never rerolls a map on this spec. It only moves
  the speed within 6-12.
- **Without the tie, a quarter of weekend maps miss.** Big boards drawn fast
  have average lanes under 2 turns.
- **Reach on a fitted Monday map.** A fitting speed there can run up to 17.5,
  but the author's 6-12 stays the outer bound.

## Random, but not unplayable

A daily is played once by many people who did not choose it, so a map nobody
has shown winnable is the wrong bet. Each dealt candidate is played by the
generated-gauntlet probes (`gauntletgen.PROBES`, through `gauntletgen.play`)
from the human's seat:

- **The first candidate a probe wins is the day's map.** It is not the easiest
  of several, which is what `gauntletgen.pick` would choose with one stage.
  Taking the first win keeps the day random among winnable maps.
- **A candidate with no probe win**, or one where a probe game ran out of turns,
  is dropped. The next candidate is dealt from the same day's rng.
- **The cap is 8 candidates.** After that the day is left empty and the job
  fails loudly. On the first generated run each probe won 15-53% of
  candidates (`gauntlet.md`, "Dealing fewer candidates"), so most candidates
  should have at least one probe win and eight misses in a row should be rare. Measure it when built.

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
- **Cost.** A few candidates times five probes, some with evolved or actuary in
  them: seconds natively, untimed in WASM, and paid by every player every day
  if the game made it.

**`tools/daily.py`**, a new job on `.github/workflows/bot-replay.yml` beside
`campaign`, modelled on `tools/campaign.py`:

- Runs hourly. It makes **today's and tomorrow's** rows if missing and does
  nothing once both exist, so the day rolls over at 00:00 UTC with the row
  already there and a missed hour never skips a day.
- **The day's rng is derived from the date**: `random.Random(f"daily:{day}")`.
  It deals the candidates and draws the depth. Each map's game seed still
  comes from `settings.roll_seed` on that rng, as the dealer always rolls one,
  so no new stream reaches the engine. Record it beside the other derived
  streams in `rules.md` when built.
- **It records as it goes** (`CLAUDE.md`): each probe game is appended to
  `results/daily/<day>.jsonl` and a rerun resumes from it. That only helps a
  local run, since an Actions runner's `results/` dies with the job, but the
  job is short and a lost run is simply remade next hour.
- `--day YYYY-MM-DD` and `--dry-run`, as `campaign.py` takes `--week`.

**A week at once** is possible: on Monday, deal the week's seven maps in one run,
as the campaign makes its week. It buys nothing over a day at a time, though,
since the weekday ranges make the week rise without the gauntlet maker's
ascending pick. It would also hold six days of spoilers in the table.

## Storage

```sql
create table if not exists public.dailies (
  day           date primary key,
  settings      jsonb not null,   -- the setup, as dealer.stage_dict writes a stage
  spec          jsonb not null,   -- the spec it was dealt from, the day's systems and depth included
  probes        jsonb not null,   -- the probe results that cleared it
  rules_version integer not null,
  created_at    timestamptz not null default now()
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

- **`GET /api/daily[?day=YYYY-MM-DD]`** returns `{day, now, token}`, where
  `token` is the setup as a settings token. It is read-only and uses the
  publishable key with no secret, as `campaign.mjs` does. It is a function only
  because the game has no PostgREST client. A 404 means no daily that day,
  and the game shows nothing.
- **A Daily entry on the menu** (web, or wherever `paths.LEADERBOARD_ORIGIN` is
  set; blank disables it) and a **`#daily` launch link** (`#daily=<day>` for a
  past one). Either fetches the token and applies it to the menu as a setup,
  as a pasted challenge link does, labelled "Daily · Monday 27 Oct". A best on
  it is `webstore.best` on its key, derived as every best is.
- **A fetch is not a send.** The game sends only on *Post to leaderboard* or
  *Share replays*; the daily is a GET of public rows, as the campaign lookup is.
- **Ironman on the daily.** Proposed: a daily plays ironman whatever the local
  preference (`webstore.ironman`), so History stays hidden until the game is
  decided. It is a courtesy, not a check: like every local best, it is trusted.
- **Copy result** on the win overlay puts a spoiler-free line on the clipboard
  (`webstore.copy_link`'s path, never the address bar):

  ```
  Star Conquest daily · Mon 27 Oct · 9 systems
  Won in 14 turns, 6 ships lost
  https://star-conquest.netlify.app/game/#daily
  ```

  The line carries no setup and no moves. The link opens today's daily, not
  the poster's game.

## On the board

`daily.html` (joins `render_board.PAGES`), on `base.html` like every page:

- **Today**: the setup in a line (`dealer.describe`), a Play link and the
  standings from `counted_scores`, so verification, the 24-hour grace and the
  first-post-of-a-match rule apply unchanged.
- **First tries.** People will replay, and posting is opt-in, so the board
  cannot know a first attempt. Proposed: the game counts local attempts at the
  day's key and stamps the count on the score as per-score metadata
  (`Challenge`, with its column joining the grant if it gets one), and the
  board marks "first try" wins. It is trusted, as local bests are, and ranking
  stays by the best score, so cheating earns no more than it does on any map.
- **Streaks**: consecutive UTC days on which a name has a counted win on the
  day's map, posted within that day. Derived from `scores` and `dailies`, never
  stored, like crowns. Names are not authenticated, so a streak is as
  trustworthy as a crown.
- **The week** as one row, Monday to Sunday, with the system range of each day
  so far. **The archive**: past days, each linking `#daily=<day>`. A past day
  stays playable and scores normally; it only stops counting toward streaks.
- **The bot column comes free.** Once anyone posts on a day's map, the hourly
  `bot_replay` gives it a bot column, so "the best bot did 31 turns" shows
  there too.

## Interactions

- **No `Settings` field, no key moves.** A daily is an ordinary dealt setup
  (`dealer.py`'s first rule). `lane_turns` is a dealer rule, not a setting:
  what it decides is stored as an ordinary `ship_ly_per_turn`.
- **`RULES_VERSION`**: a bump between making a day and playing it leaves that
  day's map on old rules (its logs are declined as outdated, as any map's are).
  The worker stamps `rules_version`. Since it makes only a day ahead, at most
  two days are affected; see the open decision below.
- **The campaign**: none. The campaign picks seeds nobody has played, and a
  daily's seed simply becomes a played one.
- **Play-by-post**: none. A daily is single-player.

## Decisions left to the author

- **Should opponents rise through the week too?** That would set depth by
  weekday (Monday 0 up to Sunday 1) instead of a random depth. With boards
  also growing, Sunday would be both biggest and hardest.
- **Players on small boards**: keep 2-6 on every day, or scale the players range
  with the systems range.
- **Mean or median lane** for `lane_turns` (the menu's readout shows the median).
- **A cap on win length.** A probe-won map can still take 80+ turns, and a daily
  should fit a sitting. Proposed: also drop a candidate whose fastest probe win
  is over a cap, say 60 turns.
- **A `RULES_VERSION` bump**: remake an unplayed future day, or leave it.
