"""The measured-layout kit: fonts, text, buttons, sliders, modals and the small
map-look primitives, shared by every scene that draws on the real surface.

Extracted from ``render`` once a second such scene existed (``mapmaker``), so the
two cannot drift apart on what a button looks like or where a slider's knob sits.
Nothing here knows about ``GameState`` or ``Ui`` — every function takes a surface,
a rect, and plain values, which is what makes it shareable at all.

**``menu`` deliberately does not use this**, and that is not an oversight. The
menu lays itself out on a fixed 1440x960 canvas and then letterbox-blits it, so
its fonts must be *unscaled* — feeding it ``config.FONT_SIZE*`` would scale twice.
It keeps its own `_fonts`/`_text`/`_button` for that reason. A scene that draws on
the real surface uses this module; a scene that draws on a fixed canvas cannot.

Everything that holds text is measured rather than given a fixed pixel size: a
constant width or row pitch is only ever right at one font size, and the UI font
grows with ``config.ui_scale`` (up to ~2x on a phone). The helpers here are what
keep labels inside their buttons and rows clear of each other at any scale — plus
the two that adapt to a touch build (see ``config.touch_ui``).
"""

from __future__ import annotations

import pygame

from . import config, uifont

# Built lazily and rebuilt whenever the UI scale moves, so a font cached at one
# scale can never outlive it. `_FONT_SCALE` is a sentinel rather than a key inside
# `_FONTS` so the dict stays `dict[str, Font]` for the type checker; the `not
# _FONTS` half of the guard is what lets a test clear the cache to rebuild fonts
# under a fresh pygame session.
_FONTS: dict[str, pygame.font.Font] = {}
_FONT_SCALE: float | None = None


def fonts() -> dict[str, pygame.font.Font]:
    global _FONT_SCALE
    if not _FONTS or _FONT_SCALE != config.ui_scale:
        _FONTS.clear()
        _FONTS["small"] = uifont.load(config.FONT_SIZE_SMALL)
        _FONTS["normal"] = uifont.load(config.FONT_SIZE)
        _FONTS["big"] = uifont.load(config.FONT_SIZE_BIG, bold=True)
        _FONT_SCALE = config.ui_scale
    return _FONTS


def text(surface, font, s, color, center=None, topleft=None, midleft=None, midright=None):
    img = font.render(s, True, color)
    rect = img.get_rect()
    if center:
        rect.center = center
    elif topleft:
        rect.topleft = topleft
    elif midleft:
        rect.midleft = midleft
    elif midright:
        rect.midright = midright
    surface.blit(img, rect)
    return rect


# --------------------------------------------------------------------------- #
# Palette — (fill, edge) pairs
# --------------------------------------------------------------------------- #
BTN_BLUE = ((40, 52, 78), (110, 140, 200))  # ordinary action
BTN_ACTIVE = ((92, 70, 46), (190, 150, 96))  # a toggle that is currently on
BTN_AMBER = ((120, 86, 46), (200, 150, 96))  # new map / rewind
BTN_VIOLET = ((52, 46, 78), (150, 130, 200))  # history / review
BTN_RED = ((92, 46, 52), (200, 96, 104))  # quit
BTN_DANGER = ((120, 46, 52), (200, 96, 104))  # ...and its brighter modal confirm
BTN_GREEN = ((46, 92, 60), (96, 190, 120))  # end turn / confirm
BTN_TEAL = ((44, 62, 74), (120, 180, 200))  # share a challenge
BTN_GOLD = ((92, 76, 36), (210, 180, 80))  # post to the public leaderboard


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #
def row_h(kind: str = "small") -> int:
    """Pitch for one line of stacked text in ``kind``'s font: the font's own line
    height plus a gap. Derived, so rows stay legibly apart instead of colliding
    once the font outgrows a hardcoded pitch."""
    return fonts()[kind].get_height() + config.ROW_GAP


def btn_w(font, label: str, min_w: int = 0) -> int:
    """Width of a button that has to fit ``label``: measured text plus padding."""
    return max(min_w, font.size(label)[0] + 2 * config.BTN_PAD_X)


def tap_size(px: int) -> int:
    """``px``, raised to ``config.TOUCH_MIN_TARGET`` on a touch build so a control
    that is merely small with a mouse doesn't become un-tappable with a finger."""
    return max(px, config.TOUCH_MIN_TARGET) if config.touch_ui else px


def key_hint(label: str, key: str) -> str:
    """``label`` with its keyboard shortcut appended. Dropped on a touch build:
    there is no key to press there, and the suffix is both noise and the thing
    that pushes these labels out of their buttons at touch scale."""
    return label if config.touch_ui else f"{label} ({key})"


