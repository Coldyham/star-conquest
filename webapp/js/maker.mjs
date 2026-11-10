// The map maker: drawn from the session's `maker` (`starconquest.maker.view`)
// as it stands. Python owns the recipe, what every press and key means and
// every word; the page hit-tests in its own pixels and sends what a press
// landed on (a system, the lanes near it, a control's key, a point in world
// units). What stays here is only about looking: the camera, a drag's
// threshold and rubber band, the Owners tool's box, and the status line's
// clock. Laid out as pygame's `mapmaker`: the tool strip across the top, the
// map, the palette band under it, the sidebar (a band under the palette on an
// upright phone) and the footer strip.

import { laneStyle, segmentDist, textOn } from "./board.mjs";
import { BUTTON_STEP, Camera, WHEEL_STEP } from "./camera.mjs";

const SVG = "http://www.w3.org/2000/svg";
// config.*, in layout units
const TAP_MIN = 22, LANE_PICK = 10, DRAG_THRESHOLD = 8, RING_PAD = 6;
const FIT_PAD = 50;                  // config.map_fit_padding()
const SELECT = "rgb(250,240,150)";   // config.COLOR_SELECT
const TEXT_DIM = "rgb(140,148,165)";
const WARN = "rgb(220,150,90)";      // mapmaker._WARN
const LANE_ARM = "rgb(150,200,240)"; // mapmaker._LANE_ARM
const FONT_SMALL = 14;
const R_MIN = 12, R_MAX = 26, PROD_LO = 2, PROD_HI = 5;   // config.NODE_*_RADIUS, PRODUCTION_WEIGHTS' span

/** config.node_radius: richer (lower production) is bigger. */
function radius(production) {
  const t = Math.max(0, Math.min(1, (production - PROD_LO) / (PROD_HI - PROD_LO)));
  return Math.round(R_MAX + t * (R_MIN - R_MAX));
}

function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
  }
  return node;
}

