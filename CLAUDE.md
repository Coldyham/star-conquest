# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, run in the browser (Pyodide, behind an HTML page), `uv` for
dependency management.

This file states the rules in short and how the system fits together. The
*why* (reasons, measurements, history, rejected alternatives) lives in
`docs/design/`, indexed by [`docs/README.md`](docs/README.md), which also lists
the measurement tools. Each rule's full wording is in `docs/design/rules.md`
under the same heading. To find anything else, **grep the three index pages
rather than reading design files**; every line names its file and section:

- `docs/sections.md`: every design-file section, one line each.
- `docs/decided-against.md`: every idea built or proposed and then dropped.
- `docs/open.md`: work left open, and decisions left to the author.

For the words players see (system, setup, map, game, stage…) against the
code's names for them (`nodes`, `config_key`, `game_key`…), grep
`docs/glossary.md`; use the player's word in anything on screen.

**Grep `decided-against.md` and `open.md` before proposing a mechanism, a bot
tactic or a re-tune.** Then read only the section you need. Keep each design
file under ~1000 lines (split by topic; update `docs/README.md` and
`docs/sections.md`) and this file to rules, structure and pointers.
`docs/bot-api.md` and `docs/bot-brief.md` (checked by `tests/test_bot_brief.py`)
are published documents for outside developers, not working notes.

## Commands

```sh
uv run python main.py                          # serves the game at 127.0.0.1:8000/play/ and opens it
                                               # (flags pre-fill the menu: --players 4 --mode
                                               # symmetric --seed 42 --nodes 24; --no-open)
uv run python main.py --no-menu --autoplay     # AI plays every seat (demo)

uv run pytest -n auto                          # full suite in parallel, ~25 s (pytest-xdist)
uv run pytest tests/test_x.py                  # a file / file::test (serial is faster for a few)

uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --trials 200          # batch stats; add --film to check playback
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
                                                 # (parallel; recorded and resumed in results/ladder/)
uv run python -m tests.sim --swap --trials 50    # ...or as one free-for-all
uv run python -m tests.sim --ladder --ai knower thinker --aux knower=2   # set a bot's aux knob

uv run python tools/daily.py --dry-run          # deal today's and tomorrow's dailies (map, puzzle), store nothing
uv run python tools/try_daily.py sat            # play a fresh Saturday daily map (--puzzle, --probe, --seed, --week)
uv run python tools/puzzle_par.py --daily FILE  # a puzzle's par, stored nothing (with SUPABASE_*: today's and tomorrow's)
uv run python tools/check_bot.py NAME           # validate a models/ or bots/ bot
uv run python tools/bot_replay.py --dry-run     # leaderboard bot column (needs SUPABASE_*)
uv run python tools/verify_scores.py --dry-run  # check each posted score's log
uv run python tools/admin.py matches            # moderation (dry run until --yes)
uv run python tools/playstyle.py --baseline     # regenerate the Playstyle bots column
node --test leaderboard/tests/*.test.mjs        # the leaderboard's JS suite
node --test webapp/tests/*.test.mjs             # the page's JS suite
uv run python -m starconquest.serve             # the server main.py starts
                                                 # (?players=3&seed=7 pre-fills the menu, &go plays it;
                                                 # the spike is /play/spike.html)
```

Measurement, tuning and generator tools (benches, censuses, CMA-ES, par
search, gauntlet dealing) are listed in `docs/README.md`, "Tools", with the
design file each one reports into.

Match the surrounding style, and keep `uvx ruff check .` clean (rules in
`pyproject.toml`; don't `ruff format` a file you aren't otherwise changing).

Run the tests a change can reach, not the whole suite each time. A docs-only
change needs none unless a test reads the file (`tests/test_bot_brief.py` reads
`docs/bot-brief.md`, `tests/test_botio.py` reads `bots/README.md`). A change to
one bot or tool runs its own `tests/test_<name>.py` (knower's Council stop also
has `tests/test_council.py`). Wording, a string choice or an asset's text runs
the tests that read it (grep `tests/` for the file or string), not the suite.
Run the full suite after a change to `starconquest/` logic (rules, engine,
mapgen, replay, settings, AI, a press or a view), and once before opening a PR on
such a change, not again for later wording fixes on it.

