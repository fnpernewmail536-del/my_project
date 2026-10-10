"""Render entry point for the project-root Flask application."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
from main13 import app
