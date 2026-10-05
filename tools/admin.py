#!/usr/bin/env python3
"""Moderate the leaderboard and play-by-post matches by hand.

The board is append-only to the public, and nothing on the site can remove a
row. This is the owner's way to: a score, a map, a player name or a tag that
should not be there, and a play-by-post seat that needs a new holder.

    export SUPABASE_URL=https://<project>.supabase.co
    export SUPABASE_SECRET_KEY=sb_secret_...

    uv run python tools/admin.py scores --user NAME           # find things
    uv run python tools/admin.py tags
    uv run python tools/admin.py matches

    uv run python tools/admin.py delete-score 123 --reason "fake"   # say what it would do
    uv run python tools/admin.py delete-score 123 --reason "fake" --yes   # ...and do it

Every command that writes is a dry run unless ``--yes`` is passed, and every one
that is applied first writes an ``admin_actions`` row: what was done, to what,
why, and the rows it is about to remove or overwrite. A deletion is therefore
undoable by hand from that row, and no moderation happens without a trace.

A claimed name (`users.owner`, see `leaderboard/schema.sql`) belongs to one
Google account, and only that account may post under it. A person claims an
*unused* name for themselves on the board's account page; a name that is
already in use is handed over here, since nothing on the site can prove who
posted under it:

    assign-name NAME EMAIL [--fold OLD...]
                             give NAME to the account signed in with EMAIL.
                             They must have signed in on the board once, so
                             the account exists. Refused if the account owns
                             another name, or someone else owns this one.
                             --fold moves each OLD name's scores and tags onto
                             NAME and deletes OLD, which is then free for
                             anyone: one person's several names become one.
    release-name NAME        make NAME an ordinary unclaimed name again.
    names [--claimed]        list names and who (by account id) owns them.

Three different things can be done to a play-by-post seat, and they are not
interchangeable:

    new-link   mint a fresh token for the seat and print its link here, once.
               The old link stops working. The seat stays claimed, so nobody
               else can take it from the lobby: this is how a link is handed
               to someone privately.
    open-seat  forget the seat's token so the seat is open again, and the next
               person to press *Get link* on the lobby page claims it. Only a
               public match has a lobby entry, so a private one is refused
               unless ``--publish`` lists it too.
    (neither)  the *same* link as before cannot be shown again. Only a hash of
               a token is stored, and a hash cannot be turned back into one.

The key is the project's secret key, the same one the scheduled worker uses;
nothing here is deployed or reachable from the site.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import pbp, replay
from tools.bot_replay import (
    MISSING_CREDENTIALS,
    ApiError,
    Supabase,
    credentials,
)

# `leaderboard/js/config.mjs`'s GAME_URL_FALLBACK: where a printed seat link opens.
GAME_URL = "https://star-conquest.netlify.app/game/"


class AdminApi(Supabase):
    """`Supabase`, plus the one Auth admin read assign-name needs."""

    def auth_user(self, email: str) -> dict | None:
        """The Supabase Auth account signed in with ``email``, or None.

        Auth's admin API has no filter by email, so this pages the whole list:
        fine for a board this size, and only ever run by hand."""
        wanted = email.strip().lower()
        page = 1
        while True:
            request = urllib.request.Request(
                f"{self.url.rstrip('/')}/auth/v1/admin/users?page={page}&per_page=1000")
            for name, value in self._headers().items():
                request.add_header(name, value)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    users = json.loads(response.read().decode("utf-8")).get("users") or []
            except (OSError, ValueError) as err:
                raise ApiError(f"GET auth/v1/admin/users failed: {err}") from err
            for user in users:
                if (user.get("email") or "").strip().lower() == wanted:
                    return user
            if len(users) < 1000:
                return None
            page += 1


class Refused(RuntimeError):
    """A command that cannot be carried out as asked; nothing has been written."""


def q(value: object) -> str:
    """A value made safe to put in a PostgREST filter."""
    return urllib.parse.quote(str(value), safe="")


def name_key(name: str) -> str:
    """`users.name_key`, as the database generates it."""
    return name.strip().lower()


def tag_key(tag: str) -> str:
    """`config_tags.tag_key`, as the database generates it."""
    return tag.strip().lower()


def mint_token() -> str:
    """A seat token in the shape `pbp.mjs`'s `mintToken` produces."""
    return secrets.token_hex(16)


