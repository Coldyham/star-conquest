#!/usr/bin/env python3
"""Generate this week's campaign meta-map and store it in ``public.campaigns``.

The campaign (``leaderboard/campaign.html``) is a weekly map of challenges. Every
node is an unplayed seed on a config people already play, so it is known to be
playable. A ring of *home* systems, one lane each from an edge node, is how a
player gets on: win a home's challenge and it is yours for the week, never to be
stolen. From there you take a field node by winning it while holding a
neighbour, and someone else's node only by beating their score on it.

Only the week's *definition* is stored here, because only it cannot be derived:
which configs existed and which seeds were free are facts about the moment it
was made, the "?" nodes are random rolls, and the layout comes from the game's
own ``mapgen``. Who holds what is a replay of the week's posted scores, done by
the page (``leaderboard/js/campaign.mjs``) over the ``campaign_scores`` view.

Runs hourly on the same job as the bot column and does nothing once the week's
row exists, so a missed hour can never skip a week:

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...
    uv run python tools/campaign.py              # this week's, if not made yet
    uv run python tools/campaign.py --dry-run    # print it, store nothing
    uv run python tools/campaign.py --week 2026-10-05

Pure of pygame, like the rest of the worker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import config, mapgen
from starconquest.settings import (
    ADV_ECON, ADV_FOG, ADV_MAP, ADV_TRAVEL, RANDOM_STRATEGY, Settings, apply_globals,
    build_state, randomise_knobs,
)
from tools.bot_replay import MISSING_CREDENTIALS, ApiError, Supabase, credentials

GRAPH_VERSION = 1
FIELD_PER_PLAYER = 4          # field nodes per player active last week...
FIELD_MIN, FIELD_MAX = 12, 40 # ...within these bounds (40 keeps mapgen's standard box)
HOMES_MIN = 4                 # homes: last week's players + HOMES_SPARE, at least this
HOMES_SPARE = 2
MYSTERY_MIN, MYSTERY_MAX = 1, 2
SYMMETRIC_SHARE = 0.2         # a config whose symmetric plays are under this share
SYMMETRIC_CHANCE = 0.25       # ...of its random ones may appear symmetric instead
HOME_OFFSET = 110.0           # world units a home sits outside its edge node
MYSTERY_MAX_NODES = config.STANDARD_MAX_NODES
SEED_TRIES = 50


def week_start(now: dt.datetime) -> dt.date:
    """The Monday (UTC) of the week ``now`` falls in."""
    day = now.astimezone(dt.timezone.utc).date()
    return day - dt.timedelta(days=day.weekday())


def field_count(active: int) -> int:
    return max(FIELD_MIN, min(FIELD_MAX, FIELD_PER_PLAYER * active))


def home_count(active: int) -> int:
    return max(HOMES_MIN, active + HOMES_SPARE)


def family_key(setup: dict) -> str:
    """A config's identity for choosing nodes: the setup minus seed, autoplay and
    mode, so a config's random and symmetric versions share one family. Only used
    here, to pick; matching a node to its games is ``sc_config_key``'s job."""
    rest = {k: v for k, v in setup.items() if k not in ("seed", "autoplay", "mode", "challenge")}
    return json.dumps(rest, sort_keys=True, separators=(",", ":"))


@dataclass
class Family:
    setup: dict                  # a representative settings_json
    plays: dict[str, int]        # games per mode


def families(games: list[dict], before: dt.date) -> list[Family]:
    """Every config family with a map registered before ``before``, hand-drawn
    maps excluded (their seed changes only the star names and the dice)."""
    cutoff = dt.datetime.combine(before, dt.time(), dt.timezone.utc)
    out: dict[str, Family] = {}
    for row in games:
        setup = row.get("settings_json") or {}
        if setup.get("custom_map"):
            continue
        seen = dt.datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00"))
        if seen >= cutoff:
            continue
        key = family_key(setup)
        family = out.setdefault(key, Family(setup=setup, plays={}))
        mode = setup.get("mode", "random")
        family.plays[mode] = family.plays.get(mode, 0) + 1
    return sorted(out.values(), key=lambda f: family_key(f.setup))


