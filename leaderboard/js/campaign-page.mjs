import { configured, eq, select } from "./api.mjs";
import { GAME_URL } from "./config.mjs";
import { parseWeek, weekAfter, weekBefore, weekParam, weekStart } from "./crowns.mjs";
import {
  attemptLines, attemptStatus, canAttempt, fieldCounts, fold, nodeRadius, playerHue, weekHistory, weekQueries,
} from "./campaign.mjs";
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
const replayBox = document.getElementById("replay");
const territoryBox = document.getElementById("territory");

const params = new URLSearchParams(location.search);
const now = new Date();
const thisWeek = weekStart(now);
const week = parseWeek(params.get("week"), now);
// Only ever turns false, when the live week closes with the page open (tick).
let current = week.getTime() === thisWeek.getTime();
const me = myName();

let graph = null;
let state = null;
let boards = new Map();   // nodeId -> game_key, for the nodes somebody has posted on
let ticker = null;
let selected = params.has("node") ? Number(params.get("node")) : null;
// The replay: one frame per move (campaign.weekHistory), and where the
// scrubber stands. `pos === frames.length` is now, the live map; anything less
// is the map just after move `pos` (0: before the first).
let frames = [];
let pos = 0;
let playing = null;
const live = () => pos === frames.length;
// The territory chart's x-axis, "time" or "move": a per-viewer preference.
const AXIS_KEY = "campaign.chartAxis";
let axis = (() => {
  try { return localStorage.getItem(AXIS_KEY) === "move" ? "move" : "time"; } catch { return "time"; }
})();
/** Who held what at the scrubber's moment. */
const holdersNow = () => (live() ? state.holders : pos ? frames[pos - 1].holders : new Map());

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
  const shown = holdersNow();
  const moved = !live() && pos ? frames[pos - 1].move.nodeId : null;
  const nodes = graph.nodes.map((node) => {
    const holder = shown.get(node.id);
    const r = nodeRadius(node.systems);
    const classes = ["node", node.kind];
    if (!holder) classes.push("neutral");
    if (current && live() && canAttempt(graph, state, node.id, me, Date.now())) classes.push("open");
    if (node.id === moved) classes.push("moved");
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
  if (!current || !live()) return [];
  const now = Date.now();
  return attemptLines(attemptStatus(graph, state, node.id, me, now), { now })
    .map(({ tone, text }) => el("p", { class: tone, text }));
}

async function drawDetail() {
  const node = graph.nodes.find((n) => n.id === selected);
  if (!node) { clear(detailBox); return; }
  const holder = holdersNow().get(node.id);
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
      ? [live() ? "Held by " : "Then held by ", el("a", { href: userHref([holder.name]), text: holder.name }),
        ` — ${holder.turns} turns · ${holder.lost} lost`]
      : [live()
        ? node.kind === "home" ? "Unclaimed home — win it to join." : "Unclaimed."
        : "Unclaimed then."]),
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
      el("a", { class: "tally-name", href: userHref([row.name]) }, [
        el("span", { class: "swatch", style: `background:${fillFor(row)}`, "aria-hidden": "true" }), row.name]),
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
  const nowMs = Date.now();
  const left = Math.max(0, weekAfter(week).getTime() - nowMs);
  const days = Math.floor(left / 86400000);
  const hours = Math.floor((left % 86400000) / 3600000);
  statusLine.textContent = `Ends in ${days ? `${days}d ` : ""}${hours}h. Dashed cyan rings are nodes you can attempt.`;
}

const kindOf = (nodeId) => (graph.nodes.find((n) => n.id === nodeId)?.kind === "home" ? "home" : "node");
const momentLabel = (ms) => new Date(ms).toLocaleString(undefined,
  { weekday: "short", hour: "2-digit", minute: "2-digit" });
/** One move in a sentence, for the replay's caption. */
const moveText = (move) => (move.from
  ? `${move.taker} took node ${move.nodeId} from ${move.from} (${move.turns} turns)`
  : `${move.taker} claimed ${kindOf(move.nodeId)} ${move.nodeId} (${move.turns} turns)`);

/** The week's first and last instants on the chart: Monday to now, or to the close. */
function span() {
  const start = week.getTime();
  const end = current ? Math.min(Date.now(), weekAfter(week).getTime()) : weekAfter(week).getTime();
  return [start, Math.max(end, start + 1)];
}

