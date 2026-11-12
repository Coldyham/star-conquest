#!/usr/bin/env python3
"""Bait patterns in the posted games: who thins a frontier system, and what comes of it.

    uv run python tools/bait_census.py            # read what is missing, then report
    uv run python tools/bait_census.py --report   # report from the ledger only

Every seat of every public replay on the current rules is read (the publishable
key; ``replay.reconstruct`` with the film's events, so nothing is decided again):

- a **thin**: a seat launches at least half of a system's garrison to another
  system of its own, from a system next to a rival's;
- **struck**: a rival fleet lands on the thinned system within ``WINDOW`` turns;
  it **bit** if it was launched after the thin, when the rival could see it;
- **held**: the system is still the seat's after that landing; **reinforced** if
  its own ships landed there between the rival's launch and the strike, a
  **bounce** if any of them came from where the thinned ships went, and
  **decisive** if the garrison without them would have fallen.

The person's seat is the one a person held; the other seats are the bots of the
day the game was posted, so their rows are those versions' habits. Each game is
appended to ``results/bait-human/games.jsonl`` (its episodes to
``episodes.jsonl``) as it is read, and a rerun reads only new replays. Read in
``docs/design/council.md``, "The bounce".
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import config, engine, replay, turnfilm
from tools.config_census import public_rows

OUT = ROOT / "results" / "bait-human"
WINDOW = 8          # turns after a thin in which a strike counts
THIN = 0.5          # share of the garrison sent away


def mine(blob):
    log = replay.GameLog.decode(blob["log"])
    snaps, launches, landings = [], {}, []
    turn = [0]
    # The film numbers fleets per turn (those in transit first, in list order), so
    # each fleet gets a global key carried across turns by its Fleet object.
    gk, turnmap, pending, nxt = {}, {}, {}, [0]

    def new_key():
        nxt[0] += 1
        return nxt[0]

    def on_turn(st):
        turn[0] = st.turn
        snaps.append({sid: (s.owner_id, s.ships) for sid, s in st.systems.items()})
        if len(snaps) == 1:
            on_turn.adj = {sid: list(s.neighbors) for sid, s in st.systems.items()}
            on_turn.human = [p.id for p in st.players.values() if p.is_human]
            on_turn.strategies = {p.id: p.ai_strategy for p in st.players.values() if not p.is_neutral}
            on_turn.adv = config.DEFENDER_ADVANTAGE
        turnmap.clear()
        for i, f in enumerate(st.fleets):
            held = gk.get(id(f))
            if held is None or held[0] is not f:
                key = pending.get((f.owner_id, f.source_id, f.dest_id)) or new_key()
                gk[id(f)] = (f, key)
            turnmap[i] = gk[id(f)][1]
        pending.clear()

    def on_event(ev):
        t = turn[0]          # the turn being resolved (board before it)
        if isinstance(ev, turnfilm.Launched):
            key = turnmap.get(ev.fleet) or new_key()
            turnmap[ev.fleet] = key
            launches[key] = {"t": t, "owner": ev.owner_id, "src": ev.source_id, "dest": ev.dest_id,
                             "ships": ev.ships, "left": ev.source_ships}
            pending[(ev.owner_id, ev.source_id, ev.dest_id)] = key
        elif isinstance(ev, turnfilm.Landed):
            landings.append({"t": t, "node": ev.node_id, "fleets": [turnmap[f] for f in ev.fleets],
                             "was_owner": ev.was_owner, "was_ships": ev.was_ships, "owner": ev.owner_id,
                             "ships": ev.ships, "sides": list(ev.sides)})
    try:
        st, _ = replay.reconstruct(log, on_turn=on_turn, on_event=on_event)
    except Exception as e:  # noqa: BLE001
        return {"match": blob["match_id"], "error": str(e)}
    hand = {i for i, tr in enumerate(log.turns) if not tr.get("ai")}
    human = on_turn.human[0] if on_turn.human else None
    adv = on_turn.adv
    # A launch's total (top-ups repeat the fleet id with the new total).
    episodes = []
    per_seat = Counter()
    for L in launches.values():
        t, p, a, b = L["t"], L["owner"], L["src"], L["dest"]
        board = snaps[t]
        if board.get(b, (None,))[0] != p:
            continue                       # not a move between own systems
        before = L["ships"] + L["left"]
        if before <= 0 or L["ships"] / before < THIN:
            continue
        rivals_next = [n for n in on_turn.adj[a] if board[n][0] not in (0, p)]
        if not rivals_next:
            continue
        per_seat[p] += 1
        # The first rival landing on A after the thin, from a fleet launched after it.
        for land in landings:
            if land["node"] != a or not (t < land["t"] <= t + WINDOW):
                continue
            if land["was_owner"] != p:
                break                      # A changed hands some other way first
            rf = [launches[f] for f in land["fleets"] if f in launches and launches[f]["owner"] not in (0, p)]
            if not rf:
                continue
            after = [r for r in rf if r["t"] > t]
            struck = sum(r["ships"] for r in rf)
            rlaunch = min(r["t"] for r in rf)
            own_in = [launches[f] for f in land["fleets"] if f in launches and launches[f]["owner"] == p]
            # own ships that landed on A between the rival's launch and its landing
            earlier = [launches[f] for l2 in landings
                       if l2["node"] == a and rlaunch <= l2["t"] < land["t"] and l2["owner"] == p
                       for f in l2["fleets"] if f in launches and launches[f]["owner"] == p]
            reinf = own_in + earlier
            reinf_ships = sum(r["ships"] for r in reinf)
            bounce = [r for r in reinf if r["src"] == b and r["t"] > t]
            held = land["owner"] == p
            garrison = land["was_ships"]
            # without the reinforcement: garrison minus what reinforced it, against the strike
            alone = garrison - sum(r["ships"] for r in earlier)
            decisive = held and reinf_ships > 0 and alone * adv <= struck
            episodes.append({
                "match": blob["match_id"], "seat": p, "human": p == human, "bot": on_turn.strategies[p],
                "rival": sorted({on_turn.strategies[r["owner"]] for r in rf}),
                "by_hand": t in hand if p == human else False,
                "thin_t": t, "a": a, "b": b, "sent": L["ships"], "left": L["left"],
                "strike_t": land["t"], "struck": struck, "bit": bool(after), "rlaunch": rlaunch,
                "reinf_detail": [(r["t"], r["src"], r["ships"]) for r in reinf],
                "held": held, "reinf": reinf_ships, "bounce": sum(r["ships"] for r in bounce),
                "decisive": decisive, "rival_lost": struck if held else struck - land["ships"],
                "garrison": garrison,
            })
            break
    turns = len(log.turns)
    return {"match": blob["match_id"], "game_key": blob["game_key"], "human": human,
            "strategies": on_turn.strategies, "turns": turns, "hand_turns": len(hand),
            "winner": st.winner, "thins": {str(k): v for k, v in per_seat.items()},
            "episodes": episodes}


def read(jobs: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    games = OUT / "games.jsonl"
    done = set()
    if games.exists():
        done = {json.loads(line)["match"] for line in games.read_text().splitlines() if line.strip()}
    rows = public_rows("public_replays", "select=match_id,game_key,turns,won,rules_version,log")
    rows = [r for r in rows if r.get("rules_version") == engine.RULES_VERSION and r.get("log")
            and r["match_id"] not in done]
    print(f"{len(rows)} replays to read ({len(done)} already read)", flush=True)
    with (ProcessPoolExecutor(jobs) as ex, games.open("a") as g,
          (OUT / "episodes.jsonl").open("a") as e):
        futs = [ex.submit(mine, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            for ep in res.pop("episodes", []):
                e.write(json.dumps(ep) + "\n")
            g.write(json.dumps(res) + "\n")
            g.flush()
            e.flush()
            if i % 50 == 0 or i == len(futs):
                print(f"  {i}/{len(futs)}", flush=True)


def report() -> None:
    games = [json.loads(line) for line in (OUT / "games.jsonl").read_text().splitlines()]
    games = [g for g in games if "error" not in g]
    eps = [json.loads(line) for line in (OUT / "episodes.jsonl").read_text().splitlines()]

    def who(g, seat) -> str:
        return "person" if g["human"] is not None and int(seat) == g["human"] else g["strategies"][str(seat)]

    thins, turns, seats = Counter(), Counter(), Counter()
    for g in games:
        for seat, n in g["thins"].items():
            thins[who(g, seat)] += n
        for seat in g["strategies"]:
            turns[who(g, seat)] += g["turns"]
            seats[who(g, seat)] += 1
    by = defaultdict(list)
    for e in eps:
        by["person" if e["human"] else e["bot"]].append(e)
    print(f"{len(games)} posted games on the current rules\n")
    print(f"{'seat':12s} {'games':>5s} {'thins/100 turns':>16s} {'struck':>7s} {'bit':>5s} {'held':>5s} "
          f"{'reinforced':>11s} {'bounce':>7s} {'decisive':>9s}")
    for w in sorted(thins, key=lambda w: -thins[w] / turns[w]):
        bit = [e for e in by[w] if e["bit"]]
        held = [e for e in bit if e["held"]]
        print(f"{w:12s} {seats[w]:5d} {100 * thins[w] / turns[w]:16.2f} {len(by[w]):7d} {len(bit):5d} "
              f"{len(held):5d} {sum(e['reinf'] > 0 for e in held):11d} {sum(e['bounce'] > 0 for e in held):7d} "
              f"{sum(e['decisive'] for e in held):9d}")
    person = [e for e in by["person"] if e["bit"]]
    held = [e for e in person if e["held"]]
    if not held:
        return
    n = turns["person"]
    print(f"\nthe person, per 100 turns: thins {100 * thins['person'] / n:.2f}, bit {100 * len(person) / n:.2f}, "
          f"bit and held {100 * len(held) / n:.2f}, with a bounce "
          f"{100 * sum(e['bounce'] > 0 for e in held) / n:.2f}")
    print(f"held baits: strike {statistics.median(e['struck'] for e in held)} ships (median), "
          f"{sum(e['struck'] for e in held) / len(held):.1f} lost by the rival each; the rival launched "
          f"{statistics.median(e['rlaunch'] - e['thin_t'] for e in held)} turn(s) after the thin (median); "
          f"answered after the rival's launch in "
          f"{sum(any(r[0] >= e['rlaunch'] for r in e['reinf_detail']) for e in held)} of {len(held)}")
    print("the person's baits that bit, by the rival that struck:")
    rivals = defaultdict(list)
    for e in person:
        for r in e["rival"]:
            rivals[r].append(e)
    for r, es in sorted(rivals.items(), key=lambda x: -len(x[1])):
        print(f"  {r:11s} bit {len(es):4d}  held {sum(e['held'] for e in es) / len(es):.0%}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true", help="report from the ledger only")
    ap.add_argument("--jobs", type=int, default=22)
    args = ap.parse_args(argv)
    if not args.report:
        read(args.jobs)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
