from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestPublishGuardCacheInvalidation(unittest.TestCase):
    def test_guard_change_requires_rebuild_and_changes_browser_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ui = root / "ui_prototype"
            vendor = root / "scripts" / "vendor"
            ui.mkdir()
            vendor.mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT / "scripts" / "build_frontend_bundle.mjs", root / "scripts")
            shutil.copy2(PROJECT_ROOT / "scripts" / "vendor" / "babel.min.js", vendor)
            for filename in ("art.jsx", "tweaks-panel.jsx", "app.jsx"):
                (ui / filename).write_text("const fixture = <div />;\n", encoding="utf-8")
            (ui / "board.css").write_text("body { margin: 0; }\n", encoding="utf-8")
            guard = ui / "publish_guard.js"
            guard.write_text("window.EDB_PUBLISH_GUARD = {};\n", encoding="utf-8")
            board = ui / "board.html"
            board.write_text(
                '<link href="board.css?v=board-css-old">\n'
                '<script src="app.bundle.js?v=frontend-bundle-old"></script>\n'
                '<script src="publish_guard.js?v=preflight-layout-gap-old"></script>\n',
                encoding="utf-8",
            )

            def build(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["node", "scripts/build_frontend_bundle.mjs", *arguments],
                    cwd=root,
                    text=True,
                    capture_output=True,
                )

            first = build()
            self.assertEqual(first.returncode, 0, first.stderr)
            previous_html = board.read_text(encoding="utf-8")
            guard.write_text("window.EDB_PUBLISH_GUARD = { updated: true };\n", encoding="utf-8")
            stale = build("--check")
            self.assertNotEqual(stale.returncode, 0, "a cached guard must not pass the package check")
            rebuilt = build()
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertNotEqual(board.read_text(encoding="utf-8"), previous_html)
            self.assertEqual(build("--check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
