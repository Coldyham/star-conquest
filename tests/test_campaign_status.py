"""The game's side of the weekly campaign (`starconquest.campaign`): reading the
board's answer about a node, counting its timers down, and saying so before
Start and in the top bar. The rules themselves are the board's
(`leaderboard/js/campaign.mjs`), tested there; this checks the game shows what
it was told, and never asks when it shouldn't.
"""

from __future__ import annotations

import json
import time



from starconquest import app, campaign, config, mapgen, pbp
from starconquest.geometry import WorldView
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


def confirmed(st: campaign.Status, now: float) -> tuple[str, list[str]]:
    shown = campaign.confirm(st, now)
    assert shown is not None
    return shown


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

    title, lines = confirmed(status(why="not-adjacent", can=False), t)
    assert title == "Campaign node 3"
    assert lines[0] == "A win here won't be a campaign move."
    assert "ordinary score" in lines[-1]
    title, lines = confirmed(status(why="home-taken", can=False, kind="home", node=11), t)
    assert title == "Campaign home 11" and "can't be taken" in lines[1]

    _, lines = confirmed(status(why="grace", graceUntil=NOW + 12 * MIN,
                                beat={"turns": 30, "lost": 2, "name": "bo"}), t)
    assert lines[0] == "Post a win within 12 min or it won't be a move."
    assert lines[-1] == "Held by bo: beat 30 turns, 2 lost."
    # A grace that has already run out locally is "not a move".
    _, lines = confirmed(status(why="grace", graceUntil=NOW + MIN), t + 120)
    assert lines[0] == "A win here won't be a campaign move."


def test_only_a_seeded_generated_setup_is_looked_up():
    assert campaign.lookup_token(Settings(seed=None)) is None
    assert campaign.lookup_token(Settings(seed=42)) == Settings(seed=42).to_token()


class FakeRequest:
    def __init__(self, url):
        self.url = url
        self.answer: tuple[str, str] = (pbp.PENDING, "")

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


def test_main_writes_the_label_each_frame_but_never_for_a_watched_replay(monkeypatch):
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    watch = campaign.Watcher()
    watch.status = status(why="grace", graceUntil=NOW + 30 * MIN, received=time.monotonic())
    monkeypatch.setattr(campaign, "enabled", lambda: False)   # pump is a no-op
    app.campaign_tick(ui, Settings(seed=42), watch)
    assert ui.campaign_label == "Node 3: post within 30 min"
    ui.watched = True
    app.campaign_tick(ui, Settings(seed=42), watch)
    assert ui.campaign_label is None


def test_a_game_started_after_losing_access_is_told_its_win_wont_count():
    late = status(why="late-start", can=False, graceUntil=NOW + 12 * MIN)
    assert campaign.label(late, 100.0) == "Node 3: not a move"
    _, lines = confirmed(late, 100.0)
    assert lines[0] == "A win here won't be a campaign move."
    assert "only covers a game started before that" in lines[1]


def test_the_stamp_taken_at_start_rides_on_the_posted_score(monkeypatch):
    settings = Settings(seed=42)
    state, ui, log = app.start_game(settings, 42, False)
    state.turn = 30            # a challenge with no turns reads as none at all
    watch = campaign.Watcher()
    watch.status = status(why="adjacent")
    app.stamp_campaign(ui, watch)
    assert ui.campaign_start == "adjacent"
    shared = app.challenge_settings(settings, state, ui, 42, log)
    assert shared.challenge is not None and shared.challenge.campaign == "adjacent"
    back = Settings.from_token(shared.to_token())
    assert back.challenge is not None and back.challenge.campaign == "adjacent"
    # Nothing looked up, nothing claimed.
    watch.status = None
    app.stamp_campaign(ui, watch)
    again = app.challenge_settings(settings, state, ui, 42, log).challenge
    assert again is not None and again.campaign == ""


def test_a_named_node_goes_by_its_crater():
    """A week whose nodes carry crater names (`tools/campaign.name_nodes`)
    says the name where it said "Node 3"; an older week keeps the number."""
    t = time.monotonic()
    body = json.loads(answer())
    body["node"]["name"] = "Mädler"
    named = campaign.parse(json.dumps(body), t)
    assert named.name == "Mädler"
    assert campaign.label(named, t) == "Mädler: open"
    late = campaign.parse(json.dumps({**body, "status": {"can": False, "why": "not-adjacent"}}), t)
    assert campaign.confirm(late, t)[0] == "Campaign: Mädler"
    shy = campaign.parse(json.dumps({**body, "status": {"can": False, "why": "no-name"}}), t)
    assert campaign.label(shy, t) == "Campaign: Mädler"
    assert campaign.parse(answer(), t).name == "Node 3"


def _near(node, x, y, why="adjacent", can=True, kind="field", holder=None, name=None):
    """A node of the answer's ``around``, as netlify/functions/campaign.mjs writes one."""
    return {"id": node, "kind": kind, **({"name": name} if name else {}), "x": x, "y": y,
            "holder": holder, "status": {"can": can, "why": why}, "settings": {"players": 2, "seed": 100 + node}}


