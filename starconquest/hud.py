"""The play screen's words: the side panel, the scoreboard, the send popup's
lines and the forecast, apart from how they are drawn.

Both shells read these: `render` lays them out in pygame and `serial.view`
sends them to the HTML page, so a line is worded in one place. A colour goes
as a role, never RGB: ``"text"``, ``"dim"``, ``"select"``, ``"fog"``, a
forecast's ``"sure"``/``"likely"``/``"gamble"``, or a seat id for that seat's
colour. A row is ``(text, role, kind)``: kind ``"head"`` for a heading (with a
star name in it, so it may need the small font), ``"row"`` for one line, and
``"wrap"`` for a line to reflow to the panel's width.
"""

from __future__ import annotations

from . import combat, config, fog
from .model import GameState, System, lane_key, shares_even
from .viewstate import CHOOSING, ROUTING, Ui

Role = str | int
Row = tuple[str, Role, str]
# A footer button: the `Ui` attribute its hit-rect goes in, its label, its key
# (None for a glyph), its colour (`widgets.BTN_*` by name, lower case), how long
# it survives a squeeze, and the cluster it is pinned to.
FooterButton = tuple[str, str, str | None, str, int, str]

# The legend for an empty panel, by scene and input. Ordered with the most
# droppable line last, and anything a control already explains at the point of
# use stays there instead: route mode's two sub-modes are described by the
# route panel once it is open, and the actions with a footer button (History,
# Autoplay, Fast forward, Menu, New map) name their own key on the button.
# `test_help_text_fits_the_empty_panel_whole` is what holds the length.
LEGEND_TOUCH = """Take every system to win.

Tap one of your systems, then a neighbour, to send its garrison down that lane
— or drag between the two.

The popup retunes the count, or makes it a standing rule (Forward). Done
finishes with that system.

Route plans a whole front's worth of rules at once.

Drag to pan, pinch to zoom."""

LEGEND_KEYS = """Take every system to win.

Click one of your systems, then a neighbour, to send its garrison down that
lane — or drag between the two.

The popup retunes the count; its Forward tab turns the send into a standing
rule instead, and Shift+click arms one without opening it.

Route (G) plans a whole front's worth of rules at once.

Drag to pan, wheel to zoom."""

# A landscape phone (`config.compact`) has a narrower, shorter panel: about 14
# rows of 24 characters against the touch build's 23 rows, so the two scenes
# whose legend runs longer get a cut of their own. What goes is what a control
# already says at the point of use: Done, route mode, the Exit button.
LEGEND_COMPACT_TOUCH = """Take every system to win.

Tap your system, then a neighbour, to send ships, or drag between them.

The popup sets the count; Forward makes it a standing rule.

Drag to pan, pinch to zoom."""

LEGEND_HISTORY_COMPACT_TOUCH = """Reviewing turns already played: nothing here changes the game.

Scrub or ◀/▶ to step a turn; Play runs it back. Graphs charts every seat.

Rewind to here replays from this turn."""

# A spectator has nothing to send, so none of the live legend's verbs are theirs:
# only what the screen is for, and how to look around it.
LEGEND_SPECTATING_TOUCH = """Watching from no seat: the whole board, and nobody's orders.

Each turn plays out here once every seat is in. The line at the top says who the
turn is waiting on.

Tap a system to see it. Drag to pan, pinch to zoom."""

LEGEND_SPECTATING_KEYS = """Watching from no seat: the whole board, and nobody's orders.

Each turn plays out here once every seat is in. The line at the top says who the
turn is waiting on.

Point at a system to see it. Drag to pan, wheel or −/+ to zoom."""

# History review is a different scene with different controls, so it gets its own
# pair rather than the live-play text above: none of the live legend's verbs work
# while scrubbing (there is no turn to end and no order to give —
# `input._handle_history_event` is modal and answers nothing else), and none of the controls that *do* work here are named there.
LEGEND_HISTORY_TOUCH = """Reviewing turns already played — nothing here changes the game.

Drag the scrubber, or the ◀/▶ buttons, to step a turn. Play runs it back.
Graphs charts every seat's systems, ships, income and losses. After the game,
its Coach tab lists turns worth a second look: tap one to go there.

Drag the map to pan, pinch to zoom.

Rewind to here restarts play from the turn on screen. Exit returns to the live
board."""

