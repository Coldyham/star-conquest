-- Star Conquest leaderboard schema. Paste into the Supabase SQL editor. The
-- whole file is idempotent: re-pasting it upgrades an existing board.
--
-- These comments say what each thing is and what breaks if you change it. Why
-- it is shaped that way: docs/design/schema.md.
--
-- Every table has RLS on, and a command with no policy is refused, so the
-- missing UPDATE/DELETE policies are what make the board append-only. The owner
-- moderates through tools/admin.py under the secret key, logged to admin_actions.
--
-- Editing this file:
-- - Order matters: functions before the views that call them, and configs,
--   config_tags and config_tag_counts before game_summary/config_summary.
-- - A new column on an existing table needs `alter table … add column if not
--   exists`, and a changed check needs a drop/add pair: `create table if not
--   exists` skips a table that already exists.
-- - A new column on a view goes last. `create or replace view` refuses a reorder
--   or rename, and a refused one leaves the old view in place.
-- - A relation a worker or function reads needs a grant to service_role (end of
--   file); tests/test_schema_grants.py checks.

-- ---------------------------------------------------------------------------
-- users: keyed by name alone. name_key folds case and surrounding spaces.
-- ---------------------------------------------------------------------------
create table if not exists public.users (
  id         bigint generated always as identity primary key,
  name       text not null check (char_length(trim(name)) between 1 and 60),
  name_key   text generated always as (lower(trim(name))) stored,
  created_at timestamptz not null default now(),
  constraint users_name_key_unique unique (name_key)
);

-- ---------------------------------------------------------------------------
-- games: one row per distinct setup. game_key is the token's Challenge.key
-- (Settings.challenge_key); a hand-written link gets a "j:"-prefixed hash from
-- js/token-decode.mjs instead. The checks are loose sanity bounds, not the
-- game's limits.
-- ---------------------------------------------------------------------------
create table if not exists public.games (
  game_key      text primary key,
  mode          text not null check (mode in ('random', 'symmetric')),
  players       integer not null check (players > 0),
  nodes         integer not null check (nodes > 0),
  seed          integer not null,
  settings_json jsonb not null,
  first_seen_at timestamptz not null default now()
);

-- games.embargo_until: an optional reveal date for the map's replays, set only
-- when js/submit.mjs's ensureGame inserts the row (there is no update path).
-- It hides replays, never scores. Readers take the embargo in force from
-- game_embargoes, which adds campaign weeks. The bound is a sanity cap.
alter table public.games add column if not exists embargo_until timestamptz;

alter table public.games drop constraint if exists games_embargo_bounds;
alter table public.games add  constraint games_embargo_bounds check (
  embargo_until is null or embargo_until <= first_seen_at + interval '90 days'
);

-- ---------------------------------------------------------------------------
-- scores: one row per submission, never deduplicated. Fewer turns is better,
-- then fewer lost. hand is how many turns the player decided; the rest were
-- autoplayed.
-- ---------------------------------------------------------------------------
create table if not exists public.scores (
  id           bigint generated always as identity primary key,
  game_key     text not null references public.games(game_key),
  user_id      bigint not null references public.users(id),
  turns        integer not null check (turns > 0),
  lost         integer not null check (lost >= 0),
  hand         integer not null check (hand >= 0),
  by_name      text not null default '',
  raw_token    text not null,
  submitted_at timestamptz not null default now()
);

create index if not exists scores_game_key_rank_idx
  on public.scores (game_key, turns, lost, submitted_at);

-- scores.match_id: the GameLog.match_id this score was played in (Challenge.log),
-- blank when there is none. No foreign key to game_logs: either row can arrive
-- first, and an unmatched id means unverified, not invalid.
alter table public.scores add column if not exists match_id text not null default '';

create index if not exists scores_match_id_idx
  on public.scores (match_id) where match_id <> '';

-- scores.campaign_start: Challenge.campaign, the attemptStatus reason the weekly
-- campaign gave for this node when the game started (e.g. 'adjacent'). Read by
-- js/campaign.mjs's startedWithAccess. A claim: a forged one gets no more grace
-- than a blank.
alter table public.scores add column if not exists campaign_start text not null default ''
  check (campaign_start ~ '^[a-z-]{0,20}$');

-- ---------------------------------------------------------------------------
-- game_logs: the replay behind a score (replay.GameLog.encoded), uploaded by the
-- game on "Post to leaderboard", and as it goes with "Share replays" on. A row
-- carries no identity.
--
-- Closed to the public: no policies and no anon grants. netlify/functions/log.mjs
-- writes it, the workers read it, and visitors see only public_replays. An
-- upload is a new row. Anyone may upload under a match_id, so the longest row
-- is not evidence: the worker records the one a score verified on
-- (score_checks.log_id) and prunes the rest. The size cap is what stops a blob filling the database
-- (whole games run 5-14 KiB).
-- ---------------------------------------------------------------------------
create table if not exists public.game_logs (
  id           bigint generated always as identity primary key,
  match_id     text not null check (match_id ~ '^[0-9a-f]{16}$'),
  game_key     text not null check (char_length(game_key) between 1 and 64),
  turns        integer not null check (turns > 0),
  -- Claims by the client, kept for finding logs without decoding them.
  -- tools/verify_scores.py settles them by replaying the log.
  finished     boolean not null default false,
  won          boolean not null default false,
  hand         integer not null default 0 check (hand >= 0),
  -- engine.RULES_VERSION at upload, a claim like the three above. Every row from
  -- before this column was played under rules 1.
  rules_version integer not null default 1 check (rules_version >= 1),
  log          text not null check (octet_length(log) between 1 and 262144),
  submitted_at timestamptz not null default now()
);

alter table public.game_logs add column if not exists finished boolean not null default false;
alter table public.game_logs add column if not exists won      boolean not null default false;
alter table public.game_logs add column if not exists hand     integer not null default 0;
alter table public.game_logs add column if not exists rules_version integer not null default 1;

-- Longest first within a match.
create index if not exists game_logs_match_idx
  on public.game_logs (match_id, turns desc);

