"""The puzzle's par check (`sc_puzzle_par`, leaderboard/schema.sql) against a real
Postgres: a par is answered only to a result that matches or beats it, never for a
day not yet started, and never under other rules than the day's puzzle.

Opt-in, like test_crowns_sql: set ``SC_TEST_PG``.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_crowns_sql import AUTH_STUB, DSN, SCHEMA, _psql, pytestmark  # noqa: F401

DB = "sc_puzzle_par_test"
_TODAY = "(now() at time zone 'UTC')::date"


@pytest.fixture(scope="module")
def board():
    _psql(f"drop database if exists {DB}")
    _psql(f"create database {DB}")
    _psql("do $$ begin"
          " if not exists (select from pg_roles where rolname = 'anon') then create role anon; end if;"
          " if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated; end if;"
          " if not exists (select from pg_roles where rolname = 'service_role') then create role service_role; end if;"
          " end $$", DB)
    out = subprocess.run(["psql", f"{DSN} dbname={DB}", "-v", "ON_ERROR_STOP=1", "-q",
                          "-f", str(AUTH_STUB), "-f", str(SCHEMA)],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    # Today's puzzle has par 10/3; tomorrow's (already made) 6/2; yesterday's par
    # was found under older rules than its puzzle.
    _psql(f"""
      insert into dailies (day, kind, settings, spec, probes, rules_version)
        select {_TODAY} + d, 'puzzle', jsonb_build_object('seed', d + 10), '{{}}'::jsonb, '[]'::jsonb, 5
        from generate_series(-1, 1) d;
      -- Today's puzzle map, posted on by alice (11/0, then 10/3, owned), bob
      -- (12/1, owned, short of the par), carol (9/9 with turns autoplayed) and
      -- an unclaimed dave (10/3).
      insert into auth.users (id) values
        ('00000000-0000-0000-0000-00000000000a'), ('00000000-0000-0000-0000-00000000000b'),
        ('00000000-0000-0000-0000-00000000000c');
      insert into users (name, owner) values
        ('alice', '00000000-0000-0000-0000-00000000000a'), ('bob', '00000000-0000-0000-0000-00000000000b'),
        ('carol', '00000000-0000-0000-0000-00000000000c'), ('dave', null);
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        values ('p0', 'random', 2, 6, 10, jsonb_build_object('seed', 10));
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, match_id)
        select 'p0', u.id, t, l, h, '', m from (values
          ('alice', 11, 0, 11, 'a1'), ('alice', 10, 3, 10, 'a2'), ('bob', 12, 1, 12, 'b1'),
          ('carol', 9, 9, 4, 'c1'), ('dave', 10, 3, 10, 'd1')) v(n, t, l, h, m)
        join users u on u.name = v.n;
      insert into score_checks (score_id, verdict) select id, 'verified' from scores;
      insert into puzzle_pars (day, turns, lost, status, line, rules_version) values
        ({_TODAY}, 10, 3, 'proved', '{{}}'::jsonb, 5),
        ({_TODAY} + 1, 6, 2, 'likely', '{{}}'::jsonb, 5),
        ({_TODAY} - 1, 8, 1, 'likely', '{{}}'::jsonb, 4);
    """, DB)
    yield DB
    _psql(f"drop database if exists {DB}")


def _ask(board: str, day: str, turns: int, lost: int) -> str:
    return _psql(f"set role anon; select sc_puzzle_par({day}, {turns}, {lost})", board)


def test_a_result_short_of_the_par_learns_nothing(board):
    assert _ask(board, _TODAY, 11, 0) == ""
    assert _ask(board, _TODAY, 10, 4) == ""


def test_a_match_or_a_beat_is_answered_with_the_par(board):
    assert '"beaten": false' in _ask(board, _TODAY, 10, 3)
    assert '"turns": 10' in _ask(board, _TODAY, 10, 3)
    assert '"beaten": true' in _ask(board, _TODAY, 10, 2)
    assert '"status": "proved"' in _ask(board, _TODAY, 9, 9)


def test_no_par_before_its_day_or_under_other_rules(board):
    assert _ask(board, f"{_TODAY} + 1", 1, 0) == ""
    assert _ask(board, f"{_TODAY} - 1", 1, 0) == ""


def test_the_public_cannot_read_the_table(board):
    out = subprocess.run(["psql", f"{DSN} dbname={board}", "-v", "ON_ERROR_STOP=1", "-qAt", "-c",
                          "set role anon; select line from puzzle_pars"],
                         capture_output=True, text=True, check=False)
    assert out.returncode != 0 and "permission denied" in out.stderr


def _mine(board: str, who: str) -> str:
    uid = f"00000000-0000-0000-0000-00000000000{who}" if who else ""
    return _psql(f"set role authenticated; set request.jwt.claim.sub = '{uid}';"
                 f" select sc_my_puzzle_par({_TODAY})", board)


def test_a_signed_in_player_whose_score_met_the_par_is_told(board):
    got = _mine(board, "a")
    assert '"my_turns": 10' in got and '"my_lost": 3' in got and '"turns": 10' in got


def test_nobody_else_is_told(board):
    assert _mine(board, "b") == ""        # short of it
    assert _mine(board, "c") == ""        # turns not played by hand
    assert _mine(board, "") == ""         # signed out (dave's 10/3 is nobody's)
    out = subprocess.run(["psql", f"{DSN} dbname={board}", "-v", "ON_ERROR_STOP=1", "-qAt", "-c",
                          f"set role anon; select sc_my_puzzle_par({_TODAY})"],
                         capture_output=True, text=True, check=False)
    assert out.returncode != 0 and "permission denied" in out.stderr
