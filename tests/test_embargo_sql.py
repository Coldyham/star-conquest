"""The embargo in force (`game_embargoes`, leaderboard/schema.sql) against a real
Postgres: a map's own stored reveal date, or the end of a live campaign week it
is a node of, whichever is later.

Opt-in, like test_crowns_sql: set ``SC_TEST_PG``. Campaign weeks are placed
relative to the server's clock, since "live" is a question about now.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_crowns_sql import AUTH_STUB, DSN, SCHEMA, _psql, pytestmark  # noqa: F401

DB = "sc_embargo_test"

# This week's Monday and the one before it, UTC — the campaign's own boundary.
_THIS_WEEK = "date_trunc('week', now() at time zone 'UTC')::date"


def _node(node_id: int, seed: int) -> str:
    return (f"jsonb_build_object('id', {node_id}, 'kind', 'field', 'settings',"
            f" '{{\"mode\":\"random\",\"players\":2,\"nodes\":18,\"seed\":{seed},"
            f"\"ship_ly_per_turn\":6.0}}'::jsonb)")


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
    # Seeds 1-3 are this week's nodes, 4 last week's; 9 is on no campaign. The
    # game rows are browser-written (`6`, not the worker's `6.0`).
    _psql(f"""
      insert into campaigns (week_start, graph) values
        ({_THIS_WEEK}, jsonb_build_object('nodes',
           jsonb_build_array({_node(0, 1)}, {_node(1, 2)}, {_node(2, 3)}), 'lanes', '[]'::jsonb)),
        ({_THIS_WEEK} - 7, jsonb_build_object('nodes',
           jsonb_build_array({_node(0, 4)}), 'lanes', '[]'::jsonb));
      insert into users (name) values ('alice');
      insert into games (game_key, mode, players, nodes, seed, settings_json, embargo_until)
        select 'g' || s, 'random', 2, 18, s,
               jsonb_build_object('mode', 'random', 'players', 2, 'nodes', 18,
                                  'seed', s, 'ship_ly_per_turn', 6),
               e
        from (values (1, null::timestamptz), (2, now() + interval '30 days'),
                     (3, now() - interval '1 day'), (4, null), (9, null)) v(s, e);
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, match_id)
        select 'g' || s, 1, 30, 0, 30, '', lpad(s::text, 16, '0')
        from unnest(array[1, 2, 3, 4, 9]) s;
      insert into game_logs (match_id, game_key, turns, finished, won, hand, log)
        select lpad(s::text, 16, '0'), 'g' || s, 30, true, true, 30, 'x'
        from unnest(array[1, 2, 3, 4, 9]) s;
    """, DB)
    yield DB
    _psql(f"drop database if exists {DB}")


def test_a_live_campaign_node_is_embargoed_until_its_week_ends(board):
    rows = _psql(f"""
      select game_key,
             case when embargo_until is null then 'none'
                  when embargo_until = ({_THIS_WEEK} + 7)::timestamp at time zone 'UTC' then 'week end'
                  when embargo_until > now() then 'stored'
                  else 'lifted' end
      from game_summary order by game_key""", board)
    assert rows.splitlines() == [
        "g1|week end",   # a node with no embargo of its own
        "g2|stored",     # its own reveal is later than the week's: that one holds
        "g3|week end",   # its own reveal has passed; the week has not
        "g4|none",       # last week's campaign is over
        "g9|none",       # on no campaign at all
    ]


def test_only_replays_off_embargo_are_public(board):
    rows = _psql("select game_key from public_replays order by game_key", board)
    assert rows.splitlines() == ["g4", "g9"]
