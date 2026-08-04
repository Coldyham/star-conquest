"""The mobile browser's on-screen keyboard, for the menu's text fields.

A browser only raises the soft keyboard for a *focused DOM text field*. SDL's
``pygame.key.start_text_input()`` — which is what summons the IME in the native
Android build — has nothing to focus in the web build, so tapping the seed or
save-file field there would pop up no keyboard at all. This module parks an
invisible one-pixel ``<input>`` over the canvas, focuses it when a field starts
editing, and hands back what was typed each frame by reading its ``value``.

Reading the value (rather than listening for key events) is what makes swipe
typing, autocorrect and the keyboard's own backspace work: none of those produce
usable key events, but all of them update the field. Loss of focus is the touch
equivalent of Enter — it is what the keyboard's Done/Go key does.

No pygame and no simulation state: just pygbag's ``platform.window`` JS bridge,
guarded exactly like ``menu._share_link`` — off the web build, on a desktop
browser, or if any DOM call fails, every function here is an inert no-op and the
caller keeps its existing SDL behaviour.
"""

from __future__ import annotations

from .paths import is_web

_ELEMENT_ID = "sc-soft-keyboard"

# The field is real and focusable but visually absent: `display:none` /
# `visibility:hidden` elements cannot take focus (and so cannot raise the
# keyboard). 16px text is the threshold below which mobile Safari zooms the page
# on focus. Parked bottom-left under the canvas, where the keyboard covers it.
_STYLE = ("position:fixed;left:0;bottom:0;width:1px;height:1px;opacity:0;"
          "border:0;padding:0;margin:0;font-size:16px;background:transparent;"
          "color:transparent;caret-color:transparent;z-index:-1;")

_field = None        # the <input> proxy, created on first use and kept for good
_open = False        # is it currently focused / are we reading from it?
_took_focus = False  # has the field actually held focus since `open`?


def is_touch_web() -> bool:
    """True on a touch-capable browser (phone/tablet). Desktop browsers report no
    touch points, so they keep the plain SDL keyboard path (and compact UI)."""
    if not is_web():
        return False
    import platform as _platform

    try:
        return int(_platform.window.navigator.maxTouchPoints) > 0
    except Exception:
        return False


def _element():
    """The hidden input, created and appended on first use; None if unavailable."""
    global _field
    if _field is not None:
        return _field
    if not is_touch_web():
        return None
    import platform as _platform

    try:
        doc = _platform.window.document
        el = doc.createElement("input")
        el.setAttribute("id", _ELEMENT_ID)
        el.setAttribute("type", "text")
        el.setAttribute("autocomplete", "off")
        el.setAttribute("autocorrect", "off")
        el.setAttribute("autocapitalize", "off")
        el.setAttribute("spellcheck", "false")
        el.setAttribute("enterkeyhint", "done")
        el.setAttribute("style", _STYLE)
        doc.body.appendChild(el)
    except Exception:
        return None
    _field = el
    return _field


def open(text: str) -> None:
    """Focus the hidden field, primed with ``text``, raising the keyboard.

    Must be called while handling the tap that opened the editor: browsers only
    show the keyboard for a ``focus()`` that happens under a recent user gesture.
    """
    global _open, _took_focus
    _took_focus = False
    el = _element()
    if el is None:
        return
    try:
        el.value = text
        el.focus()
        el.setSelectionRange(len(text), len(text))   # caret at the end
        _open = True
    except Exception:
        _open = False


def close() -> None:
    """Drop focus, dismissing the keyboard. Never creates the field."""
    global _open
    _open = False
    if _field is None:
        return
    try:
        _field.blur()
    except Exception:
        pass


def value(fallback: str) -> str:
    """What the field currently holds, or ``fallback`` when there is no field."""
    if not _open or _field is None:
        return fallback
    try:
        return str(_field.value)
    except Exception:
        return fallback


def set_value(text: str) -> None:
    """Write ``text`` back — used when the caller filters out characters it
    doesn't accept, so the DOM field and the edit buffer stay in step."""
    if not _open or _field is None:
        return
    try:
        _field.value = text
        _field.setSelectionRange(len(text), len(text))
    except Exception:
        pass


def dismissed() -> bool:
    """True once the browser has taken focus off the field — the Done/Go key, or
    the player dismissing the keyboard. The touch equivalent of pressing Enter.

    Only ever true after the field has actually *held* focus, so a ``focus()``
    the browser quietly refused doesn't read as an instant dismissal and shut the
    editor a frame after it opened."""
    global _took_focus
    if not _open or _field is None:
        return False
    import platform as _platform

    try:
        focused = str(_platform.window.document.activeElement.id) == _ELEMENT_ID
    except Exception:
        return False
    _took_focus = _took_focus or focused
    return _took_focus and not focused
