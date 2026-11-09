"""Render every screen of the pygame shell: the visual baseline for the HTML build.

Each screen is a recipe: a surface (menu, game, maker, resume), a settings patch,
a seed, and a list of steps (turns played, `Ui` calls, field sets, overlays).
Symbolic system ids in a step (`@home`, `@nbr`, ...) are resolved against the
board when the step runs, and the resolved recipe is what gets written, so the
HTML build can replay exactly the same steps through its session and screenshot
the result for side-by-side review (`tools/parity_sheet.py`).

    uv run python tools/parity_shots.py                 # every screen, every size
    uv run python tools/parity_shots.py --only game-    # names starting with this
    uv run python tools/parity_shots.py --list
    uv run python tools/parity_shots.py --out results/parity/check   # redraw elsewhere, to compare

Writes `results/parity/baseline/<size>/<name>.png` (the screen at each size,
recorded as it goes and skipped when already there; `--force` redraws) and
`tests/fixtures/parity_screens.json` (the resolved recipes). Nothing here
touches `games/`, `kv.json` or the network: preferences read from an empty
scratch store, and the clock is pinned so animated strokes land the same way
on every run. Every screen is the web build's variant (`as_web`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame

from starconquest import (
    ai,
    app,
    config,
    engine,
    gauntlet,
    main,
    mapmaker,
    menu,
    paths,
    render,
    replay,
    webstore,
)
from starconquest.custommap import CustomMap
from starconquest.settings import Settings
from starconquest.viewstate import SELECTED

OUT = ROOT / "results" / "parity" / "baseline"
RECIPES = ROOT / "tests" / "fixtures" / "parity_screens.json"
STORE = ROOT / "results" / "parity" / "kv-empty.json"
CLOCK_MS = 1000

# name -> (css width, css height, device pixel ratio, touch). Desktop is the
# baseline canvas at scale 1; the phones are test_compact's, held both ways.
SIZES = {
    "desktop": (config.BASE_SCREEN_W, config.BASE_SCREEN_H, 1.0, False),
    "phone": (412, 870, 2.625, True),
    "phone-land": (870, 412, 2.625, True),
}
ALL = list(SIZES)
GAME_SIZES = ALL
FLAT = ["desktop", "phone"]

MID = {"players": 3, "nodes": 22}
DUEL = {"players": 2, "nodes": 12}

# Each recipe: name, surface, sizes, settings (a patch over the defaults), seed,
# steps. Steps:
#   ["turns", n]            play n turns, the human seat decided by its bot
#   ["until_over", cap]     ...until someone wins
#   ["select", sid]         select a system, as a tap on it does (`input`)
#   ["ui", method, *args]   a `Ui` method (state passed when it takes one)
#   ["set", field, value]   a `Ui` field
#   ["menu", field, value]  a `MenuState` field
#   ["hint"]                the Hint, confirmed (`app.take_hint`)
#   ["history", turn]       open history review at turn (-1: the latest)
#   ["stage", gauntlet_id, stage_index]   the board is that gauntlet stage
#   ["overlay", kind]       a confirm modal: quit, rewind, hint
#   ["map", path]           settings.custom_map from a file (resolved to the map itself)
SCREENS: list[dict] = [
    # ---- setup menu ----
    {"name": "menu-basic", "surface": "menu", "sizes": FLAT, "steps": []},
    {"name": "menu-combat", "surface": "menu", "sizes": FLAT, "steps": [["menu", "tab", "combat"]]},
    {"name": "menu-advanced", "surface": "menu", "sizes": FLAT, "steps": [["menu", "tab", "advanced"]]},
    {"name": "menu-ai", "surface": "menu", "sizes": FLAT, "steps": [["menu", "tab", "ai"]]},
    {"name": "menu-ai-strategies", "surface": "menu", "sizes": FLAT,
     "steps": [["menu", "tab", "ai"], ["menu", "strategy_open", True]]},
    {"name": "menu-daily-list", "surface": "menu", "sizes": FLAT, "steps": [["menu", "daily_open", True]]},
    {"name": "menu-board-list", "surface": "menu", "sizes": ["phone"], "steps": [["menu", "board_open", True]]},
    {"name": "menu-pbp-prompt", "surface": "menu", "sizes": FLAT, "steps": [["menu", "pbp_prompt", True]]},
    {"name": "menu-slow-confirm", "surface": "menu", "sizes": FLAT,
     "steps": [["menu", "confirm_slow", True], ["menu", "slow_title", "This setup is slow"],
               ["menu", "slow_lines", ["Seat 2 (knower, Search) thinks for up to a second a turn."]]]},
    {"name": "menu-resume-prompt", "surface": "resume", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 6]]},
    # ---- play screen ----
    {"name": "game-opening", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7, "steps": []},
    {"name": "game-midgame", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False]]},
    {"name": "game-selected", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["select", "@home"]]},
    {"name": "game-send-popup", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["select", "@home"],
               ["ui", "begin_send", "@nbr"]]},
    {"name": "game-send-odds-card", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["select", "@home"],
               ["ui", "begin_send", "@nbr"], ["set", "odds_open", True]]},
    {"name": "game-forward-tab", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["select", "@home"],
               ["ui", "begin_send", "@nbr", True]]},
    {"name": "game-orders-and-rules", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False],
               ["select", "@home"], ["ui", "begin_send", "@nbr"], ["ui", "send_half"],
               ["ui", "close_send"],
               ["select", "@home2"], ["ui", "begin_send", "@nbr2", True], ["ui", "close_send"]]},
    {"name": "game-route-mode", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["ui", "begin_route"],
               ["ui", "route_tap", "@home"], ["ui", "route_tap", "@home2"], ["ui", "route_tap", "@far"]]},
    {"name": "game-hint", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["hint"]]},
    {"name": "game-lore", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["set", "lore_sid", "@home"]]},
    {"name": "game-footer-more", "surface": "game", "sizes": ["phone", "phone-land"], "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["set", "show_help", False], ["set", "more_open", True]]},
    {"name": "game-fog", "surface": "game", "sizes": GAME_SIZES, "settings": {**MID, "fog_sight": 1, "fog_scout": 2},
     "seed": 7, "steps": [["turns", 14], ["set", "show_help", False]]},
    {"name": "game-symmetric-hub", "surface": "game", "sizes": FLAT,
     "settings": {"mode": "symmetric", "layout": "hub", "players": 4}, "seed": 11,
     "steps": [["turns", 10], ["set", "show_help", False]]},
    {"name": "game-big-board", "surface": "game", "sizes": FLAT, "settings": {"players": 5, "nodes": 40},
     "seed": 12, "steps": [["turns", 20], ["set", "show_help", False]]},
    {"name": "game-custom-map", "surface": "game", "sizes": FLAT, "settings": {}, "seed": 13,
     "steps": [["map", "maps/test.json"], ["turns", 8], ["set", "show_help", False]]},
    {"name": "game-over-win", "surface": "game", "sizes": GAME_SIZES,
     "settings": {**DUEL, "ai_strategy": ["marshal", "rusherplus"]}, "seed": 21,
     "steps": [["until_over", 300]]},
    {"name": "game-over-loss", "surface": "game", "sizes": FLAT,
     "settings": {**DUEL, "ai_strategy": ["rusherplus", "marshal"]}, "seed": 21,
     "steps": [["until_over", 300]]},
    {"name": "history-midgame", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["history", 8]]},
    {"name": "history-graph", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["history", 8], ["set", "history_graph", True]]},
    {"name": "history-graph-full", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 14], ["history", 8], ["set", "history_graph", True], ["set", "graph_full", True]]},
    {"name": "history-finished-coach", "surface": "game", "sizes": GAME_SIZES,
     "settings": {**DUEL, "ai_strategy": ["marshal", "rusherplus"]}, "seed": 21,
     "steps": [["until_over", 300], ["history", -1]]},
    {"name": "confirm-quit", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 4], ["overlay", "quit"]]},
    {"name": "confirm-rewind", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 8], ["history", 3], ["overlay", "rewind"]]},
    {"name": "confirm-hint", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 4], ["overlay", "hint"]]},
    {"name": "stage-briefing", "surface": "game", "sizes": GAME_SIZES, "settings": {}, "seed": 0,
     "steps": [["stage", "tutorial", 0]]},
    {"name": "stage-playing", "surface": "game", "sizes": FLAT, "settings": {}, "seed": 0,
     "steps": [["stage", "tutorial", 1], ["set", "briefing_open", False], ["turns", 3]]},
    {"name": "pbp-invite", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["set", "pbp_match", "m-parity"], ["set", "pbp_title", "Parity match"],
               ["set", "pbp_invite", [[1, "https://example.invalid/play/#pbp=one"],
                                      [2, "https://example.invalid/play/#pbp=two"],
                                      [3, "https://example.invalid/play/#pbp=three"],
                                      [0, "https://example.invalid/play/#spectate=m-parity"]]]]},
    {"name": "pbp-waiting", "surface": "game", "sizes": GAME_SIZES, "settings": MID, "seed": 7,
     "steps": [["turns", 5], ["set", "show_help", False], ["set", "pbp_match", "m-parity"],
               ["set", "pbp_submitted", True]]},
    {"name": "pbp-spectating", "surface": "game", "sizes": FLAT, "settings": MID, "seed": 7,
     "steps": [["turns", 5], ["set", "show_help", False], ["set", "pbp_match", "m-parity"],
               ["set", "human_id", 0]]},
    # ---- map maker ----
    {"name": "maker-blank", "surface": "maker", "sizes": FLAT, "settings": {}, "steps": []},
    {"name": "maker-map", "surface": "maker", "sizes": FLAT, "settings": {}, "steps": [["map", "maps/test.json"]]},
]


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def lay_out(size: str) -> pygame.Surface:
    css_w, css_h, dpr, touch = SIZES[size]
    if touch:
        fb = main.web_framebuffer(css_w, css_h, dpr)
        scale = main.ui_scale_for(fb, True, 100, fb[0] / css_w)
    else:
        fb = (css_w, css_h)
        scale = 1.0
    config.SCREEN_W, config.SCREEN_H = fb
    config.apply_ui_scale(scale, touch=touch)
    render._FONTS.clear()
    menu._FONTS.clear()
    return pygame.Surface(fb)


# --------------------------------------------------------------------------- #
# Symbols: system ids picked off the board when a step runs
# --------------------------------------------------------------------------- #
def _mine(state, pid):
    return sorted((s for s in state.systems.values() if s.owner_id == pid),
                  key=lambda s: (-s.ships, s.id))


def resolve(arg, state, ui):
    if not (isinstance(arg, str) and arg.startswith("@")):
        return arg
    me = ui.human_id
    mine = _mine(state, me)
    if arg in ("@home", "@home2"):
        i = 0 if arg == "@home" else min(1, len(mine) - 1)
        return mine[i].id
    if arg in ("@nbr", "@nbr2"):
        src = state.systems[resolve("@home" if arg == "@nbr" else "@home2", state, ui)]
        foreign = [n for n in src.neighbors if state.systems[n].owner_id != me]
        pool = foreign or list(src.neighbors)
        return min(pool, key=lambda n: (state.systems[n].ships, n))
    if arg == "@far":
        owned = {s.id for s in mine}
        others = [s for s in state.systems.values() if s.id not in owned]
        return min(others, key=lambda s: s.id).id
    raise ValueError(f"unknown symbol {arg}")


# --------------------------------------------------------------------------- #
# Running a recipe
# --------------------------------------------------------------------------- #
def _settings(recipe: dict) -> Settings:
    patch = dict(recipe.get("settings") or {})
    s = Settings.from_dict(patch)
    for step in recipe["steps"]:
        if step[0] == "map":
            cm = CustomMap.from_dict(_map_data(step[1]))
            assert cm is not None, step
            s.custom_map = cm
    return s


def _map_data(ref) -> dict:
    """A map step's map: a path on a first draw, the map itself once resolved
    (`maps/` is not committed, so the fixture carries the map)."""
    return ref if isinstance(ref, dict) else json.loads((ROOT / ref).read_text())


def _resolve_maps(steps: list) -> list:
    return [["map", _map_data(s[1])] if s[0] == "map" else s for s in steps]


def _play(state, ui, log, n: int, until_over: bool = False) -> None:
    for _ in range(n):
        if state.winner is not None:
            break
        replay.reseed(state)
        human = None
        if state.human() is not None and not state.is_defeated(ui.human_id):
            human = engine.decide_seat(state, ui.human_id, ai.decide)
        record = engine.end_turn(state, human_orders=human, decide=ai.decide)
        log.record_turn(record, human_ai=True)
        app.refresh_fog(state, ui)
    ui.prune_forward(state)


def _call(ui, state, method: str, args: list):
    fn = getattr(ui, method)
    params = list(fn.__code__.co_varnames[1:fn.__code__.co_argcount])
    return fn(state, *args) if params[:1] == ["state"] else fn(*args)


def run_game(recipe: dict, screen: pygame.Surface) -> list:
    settings = _settings(recipe)
    seed = recipe.get("seed", 0)
    stage = None
    for step in recipe["steps"]:
        if step[0] == "stage":
            g = next(g for g in gauntlet.builtin() if g.id == step[1])
            st = g.stages[step[2]]
            settings = gauntlet.stage_settings(st)
            seed = settings.seed if settings.seed is not None else seed
            stage = (g, st)
    state, ui, log = app.start_game(settings, seed, autoplay=False, stage=stage)
    history = None
    overlay = None
    resolved: list = []
    for step in recipe["steps"]:
        kind = step[0]
        if kind == "turns":
            _play(state, ui, log, step[1])
        elif kind == "until_over":
            _play(state, ui, log, step[1])
            assert state.winner is not None, f"{recipe['name']}: no winner in {step[1]} turns"
        elif kind == "select":
            sid = resolve(step[1], state, ui)
            ui.reset_selection()
            ui.selected, ui.mode = sid, SELECTED
            step = ["select", sid]
        elif kind == "ui":
            args = [resolve(a, state, ui) for a in step[2:]]
            _call(ui, state, step[1], args)
            step = ["ui", step[1], *args]
        elif kind == "set":
            value = resolve(step[2], state, ui)
            if step[1] == "pbp_invite":
                value = tuple(tuple(x) for x in value)
            setattr(ui, step[1], value)
            step = ["set", step[1], step[2] if step[1] == "pbp_invite" else value]
        elif kind == "hint":
            app.take_hint(state, ui, log)
        elif kind == "history":
            history = app.open_history(state, ui, log)
            if step[1] >= 0:
                ui.history_turn = min(step[1], ui.history_max)
        elif kind == "overlay":
            overlay = step[1]
        elif kind == "map":
            step = ["map", _map_data(step[1])]
        resolved.append(step)

    if history is not None and history[0]:
        states, fog, _events, _live = history
        i = max(0, min(ui.history_turn, len(states) - 1))
        board = states[i]
        if ui.history_reveal:
            ids = set(board.systems)
            ui.visible, ui.seen, ui.player_intel = ids, set(ids), {}
        else:
            ui.visible, ui.seen, ui.player_intel = fog[i]
        render.draw(screen, board, ui)
    else:
        render.draw(screen, state, ui)
    if overlay == "quit":
        render.draw_confirm_quit(screen)
    elif overlay == "rewind":
        render.draw_confirm_rewind(screen, ui.history_turn)
    elif overlay == "hint":
        render.draw_confirm_hint(screen, ui, app.HINT_STRATEGY.title())
    return resolved


def run_menu(recipe: dict, screen: pygame.Surface) -> list:
    settings = _settings(recipe)
    ms = menu.MenuState()
    for _field, _value in ((s[1], s[2]) for s in recipe["steps"] if s[0] == "menu"):
        setattr(ms, _field, _value)
    if ms.strategy_open:
        ms.strategies = menu._strategy_options()
    menu._survey_lanes(ms, settings)   # what `menu.pump` works out ahead of a frame
    menu.draw(screen, ms, settings)
    return _resolve_maps(recipe["steps"])


def run_resume(recipe: dict, screen: pygame.Surface) -> list:
    settings = _settings(recipe)
    state, ui, log = app.start_game(settings, recipe.get("seed", 0), autoplay=False)
    _play(state, ui, log, recipe["steps"][0][1])
    ms, idle = menu.MenuState(), Settings()
    menu._survey_lanes(ms, idle)
    menu.draw(screen, ms, idle)
    menu.draw_resume_prompt(screen, log)
    return recipe["steps"]


def run_maker(recipe: dict, screen: pygame.Surface) -> list:
    settings = _settings(recipe)
    ed = mapmaker.open_editor(settings, seed=1)
    mapmaker.draw(screen, ed, settings)
    return _resolve_maps(recipe["steps"])


RUNNERS = {"game": run_game, "menu": run_menu, "resume": run_resume, "maker": run_maker}


def shoot(recipe: dict, size: str) -> list:
    screen = lay_out(size)
    resolved = RUNNERS[recipe["surface"]](recipe, screen)
    path = OUT / size / f"{recipe['name']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(screen, str(path))
    return resolved


def as_web() -> None:
    """Draw the web build's variant of every screen: phones only exist there, and
    after the move to HTML local play is that page too. Patched where each module
    holds its own name for `paths.is_web`, as well as at the source for modules
    imported later. Its browser bridges find no `platform.window` and stay quiet,
    as they do on a failed call."""
    web = lambda: True
    original = paths.is_web
    for name, mod in list(sys.modules.items()):
        if name.startswith("starconquest") and getattr(mod, "is_web", None) is original:
            mod.is_web = web  # pyright: ignore[reportAttributeAccessIssue]
    paths.is_web = web  # pyright: ignore[reportAttributeAccessIssue]


def isolate() -> None:
    """No user preferences, no wall clock, no network, no saved games."""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text("{}")
    webstore._file_path = lambda: STORE  # pyright: ignore[reportAttributeAccessIssue]
    pygame.time.get_ticks = lambda: CLOCK_MS
    replay.GameLog.save = lambda self: None  # pyright: ignore[reportAttributeAccessIssue]


def main_cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="screens whose name starts with this")
    ap.add_argument("--force", action="store_true", help="redraw screens already on disk")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", default="", help="draw into this folder instead of the baseline "
                    "(a regression check: compare it with the baseline byte for byte)")
    args = ap.parse_args()
    global OUT
    if args.out:
        OUT = Path(args.out).resolve()
    wanted = [r for r in SCREENS if r["name"].startswith(args.only)]
    if args.list:
        for r in wanted:
            print(f"{r['name']:28} {r['surface']:7} {','.join(r['sizes'])}")
        return
    pygame.init()
    as_web()
    isolate()
    ai.load_models()
    previous = json.loads(RECIPES.read_text()) if RECIPES.exists() else {}
    out = {r["name"]: r for r in previous.get("screens", [])}
    failed = []
    for recipe in wanted:
        for size in recipe["sizes"]:
            path = OUT / size / f"{recipe['name']}.png"
            if path.exists() and not args.force and recipe["name"] in out and not args.out:
                continue
            try:
                resolved = shoot(recipe, size)
            except Exception as exc:  # noqa: BLE001 - one broken screen must not cost the rest
                failed.append(f"{recipe['name']}@{size}: {type(exc).__name__}: {exc}")
                print(f"  FAIL {recipe['name']}@{size}: {exc}", flush=True)
                continue
            out[recipe["name"]] = {**recipe, "steps": resolved}
            print(f"  {size:10} {recipe['name']}", flush=True)
        if args.out:
            continue
        RECIPES.parent.mkdir(parents=True, exist_ok=True)
        RECIPES.write_text(json.dumps(
            {"variant": "web", "sizes": SIZES, "clock_ms": CLOCK_MS,
             "screens": [out[r["name"]] for r in SCREENS if r["name"] in out]}, indent=1) + "\n")
    if failed:
        sys.exit("failed:\n  " + "\n  ".join(failed))


if __name__ == "__main__":
    main_cli()
