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


class FakeApi(admin.AdminApi):
    """Answers each select with the first canned rows whose table matches and
    whose marker appears in the query; records every write. Its blank url makes
    anything not stood in for here fail rather than reach a network."""

    def __init__(self, answers: list[tuple[str, str, list[dict]]], *, stale: bool = False):
        super().__init__(url="", key="")
        self.answers = answers
        self.stale = stale
        self.writes: list[tuple] = []

    def select(self, table: str, query: str) -> list[dict]:
        for name, marker, rows in self.answers:
            if name == table and marker in query:
                return [dict(r) for r in rows]
        return []

    def insert(self, table: str, rows: list[dict]) -> None:
        self.writes.append(("insert", table, rows))

    def update(self, table: str, query: str, patch: dict) -> list[dict]:
        self.writes.append(("update", table, query, patch))
        return [] if self.stale else [patch]

    def delete(self, table: str, query: str) -> None:
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
    parsed = pbp.parse_link(link.split("#", 1)[1])
    assert parsed is not None
    match_id, token = parsed
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


# --------------------------------------------------------------------------- #
# Claimed names
# --------------------------------------------------------------------------- #
class AuthFakeApi(FakeApi):
    """FakeApi plus Auth's account list, by email."""

    def __init__(self, answers, accounts=None, **kw):
        super().__init__(answers, **kw)
        self.accounts = accounts or {}

    def auth_user(self, email: str) -> dict | None:
        uid = self.accounts.get(email.strip().lower())
        return {"id": uid, "email": email} if uid else None


ANN = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _named(owner=None, **over):
    return {"id": 7, "name": "Ann", "name_key": "ann", "owner": owner,
            "claimed": owner is not None, **over}


def test_assigning_a_used_name_is_a_dry_run_until_yes(capsys):
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named()]),
                       ("scores", "user_id=eq.7", [{"id": 1}, {"id": 2}])],
                      {"ann@example.com": ANN})
    plan = admin.plan_assign_name(api, " ann ", "Ann@Example.com")
    admin.apply(api, plan, yes=False, reason="")
    assert api.writes == []
    assert "2 score(s)" in capsys.readouterr().out


def test_assigning_a_used_name_audits_first_and_sets_the_owner_conditionally():
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named()])], {"ann@example.com": ANN})
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com"),
                yes=True, reason="their old name")
    audit, write = api.writes
    assert audit[:2] == ("insert", "admin_actions")
    assert audit[2][0]["detail"]["owner"] == ANN
    assert "example.com" not in str(audit), "the audit row names the account by id only"
    assert write == ("update", "users", "id=eq.7&owner=is.null", {"owner": ANN})


def test_assigning_a_name_nobody_has_used_creates_it_owned():
    api = AuthFakeApi([], {"ann@example.com": ANN})
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com"), yes=True, reason="")
    assert api.writes[-1] == ("insert", "users", [{"name": "Ann", "owner": ANN}])


def test_assigning_to_an_account_that_never_signed_in_is_refused():
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named()])])
    with pytest.raises(admin.Refused, match="sign in"):
        admin.plan_assign_name(api, "Ann", "ann@example.com")


def test_an_account_owns_one_name():
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named()]),
                       ("users", f"owner=eq.{ANN}", [{"id": 9, "name": "Annie"}])],
                      {"ann@example.com": ANN})
    with pytest.raises(admin.Refused, match="Annie"):
        admin.plan_assign_name(api, "Ann", "ann@example.com")


def test_a_name_someone_else_owns_must_be_released_first():
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named(BOB)])], {"ann@example.com": ANN})
    with pytest.raises(admin.Refused, match="release-name"):
        admin.plan_assign_name(api, "Ann", "ann@example.com")


def test_assigning_a_name_its_owner_already_has_does_nothing(capsys):
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named(ANN)]),
                       ("users", f"owner=eq.{ANN}", [{"id": 7, "name": "Ann"}])],
                      {"ann@example.com": ANN})
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com"), yes=True, reason="")
    assert api.writes == []
    assert "nothing to do" in capsys.readouterr().out


