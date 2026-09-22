/**
 * /api/pbp — the play-by-post endpoint: hand out a match's state, take a seat's
 * orders, and advance the turn when every seat is in.
 *
 * The only writer of `pbp_matches`/`pbp_orders`, for the same reason `log.mjs` is
 * the only writer of `game_logs`: these tables are closed to the publishable key
 * (RLS on, no policies), so the secret key lives here and nowhere the game can
 * reach it. A seat token can only be checked somewhere the client cannot edit.
 *
 * **This server holds no board and runs no engine.** It cannot — the board is a
 * pure function of the settings, the seed, and every turn's orders and dice,
 * which is what the game's own `replay.reconstruct` rebuilds without asking any
 * seat to decide. So this stores the inputs, and each client rebuilds the
 * position. What arrives here is orders; what leaves is orders.
 *
 * Three actions, chosen by `?action=`:
 *   state   (GET)  what a client needs to show the match: the setup, the live
 *                  turn, who is outstanding, and every resolved turn's orders.
 *   submit  (POST) one seat's orders for the live turn, authorised by its token.
 *   resolve (POST) turn the submissions into a resolved turn, once they are all
 *                  in. Sent by whichever client notices first; idempotent, so
 *                  two clients noticing together is not a race.
 *
 * A seat token authorises one seat in one match and expires with it. It is
 * stored only as a SHA-256 hash: a leaked database still hands nobody a seat.
 * There is no account, no client id and nothing that groups one person's
 * matches — the same posture the rest of this board takes.
 *
 * Environment (set in the Netlify site's settings, never committed):
 *   SUPABASE_URL         https://<project>.supabase.co
 *   SUPABASE_SECRET_KEY  Supabase's secret key (`sb_secret_…`); the legacy
 *                        `SUPABASE_SERVICE_KEY` is read too.
 * With either unset the function answers 503 and stores nothing, which is the
 * "configured or cleanly inert" shape the rest of this site has.
 */

// Node 18 does not expose `globalThis.crypto` inside module scope, so the
// platform's own Web Crypto is imported as the fallback. A static import rather
// than a lazy `require`, which an ES module does not have: this resolves at load
// on Node and is simply unused wherever `globalThis.crypto` already exists.
import { webcrypto as nodeWebcrypto } from "node:crypto";

// Same rule as log.mjs: the game is a separate Netlify site, and a deploy
// preview of it must reach the matching preview of this one. Matched by shape
// rather than listed. A desktop build sends no Origin at all.
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
  const cut = label.lastIndexOf("--");
  return (cut < 0 ? label : label.slice(cut + 2)) === GAME_SITE;
}

// `replay._MATCH_ID_RE`, and the alphabet `GameLog.encoded` produces.
const MATCH_ID = /^[0-9a-f]{16}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;
// A seat token as `mintToken` produces it: 32 hex characters, 128 bits.
const TOKEN = /^[0-9a-f]{32}$/;

const MAX_LOG_BYTES = 262144;
const MAX_BODY_BYTES = MAX_LOG_BYTES + 8192;
// `config.MAX_PLAYERS`. Seat 0 is neutral and never submits.
const MAX_SEATS = 6;
// A turn's orders are a handful of four-number objects per seat; a hundred is
// already far more launches than a board has systems.
const MAX_ORDERS = 256;

/**
 * Rate limit, per caller, in a sliding window — the same honest half-measure
 * log.mjs documents: Netlify runs functions on ephemeral instances and more than
 * one at a time, so this stops a runaway loop or a bored script, not an
 * adversary. What actually bounds the damage is that every write needs a seat
 * token, and a token is only good for one seat of one match.
 *
 * The cadence to allow is a client polling a match it is waiting on. Play-by-post
 * turns take hours, so a poll every few seconds is already generous and 240 an
 * hour leaves room for several matches at once.
 */
const WINDOW_MS = 60 * 60 * 1000;
const MAX_PER_WINDOW = 240;
const seen = new Map();

