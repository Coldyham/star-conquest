// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { compareScores, displayOrder, rankAmong, scoreComparator, standings, tally } from "../js/standings.mjs";

const at = (day) => `2026-09-${String(day).padStart(2, "0")}T12:00:00+00:00`;

/** entry("m1", "ann", 25, 2, 3) — one submission, on a map, by a player. */
const entry = (gameKey, playerKey, turns, lost, day = 1) => ({
  gameKey, playerKey, turns, lost, hand: turns, at: at(day),
});
const field = (gameKey, ...pairs) =>
  pairs.map(([turns, lost]) => ({ gameKey, turns, lost }));

const ann = { key: "ann", name: "Ann" };
const bo = { key: "bo", name: "Bo" };

test("fewest turns wins, fewest lost breaks the tie", () => {
  assert.ok(compareScores({ turns: 25, lost: 9 }, { turns: 26, lost: 0 }) < 0);
  assert.ok(compareScores({ turns: 25, lost: 3 }, { turns: 25, lost: 2 }) > 0);
  assert.equal(compareScores({ turns: 25, lost: 2 }, { turns: 25, lost: 2 }), 0);
});

test("a rank is one more than however many beat it, so a dead heat shares it", () => {
  const board = field("m1", [20, 1], [25, 2], [25, 2], [31, 6]);
  assert.equal(rankAmong(board, { turns: 20, lost: 1 }), 1);
  assert.equal(rankAmong(board, { turns: 25, lost: 2 }), 2);
  assert.equal(rankAmong(board, { turns: 31, lost: 6 }), 4);
});

test("only a player's best attempt on a map is placed, and the tries are counted", () => {
  const rows = standings(
    [entry("m1", "ann", 40, 9, 1), entry("m1", "ann", 25, 2, 3), entry("m1", "ann", 31, 4, 2)],
    [ann],
    field("m1", [20, 1], [25, 2], [31, 4], [40, 9]),
  );
  assert.equal(rows.length, 1);
  const [me] = rows[0].entries;
  assert.equal(me.score.turns, 25);
  assert.equal(me.tries, 3);
  assert.equal(me.rank, 2);
  assert.equal(rows[0].fieldSize, 4);
});

test("a roster member who never played that map gets a placeholder, not a row", () => {
  const rows = standings([entry("m1", "ann", 25, 2)], [ann, bo], field("m1", [25, 2]));
  assert.deepEqual(rows[0].entries.map((e) => e.name), ["Ann", "Bo"]);
  assert.equal(rows[0].entries[1].score, null);
  assert.equal(rows[0].played, 1);
  // Nobody "leads" a map only one of them has played.
  assert.deepEqual(rows[0].entries.map((e) => e.lead), [false, false]);
});

test("the leader is best within the roster, whatever the board thinks", () => {
  const rows = standings(
    [entry("m1", "ann", 40, 3), entry("m1", "bo", 31, 8)],
    [ann, bo],
    field("m1", [12, 0], [31, 8], [40, 3]),
  );
  const [a, b] = rows[0].entries;
  assert.deepEqual([a.lead, b.lead], [false, true]);
  assert.deepEqual([a.rank, b.rank], [3, 2]); // neither holds the record
});

test("a dead heat between rivals leads jointly and counts as a draw", () => {
  const rows = standings(
    [entry("m1", "ann", 25, 2), entry("m1", "bo", 25, 2)],
    [ann, bo],
    field("m1", [25, 2], [25, 2]),
  );
  assert.deepEqual(rows[0].entries.map((e) => e.lead), [true, true]);
  const sums = tally(rows, [ann, bo]);
  assert.equal(sums.contested, 1);
  assert.equal(sums.draws, 1);
  assert.deepEqual(sums.players.map((p) => p.leads), [0, 0]);
});

test("contested maps sort first, then the most recent attempt", () => {
  const rows = standings(
    [
      entry("solo-old", "ann", 30, 1, 2),
      entry("solo-new", "ann", 30, 1, 9),
      entry("both", "ann", 30, 1, 1),
      entry("both", "bo", 32, 1, 1),
    ],
    [ann, bo],
    [],
  );
  assert.deepEqual(rows.map((r) => r.gameKey), ["both", "solo-new", "solo-old"]);
});

