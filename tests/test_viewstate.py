"""Tests for pure Ui state (viewstate.py): camera-framing policy, and turn-
playback marks that outlive whichever film produced them.

Pure and pygame-free: WorldView.fit_to's own geometry is covered by
test_geometry.py, so the camera tests only check *which* points reset_view
hands it.
"""

from __future__ import annotations

from starconquest import config, turnfilm
from starconquest.geometry import WorldView
from starconquest.model import GameState, Player, System
from starconquest.viewstate import FadingFight, FadingHull, Ui

BOUNDS = (0.0, 0.0, 100.0, 100.0)
SCREEN = (0.0, 0.0, 800.0, 600.0)


def _state(systems: list[System], winner: int | None = None, human_alive: bool = True) -> GameState:
    s = GameState.new(0)
    s.systems = {sys.id: sys for sys in systems}
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    s.players[1] = Player(1, "P1", (0, 0, 0), is_human=True, alive=human_alive)
    s.players[2] = Player(2, "P2", (0, 0, 0))
    s.winner = winner
    return s


def _ui(seen: set[int]) -> Ui:
    return Ui(view=WorldView(BOUNDS, SCREEN), human_id=1, seen=set(seen))


def _fit_expectation(points) -> WorldView:
    """A freshly-fit view, for comparison against whatever `reset_view` produced."""
    v = WorldView(BOUNDS, SCREEN)
    v.fit_to(points)
    return v


def test_reset_view_frames_only_seen_systems_mid_game():
    systems = [System(id=i, pos=(float(i * 10), 0.0)) for i in range(5)]
    state = _state(systems)
    ui = _ui(seen={0, 1})

    ui.reset_view(state)

    expected = _fit_expectation([systems[0].pos, systems[1].pos])
    assert ui.view.zoom == expected.zoom
    assert (ui.view.off_x, ui.view.off_y) == (expected.off_x, expected.off_y)


def test_reset_view_ignores_unseen_systems_even_when_owned_by_a_rival():
    """Only `seen` gates the frame — a rival system sitting in `state.systems`
    but never sighted must not widen it."""
    systems = [System(id=i, pos=(float(i * 10), 0.0), owner_id=2) for i in range(5)]
    state = _state(systems)
    ui = _ui(seen={0})

    ui.reset_view(state)

    expected = _fit_expectation([systems[0].pos])
    assert ui.view.zoom == expected.zoom


def test_reset_view_frames_the_whole_map_once_defeated():
    """Defeat force-reveals fog elsewhere (main._accumulate_fog); the camera
    should stop hiding the map too, even if `seen` never grew past the start."""
    systems = [System(id=i, pos=(float(i * 10), 0.0)) for i in range(5)]
    state = _state(systems, human_alive=False)
    ui = _ui(seen={0})

    ui.reset_view(state)

    expected = _fit_expectation([s.pos for s in systems])
    assert ui.view.zoom == expected.zoom
    assert (ui.view.off_x, ui.view.off_y) == (expected.off_x, expected.off_y)


def test_reset_view_frames_the_whole_map_once_the_game_is_won():
    """A win can leave far neutral systems still unseen; the final camera
    should show the finished board in full regardless."""
    systems = [System(id=i, pos=(float(i * 10), 0.0)) for i in range(5)]
    state = _state(systems, winner=1, human_alive=True)
    ui = _ui(seen={0, 1})

    ui.reset_view(state)

    expected = _fit_expectation([s.pos for s in systems])
    assert ui.view.zoom == expected.zoom
    assert (ui.view.off_x, ui.view.off_y) == (expected.off_x, expected.off_y)


def test_reset_view_with_nothing_seen_yet_falls_back_to_the_full_map():
    systems = [System(id=i, pos=(float(i * 10), 0.0)) for i in range(5)]
    state = _state(systems)
    ui = _ui(seen=set())

    ui.reset_view(state)

    assert ui.view.zoom == 1.0


# --------------------------------------------------------------------------- #
# Fading marks: a fight or a finished hull, tracked independently of whichever
# film produced it (see turnfilm.py's "Animated end of turn" for why).
# --------------------------------------------------------------------------- #


def _fold(survivors=7):
    return turnfilm.Fold(attacker=2, attacker_ships=9, defender=1, defender_ships=6,
                         winner=2, survivors=survivors)


def _fading_fight(age_ms=0.0):
    return FadingFight(age_ms=age_ms, node_id=0, low_id=None, high_id=None, at=0.0,
                       burst_color=config.COLOR_TEXT, cost=2, victor=2)


def _fading_hull(age_ms=0.0):
    return FadingHull(age_ms=age_ms, node_id=0, hulls=1, owner_id=1)


