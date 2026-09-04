-- Move every score from a superseded game_key onto the current one.
--
-- games.game_key is Challenge.key out of the token — the game's blake2s checksum
-- of the setup. Adding a field to Settings moves that digest, so links shared
-- either side of such a change describe one map under two keys and the board
-- splits them. `settings._LEGACY_KEY_DROPS` teaches the game to accept both and
-- `KEY_ALIASES` in js/token-decode.mjs stops new submissions re-splitting them;
-- this is the half that repairs rows already stored.
--
-- Run in the Supabase SQL editor, which is `postgres` and so bypasses the
-- append-only RLS policies (schema.sql). Idempotent and safe if from_key holds
-- nothing: it reports what it moved and moves nothing twice.
--
-- Edit the two keys, then run.

do $$
declare
  from_key text := '3e7b44384effd7b2';  -- the superseded checksum
  to_key   text := '665714b9291851c6';  -- what that same setup hashes to now
  moved    integer;
begin
  if from_key = to_key then
    raise exception 'from_key and to_key are the same';
  end if;
  if not exists (select 1 from public.games g where g.game_key = from_key) then
    raise notice 'nothing stored under %, nothing to do', from_key;
    return;
  end if;

  -- The destination row may not exist yet, if this board has only ever seen the
  -- old link. Carry the setup across rather than stranding the scores.
  insert into public.games (game_key, mode, players, nodes, seed, settings_json, first_seen_at)
  select to_key, g.mode, g.players, g.nodes, g.seed, g.settings_json, g.first_seen_at
  from public.games g
  where g.game_key = from_key
  on conflict (game_key) do nothing;

  update public.scores s set game_key = to_key where s.game_key = from_key;
  get diagnostics moved = row_count;

  -- The map was first seen when the earlier of the two rows was created.
  update public.games d
  set first_seen_at = least(d.first_seen_at, o.first_seen_at)
  from public.games o
  where d.game_key = to_key and o.game_key = from_key;

  delete from public.games g where g.game_key = from_key;

  raise notice 'moved % score(s) from % to %', moved, from_key, to_key;
end $$;
