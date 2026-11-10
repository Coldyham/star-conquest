"""The map creator apart from its drawing: the editor's state, what each press,
key and control means, every edit, and every word.

The page hit-tests in its own pixels (``webapp/js/maker.mjs``) and draws
``view``; it hands the press over here as what it landed on: a system's index,
a lane, a control's key, or a position in world units. Nothing here reads a
screen size or a pointer, so the camera is the page's, and a press that wants
the camera moved leaves the points to frame in ``Editor.fit``.

**Its working model is the recipe, not a live ``GameState``.** Positions plus
lane index pairs, so the lanes follow their nodes for free and undo is a copy
of plain data — see ``custommap.CustomMap`` for the full argument. The one
consequence to respect is that node identity is positional, so deletion goes
through ``CustomMap.without_node`` and nowhere else.

Star names are deliberately absent — they are stamped from ``state.rng`` when a
map is built and nothing is serialized, so the editor shows ids (``#7``). See
``custommap``'s module docstring before "fixing" that.

Pure core: no pygame.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import config, custommap, mapgen, paths
from . import settings as settings_mod
from .custommap import CustomMap, MapNode, Problem
from .geometry import WorldView, dist
from .settings import Settings

# Tool modes, and the strip that offers them.
SYSTEMS, LANES, OWNERS = "systems", "lanes", "owners"
TOOLS = ((SYSTEMS, "Systems"), (LANES, "Lanes"), (OWNERS, "Owners"))
READY_TOOLS = (SYSTEMS, LANES, OWNERS)

# The production palette: a weighted roll, then the six explicit values.
# `config.node_radius` clamps, so 1 and 2 draw the same size and 5 and 6 do too —
# the numeral in the swatch is what tells them apart, which is the same
# arrangement Advanced's home-production slider (1..8) already lives with.
PALETTE_RANDOM = "random"
PALETTE_VALUES = (1, 2, 3, 4, 5, 6)

DEFAULT_FILENAME = "map"
FILENAME_MAX_LEN = 24

STATUS_MS = 4000
STATUS_ERROR_MS = 15000

# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
MENU_LABEL = "Menu"
TOOL_HINTS = {
    LANES: ("Drag from one system to another to lay a lane —",
            "or tap one, then tap the other. Tap a lane to select it."),
}
PLACE_HINT = "Click empty space to place a system"
SHIFT_TIP = "Tip: shift-click a system to lane it from the one you placed last"
LANE_HINT = "Tap a lane to select it"
GROWTH_NOTE = "Ships speed up each turn, so this is turn 0."
ECON_HEADING = "New system rolls"
LANES_HEADING = "Auto-lanes"
SEATS_HEADING = "Auto home systems"
PLANAR_LABEL = "Planar (refuse crossings)"
RELANE_LABEL = "Re-run after each change"

CONFIRMS = {
    "auto_lanes": ("Replace every lane?",
                   ("Auto-lanes rebuilds the whole network, so any bottleneck you "
                    "drew by hand goes with it.")),
    "clear_lanes": ("Remove every lane?",
                    "The systems stay where they are; only the network goes."),
    "auto_owners": ("Replace every home system?",
                    ("Auto-placing spreads the starts around the rim, so any seat "
                     "you painted by hand goes with it.")),
    "new": ("Start from a blank canvas?", "The map you have drawn will be discarded."),
}
# What the confirm's yes button says, by confirm (anything else: "Discard").
CONFIRM_YES = {"auto_lanes": "Replace", "auto_owners": "Replace", "clear_lanes": "Remove"}


def confirm_words(which: str | None) -> tuple[str, str, str]:
    """``(title, detail, yes)`` for the confirm ``which`` (the key hints are each
    shell's: `widgets.confirm_labels` in pygame)."""
    title, detail = CONFIRMS[which or "new"]
    return title, detail, CONFIRM_YES.get(which or "", "Discard")


# The footer strip: (key, label, palette, squeeze rank, cluster). The filename
# field carries no label of its own but takes part in the same measure-and-squeeze
# pass, so a narrow window sheds it in rank order like everything else. Left to
# right: the two that replace the whole map, then the two that step through its
# history, then the file cluster and *Play now*. *Menu* is not here — it sits in
# the tool strip: drawing a map is a setup step, and going back to the seats,
# strategies, fog and seed behind it is the stage after the three tools.
# The HTML build's file pair (`files`): the map as a file the browser keeps,
# and one picked to edit. pygame's footer has neither; its Save and Open are
# its maps folder, which the player can open.
FILE_CONTROLS = (("export", "Export", "blue", 48, "right"), ("import", "Import", "blue", 47, "right"))


def footer(ed: Editor | None, files: bool = False) -> list[tuple[str, str, str, int, str]]:
    """The strip's controls; ``ed`` only colours *Play now* (None: measuring),
    ``"dead"`` until the map can be played. ``files`` adds Export and Import
    before *Play now*."""
    playable = ed is not None and ed.can_play()
    return [
        ("generate", "Generate", "amber", 35, "left"),
        ("new", "New (blank)", "amber", 40, "left"),
        ("undo", "Undo", "blue", 70, "left"),
        ("redo", "Redo", "blue", 65, "left"),
        ("filename", "", "field", 45, "right"),
        ("open", "Open", "blue", 50, "right"),
        ("save", "Save", "blue", 55, "right"),
        *(FILE_CONTROLS if files else ()),
        ("play", "Play now", "blue" if playable else "dead", 100, "right"),
    ]


@dataclass
class Editor:
    """Everything the creator scene holds. Analogous to ``viewstate.Ui``: purely
    transient interaction state, with the one durable thing — ``recipe`` — handed
    to ``Settings`` on commit. ``view`` is pygame's camera (None for a shell that
    keeps its own)."""

    recipe: CustomMap
    view: WorldView | None = None
    # A throwaway generator for the "roll me a system" values. Never the global
    # `random` module: the web build boots from a fixed interpreter image, so
    # `random`'s auto-seeding can hand out the same sequence on every page load.
    rng: random.Random = field(default_factory=settings_mod.fresh_rng)
    # What *Generate* rolls, when it is pinned. `None` — every real caller — means
    # "resolve one off the Settings the way starting a game would", so the button
    # hands back a different map each press.
    seed: int | None = None

    tool: str = SYSTEMS
    pick: object = PALETTE_RANDOM        # PALETTE_RANDOM, or an int production

    sel_node: int | None = None
    sel_lane: int | None = None
    # Lane drawing: the armed source. A tap leaves it armed so the next press
    # commits.
    lane_src: int | None = None
    # Refuse to *draw* a crossing lane. Editor-time only, and that is the whole
    # point: `custommap` reports a crossing as a warning rather than a blocker, so
    # turning this off leaves a map that still plays and still shares.
    planar: bool = True
    # Re-run *Auto* after every system added or removed, so the network tracks the
    # systems while they are laid out. A preference like `planar` rather than
    # anything the recipe carries, so `adopt` leaves it alone.
    auto_relane: bool = False

    # Owners tool: what a tap paints.
    owner_pick: int = 1
    # How many homeworlds *Auto* places. `None` means "as many as the map already
    # has", so the readout is the map's own truth until someone sets a number;
    # `adopt` puts it back. Never `settings.players` — `commit` derives that from
    # the recipe, so a number written there is overwritten on the way out.
    auto_seats: int | None = None

    drag_node: int | None = None
    drag_origin: tuple[int, int] | None = None   # world coords, for the snap-back
    drag_moved: bool = False
    drag_undone: bool = False                       # is this drag's undo snapshot taken?

    # What the live validator is complaining about *right now*, so an illegal
    # drag rings amber under the cursor rather than only failing on release.
    bad_nodes: frozenset[int] = frozenset()
    bad_lanes: frozenset[int] = frozenset()

    undo_stack: list[CustomMap] = field(default_factory=list)
    redo_stack: list[CustomMap] = field(default_factory=list)

    filename: str = ""
    confirm: str | None = None        # a `CONFIRMS` key, or None

    status: str = ""
    status_ok: bool = True
    status_ms: int = 0
    # World points a press asked the camera to frame (`focus_problem`), for the
    # shell to carry out and clear.
    fit: list | None = None

    # The recipe from before the latest press, put back if that press was a
    # pinch's first finger (it may have placed a system; `snapshot`, `unpress`).
    press_undo: tuple | None = None

    def problems(self) -> list[Problem]:
        return self.recipe.problems()

    def can_play(self) -> bool:
        return self.recipe.is_playable()

    def seat_target(self) -> int:
        """How many homeworlds *Auto* places, clamped to a playable seat count."""
        want = self.recipe.seats() if self.auto_seats is None else self.auto_seats
        return max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, want))


