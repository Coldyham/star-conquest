// The map as SVG: lanes, systems and fleets, drawn from `serial` snapshots.
// A frame is a pure function of the static map, a board and how far through
// the turn's movement the fleets are (`travel`); nothing here keeps state
// between frames except the SVG nodes it reuses.
//
// Sizes are screen pixels, as in `render`: a system's radius, a label's text
// and a lane's width stay the same whatever the map's extent. `unit` is how
// many map units one pixel spans at the size the SVG had when it was built.

import { progressAt } from "./reel.mjs";

const SVG = "http://www.w3.org/2000/svg";
const MONO = "DejaVu Sans Mono, monospace";

// config.COLOR_*
const BG = "#0a0c14";
const LANE = [52, 58, 78];
const NEUTRAL = [122, 128, 140];
const TEXT = [225, 230, 240];
const TEXT_DARK = [12, 14, 22];
const TEXT_DIM = "rgb(140,148,165)";

// config, in pixels: NODE_MIN_RADIUS / NODE_MAX_RADIUS for production 5 (poorest)
// and 2 (richest), FLEET_SIZE, FILM_ARRIVAL_GAP, NODE_LABEL_GAP, the progress
// ring's offset, node_clearance() and MAP_FIT_PADDING, FONT_SIZE / FONT_SIZE_SMALL.
const R_MIN = 12, R_MAX = 26, PROD_LO = 2, PROD_HI = 5;
const FLEET = 9, ARRIVAL_GAP = 4, LABEL_GAP = 5, RING_GAP = 4;
const FIT_PAD = Math.max(50, R_MAX + 6 + 4);
const FONT = 18, FONT_SMALL = 14;
const CHAR_W = 0.602;          // DejaVu Sans Mono's advance, in ems
const LINE_H = 1.17;           // ...and its line height

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function rgb([r, g, b]) {
  return `rgb(${r},${g},${b})`;
}

/** widgets.brighten: each channel up by 60, capped. */
function brighten(color) {
  return color.map((c) => Math.min(255, c + 60));
}

