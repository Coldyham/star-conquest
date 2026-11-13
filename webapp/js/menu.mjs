// The setup menu: drawn from the session's `menu` (`starconquest.setupmenu.view`)
// as it stands, every press sent back as the control's key. Python owns the
// setup, the rules for each control and every word; what stays here is only
// about looking: where a slider's finger is along its track, the seed field's
// caret, the status line's clock, and when to ask for the lanes to be measured.

import { pickFile } from "./files.mjs";
import { drawGraph } from "./stagegraph.mjs";

const SURVEY_MS = 250;   // the setup has settled once nothing moved for this long

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

const SVG = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

/** A five-pip die (`menu._draw_die`): "roll it". */
function die() {
  const svg = sv("svg", { viewBox: "0 0 20 20", width: 20, height: 20, class: "die", "aria-hidden": "true" });
  sv("rect", { width: 20, height: 20, rx: 4, fill: "currentColor" }, svg);
  for (const [x, y] of [[5, 5], [15, 5], [10, 10], [5, 15], [15, 15]]) {
    sv("circle", { cx: x, cy: y, r: 2, class: "pip" }, svg);
  }
  return svg;
}

/** The list carets (`menu._draw_caret`). */
function caret(up) {
  const svg = sv("svg", { viewBox: "0 0 10 8", width: 10, height: 8, class: "caret", "aria-hidden": "true" });
  sv("polygon", { points: up ? "0,7 10,7 5,0" : "0,0 10,0 5,8" }, svg);
  return svg;
}

function dot(changed) {
  return changed ? h("span", { class: "dot", title: "changed from the default" }) : null;
}

export class SetupMenu {
  /** `root` holds the menu; `core` is the session; `hooks` carries out what a
   * reply's `action` asks of the page (`start`, `quit`) and plays a resumed
   * match (`resumed`). */
  constructor(root, core, hooks) {
    this.root = root;
    this.core = core;
    this.hooks = hooks;
    this.last = null;
    this.drag = null;        // a slider's drag: {key, pending, t}
    this.surveyTimer = null;
    this.statusTimer = null;
    this.chain = Promise.resolve();
    this.build();
    addEventListener("pointermove", (e) => this.dragMove(e));
    addEventListener("pointerup", (e) => this.dragEnd(e));
    addEventListener("pointercancel", (e) => this.dragEnd(e));
    document.addEventListener("keydown", (e) => this.key(e));
    // A button pressed by pointer lets go of the focus, so Enter still starts
    // the game as it does in pygame; one reached by Tab keeps it, and its key.
    root.addEventListener("click", (e) => {
      const b = e.target.closest?.("button");
      if (b && e.detail > 0) b.blur();
    });
  }

  get shown() {
    return !this.root.hidden;
  }

  build() {
    this.titleEl = h("h1", { class: "title" });
    this.subEl = h("div", { class: "subtitle" });
    this.tabsEl = h("div", { class: "tabs", role: "tablist" });
    this.panelEl = h("div", { class: "panel" });
    this.sketchEl = h("figure", { class: "sketch" });
    this.statusEl = h("p", { class: "mstatus", "aria-live": "polite" });
    this.actionsEl = h("div", { class: "actions" });
    this.hintEl = h("p", { class: "hint" });
    this.modalEl = h("div", { class: "mveil", hidden: true });
    this.root.replaceChildren(
      h("header", { class: "head" }, this.titleEl, this.subEl),
      h("div", { class: "main" }, this.tabsEl, this.panelEl),
      h("div", { class: "side" }, this.sketchEl, this.statusEl),
      this.actionsEl, this.hintEl, this.modalEl);
  }

  /** Run `op` after every command sent before it; a reply draws the menu. */
  send(op, args = {}) {
    const run = this.chain.then(() => this.core.call(op, args));
    this.chain = run.catch(() => {});
    return run.then((res) => {
      if (res.menu) this.draw(res);
      return res;
    }, (err) => {
      this.say({ text: `${op} failed: ${err.message}`, ok: false, ms: 15000 });
      console.error(err.trace || err);
      return null;
    });
  }

  /** Open the menu; `first` at boot, which may offer to resume a match. */
  async open(args = {}) {
    this.root.hidden = false;
    const res = await this.send("menu.open", args);
    this.after(res);
    return res;
  }

