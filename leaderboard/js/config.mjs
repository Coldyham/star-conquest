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
// `.netlify.app` host the game is simply this origin's /game/ — production,
// a deploy preview and a branch deploy each link to their own build, with
// nothing to edit by hand. `starconquest/webstore.py`'s `leaderboard_origin`
// is the same rule from the other side.
//
// The constant is the fallback for any other host: a custom domain, a local
// server, a file:// page.
const GAME_URL_FALLBACK = "https://star-conquest.netlify.app/game/";
const NETLIFY = ".netlify.app";

/** This origin's /game/ when it is a Netlify deploy of the game, else "". */
function ownGame() {
  const host = (globalThis.location && globalThis.location.hostname) || "";
  return host.endsWith(NETLIFY) ? `https://${host}/game/` : "";
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

// Mirror `starconquest.config.FOG_ON_SIGHT`/`FOG_ON_SCOUT`: the ranges the
// Basic tab's "Fog of war" checkbox applies, which setup.mjs names plain "Fog".
// Pinned by `tests/test_leaderboard_sync.py`.
export const FOG_ON_SIGHT = 1;
export const FOG_ON_SCOUT = 3;

// Pace: the board can't run mapgen, so families.mjs estimates the median lane in
// turns as ceil(PACE_LY / sqrt(min(systems, STANDARD_MAX_NODES)) / speed).
// PACE_LY is fitted on real maps (docs/design/setup-families.md, "Pace"), and
// `tests/test_leaderboard_sync.py` checks the formula against mapgen.
// SHIP_LY_PER_TURN and STANDARD_MAX_NODES mirror `starconquest.config`.
export const PACE_LY = 77;
export const SHIP_LY_PER_TURN = 6;
export const STANDARD_MAX_NODES = 40;

// Mirrors `Settings()`'s defaults for the fields families.mjs compares.
// settings_json is pruned to non-defaults, so a missing key means this value.
// Pinned by `tests/test_leaderboard_sync.py`.
export const SETUP_DEFAULTS = {
  layout: "hub",
  node_jitter: 0.85,
  relax_min_sep_frac: 0.6,
  lloyd_passes: 1,
  extra_edge_fraction: 0.4,
  max_edge_length_frac: 0.5,
  ship_ly_per_turn: 6,
  ship_speed_growth_pct: 0,
  home_start_ships: 12,
  home_production: 3,
  garrison_base: 2,
  garrison_k: 12,
  garrison_jitter: 3,
  combat_jitter: 0.1,
  defender_advantage: 1,
  neutral_produces: false,
  in_lane_battles: false,
  fog_sight: 8,
  fog_scout: 8,
};
