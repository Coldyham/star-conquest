// The play screen: the session in the worker, the board, and the HUD.
//
// Python owns every rule and every word: a press is hit-tested here and sent
// as a system id, the orders under it, a drag's two ends or a key's name
// (`starconquest.play`), and the reply's `view` is drawn as it stands. What
// is only about looking stays here: the camera, the pointer's hover and drag,
// and the film's clock. A setup comes from the address until the menu is
// built: ?players=3&nodes=30&seed=7&mode=symmetric&fog=1 (sight in hops).

import { BoardView } from "./board.mjs";
import { BUTTON_STEP, WHEEL_STEP } from "./camera.mjs";
import { Core, pageInfo } from "./core.mjs";
import { Hud } from "./hud.mjs";
import { fromBoard, label, Reel, travel as travelAt } from "./reel.mjs";

const $ = (id) => document.getElementById(id);
const DRAG_THRESHOLD = 8;   // config.DRAG_THRESHOLD
const THINKING_MS = 150;    // a turn slower than this says so

const core = new Core(new URL("../worker.mjs", import.meta.url));
const params = new URLSearchParams(location.search);

const game = {
  map: null, board: null, view: null, board_view: null, hud: null, seed: null,
  hover: null, drag: null, pan: null, film: null, busy: false,
};

function status(text, error = false) {
  $("status").textContent = text;
  $("status").className = error ? "error" : "";
}

function setup() {
  const settings = {};
  for (const key of ["players", "nodes"]) if (params.has(key)) settings[key] = Number(params.get(key));
  if (params.has("mode")) settings.mode = params.get("mode");
  if (params.has("fog")) settings.fog_sight = Number(params.get("fog"));
  if (params.has("ai")) settings.ai_strategy = params.get("ai").split(",");
  return settings;
}

/** What the board draws through: the view, fog, and the pointer. A film
 * draws the union of what was seen before and after (`Ui.sees`). */
function look(view, before = null) {
  const visible = new Set(view.visible);
  if (before) for (const id of before.visible) visible.add(id);
  const seen = new Set(view.seen);
  if (before) for (const id of before.seen) seen.add(id);
  return { view, sees: (id) => visible.has(id), seen, hover: game.hover, drag: game.drag };
}

function redraw() {
  if (!game.board_view || game.film) return;
  game.board_view.draw(game.board, 1, look(game.view));
  const box = game.hud.popup(game.view);
  if (box) place(box);
}

/** Put the popup beside its lane, on whichever side covers the fewest systems
 * (`render._popup_anchor`). */
function place(box) {
  const pop = game.view.popup;
  const bv = game.board_view;
  const [ax, ay] = bv.pos.get(pop.src);
  const [bx, by] = bv.pos.get(pop.dest);
  const mx = (ax + bx) / 2, my = (ay + by) / 2;
  const w = box.offsetWidth, hgt = box.offsetHeight;
  const [, , vw, vh] = bv.camera.screen;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const m = 14;
  const candidates = [[mx - w / 2, my - hgt - m], [mx + m, my - hgt / 2], [mx - w - m, my - hgt / 2], [mx - w / 2, my + m]];
  let best = null, bestScore = Infinity;
  for (const [cx, cy] of candidates) {
    const x = clamp(cx, 0, vw - w), y = clamp(cy, 2, vh - hgt);
    let score = 0;
    for (const [id, [px, py]] of bv.pos) {
      const r = bv.radius.get(id) + 6;
      if (px >= x - r && px <= x + w + r && py >= y - r && py <= y + hgt + r) score += 1;
    }
    if (score < bestScore) {
      best = [x, y];
      bestScore = score;
      if (score === 0) break;
    }
  }
  box.style.left = `${best[0]}px`;
  box.style.top = `${best[1]}px`;
}

function show(view) {
  game.view = view;
  game.hud.draw(game.board.turn, view);
  redraw();
}

/** Send one command whose reply carries a fresh view, and draw it. */
async function act(op, args = {}) {
  try {
    const res = await core.call(op, args);
    if (res.view) show(res.view);
    return res;
  } catch (err) {
    status(`${op} failed: ${err.message}`, true);
    console.error(err.trace || err);
    return null;
  }
}

