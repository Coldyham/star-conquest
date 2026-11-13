// A gauntlet as the board builds and stores it — pure, no DOM, no network.
//
// A gauntlet is the file `starconquest/gauntlet.py` reads (`Gauntlet.from_dict`;
// format in docs/design/gauntlet.md, "The file format"): a name and a list of
// stages, each a title, briefing paragraphs, a hint and a setup. The board's
// gauntlet page (gauntlet-page.mjs) builds one from pasted game links, and
// `netlify/functions/gauntlet.mjs` checks and stores it. Both go through
// `checkBody` here, so the page refuses exactly what the store would.
//
// The limits are the game's own (gauntlet.py's TITLE_MAX and friends). The game
// clamps rather than refuses, being a tolerant reader; this refuses instead, so
// an author sees the problem before saving rather than a briefing silently cut
// short on someone else's screen. Text is measured the way the game measures it:
// whitespace collapsed, then counted in code points.
//
// The stored id is `<slug>-<12 hex>`: a slug of the name plus a hash of the
// body, so saving the same gauntlet twice lands on the same row and an edited
// one is a new row. 19 + 1 + 12 = 32, gauntlet.py's NAME_MAX, so the id
// survives the game's own clamp of a gauntlet's `id` intact.

import { cellNames, cellOf, deadEnd, graphOf, layout } from "./gauntlet-graph.mjs";
import { compareScores } from "./standings.mjs";

export const LIMITS = Object.freeze({
  name: 32,
  title: 32,
  hint: 90,
  briefingLines: 6,
  briefingLine: 240,
  stages: 64,
  byName: 32,
  bodyBytes: 65536,
  slug: 19,
  bot: 24,
  lives: 9,
});

/** `schema.sql`'s check on `gauntlets.gauntlet_id`. */
export const GAUNTLET_ID = /^[a-z0-9-]{1,19}-[0-9a-f]{12}$/;

/** Whitespace collapsed, as `gauntlet._text` reads text. */
export function tidy(value) {
  return typeof value === "string" ? value.split(/\s+/).filter(Boolean).join(" ") : "";
}

/** Length in code points, as Python's `len` counts. */
export function textLength(text) {
  return [...text].length;
}

/** The readable half of an id: lowercase words of the name, hyphen-joined. */
export function slugOf(name) {
  const words = tidy(name).toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .split(/[^a-z0-9]+/).filter(Boolean);
  let slug = "";
  for (const word of words) {
    const next = slug ? `${slug}-${word}` : word;
    if (next.length > LIMITS.slug) {
      if (!slug) slug = word.slice(0, LIMITS.slug);
      break;
    }
    slug = next;
  }
  return slug || "gauntlet";
}

