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

from starconquest import ai, app, bench, engine, hostio, serial
from starconquest.model import GameState
from starconquest.replay import GameLog
from starconquest.settings import Settings, resolve_seed
from starconquest.viewstate import Ui

API_VERSION = 1


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
                "travel": serial.travel(self.state), "fog": self._fog()}

    def op_turn_end(self) -> dict:
        """Resolve one turn as End Turn (or autoplay's clock) does, and answer the
        film to watch it by and the board it lands on."""
        assert self.state is not None and self.ui is not None and self.settings is not None
        if self.state.winner is not None:
            return {"board": serial.board(self.state), "travel": serial.travel(self.state), "film": None,
                    "fog": self._fog(), "ms": 0.0}
        t0 = time.perf_counter()
        reel = app.resolve_turn(self.state, self.ui, self.log, self.settings)
        ms = (time.perf_counter() - t0) * 1000
        # The page plays the film onto the board it already has; the session's
        # own copy of the playback (`Ui.film`) is the pygame shell's, unused here.
        film = reel.film if reel is not None else None
        self.ui.stop_film()
        self.ui.deferred_view_snap = False
        return {"board": serial.board(self.state), "travel": serial.travel(self.state),
                "film": serial.film(film) if film else None, "fog": self._fog(), "ms": round(ms, 2)}

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

    def _fog(self) -> dict | None:
        if self.ui is None or self.state is None:
            return None
        return serial.fog(self.ui.visible, self.ui.seen, self.ui.player_intel)
