from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_mvp_export import write_ui_session_bundle as write_mvp_ui_session_bundle
from build_problem_board_edb import (
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


if __name__ == "__main__":
    unittest.main()
