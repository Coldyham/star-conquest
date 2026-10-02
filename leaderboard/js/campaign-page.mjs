import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { parseWeek, weekAfter, weekBefore, weekParam, weekStart } from "./crowns.mjs";
import { attemptStatus, canAttempt, fold, keyOf, nodeRadius, playerHue, waitLabel } from "./campaign.mjs";
import { deflate } from "./deflate-browser.mjs";
import { clear, el, mapSummary, relativeTime, showError, userHref } from "./format.mjs";
import { mountMyScores, myName } from "./me.mjs";
import { mountNav } from "./nav.mjs";
import { encodeToken } from "./token-encode.mjs";

const SVG = "http://www.w3.org/2000/svg";
const PAD = 40;   // page units around the outermost node

const weeksNav = document.getElementById("weeks");
const statusLine = document.getElementById("status");
const mapBox = document.getElementById("map");
const detailBox = document.getElementById("detail");
const standingsBox = document.getElementById("standings");
const feedBox = document.getElementById("feed");
const queueBox = document.getElementById("queue");

const params = new URLSearchParams(location.search);
const now = new Date();
const thisWeek = weekStart(now);
const week = parseWeek(params.get("week"), now);
const current = week.getTime() === thisWeek.getTime();
const me = myName();

let graph = null;
let scores = [];
let state = null;
let boards = new Map();   // nodeId -> game_key, for the nodes somebody has posted on
let selected = params.has("node") ? Number(params.get("node")) : null;

const clockLabel = (ms) => new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
const dayLabel = (date) =>
  date.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
const weekHref = (start) =>
  start.getTime() === thisWeek.getTime() ? "campaign.html" : `campaign.html?week=${weekParam(start)}`;

function drawWeeks() {
  clear(weeksNav).append(
    el("a", { class: "btn", href: weekHref(weekBefore(week)), "aria-label": "Previous week", text: "‹" }),
    el("span", { class: "week-label", text: current ? "This week" : `Week of ${dayLabel(week)}` }),
    current
      ? el("span", { class: "btn ghost", "aria-hidden": "true", text: "›" })
      : el("a", { class: "btn", href: weekHref(weekAfter(week)), "aria-label": "Next week", text: "›" }),
  );
}

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of children) node.append(child);
  return node;
}

const fillFor = (holder) => (holder ? `hsl(${playerHue(holder.name)} 70% 58%)` : "#3a3f55");

function drawMap() {
  mapBox.classList.remove("loading");
  const xs = graph.nodes.map((n) => n.x);
  const ys = graph.nodes.map((n) => n.y);
  const x0 = Math.min(...xs) - PAD;
  const y0 = Math.min(...ys) - PAD;
  const width = Math.max(...xs) - x0 + PAD;
  const height = Math.max(...ys) - y0 + PAD;
  const at = new Map(graph.nodes.map((n) => [n.id, n]));

  const lanes = graph.lanes.map(([a, b]) => svg("line", {
    class: "lane", x1: at.get(a).x, y1: at.get(a).y, x2: at.get(b).x, y2: at.get(b).y,
  }));
  const nodes = graph.nodes.map((node) => {
    const holder = state.holders.get(node.id);
    const r = nodeRadius(node.systems);
    const classes = ["node", node.kind];
    if (!holder) classes.push("neutral");
    if (current && canAttempt(graph, state, node.id, me, Date.now())) classes.push("open");
    if (selected === node.id) classes.push("selected");
    const label = node.mystery ? "?" : String(node.systems);
    const who = holder ? `held by ${holder.name}` : "unclaimed";
    const group = svg("g", {
      class: classes.join(" "), tabindex: "0", role: "button",
      "aria-label": `${node.kind === "home" ? "Home" : "Node"} ${node.id}, ${label} systems, ${who}`,
    }, [
      svg("circle", { class: "ring", cx: node.x, cy: node.y, r: r + 6 }),
      svg("circle", { class: "body", cx: node.x, cy: node.y, r, fill: fillFor(holder) }),
      svg("text", { x: node.x, y: node.y }, [document.createTextNode(label)]),
    ]);
    const choose = () => { selected = node.id; drawMap(); drawDetail(); };
    group.addEventListener("click", choose);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); }
    });
    return group;
  });
  clear(mapBox).append(svg("svg", {
    viewBox: `${x0} ${y0} ${width} ${height}`, role: "group", "aria-label": "This week's campaign map",
  }, [...lanes, ...nodes]));
}

