"""How actuary at Learning fared in the posted games against a person, read turn
by turn from the public replays, and what its memory could have read off them.

    uv run python tools/learning_humans.py             # fetch, replay what is new, report
    uv run python tools/learning_humans.py --baseline  # roster self-play for the same rates
    uv run python tools/learning_humans.py --private   # also the uploaded (unposted) logs,
                                                       # with SUPABASE_URL and SUPABASE_SECRET_KEY

For every actuary seat (Field or Learning) in a current log with a person at
seat 1: its fate (peak systems, turns alive, systems lost to the person and to
other bots, and taken from the person); on each Learning turn whether the
shipped bot reproduces the recorded orders, and how Field would have ordered on
the same board (what each leaves on the person's border); every (source,
target) pair the person held next to actuary, with the chance Learning's memory
gave it and the prior's; and every launch the person made at an actuary system.

Each game is one JSON line in `results/learning-humans/games.jsonl`, written as
it finishes and resumed on rerun; the report reads that file. The posted logs
are all wins for the person (`public_replays` shows only the log behind a posted
score), so nothing here is a win rate; `--private` reads `game_logs`, where a
player who ticked *Share replays* also leaves lost and abandoned games.
Findings: docs/design/learning.md, "The posted games against Learning".
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starconquest import ai, engine, mapgen, replay, settings
from starconquest.model import AiParams

ai.load_models()
actuary = sys.modules["sc_model_actuary"]

ROOT = Path("results/learning-humans")
PUBLIC = ROOT / "public_replays.jsonl"
PRIVATE = ROOT / "private_logs.jsonl"
OUT = ROOT / "games.jsonl"
BASELINE = ROOT / "baseline.jsonl"
HUMAN = replay.HUMAN_SEAT
LEARNING, FIELD = actuary.LEARNING, actuary.FIELD
STOPS = ((LEARNING, "Learning"), (FIELD, "Field"))
BANDS = ("<1", "1-1.5", "1.5-2", "2-3", "3+")
GARRISONS = ((0, 1, "0"), (1, 2, "1"), (2, 4, "2-3"), (4, 8, "4-7"), (8, 10**9, "8+"))
MIN_HAND = 10
POOL_WEIGHT = 4.0       # pseudo-strikes the prior is worth in the pooled multiplier


def band(ratio: float) -> str:
    return ("<1" if ratio < 1 else "1-1.5" if ratio < 1.5 else "1.5-2" if ratio < 2
            else "2-3" if ratio < 3 else "3+")


def garrison_band(ships: int) -> str:
    return next(label for lo, hi, label in GARRISONS if lo <= ships < hi)


def rate(hits, total) -> float:
    return hits / total if total else 0.0


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_public() -> None:
    """Every public replay, with the publishable key."""
    from tools.config_census import public_rows
    rows = public_rows("public_replays", "select=match_id,log,won&order=match_id")
    ROOT.mkdir(parents=True, exist_ok=True)
    with PUBLIC.open("w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")
    print(f"fetched {len(rows)} public replays", flush=True)


def fetch_private() -> None:
    """Every uploaded log (`game_logs`, the longest row per match), with the
    secret key: the games a sharing player lost or abandoned are only here."""
    from tools.config_census import Supabase
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        sys.exit("--private needs SUPABASE_URL and SUPABASE_SECRET_KEY")
    rows = Supabase(url, key).select("game_logs", "select=match_id,turns,won,finished,log&order=id.asc")
    longest: dict[str, dict] = {}
    for row in rows:
        if row["match_id"] not in longest or row["turns"] > longest[row["match_id"]]["turns"]:
            longest[row["match_id"]] = row
    with PRIVATE.open("w") as out:
        for row in longest.values():
            out.write(json.dumps(row) + "\n")
    print(f"fetched {len(longest)} uploaded matches", flush=True)


def load_logs(path: Path) -> list[replay.GameLog]:
    logs = []
    for line in path.open():
        row = json.loads(line)
        try:
            log = replay.GameLog.decode(row["log"])
        except ValueError:
            continue
        if log.is_current and log.hand_turns >= MIN_HAND:
            logs.append(log)
    return logs


# --------------------------------------------------------------------------- #
# One game
# --------------------------------------------------------------------------- #
def order_set(orders, pid) -> dict[tuple[int, int], int]:
    out: dict[tuple[int, int], int] = defaultdict(int)
    for o in orders:
        if o.owner_id == pid and o.ships > 0:
            out[(o.source_id, o.dest_id)] += o.ships
    return dict(out)


def kept_next_to(state, pid, sent, rival) -> dict[int, int]:
    """Ships each of ``pid``'s systems bordering ``rival`` keeps after ``sent``."""
    out = {}
    for sid, s in state.systems.items():
        if s.owner_id == pid and any(state.systems[n].owner_id == rival for n in s.neighbors):
            out[sid] = s.ships - sum(v for (src, _), v in sent.items() if src == sid)
    return out