-- ---------------------------------------------------------------------------
-- score_checks: what tools/verify_scores.py found replaying a score's log. One
-- row per score. Written by the worker, read by anyone.
--
--   verified    the log replays and reproduces the posted turns, lost and hand.
--               Once verified, always verified: it reproduced under the rules
--               it was played under.
--   mismatch    it replays and produces something else
--   unreadable  the blob does not decode, or does not replay at all
--   missing     no log was ever uploaded for this score's match_id
--
-- outdated is a second question: is the score still comparable with one
-- posted today? True when it was dealt a rival lineup the setup no longer
-- deals, or no longer reproduces under rules that have moved since
-- (engine.RULES_VERSION). So `mismatch` + outdated reads "didn't reproduce,
-- but under older rules: unverifiable, not wrong".
-- ---------------------------------------------------------------------------
create table if not exists public.score_checks (
  score_id   bigint primary key references public.scores(id) on delete cascade,
  verdict    text not null,
  -- One line on what went wrong, for whoever looks into a row. Empty on a pass.
  detail     text not null default '',
  -- bot_replay.replay_rev of the code that checked it.
  engine_rev text not null default '',
  checked_at timestamptz not null default now(),
  -- The game_logs row the verdict was reached on: the first upload that
  -- verified, else the best evidence there was. Null on `missing`. No foreign
  -- key: --prune keeps every log a verdict names, and nothing else may delete one.
  log_id     bigint,
  outdated   boolean not null default false,
  -- A hash of every board the replay passed through, '' when it didn't replay.
  -- counted_scores counts one game once by it, whatever match_id it is posted under.
  game_digest text not null default ''
);

alter table public.score_checks add column if not exists log_id bigint;
alter table public.score_checks add column if not exists outdated boolean not null default false;
alter table public.score_checks add column if not exists game_digest text not null default '';

-- `outdated` was a verdict before it was a column. A lineup row reproduced; a
-- rules row didn't.
alter table public.score_checks drop constraint if exists score_checks_verdict;
update public.score_checks
  set verdict = case when detail like 'dealt a different lineup%' then 'verified'
                     else 'mismatch' end,
      outdated = true
  where verdict = 'outdated';
alter table public.score_checks add  constraint score_checks_verdict check (
  verdict in ('verified', 'mismatch', 'unreadable', 'missing'));

-- ---------------------------------------------------------------------------
-- playstyle_readings: how the person played one game in public_replays, for the
-- Playstyle panel on user.html. One row per match, never per name: the page
-- finds a player's rows through their scores.match_id. Written by
-- tools/playstyle_worker.py, read by anyone.
-- ---------------------------------------------------------------------------
create table if not exists public.playstyle_readings (
  match_id    text primary key check (match_id ~ '^[0-9a-f]{16}$'),
  -- `playstyle.reading_rev()`: the code that read it. A row from other code is
  -- read again while its log still replays under today's rules.
  rev         text not null,
  -- `playstyle.readings()`; `shape` inside says which layout.
  readings    jsonb not null,
  computed_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- sc_config_key / sc_bots: a game's setup identity (its settings without the
-- seed) and its opponent roster, derived from settings_json.
--
-- Changing either body rehashes every config and orphans every name in
-- `configs`. So does changing a game-side default, since settings_json is
-- pruned to non-defaults. Changing sc_config_key's signature adds an overload
-- and leaves the views bound to the old one: that is a hand-run migration.
--
-- Supabase's linter flags both for a mutable search_path. Leave it: pinning it
-- would stop sc_config_key inlining into an expression index.
--
-- sc_config_key digests text, so it only groups rows written the same way. Every
-- games row comes from the browser; a Python-written setup (`0.0` where the
-- browser writes `0`) has to be matched by jsonb `=`, as campaign_games does.
-- ---------------------------------------------------------------------------
create or replace function public.sc_config_key(settings jsonb)
returns text
language sql immutable strict parallel safe as $$
  -- The parens are load-bearing: `::` binds tighter than `-`, so
  -- `settings - array[...]::text` would cast the array to the text literal
  -- '{seed,autoplay}' and delete a key by that name instead — silently wrong,
  -- and it would never throw.
  select substr(md5((settings - array['seed', 'autoplay'])::text), 1, 16);
$$;

-- One seat's bot as the board names it: "family:stop" for a bot with a stop
-- knob, the name alone otherwise. A name folded into a family as a stop
-- (ai.LEGACY: council, claudebot) is that family at that stop; council read any
-- aux that rounds to 1 or more as On. A stop is the whole part of aux, clamped
-- into the knob's range, except that a value past the top reads as `over`, the
-- top the knob had before its last stop was added (ai.aux_over); a missing or
-- unreadable aux is 1, every bot's default (evolved rounds where this takes the
-- whole part, which only a hand-edited fraction can tell apart). Both tables
-- are pinned to ai.py by tests/test_leaderboard_sync.py.
create or replace function public.sc_bot_profile(p_name text, p_aux text)
returns text
language sql immutable parallel safe as $$
  with legacy(old, family, off_stop, on_stop) as (values
    ('council', 'knower', 2, 3),
    ('claudebot', 'thinker', 0, 0)
  ), knobs(family, lo, hi, over) as (values
    ('knower', 0, 3, 2),
    ('actuary', 0, 3, 2),
    ('thinker', 0, 1, null::integer),
    ('evolved', 0, 1, null::integer)
  ), seat as (
    select coalesce(l.family, p_name) as family, l.off_stop, l.on_stop,
           case when p_aux ~ '^\s*-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?\s*$'
                then p_aux::numeric else 1 end as aux
    from (values (1)) as one(x)
    left join legacy l on l.old = p_name
  )
  select case
    when k.family is null then s.family
    else s.family || ':' || case
      when s.on_stop is not null then
        case when round(s.aux) >= 1 then s.on_stop else s.off_stop end
      when s.aux > k.hi and k.over is not null then k.over
      else greatest(k.lo, least(k.hi, floor(s.aux)))::integer
    end
  end
  from seat s left join knobs k on k.family = s.family;
