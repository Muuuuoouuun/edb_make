import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import app_server
import build_problem_board_edb
from image_reconstruction_backend import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    DEFAULT_OPENAI_IMAGE_MODEL,
    ImageReconstructionResult,
    clean_problem_image_transparency,
    postprocess_reconstructed_problem_image,
)


class TestImageReconstructionMutation(unittest.TestCase):
    def setUp(self):
        self._prev_gemini_key = os.environ.get("GEMINI_API_KEY")
        self._prev_openai_key = os.environ.get("OPENAI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-gemini-key"

    def tearDown(self):
        self._restore_env("GEMINI_API_KEY", self._prev_gemini_key)
        self._restore_env("OPENAI_API_KEY", self._prev_openai_key)

    def _restore_env(self, name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

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

    def _fake_reconstruct(self, source_path, output_path, **kwargs):
        Image.new("RGBA", (640, 360), (0, 0, 0, 0)).save(output_path)
        return ImageReconstructionResult(
            output_path=Path(output_path),
            provider=kwargs.get("provider") or "gemini",
            model=kwargs.get("model") or DEFAULT_GEMINI_IMAGE_MODEL,
            prompt=kwargs.get("prompt") or "",
            source_path=Path(source_path),
            latency_ms=12,
            postprocess={"status": "applied", "transparent_ratio": 1.0},
        )

    def test_enhance_image_defaults_to_gemini_and_marks_for_review(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = self._build_session(root)

            with patch.object(app_server, "reconstruct_problem_image", side_effect=self._fake_reconstruct) as mock_reconstruct:
                updated = app_server._mutate_enhance_image(session, {"problemIds": ["problem-1"]})

            problem = updated["problems"][0]
            self.assertNotEqual(problem["imagePath"], problem["originalImagePath"])
            self.assertEqual(problem["boardRenderPath"], problem["imagePath"])
            self.assertEqual(problem["processingStep"], "s3")
            self.assertEqual(problem["reviewStatus"], "check_needed")
            self.assertIn("ai_image_reconstructed_check_text", problem["riskFlags"])
            self.assertEqual(problem["aiImageReconstruction"]["provider"], "gemini")
            self.assertEqual(problem["aiImageReconstruction"]["model"], DEFAULT_GEMINI_IMAGE_MODEL)
            self.assertTrue(problem["aiImageReconstruction"]["transparent_background"])
            self.assertTrue(Path(problem["aiImageReconstruction"]["output_path"]).exists())

            called_kwargs = mock_reconstruct.call_args.kwargs
            self.assertEqual(called_kwargs["provider"], "gemini")
            self.assertEqual(called_kwargs["model"], DEFAULT_GEMINI_IMAGE_MODEL)
            self.assertTrue(called_kwargs["transparent_background"])
            self.assertTrue(called_kwargs["sharpen"])

            summary = updated.get("ai_image_reconstruction_summary") or []
            self.assertEqual(summary[0]["status"], "applied")
            self.assertEqual(summary[0]["provider"], "gemini")
            self.assertEqual(summary[0]["problemId"], "problem-1")

    def test_missing_gemini_key_rejects_before_mutation(self):
        os.environ.pop("GEMINI_API_KEY", None)
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = self._build_session(root)
            with self.assertRaises(ValueError) as ctx:
                app_server._mutate_enhance_image(session, {"problemIds": ["problem-1"]})
            self.assertIn("Gemini API", str(ctx.exception))
            self.assertNotIn("ai_image_reconstruction_summary", session)
            self.assertNotIn("aiImageReconstruction", session["problems"][0])

    def test_openai_provider_still_supported_as_fallback(self):
        os.environ["OPENAI_API_KEY"] = "test-openai-key"
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = self._build_session(root)

            with patch.object(app_server, "reconstruct_problem_image", side_effect=self._fake_reconstruct) as mock_reconstruct:
                updated = app_server._mutate_enhance_image(
                    session,
                    {"problemIds": ["problem-1"], "provider": "openai"},
                )

            problem = updated["problems"][0]
            self.assertEqual(problem["aiImageReconstruction"]["provider"], "openai")
            self.assertEqual(problem["aiImageReconstruction"]["model"], DEFAULT_OPENAI_IMAGE_MODEL)
            called_kwargs = mock_reconstruct.call_args.kwargs
            self.assertEqual(called_kwargs["provider"], "openai")
            self.assertEqual(called_kwargs["model"], DEFAULT_OPENAI_IMAGE_MODEL)

    def test_postprocess_removes_white_background_without_erasing_dark_text(self):
        with TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "reconstructed.png"
            image = Image.new("RGBA", (3, 1), (255, 255, 255, 255))
            image.putdata([
                (255, 255, 255, 255),
                (238, 238, 238, 255),
                (0, 0, 0, 255),
            ])
            image.save(path)

            stats = postprocess_reconstructed_problem_image(path, sharpen=False)
            output = Image.open(path).convert("RGBA")

            self.assertEqual(stats["status"], "applied")
            self.assertEqual(output.getpixel((0, 0))[3], 0)
            self.assertLess(output.getpixel((1, 0))[3], 255)
            self.assertEqual(output.getpixel((2, 0))[3], 255)

    def test_postprocess_upscales_undersized_reconstruction_and_boosts_ink_alpha(self):
        with TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "undersized_reconstructed.png"
            image = Image.new("RGBA", (40, 20), (18, 22, 26, 255))
            pixels = image.load()
            for x in range(8, 32):
                pixels[x, 10] = (210, 214, 214, 128)
            image.save(path)

            stats = postprocess_reconstructed_problem_image(
                path,
                source_size=(60, 30),
                upscale_factor=2.0,
            )
            output = Image.open(path).convert("RGBA")

            self.assertEqual(output.size, (120, 60))
            self.assertTrue(stats["upscaled"])
            self.assertGreaterEqual(stats["upscale_scale"], 3.0)
            self.assertGreater(output.getpixel((60, 30))[3], 180)

    def test_postprocess_removes_dark_model_background_without_erasing_chalk(self):
        with TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "dark_reconstructed.png"
            image = Image.new("RGBA", (80, 30), (18, 22, 26, 255))
            pixels = image.load()
            pixels[10, 10] = (35, 39, 43, 255)
            pixels[30, 10] = (180, 184, 188, 255)
            pixels[50, 10] = (248, 248, 246, 255)
            image.save(path)

            stats = postprocess_reconstructed_problem_image(path, sharpen=False)
            output = Image.open(path).convert("RGBA")

            self.assertEqual(stats["background_kind"], "dark")
            self.assertEqual(output.getpixel((0, 0))[3], 0)
            self.assertLess(output.getpixel((10, 10))[3], 255)
            self.assertGreater(output.getpixel((30, 10))[3], 200)
            self.assertEqual(output.getpixel((50, 10))[3], 255)

    def test_clean_transparency_uses_numpy_alpha_backend_for_flat_light_pages(self):
        image = Image.new("RGBA", (160, 120), (255, 255, 255, 255))
        pixels = image.load()
        pixels[10, 10] = (246, 246, 246, 255)
        pixels[42, 30] = (36, 36, 36, 255)
        pixels[80, 60] = (245, 245, 245, 255)

        cleaned, stats = clean_problem_image_transparency(
            image,
            remove_corner_page_artifacts=False,
        )

        self.assertEqual("numpy", stats["alpha_backend"])
        self.assertEqual(stats["background_kind"], "light")
        self.assertEqual(cleaned.getpixel((0, 0))[3], 0)
        self.assertEqual(cleaned.getpixel((10, 10))[3], 0)
        self.assertEqual(cleaned.getpixel((42, 30))[3], 255)
        self.assertLess(cleaned.getpixel((80, 60))[3], 255)

    def test_clean_transparency_removes_lower_right_pdf_page_badge(self):
        image = Image.new("RGBA", (120, 90), (18, 22, 26, 255))
        pixels = image.load()
        # Real problem content near the bottom but away from the outer edge.
        for x in range(8, 48):
            pixels[x, 74] = (248, 248, 246, 255)
        # Page-number badge glued to the lower-right crop edge.
        for x in range(102, 120):
            pixels[x, 74] = (248, 248, 246, 255)
            pixels[x, 89] = (248, 248, 246, 255)
        for y in range(74, 90):
            pixels[102, y] = (248, 248, 246, 255)
            pixels[119, y] = (248, 248, 246, 255)
        for x in range(108, 113):
            for y in range(78, 86):
                pixels[x, y] = (248, 248, 246, 255)

        cleaned, stats = clean_problem_image_transparency(image)

        self.assertEqual(stats["removed_corner_artifacts"], 1)
        self.assertGreater(cleaned.getpixel((16, 74))[3], 200)
        self.assertEqual(cleaned.getpixel((112, 82))[3], 0)
        self.assertEqual(cleaned.getpixel((119, 89))[3], 0)

    def test_clean_transparency_removes_upper_right_artifact_but_keeps_problem_number(self):
        image = Image.new("RGBA", (140, 100), (18, 22, 26, 255))
        pixels = image.load()
        # Problem number/content near the upper-left must never be treated as a page marker.
        for x in range(6, 18):
            pixels[x, 8] = (248, 248, 246, 255)
        for y in range(8, 22):
            pixels[6, y] = (248, 248, 246, 255)
        # Small header/page marker glued to the upper-right page edge.
        for x in range(128, 140):
            pixels[x, 0] = (248, 248, 246, 255)
            pixels[x, 9] = (248, 248, 246, 255)
        for y in range(0, 10):
            pixels[128, y] = (248, 248, 246, 255)
            pixels[139, y] = (248, 248, 246, 255)

        cleaned, stats = clean_problem_image_transparency(image)

        self.assertEqual(stats["removed_corner_artifacts"], 1)
        self.assertGreater(cleaned.getpixel((6, 12))[3], 200)
        self.assertEqual(cleaned.getpixel((134, 4))[3], 0)
        self.assertEqual(cleaned.getpixel((139, 0))[3], 0)

    def test_publish_cutout_removes_dark_background_and_corner_page_badge(self):
        image = Image.new("RGBA", (120, 90), (18, 22, 26, 255))
        pixels = image.load()
        for x in range(12, 70):
            pixels[x, 22] = (248, 248, 246, 255)
        for x in range(102, 120):
            pixels[x, 74] = (248, 248, 246, 255)
            pixels[x, 89] = (248, 248, 246, 255)
        for y in range(74, 90):
            pixels[102, y] = (248, 248, 246, 255)
            pixels[119, y] = (248, 248, 246, 255)

        cutout = build_problem_board_edb._extract_problem_cutout(image)

        self.assertEqual(cutout.mode, "RGBA")
        self.assertEqual(cutout.getpixel((0, 0))[3], 0)
        self.assertGreater(cutout.getpixel((20, 22))[3], 200)
        self.assertEqual(cutout.getpixel((112, 82))[3], 0)

    def test_publish_loader_preserves_transparent_cutout_instead_of_compositing_board(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            rendered_path = root / "rendered.png"
            rendered = Image.new("RGBA", (12, 8), (248, 248, 246, 0))
            rendered.putpixel((4, 3), (248, 248, 246, 255))
            rendered.save(rendered_path)
            crop = Image.new("RGB", (12, 8), "white")

            loaded = build_problem_board_edb._load_board_export_image(rendered_path, crop)

            self.assertEqual(loaded.mode, "RGBA")
            self.assertEqual(loaded.getpixel((0, 0))[3], 0)
            self.assertEqual(loaded.getpixel((4, 3))[3], 255)


if __name__ == "__main__":
    unittest.main()
