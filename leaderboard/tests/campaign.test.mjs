// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import {
  attemptStatus, campaignMark, canAttempt, COOLDOWN_MS, fold, GRACE_MS, nodeRadius, playerHue,
} from "../js/campaign.mjs";

// Homes 10 and 11 hang off field nodes 0 and 2; the field is a line 0 - 1 - 2.
//
//   10 - 0 - 1 - 2 - 11
const graph = {
  nodes: [
    { id: 0, kind: "field" }, { id: 1, kind: "field" }, { id: 2, kind: "field" },
    { id: 10, kind: "home" }, { id: 11, kind: "home" },
  ],
  lanes: [[0, 1], [1, 2], [10, 0], [11, 2]],
};

// Two hours apart: past every cooldown and grace period, so the tests of the
// basic rules read on their own. The timing tests below post at given minutes.
const STEP = 2 * 60 * 60 * 1000;
let clock = 0;
const win = (name, node, turns, lost = 0) => ({
  node_id: node, score_id: ++clock, user_name: name, turns, lost,
  submitted_at: new Date(Date.UTC(2026, 8, 28) + clock * STEP).toISOString(),
});
const holder = (state, node) => state.holders.get(node)?.name ?? null;

test("a home goes to the first win from a player without one, and stays", () => {
  const state = fold(graph, [win("ann", 10, 40), win("bo", 10, 20), win("ann", 11, 30)]);
  assert.equal(holder(state, 10), "ann");      // bo's better score doesn't take it
  assert.equal(holder(state, 11), null);       // ann already has a home
  assert.equal(state.homes.get("ann"), 10);
});

test("a field node is taken only from next to something you hold", () => {
  const state = fold(graph, [win("ann", 10, 40), win("ann", 1, 30), win("ann", 0, 30), win("ann", 1, 30)]);
  // The first win on 1 came before ann held 0, so only the second one counts.
  assert.equal(holder(state, 0), "ann");
  assert.equal(holder(state, 1), "ann");
  assert.deepEqual(state.captures.map((c) => c.nodeId), [10, 0, 1]);
});

test("taking a held node needs a strictly better score, so a tie defends", () => {
  const scores = [
    win("ann", 10, 40), win("ann", 0, 30, 2),
    win("bo", 11, 40), win("bo", 2, 30), win("bo", 1, 30),
    win("bo", 0, 30, 2),       // a dead heat: ann keeps it
  ];
  assert.equal(holder(fold(graph, scores), 0), "ann");
  assert.equal(holder(fold(graph, [...scores, win("bo", 0, 30, 1)]), 0), "bo");
});

test("bettering your own node raises the bar an attacker must clear", () => {
  const base = [win("ann", 10, 40), win("ann", 0, 30), win("ann", 1, 30),
    win("bo", 11, 40), win("bo", 2, 30)];
  const raised = fold(graph, [...base, win("ann", 1, 20), win("bo", 1, 25)]);
  assert.equal(holder(raised, 1), "ann");
  assert.equal(raised.holders.get(1).turns, 20);
  assert.equal(holder(fold(graph, [...base, win("bo", 1, 25)]), 1), "bo");
});

test("losing every field node leaves your home as the way back in", () => {
  const routed = [
    win("ann", 10, 40), win("ann", 0, 30),
    win("bo", 11, 40), win("bo", 2, 30), win("bo", 1, 30), win("bo", 0, 20),
  ];
  const state = fold(graph, routed);
  assert.equal(holder(state, 0), "bo");
  assert.equal(holder(state, 10), "ann");        // homes never fall
  assert.ok(canAttempt(graph, state, 0, "ann"));  // ...and it is next to node 0
  assert.equal(holder(fold(graph, [...routed, win("ann", 0, 10)]), 0), "ann");
});

test("standings rank field nodes held, then who got there first", () => {
  const state = fold(graph, [
    win("ann", 10, 40), win("bo", 11, 40), win("bo", 2, 30), win("ann", 0, 30),
  ]);
  assert.deepEqual(state.standings.map((p) => [p.name, p.fields]), [["bo", 1], ["ann", 1]]);
  const quiet = fold(graph, [win("cy", 10, 40)]);
  assert.deepEqual(quiet.standings.map((p) => [p.name, p.fields, p.home]), [["cy", 0, 10]]);
});

