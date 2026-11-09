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

from starconquest import (ai, app, bench, engine, hostio, hud, paths, play, replay, serial, setupmenu, turnfilm,
                          webstore)
from starconquest import recipe as recipes
from starconquest import run as gauntlet_run
from starconquest.model import GameState
from starconquest.replay import GameLog
from starconquest.settings import Settings, resolve_seed
from starconquest.viewstate import CHOOSING, ROUTING, Ui

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
    "confirm_route": lambda ui, state: ui.confirm_route(state),
    "reset_route": lambda ui, state: ui.reset_route(),
    "set_route_rally": lambda ui, state, rally: ui.set_route_rally(state, bool(rally)),
    "auto_rally": lambda ui, state: ui.auto_rally(state),
    "route_clear": lambda ui, state: play.route_clear(state, ui),
    "add_route_sids": lambda ui, state, sids: ui.add_route_sids(state, [int(s) for s in sids]),
    "open_odds": lambda ui, state: setattr(ui, "odds_open", ui.mode == CHOOSING and not ui.forward_armed),
    "open_lore": lambda ui, state: play.open_lore(state, ui, ui.selected),
    "dismiss_card": lambda ui, state: play.dismiss_card(ui),
    "toggle_graph": lambda ui, state: play.history_key(state, ui, "g"),
    "graph_full": lambda ui, state: setattr(ui, "graph_full", not ui.graph_full),
    "graph_tab": lambda ui, state, i: play.graph_tab(ui, int(i)),
    "toggle_trace": lambda ui, state, pid: ui.toggle_trace(int(pid)),
    "show_all_traces": lambda ui, state: ui.graph_hidden.clear(),
    "select_note": lambda ui, state, i: ui.select_note(int(i)),
    "seek_turn": lambda ui, state, turn: play.seek(ui, int(turn)),
}