def test_a_name_that_changed_hands_mid_assign_is_refused():
    api = AuthFakeApi([("users", "name_key=eq.ann", [_named()])], {"ann@example.com": ANN},
                      stale=True)
    with pytest.raises(admin.Refused, match="changed hands"):
        admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com"),
                    yes=True, reason="")


def test_releasing_a_name_clears_its_owner():
    api = FakeApi([("users", "name_key=eq.ann", [_named(ANN)])])
    admin.apply(api, admin.plan_release_name(api, "ANN"), yes=True, reason="asked")
    assert api.writes[-1] == ("update", "users", f"id=eq.7&owner=eq.{ANN}", {"owner": None})
    with pytest.raises(admin.Refused, match="not claimed"):
        admin.plan_release_name(FakeApi([("users", "", [_named()])]), "Ann")


def test_a_rename_carries_the_owner_with_it():
    """rename-user patches the name alone, so a claimed name stays claimed."""
    api = FakeApi([("users", "name_key=eq.ann&", [_named(ANN)])])
    admin.apply(api, admin.plan_rename_user(api, "Ann", "Annabel"), yes=True, reason="")
    assert api.writes[-1] == ("update", "users", "id=eq.7", {"name": "Annabel"})


# --------------------------------------------------------------------------- #
# Folding several used names into the one claimed name
# --------------------------------------------------------------------------- #
def _andy(owner=None):
    return {"id": 8, "name": "andy", "name_key": "andy", "owner": owner, "claimed": bool(owner)}


def _fold_api(andy_owner=None, owned=(), kept=True, **kw):
    answers = [
        ("users", "name_key=eq.andy&", [_andy(andy_owner)]),
        ("users", "name_key=eq.aj&", [{"id": 9, "name": "AJ", "name_key": "aj", "owner": None}]),
        ("users", f"owner=eq.{ANN}", list(owned)),
        ("scores", "user_id=eq.8", [{"id": 31}, {"id": 32}]),
        ("scores", "user_id=eq.9", [{"id": 33}]),
        ("config_tags", "user_id=eq.8", [{"id": 41}]),
    ]
    if kept:
        answers.insert(0, ("users", "name_key=eq.ann&", [_named()]))
    return AuthFakeApi(answers, {"ann@example.com": ANN}, **kw)


def test_folding_moves_every_score_and_tag_then_frees_the_old_names():
    api = _fold_api()
    plan = admin.plan_assign_name(api, "Ann", "ann@example.com", ["andy", "AJ", "ANDY"])
    admin.apply(api, plan, yes=True, reason="one person")
    audit, *writes = api.writes
    detail = audit[2][0]["detail"]
    assert [r["name"] for r in detail["folded"]] == ["andy", "AJ"], "a repeat folds once"
    assert detail["moved"]["8"] == {"scores": [31, 32], "config_tags": [41]}
    assert detail["moved"]["9"] == {"scores": [33], "config_tags": []}
    assert writes == [
        ("update", "scores", "user_id=in.(8,9)", {"user_id": 7}),
        ("update", "config_tags", "user_id=in.(8,9)", {"user_id": 7}),
        ("delete", "users", "id=in.(8,9)"),
        ("update", "users", "id=eq.7&owner=is.null", {"owner": ANN}),
    ]


def test_an_old_name_the_account_owns_lets_go_before_the_kept_one_is_claimed():
    api = _fold_api(andy_owner=ANN, owned=[{"id": 8, "name": "andy"}])
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com", ["andy"]),
                yes=True, reason="")
    writes = api.writes[1:]
    assert writes[0] == ("update", "users", f"id=eq.8&owner=eq.{ANN}", {"owner": None})
    assert writes[-1] == ("update", "users", "id=eq.7&owner=is.null", {"owner": ANN})