/** A pasted setup as a stage keeps it: no score to beat, never a demo. */
export function stageSetup(settings) {
  const { challenge, autoplay, ...setup } = settings || {};
  return setup;
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isInt(value) {
  return typeof value === "number" && Number.isInteger(value);
}

/** Why a setup cannot be a stage, or "" when it can. */
export function setupProblem(settings) {
  if (!isObject(settings)) return "has no setup";
  if (!isInt(settings.players) || settings.players < 1) return "has no player count";
  if (!isInt(settings.nodes) || settings.nodes < 1) return "has no system count";
  // A stage must be the same map every time, or it can never be cleared twice
  // (docs/design/gauntlet.md): a link with `seed: null` rolls a fresh one.
  if (!isInt(settings.seed)) return "rolls a new map each time: set a seed in the game's menu first";
  return "";
}

// -- stage targets ------------------------------------------------------------
// A score a stage must be won within before the stages beyond it open
// (docs/design/gauntlet.md, "Stage targets"; the game's `gauntlet.Target`):
// `{turns, lost?, bot?, beat?}`. A target is met; one marked `beat` (a bot's,
// as the page wrote "Beat the best bot" before targets were typed in) must be
// beaten, and the page keeps it so until its numbers are edited.
// Fixed into the file, never looked up live.

const TARGET_KEYS = ["turns", "lost", "bot", "beat"];
const TARGET_KEY_SET = new Set(TARGET_KEYS);

/** A stage's target as stored, or a string saying what is wrong with it. */
export function checkTarget(raw) {
  if (!isObject(raw) || Object.keys(raw).some((k) => !TARGET_KEY_SET.has(k))) {
    return "has a target that is not turns, lost, bot and beat";
  }
  if (!isInt(raw.turns) || raw.turns < 1) return "has a target with no turns";
  if (raw.lost !== undefined && (!isInt(raw.lost) || raw.lost < 0)) return "has a target with a bad ships-lost count";
  if (raw.bot !== undefined && typeof raw.bot !== "string") return "has a target whose bot is not a name";
  const bot = tidy(raw.bot);
  if (textLength(bot) > LIMITS.bot) return `has a target whose bot is over ${LIMITS.bot} characters`;
  if (raw.beat !== undefined && typeof raw.beat !== "boolean") return "has a target whose beat is not true or false";
  const out = { turns: raw.turns };
  if (raw.lost !== undefined) out.lost = raw.lost;
  if (bot) out.bot = bot;
  if (raw.beat) out.beat = true;
  return out;
}

/** A pasted challenge link's score as a target the author has shown can be met. */
export function linkTarget(challenge) {
  return challenge && isInt(challenge.turns) && challenge.turns > 0
    ? { turns: challenge.turns, lost: challenge.lost } : null;
}

/**
 * The bots' wins on a setup, from its `bot_scores` rows, as targets to fill in:
 * `{fastest, slowest, wins}`, each target `{turns, lost, bot}` with `bot` only
 * for naming the button. A bot's own best win stands for it, so `slowest` is
 * the slowest of those, and is null unless two or more bots won. `wins` counts
 * the bots that did; with none, both are null.
 */
export function botWins(rows) {
  const best = new Map();
  for (const row of rows || []) {
    if (!row?.won || !isInt(row.turns)) continue;
    const name = tidy(row.bot);
    if (!best.has(name) || compareScores(row, best.get(name)) < 0) best.set(name, row);
  }
  const won = [...best.values()].sort(compareScores);
  const as = (row) => ({ turns: row.turns, ...(isInt(row.lost) ? { lost: row.lost } : {}),
                         bot: tidy(row.bot).slice(0, LIMITS.bot) });
  return {
    fastest: won.length ? as(won[0]) : null,
    slowest: won.length > 1 ? as(won.at(-1)) : null,
    wins: won.length,
  };
}

/**
 * Does a won game's score `{turns, lost}` meet `target`, as the game's
 * `Target.met` decides: turns first, ships lost breaking a tie when the target
 * names them, and strictly better when it says `beat`.
 */
export function meetsTarget(score, target) {
  if (!score || !target || !isInt(score.turns)) return false;
  const named = isInt(target.lost);
  const mine = named ? [score.turns, isInt(score.lost) ? score.lost : Infinity] : [score.turns];
  const want = named ? [target.turns, target.lost] : [target.turns];
  const order = mine[0] - want[0] || (named ? mine[1] - want[1] : 0);
  return target.beat ? order < 0 : order <= 0;
}

/**
 * The best posted score on a setup as a target to fill in, from its
 * `game_summary` rows (one per game key it is filed under): `{turns, lost, by}`,
 * `by` the holder's name for the button, or null when no posted score counts.
 * `game_summary`'s best is the counted record, shown even while a map is
 * embargoed, as the map page shows it.
 */
export function postedBest(rows) {
  const best = (rows || [])
    .filter((row) => isInt(row?.best_turns))
    .map((row) => ({ turns: row.best_turns, lost: isInt(row.best_lost) ? row.best_lost : 0,
                     by: tidy(row.best_user_name || row.best_by_name) }))
    .sort(compareScores)[0];
  return best || null;
}

/** "14 turns", "14 turns / 30 lost": as the game's `Target.score_text`. */
export function targetScore(target) {
  return `${target.turns} turns${isInt(target.lost) ? ` / ${target.lost} lost` : ""}`;
}

/** The whole condition, as the game's `Target.text` words it. */
export function targetText(target) {
  if (target.beat) return `Beat ${target.bot || "the target"}: ${targetScore(target)}`;
  return isInt(target.lost) ? `Win within ${targetScore(target)}` : `Win in ${target.turns} turns or fewer`;
}

/**
 * The gauntlet the page's form describes: `{name, stages: [{title, briefing,
 * hint, settings, target?}], lanes?, start?}`. Stages are numbered from 0 in
 * list order, and the draft's lanes and starts, which join stages by `key`,
 * are written by those numbers. `lanes` and `start` are written only when they
 * differ from what the game assumes without them (each stage joined to the next
 * in list order, the first open), so a linear gauntlet builds exactly the body
 * it built before the page drew lanes, and keeps its id. A draft with no
 * `lanes` at all (one kept before lanes) is that linear gauntlet.
 */
export function buildBody(draft, { at = true } = {}) {
  const list = draft?.stages || [];
  const stages = list.map((stage, i) => ({
    id: i,
    title: tidy(stage.title),
    briefing: (stage.briefing || []).map(tidy).filter(Boolean),
    hint: tidy(stage.hint),
    settings: stageSetup(stage.settings),
    ...(stage.target ? { target: stage.target } : {}),
  }));
  const body = { name: tidy(draft?.name), stages };
  if (!Array.isArray(draft?.lanes)) return body;
  const id = new Map(list.map((stage, i) => [stage.key, i]));
  const joined = new Set();
  const lanes = draft.lanes
    .filter((lane) => id.has(lane.from) && id.has(lane.to) && lane.from !== lane.to)
    .map((lane) => {
      const how = laneHow(lane);
      const pair = [id.get(lane.from), id.get(lane.to)];
      return Object.keys(how).length ? [...pair, how] : pair;
    })
    .sort((x, y) => x[0] - y[0] || x[1] - y[1])
    .filter(([a, b]) => {
      if (joined.has(`${a},${b}`) || joined.has(`${b},${a}`)) return false;
      joined.add(`${a},${b}`);
      return true;
    });
  const opened = Array.isArray(draft.start) ? draft.start : [list[0]?.key];
  const start = list.map((stage, i) => (opened.includes(stage.key) ? i : -1)).filter((i) => i >= 0);
  const linear = lanes.length === Math.max(0, stages.length - 1)
    && lanes.every((pair, i) => pair.length === 2 && pair[0] === i && pair[1] === i + 1);
  if (!linear) body.lanes = lanes;
  if (start.length !== 1 || start[0] !== 0) body.start = start;
  writeBonus(body, list);
  const lives = Number(draft.lives);
  if (Number.isInteger(lives) && lives > 1) body.lives = Math.min(lives, LIMITS.lives);
  if (at) writeAt(body, draft);
  return body;
}

/** Each stage's place on the graph onto `body`, on every stage, only when the
 * draft's places differ from what `layout` computes from the lanes: so a
 * gauntlet the author never rearranged keeps the body (and id) it had. */
function writeAt(body, draft) {
  const cells = placed(draft);
  if (!cells) return;
  const computed = layout(graphOf(body));
  if (cells.every((cell, i) => String(cell) === String(computed.get(i)))) return;
  cells.forEach((cell, i) => { body.stages[i].at = cell; });
}

/** Each stage's `bonus_life` onto `body`, written only where it differs from
 * the graph's default (`gauntlet.to_dict`), so ticking a box back to what the
 * graph says moves no id. */
function writeBonus(body, list) {
  const graph = graphOf(body);
  for (const [i, stage] of list.entries()) {
    if (typeof stage.bonus_life === "boolean" && stage.bonus_life !== deadEnd(graph, i)) {
      body.stages[i].bonus_life = stage.bonus_life;
    }
  }
}

/** A draft lane's third element: its target, hidden (only with one), two-way. */
function laneHow(lane) {
  const how = {};
  if (isObject(lane.target)) {
    for (const k of TARGET_KEYS) if (lane.target[k] !== undefined) how[k] = lane.target[k];
    if (lane.hidden) how.hidden = true;
  }
  if (lane.two_way) how.two_way = true;
  return how;
}

/**
 * The body to store, normalised, or a string saying what is wrong with it.
 *
 * Keeps only the keys the game reads, so nothing else rides along into the
 * table; `id` is left out, being the store's to mint (`mintId`).
 */
export function checkBody(body, nameOf = (i) => `stage ${i + 1}`) {
  if (!isObject(body)) return "not a gauntlet";
  const name = tidy(body.name);
  if (!name) return "the gauntlet needs a name";
  if (textLength(name) > LIMITS.name) return `the name is over ${LIMITS.name} characters`;
  if (!Array.isArray(body.stages) || body.stages.length === 0) return "the gauntlet has no stages";
  if (body.stages.length > LIMITS.stages) return `a gauntlet has at most ${LIMITS.stages} stages`;

  const stages = [];
  const ids = new Set();
  for (const [i, raw] of body.stages.entries()) {
    const where = nameOf(i);
    if (!isObject(raw)) return `${where} is not a stage`;
    if (!isInt(raw.id) || raw.id < 0 || ids.has(raw.id)) return `${where} has a bad id`;
    ids.add(raw.id);
    const title = tidy(raw.title);
    if (textLength(title) > LIMITS.title) return `${where}'s title is over ${LIMITS.title} characters`;
    const hint = tidy(raw.hint);
    if (textLength(hint) > LIMITS.hint) return `${where}'s hint is over ${LIMITS.hint} characters`;
    if (raw.briefing !== undefined && !Array.isArray(raw.briefing)) return `${where}'s briefing is not a list`;
    const briefing = (raw.briefing || []).map(tidy).filter(Boolean);
    if (briefing.length > LIMITS.briefingLines) {
      return `${where} has more than ${LIMITS.briefingLines} briefing paragraphs`;
    }
    const long = briefing.findIndex((line) => textLength(line) > LIMITS.briefingLine);
    if (long >= 0) return `${where}'s briefing paragraph ${long + 1} is over ${LIMITS.briefingLine} characters`;
    const problem = setupProblem(raw.settings);
    if (problem) return `${where} ${problem}`;
    const stage = { id: raw.id, title, briefing, hint, settings: stageSetup(raw.settings) };
    if (raw.target !== undefined && raw.target !== null) {
      const target = checkTarget(raw.target);
      if (typeof target === "string") return `${where} ${target}`;
      stage.target = target;
    }
    if (raw.bonus_life !== undefined) {
      if (typeof raw.bonus_life !== "boolean") return `${where}'s bonus_life is not true or false`;
      stage.bonus_life = raw.bonus_life;
    }
    if (raw.at !== undefined) {
      if (!cellOf(raw.at)) return `${where}'s place on the graph is not two whole numbers from 0 to ${LIMITS.stages - 1}`;
      stage.at = cellOf(raw.at);
    }
    stages.push(stage);
  }
  const atCount = stages.filter((stage) => stage.at).length;
  if (atCount && atCount < stages.length) return "some stages have a place on the graph and some don't";
  if (new Set(stages.filter((stage) => stage.at).map((stage) => String(stage.at))).size < atCount) {
    return "two stages share a place on the graph";
  }

  const out = { name, stages };
  if (body.lanes !== undefined) {
    if (!Array.isArray(body.lanes)) return "lanes is not a list";
    const lanes = [];
    for (const pair of body.lanes) {
      if (!Array.isArray(pair) || (pair.length !== 2 && pair.length !== 3)) return "a lane is not a pair of stage ids";
      const [a, b, how] = pair;
      if (!ids.has(a) || !ids.has(b) || a === b) return "a lane joins a stage that is not there";
      const kept = pair.length === 3 ? checkLane(how) : {};
      if (typeof kept === "string") return kept;
      lanes.push(Object.keys(kept).length ? [a, b, kept] : [a, b]);
    }
    out.lanes = lanes;
  }
  if (body.start !== undefined) {
    if (!Array.isArray(body.start) || !body.start.every((s) => ids.has(s))) return "start names a stage that is not there";
    out.start = [...body.start];
  }
  if (body.lives !== undefined) {
    if (!isInt(body.lives) || body.lives < 1 || body.lives > LIMITS.lives) {
      return `lives must be a whole number from 1 to ${LIMITS.lives}`;
    }
    if (body.lives !== 1) out.lives = body.lives;
  }
  const stranded = unreachable(out);
  if (stranded >= 0) return `${nameOf(stranded)} can't be reached from a start`;
  if (new TextEncoder().encode(JSON.stringify(out)).length > LIMITS.bodyBytes) {
    return `the gauntlet is over ${LIMITS.bodyBytes / 1024} KiB`;
  }
  return out;
}

const LANE_KEYS = new Set([...TARGET_KEYS, "two_way", "hidden"]);

/**
 * A lane's third element as stored, or a string saying what is wrong with it:
 * an optional target (a stage target's keys), `hidden` (only on a lane with a
 * target: there is nothing else to reveal it), and `two_way`. Written in the
 * order the game's `gauntlet.to_dict` writes them.
 */
function checkLane(how) {
  if (!isObject(how) || Object.keys(how).some((k) => !LANE_KEYS.has(k))) {
    return "a lane's third element can only say two_way, hidden and a target (turns, lost, bot, beat)";
  }
  if (how.two_way !== undefined && typeof how.two_way !== "boolean") return "a lane's two_way is not true or false";
  if (how.hidden !== undefined && typeof how.hidden !== "boolean") return "a lane's hidden is not true or false";
  const gate = Object.fromEntries(Object.entries(how).filter(([k]) => TARGET_KEY_SET.has(k)));
  let out = {};
  if (Object.keys(gate).length) {
    const target = checkTarget(gate);
    if (typeof target === "string") return `a lane ${target}`;
    out = { ...target };
  }
  if (how.hidden) {
    if (!out.turns) return "a hidden lane needs a target to reveal it";
    out.hidden = true;
  }
  if (how.two_way) out.two_way = true;
  return out;
}

/**
 * The index of the first stage no start can reach following the lanes the way
 * they run, or -1. Read as the game's `Gauntlet.from_dict` reads it (no lanes:
 * list order; no start: the first stage; a pair already joined: dropped), so
 * this is `gauntlet.problems` refusing what it would name.
 */
function unreachable({ stages, lanes, start }) {
  const joined = lanes ?? stages.slice(1).map((s, i) => [stages[i].id, s.id]);
  const onward = new Map(stages.map((s) => [s.id, []]));
  const seen = new Set();
  for (const [a, b, how] of joined) {
    if (seen.has(`${a},${b}`) || seen.has(`${b},${a}`)) continue;
    seen.add(`${a},${b}`);
    onward.get(a).push(b);
    if (how?.two_way) onward.get(b).push(a);
  }
  const reached = new Set(start?.length ? start : [stages[0].id]);
  const queue = [...reached];
  while (queue.length) {
    for (const n of onward.get(queue.shift())) {
      if (!reached.has(n)) {
        reached.add(n);
        queue.push(n);
      }
    }
  }
  return stages.findIndex((s) => !reached.has(s.id));
}

/** JSON with every object's keys sorted, so equal bodies hash equally. */
export function canonicalJson(value) {
  return JSON.stringify(canonical(value));
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (isObject(value)) {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, canonical(value[k])]));
  }
  return value;
}

