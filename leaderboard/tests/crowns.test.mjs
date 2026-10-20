// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import {
  crownTable, parseWeek, stealFeed, stealsIn, weekAfter, weekBefore, weekParam, weekStart,
} from "../js/crowns.mjs";

const utc = (iso) => new Date(iso);
const steal = (taker, iso) => ({ taker_name: taker, submitted_at: iso });

test("a week runs Monday 00:00 UTC to the next", () => {
  // 2026-09-21 is a Monday.
  assert.equal(weekParam(weekStart(utc("2026-09-21T00:00:00Z"))), "2026-09-21");
  assert.equal(weekParam(weekStart(utc("2026-09-27T23:59:59Z"))), "2026-09-21");
  assert.equal(weekParam(weekStart(utc("2026-09-28T00:00:00Z"))), "2026-09-28");
  const start = weekStart(utc("2026-09-24T12:00:00Z"));
  assert.equal(weekParam(weekAfter(start)), "2026-09-28");
  assert.equal(weekParam(weekBefore(start)), "2026-09-14");
});

test("a steal counts in the week it was posted, and no other", () => {
  const from = utc("2026-09-21T00:00:00Z");
  const all = [
    steal("ann", "2026-09-20T23:59:59Z"),
    steal("ann", "2026-09-21T00:00:00Z"),
    steal("bo", "2026-09-27T23:59:59Z"),
    steal("bo", "2026-09-28T00:00:00Z"),
  ];
  assert.deepEqual(stealsIn(all, from, weekAfter(from)).map((s) => s.submitted_at),
    ["2026-09-21T00:00:00Z", "2026-09-27T23:59:59Z"]);
});

test("?week= snaps to its Monday, and never runs ahead of this week", () => {
  const now = utc("2026-09-24T12:00:00Z");
  assert.equal(weekParam(parseWeek("2026-09-17", now)), "2026-09-14");
  assert.equal(weekParam(parseWeek("", now)), "2026-09-21");
  assert.equal(weekParam(parseWeek("nonsense", now)), "2026-09-21");
  assert.equal(weekParam(parseWeek("2027-01-04", now)), "2026-09-21");
});

test("crowns rank first, then steals, then name, folding case", () => {
  const holders = [{ user_name: "Bo" }, { user_name: "Ann" }, { user_name: "bo" }];
  const steals = [steal("Cy", "2026-09-22T00:00:00Z"), steal("ann", "2026-09-22T00:00:00Z"),
    steal("Cy", "2026-09-23T00:00:00Z")];
  assert.deepEqual(crownTable(holders, steals).map((r) => [r.name, r.crowns, r.steals]), [
    ["Bo", 2, 0],
    ["Ann", 1, 1],
    ["Cy", 0, 2],
  ]);
});

test("a week with no steals still lists who holds crowns", () => {
  assert.deepEqual(crownTable([{ user_name: "Ann" }], []), [
    { key: "ann", name: "Ann", crowns: 1, steals: 0 },
  ]);
  assert.deepEqual(crownTable([], []), []);
});

test("the feed is newest first", () => {
  const feed = stealFeed([steal("a", "2026-09-21T01:00:00Z"), steal("b", "2026-09-23T01:00:00Z")]);
  assert.deepEqual(feed.map((s) => s.taker_name), ["b", "a"]);
});
