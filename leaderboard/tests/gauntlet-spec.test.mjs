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

import {
  KNOB_TABS, KNOBS, OPPONENTS, SPEC_FRAGMENT, buildSpec, emptyForm, gameCount, isSet, knobText, specLink,
} from "../js/gauntlet-spec.mjs";

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
  const spec = buildSpec({ ...emptyForm(), mode: "symmetric",
    knobs: { ship_speed_growth_pct: [0.5, 0.5], home_production: [2, 2] },
    fog: "either", roll: true, targets: "fastest", slack: 3, unwonLast: false, seed: "41", name: "  Sym   eight ",
    opponents: ["marshal", "rusherplus"] }, seven);
  assert.deepEqual(spec.rules.mode, { fix: "symmetric" });
  assert.deepEqual(spec.rules.ship_speed_growth_pct, { fix: 0.5 });
  assert.deepEqual(spec.rules.home_production, { fix: 2 });
  assert.deepEqual(spec.rules.fog, { in: [true, false] });
  assert.deepEqual(spec.opponents, { in: ["rusherplus", "marshal"] });   // weakest first
  assert.equal(spec.seed, 41);
  assert.equal(spec.roll, true);
  assert.equal(spec.targets, "fastest");
  assert.equal(spec.unwon_last, false);
  assert.equal(buildSpec({ ...emptyForm(), targets: true }, seven).targets, "slowest");   // a form kept from before
  assert.equal(buildSpec(emptyForm(), seven).unwon_last, undefined);
  assert.equal(spec.slack, 3);
  assert.equal(spec.name, "Sym eight");
});

test("a slider's pins are a range, a fixed value, or nothing on the default", () => {
  const rules = buildSpec({ ...emptyForm(), knobs: {
    home_production: [3, 3],               // the default: no rule
    garrison_k: [20, 8],                   // pins either way round
    defender_advantage: [1.2, 1.2],
    node_jitter: [0.1 + 0.2, 0.5],         // float noise snaps to the step
  }, switches: { in_lane_battles: "either", neutral_produces: "off" } }, seven).rules;
  assert.equal(rules.home_production, undefined);
  assert.deepEqual(rules.garrison_k, { range: [8, 20] });
  assert.deepEqual(rules.defender_advantage, { fix: 1.2 });
  assert.deepEqual(rules.node_jitter, { range: [0.3, 0.5] });
  assert.deepEqual(rules.in_lane_battles, { in: [true, false] });
  assert.equal(rules.neutral_produces, undefined);
  assert.equal(knobText(KNOBS.home_production, [3, 3]), "3 (default)");
  assert.equal(knobText(KNOBS.home_production, [3, 3], true), "rolled");
  assert.equal(knobText(KNOBS.combat_jitter, [0.1, 0.1], true), "0.1 (default)");   // combat never rolls
  assert.equal(knobText(KNOBS.garrison_k, [8, 20]), "8 to 20");
  assert.equal(knobText(KNOBS.defender_advantage, [1.2, 1.2]), "1.2");
  assert.equal(knobText(KNOBS.fog_sight, [8, 8]), "All (default)");
  assert.equal(knobText(KNOBS.fog_scout, [3, 8]), "3 to All");
  assert.ok(isSet({ knobs: { garrison_k: [8, 20] } }, "garrison_k"));
  assert.ok(!isSet({ knobs: { home_production: [3, 3] } }, "home_production"));
});

test("the fog sliders count only when fog can be on", () => {
  const knobs = { fog_sight: [1, 2] };
  assert.equal(buildSpec({ ...emptyForm(), knobs }, seven).rules.fog_sight, undefined);
  assert.deepEqual(buildSpec({ ...emptyForm(), fog: "on", knobs }, seven).rules.fog_sight, { range: [1, 2] });
});

test("a form that can never deal says why", () => {
  const bad = (over) => buildSpec({ ...emptyForm(), ...over }, seven);
  assert.match(bad({ stages: 1 }), /stages must be/);
  assert.match(bad({ playersMin: 5, playersMax: 3 }), /fewest players/);
  assert.match(bad({ playersMin: 6, playersMax: 6, systemsMin: 5, systemsMax: 8 }), /6 players need at least 9/);
  assert.match(bad({ opponents: [] }), /at least one opponent/);
  assert.match(bad({ knobs: { home_production: [1, 12] } }), /Home production must be 1 to 8/);
  assert.match(bad({ seed: "-3" }), /seed/);
  assert.match(bad({ candidates: 0 }), /candidates/);
  assert.match(bad({ name: "x".repeat(33) }), /over 32/);
});

test("the offered opponents are the ladder, weakest first", () => {
  assert.equal(OPPONENTS[0], "rusherplus");
  assert.equal(OPPONENTS.at(-1), "knower");
});

test("the link carries the spec to the game, and the fixture is what it builds today", async () => {
  const spec = buildSpec({ ...emptyForm(), mode: "symmetric", stages: 3, candidates: 1, targets: "fastest",
    seed: "12", playersMax: 3, systemsMax: 18, opponents: ["rusherplus", "heuristic"],
    knobs: { garrison_k: [6, 10], defender_advantage: [1.1, 1.1] } }, seven);
  const link = await specLink("https://example.app/game/", spec, deflate);
  assert.ok(link.startsWith(`https://example.app/game/#${SPEC_FRAGMENT}`));
  const fragment = link.split("#")[1];
  const token = fragment.slice(SPEC_FRAGMENT.length).replace(/-/g, "+").replace(/_/g, "/");
  assert.deepEqual(JSON.parse(inflateSync(Buffer.from(token, "base64")).toString("utf8")), spec);
  if (process.env.UPDATE_FIXTURES) {
    writeFileSync(FIXTURE, JSON.stringify({ spec, fragment, knobs: KNOB_TABS }, null, 2) + "\n");
    return;
  }
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf8"));
  assert.deepEqual(fixture.spec, spec);
  assert.deepEqual(fixture.knobs, JSON.parse(JSON.stringify(KNOB_TABS)));
});
