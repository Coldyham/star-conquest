// Display helpers shared by the three pages.
//
// Everything builds DOM through el() and textContent rather than innerHTML:
// usernames and Challenge.by are free text typed by strangers, so they must never
// be parsed as markup.

/** el("a", {href, class}, ["text", node]) -> HTMLElement */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/** "Random · 3 players · 18 systems · seed 42" */
export function mapSummary(game) {
  const mode = game.mode === "symmetric" ? "Symmetric" : "Random";
  return [
    mode,
    `${game.players} players`,
    `${game.nodes} systems`,
    `seed ${game.seed}`,
  ].join(" · ");
}

/**
 * "25 turns · 2 lost · 12 by hand" — the same figures the game shows, from
 * Challenge.summary: turns is the score, fewest lost breaks a tie, and the
 * by-hand count is only worth stating when some turns were autoplayed.
 *
 * Kept terse because the cabinet renders it in a pixel face, which is wide.
 */
export function scoreSummary(score) {
  let out = `${score.turns} turns · ${score.lost} lost`;
  if (score.hand < score.turns) out += ` · ${score.hand} by hand`;
  return out;
}

/** 1 -> "1ST", 2 -> "2ND", 11 -> "11TH" — the rank column on a cabinet. */
export function ordinal(n) {
  const teens = n % 100;
  const suffix = teens >= 11 && teens <= 13
    ? "TH"
    : { 1: "ST", 2: "ND", 3: "RD" }[n % 10] || "TH";
  return `${n}${suffix}`;
}

const UNITS = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];

/** "3 hours ago" */
export function relativeTime(iso) {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  for (const [unit, size] of UNITS) {
    const n = Math.floor(seconds / size);
    if (n >= 1) return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  }
  return "just now";
}

/** "5M", "3H", "2D", "4MO" — a score table's age column, one glyph pair wide. */
export function shortTime(iso) {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "now";
  const scale = [["y", 31536000], ["mo", 2592000], ["d", 86400], ["h", 3600], ["m", 60]];
  for (const [unit, size] of scale) {
    const n = Math.floor(seconds / size);
    if (n >= 1) return `${n}${unit}`;
  }
  return "now";
}

/** Who a score belongs to: the poster's name, or whatever the link disclosed. */
export function credit(score) {
  const name = (score.users && score.users.name) || score.user_name || "";
  return name.trim() || (score.by_name || "").trim() || "anonymous";
}

export function showError(node, message) {
  node.classList.remove("loading");
  clear(node).append(el("p", { class: "error", text: message }));
}
