// Star Conquest service worker.
//
// Two jobs: (1) make the site an installable PWA (a fetch handler is part of the
// installability bar), and (2) let it load instantly and work offline once cached.
//
// Each same-origin GET is answered by its path. Range requests and cross-origin
// requests pass straight through.
//
// - /api/ is never touched. Play-by-post polls `?action=state|list` with plain
//   GETs, and a poll answered from cache would hand back the previous turn.
// - /board/ is network-first, falling back to the cache only offline. Its pages
//   are thin shells over live data, and a single stale load after a deploy could
//   pair old JS with a new API or rules version.
// - /play/, the game (docs/design/html-frontend.md), splits by name. What is
//   named by its contents or a version never changes, so once cached it is
//   served from the cache, never asked for again: the game zip
//   (py/starconquest-<hash>.zip), Pyodide (pyodide/<version>/) and the fonts.
//   Everything else (the page, its JS and CSS, and js/build.mjs, which names
//   the zip) is network-first like /board/, so a deploy never boots an old page
//   against a new zip, and the cache only answers offline.
// - Anything else (the router, the manifest, the icons) is stale-while-
//   revalidate: the cached copy at once, a fresh one fetched for next time.
//
// It is served from the site root, not /play/ beside the game, so its scope stays
// "/" — the scope every install from before the game moved to /game/ registered —
// and it keeps covering the board. v3 dropped the v2 cache, whose "/" was the game;
// v4 dropped v3's, whose /game/ page loaded the runtime from the remote CDN; v5
// drops v4's, which held the pygame build's runtime and archive, several
// megabytes nothing loads any more.
//
// A page that now redirects (/game/ or /next/ to /play/) comes back from the
// network as an opaque redirect, never "ok", so it would never refresh its cache
// entry and the stale copy would win forever: a redirect deletes the entry instead.

const CACHE = "starconquest-v5";
// /play/'s files that never change under their name.
const FIXED = /^\/play\/(py\/starconquest-[0-9a-f]+\.zip$|pyodide\/[^/]+\/|fonts\/)/;

/** The network's answer, kept for offline; the cache only when it fails. */
async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res && res.ok && res.type === "basic") cache.put(req, res.clone());
    return res;
  } catch {
    return (await cache.match(req)) || new Response("Offline", { status: 503 });
  }
}

/** The cached copy if there is one, else the network's, kept. */
async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const res = await fetch(req);
    if (res && res.ok && res.type === "basic") cache.put(req, res.clone());
    return res;
  } catch {
    return new Response("Offline", { status: 503 });
  }
}

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle same-origin GETs; leave range requests and everything else alone.
  if (req.method !== "GET" || req.headers.has("range")) return;
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/board/")) {
    event.respondWith(networkFirst(req));
    return;
  }

  if (url.pathname.startsWith("/play/")) {
    event.respondWith(FIXED.test(url.pathname) ? cacheFirst(req) : networkFirst(req));
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(req);

      const network = fetch(req)
        .then((res) => {
          if (res && (res.type === "opaqueredirect" || res.redirected)) {
            cache.delete(req);
          } else if (res && res.ok && res.type === "basic") {
            cache.put(req, res.clone());
          }
          return res;
        })
        .catch(() => null);

      return cached || (await network) || new Response("Offline", { status: 503 });
    })()
  );
});
