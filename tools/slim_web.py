"""Strip comments and docstrings from the game's Python before it ships.

Every visitor downloads the game's Python (`tools/build_next.py`'s zip) before
the game can start, and comments and docstrings were half of it compressed,
with nothing reading them. They are removed with every line kept in place (a
docstring becomes an empty string spanning the same lines), so a traceback
from the browser still points at the repository's line numbers. Each file is
checked: its syntax tree must match the original's with docstrings emptied, or
the file ships as it was.
"""

from __future__ import annotations

import ast
import io
import tokenize


def _docstrings(tree: ast.AST) -> list[ast.Constant]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                found.append(first.value)
    return found


def _shape(src: str) -> str:
    """The source's syntax tree with every docstring emptied, as text."""
    tree = ast.parse(src)
    for doc in _docstrings(tree):
        doc.value = ""
    return ast.dump(tree)


def strip_python(src: str) -> str:
    """``src`` without comments or docstrings, every line where it was.

    Returns ``src`` itself if the result would not be the same program."""
    try:
        starts = {(d.lineno, d.col_offset) for d in _docstrings(ast.parse(src))}
        tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and tok.start in starts:
                # An empty string continued across the docstring's lines.
                tok = tok._replace(string='"' + "\\\n" * tok.string.count("\n") + '"')
            tokens.append(tok)
        out = tokenize.untokenize(tokens)
        if out.count("\n") != src.count("\n") or _shape(out) != _shape(src):
            return src
        return out
    except (SyntaxError, tokenize.TokenError, ValueError):
        return src
