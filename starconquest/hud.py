"""The play screen's words: the side panel, the scoreboard, the send popup's
lines and the forecast, apart from how they are drawn.

Both shells read these: `render` lays them out in pygame and `serial.view`
sends them to the HTML page, so a line is worded in one place. A colour goes
as a role, never RGB: ``"text"``, ``"dim"``, ``"select"``, ``"fog"``, ``"forward"``, ``"route"``, a
forecast's ``"sure"``/``"likely"``/``"gamble"``, or a seat id for that seat's
colour; the win overlay adds ``"teal"``, ``"beat"``, ``"tie"``, ``"miss"``,
``"par"`` and ``"failed"``, the coach ``"violet"``, and the side panel's
campaign reminder ``"campaign"``. A row is ``(text, role, kind)``: kind ``"head"`` for a heading (with a
star name in it, so it may need the small font), ``"row"`` for one line, and
``"wrap"`` for a line to reflow to the panel's width.
"""

from __future__ import annotations

import math

from . import coach, combat, config, fog, matchnames, stats
from .model import GameState, System, lane_key, shares_even
from .viewstate import CHOOSING, ROUTING, SPECTATE_ROW, Ui

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
    button, kept longest first: acting in this game (Clear, Route, Undo), then
    looking at or handing it over (History, Play, then Hint, which as training
    help folds before Play, then Autoplay), then leaving it
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
        # Rally only. Always something to pick while the game is on: a seat that
        # borders neither a rival nor a neutral holds the whole map or nothing.
        if ui.route_rally:
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
        specs.append(("history_button_rect", "History", "H", "violet", 8, "right"))
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
            specs.append(("hint_button_rect", "✗", None, "active", 6, "right"))
            if ui.hint_left(state):
                specs.append(("hint_apply_rect", "✓", None, "green", 6, "right"))
        else:
            specs.append(("hint_button_rect", "Hint", "K", "gold", 6, "right"))
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
            specs.append(("play_pause_rect", "Pause" if ui.playing else "Play", "P", "active" if ui.playing else "blue", 7, "right"))
    # Fast forward: only while the human is knocked out and the match plays on, so
    # the rest of it can be watched at speed rather than a turn every 350ms. Same
    # gate the F key goes through, so the button is drawn exactly when it means
    # something.
    if ui.can_fast_forward(state):
        specs.append(
            ("fast_forward_rect", "Normal speed" if ui.fast_forward else "Fast forward", "F", "active" if ui.fast_forward else "blue", 13, "right")
        )

    return specs


def rule_rows(state: GameState, ui: Ui, src: int, touch: bool) -> list[Row]:
    """The focused system's standing rule: its hold, then each lane with its share
    and the ships that share sends this turn. The lane the popup is on is picked
    out in the select colour."""
    rule = ui.auto_forward[src]
    editing = src == ui.sel_forward or (ui.mode == CHOOSING and ui.forward_armed and src == ui.selected)
    rows: list[Row] = [("Auto-forward", "select" if editing else ui.human_id, "head")]
    if rule.smart:
        rows.append((f"holds {ui.hold_of(state, src)} (smart)", "forward", "row"))
    elif rule.hold:
        rows.append((f"holds {rule.hold}", "forward", "row"))
    this_turn = ui.forward_this_turn(state, src)
    for dest, pct in rule.shares.items():
        # a rule can outlive its destination for a frame (the system was taken and
        # the map rebuilt); name it when it is still there, fall back to the bare id
        d = state.systems.get(dest)
        line = f"-> {d.label if d else f'System {dest}'}  {pct}% · {this_turn.get(dest, 0)}"
        lit = editing and (dest == ui.dest or ui.mode != CHOOSING)
        rows.append((line, "select" if lit else "text", "wrap"))
    if touch:
        hint = "−/+: share · Delete: remove" if editing else "tap a lane to edit"
    else:
        hint = "wheel: share · X: remove" if editing else "click a lane · X: clear"
    rows.append((hint, "dim", "wrap"))
    return rows


def hold_badge(state: GameState, ui: Ui, sid: int) -> str | None:
    """A holding system's badge under its disc: "hold N", or "smart N" for a hold
    that sizes itself (shown even at nothing, so it is seen to be armed). None
    for a system holding nothing, or not ours."""
    rule = ui.auto_forward.get(sid)
    sys = state.systems.get(sid)
    if rule is None or not (rule.hold or rule.smart) or sys is None or sys.owner_id != ui.human_id:
        return None
    return f"{'smart' if rule.smart else 'hold'} {ui.hold_of(state, sid)}"


