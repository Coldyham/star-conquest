/**
 * GET /api/replay?id=<match id> — hand back one replay for the game to watch.
 *
 * The read half of the pair with `log.mjs`, and the same shape: the game holds no
 * database key, this function does, and the browser only ever talks to this site.
 *
 * What may be served is decided in SQL, not here: `public_watchable_replays`
 * (see `schema.sql`) unions the two pools this id can name, each gated by its
 * own, unrelated rule — `public_replays` is `game_logs` restricted to the
 * matches a posted score points at (so posting a score publishes that replay,
 * and a game that merely uploaded itself because "Share replays" was on stays
 * unreadable), and a winning bot's own stored replay (`bot_scores.log`), which
 * needs no further gate here since `bot_scores` itself is already fully public.
 * This function selects from the union and nothing else; it has no `if` that
 * could drift from either rule.
 *
 * Environment: `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or the legacy
 * `SUPABASE_SERVICE_KEY`), exactly as `log.mjs` needs them. Unlike
 * `public_replays`, the union view is granted to the service key alone — a
 * bot's `match_id`/`rules_version` already ride on its own public `bot_scores`
 * row, so nothing on the site needs a second, wider door onto this view just to
 * list what is watchable.
 *
 * A signed-in player may also watch their own replay while its map is still
 * embargoed: with an `Authorization: Bearer` from the board's sign-in, an id the
 * public view has no row for is asked of `sc_my_replay` under that token, which
 * answers only for a match one of the caller's own scores points at. That
 * answer is private (`no-store`), never cached for anyone else.
 *
 * The response is the encoded log itself — `replay.GameLog.encoded` output, which
 * is base64url text — as `text/plain`, because that is precisely what
 * `GameLog.decode` wants and wrapping it in JSON would only make the game unwrap
 * it again.
 */

import { SUPABASE_ANON_KEY } from "../../js/config.mjs";

const MATCH_ID = /^[0-9a-f]{16}$/;
const BEARER = /^Bearer [A-Za-z0-9._-]+$/;

// Replays are immutable once posted: the match is over and its rows never change.
// A day of browser caching costs nothing and spares the database every reload of
// a link someone is scrubbing back and forth through.
const CACHE = "public, max-age=86400";

// Anyone may watch a replay, so unlike the upload endpoint there is no origin
// allowlist to keep — the game's site, the board itself and a local dev server
// all read the same public rows.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization",
};

function reply(status, body, headers = {}) {
  return new Response(body, { status, headers: { ...CORS, ...headers } });
}

export default async function handler(request) {
  if (request.method === "OPTIONS") return reply(204, null);
  if (request.method !== "GET") return reply(405, "GET only");

  const url = process.env.SUPABASE_URL;
  // Supabase's secret key (`sb_secret_…`), which replaced the service_role JWT —
  // that one is under its "Legacy API keys" tab now and still works, so both
  // variable names are read. Either carries the `service_role` postgres role,
  // which is what every grant in schema.sql is written against.
  const key = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return reply(503, "not configured");

  const id = new URL(request.url).searchParams.get("id") || "";
  // Checked before it reaches a query string, the same way the game checks it
  // before asking: it arrives from a URL fragment somebody else may have written.
  if (!MATCH_ID.test(id)) return reply(400, "bad id");

  const response = await fetch(
    `${url.replace(/\/$/, "")}/rest/v1/public_watchable_replays?select=log&match_id=eq.${id}`,
    { headers: { apikey: key, Authorization: `Bearer ${key}` } },
  );
  if (!response.ok) {
    console.error("public_watchable_replays read failed", response.status, await response.text());
    return reply(502, "lookup failed");
  }
  const rows = await response.json();
  // Absent and not-yet-public are the same answer on purpose: "there is no replay
  // here to watch" tells a visitor everything true without disclosing that some
  // unposted game exists under that id.
  if (Array.isArray(rows) && rows.length && typeof rows[0].log === "string") {
    return reply(200, rows[0].log, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": CACHE });
  }
  const own = await ownReplay(url, id, request.headers.get("authorization") || "");
  if (own) return reply(200, own, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" });
  return reply(404, "no replay");
}

/**
 * The caller's own replay of `id` (`sc_my_replay`, asked as the caller), or
 * null: signed out, not theirs, no replay, or any failure.
 */
async function ownReplay(url, id, authorization) {
  if (!BEARER.test(authorization) || !SUPABASE_ANON_KEY) return null;
  try {
    const response = await fetch(`${url.replace(/\/$/, "")}/rest/v1/rpc/sc_my_replay`, {
      method: "POST",
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: authorization, "Content-Type": "application/json" },
      body: JSON.stringify({ p_match_id: id }),
    });
    if (!response.ok) return null;
    const log = await response.json();
    return typeof log === "string" && log ? log : null;
  } catch {
    return null;
  }
}

export const config = { path: "/api/replay" };
