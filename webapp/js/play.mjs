// The play screen: the session in the worker, the board, and the HUD.
//
// Python owns every rule and every word: a press is hit-tested here and sent
// as a system id, the orders under it, a drag's two ends or a key's name
// (`starconquest.play`), and the reply's `view` is drawn as it stands. What
// is only about looking stays here: the camera, the pointer's hover and drag,
// and the film's clock. The page opens on the setup menu (`menu.mjs`); the
// address pre-fills it as the command line pre-fills pygame's:
// ?players=3&nodes=30&seed=7&mode=symmetric&fog=1 (sight in hops), and &go
// skips the menu and plays it. ?input=touch lays it out for a finger
// (`hud.fitLayout`).

import { BoardView } from "./board.mjs";
import { BUTTON_STEP, WHEEL_STEP } from "./camera.mjs";
import { Core, pageInfo } from "./core.mjs";
import { compact, fitLayout, Hud, touch } from "./hud.mjs";
import { MapMaker } from "./maker.mjs";
import { FLASH_MS, Marks, readMarks } from "./marks.mjs";
import { SetupMenu } from "./menu.mjs";
import { fromBoard, label, Reel, travel as travelAt } from "./reel.mjs";

const $ = (id) => document.getElementById(id);
const DRAG_THRESHOLD = 8;   // config.DRAG_THRESHOLD
const THINKING_MS = 150;    // a turn slower than this says so
const TICK_MS = 1000;       // the session's clock (`Session.op_tick`): the campaign's lookup and its grace

const core = new Core(new URL("../worker.mjs", import.meta.url));
// The site's service worker (scope "/", `tools/pwa/sw.js`), which gives /next/
// its own rules; never from the local server, which has none to serve.
if ("serviceWorker" in navigator && !["127.0.0.1", "localhost"].includes(location.hostname)) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
const params = new URLSearchParams(location.search);

const game = {
  map: null, board: null, view: null, board_view: null, hud: null, seed: null,
  hover: null, drag: null, pan: null, box: null, film: null, fingers: new Map(), pinch: null, pressed: false, held: new Marks(), busy: false, looping: false, marks: new Marks(),
  queued: [],   // game actions pressed while a turn was resolving or filming (`actOn`)
  stale: false,   // a pushed view was set aside while a turn was in flight (`pushed`)
};

function status(text, error = false) {
  $("status").textContent = text;
  $("status").className = error ? "error" : "";
  if (!game.ready && text) {   // the loading screen is still up: say it there
    $("loading-status").textContent = text;
    $("loading-status").className = error ? "error" : "";
  }
}

/** The setup the address asks for, as `Settings` fields: the menu's pre-fill. */
function setup() {
  const settings = {};
  for (const key of ["players", "nodes", "seed"]) if (params.has(key)) settings[key] = Number(params.get(key));
  if (params.has("mode")) settings.mode = params.get("mode");
  if (params.has("fog")) settings.fog_sight = Number(params.get("fog"));
  if (params.has("ai")) settings.ai_strategy = params.get("ai").split(",");
  if (params.has("autoplay")) settings.autoplay = true;
  return settings;
}

const menu = new SetupMenu($("setup"), core, {
  start: () => newGame(),
  quit: () => {},   // the session asked the browser to close the window (the `close` effect)
  resumed: (res) => {
    menu.hide();
    game.film = null;
    game.legends = res.legends;
    adopt(res);
    clock(game.view.step_ms);
  },
  maker: (view) => {
    menu.hide();
    maker.open(view);
  },
});

/** The map maker (`maker.mjs`): Play starts its map, Menu goes back to the setup. */
const maker = new MapMaker($("maker"), core, {
  play: (res) => {
    game.film = null;
    game.map = null;   // a new map: the camera starts fitted
    game.legends = res.legends;
    adopt(res);
    status("");
    clock(game.view.step_ms);
  },
  back: (res) => {
    menu.root.hidden = false;
    menu.draw(res);
  },
});

/** Is a screen other than the play screen up (the menu, the map maker)? */
function away() {
  return menu.shown || maker.shown;
}

