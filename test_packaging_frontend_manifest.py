from __future__ import annotations

import unittest
from pathlib import Path

from scripts.verify_frontend_package import collect_errors


PROJECT_ROOT = Path(__file__).resolve().parent


class TestPackagingFrontendManifest(unittest.TestCase):
    def test_frontend_package_manifest_is_current(self) -> None:
        self.assertEqual([], collect_errors(PROJECT_ROOT))

    def test_packaging_scripts_run_frontend_package_verifier(self) -> None:
        for rel_path in ("ClassInEDBMVP.spec", "package_macos_app.sh", "package_mvp.ps1"):
            with self.subTest(rel_path=rel_path):
                source = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
                if rel_path.endswith(".spec"):
                    self.assertIn("collect_errors", source)
                    self.assertIn("verify_frontend_package()", source)
                else:
                    self.assertIn("scripts/verify_frontend_package.py".replace("/", "\\" if rel_path.endswith(".ps1") else "/"), source)


if __name__ == "__main__":
    unittest.main()