  hide() {
    this.root.hidden = true;
    clearTimeout(this.surveyTimer);
  }

  async press(key) {
    if (key === "load_file") {   // the picker opens on the press itself
      pickFile(async (name, text) => this.after(await this.send("file.open", { name, text })));
    }
    const res = await this.send("menu.press", { key });
    this.after(res);
  }

  /** Carry out what a reply asks of the page: a match to start or one the
   * menu opened itself (a run's stage, a resumed match), quitting, or the next
   * slice of a gauntlet being generated (`menu.generate`). */
  after(res) {
    if (!res) return;
    if (res.game) this.hooks.resumed(res.game);
    else if (res.maker) this.hooks.maker(res.maker);
    else if (res.action === "start") this.hooks.start();
    else if (res.action === "quit") this.hooks.quit();
    if (res.generating && !this.generating) {
      this.generating = true;
      setTimeout(async () => {
        const next = await this.send("menu.generate");
        this.generating = false;
        this.after(next);
      }, 0);
    }
  }

  draw(res) {
    this.last = res;
    const m = res.menu;
    this.titleEl.textContent = m.title;
    this.subEl.replaceChildren(...m.subtitle.map((l) => h("p", { style: `color:${l.color}` }, l.text)));
    this.tabsEl.replaceChildren(...m.tabs.map((t) => h("button", {
      type: "button", class: `mbtn tab${t.active ? " on" : ""}`, role: "tab", "aria-selected": String(t.active),
      onclick: () => this.press(t.key),
    }, t.label, t.changed ? h("span", { class: "dot corner" }) : null)));
    this.drawPanel(m);
    this.drawSketch(m.sketch);
    this.drawActions(m);
    this.hintEl.textContent = m.hint || "";
    this.hintEl.hidden = !m.hint;
    this.drawModal(m.modal, res.resume, m.pbp_prompt, m.tutorial);
    if (res.status) this.say(res.status);
    this.root.classList.toggle("dragging", Boolean(m.dragging));
    clearTimeout(this.surveyTimer);
    if (res.stale && !m.dragging) {
      this.surveyTimer = setTimeout(() => this.send("menu.survey"), SURVEY_MS);
    }
  }

  say(status) {
    this.statusEl.textContent = status.text;
    this.statusEl.className = `mstatus ${status.ok ? "ok" : "err"}`;
    this.root.classList.add("saying");
    clearTimeout(this.statusTimer);
    this.statusTimer = setTimeout(() => {
      this.statusEl.textContent = "";
      this.root.classList.remove("saying");
    }, status.ms || 4000);
  }

  // --- the settings panel ------------------------------------------------ //
  drawPanel(m) {
    const seed = this.panelEl.querySelector(":scope > :not(.off) input.seed");
    const keepSeed = seed && document.activeElement === seed ? seed : null;
    const scroll = this.panelEl.scrollTop;
    // A phone gets every tab, the others laid under the one shown, so the panel
    // is as tall as the tallest and never moves as the tabs change.
    const panes = m.panels
      ? Object.entries(m.panels).map(([tab, items]) => h("div", {
        class: `tab-${tab}${tab === m.tab ? "" : " off"}`, "aria-hidden": tab === m.tab ? null : "true",
        inert: tab !== m.tab,
      }, ...items.map((item) => this.item(item))))
      : [h("div", { class: `tab-${m.tab}` }, ...m.panel.map((item) => this.item(item)))];
    this.panelEl.replaceChildren(...panes);
    if (this.lastTab === m.tab) this.panelEl.scrollTop = scroll;
    this.lastTab = m.tab;
    if (keepSeed) {
      // The field being typed into keeps its caret: only its text follows the reply.
      const fresh = this.panelEl.querySelector(":scope > :not(.off) input.seed");
      if (fresh) {
        if (keepSeed.value !== fresh.value) keepSeed.value = fresh.value;
        fresh.replaceWith(keepSeed);
      }
    }
  }

