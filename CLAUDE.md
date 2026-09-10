# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Star Conquest is a minimalist turn-based strategy game: a graph star map where
systems are nodes and spacelanes are edges, one ship type, take every system to
win. Python 3.12+, pygame for presentation, `uv` for dependency management.

Design rationale, history, and edge-case detail behind the rules below live in
two companion files, keyed by matching headings — read them when you're actually
touching that code, not as background reading.
[`docs/system-design.md`](docs/system-design.md) covers the core and the shell;
[`docs/bot-design.md`](docs/bot-design.md) covers the `models/` roster, the
margins bots price fights with, and the measurements behind every AI constant.
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
  `engine`, `ai`, `botio`, `settings`, `fog`, `replay`, `turnfilm`. This is what lets
  `tests/sim.py` and most of the suite run with no display. Do not add a pygame import
  to any of these. (`fog` is presentation-only visibility — pure hop-distance queries the
  shell reads each turn; the engine and AI never consult it. `turnfilm` is the mirror:
  presentation-only playback the *engine writes into* and never reads back — see
  Animated end of turn below. `replay` serializes a
  match to JSON and replays it back through the headless engine — see Persistence
  & replay below.)
- **Shell — the only pygame modules:** `render`, `input`, `menu`, and `main`.
  - `render.py` reads `GameState` + `Ui` and draws; it **never mutates them and
    never imports `engine` or `ai`**. Derived display stats (threat, inbound,
    per-player production rate) are computed with local helpers rather than
    reaching into `ai`. Its layout is measured rather than hardcoded — see the
    scaling convention under Key conventions. It stores nothing time-varying: the
    selected forward rule's chevron conveyor reads the wall clock in `_flow_phase`,
    and the turn playback reads `Ui.film`/`Ui.film_ms` (an immutable film plus a
    clock `main` advances from the loop's own `dt`), so a frame stays a pure
    function of `GameState` + `Ui` + time — and a test drives a film frame by
    setting a field rather than monkeypatching a clock.
  - `input.py` mutates **only** `Ui` (and queues human `Order`s); it never
    touches the simulation. It returns a high-level action string
    (`"end_turn"`, `"toggle_play"`, `"toggle_autoplay"`, `"toggle_history"`,
    `"rewind"`, `"restart"`, `"menu"`, `"quit"`) or `None`, and `main.py` decides
    what to do with it.
  - `menu.py` is a self-contained pre-game scene with the same draw/mutate split:
    `draw` only reads `Settings`, `handle_event` mutates `MenuState`/`Settings`
    and returns `"start"`/`"quit"`/`None`. `main.py` runs a two-scene
    (`"menu"` ⇄ `"game"`) state machine and builds the `GameState` on `"start"`.
    `menu.pump` is the second half of the mutate side: a per-frame poll `main.py`
    calls while the menu is up, for text that never reaches the SDL event queue
    (see `softkeyboard` below).
  - `softkeyboard.py` is a browser-only bridge, not a pygame module: on a touch
    browser it focuses a hidden DOM `<input>` so the mobile on-screen keyboard
    actually appears (SDL's `start_text_input` has nothing to focus there) and
    reports back what was typed. Everything is guarded — off the web build, on a
    desktop browser, or if any DOM call fails, every function is a no-op and the
    menu keeps its plain SDL text path.
  - `webstore.py` is the other browser bridge, same style: the address bar and a
    small key/value store (shared-settings tokens, personal bests). See the
    challenge-link notes under Key conventions.
  - `share.py` is the one bridge that talks to a network, and the one that is
    not browser-only: it posts a finished match's replay to the leaderboard so a
    posted score can be checked against the game that produced it. Same
    defensive style — guarded everywhere, silent on failure,
    fire-and-forget on both backends (a `fetch` whose promise
    is never read on the web, a daemon thread off it) so it can never stall the
    frame. Pure of pygame, and it sends **only** when the player presses *Post to
    leaderboard* or the player has ticked *Share replays*. Its other half
    *fetches* a replay to watch (`fetch_log` -> a `Download` the loop polls once a
    frame, never awaited), which is the one thing here that reads a response. See
    "Checked scores" under Key conventions.

### Turn resolution (engine.py)

Turns resolve **simultaneously**: `end_turn` collects every player's orders
against the *same* unchanged start-of-turn state, then applies them together, so
there is no turn-order advantage. The phase order inside `end_turn` is
deliberate and combat/production correctness depends on it: AI decisions →
advance fleets → lane battles (opt-in; see below) → arrivals+combat (fleets
arriving at a node are grouped and resolved together, launch-order independent)
→ production (after combat, so a system captured this turn produces for its new
owner) → win check → `turn += 1`.

**In-lane battles** (`config.IN_LANE_BATTLES`, off by default, on the menu's
Combat tab) are the one thing that breaks "fleets on lanes never interact". Two
*enemy* fleets fight only on the turn their paths touch or cross — sharing a lane
is not enough — and they fight **pairwise, in crossing order**, so a strong fleet
running a defended lane picks its opponents off one at a time and carries its
losses into each next fight. Nothing is pooled and nothing is moved: a winner is
thinned in place and keeps its own heading, speed and arrival turn, so a fleet is
only ever drawn where it really is. `engine._lane_span` measures both fleets from
one end of the lane and mirrors `Fleet.progress` (what `render` draws), so a fight
happens exactly where the triangles are seen to touch. Siting the phase *after*
`_advance_fleets` but *before* `_resolve_arrivals` is what lets a single-turn hop
still be intercepted — it is on the board, at `turns_remaining == 0`, for exactly
one lane-battle check. `combat.resolve_lane_clash` passes no `defender_owner`:
nobody holds open space, so `DEFENDER_ADVANTAGE` applies to neither side and an
exact tie annihilates rather than breaking to a defender — but the jitter, which
belongs to the dice rather than the ground, still applies.

**The engine never imports the AI.** The decision function is injected as the
`decide` parameter to `end_turn`; `main.py` and `tests/sim.py` pass `ai.decide`,
a per-seat dispatcher that routes each seat to its named strategy
(`ai.STRATEGIES`, keyed by `Player.ai_strategy`; `ai.decide` falls back to the
built-in `"heuristic"` for any unknown name, so a stale/missing strategy never
crashes). Keep this inversion — it is why the core has no AI dependency, and it is
the seam for user-written AIs (`ai.register(name, fn)`, `fn(state, pid) -> list[Order]`).

**Drop-in custom AIs.** `ai.load_models()` imports every `*.py` in the
`models/` dir (repo-anchored `ai.MODELS_DIR`, mirroring `menu._SAVE_DIR`) and
`register`s each file's `decide` under its stem; a file that fails to import or
lacks `decide` is skipped. `main.py` calls it at startup and on game start;
`ai.available_strategies()` feeds the menu's per-seat Strategy dropdown. `menu.py`
is the one shell module that imports `ai` (for discovery) — fine, since `ai` is
pure core (no pygame); the render/input prohibition on importing `ai` still holds.
`models/` is committed (not gitignored) precisely so `tools/build_web.sh` can
stage it alongside `starconquest/` and ship the same bots to the browser/PWA
build — new bots go in via commit/PR, not local drop-in only.

**Bots that aren't Python are subprocesses, and they compete without shipping.**
`botio.py` is the wire format (`hello` once, `turn_payload` per decision,
`orders_from` back) and `tests/botproc.py` the transport; a bot is a
`bots/<name>.bot.json` manifest naming a command, and `docs/bot-api.md` is the
protocol. Four rules hold it together:
- **`botio` is pure core and owns no process.** No `subprocess` import in the
  package, and none in the ordinary suite either — `tests/sim` imports
  `botproc` lazily, inside `--external`.
- **`bots/` lives outside `models/`, and that is load-bearing.**
  `build_web.sh` stages `models/`, and the web build is CPython on WASM: it
  cannot fork at all. So external bots run in `tests/sim` and (once decided)
  `tools/bot_replay`, never in the app or the browser, and the in-app Strategy
  dropdown stays Python. Registration is opt-in (`sim --external`) rather than
  automatic, unlike `ai.load_models()`, because `bot_replay`'s roster is
  `ai.available_strategies()`.
- **The bot's randomness is derived, never drawn.** `botio.decide_seed(seed,
  turn, pid)` hands a seat its own stream, so an external bot cannot shift the
  engine's dice and every other seat's battles roll as they did without it. It
  is the one sanctioned exception to "all randomness flows through `state.rng`",
  and it keeps what that rule protects: a seed still reproduces every fight.
- **A degraded seat is not a result.** A timeout holds for one turn; a dead bot
  or `botproc.FORFEIT_TIMEOUTS` timeouts falls the seat back to `heuristic` and
  records it (`degraded_runs`, printed by `sim`). The app's silent fallback for
  an unknown strategy name is right there and wrong in a tournament, where a run
  containing a fallback seat must never be scored or posted.

`apply_order` deducts ships from the source at launch, so a fleet is "off the
board" in transit (fleets on lanes never interact); order-issuing has no bearing
on outcomes.

### Animated end of turn (turnfilm.py)

Off by default, and a **local display preference** (`webstore.animate_turns`,
`paths.WEB_ANIMATE_TURNS_KEY`) rather than a `Settings` field — how a turn is
*drawn* cannot move a result, and a new `Settings` field would move
`challenge_key()` for every map that ever existed. Runs after a human End Turn and
in play mode; never under autoplay or fast-forward (the gate is in
`main.resolve_turn`, read once a turn — never in `render`, where a store read
costs a DOM call every frame). History playback animates too; scrubbing stays an
instant seek.

**It animates the past, not the present.** `end_turn` still resolves the turn
immediately and atomically, so the live `GameState` is always the fully-resolved
board; the film is a *playback of a finished turn* onto a deep copy, held as a
main-loop local and drawn through the same `render.draw(screen, view_state, ui)`
swap history mode uses. Nothing is observable mid-flight and skipping is therefore
always safe (any press but Play/Pause; `Ui.stop_film`) — pausing freezes it in
place instead (`Ui.film_paused`), so a paused review keeps showing what the turn
did rather than reverting to the plain board a skip leaves behind. `RULES_VERSION`
does not move for any of it, and nothing is recorded — a film is derived per turn
and discarded, so the log format is untouched.

Rules that hold it together:
- **Events carry results, not rules.** `engine.end_turn(on_event=…)` reports
  outcomes (`Advanced` carries each fleet's *new* `turns_remaining`, `Landed` the
  node's new owner/ships), so `turnfilm.Reel` assigns and never re-simulates: it
  draws no dice and does no arithmetic, which is what makes drift impossible.
  `Reel` is strict — an unknown id is a `KeyError`, not a silent glitch.
- **The order is the engine's, never one written down in `turnfilm`.** `film()`
  groups *consecutive* events of one class into a beat, so moving `_production`
  ahead of `_resolve_arrivals` reorders the playback with nothing here to change.
  `config.FILM_PRODUCE_MS` and `FILM_LAUNCH_MS` are always 0: launch and
  production land at their true place in the sequence with no dwell of their own
  — `Fleet.progress_at` combined with `Film.travel` already starts a launched
  fleet's glide at progress 0, so a held launch beat only bought a stutter before
  movement began, and a finished hull is made visible by a mark (below), not by
  holding the board still to show one. Keep production at 0 unless it stops being
  the last phase: `Film.plays` is "does any beat have a duration", so a dwell
  would make every otherwise-quiet turn pause instead of resolving instantly.
  Combat is the one beat with two speeds: `config.FILM_COMBAT_MS` is 0 by
  default, but `film(events, linger=True)` swaps in `FILM_LINGER_COMBAT_MS` and
  adds a trailing `FILM_LINGER_HOLD_MS` — `main.resolve_turn` asks for that on a
  live End Turn, worth watching resolve, while `main._next_history_film` never
  does, since a run of animated turns there must glide continuously rather than
  stop-start for every fight. Neither path pads `total_ms` beyond what `linger`
  asks for: a non-lingering film ends the instant its last beat does, which is
  what lets the *next* turn's move beat start immediately with nothing left to
  wait out (see history chaining, below).
- **A mark outlives the film that made it, fading on its own clock.**
  `Ui.fading_fights`/`fading_hulls` hold every still-showing fight or finished
  hull as plain data (`viewstate.FadingFight`/`FadingHull`), independent of
  `film`/`film_ms` — populated by `Ui.archive_marks(board, events)` from
  whatever `Reel.run_to` just applied (which is why `run_to`/`run` return that
  list rather than nothing), and aged every frame by `Ui.age_fading_marks(dt)`
  regardless of whether a playback is currently running. A mark is fully visible
  for `config.FILM_FLASH_MS`, then fades over `config.FILM_FADE_MS`
  (`render._mark_fade`, `_faded` blending its colour toward `config.COLOR_BG`)
  — both counted from when it fired, never from any film's own length, which is
  what lets it keep dissolving on top of whatever the *next* turn's glide is
  already doing instead of being cut off the moment `film` is replaced.
  Visibility (`Ui.sees`) is checked once, at archive time, not on every frame a
  mark is drawn — a fight you saw happen keeps fading regardless of what fog
  does afterward. A "jump" rather than a step (entering/leaving history,
  scrubbing, rewinding) calls `Ui.clear_fading_marks()`, since a mark belongs to
  a specific point in a specific playback and jumping away from it makes it
  stale rather than merely old.
- **Sub-turn position is one formula.** `Fleet.progress_at(t)`, which
  `engine._lane_span` measures a lane battle with and `render._draw_fleets` draws
  with, so a clash flashes exactly where the triangles are seen to touch — at the
  crossing fraction `engine._lane_crossings` already solves for. **Do not widen
  that function's sort tuple:** ties break on `(when, a, b)` — order of launch —
  and that decides which fight is dealt the turn's dice first, so letting the
  crossing *position* into the comparison would move stored replays with nothing
  prompting a `RULES_VERSION` bump. Hence the explicit `key=`.
- **The map layer sees both turns' fog; nothing gets swapped.** `Ui.film_visible`
  holds what was visible when the played-back turn began and `Ui.sees` unions it
  onto `visible`, which every map-layer read goes through (nodes, fleets, bursts).
  `visible` itself is never overwritten, so a film has nothing to put back and a
  system you can no longer see cannot leak past one. The HUD reads `visible`
  directly: it describes the position you are handed, not the one being drawn.
- **Whatever a film defers, `main.land_film` pays.** The camera snap that reveals
  the board on the deciding turn waits for the playback (`Ui.deferred_view_snap`),
  and `land_film` is the one place the clock running out and a press skipping both
  pass through — which is why `Ui.stop_film` deliberately leaves the debt alone.
  Anything else a playback holds back belongs there too, never at a call site.
- **Play/Pause freezes a film; every other press still skips it.**
  `input._toggles_play` exempts that one control (the P key, or its footer
  button) from the blanket "any press skips" rule, and `main.apply_toggle_play`
  is what actually holds it: `Ui.film_paused` freezes the per-frame advance,
  set only when *pausing an already-running* playthrough (`was_playing` going
  in) rather than whenever a film merely happens to be up — a manually-triggered
  film runs with `Ui.playing` False throughout, so toggling play *on* while it
  plays must leave it alone rather than freezing it on the first frame.
- **History playback chains an animated turn straight into the next one, and
  never lingers.** Both serve the same end: a run of animated turns glides
  continuously instead of stuttering. `main._next_history_film` is tried
  immediately once a film lands, before the `PLAY_MS` pacing below it — which
  only ever fires for a turn with nothing to animate, or with the preference off
  — and it never passes `linger=True`, so combat there is always the instant,
  unpadded default. A live End Turn is the opposite on purpose: watching your own
  move resolve is worth a pause, which is what `main.resolve_turn`'s
  `linger=True` buys (above). Neither path loses anything by choosing either way
  — a mark's visibility no longer depends on `film` still being current (above),
  so lingering or not only changes how long `film` itself holds the board, never
  how long a mark stays up. **`_next_history_film` must run its new reel to `0.0`
  before returning it**, unlike a live End Turn's reel, which always gets a
  `run_to` call in the same frame it's built (`main`'s per-frame update runs
  right after the event that creates it, this one is built *inside* that
  update). Skip it and a continuing fleet draws one frame at last turn's
  un-advanced `turns_remaining` — a visible snap back by a whole turn's worth of
  progress before the next frame's `run_to` catches it back up.
- **The correctness test is the history path.** One `reconstruct` pass yields both
  a board per turn and that turn's events, so applying turn *i*'s film to a copy of
  board *i-1* must land exactly on board *i* (`tests/test_turnfilm.py`, and at
  volume via `sim.check_film` behind `python -m tests.sim --film`). A missing or
  misapplied event would otherwise surface only as the board snapping on the last
  frame.

`combat.resolve_arrival` takes an optional `on_step` list purely so a multi-owner
pile-up can be shown step by step — those intermediate values are locals and
unknowable from outside, and the fold is the least legible rule in the game (every
side pooled per owner, sorted **strongest-first**, folded pairwise, with
`defender_owner=old_owner` applying in every step). Nothing else in `combat`
changed for this.

### Persistence, replay & history (replay.py)

A match is **never snapshotted** — it is recorded as its *inputs*: `Settings`,
the concrete `seed`, and per turn **every seat's orders plus the combat draws**
(`engine.TurnRecord`, logged by `replay.GameLog`, auto-saved after every turn to a
gitignored, repo-anchored `games/` dir, mirroring `ai.MODELS_DIR` and
`menu._SAVE_DIR`). `replay.reconstruct(log, on_turn=…)` feeds each turn back
through `engine.end_turn(state, script=…)` to rebuild the **exact** state at any
turn — applying the orders verbatim and dealing the recorded dice to combat, so
**no seat is ever asked to decide again**. `main.resume_game` uses this to offer
resuming the last unfinished match from the menu, and restores that turn's
standing auto-forward rules (`"rules"`, `Ui.auto_forward`) with it.

**A replay must never depend on a bot repeating itself** — that is format
version 2, and why the orders and the dice are both in the log (version 1 stored
the human's orders alone and re-ran the AI; a bot on a wall-clock budget replayed
into a *different match*, silently). The corollary is load-bearing now that
replays are kept: **retuning, rewriting or deleting a `models/` bot cannot move a
single stored game**, so no per-model "replay floor" is needed and none should be
added (`test_a_replay_does_not_consult_a_bot_even_a_deleted_one` pins it, and
`bot_replay.replay_rev` is `engine_rev` minus `ai` and `models/` for the same
reason). What *can* move one is the engine itself — phase order, how a fight
resolves, how a map is drawn from a seed — which is what `engine.RULES_VERSION`
is for: bumped by hand with such a change, stamped on every log, and read by the
verifier to report `outdated` (unverifiable) rather than `mismatch` (wrong). Version-1 logs can't be replayed faithfully
and `latest_log` skips them. A turn still carries `"ai"` (was the human seat
autoplayed) — not for replay, but for `GameLog.hand_turns` (which `main.hand_turns`
delegates to, and the leaderboard's verifier recomputes). A log also carries a
`match_id`, minted per match from `settings.fresh_rng` and likewise not part of
what makes a replay reproduce: it is how a posted score names its replay
(`GameLog.encoded` is the wire form — the token's own deflate+base64url). A
rewind (`truncate`) keeps that id; a fork mints a new one.

**History mode** is a shell-only review scene (`Ui.history`, gated so it never
enters the pure core). On entry `main.build_history` runs one `reconstruct` whose
`on_turn` callback deep-copies each turn's board and folds fog (via
`_accumulate_fog`) into a per-turn snapshot, so the bottom-bar scrubber seeks by
plain list-indexing. Fog stays a `Ui`-layer concern: mid-game a past turn shows
fog *as it was then*, while a finished game is fully revealed. **Rewind** resumes
live play from the viewed turn — mid-game it truncates the same log file
(`GameLog.truncate`, confirmed first, since it discards later turns); on a
finished game it forks a new file (`GameLog.fork`) so the completed record stays
intact.

### Key conventions

- **All balance/aesthetic constants live in `config.py`.** Do not hardcode a
  magic number elsewhere — add a named constant there. Every module reads
  `config.X` *live* at call time (nothing is cached at import), so the Advanced
  menu tunes copies on a `Settings`, and `settings._apply_globals` (called by
  `build_state` just before generation) is the single writer that pushes them
  back into `config`.
- **Nothing that holds text gets a fixed pixel size.** A button's width comes
  from its measured label (`render._btn_w`, and `render._btn` draws + returns
  the hit-rect), a stacked text row's pitch from the font's own line height
  (`render._row_h`), a modal's stack is measured then centred
  (`render._draw_modal`), and help prose is reflowed to the panel it sits in
  (`render._wrap`). One-off layout literals still go through `config.s()`.
- **`config.touch_ui` is the input modality**, set beside the scale in
  `apply_ui_scale` from `main`'s single boot-time probe (Android, or a touch
  browser). On a touch build the shell drops every keyboard-only string — the
  `(Esc)`/`(R)` suffixes on button labels (`render._key_hint`,
  `render.confirm_labels`, `menu._resume_labels`), the shortcut lines in the info
  panel's help text and the win overlay, the menu's `Enter: start game` footer —
  and floors tappable controls at `config.TOUCH_MIN_TARGET` (`render._tap_size`).
- **`settings.Settings` is the pure, serializable pre-game config** (players,
  map, seed, global knobs, per-seat AI); `menu.MenuState` holds transient menu
  interaction state (analogous to `Ui`). `settings.build_state(settings, seed)`
  is the one funnel from menu/CLI to a `GameState`. `to_dict`/`from_dict` back
  both the JSON file Save/Load (menu footer, gitignored `saves/`) and a
  `to_token`/`from_token` pair that encodes a whole config into a URL fragment
  (mirrored to `localStorage` under `paths.WEB_SHARED_SETTINGS_KEY` so an
  installed PWA, which launches from a fixed `start_url`, still sees it).
  `from_token`/`from_dict` are deliberately tolerant (clamp, default, pad), so a
  stale or hand-edited token still loads to a playable config. Never prune a
  non-heuristic seat's `ai_params` when writing a token: it is a documented
  readable field for drop-in bots (`models/README.md`).
- **Challenge links carry a score to beat.** `settings.Challenge`
  (`turns`, `lost`, `hand`, `by`, `key`) is an optional field on `Settings`, so it
  rides all of the above with no new plumbing; `build_state` ignores it. Score is
  turns-to-win, ties broken on fewest ships lost (`Player.ships_lost`, written in
  `combat.resolve_arrival`). A challenge token travels by clipboard only
  (`webstore.copy_link`) — never the address bar or `localStorage`, unlike a
  settings link (`webstore.share_token`). Editing a challenge's setup asks first
  (`menu._draw_unchallenge`); `Settings.without_challenge()` is what persists a
  "change it anyway".
  - **The leaderboard's bot column is computed offline, never served.**
    `tools/bot_replay.py` replays every `models/` bot through the human's seat on
    each posted map and caches the answer in `bot_scores`; a scheduled GitHub
    Action (`.github/workflows/bot-replay.yml`) is the whole backend, since the
    result is a pure function of the setup, the seed and the code. It drives
    `tests/sim.play_settings`, which goes through `settings.build_state` rather
    than `mapgen.generate` — a posted setup carries tuned knobs, and that is the
    only funnel that pushes them into `config`. The replayed seat gets default
    `AiParams` (slot 0 is the human's) except for `aux`, the bot-defined knob —
    `bot_replay.REPLAY_AUX` names each bot's best profile there (`knower` at
    search depth 12) and the value in force is stored on the row; opponents keep
    theirs. It also lifts the bots' own per-decide wall-clock guards 100x
    (`ai.set_budget_scale`, opt-in via a model's `BUDGET_SCALE`): those are sized
    so the browser tab never freezes, and tripping one is the only thing that
    makes such a bot's output depend on the clock — so a batch run that can never
    trip one is *more* reproducible, not less. `won`, never
    `turns`, says whether a bot took the board, and a loss is listed but never
    ranked (`standings.botOrder`). `bot_scores` is the one table with no public
    insert path: the worker's secret key is its only writer.
  - **The game uploads replays, and the worker checks scores against them.**
    `Challenge.log` carries `GameLog.match_id` into the link, `share.post_log`
    sends the log itself, and `tools/verify_scores.py` (the same scheduled worker
    as the bot column) replays it and records `verified` / `mismatch` /
    `unreadable` / `missing` in `score_checks`. The id rides on `Challenge` rather
    than `Settings` precisely because `challenge_keys()` drops that field before
    hashing — see the next bullet for what a `Settings` field would have cost.
    Four rules hold this together:
    - **Two things send, both consented to.** Pressing *Post to leaderboard*
      uploads that match. With *Share replays* ticked (`webstore.share_games`, a
      local preference — never a `Settings` field, which would travel in every
      link and move every setup digest), a game also checkpoints every
      `share.CHECKPOINT_TURNS` turns and again when it ends, which is what keeps
      the *lost and abandoned* games — the ones no score can carry. Nothing else
      sends, and a pure autoplay demo (`hand_turns == 0`) never does: it is
      reproducible from its seed, so it is bytes without information.
    - **`game_logs` is the one table the public can neither read nor write.** RLS
      on, no policies, no anon grants. Writes go through the leaderboard site's
      own `netlify/functions/log.mjs` under the secret key, which is what
      makes a size and rate limit enforceable — a replay is 5-14 KiB, so an open
      insert path is a storage bill rather than a few junk rows. Reads are the
      worker's alone, so uploading a game does not publish it.
    - **A row carries no identity** — a match id, a setup key and the moves.
      Grouping one person's games would need a durable client id, which is a
      tracking identifier by any other name.
    - **Posting a score publishes that replay, and only that replay.**
      `public_replays` is `game_logs` restricted to the matches a posted score
      points at — the one view here deliberately *not* `security_invoker`, since
      lending out a subset of a table nobody may read takes owner rights. A
      launch URL of `#log=<match id>` makes the game fetch it and open history
      review on it (`main.replay_request` -> `open_replay` -> `open_history`,
      which the H key shares), so the board's *Watch* link is a link back into
      the game rather than a second engine in JS. A watched match is marked
      (`Ui.watched`), because it ends on the same win overlay ours do:
      `Ui.can_post` is the single predicate behind both sharing buttons, the
      personal best and the checkpoint upload, so nobody's replay is one press
      from being posted as your score. Rewinding out of one and playing on forks
      a match that *is* yours — see system-design for why that stays open.
    - **The verifier binds the log to the setup** (`same_setup`), or an easy
      map's replay would back a hard map's score. It proves the *game*, never
      that a human played it — that is what `hand` discloses, recomputed from the
      log rather than trusted. Key the log by `GameLog.setup_key()`, never the
      live `Settings`: `main` resolves "roll a fresh seed" at game start and never
      writes it back.
  - **Adding a field to `Settings` invalidates every key already shared.**
    `challenge_key()` hashes the full setup dict, so a new field moves the digest
    of every map that ever existed and links from before it read as edited.
    `settings._LEGACY_KEY_DROPS` lists per schema change what that version
    lacked; `challenge_keys()` re-hashes without each and `Challenge.matches`
    (and `webstore.best`) accept any of them. Append an entry whenever a field
    joins `Settings` — `test_challenge_key_is_stable` pins the default digest and
    fails until you do. Only `challenge_keys()[0]` is ever *written*. The
    leaderboard folds by lookup instead (`KEY_ALIASES` in
    `leaderboard/js/token-decode.mjs`, `leaderboard/fold-game-key.sql`), since JS
    cannot recompute the Python digest — plus, for the splits nobody has reported
    yet, `submit.findTwin`, which posts onto whichever game row already stores
    this exact setup (`setupIdentity`, matched against `settings_json`) rather
    than opening a second page under the new digest. A split settles on the
    *newest* key — `findTwin` and `fold-game-key.sql` both move that way, and
    `game.mjs` forwards a link to a folded-away key through `aliasFor`. For the same reason, the
    leaderboard's same-setup-different-seed grouping (`sc_config_key` in
    `leaderboard/schema.sql`) is computed in SQL from stored `settings_json`
    rather than added as a field here — that would move `challenge_key()` for
    every map instead of only the config grouping.
