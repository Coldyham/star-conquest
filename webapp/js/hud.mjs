// The play screen's DOM: the top bar's scoreboard, the side panel, the send
// popup and the footer strip, each drawn from the session's `view`
// (`serial.view`). The words are Python's (`hud`); this only lays them out,
// picks the longest wording that fits, and turns presses into calls.

const NEUTRAL = [122, 128, 140];
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

  /** The footer strip's two clusters (`hud.footer`). */
  footer(view) {
    const nav = this.$("footer");
    const button = ([attr, label, key, colour]) => h("button", {
      type: "button", class: `btn ${colour}`, "data-attr": attr,
      onclick: () => this.on.footer(attr),
    }, label, key ? h("span", { class: "key" }, ` (${key})`) : null);
    const left = view.footer.filter((b) => b[5] === "left").map(button);
    const right = view.footer.filter((b) => b[5] === "right").map(button);
    nav.replaceChildren(...left, h("span", { class: "spacer" }), ...right);
  }

  /** The hint's caption over the map, and the card over the board, if any. */
  overlays(view) {
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
