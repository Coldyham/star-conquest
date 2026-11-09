// Drive headless Chrome over the DevTools protocol, with nothing but Node 22:
// open a URL, echo the page's console, wait for a condition, screenshot.
//
//   node tools/chrome.mjs URL --until "JS expr" [--timeout 60] [--shot out.png]
//        [--size 1400x900] [--mobile] [--profile DIR] [--eval "JS expr"]
//
// Exits 0 once `--until` is truthy (or straight after load without it), 1 on
// timeout or a page error. `--profile` keeps a browser profile between runs, so
// a second run is a warm cache; without it each run starts cold. `--eval`
// prints an expression's value (JSON) when done.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const url = args[0];
const opt = (name, fallback = null) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const until = opt("until");
const timeout = Number(opt("timeout", "60")) * 1000;
const shot = opt("shot");
const [width, height] = opt("size", "1400x900").split("x").map(Number);
const mobile = args.includes("--mobile");
const evalExpr = opt("eval");
const keep = opt("profile");
const profile = keep || mkdtempSync(join(tmpdir(), "sc-chrome-"));
const binary = process.env.CHROME || "google-chrome";

const chrome = spawn(binary, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-extensions",
  `--window-size=${width},${height}`, "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

const done = (code) => {
  chrome.kill("SIGKILL");
  // Chrome may still be writing into its profile as it dies: retry, never fail on it.
  if (!keep) {
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch { /* left in /tmp */ }
  }
  process.exit(code);
};
setTimeout(() => { console.error(`timed out after ${timeout / 1000}s`); done(1); }, timeout + 15000).unref();

const wsUrl = await new Promise((resolve, reject) => {
  let buf = "";
  chrome.stderr.on("data", (d) => {
    buf += d;
    const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
    if (m) resolve(m[1]);
  });
  chrome.on("exit", (c) => reject(new Error(`chrome exited ${c}`)));
});

const browser = new WebSocket(wsUrl);
await new Promise((r) => browser.addEventListener("open", r, { once: true }));
let nextId = 1;
const pending = new Map();
const listeners = [];
browser.addEventListener("message", (e) => {
  const msg = JSON.parse(e.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
  } else if (msg.method) {
    for (const fn of listeners) fn(msg);
  }
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = nextId++;
  pending.set(id, { resolve, reject });
  browser.send(JSON.stringify({ id, method, params, sessionId }));
});

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const page = (method, params) => send(method, params, sessionId);
let failed = false;
listeners.push((msg) => {
  if (msg.method === "Runtime.consoleAPICalled") {
    const text = msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ");
    console.log(`[console.${msg.params.type}] ${text}`);
  } else if (msg.method === "Runtime.exceptionThrown") {
    console.log(`[exception] ${msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text}`);
    failed = true;
  } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
    console.log(`[log.error] ${msg.params.entry.text} ${msg.params.entry.url || ""}`);
  }
});
await page("Runtime.enable");
await page("Log.enable");
await page("Page.enable");
await page("Emulation.setDeviceMetricsOverride", {
  width, height, deviceScaleFactor: mobile ? 2.625 : 1, mobile,
});
if (mobile) await page("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
const started = Date.now();
await page("Page.navigate", { url });

const value = async (expr) => (await page("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
let ok = !until;
while (until && Date.now() - started < timeout) {
  try {
    if (await value(`Boolean(${until})`)) { ok = true; break; }
  } catch { /* not ready to answer yet */ }
  await new Promise((r) => setTimeout(r, 250));
}
console.log(`${ok ? "reached" : "did not reach"} condition after ${((Date.now() - started) / 1000).toFixed(1)}s`);
if (evalExpr) console.log(JSON.stringify(await value(evalExpr)));
if (shot) {
  const { data } = await page("Page.captureScreenshot", { format: "png" });
  writeFileSync(shot, Buffer.from(data, "base64"));
  console.log(`screenshot ${shot}`);
}
done(ok && !failed ? 0 : 1);
