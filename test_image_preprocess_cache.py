from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import preprocess


class TestImagePreprocessCache(unittest.TestCase):
    def test_prepare_pages_passthroughs_image_when_no_transform_is_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "page.png"
            Image.new("RGB", (120, 80), "white").save(source)

            pages = preprocess.prepare_pages(
                source,
                root / "out",
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=False,
                max_dimension=2400,
            )

            self.assertEqual(1, len(pages))
            self.assertEqual(source.resolve(), Path(pages[0].normalized_path).resolve())
            self.assertIs(True, pages[0].metadata["image_passthrough"])
            self.assertEqual(120, pages[0].width_px)
            self.assertEqual(80, pages[0].height_px)

    def test_prepare_pages_resizes_image_instead_of_passthrough_when_over_max_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "wide.png"
            Image.new("RGB", (300, 100), "white").save(source)

            pages = preprocess.prepare_pages(
                source,
                root / "out",
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=False,
                max_dimension=200,
            )

            self.assertEqual(1, len(pages))
            self.assertNotEqual(source.resolve(), Path(pages[0].normalized_path).resolve())
            self.assertEqual((200, 67), (pages[0].width_px, pages[0].height_px))
            self.assertEqual(200, pages[0].metadata["resized_to_max_dimension"])

    def test_prepare_pages_reuses_image_normalized_cache_for_same_source_and_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            image = Image.new("RGB", (80, 60), "white")
            image.putpixel((20, 20), (0, 0, 0))
            image.save(source)
            out_dir = root / "out"

            first = preprocess.prepare_pages(
                source,
                out_dir,
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=True,
            )
            second = preprocess.prepare_pages(
                source,
                out_dir,
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=True,
            )

            self.assertEqual(Path(first[0].normalized_path).resolve(), Path(second[0].normalized_path).resolve())
            self.assertIs(True, second[0].metadata["image_normalized_cache_hit"])

    def test_prepare_pages_image_cache_is_content_keyed_and_updates_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "upload-a.png"
            second_source = root / "upload-b.png"
            image = Image.new("RGB", (80, 60), "white")
            image.putpixel((20, 20), (0, 0, 0))
            image.save(first_source)
            second_source.write_bytes(first_source.read_bytes())
            out_dir = root / "out"

            preprocess.prepare_pages(
                first_source,
                out_dir,
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=True,
            )
            second = preprocess.prepare_pages(
                second_source,
                out_dir,
                enable_perspective=False,
                enable_deskew=False,
                enable_margin_crop=True,
            )

            self.assertIs(True, second[0].metadata["image_normalized_cache_hit"])
            self.assertEqual(str(second_source), second[0].source_path)


if __name__ == "__main__":
    unittest.main()
