import { configured, inList, select } from "./api.mjs";
import { clear, el, mapSummary, relativeTime, showError, userHref } from "./format.mjs";
import {
  crownTable, parseWeek, stealFeed, weekAfter, weekBefore, weekParam, weekStart,
} from "./crowns.mjs";
import { mountMyScores } from "./me.mjs";
import { mountNav } from "./nav.mjs";

const weeksNav = document.getElementById("weeks");
const target = document.getElementById("standings");
const feedTitle = document.getElementById("feed-title");
const feedBox = document.getElementById("feed");

const now = new Date();
const thisWeek = weekStart(now);
const week = parseWeek(new URLSearchParams(location.search).get("week"), now);
const current = week.getTime() === thisWeek.getTime();

/** "21 Sep" — a week's Monday, as the navigator labels it. */
const dayLabel = (date) =>
  date.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });

const weekHref = (start) =>
  start.getTime() === thisWeek.getTime() ? "crowns.html" : `crowns.html?week=${weekParam(start)}`;

function drawWeeks() {
  const label = current ? "This week" : `Week of ${dayLabel(week)}`;
  clear(weeksNav).append(
    el("a", { class: "btn", href: weekHref(weekBefore(week)), "aria-label": "Previous week", text: "‹" }),
    el("span", { class: "week-label", text: label }),
    current
      ? el("span", { class: "btn ghost", "aria-hidden": "true", text: "›" })
      : el("a", { class: "btn", href: weekHref(weekAfter(week)), "aria-label": "Next week", text: "›" }),
  );
}

function figures(row) {
  const crowns = `${row.crowns} ${row.crowns === 1 ? "crown" : "crowns"}`;
  const steals = `${row.steals} ${row.steals === 1 ? "steal" : "steals"}`;
  return `${crowns} · ${steals}`;
}

function drawStandings(rows) {
  target.classList.remove("loading");
  if (!rows.length) {
    clear(target).append(el("p", { class: "empty" }, [
      "No contested maps yet. Find a map somebody else has posted on in ",
      el("a", { href: "index.html", text: "the list" }),
      " and beat it.",
    ]));
    return;
  }
  clear(target).append(el("div", { class: "tally" }, rows.map((row) =>
    el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.name]), text: row.name }),
      el("span", { class: "tally-figs", text: figures(row) }),
    ]))));
}

function feedItem(steal, games) {
  const game = games.get(steal.game_key);
  return el("li", { class: "steal" }, [
    el("a", { href: userHref([steal.taker_name]), text: steal.taker_name }),
    " took ",
    el("a", {
      href: `game.html?key=${encodeURIComponent(steal.game_key)}`,
      text: game ? mapSummary(game) : "a map",
    }),
    " from ",
    el("a", { href: userHref([steal.from_name]), text: steal.from_name }),
    `, ${steal.from_turns} → ${steal.turns} turns `,
    el("span", { class: "when", text: relativeTime(steal.submitted_at) }),
  ]);
}

function drawFeed(steals, games) {
  feedTitle.textContent = current ? "Steals this week" : `Steals, week of ${dayLabel(week)}`;
  clear(feedBox).append(steals.length
    ? el("ol", { class: "steals" }, stealFeed(steals).map((steal) => feedItem(steal, games)))
    : el("p", { class: "empty", text: current ? "Nothing stolen yet this week." : "Nothing was stolen that week." }));
}

async function load() {
  mountNav();
  mountMyScores();
  drawWeeks();
  if (!configured()) {
    showError(target, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  try {
    const from = encodeURIComponent(week.toISOString());
    const to = encodeURIComponent(weekAfter(week).toISOString());
    const [holders, steals] = await Promise.all([
      select("crown_holders?select=game_key,user_name"),
      select(
        "crown_steals?select=game_key,submitted_at,taker_name,from_name,turns,from_turns" +
          `&submitted_at=gte.${from}&submitted_at=lt.${to}&order=submitted_at.desc`,
      ),
    ]);
    const keys = [...new Set(steals.map((steal) => steal.game_key))];
    const games = keys.length
      ? await select(`games?select=game_key,mode,players,nodes,seed&game_key=${inList(keys)}`)
      : [];
    drawStandings(crownTable(holders, steals));
    drawFeed(steals, new Map(games.map((game) => [game.game_key, game])));
  } catch (err) {
    target.classList.remove("loading");
    showError(target, err.message);
  }
}

load();
