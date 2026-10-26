"""Shared pytest setup.

The project isn't an installable package (and `mock-microcontroller/` has a
hyphen in its name), so put each source folder on sys.path the same way
start_sim_system.py and the GUIs do.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for folder in (ROOT, ROOT / "mock-microcontroller", ROOT / "operator"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

# Let PySide6 import on CI machines that have no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