/** What an answer from the board changed, drawn unasked (`Session.op_net_reply`):
 * a fetched replay opens in history, one that would not open says why on the
 * menu, and anything else redraws the play screen, once no turn is in flight. */
function pushed(p) {
  if (p.game) {
    maker.hide();
    // A match opened (or a replay fetched) draws its map afresh; a shared
    // match rebuilt from its log (`map_fresh` false) keeps the camera.
    menu.hide();
    game.film = null;
    if (p.game.map_fresh !== false) game.map = null;
    game.legends = p.game.legends;
    adopt(p.game);
    status("");
  } else if (p.turn) {
    landTurn(p.turn);
  } else if (p.menu) {
    if (menu.shown) menu.draw(p.menu);
  } else if (p.view && game.view && !away()) {
    if (game.busy || game.film || game.seeking || game.replaying) game.stale = true;
    else show(p.view);
  }
}
core.onPush = pushed;
setInterval(() => {
  if (document.hidden || game.busy || game.film) return;
  core.call("tick").then((res) => core.push(res), () => {});
}, TICK_MS);

/** A shared match's turn, resolved by the last seat in (`Session._step_match`):
 * its film played onto the board the page has, as End Turn's is. */
async function landTurn(res) {
  if (away()) return;
  game.busy = true;
  try {
    const before = game.view;
    if (res.film && !before?.history) await playFilm(res.film, res.view, before);
    game.board = fromBoard(res.board);
    game.board_view.setTravel(res.travel);
    if (res.snap) game.board_view.rest(res.view.snap);
    show(res.view);
  } finally {
    game.busy = false;
    freshen();
    runQueued();
  }
}

/** Fetch the view a push left stale while a turn was in flight. */
function freshen() {
  if (!game.stale || game.busy || game.film) return;
  game.stale = false;
  act("game.view");
}

/** Back to the setup menu: the match is left behind, as pygame's Menu leaves it. */
async function openMenu(first = false) {
  clearTimeout(game.timer);
  game.film = null;
  game.busy = false;
  game.queued = [];
  game.map = null;   // the next board is drawn afresh, the camera fitted to it
  if (game.modal) {
    game.modal = null;
    game.hud.modal(null);
  }
  await menu.open({ first, prefill: first ? setup() : null, touch: touch(), compact: compact() });
}

/** Leave the play screen (or the map maker) for the menu reply `reply`. */
function toMenu(reply) {
  clearTimeout(game.timer);
  game.film = null;
  game.busy = false;
  game.queued = [];
  game.map = null;
  if (game.modal) {
    game.modal = null;
    game.hud.modal(null);
  }
  if (maker.shown) maker.hide();
  menu.root.hidden = false;
  menu.draw(reply);
  menu.after(reply);
}

// A link opened in an open page (pasted, or handed to an installed app) is
// read as at boot (`Session.op_page_hash`); our own address edits use
// `history.replaceState`, which fires no `hashchange`.
window.addEventListener("hashchange", async () => {
  const res = await core.call("page.hash", { hash: location.hash }).catch((err) => {
    status(`Couldn't open that link: ${err.message}`, true);
    return null;
  });
  if (res && res.menu) toMenu(res.menu);
});

/** What the board draws through: the view, fog, and the pointer. A film
 * draws the union of what was seen before and after (`Ui.sees`). */
function look(view, before = null) {
  const visible = new Set(view.visible);
  if (before) for (const id of before.visible) visible.add(id);
  const seen = new Set(view.seen);
  if (before) for (const id of before.seen) seen.add(id);
  const marks = view.history && !game.film ? game.held : game.marks;
  return { view, sees: (id) => visible.has(id), seen, hover: game.hover, drag: game.drag, marks, box: game.box };
}

function redraw() {
  if (!game.board_view || game.film) return;
  game.board_view.draw(game.board, 1, look(game.view), performance.now());
  const box = game.hud.popup(game.view);
  if (box) place(box);
  wake();
}

/** Whether the map moves with nothing playing: a rule being edited crawls
 * (`render._flow_phase`), and a film's marks keep dissolving after it. */