async function newGame(seed = null) {
  game.film = null;
  const res = await core.call("game.start", { settings: setup(), seed });
  game.seed = res.seed;
  game.map = res.static;
  game.board = fromBoard(res.board);
  game.board_view = new BoardView($("map"), game.map);
  game.board_view.setTravel(res.travel);
  game.hud = new Hud(game.map, res.legends, {
    call: (method, args) => act("ui.call", { method, args }),
    footer,
    odds: () => {},
    lore: () => {},
  });
  status("");
  show(res.view);
}

/** End the turn, and play its film onto the board the page already has. */
async function endTurn() {
  if (game.busy || !game.view?.can.end_turn) return;
  game.busy = true;
  const thinking = setTimeout(() => { $("caption").textContent = "Thinking…"; }, THINKING_MS);
  try {
    const before = game.view;
    const res = await core.call("turn.end");
    clearTimeout(thinking);
    $("caption").textContent = "";
    if (res.film) await playFilm(res.film, res.view, before);
    game.board = fromBoard(res.board);
    game.board_view.setTravel(res.travel);
    if (res.snap) {
      game.board_view.camera.reset();
      game.board_view.layout();
    }
    show(res.view);
  } catch (err) {
    clearTimeout(thinking);
    status(`End turn failed: ${err.message}`, true);
    console.error(err.trace || err);
  } finally {
    game.busy = false;
  }
}

/** Walk `film` on the page's board at the wall clock; any press skips to its end. */
function playFilm(film, view, before) {
  return new Promise((resolve) => {
    const reel = new Reel(game.board, film);
    let start = null;
    game.film = { skip: false };
    game.hud.popup(view);
    const frame = (now) => {
      start ??= now;
      const ms = game.film?.skip ? film.total : now - start;
      reel.runTo(ms);
      game.board_view.draw(reel.board, travelAt(film, ms), look(view, before));
      $("caption").textContent = label(film, ms);
      if (ms >= film.total) {
        $("caption").textContent = "";
        game.film = null;
        resolve();
      } else {
        requestAnimationFrame(frame);
      }
    };
    requestAnimationFrame(frame);
  });
}

function act_on(action) {
  switch (action) {
    case null: case undefined: return;
    case "end_turn": endTurn(); return;
    case "reset_view":
      game.board_view.camera.reset();
      game.board_view.layout();
      redraw();
      return;
    case "restart": newGame(); return;
    case "retry": newGame(game.seed); return;
    default: status(`${action.replace(/_/g, " ")}: not in this build yet`);
  }
}

/** A footer button, by the `Ui` attribute pygame keeps its hit-rect in. */
function footer(attr) {
  const actions = {
    menu_button_rect: "menu", retry_live_button_rect: "retry", restart_live_button_rect: "restart",
    undo_button_rect: "undo", history_button_rect: "toggle_history", route_button_rect: "toggle_route",
    hint_button_rect: "hint", autoplay_button_rect: "toggle_autoplay", play_pause_rect: "toggle_play",
    fast_forward_rect: "toggle_fast_forward",
  };
  if (attr === "clear_button_rect") act("ui.call", { method: "clear_selected" });
  else if (attr === "hint_apply_rect") act("ui.call", { method: "apply_hint" });
  else act_on(actions[attr]);
}

// --- the pointer on the map ---------------------------------------------- //

function local(event) {
  const r = $("map").getBoundingClientRect();
  return [event.clientX - r.left, event.clientY - r.top];
}

async function press(event) {
  if (event.button === 2) return;
  if (game.film) {
    game.film.skip = true;
    return;
  }
  if (!game.view) return;
  try { $("map").setPointerCapture(event.pointerId); } catch { /* a synthetic press */ }
  const [x, y] = local(event);
  const bv = game.board_view;
  const sid = bv.pick(x, y);
  game.drag = null;
  game.pan = null;
  if (sid !== null) {
    const res = await act("ui.tap", { sid, shift: event.shiftKey });
    const v = res?.view;
    if (v && v.mode === "selected" && v.selected === sid && game.board.sys.get(sid).owner === v.human) {
      game.drag = { src: sid, x0: x, y0: y, x, y, active: false };
    }
    return;
  }
  const hits = bv.laneHits(x, y, game.view);
  if (hits.length) {
    act("ui.lane", { hits });
    return;
  }
  game.pan = { x, y };
  act("ui.empty");
}

