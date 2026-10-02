# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, pygame for presentation, `uv` for dependency management.

This file states the rules. The reasons, measurements and rejected alternatives
behind them live in `docs/design/`, one file per area, under headings that match
this file's. Start at [`docs/README.md`](docs/README.md), which says which file
holds which section and lists every idea that was built or proposed and then
decided against. **Check that list before proposing a mechanism, a bot tactic or
a re-tune**, and read the relevant design file when you're actually touching
that code, not as background reading. The files: `core`, `shell`, `turnfilm`,
`hand-maps` and `leaderboard` for the game and the board; `bots` for the roster
as a whole, then `knower`, `marshal`, `marshal-pricing` and `marshal-flow`;
`pbp` for play-by-post. Keep each design file under ~1000 lines, and split by
topic and update the index when one grows past that. Keep this file to rules and
pointers: when a rule needs its reasoning, the reasoning goes in a design file.
Two docs point outward rather than inward: [`docs/bot-api.md`](docs/bot-api.md)
is the wire protocol for non-Python bots, and
[`docs/bot-brief.md`](docs/bot-brief.md) is a self-contained brief a player
pastes into an AI assistant to have a bot written — its starter bot is executed
and its API references checked by `tests/test_bot_brief.py`, since nobody diffs
a pasted document against the code.

## Commands

```sh
uv run python main.py                          # opens the setup menu
uv run python main.py --players 4 --mode symmetric   # CLI args pre-fill the menu
uv run python main.py --seed 42 --nodes 24     # pre-fill a reproducible map
uv run python main.py --no-menu --autoplay     # skip the menu; AI plays every seat (demo)

uv run pytest                         # full suite
uv run pytest tests/test_engine.py    # one file
uv run pytest tests/test_engine.py::test_production_cadence   # one test

uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --film --trials 200   # ...also checking every turn's
                                                 # playback rebuilds that turn
uv run python -m tests.sim --trials 200          # batch stats (winners, length, timeouts)
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
uv run python -m tests.sim --swap --trials 50    # ...or as one free-for-all

uv run python tools/check_bot.py NAME           # validate a models/ or bots/ bot:
                                                # legal orders, read-only, reproducible
uv run python tools/bot_replay.py --dry-run     # leaderboard bot column, computed
                                                # but not posted (needs SUPABASE_*)
uv run python tools/verify_scores.py --dry-run  # replay each posted score's log
                                                # and say whether it checks out
uv run python tools/admin.py matches            # moderation: delete scores/maps/
                                                # matches, rename, drop tags, reissue
                                                # or reopen a seat (dry run until --yes)
uv run python tools/position_suite.py           # rank bots on positions out of
                                                # real games (local games/ dir)
uv run python tools/config_census.py            # which setups people actually
                                                # play (public tables, no key)
uv run python tools/setup_sweep.py              # ...and whether the roster's
                                                # ranking moves on one of them
node --test leaderboard/tests/*.test.mjs        # the leaderboard's own JS suite
```

There is no lint step in `pyproject.toml`/CI; match the surrounding style. VSCode
runs Pylance in basic type-checking mode (`.vscode/settings.json`) — treat its
type warnings as real signal, not noise.

## Architecture

The whole point of the layout is a hard split between a **pure simulation core**
and a **thin pygame presentation shell**, so the entire game is testable
headlessly. Respect these boundaries — they are load-bearing, not stylistic:

- **Core — imports no pygame:** `model`, `geometry`, `mapgen`, `combat`,
  `engine`, `ai`, `botio`, `settings`, `fog`, `replay`, `turnfilm`, `custommap`,
  `pbp`, `matchnames`, `campaign`. `tests/test_settings.py::test_no_core_module_imports_pygame`
  parses for it. `fog` (visibility) and `turnfilm` (playback, which the engine
  writes into and never reads back) are presentation-only: the engine and AI
  never consult them.
