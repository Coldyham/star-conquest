// The play screen's DOM: the top bar's scoreboard, the side panel, the send
// popup and the footer strip, each drawn from the session's `view`
// (`serial.view`). The words are Python's (`hud`); this only lays them out,
// picks the longest wording that fits, and turns presses into calls.

import { drawGraph } from "./stagegraph.mjs";

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

/** Size the page as pygame's shell sizes itself (`main.ui_scale_for`,
 * `config.apply_ui_scale`): on a touch screen one baseline unit is up to a CSS
 * pixel, less where that would leave fewer than 600 x 480 units to lay out in;
 * a touch screen under 1100 x 720 units is compact, and compact held upright
 * is portrait. A desktop window keeps one unit to a CSS pixel until it is
 * smaller than a screen needs (1100 x 720 for play, 860 x 930 for the menu,
 * which zooms by `--menu-zoom`), then shrinks to fit, down to half size. Sets `--zoom` and the `touch`, `compact` and `portrait` classes
 * on the root, which the stylesheet lays out by. */
export function fitLayout() {
  // ?input=touch or ?input=mouse says which to assume: for trying the phone
  // layouts on a desktop, and for headless shots, where `hover` can't be faked.
  const input = new URLSearchParams(location.search).get("input");
  const finger = input ? input === "touch" : matchMedia("(hover: none)").matches;
  const cw = innerWidth, ch = innerHeight;
  let zoom = 1;
  if (finger) {
    const fit = Math.max(1, Math.min(cw / 1440, ch / 960));   // config.BASE_SCREEN_W/H
    const room = Math.min(cw / 600, ch / 480);                  // config.TOUCH_MIN_W/H
    zoom = fit * Math.max(0.5, Math.min(1, room));              // TOUCH_CSS_MIN, TOUCH_CSS_SCALE
  }
  let menuZoom = zoom;
  if (!finger) {
    const shrink = (w, h) => Math.max(0.5, Math.min(1, cw / w, ch / h));
    zoom = shrink(1100, 720);                                    // COMPACT_MAX_W/H
    menuZoom = shrink(860, 930);
  }
  const lw = cw / zoom, lh = ch / zoom;
  const small = finger && (lw < 1100 || lh < 720);              // COMPACT_MAX_W/H
  const root = document.documentElement;
  root.style.setProperty("--zoom", String(zoom));
  root.style.setProperty("--menu-zoom", String(menuZoom));
  // A phone's buttons stand at least 38 CSS pixels tall, a fingertip, however
  // far the zoom shrinks the rest of the layout (in layout units, as all else).
  root.style.setProperty("--tap", `${small ? Math.max(40, Math.ceil(38 / zoom)) : 40}px`);
  root.classList.toggle("touch", finger);
  root.classList.toggle("compact", small);
  root.classList.toggle("portrait", small && lh > lw);
  return zoom;
}

/** No pointer that hovers: a finger (`config.touch_ui`). */
export function touch() {
  return document.documentElement.classList.contains("touch");
}

/** A button's height in layout units: 40, or a fingertip on a phone (`--tap`). */
export function tap() {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tap")) || 40;
}

/** A touch screen too small for the desktop layout (`config.compact`). */
export function compact() {
  return document.documentElement.classList.contains("compact");
}

/** A compact screen held upright (`config.portrait`). */
export function portrait() {
  return document.documentElement.classList.contains("portrait");
}

