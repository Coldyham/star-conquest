-- Star Conquest leaderboard schema. Paste into the Supabase SQL editor once.
--
-- Trust model: no auth, no accounts. Anyone may read everything and insert a
-- user/game/score; nobody may update or delete anything. Scores are append-only
-- and "the best score" is a query, never a row that gets overwritten.
--
-- With RLS enabled, a command with no policy is refused outright — so the absence
-- of UPDATE/DELETE policies below is the mechanism, not an omission. Fixing a bad
-- row means using this SQL editor, which runs as `postgres` and bypasses RLS.
--
-- Statement order matters below: the functions must come before the views that
-- call them, and `configs` before the view that joins it.

-- ---------------------------------------------------------------------------
-- users: keyed by name alone. name_key is generated so "Andrew", "andrew " and
-- "ANDREW" are one user without the client doing its own case folding.
-- ---------------------------------------------------------------------------
create table if not exists public.users (
  id         bigint generated always as identity primary key,
  name       text not null check (char_length(trim(name)) between 1 and 60),
  name_key   text generated always as (lower(trim(name))) stored,
  created_at timestamptz not null default now(),
  constraint users_name_key_unique unique (name_key)
);

-- ---------------------------------------------------------------------------
-- games: one row per distinct setup. game_key is Challenge.key out of the token
-- — the game's own blake2s checksum of the setup (Settings.challenge_key) — so
-- this groups scores exactly as the game itself decides two matches are the same.
-- Hand-written links carry no key and get a "j:"-prefixed hash computed in JS
-- instead (see js/token-decode.mjs).
--
-- The checks are loose sanity bounds on purpose: config.MIN_PLAYERS/MAX_NODES are
-- balance constants free to drift, and policing them is not this table's job.
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

-- ---------------------------------------------------------------------------
-- scores: one row per submission, never deduplicated. turns is the score (lower
-- is better), fewest lost breaks a tie — the same rule the game uses. hand is how
-- many turns the player actually decided; the rest were autoplayed, disclosed
-- rather than disqualifying.
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

-- ---------------------------------------------------------------------------
-- sc_config_key / sc_bots: derive a game's setup identity and opponent roster
-- straight from settings_json, so grouping by "same setup, different seed"
-- needs no new field on Settings and no change to Challenge.key (see
-- docs/design-notes.md, "Keys outlive the schema that made them" — a field
-- added there moves the digest of every map that ever existed; this is
-- deliberately the other kind of key, computed here and never stamped by the
-- game). Both are IMMUTABLE so they're index-friendly if the board ever needs
-- an expression index.
--
-- Changing either function's *body* rehashes every config and orphans every
-- name already in `configs` below — same for a changed game-side default,
-- since settings_json is pruned to non-defaults. There is no in-site remedy
-- for that, only the SQL editor. Changing sc_config_key's *signature* makes
-- `create or replace function` add an overload instead of replacing it,
-- leaving the views bound to the old one — a signature change is a hand-run
-- migration, not a re-paste.
--
-- Supabase's linter will flag both for a mutable search_path. False positive:
-- every identifier here resolves to pg_catalog, and pinning search_path would
-- make sc_config_key non-inlinable, which is what lets an expression index
-- match it.
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

