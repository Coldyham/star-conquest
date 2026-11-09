// The play screen's DOM: the top bar's scoreboard, the side panel, the send
// popup and the footer strip, each drawn from the session's `view`
// (`serial.view`). The words are Python's (`hud`); this only lays them out,
// picks the longest wording that fits, and turns presses into calls.

const NEUTRAL = [122, 128, 140];
const SVG = "http://www.w3.org/2000/svg";

function sv(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

/** `render._nice_ceil`: `v` up to 1, 2 or 5 times a power of ten, at least 1. */
function niceCeil(v) {
  if (v <= 1) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const step of [1, 2, 5, 10]) if (v <= step * mag) return step * mag;
  return 10 * mag;
}
const FONT = "DejaVu Sans Mono, monospace";

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

const measure = document.createElement("canvas").getContext("2d");

/** Width of `text` in px at `size` in the shell's font. */
function textWidth(text, size = 14) {
  measure.font = `${size}px ${FONT}`;
  return measure.measureText(text).width;
}

/** The first of `forms` (longest first) that fits `width`, else the last. */
export function fit(forms, width, size = 14) {
  return forms.find((f) => textWidth(f, size) <= width) ?? forms[forms.length - 1];
}

/** `render._lane_swatches`: one colour per lane of a split, light to dark teal. */
function laneSwatches(n) {
  if (n <= 1) return ["var(--forward)"];
  const light = [190, 240, 234], dark = [40, 112, 106];
  return Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1);
    const [r, g, b] = light.map((c, k) => Math.round(c + (dark[k] - c) * t));
    return `rgb(${r},${g},${b})`;
  });
}

export class Hud {
  /** `on` holds the page's handlers: `call(method, args)`, `action(name)`,
   * `done()` (the popup's Done, or a press on empty map), `lore()`. */
  constructor(map, legends, on) {
    this.map = map;
    this.legends = legends;
    this.on = on;
    this.colors = new Map(map.players.map((p) => [p.id, p.neutral ? NEUTRAL : p.color]));
    this.$ = (id) => document.getElementById(id);
    this.tipFor = null;
  }

  /** A `hud` colour role as a CSS colour. */
  color(role) {
    if (typeof role === "number") {
      const [r, g, b] = this.colors.get(role) || NEUTRAL;
      return `rgb(${r},${g},${b})`;
    }
    return `var(--${role === "text" ? "text" : role})`;
  }

  draw(turn, view) {
    this.top(turn, view);
    this.panel(view);
    this.footer(view);
    const end = this.$("end-turn");
    if (view.route) {
      end.replaceChildren(view.route.confirm[0]);
      end.disabled = !view.route.confirm[1];
      end.classList.toggle("route", true);
    } else {
      end.replaceChildren("End Turn ", h("span", { class: "key" }, "⏎"));
      end.disabled = !view.can.end_turn;
      end.classList.toggle("route", false);
    }
  }

  /** The turn counter, our ships lost, and the scoreboard at the most detail
   * that fits (`render._draw_scoreboard`). */
  top(turn, view) {
    this.$("turn").textContent = `Turn ${turn}`;
    this.$("lost").textContent = view.lost === null ? "" : `${view.lost} lost`;
    const ul = this.$("scores");
    const room = ul.clientWidth || ul.parentElement.clientWidth / 2;
    const width = (level) => view.scores.reduce((sum, s) => sum + 18 + textWidth(s.forms[level]), 0)
      + (level > 1 ? 14 : 22) * (view.scores.length - 1);
    let level = 0;
    while (level < 4 && width(level) > room) level += 1;
    ul.replaceChildren(...view.scores.map((s) => {
      const color = s.vis === "out" ? "rgb(70,74,90)" : this.color(s.id);
      const li = h("li", { class: s.vis, style: `color:${s.vis === "frozen" ? "var(--dim)" : color}` },
        h("span", { class: "swatch", style: `background-color:${color}` }), s.forms[level]);
      li.addEventListener("pointerenter", () => this.tip(s, li));
      li.addEventListener("pointerleave", () => this.tip(null));
      return li;
    }));
    if (this.tipFor !== null) {
      const s = view.scores.find((x) => x.id === this.tipFor);
      if (!s) this.tip(null);
    }
  }

