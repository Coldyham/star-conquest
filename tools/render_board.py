"""Render the leaderboard's pages into a directory a web server can serve.

    uv run python tools/render_board.py [--out web/board]

Each ``leaderboard/<page>.html`` is a Jinja2 template extending
``leaderboard/templates/base.html``, which holds the head, the menu and the
footer's shared links. This renders every page in ``PAGES`` and copies the
static files beside them. ``tools/build_web.sh`` runs it for the deploy; to look
at the board locally, run it and serve the output:

    uv run python tools/render_board.py && cd web/board && python3 -m http.server 8000

``PAGES``, ``STATIC_FILES`` and ``STATIC_DIRS`` are an allow-list of what is
servable, so function source, tests, SQL and the README can never be published
by accident.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "leaderboard"
DEFAULT_OUT = ROOT / "web" / "board"

PAGES = (
    "index.html", "game.html", "submit.html", "user.html", "pbp.html",
    "crowns.html", "campaign.html", "account.html", "privacy.html",
)
STATIC_FILES = ("favicon.png",)
STATIC_DIRS = ("css", "js", "fonts")


def environment() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(BOARD),
        undefined=jinja2.StrictUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(page: str, env: jinja2.Environment | None = None) -> str:
    """One page's finished HTML."""
    return (env or environment()).get_template(page).render()


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    env = environment()
    for page in PAGES:
        (out / page).write_text(render(page, env), encoding="utf-8")
    for name in STATIC_FILES:
        shutil.copy2(BOARD / name, out / name)
    for name in STATIC_DIRS:
        shutil.copytree(BOARD / name, out / name, dirs_exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"where to write the board (default: {DEFAULT_OUT.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    build(args.out)
    print(f"rendered {len(PAGES)} pages into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
