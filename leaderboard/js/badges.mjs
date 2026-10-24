// Badges on a player card (user.html): one-off milestones that point a player at
// a part of the game they haven't tried yet.
//
// Derived, never stored, like crowns and the campaign: every badge is a
// question about rows the board already keeps (counted scores, crown steals,
// campaign weeks, listed gauntlets) or a count one of the functions answers
// (/api/pbp?action=record, /api/gauntlet?by=). A score deleted or found to be a
// mismatch takes its badges with it on the next page load. Wins only count
// through `counted_scores`, so an unverified score earns nothing a crown
// wouldn't.
//
// No DOM and no fetch, so tests/badges.test.mjs can drive it directly; the
// reads and the drawing are badges-card.mjs.

import { BOT_LEGACY } from "./bot-stops.mjs";
import { fold, keyOf } from "./campaign.mjs";
import { weekAfter, weekParam, weekStart } from "./crowns.mjs";
import { stageSetup } from "./gauntlet-build.mjs";
import { hasFog } from "./setup.mjs";
import { LADDER_ORDER } from "./standings.mjs";
import { setupIdentity } from "./token-decode.mjs";

// The keys a win on the tutorial's last stage is posted under: its
// `challenge_keys()`, the current one first, as the game's own `gauntlet.best`
// reads them. A score on it is the only trace the board has of someone
// finishing the tutorial. The board can't compute a challenge key, so
// tests/test_leaderboard_sync.py pins these; a Settings field that moves them
// gets the new key added here, the old ones kept so earlier scores still count.
export const TUTORIAL_FINAL_KEYS = [
  "0529b69244813bb8", "f96f4a3b67ebf118", "40ca0fbdaa4220eb", "c48539a369e1cbaa", "fe98b1f7231d339f",
];

/** Earliest first, ties by id: the order crown_steals reads "first". */
const byPosting = (a, b) => Date.parse(a.submitted_at) - Date.parse(b.submitted_at) || a.id - b.id;

/**
 * Which of `mine` (the player's counted scores) opened a map and which joined
 * one somebody else had opened, judged against `field` (every counted score on
 * those maps, the player's included). `{opened, joined}` are game_key sets.
 */
export function firsts(userId, field) {
  const first = new Map();
  for (const score of [...field].sort(byPosting)) {
    if (!first.has(score.game_key)) first.set(score.game_key, score.user_id);
  }
  const mine = new Set(field.filter((score) => score.user_id === userId).map((score) => score.game_key));
  const opened = new Set();
  const joined = new Set();
  for (const [key, who] of first) {
    if (who === userId) opened.add(key);
    else if (mine.has(key)) joined.add(key);
  }
  return { opened, joined };
}

/** A hand-drawn map as one comparable string: the drawing, whatever its seed. */
export function drawingOf(customMap) {
  return customMap ? setupIdentity({ map: customMap }) : "";
}

/**
 * How many drawings the player posted the first counted win on. `games` is
 * every hand-drawn games row ({game_key, custom_map}); `field` every counted
 * score on them. A drawing replayed on another seed is a new map to the board
 * but the same drawing, so the first win on any of its seeds is the one that
 * counts.
 */
export function drawingsOpened(userId, games, field) {
  const drawing = new Map(games.map((game) => [game.game_key, drawingOf(game.custom_map)]));
  const first = new Map();
  for (const score of [...field].sort(byPosting)) {
    const key = drawing.get(score.game_key);
    if (key && !first.has(key)) first.set(key, score.user_id);
  }
  return [...first.values()].filter((who) => who === userId).length;
}

/**
 * The bot families a stored setup's `bots` column (`sc_bots`) names, folded
 * names read as their family. "random" is a seat left to chance, whose bot is
 * in the replay, not the setup, so it names nobody.
 */
export function botFamilies(bots) {
  const out = new Set();
  for (const bot of bots || []) {
    const name = String(bot).split(":")[0];
    if (name === "random") continue;
    out.add(BOT_LEGACY[name]?.family ?? name);
  }
  return out;
}

