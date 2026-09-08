"""Headless AI-vs-AI harness — drives the pure core with no window.

Run a single verbose game:
    uv run python -m tests.sim --seed 1 --verbose

Batch many seeds and print a summary (winner spread, average length, timeouts):
    uv run python -m tests.sim --trials 200 --nodes 18 --players 3

Pit specific models against each other (one name per seat, in seat order; each is
a ``models/`` file stem or the built-in ``heuristic``). The count of names sets
the player count when ``--players`` is omitted:
    uv run python -m tests.sim --ai thinker heuristic --trials 200

Two ways to rank a roster, answering different questions. --swap is a free-for-
all: every bot is in the same game, and the roster is rotated through all N
seatings on each seed so each spends equal time in every start location:
    uv run python -m tests.sim --ai thinker claudebot heuristic --swap --trials 50

--ladder is a pairwise round-robin: every pair meets head-to-head, both seatings,
which isolates matchups ("beats X, loses to Y") instead of blurring them into one
melee. Both default their roster to every registered strategy when --ai is
omitted, so this ranks everything in models/:
    uv run python -m tests.sim --ladder --trials 50

Because the whole simulation core imports no pygame, this is also what the
pytest suite calls to assert the game actually terminates and never corrupts
its state.

Cap wall-clock time per decide() call to keep an "oracle"-style bot (one that
pre-simulates rivals' moves, e.g. models/knower.py) from brute-forcing a whole
game tree; a bot that blows its budget just takes no orders that turn:
    uv run python -m tests.sim --ladder --trials 50 --bot-timeout 0.5
"""

from __future__ import annotations

import argparse
import atexit
import itertools
import signal
from contextlib import contextmanager
from dataclasses import dataclass

from starconquest import ai, config, engine, mapgen, settings
from starconquest.model import AiParams, GameState
from starconquest.settings import Settings

_warned_no_sigalrm = False


class _BotTimeout(TimeoutError):
    """Raised when a decide() call blows its wall-clock budget."""


