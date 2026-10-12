#!/usr/bin/env python3
"""Play the human seat of a setup one turn at a time from the command line.

    uv run python tools/seat_play.py new --key 5f5b34a74b637646   # a posted map
    uv run python tools/seat_play.py new --token eNrN...          # a challenge link
    uv run python tools/seat_play.py show                         # what the seat sees
    uv run python tools/seat_play.py turn 2>6:12 9>6:3 --why "mass on the rich 6"
    uv run python tools/seat_play.py wait 4 --why "fleet in flight"
    uv run python tools/seat_play.py fight 12 9                   # price a strike
    uv run python tools/seat_play.py notes                        # the decisions so far

Built so a model (or a person at a terminal) can play real setups by hand and
have the games feed the same tools a played game does. The match is a real
`replay.GameLog` in the local ``games/`` dir: `position_suite`, `bot_distance`,
`human_habits` and the game's own history and Watch read it like any other, and
every call rebuilds the board from it with `replay.reconstruct`, so nothing here
can drift from what the engine would replay. Turns are resolved the way
`main.resolve_turn` does it (`replay.reseed`, then `engine.end_turn` with the
seat's orders and `ai.decide` for every other seat).

The view honours the setup's fog the way the shell does: full detail only for
systems in sight (`fog.observe`), a seen system out of sight is a "?" with
only its production (the size the map draws it at), a lane is listed once both
ends have been seen and its travel time only while one end is in sight, a rival fleet only while its source or destination is in sight, and a
rival's totals are the last ones taken while one of its systems was in view.
Opponent seats are named as the setup names them, so a random seat stays
"random", as on the menu.

Each turn's reasons go in a sidecar, ``<log>.notes.jsonl`` (turn, orders, why),
which is the part meant to be distilled into a bot later. The log itself stays
exactly what the game writes. A game is found by ``--log``, else the newest log
that has a sidecar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, combat, config, engine, fog, replay  # noqa: E402
from starconquest.model import GameState, Order  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

NOTES_SUFFIX = ".notes.jsonl"


# --------------------------------------------------------------------------- #
# The game on disk
# --------------------------------------------------------------------------- #
def notes_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.stem + NOTES_SUFFIX)


def find_log(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for path in replay.list_logs():
        if notes_path(path).exists():
            return path
    raise SystemExit("No seat_play game found; start one with `new` or pass --log.")


def settings_for_key(key: str) -> Settings:
    """A posted map's setup, read from the public `game_summary` view."""
    from tools.config_census import public_rows

    rows = public_rows("game_summary", f"select=settings_json&game_key=eq.{key}&limit=1")
    if not rows:
        raise SystemExit(f"No posted map with key {key}.")
    return Settings.from_dict(rows[0]["settings_json"])


def par_for_key(key: str) -> list[str]:
    """The scores to beat on a posted map: the best person, then each bot."""
    from tools.config_census import public_rows

    lines = []
    for row in public_rows("game_summary", f"select=best_turns,best_lost&game_key=eq.{key}"):
        if row.get("best_turns"):
            lines.append(f"best person: {row['best_turns']} turns, {row['best_lost']} lost")
    bots = public_rows("bot_scores", f"select=bot,won,turns,lost,aux_label&game_key=eq.{key}")
    for row in sorted(bots, key=lambda r: (not r["won"], r["turns"], r["lost"])):
        knob = f" ({row['aux_label']})" if row.get("aux_label") else ""
        result = f"{row['turns']} turns, {row['lost']} lost" if row["won"] else "lost"
        lines.append(f"{row['bot']}{knob}: {result}")
    return lines


def rebuild(log_path: Path) -> tuple[replay.GameLog, GameState, Settings, set[int], dict]:
    """The board after every recorded turn, plus the seat's fog memory: every
    system ever seen and each rival's last sighted totals, folded turn by turn
    as the shell folds them (`main._accumulate_fog`)."""
    log = replay.load(log_path)
    seen: set[int] = set()
    intel: dict[int, tuple[int, int, float]] = {}

    def fold(state: GameState) -> None:
        me = _seat(state)
        visible, scouted = fog.observe(state, me, config.FOG_SIGHT, config.FOG_SCOUT)
        seen.update(visible | scouted)
        for pid, player in state.players.items():
            if player.is_neutral or pid == me:
                continue
            if any(state.systems[s].owner_id == pid for s in visible):
                intel[pid] = fog.player_totals(state, pid)

    state, cfg = replay.reconstruct(log, on_turn=fold)
    return log, state, cfg, seen, intel


def _seat(state: GameState) -> int:
    human = state.human()
    if human is None:
        raise SystemExit("This setup has no human seat to play.")
    return human.id


