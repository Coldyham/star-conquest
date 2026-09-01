-- Star Conquest leaderboard schema. Paste into the Supabase SQL editor once.
--
-- Trust model: no auth, no accounts. Anyone may read everything and insert a
-- user/game/score; nobody may update or delete anything. Scores are append-only
-- and "the best score" is a query, never a row that gets overwritten.
--
-- With RLS enabled, a command with no policy is refused outright — so the absence
-- of UPDATE/DELETE policies below is the mechanism, not an omission. Fixing a bad
-- row means using this SQL editor, which runs as `postgres` and bypasses RLS.

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
  best.user_name as best_user_name
from public.games g
left join lateral (
  select s.turns, s.lost, s.hand, s.by_name, u.name as user_name
  from public.scores s
  join public.users u on u.id = s.user_id
  where s.game_key = g.game_key
  order by s.turns asc, s.lost asc, s.submitted_at asc
  limit 1
) best on true;

-- ---------------------------------------------------------------------------
-- Policies
-- ---------------------------------------------------------------------------
alter table public.users  enable row level security;
alter table public.games  enable row level security;
alter table public.scores enable row level security;

drop policy if exists "users public read"    on public.users;
drop policy if exists "users public insert"  on public.users;
drop policy if exists "games public read"    on public.games;
drop policy if exists "games public insert"  on public.games;
drop policy if exists "scores public read"   on public.scores;
drop policy if exists "scores public insert" on public.scores;

create policy "users public read"    on public.users  for select using (true);
create policy "users public insert"  on public.users  for insert with check (true);
create policy "games public read"    on public.games  for select using (true);
create policy "games public insert"  on public.games  for insert with check (true);
create policy "scores public read"   on public.scores for select using (true);
create policy "scores public insert" on public.scores for insert with check (true);

-- No update or delete policy anywhere: that is what makes every row append-only.

-- Explicit rather than relying on the project's default privileges, so this file
-- is the whole story. Identity columns need no sequence grant (unlike serial).
grant select on public.users, public.games, public.scores, public.game_summary to anon, authenticated;
grant insert on public.users, public.games, public.scores to anon, authenticated;
