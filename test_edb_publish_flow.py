import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app_server import validate_edb_file
from build_problem_board_edb import run_problem_export


class TestEdbPublishFlow(unittest.TestCase):
    def _make_source_image(self, path: Path) -> None:
        image = Image.new("RGB", (860, 620), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((48, 48, 812, 572), outline="black", width=4)
        draw.text((80, 92), "1. Smoke problem", fill="black")
        draw.text((80, 160), "A generated EDB should validate.", fill="black")
        image.save(path)

    def test_generated_single_problem_edb_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=True,
            )

            validation = validate_edb_file(result["edb_path"], expected_min_records=1)
            self.assertEqual(validation["recordCountActual"], 1)
            self.assertGreater(validation["outerSize"], 0)

    def test_session_source_images_point_to_rendered_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=False,
            )

            session = result["ui_session"]
            page_source = Path(session["pages"][0]["sourceImagePath"])
            problem_source = Path(session["problems"][0]["sourceImagePath"].replace("file://", ""))
            self.assertEqual(page_source.suffix.lower(), ".png")
            self.assertEqual(problem_source.suffix.lower(), ".png")
            self.assertTrue(page_source.exists())
            self.assertTrue(problem_source.exists())


if __name__ == "__main__":
    unittest.main()