def clear_forward_label(ui: Ui) -> str:
    """The panel's button that clears every standing rule."""
    return f"Clear all forwarding ({ui.forward_lanes()})"


def clear_dangerous_label(count: int) -> str:
    """The panel's button that clears the rules pointed at systems we don't hold."""
    return f"Clear dangerous ({count})"


def headline(state: GameState, ui: Ui) -> tuple[str, Role]:
    """The game-over veil's headline: who won, in their colour, or a draw."""
    if state.winner == 0:
        return "Mutual annihilation — draw", "text"
    return f"{winner_label(state, ui)} wins!", state.winner


def winner_label(state: GameState, ui: Ui) -> str:
    """"Verdant (Knower · Predict)" — the winning seat, and the bot that was
    driving it, as the top bar's tooltip and the graph legend name it
    (`Ui.seat_bots`). A seat left to random says so: "(Marshal, random)".

    Named only for a seat no person was playing: a human seat says who won and
    stops there, and a seat claimed part-way through (``engine.claim_seat``) is a
    human seat by then, so a game somebody took over is credited to them rather
    than to the strategy it opened under. The whole board is a bot's under
    autoplay, which is exactly when this is worth reading.

    Neutral is excluded explicitly. It is a real player (``id == 0``) carrying the
    default ``ai_strategy`` like any other, but nothing ever asks it to decide —
    and it reaches here on the one path that can name it, a mutual annihilation,
    where "Neutral (Heuristic)" would credit a bot that never played.

    A random seat is revealed here — which is the point, and costs nothing, since
    there is no turn left to play with the knowledge. A seat `Ui.seat_bots` has
    no label for falls back to ``Player.ai_strategy`` itself, capitalised rather
    than title-cased, so a drop-in keeps the shape its author gave its filename
    (``claudeBot`` stays ``ClaudeBot``).
    """
    name = config.player_name(state.winner)
    player = state.players.get(state.winner)
    if player is None or player.is_neutral or player.is_human or not player.ai_strategy:
        return name
    raw = player.ai_strategy
    bot = ui.seat_bots.get(state.winner) or f"{raw[:1].upper()}{raw[1:]}"
    if state.winner in ui.random_seats:
        bot += ", random"
    return f"{name} ({bot})"


def result_lines(state: GameState, ui: Ui) -> list[tuple[str, str, Role]]:
    """(font kind, text, role) for the score under the win message: turns first,
    ships lost as the tiebreak, then the verdict against any challenge.

    Only for the human's own result — there is nothing to boast about, or to
    compare against a challenge, in watching two bots fight. When the match came
    from a challenge link, say outright whether the target fell. Returned rather
    than drawn so the overlay can measure the block before placing it.

    A watched replay says whose result this is, because it is otherwise an overlay
    identical to our own win with the two sharing buttons missing from it — which
    reads as a broken feature rather than as the rule it is (``Ui.can_post``). It
    leads rather than trails: it qualifies the "X wins!" above it, and leaving the
    verdict last keeps it the line callers read off the end.
    """
    note: list[tuple[str, str, Role]] = ([("small", "Someone else's game, replayed", "dim")]
            if ui.watched else [])
    # A run's game says what it did to the run, win or lose, in place of the
    # stage's own verdict (`main.record_run_step`).
    run_line: list[tuple[str, str, Role]] = ([("small", ui.run_note, "teal" if ui.run_ok else "miss")]
                if ui.run_note else [])
    # A spectator sits at neutral's id, which is also a mutual annihilation's
    # winner: no result here is theirs.
    if ui.spectating or state.winner != ui.human_id:
        if ui.challenge_target is not None:
            return note + [("normal", "Challenge failed", "failed")] + run_line
        return note + run_line

    lost = state.players[ui.human_id].ships_lost
    line = f"Conquered in {state.turn} turns  ·  {lost} ships lost"
    if ui.hand_turns < state.turn:
        line += f"  ·  {ui.hand_turns} played by hand"
    lines: list[tuple[str, str, Role]] = [("normal", line, "text")]
    if ui.par_note:
        # Only ever set for a player whose result met the puzzle's par (`main.land_par`).
        lines.append(("normal", ui.par_note, "par"))
    if run_line:
        lines += run_line
    elif ui.in_stage and ui.can_post(state):
        lines.append(stage_verdict(state, ui))

    if ui.challenge_target is None:
        return note + lines
    # Same ordering the score uses: fewer turns wins, ties broken on losses, and
    # equal on both is a dead heat.
    target = ui.challenge_target
    mine = (state.turn, lost)
    who = f" {ui.challenge_by}'s" if ui.challenge_by else ""
    against = f"{target[0]} turns / {target[1]} lost"
    if mine < target:
        verdict, colour = f"Beat{who} {against}", "beat"
    elif mine == target:
        verdict, colour = f"Matched{who} {against}", "tie"
    else:
        verdict, colour = f"Short of{who} {against}", "miss"
    lines.append(("small", verdict, colour))
    return note + lines


