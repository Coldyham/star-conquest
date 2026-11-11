"""Files the player keeps: a setup, a match, a hand-drawn map, as the HTML
build's Save and Open hand them to the browser and back.

pygame's desktop menu saves a setup under ``saves/`` and its map maker keeps
maps under ``maps/``; a browser has no folder the player can open, so the HTML
build downloads a file instead and reads one the player picks
(``docs/design/html-frontend.md``, phase 7). This module says what each file
holds and what a picked one is; the session turns that into a ``download``
effect, or into the menu, the map maker or a watched replay. Pure core: no
pygame, no I/O.

**What a file never carries:** a challenge. A challenge link travels by
clipboard only and is never stored (``webstore.copy_link``), so a saved setup
is written without one, and a loaded one is read without one.

An opened file is recognised by its shape, never by its name: the player may
have renamed it, and a name says nothing a JSON body does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .custommap import CustomMap
from .replay import GameLog
from .settings import Settings

SETUP_NAME = "starconquest_settings"   # pygame's default save name (`menu._DEFAULT_FILENAME`)
MAP_NAME = "map"
JSON_TYPE = "application/json"
# The longest file a player may open, in characters: a 300-turn match on the
# biggest board encodes well under a megabyte; past this it is not ours.
MAX_CHARS = 8_000_000

# Keys only a `Settings` dict has (`Settings.to_dict`), so an unrelated JSON
# file is not read as a default setup by its tolerant reader.
_SETUP_KEYS = frozenset({"players", "nodes", "mode", "ai_strategy", "fog_sight", "seed"})


@dataclass(frozen=True)
class Download:
    """A file for the page to hand the browser: its name, text and type."""

    name: str
    text: str
    type: str = JSON_TYPE

    def effect(self) -> dict:
        return {"name": self.name, "text": self.text, "type": self.type}


@dataclass(frozen=True)
class Opened:
    """What a picked file turned out to be: ``kind`` ``setup``, ``match`` or
    ``map`` with its ``value``, or ``None`` with ``why`` it is none of them."""

    kind: str | None
    value: Settings | GameLog | CustomMap | None = None
    why: str = ""


def file_name(name: str, default: str) -> str:
    """``name`` as a ``.json`` file name a download can carry: path separators
    and other characters a file system refuses dropped, blank as ``default``."""
    stem = re.sub(r"[^\w .-]+", "", name).strip(" .")
    stem = stem[:-5] if stem.lower().endswith(".json") else stem
    return f"{stem or default}.json"


def setup_file(settings: Settings, name: str = "") -> Download:
    """The setup as pygame's Save writes it (`Settings.save`), less any
    challenge."""
    text = json.dumps(settings.without_challenge().to_dict(), indent=2)
    return Download(file_name(name, SETUP_NAME), text)


def match_file(log: GameLog) -> Download:
    """A match's log, as `GameLog.save` writes it under ``games/``."""
    return Download(file_name(f"game_{log.match_id}", "game"), json.dumps(log.to_dict(), indent=2))


def map_file(recipe: CustomMap, name: str = "") -> Download:
    """A hand-drawn map, as the map maker's Save writes it (`maker.save_file`)."""
    return Download(file_name(name, MAP_NAME), json.dumps(recipe.normalised().to_dict(), indent=2))


def read(text: str) -> Opened:
    """What ``text``, a picked file's contents, is. Never raises."""
    if len(text) > MAX_CHARS:
        return Opened(None, why="too big to be a Star Conquest file")
    try:
        data = json.loads(text)
    except ValueError:
        return Opened(None, why="not a Star Conquest file")
    if not isinstance(data, dict):
        return Opened(None, why="not a Star Conquest file")
    if "turns" in data and "settings" in data:
        try:
            log = GameLog.from_dict(data)
        except (ValueError, TypeError, KeyError, AttributeError):
            return Opened(None, why="a match that wouldn't open")
        if not log.turn_count:
            return Opened(None, why="a match with no turns in it")
        return Opened("match", log)
    if "n" in data and "l" in data:
        recipe = CustomMap.from_dict(data)
        if recipe is None:
            return Opened(None, why="a map that isn't usable")
        return Opened("map", recipe)
    if _SETUP_KEYS & data.keys():
        try:
            return Opened("setup", Settings.from_dict(data).without_challenge())
        except (ValueError, TypeError, KeyError, AttributeError):
            return Opened(None, why="a setup that wouldn't load")
    return Opened(None, why="not a Star Conquest file")
