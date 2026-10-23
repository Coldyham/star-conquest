// A generated gauntlet's spec, as the board's gauntlet page builds it — pure,
// no DOM, no network.
//
// The page cannot generate a gauntlet: picking stages of rising difficulty means
// playing bots on them, and only the game has an engine. So the form builds a
// spec (`starconquest/dealer.py`'s `Spec`, docs/design/gauntlet.md, "Generated
// gauntlets") and a link that hands it to the game, `/game/#gauntlet-spec=<token>`,
// which deals, plays and picks, then opens the result on its Tutorial list.
//
// The spec's rules are the dealer's: `fix`, `range` or `in` on a setup field.
// Each Advanced and Combat slider is a min and a max pin (`form.knobs`), each
// switch Off, On or Either (`form.switches`). What the form leaves alone (both
// pins on the game's default, a switch Off) is left out, so the game keeps that
// field at its default (`roll` false) or rolls it (`roll` true). The run's own options
// (`candidates`, `targets`, `slack`, `name`) ride beside it, read by
// `gauntletgen.job_from_link`.

import { SETUP_DEFAULTS } from "./config.mjs";
import { LADDER_ORDER } from "./standings.mjs";
import { encodeToken } from "./token-encode.mjs";

export const SPEC_FRAGMENT = "gauntlet-spec=";
// The bots that measure a stage (`gauntletgen.PROBES`): each candidate is played
// once by each, so the count sets how long the game takes.
export const PROBES = 4;
export const LIMITS = Object.freeze({
  stages: [2, 16],
  players: [2, 6],
  systems: [5, 40],
  candidates: [1, 8],
  slack: [0, 99],
  seed: 999999,
  name: 32,
});
/** Opponents a form offers, weakest first (the ladder is strongest first). */
export const OPPONENTS = Object.freeze([...LADDER_ORDER].reverse());

const knob = (attr, label, lo, hi, step) => ({ attr, label, lo, hi, step });
// The sliders and switches a form may rule, under the game menu's tabs and
// headings: settings.py's ADV_* tables (attr, label, lo, hi, step). The fixture
// fixtures/gauntlet-spec.json carries a copy, which tests/test_gauntletgen.py
// checks against those tables. The Visibility sliders apply only when the
// form's fog is On or Either.
export const KNOB_TABS = Object.freeze([
  { tab: "Advanced", groups: [
    { heading: "Map", knobs: [
      knob("node_jitter", "Node jitter", 0, 1, 0.05),
      knob("relax_min_sep_frac", "Relax min-sep", 0, 1.2, 0.05),
      knob("lloyd_passes", "Relax passes", 0, 5, 1),
      knob("extra_edge_fraction", "Extra edges", 0, 1, 0.05),
      knob("max_edge_length_frac", "Max edge len", 0.1, 1, 0.05),
    ], switches: [] },
    { heading: "Travel", knobs: [
      knob("ship_ly_per_turn", "Ship speed (ly/turn)", 1, 30, 0.5),
      knob("ship_speed_growth_pct", "Speed gain %/turn", 0, 2, 0.05),
    ], switches: [] },
    { heading: "Visibility", knobs: [
      knob("fog_sight", "Sight range", 0, 8, 1),
      knob("fog_scout", "Scout range", 1, 8, 1),
    ], switches: [] },
    { heading: "Economy", knobs: [
      knob("home_start_ships", "Home ships", 1, 50, 1),
      knob("home_production", "Home production", 1, 8, 1),
      knob("garrison_base", "Garrison base", 0, 20, 1),
      knob("garrison_k", "Garrison scale", 0, 40, 1),
      knob("garrison_jitter", "Garrison jitter", 0, 10, 1),
    ], switches: [{ attr: "neutral_produces", label: "Neutral produces" }] },
  ] },
  { tab: "Combat", groups: [
    { heading: "Combat", knobs: [
      knob("combat_jitter", "Combat jitter", 0, 0.5, 0.02),
      knob("defender_advantage", "Defender advantage", 0.75, 1.5, 0.05),
    ], switches: [{ attr: "in_lane_battles", label: "In-lane battles" }] },
  ] },
]);
const GROUPS = KNOB_TABS.flatMap((t) => t.groups);
export const KNOBS = Object.freeze(Object.fromEntries(GROUPS.flatMap((g) => g.knobs).map((k) => [k.attr, k])));
export const SWITCHES = Object.freeze(GROUPS.flatMap((g) => g.switches).map((s) => s.attr));
// The sliders `roll` rolls when left on the default (`dealer.ROLLED`): every
// Advanced one, never Combat.
export const ROLLED = Object.freeze(KNOB_TABS[0].groups.flatMap((g) => g.knobs).map((k) => k.attr));
export const SWITCH_VALUES = ["off", "on", "either"];
const FOG_KNOBS = ["fog_sight", "fog_scout"];

