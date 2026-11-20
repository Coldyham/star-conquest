#!/usr/bin/env python3
"""Rerun the leaderboard bot rows a changed bot could have moved, with a before/after record.

A change to one bot reaches more rows than its own. A row is affected when the
bot it replays runs the changed bot's ``decide`` (it is that bot, or deals or
borrows it: ``uses``), or when any opponent seat on its map does. Dealing and
borrowing are read from the bots that do them: ``chaotic``'s ``POOL``, and
knower's ``EXTERNAL_CANDIDATES`` (Search and Council) and ``COUNCIL_CANDIDATES``
(Council). Only rows computed before the change reached main are rerun.

    uv run python tools/bot_rerun.py rusherplus --plan       # what it touched; writes nothing
    uv run python tools/bot_rerun.py rusherplus --env .env   # snapshot, rerun, report
    uv run python tools/bot_rerun.py rusherplus --before 2026-11-11T16:51 --env .env
    uv run python tools/bot_rerun.py --keys keys.txt --bots knower marshal --env .env
    uv run python tools/bot_rerun.py --report results/bot_rerun/rusherplus-20261111T1651

``--before`` defaults to when the bot's file last changed on ``origin/main``
(fetch first). ``--plan`` and ``--report`` read with the board's publishable key
when no secret key is set; a rerun needs ``SUPABASE_URL`` and
``SUPABASE_SECRET_KEY``, from the environment or ``--env FILE``.

Each run keeps a folder under ``results/bot_rerun/``: ``before.jsonl`` and
``plan.json`` (the rows as they stood, and what to rerun; written once),
``done.txt`` (maps finished), ``logs/`` (one ``bot_replay`` log per map), then
``after.jsonl`` and ``changes.jsonl``. The same command again resumes it; delete
the folder to plan afresh. Each map is one ``bot_replay.py --game-key K
--recompute --bots ...`` process.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, config
from starconquest.settings import build_state
from tools.bot_replay import MISSING_CREDENTIALS, Supabase, _settings_for, credentials, replay_aux

RUNS = ROOT / "results" / "bot_rerun"
ROW_FIELDS = "game_key,bot,won,turns,lost,bot_timeouts,aux,engine_rev,computed_at"


# --------------------------------------------------------------------------- #
# Who runs whom
# --------------------------------------------------------------------------- #
def borrowed(name: str, aux: float) -> list[tuple[str, float]]:
    """The ``(strategy, aux)`` pairs a seat playing ``name`` at ``aux`` runs for
    itself, besides its own ``decide``."""
    family, aux = ai.canonical(name, aux)
    module = ai._module(family)
    if module is None:
        return []
    pairs: list[tuple[str, float | None]] = []
    if family == "chaotic":
        for entry in getattr(module, "POOL", ()):
            pick, _, stop = entry.partition(":")
            pairs.append((pick, float(stop) if stop else None))
    elif family == "knower":
        stop = module._stop_of(SimpleNamespace(aux=aux))
        if stop >= module.SEARCH_DEPTH_MAX:
            pairs += module.EXTERNAL_CANDIDATES
        if stop == module.COUNCIL:
            pairs += module.COUNCIL_CANDIDATES
    return [(n, config.AI_AUX if a is None else float(a)) for n, a in pairs]


def uses(name: str, aux: float, changed: str, seen: frozenset = frozenset()) -> bool:
    """Whether a seat playing ``name`` at ``aux`` ever runs ``changed``'s ``decide``."""
    family, aux = ai.canonical(name, aux)
    if family == ai.family(changed):
        return True
    if (family, aux) in seen:
        return False
    seen = seen | {(family, aux)}
    return any(uses(n, a, changed, seen) for n, a in borrowed(family, aux))


