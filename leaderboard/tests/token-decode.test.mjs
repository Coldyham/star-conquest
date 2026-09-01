// node --test leaderboard/tests/
//
// Pins the JS decoder against real tokens from the Python encoder. Regenerate the
// fixtures with `uv run python tools/dump_challenge_fixtures.py` whenever the
// token format in starconquest/settings.py changes.

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { inflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";

import { decodeToken, fragmentOf } from "../js/token-decode.mjs";

// Node 18 has no global crypto (default from 19); the browser always does.
globalThis.crypto ??= webcrypto;

const inflate = (bytes) => new Uint8Array(inflateSync(bytes));
const fixtures = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/tokens.json", import.meta.url)), "utf8"),
);

for (const fixture of fixtures) {
  test(`token: ${fixture.name}`, async () => {
    if (fixture.reject) {
      await assert.rejects(() => decodeToken(fixture.token, inflate), (err) => {
        assert.match(err.message, new RegExp(fixture.reject));
        return true;
      });
      return;
    }

    const got = await decodeToken(fixture.token, inflate);
    for (const [field, want] of Object.entries(fixture.expect)) {
      assert.deepEqual(got[field], want, `${fixture.name}: ${field}`);
    }
    if (fixture.expectGameKeyPrefix) {
      assert.ok(got.gameKey.startsWith(fixture.expectGameKeyPrefix), got.gameKey);
      const again = await decodeToken(fixture.token, inflate);
      assert.equal(again.gameKey, got.gameKey, "fallback key must be stable");
    }
    assert.equal(got.token, fixture.token);
    assert.ok(!("challenge" in got.setup), "setup must not carry the score");
  });
}

test("a pasted full URL decodes the same as its bare token", async () => {
  const { token, expect } = fixtures.find((f) => f.name === "defaults");
  const got = await decodeToken(`https://starconquest.example/#${token}`, inflate);
  assert.equal(got.gameKey, expect.gameKey);
  assert.equal(got.token, token);
});

test("surrounding whitespace survives a clipboard round trip", async () => {
  const { token } = fixtures.find((f) => f.name === "defaults");
  assert.equal(fragmentOf(`  https://example.test/index.html#${token}\n`), token);
  assert.equal(fragmentOf(` ${token} `), token);
});

test("an empty paste is rejected", async () => {
  await assert.rejects(() => decodeToken("   ", inflate), /malformed token/);
  await assert.rejects(() => decodeToken("https://example.test/#", inflate), /malformed token/);
});
