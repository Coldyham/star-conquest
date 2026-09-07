#!/usr/bin/env python3
"""Replay the log behind each posted score and record whether it checks out.

A score in a challenge link is unsigned: the token format is public, so a
hand-crafted impossible result is accepted by the board exactly as a real one is
(``leaderboard/README.md``, "Known limitations"). A *replay* is a different kind
of claim — it is the match's inputs, and feeding them back through the engine
either reproduces the posted turns, ships lost and by-hand count or it does not.
This is the offline job that does that feeding back.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SERVICE_KEY=<service_role key>       # never the anon key
    uv run python tools/verify_scores.py                 # check what's unchecked
    uv run python tools/verify_scores.py --recheck       # check everything again
    uv run python tools/verify_scores.py --stale         # only rows from older code
    uv run python tools/verify_scores.py --prune         # drop superseded uploads
    uv run python tools/verify_scores.py --dry-run       # decide, post nothing

The sibling of ``bot_replay.py`` and deliberately built out of it: the same
Supabase client, the same "a pure function of the inputs, so compute it once and
cache it" shape. It keys on ``replay_rev`` rather than ``engine_rev``, because a
replay never consults a bot and so a retuned one cannot change a verdict. The service_role key is what
makes it possible at all — ``game_logs`` grants the public insert and *no* select,
so the uploaded replays are readable here and nowhere else.

Five verdicts, stored in ``score_checks``:

    verified    the log replays and reproduces the score exactly
    mismatch    it replays and produces something else, or is for another setup
    outdated    it does not reproduce, and it was played under older *rules*
    unreadable  the blob does not decode, or cannot be replayed
    missing     nothing was ever uploaded for this score's match_id

``outdated`` is the one that keeps this honest across a rules change. A stored log
never consults a bot (see ``replay_rev``), but a change to the engine itself — the
phase order, how a fight resolves, how a map is drawn from a seed — really can
make an old game replay to a different board. ``engine.RULES_VERSION`` is stamped
on every log when it is played, and a score that fails to reproduce having been
played under an older stamp is reported as unverifiable rather than as wrong. The
check is made *after* the replay, not before, so the many old games a rules change
does not actually disturb still verify normally.

``missing`` is a fact, not an absence: a score posted before the game uploaded
logs, or by someone whose upload was blocked, is unverified rather than suspect,
and the board should be able to say which. Nothing here ever deletes a score —
a bad row is a decision for a person with the SQL editor.

Nothing imports pygame or anything off PyPI, so a bare runner can do the whole
job (same as ``bot_replay``).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import engine, replay  # noqa: E402
from starconquest.settings import Settings  # noqa: E402
from tools.bot_replay import Supabase, replay_rev  # noqa: E402 — the shared client

VERDICTS = ("verified", "mismatch", "outdated", "unreadable", "missing")


@dataclass
class Check:
    """One score's verdict, ready to store."""

    score_id: int
    verdict: str
    detail: str = ""

    def row(self, rev: str) -> dict:
        return {
            "score_id": self.score_id,
            "verdict": self.verdict,
            "detail": self.detail[:500],
            "engine_rev": rev,
        }


def pending(scores: list[dict], done: dict[int, dict], rev: str, *,
            recheck: bool = False, stale: bool = False) -> list[dict]:
    """The scores still owing a verdict, in the order given.

    A stored verdict is normally final: the log, the score and the engine are all
    fixed, so re-deriving it would spend a runner's minutes to reach the same
    answer. The two exceptions are opt-in. ``--stale`` re-checks rows decided by
    an older simulation, which is the one thing that can legitimately change a
    verdict; ``--recheck`` does the lot.

    ``outdated`` is re-decided on the same footing as any other stored verdict:
    the rules only ever move forward, so a row set aside by one bump stays set
    aside, and ``--stale`` is what looks again.

    ``missing`` is the exception to the exception: it is a statement about what
    had been *uploaded* by the time we looked, and that changes on its own. So a
    missing row is always retried, with no flag — an upload that arrived late, or
    was retried by the player, is exactly the case worth picking up.
    """
    out = []
    for score in scores:
        previous = done.get(score["id"])
        if previous is not None and not recheck:
            outdated = stale and previous.get("engine_rev", "") != rev
            if not outdated and previous.get("verdict") != "missing":
                continue
        out.append(score)
    return out


def best_logs(rows: list[dict]) -> dict[str, dict]:
    """The longest uploaded log per ``match_id``.

    Uploads are append-only (``schema.sql``), so a match submitted, played on and
    submitted again has more than one row. The longest is the current one; the
    others are its own history, and ``--prune`` is what clears them.
    """
    best: dict[str, dict] = {}
    for row in rows:
        match = str(row.get("match_id", ""))
        if not match:
            continue
        if match not in best or int(row.get("turns", 0)) > int(best[match].get("turns", 0)):
            best[match] = row
    return best


