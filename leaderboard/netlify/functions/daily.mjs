/**
 * GET /api/daily[?day=YYYY-MM-DD] — the daily challenge's setup for a day
 * (today, UTC, by default), for the game to open (`starconquest/daily.py`).
 *
 * Read-only, over the `dailies` rows daily.html reads with the same publishable
 * key (`js/config.mjs`), so this function holds no secret and needs no
 * environment. It is a function at all only because the game has no PostgREST
 * client. The table's policy hides a day until it starts, so asking for
 * tomorrow answers 404 like a day that was never made.
 *
 * 200 {day, now, settings, rules_version}; 404 when there is no daily that day.
 */

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "../../js/config.mjs";
import { dayParam, validDay } from "../../js/daily.mjs";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** The day a request asks for, or null when `day` is given but unreadable. */
export function askedDay(params, now) {
  const day = params.get("day");
  if (day === null || day === "") return dayParam(now);
  return validDay(day) ? day : null;
}

export default async function handler(request) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (request.method !== "GET") return reply(405, { error: "GET only" });

  const now = Date.now();
  const day = askedDay(new URL(request.url).searchParams, now);
  if (day === null) return reply(400, { error: "bad day" });
  try {
    const response = await fetch(
      `${SUPABASE_URL}/rest/v1/dailies?select=day,settings,rules_version&day=eq.${day}`,
      { headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` } });
    if (!response.ok) throw new Error(`dailies ${response.status}`);
    const rows = await response.json();
    if (!rows.length) return reply(404, { error: "no daily", day });
    const row = rows[0];
    return reply(200, { day: row.day, now, settings: row.settings, rules_version: row.rules_version });
  } catch (err) {
    console.error("daily lookup failed", err.message);
    return reply(502, { error: "lookup failed" });
  }
}

export const config = { path: "/api/daily" };
