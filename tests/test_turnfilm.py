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


def _produced(sid=0, hulls=1):
    return turnfilm.Produced(((sid, 3, 0, hulls),))


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


def test_linger_gives_combat_its_own_dwell_and_a_trailing_hold():
    """A live End Turn (`main.resolve_turn`) asks for this; history playback
    (`main._next_history_film`) never does, since a run of animated turns there
    must glide continuously rather than stop-start for every fight."""
    film = turnfilm.film([_landed(0), _landed(1), _landed(2)], linger=True)
    assert film.beats[0].ms == config.FILM_LINGER_COMBAT_MS
    # spread across it, one after another, same as combat used to for everyone
    at = [ms for ms, e in film.cues if isinstance(e, turnfilm.Landed)]
    assert at == [0.0, config.FILM_LINGER_COMBAT_MS / 3, config.FILM_LINGER_COMBAT_MS * 2 / 3]
    # ...and the film outlives its last beat, unlike the non-lingering default
    assert film.total_ms == config.FILM_LINGER_COMBAT_MS + config.FILM_LINGER_HOLD_MS


def test_linger_does_nothing_to_a_film_with_no_cues():
    """No trailing hold on an empty film — there is nothing in it to linger on."""
    assert turnfilm.film([], linger=True).total_ms == 0.0


def test_without_linger_combat_is_instant_with_no_trailing_hold():
    """The default: matches `test_consecutive_events_of_one_kind_share_a_beat`,
    spelled out here to contrast directly with the lingering case above."""
    film = turnfilm.film([_landed(0), _landed(1), _landed(2)])
    assert film.beats[0].ms == config.FILM_COMBAT_MS == 0
    assert film.total_ms == 0.0


def test_a_clash_rides_inside_the_move_beat_where_it_happened():
    advanced = turnfilm.Advanced(((0, 4),))
    clash = turnfilm.Clashed(low_id=0, high_id=1, when=0.25, at=0.4, a=0, b=1,
                             a_ships=5, b_ships=3, survivor=0, survivor_owner=1,
                             survivors=4, dead=(1,))
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
                             a_ships=1, b_ships=1, survivor=None,
                             survivor_owner=None, survivors=0, dead=(0, 1))
    film = turnfilm.film([clash])
    assert clash in [e for _, e in film.cues]


def test_production_applies_in_place_with_no_dwell_of_its_own():
    """Production never stops the board: its beat is an instant at its own point
    in the sequence, whatever surrounds it. Here it comes *after* combat and there
    is no movement anywhere in the turn, so it has nothing to lead from either."""
    film = turnfilm.film([_landed(), _produced()])
    produce = [b for b in film.beats if b.kind == "produce"][0]
    assert produce.ms == 0
    assert produce.holds(produce.start) is False   # an instant, not a stretch
    assert film.label(produce.start) == ""         # ...so it captions nothing
    assert dict((e, ms) for ms, e in film.cues)[_produced()] == produce.start


def test_production_leads_the_fight_it_fed_from_inside_the_glide():
    """Production runs before arrivals, so a hull finished this turn is in the
    garrison for the fight that follows it — and the two must not land on the same
    instant, or the `+1` reads as simultaneous with the fight it just fed instead
    of as having contributed to it. `FILM_PRODUCE_MS` buys exactly that gap as a
    *lead*, borrowed from the move beat rather than added to the film: the board is
    still gliding while the `+1` lands, and the turn is no longer for it."""
    assert config.FILM_PRODUCE_MS > 0
    film = turnfilm.film([turnfilm.Advanced(((0, 3),)), _produced(), _landed()])
    move, produce, combat_beat = film.beats
    assert (move.kind, produce.kind, combat_beat.kind) == ("move", "produce", "combat")
    assert produce.ms == 0                                    # an instant, still
    assert combat_beat.start - produce.start == config.FILM_PRODUCE_MS
    assert move.holds(produce.start)                          # ...spent mid-glide
    assert film.total_ms == move.end == config.FILM_MOVE_MS


def test_a_lead_borrows_only_what_is_actually_there():
    """No movement behind it, nothing to borrow: the whole turn collapses to one
    instant rather than manufacturing a stretch to lead across."""
    film = turnfilm.film([_produced(), _landed()])
    produce, combat_beat = film.beats
    assert (produce.start, combat_beat.start) == (0.0, 0.0)
    assert film.total_ms == 0.0 and not film.plays


