# Merging the game and leaderboard sites — planning brief

Status: **implemented, cutover pending.** The plan chosen from this brief is
option A, in `site-merge-implementation-plan.md` at the repo root; the code is
on `claude/leaderboard-game-netlify-merge-jtlpb8`. What remains is manual: the
Netlify UI steps and the two publishes listed at the top of that plan. This
brief is kept for its reasoning; the facts below describe the tree *before* the
merge.
It records why a merge is wanted, what the two sites look like today, the
constraints any plan has to satisfy, the open decisions, and what gets simpler
afterwards. Facts were checked against the tree on 2026-09-24 (`play-by-post` at
`db910fa`).

## Why

1. **The deploy quota.** Both sites sit on one free Netlify account. The pool is
   300 build credits a month, and each production publish on either site costs
   15, so about 20 publishes a month *in total*. Netlify already skips the
   board's build when nothing under its base directory (`leaderboard/`)
   changed, so not every push to `main` publishes **both** sites. Production
   publishing from `main` is also locked, so each publish is a deliberate,
   manual step. That holds the line but isn't a long-term answer: having to
   remember to publish is its own downside, and a change usually touches both
   sides anyway. The quota has already run out once
   (blocked until 2026-09-21), with a merged board fix waiting over a week for a
   slot.
2. **Separate origins mean separate localStorage.** The play-by-post lobby's
   "your matches" is built around that gap (next section). One origin turns most
   of that into a plain read of the game's own seat store.
3. **Passing information by url params** Challenge links to leaderboard scores,
   PBP links to opened games
4. **PWA stays a PWA** Currently opening the leaderboard from the PWA mobile game re-adds a header,
   then following a link from there opens the game with the header rather than using the full screen space. Having it all one site would reduce that issue
5. **Deploy preview links between sites** There's logic to make links by adding or removing
   "-leaderboard" from the url, which is a hack to get around previews linking back to main
   If the sites were one, this would be unnecessary and could be stripped

## Held work that depends on this

`db910fa` (pushed on `play-by-post`, working on deploy-preview-73, **not
published to production**) is the richer lobby: pass-phrase names, titles, seat
names, winners, a page per match, and "your matches". It is being held so the
quota isn't spent shipping it twice. Its Supabase migrations have already been
applied.

After a merge, these parts of it become unnecessary or simpler:

