"""The home page's filter columns on `game_summary` (leaderboard/schema.sql)
against a real Postgres: `contenders`, `bot_leads` and `fog`.

Opt-in, like test_crowns_sql: set ``SC_TEST_PG``.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_crowns_sql import AUTH_STUB, DSN, SCHEMA, _psql, pytestmark  # noqa: F401

DB = "sc_filters_test"


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
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    # solo: one player twice. pair: two players, but bob's score is a mismatch.
    # duel: two counted players. fog/scout: fog on via either range. Bots: on
    # `pair` a bot ties the human best, on `duel` a bot loses to it, on `solo`
    # only a losing (won = false) bot row exists.
    _psql("""
      insert into users (name) values ('alice'), ('bob');
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        select k, 'random', 2, 18, row_number() over (),
               jsonb_build_object('mode', 'random', 'players', 2, 'nodes', 18) || extra
        from (values ('solo', '{}'::jsonb), ('pair', '{}'), ('duel', '{}'),
                     ('fog', '{"fog_sight": 1, "fog_scout": 3}'),
                     ('scout', '{"fog_scout": 5}')) v(k, extra);
      insert into scores (game_key, user_id, turns, lost, hand, raw_token) values
        ('solo', 1, 30, 0, 30, ''), ('solo', 1, 28, 0, 28, ''),
        ('pair', 1, 30, 2, 30, ''), ('pair', 2, 20, 0, 20, ''),
        ('duel', 1, 30, 2, 30, ''), ('duel', 2, 25, 0, 25, ''),
        ('fog', 1, 30, 0, 30, ''), ('scout', 1, 30, 0, 30, '');
      insert into score_checks (score_id, verdict)
        select id, 'mismatch' from scores where game_key = 'pair' and user_id = 2;
      insert into bot_scores (game_key, bot, turns, lost, won, engine_rev) values
        ('pair', 'marshal', 20, 0, true, 'e'),
        ('duel', 'marshal', 26, 0, true, 'e'),
        ('solo', 'marshal', 99, 0, false, 'e');
    """, DB)
    yield DB
    _psql(f"drop database if exists {DB}")


def _column(board: str, column: str) -> dict[str, str]:
    rows = _psql(f"select game_key, {column} from game_summary order by game_key", board)
    return dict(line.split("|") for line in rows.splitlines())


def test_contenders_counts_players_the_crowns_count(board):
    """A mismatched score doesn't make a map contested, the same rule as
    crown_holders, so `contenders >= 2` is exactly the crowned maps."""
    assert _column(board, "contenders") == {
        "duel": "2", "fog": "1", "pair": "1", "scout": "1", "solo": "1"}
    crowned = _psql("select game_key from crown_holders order by game_key", board)
    contested = _psql("select game_key from game_summary where contenders >= 2"
                      " order by game_key", board)
    assert crowned == contested == "duel"


def test_a_bot_leads_until_a_human_strictly_beats_it(board):
    """game_summary.best counts every score (a mismatch included), as the
    card's headline does; a tie is still the bot's, and a losing bot row leads
    nothing."""
    assert _column(board, "bot_leads") == {
        "duel": "f", "fog": "f", "pair": "t", "scout": "f", "solo": "f"}


def test_fog_is_on_when_either_range_is_stored(board):
    assert _column(board, "fog") == {
        "duel": "f", "fog": "t", "pair": "f", "scout": "t", "solo": "f"}
    configs = _psql("select fog::text from config_summary order by fog", board)
    assert configs.splitlines() == ["false", "true", "true"]