LEGEND_HISTORY_KEYS = """Reviewing turns already played — nothing here changes the game.

Drag the scrubber or press ←/→ to step a turn; Home/End jump to either end. P
plays it back.

Point at a system to see it as it was then; drag to pan, wheel to zoom. G
shows graphs of every seat's systems, ships, income and losses; Tab changes
graph, F makes it full screen. After the game, the Coach tab lists turns worth
a second look: click one, or Up/Down, to go there.

Rewind to here restarts play from the turn on screen. Esc returns to the live
board."""


def legend_text(history: bool, spectating: bool, touch: bool, compact: bool) -> str:
    """The legend for the scene and the input modality — the one place the
    variants are chosen between."""
    if history:
        if compact:
            return LEGEND_HISTORY_COMPACT_TOUCH
        return LEGEND_HISTORY_TOUCH if touch else LEGEND_HISTORY_KEYS
    if spectating:
        return LEGEND_SPECTATING_TOUCH if touch else LEGEND_SPECTATING_KEYS
    if compact:
        return LEGEND_COMPACT_TOUCH
    return LEGEND_TOUCH if touch else LEGEND_KEYS


def adjacent_enemy_strength(state: GameState, sid: int, owner: int) -> int:
    """Largest garrison of a non-friendly, non-neutral neighbour."""
    best = 0
    for n in state.systems[sid].neighbors:
        o = state.systems[n]
        if o.owner_id not in (owner, 0):
            best = max(best, o.ships)
    return best


def inbound_summary(state: GameState, sid: int, owner: int) -> tuple[int, int]:
    """(friendly, enemy) ships currently inbound to a system."""
    friendly = enemy = 0
    for f in state.fleets_incoming(sid):
        if f.owner_id == owner:
            friendly += f.ships
        else:
            enemy += f.ships
    return friendly, enemy


def lane_target(state: GameState, ui: Ui, focus: int):
    """Neighbour whose lane stats to show: the chosen dest, a hovered neighbour, or
    — failing both — the destination of a standing rule on the focused system.

    That last case is why a rule shows its lane at all: on the map the rule's own
    label sits on the lane it uses, so the panel is where you read that lane's
    length and travel time (a split shows its first lane). (A queued order reopened for editing takes the
    first case instead — the popup sets ``ui.dest`` to its destination.)
    """
    if ui.mode == CHOOSING and ui.dest is not None:
        return ui.dest
    if ui.selected is not None and ui.hover is not None and ui.hover != ui.selected and state.are_adjacent(ui.selected, ui.hover):
        return ui.hover
    rule = ui.auto_forward.get(focus)
    first = next(iter(rule.shares), None) if rule is not None else None
    if first is not None and first in state.systems:
        return first
    return None


def system_rows(state: GameState, ui: Ui, sys: System) -> list[Row]:
    """A system in the side panel: its name, then who holds it and what it has."""
    if sys.id not in ui.visible:
        # fogged system: we know where it is, not who holds it or how strong it is
        return [(sys.label, "dim", "head"), ("Owner: ?", "dim", "row"), ("Ships: ?", "dim", "row"),
                ("(out of sight)", "dim", "row")]
    rows: list[Row] = [(sys.label, sys.owner_id, "head"),
                       (f"Owner: {config.player_name(sys.owner_id)}", sys.owner_id, "row")]
    if sys.owner_id == ui.human_id and not ui.spectating:
        rows.append((f"Ships: {ui.available(state, sys.id)} free / {sys.ships} total", "text", "row"))
    else:
        rows.append((f"Ships: {sys.ships}", "text", "row"))
    if sys.production > 0:
        rows.append((f"Production: {sys.production} turns/ship", "dim", "row"))
        rows.append((f"  building: {sys.prod_progress}/{sys.production}", "dim", "row"))
    else:
        rows.append(("Production: none", "dim", "row"))
    rows.append((f"Threat (adj): {adjacent_enemy_strength(state, sys.id, sys.owner_id)}", "dim", "row"))
    fin, ein = inbound_summary(state, sys.id, sys.owner_id)
    if fin or ein:
        rows.append((f"Inbound: +{fin}f / {ein}e", "dim", "row"))
    rows.append((f"Neighbours: {len(sys.neighbors)}", "dim", "row"))
    return rows


