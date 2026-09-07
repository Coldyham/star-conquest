// Display helpers shared by the three pages.
//
// Everything builds DOM through el() and textContent rather than innerHTML:
// usernames and Challenge.by are free text typed by strangers, so they must never
// be parsed as markup.

import { configTitle } from "./setup.mjs";
import { compareScores } from "./standings.mjs";

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

/**
 * "31 turns · 4 lost", or "no win · 120 turns" for a replay that never took the
 * board — see schema.sql's bot_scores, where `won` and not `turns` is the
 * discriminator. Kept apart from scoreSummary() above rather than reusing it:
 * that one would read a bot's absent `hand` as "every turn played by hand",
 * which is true but meaningless for a machine, and it has no way to say "lost".
 */
export function botSummary(row) {
  return row.won ? `${row.turns} turns · ${row.lost} lost` : `no win · ${row.turns} turns`;
}

/**
 * "knower · search depth 12" — a bot replayed at something other than its default
 * profile, so the board never quietly compares two different versions of one bot.
 * Just the name for the ordinary case, since `aux` is 1.0 nearly everywhere and a
 * suffix on every row would say nothing (see schema.sql's bot_scores).
 *
 * The label is the strategy's own `AUX_LABEL`, carried on the row rather than
 * guessed here: the knob belongs to the bot, and JS has no way to read a
 * models/*.py declaration.
 */
export function botProfile(row) {
  const aux = Number(row.aux ?? 1);
  if (aux === 1 || !row.aux_label) return row.bot;
  const value = Number.isInteger(aux) ? aux : aux.toFixed(2);
  return `${row.bot} · ${row.aux_label.toLowerCase()} ${value}`;
}

/**
 * Standard competition ranking over a list already ordered best-first: equal
 * results share a rank and the next distinct one skips it (1, 1, 3).
 *
 * Two scores are equal when both turns and lost match: `hand` is disclosure, not
 * part of the score (Challenge in settings.py). Returns one rank per score,
 * parallel to the input.
 */
export function competitionRanks(scores) {
  let rank = 0;
  let prev = null;
  return scores.map((score, i) => {
    if (!prev || prev.turns !== score.turns || prev.lost !== score.lost) rank = i + 1;
    prev = score;
    return rank;
  });
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

/**
 * The player page for one or more names: `user.html?u=Andrew&u=Rival`.
 *
 * Repeated params rather than a comma-joined one, because a name is free text and
 * may well contain a comma — URLSearchParams then escapes it for us either way.
 */
export function userHref(names) {
  const params = new URLSearchParams();
  for (const name of names) params.append("u", name);
  return `user.html?${params}`;
}

/**
 * The setup badge a card or a map's heading carries: the config's name if one
 * has been posted, else the derived label (configTitle, setup.mjs). Links to
 * the main list filtered to that one config — the badge *is* the group.
 */
export function configBadge(game) {
  return el("a", { class: "badge", href: `index.html?config=${encodeURIComponent(game.config_key)}`, text: configTitle(game) });
}

/**
 * One chip per opponent strategy (game_summary.bots), each linking to the main
 * list filtered to that bot — omitted entirely when every seat is the built-in
 * heuristic, since that's the common case and would otherwise repeat on every
 * card. Strategy names come from models/*.py drop-ins, so textContent only.
 */
export function botChips(game) {
  const bots = game.bots || [];
  if (bots.length === 1 && bots[0] === "heuristic") return [];
  return bots.map((bot) =>
    el("a", { class: "chip", href: `index.html?bot=${encodeURIComponent(bot)}`, text: bot }));
}

/**
 * "Bot leads" — the marker a list card carries when nobody has beaten this
 * map's best bot yet. `game.mjs`'s botVerdict spells the same "behind"/"tied"
 * verdict out as a sentence on the map's own page; a list card only has room
 * for a chip, and game_summary's bot_turns/bot_lost/bot_name (schema.sql) carry
 * exactly what's needed to compute it without a second query. Null once a
 * human score beats it, or when no bot has ever taken the map (bot_name null).
 */
export function botLeadBadge(game) {
  if (!game.bot_name) return null;
  const bot = { turns: game.bot_turns, lost: game.bot_lost };
  const human = { turns: game.best_turns, lost: game.best_lost };
  if (compareScores(bot, human) > 0) return null;   // a human score already beats it
  return el("span", {
    class: "badge bot-lead",
    title: `${game.bot_name} — ${game.bot_turns} turns · ${game.bot_lost} lost. No human score beats it yet.`,
    text: "Bot leads",
  });
}

export function showError(node, message) {
  node.classList.remove("loading");
  clear(node).append(el("p", { class: "error", text: message }));
}