def match_notes(ui: Ui) -> list[tuple[str, Role]]:
    """What this match is for, as ``(text, role)`` lines for the head of the
    side panel: the gauntlet stage and its target, the weekly campaign's
    reminder (`app.campaign_tick`), and a challenge link's target. Not in the
    top bar, which they would share with the scoreboard (on a portrait phone,
    by covering it)."""
    notes: list[tuple[str, Role]] = []
    if ui.stage_label:
        text = ui.stage_label + (f" · {ui.stage_target.short()}" if ui.stage_target is not None else "")
        notes.append((text, "teal"))
    if ui.campaign_label:
        notes.append((ui.campaign_label, "campaign"))
    if ui.challenge_target is not None:
        turns, lost = ui.challenge_target
        who = f"{ui.challenge_by}'s " if ui.challenge_by else ""
        notes.append((f"Beat {who}{turns}t / {lost} lost", "teal"))
    return notes


def briefing_close(touch: bool, portrait: bool) -> str:
    """The briefing card's foot: how to put it away and bring it back. On a
    touch screen the hint sits in the side panel, under the map upright."""
    if touch:
        where = "below the map" if portrait else "at the right"
        return f"Tap anywhere to begin · tap the hint {where} to read this again"
    return "Press any key to begin · B shows this again"


def briefing_notes(ui: Ui) -> tuple[str, ...]:
    """The briefing card's target, which it always shows, so the target is
    never a surprise on the win overlay."""
    return (f"Target: {ui.stage_target.text()}",) if ui.stage_target is not None else ()


def stage_verdict(state: GameState, ui: Ui) -> tuple[str, str, Role]:
    """The win overlay's stage line: cleared, or which way the target went. A
    win short of it says so, and says too when an earlier best already cleared
    the stage, since Next stage is then still offered."""
    cleared = "Stage cleared" if ui.has_next_stage else "Final stage cleared"
    if not ui.run_id:
        cleared += "".join(f"  ·  {label} unlocked: {title}" for label, title in ui.unlocked(state))
    target = ui.stage_target
    if target is None:
        return ("small", cleared, "teal")
    if ui.met_target(state):
        return ("small", f"{target.text()}: met  ·  {cleared}", "teal")
    if ui.stage_met_before:
        return ("small", f"{target.text()}: not this time  ·  your best clears it", "tie")
    return ("small", f"{target.text()}: not yet", "miss")


# How much of a name the leaderboard button shows.
POST_NAME_SHOWN = 16


def board_label(ui: Ui) -> tuple[str, str | None]:
    """The win overlay's leaderboard button and its key, by what it does now
    (``Ui.board_action``)."""
    action = ui.board_action()
    if action == "view":
        return "View on board", "L"
    if action == "posting":
        return "Posting…", None
    if action == "post":
        name = ui.post_as.strip()
        if len(name) > POST_NAME_SHOWN:
            name = name[:POST_NAME_SHOWN - 1].rstrip() + "…"
        return f"Post as {name}", "L"
    return "Post on leaderboard", "L"


def share_label(ui: Ui) -> tuple[str, str]:
    """The win overlay's share button and its key: a daily's result goes out as
    a line, not a link, since everyone has its map."""
    return ("Copy daily result" if ui.daily_day else "Challenge a friend"), "C"


