// The map as SVG: lanes, systems, fleets and the orders on them, drawn from
// `serial` snapshots through a `Camera`.
//
// Everything is in screen pixels, as in `render`: a system's radius, a label's
// text and a lane's width stay the same at any zoom, and only positions move
// with the camera. A frame is a pure function of the static map, a board, how
// far through the turn's movement the fleets are (`travel`), and what the
// player is looking at (`look`); nothing here keeps state between frames
// except the SVG nodes it reuses.

import { Camera } from "./camera.mjs";
import { markFade, markPhase } from "./marks.mjs";
import { progressAt } from "./reel.mjs";

const SVG = "http://www.w3.org/2000/svg";
const MONO = "DejaVu Sans Mono, monospace";

// config.COLOR_*
const BG = "#0a0c14";
const LANE = [52, 58, 78];
const LANE_HILITE = "rgb(120,140,190)";
const NEUTRAL = [122, 128, 140];
const TEXT = [225, 230, 240];
const TEXT_DARK = [12, 14, 22];
const TEXT_DIM = "rgb(140,148,165)";
const SELECT = "rgb(250,240,150)";
const FOG = [58, 62, 82];
const DANGER = "rgb(200,96,104)";        // widgets.BTN_DANGER's edge
const FORWARD = "rgb(110,205,195)";      // render._forward_accent

