from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from build_problem_board_edb import CROP_FORMAT_V1, _page_as_is_content_box, run_problem_export


def _page(*, footer: tuple[int, int, int, int] | None = (380, 950, 420, 965), last_line: int = 450,
          background: str | tuple[int, int, int] = "white") -> Image.Image:
    image = Image.new("RGB", (800, 1000), background)
    draw = ImageDraw.Draw(image)
    for top in range(100, last_line + 1, 50):
        draw.rectangle((60, top, 740, top + 10), fill="black")
    if footer:
        draw.rectangle(footer, fill="black")
    return image


class TestPageAsIsContentBox(unittest.TestCase):
    def test_blank_rows_and_a_lone_page_number_are_dropped_at_full_width(self) -> None:
        box = _page_as_is_content_box(_page())
        self.assertIsNotNone(box)
        # 8px (0.8% of the page) stays around the content, which ends at 460.
        self.assertEqual((0.0, 92.0, 800.0, 377.0), (box.left, box.top, box.width, box.height))

    def test_footer_that_is_wide_tall_or_close_to_the_content_is_kept(self) -> None:
        for footer in ((60, 950, 740, 965), (380, 930, 420, 990)):
            with self.subTest(footer=footer):
                box = _page_as_is_content_box(_page(footer=footer))
                self.assertEqual(min(1000.0, footer[3] + 1 + 8), box.top + box.height)
        # Content ends at 911; a number 14px below it belongs to the content.
        close = _page_as_is_content_box(_page(footer=(380, 925, 420, 940), last_line=900))
        self.assertEqual(949.0, close.top + close.height)

    def test_pages_without_room_to_trim_stay_whole(self) -> None:
        self.assertIsNone(_page_as_is_content_box(Image.new("RGB", (800, 1000), "white")))
        self.assertIsNone(_page_as_is_content_box(_page(background=(200, 200, 200))), "tinted scans stay whole")
        full = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(full).rectangle((60, 5, 740, 990), outline="black")
        self.assertIsNone(_page_as_is_content_box(full))


class TestPageAsIsTrimExport(unittest.TestCase):
    def export(self, root: Path, *, trim: bool) -> dict:
        source = root / "page.png"
        _page().save(source)
        result = run_problem_export(
            source,
            output_dir=root / f"out-{trim}",
            input_intent="page-as-is",
            record_mode="image-only",
            crop_format=CROP_FORMAT_V1,
            skip_deskew=True,
            skip_crop=True,
            max_dimension=None,
            export_edb=False,
            trim_page_whitespace=trim,
        )
        return result["ui_session"]["problems"][0]

    def test_trim_option_crops_the_page_and_shortens_its_board_height(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            whole = self.export(root, trim=False)
            trimmed = self.export(root, trim=True)
        self.assertEqual({"left": 0.0, "top": 0.0, "width": 800.0, "height": 1000.0}, whole["bbox"])
        self.assertEqual({"left": 0.0, "top": 92.0, "width": 800.0, "height": 377.0}, trimmed["bbox"])
        self.assertEqual({"left": 0, "top": 92}, trimmed["cropSourceOffset"])
        self.assertLess(trimmed["actualHeightPages"], whole["actualHeightPages"] * 0.5)
        self.assertEqual("continuous-page-as-is", trimmed["placementMode"])
        self.assertTrue(trimmed["forceFullPageBounds"])
        self.assertTrue(str(whole["imagePath"]).endswith("/page.png"), "an untrimmed page reuses its source")
        self.assertIn("/problem_crops/", str(trimmed["imagePath"]))


if __name__ == "__main__":
    unittest.main()