def hash_token(token: str) -> str:
    """`pbp.mjs`'s `hashToken`: what is stored in place of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def seat_link(match_id: str, token: str, game_url: str = GAME_URL) -> str:
    """The link a seat's token opens in the game, as the lobby page builds it."""
    return f"{game_url}#{pbp.link_fragment(match_id, token)}"


# --------------------------------------------------------------------------- #
# Seats
# --------------------------------------------------------------------------- #
def _seats(match: dict) -> tuple[list[int], dict[str, str]]:
    """``(roster, tokens)`` out of a `pbp_matches.seats` value."""
    seats = match.get("seats")
    if not isinstance(seats, dict) or not isinstance(seats.get("seats"), list):
        raise Refused(f"match {match.get('match_id')} has no seat roster")
    return list(seats["seats"]), dict(seats.get("tokens") or {})


def open_seats(match: dict) -> list[int]:
    """`pbp.mjs`'s `unclaimedSeats`: roster seats nobody holds a token for."""
    roster, tokens = _seats(match)
    return [seat for seat in roster if str(seat) not in tokens]


def reissued(match: dict, seat: int, token_hash: str) -> dict:
    """The `seats` value with ``seat`` held by a new token."""
    roster, tokens = _seats(match)
    if seat not in roster:
        raise Refused(f"seat {seat} is not a person's seat in this match (seats {roster})")
    return {"seats": roster, "tokens": {**tokens, str(seat): token_hash}}


def reopened(match: dict, seat: int, *, publish: bool = False) -> dict:
    """The `seats` value with ``seat`` open for the lobby to hand out again."""
    roster, tokens = _seats(match)
    if seat not in roster:
        raise Refused(f"seat {seat} is not a person's seat in this match (seats {roster})")
    if match.get("finished"):
        raise Refused("the match is over; there is nothing left to join")
    if str(seat) not in tokens:
        raise Refused(f"seat {seat} is already open")
    if not match.get("public") and not publish:
        raise Refused("a private match has no lobby entry, so an open seat could never be "
                      "claimed; pass --publish to list the match as well, or use new-link")
    return {"seats": roster, "tokens": {k: v for k, v in tokens.items() if k != str(seat)}}


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    """What a command is about to do, decided before anything is written.

    ``detail`` is stored on the audit row: the rows being removed or overwritten,
    so the action can be undone by hand. ``steps`` run in order; one that returns
    a string has it printed, which is how a minted link reaches the terminal.
    """

    action: str
    target: str
    summary: list[str]
    detail: dict = field(default_factory=dict)
    steps: list[Callable[[], str | None]] = field(default_factory=list)


def apply(api: Supabase, plan: Plan, *, yes: bool, reason: str) -> int:
    print(f"{plan.action} {plan.target}")
    for line in plan.summary:
        print(f"  {line}")
    if not plan.steps:
        print("nothing to do")
        return 0
    if not yes:
        print("dry run — pass --yes to apply")
        return 0
    # Recorded before it is taken, so a failure part way still leaves a trace of
    # what was attempted.
    api.insert("admin_actions", [{
        "action": plan.action, "target": plan.target, "reason": reason,
        "detail": plan.detail,
    }])
    for step in plan.steps:
        out = step()
        if out:
            print(out)
    print("done")
    return 0


def _one(rows: list[dict], what: str) -> dict:
    if not rows:
        raise Refused(f"no such {what}")
    return rows[0]


def _match(api: Supabase, match_id: str) -> dict:
    if not replay._MATCH_ID_RE.match(match_id):
        raise Refused(f"{match_id!r} is not a match id")
    return _one(api.select(
        "pbp_matches",
        f"select=match_id,seats,public,finished,turn,updated_at&match_id=eq.{match_id}"
        "&order=match_id.asc"), "match")


