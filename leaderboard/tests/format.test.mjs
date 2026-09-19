// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { botProfile, competitionRanks, embargoNote, leaderCredit } from "../js/format.mjs";

const inDays = (n) => new Date(Date.now() + n * 86400000).toISOString();

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

test("no embargo, or a lifted one, has nothing to say", () => {
  assert.equal(embargoNote(null), null);
  assert.equal(embargoNote(""), null);
  assert.equal(embargoNote(inDays(-1)), null);
  assert.equal(embargoNote(new Date().toISOString()), null);
});

test("a live embargo counts the days left, rounding up", () => {
  assert.equal(embargoNote(inDays(3)), "Replays hidden — revealing in 3 days");
  // Just under a day still reads as one, not zero.
  assert.equal(embargoNote(inDays(0.1)), "Replays hidden — revealing in 1 day");
  assert.equal(embargoNote(inDays(1)), "Replays hidden — revealing in 1 day");
});

test("leaderCredit names the current best score's holder", () => {
  assert.equal(leaderCredit({ best_user_name: "Ann", best_holders: 1 }), "Ann");
  // A missing user_name falls back to "anonymous", not a blank string —
  // credit() elsewhere makes the same fallback for a hand-written link's by_name.
  assert.equal(leaderCredit({ best_user_name: "", best_holders: 1 }), "anonymous");
});

test("leaderCredit credits every holder of a shared record, singular and plural", () => {
  assert.equal(leaderCredit({ best_user_name: "Ann", best_holders: 2 }), "Ann & 1 other");
  assert.equal(leaderCredit({ best_user_name: "Ann", best_holders: 3 }), "Ann & 2 others");
  // best_holders is absent on an older game_summary view; treat that as "just
  // the one leading name" rather than crashing or reading it as zero others.
  assert.equal(leaderCredit({ best_user_name: "Ann" }), "Ann");
});

test("a bot replayed at a tuned profile says so; a default one just gives its name", () => {
  // knower goes on the board at search depth 12, not the depth 1 an untuned seat
  // gets, so the row has to disclose which version answered.
  assert.equal(
    botProfile({ bot: "knower", aux: 12, aux_label: "Search depth" }),
    "knower · search depth 12",
  );
  assert.equal(botProfile({ bot: "thinker", aux: 1, aux_label: "" }), "thinker");
  // A strategy that ignores aux declares no AUX_LABEL, so there is nothing to name.
  assert.equal(botProfile({ bot: "marshal", aux: 4, aux_label: "" }), "marshal");
  // Written before aux was recorded: that board ran everything at the 1.0 default.
  assert.equal(botProfile({ bot: "claudebot" }), "claudebot");
  // A fractional knob is not a search depth; don't print it as one.
  assert.equal(botProfile({ bot: "x", aux: 0.75, aux_label: "Greed" }), "x · greed 0.75");
});
