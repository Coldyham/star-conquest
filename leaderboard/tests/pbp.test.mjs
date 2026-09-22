// The play-by-post endpoint. Like log.mjs's tests, this pins the places the
// handler makes a *decision* rather than its plumbing: what a submission must
// look like before it reaches a client, how a seat token is checked, and the gate
// that decides a turn is ready to resolve.
//
// The token tests are the ones worth having. A seat token is the only thing
// standing between a player and somebody else's ships, and it is the one piece of
// this design with no equivalent anywhere else in the codebase.

import assert from "node:assert/strict";
import test from "node:test";

import {
  allowedOrigin, deadlinePassed, hashToken, lapsedAction, mintToken, outstanding,
  rateLimited, sameToken, seatForToken, validateMatch, validateOrders,
  validateSeats, visibleOrders,
} from "../netlify/functions/pbp.mjs";

const MATCH = "00112233445566ff";

function match(over = {}) {
  return {
    match_id: MATCH,
    settings_json: { mode: "random", players: 2, nodes: 20, seed: 7 },
    seed: 7,
    seats: [1, 2],
    rules_version: 2,
    ...over,
  };
}

// --------------------------------------------------------------------------
// Seat tokens
// --------------------------------------------------------------------------
test("a minted token is 128 bits of hex, and never the same twice", () => {
  const seen = new Set();
  for (let i = 0; i < 200; i += 1) {
    const token = mintToken();
    assert.match(token, /^[0-9a-f]{32}$/);
    assert.ok(!seen.has(token), "tokens must not repeat");
    seen.add(token);
  }
});

test("a token is stored only as a hash", async () => {
  const token = mintToken();
  const hash = await hashToken(token);
  assert.equal(hash.length, 64);
  assert.notEqual(hash, token);
  assert.equal(hash, await hashToken(token), "the same token must hash the same");
  assert.notEqual(hash, await hashToken(mintToken()));
});

test("token comparison rejects a mismatch, whatever its shape", () => {
  const hash = "a".repeat(64);
  assert.ok(sameToken(hash, hash));
  assert.ok(!sameToken(hash, "b".repeat(64)));
  assert.ok(!sameToken(hash, "a".repeat(63)), "a length difference is a mismatch");
  assert.ok(!sameToken(hash, null));
  assert.ok(!sameToken(undefined, hash));
});

test("a token names the one seat it was minted for", async () => {
  const one = mintToken(), two = mintToken();
  const stored = { seats: { seats: [1, 2], tokens: {
    1: await hashToken(one), 2: await hashToken(two) } } };
  assert.equal(seatForToken(stored, await hashToken(one)), 1);
  assert.equal(seatForToken(stored, await hashToken(two)), 2);
});

test("a token nobody was given holds no seat at all", async () => {
  const stored = { seats: { seats: [1], tokens: { 1: await hashToken(mintToken()) } } };
  assert.equal(seatForToken(stored, await hashToken(mintToken())), null);
  assert.equal(seatForToken(stored, "not a hash"), null);
  // A match stored without a token map must refuse every token rather than
  // throwing — an unseated match is a broken row, not a way in.
  assert.equal(seatForToken({}, await hashToken(mintToken())), null);
  assert.equal(seatForToken(null, "x"), null);
});

// --------------------------------------------------------------------------
// Orders on the wire
// --------------------------------------------------------------------------
test("a well-formed order list is accepted and normalised", () => {
  assert.deepEqual(validateOrders([{ src: 3, dst: 7, ships: 12 }]),
                   [{ src: 3, dst: 7, ships: 12 }]);
  assert.deepEqual(validateOrders([]), []);
});

test("an order may not name an owner", () => {
  // The seat comes from the token. Anything else on the object is dropped rather
  // than carried, so a foreign order cannot be expressed at all.
  const out = validateOrders([{ src: 1, dst: 2, ships: 3, owner: 4 }]);
  assert.deepEqual(out, [{ src: 1, dst: 2, ships: 3 }]);
  assert.ok(!("owner" in out[0]));
});

test("a malformed order is refused rather than coerced", () => {
  assert.equal(typeof validateOrders("nope"), "string");
  assert.equal(typeof validateOrders([{ src: 1.5, dst: 2, ships: 3 }]), "string");
  assert.equal(typeof validateOrders([{ src: 1, dst: 2, ships: 0 }]), "string");
  assert.equal(typeof validateOrders([{ src: 1, dst: 2, ships: -4 }]), "string");
  assert.equal(typeof validateOrders([{ src: -1, dst: 2, ships: 3 }]), "string");
  assert.equal(typeof validateOrders([null]), "string");
  assert.equal(typeof validateOrders([{ src: 1, dst: 2 }]), "string");
});

test("an absurd number of orders is refused before it is stored", () => {
  const many = Array.from({ length: 300 }, () => ({ src: 1, dst: 2, ships: 1 }));
  assert.equal(typeof validateOrders(many), "string");
});

// --------------------------------------------------------------------------
// Opening a match
// --------------------------------------------------------------------------
test("a well-formed match is accepted", () => {
  const out = validateMatch(match());
  assert.equal(out.match_id, MATCH);
  assert.deepEqual(out.seats, [1, 2]);
  assert.equal(out.deadline_hours, null, "no deadline is a real choice");
});

