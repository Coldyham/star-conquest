// The gauntlet builder (gauntlet.html): paste a game link per stage, write its
// briefing and hint, join stages by lanes, save, and share the short link that
// opens it in the game.
//
// The graph is the editor. It is drawn by the game's own drawer from
// gauntlet-graph.mjs's `layout`, the same function as the game's
// `gauntlet.layout`, so it is the picture the game will show. A tap selects a
// stage, and the one card under the graph is that stage's. Arrows round the
// selected stage move it a row or a column; a dashed box at the foot of each
// column adds a stage there; the card's Add link, then a stage or a dashed box,
// draws a lane from it; a tap on a lane opens its options (two-way, a gate,
// hidden) in the card. A stage's place is never stored: a move changes its
// lanes or list order, planned and checked in gauntlet-build.mjs before it is
// offered (docs/design/gauntlet.md, "Lanes on the board").
//
// The draft lives in localStorage under DRAFT_KEY as the form was typed, so a
// reload loses nothing; once saved, the stored row is the record and the draft
// is only a convenience. Opening the page as `gauntlet.html#<id>` loads a stored
// gauntlet as a new draft: rows never change, so editing one and saving makes a
// new id. Everything that decides anything is in gauntlet-build.mjs, which
// /api/gauntlet runs too; this file is the DOM around it.
//
// A stage's or lane's target (docs/design/gauntlet.md, "Stage targets") is
// the turns and ships typed in, blank for none. Buttons fill it from the pasted
// challenge link's score or a bot's win on the setup read off `bot_scores`;
// either way the numbers are fixed into the file: the game never looks them up.

import { configured, inList, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { deflate } from "./deflate-browser.mjs";
import { clear, el, mapSummary } from "./format.mjs";
import {
  DIRECTIONS, GAUNTLET_ID, LIMITS, botWins, buildBody, checkBody, draftFromBody, draftNames, draftProblem,
  dropLane, emptyDraft, fileName, gameLink, joinLane, laneBack, laneFrom, linkTarget, meetsTarget, parentsOf, planAdd,
  planMove, readDraft, removeStage, postedBest, setStart, setupProblem, stageName, stageSetup, targetText, textLength,
  tidy,
} from "./gauntlet-build.mjs";
import { depths, goals, grantsLife, graphOf, hiddenStages, laneLabel, layout, reachable, ways } from "./gauntlet-graph.mjs";
// The game's own drawer, so the board draws the picture its gauntlet list shows (one site: /play/ and /board/).
import { drawGraph } from "/play/js/stagegraph.mjs";
import { inflate } from "./inflate-browser.mjs";
import { myName } from "./me.mjs";
import { mountNav } from "./nav.mjs";
import { configLabel } from "./setup.mjs";
import { decodeToken, setupIdentity } from "./token-decode.mjs";
import { encodeToken } from "./token-encode.mjs";

const ENDPOINT = "/api/gauntlet";
const DRAFT_KEY = "sc_gauntlet_draft";
// Which way of making one the page last showed: "gen" or "build".
const WAY_KEY = "sc_gauntlet_way";
const WAYS = ["gen", "build"];

let draft = emptyDraft();

/** A new draft, signed with the name this browser posts under: the Architect badge reads it. */
function freshDraft() {
  const name = myName();
  return { ...emptyDraft(), by_name: [...name].length <= LIMITS.byName ? name : "" };
}
// What each stage's link said, by stage object: the summary line or the reason
// it could not be a stage, and the link's score and game key for a target.
// Rebuilt from the link on load, never stored.
const readings = new WeakMap();
// What the board knows of a stage's setup, by stage, for the target's quick
// fills: a promise of `boardScores`' answer. Dropped when the link changes.
const botBests = new WeakMap();
let saved = null;   // {id, body} once saved, until the draft changes again
let openLane = null;   // the lane whose options show in its near stage's card
let view = "author";   // the graph as the author sees it, or as a new player does
let picked = null;     // the selected stage's key: its card is the one shown
let linking = false;   // Add link pressed: the next stage or dashed box pressed is joined from `picked`
let said = "";         // what the last edit on the graph did, said under it

const $ = (id) => document.getElementById(id);

function keep() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // storage full or blocked: the page still works, it just won't remember
  }
}

let redraw = 0;

/** After an edit: keep the draft and redraw the graph (after a pause in typing, with `soon`). */
function changed({ soon = false } = {}) {
  keep();
  clearTimeout(redraw);
  if (soon) redraw = setTimeout(drawDiagram, 200);
  else drawDiagram();
  if (saved) {
    saved = null;
    $("result").hidden = true;
    say("");
  }
  showProblem();
}

function say(message, kind = "") {
  $("status").textContent = message;
  $("status").className = `status ${kind}`;
}

function showProblem() {
  const problem = draftProblem(draft);
  $("problem").textContent = problem ? `To save: ${problem}.` : "";
  $("save").disabled = Boolean(problem);
}

/** "12/32", marked when over. */
function counter(node, text, limit) {
  const n = textLength(tidy(text));
  node.textContent = `${n}/${limit}`;
  node.classList.toggle("over", n > limit);
}

