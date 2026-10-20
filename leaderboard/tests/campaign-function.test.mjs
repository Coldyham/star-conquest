// The game's campaign lookup (netlify/functions/campaign.mjs): which node a
// setup is, and what the campaign's own rules say a win there would do.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { deflateSync } from "node:zlib";

import handler, { answer, nodeFor } from "../netlify/functions/campaign.mjs";
import { weekParam, weekStart } from "../js/crowns.mjs";

const MIN = 60000;
const graph = {
  week_start: "2026-09-28",
  nodes: [
    { id: 0, kind: "field", settings: { mode: "random", players: 3, nodes: 18, seed: 7 } },
    { id: 1, kind: "field", settings: { mode: "random", players: 3, nodes: 18, seed: 8 } },
    { id: 10, kind: "home", settings: { mode: "random", players: 2, nodes: 12, seed: 9 } },
  ],
  lanes: [[0, 1], [10, 0]],
};
const T0 = Date.UTC(2026, 8, 28);
let ids = 0;
const at = (m, name, node, turns, lost = 0) => ({
  node_id: node, score_id: ++ids, user_name: name, turns, lost,
  submitted_at: new Date(T0 + m * MIN).toISOString(),
});

/** A settings token the way the game writes one: JSON, zlib, base64url. */
const tokenOf = (dict) => deflateSync(Buffer.from(JSON.stringify(dict)), { level: 9 })
  .toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

test("a setup is matched to its node by value, the way campaign_games matches", () => {
  assert.equal(nodeFor(graph, { mode: "random", players: 3, nodes: 18, seed: 8 }).id, 1);
  assert.equal(nodeFor(graph, { seed: 8, nodes: 18, players: 3, mode: "random" }).id, 1);   // key order
  assert.equal(nodeFor(graph, { mode: "random", players: 3, nodes: 18, seed: 8, autoplay: true }).id, 1);
  assert.equal(nodeFor(graph, { mode: "random", players: 3, nodes: 18, seed: 99 }), null);
  // Python writes 1.0 where a browser writes 1: equal once both are parsed.
  const stored = JSON.parse('{"mode":"random","players":3,"nodes":18,"seed":5,"jitter":1.0}');
  assert.equal(nodeFor({ nodes: [{ id: 4, settings: stored }] },
    { mode: "random", players: 3, nodes: 18, seed: 5, jitter: 1 }).id, 4);
});

test("the answer is attemptStatus, plus the holder", () => {
  const scores = [at(0, "ann", 10, 40), at(20, "ann", 0, 30)];
  const reply = answer(graph, scores, graph.nodes[1], "Ann", T0 + 30 * MIN);
  assert.equal(reply.node.id, 1);
  assert.equal(reply.holder, null);
  assert.equal(reply.now, T0 + 30 * MIN);
  assert.equal(reply.status.why, "adjacent");
  const onZero = answer(graph, scores, graph.nodes[0], "bo", T0 + 30 * MIN);
  assert.deepEqual(onZero.holder, { name: "ann", turns: 30, lost: 0 });
  assert.equal(onZero.status.why, "no-home");
  assert.equal(answer(graph, scores, graph.nodes[0], "", T0).status.why, "no-name");
});

test("the handler finds this week's node and answers for the name given", async () => {
  const day = weekParam(weekStart(new Date()));
  const week = { ...graph, week_start: day };
  const realFetch = globalThis.fetch;
  const asked = [];
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    const body = String(url).includes("/campaigns?") ? [{ week_start: day, graph: week }] : [];
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    const token = tokenOf(graph.nodes[2].settings);
    const ok = await handler(new Request(`https://x/api/campaign?token=${token}&name=bo`));
    assert.equal(ok.status, 200);
    const body = await ok.json();
    assert.deepEqual([body.node, body.status.why, body.status.can], [{ id: 10, kind: "home" }, "home-open", true]);
    assert.ok(asked[0].includes(`week_start=eq.${day}`));

    const other = await handler(new Request(`https://x/api/campaign?token=${tokenOf({ mode: "random", players: 3, nodes: 18, seed: 1 })}`));
    assert.equal(other.status, 404);
    assert.equal((await handler(new Request("https://x/api/campaign?token=nonsense!"))).status, 400);
    assert.equal((await handler(new Request("https://x/api/campaign"))).status, 400);
    assert.equal((await handler(new Request("https://x/api/campaign", { method: "POST" }))).status, 405);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("a token the Python game minted decodes to its node", async () => {
  // A real token from the Python encoder (tests/fixtures/tokens.json), stored
  // as a node with the setup it decodes to.
  const fixture = JSON.parse(readFileSync(new URL("./fixtures/tokens.json", import.meta.url), "utf8"))
    .find((entry) => entry.name === "tuned-knobs");
  const day = weekParam(weekStart(new Date()));
  const { inflateSync } = await import("node:zlib");
  const raw = Buffer.from(fixture.token.replace(/-/g, "+").replace(/_/g, "/"), "base64");
  const setup = JSON.parse(inflateSync(raw).toString("utf8"));
  delete setup.challenge;
  const week = { week_start: day, nodes: [{ id: 6, kind: "field", settings: setup }], lanes: [] };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => new Response(JSON.stringify(
    String(url).includes("/campaigns?") ? [{ week_start: day, graph: week }] : []), { status: 200 });
  try {
    const reply = await handler(new Request(`https://x/api/campaign?token=${fixture.token}&name=ann`));
    assert.equal(reply.status, 200);
    assert.equal((await reply.json()).node.id, 6);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("the game says which game is asking, and only the grace hears it", async () => {
  // ann lost node 0 to bo a minute ago, so node 1 is hers only inside the grace.
  const day = weekParam(weekStart(new Date()));
  const now = Date.now();
  const iso = (ago) => new Date(now - ago * MIN).toISOString();
  const week = { ...graph, week_start: day, lanes: [[0, 1], [10, 0], [11, 0]],
    nodes: [...graph.nodes, { id: 11, kind: "home", settings: { mode: "random", players: 2, nodes: 12, seed: 11 } }] };
  const scores = [
    { node_id: 10, score_id: 1, user_name: "ann", turns: 40, lost: 0, submitted_at: iso(90) },
    { node_id: 0, score_id: 2, user_name: "ann", turns: 30, lost: 0, submitted_at: iso(80) },
    { node_id: 11, score_id: 3, user_name: "bo", turns: 40, lost: 0, submitted_at: iso(70) },
    { node_id: 0, score_id: 4, user_name: "bo", turns: 20, lost: 0, submitted_at: iso(1) },
  ];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => new Response(JSON.stringify(
    String(url).includes("/campaigns?") ? [{ week_start: day, graph: week }] : scores), { status: 200 });
  const token = tokenOf(graph.nodes[1].settings);
  const why = async (more) =>
    (await (await handler(new Request(`https://x/api/campaign?token=${token}&name=ann${more}`))).json()).status.why;
  try {
    assert.equal(await why(""), "grace");
    assert.equal(await why("&start=adjacent"), "grace");
    assert.equal(await why("&starting=1"), "late-start");
    assert.equal(await why("&start=not-adjacent"), "late-start");
    assert.equal(await why("&start=NOT%20A%20STAMP"), "grace");   // unreadable: on trust
  } finally {
    globalThis.fetch = realFetch;
  }
});