def test_the_one_name_refusal_points_at_fold():
    api = _fold_api(owned=[{"id": 8, "name": "andy"}])
    with pytest.raises(admin.Refused, match="--fold"):
        admin.plan_assign_name(api, "Ann", "ann@example.com")


def test_folding_someone_elses_name_or_a_missing_one_or_itself_is_refused():
    with pytest.raises(admin.Refused, match="another account"):
        admin.plan_assign_name(_fold_api(andy_owner=BOB), "Ann", "ann@example.com", ["andy"])
    with pytest.raises(admin.Refused, match="no such user"):
        admin.plan_assign_name(_fold_api(), "Ann", "ann@example.com", ["nobody"])
    with pytest.raises(admin.Refused, match="itself"):
        admin.plan_assign_name(_fold_api(), "Ann", "ann@example.com", [" ANN "])


def test_a_fold_dry_run_writes_nothing_and_counts_what_would_move(capsys):
    api = _fold_api()
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com", ["andy"]),
                yes=False, reason="")
    assert api.writes == []
    out = capsys.readouterr().out
    assert "fold 'andy' (2 score(s), 1 tag(s))" in out and "free for anyone" in out


def test_folding_into_a_name_nobody_has_used_makes_it_first():
    api = _fold_api(kept=False)
    plan = admin.plan_assign_name(api, "Ann", "ann@example.com", ["andy"])
    # The kept row exists once the insert has run: hand it back from then on.
    real_insert = api.insert

    def insert(table, rows):
        real_insert(table, rows)
        api.answers.insert(0, ("users", "name_key=eq.ann&", [_named(ANN)]))

    api.insert = insert
    admin.apply(api, plan, yes=True, reason="")
    writes = api.writes[1:]
    assert writes[0] == ("insert", "users", [{"name": "Ann", "owner": ANN}])
    assert writes[1] == ("update", "scores", "user_id=in.(8)", {"user_id": 7})
    assert writes[-1] == ("delete", "users", "id=in.(8)")


def test_folding_into_a_name_already_theirs_still_folds():
    api = _fold_api()
    api.answers[0] = ("users", "name_key=eq.ann&", [_named(ANN)])
    api.answers[3] = ("users", f"owner=eq.{ANN}", [{"id": 7, "name": "Ann"}])
    admin.apply(api, admin.plan_assign_name(api, "Ann", "ann@example.com", ["andy"]),
                yes=True, reason="")
    assert api.writes[-1] == ("delete", "users", "id=in.(8)")


def test_fold_takes_several_names_and_repeats():
    args = admin.parse_args(["assign-name", "A", "a@x", "--fold", "b", "c", "--fold", "d"])
    assert args.fold == ["b", "c", "d"]
    assert admin.parse_args(["assign-name", "A", "a@x"]).fold == []

def test_delete_campaign_keeps_the_graph_on_the_audit_row_and_drops_only_the_week():
    row = {"week_start": "2026-10-05", "graph": {"nodes": [{"id": 0}, {"id": 1}]}}
    api = FakeApi([("campaigns", "week_start=eq.2026-10-05", [row]),
                   ("campaign_games", "", [{"node_id": 1, "game_key": "g"}])])
    plan = admin.plan_delete_campaign(api, "2026-10-05")
    assert plan.detail["campaign"] == row
    admin.apply(api, plan, yes=True, reason="120-system nodes")
    assert api.writes[0][:2] == ("insert", "admin_actions")
    assert api.writes[1:] == [("delete", "campaigns", "week_start=eq.2026-10-05")]


@pytest.mark.parametrize("week", ["2026-10-06", "last week"])
def test_delete_campaign_wants_a_monday(week):
    with pytest.raises(admin.Refused):
        admin.plan_delete_campaign(FakeApi([]), week)


def test_delete_campaign_refuses_a_week_that_has_none():
    with pytest.raises(admin.Refused):
        admin.plan_delete_campaign(FakeApi([]), "2026-10-05")
