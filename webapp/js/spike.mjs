// The Pyodide spike's page: an all-bot game played turn by turn, each turn's
// film played onto the board the page already has and checked against the
// board Python answers. With ?bench=1 it runs the timing cases the local
// server hands it (`tools/spike_bench.py`) and posts each result back.

import { BoardView } from "./board.mjs";
import { Core, pageInfo } from "./core.mjs";
import { fromBoard, label, Reel, toBoard, travel } from "./reel.mjs";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const core = new Core(new URL("../worker.mjs", import.meta.url));

const stats = {};
let map = null, board = null, view = null;
let playing = false, busy = false;
const turnMs = [], roundTrip = [];
let mismatches = 0, turnsChecked = 0;

function setStats(entries) {
  Object.assign(stats, entries);
  const dl = $("stats");
  dl.replaceChildren();
  for (const [k, v] of Object.entries(stats)) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    if (k === "film mismatches" && v !== "0") dd.className = "bad";
    dl.append(dt, dd);
  }
}

const sec = (ms) => `${(ms / 1000).toFixed(2)} s`;
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

function status(text, error = false) {
  $("status").textContent = text;
  $("status").className = error ? "error" : "";
}

async function boot() {
  let ready;
  try {
    ready = await core.ready;
  } catch (err) {
    status(`Python failed to load: ${err.message}`, true);
    throw err;
  }
  const readyAt = performance.now();
  const booted = await core.call("boot", pageInfo());
  setStats({
    "page → Python ready": sec(readyAt),
    "Pyodide boot": sec(ready.timings.pyodide),
    "game zip": `${sec(ready.timings.zip)} (${Math.round(ready.zipBytes / 1024)} KB)`,
    "game import": sec(ready.timings.import),
    rules: `v${booted.rules}`,
  });
  return { ready, readyAt, booted };
}

async function newGame() {
  const bots = $("bots").value.split(",").map((s) => s.trim()).filter(Boolean);
  const seed = $("seed").value === "" ? null : Number($("seed").value);
  const settings = { players: bots.length, nodes: Number($("nodes").value), autoplay: true, ai_strategy: bots };
  const res = await core.call("game.start", { settings, seed });
  map = res.static;
  board = fromBoard(res.board);
  view = new BoardView($("map"), map);
  view.draw(board, 1);
  turnMs.length = 0;
  roundTrip.length = 0;
  mismatches = turnsChecked = 0;
  status(`Seed ${res.seed}: ${bots.join(" vs ")}`);
  setStats({ turn: "0", "Python ms / turn": "-", "round trip ms": "-", "film mismatches": "0" });
}

function play(film) {
  return new Promise((resolve) => {
    const reel = new Reel(board, film);
    let start = null;
    const frame = (now) => {
      start ??= now;
      const ms = now - start;
      reel.runTo(ms);
      view.draw(reel.board, travel(film, ms));
      $("caption").textContent = label(film, ms);
      if (ms >= film.total) {
        reel.run();
        $("caption").textContent = "";
        resolve(reel.board);
      } else {
        requestAnimationFrame(frame);
      }
    };
    requestAnimationFrame(frame);
  });
}

async function step() {
  if (busy || !board || board.winner !== null) return;
  busy = true;
  try {
    const t = performance.now();
    const res = await core.call("turn.end");
    roundTrip.push(performance.now() - t);
    turnMs.push(res.ms);
    if (res.film) {
      // The page's own copy of the board walks the film; it must land exactly
      // where Python's did, or the mirror has drifted from the engine.
      if ($("animate").checked) await play(res.film);
      else new Reel(board, res.film).run();
      turnsChecked += 1;
      if (JSON.stringify(toBoard(board)) !== JSON.stringify(res.board)) mismatches += 1;
    }
    board = fromBoard(res.board);
    view.draw(board, 1);
    setStats({
      turn: String(board.turn),
      "Python ms / turn": `${mean(turnMs).toFixed(1)} (max ${Math.max(...turnMs).toFixed(0)})`,
      "round trip ms": mean(roundTrip).toFixed(1),
      "film mismatches": `${mismatches}`,
      "films checked": String(turnsChecked),
    });
    if (board.winner !== null) {
      const winner = map.players.find((p) => p.id === board.winner);
      status(`${winner ? winner.name : "Seat " + board.winner} wins on turn ${board.turn}`);
      playing = false;
      $("play").textContent = "Play";
    }
  } catch (err) {
    status(`${err.message}\n${err.trace || ""}`, true);
    playing = false;
  } finally {
    busy = false;
  }
  if (playing) setTimeout(step, 0);
}

async function bench({ ready, readyAt }) {
  const post = (row) => fetch("/__bench", { method: "POST", body: JSON.stringify(row) });
  const plan = await (await fetch("/__bench")).json();
  const run = plan.run;
  await post({ kind: "startup", run, readyMs: readyAt, ...ready.timings, zipBytes: ready.zipBytes });
  for (const c of plan.cases) {
    status(`bench ${run}: ${c.bot} @${c.aux} on ${c.nodes}`);
    const r = await core.call("bench.decide", c);
    await post({ kind: "turns", run, runtime: "chrome", ...c, ms: r.ms, digest: r.digest, played: r.turns });
  }
  await post({ kind: "done", run });
  status(`bench ${run} done`);
}

const booted = await boot();
if (params.get("bench")) {
  await bench(booted);
} else {
  $("setup").addEventListener("submit", (e) => {
    e.preventDefault();
    playing = false;
    $("play").textContent = "Play";
    newGame();
  });
  $("play").addEventListener("click", () => {
    playing = !playing;
    $("play").textContent = playing ? "Pause" : "Play";
    if (playing) step();
  });
  $("step").addEventListener("click", () => step());
  await newGame();
  if (params.get("play")) $("play").click();
}
