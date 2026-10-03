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

**Play it in the browser:** <https://star-conquest.netlify.app/game/>. The
leaderboard is at <https://star-conquest.netlify.app/board/>.

## Run it

```sh
uv run python main.py                          # opens the setup menu
uv run python main.py --players 4 --mode symmetric   # CLI args pre-fill the menu
uv run python main.py --mode symmetric --layout ring # ...sectors joined by borders, no hub
uv run python main.py --seed 42 --nodes 24     # pre-fill a reproducible map
uv run python main.py --no-menu --autoplay     # skip the menu; AI plays every seat
uv run python main.py --watch MATCH_ID         # open a posted replay in history review
uv run python main.py --match MATCH:TOKEN      # open your seat in a play-by-post match
```

Launch drops you into a **setup menu** with four tabs:

- **Basic**: players, systems, map type and layout, seed, fog of war, autoplay.
- **Combat**: the square law explained, with a live demo fight beside the two
  combat knobs.
- **Advanced**: map spread, economy, ship speed, in-lane battles and the fog
  ranges.
- **AI**: a **Strategy** dropdown per seat, plus per-seat tuning with
  copy-to-all and reset-all. `random` leaves the bot to the seed and reveals it
  when the game ends.

CLI flags pre-fill the menu, and `--no-menu` starts a game straight from them.
**Create map** opens an editor for drawing a board by hand. **Get Link** copies
the whole setup as a URL. **Save**/**Load** write it to a named `.json` file in a
gitignored `saves/` folder. **Play by post** starts a shared match (see below).

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

| Action | Input |
|---|---|
| Send ships | click one of your systems, then a neighbour (or drag between them) |
| Set the count | the popup's slider, −/+, Half/All, or the mouse wheel |
| Standing forward rule | the popup's **Forward** tab (or Shift-click the neighbour): keep N ships and send the rest down that lane every turn |
| Re-edit a queued fleet or rule | click its arrow, or its row in the side panel |
| Delete the selected order or rule | the popup's Delete button, **Clear**, or `X` / `Backspace` |
| Route many systems at once | **Route** (`G`): box or tap systems, then pick a destination. `Tab` switches to **Rally**: everything flows to the nearest rally point. `Enter` confirms |
| Cancel / back | right-click or `Esc` |
| End the turn | **End Turn**, `Enter` or `Space` |
| Play / pause turns on a timer | **Play** (`P`) |
| Let the AI play your seat | **Autoplay** / **Take control** (`A`) |
| Review past turns | **History** (`H`): scrub with `←`/`→`/`Home`/`End`, `P` to replay, or rewind and play on from any turn |
| Zoom / pan | mouse wheel or the on-map −/+; drag empty space to pan; `R` resets the view |
| Fast forward once you're knocked out | **Fast forward** (`F`) |
| New map / back to the menu | `N` / `M` |

When the game ends: `T` retries the same map, `C` copies a challenge link, `L`
posts to the leaderboard, and `H` reviews the whole game.

Your queued fleets show as arrows and standing rules as chevrons along their
lanes. A system's number is the ships you can still send this turn. The top bar
scores each player by systems, ships and production. Each End Turn plays back as
a short animation, which any key or click skips.

On a phone, tap a system then a neighbour (or drag), pinch or drag to move the
map, and use the on-screen buttons in place of the keys.

## Sharing, the leaderboard and play-by-post

The game and the leaderboard are one site. Nothing is sent from the game until
you press a sharing button, or tick **Share replays**.

- **Challenge links.** Win a game, press **Challenge a friend**, and the link
  carries the map plus your score to beat: turns to win, with ties going to
  fewest ships lost. **Post to leaderboard** puts it on the board, where every
  map gets its own high-score table. A plain **Get Link** shares a setup with no
  score attached.
- **Checked scores.** A posted score uploads its replay. An offline job
  (`tools/verify_scores.py`) replays the log and marks each score as verified
  or not, and the board's **Watch** link plays it back.
- **The bot column.** On every posted map, each `models/` bot is replayed
  offline in the human's seat (`tools/bot_replay.py`, on a schedule), so you can
  see how the roster did there and watch its wins.
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

The code is split into a pure, pygame-free simulation core and a thin
presentation shell, so the whole game is testable headlessly. Contributor rules
are in [`CLAUDE.md`](CLAUDE.md), and the design notes behind them start at
[`docs/README.md`](docs/README.md).

```
starconquest/
  # core: imports no pygame
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
  pbp.py         # play-by-post client
  campaign.py    # is this setup one of the week's campaign nodes?
  matchnames.py  # readable play-by-post match names
  starnames.py   # IAU star names (generated by tools/gen_starnames.py)
  # shell: pygame
  render.py      # drawing; never mutates state
  input.py       # event handling
  menu.py        # pre-game setup screen
  mapmaker.py    # the hand-drawn map editor
  widgets.py     # measured-layout UI kit
  main.py        # the pygame loop: menu/game/editor scene wiring
  # pure helpers for the shell
  viewstate.py   # transient in-game UI state
  share.py       # uploads/downloads replays to and from the leaderboard
  webstore.py    # browser key/value store, address bar, clipboard
  softkeyboard.py, paths.py, uifont.py, assets/
models/          # bundled Python bots, loaded as strategies
bots/            # example non-Python bots (subprocesses speaking JSON)
leaderboard/     # the board: static pages, Netlify functions, Supabase schema
tools/           # web build, bot checker, score verifier, bot replay, admin
tests/           # pytest suite + sim.py, the headless AI-vs-AI harness and ladder
main.py          # launcher shim (pygbag needs it at the root)
```

```sh
uv run pytest                                    # full test suite
node --test leaderboard/tests/*.test.mjs         # the leaderboard's JS suite
uv run python -m tests.sim --seed 1 --verbose    # watch one AI-vs-AI game
uv run python -m tests.sim --trials 200          # batch stats (winners, length, timeouts)
uv run python -m tests.sim --ladder --trials 50  # rank every models/ bot pairwise
```

## Web (browser)

The same source runs in the browser via [pygbag] (pygame → WebAssembly). The pure
core is untouched. The pygame shell adapts for touch with a DPI/scale layer, a
bundled font, tap/drag input and an async main loop (see
[`paths.py`](starconquest/paths.py) `is_web()`).

```sh
./tools/build_web.sh                       # -> ./web/ (game at /game/, board at /board/)
cd web && python3 -m http.server 8000 --bind 0.0.0.0   # test at http://localhost:8000
```

[`tools/build_web.sh`](tools/build_web.sh) mirrors the pygame-ce WASM wheel into
the build, so there is no runtime CDN dependency. The deployed site is built by
the root [`netlify.toml`](netlify.toml), which also bundles the leaderboard's
functions under `/api/`. The game runs fine on a plain static host too, with the
leaderboard features unavailable.

[pygbag]: https://pygame-web.github.io/

## License

[MIT](LICENSE). The bundled assets keep their own, both redistributable: the
DejaVu Sans Mono faces under
[`starconquest/assets/`](starconquest/assets/DejaVuSansMono-LICENSE.txt) and
Press Start 2P under the [SIL Open Font License](leaderboard/fonts/OFL.txt).
pygame-ce, fetched unmodified by [`tools/build_web.sh`](tools/build_web.sh) into
the web build, is LGPL.
