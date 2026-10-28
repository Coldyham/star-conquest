// The daily challenge: js/daily.mjs and netlify/functions/daily.mjs
// (docs/design/daily.md).

import assert from "node:assert/strict";
import test from "node:test";

import handler, { askedDay } from "../netlify/functions/daily.mjs";
import {
  addDays, dayParam, postedOn, shortDate, standings, streaks, systemsOn, validDay, weekdayOf,
} from "../js/daily.mjs";

test("days are UTC dates, Monday first", () => {
  assert.equal(dayParam(Date.UTC(2026, 9, 28, 23, 59)), "2026-10-28");
  assert.equal(weekdayOf("2026-10-26"), 0);
  assert.equal(weekdayOf("2026-11-01"), 6);
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.deepEqual(systemsOn(0), [7, 12]);
  assert.deepEqual(systemsOn(6), [25, 30]);
  assert.equal(shortDate("2026-10-28"), "Wed 28 Oct");
  assert.ok(validDay("2026-10-28"));
  for (const bad of ["2026-02-30", "2026-1-01", "today", "", null, "2026-10-28x"]) {
    assert.ok(!validDay(bad), String(bad));
  }
  assert.ok(postedOn("2026-10-28T23:59:59Z", "2026-10-28"));
  assert.ok(!postedOn("2026-10-29T00:00:01Z", "2026-10-28"));
});

test("standings keep each player's best counted win, best first", () => {
  const rows = [
    { user_name: "ann", turns: 40, lost: 5, hand: 40, submitted_at: "2026-10-28T10:00:00Z" },
    { user_name: "ann", turns: 35, lost: 9, hand: 35, submitted_at: "2026-10-28T11:00:00Z" },
    { user_name: "bo", turns: 35, lost: 2, hand: 35, submitted_at: "2026-10-28T12:00:00Z" },
    { user_name: "cy", turns: 20, lost: 0, hand: 0, submitted_at: "2026-10-28T09:00:00Z" },
  ];
  assert.deepEqual(standings(rows).map((r) => [r.user_name, r.turns, r.lost]),
    [["bo", 35, 2], ["ann", 35, 9]]);
});

test("a streak counts back from today, or yesterday when today is not won yet", () => {
  const wins = [
    { name: "ann", day: "2026-10-26" }, { name: "ann", day: "2026-10-27" },
    { name: "ann", day: "2026-10-28" },
    { name: "bo", day: "2026-10-26" }, { name: "bo", day: "2026-10-27" },
    { name: "cy", day: "2026-10-25" },
  ];
  assert.deepEqual(streaks(wins, "2026-10-28"), [{ name: "ann", days: 3 }, { name: "bo", days: 2 }]);
  assert.deepEqual(streaks(wins, "2026-10-29"), [{ name: "ann", days: 3 }]);
});

test("the function answers today's day unless asked for another", () => {
  const now = Date.UTC(2026, 9, 28, 12);
  assert.equal(askedDay(new URLSearchParams(""), now), "2026-10-28");
  assert.equal(askedDay(new URLSearchParams("day=2026-10-01"), now), "2026-10-01");
  assert.equal(askedDay(new URLSearchParams("day=nope"), now), null);
});

test("the handler reads the day's row and says 404 for a missing day", async () => {
  const realFetch = globalThis.fetch;
  const asked = [];
  const settings = { mode: "random", players: 2, nodes: 10, seed: 41 };
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    const body = String(url).includes("day=eq.2026-10-01")
      ? [{ day: "2026-10-01", settings, rules_version: 2 }] : [];
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    const ok = await handler(new Request("https://x/api/daily?day=2026-10-01"));
    assert.equal(ok.status, 200);
    const body = await ok.json();
    assert.deepEqual([body.day, body.settings, body.rules_version], ["2026-10-01", settings, 2]);
    assert.ok(asked[0].includes("/rest/v1/dailies?") && asked[0].includes("day=eq.2026-10-01"));

    assert.equal((await handler(new Request("https://x/api/daily?day=2026-10-02"))).status, 404);
    assert.equal((await handler(new Request("https://x/api/daily?day=bad"))).status, 400);
    assert.equal((await handler(new Request("https://x/api/daily", { method: "POST" }))).status, 405);
    assert.ok(asked.at(-1).includes(`day=eq.`));
  } finally {
    globalThis.fetch = realFetch;
  }
});
