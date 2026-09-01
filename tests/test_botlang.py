"""The visual rule language: interpreter, fall-through, and the Python exporter.

Pure/headless (no pygame), like `test_ai.py`. The load-bearing test here is
`test_export_round_trips_exactly`: `botlang.export` emits a standalone model that
must make the *same* decisions as the interpreter, which is what stops the
`src` templates in the spec tables drifting from the evaluators beside them.
"""

from __future__ import annotations

import ast
import copy

from starconquest import ai, botlang, config, engine, mapgen
from starconquest.botlang import Cond, Program, Rule
from starconquest.model import GameState, Order, System
from tests import sim

_SEEDS = range(1, 21)


def _state(seed: int, nodes: int = 18, players: int = 3) -> GameState:
    return mapgen.generate_random(seed, num_nodes=nodes, num_players=players)


# --------------------------------------------------------------------------- #
# Legality — the drop-in contract every strategy owes the engine
# --------------------------------------------------------------------------- #
def test_starters_only_ever_issue_legal_orders():
    for seed in _SEEDS:
        state = _state(seed)
        for name, program in botlang.STARTERS.items():
            for pid in (1, 2, 3):
                for order in botlang.run(program, state, pid):
                    src = state.systems[order.source_id]
                    assert order.owner_id == pid, f"{name}: issued a rival's order"
                    assert src.owner_id == pid, f"{name}: launched from a system we don't hold"
                    assert state.are_adjacent(order.source_id, order.dest_id), \
                        f"{name}: {order.source_id}->{order.dest_id} is not a lane"
                    assert 0 < order.ships <= src.ships, \
                        f"{name}: sent {order.ships} of {src.ships}"


def test_one_order_per_system_at_most():
    """The cap `ai.compute_orders` and `engine.apply_order` both assume."""
    for seed in _SEEDS:
        state = _state(seed)
        for program in botlang.STARTERS.values():
            sources = [o.source_id for o in botlang.run(program, state, 1)]
            assert len(sources) == len(set(sources))


def test_a_landless_seat_issues_nothing():
    state = _state(1)
    for s in state.systems.values():
        if s.owner_id == 2:
            s.owner_id = 3
    for program in botlang.STARTERS.values():
        assert botlang.run(program, state, 2) == []


# --------------------------------------------------------------------------- #
# Determinism — a seed must reproduce a match, tie-breaks included
# --------------------------------------------------------------------------- #
def test_same_seed_same_orders():
    for program in botlang.STARTERS.values():
        first = botlang.run(program, _state(7), 1)
        second = botlang.run(program, _state(7), 1)
        assert first == second


def test_registered_strategies_route_through_decide():
    botlang.register_starters()
    state = _state(1)
    for name, program in botlang.STARTERS.items():
        assert name in ai.STRATEGIES
        state.players[2].ai_strategy = name
        assert ai.decide(state, 2) == botlang.run(program, _state(1), 2)
    assert "blockheuristic" in ai.available_strategies()


def test_botlang_declares_no_aux_slider():
    """Every block bot shares this module, so an AUX_LABEL here would give them
    all the same meaningless knob (`ai.aux_spec` reads it off the module)."""
    botlang.register_starters()
    for name in botlang.STARTERS:
        assert ai.aux_spec(name) is None


# --------------------------------------------------------------------------- #
# Fall-through — the rule that lets a useful bot be four rows long
# --------------------------------------------------------------------------- #
def _pair(a_ships: int = 10, b_owner: int = 0, b_ships: int = 1) -> GameState:
    """Two adjacent systems: 0 is ours, 1 belongs to `b_owner`."""
    state = GameState.new(1)
    state.systems[0] = System(0, (0.0, 0.0), owner_id=1, ships=a_ships, production=3)
    state.systems[1] = System(1, (1.0, 0.0), owner_id=b_owner, ships=b_ships, production=3)
    state.add_lane(0, 1, 1.0, 1)
    state.rebuild_topology()
    state.players = mapgen.generate_random(1, num_nodes=18, num_players=3).players
    return state