def _write_seats(api: Supabase, match: dict, patch: dict) -> None:
    """Rewrite a match's `seats`, conditional on nothing having moved since it
    was read — the same guard `handleClaim` uses, so a claim or a resolve landing
    in between is not overwritten."""
    written = api.update(
        "pbp_matches",
        f"match_id=eq.{match['match_id']}&updated_at=eq.{q(match['updated_at'])}",
        {**patch, "updated_at": datetime.now(UTC).isoformat()})
    if not written:
        raise Refused("the match moved while this ran; nothing was changed, run it again")


def plan_new_link(api: Supabase, match_id: str, seat: int, game_url: str) -> Plan:
    match = _match(api, match_id)
    token = mint_token()
    seats = reissued(match, seat, hash_token(token))
    held = str(seat) in _seats(match)[1]
    return Plan(
        "new-link", f"{match_id} seat {seat}",
        [f"mint a new token for seat {seat}"
         + (" — the link it has now stops working" if held else " — the seat is open, and this claims it"),
         "the new link is printed once and stored nowhere"],
        {"seats": match["seats"]},
        [lambda: _write_seats(api, match, {"seats": seats}),
         lambda: f"seat {seat}: {seat_link(match_id, token, game_url)}"])


def plan_open_seat(api: Supabase, match_id: str, seat: int, publish: bool) -> Plan:
    match = _match(api, match_id)
    seats = reopened(match, seat, publish=publish)
    patch: dict = {"seats": seats}
    summary = [f"forget seat {seat}'s token — its link stops working, and the seat "
               "is claimable from the lobby"]
    if publish and not match.get("public"):
        patch["public"] = True
        summary.append("list the match publicly on the lobby page")
    return Plan("open-seat", f"{match_id} seat {seat}", summary,
                {"seats": match["seats"], "public": match.get("public")},
                [lambda: _write_seats(api, match, patch)])


def plan_delete_match(api: Supabase, match_id: str) -> Plan:
    match = _match(api, match_id)
    orders = api.select("pbp_orders", f"select=*&match_id=eq.{match_id}&order=id.asc")
    whole = _one(api.select("pbp_matches", f"select=*&match_id=eq.{match_id}"
                                           "&order=match_id.asc"), "match")
    return Plan(
        "delete-match", match_id,
        [f"delete the match (turn {match['turn']}) and its {len(orders)} order row(s)"],
        {"match": whole, "orders": orders},
        [lambda: api.delete("pbp_orders", f"match_id=eq.{match_id}"),
         lambda: api.delete("pbp_matches", f"match_id=eq.{match_id}")])


def plan_delete_score(api: Supabase, score_ids: list[int]) -> Plan:
    ids = ",".join(str(int(i)) for i in score_ids)
    scores = api.select("scores", f"select=*&id=in.({ids})&order=id.asc")
    found = {int(s["id"]) for s in scores}
    missing = [i for i in score_ids if int(i) not in found]
    if missing:
        raise Refused(f"no such score: {', '.join(map(str, missing))}")
    tags = api.select("config_tags", f"select=*&score_id=in.({ids})&order=id.asc")
    checks = api.select("score_checks", f"select=*&score_id=in.({ids})&order=score_id.asc")
    return Plan(
        "delete-score", ids,
        [f"score {s['id']}: {s['turns']} turns on {s['game_key']}" for s in scores]
        + [f"...and the {len(tags)} tag(s) posted with them"] * bool(tags)
        + ["their replays stop being public; the uploads themselves are kept"],
        {"scores": scores, "config_tags": tags, "score_checks": checks},
        # config_tags and score_checks cascade from scores.
        [lambda: api.delete("scores", f"id=in.({ids})")])


