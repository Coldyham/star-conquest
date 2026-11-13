// The board's mirror of the game's gauntlet graph: fixtures/gauntlet-layout.json
// is read here and by tests/test_gauntlet.py, so the diagram the page draws is
// the one the game will show, cell for cell.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  BONUS, GATED, SHORTCUT, cellNames, depths, goals, grantsLife, graphOf, hiddenStages, laneLabel, layout, reachable, ways,
} from "../js/gauntlet-graph.mjs";

const read = (name) => JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url)), "utf8"));
const { cases } = read("gauntlet-layout.json");

for (const c of cases) {
  test(`layout: ${c.name}`, () => {
    const graph = graphOf(c.body);
    const cells = Object.fromEntries([...layout(graph)].map(([sid, cell]) => [String(sid), cell]));
    assert.deepEqual(cells, c.layout);
    assert.deepEqual([...goals(graph)].sort((a, b) => a - b), c.goals);
    assert.deepEqual([...hiddenStages(graph)].sort((a, b) => a - b), c.hidden_stages);
    assert.deepEqual(graph.ids.filter((sid) => grantsLife(graph, sid)), c.bonus_lives);
    const reached = reachable(graph);
    assert.deepEqual(graph.ids.filter((sid) => !reached.has(sid)), c.unreachable);
    assert.deepEqual(ways(graph).map((w) => [w.from, w.to, laneLabel(graph, w.from, w.to)]), c.labels);
    const named = (shown) => Object.fromEntries([...cellNames(graph, shown)].map(([sid, n]) => [String(sid), n]));
    assert.deepEqual(named(null), c.names);
    const hidden = hiddenStages(graph);
    assert.deepEqual(named(graph.ids.filter((sid) => !hidden.has(sid))), c.names_known);
  });
}

test("the lanes fixture reads as the game reads it", () => {
  const fx = read("gauntlet-lanes.json");
  for (const body of [fx.body, { ...fx.body, lanes: fx.stored_lanes }]) {
    const graph = graphOf(body);
    assert.deepEqual(Object.fromEntries([...depths(graph)].map(([k, v]) => [String(k), v])), fx.game.depths);
    assert.deepEqual([...goals(graph)], fx.game.goals);
    assert.deepEqual([...hiddenStages(graph)], fx.game.hidden_stages);
    for (const [a, b, label] of fx.game.labels) assert.equal(laneLabel(graph, a, b), label);
  }
  assert.deepEqual([BONUS, SHORTCUT, GATED], ["Bonus", "Shortcut", "Gated"]);
});

test("a body is read tolerantly, as from_dict reads it", () => {
  const graph = graphOf({
    stages: [{ id: 3 }, { id: 3 }, { id: "x" }, null, { id: 5 }, { id: 7 }],
    lanes: [[3, 5], [5, 3], [5, 9], [5, 5], "no", [5, 7, { hidden: true }], [3, 7, { turns: 0 }]],
    start: [42],
  });
  assert.deepEqual(graph.ids, [3, 5, 7]);
  assert.deepEqual(graph.lanes, [[3, 5], [5, 7], [3, 7]]);
  assert.equal(graph.gates.size, 0);
  assert.equal(graph.hidden.size, 0);
  assert.deepEqual(graph.start, [3]);
  assert.deepEqual(graphOf({ stages: [{ id: 0 }, { id: 1 }] }).lanes, [[0, 1]]);
  assert.equal(layout(graphOf({})).size, 0);
});