def system_rows_compact(state: GameState, ui: Ui, sys: System) -> list[Row]:
    """`system_rows` in a portrait phone's few rows: the name, then who holds it
    and its ships, what it builds, and the pressure on it."""
    if sys.id not in ui.visible:
        return [(sys.label, "dim", "head"), ("Out of sight", "dim", "row")]
    who = config.player_name(sys.owner_id)
    rows: list[Row] = [(sys.label, sys.owner_id, "head")]
    if sys.owner_id == ui.human_id and not ui.spectating:
        rows.append((f"{who} · {ui.available(state, sys.id)}/{sys.ships} free", sys.owner_id, "row"))
    else:
        rows.append((f"{who} · {sys.ships} ships", sys.owner_id, "row"))
    if sys.production > 0:
        rows.append((f"1 per {sys.production}t · {sys.prod_progress}/{sys.production}", "dim", "row"))
    else:
        rows.append(("Builds nothing", "dim", "row"))
    threat = adjacent_enemy_strength(state, sys.id, sys.owner_id)
    fin, ein = inbound_summary(state, sys.id, sys.owner_id)
    tail = f" · in +{fin}/{ein}" if fin or ein else f" · {len(sys.neighbors)} lanes"
    rows.append((f"Threat {threat}{tail}", "dim", "row"))
    return rows


def lane_rows(state: GameState, ui: Ui, src: int, dest: int) -> list[Row]:
    """The lane from the focused system to ``dest``: length, travel time, target,
    and what the popup is sending down it."""
    lane = state.lanes.get(lane_key(src, dest))
    if lane is None:
        return []
    d = state.systems[dest]
    rows: list[Row] = [(f"Lane -> {d.label}", "text", "head")]
    turns = state.travel_turns(src, dest) or lane.travel_turns
    rows.append((f"{lane.length_ly} ly  ·  {turns} turns", "text", "row"))
    if config.SHIP_SPEED_GROWTH_PCT > 0:
        rows.append((f"Ship speed: {config.ship_speed(state.turn):.1f} ly/turn", "dim", "row"))
    if dest in ui.visible:
        rows.append((f"Target: {config.player_name(d.owner_id)} · {d.ships}sh", d.owner_id, "row"))
    else:
        rows.append(("Target: ? · ?sh", "dim", "row"))
    if ui.mode == CHOOSING and ui.forward_armed:
        go = ui.forward_this_turn(state, src).get(dest, 0)
        rows.append((f"Forwarding {ui.edited_share()}% · {go} go", "select", "wrap"))
    elif ui.mode == CHOOSING:
        rows.append((f"Sending: {ui.chosen}", "select", "row"))
    return rows


def score_vis(state: GameState, ui: Ui, pid: int) -> str:
    """How much the scoreboard knows of ``pid``: live | frozen | out | hidden."""
    p = state.players[pid]
    live = pid == ui.human_id or any(state.systems[s].owner_id == pid for s in ui.visible)
    if not (live or pid in ui.player_intel):
        return "hidden"  # never sighted — you don't know it exists
    if not p.alive:
        return "out"
    return "live" if live else "frozen"


