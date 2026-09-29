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
//   neighbours (their home included). An unheld node falls to any such win; a
//   held one only to a strictly better result (fewer turns, then fewer lost),
//   so a tie defends. The holder bettering their own score raises the bar.
// - Anything else is still an ordinary leaderboard score. It just isn't a move.
//
// No DOM and no fetch, so tests/ can exercise it directly.

import { weekParam } from "./crowns.mjs";
import { compareScores } from "./standings.mjs";

const keyOf = (name) => String(name || "").trim().toLowerCase();

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
 *           standings: [{key, name, fields, home}]}
 */
export function fold(graph, scores) {
  const kind = new Map(graph.nodes.map((node) => [node.id, node.kind]));
  const links = neighbours(graph);
  const holders = new Map();
  const homes = new Map();
  const names = new Map();
  const fields = new Map();     // playerKey -> field nodes held
  const reached = new Map();    // playerKey -> when they reached that count
  const captures = [];

  const holds = (key, nodeId) => holders.get(nodeId)?.key === key;
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
    if (![...links.get(nodeId)].some((other) => holds(key, other))) continue;
    if (held && compareScores(score, held) >= 0) continue;
    holders.set(nodeId, entry);
    bump(key, 1, at);
    if (held) bump(held.key, -1, at);
    captures.push({ nodeId, taker: score.user_name, from: held ? held.name : null, turns: score.turns, at });
  }

  const players = new Set([...homes.keys(), ...[...fields].filter(([, n]) => n > 0).map(([k]) => k)]);
  const standings = [...players]
    .map((key) => ({ key, name: names.get(key), fields: fields.get(key) || 0, home: homes.get(key) ?? null }))
    .sort((a, b) =>
      b.fields - a.fields || (reached.get(a.key) || 0) - (reached.get(b.key) || 0) ||
      a.name.localeCompare(b.name));
  return { holders, homes, captures, standings };
}

/**
 * Could `name` make a move on `nodeId` right now? A home, only while it is
 * empty and they have none; a field node, only while they hold a neighbour
 * (and for somebody else's, only by beating the score `holders` shows).
 */
export function canAttempt(graph, state, nodeId, name) {
  const key = keyOf(name);
  if (!key) return false;
  const node = graph.nodes.find((n) => n.id === nodeId);
  if (!node) return false;
  const held = state.holders.get(nodeId);
  if (node.kind === "home") return !held && !state.homes.has(key);
  if (held && held.key === key) return false;
  return [...neighbours(graph).get(nodeId)].some((other) => state.holders.get(other)?.key === key);
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
