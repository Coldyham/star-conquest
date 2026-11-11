# Star Conquest

A minimalist sci-fi turn-based strategy game. Map conquest boiled down to its
bare minimum: a graph star map where **systems are nodes** and **spacelanes are
edges**. One ship type. Take every system to win.

- **Systems** have a *production* rate (turns-per-ship — lower is richer, drawn
  bigger) and a garrison of ships.
- **Spacelanes** have a length in light-years that becomes a travel time in
  turns (shown on each edge). Fleets in transit pass through each other freely,
  and combat happens only when a fleet reaches a system (unless the optional
  in-lane battles are switched on).
- **Combat** is Lanchester's square law with a slight random swing, so
  concentrating your fleet wins decisively (10 vs 6 leaves ~8, not 4).
- **Turns resolve simultaneously**: every player commits orders against the same
  board, then all orders execute together — no turn-order advantage.
- **Maps** are procedurally generated, either **random** (planar, evenly spread,
  peripheral starts so no seat is boxed in) or **symmetric** (rotationally
  identical sectors for a perfectly fair start), whose sectors meet through a
  shared **hub**, around a **ring** of borders, both (**wheel**), or through a
  **core** of prizes each shared by two neighbours. You can also draw a map by
  hand in the built-in editor.
- **Fog of war** is optional, with adjustable sight ranges.

**Play it in the browser:** <https://star-conquest.netlify.app/play/>. The
leaderboard is at <https://star-conquest.netlify.app/board/>.

## Run it

The game runs in the browser: Python in [Pyodide](https://pyodide.org/), in a
web worker behind an HTML page. `main.py` serves it on this machine and opens it.

```sh
uv run python main.py                          # serves 127.0.0.1:8000/play/ and opens the setup menu
uv run python main.py --players 4 --mode symmetric   # flags pre-fill the menu
uv run python main.py --seed 42 --nodes 24     # pre-fill a reproducible map
uv run python main.py --no-menu --autoplay     # skip the menu; AI plays every seat
uv run python main.py --no-open                # serve without opening a browser
```

The flags become the page's query (`/play/?players=4&seed=42`, `&go` to skip
the menu), so a bookmark does the same. A saved match or setup opens from the
menu's **Files** list, and a shared link (a replay, a play-by-post seat, a
challenge) opens wherever the page is served.

Launch drops you into a **setup menu** with four tabs:

- **Basic**: players, systems, map type and layout, seed, fog of war, autoplay.
- **Combat**: the square law explained, with a live demo fight beside the two
  combat knobs and the in-lane battles switch.
- **Advanced**: map spread, economy, ship speed and the fog ranges.
- **AI**: a **Strategy** dropdown per seat, plus per-seat tuning with
  copy-to-all and reset-all. `random` leaves the bot to the seed and reveals it
  when the game ends.

**Tutorial** (or **Gauntlets**, once there is more than one) plays a string of
pinned stages, each with a briefing. **Daily** opens the day's puzzle or map
(see below). **Create map** opens an editor for drawing a board by hand. **Get
Link** copies the whole setup as a URL. **Files** saves the setup or the last
match as a `.json` file and opens one again. **Play by post** starts a shared
match, and **Recently played** and **Shared matches** open the board's lists
(see below).

### Custom AIs

Drop a Python file defining `decide(state, pid) -> list[Order]` into the
`models/` folder and it becomes a strategy you can assign to any seat from the AI
tab's dropdown. The built-in `heuristic` is the default. The folder is
committed, not gitignored. It holds the bundled bots (`knower`, `marshal`,
`thinker` and others), it ships with the web build, and every bot in it is
ranked in the leaderboard's bot column. See
[`models/README.md`](models/README.md) for the authoring contract, the read-only
`GameState` API a bot can use, and a copy-paste example.

Not a Python programmer? A bot can be any program that reads a JSON board and
writes JSON orders: see [`bots/README.md`](bots/README.md) and the protocol in
[`docs/bot-api.md`](docs/bot-api.md). These bots compete in the ladder
(`tests.sim --external`) but can't ship in the browser build, which cannot start
a child process. Not a programmer at all?
[`docs/bot-brief.md`](docs/bot-brief.md) is a page you paste into an AI
assistant, which then interviews you and writes the bot.
`uv run python tools/check_bot.py <name>` validates whatever comes back.

## Controls

Every control is a button, and a button shows its key where there is a
keyboard, so the game explains itself as you go. What each screen offers:

- **Setup menu.** Set up a game across the tabs, then start it, or go to the
  tutorial, the daily challenges, the map editor, play-by-post, files and links to the leaderboard.