/**
 * Finished weeks in which `name` stole at least one crown and nobody stole
 * more (a tie shares the week). `steals` is every crown_steals row over the
 * weeks in question; a week still running can't be topped yet.
 */
export function stealWeeksTopped(steals, name, now = new Date()) {
  const me = keyOf(name);
  const weeks = new Map();
  for (const steal of steals) {
    const week = weekParam(weekStart(new Date(steal.submitted_at)));
    if (!weeks.has(week)) weeks.set(week, new Map());
    const counts = weeks.get(week);
    const key = keyOf(steal.taker_name);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  let topped = 0;
  for (const [week, counts] of weeks) {
    if (weekAfter(new Date(`${week}T00:00:00Z`)) > now) continue;
    const mine = counts.get(me) || 0;
    if (mine > 0 && mine >= Math.max(...counts.values())) topped += 1;
  }
  return topped;
}

/**
 * `{homes, wins}` over campaign weeks, each `{week_start, graph, scores}` as
 * weekQueries reads them. A home is claimed the moment it is won, so it counts
 * in a live week; a week is won only once it has closed with `name` first in
 * fold's standings, holding at least one field node.
 */
export function campaignRecord(weeks, name, now = new Date()) {
  const me = keyOf(name);
  let homes = 0;
  let wins = 0;
  for (const week of weeks) {
    const state = fold(week.graph, week.scores);
    if (state.homes.has(me)) homes += 1;
    const closed = weekAfter(new Date(`${week.week_start}T00:00:00Z`)) <= now;
    const top = state.standings[0];
    if (closed && top && top.key === me && top.fields > 0) wins += 1;
  }
  return { homes, wins };
}

/**
 * How many listed gauntlets the player has cleared: a counted win on the last
 * stage, the board's only record of a run (progress lives in each player's own
 * game). `gauntlets` are `{gauntlet_id, stages}`; `setups` the settings_json
 * of every map the player has a counted win on. Matched by setup, never key,
 * since the board can't compute a challenge key.
 */
export function gauntletsCleared(gauntlets, setups) {
  const won = new Set(setups.map((setup) => setupIdentity(stageSetup(setup))));
  return gauntlets.filter((gauntlet) => {
    const stages = Array.isArray(gauntlet.stages) ? gauntlet.stages : [];
    const last = stages[stages.length - 1];
    return Boolean(last?.settings) && won.has(setupIdentity(stageSetup(last.settings)));
  }).length;
}

/**
 * Everything the badges read, from what badges-card.mjs fetched. `wins` are
 * the player's counted scores joined to their map: `{game_key, mode,
 * settings_json, bots}`. A part that could not be read (`pbp`, `gauntletsMade`
 * when a function is down, `gauntletsCleared` without the table) stays null, and its badges show as unknown rather
 * than locked.
 */
export function factsFrom({
  wins = [], opened = new Set(), joined = new Set(), drawings = 0, steals = 0, stealWeeks = 0,
  campaign = { homes: 0, wins: 0 }, gauntletsMade = null, gauntletsCleared: cleared = 0,
  pbp = null,
} = {}) {
  const custom = wins.filter((win) => win.settings_json?.custom_map);
  const generated = wins.filter((win) => !win.settings_json?.custom_map);
  const bots = new Set();
  for (const win of wins) for (const family of botFamilies(win.bots)) bots.add(family);
  return {
    wins: wins.length,
    opened: opened.size,
    joined: joined.size,
    steals,
    stealWeeks,
    homes: campaign.homes,
    campaignWins: campaign.wins,
    tutorial: wins.some((win) => TUTORIAL_FINAL_KEYS.includes(win.game_key)),
    gauntletsMade,
    gauntletsCleared: cleared,
    symmetric: generated.some((win) => win.mode === "symmetric"),
    random: generated.some((win) => win.mode === "random"),
    laneBattles: wins.some((win) => win.settings_json?.in_lane_battles === true),
    fog: wins.some((win) => hasFog(win.settings_json)),
    handMaps: custom.length,
    drawings,
    bots,
    pbpPlayed: pbp ? pbp.played : null,
    pbpWon: pbp ? pbp.won : null,
  };
}

const atLeastOne = (value) => (value === null ? null : value > 0);

// In the order the card shows them, grouped. Within a group, a badge that
// follows on from the one before it (claim a home, then win a week) comes
// after it. `has` answers true, false, or null for "couldn't tell".
export const BADGES = [
  { id: "first-win", group: "The board", title: "On the board",
    how: "Post a win to the leaderboard.", has: (f) => f.wins > 0 },
  { id: "trailblazer", group: "The board", title: "Trailblazer",
    how: "Post the first win on a map nobody has posted on yet.", has: (f) => f.opened > 0 },
  { id: "challenger", group: "The board", title: "Challenger",
    how: "Post a win on a map somebody else posted first.", has: (f) => f.joined > 0 },
  { id: "crown-thief", group: "The board", title: "Crown thief",
    how: "Beat somebody else's record on a map.", has: (f) => f.steals > 0 },
  { id: "master-thief", group: "The board", title: "Master thief",
    how: "Steal more crowns than anyone else in a week (a tie counts).", has: (f) => f.stealWeeks > 0 },

  { id: "homesteader", group: "Campaign", title: "Homesteader",
    how: "Claim a home on the weekly campaign map.", has: (f) => f.homes > 0 },
  { id: "warlord", group: "Campaign", title: "Warlord",
    how: "Hold the most campaign nodes when a week closes.", has: (f) => f.campaignWins > 0 },

  { id: "graduate", group: "Gauntlets", title: "Graduate",
    how: "Win the tutorial's last stage and post the score.", has: (f) => f.tutorial },
  { id: "gauntlet-runner", group: "Gauntlets", title: "Gauntlet runner",
    how: "Win the last stage of a listed gauntlet and post the score.",
    has: (f) => atLeastOne(f.gauntletsCleared) },
  { id: "architect", group: "Gauntlets", title: "Architect",
    how: "Save a gauntlet on the Make a gauntlet page, under this name.",
    has: (f) => atLeastOne(f.gauntletsMade) },

  { id: "random", group: "Ways to play", title: "Wild space",
    how: "Win on a random map.", has: (f) => f.random },
  { id: "symmetric", group: "Ways to play", title: "Mirror match",
    how: "Win on a symmetric map.", has: (f) => f.symmetric },
  { id: "fog", group: "Ways to play", title: "Into the dark",
    how: "Win with fog of war on.", has: (f) => f.fog },
  { id: "lane-battles", group: "Ways to play", title: "Dogfighter",
    how: "Win with in-lane battles on (Combat tab).", has: (f) => f.laneBattles },
  { id: "hand-map", group: "Ways to play", title: "Explorer",
    how: "Win on a hand-drawn map.", has: (f) => f.handMaps > 0 },
  { id: "cartographer", group: "Ways to play", title: "Cartographer",
    how: "Draw a map in the map maker and post the first win on it.", has: (f) => f.drawings > 0 },

  ...LADDER_ORDER.map((bot) => ({
    id: `bot-${bot}`, group: "Bots", title: `Beat ${bot}`,
    how: `Win a game with ${bot} in one of the seats.`, has: (f) => f.bots.has(bot),
  })),

  { id: "pen-pal", group: "Play-by-post", title: "Pen pal",
    how: "Play a turn of a play-by-post match under this name.", has: (f) => atLeastOne(f.pbpPlayed) },
  { id: "by-return", group: "Play-by-post", title: "By return of post",
    how: "Win a play-by-post match.", has: (f) => atLeastOne(f.pbpWon) },
];

/** Every badge with `earned` (true, false or null) for one player's facts. */
export function badgeList(facts) {
  return BADGES.map(({ has, ...badge }) => {
    const answer = has(facts);
    return { ...badge, earned: answer === null ? null : Boolean(answer) };
  });
}

/** The groups in order, each with its badges: `[{group, badges}]`. */
export function grouped(list) {
  const out = [];
  for (const badge of list) {
    const last = out[out.length - 1];
    if (last && last.group === badge.group) last.badges.push(badge);
    else out.push({ group: badge.group, badges: [badge] });
  }
  return out;
}
