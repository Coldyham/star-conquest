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
import { botWatchSetup, encodeToken, newSeedSetup } from "../js/token-encode.mjs";

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

test("a config link decodes with nothing to post, and no map to register either", async () => {
  // submit.html's reader takes it as a bare setup (challenge: null) rather than
  // rejecting outright — but newSeedSetup always surrenders the seed, and
  // submit.mjs refuses to register a setup with none: there is no single map
  // to hand a name and an embargo to, which is rather the point of offering
  // someone a fresh roll instead of the exact map that was played.
  const token = await encodeToken(newSeedSetup({ mode: "random", players: 3, nodes: 18, seed: 5 }), deflate);
  const decoded = await decodeToken(token, inflate);
  assert.equal(decoded.challenge, null);
  assert.equal(decoded.seed, null);
});

test("a bot watch link keeps the map and hands the human's seat to the bot", () => {
  const setup = botWatchSetup(
    { mode: "random", players: 3, nodes: 18, seed: 7, ai_strategy: ["heuristic", "thinker"], ai: [{ aux: 9 }, { reserve_fraction: 0.5 }] },
    "knower",
    12,
  );
  assert.deepEqual(setup, {
    mode: "random",
    players: 3,
    nodes: 18,
    seed: 7, // unlike newSeedSetup, a bot replays the map actually played
    ai_strategy: ["knower", "thinker", "heuristic"],
    ai: [{ aux: 12 }, { reserve_fraction: 0.5 }, {}],
    autoplay: true,
  });
});

test("a bot watch link discards whatever seat 1's own strategy and params were", () => {
  // Mirrors tools/sim.play_settings: that slot belongs to the human, not the bot
  // being measured, so its prior value (however tuned) is not the bot's identity.
  const setup = botWatchSetup(
    { players: 2, ai_strategy: ["thinker"], ai: [{ aux: 9, reserve_fraction: 0.9 }] },
    "rusherplus",
    undefined,
  );
  assert.deepEqual(setup.ai_strategy, ["rusherplus", "heuristic"]);
  assert.deepEqual(setup.ai, [{}, {}]);
});

test("a bot watch link's aux is omitted at the untuned default", () => {
  const untuned = botWatchSetup({ players: 1 }, "heuristic", 1);
  assert.deepEqual(untuned.ai, [{}]);
  const tuned = botWatchSetup({ players: 1 }, "knower", 8);
  assert.deepEqual(tuned.ai, [{ aux: 8 }]);
});

test("a bot watch link still encodes with no settings_json or player count at all", async () => {
  const setup = botWatchSetup(null, "heuristic", 1);
  assert.deepEqual(setup, { ai_strategy: ["heuristic"], ai: [{}], autoplay: true });
  await encodeToken(setup, deflate); // must not throw
});

test("a bot watch link is not a score to post either", async () => {
  const token = await encodeToken(
    botWatchSetup({ mode: "random", players: 3, nodes: 18, seed: 5 }, "knower", 12),
    deflate,
  );
  const decoded = await decodeToken(token, inflate);
  assert.equal(decoded.challenge, null);
});