/**
 * The stored id for a checked body (`checkBody`'s result): slug of its name,
 * then the first 12 hex digits of SHA-256 over the canonical body without `id`.
 *
 * @param subtle  a Web Crypto `SubtleCrypto` (the browser's, or Node's webcrypto)
 */
export async function mintId(body, subtle = globalThis.crypto?.subtle) {
  const { id, ...rest } = body;
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(rest)));
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${slugOf(rest.name)}-${hex.slice(0, 12)}`;
}

/** The author's name as stored: tidy, and refused when too long. */
export function checkByName(value) {
  const name = tidy(value);
  return textLength(name) > LIMITS.byName ? null : name;
}

/** Where a stored gauntlet opens in the game: `#g=<id>` on the game's page. */
export function gameLink(gameUrl, gauntletId) {
  return `${gameUrl}#g=${gauntletId}`;
}

/**
 * A listed row as the public gauntlets page shows it. `row` is the page's select:
 * `{gauntlet_id, by_name, created_at, name, stages}`, the last two lifted out of
 * the body. Tolerant, like every reader of a stored body: a stage without a
 * title is numbered, and a row without a name shows its id.
 */
export function listingOf(row) {
  const stages = Array.isArray(row?.stages) ? row.stages : [];
  return {
    id: row?.gauntlet_id || "",
    name: tidy(row?.name) || row?.gauntlet_id || "",
    by: tidy(row?.by_name),
    titles: stages.map((stage, i) => tidy(stage?.title) || `Stage ${i + 1}`),
    created: row?.created_at || null,
  };
}

