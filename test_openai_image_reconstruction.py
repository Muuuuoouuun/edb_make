import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import app_server
from openai_image_backend import DEFAULT_OPENAI_IMAGE_MODEL, ImageReconstructionResult


class TestOpenAiImageReconstructionMutation(unittest.TestCase):
    def setUp(self):
        self._prev_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-openai-key"

    def tearDown(self):
        if self._prev_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._prev_key

    def _build_session(self, root: Path) -> dict:
        crop_path = root / "problem.png"
        Image.new("RGB", (320, 180), "white").save(crop_path)
        return {
            "output_dir": str(root / "out"),
            "problems": [
                {
                    "id": "problem-1",
                    "title": "1. problem",
                    "imagePath": crop_path.resolve().as_uri(),
                    "boardRenderPath": crop_path.resolve().as_uri(),
                    "sourcePageId": "page-1",
                    "bbox": {"left": 0, "top": 0, "width": 320, "height": 180},
                    "riskFlags": [],
                    "step": "s3",
                    "processingStep": "s3",
                }
            ],
            "pages": [{"id": "page-1", "problemIds": ["problem-1"]}],
        }

    def test_enhance_image_replaces_problem_image_and_marks_for_review(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = self._build_session(root)

            def fake_reconstruct(source_path, output_path, **kwargs):
                Image.new("RGB", (640, 360), "white").save(output_path)
                return ImageReconstructionResult(
                    output_path=Path(output_path),
                    model=kwargs.get("model") or DEFAULT_OPENAI_IMAGE_MODEL,
                    prompt=kwargs.get("prompt") or "",
                    source_path=Path(source_path),
                    latency_ms=12,
                )

            with patch.object(app_server, "reconstruct_problem_image", side_effect=fake_reconstruct):
                updated = app_server._mutate_enhance_image(session, {"problemIds": ["problem-1"]})

            problem = updated["problems"][0]
            self.assertNotEqual(problem["imagePath"], problem["originalImagePath"])
            self.assertEqual(problem["boardRenderPath"], problem["imagePath"])
            self.assertEqual(problem["processingStep"], "s3")
            self.assertEqual(problem["reviewStatus"], "check_needed")
            self.assertIn("ai_image_reconstructed_check_text", problem["riskFlags"])
            self.assertEqual(problem["aiImageReconstruction"]["model"], DEFAULT_OPENAI_IMAGE_MODEL)
            self.assertTrue(Path(problem["aiImageReconstruction"]["output_path"]).exists())

            summary = updated.get("ai_image_reconstruction_summary") or []
            self.assertEqual(summary[0]["status"], "applied")
            self.assertEqual(summary[0]["problemId"], "problem-1")

    def test_missing_openai_key_rejects_before_mutation(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = self._build_session(root)
            with self.assertRaises(ValueError) as ctx:
                app_server._mutate_enhance_image(session, {"problemIds": ["problem-1"]})
            self.assertIn("OpenAI API", str(ctx.exception))
            self.assertNotIn("ai_image_reconstruction_summary", session)
            self.assertNotIn("aiImageReconstruction", session["problems"][0])


if __name__ == "__main__":
    unittest.main()
