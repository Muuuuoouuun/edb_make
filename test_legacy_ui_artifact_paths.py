from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from build_mvp_export import write_ui_session_bundle as write_mvp_ui_session_bundle
from build_problem_board_edb import (
    main as build_problem_board_main,
    resolve_legacy_prototype_data_path,
    write_ui_session_bundle as write_board_ui_session_bundle,
)


PROJECT_ROOT = Path(__file__).resolve().parent


class TestLegacyUiArtifactPaths(unittest.TestCase):
    def test_legacy_session_bridge_stays_in_output_dir(self) -> None:
        writers = (write_mvp_ui_session_bundle, write_board_ui_session_bundle)
        for writer in writers:
            with self.subTest(writer=writer.__module__), TemporaryDirectory() as raw_tmp:
                output_dir = Path(raw_tmp)
                session_path, synced_path = writer(output_dir, {"problems": []}, sync_ui=True)

                self.assertEqual(output_dir / "ui_session.json", session_path)
                self.assertEqual(output_dir / "generated_session.js", synced_path)
                self.assertTrue((output_dir / "generated_session.js").is_file())

        self.assertFalse((PROJECT_ROOT / "ui_prototype" / "generated_session.js").exists())

    def test_legacy_prototype_data_rejects_project_ui_prototype(self) -> None:
        forbidden = PROJECT_ROOT / "ui_prototype" / "prototype_data.js"

        with self.assertRaises(ValueError) as ctx:
            resolve_legacy_prototype_data_path(forbidden)

        self.assertIn("must not write into project ui_prototype", str(ctx.exception))

    def test_legacy_prototype_data_allows_output_dir(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            allowed = Path(raw_tmp) / "prototype_data.js"

            self.assertEqual(allowed, resolve_legacy_prototype_data_path(allowed))

    def test_board_cli_writes_quality_session_bundle(self) -> None:
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "source.png"
            output_dir = root / "output"
            Image.new("RGB", (640, 960), "white").save(source)
            argv = [
                "build_problem_board_edb.py",
                str(source),
                "--output-dir",
                str(output_dir),
                "--input-intent",
                "page-as-is",
                "--ocr",
                "noop",
                "--skip-deskew",
                "--skip-crop",
            ]

            with patch("sys.argv", argv), redirect_stdout(io.StringIO()):
                exit_code = build_problem_board_main()

            self.assertEqual(0, exit_code)
            session_path = output_dir / "ui_session.json"
            self.assertTrue(session_path.is_file())
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertIn("classinPreflight", session)
            self.assertTrue((output_dir / "classin_handoff.json").is_file())


if __name__ == "__main__":
    unittest.main()
