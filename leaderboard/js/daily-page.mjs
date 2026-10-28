// daily.html and puzzle.html: a day's daily challenge of one kind, the board
// or the puzzle (`#today`'s data-kind), its standings and streaks
// (docs/design/daily.md). Everything is derived from `dailies`, `daily_games`,
// `game_summary` and `counted_scores`; nothing is stored for it. Each kind
// keeps its own streaks.
//
// Today's map is embargoed until midnight UTC (`game_embargoes`), so for today
// this page shows what game.html's renderEmbargoed shows: who is ahead and by
// how many turns, never the other scores. It never even fetches today's turns
// or ships lost: only the names that have won, for the streaks.

import { configured, inList, select } from "./api.mjs";
import {
  addDays, bestOnTheDay, dayParam, opponents, postedOn, shortDate, standings, streaks, validDay, weekdayOf,
  WEEKDAYS,
} from "./daily.mjs";
import { clear, el, leaderCredit, setupSummary, showError, userHref } from "./format.mjs";
import { mountMyScores } from "./me.mjs";
import { mountNav } from "./nav.mjs";

// How far back streaks are read. A longer streak shows as this many days.
const STREAK_DAYS = 60;

const daysNav = document.getElementById("days");
const todayBox = document.getElementById("today");
const standingsTitle = document.getElementById("standings-title");
const standingsBox = document.getElementById("standings");
const streaksBox = document.getElementById("streaks");

const kind = todayBox.dataset.kind === "puzzle" ? "puzzle" : "board";
const page = kind === "puzzle" ? "puzzle.html" : "daily.html";
const fragment = kind === "puzzle" ? "puzzle" : "daily";
const noun = kind === "puzzle" ? "puzzle" : "daily";

const today = dayParam(Date.now());
const asked = new URLSearchParams(location.search).get("day");
const day = validDay(asked) && asked < today ? asked : today;
const isToday = day === today;

const dayHref = (d) => (d === today ? page : `${page}?day=${d}`);
const playHref = (d) => (d === today ? `../game/#${fragment}` : `../game/#${fragment}=${d}`);

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
      ? `Today's ${noun} isn't ready yet. It is made on the hour; try again shortly.`
      : `There was no ${noun} that day.` }));
    return;
  }
  const bots = opponents(row.settings);
  clear(todayBox).append(el("div", { class: "card" }, [
    el("div", { class: "card-body" }, [
      el("div", { class: "card-main" }, [
        el("strong", { text: `${WEEKDAYS[weekdayOf(day)]}'s ${kind === "puzzle" ? "puzzle" : "map"}` }),
        el("div", { class: "card-meta", text: setupSummary(asGame(row)) + (bots.length ? ` · vs ${bots.join(", ")}` : "") }),
        keys.length
          ? el("div", { class: "card-meta" }, [el("a", { href: `game.html?key=${encodeURIComponent(keys[0])}`, text: "Map page" })])
          : null,
      ].filter(Boolean)),
      el("a", { class: "btn play", href: playHref(day), text: "▶ Play" }),
    ]),
  ]));
}

/** Today, under embargo: who is ahead and by how many turns, from
 * `game_summary`'s aggregate, as game.html shows an embargoed map. */
function drawLeader(summaries) {
  standingsTitle.textContent = "Today's standings";
  const ranked = summaries.filter((s) => s.best_turns != null)
    .sort((a, b) => a.best_turns - b.best_turns || (a.best_lost ?? 0) - (b.best_lost ?? 0));
  const posted = summaries.reduce((n, s) => n + (s.score_count || 0), 0);
  const hidden = "Every other score, and every replay, stays hidden until the day ends at midnight UTC; "
    + "whoever is ahead then is the day's winner.";
  clear(standingsBox).append(ranked.length
    ? el("p", { class: "lede" }, [
      "Currently ahead: ", el("strong", { text: leaderCredit(ranked[0]) }),
      ` — ${ranked[0].best_turns} turns, of ${posted} ${posted === 1 ? "score" : "scores"} posted. ${hidden}`,
    ])
    : el("p", { class: "empty", text: posted
      ? `No verified score yet. ${hidden}`
      : "Nobody has posted a win yet today. The best score posted before midnight UTC wins the day." }));
}

