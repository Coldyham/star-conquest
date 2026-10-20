// node --test leaderboard/tests/

import assert from "node:assert/strict";
import test from "node:test";

import { diff, families, familyForest, familyName, paceLabel, paceOf, withFirstSeen } from "../js/families.mjs";

// A config_summary row: the setup's columns plus what the family view reads.
function config(key, settings = {}, extra = {}) {
  return {
    config_key: key, mode: "random", players: 2, nodes: 18, bots: ["heuristic"], config_name: null,
    game_count: 1, last_activity: "2026-10-01T00:00:00Z", settings_json: settings, ...extra,
  };
}

test("pace is the median lane estimate, banded", () => {
  // 77 / sqrt(18) / 6 = 3.02 -> 4 turns.
  assert.deepEqual(paceOf(config("a")), { turns: 4, band: "Standard" });
  assert.deepEqual(paceOf(config("a", { ship_ly_per_turn: 19.5 })), { turns: 1, band: "Fast" });
  assert.deepEqual(paceOf(config("a", { ship_ly_per_turn: 2 })), { turns: 10, band: "Long" });
  // Past a standard board the box grows, so spacing holds at 40 systems'.
  assert.equal(paceOf(config("a", {}, { nodes: 120 })).turns, paceOf(config("a", {}, { nodes: 40 })).turns);
});

test("pace counts the systems a symmetric map really builds", () => {
  // Six seats around a hub asked for 10 build 13 systems (format.mjs's systemCount).
  const sym = config("a", {}, { mode: "symmetric", players: 6, nodes: 10 });
  assert.equal(paceOf(sym).turns, Math.ceil(77 / Math.sqrt(13) / 6));
});

test("a hand-drawn map has no pace", () => {
  assert.equal(paceOf(config("a", { custom_map: { nodes: [], lanes: [] } })), null);
  assert.equal(paceLabel(config("a", { custom_map: { nodes: [], lanes: [] } })), null);
});

test("the pace label is band and turns, and says when ships speed up", () => {
  assert.equal(paceLabel(config("a")).text, "Standard · ~4t");
  assert.ok(!paceLabel(config("a")).title.includes("speed up"));
  assert.ok(paceLabel(config("a", { ship_speed_growth_pct: 0.3 })).title.includes("speed up"));
});

test("values compare by value, a missing key reading as its default", () => {
  assert.deepEqual(diff(config("a", { combat_jitter: 0.1 }), config("b", {})).edits, []);
  assert.deepEqual(diff(config("a", { defender_advantage: 1.0 }), config("b", { defender_advantage: 1 })).edits, []);
  assert.deepEqual(diff(config("a"), config("b", { ship_ly_per_turn: 8 })).edits,
    [{ kind: "tempo", text: "Ship speed 6→8" }]);
});

test("a change of opponents alone is one light edit", () => {
  const step = diff(config("a"), config("b", {}, { bots: ["actuary", "marshal"] }));
  assert.equal(step.cost, 1);
  assert.deepEqual(step.edits, [{ kind: "bots", text: "vs +actuary +marshal −heuristic" }]);
});

test("systems and speed moved together keeping the pace count once, as a rescale", () => {
  // 77/sqrt(5)/6 = 5.7 and 77/sqrt(24)/3 = 5.2: both 6 turns.
  const step = diff(config("a", {}, { nodes: 5 }), config("b", { ship_ly_per_turn: 3 }, { nodes: 24 }));
  assert.equal(step.cost, 1);
  assert.equal(step.edits[0].text, "Rescaled");
  // The same two edits that change the pace are two.
  assert.equal(diff(config("a", {}, { nodes: 5 }), config("b", { ship_ly_per_turn: 12 }, { nodes: 24 })).cost, 2);
});

test("a mode switch counts once even when it changes the system count", () => {
  const step = diff(config("a", {}, { players: 4, nodes: 21 }), config("b", {}, { mode: "symmetric", players: 4, nodes: 21 }));
  assert.equal(step.cost, 2);
  assert.deepEqual(step.edits.map((e) => e.kind), ["shape"]);
});