# --------------------------------------------------------------------------- #
# Opening / committing
# --------------------------------------------------------------------------- #
def open_editor(settings: Settings, seed: int | None = None, view: WorldView | None = None) -> Editor:
    """The editor for ``settings`` — its existing hand map, or a blank canvas.

    Blank is the default because *Create map* means create: handing someone a
    generated board to pick apart is a different task from drawing one, and the
    footer's *Generate* is one press away for anyone who wants that instead.
    ``seed`` is therefore only consulted by *Generate*; it is accepted here so a
    caller can pin the map that button rolls.
    """
    # The lane readouts and lane styling go through `config.travel_turns_at_length`,
    # so the menu's ship-speed knob has to have reached `config` or the editor
    # quotes the previous game's travel times.
    settings_mod.apply_globals(settings)
    recipe = settings.custom_map.copy() if settings.custom_map is not None else CustomMap()
    return Editor(recipe=recipe, view=view, seed=seed)


def generated(settings: Settings, seed: int | None = None) -> CustomMap:
    """A recipe from a freshly generated map, honouring the menu's own knobs.

    Goes through ``settings.apply_globals`` rather than ``mapgen.generate``
    directly, because generation reads the map knobs live off ``config`` and that
    is the single sanctioned writer into it.

    Centred on the way in: generation lays out inside the square ``_play_bounds``
    and the creator's canvas is wider than that, so an uncentred recipe would open
    hugging the left edge. A translation is the only edit — scaling it to fill the
    width would stretch every lane and hand back travel times the seed never gave.
    """
    settings_mod.apply_globals(settings)
    concrete = settings_mod.resolve_seed(settings) if seed is None else seed
    # A larger board is laid out in a larger box (`config.world_side`) that this
    # canvas cannot hold, so the creator only ever adopts a standard-sized one.
    nodes = min(settings.nodes, config.STANDARD_MAX_NODES)
    state = mapgen.generate(concrete, settings.mode, nodes, settings.players, settings.layout)
    return centred(custommap.from_state(state))


def centred(recipe: CustomMap) -> CustomMap:
    """``recipe`` shifted so its bounding box sits in the middle of the canvas."""
    if not recipe.nodes:
        return recipe
    xs = [n.x for n in recipe.nodes]
    ys = [n.y for n in recipe.nodes]
    dx = round((config.CUSTOM_WORLD_W - (max(xs) + min(xs))) / 2)
    dy = round((config.WORLD_SIZE - (max(ys) + min(ys))) / 2)
    for node in recipe.nodes:
        node.x, node.y = node.x + dx, node.y + dy
    return recipe.normalised()


def commit(ed: Editor, settings: Settings) -> None:
    """Write the drawn map onto ``settings``, keeping its three fields in sync.

    ``players`` and ``nodes`` are not decoration once a recipe is present: the AI
    tab lists seats ``2..players``, ``challenge_keys`` blanks seats past it, and
    the leaderboard's decoder requires both. ``from_dict`` reconciles them on the
    way back in, so letting them drift here would move the digest in between.

    Runs even for a map that fails the Play gate — a half-built map must survive a
    trip to the menu to change a setting. Only *starting a game* is gated
    (``play``, since ``mapgen.generate_custom`` asserts).

    An *empty* recipe is the exception, and it is not a half-built map: it carries
    nothing to preserve and is indistinguishable in intent from having no hand map
    at all. Writing it would pin the menu into "Edit map" with a setup that cannot
    start, which is what opening the creator and leaving straight away would
    otherwise do now that a blank canvas is where it opens.
    """
    apply_map(settings, ed.recipe)


def apply_map(settings: Settings, recipe: CustomMap) -> None:
    """``recipe`` as the setup's hand map, ``players`` and ``nodes`` with it
    (`commit`); an empty one clears it."""
    recipe = recipe.normalised()
    if not recipe.nodes:
        settings.custom_map = None
        return
    settings.custom_map = recipe
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, recipe.seats()))
    settings.nodes = max(1, len(recipe.nodes))


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def set_status(ed: Editor, text: str, ok: bool) -> None:
    ed.status, ed.status_ok = text, ok
    ed.status_ms = STATUS_MS if ok else STATUS_ERROR_MS


# --------------------------------------------------------------------------- #
# History — every committed mutation goes through `push_undo` first
# --------------------------------------------------------------------------- #
def push_undo(ed: Editor) -> None:
    ed.undo_stack.append(ed.recipe.copy())
    del ed.undo_stack[:-config.EDIT_UNDO_DEPTH]
    ed.redo_stack.clear()


def undo(ed: Editor) -> None:
    if not ed.undo_stack:
        set_status(ed, "Nothing to undo", False)
        return
    ed.redo_stack.append(ed.recipe.copy())
    adopt(ed, ed.undo_stack.pop())


def redo(ed: Editor) -> None:
    if not ed.redo_stack:
        set_status(ed, "Nothing to redo", False)
        return
    ed.undo_stack.append(ed.recipe.copy())
    adopt(ed, ed.redo_stack.pop())


def adopt(ed: Editor, recipe: CustomMap) -> None:
    """Swap in a whole recipe, dropping anything that pointed into the old one.

    A selection is an index, and an index into a map that has been replaced is at
    best meaningless and at worst the wrong system.
    """
    ed.recipe = recipe
    ed.sel_node = ed.sel_lane = None
    ed.auto_seats = None
    ed.lane_src = None
    ed.drag_node = ed.drag_origin = None
    ed.bad_nodes = ed.bad_lanes = frozenset()