**Every tool or script that runs for more than a minute records as it goes.**
Append each finished game or unit to a file under `results/` the moment it
finishes (one JSON line, flushed), print progress as it runs, and resume from
that file when rerun, never repeating finished work. Never hold results in
memory to write at the end, or print only a final summary: background runs get
killed and sessions end, and a run that dies must keep everything it finished.
Send a background run's output to a file under `results/`, not only the task's
scratch output.

## Architecture

Where things live: `starconquest/` the game; `webapp/` the page that runs it;
`models/` the shipped bots; `bots/` subprocess bots; `tests/` (with the
`tests.sim` harness); `tools/` offline jobs and benches; `leaderboard/` the
board site, its SQL (`schema.sql`), JS and Netlify functions
(`leaderboard/netlify/functions/`); `results/` run ledgers (gitignored).

The game is **pure Python with no display of its own**, so it is testable
headlessly. In the browser it runs in Pyodide in a Web Worker behind `session`
(JSON in, JSON out), and the page in `webapp/` draws what it answers
(`docs/design/html-frontend.md`). The boundaries are load-bearing:

- **No module imports pygame** (`test_no_module_imports_pygame`): the pygame
  shell was deleted at cutover. `fog` and `turnfilm` are presentation-only: the
  engine and AI never consult them.
- **Python does no I/O in the browser**: `hostio` queues effects for the page
  and takes its replies; `sc_auth` never reaches Python. `webstore` and `share`
  go through it there, and through a file or `urllib` in tools and tests.
  `share` sends **only** on *Post to leaderboard* or with *Share replays* ticked.
- **The page decides nothing.** What a press means lives in `play`, the play
  screen's words in `hud`, the setup menu's presses, words and view in
  `setupmenu`, the editor's in `maker`: change them there, never in the page's
  JS alone. `viewstate.Ui` holds all transient interaction state.
- **`serve`** is the local server `main.py` starts (`webapp/` at `/play/`, the
  game zip built on request), never shipped in the zip (`build_next.LOCAL_ONLY`).