function stopPlaying() {
  clearInterval(playing);
  playing = null;
}

/** Move the scrubber to `to` and redraw what follows from it. */
function seek(to, { keepPlaying = false } = {}) {
  pos = Math.max(0, Math.min(frames.length, to));
  if (!keepPlaying) stopPlaying();
  drawMap();
  drawDetail();
  drawReplay();
  drawTerritory();
}

function play() {
  if (playing) { stopPlaying(); drawReplay(); return; }
  if (live()) pos = 0;
  playing = setInterval(() => {
    seek(pos + 1, { keepPlaying: true });
    if (live()) { stopPlaying(); drawReplay(); }
  }, 700);
  seek(pos, { keepPlaying: true });
}

/** The replay bar under the map: play, step, a scrubber and what just happened. */
function drawReplay() {
  replayBox.hidden = !frames.length;
  if (!frames.length) return;
  const step = (label, text, to, disabled) => {
    const button = el("button", { class: "btn", type: "button", "aria-label": label, text });
    button.disabled = disabled;
    button.addEventListener("click", () => seek(to));
    return button;
  };
  const toggle = el("button", {
    class: "btn", type: "button", "aria-label": playing ? "Pause" : "Replay the week", text: playing ? "❚❚" : "▶",
  });
  toggle.addEventListener("click", play);
  const scrub = el("input", {
    type: "range", min: "0", max: String(frames.length), step: "1", value: String(pos),
    "aria-label": "Move", "aria-valuetext": live() ? "Now" : `Move ${pos} of ${frames.length}`,
  });
  scrub.addEventListener("input", () => seek(Number(scrub.value)));
  let caption;
  if (live()) {
    caption = `Now · ${frames.length} ${frames.length === 1 ? "move" : "moves"} this week. Press ▶ to replay them.`;
  } else if (!pos) {
    caption = `Before the first move · Move 0 of ${frames.length}`;
  } else {
    const { move } = frames[pos - 1];
    caption = `Move ${pos} of ${frames.length} · ${momentLabel(move.at)} · ${moveText(move)}`;
  }
  clear(replayBox).append(
    el("div", { class: "replay-controls" }, [
      step("Back to the start", "⏮", 0, !pos), step("Previous move", "‹", pos - 1, !pos),
      toggle,
      step("Next move", "›", pos + 1, live()), step("Now", "⏭", frames.length, live()),
      scrub,
    ]),
    el("p", { class: "replay-caption", "aria-live": "polite", text: caption }),
  );
}

/** The latest frame at or before `ms`: what the map showed then. */
const frameAt = (ms) => {
  let index = 0;
  while (index < frames.length && frames[index].move.at <= ms) index += 1;
  return index;
};

/**
 * Field nodes held over the week, one stacked band per player in the
 * standings' order and their map colour, a step at each move. The unclaimed
 * rest is the empty space above, so the full height is the whole field.
 * Hovering reads a moment; a click or tap takes the map there.
 */
