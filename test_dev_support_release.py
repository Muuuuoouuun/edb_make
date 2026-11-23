"""Support and release guards: richer bug reports, staging gates, macOS minimum version."""
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import Mock, patch

import app_server
import bug_reporting
import test_packaging_frontend_manifest as packaging_fixtures
from scripts import stage_draft_release as stage
from scripts.verify_packaged_app import collect_package_errors

ROOT = Path(__file__).resolve().parent
WORKFLOW = (ROOT / ".github" / "workflows" / "build-installers.yml").read_text(encoding="utf-8")
APP_CONFIG = {"appId": "ClassInEDBMVP", "version": "0.2.15", "platform": "windows"}


def _report(tmp: Path, *, include=True, extra_logs=None, extra=None, app_log=""):
    log_file = tmp / "app.log"
    log_file.write_text(app_log, encoding="utf-8")
    return bug_reporting.build_bug_report(
        {"description": "업데이트가 끝나지 않습니다.", "includeDiagnostics": include,
         "context": {"view": "home", "arch": "spoofed", "lastUpdateFailed": True}},
        app_config=APP_CONFIG, log_file=log_file, extra_logs=extra_logs, extra_diagnostics=extra,
    )


class BugReportExtraDiagnosticsTests(unittest.TestCase):
    def test_update_and_startup_logs_ride_along_redacted_with_flags(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            helper = tmp / "update-helper.log"
            helper.write_text(
                "Installer exited with 1\napi_key=AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ1234\n"
                f"{Path.home()}\\AppData\\Local\\ClassInEDBMVP\\updates\\pending-1\\Setup.exe\n",
                encoding="utf-8",
            )
            # Inno and PowerShell logs can be UTF-16; Hangul must survive.
            install = tmp / "update-install.log"
            install.write_bytes("﻿설치 실패: 파일 사용 중\n".encode("utf-16-le"))
            report = _report(
                tmp,
                extra_logs={"updateHelper": helper, "updateInstall": install, "startup": tmp / "missing.log",
                            "notAllowed": helper},
                extra={"arch": "arm64", "dataFolderFallback": 1, "lastUpdateFailed": True, "other": "x"},
            )
        diagnostics = report["diagnostics"]
        self.assertEqual({"updateHelper", "updateInstall"}, set(diagnostics["extraLogs"]))
        self.assertIn("Installer exited with 1", diagnostics["extraLogs"]["updateHelper"])
        self.assertIn("설치 실패", diagnostics["extraLogs"]["updateInstall"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("AIza", serialized)
        self.assertNotIn(str(Path.home()), serialized)
        self.assertEqual("arm64", diagnostics["arch"])
        self.assertIs(True, diagnostics["dataFolderFallback"])
        self.assertIs(True, diagnostics["lastUpdateFailed"])
        self.assertNotIn("other", diagnostics)
        # Server facts never come from the client-controlled context.
        self.assertNotIn("arch", report["context"])
        self.assertNotIn("lastUpdateFailed", report["context"])

    def test_missing_logs_add_no_key_and_opt_out_sends_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            helper = tmp / "update-helper.log"
            helper.write_text("helper ran", encoding="utf-8")
            missing = _report(tmp, extra_logs={"startup": tmp / "none.log"}, extra={})
            opted_out = _report(tmp, include=False, extra_logs={"updateHelper": helper}, extra={"arch": "x64"})
        self.assertNotIn("extraLogs", missing["diagnostics"])
        self.assertNotIn("diagnostics", opted_out)
        self.assertNotIn("helper ran", json.dumps(opted_out, ensure_ascii=False))

    def test_diagnostics_stay_under_the_collector_limit_and_keep_newest_lines(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            logs = {}
            for name in bug_reporting.EXTRA_LOG_NAMES:
                path = tmp / f"{name}.log"
                path.write_text("오래된 줄\n" * 5_000 + f"{name} 마지막 줄", encoding="utf-8")
                logs[name] = path
            report = _report(tmp, extra_logs=logs, extra={"arch": "x64"},
                             app_log="old line\n" * 20_000 + "app 마지막 줄")
        diagnostics = report["diagnostics"]
        size = len(json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")))
        # reports_worker rejects diagnostics JSON over 24,000 characters.
        self.assertLessEqual(size, 24_000)
        # Four full extra logs still leave the app log a useful share.
        self.assertGreater(len(diagnostics["logTail"]), 4_000)
        self.assertTrue(diagnostics["logTail"].endswith("app 마지막 줄"))
        self.assertEqual(set(bug_reporting.EXTRA_LOG_NAMES), set(diagnostics["extraLogs"]))
        for name, tail in diagnostics["extraLogs"].items():
            self.assertLessEqual(len(json.dumps(tail, ensure_ascii=False)), bug_reporting.MAX_EXTRA_LOG_CHARS)
            self.assertTrue(tail.endswith(f"{name} 마지막 줄"))

    def test_a_long_app_log_alone_no_longer_overflows_the_collector(self):
        # Before: a 24,000-character app.log tail alone made the collector reject the report.
        with tempfile.TemporaryDirectory() as raw:
            report = _report(Path(raw), app_log='C:\\x "quoted"\n' * 10_000 + "끝")
        diagnostics = report["diagnostics"]
        self.assertLessEqual(len(json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))), 24_000)
        self.assertTrue(diagnostics["logTail"].endswith("끝"))

    def test_nul_heavy_log_is_trimmed_to_the_limit_not_emptied(self):
        # A BOM-less UTF-16LE log decodes as UTF-8 with a NUL (6 JSON chars) after every letter.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            install = tmp / "update-install.log"
            install.write_bytes("Setup failed\n".encode("utf-16-le") * 1_000)
            report = _report(tmp, extra_logs={"updateInstall": install}, extra={})
        tail = report["diagnostics"]["extraLogs"]["updateInstall"]
        size = len(json.dumps(tail, ensure_ascii=False))
        self.assertLessEqual(size, bug_reporting.MAX_EXTRA_LOG_CHARS)
        self.assertGreater(size, bug_reporting.MAX_EXTRA_LOG_CHARS - 6)

    def test_server_attaches_newest_update_attempt_and_remembers_consumed_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            updates = tmp / "updates"
            old, new = updates / "pending-old", updates / "pending-new"
            for index, folder in enumerate((old, new)):
                folder.mkdir(parents=True)
                (folder / "update-helper.log").write_text(f"{folder.name} helper", encoding="utf-8")
                os.utime(folder, (1_000 + index, 1_000 + index))
            (new / "update-install.log").write_text("inno failed", encoding="utf-8")
            (new / "update-failed").write_text("", encoding="utf-8")
            startup = tmp / "ClassInEDBMVP-startup.log"
            startup.write_text("Traceback: PermissionError", encoding="utf-8")
            with patch.object(app_server, "_update_root", return_value=updates), \
                    patch.object(app_server, "_startup_log_path", return_value=startup), \
                    patch.object(app_server, "_UPDATE_FAILURE_SEEN", False), \
                    patch.object(app_server, "APP_LOG_FILE", tmp / "app.log"), \
                    patch.object(app_server, "deliver_bug_report", return_value={"ok": True, "reportId": "EDB-1"}) as deliver:
                # The startup update check consumes the marker before anyone files a report.
                self.assertTrue(app_server._consume_update_failure_marker())
                self.assertFalse((new / "update-failed").exists())
                handler = app_server.AppRequestHandler.__new__(app_server.AppRequestHandler)
                handler._read_json_body = Mock(return_value={"description": "업데이트가 안 끝나요", "includeDiagnostics": True})
                handler._send_json = Mock()
                handler._handle_bug_report()
        handler._send_json.assert_called_once_with({"ok": True, "reportId": "EDB-1"}, status=HTTPStatus.CREATED)
        diagnostics = deliver.call_args.args[0]["diagnostics"]
        self.assertEqual("pending-new helper", diagnostics["extraLogs"]["updateHelper"])
        self.assertEqual("inno failed", diagnostics["extraLogs"]["updateInstall"])
        self.assertIn("PermissionError", diagnostics["extraLogs"]["startup"])
        self.assertIs(True, diagnostics["lastUpdateFailed"])
        self.assertIs(False, diagnostics["dataFolderFallback"])
        self.assertEqual(app_server._app_arch_key(), diagnostics["arch"])

    def test_data_folder_fallback_flags_an_installed_app_off_its_documents_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            with patch.object(app_server, "_update_root", return_value=tmp / "updates"), \
                    patch.object(app_server, "is_frozen_app", return_value=True), \
                    patch.object(app_server, "BASE_DIR", tmp / "fallback"), \
                    patch.object(app_server, "default_frozen_app_home", return_value=tmp / "documents") as home:
                self.assertIs(True, app_server._bug_report_diagnostics()[1]["dataFolderFallback"])
                home.return_value = tmp / "fallback"
                self.assertIs(False, app_server._bug_report_diagnostics()[1]["dataFolderFallback"])

    def test_copied_report_names_app_version_and_computer(self):
        source = (ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        start = source.index("function bugReportEnvironmentLine(")
        helper = source[start:source.index("\n}\n", start) + 2]
        script = helper + """
const cases = [
  [{ currentVersion: '0.2.15', platform: 'macos', arch: 'arm64' }, true],
  [{ currentVersion: '0.2.15', platform: 'macos', arch: 'x64' }, true],
  [{ currentVersion: '0.2.15', platform: 'windows', arch: 'x64' }, true],
  [null, false],
  [{ channelStatus: 'error' }, true],
];
process.stdout.write(JSON.stringify(cases.map(([info, mac]) => {
  globalThis.IS_MAC_PLATFORM = mac;
  return bugReportEnvironmentLine(info);
})));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertEqual(
            ["앱 버전 0.2.15 · Mac (Apple 칩)", "앱 버전 0.2.15 · Mac (인텔 칩)", "앱 버전 0.2.15 · Windows", "Windows", "Mac"],
            json.loads(result.stdout),
        )
        dialog = source.split("function BugReportDialog", 1)[1].split("function OperationRecoveryBanner", 1)[0]
        self.assertIn("const reportText = [environmentLine, draftText,", dialog)
        self.assertIn("environmentLine={bugReportEnvironmentLine(updateInfo)}", source)


class StagingGateTests(unittest.TestCase):
    commit, version = "a" * 40, "0.2.15"

    def _proof(self, directory: Path, **overrides):
        proof = {"platform": "windows", "arch": "x64", "targetVersion": self.version, "targetSha256": "f" * 64,
                 "targetSourceCommit": self.commit, "wrongPinRejected": True, **overrides}
        (directory / "update-smoke.json").write_text(json.dumps(proof), encoding="utf-8")

    def _check(self, directory: Path):
        stage.check_update_smoke(directory, "windows", "x64", self.version, self.commit, "f" * 64)

    def test_staging_requires_update_smoke_for_the_exact_installer(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "windows-installer"
            directory.mkdir()
            with self.assertRaisesRegex(ValueError, "verify_native_update=true"):
                self._check(directory)
            self._proof(directory)
            self._check(directory)
            for key, value in [("targetSha256", "0" * 64), ("targetSourceCommit", "b" * 40), ("targetVersion", "0.2.14"),
                               ("arch", "arm64"), ("platform", "macos"), ("wrongPinRejected", False)]:
                with self.subTest(key=key):
                    self._proof(directory, **{key: value})
                    with self.assertRaisesRegex(ValueError, f"windows-installer/{key}"):
                        self._check(directory)

    def test_staging_refuses_a_version_not_above_the_published_releases(self):
        releases = [
            {"tag_name": "v0.2.19", "draft": False, "prerelease": False},
            {"tag_name": "v0.2.30", "draft": True, "prerelease": False},
            {"tag_name": "v0.2.40-rc1", "draft": False, "prerelease": True},
            {"tag_name": "v0.3.0", "draft": False, "prerelease": True},
            {"tag_name": "nightly", "draft": False, "prerelease": False},
        ]
        for version in ("0.2.9", "0.2.19", "0.1.99"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "v0.2.19"):
                stage.validate_version_increase(releases, version)
        for version in ("0.2.20", "1.0.0"):
            stage.validate_version_increase(releases, version)
        stage.validate_version_increase([], "0.0.1")
        stage.validate_version_increase([{"tag_name": "v0.2.14", "draft": True}], "0.2.1")

    def test_main_checks_version_increase_before_downloading(self):
        main = Path(stage.__file__).read_text(encoding="utf-8").split("def main()", 1)[1]
        self.assertLess(main.index("validate_version_increase(releases, args.version)"), main.index('"gh", "run", "download"'))

    def test_workflow_runs_native_update_by_default_and_ships_its_proof(self):
        inputs = WORKFLOW.split("      verify_native_update:", 1)[1].split("      license_compliance_approved:", 1)[0]
        self.assertIn("default: true", inputs)
        # Each installer artifact carries the proof the staging script checks.
        self.assertIn("--output dist/update-smoke.json", WORKFLOW)
        self.assertIn("--output dist\\update-smoke.json", WORKFLOW)
        for artifact in ("name: macos-installer-${{ matrix.arch }}", "name: windows-installer"):
            upload = WORKFLOW.split(artifact, 1)[1].split("\n\n", 1)[0]
            self.assertIn("dist/update-smoke.json", upload)

    def test_update_channel_build_without_pin_stops_in_the_quality_gate(self):
        gate = WORKFLOW.split("- name: Validate release inputs", 1)[1].split("scripts/validate_release_inputs.py", 1)[0]
        self.assertIn("HAS_UPDATE_PIN: ${{ secrets.EDB_UPDATE_PIN != '' }}", gate)
        self.assertIn('[[ "$UPDATE_CHANNEL" == "true" && "$HAS_UPDATE_PIN" != "true" ]]', gate)
        self.assertIn('( "$PRODUCTION_RELEASE" == "true" || -n "$UPDATE_FEED_URL" ) && -z "$EDB_PACKAGE_UPDATE_PIN"', WORKFLOW)
        self.assertIn('($env:PRODUCTION_RELEASE -eq "true" -or $env:UPDATE_FEED_URL) -and -not $env:EDB_PACKAGE_UPDATE_PIN', WORKFLOW)


class MacMinimumVersionTests(unittest.TestCase):
    def test_packaged_mac_app_must_declare_the_profile_minimum_macos(self):
        minimum = json.loads((ROOT / "release" / "opencv-image-profile.json").read_text(encoding="utf-8"))["minimumMacOS"]
        fixtures = packaging_fixtures.TestPackagingFrontendManifest()
        for declared, should_fail in [(minimum, False), (None, True), ("10.15", True)]:
            with self.subTest(declared=declared), tempfile.TemporaryDirectory() as raw:
                package_root = Path(raw) / "ClassInEDBMVP.app"
                fixtures._write_packaged_runtime(package_root, "Contents/Resources")
                fixtures._write_macos_info_plist(package_root)
                plist_path = package_root / "Contents" / "Info.plist"
                info = plistlib.loads(plist_path.read_bytes())
                if declared is None:
                    info.pop("LSMinimumSystemVersion")
                else:
                    info["LSMinimumSystemVersion"] = declared
                plist_path.write_bytes(plistlib.dumps(info))
                errors = [error for error in collect_package_errors(package_root) if "LSMinimumSystemVersion" in error]
                self.assertEqual(should_fail, bool(errors), errors)


if __name__ == "__main__":
    unittest.main()
