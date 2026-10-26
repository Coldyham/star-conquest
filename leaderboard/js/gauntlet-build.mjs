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

import { bestBot } from "./standings.mjs";

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
// `{turns, lost?, bot?, beat?}`. An author's own score is met; a bot's, copied
// off the board's bot column when the gauntlet is saved, is beaten (`beat`).
// Fixed into the file, never looked up live.

const TARGET_KEYS = new Set(["turns", "lost", "bot", "beat"]);

/** A stage's target as stored, or a string saying what is wrong with it. */
export function checkTarget(raw) {
  if (!isObject(raw) || Object.keys(raw).some((k) => !TARGET_KEYS.has(k))) {
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
 * The best bot's win on a setup as a target to beat, from that setup's
 * `bot_scores` rows: `{target}`, or `{none}` saying why there is none ("no-rows":
 * the setup has no bot column yet; "no-win": no bot won it, so any win clears).
 */
export function botTarget(rows) {
  if (!rows?.length) return { none: "no-rows" };
  const best = bestBot(rows);
  if (!best) return { none: "no-win" };
  return { target: { turns: best.turns, lost: best.lost, bot: tidy(best.bot).slice(0, LIMITS.bot), beat: true } };
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
 * hint, settings, target?}]}` in play order. Stages are numbered from 0 in that order,
 * and a linear gauntlet needs no `lanes` or `start` (the game joins stages in
 * list order and opens the first).
 */
export function buildBody(draft) {
  const stages = (draft?.stages || []).map((stage, i) => ({
    id: i,
    title: tidy(stage.title),
    briefing: (stage.briefing || []).map(tidy).filter(Boolean),
    hint: tidy(stage.hint),
    settings: stageSetup(stage.settings),
    ...(stage.target ? { target: stage.target } : {}),
  }));
  return { name: tidy(draft?.name), stages };
}

/**
 * The body to store, normalised, or a string saying what is wrong with it.
 *
 * Keeps only the keys the game reads, so nothing else rides along into the
 * table; `id` is left out, being the store's to mint (`mintId`).
 */
export function checkBody(body) {
  if (!isObject(body)) return "not a gauntlet";
  const name = tidy(body.name);
  if (!name) return "the gauntlet needs a name";
  if (textLength(name) > LIMITS.name) return `the name is over ${LIMITS.name} characters`;
  if (!Array.isArray(body.stages) || body.stages.length === 0) return "the gauntlet has no stages";
  if (body.stages.length > LIMITS.stages) return `a gauntlet has at most ${LIMITS.stages} stages`;

  const stages = [];
  const ids = new Set();
  for (const [i, raw] of body.stages.entries()) {
    const where = `stage ${i + 1}`;
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
    stages.push(stage);
  }

  const out = { name, stages };
  if (body.lanes !== undefined) {
    if (!Array.isArray(body.lanes)) return "lanes is not a list";
    const lanes = [];
    for (const pair of body.lanes) {
      if (!Array.isArray(pair) || (pair.length !== 2 && pair.length !== 3)) return "a lane is not a pair of stage ids";
      const [a, b, how] = pair;
      if (!ids.has(a) || !ids.has(b) || a === b) return "a lane joins a stage that is not there";
      if (pair.length === 3) {
        if (!isObject(how) || Object.keys(how).some((k) => k !== "two_way") || typeof how.two_way !== "boolean") {
          return "a lane's third element can only say two_way";
        }
      }
      lanes.push(how?.two_way ? [a, b, { two_way: true }] : [a, b]);
    }
    out.lanes = lanes;
  }
  if (body.start !== undefined) {
    if (!Array.isArray(body.start) || !body.start.every((s) => ids.has(s))) return "start names a stage that is not there";
    out.start = [...body.start];
  }
  const stranded = unreachable(out);
  if (stranded >= 0) return `stage ${stranded + 1} can't be reached from a start`;
  if (new TextEncoder().encode(JSON.stringify(out)).length > LIMITS.bodyBytes) {
    return `the gauntlet is over ${LIMITS.bodyBytes / 1024} KiB`;
  }
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

/** A stage with nothing in it yet. */
export function emptyStage() {
  return { link: "", settings: null, title: "", briefing: [""], hint: "", aim: "none", target: null };
}

/** A stored or kept target that checks out, or null. */
function keptTarget(raw) {
  const target = isObject(raw) ? checkTarget(raw) : null;
  return typeof target === "string" ? null : target;
}

/** How a draft stage's target was chosen: none, the link's score, the best bot. */
export const AIMS = Object.freeze(["none", "link", "bot"]);

/** A draft with one empty stage. */
export function emptyDraft() {
  return { name: "", by_name: "", wants_listing: false, stages: [emptyStage()] };
}

/**
 * A stored gauntlet as a draft to edit, its stages in play order. Each stage's
 * link is rebuilt from its setup, so it reads like the link it was pasted from.
 *
 * @param linkFor  async (settings) => a game link carrying that setup
 */
export async function draftFromBody(body, linkFor) {
  const stages = [];
  for (const stage of body?.stages || []) {
    if (!isObject(stage) || !isObject(stage.settings)) continue;
    const briefing = (stage.briefing || []).filter((p) => typeof p === "string");
    const target = keptTarget(stage.target);
    stages.push({
      aim: target ? (target.beat ? "bot" : "link") : "none",
      target,
      link: await linkFor(stage.settings),
      settings: stage.settings,
      title: typeof stage.title === "string" ? stage.title : "",
      briefing: briefing.length ? briefing : [""],
      hint: typeof stage.hint === "string" ? stage.hint : "",
    });
  }
  return {
    name: typeof body?.name === "string" ? body.name : "",
    by_name: "",
    wants_listing: false,
    stages: stages.length ? stages : [emptyStage()],
  };
}

/**
 * A draft read back from storage, or null. Tolerant: whatever is missing or of
 * the wrong type comes back empty, so an old or hand-edited draft never breaks
 * the page.
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
  const stages = data.stages.filter(isObject).slice(0, LIMITS.stages).map((s) => {
    const target = keptTarget(s.target);
    return {
      link: str(s.link),
      settings: isObject(s.settings) ? s.settings : null,
      title: str(s.title),
      briefing: Array.isArray(s.briefing) && s.briefing.length
        ? s.briefing.map(str).slice(0, LIMITS.briefingLines) : [""],
      hint: str(s.hint),
      aim: target && AIMS.includes(s.aim) ? s.aim : "none",
      target,
    };
  });
  return {
    name: str(data.name),
    by_name: str(data.by_name),
    wants_listing: data.wants_listing === true,
    stages: stages.length ? stages : [emptyStage()],
  };
}

/**
 * The first thing stopping a draft from being saved, or "" when it can be:
 * a stage with no link, or one whose link did not decode, is named before the
 * body's own checks run.
 */
export function draftProblem(draft) {
  for (const [i, stage] of (draft?.stages || []).entries()) {
    if (!stage.settings) {
      return stage.link.trim() ? `stage ${i + 1}'s link can't be used as a stage`
                               : `stage ${i + 1} needs a game link`;
    }
  }
  const body = checkBody(buildBody(draft));
  if (typeof body === "string") return body;
  if (checkByName(draft.by_name) === null) return `your name is over ${LIMITS.byName} characters`;
  return "";
}

/** Move the stage at `from` by `step` places; out of range is a no-op. */
export function moveStage(stages, from, step) {
  const to = from + step;
  if (from < 0 || from >= stages.length || to < 0 || to >= stages.length) return stages;
  const out = [...stages];
  [out[from], out[to]] = [out[to], out[from]];
  return out;
}
