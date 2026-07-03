import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from build_structured_page_json import build_page_model
from page_repair import build_ai_fallback_config
import preprocess as preprocess_module
from preprocess import prepare_source_pages
from segment import PDF_CHOICE_MARKERS, segment_page
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
            self.assertGreater(len(prepared.metadata.get("pdf_text_lines") or []), 0)
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

    def test_single_right_column_pdf_marker_does_not_include_left_passage(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.line((300, 72, 300, 728), fill=(20, 20, 20), width=2)
        for y in range(110, 520, 30):
            draw.text((58, y), "left passage text", fill=(20, 20, 20))
        draw.text((330, 120), "4. problem stem", fill=(20, 20, 20))
        draw.text((342, 190), "① a        ② b", fill=(20, 20, 20))
        draw.text((342, 222), "③ c        ④ d", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 4,
                            "text": "4. problem stem",
                            "bbox": {"left": 330, "top": 116, "right": 430, "bottom": 134},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "4. problem stem",
                            "bbox": {"left": 330, "top": 116, "right": 430, "bottom": 134},
                        },
                        {
                            "text": "① a        ② b",
                            "bbox": {"left": 342, "top": 186, "right": 430, "bottom": 204},
                        },
                        {
                            "text": "③ c        ④ d",
                            "bbox": {"left": 342, "top": 218, "right": 430, "bottom": 236},
                        },
                    ],
                }
                self.source_path = "synthetic-right-column.pdf"

        segmented = segment_page(Source(image), page_id="right-column-page", subject=Subject.KOREAN)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertEqual(2, block.metadata.get("column_index"))
        self.assertTrue(block.metadata.get("visual_column_bounds_used"))
        self.assertGreater(block.bbox.left, 285)
        self.assertLess(block.bbox.width, 315)

    def test_pdf_marker_terminal_problem_stops_after_last_choice_text_line(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 120), "13. problem stem", fill=(20, 20, 20))
        draw.text((72, 190), "① a        ② b", fill=(20, 20, 20))
        draw.text((72, 222), "③ c        ④ d", fill=(20, 20, 20))
        draw.text((92, 254), "continued choice text", fill=(20, 20, 20))
        for y in range(430, 720, 30):
            draw.text((58, y), "following passage should not be included", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 13,
                            "text": "13. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 170, "bottom": 134},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "13. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 170, "bottom": 134},
                        },
                        {
                            "text": "① a        ② b",
                            "bbox": {"left": 72, "top": 186, "right": 180, "bottom": 204},
                        },
                        {
                            "text": "③ c        ④ d",
                            "bbox": {"left": 72, "top": 218, "right": 180, "bottom": 236},
                        },
                        {
                            "text": "continued choice text",
                            "bbox": {"left": 92, "top": 250, "right": 230, "bottom": 268},
                        },
                        {
                            "text": "following passage should not be included",
                            "bbox": {"left": 58, "top": 426, "right": 360, "bottom": 444},
                        },
                    ],
                }
                self.source_path = "synthetic-terminal-choice.pdf"

        segmented = segment_page(Source(image), page_id="terminal-choice-page", subject=Subject.KOREAN)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertTrue(block.metadata.get("choice_bottom_trimmed"))
        self.assertGreater(block.bbox.bottom, 280)
        self.assertLess(block.bbox.bottom, 330)

    def test_pdf_marker_terminal_problem_trims_blank_tail_to_last_ink(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 120), "29. problem stem", fill=(20, 20, 20))
        draw.text((72, 190), "formula line", fill=(20, 20, 20))
        draw.text((72, 250), "answer request", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 29,
                            "text": "29. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 180, "bottom": 134},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "29. problem stem",
                            "bbox": {"left": 60, "top": 116, "right": 180, "bottom": 134},
                        },
                        {
                            "text": "formula line",
                            "bbox": {"left": 72, "top": 186, "right": 170, "bottom": 204},
                        },
                        {
                            "text": "answer request",
                            "bbox": {"left": 72, "top": 246, "right": 190, "bottom": 264},
                        },
                    ],
                }
                self.source_path = "synthetic-terminal-blank-tail.pdf"

        segmented = segment_page(Source(image), page_id="terminal-blank-tail-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertLess(block.bbox.bottom, 320)
        self.assertEqual(1, segmented.metadata.get("pdf_content_bottom_trim_count"))

    def test_pdf_marker_choice_trim_keeps_visual_diagram_below_choices(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        choice_line = "    ".join(PDF_CHOICE_MARKERS)
        draw.text((60, 80), "8. geometry problem stem", fill=(20, 20, 20))
        draw.text((72, 150), "given conditions", fill=(20, 20, 20))
        draw.text((72, 230), choice_line, fill=(20, 20, 20))
        draw.ellipse((155, 355, 445, 645), outline=(20, 20, 20), width=2)
        draw.arc((155, 430, 445, 555), 0, 180, fill=(20, 20, 20), width=2)
        draw.line((190, 450, 410, 560), fill=(20, 20, 20), width=2)

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 8,
                            "text": "8. geometry problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "8. geometry problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        },
                        {
                            "text": "given conditions",
                            "bbox": {"left": 72, "top": 146, "right": 200, "bottom": 164},
                        },
                        {
                            "text": choice_line,
                            "bbox": {"left": 72, "top": 226, "right": 360, "bottom": 244},
                        },
                    ],
                }
                self.source_path = "synthetic-choice-diagram-tail.pdf"

        segmented = segment_page(Source(image), page_id="choice-diagram-tail-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertTrue(block.metadata.get("choice_bottom_trimmed"))
        self.assertTrue(block.metadata.get("choice_visual_tail_attached"))
        self.assertGreater(block.bbox.bottom, 650)

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