- **Map editor.** Use the tabs to switch between placing systems, drawing lanes and seating players, then play the
  map, keep it as a file, or return to the menu to tweak further.
- **Playing.** Pick one of your systems, then a neighbour, to send ships (or
  drag between them). The popup sets how many, or switches to **Forward**: a
  standing rule that keeps some ships and sends the rest down that lane every
  turn. Pick a queued arrow to change or delete it. **End Turn** resolves
  everyone's orders at once. The footer reaches the rest: **Route** many
  systems at once (to rally points, or **Chain**ed to one destination), a
  **Hint** from a bot (a hinted game can't be posted), **Undo**, **History**,
  **Play** to run turns on a timer, **Autoplay** to let a bot take your seat,
  and **Retry**, **New map** and **Menu**. While Play or Autoplay runs, End
  Turn (the default action button) becomes **Pause** or **Take control**.
- **History.** Scrub through every turn, play them back, rewind and play on from
  any of them, and open the graphs. Once the game is decided graphs include
  coach's notes and detail from behind the fog.
- **Game end.** Retry, a new map (same setup, new seed) or a gauntlet's next stage, the menu,
  history, a challenge link (or your daily result line, on a daily) and post to the
  leaderboard.

Your queued fleets show as arrows and standing rules as chevrons along their
lanes. A system's number is the ships you can still send this turn. The top bar
scores each player by systems, ships and production. Each End Turn plays back as
a short animation, which any key or click skips. On a phone the footer folds
behind **more**, and the map pans with a drag and zooms with a pinch or the
on-map −/+.

**Shortcuts the screen doesn't show:**

| Where | Input | Does |
|---|---|---|
| Playing | `Space` | the same as `Enter` (End Turn, or the default action button) |
| Playing | `Shift` + click or drag to a neighbour | opens the popup on **Forward** |
| Playing | mouse wheel with the popup open | steps the ship count |
| Playing | right-click or `Esc` | cancels the send or selection; `Esc` with nothing selected opens the menu |
| Playing | `Backspace` / `Delete` | the same as **Clear** (`X`) |
| Playing | `Shift`+`K` | queues the hint's sends (the hint caption says so, the ✓ button does it) |
| Playing | `I` | the selected or hovered star's name and lore |
| A gauntlet stage | `B` | reopens the stage's briefing |
| History | `←` / `→`, `Home` / `End` | step a turn, or jump to the first or last |
| History graphs | `Tab` / `Shift`+`Tab` | the next or previous chart (and the coach) |
| History graphs | `1`–`6` | show or hide a seat's line, in legend order |
| History graphs | `F` | full screen or docked |
| History, coach | `↑` / `↓` | step through the coach's notes |
| Map editor | `Ctrl`+`Z` / `Ctrl`+`Y` | undo / redo |
| Map editor | `Delete` / `Backspace` | delete the selected system or lane |
| Map editor | `Enter` / `Esc` | play the map / back to the menu |

## Sharing, the leaderboard and play-by-post

The game and the leaderboard are one site. Nothing is sent from the game until
you press a sharing button, or tick **Share replays**.

- **Challenge links.** Win a game, press **Challenge a friend**, and the link
  carries the map plus your score to beat: turns to win, with ties going to
  fewest ships lost. **Post on leaderboard** posts it to the board, where every
  map gets its own high-score table. In the browser, once you've posted under a
  name, the button becomes **Post as <name>**: it posts in one press, without
  opening the board, and tells you how many scores the map has and who holds
  the best. A plain **Get Link** shares a setup with no score attached.
- **Checked scores.** A posted score uploads its replay. An offline job
  (`tools/verify_scores.py`) replays the log and marks each score as verified
  or not, and the board's **Watch** link plays it back.
- **The bot column.** On every posted map, each `models/` bot is replayed
  offline in the human's seat (`tools/bot_replay.py`, on a schedule), so you can
  see how the roster did there and watch its wins.
- **Daily challenges.** Every UTC day has a fresh map, the same for everyone, and a
  puzzle: a small duel where the challenge is winning in the fewest turns. Each
  has its own standings, streaks and badges. Meet the puzzle's par (the best
  line an offline search found) and the end screen says so.
- **Claimed names.** An account (a private login and password) is optional. It
  lets you claim a name so only you can post under it, and a signed-in player's
  claimed name goes on the **Post as** button.
- **Crowns and the campaign.** A *crown* is a record you hold on a map at least
  two players have scored on. The weekly contest counts who stole the most
  records. The *campaign* is a fresh meta-map of challenges every Monday, to
  take and hold for the week.
