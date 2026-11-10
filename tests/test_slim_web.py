"""The browser build's slimming step (tools/slim_web.py).

The game zip ships the game without comments or docstrings. Each guard here is
one a broken slim would only show in the browser: a program that no longer
means the same thing, or tracebacks off by some lines.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools import slim_web

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = sorted([*(ROOT / "starconquest").rglob("*.py"), *(ROOT / "models").rglob("*.py")])


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_shipped_file_strips_to_the_same_program_on_the_same_lines(path):
    src = path.read_text(encoding="utf-8")
    out = slim_web.strip_python(src)
    assert out is not src, "stripping fell back to the original"
    assert out.count("\n") == src.count("\n")
    assert slim_web._shape(out) == slim_web._shape(src)
    # Every statement starts on the line it started on.
    lines = lambda text: [getattr(n, "lineno", None) for n in ast.walk(ast.parse(text))]
    assert lines(out) == lines(src)


def test_stripping_removes_comments_and_docstrings_but_not_strings():
    src = (
        '"""Module doc."""\n'
        "# a comment\n"
        "def f():\n"
        '    """Two\n    lines."""\n'
        '    return "# not a comment"  # trailing\n'
        "class C:\n"
        "    '''Doc.'''\n"
        "    x = f'{1}#{2}'\n"
    )
    out = slim_web.strip_python(src)
    assert "comment\n" not in out and "trailing" not in out
    assert "Module doc" not in out and "lines." not in out and "Doc." not in out
    assert '"# not a comment"' in out
    namespace: dict = {}
    exec(compile(out, "x", "exec"), namespace)  # noqa: S102 - runs the stripped source to prove it still works
    assert namespace["f"]() == "# not a comment"
    assert namespace["C"].x == "1#2"
    assert out.count("\n") == src.count("\n")


def test_a_file_that_would_change_meaning_ships_as_written():
    src = "x = (\n"  # does not parse: left exactly as it was
    assert slim_web.strip_python(src) is src


