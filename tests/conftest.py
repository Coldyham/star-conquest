import pytest

from starconquest import config, settings


@pytest.fixture(autouse=True)
def _restore_balance_knobs():
    """`build_state` writes its setup's knobs into `config`; put them back after
    every test, so a test that reads `config` directly (mapgen's pinned boards)
    never inherits whichever setup the test before it built, in any order."""
    saved = [(const, getattr(config, const)) for _, const in settings._GLOBAL_KNOBS]
    yield
    for const, value in saved:
        setattr(config, const, value)
