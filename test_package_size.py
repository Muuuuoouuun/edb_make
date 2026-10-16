from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.report_package_size import build_size_report, main


class TestPackageSize(unittest.TestCase):
    def test_macos_symlink_layout_counts_payload_once_and_stays_inside_bundle(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "Test.app"
            library = root / "Contents/Frameworks/cv2/engine.so"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"x" * 128)
            resources = root / "Contents/Resources"
            resources.mkdir()
            try:
                (resources / "cv2").symlink_to(library.parent, target_is_directory=True)
                (resources / "outside").symlink_to(Path(temp), target_is_directory=True)
                os.link(library, library.parent / "engine-hardlink.so")
            except OSError as exc:
                self.skipTest(f"links unavailable: {exc}")
            report = build_size_report(root)
            self.assertEqual(128, report["sizeBytes"])
            self.assertEqual(1, report["fileCount"])
            self.assertEqual(2, report["symlinkCount"])
            self.assertEqual(1, report["duplicateHardlinkCount"])
            self.assertEqual([{"name": "cv2", "sizeBytes": 128}], report["components"])

    def test_windows_components_and_optional_budget_report(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "app"
            package = root / "_internal/numpy"
            package.mkdir(parents=True)
            (package / "core.pyd").write_bytes(b"numpy")
            (root / "app.exe").write_bytes(b"exe")
            output = Path(temp) / "size.json"
            self.assertEqual(1, main([str(root), "--output", str(output), "--max-bytes", "7"]))
            report = json.loads(output.read_text())
            self.assertEqual(8, report["sizeBytes"])
            self.assertEqual(2, report["fileCount"])
            self.assertEqual({"numpy": 5, "(root files)": 3}, {
                item["name"]: item["sizeBytes"] for item in report["components"]
            })
            self.assertEqual(0, main([str(root), "--output", str(output), "--max-bytes", "8"]))

    def test_report_inside_payload_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(SystemExit):
                main([str(root), "--output", str(root / "size.json")])

    def test_missing_package_is_not_reported_as_empty(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                build_size_report(Path(temp) / "missing")
