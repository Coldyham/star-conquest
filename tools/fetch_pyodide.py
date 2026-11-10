"""Fetch the pinned Pyodide runtime the HTML build self-hosts.

The game serves Pyodide from its own origin, never a CDN: a school's filter
that blocked pygbag's CDN left the old build stuck on "Loading"
(`docs/design/shell.md`, "The web build's download"). The core release is
downloaded once from GitHub, checked against its published SHA-256, and
unpacked into `.cache/pyodide/<version>/` (gitignored). `starconquest/serve.py`
and `tools/build_next.py` read it from there.

Pinned to the 0.27 line, CPython 3.12: the same minor as the tests and pygbag,
so a stored replay reconstructs with the same `random` and float behaviour.
Moving the pin is a gated change: rerun `tools/spike_bench.py --digests`.

    uv run python tools/fetch_pyodide.py          # no-op when already unpacked
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "0.27.7"
URL = f"https://github.com/pyodide/pyodide/releases/download/{VERSION}/pyodide-core-{VERSION}.tar.bz2"
SHA256 = "9bc8f127db6c590b191b9aee754022cb41b1a36c7bac233776c11c5ecb541be8"

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "pyodide"
DIR = CACHE / VERSION
# What a page needs to boot the core; all must be present for the cache to count.
FILES = ("pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")


def ready() -> bool:
    return all((DIR / name).is_file() for name in FILES)


def fetch() -> Path:
    if ready():
        return DIR
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / f"pyodide-core-{VERSION}.tar.bz2"
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        print(f"downloading {URL}", flush=True)
        part = archive.with_suffix(".part")
        with urllib.request.urlopen(URL, timeout=120) as response, open(part, "wb") as out:
            shutil.copyfileobj(response, out)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        if digest != SHA256:
            part.unlink()
            sys.exit(f"checksum mismatch for {URL}: got {digest}, pinned {SHA256}")
        part.replace(archive)
    staging = CACHE / f"{VERSION}.unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(staging, filter="data")
    inner = staging / "pyodide"
    shutil.rmtree(DIR, ignore_errors=True)
    (inner if inner.is_dir() else staging).replace(DIR)
    shutil.rmtree(staging, ignore_errors=True)
    if not ready():
        sys.exit(f"the archive did not hold {', '.join(FILES)}")
    print(f"pyodide {VERSION} ready in {DIR.relative_to(ROOT)}")
    return DIR


if __name__ == "__main__":
    fetch()
