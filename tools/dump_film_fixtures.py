"""Write the turn films the HTML build's `Reel` mirror is tested against.

For a few seeded all-bot games (in-lane battles off and on), every turn's board
before, its film, and the board after, as `serial` shapes them, into
`webapp/tests/fixtures/films.json`. `webapp/tests/reel.test.mjs` plays each film
onto its board before and expects the board after: `turnfilm`'s history-path
rule, held on the JS side too. `tests/test_webapp_sync.py` fails when the file
falls behind the engine or `serial`.

    uv run python tools/dump_film_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import ai, engine, replay, serial, turnfilm
from starconquest.settings import Settings, build_state

OUT = ROOT / "webapp" / "tests" / "fixtures" / "films.json"

GAMES = [
    {"name": "duel", "seed": 3, "turns": 24,
     "settings": {"players": 2, "nodes": 14, "ai_strategy": ["marshal", "rusherplus"]}},
    {"name": "three-way", "seed": 5, "turns": 24,
     "settings": {"players": 3, "nodes": 18, "ai_strategy": ["marshal", "thinker", "heuristic"]}},
    {"name": "lane-battles", "seed": 3, "turns": 30,
     "settings": {"players": 2, "nodes": 10, "in_lane_battles": True,
                  "ai_strategy": ["rusherplus", "rusherplus"]}},
    {"name": "lane-battles-4p", "seed": 1, "turns": 30,
     "settings": {"players": 4, "nodes": 14, "in_lane_battles": True,
                  "ai_strategy": ["rusherplus", "marshal", "rusherplus", "heuristic"]}},
]


def build() -> dict:
    ai.load_models()
    games = []
    for spec in GAMES:
        settings = Settings.from_dict({**spec["settings"], "autoplay": True})
        settings.autoplay = True
        state = build_state(settings, spec["seed"])
        turns = []
        static = serial.static(state)
        for _ in range(spec["turns"]):
            if state.winner is not None:
                break
            before = serial.board(state)
            events: list[turnfilm.Event] = []
            replay.reseed(state)
            engine.end_turn(state, decide=ai.decide, on_event=events.append)
            turns.append({"before": before, "film": serial.film(turnfilm.film(events)),
                          "after": serial.board(state)})
        games.append({"name": spec["name"], "static": static, "turns": turns})
    return {"games": games}


def text() -> str:
    return json.dumps(build(), separators=(",", ":"), sort_keys=True) + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text())
    data = json.loads(OUT.read_text())
    print(f"wrote {OUT.relative_to(ROOT)}: "
          + ", ".join(f"{g['name']} {len(g['turns'])} turns" for g in data["games"])
          + f" ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
