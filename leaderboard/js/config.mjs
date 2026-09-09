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
// Usually derived rather than used: this site and the game's are two Netlify
// sites whose names differ by exactly `-leaderboard`, and Netlify names every
// other deploy `<context>--<site>.netlify.app` from the same context on both,
// since they build from one repository. So dropping the tag from our own host
// finds the game build that matches this one — a deploy preview of the board
// links to the deploy preview of the game, with nothing to edit by hand.
// `starconquest/paths.py`'s `sibling_host` is the same rule from the other side.
//
// The constant is the fallback for any host that rule cannot read: a custom
// domain, a local server, a file:// page.
const GAME_URL_FALLBACK = "https://star-conquest.netlify.app/";
const TAG = "-leaderboard";
const NETLIFY = ".netlify.app";

/** Our sibling's origin, or "" if this host says nothing about where it is. */
function siblingGame() {
  const host = (globalThis.location && globalThis.location.hostname) || "";
  if (!host.endsWith(NETLIFY)) return "";
  const label = host.slice(0, -NETLIFY.length);
  const cut = label.lastIndexOf("--");
  const [prefix, site] = cut < 0 ? ["", label] : [label.slice(0, cut + 2), label.slice(cut + 2)];
  if (!site.endsWith(TAG)) return "";        // not the board; nothing to drop
  const game = site.slice(0, -TAG.length);
  return game ? `https://${prefix}${game}${NETLIFY}/` : "";
}

export const GAME_URL = siblingGame() || GAME_URL_FALLBACK;
