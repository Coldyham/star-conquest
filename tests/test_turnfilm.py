"""Tests for turn playback.

Pure/headless — ``turnfilm`` imports no pygame, so none of this needs a display,
which is the point: the applier the shell animates with is the applier tested here.
"""

from __future__ import annotations

import pytest

from starconquest import ai, combat, config, engine, turnfilm
from starconquest.model import Fleet, GameState, Player, System
from starconquest.settings import Settings, build_state
from tests.test_engine import in_lane_battles, make_state, no_jitter


def _digest(state: GameState):
    """What "visibly identical" means: everything a frame can show."""
    return (
        tuple((sid, s.owner_id, s.ships, s.production, s.prod_progress)
              for sid, s in sorted(state.systems.items())),
        tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_total,
               f.turns_remaining) for f in state.fleets),
        state.turn,
        state.winner,
        tuple((pid, p.alive, p.ships_lost) for pid, p in sorted(state.players.items())),
    )


def _game(seed: int) -> GameState:
    return build_state(Settings(players=4, nodes=16, mode="random", seed=seed), seed)


@pytest.mark.parametrize("lane_battles", [False, True])
def test_a_film_rebuilds_the_board_that_turn_produced(lane_battles):
    """The whole correctness argument.

    Every event carries an outcome, so applying a turn's film to a copy of the
    board it started from must land exactly on the board it ended on — otherwise
    an animation would silently snap into place at its last frame.
    """
    old = config.IN_LANE_BATTLES
    config.IN_LANE_BATTLES = lane_battles
    try:
        for seed in (1, 2, 3, 7, 11, 23):
            state = _game(seed)
            config.IN_LANE_BATTLES = lane_battles   # build_state re-applies globals
            for turn in range(60):
                if state.winner is not None:
                    break
                before = turnfilm.copy_board(state)
                events: list[turnfilm.Event] = []
                engine.end_turn(state, decide=ai.decide, on_event=events.append)
                rng_state = before.rng.getstate()
                turnfilm.Reel(before, turnfilm.film(events)).run()
                assert _digest(before) == _digest(state), f"seed {seed}, turn {turn}"
                # The applier assigns and never re-simulates, so it cannot have
                # drawn a die — which is what stops a film from inventing a fight.
                assert before.rng.getstate() == rng_state
    finally:
        config.IN_LANE_BATTLES = old


@pytest.mark.parametrize("lane_battles", [False, True])
def test_an_observer_cannot_change_the_turn_it_watches(lane_battles):
    """Watching must be free of consequence: same seed, same orders, same dice,
    same board. This is what pins that no rng draw was added or reordered, and so
    that `engine.RULES_VERSION` needn't move for any of this."""
    old = config.IN_LANE_BATTLES
    config.IN_LANE_BATTLES = lane_battles
    try:
        for seed in (4, 5, 13):
            watched, blind = _game(seed), _game(seed)
            config.IN_LANE_BATTLES = lane_battles
            for _ in range(40):
                if blind.winner is not None:
                    break
                seen = engine.end_turn(watched, decide=ai.decide, on_event=[].append)
                plain = engine.end_turn(blind, decide=ai.decide)
                assert seen.orders == plain.orders
                assert seen.dice == plain.dice
                assert _digest(watched) == _digest(blind)
    finally:
        config.IN_LANE_BATTLES = old


# ---------------------------------------------------------------------------- #
# The timeline
# ---------------------------------------------------------------------------- #


def _landed(node_id=0):
    return turnfilm.Landed(node_id=node_id, fleets=(), was_owner=1, was_ships=2,
                           owner_id=1, ships=2, prod_progress=0, steps=())


def _produced(sid=0):
    return turnfilm.Produced(((sid, 3, 0),))


def test_beats_follow_the_engines_own_emission_order():
    """A film is assembled from the order events arrived in, never from a phase
    order written down in `turnfilm` — so moving `_production` ahead of the
    arrivals reorders the playback with nothing here to change."""
    after = turnfilm.film([_landed(), _produced()])
    assert [b.kind for b in after.beats] == ["combat", "produce"]
    before = turnfilm.film([_produced(), _landed()])
    assert [b.kind for b in before.beats] == ["produce", "combat"]


def test_consecutive_events_of_one_kind_share_a_beat():
    """Three nodes resolving is one Combat beat, not three, so a busy turn
    compresses instead of running long."""
    film = turnfilm.film([_landed(0), _landed(1), _landed(2)])
    assert [b.kind for b in film.beats] == ["combat"]
    assert film.beats[0].ms == config.FILM_COMBAT_MS
    # ...spread across it, each at the start of its own slot
    at = [ms for ms, e in film.cues if isinstance(e, turnfilm.Landed)]
    assert at == [0.0, config.FILM_COMBAT_MS / 3, config.FILM_COMBAT_MS * 2 / 3]