def snapshot(ed: Editor) -> None:
    """Keep what a press on the map may change, for a pinch to put back if that
    press was its first finger (`unpress`)."""
    ed.press_undo = (ed.recipe.copy(), ed.sel_node, ed.sel_lane, len(ed.undo_stack))


def unpress(ed: Editor) -> None:
    """Put back what the last press on the map changed (`snapshot`), once."""
    if ed.press_undo is not None:
        recipe, sel_node, sel_lane, depth = ed.press_undo
        ed.recipe, ed.sel_node, ed.sel_lane = recipe, sel_node, sel_lane
        del ed.undo_stack[depth:]
    ed.press_undo = None


# --------------------------------------------------------------------------- #
# Presses on the map, by what they landed on
# --------------------------------------------------------------------------- #
def chains(ed: Editor, shift: bool) -> bool:
    """Shift chains: held down, a click both places (or links) *and* keeps
    building from what it just touched. Off while Auto-lanes' "re-run after
    every change" is on, since the network it rebuilds would overwrite the
    very lane a chained click just drew."""
    return shift and not ed.auto_relane


def press_system(ed: Editor, node: int, shift: bool = False) -> None:
    """The Systems tool's press on system ``node``: chain a lane to it from the
    selection (shift), then select it and arm a move-drag on it."""
    if chains(ed, shift):
        chain_lane(ed, node)     # reads sel_node before arm_move overwrites it
    arm_move(ed, node)


def chain_lane(ed: Editor, node: int) -> None:
    """Shift-click on an already-placed system, from the Systems tool: lane it to
    the current selection and (via the caller's `arm_move`) make it the new
    anchor, so a run of shift-clicks chains and a shift-click off to the side
    branches from wherever you're pointing.

    Reads `sel_node` before `arm_move`, which would otherwise overwrite it with
    `node` first.
    """
    if ed.sel_node is None or node == ed.sel_node:
        return
    add_lane(ed, ed.sel_node, node)
    ed.sel_lane = None


def arm_move(ed: Editor, node: int) -> None:
    """Select a system and arm a move-drag on it. No undo snapshot yet: a tap that
    only selects is not an edit, and pushing one here would clear the redo stack
    every time you looked at a system. The page keeps where the press landed for
    its drag threshold."""
    ed.sel_node = node
    ed.drag_node = node
    ed.drag_origin = (ed.recipe.nodes[node].x, ed.recipe.nodes[node].y)
    ed.drag_moved = False
    ed.drag_undone = False


def drag_to(ed: Editor, world) -> None:
    """The armed system follows the pointer, at ``world``, once the shell's drag
    threshold is past. The first real movement is the edit (one undo); the node
    follows the cursor the whole way, legal or not — rubber-banding it away
    mid-drag reads as the drag being broken. The refusal shows as amber rings on
    the offenders, and is paid on release (`release_drag`)."""
    if ed.drag_node is None:
        return
    if not ed.drag_moved:
        ed.drag_moved = True
        if not ed.drag_undone:
            push_undo(ed)
            ed.drag_undone = True
    move_node(ed, ed.drag_node, world)
    refresh_bad(ed, ed.drag_node)


def release_drag(ed: Editor) -> None:
    """The press on a system is let go: a drag that left it somewhere illegal
    snaps back. Snapped back rather than clamped to "the nearest legal point":
    with several constraints live at once that point is ill-defined, and it
    silently puts the system somewhere nobody asked for."""
    node = ed.drag_node
    ed.drag_node = None
    if node is not None and ed.drag_moved and illegal(ed, node) and ed.drag_origin is not None:
        ed.recipe.nodes[node].x, ed.recipe.nodes[node].y = ed.drag_origin
        set_status(ed, "That spot is blocked — the system snapped back.", False)
    ed.drag_origin = None
    ed.drag_moved = ed.drag_undone = False
    ed.bad_nodes = ed.bad_lanes = frozenset()


def press_owner(ed: Editor, node: int | None) -> None:
    """A press on the map in the Owners tool: paint one system (``node``), or
    miss every system.

    One meaning per tap. A press on a system paints it, and a second press on a
    system *already* that owner clears it to neutral — never "select here, paint
    there", and never a second primary meaning conditional on the system.

    A press that misses every system drops the selection on its way to arming the
    shell's box. Without it the ring — and the sidebar block that follows it — has
    nothing anywhere in this tool that can clear it again.
    """
    if node is not None:
        ed.sel_node = node
        paint(ed, [node])
        return
    ed.sel_node = None


def press_lane_tool(ed: Editor, node: int | None, lanes: list[int] = ()) -> bool:
    """A press on the map in the Lanes tool: on system ``node``, or else near
    ``lanes`` (nearest first). True when it landed on nothing at all, so a
    left-drag is free to pan there — unlike the Systems tool, where it always
    means "place".

    Systems are tested before lanes: a lane's endpoint sits inside its system's
    tap reach, and "start a lane here" has to win there. One armed source serves
    both gestures — a tap leaves it armed so the next press commits, a drag
    commits on release (`lane_release`).

    A press that misses every system also drops ``sel_node``: it is the Systems
    tool's selection, this tool has no press that sets it, and its ring would
    otherwise sit on the map for the rest of the session.
    """
    if node is not None:
        ed.sel_lane = None
        if ed.lane_src is None:
            ed.lane_src = node
        elif ed.lane_src == node:
            ed.lane_src = None              # tapping the armed system again disarms
        else:
            add_lane(ed, ed.lane_src, node)
            ed.lane_src = None
        return False
    ed.lane_src = ed.sel_node = None
    ed.sel_lane = pick_lane(ed, list(lanes))
    return ed.sel_lane is None


def pick_lane(ed: Editor, order: list[int]) -> int | None:
    """The lane a press picks from those under it, nearest first — a repeat press
    cycling through overlapping candidates rather than always grabbing the same
    one, the same shape ``play.lane_pick`` uses."""
    order = [i for i in order if 0 <= i < len(ed.recipe.lanes)]
    if not order:
        return None
    if ed.sel_lane in order:
        return order[(order.index(ed.sel_lane) + 1) % len(order)]
    return order[0]


def lane_release(ed: Editor, target: int | None) -> None:
    """A lane drag let go over system ``target`` (None: over nothing). A drag
    that landed on nothing cancels the arming rather than leaving it set — the
    gesture said where it meant to end."""
    if target is not None and ed.lane_src is not None and target != ed.lane_src:
        add_lane(ed, ed.lane_src, target)
    ed.lane_src = None


def set_tool(ed: Editor, tool: str) -> None:
    if tool not in READY_TOOLS:
        return
    if ed.tool == LANES and tool != LANES:
        # Lanes-only state: a selected lane or an armed source has no tool
        # left to click it away in once Lanes isn't the active one, so it
        # would otherwise keep glowing on the map indefinitely.
        ed.sel_lane = None
        ed.lane_src = None
    ed.tool = tool


