#!/usr/bin/env python3
"""Which setups people actually play, from the board's public tables.

    uv run python tools/config_census.py                 # the live board
    uv run python tools/config_census.py --no-maps       # skip lane timing
    uv run python tools/config_census.py --csv out.csv   # a row per game, for later

Every bot in ``models/`` is fitted at whatever knobs the person fitting it
happened to pick, and "Sweep the speed and node knobs" in ``docs/bot-design.md``
is the standing warning about what that costs: ``WORLD_SIZE`` is fixed, so a
lane's length in light-years rises as the node count falls, and
``config.SHIP_LY_PER_TURN`` rescales every lane on top. A margin keyed off travel
distance is therefore live in one part of that space and unreachable in another.
This says which part the people playing are actually in, so a sweep can be aimed
there rather than at the defaults.

It needs no credentials, which is the point. ``games`` and ``scores`` carry a
public read policy (``leaderboard/schema.sql``) and a game row's
``settings_json`` is the entire setup, so "what is being played" is answerable
with the publishable key that already ships in ``leaderboard/js/config.mjs`` —
read from there rather than copied, so there is still one of it. ``game_logs``,
the replays themselves, is the table that needs the secret key, and nothing here
touches it.

Two things this is not:

* **A census of games played.** A ``games`` row exists because somebody posted a
  score, so every abandoned and lost match is invisible and the sample leans
  toward setups people finished. The uploaded replays are where the rest are, and
  those are behind the secret key.
* **Missing-data reporting.** ``settings_json`` is pruned to non-defaults on the
  way in, so an absent knob means the default. Every count below folds absent
  into the default value, which is why a knob can read 22/22 at its default
  without a single game having named it.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import settings as settings_mod  # noqa: E402
from starconquest.settings import Settings  # noqa: E402
from tools.bot_replay import Supabase  # noqa: E402 — the same paged PostgREST client

# Where the board keeps its own project details. The anon key is public by
# design (RLS is the boundary, not the key), so this is a read of a committed
# constant rather than a credential lookup.
CONFIG_MJS = ROOT / "leaderboard" / "js" / "config.mjs"

# The structural fields, which every row carries, ahead of the balance knobs.
STRUCTURE = ("mode", "players", "nodes")


def anon_credentials() -> tuple[str, str]:
    """``(url, key)`` for the public API, out of the leaderboard's own config."""
    try:
        text = CONFIG_MJS.read_text(encoding="utf-8")
    except OSError as err:
        raise SystemExit(f"Can't read {CONFIG_MJS}: {err}")

    def const(name: str) -> str:
        match = re.search(rf'{name}\s*=\s*"([^"]*)"', text)
        return match.group(1).strip() if match else ""

    url, key = const("SUPABASE_URL"), const("SUPABASE_ANON_KEY")
    if not url or not key:
        raise SystemExit(f"{CONFIG_MJS} has no SUPABASE_URL/SUPABASE_ANON_KEY to read.")
    return url, key


def fetch(url: str, key: str) -> tuple[list[dict], list[dict]]:
    """Every public game row and score row. Ordered, because ``select`` pages."""
    api = Supabase(url, key)
    games = api.select("games", "select=game_key,mode,players,nodes,seed,settings_json"
                                "&order=game_key.asc")
    scores = api.select("scores", "select=game_key,user_id,turns,hand&order=id.asc")
    return games, scores


def opponents(config: Settings) -> tuple[str, ...]:
    """The bots seat 1 faces, mirroring the board's ``sc_bots``.

    Seats 2..players, each defaulting to the built-in heuristic, sorted and
    deduplicated — a roster rather than a seating, since which seat drew which
    bot is not what a census is about.
    """
    names = [config.seat_strategy(seat) or "heuristic"
             for seat in range(2, max(config.players, 1) + 1)]
    return tuple(sorted(set(names)))


def median_lane_turns(config: Settings, seed: int) -> float | None:
    """The median lane's travel time on this setup's actual map, in turns.

    Generated rather than estimated: the whole reason this number is interesting
    is that it moves with the node count *and* the speed knob at once, and only
    the real map knows where a seed put the systems. Through ``build_state``,
    which is the one funnel that pushes a stored setup's tuned knobs into
    ``config`` before generating (see ``tools/bot_replay.py``, same reason).
    """
    try:
        state = settings_mod.build_state(config, seed)
    except Exception:                      # a setup the current mapgen won't build
        return None
    turns = sorted(lane.travel_turns for lane in state.lanes.values())
    if not turns:
        return None
    mid = len(turns) // 2
    return float(turns[mid]) if len(turns) % 2 else (turns[mid - 1] + turns[mid]) / 2


