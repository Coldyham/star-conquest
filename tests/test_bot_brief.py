"""The AI-assistant brief (`docs/bot-brief.md`) has to stay true.

It is written to be pasted into a chat, so it is the one document nobody will
diff against the code before relying on it — and a bot built from a stale brief
fails in the ways the brief was meant to prevent. So the starter bot in it is
executed and checked here, and the API it names is checked to exist.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from starconquest import ai, combat, engine, mapgen, model
from starconquest.model import AiParams, Fleet, GameState, Order, Player, System

BRIEF = Path(__file__).resolve().parent.parent / "docs" / "bot-brief.md"


def _code_blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", BRIEF.read_text(encoding="utf-8"),
                      re.DOTALL)


@pytest.fixture(scope="module")
def starter():
    """The brief's starter bot, executed as a drop-in model would be."""
    # The brief also shows the bare `decide` signature; the complete bot is the
    # block that actually returns its orders.
    blocks = [b for b in _code_blocks("python")
              if "def decide" in b and "return orders" in b]
    assert len(blocks) == 1, "the brief should carry exactly one complete bot"
    namespace: dict = {}
    exec(compile(blocks[0], str(BRIEF), "exec"), namespace)   # noqa: S102
    assert "decide" in namespace, "the starter must define decide(state, pid)"
    return namespace["decide"]


def _board(seed=5, nodes=18, players=3) -> GameState:
    state = mapgen.generate(seed, "random", nodes, players)
    for player in state.players.values():
        player.is_human = False
    return state


def _snapshot(state: GameState) -> tuple:
    return (tuple((s.id, s.owner_id, s.ships, s.prod_progress)
                  for s in state.systems.values()),
            tuple((f.owner_id, f.dest_id, f.ships, f.turns_remaining)
                  for f in state.fleets))


def test_the_starter_issues_only_legal_orders(starter):
    for seed in (3, 5, 8, 13, 21):
        state = _board(seed)
        for _ in range(8):
            orders = starter(state, 1)
            assert isinstance(orders, list)
            trial = copy.deepcopy(state)
            for order in orders:
                assert order.owner_id == 1
                assert engine.apply_order(trial, order) is not None, order
            engine.end_turn(state, decide=ai.decide)


def test_the_starter_leaves_the_state_alone(starter):
    state = _board()
    before = _snapshot(state)
    starter(state, 1)
    assert _snapshot(state) == before


def test_the_starter_is_reproducible(starter):
    """The brief tells a bot author to draw only from `state.rng`, and the
    starter is the example they will copy — so it has to obey its own rule."""
    import random
    random.seed(1)
    first = starter(_board(11), 1)
    random.seed(999)
    assert starter(_board(11), 1) == first


def test_the_starter_plays_whole_games_and_beats_the_heuristic(starter):
    """The brief advertises a figure. It does not have to be reproduced exactly
    here (that is a 200-game run), but a starter that cannot win is a broken
    promise and the one number a reader will check."""
    ai.register("brief_starter", starter)
    try:
        from . import sim
        wins = 0
        for seed in range(1, 11):
            result = sim.play(seed=seed, players=2, nodes=18,
                              strategies=["brief_starter", "heuristic"])
            wins += 1 if result.winner == 1 else 0
        assert wins >= 4, f"the starter won only {wins}/10 against the heuristic"
    finally:
        ai.STRATEGIES.pop("brief_starter", None)


def test_the_brief_names_only_real_api():
    """Every attribute the brief's tables promise a bot author. A renamed field
    turns the brief into a trap, since its reader cannot check."""
    text = BRIEF.read_text(encoding="utf-8")
    for name in ("systems", "players", "fleets", "turn", "winner", "rng",
                 "systems_of", "fleets_incoming", "are_adjacent", "travel_turns"):
        assert f"state.{name}" in text                     # it is documented...
        assert hasattr(GameState, name) or name in GameState.__dataclass_fields__
    for cls, fields in ((System, ("id", "owner_id", "ships", "production",
                                  "prod_progress", "neighbors", "pos", "name")),
                        (Player, ("id", "alive", "ships_lost", "ai_strategy",
                                  "ai_params")),
                        (Fleet, ("owner_id", "source_id", "dest_id", "ships",
                                 "turns_remaining", "turns_total")),
                        (Order, ("owner_id", "source_id", "dest_id", "ships"))):
        for field in fields:
            assert f"`{field}`" in text or field in text, (cls.__name__, field)
            assert field in cls.__dataclass_fields__, (cls.__name__, field)
    assert [f for f in Order.__dataclass_fields__] == ["owner_id", "source_id",
                                                       "dest_id", "ships"], \
        "the brief documents Order as positional in this order"
    for helper in ("edge_attacking", "edge_defending"):
        assert f"combat.{helper}()" in text and hasattr(combat, helper)
    assert "model.flow_field(state, allowed, seeds)" in text
    assert hasattr(model, "flow_field")


def test_the_brief_only_tells_people_to_run_real_commands():
    """Its whole audience is people who will paste these verbatim."""
    text = BRIEF.read_text(encoding="utf-8")
    commands = re.findall(r"uv run python ([^\n]+)", text)
    assert commands, "the brief must tell the reader how to check their bot"
    sim_flags = (Path(__file__).resolve().parent / "sim.py").read_text()
    checker = (Path(__file__).resolve().parent.parent / "tools" / "check_bot.py")
    assert checker.is_file()
    for command in commands:
        for flag in re.findall(r"--[a-z-]+", command):
            source = sim_flags if "tests.sim" in command else checker.read_text()
            assert f'"{flag}"' in source, f"{flag} is not a real flag: {command}"
        if command.startswith("tools/"):
            assert Path(command.split()[0]).name == "check_bot.py"
