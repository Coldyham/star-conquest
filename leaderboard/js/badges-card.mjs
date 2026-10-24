// The Badges panel on a player card (user.html): the reads behind badges.mjs,
// and the drawing. Every rule lives in badges.mjs; this only fetches the rows
// it judges and lays out the answer.
//
// Optional, like the Playstyle panel: a read that fails leaves its badges
// unknown (the two function counts) or the panel hidden (the board's own
// tables), and never touches the rest of the card.

import { eq, inList, select } from "./api.mjs";
import {
  badgeList, campaignRecord, drawingOf, drawingsOpened, factsFrom, firsts, gauntletsCleared, grouped,
  stealWeeksTopped,
} from "./badges.mjs";
import { weekQueries } from "./campaign.mjs";
import { weekAfter, weekStart } from "./crowns.mjs";
import { clear, el } from "./format.mjs";

// game_keys per query, so each stays one short URL (as user.mjs's readings).
const CHUNK = 80;

/** One query per CHUNK of `values`, the rows run together. */
async function chunked(values, query) {
  const list = [...new Set(values)];
  const pages = [];
  for (let i = 0; i < list.length; i += CHUNK) pages.push(select(query(inList(list.slice(i, i + CHUNK)))));
  return (await Promise.all(pages)).flat();
}

/** A count one of the functions answers, or null when it can't be read. */
async function count(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

const FIELD = "id,game_key,user_id,submitted_at";

async function campaignWeeks(userId) {
  const rows = await select(`campaign_scores?select=week_start&user_id=eq.${userId}&limit=5000`);
  const days = [...new Set(rows.map((row) => row.week_start))];
  return Promise.all(days.map(async (day) => {
    const queries = weekQueries(day);
    const [graphs, scores] = await Promise.all([select(queries.graph), select(queries.scores)]);
    return graphs.length ? { week_start: day, graph: graphs[0].graph, scores } : null;
  })).then((weeks) => weeks.filter(Boolean));
}

/** Every crown_steals row over the weeks `name` stole in, for stealWeeksTopped. */
async function stealWeeks(name, now) {
  const mine = await select(`crown_steals?select=submitted_at&taker_name=${eq(name)}&order=submitted_at.asc`);
  if (!mine.length) return { steals: 0, stealWeeks: 0 };
  const from = weekStart(new Date(mine[0].submitted_at));
  const to = weekAfter(weekStart(new Date(mine[mine.length - 1].submitted_at)));
  const all = await select(
    `crown_steals?select=taker_name,submitted_at&submitted_at=gte.${encodeURIComponent(from.toISOString())}` +
      `&submitted_at=lt.${encodeURIComponent(to.toISOString())}&limit=10000`,
  );
  return { steals: mine.length, stealWeeks: stealWeeksTopped(all, name, now) };
}

/** How many hand-drawn maps `userId` posted the first win on, over every seed of each. */
async function drawings(userId, wins) {
  const mine = new Set(wins.map((win) => drawingOf(win.settings_json?.custom_map)).filter(Boolean));
  if (!mine.size) return 0;
  const games = (await select(
    "games?select=game_key,custom_map:settings_json->custom_map" +
      "&settings_json->custom_map=not.is.null&limit=5000",
  )).filter((game) => mine.has(drawingOf(game.custom_map)));
  const field = await chunked(games.map((game) => game.game_key),
    (keys) => `counted_scores?select=${FIELD}&game_key=${keys}&limit=10000`);
  return drawingsOpened(userId, games, field);
}

/** Everything badgeList needs for one player, or null for a name never posted. */
export async function loadFacts(nameKey, now = new Date()) {
  const users = await select(`users?select=id,name&name_key=${eq(nameKey)}`);
  if (!users.length) return null;
  const { id, name } = users[0];

  const mine = await select(`counted_scores?select=${FIELD}&user_id=eq.${id}&limit=5000`);
  const keys = mine.map((score) => score.game_key);
  const [setups, field, steals, weeks, listed, pbp, made] = await Promise.all([
    chunked(keys, (list) => `game_summary?select=game_key,mode,settings_json,bots&game_key=${list}`),
    chunked(keys, (list) => `counted_scores?select=${FIELD}&game_key=${list}&limit=10000`),
    stealWeeks(name, now),
    campaignWeeks(id),
    // Optional: a board whose schema predates gauntlets has no such table, and
    // that leaves one badge unknown rather than the panel gone.
    select("gauntlets?select=gauntlet_id,stages:body->stages&listed=is.true&limit=1000").catch(() => null),
    count(`/api/pbp?action=record&name=${encodeURIComponent(name)}`),
    count(`/api/gauntlet?by=${encodeURIComponent(name)}`),
  ]);

  const byKey = new Map(setups.map((game) => [game.game_key, game]));
  const wins = [...new Set(keys)].map((key) => byKey.get(key)).filter(Boolean);
  const { opened, joined } = firsts(id, field);
  return factsFrom({
    wins,
    opened,
    joined,
    drawings: await drawings(id, wins),
    ...steals,
    campaign: campaignRecord(weeks, name, now),
    gauntletsMade: made && Number.isInteger(made.made) ? made.made : null,
    gauntletsCleared: listed ? gauntletsCleared(listed, wins.map((win) => win.settings_json)) : null,
    pbp: pbp && Number.isInteger(pbp.played) ? pbp : null,
  });
}

function medal(badge) {
  const state = badge.earned === true ? "got" : badge.earned === null ? "unknown" : "locked";
  return el("li", { class: `medal ${state}`, title: badge.earned === null ? "Couldn't check this one just now." : null }, [
    el("span", { class: "medal-mark", "aria-hidden": "true", text: badge.earned ? "★" : "☆" }),
    el("span", { class: "medal-text" }, [
      el("span", { class: "medal-title", text: badge.title }),
      el("span", { class: "medal-how", text: badge.how }),
    ]),
  ]);
}

/** Draw one player's badges into `box`, or leave it hidden if they can't be read. */
export async function drawBadges(box, nameKey) {
  let facts;
  try {
    facts = await loadFacts(nameKey);
  } catch {
    return;
  }
  if (!facts) return;
  const list = badgeList(facts);
  const got = list.filter((badge) => badge.earned).length;
  // Open to start with, since the locked ones are the point; foldable, since a
  // long list would otherwise push the maps a long way down.
  clear(box).append(el("details", { class: "badges-fold", open: "" }, [
    el("summary", {}, [el("h2", { class: "section-title", text: `Badges · ${got} of ${list.length}` })]),
    el("div", { class: "medals" }, grouped(list).map(({ group, badges }) =>
      el("section", { class: "medal-group" }, [
        el("h3", { class: "medal-group-title", text: group }),
        el("ul", { class: "medal-list" }, badges.map(medal)),
      ]))),
  ]));
  box.hidden = false;
}
