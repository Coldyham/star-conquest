#!/usr/bin/env python3
"""Save each daily puzzle's par: the best score the par tools find on its stored map.

The par is ``tools/par_best.py``'s answer on the puzzle exactly as ``tools/daily.py``
stored it (the row's own settings, never a map dealt here), saved to the board's
``puzzle_pars`` table with what it rests on:

- ``proved``: a full search finished, so no faster win exists (with ``turns_only``,
  the turn is proved and the ships lost are the fewest found);
- ``likely``: the best line found, exact only for plans of at most ``exact_up_to``
  launch orders (``par_best``'s verdict, kept as ``rests_on``).

The game asks ``/api/daily`` at a puzzle win whether the result matches or beats the
par, and congratulates only a player who does; the par itself is never served to
anyone who has not (``sc_puzzle_par``; ``docs/design/daily.md``, "The puzzle's par").

A stored par is only ever improved: replaced by a lower score, or by the same score
once it is proved (``better``), so a rerun on a slower machine cannot make it worse.
Every ``par_best`` stage is recorded as it finishes under
``results/par/puzzle/<challenge key>/``, and a rerun resumes from it. A par is
stored the moment its day is solved.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...
    uv run python tools/puzzle_par.py                        # today's and tomorrow's, if they have no par
    uv run python tools/puzzle_par.py --last 14              # ...and the 14 days before today
    uv run python tools/puzzle_par.py --days 2026-11-04 --refresh   # search again; store only an improvement
    uv run python tools/puzzle_par.py --daily tools/par_inputs/2026-11-04_puzzle.json   # offline, stores nothing
    uv run python tools/puzzle_par.py -- --k-budget 1200 --workers 4                    # par_best's own options

Options after ``--`` go to ``par_best`` (budgets, workers, ``--max-k``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay
from starconquest.settings import Settings
from tools import par_best
from tools.bot_replay import MISSING_CREDENTIALS, Supabase, credentials

TABLE = "puzzle_pars"
RESULTS = ROOT / "results" / "par" / "puzzle"
PROVED, LIKELY = "proved", "likely"
DAYS_AHEAD = 1


def status_of(summary: dict) -> str:
    """``proved`` when a full search finished, else ``likely``."""
    return PROVED if summary.get("proved") else LIKELY


def par_row(day: str, summary: dict, line: dict | None) -> dict | None:
    """The ``puzzle_pars`` row for a solved day, or None when there is nothing to
    store: no win found, or a best line that does not replay."""
    if summary["turn"] == math.inf or not summary.get("replays", False) or line is None:
        return None
    status = status_of(summary)
    return {"day": day, "turns": int(summary["turn"]), "lost": int(summary["lost"]), "status": status,
            "turns_only": bool(summary.get("turn_only")) if status == PROVED else False,
            "exact_up_to": int(summary.get("exact_up_to") or 0), "floor": int(summary["floor"]),
            "rests_on": summary["verdict"]["headline"], "line": line,
            "rules_version": engine.RULES_VERSION}


def _score(row: dict) -> tuple[int, int]:
    return row["turns"], row["lost"]


def better(new: dict, old: dict | None) -> dict | None:
    """What to store given a stored par ``old``: ``new`` (or ``new`` keeping what
    ``old`` proved) when it improves on it, else None. A par from older rules is
    always replaced. Lower turns, then fewer lost, then a proof, then a larger
    ``exact_up_to`` count as an improvement; nothing else does."""
    if old is None or old.get("rules_version") != new["rules_version"]:
        return new
    if _score(new) < _score(old):
        if old["status"] == PROVED and new["turns"] == old["turns"] and new["status"] != PROVED:
            # The same turn with fewer lost: the turn was proved, the ships were not.
            return {**new, "status": PROVED, "turns_only": True, "rests_on": old["rests_on"]}
        return new
    if _score(new) > _score(old):
        return None
    if new["status"] == PROVED and old["status"] != PROVED:
        return new
    if new["status"] == old["status"] == PROVED and old.get("turns_only") and not new["turns_only"]:
        return new
    if new["status"] == old["status"] == LIKELY and new["exact_up_to"] > old.get("exact_up_to", 0):
        return new
    return None


def work_dir(base: Path, settings: dict) -> Path:
    """The setup's results folder: by challenge key, so a map dealt locally under the
    same day (``par_best --days``) never shares its ledger. Inside it ``par_best``
    names everything ``<day>_puzzle``, as its passes expect."""
    return base / Settings.from_dict(settings).challenge_key()


def solve(day: str, settings: dict, args: argparse.Namespace) -> dict | None:
    """Run ``par_best`` on the stored setup (resuming) and answer its row, or None."""
    name = f"{day}_puzzle"
    args = argparse.Namespace(**{**vars(args), "results_dir": work_dir(args.results_dir, settings)})
    summary = par_best.solve(name, {"day": day, "kind": "puzzle", "settings": settings}, args)
    best = args.results_dir / f"{name}.best.json"
    line = replay.load(best).to_dict() if best.exists() else None
    return par_row(day, summary, line)


def show(row: dict) -> str:
    extra = (" (turn proved, ships fewest found)" if row["turns_only"] else "") if row["status"] == PROVED \
        else f" (exact up to K = {row['exact_up_to']})"
    return f"{row['day']}: par {row['turns']}/{row['lost']}, {row['status']}{extra}"


def days_wanted(args: argparse.Namespace, today: dt.date) -> list[dt.date]:
    if args.days:
        return [dt.date.fromisoformat(d) for d in args.days]
    ahead = [today + dt.timedelta(days=i) for i in range(DAYS_AHEAD + 1)]
    return [today - dt.timedelta(days=i) for i in reversed(range(1, args.last + 1))] + ahead


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, argparse.Namespace]:
    """This tool's options, and ``par_best``'s (those after ``--``)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, rest = argv[:cut], argv[cut + 1:]
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--days", nargs="+", help="these days (YYYY-MM-DD); default today and tomorrow")
    ap.add_argument("--last", type=int, default=0, help="also the N days before today")
    ap.add_argument("--refresh", action="store_true",
                    help="search days that already have a par too; store only an improvement")
    ap.add_argument("--daily", nargs="+", type=Path,
                    help="files from tools/daily.py --dry-run: solve and print, store nothing")
    args = ap.parse_args(argv)
    best = par_best.parser().parse_args(rest)
    if "--results-dir" not in rest:
        best.results_dir = RESULTS
    return args, best


