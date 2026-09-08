#!/usr/bin/env python3
"""Check a bot before you trust it: does it load, is it legal, is it any good?

    uv run python tools/check_bot.py mybot

Written for the loop where a bot was drafted with an AI assistant's help (see
``docs/bot-brief.md``): every failure prints one self-contained block to hand
back to it, because "it crashed" is not a fixable report and a traceback out of
the middle of ``tests/sim`` is not one either.

It checks the things a game cannot tell you it is unhappy about. ``engine.
apply_order`` *drops* an illegal order rather than complaining, so a bot can
spend a whole match issuing orders that never happen and look merely weak;
mutating the state corrupts replays rather than failing; and a bot that draws
from the global ``random`` plays differently on every run, which is invisible
until a leaderboard score cannot be reproduced. Those three are silent by
design, which is exactly why they are worth a tool.

Strength is measured last and separately: passing every check means the bot is
*valid*, not good.
"""

from __future__ import annotations

import argparse
import copy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, mapgen                       # noqa: E402
from starconquest.model import GameState, Order                    # noqa: E402
from tests import sim                                              # noqa: E402

BOARD = dict(mode="random", nodes=18, players=3)
SEAT = 1


def _board(seed: int = 5) -> GameState:
    state = mapgen.generate(seed, BOARD["mode"], BOARD["nodes"], BOARD["players"])
    for player in state.players.values():
        player.is_human = False          # headless: the engine drives every seat
    return state


def _snapshot(state: GameState) -> tuple:
    """Everything a decision must leave alone. The rng is excluded: drawing from
    it is not only allowed but required for tie-breaks."""
    return (state.turn, state.winner,
            tuple((s.id, s.owner_id, s.ships, s.prod_progress)
                  for s in state.systems.values()),
            tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
                  for f in state.fleets),
            tuple((p.id, p.alive, p.ships_lost) for p in state.players.values()))


def _why_illegal(state: GameState, order: Order, pid: int) -> str | None:
    """The reason `apply_order` would drop this order, or None if it stands."""
    if order.owner_id != pid:
        return f"owner is {order.owner_id}, not your {pid} — the engine discards it"
    src = state.systems.get(order.source_id)
    if src is None:
        return f"no system {order.source_id} exists"
    if src.owner_id != pid:
        return f"system {order.source_id} belongs to player {src.owner_id}, not you"
    if state.travel_turns(order.source_id, order.dest_id) is None:
        return (f"system {order.dest_id} is not a neighbour of {order.source_id} "
                f"(its neighbours are {src.neighbors}) — one order crosses one lane")
    if order.ships <= 0:
        return f"{order.ships} ships is not a launch"
    return None


def _watch_global_random(fn, *args):
    """Run `fn`, reporting whether it drew from the `random` *module*.

    Detected rather than inferred: comparing two runs only catches this when the
    board happens to have a tie to break differently, and a bot reaching for the
    wrong source of randomness deserves to be caught on every board. `state.rng`
    is a `random.Random` *instance*, so its bound methods are untouched by this
    and never trip it.
    """
    import random as module
    drawn: list[str] = []
    watched = ("random", "choice", "choices", "shuffle", "sample", "randint",
               "randrange", "uniform", "gauss", "getrandbits")
    saved = {name: getattr(module, name) for name in watched}

    def spy(name, real):
        def wrapper(*a, **k):
            drawn.append(name)
            return real(*a, **k)
        return wrapper

    for name, real in saved.items():
        setattr(module, name, spy(name, real))
    try:
        result = fn(*args)
    finally:
        for name, real in saved.items():
            setattr(module, name, real)
    return result, drawn


