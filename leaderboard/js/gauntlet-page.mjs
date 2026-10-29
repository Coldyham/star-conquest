// The gauntlet builder (gauntlet.html): paste a game link per stage, write its
// briefing and hint, save, and share the short link that opens it in the game.
//
// The draft lives in localStorage under DRAFT_KEY as the form was typed, so a
// reload loses nothing; once saved, the stored row is the record and the draft
// is only a convenience. Opening the page as `gauntlet.html#<id>` loads a stored
// gauntlet as a new draft: rows never change, so editing one and saving makes a
// new id. Everything that decides anything is in gauntlet-build.mjs, which
// /api/gauntlet runs too; this file is the DOM around it.
//
// A stage's target (docs/design/gauntlet.md, "Stage targets") is the pasted
// challenge link's own score, or the best bot's win on the setup read off
// `bot_scores`. The bot's is read when picked and again on Save, then fixed into
// the file: the game never looks it up.

import { configured, inList, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { deflate } from "./deflate-browser.mjs";
import { clear, el, mapSummary } from "./format.mjs";
import {
  GAUNTLET_ID, LIMITS, botTarget, buildBody, checkBody, draftFromBody, draftProblem, emptyDraft, emptyStage,
  fileName, gameLink, linkTarget, moveStage, readDraft, setupProblem, stageSetup, targetScore, targetText,
  textLength, tidy,
} from "./gauntlet-build.mjs";
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
// What the stage's target line says, by stage object, until the next choice.
const aimNotes = new WeakMap();
let saved = null;   // {id, body} once saved, until the draft changes again

const $ = (id) => document.getElementById(id);

function keep() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // storage full or blocked: the page still works, it just won't remember
  }
}

