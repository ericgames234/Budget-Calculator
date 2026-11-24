"""Test package.

Adds ``src/`` to the import path, so ``python -m unittest`` works from the
project root without installing anything. If you installed the package with
``pip install -e .``, this has no effect.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DATA = ROOT / "data"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
