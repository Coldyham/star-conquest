"""Star Conquest — a minimalist sci-fi turn-based strategy game.

The package is split into a pure, pygame-free simulation core
(``model``, ``mapgen``, ``combat``, ``engine``, ``ai``) and a thin presentation
shell (``render``, ``input``, ``main``). The core advances a ``GameState``
deterministically and never imports pygame, so the whole game is headlessly
testable and reproducible.
"""

__version__ = "0.1.0"