  item(it) {
    switch (it.kind) {
      case "row":
        return h("div", { class: "row" }, h("span", { class: "label" }, dot(it.changed), it.label),
          this.control(it.control));
      case "checks":
        return h("div", { class: "checks" },
          h("div", { class: "col" }, ...it.left.map((c) => this.checkRow(c))),
          h("div", { class: "col" }, ...it.right.map((c) => this.checkRow(c))));
      case "columns":
        return h("div", { class: "columns" },
          h("div", { class: "col" }, ...it.left.map((x) => this.item(x))),
          h("div", { class: "col" }, ...it.right.map((x) => this.item(x))));
      case "section":
        return h("h3", { class: "section" }, h("span", { class: "long" }, it.text),
          it.short ? h("span", { class: "short" }, it.short) : null);
      case "slider":
        return this.slider(it);
      case "note":
        return h("p", { class: "note" }, it.text);
      case "hidden":
        return h("div", { class: "hidden-note" }, ...it.lines.map((l) => h("p", {}, l)));
      case "die":
        return h("div", { class: "small-row" }, h("span", { class: "small-label" }, it.label),
          h("button", { type: "button", class: "mbtn square", "aria-label": it.label, onclick: () => this.press(it.key) },
            die()));
      case "check":
        return h("div", { class: "small-row" }, h("span", { class: "small-label" }, dot(it.changed), it.label),
          this.checkbox(it));
      case "prose":
        return h("div", { class: "prose" }, ...it.lines.map((l) => h("p", {}, l)));
      case "lanecheck":
        return h("div", { class: "lanecheck" },
          h("span", { class: "small-label" }, dot(it.changed), it.label),
          h("span", { class: "caveat" }, it.note), this.checkbox(it));
      case "readout":
        return h("div", { class: "readout" }, h("p", { class: "headline", style: `color:${it.color}` }, it.headline),
          h("p", { class: "detail" }, it.detail), h("p", { class: "band" }, it.band));
      case "matrix":
        return h("table", { class: "matrix" },
          h("thead", {}, h("tr", {}, h("th"), ...it.cols.map((c) => h("th", {}, c)))),
          h("tbody", {}, ...it.rows.map((r) => h("tr", {}, h("th", {}, r.label),
            ...r.cells.map((c) => h("td", { class: c.centre ? "centre" : "", style: `color:${c.color}` }, c.text))))));
      case "seats":
        return h("div", { class: "seats" }, h("span", { class: "small-label" }, it.label),
          ...it.seats.map((s) => h("button", {
            type: "button", class: `chip${s.selected ? " on" : ""}`,
            style: `--seat:${s.color};color:${s.text}`, onclick: () => this.press(s.key),
          }, String(s.seat))));
      case "aibuttons":
        return h("div", { class: "aibuttons" },
          ...it.buttons.map((b) => h("button", { type: "button", class: "mbtn", onclick: () => this.press(b.key) }, b.label)),
          h("button", { type: "button", class: "mbtn square", "aria-label": "Randomise", onclick: () => this.press(it.die) },
            die()),
          h("span", { class: "editing", style: `color:${it.color}` }, it.editing));
      case "dropdown":
        return this.dropdown(it);
      default:
        return h("p", { class: "note" }, `(${it.kind})`);
    }
  }

  control(c) {
    switch (c.kind) {
      case "stepper":
        return h("span", { class: `stepper${c.wide ? " wide" : ""}` },
          h("button", { type: "button", class: "mbtn square", "aria-label": "less", onclick: () => this.press(`${c.key}_dec`) }, "−"),
          h("span", { class: "value" }, c.value),
          h("button", { type: "button", class: "mbtn square", "aria-label": "more", onclick: () => this.press(`${c.key}_inc`) }, "+"));
      case "segmented":
        return h("span", { class: "segmented" }, ...c.options.map((o) => h("button", {
          type: "button", class: `mbtn seg${o.active ? " on" : ""}`, onclick: () => this.press(o.key),
        }, o.label)));
      case "derived":
        return h("span", { class: "derived" }, c.text);
      case "seed":
        return h("span", { class: "seedbox" }, this.seedField(c),
          h("button", { type: "button", class: "mbtn square", "aria-label": "Roll a seed", onclick: () => this.press("seed_random") },
            die()));
      default:
        return h("span");
    }
  }

