// The main list's filters, sorts and search: URL <-> filter state <-> the
// PostgREST query. No DOM and no fetch, so tests/listing.test.mjs can check the
// exact query a view sends — the same split as standings.mjs and setup.mjs.
//
// Every filter lives in the URL, so every filtered view is a shareable link,
// and every filter runs server-side: filtering only the loaded page would
// silently hide older maps instead.

import { contains, eq } from "./api.mjs";

// Columns spelled out rather than `select=*`: game_summary carries
// settings_json for the badge label, and that's a real payload increase for a
// 50-row page — worth it, but worth stating rather than growing silently the
// next time a column is appended.
export const GAME_COLUMNS = [
  "game_key", "mode", "players", "nodes", "seed", "last_activity", "score_count",
  "best_turns", "best_lost", "best_hand", "best_by_name", "best_user_name", "best_holders",
  "settings_json", "config_key", "bots", "config_name", "config_tags",
  "bot_turns", "bot_lost", "bot_name", "embargo_until", "contenders", "fog",
].join(",");

// config_summary's columns are already a curated, fixed set (schema.sql), so
// select=* is fine here unlike game_summary above.
const CONFIG_COLUMNS = "*";

// Each order ends on the view's own key: last_activity ties (and nulls), and
// offset paging over a tied order can repeat or skip a row between pages.
const RECENT = "last_activity.desc.nullslast";
export const SORTS = {
  game: [
    { value: "recent", label: "Recent activity", order: `${RECENT},game_key` },
    { value: "scores", label: "Most scores", order: `score_count.desc,${RECENT},game_key` },
    { value: "players", label: "Most players", order: `contenders.desc,${RECENT},game_key` },
    { value: "new", label: "Newest maps", order: "first_seen_at.desc,game_key" },
  ],
  config: [
    { value: "recent", label: "Recent activity", order: `${RECENT},config_key` },
    { value: "scores", label: "Most scores", order: `score_count.desc,${RECENT},config_key` },
    { value: "maps", label: "Most maps", order: `game_count.desc,${RECENT},config_key` },
  ],
};

export const MODES = ["random", "symmetric"];
export const PLAYER_COUNTS = [2, 3, 4, 5, 6];

// The longest search kept. A search is a convenience over a name or a tag,
// and anything longer is a paste accident.
const MAX_QUERY = 40;

// Most of a player's maps listed by key in one `not.in` — a game_key is 16
// hex characters, so this keeps the query string well under any proxy's URL
// limit. Past it, the page drops the rest of what it already knows was played
// as rows arrive (`unplayedBy` in home.mjs).
export const MAX_EXCLUDED = 300;

/**
 * "group=config" only means something above a single config's own game list,
 * so a `config` filter always falls back to the per-game view, whatever
 * `?group=` says — that keeps a hand-edited URL from landing on a grouped view
 * of one group.
 */
export function viewKind(filters) {
  return filters.group === "config" && !filters.config ? "config" : "game";
}

/** The filter state a query string asks for, with anything unknown dropped. */
export function filtersFromParams(params) {
  const group = params.get("group") === "config" ? "config" : "game";
  const players = Number(params.get("players"));
  const mode = params.get("mode");
  const fog = params.get("fog");
  const filters = {
    config: params.get("config") || "",
    bot: params.get("bot") || "",
    group,
    q: cleanQuery(params.get("q") || ""),
    sort: "recent",
    players: PLAYER_COUNTS.includes(players) ? players : 0,
    mode: MODES.includes(mode) ? mode : "",
    fog: fog === "on" || fog === "off" ? fog : "",
    contested: params.get("contested") === "1",
    botlead: params.get("botlead") === "1",
    unplayed: params.get("unplayed") === "1",
    campaign: params.get("campaign") === "1",
  };
  const sort = params.get("sort");
  if (SORTS[viewKind(filters)].some((s) => s.value === sort)) filters.sort = sort;
  return filters;
}

/** The link to a view: only the params that differ from the default view. */
export function urlFor(filters) {
  const params = new URLSearchParams();
  if (filters.config) params.set("config", filters.config);
  if (filters.bot) params.set("bot", filters.bot);
  if (filters.group === "config") params.set("group", "config");
  if (filters.q) params.set("q", filters.q);
  if (filters.sort && filters.sort !== "recent") params.set("sort", filters.sort);
  if (filters.players) params.set("players", String(filters.players));
  if (filters.mode) params.set("mode", filters.mode);
  if (filters.fog) params.set("fog", filters.fog);
  if (filters.contested) params.set("contested", "1");
  if (filters.botlead) params.set("botlead", "1");
  if (filters.unplayed) params.set("unplayed", "1");
  if (filters.campaign) params.set("campaign", "1");
  const qs = params.toString();
  return qs ? `index.html?${qs}` : "index.html";
}

