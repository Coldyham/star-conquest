"""Dump real challenge tokens as test fixtures for the leaderboard's JS decoder.

    uv run python tools/dump_challenge_fixtures.py

Writes leaderboard/tests/fixtures/tokens.json: tokens straight out of
``Settings.to_token`` paired with what the JS side must decode them to. The
leaderboard reimplements the token format in JavaScript, so this is what keeps
the two encoders honest — re-run it if the format in ``settings.py`` changes and
commit the result.

The ``expect`` shapes are deliberately only the fields the leaderboard reads, not
a full ``Settings`` round-trip: the JS decoder is a reader of four identity
fields plus the score, not a port of ``from_dict``.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest.settings import Challenge, Settings  # noqa: E402 — needs the path above

OUT = ROOT / "leaderboard" / "tests" / "fixtures" / "tokens.json"


def expect_for(settings: Settings) -> dict:
    """What the JS decoder must return for ``settings``' token."""
    challenge = settings.challenge
    assert challenge is not None
    return {
        "mode": settings.mode,
        "players": settings.players,
        "nodes": settings.nodes,
        "seed": settings.seed,
        "challenge": {
            "turns": challenge.turns,
            "lost": challenge.lost,
            "hand": challenge.hand,
            "by": challenge.by,
            "log": challenge.log,
        },
        "gameKey": challenge.key,
    }


def stamped(settings: Settings, **score) -> Settings:
    """``settings`` carrying a challenge keyed to itself, as main.py builds one."""
    settings.challenge = Challenge(**score)
    settings.challenge.key = settings.challenge_key()
    return settings


def uncompressed_token(settings: Settings) -> str:
    """The pre-compression token form: plain JSON, base64url, no deflate."""
    raw = json.dumps(settings.token_dict(), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def main() -> None:
    cases = []

    # Placeholder sender names, never real ones: these get committed.
    plain = stamped(Settings(seed=42), turns=25, lost=2, hand=25, by="name")
    cases.append({"name": "defaults", "token": plain.to_token(), "expect": expect_for(plain)})

    tuned = Settings(mode="symmetric", players=4, nodes=28, seed=99991)
    tuned.combat_jitter = 0.25
    tuned.defender_advantage = 1.5
    tuned.ship_speed_growth_pct = 4.0
    tuned.ai_strategy[1] = "marshal"
    tuned.ai[1].aux = 2.0
    tuned = stamped(tuned, turns=61, lost=140, hand=12, by="Someone Else")
    cases.append({"name": "tuned-knobs", "token": tuned.to_token(), "expect": expect_for(tuned)})

    # A score carrying the id of its uploaded replay (Challenge.log), which is
    # what tools/verify_scores.py keys on. The plain and tuned cases above have
    # none, pinning the other half of the rule: a token minted before the field
    # existed still decodes, to a blank.
    logged = stamped(Settings(nodes=20, seed=808), turns=33, lost=7, hand=33,
                     by="Logged", log="00112233445566ff")
    cases.append({"name": "with-log", "token": logged.to_token(),
                  "expect": expect_for(logged)})

    # A malformed id must not reach the database column the verifier keys on.
    forged = Settings(seed=55)
    forged.challenge = Challenge(turns=12, lost=1, hand=12, by="", log="'; drop table--")
    forged.challenge.key = forged.challenge_key()
    cases.append({"name": "bad-log-id", "token": forged.to_token(),
                  "expect": {**expect_for(forged), "challenge":
                             {"turns": 12, "lost": 1, "hand": 12, "by": "", "log": ""}}})

    # The pre-compression form real early links used; from_token still reads it.
    legacy = stamped(Settings(players=2, nodes=9, seed=7), turns=14, lost=0, hand=14, by="")
    cases.append({"name": "legacy-uncompressed", "token": uncompressed_token(legacy),
                  "expect": expect_for(legacy)})

    # A hand-written challenge with no key: Challenge.matches takes a blank key on
    # trust, so the decoder must fall back to hashing the setup itself. That hash
    # is JS-internal (Python and JS disagree on integral floats like 1.0 vs 1), so
    # pin only its shape, never a cross-language value.
    keyless = Settings(seed=1234)
    keyless.challenge = Challenge(turns=30, lost=5, hand=30, by="Nobody")
    cases.append({"name": "blank-key", "token": keyless.to_token(),
                  "expect": {k: v for k, v in expect_for(keyless).items() if k != "gameKey"},
                  "expectGameKeyPrefix": "j:"})

    # Rejections: a settings-share link carries no score to post, and turns == 0
    # is Challenge's own "no challenge" sentinel.
    cases.append({"name": "no-challenge", "token": Settings(seed=42).to_token(),
                  "reject": "not a challenge link"})
    zeroed = Settings(seed=42)
    zeroed.challenge = Challenge(turns=0, lost=0, hand=0, by="")
    cases.append({"name": "zero-turns", "token": zeroed.to_token(),
                  "reject": "not a challenge link"})
    cases.append({"name": "garbage", "token": "not a token!!", "reject": "malformed"})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