- **The build** (`tools/build_web.sh`) stages the page, the pinned Pyodide and
  the game's Python as one hashed zip (`tools/build_next.py`) at `/play/`. The
  service worker (`tools/pwa/sw.js`) is cache-first only for what its name
  fixes (the zip, Pyodide's versioned files, the fonts) and network-first for
  the page and its scripts. `tools/parity_next.py` shoots the screens in
  `tests/fixtures/parity_screens.json`.

### Turn resolution (engine.py)

Turns resolve **simultaneously** against the same start-of-turn state. Phase
order is load-bearing: AI decisions → advance fleets → lane battles (opt-in) →
production (**before** combat) → arrivals+combat (per node, launch-order
independent) → win check → `turn += 1`. A bot sizing an attack off
`target.ships` can meet one more hull than it counted (`prod_progress`).

- `apply_order` deducts ships at launch. One owner's launches down one lane in a
  turn fly as **one fleet** (`end_turn`'s `launched`).
- **In-lane battles** (`config.IN_LANE_BATTLES`, off by default): pairwise in
  crossing order, winner only thinned, no `DEFENDER_ADVANTAGE` in open space.
- **A pile-up** pools per owner, folds attackers pairwise strongest-first, and
  the survivor faces the defender last (`on_step` exists only for the film).
- **The engine never imports the AI.** `decide` is injected into `end_turn`;
  `ai.decide` routes by `Player.ai_strategy` through `ai.STRATEGIES` (unknown →
  `"heuristic"`). User AIs: `ai.register(name, fn)`; `ai.load_models()` loads
  `models/*.py`. `models/` is committed (the web build ships it).
- **Non-Python bots are subprocesses** (`botio` wire format, `tests/botproc.py`,
  `bots/<name>.bot.json`), outside `models/` since WASM can't fork. Their
  randomness is derived (`botio.decide_seed`). A degraded seat (`degraded_runs`)
  must never be scored or posted. `docs/design/external-bots.md`, "External bots".

Detail: `docs/design/core.md`.

### Animated end of turn (turnfilm.py)

A **playback of a finished turn** onto a deep copy; `end_turn` still resolves
atomically and `RULES_VERSION` never moves for it. On by default as a local
preference (`webstore.animate_turns`), never a `Settings` field. Full rule list:
`docs/design/turnfilm.md`, "The rules in one place". The easiest to break:

- **Events carry results, not rules**: `turnfilm.Reel` never re-simulates or
  draws dice; beat order is always the engine's.
- **Only movement spends time**; `FILM_COMBAT_MS` **must stay 0**. Every reel
  goes through `app._primed`.
- **Marks outlive their film**; a jump (history, scrub, rewind) calls
  `clear_fading_marks`.
- **Do not widen `_lane_crossings`' sort tuple** (`(when, a, b)` decides who
  rolls first; widening moves stored replays silently). Sub-turn position is
  `Fleet.progress_at` only.
- **Fog is a union** (`Ui.sees`); `visible` is never overwritten. The page
  plays the film; the camera snap a deciding turn owes lands when it ends (the
  session's `snap`).
- **The correctness test is the history path**: turn *i*'s film on board *i-1*
  lands on board *i* (`tests/test_turnfilm.py`, `tests.sim --film`).

### Persistence, replay & history (replay.py)

A match is **never snapshotted**: `replay.GameLog` records `Settings`, the seed,
and per turn every seat's orders plus combat draws; `replay.reconstruct` feeds
them back via `end_turn(script=…)`, so **no seat decides again** — bot changes
can't move a stored game; add no per-model replay floor. **Bump
`engine.RULES_VERSION` by hand** (and `leaderboard/js/config.mjs`'s
`CURRENT_RULES_VERSION`; `test_leaderboard_sync`) with any change to phase
order, fight resolution or map generation from a seed. Outdated logs are
declined. `strategies` in the log is stamped back so a random seat keeps its
bot. **A live turn's rng is derived, never carried** (`replay.reseed`, in
`app.resolve_turn`, never inside `end_turn`); every bot seat decides from the
same start (`engine.decide_seat`). **Combat never draws from it**: each fight
rolls its own stream (`engine._Dice`). Rules in full: `docs/design/core.md`,
"Replay and history: the rules in one place".

### Play-by-post (pbp.py)

Per-seat URLs onto a shared match, played asynchronously. Reasons:
`docs/design/pbp.md`, under the same headings as the full rules.

- **Thin server; clients resolve.** `leaderboard/netlify/functions/pbp.mjs` and the `pbp_*`
  tables store orders and the log, never a board or engine. A seat token is
  scoped to one match. Play-by-post never reaches the leaderboard.
- **The stored log is the record; a turn is decided once** (first upload wins;
  `pbp.match_log`, `pbp.settled_turn`). The resolver's rng is derived
  (`pbp.reseed`); bots decide on a scratch copy. The uploaded log carries no
  forwarding rules (`pbp.shareable`).
- **Order sequence is the whole of determinism**: `engine._collect_orders` runs
  in ascending seat id, never submission order. `RULES_VERSION` must not move.
- **Fog is convenience, not secrecy**; the board digest is a drift tripwire.
- **Nothing resolves a play-by-post turn on a clock**; play and autoplay are
  unavailable. **A spectator holds no seat** (`#spectate=`, `pbp.SPECTATOR`):
  whole board, no writes, fog off only (`pbp.Match.watchable`). **The roster is the truth about who is a person**
  (`pbp.seat_people`), not `is_human`.
- A match's name is derived (`matchnames.phrase`, mirrored in
  `leaderboard/js/matchnames.mjs`) and never a key.

### Key conventions

**Determinism and identity**

- **All randomness flows through `state.rng`**; never the global `random` in
  core code. Unreproducible rolls use `settings.random_seed()`/`fresh_rng()`.
  Sanctioned derived streams: `botio.decide_seed`, the random-seat pick,
  `pbp.reseed`, `replay.reseed`, `engine._Dice`.
- **Everything is keyed by integer id.** Lanes use `model.lane_key`. Neutral is
  player `id == 0`. Star names are flavour, stamped **last**
  (`mapgen._name_systems`), never a key; `starnames.py` and `starlore.py` are
  generated (`tools/gen_starnames.py`) — regenerate, don't hand-edit.