async function linkFor(settings) {
  return `${GAME_URL}#${await encodeToken(settings, deflate)}`;
}

/** Read a stage's pasted link into its setup, or say why it can't be one. */
async function readLink(stage) {
  const text = stage.link.trim();
  botBests.delete(stage);
  if (!text) {
    stage.settings = null;
    readings.set(stage, null);
    return;
  }
  try {
    const decoded = await decodeToken(text, inflate);
    const setup = stageSetup(decoded.setup);
    const problem = setupProblem(setup);
    stage.settings = problem ? null : setup;
    readings.set(stage, problem
      ? { error: `This link ${problem}.` }
      : { text: `${mapSummary({ ...decoded, settings_json: setup })} · ${configLabel(setup)}`,
          score: decoded.challenge, gameKey: decoded.gameKey });
  } catch {
    stage.settings = null;
    readings.set(stage, { error: "That doesn't read as a Star Conquest game link." });
  }
}

/**
 * The setup's `bot_scores` rows and its `game_summary` rows (for the best
 * posted score). A challenge link carries the game's own key; a Get Link
 * carries none, so the setup is also matched to its `games` row by jsonb
 * equality (contains and is contained by), as `campaign_games` matches.
 */
async function boardRows(stage) {
  const keys = new Set([readings.get(stage)?.gameKey].filter(Boolean));
  const json = encodeURIComponent(JSON.stringify(stage.settings));
  const games = await select(`games?select=game_key&settings_json=cs.${json}&settings_json=cd.${json}`)
    .catch(() => []);
  for (const game of games) keys.add(game.game_key);
  if (!keys.size) return { bots: [], posted: [] };
  const [bots, posted] = await Promise.all([
    select(`bot_scores?select=bot,won,turns,lost&game_key=${inList(keys)}`),
    select(`game_summary?select=best_turns,best_lost,best_by_name,best_user_name&game_key=${inList(keys)}`)
      .catch(() => []),
  ]);
  return { bots, posted };
}

const stageAt = (key) => draft.stages.find((s) => s.key === key);

/** A stage's name on a chip or in the graph: its title, else "Stage 2-1" for its place. */
function nameOf(stage) {
  return stageName(draft, stage);
}

/** "≤12", "≤12/30", "beat 31/12": a gate in a tight spot. */
function shortTarget(target) {
  const score = Number.isInteger(target.lost) ? `${target.turns}/${target.lost}` : String(target.turns);
  return target.beat ? `beat ${score}` : `≤${score}`;
}

/** Stages that are the same setup as an earlier one: they would clear together. */
function duplicates() {
  const seen = new Map();
  const out = new Set();
  draft.stages.forEach((stage, i) => {
    if (!stage.settings) return;
    const key = setupIdentity(stage.settings);
    if (seen.has(key)) out.add(i);
    else seen.set(key, i);
  });
  return out;
}

function textField(id, value, placeholder, limit, onInput, multiline = false) {
  const field = el(multiline ? "textarea" : "input", {
    id, class: "plain", placeholder, ...(multiline ? { rows: "3" } : { type: "text" }),
  });
  field.value = value;
  const count = el("p", { class: "count" });
  if (limit) counter(count, value, limit);
  field.addEventListener("input", () => {
    onInput(field.value);
    if (limit) counter(count, field.value, limit);
    changed({ soon: true });
  });
  return [field, count];
}

function renderSummary(node, stage, duplicate) {
  const reading = readings.get(stage);
  clear(node);
  node.className = "stage-summary";
  if (!reading) {
    node.textContent = "Paste the link the game's Get Link button copies.";
    return;
  }
  if (reading.error) {
    node.classList.add("bad");
    node.textContent = reading.error;
    return;
  }
  node.append(reading.text);
  if (duplicate) node.append(el("span", { class: "warn", text: " · same map as an earlier stage" }));
  node.append(" · ", el("a", { href: "#", class: "play-stage", text: "Play this stage" }));
  node.querySelector(".play-stage").addEventListener("click", async (event) => {
    event.preventDefault();
    window.open(await linkFor(stage.settings), "_blank", "noopener");
  });
}

/**
 * What the board knows of `stage`'s setup, asked once per link: `botWins`'
 * answer plus `posted`, the best posted score (`postedBest`), or null when the
 * board couldn't say.
 */
function boardScores(stage) {
  if (!stage?.settings || !configured()) return Promise.resolve(null);
  if (!botBests.has(stage)) {
    botBests.set(stage, boardRows(stage)
      .then(({ bots, posted }) => ({ ...botWins(bots), posted: postedBest(posted) }))
      .catch(() => null));
  }
  return botBests.get(stage);
}

/** "25/2": a score on a quick-fill button. */
const scoreText = (t) => (Number.isInteger(t.lost) ? `${t.turns}/${t.lost}` : `${t.turns}`);

