// daily.html: a day's daily challenge, its standings, the week, and streaks
// (docs/design/daily.md). Everything is derived from `dailies`, `daily_games`
// and `counted_scores`; nothing is stored for it.

import { configured, inList, select } from "./api.mjs";
import {
  addDays, dayParam, postedOn, shortDate, standings, streaks, systemsOn, validDay, weekdayOf, WEEKDAYS,
} from "./daily.mjs";
import { clear, el, setupSummary, showError, userHref } from "./format.mjs";
import { mountMyScores } from "./me.mjs";
import { mountNav } from "./nav.mjs";

// How far back streaks are read. A longer streak shows as this many days.
const STREAK_DAYS = 60;

const daysNav = document.getElementById("days");
const todayBox = document.getElementById("today");
const weekBox = document.getElementById("week");
const standingsTitle = document.getElementById("standings-title");
const standingsBox = document.getElementById("standings");
const streaksBox = document.getElementById("streaks");

const today = dayParam(Date.now());
const asked = new URLSearchParams(location.search).get("day");
const day = validDay(asked) && asked < today ? asked : today;
const isToday = day === today;

const dayHref = (d) => (d === today ? "daily.html" : `daily.html?day=${d}`);
const playHref = (d) => (d === today ? "../game/#daily" : `../game/#daily=${d}`);

function drawDays() {
  clear(daysNav).append(
    el("a", { class: "btn", href: dayHref(addDays(day, -1)), "aria-label": "Previous day", text: "‹" }),
    el("span", { class: "week-label", text: isToday ? `Today · ${shortDate(day)}` : shortDate(day) }),
    isToday
      ? el("span", { class: "btn ghost", "aria-hidden": "true", text: "›" })
      : el("a", { class: "btn", href: dayHref(addDays(day, 1)), "aria-label": "Next day", text: "›" }),
  );
}

/** A `dailies` row as the summary helpers read a games row. */
const asGame = (row) => ({ ...row.settings, settings_json: row.settings });

function drawToday(row, keys) {
  todayBox.classList.remove("loading");
  if (!row) {
    clear(todayBox).append(el("p", { class: "empty", text: isToday
      ? "Today's daily isn't ready yet. It is made on the hour; try again shortly."
      : "There was no daily that day." }));
    return;
  }
  const settings = row.settings;
  const bots = [];
  for (let seat = 2; seat <= (settings.players || 2); seat += 1) {
    const name = (settings.ai_strategy || [])[seat - 1];
    if (name && !bots.includes(name)) bots.push(name);
  }
  clear(todayBox).append(el("div", { class: "card" }, [
    el("div", { class: "card-body" }, [
      el("div", { class: "card-main" }, [
        el("strong", { text: `${WEEKDAYS[weekdayOf(day)]}'s map` }),
        el("div", { class: "card-meta", text: setupSummary(asGame(row)) + (bots.length ? ` · vs ${bots.join(", ")}` : "") }),
        keys.length
          ? el("div", { class: "card-meta" }, [el("a", { href: `game.html?key=${encodeURIComponent(keys[0])}`, text: "Map page" })])
          : null,
      ].filter(Boolean)),
      el("a", { class: "btn play", href: playHref(day), text: "▶ Play" }),
    ]),
  ]));
}

function drawWeek(made) {
  const monday = addDays(day, -weekdayOf(day));
  clear(weekBox).append(el("div", { class: "tally" }, WEEKDAYS.map((name, w) => {
    const d = addDays(monday, w);
    const [lo, hi] = systemsOn(w);
    const label = `${name.slice(0, 3)} · ${lo}–${hi} systems`;
    return el("div", { class: "tally-row" }, [
      made.has(d) && d <= today
        ? el("a", { class: "tally-name", href: dayHref(d), text: label })
        : el("span", { class: "tally-name", text: label }),
      el("span", { class: "tally-figs", text: d === day ? "◀" : "" }),
    ]);
  })));
}

function drawStandings(rows) {
  standingsTitle.textContent = isToday ? "Today's standings" : `Standings, ${shortDate(day)}`;
  const table = standings(rows);
  clear(standingsBox).append(table.length
    ? el("div", { class: "tally" }, table.map((row, i) => el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.user_name]), text: `${i + 1}. ${row.user_name}` }),
      el("span", { class: "tally-figs", text: `${row.turns} turns · ${row.lost} lost` }),
    ])))
    : el("p", { class: "empty", text: isToday ? "Nobody has posted a win yet today." : "Nobody posted a win that day." }));
}

function drawStreaks(list) {
  clear(streaksBox).append(list.length
    ? el("div", { class: "tally" }, list.map((row) => el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.name]), text: row.name }),
      el("span", { class: "tally-figs", text: `${row.days} ${row.days === 1 ? "day" : "days"}` }),
    ])))
    : el("p", { class: "empty", text: "No streaks running. Win today's map on the day to start one." }));
}

async function load() {
  mountNav();
  mountMyScores();
  drawDays();
  if (!configured()) {
    showError(todayBox, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  try {
    const from = addDays(today, -STREAK_DAYS);
    const monday = addDays(day, -weekdayOf(day));
    const [rows, made, games] = await Promise.all([
      select(`dailies?select=day,settings&day=eq.${day}`),
      select(`dailies?select=day&day=gte.${monday}&day=lte.${addDays(monday, 6)}`),
      select(`daily_games?select=day,game_key&day=gte.${from < monday ? from : monday}`),
    ]);
    drawToday(rows[0], games.filter((g) => g.day === day).map((g) => g.game_key));
    drawWeek(new Set(made.map((r) => r.day)));

    const dayOfKey = new Map(games.map((g) => [g.game_key, g.day]));
    const scores = dayOfKey.size
      ? await select(`counted_scores?select=game_key,user_name,turns,lost,hand,submitted_at&game_key=${inList([...dayOfKey.keys()])}`)
      : [];
    drawStandings(scores.filter((s) => dayOfKey.get(s.game_key) === day));
    const wins = scores
      .filter((s) => s.hand > 0 && postedOn(s.submitted_at, dayOfKey.get(s.game_key)))
      .map((s) => ({ name: s.user_name, day: dayOfKey.get(s.game_key) }));
    drawStreaks(streaks(wins, today));
  } catch (err) {
    todayBox.classList.remove("loading");
    showError(todayBox, err.message);
  }
}

load();
