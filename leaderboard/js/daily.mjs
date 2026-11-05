// The daily challenges (docs/design/daily.md): what the page and /api/daily
// share. A day is a UTC date; a row of `dailies` per kind is made a day ahead
// by tools/daily.py and readable once its day has started.
//
// Pure: no DOM, no fetch.

import { profileLabel, seatProfile } from "./bots.mjs";

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86400000;
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
// A day's two dailies (`dailies.kind`): the board, and the puzzle, a small duel
// won in the fewest turns.
export const KINDS = ["board", "puzzle"];

/**
 * Who a day's setup is played against, each bot once, as the board names a
 * seat's bot ("thinker · claudebot", or "marshal" at its default stop). A
 * heuristic with no reserve is the probes' "heuristic+lean"
 * (`gauntletgen.TWEAKS`), a puzzle's Tuesday opponent.
 */
export function opponents(settings) {
  const out = [];
  for (let seat = 2; seat <= (settings.players || 2); seat += 1) {
    const name = (settings.ai_strategy || [])[seat - 1];
    if (!name) continue;
    const params = (settings.ai || [])[seat - 1] || {};
    const label = name === "heuristic" && params.reserve_fraction === 0 && params.reserve_floor === 0
      ? "heuristic+lean"
      : profileLabel(seatProfile(name, params.aux));
    if (!out.includes(label)) out.push(label);
  }
  return out;
}

/** `date`'s UTC day as YYYY-MM-DD. */
export function dayParam(date) {
  return new Date(date).toISOString().slice(0, 10);
}

/** Whether `text` is a real YYYY-MM-DD date. */
export function validDay(text) {
  if (typeof text !== "string" || !DAY_RE.test(text)) return false;
  const t = Date.parse(`${text}T00:00:00Z`);
  return Number.isFinite(t) && dayParam(t) === text;
}

/** The day `n` days after `day` (negative for before). */
export function addDays(day, n) {
  return dayParam(Date.parse(`${day}T00:00:00Z`) + n * DAY_MS);
}