/**
 * A stage's or lane's target: "Win in [turns] turns, losing at most [ships]
 * ships", always shown, blank turns meaning no target. Buttons under it fill it
 * from the link's score, the best posted score and the bots' wins on `near`'s
 * setup (greyed when there is none; best posted and slowest bot only shown when
 * there is one), and a note says what it comes to, warning, never blocking, when it
 * is stricter than every score known: no one has shown it can be met.
 * `onChange` runs after each edit; `show` redraws it from `holder.target`.
 */
function targetControl(holder, near, idFor, noneText, onChange) {
  const turnsId = `${idFor}-turns`;
  const lostId = `${idFor}-lost`;
  const turns = el("input", { id: turnsId, type: "number", min: "1", max: "999", placeholder: "any",
                              "aria-label": "Turns" });
  const lost = el("input", { id: lostId, type: "number", min: "0", max: "9999", placeholder: "any",
                             "aria-label": "Ships lost" });
  const fromLink = el("button", { class: "drop", type: "button" });
  const posted = el("button", { class: "drop", type: "button" });
  const fastest = el("button", { class: "drop", type: "button" });
  const slowest = el("button", { class: "drop", type: "button" });
  const note = el("p", { class: "stage-summary" });
  const row = el("div", { class: "target" }, [
    el("div", { class: "row lane-turns" }, [
      el("label", { for: turnsId, text: "Win in" }), turns,
      el("label", { for: lostId, text: "turns, losing at most" }), lost, el("span", { text: "ships" }),
    ]),
    el("div", { class: "row target-fills" }, [fromLink, posted, fastest, slowest]),
    note,
  ]);
  let bots;   // boardScores' answer, null when the board couldn't say, undefined while asking

  const known = () => [linkTarget(readings.get(near)?.score), bots?.posted, bots?.fastest, bots?.slowest]
    .filter(Boolean);
  const showNote = () => {
    clear(note);
    if (!holder.target) {
      note.append(Number.parseInt(lost.value, 10) >= 0 ? "Give it turns too: blank turns is no target." : noneText);
      return;
    }
    note.append(holder.target.beat
      ? `${targetText(holder.target)}: strictly better clears it. Editing the numbers makes it an ordinary target.`
      : targetText(holder.target));
    const scores = known();
    if (!scores.length) {
      note.append(el("span", { class: "warn", text: " · No score on this setup to check it against." }));
    } else if (!scores.some((score) => meetsTarget(score, holder.target))) {
      note.append(el("span", { class: "warn",
        text: " · Stricter than every score above: no one has shown it can be met, so it may be impossible." }));
    }
  };
  const showFills = () => {
    const link = linkTarget(readings.get(near)?.score);
    fromLink.textContent = link ? `From link: ${scoreText(link)}` : "From link";
    fromLink.disabled = !link;
    fromLink.title = link ? "" : "Paste a Challenge a friend link to use its score";
    const asking = bots === undefined && near?.settings && configured();
    fastest.textContent = bots?.fastest ? `Fastest bot: ${scoreText(bots.fastest)} (${bots.fastest.bot})`
                                        : asking ? "Fastest bot: asking…" : "Fastest bot";
    fastest.disabled = !bots?.fastest;
    fastest.title = bots?.fastest || asking ? "" : bots === null ? "Couldn't ask the board for its bot scores"
      : bots ? "No bot has won this setup" : "No bot scores without a map link and the board";
    posted.hidden = !bots?.posted;
    if (bots?.posted) {
      posted.textContent = `Best posted: ${scoreText(bots.posted)}${bots.posted.by ? ` (${bots.posted.by})` : ""}`;
    }
    slowest.hidden = !bots?.slowest;
    if (bots?.slowest) slowest.textContent = `Slowest bot: ${scoreText(bots.slowest)} (${bots.slowest.bot})`;
  };
  const fill = (target) => {
    holder.target = { turns: target.turns, ...(Number.isInteger(target.lost) ? { lost: target.lost } : {}) };
    show();
    onChange();
  };
  fromLink.addEventListener("click", () => fill(linkTarget(readings.get(near)?.score)));
  posted.addEventListener("click", () => bots?.posted && fill(bots.posted));
  fastest.addEventListener("click", () => bots?.fastest && fill(bots.fastest));
  slowest.addEventListener("click", () => bots?.slowest && fill(bots.slowest));
  const readTyped = () => {
    const n = Number.parseInt(turns.value, 10);
    const m = Number.parseInt(lost.value, 10);
    holder.target = n >= 1 ? { turns: n, ...(m >= 0 ? { lost: m } : {}) } : null;
    showNote();
    onChange();
  };
  turns.addEventListener("input", readTyped);
  lost.addEventListener("input", readTyped);

  const show = () => {
    turns.value = holder.target?.turns ?? "";
    lost.value = Number.isInteger(holder.target?.lost) ? holder.target.lost : "";
    bots = undefined;
    showFills();
    showNote();
    boardScores(near).then((found) => {
      bots = found;
      showFills();
      showNote();
    });
  };
  show();
  return { row, show };
}