-- The distinct opponent strategies over seats 2..players: ai_strategy is
-- positional and seat-1 indexed (index 0 is the human, per
-- Settings.seat_strategy), trailing "heuristic" entries are trimmed off, and
-- the key is absent entirely when every seat is default — so a missing,
-- trimmed or blank entry all read as 'heuristic'. This is the one place that
-- rule is implemented; the client reads this column rather than re-deriving it.
create or replace function public.sc_bots(settings jsonb, p_players integer)
returns text[]
language sql immutable strict parallel safe as $$
  select coalesce(array_agg(distinct bot order by bot), array[]::text[])
  from (
    select coalesce(
             nullif(trim(settings #>> array['ai_strategy', (s.seat - 1)::text]), ''),
             'heuristic') as bot
    from generate_series(2, greatest(p_players, 1)) as s(seat)
  ) q;
$$;

-- ---------------------------------------------------------------------------
-- configs: an optional name/tags for a config_key (see sc_config_key above),
-- append-only and keyed by the derived key so the first name posted for a
-- setup is permanent — a typo can only be fixed from this SQL editor, the same
-- trade `users.name` already makes. No FK to games: config_key is derived
-- rather than stored, so a config can be named before anyone plays it, and
-- naming one costs no write to games or scores.
-- ---------------------------------------------------------------------------
create table if not exists public.configs (
  config_key text primary key check (config_key ~ '^[0-9a-f]{16}$'),
  name       text not null check (char_length(trim(name)) between 1 and 40),
  tags       text[] not null default '{}',
  by_name    text not null default '',
  created_at timestamptz not null default now()
);

-- A drop/add pair rather than a check inside `create table if not exists`, so
-- re-pasting this file can still tighten the rule later.
alter table public.configs drop constraint if exists configs_tags_shape;
alter table public.configs add  constraint configs_tags_shape check (
  coalesce(array_length(tags, 1), 0) <= 6
  -- A nested array in the POST body (e.g. `"tags": [["a","b"]]`) would flatten
  -- under array_to_string while passing a naive length check; reject it here.
  and coalesce(array_ndims(tags), 1) = 1
  -- A NULL element passes `'' = any(tags)` (NULL, not false, so the check
  -- doesn't fail) and then vanishes from array_to_string — a phantom tag that
  -- renders as nothing. array_position is non-strict in its needle, so it can
  -- search for NULL directly.
  and array_position(tags, null::text) is null
  and not ('' = any(tags))
  and char_length(array_to_string(tags, ',')) <= 100
  and array_to_string(tags, ',') ~ '^[a-z0-9 ,+-]*$'
);

-- ---------------------------------------------------------------------------
-- game_summary: the homepage in one select — every game with its current best
-- score and last activity. security_invoker makes it evaluate RLS as the caller
-- rather than the owner, so a future tightened policy can't be bypassed here.
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
  -- New columns go last: `create or replace view` can add a column but never
  -- rename or reorder the ones already there.
  tied.holders   as best_holders,
  g.settings_json,
  public.sc_config_key(g.settings_json)      as config_key,
  public.sc_bots(g.settings_json, g.players) as bots,
  cfg.name                                   as config_name,
  cfg.tags                                   as config_tags
from public.games g
left join lateral (
  select s.turns, s.lost, s.hand, s.by_name, u.name as user_name
  from public.scores s
  join public.users u on u.id = s.user_id
  where s.game_key = g.game_key
  order by s.turns asc, s.lost asc, s.submitted_at asc
  limit 1
) best on true
left join lateral (
  -- How many submissions share that best result: a dead heat on turns *and*
  -- lost is a shared record, so 2+ here means the homepage credits them all.
  select count(*) as holders
  from public.scores s
  where s.game_key = g.game_key and s.turns = best.turns and s.lost = best.lost
) tied on true
left join public.configs cfg
  on cfg.config_key = public.sc_config_key(g.settings_json);

-- ---------------------------------------------------------------------------
-- config_summary: one row per config_key, for the main list's "by config"
-- grouping (each row rolls up every game on that setup, whatever its seed).
-- `agg` counts and dates across the whole group; `rep` picks the fields that
-- describe the setup itself (settings_json, mode, players, nodes) off
-- whichever of its games was most recently active, via DISTINCT ON rather than
-- an aggregate — those columns don't have a meaningful sum or max, and every
-- game in the group carries an equivalent value anyway except settings_json's
-- seed. bots is re-derived from that representative settings_json through
-- sc_bots rather than aggregated off game_summary.bots, since array_agg over
-- an already-array column would build a matrix, not a list of arrays.
-- ---------------------------------------------------------------------------
create or replace view public.config_summary
  with (security_invoker = true) as
with agg as (
  select
    config_key,
    count(*)::integer         as game_count,
    sum(score_count)::integer as score_count,
    max(last_activity)        as last_activity
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
  cfg.tags as config_tags,
  agg.game_count,
  agg.score_count,
  agg.last_activity
from agg
join rep on rep.config_key = agg.config_key
left join public.configs cfg on cfg.config_key = agg.config_key;

-- ---------------------------------------------------------------------------
-- Policies
-- ---------------------------------------------------------------------------
alter table public.users   enable row level security;
alter table public.games   enable row level security;
alter table public.scores  enable row level security;
alter table public.configs enable row level security;

drop policy if exists "users public read"    on public.users;
drop policy if exists "users public insert"  on public.users;
drop policy if exists "games public read"    on public.games;
drop policy if exists "games public insert"  on public.games;
drop policy if exists "scores public read"   on public.scores;
drop policy if exists "scores public insert" on public.scores;
drop policy if exists "configs public read"   on public.configs;
drop policy if exists "configs public insert" on public.configs;

create policy "users public read"    on public.users  for select using (true);
create policy "users public insert"  on public.users  for insert with check (true);
create policy "games public read"    on public.games  for select using (true);
create policy "games public insert"  on public.games  for insert with check (true);
create policy "scores public read"   on public.scores for select using (true);
create policy "scores public insert" on public.scores for insert with check (true);
create policy "configs public read"   on public.configs for select using (true);
create policy "configs public insert" on public.configs for insert with check (true);

-- No update or delete policy anywhere: that is what makes every row append-only
-- — for configs, that's what makes the first name posted for a setup permanent.
--
-- Because game_summary is security_invoker, it reads `configs` as the caller:
-- a missing read policy or grant on configs fails the *whole* homepage query
-- with "permission denied for table configs", not just a blank config_name.

-- Explicit rather than relying on the project's default privileges, so this file
-- is the whole story. Identity columns need no sequence grant (unlike serial).
grant select on public.users, public.games, public.scores, public.configs,
  public.game_summary, public.config_summary to anon, authenticated;
grant insert on public.users, public.games, public.scores, public.configs to anon, authenticated;
grant execute on function public.sc_config_key(jsonb), public.sc_bots(jsonb, integer)
  to anon, authenticated;

-- New relations aren't visible to PostgREST until it reloads its schema cache.
-- Supabase's DDL event triggers usually fire this already; idempotent either way.
notify pgrst, 'reload schema';
