// Record the files pygbag's runtime loads, for tools/mirror_runtime.py to mirror.
//
//   (behind a proxy: NODE_USE_ENV_PROXY=1 NODE_EXTRA_CA_CERTS=<ca bundle>)
//   ./tools/build_web.sh && node tools/record_runtime.mjs [--wait 90]
//
// Plays the built game in headless Chromium, forwards every request for the
// runtime to the real CDN, and writes the paths it saw to
// tools/pwa/runtime_files.txt (commit that file). Rerun when pygbag is upgraded.
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { dirname, extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "web");
const REMOTE = "https://pygame-web.github.io/cdn/";
const LOCAL = "http://localhost:8000/cdn/";
const wait = Number(process.argv[process.argv.indexOf("--wait") + 1]) || 90;
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".png": "image/png", ".json": "application/json" };

const srv = createServer(async (req, res) => {
  let p = normalize(decodeURIComponent(req.url.split("?")[0])).replace(/^(\.\.[/\\])+/, "");
  let f = join(WEB, p);
  if (existsSync(f) && statSync(f).isDirectory()) f = join(f, "index.html");
  if (!existsSync(f)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" });
  res.end(await readFile(f));
}).listen(8000);

const seen = new Set();
const browser = await chromium.launch();
const ctx = await browser.newContext({ serviceWorkers: "block" });
await ctx.route("**/*", async (route) => {
  const url = route.request().url();
  const prefix = [REMOTE, LOCAL].find((x) => url.startsWith(x));
  if (!prefix) return route.continue();
  const rel = url.slice(prefix.length).split("?")[0].split("#")[0];
  const resp = await fetch(REMOTE + rel);
  if (!resp.ok) return route.fulfill({ status: resp.status, body: "" });
  seen.add(rel);
  return route.fulfill({
    status: 200,
    headers: { "content-type": resp.headers.get("content-type") || "application/octet-stream",
               "access-control-allow-origin": "*" },
    body: Buffer.from(await resp.arrayBuffer()),
  });
});
const page = await ctx.newPage();
await page.goto("http://localhost:8000/play/");
console.log(`recording for ${wait}s (the game should reach its menu)...`);
await page.waitForTimeout(wait * 1000);
await page.screenshot({ path: join(ROOT, "results", "record_runtime.png") });
await browser.close();
srv.close();
if (!seen.size) { console.error("recorded nothing"); process.exit(1); }
await writeFile(join(ROOT, "tools/pwa/runtime_files.txt"),
  "# Written by tools/record_runtime.mjs; one path under the pygbag CDN per line.\n" + [...seen].sort().join("\n") + "\n");
console.log(`wrote ${seen.size} paths`);
