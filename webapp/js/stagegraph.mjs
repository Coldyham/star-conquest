// A gauntlet's stages as a graph: the game's gauntlet list (`setupmenu.graph_view`)
// and the board's gauntlet maker (`leaderboard/js/gauntlet-page.mjs`, which
// imports this as /play/js/stagegraph.mjs) both draw with it, so the two
// pictures are one. It decides nothing: it is handed the stages at their
// `gauntlet.layout` cells and the lanes between them, and draws them.
//
// Layout is CSS (docs/design/html-frontend.md): each stage is a button in a CSS
// grid at its cell, sized by its text. The lanes are an SVG layer over the grid,
// drawn from where the buttons actually landed and redrawn when the grid's size
// changes. The box it is drawn into scrolls; nothing here keeps an offset.
//
// A view is `{nodes: [{key, col, row, title, place?, status?, color?, open,
// marks?}], lanes: [{from, to, gated?, short?, hidden?, two_way?, warn?}]}`,
// lanes naming nodes by key. Marks become classes (`sg-start`, `sg-goal`, …),
// which each page styles in its own theme.

const SVG = "http://www.w3.org/2000/svg";
const LOOP = 22;   // how far a lane bends past the nodes: over a skip, under a lane back
const HEAD = 8;    // an arrowhead's length

/** A box: `{x, y, w, h}`. */
const mid = (r) => [r.x + r.w / 2, r.y + r.h / 2];

/**
 * The curve of a lane from box `a` to box `b`, `step` columns on: `{start, c1,
 * c2, end}` (a cubic Bézier). To the next column it runs edge to edge; further
 * on (a shortcut) it arcs over the stages it skips; back or within a column it
 * dips below the two.
 */
export function laneCurve(a, b, step) {
  if (step === 1) {
    const start = [a.x + a.w, mid(a)[1]];
    const end = [b.x, mid(b)[1]];
    const dx = (end[0] - start[0]) / 2;
    return { start, c1: [start[0] + dx, start[1]], c2: [end[0] - dx, end[1]], end };
  }
  if (step > 1) {
    const start = [mid(a)[0] + 6, a.y];
    const end = [mid(b)[0] - 6, b.y];
    const top = Math.min(a.y, b.y) - LOOP;
    return { start, c1: [start[0], top], c2: [end[0], top], end };
  }
  const start = [mid(a)[0] + 6, a.y + a.h];
  const end = [mid(b)[0] - 6, b.y + b.h];
  const bottom = Math.max(a.y + a.h, b.y + b.h) + LOOP;
  return { start, c1: [start[0], bottom], c2: [end[0], bottom], end };
}

/** The curve's halfway point, where a gated lane's target is written. */
export function curveMid({ start, c1, c2, end }) {
  return [0, 1].map((i) => 0.125 * start[i] + 0.375 * c1[i] + 0.375 * c2[i] + 0.125 * end[i]);
}

/** An arrowhead's three corners at `tip`, pointing away from `from`. */
export function arrowPoints(tip, from) {
  const len = Math.hypot(tip[0] - from[0], tip[1] - from[1]) || 1;
  const [ux, uy] = [(tip[0] - from[0]) / len, (tip[1] - from[1]) / len];
  const [bx, by] = [tip[0] - ux * HEAD, tip[1] - uy * HEAD];
  return [tip, [bx - uy * HEAD / 2, by + ux * HEAD / 2], [bx + uy * HEAD / 2, by - ux * HEAD / 2]];
}

/** A node's classes: its marks, and `sg-locked` when it can't be played. */
export function nodeClasses(node) {
  return ["sg-node", node.open === false ? "sg-locked" : "", ...(node.marks || []).map((m) => `sg-${m}`)]
    .filter(Boolean).join(" ");
}

/** A lane's classes, by what it is. */
export function laneClasses(lane) {
  return ["sg-lane", lane.gated ? "sg-gated" : "", lane.hidden ? "sg-hidden" : "", lane.warn ? "sg-warn" : ""]
    .filter(Boolean).join(" ");
}