- **Shell — the only pygame modules:** `render`, `input`, `menu`, `widgets`,
  `mapmaker`, and `main`. `main` is `starconquest/main.py`, like every other
  module; the root `main.py` is only a launcher shim, kept there because
  `tools/build_web.sh` hands pygbag a `main.py` at the top of its stage dir.
  Tests import it as `from starconquest import main`.
  - `render.py` reads `GameState` + `Ui` and draws; it **never mutates them and
    never imports `engine` or `ai`** (derived stats like threat are local
    helpers). It stores nothing time-varying: a frame is a pure function of
    `GameState` + `Ui` + time (`_flow_phase` reads the clock; playback reads
    `Ui.film`/`Ui.film_ms`), so a test drives a film frame by setting a field.
  - `input.py` mutates **only** `Ui` (and queues human `Order`s), and returns an
    action string (`"end_turn"`, `"toggle_play"`, `"toggle_autoplay"`,
    `"toggle_history"`, `"rewind"`, `"restart"`, `"menu"`, `"quit"`) or `None`
    for `main.py` to act on.
  - `menu.py` has the same split: `draw` only reads `Settings`, `handle_event`
    mutates `MenuState`/`Settings` and returns `"start"`/`"quit"`/`None`;
    `menu.pump` is a per-frame poll for text that never reaches the SDL queue.
    `main.py` runs a `"menu"` ⇄ `"game"` state machine.
  - **Browser bridges** — `softkeyboard` (focuses a hidden DOM input so a touch
    keyboard appears), `webstore` (address bar, clipboard, key/value store; a
    JSON file under `data_dir()` off the web) and `share` (the one that talks to
    a network: posts replays, fetches one to watch as a polled `Download`). All
    pure of pygame, guarded everywhere, silent on failure, never blocking a
    frame. `share` sends **only** on *Post to leaderboard* or with *Share
    replays* ticked. Detail: `docs/design/shell.md`, "Browser bridges".

### Turn resolution (engine.py)

Turns resolve **simultaneously**: `end_turn` collects every seat's orders against
the *same* start-of-turn state, then applies them together. The phase order is
load-bearing: AI decisions → advance fleets → lane battles (opt-in) →
production (**before** combat, so a hull finished this turn defends the system
it was built at; a system captured this turn accrues from next turn) →
arrivals+combat (grouped per node, launch-order independent) → win check →
`turn += 1`. Nothing in the roster prices that pending hull, so a bot sizing an
attack off `target.ships` alone can meet one more ship than it counted —
`prod_progress`/`production` say when.

- `apply_order` deducts ships at launch, so a fleet in transit is off the board
  and order-issuing has no bearing on outcomes.
- **In-lane battles** (`config.IN_LANE_BATTLES`, off by default) are the one
  exception to "fleets on lanes never interact": enemy fleets fight only when
  their paths touch or cross, **pairwise in crossing order**, and a winner is
  only thinned in place. No `DEFENDER_ADVANTAGE` in open space
  (`combat.resolve_lane_clash` passes no `defender_owner`). Detail and reasons:
  `docs/design/core.md`.
- **A pile-up** pools each side per owner (a defender's reinforcement joins the
  garrison), folds attackers **pairwise, strongest-first among themselves**, and
  the survivor faces the defender **last**. `resolve_arrival`'s optional
  `on_step` exists only so the film can show those steps.
- **The engine never imports the AI.** `decide` is injected into `end_turn`;
  `main.py` and `tests/sim.py` pass `ai.decide`, which routes each seat by
  `Player.ai_strategy` through `ai.STRATEGIES` and falls back to `"heuristic"`
  for an unknown name. The seam for user AIs is `ai.register(name, fn)`.
- **Drop-in custom AIs.** `ai.load_models()` registers each `models/*.py`'s
  `decide` under its stem (skipping files that fail); `ai.available_strategies()`
  feeds the menu. `menu.py` is the one shell module that imports `ai` (and calls
  `pbp.configured()`) — never copy that into `render` or `input`. `models/` is
  committed so `tools/build_web.sh` ships the bots to the web build.
- **Non-Python bots are subprocesses** (`botio.py` the pure wire format,
  `tests/botproc.py` the transport, `bots/<name>.bot.json` manifests,
  `docs/bot-api.md` the protocol). `botio` owns no process; `bots/` stays
  outside `models/` because the WASM build cannot fork, so they run only in
  `tests/sim --external` and `tools/bot_replay`. Their randomness is derived
  (`botio.decide_seed`), never drawn. A seat degraded to `heuristic` (dead, or
  `botproc.FORFEIT_TIMEOUTS`) is recorded in `degraded_runs` and must never be
  scored or posted. Detail: `docs/design/bots.md`, "External bots".

### Animated end of turn (turnfilm.py)

