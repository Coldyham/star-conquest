"""The game's side of the weekly campaign (`starconquest.campaign`): reading the
board's answer about a node, counting its timers down, and saying so before
Start and in the top bar. The rules themselves are the board's
(`leaderboard/js/campaign.mjs`), tested there; this checks the game shows what
it was told, and never asks when it shouldn't.
"""

from __future__ import annotations

import json
import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from starconquest import campaign, config, main, mapgen, menu, pbp, render
from starconquest.geometry import WorldView
from starconquest.menu import MenuState
from starconquest.settings import Settings
from starconquest.viewstate import Ui

MIN = 60_000
NOW = 1_800_000_000_000.0   # the server's clock, ms


def answer(why="adjacent", can=True, kind="field", node=3, **status) -> str:
    """A body the way netlify/functions/campaign.mjs writes one."""
    body = {"week_start": "2026-09-28", "now": NOW,
            "node": {"id": node, "kind": kind}, "holder": None,
            "status": {"can": can, "why": why, "graceUntil": None, "beat": None, **status}}
    return json.dumps(body)


def status(received=100.0, **kw) -> campaign.Status:
    parsed = campaign.parse(answer(**kw), received)
    assert parsed is not None
    return parsed


def test_parse_reads_the_answer_and_refuses_anything_else():
    s = status(why="grace", graceUntil=NOW + 12 * MIN,
               beat={"turns": 30, "lost": 2, "name": "bo"})
    assert (s.node_id, s.kind, s.why, s.can) == (3, "field", "grace", True)
    assert s.grace_until == NOW + 12 * MIN
    assert s.beat == (30, 2, "bo")
    assert s.name == "Node 3"
    assert status(kind="home", node=11).name == "Home 11"
    for junk in ("", "not json", "[]", '{"error": "not a node"}', '{"node": 3, "status": {}}'):
        assert campaign.parse(junk, 0.0) is None


def test_timers_count_down_on_our_clock_from_when_the_answer_landed():
    s = status(received=100.0, why="grace", graceUntil=NOW + 30 * MIN)
    assert s.left_ms(s.grace_until, now=100.0) == 30 * MIN
    assert s.left_ms(s.grace_until, now=100.0 + 10 * 60) == 20 * MIN
    assert s.left_ms(None, now=100.0) is None
    assert campaign.minutes(20 * MIN) == "20 min"
    assert campaign.minutes(20 * MIN - 1) == "20 min"     # rounded up
    assert campaign.minutes(65 * MIN) == "1 h 05 min"


def test_the_top_bar_label_says_what_a_win_here_would_do():
    t = 100.0
    assert campaign.label(None) is None
    assert campaign.label(status(), t) == "Node 3: open"
    assert campaign.label(status(why="home-open", kind="home", node=11), t) == "Home 11: open"
    assert campaign.label(status(why="own", can=False), t) == "Node 3: yours"
    mine = {"turns": 25, "lost": 1, "name": "ann"}
    assert campaign.label(status(why="own", can=False, beat=mine), t) == "Node 3: yours, beat 25t / 1 lost"
    assert campaign.label(status(why="no-name", can=False), t) == "Campaign node 3"
    assert campaign.label(status(why="not-adjacent", can=False), t) == "Node 3: not a move"
    grace = status(why="grace", graceUntil=NOW + 12 * MIN)
    assert campaign.label(grace, t) == "Node 3: post within 12 min"
    assert campaign.label(grace, t + 5 * 60) == "Node 3: post within 7 min"
    # Twelve minutes on, the grace has run out here even before a refresh says so.
    assert campaign.label(grace, t + 13 * 60) == "Node 3: grace over"