function move(event) {
  if (!game.board_view || game.film) return;
  const [x, y] = local(event);
  const bv = game.board_view;
  if (game.drag && event.buttons & 1) {
    if (Math.hypot(x - game.drag.x0, y - game.drag.y0) > DRAG_THRESHOLD) game.drag.active = true;
    Object.assign(game.drag, { x, y });
    game.hover = bv.pick(x, y);
    redraw();
    return;
  }
  if (game.pan && event.buttons & 1) {
    bv.camera.pan(x - game.pan.x, y - game.pan.y);
    game.pan = { x, y };
    bv.layout();
    redraw();
    return;
  }
  const sid = bv.pick(x, y);
  if (sid !== game.hover) {
    game.hover = sid;
    act("ui.hover", { sid });
  }
}

function release(event) {
  const drag = game.drag;
  game.drag = null;
  game.pan = null;
  if (drag?.active) {
    const [x, y] = local(event);
    act("ui.drag", { src: drag.src, dest: game.board_view.pick(x, y), shift: event.shiftKey });
  } else if (drag) {
    redraw();
  }
}

function wheel(event) {
  event.preventDefault();
  if (!game.board_view || !game.view) return;
  const notch = event.deltaY < 0 ? 1 : -1;
  if (game.view.mode === "choosing") {
    act("ui.call", { method: "step_count", args: [notch] });
    return;
  }
  const [x, y] = local(event);
  game.board_view.camera.zoomAt(x, y, WHEEL_STEP ** notch);
  game.board_view.layout();
  redraw();
}

function zoomButton(factor) {
  const bv = game.board_view;
  if (!bv) return;
  const [, , w, hgt] = bv.camera.screen;
  bv.camera.zoomAt(w / 2, hgt / 2, factor);
  bv.layout();
  redraw();
}

async function key(event) {
  if (!game.view || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target instanceof HTMLInputElement) return;
  if (game.film) {
    game.film.skip = true;
    event.preventDefault();
    return;
  }
  const name = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (name === " " || name === "Enter") event.preventDefault();
  const res = await act("ui.key", { name, shift: event.shiftKey });
  act_on(res?.action);
}

async function boot() {
  try {
    await core.ready;
  } catch (err) {
    status(`Python failed to load: ${err.message}`, true);
    throw err;
  }
  status("Starting…");
  await core.call("boot", pageInfo());
  const seed = params.has("seed") ? Number(params.get("seed")) : null;
  await newGame(seed);
  const map = $("map");
  map.addEventListener("pointerdown", press);
  map.addEventListener("pointermove", move);
  map.addEventListener("pointerup", release);
  map.addEventListener("pointercancel", release);
  map.addEventListener("wheel", wheel, { passive: false });
  map.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    act("ui.empty");
  });
  map.addEventListener("pointerleave", () => {
    if (game.hover !== null && !game.drag) {
      game.hover = null;
      act("ui.hover", { sid: null });
    }
  });
  document.addEventListener("keydown", key);
  $("end-turn").addEventListener("click", endTurn);
  $("reset-view").addEventListener("click", () => act_on("reset_view"));
  $("zoom-in").addEventListener("click", () => zoomButton(BUTTON_STEP));
  $("zoom-out").addEventListener("click", () => zoomButton(1 / BUTTON_STEP));
  new ResizeObserver(() => {
    if (!game.board_view) return;
    game.board_view.resize();
    redraw();
    if (game.view) game.hud.top(game.board.turn, game.view);
  }).observe(map);
}

// For headless runs (`tools/chrome.mjs --eval`): the page's state and its two verbs.
window.scNext = { game, act, endTurn };

boot();
