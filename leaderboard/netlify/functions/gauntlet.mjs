/**
 * /api/gauntlet — store a gauntlet built on the board, and hand one back by id.
 *
 *   POST {gauntlet, by_name?, wants_listing?}  check the gauntlet, mint its id
 *        and store it; answers {id}. Saving the same gauntlet again is a no-op
 *        that answers the same id, since the id is a hash of the content.
 *   GET  ?id=<gauntlet_id>  {id, gauntlet, by_name, created_at}, or 404. What
 *        the game fetches when it opens a `#g=<id>` link.
 *   GET  ?by=<name>  {made}: how many gauntlets were saved under that author
 *        name, case folded as `users.name_key` folds it. The player card's
 *        Architect badge. A count only: an unlisted row's id never leaves here.
 *
 * The only writer of `gauntlets`, for log.mjs's reason: a gauntlet is up to 64
 * KiB of free text, so an insert path anyone can aim a script at would be a
 * storage bill. The table has no public insert grant; this holds the secret key.
 * The checking is `checkBody` in js/gauntlet-build.mjs, the same function the
 * page runs before it offers Save, so the two can never disagree about what a
 * gauntlet may be.
 *
 * Reads come through here too rather than straight from PostgREST, because only
 * a *listed* row is readable with the publishable key (schema.sql). Knowing an
 * id is the permission to read it, as it is for a play-by-post match.
 *
 * Environment: SUPABASE_URL and SUPABASE_SECRET_KEY (or the legacy
 * SUPABASE_SERVICE_KEY), as for log.mjs. With either unset it answers 503.
 */

import { webcrypto as nodeWebcrypto } from "node:crypto";

import { GAUNTLET_ID, LIMITS, checkBody, checkByName, mintId } from "../../js/gauntlet-build.mjs";
import { allowedOrigin, rateLimited } from "./log.mjs";

const MAX_BODY_BYTES = LIMITS.bodyBytes + 4096;

// Saves are what a flood would cost, and a person saves a handful an hour at
// most. A read is one small row, fetched once per link opened (the game keeps
// its own copy afterwards), so its budget is sized for a classroom behind one
// address all opening the same link.
const MAX_SAVES_PER_WINDOW = 30;
const MAX_READS_PER_WINDOW = 600;
const saves = new Map();
const reads = new Map();

const subtle = () => (globalThis.crypto?.subtle || nodeWebcrypto.subtle);

/**
 * The row to store for a POST body, or a string saying why not. Async because
 * minting the id hashes the body.
 */
export async function rowFor(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return "not an object";
  const body = checkBody(payload.gauntlet);
  if (typeof body === "string") return body;
  const byName = checkByName(payload.by_name ?? "");
  if (byName === null) return `the author's name is over ${LIMITS.byName} characters`;
  const id = await mintId(body, subtle());
  return {
    gauntlet_id: id,
    body: { id, ...body },
    by_name: byName,
    wants_listing: payload.wants_listing === true,
  };
}

function cors(origin) {
  const headers = { "Cache-Control": "no-store" };
  if (allowedOrigin(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Headers"] = "Content-Type";
    headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
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

/** PostgREST under the secret key. Its errors are logged, never relayed. */
function db(url, key) {
  const base = `${url.replace(/\/$/, "")}/rest/v1`;
  return async function call(path, init = {}) {
    const response = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
        ...(init.headers || {}),
      },
    });
    if (!response.ok) {
      console.error("gauntlets query failed", path, response.status, await response.text());
      return null;
    }
    const text = await response.text();
    return text ? JSON.parse(text) : [];
  };
}

async function handleRead(call, query, origin) {
  const id = query.get("id") || "";
  if (!GAUNTLET_ID.test(id)) return reply(400, { error: "bad id" }, origin);
  const rows = await call(`/gauntlets?select=gauntlet_id,body,by_name,created_at&gauntlet_id=eq.${id}`);
  if (rows === null) return reply(502, { error: "lookup failed" }, origin);
  if (!rows.length) return reply(404, { error: "no such gauntlet" }, origin);
  const row = rows[0];
  return reply(200, { id: row.gauntlet_id, gauntlet: row.body, by_name: row.by_name,
                      created_at: row.created_at }, origin);
}

/** How many `by_name`s fold to `name`, as `users.name_key` folds (lower, trimmed). */
export function madeBy(rows, name) {
  const key = String(name || "").trim().toLowerCase();
  return key ? rows.filter((row) => String(row.by_name || "").trim().toLowerCase() === key).length : 0;
}

async function handleMade(call, query, origin) {
  const name = query.get("by") || "";
  // A board name runs to 60 (users.name); past LIMITS.byName it simply made none.
  if (!name.trim() || name.length > 60) return reply(400, { error: "bad name" }, origin);
  // Every signed row, folded here: an ilike filter would read a name's own % or _
  // as a wildcard. The table is one row per saved gauntlet, so this stays small.
  const rows = await call("/gauntlets?select=by_name&by_name=neq.");
  if (rows === null) return reply(502, { error: "lookup failed" }, origin);
  return reply(200, { made: madeBy(rows, name) }, origin);
}

async function handleSave(call, payload, origin) {
  const row = await rowFor(payload);
  if (typeof row === "string") return reply(400, { error: row }, origin);
  // `ignore-duplicates` is ON CONFLICT DO NOTHING: the same gauntlet saved twice
  // is the same row, and the first save's author and listing request stand.
  const stored = await call("/gauntlets", {
    method: "POST",
    headers: { Prefer: "resolution=ignore-duplicates,return=minimal" },
    body: JSON.stringify([row]),
  });
  if (stored === null) return reply(502, { error: "store refused" }, origin);
  return reply(201, { id: row.gauntlet_id }, origin);
}

export default async function handler(request) {
  const origin = request.headers.get("origin");
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  if (request.method !== "GET" && request.method !== "POST") {
    return reply(405, { error: "GET or POST only" }, origin);
  }

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return reply(503, { error: "not configured" }, origin);

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY_BYTES) return reply(413, { error: "too large" }, origin);

  const ip = request.headers.get("x-nf-client-connection-ip")
    || (request.headers.get("x-forwarded-for") || "").split(",")[0].trim()
    || "unknown";
  const reading = request.method === "GET";
  const limited = reading
    ? rateLimited(ip, Date.now(), reads, MAX_READS_PER_WINDOW)
    : rateLimited(ip, Date.now(), saves, MAX_SAVES_PER_WINDOW);
  if (limited) return reply(429, { error: "slow down" }, origin);

  const call = db(url, key);
  if (reading) {
    const query = new URL(request.url).searchParams;
    return query.has("by") ? await handleMade(call, query, origin) : await handleRead(call, query, origin);
  }

  let text;
  try {
    text = await request.text();
  } catch {
    return reply(400, { error: "bad body" }, origin);
  }
  // Checked again after reading: content-length is the client's claim, and a
  // chunked upload need not send one.
  if (text.length > MAX_BODY_BYTES) return reply(413, { error: "too large" }, origin);
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    return reply(400, { error: "bad json" }, origin);
  }
  return await handleSave(call, payload, origin);
}

export const config = { path: "/api/gauntlet" };