test("names fold case, the way the board folds them", () => {
  const state = fold(graph, [win("Ann", 10, 40), win("ann", 0, 30)]);
  assert.equal(holder(state, 0), "ann");
  assert.equal(state.standings.length, 1);
});

test("what you may attempt: an empty home once, or a node beside one you hold", () => {
  const state = fold(graph, [win("ann", 10, 40)]);
  assert.ok(canAttempt(graph, state, 0, "ann"));
  assert.ok(!canAttempt(graph, state, 1, "ann"));
  assert.ok(!canAttempt(graph, state, 11, "ann"));   // already has a home
  assert.ok(canAttempt(graph, state, 11, "bo"));
  assert.ok(!canAttempt(graph, state, 0, ""));
});

// Timing: a score posted `m` minutes into the week.
const T0 = Date.UTC(2026, 8, 28);
let ids = 1000;
const at = (m, name, node, turns, lost = 0) => ({
  node_id: node, score_id: ++ids, user_name: name, turns, lost,
  submitted_at: new Date(T0 + m * 60000).toISOString(),
});
const MIN = 60000;

test("the timers are an hour and half an hour", () => {
  assert.equal(COOLDOWN_MS, 60 * MIN);
  assert.equal(GRACE_MS, 30 * MIN);
});

test("a win during a cooldown is queued, then played when the hour is up", () => {
  const scores = [at(0, "ann", 10, 40), at(10, "ann", 0, 30)];
  const early = fold(graph, scores, T0 + 30 * MIN);
  assert.equal(holder(early, 0), null);
  assert.deepEqual(early.queued.map((q) => [q.nodeId, q.at]), [[0, T0 + 60 * MIN]]);
  assert.equal(early.readyAt.get("ann"), T0 + 60 * MIN);

  const later = fold(graph, scores, T0 + 61 * MIN);
  assert.equal(holder(later, 0), "ann");
  assert.equal(later.queued.length, 0);
  const move = later.captures.find((c) => c.nodeId === 0);
  assert.equal(move.at, T0 + 60 * MIN);       // played when the wait ended...
  assert.equal(move.posted, T0 + 10 * MIN);   // ...for a win posted earlier
  assert.equal(later.readyAt.get("ann"), T0 + 120 * MIN);
});

test("queued wins play one per hour, in posting order", () => {
  const scores = [at(0, "ann", 10, 40), at(5, "ann", 0, 30), at(6, "ann", 1, 30)];
  const state = fold(graph, scores);
  assert.deepEqual(state.captures.map((c) => [c.nodeId, (c.at - T0) / MIN]), [[10, 0], [0, 60], [1, 120]]);
});

test("a queued win meets the board as it is when it plays", () => {
  // bo takes node 0 first, at minute 20; ann's queued 30-turn win on it then
  // has to beat bo's 25, and doesn't.
  const scores = [
    at(0, "ann", 10, 40), at(10, "ann", 0, 30),
    at(1, "bo", 11, 40), at(2, "bo", 2, 30), at(70, "bo", 1, 30), at(140, "bo", 0, 25),
  ];
  const state = fold(graph, scores);
  assert.equal(holder(state, 0), "bo");
  // ann's played at minute 60, before bo got there; bo then beat it at 140.
  assert.deepEqual(state.captures.filter((c) => c.nodeId === 0).map((c) => c.taker), ["ann", "bo"]);
});

test("bettering your own node never waits", () => {
  const scores = [at(0, "ann", 10, 40), at(70, "ann", 0, 30), at(80, "ann", 0, 20)];
  const state = fold(graph, scores, T0 + 81 * MIN);
  assert.equal(state.holders.get(0).turns, 20);
  assert.equal(state.queued.length, 0);
});

