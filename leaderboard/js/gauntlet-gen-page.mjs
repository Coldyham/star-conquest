// The generate form on gauntlet.html: read the form, build the spec
// (gauntlet-spec.mjs, which decides everything), and hand out the game link that
// generates it. The form is kept in localStorage between visits, as the
// builder's draft is.

import { GAME_URL } from "./config.mjs";
import { deflate } from "./deflate-browser.mjs";
import { el } from "./format.mjs";
import {
  KNOB_TABS, KNOBS, OPPONENTS, SWITCH_VALUES, SWITCHES, buildSpec, emptyForm, gameCount, isSet, knobText,
  specLink,
} from "./gauntlet-spec.mjs";
import { SETUP_DEFAULTS } from "./config.mjs";

const FORM_KEY = "sc_gauntlet_gen_form";
const $ = (id) => document.getElementById(id);

const FIELDS = {
  name: ["gen-name", "text"], stages: ["gen-stages", "text"], mode: ["gen-mode", "text"],
  playersMin: ["gen-players-min", "text"], playersMax: ["gen-players-max", "text"],
  systemsMin: ["gen-systems-min", "text"], systemsMax: ["gen-systems-max", "text"],
  fog: ["gen-fog", "text"],
  roll: ["gen-roll", "check"], targets: ["gen-targets", "text"], unwonLast: ["gen-unwon-last", "check"], slack: ["gen-slack", "text"],
  candidates: ["gen-candidates", "text"], seed: ["gen-seed", "text"],
};

function read() {
  const form = {};
  for (const [key, [id, kind]] of Object.entries(FIELDS)) {
    form[key] = kind === "check" ? $(id).checked : $(id).value;
  }
  form.opponents = OPPONENTS.filter((bot) => $(`gen-bot-${bot}`).checked);
  form.knobs = Object.fromEntries(Object.keys(KNOBS).map((attr) => [attr, pins(attr).map((p) => Number(p.value))]));
  form.switches = Object.fromEntries(SWITCHES.map((attr) => [attr, $(`gen-sw-${attr}`).value]));
  return form;
}

const pins = (attr) => [$(`gen-k-${attr}-lo`), $(`gen-k-${attr}-hi`)];

function write(form) {
  for (const [key, [id, kind]] of Object.entries(FIELDS)) {
    if (form[key] === undefined) continue;
    if (kind === "check") $(id).checked = form[key] === true;
    else $(id).value = key === "targets" && form[key] === true ? "slowest" : String(form[key]);
  }
  for (const bot of OPPONENTS) $(`gen-bot-${bot}`).checked = (form.opponents || []).includes(bot);
  for (const attr of Object.keys(KNOBS)) {
    const pair = (form.knobs || {})[attr];
    const [lo, hi] = Array.isArray(pair) && pair.length === 2 ? pair : [SETUP_DEFAULTS[attr], SETUP_DEFAULTS[attr]];
    const [a, b] = pins(attr);
    a.value = String(Math.min(lo, hi));
    b.value = String(Math.max(lo, hi));
  }
  for (const attr of SWITCHES) {
    const value = (form.switches || {})[attr];
    $(`gen-sw-${attr}`).value = SWITCH_VALUES.includes(value) ? value : "off";
  }
}

// --------------------------------------------------------------------------- //
// The sliders: a tab and a heading each fold away, and say how many they set.
// A knob is two range inputs over one track; a pin dragged past the other
// pushes it along, so the pair never crosses.
// --------------------------------------------------------------------------- //
function slider(k) {
  const lo = el("input", { id: `gen-k-${k.attr}-lo`, type: "range", min: k.lo, max: k.hi, step: k.step,
    "aria-label": `${k.label}: least` });
  const hi = el("input", { id: `gen-k-${k.attr}-hi`, type: "range", min: k.lo, max: k.hi, step: k.step,
    "aria-label": `${k.label}: most` });
  const fill = el("span", { class: "fill" });
  const readout = el("span", { id: `gen-k-${k.attr}-text`, class: "readout" });
  const push = (moved, other, up) => {
    const a = Number(moved.value), b = Number(other.value);
    if (up ? a > b : a < b) other.value = moved.value;
  };
  lo.addEventListener("input", () => push(lo, hi, true));
  hi.addEventListener("input", () => push(hi, lo, false));
  return el("div", { class: "knob", id: `gen-k-${k.attr}` }, [
    el("div", { class: "knob-head" }, [el("span", { text: k.label }), readout]),
    el("div", { class: "dual" }, [el("span", { class: "track" }), fill, lo, hi]),
  ]);
}

