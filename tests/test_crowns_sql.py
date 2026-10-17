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


def test_a_score_counts_once_verified_or_for_a_day_while_its_replay_is_awaited(board):
    """Posting a score is a public insert, so one the worker has not vouched for
    counts only while the worker may not have reached it yet, and only if it
    names a replay that could vouch for it. A score from before the cutoff
    counts as it did when posted. Only the first post of a match counts, so a
    verified replay can't be posted again under a second name."""
    _psql("""
      create or replace function sc_legacy_cutoff() returns timestamptz
        language sql immutable as $$ select timestamptz '2026-10-01 00:00Z' $$;
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        select k, 'random', 2, 18, 0, '{}' from unnest(array[
          'fresh', 'stale', 'blank', 'gone', 'forged', 'copied', 'lineup',
          'legacy', 'rules']) k;
      with v(g, u, t, at, m, verdict, outdated) as (values
        -- alice's verified score, then bob's unchecked one from an hour ago
        ('fresh', 1, 40, now() - interval '3 days', 'a000000000000001', 'verified', false),
        ('fresh', 2, 30, now() - interval '1 hour', 'a000000000000002', null, null),
        -- more than a day on, an unchecked score no longer counts
        ('stale', 1, 40, now() - interval '3 days', 'a000000000000003', 'verified', false),
        ('stale', 2, 30, now() - interval '25 hours', 'a000000000000004', null, null),
        -- a score that names no replay never counts, however fresh
        ('blank', 1, 40, now() - interval '3 days', 'a000000000000005', 'verified', false),
        ('blank', 2, 30, now() - interval '1 hour', '', null, null),
        -- `missing` is retried, so it gets its day like an unchecked one
        ('gone', 1, 40, now() - interval '3 days', 'a000000000000006', 'verified', false),
        ('gone', 2, 30, now() - interval '1 hour', 'a000000000000007', 'missing', false),
        -- a log claiming older rules is excused, not counted
        ('forged', 1, 40, now() - interval '3 days', 'a000000000000008', 'verified', false),
        ('forged', 2, 30, now() - interval '1 hour', 'a000000000000009', 'mismatch', true),
        ('forged', 3, 20, now() - interval '1 hour', 'a00000000000000a', 'unreadable', false),
        -- bob re-posts alice's verified match under his own name
        ('copied', 1, 30, now() - interval '3 days', 'a00000000000000b', 'verified', false),
        ('copied', 2, 30, now() - interval '2 days', 'a00000000000000b', 'verified', false),
        -- verified against an older lineup: still a real score
        ('lineup', 1, 40, now() - interval '3 days', 'a00000000000000c', 'verified', false),
        ('lineup', 2, 30, now() - interval '3 days', 'a00000000000000d', 'verified', true),
        -- before the cutoff, anything but a mismatch under current rules
        ('legacy', 1, 40, timestamptz '2026-09-01', 'a00000000000000e', 'missing', false),
        ('legacy', 2, 30, timestamptz '2026-09-02', '', null, null),
        ('legacy', 3, 20, timestamptz '2026-09-03', 'a00000000000000f', 'mismatch', false),
        ('rules', 1, 40, timestamptz '2026-09-01', 'a000000000000010', 'mismatch', true),
        ('rules', 2, 30, now() - interval '1 hour', 'a000000000000011', 'mismatch', true)
      ), posted as (
        insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at, match_id)
        select g, u, t, 0, t, '', at, m from v
        returning id, game_key, user_id
      )
      insert into score_checks (score_id, verdict, outdated)
      select p.id, v.verdict, v.outdated from posted p
      join v on v.g = p.game_key and v.u = p.user_id
      where v.verdict is not null;
    """, board)
    rows = _psql("select game_key, user_name from counted_scores where game_key in"
                 " ('fresh', 'stale', 'blank', 'gone', 'forged', 'copied', 'lineup',"
                 " 'legacy', 'rules') order by game_key, user_name", board)
    assert rows.splitlines() == [
        "blank|alice",
        "copied|alice",
        "forged|alice",
        "fresh|alice", "fresh|bob",
        "gone|alice", "gone|bob",
        "legacy|alice", "legacy|bob",
        "lineup|alice", "lineup|bob",
        "rules|alice",
        "stale|alice",
    ]


