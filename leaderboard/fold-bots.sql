-- Fold the bot column onto one row per family, as the game folded council,
-- hunter and claudebot into knower, actuary and thinker as aux stops
-- (2026-10-19; docs/design/bots.md, "Bot families").
--
-- tools/bot_replay.py now replays one bot per family at the top of its knob:
-- knower at Council (aux 3), actuary at Hunt (3), thinker at Thinker (1). What
-- it would do on its next run with no help from here:
--
--   * actuary rows refill by themselves: their stored aux (2, Learning) is not
--     the 3 it now asks for, and `pending()` re-runs a row whose aux moved.
--   * knower rows would refill the same way, at Council. But council at On is
--     knower at Council exactly (tools/fold_check.py: the same orders on every
--     position), so a council row already is that answer: renaming it saves the
--     whole re-run, and the Watch link's log needs nothing (a stored log is
--     never re-decided, and a seat stored as `council` still plays through
--     ai.LEGACY).
--   * claudebot (and hunter, if a run ever wrote it) would linger, a name no
--     run refreshes. They go.
--
-- Run in the Supabase SQL editor, which is `postgres` and so bypasses the
-- bot_scores read-only policies (schema.sql). Idempotent: a second run finds
-- nothing to move and says so. Run it before the next bot_replay run, or that
-- run re-plays knower everywhere a council row could have been kept.

do $$
declare
  stale_knower integer;
  doubled      integer;
  renamed      integer;
  retired      integer;
begin
  -- knower at Search: superseded by Council, from the council rows below or by
  -- the next run.
  delete from public.bot_scores where bot = 'knower' and aux <> 3;
  get diagnostics stale_knower = row_count;

  -- A map a run already gave knower at Council keeps that row.
  delete from public.bot_scores c
  where c.bot = 'council'
    and exists (select 1 from public.bot_scores k
                where k.game_key = c.game_key and k.bot = 'knower');
  get diagnostics doubled = row_count;

  update public.bot_scores
  set bot = 'knower', aux = 3, aux_label = 'Oracle: Council'
  where bot = 'council';
  get diagnostics renamed = row_count;

  delete from public.bot_scores where bot in ('claudebot', 'hunter');
  get diagnostics retired = row_count;

  raise notice 'knower at Search removed: %, council rows dropped beside a knower row: %, '
               'council rows renamed to knower at Council: %, claudebot/hunter rows removed: %',
               stale_knower, doubled, renamed, retired;
end $$;
