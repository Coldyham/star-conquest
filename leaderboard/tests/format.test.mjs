// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { readFileSync } from "node:fs";

import {
  botLeadBadge, botProfile, competitionRanks, difficultyBadge, difficultyLevel, embargoNote, leaderCredit,
  mapSummary, symmetricSize,
} from "../js/format.mjs";

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
  // knower goes on the board on Search, not the Predict an untuned seat gets, so
  // the row has to disclose which version answered. A named stop is already the
  // whole story; the number would only add noise.
  assert.equal(
    botProfile({ bot: "knower", aux: 2, aux_label: "Oracle: Search" }),
    "knower · oracle: search",
  );
  // A row cached while the knob was still a depth keeps reading as it did.
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

test("a bot ahead of the board's best leads, a dead heat is a tie, and a beaten bot has no badge", () => {
  // el() only needs createElement; a plain object stands in for the DOM node.
  globalThis.document ??= {
    createElement: () => ({ setAttribute() {}, append() {} }),
    createTextNode: (text) => text,
  };
  const game = (best_turns, best_lost) =>
    ({ bot_name: "marshal", bot_turns: 20, bot_lost: 3, best_turns, best_lost });
  assert.equal(botLeadBadge(game(25, 0)).textContent, "Bot leads");
  assert.equal(botLeadBadge(game(20, 4)).textContent, "Bot leads");
  const tie = botLeadBadge(game(20, 3));
  assert.equal(tie.textContent, "Bot tied");
  assert.match(tie.className, /\btied\b/);
  assert.equal(botLeadBadge(game(20, 2)), null);
  assert.equal(botLeadBadge(game(19, 9)), null);
  assert.equal(botLeadBadge({ ...game(20, 3), bot_name: null }), null);
});

const SIZES = JSON.parse(readFileSync(new URL("./fixtures/symmetric-sizes.json", import.meta.url)));

test("a symmetric map's size matches the game's for every layout, seat count and request", () => {
  for (const [layout, rows] of Object.entries(SIZES)) {
    for (const [players, sizes] of Object.entries(rows)) {
      sizes.forEach((size, i) => {
        assert.equal(symmetricSize(i + 1, Number(players), layout), size, `${layout} ${players}p ${i + 1}`);
      });
    }
  }
});

test("a map's summary names the systems it has, not the number asked for", () => {
  const core = { mode: "symmetric", players: 6, nodes: 10, seed: 603346, settings_json: { layout: "core" } };
  assert.equal(mapSummary(core), "Symmetric · 6 players · 18 systems · seed 603346");
  // a row with no layout to hand was made before layouts: the hub
  assert.equal(mapSummary({ mode: "symmetric", players: 6, nodes: 10, seed: 1 }),
    "Symmetric · 6 players · 13 systems · seed 1");
  assert.equal(mapSummary({ mode: "random", players: 6, nodes: 10, seed: 1 }),
    "Random · 6 players · 10 systems · seed 1");
  const drawn = { ...core, settings_json: { layout: "core", custom_map: { nodes: [] } } };
  assert.equal(mapSummary(drawn), "Symmetric · 6 players · 10 systems · seed 603346");
});

test("the difficulty word follows how many of the bots won, out of however many ran", () => {
  globalThis.document ??= {
    createElement: () => ({ setAttribute() {}, append() {} }),
    createTextNode: (text) => text,
  };
  const word = (wins, runs) => difficultyLevel(Math.round((1 - wins / runs) * 1000) / 1000).text;
  assert.deepEqual([8, 7, 6, 5, 4, 3, 2, 1, 0].map((wins) => word(wins, 8)),
    ["Easy", "Easy", "Easy", "Medium", "Medium", "Hard", "Hard", "Hard", "Very hard"]);
  // While a new bot is still being replayed, seven runs read the same counts.
  assert.deepEqual([7, 6, 5, 4, 3, 2, 1, 0].map((wins) => word(wins, 7)),
    ["Easy", "Easy", "Medium", "Medium", "Hard", "Hard", "Hard", "Very hard"]);
  // PostgREST may hand a numeric over as a string.
  assert.equal(difficultyLevel("1.000").text, "Very hard");
  assert.equal(difficultyLevel(null), null);
  const badge = difficultyBadge({ bot_wins: 2, bot_runs: 7, difficulty: 0.714 });
  assert.equal(badge.textContent, "Hard");
  assert.match(badge.className, /\bdifficulty\b.*\bhard\b/);
  assert.equal(difficultyBadge({ bot_wins: 0, bot_runs: 0, difficulty: null }), null);
  // A config card passes its average and has no counts of its own.
  assert.equal(difficultyBadge({ avg_difficulty: 0.5 }, 0.5).textContent, "Medium");
});