function moving() {
  const v = game.view;
  return game.marks.any || Boolean(v && (v.editing_rule !== null || v.route?.plan.length
    || (v.mode === "choosing" && v.popup?.forward)));
}

/** Redraw every frame while the map is moving; a film runs its own loop. */
function wake() {
  if (game.looping || !moving()) return;
  game.looping = true;
  let last = null;
  const frame = (now) => {
    if (game.film || !game.board_view) {
      game.looping = false;
      return;
    }
    game.marks.age(last === null ? 0 : now - last);
    last = now;
    game.board_view.draw(game.board, 1, look(game.view), now);
    if (moving()) requestAnimationFrame(frame);
    else game.looping = false;
  };
  requestAnimationFrame(frame);
}

/** Put the popup beside its lane, on whichever side covers the fewest systems
 * (`render._popup_anchor`). */
function place(box) {
  if (compact()) {   // docked: the stylesheet places it
    box.style.left = box.style.top = "";
    return;
  }
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

/** History's held marks for the turn on screen (`Ui.hold_marks`): read off
 * its events, settled rather than fading. */
function hold(held) {
  game.held = new Marks();
  if (!held) return;
  const visible = new Set(held.visible);
  [game.held.fights, game.held.hulls] = readMarks(held.events, (id) => visible.has(id),
    (id) => held.owners[id], FLASH_MS);
}

function show(view) {
  game.view = view;
  document.getElementById("app").classList.toggle("history", Boolean(view.history));
  document.getElementById("app").classList.toggle("graphing", Boolean(view.graph && !view.graph.full));
  game.hud.draw(game.board.turn, view);
  game.hud.veil(view);
  game.hud.overlays(view);
  redraw();
}

/** Send one command whose reply carries a fresh view, and draw it. One that
 * moves history to another turn (a coach note, an arrow key) fetches that
 * turn's board too, as the scrubber does: a view alone would draw the new
 * turn's rules and panel over the old turn's map. */
async function act(op, args = {}) {
  try {
    const was = game.view?.history?.turn;
    const res = await core.call(op, args);
    if (res.view) show(res.view);
    const now = res.view?.history?.turn;
    if (was !== undefined && now !== undefined && now !== was) seek(now);
    return res;
  } catch (err) {
    status(`${op} failed: ${err.message}`, true);
    console.error(err.trace || err);
    return null;
  }
}

/** Start the menu's setup (Start Game), the seed it names or a fresh one. */
async function newGame() {
  game.film = null;
  let res;
  try {
    res = await core.call("game.start");
  } catch (err) {
    menu.say({ text: `Couldn't start: ${err.message}`, ok: false, ms: 15000 });
    console.error(err.trace || err);
    return;
  }
  menu.hide();
  game.map = null;
  game.legends = res.legends;
  adopt(res);
  status("");
  clock(game.view.step_ms);
}

/** A whole new board from the session (a new game, a retry, an undo): rebuild
 * the map when it changed, and draw. */
function adopt(res) {
  game.seed = res.seed;
  game.marks.clear();   // they belong to a playback this jumps away from
  game.queued = [];   // ...as do presses waiting on its turn
  hold(res.held);
  const sameMap = game.map && JSON.stringify(game.map) === JSON.stringify(res.static);
  game.map = res.static;
  game.board = fromBoard(res.board);
  if (!sameMap) game.board_view = new BoardView($("map"), game.map, compact());
  game.board_view.setTravel(res.travel);
  if (!sameMap) game.board_view.rest(res.view.snap);   // `Ui.reset_view` on a fresh game
  game.hud = new Hud(game.map, game.legends, {
    call: (method, args) => act("ui.call", { method, args }),
    lane: async (dest, delta) => {
      await core.call("ui.call", { method: "focus_lane", args: [dest] });
      act("ui.call", { method: "step_share", args: [delta] });
    },
    footer,
    copy: (seat) => act("pbp.copy", { seat }),
    action: actOn,
    seek,
    done: () => act("ui.empty"),
  });
  show(res.view);
}

/** End the turn, and play its film onto the board the page already has. */
async function endTurn(byHand = true) {
  if (game.busy || away() || !game.view?.can.end_turn || (byHand && game.view.autoplay)) return;
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
    if (res.snap) game.board_view.rest(res.view.snap);
    show(res.view);
    game.busy = false;
    freshen();
    if (!runQueued()) clock(res.film ? 0 : res.view.step_ms);
  } catch (err) {
    clearTimeout(thinking);
    status(`End turn failed: ${err.message}`, true);
    console.error(err.trace || err);
  } finally {
    game.busy = false;
    runQueued();
  }
}

