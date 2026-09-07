// The upload endpoint's two pure halves: what it accepts, and how fast.
//
// The handler itself is mostly plumbing (env lookup, CORS, one fetch), so what
// is worth pinning is the validation — this is an unauthenticated endpoint whose
// rows carry 5-14 KiB blobs and a `match_id` the score verifier keys on — and
// the rate limiter's window arithmetic, which is easy to get subtly wrong and
// impossible to notice in production.

import assert from "node:assert/strict";
import test from "node:test";

import { rateLimited, validate } from "../netlify/functions/log.mjs";

const MATCH = "00112233445566ff";

/** A row the game would really post (`upload.row_for`). */
function row(over = {}) {
  return {
    match_id: MATCH,
    game_key: "abcdef0123456789",
    turns: 42,
    finished: true,
    won: true,
    hand: 40,
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
    log: "eNrtVNtu4jAQ",
  });
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