/** Why `me` can or can't move on the selected node, and when, in a line or two. */
function attemptNote(node) {
  if (!current) return [];
  if (!me) return [el("p", { class: "cannot", text: "Post a score once and this page will know which nodes are yours to attempt." })];
  const now = Date.now();
  const status = attemptStatus(graph, state, node.id, me, now);
  const lines = [];
  if (status.can) {
    const beat = status.beat ? ` Beat ${status.beat.turns} turns · ${status.beat.lost} lost to take it.` : "";
    lines.push(el("p", { class: "can", text: `You can make this move.${beat}` }));
    if (status.why === "grace") {
      lines.push(el("p", { class: "timer", text: `You've lost the node next to this one, but a win here still counts if you post it within ${waitLabel(status.graceUntil, now)} (by ${clockLabel(status.graceUntil)}).` }));
    }
    if (status.readyAt) {
      lines.push(el("p", { class: "timer", text: `You moved less than an hour ago. Post a win now and it's queued, then played at ${clockLabel(status.readyAt)} (in ${waitLabel(status.readyAt, now)}), against whoever holds the node then.` }));
    }
    return lines;
  }
  const text = {
    "own-home": "Your home. Homes can't be taken, so it's yours for the week.",
    own: "Yours. Bettering your score here raises the bar, and never waits on your cooldown.",
    "home-taken": "Claimed — homes can't be taken.",
    "has-home": "You already have a home this week.",
    "not-adjacent": "Not next to anything you hold yet.",
    "no-home": "Win a home first — that's how you join.",
  }[status.why];
  const mine = state.queued.filter((q) => q.key === keyOf(me) && q.nodeId === node.id);
  if (mine.length) lines.push(el("p", { class: "timer", text: `Your win here is queued, to be played at ${clockLabel(mine[0].at)}.` }));
  lines.push(el("p", { class: status.why === "own" || status.why === "own-home" ? "can" : "cannot", text }));
  return lines;
}

async function drawDetail() {
  const node = graph.nodes.find((n) => n.id === selected);
  if (!node) { clear(detailBox); return; }
  const holder = state.holders.get(node.id);
  const title = node.kind === "home" ? `Home ${node.id}` : `Node ${node.id}`;
  const setup = node.settings;
  let play = null;
  if (current && GAME_URL) {
    try {
      play = el("a", { class: "btn play", href: `${GAME_URL}#${await encodeToken(setup, deflate)}`, text: "Play this map" });
    } catch {
      play = null;
    }
  }
  const board = boards.get(node.id);
  const scores = board
    ? el("a", { class: "btn ghost", href: `game.html?key=${encodeURIComponent(board)}`, text: "Scores" })
    : null;
  clear(detailBox).append(
    el("h3", { text: title }),
    el("p", { text: node.mystery ? "Mystery map — settings randomised" : mapSummary(setup) }),
    el("p", {}, holder
      ? ["Held by ", el("a", { href: userHref([holder.name]), text: holder.name }),
        ` — ${holder.turns} turns · ${holder.lost} lost`]
      : [node.kind === "home" ? "Unclaimed home — win it to join." : "Unclaimed."]),
    ...attemptNote(node),
    ...[play, scores].filter(Boolean),
  );
}

function drawStandings() {
  if (!state.standings.length) {
    clear(standingsBox).append(el("p", { class: "empty", text: "Nobody has claimed a home yet." }));
    return;
  }
  clear(standingsBox).append(el("div", { class: "tally" }, state.standings.map((row) =>
    el("div", { class: "tally-row" }, [
      el("a", { class: "tally-name", href: userHref([row.name]), text: row.name }),
      el("span", { class: "tally-figs", text: `${row.fields} ${row.fields === 1 ? "node" : "nodes"} · home ${row.home ?? "—"}` }),
    ]))));
}

