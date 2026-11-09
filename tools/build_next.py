"""Stage the HTML build: the page, the pinned Pyodide, and the game's Python.

The game's Python ships as one zip the worker unpacks: the core modules and the
bots, never the pygame shell, slimmed the way the pygbag build's archive is
(`slim_web.strip_python`: comments and docstrings out, every line kept). Its
name carries a hash of its bytes, so a cached page can never pair with another
build's Python, and the bytes are deterministic, so an unchanged tree keeps
its name.

    uv run python tools/build_next.py --out web/next    # what build_web.sh runs

`starconquest/serve.py` serves the same tree straight from the sources, building
the zip on request.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_pyodide, slim_web

WEBAPP = ROOT / "webapp"
# The pygame shell, and the local server: never imported in the worker.
SHELL = frozenset({"render", "input", "menu", "widgets", "mapmaker", "main", "uifont",
                   "softkeyboard", "serve"})


def game_files(slim: bool = True) -> list[tuple[str, bytes]]:
    """``(path in the zip, bytes)`` for every file the worker's Python reads."""
    out: list[tuple[str, bytes]] = []
    for path in sorted((ROOT / "starconquest").glob("*.py")):
        if path.stem not in SHELL:
            out.append((f"starconquest/{path.name}", _source(path, slim)))
    for path in sorted((ROOT / "starconquest" / "assets" / "gauntlets").glob("*.json")):
        out.append((f"starconquest/assets/gauntlets/{path.name}", path.read_bytes()))
    for path in sorted((ROOT / "models").glob("*.py")):
        out.append((f"models/{path.name}", _source(path, slim)))
    return out


def _source(path: Path, slim: bool) -> bytes:
    src = path.read_text()
    return (slim_web.strip_python(src) if slim else src).encode()


def game_zip(slim: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in game_files(slim):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def zip_name(data: bytes) -> str:
    return f"starconquest-{hashlib.sha256(data).hexdigest()[:12]}.zip"


def build_info(zip_path: str) -> str:
    """`webapp/js/build.mjs`: which Pyodide and which zip this page boots."""
    return (f'export const PYODIDE = "{fetch_pyodide.VERSION}";\n'
            f'export const GAME_ZIP = "{zip_path}";\n')


def stage(out: Path) -> None:
    pyodide = fetch_pyodide.fetch()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEBAPP, out, ignore=shutil.ignore_patterns("tests"))
    target = out / "pyodide" / fetch_pyodide.VERSION
    target.mkdir(parents=True)
    for name in fetch_pyodide.FILES:
        shutil.copy2(pyodide / name, target / name)
    data = game_zip()
    name = zip_name(data)
    (out / "py").mkdir()
    (out / "py" / name).write_bytes(data)
    (out / "js" / "build.mjs").write_text(build_info(f"py/{name}"))
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"staged {out}: {name} ({len(data) // 1024} KB), {total // 1024} KB in all")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="the directory to stage into (replaced)")
    stage(Path(ap.parse_args().out).resolve())


if __name__ == "__main__":
    main()
