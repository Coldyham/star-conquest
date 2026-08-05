"""The small persistent bits a shared link needs: a key/value store and the
address bar.

Not a pygame module — the sibling of ``softkeyboard``, written in the same
defensive style. The browser parts go through pygbag's ``platform.window`` JS
bridge, imported *locally* (so importing this module is safe off the web build)
with every DOM call wrapped, so a missing or blocked API degrades to a no-op
return rather than a crash.

``get``/``set`` are localStorage in the browser and a JSON file under
``data_dir()`` everywhere else, so features built on them (personal bests) work on
desktop too rather than being web-only. ``share_token``/``url_token`` are
genuinely browser-only and no-op elsewhere. Key names live in ``paths`` beside
``WEB_SHARED_SETTINGS_KEY``, keeping "what may I write, and where" in one place.

Storage is always a nicety, never load-bearing: private-browsing modes, a full
quota and a read-only disk all fail here, and every caller carries on regardless.
"""

from __future__ import annotations

import json
from typing import Optional

from .paths import WEB_BESTS_KEY, WEB_SHARED_SETTINGS_KEY, data_dir, is_web

_FILE = "kv.json"      # desktop/Android backing file, beside saves/ and games/


def _file_path():
    return data_dir() / _FILE


def _read_file() -> dict:
    try:
        with open(_file_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(key: str) -> str:
    """The stored string for ``key``, or ``""`` when absent or unavailable."""
    if not is_web():
        value = _read_file().get(key)
        return value if isinstance(value, str) else ""
    import platform as _platform

    try:
        value = _platform.window.localStorage.getItem(key)
        return str(value) if value else ""
    except Exception:
        return ""


def set(key: str, value: str) -> bool:  # noqa: A001 - deliberate storage verb
    """Store ``value`` under ``key``. True if it landed."""
    if not is_web():
        try:
            data = _read_file()
            data[key] = value
            path = _file_path()
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w") as fh:
                json.dump(data, fh, indent=2)
            tmp.replace(path)       # atomic, like GameLog.save
            return True
        except Exception:
            return False
    import platform as _platform

    try:
        _platform.window.localStorage.setItem(key, value)
        return True
    except Exception:
        return False


# -- personal bests, keyed by Settings.challenge_key() ----------------------- #
# One JSON object of key -> {"turns": int, "lost": int}, so replaying a challenge
# can show what you already managed and a fresh result only overwrites a genuine
# improvement (fewer turns, or the same turns with fewer losses).


def best(challenge_key: str) -> Optional[tuple[int, int]]:
    """Your best ``(turns, lost)`` on this setup, or None if you've not won it."""
    try:
        entry = json.loads(get(WEB_BESTS_KEY) or "{}").get(challenge_key)
        if isinstance(entry, dict):
            return (int(entry["turns"]), int(entry["lost"]))
    except Exception:
        pass
    return None


def record_best(challenge_key: str, turns: int, lost: int) -> bool:
    """Store ``(turns, lost)`` if it beats what's there. True if it was an
    improvement (or a first result), so the caller can say so."""
    current = best(challenge_key)
    if current is not None and (turns, lost) >= current:
        return False
    try:
        data = json.loads(get(WEB_BESTS_KEY) or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[challenge_key] = {"turns": turns, "lost": lost}
    set(WEB_BESTS_KEY, json.dumps(data, separators=(",", ":")))
    return True


def share_token(token: str) -> tuple[bool, bool]:
    """Web only: put ``token`` in the URL as a ``#<token>`` fragment and try to
    copy the full link to the clipboard. Returns ``(url_updated, copied)``.

    The address-bar update (``history.replaceState`` — no reload, no new history
    entry) is the reliable channel; the clipboard write is best-effort, since it
    may be unavailable or blocked, and it is in a standalone PWA — no visible
    address bar — that the clipboard matters most.

    The token is also mirrored into ``localStorage``, which is what survives
    *installing* the PWA: the installed app launches from the manifest's fixed
    ``start_url`` with no fragment, so the hash is gone, but same-origin storage
    still holds what the browser saw before the install.
    """
    if not is_web():
        return (False, False)
    import platform as _platform

    try:
        win = _platform.window
        win.history.replaceState(None, "", "#" + token)
        win.localStorage.setItem(WEB_SHARED_SETTINGS_KEY, token)
        url = str(win.location.origin) + str(win.location.pathname) + "#" + token
    except Exception:
        return (False, False)
    try:
        win.navigator.clipboard.writeText(url)   # async; fire-and-forget
        return (True, True)
    except Exception:
        return (True, False)


def url_token() -> str:
    """Web only: the ``#<token>`` fragment in the address bar, else ``""``."""
    if not is_web():
        return ""
    import platform as _platform

    try:
        return str(_platform.window.location.hash).lstrip("#")
    except Exception:
        return ""
