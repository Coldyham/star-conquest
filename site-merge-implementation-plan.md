# Merge the leaderboard into the game's site (option A)

## Status
Code done on `claude/leaderboard-game-netlify-merge-jtlpb8` (which carries
`play-by-post`, so db910fa and 0d45010, in place of the `site-merge` branch
named below). Deviation: the board pages' **▶ Play** link is a static
`href="../"` rather than set from `GAME_URL` in JS: same origin, so the game is
always one level up, and that also works on a local server. Still to do, all
manual:
- [ ] Game site: add `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Scoping to Functions is a paid feature, so leave all scopes; the build command unsets the key before any build step runs.
- [ ] Game site: confirm the sensitive-variable policy is "Require approval".
- [ ] Deploy-preview checks (Verification below), including the client IP the
      rate limit sees.
- [ ] Merge, then publish the game site (publish 1 of 2).
- [ ] Old board site: switch Base directory to `legacy-board`, publish (2 of 2),
      then remove `SUPABASE_SECRET_KEY` from it.
- [ ] Afterwards: delete `leaderboard/netlify.toml` (kept only so the old site
      builds until its base dir switches), and update the deploy-quota memory.

## Context
The game (`star-conquest.netlify.app`) and the board (`star-conquest-leaderboard.netlify.app`) are two Netlify sites. They share about 20 production publishes a month and keep separate localStorage. They find each other through a hostname hack (`sibling_host` / `siblingGame`), and leaving the fullscreen PWA for the board loses the app's storage and chrome on iOS. The brief is `docs/site-merge-plan.md`.

**Decisions made:**
- **Option A.** Functions move onto the game site. The old board host becomes a redirect shell.
- **db910fa is reworked, not shipped as-is.** Its "yours" machinery is simplified here, and everything goes out in **one** publish of the merged site plus one publish of the old host.

Work goes on a new branch `site-merge` cut from `play-by-post`, so it includes db910fa and 0d45010.

## Layout
- The board's static pages are served at **`/board/`** on the game origin. Functions stay at root **`/api/{log,replay,pbp}`**, so their `export const config` paths and the board's `ENDPOINT = "/api/pbp"` don't change.
- Source stays in `leaderboard/`, so its tests, README, schema and functions don't move. `tools/build_web.sh` copies only the servable parts into `web/board/`:
  - `*.html`, `css/`, `js/`, `fonts/` and `favicon.png`
  - never `netlify/`, `tests/`, `*.sql`, `README.md` or `netlify.toml`

  An explicit copy list is better than excludes. That keeps function source out of the publish dir, so the old `/netlify/*` 404 redirect becomes unnecessary.
- Root `netlify.toml` gains:
  - `[functions] directory = "leaderboard/netlify/functions"`
  - `[build.environment] SECRETS_SCAN_OMIT_KEYS = "SUPABASE_URL"`, moved from `leaderboard/netlify.toml`
  - `[[redirects]] /board → /board/ 301`, so relative links in the pages resolve correctly
  - `[[headers]] for="/board/*"` with nosniff and `Referrer-Policy: no-referrer`, scoped to the board so pygbag's assets aren't affected
- The header comment in root `netlify.toml` is rewritten. The "no secrets on this site" rule becomes "the secret is scoped to Functions and fork previews need approval", with the reasoning carried over.
- **Netlify UI steps (manual, listed in the PR):**
  - On the game site, add `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. (Scoping to Functions only turned out to need a paid plan; the build command unsets the key instead.)
  - Confirm the sensitive-variable policy is set to "Require approval".

## Host references
**Python** (`starconquest/paths.py`, `webstore.py`):
- `LEADERBOARD_ORIGIN = "https://star-conquest.netlify.app"`. This is still the desktop/Android route and the fallback. It stays non-blank, so `render.py:2251`'s "blank disables" check is unchanged.
- Page paths gain the prefix:
  - `LEADERBOARD_SUBMIT_PATH = "/board/submit.html"` (explicit `.html`, so it also works on a local static server)
  - `LEADERBOARD_CONFIGS_PATH = "/board/index.html?group=config"`
  - `LEADERBOARD_LOBBY_PATH = "/board/pbp.html"`
- API paths stay the same.
- Delete `sibling_host`, `LEADERBOARD_TAG` and `_NETLIFY_SUFFIX`.
- `webstore.leaderboard_origin()`:
  - On the web, return `location.origin` when the host ends in `.netlify.app`. That covers production and every deploy preview.
  - Otherwise, including localhost, return `paths.LEADERBOARD_ORIGIN`.
  - Keep the function as the seam, because `test_share`, `test_pbp` and `test_menu` all patch it.
- `share._post_web`'s docstring no longer says "cross-origin".

**Board JS** (`leaderboard/js/config.mjs`):
- Replace `siblingGame` with a mirror of the rule above: `GAME_URL = location.origin + "/"` on a `.netlify.app` host, else `GAME_URL_FALLBACK`.
- Delete `TAG`.

**Functions** (`log.mjs`, `pbp.mjs`):
- Keep `allowedOrigin` as it is. Same-origin calls still send `Origin` on POST, and it allows every `star-conquest` deploy plus localhost, which local dev needs.
- Its tests stay as they are.
- `replay.mjs` is unchanged.

**Tools:**
- `tools/admin.py` `GAME_URL` is unchanged.
- In `tools/check_pbp.py`, the docstring and `--origin` example become the game origin, e.g. `https://deploy-preview-N--star-conquest.netlify.app`, since the API is at root.

## PWA and service worker (`tools/pwa/sw.js`)
- Return early (no `respondWith`) for `url.pathname.startsWith("/api/")`. This matters: pbp polls `state` and `list` with GETs, and they must never be served stale.
- Use **network-first** for `/board/`, falling back to cache when offline. The pages are thin shells over live data, and one stale load after a deploy could pair old JS with a new API or `CURRENT_RULES_VERSION`.
- Keep stale-while-revalidate for game assets.
- Bump `CACHE` to `"starconquest-v2"`. The existing activate handler already deletes old caches.
- The manifest stays at `scope: "./"`, so `/board/` is inside it. Nothing to change there.

## Board pages: a way back to the game
- Inside the fullscreen PWA there is no address bar or Back button. Add a "▶ Play" link to the `.bar` of all 5 pages (`index`, `game`, `submit`, `user`, `pbp`), and give `pbp.html` an `<nav class="actions">`.
- `href` is set from `GAME_URL` in JS, the same way the pages already build game links. The brand link stays pointed at the board's own index.

## db910fa "yours" rework (the brief's table)
Before touching the lobby, read `leaderboard/js/pbp.mjs` and `docs/pbp-design.md` "Public matches and the lobby".

- **Lobby reads the game's seat store.** Export `SEATS_KEY = "sc_pbp_seats"`. `readSeats()` parses `{id: {seat, token}}`, the plain JSON string `pbp.remember` writes (`starconquest/pbp.py:161`), and tolerates junk like `parseMine` does.
  - "Yours" = the valid ids in it, newest 50 (`MAX_IDS`), sent to `?action=list&ids=`.
  - **Never prune on a miss.** The game owns this store.
- **Claim writes the seat into `sc_pbp_seats`** in exactly the `remember` shape. The copyable invite link and "Open in game" are built from the stored token (`seatLink`).
  - `openLink` always uses the full `#pbp=<id>:<token>` link. If there's no entry, there's no Open button.
- **Delete:**
  - `sc_pbp_mine`, `MINE_KEY`, `parseMine`, `mergeFragment`, the prune, and the hash-strip in `main()`
  - `pbp.lobby_fragment`, `pbp.bare_match`, the bare-match branch around `main.py:1254`, and `PBP_NOT_HELD_MSG`
  - The fragment in `menu.py:1973`: `browse_matches` now just opens `leaderboard_url(LEADERBOARD_LOBBY_PATH)`.
- **One name.** The storage key stays `sc_pbp_name`, since the game's store already holds it. `me.mjs` `myName()`/`rememberName()` switch from `sc_leaderboard_name` to `sc_pbp_name`, so the posting name, the lobby claim name and the game's pbp prompt are one value.
  - The board's old `sc_leaderboard_name` lives on the old origin and is lost anyway, which the brief accepts.

## Old host (`star-conquest-leaderboard.netlify.app`)
- New dir `legacy-board/` holding only `netlify.toml` (with a comment) and a placeholder `index.html`. The old site's Base directory is switched to it in the UI.
- Redirects:
  - `/api/*` → `https://star-conquest.netlify.app/api/:splat` **200 (proxy)**, not 308. Python `urllib` refuses to follow 307/308 on POST (checked: `HTTPRedirectHandler.redirect_request`), and old desktop/Android builds POST `/api/log` and `/api/pbp` there. Those clients send no `Origin`, so CORS isn't affected.
  - `/submit` → `/board/submit.html` 301. Old game builds open it as a pretty URL.
  - `/*` → `https://star-conquest.netlify.app/board/:splat` 301. Covers `/`, `game.html?key=…`, `user.html`, `pbp.html?match=…`, and query strings pass through.
- Afterwards, remove `SUPABASE_SECRET_KEY` from the old site.
- **Sunset.** Keep it at least until installed desktop/Android builds have updated. Suggest reviewing 2027-03-01 and recording that date in the toml comment. The proxied `/api/` rate limit may bucket legacy clients together, which is acceptable for that small tail.

## Cutover order (2 publishes total)
1. Merge `site-merge` to `main`, then manually publish the **game** site. At that point the new origin serves `/board/` and `/api/`, and the old board still works unchanged.
2. Smoke-test production (see Verification), then publish the **old** site with its new base dir.

## Docs
- `CLAUDE.md`:
  - Rewrite "The game and the board find each other by hostname" to say same-origin, with the old host as a shim.
  - Update the Play-by-post "yours" bullet: drop `#mine=`/`lobby_fragment`/`bare_match`.
  - Update the `share.py` wording.
- `docs/pbp-design.md` "Public matches and the lobby".
- `leaderboard/README.md`: Setup step 4, step 6, "Finding each other" and Local development (serve `web/` and open `/board/`).
- `docs/system-design.md` where it covers sibling hosts.
- `docs/site-merge-plan.md`: mark done, or delete.
- Memory `netlify-deploy-quota.md`: update once shipped.

## Tests to change
- **`tests/test_webstore.py`:**
  - Delete the three `sibling_host` tests (lines ~203, 212, 222).
  - Rewrite `test_on_the_web_the_endpoint_follows_the_page`: a fake host `deploy-preview-7--star-conquest.netlify.app` → `https://deploy-preview-7--star-conquest.netlify.app/api/log`.
  - Add a case where localhost falls back to `LEADERBOARD_ORIGIN`.
- **`tests/test_pbp.py`:** delete the `lobby_fragment` and `bare_match` tests (~430, 437).
- **`tests/test_menu.py:325`:** the shared-matches URL has no fragment.
- **`tests/test_main*`:** any `PBP_NOT_HELD_MSG` or bare-match test goes (grep).
- **`leaderboard/tests/pbp-page.test.mjs`:** replace the `#mine=`, malformed-store, stored-link and open-link tests with:
  - reads `sc_pbp_seats`, tolerating junk
  - newest 50
  - claim writes the `{seat, token}` shape
  - open link carries the token
  - no prune
- **New: `tests/test_web_build.py`** (static, no build). Asserts:
  - `sw.js` bypasses `/api/`
  - `build_web.sh`'s board copy list excludes `netlify`/`tests`
  - the root `netlify.toml` functions dir exists
- **New: a Python↔JS pin in `tests/test_leaderboard_sync.py`:** the `sc_pbp_seats` and `sc_pbp_name` keys match between `paths.py` and the board JS.

## Verification
- `uv run pytest` (full suite, since shell and core both change) and `node --test leaderboard/tests/*.test.mjs`.
- Run `./tools/build_web.sh` locally, then `python3 -m http.server` in `web/`:
  - `/board/` renders with CSS and fonts.
  - No `web/board/netlify` or `web/board/tests` exist.
  - The game's Leaderboard and Shared matches buttons land on `/board/…`. API calls fall back to production from localhost, since CORS allows localhost:8000.
- On the **deploy preview** (costs no production credit):
  - Every `/api/*` call is same-origin and works.
  - Post a score and watch it.
  - Create a pbp match, claim a seat in the lobby, and check that "yours" lists it without any fragment.
  - "Open in game" resumes the seat.
  - The DevTools Application tab shows the SW not serving `/api/`.
  - Hard-reload once to pick up SW v2.
  - Check the functions log for the client IP the rate limit sees.
- **Android PWA and iOS home-screen app** (installed from the preview): claim a seat in the lobby, confirm the game opens that seat, then return via Play. Everything should stay inside the app with shared storage.
- **After both publishes:**
  - Old host `/game.html?key=…` → 301 to `/board/game.html?key=…`.
  - `curl -X POST` old-host `/api/log` is proxied, not redirected.
  - A desktop build with the old `LEADERBOARD_ORIGIN` can still post.
