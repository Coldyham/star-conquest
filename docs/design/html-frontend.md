# The HTML frontend (in progress)

The pygame shell (`render`, `menu`, `main`'s loop, `mapmaker`, `input`,
`widgets`, `softkeyboard`) is being replaced by one HTML frontend: SVG for the
map, DOM and CSS for panels and menus, vanilla ES modules with no build step,
like `leaderboard/js/`. The Python core stays Python and runs in Pyodide in a
Web Worker. Locally, `main.py` will serve the same bundle on `localhost:8000`
and open a browser.

## Why

The web build is pygame too: pygbag runs CPython and pygame in WASM. So
dropping only the desktop GUI would remove about 40 `is_web()` branches and
little else. Most of the shell is laid out, measured and wrapped by hand, and
it has a phone layout of its own (`shell.md`, "Phones: the compact layout").
A browser does that with CSS and native inputs. Python stays for the core
because stored replays depend on `random.Random`'s stream and every bot and
tool is Python.

## How it is staged

The pygame build stays live at `/play/` until the new one reaches parity at
`/next/`. The order:

1. The parity baselines below, taken from the untouched pygame build.
2. Python seams: the pygame-free logic out of `main.py` into an `app` module,
   with a JSON session the worker exposes.
3. A Pyodide spike, gated on startup time, per-turn bot time and identical
   replay digests.
4. Screen by screen to parity, then cutover.

Python does no I/O in the new build. It returns effects (fetch, storage,
clipboard), and the page carries them out. pygame gets bug fixes only once
work on the play screen starts.

## Parity checks

Two checks hold the new build to the old one.

**Replays reproduce exactly.**
- `tools/parity_replays.py` records 14 seeded all-bot games into
  `tests/fixtures/parity_replays.jsonl.gz`. They cover every ladder bot and
  stop, the symmetric layouts, lane battles, off-default balance and a custom
  map, all played with the wall-clock guards lifted
  (`ai.set_budget_scale(math.inf)`).
- The same file holds 10 stored `games/` logs at the current `RULES_VERSION`.
- Each case keeps its board digest after every turn.
- `tests/test_parity_replays.py` checks the native build against the fixture
  two ways:
  - every log reconstructs to the recorded boards;
  - every bot game, played again from its settings alone, makes the same
    boards, with the bots deciding afresh.
- The Pyodide build must pass the same two checks. The second is the one a
  stored replay can't make, because a replay never asks a bot to decide again.

**Every screen looks the same.**
- `tools/parity_shots.py` draws 42 screens of the pygame shell headlessly
  (98 images) as the web build's variant, at a desktop size and on a phone
  held both ways. The screens are each menu tab and list, the play screen
  with its popups, forwarding, route mode, the hint and the odds card, fog,
  game over, history and its graphs, the coach, the confirm modals, a gauntlet
  stage, the play-by-post overlays, and the map maker.
- Each screen is a recipe: settings, a seed, then steps (`Ui` calls, field
  sets). The resolved recipes are committed as
  `tests/fixtures/parity_screens.json`, and `tests/test_parity_screens.py`
  keeps them in step with the tool.
- The HTML build replays the same steps through its session and shoots
  `results/parity/next/` (`tools/parity_next.py`). Both sides build a recipe
  with `starconquest.recipe`; the session's `parity.load` is the page's way in.
  `tools/parity_sheet.py` then sets each pair side by side and flags a screen
  missing from either side.
- Pygame and HTML pixels will never match, so this is a review by eye plus a
  check that every screen exists in both builds. It is not a pixel diff.

The images live under `results/parity/baseline/` (gitignored, about 12 MB).
Drawing is deterministic: two full runs come out byte-identical, with the clock
pinned and preferences read from an empty store. So any commit before pygame
is deleted can redraw them exactly. The first baseline was drawn at the commit
that added these tools.

## The seams (phase A)

- `app`: `main`'s pygame-free logic, moved verbatim. The nine functions that
  wrote into `MenuState` answer an `app.MenuNote` instead, and `main` applies it.
- `pbpclient.PbpClient`: the play-by-post machine. `step()` returns an
  `Outcome` for the frontend to apply.
