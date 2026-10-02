// The weekly campaign's rules (campaign.html): who holds which node, worked out
// by replaying the week's posted scores in the order they were posted.
//
// The week's map is stored (`campaigns.graph`, written once by
// tools/campaign.py); its state never is. `campaign_scores` hands this module
// every counted, hand-played score on one of the week's nodes, and `fold` is
// the whole game:
//
// - A *home* is claimed by the first win on it from a player with no home yet.
//   It is theirs for the week and can never be taken, which is what keeps a
//   seat open to newcomers and a way back for anyone who loses the field.
// - A *field* node is taken by a win posted while the player holds one of its
//   neighbours (their home included), or held one up to GRACE_MS before posting,
//   so a neighbour stolen while you were playing doesn't void the game. An
//   unheld node falls to any such win; a held one only to a strictly better
//   result (fewer turns, then fewer lost), so a tie defends. The holder
//   bettering their own score raises the bar.
// - Every move (a home claimed, a node taken) starts a COOLDOWN_MS wait before
//   that player's next one. A win posted during it is *queued*, not dropped: it
//   is played as a move the moment the wait ends, against the board as it is
//   then, and each queued win waits its turn in posting order. Bettering your
//   own node is not a move and never waits. That paces the player who starts
//   at Monday 00:00 without wasting anything they play.
// - Anything else is still an ordinary leaderboard score. It just isn't a move.
//
// No DOM and no fetch, so tests/ and the game's endpoint
// (netlify/functions/campaign.mjs) can run it directly.

import { weekParam } from "./crowns.mjs";
import { compareScores } from "./standings.mjs";

/** How long after losing a neighbour a win beside it still counts. */
export const GRACE_MS = 30 * 60 * 1000;
/** How long each move makes a player wait before their next one. */
export const COOLDOWN_MS = 60 * 60 * 1000;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

export const keyOf = (name) => String(name || "").trim().toLowerCase();

/** Every node's neighbours, from the graph's lane list. */
export function neighbours(graph) {
  const out = new Map(graph.nodes.map((node) => [node.id, new Set()]));
  for (const [a, b] of graph.lanes) {
    out.get(a)?.add(b);
    out.get(b)?.add(a);
  }
  return out;
}

/** When the week closes, in ms: nothing resolves at or after it. */
export function weekEnd(graph) {
  const start = Date.parse(`${graph.week_start}T00:00:00Z`);
  return Number.isNaN(start) ? Infinity : start + WEEK_MS;
}

/** Scores in the order they happened: posting time, then id for a dead heat. */
function inOrder(scores) {
  return [...scores].sort((a, b) =>
    Date.parse(a.submitted_at) - Date.parse(b.submitted_at) || a.score_id - b.score_id);
}

/**
 * Replay a week up to `now` (default: all of it).
 *
 * @returns {holders: Map<nodeId, {key, name, turns, lost, since}>,
 *           homes: Map<playerKey, nodeId>,
 *           captures: [{nodeId, taker, from, turns, at, posted}],
 *           queued: [{nodeId, key, name, turns, lost, posted, at}],
 *           readyAt: Map<playerKey, ms>, lostAt: Map<"key|nodeId", ms>,
 *           standings: [{key, name, fields, home}]}
 *
 * `queued` is every win still waiting out its player's cooldown at `now`, with
 * the moment it will be played (`at`); one that would play after the week
 * closes never does, and is not listed.
 */
