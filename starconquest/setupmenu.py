"""The setup menu apart from how it is drawn: its tabs and rows, what a press
on each means, and the words it shows.

The session hands it the widget key the page sends (`press`, `apply_slider`),
and the page draws `view`, the whole menu as plain JSON. A menu is any object
with `Menu`'s fields (below); everything here mutates only that and
`Settings`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from . import ai, combat, config, daily, gauntlet, hud, mapgen, paths, pbp, webstore
from . import run as gauntlet_run
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
# The HTML build's Save and Load (`files`): the setup and the last match saved
# as files the browser keeps, and any of them, or a map, opened again.
FILES_LABEL = "Files"
FILES_ITEMS = (("save_settings", "Save setup"), ("save_game", "Save last match"), ("load_file", "Open a file…"))
# A phone's grid folds Get Link into the Files list, keeping it to six buttons.
SHARE_SAVE_LABEL = "Share or save"
SHARE_SAVE_ITEMS = (("get_link", "Get Link"), *FILES_ITEMS)

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
    files_open: bool = False
    confirm_quit: bool = False
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
    # The play-by-post roster prompt (`open_pbp_prompt`).
    pbp_prompt: bool = False
    pbp_roster: set[int] = field(default_factory=set)
    pbp_deadline_hours: int = pbp.DEADLINE_HOURS
    pbp_public: bool = False
    pbp_name: str = ""
    pbp_title: str = ""
    pbp_editing: str | None = None
    # The Tutorial list and the stage being played (`menu.MenuState` says why
    # each is kept as it is): never a `Settings` field.
    stage_at: tuple[str, int] | None = None
    stash: dict | None = None
    tutorial_open: bool = False
    gauntlet_pick: int = 0
    stage_bests: dict[str, dict[int, tuple[int, int]]] = field(default_factory=dict)
    kept_ids: frozenset[str] = frozenset()
    gauntlet_note: tuple[str, str, bool] | None = None
    runs: list = field(default_factory=list)
    run_confirm: str = ""
    # The stage the list's graph has selected (`pick_default`): a tap selects,
    # a second tap or Play plays.
    stage_pick: int | None = None


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


def daily_lines(settings: Settings) -> list[tuple[str, tuple[int, int, int]]]:
    """The daily banner's two lines and their colours while the setup is a
    daily's (`daily.daily_of`), or none: one knob moved and it is gone."""
    day, kind = daily.daily_of(settings)
    if not day:
        return []
    head, detail = daily.banner(day, settings, kind)
    bits = [detail]
    mine = webstore.best(*settings.challenge_keys())
    if mine is not None:
        bits.append(f"your best: {mine[0]} turns / {mine[1]} lost")
    return [(head, START_BORDER), ("   ·   ".join(bits), config.COLOR_TEXT_DIM)]


def gauntlets_label(ms) -> str:
    """The button that opens the gauntlet list: "Tutorial" while that is all there is."""
    return "Gauntlets" if len(ms.gauntlets) > 1 else "Tutorial"


# Menu chrome colours the words above are drawn in (presentation-only, but the
# readout and banner choose between them, so they are named here).
DISABLED_TEXT = (78, 84, 100)
WARN = (214, 172, 92)  # amber: a challenge whose settings no longer match
START_BORDER = (96, 190, 120)


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


def open_creator(ms, settings: Settings) -> str | None:
    """``"create_map"``, the map creator to open — or raise the un-challenge
    modal first and let *it* re-issue the action.

    Asking here rather than on the way out of the editor is deliberate: authoring
    a whole map and only then being told it invalidated the score on the link is
    the wrong order to find out.
    """
    if settings.challenge is not None:
        ms.challenge_snapshot = settings.to_dict()
        ms.pending_action = "create_map"
        ms.confirm_unchallenge = True
        return None
    return "create_map"


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
def pbp_prompt_labels(touch: bool) -> tuple[str, str]:
    return ("Create match", "Cancel") if touch else ("Create match (Enter)", "Cancel (Esc)")


def unchallenge_labels(touch: bool) -> tuple[str, str]:
    return ("Change it anyway", "Keep the challenge") if touch else (
        "Change it anyway (Y)", "Keep the challenge (Esc)")


def clear_map_labels(touch: bool) -> tuple[str, str]:
    return ("Clear the map", "Keep it") if touch else ("Clear the map (Y)", "Keep it (Esc)")


