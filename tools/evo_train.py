#!/usr/bin/env python3
"""Train evolved's weights by CMA-ES against a panel of sparring partners.

    uv run --with cma python tools/evo_train.py --run p1 --gens 150
    uv run --with cma python tools/evo_train.py --run p1 --gens 200      # resume
    uv run --with cma python tools/evo_train.py --run p1 --write          # ship the mean
    uv run --with cma python tools/evo_train.py --run p1 --write --out results/evolved/p1_weights.py

Offline only: the panel's bots are opponents here, and nothing of theirs is in
what ships (the weights). A genome's fitness is its mean result over paired
seatings on fresh maps each generation (seed ``SEED_BASE + 1000 g + k``, the
same maps for every candidate in a generation), a win 1, a loss 0, and a game
still running at ``--cap`` turns scored by its share of the two seats'
production. Candidates are registered with ``ai.register`` inside each worker,
so nothing is written to ``models/`` while training.

League: a panel entry ``evo:<label>`` is a frozen evolved played as the plain
policy: ``evo:shipped`` the committed weights, any other label the weights in
``results/evolved/<label>_weights.py`` (written by ``--write --out``), carried
into the current layout by name. Cells may have more
than two players: the candidate takes each seat in turn against copies of the
opponent, and shares are of all seats' production. ``--trade W`` blends in the
exchange rate: (1 − W) × the usual score + W × kills / (kills + losses) in the
candidate's fights over the game.

Each run lives in ``results/evolved/<run>/``: ``es.pkl`` (the CMA state, saved
every generation, so a run resumes on any machine) and ``log.jsonl`` (one line
per generation, with the mean genome).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import os
import pickle
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay, turnfilm  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

RESULTS = ROOT / "results" / "evolved"
WEIGHTS_FILE = ROOT / "models" / "_evolved_weights.py"
SEED_BASE = 2_000_000
# Agreed 2026-10-15: the roster targets plus a league of frozen past evolveds
# (a quarter of games), and one 3-player cell.
PANEL = ("thinker", "marshal", "marshal", "actuary", "actuary", "claudebot", "evo:shipped")
CELLS = ("random:7:6:2", "random:11:6:2", "random:11:6:3")
CAND = "evolved_candidate"

_evolved = None


def _module():
    global _evolved
    if _evolved is None:
        spec = importlib.util.spec_from_file_location(
            "sc_evolved_train", ROOT / "models" / "evolved.py")
        _evolved = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_evolved)
    return _evolved


def _layout():
    """evolved's LAYOUT, readable even while the weights file is in an older layout."""
    spec = importlib.util.spec_from_file_location(
        "sc_evolved_layout", ROOT / "models" / "evolved.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ValueError:
        pass            # building `decide` from mismatched weights; LAYOUT is set by then
    return mod.LAYOUT


def _income(state, pid: int) -> float:
    return sum(1.0 / s.production for s in state.systems.values()
               if s.owner_id == pid and s.production > 0)


def _share(state, pid: int) -> float:
    total = sum(_income(state, q) for q, p in state.players.items() if not p.is_neutral)
    return _income(state, pid) / total if total else 1.0 / max(1, len(state.players) - 1)


_league: dict[str, object] = {}


def _register_opponent(opp: str) -> None:
    """Make ``opp`` playable in this worker: a roster bot, or ``evo:<label>``."""
    if opp in ai.STRATEGIES:
        return
    if not opp.startswith("evo:"):
        ai.load_models()
        return
    if opp not in _league:
        label = opp[4:]
        # "shipped" is the committed weights file; any other label one that
        # ``--write --out results/evolved/<label>_weights.py`` wrote.
        named = read_named(WEIGHTS_FILE if label == "shipped" else RESULTS / f"{label}_weights.py")
        vec = [named.get((g, f), 0.0) for g, feats in _layout() for f in feats]
        _league[opp] = _module().make_decide(vec)
    ai.register(opp, _league[opp])


# A game is adjudicated once the candidate's production share has stayed at or
# above ADJUDICATE (a win) or at or below 1 - ADJUDICATE (a loss) for
# ADJUDICATE_TURNS turns in a row: about 13% fewer turns played, and on 96
# training games the call matched every finished game's real result.
ADJUDICATE, ADJUDICATE_TURNS = 0.9, 5


def play(vec, cell: str, seed: int, opp: str, cand_seat: int, cap: int,
         mid: int = 0, trade: float = 0.0, adjudicate: bool = True) -> float:
    """One game; the candidate's result in [0, 1].

    With ``mid``, a game still running at turn ``mid`` scores half its
    production share then and half its final result: a smoother signal than
    win or loss alone, which still rewards finishing.
    """
    if not ai.STRATEGIES or "thinker" not in ai.STRATEGIES:
        ai.load_models()
    _register_opponent(opp)
    ai.set_budget_scale(100.0)
    ai.register(CAND, _module().make_decide(vec))
    mode, nodes, ly, players = cell.split(":")
    cfg = Settings(mode=mode, nodes=int(nodes), players=int(players),
                   ship_ly_per_turn=float(ly), autoplay=True)
    state = build_state(cfg, seed)
    for pid, p in state.players.items():
        if p.is_neutral:
            continue
        p.ai_strategy = CAND if pid == cand_seat else opp
        # Every seat at its default, except evolveds, which train and spar with
        # the look-ahead off (aux 0): it costs ~6x a game. ``--cand-aux``
        # (``EVO_CAND_AUX`` in the workers) sets the candidate's own.
        evolved_seat = p.ai_strategy == CAND or p.ai_strategy.startswith("evo:")
        aux = 0.0 if evolved_seat else 1.0
        if p.ai_strategy == CAND:
            aux = float(os.environ.get("EVO_CAND_AUX", "0"))
        p.ai_params = replace(p.ai_params, aux=aux)
    early = None
    lost = killed = 0

    def count(ev) -> None:
        nonlocal lost, killed
        if not isinstance(ev, turnfilm.Landed):
            return
        for st in ev.steps:
            won = st.winner == st.attacker
            a_lost = st.attacker_ships - (st.survivors if won else 0)
            d_lost = st.defender_ships - (0 if won else st.survivors)
            if st.attacker == cand_seat:
                lost, killed = lost + a_lost, killed + d_lost
            elif st.defender == cand_seat:
                lost, killed = lost + d_lost, killed + a_lost

    run, called = 0, None
    while state.winner is None and state.turn < cap:
        if mid and state.turn == mid:
            early = _share(state, cand_seat)
        replay.reseed(state)
        engine.end_turn(state, decide=ai.decide, on_event=count if trade else None)
        if adjudicate and state.winner is None and state.turn >= mid:
            x = _share(state, cand_seat)
            run = run + 1 if (x >= ADJUDICATE or x <= 1.0 - ADJUDICATE) else 0
            if run >= ADJUDICATE_TURNS:
                called = 1.0 if x >= ADJUDICATE else 0.0
                break
    if state.winner is not None:
        final = 1.0 if state.winner == cand_seat else 0.0
    elif called is not None:
        final = called
    else:
        final = _share(state, cand_seat)
    score = final if early is None else 0.5 * early + 0.5 * final
    if trade:
        rate = killed / (killed + lost) if killed + lost else 0.5
        score = (1.0 - trade) * score + trade * rate
    return score


def play_batch(vec, games, cap: int, mid: int = 0, trade: float = 0.0,
               adjudicate: bool = True) -> list[float]:
    return [play(vec, *g, cap, mid, trade, adjudicate) for g in games]


def schedule(gen: int, n_seeds: int, panel, cells):
    """``(cell, seed, opp, seat)`` per game: each seed in every seating, the
    seeds dealt round-robin over cell x opponent."""
    combos = [(c, o) for c in cells for o in panel]
    out = []
    for k in range(n_seeds):
        cell, opp = combos[k % len(combos)]
        seed = SEED_BASE + 1000 * gen + k
        out += [(cell, seed, opp, seat) for seat in range(1, int(cell.split(":")[3]) + 1)]
    return out


LAYOUT_ADDED = (("race", "backed", "forgone"),     # batches of features (or whole groups)
                ("stage", "converge", "awaiting"),  # added since layout v4, oldest first
                ("evacuate", "recapture"),
                ("exchange", "piecemeal"),
                ("detail", "cut", "culminate", "fork", "leader", "busy", "new_front"),
                ("src_mass", "mass_gap", "rear_gap"),
                ("opening", "open_neutral"))


def to_current(vec) -> list[float]:
    """A flat genome from this or an older layout, in the current one; features
    added since start at zero."""
    layout = _layout()
    for k in range(len(LAYOUT_ADDED) + 1):
        dropped = {f for batch in LAYOUT_ADDED[len(LAYOUT_ADDED) - k:] for f in batch}
        old = [(g, f) for g, feats in layout if g not in dropped for f in feats if f not in dropped]
        if len(old) == len(vec):
            named = dict(zip(old, vec))
            return [named.get((g, f), 0.0) for g, feats in layout for f in feats]
    raise ValueError(f"{len(vec)} weights match no known layout")


def write_weights(vec, note: str, path: Path = WEIGHTS_FILE) -> None:
    """Write ``vec`` between the markers of ``path`` (a new file starts as a
    copy of the shipped weights file, so it keeps the rival and value weights)."""
    vec = to_current(vec)
    lines, i = [], 0
    for name, feats in _layout():
        lines.append(f"    # {name}: {' '.join(feats)}")
        chunk = [f"{x:.4f}" for x in vec[i:i + len(feats)]]
        for j in range(0, len(chunk), 9):
            lines.append("    " + ", ".join(chunk[j:j + 9]) + ",")
        i += len(feats)
    body = f"# {note}\nWEIGHTS = [\n" + "\n".join(lines) + "\n]\n"
    text = (path if path.exists() else WEIGHTS_FILE).read_text()
    new = re.sub(r"(# BEGIN WEIGHTS\n).*?(# END WEIGHTS)", lambda m: m[1] + body + m[2],
                 text, flags=re.S)
    path.write_text(new)


def read_named(path: Path) -> dict[tuple[str, str], float]:
    """A weights file's values keyed by (group, feature), from its comment lines."""
    text = path.read_text()
    body = re.search(r"# BEGIN WEIGHTS\n(.*?)# END WEIGHTS", text, flags=re.S)[1]
    body = body.split("WEIGHTS = [", 1)[1].split("]", 1)[0]
    out, names, group, k = {}, [], "", 0
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            group, rest = line[1:].split(":", 1)
            group, names, k = group.strip(), rest.split(), 0
            continue
        for tok in filter(None, (x.strip() for x in line.split(","))):
            out[(group, names[k])] = float(tok)
            k += 1
    return out


def migrate(src: Path) -> list[float]:
    """``src``'s weights in the current layout; features it lacks start at zero."""
    named = read_named(src)
    layout = _layout()
    vec = [named.get((g, f), 0.0) for g, feats in layout for f in feats]
    missing = [(g, f) for g, feats in layout for f in feats if (g, f) not in named]
    print(f"migrated {len(vec) - len(missing)} weights; new at zero: "
          f"{sorted({f for _, f in missing})}")
    return vec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--gens", type=int, default=150, help="train up to this generation")
    ap.add_argument("--pop", type=int, default=20)
    ap.add_argument("--sigma", type=float, default=0.3)
    ap.add_argument("--seeds", type=int, default=160, help="paired seeds per evaluation")
    ap.add_argument("--cap", type=int, default=250)
    ap.add_argument("--mid", type=int, default=40,
                    help="score half the production share at this turn (0: result only)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="evolve only these feature names (in every option type that has "
                         "them, plus guard params by name; group.feature for one "
                         "group's); the rest stay frozen")
    ap.add_argument("--panel", nargs="*", default=list(PANEL),
                    help="roster bots, and evo:<label> for a frozen past evolved "
                         "(results/evolved/<label>_weights.py)")
    ap.add_argument("--cand-aux", type=float, default=0.0,
                    help="the candidate's aux: 0 trains the plain policy, 1 with the look-ahead on")
    ap.add_argument("--no-adjudicate", action="store_true",
                    help="play every game out instead of calling decided ones")
    ap.add_argument("--trade", type=float, default=0.0,
                    help="weight of the exchange rate (kills / (kills + losses)) in the score")
    ap.add_argument("--cell", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--diagonal", action="store_true",
                    help="sep-CMA (diagonal covariance), for large genomes such as the head")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="stop after this many hours (0: no limit)")
    ap.add_argument("--write", action="store_true",
                    help="write the run's latest mean (to --out, else models/_evolved_weights.py) and stop")
    ap.add_argument("--out", type=Path, default=None,
                    help="with --write or --migrate: the weights file to write (default: the shipped one)")
    ap.add_argument("--start", type=Path, default=None,
                    help="a new run starts from this weights file (any layout, by name) "
                         "instead of the shipped one")
    ap.add_argument("--from-log-mean", type=int, default=0,
                    help="with --write: average the means of the last N generations")
    ap.add_argument("--migrate", type=Path, default=None,
                    help="write this weights file into the current layout and stop "
                         "(--run names the note)")
    args = ap.parse_args()
    cells = args.cell or list(CELLS)
    os.environ["EVO_CAND_AUX"] = str(args.cand_aux)     # spawned workers inherit it

    out = args.out or WEIGHTS_FILE
    if args.migrate:
        write_weights(migrate(args.migrate), f"{args.run}: {args.migrate.name} in the current layout",
                      out)
        return

    folder = RESULTS / args.run
    folder.mkdir(parents=True, exist_ok=True)
    es_path, log_path = folder / "es.pkl", folder / "log.jsonl"

    if args.write:
        rows = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()]
        k = max(1, args.from_log_mean)
        tail = rows[-k:]
        vec = [sum(r["mean"][i] for r in tail) / len(tail) for i in range(len(tail[0]["mean"]))]
        write_weights(vec, f"run {args.run}, mean of generations "
                           f"{tail[0]['gen']}..{tail[-1]['gen']}", out)
        print(f"wrote {len(vec)} weights from {args.run} gens "
              f"{tail[0]['gen']}..{tail[-1]['gen']} to {out}")
        return

    import cma

    if es_path.exists():
        saved = pickle.loads(es_path.read_bytes())
        es, gen = saved[0], saved[1]
        base, idx = (saved[2], saved[3]) if len(saved) > 2 else (None, None)
        print(f"resuming {args.run} at generation {gen}")
    else:
        if args.start:
            base = migrate(args.start)
            print(f"starting from {args.start}")
        else:
            spec = importlib.util.spec_from_file_location("sc_w", WEIGHTS_FILE)
            wmod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(wmod)
            base = list(wmod.WEIGHTS)
        idx = None
        if args.only:
            idx, i = [], 0
            for group, feats in _module().LAYOUT:
                # A group's name (e.g. "head") selects the whole group, and
                # "group.feature" one feature in that group only.
                idx += [i + j for j, f in enumerate(feats)
                        if f in args.only or group in args.only or f"{group}.{f}" in args.only]
                i += len(feats)
            if not idx:     # never fall back to the whole genome by accident
                sys.exit(f"--only {' '.join(args.only)} matches no weight in this layout")
            print(f"evolving {len(idx)} of {len(base)} weights")
        start = [base[i] for i in idx] if idx else base
        opts = {"popsize": args.pop, "seed": 1, "verbose": -9}
        if args.diagonal:
            opts["CMA_diagonal"] = True     # sep-CMA: for hundreds of weights
        es = cma.CMAEvolutionStrategy(start, args.sigma, opts)
        gen = 0

    def full(x):
        if not idx:
            return list(x)
        out = list(base)
        for i, v in zip(idx, x):
            out[i] = float(v)
        return out

    ctx = multiprocessing.get_context("spawn")
    started = time.perf_counter()
    with ProcessPoolExecutor(args.jobs, mp_context=ctx) as pool:
        while gen < args.gens:
            if args.hours and time.perf_counter() - started > args.hours * 3600:
                print(f"stopping at generation {gen}: --hours {args.hours} reached", flush=True)
                break
            t0 = time.perf_counter()
            xs = es.ask()
            games = schedule(gen, args.seeds, args.panel, cells)
            chunk = max(1, len(games) // 8)
            futs = [[pool.submit(play_batch, full(x), games[i:i + chunk], args.cap, args.mid,
                                 args.trade, not args.no_adjudicate)
                     for i in range(0, len(games), chunk)] for x in xs]
            fits = [sum(sum(f.result()) for f in fl) / len(games) for fl in futs]
            es.tell(xs, [-f for f in fits])
            gen += 1
            row = {"gen": gen, "best": max(fits), "avg": sum(fits) / len(fits),
                   "sigma": es.sigma, "secs": round(time.perf_counter() - t0, 1),
                   "mean": [round(float(v), 5) for v in full(es.mean)]}
            with open(log_path, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            es_path.write_bytes(pickle.dumps((es, gen, base, idx)))
            print(f"gen {gen:4d}  avg {row['avg']:.3f}  best {row['best']:.3f}  "
                  f"sigma {es.sigma:.3f}  {row['secs']}s", flush=True)


if __name__ == "__main__":
    main()
