"""tools/mirror_runtime.py: a build either serves the whole runtime from our own
origin or is left exactly as pygbag made it, never half of each."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import mirror_runtime as mr

VER = "0.9.3"
PATHS = [
    f"{VER}/cpython312/main.data", f"{VER}/cpython312/main.js", f"{VER}/cpython312/main.wasm",
    f"{VER}/cpythonrc.py", f"{VER}/pythons.js",
    "cp312/pygame_ce-2.5.7-cp312-cp312-wasm32_bi_emscripten.whl", f"index-{VER}-cp312.json",
    "vtx.js",
]
PAGE = f'<script src="{mr.REMOTE}{VER}/pythons.js" data-src="x"></script>'
RC = "import os\n" + mr.RC_ANCHOR
INDEX = '{"-CDN-": "' + mr.REMOTE + '"}'


def _body(rel: str) -> bytes:
    if rel.endswith("cpythonrc.py"):
        return RC.encode()
    if rel.startswith("index-"):
        return INDEX.encode()
    return f"<{rel}>".encode()


@pytest.fixture
def web(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "play").mkdir()
    (tmp_path / "play" / "index.html").write_text(PAGE)
    listing = tmp_path / "runtime_files.txt"
    listing.write_text("# header\n" + "\n".join(PATHS) + "\n")
    monkeypatch.setattr(mr, "LIST", listing)
    return tmp_path


def _untouched(web: Path) -> bool:
    return (web / "play" / "index.html").read_text() == PAGE and not (web / "cdn").exists()


def test_the_shipped_list_is_whole_and_names_the_installed_pygbag():
    import importlib.metadata
    ver = importlib.metadata.version("pygbag")
    assert mr.list_problem(mr.read_list(), f"{mr.REMOTE}{ver}/pythons.js") is None


def test_a_good_mirror_points_page_rc_and_index_at_our_origin(web):
    assert mr.fetch(web, download=_body) == 0
    assert (web / "play" / "index.html").read_text() == PAGE.replace(mr.REMOTE, mr.LOCAL)
    assert mr.RC_LINE + mr.RC_ANCHOR in (web / "cdn" / VER / "cpythonrc.py").read_text()
    assert (web / "cdn" / f"index-{VER}-cp312.json").read_text() == INDEX.replace(mr.REMOTE, mr.LOCAL)
    assert (web / "cdn" / "vtx.js").read_bytes() == b"<vtx.js>"


def test_a_file_the_build_already_mirrored_is_not_fetched_again(web):
    wheel = web / "cdn" / PATHS[5]
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"from curl")
    asked = []
    mr.fetch(web, download=lambda rel: asked.append(rel) or _body(rel))
    assert PATHS[5] not in asked and wheel.read_bytes() == b"from curl"


@pytest.mark.parametrize("error", [OSError("refused"), ValueError("IncompleteRead stand-in")])
def test_any_failed_download_writes_nothing_and_never_fails_the_build(web, error):
    def flaky(rel: str) -> bytes:
        if rel.endswith("main.wasm"):
            raise error
        return _body(rel)
    assert mr.fetch(web, download=flaky) == 0
    assert _untouched(web)


def test_a_list_for_another_pygbag_writes_nothing(web):
    (web / "play" / "index.html").write_text(PAGE.replace(VER, "0.9.4"))
    mr.fetch(web, download=_body)
    assert "0.9.4" in (web / "play" / "index.html").read_text()
    assert mr.REMOTE in (web / "play" / "index.html").read_text()
    assert not (web / "cdn").exists()


def test_a_recording_cut_short_writes_nothing(web):
    mr.LIST.write_text("\n".join(p for p in PATHS if not p.endswith(".whl")))
    mr.fetch(web, download=_body)
    assert _untouched(web)


def test_a_moved_anchor_writes_nothing_not_even_the_index(web):
    def moved(rel: str) -> bytes:
        return b"import os\n" if rel.endswith("cpythonrc.py") else _body(rel)
    mr.fetch(web, download=moved)
    assert _untouched(web)