/** Screen pixels per layout unit inside the app (`--zoom`). */
export function zoom() {
  return Number(getComputedStyle(document.documentElement).getPropertyValue("--zoom")) || 1;
}

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
    // A tap pins a scoreboard entry's tip (`input._track_score_hover`): a
    // finger's lift is a pointerleave, so only a press elsewhere lets it go.
    document.addEventListener("pointerdown", (e) => {
      if (this.tipFor !== null && !e.target.closest?.("#scores li")) this.tip(null);
    }, true);
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
      end.classList.toggle("green", true);
      end.classList.toggle("active", false);
    } else if (view.pbp?.end_word && !view.can.end_turn) {
      // A spectator's, or a shared turn already sent: nothing here to end.
      end.replaceChildren(view.pbp.end_word);
      end.disabled = true;
      end.classList.toggle("route", true);
      end.classList.toggle("green", true);
      end.classList.toggle("active", false);
    } else {
      // While autoplay or Play runs, the button stops it (`hud.end_turn_button`).
      const [word, action] = view.end;
      const stops = action !== "end_turn";
      end.replaceChildren(`${word} `, h("span", { class: "key" }, "⏎"));
      end.disabled = !stops && !view.can.end_turn;
      end.classList.toggle("route", false);
      end.classList.toggle("green", !stops);
      end.classList.toggle("active", stops);
    }
    this.pbp(view);
  }

  /** Play by post's overlay on the map (`serial.pbp`): the invite with each
   * seat's link to copy, the waiting veil, or a line in a pill. */
  pbp(view) {
    const box = this.$("pbp");
    const o = view.pbp?.overlay;
    box.className = o ? o.kind : "";
    if (!o) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    box.hidden = false;
    if (o.kind === "invite") {
      box.replaceChildren(h("div", { class: "panel", role: "dialog", "aria-modal": "true" },
        h("h1", {}, o.title), h("p", { class: "sub" }, o.subtitle),
        ...o.rows.map(([seat, label, role, copy, copied]) => h("div", { class: "row" },
          h("span", { class: "swatch", style: `background:${this.color(role)}` }),
          h("span", { class: "name", style: `color:${this.color(role)}` }, label),
          h("button", { type: "button", class: `btn ${copied ? "active" : "blue"}`, onclick: () => this.on.copy(seat) },
            copy))),
        h("button", { type: "button", class: "btn green done", onclick: () => this.on.call("close_invite", []) },
          o.done)));
    } else if (o.kind === "waiting") {
      box.replaceChildren(h("div", { class: "stack" }, h("h1", {}, o.headline),
        o.detail ? h("p", {}, o.detail) : null, o.msg ? h("p", { class: "small" }, o.msg) : null));
    } else {
      box.replaceChildren(h("p", { class: "pill" }, o.text));
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
      li.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") this.tip(s, li); });
      li.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") this.tip(null); });
      li.addEventListener("pointerdown", (e) => {
        if (e.pointerType !== "mouse") this.tip(this.tipFor === s.id ? null : s, li);
      });
      return li;
    }));
    if (this.tipFor !== null) {
      // the entries were rebuilt: keep the tip, refreshed, on the new one
      const i = view.scores.findIndex((x) => x.id === this.tipFor);
      this.tip(i < 0 ? null : view.scores[i], ul.children[i]);
    }
  }

  /** The scoreboard entry spelled out under the top bar, left edge on the
   * entry's but kept inside the map (`render._draw_score_tooltip`). */
  tip(score, li = null) {
    const tip = this.$("tip");
    this.tipFor = score ? score.id : null;
    if (!score || !score.tip.length) {
      tip.hidden = true;
      return;
    }
    tip.replaceChildren(...score.tip.map((line) => h("p", {}, line)));
    tip.style.borderColor = this.color(score.id);
    const room = this.$("mapbox").clientWidth;
    const pad = 8;
    tip.style.maxWidth = `${room - 2 * pad}px`;
    tip.style.left = "0px"; // measured unsqueezed by its old place
    tip.hidden = false;
    tip.style.left = `${Math.max(pad, Math.min(li.offsetLeft, room - pad - tip.offsetWidth))}px`;
  }

  /** The side panel: the system in focus and its lane, or the legend. */
  panel(view) {
    const panel = this.$("panel");
    const p = view.panel;
    // The match's notes (`hud.match_notes`): at the column's head, or on a
    // portrait band only while nothing is in focus.
    const notes = view.notes.map(([text, role]) => h("p", { class: "wrap match-note",
      style: `color:${this.color(role)}` }, text));
    // A shared match says which seat is ours, and the match's name, first.
    const you = view.pbp?.you ? [h("p", { style: `color:${this.color(view.pbp.you[1])}` }, view.pbp.you[0]),
      view.pbp.label ? h("p", { class: "wrap dim" }, view.pbp.label) : null].filter(Boolean) : [];
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
      panel.replaceChildren(...you, ...(portrait() ? [] : notes), h("section", {}, h("h2", {}, r.title),
        ...r.lines.map(([text, role]) => h("p", { style: `color:${this.color(role)}` }, text)),
        h("p", { class: "wrap hint" }, r.hint)));
      return;
    }
    // A gauntlet stage's reminder (`render._panel_stage_hint`), over the
    // legend: a tap brings the briefing back, as B does. On a phone it is the
    // panel's one job, the briefing having taught the rules.
    const hint = p.stage_hint ? [h("button", { type: "button", class: "stage-hint",
      onclick: () => this.on.call("open_briefing", []) }, p.stage_hint)] : [];
    if (p.focus === null) {
      if (portrait()) {
        panel.replaceChildren(...you, ...clears, ...notes, ...hint,
          ...(hint.length ? [] : [h("p", { class: "wrap dim" }, p.portrait)]));
        return;
      }
      if (hint.length && compact()) {
        panel.replaceChildren(...you, ...notes, ...clears, ...hint);
        return;
      }
      const set = this.legends[p.legend];
      const text = compact() ? set.compact : touch() ? set.touch : set.keys;
      const title = p.legend === "history" ? "History" : "Star Conquest";
      panel.replaceChildren(...you, ...notes, ...clears, ...hint, h("div", { class: "legend" }, h("h2", {}, title),
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
    const finger = touch();
    if (portrait()) {
      // A full-width band a few rows tall: the system on the left, its lane
      // and rule on the right (`render._draw_side_panel_body`).
      const right = [...(p.lane.length ? rows(p.lane) : []), ...(p.rule.length ? rows(p.rule_touch) : [])];
      panel.replaceChildren(...you, h("div", { class: "band" }, h("section", {}, rows(p.compact, p.lore)),
        h("section", {}, right)));
      return;
    }
    const parts = [...you, ...notes, ...clears, h("section", {}, rows(p.system, p.lore))];
    if (p.lane.length) parts.push(h("section", {}, rows(p.lane)));
    if (p.rule.length) parts.push(h("section", {}, rows(finger ? p.rule_touch : p.rule)));
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
    // Squeeze as `render._lay_out_footer` does: the least essential button
    // folds first, from either cluster, behind a "more" button whose list
    // offers it again; a phone folds the ways out of this game from the start.
    const finger = touch();
    const text = (b) => b[1] + (b[2] && !finger ? ` (${b[2]})` : "");
    const fbh = tap(), gap = 10, size = compact() ? 20 : 18;              // play.css .compact #footer .btn
    const width = (b) => Math.max(fbh, textWidth(text(b), size) + 2 * 14);   // widgets.btn_w
    const avail = nav.clientWidth - 28;
    let shown = [...view.footer];
    // Upright, the strip is in the thumb's reach: what isn't pressed turn by
    // turn (`hud.phone_folds`) goes behind "more" with the exits.
    const phone = portrait() ? new Set(view.phone_folds || []) : new Set();
    const folded = compact() ? shown.filter((b) => b[5] === "left" || phone.has(b[0])) : [];
    shown = shown.filter((b) => !folded.includes(b));
    const need = (items) => items.reduce((sum, b) => sum + width(b), 0) + gap * (items.length - 1)
      + (folded.length ? fbh + gap : 0);
    while (shown.length && need(shown) > avail) {
      const least = shown.reduce((a, b) => (b[4] < a[4] ? b : a));
      shown.splice(shown.indexOf(least), 1);
      folded.push(least);
    }
    const button = (b) => h("button", { type: "button", class: `btn ${b[3]}`, "data-attr": b[0],
      onclick: () => this.on.footer(b[0]) }, b[1], b[2] ? h("span", { class: "key" }, ` (${b[2]})`) : null);
    const kids = [];
    if (folded.length) {
      kids.push(h("button", { type: "button", class: `btn ${this.moreOpen ? "active" : "blue"} more`, "aria-label": "More",
        onclick: (e) => {
          e.stopPropagation();
          this.moreOpen = !this.moreOpen;
          this.footer(view);
        } }, "☰"));
    } else {
      this.moreOpen = false;
    }
    kids.push(...shown.filter((b) => b[5] === "left").map(button), h("span", { class: "spacer" }),
      ...shown.filter((b) => b[5] === "right").map(button));
    nav.replaceChildren(...kids);
    this.moreList(view, view.footer.filter((b) => folded.includes(b)));
  }

  /** The "more" button's list (`render._draw_more_menu`): the folded buttons
   * again, in strip order, a glyph spelled out. Any press closes it. */
  moreList(view, items) {
    const box = this.$("more");
    if (!this.moreOpen || !items.length) {
      box.hidden = true;
      return;
    }
    const labels = view.more_labels;
    box.replaceChildren(...items.map((b) => {
      const [old, fresh] = labels[b[0]] || [null, null];
      return h("button", { type: "button", class: `btn ${b[3]}`, "data-attr": b[0], onclick: () => {
        this.moreOpen = false;
        box.hidden = true;
        this.on.footer(b[0]);
      } }, old ? b[1].replace(old, fresh) : b[1], b[2] ? h("span", { class: "key" }, ` (${b[2]})`) : null);
    }));
    const more = this.$("footer").querySelector(".more").getBoundingClientRect();
    const k = zoom();
    box.style.left = `${more.left / k}px`;
    box.style.bottom = `${(innerHeight - more.top) / k + 6}px`;
    box.hidden = false;
  }

  /** The hint's caption over the map, the card over the board, and
   * history's graphs. */
  overlays(view) {
    this.graph(view);
    const finger = touch();
    const cap = this.$("hint-caption");
    cap.hidden = !view.hint;
    if (view.hint) cap.textContent = finger ? view.hint.caption.touch : view.hint.caption.keys;
    const box = this.$("card");
    const card = view.card;
    if (!card) {
      box.hidden = true;
      return;
    }
    if (card.kind === "briefing") {
      // A gauntlet stage's briefing (`render._draw_briefing`): any press puts it away.
      box.style.borderColor = "var(--teal)";
      const close = !finger ? card.close.keys : portrait() ? card.close.portrait : card.close.touch;
      box.replaceChildren(h("h1", {}, card.title), h("p", { class: "sub", style: "color:var(--teal)" }, card.sub),
        ...card.paras.map((t) => h("p", {}, t)), ...card.notes.map((t) => h("p", { class: "note" }, t)),
        h("p", { class: "close" }, close));
    } else if (card.kind === "lore") {
      box.style.borderColor = "rgb(110,140,200)";
      box.replaceChildren(h("h1", {}, card.title), h("p", { class: "sub", style: "color:rgb(110,140,200)" }, card.sub),
        ...card.paras.map((t) => h("p", {}, t)), ...card.notes.map((t) => h("p", { class: "note" }, t)),
        h("p", { class: "close" }, finger ? card.close.touch : card.close.keys));
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
        ...items, h("p", { class: "close" }, finger ? "Tap outside the card to close" : "Press any key to close"));
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
    const finger = touch();
    box.replaceChildren(h("div", { class: "stack" }, h("h1", {}, m.title),
      ...(m.body ? m.body.split("\n\n").map((t) => h("p", {}, t)) : []),
      h("div", { class: "grid" },
        h("button", { type: "button", class: `btn ${m.tone || "amber"}`, onclick: onConfirm }, m.confirm,
          finger ? null : h("span", { class: "key" }, " (Y/Enter)")),
        h("button", { type: "button", class: "btn green", onclick: onCancel }, "Cancel",
          finger ? null : h("span", { class: "key" }, " (N/Esc)")))));
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
    const second = over.next_stage ? button(over.next_label || "Next stage", "N", "teal", "next_stage")
      : over.choose_stage ? button("Choose a stage", "N", "teal", "run_list")
        : over.in_stage ? null : button("New map", "N", "green", "restart");
    const first = over.try_again ? button("Try again", "T", "amber", "run_retry")
      : over.retry === false ? null : button("Retry", "T", "amber", "retry");
    // One two-column grid, so the share pair lines up under the rest: each
    // group starts a row, and a group's odd last button sits centred.
    const group = (buttons, cls) => buttons.filter(Boolean).map((b, i, all) => {
      if (cls) b.classList.add(cls);
      if (i === 0) b.classList.add("row-start");
      if (all.length % 2 && i === all.length - 1) b.classList.add("odd");
      return b;
    });
    const buttons = group([first, second, button("Setup menu", "M", "blue", "menu"),
      button("History & coach", "H", "violet", "toggle_history")]);
    if (over.share) {
      buttons.push(...group([button(over.share[0], over.share[1], "teal", "share"),
        over.board ? button(over.board[0], over.board[1], "gold", "leaderboard") : null], "share"));
    }
    // Where the run may go more than one way, its graph: a tap picks the
    // stage Next stage plays, a second tap plays it (`play.over_pick`).
    const old = veil.querySelector(".tgraph");
    const scroll = old ? [old.scrollLeft, old.scrollTop] : null;
    const box = over.run_graph ? h("div", { class: "tgraph vgraph" }) : null;
    const kids = [
      h("h1", { style: `color:${this.color(over.headline[1])}` }, over.headline[0]),
      ...over.lines.map(([kind, text, role]) => h("p", { class: kind, style: `color:${this.color(role)}` }, text)),
      box,
      h("div", { class: "grid" }, ...buttons),
    ].filter(Boolean);
    if (over.share && over.notes) kids.push(h("p", { class: "small dim" }, over.notes));
    veil.replaceChildren(h("div", { class: "stack" }, ...kids));
    veil.hidden = false;
    if (box) {
      const graph = drawGraph(box, over.run_graph, { onPress: (key) => this.on.action(key), label: "Where the run goes next" });
      if (scroll) [box.scrollLeft, box.scrollTop] = scroll;
      if (over.run_graph.focus) graph.show(over.run_graph.focus, scroll ? undefined : { block: "nearest", inline: "center" });
    }
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
      // Two rows on a portrait phone (the track above the buttons), one elsewhere.
      nav.replaceChildren(h("div", { class: "scrub-track" }, ui.prev, ui.track, ui.next, ui.label),
        h("div", { class: "scrub-buttons" }, ui.exit, ui.play, ui.graphs, h("span", { class: "spacer" }), ui.rewind));
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
    const upright = portrait();
    ui.label.textContent = upright ? hv.label_short : hv.label;
    ui.rewind.textContent = upright ? "Rewind" : "Rewind to here";
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
    const finger = touch();
    box.classList.toggle("full", g.full);
    const tabs = g.tabs.map((label, i) => h("button", { type: "button", class: `btn ${i === g.tab ? "active" : "blue"}`,
      onclick: () => this.on.call("graph_tab", [i]) }, label));
    const head = h("div", { class: "head" }, ...tabs, h("span", { class: "note" }, finger ? g.note.touch : g.note.keys),
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
      const k = r.width / (plot.clientWidth || r.width);
      const t = Math.round((((event.clientX - r.left) / k - left) / pw) * g.turns);
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
    const shape = [pop.src, pop.dest, pop.forward, Boolean(pop.forecast), compact(),
      pop.split ? pop.split.lanes.map((l) => l[0]).join("+") : ""].join();
    if (this.pop?.shape !== shape) this.pop = this.buildPopup(box, view, shape);
    const ui = this.pop;
    if (pop.split) {
      ui.ships.textContent = `${pop.garrison}sh`;
      ui.ships.style.color = this.color(view.human);
    } else {
      ui.ships.textContent = pop.dest_ships === null ? "?sh" : `${pop.dest_ships}sh`;
      ui.ships.style.color = pop.dest_ships === null ? "var(--fog)" : this.color(pop.dest_owner);
    }
    ui.effect.textContent = pop.effect;
    box.classList.toggle("docked", compact());
    box.hidden = false;
    // the title and the forecast in the longest wording that fits the popup
    const inner = box.clientWidth - 18;
    ui.title.textContent = fit(pop.split ? pop.splits : pop.route, inner - textWidth(ui.ships.textContent) - 5);
    if (pop.forecast) {
      ui.forecast.textContent = fit(pop.forecast.forms, inner - (pop.forecast.odds ? 17 : 0));
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
    return box;
  }

  buildPopup(box, view, shape) {
    const pop = view.popup;
    const call = (method, ...args) => () => this.on.call(method, args);
    box.className = [pop.forward ? "forward" : "", pop.split ? "split" : ""].join(" ").trim();
    const ui = { shape, dragging: false, range: [0, 0] };
    const step = (label, onclick) => h("button", { type: "button", class: "step", "aria-label": label, onclick },
      label === "Fewer" ? "−" : "+");
    ui.ships = h("span");
    ui.title = h("span");
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
    // Docked on a phone, the popup is a panel rather than a card on the map, and
    // closing it by tapping the map isn't something a panel says: Done says it.
    const docked = compact();
    const discardRow = docked
      ? h("div", { class: "pair" }, ui.discard, h("button", { type: "button", class: "pb done", onclick: () => this.on.done() },
        "Done"))
      : h("div", { class: "pair" }, ui.discard);
    const laneRow = docked && !pop.split && pop.lane
      ? h("div", { class: "row dim" }, `${pop.lane[0]} ly  ·  ${pop.lane[1]} turns`) : null;
    const kids = [
      h("div", { class: "row tabs" },
        h("button", { type: "button", class: `tab${pop.forward ? "" : " on"}`, onclick: call("set_forward_mode", false) },
          "Send"),
        h("button", { type: "button", class: `tab${pop.forward ? " on" : ""}`, onclick: call("set_forward_mode", true) },
          "Forward")),
      h("div", { class: "row title" }, ui.title, ui.ships),
    ];
    if (laneRow) kids.push(laneRow);
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
        discardRow);
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
      discardRow,
    );
    box.replaceChildren(...kids);
    return ui;
  }
}