def test_archive_marks_turns_a_fight_into_a_fading_fight():
    state = _state([System(id=0, pos=(0.0, 0.0))])
    ui = _ui(seen={0})
    ui.visible = {0}
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=6,
                             owner_id=2, ships=7, prod_progress=0, steps=(_fold(),))

    ui.archive_marks(state, [landed])

    assert len(ui.fading_fights) == 1
    fight = ui.fading_fights[0]
    assert (fight.age_ms, fight.node_id, fight.cost, fight.victor) == (0.0, 0, 2, 2)


def test_archive_marks_skips_a_reinforcement():
    """`Landed.steps` is empty exactly when nothing fought — a reinforcement, or
    walking into an empty system — so neither earns a mark."""
    state = _state([System(id=0, pos=(0.0, 0.0))])
    ui = _ui(seen={0})
    ui.visible = {0}
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=4,
                             owner_id=1, ships=9, prod_progress=0, steps=())

    ui.archive_marks(state, [landed])

    assert ui.fading_fights == []


def test_archive_marks_gates_a_fight_on_visibility():
    """Checked once, here, rather than every frame a mark is drawn — see
    `Ui.archive_marks`'s docstring for why."""
    state = _state([System(id=0, pos=(0.0, 0.0))])
    ui = _ui(seen={0})
    ui.visible = set()   # cannot see it happen
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=6,
                             owner_id=2, ships=7, prod_progress=0, steps=(_fold(),))

    ui.archive_marks(state, [landed])

    assert ui.fading_fights == []


def test_archive_marks_turns_visible_hulls_into_fading_hulls():
    state = _state([
        System(id=0, pos=(0.0, 0.0), owner_id=1),
        System(id=1, pos=(10.0, 0.0), owner_id=2),
    ])
    ui = _ui(seen={0, 1})
    ui.visible = {0}   # system 1's yard is not ours to report
    produced = turnfilm.Produced(((0, 4, 0, 1), (1, 6, 0, 2)))

    ui.archive_marks(state, [produced])

    assert len(ui.fading_hulls) == 1
    hull = ui.fading_hulls[0]
    assert (hull.age_ms, hull.node_id, hull.hulls, hull.owner_id) == (0.0, 0, 1, 1)


def test_a_second_mark_at_one_system_supersedes_the_first():
    """Turns chain straight into each other, so a system fought over (or finishing
    a hull) two turns running can still be showing last turn's mark when this
    turn's fires. Two bursts and two numbers stacked on one node read as one
    garbled figure, so the older one goes rather than both being drawn."""
    state = _state([System(id=0, pos=(0.0, 0.0), owner_id=1)])
    ui = _ui(seen={0})
    ui.visible = {0}
    ui.fading_fights = [_fading_fight(age_ms=400.0)]
    ui.fading_hulls = [_fading_hull(age_ms=400.0)]
    landed = turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=6,
                             owner_id=2, ships=7, prod_progress=0, steps=(_fold(),))

    ui.archive_marks(state, [turnfilm.Produced(((0, 4, 0, 1),)), landed])

    assert [f.age_ms for f in ui.fading_fights] == [0.0]
    assert [h.age_ms for h in ui.fading_hulls] == [0.0]


def test_a_mark_elsewhere_leaves_a_fading_one_alone():
    """Superseding is per system: a fight at one node says nothing about a mark
    still dissolving over another."""
    state = _state([
        System(id=0, pos=(0.0, 0.0), owner_id=1),
        System(id=1, pos=(10.0, 0.0), owner_id=1),
    ])
    ui = _ui(seen={0, 1})
    ui.visible = {0, 1}
    ui.fading_fights = [_fading_fight(age_ms=400.0)]        # at system 0
    landed = turnfilm.Landed(node_id=1, fleets=(), was_owner=1, was_ships=6,
                             owner_id=2, ships=7, prod_progress=0, steps=(_fold(),))

    ui.archive_marks(state, [landed])

    assert sorted(f.node_id for f in ui.fading_fights) == [0, 1]


def test_age_fading_marks_ages_and_prunes_at_the_end_of_its_life():
    ui = _ui(seen=set())
    life = config.FILM_FLASH_MS + config.FILM_FADE_MS
    ui.fading_fights = [_fading_fight(age_ms=life - 10.0)]
    ui.fading_hulls = [_fading_hull(age_ms=life - 10.0)]

    ui.age_fading_marks(5.0)
    assert len(ui.fading_fights) == 1 and len(ui.fading_hulls) == 1
    assert ui.fading_fights[0].age_ms == life - 5.0

    ui.age_fading_marks(20.0)   # carries it past its lifetime
    assert ui.fading_fights == [] and ui.fading_hulls == []


def test_clear_fading_marks_drops_everything():
    ui = _ui(seen=set())
    ui.fading_fights = [_fading_fight()]
    ui.fading_hulls = [_fading_hull()]

    ui.clear_fading_marks()

    assert ui.fading_fights == [] and ui.fading_hulls == []
