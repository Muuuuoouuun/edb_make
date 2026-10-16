from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import edb_builder
from build_problem_board_edb import (
    CROP_FORMAT_V1,
    PROCESSING_STEP_CHALK,
    _tile_page_as_is_prepared_pages,
    run_problem_export,
)
from preprocess import PreparedPage


def _two_column_rule_page(size: tuple[int, int] = (800, 1000)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    width, height = size
    center = width // 2
    for top in range(70, height - 100, 55):
        draw.rectangle((45, top, center - 70, top + 9), fill="black")
        draw.rectangle((center + 70, top, width - 45, top + 9), fill="black")
    draw.rectangle((center - 1, 20, center + 1, height - 21), fill=(80, 80, 80))
    return image


class TestPageAsIsQuality(unittest.TestCase):
    def test_auto_tiling_materializes_lossless_ordered_page_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "page.png"
            image = _two_column_rule_page()
            image.save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=4,
                image=image,
                original_size=image.size,
                metadata={"dpi": 200},
            )

            tiled = _tile_page_as_is_prepared_pages([prepared], page_tile_mode="auto")

            self.assertEqual(2, len(tiled))
            self.assertEqual(["left", "right"], [page.metadata["page_tile_column"] for page in tiled])
            self.assertEqual([4, 4], [page.page_number for page in tiled])
            self.assertTrue(all(Path(page.source_path).is_file() for page in tiled))
            reconstructed = Image.new("RGB", image.size)
            reconstructed.paste(tiled[0].image, (0, 0))
            reconstructed.paste(tiled[1].image, (tiled[0].image.width, 0))
            self.assertTrue(np.array_equal(np.asarray(image), np.asarray(reconstructed)))

    def test_page_as_is_pipeline_uses_s2_and_auto_tiles_confident_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "korean-two-column.png"
            _two_column_rule_page().save(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="page-as-is",
                page_tile_mode="auto",
                record_mode="image-only",
                crop_format=CROP_FORMAT_V1,
                skip_deskew=True,
                skip_crop=True,
                max_dimension=None,
                export_edb=False,
            )

            session = result["ui_session"]
            self.assertEqual(2, len(session["pages"]))
            self.assertEqual(2, len(session["problems"]))
            self.assertEqual(
                [PROCESSING_STEP_CHALK, PROCESSING_STEP_CHALK],
                [problem["processingStep"] for problem in session["problems"]],
            )
            self.assertEqual(
                ["페이지 1 · 왼쪽", "페이지 1 · 오른쪽"],
                [problem["title"] for problem in session["problems"]],
            )
            self.assertEqual(["auto", "auto"], [page["pageTileMode"] for page in session["pages"]])
            self.assertEqual([True, True], [page["pageTileSplit"] for page in session["pages"]])
            self.assertEqual(
                ["left", "right"],
                [page["pageTileColumn"] for page in session["pages"]],
            )
            self.assertTrue(all(float(page["pageTileConfidence"]) >= 0.8 for page in session["pages"]))
            self.assertEqual("auto", result["summary"]["page_tile_mode"])
            self.assertEqual(2, result["summary"]["page_tile_count"])

    def test_page_as_is_defaults_to_one_fit_width_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "korean-two-column.png"
            image = _two_column_rule_page()
            image.save(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="page-as-is",
                record_mode="image-only",
                crop_format=CROP_FORMAT_V1,
                skip_deskew=True,
                skip_crop=True,
                max_dimension=None,
                export_edb=False,
            )

            session = result["ui_session"]
            self.assertEqual(1, len(session["pages"]))
            self.assertEqual(1, len(session["problems"]))
            self.assertEqual(image.size, (session["pages"][0]["width"], session["pages"][0]["height"]))
            self.assertEqual("continuous-page-as-is", session["problems"][0]["placementMode"])
            self.assertEqual("off", result["summary"]["page_tile_mode"])
            self.assertEqual(0, result["summary"]["page_tile_count"])

    def test_v1_full_200_dpi_page_secondary_reuses_primary_bytes(self) -> None:
        image = Image.new("RGBA", (2336, 3306), (248, 249, 246, 0))
        image.putpixel((100, 100), (248, 249, 246, 255))
        encoded = bytearray()
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded.extend(buffer.getvalue())

        secondary = edb_builder.build_v1_secondary_image_bytes(
            bytes(encoded),
            page_as_is=True,
            format_hint="PNG",
        )

        self.assertEqual(bytes(encoded), secondary)


if __name__ == "__main__":
    unittest.main()