def test_a_clash_rides_inside_the_move_beat_where_it_happened():
    advanced = turnfilm.Advanced(((0, 4),))
    clash = turnfilm.Clashed(low_id=0, high_id=1, when=0.25, at=0.4, a=0, b=1,
                             a_ships=5, b_ships=3, survivor=0, survivors=4, dead=(1,))
    film = turnfilm.film([advanced, clash])
    move = film.beats[0]
    assert move.kind == "move"
    assert dict((e, ms) for ms, e in film.cues)[clash] == move.start + 0.25 * move.ms
    # the advance lands on the beat's first frame, since `travel` sweeps the
    # schedule it applies
    assert dict((e, ms) for ms, e in film.cues)[advanced] == move.start


def test_a_clash_with_no_move_beat_still_gets_shown():
    """Defensive: clashes are attached to a move beat, so they must not vanish if
    there somehow isn't one."""
    clash = turnfilm.Clashed(low_id=0, high_id=1, when=0.5, at=0.5, a=0, b=1,
                             a_ships=1, b_ships=1, survivor=None, survivors=0, dead=(0, 1))
    film = turnfilm.film([clash])
    assert clash in [e for _, e in film.cues]


def test_production_applies_in_place_at_zero_length():
    """`FILM_PRODUCE_MS` is 0 in this cut: production still lands at its own point
    in the sequence, it just gets no dwell of its own."""
    assert config.FILM_PRODUCE_MS == 0
    film = turnfilm.film([_landed(), _produced()])
    produce = [b for b in film.beats if b.kind == "produce"][0]
    assert produce.ms == 0
    assert produce.holds(produce.start) is False   # an instant, not a stretch
    assert film.label(produce.start) == ""         # ...so it captions nothing
    assert dict((e, ms) for ms, e in film.cues)[_produced()] == produce.start


def test_cues_at_one_instant_keep_emission_order():
    """A zero-length beat guarantees ties, and the engine's order is the only
    order that can be right."""
    first, second = _produced(0), _produced(1)
    film = turnfilm.film([turnfilm.Produced(((0, 3, 0),)), turnfilm.Produced(((1, 3, 0),))])
    assert [e for _, e in film.cues] == [first, second]


def test_travel_is_one_outside_the_move_beat():
    """Before the move beat the board still carries pre-advance schedules and
    after it the advance has landed, so `progress_at(1.0)` is right at both ends."""
    film = turnfilm.film([
        turnfilm.Launched(fleet=0, owner_id=1, source_id=0, dest_id=1, ships=2,
                          turns_total=4, turns_remaining=4, source_ships=1),
        turnfilm.Advanced(((0, 3),)),
        _landed(),
    ])
    launch, move = film.beats[0], film.beats[1]
    assert (launch.kind, move.kind) == ("launch", "move")
    assert film.travel(launch.start) == 1.0
    assert film.travel(move.start) == 0.0
    assert film.travel(move.start + move.ms / 2) == pytest.approx(0.5)
    assert film.travel(move.end) == 1.0          # the combat beat, and after
    assert film.travel(film.total_ms) == 1.0


def test_a_film_outlives_its_last_fight_so_the_burst_is_never_cut():
    film = turnfilm.film([_landed()])
    last = max(ms for ms, _ in film.cues)
    assert film.total_ms - last >= config.FILM_FLASH_MS
    assert film.flashes(last)                     # showing at the moment it fires
    assert film.flashes(last)[0][0] == last       # ...and says when it fired
    assert not film.flashes(last + config.FILM_FLASH_MS + 1)


def test_a_turn_with_nothing_in_transit_has_no_move_beat():
    """An empty advance would otherwise buy a beat of dead air."""
    s = make_state([(0, 1, 3, 100), (1, 2, 3, 100)], [(0, 1, 4)])
    events: list[turnfilm.Event] = []
    engine.end_turn(s, on_event=events.append)
    assert not any(isinstance(e, turnfilm.Advanced) for e in events)
    assert "move" not in [b.kind for b in turnfilm.film(events).beats]


# ---------------------------------------------------------------------------- #
# Multi-owner pile-ups
# ---------------------------------------------------------------------------- #


