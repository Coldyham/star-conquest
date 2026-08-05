"""The key/value store behind shared links and personal bests.

Off the web build `webstore` is backed by a JSON file, which is the path these
exercise; the browser branches need `platform.window` and are unreachable here.
Every function is written to fail soft, so the interesting cases are the broken
ones: a missing file, unreadable JSON, an unwritable directory.
"""

from __future__ import annotations

import json

import pytest

from starconquest import webstore
from starconquest.paths import WEB_BESTS_KEY


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirect the backing file into tmp_path so tests never touch the repo's."""
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    return tmp_path / "kv.json"


def test_get_on_a_missing_file_is_empty():
    assert webstore.get("nothing") == ""


def test_set_then_get_round_trips():
    assert webstore.set("k", "v")
    assert webstore.get("k") == "v"


def test_set_preserves_other_keys():
    webstore.set("a", "1")
    webstore.set("b", "2")
    assert (webstore.get("a"), webstore.get("b")) == ("1", "2")


def test_corrupt_file_reads_as_empty_rather_than_raising(isolated_store):
    isolated_store.write_text("{not json")
    assert webstore.get("k") == ""
    assert webstore.set("k", "v")        # and a write repairs it
    assert webstore.get("k") == "v"


def test_non_object_json_reads_as_empty(isolated_store):
    isolated_store.write_text('["a list, not an object"]')
    assert webstore.get("k") == ""


def test_unwritable_location_fails_soft(monkeypatch, tmp_path):
    monkeypatch.setattr(webstore, "_file_path",
                        lambda: tmp_path / "no" / "such" / "dir" / "kv.json")
    assert webstore.set("k", "v") is False


# --- personal bests ---------------------------------------------------------- #
def test_no_best_recorded_yet():
    assert webstore.best("key") is None


def test_record_and_read_a_best():
    assert webstore.record_best("key", 137, 412)
    assert webstore.best("key") == (137, 412)


def test_fewer_turns_is_an_improvement():
    webstore.record_best("key", 137, 412)
    assert webstore.record_best("key", 120, 900)
    assert webstore.best("key") == (120, 900)


def test_same_turns_fewer_losses_is_an_improvement():
    webstore.record_best("key", 137, 412)
    assert webstore.record_best("key", 137, 300)
    assert webstore.best("key") == (137, 300)


def test_a_worse_result_is_rejected_and_does_not_overwrite():
    webstore.record_best("key", 137, 412)
    assert webstore.record_best("key", 150, 10) is False
    assert webstore.record_best("key", 137, 500) is False
    assert webstore.best("key") == (137, 412)


def test_bests_are_kept_per_challenge():
    webstore.record_best("one", 10, 1)
    webstore.record_best("two", 20, 2)
    assert webstore.best("one") == (10, 1)
    assert webstore.best("two") == (20, 2)


def test_corrupt_bests_blob_does_not_lose_a_new_result(isolated_store):
    isolated_store.write_text(json.dumps({WEB_BESTS_KEY: "not an object"}))
    assert webstore.best("key") is None
    assert webstore.record_best("key", 5, 5)
    assert webstore.best("key") == (5, 5)


def test_web_only_helpers_are_no_ops_off_the_browser():
    assert webstore.share_token("abc") == (False, False)
    assert webstore.url_token() == ""