/** Is Pause waiting on the turn in flight? Its film stands still meanwhile, as
 * pygame's does (`app.apply_toggle_play`); pressing Play again lets it run on. */
function pausing() {
  return Boolean(game.view?.playing && !game.view.history && game.queued.includes("toggle_play"));
}

/** A game action pressed while a turn is resolving or playing its film is
 * kept, not dropped, and carried out the moment the turn lands, before the
 * next one can start: Pause and Take control must always take. A second press
 * of the same toggle takes it back. Any but Pause skips the film to its end. */
function queue(action) {
  const at = game.queued.indexOf(action);
  if (at >= 0 && action.startsWith("toggle_")) game.queued.splice(at, 1);
  else if (at < 0) game.queued.push(action);
  if (game.film && game.queued.some((a) => a !== "toggle_play")) game.film.skip = true;
  markQueued();
}

/** Outline the footer buttons whose press is waiting on the turn. */
function markQueued() {
  for (const b of document.querySelectorAll("#footer .btn[data-attr]")) {
    b.classList.toggle("queued", game.queued.includes(FOOTER_ACTIONS[b.dataset.attr]));
  }
  // a waiting Pause already looks paused: the colour Play is drawn in when stopped
  const pp = document.querySelector('#footer .btn[data-attr="play_pause_rect"]');
  if (pp && game.view?.playing) {
    pp.classList.toggle("active", !pausing());
    pp.classList.toggle("blue", pausing());
  }
}

/** Carry out what was pressed while the turn was in flight; true if anything was. */
function runQueued() {
  const todo = game.queued;
  if (!todo.length) return false;
  game.queued = [];
  markQueued();
  (async () => {
    for (const action of todo) await actOn(action);
  })();
  return true;
}

/** Autoplay's and Play's clock: the next turn `ms` from now (straight on
 * after a film, so a run of turns glides without a stop), while either is on
 * and the game is undecided. */
function clock(ms) {
  clearTimeout(game.timer);
  const v = game.view;
  if (!v || away() || !(v.autoplay || v.playing) || v.over || v.mode === "routing") return;
  game.timer = setTimeout(() => endTurn(false), ms);
}

/** Walk `film` on the page's board at the wall clock; any press skips to its end. */
function playFilm(film, view, before) {
  return new Promise((resolve) => {
    const reel = new Reel(game.board, film);
    const seeing = look(view, before);
    let start = null, last = null, still = 0;
    game.film = { skip: game.queued.some((a) => a !== "toggle_play") };
    game.hud.popup(view);
    // A shared match's waiting veil ("Resolving the turn...") is lifted for
    // the film, so the turn's moves are seen; the view after it says what next.
    if (game.view?.pbp?.overlay?.kind === "waiting") game.hud.pbp({});
    game.marks.archive(reel.board, reel.runTo(0), seeing.sees);   // `app._primed`
    const frame = (now) => {
      start ??= now;
      if (pausing() && !game.film?.skip) still += now - (last ?? now);   // held while Pause waits
      game.marks.age(last === null ? 0 : now - last);
      last = now;
      const ms = game.film?.skip ? film.total : Math.min(now - start - still, film.total);
      game.marks.archive(reel.board, reel.runTo(ms), seeing.sees);
      game.board_view.draw(reel.board, travelAt(film, ms), seeing, now);
      $("caption").textContent = label(film, ms);
      if (ms >= film.total) {
        $("caption").textContent = "";
        game.film = null;
        resolve();
        wake();
      } else {
        requestAnimationFrame(frame);
      }
    };
    requestAnimationFrame(frame);
  });
}