- **`webstore` is the third browser bridge** (with `softkeyboard`, the web-only
  paths in `main`/`menu`, and `upload`, which is the one that also runs off the
  web): `get`/`set` are `localStorage` on the web and
  a JSON file under `data_dir()` elsewhere. The rest is genuinely web-only and
  no-ops off it: `link_url`, `set_url_fragment`, `copy_to_clipboard` and
  `url_token` are the primitives, and `sync_settings` / `share_token` /
  `copy_link` the compositions callers use. Same defensive style as
  `softkeyboard`: local `import platform`, every DOM call guarded, storage
  failure never load-bearing.
- **Quitting is a desktop concept; the web has nothing to exit to.** Every
  confirmed quit goes through `main.leave_app()`: off the web it returns True
  and the loop ends, while on the web it asks the browser to close the window
  (`webstore.close_window`) and returns False, falling back to the setup menu
  with `main.CANT_CLOSE_MSG` via `menu.set_status`. Never end the loop
  (`pygame.quit()`) directly on the web build.
- **The map viewport has two margins, both floored at `config.node_clearance()`.**
  `config.map_fit_padding()` sizes the zoom-1 fit and `config.map_pan_padding()`
  is what the pan clamp keeps past the outermost system once zoomed in —
  `geometry.WorldView` takes them as `padding` and `pan_padding`.