- **Travel time is a query**: `state.travel_turns(a, b)`, never
  `Lane.travel_turns`.
- **A fleet's lane track is stored, never ranked** (`Fleet.lane_slot`).
- **A seat commands its own ships only** (`engine._own_orders`).

**Settings, links and keys** (rules in full: `docs/design/core.md`)

- **All balance/aesthetic constants live in `config.py`**, read live.
  `settings._apply_globals` (via `build_state`) is the single writer into
  `config`.
- **`settings.build_state(settings, seed)` is the one funnel to a `GameState`.**
  `Settings` readers are tolerant. Never prune a non-heuristic seat's
  `ai_params` from a token.
- **Adding a field to `Settings` moves `challenge_key()` for every shared map**:
  append a `settings._LEGACY_KEY_DROPS` entry (`test_challenge_key_is_stable`).
  Prefer local preferences or per-score metadata (`Challenge`) to new fields.
- **Never compare setups as text across writers** (Python `1.0` vs JS `1`):
  compare by value or after `Settings.from_dict`.
- **Challenge links** travel by clipboard only (`webstore.copy_link`), never
  the address bar or `localStorage`.

**The leaderboard** (reasons: `docs/design/leaderboard.md`; the site:
`leaderboard/README.md`)

- **The bot column is computed offline, never served** (`tools/bot_replay.py`).
  `bot_scores` has no public insert path. An embargoed map is not replayed
  until its embargo lifts.
- **Replays are uploaded; scores are checked against them**
  (`tools/verify_scores.py`). Only *Post to leaderboard* and *Share replays*
  send (and a generated gauntlet whose link ticked "Ask for it to be listed
  publicly"); an autoplay demo never does. `game_logs` is not publicly readable or
  writable. A new column the board posts to `scores` joins the column grant
  (`test_schema_grants`), never `submitted_at`. `Ui.can_post` gates sharing.
- **Playstyle readings are keyed by match, never by name**;
  `leaderboard/js/playstyle-baseline.mjs` is generated, never edited.
- **Crowns, campaign, embargoes and badges are derived, never stored**
  (reasons: `docs/design/campaign.md`; badges: `leaderboard/README.md`,
  "Badges").
  `campaign_games` matches by jsonb equality. `attemptStatus` is the one answer
  to "may I move here"; the game asks `/api/campaign` and never reimplements a
  rule.
- **The daily is made by the worker and stored, never made by the game**
  (`tools/daily.py` into `dailies`, a day ahead, readable once its day starts;
  the game asks `/api/daily`). A day has two kinds, the map (`daily.BOARD`) and the puzzle,
  each with its own streaks and badges. A day is never remade. A day's map is embargoed
  until the day ends (`game_embargoes`); its winner, standings and streaks
  (mended, and true) are derived. `docs/design/daily.md`.
- **A puzzle's par is computed offline and told only to a player who meets it**
  (`tools/puzzle_par.py` into `puzzle_pars`, no public read; `sc_puzzle_par`
  answers a claimed result only when it matches or beats the par; on the board,
  `sc_my_puzzle_par` answers a signed-in player whose own posted score does).
  A stored par is only ever improved (`puzzle_par.better`). `docs/design/daily.md`, "The
  puzzle's par".
- **The game and board are one site** (`/play/`, `/board/`, `/api/`).
  Endpoints are built at call time; `paths.LEADERBOARD_ORIGIN` blank disables
  every leaderboard feature. `tools/pwa/sw.js` never touches `/api/`. The
  sensitive-variable policy stays on "Require approval". Board pages are
  Jinja2 templates on `leaderboard/templates/base.html` (`tools/render_board.py`,
  whose `PAGES` is the servable allow-list); shared chrome goes in the base.
- **A claimed name is its owner's alone.** An account (a private login and
  password, held only as a hash, `leaderboard/js/auth.mjs`) is optional; only
  the owner posts, tags or names a pbp seat under it (`sc_may_use_user`,
  `pbp.mjs` `nameAllowed`). `claim_name` takes only
  an unused name; a used one goes through `admin.py assign-name`. `users.owner`
  is never public: `users` is granted by column.

