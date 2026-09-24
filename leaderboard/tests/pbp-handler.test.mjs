// The play-by-post handlers, driven end to end over a stub PostgREST.
//
// `pbp.test.mjs` pins this endpoint's pure helpers — what a token is, who is
// outstanding, whose turn has lapsed. This pins what the *handlers* do with
// them, which is the surface nothing else reaches: the query strings, whether a
// minted token is accepted back, whether the conditional update really makes two
// clients resolving together a non-race, and whether the deadline ladder reads
// its own stored history rather than the caller's word for it.
//
// Those fail only against a real deploy, and in the worst case they fail
// silently — a match that opens, takes orders, and then quietly refuses to
// advance. `tools/check_pbp.py` is the real-deploy version of this and is what
// to run before trusting a preview; this is the half that needs no network, so
// it can fail in CI instead of in somebody's match.
//
// **The database is a stand-in, and a deliberately strict one.** It answers
// `eq` filters, projects `select` lists and enforces the one unique constraint
// the handlers lean on — and it *throws* on a column it does not have, because a
// query string that has drifted from `schema.sql` is the main thing this is here
// to catch. What it is not is PostgREST: it does not check types, defaults,
// foreign keys or RLS, so a green run here means the handlers agree with the
// schema as written, not that a deploy will work.

import assert from "node:assert/strict";
import test from "node:test";

import handler from "../netlify/functions/pbp.mjs";

process.env.SUPABASE_URL = "http://db.local";
process.env.SUPABASE_SECRET_KEY = "sb_secret_stub";

const MATCH = "00112233445566ff";
// Columns and defaults as `schema.sql` declares them. Kept here rather than
// derived, so a column added there without a matching entry shows up as a
// failure rather than as a row that happens to have whatever was inserted.
const DEFAULTS = {
  pbp_matches: { turn: 0, finished: false, log: "", deadline_hours: null, public: false,
                 turn_opened_at: () => new Date().toISOString(),
                 created_at: "", updated_at: "" },
  pbp_orders: { source: "human", board_digest: "", submitted_at: "", id: 0 },
};
const UNIQUE = { pbp_orders: (r) => `${r.match_id}|${r.turn}|${r.seat}` };

let TABLES;

function withDefaults(table, row) {
  const out = {};
  for (const [col, val] of Object.entries(DEFAULTS[table])) {
    out[col] = typeof val === "function" ? val() : val;
  }
  return { ...out, ...row };
}

function install() {
  TABLES = { pbp_matches: [], pbp_orders: [] };
  globalThis.fetch = async (url, init = {}) => {
    const parsed = new URL(url);
    const table = parsed.pathname.replace(/^\/rest\/v1\//, "");
    assert.ok(table in TABLES, `stub: no table ${table}`);
    const filters = [];
    for (const [key, val] of parsed.searchParams) {
      if (key === "select" || key === "order" || key === "limit") continue;
      const [op, ...rest] = val.split(".");
      assert.ok(op === "eq" || op === "in", `stub: unsupported operator ${op}`);
      filters.push([key, op, rest.join(".")]);
    }
    const hit = (r) => filters.every(([col, op, val]) => (op === "in"
      ? val.replace(/^\(|\)$/g, "").split(",").includes(String(r[col]))
      : String(r[col]) === val));
    const select = parsed.searchParams.get("select");
    const project = (r) => {
      if (!select || select === "*") return { ...r };
      const out = {};
      for (const col of select.split(",")) {
        assert.ok(col in r, `stub: no column "${col}" on ${table}`);
        out[col] = r[col];
      }
      return out;
    };
    const method = init.method || "GET";
    const ok = (body) => new Response(JSON.stringify(body), { status: 200 });

    if (method === "GET") {
      let rows = TABLES[table].filter(hit);
      const order = parsed.searchParams.get("order");
      if (order) {
        const keys = order.split(",").map((p) => p.split("."));
        rows = [...rows].sort((a, b) => {
          for (const [col, dir] of keys) {
            assert.ok(col in (a || {}), `stub: cannot order by "${col}"`);
            const sign = dir === "desc" ? -1 : 1;
            if (a[col] < b[col]) return -sign;
            if (a[col] > b[col]) return sign;
          }
          return 0;
        });
      }
      const limit = Number(parsed.searchParams.get("limit") || Infinity);
      return ok(rows.slice(0, limit).map(project));
    }
    if (method === "POST") {
      const incoming = JSON.parse(init.body).map((r) => withDefaults(table, r));
      const key = UNIQUE[table];
      if (key && incoming.some((r) => TABLES[table].some((e) => key(e) === key(r)))) {
        return new Response('{"code":"23505"}', { status: 409 });
      }
      TABLES[table].push(...incoming);
      return new Response("", { status: 201 });
    }
    if (method === "PATCH") {
      const patch = JSON.parse(init.body);
      const rows = TABLES[table].filter(hit);
      for (const r of rows) Object.assign(r, patch);
      return ok(rows.map((r) => ({ ...r })));
    }
    throw new Error(`stub: unsupported method ${method}`);
  };
}

/** Call the handler the way Netlify would, and hand back `[status, body]`. */
async function call(action, body, method = "POST") {
  const url = `http://localhost/api/pbp?action=${action}`
    + (body && method === "GET" ? `&${new URLSearchParams(body)}` : "");
  const reply = await handler(new Request(url, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(method === "POST" ? { body: JSON.stringify(body ?? {}) } : {}),
  }));
  return [reply.status, JSON.parse(await reply.text())];
}