def quit_labels(touch: bool) -> tuple[str, str]:
    return ("Quit", "Cancel") if touch else ("Quit (Y)", "Cancel (Esc)")


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
    if ms.confirm_quit:
        go, stay = quit_labels(touch)
        return {"kind": "quit", "title": hud.confirm_quit()[0], "lines": [],
                "go": {"key": "quit_yes", "label": go}, "stay": {"key": "quit_no", "label": stay}}
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
    elif ms.stage_at is not None:
        subtitle = _lines(stage_lines(ms, settings))
    else:
        subtitle = _lines(daily_lines(settings)) or [{"text": SUBTITLE, "color": _hex(config.COLOR_TEXT_DIM)}]
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
            "files": FILES_LABEL,
            "share_save": SHARE_SAVE_LABEL,
        },
        "lists": {"daily": {"open": ms.daily_open,
                            "items": [{"key": k, "label": label} for k, label in DAILY_ITEMS]},
                  "board": {"open": ms.board_open,
                            "items": [{"key": k, "label": label} for k, label in BOARD_ITEMS]},
                  "files": {"open": ms.files_open,
                            "items": [{"key": k, "label": label} for k, label in FILES_ITEMS]},
                  "share_save": {"open": ms.files_open,
                                 "items": [{"key": k, "label": label} for k, label in SHARE_SAVE_ITEMS]}},
        "hint": None if touch else KEY_HINT,
        "modal": modal(ms, settings, touch),
        "pbp_prompt": pbp_prompt_view(ms, settings, touch),
        "tutorial": tutorial_view(ms, settings, touch),
        "sketch": sketch_view(ms),
        "dragging": ms.drag_key,
    }


# --------------------------------------------------------------------------- #
# The play-by-post roster prompt: who at the table is a person, how long their
# clock runs, whether the lobby lists the match, and two optional words for
# it, before the match is opened
# --------------------------------------------------------------------------- #
PBP_PROMPT_TITLE = "Who's playing?"
# `pbp.DEADLINE_HOURS` is the format's own unit ("two days is what play-by-post
# exists for"), so the stepper moves in whole days, from one day up to the
# endpoint's own ceiling of 336h (14 days).
PBP_DEADLINE_STEP_H = 24
PBP_DEADLINE_MIN_H = 24
PBP_DEADLINE_MAX_H = 336
# The prompt's two free-text rows: (label, which, character limit).
PBP_TEXT_ROWS = (("Your name", "name", pbp.NAME_MAX), ("Title", "title", pbp.TITLE_MAX))