def census(games: list[dict], scores: list[dict], with_maps: bool) -> list[dict]:
    """One folded row per game: defaults filled in, opponents and map resolved."""
    plays = collections.Counter(str(row.get("game_key", "")) for row in scores)
    rows = []
    for game in games:
        key = str(game.get("game_key", ""))
        config = Settings.from_dict(game.get("settings_json") or {})
        seed = game.get("seed")
        seed = int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else None
        rows.append({
            "game_key": key,
            "scores": plays.get(key, 0),
            "config": config,
            "seed": seed,
            "bots": opponents(config),
            "lane_turns": (median_lane_turns(config, seed)
                           if with_maps and seed is not None else None),
        })
    return rows


def _tally(rows: list[dict], value) -> list[tuple[str, int, int]]:
    """``(value, games, scores)`` for one field, commonest first."""
    games: collections.Counter = collections.Counter()
    scores: collections.Counter = collections.Counter()
    for row in rows:
        label = value(row)
        games[label] += 1
        scores[label] += row["scores"]
    return [(label, count, scores[label])
            for label, count in sorted(games.items(), key=lambda kv: (-kv[1], str(kv[0])))]


def _fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def report(rows: list[dict], with_maps: bool) -> None:
    total = len(rows)
    played = sum(row["scores"] for row in rows)
    print(f"{total} games · {played} scores\n")

    print("Setup")
    for field in STRUCTURE:
        for label, games, scores in _tally(rows, lambda r, f=field: getattr(r["config"], f)):
            print(f"  {field:<22} {_fmt(label):<10} {games:>3} games  {scores:>3} scores")
    for label, games, scores in _tally(rows, lambda r: ", ".join(r["bots"]) or "—"):
        print(f"  {'opponents':<22} {label:<10} {games:>3} games  {scores:>3} scores")

    print("\nBalance knobs (absent from a row means the default, and is counted as it)")
    untouched = []
    for field, _const in settings_mod._GLOBAL_KNOBS:
        counts = _tally(rows, lambda r, f=field: getattr(r["config"], f))
        default = getattr(Settings(), field)
        if len(counts) == 1:
            untouched.append(field)
            continue
        for label, games, scores in counts:
            mark = "  (default)" if label == default else ""
            print(f"  {field:<22} {_fmt(label):<10} {games:>3} games  {scores:>3} scores{mark}")
    if untouched:
        print(f"\n  At one value on every game: {', '.join(untouched)}")

    if with_maps:
        timed = [row for row in rows if row["lane_turns"] is not None]
        print("\nLane travel time (median lane of the real map, in turns)")
        if not timed:
            print("  no map could be generated")
        else:
            for label, games, scores in _tally(timed, lambda r: r["lane_turns"]):
                print(f"  {'median lane':<22} {_fmt(label):<10} {games:>3} games  {scores:>3} scores")
            spread = sorted(row["lane_turns"] for row in timed)
            print(f"  spanning {_fmt(spread[0])}–{_fmt(spread[-1])} turns across "
                  f"{len(timed)} of {total} games")


def write_csv(rows: list[dict], path: Path) -> None:
    knobs = [field for field, _ in settings_mod._GLOBAL_KNOBS]
    header = ["game_key", "scores", *STRUCTURE, "seed", "bots", "lane_turns", *knobs]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            config = row["config"]
            writer.writerow([
                row["game_key"], row["scores"],
                *[getattr(config, f) for f in STRUCTURE], row["seed"],
                " ".join(row["bots"]), row["lane_turns"],
                *[getattr(config, knob) for knob in knobs],
            ])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="", help="override the board's Supabase URL")
    parser.add_argument("--key", default="", help="override the publishable key")
    parser.add_argument("--no-maps", action="store_true",
                        help="skip generating each map to time its lanes")
    parser.add_argument("--csv", type=Path, default=None,
                        help="also write a row per game here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url, key = anon_credentials()
    url, key = args.url or url, args.key or key

    games, scores = fetch(url, key)
    if not games:
        print("The board has no games yet.", file=sys.stderr)
        return 1

    rows = census(games, scores, with_maps=not args.no_maps)
    report(rows, with_maps=not args.no_maps)
    if args.csv:
        write_csv(rows, args.csv)
        print(f"\nWrote {args.csv} ({len(rows)} rows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
