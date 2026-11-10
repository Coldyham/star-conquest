"""The page that a Web Worker's Python answers to.

In the HTML build the core runs in Pyodide inside a Web Worker, which has no
``localStorage``, no clipboard, no address bar and no way to evaluate JS in the
page (``docs/design/html-frontend.md``). So Python does no I/O there: what
the page told it at boot is kept here (its address and a mirror of its
storage), and everything it wants done is queued as an *effect*, a plain dict
the page carries out after each command. A request's answer comes back as a
command too (``reply``), and lands in the mailbox the caller polls, exactly as a
pygbag fetch or a desktop thread's would.

Under pygbag and on the desktop none of this is used: ``in_worker`` is False and
every bridge keeps its own path. Imports nothing from the package, so any
module may import it.
"""

from __future__ import annotations

from collections.abc import Callable

# The effects the page knows how to carry out. Anything else is a bug here.
EFFECTS = frozenset({
    "http",       # {req, url, body, signed}: fetch, then answer with `reply`
    "fire",       # {url, body}: a POST nobody waits on (a replay upload)
    "bridge",     # {req, module, fn, args}: call an allow-listed export of a board module
    "store.set",  # {key, value}: localStorage.setItem
    "clipboard",  # {text}
    "navigate",   # {url}: a same-tab navigation
    "fragment",   # {token}: history.replaceState to "#token"
    "download",   # {name, text, type}: hand the browser a file to save (`files.Download`)
    "close",      # {}: ask the browser to close this window (a confirmed quit)
})

_worker = False
_page: dict[str, str] = {}
_store: dict[str, str] = {}
_outbox: list[dict] = []
_waiting: dict[int, Callable[[str, str], None]] = {}
_next_req = 1


def in_worker() -> bool:
    """Whether this is the HTML build's worker, where the page does all I/O."""
    return _worker


def boot(page: dict, store: dict) -> None:
    """Start answering to a page: its location (``origin``, ``hostname``,
    ``pathname``, ``hash``) and the ``sc_*`` keys of its localStorage."""
    global _worker, _page, _store
    _worker = True
    _page = {k: str(page.get(k) or "") for k in ("origin", "hostname", "pathname", "hash")}
    _store = {str(k): str(v) for k, v in (store or {}).items()}


def reset() -> None:
    """Back to answering to nobody (for tests)."""
    global _worker, _page, _store, _next_req
    _worker = False
    _page, _store = {}, {}
    _outbox.clear()
    _waiting.clear()
    _next_req = 1


def page(key: str) -> str:
    """One part of the page's location, ``""`` when unknown."""
    return _page.get(key, "")


def set_hash(fragment: str) -> None:
    """The page's ``#fragment`` changed (a link opened in an installed app)."""
    _page["hash"] = fragment


def store_get(key: str) -> str:
    return _store.get(key, "")


def store_set(key: str, value: str) -> None:
    """Write through: the mirror now, the page's storage after this command."""
    _store[key] = value
    emit("store.set", key=key, value=value)


def store_changed(key: str, value: str | None) -> None:
    """Another tab wrote the page's storage; keep the mirror in step."""
    if value is None:
        _store.pop(key, None)
    else:
        _store[key] = value


def emit(kind: str, **fields) -> None:
    assert kind in EFFECTS, kind
    _outbox.append({"t": kind, **fields})


def drain() -> list[dict]:
    """Every effect queued since the last drain, in order."""
    out = list(_outbox)
    _outbox.clear()
    return out


def _wait(deliver: Callable[[str, str], None]) -> int:
    global _next_req
    req = _next_req
    _next_req += 1
    _waiting[req] = deliver
    return req


def request(url: str, body: str | None, signed: bool, deliver: Callable[[str, str], None]) -> int:
    """Ask the page to fetch ``url`` (a POST of ``body`` when given), and hand
    ``deliver`` its ``(state, body)`` when it answers. ``signed`` adds the
    board's sign-in session, read in the page so the token never reaches Python."""
    req = _wait(deliver)
    emit("http", req=req, url=url, body=body, signed=signed)
    return req


def bridge(module: str, fn: str, args: list, deliver: Callable[[str, str], None]) -> int:
    """Ask the page to call ``fn`` of the board module at ``module`` and hand
    ``deliver`` how its promise settled (``ok`` with its string, else ``error``)."""
    req = _wait(deliver)
    emit("bridge", req=req, module=module, fn=fn, args=list(args))
    return req


def reply(req: int, state: str, body: str) -> bool:
    """The page answered request ``req``. False when nobody is waiting on it
    (answered twice, or after the waiter was reset)."""
    deliver = _waiting.pop(req, None)
    if deliver is None:
        return False
    deliver(state, body)
    return True
