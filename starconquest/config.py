"""All tunable constants: balance knobs, world/geometry scale, and palette.

Nothing else in the codebase should hardcode a magic number — pull it from here
so balancing the game is a matter of editing this one file.
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# World & travel
# --------------------------------------------------------------------------- #
WORLD_SIZE = 1000.0            # game is laid out in a WORLD_SIZE x WORLD_SIZE box
WORLD_MARGIN = 80.0            # keep nodes this far from the world edge

LY_PER_WORLD_UNIT = 0.1        # cosmetic: a lane's length in light-years
SHIP_LY_PER_TURN = 6.0         # how many light-years a fleet crosses per turn
#   travel_turns = max(1, ceil(length_ly / SHIP_LY_PER_TURN))
#   e.g. nodes ~240 units apart -> 24 ly -> 4 turns; ~60 units -> 6 ly -> 1 turn
#   lower value == finer granularity, so varied lane lengths read as distinct times

# Fastest ships ever get: the speed slider's top, and the ceiling growth climbs to.
SHIP_SPEED_MAX = 30.0
SHIP_SPEED_GROWTH_PCT = 0.0    # compounding % gained each game turn (0 == fixed speed)
#   speed(t) = min(SHIP_SPEED_MAX, SHIP_LY_PER_TURN * (1 + pct/100) ** t)
#   compounding, not additive: lane times then shorten at a steady rate instead of
#   collapsing in the opening turns, and "wait a few turns so the fleet arrives
#   sooner" never pays (that needs a trip longer than 1/ln(1+r) turns — ~50 at the
#   slider's 2% top, past any lane, at any base speed)

# --------------------------------------------------------------------------- #
# Map generation
# --------------------------------------------------------------------------- #
DEFAULT_NODES = 18
DEFAULT_PLAYERS = 3            # includes the human; neutral is separate (id 0)

SEED_MAX = 1_000_000          # rolled seeds are 0..SEED_MAX-1 (short enough to read out)

# Bounds for the setup menu's steppers (min systems is dynamic: players + 3).
MIN_PLAYERS = 2               # a game needs at least two sides
MAX_PLAYERS = 6               # == distinct entries in PLAYER_COLORS below
MAX_NODES = 40                # cap: edge build is O(n^2), keep map-gen responsive

KNN = 4                        # candidate edges per node (k nearest neighbours)
EXTRA_EDGE_FRACTION = 0.4      # add this fraction of extra short edges past the MST
MAX_EDGE_LENGTH_FRAC = 0.5     # prune non-MST candidate edges longer than this * WORLD_SIZE
LANE_NODE_CLEARANCE_FRAC = 0.045  # reject an extra edge passing closer than this * WORLD_SIZE
#   to an unrelated node's centre -> it would render as if running underneath that system
LLOYD_PASSES = 1              # relaxation passes to even out random node placement
NODE_JITTER = 0.85           # placement spread within a grid cell (0..1); higher == more length variety
RELAX_MIN_SEP_FRAC = 0.6     # relaxation only pushes apart nodes closer than this * ideal spacing

# Production is "turns per ship": lower == richer. Weighted so rich systems are rare.
PRODUCTION_WEIGHTS = {2: 1, 3: 3, 4: 4, 5: 2}

HOME_PRODUCTION = 3           # every homeworld gets this production
HOME_START_SHIPS = 12        # and this starting garrison

# Neutral garrison scales with desirability (richer -> lower production -> bigger).
GARRISON_BASE = 2
GARRISON_K = 12              # ships += round(GARRISON_K / production)
GARRISON_JITTER = 3         # ships += rng.randint(0, GARRISON_JITTER)

NEUTRAL_PRODUCES = False    # neutrals are static garrisons by default

# --------------------------------------------------------------------------- #
# Combat  (Lanchester square law + jitter)
# --------------------------------------------------------------------------- #
COMBAT_JITTER = 0.10        # +/- 10% random swing applied to each side's strength
DEFENDER_ADVANTAGE = 1.0    # multiplier on the defender's effective strength (1.0 == none)
DEFENDER_ADVANTAGE_MAX = 1.5  # slider ceiling: past here conquest stalemates (see bot-design)
COMBAT_PREVIEW_MAX = 50     # menu Combat page: ceiling on its two demo sliders
IN_LANE_BATTLES = False     # opposing fleets sharing a lane fight in transit

# --------------------------------------------------------------------------- #
# AI heuristic knobs
# --------------------------------------------------------------------------- #
AI_RESERVE_FRACTION = 0.25  # keep this fraction of a system's garrison at home
AI_RESERVE_FLOOR = 2        # ...but always keep at least this many
AI_EXPAND_MARGIN = 1.3      # need surplus >= garrison * this to attack a neutral
AI_ATTACK_MARGIN = 1.5      # need surplus >= enemy   * this to attack a player
AI_REINFORCE_MARGIN = 2     # only reinforce a neighbour this many ships more exposed than us
#   (one-directional + hysteresis: stops two frontier systems swapping ships each turn)

AI_AUX = 1.0                # generic per-seat knob, meaning defined by the strategy
#   Ignored by the built-in heuristic. 1.0 is the neutral "untuned" value, so a bot
#   can treat it as absent; `models/knower.py` reads it as its search depth. See
#   models/README.md.

# --------------------------------------------------------------------------- #
# Fog of war  (presentation only — the AI always has full information)
# --------------------------------------------------------------------------- #
# Visibility from the human viewpoint, in lane hops from an owned system.
FOG_MAX_HOPS = 8            # slider max; a range >= this means "unlimited" (fog off)
FOG_SIGHT = FOG_MAX_HOPS    # full-detail radius; 0 == only your own systems, MAX == off
FOG_SCOUT = FOG_MAX_HOPS    # outer grey-silhouette radius; MAX == whole map greyed
# Preset the Basic-menu "Fog of war" checkbox applies when switched on (off sets
# both ranges back to FOG_MAX_HOPS, i.e. full visibility).
FOG_ON_SIGHT = 1
FOG_ON_SCOUT = 3

# --------------------------------------------------------------------------- #
# Palette (RGB).  Player ids index PLAYER_COLORS; id 0 (neutral) uses NEUTRAL.
# --------------------------------------------------------------------------- #
COLOR_BG = (10, 12, 20)
COLOR_LANE = (52, 58, 78)
COLOR_LANE_HILITE = (120, 140, 190)
COLOR_TEXT = (225, 230, 240)
COLOR_TEXT_DIM = (140, 148, 165)
COLOR_TEXT_DARK = (12, 14, 22)      # for labels sitting on a light player colour
COLOR_NEUTRAL = (122, 128, 140)
COLOR_SELECT = (250, 240, 150)
COLOR_FOG = (58, 62, 82)            # fogged systems/lanes — cool grey, distinct from NEUTRAL
COLOR_ROUTE = (250, 120, 200)       # route-mode planning accent — magenta, the one hue no seat uses

# Index 0 is neutral; 1 is the human by convention; 2+ are AI opponents.
PLAYER_COLORS = [
    COLOR_NEUTRAL,       # 0 neutral
    (86, 170, 255),      # 1 human  — blue
    (240, 90, 90),       # 2 — red
    (95, 210, 130),      # 3 — green
    (240, 170, 70),      # 4 — orange
    (190, 130, 240),     # 5 — purple
    (240, 230, 110),     # 6 — yellow
]

PLAYER_NAMES = [
    "Neutral", "Human", "Crimson", "Verdant", "Amber", "Violet", "Gold",
]

# --------------------------------------------------------------------------- #
# Rendering sizes
# --------------------------------------------------------------------------- #
SCREEN_W = 1440
SCREEN_H = 960
HUD_TOP_H = 40
HUD_BOTTOM_H = 46
HUD_RIGHT_W = 240            # reserved right column for the system/lane info panel
# The end-turn button is the single most-tapped control, so it gets its own big
# zone: the full width of the right info panel, reaching above the ordinary
# bottom bar (see render._draw_side_panel, which reserves this same height so
# the queued-orders list never draws underneath it).
END_TURN_H = 96
FOOTER_BTN_H = 40            # height of the smaller bottom-bar buttons (play/pause, etc.)

# Shared layout metrics. Text-bearing boxes are sized from the *measured* label
# plus BTN_PAD_X, and text rows from the font's own line height plus ROW_GAP, so
# nothing can overlap or spill when the UI is scaled up (see config.ui_scale) —
# a fixed pixel width is only ever right at one font size.
HUD_PAD = 14                 # px: inner margin at the ends of the top/bottom bars
PANEL_PAD = 14               # px: inner margin of the right-hand info panel
BTN_PAD_X = 14               # px: padding each side of a label inside its button
BTN_GAP = 10                 # px: gap between neighbouring buttons in a row
ROW_GAP = 5                  # px: added to a font's line height for a text row pitch
# Floor on the side of a tappable control, applied only on a touch build (see
# config.touch_ui). The send popup's −/+ and preset rows are the smallest controls
# in the game; at their design size they come out around a third of the footer
# buttons' height on a phone, which is well under a comfortable fingertip.
TOUCH_MIN_TARGET = 30

NODE_MIN_RADIUS = 12         # for the poorest systems (production == max)
NODE_MAX_RADIUS = 26         # for the richest systems (production == 2)
NODE_TAP_MIN = 22            # px: minimum tap/click reach, so tiny systems stay hittable
NODE_RING_PAD = 6            # px: gap between a node's edge and its selection ring

# Star-name labels under the systems (flavour only — see starnames.py). Labels are
# laid out collision-first: one that would land on another label or on a node is
# dropped, so a crowded map thins out instead of turning to mush, and zooming in
# brings the missing names back.
SHOW_NODE_NAMES = True       # draw the star name beneath each on-screen system
NODE_LABEL_GAP = 5           # px: gap between a node's edge and its name label
NODE_LABEL_PAD = 3           # px: slack around a label when testing it for collisions

# Margin between the outermost system and the edge of the map viewport. Two
# values, because they answer different questions: the fit decides how big the
# whole map is drawn at zoom 1, while the pan clamp decides how close a system can
# be dragged to the edge once you are zoomed in — where a tight margin reads as
# claustrophobic. Both are floored at `node_clearance()`; see `map_*_padding`.
MAP_FIT_PADDING = 50         # px: breathing room around the zoom-1 fit-to-viewport view
MAP_PAN_PADDING = 110        # px: breathing room kept past the outermost system when zoomed
FLEET_SIZE = 9              # in-transit fleet triangle half-size (pixels)
ARROWHEAD_SIZE = 13         # px: length of the open chevron heading a planned move
ARROW_GAP = 6               # px: gap between the destination node's edge and that chevron
ARROW_WING = 0.6            # chevron half-width as a fraction of its length (both sizes)
RULE_CHEVRON_SIZE = 8       # px: length of one chevron in a forward rule's conveyor
RULE_CHEVRON_GAP = 16       # px: target spacing between those chevrons along the lane
RULE_FLOW_MS = 700          # ms for the selected rule's conveyor to advance one spacing
RULE_LABEL_GAP = 16         # px: gap past the sending system's edge to its "keep N" label
RULE_LABEL_MAX_FRAC = 0.35  # ...but never further than this fraction along a short lane
LANE_PICK_DIST = 10         # px: click within this of a queued order's lane selects it
DRAG_THRESHOLD = 8          # px: pointer travel past which a press becomes a drag
STEPPER_SIZE = 18           # px: side of the −/+ ship-count buttons on the active lane

# Turn playback (see `turnfilm.py`): the optional animated end of turn, off unless
# switched on (`webstore.animate_turns`). Presentation only — none of it can move a
# result and none of it is recorded. Durations are per *beat*; the events inside a
# beat are spread across it, so a turn with forty orders compresses rather than
# running long and a whole film is bounded by these numbers.
FILM_LAUNCH_MS = 220     # ms: fleets appear at their source and its garrison drops
FILM_MOVE_MS = 560       # ...and glide one turn's step, clashes firing where they meet
FILM_PRODUCE_MS = 0      # production's own dwell. 0 == applied at its place in the
                         # engine's sequence with no pause of its own, which is all
                         # this cut shows; the progress ring already draws the tick,
                         # so raising this only adds a moment on it
FILM_COMBAT_MS = 300     # ms: arrival fights, one node after another (and, at a
                         # multi-owner pile-up, subdivided again across its fold)
FILM_END_MS = 250        # ms held on the resolved board. Long enough to read the
                         # production tick, which lands last in the current ordering
FILM_FLASH_MS = 260      # ms a fight's burst stays up (a film always outlives its
                         # last cue by at least this, so the final burst isn't cut)
FILM_BURST_R = 22        # px a burst reaches past whatever it marks
FILM_BURST_W = 2         # px: its stroke
FILM_BURST_SPOKES = 6    # radial strokes in one
FILM_LOSS_GAP = 12       # px above a burst's centre for the ships it cost. Fixed
                         # rather than measured off the burst's reach, which grows
                         # with the flash — a label that drifted outward with it
                         # would read as a second moving thing
FILM_ARRIVAL_GAP = 4     # px between a landed fleet's tip and its node, so the
                         # garrison count underneath stays readable
FILM_CAPTION_GAP = 22    # px clearance between the phase caption and the map's floor

# Send popup: the little action panel that opens on the map when a destination
# is picked (commit send-all, then retune count / forward / cancel).
SEND_POPUP_W = 184          # px: panel width
SEND_POPUP_BTN_H = 22       # px: height of each button row
SEND_POPUP_GAP = 5          # px: vertical gap between rows
SEND_POPUP_PAD = 8          # px: inner padding

# Sliders (the send popup's count slider and history mode's turn scrubber — the
# same widget, drawn by render._draw_slider). The knob is inset by its radius at
# both ends of the track, so it never overhangs the box it sits in.
SLIDER_TRACK_H = 6          # px: thickness of the track
SLIDER_KNOB_R = 7           # px: radius of the knob

# Map camera (pan/zoom). Ratios, not pixels — relative to WorldView's one-time
# fit-to-viewport scale (zoom == 1.0), so they don't scale with apply_ui_scale.
ZOOM_MIN = 1.0              # can't zoom out past the original fit-all view
ZOOM_MAX = 6.0              # sane cap on zooming in
ZOOM_WHEEL_STEP = 1.15      # multiplicative zoom factor per wheel notch
ZOOM_BUTTON_STEP = 1.25     # multiplicative zoom factor per on-map +/- tap
MAP_ZOOM_BTN_SIZE = 44      # px: on-map zoom +/- / reset button side (touch-sized)

FONT_SIZE = 18
FONT_SIZE_SMALL = 14
FONT_SIZE_BIG = 30

FPS = 60

# --------------------------------------------------------------------------- #
# UI scaling (DPI / touch)
# --------------------------------------------------------------------------- #
# The pixel/point constants above are tuned for this baseline resolution. On a
# higher-resolution or touch display, `apply_ui_scale()` rescales them so text
# stays legible and hit targets stay finger-sized — without touching any call
# site, since every module reads `config.X` live at call time.
BASE_SCREEN_W = 1440
BASE_SCREEN_H = 960
TOUCH_UI_SCALE = 1.4        # extra multiplier applied on touch devices (Android)

# Browser (pygbag) framebuffer size. Must match the pygbag canvas (--width/--height
# passed to `pygbag --build` in tools/build_web.sh) so the surface fills the canvas
# exactly (no clipping); the browser then scales this whole surface to fit the
# window/phone, which enlarges touch targets for free — so the web build needs no
# separate touch boost. Rendering at this native resolution (rather than a small
# framebuffer the browser upscales) is what keeps text and edges crisp; the whole
# UI scales up to fill it via apply_ui_scale, so keep it a clean multiple of the
# baseline aspect and bump both these constants and the build flags together.
WEB_FB_W = 2560
WEB_FB_H = 1440

# Extra zoom applied to the setup menu's own letterbox fit on web (see
# menu.draw): its fixed 3:2 design canvas otherwise pillarboxes against the
# 16:9 WEB_FB frame above, wasting width and shrinking every touch target.
WEB_MENU_BOOST = 1.12

ui_scale = 1.0              # current factor; 1.0 == the baseline above

# Is this a touch build (no keyboard)? Set alongside the scale at boot from the
# same probe (`main`: Android, or a touch browser). The shell reads it to drop
# keyboard-only text — the "(Esc)" suffixes on button labels and the shortcut
# lines in the help panel — which is dead weight on a phone, and is exactly what
# pushes those labels out of their boxes at touch scale.
touch_ui = False

# Pixel/point constants that scale with the UI. Snapshotted at import so repeated
# `apply_ui_scale()` calls always scale from the baseline and never compound.
_SCALABLE = (
    "HUD_TOP_H", "HUD_BOTTOM_H", "HUD_RIGHT_W", "END_TURN_H", "FOOTER_BTN_H",
    "HUD_PAD", "PANEL_PAD", "BTN_PAD_X", "BTN_GAP", "ROW_GAP", "TOUCH_MIN_TARGET",
    "MAP_FIT_PADDING", "MAP_PAN_PADDING", "NODE_RING_PAD",
    "NODE_LABEL_GAP", "NODE_LABEL_PAD",
    "NODE_MIN_RADIUS", "NODE_MAX_RADIUS", "NODE_TAP_MIN", "FLEET_SIZE",
    "ARROWHEAD_SIZE", "ARROW_GAP", "RULE_CHEVRON_SIZE", "RULE_CHEVRON_GAP",
    "RULE_LABEL_GAP",
    "LANE_PICK_DIST", "DRAG_THRESHOLD", "STEPPER_SIZE", "MAP_ZOOM_BTN_SIZE",
    "FILM_BURST_R", "FILM_BURST_W", "FILM_ARRIVAL_GAP", "FILM_CAPTION_GAP",
    "FILM_LOSS_GAP",
    "SEND_POPUP_W", "SEND_POPUP_BTN_H", "SEND_POPUP_GAP", "SEND_POPUP_PAD",
    "SLIDER_TRACK_H", "SLIDER_KNOB_R",
    "FONT_SIZE", "FONT_SIZE_SMALL", "FONT_SIZE_BIG",
)
_BASE_VALUES = {name: globals()[name] for name in _SCALABLE}


def apply_ui_scale(factor: float, touch: bool = False) -> None:
    """Rescale every UI pixel/point constant to ``factor``× its baseline value.

    The result depends only on ``factor``, not on how many times this runs — each
    constant is recomputed from its import-time baseline. Fonts are built lazily
    from these sizes, so call this before the first frame is drawn (``main`` does,
    right after ``set_mode``). ``touch`` records the input modality in
    ``touch_ui``; it is set here because it comes from the same boot-time probe as
    the scale, and callers that don't care get the desktop default.
    """
    global ui_scale, touch_ui
    ui_scale = max(0.1, factor)
    touch_ui = touch
    for name, base in _BASE_VALUES.items():
        globals()[name] = max(1, round(base * ui_scale))


def s(px: float) -> int:
    """Scale a one-off pixel literal by the current UI scale (>= 1px).

    For call-site layout values that don't warrant their own named constant
    (e.g. the menu's row pitches), so they scale with everything else.
    """
    return max(1, round(px * ui_scale))


def ship_speed(turn: int) -> float:
    """Effective ship speed (ly/turn) on a given game turn."""
    rate = 1.0 + SHIP_SPEED_GROWTH_PCT / 100.0
    if rate <= 1.0 or SHIP_LY_PER_TURN >= SHIP_SPEED_MAX:
        return SHIP_LY_PER_TURN
    # Clamp the exponent at the turn the ceiling is reached: the result is capped
    # there anyway, and an unbounded power would overflow in a very long game (or
    # on a hand-edited rate from a token).
    full = math.ceil(math.log(SHIP_SPEED_MAX / SHIP_LY_PER_TURN, rate))
    return min(SHIP_SPEED_MAX, SHIP_LY_PER_TURN * rate ** min(max(0, turn), full))


def travel_turns_at(base_turns: int, turn: int) -> int:
    """Re-time a lane baked at ``SHIP_LY_PER_TURN`` for the speed on ``turn``.

    Scales the mapgen-time value rather than re-deriving from lane length, so a
    state built by hand (tests, fixtures) keeps the travel times it was given.
    Prefer ``travel_turns_at_length`` when the lane's real length is on hand
    (i.e. any real ``Lane``) — rescaling this already-rounded-up figure can
    overstate the true time by up to a turn, visibly so once it's shown
    alongside the lane's length and the current speed.
    """
    speed = ship_speed(turn)
    if speed <= SHIP_LY_PER_TURN:
        return max(1, base_turns)
    return max(1, math.ceil(base_turns * SHIP_LY_PER_TURN / speed))


def travel_turns_at_length(length_ly: float, turn: int) -> int:
    """Turns to cross a lane of ``length_ly`` if launched on ``turn``.

    Derives straight from the lane's real length and the current speed, so it
    matches what's shown alongside it (length, "Fleet speed") exactly — unlike
    ``travel_turns_at``, there's no previously-rounded-up figure to double-round.
    """
    return max(1, math.ceil(length_ly / ship_speed(turn)))


def node_clearance() -> int:
    """How much room the biggest system needs around its centre: its radius, plus
    the selection ring drawn outside that, plus the ring's own stroke.

    Any map margin below this can slice a circle at the edge of the viewport — the
    circle is drawn at a pixel radius that isn't part of the world bounds, so the
    fit/pan maths knows nothing about it. Floors both paddings below rather than
    trusting a constant to stay bigger than a radius that scales with the UI.
    """
    return NODE_MAX_RADIUS + NODE_RING_PAD + s(4)


def map_fit_padding() -> int:
    """Margin around the map in the zoom-1 fit-to-viewport view."""
    return max(MAP_FIT_PADDING, node_clearance())


def map_pan_padding() -> int:
    """Margin kept past the outermost system while panning a zoomed-in map — more
    generous than the fit's, since at zoom the viewport is otherwise filled edge to
    edge and a boundary system ends up pressed against the clip."""
    return max(MAP_PAN_PADDING, node_clearance())


def play_rect() -> tuple[int, int, int, int]:
    """The map viewport rectangle: full width minus the info panel, between the bars."""
    return (
        0,
        HUD_TOP_H,
        SCREEN_W - HUD_RIGHT_W,
        SCREEN_H - HUD_TOP_H - HUD_BOTTOM_H,
    )


def player_color(player_id: int) -> tuple[int, int, int]:
    """Colour for a player id, wrapping if there are more players than colours."""
    if player_id == 0:
        return COLOR_NEUTRAL
    idx = (player_id - 1) % (len(PLAYER_COLORS) - 1) + 1
    return PLAYER_COLORS[idx]


def player_name(player_id: int) -> str:
    if 0 <= player_id < len(PLAYER_NAMES):
        return PLAYER_NAMES[player_id]
    return f"Player {player_id}"


def _relative_luminance(color: tuple[int, int, int]) -> float:
    """WCAG relative luminance of an sRGB colour, in [0, 1]."""
    def channel(c: int) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_on(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """Label colour that reads best on a filled ``bg`` — dark or light, whichever
    has the higher WCAG contrast ratio. Keeps ship counts legible on light player
    colours (yellow/green/orange) and works for any future custom palette."""
    if _contrast_ratio(COLOR_TEXT_DARK, bg) >= _contrast_ratio(COLOR_TEXT, bg):
        return COLOR_TEXT_DARK
    return COLOR_TEXT


def node_radius(production: int) -> int:
    """Map production (turns-per-ship, lower is richer) to a node radius."""
    lo, hi = 2, max(PRODUCTION_WEIGHTS)          # richest .. poorest production value
    if hi == lo:
        return NODE_MAX_RADIUS
    t = (production - lo) / (hi - lo)             # 0 at richest, 1 at poorest
    t = max(0.0, min(1.0, t))
    return round(NODE_MAX_RADIUS + t * (NODE_MIN_RADIUS - NODE_MAX_RADIUS))
