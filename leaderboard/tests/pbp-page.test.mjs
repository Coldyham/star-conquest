// The lobby page's pure helpers: grouping, the seat link's shape, and the
// deadline wording. The DOM half is exercised by hand on a deploy preview.

import assert from "node:assert/strict";
import test from "node:test";

import { deadlineNote, groupMatches, seatLink, waitingNote } from "../js/pbp.mjs";

const base = { match_id: "00112233445566ff", seats: [1, 2], turn: 3, finished: false,
               waiting: [2], lapsed: {}, deadline_hours: 24,
               turn_opened_at: "2026-09-24T00:00:00Z" };

test("matches group by status, unknown ones as in progress", () => {
  const groups = groupMatches([{ status: "open" }, { status: "lapsed" }, { status: "weird" }]);
  assert.equal(groups.open.length, 1);
  assert.equal(groups.lapsed.length, 1);
  assert.equal(groups.in_progress.length, 1);
  assert.equal(groups.finished.length, 0);
});

test("a seat link is the game's own #pbp fragment", () => {
  assert.equal(seatLink("00112233445566ff", "ab".repeat(16), "https://g/"),
               `https://g/#pbp=00112233445566ff:${"ab".repeat(16)}`);
});

test("the deadline reads forwards and backwards, and not at all without one", () => {
  const opened = Date.parse(base.turn_opened_at);
  assert.equal(deadlineNote(base, opened + 19 * 3600000), "due in 5h");
  assert.equal(deadlineNote(base, opened + 26 * 3600000), "overdue by 2h");
  assert.equal(deadlineNote({ ...base, deadline_hours: null }, opened), "");
});

test("the waiting note names lapsed seats and what happens to them", () => {
  assert.equal(waitingNote({ ...base, lapsed: { 2: "bot" } }),
               "waiting on seat 2 · seat 2 lapsed (bot plays)");
  assert.equal(waitingNote({ ...base, finished: true }), "");
});
