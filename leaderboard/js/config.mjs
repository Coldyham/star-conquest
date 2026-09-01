// Supabase project details. Fill these in from Settings -> API Keys in the
// Supabase dashboard, then apply schema.sql in its SQL editor.
//
// The anon key belongs in git: it is designed to be public, and the row-level
// security policies in schema.sql are the actual boundary. The *service_role*
// key bypasses RLS entirely and must never appear in this repo.

export const SUPABASE_URL = "https://vppihsnfotpdkkqxstdc.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_6zAnA0eBt8wAahb6rplDpg_Rik3sE29";

// Where the game itself is deployed, e.g. "https://star-conquest.netlify.app/".
// Set it and each map page offers a "Play this map" link carrying the leader's
// score as the target to beat; leave it blank and no link is shown.
export const GAME_URL = "https://star-conquest.netlify.app/";