/** A `.json` file name for the desktop download. */
export function fileName(body, gauntletId) {
  return `${gauntletId || slugOf(body?.name)}.json`;
}

// -- the page's draft ---------------------------------------------------------
// What the page edits and keeps in localStorage between visits: the form as
// typed, each stage carrying the link it was pasted from beside the setup that
// link decoded to. `buildBody` turns it into a gauntlet; `draftFromBody` turns a
// stored gauntlet back into one, for "open, edit, save" (which saves a new id).
//
// Lanes join stages by a `key` each stage gets when it is made (from the
// draft's `next_key` counter), never by place, so reordering and removing never
// rewire a lane: `lanes` is `[{from, to, two_way, target, hidden}]` by key
// and `start` a list of keys. A stage's or lane's target is whatever numbers
// were typed or filled in; an older draft's `aim` beside it is ignored.

/** A stage with nothing in it yet. */
export function emptyStage(key = 0) {
  return { key, link: "", settings: null, title: "", briefing: [""], hint: "", target: null, bonus_life: null, at: null };
}

/** A lane with no options: any clear of `from` opens `to`. */
export function emptyLane(from, to) {
  return { from, to, two_way: false, target: null, hidden: false };
}

/** A stored or kept target that checks out, or null. */
function keptTarget(raw) {
  const target = isObject(raw) ? checkTarget(raw) : null;
  return typeof target === "string" ? null : target;
}