/** The options of the lane `lane`, shown under its near stage's card. */
function renderLane(lane) {
  const near = stageAt(lane.from);
  const far = stageAt(lane.to);
  const idFor = (name) => `lane-${lane.from}-${lane.to}-${name}`;
  const { row: gate } = targetControl(lane, near, idFor("aim"), `No gate: any clear of ${nameOf(near)} opens it.`,
    () => {
      if (!lane.target) lane.hidden = hidden.checked = false;
      hidden.disabled = !lane.target;
      changed();
      showLabel();
    });
  const label = el("p", { class: "lane-label" });
  const showLabel = () => {
    const body = buildBody(draft);
    const index = (key) => draft.stages.findIndex((s) => s.key === key);
    const named = laneLabel(graphOf(body), index(lane.from), index(lane.to));
    label.textContent = named ? `${named}: ${LABEL_WHY[named]}` : "";
  };

  const twoWay = el("input", { id: idFor("two-way"), type: "checkbox" });
  twoWay.checked = lane.two_way;
  twoWay.addEventListener("change", () => {
    lane.two_way = twoWay.checked;
    changed();
    render(idFor("two-way"));
  });
  const hidden = el("input", { id: idFor("hidden"), type: "checkbox" });
  hidden.checked = lane.hidden;
  hidden.disabled = !lane.target;
  hidden.addEventListener("change", () => {
    lane.hidden = hidden.checked;
    changed();
    render(idFor("hidden"));
  });

  const remove = el("button", { class: "drop", type: "button", text: "Remove lane" });
  remove.addEventListener("click", () => {
    dropLane(draft, lane);
    openLane = null;
    changed();
    render(`s${lane.from}-to-${lane.to}`);
  });
  const close = el("button", { class: "drop", type: "button", text: "Done" });
  close.addEventListener("click", () => {
    openLane = null;
    render(`s${lane.from}-to-${lane.to}`);
  });
  showLabel();

  return el("fieldset", { class: "lane-options" }, [
    el("legend", { text: `Lane: ${nameOf(near)} ${lane.two_way ? "↔" : "→"} ${nameOf(far)}` }),
    el("label", { class: "check" }, [twoWay, "Two-way (either end opens the other)"]),
    el("span", { class: "label", text: "Gate (what opens it)" }), gate,
    el("label", { class: "check" }, [hidden, "Hidden until met (needs a gate)"]),
    label,
    el("div", { class: "row" }, [close, remove]),
  ]);
}

// What each derived label means, beside a lane's options.
const LABEL_WHY = {
  Bonus: "the stage it opens leads to no stage that ends the gauntlet.",
  Shortcut: "it skips past stages on the way to the end.",
  Gated: "it opens on a target, not a plain win.",
};

/** The "Leads to" chips of `stage`: one per lane out, each opening its options. */
function renderChips(stage) {
  const chips = (draft.lanes || []).filter((l) => l.from === stage.key || (l.two_way && l.to === stage.key)).map((lane) => {
    const other = stageAt(lane.from === stage.key ? lane.to : lane.from);
    const text = `${nameOf(other)}${lane.two_way ? " ↔" : ""}${lane.target ? ` · ${shortTarget(lane.target)}` : ""}`
      + `${lane.hidden && lane.target ? " · hidden" : ""}`;
    const chip = el("button", {
      id: `s${lane.from}-to-${lane.to}`, type: "button", text,
      class: `chip lit${lane === openLane ? " open" : ""}`, "aria-expanded": String(lane === openLane),
    });
    chip.addEventListener("click", () => {
      openLane = openLane === lane ? null : lane;
      render(chip.id);
    });
    const glow = (on) => () => lightLane(on ? [stage.key, other.key] : null);
    chip.addEventListener("mouseenter", glow(true));
    chip.addEventListener("focus", glow(true));
    chip.addEventListener("mouseleave", glow(false));
    chip.addEventListener("blur", glow(false));
    return chip;
  });
  if (!chips.length) chips.push(el("span", { class: "hint", text: "None yet: + Add link draws one." }));
  return el("div", { class: "chips" }, chips);
}

/** "From Stage 1 · from Stage 3 (two-way)": the lanes into a stage, so either end explains one. */
function fromNote(stage) {
  const parts = parentsOf(draft, stage.key).map((key) => {
    const lane = laneFrom(draft, key, stage.key);
    return `from ${nameOf(stageAt(key))}${lane?.target ? ` (${shortTarget(lane.target)})` : ""}`;
  });
  return el("p", { class: "stage-from", text: parts.length ? `Reached ${parts.join(" · ")}` : "" });
}

