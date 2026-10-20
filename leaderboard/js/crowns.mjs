// Crowns and steals: the board's weekly contest (crowns.html).
//
// A crown is a contested map — two or more players have posted on it — whose
// record you hold right now. A steal is a score that took a record from somebody
// else. Who holds what, and which scores stole, is decided in SQL
// (`crown_holders` / `crown_steals` in schema.sql); this module only counts and
// orders what those views return, and picks the week. No DOM and no fetch, so
// tests/ can exercise it directly, the same reason standings.mjs has none.

const DAY_MS = 86400000;

/** The Monday 00:00 UTC on or before `date` — the week a steal is counted in. */
export function weekStart(date) {
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const sinceMonday = (day.getUTCDay() + 6) % 7;   // getUTCDay: Sunday is 0
  return new Date(day.getTime() - sinceMonday * DAY_MS);
}

/** The Monday a week after `start`. */
export function weekAfter(start) {
  return new Date(start.getTime() + 7 * DAY_MS);
}

/** The Monday a week before `start`. */
export function weekBefore(start) {
  return new Date(start.getTime() - 7 * DAY_MS);
}

/** "2026-09-21" — a week as the page's `?week=` parameter spells it. */
export function weekParam(start) {
  return start.toISOString().slice(0, 10);
}

/**
 * The week a `?week=` value names, snapped to its Monday so any day in it works,
 * or `fallback` for a missing or unreadable one. Never later than `fallback`'s
 * week: nothing has been stolen in a week that hasn't started.
 */
export function parseWeek(value, fallback) {
  const current = weekStart(fallback);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  if (!match) return current;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime())) return current;
  const start = weekStart(date);
  return start > current ? current : start;
}

/** Steals whose submission falls in [from, to). */
export function stealsIn(steals, from, to) {
  return steals.filter((steal) => {
    const at = Date.parse(steal.submitted_at);
    return at >= from.getTime() && at < to.getTime();
  });
}

/**
 * One row per player with a crown now or a steal in `steals`:
 * `{key, name, crowns, steals}`, most crowns first, then most steals, then name.
 *
 * Crowns lead because they are the standing — what you hold after everybody has
 * had their go — and a steal counts in the week it happened even if it has since
 * been stolen back, which is exactly the fight the page exists to reward.
 * Players are keyed by folded name, the way `users.name_key` folds them.
 */
export function crownTable(holders, steals) {
  const rows = new Map();
  const row = (name) => {
    const key = name.trim().toLowerCase();
    let entry = rows.get(key);
    if (!entry) rows.set(key, (entry = { key, name, crowns: 0, steals: 0 }));
    return entry;
  };
  for (const holder of holders) row(holder.user_name).crowns += 1;
  for (const steal of steals) row(steal.taker_name).steals += 1;
  return [...rows.values()].sort(
    (a, b) => b.crowns - a.crowns || b.steals - a.steals || a.name.localeCompare(b.name),
  );
}

/** A week's steals newest first, for the feed. */
export function stealFeed(steals) {
  return [...steals].sort((a, b) => Date.parse(b.submitted_at) - Date.parse(a.submitted_at));
}