function drawTerritory() {
  const field = graph.nodes.filter((n) => n.kind !== "home").length;
  const bands = fieldCounts(graph, frames, state.standings.map((row) => row.key));
  territoryBox.hidden = !bands.length || !field;
  if (territoryBox.hidden) return;
  const width = Math.max(280, territoryBox.clientWidth || 640);
  const height = 170;
  const pad = { left: 28, right: 8, top: 10, bottom: 22 };
  const [t0, t1] = span();
  const plotW = width - pad.left - pad.right;
  const y = (n) => height - pad.bottom - (n / field) * (height - pad.top - pad.bottom);
  const counts = (band, i) => (i === 0 ? 0 : band.counts[i - 1]);
  const n = frames.length;
  // Frame i spans edges[i] to edges[i + 1] in plot pixels. By time, from its
  // move to the next (the last: to the chart's end); by move, one even step
  // each, so a busy evening is as wide as a quiet day.
  const byTime = (ms) => pad.left + ((ms - t0) / (t1 - t0)) * plotW;
  const byMove = (i) => pad.left + (i / (n + 1)) * plotW;
  const edges = axis === "time"
    ? [t0, ...frames.map((f) => f.move.at), t1].map(byTime)
    : Array.from({ length: n + 2 }, (_, i) => byMove(i));
  /** The frame under plot pixel `px`. */
  const frameAtPx = (px) => {
    if (axis === "time") return frameAt(t0 + ((px - pad.left) / plotW) * (t1 - t0));
    return Math.max(0, Math.min(n, Math.floor(((px - pad.left) / plotW) * (n + 1))));
  };

  const below = new Array(n + 1).fill(0);
  const names = [];
  const paths = bands.map((band) => {
    const top = [];
    const bottom = [];
    for (let i = 0; i <= n; i += 1) {
      const lo = below[i];
      const hi = lo + counts(band, i);
      top.push(`${edges[i]},${y(hi)}`, `${edges[i + 1]},${y(hi)}`);
      bottom.push(`${edges[i]},${y(lo)}`, `${edges[i + 1]},${y(lo)}`);
      below[i] = hi;
    }
    // Each band's name at its right-hand end where it is tall enough to hold it,
    // so a player is never told from another by colour alone.
    const lo = below.at(-1) - counts(band, n);
    if (y(lo) - y(below.at(-1)) >= 14) {
      names.push(svg("text", {
        class: "band-name", x: width - pad.right - 4, y: (y(lo) + y(below.at(-1))) / 2,
        "text-anchor": "end", "dominant-baseline": "central",
      }, [document.createTextNode(band.name)]));
    }
    return svg("polygon", {
      class: "band", points: [...top, ...bottom.reverse()].join(" "), fill: fillFor(band),
    }, [svg("title", {}, [document.createTextNode(band.name)])]);
  });

  // Gridlines at half and all of the field.
  const grid = [0.5, 1].map((share) => {
    const k = Math.round(field * share);
    return [
      svg("line", { class: "grid", x1: pad.left, x2: width - pad.right, y1: y(k), y2: y(k) }),
      svg("text", { class: "axis", x: pad.left - 4, y: y(k), "text-anchor": "end", "dominant-baseline": "central" },
        [document.createTextNode(String(k))]),
    ];
  }).flat();
  // Ticks: Monday to Sunday in UTC like the week itself, or every few moves.
  const ticks = [];
  const tick = (px, label, line, at = px + 2, anchor = "start") => {
    ticks.push(svg("text", { class: "axis", x: at, y: height - 6, "text-anchor": anchor },
      [document.createTextNode(label)]));
    if (line) ticks.push(svg("line", { class: "grid", x1: px, x2: px, y1: pad.top, y2: height - pad.bottom }));
  };
  if (axis === "time") {
    for (let d = 0; d < 7 && t0 + d * 86400000 <= t1; d += 1) {
      const ms = t0 + d * 86400000;
      tick(byTime(ms), new Date(ms).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" }), d > 0);
    }
  } else {
    const fit = Math.max(1, Math.floor(plotW / 48));    // labels about 48px apart at most
    const every = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find((e) => (n + 1) / e <= fit) ?? 1000;
    // A move's number sits under the middle of its step, where the cursor stands on it.
    for (let i = 0; i <= n; i += every) tick(byMove(i), String(i), i > 0, (byMove(i) + byMove(i + 1)) / 2, "middle");
  }

  // Where the replay stands: by time, the moment of its move (or now); by move, the middle of its step.
  const cursorX = axis === "time"
    ? (live() ? byTime(t1) : edges[pos])
    : (edges[pos] + edges[pos + 1]) / 2;
  const cursor = svg("line", {
    class: "cursor", x1: cursorX, x2: cursorX, y1: pad.top - 4, y2: height - pad.bottom,
  });
  const hover = svg("line", { class: "hover", x1: 0, x2: 0, y1: pad.top - 4, y2: height - pad.bottom, visibility: "hidden" });
  const chart = svg("svg", {
    width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Field nodes held over the week ${axis === "time" ? "by time" : "move by move"}, out of ${field}. ` +
      bands.map((band) => `${band.name}: ${band.counts.at(-1)} now, most ${Math.max(...band.counts)}`).join("; "),
  }, [...grid, ...paths, ...names, ...ticks, cursor, hover]);

  const tip = el("div", { class: "territory-tip", hidden: "" });
  const pxAt = (event) => {
    const box = chart.getBoundingClientRect();
    return Math.max(pad.left, Math.min(width - pad.right, ((event.clientX - box.left) / box.width) * width));
  };
  chart.addEventListener("pointermove", (event) => {
    const px = pxAt(event);
    const index = frameAtPx(px);
    hover.setAttribute("x1", px);
    hover.setAttribute("x2", px);
    hover.setAttribute("visibility", "visible");
    const title = axis === "time"
      ? momentLabel(t0 + ((px - pad.left) / plotW) * (t1 - t0))
      : index ? `Move ${index} · ${momentLabel(frames[index - 1].move.at)}` : "Before the first move";
    const held = bands.map((band) => [band, counts(band, index)]).filter(([, k]) => k);
    clear(tip).append(
      el("strong", { text: title }),
      ...(held.length
        ? held.sort((a, b) => b[1] - a[1]).map(([band, k]) => el("div", {}, [
          el("span", { class: "swatch", style: `background:${fillFor(band)}`, "aria-hidden": "true" }),
          `${band.name} ${k}`]))
        : [el("div", { text: "Nothing taken yet" })]),
    );
    tip.hidden = false;
    const left = (event.clientX - territoryBox.getBoundingClientRect().left);
    tip.style.left = `${Math.min(left + 12, territoryBox.clientWidth - tip.offsetWidth)}px`;
  });
  chart.addEventListener("pointerleave", () => { tip.hidden = true; hover.setAttribute("visibility", "hidden"); });
  chart.addEventListener("click", (event) => seek(frameAtPx(pxAt(event))));

  const switcher = el("div", { class: "axis-switch", role: "group", "aria-label": "Chart across" },
    [["time", "Time"], ["move", "Moves"]].map(([value, label]) => {
      const button = el("button", {
        type: "button", class: `btn${axis === value ? "" : " ghost"}`, "aria-pressed": String(axis === value), text: label,
      });
      button.addEventListener("click", () => {
        axis = value;
        try { localStorage.setItem(AXIS_KEY, value); } catch { /* storage blocked: back to time next visit */ }
        drawTerritory();
      });
      return button;
    }));

  clear(territoryBox).append(
    el("div", { class: "territory-head" }, [
      el("h3", { class: "territory-title", text: `Field nodes held, of ${field}` }), switcher]),
    chart, tip,
    el("p", { class: "territory-note", text: "Hover to read a moment; click to show it on the map." }),
  );
}

function redraw() {
  drawStatus();
  drawMap();
  drawDetail();
  drawReplay();
  drawStandings();
  drawTerritory();
  drawFeed();
}

/**
 * Once a minute on the live week, so a grace countdown moves without a reload.
 * When the week closes with the page open, the page becomes that week's final
 * standings, read afresh so the last moves before the close are in them.
 */
function tick() {
  if (Date.now() < weekAfter(week).getTime()) {
    redraw();
    return;
  }
  current = false;
  clearInterval(ticker);
  drawWeeks();
  fetchWeek();
}

/** Read the week's map and moves, and draw them. */
async function fetchWeek() {
  try {
    const day = weekParam(week);
    const reads = weekQueries(day);
    const [rows, posted, games] = await Promise.all([
      select(reads.graph),
      select(reads.scores),
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
    state = fold(graph, posted);
    // Stay where the scrubber was; a new move arriving while you watch "now" shows it.
    const wasLive = live();
    frames = weekHistory(graph, posted);
    pos = wasLive ? frames.length : Math.min(pos, frames.length);
    boards = new Map(games.map((row) => [row.node_id, row.game_key]));
    redraw();
  } catch (err) {
    mapBox.classList.remove("loading");
    showError(mapBox, err.message);
  }
}

async function load() {
  mountNav();
  mountMyScores();
  drawWeeks();
  if (!configured()) {
    showError(mapBox, "This leaderboard isn't connected to its database yet — see leaderboard/README.md.");
    return;
  }
  await fetchWeek();
  if (current && graph) ticker = setInterval(tick, 60000);
  let width = territoryBox.clientWidth;
  addEventListener("resize", () => {
    if (!graph || territoryBox.clientWidth === width) return;
    width = territoryBox.clientWidth;
    drawTerritory();
  });
}

load();