function renderStage(stage, i, dupes) {
  const id = (name) => `s${stage.key}-${name}`;
  const place = draftNames(draft).get(stage.key);
  const summary = el("p");
  renderSummary(summary, stage, dupes.has(i));

  const join = el("button", { id: "add-link", class: `drop link${linking ? " on" : ""}`, type: "button",
                              "aria-pressed": String(linking), text: linking ? "Cancel link" : "+ Add link",
                              title: "Then tap a stage to join this one to it, or a dashed box for a new stage" });
  join.disabled = view !== "author";
  join.addEventListener("click", () => {
    linking = !linking;
    openLane = null;
    said = "";
    render("add-link");
  });
  const remove = el("button", { id: id("remove"), class: "drop", type: "button", text: "Delete stage" });
  remove.disabled = draft.stages.length === 1;
  remove.addEventListener("click", () => {
    const gone = nameOf(stage);
    const at = draft.stages.indexOf(stage);
    removeStage(draft, stage.key);
    if (openLane && !draft.lanes.includes(openLane)) openLane = null;
    picked = (draft.stages[at] || draft.stages[at - 1]).key;
    linking = false;
    said = `Deleted ${gone} and its lanes.`;
    changed();
    render();
  });

  const { row: aim, show: showAim } = targetControl(stage, stage, id("aim"),
                                                    "No target: any win opens the way on.", changed);
  const [link] = textField(id("link"), stage.link, "https://…/play/#eNo…", 0, (v) => { stage.link = v; }, true);
  link.addEventListener("input", async () => {
    await readLink(stage);
    renderSummary(summary, stage, duplicates().has(draft.stages.indexOf(stage)));
    showAim();
    showProblem();
    keep();
    drawDiagram();
  });
  const [title, titleCount] = textField(id("title"), stage.title, `Stage ${place}`, LIMITS.title,
                                        (v) => { stage.title = v; });
  // The chips, notes and the graph name this stage: redraw them once it is typed.
  title.addEventListener("change", () => render());
  const [hint, hintCount] = textField(id("hint"), stage.hint, "One line, shown beside the board",
                                      LIMITS.hint, (v) => { stage.hint = v; });

  const paragraphs = stage.briefing.flatMap((text, p) => {
    const [field, count] = textField(id(`brief-${p}`), text, p === 0 ? "What this stage is about" : "",
                                     LIMITS.briefingLine, (v) => { stage.briefing[p] = v; }, true);
    return [field, count];
  });
  const more = el("button", { class: "drop", type: "button", text: "+ Paragraph" });
  more.disabled = stage.briefing.length >= LIMITS.briefingLines;
  more.addEventListener("click", () => {
    stage.briefing.push("");
    changed();
    render(id(`brief-${stage.briefing.length - 1}`));
  });
  const fewer = el("button", { class: "drop", type: "button", text: "− Paragraph" });
  fewer.disabled = stage.briefing.length <= 1;
  fewer.addEventListener("click", () => {
    stage.briefing.pop();
    changed();
    render();
  });

  const opens = el("input", { id: id("start"), type: "checkbox" });
  opens.checked = (draft.start || []).includes(stage.key);
  opens.addEventListener("change", () => {
    setStart(draft, stage.key, opens.checked);
    changed();
    render(id("start"));
  });
  const lane = openLane && parentsOrSelf(openLane, stage.key) && draft.lanes.includes(openLane) ? openLane : null;

  // Ticked by default for a dead end (no lane out, not a goal); a tick either
  // way sticks, so adding a lane out later doesn't untick one the author set.
  const life = el("input", { id: id("life"), type: "checkbox" });
  life.checked = typeof stage.bonus_life === "boolean" ? stage.bonus_life : grantsLife(graphOf(buildBody(draft)), i);
  life.addEventListener("change", () => {
    stage.bonus_life = life.checked;
    changed();
    render(id("life"));
  });

  return el("fieldset", { class: "stage", id: `card-${stage.key}` }, [
    el("legend", { text: `Stage ${place}${tidy(stage.title) ? `: ${tidy(stage.title)}` : ""}` }),
    el("div", { class: "row stage-tools" }, [join, remove]),
    el("label", { for: id("link"), text: "Map link" }), link, summary,
    el("label", { for: id("title"), text: "Title" }), title, titleCount,
    el("label", { for: id("brief-0"), text: "Briefing (shown on a card as the stage starts)" }),
    ...paragraphs,
    el("div", { class: "row" }, [more, fewer]),
    el("label", { for: id("hint"), text: "Hint" }), hint, hintCount,
    el("span", { class: "label", text: "Target (to open the stages beyond)" }), aim,
    el("label", { class: "check" }, [opens, "Open from the start"]),
    el("label", { class: "check" }, [life, "Grants a bonus life (♥) the first time a run wins it"]),
    el("span", { class: "label", text: "Leads to (tap one for its options)" }),
    renderChips(stage),
    lane ? renderLane(lane) : null,
    fromNote(stage),
  ]);
}

/** Does `lane` run out of `key`: its near stage, or the far one of a two-way lane? */
function parentsOrSelf(lane, key) {
  return lane.from === key || (lane.two_way && lane.to === key);
}

// -- the graph -----------------------------------------------------------------
// Drawn from the body the draft builds, so a stage is placed where the game's
// `gauntlet.layout` will place it, by the game's own drawer (stagegraph.mjs,
// the one its gauntlet list uses), and edited where it is drawn.

let diagram = null;   // what drawGraph answered, to light a lane with
const nodeKey = (key) => `stage-${key}`;
const keyOfNode = (node) => Number(node.slice("stage-".length));
// The arrows round the selected stage, and where each sits beside its box.
const ARROWS = { left: "←", right: "→", up: "↑", down: "↓" };
const ARROW_PX = 28;
const ARROW_GAP = 8;

