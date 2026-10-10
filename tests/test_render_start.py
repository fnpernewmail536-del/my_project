"""Exercise the real server with Render's inherited arguments and a native DB."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


class RenderStartupTests(unittest.TestCase):
    def test_worker_initializes_database_and_threads_and_uses_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("render_start.py", "gunicorn.conf.py"):
                shutil.copy2(ROOT / name, root / name)
            (root / "wsgi.py").write_text('''
import json
import os
import threading
import libsql

started = os.getpid()
with open("import-pids.txt", "a") as output:
    output.write(str(started) + "\\n")
connection = libsql.connect(database="test-primary.db")
connection.execute("CREATE TABLE IF NOT EXISTS startup (value INTEGER)")
connection.commit()
ready = threading.Event()
def background():
    ready.set()
    threading.Event().wait()
threading.Thread(target=background, daemon=True).start()

def app(environ, start_response):
    row = connection.execute("SELECT 1").fetchone()
    body = json.dumps({"import_pid": started, "pid": os.getpid(),
                       "background_ready": ready.is_set(), "database": row[0]}).encode()
    start_response("200 OK", [("Content-Type", "application/json"),
                             ("Content-Length", str(len(body)))])
    return [body]
''', encoding="utf-8")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = dict(os.environ, PORT=str(port), RENDER="true",
                       GUNICORN_CMD_ARGS="--preload --bind=127.0.0.1:1 --workers=3")
            with (root / "server.log").open("w+") as log:
                process = subprocess.Popen([sys.executable, str(root / "render_start.py")],
                                           cwd=root, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    result = None
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                                result = json.load(response)
                            break
                        except OSError:
                            time.sleep(0.05)
                    log.flush()
                    log.seek(0)
                    output = log.read()
                    self.assertIsNotNone(result, output)
                    self.assertNotEqual(result["import_pid"], process.pid)
                    self.assertEqual(result["import_pid"], result["pid"])
                    self.assertTrue(result["background_ready"])
                    self.assertEqual(result["database"], 1)
                    self.assertEqual((root / "import-pids.txt").read_text().splitlines(),
                                     [str(result["pid"])])
                    self.assertIn(f"0.0.0.0:{port}", output)
                    self.assertIn("Application startup complete", output)
                    self.assertNotIn("Control socket listening", output)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
