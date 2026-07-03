from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
