"""Render entry point; keep project packages ahead of data directories."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
for path in (str(ROOT), str(ROOT / "Paython")):
    while path in sys.path:
        sys.path.remove(path)
sys.path[:0] = [str(ROOT), str(ROOT / "Paython")]
from Paython.main13 import app
