"""The worker host: Python does no I/O, it queues effects and takes replies.

Drives the bridges the way the HTML build's page does (`hostio.boot`, then a
command, then `hostio.drain` and `hostio.reply`), with nothing but dicts.
"""

from __future__ import annotations

import pytest

from starconquest import hostio, pbp, share, webstore
from starconquest.paths import WEB_ANIMATE_TURNS_KEY

PAGE = {"origin": "https://deploy-preview-9--star-conquest.netlify.app",
        "hostname": "deploy-preview-9--star-conquest.netlify.app",
        "pathname": "/next/", "hash": "#abc"}


@pytest.fixture(autouse=True)
def page():
    hostio.boot(PAGE, {WEB_ANIMATE_TURNS_KEY: "0"})
    yield
    hostio.reset()


def test_storage_reads_the_mirror_and_writes_through_as_an_effect():
    assert webstore.get(WEB_ANIMATE_TURNS_KEY) == "0"
    assert webstore.animate_turns() is False
    assert webstore.set_animate_turns(True)
    assert webstore.get(WEB_ANIMATE_TURNS_KEY) == "1"
    assert hostio.drain() == [{"t": "store.set", "key": WEB_ANIMATE_TURNS_KEY, "value": "1"}]


def test_another_tabs_write_reaches_the_mirror():
    hostio.store_changed(WEB_ANIMATE_TURNS_KEY, "1")
    assert webstore.animate_turns() is True
    hostio.store_changed(WEB_ANIMATE_TURNS_KEY, None)
    assert webstore.get(WEB_ANIMATE_TURNS_KEY) == ""


def test_the_page_address_comes_from_boot():
    assert webstore.leaderboard_origin() == "https://" + PAGE["hostname"]
    assert webstore.link_url("tok") == PAGE["origin"] + "/next/#tok"
    assert webstore.url_token() == "abc"
    hostio.set_hash("#def")
    assert webstore.url_token() == "def"


def test_a_challenge_link_goes_to_the_clipboard_and_nowhere_else():
    assert webstore.copy_link("tok")
    assert hostio.drain() == [{"t": "clipboard", "text": PAGE["origin"] + "/next/#tok"}]


def test_fragment_and_navigation_are_effects():
    assert webstore.set_url_fragment("xyz")
    assert webstore.open_url("https://example.invalid/board/")
    assert hostio.drain() == [{"t": "fragment", "token": "xyz"},
                              {"t": "navigate", "url": "https://example.invalid/board/"}]
    assert webstore.url_token() == "xyz"


def test_the_pygame_only_calls_answer_no():
    assert webstore.close_window() is False
    assert webstore.trigger_resize() is False
    assert webstore.viewport() is None
    assert hostio.drain() == []


def test_a_request_waits_for_its_reply():
    req = pbp.request("https://x.invalid/api/pbp?action=state", '{"a":1}', signed=True)
    assert req is not None and req.poll() == (pbp.PENDING, "")
    [effect] = hostio.drain()
    assert effect == {"t": "http", "req": effect["req"], "url": "https://x.invalid/api/pbp?action=state",
                      "body": '{"a":1}', "signed": True}
    assert hostio.reply(effect["req"], pbp.REFUSED, "not your seat")
    assert req.poll() == (pbp.REFUSED, "not your seat")
    assert not hostio.reply(effect["req"], pbp.OK, "again")


def test_requests_answered_out_of_order_reach_their_own_callers():
    a = pbp.request("https://x.invalid/a")
    b = pbp.request("https://x.invalid/b")
    ea, eb = hostio.drain()
    hostio.reply(eb["req"], pbp.OK, "B")
    hostio.reply(ea["req"], pbp.OK, "A")
    assert a is not None and b is not None
    assert (a.poll(), b.poll()) == ((pbp.OK, "A"), (pbp.OK, "B"))


def test_a_replay_download_keeps_its_three_answers():
    match_id = "0123456789abcdef"
    for state, want in ((pbp.OK, (share.OK, "{}")), (pbp.MISSING, (share.MISSING, "")),
                        (pbp.REFUSED, (share.ERROR, "")), (pbp.ERROR, (share.ERROR, ""))):
        dl = share.fetch_log(match_id)
        assert dl is not None
        [effect] = hostio.drain()
        assert effect["t"] == "http" and effect["url"].endswith(f"?id={match_id}")
        hostio.reply(effect["req"], state, "{}")
        assert dl.poll() == want


def test_quick_post_and_the_signed_in_name_go_through_the_bridge():
    asked = share.signed_in_name()
    posted = share.quick_post("tok", "Ada")
    assert asked is not None and posted is not None
    ask, post = hostio.drain()
    module = "https://" + PAGE["hostname"] + "/board/js/post.mjs"
    assert (ask["t"], ask["module"], ask["fn"], ask["args"]) == ("bridge", module, "ownName", [])
    assert (post["fn"], post["args"]) == ("quickPost", ["tok", "Ada"])
    hostio.reply(ask["req"], pbp.OK, "Ada")
    assert asked.poll() == (pbp.OK, "Ada")


def test_a_replay_upload_is_fired_and_forgotten(monkeypatch):
    monkeypatch.setattr(share, "worth_sending", lambda log: True)
    monkeypatch.setattr(share, "row_for", lambda log, key: {"match_id": "m", "key": key})
    assert share.post_log(object(), "k")  # pyright: ignore[reportArgumentType]
    [effect] = hostio.drain()
    assert effect["t"] == "fire" and effect["url"] == share.log_url()
    assert '"key": "k"' in effect["body"]


def test_nothing_unknown_is_ever_emitted():
    with pytest.raises(AssertionError):
        hostio.emit("eval", js="alert(1)")


def test_off_the_worker_nothing_is_queued():
    hostio.reset()
    assert not hostio.in_worker()
    assert pbp.bridge("m", "ownName", []) is None
    assert hostio.drain() == []