  /** The scoreboard entry spelled out under the top bar. */
  tip(score, li = null) {
    const tip = this.$("tip");
    this.tipFor = score ? score.id : null;
    if (!score || !score.tip.length) {
      tip.hidden = true;
      return;
    }
    tip.replaceChildren(...score.tip.map((line) => h("p", {}, line)));
    tip.style.borderColor = this.color(score.id);
    tip.style.left = `${li.offsetLeft}px`;
    tip.hidden = false;
  }

  /** The side panel: the system in focus and its lane, or the legend. */
  panel(view) {
    const panel = this.$("panel");
    const p = view.panel;
    const clears = [];
    if (p.clear.all) {
      clears.push(h("button", { type: "button", class: "clear", onclick: () => this.on.call("clear_all_forward", []) },
        p.clear.all));
    }
    if (p.clear.dangerous) {
      clears.push(h("button", { type: "button", class: "clear danger",
        onclick: () => this.on.call("clear_dangerous_forward", []) }, p.clear.dangerous));
    }
    if (view.route) {
      const r = view.route;
      panel.replaceChildren(h("section", {}, h("h2", {}, r.title),
        ...r.lines.map(([text, role]) => h("p", { style: `color:${this.color(role)}` }, text)),
        h("p", { class: "wrap hint" }, r.hint)));
      return;
    }
    if (p.focus === null) {
      const set = this.legends[p.legend];
      const text = matchMedia("(hover: none)").matches ? set.touch : set.keys;
      const title = p.legend === "history" ? "History" : "Star Conquest";
      panel.replaceChildren(...clears, h("div", { class: "legend" }, h("h2", {}, title),
        ...text.split("\n\n").map((para) => h("p", {}, para.replace(/\n/g, " ")))));
      return;
    }
    const rows = (list, lore = false) => list.map(([text, role, kind], i) => {
      const style = `color:${this.color(role)}`;
      if (kind === "head") {
        const head = h("h2", { style, class: textWidth(text, 18) > 212 - (lore ? 26 : 0) ? "long" : "" }, text);
        if (i === 0 && lore) {
          return h("div", { class: "head-row" }, head,
            h("button", { type: "button", class: "lore", "aria-label": "About this star",
              onclick: () => this.on.call("open_lore", []) },
              "i"));
        }
        return head;
      }
      return h("p", { style, class: kind === "wrap" ? "wrap" : "" }, text);
    });
    const parts = [...clears, h("section", {}, rows(p.system, p.lore))];
    if (p.lane.length) parts.push(h("section", {}, rows(p.lane)));
    const touch = matchMedia("(hover: none)").matches;
    if (p.rule.length) parts.push(h("section", {}, rows(touch ? p.rule_touch : p.rule)));
    panel.replaceChildren(...parts);
  }

  /** The footer strip's two clusters (`hud.footer`), or history's scrubber. */
  footer(view) {
    const nav = this.$("footer");
    if (view.history) {
      this.scrubber(nav, view);
      return;
    }
    this.scrub = null;
    const button = ([attr, label, key, colour]) => h("button", {
      type: "button", class: `btn ${colour}`, "data-attr": attr,
      onclick: () => this.on.footer(attr),
    }, label, key ? h("span", { class: "key" }, ` (${key})`) : null);
    const left = view.footer.filter((b) => b[5] === "left").map(button);
    const right = view.footer.filter((b) => b[5] === "right").map(button);
    nav.replaceChildren(...left, h("span", { class: "spacer" }), ...right);
  }