# --------------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------------- #
def render(state: GameState, cfg: Settings, seen: set[int], intel: dict) -> str:
    me = _seat(state)
    if state.is_defeated(me) or state.winner is not None:
        visible, scouted = set(state.systems), set()
    else:
        visible, scouted = fog.observe(state, me, config.FOG_SIGHT, config.FOG_SCOUT)
    fogged = len(visible) < len(state.systems)
    out = [f"TURN {state.turn}" + (f"   WINNER: P{state.winner}" if state.winner is not None else "")
           + ("   (fog)" if fogged else "")]

    out.append("PLAYERS")
    for pid, player in sorted(state.players.items()):
        if player.is_neutral:
            continue
        strategy = cfg.ai_strategy[pid - 1] if pid - 1 < len(cfg.ai_strategy) else player.ai_strategy
        if pid == me:
            systems, ships, prod = fog.player_totals(state, pid)
            label, tag = "YOU", ""
        elif not fogged or not player.alive:
            systems, ships, prod = fog.player_totals(state, pid)
            label, tag = strategy, ""
        elif pid in intel:
            (systems, ships, prod), label, tag = intel[pid], strategy, "  (last sighted)"
        else:
            out.append(f"  P{pid} {strategy}: never sighted")
            continue
        status = "" if player.alive else "  OUT"
        out.append(f"  P{pid} {label}: {systems} systems, {ships} ships, "
                   f"{prod:.2f} ships/turn, lost {player.ships_lost}{tag}{status}")

    # As `render` draws it: a system is off the map until first seen, then a
    # "?" sized by its production; a lane shows once both ends have been seen,
    # and its travel time only while one end is in sight.
    known = seen | visible | scouted
    out.append(f"SYSTEMS  id owner ships every(progress)  lanes id:turns, landing on turn "
               f"{state.turn}+turns if sent now   (? = not in sight)")
    for sid in sorted(known):
        system = state.systems[sid]
        lanes = " ".join(
            f"{n}:{state.travel_turns(sid, n) if sid in visible or n in visible else '?'}"
            for n in system.neighbors if n in known)
        if sid in visible:
            owner = "ME" if system.owner_id == me else f"P{system.owner_id}"
            detail = f"{owner:>3} {system.ships:5d}  {system.production}({system.prod_progress})"
        else:
            detail = f"{'?':>3} {'?':>5}  {system.production}(?)"
        out.append(f"  {sid:3d} {detail:<18} | {lanes}")

    out.append("FLEETS")
    shown = [f for f in state.fleets
             if f.owner_id == me or f.source_id in visible or f.dest_id in visible]
    for fleet in sorted(shown, key=lambda f: (f.turns_remaining, f.dest_id)):
        who = "ME" if fleet.owner_id == me else f"P{fleet.owner_id}"
        out.append(f"  {who} {fleet.source_id}->{fleet.dest_id} x{fleet.ships} "
                   f"lands on turn {state.turn + fleet.turns_remaining}")
    if not shown:
        out.append("  (none in sight)")
    return "\n".join(out)


def fight_text(attacker: int, defender: int) -> str:
    preview = combat.preview_fight(attacker, defender, config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE)

    def say(roll) -> str:
        return f"{'win' if roll.winner == combat.ATTACKER else 'LOSE'} {roll.survivors}"

    return (f"{attacker} on {defender}: worst {say(preview.worst)}, nominal {say(preview.nominal)}, "
            f"best {say(preview.best)}  (jitter {config.COMBAT_JITTER}, "
            f"defender x{config.DEFENDER_ADVANTAGE}, sure at {combat.edge_attacking():.3f}x)")


# --------------------------------------------------------------------------- #
# Playing a turn
# --------------------------------------------------------------------------- #
def parse_orders(tokens: list[str], me: int) -> list[Order]:
    orders = []
    for token in tokens:
        try:
            src, rest = token.split(">")
            dest, ships = rest.split(":")
            orders.append(Order(me, int(src), int(dest), int(ships)))
        except ValueError:
            raise SystemExit(f"Can't read order {token!r}; write it SRC>DEST:SHIPS.")
    return orders


def check_orders(state: GameState, orders: list[Order]) -> None:
    """Refuse what the game's own controls would never send, rather than let
    the engine quietly drop it: a typo should cost a retype, not a turn."""
    sending: dict[int, int] = {}
    for order in orders:
        source = state.systems.get(order.source_id)
        if source is None or source.owner_id != order.owner_id:
            raise SystemExit(f"{order.source_id} is not yours.")
        if order.dest_id not in source.neighbors:
            raise SystemExit(f"No lane {order.source_id}->{order.dest_id}.")
        if order.ships <= 0:
            raise SystemExit(f"Send at least one ship from {order.source_id}.")
        sending[order.source_id] = sending.get(order.source_id, 0) + order.ships
    for sid, ships in sending.items():
        if ships > state.systems[sid].ships:
            raise SystemExit(f"{sid} holds {state.systems[sid].ships}, not {ships}.")


