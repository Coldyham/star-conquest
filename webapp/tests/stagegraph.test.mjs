// The stage graph's pure half (`stagegraph.mjs`): the shape of each kind of
// lane between two stage boxes, its arrowheads and the classes a stage and a
// lane are drawn with. The DOM half is checked by the parity screens.

import assert from "node:assert/strict";
import { test } from "node:test";

import { arrowPoints, curveMid, laneClasses, laneCurve, nodeClasses } from "../js/stagegraph.mjs";

const box = (x, y) => ({ x, y, w: 100, h: 40 });

test("a lane to the next column runs edge to edge", () => {
  const c = laneCurve(box(0, 0), box(150, 60), 1);
  assert.deepEqual(c.start, [100, 20]);
  assert.deepEqual(c.end, [150, 80]);
  assert.deepEqual(c.c1, [125, 20]);
  assert.deepEqual(c.c2, [125, 80]);
});

test("a shortcut arcs over the stages it skips; a lane back dips below", () => {
  const over = laneCurve(box(0, 50), box(300, 50), 2);
  assert.ok(over.c1[1] < 50 && over.c2[1] < 50 && over.start[1] === 50 && over.end[1] === 50);
  const under = laneCurve(box(300, 50), box(0, 50), -2);
  assert.ok(under.c1[1] > 90 && under.c2[1] > 90 && under.start[1] === 90);
  const within = laneCurve(box(0, 0), box(0, 60), 0);
  assert.ok(within.c1[1] > 100);
  assert.deepEqual(curveMid({ start: [0, 0], c1: [0, 8], c2: [8, 8], end: [8, 0] }), [4, 6]);
});

test("an arrowhead points along the lane", () => {
  const [tip, left, right] = arrowPoints([100, 0], [0, 0]);
  assert.deepEqual(tip, [100, 0]);
  assert.ok(left[0] < 100 && right[0] < 100 && left[1] === -right[1]);
});

test("marks and kinds become classes", () => {
  assert.equal(nodeClasses({ open: false, marks: ["start", "picked"] }), "sg-node sg-locked sg-start sg-picked");
  assert.equal(nodeClasses({ open: true }), "sg-node");
  assert.equal(laneClasses({ gated: true, hidden: true }), "sg-lane sg-gated sg-hidden");
  assert.equal(laneClasses({ warn: true }), "sg-lane sg-warn");
  assert.equal(laneClasses({ shut: true }), "sg-lane sg-shut");
});