$$;

-- The distinct opponents over seats 2..players: each seat's family and, for a
-- bot with a stop knob, its "family:stop" profile too (sc_bot_profile), so a
-- filter on `knower` finds every knower seat and one on `knower:3` that stop
-- alone (format.mjs's botChips shows the profiles). ai_strategy and ai are
-- indexed from seat 1 (index 0 is the human), trailing default entries are
-- trimmed, and a key is absent when every seat is default, so a missing,
-- trimmed or blank strategy reads as 'heuristic'. A random seat reads as
-- 'random': which bot it was dealt is in its log, not its setup. The client
-- reads this column rather than re-deriving it.
create or replace function public.sc_bots(settings jsonb, p_players integer)
returns text[]
language sql immutable strict parallel safe as $$
  select coalesce(array_agg(distinct bot order by bot), array[]::text[])
  from (
    select unnest(case when p like '%:%' then array[split_part(p, ':', 1), p]
                       else array[p] end) as bot
    from (
      select public.sc_bot_profile(
               coalesce(nullif(trim(settings #>> array['ai_strategy', (s.seat - 1)::text]), ''),
                        'heuristic'),
               settings #>> array['ai', (s.seat - 1)::text, 'aux']) as p
      from generate_series(2, greatest(p_players, 1)) as s(seat)
    ) seats
  ) q;
$$;

-- ---------------------------------------------------------------------------
-- configs: an optional name for a config_key. Append-only, so the first name
-- posted is permanent (tools/admin.py can remove it). No foreign key to games:
-- config_key is derived, so a config can be named before anyone plays it.
-- `tags` is the old way of tagging, still read by config_tag_counts; new tags go
-- to config_tags.
-- ---------------------------------------------------------------------------
create table if not exists public.configs (
  config_key text primary key check (config_key ~ '^[0-9a-f]{16}$'),
  name       text not null check (char_length(trim(name)) between 1 and 40),
  tags       text[] not null default '{}',
  by_name    text not null default '',
  created_at timestamptz not null default now()
);

alter table public.configs drop constraint if exists configs_tags_shape;
alter table public.configs add  constraint configs_tags_shape check (
  coalesce(array_length(tags, 1), 0) <= 6
  -- A nested array (`"tags": [["a","b"]]`) would pass a length check and then
  -- flatten under array_to_string.
  and coalesce(array_ndims(tags), 1) = 1
  -- A NULL element passes `'' = any(tags)` and vanishes from array_to_string.
  -- array_position can search for NULL; `= any` cannot.
  and array_position(tags, null::text) is null
  and not ('' = any(tags))
  and char_length(array_to_string(tags, ',')) <= 100
  and array_to_string(tags, ',') ~ '^[a-z0-9 ,+-]*$'
);

-- ---------------------------------------------------------------------------
-- config_tags: one row per (score, tag). Only someone who posted a score can
-- tag: score_id must name a real score. user_id lets config_tag_counts count
-- distinct players. Readers use tag_key, the normalised form; `tag` exists only
-- to generate it. No foreign key to configs, as with games.
-- ---------------------------------------------------------------------------
create table if not exists public.config_tags (
  id         bigint generated always as identity primary key,
  config_key text not null check (config_key ~ '^[0-9a-f]{16}$'),
  score_id   bigint not null references public.scores(id) on delete cascade,
  user_id    bigint not null references public.users(id),
  tag        text not null,
  tag_key    text generated always as (lower(trim(tag))) stored,
  created_at timestamptz not null default now(),
  constraint config_tags_unique unique (score_id, tag_key)
);

-- One tag per row, so no comma in the charset.
alter table public.config_tags drop constraint if exists config_tags_tag_shape;
alter table public.config_tags add  constraint config_tags_tag_shape check (
  tag_key ~ '^[a-z0-9 +-]{1,24}$'
);

create index if not exists config_tags_config_key_idx on public.config_tags (config_key);

-- ---------------------------------------------------------------------------
-- bot_scores: how each models/ bot fares in the human's seat on a map, one row
-- per (map, bot), written by tools/bot_replay.py. A rerun replaces the row. The
-- only writer is the worker's secret key: read policy, no insert policy, no
-- insert grant.
--
-- `won` says whether the bot took the map, not `turns`: a loss still reports how
-- long it lasted. Never rank a loss against a win on turns.
-- ---------------------------------------------------------------------------
create table if not exists public.bot_scores (
  game_key     text not null references public.games(game_key),
  bot          text not null check (char_length(trim(bot)) between 1 and 60),
  won          boolean not null,
  turns        integer not null check (turns > 0),
  lost         integer not null check (lost >= 0),
  -- Turns whose orders were forfeited for blowing the wall-clock budget. Nonzero
  -- means the result depended on the runner's speed, and the page says so.
  bot_timeouts integer not null default 0 check (bot_timeouts >= 0),
  -- The AiParams.aux the bot was replayed at (bot_replay.replay_aux) and the
  -- strategy's name for that setting. aux_label is empty for a bot at its
  -- default or one that ignores aux.
  aux          real not null default 1.0,
  aux_label    text not null default '',
  -- bot_replay.engine_rev of the code that produced the row. What `--stale`
  -- compares; never part of the key.
  engine_rev   text not null default '',
  computed_at  timestamptz not null default now(),
  -- match_id/log/rules_version: the replay behind a winning row, which its
  -- Watch link plays back. All blank on a loss, never NULL, so `match_id <> ''`
  -- is the filter everywhere.
  match_id      text not null default '' check (match_id = '' or match_id ~ '^[0-9a-f]{16}$'),
  -- engine.RULES_VERSION of the stored log; 0 means no log.
  rules_version integer not null default 0 check (rules_version >= 0),
  log           text not null default '' check (octet_length(log) <= 262144),
  primary key (game_key, bot)
);

alter table public.bot_scores add column if not exists match_id text not null default '';
alter table public.bot_scores add column if not exists rules_version integer not null default 0;
alter table public.bot_scores add column if not exists log text not null default '';

-- The lookup netlify/functions/replay.mjs makes through public_watchable_replays.
create index if not exists bot_scores_match_idx
  on public.bot_scores (match_id) where match_id <> '';

-- ---------------------------------------------------------------------------
-- campaigns: the weekly meta-map (campaign.html, js/campaign.mjs). One row per
-- week, written once by tools/campaign.py; tools/admin.py delete-campaign
-- removes one so the worker makes it again. Public read; the worker's secret key
-- is the only writer.
--
-- graph = {nodes: [{id, kind: 'home'|'field', x, y, systems, mystery,
-- settings}], lanes: [[a, b], ...]}. A node's `settings` has the pruned shape of
-- games.settings_json but is written by Python (`0.0` where the browser writes
-- `0`), so match it by jsonb value. Who holds what is not stored:
-- js/campaign.mjs folds it from the week's scores.
-- ---------------------------------------------------------------------------
create table if not exists public.campaigns (
  week_start date primary key,
  graph      jsonb not null,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- campaign_games: the games row each campaign node has, once somebody has posted
-- on it. Matched by seed and jsonb equality, never sc_config_key (see campaigns).
-- ---------------------------------------------------------------------------
create or replace view public.campaign_games
  with (security_invoker = true) as
select
  c.week_start,
  (node ->> 'id')::integer as node_id,
  node ->> 'kind' as kind,
  g.game_key
from public.campaigns c
cross join lateral jsonb_array_elements(c.graph -> 'nodes') node
join public.games g
  on g.seed = (node -> 'settings' ->> 'seed')::integer
 and (g.settings_json - array['seed', 'autoplay']) = ((node -> 'settings') - array['seed', 'autoplay']);

-- ---------------------------------------------------------------------------
-- game_embargoes: the embargo in force on each map: the later of
-- games.embargo_until and the end of a live campaign week the map is a node of
-- (Monday 00:00 UTC, as campaign_scores counts). Derived, because a node's games
-- row does not exist when its week is written. Only live weeks are read. Read by
-- public_replays and game_summary.
-- ---------------------------------------------------------------------------
create or replace view public.game_embargoes
  with (security_invoker = true) as
select
  g.game_key,
  greatest(g.embargo_until, camp.ends_at) as embargo_until
from public.games g
left join lateral (
  select max((cg.week_start + 7)::timestamp at time zone 'UTC') as ends_at
  from public.campaign_games cg
  where cg.game_key = g.game_key
    and (cg.week_start + 7)::timestamp at time zone 'UTC' > now()
) camp on true;

-- ---------------------------------------------------------------------------
-- public_replays: the only game_logs rows a visitor can read. A replay is public
-- when a posted score points at it and its map is not embargoed. One upload per
-- match: the log the match's first score was checked on; before any check, the
-- longest uploaded before that score was posted; else the longest. Anyone can
-- upload under a public match_id, so the longest alone could be anybody's.
--
-- Deliberately NOT security_invoker: it lends out part of a table nobody may
-- read, which needs owner rights. The where clause is the whole consent
-- boundary.
--
-- The embargo joins through scores.game_key, not game_logs.game_key: a log
-- carries the key the game held at upload, which the site may have folded onto
-- another (submit.mjs findTwin), while the score's key is a real games row. A
-- left join, so a key with no games row reads as no embargo.
--
-- rules_version is last because it was added last (see the top of the file).
-- game.mjs's watchableIds swallows the error a stale view gives, so a refused
-- replace takes down every Watch link quietly.
-- ---------------------------------------------------------------------------
create or replace view public.public_replays as
select distinct on (l.match_id)
  l.match_id, l.game_key, l.turns, l.finished, l.won, l.hand, l.log, l.rules_version
from public.game_logs l
join lateral (
  select s.game_key, s.submitted_at, c.log_id
  from public.scores s
  left join public.score_checks c on c.score_id = s.id
  where s.match_id = l.match_id
  order by s.submitted_at, s.id
  limit 1
) s on true
left join public.game_embargoes e on e.game_key = s.game_key
where e.embargo_until is null or e.embargo_until <= now()
order by l.match_id, (l.id = s.log_id) desc nulls last,
  (l.submitted_at <= s.submitted_at) desc, l.turns desc, l.id desc;

-- ---------------------------------------------------------------------------
-- public_watchable_replays: everything netlify/functions/replay.mjs may serve,
-- which reads only this. public_replays, plus each winning bot's stored log
-- (bot_scores is already public, so it needs no further gate). Granted to
-- service_role only.
-- ---------------------------------------------------------------------------
create or replace view public.public_watchable_replays as
select
  match_id, game_key, turns, finished, won, hand, log, rules_version
from public.public_replays
union all
select
  match_id, game_key, turns, true as finished, won, 0 as hand, log, rules_version
from public.bot_scores
where match_id <> '';

-- ---------------------------------------------------------------------------
-- config_tag_counts: each config's tags by how many distinct players entered
-- them, plus the old configs.tags at a weight of 1 each. security_invoker, so
-- callers of game_summary/config_summary need a grant on it too.
-- ---------------------------------------------------------------------------
create or replace view public.config_tag_counts
  with (security_invoker = true) as
select config_key, tag, sum(uses)::integer as uses
from (
  select config_key, tag_key as tag, count(distinct user_id) as uses
  from public.config_tags
  group by config_key, tag_key
  union all
  select config_key, unnest(tags) as tag, 1 as uses
  from public.configs
  where array_length(tags, 1) > 0
) combined
group by config_key, tag;

-- ---------------------------------------------------------------------------
-- counted_scores: the one place the crowns' eligibility rule lives. A score
-- counts once its replay is `verified`, outdated or not. One that names a
-- replay and is still unchecked or `missing` counts for its first 24 hours,
-- the worker's slack. One posted before sc_legacy_cutoff() counts under the
-- rule it was posted under: anything but a mismatch under current rules.
-- Only the first score posted for a match_id counts, and only the first for a
-- game (score_checks.game_digest), since a public replay can be uploaded again
-- under a fresh match_id. Nothing is stored, so deleting a score recomputes
-- everything that reads this.
-- ---------------------------------------------------------------------------
create or replace function public.sc_legacy_cutoff()
returns timestamptz
language sql immutable parallel safe as $$
  select timestamptz '2026-10-15 00:00Z'
$$;

create or replace view public.counted_scores
  with (security_invoker = true) as
select s.id, s.game_key, s.user_id, u.name as user_name, s.turns, s.lost, s.submitted_at,
  -- New columns go last.
  s.hand,
  s.campaign_start,
  s.by_name
from public.scores s
join public.users u on u.id = s.user_id
left join public.score_checks c on c.score_id = s.id
where (c.verdict = 'verified'
       or (coalesce(c.verdict, 'missing') = 'missing'
           and s.match_id <> ''
           and s.submitted_at > now() - interval '24 hours')
       or (s.submitted_at < public.sc_legacy_cutoff()
           and not coalesce(c.verdict = 'mismatch' and not c.outdated, false)))
  and not exists (
    select 1 from public.scores e
    where s.match_id <> '' and e.match_id = s.match_id
      and (e.submitted_at, e.id) < (s.submitted_at, s.id))
  and not exists (
    select 1 from public.scores e
    join public.score_checks ec on ec.score_id = e.id
    where c.game_digest <> '' and ec.game_digest = c.game_digest
      and (e.submitted_at, e.id) < (s.submitted_at, s.id));

-- ---------------------------------------------------------------------------
-- game_summary: the home page in one select: every game with its best score,
-- last activity, best winning bot, tags, embargo and filter columns.
-- ---------------------------------------------------------------------------
create or replace view public.game_summary
  with (security_invoker = true) as
select
  g.game_key,
  g.mode,
  g.players,
  g.nodes,
  g.seed,
  g.first_seen_at,
  (select max(s.submitted_at) from public.scores s where s.game_key = g.game_key) as last_activity,
  (select count(*) from public.scores s where s.game_key = g.game_key) as score_count,
  best.turns     as best_turns,
  best.lost      as best_lost,
  best.hand      as best_hand,
  best.by_name   as best_by_name,
  best.user_name as best_user_name,
  -- New columns go last.
  tied.holders   as best_holders,
  g.settings_json,
  public.sc_config_key(g.settings_json)      as config_key,
  public.sc_bots(g.settings_json, g.players) as bots,
  cfg.name                                   as config_name,
  coalesce(tagc.tags, '{}'::text[])          as config_tags,
  -- The best winning bot_scores row, ordered as `best` is. Null when no bot has
  -- taken the map (standings.mjs's bestBot).
  bot.turns as bot_turns,
  bot.lost  as bot_lost,
  bot.bot   as bot_name,
  -- The embargo in force, from game_embargoes.
  e.embargo_until,
  -- The home page's filters, computed here because PostgREST can filter on a
  -- column but not on a comparison between two of them.
  -- contenders: distinct players with a counted score; `>= 2` is exactly the
  -- set crown_holders has a row for.
  coalesce(field.contenders, 0) as contenders,
  -- bot_leads: a winning bot exists and no human score beats it, a tie included
  -- (format.mjs's botLeadBadge: "Bot leads", "Bot tied").
  (bot.turns is not null
    and (best.turns is null or (bot.turns, bot.lost) <= (best.turns, best.lost))) as bot_leads,
  -- fog: settings_json holds non-defaults only and fog defaults off, so either
  -- key being present means fog is on (setup.mjs's hasFog).
  (g.settings_json ?| array['fog_sight', 'fog_scout']) as fog,
  -- difficulty: the share of replayed bots that did not take the map
  -- (format.mjs's difficultyBadge). A share, so a map not yet reached by a new
  -- bot still ranks. Null before any bot has run.
  runs.bot_wins,
  runs.bot_runs,
  case when runs.bot_runs > 0
    then round(1 - runs.bot_wins::numeric / runs.bot_runs, 3) end as difficulty
from public.games g
join public.game_embargoes e on e.game_key = g.game_key
left join lateral (
  -- The best counted score: one that doesn't count for a crown isn't the record
  -- either. Null when a map has scores but none counts.
  select s.turns, s.lost, s.hand, s.by_name, s.user_name
  from public.counted_scores s
  where s.game_key = g.game_key
  order by s.turns asc, s.lost asc, s.submitted_at asc
  limit 1
) best on true
left join lateral (
  -- Counted scores tied with the best on turns and lost: 2+ is a shared record.
  select count(*) as holders
  from public.counted_scores s
  where s.game_key = g.game_key and s.turns = best.turns and s.lost = best.lost
) tied on true
left join lateral (
  select bs.turns, bs.lost, bs.bot
  from public.bot_scores bs
  where bs.game_key = g.game_key and bs.won
  order by bs.turns asc, bs.lost asc
  limit 1
) bot on true
left join lateral (
  select count(*) filter (where bs.won)::integer as bot_wins, count(*)::integer as bot_runs
  from public.bot_scores bs
  where bs.game_key = g.game_key
) runs on true
left join public.configs cfg
  on cfg.config_key = public.sc_config_key(g.settings_json)
left join lateral (
  -- Top 6 by uses, the cap configs_tags_shape puts on the old array.
  select array_agg(t.tag order by t.uses desc, t.tag asc) as tags
  from (
    select tag, uses
    from public.config_tag_counts
    where config_key = public.sc_config_key(g.settings_json)
    order by uses desc, tag asc
    limit 6
  ) t
) tagc on true
left join lateral (
  select count(distinct cs.user_id)::integer as contenders
  from public.counted_scores cs
  where cs.game_key = g.game_key
) field on true;

-- ---------------------------------------------------------------------------
-- config_summary: one row per config_key, for the main list's "By config" view.
-- `agg` totals the group; `rep` takes the setup's own fields from its most
-- recently active game. bots goes through sc_bots on rep.settings_json:
-- array_agg over game_summary.bots would build a matrix.
-- ---------------------------------------------------------------------------
create or replace view public.config_summary
  with (security_invoker = true) as
with agg as (
  select
    config_key,
    count(*)::integer         as game_count,
    sum(score_count)::integer as score_count,
    max(last_activity)        as last_activity,
    -- The "Avg best turns" sort: each map's best, averaged over the group.
    round(avg(best_turns), 1) as avg_best_turns,
    -- The "Difficulty" sort: each map's estimate, averaged the same way.
    round(avg(difficulty), 3) as avg_difficulty,
    -- When the setup was first played: orders "By family" (js/families.mjs),
    -- where a config's parent is always an older config.
    min(first_seen_at)        as first_seen
  from public.game_summary
  where score_count > 0
  group by config_key
),
rep as (
  select distinct on (config_key)
    config_key, settings_json, mode, players, nodes
  from public.game_summary
  where score_count > 0
  order by config_key, last_activity desc nulls last
)
select
  agg.config_key,
  rep.settings_json,
  rep.mode,
  rep.players,
  rep.nodes,
  public.sc_bots(rep.settings_json, rep.players) as bots,
  cfg.name as config_name,
  coalesce(tagc.tags, '{}'::text[]) as config_tags,
  agg.game_count,
  agg.score_count,
  agg.last_activity,
  -- New columns go last. fog is part of sc_config_key, so the group shares it.
  (rep.settings_json ?| array['fog_sight', 'fog_scout']) as fog,
  agg.avg_best_turns,
  agg.avg_difficulty,
  agg.first_seen
from agg
join rep on rep.config_key = agg.config_key
left join public.configs cfg on cfg.config_key = agg.config_key
left join lateral (
  select array_agg(t.tag order by t.uses desc, t.tag asc) as tags
  from (
    select tag, uses
    from public.config_tag_counts
    where config_key = agg.config_key
    order by uses desc, tag asc
    limit 6
  ) t
) tagc on true;

-- ---------------------------------------------------------------------------
-- Crowns and steals (crowns.html, js/crowns.mjs), both over counted_scores.
--
-- crown_holders: one row per contested map (counted scores from two or more
-- players), naming its record holder. Ordered as game_summary.best is, so a tie
-- never takes a crown: the first to reach a result keeps it.
-- ---------------------------------------------------------------------------
create or replace view public.crown_holders
  with (security_invoker = true) as
select
  field.game_key,
  best.user_id,
  best.user_name,
  best.turns,
  (field.players - 1)::integer as rivals
from (
  select game_key, count(distinct user_id) as players
  from public.counted_scores
  group by game_key
  having count(distinct user_id) >= 2
) field
cross join lateral (
  select cs.user_id, cs.user_name, cs.turns
  from public.counted_scores cs
  where cs.game_key = field.game_key
  order by cs.turns asc, cs.lost asc, cs.submitted_at asc, cs.id asc
  limit 1
) best;

-- ---------------------------------------------------------------------------
-- crown_steals: one row per counted score that strictly beat the record it found
-- on arrival, where that record belonged to somebody else. Beating your own
-- record, tying one, and the first score on an empty map are not steals. Names
-- and turns only, which game_summary already shows on an embargoed map.
-- ---------------------------------------------------------------------------
create or replace view public.crown_steals
  with (security_invoker = true) as
select
  s.id as score_id,
  s.game_key,
  s.submitted_at,
  s.user_name  as taker_name,
  prior.user_name as from_name,
  s.turns,
  prior.turns  as from_turns
from public.counted_scores s
cross join lateral (
  select p.user_id, p.user_name, p.turns, p.lost
  from public.counted_scores p
  where p.game_key = s.game_key
    and (p.submitted_at, p.id) < (s.submitted_at, s.id)
  order by p.turns asc, p.lost asc, p.submitted_at asc, p.id asc
  limit 1
) prior
where prior.user_id <> s.user_id
  and (s.turns, s.lost) < (prior.turns, prior.lost);

-- ---------------------------------------------------------------------------
-- campaign_scores: every counted score posted during a campaign's week on one
-- of its nodes, with at least one turn played by hand. A score still in its
-- 24 hours unverified drops out if the worker never verifies it, and the
-- campaign's fold changes with it.
-- ---------------------------------------------------------------------------
create or replace view public.campaign_scores
  with (security_invoker = true) as
select
  cg.week_start,
  cg.node_id,
  cs.id as score_id,
  cs.user_id,
  cs.user_name,
  cs.turns,
  cs.lost,
  cs.submitted_at,
  -- What the campaign said when the game began, for fold's grace.
  cs.campaign_start
from public.campaign_games cg
join public.counted_scores cs on cs.game_key = cg.game_key
where cs.hand > 0
  and cs.submitted_at >= cg.week_start::timestamp at time zone 'UTC'
  and cs.submitted_at <  (cg.week_start + 7)::timestamp at time zone 'UTC';

-- ---------------------------------------------------------------------------
-- Policies. No update or delete policy anywhere: that is what makes every row
-- append-only. game_logs has no policy at all, so the public can do nothing
-- with it.
-- ---------------------------------------------------------------------------
alter table public.users   enable row level security;
alter table public.games   enable row level security;
alter table public.scores  enable row level security;
alter table public.configs enable row level security;
alter table public.config_tags enable row level security;
alter table public.bot_scores enable row level security;
alter table public.game_logs enable row level security;
alter table public.score_checks enable row level security;
alter table public.campaigns enable row level security;
alter table public.playstyle_readings enable row level security;

drop policy if exists "users public read"    on public.users;
drop policy if exists "users public insert"  on public.users;
drop policy if exists "games public read"    on public.games;
drop policy if exists "games public insert"  on public.games;
drop policy if exists "scores public read"   on public.scores;
drop policy if exists "scores public insert" on public.scores;
drop policy if exists "configs public read"   on public.configs;
drop policy if exists "configs public insert" on public.configs;
drop policy if exists "config_tags public read"   on public.config_tags;
drop policy if exists "config_tags public insert" on public.config_tags;
drop policy if exists "bot_scores public read" on public.bot_scores;
drop policy if exists "game_logs public insert" on public.game_logs;   -- superseded by the function
drop policy if exists "score_checks public read" on public.score_checks;
drop policy if exists "campaigns public read" on public.campaigns;
drop policy if exists "playstyle_readings public read" on public.playstyle_readings;

create policy "users public read"    on public.users  for select using (true);
create policy "users public insert"  on public.users  for insert with check (true);
create policy "games public read"    on public.games  for select using (true);
create policy "games public insert"  on public.games  for insert with check (true);
create policy "scores public read"   on public.scores for select using (true);
create policy "scores public insert" on public.scores for insert with check (true);
create policy "configs public read"   on public.configs for select using (true);
create policy "configs public insert" on public.configs for insert with check (true);
create policy "config_tags public read"   on public.config_tags for select using (true);
create policy "config_tags public insert" on public.config_tags for insert with check (true);

-- Read-only: written by the workers under the secret key, which bypasses RLS.
create policy "bot_scores public read" on public.bot_scores for select using (true);
create policy "score_checks public read" on public.score_checks for select using (true);
create policy "campaigns public read" on public.campaigns for select using (true);
create policy "playstyle_readings public read" on public.playstyle_readings
  for select using (true);

-- ---------------------------------------------------------------------------
-- Grants to the public. Explicit, so this file is the whole story. Identity
-- columns need no sequence grant.
--
-- The security_invoker views check the caller's privileges on everything they
-- read, so each relation behind them needs its own grant: a missing one on
-- configs fails the whole home page query, not just its config_name.
--
-- game_logs is in neither list: the public can neither read nor write it.
-- bot_scores is read-only: the workers are its only writers.
-- ---------------------------------------------------------------------------
grant select on public.users, public.games, public.scores, public.configs,
  public.config_tags, public.game_summary, public.config_summary,
  public.bot_scores, public.score_checks, public.public_replays,
  public.counted_scores, public.crown_holders, public.crown_steals,
  public.campaigns, public.campaign_games, public.campaign_scores,
  public.game_embargoes, public.playstyle_readings
  to anon, authenticated;
grant select on public.config_tag_counts to anon, authenticated;
grant insert on public.users, public.games, public.configs,
  public.config_tags to anon, authenticated;
-- scores by column, leaving submitted_at to its default: counted_scores and
-- public_replays trust it. Revoking the table grant also drops column grants.
revoke insert on public.scores from anon, authenticated;
grant insert (game_key, user_id, turns, lost, hand, by_name, raw_token, match_id,
  campaign_start) on public.scores to anon, authenticated;
grant execute on function public.sc_config_key(jsonb), public.sc_bots(jsonb, integer),
  public.sc_bot_profile(text, text) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- Grants to service_role, the secret key the workers and functions hold. It
-- bypasses RLS but not grants: every relation a caller touches needs one here,
-- views and read-only lookups included. A missing grant fails the query for the
-- worker alone while the site still works. tests/test_schema_grants.py checks
-- the callers against these lines; add each grant beside the caller it is for.
-- ---------------------------------------------------------------------------
-- tools/bot_replay.py: reads the games it replays and the rows it has, and
-- upserts (an INSERT ... ON CONFLICT DO UPDATE).
grant select on public.games, public.bot_scores to service_role;
grant insert, update on public.bot_scores to service_role;
-- tools/verify_scores.py: reads scores and which already have a verdict, writes
-- verdicts.
grant select on public.scores to service_role;
grant select, insert, update on public.score_checks to service_role;
-- tools/campaign.py writes each week's map; tools/admin.py delete-campaign
-- removes one. It counts last week's players off counted_scores.
grant select, insert, delete on public.campaigns to service_role;
grant select on public.counted_scores to service_role;
-- The workers read replays and prune superseded ones; netlify/functions/log.mjs
-- inserts.
grant select, insert, delete on public.game_logs to service_role;
-- netlify/functions/replay.mjs.
grant select on public.public_watchable_replays to service_role;
-- ...and what that view reaches. Owner rights stop at the first security_invoker
-- view in a chain: game_embargoes reads campaign_games as the caller.
-- game_embargoes is listed against the day public_replays turns security_invoker.
grant select on public.game_embargoes, public.campaign_games to service_role;
-- tools/playstyle_worker.py: reads public replays, writes one reading per match,
-- and prunes a reading whose replay is no longer public.
grant select on public.public_replays to service_role;
grant select, insert, update, delete on public.playstyle_readings to service_role;

-- ---------------------------------------------------------------------------
-- Play-by-post: pbp_matches, pbp_orders (docs/design/pbp.md).
--
-- Kept apart from the board: a match is live and advances, so these are the
-- only tables with UPDATEs. The server stores inputs (settings, seed, orders,
-- the log) and holds no board; clients rebuild it as a replay does.
--
-- Function-only, like game_logs: no policies and no anon grants. Every read and
-- write goes through netlify/functions/pbp.mjs under the secret key, the only
-- place a seat token can be checked. Seat tokens are random per match per seat
-- and stored only as hashes.
-- ---------------------------------------------------------------------------
create table if not exists public.pbp_matches (
  match_id      text primary key check (match_id ~ '^[0-9a-f]{16}$'),
  -- The setup and seed every client rebuilds from. Never change either on a
  -- match in progress: it would silently re-map the game.
  settings_json jsonb not null,
  seed          integer not null,
  rules_version integer not null default 1 check (rules_version >= 1),
  -- The full roster of person seats (a bot seat is absent), with each claimed
  -- seat's token hash.
  seats         jsonb not null,
  -- The live turn. Bumped only when the last outstanding seat submits.
  turn          integer not null default 0 check (turn >= 0),
  -- The match so far, in replay.GameLog.encoded form like game_logs. Rewritten
  -- each time a turn resolves.
  log           text not null default '' check (octet_length(log) <= 262144),
  -- When the live turn opened, for the deadline. A null deadline_hours means no
  -- deadline.
  turn_opened_at timestamptz not null default now(),
  deadline_hours integer check (deadline_hours is null or deadline_hours between 1 and 336),
  finished      boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- pbp_orders: one seat's submission for one turn, unique per (match, turn,
-- seat), so a resubmission is a conflict pbp.mjs decides. Orders are stored as
-- submitted; engine.apply_order judges them. board_digest is the board the
-- resolving client landed on: a tripwire for drift, not anti-cheat.
-- ---------------------------------------------------------------------------
create table if not exists public.pbp_orders (
  id           bigint generated always as identity primary key,
  match_id     text not null references public.pbp_matches(match_id) on delete cascade,
  turn         integer not null check (turn >= 0),
  seat         integer not null check (seat between 1 and 6),
  -- `[{"src": 3, "dst": 7, "ships": 12}, ...]`. No owner: the seat comes from
  -- the token.
  orders_json  jsonb not null,
  -- A person, a seat that missed its deadline (`hold`), or its bot after a
  -- second miss (`bot`).
  source       text not null default 'human' check (source in ('human', 'hold', 'bot')),
  board_digest text not null default '' check (char_length(board_digest) <= 64),
  submitted_at timestamptz not null default now(),
  constraint pbp_orders_one_per_seat_per_turn unique (match_id, turn, seat)
);

create index if not exists pbp_orders_turn_idx
  on public.pbp_orders (match_id, turn);

-- Public matches are listed on the lobby (pbp.html, `?action=list`) and joined
-- from it (`?action=claim`); nothing lists a private one. An open seat is one
-- with no entry in `seats.tokens`.
alter table public.pbp_matches
  add column if not exists public boolean not null default false;

create index if not exists pbp_matches_public_idx
  on public.pbp_matches (updated_at desc) where public;

-- Self-declared display text for the lobby, never read for logic: `title` from
-- the creator, `names` as `{seat: name}` from each seat's holder, `winner` the
-- seat the final resolve reports (0 is a draw by elimination).
alter table public.pbp_matches
  add column if not exists title text not null default ''
    check (char_length(title) <= 60),
  add column if not exists names jsonb not null default '{}'::jsonb
    check (jsonb_typeof(names) = 'object' and octet_length(names::text) <= 1024),
  add column if not exists winner integer
    check (winner is null or winner between 0 and 6);

-- ONE-OFF, testing only: list the matches that predate the flag. Run once when
-- adding the column above, never again -- re-running it would publish every
-- private match since.
-- update public.pbp_matches set public = true;

alter table public.pbp_matches enable row level security;
alter table public.pbp_orders enable row level security;

-- pbp.mjs's grants. The only update to pbp_orders is board_digest, stamped on
-- the resolving seat's own row after the turn is played.
grant select, insert, update on public.pbp_matches to service_role;
grant select, insert, update on public.pbp_orders to service_role;

-- ---------------------------------------------------------------------------
-- gauntlets: a gauntlet someone built on the board's gauntlet page
-- (gauntlet.html), stored so the link that opens it is a short id rather than
-- the whole file. body is the file starconquest/gauntlet.py reads
-- (Gauntlet.from_dict), its `id` equal to gauntlet_id. The id is a name slug
-- plus a hash of the body, minted by netlify/functions/gauntlet.mjs, the only
-- writer: saving the same gauntlet twice is a no-op and an edited one is a new
-- row, so a row never changes. Never progress: clears stay in each player's own
-- bests. An unlisted row is read only through the function, by id (knowing the
-- id is the permission, as with a play-by-post match); `listed` is set only by
-- tools/admin.py, at the author's request (wants_listing), and only a listed
-- row is readable with the publishable key.
-- ---------------------------------------------------------------------------
create table if not exists public.gauntlets (
  gauntlet_id   text primary key check (gauntlet_id ~ '^[a-z0-9-]{1,19}-[0-9a-f]{12}$'),
  -- The function holds a body to 64 KiB of compact JSON; jsonb's text form
  -- spaces every ':' and ',', so this backstop is looser than that, not equal.
  body          jsonb not null
    check (jsonb_typeof(body) = 'object' and octet_length(body::text) <= 131072),
  by_name       text not null default '' check (char_length(by_name) <= 32),
  wants_listing boolean not null default false,
  listed        boolean not null default false,
  created_at    timestamptz not null default now()
);

alter table public.gauntlets enable row level security;
drop policy if exists "gauntlets listed read" on public.gauntlets;
create policy "gauntlets listed read" on public.gauntlets for select using (listed);
grant select on public.gauntlets to anon, authenticated;

-- gauntlet.mjs's grants: it reads a row by id and inserts a new one.
grant select, insert on public.gauntlets to service_role;

-- ---------------------------------------------------------------------------
-- Moderation: tools/admin.py, under the secret key. admin_actions logs each
-- command before it runs, with the rows it is about to remove or overwrite.
-- Closed like game_logs.
-- ---------------------------------------------------------------------------
create table if not exists public.admin_actions (
  id       bigint generated always as identity primary key,
  action   text not null check (char_length(action) between 1 and 40),
  target   text not null check (char_length(target) between 1 and 200),
  reason   text not null default '',
  detail   jsonb not null default '{}'::jsonb,
  acted_at timestamptz not null default now()
);

alter table public.admin_actions enable row level security;
grant select, insert on public.admin_actions to service_role;

-- What the moderation commands read and change. Deleting a score cascades to
-- its config_tags and score_checks rows.
grant select, update on public.users to service_role;
grant select, update, delete on public.configs to service_role;
grant select, delete on public.config_tags to service_role;
grant delete on public.scores, public.games, public.bot_scores to service_role;
-- A seat's token rewritten or forgotten, or a whole match removed.
grant delete on public.pbp_matches, public.pbp_orders to service_role;
-- A gauntlet listed, unlisted or removed.
grant update, delete on public.gauntlets to service_role;

-- PostgREST does not see new relations until it reloads its schema cache.
-- Supabase's DDL triggers usually do this already; idempotent either way.
notify pgrst, 'reload schema';