| Today (two origins) | After (one origin) |
| --- | --- |
| Game hands the lobby `#mine=<id>,<id>` (`pbp.lobby_fragment`, the menu's `browse_matches` URL) | Lobby reads `sc_pbp_seats` directly; the button just navigates |
| Lobby's own cache `sc_pbp_mine`, pruned when a lookup misses | Gone; the game's store is the only record |
| A claim keeps its link in the lobby's cache | A claim writes the seat into `sc_pbp_seats`, in `pbp.remember`'s shape `{id: {seat, token}}` |
| Bare `#pbp=<id>` reopen (`pbp.bare_match`, `PBP_NOT_HELD_MSG`) | "Open in game" builds the full link from the stored token |
| Two name keys: `sc_leaderboard_name` (`me.mjs`) and `sc_pbp_name` | One name, read by the game's prompt too |
| iOS: the lobby opens in Safari, outside the home-screen app's storage | Lobby inside the app's scope shares its storage (see the PWA constraint) |

`?action=list&ids=` stays: private matches you're in still need a lookup by id.

Whoever plans this should choose between rebasing `db910fa` onto the merged
layout before shipping it and shipping it as is first. The quota argues for
shipping once.

## What exists today

**Game site** (`star-conquest.netlify.app`, root `netlify.toml`)
- Build: installs uv, then `tools/build_web.sh`: pygbag, then the PWA files
  from `tools/pwa/` (`manifest.webmanifest`, `sw.js`, icons, `inject.py`
  patching `index.html`). Publishes `web/`.
- **It holds no environment variables, deliberately.** On a public repo a fork's
  pull request gets a deploy preview built from the fork's own `netlify.toml`
  and build script, so the build command is arbitrary code. The file's header
  explains this at length.
- Root files include `index.html` and `favicon.png`.

**Board site** (`star-conquest-leaderboard.netlify.app`, `leaderboard/netlify.toml`)
- No build step; publishes `leaderboard/` as static files. Pages: `index.html`,
  `game.html`, `submit.html`, `user.html`, `pbp.html`, plus `css/`, `js/`,
  `fonts/` and `favicon.png`, which collides with the game's.
- Functions in `leaderboard/netlify/functions/`: `log.mjs` (`/api/log`),
  `replay.mjs` (`/api/replay`), `pbp.mjs` (`/api/pbp`). They hold
  `SUPABASE_URL` and **`SUPABASE_SECRET_KEY`**.
- Board links between its own pages are relative. `pbp.mjs` fetches `/api/pbp`
  absolute. Supabase is read cross-origin with the public anon key
  (`js/config.mjs`) and is unaffected by any of this.

**How they find each other**
- Hostname derivation both ways: `paths.sibling_host` / `LEADERBOARD_TAG` and
  `webstore.leaderboard_origin` in the game, `siblingGame` in `config.mjs` on the
  board. A deploy preview of one talks to the matching preview of the other.
- Fallback constants: `paths.LEADERBOARD_ORIGIN` (also desktop and Android's
  only route) and `GAME_URL_FALLBACK` in `config.mjs`. `tools/admin.py` has its
  own `GAME_URL`, and `tools/check_pbp.py` takes `--origin`.
- CORS: `log.mjs` and `pbp.mjs` allow-list origins by Netlify host shape
  (`allowedOrigin`, `GAME_SITE`). `replay.mjs` allows `*`. Desktop sends no
  Origin at all.
- Page paths the game opens: `paths.LEADERBOARD_SUBMIT_PATH` (`/submit`),
  `LEADERBOARD_CONFIGS_PATH`, `LEADERBOARD_LOBBY_PATH`. These are root-relative
  today and gain a prefix if the board moves under a subpath.
- About 25 tracked files mention these names. Run
  `git grep -E "netlify\.app|sibling_host|LEADERBOARD_ORIGIN|leaderboard_origin|siblingGame|GAME_URL|allowedOrigin|GAME_SITE|LEADERBOARD_TAG"`
  for the list; the heaviest are `tests/test_webstore.py`,
  `leaderboard/tests/functions.test.mjs` and `leaderboard/README.md`.

**Storage keys**, all `sc_`-prefixed. They would share one namespace:
- Game (`paths.py`): `sc_shared_settings`, `sc_bests`, `sc_share_games`,
  `sc_animate_turns`, `sc_replay_state`/`_body`, `sc_pbp_state`/`_body`,
  `sc_pbp_seats` (tokens), `sc_pbp_name`.
- Board: `sc_leaderboard_name`, `sc_pbp_mine`, `sc_pbp_name`. The shared
  `sc_pbp_name` means the same thing on both sides, so a shared value there is
  intended. Nothing else collides.

## Constraints a plan must satisfy

1. **The secret key must not become readable by a fork's preview.** This is the
   hard one. A single site running the functions holds `SUPABASE_SECRET_KEY`,
   and a fork's preview deploys the fork's own *functions* too, so scoping the
   variable to "Functions" only doesn't help. Netlify's sensitive-variable
   policy (public repos, "Require approval") holds such previews for manual
   approval, at the cost of hand-approving every fork PR preview. See option B
   below for a way to avoid the question.
2. **Merge onto the game's origin, not the board's.** localStorage doesn't
   migrate between origins. The game's holds seat tokens, bests and
   preferences; the board's holds a posting name and the lobby cache, which are
   cheap to lose. Serve the board under a subpath (e.g. `/board/`), which also
   settles the `index.html` and `favicon.png` collisions.
3. **The service worker must not cache the API.** `sw.js` is registered from the
   root and serves *every* same-origin GET stale-while-revalidate. On a merged
   origin that includes `/api/pbp?action=state|list`: a poll would get the
   previous answer, which play-by-post cannot tolerate. `/api/` needs a bypass.
   Board pages under SWR are acceptable, since their data comes from Supabase
   cross-origin (not intercepted) or `/api/`. Decide deliberately rather than by
   default.
4. **PWA scope and display.** The manifest's `scope` is `./` and `display` is
   `fullscreen`. The iOS storage win depends on board pages staying *inside*
   that scope. Once they're inside, they render with no browser chrome: no
   address bar and no Back. Board pages then need a visible way back to the
   game, and the brand link points at the board's own index today.
5. **Old URLs keep working.** Board links in the wild (`game.html?key=…`,
   `user.html`, `pbp.html?match=…`) and installed desktop or Android builds whose
   `LEADERBOARD_ORIGIN` still names the old host both depend on it. The old site
   stays up as a redirect. API calls need a method-preserving redirect (308) or a
   proxy: `fetch` turns a POST that meets a 301 or 302 into a GET.
6. **The pygbag build runs for every change.** Today the base-directory skip
   spares the game's build on a board-only change. Merged, every change builds
   the game: slower deploys, same credit cost per publish. That's acceptable, but
   a board typo fix is no longer cheap to preview.

## Options for the functions (the open decision)

- **A. One site, functions included.** Simplest end state: one deploy, no CORS,
  no sibling logic. It pays constraint 1 in full: the secret lives on the site
  that builds fork previews, and every fork preview needs approval.
- **B. Static pages merge; functions stay on the board site, proxied.** The game
  site serves the board pages under `/board/`, and a Netlify 200 rewrite sends
  `/api/*` to the board site. To the browser everything is one origin (one
  storage, no CORS), and the secret never touches the site that builds
  untrusted previews. Two sites still exist, but the board site only changes
  when a function does, which is rare. Things to verify:
  - Whether the rate limit in `log.mjs`/`pbp.mjs` still sees the client's IP
    through the proxy. It keys on `x-nf-client-connection-ip`, then
    `x-forwarded-for`. If it sees the proxy's IP instead, every player shares
    one bucket.
  - What `Origin` the function receives.
  - Whether a fork's preview proxying to the production API is acceptable.

## Suggested shape of the planning session

1. Choose A or B.
2. Design the layout: subpath name, `netlify.toml` (publish dir, functions dir,
   redirects), and how `build_web.sh` stages `leaderboard/` into `web/`.
3. List every host reference to change (the `git grep` above), and decide what
   happens to sibling-host derivation. Previews become one URL, so it likely goes.
4. Service worker: `/api/` bypass, a cache-name bump, and a policy for board pages.
5. Redirects and proxying from the old host, and a date after which it can stop.
6. Decide how `db910fa`'s "yours" machinery lands (the table above).
7. Verification: the deploy preview, then an Android PWA and an iOS home-screen
   app, checking play-by-post seats and "your matches" in each.

## References

- `docs/pbp-design.md`, "Public matches and the lobby": why the current
  two-origin "yours" design is shaped as it is.
- `netlify.toml` (root) header: the no-secrets rule and the fork-preview reasoning.
- `leaderboard/netlify.toml`, `leaderboard/README.md`: the board site's own setup.
- `tools/build_web.sh`, `tools/pwa/`: the game build and the PWA layer.
- `CLAUDE.md`: "The game and the board find each other by hostname".