def pick_palette(ed: Editor, settings: Settings, value: str) -> None:
    """A production swatch: arm the next placement, and retype the selection."""
    ed.pick = PALETTE_RANDOM if value == PALETTE_RANDOM else int(value)
    retype_selection(ed, settings)


# --------------------------------------------------------------------------- #
# Controls, keys and the confirm
# --------------------------------------------------------------------------- #
def control(ed: Editor, settings: Settings, hit: str) -> str | None:
    """A press on a laid-out control by its key: the toolbar, palette, sidebar
    or footer, but for the camera's (each shell's own), a slider's drag
    (`set_slider`) and the filename field's focus. Answers ``"play"`` or
    ``"menu"`` for the shell to act on, else None."""
    if hit.startswith("pal_"):
        pick_palette(ed, settings, hit[4:])
    elif hit.startswith("seat_"):
        pick_seat(ed, int(hit[5:]))
    elif hit.startswith("own_"):
        pick_seat(ed, int(hit[4:]))
    elif hit.startswith("tool_"):
        set_tool(ed, hit[5:])
    elif hit in ("prod_minus", "prod_plus"):
        step_selected(ed, "production", 1 if hit.endswith("plus") else -1)
    elif hit in ("ships_minus", "ships_plus"):
        step_selected(ed, "ships", 1 if hit.endswith("plus") else -1)
    elif hit in ("seats_minus", "seats_plus"):
        ed.auto_seats = ed.seat_target() + (1 if hit.endswith("plus") else -1)
    elif hit == "reroll":
        reroll_selected(ed, settings)
    elif hit == "delete":
        delete_selected(ed, settings)
    elif hit == "delete_lane":
        delete_lane(ed)
    elif hit == "planar":
        ed.planar = not ed.planar
    elif hit == "auto_relane":
        ed.auto_relane = not ed.auto_relane
        if ed.auto_relane:
            # Never relanes on the spot — that would be a destructive rewrite
            # with no confirm. It takes hold from the next system added or
            # removed, same as any other preference that only bites forward.
            set_status(ed, "Lanes will redraw on the next system added or removed", True)
    elif hit in ("clear_lanes", "auto_lanes", "auto_owners", "new"):
        ed.confirm = hit
    elif hit == "make_home":
        make_homeworld(ed)
    elif hit == "renumber":
        renumber_seats(ed)
    elif hit == "undo":
        undo(ed)
    elif hit == "redo":
        redo(ed)
    elif hit == "generate":
        push_undo(ed)
        adopt(ed, generated(settings, ed.seed))
        set_status(ed, "Generated a fresh map to edit", True)
    elif hit == "open":
        open_file(ed)
    elif hit == "save":
        save_file(ed)
    elif hit == "play":
        return play(ed, settings)
    elif hit == "back":
        commit(ed, settings)
        return "menu"
    return None


def play(ed: Editor, settings: Settings) -> str | None:
    """Commit and start, or refuse with the first blocker said out loud (and
    framed, `focus_problem`)."""
    blockers = ed.recipe.blockers()
    if blockers:
        focus_problem(ed, 0)
        set_status(ed, blockers[0].text, False)
        return None
    commit(ed, settings)
    return "play"


def press_key(ed: Editor, settings: Settings, name: str, ctrl: bool = False) -> str | None:
    """A key by its ``KeyboardEvent.key`` name (the filename field and the
    confirm take theirs first). Answers ``"play"``, ``"menu"``, or
    ``"reset_view"`` for the shell's camera, else None."""
    key = name.lower() if len(name) == 1 else name
    if ctrl and key == "z":
        undo(ed)
    elif ctrl and key in ("y", "r"):
        redo(ed)
    elif key == "r":
        return "reset_view"
    elif name == "Escape":
        commit(ed, settings)
        return "menu"
    elif name in ("Delete", "Backspace"):
        if ed.tool == LANES:
            delete_lane(ed)
        else:
            delete_selected(ed, settings)
    elif name == "Enter":
        return play(ed, settings)
    return None


def answer_confirm(ed: Editor, settings: Settings, yes: bool) -> None:
    """The confirm's yes or no."""
    which, ed.confirm = ed.confirm, None
    if not yes:
        return
    if which == "auto_lanes":
        auto_lanes(ed, settings)
    elif which == "auto_owners":
        auto_owners(ed, settings)
    elif which == "clear_lanes":
        clear_lanes(ed)
    elif which == "new":
        push_undo(ed)
        adopt(ed, CustomMap())
        set_status(ed, "Blank canvas", True)


def filename_text(raw: str) -> str:
    """What the filename field keeps of ``raw``: letters, digits, ``_-.``, and
    no more than ``FILENAME_MAX_LEN`` of them, so it can never name a path out of
    the maps folder."""
    return "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")[:FILENAME_MAX_LEN]


# --------------------------------------------------------------------------- #
# Sliders
# --------------------------------------------------------------------------- #
def lane_specs():
    """The two map knobs that govern lane *generation*, for the Lanes sidebar —
    `mapgen._planar_edges` reads exactly these two live off `config`. Selected by
    name, so reordering `settings.ADV_MAP` cannot hand over the wrong knobs."""
    wanted = ("extra_edge_fraction", "max_edge_length_frac")
    return tuple(spec for spec in settings_mod.ADV_MAP if spec[2] in wanted)


def econ_specs():
    """The Economy slider specs, for the Systems sidebar: every garrison on a
    hand map is concrete, rolled at the moment a system is placed, so placement
    is the only point at which these still bite."""
    return settings_mod.ADV_ECON


def slider_specs():
    """Every slider the creator can draw, whichever tool is up."""
    return tuple(econ_specs()) + tuple(lane_specs())


def slider_keys() -> frozenset[str]:
    return frozenset(spec[0] for spec in slider_specs())


def set_slider(settings: Settings, key: str, t: float) -> None:
    """Write slider ``key``'s value from the fraction ``t`` along its track."""
    spec = next((s for s in slider_specs() if s[0] == key), None)
    if spec is None:
        return
    _key, _label, attr, lo, hi, step, is_int = spec
    value = lo + max(0.0, min(1.0, t)) * (hi - lo)
    value = round(value / step) * step if step else value
    value = max(lo, min(hi, value))
    setattr(settings, attr, round(value) if is_int else round(value, 4))


def set_garrison(ed: Editor, t: float) -> None:
    """Write the Garrison slider's value onto the selected node.

    Not a `slider_specs()` entry: those write a `Settings` attribute, and this
    writes the selected node's `ships` directly, capped at
    `config.CUSTOM_GARRISON_SLIDER_MAX` rather than `CUSTOM_MAX_SHIPS` — the
    steppers are what reach past that.
    """
    node = selected(ed)
    if node is None:
        return
    node.ships = round(max(0.0, min(1.0, t)) * config.CUSTOM_GARRISON_SLIDER_MAX)


