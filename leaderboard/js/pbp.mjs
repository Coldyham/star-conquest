// The play-by-post lobby: open invitations, the matches this browser holds a
// seat in, and finished results — plus one match's own page (`?match=<id>`),
// where an open seat is taken. Pure helpers are exported for
// tests/pbp-page.test.mjs; the DOM half runs only in a browser.
//
// "Yours" is the game's own seat store, `sc_pbp_seats` (`pbp.remember` in
// starconquest/pbp.py): this page is served from the game's origin, under
// /board/, so the two share one localStorage. A seat claimed here is written into
// that same store in the same shape, so the game reopens it with no link at all.
// There is no account behind it; the game owns the store and this page never
// prunes it.

import { GAME_URL, PLAYER_COLORS, PLAYER_NAMES } from "./config.mjs";
import { clear, el, mapSummary, relativeTime, showError } from "./format.mjs";
import { phrase } from "./matchnames.mjs";
import { myName, rememberName } from "./me.mjs";
import { mountNav } from "./nav.mjs";
import { configLabel, tweaks } from "./setup.mjs";

const ENDPOINT = "/api/pbp";
const MATCH_ID = /^[0-9a-f]{16}$/;
const TOKEN = /^[0-9a-f]{32}$/;
// `paths.WEB_PBP_SEATS_KEY` in the game; pinned by tests/test_leaderboard_sync.py.
export const SEATS_KEY = "sc_pbp_seats";
// `NAME_MAX` in netlify/functions/pbp.mjs.
const NAME_MAX = 24;
// `MAX_LIST_IDS` in netlify/functions/pbp.mjs.
const MAX_IDS = 50;

export const GROUPS = [
  ["open", "Open seats"],
  ["mine", "Your matches"],
  ["finished", "Finished"],
];

// --------------------------------------------------------------------------
// Pure helpers
// --------------------------------------------------------------------------

/** What a match is called: its title, else its pass-phrase name. */
export function matchLabel(match) {
  return (match.title || "").trim() || phrase(match.match_id) || match.match_id;
}

/** "Random · 3 players · 18 systems · seed 42", plus "custom map" for a drawn one. */
export function setupLine(match) {
  const settings = match.settings_json || {};
  const line = mapSummary({
    mode: settings.mode ?? match.mode,
    players: settings.players ?? match.players ?? match.seats.length,
    nodes: settings.nodes ?? "?",
    seed: match.seed,
  });
  return settings.custom_map ? `${line} · custom map` : line;
}

export function seatName(seat) {
  return PLAYER_NAMES[seat] || `Player ${seat}`;
}

export function seatColor(seat) {
  return PLAYER_COLORS[seat] || PLAYER_COLORS[0];
}

/** "2-day turns", "36h turns", or "no deadline". */
export function deadlineLabel(hours) {
  if (!hours) return "no deadline";
  return hours % 24 === 0 ? `${hours / 24}-day turns` : `${hours}h turns`;
}

/**
 * One row per seat, in seat order: who holds it and where it stands.
 *
 * `kind` is "person" (someone holds the seat), "open" (a public match's seat
 * nobody has taken) or "bot" (not in the roster, so its strategy plays it —
 * `settings_json.ai_strategy`, seat-1 indexed, "heuristic" where absent).
 * `state` is "won", "waiting", "in", "hold" or "bot" (a lapsed seat, and what
 * the lapse does), or "" where nothing applies.
 */
export function roster(match) {
  const settings = match.settings_json || {};
  const players = Number(settings.players ?? match.players) || Math.max(0, ...match.seats);
  const names = match.names || {};
  const open = new Set(match.unclaimed || []);
  const waiting = new Set(match.waiting || []);
  const lapsed = match.lapsed || {};
  const rows = [];
  for (let seat = 1; seat <= players; seat += 1) {
    const person = match.seats.includes(seat);
    const kind = !person ? "bot" : open.has(seat) ? "open" : "person";
    let state = "";
    if (match.finished) state = match.winner === seat ? "won" : "";
    else if (lapsed[seat]) state = lapsed[seat];
    else if (kind === "person") state = waiting.has(seat) ? "waiting" : "in";
    rows.push({
      seat,
      kind,
      state,
      name: kind === "person" ? (names[seat] || "").trim() : "",
      bot: kind === "bot" ? ((settings.ai_strategy || [])[seat - 1] || "heuristic") : "",
    });
  }
  return rows;
}