function changed() {
  keep();
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
 * The setup's `bot_scores` rows. A challenge link carries the game's own key;
 * a Get Link carries none, so the setup is also matched to its `games` row by
 * jsonb equality (contains and is contained by), as `campaign_games` matches.
 */
async function botRows(stage) {
  const keys = new Set([readings.get(stage)?.gameKey].filter(Boolean));
  const json = encodeURIComponent(JSON.stringify(stage.settings));
  const games = await select(`games?select=game_key&settings_json=cs.${json}&settings_json=cd.${json}`)
    .catch(() => []);
  for (const game of games) keys.add(game.game_key);
  if (!keys.size) return [];
  return select(`bot_scores?select=bot,won,turns,lost&game_key=${inList(keys)}`);
}

/**
 * Set `stage.target` from how it is aimed, and say what that came to. For the
 * best bot this asks the board; an answer that arrives after the stage was
 * aimed elsewhere is dropped.
 */
async function applyAim(stage) {
  const reading = readings.get(stage);
  if (stage.aim === "link") {
    stage.target = linkTarget(reading?.score);
    aimNotes.set(stage, stage.target ? `${targetText(stage.target)}: a win at or under your score clears it.`
                                     : "This link carries no score: paste a Challenge a friend link.");
    return;
  }
  if (stage.aim !== "bot" || !stage.settings) {
    stage.target = null;
    aimNotes.set(stage, "");
    return;
  }
  let rows;
  try {
    rows = await botRows(stage);
  } catch {
    aimNotes.set(stage, stage.target ? `Couldn't ask the board; keeping ${targetText(stage.target)}.`
                                     : "Couldn't ask the board for its bot scores. Try again.");
    return;
  }
  if (stage.aim !== "bot") return;
  const found = botTarget(rows);
  stage.target = found.target || null;
  aimNotes.set(stage, found.target
    ? `${targetText(found.target)}. Strictly better clears it. Read again when you save.`
    : found.none === "no-win"
      ? "No bot won this setup, so any win clears it."
      : "No bot scores for this setup yet: post a game on it and they arrive within the hour.");
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
    changed();
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

function renderStage(stage, i, dupes) {
  const n = draft.stages.length;
  const id = (name) => `s${i}-${name}`;
  const summary = el("p");
  renderSummary(summary, stage, dupes.has(i));

  const move = (step) => {
    draft.stages = moveStage(draft.stages, i, step);
    changed();
    render();
  };
  const tools = el("div", { class: "row stage-tools" }, [
    el("button", { class: "drop", type: "button", "aria-label": "Move up", text: "↑" }),
    el("button", { class: "drop", type: "button", "aria-label": "Move down", text: "↓" }),
    el("button", { class: "drop", type: "button", text: "Remove" }),
  ]);
  const [up, down, remove] = tools.children;
  up.disabled = i === 0;
  down.disabled = i === n - 1;
  remove.disabled = n === 1;
  up.addEventListener("click", () => move(-1));
  down.addEventListener("click", () => move(1));
  remove.addEventListener("click", () => {
    draft.stages = draft.stages.filter((s) => s !== stage);
    changed();
    render();
  });

  const aim = el("select", { id: id("aim"), class: "plain" }, [
    el("option", { value: "none", text: "None: any win opens the way on" }),
    el("option", { value: "link", text: "Your score from this link" }),
    el("option", { value: "bot", text: "Beat the best bot on the board" }),
  ]);
  const aimNote = el("p", { class: "stage-summary" });
  const showAim = () => {
    const score = readings.get(stage)?.score;
    aim.options[1].text = score ? `Your score from this link: ${targetScore(linkTarget(score))}`
                                : "Your score from this link (paste a Challenge a friend link)";
    aim.options[1].disabled = !score && stage.aim !== "link";
    aim.options[2].disabled = !configured() && stage.aim !== "bot";
    aim.value = stage.aim;
    aimNote.textContent = aimNotes.get(stage) ?? (stage.target ? targetText(stage.target) : "");
  };
  showAim();
  aim.addEventListener("change", async () => {
    stage.aim = aim.value;
    aimNotes.set(stage, stage.aim === "bot" ? "Asking the board…" : "");
    showAim();
    changed();
    await applyAim(stage);
    showAim();
    changed();
  });

  const [link] = textField(id("link"), stage.link, "https://…/game/#eNo…", 0, (v) => { stage.link = v; }, true);
  link.addEventListener("input", async () => {
    await readLink(stage);
    renderSummary(summary, stage, duplicates().has(draft.stages.indexOf(stage)));
    if (stage.aim !== "none") await applyAim(stage);
    showAim();
    showProblem();
    keep();
  });
  const [title, titleCount] = textField(id("title"), stage.title, `Stage ${i + 1}`, LIMITS.title,
                                        (v) => { stage.title = v; });
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
    render();
    document.getElementById(id(`brief-${stage.briefing.length - 1}`))?.focus();
  });
  const fewer = el("button", { class: "drop", type: "button", text: "− Paragraph" });
  fewer.disabled = stage.briefing.length <= 1;
  fewer.addEventListener("click", () => {
    stage.briefing.pop();
    changed();
    render();
  });

  return el("fieldset", { class: "stage" }, [
    el("legend", { text: `Stage ${i + 1}` }),
    tools,
    el("label", { for: id("link"), text: "Game link" }), link, summary,
    el("label", { for: id("title"), text: "Title" }), title, titleCount,
    el("label", { for: id("brief-0"), text: "Briefing (shown on a card as the stage starts)" }),
    ...paragraphs,
    el("div", { class: "row" }, [more, fewer]),
    el("label", { for: id("hint"), text: "Hint" }), hint, hintCount,
    el("label", { for: id("aim"), text: "Target (to open the stages beyond)" }), aim, aimNote,
  ]);
}

function render() {
  const dupes = duplicates();
  clear($("stages")).append(...draft.stages.map((stage, i) => renderStage(stage, i, dupes)));
  $("add-stage").disabled = draft.stages.length >= LIMITS.stages;
  $("g-name").value = draft.name;
  counter($("g-name-count"), draft.name, LIMITS.name);
  $("g-by").value = draft.by_name;
  $("g-listing").checked = draft.wants_listing;
  showProblem();
}

async function save(event) {
  event.preventDefault();
  if (draftProblem(draft)) return;
  $("save").disabled = true;
  say("Saving…");
  // The best bot is read again now: the column moves, and the file fixes what
  // it says at the moment of saving.
  const aimed = draft.stages.filter((stage) => stage.aim === "bot");
  await Promise.all(aimed.map(applyAim));
  if (aimed.length) {
    keep();
    render();
    $("save").disabled = true;
  }
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
  $("add-stage").addEventListener("click", () => {
    draft.stages.push(emptyStage());
    changed();
    render();
    document.getElementById(`s${draft.stages.length - 1}-link`)?.focus();
  });
  $("form").addEventListener("submit", save);
  $("copy").addEventListener("click", copyLink);
  $("download").addEventListener("click", download);
  $("reset").addEventListener("click", () => {
    if (!confirm("Clear this draft and start over?")) return;
    draft = freshDraft();
    changed();
    render();
  });
  // Opening `#<id>` on a page already showing loads it too: a hash change is
  // not a page load.
  window.addEventListener("hashchange", () => { if (requestedId()) load(); });
  load();
}

if (typeof document !== "undefined") main();
