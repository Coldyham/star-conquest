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


def test_a_best_filed_under_a_legacy_key_is_still_yours():
    webstore.record_best("old", 137, 412)
    assert webstore.best("new", "old") == (137, 412)


def test_a_worse_result_cannot_replace_a_legacy_best():
    webstore.record_best("old", 137, 412)
    assert webstore.record_best("new", 150, 10, "old") is False
    assert webstore.best("new", "old") == (137, 412)


def test_beating_a_legacy_best_files_under_the_canonical_key():
    webstore.record_best("old", 137, 412)
    assert webstore.record_best("new", 120, 5, "old")
    assert webstore.best("new") == (120, 5)
    assert webstore.best("old") == (137, 412)   # append-only: the old entry stands


def test_corrupt_bests_blob_does_not_lose_a_new_result(isolated_store):
    isolated_store.write_text(json.dumps({WEB_BESTS_KEY: "not an object"}))
    assert webstore.best("key") is None
    assert webstore.record_best("key", 5, 5)
    assert webstore.best("key") == (5, 5)


def test_web_only_helpers_are_no_ops_off_the_browser():
    assert webstore.share_token("abc") == (False, False)
    assert webstore.url_token() == ""
    assert webstore.close_window() is False
    assert webstore.link_url("abc") == ""
    assert webstore.set_url_fragment("abc") is False
    assert webstore.copy_to_clipboard("abc") is False
    assert webstore.copy_link("abc") is False


def test_copy_link_never_stores_the_token(monkeypatch):
    """A challenge token must not be persisted: it would be read back at the next
    launch and its score-to-beat banner would haunt every later session. Unlike
    `share_token`, `copy_link` touches the clipboard and nothing else."""
    stored, urls = {}, []
    monkeypatch.setattr(webstore, "is_web", lambda: True)
    monkeypatch.setattr(webstore, "link_url", lambda t: f"https://x/#{t}")
    monkeypatch.setattr(webstore, "copy_to_clipboard", lambda text: True)
    monkeypatch.setattr(webstore, "set_url_fragment", lambda t: urls.append(t) or True)
    monkeypatch.setattr(webstore, "set", lambda k, v: stored.setdefault(k, v) or True)

    assert webstore.copy_link("challenge-token") is True
    assert stored == {}, "copy_link must not write to storage"
    assert urls == [], "copy_link must not touch the address bar"


def test_copy_link_reports_failure_when_the_clipboard_is_refused(monkeypatch):
    monkeypatch.setattr(webstore, "is_web", lambda: True)
    monkeypatch.setattr(webstore, "link_url", lambda t: f"https://x/#{t}")
    monkeypatch.setattr(webstore, "copy_to_clipboard", lambda text: False)
    assert webstore.copy_link("tok") is False


def test_quitting_ends_the_loop_off_the_web():
    """Off the browser a confirmed quit really does exit."""
    import main

    assert main.leave_app() is True


def test_quitting_in_the_browser_keeps_the_app_alive(monkeypatch):
    """In the browser, ending the loop would run pygame.quit() and leave the player
    on a dead black canvas that only a force-close escapes. So a web quit asks the
    browser to close the window and reports that we're still running, letting main
    fall back to the setup menu instead of tearing the display down."""
    import main

    tried = []
    monkeypatch.setattr(main.paths, "is_web", lambda: True)
    monkeypatch.setattr(main.webstore, "close_window", lambda: tried.append(True) or True)
    assert main.leave_app() is False
    assert tried == [True], "a web quit must at least attempt to close the window"
