// insert()'s conflict-handling options and rpc() — the two additions api.mjs
// needed for score-linked tags (config_tags's bulk insert, sc_config_key's
// call). Mocks fetch the same way functions.test.mjs does for the Netlify
// handlers; config.mjs's real (public) SUPABASE_URL/KEY are already truthy,
// so configured() needs no environment setup here.

import assert from "node:assert/strict";
import test from "node:test";

import { insert, rpc } from "../js/api.mjs";

/** Stub fetch to answer with `body` (JSON-encoded on the way out, exactly as
 * PostgREST would), and hand back every call made so a test can inspect the
 * URL and options actually sent. */
function mockFetch(body) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: body === undefined ? 204 : 201,
      text: async () => (body === undefined ? "" : JSON.stringify(body)),
      json: async () => ({}),
    };
  };
  return { calls, restore: () => { globalThis.fetch = real; } };
}

test("insert posts JSON with return=minimal by default", async () => {
  const { calls, restore } = mockFetch(undefined);
  try {
    await insert("config_tags", { config_key: "abc", tag: "fun" });
    assert.equal(calls[0].options.method, "POST");
    assert.equal(calls[0].options.headers.Prefer, "return=minimal");
    assert.match(calls[0].url, /\/rest\/v1\/config_tags$/);
    assert.deepEqual(JSON.parse(calls[0].options.body), { config_key: "abc", tag: "fun" });
  } finally {
    restore();
  }
});

test("an array body (bulk insert) goes through unchanged", async () => {
  const { calls, restore } = mockFetch(undefined);
  try {
    const rows = [{ tag: "fun" }, { tag: "grindy" }];
    await insert("config_tags", rows);
    assert.deepEqual(JSON.parse(calls[0].options.body), rows);
  } finally {
    restore();
  }
});

test("onConflict + ignoreDuplicates adds the query string and Prefer resolution", async () => {
  const { calls, restore } = mockFetch(undefined);
  try {
    await insert("config_tags", [{ tag: "fun" }], { onConflict: "score_id,tag_key", ignoreDuplicates: true });
    assert.match(calls[0].url, /\/rest\/v1\/config_tags\?on_conflict=score_id,tag_key$/);
    assert.equal(calls[0].options.headers.Prefer, "return=minimal,resolution=ignore-duplicates");
  } finally {
    restore();
  }
});

test("returning:true asks for the row back and the caller sees it", async () => {
  const { calls, restore } = mockFetch([{ id: 7 }]);
  try {
    const [row] = await insert("scores", { turns: 10 }, { returning: true });
    assert.equal(row.id, 7);
    assert.equal(calls[0].options.headers.Prefer, "return=representation");
  } finally {
    restore();
  }
});

test("rpc posts to rpc/<name> with the args as the body", async () => {
  const { calls, restore } = mockFetch(undefined);
  try {
    await rpc("sc_config_key", { settings: { mode: "random" } });
    assert.match(calls[0].url, /\/rest\/v1\/rpc\/sc_config_key$/);
    assert.equal(calls[0].options.method, "POST");
    assert.deepEqual(JSON.parse(calls[0].options.body), { settings: { mode: "random" } });
  } finally {
    restore();
  }
});

test("rpc against a scalar-returning function resolves to the bare value, not a wrapped row", async () => {
  // PostgREST's own behaviour for a `returns text` function: the JSON body is
  // the scalar itself, e.g. `"a1b2c3d4e5f6a7b8"`, not `[{"sc_config_key": "…"}]`.
  const { restore } = mockFetch("a1b2c3d4e5f6a7b8");
  try {
    const key = await rpc("sc_config_key", { settings: {} });
    assert.equal(key, "a1b2c3d4e5f6a7b8");
  } finally {
    restore();
  }
});