/** The draft as the drawer takes it: stages at their cells, the lanes between. */
function diagramView() {
  const graph = graphOf(buildBody(draft));
  const cells = layout(graph);
  const player = view === "player";
  const hiddenOnly = hiddenStages(graph);
  const unseen = player ? hiddenOnly : new Set();
  const reached = reachable(graph);
  const plain = depths(graph);
  const ends = goals(graph);
  const keyOf = (sid) => nodeKey(draft.stages[sid].key);
  const names = draftNames(draft);
  const places = draftNames(draft, graph.ids.filter((sid) => !unseen.has(sid)).map((sid) => draft.stages[sid].key));
  const words = { start: "open from the start", goal: "ends the gauntlet", stranded: "no start reaches it",
                  "off-path": "reached only through a gate", hidden: "hidden until met",
                  life: "grants a bonus life" };
  const nodes = graph.ids.filter((sid) => !unseen.has(sid)).map((sid) => {
    const stage = draft.stages[sid];
    const marks = [];
    if (graph.start.includes(sid)) marks.push("start");
    if (ends.has(sid)) marks.push("goal");
    if (!reached.has(sid)) marks.push("stranded");
    else if (!plain.has(sid)) marks.push("off-path");
    if (hiddenOnly.has(sid)) marks.push("hidden");
    if (grantsLife(graph, sid)) marks.push("life");
    const name = stageName(draft, stage, names);
    if (stage.key === picked) marks.push(linking ? "source" : "picked");
    const [col, row] = cells.get(sid);
    return { key: keyOf(sid), col, row, place: places.get(stage.key), title: name, open: true,
             marks, tip: [name, ...marks.filter((m) => words[m]).map((m) => words[m])].join(" · ") };
  });
  const lanes = [];
  for (const [a, b] of graph.lanes) {
    const pair = `${a},${b}`;
    if (player && (graph.hidden.has(pair) || unseen.has(a) || unseen.has(b))) continue;
    const gate = graph.gates.get(pair);
    // A gated lane that is its far stage's only way in: players reach that stage
    // only by meeting the target (amber, a warning, not a block).
    const only = Boolean(gate) && ways(graph).every((w) => w.to !== b || graph.gates.has(w.lane));
    lanes.push({ from: keyOf(a), to: keyOf(b), gated: Boolean(gate), short: gate ? shortTarget(gate) : "",
                 hidden: graph.hidden.has(pair), two_way: graph.twoWay.has(pair), warn: only });
  }
  const stranded = graph.ids.filter((sid) => !reached.has(sid)).map((sid) => stageName(draft, draft.stages[sid], names));
  const columns = Math.max(1, ...[...cells.values()].map(([c]) => c + 1));
  const rows = (c) => [...cells.values()].filter(([cc]) => cc === c).length;
  return { nodes, lanes, stranded, columns, rows };
}

/**
 * The dashed box at the foot of every column, and one past the last: each adds
 * a stage there (`planAdd`), joined from the selected stage when it can be, and
 * in link mode with a lane from it. Always drawn, so a link starting never
 * resizes the graph; one the link can't reach is greyed.
 */
function ghosts(shape) {
  return Array.from({ length: shape.columns + 1 }, (_, col) => {
    const plan = planAdd(draft, col, { from: picked, link: linking });
    const name = plan ? stageName(plan.draft, plan.stage) : "";
    const box = el("button", {
      type: "button", class: "sg-ghost",
      title: plan ? `Add ${name}, ${plan.how}` : linking ? "A lane from the selected stage can't reach this column"
        : "No stage can be added here",
    }, [el("span", { class: "sg-title", text: plan ? `+ ${name}` : "+ Stage" }),
        el("span", { class: "sg-status", text: plan ? plan.how : linking ? "out of the link's reach" : "" })]);
    box.disabled = !plan;
    box.addEventListener("click", () => {
      const fresh = planAdd(draft, col, { from: picked, link: linking });
      if (!fresh) return;
      draft = fresh.draft;
      picked = fresh.stage.key;
      linking = false;
      openLane = null;
      said = `Added ${stageName(draft, fresh.stage)}, ${fresh.how}.`;
      changed();
      render(`s${picked}-link`);
    });
    return { col, row: shape.rows(col), el: box };
  });
}

/** The move arrows round the selected stage, where a move lands (`planMove`), set once the boxes are placed. */
function arrows() {
  if (view !== "author" || linking || picked === null) return () => {};
  const moves = DIRECTIONS.map((dir) => [dir, planMove(draft, picked, dir)]).filter(([, plan]) => plan);
  return (rect, grid) => {
    for (const old of grid.querySelectorAll(".sg-move")) old.remove();
    const r = rect(nodeKey(picked));
    if (!r) return;
    const spot = {
      left: [r.x - ARROW_PX - ARROW_GAP, r.y + (r.h - ARROW_PX) / 2],
      right: [r.x + r.w + ARROW_GAP, r.y + (r.h - ARROW_PX) / 2],
      up: [r.x + (r.w - ARROW_PX) / 2, r.y - ARROW_PX - ARROW_GAP],
      down: [r.x + (r.w - ARROW_PX) / 2, r.y + r.h + ARROW_GAP],
    };
    for (const [dir, plan] of moves) {
      const button = el("button", { id: `move-${dir}`, type: "button", class: "sg-move", text: ARROWS[dir],
                                    title: plan.note, "aria-label": plan.note });
      button.style.left = `${spot[dir][0]}px`;
      button.style.top = `${spot[dir][1]}px`;
      button.addEventListener("click", () => {
        draft = plan.draft;
        openLane = null;
        said = plan.note;
        changed();
        render();
        (document.getElementById(`move-${dir}`) || diagram?.node(nodeKey(picked)))?.focus({ preventScroll: true });
      });
      grid.append(button);
    }
  };
}