def _fresh_seed(rng: random.Random, taken: set[tuple[str, int]], mode: str) -> int:
    for _ in range(SEED_TRIES):
        seed = rng.randrange(config.SEED_MAX)
        if (mode, seed) not in taken:
            taken.add((mode, seed))
            return seed
    raise RuntimeError("no free seed found")   # a million seeds: never in practice


def _node_setup(settings: Settings, seed: int) -> tuple[dict, int]:
    """The node's setup in the exact pruned form a posted link stores in
    ``games.settings_json``, and how many systems it really has (a symmetric map
    can round up). Building it also proves it plays."""
    settings.seed, settings.autoplay, settings.challenge = seed, False, None
    state = build_state(settings, seed)
    return settings.token_dict(), len(state.systems)


def _from_family(family: Family, rng: random.Random,
                 taken: set[tuple[str, int]]) -> tuple[dict, int]:
    settings = Settings.from_dict(dict(family.setup))
    if family.plays:   # the mode it is usually played in, not whichever row came first
        modes = sorted(family.plays)
        settings.mode = rng.choices(modes, weights=[family.plays[m] for m in modes])[0]
    random_plays = family.plays.get("random", 0)
    if (settings.mode == "random"
            and family.plays.get("symmetric", 0) < SYMMETRIC_SHARE * random_plays
            and rng.random() < SYMMETRIC_CHANCE):
        settings.mode = "symmetric"
    if settings.mode == "symmetric" and "layout" not in family.setup:
        # Nobody chose how this config's sectors meet (a random config turned
        # symmetric, or the default hub), so roll it; a layout people do play
        # is its own family and keeps its own.
        settings.layout = _symmetric_layout(rng)
    return _node_setup(settings, _fresh_seed(rng, taken, settings.mode))


def _symmetric_layout(rng: random.Random) -> str:
    """How a symmetric node's sectors meet, any of ``mapgen.SYMMETRIC_LAYOUTS``
    alike, so the week shows them all rather than only the default hub."""
    return rng.choice(mapgen.SYMMETRIC_LAYOUTS)


def _mystery(rng: random.Random, taken: set[tuple[str, int]]) -> tuple[dict, int]:
    """A "?" node: default settings with every Advanced knob rolled, and random
    players, systems, map type and opponents."""
    settings = Settings.defaults()
    randomise_knobs(settings, ADV_MAP + ADV_TRAVEL + ADV_ECON + ADV_FOG, rng)
    settings.players = rng.randint(config.MIN_PLAYERS, config.MAX_PLAYERS)
    settings.nodes = rng.randint(settings.min_nodes(), MYSTERY_MAX_NODES)
    settings.mode = rng.choice(("random", "symmetric"))
    if settings.mode == "symmetric":
        settings.layout = _symmetric_layout(rng)
    settings.ai_strategy = [RANDOM_STRATEGY] * config.MAX_PLAYERS
    return _node_setup(settings, _fresh_seed(rng, taken, settings.mode))


def _layout(n_field: int, n_homes: int, rng: random.Random):
    """Field positions and lanes from the game's own planar mapgen, plus a ring of
    homes: one per edge node ``peripheral_starts`` picks, pushed outward from the
    centre, each with a single lane to its edge node."""
    apply_globals(Settings.defaults())   # mapgen reads the knobs live off config
    board = mapgen.generate(rng.randrange(config.SEED_MAX), "random", n_field, 2)
    pos = {sid: s.pos for sid, s in board.systems.items()}
    lanes = sorted(tuple(sorted((lane.a, lane.b))) for lane in board.lanes.values())
    edges = mapgen.peripheral_starts(pos, min(n_homes, n_field), rng)
    cx = sum(p[0] for p in pos.values()) / len(pos)
    cy = sum(p[1] for p in pos.values()) / len(pos)
    homes = []
    for edge in edges:
        ex, ey = pos[edge]
        length = math.hypot(ex - cx, ey - cy) or 1.0
        homes.append((edge, (ex + (ex - cx) / length * HOME_OFFSET,
                             ey + (ey - cy) / length * HOME_OFFSET)))
    return pos, lanes, homes


