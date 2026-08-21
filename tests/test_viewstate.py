"""Tests for Ui's pure camera-framing policy (viewstate.py).

Pure and pygame-free: WorldView.fit_to's own geometry is covered by
test_geometry.py, so these only check *which* points reset_view hands it.
"""

from __future__ import annotations

from starconquest.geometry import WorldView
from starconquest.model import GameState, Player, System
from starconquest.viewstate import Ui

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
