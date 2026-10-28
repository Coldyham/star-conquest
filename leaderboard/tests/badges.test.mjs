// node --test leaderboard/tests/*.test.mjs
//
// badges.mjs's rules, with no DOM or database: who opened a map, which weeks a
// thief topped, what a campaign week awards, and which badge each fact earns.

import assert from "node:assert/strict";
import test from "node:test";

import {
  BADGES, TUTORIAL_FINAL_KEYS, badgeList, botFamilies, campaignRecord, dailyRecord, drawingsOpened, factsFrom, firsts,
  gauntletsCleared, grouped, stealWeeksTopped,
} from "../js/badges.mjs";
import { LADDER_ORDER } from "../js/standings.mjs";

const score = (id, game_key, user_id, submitted_at) => ({ id, game_key, user_id, submitted_at });
const earned = (facts) => new Set(badgeList(facts).filter((b) => b.earned).map((b) => b.id));

test("the first counted score opens a map; a later one by anyone else joins it", () => {
  const field = [
    score(3, "a", 1, "2026-10-02T00:00:00Z"),
    score(1, "a", 2, "2026-10-01T00:00:00Z"),
    score(2, "b", 1, "2026-10-01T00:00:00Z"),
    score(4, "b", 2, "2026-10-03T00:00:00Z"),
    score(5, "c", 2, "2026-10-01T00:00:00Z"),
  ];
  const { opened, joined } = firsts(1, field);
  assert.deepEqual([...opened], ["b"]);
  assert.deepEqual([...joined], ["a"]);
});

test("a dead heat on time goes to the lower id, as crown_steals orders", () => {
  const at = "2026-10-01T00:00:00Z";
  assert.deepEqual([...firsts(1, [score(9, "a", 2, at), score(8, "a", 1, at)]).opened], ["a"]);
});

test("a drawing is opened once, whichever seed it was first won on", () => {
  const ring = { nodes: [[0, 0], [1, 1]], lanes: [[0, 1]] };
  const games = [
    { game_key: "s1", custom_map: ring },
    { game_key: "s2", custom_map: { lanes: [[0, 1]], nodes: [[0, 0], [1, 1]] } },
    { game_key: "other", custom_map: { nodes: [[5, 5]], lanes: [] } },
  ];
  const field = [
    score(1, "s2", 2, "2026-10-01T00:00:00Z"),
    score(2, "s1", 1, "2026-10-02T00:00:00Z"),
    score(3, "other", 1, "2026-10-03T00:00:00Z"),
  ];
  assert.equal(drawingsOpened(1, games, field), 1);
  assert.equal(drawingsOpened(2, games, field), 1);
});

test("a bots column names families, folded names included, and never a random seat", () => {
  assert.deepEqual([...botFamilies(["knower", "knower:3", "council", "random", "claudebot"])].sort(),
    ["knower", "thinker"]);
});

test("a thief tops a finished week only, and a tie shares it", () => {
  const steal = (taker, at) => ({ taker_name: taker, submitted_at: at });
  const steals = [
    steal("Ann", "2026-09-22T10:00:00Z"), steal("Bo", "2026-09-23T10:00:00Z"),   // tied week
    steal("Bo", "2026-09-29T10:00:00Z"), steal("bo", "2026-09-30T10:00:00Z"),
    steal("ann", "2026-10-01T10:00:00Z"),                                          // Bo's week
    steal("Ann", "2026-10-06T10:00:00Z"),                                          // still running
  ];
  const now = new Date("2026-10-08T00:00:00Z");
  assert.equal(stealWeeksTopped(steals, "ann", now), 1);
  assert.equal(stealWeeksTopped(steals, "BO", now), 2);
  assert.equal(stealWeeksTopped(steals, "Cy", now), 0);
});

test("a campaign home counts at once; a week is won only once it has closed", () => {
  const graph = {
    nodes: [{ id: 0, kind: "home" }, { id: 1, kind: "home" }, { id: 2, kind: "field" }],
    lanes: [[0, 2], [1, 2]],
  };
  const move = (score_id, node_id, user_name, at) =>
    ({ score_id, node_id, user_name, turns: 20, lost: 5, submitted_at: at, campaign_start: "" });
  const scores = [
    move(1, 0, "Ann", "2026-09-21T10:00:00Z"),
    move(2, 1, "Bo", "2026-09-21T11:00:00Z"),
    move(3, 2, "Ann", "2026-09-22T10:00:00Z"),
  ];
  const closed = [{ week_start: "2026-09-21", graph, scores }];
  const live = [{ week_start: "2026-09-21", graph, scores }];
  assert.deepEqual(campaignRecord(closed, "ann", new Date("2026-09-28T00:00:00Z")), { homes: 1, wins: 1 });
  assert.deepEqual(campaignRecord(live, "Ann", new Date("2026-09-27T23:00:00Z")), { homes: 1, wins: 0 });
  // Bo holds a home and nothing else: a home alone never wins the week.
  assert.deepEqual(campaignRecord(closed, "Bo", new Date("2026-10-01T00:00:00Z")), { homes: 1, wins: 0 });
});

test("a gauntlet is cleared by a win on its last stage's setup, whatever the key order", () => {
  const stage = (settings) => ({ settings });
  const gauntlets = [
    { gauntlet_id: "two-aaaaaaaaaaaa", stages: [stage({ players: 2, nodes: 9, seed: 1 }),
                                                stage({ players: 3, nodes: 12, seed: 7, mode: "random" })] },
    { gauntlet_id: "one-bbbbbbbbbbbb", stages: [stage({ players: 2, nodes: 9, seed: 1 })] },
    { gauntlet_id: "bad-cccccccccccc", stages: null },
  ];
  // A posted win's settings_json: keys in another order, autoplay along for the ride.
  const wins = [{ seed: 7, mode: "random", nodes: 12, players: 3, autoplay: false }];
  assert.equal(gauntletsCleared(gauntlets, wins), 1);
  assert.equal(gauntletsCleared(gauntlets, [{ players: 2, nodes: 9, seed: 1 }]), 1);
  assert.equal(gauntletsCleared(gauntlets, []), 0);
});