  /** The hint's caption over the map, the card over the board, and
   * history's graphs. */
  overlays(view) {
    this.graph(view);
    const touch = matchMedia("(hover: none)").matches;
    const cap = this.$("hint-caption");
    cap.hidden = !view.hint;
    if (view.hint) cap.textContent = touch ? view.hint.caption.touch : view.hint.caption.keys;
    const box = this.$("card");
    const card = view.card;
    if (!card) {
      box.hidden = true;
      return;
    }
    if (card.kind === "lore") {
      box.style.borderColor = "rgb(110,140,200)";
      box.replaceChildren(h("h1", {}, card.title), h("p", { class: "sub", style: "color:rgb(110,140,200)" }, card.sub),
        ...card.paras.map((t) => h("p", {}, t)), ...card.notes.map((t) => h("p", { class: "note" }, t)),
        h("p", { class: "close" }, touch ? card.close.touch : card.close.keys));
    } else {
      const tone = `var(--${card.tone})`;
      box.style.borderColor = tone;
      const items = card.items.map(([kind, a, b, total]) => {
        if (kind === "row") return h("div", { class: `row${total ? " total" : ""}` }, h("span", {}, a), h("span", {}, b));
        if (kind === "text") return h("p", { style: `color:var(--${b})` }, a);
        if (kind === "head") return h("h2", {}, a);
        if (kind === "note") return h("p", { class: "note" }, a);
        return h("div", { class: "gap" });
      });
      box.replaceChildren(h("h1", {}, card.title), h("p", { class: "sub", style: `color:${tone}` }, card.label),
        ...items, h("p", { class: "close" }, touch ? "Tap anywhere to close" : "Press any key to close"));
    }
    box.hidden = false;
  }

  /** A two-button ask (`widgets.draw_modal`): `onConfirm` or dismiss. */
  modal(m, onConfirm, onCancel) {
    const box = this.$("modal");
    if (!m) {
      box.hidden = true;
      return;
    }
    const touch = matchMedia("(hover: none)").matches;
    box.replaceChildren(h("div", { class: "stack" }, h("h1", {}, m.title),
      ...m.body.split("\n\n").map((t) => h("p", {}, t)),
      h("div", { class: "grid" },
        h("button", { type: "button", class: "btn amber", onclick: onConfirm }, m.confirm,
          touch ? null : h("span", { class: "key" }, " (Y/Enter)")),
        h("button", { type: "button", class: "btn green", onclick: onCancel }, "Cancel",
          touch ? null : h("span", { class: "key" }, " (N/Esc)")))));
    box.hidden = false;
  }

  /** The game-over veil (`render._draw_win_overlay`): who won, the result,
   * and what to do next. */
  veil(view) {
    const veil = this.$("veil");
    const over = view.over;
    if (!over) {
      veil.hidden = true;
      return;
    }
    const button = (label, key, colour, action) => h("button", { type: "button", class: `btn ${colour}`,
      onclick: () => this.on.action(action) }, label, key ? h("span", { class: "key" }, ` (${key})`) : null);
    const second = over.next_stage ? button("Next stage", "N", "teal", "next_stage")
      : over.in_stage ? null : button("New map", "N", "green", "restart");
    const kids = [
      h("h1", { style: `color:${this.color(over.headline[1])}` }, over.headline[0]),
      ...over.lines.map(([kind, text, role]) => h("p", { class: kind, style: `color:${this.color(role)}` }, text)),
      h("div", { class: "grid" }, button("Retry", "T", "amber", "retry"), second,
        button("Setup menu", "M", "blue", "menu"), button("History & coach", "H", "violet", "toggle_history")),
    ];
    if (over.share) {
      kids.push(h("div", { class: "share" }, button(over.share[0], over.share[1], "teal", "share"),
        over.board ? button(over.board[0], over.board[1], "gold", "leaderboard") : null));
      if (over.notes) kids.push(h("p", { class: "small dim" }, over.notes));
    }
    veil.replaceChildren(h("div", { class: "stack" }, ...kids));
    veil.hidden = false;
  }