function drawDiagram() {
  const box = $("diagram");
  if (!box) return;
  clearTimeout(redraw);
  if (picked === null || !stageAt(picked)) picked = draft.stages[0]?.key ?? null;
  const shape = diagramView();
  const editing = view === "author";
  const scroll = box.scrollLeft;
  diagram = drawGraph(box, shape, {
    label: `The gauntlet's shape: ${shape.nodes.length} stages in ${shape.columns} columns`,
    onPress: (node) => pressStage(keyOfNode(node)),
    extras: editing ? ghosts(shape) : [],
    onLane: editing ? (from, to) => pressLane(keyOfNode(from), keyOfNode(to)) : null,
    after: arrows(),
  });
  box.scrollLeft = scroll;
  box.classList.toggle("linking", linking);
  $("diagram-note").textContent = shape.stranded.length
    ? `No start reaches ${shape.stranded.join(", ")}: link it from a stage, or tick it as open from the start.`
    : "";
  const hint = $("graph-hint");
  hint.classList.toggle("linking", linking);
  hint.textContent = linking
    ? `Linking from ${nameOf(stageAt(picked))}: tap a stage to join them, or a dashed box for a new stage with a lane `
      + "from it. Esc or Cancel link stops."
    : said || (editing ? "Tap a stage to edit it, a lane for its options, or a dashed box to add a stage there."
                       : "The graph a new player sees: no hidden lanes, and no stage only they reach.");
}

/** A press on a stage: select it, or in link mode join the selected stage to it. */
function pressStage(key) {
  if (!linking) {
    picked = key;
    openLane = null;
    said = "";
    render();
    return;
  }
  linking = false;
  if (key !== picked) {
    const there = laneFrom(draft, picked, key);
    const back = !there && laneBack(draft, picked, key);
    const lane = joinLane(draft, picked, key);
    const [a, b] = [nameOf(stageAt(picked)), nameOf(stageAt(key))];
    said = there ? `${a} already leads to ${b}: its options are below.`
      : back ? `The lane between ${a} and ${b} now runs both ways.` : `Joined ${a} → ${b}.`;
    openLane = there || back ? lane : null;
    changed();
  } else {
    said = "";
  }
  render();
  if (openLane) showLane();
}

/** A press on a lane: select its near stage and open the lane's options. */
function pressLane(from, to) {
  const lane = draft.lanes.find((l) => l.from === from && l.to === to);
  if (!lane) return;
  linking = false;
  picked = from;
  openLane = lane;
  said = "";
  render();
  showLane();
}

/** Bring the open lane's options into view. */
function showLane() {
  document.querySelector(".lane-options")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/** Light the lane between two stage keys (either way), or none. */
function lightLane(pair) {
  diagram?.light(pair ? nodeKey(pair[0]) : null, pair ? nodeKey(pair[1]) : null);
}

function showView(which) {
  view = which;
  linking = false;
  said = "";
  for (const w of ["author", "player"]) {
    const tab = $(`view-${w}`);
    tab.classList.toggle("lit", w === view);
    tab.setAttribute("aria-pressed", String(w === view));
  }
  render();
}

/** Redraw the form (the selected stage's card and the graph), then put the focus back on `focusId`. */
function render(focusId = null) {
  if (picked === null || !stageAt(picked)) picked = draft.stages[0]?.key ?? null;
  if (openLane && !draft.lanes.includes(openLane)) openLane = null;
  const dupes = duplicates();
  const i = draft.stages.findIndex((s) => s.key === picked);
  clear($("stages")).append(...(i >= 0 ? [renderStage(draft.stages[i], i, dupes)] : []));
  $("g-name").value = draft.name;
  counter($("g-name-count"), draft.name, LIMITS.name);
  $("g-by").value = draft.by_name;
  $("g-listing").checked = draft.wants_listing;
  $("g-lives").value = String(draft.lives ?? 1);
  drawDiagram();
  showProblem();
  if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
}

async function save(event) {
  event.preventDefault();
  if (draftProblem(draft)) return;
  $("save").disabled = true;
  say("Saving…");
  const gauntlet = buildBody(draft);
  try {
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gauntlet, by_name: draft.by_name, wants_listing: draft.wants_listing }),
    });
    const answer = await response.json().catch(() => ({}));
    if (!response.ok || !GAUNTLET_ID.test(answer.id || "")) {
      say(answer.error ? `Not saved: ${answer.error}` : `Not saved (the board answered ${response.status}).`, "error");
      return;
    }
    saved = { id: answer.id, body: { id: answer.id, ...checkBody(gauntlet) } };
    const link = gameLink(GAME_URL, saved.id);
    $("g-link").value = link;
    $("open").href = link;
    $("result").hidden = false;
    say("Saved.");
  } catch {
    say("Couldn't reach the board to save. Your draft is kept here; try again.", "error");
  } finally {
    showProblem();
  }
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText($("g-link").value);
    say("Link copied.");
  } catch {
    $("g-link").select();
    say("Select the link and copy it.");
  }
}

