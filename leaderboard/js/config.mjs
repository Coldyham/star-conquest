// Supabase project details. Fill these in from Settings -> API Keys in the
// Supabase dashboard, then apply schema.sql in its SQL editor.
//
// The anon key belongs in git: it is designed to be public, and the row-level
// security policies in schema.sql are the actual boundary. The *service_role*
// key bypasses RLS entirely and must never appear in this repo.

export const SUPABASE_URL = "https://vppihsnfotpdkkqxstdc.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_6zAnA0eBt8wAahb6rplDpg_Rik3sE29";

// Where the game itself is deployed. Set it and each map page offers a "Play this
// map" link carrying the leader's score as the target to beat, and a "Watch" link
// on any score whose replay is public; leave it blank and neither is shown.
//
// These pages are served by the game's own site, under /board/, so on a
// `.netlify.app` host the game is simply this origin's root — production,
// a deploy preview and a branch deploy each link to their own build, with
// nothing to edit by hand. `starconquest/webstore.py`'s `leaderboard_origin`
// is the same rule from the other side.
//
// The constant is the fallback for any other host: a custom domain, a local
// server, a file:// page.
const GAME_URL_FALLBACK = "https://star-conquest.netlify.app/";
const NETLIFY = ".netlify.app";

/** This origin's root when it is a Netlify deploy of the game, else "". */
function ownGame() {
  const host = (globalThis.location && globalThis.location.hostname) || "";
  return host.endsWith(NETLIFY) ? `https://${host}/` : "";
}

export const GAME_URL = ownGame() || GAME_URL_FALLBACK;

// Mirrors `starconquest.engine.RULES_VERSION` by hand — there is no shared build
// step between this site and the game's Python, so the two can only be kept in
// step by convention (bump this in the same commit that bumps that) and a test:
// `tests/test_leaderboard_sync.py` fails the moment they drift.
//
// What it buys: `game.mjs`'s Watch link is decided from `public_replays.
// rules_version` (a claim stored alongside the blob, same as `finished`/`won`/
// `hand` — see `schema.sql`) compared against this constant, so a replay stamped
// under rules this build no longer plays by is never offered as if it still
// reproduced the game (`GameLog.is_current` is the same check, on the Python
// side, for the game's own Watch/resume).
export const CURRENT_RULES_VERSION = 2;

// Mirror `starconquest.config.PLAYER_NAMES`/`PLAYER_COLORS` by hand, index 0
// neutral; pinned by `tests/test_leaderboard_sync.py`.
export const PLAYER_NAMES = ["Neutral", "Azure", "Crimson", "Verdant", "Amber", "Violet", "Gold"];
export const PLAYER_COLORS = ["#7a808c", "#56aaff", "#f05a5a", "#5fd282", "#f0aa46", "#be82f0", "#f0e66e"];