  /** History's bar (`render._draw_scrubber`): Exit, Play, ◀ the track ▶, the
   * turn on screen, and Rewind to here before the last turn. The track is
   * built once and updated, so a drag is never replaced under the pointer. */
  scrubber(nav, view) {
    const hv = view.history;
    const key = (k) => h("span", { class: "key" }, ` (${k})`);
    if (!this.scrub) {
      const ui = {};
      ui.exit = h("button", { type: "button", class: "btn blue", onclick: () => this.on.action("toggle_history") },
        "Exit", key("Esc"));
      ui.play = h("button", { type: "button", class: "btn blue", onclick: () => this.on.action("toggle_play") });
      ui.prev = h("button", { type: "button", class: "btn blue arrow", "aria-label": "Previous turn",
        onclick: () => this.on.seek(Math.max(0, this.view.history.turn - 1)) }, "◀");
      ui.next = h("button", { type: "button", class: "btn blue arrow", "aria-label": "Next turn",
        onclick: () => this.on.seek(this.view.history.turn + 1) }, "▶");
      ui.track = h("input", { type: "range", min: 0, step: 1, class: "track", "aria-label": "Turn" });
      ui.track.addEventListener("input", () => this.on.seek(Number(ui.track.value)));
      ui.label = h("span", { class: "label" });
      ui.rewind = h("button", { type: "button", class: "btn amber", onclick: () => this.on.action("rewind") },
        "Rewind to here");
      ui.graphs = h("button", { type: "button", class: "btn violet", onclick: () => this.on.call("toggle_graph", []) },
        "Graphs", key("G"));
      nav.replaceChildren(ui.exit, ui.play, ui.graphs, ui.prev, ui.track, ui.next, ui.label, ui.rewind);
      this.scrub = ui;
    }
    this.view = view;
    const ui = this.scrub;
    ui.play.replaceChildren(view.playing ? "Pause" : "Play", key("P"));
    ui.play.className = `btn ${view.playing ? "active" : "blue"}`;
    ui.prev.disabled = hv.turn <= 0;
    ui.next.disabled = hv.turn >= hv.max;
    ui.track.max = hv.max;
    if (document.activeElement !== ui.track) ui.track.value = hv.turn;
    ui.label.textContent = hv.label;
    ui.rewind.hidden = !hv.rewind;
    ui.graphs.hidden = !hv.graphs;
    ui.graphs.className = `btn ${hv.graph_on ? "active" : "violet"}`;
  }

  /** History's graphs (`render._draw_history_graph`): the tabs, a line per
   * seat (a stacked bar per turn for Lost/turn, stacked bands for Map share)
   * with a cursor on the turn shown, and the legend, which shows or hides a
   * seat's line; or the coach's notes, each a jump to its turn. */
  graph(view) {
    const box = this.$("graph");
    const g = view.graph;
    if (!g) {
      box.hidden = true;
      return;
    }
    const touch = matchMedia("(hover: none)").matches;
    box.classList.toggle("full", g.full);
    const tabs = g.tabs.map((label, i) => h("button", { type: "button", class: `btn ${i === g.tab ? "active" : "blue"}`,
      onclick: () => this.on.call("graph_tab", [i]) }, label));
    const head = h("div", { class: "head" }, ...tabs, h("span", { class: "note" }, touch ? g.note.touch : g.note.keys),
      h("button", { type: "button", class: "btn blue caret", "aria-label": g.full ? "Dock the graphs" : "Full screen",
        onclick: () => this.on.call("graph_full", []) }, g.full ? "⌄" : "⌃"));
    let body;
    if (g.coach) {
      body = h("div", { class: "coach" }, h("p", { class: "dim" }, g.coach.summary),
        ...(g.coach.notes.length ? g.coach.notes.map(([turn, role, text], i) => h("button", {
          type: "button", class: `note-row${i === g.coach.sel ? " on" : ""}`, onclick: () => this.on.call("select_note", [i]),
        }, h("span", { class: "turn", style: `color:${this.color(role)}` }, `T${turn}`), h("span", {}, text)))
          : [h("p", {}, "Nothing to flag: no failed attacks, emptied systems or idle ships.")]));
    } else {
      const plot = sv("svg", { class: "plot" });
      const legend = h("div", { class: "legend" }, h("div", { class: "unit" },
        g.rows.some((r) => r[4]) ? h("button", { type: "button", class: "all", onclick: () => this.on.call("show_all_traces", []) },
          "Show all") : h("span"), h("span", {}, g.unit)),
      ...g.rows.map(([pid, name, who, value, hidden]) => h("button", { type: "button", class: `seat${hidden ? " off" : ""}`,
        onclick: () => this.on.call("toggle_trace", [pid]) },
      h("span", { class: "swatch", style: `background:${this.color(pid)}` }), h("span", { class: "name" }, name,
        h("span", { class: "dim" }, ` · ${who}`)), h("span", { class: "value" }, value))),
      g.unclaimed !== null ? h("div", { class: "seat" }, h("span", { class: "swatch", style: "background:rgb(70,74,90)" }),
        h("span", { class: "name dim" }, "Unclaimed"), h("span", { class: "value" }, g.unclaimed)) : null);
      body = h("div", { class: "chart" }, plot, legend);
      this.plotData = { g, plot };
    }
    box.replaceChildren(head, body);
    box.hidden = false;
    if (!g.coach) this.plot();
  }

