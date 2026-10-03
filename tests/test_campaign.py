"""The weekly campaign generator (tools/campaign.py), offline: no database."""

from __future__ import annotations

import datetime as dt
import random
from collections import deque

from starconquest import config
from starconquest.settings import Settings, build_state
from tools import campaign

START = dt.date(2026, 9, 28)   # a Monday


def _game(key: str, seed: int, first_seen: str = "2026-09-01T00:00:00+00:00", **changes) -> dict:
    settings = Settings.defaults()
    for attr, value in changes.items():
        setattr(settings, attr, value)
    settings.seed = seed
    return {"game_key": key, "settings_json": settings.token_dict(), "seed": seed,
            "first_seen_at": first_seen}


GAMES = [_game("a", 1), _game("b", 2, players=4, nodes=24), _game("c", 3, mode="symmetric")]


def _graph(active: int = 3, games=GAMES, seed: int = 7) -> dict:
    return campaign.build_campaign(START, games, active, random.Random(seed))


def _neighbours(graph: dict) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {n["id"]: set() for n in graph["nodes"]}
    for a, b in graph["lanes"]:
        out[a].add(b)
        out[b].add(a)
    return out


def test_week_start_is_the_monday_in_utc():
    sunday_late = dt.datetime(2026, 10, 4, 23, 59, tzinfo=dt.timezone.utc)
    assert campaign.week_start(sunday_late) == START
    assert campaign.week_start(sunday_late + dt.timedelta(minutes=1)) == dt.date(2026, 10, 5)


def test_the_map_scales_with_last_weeks_players():
    assert campaign.field_count(0) == campaign.FIELD_MIN
    assert campaign.field_count(5) == 20
    assert campaign.field_count(100) == campaign.FIELD_MAX
    assert campaign.home_count(0) == campaign.HOMES_MIN
    assert campaign.home_count(6) == 8
    graph = _graph(active=5)
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds.count("field") == 20 and kinds.count("home") == 7


def test_the_graph_is_connected_and_every_home_hangs_off_one_edge_node():
    graph = _graph()
    links = _neighbours(graph)
    seen, queue = {0}, deque([0])
    while queue:
        for nxt in links[queue.popleft()] - seen:
            seen.add(nxt)
            queue.append(nxt)
    assert seen == set(links)
    kind = {n["id"]: n["kind"] for n in graph["nodes"]}
    for node in graph["nodes"]:
        if node["kind"] == "home":
            (edge,) = links[node["id"]]
            assert kind[edge] == "field"


def test_no_node_reuses_a_seed_on_the_board_or_another_node():
    graph = _graph()
    seeds = [(n["settings"].get("mode", "random"), n["settings"]["seed"]) for n in graph["nodes"]]
    assert len(seeds) == len(set(seeds))
    board = {(g["settings_json"].get("mode", "random"), g["seed"]) for g in GAMES}
    assert not board & set(seeds)


def test_every_node_builds_and_reports_its_real_system_count():
    for node in _graph()["nodes"]:
        settings = Settings.from_dict(node["settings"])
        assert settings.custom_map is None and not settings.autoplay
        assert len(build_state(settings, settings.seed).systems) == node["systems"]


def test_there_are_one_or_two_mystery_nodes_and_only_in_the_field():
    for seed in range(5):
        graph = _graph(seed=seed)
        mystery = [n for n in graph["nodes"] if n["mystery"]]
        assert campaign.MYSTERY_MIN <= len(mystery) <= campaign.MYSTERY_MAX
        assert all(n["kind"] == "field" for n in mystery)


def test_hand_drawn_and_too_new_configs_are_never_picked():
    from starconquest.custommap import CustomMap, MapNode
    drawn = Settings.defaults()
    drawn.custom_map = CustomMap(
        nodes=[MapNode(100, 100, 3, 12, 1), MapNode(400, 100, 3, 12, 2),
               MapNode(250, 400, 4, 5, 0)],
        lanes=[(0, 1), (1, 2), (0, 2)])
    drawn.seed = 9
    hand = {"game_key": "h", "settings_json": drawn.token_dict(), "seed": 9,
            "first_seen_at": "2026-09-01T00:00:00+00:00"}
    late = _game("late", 4, first_seen="2026-09-28T00:00:01+00:00", players=5)
    pool = campaign.families([hand, late, *GAMES], START)
    assert len(pool) == 2   # a config's random and symmetric versions are one family
    assert all(not f.setup.get("custom_map") and f.setup.get("players", 3) != 5 for f in pool)


def test_an_empty_board_still_makes_a_playable_week():
    graph = _graph(games=[])
    assert graph["nodes"] and all(n["systems"] > 0 for n in graph["nodes"])


def test_generation_is_deterministic_given_the_rng():
    assert _graph(seed=3) == _graph(seed=3)


def test_the_layout_ignores_whatever_knobs_a_previous_build_left_in_config():
    before = config.NODE_JITTER
    try:
        first = _graph(seed=11)
        config.NODE_JITTER = 0.1
        assert _graph(seed=11) == first
    finally:
        config.NODE_JITTER = before


def test_a_symmetric_node_rolls_how_its_sectors_meet():
    from starconquest import mapgen
    seen = set()
    for seed in range(40):
        for node in _graph(seed=seed)["nodes"]:
            setup = node["settings"]
            if setup.get("mode") == "symmetric":
                seen.add(setup.get("layout", mapgen.SYMMETRIC_LAYOUTS[0]))
    assert seen == set(mapgen.SYMMETRIC_LAYOUTS)


def test_a_layout_people_play_is_kept():
    ring = [_game("r", 5, mode="symmetric", layout="ring")]
    nodes = [n for n in _graph(games=ring)["nodes"] if n["kind"] == "home"]
    for node in nodes:   # homes are never "?" nodes, so every one is that family
        assert node["settings"]["layout"] == "ring"
