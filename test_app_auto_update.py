from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server


class FakeDownloadResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def update_status_for(body: bytes) -> dict:
    url = "https://example.test/ClassInEDBMVP-macOS.zip"
    return {
        "ok": True,
        "platform": "macos",
        "currentVersion": "0.1.0",
        "updateAvailable": True,
        "downloadUrl": url,
        "sha256": hashlib.sha256(body).hexdigest(),
        "sizeBytes": len(body),
        "latest": {
            "version": "0.2.0",
            "downloadUrl": url,
            "fileName": "ClassInEDBMVP-macOS.zip",
            "artifactType": "zip",
            "sha256": hashlib.sha256(body).hexdigest(),
            "sizeBytes": len(body),
        },
    }


class TestAppAutoUpdate(unittest.TestCase):
    def setUp(self) -> None:
        app_server.clear_app_update_status_cache()
        app_server.clear_update_pin_attempts()
        app_server.clear_update_install_state()

    def test_pin_verifier_accepts_only_matching_numeric_pin(self) -> None:
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")

        self.assertTrue(app_server.verify_update_pin("4826", verifier))
        self.assertFalse(app_server.verify_update_pin("4827", verifier))
        self.assertFalse(app_server.verify_update_pin("abcd", verifier))
        self.assertNotIn("4826", verifier)

    def test_pin_attempts_rate_limit_repeated_failures(self) -> None:
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        for _ in range(app_server.UPDATE_PIN_FAILURE_LIMIT):
            with self.assertRaisesRegex(app_server.AppUpdateError, "올바르지"):
                app_server._authorize_update_pin("0000", verifier)

        with self.assertRaises(app_server.AppUpdateError) as blocked:
            app_server._authorize_update_pin("4826", verifier)
        self.assertEqual("update_pin_rate_limited", blocked.exception.code)

    def test_update_status_exposes_capability_but_never_pin_verifier(self) -> None:
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        config = {
            "appId": "ClassInEDBMVP",
            "appName": "ClassInEDBMVP",
            "platform": "macos",
            "version": "0.1.0",
            "updateFeedUrl": "https://example.test/update.json",
            "updatePinHash": verifier,
        }
        body = b"signed update artifact"
        status_fixture = update_status_for(body)
        platform_payload = dict(status_fixture["latest"])
        platform_payload["arch"] = "arm64"
        feed = {
            "appId": "ClassInEDBMVP",
            "appName": "ClassInEDBMVP",
            "platforms": {"macos": platform_payload},
        }
        with patch.object(app_server, "load_app_update_config", return_value=config), \
                patch.object(app_server, "_fetch_update_feed", return_value=feed), \
                patch.object(app_server, "is_frozen_app", return_value=True), \
                patch.object(app_server.platform, "machine", return_value="arm64"):
            status = app_server.build_app_update_status()

        self.assertTrue(status["automaticUpdateEnabled"])
        self.assertTrue(status["automaticUpdateSupported"])
        self.assertTrue(status["automaticUpdateReady"])
        self.assertNotIn("updatePinHash", status)
        self.assertNotIn(verifier, json.dumps(status))

    def test_download_verifies_size_and_sha256_before_promoting_file(self) -> None:
        body = b"verified installer bytes"
        status = update_status_for(body)
        with TemporaryDirectory() as raw_tmp, patch.object(
            app_server,
            "urlopen",
            return_value=FakeDownloadResponse(body, status["downloadUrl"]),
        ):
            artifact = app_server._download_update_artifact(status, runtime_dir=Path(raw_tmp))

            self.assertEqual(body, artifact.read_bytes())
            self.assertEqual("ClassInEDBMVP-macOS.zip", artifact.name)
            self.assertFalse((artifact.parent / f"{artifact.name}.part").exists())

    def test_automatic_update_infers_legacy_artifact_type_from_safe_suffix(self) -> None:
        status = update_status_for(b"verified installer bytes")
        status["latest"].pop("artifactType")

        details = app_server._update_artifact_details(status)

        self.assertEqual("zip", details[2])

    def test_download_rejects_checksum_mismatch_and_removes_partial_file(self) -> None:
        body = b"tampered installer bytes"
        status = update_status_for(body)
        status["sha256"] = "0" * 64
        status["latest"]["sha256"] = "0" * 64
        with TemporaryDirectory() as raw_tmp, patch.object(
            app_server,
            "urlopen",
            return_value=FakeDownloadResponse(body, status["downloadUrl"]),
        ):
            with self.assertRaises(app_server.AppUpdateError) as raised:
                app_server._download_update_artifact(status, runtime_dir=Path(raw_tmp))

            self.assertEqual("update_checksum_mismatch", raised.exception.code)
            self.assertEqual([], list((Path(raw_tmp) / "updates").iterdir()))

    def test_prepare_update_rejects_wrong_pin_before_download(self) -> None:
        body = b"verified installer bytes"
        status = update_status_for(body)
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        with patch.object(app_server, "build_app_update_status", return_value=status), \
                patch.object(app_server, "load_app_update_config", return_value={"updatePinHash": verifier}), \
                patch.object(app_server, "_download_update_artifact") as download:
            with self.assertRaises(app_server.AppUpdateError) as raised:
                app_server.prepare_app_update("0000")

        self.assertEqual("invalid_update_pin", raised.exception.code)
        download.assert_not_called()

    def test_prepare_update_downloads_and_schedules_replacement_for_matching_pin(self) -> None:
        body = b"verified installer bytes"
        status = update_status_for(body)
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        artifact = Path("/tmp/ClassInEDBMVP-macOS.zip")
        with patch.object(app_server, "build_app_update_status", return_value=status), \
                patch.object(app_server, "load_app_update_config", return_value={"updatePinHash": verifier}), \
                patch.object(app_server, "_download_update_artifact", return_value=artifact) as download, \
                patch.object(
                    app_server,
                    "_launch_update_helper",
                    return_value={"platform": "macos", "restartScheduled": True},
                ) as launch:
            result = app_server.prepare_app_update("4826")

        self.assertTrue(result["accepted"])
        self.assertEqual("0.2.0", result["targetVersion"])
        download.assert_called_once_with(status)
        launch.assert_called_once_with(artifact, status, "zip")

    def test_runtime_config_reads_pin_hash_from_environment_without_exposing_plain_pin(self) -> None:
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        with TemporaryDirectory() as raw_tmp, patch.object(app_server, "RESOURCE_DIR", Path(raw_tmp)), \
                patch.object(app_server, "BASE_DIR", Path(raw_tmp)), \
                patch.dict(os.environ, {"EDB_UPDATE_PIN_HASH": verifier}):
            config = app_server.load_app_update_config()

        self.assertEqual(verifier, config["updatePinHash"])

    def test_failed_helper_launch_discards_only_its_update_download(self):
        status = update_status_for(b"installer")
        verifier = app_server.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            pending = runtime / "updates/pending-owned"
            pending.mkdir(parents=True)
            artifact = pending / status["latest"]["fileName"]
            artifact.write_bytes(b"installer")
            other = runtime / "updates/pending-other"
            other.mkdir()
            (other / "keep.exe").write_bytes(b"keep")
            settings = runtime / "user_settings.json"
            settings.write_text("keep")
            with patch.object(app_server, "RUNTIME_DIR", runtime), \
                    patch.object(app_server, "build_app_update_status", return_value=status), \
                    patch.object(app_server, "load_app_update_config", return_value={"updatePinHash": verifier}), \
                    patch.object(app_server, "_download_update_artifact", return_value=artifact), \
                    patch.object(app_server, "_launch_update_helper", side_effect=app_server.AppUpdateError("launch_failed", "launch failed")):
                with self.assertRaises(app_server.AppUpdateError) as error:
                    app_server.prepare_app_update("4826")
            self.assertEqual("launch_failed", error.exception.code)
            self.assertFalse(pending.exists())
            self.assertEqual(b"keep", (other / "keep.exe").read_bytes())
            self.assertEqual("keep", settings.read_text())

    def test_discard_rejects_other_directories_and_symlinked_downloads(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            (runtime / "updates").mkdir(parents=True)
            outside = root / "pending-outside"
            outside.mkdir()
            file = outside / "keep.exe"
            file.write_bytes(b"keep")
            with patch.object(app_server, "RUNTIME_DIR", runtime):
                app_server._discard_update_download(file)
                try:
                    link = runtime / "updates/pending-link"
                    link.symlink_to(outside, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"symlinks unavailable: {exc}")
                app_server._discard_update_download(link / "keep.exe")
            self.assertEqual(b"keep", file.read_bytes())


@unittest.skipUnless(shutil.which("zsh"), "zsh is unavailable")
class TestMacUpdateReplacement(unittest.TestCase):
    def _run_helper(self, root: Path, *, bad_signature=False, fail_move=""):
        source = root / "source/Test.app"
        source.mkdir(parents=True)
        (source / "version").write_text("new")
        target = root / "Applications/Test.app"
        target.mkdir(parents=True)
        (target / "version").write_text("old")
        artifact = root / "pending-test/update.zip"
        artifact.parent.mkdir()
        artifact.write_bytes(b"zip fixture")
        commands = {
            "/usr/bin/ditto": '#!/bin/sh\nif [ "$1" = "-x" ]; then mkdir -p "$4"; cp -R "$EDB_TEST_SOURCE" "$4/"; else cp -R "$1" "$2"; fi\n',
            "/usr/bin/codesign": f"#!/bin/sh\nexit {41 if bad_signature else 0}\n",
            "/usr/bin/open": '#!/bin/sh\nexit 0\n',
            "/bin/mv": '#!/bin/sh\ncase "$1" in *"' + (fail_move or "never-match-this-path") + '"*) exit 55;; esac\nexec /bin/mv "$@"\n',
        }
        script = app_server.MACOS_UPDATE_HELPER
        for index, (command, content) in enumerate(commands.items()):
            stub = root / f"stub-{index}"
            stub.write_text(content)
            stub.chmod(0o700)
            script = script.replace(command, f'"{stub}"')
        helper = root / "helper.zsh"
        helper.write_text(script)
        result = subprocess.run(
            [shutil.which("zsh"), str(helper), "99999999", str(artifact), "zip", str(target), str(root / "update.log")],
            env={**os.environ, "EDB_TEST_SOURCE": str(source)}, capture_output=True, text=True, timeout=10,
        )
        return result, target, artifact

    def test_signature_failure_keeps_old_app_and_does_not_replace_it(self):
        with TemporaryDirectory(prefix="edb update ") as temp:
            result, target, artifact = self._run_helper(Path(temp), bad_signature=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("old", (target / "version").read_text())
            self.assertFalse(artifact.exists())
            self.assertEqual([], list(target.parent.glob(".*.backup.*")))

    def test_backup_failure_never_installs_new_app(self):
        with TemporaryDirectory() as temp:
            result, target, artifact = self._run_helper(Path(temp), fail_move="Applications/Test.app")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("old", (target / "version").read_text())
            self.assertFalse(artifact.exists())

    def test_replacement_failure_restores_previous_app(self):
        with TemporaryDirectory() as temp:
            result, target, artifact = self._run_helper(Path(temp), fail_move=".updating.")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("old", (target / "version").read_text())
            self.assertFalse(artifact.exists())

    def test_success_replaces_old_app_and_removes_installer_and_backup(self):
        with TemporaryDirectory(prefix="edb update ") as temp:
            result, target, artifact = self._run_helper(Path(temp))
            self.assertEqual(0, result.returncode, (Path(temp) / "update.log").read_text())
            self.assertEqual("new", (target / "version").read_text())
            self.assertFalse(artifact.exists())
            self.assertEqual(["Test.app"], [p.name for p in target.parent.iterdir()])


@unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell is unavailable")
class TestWindowsUpdateReplacement(unittest.TestCase):
    def _run_helper(self, root: Path, *, parent_pid=99999999, exit_code=0, short_wait=False):
        installer = root / "Setup.exe"
        installer.write_bytes(b"installer fixture")
        executable = root / "App.exe"
        executable.write_bytes(b"existing app")
        marker = root / "started.txt"
        helper = root / "helper.ps1"
        source = app_server.WINDOWS_UPDATE_HELPER
        if short_wait:
            source = source.replace("WaitForExit(120000)", "WaitForExit(25)")
        helper.write_text(source, encoding="utf-8-sig")

        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"

        command = f'''
function Start-Process {{
  param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru)
  Add-Content -LiteralPath {quote(marker)} -Value $FilePath
  if ($PassThru) {{ return [pscustomobject]@{{ ExitCode = {exit_code} }} }}
}}
& {quote(helper)} -ParentPid {parent_pid} -Installer {quote(installer)} -AppExecutable {quote(executable)} -InstallDir {quote(root)} -LogFile {quote(root / 'update.log')}
'''
        completed = subprocess.run(
            [shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=15,
        )
        return completed, installer, executable, marker

    def test_wait_timeout_preserves_app_and_never_starts_installer(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with TemporaryDirectory(prefix="edb update ") as temp:
                result, installer, executable, marker = self._run_helper(Path(temp), parent_pid=sleeper.pid, short_wait=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("update cancelled", result.stderr)
                self.assertFalse(installer.exists())
                self.assertEqual(b"existing app", executable.read_bytes())
                self.assertFalse(marker.exists())
                self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_installer_failure_cleans_download_without_relaunching(self):
        with TemporaryDirectory() as temp:
            result, installer, executable, marker = self._run_helper(Path(temp), exit_code=42)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(installer.exists())
            self.assertEqual([str(installer)], marker.read_text().splitlines())
            self.assertEqual(b"existing app", executable.read_bytes())

    def test_success_cleans_download_and_relaunches_app(self):
        with TemporaryDirectory(prefix="edb update ") as temp:
            result, installer, executable, marker = self._run_helper(Path(temp))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(installer.exists())
            self.assertEqual([str(installer), str(executable)], marker.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
