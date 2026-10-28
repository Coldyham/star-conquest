"""The daily challenges, from the game's side.

Two maps a UTC day, each the same for everyone: the board and the puzzle (a
small duel won in the fewest turns), made by the hourly worker
(``tools/daily.py``) and stored on the board. The game asks ``/api/daily`` for a
day's setup of a kind and applies it to the menu, as a pasted challenge link
applies one: a daily is an ordinary setup with a pinned seed, so it plays,
scores and posts as itself. Reasons: ``docs/design/daily.md``.

The game never makes a daily: which candidate survives depends on how the
probes play, so only the stored row is the same for everyone.

What is kept is only which setups were dailies, and of which day and kind
(``remember``, by challenge key), so a game started on one is known as the daily: it plays ironman and its win
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

from . import ai, dealer, gauntletgen, pbp, webstore
from .paths import LEADERBOARD_DAILY_PATH, WEB_DAILIES_KEY
from .settings import Settings

BOARD, PUZZLE = "board", "puzzle"
KINDS = (BOARD, PUZZLE)
# `#daily` opens today's board and `#puzzle` today's puzzle; `#daily=YYYY-MM-DD`
# and `#puzzle=YYYY-MM-DD` a past day's.
LINK_FRAGMENTS = {BOARD: "daily", PUZZLE: "puzzle"}
LINK_FRAGMENT = LINK_FRAGMENTS[BOARD]
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# How many dailies are remembered as such: a fortnight of both kinds, so last
# week's is still known as a daily when it is replayed from the archive.
REMEMBER_MAX = 28
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def valid_day(text: object) -> bool:
    if not isinstance(text, str) or not _DAY_RE.match(text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def link(fragment: str) -> tuple[str, str] | None:
    """``(kind, day)`` a ``#daily`` or ``#puzzle`` fragment asks for: the day
    ``""`` for today, a date for ``#daily=<day>``; None for any other fragment
    (or an unreadable date)."""
    for kind, name in LINK_FRAGMENTS.items():
        if fragment == name:
            return kind, ""
        if fragment.startswith(name + "="):
            day = fragment[len(name) + 1:].strip()
            return (kind, day) if valid_day(day) else None
    return None


def fetch(day: str = "", kind: str = BOARD) -> pbp.Request | None:
    """Start asking for ``day``'s daily of ``kind`` (today's when blank). None
    when there is no board to ask."""
    endpoint = webstore.leaderboard_url(LEADERBOARD_DAILY_PATH)
    if not endpoint or (day and not valid_day(day)) or kind not in KINDS:
        return None
    query = ([f"day={day}"] if day else []) + ([f"kind={kind}"] if kind != BOARD else [])
    return pbp.request(f"{endpoint}?{'&'.join(query)}" if query else endpoint)


def from_reply(body: str) -> tuple[str, str, Settings] | None:
    """``(day, kind, setup)`` from ``/api/daily``'s answer, or None. A reply
    with no kind is a board's, as every reply was before the puzzle."""
    try:
        reply = json.loads(body)
    except ValueError:
        return None
    if not isinstance(reply, dict) or not valid_day(reply.get("day")) or not isinstance(reply.get("settings"), dict):
        return None
    kind = reply.get("kind", BOARD)
    if kind not in KINDS:
        return None
    settings = Settings.from_dict(reply["settings"])
    if settings.seed is None:
        return None
    return reply["day"], kind, settings


def _kept() -> dict[str, tuple[str, str]]:
    """Challenge key -> ``(day, kind)``. A day alone is a board's, as every
    entry was before the puzzle."""
    try:
        data = json.loads(webstore.get(WEB_DAILIES_KEY) or "{}")
    except ValueError:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for key, value in (data.items() if isinstance(data, dict) else ()):
        if isinstance(value, str):
            day, kind = value, BOARD
        elif isinstance(value, list) and len(value) == 2:
            day, kind = value
        else:
            continue
        if isinstance(key, str) and valid_day(day) and kind in KINDS:
            out[key] = (day, kind)
    return out


def remember(day: str, settings: Settings, kind: str = BOARD) -> None:
    """Keep that ``settings`` is ``day``'s daily of ``kind``, newest days first."""
    kept = {k: v for k, v in _kept().items() if v != (day, kind)}
    kept[settings.challenge_key()] = (day, kind)
    newest = sorted(kept.items(), key=lambda kv: kv[1][0], reverse=True)[:REMEMBER_MAX]
    webstore.set(WEB_DAILIES_KEY, json.dumps({k: list(v) for k, v in newest}, separators=(",", ":")))


def daily_of(settings: Settings) -> tuple[str, str]:
    """``(day, kind)`` of the daily ``settings`` is, or ``("", "")``."""
    return _kept().get(settings.challenge_key(), ("", ""))


def noun(kind: str) -> str:
    """'daily puzzle' or 'daily challenge'."""
    return "daily puzzle" if kind == PUZZLE else "daily challenge"


def opponent(settings: Settings) -> str:
    """A puzzle's opponent as the game names a seat's bot ("Thinker ·
    Claudebot"), or a probe tweak's name ("heuristic+lean")."""
    strategy, params = settings.seat_strategy(2), settings.seat_params(2)
    for name, (bot, tweak) in gauntletgen.TWEAKS.items():
        if strategy == bot and params == tweak:
            return name
    family, aux = ai.canonical(strategy, params.aux)
    stop = ai.aux_stop_name(family, aux)
    name = family[:1].upper() + family[1:]
    return f"{name} · {stop}" if stop and stop.lower() != family.lower() else name


def short_date(day: str) -> str:
    """'Wed 28 Oct'."""
    d = dt.date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {d.strftime('%b')}"


def label(day: str, settings: Settings, kind: str = BOARD) -> str:
    """The menu's line for a daily just opened."""
    if kind == PUZZLE:
        return f"Puzzle · {short_date(day)}: {settings.system_count()} systems · vs {opponent(settings)}"
    return f"Daily · {short_date(day)}: {dealer.describe(settings)}"


def result_line(day: str, settings: Settings, turns: int, lost: int, kind: str = BOARD) -> str:
    """The spoiler-free lines a win copies: the day, the board's size (and a
    puzzle's opponent), the result and a link that opens today's daily of the
    kind (never this setup)."""
    link = webstore.leaderboard_url("/game/#" + LINK_FRAGMENTS.get(kind, LINK_FRAGMENT))
    head = (f"Star Conquest daily puzzle · {short_date(day)} · {settings.system_count()} systems"
            f" vs {opponent(settings)}" if kind == PUZZLE
            else f"Star Conquest daily · {short_date(day)} · {settings.system_count()} systems")
    lines = [head, f"Won in {turns} turns, {lost} ships lost"]
    return "\n".join(lines + ([link] if link else []))
