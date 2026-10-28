// The daily challenge: js/daily.mjs and netlify/functions/daily.mjs
// (docs/design/daily.md).

import assert from "node:assert/strict";
import test from "node:test";

import handler, { askedDay, askedKind } from "../netlify/functions/daily.mjs";
import {
  addDays, bestOnTheDay, dayParam, opponents, postedOn, shortDate, standings, streaks, validDay, weekdayOf,
} from "../js/daily.mjs";

test("days are UTC dates, Monday first", () => {
  assert.equal(dayParam(Date.UTC(2026, 9, 28, 23, 59)), "2026-10-28");
  assert.equal(weekdayOf("2026-10-26"), 0);
  assert.equal(weekdayOf("2026-11-01"), 6);
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
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
  const on = (name, day) => ({ name, day, onTheDay: true });
  const wins = [
    on("ann", "2026-10-26"), on("ann", "2026-10-27"), on("ann", "2026-10-28"),
    on("bo", "2026-10-26"), on("bo", "2026-10-27"),
    on("cy", "2026-10-25"),
  ];
  assert.deepEqual(streaks(wins, "2026-10-28"),
    [{ name: "ann", days: 3, onTheDay: 3 }, { name: "bo", days: 2, onTheDay: 2 }]);
  assert.deepEqual(streaks(wins, "2026-10-29"), [{ name: "ann", days: 3, onTheDay: 3 }]);
});

test("a missed day won later mends the streak, but not the true one", () => {
  const wins = [
    { name: "ann", day: "2026-10-25", onTheDay: true },
    { name: "ann", day: "2026-10-26", onTheDay: false },   // won from the archive
    { name: "ann", day: "2026-10-27", onTheDay: true },
    { name: "ann", day: "2026-10-28", onTheDay: true },
    { name: "bo", day: "2026-10-27", onTheDay: false },
    { name: "bo", day: "2026-10-28", onTheDay: false },
  ];
  assert.deepEqual(streaks(wins, "2026-10-28"),
    [{ name: "ann", days: 4, onTheDay: 2 }, { name: "bo", days: 2, onTheDay: 0 }]);
});

test("the best on the day is the best posted within it, whatever came later", () => {
  const rows = [
    { user_name: "ann", turns: 40, lost: 5, hand: 40, submitted_at: "2026-10-28T10:00:00Z" },
    { user_name: "bo", turns: 30, lost: 2, hand: 30, submitted_at: "2026-10-29T09:00:00Z" },
    { user_name: "cy", turns: 42, lost: 0, hand: 42, submitted_at: "2026-10-28T23:00:00Z" },
  ];
  assert.equal(bestOnTheDay(rows, "2026-10-28").user_name, "ann");
  assert.equal(standings(rows)[0].user_name, "bo");
  assert.equal(bestOnTheDay(rows, "2026-10-27"), null);
});

test("the function answers today's day unless asked for another", () => {
  const now = Date.UTC(2026, 9, 28, 12);
  assert.equal(askedDay(new URLSearchParams(""), now), "2026-10-28");
  assert.equal(askedDay(new URLSearchParams("day=2026-10-01"), now), "2026-10-01");
  assert.equal(askedDay(new URLSearchParams("day=nope"), now), null);
});

test("the function answers the board unless asked for the puzzle", () => {
  assert.equal(askedKind(new URLSearchParams("")), "board");
  assert.equal(askedKind(new URLSearchParams("kind=puzzle")), "puzzle");
  assert.equal(askedKind(new URLSearchParams("kind=board")), "board");
  assert.equal(askedKind(new URLSearchParams("kind=chess")), null);
});

test("the handler reads the day's row and says 404 for a missing day", async () => {
  const realFetch = globalThis.fetch;
  const asked = [];
  const settings = { mode: "random", players: 2, nodes: 10, seed: 41 };
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    const kind = String(url).includes("kind=eq.puzzle") ? "puzzle" : "board";
    const body = String(url).includes("day=eq.2026-10-01")
      ? [{ day: "2026-10-01", kind, settings, rules_version: 2 }] : [];
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    const ok = await handler(new Request("https://x/api/daily?day=2026-10-01"));
    assert.equal(ok.status, 200);
    const body = await ok.json();
    assert.deepEqual([body.day, body.kind, body.settings, body.rules_version], ["2026-10-01", "board", settings, 2]);
    assert.ok(asked[0].includes("/rest/v1/dailies?") && asked[0].includes("day=eq.2026-10-01"));
    assert.ok(asked[0].includes("kind=eq.board"));

    const puzzle = await handler(new Request("https://x/api/daily?day=2026-10-01&kind=puzzle"));
    assert.equal((await puzzle.json()).kind, "puzzle");
    assert.ok(asked.at(-1).includes("kind=eq.puzzle"));
    assert.equal((await handler(new Request("https://x/api/daily?kind=chess"))).status, 400);

    assert.equal((await handler(new Request("https://x/api/daily?day=2026-10-02"))).status, 404);
    assert.equal((await handler(new Request("https://x/api/daily?day=bad"))).status, 400);
    assert.equal((await handler(new Request("https://x/api/daily", { method: "POST" }))).status, 405);
    assert.ok(asked.at(-1).includes(`day=eq.`));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("opponents name each bot once, at its stop, and the lean heuristic", () => {
  const board = { players: 4, ai_strategy: ["heuristic", "thinker", "marshal", "thinker"],
    ai: [{}, { aux: 0 }, {}, { aux: 0 }] };
  assert.deepEqual(opponents(board), ["thinker · claudebot", "marshal"]);
  const lean = { players: 2, ai_strategy: ["heuristic", "heuristic"],
    ai: [{}, { reserve_fraction: 0, reserve_floor: 0, aux: 1 }] };
  assert.deepEqual(opponents(lean), ["heuristic+lean"]);
  assert.deepEqual(opponents({ players: 2, ai_strategy: ["heuristic", "actuary"] }), ["actuary"]);
});