def plan_delete_game(api: Supabase, game_key: str) -> Plan:
    key = q(game_key)
    game = _one(api.select("games", f"select=*&game_key=eq.{key}&order=game_key.asc"), "game")
    scores = api.select("scores", f"select=*&game_key=eq.{key}&order=id.asc")
    ids = ",".join(str(int(s["id"])) for s in scores)
    tags = api.select("config_tags", f"select=*&score_id=in.({ids})&order=id.asc") if ids else []
    bots = api.select("bot_scores", f"select=game_key,bot,won,turns,lost&game_key=eq.{key}"
                                    "&order=bot.asc")
    logs = api.select("game_logs", f"select=id,match_id,turns&game_key=eq.{key}&order=id.asc")
    return Plan(
        "delete-game", game_key,
        [f"delete the map, its {len(scores)} score(s), {len(tags)} tag(s), "
         f"{len(bots)} bot result(s) and {len(logs)} uploaded replay(s)"],
        # The replays themselves are left off the audit row: they are the bulk of
        # it, and a deleted map's moves are not what anyone would restore.
        {"game": game, "scores": scores, "config_tags": tags, "bot_scores": bots,
         "game_logs": logs},
        [lambda: api.delete("scores", f"game_key=eq.{key}") if scores else None,
         lambda: api.delete("bot_scores", f"game_key=eq.{key}") if bots else None,
         lambda: api.delete("game_logs", f"game_key=eq.{key}") if logs else None,
         lambda: api.delete("games", f"game_key=eq.{key}")])


def plan_rename_user(api: Supabase, old: str, new: str) -> Plan:
    new = new.strip()
    if not 1 <= len(new) <= 60:
        raise Refused("a name is 1-60 characters")
    user = _one(api.select("users", f"select=*&name_key=eq.{q(name_key(old))}&order=id.asc"),
                "user")
    if name_key(new) != user["name_key"] and api.select(
            "users", f"select=id&name_key=eq.{q(name_key(new))}&order=id.asc"):
        raise Refused(f"{new!r} is already somebody else's name")
    return Plan(
        "rename-user", f"{user['name']} ({user['id']})",
        [f"rename {user['name']!r} to {new!r} on every score they have posted"],
        {"user": user},
        [lambda: api.update("users", f"id=eq.{int(user['id'])}", {"name": new}) and None])


def _set_owner(api: Supabase, user_id: int, was: str | None, owner: str | None) -> None:
    """Rewrite a name's owner, conditional on it still being ``was`` — so a
    claim or release landing in between is not overwritten."""
    held = "owner=is.null" if was is None else f"owner=eq.{q(was)}"
    if not api.update("users", f"id=eq.{int(user_id)}&{held}", {"owner": owner}):
        raise Refused("the name changed hands while this ran; nothing was changed, run it again")


