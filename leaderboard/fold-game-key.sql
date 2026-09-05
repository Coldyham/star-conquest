-- Move every score from a superseded game_key onto the current one.
--
-- games.game_key is Challenge.key out of the token — the game's blake2s checksum
-- of the setup. Adding a field to Settings moves that digest, so links shared
-- either side of such a change describe one map under two keys and the board
-- splits them. `settings._LEGACY_KEY_DROPS` teaches the game to accept
-- both, and `KEY_ALIASES` in js/token-decode.mjs plus `findTwin` in
-- js/submit.mjs stop new submissions re-splitting them; this is the half that
-- repairs rows already stored.
--
-- It refuses to merge two rows that are not the same setup: the keys are
-- supplied by hand, and moving one map's scores onto another would be
-- unrecoverable on an append-only board. `settings_json` minus `autoplay` is the
-- same identity `sc_config_key` and `submit.setupIdentity` compare, and it holds
-- across a schema change because `token_dict` prunes every field still at its
-- default, so a field added since is absent from both rows.
--
-- Run in the Supabase SQL editor, which is `postgres` and so bypasses the
-- append-only RLS policies (schema.sql). Idempotent and safe if from_key holds
-- nothing: it reports what it moved and moves nothing twice.
--
-- Edit the two keys, then run.

do $$
declare
  from_key text := 'e2954098c1a26e02';  -- the superseded checksum
  to_key   text := '7f7fabfca0969ba4';  -- what that same setup hashes to now
  moved    integer;
  mismatch text;
begin
  if from_key = to_key then
    raise exception 'from_key and to_key are the same';
  end if;
  if not exists (select 1 from public.games g where g.game_key = from_key) then
    raise notice 'nothing stored under %, nothing to do', from_key;
    return;
  end if;

  -- Both rows present: prove they describe one map before merging them. A
  -- destination that does not exist yet cannot disagree, and is carried across
  -- below instead.
  -- The ::text casts pick `jsonb - text` (delete a key) out of an operator set
  -- that also holds `jsonb - integer`, rather than leaving it to unknown-literal
  -- resolution — the same care the parens in schema.sql's sc_config_key take.
  select format('%s is %s, %s is %s', from_key, o.settings_json - 'autoplay'::text,
                to_key, d.settings_json - 'autoplay'::text)
  into mismatch
  from public.games o
  join public.games d on d.game_key = to_key
  where o.game_key = from_key
    and (o.settings_json - 'autoplay'::text) is distinct from (d.settings_json - 'autoplay'::text);
  if mismatch is not null then
    raise exception 'these keys are different maps: %', mismatch;
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
