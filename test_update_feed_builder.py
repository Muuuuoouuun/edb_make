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


if __name__ == "__main__":
    unittest.main()