The film is a **playback of a finished turn** onto a deep copy: `end_turn` still
resolves atomically, nothing is recorded, `RULES_VERSION` never moves. On by
default as a **local preference** (`webstore.animate_turns`), never a
`Settings` field. Runs on a hand End Turn, play and autoplay; never under fast
forward. The full rule list is `docs/design/turnfilm.md`, "The rules in one
place"; the ones easiest to break:

- **Events carry results, not rules** (`engine.end_turn(on_event=…)`).
  `turnfilm.Reel` assigns and never re-simulates or draws dice; an unknown id is
  a `KeyError`. `film()` groups *consecutive* events into beats, so the order is
  always the engine's.
- **Only movement spends time** (`FILM_MOVE_MS`); every other beat is an instant
  with a *lead*. `FILM_COMBAT_MS` **must stay 0**. Only a turn ended by hand
  lingers (`linger=not (ui.playing or ui.autoplay)`); runs of turns chain with no
  gap (`_next_history_film`, `resolve_turn`), carrying the overrun
  (`_carry_into`) under `config.MAX_FRAME_MS`, and every reel goes through
  `main._primed`.
- **Marks outlive their film** (`Ui.fading_fights`/`fading_hulls`,
  `archive_marks`, `age_fading_marks`, timed by `FILM_FLASH_MS`/`FILM_FADE_MS`).
  With the preference off, marks are still archived (`marking` is wider than
  `filming`). A jump (history, scrub, rewind) calls `clear_fading_marks`.
- **Do not widen `_lane_crossings`' sort tuple**: ties break on `(when, a, b)`,
  which decides who gets the turn's dice first — widening it moves stored
  replays with nothing to prompt a `RULES_VERSION` bump. Sub-turn position is
  one formula, `Fleet.progress_at`; `render._fleet_at` clamps an arriving fleet
  at the rim.
- **Fog is a union** (`Ui.film_visible` + `visible` via `Ui.sees`); `visible` is
  never overwritten. Deferred work is paid in `main.land_film` only.
- **Any press skips a film except** Play/Pause (freezes it, `Ui.film_paused`,
  only when pausing a running play-through), the camera cluster, and Autoplay /
  Take control.
- **The correctness test is the history path**: turn *i*'s film applied to board
  *i-1* must land on board *i* (`tests/test_turnfilm.py`, `tests.sim --film`).

### Persistence, replay & history (replay.py)

A match is **never snapshotted**: `replay.GameLog` records `Settings`, the
concrete seed, and per turn **every seat's orders plus the combat draws**
(`engine.TurnRecord`), auto-saved to the gitignored `games/` dir.
`replay.reconstruct` feeds them back through `end_turn(script=…)`, so **no seat
is ever asked to decide again** — retuning, rewriting or deleting a bot cannot
move a stored game, and no per-model replay floor should be added. What can move
one is the engine: bump `engine.RULES_VERSION` by hand with any change to phase
order, fight resolution or map generation from a seed (and
`leaderboard/js/config.mjs`'s `CURRENT_RULES_VERSION` with it;
`test_leaderboard_sync` pins the pair). Version-1 logs and outdated logs
(`GameLog.is_current`) are declined rather than replayed as if they reproduced.
A log also carries the per-turn `"ai"` flag (for `hand_turns` and the seat claim),
`"rules"` (forwarding rules) and `match_id` (from `settings.fresh_rng`; `truncate`
keeps it, `fork` mints a new one). History mode is shell-only (`Ui.history`,
`main.build_history`); rewind truncates mid-game and forks a finished game.
Reasons and alternatives: `docs/design/core.md`.

### Play-by-post (pbp.py)

A per-seat URL onto a shared match, played asynchronously. It fits because turns
already resolve simultaneously. The rationale for each rule is in
[`docs/design/pbp.md`](docs/design/pbp.md), under the same headings.

- **Thin server; clients resolve.** `leaderboard/netlify/functions/pbp.mjs` and
  the `pbp_*` tables store orders and the log. They never hold a board or run an
  engine. The function holds the only write key, like `log.mjs`. A seat token is
  scoped to one match, never to a person. Play-by-post matches never appear on
  the leaderboard.