function svg(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.append(node);
  return node;
}

function span(cls, text, style = "") {
  const node = document.createElement("span");
  node.className = cls;
  node.textContent = text;
  if (style) node.style.cssText = style;
  return node;
}

/**
 * Draw `view` into `box` (which scrolls), replacing what was there. A press on
 * a stage calls `onPress(key)`; `onHover(key | null)` follows the pointer and
 * focus. Answers `{light(from, to), show(key), node(key)}`: light a lane (either
 * way; `light(null)` for none), scroll a stage into view, find its button.
 */
export function drawGraph(box, view, { onPress = null, onHover = null, label = "" } = {}) {
  const nodes = view?.nodes || [];
  const cols = Math.max(1, ...nodes.map((n) => n.col + 1));
  const grid = document.createElement("div");
  grid.className = "sg";
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(6rem, max-content))`;
  if (label) grid.setAttribute("aria-label", label);
  const layer = svg("svg", { class: "sg-lanes", "aria-hidden": "true" });
  grid.append(layer);

  const buttons = new Map();
  for (const n of nodes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = nodeClasses(n);
    button.style.gridColumn = String(n.col + 1);
    button.style.gridRow = String(n.row + 1);
    button.dataset.key = n.key;
    if (n.place) button.append(span("sg-place", n.place));
    button.append(span("sg-title", n.title));
    if (n.status) button.append(span("sg-status", n.status, n.color ? `color:${n.color}` : ""));
    if (n.tip) button.title = n.tip;
    if (onPress) button.addEventListener("click", () => onPress(n.key));
    if (onHover) {
      for (const [type, on] of [["mouseenter", true], ["focus", true], ["mouseleave", false], ["blur", false]]) {
        button.addEventListener(type, () => onHover(on ? n.key : null));
      }
    }
    buttons.set(n.key, button);
    grid.append(button);
  }
  box.replaceChildren(grid);

  const cellOf = new Map(nodes.map((n) => [n.key, n]));
  const drawLanes = () => {
    layer.replaceChildren();
    layer.setAttribute("width", grid.scrollWidth);
    layer.setAttribute("height", grid.scrollHeight);
    const rect = (key) => {
      const b = buttons.get(key);
      return b && { x: b.offsetLeft, y: b.offsetTop, w: b.offsetWidth, h: b.offsetHeight };
    };
    for (const lane of view?.lanes || []) {
      const a = rect(lane.from);
      const b = rect(lane.to);
      if (!a || !b) continue;
      const curve = laneCurve(a, b, cellOf.get(lane.to).col - cellOf.get(lane.from).col);
      const g = svg("g", { class: laneClasses(lane), "data-from": lane.from, "data-to": lane.to }, layer);
      svg("path", { d: `M${curve.start} C${curve.c1} ${curve.c2} ${curve.end}` }, g);
      svg("polygon", { points: arrowPoints(curve.end, curve.c2).join(" "), class: "sg-head" }, g);
      if (lane.two_way) svg("polygon", { points: arrowPoints(curve.start, curve.c1).join(" "), class: "sg-head" }, g);
      if (lane.short) {
        const [x, y] = curveMid(curve);
        svg("text", { x, y: y - 5, "text-anchor": "middle", class: "sg-short" }, g).textContent = lane.short;
      }
    }
  };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(drawLanes).observe(grid);
  else requestAnimationFrame(drawLanes);

  return {
    node: (key) => buttons.get(key) || null,
    light(from, to = null) {
      for (const g of layer.querySelectorAll(".sg-lane")) {
        const [a, b] = [g.dataset.from, g.dataset.to];
        g.classList.toggle("sg-lit", from !== null && ((a === from && b === to) || (a === to && b === from)
                                                       || (to === null && (a === from || b === from))));
      }
    },
    show(key, how = { block: "nearest", inline: "nearest" }) {
      buttons.get(key)?.scrollIntoView(how);
    },
  };
}
