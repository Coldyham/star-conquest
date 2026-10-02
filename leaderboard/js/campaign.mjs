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
// - Anything else is still an ordinary leaderboard score. It just isn't a move.
//
// No DOM and no fetch, so tests/ and the game's endpoint
// (netlify/functions/campaign.mjs) can run it directly.

import { weekParam } from "./crowns.mjs";
import { compareScores } from "./standings.mjs";

/** How long after losing a neighbour a win beside it still counts. */
export const GRACE_MS = 30 * 60 * 1000;

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

/** Scores in the order they happened: posting time, then id for a dead heat. */
function inOrder(scores) {
  return [...scores].sort((a, b) =>
    Date.parse(a.submitted_at) - Date.parse(b.submitted_at) || a.score_id - b.score_id);
}

/**
 * Replay a week.
 *
 * @returns {holders: Map<nodeId, {key, name, turns, lost, since}>,
 *           homes: Map<playerKey, nodeId>, captures: [{nodeId, taker, from, turns, at}],
 *           lostAt: Map<"key|nodeId", ms>, standings: [{key, name, fields, home}]}
 */
export function fold(graph, scores) {
  const kind = new Map(graph.nodes.map((node) => [node.id, node.kind]));
  const links = neighbours(graph);
  const holders = new Map();
  const homes = new Map();
  const names = new Map();
  const fields = new Map();     // playerKey -> field nodes held
  const reached = new Map();    // playerKey -> when they reached that count
  const lostAt = new Map();     // "playerKey|nodeId" -> when they last lost it
  const captures = [];

  // Held it now, or lost it no earlier than `since`.
  const heldSince = (key, nodeId, since) =>
    holders.get(nodeId)?.key === key || (lostAt.get(`${key}|${nodeId}`) ?? -Infinity) >= since;
  const bump = (key, by, at) => {
    fields.set(key, (fields.get(key) || 0) + by);
    if (by > 0) reached.set(key, at);
  };

  for (const score of inOrder(scores)) {
    const nodeId = score.node_id;
    if (!kind.has(nodeId)) continue;
    const key = keyOf(score.user_name);
    names.set(key, score.user_name);
    const at = Date.parse(score.submitted_at);
    const entry = { key, name: score.user_name, turns: score.turns, lost: score.lost, since: at };
    const held = holders.get(nodeId);

    if (kind.get(nodeId) === "home") {
      if (held || homes.has(key)) continue;
      holders.set(nodeId, entry);
      homes.set(key, nodeId);
      captures.push({ nodeId, taker: score.user_name, from: null, turns: score.turns, at });
      continue;
    }

    if (held && held.key === key) {
      // Bettering your own result is the only way to raise the bar attackers face.
      if (compareScores(score, held) < 0) holders.set(nodeId, { ...entry, since: held.since });
      continue;
    }
    if (![...links.get(nodeId)].some((other) => heldSince(key, other, at - GRACE_MS))) continue;
    if (held && compareScores(score, held) >= 0) continue;
    holders.set(nodeId, entry);
    bump(key, 1, at);
    if (held) {
      bump(held.key, -1, at);
      lostAt.set(`${held.key}|${nodeId}`, at);
    }
    captures.push({ nodeId, taker: score.user_name, from: held ? held.name : null, turns: score.turns, at });
  }

  const players = new Set([...homes.keys(), ...[...fields].filter(([, n]) => n > 0).map(([k]) => k)]);
  const standings = [...players]
    .map((key) => ({ key, name: names.get(key), fields: fields.get(key) || 0, home: homes.get(key) ?? null }))
    .sort((a, b) =>
      b.fields - a.fields || (reached.get(a.key) || 0) - (reached.get(b.key) || 0) ||
      a.name.localeCompare(b.name));
  return { holders, homes, captures, lostAt, standings };
}

/**
 * Where `name` stands on `nodeId` at `now`, for the page and the game alike.
 *
 * `can` says whether a win posted now would be a move.
 * `why` is one of "no-name", "home-open", "home-taken", "has-home", "own-home",
 * "own", "adjacent", "grace", "no-home", "not-adjacent". `graceUntil` is when a
 * "grace" claim lapses; `beat` is the score to beat on somebody else's node.
 */
export function attemptStatus(graph, state, nodeId, name, now = Date.now()) {
  const key = keyOf(name);
  const out = { can: false, why: "no-name", graceUntil: null, beat: null };
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
  const others = [...neighbours(graph).get(nodeId)];
  if (others.some((other) => state.holders.get(other)?.key === key)) {
    return { ...out, can: true, why: "adjacent", beat };
  }
  const lapse = Math.max(...others.map((other) => state.lostAt.get(`${key}|${other}`) ?? -Infinity)) + GRACE_MS;
  if (lapse >= now) return { ...out, can: true, why: "grace", graceUntil: lapse, beat };
  return { ...out, why: state.homes.has(key) ? "not-adjacent" : "no-home" };
}

// attemptStatus's reasons a win wouldn't take the node, or wouldn't need to.
const NOT_A_MOVE = {
  "no-name": "Post a score once and this page will know which nodes are yours to attempt.",
  "own-home": "Your home. Homes can't be taken, so it's yours for the week.",
  own: "Yours. Bettering your score here raises the bar.",
  "home-taken": "Claimed — homes can't be taken.",
  "has-home": "You already have a home this week.",
  "not-adjacent": "Not next to anything you hold yet.",
  "no-home": "Win a home first — that's how you join.",
};

const timeOfDay = (ms) => new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/**
 * attemptStatus in the board's words: one or two `{tone, text}` lines, tone
 * "can", "cannot" or "timer". campaign.html's node panel and game.html's note
 * on a live node both show these, so the two pages never word a rule
 * differently. `clock` writes graceUntil as a time of day.
 */
export function attemptLines(status, { now = Date.now(), clock = timeOfDay } = {}) {
  if (status.can) {
    const beat = status.beat ? ` Beat ${status.beat.turns} turns · ${status.beat.lost} lost to take it.` : "";
    const lines = [{ tone: "can", text: `You can make this move.${beat}` }];
    if (status.why === "grace") {
      lines.push({
        tone: "timer",
        text: "You've lost the node next to this one, but a win here still counts if you post it " +
          `within ${waitLabel(status.graceUntil, now)} (by ${clock(status.graceUntil)}).`,
      });
    }
    return lines;
  }
  const own = status.why === "own" || status.why === "own-home";
  return [{ tone: own ? "can" : "cannot", text: NOT_A_MOVE[status.why] }];
}

/**
 * The two reads a week is folded from, `day` being its "YYYY-MM-DD": the graph,
 * and the moves in the order fold replays them. One copy for campaign.html,
 * game.html and netlify/functions/campaign.mjs.
 */
export function weekQueries(day) {
  return {
    graph: `campaigns?select=week_start,graph&week_start=eq.${day}`,
    scores: "campaign_scores?select=node_id,score_id,user_name,turns,lost,submitted_at" +
      `&week_start=eq.${day}&order=submitted_at.asc,score_id.asc`,
  };
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
 * `holders` shows).
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
