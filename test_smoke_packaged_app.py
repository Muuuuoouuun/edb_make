from __future__ import annotations

import os
import hashlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.smoke_packaged_app import _ProcessTreeGuard, _smoke_ui_stylesheets, _validate_ui_stylesheet, _isolated_smoke_environment
from scripts.verify_frontend_package import EXTRA_UI_STYLESHEETS


class TestPackagedStylesheetSmoke(unittest.TestCase):
    @staticmethod
    def fixture() -> tuple[str, dict[str, bytes], dict[str, str]]:
        links: list[str] = []
        requests: dict[str, bytes] = {}
        expected: dict[str, str] = {}
        for filename in ("board.css", *EXTRA_UI_STYLESHEETS):
            content = b":root{ --font-sans: system-ui; }" if filename == "board.css" else b".surface{ display: block; }"
            digest = hashlib.sha256(content).hexdigest()
            prefix = "board-css-" if filename == "board.css" else ""
            href = f"{filename}?v={prefix}{digest}"
            links.append(f'<link rel="stylesheet" href="{href}">')
            requests[f"http://127.0.0.1:9000/{href}"] = content
            expected[filename] = digest
        return "\n".join(links), requests, expected

    def test_http_smoke_requests_and_validates_every_surface_stylesheet(self) -> None:
        board, requests, expected = self.fixture()
        with patch("scripts.smoke_packaged_app._bytes_request", side_effect=requests.__getitem__) as request:
            self.assertEqual(expected, _smoke_ui_stylesheets("http://127.0.0.1:9000", board))
        self.assertEqual(len(expected), request.call_count)
        self.assertEqual(set(requests), {call.args[0] for call in request.call_args_list})

    def test_http_smoke_rejects_stale_or_non_utf8_surface_content(self) -> None:
        for filename in EXTRA_UI_STYLESHEETS:
            for content, message in ((b".surface{ display: grid; }", "does not match"), (b"\xff", "not valid UTF-8")):
                with self.subTest(stylesheet=filename, message=message):
                    board, requests, _ = self.fixture()
                    key = next(url for url in requests if f"/{filename}?" in url)
                    requests[key] = content
                    with patch("scripts.smoke_packaged_app._bytes_request", side_effect=requests.__getitem__):
                        with self.assertRaisesRegex(RuntimeError, message):
                            _smoke_ui_stylesheets("http://127.0.0.1:9000", board)

    def test_surface_smoke_rejects_missing_and_duplicate_links(self) -> None:
        board, _, expected = self.fixture()
        for filename in EXTRA_UI_STYLESHEETS:
            with self.subTest(stylesheet=filename):
                link = f'<link rel="stylesheet" href="{filename}?v={expected[filename]}">'
                for invalid in (board.replace(link, ""), board + link):
                    with self.assertRaisesRegex(RuntimeError, "exactly one"):
                        _validate_ui_stylesheet(invalid, ".surface{ display: block; }", filename)


class TestPackagedSmokeProcessTree(unittest.TestCase):
    def test_direct_script_loads_edb_parser_without_repository_on_pythonpath(self) -> None:
        script = Path(__file__).resolve().parent / "scripts" / "smoke_packaged_app.py"
        with tempfile.TemporaryDirectory() as unrelated_cwd:
            result = subprocess.run(
                [sys.executable, "-I", "-c",
                 "import runpy, sys; ns = runpy.run_path(sys.argv[1]); "
                 "assert callable(ns['parse_edb'])", str(script)],
                cwd=unrelated_cwd, capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_windows_environment_case_does_not_break_python_free_launch(self) -> None:
        for key in ("SystemRoot", "SYSTEMROOT", "systemroot"):
            with self.subTest(key=key), patch.dict(os.environ, {
                key: "C:/Windows", "PATH": "C:/Python311", "VIRTUAL_ENV": "C:/venv",
                "PYTHONPATH": "C:/dev", "ANTHROPIC_API_KEY": "test-placeholder",
            }, clear=True):
                environment = _isolated_smoke_environment(Path("app-home"), Path("empty"), windows=True)
                self.assertEqual("C:/Windows", environment["SYSTEMROOT"])
                self.assertNotIn("Python311", environment["PATH"])
                self.assertIn("System32", environment["PATH"])
                for name in ("VIRTUAL_ENV", "PYTHONPATH", "ANTHROPIC_API_KEY"):
                    self.assertNotIn(name, environment)

    def test_unicode_space_cwd_and_clean_process_exit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edb-smoke-test-") as raw_temp:
            launch_dir = Path(raw_temp) / "한글 경로 with spaces" / "실행 위치"
            launch_dir.mkdir(parents=True)
            log_path = launch_dir / "프로세스 log.bin"
            with log_path.open("wb") as log_handle:
                tree = _ProcessTreeGuard.launch(
                    [
                        sys.executable,
                        "-c",
                        (
                            "from pathlib import Path; "
                            "Path('실행됨.txt').write_text('ok', encoding='utf-8')"
                        ),
                    ],
                    cwd=launch_dir,
                    env=dict(os.environ),
                    stdout=log_handle,
                )
                try:
                    self.assertEqual(0, tree.process.wait(timeout=10))
                    self.assertTrue(tree.wait_for_empty(2))
                finally:
                    tree.terminate(timeout=2)
                    tree.close()

            self.assertEqual("ok", (launch_dir / "실행됨.txt").read_text(encoding="utf-8"))

    def test_lingering_descendant_is_detected_and_terminated(self) -> None:
        child_code = "import time; time.sleep(30)"
        parent_code = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "time.sleep(0.3)"
        )
        with tempfile.TemporaryDirectory(prefix="edb-process-tree-") as raw_temp:
            launch_dir = Path(raw_temp)
            log_path = launch_dir / "tree.log"
            with log_path.open("wb") as log_handle:
                tree = _ProcessTreeGuard.launch(
                    [sys.executable, "-c", parent_code],
                    cwd=launch_dir,
                    env=dict(os.environ),
                    stdout=log_handle,
                )
                try:
                    self.assertEqual(0, tree.process.wait(timeout=10))
                    self.assertFalse(
                        tree.wait_for_empty(0.2),
                        "the guard must not mistake parent exit for whole-tree exit",
                    )
                    tree.terminate(timeout=3)
                    self.assertTrue(tree.wait_for_empty(2))
                finally:
                    tree.terminate(timeout=3)
                    tree.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior is Windows-specific")
    def test_windows_job_close_terminates_assigned_process(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edb-job-test-") as raw_temp:
            launch_dir = Path(raw_temp)
            with (launch_dir / "job.log").open("wb") as log_handle:
                tree = _ProcessTreeGuard.launch(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    cwd=launch_dir,
                    env=dict(os.environ),
                    stdout=log_handle,
                )
                job_closed = False
                try:
                    self.assertIsNotNone(tree._windows_job)
                    self.assertGreaterEqual(tree._windows_job.active_process_count(), 1)
                    tree.close()
                    job_closed = True
                    tree.process.wait(timeout=3)
                finally:
                    if not job_closed:
                        tree.terminate(timeout=3)
                        tree.close()
            self.assertIsNotNone(tree.process.poll())


if __name__ == "__main__":
    unittest.main()