function switchRow(s) {
  const select = el("select", { id: `gen-sw-${s.attr}` }, [
    el("option", { value: "off", text: "Off (default)" }),
    el("option", { value: "on", text: "On" }),
    el("option", { value: "either", text: "Either" }),
  ]);
  return el("div", { class: "knob" }, [el("label", { for: `gen-sw-${s.attr}`, text: s.label }), select]);
}

function buildKnobs() {
  const box = $("gen-knobs");
  for (const t of KNOB_TABS) {
    const groups = t.groups.map((g) => {
      const reset = el("button", { type: "button", class: "mini", text: "Reset" });
      reset.addEventListener("click", () => {
        for (const k of g.knobs) for (const p of pins(k.attr)) p.value = String(SETUP_DEFAULTS[k.attr]);
        for (const s of g.switches) $(`gen-sw-${s.attr}`).value = "off";
        check();
      });
      return el("details", { class: "gen-group", "data-heading": g.heading }, [
        el("summary", {}, [g.heading, el("span", { class: "count" })]),
        ...g.knobs.map(slider), ...g.switches.map(switchRow),
        g.heading === "Visibility" ? el("p", { class: "hint fog-note", text: "Fog is off, so these do nothing." }) : null,
        reset,
      ]);
    });
    box.append(el("details", { class: "gen-tab", "data-tab": t.tab },
      [el("summary", {}, [`${t.tab} settings`, el("span", { class: "count" })]), ...groups]));
  }
}

/** Readouts, the filled stretch between each knob's pins, and the counts. */
function showKnobs(form) {
  const fogOff = form.fog !== "on" && form.fog !== "either";
  for (const k of Object.values(KNOBS)) {
    const [a, b] = pins(k.attr);
    const pair = [Number(a.value), Number(b.value)];
    $(`gen-k-${k.attr}-text`).textContent = knobText(k, pair, form.roll === true);
    const at = (v) => ((v - k.lo) / (k.hi - k.lo)) * 100;
    const fill = $(`gen-k-${k.attr}`).querySelector(".fill");
    fill.style.left = `${at(pair[0])}%`;
    fill.style.width = `${at(pair[1]) - at(pair[0])}%`;
    // Two pins on one spot: the one with room to move is on top.
    a.style.zIndex = pair[0] === pair[1] && pair[0] > (k.lo + k.hi) / 2 ? "3" : "1";
    b.style.zIndex = "2";
    const off = fogOff && (k.attr === "fog_sight" || k.attr === "fog_scout");
    a.disabled = b.disabled = off;
  }
  const count = (node, attrs) => {
    const n = attrs.filter((attr) => isSet(form, attr) && !(fogOff && attr.startsWith("fog_"))).length;
    node.querySelector(":scope > summary .count").textContent = n ? ` (${n} set)` : "";
  };
  for (const t of KNOB_TABS) {
    const tab = document.querySelector(`.gen-tab[data-tab="${t.tab}"]`);
    count(tab, t.groups.flatMap((g) => [...g.knobs.map((k) => k.attr), ...g.switches.map((s) => s.attr)]));
    for (const g of t.groups) {
      const group = tab.querySelector(`.gen-group[data-heading="${g.heading}"]`);
      count(group, [...g.knobs.map((k) => k.attr), ...g.switches.map((s) => s.attr)]);
      const note = group.querySelector(".fog-note");
      if (note) note.hidden = !fogOff;
    }
  }
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
  showKnobs(form);
  $("gen-result").hidden = true;
  const spec = buildSpec(form, () => 0);
  const bad = typeof spec === "string";
  $("gen-problem").textContent = bad ? `To make the link: ${spec}.` : "";
  $("gen-cost").textContent = bad ? ""
    : `The game will play ${gameCount(spec)} test games: about a minute on a computer, longer on a phone.`;
  $("gen-make").disabled = bad;
  $("gen-slack").disabled = form.targets === "none";
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
  buildKnobs();
  write(stored() || emptyForm());
  $("gen-form").addEventListener("input", check);
  $("gen-form").addEventListener("change", check);
  $("gen-form").addEventListener("submit", make);
  $("gen-copy").addEventListener("click", copy);
  check();
}

if (typeof document !== "undefined") main();
