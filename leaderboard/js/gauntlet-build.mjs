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

/**
 * The gauntlet the page's form describes: `{name, stages: [{title, briefing,
 * hint, settings}]}` in play order. Stages are numbered from 0 in that order,
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
    stages.push({ id: raw.id, title, briefing, hint, settings: stageSetup(raw.settings) });
  }

  const out = { name, stages };
  if (body.lanes !== undefined) {
    if (!Array.isArray(body.lanes)) return "lanes is not a list";
    const lanes = [];
    for (const pair of body.lanes) {
      if (!Array.isArray(pair) || pair.length !== 2) return "a lane is not a pair of stage ids";
      const [a, b] = pair;
      if (!ids.has(a) || !ids.has(b) || a === b) return "a lane joins a stage that is not there";
      lanes.push([a, b]);
    }
    out.lanes = lanes;
  }
  if (body.start !== undefined) {
    if (!Array.isArray(body.start) || !body.start.every((s) => ids.has(s))) return "start names a stage that is not there";
    out.start = [...body.start];
  }
  if (new TextEncoder().encode(JSON.stringify(out)).length > LIMITS.bodyBytes) {
    return `the gauntlet is over ${LIMITS.bodyBytes / 1024} KiB`;
  }
  return out;
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

/** A `.json` file name for the desktop download. */
export function fileName(body, gauntletId) {
  return `${gauntletId || slugOf(body?.name)}.json`;
}
