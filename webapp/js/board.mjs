// The map as SVG: lanes, systems and fleets, drawn from `serial` snapshots.
// A frame is a pure function of the static map, a board and how far through
// the turn's movement the fleets are (`travel`); nothing here keeps state
// between frames except the SVG nodes it reuses.

import { progressAt } from "./reel.mjs";

const SVG = "http://www.w3.org/2000/svg";
const NEUTRAL = "#7a808c"; // config.COLOR_NEUTRAL
const LANE = "#343a4e";    // config.COLOR_LANE
// config.NODE_MIN_RADIUS / NODE_MAX_RADIUS / FLEET_SIZE / FILM_ARRIVAL_GAP, in
// units of a 1000-wide map; production 2 is richest, 5 poorest (PRODUCTION_WEIGHTS).
const R_MIN = 12, R_MAX = 26, FLEET = 9, GAP = 4, PROD_LO = 2, PROD_HI = 5;

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function rgb([r, g, b]) {
  return `rgb(${r},${g},${b})`;
}

export class BoardView {
  constructor(svg, map) {
    this.svg = svg;
    this.map = map;
    const [x0, y0, x1, y1] = map.world;
    this.unit = Math.max(x1 - x0, y1 - y0) / 1000;
    const pad = 40 * this.unit;
    svg.setAttribute("viewBox", `${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`);
    svg.replaceChildren();
    this.colors = new Map(map.players.map((p) => [p.id, p.neutral ? NEUTRAL : rgb(p.color)]));
    this.pos = new Map(map.systems.map((s) => [s.id, [s.x, s.y]]));
    this.radius = new Map(map.systems.map((s) => {
      const t = Math.min(1, Math.max(0, (s.prod - PROD_LO) / (PROD_HI - PROD_LO)));
      return [s.id, (R_MAX + t * (R_MIN - R_MAX)) * this.unit];
    }));
    const lanes = el("g", { stroke: LANE, "stroke-width": 2 * this.unit }, svg);
    for (const [a, b] of map.lanes) {
      const [ax, ay] = this.pos.get(a);
      const [bx, by] = this.pos.get(b);
      el("line", { x1: ax, y1: ay, x2: bx, y2: by }, lanes);
    }
    this.fleetLayer = el("g", {}, svg);
    this.nodes = new Map();
    const systems = el("g", { "font-family": "DejaVu Sans Mono, monospace", "text-anchor": "middle" }, svg);
    for (const s of map.systems) {
      const g = el("g", { transform: `translate(${s.x} ${s.y})` }, systems);
      const circle = el("circle", { r: this.radius.get(s.id), "stroke-width": 2 * this.unit, stroke: "#0a0c14" }, g);
      const count = el("text", { "dominant-baseline": "central", "font-size": 13 * this.unit, fill: "#0a0c14",
        "font-weight": "bold" }, g);
      const name = el("text", { y: this.radius.get(s.id) + 14 * this.unit, "font-size": 11 * this.unit,
        fill: "#8a90a0" }, g);
      name.textContent = s.name;
      this.nodes.set(s.id, { circle, count });
    }
  }

  /** Draw `board` with fleets `travel` of the way through the turn's step. */
  draw(board, travel) {
    for (const s of board.sys.values()) {
      const node = this.nodes.get(s.id);
      node.circle.setAttribute("fill", this.colors.get(s.owner) || NEUTRAL);
      node.count.textContent = String(s.ships);
    }
    const shapes = [];
    for (const f of board.fleets) shapes.push(this.fleet(f, travel));
    this.fleetLayer.replaceChildren(...shapes);
  }

  /** A fleet's triangle, where `render._fleet_at` puts it: along its lane by
   * `progressAt`, held short of a node it has reached, shifted to its track. */
  fleet(f, travel) {
    const [ax, ay] = this.pos.get(f.src);
    const [bx, by] = this.pos.get(f.dst);
    const p = progressAt(f, travel);
    let x = ax + (bx - ax) * p;
    let y = ay + (by - ay) * p;
    const len = Math.hypot(bx - ax, by - ay) || 1;
    const ux = (bx - ax) / len, uy = (by - ay) / len;
    if (f.remaining <= 0) {
      const gap = this.radius.get(f.dst) + GAP * this.unit;
      if (Math.hypot(bx - x, by - y) < gap) {
        x = bx - ux * gap;
        y = by - uy * gap;
      }
    }
    const spread = (FLEET + 3) * this.unit;
    x -= uy * f.slot * spread;
    y += ux * f.slot * spread;
    const s = FLEET * this.unit;
    const pts = [[x + ux * s, y + uy * s], [x - ux * s - uy * s * 0.7, y - uy * s + ux * s * 0.7],
      [x - ux * s + uy * s * 0.7, y - uy * s - ux * s * 0.7]];
    const g = el("g");
    el("polygon", { points: pts.map((q) => q.join(",")).join(" "), fill: this.colors.get(f.owner) || NEUTRAL,
      stroke: "#0a0c14", "stroke-width": this.unit }, g);
    const label = el("text", { x: x - uy * s * 1.6, y: y + ux * s * 1.6, "font-size": 10 * this.unit,
      fill: "#c8ccd6", "text-anchor": "middle", "dominant-baseline": "central",
      "font-family": "DejaVu Sans Mono, monospace" }, g);
    label.textContent = String(f.ships);
    return g;
  }
}
