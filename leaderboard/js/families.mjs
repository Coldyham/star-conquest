// Pace and setup families: the third level of the main list, above "By game"
// and "By config" (docs/design/setup-families.md).
//
// No DOM and no fetch, so tests/families.test.mjs can drive it directly — the
// same split as setup.mjs and listing.mjs. Every comparison is by value with a
// missing key read as its default, never as text: settings_json is pruned to
// non-defaults, and a Python-written `1.0` must equal a browser-written `1`.

import { profileLabel, profiles } from "./bots.mjs";
import { FOG_ON_SCOUT, FOG_ON_SIGHT, PACE_LY, SETUP_DEFAULTS, SHIP_LY_PER_TURN, STANDARD_MAX_NODES } from "./config.mjs";
import { setupSummary, systemCount } from "./format.mjs";
import { KNOB_LABELS } from "./setup.mjs";

// Median lane in turns at or below which each band applies.
const BANDS = [[2, "Fast"], [4, "Standard"], [Infinity, "Long"]];

/**
 * The estimated median lane of a setup's maps, in turns at the starting speed,
 * as `{turns, band}` — or null for a hand-drawn map, whose lanes no formula
 * knows. Systems and speed together set how long a lane takes, so this is the
 * one number a "same lanes, more systems" rescale keeps.
 */
export function paceOf(row) {
  const settings = row.settings_json || {};
  if (settings.custom_map) return null;
  const systems = Number(systemCount(row));
  const speed = Number(settings.ship_ly_per_turn ?? SHIP_LY_PER_TURN);
  if (!(systems > 0) || !(speed > 0)) return null;
  const turns = Math.max(1, Math.ceil(PACE_LY / Math.sqrt(Math.min(systems, STANDARD_MAX_NODES)) / speed));
  return { turns, band: BANDS.find(([top]) => turns <= top)[1] };
}

/** `{text, title}` for a card's pace chip ("Standard · ~3t"), or null. */
export function paceLabel(row) {
  const pace = paceOf(row);
  if (!pace) return null;
  const growth = Number((row.settings_json || {}).ship_speed_growth_pct ?? 0) > 0;
  const title = `Median lane about ${pace.turns} turn${pace.turns === 1 ? "" : "s"}, ` +
    "estimated from systems and ship speed" + (growth ? "; ships speed up as the game goes on" : "");
  return { text: `${pace.band} · ~${pace.turns}t`, title };
}

// --------------------------------------------------------------------------
// The family tree.

// Fields compared knob by knob, with what kind of change each is (the edit
// chip's colour). Fog, systems, players, mode, layout, the hand map, opponents
// and tuned AI are compared separately below.
const KNOB_KINDS = {
  node_jitter: "gen", relax_min_sep_frac: "gen", lloyd_passes: "gen",
  extra_edge_fraction: "gen", max_edge_length_frac: "gen",
  ship_ly_per_turn: "tempo", ship_speed_growth_pct: "tempo",
  home_start_ships: "rules", home_production: "rules", garrison_base: "rules", garrison_k: "rules",
  garrison_jitter: "rules", neutral_produces: "rules",
  combat_jitter: "rules", defender_advantage: "rules", in_lane_battles: "rules",
};
const LABELS = { ...KNOB_LABELS, in_lane_battles: "Lane battles" };

// How far apart two configs are per kind of difference. A family edge is at
// most MAX_EDIT: one or two slider moves, a fog switch, a mode switch, a
// new opponent — never a different player count plus anything else.
const WEIGHT = { players: 3, custom_map: 3, mode: 2, fog: 2 };
export const MAX_EDIT = 3;

function valueOf(settings, key) {
  return key in settings ? settings[key] : SETUP_DEFAULTS[key];
}

function same(a, b) {
  if (typeof a === "number" || typeof b === "number") return Number(a) === Number(b);
  return a === b;
}

function shown(value) {
  if (typeof value === "boolean") return value ? "on" : "off";
  return typeof value === "number" ? String(Number(value)) : String(value);
}

function fogShown(sight, scout) {
  if (Number(sight) === SETUP_DEFAULTS.fog_sight && Number(scout) === SETUP_DEFAULTS.fog_scout) return "off";
  return `${sight}/${scout}`;
}

function opponents(row) {
  return profiles(row.bots).map(profileLabel);
}

/**
 * What changed going from config `a` to config `b`, as edit chips
 * (`{kind, text}`) plus a cost: the sum of WEIGHT (1 for anything not listed),
 * less one when systems and speed moved together and kept the pace — a
 * rescale is one decision, not two.
 */
