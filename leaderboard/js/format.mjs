// Display helpers shared by the three pages.
//
// Everything builds DOM through el() and textContent rather than innerHTML:
// usernames and Challenge.by are free text typed by strangers, so they must never
// be parsed as markup.

import { profileLabel, profiles, stopName } from "./bots.mjs";
import { configTitle, fogLabel, hasFog } from "./setup.mjs";
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

/**
 * "Random · 3 players · 18 systems · seed 42" — or "seed random" for a plain
 * settings-share link with no seed pinned (Settings.seed is None). Every row
 * actually stored in `games` has a concrete seed (the column is not-null), so
 * this only ever matters in submit.mjs's preview, before such a link is
 * rejected there for having no single map to register.
 */
export function mapSummary(game) {
  const mode = game.mode === "symmetric" ? "Symmetric" : "Random";
  return [
    mode,
    `${game.players} players`,
    `${systemCount(game)} systems`,
    `seed ${game.seed ?? "random"}`,
  ].join(" · ");
}

const SYMMETRIC_LAYOUTS = ["hub", "ring", "wheel", "core"];

/** Python's round(): halves go to the even neighbour, where Math.round goes up. */
function roundHalfEven(x) {
  const down = Math.floor(x);
  const frac = x - down;
  if (frac !== 0.5) return Math.round(x);
  return down % 2 === 0 ? down : down + 1;
}

/**
 * How many systems the game's `mapgen.symmetric_size` builds for a symmetric
 * map asked for `nodes`: whole sectors of at least two, plus any shared systems.
 * Pinned against the game by leaderboard/tests/fixtures/symmetric-sizes.json.
 */
export function symmetricSize(nodes, players, layout) {
  const shape = SYMMETRIC_LAYOUTS.includes(layout) ? layout : "hub";
  const seats = Math.max(2, players);
  const shared = shape === "hub" || shape === "wheel" ? 1 : shape === "core" ? seats : 0;
  return seats * Math.max(2, roundHalfEven((nodes - shared) / seats)) + shared;
}

/**
 * A row's real system count, as `Settings.system_count` gives it: `nodes` is
 * what was asked for, which a generated symmetric map rounds to whole sectors.
 * Reads `layout` and `custom_map` off the row or its `settings_json`; with
 * neither to hand, a symmetric map is taken to be the hub it was before layouts.
 */
export function systemCount(game) {
  const settings = game.settings_json || {};
  const custom = game.custom_map ?? settings.custom_map;
  if (game.mode !== "symmetric" || custom || typeof game.nodes !== "number") return game.nodes;
  return symmetricSize(game.nodes, game.players, game.layout ?? settings.layout);
}

/**
 * "Random · 3 players · 18 systems" — mapSummary() without the seed, for a
 * config card: every map in the group shares these (sc_config_key keys on the
 * whole setup but its seed).
 */
