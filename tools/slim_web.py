"""Slim the browser build's staged copy of the game before pygbag packs it.

    uv run python tools/slim_web.py <stage>    # in place; tools/build_web.sh runs it

pygbag ships the stage dir (main.py, starconquest/, models/) as one archive that
every visitor downloads before the game can start. Two things made up most of it,
and neither is read by the game:

- **Comments and docstrings.** Half the compressed Python. They are removed with
  every line kept in place (a docstring becomes an empty string spanning the
  same lines), so a traceback from the browser still points at the repository's
  line numbers. Each file is checked: its syntax tree must match the original's
  with docstrings emptied, or the file ships as it was.
- **Glyphs no one sees.** DejaVu Sans Mono covers a dozen scripts and box-drawing
  sets; the game draws Latin and Greek text, star lore in a few transliterations,
  and a handful of symbols. The fonts keep the ranges in ``KEEP_RANGES`` plus
  every character in the shipped source, with their hinting, so what is kept
  renders exactly as before.

The desktop game is untouched: only the stage dir, a scratch copy, is changed.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

FONTS = ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf")

# Inclusive codepoint ranges every font keeps, whatever the source uses: what a
# player might type into a name (Latin, Greek, Cyrillic) and the symbol blocks a
# label is likeliest to borrow from.
KEEP_RANGES = (
    (0x0020, 0x007E),  # Basic Latin
    (0x00A0, 0x024F),  # Latin-1 Supplement, Latin Extended-A and -B
    (0x0250, 0x02FF),  # IPA extensions, spacing modifier letters
    (0x0300, 0x036F),  # combining diacritics
    (0x0370, 0x03FF),  # Greek and Coptic
    (0x0400, 0x04FF),  # Cyrillic
    (0x1E00, 0x1FFF),  # Latin Extended Additional, Greek Extended
    (0x2000, 0x206F),  # General Punctuation
    (0x2070, 0x209F),  # superscripts and subscripts
    (0x20A0, 0x20BF),  # currency
    (0x2190, 0x21FF),  # arrows
)

# Shipped as documentation for bot authors; nothing in the game reads it.
DROP = ("models/README.md",)


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


def keep_codepoints(texts) -> set[int]:
    keep = {c for lo, hi in KEEP_RANGES for c in range(lo, hi + 1)}
    for text in texts:
        keep.update(ord(ch) for ch in text if ord(ch) > 0x7E)
    return keep


def subset_font(path: Path, codepoints: set[int]) -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    options.hinting = True  # FreeType renders hinted outlines; keep them identical
    options.name_IDs = ["*"]  # the licence and copyright strings stay in the file
    options.notdef_outline = True
    options.glyph_names = False
    font = TTFont(str(path))
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    font.save(str(path))


def _size(paths) -> int:
    return sum(p.stat().st_size for p in paths if p.exists())


def slim(stage: Path) -> None:
    sources = sorted(stage.rglob("*.py"))
    texts = [p.read_text(encoding="utf-8") for p in sources]
    # Coverage comes from the source as written, comments included: a superset.
    codepoints = keep_codepoints(texts)

    before = _size(sources)
    kept = 0
    for path, src in zip(sources, texts):
        out = strip_python(src)
        if out is src:
            kept += 1
            print(f"slim_web: WARNING — {path.relative_to(stage)} shipped unstripped")
        path.write_text(out, encoding="utf-8")
    print(f"slim_web: Python {before:,} -> {_size(sources):,} bytes"
          + (f" ({kept} file(s) left as they were)" if kept else ""))

    fonts = [stage / "starconquest" / "assets" / name for name in FONTS]
    before = _size(fonts)
    for font in fonts:
        subset_font(font, codepoints)
    print(f"slim_web: fonts {before:,} -> {_size(fonts):,} bytes")

    for name in DROP:
        (stage / name).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not (Path(argv[0]) / "starconquest").is_dir():
        sys.exit("usage: slim_web.py <stage dir holding main.py, starconquest/ and models/>")
    slim(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