def _pileup(garrison: int, attackers: list[tuple[int, int]]):
    s = GameState.new(0)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=1, ships=garrison, production=100)
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    for pid in (1, 2, 3):
        s.players[pid] = Player(pid, f"P{pid}", (0, 0, 0))
    fleets = [Fleet(owner_id=o, source_id=1, dest_id=0, ships=n,
                    turns_total=1, turns_remaining=0) for o, n in attackers]
    steps: list = []
    result = combat.resolve_arrival(s, 0, fleets, on_step=steps)
    return result, [turnfilm.Fold(*step) for step in steps]


def test_a_pile_up_reports_its_fold_step_by_step():
    """The rule nothing on screen currently hints at: every side is pooled per
    owner, sorted strongest-first, and folded pairwise — carrying its losses
    forward. The garrison is not resolved last; it takes its place by size.
    """
    with no_jitter():
        (owner, ships), folds = _pileup(10, [(2, 11), (3, 6)])
    # strongest first: P2's 11 takes on the garrison's 10, then P3's 6 takes on
    # whatever is left of it — so the *weakest* arrival ends up holding the system
    assert [(f.attacker, f.attacker_ships, f.defender, f.defender_ships) for f in folds] == [
        (2, 11, 1, 10),
        (2, 5, 3, 6),
    ]
    assert [(f.winner, f.survivors) for f in folds] == [(2, 5), (3, 3)]
    assert (owner, ships) == (3, 3)
    # each step's carried force is the previous step's survivors
    for earlier, later in zip(folds, folds[1:]):
        assert (later.attacker, later.attacker_ships) == (earlier.winner, earlier.survivors)
    # ...and the last step is the answer the node ends up with
    assert (folds[-1].winner, folds[-1].survivors) == (owner, ships)


def test_a_weak_garrison_is_the_case_that_reads_as_expected():
    """When the garrison is the weakest side the fold *is* "the attackers fight,
    then the survivor takes the system" — which is why the rule is easy to
    mis-read from the cases you happen to see."""
    with no_jitter():
        _, folds = _pileup(3, [(2, 10), (3, 9)])
    assert (folds[0].attacker, folds[0].defender) == (2, 3)   # the attackers, first
    assert folds[-1].defender == 1                            # the garrison, last


def test_only_a_side_worth_fighting_makes_a_step():
    """One engagement per side past the strongest, so a lone arrival reports
    nothing and an ordinary attack reports the one fight it is."""
    with no_jitter():
        _, empty = _pileup(0, [(2, 5)])          # walking into an empty system
        assert empty == []
        _, plain = _pileup(4, [(2, 9)])          # an ordinary two-sided attack
        assert [(f.attacker, f.defender) for f in plain] == [(2, 1)]


def test_a_pile_up_rides_in_the_landed_event():
    """The fold reaches a film through `Landed`, so the animation can show it."""
    s = make_state([(0, 2, 12, 100), (1, 3, 9, 100), (2, 1, 4, 100)],
                   [(0, 2, 1), (1, 2, 1)])
    with no_jitter():
        engine.apply_order(s, engine.Order(2, 0, 2, 12))
        engine.apply_order(s, engine.Order(3, 1, 2, 9))
        events: list[turnfilm.Event] = []
        engine.end_turn(s, on_event=events.append)
    landed = [e for e in events if isinstance(e, turnfilm.Landed)]
    assert len(landed) == 1
    assert landed[0].node_id == 2
    assert (landed[0].was_owner, landed[0].was_ships) == (1, 4)
    assert len(landed[0].fleets) == 2
    assert len(landed[0].steps) == 2      # three sides -> two engagements


def test_a_film_of_nothing_but_instants_does_not_play():
    """Production alone, at a zero dwell, leaves nothing to watch — so the board
    should jump as it always did rather than hold on the finished position."""
    assert not turnfilm.film([_produced()]).plays
    assert turnfilm.film([_landed()]).plays


# ---------------------------------------------------------------------------- #
# Reaching a replay
# ---------------------------------------------------------------------------- #


def test_reconstruct_buckets_each_turns_events_at_its_own_boundary():
    """`on_turn` already fires at the opening position and once after each turn,
    which is exactly the cut a per-turn film needs — so history animation needs no
    second mechanism, and turn i's events rebuild board i-1 into board i."""
    from starconquest import replay

    state = _game(9)
    log = replay.new_log(Settings(players=4, nodes=16, mode="random", seed=9), 9)
    for _ in range(12):
        if state.winner is not None:
            break
        log.record_turn(engine.end_turn(state, decide=ai.decide), human_ai=True, rules={})

    boards: list[GameState] = []
    events: list[turnfilm.Event] = []
    buckets: list[list[turnfilm.Event]] = []

    def capture(s: GameState) -> None:
        boards.append(turnfilm.copy_board(s))
        buckets.append(events.copy())
        events.clear()

    replay.reconstruct(log, on_turn=capture, on_event=events.append)

    assert len(buckets) == len(boards)
    assert buckets[0] == []          # the opening position happened to nobody
    for i in range(1, len(boards)):
        board = turnfilm.copy_board(boards[i - 1])
        turnfilm.Reel(board, turnfilm.film(buckets[i])).run()
        assert _digest(board) == _digest(boards[i]), f"turn {i}"