def test_the_top_bar_label_carries_the_score_to_beat_on_a_held_node():
    t = 100.0
    held = {"turns": 30, "lost": 2, "name": "bo"}
    assert campaign.label(status(beat=held), t) == "Node 3: beat bo's 30t / 2 lost"
    assert campaign.label(status(beat={**held, "name": ""}), t) == "Node 3: beat 30t / 2 lost"
    grace = status(why="grace", graceUntil=NOW + 12 * MIN, beat=held)
    assert campaign.label(grace, t) == "Node 3: post within 12 min, beat 30t / 2 lost"
    assert campaign.label(grace, t + 13 * 60) == "Node 3: grace over"


def test_start_is_confirmed_only_when_a_win_wouldnt_count_or_is_on_the_clock():
    t = 100.0
    assert campaign.confirm(None) is None
    for quiet in (status(), status(why="own", can=False), status(why="own-home", can=False),
                  status(why="no-name", can=False), status(why="home-open", kind="home")):
        assert campaign.confirm(quiet, t) is None, quiet.why

    title, lines = campaign.confirm(status(why="not-adjacent", can=False), t)
    assert title == "Campaign node 3"
    assert lines[0] == "A win here won't be a campaign move."
    assert "ordinary score" in lines[-1]
    title, lines = campaign.confirm(status(why="home-taken", can=False, kind="home", node=11), t)
    assert title == "Campaign home 11" and "can't be taken" in lines[1]

    _, lines = campaign.confirm(status(why="grace", graceUntil=NOW + 12 * MIN,
                                       beat={"turns": 30, "lost": 2, "name": "bo"}), t)
    assert lines[0] == "Post a win within 12 min or it won't be a move."
    assert lines[-1] == "Held by bo: beat 30 turns, 2 lost."
    # A grace that has already run out locally is "not a move".
    _, lines = campaign.confirm(status(why="grace", graceUntil=NOW + MIN), t + 120)
    assert lines[0] == "A win here won't be a campaign move."


def test_only_a_seeded_generated_setup_is_looked_up():
    assert campaign.lookup_token(Settings(seed=None)) is None
    assert campaign.lookup_token(Settings(seed=42)) == Settings(seed=42).to_token()


class FakeRequest:
    def __init__(self, url):
        self.url = url
        self.answer = (pbp.PENDING, "")

    def poll(self):
        return self.answer


def _watching(monkeypatch, name="ann"):
    """A watcher on the web, with every request it starts recorded, not sent."""
    sent: list[FakeRequest] = []

    def fake(url, body=None):
        sent.append(FakeRequest(url))
        return sent[-1]

    monkeypatch.setattr(campaign, "enabled", lambda: True)
    monkeypatch.setattr(campaign.webstore, "leaderboard_url", lambda path: f"https://site{path}")
    monkeypatch.setattr(campaign.webstore, "pbp_name", lambda: name)
    monkeypatch.setattr(campaign.pbp, "request", fake)
    return campaign.Watcher(), sent


def test_the_watcher_asks_once_the_setup_settles_then_refreshes(monkeypatch):
    watch, sent = _watching(monkeypatch)
    settings = Settings(seed=42)
    watch.pump(settings, now=0.0)
    watch.pump(settings, now=campaign.SETTLE_S / 2)
    assert sent == []                       # still settling
    watch.pump(settings, now=campaign.SETTLE_S)
    assert len(sent) == 1
    assert sent[0].url.startswith("https://site/api/campaign?token=")
    assert sent[0].url.endswith("&name=ann&starting=1")   # from the menu: a game about to begin

    sent[0].answer = (pbp.OK, answer(why="grace", graceUntil=NOW + 30 * MIN))
    watch.pump(settings, now=1.0)
    assert watch.status is not None and watch.status.received == 1.0
    watch.pump(settings, now=1.0 + campaign.REFRESH_S - 1)
    assert len(sent) == 1
    watch.pump(settings, now=1.0 + campaign.REFRESH_S)
    assert len(sent) == 2

    # A failed refresh keeps the last answer; "not a node" clears it.
    sent[1].answer = (pbp.ERROR, "")
    watch.pump(settings, now=200.0)
    assert watch.status is not None
    watch.pump(settings, now=200.0 + campaign.REFRESH_S)
    sent[2].answer = (pbp.MISSING, '{"error": "not a node"}')
    watch.pump(settings, now=200.0 + campaign.REFRESH_S + 1)
    assert watch.status is None