/** A pin's value on its knob's step, or null when it is off the slider. */
export function snapKnob(k, value) {
  const n = Number(value);
  if (value === "" || value === null || !Number.isFinite(n) || n < k.lo - 1e-9 || n > k.hi + 1e-9) return null;
  return Math.min(k.hi, Math.max(k.lo, Number((Math.round(n / k.step) * k.step).toFixed(4))));
}

/** A knob's pins as a rule, or null when they sit on the game's default. */
export function knobRule(k, pair) {
  let [a, b] = pair.map((v) => snapKnob(k, v));
  if (a > b) [a, b] = [b, a];
  if (a === b) return a === SETUP_DEFAULTS[k.attr] ? null : { fix: a };
  return { range: [a, b] };
}

/** What a knob's pins say, for its readout: "3 (default)", "5" or "2 to 5";
 * "rolled" for one left on the default when the form rolls. */
export function knobText(k, pair, roll = false) {
  const rule = knobRule(k, pair);
  // A fog range at its top is unlimited, as the menu's slider reads it.
  const show = (v) => (FOG_KNOBS.includes(k.attr) && v >= k.hi ? "All" : String(v));
  if (rule === null) return roll && ROLLED.includes(k.attr) ? "rolled" : `${show(SETUP_DEFAULTS[k.attr])} (default)`;
  return rule.fix !== undefined ? show(rule.fix) : `${show(rule.range[0])} to ${show(rule.range[1])}`;
}

/** Whether a knob or switch says anything beyond the game's default. */
export function isSet(form, attr) {
  if (attr in KNOBS) {
    const pair = (form.knobs || {})[attr];
    return Array.isArray(pair) && pair.every((v) => snapKnob(KNOBS[attr], v) !== null)
      && knobRule(KNOBS[attr], pair) !== null;
  }
  return ["on", "either"].includes((form.switches || {})[attr]);
}

/** The form as it starts: a modest run a phone can generate. */
export function emptyForm() {
  return {
    name: "",
    stages: 6,
    mode: "any",              // any | random | symmetric
    playersMin: 2,
    playersMax: 4,
    systemsMin: 13,
    systemsMax: 24,
    opponents: OPPONENTS.filter((bot) => bot !== "knower"),
    fog: "off",               // off | on | either
    knobs: {},                // attr -> [min, max]; missing is the default
    switches: {},             // attr -> off | on | either
    roll: false,
    targets: false,
    slack: 0,
    candidates: 3,
    seed: "",                 // "" rolls one when the link is made
  };
}

function wholeIn(value, [lo, hi]) {
  const n = Number(value);
  return Number.isInteger(n) && n >= lo && n <= hi ? n : null;
}

/**
 * The spec a form describes, or a string saying what is wrong with it.
 *
 * @param rollSeed  () => an integer seed, used when the form gives none, so the
 *                  link always names its seed and opening it twice makes the
 *                  same gauntlet
 */
