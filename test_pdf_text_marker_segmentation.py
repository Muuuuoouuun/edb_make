import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from build_problem_board_edb import build_problem_entries
from build_structured_page_json import build_page_model
from layout_template_schema import LayoutTemplate
from page_repair import build_ai_fallback_config
import preprocess as preprocess_module
from preprocess import prepare_source_pages
from segment import PDF_CHOICE_MARKERS, segment_page
from structured_schema import Subject


def _problem_block_ids(problem):
    return (
        list(problem.stem_block_ids)
        + list(problem.choice_block_ids)
        + list(problem.explanation_block_ids)
        + list(problem.figure_block_ids)
    )


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

    def test_pdf_passage_range_block_attaches_to_child_problems_without_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "passage_range_exam.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((48, 92), "[1~2] 다음 글을 읽고 물음에 답하시오.", fontsize=14)
            page.insert_text((48, 138), "shared passage first line", fontsize=12)
            page.insert_text((48, 170), "shared passage second line", fontsize=12)
            page.insert_text((48, 320), "1. first question", fontsize=14)
            page.insert_text((330, 320), "2. second question", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.KOREAN,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            by_number = {
                problem.metadata.get("problem_number"): problem
                for problem in page_model.problems
                if problem.metadata.get("problem_number") is not None
            }
            self.assertEqual({1, 2}, set(by_number))
            passage_fragments = [
                problem
                for problem in page_model.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            self.assertEqual(1, len(passage_fragments))
            shared_ids = by_number[1].metadata.get("shared_passage_block_ids")
            self.assertTrue(shared_ids)
            self.assertEqual(shared_ids, _problem_block_ids(passage_fragments[0]))
            self.assertEqual(shared_ids, by_number[2].metadata.get("shared_passage_block_ids"))

            shared_block = next(
                block for block in page_model.blocks if block.block_id == shared_ids[0]
            )
            first_problem_block = next(
                block for block in page_model.blocks if block.metadata.get("problem_number") == 1
            )
            self.assertEqual("pdf-passage-range", shared_block.metadata.get("segmenter"))
            self.assertLess(shared_block.bbox.top, first_problem_block.bbox.top)
            self.assertLess(shared_block.bbox.bottom, first_problem_block.bbox.top)

    def test_pdf_passage_range_block_can_attach_to_prior_column_child_markers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "cross_column_passage_range_exam.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((330, 120), "1. first question", fontsize=14)
            page.insert_text((330, 320), "2. second question", fontsize=14)
            page.insert_text((48, 540), "[1~2] 다음 글을 읽고 물음에 답하시오.", fontsize=14)
            page.insert_text((48, 590), "shared passage first line", fontsize=12)
            page.insert_text((48, 630), "shared passage second line", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.ENGLISH,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            by_number = {
                problem.metadata.get("problem_number"): problem
                for problem in page_model.problems
                if problem.metadata.get("problem_number") is not None
            }
            self.assertEqual({1, 2}, set(by_number))
            passage_fragments = [
                problem
                for problem in page_model.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            self.assertEqual(1, len(passage_fragments))
            shared_ids = by_number[1].metadata.get("shared_passage_block_ids")
            self.assertTrue(shared_ids)
            self.assertEqual(shared_ids, _problem_block_ids(passage_fragments[0]))
            self.assertEqual(shared_ids, by_number[2].metadata.get("shared_passage_block_ids"))

            shared_block = next(
                block for block in page_model.blocks if block.block_id == shared_ids[0]
            )
            first_problem_block = next(
                block for block in page_model.blocks if block.metadata.get("problem_number") == 1
            )
            self.assertEqual("pdf-passage-range", shared_block.metadata.get("segmenter"))
            self.assertGreater(shared_block.bbox.top, first_problem_block.bbox.top)

    def test_pdf_passage_range_stitches_following_column_before_child_questions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "two_column_continued_passage.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
            page.insert_text((48, 82), "[1~2] Read the passage and answer the questions.", fontsize=12)
            for row, y in enumerate(range(120, 730, 28), start=1):
                page.insert_text((48, y), f"left passage line {row:02d}", fontsize=11)
            for row, y in enumerate(range(82, 300, 28), start=1):
                page.insert_text((330, y), f"continued passage line {row:02d}", fontsize=11)
            page.insert_text((330, 350), "1. first question", fontsize=14)
            page.insert_text((342, 410), "① a   ② b   ③ c", fontsize=12)
            page.insert_text((330, 540), "2. second question", fontsize=14)
            page.insert_text((342, 600), "① a   ② b   ③ c", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.KOREAN,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            by_number = {
                problem.metadata.get("problem_number"): problem
                for problem in page_model.problems
                if problem.metadata.get("problem_number") is not None
            }
            self.assertEqual({1, 2}, set(by_number))
            passage = next(
                problem
                for problem in page_model.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            )
            shared_ids = _problem_block_ids(passage)
            self.assertEqual(2, len(shared_ids))
            self.assertEqual(shared_ids, by_number[1].metadata.get("shared_passage_block_ids"))
            self.assertEqual(shared_ids, by_number[2].metadata.get("shared_passage_block_ids"))
            shared_blocks = [
                next(block for block in page_model.blocks if block.block_id == block_id)
                for block_id in shared_ids
            ]
            self.assertEqual({1, 2}, {block.metadata.get("column_index") for block in shared_blocks})
            first_question = next(
                block for block in page_model.blocks if block.metadata.get("problem_number") == 1
            )
            right_fragment = next(
                block for block in shared_blocks if block.metadata.get("column_index") == 2
            )
            self.assertLess(right_fragment.bbox.bottom, first_question.bbox.top)

            entries = build_problem_entries(
                [prepared],
                [page_model],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )
            passage_entry = next(entry for entry in entries if entry.problem_id == passage.unit_id)
            with Image.open(passage_entry.crop_path) as stitched:
                self.assertLess(stitched.width, prepared.image.width * 0.7)
                self.assertGreater(stitched.height, prepared.image.height * 0.75)

    def test_pdf_passage_range_without_same_page_questions_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "passage_only_page.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
            page.insert_text((48, 82), "[31~34] Read the passage and answer the questions.", fontsize=12)
            for row, y in enumerate(range(120, 730, 28), start=1):
                page.insert_text((48, y), f"left passage line {row:02d}", fontsize=11)
            for row, y in enumerate(range(82, 730, 28), start=1):
                page.insert_text((330, y), f"right passage line {row:02d}", fontsize=11)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.KOREAN,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            passages = [
                problem
                for problem in page_model.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            self.assertEqual(1, len(passages))
            self.assertEqual({"start": 31, "end": 34}, passages[0].metadata.get("passage_range"))
            self.assertEqual(2, len(_problem_block_ids(passages[0])))
            self.assertEqual("pdf-passage-ranges", page_model.metadata.get("segmenter"))

            # Passage rendering must not run the generic vertical-guide trim:
            # on real exam columns it can interpret final glyph strokes as a
            # guide and remove the rightmost 1-3 characters.
            with patch(
                "build_problem_board_edb._trim_edge_vertical_guides",
                side_effect=AssertionError("passage crop must preserve horizontal bounds"),
            ), patch(
                "build_problem_board_edb._trim_edge_attached_page_chrome",
                side_effect=AssertionError("passage crop must not trim edge-adjacent glyphs"),
            ):
                entries = build_problem_entries(
                    [prepared],
                    [page_model],
                    Path(temp_dir) / "out",
                    LayoutTemplate(name="academy-default"),
                )
            passage_entry = next(
                entry for entry in entries if entry.problem_id == passages[0].unit_id
            )
            shared_blocks = [
                block
                for block in page_model.blocks
                if block.block_id in set(_problem_block_ids(passages[0]))
            ]
            with Image.open(passage_entry.crop_path) as preserved_crop:
                self.assertGreaterEqual(
                    preserved_crop.width,
                    max(round(block.bbox.width) for block in shared_blocks) + 40,
                )

    def test_pdf_passage_range_prevents_exw_from_becoming_example_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "exw_passage_page.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((48, 82), "10. preceding question", fontsize=14)
            page.insert_text((48, 280), "[11~15] Read the passage and answer the questions.", fontsize=12)
            page.insert_text((48, 330), "shared passage first line", fontsize=11)
            page.insert_text((330, 82), "EXW means Ex Works in international trade.", fontsize=11)
            page.insert_text((330, 120), "continued passage line", fontsize=11)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.KOREAN,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            self.assertEqual("pdf-text-markers", page_model.metadata.get("segmenter"))
            self.assertIn(10, {
                problem.metadata.get("problem_number")
                for problem in page_model.problems
            })
            passages = [
                problem
                for problem in page_model.problems
                if problem.metadata.get("passage_role") == "passage_fragment"
            ]
            self.assertEqual(1, len(passages))
            self.assertEqual({"start": 11, "end": 15}, passages[0].metadata.get("passage_range"))

    def test_pdf_problem_markers_ignore_nested_low_number_procedure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "nested_procedure.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((48, 82), "7. seventh question", fontsize=14)
            page.insert_text((48, 260), "8. eighth question", fontsize=14)
            page.insert_text((72, 350), "1. first procedure step", fontsize=11)
            page.insert_text((72, 400), "2. second procedure step", fontsize=11)
            page.insert_text((330, 82), "9. ninth question", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.KOREAN,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            self.assertEqual(
                [7, 8, 9],
                [
                    problem.metadata.get("problem_number")
                    for problem in page_model.problems
                    if problem.metadata.get("problem_number") is not None
                ],
            )
            self.assertEqual(2, page_model.metadata.get("pdf_nested_enumeration_marker_count"))

    def test_pdf_passage_range_block_stops_before_cross_column_child_questions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "passage_range_cross_column_children.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((48, 92), "[1~2] 다음 글을 읽고 물음에 답하시오.", fontsize=14)
            page.insert_text((48, 138), "shared passage first line", fontsize=12)
            page.insert_text((48, 170), "shared passage second line", fontsize=12)
            page.insert_text((330, 220), "1. first question in right column", fontsize=14)
            page.insert_text((330, 360), "2. second question in right column", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.ENGLISH,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            shared_block = next(
                block for block in page_model.blocks if block.metadata.get("segmenter") == "pdf-passage-range"
            )
            first_problem_block = next(
                block for block in page_model.blocks if block.metadata.get("problem_number") == 1
            )
            self.assertLess(shared_block.bbox.bottom, first_problem_block.bbox.top)

    def test_pdf_workbook_example_markers_ignore_section_headings_and_footer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "math_workbook_examples.pdf"
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((32, 54), "1. 삼각비", fontsize=18)
            page.insert_text((250, 118), "#1. 삼각비의 뜻", fontsize=14)
            page.insert_text((32, 220), "ex) 다음 그림에서 x의 값을 구하시오.", fontsize=14)
            page.draw_rect(fitz.Rect(90, 270, 300, 390), color=(0, 0, 0), width=1)
            page.insert_text((120, 330), "45°", fontsize=14)
            page.insert_text((250, 440), "#2. 특수각", fontsize=14)
            page.insert_text((32, 520), "ex) 다음 표를 완성하시오.", fontsize=14)
            page.draw_rect(fitz.Rect(90, 570, 360, 680), color=(0, 0, 0), width=1)
            page.insert_text((32, 760), "중3-2 수학", fontsize=12)
            page.insert_text((280, 760), "- 1 -", fontsize=12)
            page.insert_text((455, 760), "YouTube - 친절한카수박", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            prepared = prepare_source_pages(
                pdf_path,
                pdf_dpi=144,
                detect_perspective=False,
                deskew=True,
                crop_margins=True,
            )[0]
            page_model = build_page_model(
                prepared,
                subject=Subject.MATH,
                ocr_mode="none",
                ai_config=build_ai_fallback_config(mode="off"),
            )

            self.assertEqual("pdf-example-markers", page_model.metadata.get("segmenter"))
            self.assertEqual(2, len(page_model.problems))
            self.assertEqual(
                [None, None],
                [problem.metadata.get("problem_number") for problem in page_model.problems],
            )
            for problem in page_model.problems:
                self.assertTrue(problem.figure_block_ids)

            footer_top = prepared.image.height * 0.9
            self.assertTrue(all(block.bbox.bottom < footer_top for block in page_model.blocks))

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

    def test_external_pymupdf_candidates_include_local_posix_venv(self):
        candidates = preprocess_module._iter_external_pymupdf_python_candidates()
        expected = Path(preprocess_module.__file__).resolve().parent / ".venv" / "bin" / "python"

        self.assertIn(expected, candidates)

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

    def test_pdf_problem_markers_keep_year_started_problem_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "year_problem_title.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=420)
            page.insert_text((48, 120), "9. 2024 Grand Butterfly Circus", fontsize=14)
            page.insert_text((48, 260), "10. next problem stem", fontsize=14)
            doc.save(pdf_path)
            doc.close()

            pages = preprocess_module.render_pdf_pages(
                pdf_path,
                Path(temp_dir) / "rendered",
                dpi=72,
            )

            markers = pages[0].metadata.get("pdf_problem_markers") or []
            self.assertEqual([9, 10], [marker.get("number") for marker in markers])

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

    def test_pdf_marker_choice_trim_keeps_thin_math_graph_below_choices(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        choice_line = "    ".join(PDF_CHOICE_MARKERS)
        draw.text((60, 80), "9. graph problem stem", fill=(20, 20, 20))
        draw.text((72, 150), "choose the matching graph", fill=(20, 20, 20))
        draw.text((72, 230), choice_line, fill=(20, 20, 20))
        draw.line((155, 346, 445, 346), fill=(20, 20, 20), width=2)
        draw.line((300, 332, 300, 360), fill=(20, 20, 20), width=2)
        draw.line((220, 354, 380, 338), fill=(20, 20, 20), width=2)

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 9,
                            "text": "9. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "9. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        },
                        {
                            "text": "choose the matching graph",
                            "bbox": {"left": 72, "top": 146, "right": 230, "bottom": 164},
                        },
                        {
                            "text": choice_line,
                            "bbox": {"left": 72, "top": 226, "right": 360, "bottom": 244},
                        },
                    ],
                }
                self.source_path = "synthetic-choice-thin-graph-tail.pdf"

        segmented = segment_page(Source(image), page_id="choice-thin-graph-tail-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertTrue(block.metadata.get("choice_bottom_trimmed"))
        self.assertTrue(block.metadata.get("choice_visual_tail_attached"))
        self.assertGreater(block.bbox.bottom, 375)

    def test_pdf_marker_choice_visual_tail_ignores_column_rule_below_choices(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        choice_line = "    ".join(PDF_CHOICE_MARKERS)
        draw.text((60, 80), "10. graph problem stem", fill=(20, 20, 20))
        draw.text((72, 150), "choose the matching graph", fill=(20, 20, 20))
        draw.text((72, 230), choice_line, fill=(20, 20, 20))
        draw.line((155, 346, 445, 346), fill=(20, 20, 20), width=2)
        draw.line((300, 332, 300, 360), fill=(20, 20, 20), width=2)
        draw.line((220, 354, 380, 338), fill=(20, 20, 20), width=2)
        draw.line((540, 0, 540, 799), fill=(20, 20, 20), width=2)

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 10,
                            "text": "10. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "10. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        },
                        {
                            "text": "choose the matching graph",
                            "bbox": {"left": 72, "top": 146, "right": 230, "bottom": 164},
                        },
                        {
                            "text": choice_line,
                            "bbox": {"left": 72, "top": 226, "right": 360, "bottom": 244},
                        },
                    ],
                }
                self.source_path = "synthetic-choice-column-rule-tail.pdf"

        segmented = segment_page(Source(image), page_id="choice-column-rule-tail-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertTrue(block.metadata.get("choice_bottom_trimmed"))
        self.assertTrue(block.metadata.get("choice_visual_tail_attached"))
        self.assertGreater(block.bbox.bottom, 375)
        self.assertLess(block.bbox.bottom, 450)

    def test_pdf_marker_choice_visual_tail_ignores_detached_footer(self):
        image = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        choice_line = "    ".join(PDF_CHOICE_MARKERS)
        draw.text((60, 80), "11. graph problem stem", fill=(20, 20, 20))
        draw.text((72, 150), "choose the matching graph", fill=(20, 20, 20))
        draw.text((72, 230), choice_line, fill=(20, 20, 20))
        draw.line((155, 346, 445, 346), fill=(20, 20, 20), width=2)
        draw.line((300, 332, 300, 360), fill=(20, 20, 20), width=2)
        draw.line((220, 354, 380, 338), fill=(20, 20, 20), width=2)
        draw.text((500, 760), "11 / 20", fill=(20, 20, 20))

        class Source:
            def __init__(self, source_image):
                self.image = source_image
                self.metadata = {
                    "source_type": "pdf",
                    "pdf_problem_markers": [
                        {
                            "number": 11,
                            "text": "11. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        }
                    ],
                    "pdf_text_lines": [
                        {
                            "text": "11. graph problem stem",
                            "bbox": {"left": 60, "top": 76, "right": 220, "bottom": 94},
                        },
                        {
                            "text": "choose the matching graph",
                            "bbox": {"left": 72, "top": 146, "right": 230, "bottom": 164},
                        },
                        {
                            "text": choice_line,
                            "bbox": {"left": 72, "top": 226, "right": 360, "bottom": 244},
                        },
                    ],
                }
                self.source_path = "synthetic-choice-detached-footer-tail.pdf"

        segmented = segment_page(Source(image), page_id="choice-detached-footer-tail-page", subject=Subject.MATH)

        self.assertEqual("pdf-text-markers", segmented.metadata.get("segmenter"))
        self.assertEqual(1, len(segmented.blocks))
        block = segmented.blocks[0]
        self.assertTrue(block.metadata.get("choice_bottom_trimmed"))
        self.assertTrue(block.metadata.get("choice_visual_tail_attached"))
        self.assertGreater(block.bbox.bottom, 375)
        self.assertLess(block.bbox.bottom, 450)

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