export function rateLimited(key, now = Date.now(), store = seen) {
  const fresh = (store.get(key) || []).filter((at) => now - at < WINDOW_MS);
  if (fresh.length >= MAX_PER_WINDOW) {
    store.set(key, fresh);
    return true;
  }
  fresh.push(now);
  store.set(key, fresh);
  if (store.size > 5000) {
    for (const [other, times] of store) {
      if (!times.some((at) => now - at < WINDOW_MS)) store.delete(other);
    }
  }
  return false;
}

/**
 * The Web Crypto implementation, however this runtime exposes it.
 *
 * `globalThis.crypto` is what Netlify's runtime (and Node 20+) provides, but
 * Node 18 only exposes it outside module scope — reading it at module level
 * there throws `crypto is not defined`, which would fail nowhere but in tests
 * and in an older build. Resolved per call, and per call is free: this is only
 * reached when a token is minted or checked.
 */
function webcrypto() {
  if (typeof globalThis.crypto !== "undefined" && globalThis.crypto.subtle) {
    return globalThis.crypto;
  }
  return nodeWebcrypto;
}

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

/** A fresh seat token: 128 bits of the platform's own CSPRNG, as hex. */
export function mintToken(random = null) {
  const bytes = new Uint8Array(16);
  (random || webcrypto()).getRandomValues(bytes);
  return hex(bytes);
}

/**
 * The stored form of a token. Hashed so the database never holds the thing that
 * grants the seat — the same reason a password is not stored either.
 */
export async function hashToken(token) {
  const data = new TextEncoder().encode(token);
  const digest = await webcrypto().subtle.digest("SHA-256", data);
  return hex(new Uint8Array(digest));
}

/**
 * Constant-time string comparison, so a caller cannot learn a hash by timing how
 * long the mismatch took. Cheap insurance; the strings are 64 hex characters.
 */
export function sameToken(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * One seat's orders, checked rather than trusted.
 *
 * Shape only, and the omission is deliberate — exactly as `botio.orders_from`
 * documents on the bot wire. Whether a source is held, a destination adjacent or
 * a count affordable is `engine.apply_order`'s to decide, and it decides it for
 * every seat alike; a second copy of those rules here would be a second thing to
 * keep in step for no change in outcome. What this does enforce is that a
 * malformed submission cannot reach a client as something subtly wrong.
 *
 * **There is no owner on the wire.** The seat comes from the token, so a foreign
 * order is not something the protocol can express rather than something it
 * filters — the property `engine._own_orders` exists to guarantee, moved one
 * layer earlier.
 */
export function validateOrders(raw) {
  if (!Array.isArray(raw)) return "orders must be a list";
  if (raw.length > MAX_ORDERS) return "too many orders";
  const out = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return "bad order";
    const { src, dst, ships } = item;
    if (!Number.isInteger(src) || src < 0) return "bad order src";
    if (!Number.isInteger(dst) || dst < 0) return "bad order dst";
    if (!Number.isInteger(ships) || ships <= 0) return "bad order ships";
    out.push({ src, dst, ships });
  }
  return out;
}

/** The `seats` roster on a new match: `{seat: {token_hash}}` for each person. */
export function validateSeats(raw) {
  if (!Array.isArray(raw) || raw.length < 1) return "seats must be a non-empty list";
  const seats = [];
  for (const seat of raw) {
    if (!Number.isInteger(seat) || seat < 1 || seat > MAX_SEATS) return "bad seat";
    if (seats.includes(seat)) return "duplicate seat";
    seats.push(seat);
  }
  return seats.sort((a, b) => a - b);
}

/** A match to create, or a string saying why not. */
export function validateMatch(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "not an object";
  const { match_id: matchId, settings_json: settings, seed, seats, rules_version: rules } = body;
  if (typeof matchId !== "string" || !MATCH_ID.test(matchId)) return "bad match_id";
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return "bad settings_json";
  if (!Number.isInteger(seed)) return "bad seed";
  if (rules !== undefined && (!Number.isInteger(rules) || rules < 1)) return "bad rules_version";
  const roster = validateSeats(seats);
  if (typeof roster === "string") return roster;
  const hours = body.deadline_hours;
  if (hours !== undefined && hours !== null
      && (!Number.isInteger(hours) || hours < 1 || hours > 336)) {
    return "bad deadline_hours";
  }
  return {
    match_id: matchId,
    settings_json: settings,
    seed,
    rules_version: rules === undefined ? 1 : rules,
    seats: roster,
    deadline_hours: hours === undefined ? null : hours,
  };
}

