"""Start Gunicorn without Render's inherited pre-fork application preload."""

import os
from pathlib import Path
import sys


def main():
    # Render supplies --preload in GUNICORN_CMD_ARGS. Loading libSQL and starting
    # application threads in the master can leave unusable state after fork.
    # The repository config is the single source of server settings instead.
    os.environ.pop("GUNICORN_CMD_ARGS", None)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    root = Path(__file__).resolve().parent
    sys.argv = [
        "gunicorn", "--config", str(root / "gunicorn.conf.py"),
        "--chdir", str(root), "wsgi:app",
    ]
    from gunicorn.app.wsgiapp import run

    run()


if __name__ == "__main__":
    main()
