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

_FILE = "kv.json"  # desktop/Android backing file, beside saves/ and games/


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
            tmp.replace(path)  # atomic, like GameLog.save
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


def link_url(token: str) -> str:
    """The full shareable URL carrying ``token``, or ``""`` off the web."""
    if not is_web():
        return ""
    import platform as _platform

    try:
        loc = _platform.window.location
        return f"{loc.origin}{loc.pathname}#{token}"
    except Exception:
        return ""


def set_url_fragment(token: str) -> bool:
    """Web only: replace the address bar's ``#fragment``, no reload, no new
    history entry (``history.replaceState``)."""
    if not is_web():
        return False
    import platform as _platform

    try:
        _platform.window.history.replaceState(None, "", "#" + token)
        return True
    except Exception:
        return False


def copy_to_clipboard(text: str) -> bool:
    """Web only, best-effort: the write is async and fire-and-forget, so True
    means the call was accepted, not that the paste buffer definitely changed."""
    if not is_web():
        return False
    import platform as _platform

    try:
        _platform.window.navigator.clipboard.writeText(text)
        return True
    except Exception:
        return False


def sync_settings(token: str) -> bool:
    """Keep the address bar and the remembered token in step with a setup.

    The stored copy is what survives *installing* the PWA: the installed app
    launches from the manifest's fixed ``start_url`` with no fragment, so the hash
    is gone, but same-origin storage still holds what the browser saw before.
    """
    updated = set_url_fragment(token)
    return set(WEB_SHARED_SETTINGS_KEY, token) or updated


def share_token(token: str) -> tuple[bool, bool]:
    """Share a *setup*: sync the address bar and stored token, then try the
    clipboard. Returns ``(url_updated, copied)``.

    Both channels on purpose — the address bar is the reliable one in a browser
    tab, and the clipboard is the only one that exists in an installed PWA.
    """
    if not is_web():
        return (False, False)
    updated = sync_settings(token)
    return (updated, copy_to_clipboard(link_url(token)))


def copy_link(token: str) -> bool:
    """Share a *challenge*: clipboard only — no address bar, nothing stored.

    Deliberately unlike ``share_token``. A challenge token must not be persisted
    or left in the URL: it would be read back at the next launch and the score-to-
    beat banner would haunt every later session (and in an installed PWA there is
    no address bar to read a link out of anyway, so the clipboard is the only
    channel that actually works there).
    """
    url = link_url(token)
    return bool(url) and copy_to_clipboard(url)


def close_window() -> bool:
    """Web only: ask the browser to close this window/tab. True if the call went
    through — which is *not* a promise the app is gone, so callers must cope with
    still being alive afterwards.

    A page may normally only close itself if a script opened it, so this succeeds in
    an installed PWA window far more often than in an ordinary tab, and never at all
    in some browsers. There is no other way for a web app to exit, and the
    alternative — ending the main loop, which tears down the canvas — strands the
    player on a blank page with no way back, so a caller that can't verify the close
    must stay in the app instead (see ``main.leave_app``).
    """
    if not is_web():
        return False
    import platform as _platform

    try:
        _platform.window.close()
        return True
    except Exception:
        return False


def url_token() -> str:
    """Web only: the ``#<token>`` fragment in the address bar, else ``""``."""
    if not is_web():
        return ""
    import platform as _platform

    try:
        return str(_platform.window.location.hash).lstrip("#")
    except Exception:
        return ""
