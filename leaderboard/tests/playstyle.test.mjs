// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { BASELINE } from "../js/playstyle-baseline.mjs";
import { COVER, ROWS, SHAPE, THRESHOLDS, median, pool, summary, waveMedian } from "../js/playstyle.mjs";

const BINS = 41;
const blank = () => Array(BINS).fill(0);
const perThreshold = (f) => THRESHOLDS.map(f);

/** One game's reading, with waves at the given (band, bin) pairs. */
function reading({ waves = [], relief = {}, frontier = [0, 0], incomeAt = null, turns = 50 } = {}) {
  const bands = [blank(), blank(), blank()];
  for (const [band, bin] of waves) bands[band][bin] += 1;
  const reach = (at) => perThreshold((x) => (at === null || x > 0.67 ? [] : [at]));
  const never = (at) => perThreshold((x) => (at === null || x > 0.67 ? 1 : 0));
  return {
    shape: SHAPE,
    games: 1,
    turns: [turns],
    hand_turns: turns,
    waves: bands,
    relief: Object.fromEntries(COVER.map((how) => [how, relief[how] ?? [0, 0]])),
    frontier,
    reach: { income: reach(incomeAt), ships: reach(null), board: reach(null) },
    never: { income: never(incomeAt), ships: never(null), board: never(null) },
  };
}

test("readings pool by adding, and a foreign shape is left out", () => {
  const a = reading({ waves: [[0, 14]], frontier: [10, 1], incomeAt: 0.4 });
  const b = reading({ waves: [[2, 20]], frontier: [30, 2], incomeAt: 0.6, turns: 70 });
  const both = pool([a, { ...b }, { shape: SHAPE + 1 }, null, undefined]);
  assert.equal(both.games, 2);
  assert.deepEqual(both.turns, [50, 70]);
  assert.deepEqual(both.frontier, [40, 3]);
  assert.equal(both.waves[0][14] + both.waves[2][20], 2);
  assert.deepEqual(both.reach.income[0], [0.4, 0.6]);
  assert.equal(a.games, 1, "pool must not change what it was handed");
  assert.equal(pool([]), null);
});

test("the median wave is the bin's middle, and the last bin is open", () => {
  const bins = blank();
  bins[13] = 2;
  bins[15] = 1;
  assert.deepEqual(waveMedian(bins), { ratio: 1.35, open: false, count: 3 });
  const top = blank();
  top[BINS - 1] = 5;
  assert.deepEqual(waveMedian(top), { ratio: 4, open: true, count: 5 });
  assert.equal(waveMedian(blank()), null);
  assert.equal(median([3, 1, 2, 10]), 2.5);
  assert.equal(median([]), null);
});

test("a summary reads relief, border losses and the threshold a game never reached", () => {
  const s = summary(
    pool([
      reading({ relief: { inbound: [2, 0], uncovered: [2, 2] }, frontier: [100, 5], incomeAt: 0.5 }),
      reading({ incomeAt: null }),
    ]),
  );
  assert.equal(s.emptiesPerGame, 2);
  assert.equal(s.covered, 0.5);
  assert.equal(s.lostAfterEmpty, 0.5);
  assert.equal(s.frontierLoss, 5);
  assert.deepEqual(s.twoThirdsIncome, { at: 0.5, never: 1 });
  assert.equal(s.wave, null);
});

test("every row shows the baseline as text", () => {
  assert.equal(BASELINE.shape, SHAPE);
  const s = summary(BASELINE);
  for (const row of ROWS) {
    const text = row.show(s);
    assert.equal(typeof text, "string");
    assert.ok(text.length, row.label);
    assert.ok(!text.includes("NaN"), row.label);
  }
});
