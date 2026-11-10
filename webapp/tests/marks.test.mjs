// The film's marks against `viewstate.read_marks` and `Ui.archive_marks`.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FADE_MS, FLASH_MS, markFade, Marks, readMarks } from "../js/marks.mjs";

const all = () => true;
const landed = (node, owner, was, steps = [{}]) =>
  ({ k: "Landed", node_id: node, owner_id: owner, was_owner: was, steps, losses: [[owner, 2], [was, 5]] });

test("a fight is marked where it was seen; a landing with no fight is not", () => {
  const [fights] = readMarks([landed(3, 1, 2), landed(4, 1, 1, [])], all, () => 0);
  assert.equal(fights.length, 1);
  assert.equal(fights[0].node, 3);
  assert.equal(fights[0].color, 1);            // taken: the taker's colour
  const [held] = readMarks([landed(3, 2, 2)], all, () => 0);
  assert.equal(held[0].color, null);           // held: plain text
  const [hidden] = readMarks([landed(3, 1, 2)], () => false, () => 0);
  assert.equal(hidden.length, 0);
});

test("a hull is credited to whoever held the yard, and a rival's is not reported", () => {
  const ev = { k: "Produced", hulls: [[3, 1], [4, 2]] };
  const [, hulls] = readMarks([ev], (id) => id === 3, () => 7);
  assert.deepEqual(hulls.map((h) => [h.node, h.hulls, h.owner]), [[3, 1, 7]]);
});

test("a second fight at a system supersedes the first, and marks dissolve on their own clock", () => {
  const marks = new Marks();
  const board = { sys: new Map([[3, { owner: 1 }]]) };
  marks.archive(board, [landed(3, 1, 2)], all);
  marks.age(100);
  marks.archive(board, [landed(3, 2, 1)], all);
  assert.equal(marks.fights.length, 1);
  assert.equal(marks.fights[0].age, 0);
  assert.equal(markFade(FLASH_MS), 0);
  assert.equal(markFade(FLASH_MS + FADE_MS), 1);
  marks.age(FLASH_MS + FADE_MS);
  assert.equal(marks.any, false);
});
