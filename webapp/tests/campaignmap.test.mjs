// The campaign map's drawer (js/campaignmap.mjs), shared by the board's
// campaign page and the game's win screen: its pure parts.

import assert from "node:assert/strict";
import test from "node:test";

import { fillFor, frame, nodeClasses, PAD } from "../js/campaignmap.mjs";

test("a node is its holder's colour, or the unclaimed grey", () => {
  assert.equal(fillFor(200), "hsl(200 70% 58%)");
  assert.equal(fillFor(0), "hsl(0 70% 58%)");
  assert.equal(fillFor(null), "#3a3f55");
  assert.equal(fillFor(undefined), "#3a3f55");
});

test("kind, holder and marks become classes", () => {
  assert.equal(nodeClasses({ kind: "field", hue: null, marks: ["open"] }), "node field neutral open");
  assert.equal(nodeClasses({ kind: "home", hue: 12, marks: ["moved", "selected"] }), "node home moved selected");
});

test("the view box frames every node, padded", () => {
  assert.deepEqual(frame([{ x: 100, y: 50 }, { x: 300, y: 10 }]), [100 - PAD, 10 - PAD, 200 + 2 * PAD, 40 + 2 * PAD]);
});
