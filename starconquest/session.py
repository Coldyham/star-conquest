"""The HTML build's one entry point into the game: JSON commands in, JSON out.

The page's worker hands every command to `Session.handle` as a JSON string and
gets one back (``docs/design/html-frontend.md``):

    request  {"id": n, "op": "turn.end", "args": {...}}
    reply    {"id": n, "ok": true, "result": {...}, "effects": [...]}

A command runs to the end before the next starts, so the board a reply
describes is the board the next command sees. ``effects`` are what Python
asked the page to do meanwhile (`hostio`): fetches, storage writes, the
clipboard. A failing command answers ``ok: false`` with the error and its
traceback, and leaves the session as the failure left it.

Each ``op`` is a method named ``op_`` plus the op with dots as underscores.
"""

from __future__ import annotations

import json
import math
import time
import traceback

from starconquest import ai, app, bench, engine, hostio, play, serial
from starconquest.model import GameState
from starconquest.replay import GameLog
from starconquest.settings import Settings, resolve_seed
from starconquest.viewstate import Ui

API_VERSION = 1

# The `Ui` methods the page may call by name (``ui.call``): the send popup's
# controls and the side panel's buttons, each with the state handed in where it
# needs one. Nothing else on `Ui` is reachable from the page.
UI_CALLS = {
    "step_count": lambda ui, state, delta: ui.step_count(state, int(delta)),
    "set_send_count": lambda ui, state, count: ui.set_send_count(state, int(count)),
    "set_slider_fraction": lambda ui, state, t: ui.set_slider_fraction(state, float(t)),
    "send_all": lambda ui, state: ui.send_all(state),
    "send_half": lambda ui, state: ui.send_half(state),
    "set_forward_mode": lambda ui, state, armed: ui.set_forward_mode(state, bool(armed)),
    "set_share": lambda ui, state, pct: ui.set_share(int(pct)),
    "step_share": lambda ui, state, steps: ui.step_share(int(steps)),
    "step_hold": lambda ui, state, delta: ui.step_hold(int(delta), state),
    "toggle_smart_hold": lambda ui, state: ui.toggle_smart_hold(),
    "focus_lane": lambda ui, state, dest: ui.focus_lane(int(dest)),
    "even_split": lambda ui, state: ui.even_split(),
    "delete_all_forward": lambda ui, state: ui.delete_all_forward(),
    "cancel_send": lambda ui, state: ui.cancel_send(),
    "clear_all_forward": lambda ui, state: ui.clear_all_forward(),
    "clear_dangerous_forward": lambda ui, state: ui.clear_dangerous_forward(state),
    "clear_selected": lambda ui, state: play.clear_selected(ui),
    "apply_hint": lambda ui, state: ui.apply_hint(state),
}