class Report:
    """Findings, and the block to paste back to whatever wrote the bot."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []

    def ok(self, check: str, detail: str = "") -> None:
        print(f"  PASS  {check}{f' — {detail}' if detail else ''}")

    def fail(self, check: str, detail: str) -> None:
        print(f"  FAIL  {check}")
        self.failures.append((check, detail))

    def finish(self, name: str) -> int:
        if not self.failures:
            print(f"\n{name}: valid. See the strength numbers above for whether it is good.")
            return 0
        print(f"\n{name}: {len(self.failures)} problem(s). "
              f"Everything between the lines is meant to be pasted back verbatim:")
        print("\n" + "-" * 70)
        print(f"My Star Conquest bot `{name}` fails these checks. Please fix the bot.\n")
        for check, detail in self.failures:
            print(f"* {check}\n{detail}\n")
        print("-" * 70)
        return 1


def _resolve(name: str) -> bool:
    """Register the strategies, Python then external, and say if `name` is there."""
    ai.load_models()
    if name in ai.STRATEGIES:
        return True
    from tests import botproc          # imported late: it owns the subprocesses
    if name in [m.name for m in botproc.load_manifests()]:
        botproc.register_external([name])
    return name in ai.STRATEGIES


def check(name: str, games: int, opponent: str) -> int:
    report = Report()
    print(f"Checking `{name}`\n")

    if not _resolve(name):
        print(f"  FAIL  loads")
        print(f"\nNothing named `{name}` is registered. A Python bot is "
              f"`models/{name}.py` defining `decide(state, pid)`; an external "
              f"bot is `bots/{name}.bot.json`. A Python file that fails to "
              f"import is skipped silently — run "
              f"`uv run python -c 'import models.{name}'` to see why. A "
              f"filename starting with `_` is skipped on purpose.\n"
              f"Registered now: {', '.join(ai.available_strategies())}")
        return 1
    report.ok("loads")
    decide = ai.STRATEGIES[name]

    # -- answers at all ----------------------------------------------------- #
    state = _board()
    before = _snapshot(state)
    try:
        orders = decide(state, SEAT)
    except Exception:
        report.fail("answers without raising", "It raised on the opening "
                    f"position:\n\n```\n{traceback.format_exc()}```")
        return report.finish(name)
    if not isinstance(orders, list) or any(not isinstance(o, Order) for o in orders):
        report.fail("returns a list of Orders",
                    f"`decide` returned {type(orders).__name__} "
                    f"({orders!r:.200}). It must return a list of "
                    "`Order(owner_id, source_id, dest_id, ships)`, empty if "
                    "there is nothing to do.")
        return report.finish(name)
    report.ok("returns a list of Orders", f"{len(orders)} on the opening position")

    # -- leaves the board alone --------------------------------------------- #
    if _snapshot(state) != before:
        report.fail("treats the state as read-only",
                    "`decide` changed the board. The state passed in is "
                    "read-only — the engine applies orders itself, and a bot "
                    "that edits it corrupts the game and its replay. Copy "
                    "anything you want to modify (`copy.copy` a system, or "
                    "`copy.deepcopy` the state) and return orders instead.")
    else:
        report.ok("treats the state as read-only")

    # -- issues orders the engine will actually run ------------------------- #
    dropped = []
    trial = copy.deepcopy(state)
    for order in orders:
        why = _why_illegal(trial, order, SEAT)
        if why is not None:
            dropped.append((order, why))
        else:
            engine.apply_order(trial, order)     # so later orders see the deduction
    if dropped:
        lines = "\n".join(f"  - {o} → {why}" for o, why in dropped[:6])
        report.fail("issues legal orders",
                    f"{len(dropped)} of {len(orders)} orders would be silently "
                    f"discarded by the engine — they never happen, and nothing "
                    f"reports it:\n{lines}")
    else:
        report.ok("issues legal orders")

    # -- same seed, same decision ------------------------------------------- #
    # Several boards, and the global `random` deliberately reseeded between the
    # paired calls: a bot drawing from it (rather than from `state.rng`) then
    # diverges on purpose instead of by luck. One board is not enough — a bot
    # can reach for the wrong source of randomness and still agree with itself
    # on a position that happens to have no tie to break.
    divergence = None
    global_draws: list[str] = []
    for probe in (5, 17, 33):
        import random as _random
        _random.seed(1)
        first, drew = _watch_global_random(decide, _board(probe), SEAT)
        global_draws += drew
        _random.seed(999)
        second = decide(_board(probe), SEAT)
        if first != second and divergence is None:
            divergence = (first, second)
    if global_draws:
        report.fail("draws only from the game's own randomness",
                    f"`decide` called `random.{global_draws[0]}()` — the "
                    "`random` module's own generator, which is seeded from the "
                    "clock. A seed has to reproduce a whole match, so tie-breaks "
                    "must come from `state.rng` (a Python bot: "
                    "`state.rng.random()`, `state.rng.choice(...)` — same "
                    "methods, different generator) or from the payload's "
                    "`rng_seed` (an external bot). This one is not caught by "
                    "playing a game: it only shows up as a score nobody can "
                    "reproduce.")
    else:
        report.ok("draws only from the game's own randomness")
    if divergence is not None:
        orders, again = divergence
        report.fail("is reproducible",
                    "Asked twice on an identical board it answered "
                    f"differently ({orders} then {again}). Every bot must be a "
                    "pure function of the state: draw tie-breaks from "
                    "`state.rng` (a Python bot) or from the payload's "
                    "`rng_seed` (an external bot), never from the `random` "
                    "module, the clock, or anything outside the game. A seed "
                    "has to reproduce a whole match.")
    else:
        report.ok("is reproducible")

    # -- survives real games ------------------------------------------------ #
    played, wins, timeouts, turns = 0, 0, 0, []
    for seed in range(1, games + 1):
        try:
            result = sim.play(seed=seed, mode=BOARD["mode"], nodes=BOARD["nodes"],
                              players=2, strategies=[name, opponent])
        except Exception:
            report.fail("survives a whole game",
                        f"It raised part-way through game {seed} (seat {SEAT} "
                        f"vs {opponent}). The opening position was fine, so "
                        f"this is a board state further in — an empty "
                        f"garrison, a system with no neighbours it holds, a "
                        f"player already eliminated:\n\n"
                        f"```\n{traceback.format_exc()}```")
            return report.finish(name)
        played += 1
        wins += 1 if result.winner == SEAT else 0
        timeouts += 1 if result.winner is None else 0
        if result.winner is not None:
            turns.append(result.turns)
    report.ok("survives a whole game", f"{played} games, no crashes")

    from tests import botproc
    if botproc.degraded_runs():
        report.fail("stays answering for a whole game",
                    "The seat fell back to the built-in heuristic part-way "
                    f"through: {'; '.join(botproc.degraded_runs())}. Those "
                    "results are not this bot's. Usually a crash in the bot "
                    "process, or a reply slower than its manifest's "
                    "`budget_ms`.")

    print(f"\nStrength: {wins}/{played} wins against `{opponent}` "
          f"({100 * wins // max(1, played)}%)"
          + (f", {timeouts} unfinished" if timeouts else "")
          + (f", {sum(turns) // len(turns)} turns on average" if turns else ""))
    if timeouts > played // 3:
        print("  Note: that many unfinished games usually means the bot is not "
              "committing — it holds ships back and never takes a system. "
              "Taking every enemy system is the win condition.")
    print(f"  Next: uv run python -m tests.sim --ai {name} {opponent} --swap "
          f"--trials 100   (both seatings, so start position cancels out)")
    return report.finish(name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="a models/ file stem, or a bots/ manifest name")
    ap.add_argument("--games", type=int, default=6, help="games to play (default 6)")
    ap.add_argument("--vs", default="heuristic", help="opponent (default heuristic)")
    args = ap.parse_args()
    return check(args.name, args.games, args.vs)


if __name__ == "__main__":
    raise SystemExit(main())