def plan_assign_name(api: AdminApi, name: str, email: str,
                     fold: list[str] | tuple[str, ...] = ()) -> Plan:
    """Give ``name`` to the account that signed in as ``email``, folding each of
    ``fold`` into it first: their scores and tags move onto ``name``, and their
    rows are deleted, so those names are free for anyone again."""
    name = name.strip()
    if not 1 <= len(name) <= 60:
        raise Refused("a name is 1-60 characters")
    account = api.auth_user(email)
    if not account:
        raise Refused(f"no account has signed in as {email!r}; they must sign in on the "
                      "board's account page once first")
    uid = str(account["id"])
    rows = api.select("users", f"select=*&name_key=eq.{q(name_key(name))}&order=id.asc")
    user = rows[0] if rows else None
    if user is not None and user.get("owner") and user["owner"] != uid:
        raise Refused(f"{user['name']!r} is claimed by another account; release-name it first")

    folded: list[dict] = []
    for old in dict.fromkeys(name_key(f) for f in fold):
        if old == name_key(name):
            raise Refused(f"{old!r} is the name being kept; it cannot be folded into itself")
        row = _one(api.select("users", f"select=*&name_key=eq.{q(old)}&order=id.asc"),
                   f"user {old!r}")
        if row.get("owner") and row["owner"] != uid:
            raise Refused(f"{row['name']!r} is claimed by another account; release-name it first")
        folded.append(row)
    folded_ids = {int(r["id"]) for r in folded}

    owned = api.select("users", f"select=id,name&owner=eq.{q(uid)}&order=id.asc")
    for row in owned:
        if (user is None or int(row["id"]) != int(user["id"])) and int(row["id"]) not in folded_ids:
            raise Refused(f"that account already owns {row['name']!r}; release-name it, or "
                          "--fold it into this one (one name per account)")

    summary: list[str] = []
    steps: list[Callable[[], str | None]] = []
    moved: dict[str, dict] = {}
    for row in folded:
        rid = int(row["id"])
        scores = api.select("scores", f"select=id&user_id=eq.{rid}&order=id.asc")
        tags = api.select("config_tags", f"select=id&user_id=eq.{rid}&order=id.asc")
        moved[str(rid)] = {"scores": [s["id"] for s in scores],
                           "config_tags": [t["id"] for t in tags]}
        summary.append(f"fold {row['name']!r} ({len(scores)} score(s), {len(tags)} tag(s)) "
                       f"into {name if user is None else user['name']!r}; "
                       f"{row['name']!r} is then free for anyone to use")
        if row.get("owner") == uid:
            # The account's old name lets go first: one name per account is a
            # unique index, and the kept name is about to take its owner.
            steps.append(lambda r=row: _set_owner(api, r["id"], uid, None))

    def kept_id() -> int:
        """The kept row's id, read when the steps run (it may be newly made)."""
        return int(_one(api.select("users", f"select=id&name_key=eq.{q(name_key(name))}"
                                            "&order=id.asc"), f"user {name!r}")["id"])

    if user is None:
        summary.insert(0, f"create the name {name!r}, owned by that account "
                          "(nobody has used it yet)")
        # Made (and owned) before the folds, so they have a row to move onto.
        steps.append(lambda: api.insert("users", [{"name": name, "owner": uid}]))
    elif user.get("owner") == uid:
        if not folded:
            return Plan("assign-name", f"{user['name']} ({user['id']})",
                        [f"{user['name']!r} already belongs to that account"])
    else:
        scores = api.select("scores", f"select=id&user_id=eq.{int(user['id'])}&order=id.asc")
        summary.insert(0, f"give {user['name']!r} and its {len(scores)} score(s) to that "
                          "account; from now on only they can post under it")

    if folded:
        ids = ",".join(str(i) for i in sorted(folded_ids))
        steps += [
            lambda: api.update("scores", f"user_id=in.({ids})", {"user_id": kept_id()}) and None,
            lambda: api.update("config_tags", f"user_id=in.({ids})",
                               {"user_id": kept_id()}) and None,
            lambda: api.delete("users", f"id=in.({ids})"),
        ]
    if user is not None and user.get("owner") != uid:
        # Last, so a fold that fails part way never leaves the name claimed with
        # half its old scores still elsewhere.
        steps.append(lambda: _set_owner(api, user["id"], None, uid))

    # The audit row names the account by id, never by email, and keeps every
    # folded row and what moved off it, so a fold can be undone by hand.
    return Plan("assign-name", name if user is None else f"{user['name']} ({user['id']})",
                summary, {"user": user, "owner": uid, "folded": folded, "moved": moved},
                steps)


def plan_release_name(api: Supabase, name: str) -> Plan:
    user = _one(api.select("users", f"select=*&name_key=eq.{q(name_key(name))}&order=id.asc"),
                "user")
    if not user.get("owner"):
        raise Refused(f"{user['name']!r} is not claimed")
    return Plan(
        "release-name", f"{user['name']} ({user['id']})",
        [f"release {user['name']!r}: its scores stay, and anyone may post under it again"],
        {"user": user},
        [lambda: _set_owner(api, user["id"], user["owner"], None)])


