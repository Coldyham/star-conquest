# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, pygame for presentation, `uv` for dependency management.

This file states the rules in short. Each rule's full wording, with every
function and test it names, is in `docs/design/rules.md` under the same heading;
grep it for the section you are touching. The reasons, measurements and rejected
alternatives live in the other `docs/design/` files, one per area. [`docs/README.md`](docs/README.md) is the short index of
those files. Two companion pages are for **grepping, not reading whole**:
`docs/sections.md` (every design file's sections, one line each) and
`docs/decided-against.md` (every idea built or proposed and then dropped).
**Grep `docs/decided-against.md` before proposing a mechanism, a bot tactic or a
re-tune.** Read a design file only when touching that code, and prefer the
section you need to the whole file. Design files: `core`, `shell`, `turnfilm`,
`hand-maps`, `leaderboard`, `setup-families`, `schema`, `rules`; `bots`, `knower`,
`council`, `marshal`, `marshal-pricing`, `marshal-flow`, `marshal-joint`,
`actuary`, `convoy`, `learner`, `learning`, `fulcrum`; `pbp`; `par`; `seat-play`. Keep
each under ~1000 lines (split by topic and update the index and
`docs/sections.md` when one grows past that). Keep this file to rules and
pointers. `docs/bot-api.md` (wire protocol for non-Python bots) and
`docs/bot-brief.md` (a brief players paste into an AI assistant; checked by
`tests/test_bot_brief.py`) are published documents, not working notes.

## Commands

```sh
uv run python main.py                          # setup menu (CLI args pre-fill it:
                                               # --players 4 --mode symmetric --seed 42 --nodes 24)
uv run python main.py --no-menu --autoplay     # AI plays every seat (demo)

uv run pytest                                  # full suite (or a file / file::test)

uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --trials 200          # batch stats; add --film to check playback
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
uv run python -m tests.sim --swap --trials 50    # ...or as one free-for-all
uv run python -m tests.sim --ladder --ai knower thinker --aux knower=2   # set a bot's aux knob

uv run python tools/check_bot.py NAME           # validate a models/ or bots/ bot
uv run python tools/bot_replay.py --dry-run     # leaderboard bot column (needs SUPABASE_*)
uv run python tools/verify_scores.py --dry-run  # check each posted score's log
uv run python tools/admin.py matches            # moderation (dry run until --yes)
uv run python tools/position_suite.py           # rank bots on real-game positions
uv run python tools/bot_distance.py             # how differently bots play, by phase
uv run python tools/learner_check.py            # actuary's rival model vs the roster
                                                # (--logs public: posted human games)
uv run python tools/human_habits.py             # how people play vs roster self-play
uv run python tools/playstyle.py --baseline     # regenerate the Playstyle bots column
uv run python tools/config_census.py            # which setups people play
uv run python tools/setup_sweep.py              # ...and whether the ranking moves there
uv run python tools/seat_play.py new --key KEY  # play a posted map's seat by hand
uv run python tools/par_search.py --nodes 13 --ai marshal actuary   # par search (local)
node --test leaderboard/tests/*.test.mjs        # the leaderboard's JS suite
```

No lint step; match the surrounding style. Pylance basic type-checking
(`.vscode/settings.json`) warnings are real signal.

## Architecture

A hard split between a **pure simulation core** and a **thin pygame shell**, so
the game is testable headlessly. The boundaries are load-bearing:

- **Core — imports no pygame:** `model`, `geometry`, `mapgen`, `combat`,
  `engine`, `ai`, `botio`, `settings`, `fog`, `replay`, `turnfilm`, `custommap`,
  `pbp`, `matchnames`, `campaign` (`test_no_core_module_imports_pygame`). `fog`
  and `turnfilm` are presentation-only: the engine and AI never consult them.
- **Shell — the only pygame modules:** `render`, `input`, `menu`, `widgets`,
  `mapmaker`, `main` (`starconquest/main.py`; the root `main.py` is a launcher
  shim for pygbag).
  - `render` reads `GameState` + `Ui`, **never mutates them, never imports
    `engine` or `ai`**, and stores nothing time-varying: a frame is a pure
    function of state + `Ui` + time.
  - `input` mutates **only** `Ui` (and queues human `Order`s) and returns an
    action string or `None` for `main` to act on.
  - `menu`: `draw` only reads `Settings`; `handle_event` mutates
    `MenuState`/`Settings` and returns `"start"`/`"quit"`/`None`. `main` runs a
    `"menu"` ⇄ `"game"` state machine.
  - **Browser bridges** (`softkeyboard`, `webstore`, `share`) are pure of pygame,
    guarded, silent on failure, never block a frame. `share` sends **only** on
    *Post to leaderboard* or with *Share replays* ticked. `docs/design/shell.md`.

### Turn resolution (engine.py)

Turns resolve **simultaneously** against the same start-of-turn state. Phase
order is load-bearing: AI decisions → advance fleets → lane battles (opt-in) →
production (**before** combat) → arrivals+combat (per node, launch-order
independent) → win check → `turn += 1`. A bot sizing an attack off
`target.ships` can meet one more hull than it counted (`prod_progress`).

- `apply_order` deducts ships at launch.
- **In-lane battles** (`config.IN_LANE_BATTLES`, off by default): pairwise in
  crossing order, winner only thinned, no `DEFENDER_ADVANTAGE` in open space.
- **A pile-up** pools per owner, folds attackers pairwise strongest-first, and
  the survivor faces the defender last (`on_step` exists only for the film).
- **The engine never imports the AI.** `decide` is injected into `end_turn`;
  `ai.decide` routes by `Player.ai_strategy` through `ai.STRATEGIES` (unknown →
  `"heuristic"`). User AIs: `ai.register(name, fn)`; `ai.load_models()` loads
  `models/*.py`. `menu` is the one shell module that imports `ai` — never copy
  that into `render` or `input`. `models/` is committed (the web build ships it).
- **Non-Python bots are subprocesses** (`botio` wire format, `tests/botproc.py`,
  `bots/<name>.bot.json`), outside `models/` since WASM can't fork. Their
  randomness is derived (`botio.decide_seed`). A degraded seat (`degraded_runs`)
  must never be scored or posted. `docs/design/bots.md`, "External bots".

Detail: `docs/design/core.md`.

### Animated end of turn (turnfilm.py)

A **playback of a finished turn** onto a deep copy; `end_turn` still resolves
atomically and `RULES_VERSION` never moves for it. On by default as a local
preference (`webstore.animate_turns`), never a `Settings` field. Full rule list:
`docs/design/turnfilm.md`, "The rules in one place". The easiest to break:

- **Events carry results, not rules**: `turnfilm.Reel` never re-simulates or
  draws dice; beat order is always the engine's.
- **Only movement spends time**; `FILM_COMBAT_MS` **must stay 0**. Every reel
  goes through `main._primed`.
- **Marks outlive their film**; a jump (history, scrub, rewind) calls
  `clear_fading_marks`.
- **Do not widen `_lane_crossings`' sort tuple** (`(when, a, b)` decides who
  rolls first; widening moves stored replays silently). Sub-turn position is
  `Fleet.progress_at` only.
