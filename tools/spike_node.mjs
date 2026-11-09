// Run the game's Python in Pyodide under Node, for tools/spike_bench.py.
//
//   node tools/spike_node.mjs ROOT ZIP MODE
//
// Boots the pinned Pyodide from ROOT/.cache, unpacks the built game ZIP at
// /game (what the browser's worker runs), mounts the repository at /repo for
// the ledgers and tools, and runs `tools/spike_bench.py`'s in-Pyodide entry
// point for MODE ("turns" or "digests"). Python appends each finished case to
// its ledger under results/ as it goes.

import { readFileSync } from "node:fs";

const [root, zipPath, mode] = process.argv.slice(2);
const version = JSON.parse(readFileSync(`${root}/results/next-spike/plan.json`, "utf8")).pyodide;
const { loadPyodide } = await import(`${root}/.cache/pyodide/${version}/pyodide.mjs`);
const t0 = performance.now();
const py = await loadPyodide({ indexURL: `${root}/.cache/pyodide/${version}/` });
const booted = performance.now();
py.setStdout({ batched: (s) => console.log(s) });
py.setStderr({ batched: (s) => console.error(s) });
py.unpackArchive(new Uint8Array(readFileSync(zipPath)), "zip", { extractDir: "/game" });
py.FS.mkdirTree("/repo");
py.FS.mount(py.FS.filesystems.NODEFS, { root }, "/repo");
py.runPython(`
import sys
sys.path.insert(0, "/game")
import starconquest.session  # the shipped package, before /repo can shadow it
sys.path.append("/repo")
`);
const imported = performance.now();
console.log(`pyodide boot ${((booted - t0) / 1000).toFixed(2)}s, import ${((imported - booted) / 1000).toFixed(2)}s`);
py.runPython(`
from tools import spike_bench
spike_bench.in_pyodide(${JSON.stringify(mode)}, boot_s=${(booted - t0) / 1000}, import_s=${(imported - booted) / 1000})
`);
