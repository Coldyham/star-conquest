"""evolved: blind by construction, its switchable moves off at zero weights,
and a look-ahead that neither draws from the game's rng nor rolls its dice."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from starconquest import ai, engine, replay
from starconquest.settings import Settings, build_state

MODELS = Path(__file__).resolve().parent.parent / "models"
# Moves whose own weight group switches them off when all zero.
SWITCHABLE = ("evacuate", "recapture")


def _evolved():
    spec = importlib.util.spec_from_file_location("evolved_under_test", MODELS / "evolved.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_evolved_reads_only_the_board():
    # The bot is blind: it never reads or runs another seat's code. So it
    # imports nothing from the AI registry or other bots and declares no
    # oracle hooks; core rules helpers (combat, engine, model) are fine.
    for name in ("evolved.py", "_evolved_weights.py"):
        tree = ast.parse((MODELS / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("starconquest.ai", "models"), (name, node.module)
                assert not (node.module or "").startswith("models"), (name, node.module)
                if node.module == "starconquest":
                    assert {a.name for a in node.names} <= {"combat", "engine", "model"}, name
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith(("models", "starconquest.ai")), (name, a.name)
            if isinstance(node, (ast.Name, ast.Attribute)):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert ident not in ("IS_ORACLE", "is_oracle_seat", "STRATEGIES",
                                     "load_models"), (name, ident)
            if isinstance(node, (ast.FunctionDef, ast.Assign)):
                names = ([node.name] if isinstance(node, ast.FunctionDef)
                         else [t.id for t in node.targets if isinstance(t, ast.Name)])
                assert not {"IS_ORACLE", "is_oracle_seat"} & set(names), (name, names)


def test_weights_match_the_layout():
    evo = _evolved()
    w = evo._weights_module()
    assert len(w.WEIGHTS) == evo.N_WEIGHTS


def _kinds_chosen(vec, seeds=(11, 12)) -> set[str]:
    evo = _evolved()
    policy = evo.make_policy(vec)
    kinds: set[str] = set()
    for seed in seeds:
        state = build_state(Settings(mode="random", nodes=11, players=2, autoplay=True), seed)
        for p in state.players.values():
            if not p.is_neutral:
                p.ai_strategy = "heuristic"

        def decide(st, pid, _p=policy):
            orders, chosen = _p(st, pid)
            kinds.update(k[0] for k in chosen)
            return orders

        while state.winner is None and state.turn < 120:
            replay.reseed(state)
            engine.end_turn(state, decide=decide)
    return kinds


def test_switchable_moves_are_off_at_zero_and_on_when_weighted():
    evo = _evolved()
    shipped = list(evo._load_weights())
    start, off = {}, 0
    for g, feats in evo.LAYOUT:
        start[g] = off
        off += len(feats)
    zero = list(shipped)
    for g in SWITCHABLE:
        n = len(dict(evo.LAYOUT)[g])
        zero[start[g]:start[g] + n] = [0.0] * n
    assert not _kinds_chosen(zero) & set(SWITCHABLE)
    # A large bias switches each one on, so the check above is not vacuous.
    forced = list(zero)
    for g in SWITCHABLE:
        forced[start[g]] += 20.0
    assert set(SWITCHABLE) <= _kinds_chosen(forced, seeds=range(11, 17))


def _midgame(seed=21, players=3):
    state = build_state(Settings(mode="random", nodes=13, players=players, autoplay=True), seed)
    for p in state.players.values():
        if not p.is_neutral:
            p.ai_strategy = "heuristic"
    ai.load_models()
    while state.winner is None and state.turn < 12:
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide)
    return state


def test_look_ahead_leaves_the_game_untouched():
    # The rollouts play on copies: the board, its rng and its dice salt are as
    # they were, so a seat's look-ahead cannot move anyone else's game.
    evo = _evolved()
    state = _midgame()
    pid = next(p.id for p in state.players.values() if not p.is_neutral and not state.is_defeated(p.id))
    before = (state.rng.getstate(), state.dice_salt, state.turn,
              {k: (s.owner_id, s.ships) for k, s in state.systems.items()},
              [(f.owner_id, f.dest_id, f.ships, f.turns_remaining) for f in state.fleets])
    evo.decide(state, pid)
    after = (state.rng.getstate(), state.dice_salt, state.turn,
             {k: (s.owner_id, s.ships) for k, s in state.systems.items()},
             [(f.owner_id, f.dest_id, f.ships, f.turns_remaining) for f in state.fleets])
    assert before == after


def test_rollouts_never_roll_the_games_dice():
    evo = _evolved()
    state = _midgame()
    assert evo._clone(state).dice_salt != state.dice_salt


def test_decide_ms_counts_only_the_look_ahead():
    evo = _evolved()
    on = Settings(nodes=31, players=3)
    assert evo.decide_ms(on, 1) > 10.0
    off = Settings(nodes=31, players=3)
    off.ai[0].aux = 0.0
    assert evo.decide_ms(off, 1) == 0.0
