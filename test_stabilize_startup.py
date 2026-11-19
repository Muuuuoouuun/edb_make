"""Startup and lifecycle: macOS reopen launcher, Korean startup dialog, port and folder fallback."""
from __future__ import annotations

import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_server as app


class TestStabilizeStartup(unittest.TestCase):
    def test_macos_plain_open_reopens_running_server_or_starts_one_in_background(self) -> None:
        for healthy in (True, False):
            with self.subTest(healthy=healthy), \
                    mock.patch.object(app, "is_frozen_app", return_value=True), \
                    mock.patch.object(app.sys, "platform", "darwin"), \
                    mock.patch.object(app.sys, "argv", ["ClassInEDBMVP"]), \
                    mock.patch.object(app, "_local_server_is_healthy", return_value=healthy), \
                    mock.patch.object(app.webbrowser, "open") as browser, \
                    mock.patch.object(app.subprocess, "Popen") as popen, \
                    mock.patch.object(app, "run_server") as run_server:
                self.assertEqual(0, app.main())
                run_server.assert_not_called()
                if healthy:
                    browser.assert_called_once_with("http://127.0.0.1:8765/")
                    popen.assert_not_called()
                else:
                    browser.assert_not_called()
                    self.assertEqual([sys.executable, "--open-browser"], popen.call_args.args[0])
                    self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_frozen_startup_failure_shows_korean_dialog_and_logs_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(app, "is_frozen_app", return_value=True), \
                mock.patch.object(app.sys, "platform", "win32"), \
                mock.patch.object(app.sys, "argv", ["ClassInEDBMVP.exe", "--no-open-browser"]), \
                mock.patch.object(app.tempfile, "gettempdir", return_value=tmp), \
                mock.patch.object(app, "ensure_runtime_dirs", side_effect=PermissionError(13, "denied")), \
                mock.patch("ctypes.windll", create=True) as windll:
            self.assertEqual(1, app.main())
            message = windll.user32.MessageBoxW.call_args.args[1]
            self.assertIn("작업 폴더", message)
            self.assertIn("허용한 뒤 다시 열어 주세요", message)
            self.assertNotIn("PermissionError", message)
            log = (Path(tmp) / "ClassInEDBMVP-startup.log").read_text(encoding="utf-8")
            self.assertIn("PermissionError", log)

    def test_port_held_by_another_program_moves_to_next_port(self) -> None:
        server = mock.MagicMock()
        server.shutdown_reason.return_value = "test"

        def bind(address, _handler):
            if address[1] == 8765:
                raise OSError(errno.EADDRINUSE, "in use")
            return server

        with mock.patch.object(app, "ensure_runtime_dirs"), \
                mock.patch.object(app, "hydrate_user_settings_env"), \
                mock.patch.object(app, "write_placeholder_generated_session"), \
                mock.patch.object(app, "_run_startup_maintenance"), \
                mock.patch.object(app, "_local_server_is_healthy", return_value=False) as healthy, \
                mock.patch.object(app, "AppHTTPServer", side_effect=bind) as server_class:
            app.run_server(host="127.0.0.1", port=8765)
        self.assertEqual(("127.0.0.1", 8766), server_class.call_args.args[0])
        # A copy still loading on 8765 gets a long wait before the port is given up.
        self.assertIn(mock.call("127.0.0.1", 8765, timeout=15.0), healthy.call_args_list)
        server.serve_forever.assert_called_once()

    def test_blocked_documents_folder_falls_back_to_per_user_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("not a folder", encoding="utf-8")
            with mock.patch.object(app, "is_frozen_app", return_value=True), \
                    mock.patch.object(app.sys, "platform", "win32"), \
                    mock.patch.dict(app.os.environ, {"EDB_APP_HOME": "", "LOCALAPPDATA": tmp}), \
                    mock.patch.object(app, "default_frozen_app_home", return_value=blocked):
                self.assertEqual((Path(tmp) / "ClassInEDBMVP").resolve(), app.app_root())


if __name__ == "__main__":
    unittest.main()