def build_campaign(start: dt.date, games: list[dict], active: int,
                   rng: random.Random) -> dict:
    """The week's graph, as stored in ``campaigns.graph``. Pure given its inputs."""
    n_field, n_homes = field_count(active), home_count(active)
    pos, lanes, homes = _layout(n_field, n_homes, rng)   # before any build_state tunes config

    pool = families(games, start)
    # Every (mode, seed) on the board, whatever its config: stricter than per
    # config, and a million seeds make the difference moot.
    taken = {(str(row.get("settings_json", {}).get("mode", "random")), int(row["seed"]))
             for row in games}
    fallback = Family(setup=Settings.defaults().token_dict(), plays={})
    total = n_field + len(homes)
    mystery = set(rng.sample(range(n_field), rng.randint(MYSTERY_MIN, MYSTERY_MAX)))

    nodes = []
    for index in range(total):
        is_home = index >= n_field
        if index in mystery:
            setup, systems = _mystery(rng, taken)
        else:
            setup, systems = _from_family(rng.choice(pool) if pool else fallback, rng, taken)
        x, y = homes[index - n_field][1] if is_home else pos[index]
        nodes.append({
            "id": index,
            "kind": "home" if is_home else "field",
            "x": round(x), "y": round(y),
            "systems": systems,
            "mystery": index in mystery,
            "settings": setup,
        })
    lanes = [list(pair) for pair in lanes]
    lanes += [[edge, n_field + i] for i, (edge, _p) in enumerate(homes)]
    return {"version": GRAPH_VERSION, "week_start": start.isoformat(), "nodes": nodes,
            "lanes": lanes}


def active_players(api: Supabase, start: dt.date) -> int:
    """Distinct players with a counted score in the week before ``start``."""
    lo, hi = (start - dt.timedelta(days=7)).isoformat(), start.isoformat()
    rows = api.select("counted_scores",   # dates: a '+00:00' offset would need escaping
                      f"select=user_id&submitted_at=gte.{lo}&submitted_at=lt.{hi}&order=id.asc")
    return len({row["user_id"] for row in rows})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--week", help="the week's Monday (YYYY-MM-DD); default this week")
    parser.add_argument("--dry-run", action="store_true", help="print the graph, store nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url, key = credentials()
    if not url or not key:
        print(MISSING_CREDENTIALS, file=sys.stderr)
        return 2
    start = (week_start(dt.datetime.fromisoformat(args.week).replace(tzinfo=dt.timezone.utc))
             if args.week else week_start(dt.datetime.now(dt.timezone.utc)))
    api = Supabase(url, key)
    if api.select("campaigns", f"select=week_start&week_start=eq.{start.isoformat()}"):
        print(f"campaign for {start} already exists — nothing to do")
        return 0

    games = api.select("games", "select=game_key,settings_json,seed,first_seen_at&order=game_key.asc")
    active = active_players(api, start)
    graph = build_campaign(start, games, active, random.Random())
    fields = sum(1 for n in graph["nodes"] if n["kind"] == "field")
    print(f"campaign {start}: {active} active last week -> {fields} field nodes, "
          f"{len(graph['nodes']) - fields} homes, {len(graph['lanes'])} lanes")
    if args.dry_run:
        print(json.dumps(graph, indent=1)[:4000])
        print("dry run — nothing written")
        return 0
    try:
        api.insert("campaigns", [{"week_start": start.isoformat(), "graph": graph}])
    except ApiError as err:
        if "409" in str(err) or "23505" in str(err):   # another run beat us to it
            print(f"campaign for {start} was written by another run")
            return 0
        raise
    print("stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
