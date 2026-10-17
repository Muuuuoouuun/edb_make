from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.build_minimal_opencv import add_receipt, cached_wheel, extract_source, sha256
from scripts.verify_minimal_opencv import page_probe, used_symbols


class TestMinimalOpenCV(unittest.TestCase):
    def test_wheel_receipt_preserves_payload_and_has_valid_record_hash(self):
        with TemporaryDirectory() as temp:
            wheel = Path(temp) / "opencv.whl"
            record_name = "opencv_python_headless-4.13.0.92.dist-info/RECORD"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("cv2/native.so", b"native fixture")
                archive.writestr(record_name, "cv2/native.so,,\n" + record_name + ",,\n")
            receipt = {"schemaVersion": 1, "profile": {"name": "image-only"}}
            add_receipt(wheel, receipt)
            with zipfile.ZipFile(wheel) as archive:
                self.assertEqual(b"native fixture", archive.read("cv2/native.so"))
                rows = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
                entry = next(row for row in rows if row[0].endswith("edb-build-profile.json"))
                payload = archive.read(entry[0])
                digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
                self.assertEqual("sha256=" + digest, entry[1])
                self.assertEqual(len(payload), int(entry[2]))
                self.assertEqual(receipt, json.loads(payload))

    def test_cached_wheel_rejects_tampering_and_path_escape(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            wheel = root / "opencv.whl"
            wheel.write_bytes(b"verified")
            manifest = root / "wheel-manifest.json"
            manifest.write_text(json.dumps({"buildKey": "key", "fileName": wheel.name, "sha256": sha256(wheel)}))
            self.assertEqual(wheel, cached_wheel(root, "key"))
            wheel.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                cached_wheel(root, "key")
            manifest.write_text(json.dumps({"buildKey": "key", "fileName": "../outside.whl"}))
            with self.assertRaises(ValueError):
                cached_wheel(root, "key")

    def test_source_hash_is_checked_before_extraction(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.tar.gz"
            archive.write_bytes(b"untrusted source")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                extract_source(archive, root / "extract", "0" * 64)
            self.assertFalse((root / "extract").exists())

    def test_source_archive_cannot_escape_destination(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                entry = tarfile.TarInfo("../outside.txt")
                entry.size = 4
                stream.addfile(entry, io.BytesIO(b"evil"))
            with self.assertRaises(tarfile.TarError):
                extract_source(archive, root / "extract", sha256(archive))
            self.assertFalse((root / "outside.txt").exists())

    def test_app_api_inventory_includes_nested_runtime_helpers(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            (root / "app.py").write_text("cv2.threshold(image)\n")
            (root / "scripts/quality.py").write_text("cv2.Laplacian(image)\n")
            (root / "test_optional.py").write_text("cv2.VideoCapture(0)\n")
            self.assertEqual({"threshold", "Laplacian"}, used_symbols(root))

    def test_page_operations_are_repeatable(self):
        first = page_probe()
        second = page_probe()
        self.assertEqual(first["hashes"], second["hashes"])
        self.assertEqual(52, first["componentCount"])

class TestOpenCVPerformanceGate(unittest.TestCase):
    def run_comparison(self, elapsed):
        from unittest.mock import patch
        from scripts import verify_minimal_opencv as verifier
        with TemporaryDirectory() as temp:
            reference = Path(temp) / 'reference.json'
            reference.write_text(json.dumps({'hashes': {'image': 'same'}, 'componentCount': 1, 'medianMs': 10}))
            with patch.object(verifier, 'page_probe', return_value={
                'hashes': {'image': 'same'}, 'componentCount': 1, 'medianMs': elapsed,
            }):
                return verifier.main(['--compare', str(reference)])

    def test_equivalent_output_with_large_slowdown_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'performance regression'):
            self.run_comparison(1000)

    def test_small_measurement_variation_is_allowed(self):
        self.assertEqual(0, self.run_comparison(10.5))

    def test_nonfinite_timing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'performance regression'):
            self.run_comparison(float('nan'))