def slider_value(settings: Settings, spec) -> tuple[str, float]:
    """A slider's value as shown, and its fraction along the track."""
    _key, _label, attr, lo, hi, _step, is_int = spec
    value = getattr(settings, attr)
    shown = f"{int(value)}" if is_int else f"{value:g}"
    return shown, 0.0 if hi == lo else (value - lo) / (hi - lo)


# --------------------------------------------------------------------------- #
# Editing operations
# --------------------------------------------------------------------------- #
def place(ed: Editor, settings: Settings, world, link: int | None = None) -> int | None:
    """Place a system at ``world``, if the rules allow it there, and answer its
    index (None if refused). The shell arms its move-drag, so place-then-position
    is one gesture — and one undo: ``drag_undone`` is set, so nudging it into place
    does not push a second snapshot on top of the placement's.

    ``link`` is a system to lane the new one to — shift-click chaining, and its
    only caller. Validated against the recipe with the new node already in it,
    since the lane's far end didn't exist a moment ago; a refused link never
    refuses the placement, it only leaves the status saying which half failed.
    """
    if len(ed.recipe.nodes) >= config.STANDARD_MAX_NODES:
        set_status(ed, f"That's the limit — {config.STANDARD_MAX_NODES} systems.", False)
        return None
    wx, wy = world
    node = MapNode(x=round(wx), y=round(wy),
                   production=rolled_production(ed), ships=0).clamped()
    node.ships = rolled_garrison(ed, settings, node)

    candidate = CustomMap(nodes=ed.recipe.nodes + [node], lanes=list(ed.recipe.lanes))
    index = len(candidate.nodes) - 1
    if offenders(candidate, index, ed.planar)[0]:
        set_status(ed, "Too close to another system or a lane.", False)
        return None
    lanes = lane_candidate(ed, candidate, link, index) if link is not None else None

    push_undo(ed)
    ed.recipe.nodes.append(node)
    if lanes is not None:
        ed.recipe.lanes = lanes
        ed.sel_lane = lanes.index((min(link, index), max(link, index)))
    elif ed.auto_relane and len(ed.recipe.nodes) >= 2:
        # Off when `link` is given — `chains` never allows both, since a
        # generated network would overwrite the chained lane inside the same
        # gesture that just drew it.
        relane(ed, settings)
    arm_move(ed, index)
    ed.drag_undone = True
    if link is None or lanes is not None:
        set_status(ed, "", True)
    return index


def rolled_production(ed: Editor) -> int:
    if isinstance(ed.pick, int):        # PALETTE_RANDOM is the string, so this splits them
        return ed.pick
    return weighted_production(ed)


def weighted_production(ed: Editor) -> int:
    """The Random swatch's roll, with no palette pick able to override it — what
    ``mapgen._assign_production_and_garrisons`` gives an ordinary system."""
    values = list(config.PRODUCTION_WEIGHTS)
    weights = [config.PRODUCTION_WEIGHTS[v] for v in values]
    return ed.rng.choices(values, weights=weights, k=1)[0]


def rolled_garrison(ed: Editor, settings: Settings, node: MapNode) -> int:
    """The garrison a freshly placed system starts with.

    Concrete from the moment it is placed — nothing stored is a sentinel resolved
    later, which is what lets the seed stop shaping a custom map. The Economy
    sliders in the sidebar are what govern this roll, so they have to reach
    ``config`` first.
    """
    settings_mod.apply_globals(settings)
    if node.owner != 0:
        return config.HOME_START_SHIPS
    return config.garrison_for(node.production, ed.rng.randint(0, max(0, config.GARRISON_JITTER)))


def retype_selection(ed: Editor, settings: Settings) -> None:
    """Picking a swatch retypes the selected system as well as arming the next
    placement — otherwise choosing "3/ship" while a system is selected looks like
    it did nothing at all."""
    node = selected(ed)
    if node is None:
        return
    push_undo(ed)
    node.production = rolled_production(ed)
    if node.owner == 0:
        node.ships = rolled_garrison(ed, settings, node)


def reroll_selected(ed: Editor, settings: Settings) -> None:
    node = selected(ed)
    if node is None:
        return
    push_undo(ed)
    node.production = rolled_production(ed)
    node.ships = rolled_garrison(ed, settings, node)


def step_selected(ed: Editor, attr: str, delta: int) -> None:
    node = selected(ed)
    if node is None:
        return
    hi = {"production": config.CUSTOM_MAX_PRODUCTION,
          "ships": config.CUSTOM_MAX_SHIPS}[attr]
    value = max(0, min(hi, getattr(node, attr) + delta))
    if value == getattr(node, attr):
        return
    push_undo(ed)
    setattr(node, attr, value)


def set_owner(ed: Editor, pid: int) -> None:
    """Stamp a seat onto the selected system, if there is one and it doesn't
    already hold it. The stamping half of `pick_seat`."""
    node = selected(ed)
    if node is None or node.owner == pid:
        return
    push_undo(ed)
    node.owner = pid


def pick_seat(ed: Editor, pid: int) -> None:
    """Arm the seat a map tap paints, and stamp it on the selected system.

    The palette band and the sidebar's owner row both land here, so they cannot
    disagree about what pressing a swatch does — the production palette already
    works this way (`retype_selection`).

    Never a toggle to neutral: Neutral is its own swatch in both rows, so
    pressing the seat a system already holds arms the pick and stops there —
    `set_owner`'s own guard. That second meaning belongs to a *map* tap
    (`press_owner`), where there is nothing else to press.
    """
    ed.owner_pick = pid
    set_owner(ed, pid)


def delete_selected(ed: Editor, settings: Settings) -> None:
    if ed.sel_node is None:
        return
    push_undo(ed)
    ed.recipe = ed.recipe.without_node(ed.sel_node)
    ed.sel_node = None
    ed.drag_node = ed.drag_origin = None
    ed.bad_nodes = ed.bad_lanes = frozenset()
    # `without_node` already drops every lane the deleted system carried, so
    # there is always at least the two nodes `relane` needs once this fires.
    if ed.auto_relane and len(ed.recipe.nodes) >= 2:
        relane(ed, settings)


def paint(ed: Editor, targets: list[int]) -> None:
    """Stamp ``owner_pick`` onto ``targets``, or clear them if they already hold it.

    The toggle is decided once, from the whole group, so a box never half-paints
    and half-clears: if every system in it already belongs to the picked seat, the
    press clears them all; otherwise it paints them all.
    """
    targets = [i for i in targets if 0 <= i < len(ed.recipe.nodes)]
    if not targets:
        return
    clearing = all(ed.recipe.nodes[i].owner == ed.owner_pick for i in targets)
    owner = 0 if clearing else ed.owner_pick
    if all(ed.recipe.nodes[i].owner == owner for i in targets):
        return
    push_undo(ed)
    for i in targets:
        ed.recipe.nodes[i].owner = owner


def make_homeworld(ed: Editor) -> None:
    """Stamp the homeworld production and garrison onto the selected system."""
    node = selected(ed)
    if node is None:
        return
    if (node.production, node.ships) == (config.HOME_PRODUCTION, config.HOME_START_SHIPS):
        return
    push_undo(ed)
    node.production = config.HOME_PRODUCTION
    node.ships = config.HOME_START_SHIPS