test("a match is refused on anything malformed", () => {
  assert.equal(typeof validateMatch(null), "string");
  assert.equal(typeof validateMatch(match({ match_id: "nope" })), "string");
  assert.equal(typeof validateMatch(match({ seed: 1.5 })), "string");
  assert.equal(typeof validateMatch(match({ settings_json: "x" })), "string");
  assert.equal(typeof validateMatch(match({ seats: [] })), "string");
  assert.equal(typeof validateMatch(match({ deadline_hours: 0 })), "string");
  assert.equal(typeof validateMatch(match({ deadline_hours: 9999 })), "string");
});

test("seats are validated against the real player limit", () => {
  assert.deepEqual(validateSeats([2, 1]), [1, 2], "...and sorted");
  assert.equal(typeof validateSeats([0]), "string", "seat 0 is neutral");
  assert.equal(typeof validateSeats([7]), "string", "config.MAX_PLAYERS is 6");
  assert.equal(typeof validateSeats([1, 1]), "string", "one person per seat");
});

// --------------------------------------------------------------------------
// The gate: when is a turn ready?
// --------------------------------------------------------------------------
test("a turn is outstanding until every seat has submitted", () => {
  assert.deepEqual(outstanding([1, 2, 3], []), [1, 2, 3]);
  assert.deepEqual(outstanding([1, 2, 3], [2]), [1, 3]);
  assert.deepEqual(outstanding([1, 2, 3], [1, 2, 3]), []);
});

test("a duplicate submission cannot make a turn look ready", () => {
  // Counting rows rather than seats would resolve this turn on one seat's two
  // submissions. The unique constraint forbids that too; this must not rely on it.
  assert.deepEqual(outstanding([1, 2], [1, 1]), [2]);
});

// --------------------------------------------------------------------------
// Deadlines
// --------------------------------------------------------------------------
test("a match with no deadline never lapses", () => {
  assert.ok(!deadlinePassed("2026-01-01T00:00:00Z", null, Date.parse("2030-01-01T00:00:00Z")));
  assert.ok(!deadlinePassed("2026-01-01T00:00:00Z", undefined, Date.now()));
});

test("a deadline passes only once its hours are up", () => {
  const opened = "2026-01-01T00:00:00Z";
  const at = (iso) => Date.parse(iso);
  assert.ok(!deadlinePassed(opened, 48, at("2026-01-02T23:59:00Z")));
  assert.ok(deadlinePassed(opened, 48, at("2026-01-03T00:00:00Z")));
});

test("an unreadable timestamp is never treated as lapsed", () => {
  assert.ok(!deadlinePassed("not a date", 24, Date.now()));
});

test("the first miss holds, the second falls to the bot", () => {
  // Diplomacy's two-stage convention: submitting nothing is already a legal turn,
  // so a first miss needs no bot and invents nothing.
  assert.equal(lapsedAction(0), "hold");
  assert.equal(lapsedAction(1), "bot");
  assert.equal(lapsedAction(5), "bot");
});

// --------------------------------------------------------------------------
// Shared with log.mjs
// --------------------------------------------------------------------------
test("CORS admits the game's own deploys and nothing else", () => {
  assert.ok(allowedOrigin("https://star-conquest.netlify.app"));
  assert.ok(allowedOrigin("https://deploy-preview-42--star-conquest.netlify.app"));
  assert.ok(allowedOrigin("http://localhost:8000"));
  assert.ok(!allowedOrigin("https://evil-star-conquest.netlify.app"));
  assert.ok(!allowedOrigin("http://star-conquest.netlify.app"), "https only");
  assert.ok(!allowedOrigin(null), "a desktop build sends no Origin");
});

test("the rate limit allows a polling client and stops a flood", () => {
  const store = new Map();
  const now = Date.now();
  // A client polling every few seconds for an hour stays well inside it.
  for (let i = 0; i < 240; i += 1) {
    assert.ok(!rateLimited("ip", now + i, store), `poll ${i} should pass`);
  }
  assert.ok(rateLimited("ip", now + 240, store), "...but the next is refused");
  // A different caller is unaffected.
  assert.ok(!rateLimited("other", now + 240, store));
});

// --------------------------------------------------------------------------
// Which orders a client may see (regression)
// --------------------------------------------------------------------------
const ROWS = [
  { turn: 0, seat: 1, orders_json: [] },
  { turn: 0, seat: 2, orders_json: [] },
  { turn: 1, seat: 1, orders_json: [] },
];

test("the live turn's orders are withheld while anyone is still to submit", () => {
  // Otherwise a player composing their own orders could read everyone else's,
  // and a simultaneous turn stops being simultaneous.
  const out = visibleOrders(ROWS, 1, [2]);
  assert.deepEqual(out.map((r) => r.turn), [0, 0]);
});

test("...and released the moment the turn is complete", () => {
  // The other half, and the one a live match caught: a client resolving a turn
  // it cannot see the orders for plays it as though nobody moved.
  const out = visibleOrders(ROWS, 1, []);
  assert.deepEqual(out.map((r) => r.turn), [0, 0, 1]);
});

test("a resolved turn's orders are always visible", () => {
  assert.equal(visibleOrders(ROWS, 2, [1, 2]).length, 3);
});
