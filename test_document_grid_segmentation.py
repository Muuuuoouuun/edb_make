import unittest

from PIL import Image, ImageDraw

from segment import segment_page
from structured_schema import Subject


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


if __name__ == "__main__":
    unittest.main()
