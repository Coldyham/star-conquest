// The daily challenge: js/daily.mjs and netlify/functions/daily.mjs
// (docs/design/daily.md).

import assert from "node:assert/strict";
import test from "node:test";

import handler, { askedDay, askedKind, askedResult } from "../netlify/functions/daily.mjs";
import {
  addDays, addMonths, bestOnTheDay, dailyMark, dayNumber, dayParam, numberPrefix, monthWeeks, opponents, parNote, playerDays, playerStreaks,
  postedOn, resultLine, shortDate, standings, streaks, validDay, weekDays, weekdayOf,
} from "../js/daily.mjs";

test("days are UTC dates, Monday first", () => {
  assert.equal(dayParam(Date.UTC(2026, 9, 28, 23, 59)), "2026-10-28");
  assert.equal(weekdayOf("2026-10-26"), 0);
  assert.equal(weekdayOf("2026-11-01"), 6);
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(shortDate("2026-10-28"), "Wed 28 Oct");
  assert.equal(dayNumber("2026-10-28"), 1);
  assert.equal(dayNumber("2026-11-07"), 11);
  assert.equal(dayNumber("2027-03-29"), 153);  // across the clocks changing: UTC days
  assert.equal(numberPrefix("2026-11-07"), "#11 · ");
  assert.equal(numberPrefix("2026-10-27"), "");
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

test("a par check reads a whole result, or none", () => {
  assert.equal(askedResult(new URLSearchParams("day=2026-10-01")), undefined);
  assert.deepEqual(askedResult(new URLSearchParams("turns=10&lost=3")), { turns: 10, lost: 3 });
  assert.equal(askedResult(new URLSearchParams("turns=10")), null);
  assert.equal(askedResult(new URLSearchParams("turns=0&lost=3")), null);
  assert.equal(askedResult(new URLSearchParams("turns=1.5&lost=3")), null);
  assert.equal(askedResult(new URLSearchParams("turns=10&lost=-1")), null);
});

test("the par check asks sc_puzzle_par and passes on only what it answers", async () => {
  const realFetch = globalThis.fetch;
  const asked = [];
  globalThis.fetch = async (url, init) => {
    asked.push([String(url), JSON.parse(init.body)]);
    const { p_turns: turns } = JSON.parse(init.body);
    const body = turns <= 10 ? { turns: 10, lost: 3, status: "proved", turns_only: false, beaten: turns < 10 } : null;
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    const met = await handler(new Request("https://x/api/daily?day=2026-10-01&kind=puzzle&turns=10&lost=3"));
    assert.equal(met.status, 200);
    assert.deepEqual((await met.json()).par, { turns: 10, lost: 3, status: "proved", turns_only: false, beaten: false });
    assert.ok(asked[0][0].endsWith("/rest/v1/rpc/sc_puzzle_par"));
    assert.deepEqual(asked[0][1], { p_day: "2026-10-01", p_turns: 10, p_lost: 3 });

    const short = await handler(new Request("https://x/api/daily?day=2026-10-01&kind=puzzle&turns=12&lost=0"));
    assert.equal((await short.json()).par, null);
    assert.equal((await handler(new Request("https://x/api/daily?day=2026-10-01&turns=10&lost=3"))).status, 400);
    assert.equal((await handler(new Request("https://x/api/daily?kind=puzzle&turns=x&lost=3"))).status, 400);
    assert.equal(asked.length, 2);
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

test("a player's streaks are counted per kind, mended and true", () => {
  const games = [
    { day: "2026-10-30", game_key: "p30", kind: "puzzle" },
    { day: "2026-10-31", game_key: "p31", kind: "puzzle" },
    { day: "2026-11-01", game_key: "p01", kind: "puzzle" },
    { day: "2026-10-29", game_key: "b29", kind: "board" },
  ];
  const scores = [
    { game_key: "p30", hand: 1, submitted_at: "2026-10-30T12:00:00Z" },
    { game_key: "p31", hand: 1, submitted_at: "2026-11-01T09:00:00Z" },   // from the archive
    { game_key: "p01", hand: 1, submitted_at: "2026-11-01T10:00:00Z" },
    { game_key: "b29", hand: 1, submitted_at: "2026-10-29T10:00:00Z" },   // lapsed
    { game_key: "other", hand: 1, submitted_at: "2026-11-01T10:00:00Z" },
  ];
  assert.deepEqual(playerStreaks(scores, games, "2026-11-01"),
    { board: { days: 0, onTheDay: 0 }, puzzle: { days: 3, onTheDay: 1 } });
  const autoplayed = scores.map((s) => ({ ...s, hand: 0 }));
  assert.deepEqual(playerStreaks(autoplayed, games, "2026-11-01").puzzle, { days: 0, onTheDay: 0 });
  const won = playerDays(scores, games);
  assert.deepEqual([...won.puzzle], [["2026-10-30", "day"], ["2026-10-31", "later"], ["2026-11-01", "day"]]);
  assert.deepEqual([...won.board], [["2026-10-29", "day"]]);
  // A win on the day outranks a later one on the same map, whichever is read first.
  const both = [{ game_key: "p31", hand: 1, submitted_at: "2026-11-02T09:00:00Z" },
    { game_key: "p31", hand: 1, submitted_at: "2026-10-31T09:00:00Z" }];
  assert.equal(playerDays(both, games).puzzle.get("2026-10-31"), "day");
});

test("the streak calendar's weeks and months run Monday first", () => {
  assert.deepEqual(weekDays("2026-11-04"),
    ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"]);
  assert.equal(weekDays("2026-11-01")[0], "2026-10-26");
  assert.equal(addMonths("2026-11-04", -1), "2026-10-01");
  assert.equal(addMonths("2026-01-31", -1), "2025-12-01");
  assert.equal(addMonths("2026-12-15", 1), "2027-01-01");
  const nov = monthWeeks("2026-11-20");
  assert.equal(nov.length, 6);   // the 1st is a Sunday, the 30th a Monday
  assert.deepEqual(nov[0], [null, null, null, null, null, null, "2026-11-01"]);
  assert.deepEqual(nov[5], ["2026-11-30", null, null, null, null, null, null]);
  assert.equal(monthWeeks("2027-02-10").length, 4);   // 1 Feb 2027 is a Monday
});

test("a daily's map links to its day's page", () => {
  assert.deepEqual(dailyMark({ day: "2026-11-01", kind: "puzzle" }, "2026-11-01"),
    { label: "Daily puzzle · Today", button: "Daily puzzle page", href: "puzzle.html" });
  assert.deepEqual(dailyMark({ day: "2026-10-28", kind: "board" }, "2026-11-01"),
    { label: "Daily · Wed 28 Oct", button: "Daily page", href: "daily.html?day=2026-10-28" });
});

test("a streak that reaches back to the first day read is only a lower bound", () => {
  const on = (name, day, onTheDay = true) => ({ name, day, onTheDay });
  const wins = [
    on("ann", "2026-10-29"), on("ann", "2026-10-30"), on("ann", "2026-10-31", false),
    on("bo", "2026-10-30"), on("bo", "2026-10-31"),
  ];
  assert.deepEqual(streaks(wins, "2026-11-01", "2026-10-29"), [
    { name: "ann", days: 3, onTheDay: 0, daysAtLeast: true, onTheDayAtLeast: false },
    { name: "bo", days: 2, onTheDay: 2, daysAtLeast: false, onTheDayAtLeast: false },
  ]);
  assert.deepEqual(streaks([on("cy", "2026-10-31"), on("cy", "2026-11-01")], "2026-11-01", "2026-10-31"),
    [{ name: "cy", days: 2, onTheDay: 2, daysAtLeast: true, onTheDayAtLeast: true }]);
});

test("the par's congratulation is only for a score that met it, in the game's words", () => {
  const proved = { turns: 10, lost: 3, status: "proved", turns_only: false };
  const likely = { turns: 10, lost: 3, status: "likely", turns_only: false };
  assert.equal(parNote(proved, 10, 3), "Perfect puzzle! You matched the proven best: 10 turns / 3 lost");
  assert.equal(parNote({ ...proved, turns_only: true }, 10, 3),
    "Perfect puzzle! You matched the par of 10 turns / 3 lost, proven the fastest win");
  assert.equal(parNote(likely, 10, 3), "Par! You matched the best line our search found: 10 turns / 3 lost");
  assert.equal(parNote(likely, 9, 8), "Beyond par! You won faster than the par of 10 turns / 3 lost");
  assert.equal(parNote(proved, 10, 2), "Beyond par! The par's 10 turns with fewer ships lost than its 3");
  assert.equal(parNote(likely, 10, 4), "");
  assert.equal(parNote(likely, 11, 0), "");
  assert.equal(parNote(null, 10, 3), "");
});

test("a daily's result line is the game's, word for word", () => {
  // tests/test_daily.py checks the game says these same strings.
  assert.equal(resultLine("2026-11-08", 12, 3, "board", "https://example.org/"),
    "Star Conquest daily #12 - 12 turns, 3 ships\nhttps://example.org/play/#daily");
  assert.equal(resultLine("2026-11-08", 7, 0, "puzzle", "https://example.org"),
    "Star Conquest puzzle #12 - 7 turns, 0 ships\nhttps://example.org/play/#puzzle");
  assert.equal(resultLine("2026-10-28", 9, 1, "board", ""), "Star Conquest daily #1 - 9 turns, 1 ships");
});
