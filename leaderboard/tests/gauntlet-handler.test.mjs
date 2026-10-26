// /api/gauntlet, driven end to end over a stub PostgREST.
//
// What the handler decides on its own, past `checkBody` (gauntlet-build.test.mjs
// pins that): which id a body is stored under, that saving twice is one row,
// that a read is by id only, and that a malformed request never reaches the
// database. The stub keeps rows in memory, honours `ignore-duplicates`, and
// throws on a table or column `schema.sql` does not declare.

import assert from "node:assert/strict";
import test from "node:test";

import handler, { rowFor } from "../netlify/functions/gauntlet.mjs";

process.env.SUPABASE_URL = "http://db.local";
process.env.SUPABASE_SECRET_KEY = "sb_secret_stub";

const COLUMNS = ["gauntlet_id", "body", "by_name", "wants_listing", "listed", "created_at"];
let ROWS;
let CALLS;

function install() {
  ROWS = [];
  CALLS = 0;
  globalThis.fetch = async (url, init = {}) => {
    CALLS += 1;
    const parsed = new URL(url);
    assert.equal(parsed.pathname, "/rest/v1/gauntlets");
    assert.equal(init.headers.apikey, "sb_secret_stub");
    if ((init.method || "GET") === "POST") {
      assert.match(init.headers.Prefer, /resolution=ignore-duplicates/);
      for (const row of JSON.parse(init.body)) {
        for (const col of Object.keys(row)) assert.ok(COLUMNS.includes(col), `no column ${col}`);
        if (!ROWS.some((r) => r.gauntlet_id === row.gauntlet_id)) {
          ROWS.push({ listed: false, created_at: "2026-10-22T10:00:00Z", ...row });
        }
      }
      return new Response("", { status: 201 });
    }
    const select = parsed.searchParams.get("select").split(",");
    for (const col of select) assert.ok(COLUMNS.includes(col), `no column ${col}`);
    const byName = parsed.searchParams.get("by_name");
    const id = (parsed.searchParams.get("gauntlet_id") || "").replace(/^eq\./, "");
    const hits = ROWS.filter((r) => (byName === null ? r.gauntlet_id === id : r.by_name !== ""))
      .map((r) => Object.fromEntries(select.map((c) => [c, r[c]])));
    return new Response(JSON.stringify(hits), { status: 200 });
  };
}

let ip = 0;
function request(method, { body, query = "", headers = {} } = {}) {
  ip += 1;   // a fresh caller each time, so the rate limit stays out of the way
  return new Request(`https://star-conquest.netlify.app/api/gauntlet${query}`, {
    method,
    headers: { "x-nf-client-connection-ip": `10.0.0.${ip % 250}.${ip}`, ...headers },
    body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });
}

const GAUNTLET = {
  name: "Hard mode",
  stages: [{ id: 0, title: "One", briefing: ["Go."], hint: "", settings: { players: 2, nodes: 9, seed: 1 } }],
};

test("a save stores one row under the minted id, and a second save of it is the same row", async () => {
  install();
  const first = await handler(request("POST", { body: { gauntlet: GAUNTLET, by_name: " Ada ", wants_listing: true } }));
  assert.equal(first.status, 201);
  const { id } = await first.json();
  assert.match(id, /^hard-mode-[0-9a-f]{12}$/);
  assert.equal(ROWS.length, 1);
  assert.deepEqual(ROWS[0].body, { id, ...GAUNTLET });
  assert.equal(ROWS[0].by_name, "Ada");
  assert.equal(ROWS[0].wants_listing, true);
  assert.equal(ROWS[0].listed, false);

  const again = await handler(request("POST", { body: { gauntlet: GAUNTLET, by_name: "Someone else" } }));
  assert.equal((await again.json()).id, id);
  assert.equal(ROWS.length, 1);
  assert.equal(ROWS[0].by_name, "Ada");
});

