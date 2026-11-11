// The game's Python, in a Web Worker: Pyodide plus the game zip, and nothing
// else. Every message is one command for `starconquest.session.Session.handle`
// (a JSON string in, a JSON string out), answered in order; Python does no I/O
// of its own, so the page carries out each reply's effects. The one exception
// is the games folder, where every match's log is kept as it is played
// (`replay.GAMES_DIR`): it lives in IndexedDB, so the menu can offer to resume
// an unfinished match after a reload, and is written back shortly after a
// command changes it. The map maker's maps folder (`paths.maps_dir`) is kept
// the same way, so a map saved there opens again after a reload.

import { GAME_ZIP, PYODIDE } from "./js/build.mjs";

const started = performance.now();
const GAMES = "/game/games";
const MAPS = "/game/maps";
const SYNC_MS = 400;
let syncTimer = null;

function syncfs(py, populate) {
  return new Promise((resolve) => py.FS.syncfs(populate, (err) => {
    if (err) console.warn("games folder sync failed", err);
    resolve();
  }));
}

const handle = (async () => {
  const base = new URL(`./pyodide/${PYODIDE}/`, import.meta.url).href;
  const { loadPyodide } = await import(`${base}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: base });
  const booted = performance.now();
  const zip = await (await fetch(new URL(GAME_ZIP, import.meta.url))).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/game" });
  // The games folder and the map maker's maps folder (`paths.maps_dir`),
  // each kept in IndexedDB.
  for (const dir of [GAMES, MAPS]) {
    try {
      py.FS.mkdirTree(dir);
      py.FS.mount(py.FS.filesystems.IDBFS, {}, dir);
    } catch (err) {
      console.warn(`no stored ${dir} here`, err);   // still saved, for this visit only
    }
  }
  await syncfs(py, true);
  self.py = py;
  const fetched = performance.now();
  py.runPython("import sys; sys.path.insert(0, '/game')");
  const fn = py.runPython("from starconquest.session import Session; Session().handle");
  const imported = performance.now();
  postMessage({
    kind: "ready",
    timings: {
      pyodide: booted - started,
      zip: fetched - booted,
      import: imported - fetched,
      total: imported - started,
    },
    zip: GAME_ZIP,
    zipBytes: zip.byteLength,
  });
  return fn;
})();

handle.catch((err) => postMessage({ kind: "failed", error: String(err && err.stack || err) }));

onmessage = async (event) => {
  const fn = await handle;
  postMessage({ kind: "reply", reply: fn(event.data) });
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => syncfs(self.py, false), SYNC_MS);
};