def plan_delete_tag(api: Supabase, tag: str, config_key: str | None) -> Plan:
    key = tag_key(tag)
    where = f"&config_key=eq.{q(config_key)}" if config_key else ""
    rows = api.select("config_tags", f"select=*&tag_key=eq.{q(key)}{where}&order=id.asc")
    # The tags a config was given when it was first named, before tags had a table.
    legacy = api.select("configs", f"select=config_key,tags&tags=cs.{q('{' + json.dumps(key) + '}')}"
                                   f"{where}&order=config_key.asc")
    steps: list[Callable[[], str | None]] = []
    if rows:
        steps.append(lambda: api.delete("config_tags", f"tag_key=eq.{q(key)}{where}"))
    for config in legacy:
        kept = [t for t in config["tags"] if t != key]
        steps.append(lambda c=config, k=kept: api.update(
            "configs", f"config_key=eq.{q(c['config_key'])}", {"tags": k}) and None)
    configs = sorted({r["config_key"] for r in rows} | {c["config_key"] for c in legacy})
    return Plan(
        "delete-tag", key + (f" on {config_key}" if config_key else ""),
        [f"remove {len(rows)} use(s) of {key!r} and {len(legacy)} original tag(s), "
         f"across {len(configs)} config(s)"],
        {"config_tags": rows, "configs": legacy},
        steps)


def plan_delete_config_name(api: Supabase, config_key: str) -> Plan:
    config = _one(api.select("configs", f"select=*&config_key=eq.{q(config_key)}"
                                        "&order=config_key.asc"), "named config")
    return Plan(
        "delete-config-name", config_key,
        [f"remove the name {config['name']!r} (and its original tags {config['tags']}); "
         "the next name posted for this config is the one that sticks"],
        {"config": config},
        [lambda: api.delete("configs", f"config_key=eq.{q(config_key)}")])


# --------------------------------------------------------------------------- #
# Looking things up
# --------------------------------------------------------------------------- #
def show_scores(api: Supabase, game_key: str | None, user: str | None, limit: int) -> None:
    where = f"&game_key=eq.{q(game_key)}" if game_key else ""
    if user:
        found = api.select("users", f"select=id&name_key=eq.{q(name_key(user))}&order=id.asc")
        if not found:
            raise Refused(f"no such user {user!r}")
        where += f"&user_id=eq.{int(found[0]['id'])}"
    scores = api.select("scores", "select=id,game_key,turns,lost,hand,by_name,submitted_at,"
                                  f"users(name)&order=id.desc{where}")[:limit]
    for s in scores:
        name = (s.get("users") or {}).get("name", "")
        by = f"  by {s['by_name']!r}" if s.get("by_name") else ""
        print(f"{s['id']:>6}  {s['game_key']:<18} {s['turns']:>4}t {s['lost']:>4} lost  "
              f"{name!r}{by}  {s['submitted_at'][:16]}")


def show_tags(api: Supabase, config_key: str | None) -> None:
    where = f"&config_key=eq.{q(config_key)}" if config_key else ""
    rows = api.select("config_tags", f"select=tag_key,config_key&order=id.asc{where}")
    uses: dict[str, set[str]] = {}
    for row in rows:
        uses.setdefault(row["tag_key"], set()).add(row["config_key"])
    for config in api.select("configs", f"select=config_key,tags&order=config_key.asc{where}"):
        for tag in config.get("tags") or []:
            uses.setdefault(tag, set()).add(config["config_key"])
    for tag in sorted(uses):
        print(f"{tag:<26} on {len(uses[tag])} config(s)")


def show_names(api: Supabase, claimed_only: bool) -> None:
    where = "&owner=not.is.null" if claimed_only else ""
    for u in api.select("users", f"select=id,name,owner&order=name_key.asc{where}"):
        owner = f"  owned by {u['owner']}" if u.get("owner") else ""
        print(f"{u['id']:>6}  {u['name']!r}{owner}")