def play_turn(log: replay.GameLog, state: GameState, orders: list[Order]) -> None:
    replay.reseed(state)
    record = engine.end_turn(state, human_orders=orders, decide=ai.decide)
    log.record_turn(record)
    if state.winner is not None:
        log.mark_finished(state.winner)


def note(log_path: Path, turn: int, orders: list[str], why: str) -> None:
    with open(notes_path(log_path), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"turn": turn, "orders": orders, "why": why}) + "\n")


def _alarm(state: GameState, me: int, visible: set[int]) -> tuple:
    """What `wait` stops on a change of: what the seat owns, how many of its
    fleets are still flying (one fewer is one landed), and the rival fleets in
    sight bound for it."""
    mine = frozenset(s.id for s in state.systems.values() if s.owner_id == me)
    flying = sum(1 for f in state.fleets if f.owner_id == me)
    threats = frozenset((f.source_id, f.dest_id, f.ships) for f in state.fleets
                        if f.owner_id != me and f.dest_id in mine
                        and (f.source_id in visible or f.dest_id in visible))
    return mine, flying, threats


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", help="the game's log file (default: newest seat_play game)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="start a game")
    source = new.add_mutually_exclusive_group(required=True)
    source.add_argument("--key", help="a posted map's game key")
    source.add_argument("--token", help="a setup or challenge link's #fragment")
    sub.add_parser("show", help="the board as the seat sees it")
    turn = sub.add_parser("turn", help="end a turn with these orders (SRC>DEST:SHIPS)")
    turn.add_argument("orders", nargs="*")
    turn.add_argument("--why", default="")
    wait = sub.add_parser("wait", help="end up to N turns with no orders, stopping on news")
    wait.add_argument("n", type=int)
    wait.add_argument("--why", default="")
    fight = sub.add_parser("fight", help="price A ships attacking a garrison of D")
    fight.add_argument("attacker", type=int)
    fight.add_argument("defender", type=int)
    sub.add_parser("par", help="the scores to beat on this map")
    sub.add_parser("notes", help="every recorded decision")
    args = parser.parse_args(argv)
    ai.load_models()

    if args.cmd == "new":
        cfg = settings_for_key(args.key) if args.key else Settings.from_token(args.token.split("#")[-1])
        if args.key and cfg.challenge is None:
            from starconquest.settings import Challenge
            cfg.challenge = Challenge(key=args.key)
        state = build_state(cfg, cfg.seed)
        _seat(state)
        log = replay.new_log(cfg, cfg.seed, state)
        log.save()
        notes_path(log.path).write_text("", encoding="utf-8")
        print(f"log: {log.path}")
        print(render(state, cfg, set(), {}))
        return 0

    log_path = find_log(args.log)
    log, state, cfg, seen, intel = rebuild(log_path)
    me = _seat(state)

    if args.cmd == "show":
        print(render(state, cfg, seen, intel))
    elif args.cmd == "fight":
        print(fight_text(args.attacker, args.defender))
    elif args.cmd == "par":
        key = (log.settings.get("challenge") or {}).get("key", "")
        print("\n".join(par_for_key(key)) if key else "No posted map key on this game.")
    elif args.cmd == "notes":
        print(notes_path(log_path).read_text(encoding="utf-8"), end="")
    elif state.winner is not None:
        raise SystemExit(f"The game is over: P{state.winner} won on turn {state.turn}.")
    elif args.cmd == "turn":
        orders = parse_orders(args.orders, me)
        check_orders(state, orders)
        note(log_path, state.turn, args.orders, args.why)
        play_turn(log, state, orders)
        log.save()
        log, state, cfg, seen, intel = rebuild(log_path)
        print(render(state, cfg, seen, intel))
    elif args.cmd == "wait":
        note(log_path, state.turn, [f"wait {args.n}"], args.why)
        visible, _ = fog.observe(state, me, config.FOG_SIGHT, config.FOG_SCOUT)
        before = _alarm(state, me, visible)
        for _ in range(args.n):
            play_turn(log, state, [])
            if state.winner is not None:
                break
            visible, _ = fog.observe(state, me, config.FOG_SIGHT, config.FOG_SCOUT)
            if _alarm(state, me, visible) != before:
                break
        log.save()
        log, state, cfg, seen, intel = rebuild(log_path)
        print(render(state, cfg, seen, intel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
