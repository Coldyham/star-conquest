"""Transient view/UI state — everything that is about *interacting* with the
game rather than the game itself. Kept out of GameState so the simulation core
stays pure. Shared by input.py (mutates it) and render.py (reads it).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from . import config, turnfilm
from .geometry import WorldView
from . import model
from .model import GameState, Order

# interaction modes
IDLE = "idle"  # nothing selected
SELECTED = "selected"  # a source system is selected, awaiting a destination
# a destination is picked: the send is committed and the on-map popup is open
# to retune / forward / cancel it
CHOOSING = "choosing"
# multi-select + route-to planning. Unlike every other mode nothing is committed
# while it is on: it builds a *proposal* (`route_plan`) that the player confirms
# or discards in one go, because it writes many rules at once and can overwrite
# existing ones — too much to undo click-by-click the way a single send is.
ROUTING = "routing"


@dataclass(frozen=True)
class FadingFight:
    """A fight, interpreted once at the moment it fired and aged every frame
    after — independent of whichever film/turn is currently playing, so it can
    keep dissolving on top of the *next* turn's glide instead of vanishing the
    moment the film that produced it is replaced.

    ``node_id`` is set for an arrival (`Landed`); ``low_id``/``high_id``/``at``
    for an open-space clash instead, mirroring the lane-fraction placement
    `render` already draws a `Clashed` at. Screen position is still resolved
    fresh every frame from `state.systems[...].pos` (which never moves), so
    nothing here is display-space.
    """

    age_ms: float
    node_id: Optional[int]
    low_id: Optional[int]
    high_id: Optional[int]
    at: float
    burst_color: tuple[int, int, int]
    cost: int
    victor: Optional[int]


@dataclass(frozen=True)
class FadingHull:
    """A finished hull, interpreted once at the moment it fired — the
    production half of `FadingFight`."""

    age_ms: float
    node_id: int
    hulls: int
    owner_id: int


def _clip_to_play(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Intersect a screen-space rect with the map viewport (empty if disjoint)."""
    rx, ry, rw, rh = rect
    px, py, pw, ph = config.play_rect()
    x0, y0 = max(rx, px), max(ry, py)
    x1, y1 = min(rx + rw, px + pw), min(ry + rh, py + ph)
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