test("fog switches as one field", () => {
  assert.deepEqual(diff(config("a"), config("b", { fog_sight: 1, fog_scout: 3 })).edits,
    [{ kind: "fog", text: "Fog on" }]);
  assert.equal(diff(config("a"), config("b", { fog_sight: 1, fog_scout: 3 })).cost, 2);
});

test("each config's parent is its nearest older config, within the edit limit", () => {
  const forest = familyForest([
    config("c", { ship_ly_per_turn: 8, combat_jitter: 0.06, home_production: 2 }, { first_seen: "2026-10-03" }),
    config("a", {}, { first_seen: "2026-10-01" }),
    config("b", { ship_ly_per_turn: 8, combat_jitter: 0.06 }, { first_seen: "2026-10-02" }),
    config("d", {}, { players: 5, first_seen: "2026-10-04", bots: ["marshal"] }),
  ]);
  const parent = Object.fromEntries(forest.map((n) => [n.config.config_key, n.parent]));
  assert.deepEqual(parent, { a: null, b: "a", c: "b", d: null });
});

test("a newer config never moves an older one", () => {
  const base = [
    config("a", {}, { first_seen: "2026-10-01" }),
    config("b", { home_production: 2, combat_jitter: 0.08 }, { first_seen: "2026-10-02" }),
  ];
  const before = familyForest(base).map((n) => n.parent);
  const after = familyForest([...base, config("z", { home_production: 2 }, { first_seen: "2026-10-03" })]);
  assert.deepEqual(after.slice(0, 2).map((n) => n.parent), before);
});

test("ties on first sighting break by config key, and the closest older config wins", () => {
  const forest = familyForest([
    config("b", { ship_ly_per_turn: 8 }, { first_seen: "2026-10-01" }),
    config("a", { ship_ly_per_turn: 7 }, { first_seen: "2026-10-01" }),
    config("c", { ship_ly_per_turn: 8, combat_jitter: 0.06 }, { first_seen: "2026-10-02" }),
  ]);
  assert.deepEqual(forest.map((n) => [n.config.config_key, n.parent]), [["a", null], ["b", "a"], ["c", "b"]]);
});

test("without first_seen the order falls back to last activity, or to the games' first sightings", () => {
  const configs = [
    config("a", {}, { last_activity: "2026-10-05" }),
    config("b", { ship_ly_per_turn: 8 }, { last_activity: "2026-10-01" }),
  ];
  assert.deepEqual(familyForest(configs).map((n) => n.config.config_key), ["b", "a"]);
  const games = [
    { config_key: "a", first_seen_at: "2026-09-01" }, { config_key: "a", first_seen_at: "2026-09-03" },
    { config_key: "b", first_seen_at: "2026-09-02" },
  ];
  const seen = withFirstSeen(configs, games);
  assert.deepEqual(seen.map((c) => c.first_seen), ["2026-09-01", "2026-09-02"]);
  assert.deepEqual(familyForest(seen).map((n) => [n.config.config_key, n.parent]), [["a", null], ["b", "a"]]);
});

test("families list members in tree order, most recently active family first", () => {
  const out = families(familyForest([
    config("a", {}, { first_seen: "1", last_activity: "2026-10-01" }),
    config("b", { ship_ly_per_turn: 8 }, { first_seen: "2", last_activity: "2026-10-02" }),
    config("x", { fog_sight: 1, fog_scout: 3 }, { players: 6, first_seen: "3", last_activity: "2026-10-09", config_name: "Six" }),
    config("c", {}, { bots: ["marshal"], first_seen: "4", last_activity: "2026-10-03" }),
  ]));
  assert.deepEqual(out.map((f) => f.name), ["Six", "Random · 2 players · 18 systems"]);
  assert.deepEqual(out[1].members.map((m) => [m.config.config_key, m.depth]), [["a", 0], ["b", 1], ["c", 1]]);
  assert.equal(out[1].lastActivity, "2026-10-03");
});

test("a family is named after its first named member", () => {
  const members = [{ config: config("a") }, { config: config("b", {}, { config_name: " Mini marshal " }) }];
  assert.equal(familyName(members), "Mini marshal");
});
