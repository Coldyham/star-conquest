#!/usr/bin/env python3
"""Record or check Hunt's orders (actuary's Style stop 3, once the bot hunter)
on a fixed set of positions, so a speed-up can be shown to change no decision.

    uv run python tools/hunt_exact.py --record results/hunter/exact.pkl
    uv run python tools/hunt_exact.py --check results/hunter/exact.pkl

Positions come from Hunt-vs-actuary games (7, 11, 18 and 24 systems, two and
three seats), every fifth turn; at each, Hunt's orders with and without its
look-ahead are stored, and the time each took.
"""
from __future__ import annotations

import argparse
import copy
import math
import pickle
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay  # noqa: E402
from starconquest import settings as settings_mod  # noqa: E402
from starconquest.settings import Settings, build_state  # noqa: E402

SETUPS = ((7, 6.0, 2), (11, 6.0, 3), (18, 6.0, 2), (18, 18.0, 2), (24, 3.0, 2))


def positions():
    out = []
    for i, (nodes, ly, seats) in enumerate(SETUPS):
        cfg = Settings(mode="random", nodes=nodes, players=seats, ship_ly_per_turn=ly,
                       autoplay=True)
        st = build_state(cfg, 9_950_000 + i)
        for pid in range(1, seats + 1):
            st.players[pid].ai_strategy = "actuary"
            st.players[pid].ai_params = replace(st.players[pid].ai_params,
                                                aux=3.0 if pid == 1 else 1.0)
        while st.winner is None and st.turn < 120 and st.players[1].alive:
            if st.turn % 5 == 0:
                out.append((cfg, copy.deepcopy(st)))
            replay.reseed(st)
            engine.end_turn(st, decide=ai.decide)
    return out


def orders(hu, boards, look):
    hu.LOOKAHEAD = look
    rows, t0 = [], time.process_time()
    for cfg, b in boards:
        # build_state writes the setup's balance into `config`, which travel times
        # and fights read live: every position is decided under its own setup.
        settings_mod._apply_globals(cfg)
        rows.append(sorted((o.source_id, o.dest_id, o.ships) for o in hu.decide(b, 1)))
    return rows, time.process_time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record")
    g.add_argument("--check")
    a = ap.parse_args()
    ai.load_models()
    ai.set_budget_scale(math.inf)
    hu = sys.modules[ai.STRATEGIES["actuary"].__module__]
    look0 = hu.LOOKAHEAD
    if a.record:
        boards = positions()
        plain, tp = orders(hu, boards, False)
        look, tl = orders(hu, boards, True)
        Path(a.record).write_bytes(pickle.dumps({"boards": boards, "plain": plain, "look": look}))
        print(f"{len(boards)} positions recorded; plain {tp:.2f}s, look-ahead {tl:.2f}s CPU")
    else:
        rec = pickle.loads(Path(a.check).read_bytes())
        plain, tp = orders(hu, rec["boards"], False)
        look, tl = orders(hu, rec["boards"], True)
        bad_p = sum(x != y for x, y in zip(plain, rec["plain"]))
        bad_l = sum(x != y for x, y in zip(look, rec["look"]))
        print(f"{len(plain)} positions: plain differs on {bad_p}, look-ahead on {bad_l}; "
              f"plain {tp:.2f}s, look-ahead {tl:.2f}s CPU")
        hu.LOOKAHEAD = look0
        sys.exit(1 if bad_p or bad_l else 0)
    hu.LOOKAHEAD = look0


if __name__ == "__main__":
    main()