const state = (match = MATCH) => call("state", { match }, "GET");

/** A match with `seats` people in it, and the tokens it minted. */
async function opened(seats = [1, 2]) {
  install();
  const [status, body] = await call("create", {
    match_id: MATCH, settings_json: { mode: "random", players: seats.length },
    seed: 7, seats, rules_version: 2,
  });
  assert.equal(status, 201, JSON.stringify(body));
  return body.tokens;
}

/** Wind the stored clock back, the one thing no caller could ever do. */
function elapse() {
  for (const m of TABLES.pbp_matches) {
    m.turn_opened_at = "2020-01-01T00:00:00Z";
    m.deadline_hours = 1;
  }
}

// --------------------------------------------------------------------------
// Opening, and what a token is good for
// --------------------------------------------------------------------------
test("a match opens with one token per seat, and they differ", async () => {
  const tokens = await opened([1, 2, 3]);
  assert.deepEqual(Object.keys(tokens).sort(), ["1", "2", "3"]);
  assert.equal(new Set(Object.values(tokens)).size, 3);
});

test("a token names its own seat and nothing else", async () => {
  const tokens = await opened();
  for (const [seat, token] of Object.entries(tokens)) {
    const [status, body] = await call("seat", { match_id: MATCH, token });
    assert.equal(status, 200, JSON.stringify(body));
    assert.equal(body.seat, Number(seat));
  }
  const [status] = await call("seat", { match_id: MATCH, token: "f".repeat(32) });
  assert.equal(status, 403, "a token nobody was given must name no seat");
});

test("a public read hands out no token, hashed or otherwise", async () => {
  const tokens = await opened();
  const [, body] = await state();
  const text = JSON.stringify(body);
  for (const token of Object.values(tokens)) {
    assert.ok(!text.includes(token), "a plaintext token reached a public read");
  }
  assert.ok(!text.includes("tokens"), "the stored hashes reached a public read");
  assert.deepEqual(body.seats, [1, 2], "...but the roster itself is public");
});

