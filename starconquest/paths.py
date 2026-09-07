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

# The public leaderboard's score-entry page. The win overlay opens it with
# ``#<token>`` appended, which is all the form needs to prefill itself — the same
# challenge token the clipboard link carries, read by the site's own decoder.
# Blank disables the feature: the overlay simply doesn't offer the button.
#
# Written without the ``.html``, the way the host serves it: that form answers
# too, but this is the canonical URL and avoids a redirect hop.
LEADERBOARD_SUBMIT_URL = "https://star-conquest-leaderboard.netlify.app/submit"

# Where a match's replay is posted, so a score can be checked against the game
# that produced it (`upload.post_log`).
#
# Not Supabase directly: this is the leaderboard site's own function
# (`leaderboard/netlify/functions/log.mjs`), which validates a row, rate-limits by
# caller, and holds the only key that may write `game_logs`. Posting straight to
# PostgREST would mean shipping a key with insert rights in the game binary, and
# an insert path anyone could aim a script at — the table stores whole replays, so
# that is a storage bill rather than a few junk rows.
#
# Blank disables uploading entirely, the same way a blank LEADERBOARD_SUBMIT_URL
# disables the submit button.
LEADERBOARD_LOG_URL = "https://star-conquest-leaderboard.netlify.app/api/log"


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