/** A draft with one empty stage, open from the start. */
export function emptyDraft() {
  return { name: "", by_name: "", wants_listing: false, lives: 1, stages: [emptyStage(0)], lanes: [], start: [0], next_key: 1 };
}

/** Each stage joined to the next in list order: a linear gauntlet's lanes. */
function chainOf(stages) {
  return stages.slice(1).map((stage, i) => emptyLane(stages[i].key, stage.key));
}

/**
 * A stored gauntlet as a draft to edit, its stages in play order with its lanes
 * and starts. Each stage's link is rebuilt from its setup, so it reads like the
 * link it was pasted from, but only its setup: the body keeps a target's
 * numbers, never the challenge link they came from. The graph is read as the
 * game reads it (`graphOf`).
 *
 * @param linkFor  async (settings) => a game link carrying that setup
 */
export async function draftFromBody(body, linkFor) {
  const stages = [];
  const keyOf = new Map();
  for (const stage of body?.stages || []) {
    if (!isObject(stage) || !isObject(stage.settings) || !isInt(stage.id) || keyOf.has(stage.id)) continue;
    const briefing = (stage.briefing || []).filter((p) => typeof p === "string");
    const target = keptTarget(stage.target);
    keyOf.set(stage.id, stages.length);
    stages.push({
      key: stages.length,
      link: await linkFor(stage.settings),
      settings: stage.settings,
      title: typeof stage.title === "string" ? stage.title : "",
      briefing: briefing.length ? briefing : [""],
      hint: typeof stage.hint === "string" ? stage.hint : "",
      target,
      bonus_life: typeof stage.bonus_life === "boolean" ? stage.bonus_life : null,
      at: cellOf(stage.at),
    });
  }
  if (!stages.length) return emptyDraft();
  const graph = graphOf({ ...body, stages: [...keyOf.keys()].map((id) => ({ id })) });
  const lanes = graph.lanes.map(([a, b]) => {
    const pair = `${a},${b}`;
    const target = keptTarget(graph.gates.get(pair));
    return {
      ...emptyLane(keyOf.get(a), keyOf.get(b)),
      two_way: graph.twoWay.has(pair),
      target,
      hidden: Boolean(target) && graph.hidden.has(pair),
    };
  });
  return {
    name: typeof body?.name === "string" ? body.name : "",
    by_name: "",
    wants_listing: false,
    lives: livesOf(body?.lives),
    stages,
    lanes,
    start: graph.start.map((id) => keyOf.get(id)),
    next_key: stages.length,
  };
}