def same_setup(log: replay.GameLog, settings_json: dict | None) -> bool:
    """Whether ``log`` was played on the setup its score is filed under.

    Without this a log of an easy map could be attached to a hard map's score and
    verify perfectly. Both sides are hashed here, in Python, by the same code —
    so a key the *site* folded (``KEY_ALIASES``) or computed itself for a
    hand-written link never has to be reproduced; the two setups are compared
    directly, and ``challenge_keys`` covers a schema change on either side.
    """
    if not isinstance(settings_json, dict):
        return False
    try:
        posted = Settings.from_dict(settings_json)
        played = Settings.from_dict(log.settings)
    except Exception:  # noqa: BLE001 — an unreadable setup is not the same setup
        return False
    seed = posted.seed if posted.seed is not None else log.seed
    if seed != log.seed:
        return False
    return bool(set(posted.challenge_keys()) & set(played.challenge_keys()))


def verify(score: dict, blob: str | None, settings_json: dict | None) -> Check:
    """Decide one score against its uploaded replay.

    The engine is never asked to *judge* anything: it replays the recorded orders
    and deals back the recorded dice (``replay.reconstruct``), and the position it
    lands on is simply read. A score matches when the same seat won on the same
    turn having lost the same ships, with the same number of turns played by hand.
    """
    sid = int(score["id"])
    if not blob:
        return Check(sid, "missing")
    try:
        log = replay.GameLog.decode(blob)
    except ValueError as err:
        return Check(sid, "unreadable", str(err))
    if not same_setup(log, settings_json):
        return Check(sid, "mismatch", "the replay is of a different setup")
    try:
        state, _ = replay.reconstruct(log)
    except Exception as err:  # noqa: BLE001 — a log that won't replay is a verdict
        return Check(sid, "unreadable", f"replay failed: {err}")

    human = state.human()
    if human is None:
        return Check(sid, "unreadable", "the replay has no human seat")
    if state.winner != human.id:
        return Check(sid, "mismatch", "the replay is not a win for the human seat")

    claimed = (int(score["turns"]), int(score["lost"]), int(score["hand"]))
    actual = (state.turn, human.ships_lost, log.hand_turns)
    if claimed != actual:
        detail = (f"posted {claimed[0]}/{claimed[1]}/{claimed[2]} turns/lost/hand, "
                  f"replay gives {actual[0]}/{actual[1]}/{actual[2]}")
        return Check(sid, *_disagreement(log, detail))
    return Check(sid, "verified")


def _disagreement(log: replay.GameLog, detail: str) -> tuple[str, str]:
    """Whether a replay that did not reproduce its score is a *wrong score* or an
    *unverifiable* one.

    Asked only once the replay has already disagreed, which is what keeps a rules
    change from wiping the board: a change to the engine disturbs some games and
    not others, and the ones it leaves alone still verify on their own merits.
    Only the ones it broke are set aside, and they are set aside rather than
    accused — the score may well have been honest under the rules it was played
    under, and nothing here can now tell.
    """
    if log.rules_version != engine.RULES_VERSION:
        return "outdated", (f"played under rules v{log.rules_version}, now on "
                            f"v{engine.RULES_VERSION} — {detail}")
    return "mismatch", detail


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recheck", action="store_true",
                        help="re-decide every score, not only the undecided ones")
    parser.add_argument("--stale", action="store_true",
                        help="also re-decide rows checked by a different engine_rev")
    parser.add_argument("--prune", action="store_true",
                        help="delete uploaded logs superseded by a longer one")
    parser.add_argument("--limit", type=int, default=0,
                        help="check at most this many scores (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and report, but write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (the service_role key).",
              file=sys.stderr)
        return 2

    api = Supabase(url, key)
    # `replay_rev`, not `engine_rev`: a verdict cannot depend on a bot, so tuning
    # one must not mark every score on the board stale.
    rev = replay_rev()
    print(f"replay_rev {rev}")

    scores = api.select("scores", "select=id,game_key,turns,lost,hand,match_id&order=id.asc")
    checked = {int(r["score_id"]): r for r in
               api.select("score_checks", "select=score_id,verdict,engine_rev&order=score_id.asc")}
    todo = pending(scores, checked, rev, recheck=args.recheck, stale=args.stale)
    if args.limit > 0:
        todo = todo[: args.limit]
    if not todo:
        print(f"{len(scores)} scores, all checked — nothing to do.")
        return 0

    logs = best_logs(api.select("game_logs", "select=id,match_id,turns,log&order=id.asc"))
    games = {r["game_key"]: r.get("settings_json") for r in
             api.select("games", "select=game_key,settings_json&order=game_key.asc")}

    print(f"{len(todo)} of {len(scores)} scores to check against {len(logs)} replays")
    results: list[Check] = []
    for score in todo:
        entry = logs.get(str(score.get("match_id", "")))
        check = verify(score, entry.get("log") if entry else None,
                       games.get(score["game_key"]))
        results.append(check)
        note = f" — {check.detail}" if check.detail else ""
        print(f"  score {check.score_id}: {check.verdict}{note}")

    tally = {v: sum(1 for c in results if c.verdict == v) for v in VERDICTS}
    print("  " + " · ".join(f"{v} {tally[v]}" for v in VERDICTS))

    if args.dry_run:
        print("dry run — nothing written")
        return 0
    api.upsert("score_checks", [c.row(rev) for c in results])

    if args.prune:
        keep = {int(entry["id"]) for entry in logs.values()}
        extra = [r for r in api.select("game_logs", "select=id,match_id&order=id.asc")
                 if int(r["id"]) not in keep]
        for row in extra:
            api.delete("game_logs", f"id=eq.{int(row['id'])}")
        print(f"pruned {len(extra)} superseded upload(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