def opponents(game: dict) -> list[tuple[int, str, float]] | None:
    """``(seat, strategy, aux)`` of every seat but the replayed one, as dealt."""
    built = _settings_for(game)
    if built is None:
        return None
    state = build_state(*built)
    return [(p.id, p.ai_strategy, p.ai_params.aux) for p in state.players.values()
            if p.id != 0 and not p.is_human]


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def when(text: str) -> datetime:
    stamp = datetime.fromisoformat(text)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def plan_changed(games: Iterable[dict], rows: Iterable[dict], changed: str,
                 cutoff: datetime, bots: Iterable[str]) -> dict[str, dict]:
    """``{game_key: {"bots": [...], "why": str}}``: rows from before ``cutoff``
    that ``changed`` could have moved, for the bots in ``bots``."""
    wanted = set(bots)
    stale: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["bot"] in wanted and when(row["computed_at"]) < cutoff:
            stale[row["game_key"]].append(row["bot"])
    plan: dict[str, dict] = {}
    for game in games:
        key = game["game_key"]
        if key not in stale:
            continue
        seats = opponents(game)
        if seats is None:
            print(f"  skipping {key}: unreadable settings_json")
            continue
        rivals = [f"seat {pid} {name}@{aux:g}" for pid, name, aux in seats
                  if uses(name, aux, changed)]
        rerun = sorted(bot for bot in stale[key]
                       if rivals or uses(bot, replay_aux(bot), changed))
        if rerun:
            why = f"rival {', '.join(rivals)}" if rivals else "replayed bot runs it"
            plan[key] = {"bots": rerun, "why": why}
    return plan


def plan_keys(keys: Iterable[str], rows: Iterable[dict], bots: Iterable[str],
              cutoff: datetime | None) -> dict[str, dict]:
    """The listed maps' existing rows for ``bots``, those from before ``cutoff`` if given."""
    wanted, listed = set(bots), set(keys)
    picked: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if (row["game_key"] in listed and row["bot"] in wanted
                and (cutoff is None or when(row["computed_at"]) < cutoff)):
            picked[row["game_key"]].append(row["bot"])
    return {key: {"bots": sorted(b), "why": "listed"} for key, b in picked.items()}