def wrap(font, s: str, width: int) -> list[str]:
    """``s`` broken into lines that each fit ``width`` px in ``font``.

    Paragraphs are separated by a blank line and kept as one blank line in the
    output; line breaks *within* a paragraph are just whitespace, so the source can
    be written at whatever width reads well and still reflow to the real panel.
    """
    lines: list[str] = []
    for para in s.strip().split("\n\n"):
        if lines:
            lines.append("")
        line = ""
        for word in para.split():
            trial = f"{line} {word}" if line else word
            if line and font.size(trial)[0] > width:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
def btn(surface, rect: pygame.Rect, label: str, fill, edge, font=None, color=None) -> tuple[int, int, int, int]:
    """Draw a filled, outlined, centre-labelled button; return its hit-rect tuple
    for storing on the scene's state (the store-rect-then-test handoff input relies
    on). Every button comes through here so they share one look."""
    radius = config.s(6)
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    pygame.draw.rect(surface, edge, rect, config.s(2), border_radius=radius)
    text(surface, font or fonts()["normal"], label, color or config.COLOR_TEXT, center=rect.center)
    return (rect.x, rect.y, rect.w, rect.h)


# Slider track colour, shared by the popup's count slider, the scrubber and the
# map creator's sidebar — kept local like the button palette above.
_SLIDER_TROUGH = (40, 44, 60)