/** A kept or stored `lives`, clamped as `Gauntlet.from_dict` clamps it. */
function livesOf(raw) {
  return isInt(raw) ? Math.max(1, Math.min(LIMITS.lives, raw)) : 1;
}

function readLane(raw, have) {
  if (!isObject(raw) || !have.has(raw.from) || !have.has(raw.to) || raw.from === raw.to) return null;
  const target = keptTarget(raw.target);
  return {
    from: raw.from,
    to: raw.to,
    two_way: raw.two_way === true,
    target,
    hidden: Boolean(target) && raw.hidden === true,
  };
}

/**
 * A draft read back from storage, or null. Tolerant: whatever is missing or of
 * the wrong type comes back empty, so an old or hand-edited draft never breaks
 * the page. A draft kept before lanes (or with keys that don't hold) comes back
 * linear: its stages keyed in order, each joined to the next, the first open.
 */
export function readDraft(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isObject(data) || !Array.isArray(data.stages)) return null;
  const str = (v) => (typeof v === "string" ? v : "");
  const raw = data.stages.filter(isObject).slice(0, LIMITS.stages);
  const keys = raw.map((s) => s.key);
  const keyed = raw.length > 0 && keys.every((k) => isInt(k) && k >= 0) && new Set(keys).size === keys.length;
  const stages = raw.map((s, i) => {
    const target = keptTarget(s.target);
    return {
      key: keyed ? s.key : i,
      link: str(s.link),
      settings: isObject(s.settings) ? s.settings : null,
      title: str(s.title),
      briefing: Array.isArray(s.briefing) && s.briefing.length
        ? s.briefing.map(str).slice(0, LIMITS.briefingLines) : [""],
      hint: str(s.hint),
      target,
      bonus_life: typeof s.bonus_life === "boolean" ? s.bonus_life : null,
      at: cellOf(s.at),
    };
  });
  if (!stages.length) stages.push(emptyStage(0));
  const have = new Set(stages.map((s) => s.key));
  let lanes = chainOf(stages);
  let start = [stages[0].key];
  if (keyed && Array.isArray(data.lanes)) {
    const joined = new Set();
    lanes = data.lanes.map((lane) => readLane(lane, have)).filter((lane) => {
      if (!lane || joined.has(`${lane.from},${lane.to}`) || joined.has(`${lane.to},${lane.from}`)) return false;
      joined.add(`${lane.from},${lane.to}`);
      return true;
    });
  }
  if (keyed && Array.isArray(data.start)) start = [...new Set(data.start.filter((k) => have.has(k)))];
  return {
    name: str(data.name),
    by_name: str(data.by_name),
    wants_listing: data.wants_listing === true,
    lives: livesOf(data.lives),
    stages,
    lanes,
    start,
    next_key: Math.max(isInt(data.next_key) ? data.next_key : 0, ...stages.map((s) => s.key + 1)),
  };
}

/**
 * The first thing stopping a draft from being saved, or "" when it can be:
 * a stage with no link, or one whose link did not decode, is named before the
 * body's own checks run, then a draft with no stage open from the start. A
 * stage is named as the graph shows it: its title, else "Stage 2-1".
 */
export function draftProblem(draft) {
  const names = draftNames(draft);
  const name = (stage) => stageName(draft, stage, names);
  for (const stage of draft?.stages || []) {
    if (!stage.settings) {
      return stage.link.trim() ? `${name(stage)}'s link can't be used as a stage` : `${name(stage)} needs a game link`;
    }
  }
  if (Array.isArray(draft?.start) && !draft.stages.some((s) => draft.start.includes(s.key))) {
    return "tick at least one stage as open from the start";
  }
  const body = checkBody(buildBody(draft), (i) => name(draft.stages[i]));
  if (typeof body === "string") return body;
  if (checkByName(draft.by_name) === null) return `your name is over ${LIMITS.byName} characters`;
  return "";
}