// --------------------------------------------------------------------------
// A turn, and the gates around it
// --------------------------------------------------------------------------
test("a seat may submit once, and the turn waits for the other", async () => {
  const tokens = await opened();
  const [status, body] = await call("submit", {
    match_id: MATCH, token: tokens[1], turn: 0,
    orders: [{ src: 1, dst: 2, ships: 3 }],
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.deepEqual(body.waiting, [2]);

  const [again] = await call("submit", {
    match_id: MATCH, token: tokens[1], turn: 0, orders: [],
  });
  assert.equal(again, 409, "a second submission from one seat must be refused");
});

test("the live turn's orders are withheld until every seat is in", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0,
                         orders: [{ src: 1, dst: 2, ships: 3 }] });
  let [, body] = await state();
  assert.deepEqual(body.turns, [], "a seat still composing must not read the others");

  await call("submit", { match_id: MATCH, token: tokens[2], turn: 0, orders: [] });
  [, body] = await state();
  assert.equal(body.turns.length, 2, "...and released the moment the turn is complete");
});

test("a turn resolves once, and the second client is told it already moved", async () => {
  const tokens = await opened();
  for (const seat of [1, 2]) {
    await call("submit", { match_id: MATCH, token: tokens[seat], turn: 0, orders: [] });
  }
  const payload = { match_id: MATCH, turn: 0, log: "abc", board_digest: "d", finished: false };
  const [first, body] = await call("resolve", { ...payload, token: tokens[1] });
  assert.equal(first, 200, JSON.stringify(body));
  assert.equal(body.turn, 1);

  // A client that turns up *after* the turn moved is simply behind, and the
  // reply names the turn it should re-read — it never reaches the conditional
  // update, because the turn it asked about is no longer the live one.
  const [second, again] = await call("resolve", { ...payload, token: tokens[2] });
  assert.equal(second, 409);
  assert.equal(again.error, "stale turn");
  assert.equal(again.turn, 1, "...and is told where the match actually is");

  const [, after] = await state();
  assert.equal(after.turn, 1);
  assert.equal(after.log, "abc");
});

test("two clients resolving at the same instant: exactly one wins", async () => {
  // The case `already resolved` exists for, and the one the sequential test
  // above cannot reach. Both read the same live turn, both pass every gate, and
  // the conditional update is what decides between them: PostgREST turns the
  // `turn=eq.N` filter into the UPDATE's WHERE, so the loser matches no row.
  // What must hold is not who wins but that the match moves exactly one turn.
  const tokens = await opened();
  for (const seat of [1, 2]) {
    await call("submit", { match_id: MATCH, token: tokens[seat], turn: 0, orders: [] });
  }
  const results = await Promise.all([1, 2].map((seat) => call("resolve", {
    match_id: MATCH, token: tokens[seat], turn: 0,
    log: `log-${seat}`, board_digest: "d",
  })));
  const won = results.filter(([status]) => status === 200);
  assert.equal(won.length, 1, `exactly one resolve must win, got ${JSON.stringify(results)}`);
  assert.equal(results.find(([status]) => status !== 200)[0], 409);

  const [, after] = await state();
  assert.equal(after.turn, 1, "the match advances exactly one turn either way");
});

test("a turn nobody has finished cannot be resolved", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  const [status, body] = await call("resolve", {
    match_id: MATCH, token: tokens[1], turn: 0, log: "abc", board_digest: "d",
  });
  assert.equal(status, 409);
  assert.deepEqual(body.waiting, [2]);
});

// --------------------------------------------------------------------------
// The deadline ladder, read off stored history rather than the caller's word
// --------------------------------------------------------------------------
test("a turn that has only just opened has lapsed for nobody", async () => {
  await opened();
  const [, body] = await state();
  assert.deepEqual(body.lapsed, {});
});

test("a first miss holds, and the orders are forced empty whatever was sent", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  const [, body] = await state();
  assert.deepEqual(body.lapsed, { 2: "hold" });

  const [status] = await call("lapse", {
    match_id: MATCH, token: tokens[1], turn: 0,
    seats: { 2: [{ src: 1, dst: 2, ships: 9 }] },   // a client trying it on
  });
  assert.equal(status, 201);
  const row = TABLES.pbp_orders.find((r) => r.seat === 2 && r.turn === 0);
  assert.equal(row.source, "hold");
  assert.deepEqual(row.orders_json, [], "a held turn is no orders at all");
});

