"""Star Conquest launcher. The game loop lives in ``starconquest/main.py``.

    uv run python main.py                       # opens the setup menu
    uv run python main.py --no-menu --autoplay  # skip the menu (scriptable demo)

This file stays at the root because ``tools/build_web.sh`` hands pygbag a
``main.py`` at the top of its stage dir, and because it is the documented way to
launch. Keep it a shim: anything else belongs in the package.
"""

from starconquest import main

if __name__ == "__main__":
    main.run()
