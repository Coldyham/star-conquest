// node --test leaderboard/tests/*.test.mjs
//
// badges.mjs's rules, with no DOM or database: who opened a map, which weeks a
// thief topped, what a campaign week awards, and which badge each fact earns.

import assert from "node:assert/strict";
import test from "node:test";

import {
  BADGES, TUTORIAL_FINAL_KEYS, badgeList, botFamilies, campaignRecord, drawingsOpened, factsFrom, firsts,
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

test("a count a function couldn't answer is unknown, never locked", () => {
  const list = badgeList(factsFrom({}));
  const state = Object.fromEntries(list.map((b) => [b.id, b.earned]));
  assert.equal(state["pen-pal"], null);
  assert.equal(state["by-return"], null);
  assert.equal(state.architect, null);
  assert.equal(state["first-win"], false);

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
