"""Star Conquest — a minimalist sci-fi turn-based strategy game.

Pure Python with no display of its own: the simulation (``model``, ``mapgen``,
``combat``, ``engine``, ``ai``) advances a ``GameState`` deterministically, and
``session`` answers the browser page that draws it (``webapp/``), so the whole
game is headlessly testable and reproducible.
"""

__version__ = "0.1.0"
