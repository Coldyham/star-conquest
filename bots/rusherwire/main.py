#!/usr/bin/env python3
"""rusherplus, as an external bot: the same decisions, over the wire.

A line-for-line port of ``models/rusherplus.py`` that imports **nothing** from
``starconquest`` — its whole view of the game is the JSON it is handed, which is
what makes it a test of the schema rather than of itself. It needs no virtualenv
either: stdlib only, so any ``python3`` runs it.

It is Python because ``tests/test_botio.py`` demands orders *identical* to the
in-process original, and matching that exactly means matching its tie-breaks:
``random.Random`` draws, in the same order, off the seed the payload carries.
A bot in another language cannot reproduce those draws and does not need to —
the equality test exists to prove the payload is sufficient, not to make
determinism a cross-language requirement.

Protocol: docs/bot-api.md.
"""

import json
import random
import sys


class Bot:
    def __init__(self, hello):
        self.pid = hello["you"]
        # Static half of the board: production and the graph never change.
        self.neighbors = {s["id"]: list(s["neighbors"]) for s in hello["map"]["systems"]}
        self.order = [s["id"] for s in hello["map"]["systems"]]

    def decide(self, turn):
        pid = self.pid
        rng = random.Random(turn["rng_seed"])
        owner = {s["id"]: s["owner"] for s in turn["systems"]}
        ships = {s["id"]: s["ships"] for s in turn["systems"]}
        orders = []
        for sid in self.order:
            if owner[sid] != pid or ships[sid] <= 1:
                continue
            hostile = [n for n in self.neighbors[sid] if owner[n] != pid]
            targets = hostile or list(self.neighbors[sid])
            if not targets:
                continue
            # min() evaluates the key once per element, in order: the same draws
            # in the same sequence as the original's `state.rng`.
            target = min(targets, key=lambda n: (ships[n], rng.random()))
            attacked_by = sum(f["ships"] for f in turn["fleets"]
                              if f["dst"] == sid and f["owner"] != pid)
            reinforced_by = sum(f["ships"] for f in turn["fleets"]
                                if f["dst"] == sid and f["owner"] == pid)
            if attacked_by > ships[sid] + reinforced_by:
                orders.append({"src": sid, "dst": target, "ships": ships[sid]})
                continue
            send = (ships[sid] + reinforced_by - 1) - attacked_by
            if send > ships[target] + 1:
                orders.append({"src": sid, "dst": target, "ships": send})
        return orders


def main():
    bot = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        kind = message.get("type")
        if kind == "hello":
            bot = Bot(message)
            reply = {"type": "ready", "name": "rusherwire", "version": "1.0.0"}
        elif kind == "turn" and bot is not None:
            reply = {"type": "orders", "orders": bot.decide(message)}
        else:
            reply = {"type": "orders", "orders": []}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
