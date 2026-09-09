/**
 * POST /api/log — accept one game replay from the game and store it.
 *
 * The only writer of `game_logs`. Everything else on this board inserts straight
 * into PostgREST with the public publishable key, and that is fine for a score: a
 * bad row is a hundred bytes and a person with the SQL editor. A replay is 5-14
 * KiB, so an insert path anyone can aim a script at is a storage bill rather than
 * a nuisance — hence a function in front of it, holding the only key that may
 * write the table (`schema.sql` gives `game_logs` no insert policy and no public
 * grant).
 *
 * What it does, in order: reject anything but a POST, rate-limit the caller,
 * check the row's shape and size, then forward it under the secret key.
 *
 * Environment (set in the Netlify site's settings, never committed):
 *   SUPABASE_URL         https://<project>.supabase.co
 *   SUPABASE_SECRET_KEY  Supabase's secret key (`sb_secret_…`) — the same one the
 *                        GitHub Actions worker uses, and just as much not-in-git.
 *                        `SUPABASE_SERVICE_KEY` is read too, for the legacy
 *                        service_role JWT that key replaced.
 *
 * With either unset the function answers 503 and stores nothing, which is the
 * same "configured or cleanly inert" shape the rest of the board has.
 */

// A browser upload is cross-origin (the game is a separate Netlify site), so the
// preflight has to be answered with an origin the browser will accept. `*` would
// do; this keeps the surface to the game's own deploys instead.
//
// Matched by shape rather than listed, because the deploys that matter are
// contextual: `deploy-preview-42--star-conquest.netlify.app` is as real a caller
// as production, and a preview of the game posts to the preview of this site
// (see `config.mjs`, and `paths.sibling_host` in the game). A desktop build sends
// no Origin header at all, and CORS has nothing to say about it.
const GAME_SITE = "star-conquest";
const LOCAL = ["http://localhost:8000", "http://127.0.0.1:8000"];

export function allowedOrigin(origin) {
  if (!origin) return false;
  if (LOCAL.includes(origin)) return true;
  let host;
  try {
    const url = new URL(origin);
    if (url.protocol !== "https:") return false;
    host = url.hostname;
  } catch {
    return false;
  }
  if (!host.endsWith(".netlify.app")) return false;
  const label = host.slice(0, -".netlify.app".length);
  // The site name is the last `--`-separated part — and the whole label when
  // there is no context prefix, which is production. So a prefix is allowed and a
  // *different site* that merely ends in ours is not.
  const cut = label.lastIndexOf("--");
  return (cut < 0 ? label : label.slice(cut + 2)) === GAME_SITE;
}

// Generous against the 13.6 KiB worst case measured across whole games, and the
// same bound `game_logs`' own check constraint enforces — this one exists to
// refuse a big body before it is parsed, rather than after.
const MAX_LOG_BYTES = 262144;
const MAX_BODY_BYTES = MAX_LOG_BYTES + 4096;

// `replay._MATCH_ID_RE`, and the alphabet `GameLog.encoded` produces.
const MATCH_ID = /^[0-9a-f]{16}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;

/**
 * Rate limit: per-caller uploads in a sliding window.
 *
 * Honest about what this is. Netlify runs functions on ephemeral instances and
 * more than one at a time, so an in-memory window is per-instance and a
 * determined flood spread across instances gets through — it stops a runaway
 * loop or a bored script, not an adversary. The bounds that actually hold are
 * `MAX_LOG_BYTES` and the table's own check constraint, plus the fact that
 * dropping the table is one statement in the SQL editor.
 *
 * A durable limit would mean storing caller addresses, and a table of everyone's
 * IP is a worse thing to own than the abuse it would prevent here.
 *
 * The cadence it has to allow is a real game's: a checkpoint every 25 turns plus
 * one on the last turn, which on a fast match is a handful of minutes apart. 30
 * an hour leaves room for several games at once and still caps one caller at a
 * few hundred KiB.
 */
const WINDOW_MS = 60 * 60 * 1000;
const MAX_PER_WINDOW = 30;
const seen = new Map();