def test_a_fight_lands_on_the_films_closing_instant():
    """Which is the frame the next turn's glide starts on, so a chained run of
    animated turns never stops for one (`main._next_history_film`). The burst
    outliving the join is `Ui.fading_fights`' job, not the film's."""
    film = turnfilm.film([turnfilm.Advanced(((0, 3),)), _landed(0), _landed(1)])
    at = [ms for ms, e in film.cues if isinstance(e, turnfilm.Landed)]
    assert at == [film.total_ms, film.total_ms] == [config.FILM_MOVE_MS] * 2


def test_cues_at_one_instant_keep_emission_order():
    """A zero-length beat guarantees ties, and the engine's order is the only
    order that can be right."""
    first, second = _produced(0), _produced(1)
    film = turnfilm.film([turnfilm.Produced(((0, 3, 0, 1),)),
                          turnfilm.Produced(((1, 3, 0, 1),))])
    assert [e for _, e in film.cues] == [first, second]


def test_travel_is_one_outside_the_move_beat():
    """Before the move beat the board still carries pre-advance schedules and
    after it the advance has landed, so `progress_at(1.0)` is right at both ends.

    Only movement spends time, so "outside" is the closing instants of a turn that
    glided — and the whole of one that did not.
    """
    film = turnfilm.film([
        turnfilm.Launched(fleet=0, owner_id=1, source_id=0, dest_id=1, ships=2,
                          turns_total=4, turns_remaining=4, source_ships=1,
                          lane_slot=0),
        turnfilm.Advanced(((0, 3),)),
        _landed(),
    ])
    move = next(b for b in film.beats if b.kind == "move")
    assert move.start == 0.0                     # nothing holds the board first
    assert film.travel(0.0) == 0.0
    assert film.travel(move.ms / 2) == pytest.approx(0.5)
    assert film.travel(move.end) == 1.0          # the combat instant, and after
    assert film.travel(film.total_ms) == 1.0
    assert turnfilm.film([_produced()]).travel(0.0) == 1.0   # a turn with no glide


def test_launch_is_folded_into_the_move_with_no_pause_of_its_own():
    """`FILM_LAUNCH_MS` is 0, and launches open a turn so there is nothing behind
    them to lead from anyway: a launched fleet already starts its glide at progress
    0 (`Fleet.progress_at` combined with `travel`), so a beat of its own only
    bought a stutter before movement began."""
    assert config.FILM_LAUNCH_MS == 0
    film = turnfilm.film([
        turnfilm.Launched(fleet=0, owner_id=1, source_id=0, dest_id=1, ships=2,
                          turns_total=4, turns_remaining=4, source_ships=1,
                          lane_slot=0),
        turnfilm.Advanced(((0, 3),)),
    ])
    launch = next(b for b in film.beats if b.kind == "launch")
    assert launch.ms == 0
    assert launch.holds(launch.start) is False       # an instant, not a stretch
    assert film.travel(0.0) == 0.0                   # the glide starts at once


def test_run_to_returns_what_it_just_applied():
    """`Reel.run_to` hands back the events it applied on *this* call, not the
    whole history — `Ui.archive_marks` turns those into fading marks, and it must
    never see the same fight or tick twice."""
    film = turnfilm.film([turnfilm.Advanced(()), _landed()])
    reel = turnfilm.Reel(make_state([(0, 1, 2, 100)], []), film)
    first = reel.run_to(0.0)
    assert first == [e for at, e in film.cues if at <= 0.0]
    assert first and not reel.done          # the Landed cue is still ahead, in
                                             # the (zero-length) combat beat
    assert reel.run_to(0.0) == []           # nothing new due yet
    rest = reel.run_to(film.total_ms)
    assert rest == [e for at, e in film.cues if at > 0.0]
    assert reel.done


