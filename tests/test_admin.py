"""The moderation CLI's plans, against a recording stand-in for Supabase.

No network: every command decides what it will do from rows it has read, and
the stand-in hands those rows back and records every write in order, which is
what these assert on.
"""

from __future__ import annotations

import pytest

from starconquest import pbp
from tools import admin

MATCH = "f7697d6f02fbc2e9"
STAMP = "2026-09-24T10:14:14.123456+00:00"


class FakeApi:
    """Answers each select with the first canned rows whose table matches and
    whose marker appears in the query; records every write."""

    def __init__(self, answers: list[tuple[str, str, list[dict]]], *, stale: bool = False):
        self.answers = answers
        self.stale = stale
        self.writes: list[tuple] = []

    def select(self, table, query):
        for name, marker, rows in self.answers:
            if name == table and marker in query:
                return [dict(r) for r in rows]
        return []

    def insert(self, table, rows):
        self.writes.append(("insert", table, rows))

    def update(self, table, query, patch):
        self.writes.append(("update", table, query, patch))
        return [] if self.stale else [patch]

    def delete(self, table, query):
        self.writes.append(("delete", table, query))


def _match(**over):
    row = {"match_id": MATCH, "public": True, "finished": False, "turn": 4,
           "updated_at": STAMP,
           "seats": {"seats": [1, 2, 3], "tokens": {"1": "h1", "2": "h2"}}}
    return {**row, **over}


def _api(match=None, **kw):
    return FakeApi([("pbp_matches", "", [match or _match()])], **kw)


def test_a_token_hashes_exactly_as_the_endpoint_does():
    # The vector is `pbp.mjs`'s own `hashToken` of the same string.
    assert admin.hash_token("0123456789abcdef0123456789abcdef") == (
        "3eb1bd439947eb762998e566ccc2e099c791118b2f40579cc4f7da2b5061b7f9")
    assert pbp.valid_token(admin.mint_token())


def test_a_dry_run_writes_nothing(capsys):
    api = _api()
    plan = admin.plan_new_link(api, MATCH, 2, admin.GAME_URL)
    assert admin.apply(api, plan, yes=False, reason="") == 0
    assert api.writes == []
    assert "dry run" in capsys.readouterr().out


def test_a_new_link_replaces_the_seats_token_and_prints_one_that_opens_it(capsys):
    api = _api()
    admin.apply(api, admin.plan_new_link(api, MATCH, 2, admin.GAME_URL),
                yes=True, reason="lost their link")
    audit, write = api.writes
    assert audit[:2] == ("insert", "admin_actions")
    assert audit[2][0]["reason"] == "lost their link"

    _, table, query, patch = write
    assert table == "pbp_matches"
    assert "updated_at=eq.2026-09-24T10%3A14%3A14.123456%2B00%3A00" in query
    link = next(line for line in capsys.readouterr().out.splitlines() if "#pbp=" in line)
    match_id, token = pbp.parse_link(link.split("#", 1)[1])
    assert match_id == MATCH
    assert patch["seats"]["tokens"] == {"1": "h1", "2": admin.hash_token(token)}
    assert token not in str(audit), "the plaintext token must never be stored"


def test_a_new_link_on_an_open_seat_claims_it_privately():
    api = _api()
    admin.apply(api, admin.plan_new_link(api, MATCH, 3, admin.GAME_URL), yes=True, reason="")
    assert set(api.writes[1][3]["seats"]["tokens"]) == {"1", "2", "3"}


def test_opening_a_seat_forgets_only_its_token():
    api = _api()
    admin.apply(api, admin.plan_open_seat(api, MATCH, 2, publish=False), yes=True, reason="")
    patch = api.writes[1][3]
    assert patch["seats"] == {"seats": [1, 2, 3], "tokens": {"1": "h1"}}
    assert "public" not in patch
    assert admin.open_seats({"seats": patch["seats"]}) == [2, 3]


def test_a_private_match_is_only_opened_if_it_is_listed_too():
    api = _api(_match(public=False))
    with pytest.raises(admin.Refused, match="--publish"):
        admin.plan_open_seat(api, MATCH, 2, publish=False)
    admin.apply(api, admin.plan_open_seat(api, MATCH, 2, publish=True), yes=True, reason="")
    assert api.writes[1][3]["public"] is True


@pytest.mark.parametrize(("match", "seat", "why"), [
    (_match(), 3, "already open"),
    (_match(), 5, "not a person's seat"),
    (_match(finished=True), 2, "over"),
])
def test_a_seat_that_cannot_be_opened_is_refused(match, seat, why):
    with pytest.raises(admin.Refused, match=why):
        admin.plan_open_seat(_api(match), MATCH, seat, publish=False)


def test_a_seat_write_that_lost_a_race_is_refused():
    api = _api(stale=True)
    with pytest.raises(admin.Refused, match="moved"):
        admin.apply(api, admin.plan_open_seat(api, MATCH, 1, publish=False),
                    yes=True, reason="")


def test_a_game_is_deleted_after_everything_that_references_it():
    api = FakeApi([
        ("games", "", [{"game_key": "abc", "settings_json": {}}]),
        ("scores", "", [{"id": 7, "game_key": "abc", "turns": 30}]),
        ("config_tags", "", [{"id": 1, "score_id": 7, "tag_key": "fun"}]),
        ("bot_scores", "", [{"game_key": "abc", "bot": "marshal"}]),
        ("game_logs", "", [{"id": 3, "match_id": "0" * 16, "turns": 30}]),
    ])
    admin.apply(api, admin.plan_delete_game(api, "abc"), yes=True, reason="")
    deleted = [w[1] for w in api.writes if w[0] == "delete"]
    assert deleted == ["scores", "bot_scores", "game_logs", "games"]
    assert api.writes[0][2][0]["detail"]["config_tags"][0]["tag_key"] == "fun"


def test_a_tag_is_removed_from_its_rows_and_from_old_named_configs():
    api = FakeApi([
        ("config_tags", "", [{"id": 1, "config_key": "a" * 16, "tag_key": "rude"}]),
        ("configs", "tags=cs.", [{"config_key": "b" * 16, "tags": ["fun", "rude"]}]),
    ])
    admin.apply(api, admin.plan_delete_tag(api, " Rude ", None), yes=True, reason="")
    assert ("delete", "config_tags", "tag_key=eq.rude") in api.writes
    assert api.writes[-1] == ("update", "configs", f"config_key=eq.{'b' * 16}", {"tags": ["fun"]})


def test_a_rename_onto_someone_elses_name_is_refused():
    api = FakeApi([
        ("users", "name_key=eq.old", [{"id": 1, "name": "Old", "name_key": "old"}]),
        ("users", "name_key=eq.taken", [{"id": 2}]),
    ])
    with pytest.raises(admin.Refused, match="already"):
        admin.plan_rename_user(api, "Old", "Taken")
    admin.plan_rename_user(api, "Old", "OLD")  # a change of case is still yours


def test_deleting_a_score_that_does_not_exist_is_refused():
    api = FakeApi([("scores", "", [{"id": 1, "game_key": "abc", "turns": 9}])])
    with pytest.raises(admin.Refused, match="2"):
        admin.plan_delete_score(api, [1, 2])
