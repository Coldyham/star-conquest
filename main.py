"""Star Conquest launcher: serves the game on this machine and opens it in the
browser (``starconquest/serve.py``).

    uv run python main.py                       # opens the setup menu
    uv run python main.py --players 4 --seed 42 # the menu pre-filled
    uv run python main.py --no-menu --autoplay  # skip the menu (AI plays every seat)

Keep it a shim: anything else belongs in the package. ``--html``, which chose
this build while the pygame one was the default, is still accepted and ignored.
"""

import sys

from starconquest import serve

if __name__ == "__main__":
    serve.main([a for a in sys.argv[1:] if a != "--html"])