def score_label(state: GameState, ui: Ui, pid: int, v: str, with_name: bool, terse: int = 0) -> str:
    """A seat's scoreboard entry at a level of detail: with or without its name,
    then shedding production, systems and the unit (``terse`` 0..3)."""
    p = state.players[pid]
    # A shared match can seat a person at any colour, so the scoreboard is the
    # one place that says outright which swatch is yours — a solo game has no
    # such ambiguity (it is always the seat you are looking at), hence `in_pbp`.
    you = " (you)" if ui.in_pbp and pid == ui.human_id else ""
    if v == "out":
        return f"{p.name} out{you}" if with_name else f"out{you}"
    systems, ships, prod = fog.player_totals(state, pid) if v == "live" else ui.player_intel[pid]
    name = f"{p.name} " if with_name else ""
    mark = "" if v == "live" else " ?"  # stale, last-known intel
    stats_ = (f"{systems}s {ships}sh {prod:.1f}/t", f"{systems}s {ships}sh", f"{ships}sh",
              f"{ships}")[terse]
    return f"{name}{stats_}{mark}{you}"


def seat_who(state: GameState, ui: Ui, pid: int) -> str:
    """Who is playing ``pid``, for a tooltip or a key: its bot (`Ui.bot_label`),
    "you", or "a person" for someone else's seat in a shared match."""
    bot = ui.bot_label(state, pid)
    if bot is not None:
        return bot
    if pid == ui.human_id:
        return "you"
    return "a person"


def score_tip(state: GameState, ui: Ui, pid: int) -> list[str]:
    """A scoreboard entry spelled out: who plays the seat, then its standings in
    words (or the last-known ones, or that it is out). Empty for a hidden seat."""
    v = score_vis(state, ui, pid)
    if v == "hidden":
        return []
    p = state.players[pid]
    lines = [f"{p.name} · {seat_who(state, ui, pid)}"]
    if v == "out":
        lines.append("Knocked out")
    else:
        systems, ships, prod = fog.player_totals(state, pid) if v == "live" else ui.player_intel[pid]
        stats_line = f"{systems} systems · {ships} ships · {prod:.1f} ships/turn"
        lines.append(stats_line if v == "live" else f"Last seen: {stats_line}")
    return lines


def forecast(state: GameState, ui: Ui) -> tuple[list[str], str] | None:
    """The send popup's forecast: our ships landing on ``ui.dest`` that turn
    (this send and any other landing with it) against the garrison they will
    find, with the odds of taking it (`combat.forecast_landing`), as wordings
    longest first for the caller to fit, and a tone: ``"sure"`` for a sure
    capture, ``"likely"`` for a likely one, ``"gamble"`` otherwise, ``"dim"``
    where there is no fight. Only what the player can see goes in: a
    destination out of full view says so instead of reading its garrison."""
    src, dest = ui.selected, ui.dest
    turns = state.travel_turns(src, dest) if src is not None and dest is not None else None
    if turns is None:
        return None
    if not ui.sees(dest):
        return [f"lands in {turns} turns · garrison unseen", f"in {turns} · unseen"], "dim"
    landing = ui.landing_at(state, dest, turns)
    fc = combat.forecast_landing(state, ui.human_id, dest, turns, landing)
    if fc.reinforce:
        there = fc.garrison + fc.landing
        return [f"lands in {turns} turns: {there} there", f"in {turns}: {there} there"], "dim"
    odds = fc.odds
    assert odds is not None
    g = fc.garrison
    if odds.sure:
        long = f"sure, ~{round(odds.survivors)} left"
        short = f"sure ~{round(odds.survivors)}"
        tone = "sure"
    else:
        # never "100%" for what a roll can still lose, nor "0%" for a chance
        pct = min(99, max(1, round(odds.capture * 100))) if odds.capture > 0 else 0
        long = f"{pct}% win · {fc.sure_at} is sure"
        short = f"{pct}% · {fc.sure_at} sure"
        tone = "likely" if odds.capture >= 0.5 else "gamble"
    return [f"{landing} vs {g} in {turns} turns: {long}",
            f"{landing} vs {g} in {turns}: {long}",
            f"{landing} vs {g}: {long}", f"{landing} vs {g}: {short}",
            f"{landing}v{g} {short}", f"{landing}v{g} {short.replace(' · ', '·')}", short], tone


