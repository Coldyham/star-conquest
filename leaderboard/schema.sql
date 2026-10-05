-- Star Conquest leaderboard schema. Paste into the Supabase SQL editor once.
--
-- Trust model: no accounts needed. Anyone may read everything and insert a
-- user/game/score; the public may not update or delete anything. The one
-- exception is opt-in: a person signed in with Google (Supabase Auth) may claim
-- one name nobody has used (`claim_name`), or the owner assigns them one that is
-- in use (`tools/admin.py assign-name`), and then only they post under it. Scores are
-- append-only and "the best score" is a query, never a row that gets overwritten.
--
-- With RLS enabled, a command with no policy is refused outright — so the absence
-- of UPDATE/DELETE policies below is the mechanism, not an omission. Removing or
-- correcting a bad row is the owner's job, through `tools/admin.py` under the
-- secret key, which logs every change it makes to `admin_actions` (end of file).
--
-- Statement order matters below: the functions must come before the views that
-- call them, and `configs`/`config_tags` before the views that join them
-- (`config_tag_counts` before `game_summary`/`config_summary` in turn, since
-- those two now read it rather than `configs.tags` directly).

-- ---------------------------------------------------------------------------
-- users: keyed by name (owner, below, says who may use a claimed one). name_key is generated so "Andrew", "andrew " and
-- "ANDREW" are one user without the client doing its own case folding.
-- ---------------------------------------------------------------------------
create table if not exists public.users (
  id         bigint generated always as identity primary key,
  name       text not null check (char_length(trim(name)) between 1 and 60),
  name_key   text generated always as (lower(trim(name))) stored,
  created_at timestamptz not null default now(),
  constraint users_name_key_unique unique (name_key)
);

-- A claimed name: owner is the Supabase Auth account that alone may post under
-- it (scores, tags, play-by-post seat names). Null for every name nobody has
-- claimed, which is every name that existed before claiming did, and those work
-- exactly as before. The uuid is never readable by the public (the column grant
-- below leaves it out); `claimed` is what pages read. One name per account
-- (users_owner_unique). Written only by `claim_name`/`release_name` and admin.py.
alter table public.users add column if not exists
  owner uuid references auth.users(id) on delete set null;
alter table public.users add column if not exists
  claimed boolean generated always as (owner is not null) stored;