def test_reconstruct_without_an_observer_is_unchanged():
    from starconquest import replay

    state = _game(10)
    log = replay.new_log(Settings(players=4, nodes=16, mode="random", seed=10), 10)
    for _ in range(10):
        if state.winner is not None:
            break
        log.record_turn(engine.end_turn(state, decide=ai.decide), human_ai=True, rules={})
    plain, _ = replay.reconstruct(log)
    watched, _ = replay.reconstruct(log, on_event=[].append)
    assert _digest(plain) == _digest(watched) == _digest(state)


def test_a_reinforcement_reports_no_engagement():
    """Flying into your own system is not a fight, and the film has to be able to
    tell — otherwise it marks a reinforcement as combat."""
    s = make_state([(0, 1, 12, 100), (1, 1, 3, 100)], [(0, 1, 1)])
    with no_jitter():
        engine.apply_order(s, engine.Order(1, 0, 1, 6))
        events: list[turnfilm.Event] = []
        engine.end_turn(s, on_event=events.append)
    landed = [e for e in events if isinstance(e, turnfilm.Landed)]
    assert len(landed) == 1
    assert (landed[0].was_owner, landed[0].owner_id) == (1, 1)
    assert landed[0].ships == 9        # 3 already there, 6 arriving
    assert landed[0].steps == ()       # nothing fought


# --------------------------------------------------------------------------- #
# What a fight cost
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [3, 11, 26])
def test_a_fights_cost_matches_what_the_scoreboard_was_charged(seed):
    """The oracle for `destroyed` is `Player.ships_lost`, which the engine writes
    from the other end (`combat._record_losses`, charging each side what it
    brought less what it kept).

    Every ship that dies in a turn dies at a clash or at an arrival, so the two
    totals have to agree turn by turn — a label derived from the events cannot be
    allowed to disagree with the number the scoreboard shows.
    """
    state = _game(seed)
    with in_lane_battles():
        for _ in range(60):
            if state.winner is not None:
                break
            before = {pid: p.ships_lost for pid, p in state.players.items()}
            events: list[turnfilm.Event] = []
            engine.end_turn(state, decide=ai.decide, on_event=events.append)
            charged = sum(p.ships_lost - before[pid]
                          for pid, p in state.players.items())
            shown = sum(e.destroyed for e in events
                        if isinstance(e, (turnfilm.Clashed, turnfilm.Landed)))
            assert shown == charged, f"turn {state.turn}"


def test_a_pile_ups_cost_survives_the_per_owner_pooling():
    """The case that used to be unrecoverable: three owners at one node. Their
    per-side losses are pooled by owner before the scoreboard sees them, but the
    fold's own steps still add up to the whole engagement.
    """
    s = make_state([(0, 1, 10, 100), (1, 2, 11, 100), (2, 3, 6, 100)],
                   [(0, 1, 1), (0, 2, 1)])
    with no_jitter():
        engine.apply_order(s, engine.Order(2, 1, 0, 11))
        engine.apply_order(s, engine.Order(3, 2, 0, 6))
        events: list[turnfilm.Event] = []
        engine.end_turn(s, on_event=events.append)

    landed = next(e for e in events if isinstance(e, turnfilm.Landed))
    assert len(landed.steps) == 2                 # 11 v 10, then the winner v 6
    # everyone brought 27 hulls between them, and only the survivors are left
    assert landed.destroyed == 27 - s.systems[0].ships
    assert landed.destroyed == sum(p.ships_lost for p in s.players.values())


def test_a_landing_that_did_not_fight_cost_nothing():
    """A reinforcement and a walk into an empty system have no steps, and so no
    losses to label — the same test that keeps them from drawing combat's burst."""
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=3,
                             owner_id=1, ships=9, prod_progress=0, steps=())
    assert landed.destroyed == 0


def test_a_clash_that_annihilates_reports_every_ship():
    """Nobody holds open space, so matched fleets wipe each other out — and the
    label has to say so rather than reading zero off the missing survivor."""
    clash = turnfilm.Clashed(low_id=0, high_id=1, when=0.5, at=0.5, a=0, b=1,
                             a_ships=7, b_ships=7, survivor=None, survivors=0,
                             dead=(0, 1))
    assert clash.destroyed == 14