export function fold(graph, scores, now = Infinity) {
  const kind = new Map(graph.nodes.map((node) => [node.id, node.kind]));
  const links = neighbours(graph);
  const close = weekEnd(graph);
  const horizon = Math.min(now, close - 1);
  const holders = new Map();
  const homes = new Map();
  const names = new Map();
  const fields = new Map();     // playerKey -> field nodes held
  const reached = new Map();    // playerKey -> when they reached that count
  const readyAt = new Map();    // playerKey -> when their next move may land
  const lostAt = new Map();     // "playerKey|nodeId" -> when they last lost it
  const waiting = new Map();    // playerKey -> [score entries], posting order
  const captures = [];

  const holds = (key, nodeId) => holders.get(nodeId)?.key === key;
  const heldSince = (key, nodeId, since) =>
    holds(key, nodeId) || (lostAt.get(`${key}|${nodeId}`) ?? -Infinity) >= since;
  const bump = (key, by, at) => {
    fields.set(key, (fields.get(key) || 0) + by);
    if (by > 0) reached.set(key, at);
  };

  // One field move, at `at`, by a win posted at `entry.posted`.
  const take = (entry, at) => {
    const { nodeId, key } = entry;
    const held = holders.get(nodeId);
    if (held && held.key === key) {
      // Bettering your own result is the only way to raise the bar attackers face.
      if (compareScores(entry, held) < 0) holders.set(nodeId, { ...held, name: entry.name, turns: entry.turns, lost: entry.lost });
      return;
    }
    const since = entry.posted - GRACE_MS;
    if (![...links.get(nodeId)].some((other) => heldSince(key, other, since))) return;
    if (held && compareScores(entry, held) >= 0) return;
    holders.set(nodeId, { key, name: entry.name, turns: entry.turns, lost: entry.lost, since: at });
    bump(key, 1, at);
    if (held) {
      bump(held.key, -1, at);
      lostAt.set(`${held.key}|${nodeId}`, at);
    }
    readyAt.set(key, at + COOLDOWN_MS);
    captures.push({ nodeId, taker: entry.name, from: held ? held.name : null, turns: entry.turns, at, posted: entry.posted });
  };

  // Play every queued win whose wait is over by `until`, earliest first.
  const drain = (until) => {
    for (;;) {
      let next = null;
      for (const [key, queue] of waiting) {
        if (!queue.length) continue;
        const at = readyAt.get(key) ?? -Infinity;
        if (at > until) continue;
        if (!next || at < next.at || (at === next.at && queue[0].order < next.queue[0].order)) next = { key, queue, at };
      }
      if (!next) return;
      take(next.queue.shift(), next.at);
    }
  };

  let order = 0;
  for (const score of inOrder(scores)) {
    const nodeId = score.node_id;
    if (!kind.has(nodeId)) continue;
    const posted = Date.parse(score.submitted_at);
    if (posted > horizon) break;
    drain(posted);
    const key = keyOf(score.user_name);
    names.set(key, score.user_name);
    const entry = { nodeId, key, name: score.user_name, turns: score.turns, lost: score.lost, posted, order: order++ };

    if (kind.get(nodeId) === "home") {
      // Cooldowns never touch a home: anyone waiting on one already has theirs.
      if (holders.get(nodeId) || homes.has(key)) continue;
      holders.set(nodeId, { key, name: entry.name, turns: entry.turns, lost: entry.lost, since: posted });
      homes.set(key, nodeId);
      readyAt.set(key, posted + COOLDOWN_MS);
      captures.push({ nodeId, taker: entry.name, from: null, turns: entry.turns, at: posted, posted });
      continue;
    }
    const queue = waiting.get(key) || [];
    if (!holds(key, nodeId) && (queue.length || (readyAt.get(key) ?? -Infinity) > posted)) {
      queue.push(entry);
      waiting.set(key, queue);
      continue;
    }
    take(entry, posted);
  }
  drain(horizon);

  const queued = [];
  for (const [key, queue] of waiting) {
    // Only the first entry's time is certain: a later one plays at the same
    // moment if the one ahead of it fails, or a cooldown later if it lands.
    // Listed at the soonest it could play.
    const at = readyAt.get(key) ?? -Infinity;
    if (at >= close) continue;
    for (const entry of queue) {
      queued.push({ nodeId: entry.nodeId, key, name: entry.name, turns: entry.turns, lost: entry.lost, posted: entry.posted, at });
    }
  }
  queued.sort((a, b) => a.at - b.at || a.posted - b.posted);

  const players = new Set([...homes.keys(), ...[...fields].filter(([, n]) => n > 0).map(([k]) => k)]);
  const standings = [...players]
    .map((key) => ({ key, name: names.get(key), fields: fields.get(key) || 0, home: homes.get(key) ?? null }))
    .sort((a, b) =>
      b.fields - a.fields || (reached.get(a.key) || 0) - (reached.get(b.key) || 0) ||
      a.name.localeCompare(b.name));
  return { holders, homes, captures, queued, readyAt, lostAt, standings };
}