/** "Alice", "open seat", "knower (bot)", "random bot", or "player" for an unnamed person. */
export function seatWho(row) {
  if (row.kind === "open") return "open seat";
  if (row.kind === "bot") return row.bot === "random" ? "random bot" : `${row.bot} (bot)`;
  return row.name || "player";
}

/** "Alice (Azure)", or just "Azure" where the seat has no name. */
export function seatCredit(row) {
  const bot = row.bot === "random" ? "random bot" : row.bot;
  const who = row.kind === "person" ? row.name : row.kind === "bot" ? bot : "";
  return who ? `${who} (${seatName(row.seat)})` : seatName(row.seat);
}

/** "Won by Alice (Azure) in 34 turns", "Draw after 34 turns", or "Finished after 34 turns". */
export function resultNote(match) {
  const turns = `${match.turn} ${match.turn === 1 ? "turn" : "turns"}`;
  if (match.winner === 0) return `Draw after ${turns}`;
  const row = roster(match).find((r) => r.seat === match.winner);
  return row ? `Won by ${seatCredit(row)} in ${turns}` : `Finished after ${turns}`;
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

/** "Turn 5 · 2-day turns · due in 5h" — a live match's clock. */
export function liveNote(match, now = Date.now()) {
  const parts = [`Turn ${match.turn}`, deadlineLabel(match.deadline_hours)];
  const due = deadlineNote(match, now);
  if (due) parts.push(due);
  return parts.join(" · ");
}

/** "waiting on Bob (Crimson) · Crimson lapsed (bot plays)" — open seats aside. */
export function waitingNote(match) {
  const open = new Set(match.unclaimed || []);
  const waiting = (match.waiting || []).filter((seat) => !open.has(seat));
  if (match.finished || !waiting.length) return "";
  const rows = new Map(roster(match).map((r) => [r.seat, r]));
  const credit = (seat) => (rows.has(seat) ? seatCredit(rows.get(seat)) : seatName(seat));
  const parts = [`waiting on ${waiting.map(credit).join(", ")}`];
  for (const [seat, action] of Object.entries(match.lapsed || {})) {
    parts.push(`${seatName(Number(seat))} lapsed (${action === "bot" ? "bot plays" : "holds"})`);
  }
  return parts.join(" · ");
}

/**
 * `{open, mine, finished}`: the public open invitations you are not in, your
 * live matches, and every finished match either list returned. A stranger's
 * match that is under way is none of those, and is not shown.
 */
export function groupMatches(matches, mine = {}) {
  const out = { open: [], mine: [], finished: [] };
  const seen = new Set();
  for (const match of matches) {
    if (seen.has(match.match_id)) continue;
    seen.add(match.match_id);
    if (match.finished) out.finished.push(match);
    else if (match.match_id in mine) out.mine.push(match);
    else if (match.status === "open") out.open.push(match);
  }
  return out;
}

/** The link a seat's token opens in the game: `pbp.link_fragment`'s shape. */
export function seatLink(matchId, token, game = GAME_URL) {
  return `${game}#pbp=${matchId}:${token}`;
}

/** "Open in game" for a seat this browser holds: always the full seat link. */
export function openLink(matchId, entry, game = GAME_URL) {
  return entry && entry.token ? seatLink(matchId, entry.token, game) : null;
}

/**
 * `{id: {seat, token}}` out of the game's seat store, keeping only entries the
 * game itself could use — a malformed store reads as empty, a junk entry is
 * skipped. Never written back: the store is the game's.
 */
export function parseSeats(text) {
  let data;
  try {
    data = JSON.parse(text || "{}");
  } catch {
    return {};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return {};
  const out = {};
  for (const [id, entry] of Object.entries(data)) {
    if (!MATCH_ID.test(id)) continue;
    if (!Number.isInteger(entry?.seat) || entry.seat < 1) continue;
    if (typeof entry?.token !== "string" || !TOKEN.test(entry.token)) continue;
    out[id] = { seat: entry.seat, token: entry.token };
  }
  return out;
}

/**
 * The store's text with one seat added, in exactly `pbp.remember`'s shape.
 * Everything else in it is kept as it was, even entries this page can't read,
 * and a store that isn't an object is replaced — which is what the game's own
 * `remembered()` then `remember()` does too.
 */
export function withSeat(text, matchId, seat, token) {
  let data;
  try {
    data = JSON.parse(text || "{}");
  } catch {
    data = {};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) data = {};
  data[matchId] = { seat, token };
  return JSON.stringify(data);
}

/** The ids to look up, newest last, capped at what one request may ask. */
export function mineIds(mine) {
  return Object.keys(mine).slice(-MAX_IDS);
}

// --------------------------------------------------------------------------
// Storage and network
// --------------------------------------------------------------------------

function readSeats() {
  try {
    return parseSeats(localStorage.getItem(SEATS_KEY));
  } catch {
    return {};
  }
}

/** Keep a claimed seat where the game looks for it. False if storage refused. */
function storeSeat(matchId, seat, token) {
  try {
    localStorage.setItem(SEATS_KEY, withSeat(localStorage.getItem(SEATS_KEY), matchId, seat, token));
    return true;
  } catch {
    return false;
  }
}

async function fetchList(ids = null) {
  const query = ids ? `&ids=${ids.join(",")}` : "";
  const response = await fetch(`${ENDPOINT}?action=list${query}`);
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()).matches || [];
}

async function claim(matchId, seat, name) {
  const response = await fetch(`${ENDPOINT}?action=claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match_id: matchId, seat, name }),
  });
  const body = await response.json().catch(() => ({}));
  return [response.status, body];
}

// --------------------------------------------------------------------------
// DOM
// --------------------------------------------------------------------------

function swatch(seat) {
  return el("span", { class: "swatch", style: `--seat: ${seatColor(seat)}`, "aria-hidden": "true" });
}

/** A compact "who's at the table" line of chips, one per seat. */
function rosterChips(match) {
  return roster(match).map((row) => el("span", { class: `chip seat ${row.kind}`, title: seatName(row.seat) }, [
    swatch(row.seat), seatWho(row),
  ]));
}

function statusLine(match) {
  if (match.finished) return el("div", { class: "credit", text: resultNote(match) });
  return el("div", { class: "card-meta", text: liveNote(match) });
}

function matchCard(match, mine) {
  const label = matchLabel(match);
  const children = [el("div", { class: "config-title", text: label })];
  if (label !== phrase(match.match_id)) {
    children.push(el("div", { class: "card-meta", text: phrase(match.match_id) }));
  }
  children.push(el("div", { class: "setup", text: setupLine(match) }), statusLine(match));
  const waiting = waitingNote(match);
  if (waiting) children.push(el("div", { class: "card-meta", text: waiting }));

  const tags = [el("span", { class: "badge", text: configLabel(match.settings_json) }), ...rosterChips(match)];
  if (match.match_id in mine) tags.unshift(el("span", { class: "badge yours", text: "Yours" }));
  return el("div", { class: "card" }, [
    el("a", { class: "card-body", href: `pbp.html?match=${match.match_id}` },
       [el("div", { class: "card-main" }, children)]),
    el("div", { class: "card-tags" }, tags),
  ]);
}

async function renderList(node, mine) {
  const ids = mineIds(mine);
  const [publicList, mineList] = await Promise.all([
    fetchList(),
    ids.length ? fetchList(ids).catch(() => null) : Promise.resolve([]),
  ]);
  const groups = groupMatches([...(mineList || []), ...publicList], mine);

  node.classList.remove("loading");
  clear(node);
  let any = false;
  for (const [key, label] of GROUPS) {
    if (!groups[key].length) continue;
    any = true;
    const section = el("section", {}, [el("h2", { text: `${label} (${groups[key].length})` })]);
    const holder = key === "finished" ? el("details", {}, [el("summary", { text: "Show" })]) : section;
    for (const match of groups[key]) holder.append(matchCard(match, mine));
    if (holder !== section) section.append(holder);
    node.append(section);
  }
  if (!any) node.append(el("p", { class: "lede", text: "No open seats or finished matches right now." }));
}

function stateText(row) {
  return {
    won: "won", waiting: "to move", in: "orders in", hold: "missed a turn — holds", bot: "lapsed — bot plays",
  }[row.state] || "";
}

function rosterTable(match) {
  return el("ul", { class: "roster" }, roster(match).map((row) => el("li", { class: `roster-row ${row.kind}` }, [
    swatch(row.seat),
    el("span", { class: "roster-seat", text: seatName(row.seat) }),
    el("span", { class: "roster-who", text: seatWho(row) }),
    el("span", { class: `roster-state ${row.state}`, text: stateText(row) }),
  ])));
}

function openButton(match, entry) {
  const href = openLink(match.match_id, entry);
  return href ? el("a", { class: "btn play", href, text: "Open in game" }) : null;
}

function claimControl(match, seat, mine, onClaimed) {
  const slot = el("form", { class: "seat-claim" });
  const nameField = el("input", {
    type: "text", maxlength: String(NAME_MAX), class: "seat-name",
    placeholder: "Your name (optional)", "aria-label": `Your name for ${seatName(seat)}`,
    value: myName(),
  });
  const button = el("button", { class: "btn", type: "submit" }, [swatch(seat), `Take ${seatName(seat)}`]);
  slot.append(nameField, button);
  slot.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    const name = nameField.value.trim();
    let status;
    let body;
    try {
      [status, body] = await claim(match.match_id, seat, name);
    } catch {
      status = 0;
      body = {};
    }
    if (status === 201 && body.token) {
      if (name) rememberName(name);
      const link = seatLink(match.match_id, body.token);
      const kept = storeSeat(match.match_id, seat, body.token);
      if (kept) mine[match.match_id] = { seat, token: body.token };
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
      field.addEventListener("focus", () => field.select());
      clear(slot).append(
        el("span", { class: "hint", text: kept
          ? `${seatName(seat)} is yours, and this browser keeps it — this page will offer "Open in game". Copy the link to play elsewhere:`
          : `${seatName(seat)} is yours. This browser couldn't save it — keep this link:` }),
        field, copy, el("a", { class: "btn play", href: link, text: "Open in game" }));
      onClaimed(slot);
      return;
    }
    const message = status === 409 ? (body.error || "already taken") : status === 400 ? (body.error || "refused") : "could not take it";
    clear(slot).append(el("span", { class: "hint", text: `${seatName(seat)}: ${message}` }));
  });
  return slot;
}

