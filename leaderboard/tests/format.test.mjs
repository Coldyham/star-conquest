// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { competitionRanks } from "../js/format.mjs";

/** scores([31, 16], …) -> the {turns, lost} shape a score row ranks by. */
const scores = (...pairs) => pairs.map(([turns, lost]) => ({ turns, lost }));

test("a dead heat shares its place, and the next distinct score skips one", () => {
  assert.deepEqual(
    competitionRanks(scores([31, 16], [31, 16], [41, 14], [75, 31])),
    [1, 1, 3, 4],
  );
});

test("equal turns with different losses are placed, not tied", () => {
  assert.deepEqual(competitionRanks(scores([31, 16], [31, 17])), [1, 2]);
});

test("a three-way tie takes one place between the scores around it", () => {
  assert.deepEqual(
    competitionRanks(scores([20, 5], [31, 16], [31, 16], [31, 16], [41, 14])),
    [1, 2, 2, 2, 5],
  );
});

test("hand is disclosure, not part of the score", () => {
  const tied = [
    { turns: 31, lost: 16, hand: 31 },
    { turns: 31, lost: 16, hand: 6 },
  ];
  assert.deepEqual(competitionRanks(tied), [1, 1]);
});

test("an empty board ranks nothing", () => {
  assert.deepEqual(competitionRanks([]), []);
});

test("competitionRanks holds under a lost-first sort too, since a tie is symmetric", () => {
  // Sorted by lost, then turns — the board's other ranking — rather than the
  // turns-first order every other test in this file uses.
  assert.deepEqual(
    competitionRanks(scores([41, 14], [31, 16], [31, 16], [75, 31])),
    [1, 2, 2, 4],
  );
});
