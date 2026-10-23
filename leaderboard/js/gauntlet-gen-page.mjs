// The generate form on gauntlet.html: read the form, build the spec
// (gauntlet-spec.mjs, which decides everything), and hand out the game link that
// generates it. The form is kept in localStorage between visits, as the
// builder's draft is.

import { GAME_URL } from "./config.mjs";
import { deflate } from "./deflate-browser.mjs";
import { el } from "./format.mjs";
import { OPPONENTS, buildSpec, emptyForm, gameCount, specLink } from "./gauntlet-spec.mjs";

const FORM_KEY = "sc_gauntlet_gen_form";
const $ = (id) => document.getElementById(id);

const FIELDS = {
  name: ["gen-name", "text"], stages: ["gen-stages", "text"], mode: ["gen-mode", "text"],
  playersMin: ["gen-players-min", "text"], playersMax: ["gen-players-max", "text"],
  systemsMin: ["gen-systems-min", "text"], systemsMax: ["gen-systems-max", "text"],
  speedGain: ["gen-speed", "text"], homeProduction: ["gen-prod", "text"], fog: ["gen-fog", "text"],
  roll: ["gen-roll", "check"], targets: ["gen-targets", "check"], slack: ["gen-slack", "text"],
  candidates: ["gen-candidates", "text"], seed: ["gen-seed", "text"],
};

function read() {
  const form = {};
  for (const [key, [id, kind]] of Object.entries(FIELDS)) {
    form[key] = kind === "check" ? $(id).checked : $(id).value;
  }
  form.opponents = OPPONENTS.filter((bot) => $(`gen-bot-${bot}`).checked);
  return form;
}

function write(form) {
  for (const [key, [id, kind]] of Object.entries(FIELDS)) {
    if (form[key] === undefined) continue;
    if (kind === "check") $(id).checked = form[key] === true;
    else $(id).value = String(form[key]);
  }
  for (const bot of OPPONENTS) $(`gen-bot-${bot}`).checked = (form.opponents || []).includes(bot);
}

function keep(form) {
  try {
    localStorage.setItem(FORM_KEY, JSON.stringify(form));
  } catch {
    // storage blocked: the form still works, it just won't remember
  }
}

function stored() {
  try {
    const data = JSON.parse(localStorage.getItem(FORM_KEY) || "null");
    return data && typeof data === "object" ? { ...emptyForm(), ...data } : null;
  } catch {
    return null;
  }
}

/** Say what is wrong, or how many games the game will play. */
function check() {
  const form = read();
  keep(form);
  $("gen-result").hidden = true;
  const spec = buildSpec(form, () => 0);
  const bad = typeof spec === "string";
  $("gen-problem").textContent = bad ? `To make the link: ${spec}.` : "";
  $("gen-cost").textContent = bad ? ""
    : `The game will play ${gameCount(spec)} test games: about a minute on a computer, longer on a phone.`;
  $("gen-make").disabled = bad;
  $("gen-slack").disabled = !form.targets;
}

async function make(event) {
  event.preventDefault();
  const spec = buildSpec(read());
  if (typeof spec === "string") return;
  const link = await specLink(GAME_URL, spec, deflate);
  $("gen-link").value = link;
  $("gen-open").href = link;
  $("gen-result").hidden = false;
}

async function copy() {
  try {
    await navigator.clipboard.writeText($("gen-link").value);
    $("gen-cost").textContent = "Link copied.";
  } catch {
    $("gen-link").select();
  }
}

function main() {
  const box = $("gen-opponents");
  for (const bot of OPPONENTS) {
    const input = el("input", { id: `gen-bot-${bot}`, type: "checkbox" });
    box.append(el("label", { class: "check", for: `gen-bot-${bot}` }, [input, ` ${bot}`]));
  }
  write(stored() || emptyForm());
  $("gen-form").addEventListener("input", check);
  $("gen-form").addEventListener("change", check);
  $("gen-form").addEventListener("submit", make);
  $("gen-copy").addEventListener("click", copy);
  check();
}

if (typeof document !== "undefined") main();