test("a neighbour lost while you play still counts for half an hour", () => {
  // ann holds 0 and is playing 1; bo takes 0 from her at minute 200.
  const base = [
    at(0, "ann", 10, 40), at(70, "ann", 0, 30),
    at(1, "bo", 11, 40), at(70, "bo", 2, 30), at(140, "bo", 1, 30), at(200, "bo", 0, 20),
  ];
  const inTime = fold(graph, [...base, at(225, "ann", 1, 25)]);
  assert.equal(holder(inTime, 1), "ann");
  const late = fold(graph, [...base, at(235, "ann", 1, 25)]);
  assert.equal(holder(late, 1), "bo");

  const status = attemptStatus(graph, fold(graph, base), 1, "ann", T0 + 210 * MIN);
  assert.equal(status.why, "grace");
  assert.equal(status.graceUntil, T0 + 230 * MIN);
  assert.deepEqual(status.beat, { turns: 30, lost: 0, name: "bo" });
  assert.ok(!canAttempt(graph, fold(graph, base), 1, "ann", T0 + 231 * MIN));
});

test("a queued win keeps the grace it was posted with", () => {
  // ann takes 0 at minute 200, so she waits until 260. bo takes 0 from her at
  // 205. ann's win on 1, posted at 210, plays at 260: 55 minutes after the
  // loss, but it is judged from when she posted it, so it counts.
  const base = [
    at(0, "ann", 10, 40), at(200, "ann", 0, 30),
    at(0, "bo", 11, 40), at(60, "bo", 2, 30), at(120, "bo", 1, 30), at(205, "bo", 0, 20),
  ];
  const queued = fold(graph, [...base, at(210, "ann", 1, 20)]);
  assert.equal(holder(queued, 1), "ann");
  assert.equal(queued.captures.at(-1).at, T0 + 260 * MIN);
  // Posted 35 minutes after the loss, it doesn't.
  assert.equal(holder(fold(graph, [...base, at(240, "ann", 1, 20)]), 1), "bo");
});

test("what you may attempt, and when it would land", () => {
  const state = fold(graph, [at(0, "ann", 10, 40)]);
  const now = T0 + 20 * MIN;
  assert.deepEqual(attemptStatus(graph, state, 0, "ann", now),
    { can: true, why: "adjacent", graceUntil: null, readyAt: T0 + 60 * MIN, beat: null });
  assert.equal(attemptStatus(graph, state, 0, "ann", T0 + 61 * MIN).readyAt, null);
  assert.equal(attemptStatus(graph, state, 10, "ann", now).why, "own-home");
  assert.equal(attemptStatus(graph, state, 11, "ann", now).why, "has-home");
  assert.equal(attemptStatus(graph, state, 11, "bo", now).why, "home-open");
  assert.equal(attemptStatus(graph, state, 0, "bo", now).why, "no-home");
  assert.equal(attemptStatus(graph, state, 1, "ann", now).why, "not-adjacent");
  assert.equal(attemptStatus(graph, state, 0, "", now).why, "no-name");
});

test("nothing plays after the week closes", () => {
  const week = { ...graph, week_start: "2026-09-28" };
  const close = T0 + 7 * 24 * 60 * MIN;
  const lastMinute = (close - T0) / MIN - 10;
  const scores = [at(lastMinute - 20, "ann", 10, 40), at(lastMinute, "ann", 0, 30)];
  const state = fold(week, scores);
  assert.equal(holder(state, 0), null);
  assert.equal(state.queued.length, 0);
});

test("bigger maps draw bigger, and one name is one colour", () => {
  assert.ok(nodeRadius(120) > nodeRadius(40) && nodeRadius(40) > nodeRadius(12));
  assert.equal(playerHue("Ann"), playerHue("ann"));
});

test("a map's campaign badge points at its node, in its own week", () => {
  const monday = new Date(Date.UTC(2026, 8, 28));
  assert.deepEqual(campaignMark({ week_start: "2026-09-28", node_id: 15, kind: "home" }, monday), {
    current: true, label: "Campaign · Home 15", href: "campaign.html?node=15",
  });
  assert.deepEqual(campaignMark({ week_start: "2026-09-21", node_id: 3, kind: "field" }, monday), {
    current: false, label: "Past campaign · Node 3", href: "campaign.html?week=2026-09-21&node=3",
  });
});