- `hostio`: the worker host. It keeps the page's address and a storage mirror,
  queues effects (`http`, `fire`, `bridge`, `store.set`, `clipboard`,
  `navigate`, `fragment`), and takes replies.
- `serial`: JSON shapes for the map, the board, fog, events and films. Board
  fleets keep `state.fleets` order.
- `webapp/js/reel.mjs` mirrors `turnfilm.Reel`; `webapp/tests/reel.test.mjs`
  holds it to the history-path rule.
- `Ui`'s three pixel-reading methods each gained a pure half (`snap_points`,
  `set_slider_fraction`, `add_route_sids`).

Every step left the full suite passing and the 98 parity screens
byte-identical.

## The Pyodide spike (phase 0)

`uv run python -m starconquest.serve` served `/next/` locally (now
`/next/spike.html`; `?play=1` autoplays): an all-bot game
on an SVG board, each turn's film played by the JS `Reel` and checked against
the board Python returns. `tools/build_web.sh` stages the same tree at
`web/next/` for deploy previews, never failing the build over it. Pyodide
0.27.7 (CPython 3.12.7) is self-hosted, downloaded by `tools/fetch_pyodide.py`
and checked against its published SHA-256.

**The gate, measured 2026-11-09** (`tools/spike_bench.py`,
`tools/spike_pygbag.py`; ledgers and `report.md` in `results/next-spike/`):

- **Replays: pass.** In Pyodide all 24 corpus games reconstruct, and the 14 bot
  games replay, to identical boards: 2,326 boards, none different. Every timing
  case also ended on the same board natively, in Pyodide under Node, in Pyodide
  in Chrome, and in pygbag in Chrome.
- **Bot time: pass.** Pyodide's decide time in Chrome is a median 1.03× pygbag's
  (range 0.98–1.05×) over the cases above 0.5 ms. Both are about 2.1× native.
  The costliest case is knower at Council: 57 ms a turn at 40 systems.
- **Startup: pass.** In headless Chrome, served locally, Python is ready 1.21 s
  after navigation cold and 0.98 s warm. pygbag reached a bench script's
  `main` at 1.58 s cold, before the real game would import its shell, and
  1.23–1.52 s warm.
- **Download: about half.** 6.0 MB gzipped (Pyodide 5.6 MB plus the 227 KB game
  zip), against pygbag's 10.6 MB runtime plus its 427 KB archive.

**Not measured:** a real phone (the phone screenshot is an emulated viewport on
desktop CPU), and download time over a real network. Both sides were served
from localhost.

**Caveat:** the service worker (scope `/`) caches `/next/` like any other path.
That is safe, because the zip's name is its hash, but `/next/` gets its own
rules in phase 7.

## The play screen (phase 1)

`/next/` plays a game: `webapp/index.html` and `css/play.css`, laid out as
pygame's desktop screen, driven by `js/play.mjs`. It opens on the setup menu
(phase 2, below). `window.scNext` exposes the page's state for headless runs
(`tools/chrome.mjs --eval`).

**Who decides what.** Python decides every rule and writes every word; the
page hit-tests, draws, and keeps what is only about looking.

- `play` holds what a press means, moved verbatim out of `input`: a tap on a
  system, a press near lanes (the orders and rules in reach, nearest first;
  a repeat press cycles), a drag's release, the clear and cancel presses, and
  every live-play key by its `KeyboardEvent.key` name. `input` hit-tests in
  pygame's pixels and hands over; the page does the same through the session.
- `hud` holds the words: the side panel's rows, the scoreboard's entries at
  each level of detail and its tooltip, the popup's caption, presets, discard
  button and forecast (longest wording first, for the page to fit), the
  legends, and the footer's buttons with their keys, colours and squeeze
  order. Colours go as roles (`text`, `dim`, a seat id…). `render` reads the
  same functions, so the parity screens stayed byte-identical.
- `serial.view` is what the page draws from `Ui`. The session answers it from
  `game.start` and `turn.end` and from the press ops: `ui.tap`, `ui.lane`,
  `ui.empty`, `ui.drag`, `ui.hover`, `ui.key` (which answers an action for
  the page: `end_turn`, `reset_view`, `menu`…) and `ui.call`, which reaches
  only the `Ui` methods in `session.UI_CALLS`.
