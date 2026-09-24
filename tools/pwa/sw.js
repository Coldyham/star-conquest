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

const CACHE = "starconquest-v2";

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
  if (req.method !== "GET" || url.origin !== self.location.origin || req.headers.has("range")) {
    return;
  }

  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/board/")) {
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
          if (res && res.ok && res.type === "basic") {
            cache.put(req, res.clone());
          }
          return res;
        })
        .catch(() => null);

      return cached || (await network) || new Response("Offline", { status: 503 });
    })()
  );
});
