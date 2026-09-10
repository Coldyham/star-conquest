// The two functions this site runs: the upload endpoint and the replay endpoint.
//
// Both handlers are mostly plumbing (env lookup, CORS, one fetch), so what is
// worth pinning is where they make a decision. For `log.mjs` that is validation
// — an unauthenticated endpoint whose rows carry 5-14 KiB blobs and a `match_id`
// the score verifier keys on — plus the rate limiter's window arithmetic, which
// is easy to get subtly wrong and impossible to notice in production.

import assert from "node:assert/strict";
import test from "node:test";

import { allowedOrigin, rateLimited, validate } from "../netlify/functions/log.mjs";

const MATCH = "00112233445566ff";

/** A row the game would really post (`share.row_for`). */
function row(over = {}) {
  return {
    match_id: MATCH,
    game_key: "abcdef0123456789",
    turns: 42,
    finished: true,
    won: true,
    hand: 40,
    rules_version: 2,
    log: "eNrtVNtu4jAQ",
    ...over,
  };
}

test("a well-formed row is accepted and normalised", () => {
  const out = validate(row());
  assert.deepEqual(out, {
    match_id: MATCH,
    game_key: "abcdef0123456789",
    turns: 42,
    finished: true,
    won: true,
    hand: 40,
    rules_version: 2,
    log: "eNrtVNtu4jAQ",
  });
});

test("rules_version is a positive integer, or defaults to 1 (the only version there ever was before this field existed)", () => {
  assert.equal(validate({ ...row(), rules_version: undefined }).rules_version, 1);
  assert.equal(validate(row({ rules_version: 0 })), "bad rules_version");
  assert.equal(validate(row({ rules_version: -1 })), "bad rules_version");
  assert.equal(validate(row({ rules_version: 1.5 })), "bad rules_version");
  assert.equal(validate(row({ rules_version: "2" })), "bad rules_version");
});

test("a match_id of the wrong shape never reaches the database", () => {
  // The column the verifier keys scores against, so this is the one field where
  // a bad value would corrupt something rather than merely be junk.
  for (const bad of ["", "nope", MATCH.toUpperCase(), `${MATCH}0`, "'; drop table--", 17, null]) {
    assert.equal(validate(row({ match_id: bad })), "bad match_id", `accepted ${bad}`);
  }
});

test("the log must be a non-empty base64url string within the size cap", () => {
  assert.equal(validate(row({ log: "" })), "bad log");
  assert.equal(validate(row({ log: 42 })), "bad log");
  assert.equal(validate(row({ log: "x".repeat(262145) })), "bad log");
  // `GameLog.encoded` produces base64url and nothing else; padding included.
  assert.equal(validate(row({ log: "abc=" })), "bad log encoding");
  assert.equal(validate(row({ log: "not base64!" })), "bad log encoding");
  assert.equal(typeof validate(row({ log: "-_azAZ09" })), "object");
});

test("turns and hand are bounded, and hand cannot exceed the game", () => {
  assert.equal(validate(row({ turns: 0 })), "bad turns");
  assert.equal(validate(row({ turns: 1.5 })), "bad turns");
  assert.equal(validate(row({ turns: "42" })), "bad turns");
  assert.equal(validate(row({ hand: -1 })), "bad hand");
  assert.equal(validate(row({ hand: 43 })), "bad hand");
  // Absent is fine — it is an index, not a required claim.
  assert.equal(validate({ ...row(), hand: undefined }).hand, 0);
});

test("game_key must be present and bounded", () => {
  assert.equal(validate(row({ game_key: "" })), "bad game_key");
  assert.equal(validate(row({ game_key: "x".repeat(65) })), "bad game_key");
});

test("the claim flags are booleans, never whatever was sent", () => {
  // They are stored as claims and used only to find logs, so a truthy string
  // must not sneak through as true — the schema's own comment says the verifier
  // trusts none of them, and that only holds if the types are what they say.
  const out = validate(row({ finished: "yes", won: 1 }));
  assert.equal(out.finished, false);
  assert.equal(out.won, false);
});

test("anything that is not an object is rejected outright", () => {
  for (const bad of [null, undefined, "a string", [row()], 7]) {
    assert.equal(validate(bad), "not an object");
  }
});

test("every deploy of the game is an allowed origin, not just production", () => {
  // A preview of the game posts to the preview of this site, so a fixed
  // allowlist would refuse exactly the case previews exist for.
  assert.equal(allowedOrigin("https://star-conquest.netlify.app"), true);
  assert.equal(allowedOrigin("https://deploy-preview-42--star-conquest.netlify.app"), true);
  assert.equal(allowedOrigin("https://some-branch--star-conquest.netlify.app"), true);
  assert.equal(allowedOrigin("http://localhost:8000"), true);
});