function luminance(color) {
  const [r, g, b] = color.map((c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** config.text_on: dark or light text, whichever reads better on `bg`. */
function textOn(bg) {
  return contrast(TEXT_DARK, bg) >= contrast(TEXT, bg) ? TEXT_DARK : TEXT;
}

/** widgets.lane_style: a lane's width (px) and colour from its travel time;
 * fast lanes thin and bright, slow ones thick and dim. */
function laneStyle(turns) {
  const width = Math.max(2, Math.min(6, turns));
  const f = (Math.min(6, Math.max(1, turns)) - 1) / 5;
  const scale = 1.3 - 0.6 * f;
  return [width, LANE.map((c) => Math.min(255, Math.trunc(c * scale)))];
}

/** The production-progress arc: from the top, anticlockwise, `frac` of the way round. */
function arcPath(r, frac) {
  const a = Math.PI / 2 + 2 * Math.PI * frac;
  const x = r * Math.cos(a), y = -r * Math.sin(a);
  return `M 0 ${-r} A ${r} ${r} 0 ${frac > 0.5 ? 1 : 0} 0 ${x} ${y}`;
}

export class BoardView {
  constructor(svg, map) {
    this.svg = svg;
    this.map = map;
    const [x0, y0, x1, y1] = map.world;
    // Fit the map with FIT_PAD pixels clear on every side, as `WorldView` does.
    const box = svg.getBoundingClientRect();
    const span = Math.max(x1 - x0, y1 - y0);
    const u = box.width > 4 * FIT_PAD && box.height > 4 * FIT_PAD
      ? Math.max((x1 - x0) / (box.width - 2 * FIT_PAD), (y1 - y0) / (box.height - 2 * FIT_PAD))
      : span / 1000;
    this.unit = u;
    const pad = FIT_PAD * u;
    svg.setAttribute("viewBox", `${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`);
    svg.replaceChildren();
    this.colors = new Map(map.players.map((p) => [p.id, p.neutral ? NEUTRAL : p.color]));
    this.prod = new Map(map.systems.map((s) => [s.id, s.prod]));
    this.pos = new Map(map.systems.map((s) => [s.id, [s.x, s.y]]));
    this.radius = new Map(map.systems.map((s) => {
      const t = Math.min(1, Math.max(0, (s.prod - PROD_LO) / (PROD_HI - PROD_LO)));
      return [s.id, Math.round(R_MAX + t * (R_MIN - R_MAX)) * u];
    }));
    // Lanes, then each lane's travel time on a plate at its midpoint
    // (`widgets.label_pill`); `setTravel` styles and fills both.
    const lanes = el("g", {}, svg);
    const plates = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL * u }, svg);
    this.lanes = map.lanes.map(([a, b]) => {
      const [ax, ay] = this.pos.get(a);
      const [bx, by] = this.pos.get(b);
      const line = el("line", { x1: ax, y1: ay, x2: bx, y2: by }, lanes);
      const g = el("g", { transform: `translate(${(ax + bx) / 2} ${(ay + by) / 2})` }, plates);
      const rect = el("rect", { fill: BG, rx: 5 * u }, g);
      const text = el("text", { "dominant-baseline": "central", fill: TEXT_DIM }, g);
      return { line, rect, text };
    });
    this.fleetLayer = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL * u }, svg);
    this.nodes = new Map();
    const systems = el("g", { "font-family": MONO, "text-anchor": "middle" }, svg);
    for (const s of map.systems) {
      const g = el("g", { transform: `translate(${s.x} ${s.y})` }, systems);
      const r = this.radius.get(s.id);
      // pygame strokes inside the disc; an SVG stroke straddles its path.
      const circle = el("circle", { r: r - u, "stroke-width": 2 * u }, g);
      const ring = el("path", { fill: "none", stroke: TEXT_DIM, "stroke-width": 2 * u }, g);
      const count = el("text", { "dominant-baseline": "central", "font-size": FONT * u }, g);
      const name = el("text", { y: r + (LABEL_GAP + FONT_SMALL * LINE_H / 2) * u, "font-size": FONT_SMALL * u,
        "dominant-baseline": "central", fill: TEXT_DIM }, g);
      name.textContent = s.name;
      this.nodes.set(s.id, { circle, ring, count, r });
    }
  }

  /** Style and label each lane by its travel time: `travel` is the session's
   * list, in the static map's lane order. */
  setTravel(travel) {
    const u = this.unit, h = FONT_SMALL * LINE_H * u + 4 * u;
    this.lanes.forEach(({ line, rect, text }, i) => {
      const [width, color] = laneStyle(travel[i]);
      line.setAttribute("stroke", rgb(color));
      line.setAttribute("stroke-width", width * u);
      const label = String(travel[i]);
      const w = label.length * FONT_SMALL * CHAR_W * u + 8 * u;
      text.textContent = label;
      for (const [k, v] of Object.entries({ x: -w / 2, y: -h / 2, width: w, height: h })) rect.setAttribute(k, v);
    });
  }

  /** Draw `board` with fleets `travel` of the way through the turn's step. */
  draw(board, travel) {
    for (const s of board.sys.values()) {
      const node = this.nodes.get(s.id);
      const color = this.colors.get(s.owner) || NEUTRAL;
      node.circle.setAttribute("fill", rgb(color));
      node.circle.setAttribute("stroke", rgb(brighten(color)));
      node.count.setAttribute("fill", rgb(textOn(color)));
      node.count.textContent = String(s.ships);
      const prod = this.prod.get(s.id);
      const frac = prod > 0 ? s.prog / prod : 0;
      node.ring.setAttribute("d", frac > 0 ? arcPath(node.r + RING_GAP * this.unit, frac) : "");
    }
    const shapes = [];
    for (const f of board.fleets) shapes.push(this.fleet(f, travel));
    this.fleetLayer.replaceChildren(...shapes);
  }

  /** A fleet's triangle, where `render._fleet_at` puts it: along its lane by
   * `progressAt`, held short of a node it has reached, shifted to its track. */
  fleet(f, travel) {
    const u = this.unit;
    const [ax, ay] = this.pos.get(f.src);
    const [bx, by] = this.pos.get(f.dst);
    const p = progressAt(f, travel);
    let x = ax + (bx - ax) * p;
    let y = ay + (by - ay) * p;
    const len = Math.hypot(bx - ax, by - ay) || 1;
    const ux = (bx - ax) / len, uy = (by - ay) / len;
    if (f.remaining <= 0) {
      const gap = this.radius.get(f.dst) + ARRIVAL_GAP * u;
      if (Math.hypot(bx - x, by - y) < gap) {
        x = bx - ux * gap;
        y = by - uy * gap;
      }
    }
    const spread = (FLEET + 3) * u;
    x -= uy * f.slot * spread;
    y += ux * f.slot * spread;
    const s = FLEET * u;
    const pts = [[x + ux * s, y + uy * s], [x - ux * s - uy * s * 0.7, y - uy * s + ux * s * 0.7],
      [x - ux * s + uy * s * 0.7, y - uy * s - ux * s * 0.7]];
    const g = el("g");
    el("polygon", { points: pts.map((q) => q.join(",")).join(" "), fill: rgb(this.colors.get(f.owner) || NEUTRAL) },
      g);
    const label = el("text", { x: x - uy * spread, y: y + ux * spread, fill: rgb(TEXT),
      "dominant-baseline": "central" }, g);
    label.textContent = String(f.ships);
    return g;
  }
}