def auto_owners(ed: Editor, settings: Settings) -> None:
    """Spread ``seat_target()`` homeworlds around the rim through
    ``mapgen.peripheral_starts`` — the generator's own placement, so a hand-drawn
    board is started from no differently than one rolled from a seed.

    Replaces rather than merges, which is why it is behind a confirm: one start
    per angular sector is the whole property, and a seat kept somewhere else
    breaks it.

    A system that loses its start is rolled back down to an ordinary one, as
    ``mapgen`` rolls every non-home system — otherwise the map's largest prize is
    a leftover garrison sitting where a homeworld used to be. Only an exact
    homeworld stamp is demoted; numbers an author set are left alone, the same
    line ``make_homeworld`` draws.
    """
    want = ed.seat_target()
    if len(ed.recipe.nodes) < want:
        set_status(ed, f"Place at least {want} systems first.", False)
        return
    # The Economy sliders govern both stamps below, and they only write `Settings`
    # — `apply_globals` is the single sanctioned writer into `config`.
    settings_mod.apply_globals(settings)
    homes = mapgen.peripheral_starts(
        {i: n.pos for i, n in enumerate(ed.recipe.nodes)}, want, ed.rng)

    push_undo(ed)
    picked = set(homes)
    stamp = (config.HOME_PRODUCTION, config.HOME_START_SHIPS)
    for i, node in enumerate(ed.recipe.nodes):
        if i in picked:
            continue
        node.owner = 0
        if (node.production, node.ships) == stamp:
            node.production = weighted_production(ed)
            node.ships = rolled_garrison(ed, settings, node)
    for pid, i in enumerate(homes, start=1):
        node = ed.recipe.nodes[i]
        node.owner = pid
        node.production, node.ships = stamp
    # The palette only offers seats up to the highest one held, so a pick left
    # above that would paint a gap straight back in — the blocker `renumber_seats`
    # exists to clear.
    ed.owner_pick = min(ed.owner_pick, want) if ed.owner_pick > 0 else 0
    set_status(ed, f"{want} home systems placed around the rim", True)


def renumber_seats(ed: Editor) -> None:
    """Compact the seats to ``1..k`` ascending, closing any gap.

    Offered as a one-press fix rather than done silently: it changes a seat's
    colour without being asked, and the colour is part of what an author intended.
    """
    held = sorted({n.owner for n in ed.recipe.nodes if n.owner > 0})
    remap = {pid: i for i, pid in enumerate(held, start=1)}
    if all(pid == new for pid, new in remap.items()):
        return
    push_undo(ed)
    for node in ed.recipe.nodes:
        if node.owner > 0:
            node.owner = remap[node.owner]
    ed.owner_pick = min(ed.owner_pick, len(held)) if ed.owner_pick > 0 else 0
    set_status(ed, "Seats renumbered", True)


def lane_candidate(ed: Editor, recipe: CustomMap, a: int, b: int
                   ) -> list[tuple[int, int]] | None:
    """``recipe.lanes`` with ``a``-``b`` added, or ``None`` with the refusal said
    out loud. Split out of `add_lane` so a placement that also links (shift-click
    chaining) can validate the new lane against a recipe that already holds the
    new system, before either half is committed — and so both take one undo
    snapshot rather than two."""
    key = (min(a, b), max(a, b))
    if key in recipe.lanes:
        set_status(ed, f"#{key[0]} and #{key[1]} are already linked.", False)
        return None

    candidate = CustomMap(nodes=recipe.nodes, lanes=sorted(recipe.lanes + [key]))
    index = candidate.lanes.index(key)
    if lane_offenders(candidate, index, ed.planar)[1]:
        set_status(ed, "That lane would run under a system."
                   if not ed.planar else
                   "That lane would run under a system, or cross another.", False)
        return None
    return candidate.lanes


def add_lane(ed: Editor, a: int, b: int) -> None:
    """Draw one lane, if the rules allow it there. The single commit path, shared
    by the tap-tap and drag gestures."""
    lanes = lane_candidate(ed, ed.recipe, a, b)
    if lanes is None:
        return
    push_undo(ed)
    ed.recipe.lanes = lanes
    ed.sel_lane = lanes.index((min(a, b), max(a, b)))
    set_status(ed, "", True)


def delete_lane(ed: Editor) -> None:
    lane = selected_lane(ed)
    if lane is None:
        return
    push_undo(ed)
    ed.recipe.lanes = [pair for pair in ed.recipe.lanes if pair != lane]
    ed.sel_lane = None


def clear_lanes(ed: Editor) -> None:
    if not ed.recipe.lanes:
        return
    push_undo(ed)
    ed.recipe.lanes = []
    ed.sel_lane = None
    set_status(ed, "Every lane removed", True)


def relane(ed: Editor, settings: Settings) -> None:
    """Rebuild the network in place with ``mapgen``'s own planar edge builder —
    ``auto_lanes`` without the confirm or the undo snapshot, so a placement or
    deletion that re-runs it (``Editor.auto_relane``) folds into that edit's own
    single undo step rather than pushing a second one.

    ``_planar_edges`` reads ``EXTRA_EDGE_FRACTION``/``MAX_EDGE_LENGTH_FRAC`` live
    off ``config``, so the knobs go in through ``settings.apply_globals`` — the
    single sanctioned writer. This module never ``setattr``s ``config`` itself.
    """
    settings_mod.apply_globals(settings)
    positions = [n.pos for n in ed.recipe.nodes]
    ed.recipe.lanes = sorted(
        (min(a, b), max(a, b)) for a, b in mapgen._planar_edges(positions)
    )
    ed.sel_lane = None


def auto_lanes(ed: Editor, settings: Settings) -> None:
    """Rebuild the whole lane network — the *Auto* button's confirmed, undoable
    one-shot version of ``relane``.

    Replaces rather than merges, which is why it is behind a confirm:
    ``_planar_edges`` always builds a full MST, so merging it into a hand-drawn
    set would silently bury a deliberate bottleneck. Its output is planar and
    graze-free by construction, so it can never produce a map the manual rules
    would then refuse.
    """
    if len(ed.recipe.nodes) < 2:
        set_status(ed, "Place at least two systems first.", False)
        return
    push_undo(ed)
    relane(ed, settings)
    set_status(ed, f"{len(ed.recipe.lanes)} lanes drawn", True)


# --------------------------------------------------------------------------- #
# Geometry & validation helpers — all of them defer to `CustomMap.problems`
# --------------------------------------------------------------------------- #
# The positional rules, the ones a move or a placement can break. A seat gap or a
# disconnected graph is equally a blocker, but neither is the drag's fault and
# neither should snap a system back. "crossing" joins them only while the Planar
# toggle is on — it is a warning in the validator, not a blocker, so with the
# toggle off a crossing must not refuse anything.
PLACEMENT_CODES = frozenset({"too_close", "graze"})


def codes(planar: bool) -> frozenset[str]:
    return PLACEMENT_CODES | {"crossing"} if planar else PLACEMENT_CODES


