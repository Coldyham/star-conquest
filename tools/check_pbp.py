#!/usr/bin/env python3
"""Play a whole play-by-post match against a real endpoint, and check it agreed.

    uv run python tools/check_pbp.py --origin https://deploy-preview-60--star-conquest-leaderboard.netlify.app

The suite already pins the two halves of this feature in isolation — the pure
client (`tests/test_pbp.py`), what `main` does with it (`tests/test_pbp_client.py`,
including two clients playing a match out against an in-memory stand-in) and the
endpoint's own decisions (`leaderboard/tests/pbp.test.mjs`). What none of them
touch is the wire between them: whether the deployed function's queries match the
deployed schema, whether a token minted there is accepted back there, whether
PostgREST's conditional update really does make two clients resolving together a
non-race. Those fail only against a real deploy, and they fail *silently* in the
worst case — a match that opens, takes orders, and then quietly refuses to
advance.

So this is the deploy check: one throwaway match, two seats, played out through
the same `pbp` module the game uses, asserting at each step the thing the design
claims. It is not a unit test and does not belong in the suite; it needs a
network and it writes to a real database.

**It creates a real match.** `pbp_matches`/`pbp_orders` have no delete path here
(append-only, like everything else on this board), so each run leaves one small
finished match behind under a random id. That is what the tables are for and it
is a handful of rows, but it is not nothing — run it against a deploy preview
rather than production unless you mean to.

A deadline cannot be checked here: the clock is `turn_opened_at`, written by the
database when a turn opens, so tripping one means waiting out real hours. What
this does check is that the endpoint *publishes* the judgement (`lapsed`) and
that a fresh turn reads as not lapsed — the plumbing, not the elapsing.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starconquest import ai, pbp, replay, webstore
from starconquest.settings import Settings

# Long enough for a cold Netlify function on a preview deploy, which can take a
# few seconds to wake, and for the several round trips a turn costs.
POLL_SECONDS = 0.25
TIMEOUT_SECONDS = 45


class Failed(Exception):
    """A check that did not hold. The message is the report."""


def await_call(request, what: str) -> dict:
    """Block until ``request`` answers, and hand back its body as a dict.

    Blocking is the one thing the game itself must never do (`pbp.Request` is
    polled once a frame precisely so a round trip cannot stall a frame), but a
    command-line check has no frame to protect and nothing else to be doing.
    """
    if request is None:
        raise Failed(f"{what}: no endpoint resolved — is --origin right?")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status, body = request.poll()
        if status == pbp.PENDING:
            time.sleep(POLL_SECONDS)
            continue
        if status != pbp.OK:
            detail = (pbp.parse_body(body) or {}).get("error") if body else ""
            raise Failed(f"{what}: answered {status}"
                         + (f" — {detail}" if detail else ""))
        parsed = pbp.parse_body(body)
        if parsed is None:
            raise Failed(f"{what}: answered with {len(body)} bytes that are not "
                         f"a JSON object: {body[:120]!r}")
        return parsed
    raise Failed(f"{what}: no answer in {TIMEOUT_SECONDS}s")


def expect_refused(request, what: str) -> None:
    """The opposite: a call that must *not* be accepted."""
    if request is None:
        return                      # never sent is a stronger refusal than 403
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status, _ = request.poll()
        if status == pbp.PENDING:
            time.sleep(POLL_SECONDS)
            continue
        if status == pbp.OK:
            raise Failed(f"{what}: was accepted, and must not have been")
        return
    raise Failed(f"{what}: no answer in {TIMEOUT_SECONDS}s")


def read_match(match_id: str) -> pbp.Match:
    body = await_call(pbp.fetch_state(match_id), "reading the match")
    match = pbp.match_from_dict(body)
    if match is None:
        raise Failed(f"reading the match: {body!r} does not describe one")
    return match


def check(origin: str, seats: int, turns: int) -> int:
    """Play a match out, reporting as it goes. 0 if everything held."""
    # The one override: `pbp.endpoint` resolves per call through here, so aiming
    # at a preview is a matter of answering this differently rather than of
    # editing a URL anywhere. Desktop has no page host to derive a sibling from
    # (`paths.sibling_host`), so without this a desktop run silently asks
    # *production* — which cost ten minutes of confusion once already.
    webstore.leaderboard_origin = lambda: origin.rstrip("/")
    if not pbp.configured():
        raise Failed(f"no endpoint resolves from origin {origin!r}")
    print(f"endpoint: {pbp.endpoint('state')}")

    ai.load_models()
    settings = Settings(mode="random", players=seats, nodes=16, seed=7)
    match_id = replay._new_match_id()
    roster = list(range(1, seats + 1))

    # --- open it ---------------------------------------------------------- #
    body = await_call(pbp.create(match_id, settings, seed=7, seats=roster),
                      "opening the match")
    tokens = pbp.tokens_from(body, match_id)
    if sorted(tokens) != roster:
        raise Failed(f"opening the match: got tokens for {sorted(tokens)}, "
                     f"asked to seat {roster}")
    print(f"opened {match_id} with {len(tokens)} seats")

    # --- a token names its own seat, and nothing else --------------------- #
    for seat_id, token in sorted(tokens.items()):
        named = pbp.seat_from(
            await_call(pbp.identify(match_id, token), f"identifying seat {seat_id}"),
            match_id, token)
        if named is None or named.seat != seat_id:
            raise Failed(f"identifying seat {seat_id}: endpoint said {named}")
    expect_refused(pbp.identify(match_id, "f" * 32), "a token nobody was given")
    print("every token names its own seat; a forged one names none")

    # --- nothing anybody holds is in a public read ------------------------ #
    raw = await_call(pbp.fetch_state(match_id), "reading the match")
    leaked = [s for s, t in tokens.items() if t in repr(raw)]
    if leaked:
        raise Failed(f"a public read carries seat {leaked}'s token in the clear")
    if "tokens" in repr(raw.get("seats")):
        raise Failed("a public read carries the stored token hashes")
    print("a public read carries no token, hashed or otherwise")

    seated = {s: pbp.Seat(match_id, s, t) for s, t in tokens.items()}
    state, _log = pbp.rebuild(read_match(match_id), ai.decide)

    # --- play it out ------------------------------------------------------ #
    for _ in range(turns):
        match = read_match(match_id)
        if match.finished:
            break
        if match.lapsed:
            raise Failed(f"a turn opened moments ago reads as lapsed: {match.lapsed}")

        for seat_id, seat in sorted(seated.items()):
            if match.has_submitted(seat_id):
                continue
            orders = _orders_for(state, seat_id)
            sent = await_call(pbp.submit(seat, match.turn, orders),
                              f"submitting for seat {seat_id}")
            if sent.get("seat") != seat_id:
                raise Failed(f"submitting for seat {seat_id}: endpoint filed it "
                             f"under seat {sent.get('seat')}")
            # A seat may not send twice, and the unique constraint is what says so.
            expect_refused(pbp.submit(seat, match.turn, orders),
                           f"a second submission from seat {seat_id}")

        match = read_match(match_id)
        if not match.ready:
            raise Failed(f"turn {match.turn}: every seat submitted but the turn "
                         f"is still waiting on {match.waiting}")

        # Two clients resolving the same turn is the design's central claim, so
        # it is checked rather than assumed: both compute it, both report, and
        # the second is told it was already done.
        one, one_log, one_digest = pbp.resolve(match, ai.decide)
        _, _, two_digest = pbp.resolve(match, ai.decide)
        if one_digest != two_digest:
            raise Failed(f"turn {match.turn}: two clients resolved the same "
                         f"orders to different boards ({one_digest} vs {two_digest})")
        if replay.digest_hex(replay.reconstruct(one_log)[0]) != one_digest:
            raise Failed(f"turn {match.turn}: the log does not rebuild its own board")

        await_call(pbp.send_resolved(seated[roster[0]], match.turn, one_log,
                                     one_digest, one.winner is not None),
                   f"reporting turn {match.turn}")
        expect_refused(pbp.send_resolved(seated[roster[-1]], match.turn, one_log,
                                         one_digest, one.winner is not None),
                       f"a second report of turn {match.turn}")

        after = read_match(match_id)
        if after.turn != match.turn + 1:
            raise Failed(f"reported turn {match.turn}, but the match is still "
                         f"on turn {after.turn}")
        # A client that was never here rebuilds the same board from stored rows
        # alone — the property the whole thin server rests on.
        state, _log = pbp.rebuild(after, ai.decide)
        if replay.digest_hex(state) != one_digest:
            raise Failed(f"turn {match.turn}: a fresh rebuild from the stored "
                         f"orders disagrees with the board that was reported")
        print(f"turn {match.turn} -> {after.turn}: agreed ({one_digest})")

    # --- a stale submission is refused, not applied ----------------------- #
    expect_refused(pbp.submit(seated[roster[0]], 0, []),
                   "a submission for a turn that has already resolved")
    print("a stale submission is refused")
    print(f"\nOK — {match_id} played {read_match(match_id).turn} turns and "
          f"every client agreed on every one")
    return 0


def _orders_for(state, seat: int) -> list:
    """What a person at ``seat`` does this turn.

    Deliberately not `ai.decide`: a bot draws from `state.rng`, which would leave
    this process's dice somewhere no other client's are, and the disagreement
    this tool exists to detect would then be its own fault rather than the
    endpoint's. A person's orders draw nothing, and neither does this.
    """
    from starconquest.model import Order

    out = []
    for sid, system in sorted(state.systems.items()):
        if system.owner_id != seat or system.ships < 4 or not system.neighbors:
            continue
        lanes = sorted(system.neighbors)
        out.append(Order(seat, sid, lanes[(sid + state.turn) % len(lanes)],
                         system.ships // 2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--origin", required=True,
                    help="the leaderboard site to check, e.g. "
                         "https://deploy-preview-60--star-conquest-leaderboard.netlify.app")
    ap.add_argument("--seats", type=int, default=2, help="people to seat (default 2)")
    ap.add_argument("--turns", type=int, default=4,
                    help="turns to play (default 4); the match is left unfinished")
    args = ap.parse_args()
    try:
        return check(args.origin, args.seats, args.turns)
    except Failed as err:
        print(f"\nFAILED: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