def test_a_rule_with_no_target_falls_through():
    """Rule 1 matches but can't afford the target; rule 2 must still get a turn."""
    state = _pair(a_ships=10, b_owner=2, b_ships=50)
    program = Program("t", (
        Rule((), "attack_best", 3.0, "all"),      # matches, unaffordable -> no target
        Rule((), "attack_best", 0.1, "all"),      # ...so this one fires
    ))
    orders = botlang.run(program, state, 1)
    assert orders == [Order(1, 0, 1, 10)]


def test_a_rule_with_nothing_to_spend_falls_through():
    state = _pair(a_ships=3, b_owner=0, b_ships=1)
    program = Program("t", (
        Rule((), "attack_best", 1.0, "all_but", 99),   # pool <= 0
        Rule((), "attack_best", 1.0, "all_but", 1),    # ...so this one fires
    ))
    assert botlang.run(program, state, 1) == [Order(1, 0, 1, 2)]


def test_hold_stops_evaluation_instead_of_falling_through():
    state = _pair(a_ships=10, b_owner=0, b_ships=1)
    program = Program("t", (
        Rule((), "hold"),
        Rule((), "attack_best", 1.0, "all"),      # unreachable
    ))
    assert botlang.run(program, state, 1) == []


def test_unknown_rule_parts_are_skipped_not_raised():
    """Tolerant like the rest of the drop-in contract: a stale program still plays."""
    state = _pair(a_ships=10, b_owner=0, b_ships=1)
    program = Program("t", (
        Rule((Cond("no_such_condition"),), "attack_best", 1.0, "all"),
        Rule((), "no_such_action", 1.0, "all"),
        Rule((), "attack_best", 1.0, "no_such_amount"),
        Rule((), "attack_best", 1.0, "all"),
    ))
    assert botlang.run(program, state, 1) == [Order(1, 0, 1, 10)]


# --------------------------------------------------------------------------- #
# Conditions and amounts
# --------------------------------------------------------------------------- #
def test_conditions_gate_the_rule():
    state = _pair(a_ships=10, b_owner=0, b_ships=1)
    fires = Rule((Cond("ships_at_least", 10),), "attack_best", 1.0, "all")
    blocks = Rule((Cond("ships_at_least", 11),), "attack_best", 1.0, "all")
    assert botlang.run(Program("t", (fires,)), state, 1) == [Order(1, 0, 1, 10)]
    assert botlang.run(Program("t", (blocks,)), state, 1) == []


def test_conditions_are_anded():
    state = _pair(a_ships=10, b_owner=0, b_ships=1)
    both = (Cond("ships_at_least", 5), Cond("turn_at_least", 99))
    assert botlang.run(Program("t", (Rule(both, "attack_best", 1.0, "all"),)), state, 1) == []


def test_enough_sends_only_what_the_margin_needs():
    """A 20-ship system taking a 4-ship neutral at 1.5 commits 6, not all 20."""
    state = _pair(a_ships=20, b_owner=0, b_ships=4)
    program = Program("t", (Rule((), "expand_neutral", 1.5, "enough"),))
    expected = int(4 * config.DEFENDER_ADVANTAGE * 1.5)
    assert botlang.run(program, state, 1) == [Order(1, 0, 1, expected)]


def test_surplus_reads_the_seats_reserve_sliders():
    """The one deliberate contact point with the AI tab's existing knobs."""
    state = _pair(a_ships=20, b_owner=0, b_ships=1)
    program = Program("t", (Rule((), "attack_best", 1.0, "surplus"),))
    state.players[1].ai_params.reserve_fraction = 0.0
    state.players[1].ai_params.reserve_floor = 0
    assert botlang.run(program, state, 1)[0].ships == 20
    state.players[1].ai_params.reserve_floor = 5
    assert botlang.run(program, state, 1)[0].ships == 15


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def test_dict_round_trip():
    for program in botlang.STARTERS.values():
        assert botlang.from_dict(botlang.to_dict(program)) == program