def offenders(recipe: CustomMap, index: int, planar: bool = True
              ) -> tuple[frozenset[int], frozenset[int]]:
    """The nodes and lanes a positional rule is complaining about *around* the
    system ``index`` — one validator, filtered, rather than a second copy of the
    geometry that could drift from what the Play gate enforces."""
    return _filter(recipe, planar, lambda problem: index in problem.nodes)


def lane_offenders(recipe: CustomMap, lane: int, planar: bool
                   ) -> tuple[frozenset[int], frozenset[int]]:
    """The same, for a lane rather than a system — what a newly drawn lane is
    tested against."""
    return _filter(recipe, planar, lambda problem: lane in problem.lanes)


def _filter(recipe: CustomMap, planar: bool, touches) -> tuple[frozenset[int], frozenset[int]]:
    wanted = codes(planar)
    nodes: set[int] = set()
    lanes: set[int] = set()
    for problem in recipe.problems():
        if problem.code not in wanted or not touches(problem):
            continue
        nodes.update(problem.nodes)
        lanes.update(problem.lanes)
    return frozenset(nodes), frozenset(lanes)


def refresh_bad(ed: Editor, index: int) -> None:
    ed.bad_nodes, ed.bad_lanes = offenders(ed.recipe, index, ed.planar)


def illegal(ed: Editor, index: int) -> bool:
    return bool(offenders(ed.recipe, index, ed.planar)[0])


def move_node(ed: Editor, index: int, world) -> None:
    moved = MapNode(x=round(world[0]), y=round(world[1])).clamped()
    ed.recipe.nodes[index].x, ed.recipe.nodes[index].y = moved.x, moved.y


def selected(ed: Editor) -> MapNode | None:
    if ed.sel_node is None or not 0 <= ed.sel_node < len(ed.recipe.nodes):
        return None
    return ed.recipe.nodes[ed.sel_node]


def selected_lane(ed: Editor) -> tuple[int, int] | None:
    """The selected lane as its node pair, or None. Returns the *pair* rather than
    the index, because an index into a list that has since been edited is at best
    meaningless and at worst the wrong lane."""
    if ed.sel_lane is None or not 0 <= ed.sel_lane < len(ed.recipe.lanes):
        return None
    return ed.recipe.lanes[ed.sel_lane]


def lane_length(a: MapNode, b: MapNode) -> float:
    return round(dist(a.pos, b.pos) * config.LY_PER_WORLD_UNIT, 1)


def lane_turns(a: MapNode, b: MapNode) -> int:
    """Travel time for a lane between two recipe nodes, on turn 0.

    Through ``config.travel_turns_at_length`` — the same call ``GameState.
    travel_turns`` makes — so the number authored against is the number the built
    map gives, including the menu's ship-speed slider.
    """
    return config.travel_turns_at_length(lane_length(a, b), 0)


def seat_entries(recipe: CustomMap) -> list[int]:
    """Neutral, then the seats — one more than are currently held, floored at two.

    One list with two readers: the Owners tool's palette band and the selected
    system's owner row in the sidebar. They must agree about which seats exist, or
    one of them offers a seat the other says is a gap.

    Offering ``n + 1`` is what makes the common path gap-free by construction: you
    can always reach the next seat and never one past it. It does not *prevent* a
    gap (paint seat 3, then clear seat 2), which is why that case gets a blocker
    with a one-press fix rather than a silent renumber.
    """
    counts = recipe.owner_counts()
    held = sum(1 for pid, n in counts.items() if pid > 0 and n > 0)
    top = max(config.MIN_PLAYERS, min(held + 1, config.MAX_PLAYERS))
    return [0] + list(range(1, top + 1))


def seat_caption(recipe: CustomMap, pid: int) -> str:
    """A seat swatch's caption: its name and how many systems it holds."""
    counts = recipe.owner_counts()
    return f"{config.player_name(pid)} ({counts.get(pid, 0)})" if pid else f"Neutral ({counts.get(0, 0)})"


def seat_glyph(pid: int) -> str:
    """What a seat swatch says. Seat 1 is the human (`app.new_ui` passes
    human_id=1) — worth marking, since which colour you are is not otherwise
    visible until the game opens."""
    return "-" if pid == 0 else ("You" if pid == 1 else str(pid))


def production_entries() -> list[tuple[object, str, int]]:
    """The production palette: (value, caption, production drawn at)."""
    entries: list[tuple[object, str, int]] = [(PALETTE_RANDOM, "Random (2-5)", config.HOME_PRODUCTION)]
    entries += [(p, f"{p}/ship", p) for p in PALETTE_VALUES]
    return entries


def focus_problem(ed: Editor, index: int) -> list | None:
    """Select problem ``index``'s first system and answer the points to frame
    (also left in ``ed.fit`` for a shell that frames later)."""
    problems = ed.problems()
    if not 0 <= index < len(problems):
        return None
    targets = [i for i in problems[index].nodes if 0 <= i < len(ed.recipe.nodes)]
    if not targets:
        return None
    ed.sel_node = targets[0]
    ed.fit = [ed.recipe.nodes[i].pos for i in targets]
    return ed.fit


def problems_heading(problems: list[Problem]) -> str:
    noun = "problem" if len(problems) == 1 else "problems"
    return "Ready to play" if not problems else f"{len(problems)} {noun}"


def lane_stats(ed: Editor) -> list[tuple[str, str]] | None:
    """The selected lane's (label, value) rows: how far it runs, how long it takes."""
    lane = selected_lane(ed)
    if lane is None:
        return None
    na, nb = ed.recipe.nodes[lane[0]], ed.recipe.nodes[lane[1]]
    turns = lane_turns(na, nb)
    return [("Length", f"{lane_length(na, nb):g} ly"),
            ("Travel", f"{turns} turn" + ("s" if turns != 1 else ""))]


# --------------------------------------------------------------------------- #
# The map library
# --------------------------------------------------------------------------- #
def map_path(name: str) -> Path:
    """The file for ``name`` under the gitignored maps dir. The field's character
    filter excludes path separators, so it can never escape that directory."""
    name = name.strip() or DEFAULT_FILENAME
    if not name.endswith(".json"):
        name += ".json"
    return paths.maps_dir() / name


def save_file(ed: Editor) -> None:
    path = map_path(ed.filename)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(ed.recipe.normalised().to_dict(), fh, indent=2)
        set_status(ed, f"Saved {path.name}", True)
    except OSError:
        set_status(ed, f"Couldn't save {path.name}", False)


def open_file(ed: Editor) -> None:
    path = map_path(ed.filename)
    try:
        with open(path) as fh:
            recipe = CustomMap.from_dict(json.load(fh))
    except (OSError, ValueError):
        set_status(ed, f"Couldn't open {path.name}", False)
        return
    if recipe is None:
        set_status(ed, f"{path.name} isn't a usable map", False)
        return
    push_undo(ed)
    adopt(ed, recipe)
    set_status(ed, f"Opened {path.name}", True)


