# The in-app bot maker

A visual, IFTTT-style rule builder, so a player who doesn't write Python has
something between the AI tab's five sliders and a `models/*.py` file.

**Half of it is built.** `starconquest/botlang.py` is the rule language and its
interpreter, shipped and registered — `blockrush`, `blockturtle` and
`blockheuristic` appear in the AI tab's Strategy dropdown today. Why it works the
way it does is in [`design-notes.md`](design-notes.md#visual-bot-programs-botlangpy),
next to every other design note.

**The other half — the editor — is not built.** This file is what that phase needs
and would otherwise have to rediscover: the measurements that justify continuing,
and the four constraints that decide the shape.

## Why it's worth finishing

A 400-game pairwise ladder (`--ladder --trials 20`, 18 nodes, both seatings), rule
bots against the built-ins:

| | vs `heuristic` | vs `rusherplus` | ladder share |
|---|---|---|---|
| `blockturtle` | **76%** | 85% | 27% (1st of 5) |
| `blockrush` | **59%** | 21% | 22% |
| `blockheuristic` | 49% | 65% | 16% |

`blockheuristic` re-expresses `ai.compute_orders` and lands at parity with it,
which was the question the spike existed to answer. Two of the three *beat* the
built-in outright. Timeouts were 29/400, against a ~20% natural stalemate rate for
evenly matched bots on this map size (`heuristic` vs itself: 8/40).

Against the hand-written bots the ceiling is just as clear — above `heuristic`,
`rusherplus` and `claudebot`, and 0% against `thinker` and `knower`. A flat
per-system rule list can't converge waves launched from different distances,
schedule reinforcements by when a blow lands, evacuate a doomed system, or predict
a rival. That's a fine place to sit: the bar is "better than five sliders", not
"state of the art".

Reproduce with:

```sh
uv run python -m tests.sim --ai blockheuristic heuristic blockrush rusherplus blockturtle --ladder --trials 20
uv run python -m tests.sim --ladder --trials 5 --bot-timeout 0.5   # whole roster, incl. knower
```

## Four constraints that decide the design

**1. The editor cannot be a menu tab.** `menu.draw` renders onto a fixed 1440x960
canvas and letterbox-scales it, and every tab lives in one 560x496 panel with no
scrolling anywhere — `test_tab_content_stays_inside_the_panel` hard-asserts
containment. Five tabs at the current 126px width also overflow the tab row
(5x126 + 4x8 = 662 > 560). It wants to be a **third scene** in `main.py`'s
`"menu"` / `"game"` machine, drawn at real resolution with `config.s()` (menu
layout literals are raw pixels and must *not* use `config.s()`; a real-surface
scene is the opposite, like `menu.draw_resume_prompt`).

What to reuse rather than rebuild:

- `render._btn` / `_btn_w` / `_row_h` / `_draw_modal` / `_wrap` / `_tap_size` — the
  measured-layout helpers. Nothing holding text gets a fixed pixel size.
- `render._draw_order_list` — the project's only scrolling list, and the pattern
  to copy: variable row heights, a `page_from(start)` closure, and hitboxes that
  each carry **their own index** (a positional mapping silently deletes the wrong
  row once only a window is on screen).
- `input._handle_route_event` — the template for a sub-scene that takes the whole
  event stream, so nothing underneath stays clickable.
- `render._lay_out_footer` — measured button strip with a squeeze order; route
  mode shows how to replace the strip wholesale.

There is **no drag-and-drop anywhere in the codebase**. The nearest primitives are
`menu.MenuState.drag_key`, `Ui.popup_drag_off`, and the `config.DRAG_THRESHOLD`
arm/commit pattern. Rule rows reordered with up/down buttons need none of it,
which is most of why rows beat a free-form block canvas here.

**2. Generated Python runs in the browser.** pygbag ships full CPython 3.12 for
WASM — it uses `compile`/`exec` in its own bootstrap — and the app bundle unpacks
to a real MEMFS tree at `/data/data/starconquest/assets`. `ai.load_models()`'s
`spec_from_file_location` + `exec_module` already runs there on **every web boot**,
against the `models/` dir `tools/build_web.sh` stages. So `botlang.export` output
executes in the browser exactly as it does on desktop. No sandboxing question to
solve, and no CSP interaction (it's Python `exec`, not JS `eval`).

**3. But nothing written to disk survives a web reload.** pygbag 0.9.3 mounts no
IDBFS and calls no `syncfs`, and the bundle is re-unpacked from the `.apk` each
load. Anything written under `models/`, `saves/`, `games/` or `kv.json` on web is
RAM-backed and gone. The durable pattern is therefore: **source of truth in
`localStorage` via `webstore.get`/`set`** (~5MB per origin, string-only, fails
soft — the module's rule is "storage is always a nicety, never load-bearing"),
materialised into `ai.MODELS_DIR` or `exec`'d at boot beside `main.py`'s
`ai.load_models()`. Add the key name to `paths.py`, which is the one place key
names live (`WEB_SHARED_SETTINGS_KEY`, `WEB_BESTS_KEY`). Off the web, a `bots/`
dir under `paths.data_dir()` mirrors `ai.MODELS_DIR` and `menu._SAVE_DIR`.

**4. Sharing has a ready-made encoder.** `Settings.to_token`'s prune -> minified
JSON -> `zlib(9)` -> unpadded base64url round-trips through
`botlang.to_dict`/`from_dict` unchanged. A maxed-out settings token is 487 chars
today and ~2000 chars is a comfortable share limit, so a rule program fits easily.
Note `webstore.copy_link` (clipboard only) versus `webstore.share_token` (address
bar + `localStorage`) — a challenge token deliberately never persists, and a bot
token probably shouldn't either.

## Suggested order

1. **The scene.** Rule rows over the existing `Program` model, `+ Add rule`,
   delete, reorder. Conditions/actions/amounts come straight from `botlang`'s
   `CONDITIONS` / `ACTIONS` / `AMOUNTS` tables, which already carry a label, a
   knob label, and lo/hi/step/is_int for exactly this — the editor should stay
   data-driven off them rather than hardcoding a widget per rule kind, the way
   `menu._SLIDER_SPECS` does.
2. **Persistence**, per constraint 3.
3. **The in-app benchmark.** Cheap once the scene exists and the highest-value
   part for the player: `sim.play()` is pure and headless, so a Test button can
   step a few games per frame against `heuristic` behind a progress bar without
   blocking the frame. Do not call `sim.play` in a loop on the main thread — it
   plays a whole game per call.
4. **Sharing**, per constraint 4.

## Traps found the hard way

- **A program with no enemy-attack rule cannot win a game.** `blockturtle` first
  had hold / reinforce / expand-neutral / send-to-front — a complete-looking
  defensive bot that won **0 of 300** ladder games, because taking every enemy
  system is the win condition and no rule could take one. The editor should
  probably say something when a program has no rule that can capture from a
  player. It went to 77% the moment one `attack_best` rule was added.
- **Timeout rate is the health signal**, and its baseline is ~20%, not 0. Judge a
  program against a same-seed `heuristic`-vs-`heuristic` control, the way
  `test_starters_finish_their_games` does, never an absolute bar.
- **Keep each spec table entry's evaluator and its `src` template in step.**
  `test_export_round_trips_exactly` runs the interpreter and the exported module
  over 8 turns of 5 games and demands identical orders; that test is what will
  tell you, and it is the only thing that will.
