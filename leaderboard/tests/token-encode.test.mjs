// node --test leaderboard/tests/
//
// The writing half's tests. The decoder's fixtures pin *this* format against the
// Python encoder (token-decode.test.mjs); what's left to pin here is that a
// config link says what it means to say — the setup, no seed, no score — and
// that the bytes come out in the one shape both readers accept.

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";
import { deflateSync, inflateSync } from "node:zlib";

import { decodeToken } from "../js/token-decode.mjs";
import { encodeToken, newSeedSetup } from "../js/token-encode.mjs";

globalThis.crypto ??= webcrypto;

const deflate = (bytes) => new Uint8Array(deflateSync(bytes, { level: 9 }));
const inflate = (bytes) => new Uint8Array(inflateSync(bytes));

/** The dict inside a token, however it was encoded — the sniff both readers do. */
function payloadOf(token) {
  const b64 = token.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (c) => c.charCodeAt(0));
  const raw = bytes[0] === 0x7b ? bytes : inflate(bytes);
  return JSON.parse(new TextDecoder().decode(raw));
}

test("a config link keeps the tuning and gives up the map", () => {
  const setup = newSeedSetup({
    mode: "symmetric",
    players: 4,
    nodes: 24,
    seed: 1234,
    autoplay: true,
    defender_advantage: 1.5,
    ai_strategy: ["", "thinker"],
  });
  assert.equal(setup.seed, null, "the seed is surrendered, not carried");
  assert.ok(!("autoplay" in setup), "autoplay is a play style, not part of the setup");
  assert.deepEqual(setup, {
    mode: "symmetric",
    players: 4,
    nodes: 24,
    seed: null,
    defender_advantage: 1.5,
    ai_strategy: ["", "thinker"],
  });
});

test("the two dropped keys are the two sc_config_key drops, and nothing else", () => {
  // If this ever disagrees with schema.sql's sc_config_key, the button would
  // offer a setup the page didn't group by.
  const full = { mode: "random", players: 3, nodes: 18, seed: 7, autoplay: false, fog_sight: 3 };
  const dropped = Object.keys(full).filter((k) => !(k in newSeedSetup(full)));
  assert.deepEqual(dropped, ["autoplay"]); // seed survives, emptied to null
  assert.equal(newSeedSetup(full).seed, null);
});

test("a missing settings_json still encodes to something playable", async () => {
  // settings_json is NOT NULL in schema.sql, so this is belt and braces: an
  // all-defaults setup is a real game, not a broken link.
  assert.deepEqual(newSeedSetup(null), { seed: null });
  assert.deepEqual(payloadOf(await encodeToken(newSeedSetup(null), deflate)), { seed: null });
});

test("the token round-trips, compressed or not", async () => {
  const setup = newSeedSetup({ mode: "random", players: 3, nodes: 18, seed: 42, combat_jitter: 0.25 });
  for (const compressor of [deflate, null]) {
    const token = await encodeToken(setup, compressor);
    assert.deepEqual(payloadOf(token), setup, compressor ? "deflated" : "plain");
  }
});

test("the fragment is URL-safe base64 with no padding", async () => {
  const token = await encodeToken(newSeedSetup({ mode: "symmetric", players: 6, nodes: 40 }), deflate);
  assert.match(token, /^[A-Za-z0-9_-]+$/);
});

test("a config link is not a score to post", async () => {
  // submit.html's reader must reject it: there is no result on this setup yet,
  // which is rather the point of handing someone a fresh map.
  const token = await encodeToken(newSeedSetup({ mode: "random", players: 3, nodes: 18, seed: 5 }), deflate);
  await assert.rejects(() => decodeToken(token, inflate), /not a challenge link/);
});