/**
 * Where `name` stands on `nodeId` at `now`, for the page and the game alike.
 *
 * `can` says whether a win posted now would be a move (possibly a queued one).
 * `why` is one of "no-name", "home-open", "home-taken", "has-home", "own-home",
 * "own", "adjacent", "grace", "no-home", "not-adjacent". `graceUntil` is when a
 * "grace" claim lapses; `readyAt` is set when a win posted now would be queued
 * until then; `beat` is the score to beat on somebody else's node.
 */
export function attemptStatus(graph, state, nodeId, name, now = Date.now()) {
  const key = keyOf(name);
  const out = { can: false, why: "no-name", graceUntil: null, readyAt: null, beat: null };
  if (!key) return out;
  const node = graph.nodes.find((n) => n.id === nodeId);
  if (!node) return { ...out, why: "not-adjacent" };
  const held = state.holders.get(nodeId);
  if (node.kind === "home") {
    if (held && held.key === key) return { ...out, why: "own-home" };
    if (held) return { ...out, why: "home-taken" };
    if (state.homes.has(key)) return { ...out, why: "has-home" };
    return { ...out, can: true, why: "home-open" };
  }
  if (held && held.key === key) return { ...out, why: "own" };
  const beat = held ? { turns: held.turns, lost: held.lost, name: held.name } : null;
  const ready = state.readyAt.get(key) ?? -Infinity;
  const readyAt = ready > now ? ready : null;
  const others = [...neighbours(graph).get(nodeId)];
  if (others.some((other) => state.holders.get(other)?.key === key)) {
    return { ...out, can: true, why: "adjacent", readyAt, beat };
  }
  const lapse = Math.max(...others.map((other) => state.lostAt.get(`${key}|${other}`) ?? -Infinity)) + GRACE_MS;
  if (lapse > now) return { ...out, can: true, why: "grace", graceUntil: lapse, readyAt, beat };
  return { ...out, why: state.homes.has(key) ? "not-adjacent" : "no-home" };
}

/** "34 min", "1 h 05 min": how long until `then`, rounded up to the minute. */
export function waitLabel(then, now = Date.now()) {
  const minutes = Math.max(1, Math.ceil((then - now) / 60000));
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/**
 * Could `name` make a move on `nodeId` right now? A home, only while it is
 * empty and they have none; a field node, only while they hold a neighbour or
 * lost one within GRACE_MS (and for somebody else's, only by beating the score
 * `holders` shows). A win during a cooldown still counts, later.
 */
export function canAttempt(graph, state, nodeId, name, now = Date.now()) {
  return attemptStatus(graph, state, nodeId, name, now).can;
}

/**
 * What a map's campaign badge says and where it goes, for one `campaign_games`
 * row: that node on the campaign page, preselected, in its own week.
 */
export function campaignMark(entry, thisWeek) {
  const current = entry.week_start === weekParam(thisWeek);
  const node = `${entry.kind === "home" ? "Home" : "Node"} ${entry.node_id}`;
  const week = current ? "" : `week=${entry.week_start}&`;
  return {
    current,
    label: `${current ? "Campaign" : "Past campaign"} · ${node}`,
    href: `campaign.html?${week}node=${entry.node_id}`,
  };
}

/** A node's circle radius in the page's own units, growing with its systems. */
export function nodeRadius(systems) {
  return Math.round(12 + 2.6 * Math.sqrt(Math.max(1, systems)));
}

/** A stable colour per player, so one name is one colour all week. */
export function playerHue(name) {
  let hash = 0;
  for (const ch of keyOf(name)) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return hash % 360;
}
