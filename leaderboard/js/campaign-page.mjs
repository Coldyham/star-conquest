import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { parseWeek, weekAfter, weekBefore, weekParam, weekStart } from "./crowns.mjs";
import { canAttempt, fold, nodeRadius, playerHue } from "./campaign.mjs";
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

const params = new URLSearchParams(location.search);
const now = new Date();
const thisWeek = weekStart(now);
const week = parseWeek(params.get("week"), now);
const current = week.getTime() === thisWeek.getTime();
const me = myName();

let graph = null;
let state = null;
let boards = new Map();   // nodeId -> game_key, for the nodes somebody has posted on
let selected = params.has("node") ? Number(params.get("node")) : null;

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
    if (current && canAttempt(graph, state, node.id, me)) classes.push("open");
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

/** Why `me` can or can't move on the selected node, in one line. */
function attemptNote(node, holder) {
  if (!current) return null;
  if (!me) return el("p", { class: "cannot", text: "Post a score once and this page will know which nodes are yours to attempt." });
  if (canAttempt(graph, state, node.id, me)) {
    const beat = holder ? ` Beat ${holder.turns} turns · ${holder.lost} lost to take it.` : "";
    return el("p", { class: "can", text: `You can make this move.${beat}` });
  }
  if (holder && holder.key === me.toLowerCase()) return el("p", { class: "can", text: "Yours. Bettering your score here raises the bar." });
  if (node.kind === "home") return el("p", { class: "cannot", text: holder ? "Claimed — homes can't be taken." : "You already have a home this week." });
  return el("p", { class: "cannot", text: state.homes.has(me.toLowerCase())
    ? "Not next to anything you hold yet."
    : "Win a home first — that's how you join." });
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
    attemptNote(node, holder),
    play,
    scores,
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
      el("span", { class: "when", text: relativeTime(new Date(move.at).toISOString()) }),
    ].flat())))
    : el("p", { class: "empty", text: current ? "No moves yet this week." : "Nobody moved that week." }));
}

function drawStatus() {
  if (!current) { statusLine.textContent = "Final standings for that week."; return; }
  const left = Math.max(0, weekAfter(week).getTime() - now.getTime());
  const days = Math.floor(left / 86400000);
  const hours = Math.floor((left % 86400000) / 3600000);
  statusLine.textContent = `Ends in ${days ? `${days}d ` : ""}${hours}h. Dashed cyan rings are nodes you can attempt.`;
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
    const [rows, scores, games] = await Promise.all([
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
    state = fold(graph, scores);
    boards = new Map(games.map((row) => [row.node_id, row.game_key]));
    drawStatus();
    drawMap();
    drawDetail();
    drawStandings();
    drawFeed();
  } catch (err) {
    mapBox.classList.remove("loading");
    showError(mapBox, err.message);
  }
}

load();