def route_lines(state: GameState, ui: Ui) -> list[tuple[str, Role]]:
    """What route mode is holding, and what confirming it would do: counts rather
    than a list of hops, since the map shows every hop and the numbers are what
    isn't obvious from looking."""
    lines: list[tuple[str, Role]] = []
    if ui.route_rally:
        n = len(ui.route_sel)
        lines.append((f"{n} rally point{'' if n == 1 else 's'}", "route"))
        if not n:
            lines.append(("tap systems to gather at", "dim"))
        else:
            lines.append((f"{len(ui.route_plan)} rules", "text"))
    else:
        dest = ui.route_dest
        lines.append((f"{len(ui.route_sources())} selected", "route"))
        if dest is None:
            lines.append(("no destination yet", "dim"))
        elif dest in state.systems:
            sys = state.systems[dest]
            lines.append((f"to {sys.short}", sys.owner_id))
            lines.append((f"{len(ui.route_plan)} rules", "text"))
    if ui.route_replaces:
        lines.append((f"{len(ui.route_replaces)} replaced", "likely"))
    if ui.route_clears:
        lines.append((f"{len(ui.route_clears)} cleared", "likely"))
    if smart := ui.route_newly_smart():
        lines.append((f"{len(smart)} smart hold{'' if len(smart) == 1 else 's'}", "text"))
    if ui.route_unroutable:
        lines.append((f"{len(ui.route_unroutable)} unreachable", "gamble"))
    if ui.route_cycles:
        lines.append((f"{len(ui.route_cycles)} would loop", "gamble"))
    return lines


def route_hint(ui: Ui) -> str:
    """How route mode's current sub-mode is driven."""
    if ui.route_rally:
        return ("Tap the systems ships should gather at — tap again to drop one. "
                "Everything else you hold forwards to the nearest of them, and every "
                "other rule is cleared. Auto-route picks your front line (or, with no "
                "rival in reach, your border with neutral systems); a system under "
                "attack behind it keeps what it needs and passes the rest on.")
    return ("Drag a box to pick a group. Tap a system to aim at it — tap it again to "
            "un-aim, and to drop it from the group.")


def route_confirm(ui: Ui) -> tuple[str, bool]:
    """End Turn's place in route mode: confirm the plan, or say what it lacks."""
    if ui.route_plan:
        return f"Confirm {len(ui.route_plan)}", True
    if not ui.route_sel:
        return ("Rally" if ui.route_rally else "Route"), False
    if ui.route_rally:
        # picked, but the field is empty: rallying on the only system we hold,
        # or on a pocket with nothing behind it
        return "Nothing to route", False
    return "Pick a target", False


def odds_card(state: GameState, ui: Ui) -> tuple[str, list[tuple]] | None:
    """The forecast's breakdown (`combat.forecast_landing`): ``(title, items)``.
    An item is ``("row", label, value, total)``, ``("text", text, tone)`` (the
    verdict, in `forecast`'s tone), ``("head", text)``, ``("note", text)`` or
    ``("gap",)``. None unless the forecast row reads the target."""
    src, dest = ui.selected, ui.dest
    if src is None or dest is None or not ui.sees(dest):
        return None
    turns = state.travel_turns(src, dest)
    if turns is None:
        return None
    node = state.systems[dest]
    landing = ui.landing_at(state, dest, turns)
    fc = combat.forecast_landing(state, ui.human_id, dest, turns, landing)
    line = forecast(state, ui)
    tone = line[1] if line is not None else "dim"
    send = min(ui.chosen, landing)
    plural = "s" if turns != 1 else ""
    items: list[tuple] = [("row", "Your send", str(send), False)]
    if landing > send:
        items.append(("row", "Your other ships landing that turn", f"+{landing - send}", False))
    items += [("row", "You land with", str(landing), True), ("gap",),
              ("row", f"At {node.short} now", str(fc.held), False),
              ("row", f"Built in the {turns} turn{plural} before you land", f"+{fc.built}", False)]
    if fc.inbound:
        whose = "Your" if fc.reinforce else f"{config.player_name(node.owner_id)}'s"
        items.append(("row", f"{whose} fleets landing by then", f"+{fc.inbound}", False))
    items.append(("row", "It will hold", str(fc.garrison), True))
    if fc.reinforce:
        items.append(("row", "There once you land", str(fc.garrison + landing), True))
    items.append(("gap",))
    if fc.odds is not None:
        g, odds = fc.garrison, fc.odds
        kept = round(odds.survivors)
        if odds.sure:
            verdict = f"{landing} against {g}: every roll takes it, and you keep about {kept}."
        elif odds.capture > 0:
            pct = min(99, max(1, round(odds.capture * 100)))
            verdict = (f"{landing} against {g}: you take it on {pct}% of rolls, keeping about "
                       f"{kept} when you do. {fc.sure_at} would take it on every roll.")
        else:
            verdict = f"{landing} against {g}: no roll takes it. {fc.sure_at} would take it on every roll."
        items.append(("text", verdict, tone))
    for owner, ships in fc.rivals:
        items.append(("note", f"Not counted: {config.player_name(owner)}'s {ships} ships also land "
                              f"there by then, and fight whoever holds it."))
    items.append(("note", "Not known: what anyone else sends this turn."))
    if fc.odds is not None:
        jitter, adv = config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE
        pct = round(jitter * 100)
        items += [("gap",), ("head", "How a fight is rolled")]
        if adv != 1:
            # unrounded: the bonus is a multiplier on strength, not extra ships
            items.append(("note", f"Defender advantage ×{adv:g}: the {fc.garrison} holding it fight "
                                  f"like {round(fc.garrison * adv, 2):g}."))
        else:
            items.append(("note", "No defender advantage: the holder fights at its ship count."))
        if jitter > 0:
            items.append(("note", f"Jitter ±{pct}%: each side's strength is rolled up to {pct}% "
                                  f"up or down, so a close fight can go either way."))
        else:
            items.append(("note", "Jitter is off: every fight goes exactly by the numbers."))
        items.append(("note", "The setup menu's Combat tab plays out any fight: how many each "
                              "side loses (Lanchester's square law), and the best and worst rolls."))
    title = f"{'Reinforcing' if fc.reinforce else 'Attacking'} {node.short}"
    return title, items