function drawFeed() {
  const moves = [...state.captures].reverse();
  clear(feedBox).append(moves.length
    ? el("ol", { class: "steals" }, moves.map((move) => el("li", { class: "steal" }, [
      el("a", { href: userHref([move.taker]), text: move.taker }),
      move.from
        ? [` took node ${move.nodeId} from `, el("a", { href: userHref([move.from]), text: move.from })]
        : ` claimed ${graph.nodes.find((n) => n.id === move.nodeId)?.kind === "home" ? "home" : "node"} ${move.nodeId}`,
      `, ${move.turns} turns `,
      move.at !== move.posted ? `(queued from ${clockLabel(move.posted)}) ` : "",
      el("span", { class: "when", text: relativeTime(new Date(move.at).toISOString()) }),
    ].flat())))
    : el("p", { class: "empty", text: current ? "No moves yet this week." : "Nobody moved that week." }));
}

function drawQueue() {
  clear(queueBox).append(state.queued.length
    ? el("ol", { class: "steals" }, state.queued.map((move) => el("li", { class: "steal" }, [
      el("a", { href: userHref([move.name]), text: move.name }),
      ` on node ${move.nodeId}, ${move.turns} turns · ${move.lost} lost, plays at ${clockLabel(move.at)} `,
      el("span", { class: "when", text: `(in ${waitLabel(move.at)})` }),
    ])))
    : el("p", { class: "empty", text: current ? "Nothing waiting." : "Nothing was left waiting." }));
}

function drawStatus() {
  if (!current) { statusLine.textContent = "Final standings for that week."; return; }
  const nowMs = Date.now();
  const left = Math.max(0, weekAfter(week).getTime() - nowMs);
  const days = Math.floor(left / 86400000);
  const hours = Math.floor((left % 86400000) / 3600000);
  const ready = me ? state.readyAt.get(keyOf(me)) : null;
  const wait = ready && ready > nowMs
    ? ` Your next move can land at ${clockLabel(ready)} (in ${waitLabel(ready, nowMs)}); a win posted before then is queued, not lost.`
    : "";
  statusLine.textContent = `Ends in ${days ? `${days}d ` : ""}${hours}h. Dashed cyan rings are nodes you can attempt.${wait}`;
}

/** Re-fold against the clock, so queued moves land and timers count down without a reload. */
function redraw() {
  state = fold(graph, scores, Date.now());
  drawStatus();
  drawMap();
  drawDetail();
  drawStandings();
  drawQueue();
  drawFeed();
}

async function load() {
  mountNav();
  mountMyScores();
  drawWeeks();
  if (!configured()) {
    showError(mapBox, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  try {
    const day = weekParam(week);
    const [rows, posted, games] = await Promise.all([
      select(`campaigns?select=week_start,graph&week_start=${eq(day)}`),
      select(`campaign_scores?select=node_id,score_id,user_name,turns,lost,submitted_at` +
        `&week_start=${eq(day)}&order=submitted_at.asc,score_id.asc`),
      select(`campaign_games?select=node_id,game_key&week_start=${eq(day)}`).catch(() => []),
    ]);
    if (!rows.length) {
      mapBox.classList.remove("loading");
      clear(mapBox).append(el("p", { class: "empty", text: current
        ? "This week's map hasn't been made yet — it appears within the hour after Monday 00:00 UTC."
        : "There was no campaign that week." }));
      return;
    }
    graph = rows[0].graph;
    scores = posted;
    boards = new Map(games.map((row) => [row.node_id, row.game_key]));
    redraw();
    if (current) setInterval(redraw, 60000);
  } catch (err) {
    mapBox.classList.remove("loading");
    showError(mapBox, err.message);
  }
}

load();