async function renderDetail(node, matchId, mine) {
  const heading = document.getElementById("title");
  const lede = document.getElementById("lede");
  const back = document.getElementById("back");
  back.href = "pbp.html";
  back.textContent = "← All matches";
  const [match] = MATCH_ID.test(matchId) ? await fetchList([matchId]) : [];
  node.classList.remove("loading");
  clear(node);
  if (!match) {
    heading.textContent = "No such match";
    lede.textContent = "It may have been removed, or the link is mistyped.";
    return;
  }
  const label = matchLabel(match);
  heading.textContent = label;
  document.title = `${label} · Play by post · Star Conquest Leaderboard`;
  lede.textContent = label === phrase(match.match_id) ? "" : phrase(match.match_id);

  const tags = tweaks(match.settings_json).map((t) => el("span", { class: "chip", text: t.label }));
  if (!tags.length) tags.push(el("span", { class: "badge", text: "Default setup" }));
  const entry = mine[match.match_id];
  node.append(
    el("div", { class: "setup", text: setupLine(match) }),
    el("div", { class: "card-tags" }, tags),
    statusLine(match),
  );
  const waiting = waitingNote(match);
  if (waiting) node.append(el("div", { class: "card-meta", text: waiting }));
  node.append(
    el("div", { class: "card-meta", text: `started ${relativeTime(match.created_at)}, last moved ${relativeTime(match.updated_at)}` }),
    el("h2", { text: "At the table" }),
    rosterTable(match),
  );

  const yours = el("div", { class: "yours-slot" });
  const button = entry ? openButton(match, entry) : null;
  if (button) yours.append(button);
  node.append(yours);

  const open = match.finished ? [] : match.unclaimed || [];
  if (open.length && !entry) {
    const section = el("section", { class: "claims" }, [
      el("h2", { text: "Take a seat" }),
      el("p", { class: "lede", text: "The name is optional and shows beside your colour. The seat is kept in this browser, beside the game's own, so this page can always reopen it — copy its link to play from another device." }),
    ]);
    const controls = open.map((seat) => claimControl(match, seat, mine, (taken) => {
      for (const other of controls) if (other !== taken) other.remove();
    }));
    section.append(...controls);
    node.append(section);
  }
}

async function main() {
  mountNav();
  const node = document.getElementById("lobby");
  const mine = readSeats();
  const matchId = new URLSearchParams(location.search).get("match");
  try {
    if (matchId) await renderDetail(node, matchId, mine);
    else await renderList(node, mine);
  } catch {
    showError(node, "Couldn't load the matches.");
  }
}

if (typeof document !== "undefined") main();
