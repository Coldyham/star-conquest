"""Claimed names in leaderboard/schema.sql, run against a real Postgres.

Opt-in like the other SQL suites (``SC_TEST_PG``; see test_crowns_sql). Each
statement runs as the role PostgREST would use (``anon``/``authenticated``),
signed in or out through ``request.jwt.claim.sub`` (tests/supabase_auth_stub.sql),
since RLS and column grants are only checked for a non-superuser.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_crowns_sql import AUTH_STUB, DSN, SCHEMA, _psql, pytestmark  # noqa: F401

DB = "sc_claims_test"
ANN = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _load() -> None:
    out = subprocess.run(["psql", f"{DSN} dbname={DB}", "-v", "ON_ERROR_STOP=1", "-q",
                          "-f", str(AUTH_STUB), "-f", str(SCHEMA)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


@pytest.fixture()
def board():
    _psql(f"drop database if exists {DB}")
    _psql(f"create database {DB}")
    _psql("do $$ begin"
          " if not exists (select from pg_roles where rolname = 'anon') then create role anon; end if;"
          " if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated; end if;"
          " if not exists (select from pg_roles where rolname = 'service_role') then create role service_role; end if;"
          " end $$", DB)
    _load()
    _psql(f"""
      insert into auth.users (id, email) values ('{ANN}', 'ann@x'), ('{BOB}', 'bob@x');
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        values ('map', 'random', 2, 18, 0, '{{}}');
      insert into users (name) values ('Used'), ('Empty');
      insert into scores (game_key, user_id, turns, lost, hand, raw_token)
        select 'map', id, 9, 1, 9, '' from users where name = 'Used';
    """, DB)
    yield DB


def _as(sql: str, role: str = "authenticated", uid: str = "") -> subprocess.CompletedProcess:
    """Run ``sql`` as ``role``, signed in as ``uid`` (or signed out)."""
    script = (f"set role {role}; set request.jwt.claim.sub = '{uid}'; {sql}")
    return subprocess.run(["psql", f"{DSN} dbname={DB}", "-v", "ON_ERROR_STOP=1", "-qAt",
                           "-c", script], capture_output=True, text=True)


def _ok(sql: str, role: str = "authenticated", uid: str = "") -> str:
    out = _as(sql, role, uid)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _refused(sql: str, match: str, role: str = "authenticated", uid: str = "") -> None:
    out = _as(sql, role, uid)
    assert out.returncode != 0, f"expected a refusal, got {out.stdout!r}"
    assert match in out.stderr, out.stderr


def _score_as(name: str) -> str:
    return ("insert into scores (game_key, user_id, turns, lost, hand, raw_token)"
            f" select 'map', id, 5, 0, 5, '' from users where name_key = lower('{name}')")


def test_an_unused_name_is_claimed_and_then_only_its_owner_posts(board):
    _ok("select claim_name('Ann')", uid=ANN)
    assert _ok("select my_name()", uid=ANN) == "Ann"
    _ok(_score_as("ann"), uid=ANN)
    _refused(_score_as("ann"), "row-level security", role="anon")
    _refused(_score_as("ann"), "row-level security", uid=BOB)
    # ...and an unclaimed name is anyone's, exactly as before.
    _ok(_score_as("used"), role="anon")


def test_a_name_with_a_score_is_in_use_but_an_empty_row_is_not(board):
    _refused("select claim_name('used')", "name in use", uid=ANN)
    _ok("select claim_name(' EMPTY ')", uid=ANN)
    assert _ok("select claimed from users where name = 'Empty'", role="anon") == "t"


def test_one_name_per_account_and_signing_in_is_required(board):
    _refused("select claim_name('Ann')", "permission denied", role="anon")
    _refused("select claim_name('Ann')", "sign in", uid="")
    _ok("select claim_name('Ann')", uid=ANN)
    _refused("select claim_name('Annie')", "already own", uid=ANN)
    _refused("select claim_name('ann')", "name in use", uid=BOB)
    _ok("select release_name()", uid=ANN)
    _ok("select claim_name('Annie')", uid=ANN)
    _ok("select claim_name('Ann')", uid=BOB)


def test_the_owner_uuid_is_never_public(board):
    _ok("select claim_name('Ann')", uid=ANN)
    _refused("select owner from users", "permission denied", role="anon")
    _refused("select * from users", "permission denied", role="anon")
    assert _ok("select name, claimed from users where name = 'Ann'", role="anon") == "Ann|t"
    # Nobody makes a row pre-owned by an account, their own included:
    # claim_name is the only way an owner is set from the site.
    _refused(f"insert into users (name, owner) values ('Mine', '{ANN}')",
             "row-level security", uid=ANN)


def test_a_plain_insert_still_makes_an_unclaimed_user(board):
    assert _ok("insert into users (name) values ('New') returning id", role="anon").isdigit()


def test_re_pasting_the_schema_keeps_claims_and_stays_private(board):
    _ok("select claim_name('Ann')", uid=ANN)
    _load()
    assert _ok("select my_name()", uid=ANN) == "Ann"
    _refused("select owner from users", "permission denied", role="anon")