/** Monday 0 ... Sunday 6, as tools/daily.py counts. */
export function weekdayOf(day) {
  return (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7;
}

/** The seven days, Monday first, of the week holding `day`. */
export function weekDays(day) {
  const monday = addDays(day, -weekdayOf(day));
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** The first of the month `n` months after the one holding `day`. */
export function addMonths(day, n) {
  const d = new Date(`${day.slice(0, 7)}-01T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + n);
  return dayParam(d);
}

/**
 * The month holding `day` as calendar rows, Monday first: each row seven
 * days, with null in the cells before the 1st and after the month's end.
 */
export function monthWeeks(day) {
  const first = `${day.slice(0, 7)}-01`;
  const next = addMonths(first, 1);
  const rows = [];
  for (let monday = addDays(first, -weekdayOf(first)); monday < next; monday = addDays(monday, 7)) {
    rows.push(weekDays(monday).map((d) => (d >= first && d < next ? d : null)));
  }
  return rows;
}

/** "Wed 28 Oct": a day as the result line and the page name it. */
export function shortDate(day) {
  const d = new Date(`${day}T00:00:00Z`);
  return `${WEEKDAYS[weekdayOf(day)].slice(0, 3)} ${d.getUTCDate()} `
    + d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
}

/**
 * The day's standings from its `counted_scores` rows: each player's best win
 * (fewest turns, then fewest ships lost, then earliest), best first. A row
 * with `hand` 0 was autoplayed through and does not count.
 */
export function standings(rows) {
  const best = new Map();
  const better = (a, b) => a.turns - b.turns || a.lost - b.lost
    || String(a.submitted_at).localeCompare(String(b.submitted_at));
  for (const row of rows) {
    if (!(row.hand > 0)) continue;
    const held = best.get(row.user_name);
    if (!held || better(row, held) < 0) best.set(row.user_name, row);
  }
  return [...best.values()].sort(better);
}

/** Consecutive days in `days` (a Set) ending on `today`, or on the day
 * before when `today` is not in it yet (a streak not yet extended today is
 * still alive), and the day before the run's first. */
function walk(days, today) {
  let day = days.has(today) ? today : addDays(today, -1);
  let n = 0;
  while (days.has(day)) {
    n += 1;
    day = addDays(day, -1);
  }
  return { n, before: day };
}

/**
 * Each name's streaks, ending on `today` or the day before. `wins` is
 * `[{name, day, onTheDay}]`: a counted win on a day's map, and whether it was
 * posted within that day.
 *
 * `days` is the mended streak: a missed day counts once it is won later, from
 * the archive. `onTheDay` is the true streak: only wins posted on the day
 * itself, while the map was new and its replays still embargoed.
 * Returns `[{name, days, onTheDay}]` with `days` > 0, longest first, then the
 * longer true streak, then by name.
 *
 * `from`, when given, is the earliest day `wins` were read from: each row then
 * also says whether either streak runs back to it (`daysAtLeast`,
 * `onTheDayAtLeast`), so it may be longer than counted.
 */
export function streaks(wins, today, from = null) {
  const byName = new Map();
  for (const { name, day, onTheDay } of wins) {
    if (!byName.has(name)) byName.set(name, { any: new Set(), onDay: new Set() });
    const sets = byName.get(name);
    sets.any.add(day);
    if (onTheDay) sets.onDay.add(day);
  }
  const out = [];
  for (const [name, sets] of byName) {
    const any = walk(sets.any, today);
    if (any.n === 0) continue;
    const onDay = walk(sets.onDay, today);
    const row = { name, days: any.n, onTheDay: onDay.n };
    if (from != null) {
      row.daysAtLeast = any.before < from;
      row.onTheDayAtLeast = onDay.n > 0 && onDay.before < from;
    }
    out.push(row);
  }
  return out.sort((a, b) => b.days - a.days || b.onTheDay - a.onTheDay || a.name.localeCompare(b.name));
}

/**
 * The best counted win posted within `day`, while its map was embargoed: the
 * day's own winner, which later scores (made with the replays out) can beat
 * on the standings but never take from it. Null when nobody won on the day.
 */
export function bestOnTheDay(rows, day) {
  return standings(rows.filter((row) => postedOn(row.submitted_at, day)))[0] || null;
}

/** Whether a score posted at `submittedAt` was posted within `day` (UTC). */
export function postedOn(submittedAt, day) {
  return dayParam(submittedAt) === day;
}

/**
 * One player's running streak of each kind, as the daily page counts them.
 * `scores` are the player's counted scores (`{game_key, hand, submitted_at}`),
 * `dailyGames` the `daily_games` rows (`{day, game_key, kind}`) of those maps.
 * Returns `{board, puzzle}`, each `{days, onTheDay}` (0 when none is running).
 */
export function playerStreaks(scores, dailyGames, today) {
  const won = playerDays(scores, dailyGames);
  const out = {};
  for (const kind of KINDS) {
    const wins = [...won[kind]].map(([day, how]) => ({ name: "", day, onTheDay: how === "day" }));
    const [run] = streaks(wins, today);
    out[kind] = run ? { days: run.days, onTheDay: run.onTheDay } : { days: 0, onTheDay: 0 };
  }
  return out;
}

/**
 * Which days' dailies one player has won, of each kind, from the same inputs
 * as playerStreaks: `{board, puzzle}`, each a Map of day to `"day"` (a counted
 * win posted within the day) or `"later"` (won only from the archive). The
 * player card's streak calendar draws these.
 */
export function playerDays(scores, dailyGames) {
  const out = {};
  for (const kind of KINDS) {
    const dayOf = new Map(dailyGames.filter((g) => (g.kind || "board") === kind).map((g) => [g.game_key, g.day]));
    const won = new Map();
    for (const s of scores) {
      if (!(s.hand > 0) || !dayOf.has(s.game_key)) continue;
      const day = dayOf.get(s.game_key);
      if (postedOn(s.submitted_at, day)) won.set(day, "day");
      else if (!won.has(day)) won.set(day, "later");
    }
    out[kind] = won;
  }
  return out;
}

/**
 * A map that was a day's daily (a `daily_games` row, `{day, kind}`), for
 * game.html's badge and button: `{label, button, href}`, the day's own page.
 */
export function dailyMark(entry, today) {
  const puzzle = entry.kind === "puzzle";
  const page = puzzle ? "puzzle.html" : "daily.html";
  return {
    label: `${puzzle ? "Daily puzzle" : "Daily"} · ${entry.day === today ? "Today" : shortDate(entry.day)}`,
    button: puzzle ? "Daily puzzle page" : "Daily page",
    href: entry.day === today ? page : `${page}?day=${entry.day}`,
  };
}

/**
 * The congratulation for a puzzle score that met its par, in the game's words
 * (`starconquest/daily.py`'s `par_note`; the two are kept alike by
 * tests/test_daily.py). `par` is `{turns, lost, status, turns_only}`; "" for a
 * score short of it.
 */
export function parNote(par, turns, lost) {
  if (!par) return "";
  const worse = turns > par.turns || (turns === par.turns && lost > par.lost);
  if (worse) return "";
  const score = `${par.turns} turns / ${par.lost} lost`;
  if (turns < par.turns) return `Beyond par! You won faster than the par of ${score}`;
  if (lost < par.lost) return `Beyond par! The par's ${par.turns} turns with fewer ships lost than its ${par.lost}`;
  const proved = par.status === "proved";
  if (proved && !par.turns_only) return `Perfect puzzle! You matched the proven best: ${score}`;
  if (proved) return `Perfect puzzle! You matched the par of ${score}, proven the fastest win`;
  return `Par! You matched the best line our search found: ${score}`;
}