/** A past day: every counted win, the best on the day marked as the day's
 * winner, and scores posted after the day (with its replays out) marked as
 * later. */
function drawStandings(rows) {
  standingsTitle.textContent = `Standings, ${shortDate(day)}`;
  const table = standings(rows);
  const winner = bestOnTheDay(rows, day);
  clear(standingsBox).append(table.length
    ? el("div", { class: "tally" }, table.map((row, i) => el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.user_name]), text: `${i + 1}. ${row.user_name}` }),
      el("span", { class: "tally-figs" }, [
        `${row.turns} turns · ${row.lost} lost`,
        winner && winner.user_name === row.user_name
          ? el("span", { class: "at-reveal", title: "The best score posted on the day, before its replays were out", text: "Best on the day" })
          : null,
        postedOn(row.submitted_at, day)
          ? null
          : el("span", { class: "daily-later", title: "Posted after the day, once its replays were out", text: "later" }),
      ].filter(Boolean)),
    ])))
    : el("p", { class: "empty", text: "Nobody has posted a win on that day's map yet. It is still playable." }));
}

/** Streaks: the mended count, with the true one (days won on the day itself)
 * in the title, which a mouse shows on hover and a tap opens (tip.mjs). */
function drawStreaks(list) {
  const days = (n) => `${n} ${n === 1 ? "day" : "days"}`;
  clear(streaksBox).append(list.length
    ? el("div", { class: "tally" }, list.map((row) => el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.name]), text: row.name }),
      el("span", {
        class: "tally-figs",
        title: row.onTheDay === row.days
          ? `True streak: every one of the ${days(row.days)} won on the day itself`
          : `True streak: ${days(row.onTheDay)} won on the day itself; the rest won later from the archive`,
        text: row.onTheDay === row.days ? days(row.days) : `${days(row.days)} (${row.onTheDay} true)`,
      }),
    ])))
    : el("p", { class: "empty", text: "No streaks running. Win today's map to start one, or a missed day's to mend one." }));
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
    const [rows, games] = await Promise.all([
      select(`dailies?select=day,settings&day=eq.${day}&kind=eq.${kind}`),
      select(`daily_games?select=day,game_key&kind=eq.${kind}&day=gte.${from < day ? from : day}`),
    ]);
    drawToday(rows[0], games.filter((g) => g.day === day).map((g) => g.game_key));

    // Today's map is embargoed: its names only (for the streaks), and its
    // standing from game_summary's aggregate. Every past day's in full.
    const dayOfKey = new Map(games.map((g) => [g.game_key, g.day]));
    const todayKeys = [...dayOfKey].filter(([, d]) => d === today).map(([k]) => k);
    const pastKeys = [...dayOfKey].filter(([, d]) => d !== today).map(([k]) => k);
    const [past, todays, summaries] = await Promise.all([
      pastKeys.length
        ? select(`counted_scores?select=game_key,user_name,turns,lost,hand,submitted_at&game_key=${inList(pastKeys)}`)
        : [],
      todayKeys.length
        ? select(`counted_scores?select=game_key,user_name,hand,submitted_at&game_key=${inList(todayKeys)}`)
        : [],
      isToday && todayKeys.length
        ? select(`game_summary?select=game_key,best_turns,best_lost,best_user_name,best_holders,score_count&game_key=${inList(todayKeys)}`)
        : [],
    ]);
    if (isToday) drawLeader(summaries);
    else drawStandings(past.filter((s) => dayOfKey.get(s.game_key) === day));
    const wins = [...past, ...todays]
      .filter((s) => s.hand > 0)
      .map((s) => ({ name: s.user_name, day: dayOfKey.get(s.game_key),
        onTheDay: postedOn(s.submitted_at, dayOfKey.get(s.game_key)) }));
    drawStreaks(streaks(wins, today));
  } catch (err) {
    todayBox.classList.remove("loading");
    showError(todayBox, err.message);
  }
}

load();