def actuary_seats(state) -> list[int]:
    return [pid for pid, p in state.players.items()
            if pid != HUMAN and not p.is_neutral and p.ai_strategy == "actuary"]


def analyse(log: replay.GameLog, source: str) -> dict:
    actuary.reset()
    seats: dict[int, dict] = {}
    rec = {"match": log.match_id, "source": source, "turns": log.turn_count,
           "hand": log.hand_turns, "won": log.winner == HUMAN, "finished": log.finished,
           "players": log.settings.get("players"), "nodes": log.settings.get("nodes"),
           "created": log.created_at[:10], "seats": seats,
           "human_strikes": [], "pairs": [], "learning_turns": [], "losses": []}
    prev_owner: dict[int, int] = {}

    def on_turn(state):
        nonlocal prev_owner
        i = state.turn
        owners = {sid: s.owner_id for sid, s in state.systems.items()}
        for sid, before in prev_owner.items():
            if owners[sid] != before and before != 0:
                rec["losses"].append({"turn": i, "sid": sid, "from": before, "to": owners[sid]})
        prev_owner = owners
        for pid in actuary_seats(state):
            p = state.players[pid]
            held = sum(1 for o in owners.values() if o == pid)
            s = seats.setdefault(pid, {"stop": actuary._stop_of(p), "aux": p.ai_params.aux,
                                       "peak": 0, "last_alive": None, "repro": [0, 0]})
            s["peak"] = max(s["peak"], held)
            if held:
                s["last_alive"] = i
        if state.winner is not None or i >= log.turn_count:
            return
        orders = log.orders_for(i)
        human_sent = order_set(orders, HUMAN)
        snap = actuary._Snap(state)
        curves = actuary._curves(state)
        prior = actuary.strike_curve(actuary.EMPTY, actuary.PLAYER, 0)
        hostile = actuary._hostile_inbound(snap)
        seen = actuary.models_for(state).get(HUMAN, actuary.EMPTY).turns
        hand = not log.turn_is_ai(i)
        for pid in actuary_seats(state):
            if state.is_defeated(pid):
                continue
            stop = seats[pid]["stop"]
            borders = any(owners[n] == HUMAN for sid, sys_ in state.systems.items()
                          if owners[sid] == pid for n in sys_.neighbors)
            if borders and hand:
                for sid in state.systems:
                    if owners[sid] != HUMAN or snap.ships[sid] <= 0:
                        continue
                    pressed = 1 if hostile.get(sid, 0) > 0 else 0
                    for opt in actuary._options(snap, state, sid):
                        if owners[opt.target] != pid:
                            continue
                        b = actuary._ratio_bin(opt.ratio)
                        struck = human_sent.get((sid, opt.target), 0)
                        pair = {"turn": i, "seat": pid, "stop": stop, "src": sid, "dst": opt.target,
                                "ratio": round(opt.ratio, 2), "pressed": pressed, "struck": struck,
                                "dst_ships": snap.ships[opt.target], "p_mem": round(curves[HUMAN][b], 4),
                                "p_prior": round(prior[b], 4), "mem_turns": seen}
                        rec["pairs"].append(pair)
                        if struck:
                            rec["human_strikes"].append(dict(pair, ships=struck, lands=i + opt.turns))
            if stop != LEARNING:
                continue
            actual = order_set(orders, pid)
            got = order_set(actuary.decide(copy.deepcopy(state), pid), pid)
            seats[pid]["repro"][0] += int(got == actual)
            seats[pid]["repro"][1] += 1
            if not borders:
                continue
            probe = copy.deepcopy(state)
            probe.players[pid].ai_params = copy.copy(probe.players[pid].ai_params)
            probe.players[pid].ai_params.aux = float(FIELD)
            field = order_set(actuary.decide(probe, pid), pid)
            rec["learning_turns"].append({
                "turn": i, "seat": pid, "same_as_field": field == actual,
                "kept_learning": kept_next_to(state, pid, actual, HUMAN),
                "kept_field": kept_next_to(state, pid, field, HUMAN),
                "sent_learning": sum(actual.values()), "sent_field": sum(field.values()),
                "at_human_learning": sum(v for (_, d), v in actual.items() if owners[d] == HUMAN),
                "at_human_field": sum(v for (_, d), v in field.items() if owners[d] == HUMAN)})

    replay.reconstruct(log, on_turn=on_turn)
    return rec


