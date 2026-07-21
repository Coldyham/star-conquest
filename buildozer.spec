[app]

# --- identity ---------------------------------------------------------------
title = Star Conquest
package.name = starconquest
package.domain = uk.co.visualwind
version = 0.1.1

# --- sources ----------------------------------------------------------------
# Bundle the entry point (main.py) and the package. Only these extensions are
# packaged, so the .venv, git metadata and __pycache__ never make it into the APK.
# .ttf/.txt pull in the bundled DejaVu font (and its licence) under
# starconquest/assets/.
source.dir = .
source.include_exts = py,ttf,txt
# Runtime-writable, developer-only, and doc dirs must not be baked into the APK
# (the app writes saves/games/models into app-private storage at runtime instead).
source.exclude_dirs = tests, games, saves, models, .venv, .git, .vscode, __pycache__, .pytest_cache

# --- requirements -----------------------------------------------------------
# pygame-ce provides the `pygame` module via python-for-android's SDL2 bootstrap.
requirements = python3,pygame-ce

# --- display ----------------------------------------------------------------
orientation = landscape
fullscreen = 1

# --- android ----------------------------------------------------------------
# No permissions needed: the game writes only to app-private internal storage.
android.archs = arm64-v8a, armeabi-v7a
android.api = 34
android.minapi = 24
# Let buildozer accept the Android SDK licences non-interactively on first build.
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