const GAME_ACTIONS = new Set(["toggle_play", "toggle_autoplay", "toggle_fast_forward", "toggle_route", "restart", "retry",
  "undo", "hint", "take_hint", "toggle_history", "rewind", "rewind_confirmed", "quit", "quit_confirmed", "share", "leaderboard", "next_stage"]);

async function actOn(action) {
  if (action === null || action === undefined) return;
  if (action === "end_turn") {
    endTurn();
  } else if (action === "menu") {
    openMenu();
  } else if (action === "reset_view") {
    game.board_view.rest(game.view.snap);
    redraw();
  } else if (GAME_ACTIONS.has(action)) {
    if (game.busy) {
      queue(action);
      return;
    }
    clearTimeout(game.timer);
    const res = await core.call("game.action", { name: action }).catch((err) => {
      status(`${action} failed: ${err.message}`, true);
      return null;
    });
    if (!res) return;
    if (res.quit) {   // the browser kept the window open: back to the menu, saying how to leave
      toMenu(res.menu);
      return;
    }
    if (res.board) adopt(res);
    else show(res.view);
    if (res.modal) ask(res.modal);
    if (game.view.history) replay();
    else clock(game.view.step_ms);
  } else {
    status(`${action.replace(/_/g, " ")}: not in this build yet`);
  }
}

/** A modal ask (the first hint of a game, a mid-game rewind): confirming
 * runs the action it names. */
function ask(m) {
  game.modal = m;
  const close = () => {
    game.modal = null;
    game.hud.modal(null);
  };
  game.hud.modal(m, () => {
    close();
    actOn(m.then || "take_hint");
  }, close);
}

/** Jump history to `turn`. */
async function seek(turn) {
  if (game.seeking) {
    game.seekTo = turn;
    return;
  }
  game.seeking = true;
  try {
    const res = await core.call("history.seek", { turn });
    game.board = fromBoard(res.board);
    game.board_view.setTravel(res.travel);
    game.marks.clear();
    hold(res.held);
    show(res.view);
  } finally {
    game.seeking = false;
    freshen();
  }
  if (game.seekTo !== undefined && game.seekTo !== null) {
    const next = game.seekTo;
    game.seekTo = null;
    if (next !== game.view.history?.turn) seek(next);
  }
}

/** History's playback: one turn after another, each film straight into the
 * next, a quiet turn after `PLAY_MS`, until the last turn or a pause. */
async function replay() {
  if (game.replaying) return;
  game.replaying = true;
  try {
    while (game.view?.history && game.view.playing) {
      const res = await core.call("history.step");
      if (res.film) await playFilm(res.film, res.view, res.before);
      else if (res.view.playing) await new Promise((r) => setTimeout(r, 350));
      game.board = fromBoard(res.board);
      game.board_view.setTravel(res.travel);
      hold(res.held);
      show(res.view);
    }
  } finally {
    game.replaying = false;
    freshen();
  }
}

/** The game action behind each footer button, by the `Ui` attribute pygame
 * keeps its hit-rect in. */
const FOOTER_ACTIONS = {
  menu_button_rect: "menu", retry_live_button_rect: "retry", restart_live_button_rect: "restart",
  undo_button_rect: "undo", history_button_rect: "toggle_history", route_button_rect: "toggle_route",
  hint_button_rect: "hint", autoplay_button_rect: "toggle_autoplay", play_pause_rect: "toggle_play",
  fast_forward_rect: "toggle_fast_forward",
};

/** A footer button, by its `Ui` attribute. */
function footer(attr) {
  const route = { route_mode_rect: ["set_route_rally", [!game.view.route?.rally]], route_auto_rect: ["auto_rally", []],
    route_cancel_rect: ["reset_route", []] };
  if (route[attr]) act("ui.call", { method: route[attr][0], args: route[attr][1] });
  else if (attr === "clear_button_rect") act("ui.call", { method: "clear_selected" });
  else if (attr === "hint_apply_rect") act("ui.call", { method: "apply_hint" });
  else actOn(FOOTER_ACTIONS[attr]);
}