- **AI is per-seat and pluggable.** Each `Player` carries `ai_strategy` (a key
  into `ai.STRATEGIES`) and `ai_params` (`model.AiParams`, defaults mirroring
  the `config.AI_*` constants). `ai.compute_orders` reads the seat's params, so
  seats can play to different profiles; the menu's AI tab edits them per seat.
  `Settings` mirrors both per-seat lists (`ai: list[AiParams]`, `ai_strategy:
  list[str]`, indexed by seat-1), and `build_state` stamps each non-neutral
  `Player` with its `seat_strategy(...)` and a copy of its `seat_params(...)`.
  - Those fields are readable for *every* seat — see `models/knower.py` for
    what that makes possible. Any such bot must keep three rules: never call
    `ai.load_models()` from inside a model (it re-`exec_module`s every file,
    including yours, unguarded); read `ai.STRATEGIES` *lazily* inside `decide`,
    since files load in sorted order and the registry is incomplete at your
    import time; and draw **nothing** from `state.rng` (`tests/test_knower.py`
    asserts both of the latter).
  - **Attack margins are measured against the *effective* garrison.**
    `ai._frontier_order` multiplies a target's ships by
    `config.DEFENDER_ADVANTAGE` before applying `expand_margin`/`attack_margin`,
    because that is what a fleet actually has to out-fight
    (`combat._apply_advantage`). Against the raw count the AI stops expanding
    entirely at a high setting. Identity at the 1.0 default — see bot-design
    for why the knob's top end still turtles regardless.
  - **A bot prices a fight with `combat.edge_attacking()` /
    `edge_defending()`, never a constant.** They are the break-even multiples —
    what a fleet must beat the garrison by, and what a garrison must beat the
    incoming force by, to win the *worst* roll — read live off
    `config.COMBAT_JITTER` and `config.DEFENDER_ADVANTAGE`, both of which are
    menu sliders. They move in opposite directions, since the advantage belongs
    to whoever holds the system. Most `models/` bots' margins are pads/absolutes
    over those, with the edge's jitter half floored at each bot's `TUNED_SWING`
    (the swing it was fitted at), so a knob can only ever *raise* a margin above
    its measured figure — nothing in the roster moves at the 0.10/1.0 defaults.
    Clearing an edge is not a promise of capture: ties break to the defender and
    matched forces annihilate to neutral, hence the `target.ships + 1` floors.
    - **The exception, and it is a measured one: `marshal._enemy_margin` carries
      the advantage half of the edge and *none* of the jitter half.** 86.7% of
      out-matched garrisons evacuate rather than fight (only claudebot stands),
      so a premium against the dice is paid on a fight that mostly never
      happens; dropping it is the largest single gain measured on any bot in the
      roster. The advantage half is kept because it prices the ground rather
      than the dice and applies in full whenever a garrison *does* stand —
      dropping that too reads z = -12.35 at `DEFENDER_ADVANTAGE 1.5`. Marshal's
      *defence* margin still prices the jitter in full, which is the asymmetry:
      our own garrison cannot decline the engagement. See "Garrisons run away"
      in bot-design before copying either half into another bot.
  - **The game and the board find each other by hostname, not by configuration.**
    They are two Netlify sites whose names differ by `paths.LEADERBOARD_TAG`, and
    Netlify names every deploy `<context>--<site>.netlify.app` from the same
    context on both — so `paths.sibling_host` (via `webstore.leaderboard_origin`,
    and `siblingGame` in `leaderboard/js/config.mjs` for the reverse) makes a
    deploy preview of one talk to the deploy preview of the other, with no URL
    edited by hand. Endpoints are therefore built at *call* time from
    `LEADERBOARD_*_PATH`, never stored as whole URLs; `paths.LEADERBOARD_ORIGIN`
    is the fallback for a host the rule cannot read (desktop, a custom domain)
    and blanking it disables every leaderboard feature — which is what `render`
    tests, since resolving costs a DOM read it must not do once a frame.
  - **`AiParams.aux` is the one bot-defined knob.** The core never interprets it
    (only the AI tab's aux slider writes it); each strategy assigns its own
    meaning. `config.AI_AUX` is `1.0` and that is the documented "untuned" value,
    so a bot's default behaviour must be what it does at 1.0 — a stale token or
    save with no `aux` key deserialises to it. Add per-bot knobs here rather than
    growing `AiParams` one field per strategy. A strategy names its knob with
    module-level `AUX_LABEL` (+ optional `AUX_RANGE`, `AUX_INT`), read by
    `ai.aux_spec`; `menu._ai_specs` appends that slider to `_AI_PARAMS` for the
    edited seat, so a strategy declaring nothing (the built-in heuristic,
    `thinker`, …) shows no aux slider at all. `models/knower.py` labels it
    *Search depth*. An `AUX_INT` slider stores an **int**, and `_ai_from_dict`
    preserves that — `aux` is the one field whose int/float form survives a
    decode, since `challenge_key` hashes the JSON and `12` is not `12.0`. Widen
    it and every link carrying an int aux reads as edited the moment it opens.
    The board is the one place that cannot hold the distinction (jsonb drops the
    `.0`), so `verify_scores.same_setup` widens both sides through `_aux_widened`
    before hashing — drop that and every posted score with an aux reads as a
    different map.
  - **A predicting bot advertises itself** with `IS_ORACLE = True` and, when
    prediction is per-seat rather than per-module, `is_oracle_seat(player)` —
    which callers prefer over the flag (`knower.is_oracle_seat` is "depth ≥ 1", so
    its depth-0 seats are predicted for real, and trusted, instead of approximated).
  - **A seat commands its own ships and nothing else.** `apply_order` only
    checks the *declared* owner holds the source, so `_collect_orders` filters
    every seat's orders (including the human's, under autoplay) through
    `engine._own_orders`. Without it any drop-in bot could launch a rival's
    fleet, or the human's.
