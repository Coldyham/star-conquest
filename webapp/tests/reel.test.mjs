// The history-path rule on the page's side: turn i's film, played onto board
// i-1, lands exactly on board i. Fixtures from tools/dump_film_fixtures.py.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { fromBoard, label, progressAt, Reel, toBoard, travel } from "../js/reel.mjs";

const fixtures = JSON.parse(readFileSync(new URL("./fixtures/films.json", import.meta.url), "utf8"));

for (const game of fixtures.games) {
  test(`every film of ${game.name} lands on the board after it`, () => {
    for (const [i, turn] of game.turns.entries()) {
      const reel = new Reel(fromBoard(turn.before), turn.film);
      reel.run();
      assert.ok(reel.done);
      assert.deepEqual(toBoard(reel.board), turn.after, `${game.name} turn ${i}`);
    }
  });

  test(`${game.name}: a film run in slices lands where one run does`, () => {
    for (const turn of game.turns) {
      const reel = new Reel(fromBoard(turn.before), turn.film);
      for (let ms = 0; ms <= turn.film.total; ms += 37) reel.runTo(ms);
      reel.run();
      assert.deepEqual(toBoard(reel.board), turn.after);
    }
  });
}

test("a board passes through unchanged", () => {
  const snap = fixtures.games[0].turns[3].before;
  assert.deepEqual(toBoard(fromBoard(snap)), snap);
});

test("a film naming a fleet the board lacks fails loudly", () => {
  const turn = fixtures.games.flatMap((g) => g.turns).find((t) => t.film.cues.some(([, e]) => e.k === "Landed" && e.fleets.length));
  const board = fromBoard(turn.before);
  board.fleets = [];
  const reel = new Reel(board, turn.film);
  assert.throws(() => reel.run(), /names fleet/);
});

test("progress and travel follow the Python formulas", () => {
  const fleet = { total: 4, remaining: 2 };
  assert.equal(progressAt(fleet, 0), 0.25);
  assert.equal(progressAt(fleet, 1), 0.5);
  assert.equal(progressAt({ total: 0, remaining: 0 }, 0.5), 1.0);
  const film = { beats: [["lead", "", 0, 0], ["move", "Moving", 100, 200]], cues: [], total: 300 };
  assert.equal(travel(film, 50), 1.0);
  assert.equal(travel(film, 200), 0.5);
  assert.equal(label(film, 150), "Moving");
  assert.equal(label(film, 0), "");
});