function download() {
  if (!saved) return;
  const blob = new Blob([JSON.stringify(saved.body, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: fileName(saved.body, saved.id) });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** `#<id>`: a stored gauntlet to open as a draft, or null. */
function requestedId() {
  const id = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  return GAUNTLET_ID.test(id) ? id : null;
}

/** Show one way of making a gauntlet (generate, or build from links) and remember it. */
function showWay(way) {
  for (const w of WAYS) {
    $(`way-${w}`).hidden = w !== way;
    const tab = $(`way-${w}-tab`);
    tab.classList.toggle("ghost", w !== way);
    tab.setAttribute("aria-pressed", String(w === way));
  }
  try {
    localStorage.setItem(WAY_KEY, way);
  } catch {
    // storage blocked: the page opens on Generate next time
  }
}

function storedWay() {
  try {
    const way = localStorage.getItem(WAY_KEY);
    return WAYS.includes(way) ? way : "gen";
  } catch {
    return "gen";
  }
}

async function load() {
  const id = requestedId();
  if (id) {
    showWay("build");   // a stored gauntlet opens as a draft to remix
    say("Opening…");
    try {
      const response = await fetch(`${ENDPOINT}?id=${id}`);
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || String(response.status));
      draft = await draftFromBody(answer.gauntlet, linkFor);
      openLane = null;
      picked = null;
      history.replaceState(null, "", location.pathname);
      keep();
      say(`Opened ${tidy(answer.gauntlet.name)}. Saving your edits makes a new link.`);
    } catch (err) {
      say(`Couldn't open that gauntlet (${err.message}).`, "error");
    }
  } else {
    let stored = null;
    try {
      stored = readDraft(localStorage.getItem(DRAFT_KEY) || "");
    } catch {
      // storage blocked: start empty
    }
    draft = stored || freshDraft();
  }
  await Promise.all(draft.stages.map(readLink));
  render();
}

/**
 * Open a `.json` gauntlet file as a draft (a desktop file, or a generated
 * gauntlet the board never saw), read as a stored one is. The author's name
 * and listing choice stay as they are.
 */
async function openFile() {
  const file = $("open-file").files?.[0];
  $("open-file").value = "";
  if (!file) return;
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    say(`${file.name} isn't a gauntlet file (it doesn't read as JSON).`, "error");
    return;
  }
  const opened = await draftFromBody(data, linkFor);
  if (!opened.stages.some((stage) => stage.settings)) {
    say(`${file.name} has no stages with a setup.`, "error");
    return;
  }
  if (!confirm("Replace this draft with the file?")) return;
  draft = { ...opened, by_name: draft.by_name, wants_listing: draft.wants_listing };
  openLane = null;
  picked = null;
  changed();
  await Promise.all(draft.stages.map(readLink));
  render();
  say(`Opened ${file.name}. Saving it here gives it a link.`);
}

function main() {
  mountNav();
  for (const w of WAYS) $(`way-${w}-tab`).addEventListener("click", () => showWay(w));
  showWay(requestedId() ? "build" : storedWay());
  $("g-name").addEventListener("input", () => {
    draft.name = $("g-name").value;
    counter($("g-name-count"), draft.name, LIMITS.name);
    changed();
  });
  $("g-by").addEventListener("input", () => { draft.by_name = $("g-by").value; changed(); });
  $("g-listing").addEventListener("change", () => { draft.wants_listing = $("g-listing").checked; changed(); });
  $("g-lives").addEventListener("input", () => {
    const n = Number($("g-lives").value);
    draft.lives = Number.isInteger(n) ? Math.max(1, Math.min(LIMITS.lives, n)) : 1;
    changed();
    drawDiagram();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !linking) return;
    linking = false;
    render("add-link");
  });
  for (const w of ["author", "player"]) $(`view-${w}`).addEventListener("click", () => showView(w));
  $("open-file").addEventListener("change", openFile);
  $("form").addEventListener("submit", save);
  $("copy").addEventListener("click", copyLink);
  $("download").addEventListener("click", download);
  $("reset").addEventListener("click", () => {
    if (!confirm("Clear this draft and start over?")) return;
    draft = freshDraft();
    openLane = null;
    picked = null;
    linking = false;
    said = "";
    changed();
    render();
  });
  // Opening `#<id>` on a page already showing loads it too: a hash change is
  // not a page load.
  window.addEventListener("hashchange", () => { if (requestedId()) load(); });
  load();
}

if (typeof document !== "undefined") main();
