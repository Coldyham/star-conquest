"""The crown views in leaderboard/schema.sql, run against a real Postgres.

Opt-in: set ``SC_TEST_PG`` to a libpq connection string for a scratch server
you don't mind a database being created and dropped on (e.g.
``host=/tmp/pg port=55432 user=postgres``). Without it the module skips — the
ordinary suite has no database, and a mirror of the rules in Python would only
test the mirror.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DSN = os.environ.get("SC_TEST_PG", "")
SCHEMA = Path(__file__).resolve().parents[1] / "leaderboard" / "schema.sql"
DB = "sc_crowns_test"

pytestmark = pytest.mark.skipif(not DSN or not shutil.which("psql"),
                                reason="set SC_TEST_PG to run the crown SQL against Postgres")


def _psql(sql: str, db: str = "postgres") -> str:
    out = subprocess.run(
        ["psql", f"{DSN} dbname={db}", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


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
                          "-f", str(SCHEMA)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    # Users 1-3; each map a separate story, scores posted a minute apart in order.
    _psql("""
      insert into users (name) values ('alice'), ('bob'), ('cara');
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        select k, 'random', 2, 18, 0, '{}' from unnest(array[
          'solo', 'steal', 'tie', 'own', 'bad', 'lost']) k;
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at)
      select g, u, t, l, t, '', timestamptz '2026-09-21 00:00Z' + n * interval '1 minute'
      from (values
        -- one player only: no crown, however good
        (1, 'solo', 1, 10, 0),
        -- bob beats alice: a steal, and bob's crown
        (2, 'steal', 1, 50, 3), (3, 'steal', 2, 40, 9),
        -- cara only ties alice: alice keeps it, no steal
        (4, 'tie', 1, 30, 2), (5, 'tie', 3, 30, 2),
        -- alice improves on her own record after bob trails: no steal
        (6, 'own', 1, 30, 2), (7, 'own', 2, 35, 0), (8, 'own', 1, 25, 0),
        -- bob's better score is a proven mismatch: it neither holds nor steals
        (9, 'bad', 1, 30, 2), (10, 'bad', 2, 35, 0), (11, 'bad', 2, 10, 0),
        -- same turns, fewer lost: the tie-break on lost does steal
        (12, 'lost', 1, 20, 5), (13, 'lost', 3, 20, 4)
      ) v(n, g, u, t, l);
      insert into score_checks (score_id, verdict)
        select id, 'mismatch' from scores where game_key = 'bad' and turns = 10;
    """, DB)
    yield DB
    _psql(f"drop database if exists {DB}")


def test_crowns_go_to_the_first_holder_of_the_best_result_on_contested_maps(board):
    rows = _psql("select game_key, user_name, rivals from crown_holders order by game_key", board)
    assert rows.splitlines() == [
        "bad|alice|1",     # bob's mismatch is not counted, so alice keeps it
        "lost|cara|1",
        "own|alice|1",
        "steal|bob|1",
        "tie|alice|1",     # a tie never takes a crown
    ]                      # and 'solo' is absent: nobody to take it from


def test_only_beating_somebody_elses_record_is_a_steal(board):
    rows = _psql("select game_key, taker_name, from_name, turns, from_turns"
                 " from crown_steals order by game_key", board)
    assert rows.splitlines() == ["lost|cara|alice|20|20", "steal|bob|alice|40|50"]


def test_campaign_scores_are_the_weeks_hand_played_scores_on_its_nodes(board):
    """Matched by config and seed, inside the week, with a turn played by hand,
    and not a proven mismatch — everything else is an ordinary score. The node
    was written by Python (`18.0`) and the game row by a browser (`18`)."""
    _psql("""
      insert into campaigns (week_start, graph) values ('2026-09-28', jsonb_build_object(
        'nodes', jsonb_build_array(
          jsonb_build_object('id', 0, 'kind', 'field', 'settings',
            '{"mode":"random","players":3,"nodes":18,"seed":100,"ship_ly_per_turn":18.0}'::jsonb),
          jsonb_build_object('id', 1, 'kind', 'home', 'settings',
            '{"mode":"random","players":3,"nodes":18,"seed":102}'::jsonb)),
        'lanes', '[[0, 1]]'::jsonb));
      insert into games (game_key, mode, players, nodes, seed, settings_json) values
        ('node', 'random', 3, 18, 100,
         '{"mode":"random","players":3,"nodes":18,"seed":100,"ship_ly_per_turn":18}'),
        ('other-seed', 'random', 3, 18, 101, '{"mode":"random","players":3,"nodes":18,"seed":101}'),
        ('other-config', 'random', 4, 18, 100, '{"mode":"random","players":4,"nodes":18,"seed":100}');
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at) values
        ('node', 1, 40, 2, 40, '', '2026-09-28 00:00Z'),          -- counts
        ('node', 2, 38, 2, 5,  '', '2026-10-04 23:59Z'),          -- counts: any hand turns
        ('node', 3, 30, 0, 0,  '', '2026-09-30 12:00Z'),          -- autoplayed
        ('node', 3, 30, 0, 30, '', '2026-09-27 23:59Z'),          -- the week before
        ('node', 3, 30, 0, 30, '', '2026-10-05 00:00Z'),          -- the week after
        ('node', 3, 29, 0, 29, '', '2026-09-29 12:00Z'),          -- a proven mismatch
        ('other-seed', 3, 20, 0, 20, '', '2026-09-29 12:00Z'),
        ('other-config', 3, 20, 0, 20, '', '2026-09-29 12:00Z');
      insert into score_checks (score_id, verdict)
        select id, 'mismatch' from scores where game_key = 'node' and turns = 29;
    """, board)
    rows = _psql("select node_id, user_name, turns from campaign_scores"
                 " where week_start = '2026-09-28' order by submitted_at", board)
    assert rows.splitlines() == ["0|alice|40", "0|bob|38"]
    games = _psql("select node_id, kind, game_key from campaign_games"
                  " where week_start = '2026-09-28'", board)
    assert games.splitlines() == ["0|field|node"]   # nobody has posted on the home


def test_campaign_scores_carry_the_stamp_from_the_start_of_the_game(board):
    """`scores.campaign_start` rides through to `campaign_scores` for fold's
    grace; blank by default, and nothing but a reason's shape is stored."""
    _psql("""
      insert into campaigns (week_start, graph) values ('2026-10-05', jsonb_build_object(
        'nodes', jsonb_build_array(jsonb_build_object('id', 4, 'kind', 'field', 'settings',
          '{"mode":"random","players":3,"nodes":18,"seed":400}'::jsonb)),
        'lanes', '[]'::jsonb));
      insert into games (game_key, mode, players, nodes, seed, settings_json) values
        ('stamped', 'random', 3, 18, 400, '{"mode":"random","players":3,"nodes":18,"seed":400}');
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at, campaign_start)
        values ('stamped', 1, 40, 2, 40, '', '2026-10-05 01:00Z', 'adjacent');
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at)
        values ('stamped', 2, 38, 2, 38, '', '2026-10-05 02:00Z');
    """, board)
    rows = _psql("select user_name, campaign_start from campaign_scores"
                 " where week_start = '2026-10-05' order by submitted_at", board)
    assert rows.splitlines() == ["alice|adjacent", "bob|"]
    refused = subprocess.run(
        ["psql", f"{DSN} dbname={board}", "-v", "ON_ERROR_STOP=1", "-qAt", "-c",
         "insert into scores (game_key, user_id, turns, lost, hand, raw_token, campaign_start)"
         " values ('stamped', 3, 30, 0, 30, '', 'Adjacent; drop')"],
        capture_output=True, text=True)
    assert refused.returncode != 0 and "campaign_start" in refused.stderr
