// PostgREST, which is all Supabase's REST API is, over plain fetch.
//
// No client library: the handful of queries this site makes are shorter as URLs
// than the code to load a bundle would be, and it keeps the deploy self-contained.
//
// Writes go through insert() only. There is no update() or remove() here because
// there are no UPDATE/DELETE policies to call them with — see schema.sql.

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./config.mjs";

export const UNIQUE_VIOLATION = "23505";

export function configured() {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(path, options = {}) {
  if (!configured()) {
    throw new ApiError("The leaderboard is not connected to its database yet.", 0, "unconfigured");
  }
  let res;
  try {
    res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
      ...options,
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        "Content-Type": "application/json",
        ...options.headers,
      },
    });
  } catch (err) {
    throw new ApiError(`Couldn't reach the leaderboard: ${err.message}`, 0, "network");
  }

  if (!res.ok) {
    // PostgREST puts the Postgres error code in the body; it's how the caller
    // tells "someone else just inserted this" from a real failure.
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.message || `Request failed (${res.status})`, res.status, body.code);
  }
  // A `return=minimal` insert answers 201 with no body at all, so this cannot
  // just be res.json() — that throws on empty input.
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

/** GET a PostgREST query string, e.g. `games?select=*&game_key=eq.abc`. */
export function select(query) {
  return request(query);
}

/** POST one row. `returning` asks for the inserted row back (needed for ids). */
export function insert(table, row, { returning = false } = {}) {
  return request(table, {
    method: "POST",
    body: JSON.stringify(row),
    headers: { Prefer: returning ? "return=representation" : "return=minimal" },
  });
}

export const eq = (value) => `eq.${encodeURIComponent(value)}`;

/**
 * `in.("a","b")` — every value quoted, since PostgREST reads a bare comma, dot,
 * colon or bracket inside the list as syntax. Player names are free text, so
 * assume the worst: quotes and backslashes are escaped, and each value is encoded
 * on its own so its own commas can't be mistaken for the list's separators.
 */
export const inList = (values) =>
  `in.(${[...values]
    .map((value) => encodeURIComponent(`"${String(value).replace(/["\\]/g, "\\$&")}"`))
    .join(",")})`;

/**
 * `cs.{"a","b"}` — PostgREST's array-contains filter, quoted the same way as
 * inList() and for the same reason: bot names and tags are free text, and the
 * query string is percent-decoded *before* PostgREST parses it, so a literal
 * `+` or space must survive that round trip (encodeURIComponent handles both;
 * a hand-built template string would turn a `+` into a space).
 */
export const contains = (values) =>
  `cs.{${[...values]
    .map((value) => encodeURIComponent(`"${String(value).replace(/["\\]/g, "\\$&")}"`))
    .join(",")}}`;
