// The Streaks panel on a player card (user.html): the player's running daily
// streaks, each kind counted as daily.html and puzzle.html count them
// (daily.mjs's playerStreaks). Each kind shows this week as a bar of seven
// days; a tap on the bar opens a month calendar of every day won, on the day
// or later from the archive. On the player's own card (signed in, owning the
// name) a puzzle day whose par they met is gold, striped when they matched only
// its turns (schema.sql's sc_my_par_days); nobody else's card asks. Optional, like the Badges panel: a read that
// fails leaves the panel hidden and never touches the rest of the card.

import { eq, inList, rpc, select } from "./api.mjs";
import { storedSession } from "./auth.mjs";
import {
  WEEKDAYS, addDays, addMonths, dayParam, monthWeeks, parDays, playerDays, playerStreaks, shortDate, weekDays,
} from "./daily.mjs";
import { clear, el } from "./format.mjs";

// game_keys per query, so each stays one short URL (as badges-card.mjs).
const CHUNK = 80;

const KIND_ROWS = [
  { kind: "puzzle", title: "Daily puzzle", href: "puzzle.html" },
  { kind: "board", title: "Daily map", href: "daily.html" },
];

const STATE_TEXT = {
  day: "won on the day",
  later: "won later, from the archive",
  miss: "not won",
  today: "today, not won yet",
  future: "still to come",
  none: "before the dailies began",
};

const PAR_TEXT = { par: ", met par", "par-turns": ", matched par's turns" };

// sc_my_par_days answers at most this many days in one call.
const PAR_SPAN = 400;

/** The days the signed-in viewer met the par, when `nameKey` is their own
 * claimed name; otherwise (or on any failure) none. */
async function ownParDays(nameKey, from, to) {
  if (!storedSession()) return new Map();
  try {
    const mine = await rpc("my_name", {});
    if (!mine || mine.trim().toLowerCase() !== nameKey) return new Map();
    return parDays(await rpc("sc_my_par_days", { p_from: from, p_to: to }));
  } catch {
    return new Map();
  }
}

const days = (n) => `${n} ${n === 1 ? "day" : "days"}`;
const monthName = (day) => new Date(`${day}T00:00:00Z`)
  .toLocaleString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

/** Draw one player's running streaks into `box`, or leave it hidden. */
export async function drawStreaks(box, nameKey) {
  const today = dayParam(Date.now());
  let found, won, firstDay, par;
  try {
    const users = await select(`users?select=id&name_key=${eq(nameKey)}`);
    if (!users.length) return;
    const [scores, first] = await Promise.all([
      select(`counted_scores?select=game_key,hand,submitted_at&user_id=eq.${users[0].id}&limit=5000`),
      // The first daily ever dealt: no day before it can have been missed.
      select("daily_games?select=day&order=day.asc&limit=1").catch(() => []),
    ]);
    const keys = [...new Set(scores.map((s) => s.game_key))];
    const pages = [];
    for (let i = 0; i < keys.length; i += CHUNK) {
      pages.push(select(`daily_games?select=day,game_key,kind&game_key=${inList(keys.slice(i, i + CHUNK))}`));
    }
    const games = (await Promise.all(pages)).flat();
    found = playerStreaks(scores, games, today);
    won = playerDays(scores, games);
    firstDay = first.length ? first[0].day : null;
    const puzzleDays = [...won.puzzle.keys()].sort();
    const floor = addDays(today, -PAR_SPAN);
    par = puzzleDays.length
      ? await ownParDays(nameKey, puzzleDays[0] > floor ? puzzleDays[0] : floor, today)
      : new Map();
  } catch {
    return;
  }
  const stateOf = (map) => (day) => {
    if (map.has(day)) return map.get(day);
    if (day > today) return "future";
    if (firstDay && day < firstDay) return "none";
    return day === today ? "today" : "miss";
  };
  clear(box).append(
    el("h2", { class: "section-title", text: "Daily streaks" }),
    el("div", { class: "tally streaks" }, KIND_ROWS.map((row) => streakRow(row, found[row.kind], won[row.kind],
      stateOf(won[row.kind]), row.kind === "puzzle" ? par : new Map(), today))),
  );
  box.hidden = false;
}