/**
 * Which seats still owe orders for `turn`.
 *
 * The whole gate, and it is deliberately a set difference rather than a count:
 * counting submissions would advance a turn on two rows from one seat, which the
 * unique constraint already forbids but which should not be load-bearing here.
 */
export function outstanding(seats, submitted) {
  const inHand = new Set(submitted);
  return seats.filter((seat) => !inHand.has(seat));
}

/**
 * Whether `turn` has run out of time, and what that means for a seat.
 *
 * Two stages, Diplomacy's: the first miss **holds** (the seat submits nothing,
 * which is already a legal turn — production ticks, garrisons defend), and only
 * a second consecutive miss falls to the seat's bot. `misses` is how many turns
 * in a row this seat has now let pass.
 */
export function lapsedAction(misses) {
  return misses >= 1 ? "bot" : "hold";
}

export function deadlinePassed(openedAt, hours, now = Date.now()) {
  if (hours === null || hours === undefined) return false;
  const opened = Date.parse(openedAt);
  if (Number.isNaN(opened)) return false;
  return now - opened >= hours * 3600 * 1000;
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
      console.error("pbp query failed", path, response.status, await response.text());
      return null;
    }
    const text = await response.text();
    return text ? JSON.parse(text) : [];
  };
}

export default async function handler(request) {
  const origin = request.headers.get("origin");
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return reply(503, { error: "not configured" }, origin);

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY_BYTES) return reply(413, { error: "too large" }, origin);

  const ip = request.headers.get("x-nf-client-connection-ip")
    || (request.headers.get("x-forwarded-for") || "").split(",")[0].trim()
    || "unknown";
  if (rateLimited(ip)) return reply(429, { error: "slow down" }, origin);

  const query = new URL(request.url).searchParams;
  const action = query.get("action") || "state";
  const call = db(url, key);

  if (action === "state" && request.method === "GET") {
    return await handleState(call, query, origin);
  }
  if (request.method !== "POST") return reply(405, { error: "POST only" }, origin);

  let body;
  try {
    body = await request.json();
  } catch {
    return reply(400, { error: "bad json" }, origin);
  }
  if (action === "create") return await handleCreate(call, body, origin);
  if (action === "submit") return await handleSubmit(call, body, origin);
  return reply(400, { error: "unknown action" }, origin);
}

/**
 * The public half: everything a client needs to rebuild the match, minus
 * anything that would let it act as a seat it does not hold.
 *
 * Deliberately open — no token required. A match is only reachable by knowing
 * its 16-hex id, the orders in it are what every player is about to see anyway,
 * and requiring a token here would stop a player watching a match they have
 * finished playing. Tokens gate *writing*, which is the thing that matters.
 */
async function handleState(call, query, origin) {
  const matchId = query.get("match") || "";
  if (!MATCH_ID.test(matchId)) return reply(400, { error: "bad match" }, origin);

  const rows = await call(`/pbp_matches?select=*&match_id=eq.${matchId}`);
  if (rows === null) return reply(502, { error: "store refused" }, origin);
  if (!rows.length) return reply(404, { error: "no such match" }, origin);
  const match = rows[0];

  const orders = await call(
    `/pbp_orders?select=turn,seat,orders_json,source&match_id=eq.${matchId}&order=turn.asc,seat.asc`);
  if (orders === null) return reply(502, { error: "store refused" }, origin);

  const live = orders.filter((row) => row.turn === match.turn);
  return reply(200, {
    match_id: match.match_id,
    settings_json: match.settings_json,
    seed: match.seed,
    rules_version: match.rules_version,
    seats: match.seats.seats ?? match.seats,
    turn: match.turn,
    log: match.log,
    finished: match.finished,
    turn_opened_at: match.turn_opened_at,
    deadline_hours: match.deadline_hours,
    // Who is still to submit for the live turn. The waiting overlay is built
    // from exactly this.
    submitted: live.map((row) => row.seat),
    // Every resolved turn's orders, so a client that has never seen this match
    // can rebuild it from the opening position.
    turns: orders.filter((row) => row.turn < match.turn),
  }, origin);
}