// -- editing the graph ---------------------------------------------------------
// The page's presses on a draft's lanes and stages. Each changes `draft` in
// place, as the page's other edits do.

function takeKey(draft) {
  const used = draft.stages.map((s) => s.key + 1);
  const key = Math.max(isInt(draft.next_key) ? draft.next_key : 0, ...used);
  draft.next_key = key + 1;
  return key;
}

/**
 * Add a stage at the bottom, joined from the stage that was last, and return
 * it: an author who never touches a lane gets a linear gauntlet. The page adds
 * stages through `addStageAt`, in the column the author picks.
 */
export function addStage(draft) {
  if (!Array.isArray(draft.lanes)) draft.lanes = chainOf(draft.stages);
  if (!Array.isArray(draft.start)) draft.start = draft.stages.length ? [draft.stages[0].key] : [];
  const stage = emptyStage(takeKey(draft));
  const cells = placed(draft, false);
  if (cells) stage.at = [Math.max(-1, ...cells.map(([col]) => col)) + 1, 0];
  const last = draft.stages.at(-1);
  draft.stages.push(stage);
  if (last) draft.lanes.push(emptyLane(last.key, stage.key));
  else draft.start.push(stage.key);
  return stage;
}

/** The stages a lane leads into `key` from, the way each lane runs. */
export function parentsOf(draft, key) {
  return (draft.lanes || []).flatMap((lane) => (lane.to === key ? [lane.from]
                                              : lane.two_way && lane.from === key ? [lane.to] : []));
}

/** Remove a stage with its lanes and its start mark; nothing is rewired. */
export function removeStage(draft, key) {
  draft.stages = draft.stages.filter((s) => s.key !== key);
  if (Array.isArray(draft.lanes)) draft.lanes = draft.lanes.filter((l) => l.from !== key && l.to !== key);
  if (Array.isArray(draft.start)) draft.start = draft.start.filter((k) => k !== key);
}

/** The lane that runs from `a` to `b` (one-way that way, or two-way), or null. */
export function laneFrom(draft, a, b) {
  return (draft.lanes || []).find((l) => (l.from === a && l.to === b) || (l.two_way && l.from === b && l.to === a))
    ?? null;
}

/** A one-way lane from `b` back to `a`, which `a`'s chip offers to make two-way. */
export function laneBack(draft, a, b) {
  return (draft.lanes || []).find((l) => l.from === b && l.to === a && !l.two_way) ?? null;
}

/**
 * Join `a` to `b` and return the lane. A pair already joined the other way is
 * made two-way rather than given a second lane, since the game keeps a pair's
 * first lane and drops the second.
 */
export function joinLane(draft, a, b) {
  const there = laneFrom(draft, a, b);
  if (there) return there;
  const back = laneBack(draft, a, b);
  if (back) {
    back.two_way = true;
    return back;
  }
  const lane = emptyLane(a, b);
  draft.lanes.push(lane);
  return lane;
}

/** Remove a lane. */
export function dropLane(draft, lane) {
  draft.lanes = draft.lanes.filter((l) => l !== lane);
}

/** Tick or untick a stage as open from the start, keeping list order. */
export function setStart(draft, key, on) {
  const opened = new Set(draft.start || []);
  if (on) opened.add(key);
  else opened.delete(key);
  draft.start = draft.stages.map((s) => s.key).filter((k) => opened.has(k));
}

// -- the graph as the page edits it -------------------------------------------
// The author places each stage on the graph: a draft stage's `at` is its
// `[column, row]`, kept closed up (no empty column, rows 0.. in each). Lanes
// never move a stage, and moving one never touches a lane. A draft opened from
// a body without places (or kept before them) is placed as `layout` computes it.

/** The draft's graph as the game will read it: stages by list index. */
function draftGraph(draft) {
  return graphOf(buildBody(draft));
}

/**
 * Each stage's place, by list index, closed up: the draft's own when every
 * stage has one and no two share, else (unless `compute` is false, when it is
 * null) what `layout` makes of the lanes.
 */
function placed(draft, compute = true) {
  const list = draft?.stages || [];
  let cells = list.map((stage) => cellOf(stage.at));
  if (!cells.every(Boolean) || new Set(cells.map(String)).size !== cells.length) {
    if (!compute) return null;
    const computed = layout(graphOf(buildBody(draft, { at: false })));
    cells = list.map((_, i) => computed.get(i));
  }
  const cols = [...new Set(cells.map(([col]) => col))].sort((x, y) => x - y);
  const out = cells.map(([col]) => [cols.indexOf(col), 0]);
  for (const col of cols) {
    const here = cells.map((cell, i) => [cell, i]).filter(([[c]]) => c === col).sort(([x], [y]) => x[1] - y[1]);
    here.forEach(([, i], row) => { out[i][1] = row; });
  }
  return out;
}