  /** Draw the plot into its SVG at the size the layout gave it. */
  plot() {
    const { g, plot } = this.plotData;
    const W = plot.clientWidth || 600, H = plot.clientHeight || 200;
    const left = g.area ? 40 : 30, right = 8, top = 8, bottom = 22;
    const pw = Math.max(1, W - left - right), ph = Math.max(1, H - top - bottom);
    plot.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const ids = Object.keys(g.series);
    const val = (pid, t) => g.series[pid][t] ?? null;
    let peak;
    if (g.area) peak = 100;
    else if (g.stacked) {
      peak = 0;
      for (let t = 0; t <= g.turns; t++) peak = Math.max(peak, ids.reduce((s, pid) => s + (val(pid, t) ?? 0), 0));
    } else peak = Math.max(0, ...ids.flatMap((pid) => g.series[pid].filter((v) => v !== null)));
    // A whole-number metric never gets a fractional gridline (its axis starts
    // at 5); a share spans the whole map (`render._draw_history_graph`).
    const ymax = g.area ? 100 : niceCeil(g.metric === "income" ? peak : Math.max(peak, 5));
    const x = (t) => left + (g.turns > 0 ? (t / g.turns) * pw : 0);
    const y = (v) => top + ph - (v / ymax) * ph;
    const grid = sv("g", { stroke: "rgb(40,44,60)", "stroke-width": 1 }, plot);
    const labels = sv("g", { fill: "rgb(140,148,165)", "font-size": 14, "font-family": FONT }, plot);
    // 1x and 5x tops split into fifths, 2x into quarters: every line a round figure.
    const steps = String(ymax).replace(/^[0.]+/, "")[0] === "2" ? 4 : 5;
    for (let i = 0; i <= steps; i++) {
      const v = (ymax / steps) * i;
      sv("line", { x1: left, x2: left + pw, y1: y(v), y2: y(v) }, grid);
      const t = sv("text", { x: 2, y: y(v) + 5 }, labels);
      t.textContent = g.area ? `${Math.round(v)}%` : g.metric === "income" ? v.toFixed(1) : String(Math.round(v));
    }
    const room = Math.max(1, Math.floor(pw / 110));
    const tick = Math.max(1, niceCeil(g.turns / room));
    for (let t = 0; t <= g.turns; t += tick) {
      const lab = sv("text", { x: x(t), y: H - 4, "text-anchor": t === 0 ? "start" : "middle" }, labels);
      lab.textContent = t === 0 ? "Turn 0" : String(t);
    }
    const color = (pid) => `rgb(${g.colors[pid].join(",")})`;
    if (g.stacked || g.area) {
      const bw = Math.max(1, pw / (g.turns + 1));
      const base = new Array(g.turns + 1).fill(0);
      for (const pid of ids) {
        if (g.area) {
          const pts = [];
          for (let t = 0; t <= g.turns; t++) pts.push([x(t), y(base[t] + (val(pid, t) ?? 0))]);
          const back = [];
          for (let t = g.turns; t >= 0; t--) back.push([x(t), y(base[t])]);
          sv("polygon", { points: pts.concat(back).map((p) => p.join(",")).join(" "), fill: color(pid), opacity: 0.85 }, plot);
        } else {
          for (let t = 0; t <= g.turns; t++) {
            const v = val(pid, t) ?? 0;
            if (v > 0) sv("rect", { x: x(t) - bw / 2, y: y(base[t] + v), width: bw * 0.8, height: y(base[t]) - y(base[t] + v),
              fill: color(pid) }, plot);
          }
        }
        for (let t = 0; t <= g.turns; t++) base[t] += val(pid, t) ?? 0;
      }
    } else {
      for (const pid of ids) {
        let run = [];
        const flush = () => {
          if (run.length > 1) sv("polyline", { points: run.join(" "), fill: "none", stroke: color(pid), "stroke-width": 2 }, plot);
          run = [];
        };
        g.series[pid].forEach((v, t) => (v === null ? flush() : run.push(`${x(t)},${y(v)}`)));
        flush();
        const v = val(pid, g.turn);
        if (v !== null) sv("circle", { cx: x(g.turn), cy: y(v), r: 4, fill: color(pid) }, plot);
      }
    }
    sv("line", { x1: x(g.turn), x2: x(g.turn), y1: top, y2: top + ph, stroke: "rgb(200,206,220)", "stroke-width": 1 }, plot);
    const seekAt = (event) => {
      const r = plot.getBoundingClientRect();
      const t = Math.round(((event.clientX - r.left - left) / pw) * g.turns);
      this.on.seek(Math.max(0, Math.min(g.turns, t)));
    };
    plot.onpointerdown = (event) => {
      plot.setPointerCapture?.(event.pointerId);
      seekAt(event);
    };
    plot.onpointermove = (event) => {
      if (event.buttons & 1) seekAt(event);
    };
  }