- The page keeps the camera (`js/camera.mjs`, `WorldView` with the free
  clamp), the hover and drag between presses, and the film's clock. During a
  film it draws the union of what was visible before and after.

Sizes are screen pixels at any zoom, as in `render`: the board draws through
the camera, and only positions move.

**Done, desktop:** the map with fog and its rings, the camera (resting on
what has been seen, as `Ui.reset_view`), queued orders, the send popup, the
Forward tab with holds and splits, forwarding rules on the map, route mode,
the side panel, the scoreboard and its tooltip, the footer, End Turn and the
film with its marks, star names that yield, a drag to send, autoplay, play,
fast forward, undo, retry, new map, game over, the hint and its modal, the
lore and odds cards, history (scrubber, playback, held marks, rewind), and
its graphs and coach. Every phase-1 parity recipe shoots at the desktop size
(`tools/parity_next.py`, 22 screens).

**Phones.** The page sizes itself as pygame's shell does (`hud.fitLayout`
after `main.ui_scale_for` and `config.apply_ui_scale`): on a touch screen one
baseline unit is up to a CSS pixel, less where that would leave under 600 x 480
units, applied as CSS `zoom` on the app; a touch screen under 1100 x 720 units
is compact, and compact held upright is portrait. The stylesheet lays out by
the `touch`, `compact` and `portrait` classes this sets, and the board and the
graphs work in layout units, dividing a pointer's screen position by the zoom.
Compact draws systems at 0.8, docks the popup (in the side column sideways, a
sheet across the foot upright) with its lane row and Done, folds the footer's
ways out of the game behind the "more" list from the start, and upright puts
the panel in a band of fixed height under the map (as pygame's
`config.PORTRAIT_PANEL_H`, so the map never moves as it fills; it scrolls past
its room) (the system on the left, its lane and rule
on the right), End Turn across the foot, the scrubber in two rows and the
graphs' legend under the plot. Every phase-1 recipe shoots at all three sizes.

**Differences kept on purpose:** a recipe loaded mid-game frames what has
been seen by then, where pygame's camera was framed at turn 0 and never moved;
the page fits names, the footer and the forecast with its own font metrics,
so a squeeze can fall a button differently at the exact boundary (portrait
`game-footer-more` folds Play, which pygame fits by a fifth of a unit).

**Phase 1 is complete** at every size. Left for later phases: sharing,
play-by-post, gauntlet stages, and the map maker.

## The setup menu (phase 2)

