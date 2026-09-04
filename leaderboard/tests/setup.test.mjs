// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { configLabel, configTitle, tweaks } from "../js/setup.mjs";

test("no settings, or an empty object, is Default", () => {
  assert.equal(configLabel({ mode: "random", players: 3, nodes: 18, seed: 42 }), "Default");
  assert.equal(configLabel({}), "Default");
  assert.equal(configLabel(null), "Default");
});

test("one or two tweaks are spelled out in full", () => {
  assert.equal(configLabel({ defender_advantage: 1.5 }), "Def adv 1.5");
  assert.equal(
    configLabel({ defender_advantage: 1.5, combat_jitter: 0.25 }),
    "Combat jitter 0.25 · Def adv 1.5",
  );
});

test("more than two tweaks collapse to a count", () => {
  assert.equal(
    configLabel({ defender_advantage: 1.5, combat_jitter: 0.25, fog_sight: 3 }),
    "3 tweaks",
  );
});

test("a boolean knob reads as on/off, not true/false", () => {
  assert.equal(configLabel({ neutral_produces: true }), "Neutral prod on");
});

test("tuned per-seat AI params show as one entry, not one per field", () => {
  assert.equal(
    configLabel({ ai: [{ aux: 2.0 }] }),
    "Tuned AI",
  );
  // Counts toward the "many tweaks" collapse like any other entry.
  assert.equal(
    configLabel({ ai: [{ aux: 2.0 }], defender_advantage: 1.5, combat_jitter: 0.25 }),
    "3 tweaks",
  );
});

test("ai_strategy is read separately as bots, not as a tweak", () => {
  // Present alone, ai_strategy contributes nothing to the label — it's read
  // via game_summary.bots and shown as chips, not folded into the badge text.
  assert.equal(configLabel({ ai_strategy: ["heuristic", "marshal"] }), "Default");
});

test("an unrecognised knob still renders, under its own key", () => {
  assert.equal(configLabel({ some_future_knob: 7 }), "some_future_knob 7");
});

test("tweaks() is sorted by key, so identical setups read identically", () => {
  const t = tweaks({ fog_sight: 3, defender_advantage: 1.5 });
  assert.deepEqual(t.map((x) => x.key), ["defender_advantage", "fog_sight"]);
});

test("configTitle prefers a submitted name over the derived label", () => {
  assert.equal(configTitle({ config_name: "Blitz Ladder", settings_json: { defender_advantage: 1.5 } }), "Blitz Ladder");
  assert.equal(configTitle({ config_name: "", settings_json: { defender_advantage: 1.5 } }), "Def adv 1.5");
  assert.equal(configTitle({ settings_json: {} }), "Default");
});