  /** The send popup (`render._draw_send_popup`), or nothing. Returns its node
   * when shown, for the page to place. Built once per lane and tab, then
   * updated in place, so a slider being dragged is never replaced under the
   * pointer. */
  popup(view) {
    const box = this.$("popup");
    const pop = view.popup;
    if (!pop) {
      box.hidden = true;
      box.replaceChildren();
      this.pop = null;
      return null;
    }
    const shape = [pop.src, pop.dest, pop.forward, Boolean(pop.forecast),
      pop.split ? pop.split.lanes.map((l) => l[0]).join("+") : ""].join();
    if (this.pop?.shape !== shape) this.pop = this.buildPopup(box, view, shape);
    const ui = this.pop;
    ui.ships.textContent = pop.dest_ships === null ? "?sh" : `${pop.dest_ships}sh`;
    ui.ships.style.color = pop.dest_ships === null ? "var(--fog)" : this.color(pop.dest_owner);
    ui.effect.textContent = pop.effect;
    if (pop.forecast) {
      ui.forecast.textContent = fit(pop.forecast.forms, 168 - (pop.forecast.odds ? 17 : 0));
      ui.forecast.className = `tone-${pop.forecast.tone}`;
      ui.odds.hidden = !pop.forecast.odds;
      ui.forecastRow.disabled = !pop.forecast.odds;
    }
    if (pop.hold) {
      ui.holdValue.textContent = String(pop.hold.value);
      ui.holdValue.style.color = pop.hold.value || pop.hold.smart ? "var(--text)" : "var(--dim)";
      ui.smart.classList.toggle("on", pop.hold.smart);
    }
    if (pop.split) {
      pop.split.lanes.forEach(([dest, pct, hostile], i) => {
        const row = ui.lanes[i];
        row.pct.textContent = `${pct}%`;
        row.name.style.color = hostile ? "var(--gamble)" : "var(--text)";
        row.node.classList.toggle("on", dest === view.dest);
        row.seg.style.width = `${pct}%`;
      });
      ui.stays.textContent = `${pop.split.stays}%`;
      ui.even.classList.toggle("on", pop.split.even);
    } else {
      ui.value.textContent = pop.value;
      const [lo, hi, value] = pop.slider;
      ui.range = [lo, hi];
      if (!ui.dragging) {
        Object.assign(ui.slider, { min: lo, max: hi });
        ui.slider.value = value;
      }
      pop.presets.forEach(([label, on], i) => {
        ui.presets[i].textContent = label;
        ui.presets[i].classList.toggle("on", on);
      });
    }
    ui.discard.textContent = pop.discard;
    box.hidden = false;
    return box;
  }