`/next/` opens on the setup menu (`js/menu.mjs`, `css/menu.css`), laid out as
pygame's: on a desktop its 1440x960 canvas, on a phone the compact menu, a
sketch of the map and the actions in a grid along the foot, sideways the
title, map and actions in a column beside the panel. As in pygame, a panel
narrower than 500 units (`menu._NARROW_PANEL_W`) stacks the Advanced and
Combat tabs' two columns, judged by a container query on the panel's own
width, so an upright phone with room keeps both. A phone is sent every tab
(`setupmenu.view`'s ``panels``) and lays the others, hidden, under the one
shown, so the panel is as tall as its tallest tab (pygame's
`_tallest_tab_h`) and never moves as the tabs change. The address
pre-fills it as the command line pre-fills pygame's
(`?players=3&nodes=30&seed=7&mode=symmetric&fog=1`, fog as sight in hops), then
a shared link (`#<token>`) does; `&go` skips the menu and plays it. Start
plays the menu's setup (`game.start` with no settings), and the game's Menu
and Setup menu come back to it, the match left behind.

**Who decides what.** `setupmenu` is the menu apart from its drawing, read by
both shells, as `play` and `hud` are for the play screen:

- What a press means, by the widget's key (`setupmenu.press`): the tabs and
  seats, every stepper, toggle and die, the strategy list, and the three
  preferences on the Basic tab. A slider takes the fraction along its track
  (`apply_slider`), the seed field its text (`set_seed_text`). Start goes
  through `setupmenu.start` (a hand map's blockers, then the slow-setup
  confirm); a challenge link's setup asks before an edit drops its score
  (`before_edit`, `after_edit`, `answer_unchallenge`). `menu` hit-tests in
  pygame's pixels and hands the key over; its rules were moved, not copied,
  and its tests pass unchanged but for where they patch.
- Every word, and `setupmenu.view`: the whole menu as plain JSON (the tabs and
  their changed-dots, each tab's rows, the Combat tab's readout and jitter
  matrix, the actions, the open list, the confirm, the phone's sketch).
- The session's `menu.*` ops: `menu.open` (at boot, `first`, which may offer
  to resume), `menu.press`, `menu.key`, `menu.slide` (`start`, `move`, `end`),
  `menu.seed`, `menu.survey` and `menu.resume`. A reply carries `menu`, an
  `action` for the page (`start`, `quit`), a status line said once, and
  `stale` while the lane readout (and on a phone the sketch) describe an
  older setup. The page asks `menu.survey` once nothing has moved for 250 ms,
  never mid-drag, as `menu.pump` waits for a slider's release.

**Saved games persist.** The worker mounts the games folder (`replay.GAMES_DIR`,
`/game/games`) on IndexedDB and writes it back 400 ms after a command, so a
match's log survives a reload and the boot prompt can offer to resume it, as
pygame's does from `games/` on a desktop. The pygbag build kept no games
across a reload.

**Left for their phases, and saying so:** Create map answers "Not in this
build yet" in the status line (Tutorial, the Daily list and Play by post did
until phases 4 and 5). Get Link, Shared matches
and Recently played work, through the same `webstore` calls as pygame's
footer. Quit asks the browser to close the window and says to close the tab
when it can't, as `main.leave_app` does on the web.

**Differences kept on purpose:** no Game UI size stepper (the browser's own
zoom does that job, and the plan drops `ui_scale`); the desktop footer is
pygame's web footer (Get Link and the board's lists, not Save and Load, which
wait for files in phase 7); a slider on a phone
grabs on touch rather than waiting for a sideways move, so the panel scrolls
from anywhere else.

**Phase 2 is complete** at the desktop and phone sizes: every menu parity
recipe but the play-by-post prompt (phase 4) shoots (`tools/parity_next.py`),
and a headless run sets players, a seed and a slider, starts, plays three
turns, goes back to the menu, reloads and resumes the match.

## Sharing (phase 3)

`/next/` talks to the board as pygame's web build does, through the same
`app`, `share` and `campaign` calls; the session carries out pygame's loop's
part (`main`'s ``pending_*`` polls).

- **The win overlay's two buttons** are game actions (`game.action`
  ``share`` and ``leaderboard``, and the C and L keys), gated on
  `Ui.can_post` as pygame gates them. Share copies a challenge link (a daily's
  result line on a daily); the board button opens the entry form, posts under
  a name (`share.quick_post`, through the board's own module in the page), or
  once posted opens the map's page (`Ui.board_action`). Each sends the replay
  first, the press being the consent. The overlay's line under them is
  `Ui.share_msg`.
- **An answer lands later, and is pushed.** The signed-in name for the post
  button, a puzzle's par and a quick post's summary are asked for after the
  command that made them due (`Session._ask_due`) and land when the page hands
  back the answer (`net.reply`). That reply's ``pushed`` is what changed on
  screen: a ``view``, a ``game`` or a ``menu`` reply. The page draws it unasked,
  or, while a turn is in flight, asks for the view again once it lands
  (`game.view`).
- **The weekly campaign.** The session keeps one `campaign.Watcher`, pumped by
  the menu's replies and by `tick`, which the page sends once a second (it
  settles a lookup, refreshes it, and counts a grace down). Start raises the
  campaign's confirm with the slow-setup lines; the answer at Start is stamped
  on the match, kept through an undo or rewind and stamped again on a retry.
- **The match's notes** (`hud.match_notes`: the stage, the campaign's
  reminder, a challenge link's target) head the side panel, on a portrait
  phone only while nothing is in focus, as `render` draws them.
- **A `#log=<id>` link** fetches the replay at boot ("Loading replay...") and
  opens it in history at turn 0, as someone else's game; one that is missing,
  unreadable or recorded under older rules says so on the menu.
