"""All tunable constants: balance knobs, world/geometry scale, and palette.

Nothing else in the codebase should hardcode a magic number — pull it from here
so balancing the game is a matter of editing this one file.
"""

from __future__ import annotations

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

# --------------------------------------------------------------------------- #
# Map generation
# --------------------------------------------------------------------------- #
DEFAULT_NODES = 18
DEFAULT_PLAYERS = 3            # includes the human; neutral is separate (id 0)

KNN = 4                        # candidate edges per node (k nearest neighbours)
EXTRA_EDGE_FRACTION = 0.4      # add this fraction of extra short edges past the MST
MAX_EDGE_LENGTH_FRAC = 0.5     # prune non-MST candidate edges longer than this * WORLD_SIZE
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

# --------------------------------------------------------------------------- #
# AI heuristic knobs
# --------------------------------------------------------------------------- #
AI_RESERVE_FRACTION = 0.25  # keep this fraction of a system's garrison at home
AI_RESERVE_FLOOR = 2        # ...but always keep at least this many
AI_EXPAND_MARGIN = 1.3      # need surplus >= garrison * this to attack a neutral
AI_ATTACK_MARGIN = 1.5      # need surplus >= enemy   * this to attack a player
AI_REINFORCE_MARGIN = 2     # only reinforce a neighbour this many ships more exposed than us
#   (one-directional + hysteresis: stops two frontier systems swapping ships each turn)

# --------------------------------------------------------------------------- #
# Palette (RGB).  Player ids index PLAYER_COLORS; id 0 (neutral) uses NEUTRAL.
# --------------------------------------------------------------------------- #
COLOR_BG = (10, 12, 20)
COLOR_LANE = (52, 58, 78)
COLOR_LANE_HILITE = (120, 140, 190)
COLOR_TEXT = (225, 230, 240)
COLOR_TEXT_DIM = (140, 148, 165)
COLOR_NEUTRAL = (122, 128, 140)
COLOR_SELECT = (250, 240, 150)

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
SCREEN_W = 1180
SCREEN_H = 780
HUD_TOP_H = 40
HUD_BOTTOM_H = 46
HUD_RIGHT_W = 240            # reserved right column for the system/lane info panel

NODE_MIN_RADIUS = 12         # for the poorest systems (production == max)
NODE_MAX_RADIUS = 26         # for the richest systems (production == 2)
FLEET_SIZE = 9              # in-transit fleet triangle half-size (pixels)

FONT_SIZE = 18
FONT_SIZE_SMALL = 14
FONT_SIZE_BIG = 30

FPS = 60


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


def node_radius(production: int) -> int:
    """Map production (turns-per-ship, lower is richer) to a node radius."""
    lo, hi = 2, max(PRODUCTION_WEIGHTS)          # richest .. poorest production value
    if hi == lo:
        return NODE_MAX_RADIUS
    t = (production - lo) / (hi - lo)             # 0 at richest, 1 at poorest
    t = max(0.0, min(1.0, t))
    return round(NODE_MAX_RADIUS + t * (NODE_MIN_RADIUS - NODE_MAX_RADIUS))