def test_the_game_under_way_asks_with_its_stamp_and_a_new_stamp_asks_again(monkeypatch):
    watch, sent = _watching(monkeypatch)
    settings = Settings(seed=42)
    watch.pump(settings, now=0.0)
    watch.pump(settings, now=campaign.SETTLE_S)
    sent[0].answer = (pbp.OK, answer(why="adjacent"))
    watch.pump(settings, now=1.0)
    assert campaign.stamp(watch.status) == "adjacent"

    # Into the game, stamped: the answer stays, and the board is asked again at once.
    watch.pump(settings, now=2.0, start="adjacent")
    assert watch.status is not None and len(sent) == 2
    assert sent[1].url.endswith("&name=ann&start=adjacent")
    # An unstamped game says nothing about its start, which the board trusts.
    sent[1].answer = (pbp.OK, answer())
    watch.pump(settings, now=3.0, start="")
    assert len(sent) == 3 and sent[2].url.endswith("&name=ann")
    # Back on the menu, a game about to begin again.
    watch.pump(settings, now=4.0)
    assert len(sent) == 4 and sent[3].url.endswith("&starting=1")
    assert campaign.stamp(None) == ""


def test_a_changed_setup_drops_the_old_answer_at_once(monkeypatch):
    watch, sent = _watching(monkeypatch)
    watch.pump(Settings(seed=42), now=0.0)
    watch.pump(Settings(seed=42), now=1.0)
    sent[0].answer = (pbp.OK, answer(why="not-adjacent", can=False))
    watch.pump(Settings(seed=42), now=1.1)
    assert watch.status is not None
    watch.pump(Settings(seed=43), now=1.2)
    assert watch.status is None             # never confirm from another map's answer
    watch.pump(Settings(seed=None), now=10.0)
    assert len(sent) == 1                   # and an unseeded setup isn't asked about


def test_an_unchanged_setup_is_not_re_encoded_but_an_edit_in_place_is_seen(monkeypatch):
    watch, sent = _watching(monkeypatch)
    encoded = []
    real = campaign.lookup_token
    monkeypatch.setattr(campaign, "lookup_token", lambda s: encoded.append(s.seed) or real(s))
    settings = Settings(seed=42)
    for i in range(5):
        watch.pump(settings, now=i * 0.1)
    assert encoded == [42]
    watch.pump(settings, now=campaign.SETTLE_S)
    sent[0].answer = (pbp.OK, answer(why="not-adjacent", can=False))
    watch.pump(settings, now=campaign.SETTLE_S + 0.1)
    assert watch.status is not None
    settings.seed = 43                      # the menu edits its Settings in place
    watch.pump(settings, now=campaign.SETTLE_S + 0.2)
    assert encoded == [42, 43] and watch.status is None


def test_off_the_web_nothing_is_asked(monkeypatch):
    monkeypatch.setattr(campaign.pbp, "request", lambda *a: (_ for _ in ()).throw(AssertionError))
    watch = campaign.Watcher()
    for now in (0.0, 5.0, 500.0):
        watch.pump(Settings(seed=42), now=now)
    assert watch.status is None


def _menu():
    pygame.init()
    menu._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    return screen, MenuState(), Settings.defaults()


def _click(screen, ms, settings, key):
    menu.draw(screen, ms, settings)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=ms.rects[key].center, button=1)
    return menu.handle_event(ev, ms, settings)