def lore_card(lore, touch: bool) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    """A star info card's paragraphs, close line and notes (`starlore.Lore`)."""
    origin = lore.origin or "The IAU catalogue gives no origin for this name."
    notes = (f"Source: {lore.reference}",) if lore.reference else ()
    close = "Tap anywhere to close" if touch else "Press any key to close"
    return (origin,), close, notes


def hint_caption(state: GameState, ui: Ui, touch: bool) -> str:
    """What the hint on the map amounts to: its sends left, and those our own
    orders already make."""
    left = ui.hint_left(state)
    lanes = len({(o.source_id, o.dest_id) for o in ui.hint_orders})
    covered = lanes - len(left)
    if not lanes:
        caption = "Hint: hold this turn"
    elif not left:
        caption = "Hint: your orders already make its sends"
    else:
        caption = f"Hint: {len(left)} send{'s' if len(left) != 1 else ''}"
        if covered:
            caption += f" · {covered} already yours"
    if not touch:
        caption += " · K hides, Shift+K applies" if left else " · K hides"
    return caption


def confirm_hint(ui: Ui, advisor: str) -> tuple[str, str, str]:
    """The ask before a game's first hint: title, body and the confirm button's
    word. A hinted game is never posted (`Ui.can_post`) or clears its stage."""
    stake = "posting to the leaderboard" + (" or clearing this stage" if ui.in_stage else "")
    return ("Show a hint?",
            (f"Shows on the map the moves the {advisor} bot would make in your "
             f"position this turn, leaving out any your orders already make."
             f"\n\nUsing a hint makes this game ineligible for {stake}."),
            "Show hint")


def confirm_rewind(turn: int) -> tuple[str, str, str]:
    """The ask before a destructive mid-game rewind: title, body, and the
    confirm button's word."""
    return f"Rewind to turn {turn}?", "All turns after this one will be discarded.", "Rewind"


def scrubber_label(ui: Ui, turn: int, short: bool = False) -> str:
    """History's turn readout: the turn on screen, the last, and whether the
    board is shown as it was seen or revealed. ``short`` for a portrait
    phone's two-row bar."""
    tag = "revealed" if ui.history_reveal else "as seen"
    if short:
        return f"{turn}/{ui.history_max} · {tag}"
    return f"Turn {turn}/{ui.history_max}  ·  {tag}"


def graph_value(metric: str, v: float) -> str:
    """A graph's figure for a seat at a turn: a dash for one out of sight."""
    if math.isnan(v):
        return "–"   # a rival out of sight that turn (`stats.masked`)
    if metric == "income":
        return f"{v:.1f}"
    if metric in stats.AREA:
        return f"{v:.0f}%"
    return str(round(v))


