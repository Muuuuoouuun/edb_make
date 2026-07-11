import unittest

from PIL import Image, ImageDraw

from segment import _reassign_single_column_entries_by_visual_columns, segment_page
from structured_schema import Box, Subject


class TestDocumentGridSegmentation(unittest.TestCase):
    def test_two_column_three_row_grid_with_center_rule_and_highlight(self):
        image = Image.new("RGB", (600, 780), "white")
        draw = ImageDraw.Draw(image)

        # Center rule like a workbook scan or UI overlay.
        draw.line((300, 0, 300, 780), fill=(190, 80, 60), width=2)
        # Light selection fill should not become "ink" and merge rows.
        draw.rectangle((8, 250, 292, 500), fill=(225, 228, 250))

        rows = [(0, 250), (250, 510), (510, 780)]
        cols = [(0, 292), (308, 600)]
        number = 1
        for col_left, col_right in cols:
            for row_top, row_bottom in rows:
                draw.text((col_left + 20, row_top + 24), f"{number}.", fill=(30, 120, 150))
                draw.text((col_left + 62, row_top + 28), "question stem text", fill=(20, 20, 20))
                for offset in (54, 72, 90):
                    draw.rectangle((col_left + 62, row_top + offset, col_right - 36, row_top + offset + 3), fill=(20, 20, 20))
                draw.rectangle((col_left + 96, row_top + 86, col_right - 96, row_top + 150), outline=(80, 80, 80), width=2)
                draw.line((col_left + 110, row_top + 150, col_right - 110, row_top + 104), fill=(80, 80, 80), width=2)
                draw.text((col_left + 24, row_bottom - 58), "①  a      ②  b", fill=(20, 20, 20))
                draw.text((col_left + 24, row_bottom - 34), "③  c      ④  d", fill=(20, 20, 20))
                draw.rectangle((col_left + 24, row_bottom - 18, col_right - 42, row_bottom - 15), fill=(20, 20, 20))
                number += 1

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"document_like": True}
                self.source_path = "synthetic-grid.png"

        page = segment_page(Source(image), page_id="grid", subject=Subject.MATH)

        self.assertEqual(2, page.metadata.get("column_count"))
        self.assertEqual(6, len(page.blocks))
        self.assertEqual([1, 1, 1, 2, 2, 2], [block.metadata.get("column_index") for block in page.blocks])

    def test_last_document_problem_keeps_short_tail_choices(self):
        image = Image.new("RGB", (420, 760), "white")
        draw = ImageDraw.Draw(image)

        draw.text((32, 54), "1.", fill=(30, 120, 150))
        draw.text((72, 58), "first problem stem", fill=(20, 20, 20))
        for y in (92, 112, 132):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.rectangle((92, 168, 310, 270), outline=(30, 30, 30), width=2)
        draw.text((52, 318), "① a        ② b", fill=(20, 20, 20))
        draw.text((52, 342), "③ c        ④ d", fill=(20, 20, 20))

        draw.text((32, 432), "2.", fill=(30, 120, 150))
        draw.text((72, 436), "last problem stem", fill=(20, 20, 20))
        for y in (470, 490, 510):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.rectangle((92, 542, 310, 626), outline=(30, 30, 30), width=2)
        draw.text((52, 698), "① a        ② b", fill=(20, 20, 20))
        draw.text((52, 722), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"document_like": True}
                self.source_path = "synthetic-last-tail.png"

        page = segment_page(Source(image), page_id="last-tail", subject=Subject.MATH)

        self.assertEqual("document-bands", page.metadata.get("segmenter"))
        self.assertEqual(2, len(page.blocks))
        self.assertGreater(page.blocks[-1].bbox.bottom, 730)

    def test_last_document_problem_keeps_widely_spaced_bottom_choices(self):
        image = Image.new("RGB", (420, 800), "white")
        draw = ImageDraw.Draw(image)

        draw.text((32, 64), "1.", fill=(30, 120, 150))
        draw.text((72, 68), "first problem stem", fill=(20, 20, 20))
        for y in (104, 124, 144):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.rectangle((92, 184, 310, 286), outline=(30, 30, 30), width=2)
        draw.text((52, 340), "① a        ② b", fill=(20, 20, 20))
        draw.text((52, 364), "③ c        ④ d", fill=(20, 20, 20))

        draw.text((32, 448), "2.", fill=(30, 120, 150))
        draw.text((72, 452), "last problem stem", fill=(20, 20, 20))
        for y in (490, 510, 530):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.rectangle((92, 558, 310, 642), outline=(30, 30, 30), width=2)
        draw.text((52, 740), "① a        ② b", fill=(20, 20, 20))
        draw.text((52, 764), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"document_like": True}
                self.source_path = "synthetic-wide-tail-gap.png"

        page = segment_page(Source(image), page_id="wide-tail-gap", subject=Subject.MATH)

        self.assertEqual("document-bands", page.metadata.get("segmenter"))
        self.assertEqual(2, len(page.blocks))
        self.assertGreater(page.blocks[-1].bbox.bottom, 770)

    def test_last_document_problem_does_not_extend_to_center_footer_number(self):
        image = Image.new("RGB", (420, 800), "white")
        draw = ImageDraw.Draw(image)

        draw.text((32, 64), "1.", fill=(30, 120, 150))
        draw.text((72, 68), "first problem stem", fill=(20, 20, 20))
        for y in (104, 124, 144):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.text((52, 340), "① a        ② b", fill=(20, 20, 20))

        draw.text((32, 448), "2.", fill=(30, 120, 150))
        draw.text((72, 452), "last problem stem", fill=(20, 20, 20))
        for y in (490, 510, 530):
            draw.rectangle((72, y, 350, y + 3), fill=(20, 20, 20))
        draw.rectangle((92, 558, 310, 642), outline=(30, 30, 30), width=2)
        draw.text((196, 758), "- 3 -", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"document_like": True}
                self.source_path = "synthetic-footer-tail.png"

        page = segment_page(Source(image), page_id="footer-tail", subject=Subject.MATH)

        self.assertEqual("document-bands", page.metadata.get("segmenter"))
        self.assertEqual(2, len(page.blocks))
        self.assertLess(page.blocks[-1].bbox.bottom, 710)

    def test_photo_like_image_with_gray_background_uses_visual_problem_markers(self):
        image = Image.new("RGB", (500, 760), (205, 205, 196))
        draw = ImageDraw.Draw(image)

        for top, number in [(54, 1), (360, 2)]:
            draw.rectangle((28, top - 12, 460, top + 250), fill=(238, 238, 230))
            draw.text((42, top), f"{number}.", fill=(20, 20, 20))
            draw.text((82, top + 4), "problem stem text", fill=(30, 30, 30))
            for y in (top + 38, top + 62, top + 86):
                draw.rectangle((82, y, 430, y + 4), fill=(30, 30, 30))
            draw.text((62, top + 188), "① a        ② b", fill=(30, 30, 30))
            draw.text((62, top + 212), "③ c        ④ d", fill=(30, 30, 30))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"source_type": "image"}
                self.source_path = "photo-like-upload.png"

        page = segment_page(Source(image), page_id="photo-upload", subject=Subject.MATH)

        self.assertEqual("visual-problem-markers", page.metadata.get("segmenter"))
        self.assertEqual(2, len(page.blocks))
        self.assertGreater(page.blocks[0].bbox.bottom, 285)
        self.assertLess(page.blocks[0].bbox.bottom, 350)
        self.assertGreater(page.blocks[1].bbox.bottom, 590)

    def test_visual_problem_markers_keep_distant_choices_before_next_number(self):
        image = Image.new("RGB", (420, 800), "white")
        draw = ImageDraw.Draw(image)

        for top, number in [(60, 1), (430, 2)]:
            draw.text((32, top), f"{number}.", fill=(20, 20, 20))
            draw.text((72, top + 4), "problem stem text", fill=(20, 20, 20))
            for y in (top + 38, top + 62, top + 86):
                draw.rectangle((72, y, 350, y + 4), fill=(20, 20, 20))
            draw.text((52, top + 220), "① a        ② b", fill=(20, 20, 20))
            draw.text((52, top + 244), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"document_like": True, "source_type": "image"}
                self.source_path = "black-number-upload.png"

        page = segment_page(Source(image), page_id="black-number-upload", subject=Subject.MATH)

        self.assertEqual("visual-problem-markers", page.metadata.get("segmenter"))
        self.assertEqual(2, len(page.blocks))
        self.assertGreater(page.blocks[0].bbox.bottom, 315)
        self.assertLess(page.blocks[0].bbox.bottom, 415)
        self.assertGreater(page.blocks[1].bbox.bottom, 685)

    def test_two_column_image_upload_single_marker_per_column_uses_visual_problem_markers(self):
        image = Image.new("RGB", (800, 520), "white")
        draw = ImageDraw.Draw(image)

        for left, number in [(32, 1), (432, 2)]:
            top = 60
            draw.text((left, top), f"{number}.", fill=(20, 20, 20))
            draw.text((left + 40, top + 4), "problem stem text", fill=(20, 20, 20))
            for y in (top + 38, top + 62, top + 86):
                draw.rectangle((left + 40, y, left + 330, y + 4), fill=(20, 20, 20))
            draw.text((left + 20, top + 220), "① a        ② b", fill=(20, 20, 20))
            draw.text((left + 20, top + 244), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {"source_type": "image"}
                self.source_path = "two-column-single-marker-upload.png"

        page = segment_page(Source(image), page_id="two-column-upload", subject=Subject.MATH)

        self.assertEqual("visual-problem-markers", page.metadata.get("segmenter"))
        self.assertEqual(2, page.metadata.get("column_count"))
        self.assertEqual(2, len(page.blocks))
        self.assertEqual([1, 2], [block.metadata.get("column_index") for block in page.blocks])
        self.assertGreater(page.blocks[0].bbox.bottom, 315)
        self.assertGreater(page.blocks[1].bbox.bottom, 315)

    def test_single_column_entries_with_visual_two_column_top_inversion_are_reassigned(self):
        content_box = Box.from_points(18, 15, 848, 642)
        entries = [
            (Box.from_points(4, 1, 439, 324), 1, 1, 2, False),
            (Box.from_points(427, 9, 862, 334), 1, 2, 2, False),
            (Box.from_points(19, 390, 423, 639), 2, 1, 2, False),
            (Box.from_points(428, 372, 861, 642), 2, 2, 2, False),
        ]

        groups, columns, changed = _reassign_single_column_entries_by_visual_columns(
            [entries],
            [content_box],
            content_box=content_box,
            page_height=652,
        )

        self.assertTrue(changed)
        self.assertEqual(2, len(columns))
        self.assertEqual(2, len(groups))
        self.assertEqual([1, 390], [int(entry[0].top) for entry in groups[0]])
        self.assertEqual([9, 372], [int(entry[0].top) for entry in groups[1]])


if __name__ == "__main__":
    unittest.main()
