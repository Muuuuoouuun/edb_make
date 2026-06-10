import tempfile
import unittest
from pathlib import Path

import fitz

from build_structured_page_json import build_page_model
from page_repair import build_ai_fallback_config
from preprocess import prepare_source_pages
from segment import segment_page
from structured_schema import Subject


class TestPdfTextMarkerSegmentation(unittest.TestCase):
    def test_pdf_problem_markers_drive_problem_count_without_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "two_column_exam.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            for number, x, y in ((1, 60, 120), (2, 60, 430), (3, 330, 120), (4, 330, 430)):
                page.insert_text((x, y), f"{number}. problem stem", fontsize=14)
                page.draw_rect(fitz.Rect(x + 35, y + 50, x + 180, y + 140), color=(0, 0, 0), width=1)
                page.insert_text((x, y + 210), "① a   ② b   ③ c", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=False,
                crop_margins=True,
            )[0]
            segmented = segment_page(prepared, page_id=prepared.page_id, subject=Subject.MATH)

            self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
            self.assertEqual(4, len(segmented.blocks))
            self.assertEqual([1, 2, 3, 4], [block.metadata.get("problem_number") for block in segmented.blocks])

            page_model = build_page_model(
                prepared,
                subject=Subject.MATH,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )
            self.assertEqual(4, len(page_model.problems))


if __name__ == "__main__":
    unittest.main()
