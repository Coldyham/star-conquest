-- The slice of Supabase's own `auth` schema that leaderboard/schema.sql leans
-- on, for the opt-in SQL tests (SC_TEST_PG) on a bare Postgres. A real project
-- has all of this already, and more.
--
-- auth.uid() reads the request's JWT subject the way Supabase's does, so a test
-- signs in as an account with `set request.jwt.claim.sub = '<uuid>'` and signs
-- out by setting it to ''.
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key, email text);
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
grant usage on schema auth to anon, authenticated, service_role;

-- Supabase's service_role skips RLS (the reason the secret key can write what
-- the public cannot), so updates and deletes from admin.py see every row.
alter role service_role bypassrls;