@contextmanager
def _time_budget(seconds: float):
    """Arm a wall-clock alarm for the duration of the block; raises _BotTimeout.

    Relies on SIGALRM (POSIX only) and single-threaded, sequential decide()
    calls (engine._collect_orders loops seats one at a time) — no need for
    multiprocessing sandboxing since models/ bots are already fully trusted.
    """
    global _warned_no_sigalrm
    if not hasattr(signal, "SIGALRM"):
        if not _warned_no_sigalrm:
            print("--bot-timeout is unsupported on this platform (no SIGALRM); ignoring it")
            _warned_no_sigalrm = True
        yield
        return

    def _raise(signum, frame):
        raise _BotTimeout(f"decide() exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _timed_decide(seconds: float, timeouts: list[int]) -> ai.DecideFn:
    """Wrap ai.decide with a per-call budget; a blown budget scores as no orders."""

    def _decide(state: GameState, pid: int) -> list:
        try:
            with _time_budget(seconds):
                return ai.decide(state, pid)
        except _BotTimeout:
            timeouts[0] += 1
            return []

    return _decide


@dataclass
class SimResult:
    seed: int
    winner: int | None  # player id, 0 for a draw, None if it timed out
    turns: int
    timed_out: bool
    bot_timeouts: int = 0


@dataclass
class ReplayResult:
    """One bot's run through a stored setup, seen from the human seat it took over.

    Mirrors what a win writes into a challenge link (``main.challenge_settings``):
    ``turns`` is ``state.turn`` at the win and ``lost`` that seat's ``ships_lost``,
    so a bot's row and a human's score are the same two numbers measured the same
    way. ``won`` is the discriminator — a lost or timed-out game still reports both.
    """

    bot: str
    won: bool
    turns: int
    lost: int
    timed_out: bool
    bot_timeouts: int = 0


@dataclass
class SwapGame:
    """One --swap game: its result plus the seat->strategy line-up used, so a win
    can be credited to whichever strategy held the winning seat that game."""

    result: SimResult
    assignment: list[str]


def check_invariants(state: GameState) -> None:
    for s in state.systems.values():
        assert s.ships >= 0, f"negative garrison at system {s.id}: {s.ships}"
        assert s.owner_id in state.players, f"unknown owner {s.owner_id}"
        assert s.prod_progress >= 0
    for f in state.fleets:
        assert f.ships > 0, "a fleet with no ships is in transit"
        assert f.owner_id in state.players


def player_stats(state: GameState) -> dict[int, tuple[int, int]]:
    """player id -> (systems owned, total ships incl. in-transit)."""
    stats: dict[int, tuple[int, int]] = {}
    for pid, p in state.players.items():
        if p.is_neutral:
            continue
        systems = sum(1 for s in state.systems.values() if s.owner_id == pid)
        ships = sum(s.ships for s in state.systems.values() if s.owner_id == pid)
        ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
        stats[pid] = (systems, ships)
    return stats


def print_state(state: GameState) -> None:
    parts = []
    for pid, (systems, ships) in sorted(player_stats(state).items()):
        parts.append(f"{config.player_name(pid)}: {systems}sys/{ships}sh")
    neutral = sum(1 for s in state.systems.values() if s.owner_id == 0)
    fleets = len(state.fleets)
    print(f"turn {state.turn:>3} | " + "  ".join(parts) + f"  | neutral {neutral}, fleets {fleets}")


def _assign_strategies(state: GameState, strategies: list[str]) -> None:
    """Stamp non-neutral seats with named strategies, in seat order (1-based).

    Mirrors ``settings.build_state``: the seat with player id ``i`` takes
    ``strategies[i - 1]``; any seat past the list keeps its default
    (``"heuristic"``). Names must already be registered (``ai.load_models`` /
    ``ai.STRATEGIES``) — an unregistered name would have ``decide`` silently fall
    back to the heuristic, so the CLI validates them up front rather than here.
    """
    for player in state.players.values():
        if player.is_neutral:
            continue
        idx = player.id - 1
        if 0 <= idx < len(strategies):
            player.ai_strategy = strategies[idx]


def _rotations(strategies: list[str]) -> list[list[str]]:
    """The N cyclic seat line-ups of a roster — each bot in each seat exactly once.

    ``[A, B, C]`` -> ``[A,B,C], [B,C,A], [C,A,B]``. Rotating the seating by one is
    an exact symmetry of a ``symmetric`` map, so these N line-ups fully cancel seat
    bias there; on ``random`` maps they balance seat occupancy and the seed spread
    blurs the rest. Linear in N, not the N! of every possible permutation.
    """
    n = len(strategies)
    return [[strategies[(i + k) % n] for i in range(n)] for k in range(n)]


def play(
    seed: int,
    mode: str = "random",
    nodes: int = config.DEFAULT_NODES,
    players: int = config.DEFAULT_PLAYERS,
    max_turns: int = 600,
    verbose: bool = False,
    strategies: list[str] | None = None,
    bot_timeout: float = 0.0,
) -> SimResult:
    state = mapgen.generate(seed, mode, nodes, players)
    # AI-vs-AI: drive every slot with the AI, including the human's seat.
    for p in state.players.values():
        p.is_human = False
    if strategies:
        _assign_strategies(state, strategies)
    check_invariants(state)
    if verbose:
        print_state(state)
    timeouts = [0]
    decide = _timed_decide(bot_timeout, timeouts) if bot_timeout > 0 else ai.decide
    while state.winner is None and state.turn < max_turns:
        engine.end_turn(state, decide=decide)
        check_invariants(state)
        if verbose:
            print_state(state)
    return SimResult(seed, state.winner, state.turn, state.winner is None, timeouts[0])


def play_settings(
    cfg: Settings,
    seed: int,
    bot: str,
    aux: float | None = None,
    max_turns: int = 600,
    bot_timeout: float = 0.0,
) -> ReplayResult:
    """Replay a stored setup with ``bot`` holding the human's seat.

    What the leaderboard's "how would each bot have done?" column is made of
    (``tools/bot_replay.py``): the same map, the same opponents in the same
    seats, with a drop-in strategy standing in for the person who posted the
    score. ``play`` above answers a different question and takes the map
    parameters raw; this one goes through ``settings.build_state`` because a
    posted setup carries tuned balance knobs, and only that funnel pushes them
    into ``config`` before generation.

    The *map* is identical to the one the human played — mapgen is a pure
    function of the seed, down to the star names it rolls last. The *battles*
    are not: once orders diverge, so do the draws taken from ``state.rng``, which
    is exactly right. The bot fights its own war on the same board.

    The seat is handed default ``AiParams`` rather than the setup's own ``ai[0]``.
    That slot belongs to the human, so whatever a menu left in it is unrelated to
    how this bot ought to play; taking it would also let two identical maps score
    the same bot differently. ``AiParams()`` is the documented untuned profile
    (``config.AI_AUX`` is 1.0 — see ``models/README.md``). Opponent seats keep the
    strategy *and* the params the setup gave them, since those are part of the
    map's difficulty.

    ``aux`` is the one exception, and the only knob a caller may set: it is the
    bot-defined knob (``AiParams.aux``), so "this bot at its best" is a statement
    only the caller can make — ``models/knower.py`` reads it as search depth and
    is a substantially stronger player above the 1.0 default. ``None`` keeps that
    default. Every other field stays untuned deliberately: they belong to the
    built-in heuristic's own tuning, not to a bot's identity.

    A bot that never takes the board still returns a result: ``won`` is False and
    ``turns``/``lost`` report how long it lasted and what it spent. Never rank a
    lost game against a won one on turns alone.
    """
    state = settings.build_state(cfg, seed)
    seat = state.human()
    if seat is None:
        raise ValueError("this setup has no human seat to replay")
    # Hand the seat to the AI: engine._collect_orders skips the human, so this is
    # what makes `decide` run for it at all (`play` does the same for every seat).
    seat.is_human = False
    seat.ai_strategy = bot
    seat.ai_params = AiParams() if aux is None else AiParams(aux=aux)

    check_invariants(state)
    timeouts = [0]
    decide = _timed_decide(bot_timeout, timeouts) if bot_timeout > 0 else ai.decide
    while state.winner is None and state.turn < max_turns:
        engine.end_turn(state, decide=decide)
        check_invariants(state)
    return ReplayResult(
        bot=bot,
        won=state.winner == seat.id,
        turns=state.turn,
        lost=seat.ships_lost,
        timed_out=state.winner is None,
        bot_timeouts=timeouts[0],
    )


def run_trials(seeds, mode, nodes, players, max_turns, strategies=None, bot_timeout=0.0) -> list[SimResult]:
    return [play(s, mode, nodes, players, max_turns, strategies=strategies, bot_timeout=bot_timeout) for s in seeds]


def run_swap(seeds, mode, nodes, strategies, max_turns, bot_timeout=0.0) -> list[SwapGame]:
    """Play every rotation of the roster on each seed (same map, seats rotated)."""
    n = len(strategies)
    games: list[SwapGame] = []
    for seed in seeds:
        for assignment in _rotations(strategies):
            r = play(seed, mode, nodes, n, max_turns, strategies=assignment, bot_timeout=bot_timeout)
            games.append(SwapGame(r, assignment))
    return games


def run_ladder(seeds, mode, nodes, roster, max_turns, bot_timeout=0.0) -> list[SwapGame]:
    """Pairwise round-robin: every unordered pair, both seatings, on every seed.

    Two players per game, so a win means "beat *that* bot" rather than "survived
    the melee" — which is what makes a matchup grid readable. Both seatings of
    each pair are played on the same map so first-position advantage cancels.
    Cost is ``pairs * 2 * seeds`` games, i.e. quadratic in the roster.
    """
    games: list[SwapGame] = []
    for seed in seeds:
        for a, b in itertools.combinations(roster, 2):
            for assignment in ([a, b], [b, a]):
                r = play(seed, mode, nodes, 2, max_turns, strategies=assignment, bot_timeout=bot_timeout)
                games.append(SwapGame(r, assignment))
    return games


def _seat_label(pid: int, strategies: list[str] | None) -> str:
    """A player's display name, annotated with its strategy in a tournament run."""
    name = config.player_name(pid)
    if strategies and 1 <= pid <= len(strategies):
        return f"{name} [{strategies[pid - 1]}]"
    return name


def _summarise(results: list[SimResult], strategies: list[str] | None = None) -> None:
    n = len(results)
    timeouts = sum(r.timed_out for r in results)
    bot_timeouts = sum(r.bot_timeouts for r in results)
    finished = [r for r in results if not r.timed_out]
    wins: dict[int, int] = {}
    for r in finished:
        w = r.winner
        if w is None:            # excluded by the filter above; this narrows the type
            continue
        wins[w] = wins.get(w, 0) + 1
    avg = sum(r.turns for r in finished) / len(finished) if finished else 0.0
    print(f"\n{n} games | finished {len(finished)} | timeouts {timeouts} | bot timeouts {bot_timeouts}")
    print(f"avg length (finished): {avg:.1f} turns")
    for pid in sorted(wins):
        label = "draw" if pid == 0 else _seat_label(pid, strategies)
        print(f"  {label}: {wins[pid]} wins")


def _tally(games: list[SwapGame], roster: list[str]) -> tuple[dict[str, int], int, int]:
    """Wins per strategy across ``games``, plus the draw and timeout counts.

    A win is credited to whichever strategy held the winning seat that game, which
    is the whole reason ``SwapGame`` records the line-up alongside the result.
    """
    wins: dict[str, int] = dict.fromkeys(roster, 0)
    draws = timeouts = 0
    for g in games:
        winner = g.result.winner     # None (timeout) / 0 (draw) / winning seat id
        if winner is None:
            timeouts += 1
        elif winner == 0:
            draws += 1
        else:
            wins[g.assignment[winner - 1]] += 1
    return wins, draws, timeouts


def _avg_turns(games: list[SwapGame]) -> float:
    """Mean length of the games that actually finished (timeouts would skew it)."""
    lengths = [g.result.turns for g in games if not g.result.timed_out]
    return sum(lengths) / len(lengths) if lengths else 0.0


def _bot_timeout_total(games: list[SwapGame]) -> int:
    return sum(g.result.bot_timeouts for g in games)


def _rank_lines(wins: dict[str, int], finished: int) -> None:
    for strat, w in sorted(wins.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100 * w / finished if finished else 0.0
        print(f"  {strat}: {w} wins ({pct:.0f}%)")


def _summarise_swap(games: list[SwapGame], roster: list[str], seeds: int) -> None:
    """Rank a --swap run by wins per strategy (each bot played every game)."""
    wins, draws, timeouts = _tally(games, roster)
    total = len(games)
    finished = total - timeouts
    print(f"\n{total} games | {len(roster)} rotations x {seeds} seeds | draws {draws} | timeouts {timeouts} "
          f"| bot timeouts {_bot_timeout_total(games)}")
    print(f"avg length (finished): {_avg_turns(games):.1f} turns")
    _rank_lines(wins, finished)


def _summarise_ladder(games: list[SwapGame], roster: list[str], seeds: int) -> None:
    """Rank a --ladder run, then print the head-to-head grid behind the ranking.

    A row reads "this bot's win rate against each column", so a strategy that
    ranks mid-table but beats the leader shows up rather than being averaged away.
    """
    wins, draws, timeouts = _tally(games, roster)
    total = len(games)
    finished = total - timeouts
    pairs = len(roster) * (len(roster) - 1) // 2
    print(f"\n{total} games | {pairs} pairs x 2 seatings x {seeds} seeds "
          f"| draws {draws} | timeouts {timeouts} | bot timeouts {_bot_timeout_total(games)}")
    print(f"avg length (finished): {_avg_turns(games):.1f} turns")
    _rank_lines(wins, finished)

    # head[pair] = {strategy: wins}, plus a "" key for games they finished. Both
    # sides are counted explicitly rather than one being derived from the other:
    # a draw is a finished game neither bot won, so the two rates need not sum to
    # 100% and inferring one from the other would silently credit draws as wins.
    head: dict[tuple[str, str], dict[str, int]] = {}
    for g in games:
        a, b = g.assignment
        cell = head.setdefault((a, b) if a < b else (b, a), {a: 0, b: 0, "": 0})
        if g.result.winner is None:
            continue                    # timeout: nobody played it to a finish
        cell[""] += 1
        if g.result.winner != 0:
            cell[g.assignment[g.result.winner - 1]] += 1

    width = max(len(s) for s in roster)
    print("\nhead-to-head (row's win rate vs column)")
    print(" " * (width + 2) + "  ".join(f"{s[:6]:>6}" for s in roster))
    for a in roster:
        cells = []
        for b in roster:
            cell = head.get((a, b) if a < b else (b, a))
            if a == b or cell is None or cell[""] == 0:
                cells.append(f"{'—' if a == b else '-':>6}")
            else:
                cells.append(f"{100 * cell[a] / cell['']:>5.0f}%")
        print(f"  {a:<{width}}" + "  ".join(cells))


def _register_external(args) -> None:
    """Register `bots/` under `--external`, and report any seat that stopped
    being its bot mid-run.

    Imported here rather than at module scope so the ordinary headless suite
    never pulls in `subprocess` — and opt-in rather than automatic, unlike
    `ai.load_models()`, because a default `--ladder` silently spawning child
    processes would be a surprise and `tools/bot_replay` takes its roster from
    `ai.available_strategies()`.
    """
    if not getattr(args, "external", False):
        return
    from . import botproc
    names = botproc.register_external()
    print(f"external bots: {', '.join(names) if names else '(none in bots/)'}")
    atexit.register(_report_degraded)


def _report_degraded() -> None:
    """A degraded seat played `heuristic` for part of a game, so nothing in this
    run may be reported as that bot's score. Said once, at the end, loudly."""
    from . import botproc
    notes = botproc.degraded_runs()
    if notes:
        print("\nDEGRADED — a bot stopped answering and its seat fell back to "
              "heuristic. These results are not that bot's:")
        for note in notes:
            print(f"  {note}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Headless AI-vs-AI simulation harness")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", choices=["random", "symmetric"], default="random")
    ap.add_argument("--nodes", type=int, default=config.DEFAULT_NODES)
    ap.add_argument("--players", type=int, default=None, help=f"seat count (defaults to the number of --ai names, else {config.DEFAULT_PLAYERS})")
    ap.add_argument(
        "--ai",
        nargs="+",
        metavar="NAME",
        help="strategy per seat, in order: a models/ file stem or 'heuristic'; sets the player count when --players is omitted",
    )
    ap.add_argument("--swap", action="store_true", help="free-for-all: rotate the roster through every seat (cancels start bias) and rank by total wins per strategy")
    ap.add_argument("--ladder", action="store_true", help="pairwise round-robin: every pair head-to-head, both seatings, ranked with a matchup grid")
    ap.add_argument("--max-turns", type=int, default=600)
    ap.add_argument("--trials", type=int, default=1, help="run seeds [seed .. seed+trials)")
    ap.add_argument("--bot-timeout", type=float, default=0.0, help="wall-clock seconds allowed per decide() call (0 = disabled)")
    ap.add_argument("--external", action="store_true", help="also register the bots/ external bots (subprocesses speaking docs/bot-api.md); off by default, since a subprocess in an unasked-for ladder is a surprise")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.swap and args.ladder:
        ap.error("--swap and --ladder are different tournaments; pick one")

    strategies = args.ai
    if strategies:
        ai.load_models()  # register drop-in models/ strategies before we name them
        _register_external(args)
        unknown = [n for n in strategies if n not in ai.STRATEGIES]
        if unknown:
            ap.error(f"unknown strategy: {', '.join(unknown)}. available: {', '.join(ai.available_strategies())}")
    elif args.swap or args.ladder:
        # No roster named: rank everything registered, so a tournament over the
        # whole models/ dir needs no arguments at all.
        ai.load_models()
        _register_external(args)
        strategies = ai.available_strategies()

    if args.swap or args.ladder:
        flag = "--swap" if args.swap else "--ladder"
        seats = 2 if args.ladder else len(strategies)
        if len(strategies) < 2:
            ap.error(f"{flag} needs at least 2 strategies; only found: {', '.join(strategies)}")
        if args.players is not None and args.players != seats:
            ap.error(f"{flag} sets the seat count itself; omit --players (it is forced to {seats})")
        seeds = range(args.seed, args.seed + args.trials)
        if args.ladder:
            games = run_ladder(seeds, args.mode, args.nodes, strategies, args.max_turns, args.bot_timeout)
            _summarise_ladder(games, strategies, args.trials)
        else:
            games = run_swap(seeds, args.mode, args.nodes, strategies, args.max_turns, args.bot_timeout)
            _summarise_swap(games, strategies, args.trials)
        return

    if args.players is not None:
        players = args.players
    elif strategies:
        players = len(strategies)
    else:
        players = config.DEFAULT_PLAYERS

    if args.trials > 1:
        seeds = range(args.seed, args.seed + args.trials)
        results = run_trials(seeds, args.mode, args.nodes, players, args.max_turns, strategies, args.bot_timeout)
        _summarise(results, strategies)
    else:
        r = play(args.seed, args.mode, args.nodes, players, args.max_turns, args.verbose, strategies, args.bot_timeout)
        if r.winner == 0:
            winner = "draw"
        elif r.winner is None:
            winner = "TIMEOUT"
        else:
            winner = _seat_label(r.winner, strategies)
        print(f"\nseed {r.seed}: winner={winner} in {r.turns} turns")


if __name__ == "__main__":
    main()
