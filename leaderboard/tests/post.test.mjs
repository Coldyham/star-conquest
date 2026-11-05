// quickPost: the game's one-press post, run against a stubbed PostgREST. It
// files the score exactly as the submit page does (postScore) and answers the
// map's aggregate, never the score list.

import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { quickPost } from "../js/post.mjs";

globalThis.crypto ??= webcrypto;

const fixtures = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/tokens.json", import.meta.url)), "utf8"),
);
const scored = fixtures.find((f) => f.name === "defaults");

/** Answer each request by the first route whose pattern its URL matches; a
 * route answering an Error fails the request as PostgREST would. */
function mockFetch(routes) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    const route = routes.find(([pattern]) => pattern.test(url));
    const answer = route ? route[1] : [];
    const failed = answer instanceof Error;
    return {
      ok: !failed,
      status: failed ? 500 : 200,
      text: async () => JSON.stringify(failed ? { message: answer.message } : answer),
      json: async () => (failed ? { message: answer.message } : answer),
      headers: { get: () => null },
    };
  };
  return { calls, restore: () => { globalThis.fetch = real; } };
}

const SUMMARY = { score_count: 4, best_turns: 20, best_lost: 1, best_user_name: "Bob", best_holders: 1 };

test("posts the score on a known map and answers its summary", async () => {
  const { calls, restore } = mockFetch([
    [/\/games\?select=game_key&game_key=/, [{ game_key: scored.expect.gameKey }]],
    [/\/users\?select=id/, [{ id: 7 }]],
    [/\/scores$/, [{ id: 99 }]],
    [/\/game_summary\?/, [SUMMARY]],
  ]);
  try {
    const got = await quickPost(scored.token, "Ada");
    assert.deepEqual(got, { game_key: scored.expect.gameKey, ...SUMMARY });
    const insert = calls.find((c) => c.method === "POST" && /\/scores$/.test(c.url));
    const row = JSON.parse(insert.body);
    assert.equal(row.user_id, 7);
    assert.equal(row.turns, scored.expect.challenge.turns);
    assert.equal(row.lost, scored.expect.challenge.lost);
    assert.equal(row.raw_token, scored.token);
    // Never the score list: the answer is what an embargoed page shows.
    assert.ok(!calls.some((c) => /\/(counted_)?scores\?/.test(c.url)));
  } finally {
    restore();
  }
});

test("a summary that cannot be read still answers the posted key", async () => {
  const { restore } = mockFetch([
    [/\/games\?select=game_key&game_key=/, [{ game_key: scored.expect.gameKey }]],
    [/\/users\?select=id/, [{ id: 7 }]],
    [/\/scores$/, [{ id: 99 }]],
    [/\/game_summary\?/, new Error("view missing")],
  ]);
  try {
    assert.deepEqual(await quickPost(scored.token, "Ada"), { game_key: scored.expect.gameKey });
  } finally {
    restore();
  }
});

test("a refused score rejects, so the game falls back to the form", async () => {
  const { restore } = mockFetch([
    [/\/games\?select=game_key&game_key=/, [{ game_key: scored.expect.gameKey }]],
    [/\/users\?select=id/, [{ id: 7 }]],
    [/\/scores$/, new Error("insert refused")],
  ]);
  try {
    await assert.rejects(() => quickPost(scored.token, "Ada"), /insert refused/);
  } finally {
    restore();
  }
});

test("no name, or a link without a score, posts nothing", async () => {
  const { calls, restore } = mockFetch([]);
  try {
    await assert.rejects(() => quickPost(scored.token, "  "), /name/);
    const bare = fixtures.find((f) => !f.reject && f.expect && f.expect.challenge === null);
    if (bare) await assert.rejects(() => quickPost(bare.token, "Ada"), /no score/);
    assert.equal(calls.length, 0);
  } finally {
    restore();
  }
});

test("a name someone else has claimed is refused before any score is sent", async () => {
  const { calls, restore } = mockFetch([
    [/\/games\?select=game_key&game_key=/, [{ game_key: scored.expect.gameKey }]],
    [/\/users\?select=id/, [{ id: 7, name: "Ada", claimed: true }]],
    [/\/scores$/, [{ id: 99 }]],
  ]);
  try {
    await assert.rejects(() => quickPost(scored.token, "Ada"), /claimed/);
    assert.ok(!calls.some((c) => c.method === "POST" && /\/scores$/.test(c.url)));
  } finally {
    restore();
  }
});