test("a site that merely ends in the game's name is not the game", () => {
  // The site name is the last `--`-separated part, so a prefix is a context and
  // anything else is somebody else's site.
  for (const bad of [
    "https://evil-star-conquest.netlify.app",
    "https://star-conquest.netlify.app.evil.com",
    "https://star-conquest-leaderboard.netlify.app",
    "http://star-conquest.netlify.app",        // https only
    "https://example.com",
    "not a url",
    "",
    null,
  ]) {
    assert.equal(allowedOrigin(bad), false, `allowed ${bad}`);
  }
});

test("a caller is limited per window, and the window slides", () => {
  const store = new Map();
  const start = 1_000_000;
  // A real game checkpoints every 25 turns plus once at the end, so the limit
  // has to sit well above one game's worth.
  for (let i = 0; i < 30; i += 1) {
    assert.equal(rateLimited("1.2.3.4", start + i, store), false, `refused upload ${i}`);
  }
  assert.equal(rateLimited("1.2.3.4", start + 31, store), true);
  // An hour later the window has moved past all of them.
  assert.equal(rateLimited("1.2.3.4", start + 60 * 60 * 1000 + 1, store), false);
});

test("one caller hitting the limit does not block another", () => {
  const store = new Map();
  for (let i = 0; i < 40; i += 1) rateLimited("noisy", 1000 + i, store);
  assert.equal(rateLimited("noisy", 1050, store), true);
  assert.equal(rateLimited("quiet", 1050, store), false);
});

// --- the read half ---------------------------------------------------------
//
// `replay.mjs` is thinner than `log.mjs` on purpose: what may be served is
// decided by the `public_replays` view, not by a condition here. So what is
// worth pinning is the id check (it comes off a URL fragment and goes into a
// query string) and that a missing replay and an unpublished one are the same
// answer — telling them apart would disclose that some unposted game exists.

import replayHandler from "../netlify/functions/replay.mjs";

/** Run the handler with the environment and Supabase response we choose. */
async function callReplay(id, { rows = [], ok = true, configured = true } = {}) {
  const env = { SUPABASE_URL: process.env.SUPABASE_URL, SUPABASE_SERVICE_KEY: process.env.SUPABASE_SERVICE_KEY };
  const fetched = globalThis.fetch;
  process.env.SUPABASE_URL = configured ? "https://p.supabase.co" : "";
  process.env.SUPABASE_SERVICE_KEY = configured ? "service-key" : "";
  const seen = [];
  globalThis.fetch = async (url) => {
    seen.push(url);
    return { ok, status: ok ? 200 : 500, json: async () => rows, text: async () => "" };
  };
  try {
    const response = await replayHandler(
      new Request(`https://board.test/api/replay?id=${encodeURIComponent(id)}`),
    );
    return { response, body: await response.text(), seen };
  } finally {
    globalThis.fetch = fetched;
    process.env.SUPABASE_URL = env.SUPABASE_URL ?? "";
    process.env.SUPABASE_SERVICE_KEY = env.SUPABASE_SERVICE_KEY ?? "";
  }
}

test("a published replay comes back as the encoded log itself", async () => {
  const { response, body, seen } = await callReplay(MATCH, { rows: [{ log: "eNrtVNtu" }] });
  assert.equal(response.status, 200);
  // Plain text, because that is exactly what GameLog.decode wants; wrapping it
  // in JSON would only make the game unwrap it again.
  assert.match(response.headers.get("content-type"), /text\/plain/);
  assert.equal(body, "eNrtVNtu");
  // Read through the view, never the table: that is where "a posted score
  // published this" is decided.
  assert.match(seen[0], /public_replays\?select=log&match_id=eq\./);
});

test("an id of the wrong shape never reaches the query string", async () => {
  for (const bad of ["", "nope", `${MATCH}0`, "*", "eq.anything"]) {
    const { response, seen } = await callReplay(bad);
    assert.equal(response.status, 400, `accepted ${bad}`);
    assert.equal(seen.length, 0, "must not have asked the database");
  }
});

test("an unpublished replay is indistinguishable from a missing one", async () => {
  // Both are "there is no replay here to watch" — anything more would disclose
  // that some game nobody posted exists under that id.
  const { response } = await callReplay(MATCH, { rows: [] });
  assert.equal(response.status, 404);
});

test("an unconfigured board serves nothing rather than failing oddly", async () => {
  const { response, seen } = await callReplay(MATCH, { configured: false });
  assert.equal(response.status, 503);
  assert.equal(seen.length, 0);
});

test("a database error is not relayed to the caller", async () => {
  const { response, body } = await callReplay(MATCH, { ok: false });
  assert.equal(response.status, 502);
  assert.equal(body, "lookup failed");
});
