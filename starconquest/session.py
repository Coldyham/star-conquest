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
from collections.abc import Callable

from starconquest import (ai, app, bench, campaign, daily, engine, files, gauntletgen, hostio, hud, maker, paths,
                          pbp, play, replay, serial, setupmenu, share, turnfilm, webstore)
from starconquest import recipe as recipes
from starconquest import run as gauntlet_run
from starconquest.model import GameState
from starconquest.pbpclient import Outcome, PbpClient
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
    "open_briefing": lambda ui, state: setattr(ui, "briefing_open", True),
    "toggle_graph": lambda ui, state: play.history_key(state, ui, "g"),
    "graph_full": lambda ui, state: setattr(ui, "graph_full", not ui.graph_full),
    "graph_tab": lambda ui, state, i: play.graph_tab(ui, int(i)),
    "toggle_trace": lambda ui, state, pid: ui.toggle_trace(int(pid)),
    "show_all_traces": lambda ui, state: ui.graph_hidden.clear(),
    "select_note": lambda ui, state, i: ui.select_note(int(i)),
    "seek_turn": lambda ui, state, turn: play.seek(ui, int(turn)),
    "close_invite": lambda ui, state: play.close_invite(ui),
}

# Ops that are not the player's doing: they leave play-by-post's idle clock
# running (`PbpClient.idle`, which stretches the wait between reads).
_NOT_INPUT = ("tick", "net.reply", "game.view", "menu.generate")
# How long one `menu.generate` may hold the worker: a slice of probe games,
# then the page's other commands get their turn before it asks again.
GEN_SLICE_S = 0.1


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
        # The map maker's editor while it is open (`maker`).
        self.maker: maker.Editor | None = None
        # The board: the weekly campaign's lookup for the setup on the menu or
        # in play (`campaign.Watcher`), a replay being fetched for a `#log=`
        # link, and the calls whose answers land on the win overlay (the
        # signed-in name, a puzzle's par, a quick post), each with its landing.
        self.campaign = campaign.Watcher()
        self.fetching: share.Download | None = None
        self.waits: list[tuple[pbp.Request, Callable[[str, str], None]]] = []
        # Play by post (`pbpclient`): one shared match's calls, stepped by
        # `tick` on the time that has passed since the last step.
        self.pbp = PbpClient()
        self.stepped: float | None = None
        # The menu's own calls to the board (a daily, a linked gauntlet, a
        # generated gauntlet's listing), each landing as an `app.MenuNote`,
        # and a `#gauntlet-spec=` link's generator, stepped by `menu.generate`.
        self.menu_waits: list[tuple[pbp.Request, Callable[[str, str], app.MenuNote]]] = []
        self.gen_job: gauntletgen.Job | None = None
        self.daily_due = False
        # A match the menu opened itself (a run's stage), for the next menu
        # reply to hand the page as ``game``.
        self.opened: dict | None = None

    def handle(self, raw: str) -> str:
        try:
            cmd = json.loads(raw)
        except ValueError as err:
            return json.dumps({"id": None, "ok": False, "error": f"not JSON: {err}", "effects": []})
        reply: dict = {"id": cmd.get("id")}
        fn = getattr(self, "op_" + str(cmd.get("op", "")).replace(".", "_"), None)
        if cmd.get("op") not in _NOT_INPUT:
            self.pbp.idle = 0
        if fn is None:
            reply.update(ok=False, error=f"no such op: {cmd.get('op')!r}")
        else:
            try:
                reply.update(ok=True, result=fn(**(cmd.get("args") or {})))
                self._ask_due()
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
        menu_setup = settings is None
        if settings is not None:
            self.settings = Settings.from_dict(settings)
        elif self.settings is None:
            self.settings = Settings()
        ai.load_models()   # files added since boot, or named by a loaded setup
        self.seed = seed if seed is not None else resolve_seed(self.settings)
        # The gauntlet stage the menu's setup still is, if any (`menu.active_stage`).
        stage = setupmenu.active_stage(self.menu, self.settings) if menu_setup else None
        self.state, self.ui, self.log = app.start_game(self.settings, self.seed, self.settings.autoplay, stage)
        self.history = self.live_fog = None
        if menu_setup:
            # What the campaign said about this setup as it begins (Start).
            app.stamp_campaign(self.ui, self.campaign)
            app.campaign_tick(self.ui, self.settings, self.campaign)
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
    # ------------------------------------------------------------------ #
    # The daily, gauntlets and runs on the menu (`app`'s menu side, each
    # answering a `MenuNote` that `_note` shows, as `main.show_note` does)
    def _note(self, note: app.MenuNote) -> None:
        ms, settings = self.menu, self._settings()
        if note.status is not None and note.status[0]:
            self._say(*note.status)
        if note.gauntlet is not None:
            setupmenu.open_tutorial(ms, settings, self._say, pick=note.gauntlet)
        if note.said is not None:
            setupmenu.say_gauntlet(ms, *note.said, self._say)

    def _begin(self, action: str) -> str | None:
        """``action`` (``start``, ``run``, ``run_continue``) through Start's gate,
        the campaign's confirm asked for a plain start only (`menu._start`)."""
        asked = campaign.confirm(self.campaign.status) if action == "start" else None
        return setupmenu.start(self.menu, self._settings(), action, self._say, asked=asked)

    def _daily(self, kind: str, day: str = "") -> None:
        """Fetch ``day``'s daily of ``kind`` onto the menu (`app.open_daily`)."""
        if self.daily_due:
            return   # one at a time, as pygame's ``pending_daily``
        pending, note = app.open_daily(day, kind)
        self._note(note)
        if pending is not None:
            self.daily_due = True

            def land(st: str, body: str) -> app.MenuNote:
                self.daily_due = False
                return app.land_daily(self._settings(), st, body, kind)
            self.menu_waits.append((pending, land))

    def _open_run(self, fresh: bool, reopen: bool = True, pick: int | None = None) -> bool:
        """Start a run of the gauntlet picked in the list, or carry on the active
        one (`app.open_run`) on ``pick``, else the stage the list has selected;
        the match goes to the page with the next menu reply (``opened``). False,
        with the list open again saying why, when there is nothing to play."""
        ms, settings = self.menu, self._settings()
        opened, note = app.open_run(settings, fresh, setupmenu.picked_gauntlet(ms), ms.gauntlets,
                                    lambda g, sid: setupmenu.load_stage(ms, settings, g, sid),
                                    ms.stage_pick if pick is None else pick)
        self._note(note)
        if opened is None:
            if reopen:
                setupmenu.reopen_gauntlets(ms, settings, self._say)
            return False
        self.state, self.ui, self.log, self.seed = opened
        self.history = self.live_fog = None
        app.stamp_campaign(self.ui, self.campaign)
        self.resume_log = None
        self.opened = self._started()
        return True

    def _menu_links(self) -> None:
        """A gauntlet link (``#g=``, ``#gauntlet=``), else a ``#daily`` or
        ``#puzzle`` link, else a ``#gauntlet-spec=`` one to generate from, unless
        a replay or a match took the launch. Any of them puts the resume prompt
        away, as pygame's launch does."""
        if self.fetching is not None or self.pbp.invite is not None:
            return
        gid, inline = app.gauntlet_request()
        if gid is not None or inline is not None:
            pending, note = app.open_linked_gauntlet(gid, inline)
            self._note(note)
            if pending is not None:
                self.menu_waits.append((pending, app.land_gauntlet))
            self.resume_log = None
            return
        asked = daily.link(webstore.url_token())
        if asked is not None:
            kind, day = asked
            self._daily(kind, day)
            self.resume_log = None
            return
        job, note = app.start_generation(app.spec_request(""))
        self._note(note)
        if job is not None:
            self.gen_job = job
            self.resume_log = None

    def op_menu_generate(self) -> dict:
        """One slice of a ``#gauntlet-spec=`` generator (`app.pump_generation`),
        which the page asks for again while the reply says ``generating``; once
        done, the gauntlet opens on the Tutorial list and a listing is asked
        for if its link ticked the box (`app.ask_listing`)."""
        job = self.gen_job
        if job is None:
            return self._menu()
        self.gen_job, note = app.pump_generation(self._settings(), job, GEN_SLICE_S)
        self._note(note)
        if self.gen_job is None:
            asked, said = app.ask_listing(job)
            self._note(said)
            if asked is not None:
                gid, name, pending = asked
                self.menu_waits.append((pending, lambda st, _body: app.land_listing(gid, name, st)))
        return self._menu()

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
                if log is not None and not log.finished and log.turn_count > 0:
                    self.resume_log = log
                self._watch_link()
                self._match_link()
                self._menu_links()
        if self.ui is not None and self.ui.in_pbp:
            self.pbp.leave()
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.maker = None
        self.menu.strategies = setupmenu.strategy_options()
        return self._menu()

    def _menu(self, action: str | None = None) -> dict:
        ms, settings = self.menu, self._settings()
        self.campaign.pump(settings)
        out = {"menu": setupmenu.view(ms, settings, self.touch, bool(paths.LEADERBOARD_ORIGIN),
                                      every_tab=self.compact),
               "action": action, "status": None,
               "stale": not setupmenu.surveyed(ms, settings, self.compact)}
        if ms.status:   # said once; the page holds it on screen
            out["status"] = {"text": ms.status, "ok": ms.status_ok,
                             "ms": setupmenu.STATUS_MS if ms.status_ok else setupmenu.STATUS_ERROR_MS}
            ms.status = ""
        if self.gen_job is not None:
            out["generating"] = True
        if self.opened is not None:
            out["game"], self.opened = self.opened, None
        if action == "create_map" and self.maker is not None:
            out["maker"] = maker.view(self.maker, settings, self.touch, files=True)
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
        if ms.confirm_unchallenge or ms.confirm_slow or ms.confirm_clear_map or ms.confirm_quit:
            return self._menu(self._then(self._answer(key)))
        if ms.pbp_prompt:
            # Modal: only its own controls answer.
            if key.startswith("pbp_"):
                return self._menu(self._then(setupmenu.pbp_press(ms, settings, key)))
            return self._menu()
        if ms.tutorial_open:
            # Modal too: a stage, a run button, the picker, Leave, Copy, Close.
            if key.startswith(setupmenu.TUTORIAL_KEYS):
                return self._menu(self._then(setupmenu.tutorial_press(
                    ms, settings, key, paths.is_web(), self._say, self._begin)))
            return self._menu()
        if key != "seed":
            ms.editing_seed = False
        if not (key == "strategy" or key.startswith("strategy_opt_")):
            ms.strategy_open = False
        daily_was, board_was, files_was = ms.daily_open, ms.board_open, ms.files_open
        ms.daily_open = ms.board_open = ms.files_open = False
        setupmenu.before_edit(ms, settings)
        action = None
        if key == "start":
            action = self._begin("start")
        elif key == "tutorial":
            setupmenu.open_tutorial(ms, settings, self._say)
        elif key in ("daily", "puzzle"):
            self._daily(daily.PUZZLE if key == "puzzle" else daily.BOARD)
        elif key == "daily_menu":
            ms.daily_open = not daily_was
        elif key == "board_menu":
            ms.board_open = not board_was
        elif key == "files_menu":
            ms.files_open = not files_was
        elif key == "save_settings":
            self._download(files.setup_file(settings))
        elif key == "save_game":
            self._save_last_match()
        elif key == "load_file":
            pass   # the page opens its file picker on the press itself (`file.open`)
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
        elif key == "play_by_post":
            # The same gate Start goes through (a hand map that cannot build),
            # then the roster prompt rather than the match itself.
            if setupmenu.start(ms, settings, "play_by_post", self._say) is not None:
                setupmenu.open_pbp_prompt(ms, settings)
        elif key == "clear_map":
            ms.confirm_clear_map = settings.custom_map is not None
        elif key == "quit":
            ms.confirm_quit = True
        elif key == "create_map":
            action = self._then(setupmenu.open_creator(ms, settings))
        else:
            raise ValueError(f"not a menu control: {key!r}")
        setupmenu.after_edit(ms, settings)
        return self._menu(action)

    def op_menu_pbp_text(self, which: str, text: str, done: bool = False) -> dict:
        """The roster prompt's text row ``which`` (``name`` or ``title``) holds
        ``text`` as typed; ``done`` when it loses the caret."""
        ms = self.menu
        if ms.pbp_prompt and which in ("name", "title"):
            setupmenu.set_pbp_text(ms, which, str(text))
            ms.pbp_editing = None if done else which
        return self._menu()

    def _then(self, action: str | None) -> str | None:
        """Carry out a menu action the session owns, and pass the page the
        rest: ``play_by_post`` creates the match (`PbpClient.create`) on the
        setup, for the roster and deadline the prompt confirmed; the page stays
        on the menu until the match opens (`tick`)."""
        if action in ("run", "run_continue"):
            self._open_run(fresh=action == "run")
            return None
        if action == "create_map":
            self.maker = maker.open_editor(self._settings())
            return action
        if action != "play_by_post":
            return action
        ms, settings = self.menu, self._settings()
        if self.pbp.make is None:
            # The seed is resolved now and sent: "roll a fresh seed" has to
            # mean one seed for the whole table, not one each.
            self.seed = resolve_seed(settings)
            line = self.pbp.create(settings, self.seed, sorted(ms.pbp_roster) or None,
                                   ms.pbp_deadline_hours, ms.pbp_public, ms.pbp_title, ms.pbp_name)
            if line is not None:
                self._say(*line)
        return None

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

    # ------------------------------------------------------------------ #
    # Files (`files`): Save hands the browser a file (a ``download`` effect);
    # Open is the page's file picker, whose file comes back as `file.open`.
    def _download(self, file: files.Download) -> None:
        hostio.emit("download", **file.effect())
        self._say(f"Saved {file.name}", True)

    def _save_last_match(self) -> None:
        """The newest match in the games folder, as its log."""
        for path in replay.list_logs():
            try:
                log = replay.load(path)
            except (OSError, ValueError):
                continue
            if log.turn_count:
                self._download(files.match_file(log))
                return
        self._say("No match to save yet", False)

    def op_file_open(self, name: str, text: str, where: str = "menu") -> dict:
        """The player picked file ``name`` holding ``text``: in the map maker
        (``where`` ``maker``) a map to edit, on the menu a setup to load, a map
        to play or edit, or a match to watch from its first turn (as pygame's
        ``--watch FILE``). Anything else says what it is not."""
        name = str(name)[:120]
        opened = files.read(str(text))
        if where == "maker" and self.maker is not None:
            ed = self.maker
            if opened.kind == "map":
                maker.push_undo(ed)
                maker.adopt(ed, opened.value)
                maker.set_status(ed, f"Opened {name}", True)
            else:
                maker.set_status(ed, f"{name} isn't a map: {opened.why or 'it is a ' + str(opened.kind)}", False)
            return self._maker()
        ms, settings = self.menu, self._settings()
        if opened.kind is None:
            self._say(f"Couldn't open {name}: {opened.why}", False)
        elif opened.kind == "setup":
            setupmenu.before_edit(ms, settings)
            settings.copy_from(opened.value)
            ms.seed_text = "" if settings.seed is None else str(settings.seed)
            setupmenu.after_edit(ms, settings)
            self._say(f"Loaded {name}", True)
        elif opened.kind == "map":
            setupmenu.before_edit(ms, settings)
            maker.apply_map(settings, opened.value)
            setupmenu.after_edit(ms, settings)
            self._say(f"Opened {name}: Edit map to change it", True)
        else:
            result = self._open_watched(share.OK, opened.value.encoded())
            if "game" not in result:
                return result["menu"]
            self.opened = result["game"]
        return self._menu()

    def _leave(self) -> str:
        """A confirmed quit (`main.leave_app`): ask the browser to close the
        window, and say how to leave if it won't, back on the menu."""
        webstore.close_window()
        if self.ui is not None and self.ui.in_pbp:
            self.pbp.leave()
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.maker = None
        self._say(setupmenu.CANT_CLOSE_MSG, False)
        return "quit"

    def op_page_hash(self, hash: str) -> dict:
        """The page's address took a new ``#fragment`` (a link opened in an
        installed app, or pasted into an open tab): open it as boot does, on
        the menu, leaving any match behind. A blank one changes nothing."""
        hostio.set_hash(str(hash).lstrip("#"))
        if not hostio.page("hash"):
            return {}
        if self.ui is not None and self.ui.in_pbp:
            self.pbp.leave()
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.maker = None
        self.settings = Settings()
        app._apply_shared_link(self.settings)
        self.menu = setupmenu.Menu()
        self.menu.strategies = setupmenu.strategy_options()
        self.resume_log = None
        self._watch_link()
        self._match_link()
        self._menu_links()
        return {"menu": self._menu()}

    def _answer(self, key: str) -> str | None:
        """A press while a confirm is up: only its two buttons answer it."""
        ms, settings = self.menu, self._settings()
        if ms.confirm_unchallenge and key in ("unchallenge_change", "unchallenge_keep"):
            return setupmenu.answer_unchallenge(ms, settings, key == "unchallenge_change", self._say)
        if ms.confirm_slow and key in ("slow_go", "slow_back"):
            return setupmenu.answer_slow(ms, key == "slow_go")
        if ms.confirm_quit and key in ("quit_yes", "quit_no"):
            ms.confirm_quit = False
            if key == "quit_yes":
                return self._leave()
            return None
        if ms.confirm_clear_map and key in ("clear_map_yes", "clear_map_no"):
            ms.confirm_clear_map = False
            if key == "clear_map_yes":
                setupmenu.clear_map(settings)
                self._say(setupmenu.CLEARED_MAP_MSG, True)
        return None

    def op_menu_key(self, name: str) -> dict:
        """A key on the menu, by its ``KeyboardEvent.key`` name: Y or Enter
        answers a confirm yes and N or Esc no; otherwise Esc closes an open
        list, and Enter (or space) starts as Start Game does. On the gauntlet
        list the arrows move its selection by cell and Enter plays it."""
        ms, name = self.menu, str(name)
        if ms.tutorial_open and not (ms.confirm_slow or ms.confirm_unchallenge):
            g = setupmenu.picked_gauntlet(ms)
            if name == "Escape":
                ms.tutorial_open = False
            elif name in setupmenu.STEPS and g is not None:
                setupmenu.step_pick(ms, self._settings(), g, *setupmenu.STEPS[name])
            elif name == "Enter" and g is not None:
                return self._menu(self._then(setupmenu.play_pick(ms, self._settings(), g, self._begin)))
            return self._menu()
        if ms.pbp_prompt:
            if name == "Enter":
                return self._menu(self._then(setupmenu.confirm_pbp(ms, self._settings())))
            if name == "Escape":
                if ms.pbp_editing is not None:
                    ms.pbp_editing = None
                else:
                    setupmenu.close_pbp_prompt(ms)
            return self._menu()
        modal = setupmenu.modal(ms, self._settings(), self.touch)
        if modal is not None:
            if name in ("y", "Y", "Enter"):
                return self.op_menu_press(modal["go"]["key"])
            if name in ("n", "N", "Escape"):
                return self.op_menu_press(modal["stay"]["key"])
            return self._menu()
        if ms.strategy_open or ms.daily_open or ms.board_open or ms.files_open:
            if name == "Escape":
                ms.strategy_open = ms.daily_open = ms.board_open = ms.files_open = False
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
            ms.strategy_open = ms.daily_open = ms.board_open = ms.files_open = False
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
        if gauntlet_run.is_playing(log):
            # A run's own game comes back as the run's, so it keeps the run's
            # rules and its result reaches the run.
            self._open_run(fresh=False)
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
        if self.ui.in_pbp:
            # The turn advances when the last seat is in, never on a press:
            # this sends ours and waits (`tick` collects the rest).
            if not self.ui.awaiting_others(self.state) and not self.ui.spectating:
                self.pbp.send(self.state, self.ui)
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
        it confirms and cancels explicitly, so a stray tap must not undo the work.

        A press on nothing is over nothing, so it drops the hover too: a finger
        leaves no pointer behind to clear it, and the side panel would keep the
        last system pressed in place of the legend and its buttons. Done on the
        popup is the exception: `play.cancel` leaves its source lit as the hover."""
        ui = self._ui()
        ui.press_undo = play.press_snapshot(ui)
        if ui.mode != ROUTING:
            if ui.mode != CHOOSING:
                ui.hover = None
            play.cancel(ui)
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
            if not ui.watched:
                app.stamp_campaign(self.ui, self.campaign)
                self._stage_again()
            return self._whole()
        elif name == "rewind_confirmed" and ui.history and not ui.in_pbp:
            self.history = self.live_fog = None
            self.state, self.ui = app.apply_rewind(self.log, self.settings, ui.history_turn)
            self.ui.campaign_start = ui.campaign_start   # the same match, so the same start
            self._stage_again()
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
        elif name == "retry" and not (ui.run_id and ui.run_note):
            # Fork the log at turn 0, so the finished record stays intact. Not
            # on a run's decided game: a fresh game the run wouldn't record.
            self.log = self.log.fork(0)
            try:
                self.log.save()
            except OSError:
                pass
            self.seed = self.log.seed
            self.history = self.live_fog = None
            self.state, self.ui = app.resume_game(self.log, self.settings)
            if not ui.watched:
                app.stamp_campaign(self.ui, self.campaign)
                self._stage_again()
            return self._whole()
        elif name == "quit":
            title, body, confirm = hud.confirm_quit()
            return {"fresh": False, "view": self._view(),
                    "modal": {"kind": "quit", "title": title, "body": body, "confirm": confirm, "tone": "red",
                              "then": "quit_confirmed"}}
        elif name == "quit_confirmed":
            return {"quit": True, "menu": self._menu(self._leave())}
        elif name == "undo" and ui.can_undo(state):
            self.state, self.ui = app.apply_rewind(self.log, self.settings, max(self.log.turn_count - 1, 0))
            self.ui.campaign_start = ui.campaign_start   # the same match, so the same start
            self._stage_again()
            return self._whole()
        elif name == "next_stage" and ui.offers_next_stage(state):
            # The stage after this one, briefing and all. The menu's setup
            # becomes that stage's, so going back to the menu lands on it. In
            # a run it is the run's next stage, begun as its next step.
            if ui.run_id:
                if self._open_run(fresh=False, reopen=False, pick=ui.run_next):
                    self.opened = None
                    return self._whole()
            else:
                found = setupmenu.next_stage(self.menu, self.settings)
                if found is not None:
                    ai.load_models()
                    self.seed = resolve_seed(self.settings)
                    self.state, self.ui, self.log = app.start_game(self.settings, self.seed,
                                                                   self.settings.autoplay, found)
                    self.history = self.live_fog = None
                    app.stamp_campaign(self.ui, self.campaign)
                    return self._whole()
        elif name == "run_retry" and ui.run_again and ui.run_stage is not None:
            # The same stage again as the run's next step: the life is already
            # spent, so this is only the list's pick made for the player.
            if self._open_run(fresh=False, reopen=False, pick=ui.run_stage):
                self.opened = None
                return self._whole()
        elif name == "run_list" and ui.run_id and ui.run_on:
            # To the gauntlet list, to choose where the run goes next.
            self.state = self.ui = self.log = None
            self.history = self.live_fog = None
            setupmenu.reopen_gauntlets(self.menu, self._settings(), self._say)
            return {"menu": self._menu()}
        elif name == "share" and ui.can_post(state):
            # A challenge link to the clipboard, or a daily's result line.
            ui.share_msg = (app.copy_daily_result(self.settings, state, ui) if ui.daily_day
                            else app.share_challenge(self.settings, state, ui, self.seed, self.log))
        elif name == "leaderboard" and ui.can_post(state):
            self._leaderboard()
        return {"fresh": False, "view": self._view()}

    def _stage_again(self) -> None:
        """A Retry, undo or rewind keeps the stage being played, but not its
        briefing, read the first time round (`app.stage_ui`)."""
        app.stage_ui(self._ui(), setupmenu.active_stage(self.menu, self._settings()), briefing=False)

    def _leaderboard(self) -> None:
        """The win overlay's board button, by what it does now
        (`Ui.board_action`): open the map's page once posted, post under the
        remembered or signed-in name, or open the entry form."""
        state, ui = self._state(), self._ui()
        assert self.settings is not None and self.log is not None and self.seed is not None
        board = ui.board_action()
        if board == "view":
            ui.share_msg = app.open_map_page(ui.posted_key) or ui.share_msg
        elif board == "post":
            request = app.quick_post(self.settings, state, ui, self.seed, self.log)
            if request is not None:
                figures = (state.turn, state.players[ui.human_id].ships_lost)
                self.waits.append((request, lambda st, body: app.land_quick_post(ui, figures, st, body)))
        elif board == "enter":
            ui.share_msg = app.post_to_leaderboard(self.settings, state, ui, self.seed, self.log)

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
        ui.history_chart = None
        ui.coach_review, ui.coach_open, ui.coach_sel = None, False, -1
        ui.history_graph = ui.graph_full = False
        ui.playing = False   # replay playback must not carry into live play
        ui.clear_fading_marks()
        if self.live_fog is not None:
            ui.visible, ui.seen, ui.player_intel = self.live_fog
        ui.leave_history_rules()
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
        ui.show_history_rules()
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
        elif built.overlay == "quit":
            title, body, confirm = hud.confirm_quit()
            out["modal"] = {"kind": "quit", "title": title, "body": body, "confirm": confirm, "tone": "red",
                            "then": "quit_confirmed"}
        return out

    # ------------------------------------------------------------------ #
    # The map maker (`maker`): the page hit-tests in its own pixels and sends
    # what a press landed on (a system, the lanes near it, a control's key, a
    # point in world units), and draws the reply's ``maker`` as it stands. Play
    # answers the match (``game``), Menu the menu's reply (``back``).
    def _ed(self) -> maker.Editor:
        assert self.maker is not None, "no map maker"
        return self.maker

    def _maker(self, action: str | None = None, **extra) -> dict:
        """The editor's view, or what replaced it: Play's match, Menu's menu."""
        if action == "play":
            self.maker = None
            return {"game": self.op_game_start()}
        if action == "menu":
            self.maker = None
            return {"back": self._menu()}
        out = {"maker": maker.view(self._ed(), self._settings(), self.touch, files=True), **extra}
        if action is not None:
            out["action"] = action
        return out

    def _map_press(self) -> maker.Editor:
        """A press on the map: on a touch screen, kept for a pinch to put back
        if this was its first finger (`op_maker_unpress`)."""
        ed = self._ed()
        if self.touch:
            maker.snapshot(ed)
        return ed

    def op_maker_open(self) -> dict:
        """Open the map maker on the menu's setup, as Create map does."""
        self.maker = maker.open_editor(self._settings())
        return self._maker()

    def op_maker_place(self, x: float, y: float, shift: bool = False) -> dict:
        """The Systems tool's press on empty map at world ``(x, y)``: place a
        system there (``armed`` is its index, for the page's move-drag)."""
        ed = self._map_press()
        link = ed.sel_node if maker.chains(ed, bool(shift)) else None
        return self._maker(armed=maker.place(ed, self._settings(), (float(x), float(y)), link))

    def op_maker_node(self, node: int, shift: bool = False) -> dict:
        """A press on system ``node``, by the tool: select it and arm a
        move-drag (Systems, ``armed``), arm or lay a lane (Lanes), paint it (Owners)."""
        ed, node = self._map_press(), int(node)
        armed = None
        if ed.tool == maker.SYSTEMS:
            maker.press_system(ed, node, bool(shift))
            armed = node
        elif ed.tool == maker.LANES:
            maker.press_lane_tool(ed, node)
        else:
            maker.press_owner(ed, node)
        return self._maker(armed=armed)

    def op_maker_miss(self, lanes: list | None = None) -> dict:
        """A press on the map that missed every system, with the lanes within
        reach nearest first. ``pan`` says a left-drag from there pans (Lanes, on
        nothing at all); ``box`` that it draws the Owners tool's box."""
        ed = self._map_press()
        pan = box = False
        if ed.tool == maker.LANES:
            pan = maker.press_lane_tool(ed, None, [int(i) for i in lanes or []])
        elif ed.tool == maker.OWNERS:
            maker.press_owner(ed, None)
            box = True
        return self._maker(pan=pan, box=box)

    def op_maker_drag(self, x: float, y: float) -> dict:
        """The armed system dragged past the threshold, to world ``(x, y)``."""
        maker.drag_to(self._ed(), (float(x), float(y)))
        return self._maker()

    def op_maker_release(self) -> dict:
        """The press on a system let go (an illegal spot snaps back)."""
        maker.release_drag(self._ed())
        return self._maker()

    def op_maker_lane(self, target: int | None = None) -> dict:
        """A lane drag let go over system ``target`` (None: over nothing)."""
        maker.lane_release(self._ed(), None if target is None else int(target))
        return self._maker()

    def op_maker_box(self, nodes: list) -> dict:
        """The Owners tool's box let go around ``nodes``: paint them."""
        maker.paint(self._ed(), [int(i) for i in nodes])
        return self._maker()

    def op_maker_control(self, key: str) -> dict:
        """A press on a control by its key (`maker.control`). Export hands the
        browser the map as a file; Import is the page's file picker (`file.open`)."""
        ed, key = self._ed(), str(key)
        if key == "export":
            file = files.map_file(ed.recipe, ed.filename or maker.DEFAULT_FILENAME)
            hostio.emit("download", **file.effect())
            maker.set_status(ed, f"Exported {file.name}", True)
            return self._maker()
        if key == "import":
            return self._maker()
        return self._maker(maker.control(ed, self._settings(), key))

    def op_maker_slide(self, key: str, t: float, phase: str = "move") -> dict:
        """Slider ``key`` pressed (``start``), dragged, or let go at fraction
        ``t``. The Garrison slider's press is one undo for the whole drag."""
        ed, key = self._ed(), str(key)
        if key == "ships_slider":
            if maker.selected(ed) is None:
                return self._maker()
            if phase == "start":
                maker.push_undo(ed)
            maker.set_garrison(ed, float(t))
        elif key in maker.slider_keys():
            maker.set_slider(self._settings(), key, float(t))
        return self._maker()

    def op_maker_key(self, name: str, ctrl: bool = False) -> dict:
        """A key by its ``KeyboardEvent.key`` name: Y or Enter answers a confirm
        yes and N or Esc no; otherwise `maker.press_key` (``action``
        ``reset_view`` is the page's camera)."""
        ed, name = self._ed(), str(name)
        if ed.confirm is not None:
            if name in ("y", "Y", "Enter"):
                maker.answer_confirm(ed, self._settings(), True)
            elif name in ("n", "N", "Escape"):
                maker.answer_confirm(ed, self._settings(), False)
            return self._maker()
        return self._maker(maker.press_key(ed, self._settings(), name, bool(ctrl)))

    def op_maker_confirm(self, yes: bool) -> dict:
        maker.answer_confirm(self._ed(), self._settings(), bool(yes))
        return self._maker()

    def op_maker_problem(self, index: int) -> dict:
        """A press on a problem's row: select its offender (``fit`` frames it)."""
        maker.focus_problem(self._ed(), int(index))
        return self._maker()

    def op_maker_filename(self, text: str) -> dict:
        """The filename field holds ``text`` as typed (filtered to a name it can take)."""
        self._ed().filename = maker.filename_text(str(text))
        return self._maker()

    def op_maker_unpress(self) -> dict:
        """A second finger made the last map press a pinch: put back what it changed."""
        maker.unpress(self._ed())
        return self._maker()

    def op_parity_maker(self, recipe: dict) -> dict:
        """Put a parity screen's maker recipe on screen (`tools/parity_next.py`),
        as `parity_shots.run_maker` draws it: the recipe's setup and map, with
        Generate pinned to seed 1."""
        self.settings = recipes.settings_of(recipe)
        self.state = self.ui = self.log = None
        self.history = self.live_fog = None
        self.maker = maker.open_editor(self.settings, seed=1)
        return self._maker()

    def op_net_reply(self, req: int, state: str, body: str) -> dict:
        """The page carried out an ``http`` or ``bridge`` effect; hand its answer
        to whoever is polling for it. ``pushed`` is what the answer changed on
        screen, for the page to draw unasked: a ``view`` (the win overlay's
        button or line, the campaign reminder), a ``game`` (a fetched replay,
        opened in history), or a ``menu`` reply (a replay that would not open)."""
        out: dict = {"delivered": hostio.reply(req, state, body)}
        pushed = self._land()
        if pushed:
            out["pushed"] = pushed
        return out

    def op_tick(self) -> dict:
        """The page's clock, once a second while it is open: the campaign's
        lookup settles, refreshes and counts its grace down on it. ``pushed``
        as `op_net_reply`'s, when the reminder's words changed."""
        if not hostio.in_worker():   # not booted: the watcher would decide it is off for good
            return {}
        before = self._view()
        pushed = self._step_match() or self._land(before)
        return {"pushed": pushed} if pushed else {}

    def op_game_view(self) -> dict:
        """The play screen's view as it stands, for a page that set a pushed
        one aside while a turn was in flight."""
        return {"view": self._view()}

    def op_pbp_copy(self, seat: int) -> dict:
        """The invite overlay's Copy on ``seat``'s row: its link to the clipboard."""
        ui = self._ui()
        link = next((link for row, link in ui.pbp_invite if row == int(seat)), "")
        if link and webstore.copy_to_clipboard(link):
            ui.pbp_invite_copied = int(seat)
        return {"view": self._view()}

    def _match_link(self) -> None:
        """A ``#pbp=<match>:<token>`` seat link or a ``#spectate=<match>`` link,
        unless a replay link already took the launch: open the match (`tick`
        collects it). No resume prompt over it."""
        if self.fetching is not None:
            return
        invite = app.pbp_request()
        line = self.pbp.open_link(invite, app.pbp_spectate_request() if invite is None else None)
        if line is not None:
            self._say(*line)
        if self.pbp.invite is not None:
            self.resume_log = None

    def _step_match(self) -> dict:
        """Step play-by-post's client on the time since its last step, never
        mid-film (the page sends no `tick` while one plays), and answer what it
        changed on screen: a match opened or rebuilt (``game``), a turn played
        onto the board (``turn``, with its film), the menu's line (``menu``)."""
        now = time.monotonic()
        dt = 0 if self.stepped is None else int((now - self.stepped) * 1000)
        self.stepped = now
        state, ui = self.state, self.ui
        was_over = state is not None and state.winner is not None
        was_out = state is not None and ui is not None and state.is_defeated(ui.human_id)
        self.pbp.idle += dt
        heard = self.pbp.step(dt, state, ui, self.log, self._settings(), filming=False)
        return self._heard(heard, was_over, was_out)

    def _heard(self, heard: Outcome, was_over: bool, was_out: bool) -> dict:
        if heard.status is not None:
            self._say(*heard.status)
        if heard.created:
            self.resume_log = None
        if heard.game is not None:
            self.state, self.ui, self.log = heard.game
            if heard.seed is not None:
                self.seed = heard.seed
            self.history = self.live_fog = None
            return {"game": {**self._whole(), "legends": serial.legends(), "map_fresh": heard.opened}}
        if heard.reel is not None:
            state, ui = self._state(), self._ui()
            film = heard.reel.film
            ui.stop_film()
            snap = ((state.winner is not None and not was_over)
                    or (state.is_defeated(ui.human_id) and not was_out))
            ui.deferred_view_snap = False
            return {"turn": {"board": serial.board(state), "travel": serial.travel(state),
                             "film": serial.film(film) if film else None, "fog": self._fog(),
                             "view": self._view(), "snap": snap}}
        if heard.status is not None and self.state is None:
            return {"menu": self._menu()}
        return {}

    def _watch_link(self) -> None:
        """A ``#log=<id>`` link asks to watch a posted replay: fetch it, and
        open it in history once it lands (`_land`)."""
        wanted = app.replay_request()
        if not wanted:
            return
        self.fetching = share.fetch_log(wanted)
        if self.fetching is None:
            print(f"cannot fetch replay {wanted!r}: no endpoint, or not a match id")
            self._say(app.WATCH_UNREACHABLE_MSG, False)
        else:
            self._say("Loading replay...", True)

    def _ask_due(self) -> None:
        """Start the calls a command made due: the signed-in name for the win
        overlay's post button, and a daily puzzle's par (`app.resolve_turn`)."""
        ui = self.ui
        if ui is None:
            return
        if ui.post_name_due:
            ui.post_name_due = False
            asked = share.signed_in_name()
            if asked is not None:
                self.waits.append((asked, lambda st, body: app.land_post_name(ui, st, body)))
        if ui.par_due is not None:
            figures, ui.par_due = ui.par_due, None
            asked = daily.par_check(ui.daily_day, *figures)
            if asked is not None:
                self.waits.append((asked, lambda st, body: app.land_par(ui, figures, st, body)))

    def _land(self, before: dict | None = None) -> dict:
        """Land every answer that has arrived, and keep the campaign's lookup
        current. Answers what changed on screen (`op_net_reply`'s ``pushed``)."""
        pushed: dict = {}
        before = before if before is not None else self._view()
        waiting = []
        for request, land in self.waits:
            got, body = request.poll()
            if got == pbp.PENDING:
                waiting.append((request, land))
            else:
                land(got, body)
        self.waits = waiting
        menu_landed = False
        still = []
        for request, land_note in self.menu_waits:
            got, body = request.poll()
            if got == pbp.PENDING:
                still.append((request, land_note))
            else:
                self._note(land_note(got, body))
                menu_landed = True
        self.menu_waits = still
        if self.ui is not None and self.state is not None and self.settings is not None:
            app.campaign_tick(self.ui, self.settings, self.campaign)
        else:
            self.campaign.pump(self._settings())
        if self.fetching is not None:
            got, body = self.fetching.poll()
            if got != share.PENDING:
                self.fetching = None
                return self._open_watched(got, body)
        if before is not None and self._view() != before:
            pushed["view"] = self._view()
        if menu_landed and self.state is None:
            pushed["menu"] = self._menu()
        return pushed

    def _open_watched(self, got: str, body: str) -> dict:
        """A fetched replay landed: open it in history from its first turn, or
        say on the menu why it can't be (`main`'s ``pending_replay``)."""
        opened = app.open_replay(body, self._settings()) if got == share.OK else None
        if opened is not None:
            self.state, self.ui, self.log = opened
            self.seed = self.log.seed
            states, fog, events, live = app.open_history(self.state, self.ui, self.log)
            if states:
                self.history, self.live_fog = (states, fog, events), live
                return {"game": {**self._whole(), "legends": serial.legends()}}
            self.state = self.ui = self.log = None
        elif got != share.OK:
            print(f"replay fetch ended in state {got!r}")
            self._say(app.WATCH_MISSING_MSG if got == share.MISSING else app.WATCH_UNREACHABLE_MSG, False)
        else:
            stale = app._decode_log(body)
            if stale is not None and not stale.is_current:
                print(f"replay was recorded under rules v{stale.rules_version}, "
                      f"this build plays v{engine.RULES_VERSION}")
                self._say(app.WATCH_OUTDATED_MSG, False)
            else:
                print(f"replay body was not a readable log ({len(body)} bytes): {body[:120]!r}")
                self._say(app.WATCH_UNREADABLE_MSG, False)
        return {"menu": self._menu()}

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
