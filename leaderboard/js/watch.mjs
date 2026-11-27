// A score's Watch link, and which replays a signed-in player may watch of their
// own. Shared by the map page, the player card and the daily pages.

import { rpc } from "./api.mjs";
import { storedSession } from "./auth.mjs";
import { CURRENT_RULES_VERSION, GAME_URL } from "./config.mjs";
import { el } from "./format.mjs";

/**
 * "Watch" on a score whose replay is actually there.
 *
 * The link goes back into the *game*, not into a player here: the engine is
 * Python and the game already has the whole reviewer — `reconstruct`, the fog
 * replay and the scrubber. Reimplementing any of that in JS would be a second
 * engine to keep in step with the first.
 *
 * Offered only for ids a replay query actually returned, so it can never lead
 * to a 404: a score can name a match whose upload never arrived.
 */
export function watchLink(matchId) {
  return el("a", {
    class: "watch",
    href: `${GAME_URL}#log=${matchId}`,
    title: "Replay this game in the browser",
    text: "Watch",
  });
}

/**
 * Stands in for Watch on a score whose replay exists but predates the current
 * rules — disclosed rather than just dropped, so a historic score reads as
 * "can't replay this one" instead of looking indistinguishable from a score
 * that never had a replay at all.
 */
export function outdatedNote() {
  return el("span", {
    class: "outdated",
    title: "This replay was recorded under an earlier ruleset and can no longer be reconstructed exactly.",
    text: "Older ruleset",
  });
}

/**
 * What a score's result cell carries after its figures, from the
 * `rules_version` of its replay (undefined: no replay): Watch, the older-rules
 * note, or nothing. Nothing without GAME_URL, which there is nowhere to send a
 * watcher.
 */
export function replayCell(matchId, version) {
  if (!GAME_URL || version === undefined) return [];
  return version === CURRENT_RULES_VERSION ? [watchLink(matchId)] : [outdatedNote()];
}

/**
 * Which of `matchIds` are the signed-in caller's own replays, embargoed or
 * not (schema.sql's `sc_my_replays`), as a Map of match_id to rules_version.
 * Empty when signed out, on an older schema, or on any failure: a missing
 * Watch link is not worth taking a page down for.
 */
export async function ownReplays(matchIds) {
  const ids = [...new Set(matchIds.filter(Boolean))].slice(0, 200);
  if (!GAME_URL || !ids.length || !storedSession()) return new Map();
  try {
    const rows = await rpc("sc_my_replays", { p_match_ids: ids });
    return new Map((Array.isArray(rows) ? rows : []).map((r) => [r.match_id, r.rules_version]));
  } catch {
    return new Map();
  }
}