create unique index if not exists users_owner_unique
  on public.users (owner) where owner is not null;

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
-- games.embargo_until: an optional reveal date, set only when the site itself
-- inserts a *new* row (js/submit.mjs's ensureGame) — never by an update, since
-- there is no update policy for it to use. That is what makes it permanent and
-- unappealable in either direction: nobody, the setter included, can move it
-- once the row exists, and nobody can attach one to a map already on the board
-- either (the whole point of "first insert wins" here is that an embargo has to
-- be decided before anyone has seen what it would hide).
--
-- It gates one thing only — a replay's visibility (`public_replays` below) —
-- never a score. It is not the whole story, either: a campaign node is
-- embargoed until its week ends without this column saying so, and readers
-- take the embargo in force from `game_embargoes` below, never from here. Scores and rankings post and rank normally throughout: a
-- friend's turn count tells you you're behind, not how they did it. That is a
-- deliberate, narrower promise than "hide the whole map": with no accounts to
-- check identity against, a stricter blackout could only be enforced by hiding
-- every reader equally, which would also hide a submitter's own confirmation
-- that their score landed — so the boundary is drawn at "the moves", the one
-- thing a challenge like this is actually trying to keep secret.
--
-- The bound below is a sanity cap, not a promise about the *right* embargo
-- length — same spirit as the loose checks on this table's other columns.
-- ---------------------------------------------------------------------------
alter table public.games add column if not exists embargo_until timestamptz;

alter table public.games drop constraint if exists games_embargo_bounds;
alter table public.games add  constraint games_embargo_bounds check (
  embargo_until is null or embargo_until <= first_seen_at + interval '90 days'
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
-- scores.match_id: `GameLog.match_id` out of the token (`Challenge.log`), naming
-- the replay this score was made in. Added rather than declared in the table
-- above so re-pasting this file upgrades an existing board; blank for every score
-- posted before the game uploaded logs, and for any hand-written link.
--
-- Deliberately no foreign key to game_logs: the log is posted by the *game* and
-- the score by this *site*, so either can arrive first, and a score whose upload
-- was blocked must still post. An unmatched match_id means unverified, never
-- invalid.
-- ---------------------------------------------------------------------------
alter table public.scores add column if not exists match_id text not null default '';

create index if not exists scores_match_id_idx
  on public.scores (match_id) where match_id <> '';

-- ---------------------------------------------------------------------------
-- scores.campaign_start: `Challenge.campaign` out of the token, what the weekly
-- campaign said about this node for this player when the game was *started*
-- (an attemptStatus reason, e.g. 'adjacent'). It decides one thing: whether the
-- campaign's grace covers the score (js/campaign.mjs, startedWithAccess). A
-- claim like `hand`, and safe as one: a forged stamp gets no more grace than a
-- blank, which is what every score before this column, every desktop game and
-- every hand-written link carries.
-- ---------------------------------------------------------------------------
alter table public.scores add column if not exists campaign_start text not null default ''
  check (campaign_start ~ '^[a-z-]{0,20}$');

-- ---------------------------------------------------------------------------
-- game_logs: the replay behind a score — settings, seed, and every turn's orders
-- and combat draws, deflated and base64url'd by `replay.GameLog.encoded`. Posted
-- by the game itself when the player presses "Post to leaderboard", which is the
-- consent: a game merely played, abandoned or lost is never uploaded.
--
-- With "Share replays" switched on in the game's menu it also stores games as
-- they go — every 25 turns and again at the end — so the losses and the abandoned
-- games are kept too. Those are the ones no score can ever carry, and the ones a
-- bot is worth measuring against.
--
-- Neither path attaches anything identifying: a row is a match id, a setup key
-- and the moves. There is deliberately no client id, which would be the only way
-- to group one person's games and is a tracking identifier by any other name.
--
-- This table is the strictest on the board: no select policy *and* no insert
-- policy, and no grant of either to anon. Writes come through this site's own
-- function (`netlify/functions/log.mjs`) under the service_role key, which is
-- what makes a size limit and a rate limit enforceable at all — every other table
-- here takes a hundred-byte row from anyone, whereas a replay is 5-14 KiB and an
-- open insert path would be a storage bill. Reads are the worker's alone, so
-- uploading a game does not publish it.
--
-- Append-only like every other table here, which is what shapes the key: an
-- upload is a new row, never an update, so a match played on past a first upload
-- lands beside its earlier self and the *longest* row is the current one. The
-- worker prunes what it supersedes; the public cannot delete anything.
--
-- The size bound is the real defence on a table anyone may insert into: measured
-- whole games run 5-14 KiB encoded (a 4-player 24-node match of 295 turns is the
-- worst yet seen at 13.6 KiB), so 256 KiB is generous for a very long game and
-- still refuses a blob posted to fill the database.
-- ---------------------------------------------------------------------------
create table if not exists public.game_logs (
  id           bigint generated always as identity primary key,
  match_id     text not null check (match_id ~ '^[0-9a-f]{16}$'),
  game_key     text not null check (char_length(game_key) between 1 and 64),
  turns        integer not null check (turns > 0),
  -- Claims by the client, and stored as claims: an index for finding logs
  -- without decoding every blob (completed games, wins, games actually played by
  -- hand), never evidence. Replaying the log settles all three, which is exactly
  -- what tools/verify_scores.py does before a score is called verified.
  finished     boolean not null default false,
  won          boolean not null default false,
  hand         integer not null default 0 check (hand >= 0),
  -- The rules this match was played under (`engine.RULES_VERSION` at the time),
  -- one more claim of the same kind: it lets a caller tell a replay that can
  -- still be reconstructed exactly from one that can't (`GameLog.is_current`)
  -- without decoding the blob to find out. Every row before this column existed
  -- really was played under rules v1 — it is the only version there has ever
  -- been until now — so the default below is historical fact, not a guess.
  rules_version integer not null default 1 check (rules_version >= 1),
  log          text not null check (octet_length(log) between 1 and 262144),
  submitted_at timestamptz not null default now()
);

-- For a board created before games were stored as they were played. Same
-- drop/add spirit as configs_tags_shape above: re-pasting this file upgrades an
-- existing table, which `create table if not exists` alone would silently skip.
alter table public.game_logs add column if not exists finished boolean not null default false;
alter table public.game_logs add column if not exists won      boolean not null default false;
alter table public.game_logs add column if not exists hand     integer not null default 0;
alter table public.game_logs add column if not exists rules_version integer not null default 1;

-- Longest first: that is the row a verifier wants, and the index answers the
-- lookup by match_id at the same time.
create index if not exists game_logs_match_idx
  on public.game_logs (match_id, turns desc);

-- ---------------------------------------------------------------------------
-- score_checks: what happened when the worker replayed a score's log
-- (`tools/verify_scores.py`). One row per score, keyed by it.
--
--   verified    the log replays and reproduces the posted turns, lost and hand
--   mismatch    it replays, and produces something else — the score is wrong
--   outdated    it does not reproduce, and was played under older *rules*
--               (`engine.RULES_VERSION`), so it is unverifiable rather than wrong
--   unreadable  the blob does not decode, or does not replay at all
--   missing     no log was ever uploaded for this score's match_id
--
-- Written by the worker alone (read policy, no insert policy — same shape as
-- bot_scores) but publicly readable, because "checked" is the whole point of
-- having it. `missing` is stored rather than inferred from absence so the board
-- can tell "nobody has looked at this yet" from "we looked, and there is nothing
-- to check" — the first is silence, the second is a fact about the score.
-- ---------------------------------------------------------------------------
create table if not exists public.score_checks (
  score_id   bigint primary key references public.scores(id) on delete cascade,
  verdict    text not null,
  -- What went wrong, in one line, for a mismatch or an unreadable log. Empty on
  -- a pass. Shown to nobody by default: it is for whoever is looking into a row.
  detail     text not null default '',
  -- The simulation that produced this verdict (`bot_replay.replay_rev`) — the
  -- core modules only, since a log records orders and dice and so is immune to a
  -- *bot* changing. An engine rule changing is a different matter, and that is
  -- what the `outdated` verdict above is for.
  engine_rev text not null default '',
  checked_at timestamptz not null default now()
);

-- A drop/add pair rather than a check inside `create table if not exists`, so
-- re-pasting this file widens the set on a board created before `outdated`
-- existed (the same shape configs_tags_shape uses to tighten one).
alter table public.score_checks drop constraint if exists score_checks_verdict;
alter table public.score_checks add  constraint score_checks_verdict check (
  verdict in ('verified', 'mismatch', 'outdated', 'unreadable', 'missing'));

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
--
-- A digest of the text, so it only groups rows written the same way: every
-- games row comes from the browser. A Python-written setup (Python keeps `0.0`,
-- the browser writes `0`) is matched by jsonb `=` instead -- see campaign_games.
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
-- config_tags: unlike `configs.name`, tags are meant to accumulate rather than
-- be set once — but only from someone who has actually logged a score, which
-- `score_id` is what enforces (a request naming no real `scores.id` is refused
-- by the foreign key, not by any identity check — there is none anywhere on
-- this board). `user_id` rides along too so `config_tag_counts` below can rank
-- by *distinct players*, not raw submissions: one person replaying the same
-- config ten times must not alone make a tag look like consensus.
--
-- tag_key mirrors users.name_key above: a generated, stored, lower/trim column,
-- so normalising "Fun", "fun " and "FUN" to one entry is the database's job
-- rather than something every caller (this site's JS, or anything else that
-- ever POSTs here directly) has to get right on its own. Downstream readers see
-- only tag_key; `tag` exists solely to generate it from.
--
-- No FK to `configs`: same reasoning as `games` above — a config can be tagged
-- before anyone has named it, so config_key is checked for shape alone.
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

-- A drop/add pair, same spirit as configs_tags_shape, so a re-paste can
-- tighten this later. One tag per row, so no comma in the charset.
alter table public.config_tags drop constraint if exists config_tags_tag_shape;
alter table public.config_tags add  constraint config_tags_tag_shape check (
  tag_key ~ '^[a-z0-9 +-]{1,24}$'
);

create index if not exists config_tags_config_key_idx on public.config_tags (config_key);

-- ---------------------------------------------------------------------------
-- bot_scores: how each models/ bot fares in the human's seat on a given map,
-- computed by tools/bot_replay.py (the scheduled GitHub Actions worker) and
-- keyed by the pair it answers for. One row per (map, bot): the result is a
-- pure function of the setup, the seed and the code, so there is nothing to
-- accumulate — a rerun replaces the row rather than appending to it.
--
-- This is the one table on the board that is NOT publicly writable. It has a
-- read policy and no insert/update/delete policy, and no insert grant, so the
-- only writer is the worker's service_role key, which bypasses RLS entirely.
-- Human scores are unverifiable by design (see README, "Known limitations");
-- these are machine-computed from the seed and cannot be posted by hand at all.
--
-- `won` is the discriminator, not `turns`: a bot that never took the map still
-- reports how long it lasted and what it lost, which is worth showing. Callers
-- must not rank a lost game against a won one on turns alone.
-- ---------------------------------------------------------------------------
create table if not exists public.bot_scores (
  game_key     text not null references public.games(game_key),
  bot          text not null check (char_length(trim(bot)) between 1 and 60),
  won          boolean not null,
  turns        integer not null check (turns > 0),
  lost         integer not null check (lost >= 0),
  -- How many times the bot blew its per-decision wall-clock budget and forfeited
  -- that turn's orders. Zero for an ordinary row; anything else means the result
  -- depended on how fast the runner was that day, so the page discloses it
  -- rather than presenting it as reproducible.
  bot_timeouts integer not null default 0 check (bot_timeouts >= 0),
  -- The bot-defined knob this answer belongs to (`AiParams.aux`), and what that
  -- strategy calls it. 1.0 is the untuned default every bot is replayed at unless
  -- `bot_replay.REPLAY_AUX` says otherwise — knower runs on Oracle: Search (2),
  -- which is a materially stronger player than its default Predict. Recorded rather
  -- than implied, so a reader comparing the board against a game they played
  -- from the menu can see which version answered; `aux_label` is empty for a bot
  -- at its default or one that ignores aux entirely.
  aux          real not null default 1.0,
  aux_label    text not null default '',
  -- Digest of the simulation code that produced this row (bot_replay.engine_rev):
  -- the outcome-determining core modules plus every models/*.py. Provenance, and
  -- what `--stale` re-derives from — never part of the key, so a map only ever
  -- has one row per bot and the board never shows two answers to one question.
  engine_rev   text not null default '',
  computed_at  timestamptz not null default now(),
  -- match_id/log/rules_version: the exact replay behind a *winning* row, so its
  -- Watch link can play back the recorded game rather than asking the browser
  -- to re-decide the whole match live (which could disagree with this row —
  -- see tools/bot_replay.py's module doc for why that actually happened).
  -- Blank together on a loss, the same rule a human's own posted score follows
  -- (only a win is worth a Watch link) — never NULL, so an ordinary equality
  -- filter (`match_id <> ''`) is enough everywhere this is read, in SQL and in
  -- JS alike, with no null-handling anywhere.
  match_id      text not null default '' check (match_id = '' or match_id ~ '^[0-9a-f]{16}$'),
  -- The rules a stored log was played under (`engine.RULES_VERSION` at the
  -- time), read the same way `game_logs.rules_version` already is: 0 (rather
  -- than game_logs's historical 1) means "no log here at all", since bot_scores
  -- predates this column entirely and every row before it genuinely has no
  -- replay to disclaim.
  rules_version integer not null default 0 check (rules_version >= 0),
  log           text not null default '' check (octet_length(log) <= 262144),
  primary key (game_key, bot)
);

-- For a board created before a win started storing its own replay. Same
-- drop/add spirit as game_logs's retrofitted columns above: re-pasting this
-- file upgrades an existing table, which `create table if not exists` alone
-- would silently skip.
alter table public.bot_scores add column if not exists match_id text not null default '';
alter table public.bot_scores add column if not exists rules_version integer not null default 0;
alter table public.bot_scores add column if not exists log text not null default '';

-- Matches game_logs's own match_id index in shape and purpose: this is the
-- lookup netlify/functions/replay.mjs's fallback does, via
-- public_watchable_replays below.
create index if not exists bot_scores_match_idx
  on public.bot_scores (match_id) where match_id <> '';

-- ---------------------------------------------------------------------------
-- campaigns: the weekly meta-map (campaign.html, js/campaign.mjs). One row per
-- week, written once near its start by the worker (tools/campaign.py) and never
-- again — the only stored part of the campaign. It has to be stored: which
-- configs existed and which seeds were free are facts about the moment it was
-- made, the "?" nodes are random rolls, and the layout comes from the game's
-- own mapgen, which the board cannot run.
--
-- graph = {nodes: [{id, kind: 'home'|'field', x, y, systems, mystery,
-- settings}], lanes: [[a, b], ...]}, where `settings` is the node's setup in the
-- pruned form a posted link stores in games.settings_json, so the views below
-- can match a node to its game by config and seed. The same fields, not the
-- same text: the worker writes Python's `0.0` where the site stores a browser's
-- `0`.
--
-- Who holds what is *not* stored: it is a replay of the week's posted scores in
-- time order, done by the page, the same way crowns are derived. Public read,
-- no insert policy and no insert grant, so the worker's secret key is its only
-- writer, as with bot_scores.
-- ---------------------------------------------------------------------------
create table if not exists public.campaigns (
  week_start date primary key,
  graph      jsonb not null,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- campaign_games: which game row each campaign node has, once somebody has
-- posted on it -- whichever row carries the node's config and seed, however
-- that row got its game_key. Compared as jsonb, which is equal by value, never
-- through sc_config_key: a digest of the text tells the node's `0.0` from the
-- posted row's `0`.
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
-- game_embargoes: the embargo actually in force on each map — the stored
-- `games.embargo_until`, or the end of a live campaign week the map is a node
-- of, whichever is later. A campaign's maps are hidden until its week is over
-- (the same Monday-to-Monday UTC boundary campaign_scores counts by), since a
-- replay posted on Tuesday would otherwise hand every rival the way through a
-- node they are still racing for.
--
-- Derived rather than stored, and it has to be: a campaign node's seed is
-- fresh (tools/campaign.py), so its `games` row does not exist when the week's
-- campaign is written — it is created by whoever posts on it first, from the
-- site, which knows nothing about campaigns. And `games` is append-only, so
-- there is no later moment to stamp it either. Only live weeks are consulted:
-- a lifted embargo reads the same as none, and it keeps the scan to this
-- week's row however many campaigns pile up.
--
-- Read by public_replays (the gate) and game_summary (what the pages show),
-- so neither needs to know where an embargo came from.
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
-- public_replays: the replays anyone may watch — and the *only* rows of
-- game_logs that ever leave this database to a visitor.
--
-- The rule is one join: a replay is public exactly when a posted score points at
-- it. Posting a score is a deliberate, public act; a game that merely uploaded
-- itself because "Share replays" was on is not, and stays unreadable. So the
-- consent boundary is expressed here in SQL rather than in a function's `if`.
--
-- Note this view is deliberately NOT security_invoker, unlike game_summary and
-- config_summary below. Those exist so a future tightened policy still applies
-- to their callers; this one exists to lend out a *subset* of a table nobody may
-- read, which only owner rights can do. The where clause is the whole boundary,
-- so change it with that in mind.
--
-- Longest upload per match, since a game checkpoints as it goes: distinct on
-- picks it, and the rest are that match's own history.
--
-- `rules_version` is listed *last*, after `log` — not for taste, but because
-- `create or replace view` only accepts an existing view's columns unchanged in
-- name, order and type, with any new ones appended at the end. `log` was the
-- last column before this field existed; inserting `rules_version` ahead of it
-- makes Postgres refuse the whole statement (it reads as trying to rename `log`
-- to `rules_version`), so a board that already had this view would fail to pick
-- up the new column at all — silently, from `game.mjs`'s side, since a failed
-- `create or replace view` leaves the *old* view in place and every later query
-- for `rules_version` against it errors and is swallowed by `watchableIds`'s
-- `.catch(() => [])`, which is what takes down every Watch link, not just a new
-- game's.
--
-- The join/where below is the other half of `games.embargo_until`: a replay
-- whose map is still embargoed is filtered out here, at the one place every
-- reader of a replay goes through (this function, the game's own Watch link,
-- and nowhere else — see the comment on that column). Matched through
-- `scores.game_key` rather than `game_logs.game_key`: a log is stamped with
-- whatever digest the *game* held at upload time, which is not necessarily the
-- key this site filed the map under (`findTwin` in submit.mjs can fold it onto
-- an older one), while a score's `game_key` is guaranteed by its foreign key to
-- name a row that actually exists in `games`. A `left join` rather than an
-- inner one so a log with no matching `games` row (nothing has registered that
-- exact key yet) reads as "no embargo" rather than vanishing.
-- ---------------------------------------------------------------------------
create or replace view public.public_replays as
select distinct on (l.match_id)
  l.match_id, l.game_key, l.turns, l.finished, l.won, l.hand, l.log, l.rules_version
from public.game_logs l
join public.scores s on s.match_id = l.match_id
left join public.game_embargoes e on e.game_key = s.game_key
where e.embargo_until is null or e.embargo_until <= now()
order by l.match_id, l.turns desc, l.id desc;

-- ---------------------------------------------------------------------------
-- public_watchable_replays: the worker-side counterpart to public_replays —
-- everything netlify/functions/replay.mjs may hand back for a given id,
-- whichever pool it came from. That function reads exactly one relation on
-- purpose ("what may be served is decided in SQL, not here... it has no `if`
-- that could drift from that rule" — its own doc), so a second, differently
-- gated source is unioned in here rather than added as a branch there.
--
-- The two halves are gated by two different, unrelated rules, which is why
-- this is a plain `union all` rather than one already-existing view widened:
-- a human replay is public only because a posted score points at it
-- (public_replays' own consent boundary, untouched); a bot's replay is
-- public because bot_scores itself already is — read by anyone, keyed by no
-- person, computed by a worker rather than disclosed by one — so a win's
-- stored log needs no *further* gate here at all. Filtering to `match_id <>
-- ''` is what keeps a loss (which stores none) out.
--
-- Not itself granted to anon/authenticated: nothing here that the public
-- couldn't already read some other way (public_replays directly, or a bot's
-- own row) needs a second, wider door — this view exists purely so
-- replay.mjs's one lookup covers both pools.
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
-- config_tag_counts: per-config tag frequency, counting distinct players
-- (`config_tags.user_id`) rather than raw submissions. Folds in the legacy
-- `configs.tags` array too, at weight 1 per tag with no player behind it —
-- otherwise a board's existing named tags would simply vanish the moment this
-- ships, before a single new-style submission exists to replace them. Real,
-- counted submissions naturally outrank that flat legacy weight as they
-- accumulate; nothing here ever writes `configs.tags` again, but nothing
-- deletes what is already there either.
--
-- security_invoker, like game_summary/config_summary below (which read this
-- view rather than `configs.tags` directly) — so the caller's own grants are
-- what's checked at every step, not the view owner's.
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
-- counted_scores: the one place the crowns' eligibility rule lives: every posted score
-- counts unless the worker replayed its log and found it wrong (`mismatch`). An
-- unchecked, missing or outdated replay still counts, because the game uploads a
-- log once, with no retry, so a score posted offline or pasted as a hand-made
-- link can never become `verified` -- and shutting those out would shut out
-- whole ways of playing. Nothing is stored: deleting a score (tools/admin.py)
-- recomputes game_summary's `contenders` and the crown views for free.
-- ---------------------------------------------------------------------------
create or replace view public.counted_scores
  with (security_invoker = true) as
select s.id, s.game_key, s.user_id, u.name as user_name, s.turns, s.lost, s.submitted_at,
  -- New columns go last: `create or replace view` can add one, never reorder.
  s.hand,
  s.campaign_start
from public.scores s
join public.users u on u.id = s.user_id
left join public.score_checks c on c.score_id = s.id
where c.verdict is distinct from 'mismatch';

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
  coalesce(tagc.tags, '{}'::text[])          as config_tags,
  -- The best *winning* bot_scores row for this map (fewest turns, ties broken
  -- by lost — the same ordering `best` above uses for the human leader), so a
  -- list card can tell whether any human score actually beats it without a
  -- second per-game query. Null exactly when no bot has ever taken the map,
  -- matching standings.mjs's bestBot() null case.
  bot.turns as bot_turns,
  bot.lost  as bot_lost,
  bot.bot   as bot_name,
  -- The embargo in force, not just the stored one: game_embargoes folds in a
  -- live campaign week, so a campaign node reads as embargoed on every page
  -- that already honours `games.embargo_until`.
  e.embargo_until,
  -- The home page's filters, computed here because PostgREST can filter on a
  -- column but not on a comparison between two of them.
  -- contenders: distinct players with a counted score (counted_scores below),
  -- so `contenders >= 2` is exactly the set crown_holders has a row for.
  coalesce(field.contenders, 0) as contenders,
  -- bot_leads: a winning bot exists and no human score beats it — a tie still
  -- reads as the bot's, the same verdict as format.mjs's botLeadBadge.
  (bot.turns is not null
    and (best.turns is null or (bot.turns, bot.lost) <= (best.turns, best.lost))) as bot_leads,
  -- fog: settings_json is pruned to non-defaults and the default is fog off,
  -- so either range being present at all means fog is on (setup.mjs's hasFog).
  (g.settings_json ?| array['fog_sight', 'fog_scout']) as fog
from public.games g
join public.game_embargoes e on e.game_key = g.game_key
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
left join lateral (
  select bs.turns, bs.lost, bs.bot
  from public.bot_scores bs
  where bs.game_key = g.game_key and bs.won
  order by bs.turns asc, bs.lost asc
  limit 1
) bot on true
left join public.configs cfg
  on cfg.config_key = public.sc_config_key(g.settings_json)
left join lateral (
  -- Top 6 by frequency (distinct players first, legacy weight-1 tags filling
  -- in behind them), same cap `configs_tags_shape` enforced on the old array.
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
  coalesce(tagc.tags, '{}'::text[]) as config_tags,
  agg.game_count,
  agg.score_count,
  agg.last_activity,
  -- New columns go last. fog is part of sc_config_key, so every game in the
  -- group shares it; see game_summary.fog.
  (rep.settings_json ?| array['fog_sight', 'fog_scout']) as fog
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
-- Crowns and steals: the board's weekly contest (crowns.html, js/crowns.mjs).
-- What it rewards is *taking first place from somebody else* on a map more than
-- one person has played -- not volume, which a count of wins or of maps would
-- pay out for grinding easy setups.
--
-- counted_scores, the eligibility rule, is defined above game_summary, which
-- reads it for the home page's `contenders`.
-- ---------------------------------------------------------------------------
-- crown_holders: one row per *contested* map (counted scores from two or more
-- distinct players), naming whoever holds its record. The record is chosen the
-- way game_summary.best is -- fewest turns, then fewest lost, then earliest -- so
-- a tie never takes a crown: the first to reach a result keeps it.
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
-- crown_steals: one row per counted score that strictly beat the record it
-- found on arrival, where that record belonged to somebody else. Beating your
-- own record, tying one, and the first score on an empty map are not steals.
-- Names and turns only -- the same detail game_summary already shows on an
-- embargoed map, so this reveals nothing an embargo hides.
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
-- of its nodes, with at least one turn played by hand.
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
-- Policies
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

-- Whether the caller may post as this user row: it is unclaimed, or theirs.
-- Security definer because the public cannot read users.owner (see the grants).
create or replace function public.sc_may_use_user(p_user_id bigint)
returns boolean language sql stable security definer set search_path = public as $$
  select not exists (
    select 1 from public.users u
    where u.id = p_user_id and u.owner is not null
      and u.owner is distinct from auth.uid())
$$;

-- claim_name: the one self-service way to own a name. Only a name nobody has
-- used: no row yet, or a row with no score and no tag behind it (a submission
-- that failed after ensureUser). A name already in use is the owner's to hand
-- out (tools/admin.py assign-name), since nothing here proves who used it.
-- Errors carry a fixed message the board matches on: "sign in", "already own",
-- "name in use", "bad name".
create or replace function public.claim_name(p_name text)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  me uuid := auth.uid();
  row_id bigint;
  row_owner uuid;
begin
  if me is null then raise exception 'sign in to claim a name'; end if;
  if char_length(trim(coalesce(p_name, ''))) not between 1 and 60 then
    raise exception 'bad name';
  end if;
  if exists (select 1 from public.users where owner = me) then
    raise exception 'you already own a name';
  end if;
  select id, owner into row_id, row_owner from public.users
    where name_key = lower(trim(p_name)) for update;
  if row_id is null then
    begin
      insert into public.users (name, owner) values (trim(p_name), me)
        returning id into row_id;
    exception when unique_violation then
      raise exception 'name in use';
    end;
    return row_id;
  end if;
  if row_owner is not null
     or exists (select 1 from public.scores where user_id = row_id)
     or exists (select 1 from public.config_tags where user_id = row_id) then
    raise exception 'name in use';
  end if;
  update public.users set owner = me where id = row_id;
  return row_id;
end
$$;

-- release_name: give your name back. It becomes an ordinary unclaimed name again
-- (its scores stay on it), and you may claim another unused one.
create or replace function public.release_name()
returns void language sql security definer set search_path = public as $$
  update public.users set owner = null where owner = auth.uid() and auth.uid() is not null
$$;

-- my_name: the name the signed-in caller owns, or null — the one way the board
-- reads ownership back, since the public cannot select users.owner.
create or replace function public.my_name()
returns text language sql stable security definer set search_path = public as $$
  select name from public.users where owner = auth.uid() and auth.uid() is not null
$$;

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

create policy "users public read"    on public.users  for select using (true);
-- Claimed rows are made only by claim_name (security definer), never by a
-- plain insert, so nobody can create a name pre-owned by someone else's uuid.
create policy "users public insert"  on public.users  for insert with check (owner is null);
create policy "games public read"    on public.games  for select using (true);
create policy "games public insert"  on public.games  for insert with check (true);
create policy "scores public read"   on public.scores for select using (true);
-- A claimed name is its owner's alone: auth.uid() is the signed-in caller's
-- account, null for an anonymous one, so an anonymous post under a claimed name
-- fails here. submit.mjs checks first to explain; this is the actual boundary.
create policy "scores public insert" on public.scores for insert
  with check (public.sc_may_use_user(user_id));
create policy "configs public read"   on public.configs for select using (true);
create policy "configs public insert" on public.configs for insert with check (true);
create policy "config_tags public read"   on public.config_tags for select using (true);
create policy "config_tags public insert" on public.config_tags for insert
  with check (public.sc_may_use_user(user_id));

-- Read only, and no insert policy to match: bot_scores is written solely by
-- tools/bot_replay.py under the service_role key, which bypasses RLS.
create policy "bot_scores public read" on public.bot_scores for select using (true);

-- game_logs has neither: RLS is on and no policy exists, so every public command
-- against it is refused outright and the only writer is netlify/functions/log.mjs
-- under the service_role key (which bypasses RLS entirely). score_checks is a
-- bot_scores-shaped table again — the worker writes the verdicts, everyone reads.
create policy "score_checks public read" on public.score_checks for select using (true);
create policy "campaigns public read" on public.campaigns for select using (true);

-- No update or delete policy anywhere: that is what makes every row append-only
-- — for configs, that's what makes the first name posted for a setup permanent,
-- and for config_tags it's the whole design: unlike a name, a tag is *meant* to
-- accumulate indefinitely, which an append-only table naturally supports without
-- ever needing an UPDATE policy at all.
-- bot_scores is append-only to the public in the strongest sense (it has no
-- insert policy either), but not immutable in itself: the worker's service_role
-- key bypasses RLS, which is how a recompute replaces a row.
--
-- Because game_summary is security_invoker, it reads `configs` as the caller:
-- a missing read policy or grant on configs fails the *whole* homepage query
-- with "permission denied for table configs", not just a blank config_name.

-- Explicit rather than relying on the project's default privileges, so this file
-- is the whole story. Identity columns need no sequence grant (unlike serial).
-- users is granted by column, below, so its owner uuid stays private.
grant select on public.games, public.scores, public.configs,
  public.config_tags, public.game_summary, public.config_summary,
  public.bot_scores, public.score_checks, public.public_replays,
  public.counted_scores, public.crown_holders, public.crown_steals,
  public.campaigns, public.campaign_games, public.campaign_scores,
  public.game_embargoes
  to anon, authenticated;
-- game_logs is deliberately absent from that list: no select grant and no select
-- policy is what keeps an uploaded replay readable only by the worker. The
-- public_replays view above is the one exception, and it lends out only the
-- replays a posted score already points at.
-- bot_scores is absent from this list on purpose: no insert grant and no insert
-- policy is what leaves the replay worker as its only writer.
-- config_tag_counts needs its own select grant despite nothing querying it
-- directly today: it is security_invoker, so game_summary/config_summary check
-- the *caller's* privilege on it as a distinct relation, the same reason
-- `configs` itself needs one (see the comment above the policies).
grant select on public.config_tag_counts to anon, authenticated;
-- Every users column but owner. The revoke first, because a re-paste over a
-- table-wide grant would otherwise leave it standing beside the column one.
revoke select on public.users from anon, authenticated;
grant select (id, name, name_key, created_at, claimed) on public.users
  to anon, authenticated;
grant insert on public.users, public.games, public.scores, public.configs,
  public.config_tags to anon, authenticated;
-- game_logs appears in neither grant: not in select (a replay is not public) and
-- not in insert (uploads go through this site's function, which can size- and
-- rate-limit them). It is the one table the public can neither read nor write.
grant execute on function public.sc_config_key(jsonb), public.sc_bots(jsonb, integer)
  to anon, authenticated;
-- Claiming needs a signed-in caller; the functions refuse a null auth.uid() too.
revoke execute on function public.claim_name(text), public.release_name(),
  public.my_name() from public, anon;
grant execute on function public.claim_name(text), public.release_name(),
  public.my_name() to authenticated;
grant execute on function public.sc_may_use_user(bigint) to anon, authenticated;

-- service_role bypassing RLS only skips policies — the base GRANT system
-- underneath still applies, so tools/bot_replay.py needs its own explicit
-- grants: SELECT on games (what it replays) and bot_scores (to see what's
-- already cached), plus INSERT/UPDATE on bot_scores for the upsert itself
-- (its "merge-duplicates" Prefer header is an INSERT ... ON CONFLICT DO UPDATE).
--
-- This note has been here since the first time that caught someone, and it did
-- not stop it happening twice more below. It states the mechanism, which was
-- never the hard part: the hard part is re-deriving *which* relations a caller
-- reads, every time one gains a query, and a comment cannot check a list.
-- `tests/test_schema_grants.py` does — it reads the callers and these grants and
-- requires them to agree, so a new query with no grant fails a test rather than
-- a production run. Add the grant beside the caller it is for; the test will say
-- if you missed one.
grant select on public.games, public.bot_scores to service_role;
grant insert, update on public.bot_scores to service_role;
-- tools/verify_scores.py: read the scores and the replays behind them, write the
-- verdicts, and delete a game_logs row that a longer upload of the same match has
-- superseded (the public has no delete path anywhere, worker or not).
--
-- Every relation the key touches has to be listed, including the *views* and
-- including a table it only reads to decide what work is left. This is the trap
-- the note above describes and it has caught this file twice: a missing grant
-- here does not degrade, it fails the query outright — and it fails it only for
-- the worker, so the same row stays perfectly visible to the anon key on the
-- site, which is exactly how it hides.
grant select on public.scores to service_role;
-- select: which scores already have a verdict (`verify_scores.pending`).
grant select, insert, update on public.score_checks to service_role;
-- The weekly campaign worker (tools/campaign.py) writes each week's map once.
grant select, insert on public.campaigns to service_role;
-- ...and counts last week's active players off the same rule the board uses.
grant select on public.counted_scores to service_role;
-- select + delete for the worker (read a replay, prune one a longer upload has
-- superseded); insert for netlify/functions/log.mjs, which holds the same key.
grant select, insert, delete on public.game_logs to service_role;
-- ...and the view netlify/functions/replay.mjs actually serves a replay out of:
-- public_watchable_replays, the union of public_replays with a winning bot's own
-- stored log (see that view's own comment). Like public_replays before it, it is
-- not security_invoker, so it reads game_logs and bot_scores with owner rights
-- whoever asks — the caller still needs SELECT on the view itself, which grants
-- nothing beyond the rows its own definition already allows anyone to read some
-- other way (public_replays directly, or a bot's own public row). public_replays
-- needs no grant of its own here any more: nothing under this key reads it
-- directly since replay.mjs switched to the union.
grant select on public.public_watchable_replays to service_role;
-- ...and the security_invoker views that union reaches. Owner rights stop at the
-- first view that sets security_invoker: Postgres checks what *that* view reads
-- against the role running the query, however deep in the chain it sits. So
-- public_replays (owner rights) can read game_embargoes, but game_embargoes then
-- reads campaign_games as service_role, which therefore needs SELECT on it, and
-- every Watch link failed with "permission denied for view campaign_games" from
-- the day the embargo join went in until this grant. game_embargoes itself is
-- listed too, against the day public_replays turns security_invoker.
-- test_schema_grants walks that chain.
grant select on public.game_embargoes, public.campaign_games to service_role;

-- ---------------------------------------------------------------------------
-- Play-by-post: pbp_matches, pbp_orders
--
-- A different kind of thing to everything above, and deliberately kept apart
-- from it. The board's tables are append-only, carry no identity, and describe
-- finished single-player results. A play-by-post match is none of those: it is
-- *live*, it advances, and a seat has to be something only one person can
-- submit for. Rather than weaken the guarantees the rest of this file makes --
-- there are no UPDATE policies anywhere above, and that absence is the
-- mechanism -- multiplayer gets its own two tables with their own rules, and
-- nothing about a posted score changes.
--
-- The server holds no board and runs no engine. It cannot: the board is a pure
-- function of the settings, the seed and every turn's orders and dice, which is
-- what `replay.reconstruct` already rebuilds without asking any seat to decide.
-- So this stores the *inputs* and lets each client rebuild the position, exactly
-- as a replay does -- which is also why a finished play-by-post match is an
-- ordinary log that resumes, reviews and verifies like any other.
--
-- Seat tokens are the one identity-shaped thing here, and they are deliberately
-- the weakest kind: random per match per seat, stored only as a hash, meaningful
-- in one match and dead when it ends. They authorise "this seat, this match" and
-- nothing else. They are not accounts, they do not group a person's games, and
-- there is still no client id anywhere on this board.
--
-- Like game_logs, both tables are function-only: no policies, no anon grants.
-- Writes go through netlify/functions/pbp.mjs under the secret key, which is
-- what makes a size limit, a rate limit and a token check enforceable at all.
-- ---------------------------------------------------------------------------
create table if not exists public.pbp_matches (
  match_id      text primary key check (match_id ~ '^[0-9a-f]{16}$'),
  -- The setup every client rebuilds from, and the seed that fixes the map and
  -- every fight. Both immutable for the life of the match: changing either would
  -- silently re-map a game in progress.
  settings_json jsonb not null,
  seed          integer not null,
  rules_version integer not null default 1 check (rules_version >= 1),
  -- Which seats are people, and which of those have been claimed. `seats` is the
  -- full roster (a bot seat is simply absent), so an unclaimed seat is already a
  -- representable thing -- that is what an open-seat lobby would list, without
  -- this table changing shape.
  seats         jsonb not null,
  -- The live turn: what every seat is currently submitting orders for. Bumped
  -- only when the last outstanding seat submits.
  turn          integer not null default 0 check (turn >= 0),
  -- The match so far, in `replay.GameLog.encoded` form -- the same deflate +
  -- base64url blob game_logs stores, so a finished match needs no conversion to
  -- be watched, resumed or verified. Rewritten each time a turn resolves, which
  -- is the one genuine UPDATE on this board and the reason these tables are
  -- separate from the append-only ones above.
  log           text not null default '' check (octet_length(log) <= 262144),
  -- When the live turn opened, for the deadline. Wall-clock time is otherwise
  -- absent from this game: `GameLog` stamps `created_at`/`updated_at` but never
  -- reads them back for logic, so a deadline has to live here rather than in the
  -- log. Null means the match has no deadline at all, which is a real choice for
  -- two people who know each other.
  turn_opened_at timestamptz not null default now(),
  deadline_hours integer check (deadline_hours is null or deadline_hours between 1 and 336),
  finished      boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- pbp_orders: one seat's submission for one turn.
--
-- Append-only in spirit and unique per (match, turn, seat), so a resubmission is
-- a conflict the function has to decide about rather than something that happens
-- by accident. Orders are stored as submitted; whether a source is held or a
-- destination adjacent is `engine.apply_order`'s to decide, for every seat
-- alike, exactly as it is for a bot on the wire (`botio.orders_from`).
--
-- `board_digest` is the coherence tripwire: every client that resolves a turn
-- reports what board it landed on. The board is a pure function of the stored
-- inputs, so honest clients agree by construction -- this is what catches a
-- stale build or a genuine nondeterminism bug, and it is emphatically not an
-- anti-cheat measure (a client that would lie about this would lie about its
-- orders too).
-- ---------------------------------------------------------------------------
create table if not exists public.pbp_orders (
  id           bigint generated always as identity primary key,
  match_id     text not null references public.pbp_matches(match_id) on delete cascade,
  turn         integer not null check (turn >= 0),
  seat         integer not null check (seat between 1 and 6),
  -- `[{"src": 3, "dst": 7, "ships": 12}, ...]`. No owner on the wire: the seat is
  -- stamped from the token, so a foreign order is not something the protocol can
  -- express rather than something it filters.
  orders_json  jsonb not null,
  -- How this submission came about: a person, a seat that let its deadline pass
  -- (`hold`), or one played by its bot after a second miss (`bot`). Disclosed,
  -- never hidden -- the same posture `hand` takes on a posted score.
  source       text not null default 'human' check (source in ('human', 'hold', 'bot')),
  board_digest text not null default '' check (char_length(board_digest) <= 64),
  submitted_at timestamptz not null default now(),
  constraint pbp_orders_one_per_seat_per_turn unique (match_id, turn, seat)
);

create index if not exists pbp_orders_turn_idx
  on public.pbp_orders (match_id, turn);

-- Public matches: listed on the lobby page (`pbp.html`, `?action=list`) and
-- joinable from it (`?action=claim`). A private match stays reachable only by
-- knowing its id, which is why this defaults to false and why nothing lists a
-- row that is not flagged. A public match's open seats are the ones with no
-- entry in `seats.tokens`: `create` mints only the creator's token, and a claim
-- mints the rest one at a time, so "unclaimed" is a missing hash rather than a
-- second field to keep in step with it.
alter table public.pbp_matches
  add column if not exists public boolean not null default false;

create index if not exists pbp_matches_public_idx
  on public.pbp_matches (updated_at desc) where public;

-- What the lobby says about a match beyond its setup. All three are
-- self-declared display text, set through the function and never read back for
-- logic: `title` by the creator at `create`; `names` as `{seat: name}`, each
-- entry written by that seat's own holder (the creator's at `create`, a joiner's
-- at `claim`); `winner` the seat the resolving client reports on the final turn,
-- trusted exactly as far as `finished` beside it (0 is a draw by elimination).
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

-- RLS on, and deliberately no policies at all: with row-level security enabled,
-- a command with no policy is refused outright, so these two tables are closed
-- to the publishable key exactly as game_logs is. That is the mechanism, not an
-- omission -- see the note at the top of this file. Every read and write goes
-- through netlify/functions/pbp.mjs under the secret key, which is the only
-- place a seat token can actually be checked.
alter table public.pbp_matches enable row level security;
alter table public.pbp_orders enable row level security;

-- ...and the grants the function needs under that key. A secret key bypasses RLS
-- but *not* the GRANT system underneath (the trap this file's own note above
-- describes, which has caught it three times): without these the queries fail
-- outright, and only for the worker. `update` on pbp_matches is what no other
-- table here has -- it is the one thing that genuinely advances.
grant select, insert, update on public.pbp_matches to service_role;
-- `update` on pbp_orders is narrower than it sounds: the only column ever
-- written after the fact is `board_digest`, stamped on the resolving seat's own
-- row once the turn it describes has been played. The orders themselves are
-- never rewritten — that is what the unique constraint per (match, turn, seat)
-- is for.
grant select, insert, update on public.pbp_orders to service_role;

-- ---------------------------------------------------------------------------
-- Moderation: tools/admin.py
--
-- The one place anything on the board is deleted or rewritten, by the owner,
-- under the secret key. admin_actions is its log: one row per applied command,
-- written *before* the command runs, holding what was done, to what, why, and
-- the rows about to be removed or overwritten — so a moderation can be undone
-- by hand, and none happens without a trace. Closed like game_logs: RLS on, no
-- policies, no public grants.
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
-- its config_tags and score_checks rows through their foreign keys; config_tags
-- is also deleted from directly, when a tag is removed on its own.
grant select, insert, update on public.users to service_role;
-- users.owner is also read by netlify/functions/pbp.mjs (is this seat name
-- claimed, and by whom) and written by admin.py assign-name/release-name.
grant select, update, delete on public.configs to service_role;
grant select, delete on public.config_tags to service_role;
grant delete on public.scores, public.games, public.bot_scores to service_role;
-- A seat's token rewritten or forgotten, or a whole match removed.
grant delete on public.pbp_matches, public.pbp_orders to service_role;

-- New relations aren't visible to PostgREST until it reloads its schema cache.
-- Supabase's DDL event triggers usually fire this already; idempotent either way.
notify pgrst, 'reload schema';
