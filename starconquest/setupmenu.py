"""The setup menu apart from how it is drawn: its tabs and rows, what a press
on each means, and the words it shows.

Both shells read it. The pygame menu (`menu`) hit-tests its own rects and hands
the widget key here (`press`, `apply_slider`); the HTML build's session does the
same with the key the page sends, and draws `view`, the whole menu as plain
JSON. A menu is any object with `MenuState`'s fields (`menu.MenuState` in
pygame, `Menu` below for the session); everything here mutates only that and
`Settings`, as `menu.handle_event` always has.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from . import ai, combat, config, gauntlet, mapgen, pbp, webstore
from .model import AiParams
from .settings import (ADV_COMBAT, ADV_ECON, ADV_FOG, ADV_MAP, ADV_TRAVEL, RANDOM_STRATEGY,
                       MapSketch, Settings, fog_off, fresh_rng, lane_lengths, lane_survey_key,
                       lane_turns, map_sketch, random_seed, randomise_knobs, seed_top,
                       setup_warnings, sketch_key)

TABS = (("basic", "Basic"), ("combat", "Combat"), ("advanced", "Advanced"), ("ai", "AI"))

TITLE = "STAR CONQUEST"
SUBTITLE = "configure your galaxy, then conquer it"
KEY_HINT = "Enter: start game   ·   Esc: quit"

# Slider spec: (key, label, attr, lo, hi, step, is_int). The Advanced groups live
# in `settings` so the weekly campaign worker rolls a "?" map from the same
# ranges the dice button uses. Every Advanced-tab slider, flattened, is what
# its "Randomise all" die walks; the combat knobs are on the Combat tab, and a
# die rolls only what you can see.
ADV_ALL = ADV_MAP + ADV_TRAVEL + ADV_ECON + ADV_FOG
# The five knobs the built-in heuristic reads. The sixth, `aux`, is bot-defined
# and so has no fixed label or range (`ai_specs`).
AI_PARAMS = (
    ("ai_reserve_frac", "Reserve fraction", "reserve_fraction", 0.0, 0.9, 0.05, False),
    ("ai_reserve_floor", "Reserve floor", "reserve_floor", 0, 20, 1, True),
    ("ai_expand", "Expand margin", "expand_margin", 1.0, 3.0, 0.1, False),
    ("ai_attack", "Attack margin", "attack_margin", 1.0, 3.0, 0.1, False),
    ("ai_reinforce", "Reinforce margin", "reinforce_margin", 0, 10, 1, True),
)
AUX_KEY = "ai_aux"
# The Combat tab's demo fight: the one slider group that does not touch
# `Settings`. Its values are the menu's own view state, so playing with them
# never lands in a save file or a share token, and never trips the un-challenge
# modal. `attr` matches the menu's field name.
PREVIEW = (
    ("preview_attacker", "Attacker ships", "preview_attacker", 1, config.COMBAT_PREVIEW_MAX, 1, True),
    ("preview_defender", "Defender ships", "preview_defender", 1, config.COMBAT_PREVIEW_MAX, 1, True),
)

# key -> (kind, attr, lo, hi, step, is_int); kind says which object it writes.
SLIDER_SPECS: dict[str, tuple] = {}
for _grp in (ADV_MAP, ADV_TRAVEL, ADV_ECON, ADV_COMBAT, ADV_FOG):
    for _key, _label, _attr, _lo, _hi, _step, _is_int in _grp:
        SLIDER_SPECS[_key] = ("adv", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in AI_PARAMS:
    SLIDER_SPECS[_key] = ("ai", _attr, _lo, _hi, _step, _is_int)
for _key, _label, _attr, _lo, _hi, _step, _is_int in PREVIEW:
    SLIDER_SPECS[_key] = ("preview", _attr, _lo, _hi, _step, _is_int)
# The aux knob is registered with the generic range so a press knows the key;
# whether it is shown at all, and under what label and range, is the seat's
# strategy's call (`ai_specs`, resolved live in `slider_spec`).
SLIDER_SPECS[AUX_KEY] = ("ai", "aux", *ai.AUX_RANGE_DEFAULT, False)

# Zero-value instances for spotting an edited field: the same plain-equality
# test `Settings.token_dict` uses to decide what a share link carries.
DEFAULT_SETTINGS = Settings()
DEFAULT_AI_PARAMS = AiParams()

TAB_FIELDS = {
    "basic": ("players", "nodes", "mode", "seed", "autoplay", "fog_sight", "fog_scout", "custom_map"),
    "combat": tuple(attr for _, _, attr, *_ in ADV_COMBAT) + ("in_lane_battles",),
    "advanced": tuple(attr for _, _, attr, *_ in ADV_ALL) + ("neutral_produces",),
}

SEED_MAX_LEN = len(str(seed_top()))  # a typed seed stops at the newest pool's era

# The footer's daily challenges: one button opening the puzzle and the map,
# puzzle first as on the board site, each item the key a press is handled as.
DAILY_LABEL = "Daily"
DAILY_ITEMS = (("puzzle", "Daily puzzle"), ("daily", "Daily map"))
# A phone's one button for the board's two lists.
BOARD_LABEL = "Leaderboard"
BOARD_ITEMS = (("browse_configs", "Recently played"), ("browse_matches", "Shared matches"))

# The lane-battle toggle's caveat: a lane fight is the page's fight minus the
# ground, so the advantage slider applies to neither side.
LANE_NOTE = "same jitter, but no defender advantage"

# The Combat tab's prose, hand-broken for the desktop panel. The exact number
# the code returns, not a range: `_survivors` rounds sqrt(44) = 6.63 to 7.
COMBAT_PROSE = (
    "Battles use Lanchester's square law.",
    "The winner keeps sqrt(W² − L²) ships",
    "e.g. 12 attacking 10 leaves 7 — √44, rounded",
)
MATRIX_N = 3  # jitter matrix: swings per axis, spanning ∓jitter; keep it odd

SKETCH_CAPTIONS = {"yours": "your map", "sample": "a sample map: the seed is random",
                   "hidden": "Map hidden by fog of war"}

# The menu's one status line. A failure holds far longer than a success: an
# error is the only account the player gets of something that did not happen.
STATUS_MS = 4000
STATUS_ERROR_MS = 15000

CLEARED_CHALLENGE_MSG = "Challenge cleared — this is your own setup now"
CLEARED_MAP_MSG = "Custom map cleared — maps come from the seed again"
# Shown when a quit couldn't close the app: a browser tab can rarely close itself.
CANT_CLOSE_MSG = "Close the tab or app window to exit"
# The menu's actions whose screens the HTML build has yet to reach
# (`docs/design/html-frontend.md`, "How it is staged"), and what each opens.
LATER = {"tutorial": "gauntlets and the tutorial", "daily": "the daily map", "puzzle": "the daily puzzle",
         "play_by_post": "play by post", "create_map": "the map creator"}
LATER_MSG = "Not in this build yet: {what}"


@dataclass
class Menu:
    """The setup menu's own state for a shell that keeps no pixels (the HTML
    build's session): the fields of `menu.MenuState` this module reads."""

    tab: str = "basic"
    ai_seat: int = 2
    preview_attacker: int = 12
    preview_defender: int = 10
    drag_key: str | None = None
    strategy_open: bool = False
    daily_open: bool = False
    board_open: bool = False
    strategies: list[str] = field(default_factory=lambda: strategy_options())
    seed_text: str = ""
    editing_seed: bool = False
    status: str = ""
    status_ok: bool = True
    confirm_unchallenge: bool = False
    challenge_snapshot: dict | None = None
    confirm_clear_map: bool = False
    pending_action: str | None = None
    confirm_slow: bool = False
    slow_lines: list[str] = field(default_factory=list)
    slow_action: str | None = None
    slow_ack: tuple[str, ...] | None = None
    slow_title: str = ""
    lane_key: tuple | None = None
    lane_ly: list[float] = field(default_factory=list)
    sketch_at: tuple | None = None
    sketch: MapSketch | None = None
    gauntlets: list[gauntlet.Gauntlet] = field(default_factory=gauntlet.every)


