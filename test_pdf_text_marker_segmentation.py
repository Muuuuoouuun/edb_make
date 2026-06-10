import tempfile
import unittest
from pathlib import Path

import fitz

from build_structured_page_json import build_page_model
from page_repair import build_ai_fallback_config
import preprocess as preprocess_module
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
                deskew=True,
                crop_margins=True,
            )[0]
            self.assertFalse(prepared.metadata.get("deskewed"))
            self.assertEqual("pdf_text_layer", prepared.metadata.get("deskew_skipped_reason"))
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

    def test_pdf_render_uses_external_pymupdf_when_module_missing(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            pdf_path = Path(temp_dir) / "single_problem.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=240)
            page.insert_text((48, 80), "1. problem stem", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            original_fitz = preprocess_module.fitz
            preprocess_module.fitz = None
            try:
                pages = preprocess_module.render_pdf_pages(
                    pdf_path,
                    Path(temp_dir) / "rendered",
                    dpi=72,
                )
            finally:
                preprocess_module.fitz = original_fitz

            self.assertEqual(1, len(pages))
            self.assertTrue(Path(pages[0].normalized_path).exists())
            self.assertEqual("external_pymupdf", pages[0].metadata.get("pdf_renderer"))
            self.assertEqual(1, len(pages[0].metadata.get("pdf_problem_markers") or []))


if __name__ == "__main__":
    unittest.main()