class Session:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.state: GameState | None = None
        self.ui: Ui | None = None
        self.log: GameLog | None = None
        self.seed: int | None = None

    def handle(self, raw: str) -> str:
        try:
            cmd = json.loads(raw)
        except ValueError as err:
            return json.dumps({"id": None, "ok": False, "error": f"not JSON: {err}", "effects": []})
        reply: dict = {"id": cmd.get("id")}
        fn = getattr(self, "op_" + str(cmd.get("op", "")).replace(".", "_"), None)
        if fn is None:
            reply.update(ok=False, error=f"no such op: {cmd.get('op')!r}")
        else:
            try:
                reply.update(ok=True, result=fn(**(cmd.get("args") or {})))
            except Exception as err:  # noqa: BLE001 - a failed command answers, never kills the worker
                reply.update(ok=False, error=f"{type(err).__name__}: {err}", trace=traceback.format_exc())
        reply["effects"] = hostio.drain()
        return json.dumps(reply, separators=(",", ":"))

    # ------------------------------------------------------------------ #
    def op_boot(self, page: dict, store: dict) -> dict:
        """Answer to this page: its address and its stored `sc_*` keys."""
        hostio.boot(page, store)
        loaded = ai.load_models()
        return {"api": API_VERSION, "rules": engine.RULES_VERSION, "bots": sorted(ai.STRATEGIES),
                "loaded": loaded}

    def op_game_start(self, settings: dict, seed: int | None = None) -> dict:
        """A fresh match on ``settings`` (`Settings.from_dict`), seeded or rolled."""
        self.settings = Settings.from_dict(settings)
        self.seed = seed if seed is not None else resolve_seed(self.settings)
        self.state, self.ui, self.log = app.start_game(self.settings, self.seed, self.settings.autoplay)
        return {"seed": self.seed, "static": serial.static(self.state), "board": serial.board(self.state),
                "travel": serial.travel(self.state), "fog": self._fog(), "view": self._view(),
                "legends": serial.legends()}

    def op_turn_end(self) -> dict:
        """Resolve one turn as End Turn (or autoplay's clock) does, and answer the
        film to watch it by and the board it lands on."""
        assert self.state is not None and self.ui is not None and self.settings is not None
        if self.state.winner is not None:
            return {"board": serial.board(self.state), "travel": serial.travel(self.state), "film": None,
                    "fog": self._fog(), "view": self._view(), "snap": False, "ms": 0.0}
        was_over, was_out = self.state.winner is not None, self.state.is_defeated(self.ui.human_id)
        t0 = time.perf_counter()
        reel = app.resolve_turn(self.state, self.ui, self.log, self.settings)
        ms = (time.perf_counter() - t0) * 1000
        # The page plays the film onto the board it already has; the session's
        # own copy of the playback (`Ui.film`) is the pygame shell's, unused here.
        film = reel.film if reel is not None else None
        self.ui.stop_film()
        # The turn that decides the game, or knocks us out, fits the camera to
        # the whole map once its film ends; the camera is the page's, so it is told.
        snap = ((self.state.winner is not None and not was_over)
                or (self.state.is_defeated(self.ui.human_id) and not was_out))
        self.ui.deferred_view_snap = False
        return {"board": serial.board(self.state), "travel": serial.travel(self.state),
                "film": serial.film(film) if film else None, "fog": self._fog(), "view": self._view(),
                "snap": snap, "ms": round(ms, 2)}

    # ------------------------------------------------------------------ #
    # The play screen's presses, hit-tested by the page (`play`).
    def op_ui_tap(self, sid: int, shift: bool = False) -> dict:
        """A press on system ``sid``."""
        play.tap_system(self._state(), self._ui(), int(sid), bool(shift))
        return {"view": self._view()}

    def op_ui_lane(self, hits: list) -> dict:
        """A press near lanes carrying orders or rules: ``hits`` nearest first,
        each ``[kind, a, b]`` as `play.LaneTag`."""
        tag = play.lane_pick(self._ui(), [(str(k), int(a), int(b)) for k, a, b in hits])
        if tag is not None:
            play.tap_lane(self._state(), self._ui(), tag)
        return {"view": self._view()}

    def op_ui_empty(self) -> dict:
        """A press on empty map, or the popup's Done."""
        play.cancel(self._ui())
        return {"view": self._view()}

    def op_ui_drag(self, src: int, dest: int | None, shift: bool = False) -> dict:
        """A drag from our system ``src`` released over ``dest``."""
        play.drag_send(self._state(), self._ui(), int(src), None if dest is None else int(dest), bool(shift))
        return {"view": self._view()}

    def op_ui_hover(self, sid: int | None) -> dict:
        """The pointer is over system ``sid`` (None: over nothing)."""
        self._ui().hover = None if sid is None else int(sid)
        return {"view": self._view()}

    def op_ui_key(self, name: str, shift: bool = False) -> dict:
        """A key by its ``KeyboardEvent.key`` name. ``action`` is for the page
        to carry out (``end_turn``, ``reset_view``, ``menu``…), or None."""
        action = play.press_key(self._state(), self._ui(), str(name), bool(shift))
        return {"action": action, "view": self._view()}

    def op_game_action(self, name: str) -> dict:
        """One of `main`'s actions on the match: play and pause, autoplay, fast
        forward, a new map, a retry from turn 0, or undoing the last turn. One
        that replaces the board answers it whole, as `game.start` does, with
        ``fresh`` set."""
        state, ui = self._state(), self._ui()
        assert self.settings is not None and self.log is not None and self.seed is not None
        if name == "toggle_play":
            app.apply_toggle_play(ui)
        elif name == "toggle_autoplay":
            ui.autoplay = not ui.autoplay
            ui.playing = False
            ui.reset_selection()
            ui.reset_route()
            ui.clear_pending()
        elif name == "toggle_fast_forward":
            app.toggle_fast_forward(state, ui)
        elif name == "restart" and not ui.in_stage:
            self.seed += 1
            self.state, self.ui, self.log = app.start_game(self.settings, self.seed, app.carry_autoplay(ui))
            return self._whole()
        elif name == "retry":
            # Fork the log at turn 0, so the finished record stays intact.
            self.log = self.log.fork(0)
            try:
                self.log.save()
            except OSError:
                pass
            self.seed = self.log.seed
            self.state, self.ui = app.resume_game(self.log, self.settings)
            return self._whole()
        elif name == "undo" and ui.can_undo(state):
            self.state, self.ui = app.apply_rewind(self.log, self.settings, max(self.log.turn_count - 1, 0))
            return self._whole()
        return {"fresh": False, "view": self._view()}

    def _whole(self) -> dict:
        state = self._state()
        return {"fresh": True, "seed": self.seed, "static": serial.static(state), "board": serial.board(state),
                "travel": serial.travel(state), "fog": self._fog(), "view": self._view()}

    def op_ui_call(self, method: str, args: list | None = None) -> dict:
        """One of `UI_CALLS`, by name."""
        fn = UI_CALLS.get(method)
        if fn is None:
            raise ValueError(f"not a page-callable Ui method: {method!r}")
        fn(self._ui(), self._state(), *(args or []))
        return {"view": self._view()}

    def op_net_reply(self, req: int, state: str, body: str) -> dict:
        """The page carried out an ``http`` or ``bridge`` effect; hand its answer
        to whoever is polling for it."""
        return {"delivered": hostio.reply(req, state, body)}

    def op_bench_decide(self, bot: str, aux: float, nodes: int, seed: int, turns: int,
                        budget_scale: float | None = None) -> dict:
        """`bench.decide_times`, with every bot's wall-clock guard scaled by
        ``budget_scale`` for the call (None: lifted, so the result is exact)."""
        scale = math.inf if budget_scale is None else budget_scale
        ai.set_budget_scale(scale)
        try:
            return bench.decide_times(bot, aux, nodes, seed, turns)
        finally:
            ai.set_budget_scale(1.0)

    def _state(self) -> GameState:
        assert self.state is not None, "no game"
        return self.state

    def _ui(self) -> Ui:
        assert self.ui is not None, "no game"
        return self.ui

    def _view(self) -> dict | None:
        if self.ui is None or self.state is None:
            return None
        return serial.view(self.state, self.ui)

    def _fog(self) -> dict | None:
        if self.ui is None or self.state is None:
            return None
        return serial.fog(self.ui.visible, self.ui.seen, self.ui.player_intel)