function sv(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

const rgb = ([r, g, b]) => `rgb(${r},${g},${b})`;
/** widgets.brighten(color, 40). */
const brighten = (c) => c.map((v) => Math.min(255, v + 40));

/** A swatch: a system's disc with its glyph, as the palette and owner rows draw it. */
function disc(r, color, glyph, on) {
  const size = 2 * (R_MAX + RING_PAD + 2);   // every swatch the same box, so the discs line up
  const svg = sv("svg", { width: size, height: size, viewBox: `${-size / 2} ${-size / 2} ${size} ${size}`,
    "aria-hidden": "true" });
  sv("circle", { r, fill: rgb(color), stroke: rgb(brighten(color)), "stroke-width": 2 }, svg);
  const t = sv("text", { "text-anchor": "middle", "dominant-baseline": "central", fill: rgb(textOn(color)),
    "font-size": FONT_SMALL }, svg);
  t.textContent = glyph;
  if (on) sv("circle", { r: r + RING_PAD, fill: "none", stroke: SELECT, "stroke-width": 2 }, svg);
  return svg;
}

export class MapMaker {
  /** `root` the scene's element; `core` the session; `hooks.play(game)` when Play
   * starts a match, `hooks.back(menuReply)` when Menu goes back to the setup. */
  constructor(root, core, hooks) {
    this.root = root;
    this.core = core;
    this.hooks = hooks;
    this.chain = Promise.resolve();
    this.view = null;
    this.press = null;
    this.fingers = new Map();
    this.pinch = null;
    this.camera = null;
    this.build();
    addEventListener("keydown", (e) => this.key(e));
  }

  get shown() {
    return !this.root.hidden;
  }

  build() {
    this.toolsEl = h("div", { class: "tools" });
    this.statusEl = h("p", { class: "mkstatus", "aria-live": "polite" });
    this.svg = sv("svg", { class: "mkmap", role: "img", "aria-label": "The map being drawn" });
    const zoom = (factor) => () => {
      const [, , w, hgt] = this.camera.screen;
      this.camera.zoomAt(w / 2, hgt / 2, factor);
      this.draw();
    };
    this.cameraEl = h("div", { class: "mkcamera" },
      h("button", { type: "button", class: "btn blue", onclick: () => this.resetView() }, "Reset",
        h("span", { class: "key" }, " (R)")),
      h("button", { type: "button", class: "btn blue glyph", "aria-label": "Zoom out", onclick: zoom(1 / BUTTON_STEP) },
        "−"),
      h("button", { type: "button", class: "btn blue glyph", "aria-label": "Zoom in", onclick: zoom(BUTTON_STEP) }, "+"));
    this.mapEl = h("section", { class: "mkmapbox" }, this.svg, this.cameraEl);
    this.paletteEl = h("div", { class: "mkpalette" });
    this.sideEl = h("aside", { class: "mkside" });
    this.footerEl = h("nav", { class: "mkfooter" });
    this.modalEl = h("div", { class: "mkmodal", hidden: true });
    this.root.replaceChildren(h("header", { class: "mktop" }, this.toolsEl, this.statusEl),
      this.mapEl, this.paletteEl, this.sideEl, this.footerEl, this.modalEl);
    this.svg.addEventListener("pointerdown", (e) => this.down(e));
    this.svg.addEventListener("pointermove", (e) => this.move(e));
    this.svg.addEventListener("pointerup", (e) => this.up(e));
    this.svg.addEventListener("pointercancel", (e) => this.up(e, true));
    this.svg.addEventListener("contextmenu", (e) => e.preventDefault());
    this.svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [x, y] = this.local(e);
      this.camera.zoomAt(x, y, WHEEL_STEP ** (e.deltaY < 0 ? 1 : -1));
      this.draw();
    }, { passive: false });
    new ResizeObserver(() => {
      if (!this.camera) return;
      this.camera.resize(this.screen());
      this.draw();
    }).observe(this.svg);
  }

  /** Run `op` after every command sent before it; a reply draws the editor,
   * or hands over to the match (Play) or the menu (Menu). */
  send(op, args = {}) {
    const run = this.chain.then(() => this.core.call(op, args));
    this.chain = run.catch(() => {});
    return run.then((res) => {
      this.landed(res);
      return res;
    }, (err) => {
      this.say({ text: `${op} failed: ${err.message}`, ok: false, ms: 15000 });
      console.error(err.trace || err);
      return null;
    });
  }

  landed(res) {
    if (!res) return;
    if (res.game) {
      this.hide();
      this.hooks.play(res.game);
    } else if (res.back) {
      this.hide();
      this.hooks.back(res.back);
    } else if (res.maker) {
      this.show(res.maker);
      if (res.action === "reset_view") this.resetView();
    }
  }

  /** Open on `view` (the menu's Create map, or a parity recipe). */
  open(view) {
    this.root.hidden = false;
    this.camera = null;
    this.press = null;
    this.show(view);
  }

  hide() {
    this.root.hidden = true;
    this.press = null;
  }

  screen() {
    return [0, 0, Math.max(1, this.svg.clientWidth), Math.max(1, this.svg.clientHeight)];
  }

  resetView() {
    this.camera.reset();
    this.draw();
  }

  show(view) {
    this.view = view;
    if (!this.camera) {
      const cam = new Camera(view.world, this.screen(), FIT_PAD, null);
      this.camera = cam;
    }
    if (view.fit) this.camera.fitTo(view.fit);
    this.drawTools();
    this.drawPalette();
    this.drawSide();
    this.drawFooter();
    this.drawModal();
    if (view.status) this.say(view.status);
    this.draw();
  }

  say(status) {
    this.statusEl.textContent = status.text;
    this.statusEl.className = `mkstatus ${status.ok ? "ok" : "err"}`;
    clearTimeout(this.statusTimer);
    this.statusTimer = setTimeout(() => { this.statusEl.textContent = ""; }, status.ms || 4000);
  }

  // --- chrome ------------------------------------------------------------ //
  control(key) {
    return this.send("maker.control", { key });
  }

  button(key, label, palette, extra = {}) {
    return h("button", { type: "button", class: `btn ${palette}`, onclick: () => this.control(key), ...extra }, label);
  }

  drawTools() {
    const v = this.view;
    this.toolsEl.replaceChildren(
      ...v.tools.map(([key, label, ready]) => h("button", { type: "button",
        class: `btn ${key === v.tool ? "active" : "blue"}`, disabled: !ready,
        onclick: () => this.control(`tool_${key}`) }, label)),
      h("button", { type: "button", class: "btn green menu", onclick: () => this.control("back") }, v.menu,
        h("span", { class: "key" }, " (Esc)")));
  }

  drawPalette() {
    const p = this.view.palette;
    this.paletteEl.classList.toggle("hinting", p.kind === "hint");
    if (p.kind === "hint") {
      this.paletteEl.replaceChildren(h("div", { class: "hint" }, ...p.lines.map((l) => h("p", {}, l))));
      return;
    }
    this.paletteEl.replaceChildren(...p.entries.map((e) => h("button", { type: "button", class: "swatch",
      "aria-pressed": String(e.on), onclick: () => this.control(e.key) },
    disc(radius(e.production), this.view.colors[e.owner], e.glyph, e.on), h("span", { class: "caption" }, e.caption))));
  }

  drawSide() {
    const v = this.view;
    const blocks = v.side.map((b) => this.block(b));
    const pr = v.problems;
    const problems = h("div", { class: "problems" },
      h("p", { class: pr.ok ? "dim" : "warn" }, pr.heading),
      ...pr.rows.map(([text, blocks], i) => h("button", { type: "button", class: `problem ${blocks ? "bad" : "warn"}`,
        onclick: () => this.send("maker.problem", { index: i }) }, text)));
    const scroll = this.sideEl.scrollTop;
    this.sideEl.replaceChildren(...blocks, problems);
    this.sideEl.scrollTop = scroll;
  }

  block(b) {
    switch (b.kind) {
      case "title": return h("h2", {}, b.text);
      case "heading": return h("p", { class: "dim heading" }, b.text);
      case "text": return h("p", { class: b.role === "warn" ? "warn" : "dim" }, b.text);
      case "stat": return h("p", { class: "row" }, h("span", { class: "dim" }, b.label), h("span", {}, b.value));
      case "stepper":
        return h("div", { class: "row stepper" }, h("span", { class: "dim" }, b.label),
          h("span", { class: "value" }, b.value),
          h("button", { type: "button", class: "step", "aria-label": `${b.label} down`,
            onclick: () => this.control(`${b.key}_minus`) }, "−"),
          h("button", { type: "button", class: "step", "aria-label": `${b.label} up`,
            onclick: () => this.control(`${b.key}_plus`) }, "+"));
      case "slider": return this.slider(b);
      case "check":
        return h("label", { class: "check" }, h("input", { type: "checkbox", checked: b.on,
          onchange: () => this.control(b.key) }), b.label);
      case "owners":
        return h("div", { class: "owners" }, h("p", { class: "row" }, h("span", { class: "dim" }, "Owner"),
          h("span", {}, b.name)),
        h("div", { class: "swatches" }, ...b.entries.map((e) => h("button", { type: "button",
          class: `own${e.on ? " on" : ""}`, style: `background:${rgb(this.view.colors[e.owner])};`
            + `color:${rgb(textOn(this.view.colors[e.owner]))}`,
          onclick: () => this.control(e.key) }, e.glyph))));
      case "buttons":
        return h("div", { class: "buttons" }, ...b.items.map(([key, label, palette]) => this.button(key, label, palette)));
      default: return h("p", {}, "");
    }
  }

  slider(b) {
    const input = h("input", { type: "range", min: 0, max: 1000, step: 1, value: Math.round(b.t * 1000),
      "aria-label": b.label || "Garrison" });
    const t = () => Number(input.value) / 1000;
    let phase = "start";
    input.addEventListener("input", () => {
      this.send("maker.slide", { key: b.key, t: t(), phase });
      phase = "move";
    });
    input.addEventListener("change", () => {
      this.send("maker.slide", { key: b.key, t: t(), phase: phase === "start" ? "start" : "end" });
      phase = "start";
    });
    if (!b.label) return h("div", { class: "slider bare" }, input);
    return h("div", { class: "slider" }, h("p", { class: "row" }, h("span", { class: "dim" }, b.label),
      h("span", {}, b.value)), input);
  }

  drawFooter() {
    const v = this.view;
    const side = (cluster) => v.footer.filter((f) => f[4] === cluster).map(([key, label, palette]) => {
      if (key === "filename") return this.filenameField();
      if (key === "play") return this.button(key, label, palette === "dead" ? "dead" : palette);
      return this.button(key, label, palette);
    });
    this.footerEl.replaceChildren(h("div", { class: "left" }, ...side("left")),
      h("div", { class: "right" }, ...side("right")));
  }

  filenameField() {
    if (!this.nameEl) {
      this.nameEl = h("input", { type: "text", class: "filename", maxlength: 24, spellcheck: "false",
        autocomplete: "off", "aria-label": "Map file name" });
      this.nameEl.addEventListener("input", () => {
        const clean = this.nameEl.value.replace(/[^A-Za-z0-9_.-]/g, "").slice(0, 24);
        if (clean !== this.nameEl.value) this.nameEl.value = clean;
        this.send("maker.filename", { text: clean });
      });
      this.nameEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === "Escape") this.nameEl.blur();
      });
    }
    // The field keeps its own text while typed into, so a late reply never
    // takes a keystroke back.
    if (document.activeElement !== this.nameEl) this.nameEl.value = this.view.filename;
    this.nameEl.placeholder = this.view.filename_shown;
    return this.nameEl;
  }

  drawModal() {
    const c = this.view.confirm;
    if (!c) {
      this.modalEl.hidden = true;
      this.modalEl.replaceChildren();
      return;
    }
    this.modalEl.replaceChildren(h("div", { class: "stack" }, h("h1", {}, c.title), h("p", {}, c.detail),
      h("div", { class: "grid" },
        h("button", { type: "button", class: "btn danger", onclick: () => this.send("maker.confirm", { yes: true }) },
          c.yes, h("span", { class: "key" }, " (Y)")),
        h("button", { type: "button", class: "btn blue", onclick: () => this.send("maker.confirm", { yes: false }) },
          c.no, h("span", { class: "key" }, " (N)")))));
    this.modalEl.hidden = false;
  }

  // --- the map ----------------------------------------------------------- //
  /** A pointer event's position on the map, in layout units. */
  local(event) {
    const r = this.svg.getBoundingClientRect();
    const k = r.width / (this.svg.clientWidth || r.width);
    return [(event.clientX - r.left) / k, (event.clientY - r.top) / k];
  }

  pos(i) {
    const n = this.view.nodes[i];
    return this.camera.toScreen(n[0], n[1]);
  }

  /** The system under (x, y), nearest first, with the tap floor (`mapmaker._pick_node`). */
  pick(x, y) {
    let best = null, bestD = Infinity;
    this.view.nodes.forEach((n, i) => {
      const [sx, sy] = this.pos(i);
      const d = Math.hypot(x - sx, y - sy);
      if (d <= Math.max(radius(n[2]), TAP_MIN) && d < bestD) {
        best = i;
        bestD = d;
      }
    });
    return best;
  }

  /** The lanes within reach of (x, y), nearest first (`mapmaker._lanes_near`). */
  lanesNear(x, y) {
    const hits = [];
    this.view.lanes.forEach(([a, b], i) => {
      const d = segmentDist(x, y, ...this.pos(a), ...this.pos(b));
      if (d <= LANE_PICK) hits.push([d, i]);
    });
    return hits.sort((p, q) => p[0] - q[0]).map(([, i]) => i);
  }

  async down(e) {
    if (this.view.confirm) return;
    this.svg.setPointerCapture?.(e.pointerId);
    const [x, y] = this.local(e);
    if (e.pointerType === "touch") {
      this.fingers.set(e.pointerId, [x, y]);
      if (this.fingers.size === 2) {
        // A pinch: the first finger's press already reached the session, so
        // put back what it changed, and the fingers only move the camera.
        if (!this.pinch && this.press) this.send("maker.unpress");
        this.press = null;
        this.pinch = this.span();
        return;
      }
      if (this.pinch || this.fingers.size > 2) return;
    }
    if (e.button === 2) {
      this.press = { kind: "pan", last: [x, y] };
      return;
    }
    if (e.button !== 0) return;
    const node = this.pick(x, y);
    const tool = this.view.tool;
    const press = { kind: null, start: [x, y], at: [x, y], moved: false, node };
    this.press = press;
    if (tool === "systems") {
      press.kind = "move";
      if (node !== null) {
        this.send("maker.node", { node, shift: e.shiftKey });
      } else {
        const [wx, wy] = this.camera.toWorld(x, y);
        const res = await this.send("maker.place", { x: wx, y: wy, shift: e.shiftKey });
        press.node = res?.armed ?? null;
      }
    } else if (tool === "lanes") {
      if (node !== null) {
        press.kind = "lane";
        const res = await this.send("maker.node", { node });
        if (res?.maker?.lane_src !== node && this.press === press) press.kind = null;
      } else {
        const res = await this.send("maker.miss", { lanes: this.lanesNear(x, y) });
        if (res?.pan && this.press === press) {
          press.kind = "pan";
          press.last = press.at;
        }
      }
    } else if (node !== null) {
      this.send("maker.node", { node });
    } else {
      press.kind = "box";
      this.send("maker.miss");
    }
  }

  span() {
    const [[x0, y0], [x1, y1]] = this.fingers.values();
    return { spread: Math.max(1, Math.hypot(x1 - x0, y1 - y0)), mid: [(x0 + x1) / 2, (y0 + y1) / 2] };
  }

  move(e) {
    const [x, y] = this.local(e);
    if (this.fingers.has(e.pointerId)) {
      this.fingers.set(e.pointerId, [x, y]);
      if (this.pinch) {
        if (this.fingers.size === 2) {
          const { spread, mid } = this.span();
          this.camera.pan(mid[0] - this.pinch.mid[0], mid[1] - this.pinch.mid[1]);
          this.camera.zoomAt(mid[0], mid[1], spread / this.pinch.spread);
          this.pinch = { spread, mid };
          this.draw();
        }
        return;
      }
    }
    const p = this.press;
    if (!p) return;
    if (p.kind === "pan") {
      this.camera.pan(x - p.last[0], y - p.last[1]);
      p.last = [x, y];
      this.draw();
      return;
    }
    p.at = [x, y];
    if (!p.moved && Math.hypot(x - p.start[0], y - p.start[1]) > DRAG_THRESHOLD) p.moved = true;
    if (!p.moved) return;
    if (p.kind === "move" && p.node !== null) this.dragTo(x, y);
    else if (p.kind === "lane" || p.kind === "box") this.draw();
  }

  /** The armed system follows the pointer: one drag in flight at a time, the
   * latest position sent once it lands. */
  dragTo(x, y) {
    this.dragAt = this.camera.toWorld(x, y);
    if (this.dragging) return;
    this.dragging = true;
    const step = () => {
      const at = this.dragAt;
      this.dragAt = null;
      this.send("maker.drag", { x: at[0], y: at[1] }).then(() => {
        if (this.dragAt) step();
        else this.dragging = false;
      });
    };
    step();
  }

  up(e, cancelled = false) {
    if (this.fingers.delete(e.pointerId) && this.pinch) {
      if (!this.fingers.size) this.pinch = null;
      return;
    }
    const p = this.press;
    this.press = null;
    if (!p || cancelled) {
      if (p?.kind === "move") this.send("maker.release");
      this.draw();
      return;
    }
    const [x, y] = this.local(e);
    if (p.kind === "move") {
      if (p.moved && p.node !== null) this.dragTo(x, y);
      this.send("maker.release");
    } else if (p.kind === "lane" && p.moved) {
      this.send("maker.lane", { target: this.pick(x, y) });
    } else if (p.kind === "box" && p.moved) {
      this.send("maker.box", { nodes: this.inBox(p.start, [x, y]) });
    }
    this.draw();
  }

  /** The systems inside a dragged box, clipped to the map (`mapmaker._nodes_in_box`). */
  inBox([x0, y0], [x1, y1]) {
    const [, , w, hgt] = this.camera.screen;
    const bx0 = Math.max(0, Math.min(x0, x1)), by0 = Math.max(0, Math.min(y0, y1));
    const bx1 = Math.min(w, Math.max(x0, x1)), by1 = Math.min(hgt, Math.max(y0, y1));
    const out = [];
    this.view.nodes.forEach((n, i) => {
      const [sx, sy] = this.pos(i);
      if (sx >= bx0 && sx <= bx1 && sy >= by0 && sy <= by1) out.push(i);
    });
    return out;
  }

  /** The map: lanes styled by travel time, systems with their garrisons and
   * ids, the rings (amber for an illegal spot, blue for an armed lane source,
   * the selection), and a lane drag's rubber band or the Owners box. */
  draw() {
    if (!this.view || !this.camera || this.root.hidden) return;
    const v = this.view;
    const svg = this.svg;
    svg.replaceChildren();
    const bad = new Set(v.bad_lanes);
    v.lanes.forEach(([a, b, turns], i) => {
      let [width, color] = laneStyle(turns);
      color = rgb(color);
      if (bad.has(i)) [width, color] = [width + 1, WARN];
      else if (i === v.sel_lane) [width, color] = [width + 2, SELECT];
      const [ax, ay] = this.pos(a), [bx, by] = this.pos(b);
      sv("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: color, "stroke-width": width, "stroke-linecap": "round" },
        svg);
    });
    const badNodes = new Set(v.bad_nodes);
    v.nodes.forEach((n, i) => {
      const [x, y] = this.pos(i);
      const r = radius(n[2]);
      const color = v.colors[n[4]];
      sv("circle", { cx: x, cy: y, r, fill: rgb(color), stroke: rgb(brighten(color)), "stroke-width": 2 }, svg);
      const ships = sv("text", { x, y, "text-anchor": "middle", "dominant-baseline": "central",
        fill: rgb(textOn(color)), "font-size": FONT_SMALL }, svg);
      ships.textContent = String(n[3]);
      const ring = badNodes.has(i) ? WARN : i === v.lane_src ? LANE_ARM : i === v.sel_node ? SELECT : null;
      if (ring) sv("circle", { cx: x, cy: y, r: r + RING_PAD, fill: "none", stroke: ring, "stroke-width": 2 }, svg);
      const id = sv("text", { x, y: y + r + 12, "text-anchor": "middle", "dominant-baseline": "central",
        fill: TEXT_DIM, "font-size": FONT_SMALL }, svg);
      id.textContent = `#${i}`;
    });
    const p = this.press;
    if (p?.moved && p.kind === "lane" && p.node !== null) {
      const [ax, ay] = this.pos(p.node);
      const [bx, by] = p.at;
      sv("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: LANE_ARM, "stroke-width": 3 }, svg);
      sv("circle", { cx: bx, cy: by, r: 5, fill: "none", stroke: LANE_ARM, "stroke-width": 2 }, svg);
      const target = this.pick(bx, by);
      if (target !== null && target !== p.node) {
        const [tx, ty] = this.pos(target);
        sv("circle", { cx: tx, cy: ty, r: radius(v.nodes[target][2]) + 4, fill: "none", stroke: SELECT, "stroke-width": 2 },
          svg);
      }
    } else if (p?.moved && p.kind === "box") {
      const [x0, y0] = p.start, [x1, y1] = p.at;
      sv("rect", { x: Math.min(x0, x1), y: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0),
        fill: "none", stroke: SELECT, "stroke-width": 1 }, svg);
    }
  }

  // --- keys -------------------------------------------------------------- //
  key(e) {
    if (!this.shown || !this.view || e.altKey || e.metaKey) return;
    if (e.target instanceof HTMLInputElement && e.target.type === "text") return;
    const ctrl = e.ctrlKey;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (this.view.confirm) {
      if (["y", "n", "Enter", "Escape"].includes(k)) {
        e.preventDefault();
        this.send("maker.key", { name: k });
      }
      return;
    }
    if (!ctrl && k === "r") {
      this.resetView();
      return;
    }
    if ((ctrl && ["z", "y", "r"].includes(k)) || ["Escape", "Delete", "Backspace", "Enter"].includes(k)) {
      if ((k === "Enter" || k === " ") && e.target instanceof HTMLButtonElement) return;   // its own press
      e.preventDefault();
      this.send("maker.key", { name: k, ctrl });
    }
  }
}