export function setupSummary(config) {
  return mapSummary(config).split(" · ").slice(0, 3).join(" · ");
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
 * "knower · oracle: search" — a bot replayed at something other than its default
 * profile, so the board never quietly compares two different versions of one bot.
 * Just the name for the ordinary case, since `aux` is 1.0 nearly everywhere and a
 * suffix on every row would say nothing (see schema.sql's bot_scores).
 *
 * The label is the strategy's own `AUX_LABEL`, carried on the row rather than
 * guessed here: the knob belongs to the bot, and JS has no way to read a
 * models/*.py declaration. A knob with named stops arrives as "Label: Stop"
 * (`bot_replay.aux_note`) and already says everything, so no number is appended;
 * any other label gets the value ("greed 0.75", or an older row's "search depth 12").
 */
export function botProfile(row) {
  const aux = Number(row.aux ?? 1);
  if (aux === 1 || !row.aux_label) return row.bot;
  if (row.aux_label.includes(": ")) return `${row.bot} · ${row.aux_label.toLowerCase()}`;
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
 * "Fog" on a setup played under fog of war whose badge doesn't already say so
 * — the one tweak that changes what kind of game a map is, so a posted name
 * must not hide it. An unnamed config's own label leads with it already
 * (setup.mjs's configLabel), so it gets no second one. Links to the list
 * filtered to fog maps (`?fog=on`, listing.mjs), on the grouped view when
 * `group` is "config".
 */
export function fogBadge(game, group = "game") {
  if (!hasFog(game.settings_json) || !(game.config_name || "").trim()) return null;
  const href = group === "config" ? "index.html?group=config&fog=on" : "index.html?fog=on";
  const label = fogLabel(game.settings_json);
  const title = label === "Fog" ? "Fog of war" : `Fog of war, sight/scout range ${label.slice(4)}`;
  return el("a", { class: "badge fog", href, title, text: "Fog" });
}

/**
 * One chip per opponent (game_summary.bots, through `profiles`), linking to the
 * main list filtered to that bot — omitted entirely when every seat is the
 * built-in heuristic, since that's the common case and would otherwise repeat on
 * every card. A bot off its default stop is a two-part chip, "knower · council":
 * the family half finds every stop of it, the stop half that stop alone. Strategy
 * names come from models/*.py drop-ins, so textContent only.
 */
export function botChips(game) {
  const shown = profiles(game.bots);
  if (shown.length === 1 && shown[0] === "heuristic") return [];
  const link = (bot, text, cls = null) =>
    el("a", { class: cls, href: `index.html?bot=${encodeURIComponent(bot)}`, text });
  return shown.map((profile) => {
    const family = profile.split(":")[0];
    const stop = stopName(profile);
    if (!stop) return link(family, family, "chip");
    return el("span", { class: "chip pair", title: profileLabel(profile) },
      [link(family, family), " · ", link(profile, stop.toLowerCase())]);
  });
}

/**
 * "Name" or "Name & N others" for a map's current best score — the one thing
 * `game_summary`'s aggregate says about who is ahead, independent of the full
 * per-score list. Shared by the list card (home.mjs's row()) and the map's own
 * page, which falls back to this alone while a map is still embargoed (see
 * game.mjs's renderEmbargoed) — an embargo hides the rest of the board, but not
 * the one fact that gives a friend something left to chase.
 */
export function leaderCredit(game) {
  const name = (game.best_user_name || "").trim() || "anonymous";
  // A dead heat on turns *and* lost is a shared record, so credit all of it.
  // best_holders is absent unless the game_summary view is current; treat a
  // missing count as the one leading name.
  const others = Math.max(0, Number(game.best_holders || 1) - 1);
  return others ? `${name} & ${others} other${others === 1 ? "" : "s"}` : name;
}

// The share of bots that lose a map (game_summary.difficulty) at or below which
// each word applies, easiest first. Cut along the roster's classes, so on the
// 7-bot roster, one per family at its top stop: Hard is a map only the top two
// (knower at Council, actuary at Hunt) take, 1-2 winning; Medium is one marshal,
// evolved and thinker take too, 3-5; Easy is one heuristic or rusherplus take
// too, 6+; Very hard is none. The same counts hold on 6 bots, while a new bot is
// still being replayed onto the maps the other six have reached.
const DIFFICULTY_LEVELS = [
  { most: 0.2, text: "Easy", cls: "easy" },
  { most: 0.6, text: "Medium", cls: "medium" },
  { most: 0.999, text: "Hard", cls: "hard" },
  { most: 1, text: "Very hard", cls: "very-hard" },
];

/** The word for a difficulty share, or null for a map no bot has run on yet. */
export function difficultyLevel(difficulty) {
  if (difficulty === null || difficulty === undefined || difficulty === "") return null;
  const share = Number(difficulty);
  return DIFFICULTY_LEVELS.find((level) => share <= level.most) || DIFFICULTY_LEVELS.at(-1);
}

/**
 * "Easy" … "Very hard": the estimated difficulty, from how many of the board's
 * bots took the map from the human's seat (game_summary.bot_wins/bot_runs). A
 * config card passes its average (config_summary.avg_difficulty) and no counts.
 * Not a link, like .bot-lead: it states an estimate, the sort is the way to use it.
 */
export function difficultyBadge(game, difficulty = game.difficulty) {
  const level = difficultyLevel(difficulty);
  if (!level) return null;
  const title = game.bot_runs
    ? `Estimated difficulty: ${game.bot_wins} of ${game.bot_runs} bots won this map from your seat.`
    : `Estimated difficulty: on average ${Math.round((1 - Number(difficulty)) * 100)}% of the bots ` +
      "won this setup's maps from your seat.";
  return el("span", { class: `badge difficulty ${level.cls}`, title, text: level.text });
}

/**
 * "Bot leads" / "Bot tied" — the marker a list card carries when nobody has
 * beaten this map's best bot yet, worded for whether the board's best trails
 * it or exactly matches it. `game.mjs`'s botVerdict spells the same
 * "behind"/"tied" verdict out as a sentence on the map's own page; a list card
 * only has room for a chip, and game_summary's bot_turns/bot_lost/bot_name
 * (schema.sql) carry exactly what's needed to compute it without a second
 * query. Null once a human score beats it, or when no bot has ever taken the
 * map (bot_name null). Both are what the "Bot unbeaten" filter (`bot_leads`)
 * lists.
 */
export function botLeadBadge(game) {
  if (!game.bot_name) return null;
  const bot = { turns: game.bot_turns, lost: game.bot_lost };
  const human = { turns: game.best_turns, lost: game.best_lost };
  const order = game.best_turns == null ? -1 : compareScores(bot, human);
  if (order > 0) return null;   // a human score already beats it
  const tied = order === 0;
  return el("span", {
    class: tied ? "badge bot-lead tied" : "badge bot-lead",
    title: `${game.bot_name} — ${game.bot_turns} turns · ${game.bot_lost} lost. ` +
      (tied ? "The board's best matches it exactly." : "No human score beats it yet."),
    text: tied ? "Bot tied" : "Bot leads",
  });
}

/**
 * "Replays hidden — revealing in 3 days", or null once the embargo has lifted
 * or none was ever set (`games.embargo_until`, schema.sql). Deliberately says
 * nothing about how many replays are behind it, or whether any exist at all —
 * either would leak the one thing the embargo exists to hide. Rounds up, so a
 * few hours left still reads as "1 day" rather than "0 days".
 */
export function embargoNote(until) {
  if (!until) return null;
  const ms = new Date(until).getTime() - Date.now();
  if (!(ms > 0)) return null;
  const days = Math.max(1, Math.ceil(ms / 86400000));
  return `Replays hidden — revealing in ${days} ${days === 1 ? "day" : "days"}`;
}

/**
 * The badge a map's card carries while embargoNote() above has something to
 * say — same shape as botLeadBadge: a stated fact, not a filter, so it is a
 * span rather than a link.
 */
export function embargoBadge(game) {
  const note = embargoNote(game.embargo_until);
  if (!note) return null;
  return el("span", { class: "badge embargo", title: note, text: "Embargoed" });
}

/**
 * This map is a node on a campaign (`campaignMark`, campaign.mjs) — a link to
 * that node on the campaign page, or null for a map on none.
 */
export function campaignBadge(mark) {
  if (!mark) return null;
  return el("a", { class: "badge campaign", href: mark.href, text: mark.label });
}

export function showError(node, message) {
  node.classList.remove("loading");
  clear(node).append(el("p", { class: "error", text: message }));
}
