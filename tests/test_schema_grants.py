"""Every relation the worker touches is granted to it in schema.sql.

The trap this exists for has now caught that file twice, and it hides unusually
well. A secret key bypasses row-level security but *not* the GRANT system
underneath, so a relation the key was never granted fails the query outright —
and it fails it only for the worker, leaving the very same rows perfectly visible
to the publishable key on the site. `public_replays` was the example: the board
rendered a "Watch" link off it while the function serving that replay could not
read it at all.

Nothing here talks to a database. It reads the SQL and the callers as text and
checks they agree, which is the only place they *can* be checked without a live
project — `schema.sql` has no test harness of its own (see the leaderboard's
README) and a missing grant is not visible until something runs in anger.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "leaderboard" / "schema.sql"
FUNCTIONS = ROOT / "leaderboard" / "netlify" / "functions"

# What each caller verb needs to be granted.
_PYTHON_CALLS = {"select": {"select"}, "upsert": {"insert", "update"}, "delete": {"delete"}}


def _granted(role: str) -> dict[str, set[str]]:
    """``{relation: {privilege, …}}`` for ``role``, out of schema.sql.

    Deliberately a text scrape rather than anything cleverer: the file is the
    single source of truth for a project that may not exist yet, and the shape it
    is written in (one `grant … on … to …;` per line group) is stable.
    """
    out: dict[str, set[str]] = {}
    for privs, rels, roles in re.findall(
            r"^grant\s+(.+?)\s+on\s+(.+?)\s+to\s+([\w,\s]+);",
            SCHEMA.read_text(), re.MULTILINE | re.DOTALL):
        if role not in {r.strip() for r in roles.split(",")}:
            continue
        if "function" in rels:                      # `grant execute on function …`
            continue
        wanted = {p.strip() for p in privs.split(",")}
        for rel in (r.strip() for r in rels.split(",")):
            out.setdefault(rel.removeprefix("public."), set()).update(wanted)
    return out


def _python_uses() -> set[tuple[str, str]]:
    """``(relation, privilege)`` pairs the worker scripts need."""
    uses = set()
    for path in sorted((ROOT / "tools").glob("*.py")):
        text = path.read_text()
        for verb, privs in _PYTHON_CALLS.items():
            for rel in re.findall(rf'\.{verb}\(\s*"([a-z_]+)"', text):
                uses.update((rel, priv) for priv in privs)
    return uses


def _function_uses() -> set[tuple[str, str]]:
    """...and the pairs the Netlify functions need, read off their REST paths."""
    uses = set()
    for path in sorted(FUNCTIONS.glob("*.mjs")):
        text = path.read_text()
        for match in re.finditer(r"/rest/v1/(\w+)", text):
            window = text[match.start(): match.start() + 400]
            uses.add((match.group(1), "insert" if '"POST"' in window else "select"))
    return uses


def test_the_schema_grants_everything_the_worker_reads_and_writes():
    granted = _granted("service_role")
    missing = sorted(f"{priv} on {rel}"
                     for rel, priv in _python_uses() | _function_uses()
                     if priv not in granted.get(rel, set()))
    assert not missing, (
        "schema.sql does not grant service_role: " + ", ".join(missing)
        + ". A secret key bypasses RLS but not GRANT, so these fail outright — and"
        " only for the worker, which is why nothing on the site would show it.")


def test_the_scrape_actually_found_the_callers():
    """A regex that silently matches nothing would make the test above vacuous."""
    uses = _python_uses() | _function_uses()
    assert ("game_logs", "select") in uses          # verify_scores / position_suite
    assert ("game_logs", "insert") in uses          # netlify/functions/log.mjs
    assert ("public_replays", "select") in uses     # netlify/functions/replay.mjs
    assert ("bot_scores", "insert") in uses         # bot_replay's upsert
    assert len(uses) >= 8


@pytest.mark.parametrize("relation", ["game_logs", "public_replays"])
def test_a_replay_is_never_granted_to_the_public(relation):
    """The other half of the rule, and the one that matters more: `game_logs` is
    readable by nobody but the worker, and `public_replays` lends out only the
    replays a posted score already points at."""
    public = _granted("anon")
    assert "insert" not in public.get(relation, set())
    assert "update" not in public.get(relation, set())
    assert "delete" not in public.get(relation, set())
    if relation == "game_logs":
        assert public.get(relation, set()) == set(), "an uploaded replay is not public"


def _view_columns(view: str) -> list[str]:
    """The column list `create or replace view public.<view>` selects, in order,
    as written (``l.rules_version`` -> ``rules_version``)."""
    match = re.search(
        rf"create or replace view public\.{view} as\s*\nselect(?: distinct on \([^)]*\))?\s*\n\s*(.+?)\n",
        SCHEMA.read_text())
    assert match is not None, f"{view} not found in {SCHEMA}"
    return [col.strip().rsplit(".", 1)[-1] for col in match.group(1).split(",")]


def test_a_views_new_columns_land_after_its_old_ones():
    """`create or replace view` only accepts an *existing* view's columns back
    unchanged in name, order and type; a new one has to be appended at the end or
    Postgres refuses the whole statement (it reads as renaming/retyping whatever
    column now sits where the new one was inserted). That already happened once
    here: `rules_version` was added ahead of `log` in `public_replays`, which
    would fail silently from a caller's point of view — the *old* view is left in
    place, and every later query for the new column errors and is swallowed by
    `game.mjs`'s `.catch(() => [])`, taking down every replay's Watch link, not
    just a new one's.

    Pinned against the shipped column order rather than history, since that is
    the one thing a future column addition could get wrong the same way.
    """
    assert _view_columns("public_replays") == [
        "match_id", "game_key", "turns", "finished", "won", "hand", "log", "rules_version",
    ]
