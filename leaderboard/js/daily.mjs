// The daily challenge (docs/design/daily.md): what the page and /api/daily
// share. A day is a UTC date; a row of `dailies` is made a day ahead by
// tools/daily.py and readable once its day has started.
//
// Pure: no DOM, no fetch.

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86400000;
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

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

/** The day's system count range: Monday 7-12, three more each day. */
export function systemsOn(weekday) {
  return [7 + 3 * weekday, 12 + 3 * weekday];
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

/**
 * Each name's streak of consecutive days won, ending on `today` or the day
 * before (a streak not yet extended today is still alive). `wins` is
 * `[{name, day}]`: a counted win on a day's map, posted within that day.
 * Returns `[{name, days}]`, longest first, then by name.
 */
export function streaks(wins, today) {
  const byName = new Map();
  for (const { name, day } of wins) {
    if (!byName.has(name)) byName.set(name, new Set());
    byName.get(name).add(day);
  }
  const out = [];
  for (const [name, days] of byName) {
    let day = days.has(today) ? today : addDays(today, -1);
    let n = 0;
    while (days.has(day)) {
      n += 1;
      day = addDays(day, -1);
    }
    if (n > 0) out.push({ name, days: n });
  }
  return out.sort((a, b) => b.days - a.days || a.name.localeCompare(b.name));
}

/** Whether a score posted at `submittedAt` was posted within `day` (UTC). */
export function postedOn(submittedAt, day) {
  return dayParam(submittedAt) === day;
}