export function diff(a, b) {
  const sa = a.settings_json || {};
  const sb = b.settings_json || {};
  const edits = [];
  let cost = 0;
  const add = (kind, text, weight = 1) => { edits.push({ kind, text }); cost += weight; };

  if (Number(a.players) !== Number(b.players)) add("shape", `Players ${a.players}→${b.players}`, WEIGHT.players);
  const mapA = sa.custom_map ? JSON.stringify(sa.custom_map) : "";
  const mapB = sb.custom_map ? JSON.stringify(sb.custom_map) : "";
  if (mapA !== mapB) add("shape", mapB ? (mapA ? "Hand map redrawn" : "Hand map") : "Generated map", WEIGHT.custom_map);
  if (a.mode !== b.mode) add("shape", `Mode ${a.mode}→${b.mode}`, WEIGHT.mode);
  else if (a.mode === "symmetric" && valueOf(sa, "layout") !== valueOf(sb, "layout")) {
    add("shape", `Layout ${valueOf(sa, "layout")}→${valueOf(sb, "layout")}`);
  }
  // The Systems stepper is what a player moved: a symmetric map builds whole
  // sectors around it, so a mode switch alone can change the count it shows.
  const sysA = systemCount(a);
  const sysB = systemCount(b);
  const resized = !same(a.nodes, b.nodes);
  if (resized) add("size", `Systems ${sysA}→${sysB}`);
  for (const [key, kind] of Object.entries(KNOB_KINDS)) {
    const va = valueOf(sa, key);
    const vb = valueOf(sb, key);
    if (!same(va, vb)) add(kind, `${LABELS[key] || key} ${shown(va)}→${shown(vb)}`);
  }
  const fogA = fogShown(valueOf(sa, "fog_sight"), valueOf(sa, "fog_scout"));
  const fogB = fogShown(valueOf(sb, "fog_sight"), valueOf(sb, "fog_scout"));
  if (fogA !== fogB) add("fog", fogB === "off" ? "Fog off" : fogA === "off" && fogB === `${FOG_ON_SIGHT}/${FOG_ON_SCOUT}` ? "Fog on" : `Fog ${fogA}→${fogB}`, WEIGHT.fog);

  const botsA = opponents(a);
  const botsB = opponents(b);
  const gained = botsB.filter((bot) => !botsA.includes(bot));
  const lost = botsA.filter((bot) => !botsB.includes(bot));
  if (gained.length || lost.length) {
    add("bots", `vs ${[...gained.map((bot) => `+${bot}`), ...lost.map((bot) => `−${bot}`)].join(" ")}`);
  }
  if (JSON.stringify(sa.ai || null) !== JSON.stringify(sb.ai || null)) add("ai", "Tuned AI");

  const speedMoved = !same(valueOf(sa, "ship_ly_per_turn"), valueOf(sb, "ship_ly_per_turn"));
  const paceA = paceOf(a);
  const paceB = paceOf(b);
  if (resized && speedMoved && paceA && paceB && paceA.turns === paceB.turns) {
    edits.unshift({ kind: "rescale", text: "Rescaled" });
    cost -= 1;
  }
  return { edits, cost };
}

/**
 * `configs` with `first_seen` filled in from their games' `first_seen_at`, for
 * a board whose config_summary predates the column. Rows that already carry it
 * are left alone.
 */
export function withFirstSeen(configs, games) {
  const first = new Map();
  for (const game of games) {
    const seen = game.first_seen_at || "";
    if (seen && (!first.has(game.config_key) || seen < first.get(game.config_key))) first.set(game.config_key, seen);
  }
  return configs.map((c) => (c.first_seen || !first.has(c.config_key) ? c : { ...c, first_seen: first.get(c.config_key) }));
}

/** When a config was first played: `first_seen`, else its latest activity. */
function seenAt(config) {
  return config.first_seen || config.last_activity || "";
}

/**
 * Every config as a node `{config, parent, edits}`, oldest first, where
 * `parent` is the config_key of the nearest older config within MAX_EDIT (the
 * first such on a tie), or null for a root. A parent is only ever older, so a
 * config played later can never move an earlier one to another family.
 */
export function familyForest(configs) {
  const ordered = [...configs].sort((a, b) =>
    seenAt(a).localeCompare(seenAt(b)) || a.config_key.localeCompare(b.config_key));
  return ordered.map((config, i) => {
    let best = null;
    for (const older of ordered.slice(0, i)) {
      const step = diff(older, config);
      if (step.cost <= MAX_EDIT && (!best || step.cost < best.cost)) best = { ...step, parent: older.config_key };
    }
    return { config, parent: best ? best.parent : null, edits: best ? best.edits : [] };
  });
}

/**
 * The forest as families: `{root, members, lastActivity, name}`, members in
 * tree order (each followed by its children, oldest first) with a `depth`.
 * Families come most recently active first.
 */
export function families(forest) {
  const children = new Map(forest.map((node) => [node.config.config_key, []]));
  for (const node of forest) if (node.parent) children.get(node.parent).push(node);
  const out = forest.filter((node) => !node.parent).map((root) => {
    const members = [];
    const walk = (node, depth) => {
      members.push({ ...node, depth });
      for (const child of children.get(node.config.config_key)) walk(child, depth + 1);
    };
    walk(root, 0);
    const lastActivity = members.map((m) => m.config.last_activity || "").sort().pop();
    return { root, members, lastActivity, name: familyName(members) };
  });
  return out.sort((a, b) => b.lastActivity.localeCompare(a.lastActivity) ||
    a.root.config.config_key.localeCompare(b.root.config.config_key));
}

/** A family is called after its first named member, else its root's setup. */
export function familyName(members) {
  const named = members.find((m) => (m.config.config_name || "").trim());
  return named ? named.config.config_name.trim() : setupSummary(members[0].config);
}
