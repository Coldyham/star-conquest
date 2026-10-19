// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { profileLabel, profiles, seatProfile, stopName } from "../js/bots.mjs";
import { diff } from "../js/families.mjs";
import { botChips } from "../js/format.mjs";
import { roster, seatWho } from "../js/pbp.mjs";

test("a seat reads as its family at its stop, as sc_bot_profile reads it", () => {
  // The same cases the SQL was run against beside each bot's own reading.
  const cases = [
    ["knower", undefined, "knower:1"], ["knower", 2, "knower:2"], ["knower", 2.7, "knower:2"],
    ["knower", 3, "knower:3"], ["knower", 3.5, "knower:2"], ["knower", 12, "knower:2"],
    ["knower", -1, "knower:0"], ["knower", "junk", "knower:1"],
    ["actuary", 12, "actuary:2"], ["actuary", 3, "actuary:3"], ["thinker", 12, "thinker:1"],
    ["evolved", 0, "evolved:0"], ["council", 1, "knower:3"], ["council", 0, "knower:2"],
    ["council", 12, "knower:3"], ["council", "junk", "knower:3"], ["claudebot", 7, "thinker:0"],
    ["marshal", 4, "marshal"], ["random", 12, "random"], ["heuristic", null, "heuristic"],
  ];
  for (const [name, aux, want] of cases) assert.equal(seatProfile(name, aux), want, `${name} ${aux}`);
});

test("a bots column is read as one profile per opponent, labelled by its stop", () => {
  assert.deepEqual(profiles(["heuristic", "knower", "knower:1", "knower:3", "marshal"]),
    ["heuristic", "knower:1", "knower:3", "marshal"]);
  assert.deepEqual(profiles(["council"]), ["council"], "an older row's bare name stands");
  assert.equal(profileLabel("knower:3"), "knower · council");
  assert.equal(profileLabel("knower:1"), "knower", "the default stop needs no name");
  assert.equal(profileLabel("thinker:0"), "thinker · claudebot");
  assert.equal(profileLabel("marshal"), "marshal");
  assert.equal(stopName("actuary:3"), "Hunt");
  assert.equal(stopName("knower:1"), null);
});

test("a bot off its default stop is a two-part chip, each half its own filter", () => {
  globalThis.document ??= {
    createElement: () => ({ setAttribute() {}, append() {} }),
    createTextNode: (text) => text,
  };
  const made = [];
  const real = globalThis.document.createElement;
  globalThis.document.createElement = (tag) => {
    const node = { tag, attrs: {}, children: [], setAttribute(k, v) { this.attrs[k] = v; },
      append(child) { this.children.push(child); } };
    made.push(node);
    return node;
  };
  try {
    const chips = botChips({ bots: ["knower", "knower:3", "marshal"] });
    assert.equal(chips.length, 2);
    const [pair, plain] = chips;
    assert.match(pair.className, /\bpair\b/);
    const [family, , stop] = pair.children;
    assert.equal(family.attrs.href, "index.html?bot=knower");
    assert.equal(stop.attrs.href, "index.html?bot=knower%3A3");
    assert.equal(stop.textContent, "council");
    assert.equal(plain.attrs.href, "index.html?bot=marshal");
    assert.equal(botChips({ bots: ["knower", "knower:1"] })[0].textContent, "knower");
    assert.deepEqual(botChips({ bots: ["heuristic"] }), []);
  } finally {
    globalThis.document.createElement = real;
  }
});

test("a play-by-post bot seat is named by its family and stop", () => {
  const match = {
    match_id: "00112233445566ff", seats: [1], turn: 1, finished: false, waiting: [], lapsed: {},
    unclaimed: [], names: { 1: "Alice" },
    settings_json: { players: 4, ai_strategy: ["heuristic", "council", "knower", "knower"],
                     ai: [{ aux: 1 }, { aux: 1 }, { aux: 12 }, { aux: 3 }] },
  };
  assert.deepEqual(roster(match).slice(1).map(seatWho),
    ["knower · council (bot)", "knower · search (bot)", "knower · council (bot)"]);
});

test("a setup diff names an opponent by its profile", () => {
  const cfg = (key, bots) => ({ config_key: key, settings_json: {}, bots, nodes: 18, players: 2, mode: "random" });
  const step = diff(cfg("a", ["knower", "knower:2"]), cfg("b", ["knower", "knower:3"]));
  assert.deepEqual(step.edits, [{ kind: "bots", text: "vs +knower · council −knower · search" }]);
});