  buildPopup(box, view, shape) {
    const pop = view.popup;
    const call = (method, ...args) => () => this.on.call(method, args);
    box.className = [pop.forward ? "forward" : "", pop.split ? "split" : ""].join(" ").trim();
    const src = this.map.systems.find((s) => s.id === pop.src);
    const dest = this.map.systems.find((s) => s.id === pop.dest);
    const ui = { shape, dragging: false, range: [0, 0] };
    const step = (label, onclick) => h("button", { type: "button", class: "step", "aria-label": label, onclick },
      label === "Fewer" ? "−" : "+");
    ui.ships = h("span");
    ui.effect = h("div", { class: "row dim" });
    ui.value = h("span");
    ui.slider = h("input", { type: "range", step: 1, "aria-label": pop.forward ? "Share to forward" : "Ships to send" });
    ui.slider.addEventListener("pointerdown", () => { ui.dragging = true; });
    ui.slider.addEventListener("pointerup", () => { ui.dragging = false; });
    ui.slider.addEventListener("input", () => {
      const [lo, hi] = ui.range;
      this.on.call("set_slider_fraction", [hi > lo ? (Number(ui.slider.value) - lo) / (hi - lo) : 0]);
    });
    ui.presets = [0, 1].map((i) => h("button", {
      type: "button", class: "pb",
      onclick: pop.forward ? call("set_share", i === 0 ? 50 : 100) : call(i === 0 ? "send_half" : "send_all"),
    }));
    ui.discard = h("button", { type: "button", class: "pb danger", onclick: call("cancel_send") });
    const kids = [
      h("div", { class: "row tabs" },
        h("button", { type: "button", class: `tab${pop.forward ? "" : " on"}`, onclick: call("set_forward_mode", false) },
          "Send"),
        h("button", { type: "button", class: `tab${pop.forward ? " on" : ""}`, onclick: call("set_forward_mode", true) },
          "Forward")),
      pop.split
        ? h("div", { class: "row title" }, h("span", {}, `${src.name} splits`),
          h("span", { style: `color:${this.color(view.human)}` }, `${pop.garrison}sh`))
        : h("div", { class: "row title" }, h("span", {}, `${src.name} -> ${dest.name}`), ui.ships),
    ];
    if (pop.hold) {
      ui.holdValue = h("span", { class: "value" });
      ui.smart = h("button", { type: "button", class: "pb chip", onclick: call("toggle_smart_hold") }, "smart");
      kids.push(h("div", { class: "row well" }, h("span", { class: "dim" }, "holds"), ui.smart,
        h("span", { class: "spacer" }),
        step("Fewer", call("step_hold", -1)), ui.holdValue, step("More", call("step_hold", 1))));
    }
    if (pop.split) {
      const swatches = laneSwatches(pop.split.lanes.length);
      ui.lanes = pop.split.lanes.map(([d], i) => {
        const sys = this.map.systems.find((x) => x.id === d);
        const row = { name: h("span", { class: "name" }, sys.name), pct: h("span", { class: "value" }),
          seg: h("span", { class: "seg", style: `background:${swatches[i]}` }) };
        row.node = h("div", { class: "row lane", onclick: (e) => {
          if (!e.target.closest(".step")) this.on.call("focus_lane", [d]);
        } }, h("span", { class: "swatch", style: `background:${swatches[i]}` }), row.name,
        step("Fewer", () => this.on.lane(d, -1)), row.pct, step("More", () => this.on.lane(d, 1)));
        return row;
      });
      ui.stays = h("span", { class: "value dim" });
      ui.even = h("button", { type: "button", class: "pb", onclick: call("even_split") }, "Even split");
      kids.push(...ui.lanes.map((r) => r.node),
        h("div", { class: "row lane" }, h("span", { class: "swatch home" }), h("span", { class: "name dim" }, "stays"),
          h("span", { class: "step-gap" }), ui.stays, h("span", { class: "step-gap" })),
        h("div", { class: "row" }, h("div", { class: "bar" }, ...ui.lanes.map((r) => r.seg))),
        ui.effect,
        h("div", { class: "pair" }, ui.even,
          h("button", { type: "button", class: "pb danger", onclick: call("delete_all_forward") }, "Delete all")),
        h("div", { class: "pair" }, ui.discard));
      box.replaceChildren(...kids);
      return ui;
    }
    kids.push(ui.effect);
    if (pop.forecast) {
      ui.forecast = h("span");
      ui.odds = h("span", { class: "i" }, "i");
      ui.forecastRow = h("button", { type: "button", class: "row forecast", onclick: () => this.on.call("open_odds", []) },
        ui.forecast, ui.odds);
      kids.push(ui.forecastRow);
    }
    kids.push(
      h("div", { class: "row stepper" },
        h("button", { type: "button", class: "step", "aria-label": "Fewer", onclick: call("step_count", -1) }, "−"),
        ui.value,
        h("button", { type: "button", class: "step", "aria-label": "More", onclick: call("step_count", 1) }, "+")),
      h("div", { class: "row" }, ui.slider),
      h("div", { class: "pair" }, ...ui.presets),
      h("div", { class: "pair" }, ui.discard),
    );
    box.replaceChildren(...kids);
    return ui;
  }
}
