"""The HTML build's committed fixtures match what the engine makes today.

`webapp/tests/reel.test.mjs` checks the page's `Reel` mirror against
`webapp/tests/fixtures/films.json`. A change to the engine, the film schedule or
`serial` that moves those films must reach the fixture, or the JS test would
keep passing against the old ones: rerun `tools/dump_film_fixtures.py`.
"""

from __future__ import annotations

from tools import dump_film_fixtures


def test_the_film_fixtures_are_current():
    assert dump_film_fixtures.OUT.read_text() == dump_film_fixtures.text(), \
        "rerun: uv run python tools/dump_film_fixtures.py"


def test_the_page_sends_every_game_action_the_session_answers():
    """`webapp/js/play.mjs`'s GAME_ACTIONS reaches each action
    `Session.op_game_action` answers; one missing says "not in this build yet"
    (as the win overlay's Next stage once did)."""
    import inspect
    import re
    from pathlib import Path

    from starconquest.session import Session

    answered = set(re.findall(r'name == "(\w+)"', inspect.getsource(Session.op_game_action)))
    page = (Path(__file__).resolve().parents[1] / "webapp" / "js" / "play.mjs").read_text()
    listed = re.search(r"const GAME_ACTIONS = new Set\(\[(.*?)\]\)", page, re.DOTALL)
    assert listed is not None
    sent = set(re.findall(r'"(\w+)"', listed.group(1)))
    assert answered <= sent, f"missing from GAME_ACTIONS: {sorted(answered - sent)}"
