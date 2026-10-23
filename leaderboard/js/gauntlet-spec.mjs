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
// What the form leaves alone is left out, so the game keeps that field at its
// default (`roll` false) or rolls it (`roll` true). The run's own options
// (`candidates`, `targets`, `slack`, `name`) ride beside it, read by
// `gauntletgen.job_from_link`.

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
  homeProduction: [1, 8],
  candidates: [1, 8],
  slack: [0, 99],
  seed: 999999,
  name: 32,
});
/** Opponents a form offers, weakest first (the ladder is strongest first). */
export const OPPONENTS = Object.freeze([...LADDER_ORDER].reverse());

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
    speedGain: "default",     // default | off
    homeProduction: "",       // "" keeps the default, else a number
    fog: "off",               // off | on | either
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
  if (form.speedGain === "off") rules.ship_speed_growth_pct = { fix: 0 };
  if (String(form.homeProduction ?? "").trim() !== "") {
    const prod = wholeIn(form.homeProduction, LIMITS.homeProduction);
    if (prod === null) return `home production must be ${LIMITS.homeProduction[0]} to ${LIMITS.homeProduction[1]}`;
    rules.home_production = { fix: prod };
  }
  rules.fog = form.fog === "on" ? { fix: true } : form.fog === "either" ? { in: [true, false] } : { fix: false };

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