def main(argv: list[str] | None = None) -> int:
    args, best_args = parse_args(argv)
    ai.load_models()
    ai.set_budget_scale(math.inf)
    if args.daily:
        for path in args.daily:
            data = json.loads(path.read_text(encoding="utf-8"))
            row = solve(data["day"], data["settings"], best_args)
            print(show(row) if row else f"{data['day']}: no par (no win found, or the line does not replay)")
        return 0
    url, key = credentials()
    if not url or not key:
        print(MISSING_CREDENTIALS, file=sys.stderr)
        return 2
    api = Supabase(url, key)
    failed = False
    for day in days_wanted(args, dt.datetime.now(dt.UTC).date()):
        iso = day.isoformat()
        # The secret key reads past the policy, so tomorrow's puzzle is solved before it starts.
        rows = api.select("dailies", f"select=day,settings,rules_version&day=eq.{iso}&kind=eq.puzzle&order=day")
        if not rows:
            print(f"{iso}: no puzzle stored — nothing to do")
            continue
        daily_row = rows[0]
        if daily_row["rules_version"] != engine.RULES_VERSION:
            print(f"{iso}: stored under rules {daily_row['rules_version']}, these are "
                  f"{engine.RULES_VERSION} — skipped")
            continue
        stored = api.select(TABLE, f"select=day,turns,lost,status,turns_only,exact_up_to,rests_on,rules_version"
                                   f"&day=eq.{iso}&order=day")
        old = stored[0] if stored else None
        if old is not None and old["rules_version"] == engine.RULES_VERSION and not args.refresh:
            print(f"{iso}: par {old['turns']}/{old['lost']} ({old['status']}) already stored — nothing to do")
            continue
        print(f"{iso}: searching", flush=True)
        row = solve(iso, daily_row["settings"], best_args)
        if row is None:
            print(f"{iso}: no par found (no win, or the line does not replay)", file=sys.stderr)
            failed = True
            continue
        keep = better(row, old)
        if keep is None:
            print(f"{show(row)} — no better than the stored {old['turns']}/{old['lost']} ({old['status']}), kept that")
            continue
        api.upsert(TABLE, [keep])
        print(f"{show(keep)} — stored", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
