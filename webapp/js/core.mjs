// The page's side of the session: send a command, await its reply, and carry
// out the effects Python asked for (`starconquest.hostio.EFFECTS`).

const AUTH_KEY = "sc_auth"; // paths.WEB_AUTH_KEY: the board's sign-in session
const BRIDGED = { ownName: (r) => r, quickPost: (r) => JSON.stringify(r) };

export class Core {
  constructor(workerUrl) {
    this.worker = new Worker(workerUrl, { type: "module" });
    this.next = 1;
    this.waiting = [];
    this.ready = new Promise((resolve, reject) => {
      this.worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.kind === "ready") resolve(msg);
        else if (msg.kind === "failed") reject(new Error(msg.error));
        else if (msg.kind === "reply") this.landed(JSON.parse(msg.reply));
      };
      this.worker.onerror = (event) => reject(new Error(event.message || "the worker failed to load"));
    });
  }

  /** Run `op` with `args`; resolves to its result, rejects with its error. */
  call(op, args = {}) {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      this.waiting.push({ id, resolve, reject });
      this.worker.postMessage(JSON.stringify({ id, op, args }));
    });
  }

  landed(reply) {
    const i = this.waiting.findIndex((w) => w.id === reply.id);
    const waiter = i >= 0 ? this.waiting.splice(i, 1)[0] : null;
    for (const effect of reply.effects || []) this.effect(effect);
    if (!waiter) return;
    if (reply.ok) waiter.resolve(reply.result);
    else {
      const err = new Error(reply.error);
      err.trace = reply.trace;
      waiter.reject(err);
    }
  }

  /** Hand the session an effect's answer. What it changed on screen comes
   * back as `pushed`, for `onPush` to draw. */
  answer(req, state, body) {
    this.call("net.reply", { req, state, body }).then((res) => this.push(res), () => {});
  }

  /** A reply's `pushed`, if any, to whoever draws it. */
  push(res) {
    if (res && res.pushed && this.onPush) this.onPush(res.pushed);
  }

  effect(e) {
    try {
      switch (e.t) {
        case "store.set": localStorage.setItem(e.key, e.value); break;
        case "clipboard": navigator.clipboard.writeText(e.text).catch(() => {}); break;
        case "navigate": location.assign(e.url); break;
        case "fragment": history.replaceState(null, "", `#${e.token}`); break;
        case "fire":
          fetch(e.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: e.body })
            .catch(() => {});
          break;
        case "http": this.http(e); break;
        case "bridge": this.bridge(e); break;
        default: console.warn("unknown effect", e);
      }
    } catch (err) {
      console.warn("effect failed", e.t, err);
    }
  }

  http(e) {
    const headers = { "Content-Type": "application/json" };
    if (e.signed) {
      try {
        const auth = JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
        if (auth && auth.access_token) headers.Authorization = `Bearer ${auth.access_token}`;
      } catch { /* signed out, as far as the endpoint can tell */ }
    }
    const init = e.body === null ? {} : { method: "POST", headers, body: e.body };
    fetch(e.url, init)
      .then((r) => r.text().then((text) => this.answer(e.req,
        r.ok ? "ok" : r.status === 404 ? "missing" : r.status === 403 ? "refused" : "error", text)))
      .catch((err) => {
        console.warn("call failed", err);
        this.answer(e.req, "error", "");
      });
  }

  bridge(e) {
    const shape = BRIDGED[e.fn];
    if (!shape) {
      this.answer(e.req, "error", `not a bridged call: ${e.fn}`);
      return;
    }
    import(e.module)
      .then((m) => m[e.fn](...e.args))
      .then((r) => this.answer(e.req, "ok", shape(r)))
      .catch((err) => this.answer(e.req, "error", String(err && err.message || err)));
  }
}

/** What the session's `boot` wants to know about this page. */
export function pageInfo() {
  const store = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      // Never the sign-in session: the token stays in the page (`http` adds it).
      if (key && key.startsWith("sc_") && key !== AUTH_KEY) store[key] = localStorage.getItem(key);
    }
  } catch { /* storage blocked: start with none */ }
  const { origin, hostname, pathname, hash } = location;
  return { page: { origin, hostname, pathname, hash }, store };
}