test("each way to play is read off the setup, and a hand-drawn map is neither mode", () => {
  const facts = factsFrom({
    wins: [
      { game_key: "x", mode: "random", settings_json: { custom_map: { nodes: [] } }, bots: ["heuristic"] },
      { game_key: "y", mode: "symmetric", settings_json: { fog_sight: 1, in_lane_battles: true },
        bots: ["knower", "knower:2"] },
    ],
  });
  assert.equal(facts.random, false);
  assert.equal(facts.symmetric, true);
  assert.equal(facts.fog, true);
  assert.equal(facts.laneBattles, true);
  assert.equal(facts.handMaps, 1);
  const got = earned(facts);
  for (const id of ["first-win", "symmetric", "fog", "lane-battles", "hand-map", "bot-knower", "bot-heuristic"]) {
    assert.ok(got.has(id), id);
  }
  for (const id of ["random", "cartographer", "bot-actuary", "graduate"]) assert.ok(!got.has(id), id);
});

test("a win on any of the tutorial's last-stage keys is the Graduate badge", () => {
  const win = (game_key) => ({ game_key, mode: "random", settings_json: {}, bots: [] });
  assert.ok(earned(factsFrom({ wins: [win(TUTORIAL_FINAL_KEYS.at(-1))] })).has("graduate"));
  assert.ok(!earned(factsFrom({ wins: [win("0000000000000000")] })).has("graduate"));
});

test("dailies: any win completes one, the best on the day wins it, seven in a week make a full week", () => {
  // Mon 19 Oct to Sun 25 Oct, then Mon 26 Oct and today, Wed 28 Oct.
  const days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25",
    "2026-10-26", "2026-10-28"];
  const dailyGames = days.map((day) => ({ day, game_key: `k${day}` }));
  const win = (user_id, day, turns, at, hand = turns) =>
    ({ game_key: `k${day}`, user_id, user_name: `p${user_id}`, turns, lost: 0, hand, submitted_at: at });
  const field = [
    // Ann wins Mon to Sat on the day, and Sunday a day late, from the archive.
    ...days.slice(0, 6).map((day) => win(1, day, 30, `${day}T12:00:00Z`)),
    win(1, "2026-10-25", 30, "2026-10-26T09:00:00Z"),
    // Bo beats her on the Monday, on the day; and on the Tuesday only later.
    win(2, "2026-10-19", 25, "2026-10-19T20:00:00Z"),
    win(2, "2026-10-20", 20, "2026-10-21T08:00:00Z"),
    // Bo's Sunday was autoplayed through: it never counts.
    win(2, "2026-10-25", 10, "2026-10-25T08:00:00Z", 0),
    // Today's best can't be the day's winner until the day ends.
    win(2, "2026-10-28", 10, "2026-10-28T08:00:00Z"),
    // A map that isn't a daily.
    { game_key: "other", user_id: 2, user_name: "p2", turns: 5, lost: 0, hand: 5, submitted_at: "2026-10-20T00:00:00Z" },
  ];
  const now = new Date("2026-10-28T12:00:00Z");
  assert.deepEqual(dailyRecord(1, dailyGames, field, now), { days: 7, won: 5, weeks: 1 });
  assert.deepEqual(dailyRecord(2, dailyGames, field, now), { days: 3, won: 1, weeks: 0 });
  assert.deepEqual(dailyRecord(3, dailyGames, field, now), { days: 0, won: 0, weeks: 0 });

  const state = (daily) => Object.fromEntries(badgeList(factsFrom({ daily })).map((b) => [b.id, b.earned]));
  assert.deepEqual([state({ days: 7, won: 5, weeks: 1 })].map((s) => [s.daily, s["daily-winner"], s["full-week"]]),
    [[true, true, true]]);
  assert.deepEqual([state({ days: 3, won: 0, weeks: 0 })].map((s) => [s.daily, s["daily-winner"], s["full-week"]]),
    [[true, false, false]]);
  assert.deepEqual([state(null)].map((s) => [s.daily, s["daily-winner"], s["full-week"]]), [[null, null, null]]);
});

test("a count a function couldn't answer is unknown, never locked", () => {
  const list = badgeList(factsFrom({}));
  const state = Object.fromEntries(list.map((b) => [b.id, b.earned]));
  assert.equal(state["pen-pal"], null);
  assert.equal(state["by-return"], null);
  assert.equal(state.architect, null);
  assert.equal(state["first-win"], false);
  assert.equal(badgeList(factsFrom({ gauntletsCleared: null })).find((b) => b.id === "gauntlet-runner").earned, null);

  const known = Object.fromEntries(badgeList(factsFrom({ pbp: { played: 2, won: 0 }, gauntletsMade: 1 }))
    .map((b) => [b.id, b.earned]));
  assert.equal(known["pen-pal"], true);
  assert.equal(known["by-return"], false);
  assert.equal(known.architect, true);
});

test("every ladder bot has a badge, every id is unique, and groups stay together", () => {
  const ids = BADGES.map((b) => b.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const bot of LADDER_ORDER) assert.ok(ids.includes(`bot-${bot}`), bot);
  const groups = grouped(badgeList(factsFrom({}))).map((g) => g.group);
  assert.equal(new Set(groups).size, groups.length);
});
