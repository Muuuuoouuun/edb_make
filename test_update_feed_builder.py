from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUpdateFeedBuilder(unittest.TestCase):
    def test_builds_platform_feed_with_sha256_and_size(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            setup = tmpdir / "ClassInEDBMVP-Setup.exe"
            dmg.write_bytes(b"mac artifact")
            setup.write_bytes(b"windows artifact")
            output = tmpdir / "update.json"
            manifest = tmpdir / "manifest.json"
            checksums = tmpdir / "checksums.txt"

            subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--summary",
                    "Test release",
                    "--channel",
                    "stable",
                    "--update-feed-url",
                    "https://example.test/update.json",
                    "--release-notes-url",
                    "https://example.test/releases/0.1.1",
                    "--manifest-url",
                    "https://example.test/manifest.json",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--macos-arch",
                    "universal2",
                    "--windows-url",
                    "https://example.test/ClassInEDBMVP-Setup.exe",
                    "--windows-file",
                    str(setup),
                    "--manifest-output",
                    str(manifest),
                    "--checksums-output",
                    str(checksums),
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )

            feed = json.loads(output.read_text(encoding="utf-8"))
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            checksum_text = checksums.read_text(encoding="utf-8")

        self.assertEqual("0.1.1", feed["version"])
        self.assertEqual(1, feed["schemaVersion"])
        self.assertEqual("ClassInEDBMVP", feed["appId"])
        self.assertEqual("stable", feed["channel"])
        self.assertEqual("Test release", feed["summary"])
        self.assertIn("publishedAt", feed)
        self.assertEqual("https://example.test/manifest.json", feed["manifestUrl"])
        self.assertIn("manifestSha256", feed)
        self.assertEqual(
            "https://example.test/ClassInEDBMVP-macOS.dmg",
            feed["platforms"]["macos"]["downloadUrl"],
        )
        self.assertEqual("dmg", feed["platforms"]["macos"]["artifactType"])
        self.assertEqual("universal2", feed["platforms"]["macos"]["arch"])
        self.assertEqual(len(b"mac artifact"), feed["platforms"]["macos"]["sizeBytes"])
        self.assertEqual(hashlib.sha256(b"mac artifact").hexdigest(), feed["platforms"]["macos"]["sha256"])
        self.assertEqual(hashlib.sha256(b"windows artifact").hexdigest(), feed["platforms"]["windows"]["sha256"])
        self.assertEqual("https://example.test/update.json", manifest_data["updateFeedUrl"])
        self.assertEqual(2, len(manifest_data["artifacts"]))
        self.assertIn("ClassInEDBMVP-macOS.dmg", checksum_text)
        self.assertIn("manifest.json", checksum_text)

    def test_rejects_manifest_sha_mismatch_when_generating_manifest(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--manifest-output",
                    str(tmpdir / "manifest.json"),
                    "--manifest-sha256",
                    "f" * 64,
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("manifestSha256 does not match generated release manifest", result.stderr)

    def test_rejects_empty_artifact_file(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("artifact file is empty", result.stderr)

    def test_rejects_platform_artifact_with_wrong_extension(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            wrong_macos_artifact = tmpdir / "ClassInEDBMVP-Setup.exe"
            wrong_macos_artifact.write_bytes(b"windows setup")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(wrong_macos_artifact),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("dmg artifact must use expected file extension (.dmg)", result.stderr)

    def test_rejects_platform_artifact_type_mismatch(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            setup = tmpdir / "ClassInEDBMVP-Setup.exe"
            setup.write_bytes(b"windows setup")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-Setup.exe",
                    "--macos-file",
                    str(setup),
                    "--macos-artifact-type",
                    "setup-exe",
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("macos artifact type must be one of: dmg, zip", result.stderr)

    def test_rejects_download_url_filename_that_mismatches_artifact_type(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-Setup.exe",
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "macos download URL file name does not match dmg artifact extension (.dmg)",
            result.stderr,
        )

    def test_rejects_download_url_filename_that_disagrees_with_local_artifact(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS-old.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "macos download URL file name 'ClassInEDBMVP-macOS-old.dmg' "
            "does not match artifact file name 'ClassInEDBMVP-macOS.dmg'",
            result.stderr,
        )

    def test_rejects_download_url_without_artifact_extension(self) -> None:
        invalid_urls = (
            "https://example.test/download",
            "https://example.test/releases/0.1.1/",
        )
        for download_url in invalid_urls:
            with self.subTest(download_url=download_url), TemporaryDirectory() as raw_tmp:
                tmpdir = Path(raw_tmp)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                        "--version",
                        "0.1.1",
                        "--macos-url",
                        download_url,
                        "--output",
                        str(tmpdir / "update.json"),
                    ],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                )

            self.assertNotEqual(0, result.returncode)
            self.assertIn(
                "macos download URL file name must include dmg artifact extension (.dmg)",
                result.stderr,
            )

    def test_derives_download_file_name_without_query_string(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            output = tmpdir / "update.json"

            subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg?download=1",
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )

            feed = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("ClassInEDBMVP-macOS.dmg", feed["platforms"]["macos"]["fileName"])

    def test_rejects_artifact_without_download_url(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("macos artifact requires a download URL", result.stderr)

    def test_rejects_non_https_download_url(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "http://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("macos download URL must use https or loopback http", result.stderr)

    def test_rejects_non_https_metadata_urls(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--update-feed-url",
                    "http://example.test/update.json",
                    "--manifest-url",
                    "https://example.test/manifest.json",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("update feed URL must use https or loopback http", result.stderr)

    def test_allows_loopback_http_download_url_for_local_testing(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            output = tmpdir / "update.json"

            subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--update-feed-url",
                    "http://localhost:8000/update.json",
                    "--manifest-output",
                    str(tmpdir / "manifest.json"),
                    "--manifest-url",
                    "http://127.0.0.1:8000/manifest.json",
                    "--macos-url",
                    "http://127.0.0.1:8000/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )

            feed = json.loads(output.read_text(encoding="utf-8"))
            manifest = json.loads((tmpdir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("http://127.0.0.1:8000/ClassInEDBMVP-macOS.dmg", feed["platforms"]["macos"]["downloadUrl"])
        self.assertEqual("http://127.0.0.1:8000/manifest.json", feed["manifestUrl"])
        self.assertEqual("http://localhost:8000/update.json", manifest["updateFeedUrl"])

    def test_rejects_reusing_same_artifact_for_multiple_platforms(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            artifact = tmpdir / "ClassInEDBMVP-macOS.dmg"
            artifact.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "0.1.1",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(artifact),
                    "--windows-url",
                    "https://example.test/ClassInEDBMVP-Setup.exe",
                    "--windows-file",
                    str(artifact),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("windows and macos artifacts point to the same file", result.stderr)

    def test_rejects_empty_release_version(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
            dmg.write_bytes(b"mac artifact")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                    "--version",
                    "",
                    "--macos-url",
                    "https://example.test/ClassInEDBMVP-macOS.dmg",
                    "--macos-file",
                    str(dmg),
                    "--output",
                    str(tmpdir / "update.json"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("release version must not be empty", result.stderr)

    def test_rejects_empty_required_feed_identity_fields(self) -> None:
        required_fields = (
            ("--app-id", "app id must not be empty"),
            ("--app-name", "app name must not be empty"),
            ("--channel", "release channel must not be empty"),
        )
        for option_name, expected_error in required_fields:
            with self.subTest(option_name=option_name), TemporaryDirectory() as raw_tmp:
                tmpdir = Path(raw_tmp)
                dmg = tmpdir / "ClassInEDBMVP-macOS.dmg"
                dmg.write_bytes(b"mac artifact")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PROJECT_ROOT / "scripts" / "build_update_feed.py"),
                        "--version",
                        "0.1.1",
                        option_name,
                        "",
                        "--macos-url",
                        "https://example.test/ClassInEDBMVP-macOS.dmg",
                        "--macos-file",
                        str(dmg),
                        "--output",
                        str(tmpdir / "update.json"),
                    ],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                )

            self.assertNotEqual(0, result.returncode)
            self.assertIn(expected_error, result.stderr)


if __name__ == "__main__":
    unittest.main()
