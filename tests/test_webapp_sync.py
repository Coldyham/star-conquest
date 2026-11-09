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