def test_start_on_a_node_that_wouldnt_count_asks_first(monkeypatch):
    monkeypatch.setattr(menu, "setup_warnings", lambda settings, people=None: [])
    screen, ms, settings = _menu()
    try:
        ms.campaign_watch.status = status(why="not-adjacent", can=False)
        assert _click(screen, ms, settings, "start") is None
        assert ms.confirm_slow and ms.slow_title == "Campaign node 3"
        menu.draw(screen, ms, settings)            # the modal draws
        assert _click(screen, ms, settings, "slow_go") == "start"
    finally:
        pygame.quit()


def test_start_on_an_open_node_just_starts(monkeypatch):
    monkeypatch.setattr(menu, "setup_warnings", lambda settings, people=None: [])
    screen, ms, settings = _menu()
    try:
        ms.campaign_watch.status = status()
        assert _click(screen, ms, settings, "start") == "start"
    finally:
        pygame.quit()


def test_a_campaign_confirm_and_a_slow_warning_share_one_modal(monkeypatch):
    monkeypatch.setattr(menu, "setup_warnings",
                        lambda settings, people=None: ["Knower would search 3 turns deep, not 12"])
    screen, ms, settings = _menu()
    try:
        ms.campaign_watch.status = status(why="grace", graceUntil=NOW + 30 * MIN, received=time.monotonic())
        assert _click(screen, ms, settings, "start") is None
        assert ms.slow_lines[0].startswith("Post a win within")
        assert ms.slow_lines[-1].startswith("Knower")
        # The slow warning alone keeps its own heading.
        ms.confirm_slow = False
        ms.campaign_watch.status = None
        assert _click(screen, ms, settings, "start") is None
        assert ms.slow_title == ""
    finally:
        pygame.quit()


def test_the_top_bar_shows_the_label_beside_the_challenge_target():
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
        ui.visible = ui.seen = set(state.systems)
        w = screen.get_width()
        assert render._draw_campaign_label(screen, ui, w, w) == w      # nothing to show
        ui.campaign_label = "Node 3: post within 12 min"
        alone = render._draw_campaign_label(screen, ui, w, w)
        assert alone < w - config.HUD_PAD
        ui.challenge_target = (40, 3)
        target = render._draw_challenge_target(screen, ui, w)
        assert render._draw_campaign_label(screen, ui, w, target) < target
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_main_writes_the_label_each_frame_but_never_for_a_watched_replay(monkeypatch):
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    watch = campaign.Watcher()
    watch.status = status(why="grace", graceUntil=NOW + 30 * MIN, received=time.monotonic())
    monkeypatch.setattr(campaign, "enabled", lambda: False)   # pump is a no-op
    main.campaign_tick(ui, Settings(seed=42), watch)
    assert ui.campaign_label == "Node 3: post within 30 min"
    ui.watched = True
    main.campaign_tick(ui, Settings(seed=42), watch)
    assert ui.campaign_label is None


def test_a_game_started_after_losing_access_is_told_its_win_wont_count():
    late = status(why="late-start", can=False, graceUntil=NOW + 12 * MIN)
    assert campaign.label(late, 100.0) == "Node 3: not a move"
    _, lines = campaign.confirm(late, 100.0)
    assert lines[0] == "A win here won't be a campaign move."
    assert "only covers a game started before that" in lines[1]


def test_the_stamp_taken_at_start_rides_on_the_posted_score(monkeypatch):
    settings = Settings(seed=42)
    state, ui, log = main.start_game(settings, 42, False)
    state.turn = 30            # a challenge with no turns reads as none at all
    watch = campaign.Watcher()
    watch.status = status(why="adjacent")
    main.stamp_campaign(ui, watch)
    assert ui.campaign_start == "adjacent"
    shared = main.challenge_settings(settings, state, ui, 42, log)
    assert shared.challenge is not None and shared.challenge.campaign == "adjacent"
    back = Settings.from_token(shared.to_token())
    assert back.challenge is not None and back.challenge.campaign == "adjacent"
    # Nothing looked up, nothing claimed.
    watch.status = None
    main.stamp_campaign(ui, watch)
    assert main.challenge_settings(settings, state, ui, 42, log).challenge.campaign == ""