// --- the pointer on the map ---------------------------------------------- //

/** A pointer event's position on the map, in layout units. */
function local(event) {
  const map = $("map");
  const r = map.getBoundingClientRect();
  const k = r.width / (map.clientWidth || r.width);
  return [(event.clientX - r.left) / k, (event.clientY - r.top) / k];
}

/** The two fingers on the map: how far apart, and their midpoint. */
function fingerSpan() {
  const [[x0, y0], [x1, y1]] = game.fingers.values();
  return { spread: Math.max(1, Math.hypot(x1 - x0, y1 - y0)), mid: [(x0 + x1) / 2, (y0 + y1) / 2] };
}

/** Two fingers zoom and pan the map (`input._pinch`). The first finger's press
 * reached the session before the second landed, so a pinch starts by putting
 * back what it changed (`ui.unpress`), and the fingers do nothing else until
 * every one is up. A finger landing again picks the pinch up from there. */
function pinchStart() {
  if (!game.pinch) {
    game.drag = game.pan = game.box = null;
    if (game.pressed && game.view) act("ui.unpress");
    game.pressed = false;
  }
  game.pinch = fingerSpan();
}

function pinchMove() {
  const bv = game.board_view;
  const { spread, mid } = fingerSpan();
  bv.camera.pan(mid[0] - game.pinch.mid[0], mid[1] - game.pinch.mid[1]);
  bv.camera.zoomAt(mid[0], mid[1], spread / game.pinch.spread);
  game.pinch = { spread, mid };
  bv.layout();
  redraw();
}

async function press(event) {
  if (event.button === 2) return;
  if (event.pointerType === "touch") {
    game.fingers.set(event.pointerId, local(event));
    try { $("map").setPointerCapture(event.pointerId); } catch { /* a synthetic press */ }
    if (game.fingers.size === 2 && game.board_view) {
      pinchStart();
      return;
    }
    if (game.pinch || game.fingers.size > 2) return;
  }
  game.pressed = false;
  if (game.hud?.moreOpen) {   // any press closes the "more" list, and is spent doing it
    game.hud.moreOpen = false;
    game.hud.footer(game.view);
    return;
  }
  if (game.film) {
    game.film.skip = true;
    return;
  }
  if (!game.view) return;
  if (game.view.card) {
    act("ui.call", { method: "dismiss_card" });
    return;
  }
  try { $("map").setPointerCapture(event.pointerId); } catch { /* a synthetic press */ }
  const [x, y] = local(event);
  const bv = game.board_view;
  const sid = bv.pick(x, y);
  game.drag = null;
  game.pan = null;
  if (sid !== null) {
    game.pressed = true;
    const res = await act("ui.tap", { sid, shift: event.shiftKey });
    const v = res?.view;
    if (game.pinch || !game.pressed) return;   // a second finger landed meanwhile
    if (v && v.mode === "selected" && v.selected === sid && game.board.sys.get(sid).owner === v.human) {
      game.drag = { src: sid, x0: x, y0: y, x, y, active: false };
    }
    return;
  }
  if (game.view.mode === "routing") {
    // Empty space: a box in chain mode, a pan in rally mode, which has no box.
    // Neither clears the group: the mode confirms and cancels explicitly.
    if (game.view.route.rally) game.pan = { x, y };
    else game.box = { x0: x, y0: y, x, y, active: false };
    return;
  }
  const hits = bv.laneHits(x, y, game.view);
  game.pressed = true;
  if (hits.length) {
    act("ui.lane", { hits });
    return;
  }
  game.pan = { x, y };
  act("ui.empty");
}

