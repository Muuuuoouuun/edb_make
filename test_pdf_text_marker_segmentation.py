import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

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

    def test_pdf_problem_markers_ignore_chrome_print_date_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "chrome_header.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=400)
            page.insert_text((8, 18), "26. 6. 13. 오후 12:51", fontsize=8)
            page.insert_text((48, 120), "1. real problem stem", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            pages = preprocess_module.render_pdf_pages(
                pdf_path,
                Path(temp_dir) / "rendered",
                dpi=72,
            )

            markers = pages[0].metadata.get("pdf_problem_markers") or []
            self.assertEqual([1], [marker.get("number") for marker in markers])

    def test_pdf_problem_markers_ignore_decimal_measurement_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "decimal_measurement.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=400)
            page.insert_text((48, 120), "13. real problem stem", fontsize=14)
            page.insert_text((180, 220), "3.4 ㎛", fontsize=12)
            page.insert_text((48, 300), "14. next problem stem", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            pages = preprocess_module.render_pdf_pages(
                pdf_path,
                Path(temp_dir) / "rendered",
                dpi=72,
            )

            markers = pages[0].metadata.get("pdf_problem_markers") or []
            self.assertEqual([13, 14], [marker.get("number") for marker in markers])

    def test_pdf_marker_last_problem_excludes_isolated_footer_page_number(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 120), "1. problem stem", fill=(20, 20, 20))
        draw.text((60, 320), "2. problem stem", fill=(20, 20, 20))
        draw.rectangle((95, 380, 245, 500), outline=(20, 20, 20), width=2)
        draw.text((286, 760), "- 3 -", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 1,
                            "text": "1. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 152, "bottom": 134},
                        },
                        {
                            "number": 2,
                            "text": "2. problem stem",
                            "bbox": {"left": 60, "top": 316, "right": 152, "bottom": 334},
                        },
                    ],
                }
                self.source_path = "synthetic-footer.pdf"

        segmented = segment_page(Source(image), page_id="footer-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(2, len(segmented.blocks))
        self.assertLess(segmented.blocks[-1].bbox.bottom, 735)

    def test_pdf_marker_last_problem_keeps_real_choices_near_page_bottom(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 120), "1. problem stem", fill=(20, 20, 20))
        draw.text((60, 320), "2. problem stem", fill=(20, 20, 20))
        draw.rectangle((95, 380, 245, 500), outline=(20, 20, 20), width=2)
        draw.text((70, 742), "① a        ② b", fill=(20, 20, 20))
        draw.text((70, 766), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 1,
                            "text": "1. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 152, "bottom": 134},
                        },
                        {
                            "number": 2,
                            "text": "2. problem stem",
                            "bbox": {"left": 60, "top": 316, "right": 152, "bottom": 334},
                        },
                    ],
                }
                self.source_path = "synthetic-bottom-choices.pdf"

        segmented = segment_page(Source(image), page_id="bottom-choices-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(2, len(segmented.blocks))
        self.assertGreater(segmented.blocks[-1].bbox.bottom, 780)

    def test_pdf_text_stem_markers_segment_without_problem_numbers(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 120), "다음 자료에 대한 설명으로 옳은 것은?", fill=(20, 20, 20))
        draw.rectangle((95, 170, 245, 250), outline=(20, 20, 20), width=2)
        draw.text((60, 340), "밑줄 친 ㉠에 대한 설명으로 옳은 것은?", fill=(20, 20, 20))
        draw.text((70, 520), "① a        ② b", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "hwp",
                    "pdf_problem_markers": [
                        {
                            "marker_kind": "text_stem",
                            "text": "다음 자료에 대한 설명으로 옳은 것은?",
                            "bbox": {"left": 60, "top": 116, "right": 260, "bottom": 134},
                        },
                        {
                            "marker_kind": "text_stem",
                            "text": "밑줄 친 ㉠에 대한 설명으로 옳은 것은?",
                            "bbox": {"left": 60, "top": 336, "right": 292, "bottom": 354},
                        },
                    ],
                }
                self.source_path = "synthetic-stems.pdf"

        segmented = segment_page(Source(image), page_id="stem-page", subject=Subject.SOCIAL)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(2, len(segmented.blocks))
        self.assertEqual([None, None], [block.metadata.get("problem_number") for block in segmented.blocks])
        self.assertEqual(
            ["text_stem", "text_stem"],
            [block.metadata.get("problem_number_source") for block in segmented.blocks],
        )

    def test_hwp_layout_markers_ignore_near_zero_height_problem_numbers(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((330, 96), "33. real problem stem", fill=(20, 20, 20))
        draw.rectangle((365, 160, 500, 250), outline=(20, 20, 20), width=2)
        draw.text((330, 320), "34. real problem stem", fill=(20, 20, 20))
        draw.rectangle((365, 380, 500, 480), outline=(20, 20, 20), width=2)

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "hwp",
                    "pdf_problem_markers": [
                        {
                            "number": 32,
                            "text": "32. hidden problem marker from split passage",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {
                                "left": 60,
                                "top": 791.5,
                                "right": 150,
                                "bottom": 792.0,
                            },
                        },
                        {
                            "number": 33,
                            "text": "33. real problem stem",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {"left": 330, "top": 92, "right": 450, "bottom": 112},
                        },
                        {
                            "number": 34,
                            "text": "34. real problem stem",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {"left": 330, "top": 316, "right": 450, "bottom": 336},
                        },
                    ],
                }
                self.source_path = "synthetic-hwp-layout.pdf"

        segmented = segment_page(Source(image), page_id="hwp-layout-page", subject=Subject.KOREAN)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual([33, 34], [block.metadata.get("problem_number") for block in segmented.blocks])
        self.assertEqual(1, segmented.metadata.get("ignored_tiny_pdf_marker_count"))
        self.assertEqual([2, 2], [block.metadata.get("column_index") for block in segmented.blocks])
        self.assertGreaterEqual(min(block.bbox.left for block in segmented.blocks), 300.0)
        self.assertGreaterEqual(min(block.bbox.height for block in segmented.blocks), 40.0)

    def test_hwp_layout_markers_ignore_off_page_problem_numbers(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((330, 96), "33. real problem stem", fill=(20, 20, 20))
        draw.rectangle((365, 160, 500, 250), outline=(20, 20, 20), width=2)
        draw.text((330, 320), "34. real problem stem", fill=(20, 20, 20))
        draw.rectangle((365, 380, 500, 480), outline=(20, 20, 20), width=2)

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "hwp",
                    "pdf_problem_markers": [
                        {
                            "number": 32,
                            "text": "32. off-page problem marker from split passage",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {
                                "left": 60,
                                "top": 812.0,
                                "right": 150,
                                "bottom": 850.0,
                            },
                        },
                        {
                            "number": 33,
                            "text": "33. real problem stem",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {"left": 330, "top": 92, "right": 450, "bottom": 112},
                        },
                        {
                            "number": 34,
                            "text": "34. real problem stem",
                            "marker_kind": "hwp_layout_number",
                            "bbox": {"left": 330, "top": 316, "right": 450, "bottom": 336},
                        },
                    ],
                }
                self.source_path = "synthetic-hwp-layout.pdf"

        segmented = segment_page(Source(image), page_id="hwp-off-page-layout", subject=Subject.KOREAN)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual([33, 34], [block.metadata.get("problem_number") for block in segmented.blocks])
        self.assertEqual(1, segmented.metadata.get("ignored_tiny_pdf_marker_count"))
        self.assertEqual([32], segmented.metadata.get("ignored_tiny_pdf_marker_numbers"))
        self.assertEqual([2, 2], [block.metadata.get("column_index") for block in segmented.blocks])
        self.assertGreaterEqual(min(block.bbox.left for block in segmented.blocks), 300.0)


if __name__ == "__main__":
    unittest.main()