/** True when anything narrows the list (a sort or the grouping does not). */
export function isFiltered(filters) {
  return Boolean(filters.config || filters.bot || filters.q || filters.players || filters.mode ||
    filters.fog || filters.contested || filters.botlead || filters.unplayed || filters.campaign);
}

/**
 * A search term with PostgREST's own syntax taken out of it: letters (any
 * script), digits, spaces, `+`, `-` and `_`. Commas, dots, quotes, brackets
 * and `*` would otherwise be read as the `or=(…)` list's structure. A name with
 * other punctuation in it is still found by the part around it.
 */
export function cleanQuery(raw) {
  return raw.replace(/[^\p{L}\p{N} +_-]+/gu, " ").replace(/\s+/g, " ").trim().slice(0, MAX_QUERY).trim();
}

/**
 * The `or=(…)` for a search, or "" for none: the config's name, its tags, and
 * on the per-game view the leader's name and — for a bare number — the seed.
 * Values are double-quoted inside the list (cleanQuery has already removed
 * the quote character) and the whole list encoded once.
 */
export function searchClause(q, kind) {
  if (!q) return "";
  const like = `"*${q}*"`;
  const terms = [`config_name.ilike.${like}`, `config_tags.cs.{"${q.toLowerCase()}"}`];
  if (kind === "game") {
    terms.push(`best_user_name.ilike.${like}`);
    if (/^\d{1,9}$/.test(q)) terms.push(`seed.eq.${q}`);
  }
  return `&or=${encodeURIComponent(`(${terms.join(",")})`)}`;
}

const quoted = (keys) => keys.map((key) => encodeURIComponent(`"${key}"`)).join(",");

/** `not.in.("a","b")` over a player's own maps, capped at MAX_EXCLUDED. */
function notIn(keys) {
  return `not.in.(${quoted(keys.slice(0, MAX_EXCLUDED))})`;
}

/**
 * The list query for a view: `{kind, query}`, where kind is "game" or
 * "config". `played` is the game_keys this browser's player has a score on,
 * newest first — only read when `filters.unplayed` is set. `nodes` is this
 * week's campaign maps' game_keys (a handful), read when `filters.campaign`
 * is; a week with none on the board matches nothing rather than everything.
 * The per-map-only filters (contested, bot leads, unplayed, campaign) are
 * dropped on the grouped view: a config has no single record to be contested
 * or led, and is never itself a campaign node.
 */
export function listQuery(filters, { played = [], nodes = [] } = {}) {
  const kind = viewKind(filters);
  const sort = SORTS[kind].find((s) => s.value === filters.sort) || SORTS[kind][0];
  let query = kind === "config"
    ? `config_summary?select=${CONFIG_COLUMNS}`
    : `game_summary?select=${GAME_COLUMNS}&score_count=gt.0`;
  if (kind === "game" && filters.config) query += `&config_key=${eq(filters.config)}`;
  if (filters.bot) query += `&bots=${contains([filters.bot])}`;
  if (filters.players) query += `&players=eq.${filters.players}`;
  if (filters.mode) query += `&mode=${eq(filters.mode)}`;
  if (filters.fog) query += `&fog=is.${filters.fog === "on"}`;
  if (kind === "game") {
    if (filters.contested) query += "&contenders=gte.2";
    if (filters.botlead) query += "&bot_leads=is.true";
    if (filters.unplayed && played.length) query += `&game_key=${notIn(played)}`;
    if (filters.campaign) query += nodes.length ? `&game_key=in.(${quoted(nodes)})` : "&game_key=is.null";
  }
  query += searchClause(filters.q, kind);
  query += `&order=${sort.order}`;
  return { kind, query };
}

// How many configs a random pick draws from. Far more setups than the board
// has today; past it the pick is among the most recently active.
export const RANDOM_POOL = 1000;

/**
 * The configs "Random setup" picks among: every config the grouped view would
 * list under these filters, minus hand-drawn maps — the same pool
 * tools/campaign.py's `families` draws a node from, since a fresh seed on a
 * hand-drawn map changes only its star names and dice.
 */
export function randomPoolQuery(filters) {
  const { query } = listQuery({ ...filters, config: "", group: "config", sort: "recent" });
  return query.replace("config_summary?select=*", "config_summary?select=config_key,settings_json") +
    `&settings_json->custom_map=is.null&limit=${RANDOM_POOL}`;
}

/** One row, uniformly — or null from an empty pool. `random` is injectable for tests. */
export function pickRandom(rows, random = Math.random) {
  if (!rows.length) return null;
  return rows[Math.min(rows.length - 1, Math.floor(random() * rows.length))];
}