def replay_new(source: str, path: Path) -> None:
    done = {json.loads(line)["match"] for line in OUT.open()} if OUT.exists() else set()
    todo = [log for log in load_logs(path) if log.match_id not in done
            and "actuary" in (list(log.strategies.values()) + list(log.settings.get("ai_strategy") or []))]
    print(f"{source}: {len(done)} done, {len(todo)} to replay", flush=True)
    with OUT.open("a") as out:
        for n, log in enumerate(todo, 1):
            rec = analyse(log, source)
            if rec["seats"]:
                out.write(json.dumps(rec) + "\n")
                out.flush()
            print(f"{n}/{len(todo)} {log.match_id} turns {rec['turns']} "
                  f"stops {[s['stop'] for s in rec['seats'].values()]}", flush=True)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #
def load_records() -> list[dict]:
    recs = [json.loads(line) for line in OUT.open()]
    for rec in recs:
        rec["seats"] = {int(k): v for k, v in rec["seats"].items()}
        for t in rec["learning_turns"]:
            t["kept_learning"] = {int(k): v for k, v in t["kept_learning"].items()}
            t["kept_field"] = {int(k): v for k, v in t["kept_field"].items()}
    return recs


def seat_rows(recs, stop) -> list[dict]:
    rows = []
    for rec in recs:
        for pid, s in rec["seats"].items():
            if s["stop"] != stop:
                continue
            rows.append({
                "rec": rec, "pid": pid, "repro": s["repro"], "peak": s["peak"],
                "alive": (s["last_alive"] or 0) / max(1, rec["turns"]),
                "eliminated": (s["last_alive"] or 0) < rec["turns"] - 1,
                "lost_h": sum(1 for l in rec["losses"] if l["from"] == pid and l["to"] == HUMAN),
                "lost_b": sum(1 for l in rec["losses"] if l["from"] == pid and l["to"] not in (HUMAN, pid, 0)),
                "took_h": sum(1 for l in rec["losses"] if l["to"] == pid and l["from"] == HUMAN)})
    return rows


def retaken(recs, stop, taker, loser_is_seat: bool, within: int) -> tuple[int, int]:
    """Systems a seat at ``stop`` took from the person (or the person from it),
    and how many the loser took back within ``within`` turns."""
    n = back = 0
    for rec in recs:
        for l in rec["losses"]:
            seat = rec["seats"].get(l["to"] if loser_is_seat is False else l["from"])
            if not seat or seat["stop"] != stop:
                continue
            if (l["from"], l["to"]) != ((HUMAN, l["to"]) if not loser_is_seat else (l["from"], HUMAN)):
                continue
            n += 1
            back += any(m["sid"] == l["sid"] and m["from"] == l["to"] and m["to"] == l["from"]
                        and l["turn"] < m["turn"] <= l["turn"] + within for m in rec["losses"])
    return back, n