def slider(surface, rect: pygame.Rect, t: float, fill_col, knob_col) -> None:
    """A horizontal slider filling ``rect``: trough, filled portion, round knob at
    fraction ``t``. Shared by the send popup's count slider, history mode's turn
    scrubber and the map creator's sidebar — the same widget at different sizes.

    The knob *travels* over ``rect`` inset by its own radius at each end, so it
    never overhangs the box it sits in — and callers mapping a pointer x back to a
    value must invert exactly that (see ``Ui.set_slider_from_x``), or the knob
    drifts away from the finger at the extremes.
    """
    thick = config.SLIDER_TRACK_H
    knob = config.SLIDER_KNOB_R
    cy = rect.centery
    travel = max(0, rect.w - 2 * knob)
    trough = pygame.Rect(rect.x + knob, cy - thick // 2, travel, thick)
    pygame.draw.rect(surface, _SLIDER_TROUGH, trough, border_radius=max(1, thick // 2))
    fill_w = int(travel * max(0.0, min(1.0, t)))
    if fill_w > 0:
        pygame.draw.rect(surface, fill_col, pygame.Rect(trough.x, trough.y, fill_w, thick), border_radius=max(1, thick // 2))
    hx = trough.x + fill_w
    pygame.draw.circle(surface, knob_col, (hx, cy), knob)
    pygame.draw.circle(surface, fill_col, (hx, cy), knob, config.s(2))


def slider_fraction(rect, px: int) -> float:
    """Where ``px`` falls along ``rect``'s slider, 0..1 — the exact inverse of
    ``slider``'s knob placement, and the one function a caller should use rather
    than re-deriving the inset. ``rect`` is an (x, y, w, h) tuple or a Rect.

    Mirrors ``Ui.set_slider_from_x`` and ``input._seek_scrubber``; a zero-width
    rect (a control not drawn this frame) reads as 0.0 rather than dividing.
    """
    x, _y, w, _h = (rect.x, rect.y, rect.w, rect.h) if isinstance(rect, pygame.Rect) else rect
    travel = w - 2 * config.SLIDER_KNOB_R
    if travel <= 0:
        return 0.0
    return max(0.0, min(1.0, (px - x - config.SLIDER_KNOB_R) / travel))


def step_button(surface, rect: pygame.Rect, sign: str, color) -> None:
    """A small filled -/+ button glyph inside ``rect``."""
    pygame.draw.rect(surface, config.COLOR_BG, rect, border_radius=config.s(4))
    pygame.draw.rect(surface, color, rect, config.s(1), border_radius=config.s(4))
    cx, cy = rect.center
    r = rect.w // 4
    lw = config.s(2)
    pygame.draw.line(surface, color, (cx - r, cy), (cx + r, cy), lw)  # - (and +'s bar)
    if sign == "+":
        pygame.draw.line(surface, color, (cx, cy - r), (cx, cy + r), lw)


def x_button(surface, rect, boxed: bool = False) -> None:
    """A x delete glyph inside ``rect`` (x, y, w, h). ``boxed`` draws a framed
    background so an enlarged (selected-row) delete target reads as a button."""
    rx, ry, rw, rh = rect
    if boxed:
        radius = config.s(4)
        pygame.draw.rect(surface, (60, 40, 46), pygame.Rect(rx, ry, rw, rh), border_radius=radius)
        pygame.draw.rect(surface, (150, 90, 96), pygame.Rect(rx, ry, rw, rh), config.s(1), border_radius=radius)
    pad = max(3, rw // 4)
    lw = max(2, rw // 8)
    col = config.COLOR_TEXT if boxed else config.COLOR_TEXT_DIM
    pygame.draw.line(surface, col, (rx + pad, ry + pad), (rx + rw - pad, ry + rh - pad), lw)
    pygame.draw.line(surface, col, (rx + rw - pad, ry + pad), (rx + pad, ry + rh - pad), lw)


# --------------------------------------------------------------------------- #
# Map-look primitives
#
# What lets a second scene draw a star map that reads as the same game rather than
# an approximation of it, without sharing the drawing functions themselves (those
# are threaded through fog, film and order state a creator has none of).
# --------------------------------------------------------------------------- #
def lane_style(travel_turns: int) -> tuple[int, tuple[int, int, int]]:
    """Width (px) and colour for a lane from its travel time. Fast = thin+bright.

    Thickness tracks travel time directly (clamped) so the spread reads at a
    glance; colour brightens the quick lanes and mutes the slow ones.
    """
    width = config.s(max(2, min(6, travel_turns)))  # 2px (fast) .. 6px (slow)
    f = (min(6, max(1, travel_turns)) - 1) / 5.0  # 0 fast .. 1 slow
    scale = 1.3 - 0.6 * f  # 1.3x (bright) .. 0.7x (dim)
    r, g, b = config.COLOR_LANE
    color = (min(255, int(r * scale)), min(255, int(g * scale)), min(255, int(b * scale)))
    return width, color


def pill_rect(font, s: str, center) -> pygame.Rect:
    """Bounds of the plate ``label_pill`` would draw. Measured separately because
    the star-name pass has to treat these labels as occupied space."""
    rect = pygame.Rect((0, 0), font.size(s))
    rect.center = center
    return rect.inflate(config.s(8), config.s(4))


def label_pill(surface, font, s: str, color, center) -> None:
    """Draw text centred on a small dark rounded rect so it reads over any line."""
    img = font.render(s, True, color)
    rect = img.get_rect(center=center)
    pygame.draw.rect(surface, config.COLOR_BG, pill_rect(font, s, center), border_radius=config.s(5))
    surface.blit(img, rect)


def brighten(color, amount=60):
    return tuple(min(255, c + amount) for c in color)


# --------------------------------------------------------------------------- #
# Modals
# --------------------------------------------------------------------------- #
def modal_buttons(surface, labels: tuple[str, str]) -> tuple[pygame.Rect, pygame.Rect]:
    """The (confirm, cancel) rects for a two-button modal, sized to fit the wider
    of ``labels`` — shared by every modal's drawer and its hit-tester, so the two
    can never disagree about where the buttons are."""
    w, h = surface.get_size()
    font = fonts()["normal"]
    bw = max(btn_w(font, s, config.s(200)) for s in labels)
    bh = max(config.s(42), font.get_height() + config.s(16))
    gap = config.s(12)
    y = h // 2 + config.s(24)
    return (pygame.Rect(w // 2 - bw - gap, y, bw, bh), pygame.Rect(w // 2 + gap, y, bw, bh))


def draw_modal(surface, title: str, detail: str, buttons) -> None:
    """A confirm modal: veil, title, an optional detail line, and two buttons.
    ``buttons`` is ((label, fill, edge), (label, fill, edge)) — confirm then cancel.
    """
    w, h = surface.get_size()
    big, normal = fonts()["big"], fonts()["normal"]
    veil = pygame.Surface((w, h), pygame.SRCALPHA)
    veil.fill((5, 6, 12, 200))
    surface.blit(veil, (0, 0))
    # Stacked upward from the button row so the title and detail always clear it.
    detail_h = row_h() if detail else 0
    y = h // 2 - config.s(12) - detail_h - big.get_height()
    text(surface, big, title, config.COLOR_TEXT, center=(w // 2, y + big.get_height() // 2))
    if detail:
        y += row_h("big")
        text(surface, fonts()["small"], detail, config.COLOR_TEXT_DIM, center=(w // 2, y + fonts()["small"].get_height() // 2))
    for rect, (label, fill, edge) in zip(modal_buttons(surface, (buttons[0][0], buttons[1][0])), buttons):
        btn(surface, rect, label, fill, edge, normal)


def confirm_labels(confirm: str) -> tuple[str, str]:
    """(confirm, cancel) labels for a two-button modal, with the Y/N key hints
    dropped on a touch build. Shared with ``menu``'s resume prompt so every modal
    in the game words its answers the same way."""
    if config.touch_ui:
        return (confirm, "Cancel")
    return (f"{confirm} (Y/Enter)", "Cancel (N/Esc)")