@dataclass
class Ui:
    view: WorldView
    human_id: int = 1
    mode: str = IDLE
    selected: Optional[int] = None  # source system id
    hover: Optional[int] = None  # system under the cursor
    dest: Optional[int] = None  # chosen destination system id
    chosen: int = 0  # ships the active one-shot send commits
    keep: int = 0  # ships held back by the active forward rule
    # In CHOOSING the send is already committed: as a one-shot order at
    # `sel_order` (when forward_armed is False, sized by `chosen`) or as the
    # standing rule out of `selected` in `auto_forward` (when forward_armed is
    # True, holding back `keep` and forwarding the rest). The popup edits
    # whichever is live.
    forward_armed: bool = False  # active send is a standing forward rule
    # True when the popup was opened on an order/rule that *predates* it (see
    # `edit_order`/`edit_forward`) rather than one it just created. Can't be
    # derived: the popup commits immediately, so a fresh compose and a reopened
    # order look identical by the time it is on screen. Two things read it — the
    # bottom button's label (Cancel vs Delete) and how far `_close_send` unwinds.
    editing_existing: bool = False
    pending: list[Order] = field(default_factory=list)
    sel_order: Optional[int] = None  # index into `pending` being edited, if any
    # standing auto-forward rules: source_id -> (dest_id, keep). Human-only QoL,
    # so it lives here rather than in the pure GameState. Each turn a rule
    # forwards (garrison - keep) ships from source to dest (see main.resolve_turn).
    auto_forward: dict[int, tuple[int, int]] = field(default_factory=dict)
    sel_forward: Optional[int] = None  # source id of the rule being edited, if any
    # Route mode (see ROUTING above) has two sub-modes, both of which end in one
    # `route_plan` the player confirms. They differ only in how `model.flow_field`
    # is seeded, so everything downstream of the plan is shared.
    #   chain (`route_rally` False) — pick a group of owned systems, aim them at a
    #     destination, and lay a forwarding chain from each of them to it. A drag
    #     boxes a group; a tap always aims (see `route_tap`).
    #   rally (`route_rally` True) — pick the systems ships should gather at, and
    #     every other system we hold forwards toward the nearest of them. A tap
    #     toggles a rally point; drag is free for panning.
    #   route_sel        — the chosen group; in rally mode, the rally points. In
    #                      chain mode the destination is *not* removed from it: it
    #                      is skipped when building the plan instead, so re-aiming
    #                      somewhere else hands the system straight back as a
    #                      source rather than silently having dropped it.
    #   route_plan       — source -> (next hop, keep): the rules a confirm writes.
    #                      Covers the *whole* path, not just the selected systems,
    #                      so ships actually conveyor the full distance.
    #   route_replaces   — sources whose existing rule this would change (the old
    #                      rule is still readable from `auto_forward`, so the set
    #                      of ids is all the preview needs)
    #   route_unroutable — systems with no path to a sink through our own
    #                      territory: the selected ones in chain mode, every owned
    #                      system the rally field never reached in rally mode
    #   route_cycles     — systems the plan would trap ships circling in (see
    #                      `_detect_route_cycles`)
    #   route_box        — a selection box is being dragged. Its corners reuse
    #                      `drag_start`/`drag_pos` below, which are dead in this
    #                      mode; the separate flag is what stops render's
    #                      drag-to-target rubber band drawing over the box.
    route_sel: set[int] = field(default_factory=set)
    route_dest: Optional[int] = None
    # The sub-mode is a preference, not part of the proposal: `reset_route` leaves
    # it alone so re-entering the mode comes back where you left it.
    route_rally: bool = True
    route_plan: dict[int, tuple[int, int]] = field(default_factory=dict)
    route_replaces: set[int] = field(default_factory=set)
    route_unroutable: set[int] = field(default_factory=set)
    route_cycles: set[int] = field(default_factory=set)
    #   route_press      — a press landed on empty map space and may yet become a
    #                      box. Separate from `route_box` (already past the drag
    #                      threshold) so a tap that never moves selects nothing.
    route_box: bool = False
    route_press: bool = False
    # Fog of war (human-only, so it lives here not in GameState). Recomputed each
    # turn by main.refresh_fog from the human's territory; render reads these.
    #   visible      — systems in full detail this turn (owner + ship counts)
    #   seen         — every system ever perceived; the rest of `seen` (minus
    #                  `visible`) renders as a grey "?" silhouette, memory included
    #   player_intel — last-known (systems, ships, prod) per rival ever sighted,
    #                  for the fogged scoreboard's frozen rows
    visible: set[int] = field(default_factory=set)
    seen: set[int] = field(default_factory=set)
    player_intel: dict[int, tuple[int, int, float]] = field(default_factory=dict)
    autoplay: bool = False
    # Play/pause: while True, main steps turns repeatedly (as if tapping Enter),
    # toggled by P or the play/pause button. Distinct from autoplay, which hands
    # the human seat to the AI; here the human's own orders still run each step.
    playing: bool = False
    # Fast forward: drop the delay between auto-resolved turns so a match the human
    # is no longer in reaches its end quickly (see main.step_delay). Offered only
    # once the human seat is knocked out — there is nothing left to decide then, and
    # the only remaining question is who wins.
    fast_forward: bool = False
    show_help: bool = True
    # History mode: a modal review scene for scrubbing back through the recorded
    # match. While active, main draws a reconstructed past board (not the live
    # `state`) and input suppresses board interaction. `history_turn` is the
    # viewed turn (0 == opening position .. `history_max` == latest recorded);
    # `history_max` mirrors the log's turn count so input can map a scrubber
    # click to a turn without needing the log. `history_reveal` lifts fog for a
    # finished game (see behind the fog of war). `dragging_scrubber` tracks a
    # held mouse-drag on the scrubber track.
    history: bool = False
    history_turn: int = 0
    history_max: int = 0
    history_reveal: bool = False
    dragging_scrubber: bool = False
    # Turn playback (see `turnfilm`). `film` is the immutable score of the turn
    # being replayed and `film_ms` where in it we are — the read-only half, so a
    # frame stays a pure function of GameState + Ui + time. `film_ms` is advanced by
    # main from the loop's own `dt` rather than the wall clock, so a test can drive
    # a frame by setting it. The *board* being mutated is a main-loop local, exactly
    # like the reconstructed history boards, and reaches render as `state`.
    #   film_visible — what the human could see at the *start* of the turn being
    #     played back, which the map layer adds to `visible` for the film's
    #     duration (`sees`). `visible` is not monotone, so without it a system
    #     lost this turn would draw as a grey "?" while the fight that took it
    #     played out. Additive rather than a swap, so nothing has to be put back
    #     when a film ends or is skipped.
    #   deferred_view_snap — the camera re-frame `main.resolve_turn` owes once the
    #     film lands (see `main.land_film`): the turn that decides the game reveals
    #     the whole board, and doing that first would play the last turn out on a
    #     map it had already given away.
    #   film_paused — freezes `film_ms` in place without discarding the film, so
    #     pausing mid-playback (the Play/Pause control, `main`'s "toggle_play")
    #     keeps showing what the turn actually did instead of reverting to the
    #     plain board a skip leaves behind. Distinct from `playing`, which governs
    #     whether *further* turns start — a single manually-triggered film runs
    #     with `playing` False throughout, so gating its advance on that would
    #     freeze it on the first frame.
    film: Optional[turnfilm.Film] = None
    film_ms: float = 0.0
    film_visible: frozenset[int] = frozenset()
    film_paused: bool = False
    deferred_view_snap: bool = False
    # Marks (a fight's cost, a finished hull) outlive the film that produced
    # them — see `FadingFight`/`FadingHull` and `archive_marks`/
    # `age_fading_marks` below. Deliberately not part of `film`/`film_ms`:
    # a mark's whole point is to keep fading on top of the *next* turn's
    # glide, so tying its lifetime to the film that made it would erase it
    # the moment that film is replaced.
    fading_fights: list[FadingFight] = field(default_factory=list)
    fading_hulls: list[FadingHull] = field(default_factory=list)
    end_turn_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Play/pause button hit-rect, rebuilt by render each frame (zeroed while
    # autoplay drives turns itself); tested by input, like end_turn_rect.
    play_pause_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for the queued-orders panel, rebuilt by render each frame and
    # tested by input (same store-rect-then-test handoff as end_turn_rect).
    # (pending_index, row_rect, delete_rect) per drawn row — the index is carried
    # explicitly rather than implied by position, because the list scrolls and only
    # a window of it is drawn; a positional mapping would delete the wrong order.
    order_hitboxes: list[tuple[int, tuple[int, int, int, int], tuple[int, int, int, int]]] = field(default_factory=list)
    # Queued-list scrolling: `order_scroll` is the first entry drawn, and render
    # records how far it may go in `order_scroll_max` (0 == everything fits) along
    # with the ▲/▼ button rects. The list is capped to part of the panel so the
    # system details above it are never pushed off, so it can overflow well before
    # the orders themselves become unmanageable.
    order_scroll: int = 0
    order_scroll_max: int = 0
    order_up_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    order_down_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for the −/+ ship-count buttons flanking the active count label in
    # the send popup. Rebuilt by render each frame; zeroed when the popup is closed
    # (same handoff as end_turn_rect).
    minus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    plus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # The popup's count slider: the whole row is the grab target, but the knob
    # *travels* over the row inset by `config.SLIDER_KNOB_R` at each end, so it
    # never overhangs the panel and never lags the finger (see `set_slider_from_x`,
    # whose mapping render._draw_slider inverts exactly). Rebuilt by render each
    # frame from the popup's current top-left, so it follows a dragged popup.
    slider_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    dragging_slider: bool = False
    # Hit-rects for the send popup's action buttons (CHOOSING mode), rebuilt by
    # render each frame and zeroed otherwise (same handoff as end_turn_rect).
    send_tab_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    forward_tab_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    send_all_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    send_half_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    cancel_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Send-popup placement. By default render auto-anchors it to the side of the
    # lane that covers the fewest system nodes; once the user drags it, popup_pos
    # pins the top-left (cleared on a new target so auto-placement resumes).
    # popup_rect is the full panel rect (render writes it; input hit-tests it to
    # start a drag on the background — away from the buttons).
    popup_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    popup_pos: Optional[tuple[int, int]] = None
    dragging_popup: bool = False
    popup_drag_off: tuple[int, int] = (0, 0)
    # Drag-to-target gesture (touch-friendly alternative to tap-source-then-tap-
    # dest): a press on an owned system arms `drag_src`; dragging past a threshold
    # sets `drag_active` and `drag_pos` follows the finger; releasing over an
    # adjacent system commits the send/rule. Render draws a line while active.
    drag_src: Optional[int] = None
    drag_active: bool = False
    drag_start: tuple[int, int] = (0, 0)
    drag_pos: tuple[int, int] = (0, 0)
    # Persistent side-panel button to clear every standing forward rule at once;
    # drawn (and hit-tested) only while any rule exists.
    clear_forward_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Below it, a button that clears only the rules pointed at a system we don't
    # hold; drawn (and hit-tested) only while at least one such rule exists.
    clear_dangerous_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Hit-rects for auto-forward rule rows, listed below the queued orders:
    # (source_id, row_rect, delete_rect) tuples.
    forward_hitboxes: list[tuple[int, tuple[int, int, int, int], tuple[int, int, int, int]]] = field(default_factory=list)
    # History-mode hit-rects, rebuilt by render each frame and tested by input
    # (same store-rect-then-test handoff as end_turn_rect). `history_button_rect`
    # is the bottom-bar (and game-over overlay) toggle; the others are live only
    # while `history` is on. `scrubber_rect` is the draggable track.
    history_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    scrubber_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    rewind_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    exit_history_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Step-one-turn buttons flanking the track, the touch/mouse equivalent of the
    # Left/Right arrow keys — picking an exact turn by dragging alone gets fiddly
    # once a match has a long history.
    history_prev_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    history_next_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Game-over overlay buttons (touch-reachable equivalents of the T/R/M keys),
    # rebuilt by render each frame and tested by input like the rects above.
    # `retry_button_rect` replays the very same match from the opening position
    # (forks the log, same as rewinding to turn 0 from history) rather than
    # `restart_button_rect`'s fresh map on the next seed.
    retry_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    restart_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    menu_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Result-sharing, all game-over overlay only. `hand_turns` counts turns the
    # human actually decided (main increments it in resolve_turn, and recomputes it
    # from the log on resume/rewind) — autoplaying a decided game to skip the
    # cleanup is normal play, so the number is disclosed on the link rather than
    # voiding the score. `challenge_target` is the (turns, lost) this match was set
    # to beat, if it came from a challenge link, so render can say whether you did.
    hand_turns: int = 0
    # Set when this match was *downloaded* rather than played here — a leaderboard
    # `#log=` link or `--watch` (`main.open_replay`). The result on screen is then
    # somebody else's, so none of the sharing below is offered for it: watching a
    # replay must not be one press away from posting its score as your own.
    watched: bool = False
    challenge_target: Optional[tuple[int, int]] = None
    challenge_by: str = ""
    share_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Sits beside the share button: the same token, but opening the public
    # leaderboard's entry form instead of going to the clipboard. Zero-width when
    # no `paths.LEADERBOARD_ORIGIN` is configured.
    leaderboard_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    share_msg: str = ""  # outcome of the last share, drawn under the buttons
    # Live-play bottom-bar buttons that are touch equivalents of keyboard-only
    # actions (A: autoplay, N: new map, F: fast forward). menu_button_rect above is
    # shared with the game-over overlay — the two scenes never draw at the same
    # time, so whichever last ran render.draw owns the current value.
    autoplay_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    restart_live_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Drawn (and hit-tested) only while the human is knocked out and the match is
    # still running — the one situation `fast_forward` applies to.
    fast_forward_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Quit (Esc) and Clear/cancel (X) touch equivalents. quit_button_rect is
    # shared between the live footer and the game-over overlay, like
    # menu_button_rect above; clear_button_rect is live-play only.
    quit_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    clear_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Route-mode hit-rects, rebuilt by render each frame and tested by input (same
    # store-rect-then-test handoff as end_turn_rect). `route_button_rect` is the
    # live-play toggle into the mode; the rest are live only while it is on, and
    # `route_confirm_rect` deliberately takes over the End Turn button's slot, so
    # ending the turn under an open plan is impossible rather than merely guarded.
    route_button_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    route_confirm_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    route_cancel_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # the sub-mode toggle: one button naming the sub-mode it is in, which pressing
    # (or Tab) switches
    route_mode_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # rally mode's "pick the front line for me" shortcut, drawn only when there is
    # something threatened to pick
    route_auto_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Camera pan: a press on empty space (no node/lane/button under it) arms
    # this instead of the drag-to-target gesture, so panning and drag-to-send
    # never fight over the same press. `pan_last` is the previous motion-event
    # position, used to turn each MOUSEMOTION into a `view.pan()` delta.
    pan_active: bool = False
    pan_last: tuple[int, int] = (0, 0)
    # On-map camera control hit-rects (reset view, zoom +/-), rebuilt by render
    # each frame and tested by input (same store-rect-then-test handoff as
    # end_turn_rect); live-play only.
    reset_view_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    zoom_minus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    zoom_plus_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    # -- camera ------------------------------------------------------------- #
    def stop_film(self) -> None:
        """Abandon a playback. Main drops its reel whenever there is no film, so
        this is the whole of "skip" — safe at any moment, because the turn it was
        showing has already been resolved.

        Deliberately does *not* clear `deferred_view_snap`: `input` skips by
        calling this, and what the film was holding back is still owed. Main
        applies it wherever it drops the reel (`main.land_film`), which is the one
        place both the skip and the natural end pass through.
        """
        self.film = None
        self.film_ms = 0.0
        self.film_visible = frozenset()
        self.film_paused = False

    def sees(self, sid: int) -> bool:
        """Whether the map may draw ``sid`` in full detail.

        `visible` on its own everywhere but during a turn playback, which also
        gets the systems that were visible when that turn began — see
        `film_visible`.
        """
        return sid in self.visible or sid in self.film_visible

    def archive_marks(self, board: GameState, events: list[turnfilm.Event]) -> None:
        """Turn newly-applied film events into independent fading marks.

        Interpreted once, here, rather than re-derived from the raw event every
        frame — which is what lets a mark keep dissolving after `film` has moved
        on to the next turn (`age_fading_marks` is what ages it from there).
        ``board`` is the film's own board (already carrying this event), so a
        `Produced` mark's colour reflects who owned the system *at the time*,
        not whoever holds it by the time this is called.

        Visibility is checked once, now, rather than on every frame a mark is
        drawn: a fight or a tick you actually saw fire keeps fading regardless of
        what fog does afterward, rather than blinking out mid-fade the moment the
        next turn's own `film_visible` happens to differ.
        """
        for event in events:
            if isinstance(event, turnfilm.Clashed):
                if not self.sees(event.low_id) and not self.sees(event.high_id):
                    continue
                self.fading_fights.append(FadingFight(
                    age_ms=0.0, node_id=None, low_id=event.low_id, high_id=event.high_id,
                    at=event.at, burst_color=config.COLOR_TEXT,
                    cost=event.cost, victor=event.victor))
            elif isinstance(event, turnfilm.Landed):
                # `steps` is empty for a reinforcement or an unopposed landing —
                # neither is a fight, so neither earns a mark.
                if not event.steps or not self.sees(event.node_id):
                    continue
                color = (config.COLOR_TEXT if event.owner_id == event.was_owner
                         else config.player_color(event.owner_id))
                # A second fight at the same system supersedes the first. Turns
                # chain straight into each other, so the previous one's mark can
                # still be fading here, and two bursts with two costs stacked on
                # one node read as a garbled number rather than as two fights.
                self.fading_fights = [f for f in self.fading_fights
                                      if f.node_id != event.node_id]
                self.fading_fights.append(FadingFight(
                    age_ms=0.0, node_id=event.node_id, low_id=None, high_id=None,
                    at=0.0, burst_color=color, cost=event.cost, victor=event.victor))
            elif isinstance(event, turnfilm.Produced):
                for sid, hulls in event.hulls:
                    if not self.sees(sid):   # a rival's yard is not ours to report
                        continue
                    # ...and the same for a yard finishing a hull two turns
                    # running: one `+N`, not a pile of them.
                    self.fading_hulls = [h for h in self.fading_hulls
                                         if h.node_id != sid]
                    self.fading_hulls.append(FadingHull(
                        age_ms=0.0, node_id=sid, hulls=hulls,
                        owner_id=board.systems[sid].owner_id))

    def age_fading_marks(self, dt: float) -> None:
        """Advance every fading mark's clock and drop whatever has fully
        dissolved (`config.FILM_FLASH_MS` fully visible, then
        `config.FILM_FADE_MS` fading out).

        Independent of `film`/`playing`: a mark keeps aging, and eventually
        goes, whether or not a playback is currently running — which is what
        lets one outlive the film that created it and dissolve on top of
        whatever the next turn is doing instead of disappearing the moment
        `film` is replaced.
        """
        life = config.FILM_FLASH_MS + config.FILM_FADE_MS
        self.fading_fights = [replace(f, age_ms=f.age_ms + dt)
                              for f in self.fading_fights if f.age_ms + dt < life]
        self.fading_hulls = [replace(h, age_ms=h.age_ms + dt)
                             for h in self.fading_hulls if h.age_ms + dt < life]

    def clear_fading_marks(self) -> None:
        """Drop every fading mark outright — for a jump rather than a step:
        entering/leaving history, scrubbing to a turn, or rewinding. Those marks
        belong to a specific point in a specific playback; jumping away from it
        makes them stale rather than merely old."""
        self.fading_fights = []
        self.fading_hulls = []

    def reset_view(self, state: GameState) -> None:
        """Recompute the camera's resting position: framed to just the systems
        seen so far, or the whole map once there is nothing left to hide (the
        human is defeated, or the game has ended). ``view.fit_to`` still leaves
        the full map reachable by zooming out manually either way.

        Call this only at the moments the camera should actually jump — a fresh
        game, the reset button, a defeat, a win — never from an ordinary turn
        just because fog grew, or the view would keep moving under the player.
        """
        if state.winner is not None or state.is_defeated(self.human_id):
            points = [s.pos for s in state.systems.values()]
        else:
            points = [s.pos for sid, s in state.systems.items() if sid in self.seen]
        self.view.fit_to(points)

    # -- spectating --------------------------------------------------------- #
    def can_fast_forward(self, state: GameState) -> bool:
        """Is the 'just show me who wins' control available?

        Only while the human seat is knocked out and the match is still running:
        there are no decisions left to rush past then, and the alternative is
        sitting through a conclusion you have no part in. Render gates the footer
        button on this and main gates the F key on it, so the two can never
        disagree about when fast forward means anything.
        """
        return state.winner is None and state.is_defeated(self.human_id)

    def can_post(self, state: GameState) -> bool:
        """Is this result the player's own to publish — as a challenge link or as
        a leaderboard entry?

        Three things must hold: the human's seat won it, at least one turn was
        decided by hand (a pure autoplay demo is a bot's win, not a score), and the
        match was played here rather than downloaded to watch. Render gates both
        overlay buttons on this and main gates both actions on it, so a keyboard
        shortcut can never reach a result the overlay declines to offer.

        The third condition is not airtight and is not meant to be: rewinding a
        watched replay to a turn from its end and playing that turn out forks a
        match of your own (`resume_game` builds a fresh `Ui`), which this will then
        happily let you post. Sealing that would mean recording where a fork
        branched and carrying it through every later rewind — a bigger idea than
        the problem. What this stops is a posted score being one button-press away
        from any replay on the board.
        """
        return (state.winner == self.human_id and self.hand_turns > 0
                and not self.watched)

    # -- ship accounting ---------------------------------------------------- #
    def committed(self, sid: int) -> int:
        """Ships already promised out of a system by queued (not yet run) orders."""
        return sum(o.ships for o in self.pending if o.source_id == sid)

    def available(self, state: GameState, sid: int) -> int:
        """Ships still free to deploy from a system this turn."""
        return state.systems[sid].ships - self.committed(sid)

    def count_adjust_active(self) -> bool:
        """True while the mouse wheel should nudge a ship count (``step_count``
        below would do something) rather than zoom the camera — mirrors
        ``step_count``'s own dispatch conditions exactly, so the two can't
        drift out of sync."""
        return (self.mode == CHOOSING and (self.forward_armed or self.selected is not None)) or (
            self.sel_forward is not None and self.sel_forward in self.auto_forward
        )

    def step_count(self, state: GameState, delta: int) -> None:
        """Nudge the ship count being adjusted by ``delta`` — the shared logic
        behind both the mouse wheel and the popup's −/+ buttons. Applies to the
        active send (CHOOSING — send count or forward keep), or to a *dormant*
        standing rule highlighted without opening the popup (see `edit_forward`);
        a live one is edited through the popup, so the CHOOSING branch has it."""
        if not self.count_adjust_active():
            return
        if self.mode == CHOOSING and self.forward_armed:
            self.set_keep(state, self.keep + delta)  # forward: adjust keep
        elif self.mode == CHOOSING and self.selected is not None:
            self.set_send_count(state, self.chosen + delta)  # send: adjust count
        elif self.sel_forward is not None and self.sel_forward in self.auto_forward:
            # adjust a standing rule's `keep` in place; it ranges over the
            # source's whole garrison (0 keeps nothing, all forwards nothing)
            src = self.sel_forward
            dest, keep = self.auto_forward[src]
            cap = state.systems[src].ships if src in state.systems else keep
            self.auto_forward[src] = (dest, max(0, min(cap, keep + delta)))

    # -- the popup's count slider ------------------------------------------- #
    def slider_range(self, state: GameState) -> tuple[int, int, int]:
        """``(lo, hi, value)`` for whichever count the popup is editing — the one
        source of truth the slider's two halves share, so the drawn knob and the
        value a click maps to can't drift (same pairing as
        ``count_adjust_active``/``step_count`` above). ``lo == hi`` is a real
        state (an empty source), and both halves must tolerate it."""
        if self.mode != CHOOSING or self.selected is None:
            return (0, 0, 0)
        if self.forward_armed:
            garrison = state.systems[self.selected].ships if self.selected in state.systems else 0
            return (0, garrison, self.keep)
        cap = self._active_cap(state)
        return (1 if cap > 0 else 0, cap, self.chosen)

    def set_slider_from_x(self, state: GameState, px: int) -> None:
        """Map a press/drag x within ``slider_rect`` onto the count being edited.

        Inverse of the knob placement in ``render._draw_slider``: the travel is the
        recorded row inset by the knob radius at each end, so the knob sits exactly
        under the pointer across the whole range. Funnels into ``set_keep`` /
        ``set_send_count``, inheriting their clamping and the write-through to the
        queued order. A no-op on a zeroed rect (the popup closed mid-drag) or a
        zero-width range (an empty source)."""
        x, _y, w, _h = self.slider_rect
        lo, hi, _value = self.slider_range(state)
        travel = w - 2 * config.SLIDER_KNOB_R
        if w <= 0 or travel <= 0 or hi <= lo:
            return
        t = max(0.0, min(1.0, (px - x - config.SLIDER_KNOB_R) / travel))
        count = round(lo + t * (hi - lo))
        if self.forward_armed:
            self.set_keep(state, count)
        else:
            self.set_send_count(state, count)

    # -- active send (CHOOSING) --------------------------------------------- #
    def _active_cap(self, state: GameState) -> int:
        """The most ships the active send can commit from ``selected``."""
        if self.selected is None:
            return 0
        if self.forward_armed:
            # a rule forwards (garrison - keep); its ceiling is the whole garrison
            return state.systems[self.selected].ships
        cap = self.available(state, self.selected)
        if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            cap += self.pending[self.sel_order].ships  # the send's own committed ships
        return cap

    def begin_send(self, state: GameState, dest: int, forward: bool = False) -> None:
        """Commit a send-all order to ``dest`` and open the adjust popup. No
        confirmation click: the order is live immediately (retune or cancel it
        via the popup). ``forward`` arms it as a standing rule from the start —
        allowed even from an empty system (the rule forwards future production),
        whereas a one-shot send needs ships free right now."""
        if self.selected is None:
            return
        avail = self.available(state, self.selected)
        if avail <= 0 and not forward:
            return
        self.dest = dest
        self.mode = CHOOSING
        self.forward_armed = False
        self.editing_existing = False  # this popup created its own subject
        self.chosen = max(0, avail)
        self.keep = 0
        self.popup_pos = None  # fresh target -> auto-place the popup
        self.dragging_popup = False
        self.dragging_slider = False
        if avail > 0:
            self.pending.append(Order(self.human_id, self.selected, dest, avail))
            self.sel_order = len(self.pending) - 1
        else:
            self.sel_order = None  # nothing to send now; forward-only rule
        if forward:
            self.toggle_forward(state)

    def set_send_count(self, state: GameState, count: int) -> None:
        """Set the active one-shot send's ship count (clamped) on its order.
        Send-tab only — forwarding is sized by `keep` (see set_keep)."""
        if self.mode != CHOOSING or self.forward_armed or self.selected is None:
            return
        cap = self._active_cap(state)
        lo = 1 if cap > 0 else 0  # an empty source sends nothing (0), not 1
        self.chosen = max(lo, min(cap, count))
        if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            self.pending[self.sel_order].ships = self.chosen

    def set_keep(self, state: GameState, keep: int) -> None:
        """Set how many ships the active forward rule holds back each turn
        (0 == forward everything). Forward-tab only."""
        if self.mode != CHOOSING or not self.forward_armed or self.selected is None:
            return
        self.keep = max(0, min(state.systems[self.selected].ships, keep))
        self.auto_forward[self.selected] = (self.dest, self.keep)

    def send_all(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state))

    def send_half(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state) // 2)

    def keep_none(self, state: GameState) -> None:
        self.set_keep(state, 0)  # forward everything

    def keep_half(self, state: GameState) -> None:
        if self.selected is not None:
            self.set_keep(state, state.systems[self.selected].ships // 2)

    def toggle_forward(self, state: GameState) -> None:
        """Flip the active send between a one-shot order (sized by `chosen`) and
        a standing rule that holds back `keep` and forwards the rest each turn.
        Arming defaults to keep 0 (forward everything)."""
        if self.mode != CHOOSING or self.selected is None or self.dest is None:
            return
        self.forward_armed = not self.forward_armed
        if self.forward_armed:
            if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
                del self.pending[self.sel_order]
            self.sel_order = None
            self.keep = 0
            self.auto_forward[self.selected] = (self.dest, self.keep)
        else:
            self.auto_forward.pop(self.selected, None)
            cap = self._active_cap(state)
            if cap > 0:
                self.chosen = max(1, min(cap, self.chosen))
                self.pending.append(Order(self.human_id, self.selected, self.dest, self.chosen))
                self.sel_order = len(self.pending) - 1
            else:
                self.chosen = 0  # empty source: no one-shot order to queue
                self.sel_order = None

    def set_forward_mode(self, state: GameState, armed: bool) -> None:
        """Switch the popup's Send/Forward tab explicitly (idempotent)."""
        if self.mode == CHOOSING and self.forward_armed != armed:
            self.toggle_forward(state)

    def clear_all_forward(self) -> None:
        """Remove every standing forward rule at once."""
        self.auto_forward.clear()

    def clear_dangerous_forward(self, state: GameState) -> None:
        """Remove only the standing rules currently pointed at a system we don't
        hold, leaving the rest of the network untouched."""
        for sid in [s for s in self.auto_forward if self.rule_is_hostile(state, s)]:
            self.auto_forward.pop(sid, None)

    def cancel_send(self) -> None:
        """Discard the active send entirely, keeping just the source selected."""
        if self.forward_armed:
            if self.selected is not None:
                self.auto_forward.pop(self.selected, None)
        elif self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            del self.pending[self.sel_order]
        self._close_send()

    def close_send(self) -> None:
        """Leave the popup but keep the committed send (order or rule) in place."""
        self._close_send()

    def _close_send(self) -> None:
        # Reopening an existing order/rule borrowed `selected` to point the popup
        # at its source; leaving that armed on close would mean the next tap on a
        # neighbour queues a *second* fleet from a system the player only meant to
        # look at. So an edit unwinds all the way, while a compose keeps its source
        # selected (you picked it deliberately, and may want another send from it).
        if self.editing_existing:
            self.reset_selection()
            self.sel_forward = None
            return
        self.sel_order = None
        self.forward_armed = False
        self.dest = None
        self.chosen = 0
        self.keep = 0
        self.popup_pos = None
        self.dragging_popup = False
        self.dragging_slider = False
        self.mode = SELECTED if self.selected is not None else IDLE

    # -- route mode --------------------------------------------------------- #
    def can_route(self, state: GameState) -> bool:
        """Is route mode available? Only while there is a human turn left to plan:
        not once the match is decided, not while we hold nothing, and not under
        autoplay (where the AI issues our orders and a plan would never fire).

        Render gates the footer button on this and input gates the key on it, so
        the two can never disagree about when the mode means anything — the same
        pairing as ``can_fast_forward``.
        """
        return (
            state.winner is None
            and not state.is_defeated(self.human_id)
            and not self.autoplay
        )

    def begin_route(self) -> None:
        """Enter route mode from scratch, dropping any in-progress send."""
        self.reset_selection()
        self.sel_forward = None
        self.reset_route()
        self.mode = ROUTING
        self.playing = False  # a plan must not be resolved out from under us

    def reset_route(self) -> None:
        """Drop the whole proposal and leave the mode. Committed rules survive (a
        confirm has already written them into `auto_forward`).

        `route_rally` is deliberately left alone: the sub-mode is a preference, and
        this runs every turn from `main.resolve_turn`, so clearing it would drag the
        player back to the default sub-mode between one plan and the next.

        Deliberately *not* folded into `reset_selection`: that runs from several
        places mid-gesture, and would wipe the plan the route branch is building.
        """
        self.route_sel = set()
        self.route_dest = None
        self.route_plan = {}
        self.route_replaces = set()
        self.route_unroutable = set()
        self.route_cycles = set()
        self.route_box = False
        self.route_press = False
        if self.mode == ROUTING:
            self.mode = IDLE

    def set_route_rally(self, state: GameState, rally: bool) -> None:
        """Switch route sub-mode, dropping the proposal but staying in the mode.

        The proposal has to go: a chain group and a set of rally points are
        different kinds of thing living in the same `route_sel`, so carrying one
        over would silently reinterpret sources as sinks. Idempotent, so the two
        footer buttons can both be pressed repeatedly.
        """
        if rally == self.route_rally:
            return
        self.route_rally = rally
        self.route_sel = set()
        self.route_dest = None
        self.route_press = False
        self.route_box = False
        self.recompute_route(state)

    def route_tap(self, state: GameState, sid: int) -> None:
        """A tap on a system, in whichever sub-mode is live.

        **Rally**: a plain membership toggle — tap to make a rally point, tap again
        to take it back. One action, not two: unlike the aim-or-remove shape
        rejected below, what a tap *does* never changes with what it lands on.

        **Chain**: a tap always aims the group at that system. Tapping whatever is
        already the destination un-aims it, and drops it from the group if it was
        in it.

        One primary meaning is the whole point. Making a tap mean "aim" on some
        systems and "remove" on others is what makes it feel arbitrary, and it also
        makes aiming at one of your own picks destructive — pick a group, aim at a
        member, aim somewhere else, and the member is silently gone. Here aiming
        never removes anything: the destination stays in the group and is merely
        skipped as a source (`route_sources`), so re-aiming hands it straight back.

        Removing is therefore the second tap on the thing you are pointing at, and
        adding is the drag box's job — a chain tap never adds.
        """
        if sid not in state.systems:
            return
        if self.route_rally:
            if sid in self.route_sel:
                self.route_sel.discard(sid)
            elif sid in self.seen:
                self.route_sel.add(sid)
        elif sid == self.route_dest:
            self.route_dest = None
            self.route_sel.discard(sid)  # a second tap on the target rejects it
        elif sid in self.seen:
            self.route_dest = sid
        self.recompute_route(state)

    def threatened_systems(self, state: GameState) -> set[int]:
        """The systems we hold that something is pointed at: a rival holds a
        neighbouring system, or rival ships are already inbound. Neutral neighbours
        don't count — neutral never attacks.

        The same shape as the AI's own `_threat`, recomputed here rather than
        imported, so the shell keeps its derived stats local (and `ai` stays out of
        render's reach). It discloses nothing fog hasn't: a neighbour of a system we
        hold is one hop away and a fleet inbound to one ends at a system we hold, so
        both are in full view at any sight range — the `visible` guards are there so
        that stays true if the tiers ever change.
        """
        threatened = set()
        for sid, sys in state.systems.items():
            if sys.owner_id != self.human_id:
                continue
            if any(state.systems[n].owner_id not in (self.human_id, 0) and n in self.visible
                   for n in sys.neighbors):
                threatened.add(sid)
        for f in state.fleets:
            if f.owner_id != self.human_id and state.systems.get(f.dest_id) is not None \
                    and state.systems[f.dest_id].owner_id == self.human_id:
                threatened.add(f.dest_id)
        return threatened

    def auto_rally(self, state: GameState) -> None:
        """Rally on every threatened system at once — the front line as one gesture.

        Replaces the picks rather than adding to them: it is a "do the obvious thing"
        button, and what it means has to be the same whatever was picked before. Tap
        from there to adjust. A no-op when nothing is threatened, which is also when
        render leaves the button out.
        """
        threatened = self.threatened_systems(state)
        if not threatened:
            return
        self.route_sel = threatened
        self.recompute_route(state)

    def route_sources(self) -> set[int]:
        """The chain-mode group members that will actually get a rule — everything
        picked bar the destination, which is the sink and can't forward to itself.

        Rally mode has no equivalent: its rules are laid on systems the player
        never picked, so what it plans is `route_plan` and nothing narrower.
        """
        return self.route_sel - {self.route_dest}

    def add_route_box(self, state: GameState, rect: tuple[int, int, int, int]) -> None:
        """Add every one of our systems inside a dragged screen-space box.

        Additive, so several boxes build one group. Clipped to the map viewport
        first: ``view.to_screen`` projects *every* system, including ones panned
        out under the side panel or behind the HUD bars, and only the drawing is
        clipped — an unclipped box dragged to the edge would quietly pick up
        systems that aren't on screen at all.
        """
        bx, by, bw, bh = _clip_to_play(rect)
        if bw <= 0 or bh <= 0:
            return
        for sid, sys in state.systems.items():
            if sys.owner_id != self.human_id:
                continue
            sx, sy = self.view.to_screen(sys.pos)
            if bx <= sx <= bx + bw and by <= sy <= by + bh:
                self.route_sel.add(sid)
        self.recompute_route(state)

    def set_route_dest(self, state: GameState, sid: int) -> None:
        """Aim the selection at a destination (replacing any previous one).

        Restricted to systems we have at least seen: routing to a never-seen one
        would answer "is there a path to it through my territory?" about topology
        the fog hasn't disclosed. Sources need no such guard — ``fog.observe``
        seeds every owned system, so one is never fogged.
        """
        if sid not in state.systems or sid not in self.seen:
            return
        self.route_dest = sid
        self.recompute_route(state)

    def recompute_route(self, state: GameState) -> None:
        """Rebuild the proposal from the selection. The one writer of `route_plan` /
        `route_replaces` / `route_unroutable` / `route_cycles`, called from every
        mutator above so the preview is never stale.

        A rule can only exist on a system we own (`rule_is_live`), so both sub-modes
        search over our own territory — that is forced by the rule model, not a
        policy choice. The *sinks* are exempt: a sink's incoming rule sits on the
        last owned system of the path, which is what lets either sub-mode be aimed
        at enemy systems as an assault funnel.

        Both search by **travel turns**, not hops (`flow_field(by_turns=True)`): a
        conveyor is judged by how long ships take to arrive, so the nearest rally
        point is the soonest-reached one and a route takes the fastest path rather
        than the one with fewest jumps.

        Cycle detection is shared, and so is `_add_hop`, so `keep` preservation and
        overwrite reporting are identical whichever sub-mode built the plan.
        """
        self.route_plan = {}
        self.route_replaces = set()
        self.route_unroutable = set()
        self.route_cycles = set()
        self._prune_route_sel(state)
        if self.route_rally:
            self._plan_rally(state)
        else:
            self._plan_chain(state)
        self._detect_route_cycles(state)

    def _prune_route_sel(self, state: GameState) -> None:
        """Drop picks that can no longer mean anything, so a stale selection can't
        plan. Chain sources must be systems we still hold; a rally point need only
        exist and have been seen, since it is a sink rather than a rule holder."""
        if self.route_rally:
            self.route_sel = {
                sid for sid in self.route_sel
                if sid in state.systems and sid in self.seen
            }
        else:
            self.route_sel = {
                sid for sid in self.route_sel
                if sid in state.systems and state.systems[sid].owner_id == self.human_id
            }

    def _owned(self, state: GameState) -> set[int]:
        """The systems a rule could live on — the search space for both sub-modes."""
        return {sid for sid, s in state.systems.items() if s.owner_id == self.human_id}

    def _add_hop(self, node: int, nxt: int) -> None:
        """Record one planned rule, preserving the `keep` of an existing rule that
        already pointed the same way — so re-running a route over a conveyor that is
        already correct is idempotent rather than quietly resetting tuning. Only a
        changed next hop resets it to 0 (forward everything)."""
        old = self.auto_forward.get(node)
        keep = old[1] if old is not None and old[0] == nxt else 0
        if old is not None and old != (nxt, keep):
            self.route_replaces.add(node)
        self.route_plan[node] = (nxt, keep)

    def _plan_chain(self, state: GameState) -> None:
        """Chain routing: walk each selected system's path to the destination.

        Cases worth knowing, all decided here:
          * the destination is the sink and never gets a rule of its own;
          * a selected system that *is* the destination is skipped, not dropped
            from the group and not reported unroutable — so re-aiming elsewhere
            gives it straight back as a source;
          * a selected system the search never reached goes in `route_unroutable`;
          * with no destination yet the plan is empty and so is `route_unroutable`
            (otherwise every selection would read as unroutable before aiming);
          * every system *along* a path gets a rule, including ones the player
            never selected — that is what makes ships travel the full distance.

        Two selected systems can never disagree about a shared hop: `parent` is a
        dict, so the next hop is a function of the node alone.
        """
        if self.route_dest is None or self.route_dest not in state.systems:
            return
        parent = model.flow_field(state, self._owned(state), {self.route_dest},
                                  by_turns=True)
        for sid in sorted(self.route_sources()):
            node = sid
            while node != self.route_dest:
                if node in self.route_plan:
                    break  # this hop onward is already laid by an earlier route
                nxt = parent.get(node)
                if nxt is None:
                    self.route_unroutable.add(sid)
                    break
                self._add_hop(node, nxt)
                node = nxt

    def _plan_rally(self, state: GameState) -> None:
        """Rally routing: every owned system forwards toward its nearest rally point,
        with ties split to even out the load.

        Nearest is `model.flow_costs` — travel turns, multi-source. Where a system is
        genuinely equidistant from two rally points, sending it to whichever is
        already drawing less is free: the ships arrive just as soon either way, and
        the alternative (an arbitrary but consistent tie-break) piles a whole region
        onto one point while its neighbour idles. Load is measured in **ships per
        turn**, not systems, since that is the flow the rally point actually has to
        absorb — four barren systems are less of a stream than one rich one. That is
        `1 / production` (`System.production` is turns *per ship*, so lower is
        richer), the same figure `fog.player_totals` reports. It counts inflow only:
        a rally point's own output isn't something the plan directed anywhere.

        Assigning nearest-first is what makes that exact rather than a guess: a
        node's next hop is always strictly nearer, so it has already been assigned,
        and `target` tells us which rally point this node's ships will really reach
        rather than which one we aimed them at. Every hop of every path gets a rule,
        so the plan covers the whole field; rally points get none, which is what
        makes them sinks. Everything else we hold is unroutable — a pocket cut off
        from every rally point — rather than silently left out.

        Since a chosen hop always steps to a strictly nearer node, the plan still
        cannot loop, however the ties fall.
        """
        if not self.route_sel:
            return
        owned = self._owned(state)
        cost = model.flow_costs(state, owned, set(self.route_sel))
        load = {sid: 0.0 for sid in self.route_sel}
        target: dict[int, int] = {}   # node -> the rally point its ships end at
        for node in sorted(cost, key=lambda n: (cost[n], n)):
            if node in self.route_sel:
                continue
            best = None
            for nbr in sorted(state.systems[node].neighbors):
                if nbr not in cost:
                    continue
                step = state.travel_turns(node, nbr) or 1
                if cost[nbr] + step != cost[node]:
                    continue          # not a hop along a shortest route
                dest = nbr if nbr in self.route_sel else target.get(nbr)
                if dest is None:
                    continue          # that neighbour leads nowhere we can reach
                key = (load[dest], dest, nbr)
                if best is None or key < best[0]:
                    best = (key, nbr, dest)
            if best is None:
                continue
            _key, nxt, dest = best
            self._add_hop(node, nxt)
            target[node] = dest
            prod = state.systems[node].production
            load[dest] += 1.0 / prod if prod > 0 else 0.0
        self.route_unroutable = owned - set(self.route_plan) - self.route_sel

    def _detect_route_cycles(self, state: GameState) -> None:
        """Flag systems where the plan would close a loop with a *surviving* rule.

        The plan alone can't loop — each hop steps strictly closer to a sink. But a
        sink itself gets no plan rule, so if one already forwards back into the plan
        (directly, or down a chain of rules on systems the plan doesn't touch) ships
        circulate forever. In rally mode that is the *only* way a loop can form, the
        plan covering every reachable system: an owned rally point still carrying a
        rule from before is a sink that isn't one. Friendly arrivals
        are lossless, so nothing is destroyed; the ships simply never reach a
        front, which is worse than losing them because it looks like it is
        working. `confirm_route` breaks any loop it finds here.
        """
        merged: dict[int, int] = {
            src: dest for src, (dest, _keep) in self.auto_forward.items()
            if self.rule_is_live(state, src)
        }
        merged.update({src: dest for src, (dest, _keep) in self.route_plan.items()})
        for start in self.route_plan:
            walked: list[int] = []
            seen: set[int] = set()
            node: Optional[int] = start
            while node is not None and node not in seen:
                seen.add(node)
                walked.append(node)
                node = merged.get(node)
            if node is not None:  # re-entered the path we came down: a loop
                self.route_cycles.update(walked[walked.index(node):])

    def confirm_route(self, state: GameState) -> None:
        """Write the proposal into `auto_forward` and leave the mode.

        Recomputes first so the rules committed are the ones just previewed,
        rather than resting on an argument about what else could have run in
        between.
        """
        self.recompute_route(state)
        plan = dict(self.route_plan)
        # Break any loop before arming it. The closing edge is always a rule on a
        # system the plan doesn't touch (the plan itself is acyclic), so dropping
        # exactly those clears every cycle without discarding planned hops.
        for sid in self.route_cycles:
            if sid not in plan:
                self.clear_forward(sid)
        self.auto_forward.update(plan)
        self.reset_route()

    # -- selection helpers -------------------------------------------------- #
    def reset_selection(self) -> None:
        """Drop all selection/composition state. Committed orders/rules survive
        (they live in `pending`/`auto_forward`), so this only closes the popup."""
        self.mode = IDLE
        self.selected = None
        self.dest = None
        self.chosen = 0
        self.keep = 0
        self.sel_order = None
        self.forward_armed = False
        self.editing_existing = False
        self.popup_pos = None
        self.dragging_popup = False
        self.dragging_slider = False

    def rule_is_live(self, state: GameState, sid: int) -> bool:
        """Is the standing rule out of ``sid`` one that will actually fire — i.e.
        we still hold the source and the destination still exists? The same
        predicate decides whether a rule is drawn, picked off its lane, expanded
        into an order at end of turn, and editable in the popup — and, in
        `prune_forward`, whether it survives the turn at all."""
        rule = self.auto_forward.get(sid)
        src = state.systems.get(sid)
        return rule is not None and src is not None and src.owner_id == self.human_id and rule[0] in state.systems

    def rule_is_hostile(self, state: GameState, sid: int) -> bool:
        """Is the standing rule out of ``sid`` one of the "dangerous" ones
        `_draw_forward_rules` tints — live, but pointed at a system we don't hold?
        The same predicate is what `clear_dangerous_forward` sweeps."""
        return self.rule_is_live(state, sid) and state.systems[self.auto_forward[sid][0]].owner_id != self.human_id

    def edit_order(self, state: GameState, i: int) -> None:
        """Reopen the send popup on an already-queued order — the one editor for a
        ship count, whether the order is being composed or revisited.

        Editing an existing order and composing a new one are mutually exclusive,
        so this drops any in-progress source/destination selection. Re-targeting
        the order already in the popup is a no-op, so repeat clicks on its lane
        (which cycle back to it when it is the lane's only candidate) don't throw
        away a popup the player has dragged somewhere.
        """
        if not 0 <= i < len(self.pending):
            return
        if self.mode == CHOOSING and not self.forward_armed and self.sel_order == i:
            return
        o = self.pending[i]
        if o.source_id not in state.systems or o.dest_id not in state.systems:
            return  # the popup reads both ends; never point it at neither
        self.reset_selection()
        self.sel_forward = None
        self.selected = o.source_id
        self.dest = o.dest_id
        self.mode = CHOOSING
        self.forward_armed = False
        self.editing_existing = True
        self.chosen = o.ships
        self.sel_order = i

    def edit_forward(self, state: GameState, sid: int) -> None:
        """Reopen the send popup's Forward tab on a standing rule — the mirror of
        `edit_order`, and idempotent for the same reason.

        A *dormant* rule (`rule_is_live` false) is highlighted but not opened: the
        popup reads the source's garrison and the destination's owner unguarded, and
        pointing it at a system we don't hold would let the Send tab queue an order
        out of someone else's territory. Highlighting still gives the list row, its
        ×, and the X key something to act on.
        """
        if self.mode == CHOOSING and self.forward_armed and self.selected == sid:
            return
        self.reset_selection()
        self.sel_order = None
        self.sel_forward = sid
        if not self.rule_is_live(state, sid):
            return
        dest, keep = self.auto_forward[sid]
        self.selected = sid
        self.dest = dest
        self.mode = CHOOSING
        self.forward_armed = True
        self.editing_existing = True
        self.keep = keep
        # seed the Send tab too, so switching to it sends all (as composing does)
        # rather than the 1 ship a `chosen` of 0 would clamp up to
        self.chosen = self.available(state, sid)

    def clear_pending(self) -> None:
        self.pending.clear()
        self.sel_order = None
        self.order_scroll = 0  # the list it scrolled through is gone

    def scroll_orders(self, delta: int) -> None:
        """Move the queued-list window by ``delta`` rows, clamped to what render
        reported as scrollable. Clamps where we are *before* applying ``delta``, so
        an offset left stale by removed orders snaps back on the first scroll
        instead of needing one press per vanished row."""
        here = max(0, min(self.order_scroll, self.order_scroll_max))
        self.order_scroll = max(0, min(self.order_scroll_max, here + delta))

    def clear_forward(self, sid: int) -> None:
        """Remove the standing auto-forward rule out of a system, if any."""
        self.auto_forward.pop(sid, None)
        if self.sel_forward == sid:
            self.sel_forward = None

    def prune_forward(self, state: GameState) -> None:
        """Drop every rule that no longer holds — losing the source system ends its
        forwarding. A dropped rule is gone for good: keeping it would have it fire
        again, unannounced, on the turn the system was recaptured.

        Called once per turn resolution, so a rule is only ever dormant for the rest
        of the turn that lost it; `rule_is_live` still guards each use of one.
        """
        for sid in [s for s in self.auto_forward if not self.rule_is_live(state, s)]:
            self.clear_forward(sid)
