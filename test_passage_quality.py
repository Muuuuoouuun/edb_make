from __future__ import annotations

import unittest

from PIL import Image, ImageDraw, ImageFilter

from passage_quality import assess_passage_crop_quality


def _text_image(*, width: int = 900, height: int = 600, blur: float = 0.0) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for y in range(30, height - 30, 25):
        draw.text((30, y), "Korean passage text 1234567890", fill="black")
    return image.filter(ImageFilter.GaussianBlur(blur)) if blur else image


class TestPassageQuality(unittest.TestCase):
    def test_sharp_200_dpi_passage_scores_good(self) -> None:
        quality = assess_passage_crop_quality(
            _text_image(),
            source_dpi=200,
            detection_confidence=0.98,
            text_line_count=12,
            text_character_count=360,
        )
        self.assertEqual("good", quality["grade"])
        self.assertGreaterEqual(quality["score_10"], 8.5)
        self.assertEqual([], quality["warnings"])

    def test_blurry_low_resolution_passage_is_flagged(self) -> None:
        quality = assess_passage_crop_quality(
            _text_image(width=420, height=300, blur=3.0),
            source_dpi=96,
            detection_confidence=0.82,
            text_line_count=2,
            text_character_count=30,
        )
        self.assertNotEqual("good", quality["grade"])
        self.assertIn("low_source_dpi", quality["warnings"])
        self.assertIn("narrow_passage_crop", quality["warnings"])
        self.assertIn("blurry_passage_crop", quality["warnings"])
        self.assertIn("low_passage_detection_confidence", quality["warnings"])

    def test_near_blank_crop_is_never_good(self) -> None:
        quality = assess_passage_crop_quality(
            Image.new("RGB", (900, 600), "white"),
            source_dpi=200,
            detection_confidence=0.98,
        )
        self.assertNotEqual("good", quality["grade"])
        self.assertIn("near_blank_passage_crop", quality["warnings"])

    def test_text_layer_outside_crop_is_flagged_even_when_image_is_sharp(self) -> None:
        quality = assess_passage_crop_quality(
            _text_image(),
            source_dpi=200,
            detection_confidence=0.98,
            text_line_count=12,
            text_character_count=360,
            text_bounds_score=0.72,
        )
        self.assertNotEqual("good", quality["grade"])
        self.assertEqual(0.72, quality["text_bounds_score"])
        self.assertIn("passage_text_bounds_clipped", quality["warnings"])

    def test_excessive_cross_page_join_gap_requires_review(self) -> None:
        quality = assess_passage_crop_quality(
            _text_image(),
            source_dpi=200,
            detection_confidence=0.98,
            text_line_count=12,
            text_character_count=360,
            stitch_diagnostics={
                "join_count": 2,
                "join_blank_band_px": [28, 96],
                "max_join_blank_band_px": 96,
            },
        )

        self.assertEqual("review", quality["grade"])
        self.assertEqual(2, quality["join_count"])
        self.assertEqual(96, quality["max_join_blank_band_px"])
        self.assertIn("passage_join_gap_excessive", quality["warnings"])


if __name__ == "__main__":
    unittest.main()