def change_reached_main(changed: str) -> tuple[datetime, str]:
    """When the file defining ``changed`` last changed on ``origin/main``'s first-parent line."""
    module = ai._module(changed)
    if module is None or not getattr(module, "__file__", None):
        raise SystemExit(f"Can't find the file for {changed!r}; pass --before")
    path = Path(module.__file__).resolve().relative_to(ROOT)
    out = subprocess.run(
        ["git", "log", "origin/main", "--first-parent", "-1", "--format=%cI %h", "--", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    if not out:
        raise SystemExit(f"{path} has no history on origin/main; pass --before")
    return when(out[0]), f"{out[1]} changed {path} on origin/main"


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
def load_env(path: Path) -> None:
    """Set ``NAME=value`` lines from a dotenv file (``export``, quotes and comments allowed)."""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.removeprefix("export ").partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[name.strip()] = value


def public_credentials() -> tuple[str, str]:
    """The board's own URL and publishable key: enough to read ``games`` and ``bot_scores``."""
    text = (ROOT / "leaderboard" / "js" / "config.mjs").read_text()
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', text)
    key = re.search(r'SUPABASE_ANON_KEY\s*=\s*"([^"]+)"', text)
    return (url.group(1) if url else "", key.group(1) if key else "")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def compare(before: dict[tuple[str, str], dict],
            after: dict[tuple[str, str], dict]) -> tuple[list[dict], dict[str, dict]]:
    """Rows whose result moved, and per bot: rows, moved, wins gained and lost, and
    the turn and ships-lost change summed over rows won both times."""
    changes: list[dict] = []
    stats: dict[str, dict] = defaultdict(lambda: {"rows": 0, "moved": 0, "gained": 0,
                                                  "dropped": 0, "both": 0, "dturns": 0,
                                                  "dlost": 0})
    for pair, old in sorted(before.items()):
        new = after.get(pair)
        if new is None:
            continue
        s = stats[pair[1]]
        s["rows"] += 1
        if (old["won"], old["turns"], old["lost"]) != (new["won"], new["turns"], new["lost"]):
            s["moved"] += 1
            changes.append({"game_key": pair[0], "bot": pair[1],
                            "before": {k: old[k] for k in ("won", "turns", "lost")},
                            "after": {k: new[k] for k in ("won", "turns", "lost")}})
        s["gained"] += new["won"] and not old["won"]
        s["dropped"] += old["won"] and not new["won"]
        if old["won"] and new["won"]:
            s["both"] += 1
            s["dturns"] += new["turns"] - old["turns"]
            s["dlost"] += new["lost"] - old["lost"]
    return changes, dict(stats)


def report(run: Path, api: Supabase) -> None:
    before = {(r["game_key"], r["bot"]): r for r in read_jsonl(run / "before.jsonl")}
    done = set(read_lines(run / "done.txt"))
    before = {pair: row for pair, row in before.items() if pair[0] in done}
    rows = api.select("bot_scores", f"select={ROW_FIELDS}&order=game_key.asc,bot.asc")
    after = {(r["game_key"], r["bot"]): r for r in rows if (r["game_key"], r["bot"]) in before}
    changes, stats = compare(before, after)
    write_jsonl(run / "after.jsonl", (after[pair] for pair in sorted(after)))
    write_jsonl(run / "changes.jsonl", changes)
    print(f"\n{len(done)} maps rerun; {len(changes)} of {len(after)} rows moved "
          f"(changes in {_shown(run / 'changes.jsonl')})")
    print(f"{'bot':<12}{'rows':>6}{'moved':>7}{'+wins':>7}{'-wins':>7}"
          f"{'Δturns':>9}{'Δlost':>8}   (Δ: mean over rows won both times)")
    for bot, s in sorted(stats.items()):
        print(f"{bot:<12}{s['rows']:>6}{s['moved']:>7}{s['gained']:>7}{s['dropped']:>7}"
              f"{_mean(s['dturns'], s['both']):>9}{_mean(s['dlost'], s['both']):>8}")


def _mean(total: int, count: int) -> str:
    return f"{total / count:+.1f}" if count else "-"


def _shown(path: Path) -> Path:
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Rerunning
# --------------------------------------------------------------------------- #
def rerun_map(key: str, bots: list[str], logs: Path) -> bool:
    log = logs / f"{key.replace(':', '_')}.log"
    command = [sys.executable, str(ROOT / "tools" / "bot_replay.py"), "--game-key", key,
               "--recompute", "--deadline-minutes", "1440", "--bots", *bots]
    with log.open("w") as handle:
        code = subprocess.run(command, check=False, cwd=ROOT, stdout=handle,
                              stderr=subprocess.STDOUT).returncode
    return code == 0 and f"Wrote {len(bots)} rows." in log.read_text()


def run_plan(run: Path, plan: dict[str, dict], jobs: int) -> int:
    logs = run / "logs"
    logs.mkdir(exist_ok=True)
    done = set(read_lines(run / "done.txt"))
    todo = sorted((k for k in plan if k not in done),
                  key=lambda k: ("knower" not in plan[k]["bots"], -len(plan[k]["bots"]), k))
    print(f"{len(todo)} maps to rerun ({len(done)} already done), {jobs} at a time")
    failed = 0
    with ThreadPoolExecutor(jobs) as pool, (run / "done.txt").open("a") as ledger:
        futures = {pool.submit(rerun_map, k, plan[k]["bots"], logs): k for k in todo}
        for n, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            if future.result():
                ledger.write(key + "\n")
                ledger.flush()
                print(f"[{n}/{len(todo)}] done {key} ({' '.join(plan[key]['bots'])})", flush=True)
            else:
                failed += 1
                print(f"[{n}/{len(todo)}] FAIL {key}: see {logs / key.replace(':', '_')}.log",
                      flush=True)
    if failed:
        print(f"{failed} maps failed; run the same command again to retry them")
    return failed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("changed", nargs="?", default="",
                        help="the bot that changed: rerun every row it could have moved")
    parser.add_argument("--keys", type=Path, help="instead: rerun these maps (one game_key a line)")
    parser.add_argument("--bots", nargs="*", default=None,
                        help="only these replayed bots (default: every registered one)")
    parser.add_argument("--before", default="",
                        help="only rows computed before this (ISO date or time, UTC, or 'now'); "
                             "with a changed bot, defaults to when its file last changed on main")
    parser.add_argument("--env", type=Path, help="read SUPABASE_URL/SUPABASE_SECRET_KEY from this file")
    parser.add_argument("--plan", action="store_true", help="print what would be rerun and stop")
    parser.add_argument("--report", type=Path, help="compare a run folder's before with the board now")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2),
                        help="maps rerun at once")
    args = parser.parse_args(argv)
    if not args.report and bool(args.changed) == bool(args.keys):
        parser.error("name a changed bot or pass --keys (one of them)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env:
        load_env(args.env)
    url, key = credentials()
    if not (url and key):
        if not (args.plan or args.report):
            print(MISSING_CREDENTIALS, file=sys.stderr)
            return 2
        url, key = public_credentials()
    api = Supabase(url, key)

    if args.report:
        report(args.report.resolve(), api)
        return 0

    ai.load_models()
    roster = args.bots if args.bots is not None else ai.available_strategies()
    unknown = [bot for bot in roster if bot not in ai.STRATEGIES]
    if unknown:
        print(f"Unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2

    if args.before == "now":
        cutoff, source = datetime.now(UTC), "now"
    elif args.before:
        cutoff, source = when(args.before), "--before"
    elif args.changed:
        cutoff, source = change_reached_main(ai.family(args.changed))
    else:
        cutoff, source = None, ""
    label = (f"{ai.family(args.changed)}" if args.changed else f"keys-{args.keys.stem}")
    if cutoff is not None:
        label += f"-{cutoff.astimezone(UTC):%Y%m%dT%H%M}"
        print(f"rows computed before {cutoff.astimezone(UTC):%Y-%m-%d %H:%M} UTC ({source})")
    run = RUNS / label

    if (run / "plan.json").exists() and not args.plan:
        plan = json.loads((run / "plan.json").read_text())["maps"]
        print(f"resuming {_shown(run)}")
    else:
        rows = api.select("bot_scores", f"select={ROW_FIELDS}&order=game_key.asc,bot.asc")
        if args.changed:
            games = api.select("games", "select=game_key,seed,settings_json&order=game_key.asc")
            plan = plan_changed(games, rows, args.changed, cutoff, roster)
        else:
            plan = plan_keys(read_lines(args.keys), rows, roster, cutoff)
        pairs = {(k, b) for k, entry in plan.items() for b in entry["bots"]}
        if args.plan:
            for k, entry in sorted(plan.items()):
                print(f"  {k:<20} {' '.join(entry['bots']):<55} {entry['why']}")
            by_bot = defaultdict(int)
            for _, b in pairs:
                by_bot[b] += 1
            print(f"{len(plan)} maps, {len(pairs)} rows: "
                  + ", ".join(f"{b} {n}" for b, n in sorted(by_bot.items())))
            return 0
        if not plan:
            print("Nothing to rerun.")
            return 0
        run.mkdir(parents=True, exist_ok=True)
        write_jsonl(run / "before.jsonl", (r for r in rows if (r["game_key"], r["bot"]) in pairs))
        (run / "plan.json").write_text(json.dumps(
            {"changed": args.changed, "keys": str(args.keys or ""),
             "cutoff": cutoff.isoformat() if cutoff else "", "cutoff_from": source,
             "maps": plan}, indent=1))
        print(f"{len(plan)} maps, {len(pairs)} rows; before saved in "
              f"{_shown(run / 'before.jsonl')}")

    failed = run_plan(run, plan, args.jobs)
    report(run, api)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