  seedField(c) {
    const input = h("input", {
      class: "seed", type: "text", inputmode: "numeric", autocomplete: "off", spellcheck: "false",
      placeholder: c.placeholder, maxlength: String(c.max), value: c.text, "aria-label": "Seed",
    });
    input.addEventListener("input", () => this.send("menu.seed", { text: input.value }));
    input.addEventListener("blur", () => this.send("menu.seed", { text: input.value, done: true }));
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        input.blur();
        this.press("start");
      } else if (e.key === "Escape") {
        input.blur();
      }
    });
    return input;
  }

  checkRow(c) {
    return h("div", { class: "row" }, h("span", { class: "label" }, dot(c.changed), c.label), this.checkbox(c));
  }

  checkbox(c) {
    return h("button", {
      type: "button", class: `check${c.on ? " on" : ""}`, role: "checkbox", "aria-checked": String(c.on),
      "aria-label": c.label, onclick: () => this.press(c.key),
    }, h("span", { class: "tick" }));
  }

  slider(it) {
    const track = h("div", { class: "track" }, h("div", { class: "fill", style: `width:${it.t * 100}%` }),
      h("div", { class: "knob", style: `left:${it.t * 100}%` }));
    const node = h("div", { class: "slider", "data-key": it.key },
      h("div", { class: "top" }, h("span", { class: "small-label" }, dot(it.changed), it.label),
        h("span", { class: "svalue" }, it.text)),
      h("div", { class: "hit" }, track));
    node.querySelector(".hit").addEventListener("pointerdown", (e) => this.dragStart(e, it.key));
    return node;
  }

  dropdown(it) {
    const list = it.open ? h("ul", { class: "options", role: "listbox" }, ...it.options.map((o) => h("li", {
      role: "option", "aria-selected": String(o.current), class: o.current ? "current" : "",
      onclick: () => this.press(o.key),
    }, o.label))) : null;
    return h("div", { class: `dropdown${it.open ? " open" : ""}` },
      h("span", { class: "small-label" }, dot(it.changed), it.label),
      h("div", { class: "dd" },
        h("button", { type: "button", class: "mbtn trigger", "aria-expanded": String(it.open), onclick: () => this.press(it.key) },
          h("span", {}, it.value), caret(it.open)),
        list));
  }

  // --- a slider's drag: the fraction along its track ----------------------- //
  fraction(key, x) {
    const track = this.panelEl.querySelector(`:scope > :not(.off) .slider[data-key="${key}"] .track`);
    if (!track) return null;
    const r = track.getBoundingClientRect();
    return r.width ? Math.max(0, Math.min(1, (x - r.left) / r.width)) : 0;
  }

  dragStart(e, key) {
    if (e.button !== 0) return;
    e.preventDefault();
    const t = this.fraction(key, e.clientX);
    if (t === null) return;
    this.drag = { key, pending: null, busy: true, startX: e.clientX, startY: e.clientY, moved: false };
    this.send("menu.slide", { key, t, phase: "start" }).then(() => this.dragFlush());
  }

  dragMove(e) {
    if (!this.drag) return;
    this.drag.moved = true;
    this.drag.pending = this.fraction(this.drag.key, e.clientX);
    if (!this.drag.busy) this.dragFlush();
  }

  /** One move at a time: the latest position goes once the last has landed. */
  dragFlush() {
    const d = this.drag;
    if (!d) return;
    if (d.pending === null || d.pending === undefined) {
      d.busy = false;
      return;
    }
    const t = d.pending;
    d.pending = null;
    d.busy = true;
    this.send("menu.slide", { key: d.key, t, phase: "move" }).then(() => this.dragFlush());
  }

  dragEnd(e) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    const t = this.fraction(d.key, e.clientX);
    this.send("menu.slide", { key: d.key, t: t ?? 0, phase: "end" });
  }

  // --- the phone's map sketch ---------------------------------------------- //
  drawSketch(sk) {
    if (!sk) {
      this.sketchEl.replaceChildren();
      this.sketchEl.hidden = true;
      return;
    }
    this.sketchEl.hidden = false;
    const cap = h("figcaption", {}, sk.caption);
    if (sk.kind === "hidden") {
      this.sketchEl.replaceChildren(h("div", { class: "fogged" }, "?"), cap);
      return;
    }
    const pts = Object.values(sk.points);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys);
    const w = Math.max(...xs) - x0 || 1, hgt = Math.max(...ys) - y0 || 1;
    const svg = sv("svg", { viewBox: `${x0} ${y0} ${w} ${hgt}`, preserveAspectRatio: "xMidYMid meet" });
    // Sized in screen pixels whatever the scale (`menu._compact_sketch`): a lane
    // one wide, a home 5 across its radius and every other system half that, each
    // drawn as a round-capped dot of a stroke that does not scale.
    const stroke = { "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" };
    for (const [a, b] of sk.lanes) {
      const [ax, ay] = sk.points[a], [bx, by] = sk.points[b];
      sv("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: sk.lane, "stroke-width": 1, ...stroke }, svg);
    }
    for (const [sid, [x, y]] of Object.entries(sk.points)) {
      const home = sk.homes[sid];
      sv("line", { x1: x, y1: y, x2: x, y2: y, stroke: home || sk.neutral, "stroke-width": home ? 10 : 5, ...stroke }, svg);
    }
    this.sketchEl.replaceChildren(svg, cap);
  }

  // --- the actions along the foot ------------------------------------------ //
  drawActions(m) {
    const a = m.actions;
    const btn = (key, label, tone, extra = {}) => h("button", {
      type: "button", class: `mbtn act ${tone} k-${key}`, onclick: () => this.press(key), ...extra,
    }, label);
    const listBtn = (key, label, tone, list) => h("span", { class: `droplist k-${key}` },
      h("button", { type: "button", class: `mbtn act ${tone}${list.open ? " open" : ""}`, "aria-expanded": String(list.open),
        onclick: () => this.press(key) }, h("span", {}, label), caret(list.open)),
      list.open ? h("ul", { class: "drop", role: "menu" }, ...list.items.map((i) => h("li", { role: "menuitem" },
        h("button", { type: "button", onclick: () => this.press(i.key) }, i.label)))) : null);
    const row1 = h("div", { class: "row1" },
      a.tutorial ? btn("tutorial", a.tutorial, "go") : null,
      a.get_link ? btn("get_link", "Get Link", "plain") : null,
      a.leaderboard ? btn("leaderboard", a.leaderboard, "plain") : null,
      a.daily ? listBtn("daily_menu", a.daily, "go", m.lists.daily) : null,
      a.files ? listBtn("files_menu", a.files, "plain", m.lists.files) : null);
    // Start stays centred: the map creator to its left, the rest to its right.
    const row2 = h("div", { class: "row2" },
      h("span", { class: "lead" },
        a.clear_map ? btn("clear_map", "x", "plain", { "aria-label": "Clear the drawn map" }) : null,
        btn("create_map", a.create, "blue")),
      btn("start", "Start Game", "start"),
      h("span", { class: "trail" },
        a.play_by_post ? btn("play_by_post", "Play by post", "blue") : null,
        btn("quit", "Quit", "plain quit")));
    // A phone's grid: Get Link joins the Files list as "Share or save",
    // keeping it to six.
    const grid = h("div", { class: "grid" },
      a.tutorial ? btn("tutorial", a.tutorial, "go") : null,
      a.daily ? listBtn("daily_menu", a.daily, "go", m.lists.daily) : null,
      a.play_by_post ? btn("play_by_post", "Play by post", "blue") : null,
      listBtn("files_menu", a.share_save, "plain", m.lists.share_save),
      a.leaderboard ? btn("leaderboard", a.leaderboard, "plain") : null,
      h("span", { class: "create" }, btn("create_map", a.create, "blue"),
        a.clear_map ? btn("clear_map", "x", "plain corner", { "aria-label": "Clear the drawn map" }) : null));
    this.actionsEl.replaceChildren(h("div", { class: "wide" }, row1, row2),
      h("div", { class: "narrow" }, btn("start", "Start Game", "start"), grid));
  }

  // --- confirms: the menu's own, and the boot prompt to resume --------------- //
  drawModal(modal, resume, prompt, tutorial) {
    if (tutorial && !modal) {
      this.drawTutorial(tutorial);
      return;
    }
    if (prompt && !modal) {
      this.drawPrompt(prompt);
      return;
    }
    if (modal) {
      this.modalEl.hidden = false;
      this.modalEl.replaceChildren(h("div", { class: "mmodal", role: "dialog", "aria-modal": "true" },
        h("p", { class: "mtitle" }, modal.title),
        ...modal.lines.filter((l) => l.text).map((l) => h("p", { class: "mline", style: `color:${l.color}` }, l.text)),
        h("div", { class: "mbuttons" },
          h("button", { type: "button", class: "mbtn go", onclick: () => this.press(modal.go.key) }, modal.go.label),
          h("button", { type: "button", class: "mbtn stay", onclick: () => this.press(modal.stay.key) }, modal.stay.label))));
      return;
    }
    if (resume) {
      this.modalEl.hidden = false;
      this.modalEl.replaceChildren(h("div", { class: "resume", role: "dialog", "aria-modal": "true" },
        h("p", { class: "rtitle" }, resume.title),
        h("p", { class: "rdetail" }, resume.detail),
        h("div", { class: "mbuttons" },
          h("button", { type: "button", class: "mbtn start", onclick: () => this.resume(true) }, resume.go),
          h("button", { type: "button", class: "mbtn plain", onclick: () => this.resume(false) }, resume.stay))));
      return;
    }
    this.modalEl.hidden = true;
    this.modalEl.replaceChildren();
  }

  /** A modal's link to a page on the board, pressed as its key like any control. */
  link(l) {
    return l ? h("p", { class: "mlink" },
      h("button", { type: "button", class: "linkish", onclick: () => this.press(l.key) }, l.label)) : null;
  }

  /** Play by post's roster prompt (`setupmenu.pbp_prompt_view`): who at the
   * table is a person, the deadline, the lobby, and two optional words, with
   * the board's shared matches linked at its foot. A text field being typed
   * into keeps its caret across the reply. */
  drawPrompt(p) {
    const focused = this.modalEl.querySelector("input.ptext:focus");
    const which = focused?.dataset.which;
    const seats = p.seats.map((s) => h("div", { class: "prow" },
      h("span", { class: "swatch", style: `background:${s.color}` }),
      h("span", { class: "pname", style: `color:${s.color}` }, s.name),
      s.you ? h("span", { class: "you" }, "(you)")
        : this.checkbox({ on: s.checked, key: s.key, label: `${s.name} is a person` })));
    const texts = p.text.map((t) => {
      const input = h("input", { class: "ptext", type: "text", autocomplete: "off", spellcheck: "false",
        maxlength: String(t.limit), placeholder: "optional", value: t.text, "aria-label": t.label, "data-which": t.which });
      input.addEventListener("input", () => this.send("menu.pbp_text", { which: t.which, text: input.value }));
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          input.blur();
          this.press(p.go.key);
        } else if (e.key === "Escape") {
          input.blur();
        }
      });
      input.addEventListener("blur", () => this.send("menu.pbp_text", { which: t.which, text: input.value, done: true }));
      return h("div", { class: "prow" }, h("span", { class: "plabel" }, t.label), input);
    });
    this.modalEl.hidden = false;
    this.modalEl.replaceChildren(h("div", { class: "mmodal pbp", role: "dialog", "aria-modal": "true" },
      h("p", { class: "mtitle" }, p.title),
      ...seats,
      h("div", { class: "prow" }, h("span", { class: "plabel" }, "Deadline"), this.control({
        kind: "stepper", key: "pbp_deadline", value: p.deadline, wide: true })),
      h("div", { class: "prow" }, h("span", { class: "plabel" }, "Publicly joinable"),
        this.checkbox({ on: p.public, key: "pbp_public", label: "Publicly joinable" })),
      ...texts,
      h("div", { class: "mbuttons" },
        h("button", { type: "button", class: "mbtn start", onclick: () => this.press(p.go.key) }, p.go.label),
        h("button", { type: "button", class: "mbtn plain", onclick: () => this.press(p.stay.key) }, p.stay.label)),
      this.link(p.link)));
    if (which) {
      const fresh = this.modalEl.querySelector(`input.ptext[data-which="${which}"]`);
      if (fresh && focused) {
        // Its own text, not the reply's: a reply to an earlier keystroke
        // would take back the ones typed since. The field's text is settled
        // when it lets go of the caret (`menu.pbp_text`'s ``done``).
        fresh.replaceWith(focused);
        focused.focus();
      }
    }
  }

  /** The Tutorial list (`setupmenu.tutorial_view`): the gauntlet's stages as
   * a graph (`stagegraph.mjs`), the selected stage's strip, its runs, Leave,
   * Copy link and Close, and the board's gauntlets linked at its foot. A tap selects a stage and a second tap plays it, both
   * decided in Python; what stays here is the graph box's scroll and focus,
   * kept across the redraw every reply makes. */
  drawTutorial(t) {
    const tone = { warn: "go", hl: "blue", dim: "plain" };
    const old = this.modalEl.querySelector(".tgraph");
    const scroll = old ? [old.scrollLeft, old.scrollTop] : null;
    const focused = document.activeElement?.closest?.(".tgraph") ? document.activeElement.dataset.key : null;
    const box = h("div", { class: "tgraph" });
    const pick = t.pick;
    const strip = pick ? h("div", { class: "tpick" },
      h("p", { class: "tptitle" }, pick.title),
      ...pick.lines.map((l) => h("p", { class: "tpline", style: `color:${l.color}` }, l.text)),
      h("div", { class: "mbuttons" },
        h("button", { type: "button", class: `mbtn ${pick.play.tone || "start"}`, disabled: !pick.play.enabled,
                      onclick: () => this.press(pick.play.key) }, pick.play.label),
        pick.edit ? h("button", { type: "button", class: "mbtn blue",
                                  onclick: () => this.press(pick.edit.key) }, pick.edit.label) : null)) : null;
    const run = t.run ? [h("p", { class: "tnote" }, t.run.text),
      h("div", { class: "mbuttons" }, ...t.run.buttons.map((b) => h("button", {
        type: "button", class: `mbtn ${b.warn ? "go" : "start-edge"}`, onclick: () => this.press(b.key) }, b.label)))] : [];
    this.modalEl.hidden = false;
    this.modalEl.replaceChildren(h("div", { class: "mmodal tutorial", role: "dialog", "aria-modal": "true" },
      h("p", { class: "mtitle" }, t.heading),
      t.picker ? h("div", { class: "prow" }, h("span", { class: "plabel" }, t.picker.name),
        this.control({ kind: "stepper", key: "gauntlet_pick", value: t.picker.at })) : null,
      box,
      strip,
      ...run,
      t.note ? h("p", { class: "tnote" }, t.note) : null,
      t.said ? h("p", { class: `tnote ${t.said.ok ? "ok" : "err"}` }, t.said.text) : null,
      h("div", { class: "mbuttons tbuttons" }, ...t.buttons.map((b) => h("button", {
        type: "button", class: `mbtn ${tone[b.tone]}`, onclick: () => this.press(b.key) }, b.label))),
      this.link(t.link)));
    const graph = drawGraph(box, t.graph, { onPress: (key) => this.press(key), label: "The gauntlet's stages" });
    const focus = t.graph.focus;
    if (scroll) [box.scrollLeft, box.scrollTop] = scroll;
    if (focus) {
      // Opening shows the selected stage in the middle; afterwards only enough
      // to keep it in view, so a scroll by hand isn't undone by the next reply.
      graph.show(focus, scroll ? { block: "nearest", inline: "nearest" } : { block: "nearest", inline: "center" });
      if (focused) graph.node(focus)?.focus({ preventScroll: true });
    }
  }

  async resume(accept) {
    const res = await this.send("menu.resume", { accept });
    this.after(res);
  }

  async key(e) {
    if (!this.shown || e.target instanceof HTMLInputElement || e.ctrlKey || e.metaKey || e.altKey) return;
    if (this.last?.resume) {
      if (["y", "Y", "Enter"].includes(e.key)) this.resume(true);
      else if (["n", "N", "Escape"].includes(e.key)) this.resume(false);
      e.preventDefault();
      return;
    }
    const arrow = e.key.startsWith("Arrow") && Boolean(this.last?.menu?.tutorial);   // the gauntlet list's selection
    if (!arrow && !["Enter", " ", "Escape", "y", "Y", "n", "N"].includes(e.key)) return;
    if ((e.key === " " || e.key === "Enter") && e.target instanceof HTMLButtonElement) return;   // its own press
    e.preventDefault();
    const res = await this.send("menu.key", { name: e.key });
    this.after(res);
  }
}