def deadline_label(hours: int) -> str:
    days = max(1, hours // 24)
    return "1 day" if days == 1 else f"{days} days"


def step_deadline(ms, by: int) -> None:
    ms.pbp_deadline_hours = max(PBP_DEADLINE_MIN_H, min(
        PBP_DEADLINE_MAX_H, ms.pbp_deadline_hours + by * PBP_DEADLINE_STEP_H))


def pbp_text(ms, which: str) -> str:
    return ms.pbp_name if which == "name" else ms.pbp_title


def set_pbp_text(ms, which: str, text: str) -> None:
    """A text row's new text, reduced to printable characters within its limit."""
    limit = next(cap for _, key, cap in PBP_TEXT_ROWS if key == which)
    text = "".join(ch for ch in text if ch.isprintable())[:limit]
    if which == "name":
        ms.pbp_name = text
    else:
        ms.pbp_title = text


def open_pbp_prompt(ms, settings: Settings) -> None:
    """Open the roster prompt on its defaults: every seat a person, the format's
    own deadline, private, and the name this installation last used."""
    ms.pbp_prompt = True
    ms.pbp_roster = set(range(1, settings.players + 1))
    ms.pbp_deadline_hours = pbp.DEADLINE_HOURS
    ms.pbp_public = False
    ms.pbp_name = ""
    set_pbp_text(ms, "name", webstore.pbp_name())
    ms.pbp_title = ""
    ms.pbp_editing = None


def close_pbp_prompt(ms) -> None:
    ms.pbp_prompt = False
    ms.pbp_editing = None


def confirm_pbp(ms, settings: Settings, asked: tuple[str, list[str]] | None = None) -> str | None:
    """Create match: ``"play_by_post"`` for the seats still checked (seat 1
    always, the creator's), unless a bot in the setup warns first."""
    close_pbp_prompt(ms)
    return unless_slow(ms, settings, "play_by_post", ms.pbp_roster | {1}, asked)


def pbp_press(ms, settings: Settings, key: str) -> str | None:
    """A press on the prompt's control ``key``: a seat's box (``pbp_seat_<n>``,
    never seat 1's, the creator's), the deadline's −/+, Publicly joinable, a
    text row's field (``pbp_name_field``: it takes the caret), Create match or
    Cancel. Answers ``"play_by_post"`` when the match is to be created."""
    ms.pbp_editing = None
    if key.startswith("pbp_seat_"):
        seat = int(key[len("pbp_seat_"):])
        if 2 <= seat <= settings.players:
            ms.pbp_roster.symmetric_difference_update({seat})
    elif key in ("pbp_deadline_dec", "pbp_deadline_inc"):
        step_deadline(ms, -1 if key.endswith("dec") else 1)
    elif key == "pbp_public":
        ms.pbp_public = not ms.pbp_public
    elif key in ("pbp_name_field", "pbp_title_field"):
        ms.pbp_editing = key[len("pbp_"):-len("_field")]
    elif key == "pbp_confirm":
        return confirm_pbp(ms, settings)
    elif key == "pbp_cancel":
        close_pbp_prompt(ms)
    return None


def pbp_prompt_view(ms, settings: Settings, touch: bool) -> dict | None:
    """The roster prompt as it stands, or None while it is shut."""
    if not ms.pbp_prompt:
        return None
    go, stay = pbp_prompt_labels(touch)
    return {
        "title": PBP_PROMPT_TITLE,
        "seats": [{"seat": seat, "name": config.player_name(seat), "color": _hex(config.player_color(seat)),
                   "you": seat == 1, "checked": seat in ms.pbp_roster, "key": f"pbp_seat_{seat}"}
                  for seat in range(1, settings.players + 1)],
        "deadline": deadline_label(ms.pbp_deadline_hours),
        "public": ms.pbp_public,
        "text": [{"label": label, "key": f"pbp_{which}_field", "which": which, "text": pbp_text(ms, which),
                  "limit": cap, "editing": ms.pbp_editing == which} for label, which, cap in PBP_TEXT_ROWS],
        "go": {"key": "pbp_confirm", "label": go},
        "stay": {"key": "pbp_cancel", "label": stay},
    }


# --------------------------------------------------------------------------- #
# Gauntlets and the tutorial: the stage the setup is, the Tutorial list (its
# stages, progress and runs) and what a press on it means. Moved out of
# `menu`, which keeps the list's drawing and hit-testing
# --------------------------------------------------------------------------- #
HIGHLIGHT = (120, 150, 210)   # `menu._HL_BORDER`: an open stage's status
COPY_GAUNTLET = "Copy link"
TUTORIAL_KEYS = ("stage_", "tutorial_", "gauntlet_pick_", "run_")
RUN_KEYS = ("run_start", "run_continue", "run_abandon")
RUN_ABOUT = "Run: every stage in order, each within its target, one life"
RUN_ABOUT_FORK = "Run: one way through to the end, each stage within its target, one life"
PROGRESS_NOTE = "Progress is kept in this browser — clearing site data resets it"

Found = tuple[gauntlet.Gauntlet, gauntlet.Stage]


def active_stage(ms, settings: Settings) -> Found | None:
    """The gauntlet stage the menu's setup still is, or None.

    A stage of the gauntlet being played, matched by challenge key
    (`gauntlet.stage_of`): one knob moved and it is your own setup again. A
    challenge link's setup is never a stage, even one that happens to match,
    because its banner has the subtitle slot.
    """
    if ms.stage_at is None or settings.challenge is not None:
        return None
    g = gauntlet.find(ms.gauntlets, ms.stage_at[0])
    if g is None:
        return None
    sid = gauntlet.stage_of(g, settings)
    stage = g.stage(sid) if sid is not None else None
    return (g, stage) if stage is not None else None


def next_stage(ms, settings: Settings) -> Found | None:
    """Move the menu onto the stage after the one being played and return it, or
    None at the end (or a fork): the win overlay's Next stage, so going back to
    the menu afterwards lands on the stage just started."""
    found = active_stage(ms, settings)
    if found is None:
        return None
    g, stage = found
    nxt = gauntlet.next_stage(g, stage.id, gauntlet.bests(g))
    if nxt is None:
        return None
    return load_stage(ms, settings, g, nxt)


def load_stage(ms, settings: Settings, g: gauntlet.Gauntlet, sid: int) -> Found | None:
    """Put the menu on stage ``sid`` of ``g`` and return it, as picking it from
    the list does (and a run, which moves from stage to stage)."""
    stage = g.stage(sid)
    if stage is None:
        return None
    if ms.stash is None and active_stage(ms, settings) is None:
        ms.stash = settings.to_dict()   # the player's own setup, once
    settings.copy_from(gauntlet.stage_settings(stage))
    ms.stage_at = (g.id, sid)
    ms.seed_text = "" if settings.seed is None else str(settings.seed)
    return g, stage


def playing_word(ms) -> str:
    """What the stage being played belongs to, in a label: "tutorial" only for
    the built-in one."""
    return "tutorial" if ms.stage_at is None or ms.stage_at[0] == gauntlet.TUTORIAL_ID else "gauntlet"


def leave_gauntlet(ms, settings: Settings, say: Say) -> None:
    """Stop playing the gauntlet. Your own setup comes back if the menu is still
    on a stage; if you have already changed it, what is there now stays."""
    if active_stage(ms, settings) is not None and ms.stash is not None:
        settings.copy_from(Settings.from_dict(ms.stash))
        ms.seed_text = "" if settings.seed is None else str(settings.seed)
    word = playing_word(ms)
    ms.stage_at, ms.stash = None, None
    ms.tutorial_open = False
    say(f"Left the {word} — this is your own setup", True)


def stage_bits(settings: Settings) -> list[str]:
    """What the stage is, in a line: its size, whether it is fogged, and who it
    is against — a bot at a named stop of its knob says which."""
    bots = []
    for seat in range(2, settings.players + 1):
        name = settings.seat_strategy(seat)
        stop = ai.aux_stop_name(name, settings.seat_params(seat).aux)
        bots.append(f"{name} · {stop}" if stop else name)
    bits = [f"{settings.system_count()} systems", "no fog" if fog_off(settings) else "fog of war"]
    if bots:
        bits.append("vs " + ", ".join(bots))
    return bits


def stage_lines(ms, settings: Settings) -> list[tuple[str, tuple[int, int, int]]]:
    """The stage banner's two lines and their colours, in the subtitle's slot
    while a gauntlet is being played (as the challenge banner uses it), or none
    when the stage's gauntlet can't be found. Once a knob has moved it warns
    rather than locks, for the reason `challenge_lines` gives."""
    g = gauntlet.find(ms.gauntlets, ms.stage_at[0]) if ms.stage_at else None
    found = active_stage(ms, settings)
    if g is None:
        return []
    if found is None:
        return [(f"{g.name.upper()} — setup changed", WARN),
                (f"this isn't the stage any more — open {gauntlets_label(ms)} to pick one, or leave", WARN)]
    _g, stage = found
    head = f"{g.name.upper()} {gauntlet.place(g, stage.id)} — {stage.title}"
    bits = stage_bits(settings)
    mine = webstore.best(*settings.challenge_keys())
    if mine is not None:
        bits.append(f"your best: {mine[0]} turns / {mine[1]} lost")
    return [(head, START_BORDER), ("   ·   ".join(bits), config.COLOR_TEXT_DIM)]


def open_tutorial(ms, settings: Settings, say: Say, pick: str | None = None) -> None:
    """Open the Tutorial list, on gauntlet ``pick`` (else the one being
    played), reading every gauntlet, their progress and the runs afresh."""
    ms.gauntlets = gauntlet.every()     # a user gauntlet dropped in since shows now
    ms.stage_bests = {}
    ms.kept_ids = frozenset(g.id for g in gauntlet.remembered())
    ms.runs = gauntlet_run.runs()
    ms.run_confirm = ""
    ms.gauntlet_note = None
    ms.stage_pick = None
    ids = [g.id for g in ms.gauntlets]
    if pick is None and ms.stage_at:
        pick = ms.stage_at[0]
    ms.gauntlet_pick = ids.index(pick) if pick in ids else 0
    ms.tutorial_open = bool(ms.gauntlets)
    if not ms.gauntlets:
        say("No tutorial found in this build", False)


def say_gauntlet(ms, gid: str, text: str, ok: bool, say: Say) -> None:
    """Tell the player something about gauntlet ``gid``: in the list while it is
    open, since the list covers the status line, else on the status line."""
    if ms.tutorial_open:
        ms.gauntlet_note = (gid, text, ok)
    else:
        say(text, ok)


def reopen_gauntlets(ms, settings: Settings, say: Say) -> None:
    """Open the list again on the gauntlet it was showing, keeping what was
    said about it: where a run that couldn't start explains why."""
    g, said = picked_gauntlet(ms), ms.gauntlet_note
    open_tutorial(ms, settings, say, pick=g.id if g is not None else None)
    ms.gauntlet_note = said


def picked_gauntlet(ms) -> gauntlet.Gauntlet | None:
    """The gauntlet the list is showing."""
    if not ms.gauntlets:
        return None
    return ms.gauntlets[max(0, min(len(ms.gauntlets) - 1, ms.gauntlet_pick))]


def tutorial_labels(ms, touch: bool) -> tuple[str, str]:
    leave = f"Leave {playing_word(ms)}"
    return (leave, "Close") if touch else (leave, "Close (Esc)")


def shareable(ms, g: gauntlet.Gauntlet, web: bool) -> bool:
    """Can Copy link send ``g`` on: one opened from a link or generated here, on
    the web (where there is a clipboard and a page to link to). Never a built-in
    or a desktop file."""
    return web and g.id in ms.kept_ids


def copy_gauntlet(ms, g: gauntlet.Gauntlet, web: bool, say: Say) -> None:
    """Copy a ``#gauntlet=`` link carrying the whole file: no board needed, so a
    generated gauntlet can be shared as it is."""
    data = gauntlet.kept(g.id) if shareable(ms, g, web) else None
    url = webstore.link_url(gauntlet.link_fragment(data)) if data is not None else ""
    if url and webstore.copy_to_clipboard(url):
        say_gauntlet(ms, g.id, f"Link to {g.name} copied", True, say)
    else:
        say_gauntlet(ms, g.id, "Couldn't copy the gauntlet's link", False, say)


def live_run(ms):
    return next((r for r in ms.runs if r.status == gauntlet_run.ACTIVE), None)


def run_row(ms, g: gauntlet.Gauntlet) -> tuple[str, list[tuple[str, str]]] | None:
    """The list's run line and its buttons, ``(text, [(key, label)])``, or None
    for a gauntlet that can't be run (`run.offered`): the tutorial. Where the
    run may go more than one way, the button names the selected stage once it
    is one of them, and the line asks for one until it is."""
    if not gauntlet_run.offered(g):
        return None
    live = live_run(ms)
    if live is not None and live.gauntlet_id == g.id:
        at, total = gauntlet_run.position(live, g)
        abandon = "Sure? Abandon" if ms.run_confirm == "run_abandon" else "Abandon run"
        where = f"stage {at} of {total}" if total else f"stage {at}"
        go = "Continue run"
        ways = gauntlet_run.ways_on(live, g)
        if len(ways) > 1:
            stage = g.stage(ms.stage_pick) if ms.stage_pick in ways else None
            where += " · choose the way on: a stage marked run"
            go = f"Continue run: {stage.title}" if stage is not None else go
        return f"Run under way: {where}", [("run_continue", go), ("run_abandon", abandon)]
    if live is not None and ms.run_confirm == "run_start":
        return "Starting this ends your run of another gauntlet", [("run_start", "Start anyway")]
    about = RUN_ABOUT if gauntlet_run.linear(g) else RUN_ABOUT_FORK
    return gauntlet_run.summary(g, ms.runs) or about, [("run_start", "Start run")]


def run_stages(ms, g: gauntlet.Gauntlet) -> list[int]:
    """The stages to mark "run" on the active run of ``g``: the one it is
    playing, or every way it may go on."""
    live = live_run(ms)
    if live is None or live.gauntlet_id != g.id or live.status != gauntlet_run.ACTIVE:
        return []
    return [live.playing.stage] if live.playing is not None else gauntlet_run.ways_on(live, g)


@dataclass(frozen=True)
class StageRow:
    """One row of the Tutorial list: the stage, its place, whether it can be
    picked, its status and colour, and whether it is the one being played."""

    stage: gauntlet.Stage
    index: int
    open: bool
    status: str
    colour: tuple[int, int, int]
    here: bool
    short: str = ""    # the status in a word or two, for a node on the graph


def stage_rows(ms, settings: Settings, g: gauntlet.Gauntlet) -> list[StageRow]:
    """The list's stages in order, each with its best once won and its target
    if it has one.

    A stage is open if it is a start, is won, or a lane leads to it from a
    cleared one (`gauntlet.open_stages`): a win short of the stage's target
    leaves it open but opens nothing beyond. A locked one says what the lane
    into it needs ("Bonus: target 86/72"), and one behind only hidden lanes
    isn't listed until they open (`gauntlet.shown`). Bests are read once per
    opening of the list (``ms.stage_bests``)."""
    if g.id not in ms.stage_bests:
        ms.stage_bests[g.id] = gauntlet.bests(g)
    bests = ms.stage_bests[g.id]
    done = gauntlet.cleared(g, bests)
    opened = gauntlet.open_stages(g, done, set(bests), bests)
    found = active_stage(ms, settings)
    here = found[1].id if found is not None and found[0].id == g.id else None
    run_on = run_stages(ms, g)
    rows = []
    for i, stage in enumerate(gauntlet.shown(g, opened)):
        open_ = stage.id in opened
        mine = bests.get(stage.id)
        aim = f" · {stage.target.short()}" if stage.target is not None else ""
        if stage.id in done and mine is not None:
            status, colour = f"✓ {mine[0]} turns / {mine[1]} lost", START_BORDER
            short = f"✓ {mine[0]}/{mine[1]}"
        elif mine is not None:     # won, short of the target: "18 turns · target 14"
            status, colour = f"{mine[0]} turns / {mine[1]} lost{aim}", HIGHLIGHT
            short = f"{mine[0]}/{mine[1]}"
        elif open_:
            short = "next" if bests else "start here"
            status, colour = short + aim, HIGHLIGHT
        else:
            gate = gauntlet.way_in(g, stage.id)
            if gate is not None:
                aim = f" · {gate[0]}: {gate[1].short()}"
            status, colour, short = "locked" + aim, DISABLED_TEXT, "locked"
        if stage.id in run_on:
            status, colour, short = "run · " + status, START_BORDER, "run · " + short
        rows.append(StageRow(stage, i, open_, status, colour, stage.id == here, short))
    return rows


def tutorial_press(ms, settings: Settings, key: str, web: bool, say: Say,
                   begin: Callable[[str], str | None]) -> str | None:
    """A press on the Tutorial list's control ``key``: pick an open stage (it
    loads and starts through ``begin``, the gate Start goes through), step the
    gauntlet picker, a run button, Leave, Copy link or Close. Answers the
    action to carry out (``start``, ``run``, ``run_continue``) or None."""
    g = picked_gauntlet(ms)
    armed, ms.run_confirm = ms.run_confirm, ""   # any other press disarms a run button
    if key in RUN_KEYS and g is not None:
        return _run_press(ms, settings, g, key, armed, say, begin)
    if key == "tutorial_close":
        ms.tutorial_open = False
    elif key == "tutorial_leave":
        leave_gauntlet(ms, settings, say)
    elif key == "tutorial_copy" and g is not None:
        copy_gauntlet(ms, g, web, say)
    elif key in ("gauntlet_pick_dec", "gauntlet_pick_inc") and ms.gauntlets:
        step = 1 if key.endswith("inc") else -1
        ms.gauntlet_pick = (ms.gauntlet_pick + step) % len(ms.gauntlets)
        ms.stage_pick = None
    elif key == "stage_play" and g is not None:
        return play_pick(ms, settings, g, begin)
    elif key == "stage_edit" and g is not None:
        edit_on_board(ms, g, say)
    elif key.startswith("stage_") and g is not None and key[len("stage_"):].isdigit():
        sid = int(key[len("stage_"):])
        if sid == picked_stage(ms, settings, g):
            return play_pick(ms, settings, g, begin)
        if any(r.stage.id == sid for r in stage_rows(ms, settings, g)):
            ms.stage_pick = sid
    return None


def play_pick(ms, settings: Settings, g: gauntlet.Gauntlet,
              begin: Callable[[str], str | None]) -> str | None:
    """Play the selected stage, if it is open: load it and start through
    ``begin``, the gate Start goes through. A locked one only stays selected."""
    sid = picked_stage(ms, settings, g)
    rows = {r.stage.id: r for r in stage_rows(ms, settings, g)}
    if sid is None or not rows[sid].open or load_stage(ms, settings, g, sid) is None:
        return None
    ms.tutorial_open = False
    return begin("start")


def picked_stage(ms, settings: Settings, g: gauntlet.Gauntlet) -> int | None:
    """The selected stage, settled: the pick if it is still shown, else the
    stage being played, the run's, the first open stage not yet cleared, the
    first start, in that order (and kept, so arrows step from it)."""
    rows = stage_rows(ms, settings, g)
    shown = {r.stage.id for r in rows}
    if ms.stage_pick not in shown:
        bests = ms.stage_bests.get(g.id, {})
        done = gauntlet.cleared(g, bests)
        here = next((r.stage.id for r in rows if r.here), None)
        run_on = next(iter(run_stages(ms, g)), None)
        fresh = next((r.stage.id for r in rows if r.open and r.stage.id not in done), None)
        start = next((s for s in g.start if s in shown), None)
        ms.stage_pick = next((s for s in (here, run_on, fresh, start) if s in shown),
                             rows[0].stage.id if rows else None)
    return ms.stage_pick


# Arrow keys on the list's graph, by `KeyboardEvent.key` name: (columns, rows).
STEPS = {"ArrowLeft": (-1, 0), "ArrowRight": (1, 0), "ArrowUp": (0, -1), "ArrowDown": (0, 1)}


def step_pick(ms, settings: Settings, g: gauntlet.Gauntlet, dx: int, dy: int) -> None:
    """Move the selection to the nearest shown stage that way on the graph, by
    cell: the closest column (or row) that way, then the nearest across it. At
    an edge it stays put."""
    sid = picked_stage(ms, settings, g)
    if sid is None:
        return
    cells = gauntlet.layout(g)
    shown = [r.stage.id for r in stage_rows(ms, settings, g)]
    c, r = cells[sid]

    def ahead(other: int) -> tuple[int, int] | None:
        oc, orow = cells[other]
        along, across = (oc - c, orow - r) if dx else (orow - r, oc - c)
        if along * (dx or dy) <= 0:
            return None
        return abs(along), abs(across)

    best = min(((ahead(o), o) for o in shown if o != sid and ahead(o) is not None), default=None)
    if best is not None:
        ms.stage_pick = best[1]


def board_draft(g: gauntlet.Gauntlet) -> str:
    """Where ``g`` opens as a draft on the board's maker, or "" for one the
    board never stored (a built-in, a desktop file, a generated one): only a
    stored row has an id of the board's shape."""
    if not gauntlet.on_board(g):
        return ""
    base = webstore.leaderboard_url(paths.LEADERBOARD_MAKER_PATH)
    return f"{base}#{g.id}" if base else ""


def edit_on_board(ms, g: gauntlet.Gauntlet, say: Say) -> None:
    """Open ``g`` on the board's gauntlet page as a draft to edit."""
    url = board_draft(g)
    if url and webstore.open_url(url):
        say_gauntlet(ms, g.id, f"Opening {g.name} on the board", True, say)
    else:
        say_gauntlet(ms, g.id, "Couldn't open the board", False, say)


def _run_press(ms, settings: Settings, g: gauntlet.Gauntlet, key: str, armed: str, say: Say,
               begin: Callable[[str], str | None]) -> str | None:
    """A run button: put the menu on the stage to play and hand back the action
    ("run" to start, "run_continue"), which goes through the same gate Start
    does. Starting over another gauntlet's run, and abandoning, each take a
    second press (``armed``)."""
    live = live_run(ms)
    mine = live if live is not None and live.gauntlet_id == g.id else None
    if key == "run_abandon":
        if mine is None:
            return None
        if armed != key:
            ms.run_confirm = key
            return None
        gauntlet_run.abandon(mine)
        ms.runs = gauntlet_run.runs()
        say_gauntlet(ms, g.id, "Run abandoned", True, say)
        return None
    pick = picked_stage(ms, settings, g)
    if key == "run_start":
        if live is not None and mine is None and armed != key:
            ms.run_confirm = key
            return None
        starts = sorted(g.start, key=g.position)
        sid = pick if pick in starts else starts[0] if len(starts) == 1 else None
        action = "run"
        if sid is None:
            say_gauntlet(ms, g.id, "Select the start to run from first", False, say)
    else:
        sid = gauntlet_run.stage_to_play(mine, g, pick) if mine is not None else None
        action = "run_continue"
        if sid is None and mine is not None and gauntlet_run.ways_on(mine, g):
            say_gauntlet(ms, g.id, "Select a stage marked run to choose the way on", False, say)
    if sid is None or load_stage(ms, settings, g, sid) is None:
        return None
    ms.tutorial_open = False
    return begin(action)


def graph_view(ms, settings: Settings, g: gauntlet.Gauntlet) -> dict:
    """The gauntlet as the list's graph draws it (`webapp/js/stagegraph.mjs`):
    each shown stage at its `gauntlet.layout` cell with its short status, and
    the lanes between shown stages. A hidden lane is drawn only once met (its
    near stage's best meets its gate), so it never gives a bonus away."""
    rows = stage_rows(ms, settings, g)
    cells = gauntlet.layout(g)
    ends = gauntlet.goals(g)
    pick = picked_stage(ms, settings, g)
    run_on = run_stages(ms, g)
    shown = {r.stage.id for r in rows}
    bests = ms.stage_bests.get(g.id, {})
    nodes = []
    for r in rows:
        sid = r.stage.id
        marks = [m for m, on in (("start", sid in g.start), ("goal", sid in ends), ("run", sid in run_on),
                                 ("here", r.here), ("picked", sid == pick)) if on]
        col, row = cells[sid]
        nodes.append({"key": f"stage_{sid}", "col": col, "row": row, "place": str(r.index + 1),
                      "title": r.stage.title, "status": r.short, "color": _hex(r.colour),
                      "open": r.open, "marks": marks})
    lanes = []
    for a, b in g.lanes:
        gate = g.lane_targets.get((a, b))
        mine = bests.get(a)
        hidden = (a, b) in g.hidden
        if a not in shown or b not in shown or (hidden and not (mine is not None and gate.met(mine))):
            continue
        lanes.append({"from": f"stage_{a}", "to": f"stage_{b}", "gated": gate is not None,
                      "short": gate.short() if gate is not None else "", "hidden": hidden,
                      "two_way": (a, b) in g.two_way})
    return {"nodes": nodes, "lanes": lanes, "focus": f"stage_{pick}" if pick is not None else None}


def pick_view(ms, settings: Settings, g: gauntlet.Gauntlet, web: bool) -> dict | None:
    """The strip under the graph: the selected stage, its progress, its target
    and each way on with what it needs, with Play (and Edit on the board for a
    gauntlet stored there)."""
    sid = picked_stage(ms, settings, g)
    rows = {r.stage.id: r for r in stage_rows(ms, settings, g)}
    if sid is None:
        return None
    r = rows[sid]
    lines = [{"text": r.status, "color": _hex(r.colour)}]
    if r.stage.target is not None:
        lines.append({"text": f"Target: {r.stage.target.text()}", "color": _hex(HIGHLIGHT)})
    plain = [g.stage(b).title for b in sorted(g.onward(sid, gated=False), key=g.position)
             if b in rows]
    if plain:
        lines.append({"text": "Leads to " + ", ".join(plain), "color": _hex(DISABLED_TEXT)})
    bests = ms.stage_bests.get(g.id, {})
    for a, b, lane in g.ways():
        gate = g.lane_targets.get(lane)
        if a != sid or gate is None:
            continue
        mine = bests.get(a)
        if lane in g.hidden and not (mine is not None and gate.met(mine)):
            continue       # a hidden way says nothing until it is met
        label = gauntlet.lane_label(g, a, b) or gauntlet.GATED
        lines.append({"text": f"{label} to {g.stage(b).title}: {gate.text()}", "color": _hex(WARN)})
    edit = bool(web and board_draft(g))
    return {"title": f"{r.index + 1} · {r.stage.title}", "lines": lines,
            "play": {"key": "stage_play", "label": "Play", "enabled": r.open},
            "edit": {"key": "stage_edit", "label": "Edit on the board"} if edit else None}


def tutorial_view(ms, settings: Settings, touch: bool, web: bool = True) -> dict | None:
    """The Tutorial list as it stands, or None while it is shut."""
    g = picked_gauntlet(ms) if ms.tutorial_open else None
    if g is None:
        return None
    leave, close = tutorial_labels(ms, touch)
    picker = len(ms.gauntlets) > 1
    said = ms.gauntlet_note if ms.gauntlet_note is not None and ms.gauntlet_note[0] == g.id else None
    graph = graph_view(ms, settings, g)    # settles the selection the run row names
    row = run_row(ms, g)
    buttons = [{"key": "tutorial_leave", "label": leave, "tone": "warn"}] if ms.stage_at is not None else []
    if shareable(ms, g, web):
        buttons.append({"key": "tutorial_copy", "label": COPY_GAUNTLET, "tone": "hl"})
    buttons.append({"key": "tutorial_close", "label": close, "tone": "dim"})
    return {
        "heading": "Pick a gauntlet, then a stage" if picker else "Pick a stage",
        "picker": {"name": g.name, "at": f"{ms.gauntlet_pick + 1}/{len(ms.gauntlets)}"} if picker else None,
        "graph": graph,
        "pick": pick_view(ms, settings, g, web),
        "run": {"text": row[0], "buttons": [{"key": k, "label": label,
                                             "warn": k == "run_abandon" or ms.run_confirm == k}
                                            for k, label in row[1]]} if row is not None else None,
        "note": PROGRESS_NOTE if web else "",
        "said": {"text": said[1], "ok": said[2]} if said is not None else None,
        "buttons": buttons,
    }