export function buildSpec(form, rollSeed = () => Math.floor(Math.random() * LIMITS.seed)) {
  const stages = wholeIn(form.stages, LIMITS.stages);
  if (stages === null) return `stages must be ${LIMITS.stages[0]} to ${LIMITS.stages[1]}`;
  const playersMin = wholeIn(form.playersMin, LIMITS.players);
  const playersMax = wholeIn(form.playersMax, LIMITS.players);
  if (playersMin === null || playersMax === null) return `players must be ${LIMITS.players[0]} to ${LIMITS.players[1]}`;
  if (playersMin > playersMax) return "the fewest players is more than the most";
  const systemsMin = wholeIn(form.systemsMin, LIMITS.systems);
  const systemsMax = wholeIn(form.systemsMax, LIMITS.systems);
  if (systemsMin === null || systemsMax === null) return `systems must be ${LIMITS.systems[0]} to ${LIMITS.systems[1]}`;
  if (systemsMin > systemsMax) return "the fewest systems is more than the most";
  if (systemsMax < playersMin + 3) return `${playersMin} players need at least ${playersMin + 3} systems`;
  const opponents = OPPONENTS.filter((bot) => (form.opponents || []).includes(bot));
  if (!opponents.length) return "pick at least one opponent";
  const candidates = wholeIn(form.candidates, LIMITS.candidates);
  if (candidates === null) return `candidates must be ${LIMITS.candidates[0]} to ${LIMITS.candidates[1]}`;

  const rules = {
    players: { range: [playersMin, playersMax] },
    systems: { range: [systemsMin, systemsMax] },
  };
  rules.mode = form.mode === "random" || form.mode === "symmetric"
    ? { fix: form.mode } : { in: ["random", "symmetric"] };
  rules.fog = form.fog === "on" ? { fix: true } : form.fog === "either" ? { in: [true, false] } : { fix: false };
  for (const k of Object.values(KNOBS)) {
    const pair = (form.knobs || {})[k.attr];
    if (!Array.isArray(pair) || pair.length !== 2) continue;
    if (form.fog !== "on" && form.fog !== "either" && FOG_KNOBS.includes(k.attr)) continue;
    if (pair.some((v) => snapKnob(k, v) === null)) return `${k.label} must be ${k.lo} to ${k.hi}`;
    const rule = knobRule(k, pair);
    if (rule !== null) rules[k.attr] = rule;
  }
  for (const attr of SWITCHES) {
    const value = (form.switches || {})[attr];
    if (value === "on") rules[attr] = { fix: true };
    else if (value === "either") rules[attr] = { in: [true, false] };
  }

  let seed;
  if (String(form.seed ?? "").trim() === "") seed = rollSeed();
  else {
    seed = wholeIn(form.seed, [0, LIMITS.seed]);
    if (seed === null) return `the seed must be a whole number up to ${LIMITS.seed}`;
  }
  const spec = { stages, seed, rules, opponents: { in: opponents }, roll: form.roll === true, candidates };
  if (form.targets === true) {
    spec.targets = true;
    const slack = wholeIn(form.slack ?? 0, LIMITS.slack);
    if (slack === null) return `slack must be ${LIMITS.slack[0]} to ${LIMITS.slack[1]} turns`;
    if (slack) spec.slack = slack;
  }
  const name = String(form.name ?? "").split(/\s+/).filter(Boolean).join(" ");
  if ([...name].length > LIMITS.name) return `the name is over ${LIMITS.name} characters`;
  if (name) spec.name = name;
  return spec;
}

/** How many probe games the game will play for a spec: what a phone waits on. */
export function gameCount(spec) {
  return spec.stages * spec.candidates * PROBES;
}

/** The game link that generates ``spec``: `<game>#gauntlet-spec=<token>`. */
export async function specLink(gameUrl, spec, deflate) {
  return `${gameUrl}#${SPEC_FRAGMENT}${await encodeToken(spec, deflate)}`;
}
