"""Transient view/UI state — everything that is about *interacting* with the
game rather than the game itself. Kept out of GameState so the simulation core
stays pure. Shared by input.py (mutates it) and render.py (reads it).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from . import coach, combat, config, model, stats, turnfilm
from .geometry import WorldView
from .model import ForwardRule, GameState, Order

if TYPE_CHECKING:
    from .gauntlet import Target

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

# The invite overlay's row for the spectator link (`Ui.pbp_invite`): not a seat,
# so outside 1..6, and not 0, which `pbp_invite_copied` uses for "no row".
SPECTATE_ROW = -1


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
    node_id: int | None
    low_id: int | None
    high_id: int | None
    at: float
    burst_color: tuple[int, int, int]
    losses: tuple[tuple[int, int], ...]   # (owner, ships lost), in resolution order


@dataclass(frozen=True)
class FadingHull:
    """A finished hull, interpreted once at the moment it fired — the
    production half of `FadingFight`."""

    age_ms: float
    node_id: int
    hulls: int
    owner_id: int


def read_marks(board: GameState, events: list[turnfilm.Event], sees, age_ms: float = 0.0
               ) -> tuple[list[FadingFight], list[FadingHull]]:
    """The marks a turn's ``events`` earn, read once: a fight's losses and a
    finished hull, for whatever ``sees(sid)`` allows. ``board`` gives a hull's
    owner, so it should be one from before the turn's fights (production comes
    first). A second fight or hull at the same system supersedes the first, so
    one place never carries two of a kind. Shared by the fading marks a film
    leaves (`Ui.archive_marks`) and history's held ones (`Ui.hold_marks`).
    """
    fights: list[FadingFight] = []
    hulls: list[FadingHull] = []
    for event in events:
        if isinstance(event, turnfilm.Clashed):
            if not sees(event.low_id) and not sees(event.high_id):
                continue
            fights.append(FadingFight(
                age_ms=age_ms, node_id=None, low_id=event.low_id, high_id=event.high_id,
                at=event.at, burst_color=config.COLOR_TEXT,
                losses=event.losses))
        elif isinstance(event, turnfilm.Landed):
            # `steps` is empty for a reinforcement or an unopposed landing —
            # neither is a fight, so neither earns a mark.
            if not event.steps or not sees(event.node_id):
                continue
            color = (config.COLOR_TEXT if event.owner_id == event.was_owner
                     else config.player_color(event.owner_id))
            fights = [f for f in fights if f.node_id != event.node_id]
            fights.append(FadingFight(
                age_ms=age_ms, node_id=event.node_id, low_id=None, high_id=None,
                at=0.0, burst_color=color, losses=event.losses))
        elif isinstance(event, turnfilm.Produced):
            for sid, n in event.hulls:
                if not sees(sid):   # a rival's yard is not ours to report
                    continue
                hulls = [h for h in hulls if h.node_id != sid]
                hulls.append(FadingHull(age_ms=age_ms, node_id=sid, hulls=n,
                                        owner_id=board.systems[sid].owner_id))
    return fights, hulls


@dataclass
class Ui:
    view: WorldView
    human_id: int = 1
    mode: str = IDLE
    selected: int | None = None  # any system, to read about; a send's source when ours (`source`)
    hover: int | None = None  # system under the cursor
    dest: int | None = None  # chosen destination system id
    chosen: int = 0  # ships the active one-shot send commits
    # In CHOOSING the send is already committed: as a one-shot order at
    # `sel_order` (when forward_armed is False, sized by `chosen`) or as the lane
    # to `dest` of the standing rule out of `selected` in `auto_forward` (when
    # forward_armed is True: the popup edits that lane's share and the system's
    # hold). The popup edits whichever is live.
    forward_armed: bool = False  # active send is a standing forward rule
    # True when the popup was opened on an order/rule that *predates* it (see
    # `edit_order`/`edit_forward`) rather than one it just created. Can't be
    # derived: the popup commits immediately, so a fresh compose and a reopened
    # order look identical by the time it is on screen. Two things read it — the
    # bottom button's label (Cancel vs Delete) and how far `_close_send` unwinds.
    editing_existing: bool = False
    pending: list[Order] = field(default_factory=list)
    sel_order: int | None = None  # index into `pending` being edited, if any
    # standing auto-forward rules: source_id -> ForwardRule (a hold, and a share
    # per lane). Human-only QoL, so it lives here rather than in the pure
    # GameState. Each turn a rule sends each lane its share of (free ships - hold)
    # (`forward_this_turn`, expanded by `forward_orders`).
    auto_forward: dict[int, ForwardRule] = field(default_factory=dict)
    sel_forward: int | None = None  # source id of the rule being edited, if any
    # source_id -> the turn a lane was last added to its rule from the popup (never
    # by a route, which clears it). A rule set up this turn opens the popup on its
    # Forward tab and a further lane splits it; one from an earlier turn, or laid
    # by a route, is replaced outright by the next lane added (see `toggle_forward`).
    forward_set_on: dict[int, int] = field(default_factory=dict)
    # The rule a fresh compose replaced, kept so Cancel or the Send tab can put
    # it back as it was: (source, rule). None when nothing was replaced.
    forward_replaced: tuple[int, ForwardRule] | None = None
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
    #   route_plan       — source -> ForwardRule: the rules a confirm writes.
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
    #   route_clears     — rally only: systems whose live rule the plan leaves
    #                      out (a rally point, a pocket no rally point reaches),
    #                      which a confirm deletes, so the plan is the whole of
    #                      the empire's forwarding (see `_plan_rally`)
    route_sel: set[int] = field(default_factory=set)
    route_dest: int | None = None
    # The sub-mode is a preference, not part of the proposal: `reset_route` leaves
    # it alone so re-entering the mode comes back where you left it.
    route_rally: bool = True
    route_plan: dict[int, ForwardRule] = field(default_factory=dict)
    route_replaces: set[int] = field(default_factory=set)
    route_unroutable: set[int] = field(default_factory=set)
    route_cycles: set[int] = field(default_factory=set)
    route_clears: set[int] = field(default_factory=set)
    # Fog of war (human-only, so it lives here not in GameState). Recomputed each
    # turn from the human's territory (`app.refresh_fog`).
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
    # The standing rules on each history board (`GameLog.rules_for`, the live
    # ones on the latest) and the live rules, held aside while `auto_forward`
    # shows the reviewed board's (`show_history_rules`, the same swap as fog);
    # `leave_history_rules` puts them back.
    history_rules: list[dict[int, ForwardRule]] = field(default_factory=list)
    live_rules: dict[int, ForwardRule] | None = None
    # End-of-game graphs over the reviewed map (`render._draw_history_graph`),
    # docked over the map's lower part or, with `graph_full`, filling the whole
    # space between the bars; the scrubber stays either way. `history_chart` is
    # read off the history boards once, on entering review (`stats.chart`), and
    # dropped on leaving it; `graph_metric` indexes `stats.METRICS`;
    # `graph_hidden` is the seats whose lines the legend has switched off. Open
    # by default on a finished game.
    history_chart: stats.Chart | None = None
    # Which bot drives each seat, for the top bar's hover tooltip and the graph
    # legend: ``{pid: "Knower · Search"}``, worked out once by `main.new_ui`
    # (render imports no `ai` to name a stop with). `random_seats` were left to
    # `settings.RANDOM_STRATEGY` and read "Random" until `bots_revealed`.
    seat_bots: dict[int, str] = field(default_factory=dict)
    random_seats: frozenset[int] = frozenset()
    history_graph: bool = False
    graph_full: bool = False
    graph_metric: int = 0
    # The post-game coach (`coach.review`), read off the history boards once
    # on entering review of a decided game, and dropped on leaving it; None mid-game,
    # where it would give away what fog hid. `coach_open` puts its notes in the
    # graph panel in place of a chart (the Coach tab), `coach_sel` is the note
    # being looked at (-1: none yet).
    coach_review: coach.Review | None = None
    coach_open: bool = False
    coach_sel: int = -1
    graph_hidden: set[int] = field(default_factory=set)
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
    film: turnfilm.Film | None = None
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
    # Where the player dragged the send popup (its top-left), or None to let the
    # page place it beside the lane; cleared on a new target.
    popup_pos: tuple[int, int] | None = None
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
    # Training help (`main.take_hint`): a bot's suggested sends for the live turn,
    # drawn as dashed arrows while `hint_turn` is the board's turn, less what our
    # own orders already send (`hint_left`). `hinted` is sticky — read back from
    # the log on resume (`GameLog.hinted_from`) — and takes the result off the
    # leaderboard (`can_post`).
    hint_orders: list[Order] = field(default_factory=list)
    hint_turn: int = -1
    hinted: bool = False
    # Play-by-post: this match is shared, and this seat is ours. `pbp_match` is
    # the match id, `pbp_waiting` the seats still to move for the live turn, and
    # `pbp_submitted` whether we have sent ours — which is what puts the waiting
    # overlay up. Held here rather than on `GameState` for the reason every other
    # human-only concern is: the simulation neither knows nor cares that the seat
    # beside it is a person somewhere else.
    pbp_match: str = ""
    pbp_title: str = ""          # the creator's title for it, "" for none
    pbp_submitted: bool = False
    pbp_waiting: tuple[int, ...] = ()
    pbp_msg: str = ""            # what the last call said, drawn on the overlay
    # Every other seat's link, shown once right after *we* create the match —
    # "a modal listing the seats with a Copy button each" rather than a status
    # line over a clipboard blob nobody thought to check (`main.pbp_seat_links`
    # fills it in, only on the creation path — following someone else's link
    # never sets it). `(seat, link)` pairs, plus `(SPECTATE_ROW, link)` when the
    # match takes spectators; empty means there is nothing to show, which is
    # what render/input gate the overlay's presence on.
    pbp_invite: tuple[tuple[int, str], ...] = ()
    pbp_invite_copied: int = 0   # seat whose row last showed "Copied" feedback
    challenge_target: tuple[int, int] | None = None
    challenge_by: str = ""
    # Ironman mode (`webstore.ironman`), stamped by `main.new_ui` when the match
    # starts so neither render nor input reads the store once a frame. It only
    # takes History away while the game is undecided — see `can_review`.
    ironman: bool = False
    # The day this match's setup is the daily challenge of ("2026-10-28"), or
    # blank. Stamped by `main.new_ui` from `daily.daily_of`: a daily plays
    # ironman, and its win overlay copies a result line, not a challenge link.
    daily_day: str = ""
    # ...and which daily it is, "board" or "puzzle" (`daily.KINDS`), blank with
    # the day.
    daily_kind: str = ""
    # A puzzle won wholly by hand asks the board whether its (turns, lost) met
    # the par (`main`, on `par_due`, which it clears); `par_note` is the
    # congratulation, blank unless it did (`daily.par_note`). The par itself
    # never reaches a player short of it.
    par_due: tuple[int, int] | None = None
    par_note: str = ""
    # The weekly campaign's reminder for this setup, e.g. "Node 3: post within 12
    # min", or None when it isn't a campaign node (or nobody could ask).
    # Written by `main` once a frame from `campaign.label`, so the countdown
    # moves; `render` only draws it, at the side panel's head (`_match_notes`).
    campaign_label: str | None = None
    # The campaign's answer for this setup when this match was started
    # (`campaign.stamp`), carried onto a posted score as `Challenge.campaign`.
    # Blank for a match nobody looked up, or one resumed from a save.
    campaign_start: str = ""
    # A gauntlet stage (the tutorial's, say) this match is playing, as plain
    # values `main` copies off the stage when the match starts, so render never
    # sees a `Gauntlet`. `stage_label` ("Tutorial 3/10 · Defender's edge") is None
    # for an ordinary match, and is what every other stage control is gated on.
    # The briefing card is up over the board while `briefing_open`; any press
    # closes it and the hint line in the side panel opens it again. `has_next_stage` puts Next stage where New map was on the win
    # overlay: a stage's seed is pinned, so a new map would leave the stage.
    # `stage_target` is the stage's `gauntlet.Target` (a plain value), and
    # `stage_met_before` whether a best filed before this match already meets it.
    stage_label: str | None = None
    stage_title: str = ""
    briefing: tuple[str, ...] = ()
    briefing_open: bool = False
    stage_hint: str | None = None
    has_next_stage: bool = False
    stage_target: Target | None = None
    stage_met_before: bool = False
    # The stage's gated ways out, as (label, far stage's title, target, met by
    # a best filed before this match): what a win here may unlock.
    stage_gates: tuple[tuple[str, str, Target, bool], ...] = ()
    # The run this match is the undecided game of (`run.Run.id`), or blank. Set
    # by `main.run_ui` only on the run's own game: a Retry or fork of it is free
    # play. A run game plays ironman and offers no Hint, and its Next stage
    # follows this game's score alone. `run_note` is the overlay's line once
    # `main.record_run_step` has judged the game (blank before), `run_ok` its
    # colour. `run_at`/`run_total` are the stage's place in the run ("3/8").
    run_id: str = ""
    run_at: int = 0
    run_total: int = 0
    run_note: str = ""
    run_ok: bool = True
    # The stage the run is playing, and its lives left as the game began (0
    # where the run doesn't count lives: one life and no bonus stage). Once the
    # game is judged: `run_on` while the run goes on, `run_next` the stage Next
    # stage begins (`run.next_up`), `run_again` when the game cost a life and
    # Try again may replay the stage as a new step.
    run_stage: int | None = None
    run_lives: int = 0
    run_on: bool = False
    run_next: int | None = None
    run_again: bool = False
    # The system whose star info card (`starlore`) is up over the board, or None.
    # The i button beside a selected system's name in the side panel and the I
    # key open it; any press closes it.
    lore_sid: int | None = None
    # The send popup's forecast row opens a card over the board
    # setting out how its numbers were reached (`render._draw_odds_card`); any
    # press closes it, as the star info card.
    odds_open: bool = False
    share_msg: str = ""  # outcome of the last share, drawn under the buttons
    # Quick post (`share.quick_post`): the name the leaderboard button posts under
    # without the entry form ("" for the form), and how far the post has got:
    # "", "posting", "posted" (the button then opens the map's page, `posted_key`)
    # or "failed" (the button falls back to the form). `post_as` starts as the
    # remembered name; `post_name_due` asks main to replace it with the signed-in
    # player's claimed name, if they own one (`share.signed_in_name`).
    post_as: str = ""
    post_name_due: bool = False
    post_state: str = ""
    posted_key: str = ""
    # The footer's overflow: a strip too narrow for every button folds the least
    # essential behind one "more" button (and a compact screen always folds the
    # exits), and opening it lists them above it.
    more_open: bool = False
    # The selection state from just before the latest press, put back when that
    # press turns out to be the first finger of a pinch rather than a tap.
    press_undo: tuple | None = None

    # -- camera ------------------------------------------------------------- #
    def stop_film(self) -> None:
        """Abandon a playback. Main drops its reel whenever there is no film, so
        this is the whole of "skip" — safe at any moment, because the turn it was
        showing has already been resolved.

        Deliberately does *not* clear `deferred_view_snap`: a skip leaves what
        the film was holding back still owed, and the session settles it.
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
        fights, hulls = read_marks(board, events, self.sees)
        # A second fight (or hull) at the same system supersedes the first.
        # Turns chain straight into each other, so the previous one's mark can
        # still be fading here, and two bursts with two costs stacked on one
        # node read as a garbled number rather than as two fights.
        taken = {f.node_id for f in fights if f.node_id is not None}
        self.fading_fights = [f for f in self.fading_fights if f.node_id not in taken] + fights
        built = {h.node_id for h in hulls}
        self.fading_hulls = [h for h in self.fading_hulls if h.node_id not in built] + hulls

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
        self.view.fit_to([state.systems[sid].pos for sid in self.snap_points(state)])

    def snap_points(self, state: GameState) -> list[int]:
        """The systems `reset_view` frames: every one once there is nothing left
        to hide, else those seen so far. A frontend with its own camera frames
        these."""
        if state.winner is not None or state.is_defeated(self.human_id):
            return list(state.systems)
        return [sid for sid in state.systems if sid in self.seen]

    def graph_seats(self) -> tuple[int, ...]:
        """The seats the graphs' legend offers: every seat once review is
        revealed (a finished game), else ours and each rival the top bar has
        shown at some turn — mid-game the chart is `stats.masked` to the turns
        each was in sight, so a never-sighted rival has no curve to offer."""
        chart = self.history_chart
        if chart is None:
            return ()
        if self.history_reveal:
            return chart.seats
        return tuple(pid for pid in chart.seats if pid == self.human_id or chart.sighted(pid))

    def bots_revealed(self, state: GameState) -> bool:
        """May a random seat's bot be named? Only once the game is decided — on
        the board itself, or in review of a finished game, whose boards are all
        undecided but one. A knocked-out spectator still waits: the bot is half
        of what the remaining seats are playing against."""
        return state.winner is not None or (self.history and self.history_reveal)

    def bot_label(self, state: GameState, pid: int) -> str | None:
        """What drives ``pid`` as the HUD names it, or None for neutral and for a
        seat a person holds (on this board: one claimed later still read as a bot
        on the turns before). A random seat reads "Random" until
        `bots_revealed`, then its bot with "(random)" after it."""
        player = state.players.get(pid)
        if player is None or player.is_neutral or player.is_human:
            return None
        bot = self.seat_bots.get(pid)
        if pid in self.random_seats:
            if not self.bots_revealed(state):
                return "Random"
            return f"{bot} (random)" if bot else "Random"
        return bot

    def graph_traces(self) -> tuple[int, ...]:
        """The seats whose lines the graphs draw: offered and not hidden."""
        return tuple(pid for pid in self.graph_seats() if pid not in self.graph_hidden)

    def select_note(self, i: int) -> None:
        """Look at the coach's note ``i``: History jumps to the board it names."""
        if self.coach_review is None or not self.coach_review.notes:
            return
        self.coach_sel = max(0, min(i, len(self.coach_review.notes) - 1))
        self.history_turn = max(0, min(self.coach_review.notes[self.coach_sel].turn, self.history_max))
        self.playing = False
        self.clear_fading_marks()   # a jump, as with the scrubber

    def show_history_rules(self) -> None:
        """Under review, `auto_forward` is the rules standing on the board on
        screen, not the end of the game's, so every reader (the map, the panel,
        a hold badge) shows that turn's as it was played."""
        if self.live_rules is None:
            return
        i = self.history_turn
        self.auto_forward = self.history_rules[i] if 0 <= i < len(self.history_rules) else {}

    def leave_history_rules(self) -> None:
        """Back to the live rules on leaving review."""
        if self.live_rules is not None:
            self.auto_forward = self.live_rules
        self.live_rules = None
        self.history_rules = []

    def toggle_trace(self, pid: int) -> None:
        """Show or hide ``pid``'s line on the graphs."""
        if pid in self.graph_hidden:
            self.graph_hidden.discard(pid)
        elif pid in self.graph_seats():
            self.graph_hidden.add(pid)

    # -- spectating --------------------------------------------------------- #
    def can_review(self, state: GameState) -> bool:
        """Is History (and so rewind) available?

        It needs a recorded turn to scrub through, and under ironman it waits for
        the game to be decided: reviewing is where a bad turn gets rewound. Render
        gates the footer button on this and main gates entering on it, so the H
        key and the button can never disagree.
        """
        return state.turn > 0 and not (self.ironman and state.winner is None)

    def can_undo(self, state: GameState) -> bool:
        """Is Undo (U) available: rewinding the live game one turn?

        It is History's rewind to the turn before, so it asks what History does
        (`can_review`: a recorded turn, and not under ironman while undecided),
        and only in live play of an undecided game of our own: a decided game is
        rewound from History, which forks it, and a shared match is never ours
        to rewind.
        """
        return (self.can_review(state) and state.winner is None
                and not self.in_pbp and not self.history)

    def can_fast_forward(self, state: GameState) -> bool:
        """Is the 'just show me who wins' control available?

        Only while the human seat is knocked out and the match is still running:
        there are no decisions left to rush past then, and the alternative is
        sitting through a conclusion you have no part in. Render gates the footer
        button on this and main gates the F key on it, so the two can never
        disagree about when fast forward means anything.
        """
        return state.winner is None and state.is_defeated(self.human_id)

    @property
    def in_pbp(self) -> bool:
        """Whether this is a shared match rather than a game of our own."""
        return bool(self.pbp_match)

    @property
    def can_retry(self) -> bool:
        """Whether live play offers Retry (T): restarting this match from turn 0.

        Not in a shared match, whose log is the table's record and is decided
        once (`pbp.settled_turn`), not one seat's to start over.
        """
        return not self.in_pbp

    @property
    def spectating(self) -> bool:
        """Whether we are watching a shared match from no seat at all.

        A spectator sits at neutral's id (``pbp.SPECTATOR``), which no person
        holds, so nothing on the board is ours: input takes no orders, the End
        Turn block says so instead, and the whole board is in view
        (``main._accumulate_fog``).
        """
        return self.in_pbp and self.human_id == 0

    def awaiting_others(self, state: GameState) -> bool:
        """Whether to hold the board and say we are waiting.

        Only once *we* have submitted: before that the turn is ours to play, and
        the fact that somebody else is already in is not something to interrupt
        anyone with. A decided match is nobody's turn, so it never waits.
        """
        return (self.in_pbp and self.pbp_submitted
                and state.winner is None)

    def can_post(self, state: GameState) -> bool:
        """Is this result the player's own to publish — as a challenge link or as
        a leaderboard entry?

        Four things must hold: the human's seat won it, at least one turn was
        decided by hand (a pure autoplay demo is a bot's win, not a score), the
        match was played here rather than downloaded to watch, and it was a game
        of our own rather than a shared one. Render gates both overlay buttons on
        this and main gates both actions on it, so a keyboard shortcut can never
        reach a result the overlay declines to offer.

        The fourth is what keeps play-by-post off the board, and it is a matter
        of meaning rather than of trust: a leaderboard score is turns-to-win
        against a fixed setup, which measures a person against a map. Beating two
        friends to the same map in nine turns says nothing about that setup, and
        posting it as though it did would quietly corrupt every honest score
        beside it.

        The third condition is not airtight and is not meant to be: rewinding a
        watched replay to a turn from its end and playing that turn out forks a
        match of your own (`resume_game` builds a fresh `Ui`), which this will then
        happily let you post. Sealing that would mean recording where a fork
        branched and carrying it through every later rewind — a bigger idea than
        the problem. What this stops is a posted score being one button-press away
        from any replay on the board.
        """
        return (state.winner == self.human_id and self.hand_turns > 0
                and not self.watched and not self.in_pbp and not self.hinted)

    def board_action(self) -> str:
        """What the leaderboard button does now: "view" the posted map's page,
        nothing while "posting", "post" under `post_as`, or "enter" a result on
        the entry form. Render labels the button by it and main acts on it."""
        if self.post_state == "posted":
            return "view"
        if self.post_state == "posting":
            return "posting"
        if self.post_as and self.post_state != "failed":
            return "post"
        return "enter"

    def can_hint(self, state: GameState) -> bool:
        """Is the Hint control offered? Only on our own live turn: not in a shared
        match (a bot's advice against friends), not while reviewing, under
        autoplay or spectating, and not once the game is decided or we are out.
        Not in a run's game either: a run is a test of the player's own play."""
        return (not self.in_pbp and not self.run_id and not self.history and not self.autoplay
                and not self.watched and state.winner is None
                and self.human_id in state.players and self.human_id != 0
                and not state.is_defeated(self.human_id))

    def hint_live(self, state: GameState) -> bool:
        """Whether the hint arrows are for the board on screen."""
        return self.hint_turn == state.turn and not self.history

    def odds_card_up(self) -> bool:
        """Is the forecast's breakdown over the board? Only while the send popup
        it was opened from is still on its Send tab."""
        return (self.odds_open and self.mode == CHOOSING and not self.forward_armed
                and self.selected is not None and self.dest is not None and not self.history)

    def hide_hint(self) -> None:
        self.hint_turn, self.hint_orders = -1, []

    def planned_sends(self, state: GameState) -> dict[tuple[int, int], int]:
        """Ships each ``(source, dest)`` lane carries out at the end of this turn
        as things stand: the queued sends plus what the rules forward."""
        sends: dict[tuple[int, int], int] = {}
        for o in (*self.pending, *self.forward_orders(state)):
            sends[(o.source_id, o.dest_id)] = sends.get((o.source_id, o.dest_id), 0) + o.ships
        return sends

    def hint_left(self, state: GameState) -> list[Order]:
        """The hint's sends our orders don't already make: each lane's hinted
        ships less what our queued sends and rules put down it, where that leaves
        any. What the map draws and `apply_hint` queues."""
        planned = self.planned_sends(state)
        hinted: dict[tuple[int, int], int] = {}
        for o in self.hint_orders:
            hinted[(o.source_id, o.dest_id)] = hinted.get((o.source_id, o.dest_id), 0) + o.ships
        return [Order(self.human_id, src, dest, n - planned.get((src, dest), 0))
                for (src, dest), n in hinted.items() if n > planned.get((src, dest), 0)]

    def apply_hint(self, state: GameState) -> None:
        """Queue what is left of the hint (`hint_left`) as our own sends, then put
        the hint away.

        A queued send takes its ships ahead of the source's rule, which forwards
        what is left, so topping up one lane can thin the rule's send down it:
        this repeats until every lane carries the hint or its source has nothing
        free. A send whose ships we have already promised elsewhere is cut to what
        is free, never taken from our other orders."""
        if not self.hint_live(state):
            return
        if self.mode == CHOOSING:
            self._close_send()   # the popup's count would no longer be its order's
        added = True
        while added:
            added = False
            for o in self.hint_left(state):
                n = min(o.ships, self.available(state, o.source_id))
                if n <= 0:
                    continue
                same = next((p for p in self.pending
                             if (p.source_id, p.dest_id) == (o.source_id, o.dest_id)), None)
                if same is not None:
                    same.ships += n
                else:
                    self.pending.append(Order(self.human_id, o.source_id, o.dest_id, n))
                added = True
        self.hide_hint()

    @property
    def in_stage(self) -> bool:
        return self.stage_label is not None

    def met_target(self, state: GameState) -> bool:
        """Does this win meet the stage's target (any win, with none)?"""
        if self.stage_target is None:
            return True
        return self.stage_target.met((state.turn, state.players[self.human_id].ships_lost))

    def stage_cleared(self, state: GameState) -> bool:
        """Is the stage cleared once this win is filed? `main.record_best` files
        it on the same `can_post` gate, and a stage is cleared by a best that
        meets its target (`gauntlet.cleared`): this game's, or an earlier one."""
        return self.can_post(state) and (self.stage_met_before or self.met_target(state))

    def unlocked(self, state: GameState) -> list[tuple[str, str]]:
        """The gated ways out this stage has open once this win is filed, as
        (label, title): ones a best already met, and ones this win meets, filed
        on the same `can_post` gate as the best. A way still shut says nothing,
        so a hidden stage stays hidden."""
        score = (state.turn, state.players[self.human_id].ships_lost)
        return [(label, title) for label, title, target, before in self.stage_gates
                if before or (self.can_post(state) and target.met(score))]

    def offers_next_stage(self, state: GameState) -> bool:
        """Is Next stage on the win overlay? Only where it leads to a stage the
        list would show open: on along the one ungated lane once the stage is
        cleared (`stage_cleared`), or, with no ungated way on, to the one stage a
        gated lane has open (`unlocked`), as `gauntlet.next_stage` reads it. A
        gate open beside an ungated way is a fork: choose on the list. In a run
        only this game's score counts: the run's one next stage (`run_next`, set
        by `main.record_run_step`)."""
        if not self.in_stage:
            return False
        if self.run_id:
            return bool(self.run_note) and self.run_next is not None
        gates = self.unlocked(state)
        if self.has_next_stage:
            return not gates and self.stage_cleared(state)
        return len(gates) == 1

    # -- ship accounting ---------------------------------------------------- #
    def committed(self, sid: int) -> int:
        """Ships already promised out of a system by queued (not yet run) orders.

        Nothing while reviewing history: the queue is for the live board, and
        taking it off a past board's garrison would show counts that never were
        (sending all 20 out of a system now read as -18 on a turn it held 2)."""
        if self.history:
            return 0
        return sum(o.ships for o in self.pending if o.source_id == sid)

    def landing_at(self, state: GameState, dest: int, turns: int) -> int:
        """Our ships landing on ``dest`` in ``turns`` turns: queued sends whose
        lane takes exactly that long, and fleets already that far out. They
        land together, so they fight as one (`combat.resolve_arrival`)."""
        queued = sum(o.ships for o in self.pending
                     if o.dest_id == dest and state.travel_turns(o.source_id, dest) == turns)
        flying = sum(f.ships for f in state.fleets
                     if f.owner_id == self.human_id and f.dest_id == dest
                     and f.turns_remaining == turns)
        return queued + flying

    def available(self, state: GameState, sid: int) -> int:
        """Ships still free to deploy from a system this turn."""
        return state.systems[sid].ships - self.committed(sid)

    def count_adjust_active(self) -> bool:
        """True while the mouse wheel should nudge a ship count (``step_count``
        below would do something) rather than zoom the camera — mirrors
        ``step_count``'s own dispatch conditions exactly, so the two can't
        drift out of sync."""
        return self.mode == CHOOSING and self.selected is not None

    def step_count(self, state: GameState, delta: int) -> None:
        """Nudge the count being adjusted by ``delta`` steps — the shared logic
        behind both the mouse wheel and the popup's −/+ buttons: ships on the Send
        tab, the edited lane's share (`config.FORWARD_STEP_PCT` a step) on the
        Forward tab."""
        if not self.count_adjust_active():
            return
        if self.forward_armed:
            self.step_share(delta)
        else:
            self.set_send_count(state, self.chosen + delta)

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
            return (0, 100, self.edited_share())
        cap = self._active_cap(state)
        return (1 if cap > 0 else 0, cap, self.chosen)

    def set_slider_fraction(self, state: GameState, t: float) -> None:
        """Set the count being edited to fraction ``t`` (clamped to 0..1) of the
        slider's range: what a press at that point along the track means."""
        lo, hi, _value = self.slider_range(state)
        if hi <= lo:
            return
        t = max(0.0, min(1.0, t))
        count = round(lo + t * (hi - lo))
        if self.forward_armed:
            snap = config.FORWARD_SNAP_PCT
            self.set_share(round(count / snap) * snap)
        else:
            self.set_send_count(state, count)

    # -- active send (CHOOSING) --------------------------------------------- #
    def _active_cap(self, state: GameState) -> int:
        """The most ships the active send can commit from ``selected``."""
        if self.selected is None:
            return 0
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
        self.forward_replaced = None  # a replace by an earlier compose is committed
        self.editing_existing = False  # this popup created its own subject
        self.chosen = max(0, avail)
        self.popup_pos = None  # fresh target -> auto-place the popup
        if avail > 0:
            self.pending.append(Order(self.human_id, self.selected, dest, avail))
            self.sel_order = len(self.pending) - 1
        else:
            self.sel_order = None  # nothing to send now; forward-only rule
        if forward:
            self.toggle_forward(state)

    def set_send_count(self, state: GameState, count: int) -> None:
        """Set the active one-shot send's ship count (clamped) on its order.
        Send-tab only — forwarding is sized by shares (see set_share)."""
        if self.mode != CHOOSING or self.forward_armed or self.selected is None:
            return
        cap = self._active_cap(state)
        lo = 1 if cap > 0 else 0  # an empty source sends nothing (0), not 1
        self.chosen = max(lo, min(cap, count))
        if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
            self.pending[self.sel_order].ships = self.chosen

    # -- the Forward tab ------------------------------------------------------ #
    def edited_rule(self) -> ForwardRule | None:
        """The standing rule the Forward tab is editing, if it is open."""
        if self.mode != CHOOSING or not self.forward_armed or self.selected is None:
            return None
        return self.auto_forward.get(self.selected)

    def edited_share(self) -> int:
        """The edited lane's share of its system's surplus (0 when there is none)."""
        rule = self.edited_rule()
        return rule.shares.get(self.dest, 0) if rule is not None and self.dest is not None else 0

    def set_share(self, pct: int) -> None:
        """Move the edited lane's share toward ``pct`` (see `model.shares_set`: a rise
        takes from what stays home first, then from the other lanes). Taking it to
        nothing deletes the lane, as the bottom button would: a lane that sends
        nothing is not a rule. A *different* lane emptied by a rise stays, at 0%,
        so it can be raised again rather than vanishing under another lane's +."""
        rule = self.edited_rule()
        if rule is not None and self.dest in rule.shares:
            rule.shares = model.shares_set(rule.shares, self.dest, pct)
            if rule.shares[self.dest] == 0:
                self.cancel_send()

    def step_share(self, steps: int) -> None:
        self.set_share(self.edited_share() + steps * config.FORWARD_STEP_PCT)

    def step_hold(self, delta: int, state: GameState | None = None) -> None:
        """Nudge how many ships the edited system holds back, across all its lanes.
        On a smart hold it takes the hold back by hand, stepping from the figure the
        smart hold had reached, so the number moves the way the button says."""
        rule = self.edited_rule()
        if rule is not None:
            smart = rule.smart and state is not None and self.selected is not None
            start = self.hold_of(state, self.selected) if smart else rule.hold
            rule.smart = False
            rule.hold = max(0, min(config.FORWARD_HOLD_MAX, start + delta))

    def toggle_smart_hold(self) -> None:
        """Switch the edited system between a hold by hand and a smart one."""
        rule = self.edited_rule()
        if rule is not None:
            rule.smart = not rule.smart

    def hold_of(self, state: GameState, sid: int) -> int:
        """Ships ``sid``'s rule keeps back this turn: its hold, or for a smart hold
        enough to keep the system against the heaviest wave flying in."""
        rule = self.auto_forward.get(sid)
        if rule is None:
            return 0
        if rule.smart and sid in state.systems:
            return combat.smart_hold(state, sid, self.human_id)
        return rule.hold

    def focus_lane(self, dest: int) -> None:
        """Point the Forward tab at another lane of the same system."""
        rule = self.edited_rule()
        if rule is not None and dest in rule.shares:
            self.dest = dest

    def even_split(self) -> None:
        rule = self.edited_rule()
        if rule is not None:
            rule.shares = model.shares_even(rule.shares)

    def delete_all_forward(self) -> None:
        """Drop every lane of the edited system's rule, and close the popup."""
        if self.edited_rule() is None or self.selected is None:
            return
        self.clear_forward(self.selected)
        self._close_send()

    def forward_orders(self, state: GameState) -> list[Order]:
        """Standing auto-forward rules as this turn's orders.

        A rule holds back its ``hold`` and sends each lane its share of the rest
        (`forward_this_turn`, the same figures the popup shows). Ships already
        promised by manually-queued orders from the same source are not counted as
        free, so a rule cooperates with (rather than double-counts) manual sends.
        """
        orders: list[Order] = []
        for src in self.auto_forward:
            # the same test that decides whether the rule is drawn, pickable, editable
            # and (at the end of this turn, in `prune_forward`) kept at all
            if not self.rule_is_live(state, src):
                continue
            for dest, send in self.forward_this_turn(state, src).items():
                if send > 0 and state.are_adjacent(src, dest):
                    orders.append(Order(self.human_id, src, dest, send))
        return orders

    def forward_this_turn(self, state: GameState, sid: int) -> dict[int, int]:
        """Ships each lane of ``sid``'s rule will send at the end of this turn: its
        share of the free ships beyond the hold. The one formula behind both the
        orders `forward_orders` issues and every number the popup and
        panel show, so what you read is what goes."""
        rule = self.auto_forward.get(sid)
        if rule is None or sid not in state.systems:
            return {}
        surplus = self.available(state, sid) - self.hold_of(state, sid)
        return model.forward_split(surplus, rule.shares, state.turn)

    def send_all(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state))

    def send_half(self, state: GameState) -> None:
        self.set_send_count(state, self._active_cap(state) // 2)

    def forwarded_this_turn(self, state: GameState, sid: int) -> bool:
        """Was ``sid``'s standing rule set up this turn? Such a rule opens the
        popup on its Forward tab, and a lane added to it splits it."""
        return sid in self.auto_forward and self.forward_set_on.get(sid) == state.turn

    def opens_on_forward(self, state: GameState, sid: int) -> bool:
        """Should a send composed from ``sid`` open on the Forward tab? When it
        has no ships to send now (forwarding future production is the only useful
        action), or when it already forwards by a rule set up this turn."""
        return self.available(state, sid) == 0 or self.forwarded_this_turn(state, sid)

    def toggle_forward(self, state: GameState) -> None:
        """Flip the active send between a one-shot order (sized by `chosen`) and
        a lane of the source's standing rule.

        Arming onto a rule set up this turn adds the lane with `model.shares_with`
        (a second makes it 50/50); arming onto one from an earlier turn replaces it
        with this lane alone at 100%, hold 0, keeping the old rule aside. Disarming
        undoes either — `model.shares_without`, or the old rule put back — so trying
        the Forward tab and leaving it leaves the system as it was."""
        if self.mode != CHOOSING or self.selected is None or self.dest is None:
            return
        self.forward_armed = not self.forward_armed
        sid = self.selected
        if self.forward_armed:
            if self.sel_order is not None and 0 <= self.sel_order < len(self.pending):
                del self.pending[self.sel_order]
            self.sel_order = None
            old = self.auto_forward.get(sid)
            if old is not None and not self.forwarded_this_turn(state, sid):
                self.forward_replaced = (sid, old)
                self.auto_forward[sid] = ForwardRule({self.dest: 100})
            else:
                rule = self.auto_forward.setdefault(sid, ForwardRule())
                rule.shares = model.shares_with(rule.shares, self.dest)
            self.forward_set_on[sid] = state.turn
        else:
            self._unarm_lane(sid, self.dest)
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

    def forward_lanes(self) -> int:
        """How many lanes the standing rules forward down, all systems together."""
        return sum(len(rule.shares) for rule in self.auto_forward.values())

    def clear_dangerous_forward(self, state: GameState) -> None:
        """Remove only the lanes currently pointed at a system we don't hold, leaving
        the rest of the network untouched (a split hands their share to its other
        lanes, as deleting one by hand does)."""
        for sid in [s for s in self.auto_forward if self.rule_is_hostile(state, s)]:
            for dest in [d for d in self.auto_forward[sid].shares if self.lane_is_hostile(state, d)]:
                self._drop_lane(sid, dest)

    def _unarm_lane(self, sid: int, dest: int) -> None:
        """Undo arming the composed lane: put back a rule it replaced, else take
        the lane back out of the rule it joined."""
        if self.forward_replaced is not None and self.forward_replaced[0] == sid:
            self.auto_forward[sid] = self.forward_replaced[1]
            self.forward_set_on.pop(sid, None)  # the old rule predates this turn
            self.forward_replaced = None
        else:
            self._drop_lane(sid, dest)

    def _drop_lane(self, sid: int, dest: int) -> None:
        """Take one lane out of ``sid``'s rule, handing its share to the others, and
        drop the rule with its last lane."""
        rule = self.auto_forward.get(sid)
        if rule is None:
            return
        rule.shares = model.shares_without(rule.shares, dest)
        if not rule.shares:
            self.clear_forward(sid)

    def cancel_send(self) -> None:
        """Discard the active send, keeping just the source selected. On the Forward
        tab that is the one lane being edited; the system's other lanes stay."""
        if self.forward_armed:
            if self.selected is not None and self.dest is not None:
                self._unarm_lane(self.selected, self.dest)
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
        self.forward_replaced = None
        self.dest = None
        self.chosen = 0
        self.popup_pos = None
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
            and not self.spectating
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
        self.route_clears = set()
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

    def frontier_systems(self, state: GameState) -> set[int]:
        """The systems we hold that border a rival: the front line proper, half of
        `threatened_systems`. Neutral neighbours don't count — neutral never
        attacks."""
        return {
            sid for sid, sys in state.systems.items()
            if sys.owner_id == self.human_id
            and any(state.systems[n].owner_id not in (self.human_id, 0) and n in self.visible
                    for n in sys.neighbors)
        }

    def threatened_systems(self, state: GameState) -> set[int]:
        """The systems we hold that something is pointed at: a rival holds a
        neighbouring system (`frontier_systems`), or rival ships are already
        inbound. Neutral neighbours don't count — neutral never attacks.

        The same shape as the AI's own `_threat`, recomputed here rather than
        imported, so the shell keeps its derived stats local (and `ai` stays out of
        render's reach). It discloses nothing fog hasn't: a neighbour of a system we
        hold is one hop away and a fleet inbound to one ends at a system we hold, so
        both are in full view at any sight range — the `visible` guards are there so
        that stays true if the tiers ever change.
        """
        threatened = self.frontier_systems(state)
        for f in state.fleets:
            if f.owner_id != self.human_id and state.systems.get(f.dest_id) is not None \
                    and state.systems[f.dest_id].owner_id == self.human_id:
                threatened.add(f.dest_id)
        return threatened

    def border_systems(self, state: GameState) -> set[int]:
        """The systems we hold next to a neutral one: where we expand from, and so
        auto-route's front when no rival is in reach yet."""
        return {
            sid for sid, sys in state.systems.items()
            if sys.owner_id == self.human_id
            and any(state.systems[n].owner_id == 0 and n in self.visible for n in sys.neighbors)
        }

    def auto_rally_picks(self, state: GameState) -> set[int]:
        """The rally points auto-route would pick. Never empty while the game is on:
        a seat bordering neither a rival nor a neutral holds the whole map or none.

        Every system on the front (`frontier_systems`) is a rally point. One behind
        it with rival ships inbound is one only if it can't keep them off by itself
        (`combat.holds_unaided`); one that can is left to the plan, which gives it a
        smart hold (`_plan_rally`) — it keeps what the attack needs and passes the
        rest on to the front, rather than soaking up everything nearer to it than
        the front is. With no front at all to pass ships on to, every threatened
        system is a rally point. With nothing threatened either, the front is the
        expansion one: every system bordering a neutral (`border_systems`).
        """
        threatened = self.threatened_systems(state)
        if not threatened:
            return self.border_systems(state)
        frontier = self.frontier_systems(state)
        weak = {sid for sid in threatened - frontier
                if not combat.holds_unaided(state, sid, self.human_id)}
        return (frontier | weak) if frontier else threatened

    def auto_rally(self, state: GameState) -> None:
        """Rally on `auto_rally_picks` at once — the front line as one gesture.

        Replaces the picks rather than adding to them: it is a "do the obvious thing"
        button, and what it means has to be the same whatever was picked before. Tap
        from there to adjust. A no-op on the (finished-game) chance there is
        nothing to pick.
        """
        picks = self.auto_rally_picks(state)
        if not picks:
            return
        self.route_sel = picks
        self.recompute_route(state)

    def route_sources(self) -> set[int]:
        """The chain-mode group members that will actually get a rule — everything
        picked bar the destination, which is the sink and can't forward to itself.

        Rally mode has no equivalent: its rules are laid on systems the player
        never picked, so what it plans is `route_plan` and nothing narrower.
        """
        return self.route_sel - {self.route_dest}

    def route_newly_smart(self) -> set[int]:
        """Planned systems whose smart hold the plan arms — not ones already smart,
        which the map and panel show already."""
        return {sid for sid, rule in self.route_plan.items()
                if rule.smart and not (sid in self.auto_forward and self.auto_forward[sid].smart)}

    def add_route_sids(self, state: GameState, sids) -> None:
        """Add those of ``sids`` that are ours to the route's group, as a box
        drawn around them does; a frontend that hit-tests its own map calls this."""
        for sid in sids:
            if sid in state.systems and state.systems[sid].owner_id == self.human_id:
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

        Cycle detection is shared, and so is `_add_hop`, so hold preservation and
        overwrite reporting are identical whichever sub-mode built the plan.
        """
        self.route_plan = {}
        self.route_replaces = set()
        self.route_unroutable = set()
        self.route_cycles = set()
        self.route_clears = set()
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

    def _add_hop(self, node: int, shares: dict[int, int], smart: bool = False) -> None:
        """Record one planned rule: the system's surplus down the lanes in
        ``shares``. The hold of any existing rule is kept — it is about the
        system's own safety, whichever way its ships go — so re-running a route over
        a conveyor that is already correct is idempotent rather than quietly
        resetting tuning. ``smart`` arms a smart hold on top (a threatened system,
        in rally); it never disarms one. An existing rule is reported replaced only
        when its lanes change."""
        old = self.auto_forward.get(node)
        if old is not None and old.shares != shares:
            self.route_replaces.add(node)
        self.route_plan[node] = (ForwardRule(shares, old.hold, old.smart or smart) if old is not None
                                 else ForwardRule(shares, smart=smart))

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
                self._add_hop(node, {nxt: 100})
                node = nxt

    def _plan_rally(self, state: GameState) -> None:
        """Rally routing: every owned system forwards toward its nearest rally point,
        splitting evenly where more than one way is just as quick.

        Nearest is `model.flow_costs` — travel turns, multi-source. A system's hops
        are the neighbours one lane nearer along a shortest route that themselves
        lead somewhere (a rally point, or a system already planned); where there are
        several, its rule splits evenly down all of them. Equally quick means the
        ships arrive just as soon whichever way they go, so there is nothing for a
        tie-break to decide — sharing them out is what keeps one rally point from
        drawing a whole region while its neighbour idles.

        Planning nearest-first is what makes "leads somewhere" known: a hop is
        always strictly nearer, so it has already been planned. Every hop of every
        path gets a rule, so the plan covers the whole field; rally points get none,
        which is what makes them sinks. Everything else we hold is unroutable — a
        pocket cut off from every rally point — rather than silently left out.

        Since every hop steps to a strictly nearer node, the plan still cannot loop,
        however many ways a system splits.

        A threatened system the plan routes through (rival ships inbound, or a
        rival next door, but not picked) gets a **smart hold**: it keeps what the
        heaviest wave needs and forwards the rest, so passing ships on never
        strips a system under attack. Auto-route leaves exactly the threatened
        systems that can hold unaided to be ruled this way (`auto_rally`).

        Rally is the **whole** plan for the empire's forwarding: every live rule it
        doesn't rewrite — on a rally point, or in a pocket no rally point reaches —
        goes in `route_clears` and is deleted on confirm. A rule left standing from
        an earlier plan is exactly the one that keeps draining a system that has
        since become the front.
        """
        if not self.route_sel:
            return
        owned = self._owned(state)
        threatened = self.threatened_systems(state)
        cost = model.flow_costs(state, owned, set(self.route_sel))
        for node in sorted(cost, key=lambda n: (cost[n], n)):
            if node in self.route_sel:
                continue
            hops = [
                nbr for nbr in sorted(state.systems[node].neighbors)
                if nbr in cost
                and cost[nbr] + (state.travel_turns(node, nbr) or 1) == cost[node]
                and (nbr in self.route_sel or nbr in self.route_plan)
            ]
            if hops:
                self._add_hop(node, model.shares_even(dict.fromkeys(hops, 0)), node in threatened)
        self.route_unroutable = owned - set(self.route_plan) - self.route_sel
        self.route_clears = {sid for sid in self.auto_forward
                             if sid not in self.route_plan and self.rule_is_live(state, sid)}

    def _detect_route_cycles(self, state: GameState) -> None:
        """Flag systems where the plan would close a loop with a *surviving* rule.

        The plan alone can't loop — each hop steps strictly closer to a sink. But a
        sink itself gets no plan rule, so if one already forwards back into the plan
        (directly, or down a chain of rules on systems the plan doesn't touch) ships
        circulate forever. Rally can't loop at all: every rule it doesn't rewrite is
        in `route_clears`, which this leaves out of the graph. Friendly arrivals
        are lossless, so nothing is destroyed; the ships simply never reach a
        front, which is worse than losing them because it looks like it is
        working. `confirm_route` breaks any loop it finds here.

        A split rule is several edges out of one system, and a loop down any of
        them traps that share, so every lane with a share counts. What is flagged is
        each system on a cycle reachable from the plan (`model.cycle_nodes`).
        """
        graph: dict[int, list[int]] = {
            src: [d for d, pct in rule.shares.items() if pct > 0]
            for src, rule in self.auto_forward.items()
            if self.rule_is_live(state, src) and src not in self.route_clears
        }
        graph.update({src: [d for d, pct in rule.shares.items() if pct > 0]
                      for src, rule in self.route_plan.items()})
        self.route_cycles = model.cycle_nodes(graph, set(self.route_plan))

    def confirm_route(self, state: GameState) -> None:
        """Write the proposal into `auto_forward` and leave the mode.

        Recomputes first so the rules committed are the ones just previewed,
        rather than resting on an argument about what else could have run in
        between.
        """
        self.recompute_route(state)
        plan = dict(self.route_plan)
        for sid in self.route_clears:   # rally rewrites every rule, not only the ones it reaches
            self.clear_forward(sid)
        # Break any loop before arming it. The closing edge is always a rule on a
        # system the plan doesn't touch (the plan itself is acyclic), so dropping
        # exactly those clears every cycle without discarding planned hops.
        for sid in self.route_cycles:
            if sid not in plan:
                self.clear_forward(sid)
        for src, rule in plan.items():
            self.auto_forward[src] = ForwardRule(dict(rule.shares), rule.hold, rule.smart)
            # A route is a finished plan, not a rule being built up from the popup:
            # the next tap from one of its systems opens on Send, and a lane added
            # there replaces the route's rule rather than splitting it.
            self.forward_set_on.pop(src, None)
        self.reset_route()

    # -- selection helpers -------------------------------------------------- #
    def source(self, state: GameState) -> int | None:
        """The selected system when it is ours to send from, else None. Any system
        can be selected, to read about it in the side panel; only ours sends."""
        if self.selected in state.systems and state.systems[self.selected].owner_id == self.human_id:
            return self.selected
        return None

    def reset_selection(self) -> None:
        """Drop all selection/composition state. Committed orders/rules survive
        (they live in `pending`/`auto_forward`), so this only closes the popup."""
        self.mode = IDLE
        self.selected = None
        self.dest = None
        self.chosen = 0
        self.sel_order = None
        self.forward_armed = False
        self.forward_replaced = None
        self.editing_existing = False
        self.popup_pos = None

    def rule_is_live(self, state: GameState, sid: int) -> bool:
        """Is the standing rule out of ``sid`` one that will actually fire — i.e.
        we still hold the source and every destination still exists? The same
        predicate decides whether a rule is drawn, picked off its lanes, expanded
        into orders at end of turn, and editable in the popup — and, in
        `prune_forward`, whether it survives the turn at all."""
        rule = self.auto_forward.get(sid)
        src = state.systems.get(sid)
        return (rule is not None and bool(rule.shares) and src is not None
                and src.owner_id == self.human_id and all(d in state.systems for d in rule.shares))

    def lane_is_hostile(self, state: GameState, dest: int) -> bool:
        """Does a lane into ``dest`` charge a system we don't hold?"""
        return state.systems[dest].owner_id != self.human_id

    def rule_is_hostile(self, state: GameState, sid: int) -> bool:
        """Is the standing rule out of ``sid`` one of the "dangerous" ones
        `_draw_forward_rules` tints — live, but with a lane pointed at a system we
        don't hold? The same predicate is what `clear_dangerous_forward` sweeps."""
        return self.rule_is_live(state, sid) and any(
            self.lane_is_hostile(state, d) for d in self.auto_forward[sid].shares)

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

    def edit_forward(self, state: GameState, sid: int, dest: int | None = None) -> None:
        """Reopen the send popup's Forward tab on a standing rule, at its lane to
        ``dest`` (its first lane when that is not one of them) — the mirror of
        `edit_order`, and idempotent for the same reason. Already open on the same
        system, it only moves to the other lane, keeping a dragged popup put.

        A *dormant* rule (`rule_is_live` false) is highlighted but not opened: the
        popup reads the source's garrison and the destination's owner unguarded, and
        pointing it at a system we don't hold would let the Send tab queue an order
        out of someone else's territory. Highlighting still gives the X key
        something to act on.
        """
        rule = self.auto_forward.get(sid)
        if rule is None:
            return
        if dest not in rule.shares:
            dest = next(iter(rule.shares), None)
        if self.mode == CHOOSING and self.forward_armed and self.selected == sid:
            if dest is not None:
                self.dest = dest
            return
        self.reset_selection()
        self.sel_order = None
        self.sel_forward = sid
        if not self.rule_is_live(state, sid):
            return
        self.selected = sid
        self.dest = dest
        self.mode = CHOOSING
        self.forward_armed = True
        self.editing_existing = True
        # seed the Send tab too, so switching to it sends all (as composing does)
        # rather than the 1 ship a `chosen` of 0 would clamp up to
        self.chosen = self.available(state, sid)

    def clear_pending(self) -> None:
        self.pending.clear()
        self.sel_order = None

    def clear_forward(self, sid: int) -> None:
        """Remove the standing auto-forward rule out of a system, if any."""
        self.auto_forward.pop(sid, None)
        self.forward_set_on.pop(sid, None)
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