def report(recs) -> None:
    for source in sorted({r["source"] for r in recs}):
        rs = [r for r in recs if r["source"] == source]
        print(f"{source}: {len(rs)} games with an actuary seat; the person won {sum(r['won'] for r in rs)}, "
              f"{sum(r['finished'] for r in rs)} finished")
    for stop, name in STOPS:
        rows = seat_rows(recs, stop)
        if not rows:
            continue
        print(f"\n== {name}: {len(rows)} seats; players {dict(Counter(r['rec']['players'] for r in rows))}, "
              f"systems median {st.median(r['rec']['nodes'] for r in rows)}, game turns median "
              f"{st.median(r['rec']['turns'] for r in rows)}")
        print(f"   peak systems median {st.median(r['peak'] for r in rows)}; eliminated before the end "
              f"{sum(r['eliminated'] for r in rows)}; alive for {st.mean(r['alive'] for r in rows):.0%} of the game")
        print(f"   systems lost to the person {sum(r['lost_h'] for r in rows)}, to other bots "
              f"{sum(r['lost_b'] for r in rows)}, taken from the person {sum(r['took_h'] for r in rows)}")
        if stop == LEARNING:
            a, b = (sum(x) for x in zip(*(r["repro"] for r in rows)))
            print(f"   the shipped bot reproduces {a}/{b} recorded turns; by game:",
                  " ".join(f"{r['rec']['created'][5:]}:{r['repro'][0]}/{r['repro'][1]}"
                           for r in sorted(rows, key=lambda r: r["rec"]["created"])))
        for within in (3, 5):
            back, n = retaken(recs, stop, HUMAN, False, within)
            back2, n2 = retaken(recs, stop, HUMAN, True, within)
            print(f"   within {within} turns: the person retook {back}/{n} ({rate(back, n):.0%}) of its captures; "
                  f"it retook {back2}/{n2} ({rate(back2, n2):.0%}) of what the person took")

    turns = [t for rec in recs for t in rec["learning_turns"]]
    if turns:
        same = sum(t["same_as_field"] for t in turns)
        print(f"\n== Learning turns on the person's border: {len(turns)}; same orders as Field on {same} ({rate(same, len(turns)):.0%})")
        print(f"   ships kept on that border per turn: Learning {st.mean(sum(t['kept_learning'].values()) for t in turns):.2f}, "
              f"Field {st.mean(sum(t['kept_field'].values()) for t in turns):.2f}; sent at the person "
              f"{st.mean(t['at_human_learning'] for t in turns):.2f} against {st.mean(t['at_human_field'] for t in turns):.2f}; "
              f"sent anywhere {st.mean(t['sent_learning'] for t in turns):.2f} against {st.mean(t['sent_field'] for t in turns):.2f}")
        tot, struck, lost = Counter(), Counter(), Counter()
        for rec in recs:
            for t in rec["learning_turns"]:
                for sid, kept in t["kept_learning"].items():
                    if sid not in t["kept_field"]:
                        continue
                    cls = ("thinner" if kept < t["kept_field"][sid] else
                           "thicker" if kept > t["kept_field"][sid] else "same")
                    tot[cls] += 1
                    struck[cls] += any(s["dst"] == sid and t["turn"] <= s["turn"] <= t["turn"] + 1
                                       for s in rec["human_strikes"])
                    lost[cls] += any(l["sid"] == sid and l["from"] == t["seat"] and l["to"] == HUMAN
                                     and t["turn"] < l["turn"] <= t["turn"] + 3 for l in rec["losses"])
        print("   border systems Learning garrisoned thinner / the same / thicker than Field, and the share "
              "the person launched at within 2 turns, lost to the person within 3:")
        for cls in ("thinner", "same", "thicker"):
            print(f"     {cls:8} {tot[cls]:5}  struck {rate(struck[cls], tot[cls]):5.1%}  lost {rate(lost[cls], tot[cls]):5.1%}")

    pairs = [p for rec in recs for p in rec["pairs"] if p["pressed"] == 0]
    for stop, name in STOPS:
        ps = [p for p in pairs if p["stop"] == stop]
        if not ps:
            continue
        print(f"\n== the person's (source, target) pairs next to {name}, unpressed: {len(ps)}, struck "
              f"{sum(1 for p in ps if p['struck'])}; Brier memory {brier(ps, 'p_mem'):.4f}, prior "
              f"{brier(ps, 'p_prior'):.4f}, never {st.mean(bool(p['struck']) for p in ps):.4f}")
        print("   band      n   struck   rate   memory  prior   read as certain (8p >= 1)")
        for b in BANDS:
            q = [p for p in ps if band(p["ratio"]) == b]
            if q:
                print(f"   {b:6} {len(q):5} {sum(1 for p in q if p['struck']):6}   {st.mean(bool(p['struck']) for p in q):.3f}  "
                      f"{st.mean(p['p_mem'] for p in q):.3f}   {st.mean(p['p_prior'] for p in q):.3f}   "
                      f"{st.mean(8 * p['p_mem'] >= 1 for p in q):.0%}")
    strikes = [s for rec in recs for s in rec["human_strikes"] if s["stop"] == LEARNING]
    if strikes:
        under = [s for s in strikes if 8 * s["p_mem"] < 1]
        print(f"\n== the person's launches at Learning systems: {len(strikes)}; Learning read {len(under)} "
              f"({rate(len(under), len(strikes)):.0%}) as less than certain, all at ratios under 1.5")
    print("\n== the person's per-turn strike rate on an actuary system, by its garrison (all stops):")
    for _lo, _hi, label in GARRISONS:
        q = [p for p in pairs if garrison_band(p["dst_ships"]) == label]
        if q:
            print(f"   garrison {label:4} pairs {len(q):5}  struck {st.mean(bool(p['struck']) for p in q):5.1%}")
    pooled(recs)


