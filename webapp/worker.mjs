// The game's Python, in a Web Worker: Pyodide plus the game zip, and nothing
// else. Every message is one command for `starconquest.session.Session.handle`
// (a JSON string in, a JSON string out), answered in order; Python does no I/O
// of its own, so the page carries out each reply's effects.

import { GAME_ZIP, PYODIDE } from "./js/build.mjs";

const started = performance.now();

const handle = (async () => {
  const base = new URL(`./pyodide/${PYODIDE}/`, import.meta.url).href;
  const { loadPyodide } = await import(`${base}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: base });
  const booted = performance.now();
  const zip = await (await fetch(new URL(GAME_ZIP, import.meta.url))).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/game" });
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
};