- **The stored log is the record, and a turn is decided once.** Whoever resolves
  a turn uploads its log, and every other client applies it (`pbp.match_log`,
  `pbp.settled_turn`). Nobody decides that turn again, because the bots stop on
  a wall clock. The resolver's rng is derived (`pbp.reseed`), never carried.
  Bots decide on a scratch copy (`pbp.turn_orders`), so the dice roll after every
  order is fixed. Each stepping client re-rolls them (`pbp.verify_turn`).
  `match_log` refuses a log that files an order under a person's seat. The
  endpoint keeps the first upload, and a client that loses the race rebuilds
  from it. The uploaded log carries no forwarding rules (`pbp.shareable`), so
  each device keeps its own seat's (`pbp.remember_rules`, `sc_pbp_rules`),
  saved on submit and at every turn's end, and `main.open_match` restores them.
- **Order sequence is the whole of determinism.** `engine._collect_orders` runs
  in ascending seat id and must never depend on submission order.
  `RULES_VERSION` did not move for any of this, and must not.
- **Fog is convenience, not secrecy.** A client holds the whole log. The board
  digest is a tripwire against drift, not an anti-cheat mechanism. The live
  turn's orders are released all at once, only when every seat is in
  (`visibleOrders`).
- **Nothing resolves a play-by-post turn on a clock.** End Turn submits
  (`main.pbp_send`), and play and autoplay are unavailable. A settled turn still
  goes through `main.resolve_turn`, passing the record as `script`.
- **The roster is the truth about who is a person**, not the `is_human` flags
  left by a rebuild (`pbp.seat_people`, re-stamped in `main.open_match`).
- **How a deadline works.** A first miss holds (the seat files empty orders), and
  a second consecutive miss hands the seat to its bot. The endpoint decides the
  lapse (`lapsedSeats`, re-checked in `handleLapse`) from the `source` column.
  Any client may file the bot's orders, computed on a board copy, but never a
  lapse for its own seat.
- **Public matches are listed; open seats are claimed, not handed out.** Only
  `public` rows appear on the lobby page (`leaderboard/pbp.html`, `?action=list`).
  A public match mints the creator's token alone, and an open seat is one with
  no stored hash until `?action=claim` mints it.
- **A match's name is derived; its title, seat names and winner are claims.**
  `matchnames.phrase` (mirrored in `leaderboard/js/matchnames.mjs`, pinned by
  `test_leaderboard_sync`) labels a match from its id and is never a key. Title
  and `names` are self-declared at create/claim; `winner` rides on the final
  resolve, trusted as far as `finished`. The lobby's "yours" is the game's own
  seat store (`sc_pbp_seats`), read directly since the board shares the game's
  origin. A claim there writes into it in `pbp.remember`'s `{seat, token}`
  shape, and the lobby never prunes it.

### Key conventions

**Determinism and identity**

- **All randomness flows through `state.rng`.** A seed reproduces a map *and*
  every battle; never call the global `random` in core code (`test_mapgen.py`
  asserts determinism). Unreproducible rolls go through
  `settings.random_seed()`/`settings.fresh_rng()` (the web build's fixed
  interpreter image makes global `random` repeat across loads). The sanctioned
  exceptions derive a stream rather than draw one: `botio.decide_seed`, the
  random-seat pick, `pbp.reseed`.
- **Everything is keyed by integer id.** Systems are `dict[int, System]`; lanes
  use `model.lane_key` (a `frozenset`). Neutral is a real player, `id == 0`.
  Star names (`System.name`, `starnames.py`, generated from
  `tools/iau-star-names.csv` by `tools/gen_starnames.py` — regenerate, don't
  hand-edit) are flavour, stamped **last** by `mapgen._name_systems` so seeds
  don't move, and never a key; mechanical readouts stay on ids.
- **Travel time is a query.** Always ask `state.travel_turns(a, b)`, never
  `Lane.travel_turns`: with `config.SHIP_SPEED_GROWTH_PCT` on, ships speed up
  each turn. Growth bites at launch only.
- **A fleet's lane track is stored, never ranked** (`Fleet.lane_slot`, from
  `model.free_lane_slot`, carried by `turnfilm.Launched`, in the film board
  digest, not on the bot wire).
- **A seat commands its own ships and nothing else**: `_collect_orders` filters
  every seat through `engine._own_orders`.

**Settings, links and keys**

- **All balance/aesthetic constants live in `config.py`**, read *live* at call
  time. The Advanced menu tunes copies on a `Settings`; `settings._apply_globals`
  (called by `build_state`) is the single writer back into `config`.
