import unittest

import numpy as np
from PIL import Image, ImageDraw

from page_tiling import PageTilingOptions, tile_page_columns


def _draw_column_content(draw: ImageDraw.ImageDraw, left: int, right: int, *, color) -> None:
    for top in range(80, 840, 95):
        draw.rectangle((left, top, right, top + 10), fill=color)
        draw.rectangle((left + 24, top + 24, right - 18, top + 30), fill=color)


class TestPageTiling(unittest.TestCase):
    def test_clear_two_column_page_splits_left_to_right_without_pixel_loss(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 330, color=(15, 35, 95))
        _draw_column_content(draw, 470, 745, color=(120, 20, 10))

        result = tile_page_columns(image)

        self.assertTrue(result.was_split)
        self.assertEqual("two_column_gutter", result.reason)
        self.assertEqual(2, len(result.tiles))
        self.assertEqual([0, 1], [tile.column_index for tile in result.tiles])
        self.assertEqual((0, 0, result.split_x, 1000), result.tiles[0].source_box)
        self.assertEqual((result.split_x, 0, 800, 1000), result.tiles[1].source_box)
        self.assertLess(result.split_x, 430)
        self.assertGreater(result.split_x, 370)

        reconstructed = Image.new(image.mode, image.size)
        reconstructed.paste(result.tiles[0].image, (0, 0))
        reconstructed.paste(result.tiles[1].image, (result.split_x, 0))
        self.assertTrue(np.array_equal(np.asarray(image), np.asarray(reconstructed)))

        # Reading order is observable from the deliberately different colours.
        self.assertEqual((15, 35, 95), result.images[0].getpixel((60, 80)))
        right_source_x = 470 - result.split_x
        self.assertEqual((120, 20, 10), result.images[1].getpixel((right_source_x, 80)))

    def test_single_column_content_crossing_center_fails_open(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        for top in range(70, 900, 65):
            draw.rectangle((70, top, 730, top + 12), fill="black")

        result = tile_page_columns(image)

        self.assertFalse(result.was_split)
        self.assertEqual(1, len(result.tiles))
        self.assertIs(image, result.tiles[0].image)
        self.assertEqual((0, 0, 800, 1000), result.tiles[0].source_box)

    def test_narrow_center_gap_is_not_treated_as_gutter(self):
        image = Image.new("L", (800, 1000), 255)
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 45, 389, color=0)
        _draw_column_content(draw, 410, 755, color=0)

        result = tile_page_columns(image)

        self.assertFalse(result.was_split)
        self.assertEqual("no_wide_central_gutter", result.reason)
        self.assertIs(image, result.images[0])

    def test_thin_center_decoration_does_not_trigger_column_split(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 325, color=(20, 20, 20))
        _draw_column_content(draw, 475, 745, color=(20, 20, 20))
        draw.rectangle((398, 25, 401, 974), fill=(80, 80, 80))

        result = tile_page_columns(image)

        self.assertFalse(result.was_split)
        self.assertEqual("central_vertical_rule", result.reason)
        self.assertIs(image, result.images[0])

    def test_center_rule_can_be_explicitly_accepted_with_bilateral_content(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 390, color=(20, 20, 20))
        _draw_column_content(draw, 410, 745, color=(20, 20, 20))
        draw.rectangle((398, 25, 401, 974), fill=(80, 80, 80))

        result = tile_page_columns(
            image,
            PageTilingOptions(allow_center_vertical_rule=True),
        )

        self.assertTrue(result.was_split)
        self.assertEqual("two_column_center_rule", result.reason)
        self.assertEqual(400, result.split_x)
        self.assertEqual([0, 1], [tile.column_index for tile in result.tiles])

        reconstructed = Image.new(image.mode, image.size)
        reconstructed.paste(result.tiles[0].image, (0, 0))
        reconstructed.paste(result.tiles[1].image, (result.split_x, 0))
        self.assertTrue(np.array_equal(np.asarray(image), np.asarray(reconstructed)))

    def test_center_rule_opt_in_still_requires_content_on_both_sides(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 390, color=(20, 20, 20))
        draw.rectangle((399, 25, 400, 974), fill=(80, 80, 80))

        result = tile_page_columns(
            image,
            PageTilingOptions(allow_center_vertical_rule=True),
        )

        self.assertFalse(result.was_split)
        self.assertIs(image, result.images[0])

    def test_center_rule_opt_in_rejects_off_center_rule(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 260, color=(20, 20, 20))
        _draw_column_content(draw, 340, 745, color=(20, 20, 20))
        draw.rectangle((279, 25, 281, 974), fill=(80, 80, 80))

        result = tile_page_columns(
            image,
            PageTilingOptions(allow_center_vertical_rule=True),
        )

        self.assertFalse(result.was_split)
        self.assertIs(image, result.images[0])

    def test_center_rule_opt_in_rejects_wide_center_decoration(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 380, color=(20, 20, 20))
        _draw_column_content(draw, 420, 745, color=(20, 20, 20))
        draw.rectangle((390, 25, 409, 974), fill=(80, 80, 80))

        result = tile_page_columns(
            image,
            PageTilingOptions(allow_center_vertical_rule=True),
        )

        self.assertFalse(result.was_split)
        self.assertIs(image, result.images[0])

    def test_blank_right_half_is_not_mistaken_for_two_columns(self):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 325, color=(10, 10, 10))

        result = tile_page_columns(image)

        self.assertFalse(result.was_split)
        self.assertEqual("insufficient_right_column_ink", result.reason)
        self.assertIs(image, result.images[0])

    def test_transparent_image_is_evaluated_against_white_without_changing_output_mode(self):
        image = Image.new("RGBA", (800, 1000), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 55, 330, color=(0, 0, 0, 255))
        _draw_column_content(draw, 470, 745, color=(0, 0, 0, 255))

        result = tile_page_columns(image)

        self.assertTrue(result.was_split)
        self.assertEqual(["RGBA", "RGBA"], [tile.image.mode for tile in result.tiles])

    def test_thresholds_can_be_tightened_without_changing_api(self):
        image = Image.new("L", (600, 700), 255)
        draw = ImageDraw.Draw(image)
        _draw_column_content(draw, 35, 260, color=0)
        _draw_column_content(draw, 340, 565, color=0)

        result = tile_page_columns(
            image,
            PageTilingOptions(min_gutter_width_ratio=0.20),
        )

        self.assertFalse(result.was_split)
        self.assertEqual(1, len(result.images))


if __name__ == "__main__":
    unittest.main()
