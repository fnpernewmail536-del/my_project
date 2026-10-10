"""Render entry point for the project-root Flask application."""
from pathlib import Path
import faulthandler
import sys
import time

ROOT = Path(__file__).resolve().parent
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
_startup_started = time.monotonic()
print("[STARTUP] Loading Flask application in worker", flush=True)
faulthandler.dump_traceback_later(45, repeat=False)
try:
    from main13 import app
finally:
    faulthandler.cancel_dump_traceback_later()
print(f"[STARTUP] Flask application ready in {time.monotonic() - _startup_started:.2f}s", flush=True)

