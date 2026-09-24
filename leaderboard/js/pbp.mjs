// The play-by-post lobby: public matches by status, and a one-time link for
// each open seat. Pure helpers are exported for tests/pbp-page.test.mjs; the
// DOM half runs only in a browser.

import { GAME_URL } from "./config.mjs";
import { clear, el, relativeTime, showError } from "./format.mjs";

const ENDPOINT = "/api/pbp";

export const GROUPS = [
  ["open", "Open seats"],
  ["lapsed", "Lapsed"],
  ["in_progress", "In progress"],
  ["finished", "Finished"],
];

/** `{status: [match, ...]}` for every group, in `GROUPS` order. */
export function groupMatches(matches) {
  const out = Object.fromEntries(GROUPS.map(([key]) => [key, []]));
  for (const match of matches) (out[match.status] ?? out.in_progress).push(match);
  return out;
}

/** The link a seat's token opens in the game: `pbp.link_fragment`'s shape. */
export function seatLink(matchId, token, game = GAME_URL) {
  return `${game}#pbp=${matchId}:${token}`;
}

/** "due in 5h", "overdue by 2h", or "" for a match with no deadline. */
export function deadlineNote(match, now = Date.now()) {
  if (match.finished || match.deadline_hours === null || match.deadline_hours === undefined) return "";
  const due = Date.parse(match.turn_opened_at) + match.deadline_hours * 3600 * 1000;
  if (Number.isNaN(due)) return "";
  const hours = Math.round(Math.abs(due - now) / 3600000);
  const span = hours < 1 ? "<1h" : hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
  return due >= now ? `due in ${span}` : `overdue by ${span}`;
}

/** One line of facts about a match. */
export function matchSummary(match, now = Date.now()) {
  const parts = [
    match.mode === "symmetric" ? "Symmetric" : "Random",
    `${match.seats.length} ${match.seats.length === 1 ? "person" : "people"}`,
    `turn ${match.turn}`,
  ];
  if (match.deadline_hours) parts.push(`${match.deadline_hours}h turns`);
  else parts.push("no deadline");
  const due = deadlineNote(match, now);
  if (due) parts.push(due);
  return parts.join(" · ");
}

/** "waiting on 2, 3 · 3 lapsed (bot)" */
export function waitingNote(match) {
  if (match.finished || !match.waiting.length) return "";
  const parts = [`waiting on seat ${match.waiting.join(", ")}`];
  for (const [seat, action] of Object.entries(match.lapsed || {})) {
    parts.push(`seat ${seat} lapsed (${action === "bot" ? "bot plays" : "holds"})`);
  }
  return parts.join(" · ");
}

async function claim(matchId, seat) {
  const response = await fetch(`${ENDPOINT}?action=claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match_id: matchId, seat }),
  });
  const body = await response.json().catch(() => ({}));
  return [response.status, body];
}

function seatControl(match, seat) {
  const slot = el("div", { class: "seat-claim" });
  const button = el("button", { class: "btn", type: "button", text: `Get seat ${seat} link` });
  button.addEventListener("click", async () => {
    button.disabled = true;
    let status;
    let body;
    try {
      [status, body] = await claim(match.match_id, seat);
    } catch {
      status = 0;
      body = {};
    }
    if (status === 201 && body.token) {
      const link = seatLink(match.match_id, body.token);
      const field = el("input", { type: "text", readonly: "", value: link, class: "seat-link" });
      const copy = el("button", { class: "btn ghost", type: "button", text: "Copy" });
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(link);
          copy.textContent = "Copied";
        } catch {
          field.select();
        }
      });
      clear(slot).append(
        el("span", { class: "hint", text: `Seat ${seat} — shown once, copy it now:` }),
        field, copy);
      field.addEventListener("focus", () => field.select());
      return;
    }
    const message = status === 409 ? (body.error || "already claimed") : "could not claim";
    clear(slot).append(el("span", { class: "hint", text: `Seat ${seat}: ${message}` }));
  });
  slot.append(button);
  return slot;
}

function matchCard(match) {
  const card = el("div", { class: "card" }, [
    el("div", { class: "config-title", text: match.match_id }),
    el("div", { class: "card-meta", text: matchSummary(match) }),
  ]);
  const waiting = waitingNote(match);
  if (waiting) card.append(el("div", { class: "card-meta", text: waiting }));
  card.append(el("div", { class: "card-meta",
                          text: `started ${relativeTime(match.created_at)}, last moved ${relativeTime(match.updated_at)}` }));
  for (const seat of match.unclaimed || []) card.append(seatControl(match, seat));
  return card;
}

function render(node, matches) {
  node.classList.remove("loading");
  clear(node);
  const groups = groupMatches(matches);
  let any = false;
  for (const [key, label] of GROUPS) {
    if (!groups[key].length) continue;
    any = true;
    const section = el("section", {}, [el("h2", { text: `${label} (${groups[key].length})` })]);
    const holder = key === "finished" ? el("details", {}, [el("summary", { text: "Show" })]) : section;
    for (const match of groups[key]) holder.append(matchCard(match));
    if (holder !== section) section.append(holder);
    node.append(section);
  }
  if (!any) node.append(el("p", { class: "lede", text: "No public matches right now." }));
}

async function main() {
  const node = document.getElementById("lobby");
  try {
    const response = await fetch(`${ENDPOINT}?action=list`);
    if (!response.ok) throw new Error(String(response.status));
    const body = await response.json();
    render(node, body.matches || []);
  } catch {
    showError(node, "Couldn't load the matches.");
  }
}

if (typeof document !== "undefined") main();
