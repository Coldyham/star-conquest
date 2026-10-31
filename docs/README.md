# Docs index

`CLAUDE.md` says *what* each rule is and how the system fits together. The
design notes in `design/` hold the *why*: the reasoning, the measurements, the
history and the alternatives that lost. This page says which file covers what.

## Finding something without reading a whole file

Three pages are for **grepping, never reading whole**. Every line in each names
its design file and section, so a grep hit is enough to know where to read:

| Page | Holds |
| --- | --- |
| [`sections.md`](sections.md) | Every design-file section, one line each: what it covers and its verdict |
| [`decided-against.md`](decided-against.md) | Every idea built or proposed and then dropped, with its outcome |
| [`open.md`](open.md) | Work measured or proposed but neither shipped nor rejected, and decisions left to the author |

1. **Before proposing a mechanism, a bot tactic or a re-tune**, grep
   `decided-against.md` and `open.md` for the constant, function or idea.
   Most obvious ideas have been built and measured, and many lost.
2. **To find an explanation**, grep `sections.md` (or `design/rules.md` for a
   rule's full wording), then find the heading's line in that file
   (`grep -n '^##.*Heading' docs/design/file.md`) and read from there.
3. **Before running a bot measurement**, read "Measuring a bot" in
   [`design/bots.md`](design/bots.md): the method mistakes earlier sweeps made.

Read a design file when you are changing that code, not as background, and
read the section you need rather than the whole file.

## Adding to them

Put a section in the file for its area, under a heading that matches the
`CLAUDE.md` bullet it explains. Record a negative result as carefully as a win:
what was built, the numbers, why it lost. Then add its line, starting with the
file name, to `sections.md`; to `decided-against.md` if it lost; to `open.md`
if it is left open (and delete that line when it closes). Keep index lines
short: the verdict, not the numbers. Point to another file's section by file
and heading (`marshal-pricing.md, "Garrisons run away"`), never "above" or
"below"; code comments do the same. Keep each file under ~1000 lines; when one
grows past that, split it by topic and update this page and `sections.md`.

## Files

| File | Covers |
| --- | --- |
| [`design/rules.md`](design/rules.md) | Every `CLAUDE.md` rule in full, same headings, with the functions and tests it names |
| [`design/core.md`](design/core.md) | Settings tokens, the random seat, challenge links and stable digests, ship-speed growth, in-lane battles, the replay log and dice streams, star names, symmetric layouts |
| [`design/shell.md`](design/shell.md) | Text sizing and touch, viewport margins, the send popup, forwarding rules, the Combat page, spectating, ironman, graphs, route mode |
| [`design/turnfilm.md`](design/turnfilm.md) | The animated end of turn |
| [`design/training.md`](design/training.md) | Training help: the send popup's odds forecast, the bot hint (K) that takes a game off the board, and the post-game coach |
| [`design/hand-maps.md`](design/hand-maps.md) | The hand-drawn map recipe and the map creator |
| [`design/gauntlet.md`](design/gauntlet.md) | Gauntlets and the tutorial: the format, board-made gauntlets, generated gauntlets, stage targets, the proposed run and carry-over |
| [`design/leaderboard.md`](design/leaderboard.md) | The offline bot column, checked scores, watching a replay, versioning, playstyle readings, one site with templated pages |
| [`design/campaign.md`](design/campaign.md) | The weekly campaign: crowns, embargoes, the grace period, campaign fleets (decided against) |
| [`design/daily.md`](design/daily.md) | The daily challenge: the spec, systems by weekday, ship speed fitted to the map, made by the worker, storage, in the game and on the board; the daily puzzle |
| [`design/other-games.md`](design/other-games.md) | Features from other games (proposed): layers on top and mechanics behind default-off settings |
| [`design/setup-families.md`](design/setup-families.md) | Grouping setups on the board: pace and the family tree |
| [`design/schema.md`](design/schema.md) | The board's database: trust model, each table and view, the grant trap, play-by-post tables |
| [`design/pbp.md`](design/pbp.md) | Play-by-post: the stored log as the record, deadlines, the lobby, spectators, polling cost |
| [`design/par.md`](design/par.md) | The par search (a local check) and the `verify_scores` gap it exposed |
| [`design/seat-play.md`](design/seat-play.md) | Games played by hand against marshal and actuary, and what they taught |
| [`design/bots.md`](design/bots.md) | The roster as a whole: measurement method, bot distance, proposed families, the parameter space, real-game positions, the bot column's replays, break-even margins, defender advantage |
| [`design/external-bots.md`](design/external-bots.md) | The in-app bot maker (not merged) and bots that aren't Python |
| [`design/bot-families.md`](design/bot-families.md) | One bot per family, variants as stops of its knob; folds, legacy names, `AUX_OVER` |
| [`design/ladder.md`](design/ladder.md) | **The current roster ladder**: how and when to re-run it, why `LADDER_ORDER` differs, earlier readings |
| [`design/win-turns.md`](design/win-turns.md) | Turns to win: how fast the top bots finish, and marshal's stalemates |
| [`design/knower.md`](design/knower.md) | knower: the oracle, the search, its horizon and cost, borrowed candidates |
| [`design/council.md`](design/council.md) | knower's Council stop: the learned judge and borrowed candidates playing their own lines |
| [`design/marshal.md`](design/marshal.md) | marshal: what it was built on, where it stands against knower, the 2026-09 sweep |
| [`design/marshal-pricing.md`](design/marshal-pricing.md) | marshal: what a strike or a defence is priced against |
| [`design/marshal-flow.md`](design/marshal-flow.md) | marshal: where the surplus goes; successor fixes; `FEED` |
| [`design/marshal-joint.md`](design/marshal-joint.md) | marshal: which constants move together; screening against the roster |
| [`design/marshal-cma.md`](design/marshal-cma.md) | marshal: twelve constants tuned by CMA-ES |
| [`design/actuary.md`](design/actuary.md) | actuary: the projected ledger, its cost, where it stands, the retired planned opening |
| [`design/field.md`](design/field.md) | actuary's Field stop: the influence field, backyard neutrals |
| [`design/learner.md`](design/learner.md) | learner: rival habits read off the board, memory keyed by the game's path |
| [`design/learning.md`](design/learning.md) | actuary's Learning stop: the learned strike curve in the risk term |
| [`design/hunter.md`](design/hunter.md) | actuary's Hunt stop: edge risk, the look-ahead, the field by lane length |
| [`design/convoy.md`](design/convoy.md) | convoy (built, not shipped; code at `7153065`) |
| [`design/evolved.md`](design/evolved.md) | evolved: CMA-ES weights over principled moves, its look-ahead |

**Not design notes.** [`bot-api.md`](bot-api.md) (the wire protocol) and
[`bot-brief.md`](bot-brief.md) (a brief a player pastes into an AI assistant)
are published documents for outside developers; `tests/test_bot_brief.py`
checks the brief against the code.

## Tools

Everyday commands are in `CLAUDE.md`. These measure, tune or generate; each
records under `results/`; the argparse ones take `--help`, and each module docstring says what it is for.

| Tool | Does | Read |
| --- | --- | --- |
| `position_suite.py` | Rank bots on real-game positions | bots.md |
| `bot_distance.py` | How differently bots play, by phase | bots.md |
| `config_census.py`, `setup_sweep.py` | Which setups people play; whether the ranking moves there | bots.md |
| `sweep.py` | Paired A/B of a bot's constants (`--arm NAME:K=V,...`, `--cell`) | bots.md, "Measuring a bot" |
| `fold_check.py` | A stop plays as the bot folded into it | bot-families.md |
| `gen_bot_stops.py` | Regenerate the board's `bot-stops.mjs` | bot-families.md |
| `win_turns.py` | Turns to win; `--label NEW --base OLD` pairs two trees | win-turns.md |
| `candidate_bench.py` | A bot as one more knower candidate, paired | knower.md, council.md |
| `pileup_bench.py`, `bait_bench.py` | knower's pile-up pricing; Search offered bait openings | knower.md |
| `mp_tactics.py`, `guard_census.py` | marshal's multiplayer tactics; is the guard a pool | marshal.md |
| `coupling_census.py` | Which marshal constants share decisions | marshal-joint.md |
| `const_train.py` (`uv run --with cma`) | Tune marshal's constants by CMA-ES | marshal-cma.md |
| `hunt_bench.py`, `hunt_predict.py`, `hunt_sweep.py`, `hunt_cost.py`, `hunt_exact.py`, `hunt_tune.py` | actuary at Hunt: paired bench, strike prediction, sweeps, CMA-ES tuning, decide cost, exact-orders check | hunter.md |
| `learner_check.py` (`--logs public`) | actuary's rival model vs the roster or posted human games | learner.md |
| `human_habits.py` | How people play vs roster self-play | learner.md |
| `evo_bench.py`, `evo_train.py` (`--with cma`), `evo_probe.py`, `evo_losses.py` | evolved's gate, training and diagnosis | evolved.md |
| `par_search.py` | Par search on a map (local only) | par.md |
| `seat_play.py` | Play a posted map's seat by hand | seat-play.md |
| `gauntlet_check.py`, `gauntlet_gen.py`, `gauntlet_pool_bench.py` | The tutorial rises in difficulty; deal, probe and pick a gauntlet; the probe pool | gauntlet.md |
| `playstyle.py --baseline`, `playstyle_worker.py` | Regenerate the Playstyle bots column; the readings worker | leaderboard.md |
| `render_board.py` | Render the board's Jinja2 pages | leaderboard.md |
| `slim_web.py` | Slim the web build's stage: strip comments, subset the fonts (`build_web.sh` runs it) | shell.md |
| `campaign.py` | Generate this week's campaign map into `public.campaigns` | campaign.md |
| `daily.py` | Deal and probe today's and tomorrow's daily challenge and puzzle into `public.dailies` | daily.md |
| `try_daily.py` | Deal a weekday's daily map or puzzle (or a real day's) and open it in the desktop game, to feel and tune the spec | daily.md |
| `check_pbp.py` | Play a whole play-by-post match against a real endpoint | pbp.md |
| `gen_starnames.py` | Regenerate `starnames.py`/`starlore.py` | core.md |
| `dump_challenge_fixtures.py` | Real challenge tokens as fixtures for the board's JS decoder | core.md |
