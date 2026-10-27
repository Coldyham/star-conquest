# The daily challenge (proposed, not built)

Proposed on 2026-10-27. A fresh challenge every UTC day, the same for everyone,
dealt at random from one spec and kept only if a roster bot showed it can be
won. Browser games keep players with a daily (Wordle, the chess.com daily
puzzle, Polytopia, Slay the Spire's Daily Climb); the weekly campaign is a
week's contest, not a habit. Nothing here is built. Open decisions are at the
end and in [`open.md`](open.md).

## The idea in one paragraph

The day's challenge is a **generated gauntlet** ([`gauntlet.md`](gauntlet.md),
"Generated gauntlets"), dealt by the hourly worker from a fixed spec with a seed
derived from the date, probed by the same bots and stored once as a file in a
`dailies` table. The game fetches it and keeps it like a `#g=` gauntlet, so
progress is derived from local bests as for every gauntlet. Each stage is an
ordinary setup with a pinned seed, so scores post to the board as themselves,
crowns and verification apply unchanged, and the board's daily page is a view
over scores it already has.

## The spec

The author's spec, from the board's generate form (2026-10-27), with the run's
own options beside it as a `#gauntlet-spec=` link carries them. It is kept in the
repository (proposed: `tools/daily_spec.json`) and read by `dealer.Spec.from_dict`.
Its `seed` is dropped: each day brings its own.

```json
{"stages": 2, "roll": false,
 "rules": {"players": {"range": [2, 6]}, "systems": {"range": [7, 40]},
           "mode": {"in": ["random", "symmetric"]}, "fog": {"in": [true, false]},
           "node_jitter": {"range": [0.3, 1]}, "relax_min_sep_frac": {"range": [0.3, 1.2]},
           "lloyd_passes": {"range": [0, 2]}, "extra_edge_fraction": {"range": [0.4, 1]},
           "max_edge_length_frac": {"range": [0.5, 1]}, "ship_ly_per_turn": {"range": [6, 12]},
           "fog_sight": {"range": [1, 8]}, "fog_scout": {"range": [2, 8]},
           "home_start_ships": {"range": [10, 14]}, "home_production": {"range": [1, 4]},
           "garrison_base": {"range": [2, 4]}, "garrison_k": {"range": [9, 14]},
           "garrison_jitter": {"range": [2, 4]}, "combat_jitter": {"range": [0.04, 0.1]},
           "defender_advantage": {"range": [1, 1.1]}},
 "opponents": {"in": ["rusherplus", "heuristic", "thinker", "evolved", "marshal", "actuary"]},
 "candidates": 3, "targets": "fastest", "slack": 5, "unwon_last": false}
```

- **Random, but not unplayable.** The ranges keep the dealer away from the
  extremes that made the first generated run measure the knobs rather than the
  map (`gauntlet.md`, "The first run"). Every published stage was **won by a
  probe from the human's seat**: `unwon_last` is false (the form's default is
  true, which lets the last stage be one no probe won). A daily is played
  once by many people who did not choose it, so a map nobody can show winnable
  is the wrong bet. With two stages, stage 1 is the easier pick and stage 2 the
  harder, by the probes' share of wins and then their mean winning turns
  (`gauntletgen.pick`).
- **Opponents rise with depth** (`dealer.deal`): stage 1's seats lean toward
  rusherplus and heuristic, stage 2's toward marshal and actuary.
- **Targets: the fastest probe win plus 5 turns** (`gauntletgen.target`), shown
  on the briefing card and the top bar as on any dealt gauntlet.
- **A spec change applies from the next day not yet made.** Days already made
  are stored, so editing the spec never re-deals one. The row stores the spec it
  was dealt from, so the archive can say what changed.

## Where it is made: the worker, not the game

Generated gauntlets run in the game; the daily does not, for three reasons:

- **Everyone must play the same map.** A generated result depends on how the
  probes play, and bots are free to move (`gauntlet.md`, "Sharing the result").
  Two builds, say one behind a stale service worker, would generate two
  different dailies from the same date. Storing the file once makes it the
  record, as the campaign stores its week's graph.
- **actuary is an opponent in the spec, and it is time-budgeted.** In the game
  budgets are never lifted (`gauntlet.md`, "In the game"), so a probe game
  against actuary can come out differently on a phone and a desktop. The
  worker lifts them (`ai.set_budget_scale`), as `tools/gauntlet_gen.py` does.
- **Cost.** Up to 3 rounds × 2 stages × the probes is about 30 games, some
  with evolved and actuary in them: seconds natively, untimed in WASM, and paid
  by every player every day if the game made it.

**`tools/daily.py`**, a new job on `.github/workflows/bot-replay.yml` beside
`campaign`, modelled on `tools/campaign.py`:

- Runs hourly. It makes **today's and tomorrow's** rows if missing and does
  nothing once both exist, so the day rolls over at 00:00 UTC with the row
  already there and a missed hour never skips a day.
- **The run seed is derived from the date**: an integer from
  `blake2s(f"daily:{day}:{attempt}")` taken mod `SEED_MAX`, passed to
  `gauntletgen.Job` as a spec link's seed is. The dealer still rolls each
  stage's game seed through `settings.roll_seed` from the run's rng, so no new
  stream reaches the engine. Record it beside the other derived streams in
  `rules.md` when built.
- **No usable pick, deal again.** If the run picks fewer than `stages` won
  stages, `attempt` goes up by one and it deals again, up to a cap (proposed:
  5). A day is never published with a stage no probe won; after the cap the
  day is left empty and the job fails loudly.