def test_a_replay_is_the_log_its_matchs_first_score_was_checked_on(board):
    """Anyone can upload under a public match_id, so the longest upload is not
    evidence. The Watch link serves the log the match's first score verified
    on, even past a later poster's own verified copy; before any check, the
    longest uploaded before the score was posted, else the longest."""
    _psql("""
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        values ('watch', 'random', 2, 18, 0, '{}');
      insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at, match_id)
        values ('watch', 1, 30, 0, 30, '', now() - interval '3 days', 'b000000000000001'),
               ('watch', 2, 50, 0, 50, '', now() - interval '2 days', 'b000000000000001'),
               ('watch', 1, 30, 0, 30, '', now() - interval '1 hour', 'b000000000000002'),
               ('watch', 1, 30, 0, 30, '', now() - interval '1 hour', 'b000000000000003');
      insert into game_logs (match_id, game_key, turns, log) values
        ('b000000000000001', 'watch', 30, 'real'),
        ('b000000000000001', 'watch', 99, 'forged'),
        ('b000000000000001', 'watch', 50, 'copy'),
        ('b000000000000002', 'watch', 10, 'checkpoint'),
        ('b000000000000002', 'watch', 30, 'final'),
        ('b000000000000003', 'watch', 99, 'forged');
      insert into game_logs (match_id, game_key, turns, log, submitted_at) values
        ('b000000000000003', 'watch', 30, 'own', now() - interval '2 hours');
      insert into score_checks (score_id, verdict, log_id)
        select s.id, 'verified', l.id from scores s
        join game_logs l on l.match_id = s.match_id
          and l.log = case s.user_id when 1 then 'real' else 'copy' end
        where s.match_id = 'b000000000000001';
    """, board)
    rows = _psql("select match_id, log from public_replays where game_key = 'watch'"
                 " order by match_id", board)
    assert rows.splitlines() == ["b000000000000001|real", "b000000000000002|final",
                                 "b000000000000003|own"]


def test_a_game_counts_once_whatever_match_it_is_posted_under(board):
    """A verified replay is public, so it can be uploaded again under a fresh
    match_id and posted under another name. It replays through the same boards
    (score_checks.game_digest), so only its first post counts."""
    _psql("""
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        values ('twin', 'random', 2, 18, 0, '{}');
      with v(u, t, at, m, digest) as (values
        (1, 30, now() - interval '3 days', 'c000000000000001', 'aaaa'),
        (2, 30, now() - interval '2 days', 'c000000000000002', 'aaaa'),
        (3, 31, now() - interval '1 day', 'c000000000000003', 'bbbb')
      ), posted as (
        insert into scores (game_key, user_id, turns, lost, hand, raw_token, submitted_at, match_id)
        select 'twin', u, t, 0, t, '', at, m from v
        returning id, user_id
      )
      insert into score_checks (score_id, verdict, game_digest)
      select p.id, 'verified', v.digest from posted p join v on v.u = p.user_id;
    """, board)
    rows = _psql("select user_name from counted_scores where game_key = 'twin'"
                 " order by user_name", board)
    assert rows.splitlines() == ["alice", "cara"]


def test_a_maps_record_is_its_best_counted_score(board):
    """game_summary's best is what the home card and "Bot leads" show, so a
    score that doesn't count for a crown isn't the record either; with nothing
    counted there is no record, though the scores are still there."""
    _psql("""
      create or replace function sc_legacy_cutoff() returns timestamptz
        language sql immutable as $$ select timestamptz '2026-10-01 00:00Z' $$;
      insert into games (game_key, mode, players, nodes, seed, settings_json)
        values ('record', 'random', 2, 18, 0, '{}'), ('nothing', 'random', 2, 18, 0, '{}');
      insert into scores (game_key, user_id, turns, lost, hand, by_name, raw_token, submitted_at, match_id)
        values ('record', 1, 40, 0, 40, 'Al', '', now() - interval '3 days', 'd000000000000001'),
               ('record', 2, 40, 0, 40, '', '', now() - interval '2 days', 'd000000000000002'),
               ('record', 3, 10, 0, 10, '', '', now() - interval '2 days', ''),
               ('nothing', 3, 10, 0, 10, '', '', now() - interval '2 days', '');
      insert into score_checks (score_id, verdict)
        select id, 'verified' from scores where match_id in ('d000000000000001', 'd000000000000002');
    """, board)
    rows = _psql("select game_key, best_turns, best_by_name, best_user_name, best_holders,"
                 " score_count from game_summary where game_key in ('record', 'nothing')"
                 " order by game_key", board)
    assert rows.splitlines() == ["nothing||||0|1", "record|40|Al|alice|2|3"]


def test_the_public_cannot_choose_when_a_score_was_posted(board):
    """counted_scores trusts submitted_at, so the public's insert leaves it to
    the server: a backdated score is refused, and one without it is stamped now."""
    _psql("insert into games (game_key, mode, players, nodes, seed, settings_json)"
          " values ('stamp', 'random', 2, 18, 0, '{}')", board)
    backdated = subprocess.run(
        ["psql", f"{DSN} dbname={board}", "-v", "ON_ERROR_STOP=1", "-qAt", "-c",
         "set role anon; insert into scores"
         " (game_key, user_id, turns, lost, hand, raw_token, submitted_at)"
         " values ('stamp', 1, 30, 0, 30, '', '2026-09-01 00:00Z')"],
        capture_output=True, text=True)
    assert backdated.returncode != 0 and "permission denied" in backdated.stderr
    _psql("set role anon; insert into scores (game_key, user_id, turns, lost, hand, raw_token)"
          " values ('stamp', 1, 30, 0, 30, '')", board)
    assert _psql("select count(*) from scores where game_key = 'stamp'"
                 " and submitted_at > now() - interval '1 minute'", board) == "1"