- **Share replays** needed nothing new: the checkpoint upload in
  `app.resolve_turn` already goes out as a ``fire`` effect.

**Differences kept on purpose:** a board page opens in this tab (the
``navigate`` effect, as pygame's web build does), so the back button returns
to a fresh `/next/`; a decided match is not offered to resume, so nothing is
lost.

**Left for later:** a link pasted into an open page (`hashchange`) is not read
until a reload, and the daily's buttons and par wait for phase 5 with the daily.

## Play by post (phase 4)

`/next/` plays a shared match through the same machine pygame's loop drives,
`pbpclient.PbpClient`, held by the session and stepped by `tick` on the time
that has passed since its last step.

- **The roster prompt** is `setupmenu`'s now (`open_pbp_prompt`,
  `pbp_press`, `confirm_pbp`, `pbp_prompt_view`), read by pygame's `menu`
  too, which keeps only its hit-testing and drawing. Play by post goes
  through Start's gate (a hand map's blockers), then the prompt; Create match
  goes through the slow-setup confirm with the seats that are people, and the
  session creates the match on a seed resolved once for the whole table. The
  page's two text rows are native inputs (`menu.pbp_text`) and keep their own
  text while typed into, so a late reply never takes keystrokes back.
- **Links at boot.** A `#pbp=<match>:<token>` seat link or a `#spectate=`
  link opens the match ("Opening match...", no resume prompt over it), unless
  a `#log=` link took the launch.
- **Answers are stepped, not landed.** A pbp answer waits in its mailbox
  until the next `tick` collects it, so a read never lands mid-film: the page
  sends no `tick` while a turn is in flight or filming (`PbpClient.step`'s
  ``filming``). The step's `Outcome` comes back as `pushed`: a ``game`` (a
  match opened, or rebuilt from its log, which keeps the camera:
  ``map_fresh`` false), a ``turn`` (one settled elsewhere or resolved here,
  with its film, played as End Turn's is), a ``menu`` reply for the menu's
  line, or a ``view`` (who is waited on, the last call's line). Every op but
  `tick`, `net.reply` and `game.view` counts as input for the idle clock that
  stretches the wait between reads.
- **End Turn sends.** In a shared match `turn.end` resolves nothing: it sends
  our orders (`PbpClient.send`) and answers the waiting view. Nothing resolves
  on a clock; Play and Autoplay stay dead, as in pygame.
- **The words are `hud`'s** (`you_are`, `invite_label`, `waiting_lines`,
  `spectator_line`, `end_turn_word`), read by `render` too; `serial.pbp`
  picks the overlay in `render`'s order under the win veil, a briefing and
  the cards: the invite (each row's Copy is `pbp.copy`, Continue
  `play.close_invite`), the waiting veil, the last call's line, or a
  spectator's. The invite is modal for keys too (`play.press_key`).

All four play-by-post parity recipes shoot at their sizes, and pygame's
screens stayed byte-identical.

**Differences kept on purpose:** a pbp answer can take up to a second longer
to show than in pygame, which polls once a frame; against a five-second read
cadence that is not felt.

**Left for later:** a seat link pasted into an open page (`hashchange`) is
not read until a reload, as with phase 3's links.

## The daily and gauntlets (phase 5)

`/next/` plays the daily, the tutorial and every gauntlet, runs included,
through the same `app` calls pygame's loop makes (`open_daily`, `land_daily`,
`open_linked_gauntlet`, `land_gauntlet`, `open_run`, the generator and its
listing); each answers an `app.MenuNote`, which the session shows on its menu
as `main.show_note` does on pygame's.

- **The Tutorial list is `setupmenu`'s now**: the stage being played
  (`active_stage`, `next_stage`, `load_stage`), the stage banner in the
  subtitle's slot (`stage_lines`), the list's rows with their progress and
  colours (`stage_rows`), its run line (`run_row`) and what a press on it
  means (`tutorial_press`), and `tutorial_view` for the page. pygame's `menu`
  keeps only its drawing and hit-testing and reads the same rows; its names
  stay as wrappers, so its tests pass unchanged. The list is modal: only its
  own controls answer, and Esc closes it.
