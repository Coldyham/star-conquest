// The generate form's pure half: the spec it builds and the link that carries
// it to the game. fixtures/gauntlet-spec.json is a link built here, which
// tests/test_gauntletgen.py decodes with the game's own `spec_from_fragment`
// and deals from, so the two sides cannot drift apart. Regenerate it after a
// deliberate change:
//   UPDATE_FIXTURES=1 node --test leaderboard/tests/gauntlet-spec.test.mjs

import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

import { OPPONENTS, SPEC_FRAGMENT, buildSpec, emptyForm, gameCount, specLink } from "../js/gauntlet-spec.mjs";

const FIXTURE = fileURLToPath(new URL("./fixtures/gauntlet-spec.json", import.meta.url));
const deflate = (bytes) => new Uint8Array(deflateSync(bytes, { level: 9 }));
const seven = () => 7;

test("the starting form builds a spec a phone can afford", () => {
  const spec = buildSpec(emptyForm(), seven);
  assert.deepEqual(spec, {
    stages: 6, seed: 7, roll: false, candidates: 3,
    rules: { players: { range: [2, 4] }, systems: { range: [13, 24] },
             mode: { in: ["random", "symmetric"] }, fog: { fix: false } },
    opponents: { in: ["rusherplus", "heuristic", "thinker", "evolved", "marshal", "actuary"] },
  });
  assert.equal(gameCount(spec), 72);
});

test("each choice becomes the dealer's rule", () => {
  const spec = buildSpec({ ...emptyForm(), mode: "symmetric", speedGain: "off", homeProduction: "2",
    fog: "either", roll: true, targets: true, slack: 3, seed: "41", name: "  Sym   eight ",
    opponents: ["marshal", "rusherplus"] }, seven);
  assert.deepEqual(spec.rules.mode, { fix: "symmetric" });
  assert.deepEqual(spec.rules.ship_speed_growth_pct, { fix: 0 });
  assert.deepEqual(spec.rules.home_production, { fix: 2 });
  assert.deepEqual(spec.rules.fog, { in: [true, false] });
  assert.deepEqual(spec.opponents, { in: ["rusherplus", "marshal"] });   // weakest first
  assert.equal(spec.seed, 41);
  assert.equal(spec.roll, true);
  assert.equal(spec.targets, true);
  assert.equal(spec.slack, 3);
  assert.equal(spec.name, "Sym eight");
});

test("a form that can never deal says why", () => {
  const bad = (over) => buildSpec({ ...emptyForm(), ...over }, seven);
  assert.match(bad({ stages: 1 }), /stages must be/);
  assert.match(bad({ playersMin: 5, playersMax: 3 }), /fewest players/);
  assert.match(bad({ playersMin: 6, playersMax: 6, systemsMin: 5, systemsMax: 8 }), /6 players need at least 9/);
  assert.match(bad({ opponents: [] }), /at least one opponent/);
  assert.match(bad({ homeProduction: "12" }), /home production/);
  assert.match(bad({ seed: "-3" }), /seed/);
  assert.match(bad({ candidates: 0 }), /candidates/);
  assert.match(bad({ name: "x".repeat(33) }), /over 32/);
});

test("the offered opponents are the ladder, weakest first", () => {
  assert.equal(OPPONENTS[0], "rusherplus");
  assert.equal(OPPONENTS.at(-1), "knower");
});

test("the link carries the spec to the game, and the fixture is what it builds today", async () => {
  const spec = buildSpec({ ...emptyForm(), mode: "symmetric", stages: 3, candidates: 1, targets: true,
    seed: "12", playersMax: 3, systemsMax: 18, opponents: ["rusherplus", "heuristic"] }, seven);
  const link = await specLink("https://example.app/game/", spec, deflate);
  assert.ok(link.startsWith(`https://example.app/game/#${SPEC_FRAGMENT}`));
  const fragment = link.split("#")[1];
  const token = fragment.slice(SPEC_FRAGMENT.length).replace(/-/g, "+").replace(/_/g, "/");
  assert.deepEqual(JSON.parse(inflateSync(Buffer.from(token, "base64")).toString("utf8")), spec);
  if (process.env.UPDATE_FIXTURES) {
    writeFileSync(FIXTURE, JSON.stringify({ spec, fragment }, null, 2) + "\n");
    return;
  }
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf8"));
  assert.deepEqual(fixture.spec, spec);
});