# --------------------------------------------------------------------------- #
# The view, for a shell that keeps no pixels (the HTML build's page)
# --------------------------------------------------------------------------- #
def _rgb(color) -> list[int]:
    return [int(c) for c in color]


def view(ed: Editor, settings: Settings, touch: bool = False, files: bool = False) -> dict:
    """The whole editor as plain JSON: the map (each system's position,
    production, garrison and owner, each lane's travel time), the tool strip, the palette band, the sidebar's
    blocks, the problems, the footer, the confirm and the status line (said
    once: it is cleared as it is sent). Colours of seats go as RGB, as the
    board's own do (`serial.static`)."""
    nodes = ed.recipe.nodes
    out: dict = {
        "world": [0.0, 0.0, config.CUSTOM_WORLD_W, config.WORLD_SIZE],
        "tool": ed.tool,
        "tools": [[key, label, key in READY_TOOLS] for key, label in TOOLS],
        "menu": MENU_LABEL,
        "nodes": [[n.x, n.y, n.production, n.ships, n.owner] for n in nodes],
        "lanes": [[a, b, lane_turns(nodes[a], nodes[b])] for a, b in ed.recipe.lanes],
        "sel_node": ed.sel_node if selected(ed) is not None else None,
        "sel_lane": ed.sel_lane if selected_lane(ed) is not None else None,
        "lane_src": ed.lane_src,
        "bad_nodes": sorted(ed.bad_nodes),
        "bad_lanes": sorted(ed.bad_lanes),
        "colors": {str(pid): _rgb(config.player_color(pid)) for pid in range(config.MAX_PLAYERS + 1)},
        "palette": _palette_view(ed),
        "side": _side_view(ed, settings, touch),
        "footer": [list(spec) for spec in footer(ed, files)],
        "filename": ed.filename,
        "filename_shown": ed.filename or DEFAULT_FILENAME,
        "can_play": ed.can_play(),
        "confirm": None,
        "fit": [list(p) for p in ed.fit] if ed.fit else None,
        "status": None,
    }
    problems = ed.problems()
    out["problems"] = {"heading": problems_heading(problems), "ok": not problems,
                       "rows": [[p.text, p.blocks] for p in problems]}
    if ed.confirm is not None:
        title, detail, yes = confirm_words(ed.confirm)
        out["confirm"] = {"title": title, "detail": detail, "yes": yes, "no": "Cancel"}
    if ed.status:
        out["status"] = {"text": ed.status, "ok": ed.status_ok, "ms": ed.status_ms}
        ed.status, ed.status_ms = "", 0
    ed.fit = None
    return out


def _palette_view(ed: Editor) -> dict:
    if ed.tool == OWNERS:
        return {"kind": "seats", "entries": [
            {"key": f"seat_{pid}", "owner": pid, "glyph": seat_glyph(pid), "caption": seat_caption(ed.recipe, pid),
             "production": config.HOME_PRODUCTION, "on": ed.owner_pick == pid}
            for pid in seat_entries(ed.recipe)]}
    if ed.tool in TOOL_HINTS:
        return {"kind": "hint", "lines": list(TOOL_HINTS[ed.tool])}
    return {"kind": "production", "entries": [
        {"key": f"pal_{value}", "owner": 0, "glyph": "?" if value is PALETTE_RANDOM else str(value),
         "caption": caption, "production": shown, "on": ed.pick == value}
        for value, caption, shown in production_entries()]}


def _sliders(settings: Settings, heading: str, specs) -> list[dict]:
    out: list[dict] = [{"kind": "heading", "text": heading}]
    for spec in specs:
        shown, t = slider_value(settings, spec)
        out.append({"kind": "slider", "key": spec[0], "label": spec[1], "value": shown, "t": t})
    return out


def _side_view(ed: Editor, settings: Settings, touch: bool) -> list[dict]:
    """The sidebar's blocks, top to bottom, as pygame's `_draw_side_body` lays
    them out (the problems follow them, `view`'s ``problems``)."""
    blocks: list[dict] = []
    if ed.tool == LANES:
        stats = lane_stats(ed)
        if stats is None:
            blocks.append({"kind": "text", "text": LANE_HINT, "role": "dim"})
        else:
            a, b = selected_lane(ed)
            blocks.append({"kind": "title", "text": f"Lane #{a} - #{b}"})
            blocks += [{"kind": "stat", "label": label, "value": value} for label, value in stats]
            if config.SHIP_SPEED_GROWTH_PCT > 0:
                blocks.append({"kind": "text", "text": GROWTH_NOTE, "role": "warn"})
            blocks.append({"kind": "buttons", "items": [["delete_lane", "Delete lane", "red"]]})
        blocks += _sliders(settings, LANES_HEADING, lane_specs())
        blocks.append({"kind": "check", "key": "planar", "label": PLANAR_LABEL, "on": ed.planar})
        blocks.append({"kind": "check", "key": "auto_relane", "label": RELANE_LABEL, "on": ed.auto_relane})
        blocks.append({"kind": "buttons", "items": [["auto_lanes", "Auto", "violet"],
                                                    ["clear_lanes", "Clear all", "amber"]]})
        return blocks
    blocks += _selection_view(ed, touch)
    if ed.tool == OWNERS:
        if selected(ed) is not None:
            blocks.append({"kind": "buttons", "items": [["make_home", "Make home system", "blue"]]})
        if any(p.code == "seat_gap" for p in ed.problems()):
            blocks.append({"kind": "buttons", "items": [["renumber", "Renumber seats", "amber"]]})
        blocks.append({"kind": "heading", "text": SEATS_HEADING})
        blocks.append({"kind": "stepper", "key": "seats", "label": "Seats", "value": str(ed.seat_target())})
        blocks.append({"kind": "buttons", "items": [["auto_owners", "Auto", "violet"]]})
        return blocks
    if ed.sel_node is None and not touch:
        blocks.append({"kind": "text", "text": SHIFT_TIP, "role": "dim"})
    blocks += _sliders(settings, ECON_HEADING, econ_specs())
    return blocks


def _selection_view(ed: Editor, touch: bool) -> list[dict]:
    node = selected(ed)
    if node is None:
        return [{"kind": "text", "text": PLACE_HINT, "role": "dim"}]
    name = "Neutral" if node.owner == 0 else config.player_name(node.owner)
    return [
        {"kind": "title", "text": f"System #{ed.sel_node}"},
        {"kind": "stepper", "key": "prod", "label": "Production", "value": f"{node.production}/ship"},
        {"kind": "stepper", "key": "ships", "label": "Garrison", "value": str(node.ships)},
        {"kind": "slider", "key": "ships_slider", "label": "", "value": "",
         "t": min(1.0, node.ships / config.CUSTOM_GARRISON_SLIDER_MAX)},
        {"kind": "owners", "name": name, "entries": [
            {"key": f"own_{pid}", "owner": pid, "glyph": "-" if pid == 0 else str(pid), "on": pid == node.owner}
            for pid in seat_entries(ed.recipe)]},
        {"kind": "buttons", "items": [["reroll", "Reroll", "blue"], ["delete", "Delete", "red"]]},
    ]