# --------------------------------------------------------------------------- #
# Reading the setup
# --------------------------------------------------------------------------- #
def default_for(target):
    """The zero-value instance matching ``target``'s type, or None for anything
    that isn't a persisted setting (the Combat demo writes the menu itself)."""
    if isinstance(target, Settings):
        return DEFAULT_SETTINGS
    if isinstance(target, AiParams):
        return DEFAULT_AI_PARAMS
    return None


def changed(target, attr: str) -> bool:
    """True if ``target.attr`` has moved from the game's default."""
    default = default_for(target)
    return default is not None and getattr(target, attr) != getattr(default, attr)


def layout_changed(settings: Settings) -> bool:
    """A layout left over from a symmetric setup is not a change to a map it
    cannot shape: the same rule that keeps it out of the setup's key."""
    return not settings.layout_inert() and changed(settings, "layout")


def ai_seats(settings: Settings) -> list[int]:
    """Seats the AI tab can tune: opponents 2..N, plus your seat 1 in autoplay."""
    start = 1 if settings.autoplay else 2
    return list(range(start, settings.players + 1))


def tab_changed(tab: str, settings: Settings) -> bool:
    """True if anything shown on ``tab`` differs from default."""
    if tab == "ai":
        seats = [s - 1 for s in ai_seats(settings)]
        if any(settings.ai_strategy[i] != "heuristic" for i in seats):
            return True
        return any(settings.ai[i] != DEFAULT_AI_PARAMS for i in seats)
    if tab == "basic" and layout_changed(settings):
        return True
    return any(changed(settings, attr) for attr in TAB_FIELDS.get(tab, ()))


def strategy_options() -> list[str]:
    """The strategy list: every registered strategy, then ``random``, last
    because it is not one of them (`settings.build_state` resolves it from the
    seed) and so the measured ladder order above it is left undisturbed."""
    return ai.available_strategies() + [RANDOM_STRATEGY]


def strategy_label(name: str, aux: float) -> str:
    """What the strategy list shows for a seat: its name, or for a name since
    folded into a family (`ai.LEGACY`) the family and the stop it plays at."""
    if name not in ai.LEGACY:
        return name
    stop = ai.aux_stop_name(name, aux)
    return f"{ai.family(name)} · {stop}" if stop else ai.family(name)


def ai_specs(seat: int, settings: Settings) -> tuple:
    """Slider specs for ``seat`` on the AI tab: the heuristic's five, plus the
    ``aux`` knob under whatever the seat's strategy calls it, if anything."""
    spec = ai.aux_spec(settings.seat_strategy(seat))
    if spec is None:
        return AI_PARAMS
    label, lo, hi, step, is_int = spec
    return AI_PARAMS + ((AUX_KEY, label, "aux", lo, hi, step, is_int),)


