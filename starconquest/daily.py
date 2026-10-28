"""The daily challenge, from the game's side.

One map a UTC day, the same for everyone, made by the hourly worker
(``tools/daily.py``) and stored on the board. The game asks ``/api/daily`` for a
day's setup and applies it to the menu, as a pasted challenge link applies one:
a daily is an ordinary setup with a pinned seed, so it plays, scores and posts
as itself. Reasons: ``docs/design/daily.md``.

The game never makes a daily: which candidate survives depends on how the
probes play, so only the stored row is the same for everyone.

What is kept is only which setups were dailies (``remember``, by challenge key),
so a game started on one is known as the daily: it plays ironman and its win
overlay copies a result line instead of a challenge link. Progress is a best
on the key like any map's, derived and never stored here.

Like ``campaign``, nothing here is load-bearing: off the web with no board, or
with the board unreachable, there is no daily and the game plays as before.

Pure core: no pygame.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from . import dealer, pbp, webstore
from .paths import LEADERBOARD_DAILY_PATH, WEB_DAILIES_KEY
from .settings import Settings

# `#daily` opens today's; `#daily=YYYY-MM-DD` a past day's.
LINK_FRAGMENT = "daily"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# How many dailies are remembered as such: a fortnight, so last week's is
# still known as a daily when it is replayed from the archive.
REMEMBER_MAX = 14
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def valid_day(text: object) -> bool:
    if not isinstance(text, str) or not _DAY_RE.match(text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def link_day(fragment: str) -> str | None:
    """The day a ``#daily`` fragment asks for: ``""`` for today, a date for
    ``#daily=<day>``, None for any other fragment (or an unreadable date)."""
    if fragment == LINK_FRAGMENT:
        return ""
    if not fragment.startswith(LINK_FRAGMENT + "="):
        return None
    day = fragment[len(LINK_FRAGMENT) + 1:].strip()
    return day if valid_day(day) else None


def fetch(day: str = "") -> pbp.Request | None:
    """Start asking for ``day``'s daily (today's when blank). None when there
    is no board to ask."""
    endpoint = webstore.leaderboard_url(LEADERBOARD_DAILY_PATH)
    if not endpoint or (day and not valid_day(day)):
        return None
    return pbp.request(f"{endpoint}?day={day}" if day else endpoint)


def from_reply(body: str) -> tuple[str, Settings] | None:
    """``(day, setup)`` from ``/api/daily``'s answer, or None."""
    try:
        reply = json.loads(body)
    except ValueError:
        return None
    if not isinstance(reply, dict) or not valid_day(reply.get("day")) or not isinstance(reply.get("settings"), dict):
        return None
    settings = Settings.from_dict(reply["settings"])
    if settings.seed is None:
        return None
    return reply["day"], settings


def _kept() -> dict[str, str]:
    try:
        data = json.loads(webstore.get(WEB_DAILIES_KEY) or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and valid_day(v)} if isinstance(data, dict) else {}


def remember(day: str, settings: Settings) -> None:
    """Keep that ``settings`` is ``day``'s daily, newest days first."""
    kept = {k: v for k, v in _kept().items() if v != day}
    kept[settings.challenge_key()] = day
    newest = sorted(kept.items(), key=lambda kv: kv[1], reverse=True)[:REMEMBER_MAX]
    webstore.set(WEB_DAILIES_KEY, json.dumps(dict(newest), separators=(",", ":")))


def day_of(settings: Settings) -> str:
    """The day ``settings`` is the daily of, or ``""``."""
    return _kept().get(settings.challenge_key(), "")


def short_date(day: str) -> str:
    """'Wed 28 Oct'."""
    d = dt.date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {d.strftime('%b')}"


def label(day: str, settings: Settings) -> str:
    """The menu's line for a daily just opened."""
    return f"Daily · {short_date(day)}: {dealer.describe(settings)}"


def result_line(day: str, settings: Settings, turns: int, lost: int) -> str:
    """The spoiler-free lines a win copies: the day, the board's size, the
    result and a link that opens today's daily (never this setup)."""
    link = webstore.leaderboard_url("/game/#" + LINK_FRAGMENT)
    lines = [f"Star Conquest daily · {short_date(day)} · {settings.system_count()} systems",
             f"Won in {turns} turns, {lost} ships lost"]
    return "\n".join(lines + ([link] if link else []))