def test_run_returns_every_cue_in_order():
    film = turnfilm.film([turnfilm.Advanced(()), _landed()])
    reel = turnfilm.Reel(make_state([(0, 1, 2, 100)], []), film)
    assert reel.run() == [e for _, e in film.cues]
    assert reel.done


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
    """Every side is pooled per owner; attackers fold pairwise, strongest-first,
    among themselves; and the garrison — not just another side in the size-ranked
    queue — faces whatever survives that, last, regardless of its own size.
    """
    with no_jitter():
        (owner, ships), folds = _pileup(10, [(2, 11), (3, 6)])
    # attackers first: P2's 11 takes on P3's 6, then whatever survives that
    # meets the garrison's 10 last — holding the ground is worth more than
    # queuing by size, and here it's enough to win the system back
    assert [(f.attacker, f.attacker_ships, f.defender, f.defender_ships) for f in folds] == [
        (2, 11, 3, 6),
        (2, 9, 1, 10),
    ]
    assert [(f.winner, f.survivors) for f in folds] == [(2, 9), (1, 4)]
    assert (owner, ships) == (1, 4)
    # each step's carried force is the previous step's survivors
    for earlier, later in zip(folds, folds[1:]):
        assert (later.attacker, later.attacker_ships) == (earlier.winner, earlier.survivors)
    # ...and the last step is the answer the node ends up with
    assert (folds[-1].winner, folds[-1].survivors) == (owner, ships)


def test_the_garrison_fights_last_even_when_it_is_the_weakest_side():
    """The garrison's place in the fold is now fixed — last — rather than earned
    by size, so this reads the same whether it happens to be the weakest side or
    not (contrast the previous test, where it's the strongest)."""
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
    """Production alone leaves nothing to watch: its lead has no movement to
    borrow from, so the whole turn is one instant and the board should jump as it
    always did rather than hold on the finished position.

    Combat is an instant too (`FILM_COMBAT_MS` is 0, like launch), so a `Landed`
    alone would not play either — but that never happens for real: an arrival
    always follows an `Advanced` the same turn (`_advance_fleets` processes it
    before `_resolve_arrivals` ever sees it), so `plays` is carried by the move
    beat whenever there is a fight to show.
    """
    assert not turnfilm.film([_produced()]).plays
    assert turnfilm.film([turnfilm.Advanced(((0, 3),)), _landed()]).plays


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


def test_a_pile_ups_sides_and_losses_survive_the_per_owner_pooling():
    """The case that used to be unrecoverable: three owners at one node. Their
    losses are pooled by owner before the scoreboard sees them, but the fold's own
    steps still name every side that fought and what each brought.
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
    assert dict(landed.sides) == {1: 10, 2: 11, 3: 6}   # every side, once each
    # everyone brought 27 hulls between them, and only the survivors are left
    assert landed.destroyed == 27 - s.systems[0].ships
    assert landed.destroyed == sum(p.ships_lost for p in s.players.values())
    # ...of which the label shows only what it cost whoever kept the ground
    holder = s.systems[0].owner_id
    assert landed.victor == holder
    assert landed.cost == dict(landed.sides)[holder] - s.systems[0].ships
    assert landed.cost == s.players[holder].ships_lost
    assert landed.cost < landed.destroyed


def test_the_label_is_the_victors_loss_not_the_wiped_out_garrisons():
    """The complaint this replaced: 9 ships taking a 6-ship system read "−8",
    which is almost all the defender's garrison — visible anyway as the count on
    the node — and buried the 2 the attacker actually paid."""
    s = make_state([(0, 0, 6, 100), (1, 2, 9, 100)], [(0, 1, 1)])
    with no_jitter():
        engine.apply_order(s, engine.Order(2, 1, 0, 9))
        events: list[turnfilm.Event] = []
        engine.end_turn(s, on_event=events.append)

    landed = next(e for e in events if isinstance(e, turnfilm.Landed))
    assert (landed.owner_id, landed.ships) == (2, 7)   # sqrt(81 - 36) rounds to 7
    assert landed.destroyed == 8                        # 6 of them the garrison's
    assert (landed.cost, landed.victor) == (2, 2)


def test_a_landing_that_did_not_fight_cost_nothing():
    """A reinforcement and a walk into an empty system have no steps, and so no
    losses to label — the same test that keeps them from drawing combat's burst."""
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=3,
                             owner_id=1, ships=9, prod_progress=0, steps=())
    assert landed.sides == ()
    assert landed.destroyed == landed.cost == 0
    assert landed.victor is None