def popup_effect(state: GameState, ui: Ui) -> str:
    """The popup's caption: what it does this turn, in plain words."""
    rule = ui.edited_rule()
    this_turn = ui.forward_this_turn(state, ui.selected) if rule is not None else {}
    stay = max(0, ui.available(state, ui.selected) - sum(this_turn.values()))
    if rule is not None and len(rule.shares) > 1:
        return f"{sum(this_turn.values())} go  ·  {stay} stay"
    if ui.forward_armed:
        return f"{this_turn.get(ui.dest, 0)} go  ·  {stay} stay"
    garrison = state.systems[ui.selected].ships
    return f"send {ui.chosen}  ·  {max(0, garrison - ui.chosen)} home"


def popup_discard(ui: Ui) -> str:
    """The popup's bottom button: Cancel while composing, and a deletion when it
    was opened on something already on the board."""
    rule = ui.edited_rule()
    split = rule is not None and len(rule.shares) > 1
    if ui.editing_existing:
        return ("Delete lane" if split else "Delete rule") if ui.forward_armed else "Delete order"
    return "Cancel"


def popup_presets(state: GameState, ui: Ui) -> list[tuple[str, bool]]:
    """The two preset buttons and whether each matches the current value: Half
    and All on Send, 50% and 100% on Forward."""
    if ui.forward_armed:
        share = ui.edited_share()
        return [("50%", share == 50), ("100%", share == 100)]
    cap = ui._active_cap(state)
    return [("Half", ui.chosen == max(1, cap // 2)), ("All", ui.chosen == cap)]


def even_split(ui: Ui) -> bool:
    """Whether the edited rule's lanes already share evenly (lights Even split)."""
    rule = ui.edited_rule()
    return rule is not None and rule.shares == shares_even(rule.shares)


def footer(state: GameState, ui: Ui) -> list[FooterButton]:
    """The bottom bar's buttons, in visual order within each cluster.

    Two clusters: the buttons that leave this game (Retry, Menu, New map) pinned
    to the left edge, and the buttons that hand control to the AI or a review
    mode (History, Clear, Route, Autoplay, Play, Fast forward) packed right.
    Where the strip is too narrow the least essential fold behind a "more"
    button, kept longest first: acting in this game (Clear, Route, Hint), then
    looking at or handing it over (History, Play, Autoplay), then leaving it
    (Menu last, since it reaches everything else). Fast forward outranks them
    all, being offered only when watching is all there is to do. Route mode
    gets a strip of its own.
    """
    # (ui attribute, label, fill, edge, how long it survives a squeeze, which
    # cluster it's pinned to), in visual order within its cluster — left to right.
    # play/pause is hidden under autoplay, which drives turns on its own timer and
    # would make it a no-op; history needs a recorded turn to scrub through, and
    # under ironman a decided game (`Ui.can_review`).
    specs: list[FooterButton] = []

    # Route mode replaces the strip wholesale rather than adding to it: the shared
    # zeroing loop below then takes every live-play rect out of service for free,
    # so nothing from the ordinary strip can be clicked under an open plan.
    if ui.mode == ROUTING:
        # One button rather than a Chain/Rally pair: with two, whichever is lit has
        # to be read as "you are here" and the other as "go here", and a strip of
        # buttons that all mean "do this" is the wrong place to learn that. This
        # one names the sub-mode it is *in* and switches when pressed. Teal ties it
        # to the live-play Route button it came from, and it outranks Cancel in a
        # squeeze — Cancel still has Esc and the confirm slot behind it, whereas
        # losing this makes the sub-mode unreachable on a touch build.
        mode_label, mode_key = "Mode: Rally" if ui.route_rally else "Mode: Chain", "Tab"
        specs.append(("route_mode_rect", mode_label, mode_key, "teal", 6, "right"))
        # Rally only, and only when there is a front to pick: drawn exactly when it
        # would do something, the same gate `can_fast_forward` earns its button by.
        if ui.route_rally and ui.threatened_systems(state):
            specs.append(("route_auto_rect", "Auto-route", "T", "violet", 5, "right"))
        specs.append(("route_cancel_rect", "Cancel", "Esc", "red", 4, "right"))
        specs.append(("menu_button_rect", "Menu", "M", "blue", 2, "right"))
        return specs

    # The "leaves this game" group, pinned left away from the control-cluster
    # taps, Menu first (and first in the folded list), then the two restarts
    # side by side in the win overlay's colours. Menu is where Esc goes too.
    specs.append(("menu_button_rect", "Menu", "M", "blue", 4, "left"))
    # Retry restarts this match from turn 0, the same fork as the win overlay's
    # Retry: the one way back to the start of a game that has no history to
    # rewind through (a daily). No confirm, like New map beside it. It took Quit's
    # place: Quit only opened a modal, which on the web can do no more than Menu
    # (main.leave_app), so Esc goes to the menu instead.
    if ui.can_retry:
        specs.append(("retry_live_button_rect", "Retry", "T", "amber", 3, "left"))
    # new map reseeds mid-game too (not just at game end), with no confirmation —
    # matching the N key exactly. (R is reset-view, and has no button here: the
    # on-map zoom cluster carries it.)
    # Not in a gauntlet stage: its seed is pinned, so a new map would leave it.
    if not ui.in_stage:
        specs.append(("restart_live_button_rect", "New map", "N", "green", 2, "left"))
    # Undo takes back the last turn: History's rewind to the turn before, in one
    # press, with no confirm (a daily puzzle is where it earns its place).
    if ui.can_undo(state):
        specs.append(("undo_button_rect", "Undo", "U", "amber", 9, "right"))
    if ui.can_review(state):
        specs.append(("history_button_rect", "History", "H", "violet", 7, "right"))
    # clear/cancel mirrors X (and Backspace/Delete): discards the send being
    # adjusted, or drops the highlighted order/rule; a no-op when nothing is.
    if not ui.spectating:   # a spectator has nothing to clear
        specs.append(("clear_button_rect", "Clear", "X", "blue", 12, "right"))
    # Route mode: multi-select a group of systems and forward them all to one
    # destination. Gated on the same predicate as the G key, so the button is
    # drawn exactly when the mode means something.
    if ui.can_route(state):
        specs.append(("route_button_rect", "Route", "G", "teal", 11, "right"))
    # Hint: a bot's suggested sends, as training help. Its first press in a game
    # not yet hinted asks (`main.press_hint`), since a hinted game is never
    # posted (`Ui.can_post`). While it is up it splits in two: put it away, or
    # queue what it suggests — the latter only while there is something left.
    # Glyphs only, so the pair is no wider than the button it replaces; the map
    # caption names their keys (`_draw_hint`).
    if ui.can_hint(state):
        if ui.hint_live(state):
            specs.append(("hint_button_rect", "✗", None, "active", 10, "right"))
            if ui.hint_left(state):
                specs.append(("hint_apply_rect", "✓", None, "green", 10, "right"))
        else:
            specs.append(("hint_button_rect", "Hint", "K", "gold", 10, "right"))
    # autoplay hands the human seat's decisions to the AI, or takes control back;
    # shown either way, unlike play/pause. Neither is offered in a shared match:
    # a play-by-post turn advances when the last seat submits, so there is no
    # clock here for either control to run, and a client that ran one anyway
    # would resolve a turn the others had not agreed to. Taken away rather than
    # guarded, the same treatment End Turn gets while we are waiting — and
    # `_FOOTER_RECTS` zeroes both, so neither answers a tap either.
    if not ui.in_pbp:
        specs.append(("autoplay_button_rect", "Take control" if ui.autoplay else "Autoplay", "A", "active" if ui.autoplay else "blue", 5, "right"))
        if not ui.autoplay:
            specs.append(("play_pause_rect", "Pause" if ui.playing else "Play", "P", "active" if ui.playing else "blue", 6, "right"))
    # Fast forward: only while the human is knocked out and the match plays on, so
    # the rest of it can be watched at speed rather than a turn every 350ms. Same
    # gate the F key goes through, so the button is drawn exactly when it means
    # something.
    if ui.can_fast_forward(state):
        specs.append(
            ("fast_forward_rect", "Normal speed" if ui.fast_forward else "Fast forward", "F", "active" if ui.fast_forward else "blue", 13, "right")
        )

    return specs
