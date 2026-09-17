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

# ---------------------------------------------------------------------------
# The public leaderboard, and how the game finds it.
#
# The two halves are separate Netlify sites whose names differ by exactly one
# string: `star-conquest` and `star-conquest-leaderboard`. Netlify names every
# other deploy `<context>--<site>.netlify.app` — `deploy-preview-42--…` for a PR,
# `<branch>--…` for a branch deploy — and both sites build from this one
# repository, so a PR produces the same `<context>` on each.
#
# That makes the sibling derivable at runtime rather than configured: insert the
# tag into our own site name and a deploy preview of the game talks to the
# matching preview of the board, a branch deploy to its branch deploy, and
# production to production, with nothing to edit by hand between them. See
# `sibling_host`, and `webstore.leaderboard_origin` for the DOM half.
#
# The constant below is the fallback for everywhere that reasoning does not
# reach: desktop, Android, a local server, or a custom domain that is not a
# `.netlify.app` name at all. Blank disables every leaderboard feature — the win
# overlay stops offering the button, uploads no-op, replays are unwatchable.
LEADERBOARD_ORIGIN = "https://star-conquest-leaderboard.netlify.app"
LEADERBOARD_TAG = "-leaderboard"     # what the board's site name has and ours does not
_NETLIFY_SUFFIX = ".netlify.app"

# Paths on that origin. `/submit` is written without the `.html` the way the host
# serves it: that form answers too, but this is the canonical URL and avoids a
# redirect hop. The two `/api` paths are the site's own functions
# (`leaderboard/netlify/functions/`), which hold the only key that may touch
# `game_logs` — posting straight to PostgREST would mean shipping a key with
# insert rights inside the game.
LEADERBOARD_SUBMIT_PATH = "/submit"    # the score-entry form, opened with #<token>
LEADERBOARD_LOG_PATH = "/api/log"      # where a replay is uploaded (`share.post_log`)
LEADERBOARD_REPLAY_PATH = "/api/replay"  # ...and fetched back (`share.fetch_log`)
# The board's "by config" listing (`leaderboard/js/home.mjs`'s `?group=config`):
# every setup somebody has posted a score under, grouped and named. Not specific
# to the setup on the menu right now — there is no way to name an arbitrary,
# possibly never-played setup's own config page without either shipping the
# Supabase project straight into the game or reimplementing `sc_config_key`'s
# Postgres-specific hashing a second time (schema.sql spells out why that hash
# is deliberately computed nowhere but there). This is the same link the web
# menu's file row used to spend on a Save/Load row that never actually
# persisted anything in the browser (see `menu._file_control`).
LEADERBOARD_CONFIGS_PATH = "/index.html?group=config"


def sibling_host(host: str, tag: str, *, add: bool) -> str:
    """``host`` with ``tag`` added to or removed from its Netlify *site name*.

    The site name is the last `--`-separated part of the label before
    ``.netlify.app``, which is what makes this work across contexts:

        star-conquest.netlify.app                  -> star-conquest-leaderboard.…
        deploy-preview-42--star-conquest.netlify…  -> deploy-preview-42--star-conquest-leaderboard.…

    Returns ``""`` for anything it cannot reason about — a custom domain,
    localhost, a host already in the wanted state — and the caller then falls
    back to the configured origin. Deliberately pure, so the string rule is
    testable without a browser.
    """
    if not host.endswith(_NETLIFY_SUFFIX):
        return ""
    label = host[: -len(_NETLIFY_SUFFIX)]
    prefix, sep, site = label.rpartition("--")
    if add:
        if site.endswith(tag):
            return ""                      # already the sibling; nothing to add
        site += tag
    else:
        if not site.endswith(tag):
            return ""                      # already the sibling; nothing to remove
        site = site[: -len(tag)]
    return f"{prefix}{sep}{site}{_NETLIFY_SUFFIX}" if site else ""


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