- **Play-by-post.** A shared match where each seat has its own URL and plays at
  its own pace. A turn resolves once every seat has submitted. Public matches
  with open seats are listed in the lobby at `/board/pbp.html`. A seat that
  misses two deadlines in a row is handed to its bot. Play-by-post matches never
  appear on the leaderboard.

The board itself (pages, schema, Netlify functions, setup and moderation) is
documented in [`leaderboard/README.md`](leaderboard/README.md).

## Development

The game is pure Python with no display of its own, run in the browser by a
thin HTML page, so the whole game is testable headlessly. Contributor rules
are in [`CLAUDE.md`](CLAUDE.md), and the design notes behind them start at
[`docs/README.md`](docs/README.md).

```
starconquest/
  config.py      # every balance/aesthetic constant
  settings.py    # pure pre-game config (Settings) -> build_state; share-link tokens
  model.py       # dataclasses (GameState, System, Lane, Fleet, Order, Player, AiParams)
  geometry.py    # distance, segment-crossing, world->screen transform
  mapgen.py      # random, symmetric and hand-drawn map generation
  custommap.py   # a hand-drawn map as a serializable recipe
  combat.py      # Lanchester-with-jitter resolution
  engine.py      # simultaneous turn resolution
  ai.py          # heuristic AI + strategy registry (decide/register/load_models)
  botio.py       # JSON wire format for external (any-language) bots
  fog.py         # fog-of-war visibility (presentation only)
  replay.py      # game logs: every match saved and replayable from its orders
  turnfilm.py    # the end-of-turn animation, played back from a resolved turn
  pbp.py, pbpclient.py     # play-by-post: the shared match and its calls
  campaign.py    # is this setup one of the week's campaign nodes?
  daily.py       # the daily map and puzzle, from the game's side
  gauntlet.py, run.py      # gauntlets (the tutorial is one) and a run through one
  dealer.py, gauntletgen.py  # dealing gauntlet stages from a spec
  stats.py, coach.py       # end-of-game graphs and the post-game coach
  files.py       # setups, matches and maps the player keeps as files
  matchnames.py  # readable play-by-post match names
  starnames.py   # IAU star names (generated by tools/gen_starnames.py)
  starlore.py    # each name's language, origin and reference (generated likewise)
  # what the page shows and what a press means, read by the page
  session.py     # the worker's entry point: JSON in, JSON out
  app.py         # starting, resolving and reviewing a match
  play.py, hud.py          # the play screen's presses and words
  setupmenu.py, maker.py   # the setup menu's and the map editor's
  viewstate.py   # transient in-game UI state
  serial.py      # the view as JSON
  hostio.py      # the page's I/O, queued as effects (Python does none itself)
  share.py       # uploads/downloads replays to and from the leaderboard
  webstore.py    # key/value store, address bar, clipboard (through hostio)
  serve.py       # the local server main.py starts
  paths.py, assets/
webapp/          # the page: HTML, CSS, and the JS that draws the board and runs the worker
models/          # bundled Python bots, loaded as strategies
bots/            # example non-Python bots (subprocesses speaking JSON)
leaderboard/     # the board: static pages, Netlify functions, Supabase schema
tools/           # web build, bot checker, score verifier, bot replay, admin
tests/           # pytest suite + sim.py, the headless AI-vs-AI harness and ladder
main.py          # launcher: serves the game and opens it
```

```sh
uv run pytest                                    # full test suite
node --test leaderboard/tests/*.test.mjs         # the leaderboard's JS suite
node --test webapp/tests/*.test.mjs              # the page's JS suite
uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --trials 200          # batch stats (winners, length, timeouts)
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
```

## Web (browser)

```sh
./tools/build_web.sh                       # -> ./web/ (game at /play/, board at /board/)
cd web && python3 -m http.server 8000 --bind 0.0.0.0   # test at http://localhost:8000
```

[`tools/build_web.sh`](tools/build_web.sh) stages the page, a pinned Pyodide
(downloaded and checksum-checked) and the game's Python as one hashed zip into
the build, so there is no runtime CDN dependency. The deployed site is built by
the root [`netlify.toml`](netlify.toml), which also bundles the leaderboard's
functions under `/api/`. The game runs fine on a plain static host too, with the
leaderboard features unavailable.

## License

[MIT](LICENSE). The bundled assets keep their own, both redistributable: the
DejaVu Sans Mono faces under
[`starconquest/assets/`](starconquest/assets/DejaVuSansMono-LICENSE.txt) and
Press Start 2P under the [SIL Open Font License](leaderboard/fonts/OFL.txt).
Pyodide, fetched unmodified by [`tools/build_web.sh`](tools/build_web.sh) into
the web build, is MPL-2.0.