- **Fog is a union** (`Ui.sees`); `visible` is never overwritten. Deferred work
  is paid in `main.land_film` only.
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
`main.resolve_turn`, never inside `end_turn`); every bot seat decides from the
same start (`engine.decide_seat`). **Combat never draws from it**: each fight
rolls its own stream (`engine._Dice`). Rules in full: `docs/design/core.md`,
"Replay and history: the rules in one place".

### Play-by-post (pbp.py)

Per-seat URLs onto a shared match, played asynchronously. Reasons:
`docs/design/pbp.md`, under the same headings as the full rules.

- **Thin server; clients resolve.** `netlify/functions/pbp.mjs` and the `pbp_*`
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
  unavailable. **The roster is the truth about who is a person**
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
  (`mapgen._name_systems`), never a key; `starnames.py` is generated
  (`tools/gen_starnames.py`) — regenerate, don't hand-edit.
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
  `bot_scores` has no public insert path.
- **Replays are uploaded; scores are checked against them**
  (`tools/verify_scores.py`). Only *Post to leaderboard* and *Share replays*
  send; an autoplay demo never does. `game_logs` is not publicly readable or
  writable. A new column the board posts to `scores` joins the column grant
  (`test_schema_grants`), never `submitted_at`. `Ui.can_post` gates sharing.