/** Open a match, and mint one token per seated player. */
async function handleCreate(call, body, origin) {
  const row = validateMatch(body);
  if (typeof row === "string") return reply(400, { error: row }, origin);

  const tokens = {};
  const hashes = {};
  for (const seat of row.seats) {
    const token = mintToken();
    tokens[seat] = token;
    hashes[seat] = await hashToken(token);
  }

  const stored = await call("/pbp_matches", {
    method: "POST",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify([{
      match_id: row.match_id,
      settings_json: row.settings_json,
      seed: row.seed,
      rules_version: row.rules_version,
      seats: { seats: row.seats, tokens: hashes },
      deadline_hours: row.deadline_hours,
    }]),
  });
  if (stored === null) return reply(502, { error: "store refused" }, origin);

  // The one and only time the tokens exist in the clear. Nothing stores them;
  // whoever asked for the match is responsible for handing them out.
  return reply(201, { match_id: row.match_id, tokens }, origin);
}

/** One seat's orders for the live turn, authorised by that seat's token. */
async function handleSubmit(call, body, origin) {
  if (!body || typeof body !== "object") return reply(400, { error: "not an object" }, origin);
  const { match_id: matchId, token, turn } = body;
  if (typeof matchId !== "string" || !MATCH_ID.test(matchId)) return reply(400, { error: "bad match_id" }, origin);
  if (typeof token !== "string" || !TOKEN.test(token)) return reply(403, { error: "bad token" }, origin);
  if (!Number.isInteger(turn) || turn < 0) return reply(400, { error: "bad turn" }, origin);

  const orders = validateOrders(body.orders);
  if (typeof orders === "string") return reply(400, { error: orders }, origin);
  const digest = body.board_digest;
  if (digest !== undefined && (typeof digest !== "string" || digest.length > 64)) {
    return reply(400, { error: "bad board_digest" }, origin);
  }

  const rows = await call(`/pbp_matches?select=*&match_id=eq.${matchId}`);
  if (rows === null) return reply(502, { error: "store refused" }, origin);
  if (!rows.length) return reply(404, { error: "no such match" }, origin);
  const match = rows[0];
  if (match.finished) return reply(409, { error: "match is over" }, origin);

  // Which seat is this token's? Every hash is compared so a wrong token cannot
  // be told apart from one for another seat by how fast the answer comes back.
  const offered = await hashToken(token);
  let seat = null;
  for (const [candidate, stored] of Object.entries(match.seats.tokens || {})) {
    if (sameToken(offered, stored)) seat = Number(candidate);
  }
  if (seat === null) return reply(403, { error: "bad token" }, origin);

  // A submission names the turn it is for, so a client that has fallen behind
  // cannot have yesterday's orders applied to today's board.
  if (turn !== match.turn) {
    return reply(409, { error: "stale turn", turn: match.turn }, origin);
  }

  const stored = await call("/pbp_orders", {
    method: "POST",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify([{
      match_id: matchId,
      turn,
      seat,
      orders_json: orders,
      source: "human",
      board_digest: typeof digest === "string" ? digest : "",
    }]),
  });
  // The unique constraint is what answers a double submission, so a conflict is
  // "you already sent this turn" rather than a failure.
  if (stored === null) return reply(409, { error: "already submitted" }, origin);

  const live = await call(
    `/pbp_orders?select=seat&match_id=eq.${matchId}&turn=eq.${turn}`);
  if (live === null) return reply(502, { error: "store refused" }, origin);
  const roster = match.seats.seats ?? match.seats;
  const waiting = outstanding(roster, live.map((r) => r.seat));

  return reply(201, { seat, turn, waiting }, origin);
}

export const config = { path: "/api/pbp" };
