from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.clean_local_artifacts import collect_cleanup_candidates, remove_candidate


PROJECT_ROOT = Path(__file__).resolve().parent


class TestGeneratedArtifactIgnores(unittest.TestCase):
    def test_repository_does_not_track_ignored_files(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "-ci", "--exclude-standard"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], result.stdout.splitlines())

    def test_legacy_standalone_openai_image_backend_is_removed(self) -> None:
        self.assertFalse(
            (PROJECT_ROOT / "openai_image_backend.py").exists(),
            "OpenAI image reconstruction lives in image_reconstruction_backend.py",
        )

    def test_local_run_artifacts_are_ignored(self) -> None:
        samples = [
            ".DS_Store",
            ".claude/settings.local.json",
            "pipeline_output_worker99_img1/pages.json",
            "ffffffffffffffffffffffffffffffffffffffff_img_sample_deadbeef/pages.json",
            f"{'a' * 40}_legacy_session_deadbeef00/pages.json",
            "generated_edb_pair_future_20990101/page_as_is/classin_handoff.json",
            "dist_browser_home/.app_runtime/latest_session.json",
            "tmp_validation_future/ClassInEDBMVP/ui_prototype/app.bundle.js",
        ]
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(samples) + "\n",
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(set(samples), set(result.stdout.splitlines()))

    def test_local_cleanup_defaults_target_stale_package_outputs_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            for name in (
                "dist",
                "dist_sizecheck",
                "build",
                "tmp_validation_future",
                "generated_edb_pair_future_20990101",
                ".app_runtime",
                "ui_prototype",
            ):
                (root / name).mkdir()

            candidates = collect_cleanup_candidates(root)
            names = {candidate.path.name for candidate in candidates}

        self.assertEqual({"build", "dist", "dist_sizecheck", "tmp_validation_future"}, names)

    def test_local_cleanup_can_opt_into_generated_exports_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            for name in ("dist", "generated_edb_pair_future_20990101", ".app_runtime"):
                (root / name).mkdir()

            candidates = collect_cleanup_candidates(root, include_edb_exports=True, include_runtime=True)
            names = {candidate.path.name for candidate in candidates}

        self.assertEqual({"dist", "generated_edb_pair_future_20990101", ".app_runtime"}, names)

    def test_local_cleanup_removes_only_root_child_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            dist_dir = root / "dist_sizecheck"
            nested_file = dist_dir / "ClassInEDBMVP.app" / "Contents" / "Info.plist"
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("old app", encoding="utf-8")
            keep_dir = root / "generated_edb_pair_future_20990101"
            keep_dir.mkdir()

            [candidate] = collect_cleanup_candidates(root)
            remove_candidate(root, candidate)

            self.assertFalse(dist_dir.exists())
            self.assertTrue(keep_dir.exists())


if __name__ == "__main__":
    unittest.main()