def test_from_dict_tolerates_junk():
    assert botlang.from_dict(None) == Program("untitled")
    assert botlang.from_dict({}) == Program("untitled")
    messy = {
        "name": "x",
        "extra": "ignored",
        "rules": [
            "not a rule",
            {"action": "no_such_action"},
            {"action": "attack_best", "amount": "all", "margin": "junk",
             "when": ["nope", {"kind": "no_such_condition"}, {"kind": "frontier"}]},
        ],
    }
    program = botlang.from_dict(messy)
    assert program.name == "x"
    assert len(program.rules) == 1
    assert program.rules[0].when == (Cond("frontier", 0.0),)
    assert program.rules[0].margin == botlang.ACTIONS["attack_best"].default


# --------------------------------------------------------------------------- #
# Export — the anti-drift pin for the whole spec-table design
# --------------------------------------------------------------------------- #
def test_export_round_trips_exactly(tmp_path):
    """The generated model must decide identically to the interpreter.

    Run over several turns of a real game so every rule in every starter gets
    exercised, not just the opening position.
    """
    for name, program in botlang.STARTERS.items():
        (tmp_path / f"{name}_exported.py").write_text(botlang.export(program))
    loaded = ai.load_models(tmp_path)
    try:
        assert loaded == sorted(f"{n}_exported" for n in botlang.STARTERS)
        for seed in range(1, 6):
            state = _state(seed)
            for _ in range(8):
                for name, program in botlang.STARTERS.items():
                    for pid in (1, 2, 3):
                        interpreted = botlang.run(program, copy.deepcopy(state), pid)
                        exported = ai.STRATEGIES[f"{name}_exported"](copy.deepcopy(state), pid)
                        assert interpreted == exported, f"{name} seat {pid} diverged"
                engine.end_turn(state, decide=ai.decide)
    finally:
        for name in loaded:
            ai.STRATEGIES.pop(name, None)


def test_export_is_a_standalone_module(tmp_path):
    """It must never call back into botlang, nor reach into anything private of
    ai's — the helpers are vendored, the way thinker.py and knower.py vendor theirs."""
    source = botlang.export(botlang.STARTERS["blockheuristic"])
    tree = ast.parse(source)                      # AST, so vendored docstrings that
    imported: set[str] = set()                    # merely *mention* ai._frontier_order
    for node in ast.walk(tree):                   # don't read as references
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id not in ("ai", "botlang"), f"reaches into {node.value.id}"
    assert not [m for m in imported if "botlang" in m or m.endswith(".ai")], imported
    path = tmp_path / "standalone.py"
    path.write_text(source)
    assert ai.load_models(tmp_path) == ["standalone"]
    ai.STRATEGIES.pop("standalone", None)


def test_export_documents_every_rule():
    source = botlang.export(botlang.STARTERS["blockturtle"])
    for rule in botlang.STARTERS["blockturtle"].rules:
        assert botlang.describe(rule) in source


# --------------------------------------------------------------------------- #
# Termination — the failure mode `models/README.md` warns about
# --------------------------------------------------------------------------- #
def _timeouts(line: list[str], seeds) -> int:
    return sum(sim.play(s, "random", 18, 2, 600, strategies=line).timed_out for s in seeds)


def test_starters_finish_their_games():
    """A bot that fails to commit stalls into 600-turn games (`models/README.md`).

    Measured against a `heuristic` vs `heuristic` control on the same seeds rather
    than a fixed threshold: two evenly matched bots on an 18-node map genuinely
    stalemate about a fifth of the time, so an absolute bar would either be
    meaningless or would fail whenever balance moved.
    """
    botlang.register_starters()
    seeds = range(1, 21)
    control = _timeouts(["heuristic", "heuristic"], seeds)
    for name in botlang.STARTERS:
        stalled = _timeouts([name, "heuristic"], seeds)
        assert stalled <= control + 4, \
            f"{name} stalled {stalled}/20 games against a control of {control}/20"
