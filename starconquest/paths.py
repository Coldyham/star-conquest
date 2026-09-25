"""Where the game keeps its writable data (saved games, saved settings, drop-in
AI models).

Pure core (no pygame): a single place that answers "where may I write?" so no
other module hardcodes a location. On desktop that is the repo root (the parent
of this package dir), exactly matching the historical layout so nothing moves. On
Android the app bundle is read-only, so we use the app-private storage dir that
python-for-android exposes — a real, writable filesystem, which is what lets
saved games persist and drop-in ``models/`` still be imported by path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The package dir's parent: the repo root on desktop.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# localStorage key the web build uses to remember the last shared-settings token.
# An installed PWA launches from the manifest's fixed ``start_url`` (no URL
# fragment), so a ``#<token>`` seen in the browser is stashed here and read back
# on the next launch — the one place that key is named, shared by main/menu.
WEB_SHARED_SETTINGS_KEY = "sc_shared_settings"

# Storage key for your best result per challenge setup (see `webstore.best`),
# so replaying a shared link can show what you already managed.
WEB_BESTS_KEY = "sc_bests"

# Storage key for the "share replays" preference (`webstore.share_games`): off
# unless the player turns it on from the menu, and stored beside the other local
# preferences rather than on `Settings` — it belongs to this installation, not to
# a game setup, and putting it on `Settings` would both move `challenge_key()`
# for every map that has ever existed and travel in every shared link.
WEB_SHARE_GAMES_KEY = "sc_share_games"

# Storage key for the "animate turns" display preference (`webstore.animate_turns`):
# on unless the player turns it off, and stored beside the other local preferences
# for the same reason `sc_share_games` is — it belongs to this installation, not to
# a game setup. On `Settings` it would move `challenge_key()` for every map that has
# ever existed and travel in every shared link, and nothing about how a turn is
# *drawn* can move a result.
WEB_ANIMATE_TURNS_KEY = "sc_animate_turns"

# Where a browser download parks its result for the game loop to collect
# (`share.fetch_log`). localStorage rather than a `window` property because
# reading one back is the one bridge call this build cannot take for granted:
# writing through `window.eval` is proven (that is how a replay is uploaded) and
# so is reading a *property* (the URL fragment, these very keys), but `eval`
# returning a value is neither. So the fetch writes here and the poll reads it
# with `webstore.get`, which every stored preference already depends on.
WEB_REPLAY_STATE_KEY = "sc_replay_state"
WEB_REPLAY_BODY_KEY = "sc_replay_body"

# The same mailbox pattern for a play-by-post request (`pbp.Request`). A separate
# pair rather than a shared one because a client can have a replay download and a
# match poll in flight at once, and one landing must not collect the other's body.
WEB_PBP_STATE_KEY = "sc_pbp_state"
WEB_PBP_BODY_KEY = "sc_pbp_body"

# Where a seat's token is remembered, so reopening the game returns you to your
# match without the link. Keyed by match id: `{"<match id>": {"seat": 2,
# "token": "…"}}`. A token is the only thing that can move a seat's ships, so it
# is stored where the game already keeps its own preferences and nowhere else —
# never in `Settings`, which travels in every shared link.
WEB_PBP_SEATS_KEY = "sc_pbp_seats"

# Our own standing forwarding rules in each match we hold a seat in, by match id:
# `{"<match id>": {"<src>": [dest, keep]}}` (`replay.rules_to_dict`). A solo game
# keeps them in its log, but a shared match's log is uploaded for every seat, so
# `pbp.shareable` strips them and this is the only copy. Local, like the token.
WEB_PBP_RULES_KEY = "sc_pbp_rules"

# The name last typed into the play-by-post prompt's "Your name" field, offered
# again next time. Display text only: it goes to the endpoint just when a match
# is created with it filled in.
WEB_PBP_NAME_KEY = "sc_pbp_name"

# ---------------------------------------------------------------------------
# The public leaderboard, and how the game finds it.
#
# The board is served from the game's own site: its pages under `/board/`
# (staged by `tools/build_web.sh` from `leaderboard/`), its functions at `/api/`.
# One origin means one localStorage, so the lobby reads the seats this file's
# keys name directly, and every deploy — production, a PR's deploy preview, a
# branch deploy — carries its own matching board with nothing to derive. On the
# web `webstore.leaderboard_origin` therefore answers with the page's own host.
#
# The constant below is the route for everywhere that is not a `.netlify.app`
# page: desktop, Android, a local server, or a custom domain. Blank disables
# every leaderboard feature — the win overlay stops offering the button, uploads
# no-op, replays are unwatchable. (The board's old host,
# `star-conquest-leaderboard.netlify.app`, is a redirect shell that proxies
# `/api/` here, so an installed build still naming it keeps working — see
# `legacy-board/netlify.toml`.)
LEADERBOARD_ORIGIN = "https://star-conquest.netlify.app"
NETLIFY_SUFFIX = ".netlify.app"

# Paths on that origin. The page paths carry an explicit `.html`, which the
# host answers directly and which also works on a plain local static server
# (`python -m http.server` in `web/`). The `/api` paths are the site's own
# functions (`leaderboard/netlify/functions/`), which hold the only key that may
# touch `game_logs` — posting straight to PostgREST would mean shipping a key
# with insert rights inside the game.
LEADERBOARD_SUBMIT_PATH = "/board/submit.html"  # the score-entry form, opened with #<token>
LEADERBOARD_LOG_PATH = "/api/log"      # where a replay is uploaded (`share.post_log`)
LEADERBOARD_REPLAY_PATH = "/api/replay"  # ...and fetched back (`share.fetch_log`)
LEADERBOARD_PBP_PATH = "/api/pbp"      # play-by-post: match state and submissions
# The board's "by config" listing (`leaderboard/js/home.mjs`'s `?group=config`):
# every setup somebody has posted a score under, grouped and named. Not specific
# to the setup on the menu right now — there is no way to name an arbitrary,
# possibly never-played setup's own config page without either shipping the
# Supabase project straight into the game or reimplementing `sc_config_key`'s
# Postgres-specific hashing a second time (schema.sql spells out why that hash
# is deliberately computed nowhere but there). This is the same link the web
# menu's file row used to spend on a Save/Load row that never actually
# persisted anything in the browser (see `menu._file_control`).
LEADERBOARD_CONFIGS_PATH = "/board/index.html?group=config"
# The play-by-post lobby. It reads `WEB_PBP_SEATS_KEY` itself, being on the
# same origin, so the game hands it nothing.
LEADERBOARD_LOBBY_PATH = "/board/pbp.html"


def _android_data_dir() -> Path | None:
    """The app-private writable dir on Android, or None when not on Android."""
    # python-for-android sets ANDROID_PRIVATE to the app's internal files dir.
    private = os.environ.get("ANDROID_PRIVATE")
    if private:
        return Path(private)
    try:  # newer p4a exposes it programmatically instead of via the env var
        from android.storage import app_storage_path  # type: ignore

        return Path(app_storage_path())
    except Exception:
        return None


def is_android() -> bool:
    """True when running inside a python-for-android build."""
    return _android_data_dir() is not None


def is_web() -> bool:
    """True when running in the browser (pygbag/Emscripten WebAssembly build)."""
    return sys.platform == "emscripten"


def data_dir() -> Path:
    """Base dir for all writable game data: the Android app-private dir, else the
    repo root (unchanged desktop behaviour)."""
    return _android_data_dir() or _REPO_ROOT


def saves_dir() -> Path:
    """Where saved setup configs and shared challenge tokens live (gitignored).

    Named here rather than in ``menu`` now that two modules write into it.
    """
    return data_dir() / "saves"


def maps_dir() -> Path:
    """Where hand-authored maps live (gitignored), mirroring ``saves_dir``.

    On the web build this is emscripten MEMFS and does not survive a reload —
    exactly as ``saves/`` already behaves. Matching that is the consistent
    choice; routing both through ``webstore`` is a separate improvement.
    """
    return data_dir() / "maps"
