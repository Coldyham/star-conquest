/**
 * GET /api/campaign?token=<settings token>&name=<player> — is the setup the
 * game is about to play one of this week's campaign nodes, and what would a
 * win there do for `name` right now?
 *
 * The game asks this so it can confirm before Start and keep a countdown in
 * its top bar (`starconquest/campaign.py`). The answer is computed by the
 * campaign's own rules, `js/campaign.mjs` (`fold` and `attemptStatus`), so
 * there is one copy of them and the game and campaign.html can't disagree.
 * The game never reimplements a timer, only counts down to the times
 * returned here.
 *
 * Read-only, over the same public rows campaign.html reads with the same
 * publishable key (`js/config.mjs`), so this function holds no secret and
 * needs no environment. It is a function at all only because the game has no
 * PostgREST client and the rules are in JavaScript.
 *
 * A node is matched the way `campaign_games` matches one: the token's setup
 * against each node's stored `settings` by value (`setupIdentity`, after both
 * have been through JSON.parse, so Python's `1.0` and a browser's `1` agree),
 * never by `sc_config_key`.
 *
 * 404 means "not a campaign node this week" (or no campaign yet), which the
 * game treats as nothing to show.
 */

import { inflateSync } from "node:zlib";

import { attemptStatus, fold, keyOf, weekEnd } from "../../js/campaign.mjs";
import { SUPABASE_ANON_KEY, SUPABASE_URL } from "../../js/config.mjs";
import { weekParam, weekStart } from "../../js/crowns.mjs";
import { decodeToken, setupIdentity } from "../../js/token-decode.mjs";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

// Generous for any real settings token (a few hundred characters), small
// enough that nobody can make this decompress something large.
const MAX_TOKEN = 4096;
const MAX_NAME = 60;

const inflate = (bytes) => new Uint8Array(inflateSync(bytes, { maxOutputLength: 1 << 16 }));

function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** The node of `graph` whose stored setup is `setup`, or null. */
export function nodeFor(graph, setup) {
  const want = setupIdentity(setup);
  return graph.nodes.find((node) => setupIdentity(node.settings) === want) || null;
}

/** The reply for `name` on `node` at `now`: pure, so tests can call it. */
export function answer(graph, scores, node, name, now) {
  const state = fold(graph, scores, now);
  const held = state.holders.get(node.id);
  const key = keyOf(name);
  const queued = key ? state.queued.find((q) => q.key === key && q.nodeId === node.id) : null;
  return {
    week_start: graph.week_start,
    week_end: weekEnd(graph),
    now,
    node: { id: node.id, kind: node.kind },
    holder: held ? { name: held.name, turns: held.turns, lost: held.lost } : null,
    status: attemptStatus(graph, state, node.id, name, now),
    queued_at: queued ? queued.at : null,
  };
}

async function read(path) {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` },
  });
  if (!response.ok) throw new Error(`${path.split("?")[0]} ${response.status}`);
  return response.json();
}

export default async function handler(request) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (request.method !== "GET") return reply(405, { error: "GET only" });

  const params = new URL(request.url).searchParams;
  const token = params.get("token") || "";
  const name = (params.get("name") || "").trim().slice(0, MAX_NAME);
  if (!token || token.length > MAX_TOKEN) return reply(400, { error: "bad token" });
  let decoded;
  try {
    decoded = await decodeToken(token, inflate);
  } catch {
    return reply(400, { error: "bad token" });
  }

  const now = Date.now();
  const day = weekParam(weekStart(new Date(now)));
  try {
    const rows = await read(`campaigns?select=week_start,graph&week_start=eq.${day}`);
    if (!rows.length) return reply(404, { error: "no campaign" });
    const graph = rows[0].graph;
    const node = nodeFor(graph, decoded.setup);
    if (!node) return reply(404, { error: "not a node" });
    const scores = await read(
      `campaign_scores?select=node_id,score_id,user_name,turns,lost,submitted_at` +
      `&week_start=eq.${day}&order=submitted_at.asc,score_id.asc`);
    return reply(200, answer(graph, scores, node, name, now));
  } catch (err) {
    console.error("campaign lookup failed", err.message);
    return reply(502, { error: "lookup failed" });
  }
}

export const config = { path: "/api/campaign" };