def show_matches(api: Supabase) -> None:
    for m in api.select("pbp_matches", "select=match_id,seats,public,finished,turn,"
                                       "updated_at&order=updated_at.desc"):
        roster, _ = _seats(m)
        state = "finished" if m["finished"] else f"turn {m['turn']}"
        vis = "public" if m["public"] else "private"
        free = open_seats(m)
        opened = f", open {free}" if free and not m["finished"] else ""
        print(f"{m['match_id']}  {vis:<7} {state:<9} seats {roster}{opened}  "
              f"moved {m['updated_at'][:16]}")


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def writes(name: str, help_: str) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_)
        cmd.add_argument("--yes", action="store_true", help="apply it, not just describe it")
        cmd.add_argument("--reason", default="", help="kept on the audit row")
        return cmd

    cmd = sub.add_parser("scores", help="list scores, newest first")
    cmd.add_argument("--game", help="only this game_key")
    cmd.add_argument("--user", help="only this player name")
    cmd.add_argument("--limit", type=int, default=50)
    cmd = sub.add_parser("tags", help="every tag in use, and how widely")
    cmd.add_argument("--config", help="only this config_key")
    sub.add_parser("matches", help="every play-by-post match, private ones included")
    cmd = sub.add_parser("names", help="every player name, and which are claimed")
    cmd.add_argument("--claimed", action="store_true", help="only claimed names")

    cmd = writes("delete-score", "delete scores and the tags posted with them")
    cmd.add_argument("ids", type=int, nargs="+")
    cmd = writes("delete-game", "delete a map and everything posted on it")
    cmd.add_argument("game_key")
    cmd = writes("rename-user", "change a player's name everywhere it shows")
    cmd.add_argument("name")
    cmd.add_argument("new_name")
    cmd = writes("assign-name", "give a name, used or not, to a signed-in account")
    cmd.add_argument("name")
    cmd.add_argument("email", help="the Google account's email, as it signed in")
    cmd.add_argument("--fold", nargs="+", action="extend", default=[], metavar="OLD",
                     help="other names of theirs to merge into NAME (then deleted)")
    cmd = writes("release-name", "make a claimed name anyone's to use again")
    cmd.add_argument("name")
    cmd = writes("delete-tag", "remove a tag from every config, or one")
    cmd.add_argument("tag")
    cmd.add_argument("--config", help="only from this config_key")
    cmd = writes("delete-config-name", "remove a config's name so it can be named again")
    cmd.add_argument("config_key")

    cmd = writes("new-link", "mint a seat a new link and print it here")
    cmd.add_argument("match_id")
    cmd.add_argument("seat", type=int)
    cmd.add_argument("--game-url", default=GAME_URL, help=f"the game build (default {GAME_URL})")
    cmd = writes("open-seat", "make a seat claimable from the lobby again")
    cmd.add_argument("match_id")
    cmd.add_argument("seat", type=int)
    cmd.add_argument("--publish", action="store_true", help="list a private match publicly too")
    cmd = writes("delete-match", "delete a play-by-post match and its orders")
    cmd.add_argument("match_id")
    return parser.parse_args(argv)


def plan_for(api: AdminApi, args: argparse.Namespace) -> Plan:
    match args.command:
        case "delete-score":
            return plan_delete_score(api, args.ids)
        case "delete-game":
            return plan_delete_game(api, args.game_key)
        case "rename-user":
            return plan_rename_user(api, args.name, args.new_name)
        case "assign-name":
            return plan_assign_name(api, args.name, args.email, args.fold)
        case "release-name":
            return plan_release_name(api, args.name)
        case "delete-tag":
            return plan_delete_tag(api, args.tag, args.config)
        case "delete-config-name":
            return plan_delete_config_name(api, args.config_key)
        case "new-link":
            return plan_new_link(api, args.match_id, args.seat, args.game_url)
        case "open-seat":
            return plan_open_seat(api, args.match_id, args.seat, args.publish)
        case "delete-match":
            return plan_delete_match(api, args.match_id)
    raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url, key = credentials()
    if not url or not key:
        print(MISSING_CREDENTIALS, file=sys.stderr)
        return 2
    api = AdminApi(url, key)
    try:
        if args.command == "scores":
            show_scores(api, args.game, args.user, args.limit)
        elif args.command == "tags":
            show_tags(api, args.config)
        elif args.command == "matches":
            show_matches(api)
        elif args.command == "names":
            show_names(api, args.claimed)
        else:
            return apply(api, plan_for(api, args), yes=args.yes, reason=args.reason)
    except Refused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    except ApiError as err:
        print(f"store refused: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
