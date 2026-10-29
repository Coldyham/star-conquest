// Star Conquest service worker.
//
// Two jobs: (1) make the site an installable PWA (a fetch handler is part of the
// installability bar), and (2) let it load instantly and work offline once cached.
//
// Strategy is stale-while-revalidate for same-origin GETs: serve the cached copy
// immediately (fast, offline-capable) while fetching a fresh copy in the
// background for next time. That matters here because the app archives
// (starconquest.tar.gz / .apk) keep the same filename across rebuilds — a plain
// cache-first SW would pin users to a stale build after a redeploy; SWR heals on
// the next load. Range requests and cross-origin requests pass straight through.
//
// Two same-origin exceptions, since the leaderboard is served from this site:
// - /api/ is never touched. Play-by-post polls `?action=state|list` with plain
//   GETs, and a poll answered from cache would hand back the previous turn.
// - /board/ is network-first, falling back to the cache only offline. Its pages
//   are thin shells over live data, and a single stale load after a deploy could
//   pair old JS with a new API or rules version.
//
// One cross-origin prefix is cached too: pygbag's runtime (CPython and pygame
// compiled to WebAssembly, several megabytes) loads from the pygame-web CDN under
// a versioned path. Without this, a returning player re-checks every runtime file
// with the CDN before the game can start, and nothing loads offline. It is the
// same stale-while-revalidate, keeping only CORS responses the page can read.
//
// It is served from the site root, not /game/ beside the game, so its scope stays
// "/" — the scope every install from before the game moved to /game/ registered —
// and it keeps covering the board. v3 drops the v2 cache, whose "/" was the game.

const CACHE = "starconquest-v3";
const RUNTIME = "https://pygame-web.github.io/cdn/";

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

  // Only handle GETs, same-origin or pygbag's runtime; leave range requests and
  // everything else alone.
  if (req.method !== "GET" || req.headers.has("range")) return;
  const sameOrigin = url.origin === self.location.origin;
  if (!sameOrigin && !url.href.startsWith(RUNTIME)) return;

  if (sameOrigin && url.pathname.startsWith("/api/")) return;

  if (sameOrigin && url.pathname.startsWith("/board/")) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE);
        try {
          const res = await fetch(req);
          if (res && res.ok && res.type === "basic") cache.put(req, res.clone());
          return res;
        } catch {
          return (await cache.match(req)) || new Response("Offline", { status: 503 });
        }
      })()
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(req);

      const network = fetch(req)
        .then((res) => {
          if (res && res.ok && (res.type === "basic" || res.type === "cors")) {
            cache.put(req, res.clone());
          }
          return res;
        })
        .catch(() => null);

      return cached || (await network) || new Response("Offline", { status: 503 });
    })()
  );
});
