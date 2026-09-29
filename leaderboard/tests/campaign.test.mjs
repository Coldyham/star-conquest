// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { campaignMark, canAttempt, fold, nodeRadius, playerHue } from "../js/campaign.mjs";

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

let clock = 0;
const win = (name, node, turns, lost = 0) => ({
  node_id: node, score_id: ++clock, user_name: name, turns, lost,
  submitted_at: new Date(Date.UTC(2026, 8, 28) + clock * 60000).toISOString(),
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
