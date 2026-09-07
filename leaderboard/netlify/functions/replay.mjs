/**
 * GET /api/replay?id=<match id> — hand back one replay for the game to watch.
 *
 * The read half of the pair with `log.mjs`, and the same shape: the game holds no
 * database key, this function does, and the browser only ever talks to this site.
 *
 * What may be served is decided in SQL, not here. `public_replays` (see
 * `schema.sql`) is `game_logs` restricted to the matches a posted score points
 * at — so posting a score publishes that replay, and a game that merely uploaded
 * itself because "Share replays" was on stays unreadable. This function selects
 * from the view and nothing else; it has no `if` that could drift from that rule.
 *
 * Environment: `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`, exactly as `log.mjs`
 * needs them. The view is granted to `anon` as well, so a page can list what is
 * watchable without a key; the game comes through here because it should not
 * carry one.
 *
 * The response is the encoded log itself — `replay.GameLog.encoded` output, which
 * is base64url text — as `text/plain`, because that is precisely what
 * `GameLog.decode` wants and wrapping it in JSON would only make the game unwrap
 * it again.
 */

const MATCH_ID = /^[0-9a-f]{16}$/;

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
};

function reply(status, body, headers = {}) {
  return new Response(body, { status, headers: { ...CORS, ...headers } });
}

export default async function handler(request) {
  if (request.method === "OPTIONS") return reply(204, null);
  if (request.method !== "GET") return reply(405, "GET only");

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return reply(503, "not configured");

  const id = new URL(request.url).searchParams.get("id") || "";
  // Checked before it reaches a query string, the same way the game checks it
  // before asking: it arrives from a URL fragment somebody else may have written.
  if (!MATCH_ID.test(id)) return reply(400, "bad id");

  const response = await fetch(
    `${url.replace(/\/$/, "")}/rest/v1/public_replays?select=log&match_id=eq.${id}`,
    { headers: { apikey: key, Authorization: `Bearer ${key}` } },
  );
  if (!response.ok) {
    console.error("public_replays read failed", response.status, await response.text());
    return reply(502, "lookup failed");
  }
  const rows = await response.json();
  // Absent and not-yet-public are the same answer on purpose: "there is no replay
  // here to watch" tells a visitor everything true without disclosing that some
  // unposted game exists under that id.
  if (!Array.isArray(rows) || !rows.length || typeof rows[0].log !== "string") {
    return reply(404, "no replay");
  }
  return reply(200, rows[0].log, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": CACHE });
}

export const config = { path: "/api/replay" };