class Session:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.state: GameState | None = None
        self.ui: Ui | None = None
        self.log: GameLog | None = None
        self.seed: int | None = None
        # History review: a board, the human's fog and the events per recorded
        # turn (`app.build_history`), and the live fog to restore on leaving.
        self.history: tuple[list, list, list] | None = None
        self.live_fog: tuple | None = None
        # The setup menu (`setupmenu`): its state, whether the page is laid out
        # for a finger (no key hints) and as a phone (which shows a map sketch),
        # and an unfinished match to offer resuming, asked once at boot.
        self.menu = setupmenu.Menu()
        self.touch = False
        self.compact = False
        self.resume_log: GameLog | None = None

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

    def op_game_start(self, settings: dict | None = None, seed: int | None = None) -> dict:
        """A fresh match on ``settings`` (`Settings.from_dict`), or on the menu's
        setup when none are given (Start Game), seeded or rolled."""
        if settings is not None:
            self.settings = Settings.from_dict(settings)
        elif self.settings is None:
            self.settings = Settings()
        ai.load_models()   # files added since boot, or named by a loaded setup
        self.seed = seed if seed is not None else resolve_seed(self.settings)
        self.state, self.ui, self.log = app.start_game(self.settings, self.seed, self.settings.autoplay)
        self.history = self.live_fog = None
        return self._started()

    def _started(self) -> dict:
        return {"seed": self.seed, "static": serial.static(self._state()), "board": serial.board(self._state()),
                "travel": serial.travel(self._state()), "fog": self._fog(), "view": self._view(),
                "legends": serial.legends()}

    # ------------------------------------------------------------------ #
    # The setup menu (`setupmenu`): the page sends a control's key, a
    # slider's fraction or the seed field's text, and draws the reply's
    # ``menu`` as it stands. ``action`` is for the page to carry out
    # (``start``, ``quit``, or a screen of a later phase).
    def op_menu_open(self, prefill: dict | None = None, touch: bool = False, compact: bool = False,
                     first: bool = False) -> dict:
        """Show the menu: at boot (``first``) on a fresh setup, pre-filled from
        the address (``prefill``) and then a shared link, as the command line
        and the link pre-fill pygame's; afterwards on the setup last played,
        the match left behind."""
        self.touch, self.compact = bool(touch), bool(compact)
        if first or self.settings is None:
            self.settings = Settings.from_dict(prefill or {})
            app._apply_shared_link(self.settings)
            self.menu = setupmenu.Menu()
            self.resume_log = None
            if first:
                log = replay.latest_log()
                if (log is not None and not log.finished and log.turn_count > 0
                        and not gauntlet_run.is_playing(log)):
                    self.resume_log = log
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.menu.strategies = setupmenu.strategy_options()
        return self._menu()

    def _menu(self, action: str | None = None) -> dict:
        ms, settings = self.menu, self._settings()
        out = {"menu": setupmenu.view(ms, settings, self.touch, bool(paths.LEADERBOARD_ORIGIN),
                                      every_tab=self.compact),
               "action": action, "status": None,
               "stale": not setupmenu.surveyed(ms, settings, self.compact)}
        if ms.status:   # said once; the page holds it on screen
            out["status"] = {"text": ms.status, "ok": ms.status_ok,
                             "ms": setupmenu.STATUS_MS if ms.status_ok else setupmenu.STATUS_ERROR_MS}
            ms.status = ""
        if self.resume_log is not None:
            go, stay = setupmenu.resume_labels(self.touch)
            out["resume"] = {"title": "Resume last game?", "detail": setupmenu.resume_detail(self.resume_log),
                             "go": go, "stay": stay}
        return out

    def _say(self, text: str, ok: bool) -> None:
        self.menu.status, self.menu.status_ok = text, ok

    def op_menu_press(self, key: str) -> dict:
        """A press on the menu's control ``key`` (`setupmenu.view` names them)."""
        ms, settings, key = self.menu, self._settings(), str(key)
        if ms.confirm_unchallenge or ms.confirm_slow or ms.confirm_clear_map:
            return self._menu(self._answer(key))
        if key != "seed":
            ms.editing_seed = False
        if not (key == "strategy" or key.startswith("strategy_opt_")):
            ms.strategy_open = False
        daily_was, board_was = ms.daily_open, ms.board_open
        ms.daily_open = ms.board_open = False
        setupmenu.before_edit(ms, settings)
        action = None
        if key == "start":
            action = setupmenu.start(ms, settings, "start", self._say)
        elif key == "daily_menu":
            ms.daily_open = not daily_was
        elif key == "board_menu":
            ms.board_open = not board_was
        elif setupmenu.press(ms, settings, key, self._say):
            pass
        elif key == "get_link":
            updated, copied = webstore.share_token(settings.to_token())
            if copied:
                self._say("Link copied — paste to share", True)
            elif updated:
                self._say("Link updated — copy it from the address bar", True)
            else:
                self._say("Couldn't create link", False)
        elif key in ("browse_matches", "browse_configs"):
            self._browse(key)
        elif key == "clear_map":
            ms.confirm_clear_map = settings.custom_map is not None
        elif key == "quit":
            # The page asks the browser to close it, which a tab rarely allows.
            self._say(setupmenu.CANT_CLOSE_MSG, False)
            action = key
        elif key in setupmenu.LATER:
            self._say(setupmenu.LATER_MSG.format(what=setupmenu.LATER[key]), False)
        else:
            raise ValueError(f"not a menu control: {key!r}")
        setupmenu.after_edit(ms, settings)
        return self._menu(action)

    def _browse(self, key: str) -> None:
        """Open one of the board's lists in this tab, as pygame's footer does."""
        path, opened, failed = (
            (paths.LEADERBOARD_LOBBY_PATH, "Shared matches opened", "Couldn't open the lobby")
            if key == "browse_matches" else
            (paths.LEADERBOARD_RECENT_PATH, "Recently played opened", "Couldn't open the leaderboard"))
        url = webstore.leaderboard_url(path)
        if not url:
            self._say("No leaderboard is configured", False)
        elif webstore.open_url(url):
            self._say(opened, True)
        elif webstore.copy_to_clipboard(url):
            self._say("Couldn't open a tab — link copied instead", True)
        else:
            self._say(failed, False)

    def _answer(self, key: str) -> str | None:
        """A press while a confirm is up: only its two buttons answer it."""
        ms, settings = self.menu, self._settings()
        if ms.confirm_unchallenge and key in ("unchallenge_change", "unchallenge_keep"):
            return setupmenu.answer_unchallenge(ms, settings, key == "unchallenge_change", self._say)
        if ms.confirm_slow and key in ("slow_go", "slow_back"):
            return setupmenu.answer_slow(ms, key == "slow_go")
        if ms.confirm_clear_map and key in ("clear_map_yes", "clear_map_no"):
            ms.confirm_clear_map = False
            if key == "clear_map_yes":
                setupmenu.clear_map(settings)
                self._say(setupmenu.CLEARED_MAP_MSG, True)
        return None

    def op_menu_key(self, name: str) -> dict:
        """A key on the menu, by its ``KeyboardEvent.key`` name: Y or Enter
        answers a confirm yes and N or Esc no; otherwise Esc closes an open
        list, and Enter (or space) starts as Start Game does."""
        ms, name = self.menu, str(name)
        modal = setupmenu.modal(ms, self._settings(), self.touch)
        if modal is not None:
            if name in ("y", "Y", "Enter"):
                return self.op_menu_press(modal["go"]["key"])
            if name in ("n", "N", "Escape"):
                return self.op_menu_press(modal["stay"]["key"])
            return self._menu()
        if ms.strategy_open or ms.daily_open or ms.board_open:
            if name == "Escape":
                ms.strategy_open = ms.daily_open = ms.board_open = False
            return self._menu()
        if name in ("Enter", " "):
            return self.op_menu_press("start")
        if name == "Escape":
            return self.op_menu_press("quit")
        return self._menu()

    def op_menu_slide(self, key: str, t: float, phase: str = "move") -> dict:
        """Slider ``key`` pressed (``phase`` ``"start"``), dragged or let go
        (``"end"``) at fraction ``t`` of its track. The lanes are measured again
        only once it is let go."""
        ms, settings = self.menu, self._settings()
        if phase == "start":
            ms.strategy_open = ms.daily_open = ms.board_open = False
            setupmenu.before_edit(ms, settings)
            ms.drag_key = str(key)
        setupmenu.apply_slider(str(key), ms, settings, float(t))
        if phase == "end":
            ms.drag_key = None
            setupmenu.after_edit(ms, settings)
        return self._menu()

    def op_menu_seed(self, text: str, done: bool = False) -> dict:
        """The seed field holds ``text`` as typed (filtered to a seed it can
        take); ``done`` when it loses the caret."""
        ms, settings = self.menu, self._settings()
        setupmenu.before_edit(ms, settings)
        setupmenu.set_seed_text(ms, settings, str(text))
        ms.editing_seed = not done
        setupmenu.after_edit(ms, settings)
        return self._menu()

    def op_menu_survey(self) -> dict:
        """Measure the setup's lanes (and on a phone its map sketch), which the
        page asks for once the setup has settled."""
        setupmenu.survey(self.menu, self._settings(), self.compact)
        return self._menu()

    def op_menu_resume(self, accept: bool) -> dict:
        """Answer the boot prompt: play the unfinished match on, or leave it."""
        log, self.resume_log = self.resume_log, None
        if not accept or log is None:
            return self._menu()
        ai.load_models()   # a resumed game may name a drop-in strategy
        settings = self._settings()
        self.state, self.ui = app.resume_game(log, settings)
        self.log, self.seed = log, log.seed
        self.history = self.live_fog = None
        return {"game": self._started()}

    def op_parity_menu(self, recipe: dict) -> dict:
        """Put a parity screen's menu recipe on screen (`tools/parity_next.py`):
        a fresh setup with the recipe's menu fields set, and for the resume
        prompt the match it offers, played out from its game recipe."""
        self.settings = Settings()
        self.menu = setupmenu.Menu()
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.resume_log = None
        if recipe.get("surface") == "resume":
            self.resume_log = recipes.build(recipe).log
            self.settings = Settings()
        for step in recipe.get("steps", []):
            if step[0] == "menu" and hasattr(self.menu, step[1]):
                setattr(self.menu, step[1], step[2])
        setupmenu.survey(self.menu, self.settings, self.compact)
        return self._menu()

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
        self._ui().press_undo = play.press_snapshot(self._ui())
        play.tap(self._state(), self._ui(), int(sid), bool(shift))
        return {"view": self._view()}

    def op_ui_lane(self, hits: list) -> dict:
        """A press near lanes carrying orders or rules: ``hits`` nearest first,
        each ``[kind, a, b]`` as `play.LaneTag`."""
        self._ui().press_undo = play.press_snapshot(self._ui())
        tag = play.lane_pick(self._ui(), [(str(k), int(a), int(b)) for k, a, b in hits])
        if tag is not None:
            play.tap_lane(self._state(), self._ui(), tag)
        return {"view": self._view()}

    def op_ui_empty(self) -> dict:
        """A press on empty map, or the popup's Done. Route mode keeps its group:
        it confirms and cancels explicitly, so a stray tap must not undo the work."""
        self._ui().press_undo = play.press_snapshot(self._ui())
        if self._ui().mode != ROUTING:
            play.cancel(self._ui())
        return {"view": self._view()}

    def op_ui_unpress(self) -> dict:
        """A second finger made the last press a pinch: put back what that
        press (`op_ui_tap`, `op_ui_lane`, `op_ui_empty`) changed, once."""
        ui = self._ui()
        if ui.press_undo is not None:
            play.press_restore(ui, ui.press_undo)
            ui.press_undo = None
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
        elif name == "hint":
            # The first hint in a game asks first: a hinted game is never posted.
            if app.press_hint(state, ui, self.log):
                title, body, confirm = hud.confirm_hint(ui, app.HINT_STRATEGY.title())
                return {"fresh": False, "view": self._view(),
                        "modal": {"kind": "hint", "title": title, "body": body, "confirm": confirm}}
        elif name == "take_hint":
            app.take_hint(state, ui, self.log)
        elif name == "toggle_history":
            if ui.history:
                self._leave_history()
            elif self.log.turn_count > 0 and ui.can_review(state):
                ui.clear_fading_marks()
                states, fog, events, live = app.open_history(state, ui, self.log)
                if states:
                    self.history, self.live_fog = (states, fog, events), live
            return self._whole(fresh=False)
        elif name == "rewind" and ui.history and not ui.in_pbp:
            if not ui.history_reveal:
                title, body, confirm = hud.confirm_rewind(ui.history_turn)
                return {"fresh": False, "view": self._view(),
                        "modal": {"kind": "rewind", "title": title, "body": body, "confirm": confirm,
                                  "then": "rewind_confirmed"}}
            # A finished game: fork a new save so the completed record stays intact.
            self.log = self.log.fork(ui.history_turn)
            try:
                self.log.save()
            except OSError:
                pass
            self.seed = self.log.seed
            self.history = self.live_fog = None
            self.state, self.ui = app.resume_game(self.log, self.settings)
            return self._whole()
        elif name == "rewind_confirmed" and ui.history and not ui.in_pbp:
            self.history = self.live_fog = None
            self.state, self.ui = app.apply_rewind(self.log, self.settings, ui.history_turn)
            return self._whole()
        elif name == "toggle_route":
            # Leaving discards the plan: it is only ever committed by its confirm.
            if ui.mode == ROUTING:
                ui.reset_route()
            elif ui.can_route(state):
                ui.begin_route()
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

    def _whole(self, fresh: bool = True) -> dict:
        """The board on screen (a history turn while reviewing) and its view.
        ``fresh`` says the match itself was replaced."""
        state = self._shown()
        view = self._view()
        return {"fresh": fresh, "seed": self.seed, "static": serial.static(self._state()),
                "board": serial.board(state), "travel": serial.travel(state), "fog": self._fog(), "view": view,
                "held": self._held()}

    def _leave_history(self) -> None:
        """Back to the live board, with its fog exactly as it was (`main`)."""
        ui = self._ui()
        ui.history = False
        ui.dragging_scrubber = False
        ui.dragging_graph = False
        ui.history_chart = None
        ui.coach_review, ui.coach_open, ui.coach_sel = None, False, -1
        ui.history_graph = ui.graph_full = False
        ui.playing = False   # replay playback must not carry into live play
        ui.clear_fading_marks()
        if self.live_fog is not None:
            ui.visible, ui.seen, ui.player_intel = self.live_fog
        self.history = self.live_fog = None

    def _shown(self) -> GameState:
        """The board on screen: the history turn under review, with the human's
        fog as it was then (everything, once a decided game is revealed), else
        the live board."""
        ui = self._ui()
        if not ui.history or self.history is None:
            return self._state()
        states, fog, _events = self.history
        i = max(0, min(ui.history_turn, len(states) - 1))
        if ui.history_reveal:
            every = set(states[i].systems)
            ui.visible, ui.seen, ui.player_intel = every, set(every), {}
        else:
            ui.visible, ui.seen, ui.player_intel = fog[i]
        return states[i]

    def _held(self) -> dict | None:
        """The marks of the history turn on screen, held rather than fading:
        its events, who held each system as it began, and what could be seen
        across it (`app.hold_history_marks`)."""
        ui = self._ui()
        if not ui.history or self.history is None:
            return None
        states, fog, events = self.history
        i = ui.history_turn
        if i <= 0 or i >= len(events) or i >= len(states):
            return None
        board = states[i - 1]
        visible = set(board.systems) if ui.history_reveal else set(fog[i - 1][0]) | set(fog[i][0])
        return {"events": [serial.event(e) for e in events[i]],
                "owners": {str(sid): s.owner_id for sid, s in board.systems.items()},
                "visible": sorted(visible)}

    def op_history_seek(self, turn: int) -> dict:
        """Show history turn ``turn``."""
        play.seek(self._ui(), int(turn))
        return self._whole(fresh=False)

    def op_history_step(self) -> dict:
        """Playback's next turn: the film from the turn on screen to the next
        (None for a quiet turn), then that turn whole. Past the last turn, or
        once paused, it stays put and playback stops."""
        ui = self._ui()
        assert ui.history and self.history is not None, "not reviewing"
        _states, fog, events = self.history
        nxt = ui.history_turn + 1
        if nxt > ui.history_max or not ui.playing:
            ui.playing = False   # past the end, or paused since the last step
            return {**self._whole(fresh=False), "film": None, "before": None}
        before = {"visible": sorted(fog[ui.history_turn][0]), "seen": sorted(fog[ui.history_turn][1])}
        film = turnfilm.film(events[nxt]) if nxt < len(events) and events[nxt] else None
        ui.history_turn = nxt
        if ui.history_turn >= ui.history_max:
            ui.playing = False
        return {**self._whole(fresh=False), "film": serial.film(film) if film is not None and film.plays else None,
                "before": before}

    def op_ui_call(self, method: str, args: list | None = None) -> dict:
        """One of `UI_CALLS`, by name."""
        fn = UI_CALLS.get(method)
        if fn is None:
            raise ValueError(f"not a page-callable Ui method: {method!r}")
        fn(self._ui(), self._state(), *(args or []))
        return {"view": self._view()}

    def op_parity_load(self, recipe: dict) -> dict:
        """Build a parity screen's game recipe (`recipe.build`) as the match
        on screen, for `tools/parity_next.py` to shoot beside pygame's. Its
        modal, if any, comes back as ``modal``."""
        built = recipes.build(recipe)
        self.settings, self.seed, self.state, self.ui, self.log = (
            built.settings, built.seed, built.state, built.ui, built.log)
        self.history = self.live_fog = None
        if built.history is not None and built.history[0]:
            states, fog, events, live = built.history
            self.history, self.live_fog = (states, fog, events), live
        out = {**self._whole(), "legends": serial.legends()}
        if built.overlay == "rewind":
            title, body, confirm = hud.confirm_rewind(self.ui.history_turn)
            out["modal"] = {"kind": "rewind", "title": title, "body": body, "confirm": confirm}
        elif built.overlay == "hint":
            title, body, confirm = hud.confirm_hint(self.ui, app.HINT_STRATEGY.title())
            out["modal"] = {"kind": "hint", "title": title, "body": body, "confirm": confirm}
        return out

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

    def _settings(self) -> Settings:
        if self.settings is None:
            self.settings = Settings()
        return self.settings

    def _state(self) -> GameState:
        assert self.state is not None, "no game"
        return self.state

    def _ui(self) -> Ui:
        assert self.ui is not None, "no game"
        return self.ui

    def _view(self) -> dict | None:
        if self.ui is None or self.state is None:
            return None
        return serial.view(self._shown(), self.ui)

    def _fog(self) -> dict | None:
        if self.ui is None or self.state is None:
            return None
        return serial.fog(self.ui.visible, self.ui.seen, self.ui.player_intel)