test("a read hands back the stored gauntlet by id, and 404s an unknown one", async () => {
  install();
  const { id } = await (await handler(request("POST", { body: { gauntlet: GAUNTLET } }))).json();
  const got = await handler(request("GET", { query: `?id=${id}` }));
  assert.equal(got.status, 200);
  const out = await got.json();
  assert.equal(out.id, id);
  assert.deepEqual(out.gauntlet, { id, ...GAUNTLET });
  assert.equal(out.by_name, "");
  const missing = await handler(request("GET", { query: "?id=hard-mode-000000000000" }));
  assert.equal(missing.status, 404);
});

test("a malformed id or body never reaches the database", async () => {
  install();
  for (const query of ["", "?id=", "?id=Hard-0123456789ab", "?id=x-0123456789ab%26listed=eq.true"]) {
    assert.equal((await handler(request("GET", { query }))).status, 400, query);
  }
  assert.equal((await handler(request("POST", { body: "{nope" }))).status, 400);
  const bad = await handler(request("POST", { body: { gauntlet: { ...GAUNTLET, name: "" } } }));
  assert.equal(bad.status, 400);
  assert.match((await bad.json()).error, /needs a name/);
  const longName = await handler(request("POST", { body: { gauntlet: GAUNTLET, by_name: "x".repeat(33) } }));
  assert.equal(longName.status, 400);
  assert.equal(CALLS, 0);
});

test("an oversized body is refused before it is parsed", async () => {
  install();
  const big = JSON.stringify({ gauntlet: GAUNTLET, pad: "x".repeat(80000) });
  assert.equal((await handler(request("POST", { body: big }))).status, 413);
  assert.equal(CALLS, 0);
});

test("only GET and POST are answered, and an unconfigured deploy stores nothing", async () => {
  install();
  assert.equal((await handler(request("DELETE"))).status, 405);
  assert.equal((await handler(request("OPTIONS"))).status, 204);
  const saved = process.env.SUPABASE_URL;
  delete process.env.SUPABASE_URL;
  try {
    assert.equal((await handler(request("POST", { body: { gauntlet: GAUNTLET } }))).status, 503);
  } finally {
    process.env.SUPABASE_URL = saved;
  }
});

test("a caller is rate limited on saves", async () => {
  install();
  const headers = { "x-nf-client-connection-ip": "192.0.2.77" };
  const statuses = [];
  for (let i = 0; i < 32; i += 1) {
    const g = { ...GAUNTLET, name: `Run ${i}` };
    statuses.push((await handler(new Request("https://star-conquest.netlify.app/api/gauntlet", {
      method: "POST", headers, body: JSON.stringify({ gauntlet: g }) }))).status);
  }
  assert.equal(statuses.filter((s) => s === 201).length, 30);
  assert.equal(statuses.at(-1), 429);
});

test("rowFor answers the stored row or why not", async () => {
  assert.equal(await rowFor(null), "not an object");
  assert.equal(await rowFor([]), "not an object");
  const row = await rowFor({ gauntlet: GAUNTLET, wants_listing: "yes" });
  assert.equal(row.wants_listing, false);   // only a real true asks
  assert.equal(row.body.id, row.gauntlet_id);
});

test("?by= counts the gauntlets saved under a name, case folded, and hands back no id", async () => {
  install();
  const other = { ...GAUNTLET, name: "Other" };
  const third = { ...GAUNTLET, name: "Third" };
  await handler(request("POST", { body: { gauntlet: GAUNTLET, by_name: "Ada" } }));
  await handler(request("POST", { body: { gauntlet: other, by_name: " ada " } }));
  await handler(request("POST", { body: { gauntlet: third } }));
  const reply = await handler(request("GET", { query: "?by=ADA" }));
  assert.equal(reply.status, 200);
  const body = await reply.json();
  assert.deepEqual(body, { made: 2 });
  assert.equal((await (await handler(request("GET", { query: "?by=Bo" }))).json()).made, 0);
  assert.equal((await handler(request("GET", { query: "?by=" }))).status, 400);
});