- **It records as it goes** (`CLAUDE.md`): each probe game is appended to
  `results/daily/<day>.jsonl` and a rerun resumes from it. That only helps a
  local run, since an Actions runner's `results/` dies with the job, but the
  job is about a minute and a lost run is simply remade next hour.
- `--day YYYY-MM-DD` and `--dry-run`, as `campaign.py` takes `--week`.

## Storage

```sql
create table if not exists public.dailies (
  day           date primary key,
  gauntlet      jsonb not null,   -- the gauntlet file: id "daily-<day>", its stages
  spec          jsonb not null,   -- the spec it was dealt from, seed and attempt included
  rules_version integer not null,
  created_at    timestamptz not null default now()
);
```

- **Public read, no public write**, as `campaigns`: only the worker's secret
  key inserts. A row never changes.
- **Never in `gauntlets`.** `/api/gauntlet` is that table's only writer, and a
  daily is not a board-made gauntlet.
- **Its stages are matched to `games` by value**, seed and jsonb equality on
  each stage's `settings`, as `campaign_games` matches campaign nodes, never by
  `sc_config_key`. A view `daily_games (day, stage, game_key)` is the one join.

## In the game

- **`GET /api/daily[?day=YYYY-MM-DD]`** returns `{day, now, gauntlet}`:
  read-only, the publishable key, no secret, as `campaign.mjs` is. It is a
  function only because the game has no PostgREST client. A 404 means no daily
  that day, and the game shows nothing.
- **A Daily entry on the menu** (web, or wherever `paths.LEADERBOARD_ORIGIN` is
  set; blank disables it) and a **`#daily` launch link** (`#daily=<day>` for an
  archived one). The file is fetched once and kept with `gauntlet.remember`
  under its id, `daily-<day>`, so it opens like a `#g=` gauntlet, works offline
  once fetched, and its progress is `webstore.best` on its stages' keys. This
  is derived and never stored, as the rules require.
- **A fetch is not a send.** The game sends only on *Post to leaderboard* or
  *Share replays*; the daily is a GET of public rows, as the campaign lookup is.
- **Ironman on the daily.** Proposed: a daily stage plays ironman whatever the
  local preference (`webstore.ironman`), so History stays hidden until the game
  is decided. It is a courtesy, not a check: like every local best, it is
  trusted.
- **Copy result** on the win overlay of a daily stage puts a spoiler-free line
  on the clipboard (`webstore.copy_link`'s path, never the address bar):

  ```
  Star Conquest daily 2026-10-27
  Stage 1 ★ T14 · Stage 2 ★ T31 (target 33)
  https://star-conquest.netlify.app/game/#daily
  ```

  ★ marks a met target and ☆ a win short of it. The line carries no setup and
  no moves; the link opens today's daily, not the poster's game.

## On the board

`daily.html` (joins `render_board.PAGES`), on `base.html` like every page:

- **Today**: the two stages with their briefings, targets and a Play link, and
  standings per stage from `counted_scores`, so verification, the 24-hour grace
  and the first-post-of-a-match rule apply unchanged.
- **First tries.** People will replay, and posting is opt-in, so the board
  cannot know a first attempt. Proposed: the game counts local attempts at a
  stage's key and stamps the count on the score as per-score metadata
  (`Challenge`, with its column joining the grant if it gets one), and the
  board marks "first try" wins. It is trusted, as local bests are, and ranking
  stays by the best score, so cheating earns no more than it does on any map.
- **Streaks**: consecutive UTC days on which a name has a counted win on the
  day's last stage, posted within that day. Derived from `scores` and
  `dailies`, never stored, like crowns. Names are not authenticated, so a
  streak is as trustworthy as a crown.
- **The archive**: past days, each linking `#daily=<day>`. An archived day stays
  playable and scores normally. It only stops counting toward streaks.
- **The bot column comes free.** Once anyone posts on a daily stage, the hourly
  `bot_replay` gives it a bot column, so "Beat the best bot" reads there too.

## Interactions

- **No `Settings` field, no key moves.** A stage is an ordinary dealt setup
  (`dealer.py`'s first rule).
- **`RULES_VERSION`**: a bump between making a day and playing it leaves that
  day's stages on old rules (their logs are declined as outdated, as any map's
  are). The worker stamps `rules_version`. Since it makes only a day ahead, at
  most two days are affected; see the open decision below.
- **The campaign**: none. The campaign picks seeds nobody has played, and a
  daily stage's seed simply becomes a played one.
- **Play-by-post**: none. A daily is single-player.

## Decisions left to the author

- **Do stage targets gate?** In a dealt gauntlet a target gates the stages
  beyond it (`gauntlet.md`, "Stage targets"). With "fastest + 5", a casual
  player could be shut out of stage 2, the day's main map. Alternative: make
  both stages starts (the file's `start` list), so targets are only stars.
- **A cap on win length.** A probe-won map can still take 80+ turns; a daily
  should fit a sitting. Proposed: keep only candidates whose fastest probe win
  is within a cap (say 60 turns). This would be a daily option in `pick`'s
  filter, not a dealer rule.
- **One stage or two.** The spec says two; a single stage makes the result line
  and the streak simpler.
- **Difficulty by weekday** (an easy Monday rising to a hard Sunday, as the NYT
  crossword does): a second spec per weekday, or one spec whose opponent pool
  narrows by weekday. Not in the author's spec; offered, not assumed.
- **A `RULES_VERSION` bump**: remake an unplayed future day, or leave it.