def visible_adv(settings: Settings):
    """The Advanced sliders actually offered for this setup: a hand map has
    already placed its systems and set every garrison."""
    if settings.custom_map is None:
        return ADV_ALL
    return ADV_TRAVEL + ADV_FOG


def slider_spec(key: str, ms, settings: Settings) -> tuple | None:
    """``SLIDER_SPECS[key]``, except the aux knob, whose range belongs to the
    strategy the edited seat is running (None if it declares no aux knob)."""
    if key == AUX_KEY:
        for k, _label, attr, lo, hi, step, is_int in ai_specs(ms.ai_seat, settings):
            if k == key:
                return ("ai", attr, lo, hi, step, is_int)
        return None
    return SLIDER_SPECS.get(key)


def fmt(value, is_int: bool) -> str:
    return str(round(value)) if is_int else f"{value:.2f}"


def slider_text(key: str, value, hi, is_int: bool, names=None, lo=0) -> str:
    """A slider's value as shown: the stop's name, "All" for a fog range at its
    max (unlimited), else the number."""
    if names:
        return names[max(0, min(len(names) - 1, round(value - lo)))]
    if key.startswith("adv_fog_") and value >= hi:
        return "All"
    return fmt(value, is_int)


def lane_readout_lines(ms, settings: Settings) -> list[str]:
    """What the setup does to travel: the lane spread in turns at the chosen
    speed, and how much bigger than a standard board the map is laid out. Empty
    until `survey` has measured *this* setup: a stale figure is worse than none."""
    if ms.lane_key != lane_survey_key(settings):
        return []
    spread = lane_turns(ms.lane_ly, settings.ship_ly_per_turn)
    if spread is None:
        return []
    lo, mid, hi = spread
    span = f"{lo} turn" + ("" if lo == 1 else "s") if lo == hi else f"{lo}-{hi} turns"
    # "~" when the seed is left to chance: then it is a sample, not your map.
    sampled = "~" if settings.seed is None and settings.custom_map is None else ""
    lines = [f"Lanes: {sampled}{span}, median {mid}"]
    if settings.custom_map is None and settings.nodes > config.STANDARD_MAX_NODES:
        scale = config.world_side(settings.nodes) / config.WORLD_SIZE
        lines.append(f"Map: {scale:.1f}x standard width")
    return lines


def survey(ms, settings: Settings, sketch: bool = False) -> bool:
    """Measure the setup's lanes again if anything that shapes them changed,
    and its map sketch too when ``sketch`` (only a phone's menu draws one).
    Never mid-drag, so a map slider costs one generation, on release. True if
    anything was measured."""
    if ms.drag_key is not None:
        return False
    did = False
    key = lane_survey_key(settings)
    if key != ms.lane_key:
        ms.lane_key, ms.lane_ly = key, lane_lengths(settings)
        did = True
    if sketch:
        key = sketch_key(settings)
        if key != ms.sketch_at:
            ms.sketch_at, ms.sketch = key, map_sketch(settings)
            did = True
    return did


def surveyed(ms, settings: Settings, sketch: bool = False) -> bool:
    """Whether `survey` has nothing left to measure."""
    if ms.lane_key != lane_survey_key(settings):
        return False
    return not sketch or ms.sketch_at == sketch_key(settings)


def readout_lines(preview: combat.CombatPreview) -> tuple[str, tuple[int, int, int], str, str]:
    """(headline, headline colour, detail, band) for one previewed fight: the
    same fight from three angles, which must never disagree."""
    roll = preview.nominal
    a, d, s = preview.attacker, preview.defender, roll.survivors

    if roll.winner == combat.ATTACKER:
        headline, color = f"{a} vs {d} → you keep {s}", config.player_color(1)
    elif roll.winner == combat.DEFENDER:
        headline, color = f"{a} vs {d} → they keep {s}", config.player_color(2)
    else:
        headline, color = f"{a} vs {d} → both wiped out", DISABLED_TEXT
    # Never assert a winner the dice don't guarantee: an uncertain fight goes
    # amber whoever the average roll favours.
    if not preview.certain:
        color = WARN

    # What each side actually loses. The sub-1:1 exchange *is* the square law.
    if preview.annihilation:
        detail = "Both fleets are spent — the system goes neutral."
    elif roll.winner == combat.ATTACKER:
        lost = f"You lose {a - s}" if s < a else "You lose nothing"
        detail = f"{lost}, they lose all {d}."
    else:
        lost = f"They lose {d - s}" if s < d else "They lose nothing"
        detail = f"{lost}, you lose all {a}."

    pct = round(preview.jitter * 100)
    if preview.jitter <= 0:
        band = "Jitter off: this result is exact."
    elif not preview.certain:
        likely = {combat.ATTACKER: "yours", combat.DEFENDER: "theirs"}.get(roll.winner, "a wipe-out")
        band = (
            f"Jitter ±{pct}%: likely {likely}, you keep {preview.best.attacker_survivors}"
            f" to them {preview.worst.defender_survivors}."
        )
    elif preview.best.winner == combat.ATTACKER:
        band = "Jitter ±{}%: you keep {}–{}.".format(pct, *preview.band)
    elif preview.best.winner == combat.DEFENDER:
        band = f"Jitter ±{pct}%: they keep {preview.best.defender_survivors}–{preview.worst.defender_survivors}."
    else:  # both corners annihilate: tiny matched fleets, where no roll saves either
        band = f"Jitter ±{pct}%: no roll leaves a survivor."

    return headline, color, detail, band