def graph_note(ui: Ui, touch: bool) -> str:
    """The graphs' header line: that rivals are charted only while seen, else
    (with keys) how to drive the panel."""
    note = "" if ui.history_reveal else "Rivals only while in sight"
    if not note and not touch:
        note = ("Up/Down: step notes  ·  Tab: graphs" if ui.coach_open else
                "Tab: next graph  ·  1-9 or click a seat: show/hide") + "  ·  F: " + (
            "dock" if ui.graph_full else "full screen")
    return note


# A coach note's turn label, coloured by what kind of lesson it is.
COACH_ROLE = {coach.GAMBLE: "gamble", coach.SURPRISE: "likely", coach.EMPTIED: "gamble",
              coach.EVACUATED: "teal", coach.IDLE: "likely", coach.TURN: "violet"}


def portrait_hint(ui: Ui) -> str:
    """A portrait phone's panel band while nothing is in focus: a reminder,
    since the rules in full are the tutorial's to teach."""
    if ui.history:
        return "Reviewing played turns. Drag the scrubber or step a turn; tap a system to see it."
    if ui.spectating:
        return "Watching: tap a system to see it. Drag to pan, pinch to zoom."
    return "Tap one of your systems, then a neighbour, to send its ships. Drag to pan, pinch to zoom."


# A folded footer button's row in the "more" list, where it differs from the
# button: the list has room to say what a glyph or a short word means.
MORE_LABELS = {"menu_button_rect": ("Menu", "Setup menu"),
               "hint_button_rect": ("✗", "✗ Hide hint"),
               "hint_apply_rect": ("✓", "✓ Apply hint")}


# --------------------------------------------------------------------------- #
# Play-by-post
# --------------------------------------------------------------------------- #
INVITE_TITLE = "Match created — copy each seat's link"
# The one and only time these links are ever shown: `app.pbp_open` mints the
# tokens and hands them over right here, and nothing stores them again. A
# missed one has no second chance.
INVITE_SUBTITLE = "Save these somewhere — they won't be shown again"
INVITE_DONE = "Continue"


def you_are(ui: Ui) -> tuple[tuple[str, Role], str]:
    """The side panel's head in a shared match: which seat is ours (in its
    colour), or that a spectator holds none; and the match's title, or its
    pass-phrase name where it has none (whichever the lobby headlines it by)."""
    line: tuple[str, Role] = (("Spectating", "text") if ui.spectating
                              else (f"You are {config.player_name(ui.human_id)}", ui.human_id))
    return line, ui.pbp_title or matchnames.phrase(ui.pbp_match)


def invite_label(ui: Ui, seat: int) -> str:
    """An invite row's name: the seat's, ours marked, or the spectators'."""
    if seat == SPECTATE_ROW:
        return "Spectators"
    return f"{config.player_name(seat)} (you)" if seat == ui.human_id else config.player_name(seat)


def invite_copy(ui: Ui, seat: int) -> str:
    """An invite row's button, which says so once its link was copied."""
    return "Copied" if ui.pbp_invite_copied == seat else "Copy"


def seat_name(state: GameState, seat: int) -> str:
    """What to call a seat we are waiting on: its player name, which is
    already how every other readout names a side."""
    player = state.players.get(seat)
    return player.name if player is not None else f"Seat {seat}"


def waiting_lines(state: GameState, ui: Ui) -> tuple[str, str]:
    """The waiting veil's headline and detail, once our orders are in."""
    names = [seat_name(state, seat) for seat in ui.pbp_waiting]
    if not names:
        return "Resolving the turn...", ""
    if len(names) == 1:
        return "Waiting for " + names[0], "Your orders are in."
    return f"Waiting for {len(names)} players", ", ".join(names)


def spectator_line(state: GameState, ui: Ui) -> str:
    """Who the live turn is waiting on, for a spectator's screen."""
    names = [seat_name(state, seat) for seat in ui.pbp_waiting]
    return ("Waiting for " + ", ".join(names)) if names else "Resolving the turn..."


def end_turn_word(state: GameState, ui: Ui) -> str | None:
    """What stands where End Turn would, when there is no turn of ours to
    end: a spectator's, or a shared turn already sent. None otherwise."""
    if ui.spectating:
        return "Spectating"
    return "Orders sent" if ui.awaiting_others(state) else None