- **A stage starts through Start's gate**, and `game.start` plays it as the
  stage the menu's setup still is. Retry, Undo and a rewind keep the stage
  without its briefing; Next stage on the win overlay moves the menu on and
  starts the next one, or the run's next step.
- **A run's game reaches the page with the menu's reply** (``game``), whether
  started, continued from the list, or offered by the boot prompt (a run's
  own unfinished game is offered now, and comes back as the run's).
- **The briefing is a card** (`serial.card` kind ``briefing``, its foot and
  target worded by `hud.briefing_close` and `hud.briefing_notes`, read by
  `render` too) that any press puts away (`play.card_up`). The stage's hint
  heads the empty side panel, a tap bringing the briefing back
  (`open_briefing`, as B does); on a phone it is the panel's one job.
- **The board's calls land on the menu.** The Daily list's two items and a
  `#daily`/`#puzzle` link fetch the day onto the menu; a `#g=<id>` link is
  fetched, kept and opened on the list, a `#gauntlet=` one opened at once.
  Each answer lands through `net.reply` and comes back as a pushed ``menu``.
  A puzzle won by hand asks its par as phase 3 wired it.
- **A `#gauntlet-spec=` link generates on the page's clock**: the menu's reply
  says ``generating`` and the page asks `menu.generate` again at once, each
  slice holding the worker at most `session.GEN_SLICE_S` (0.1 s), so its other
  commands still get their turn. Done, the gauntlet opens on the list and a
  ticked listing ask is sent.

Both stage parity recipes shoot at their sizes, as does the Daily list, and
pygame's game, menu and stage screens stayed byte-identical.

**Differences kept on purpose:** the briefing card's paragraphs are set in the
card's own type, as the lore card's are, rather than pygame's fitted fonts,
and the board behind it is not veiled.

**Left for later:** a link pasted into an open page is not read until a
reload, as with phase 3's links.

## The map maker (phase 6)

`/next/`'s Create map (Edit map, with a hand map set) opens the map maker
(`js/maker.mjs`, `css/maker.css`), laid out as pygame's: the tool strip, the
map, the palette band, the sidebar (on an upright phone a band under the
palette, two sliders to a row) and the footer.

- **The editor is `maker`'s now**, a core module: the `Editor` and its
  recipe, what every press, key and control means (`press_system`,
  `press_lane_tool`, `press_owner`, `control`, `press_key`, `answer_confirm`),
  every edit with its undo, the sliders by fraction, the map library, every
  word, and `view` for the page. pygame's `mapmaker` keeps its drawing, its
  hit-testing in pixels, its camera and its gestures' thresholds, and reads
  the rest from `maker` under its old names, so its tests pass unchanged and
  its maker screens stayed byte-identical.
- **The page hit-tests and sends what a press landed on**: a system's index
  (`maker.node`), a point in world units (`maker.place`), the lanes near a
  miss nearest first (`maker.miss`, which says whether a drag from there pans
  or draws the Owners box), the armed system dragged past the threshold
  (`maker.drag`, one in flight at a time) and let go (`maker.release`), a lane
  drag's target (`maker.lane`), the box's systems (`maker.box`), a control's
  key, a slider's fraction, a key's name, a problem's row. The camera is the
  page's (`camera.mjs` with no room: `WorldView`'s own clamp); a press that
  frames something (a problem, Play refused) sends the points as ``fit``.
- **Play** commits the map and starts it as Start does (``game``); **Menu**
  and Esc commit it and answer the menu's reply (``back``). A pinch's first
  finger is put back (`maker.unpress`, `maker.snapshot` taken on a touch
  screen's every map press).
- **Save and Open** write the maps folder, which the worker keeps in
  IndexedDB as it does the games folder, so a saved map opens again after a
  reload (pygbag's build kept none).

Both maker parity recipes shoot at their sizes.

**Differences kept on purpose:** the footer wraps its two clusters onto two
rows where they don't fit, rather than shedding controls by rank; the
filename field shows the default name as a placeholder; systems draw at the
desktop's radius on a phone, where pygame's follow the UI scale.

**Left for later:** nothing of the editor; its files on disk are the browser's
own, not a folder the player can open (phase 7's Save and Load files).