def matrix_swings() -> tuple[float, ...]:
    """Each axis of the jitter matrix, -1 .. +1 as a fraction of jitter; the
    middle sample is exactly 0.0, the fight the readout describes."""
    return tuple(-1.0 + 2.0 * i / (MATRIX_N - 1) for i in range(MATRIX_N))


def swing_label(swing: float, jitter: float) -> str:
    pct = swing * jitter * 100
    return "0%" if not pct else f"{'+' if pct > 0 else '−'}{abs(pct):.0f}%"


def matrix_cell(preview: combat.CombatPreview, a_swing: float, d_swing: float
                ) -> tuple[str, tuple[int, int, int]]:
    """One jitter-matrix cell: who keeps how many, in their colour."""
    roll = preview.roll(a_swing, d_swing)
    if roll.winner == combat.ATTACKER:
        return f"you {roll.survivors}", config.player_color(1)
    if roll.winner == combat.DEFENDER:
        return f"them {roll.survivors}", config.player_color(2)
    return "wipe-out", DISABLED_TEXT


def challenge_lines(settings: Settings) -> list[tuple[str, tuple[int, int, int]]]:
    """The challenge banner's two lines and their colours: the score to beat,
    then the setup it was scored on (or that the setup has moved off it)."""
    ch = settings.challenge
    valid = ch.matches(settings)

    head = f"CHALLENGE — beat {ch.summary()}"
    if ch.by:
        head += f"   ·   from {ch.by}"
    first = (head, START_BORDER if valid else WARN)

    if not valid:
        return [first, ("setup changed — your score won't compare", WARN)]

    bots = [settings.seat_strategy(s) for s in range(2, settings.players + 1)]
    bits = [f"seed {settings.seed}", f"{settings.system_count()} systems", settings.mode]
    if bots:
        bits.append("vs " + ", ".join(bots))
    mine = webstore.best(*settings.challenge_keys())
    if mine is not None:
        bits.append(f"your best: {mine[0]} turns / {mine[1]} lost")
    return [first, ("   ·   ".join(bits), config.COLOR_TEXT_DIM)]


def gauntlets_label(ms) -> str:
    """The button that opens the gauntlet list: "Tutorial" while that is all there is."""
    return "Gauntlets" if len(ms.gauntlets) > 1 else "Tutorial"


# Menu chrome colours the words above are drawn in (presentation-only, but the
# readout and banner choose between them, so they are named here).
DISABLED_TEXT = (78, 84, 100)
WARN = (214, 172, 92)  # amber: a challenge whose settings no longer match
START_BORDER = (96, 190, 120)
STATUS_ERR = (214, 130, 110)


# --------------------------------------------------------------------------- #
# Changing the setup
# --------------------------------------------------------------------------- #
def set_players(settings: Settings, n: int) -> None:
    if settings.custom_map is not None:
        return  # derived from the hand map (`interlocked`)
    settings.players = max(config.MIN_PLAYERS, min(config.MAX_PLAYERS, n))
    settings.nodes = max(settings.nodes, settings.min_nodes())