test("the card counts maps, submissions, records held and head-to-head leads", () => {
  const rows = standings(
    [
      entry("m1", "ann", 25, 2, 1), entry("m1", "ann", 40, 9, 2), entry("m1", "bo", 31, 4, 1),
      entry("m2", "ann", 30, 5, 3), entry("m2", "bo", 22, 1, 4),
      entry("m3", "bo", 50, 7, 5),
    ],
    [ann, bo],
    [...field("m1", [25, 2], [31, 4]), ...field("m2", [22, 1], [30, 5]), ...field("m3", [50, 7])],
  );
  const sums = tally(rows, [ann, bo]);
  assert.deepEqual(sums.players[0], { key: "ann", name: "Ann", maps: 2, scores: 3, records: 1, leads: 1 });
  assert.deepEqual(sums.players[1], { key: "bo", name: "Bo", maps: 3, scores: 3, records: 2, leads: 1 });
  assert.equal(sums.contested, 2);
  assert.equal(sums.draws, 0);
});

test("a player with nothing posted still gets a card row of zeroes", () => {
  const sums = tally(standings([], [ann], []), [ann]);
  assert.deepEqual(sums.players, [{ key: "ann", name: "Ann", maps: 0, scores: 0, records: 0, leads: 0 }]);
  assert.equal(sums.contested, 0);
});

test("without the field there is no placing to show, but the comparison still works", () => {
  const rows = standings([entry("m1", "ann", 25, 2), entry("m1", "bo", 26, 2)], [ann, bo], []);
  assert.equal(rows[0].entries[0].rank, null);
  assert.equal(rows[0].fieldSize, 0);
  assert.equal(rows[0].entries[0].lead, true);
  assert.equal(tally(rows, [ann, bo]).players[0].records, 0);
});

test("scoreComparator: 'lost' ranks by fewest ships lost, turns breaks the tie", () => {
  const byLost = scoreComparator("lost");
  assert.ok(byLost({ turns: 40, lost: 1 }, { turns: 20, lost: 2 }) < 0);
  assert.ok(byLost({ turns: 25, lost: 2 }, { turns: 20, lost: 2 }) > 0);
  assert.equal(byLost({ turns: 25, lost: 2 }, { turns: 25, lost: 2 }), 0);
});

test("scoreComparator: 'turns' is compareScores, the game's own rule", () => {
  assert.equal(scoreComparator("turns"), compareScores);
  assert.equal(scoreComparator("anything-else"), compareScores);
});

test("displayOrder: a dead heat keeps its earliest submission on top, in both sorts", () => {
  const submitted = (day, turns, lost) =>
    ({ turns, lost, submitted_at: `2026-09-${String(day).padStart(2, "0")}T12:00:00+00:00` });
  // a/b tie on both figures (25, 2); c is strictly fewer turns but more lost,
  // so the two sorts disagree on who leads and only agree that a/b tie — with
  // b's earlier submission the one that must come first, in both orderings.
  const a = submitted(9, 25, 2);
  const b = submitted(1, 25, 2);
  const c = submitted(5, 20, 4);
  const scores = [a, b, c]; // deliberately not already in either display order

  assert.deepEqual(
    displayOrder(scores, "turns").map((s) => s.submitted_at),
    [c, b, a].map((s) => s.submitted_at), // fewest turns first; the tie breaks to b
  );
  assert.deepEqual(
    displayOrder(scores, "lost").map((s) => s.submitted_at),
    [b, a, c].map((s) => s.submitted_at), // fewest lost first; the tie still breaks to b
  );
});

test("displayOrder does not mutate its input", () => {
  const scores = [{ turns: 30, lost: 1, submitted_at: "2026-09-02T00:00:00Z" }, { turns: 20, lost: 1, submitted_at: "2026-09-01T00:00:00Z" }];
  const original = [...scores];
  displayOrder(scores, "turns");
  assert.deepEqual(scores, original);
});