- **All randomness flows through `state.rng`** (a seeded `random.Random`). A
  seed fully reproduces a map *and* every battle. Never call the global `random`
  module in core code, and keep new map-gen / combat code deterministic given
  the seed (`test_mapgen.py` asserts this). The *unreproducible* rolls go
  through `settings.random_seed()` / `settings.fresh_rng()`, which mix the
  clock and a per-call counter into a throwaway RNG rather than using the
  global `random` (the web build boots from a fixed interpreter image, so
  `random`'s auto-seeding can hand out the same "random" seeds on every load).
- **Travel time is a query, not a stored value.** `Lane.travel_turns` (and
  `adjacency`) hold the mapgen-time figure; with `config.SHIP_SPEED_GROWTH_PCT`
  on, ships compound faster each turn, so always ask `state.travel_turns(a, b)` — it
  re-times through `config.travel_turns_at` for the *current* turn. Growth bites
  at launch only: a fleet in transit keeps its `turns_total`.
- **A fleet's lane track is stored, never ranked.** `Fleet.lane_slot` is which
  parallel line of the lane a fleet flies on (0 is the centre, then -1, +1, …),
  handed out at launch by `model.free_lane_slot` and held until the fleet leaves
  the board; `render` multiplies it by a spread and draws. Do not go back to
  deriving it from a fleet's rank among whoever is on the lane — that is what made
  every fleet in transit step sideways whenever a lane-mate launched *or* arrived.
  It is the one cosmetic field on a core dataclass, it is deliberately not on the
  wire to external bots (`botio`), and nothing in the rules reads it. Because it is
  stored, `turnfilm.Launched` must carry it: a film that invented its own tracks
  would pop every fleet sideways as it ended, which is why `lane_slot` is in the
  board digest both film oracles compare.
- **Everything is keyed by integer id.** Systems are `dict[int, System]`; lanes
  use a canonical order-independent `frozenset` key (`model.lane_key`). Neutral
  is a real player with `id == 0`.
- **Star names (`starnames.py`) are flavour on top of that, never a key.**
  `System.name` is a cosmetic IAU star name (`System.label` is `"Vega (7)"`,
  `System.short` the name alone); mapgen's `_name_systems` stamps one per system
  **last**, after every roll that shapes the map, so a seed still lays out the
  board it always did and a replay recreates the names from `state.rng` with
  nothing serialized. `NAMES` is generated from `tools/iau-star-names.csv` by
  `tools/gen_starnames.py` — regenerate, don't hand-edit. On the map,
  `render._draw_node_names` places labels collision-first and drops what doesn't
  fit (see system-design); ids stay on the mechanical readouts — the queued list,
  `tests/sim` logs, tokens.
- **The menu's Combat tab teaches the square law from the real code.**
  `combat.preview_fight` sits beside `resolve_fight` and shares its
  `_apply_advantage`/`_resolve_effective`/`_survivors` helpers, so the page
  cannot drift from the fight it predicts (pinned by a zero-jitter equivalence
  test). It takes `jitter`/`advantage` as **parameters and reads no `config`** —
  those only reach `config` at game start via `settings._apply_globals`, so
  reading them would preview the previous game's balance — and it **draws no
  rng**, keeping `menu.draw` a pure read. `best`/`worst` are the corners of the
  jitter square, not samples, so they really do bound the outcome.
  - **The demo sliders are the one group that writes `MenuState`, not
    `Settings`** — the third `kind` in `_SLIDER_SPECS`, routed in
    `_apply_slider`. A scratch calculation has no business in a save file or a
    share token, and on a challenge link it would raise the un-challenge modal.
    `_ADV_COMBAT` moved tab but *not* namespace: still `adv_`-keyed, still
    writing `Settings`, just drawn beside the demo it governs.
  - **This one page hand-breaks its prose instead of reflowing it.** The rule
    above exists because the *font* scales; the menu canvas is fixed, so a
    runtime wrap would instead make the page's height depend on its text and
    silently overflow the panel. `test_tab_content_stays_inside_the_panel`
    guards every tab's rects against that 560x496 box.
- **The send popup is the *only* ship-count editor.** Composing a new send opens
  it (`Ui.begin_send`), and so does reopening an already-queued order or
  standing rule — `Ui.edit_order` / `Ui.edit_forward` put the popup back into
  `CHOOSING` aimed at that subject. `Ui.editing_existing` records that the
  subject *predates* the popup, and drives the bottom button (Cancel vs
  "Delete order"/"Delete rule") and `_close_send`'s unwind to `IDLE` on edit.
  - **A dormant rule highlights but never opens it** (`Ui.rule_is_live`): the
    popup reads the source's garrison and destination unguarded, and aiming it
    at a system we no longer hold would let the Send tab queue an order out of
    enemy territory. Dormancy only lasts the turn — `Ui.prune_forward`
    (`main.resolve_turn`) deletes a rule whose source was taken, so it can never
    come silently back to life on recapture.
  - **The count slider must be claimed before the popup's drag fallthrough** —
    `slider_rect` is hit-tested first, and `dragging_slider` checked ahead of
    `dragging_popup` in the MOUSEMOTION chain. Both halves tolerate `lo == hi`
    (an empty source, the touch default) and a zeroed rect (popup closed
    mid-drag).
- **The side panel's queued list is capped and scrolled, not truncated.** It
  takes at most half the panel, and what doesn't fit is reached with
  `ui.order_scroll` (▲/▼ buttons, or the wheel while over the panel). Each
  drawn row carries **its own index** into `pending` (`ui.order_hitboxes` is
  `(index, row, delete)`) — a positional mapping would silently delete the
  wrong order once only a window of the list is on screen.
- **Losing makes the human a spectator, not a blind one.** `fog.observe`
  returns empty for a landless player, so `main._accumulate_fog` reveals the
  whole board (dropping frozen `player_intel`) once
  `GameState.is_defeated(human_id)` — fixing history mode and a resumed game
  for free since both fold fog through that one helper. Its companion is
  **fast forward** (`Ui.can_fast_forward`, `main.step_delay`, the F key /
  footer button): `main.FAST_FORWARD_MS` replaces the autoplay/play delay so
  the rest of a lost match resolves a turn per frame. Offered only while
  spectating.
- **`viewstate.Ui` holds all transient interaction state**, including human-only
  quality-of-life features (e.g. `auto_forward` standing rules) that must stay
  out of the pure `GameState`. `main.resolve_turn` expands such UI state into
  `Order`s at end-of-turn.
- **Route mode (`viewstate.ROUTING`) is the one control that doesn't commit as
  you go.** It builds a *proposal* — `route_sel` (plus `route_dest` in chain
  mode), recomputed by `Ui.recompute_route` into `route_plan` — which
  `confirm_route` writes into `auto_forward` in one go.
  `input._handle_route_event` takes the whole event stream (placed after the
  game-over branch), and render swaps the footer strip and the End Turn button,
  so nothing from live play stays clickable under an open plan.
  - **Two sub-modes, one plan** (`Ui.route_rally`, the footer's Mode button and
    Tab). Both seed the same `model.flow_field` search (the one
    `ai._flow_to_frontier` also delegates to) over our own territory and share
    `_add_hop`, `_detect_route_cycles` and the confirm, so they differ *only* in
    the seeding: **chain** seeds the one destination and `_plan_chain` walks each
    selected system's path to it; **rally** seeds every pick at once and
    `_plan_rally` takes the returned field whole, so every owned system it
    reaches forwards toward its nearest rally point. Both measure "nearest" in
    **travel turns**, not hops (`flow_field(by_turns=True)` / `flow_costs`) — the
    same `state.travel_turns` rule the rest of the game follows. The AI keeps the
    unweighted default, which is why the flag exists rather than a changed
    default. Rally splits a genuine tie toward whichever point is drawing less,
    measured in ships/turn (`1 / production`, as `fog.player_totals` reports),
    assigning nearest-first so each node's real destination is already known.
    `Ui.auto_rally` (rally's Auto-route button, `T`) picks every
    `threatened_systems` — the shell's own local copy of the AI's threat maths,
    per the render/input rule against importing `ai`. Every hop of every path gets
    a rule, not just the selected systems. Owned-only is *forced*, not chosen: a
    rule can only live on a system we hold, so a path through enemy space cannot
    be expressed. The **sinks** are exempt (`flow_field` seeds need not be in
    `allowed`), which is what lets either sub-mode be aimed at an enemy front.
    The sub-mode is a preference, so `reset_route` leaves it alone while clearing
    everything else; `set_route_rally` drops the proposal, since `route_sel`
    means sources in one and sinks in the other.
  - **A drag boxes a group; a tap always aims** (`Ui.route_tap`, chain mode).
    Aiming is never destructive — the destination stays in `route_sel` and is
    merely skipped as a source (`Ui.route_sources`), so re-aiming hands it
    straight back. Removing is the *second* tap on whatever you are already
    pointing at. Never give a tap a second primary meaning conditional on the
    system: that is what made aiming at one of your own picks silently drop it.
    A rally tap is a plain membership toggle, which is one meaning rather than
    two, and frees drag for panning.
- **`tests/sim.py` is both a demo harness and a test fixture.** Because it drives
  the pure core headlessly, the suite uses it to assert games actually terminate
  and never corrupt state (`check_invariants`). After changing `ai.py` or
  travel/combat balance, run a `--trials` batch and watch the timeout rate.
  `--film` adds the turn-playback oracle to every turn of every game (see Animated
  end of turn).
  It also hosts the two bot tournaments, sharing `_tally`/`_avg_turns`: `--swap`
  is a free-for-all (whole roster in one game, rotated through every seat via
  the cyclic `_rotations`), `--ladder` is a pairwise round-robin (`run_ladder`:
  every pair, both seatings, plus a head-to-head grid). `--external` adds the
  `bots/` subprocess bots to either (see the external-bot rules above). Both default their
  roster to `ai.available_strategies()`, so a whole-`models/` ranking needs no
  arguments. `play_settings` is the third entry point — one bot through the
  human's seat on a stored `Settings`, going through `settings.build_state` so a
  posted setup's tuned knobs actually apply. It is what the leaderboard's bot
  column is made of (`tools/bot_replay.py`, under Key conventions).
  - **`play_from` is the fourth, and the only one that starts anywhere but the
    opening.** It branches a recorded match at turn N (`reconstruct` on a
    *truncated copy* — never the caller's log) and hands the seat to a bot, so a
    stored game yields a position every few turns instead of one number. The
    baseline comes free: we know what the person who was there then took.
    `sim.positions` picks the turns and `tools/position_suite.py` drives it over
    a corpus. Read its three numbers separately — *faster* is the only paired
    comparison, *recovered* (games the person lost, which no score can carry) has
    no baseline at all, and both are a direction rather than a verdict, since the
    sample is whatever games happen to exist. See bot-design.
  - **Sweep the speed and node knobs, not just their defaults.** `WORLD_SIZE` is
    fixed, so a lane's length in light-years rises as the node count falls, and
    `config.SHIP_LY_PER_TURN` (menu slider, 1-30) rescales every lane on top —
    lanes run 14-36 turns at 12 nodes and 1 ly/turn, and nearly all of them are
    a single turn from 18 ly/turn up. Any margin keyed off travel distance is therefore live in part of
    that space and unreachable in the rest, so a batch at the default 6 ly/turn
    measures one regime out of three and a knob can look like dead code purely
    because of where it was measured. See bot-design.

Map generation (`mapgen.py`) has two modes: `random` (jittered-grid placement +
light relaxation + a Euclidean MST for connectivity, which is planar so edges
don't cross, plus a few crossing-rejected extra edges for loops) and `symmetric`
(one base sector rotated N times about a shared contested centre for a perfectly
fair start). Both must stay connected and planar-ish — `test_mapgen.py` guards
both.
