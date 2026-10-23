from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

import app_server


def _session(root: Path, *, crop_size: tuple[int, int], offset: dict | None) -> dict:
    page = root / "page.png"
    Image.new("RGB", (400, 1000), "white").save(page)
    crop = root / "crop.png"
    Image.new("RGB", crop_size, "white").save(crop)
    problem = {
        "id": "p1",
        "sourcePageId": "page-1",
        "imagePath": crop.resolve().as_uri(),
        "bbox": {"left": 0, "top": 100, "width": 400, "height": 600},
    }
    if offset is not None:
        problem["cropSourceOffset"] = offset
    return {"pages": [{"id": "page-1", "sourceImagePath": page.resolve().as_uri()}], "problems": [problem]}


class TestBoardSplitRatio(unittest.TestCase):
    def test_board_cut_maps_through_the_trimmed_and_padded_crop(self) -> None:
        with TemporaryDirectory() as tmp:
            # The crop starts 36px above y=250 (trimmed to 250, padded by 36)
            # and is 300px tall, so its middle sits at page y=364.
            session = _session(Path(tmp), crop_size=(400, 300), offset={"left": 0, "top": 214})
            ratio = app_server._split_ratio_from_crop_ratio(session, "p1", 0.5)
        self.assertAlmostEqual((364 - 100) / 600, ratio)

    def test_crop_without_offset_is_treated_as_the_page_box(self) -> None:
        with TemporaryDirectory() as tmp:
            session = _session(Path(tmp), crop_size=(400, 600), offset=None)
            self.assertAlmostEqual(0.25, app_server._split_ratio_from_crop_ratio(session, "p1", 0.25))

    def test_cuts_at_the_ends_are_refused_with_a_readable_message(self) -> None:
        with TemporaryDirectory() as tmp:
            session = _session(Path(tmp), crop_size=(400, 600), offset=None)
            for ratio in (0.0, 0.02, 0.97, 1.0):
                with self.subTest(ratio=ratio), self.assertRaises(ValueError) as raised:
                    app_server._split_ratio_from_crop_ratio(session, "p1", ratio)
                self.assertRegex(str(raised.exception), "[가-힣]")


if __name__ == "__main__":
    unittest.main()