- **Playstyle readings are keyed by match, never by name**;
  `leaderboard/js/playstyle-baseline.mjs` is generated, never edited.
- **Crowns, campaign and embargoes are derived, never stored.**
  `campaign_games` matches by jsonb equality. `attemptStatus` is the one answer
  to "may I move here"; the game asks `/api/campaign` and never reimplements a
  rule.
- **The game and board are one site** (`/game/`, `/board/`, `/api/`).
  Endpoints are built at call time; `paths.LEADERBOARD_ORIGIN` blank disables
  every leaderboard feature. `tools/pwa/sw.js` never touches `/api/`. The
  sensitive-variable policy stays on "Require approval".

**The AI** (rules in full: `docs/design/bots.md`, "Per-seat AI and pricing a
fight: the rules in full")

- **AI is per-seat and pluggable** (`Player.ai_strategy`, `model.AiParams`).
- **A random seat is resolved in `build_state`** from `settings.RANDOM_POOL`,
  never the loaded roster. **Editing the pool re-deals every random map ever
  shared** (`test_the_pool_still_deals_what_it_dealt`).
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

**Shell** (rules in full: `docs/design/shell.md`)

- **Nothing that holds text gets a fixed pixel size**: use `widgets.py` and
  `config.s()`; key font caches on `config.ui_scale`. `render`'s `_`-prefixed
  kit globals stay swappable. `menu` lays out on a fixed 1440x960 canvas.
- **`config.touch_ui`**: drop keyboard-only strings; floor targets at
  `config.TOUCH_MIN_TARGET`.
- **Quitting goes through `main.leave_app()`.**
- **`viewstate.Ui` holds all transient interaction state**; `main.resolve_turn`
  expands rules into `Order`s.
- **The send popup is the only ship-count editor.** Orders and rules are reached
  from the map. Forwarding-rule share arithmetic lives in `model` only
  (`Ui.forward_this_turn` is the one formula).
- **The Combat tab** uses `combat.preview_fight` (no `config`, no rng);
  `test_tab_content_stays_inside_the_panel` guards it.
- **Route mode** is the one control that doesn't commit as you go.

### Map generation (mapgen.py)

`random` and `symmetric` modes; both stay connected and planar-ish. **`hub`
must keep drawing exactly the board it always did** (`test_symmetric.py`);
nothing past the sectors draws from `state.rng`. Show
`Settings.system_count()`, never raw `nodes`. **Map generation never reads a
screen size** (`test_no_core_module_reads_a_screen_size`). The lane survey runs
in `menu.pump`, never `draw`. `docs/design/core.md`, "Past a standard board the
box grows".

### Hand-authored maps (custommap.py, mapmaker.py)

Full list: `docs/design/hand-maps.md`, "The rules in one place". The easiest to
break: **`"custom"` never joins `settings.MODES`**; every stored value is
concrete; never widen `WORLD_SIZE`; `CustomMap.from_dict` never raises,
`generate_custom` asserts; `normalised()` stays idempotent and deletion goes
through `without_node`; the editor's model is the recipe, never a `GameState`;
`problems()` is the one validator; `tools/bot_replay._OUTCOME_MODULES` includes
`custommap`.
