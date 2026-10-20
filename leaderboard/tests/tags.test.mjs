// Pure normalisation, no DOM/fetch — mirrors `config_tags_tag_shape` in
// schema.sql, which stays the authoritative check; this is the client-side
// pre-filter so a bad tag doesn't need a round trip to be rejected.

import assert from "node:assert/strict";
import test from "node:test";

import { normalizeTags } from "../js/tags.mjs";

test("trims and lowercases", () => {
  assert.deepEqual(normalizeTags(" Fun , GRINDY "), ["fun", "grindy"]);
});

test("dedupes case/whitespace variants down to one entry", () => {
  assert.deepEqual(normalizeTags("Fun, fun, FUN , fun"), ["fun"]);
});

test("drops empty entries from stray commas", () => {
  assert.deepEqual(normalizeTags("fun,,  ,grindy"), ["fun", "grindy"]);
});

test("drops anything outside the allowed shape, keeping the rest", () => {
  assert.deepEqual(normalizeTags("fun!, gr#indy, ok-tag, a+b"), ["ok-tag", "a+b"]);
});

test("drops a tag longer than 24 characters; 24 itself is fine", () => {
  const long = "x".repeat(25);
  const atLimit = "x".repeat(24);
  assert.deepEqual(normalizeTags(`${long}, short`), ["short"]);
  assert.deepEqual(normalizeTags(atLimit), [atLimit]);
});

test("caps at 6 tags regardless of how many are entered", () => {
  const many = Array.from({ length: 10 }, (_, i) => `tag${i}`).join(",");
  assert.equal(normalizeTags(many).length, 6);
});

test("blank or missing input is no tags, not an error", () => {
  assert.deepEqual(normalizeTags(""), []);
  assert.deepEqual(normalizeTags(undefined), []);
  assert.deepEqual(normalizeTags(null), []);
});