def test_annihilation_charges_everyone_and_labels_nobody():
    """Matched forces wipe each other out, at a system and in open space alike.
    Every ship is charged, and there is no victor whose attrition the label could
    be — so it says nothing, which is what the emptied board shows anyway."""
    clash = turnfilm.Clashed(low_id=0, high_id=1, when=0.5, at=0.5, a=0, b=1,
                             a_ships=7, b_ships=7, survivor=None,
                             survivor_owner=None, survivors=0, dead=(0, 1))
    assert clash.destroyed == 14
    assert clash.cost == 0 and clash.victor is None

    fold = turnfilm.Fold(attacker=2, attacker_ships=6, defender=1,
                         defender_ships=6, winner=0, survivors=0)
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=6,
                             owner_id=0, ships=0, prod_progress=0, steps=(fold,))
    assert landed.destroyed == 12
    assert landed.cost == 0 and landed.victor is None


def test_a_clash_charges_the_fleet_that_flew_on_for_what_it_lost():
    """In open space the survivor is a fleet rather than a garrison, so the cost is
    what it launched with less what is left flying — and the owner has to ride on
    the event, since a fleet id cannot be resolved to a player from the board it
    has already left."""
    s = make_state([(0, 1, 20, 100), (1, 2, 20, 100)], [(0, 1, 2)])
    with no_jitter(), in_lane_battles():
        engine.apply_order(s, engine.Order(1, 0, 1, 12))
        engine.apply_order(s, engine.Order(2, 1, 0, 5))
        events: list[turnfilm.Event] = []
        engine.end_turn(s, on_event=events.append)

    clash = next(e for e in events if isinstance(e, turnfilm.Clashed))
    assert clash.victor == 1                    # the 12 beat the 5
    assert clash.cost == 12 - clash.survivors
    assert clash.cost == s.players[1].ships_lost
    assert clash.destroyed == clash.cost + 5


# --------------------------------------------------------------------------- #
# Hulls finished, for the mark over the system
# --------------------------------------------------------------------------- #


def test_production_reports_the_hulls_it_finished_not_just_the_new_total():
    """A tick's fourth number is a *delta*, and it is the one thing here that
    cannot be recovered afterwards: by the time anything draws, the count the ship
    was added to is gone. Most ticks are progress with nothing to show for them,
    and `hulls` is the subset that finished something."""
    # production 1 emits every turn; production 3 takes three to get there
    s = make_state([(0, 1, 0, 1), (1, 2, 0, 3)], [(0, 1, 5)])
    events: list[turnfilm.Event] = []
    engine.end_turn(s, on_event=events.append)
    tick = next(e for e in events if isinstance(e, turnfilm.Produced))
    assert dict((sid, hulls) for sid, _, _, hulls in tick.ticks) == {0: 1, 1: 0}
    assert tick.hulls == ((0, 1),), "only the system that finished one is marked"
    assert s.systems[0].ships == 1 and s.systems[1].ships == 0

    events.clear()
    engine.end_turn(s, on_event=events.append)
    tick = next(e for e in events if isinstance(e, turnfilm.Produced))
    assert tick.hulls == ((0, 1),)      # ...again, while the other still accrues


def test_a_produced_cue_carries_its_hulls_for_the_reel_to_hand_onward():
    """A finished hull no longer has its own expiry inside `turnfilm` — a mark's
    lifetime is `Ui`'s concern now (`archive_marks`/`age_fading_marks`), reached
    through whatever `Reel.run_to` returns. This just pins that the `Produced`
    cue `run_to` hands back still carries `.hulls` intact, which is all `Ui` needs
    to build a `FadingHull` from it."""
    produced = _produced(0, hulls=2)
    film = turnfilm.film([turnfilm.Advanced(()), produced])
    reel = turnfilm.Reel(make_state([(0, 1, 3, 100)], []), film)
    applied = reel.run()
    tick = next(e for e in applied if isinstance(e, turnfilm.Produced))
    assert tick.hulls == ((0, 2),)


def test_a_turn_that_only_produced_still_does_not_play():
    """The mark rides on a playback; it is not a reason to start one. A produce
    beat is an instant whose lead has nothing to borrow here, so a quiet turn
    resolves at once instead of costing time for a couple of ships appearing."""
    assert not turnfilm.film([_produced(0)]).plays