def mine_around(why="own") -> campaign.Status:
    """Node 3, yours, with a neighbour on each side of it: an open field node
    east, one held by bo north, and bo's home west."""
    body = json.loads(answer(why=why, can=False))
    body["around"] = {
        "nodes": [_near(3, 100, 100, why=why, can=False, name="Mädler"),
                  _near(5, 200, 110),
                  _near(6, 90, 0, holder={"name": "bo", "turns": 30, "lost": 2}),
                  _near(9, 0, 100, why="has-home", can=False, kind="home", holder={"name": "bo", "turns": 9, "lost": 0})],
        "lanes": [[3, 5], [3, 6], [9, 3], [5, 6], [5, 77]]}
    parsed = campaign.parse(json.dumps(body), 0.0)
    assert parsed is not None
    return parsed


def test_the_answer_carries_the_nodes_neighbourhood():
    s = mine_around()
    assert [n.node_id for n in s.around] == [3, 5, 6, 9]
    assert s.lanes == ((3, 5), (3, 6), (9, 3), (5, 6))    # the lane to a node it didn't send is dropped
    assert (s.around[2].holder, s.around[2].beat) == ("bo", (30, 2))
    assert s.around[1].setup == {"players": 2, "seed": 105}
    assert set(campaign.playable(s)) == {5, 6}
    # A neighbourhood it can't read leaves the answer standing, without one.
    body = json.loads(answer())
    body["around"] = {"nodes": [{"id": "x"}]}
    bare = campaign.parse(json.dumps(body), 0.0)
    assert bare is not None and bare.around == () and campaign.playable(bare) == {}
    assert campaign.parse(answer(), 0.0).around == ()


def test_the_win_screen_draws_a_node_only_once_it_is_yours():
    assert campaign.graph(None) is None
    assert campaign.graph(mine_around(why="adjacent")) is None   # a win here, not yet posted
    assert campaign.graph(status()) is None                      # yours, but an older board sent no neighbours
    assert campaign.graph(mine_around(why="own-home")) is not None


def test_the_graph_lays_neighbours_out_as_on_the_map():
    g = campaign.graph(mine_around())
    assert g is not None
    at = {n["key"]: (n["col"], n["row"]) for n in g["nodes"]}
    # Middle; east; north; west. Screen y runs down, as on the campaign page.
    assert at == {"node_3": (1, 1), "node_5": (2, 1), "node_6": (1, 0), "node_9": (0, 1)}
    said = {n["key"]: (n["title"], n["status"], n["marks"], n["open"]) for n in g["nodes"]}
    assert said["node_3"] == ("Mädler", "yours", ["here"], True)
    assert said["node_5"] == ("Node 5", "open", ["move"], True)
    assert said["node_6"] == ("Node 6", "bo: beat 30t / 2 lost", ["move"], True)
    assert said["node_9"] == ("Home 9", "bo's home", [], False)
    # Lanes run left to right (down a column), without heads: a campaign lane runs both ways.
    assert [(lane["from"], lane["to"]) for lane in g["lanes"]] == [
        ("node_3", "node_5"), ("node_6", "node_3"), ("node_9", "node_3"), ("node_6", "node_5")]
    assert all(lane["plain"] for lane in g["lanes"])
    assert g["focus"] == "node_3"


def test_a_crowded_neighbourhood_takes_the_next_cell_round():
    body = json.loads(answer(why="own", can=False))
    body["around"] = {"nodes": [_near(0, 0, 0, why="own", can=False)]
                      + [_near(i, 10 + i, 0) for i in range(1, 11)], "lanes": []}
    g = campaign.graph(campaign.parse(json.dumps(body), 0.0))
    assert g is not None
    cells = [(n["col"], n["row"]) for n in g["nodes"]]
    assert len(set(cells)) == len(cells) == 11    # ten all due east: no two share a cell


def test_a_tap_picks_a_neighbour_and_a_second_plays_it():
    from starconquest import play
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    ui.campaign_status = mine_around()
    assert play.over_node(ui, 9) is None and ui.campaign_pick is None    # bo's home: not a move
    assert play.over_node(ui, 3) is None and ui.campaign_pick is None    # the node itself
    assert play.over_node(ui, 5) is None and ui.campaign_pick == 5
    g = campaign.graph(ui.campaign_status, ui.campaign_pick)
    assert g is not None and g["focus"] == "node_5"
    [picked] = [n for n in g["nodes"] if "picked" in n["marks"]]
    assert (picked["key"], picked["status"]) == ("node_5", "press again to play")
    assert play.over_node(ui, 5) == "campaign_play"


def test_a_post_asks_again_at_once_dropping_a_lookup_in_flight(monkeypatch):
    monkeypatch.setattr(campaign, "enabled", lambda: True)
    asked = []
    monkeypatch.setattr(campaign, "fetch", lambda token, name, start=None: asked.append(start) or FakeRequest(token))
    watch = campaign.Watcher()
    settings = Settings(seed=5)
    watch.pump(settings, now=0.0, start="")
    watch.pump(settings, now=campaign.SETTLE_S, start="")
    assert len(asked) == 1
    watch.ask_again()
    assert watch._request is None
    watch.pump(settings, now=campaign.SETTLE_S + 1, start="")
    assert len(asked) == 2      # long before REFRESH_S