def brier(ps, key) -> float:
    return st.mean((p[key] - (1 if p["struck"] else 0)) ** 2 for p in ps)


def pooled(recs) -> None:
    """Would one learned multiplier on the prior curve read the person faster
    than 31 bins? Each pair is predicted before it is counted, in turn order."""
    rows = []
    for rec in recs:
        hits = expected = 0.0
        by_turn: dict[int, list] = defaultdict(list)
        for p in rec["pairs"]:
            if p["pressed"] == 0:
                by_turn[p["turn"]].append(p)
        for turn in sorted(by_turn):
            m = (hits + POOL_WEIGHT) / (expected + POOL_WEIGHT)
            rows.extend((p, min(1.0, m * p["p_prior"])) for p in by_turn[turn])
            for p in by_turn[turn]:
                hits += bool(p["struck"])
                expected += p["p_prior"]
    if not rows:
        return

    def score(q, f):
        return st.mean((f(p, pp) - (1 if p["struck"] else 0)) ** 2 for p, pp in q)
    print(f"\n== a pooled multiplier on the prior ({POOL_WEIGHT:g} pseudo-strikes) against the binned memory, "
          f"the person's pairs in turn order ({len(rows)}):")
    print("   boards seen     n    rate   pooled   binned   prior")
    for lo, hi in ((0, 10), (10, 25), (25, 50), (50, 10**6)):
        q = [r for r in rows if lo <= r[0]["mem_turns"] < hi]
        if q:
            print(f"   {lo:3}-{hi if hi < 10**6 else '':<4} {len(q):6}   {st.mean(bool(p['struck']) for p, _ in q):.3f}  "
                  f"{score(q, lambda p, pp: pp):.4f}   {score(q, lambda p, pp: p['p_mem']):.4f}   "
                  f"{score(q, lambda p, pp: p['p_prior']):.4f}")
    print(f"   all {len(rows):6}: pooled {score(rows, lambda p, pp: pp):.4f}  binned "
          f"{score(rows, lambda p, pp: p['p_mem']):.4f}  prior {score(rows, lambda p, pp: p['p_prior']):.4f}")
    ends = []
    for rec in recs:
        ps = [p for p in rec["pairs"] if p["pressed"] == 0]
        if len(ps) >= 20:
            ends.append((sum(bool(p["struck"]) for p in ps) + POOL_WEIGHT)
                        / (sum(p["p_prior"] for p in ps) + POOL_WEIGHT))
    if ends:
        ends.sort()
        print(f"   the person's multiplier at the end of {len(ends)} games: median {st.median(ends):.2f}, "
              f"quartiles {ends[len(ends) // 4]:.2f}-{ends[3 * len(ends) // 4]:.2f}")


# --------------------------------------------------------------------------- #
# Roster self-play, for the same rates
# --------------------------------------------------------------------------- #
LINEUPS = (("actuary", "marshal", "thinker"), ("actuary", "marshal", "evolved"),
           ("actuary", "thinker", "evolved"))


