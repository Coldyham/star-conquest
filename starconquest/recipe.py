"""A parity screen's recipe applied to a match (`docs/design/html-frontend.md`,
"Parity checks").

A recipe is settings, a seed, then steps: play some turns, select a system,
call a `Ui` method, set a `Ui` field, take a hint, open history, put up a
modal. The session builds one here (`Session.op_parity_load`) and the page
draws it (`tools/parity_next.py`); the pygame shell's `tools/parity_shots.py`
drew the same recipes for the baseline, until the cutover. The committed recipes
(`tests/fixtures/parity_screens.json`) are already resolved: no ``@`` symbol
is left in them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ai, app, engine, gauntlet, replay
from .custommap import CustomMap
from .model import GameState
from .replay import GameLog
from .settings import Settings
from .viewstate import SELECTED, Ui


def _mine(state: GameState, pid: int):
    return sorted((s for s in state.systems.values() if s.owner_id == pid),
                  key=lambda s: (-s.ships, s.id))


def resolve(arg, state: GameState, ui: Ui):
    """A step's argument, with a symbol resolved against the position: ``@home``
    (our strongest system), ``@home2``, ``@nbr`` (``@home``'s weakest foreign
    neighbour), ``@nbr2``, ``@far`` (the lowest id we don't hold)."""
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


def settings_of(recipe: dict, map_data=lambda ref: ref) -> Settings:
    """The recipe's setup, with its hand-made map if it has one (``map_data``
    turns a map step's reference into the map)."""
    s = Settings.from_dict(dict(recipe.get("settings") or {}))
    for step in recipe["steps"]:
        if step[0] == "map":
            cm = CustomMap.from_dict(map_data(step[1]))
            assert cm is not None, step
            s.custom_map = cm
    return s


def play(state: GameState, ui: Ui, log: GameLog, n: int) -> None:
    """``n`` turns with every seat a bot's, the human's included, recorded."""
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


def call(ui: Ui, state: GameState, method: str, args: list):
    """``ui.method(*args)``, handing the state in first where it takes one."""
    fn = getattr(ui, method)
    params = list(fn.__code__.co_varnames[1:fn.__code__.co_argcount])
    return fn(state, *args) if params[:1] == ["state"] else fn(*args)


@dataclass
class Built:
    """A game recipe built: the match, its history review if a step opened
    one (`app.open_history`'s four lists), the modal to put up, and the steps
    with every symbol resolved."""

    settings: Settings
    seed: int
    state: GameState
    ui: Ui
    log: GameLog
    history: tuple[Any, Any, Any, Any] | None
    overlay: str | None
    resolved: list


def build(recipe: dict, map_data=lambda ref: ref) -> Built:
    """Play out a game recipe (its surface ``"game"``)."""
    settings = settings_of(recipe, map_data)
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
            play(state, ui, log, step[1])
        elif kind == "until_over":
            play(state, ui, log, step[1])
            assert state.winner is not None, f"{recipe['name']}: no winner in {step[1]} turns"
        elif kind == "select":
            sid = resolve(step[1], state, ui)
            ui.reset_selection()
            ui.selected, ui.mode = sid, SELECTED
            step = ["select", sid]
        elif kind == "ui":
            args = [resolve(a, state, ui) for a in step[2:]]
            call(ui, state, step[1], args)
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
            step = ["map", map_data(step[1])]
        resolved.append(step)
    return Built(settings, seed, state, ui, log, history, overlay, resolved)