// config, in pixels: NODE_MIN_RADIUS / NODE_MAX_RADIUS for production 5 (poorest)
// and 2 (richest), FLEET_SIZE, FILM_ARRIVAL_GAP, NODE_LABEL_GAP, the progress
// ring's offset, NODE_RING_PAD, node_clearance() and MAP_FIT_PADDING,
// NODE_TAP_MIN, LANE_PICK_DIST, the arrowheads, FONT_SIZE / FONT_SIZE_SMALL.
const R_MIN = 12, R_MAX = 26, PROD_LO = 2, PROD_HI = 5;
const FLEET = 9, ARRIVAL_GAP = 4, LABEL_GAP = 5, RING_GAP = 4, RING_PAD = 6;
export const FIT_PAD = Math.max(50, R_MAX + RING_PAD + 4);
const TAP_MIN = 22, LANE_PICK = 10;
const ARROW = 13, ARROW_GAP = 6, ARROW_WING = 0.6;
// config.RULE_*: a rule's conveyor, its crawl, and where its share label sits.
const CHEVRON = 8, CHEVRON_GAP = 16, FLOW_MS = 700, RULE_LABEL_GAP = 16, RULE_LABEL_FRAC = 0.35;
const CLEARANCE = R_MAX + RING_PAD + 4;   // config.node_clearance()
// config.FILM_BURST_*, FILM_LOSS_GAP, NODE_LABEL_PAD
const BURST_R = 22, BURST_W = 2, BURST_SPOKES = 6, LOSS_GAP = 12, NAME_PAD = 3;
const FONT = 18, FONT_SMALL = 14;
const CHAR_W = 0.602;          // DejaVu Sans Mono's advance, in ems
const LINE_H = 1.17;           // ...and its line height

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function set(node, attrs) {
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
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

function segmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** An open chevron with its tip at (tx, ty), pointing along (ux, uy). */
function chevron(parent, tx, ty, ux, uy, size, color, width) {
  const backX = tx - ux * size, backY = ty - uy * size;
  const wing = size * ARROW_WING;
  el("polyline", { points: `${backX - uy * wing},${backY + ux * wing} ${tx},${ty} ${backX + uy * wing},${backY - ux * wing}`,
    fill: "none", stroke: color, "stroke-width": width, "stroke-linejoin": "round" }, parent);
}

/** `render._rule_chevron_dists`: where along a rule's lane its chevrons sit,
 * walked one spacing forward over a cycle by `phase` (0..1). */
function chevronDists(length, srcR, destR, phase) {
  const start = srcR + ARROW_GAP + CHEVRON;
  const span = length - (destR + ARROW_GAP) - start;
  if (span <= CHEVRON) return [length / 2];
  const count = Math.max(2, Math.round(span / CHEVRON_GAP));
  const step = span / count;
  const out = [];
  for (let i = 0; i <= count; i++) if (i + phase <= count) out.push(start + (i + phase) * step);
  return out;
}

/** A standing rule's lane: a conveyor of small chevrons toward the destination. */
function ruleFlow(parent, a, b, srcR, destR, color, width, phase) {
  const [ax, ay] = a, [bx, by] = b;
  const len = Math.hypot(bx - ax, by - ay) || 1;
  const ux = (bx - ax) / len, uy = (by - ay) / len;
  for (const d of chevronDists(len, srcR, destR, phase)) {
    chevron(parent, ax + ux * d, ay + uy * d, ux, uy, CHEVRON, color, width);
  }
}

const PILL_H = FONT_SMALL * LINE_H + 4;

function pillWidth(text) {
  return text.length * FONT_SMALL * CHAR_W + 8;
}

/** Text on a small dark plate, centred on (x, y) (`widgets.label_pill`). */
function pill(parent, x, y, text, color) {
  const w = pillWidth(text);
  el("rect", { x: x - w / 2, y: y - PILL_H / 2, width: w, height: PILL_H, rx: 5, fill: BG }, parent);
  const t = el("text", { x, y, fill: color, "dominant-baseline": "central" }, parent);
  t.textContent = text;
}

function pillRect(x, y, text) {
  const w = pillWidth(text);
  return [x - w / 2, y - PILL_H / 2, w, PILL_H];
}

/** `render._faded`: `color` blended toward the background by `fade`. */
function faded([r, g, b], fade) {
  const bg = [10, 12, 20];
  return rgb([r, g, b].map((c, i) => Math.round(c + (bg[i] - c) * fade)));
}

function overlaps(a, b) {
  return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
}

/** `render._rule_label_center`: a rule's share label, just past the sending
 * system and off the lane, trying the other side and further along to clear
 * `avoid` (the source's hold badge). */
function ruleLabelAt(a, b, avoid, text) {
  const [ax, ay] = a, [bx, by] = b;
  const length = Math.hypot(bx - ax, by - ay) || 1;
  const ux = (bx - ax) / length, uy = (by - ay) / length;
  const [px, py] = ux >= 0 ? [-uy, ux] : [uy, -ux];
  const base = Math.min(CLEARANCE + RULE_LABEL_GAP, length * RULE_LABEL_FRAC);
  const clear = CLEARANCE + RULE_LABEL_GAP;
  const lift = FONT_SMALL * LINE_H + 8;
  const at = (along, side) => {
    const off = Math.max(lift, Math.sqrt(Math.max(0, clear * clear - along * along)));
    return [ax + ux * along + side * px * off, ay + uy * along + side * py * off];
  };
  const first = at(base, 1);
  if (!avoid) return first;
  const w = pillWidth(text);
  for (const along of [base, base * 1.6, base * 2.2]) {
    for (const side of [1, -1]) {
      const [x, y] = at(Math.min(along, length / 2), side);
      if (!overlaps([x - w / 2, y - PILL_H / 2, w, PILL_H], avoid)) return [x, y];
    }
  }
  return first;
}

/** A planned send: the lane as a line, an open chevron short of the destination. */
function planned(parent, a, b, destR, color, width) {
  const [ax, ay] = a, [bx, by] = b;
  const len = Math.hypot(bx - ax, by - ay) || 1;
  const ux = (bx - ax) / len, uy = (by - ay) / len;
  el("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: color, "stroke-width": width }, parent);
  chevron(parent, bx - ux * (destR + ARROW_GAP), by - uy * (destR + ARROW_GAP), ux, uy, ARROW, color, width);
}

export class BoardView {
  constructor(svg, map) {
    this.svg = svg;
    this.map = map;
    svg.replaceChildren();
    const defs = el("defs", {}, svg);
    const hatch = el("pattern", { id: "fog-hatch", patternUnits: "userSpaceOnUse", width: 6, height: 6,
      patternTransform: "rotate(45)" }, defs);
    el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: rgb(brighten(FOG)), "stroke-width": 1 }, hatch);
    this.colors = new Map(map.players.map((p) => [p.id, p.neutral ? NEUTRAL : p.color]));
    this.prod = new Map(map.systems.map((s) => [s.id, s.prod]));
    this.world = new Map(map.systems.map((s) => [s.id, [s.x, s.y]]));
    this.nbrs = new Map(map.systems.map((s) => [s.id, new Set(s.nbrs)]));
    this.radius = new Map(map.systems.map((s) => {
      const t = Math.min(1, Math.max(0, (s.prod - PROD_LO) / (PROD_HI - PROD_LO)));
      return [s.id, Math.round(R_MAX + t * (R_MIN - R_MAX))];
    }));
    this.pos = new Map();
    this.travel = map.lanes.map(() => 1);
    const box = svg.getBoundingClientRect();
    this.camera = new Camera(map.world, [0, 0, Math.max(1, box.width), Math.max(1, box.height)], FIT_PAD);

    const laneLayer = el("g", {}, svg);
    const plates = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL }, svg);
    this.lanes = map.lanes.map(([a, b]) => {
      const line = el("line", {}, laneLayer);
      const g = el("g", {}, plates);
      const rect = el("rect", { fill: BG, rx: 5 }, g);
      const text = el("text", { "dominant-baseline": "central", fill: TEXT_DIM }, g);
      return { a, b, line, g, rect, text };
    });
    this.orderLayer = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL,
      "stroke-linecap": "round" }, svg);
    this.fleetLayer = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL }, svg);
    this.nodes = new Map();
    const systems = el("g", { "font-family": MONO, "text-anchor": "middle" }, svg);
    for (const s of map.systems) {
      const r = this.radius.get(s.id);
      const g = el("g", {}, systems);
      const mark = el("circle", { fill: "none" }, g);
      // pygame strokes inside the disc; an SVG stroke straddles its path.
      const circle = el("circle", { r: r - 1, "stroke-width": 2 }, g);
      const hatchDisc = el("circle", { r: r - 1, fill: "url(#fog-hatch)" }, g);
      const ring = el("path", { fill: "none", stroke: TEXT_DIM, "stroke-width": 2 }, g);
      const count = el("text", { "dominant-baseline": "central", "font-size": FONT }, g);
      this.nodes.set(s.id, { g, mark, circle, hatchDisc, ring, count, r, name: s.name });
    }
    this.markLayer = el("g", { "font-family": MONO, "text-anchor": "middle", "font-size": FONT_SMALL,
      fill: "none" }, svg);
    this.nameLayer = el("g", { "font-family": MONO, "font-size": FONT_SMALL, "dominant-baseline": "hanging" }, svg);
    this.dragLayer = el("g", {}, svg);
    this.layout();
  }

  /** The SVG's box changed: refit the camera to it. */
  resize() {
    const box = this.svg.getBoundingClientRect();
    this.camera.resize([0, 0, Math.max(1, box.width), Math.max(1, box.height)]);
    this.layout();
  }

  /** Recompute every system's screen position after the camera moved. */
  layout() {
    const box = this.camera.screen;
    this.svg.setAttribute("viewBox", `0 0 ${box[2]} ${box[3]}`);
    for (const [id, [x, y]] of this.world) this.pos.set(id, this.camera.toScreen(x, y));
    for (const [id, node] of this.nodes) {
      const [x, y] = this.pos.get(id);
      node.g.setAttribute("transform", `translate(${x} ${y})`);
    }
    for (const lane of this.lanes) {
      const [ax, ay] = this.pos.get(lane.a);
      const [bx, by] = this.pos.get(lane.b);
      set(lane.line, { x1: ax, y1: ay, x2: bx, y2: by });
      lane.g.setAttribute("transform", `translate(${(ax + bx) / 2} ${(ay + by) / 2})`);
    }
  }

  /** Style and label each lane by its travel time: `travel` is the session's
   * list, in the static map's lane order. */
  setTravel(travel) {
    this.travel = travel;
    const h = FONT_SMALL * LINE_H + 4;
    this.lanes.forEach(({ rect, text }, i) => {
      const label = String(travel[i]);
      const w = label.length * FONT_SMALL * CHAR_W + 8;
      text.textContent = label;
      set(rect, { x: -w / 2, y: -h / 2, width: w, height: h });
    });
  }

  /** The system under screen point (px, py): the nearest within reach
   * (`input.pick_node`), or null. */
  pick(px, py) {
    let best = null, bestD = 0;
    for (const [id, [x, y]] of this.pos) {
      const d = Math.hypot(px - x, py - y);
      if (d <= Math.max(this.radius.get(id) + 2, TAP_MIN) && (best === null || d < bestD)) {
        best = id;
        bestD = d;
      }
    }
    return best;
  }

  /** The queued orders whose lane passes within reach of (px, py), nearest
   * first, as `play.LaneTag`s (`input._pick_lane`). */
  laneHits(px, py, view) {
    const hits = [];
    view.pending.forEach(([src, dst], i) => {
      const [ax, ay] = this.pos.get(src);
      const [bx, by] = this.pos.get(dst);
      const d = segmentDist(px, py, ax, ay, bx, by);
      if (d <= LANE_PICK) hits.push([d, ["order", i, -1]]);
    });
    for (const rule of view.rules) {
      if (!rule.live) continue;   // only the rules that fire are pickable
      const [ax, ay] = this.pos.get(rule.src);
      for (const [dst] of rule.lanes) {
        const [bx, by] = this.pos.get(dst);
        const d = segmentDist(px, py, ax, ay, bx, by);
        if (d <= LANE_PICK) hits.push([d, ["rule", rule.src, dst]]);
      }
    }
    return hits.sort((a, b) => a[0] - b[0]).map(([, tag]) => tag);
  }

  /** Draw `board` with fleets `travel` of the way through the turn's step.
   * `look` is what the player sees and is doing: `sees(id)`, `seen` (a Set),
   * and the session's `view` (selection, orders, the popup) plus the page's
   * own `hover` and `drag`; without it, everything is in plain sight. */
  draw(board, travel, look = null, now = 0) {
    const view = look?.view ?? null;
    const sees = look?.sees ?? (() => true);
    const fogState = (id) => (sees(id) ? "visible" : look?.seen?.has(id) ? "fogged" : "hidden");
    const source = view?.source ?? null;
    const valid = source !== null ? this.nbrs.get(source) : new Set();
    const human = view?.human ?? null;
    const carrying = new Set(board.fleets.map((f) => `${Math.min(f.src, f.dst)}-${Math.max(f.src, f.dst)}`));
    this.taken = [];   // labels a star name must not land on
    this.floor = new Map();   // a badged system's name goes under its badge

    this.lanes.forEach((lane, i) => {
      const sa = fogState(lane.a), sb = fogState(lane.b);
      const shown = (sa !== "hidden" && sb !== "hidden") || carrying.has(`${lane.a}-${lane.b}`);
      lane.line.style.display = shown ? "" : "none";
      const plate = shown && (sa === "visible" || sb === "visible");
      lane.g.style.display = plate ? "" : "none";
      if (plate) {
        const [ax, ay] = this.pos.get(lane.a), [bx, by] = this.pos.get(lane.b);
        this.taken.push(pillRect((ax + bx) / 2, (ay + by) / 2, String(this.travel[i])));
      }
      let [width, color] = laneStyle(this.travel[i]);
      color = sa !== "visible" || sb !== "visible" ? rgb(FOG) : rgb(color);
      if (source !== null && (lane.a === source || lane.b === source)) {
        color = LANE_HILITE;
        width = Math.max(width, 3);
      }
      set(lane.line, { stroke: color, "stroke-width": width });
    });

    this.orderLayer.replaceChildren();
    const badged = new Set();
    if (view) {
      this.drawRules(view, human, now, badged);
      this.drawOrders(view, human, now);
    }

    for (const s of board.sys.values()) {
      const node = this.nodes.get(s.id);
      const state = fogState(s.id);
      node.g.style.display = state === "hidden" ? "none" : "";
      const r = node.r;
      if (view && s.id === view.selected) {
        set(node.mark, { r: r + RING_PAD, stroke: SELECT, "stroke-width": 3, display: "" });
      } else if (valid.has(s.id)) {
        set(node.mark, { r: r + RING_GAP, stroke: LANE_HILITE, "stroke-width": 2, display: "" });
      } else if (look && s.id === look.hover) {
        set(node.mark, { r: r + RING_GAP, stroke: TEXT_DIM, "stroke-width": 2, display: "" });
      } else {
        node.mark.setAttribute("display", "none");
      }
      if (state === "fogged") {
        set(node.circle, { fill: rgb(FOG), stroke: rgb(brighten(FOG)) });
        node.hatchDisc.setAttribute("display", "");
        node.ring.setAttribute("d", "");
        set(node.count, { fill: rgb(TEXT) });
        node.count.textContent = "?";
        continue;
      }
      node.hatchDisc.setAttribute("display", "none");
      const color = this.colors.get(s.owner) || NEUTRAL;
      set(node.circle, { fill: rgb(color), stroke: rgb(brighten(color)) });
      node.count.setAttribute("fill", rgb(textOn(color)));
      const own = view && s.owner === human && view.available[s.id] !== undefined;
      node.count.textContent = String(own ? view.available[s.id] : s.ships);
      const prod = this.prod.get(s.id);
      const frac = prod > 0 ? s.prog / prod : 0;
      node.ring.setAttribute("d", frac > 0 ? arcPath(r + RING_GAP, frac) : "");
    }

    const shapes = [];
    for (const f of board.fleets) {
      if (f.owner === human || sees(f.src) || sees(f.dst) || human === null) shapes.push(this.fleet(f, travel));
    }
    this.fleetLayer.replaceChildren(...shapes);
    this.drawMarks(look?.marks ?? null);
    this.drawNames(look, fogState);
    this.drawDrag(look, human);
  }

  /** Every fading fight's burst, and the numbers over the map: each side's
   * losses and each finished hull, stacked over their system in the order
   * they resolved (`render._draw_film_flashes`, `_film_labels`). */
  drawMarks(marks) {
    this.markLayer.replaceChildren();
    if (!marks) return;
    const stacks = new Map();
    const stack = (key) => {
      if (!stacks.has(key)) stacks.set(key, []);
      return stacks.get(key);
    };
    for (const h of marks.hulls) {
      stack(h.node).push([`+${h.hulls}`, faded(this.colors.get(h.owner) || NEUTRAL, markFade(h.age))]);
    }
    for (const f of marks.fights) {
      let at;
      if (f.node === null) {
        const [ax, ay] = this.pos.get(f.low), [bx, by] = this.pos.get(f.high);
        at = [ax + (bx - ax) * f.at, ay + (by - ay) * f.at];
      } else {
        at = this.pos.get(f.node);
      }
      const phase = markPhase(f.age), fade = markFade(f.age);
      const color = faded(f.color === null ? TEXT : this.colors.get(f.color) || NEUTRAL, fade);
      const reach = BURST_R * (0.35 + 0.65 * phase);
      const width = Math.max(1, Math.round(BURST_W * (1 - phase * 0.5)));
      el("circle", { cx: at[0], cy: at[1], r: reach, stroke: color, "stroke-width": width }, this.markLayer);
      for (let i = 0; i < BURST_SPOKES; i++) {
        const angle = 2 * Math.PI * i / BURST_SPOKES + phase;
        const ux = Math.cos(angle), uy = Math.sin(angle);
        el("line", { x1: at[0] + ux * reach, y1: at[1] + uy * reach, x2: at[0] + ux * (reach + BURST_R * 0.4),
          y2: at[1] + uy * (reach + BURST_R * 0.4), stroke: color, "stroke-width": width }, this.markLayer);
      }
      const items = stack(f.node === null ? `${at[0]},${at[1]}` : f.node);
      for (const [owner, lost] of f.losses) {
        items.push([`−${lost}`, faded(this.colors.get(owner) || NEUTRAL, fade)]);
      }
    }
    for (const [key, items] of stacks) {
      const at = typeof key === "number" ? this.pos.get(key) : key.split(",").map(Number);
      const x = at[0], y = at[1] - BURST_R - LOSS_GAP;
      items.slice().reverse().forEach(([text, color], above) => {
        const yy = y - above * 22;
        pill(this.markLayer, x, yy, text, color);
        this.taken.push(pillRect(x, yy, text));
      });
    }
  }

  /** Star names, placed where they hit neither a system, nor the slot over it
   * a film writes into, nor any label already on the map; what you are looking
   * at gets first refusal: the selection, the hover, then the biggest systems
   * (`render._draw_node_names`). A crowded name waits for a closer zoom. */
  drawNames(look, fogState) {
    this.nameLayer.replaceChildren();
    const view = look?.view ?? null;
    const [, , vw, vh] = this.camera.screen;
    const shown = [...this.nodes.keys()].filter((id) => fogState(id) !== "hidden");
    const taken = this.taken.slice();
    for (const id of shown) {
      const [x, y] = this.pos.get(id);
      const r = this.radius.get(id);
      taken.push([x - r, y - r, 2 * r, 2 * r]);
      const slot = pillRect(x, y - BURST_R - LOSS_GAP, "+00");
      taken.push([slot[0], slot[1] - 22, slot[2], slot[3] + 22]);
    }
    const selected = view?.selected ?? null, hover = look?.hover ?? null;
    const rank = (id) => (id === selected ? -1e9 : id === hover ? -1e8 : -this.radius.get(id));
    for (const id of shown.sort((a, b) => rank(a) - rank(b))) {
      const [x, y] = this.pos.get(id);
      if (x < 0 || y < 0 || x > vw || y > vh) continue;
      const name = this.nodes.get(id).name;
      const w = name.length * FONT_SMALL * CHAR_W, h = FONT_SMALL * LINE_H;
      const r = this.radius.get(id);
      let placed = null;
      for (const side of [1, -1]) {
        let edge = y + side * (r + LABEL_GAP);
        if (side > 0 && this.floor.has(id)) edge = this.floor.get(id) + NAME_PAD + LABEL_GAP;
        let rx = x - w / 2, ry = side > 0 ? edge : edge - h;
        rx = Math.max(0, Math.min(vw - w, rx));
        ry = Math.max(0, Math.min(vh - h, ry));
        const padded = [rx - NAME_PAD, ry - NAME_PAD, w + 2 * NAME_PAD, h + 2 * NAME_PAD];
        if (!taken.some((t) => overlaps(padded, t))) {
          placed = [rx, ry];
          taken.push(padded);
          break;
        }
      }
      if (!placed) continue;
      const color = id === selected ? SELECT : id === hover ? rgb(TEXT) : TEXT_DIM;
      const t = el("text", { x: placed[0], y: placed[1] + 1, fill: color }, this.nameLayer);
      t.textContent = name;
    }
  }

  /** Standing rules as a chevron conveyor in our colour, the one being edited
   * in the select colour with its focused lane crawling, a lane into a system
   * we don't hold tinted as dangerous; each lane's share where less than
   * everything, and a hold badge under its source (`render._draw_forward_rules`).
   * Adds each badged source to `badged`, whose name then goes under the badge. */
  drawRules(view, human, now, badged) {
    const own = rgb(this.colors.get(human) || NEUTRAL);
    const editing = view.editing_rule;
    for (const rule of view.rules) {
      const selected = rule.src === editing;
      const a = this.pos.get(rule.src);
      const r = this.radius.get(rule.src);
      let avoid = null;
      if (rule.badge) {
        const w = pillWidth(rule.badge);
        const y = a[1] + r + LABEL_GAP + PILL_H / 2;
        avoid = [a[0] - w / 2, y - PILL_H / 2, w, PILL_H];
        badged.add(rule.src);
        this.taken.push(avoid);
        this.floor.set(rule.src, avoid[1] + PILL_H);
      }
      for (const [dst, pct, hostile] of rule.lanes) {
        const focused = selected && (dst === view.dest || view.mode !== "choosing");
        const color = selected ? SELECT : hostile ? DANGER : own;
        const phase = focused ? (now % FLOW_MS) / FLOW_MS : 0;
        const b = this.pos.get(dst);
        ruleFlow(this.orderLayer, a, b, r, this.radius.get(dst), color, focused ? 3 : 2, phase);
        if (pct < 100) {
          const [x, y] = ruleLabelAt(a, b, avoid, `${pct}%`);
          pill(this.orderLayer, x, y, `${pct}%`, selected ? rgb(TEXT) : TEXT_DIM);
          this.taken.push(pillRect(x, y, `${pct}%`));
        }
      }
      if (rule.badge) pill(this.orderLayer, a[0], avoid[1] + PILL_H / 2, rule.badge, selected ? rgb(TEXT) : FORWARD);
    }
  }

  /** Queued sends, the order being edited in the select colour, and the move
   * the popup is editing (`render._draw_pending`, `_draw_choosing_preview`). */
  drawOrders(view, human, now) {
    const own = rgb(this.colors.get(human) || NEUTRAL);
    view.pending.forEach(([src, dst, ships], i) => {
      const a = this.pos.get(src), b = this.pos.get(dst);
      const chosen = i === view.sel_order;
      planned(this.orderLayer, a, b, this.radius.get(dst), chosen ? SELECT : own, chosen ? 5 : 3);
      const label = el("text", { x: a[0] + (b[0] - a[0]) * 0.4, y: a[1] + (b[1] - a[1]) * 0.4 - 10,
        fill: rgb(TEXT), "dominant-baseline": "central" }, this.orderLayer);
      label.textContent = String(ships);
    });
    if (view.mode === "choosing" && view.selected !== null && view.dest !== null) {
      const a = this.pos.get(view.selected), b = this.pos.get(view.dest);
      if (view.popup?.forward) {
        ruleFlow(this.orderLayer, a, b, this.radius.get(view.selected), this.radius.get(view.dest), SELECT, 3,
          (now % FLOW_MS) / FLOW_MS);
      } else {
        planned(this.orderLayer, a, b, this.radius.get(view.dest), SELECT, 3);
      }
    }
  }

  /** The rubber band while dragging from a source toward a target (`render._draw_drag`). */
  drawDrag(look, human) {
    this.dragLayer.replaceChildren();
    const drag = look?.drag;
    if (!drag?.active) return;
    const [ax, ay] = this.pos.get(drag.src);
    const col = rgb(this.colors.get(human) || NEUTRAL);
    el("line", { x1: ax, y1: ay, x2: drag.x, y2: drag.y, stroke: col, "stroke-width": 3 }, this.dragLayer);
    el("circle", { cx: drag.x, cy: drag.y, r: 5, fill: "none", stroke: col, "stroke-width": 2 }, this.dragLayer);
    const t = look.hover;
    if (t !== null && t !== drag.src && this.nbrs.get(drag.src).has(t)) {
      const [tx, ty] = this.pos.get(t);
      el("circle", { cx: tx, cy: ty, r: this.radius.get(t) + 4, fill: "none", stroke: SELECT, "stroke-width": 2 },
        this.dragLayer);
    }
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
      const gap = this.radius.get(f.dst) + ARRIVAL_GAP;
      if (Math.hypot(bx - x, by - y) < gap) {
        x = bx - ux * gap;
        y = by - uy * gap;
      }
    }
    const spread = FLEET + 3;
    x -= uy * f.slot * spread;
    y += ux * f.slot * spread;
    const s = FLEET;
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