def baseline(seeds: range, nodes: int = 18, max_turns: int = 400) -> None:
    """Learning in three-seat games on random maps: how often rivals retake what
    it captures, how often it retakes, and rivals' strike rate on its systems by
    garrison. Split by whether Learning won, since the posted games are all
    losses for it."""
    from tests import sim
    done = {json.loads(line)["key"] for line in BASELINE.open()} if BASELINE.exists() else set()
    with BASELINE.open("a") as out:
        for seed in seeds:
            for lineup in LINEUPS:
                key = f"{seed}:{nodes}:{','.join(lineup)}"
                if key in done:
                    continue
                settings._apply_globals(settings.Settings())
                actuary.reset()
                state = mapgen.generate(seed, "random", nodes, len(lineup))
                for p in state.players.values():
                    p.is_human = False
                sim._assign_strategies(state, list(lineup))
                me = next(pid for pid, p in state.players.items() if p.ai_strategy == "actuary")
                state.players[me].ai_params = AiParams(aux=float(LEARNING))
                losses: list[tuple[int, int, int, int]] = []
                pairs: Counter = Counter()
                hits: Counter = Counter()
                prev: dict[int, int] | None = None

                def note_losses(prev, state, losses=losses):
                    owners = {sid: s.owner_id for sid, s in state.systems.items()}
                    if prev:
                        losses.extend((state.turn, sid, b, owners[sid]) for sid, b in prev.items()
                                      if owners[sid] != b and b != 0)
                    return owners

                while state.winner is None and state.turn < max_turns:
                    prev = note_losses(prev, state)
                    issued: dict[int, list] = {}

                    def recording(st_, pid, issued=issued):
                        issued[pid] = ai.decide(st_, pid) or []
                        return issued[pid]

                    before = copy.deepcopy(state)
                    engine.end_turn(state, decide=recording)
                    for pid, orders in issued.items():
                        if pid == me:
                            continue
                        sent = {(o.source_id, o.dest_id) for o in orders if o.ships > 0}
                        for sid, s in before.systems.items():
                            if s.owner_id != pid or s.ships <= 0:
                                continue
                            for n in s.neighbors:
                                if before.systems[n].owner_id == me:
                                    label = garrison_band(before.systems[n].ships)
                                    pairs[label] += 1
                                    hits[label] += (sid, n) in sent
                note_losses(prev, state)

                def back(taken, within, losses=losses):
                    return sum(any(m[1] == sid and m[2] == to and m[3] == frm and t < m[0] <= t + within
                                   for m in losses) for t, sid, frm, to in taken)
                caps = [l for l in losses if l[3] == me and l[2] != 0]
                taken = [l for l in losses if l[2] == me and l[3] != 0]
                row = {"key": key, "won": state.winner == me, "turns": state.turn,
                       "captures": len(caps), "retaken3": back(caps, 3), "retaken5": back(caps, 5),
                       "taken": len(taken), "retook3": back(taken, 3), "retook5": back(taken, 5),
                       "pairs": dict(pairs), "hits": dict(hits)}
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"{key} won={row['won']} turns {row['turns']}", flush=True)
    rows = [json.loads(line) for line in BASELINE.open()]
    print(f"\n== roster self-play, Learning in {len(rows)} three-seat games at {nodes} systems")
    for label, sel in (("all", rows), ("Learning won", [r for r in rows if r["won"]]),
                       ("Learning lost", [r for r in rows if not r["won"]])):
        c, t = sum(r["captures"] for r in sel), sum(r["taken"] for r in sel)
        if c and t:
            print(f"   {label:14} n {len(sel):3}: rivals retook its captures within 3: "
                  f"{rate(sum(r['retaken3'] for r in sel), c):.0%}, 5: {rate(sum(r['retaken5'] for r in sel), c):.0%} "
                  f"(n {c}); it retook within 3: {rate(sum(r['retook3'] for r in sel), t):.0%}, "
                  f"5: {rate(sum(r['retook5'] for r in sel), t):.0%} (n {t})")
    pairs, hits = Counter(), Counter()
    for r in rows:
        pairs.update(r["pairs"])
        hits.update(r["hits"])
    for _lo, _hi, label in GARRISONS:
        print(f"   rivals' strike rate on a Learning system with garrison {label:4}: "
              f"{rate(hits[label], pairs[label]):5.1%} (pairs {pairs[label]})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch", action="store_true", help="refetch the public replays")
    ap.add_argument("--private", action="store_true", help="also read game_logs (secret key)")
    ap.add_argument("--baseline", action="store_true", help="roster self-play for the same rates")
    ap.add_argument("--seeds", default="1-20", help="baseline seeds, lo-hi")
    args = ap.parse_args(argv)
    ROOT.mkdir(parents=True, exist_ok=True)
    if args.baseline:
        lo, hi = (int(x) for x in args.seeds.split("-"))
        baseline(range(lo, hi + 1))
        return 0
    if args.fetch or not PUBLIC.exists():
        fetch_public()
    replay_new("public", PUBLIC)
    if args.private:
        fetch_private()
        replay_new("private", PRIVATE)
    report(load_records())
    return 0


if __name__ == "__main__":
    sys.exit(main())