**The AI** (rules in full: `docs/design/bots.md`, "Per-seat AI and pricing a
fight: the rules in full")

The roster, strongest first (`ai.LADDER_ORDER`; a family's variants are stops
of its `aux` knob, `docs/design/bot-families.md`; the measured ladder, and
when to re-run it, is `docs/design/ladder.md`):

- `knower` (Oracle: Off, Predict, Search, Council): an oracle (`IS_ORACLE`)
  that runs rival seats' `decide` to read their orders. Search plays out
  openings borrowed from other bots; Council adds a learned judge.
- `actuary` (Style: Greedy, Field, Learning, Hunt): prices every launch
  against a projected ledger of the board. Field pulls by an influence field,
  Learning remembers each rival's strike habits, Hunt adds a look-ahead.
- `marshal`: thinker's planner in fixed phases, made to commit; a fast,
  deterministic rule bot.
- `evolved` (Look-ahead: Off, On): blind; CMA-ES-trained weights over priced
  moves.
- `thinker` (Claudebot, Thinker): flows ships to the front.
- `heuristic` (in `ai.py`, the fallback) and `rusherplus` (attacks the weakest
  neighbour): the weak baselines.
- `chaotic` (unranked): a different non-oracle, memoryless bot each turn, the
  pick derived from seed, turn and seat (`docs/design/chaotic.md`).


- **AI is per-seat and pluggable** (`Player.ai_strategy`, `model.AiParams`).
- **A random seat is resolved in `build_state`** from the pool its seed's era
  names (`settings.pool_for`), never the loaded roster. **`RANDOM_POOLS` is
  append-only**: append a pool to change what a random seat deals; editing a
  shipped one re-deals its era (`test_a_shipped_pool_is_never_edited`). Roll
  seeds only through `settings.roll_seed`. An entry `name:stop` deals the stop
  too (`settings.resolve_profile`).
- **A variant of a bot is a stop of its knob, not a bot.** A folded name
  (`council`, `claudebot`) stays a legacy name (`ai.LEGACY`): never rewritten,
  never registered, read only through `ai.canonical`/`ai.family`/`ai.known`. A
  knob that grows a top stop declares `AUX_OVER` so stored values keep their
  stop. A fold plays exactly as what it replaced (`tools/fold_check.py`).
  `docs/design/bot-families.md`.
- **A bot that reads other seats** (`models/knower.py`) never calls
  `ai.load_models()`, reads `ai.STRATEGIES` lazily, and draws **nothing** from
  `state.rng`. Predicting bots advertise `IS_ORACLE`/`is_oracle_seat`/
  `stand_in_params`.
- **A bot that remembers between turns keys memory by the game's path**
  (`models/actuary.py`), never "the current game", and answers
  `is_oracle_seat`. `docs/design/learner.md`.
- **Price a fight with `combat.edge_attacking()`/`edge_defending()`, never a
  constant**, and floor the ask at `target.ships + 1`. Read
  `docs/design/marshal-pricing.md`, "Garrisons run away", before copying
  `marshal._enemy_margin`.
- **`AiParams.aux` is the one bot-defined knob** (`AUX_LABEL`, `AUX_RANGE`, …).
  Narrow by clamping on read, never rewriting. No new `AiParams` fields.
- **Setup warnings** go through `setup_warning(settings, seats)`.
- **An all-bot game has no human seat** until a turn is ended under manual
  control. `resolve_turn` passes `human_orders=None` for an unclaimed seat.
- **After changing `ai.py` or travel/combat balance, run a `tests.sim --trials`
  batch, sweeping node count and ship speed**, and watch the timeout rate.

**The page** (`webapp/`; rules and reasons: `docs/design/html-frontend.md`,
and `docs/design/shell.md` for the pygame shell's, whose behaviour it keeps)

- **Layout is CSS**: nothing that holds text gets a fixed pixel size. A touch
  screen drops keyboard-only strings; a phone (`compact`) gets its own layout,
  where the footer folds behind "more", never drops a button, and the popup
  docks. A pinch undoes its first finger's tap (`Ui.press_undo`).
- **Quitting asks first** (`hud.confirm_quit`), then asks the browser to close
  the window (`Session._leave`, the `close` effect).
- **`app.resolve_turn` expands rules into `Order`s.**
- **The send popup is the only ship-count editor.** Orders and rules are reached
  from the map. Forwarding-rule share arithmetic lives in `model` only
  (`Ui.forward_this_turn` is the one formula). Closing it lets its source go,
  left as the hover (`play.cancel`); while open, another neighbour is a second
  send from the source.
- **The Combat tab** uses `combat.preview_fight` (no `config`, no rng).
- **Route mode** is the one control that doesn't commit as you go.
- **Training help** (`docs/design/training.md`): the send popup's forecast reads only
  what the player can see (`combat.forecast_landing`). The Hint (K) runs marshal on a
  deep copy; a hinted game is never posted (`Ui.hinted`, `GameLog.hinted_from`).
  The coach (`coach.review`) reads only a decided game's history, never mid-game.

### Map generation (mapgen.py)

`random` and `symmetric` modes; both stay connected and planar-ish. **`hub`
must keep drawing exactly the board it always did** (`test_symmetric.py`);
nothing past the sectors draws from `state.rng`. Show
`Settings.system_count()`, never raw `nodes`. **Map generation never reads a
screen size** (`test_no_core_module_reads_a_screen_size`). The lane survey runs
in `setupmenu.survey`, never while a view is built. `docs/design/core.md`,
"Past a standard board the box grows".

### Hand-authored maps (custommap.py, maker.py)

Full list: `docs/design/hand-maps.md`, "The rules in one place". The easiest to
break: **`"custom"` never joins `settings.MODES`**; every stored value is
concrete; never widen `WORLD_SIZE`; `CustomMap.from_dict` never raises,
`generate_custom` asserts; `normalised()` stays idempotent and deletion goes
through `without_node`; the editor's model is the recipe, never a `GameState`;
`problems()` is the one validator; `tools/bot_replay._OUTCOME_MODULES` includes
`custommap`. **The editor's rules and words live in `maker`**, read by the
page: never in its JS alone.

### Gauntlets (gauntlet.py)

Pinned setups played in order, each with a briefing card and a side-panel hint;
the tutorial is one (`starconquest/assets/gauntlets/tutorial.json`). Full rules:
`docs/design/rules.md`, "Gauntlets". **A gauntlet is data, never `Settings`**: a
stage is its setup, by `challenge_key()`. **Progress is derived, never stored**
(`webstore.best` on the stage's keys). A stage is active only while the setup
matches it (`setupmenu.active_stage`). Built-in stages pin every field and a seed.
`from_dict` never raises. The play screen never sees a `Gauntlet` (`app.stage_ui`).
**A run (`run.py`) is the one stored progress**: from a start to a goal over
any stage it has opened (`run.frontier`; offered for a target or a fork,
`run.offered`), on the gauntlet's lives, a loss costing one; a gated lane opens
on the run's own score. Lives left are derived from the steps
(`run.lives_left`); a bonus life is `gauntlet.grants_life`, by default a dead
end's. It judges only its own game (`run.finish`), so a
rewind is free play; Continue resumes that game's log, never a fresh copy.
**A stage's place on the graph is computed (`gauntlet.layout`, mirrored by
`gauntlet-graph.mjs`; `gauntlet-layout.json`), never stored**; the board page
edits lanes by draft key, and a linear draft builds exactly its old body. The
game's list and the board draw it with one drawer, `webapp/js/stagegraph.mjs`
(the board imports it from `/play/`; it decides nothing): a tap selects
(`MenuState.stage_pick`), a second tap plays, and a hidden lane is drawn only
once met.
**A gauntlet made on the board (`gauntlet.html`) is stored, never its progress**:
`/api/gauntlet` is the only writer of the `gauntlets` table, both it and the
page check with `gauntlet-build.mjs`'s `checkBody`, and a row never changes but
for `listed` (admin only). Never store one in `campaigns`. **A generated
gauntlet is sent to the board only when its link asked to be listed**
(`wants_listing`, `gauntlet.ask_listing`): that ticked box is the consent. The game fetches
`#g=<id>` once and keeps the file (`gauntlet.remember`).
