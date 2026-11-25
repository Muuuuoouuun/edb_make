"""Run the checked-out app on an unused loopback port with generated test data."""
from functools import partial
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from fixtures import build

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
from preview import Preview, ROOT


class TestHandler(Preview):
    def log_message(self, *_):
        pass


def main():
    handler = partial(TestHandler, directory=str(ROOT))
    with TemporaryDirectory(prefix="png-maker-qa-") as directory:
        build(Path(directory))
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            env = dict(os.environ, PNG_MAKER_TEST_DIR=directory,
                       PNG_MAKER_TEST_URL=f"http://127.0.0.1:{server.server_port}/")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for test in ("browser.cjs", "edge-cases.cjs"):
                    subprocess.run(["node", str(TESTS / test)], env=env, check=True)
                subprocess.run([sys.executable, str(TESTS / "verify.py")], env=env, check=True)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    main()