test("a second consecutive miss falls to the bot, and its orders are kept", async () => {
  const tokens = await opened();
  // Turn 0: seat 2 misses and holds.
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  await call("lapse", { match_id: MATCH, token: tokens[1], turn: 0, seats: { 2: [] } });
  await call("resolve", { match_id: MATCH, token: tokens[1], turn: 0,
                          log: "abc", board_digest: "d" });
  // Turn 1: it misses again, so now the seat is played for it.
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 1, orders: [] });
  elapse();
  const [, body] = await state();
  assert.deepEqual(body.lapsed, { 2: "bot" }, "earned off the stored `source` column");

  const orders = [{ src: 4, dst: 5, ships: 12 }];
  const [status] = await call("lapse", {
    match_id: MATCH, token: tokens[1], turn: 1, seats: { 2: orders },
  });
  assert.equal(status, 201);
  const row = TABLES.pbp_orders.find((r) => r.seat === 2 && r.turn === 1);
  assert.equal(row.source, "bot");
  assert.deepEqual(row.orders_json, orders);
});

test("a seat that played resets its own run of misses", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  await call("lapse", { match_id: MATCH, token: tokens[1], turn: 0, seats: { 2: [] } });
  await call("resolve", { match_id: MATCH, token: tokens[1], turn: 0,
                          log: "abc", board_digest: "d" });
  // ...but this time seat 2 turns up. Both seats have to be in for the turn to
  // be complete — a seat that simply says nothing leaves it outstanding, which
  // is what the deadline is for; holding is what the *engine* does with an
  // empty submission, not something the endpoint infers from silence.
  for (const seat of [1, 2]) {
    await call("submit", { match_id: MATCH, token: tokens[seat], turn: 1, orders: [] });
  }
  await call("resolve", { match_id: MATCH, token: tokens[1], turn: 1,
                          log: "abcd", board_digest: "e" });
  assert.equal((await state())[1].turn, 2);
  const played = TABLES.pbp_orders.find((r) => r.seat === 2 && r.turn === 1);
  assert.equal(played.source, "human", "seat 2 really did play this one itself");

  await call("submit", { match_id: MATCH, token: tokens[1], turn: 2, orders: [] });
  elapse();
  assert.deepEqual((await state())[1].lapsed, { 2: "hold" },
                   "turning up once puts the seat back on its first miss");
});

test("nothing may be filed while the clock is still running", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  const [status, body] = await call("lapse", {
    match_id: MATCH, token: tokens[1], turn: 0, seats: { 2: [] },
  });
  assert.equal(status, 409);
  assert.equal(body.error, "nothing has lapsed");
});

test("filing for a seat that is not outstanding is refused", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  const [status] = await call("lapse", {
    match_id: MATCH, token: tokens[1], turn: 0, seats: { 1: [] },
  });
  assert.equal(status, 409, "seat 1 submitted on time and is nobody's to file");
});

test("a lapse needs a seat's token, not merely a match id", async () => {
  await opened();
  elapse();
  const [status] = await call("lapse", {
    match_id: MATCH, token: "a".repeat(32), turn: 0, seats: { 2: [] },
  });
  assert.equal(status, 403);
});

test("filing the last outstanding seat completes the turn without resolving it", async () => {
  const tokens = await opened();
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  await call("lapse", { match_id: MATCH, token: tokens[1], turn: 0, seats: { 2: [] } });
  const [, body] = await state();
  assert.equal(body.turn, 0, "a lapse advances nothing by itself");
  assert.deepEqual(body.submitted.sort(), [1, 2], "...it only makes the turn complete");
});

// --------------------------------------------------------------------------
// Public matches: the lobby list, and claiming an open seat
// --------------------------------------------------------------------------
async function openedPublic(seats = [1, 2, 3], extra = {}) {
  install();
  const [status, body] = await call("create", {
    match_id: MATCH, settings_json: { mode: "random", players: seats.length },
    seed: 7, seats, rules_version: 2, public: true, claimed: [1], ...extra,
  });
  assert.equal(status, 201, JSON.stringify(body));
  return body.tokens;
}

const list = () => call("list", null, "GET");