function move(event) {
  if (game.fingers.has(event.pointerId)) {
    game.fingers.set(event.pointerId, local(event));
    if (game.pinch) {
      if (game.fingers.size === 2 && game.board_view) pinchMove();
      return;
    }
  }
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
  if (game.box && event.buttons & 1) {
    if (Math.hypot(x - game.box.x0, y - game.box.y0) > DRAG_THRESHOLD) game.box.active = true;
    Object.assign(game.box, { x, y });
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
  if (game.fingers.delete(event.pointerId) && game.pinch) {
    // One finger left holds the pinch still (it moves the map only with two);
    // none, and fingers tap and pan again.
    if (!game.fingers.size) game.pinch = null;
    return;
  }
  const drag = game.drag, box = game.box;
  game.drag = null;
  game.pan = null;
  game.box = null;
  if (box?.active) {
    act("ui.call", { method: "add_route_sids", args: [game.board_view.inBox(box)] });
    return;
  }
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
  if (away() || !game.view || event.ctrlKey || event.metaKey || event.altKey) return;
  if (game.modal) {
    const k = event.key.toLowerCase();
    if (k === "y" || k === "enter") $("modal").querySelector(".btn.amber").click();
    else if (k === "n" || k === "escape") $("modal").querySelector(".btn.green").click();
    event.preventDefault();
    return;
  }
  if (event.target instanceof HTMLInputElement) return;
  const name = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  // Any key skips a film, but Play/Pause (which pauses it), Autoplay (the one
  // way to stop autoplay) and the camera's own key (`input._toggles_play`,
  // `_toggles_autoplay`, `_moves_camera`).
  if (game.film && !["p", "a", "r"].includes(name)) {
    game.film.skip = true;
    event.preventDefault();
    return;
  }
  const tabbed = name === "Tab" && (game.view.mode === "routing" || game.view.history);
  if (name === " " || name === "Enter" || tabbed || (game.view.history && /^(Arrow|Home|End)/.test(name))) {
    event.preventDefault();
  }
  const res = await act("ui.key", { name, shift: event.shiftKey });
  actOn(res?.action);
}

fitLayout();
addEventListener("resize", fitLayout);

async function boot() {
  try {
    await core.ready;
  } catch (err) {
    status(`Python failed to load: ${err.message}`, true);
    throw err;
  }
  status("Starting…");
  try {
    await core.call("boot", pageInfo());
  } catch (err) {
    status(`The game failed to start: ${err.message}`, true);
    throw err;
  }
  status("");
  await openMenu(true);
  if (params.has("go")) await newGame();
  $("loading").hidden = true;
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
  $("end-turn").addEventListener("click", () => {
    if (game.view?.route) {
      if (game.view.route.confirm[1]) act("ui.call", { method: "confirm_route" });
    } else {
      endTurn();
    }
  });
  $("reset-view").addEventListener("click", () => actOn("reset_view"));
  $("zoom-in").addEventListener("click", () => zoomButton(BUTTON_STEP));
  $("zoom-out").addEventListener("click", () => zoomButton(1 / BUTTON_STEP));
  new ResizeObserver(() => {
    if (!game.board_view) return;
    game.board_view.resize();
    redraw();
    if (game.view) game.hud.top(game.board.turn, game.view);
  }).observe(map);
  addEventListener("resize", () => {
    if (menu.shown) menu.open({ touch: touch(), compact: compact() });
  });
  game.ready = true;
}

/** Put a parity screen's recipe on screen (`tools/parity_next.py`): the menu's
 * through `parity.menu`, the game's through `parity.load`. */
async function loadRecipe(recipe) {
  if (recipe.surface === "maker") {
    menu.hide();
    clearTimeout(game.timer);
    const res = await core.call("parity.maker", { recipe });
    maker.open(res.maker);
    status("");
    return;
  }
  maker.hide();
  if (recipe.surface === "menu" || recipe.surface === "resume") {
    await openMenu();
    await menu.send("parity.menu", { recipe });
    return;
  }
  menu.hide();
  game.film = null;
  game.map = null;   // a new board view: the camera starts fitted
  const res = await core.call("parity.load", { recipe });
  game.legends = res.legends;
  adopt(res);
  game.hover = res.view.hover;
  game.hud.moreOpen = res.view.more_open;
  game.hud.footer(res.view);
  redraw();
  if (res.modal) ask(res.modal);
  status("");
}

// For headless runs (`tools/chrome.mjs --eval`): the page's state and its verbs.
window.scNext = { game, core, act, endTurn, loadRecipe };

boot();