def node_step(n: int, up: bool) -> int:
    """The Systems stepper's next value: one at a time up to a standard board,
    then whole ``config.NODES_BIG_STEP`` multiples, or reaching ``MAX_NODES``
    takes eighty presses."""
    big, step = config.STANDARD_MAX_NODES, config.NODES_BIG_STEP
    if up:
        return n + 1 if n < big else (n // step + 1) * step
    return n - 1 if n <= big else max(big, -(-n // step) * step - step)


def set_nodes(settings: Settings, n: int) -> None:
    if settings.custom_map is not None:
        return  # derived from the hand map
    settings.nodes = max(settings.min_nodes(), min(config.MAX_NODES, n))


def step_systems(settings: Settings, up: bool) -> None:
    """Move the Systems row to the next map size this setup can actually build.

    A generated symmetric map only comes in whole sectors, so a plain step can
    land on a request that builds the board already shown. Steps on past those
    to the next size that differs, and stores that size. A press with no such
    size in its direction does nothing.
    """
    if settings.layout_inert():
        return set_nodes(settings, node_step(settings.nodes, up))

    def size(n: int) -> int:
        return mapgen.symmetric_size(n, settings.players, settings.layout)

    now = settings.system_count()
    n = node_step(now, up)
    while size(n) == now and settings.min_nodes() < n < config.MAX_NODES:
        n += 1 if up else -1
    if size(n) != now:
        set_nodes(settings, size(n))


def step_layout(settings: Settings, step: int) -> None:
    layouts = mapgen.SYMMETRIC_LAYOUTS
    at = layouts.index(settings.layout) if settings.layout in layouts else 0
    settings.layout = layouts[(at + step) % len(layouts)]


def seed_digits(text: str) -> str:
    """The seed field's text: digits only, and no seed past ``seed_top``, the
    last digit that would cross it refused as a full field refuses one."""
    digits = "".join(ch for ch in text if ch.isdigit())[:SEED_MAX_LEN]
    while digits and int(digits) > seed_top():
        digits = digits[:-1]
    return digits


def set_seed_text(ms, settings: Settings, text: str) -> str:
    """Type ``text`` into the seed field: filtered, then the setup's seed (blank
    is "roll one at Start"). Returns the text the field now holds."""
    ms.seed_text = seed_digits(text)
    settings.seed = int(ms.seed_text) if ms.seed_text else None
    return ms.seed_text


def apply_slider(key: str, ms, settings: Settings, t: float) -> None:
    """Set slider ``key`` to the fraction ``t`` (0..1) of its track, snapped to
    its step."""
    spec = slider_spec(key, ms, settings)
    if spec is None:
        return
    kind, attr, lo, hi, step, is_int = spec
    t = max(0.0, min(1.0, t))
    snapped = round((lo + t * (hi - lo)) / step) * step
    snapped = max(lo, min(hi, snapped))
    value = round(snapped) if is_int else round(snapped, 4)
    if kind == "adv":
        target = settings
    elif kind == "preview":
        target = ms   # transient: never reaches Settings
    else:
        target = settings.ai[ms.ai_seat - 1]
        if key == AUX_KEY:
            # Editing a legacy seat's stop makes it a seat of the family.
            settings.ai_strategy[ms.ai_seat - 1] = ai.family(settings.ai_strategy[ms.ai_seat - 1])
    setattr(target, attr, value)


def toggle_fog(settings: Settings) -> None:
    """The Basic tab's one-press fog: the preset on, or full sight."""
    if fog_off(settings):
        settings.fog_sight = config.FOG_ON_SIGHT
        settings.fog_scout = config.FOG_ON_SCOUT
    else:
        settings.fog_sight = settings.fog_scout = config.FOG_MAX_HOPS


def clear_map(settings: Settings) -> None:
    """Drop a hand-drawn map: maps come from the seed again. ``nodes`` was
    reconciled to the recipe and may sit below the generated floor."""
    settings.custom_map = None
    settings.nodes = max(settings.min_nodes(), min(config.MAX_NODES, settings.nodes))


Say = Callable[[str, bool], None]


def press(ms, settings: Settings, key: str, say: Say) -> bool:
    """A press on the setup control ``key``: a tab, a seat, a stepper, a toggle,
    the strategy list, a die. True if ``key`` was one of them; the rest (Start,
    the text fields, files and links) are the shell's own. ``say`` shows a
    status line."""
    seat = ms.ai_seat
    if key.startswith("tab_"):
        ms.tab = key[len("tab_"):]
    elif key.startswith("seat_"):
        ms.ai_seat = int(key[len("seat_"):])
    elif key == "strategy":   # open or close the list, rescanning models/
        if not ms.strategy_open:
            ai.load_models()
            ms.strategies = strategy_options()
        ms.strategy_open = not ms.strategy_open
    elif key.startswith("strategy_opt_"):
        idx = int(key[len("strategy_opt_"):])
        if 0 <= idx < len(ms.strategies) and ms.strategies[idx] != settings.ai_strategy[seat - 1]:
            # A new strategy starts at its own default stop: an aux carried from
            # the last one would mean something else to it.
            settings.ai_strategy[seat - 1] = ms.strategies[idx]
            settings.ai[seat - 1] = replace(settings.ai[seat - 1], aux=config.AI_AUX)
        ms.strategy_open = False
    elif key == "copy_all":
        src, strat = settings.ai[seat - 1], settings.ai_strategy[seat - 1]
        for s in ai_seats(settings):
            settings.ai[s - 1] = replace(src)
            settings.ai_strategy[s - 1] = strat
    elif key == "reset_all":
        for s in ai_seats(settings):
            settings.ai[s - 1] = AiParams()
            settings.ai_strategy[s - 1] = "heuristic"
    elif key == "randomise_ai":
        randomise_knobs(settings.ai[seat - 1], ai_specs(seat, settings), fresh_rng())
        settings.ai_strategy[seat - 1] = ai.family(settings.ai_strategy[seat - 1])
    elif key == "randomise_adv":
        randomise_knobs(settings, visible_adv(settings), fresh_rng())
    elif key == "neutral_produces":
        settings.neutral_produces = not settings.neutral_produces
    elif key == "in_lane_battles":
        settings.in_lane_battles = not settings.in_lane_battles
    elif key in ("players_dec", "players_inc"):
        set_players(settings, settings.players + (1 if key.endswith("inc") else -1))
    elif key in ("nodes_dec", "nodes_inc"):
        step_systems(settings, up=key.endswith("inc"))
    elif key in ("mode_random", "mode_symmetric"):
        settings.mode = key[len("mode_"):]
    elif key in ("layout_dec", "layout_inc"):
        step_layout(settings, 1 if key.endswith("inc") else -1)
    elif key == "seed_random":
        settings.seed = random_seed()
        ms.seed_text = str(settings.seed)
    elif key == "autoplay":
        settings.autoplay = not settings.autoplay
    elif key == "fog_of_war":
        toggle_fog(settings)
    elif key in PREFERENCES:
        # A preference the store might refuse (private browsing, a full quota):
        # say so rather than redraw an unticked box with no explanation.
        read, write, refused = PREFERENCES[key]
        want = not read()
        if not write(want) and want:
            say(refused, False)
    else:
        return False
    return True


# The Basic tab's right-hand column: this installation's preferences, never
# Settings fields, so they never travel in a save file or a link and never show
# a changed-dot. key -> (read, write, what a refused write says).
PREFERENCES = {
    "share_games": (webstore.share_games, webstore.set_share_games,
                    "Couldn't save that here — replays stay private"),
    "animate_turns": (webstore.animate_turns, webstore.set_animate_turns,
                      "Couldn't save that here — turns stay instant"),
    "ironman": (webstore.ironman, webstore.set_ironman,
                "Couldn't save that here — History stays available"),
}


# --------------------------------------------------------------------------- #
# A challenge link's setup
# --------------------------------------------------------------------------- #
def comparable(settings: Settings) -> bool:
    """Is there a challenge whose score still applies to this exact setup?"""
    return settings.challenge is not None and settings.challenge.matches(settings)


def before_edit(ms, settings: Settings) -> None:
    """Remember the last setup the challenge's score still applies to, before
    an edit gets the chance to change it."""
    if comparable(settings):
        ms.challenge_snapshot = settings.to_dict()


def after_edit(ms, settings: Settings) -> None:
    """Edited away from the challenge's setup: ask rather than silently
    invalidate its score, once a slider is let go."""
    if settings.challenge is not None and ms.drag_key is None and not settings.challenge.matches(settings):
        ms.confirm_unchallenge = True


def answer_unchallenge(ms, settings: Settings, keep_edit: bool, say: Say) -> str | None:
    """Keep the edit and drop the score, or put the setup back the way the link
    had it. Returns the action the modal stood in front of, if the edit is kept."""
    ms.confirm_unchallenge = False
    deferred, ms.pending_action = ms.pending_action, None
    if keep_edit:
        settings.challenge = None
        ms.challenge_snapshot = None
        # Also drop it from the address bar and the remembered token, or a
        # reload would resurrect the banner just got rid of.
        webstore.sync_settings(settings.to_token())
        say(CLEARED_CHALLENGE_MSG, True)
        return deferred
    if ms.challenge_snapshot is not None:
        settings.copy_from(Settings.from_dict(ms.challenge_snapshot))
        ms.seed_text = "" if settings.seed is None else str(settings.seed)
    return None


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #
def start(ms, settings: Settings, action: str, say: Say,
          asked: tuple[str, list[str]] | None = None) -> str | None:
    """``action``, unless a hand-drawn map would refuse to build (its first
    blocker is said) or a bot warns about the setup (`unless_slow`). ``asked``
    is the campaign's confirm for this setup, if any (`campaign.confirm`)."""
    blockers = settings.custom_map.blockers() if settings.custom_map is not None else []
    if blockers:
        say(blockers[0].text, False)
        return None
    if action in ("start", "run", "run_continue"):
        # Play by post asks at its roster prompt instead, once it knows which
        # seats are bots.
        return unless_slow(ms, settings, action, asked=asked)
    return action


def unless_slow(ms, settings: Settings, action: str, people: set[int] | None = None,
                asked: tuple[str, list[str]] | None = None) -> str | None:
    """``action``, unless a bot in the setup warns it will struggle: then raise
    the slow-setup modal holding ``action`` for "Play anyway" to re-issue. A
    warning already played through is not asked again for the same setup."""
    lines = setup_warnings(settings, people)
    title = ""
    if asked is not None:
        title, campaign_lines = asked
        lines = campaign_lines + lines
    if not lines or tuple(lines) == ms.slow_ack:
        return action
    ms.confirm_slow, ms.slow_lines, ms.slow_action = True, lines, action
    ms.slow_title = title
    return None


def answer_slow(ms, play: bool) -> str | None:
    """Play anyway (re-issuing the held action) or go back to the settings."""
    action, ms.slow_action = ms.slow_action, None
    ms.confirm_slow = False
    if not play:
        return None
    ms.slow_ack = tuple(ms.slow_lines)
    return action


# --------------------------------------------------------------------------- #
# Modal words: (go, stay), with the key hint dropped on a touch screen, where
# there is no Y/N to press
# --------------------------------------------------------------------------- #
def unchallenge_labels(touch: bool) -> tuple[str, str]:
    return ("Change it anyway", "Keep the challenge") if touch else (
        "Change it anyway (Y)", "Keep the challenge (Esc)")


def clear_map_labels(touch: bool) -> tuple[str, str]:
    return ("Clear the map", "Keep it") if touch else ("Clear the map (Y)", "Keep it (Esc)")


def slow_labels(touch: bool) -> tuple[str, str]:
    return ("Play anyway", "Change settings") if touch else ("Play anyway (Y)", "Change settings (Esc)")


def resume_labels(touch: bool) -> tuple[str, str]:
    return ("Resume", "New game") if touch else ("Resume (Y/Enter)", "New game (N/Esc)")


def resume_detail(log) -> str:
    st = log.settings
    return f"turn {log.turn_count} · {st.get('players', '?')} players · {st.get('mode', 'random')} map"


# --------------------------------------------------------------------------- #
# The whole menu as plain JSON, for the HTML build
# --------------------------------------------------------------------------- #
def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


def _slider(key, label, attr, lo, hi, step, is_int, target, names=None) -> dict:
    value = getattr(target, attr)
    t = 0.0 if hi == lo else max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return {"kind": "slider", "key": key, "label": label, "text": slider_text(key, value, hi, is_int, names, lo),
            "t": round(t, 4), "changed": changed(target, attr),
            "steps": round((hi - lo) / step) if step else 0}


def _sliders(specs, target, names=None) -> list[dict]:
    return [_slider(*spec, target=target, names=(names or {}).get(spec[0])) for spec in specs]


def _section(text: str) -> dict:
    return {"kind": "section", "text": text}


def _basic(ms, settings: Settings) -> list[dict]:
    drawn = settings.custom_map is not None

    def row(label, is_changed, control):
        return {"kind": "row", "label": label, "changed": is_changed, "control": control}

    def derived(text):
        return {"kind": "derived", "text": text}

    rows = [
        row("Players", changed(settings, "players"),
            derived(f"{settings.players} (from the map)") if drawn
            else {"kind": "stepper", "key": "players", "value": str(settings.players)}),
        row("Systems", changed(settings, "nodes"),
            derived(f"{settings.nodes} (from the map)") if drawn
            else {"kind": "stepper", "key": "nodes", "value": str(settings.system_count())}),
        row("Map type", changed(settings, "mode"),
            derived("hand-drawn") if drawn else
            {"kind": "segmented", "options": [
                {"key": "mode_random", "label": "Random", "active": settings.mode == "random"},
                {"key": "mode_symmetric", "label": "Symmetric", "active": settings.mode == "symmetric"}]}),
        # Offered only where it shapes the map; elsewhere the row says why rather
        # than vanishing and shifting every row below it.
        row("Layout", layout_changed(settings),
            derived("hand-drawn") if drawn else derived("symmetric maps only") if settings.layout_inert()
            else {"kind": "stepper", "key": "layout", "value": settings.layout.title(), "wide": True}),
        row("Seed", changed(settings, "seed"),
            {"kind": "seed", "text": ms.seed_text if ms.editing_seed
             else "" if settings.seed is None else str(settings.seed),
             "placeholder": "random", "max": SEED_MAX_LEN}),
    ]
    fog_changed = changed(settings, "fog_sight") or changed(settings, "fog_scout")
    rows.append({
        "kind": "checks",
        "left": [{"key": "autoplay", "label": "Autoplay", "on": settings.autoplay,
                  "changed": changed(settings, "autoplay")},
                 {"key": "fog_of_war", "label": "Fog of war", "on": not fog_off(settings),
                  "changed": fog_changed}],
        "right": [{"key": "share_games", "label": "Share replays", "on": webstore.share_games(), "changed": False},
                  {"key": "animate_turns", "label": "Animate turns", "on": webstore.animate_turns(),
                   "changed": False},
                  {"key": "ironman", "label": "Ironman", "on": webstore.ironman(), "changed": False}],
    })
    return rows


def _advanced(ms, settings: Settings) -> list[dict]:
    drawn = settings.custom_map is not None
    left = [_section("Map")]
    if drawn:
        left.append({"kind": "hidden", "lines": ["Placement and lanes are", "hand-drawn. The two lane",
                                                 "knobs live in the creator."]})
    else:
        left += _sliders(ADV_MAP, settings)
    left.append(_section("Travel"))
    left += _sliders(ADV_TRAVEL, settings)
    left += [{"kind": "note", "text": line} for line in lane_readout_lines(ms, settings)]
    left.append({"kind": "die", "key": "randomise_adv", "label": "Randomise all"})

    right = [_section("Visibility")] + _sliders(ADV_FOG, settings) + [_section("Economy")]
    if drawn:
        right.append({"kind": "hidden", "lines": ["Every system's production", "and garrison is set in",
                                                  "the creator."]})
    else:
        right += _sliders(ADV_ECON, settings)
    right.append({"kind": "check", "key": "neutral_produces", "label": "Neutral produces",
                  "on": settings.neutral_produces, "changed": changed(settings, "neutral_produces")})
    return [{"kind": "columns", "left": left, "right": right}]


def _combat(ms, settings: Settings) -> list[dict]:
    preview = combat.preview_fight(ms.preview_attacker, ms.preview_defender,
                                   settings.combat_jitter, settings.defender_advantage)
    headline, color, detail, band = readout_lines(preview)
    items: list[dict] = [_section("How it works"), {"kind": "prose", "lines": list(COMBAT_PROSE)},
                         {"kind": "columns",
                          "left": [_section("The fight")] + _sliders(PREVIEW, ms),
                          "right": [_section("The rules")] + _sliders(ADV_COMBAT, settings)},
                         {"kind": "lanecheck", "key": "in_lane_battles", "label": "In-lane battles",
                          "note": LANE_NOTE, "on": settings.in_lane_battles,
                          "changed": changed(settings, "in_lane_battles")},
                         {"kind": "readout", "headline": headline, "color": _hex(color),
                          "detail": detail, "band": band},
                         {"kind": "section", "text": "Jitter matrix — your swing across, theirs down",
                          "short": "Jitter matrix"}]
    if preview.jitter <= 0:
        items.append({"kind": "note", "text": "Jitter is off, so every battle plays out exactly like this."})
        return items
    swings = matrix_swings()
    rows = []
    for d_swing in reversed(swings):
        cells = []
        for a_swing in swings:
            text, colour = matrix_cell(preview, a_swing, d_swing)
            cells.append({"text": text, "color": _hex(colour), "centre": a_swing == 0.0 and d_swing == 0.0})
        rows.append({"label": swing_label(d_swing, preview.jitter), "cells": cells})
    items.append({"kind": "matrix", "cols": [swing_label(s, preview.jitter) for s in swings], "rows": rows})
    return items


def _ai_tab(ms, settings: Settings) -> list[dict]:
    seats = ai_seats(settings)
    if ms.ai_seat not in seats:
        ms.ai_seat = seats[0]
    strategy, params = settings.seat_strategy(ms.ai_seat), settings.ai[ms.ai_seat - 1]
    label = strategy_label(strategy, params.aux)
    items: list[dict] = [
        {"kind": "seats", "label": "Seat", "seats": [
            {"key": f"seat_{s}", "seat": s, "color": _hex(config.player_color(s)),
             "text": _hex(config.text_on(config.player_color(s))), "selected": s == ms.ai_seat}
            for s in seats]},
        {"kind": "aibuttons", "buttons": [{"key": "copy_all", "label": "Copy to all"},
                                          {"key": "reset_all", "label": "Reset all"}],
         "die": "randomise_ai", "editing": f"editing {config.player_name(ms.ai_seat)}",
         "color": _hex(config.player_color(ms.ai_seat))},
        {"kind": "dropdown", "key": "strategy", "label": "Strategy", "value": label, "open": ms.strategy_open,
         "changed": strategy != "heuristic",
         "options": [{"key": f"strategy_opt_{i}", "label": opt, "current": opt == label}
                     for i, opt in enumerate(ms.strategies)]},
    ]
    names = ai.aux_names(strategy)
    shown = replace(params, aux=ai.aux_reads_as(strategy, params.aux)) if names else params
    items += _sliders(ai_specs(ms.ai_seat, settings), shown, names={AUX_KEY: names} if names else None)
    return items


TAB_VIEWS = {"basic": _basic, "advanced": _advanced, "combat": _combat, "ai": _ai_tab}


def _lines(pairs) -> list[dict]:
    return [{"text": text, "color": _hex(colour)} for text, colour in pairs]


def modal(ms, settings: Settings, touch: bool) -> dict | None:
    """The confirm the menu is waiting on, if any: title and lines, then the
    go and stay buttons (the stay one carries the highlight)."""
    dim, warn = _hex(config.COLOR_TEXT_DIM), _hex(WARN)
    if ms.confirm_unchallenge:
        go, stay = unchallenge_labels(touch)
        ch = settings.challenge
        return {"kind": "unchallenge", "title": "Change the challenge setup?",
                "lines": [{"text": "Your result won't compare to the score on the link", "color": warn},
                          {"text": f"Target: {ch.summary()}" if ch is not None else "", "color": dim}],
                "go": {"key": "unchallenge_change", "label": go},
                "stay": {"key": "unchallenge_keep", "label": stay}}
    if ms.confirm_slow:
        go, stay = slow_labels(touch)
        return {"kind": "slow", "title": ms.slow_title or "This setup may play slowly",
                "lines": [{"text": line, "color": warn if i == 0 else dim} for i, line in enumerate(ms.slow_lines)],
                "go": {"key": "slow_go", "label": go}, "stay": {"key": "slow_back", "label": stay}}
    if ms.confirm_clear_map:
        go, stay = clear_map_labels(touch)
        return {"kind": "clear_map", "title": "Discard the map you drew?",
                "lines": [{"text": "Maps come from the seed again — this one is not saved", "color": warn},
                          {"text": "Save it from the creator first if you want to keep it", "color": dim}],
                "go": {"key": "clear_map_yes", "label": go}, "stay": {"key": "clear_map_no", "label": stay}}
    return None


def sketch_view(ms) -> dict | None:
    """The phone menu's map: where its systems sit, its lanes and homes (with
    their colours), and what it is."""
    sketch = ms.sketch
    if sketch is None:
        return None
    out: dict = {"kind": sketch.kind, "caption": SKETCH_CAPTIONS[sketch.kind],
                 "lane": _hex(config.COLOR_LANE), "neutral": _hex(config.COLOR_NEUTRAL)}
    if sketch.kind != "hidden":
        out["points"] = {str(sid): [round(x, 1), round(y, 1)] for sid, (x, y) in sketch.points.items()}
        out["lanes"] = [list(lane) for lane in sketch.lanes]
        out["homes"] = {str(sid): _hex(config.player_color(owner)) for sid, owner in sketch.homes.items()}
    return out


def view(ms, settings: Settings, touch: bool = False, board: bool = True, every_tab: bool = False) -> dict:
    """The menu on screen, for the page to draw as it stands. ``board`` says
    there is a leaderboard to reach (the dailies, the board's lists).
    ``every_tab`` adds the other tabs' rows too (``panels``), for a phone, whose
    panel is as tall as its tallest tab so that it never moves as tabs change
    (`menu._tallest_tab_h`)."""
    if settings.challenge is not None:
        subtitle = _lines(challenge_lines(settings))
    else:
        subtitle = [{"text": SUBTITLE, "color": _hex(config.COLOR_TEXT_DIM)}]
    drawn = settings.custom_map is not None
    shared = pbp.configured()
    return {
        "title": TITLE,
        "subtitle": subtitle,
        "tabs": [{"key": f"tab_{key}", "label": label, "active": ms.tab == key,
                  "changed": tab_changed(key, settings)} for key, label in TABS],
        "tab": ms.tab,
        "panel": TAB_VIEWS[ms.tab](ms, settings),
        "panels": {key: TAB_VIEWS[key](ms, settings) for key, _label in TABS} if every_tab else None,
        "actions": {
            "tutorial": gauntlets_label(ms) if ms.gauntlets else None,
            "daily": DAILY_LABEL if board else None,
            "get_link": True,
            "shared": shared,
            "recent": board,
            "board": BOARD_LABEL if board and shared else None,
            "play_by_post": shared,
            "create": "Edit map" if drawn else "Create map",
            "clear_map": drawn,
        },
        "lists": {"daily": {"open": ms.daily_open,
                            "items": [{"key": k, "label": label} for k, label in DAILY_ITEMS]},
                  "board": {"open": ms.board_open,
                            "items": [{"key": k, "label": label} for k, label in BOARD_ITEMS]}},
        "hint": None if touch else KEY_HINT,
        "modal": modal(ms, settings, touch),
        "sketch": sketch_view(ms),
        "dragging": ms.drag_key,
    }