test("a public match mints only the creator's token", async () => {
  const tokens = await openedPublic();
  assert.deepEqual(Object.keys(tokens), ["1"]);
  assert.deepEqual(Object.keys(TABLES.pbp_matches[0].seats.tokens), ["1"]);
});

test("only a public match may leave a seat unminted", async () => {
  install();
  const [status] = await call("create", {
    match_id: MATCH, settings_json: {}, seed: 7, seats: [1, 2], claimed: [1],
  });
  assert.equal(status, 400);
});

test("the lobby lists public matches only, and never a log or a hash", async () => {
  await openedPublic();
  TABLES.pbp_matches.push(withDefaults("pbp_matches", {
    match_id: "ffffffffffffffff", settings_json: {}, seed: 1, rules_version: 2,
    seats: { seats: [1], tokens: { 1: "a".repeat(64) } }, log: "SECRETLOG",
  }));
  TABLES.pbp_matches[0].log = "SECRETLOG";
  const [status, body] = await list();
  assert.equal(status, 200, JSON.stringify(body));
  assert.deepEqual(body.matches.map((m) => m.match_id), [MATCH]);
  const text = JSON.stringify(body);
  assert.ok(!text.includes("SECRETLOG") && !text.includes("tokens"));
  const [m] = body.matches;
  assert.equal(m.status, "open");
  assert.deepEqual(m.unclaimed, [2, 3]);
  assert.deepEqual(m.waiting, [1, 2, 3]);
});

test("a claimed seat's token works, and the seat cannot be claimed twice", async () => {
  await openedPublic([1, 2]);
  const [status, body] = await call("claim", { match_id: MATCH, seat: 2 });
  assert.equal(status, 201, JSON.stringify(body));
  const [seatStatus, seatBody] = await call("seat", { match_id: MATCH, token: body.token });
  assert.equal(seatStatus, 200);
  assert.equal(seatBody.seat, 2);
  const [again, againBody] = await call("claim", { match_id: MATCH, seat: 2 });
  assert.equal(again, 409);
  assert.equal(againBody.error, "already claimed");
  const [, listed] = await list();
  assert.equal(listed.matches[0].status, "in_progress");
  assert.deepEqual(listed.matches[0].unclaimed, []);
});

test("claiming keeps every other seat's hash", async () => {
  const tokens = await openedPublic([1, 2, 3]);
  await call("claim", { match_id: MATCH, seat: 2 });
  await call("claim", { match_id: MATCH, seat: 3 });
  const [status, body] = await call("seat", { match_id: MATCH, token: tokens[1] });
  assert.equal(status, 200);
  assert.equal(body.seat, 1);
});

test("a claim that raced another write is told to retry, not handed a seat", async () => {
  await openedPublic([1, 2]);
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if ((init?.method || "GET") === "PATCH") TABLES.pbp_matches[0].updated_at = "moved";
    return realFetch(url, init);
  };
  const [status] = await call("claim", { match_id: MATCH, seat: 2 });
  globalThis.fetch = realFetch;
  assert.equal(status, 409);
  assert.deepEqual(Object.keys(TABLES.pbp_matches[0].seats.tokens), ["1"]);
});

test("a private or finished match cannot be claimed into", async () => {
  await opened([1, 2]);
  const [privateStatus] = await call("claim", { match_id: MATCH, seat: 2 });
  assert.equal(privateStatus, 404, "a private match reads as missing");
  await openedPublic([1, 2]);
  TABLES.pbp_matches[0].finished = true;
  const [finishedStatus] = await call("claim", { match_id: MATCH, seat: 2 });
  assert.equal(finishedStatus, 409);
});

test("a live match past its deadline lists as lapsed", async () => {
  const tokens = await opened([1, 2]);
  TABLES.pbp_matches[0].public = true;
  await call("submit", { match_id: MATCH, token: tokens[1], turn: 0, orders: [] });
  elapse();
  const [, body] = await list();
  assert.equal(body.matches[0].status, "lapsed");
  assert.deepEqual(body.matches[0].lapsed, { 2: "hold" });
});