- **`settings.Settings` is the pure, serializable pre-game config**;
  `menu.MenuState` is transient menu state. `settings.build_state(settings,
  seed)` is the one funnel to a `GameState`. `to_dict`/`from_dict` back Save/Load
  (`saves/`) and `to_token`/`from_token` (a URL fragment, mirrored to
  `localStorage` under `paths.WEB_SHARED_SETTINGS_KEY` for installed PWAs). The
  readers are deliberately tolerant. Never prune a non-heuristic seat's
  `ai_params` from a token (`models/README.md` documents it as readable).
- **Adding a field to `Settings` moves `challenge_key()` for every map ever
  shared.** Append a `settings._LEGACY_KEY_DROPS` entry whenever one joins
  (`test_challenge_key_is_stable` fails until you do); only
  `challenge_keys()[0]` is written. Local preferences (`animate_turns`,
  `share_games`) and per-score metadata (`Challenge`, which `challenge_keys()`
  drops) exist to avoid this. The leaderboard folds splits by lookup
  (`KEY_ALIASES`, `fold-game-key.sql`, `submit.findTwin`; the newest key wins).
  Detail: `docs/design/core.md`, "Keys outlive the schema that made them".
- **Whole-number floats don't survive the browser**: Python writes `1.0`, the
  board's JS writes `1`. Never compare setups as text across writers — compare
  by value (jsonb `=`) or after `Settings.from_dict`. `sc_config_key` and
  `setupIdentity` are text identities, safe only among browser-written rows.
  `aux` is the one field whose int/float form survives a decode
  (`_ai_from_dict`); `verify_scores._aux_widened` covers the verifier. Detail:
  `docs/design/core.md`.
- **Challenge links carry a score to beat** (`settings.Challenge`: `turns`,
  `lost`, `hand`, `by`, `key`, `log`; `build_state` ignores it). Turns-to-win,
  ties on fewest ships lost. Clipboard only (`webstore.copy_link`), never the
  address bar or `localStorage`. Editing a challenge's setup asks first
  (`menu._draw_unchallenge`, `Settings.without_challenge()`).

**The leaderboard** (reasons: `docs/design/leaderboard.md`; the site itself:
`leaderboard/README.md`)