/** Give every stage its place, closed up (`placed`). */
export function placeStages(draft) {
  placed(draft).forEach((cell, i) => { draft.stages[i].at = cell; });
  return draft;
}

/** Each stage's `[column, row]` by key, as the game will draw it. */
export function draftCells(draft) {
  const cells = layout(draftGraph(draft));
  return new Map((draft?.stages || []).map((stage, i) => [stage.key, cells.get(i)]));
}

/**
 * Each stage's corner name by key ("2-1", or "2" alone in its column), over
 * the stages keyed in `shown` (every stage by default): `cellNames`.
 */
export function draftNames(draft, shown = null) {
  const list = draft?.stages || [];
  const index = new Map(list.map((stage, i) => [stage.key, i]));
  const names = cellNames(draftGraph(draft), shown === null ? null : [...shown].map((key) => index.get(key)));
  return new Map(list.flatMap((stage, i) => (names.has(i) ? [[stage.key, names.get(i)]] : [])));
}

/** A stage's name on the page: its title, else "Stage 2-1", as the game names an untitled one. */
export function stageName(draft, stage, names = draftNames(draft)) {
  return tidy(stage?.title) || `Stage ${names.get(stage?.key) ?? "?"}`;
}

const columnOf = (draft, col) => draft.stages.filter((stage) => stage.at[0] === col)
  .sort((x, y) => x.at[1] - y.at[1]);
const lastColumn = (draft) => Math.max(0, ...draft.stages.map((stage) => stage.at[0]));

/** What a stage added at the foot of column `col` will be called: "Stage 3-2". */
export function newStageName(draft, col) {
  placeStages(draft);
  const count = columnOf(draft, col).length;
  return `Stage ${count ? `${col + 1}-${count + 1}` : `${col + 1}`}`;
}

/**
 * Add a stage at the foot of column `col` (at most one past the last) and
 * return it, or null at the stage limit. It has no lanes, and is open from
 * nothing, unless `from` is given: then a lane runs to it from that stage.
 */
export function addStageAt(draft, col, { from = null } = {}) {
  placeStages(draft);
  if (draft.stages.length >= LIMITS.stages) return null;
  const column = Math.max(0, Math.min(col, lastColumn(draft) + 1));
  const stage = emptyStage(takeKey(draft));
  stage.at = [column, columnOf(draft, column).length];
  draft.stages.push(stage);
  if (from !== null && draft.stages.some((s) => s.key === from)) joinLane(draft, from, stage.key);
  placeStages(draft);
  return stage;
}

export const DIRECTIONS = ["left", "right", "up", "down"];

/**
 * The ways stage `key` can move: up or down a row within its column, left or
 * right a column (into a new column at either end, unless it is alone in its
 * own, which would leave the picture as it was).
 */
export function moveOptions(draft, key) {
  placeStages(draft);
  const stage = draft.stages.find((s) => s.key === key);
  if (!stage) return [];
  const [col, row] = stage.at;
  const size = columnOf(draft, col).length;
  const ok = { up: row > 0, down: row < size - 1, left: col > 0 || size > 1, right: col < lastColumn(draft) || size > 1 };
  return DIRECTIONS.filter((dir) => ok[dir]);
}

/**
 * Move stage `key` one place `dir` and say what moved, or null where it can't
 * go (`moveOptions`). Up and down swap it with its neighbour in the column;
 * left and right take it into the next column at the same row (or its foot).
 * Lanes are never touched.
 */
export function moveStageTo(draft, key, dir) {
  if (!moveOptions(draft, key).includes(dir)) return null;
  const stage = draft.stages.find((s) => s.key === key);
  const name = stageName(draft, stage);
  const [col, row] = stage.at;
  if (dir === "up" || dir === "down") {
    const other = columnOf(draft, col)[row + (dir === "up" ? -1 : 1)];
    [stage.at, other.at] = [other.at, stage.at];
    return `Moved ${name} ${dir} a row.`;
  }
  let to = col + (dir === "left" ? -1 : 1);
  if (to < 0) {
    for (const s of draft.stages) s.at = [s.at[0] + 1, s.at[1]];
    to = 0;
  }
  for (const s of columnOf(draft, stage.at[0])) if (s.at[1] > row) s.at = [s.at[0], s.at[1] - 1];
  const at = Math.min(row, columnOf(draft, to).length);
  for (const s of columnOf(draft, to)) if (s.at[1] >= at) s.at = [s.at[0], s.at[1] + 1];
  stage.at = [to, at];
  placeStages(draft);
  return `Moved ${name} ${dir} a column.`;
}