function streakRow({ kind, title, href }, run, wins, stateOf, par, today) {
  // A par day is a won day too: the gold class goes beside "day" or "later".
  const classOf = (day) => `${stateOf(day)}${par.has(day) ? ` ${par.get(day)}` : ""}`;
  const textOf = (day) => `${STATE_TEXT[stateOf(day)]}${PAR_TEXT[par.get(day)] || ""}`;
  const figs = el("span", run.days
    ? {
      class: "tally-figs",
      // As on the daily page: the mended count, the true one on hover.
      title: run.onTheDay === run.days
        ? `True streak: every one of the ${days(run.days)} won on the day itself`
        : `True streak: ${days(run.onTheDay)} won on the day itself; the rest won later from the archive`,
      text: run.onTheDay === run.days ? days(run.days) : `${days(run.days)} (${run.onTheDay} true)`,
    }
    : { class: "tally-figs", text: "No streak running" });

  const calId = `streak-cal-${kind}`;
  const bar = el("button", {
    class: "streak-week", type: "button", "aria-expanded": "false", "aria-controls": calId,
    "aria-label": `${title}, this week: ${weekDays(today).map((d, i) => `${WEEKDAYS[i]} ${textOf(d)}`).join("; ")}. Show the calendar`,
  }, [
    ...weekDays(today).map((day, i) => el("span", { class: "wk-day", "aria-hidden": "true" }, [
      el("span", { class: "wk-letter", text: WEEKDAYS[i][0] }),
      el("span", { class: `pip ${classOf(day)}${day === today ? " is-today" : ""}` }),
    ])),
    el("span", { class: "wk-more", "aria-hidden": "true", text: "▾" }),
  ]);

  // Back as far as the first month with a win; forward to this month.
  const earliest = wins.size ? [...wins.keys()].sort()[0] : today;
  const cal = el("div", { class: "streak-cal", id: calId, hidden: "" });
  let month = addMonths(today, 0);
  const drawCal = () => {
    const prev = el("button", { class: "cal-nav", type: "button", "aria-label": "Previous month", text: "‹" });
    const next = el("button", { class: "cal-nav", type: "button", "aria-label": "Next month", text: "›" });
    prev.disabled = month <= addMonths(earliest, 0);
    next.disabled = month >= addMonths(today, 0);
    prev.addEventListener("click", () => { month = addMonths(month, -1); drawCal(); });
    next.addEventListener("click", () => { month = addMonths(month, 1); drawCal(); });
    const inMonth = [...wins].filter(([day]) => day.startsWith(month.slice(0, 7)));
    clear(cal).append(
      el("div", { class: "cal-head" }, [prev, el("span", { class: "cal-month", text: monthName(month) }), next]),
      el("div", { class: "cal-grid", role: "grid" }, [
        el("div", { class: "cal-row", role: "row" },
          WEEKDAYS.map((name) => el("span", { class: "cal-dow", role: "columnheader", title: name, text: name.slice(0, 2) }))),
        ...monthWeeks(month).map((week) => el("div", { class: "cal-row", role: "row" }, week.map((day) => {
          if (!day) return el("span", { class: "cal-day blank", role: "gridcell" });
          const state = stateOf(day);
          const props = {
            class: `cal-day ${classOf(day)}${day === today ? " is-today" : ""}`,
            role: "gridcell",
            title: `${shortDate(day)}: ${textOf(day)}`,
            text: String(Number(day.slice(8))),
          };
          // A past or present day opens that day's page; a future one has none.
          return state === "future" || state === "none"
            ? el("span", props)
            : el("a", { ...props, href: day === today ? href : `${href}?day=${day}` });
        }))),
      ]),
      el("p", { class: "cal-foot" }, [
        `${inMonth.length} won this month · `,
        el("span", { class: "pip day", "aria-hidden": "true" }), " on the day ",
        el("span", { class: "pip later", "aria-hidden": "true" }), " later ",
        el("span", { class: "pip miss", "aria-hidden": "true" }), " missed",
        ...(par.size ? [
          " ", el("span", { class: "pip day par", "aria-hidden": "true" }), " par ",
          el("span", { class: "pip day par-turns", "aria-hidden": "true" }), " par's turns",
        ] : []),
      ]),
    );
  };
  bar.addEventListener("click", () => {
    const open = cal.hidden;
    if (open) drawCal();
    cal.hidden = !open;
    bar.setAttribute("aria-expanded", String(open));
  });

  return el("div", { class: "streak" }, [
    el("div", { class: "tally-row" }, [el("a", { class: "tally-name", href, text: title }), figs]),
    bar,
    cal,
  ]);
}