export function rateLimited(key, now = Date.now(), store = seen) {
  const fresh = (store.get(key) || []).filter((at) => now - at < WINDOW_MS);
  if (fresh.length >= MAX_PER_WINDOW) {
    store.set(key, fresh);
    return true;
  }
  fresh.push(now);
  store.set(key, fresh);
  // Housekeeping: without this the map grows one key per caller for the life of
  // the instance. Cheap because it only runs on an accepted request.
  if (store.size > 5000) {
    for (const [other, times] of store) {
      if (!times.some((at) => now - at < WINDOW_MS)) store.delete(other);
    }
  }
  return false;
}

/**
 * The row to store, or a string saying why not.
 *
 * Everything is checked rather than trusted: this is an unauthenticated endpoint,
 * and `match_id` in particular is the column the verifier keys scores against, so
 * a value of the wrong shape must never reach it. `finished`, `won` and `hand`
 * are claims by the client and are stored as claims — an index for finding logs,
 * never evidence, since replaying the log settles all three.
 */
export function validate(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "not an object";
  const { match_id: matchId, game_key: gameKey, turns, log, hand } = body;

  if (typeof matchId !== "string" || !MATCH_ID.test(matchId)) return "bad match_id";
  if (typeof gameKey !== "string" || !gameKey || gameKey.length > 64) return "bad game_key";
  if (!Number.isInteger(turns) || turns < 1 || turns > 100000) return "bad turns";
  if (typeof log !== "string" || !log || log.length > MAX_LOG_BYTES) return "bad log";
  // base64url only: the blob goes into the database as text, and there is no
  // reason for anything else to be in it.
  if (!BASE64URL.test(log)) return "bad log encoding";
  if (hand !== undefined && (!Number.isInteger(hand) || hand < 0 || hand > turns)) {
    return "bad hand";
  }
  return {
    match_id: matchId,
    game_key: gameKey,
    turns,
    finished: body.finished === true,
    won: body.won === true,
    hand: hand === undefined ? 0 : hand,
    log,
  };
}

function cors(origin) {
  const headers = { "Cache-Control": "no-store" };
  if (allowedOrigin(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Headers"] = "Content-Type";
    headers["Access-Control-Allow-Methods"] = "POST, OPTIONS";
    headers["Vary"] = "Origin";
  }
  return headers;
}

function reply(status, body, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors(origin), "Content-Type": "application/json" },
  });
}

export default async function handler(request) {
  const origin = request.headers.get("origin");
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  if (request.method !== "POST") return reply(405, { error: "POST only" }, origin);

  const url = process.env.SUPABASE_URL;
  // Supabase's secret key (`sb_secret_…`), which replaced the service_role JWT —
  // that one is under its "Legacy API keys" tab now and still works, so both
  // variable names are read. Either carries the `service_role` postgres role,
  // which is what every grant in schema.sql is written against.
  const key = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return reply(503, { error: "not configured" }, origin);

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY_BYTES) return reply(413, { error: "too large" }, origin);

  // Netlify's own client-IP header, with the standard forwarded chain as the
  // fallback; an unknown caller shares one bucket rather than escaping the limit.
  const ip = request.headers.get("x-nf-client-connection-ip")
    || (request.headers.get("x-forwarded-for") || "").split(",")[0].trim()
    || "unknown";
  if (rateLimited(ip)) return reply(429, { error: "slow down" }, origin);

  let body;
  try {
    body = await request.json();
  } catch {
    return reply(400, { error: "bad json" }, origin);
  }
  const row = validate(body);
  if (typeof row === "string") return reply(400, { error: row }, origin);
  // Checked after parsing too: content-length is the client's claim, and a
  // chunked upload does not have to send one at all.
  if (row.log.length > MAX_LOG_BYTES) return reply(413, { error: "too large" }, origin);

  const response = await fetch(`${url.replace(/\/$/, "")}/rest/v1/game_logs`, {
    method: "POST",
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify([row]),
  });
  if (!response.ok) {
    // Never relay PostgREST's message: it is written for whoever holds the
    // service key, and this caller is not that.
    console.error("game_logs insert failed", response.status, await response.text());
    return reply(502, { error: "store refused" }, origin);
  }
  return reply(201, { stored: row.match_id, turns: row.turns }, origin);
}

export const config = { path: "/api/log" };