- **The bot column is computed offline, never served.** `tools/bot_replay.py`
  (scheduled by `.github/workflows/bot-replay.yml`) replays every `models/` bot
  through the human's seat via `tests/sim.play_settings`, which goes through
  `build_state` so tuned knobs apply. The seat is handed over outright
  (`sim._hand_over` clears `is_human`), gets default `AiParams` except
  `bot_replay.REPLAY_AUX`'s `aux`, and runs with wall-clock guards lifted 100x
  (`ai.set_budget_scale`, a model's `BUDGET_SCALE`). `engine_rev` hashes the
  outcome modules, `models/` and `tests/sim.py`; `replay_rev` excludes `ai`,
  `models/` and the harness. `won`, never `turns`, decides a result. A win
  stores its log on the row, and the Watch link plays that back
  (`standings.botWatchKind`). `bot_scores` has no public insert path.
- **The game uploads replays and the worker checks scores against them.**
  `share.post_log` sends; `tools/verify_scores.py` records `verified` /
  `mismatch` / `unreadable` / `missing` (and `outdated`) in `score_checks`,
  binding the log to the setup (`same_setup`, keyed by `GameLog.setup_key()`,
  never the live `Settings`). Only two things send: *Post to leaderboard*, and
  checkpoints with *Share replays* (`webstore.share_games`); a pure autoplay demo
  never does. `game_logs` is unreadable and unwritable by the public (writes via
  `netlify/functions/log.mjs`), rows carry no identity, and `public_replays`
  exposes only matches a posted score points at. `Ui.can_post` gates every
  sharing action, so a watched replay (`Ui.watched`) can't be posted as yours.
- **Crowns, the weekly campaign and embargoes derive their state from
  `counted_scores` and stored maps; nothing about who holds what is stored.**
  `campaign_games` matches by jsonb equality, never `sc_config_key`. The
  campaign's timers (`GRACE_MS`, `COOLDOWN_MS`, queued wins) are replayed
  against a `now` passed to `fold`, never stored, and `attemptStatus` is the one
  answer to "may I move here" for the page and the game alike: the game asks
  `/api/campaign` (`netlify/functions/campaign.mjs`, which runs that same JS)
  and only counts down the server times it is given (`starconquest/campaign.py`),
  never reimplementing a rule. Detail:
  `docs/design/leaderboard.md` and `leaderboard/README.md`.
- **The game and the board are one site.** The root `netlify.toml` builds both;
  the game is at `/game/`, the board at `/board/`, functions at `/api/`, and
  `tools/pwa/root.html` routes the root. Endpoints are built at call time from
  `LEADERBOARD_*_PATH`, never stored. `paths.LEADERBOARD_ORIGIN` blank disables
  every leaderboard feature. The sensitive-variable policy must stay on
  "Require approval". `legacy-board/` proxies `/api/`. `tools/pwa/sw.js` never
  touches `/api/` (`tests/test_web_build.py`). Detail:
  `docs/design/leaderboard.md`.

**The AI** (reasons: `docs/design/bots.md` and the per-bot files)

- **AI is per-seat and pluggable.** Each `Player` carries `ai_strategy` and
  `ai_params` (`model.AiParams`, defaults mirroring `config.AI_*`); `Settings`
  mirrors both per seat (indexed by seat-1) and `build_state` stamps them.
- **A seat may be left to the seed.** `settings.RANDOM_STRATEGY` (`"random"`)
  is resolved inside `build_state` by `settings.resolve_strategy`, never by a
  dispatcher bot. The pick is derived (`random.Random(f"{seed}:strategy:{pid}")`)
  from `ai.available_strategies()`. `Settings` keeps `"random"`; the win overlay
  (`render._winner_label`) reveals the bot. Detail: `docs/design/core.md`.
- **A bot that reads other seats** (see `models/knower.py`) must never call
  `ai.load_models()`, must read `ai.STRATEGIES` lazily inside `decide`, and must
  draw **nothing** from `state.rng` (`tests/test_knower.py`). A predicting bot
  advertises `IS_ORACLE = True` and optionally `is_oracle_seat(player)`, which
  callers prefer.
- **A bot prices a fight with `combat.edge_attacking()`/`edge_defending()`,
  never a constant**, with the jitter half floored at its `TUNED_SWING`, and
  floors its ask at `target.ships + 1`. Margins compare against the *effective*
  garrison (`ai._frontier_order` multiplies by `DEFENDER_ADVANTAGE`).
  `marshal._enemy_margin` deliberately carries the advantage half and none of
  the jitter half — read `docs/design/marshal-pricing.md`, "Garrisons run away",
  before copying either half.
- **`AiParams.aux` is the one bot-defined knob.** The core never interprets it;
  `1.0` is "untuned". A strategy declares `AUX_LABEL` (+ `AUX_RANGE`, `AUX_INT`,
  `AUX_NAMES`; read by `ai.aux_spec`/`ai.aux_names`). Narrow a knob by clamping
  on read, never by rewriting what was stored. Add per-bot knobs here, not as
  new `AiParams` fields. Detail: `docs/design/bots.md`.
- **A bot can warn about a setup** with `setup_warning(settings, seats)`
  (`ai.setup_warning`, `settings.setup_warnings`), raised on Start and on the
  play-by-post roster's Confirm. knower's is fitted in
  `docs/design/knower.md`, "Cost per decide".
- **An all-bot game has no human seat.** `build_state` clears the `is_human`
  `mapgen` stamps on pid 1 when `Settings.autoplay` is set. The seat is claimed
  by the first turn *ended* under manual control (`end_turn`'s `claim_seat`,
  re-applied by `reconstruct` from the `"ai"` flags), not by pressing Take
  control. `resolve_turn` must pass `human_orders=None` for an unclaimed seat, or
  its strategy runs twice. Resuming, rewinding or watching always lands paused.
  Detail: `docs/design/core.md`.
- **`tests/sim.py` is a demo harness, a test fixture and the tournament host**
  (`check_invariants`, `--ladder`, `--swap`, `--external`, `play_settings`,
  `play_from`). After changing `ai.py` or travel/combat balance, run a
  `--trials` batch and watch the timeout rate. **Sweep node count and ship
  speed, not just the defaults**: a margin keyed off travel time is live in one
  regime of three. Detail: `docs/design/bots.md`.

**Shell**

- **Nothing that holds text gets a fixed pixel size.** Use the `widgets.py` kit
  (`btn_w`, `btn`, `row_h`, `draw_modal`, `wrap`) and `config.s()`. `render`
  binds the kit to `_`-prefixed module globals so tests can swap them — keep
  that. `menu` deliberately does not use the kit: it lays out on a fixed
  1440x960 canvas with unscaled fonts. `config.apply_ui_scale` runs once at boot;
  key any font cache on `config.ui_scale`.
- **`config.touch_ui` is the input modality.** On touch, drop keyboard-only
  strings (`render._key_hint` and friends) and floor tappable controls at
  `config.TOUCH_MIN_TARGET`.
- **Quitting goes through `main.leave_app()`.** On the web it asks the browser
  to close and falls back to the menu with `main.CANT_CLOSE_MSG`; never end the
  loop directly there.
- **The map viewport has two margins**, `config.map_fit_padding()` and
  `config.map_pan_padding()`, both floored at `config.node_clearance()`.
- **`viewstate.Ui` holds all transient interaction state**, including
  human-only conveniences like `auto_forward` rules; `main.resolve_turn` expands
  them into `Order`s.
- **The send popup is the only ship-count editor** (`Ui.begin_send`,
  `edit_order`, `edit_forward`; `Ui.editing_existing`). A dormant rule never
  opens it (`Ui.rule_is_live`; `prune_forward` deletes rules whose source was
  taken). Its slider is hit-tested before the panel drag.
- **The queued list is capped and scrolled** (`ui.order_scroll`), and each row
  carries its own index into `pending` (`ui.order_hitboxes`).
- **Losing makes the human a spectator**: the whole board is revealed once
  `is_defeated(human_id)`, and fast forward (`main.FAST_FORWARD_MS`) is offered.
- **The Combat tab teaches the square law from the real code**:
  `combat.preview_fight` shares `resolve_fight`'s helpers, takes
  `jitter`/`advantage` as parameters (never `config`) and draws no rng. Its demo
  sliders write `MenuState`, not `Settings`. Its prose is hand-broken, and
  `test_tab_content_stays_inside_the_panel` guards the 560x496 panel.
- **Route mode (`viewstate.ROUTING`) is the one control that doesn't commit as
  you go**: a proposal (`route_sel`, `route_plan`) confirmed in one go. Chain and
  rally share `model.flow_field(by_turns=True)`; paths are owned-only except the
  sinks; a tap always aims and never removes on first press. Detail:
  `docs/design/shell.md`.

### Map generation (mapgen.py)

Two modes: `random` (jittered grid, relaxation, a planar Euclidean MST plus a few
crossing-rejected extra edges) and `symmetric` (one sector rotated about a
shared centre). Both must stay connected and planar-ish (`test_mapgen.py`).
`symmetric` can return **more nodes than asked** (41 at 40), hence
`config.CUSTOM_MAX_NODES`. Past `config.STANDARD_MAX_NODES` (40) the box grows
(`config.world_side`); below it nothing moves. The Advanced tab's lane survey
runs in `menu.pump`, never `draw`. Detail: `docs/design/core.md`.

### Hand-authored maps (custommap.py, mapmaker.py)

A board has a third source: `custommap.CustomMap`, a recipe of concrete systems
and index-pair lanes riding on `Settings.custom_map`, built by
`mapgen.generate_custom`. The full rule list is `docs/design/hand-maps.md`, "The
rules in one place"; the ones easiest to break:

- **`"custom"` must never join `settings.MODES`** (the schema's `check` would
  refuse the token). The setup keeps its mode; the recipe overrides it.
- **Every stored value is concrete**; the seed drives only dice and star names.
  Hand maps live in the wider `config.CUSTOM_WORLD_W` box — never widen
  `WORLD_SIZE`.
- **One tolerant gate, one strict builder**: `CustomMap.from_dict` never raises;
  `generate_custom` asserts, so `menu._start` refuses a map with blockers and
  `mapmaker.commit` writes `None` for an empty recipe.
- **`normalised()` must stay idempotent, coordinates are integers**, and
  deletion goes through `CustomMap.without_node` only.
- **The editor's model is the recipe, never a live `GameState`**, and
  `mapmaker` draws on the real surface, not `menu`'s canvas.
- **`problems()` is the one validator** (Play gate, sidebar, drag and lane
  legality). A crossing lane warns; a lane under a system blocks.
- **The three tools share one scene**: the viewport must not depend on the tool,
  and `ed.rects` keys must not collide. A press never gets a second meaning
  conditional on what it lands on.
- **`custom_map` has its `_LEGACY_KEY_DROPS` entry**, and
  `tools/bot_replay._OUTCOME_MODULES` includes `custommap`.
  `tools/setup_sweep` refuses hand maps.
