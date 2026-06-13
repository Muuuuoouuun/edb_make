import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlparse
from urllib.request import url2pathname

from PIL import Image, ImageDraw

import build_problem_board_edb as problem_board
from app_server import (
    _session_publish_history,
    _session_publish_summary,
    content_disposition_attachment,
    validate_edb_file,
)
from build_mvp_export import _render_problem_crops, build_ui_session as build_mvp_ui_session, run_export as run_mvp_export
from build_problem_board_edb import (
    ONE_PROBLEM_SLOT_HEIGHT_PAGES,
    PROCESSING_STEP_RECONSTRUCT,
    ProblemEntry,
    V1_DEFAULT_DISPLAY_WIDTH_PX,
    build_problem_entries,
    build_ui_session as build_problem_ui_session,
    build_image_only_records,
    run_problem_export,
    _pad_problem_crop_bottom,
    _hwp_conversion_has_pdf_problem_markers,
    _trim_edge_vertical_guides,
)
from edb_builder import CROP_FORMAT_V1
from layout_template_schema import LayoutTemplate
from preprocess import PreparedPage
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject


def _path_from_file_uri(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return Path(value)
    return Path(url2pathname(parsed.path))


class TestEdbPublishFlow(unittest.TestCase):
    def test_hwp_layout_problem_markers_count_as_marker_document_signal(self):
        self.assertTrue(
            _hwp_conversion_has_pdf_problem_markers(
                {
                    "source_type": "hwp",
                    "hwp_conversion_quality": {
                        "hwp_layout_problem_marker_count": 56,
                        "hwp_layout_problem_markers": [
                            {"pageIndex": 0, "number": 1},
                        ],
                    },
                }
            )
        )

    def test_build_problem_entries_parallelizes_cutout_generation_without_reordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            image = Image.new("RGB", (420, 520), "white")
            draw = ImageDraw.Draw(image)
            draw.text((52, 62), "1. first", fill="black")
            draw.text((52, 282), "2. second", fill="black")
            image.save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(420, 520),
            )
            blocks = [
                ContentBlock(
                    block_id="b-1",
                    block_type=BlockType.STEM,
                    bbox=Box(40, 40, 240, 130),
                    reading_order=0,
                    text="1. first",
                ),
                ContentBlock(
                    block_id="b-2",
                    block_type=BlockType.STEM,
                    bbox=Box(40, 260, 240, 130),
                    reading_order=1,
                    text="2. second",
                ),
            ]
            page = PageModel(
                page_id="page-1",
                width_px=420,
                height_px=520,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=blocks,
                problems=[
                    ProblemUnit(
                        unit_id="problem-1",
                        subject=Subject.KOREAN,
                        title="1.",
                        stem_block_ids=["b-1"],
                        metadata={"problem_number": 1},
                    ),
                    ProblemUnit(
                        unit_id="problem-2",
                        subject=Subject.KOREAN,
                        title="2.",
                        stem_block_ids=["b-2"],
                        metadata={"problem_number": 2},
                    ),
                ],
            )
            barrier = threading.Barrier(2)
            lock = threading.Lock()
            calls = 0

            def fake_cutout(crop, *, chalk_color=None):
                nonlocal calls
                with lock:
                    calls += 1
                    call_index = calls
                if call_index <= 2:
                    barrier.wait(timeout=0.8)
                return crop.convert("RGBA")

            with mock.patch.object(problem_board, "_extract_problem_cutout", side_effect=fake_cutout):
                entries = build_problem_entries(
                    [prepared],
                    [page],
                    root / "out",
                    LayoutTemplate(name="academy-default"),
                )

            self.assertEqual(["problem-1", "problem-2"], [entry.problem_id for entry in entries])
            self.assertTrue(all(entry.crop_path.exists() for entry in entries))
            self.assertTrue(all(entry.board_render_path.exists() for entry in entries))

    def _make_source_image(self, path: Path) -> None:
        image = Image.new("RGB", (860, 620), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((48, 48, 812, 572), outline="black", width=4)
        draw.text((80, 92), "1. Smoke problem", fill="black")
        draw.text((80, 160), "A generated EDB should validate.", fill="black")
        image.save(path)

    def _make_problem_entry(self, root: Path, name: str, bounds: Box) -> ProblemEntry:
        crop_path = root / f"{name}.png"
        Image.new("RGB", (int(bounds.width), int(bounds.height)), "white").save(crop_path)
        prepared = PreparedPage(
            page_id="page-1",
            source_path=str(root / "source.png"),
            page_number=1,
            image=Image.new("RGB", (900, 1200), "white"),
            original_size=(900, 1200),
        )
        return ProblemEntry(
            problem_id=name,
            title=name,
            problem_number=None,
            subject=Subject.UNKNOWN,
            source_page_id="page-1",
            source_path=str(root / "source.png"),
            prepared_page=prepared,
            bounds=bounds,
            crop_path=crop_path,
            board_render_path=crop_path,
            blocks=[],
            actual_height_pages=0.72,
            overflow_allowed=False,
            reading_heavy=False,
            risk_flags=[],
        )

    def test_generated_single_problem_edb_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=True,
            )

            validation = validate_edb_file(result["edb_path"], expected_min_records=1)
            self.assertEqual(validation["recordCountActual"], 1)
            self.assertGreater(validation["outerSize"], 0)

    def test_problem_export_writes_classin_handoff_manifest_for_manual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=True,
            )

            handoff_path = result["classin_handoff_path"]
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

            self.assertEqual("ready_for_classin_review", handoff["status"])
            self.assertEqual(str(result["edb_path"]), handoff["edbPath"])
            self.assertEqual(1, handoff["expectedRecordCount"])
            self.assertEqual(1, handoff["expectedCoreProblemCount"])
            self.assertTrue(handoff["manualReviewRequired"])
            self.assertIn("ClassIn에서 EDB 파일 열기", handoff["classinReviewChecklist"])
            self.assertTrue((root / "out" / "classin_handoff.md").is_file())

    def test_problem_ui_session_summarizes_duplicate_problem_number_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            crop = root / "crop.png"
            Image.new("RGB", (320, 240), "white").save(crop)

            placements = []
            for index, number in enumerate([35, 36, 37, 35, 36, 37], start=1):
                page_id = "page-choice-a" if index <= 3 else "page-choice-b"
                placements.append(
                    {
                        "problem_id": f"problem-{index}",
                        "title": f"{number}.",
                        "problem_number": number,
                        "subject": Subject.KOREAN,
                        "source_page_id": page_id,
                        "source_path": str(source),
                        "crop_path": str(crop),
                        "board_render_path": str(crop),
                        "bbox": {"left": 0, "top": 0, "width": 320, "height": 240},
                        "actual_content_height_pages": 0.8,
                        "overflow_allowed": False,
                        "start_y_pages": float(index),
                        "snapped_next_start_y_pages": float(index + 1),
                        "overflow_amount_pages": 0.0,
                        "overflow_violation": False,
                        "slot_span_count": 1,
                        "placement_x_ratio": 0.0,
                        "placement_y_ratio": 0.0,
                        "placement_scale_ratio": 1.0,
                        "record_mode": "image-only",
                        "text_record_count": 0,
                        "image_record_count": 1,
                        "risk_flags": [],
                    }
                )

            ui_session = build_problem_ui_session(
                [],
                placements,
                root / "out",
                None,
                [source],
                record_mode="image-only",
            )

            groups = ui_session["duplicateProblemNumberGroups"]
            self.assertEqual(1, len(groups))
            self.assertEqual(35, groups[0]["numberStart"])
            self.assertEqual(37, groups[0]["numberEnd"])
            self.assertEqual("35-37", groups[0]["numberLabel"])
            self.assertEqual(2, groups[0]["occurrencesPerNumber"])
            self.assertEqual(3, groups[0]["duplicateRecordCount"])
            self.assertEqual(6, groups[0]["totalRecordCount"])
            self.assertEqual(["page-choice-a", "page-choice-b"], groups[0]["sourcePageIds"])
            self.assertEqual(groups, ui_session["duplicate_problem_number_groups"])
            self.assertEqual(1, ui_session["duplicateProblemNumberGroupCount"])

    def test_problem_ui_session_flags_duplicate_problem_numbers_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            crop = root / "crop.png"
            Image.new("RGB", (320, 240), "white").save(crop)

            placements = []
            for index, number in enumerate([24, 25, 24], start=1):
                placements.append(
                    {
                        "problem_id": f"problem-{index}",
                        "title": f"{number}.",
                        "problem_number": number,
                        "subject": Subject.KOREAN,
                        "source_page_id": f"page-{index}",
                        "source_path": str(source),
                        "crop_path": str(crop),
                        "board_render_path": str(crop),
                        "bbox": {"left": 0, "top": 0, "width": 320, "height": 240},
                        "actual_content_height_pages": 0.8,
                        "overflow_allowed": False,
                        "start_y_pages": float(index),
                        "snapped_next_start_y_pages": float(index + 1),
                        "overflow_amount_pages": 0.0,
                        "overflow_violation": False,
                        "slot_span_count": 1,
                        "placement_x_ratio": 0.0,
                        "placement_y_ratio": 0.0,
                        "placement_scale_ratio": 1.0,
                        "record_mode": "image-only",
                        "text_record_count": 0,
                        "image_record_count": 1,
                        "risk_flags": [],
                    }
                )

            ui_session = build_problem_ui_session(
                [],
                placements,
                root / "out",
                None,
                [source],
                record_mode="image-only",
            )

            duplicate_problems = {
                problem["id"]: problem
                for problem in ui_session["problems"]
                if problem["problemNumber"] == 24
            }
            self.assertEqual({"problem-1", "problem-3"}, set(duplicate_problems))
            for problem in duplicate_problems.values():
                self.assertIn("duplicate_problem_number", problem["riskFlags"])
                self.assertEqual("check_needed", problem["reviewStatus"])

            unique_problem = next(problem for problem in ui_session["problems"] if problem["problemNumber"] == 25)
            self.assertNotIn("duplicate_problem_number", unique_problem["riskFlags"])
            self.assertEqual("normal", unique_problem["reviewStatus"])

    def test_ui_session_exposes_shared_passage_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_image = root / "page.png"
            crop_path = root / "crop.png"
            Image.new("RGB", (900, 1200), "white").save(source_image)
            Image.new("RGB", (600, 420), "white").save(crop_path)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source_image),
                page_number=1,
                image=Image.new("RGB", (900, 1200), "white"),
                original_size=(900, 1200),
            )
            page = PageModel(
                page_id="page-1",
                width_px=900,
                height_px=1200,
                subject=Subject.KOREAN,
                source_path=str(source_image),
                blocks=[
                    ContentBlock(
                        block_id="range-header",
                        block_type=BlockType.STEM,
                        bbox=Box(left=40, top=40, width=500, height=40),
                        reading_order=0,
                        text="[13~14] 다음 글을 읽고 물음에 답하시오.",
                    ),
                    ContentBlock(
                        block_id="shared-passage",
                        block_type=BlockType.STEM,
                        bbox=Box(left=40, top=90, width=500, height=360),
                        reading_order=1,
                        text="shared passage",
                    ),
                    ContentBlock(
                        block_id="q13",
                        block_type=BlockType.STEM,
                        bbox=Box(left=40, top=480, width=500, height=120),
                        reading_order=2,
                        text="13. question",
                    ),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title="13.",
                        stem_block_ids=["range-header", "shared-passage", "q13"],
                        metadata={
                            "problem_number": 13,
                            "passage_group_id": "page-1-passage-13-14",
                            "passage_range": {"start": 13, "end": 14},
                            "passage_role": "child_question",
                            "shared_passage_block_ids": ["range-header", "shared-passage"],
                            "passage_child_problem_numbers": [13, 14],
                        },
                    )
                ],
            )
            placement = {
                "problem_id": "page-1-problem-1",
                "title": "13.",
                "problem_number": 13,
                "subject": "국어",
                "source_page_id": "page-1",
                "source_path": str(source_image),
                "crop_path": str(crop_path),
                "board_render_path": str(crop_path),
                "actual_content_height_pages": 0.75,
                "overflow_allowed": True,
                "overflow_violation": False,
                "overflow_amount_pages": 0.0,
                "slot_span_count": 1,
                "start_y_pages": 0.0,
                "snapped_next_start_y_pages": 1.2,
                "placement_x_ratio": 0.0,
                "placement_y_ratio": 0.0,
                "placement_scale_ratio": 1.0,
                "record_mode": "image-only",
                "processing_step": "raw",
                "text_record_count": 0,
                "image_record_count": 1,
                "bbox": {"left": 40, "top": 40, "width": 500, "height": 560},
                "risk_flags": [],
            }

            ui_session = build_problem_ui_session(
                [prepared],
                [placement],
                root / "out",
                None,
                [source_image],
                record_mode="image-only",
                pages=[page],
            )

            problem = ui_session["problems"][0]
            self.assertEqual("page-1-passage-13-14", problem["passageGroupId"])
            self.assertEqual({"start": 13, "end": 14}, problem["passageRange"])
            self.assertEqual("child_question", problem["passageRole"])
            self.assertEqual(["range-header", "shared-passage"], problem["sharedPassageBlockIds"])
            self.assertEqual([13, 14], problem["passageChildProblemNumbers"])

    def test_ui_session_links_cross_page_passage_child_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_1_image = root / "page-1.png"
            page_2_image = root / "page-2.png"
            crop_13 = root / "crop-13.png"
            crop_15 = root / "crop-15.png"
            for path in (page_1_image, page_2_image):
                Image.new("RGB", (900, 1200), "white").save(path)
            for path in (crop_13, crop_15):
                Image.new("RGB", (600, 420), "white").save(path)

            prepared_pages = [
                PreparedPage(
                    page_id="page-1",
                    source_path=str(page_1_image),
                    page_number=1,
                    image=Image.new("RGB", (900, 1200), "white"),
                    original_size=(900, 1200),
                ),
                PreparedPage(
                    page_id="page-2",
                    source_path=str(page_2_image),
                    page_number=2,
                    image=Image.new("RGB", (900, 1200), "white"),
                    original_size=(900, 1200),
                ),
            ]
            pages = [
                PageModel(
                    page_id="page-1",
                    width_px=900,
                    height_px=1200,
                    subject=Subject.KOREAN,
                    source_path=str(page_1_image),
                    problems=[
                        ProblemUnit(
                            unit_id="page-1-problem-13",
                            subject=Subject.KOREAN,
                            title="13.",
                            metadata={
                                "problem_number": 13,
                                "passage_group_id": "page-1-passage-13-16",
                                "passage_range": {"start": 13, "end": 16},
                                "passage_role": "child_question",
                                "shared_passage_block_ids": ["range-header", "shared-passage-a"],
                                "passage_child_problem_numbers": [13, 14, 15, 16],
                            },
                        ),
                    ],
                ),
                PageModel(
                    page_id="page-2",
                    width_px=900,
                    height_px=1200,
                    subject=Subject.KOREAN,
                    source_path=str(page_2_image),
                    problems=[
                        ProblemUnit(
                            unit_id="page-2-problem-15",
                            subject=Subject.KOREAN,
                            title="15.",
                            metadata={"problem_number": 15},
                        ),
                    ],
                ),
            ]
            placements = [
                {
                    "problem_id": "page-1-problem-13",
                    "title": "13.",
                    "problem_number": 13,
                    "subject": "국어",
                    "source_page_id": "page-1",
                    "source_path": str(page_1_image),
                    "crop_path": str(crop_13),
                    "board_render_path": str(crop_13),
                    "actual_content_height_pages": 0.75,
                    "overflow_allowed": True,
                    "overflow_violation": False,
                    "overflow_amount_pages": 0.0,
                    "slot_span_count": 1,
                    "start_y_pages": 0.0,
                    "snapped_next_start_y_pages": 1.2,
                    "placement_x_ratio": 0.0,
                    "placement_y_ratio": 0.0,
                    "placement_scale_ratio": 1.0,
                    "record_mode": "image-only",
                    "processing_step": "raw",
                    "text_record_count": 0,
                    "image_record_count": 1,
                    "bbox": {"left": 40, "top": 40, "width": 500, "height": 560},
                    "risk_flags": [],
                },
                {
                    "problem_id": "page-2-problem-15",
                    "title": "15.",
                    "problem_number": 15,
                    "subject": "국어",
                    "source_page_id": "page-2",
                    "source_path": str(page_2_image),
                    "crop_path": str(crop_15),
                    "board_render_path": str(crop_15),
                    "actual_content_height_pages": 0.75,
                    "overflow_allowed": True,
                    "overflow_violation": False,
                    "overflow_amount_pages": 0.0,
                    "slot_span_count": 1,
                    "start_y_pages": 1.2,
                    "snapped_next_start_y_pages": 2.4,
                    "placement_x_ratio": 0.0,
                    "placement_y_ratio": 0.0,
                    "placement_scale_ratio": 1.0,
                    "record_mode": "image-only",
                    "processing_step": "raw",
                    "text_record_count": 0,
                    "image_record_count": 1,
                    "bbox": {"left": 40, "top": 40, "width": 500, "height": 560},
                    "risk_flags": [],
                },
            ]

            ui_session = build_problem_ui_session(
                prepared_pages,
                placements,
                root / "out",
                None,
                [page_1_image, page_2_image],
                record_mode="image-only",
                pages=pages,
            )

            problems_by_id = {problem["id"]: problem for problem in ui_session["problems"]}
            linked_problem = problems_by_id["page-2-problem-15"]
            self.assertEqual("page-1-passage-13-16", linked_problem["passageGroupId"])
            self.assertEqual({"start": 13, "end": 16}, linked_problem["passageRange"])
            self.assertEqual("child_question", linked_problem["passageRole"])
            self.assertEqual([13, 14, 15, 16], linked_problem["passageChildProblemNumbers"])
            self.assertEqual(["page-1", "page-2"], linked_problem["passageSourcePageIds"])
            self.assertTrue(linked_problem["passageContinuesAcrossPages"])

    def test_classin_handoff_manifest_explains_duplicate_problem_number_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            edb_path = root / "lesson.edb"
            source.write_bytes(b"hwp")
            edb_path.write_bytes(b"edb")
            duplicate_groups = [
                {
                    "numberStart": 35,
                    "numberEnd": 45,
                    "numberLabel": "35-45",
                    "occurrencesPerNumber": 2,
                    "duplicateRecordCount": 11,
                    "totalRecordCount": 22,
                    "sourcePageIds": ["page-13", "page-17"],
                    "problemIds": ["p35-a", "p35-b"],
                    "message": "문항 번호 35-45가 각 2회 등장합니다.",
                }
            ]

            json_path, md_path = problem_board.write_classin_handoff_manifest(
                root,
                source_paths=[source],
                edb_path=edb_path,
                ui_session={
                    "core_problem_count": 56,
                    "supplemental_item_count": 0,
                    "detected_problem_count": 56,
                    "source_page_count": 20,
                    "duplicate_problem_number_groups": duplicate_groups,
                    "reviewSummary": {},
                },
                summary={"record_count": 56, "record_mode": "image-only", "placements": []},
                template=LayoutTemplate(name="academy-default", board_page_count=112),
            )

            handoff = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")
            self.assertEqual("needs_attention_before_classin", handoff["status"])
            self.assertFalse(handoff["readyForClassIn"])
            self.assertEqual(duplicate_groups, handoff["duplicateProblemNumberGroups"])
            self.assertIn("35-45 x2", handoff["duplicateProblemNumberNote"])
            self.assertIn("Duplicate problem numbers: 35-45 x2", markdown)

    def test_classin_handoff_manifest_summarizes_passage_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            edb_path = root / "lesson.edb"
            source.write_bytes(b"hwp")
            edb_path.write_bytes(b"edb")

            json_path, md_path = problem_board.write_classin_handoff_manifest(
                root,
                source_paths=[source],
                edb_path=edb_path,
                ui_session={
                    "core_problem_count": 4,
                    "supplemental_item_count": 0,
                    "detected_problem_count": 4,
                    "source_page_count": 2,
                    "reviewSummary": {},
                    "problems": [
                        {
                            "id": "p13",
                            "title": "13.",
                            "problemNumber": 13,
                            "sourcePageId": "page-1",
                            "passageGroupId": "page-1-passage-13-16",
                            "passageRange": {"start": 13, "end": 16},
                            "passageRole": "child_question",
                            "passageChildProblemNumbers": [13, 14, 15, 16],
                            "passageSourcePageIds": ["page-1", "page-2"],
                            "passageContinuesAcrossPages": True,
                        },
                        {
                            "id": "p15",
                            "title": "15.",
                            "problemNumber": 15,
                            "sourcePageId": "page-2",
                            "passageGroupId": "page-1-passage-13-16",
                            "passageRange": {"start": 13, "end": 16},
                            "passageRole": "child_question",
                            "passageChildProblemNumbers": [13, 14, 15, 16],
                            "passageSourcePageIds": ["page-1", "page-2"],
                            "passageContinuesAcrossPages": True,
                        },
                        {
                            "id": "p16",
                            "title": "16.",
                            "problemNumber": 16,
                            "sourcePageId": "page-2",
                            "passageGroupId": "",
                            "metadata": {
                                "passage_group_id": "page-1-passage-13-16",
                                "passage_range": {"start": 13, "end": 16},
                                "passage_role": "child_question",
                                "passage_child_problem_numbers": [13, 14, 15, 16],
                                "passage_source_page_ids": ["page-1", "page-2"],
                                "passage_continues_across_pages": True,
                            },
                        },
                    ],
                },
                summary={"record_count": 4, "record_mode": "image-only", "placements": []},
                template=LayoutTemplate(name="academy-default", board_page_count=8),
            )

            handoff = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")
            self.assertEqual(1, handoff["passageGroupCount"])
            self.assertEqual(1, handoff["crossPagePassageGroupCount"])
            self.assertEqual(3, handoff["passageProblemCount"])
            self.assertEqual(
                [
                    {
                        "groupId": "page-1-passage-13-16",
                        "numberStart": 13,
                        "numberEnd": 16,
                        "numberLabel": "13-16",
                        "problemNumbers": [13, 15, 16],
                        "childProblemNumbers": [13, 14, 15, 16],
                        "problemIds": ["p13", "p15", "p16"],
                        "sourcePageIds": ["page-1", "page-2"],
                        "sourcePageCount": 2,
                        "problemCount": 3,
                        "continuesAcrossPages": True,
                        "roles": ["child_question"],
                        "message": "긴 지문 그룹 13-16이 2개 원본 페이지와 3개 감지 문항에 걸쳐 있습니다.",
                    }
                ],
                handoff["passageGroups"],
            )
            self.assertIn("## Passage Groups", markdown)
            self.assertIn("page-1-passage-13-16", markdown)
            self.assertIn("13-16", markdown)
            self.assertIn("cross-page", markdown)

    def test_classin_handoff_manifest_includes_asset_preflight_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            edb_path = root / "lesson.edb"
            tiny_crop = root / "tiny.png"
            blank_crop = root / "blank.png"
            missing_crop = root / "missing.png"
            source.write_bytes(b"hwp")
            edb_path.write_bytes(b"edb")
            Image.new("RGB", (90, 50), "white").save(tiny_crop)
            blank_image = Image.new("RGB", (800, 300), "white")
            ImageDraw.Draw(blank_image).line((780, 0, 780, 3), fill=(20, 20, 20), width=1)
            blank_image.save(blank_crop)

            json_path, md_path = problem_board.write_classin_handoff_manifest(
                root,
                source_paths=[source],
                edb_path=edb_path,
                ui_session={
                    "core_problem_count": 2,
                    "supplemental_item_count": 0,
                    "detected_problem_count": 2,
                    "source_page_count": 1,
                    "reviewSummary": {"riskFlagCounts": {"manual_check": 1}},
                    "problems": [
                        {
                            "id": "p-small",
                            "title": "1.",
                            "imagePath": tiny_crop.resolve().as_uri(),
                            "riskFlags": ["manual_check"],
                            "reviewStatus": "check_needed",
                        },
                        {
                            "id": "p-missing",
                            "title": "2.",
                            "imagePath": missing_crop.resolve().as_uri(),
                            "riskFlags": [],
                            "reviewStatus": "normal",
                        },
                        {
                            "id": "p-blank",
                            "title": "3.",
                            "imagePath": blank_crop.resolve().as_uri(),
                            "riskFlags": [],
                            "reviewStatus": "normal",
                        },
                    ],
                },
                summary={"record_count": 3, "record_mode": "image-only", "placements": []},
                template=LayoutTemplate(name="academy-default", board_page_count=50),
            )

            handoff = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")
            preflight = handoff["classinPreflight"]
            issue_types = {issue["type"] for issue in preflight["issues"]}
            self.assertEqual("needs_attention", preflight["status"])
            self.assertFalse(preflight["passed"])
            self.assertIn("small_problem_image", issue_types)
            self.assertIn("missing_problem_image", issue_types)
            self.assertIn("low_ink_problem_image", issue_types)
            self.assertIn("review_flags_remaining", issue_types)
            self.assertEqual(3, preflight["checkedProblemCount"])
            self.assertIn("ClassIn Preflight", markdown)
            self.assertIn("small_problem_image", markdown)
            self.assertIn("missing_problem_image", markdown)
            self.assertIn("low_ink_problem_image", markdown)

    def test_classin_preflight_flags_board_placement_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            edb_path = root / "lesson.edb"
            first_crop = root / "first.png"
            second_crop = root / "second.png"
            source.write_bytes(b"hwp")
            edb_path.write_bytes(b"edb")
            for path, label in ((first_crop, "13. long passage"), (second_crop, "14. child question")):
                image = Image.new("RGB", (640, 280), "white")
                draw = ImageDraw.Draw(image)
                for line in range(8):
                    draw.text((40, 32 + line * 26), f"{label} text line {line}", fill=(20, 20, 20))
                image.save(path)

            json_path, md_path = problem_board.write_classin_handoff_manifest(
                root,
                source_paths=[source],
                edb_path=edb_path,
                ui_session={
                    "core_problem_count": 2,
                    "supplemental_item_count": 0,
                    "detected_problem_count": 2,
                    "source_page_count": 1,
                    "reviewSummary": {},
                    "problems": [
                        {
                            "id": "p13",
                            "title": "13.",
                            "imagePath": first_crop.resolve().as_uri(),
                            "riskFlags": [],
                            "reviewStatus": "normal",
                            "startYPages": 0.0,
                            "actualHeightPages": 1.1,
                            "placementScaleRatio": 1.4,
                            "snappedNextStartYPages": 1.2,
                        },
                        {
                            "id": "p14",
                            "title": "14.",
                            "imagePath": second_crop.resolve().as_uri(),
                            "riskFlags": [],
                            "reviewStatus": "normal",
                            "startYPages": 1.2,
                            "actualHeightPages": 0.8,
                            "placementScaleRatio": 1.0,
                            "snappedNextStartYPages": 2.4,
                        },
                    ],
                },
                summary={"record_count": 2, "record_mode": "image-only", "placements": []},
                template=LayoutTemplate(name="academy-default", board_page_count=50),
            )

            handoff = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")
            preflight = handoff["classinPreflight"]
            overlap_issues = [
                issue for issue in preflight["issues"] if issue["type"] == "board_placement_overlap"
            ]
            self.assertEqual("needs_attention_before_classin", handoff["status"])
            self.assertFalse(handoff["readyForClassIn"])
            self.assertEqual(1, len(overlap_issues))
            self.assertEqual("p13", overlap_issues[0]["problemId"])
            self.assertEqual("p14", overlap_issues[0]["nextProblemId"])
            self.assertGreater(overlap_issues[0]["renderedBottomYPages"], overlap_issues[0]["nextStartYPages"])
            self.assertIn("Handoff status: `needs_attention_before_classin`", markdown)
            self.assertIn("board_placement_overlap", markdown)

    def test_korean_edb_filename_download_header_is_http_safe(self):
        header = content_disposition_attachment(
            "20260610_223707_1781098627053740000_고1_샘플_7f796ebe63.edb"
        )
        header.encode("latin-1")
        self.assertIn('filename="20260610_223707_1781098627053740000__1____7f796ebe63.edb"', header)
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%EA%B3%A01_%EC%83%98%ED%94%8C", header)

    def test_publish_summary_exposes_validated_edb_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edb_path = root / "lesson.edb"
            edb_path.write_bytes(b"placeholder")

            summary = _session_publish_summary(
                edb_path=edb_path,
                output_dir=root,
                edb_validation={
                    "outerSize": 1234,
                    "innerSize": 987,
                    "pageCountHint": 23,
                    "recordCountHint": 45,
                    "recordCountActual": 45,
                },
                record_count=45,
                published_at="2026-06-13T12:00:00+09:00",
            )

            self.assertTrue(summary["validated"])
            self.assertEqual(summary["statusLabel"], "검증 완료")
            self.assertEqual(summary["edbFileName"], "lesson.edb")
            self.assertEqual(summary["edbPath"], str(edb_path.resolve()))
            self.assertEqual(summary["outputDir"], str(root.resolve()))
            self.assertEqual(summary["recordCount"], 45)
            self.assertEqual(summary["recordCountActual"], 45)
            self.assertEqual(summary["pageCountHint"], 23)
            self.assertIn("/api/file?path=", summary["edbFileUri"])
            self.assertEqual(summary["publishedAt"], "2026-06-13T12:00:00+09:00")

    def test_publish_summary_exposes_classin_handoff_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edb_path = root / "lesson.edb"
            handoff_path = root / "classin_handoff.json"
            handoff_md_path = root / "classin_handoff.md"
            edb_path.write_bytes(b"placeholder")
            handoff_path.write_text("{}", encoding="utf-8")
            handoff_md_path.write_text("# check", encoding="utf-8")

            summary = _session_publish_summary(
                edb_path=edb_path,
                output_dir=root,
                edb_validation={
                    "outerSize": 1234,
                    "innerSize": 987,
                    "pageCountHint": 23,
                    "recordCountHint": 45,
                    "recordCountActual": 45,
                },
                record_count=45,
                classin_handoff_path=handoff_path,
                classin_handoff_markdown_path=handoff_md_path,
                published_at="2026-06-13T12:00:00+09:00",
            )

            self.assertEqual(str(handoff_path.resolve()), summary["classinHandoffPath"])
            self.assertEqual(str(handoff_md_path.resolve()), summary["classinHandoffMarkdownPath"])
            self.assertIn("/api/file?path=", summary["classinHandoffUri"])
            self.assertIn("/api/file?path=", summary["classinHandoffMarkdownUri"])

    def test_publish_summary_exposes_classin_preflight_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edb_path = root / "lesson.edb"
            edb_path.write_bytes(b"placeholder")
            preflight = {
                "status": "needs_attention",
                "passed": False,
                "issueCount": 2,
                "checkedProblemCount": 3,
                "issues": [{"type": "small_problem_image"}],
            }

            summary = _session_publish_summary(
                edb_path=edb_path,
                output_dir=root,
                edb_validation={
                    "outerSize": 1234,
                    "innerSize": 987,
                    "pageCountHint": 50,
                    "recordCountHint": 3,
                    "recordCountActual": 3,
                },
                record_count=3,
                classin_preflight=preflight,
                published_at="2026-06-13T12:00:00+09:00",
            )

            self.assertEqual(preflight, summary["classinPreflight"])
            self.assertEqual("needs_attention", summary["classinPreflightStatus"])
            self.assertEqual(2, summary["classinPreflightIssueCount"])
            self.assertFalse(summary["classinPreflightPassed"])

    def test_publish_summary_preserves_core_and_supplemental_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edb_path = root / "lesson.edb"
            edb_path.write_bytes(b"placeholder")

            summary = _session_publish_summary(
                edb_path=edb_path,
                output_dir=root,
                edb_validation={
                    "outerSize": 1234,
                    "innerSize": 987,
                    "pageCountHint": 92,
                    "recordCountHint": 46,
                    "recordCountActual": 46,
                },
                record_count=46,
                core_problem_count=45,
                supplemental_item_count=1,
                published_at="2026-06-13T12:00:00+09:00",
            )

            self.assertEqual(46, summary["recordCount"])
            self.assertEqual(45, summary["coreProblemCount"])
            self.assertEqual(1, summary["supplementalItemCount"])
            self.assertEqual("45문항 + 자료 1", summary["recordCountLabel"])
            self.assertEqual(45, summary["core_problem_count"])
            self.assertEqual(1, summary["supplemental_item_count"])
            self.assertEqual("45문항 + 자료 1", summary["record_count_label"])

    def test_publish_history_keeps_latest_first_and_limits_entries(self):
        current = {
            "edbFileName": "latest.edb",
            "edbPath": "/tmp/latest.edb",
            "publishedAt": "2026-06-13T12:05:00+09:00",
        }
        prior = [
            {"edbFileName": f"old-{index}.edb", "edbPath": f"/tmp/old-{index}.edb"}
            for index in range(1, 7)
        ]
        session = {"publish_history": prior}

        history = _session_publish_history(session, current, limit=5)

        self.assertEqual(
            ["latest.edb", "old-1.edb", "old-2.edb", "old-3.edb", "old-4.edb"],
            [item["edbFileName"] for item in history],
        )
        self.assertEqual("/tmp/latest.edb", history[0]["edbPath"])
        self.assertEqual(5, len(history))

    def test_publish_history_preserves_existing_summary_when_history_missing(self):
        current = {"edbFileName": "latest.edb", "edbPath": "/tmp/latest.edb"}
        previous = {"edbFileName": "previous.edb", "edbPath": "/tmp/previous.edb"}
        session = {"publishSummary": previous}

        history = _session_publish_history(session, current, limit=5)

        self.assertEqual(["latest.edb", "previous.edb"], [item["edbFileName"] for item in history])

    def test_v1_multi_problem_export_uses_one_problem_per_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [
                self._make_problem_entry(root, "problem-1", Box(0, 40, 380, 300)),
                self._make_problem_entry(root, "problem-2", Box(410, 40, 380, 300)),
            ]
            template = LayoutTemplate(
                name="academy-default",
                base_slot_height_pages=ONE_PROBLEM_SLOT_HEIGHT_PAGES,
            )

            _records, placements = build_image_only_records(
                entries,
                template,
                crop_format=CROP_FORMAT_V1,
            )

            self.assertEqual([0.0, 1.2], [item["start_y_pages"] for item in placements])
            self.assertEqual([1.2, 2.4], [item["snapped_next_start_y_pages"] for item in placements])
            self.assertEqual([1.0, 1.0], [item["placement_scale_ratio"] for item in placements])
            self.assertEqual(
                [V1_DEFAULT_DISPLAY_WIDTH_PX, V1_DEFAULT_DISPLAY_WIDTH_PX],
                [item["rendered_width_px"] for item in placements],
            )
            self.assertAlmostEqual(
                placements[0]["rendered_height_px"],
                V1_DEFAULT_DISPLAY_WIDTH_PX * (300 / 380),
                places=6,
            )

    def test_v1_reconstruct_step_exports_transparent_high_res_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._make_problem_entry(root, "problem-1", Box(0, 40, 380, 300))
            entry.processing_step = PROCESSING_STEP_RECONSTRUCT
            image = Image.open(entry.crop_path).convert("RGB")
            draw = ImageDraw.Draw(image)
            draw.text((24, 40), "1. Transparent export", fill="black")
            image.save(entry.crop_path)
            template = LayoutTemplate(
                name="academy-default",
                base_slot_height_pages=ONE_PROBLEM_SLOT_HEIGHT_PAGES,
            )

            records, placements = build_image_only_records(
                [entry],
                template,
                crop_format=CROP_FORMAT_V1,
            )

            self.assertIn(b"\x89PNG\r\n\x1a\n", records[0])
            self.assertEqual(placements[0]["processing_step"], PROCESSING_STEP_RECONSTRUCT)
            self.assertEqual(placements[0]["image_pixel_width"], 1330)
            self.assertGreater(placements[0]["image_pixel_width"], int(entry.bounds.width))
            self.assertEqual(placements[0]["rendered_width_px"], V1_DEFAULT_DISPLAY_WIDTH_PX)

    def test_problem_crops_use_same_column_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (1000, 1400), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(1000, 1400),
            )

            blocks: list[ContentBlock] = []
            problems: list[ProblemUnit] = []

            def add_problem(number: int, column: int, top: float, bottom: float, order: int) -> None:
                block_id = f"b-{number}"
                blocks.append(
                    ContentBlock(
                        block_id=block_id,
                        block_type=BlockType.STEM,
                        bbox=Box(80 + column * 460, top, 360, bottom - top),
                        reading_order=order,
                        text=f"{number}. problem",
                        metadata={"column_index": column, "question_band_index": order},
                    )
                )
                problems.append(
                    ProblemUnit(
                        unit_id=f"problem-{number}",
                        subject=Subject.MATH,
                        title=f"{number}.",
                        stem_block_ids=[block_id],
                        metadata={
                            "problem_number": number,
                            "column_index": column,
                            "question_band_index": order,
                        },
                    )
                )

            add_problem(7, 0, 100, 250, 0)
            add_problem(8, 0, 500, 650, 1)
            add_problem(9, 0, 900, 1050, 2)
            add_problem(10, 1, 100, 280, 3)
            add_problem(11, 1, 520, 894, 4)
            add_problem(12, 1, 900, 1080, 5)

            page = PageModel(
                page_id="page-1",
                width_px=1000,
                height_px=1400,
                subject=Subject.MATH,
                source_path=str(source),
                blocks=blocks,
                problems=problems,
            )

            entries = build_problem_entries(
                [prepared],
                [page],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )

            problem_11 = next(entry for entry in entries if entry.problem_number == 11)
            problem_12 = next(entry for entry in entries if entry.problem_number == 12)
            self.assertLessEqual(problem_11.bounds.bottom, 895)
            self.assertLessEqual(problem_12.bounds.top, 886)

    def test_marker_document_continuation_page_preserves_single_review_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (720, 960), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )
            blocks = [
                ContentBlock(
                    block_id="tail-1",
                    block_type=BlockType.STEM,
                    bbox=Box(80, 120, 560, 150),
                    reading_order=0,
                    text=None,
                    metadata={
                        "segmenter": "document-bands",
                        "column_index": 0,
                        "question_band_index": 0,
                        "source_band_index": 0,
                    },
                ),
                ContentBlock(
                    block_id="tail-2",
                    block_type=BlockType.CHOICE,
                    bbox=Box(95, 310, 520, 90),
                    reading_order=1,
                    text=None,
                    metadata={
                        "segmenter": "document-bands",
                        "column_index": 0,
                        "question_band_index": 0,
                        "source_band_index": 0,
                    },
                ),
            ]
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=blocks,
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title=None,
                        stem_block_ids=["tail-1"],
                        choice_block_ids=["tail-2"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_by_band",
                            "question_band_index": 0,
                            "column_index": 0,
                        },
                    )
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "pdf_problem_markers": [],
                    "pdf_text_marker_count": 0,
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )

            entries = build_problem_entries(
                [prepared],
                [page],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )

            self.assertEqual(1, len(entries))
            self.assertEqual("이어지는 자료", entries[0].title)
            self.assertIsNone(entries[0].problem_number)
            self.assertEqual("page-1-continuation", entries[0].problem_id)
            self.assertEqual(0.0, entries[0].bounds.left)
            self.assertEqual(0.0, entries[0].bounds.top)
            self.assertEqual(720.0, entries[0].bounds.width)
            self.assertEqual(960.0, entries[0].bounds.height)
            self.assertIn("marker_document_continuation", entries[0].risk_flags)
            self.assertEqual(["page-1-continuation"], [problem.unit_id for problem in page.problems])

    def test_mvp_export_preserves_marker_document_continuation_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (720, 960), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="tail-image",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 120, 560, 540),
                        reading_order=0,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 1,
                            "question_band_index": 1,
                            "source_band_index": 1,
                        },
                    )
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title=None,
                        figure_block_ids=["tail-image"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_grouping",
                            "question_band_index": 1,
                            "column_index": 1,
                        },
                    )
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "pdf_problem_markers": [],
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )

            crop_paths = _render_problem_crops([page], [prepared], root / "problem_crops")

            self.assertEqual(["page-1-continuation"], list(crop_paths))
            self.assertTrue(crop_paths["page-1-continuation"].exists())
            self.assertEqual(["page-1-continuation"], [problem.unit_id for problem in page.problems])
            self.assertEqual("이어지는 자료", page.problems[0].title)
            self.assertIn("marker_document_continuation", page.problems[0].metadata["risk_flags"])

    def test_mvp_export_keeps_fallback_crops_when_pdf_markers_are_sparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (720, 960), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 3,
                        "pdf_text_markers_reliable": False,
                    },
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.SOCIAL,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="fallback-image",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 120, 560, 540),
                        reading_order=0,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 1,
                            "question_band_index": 1,
                            "source_band_index": 1,
                        },
                    )
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.SOCIAL,
                        title=None,
                        figure_block_ids=["fallback-image"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_grouping",
                            "question_band_index": 1,
                            "column_index": 1,
                        },
                    )
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "pdf_problem_markers": [],
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 3,
                        "pdf_text_markers_reliable": False,
                    },
                },
            )

            crop_paths = _render_problem_crops([page], [prepared], root / "problem_crops")

            self.assertEqual(["page-1-problem-1"], list(crop_paths))
            self.assertTrue(crop_paths["page-1-problem-1"].exists())

    def test_mvp_export_skips_unnumbered_marker_document_continuation_without_fallback_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (720, 960), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(720, 960),
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.MATH,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="tail-image",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 120, 560, 120),
                        reading_order=0,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 0,
                            "question_band_index": 0,
                            "source_band_index": 0,
                        },
                    )
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.MATH,
                        title=None,
                        figure_block_ids=["tail-image"],
                        metadata={"grouping_source": "text_markers_unavailable"},
                    )
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "pdf_problem_markers": [],
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 30,
                        "pdf_text_markers_reliable": True,
                    },
                },
            )

            crop_paths = _render_problem_crops([page], [prepared], root / "problem_crops")

            self.assertEqual({}, crop_paths)

    def test_mvp_export_preserves_marker_document_continuation_before_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="tail-image",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 120, 560, 540),
                        reading_order=0,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 1,
                            "question_band_index": 1,
                            "source_band_index": 1,
                        },
                    )
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title=None,
                        figure_block_ids=["tail-image"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_grouping",
                            "question_band_index": 1,
                            "column_index": 1,
                        },
                    )
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "pdf_problem_markers": [],
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": True,
                        "pdf_text_marker_count": 45,
                    },
                },
            )

            with (
                mock.patch("build_mvp_export.prepare_source_pages", return_value=[prepared]),
                mock.patch("build_mvp_export.build_page_model", return_value=page),
            ):
                result = run_mvp_export(
                    source,
                    output_dir=root / "out",
                    subject_name="korean",
                    ocr="none",
                    sync_ui=False,
                )

            self.assertEqual(["page-1-continuation"], list(result["problem_crop_paths"]))
            self.assertEqual(1, result["ui_session"]["detected_problem_count"])
            self.assertEqual(0, result["ui_session"]["core_problem_count"])
            self.assertEqual(1, result["ui_session"]["supplemental_item_count"])
            self.assertEqual(1, len(result["ui_session"]["problems"]))
            self.assertEqual("이어지는 자료", result["ui_session"]["problems"][0]["title"])
            self.assertCountEqual(
                ["marker_document_continuation", "fallback_grouping"],
                result["ui_session"]["problems"][0]["riskFlags"],
            )
            pages_payload = json.loads((root / "out" / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual(["page-1-continuation"], [problem["unit_id"] for problem in pages_payload[0]["problems"]])
            self.assertTrue(pages_payload[0]["problems"][0]["metadata"]["marker_document_continuation"])
            self.assertEqual(["page-1-continuation"], result["ui_session"]["pages"][0]["problemIds"])

    def test_mvp_export_skips_hwp_template_instruction_fallback_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "school.hwp"
            source.write_bytes(b"hwp")
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "hwp_preview_text": (
                        "단어의 뜻이 옳게 짝지어진 것은?\n"
                        "개요 번호 모양 서식 적용되어 있습니다.\n"
                        "Ctrl+3 누르면 지시문(1., 2., 3.)\n"
                        "위 네모칸 표는 복사 붙여넣어서 사용하세요."
                    ),
                    "hwp_conversion_quality": {
                        "has_pdf_text_markers": False,
                        "pdf_text_marker_count": 0,
                        "pdf_text_markers_reliable": False,
                        "preferred_segmentation_path": "ocr_fallback",
                    },
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.ENGLISH,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="question-band",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 80, 520, 180),
                        reading_order=0,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 1,
                            "question_band_index": 1,
                            "source_band_index": 1,
                        },
                    ),
                    ContentBlock(
                        block_id="instruction-band",
                        block_type=BlockType.IMAGE,
                        bbox=Box(80, 360, 540, 260),
                        reading_order=1,
                        text=None,
                        metadata={
                            "segmenter": "document-bands",
                            "column_index": 1,
                            "question_band_index": 2,
                            "source_band_index": 2,
                        },
                    ),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.ENGLISH,
                        title=None,
                        figure_block_ids=["question-band"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_grouping",
                            "question_band_index": 1,
                            "column_index": 1,
                        },
                    ),
                    ProblemUnit(
                        unit_id="page-1-problem-2",
                        subject=Subject.ENGLISH,
                        title=None,
                        figure_block_ids=["instruction-band"],
                        metadata={
                            "fallback_grouping": True,
                            "grouping_source": "fallback_grouping",
                            "question_band_index": 2,
                            "column_index": 1,
                        },
                    ),
                ],
                metadata={
                    "source_type": "hwp",
                    "document_like": True,
                    "segmenter": "document-bands",
                    "hwp_preview_text": prepared.metadata["hwp_preview_text"],
                    "hwp_conversion_quality": prepared.metadata["hwp_conversion_quality"],
                },
            )

            with (
                mock.patch("build_mvp_export.prepare_source_pages", return_value=[prepared]),
                mock.patch("build_mvp_export.build_page_model", return_value=page),
            ):
                result = run_mvp_export(
                    source,
                    output_dir=root / "out",
                    subject_name="english",
                    ocr="none",
                    sync_ui=False,
                )

            self.assertEqual(["page-1-problem-1"], list(result["problem_crop_paths"]))
            self.assertEqual(["page-1-problem-1"], [problem["id"] for problem in result["ui_session"]["problems"]])
            pages_payload = json.loads((root / "out" / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual(["page-1-problem-1"], [problem["unit_id"] for problem in pages_payload[0]["problems"]])
            self.assertEqual(
                ["page-1-problem-2"],
                pages_payload[0]["metadata"]["template_instruction_problem_ids_skipped"],
            )
            with Image.open(result["problem_crop_paths"]["page-1-problem-1"]) as crop:
                self.assertGreaterEqual(crop.height, 320)

    def test_hwp_count_match_suppresses_page_count_similarity_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_image = root / "page.png"
            crop_path = root / "crop.png"
            Image.new("RGB", (900, 1200), "white").save(source_image)
            Image.new("RGB", (600, 420), "white").save(crop_path)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source_image),
                page_number=1,
                image=Image.new("RGB", (900, 1200), "white"),
                original_size=(900, 1200),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(root / "single.hwp"),
                    "hwp_conversion_quality": {
                        "hwp_text_extractor": "rhwp-markdown",
                        "hwp_text_numbered_problem_count": 1,
                    },
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=900,
                height_px=1200,
                subject=Subject.KOREAN,
                source_path=str(source_image),
                blocks=[
                    ContentBlock(
                        block_id="band-1",
                        block_type=BlockType.IMAGE,
                        bbox=Box(left=60, top=80, width=620, height=360),
                        reading_order=1,
                    ),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="p1",
                        subject=Subject.KOREAN,
                        title=None,
                        figure_block_ids=["band-1"],
                    )
                ],
                metadata=prepared.metadata,
            )
            placement = {
                "problem_id": "p1",
                "title": "1",
                "problem_number": 1,
                "subject": "국어",
                "source_page_id": "page-1",
                "source_path": str(source_image),
                "crop_path": str(crop_path),
                "board_render_path": str(crop_path),
                "actual_content_height_pages": 0.75,
                "overflow_allowed": False,
                "overflow_violation": False,
                "overflow_amount_pages": 0.0,
                "slot_span_count": 1,
                "start_y_pages": 0.0,
                "snapped_next_start_y_pages": 1.0,
                "placement_x_ratio": 0.0,
                "placement_y_ratio": 0.0,
                "placement_scale_ratio": 1.0,
                "record_mode": "problem",
                "processing_step": PROCESSING_STEP_RECONSTRUCT,
                "text_record_count": 0,
                "image_record_count": 1,
                "bbox": {"left": 60, "top": 80, "width": 620, "height": 360},
                "risk_flags": ["fallback_grouping"],
            }

            session = build_problem_ui_session(
                [prepared],
                [placement],
                root / "out",
                None,
                [root / "single.hwp"],
                record_mode="problem",
                pages=[page],
                input_intent="exam",
            )

        self.assertFalse(
            [
                message
                for message in session["warning_messages"]
                if "원본 페이지 수와 비슷" in message
            ]
        )

    def test_mvp_export_deduplicates_repeated_marker_problem_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 2,
                "pdf_text_markers_reliable": True,
            }

            prepared_pages = []
            page_models = []
            for page_index in range(2):
                page_id = f"page-{page_index + 1}"
                prepared_pages.append(
                    PreparedPage(
                        page_id=page_id,
                        source_path=str(source),
                        page_number=page_index + 1,
                        image=Image.new("RGB", (720, 960), "white"),
                        original_size=(720, 960),
                        metadata={
                            "source_type": "hwp",
                            "source_hwp_path": str(source),
                            "document_like": True,
                            "hwp_conversion_quality": quality,
                        },
                    )
                )
                block_id = f"{page_id}-block-1"
                page_models.append(
                    PageModel(
                        page_id=page_id,
                        width_px=720,
                        height_px=960,
                        subject=Subject.MATH,
                        source_path=str(source),
                        blocks=[
                            ContentBlock(
                                block_id=block_id,
                                block_type=BlockType.TITLE,
                                bbox=Box(80, 120, 560, 120),
                                reading_order=0,
                                text="1.",
                                metadata={
                                    "segmenter": "pdf-text-markers",
                                    "problem_number": 1,
                                },
                            )
                        ],
                        problems=[
                            ProblemUnit(
                                unit_id=f"{page_id}-problem-1",
                                subject=Subject.MATH,
                                title="1.",
                                stem_block_ids=[block_id],
                                metadata={
                                    "problem_number": 1,
                                    "problem_number_source": "pdf_text_marker",
                                },
                            )
                        ],
                        metadata={
                            "source_type": "hwp",
                            "source_hwp_path": str(source),
                            "document_like": True,
                            "segmenter": "pdf-text-markers",
                            "hwp_conversion_quality": quality,
                        },
                    )
                )

            with (
                mock.patch("build_mvp_export.prepare_source_pages", return_value=prepared_pages),
                mock.patch("build_mvp_export.build_page_model", side_effect=page_models),
            ):
                result = run_mvp_export(
                    source,
                    output_dir=root / "out",
                    subject_name="math",
                    ocr="none",
                    sync_ui=False,
                )

            self.assertEqual(["page-1-problem-1"], list(result["problem_crop_paths"]))
            self.assertEqual(["page-1-problem-1"], [problem["id"] for problem in result["ui_session"]["problems"]])
            pages_payload = json.loads((root / "out" / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual([], pages_payload[1]["problems"])
            self.assertEqual([1], pages_payload[1]["metadata"]["duplicate_problem_numbers_skipped"])

    def test_mvp_export_preserves_repeated_numbers_when_hwp_text_signal_expects_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 2,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 2,
                "hwp_text_stem_problem_count": 0,
            }

            prepared_pages = []
            page_models = []
            for page_index, section in enumerate(("화법과 작문", "언어와 매체")):
                page_id = f"page-{page_index + 1}"
                prepared_pages.append(
                    PreparedPage(
                        page_id=page_id,
                        source_path=str(source),
                        page_number=page_index + 1,
                        image=Image.new("RGB", (720, 960), "white"),
                        original_size=(720, 960),
                        metadata={
                            "source_type": "hwp",
                            "source_hwp_path": str(source),
                            "document_like": True,
                            "hwp_conversion_quality": quality,
                        },
                    )
                )
                block_id = f"{page_id}-block-1"
                page_models.append(
                    PageModel(
                        page_id=page_id,
                        width_px=720,
                        height_px=960,
                        subject=Subject.KOREAN,
                        source_path=str(source),
                        blocks=[
                            ContentBlock(
                                block_id=block_id,
                                block_type=BlockType.TITLE,
                                bbox=Box(80, 120, 560, 120),
                                reading_order=0,
                                text="35.",
                                metadata={
                                    "segmenter": "pdf-text-markers",
                                    "problem_number": 35,
                                    "section_title": section,
                                },
                            )
                        ],
                        problems=[
                            ProblemUnit(
                                unit_id=f"{page_id}-problem-1",
                                subject=Subject.KOREAN,
                                title="35.",
                                stem_block_ids=[block_id],
                                metadata={
                                    "problem_number": 35,
                                    "problem_number_source": "pdf_text_marker",
                                },
                            )
                        ],
                        metadata={
                            "source_type": "hwp",
                            "source_hwp_path": str(source),
                            "document_like": True,
                            "segmenter": "pdf-text-markers",
                            "hwp_conversion_quality": quality,
                        },
                    )
                )

            with (
                mock.patch("build_mvp_export.prepare_source_pages", return_value=prepared_pages),
                mock.patch("build_mvp_export.build_page_model", side_effect=page_models),
            ):
                result = run_mvp_export(
                    source,
                    output_dir=root / "out",
                    subject_name="korean",
                    ocr="none",
                    sync_ui=False,
                )

            self.assertEqual(
                ["page-1-problem-1", "page-2-problem-1"],
                list(result["problem_crop_paths"]),
            )
            pages_payload = json.loads((root / "out" / "pages.json").read_text(encoding="utf-8"))
            self.assertNotIn("duplicate_problem_numbers_skipped", pages_payload[1]["metadata"])

    def test_mvp_export_flags_hwp_problem_count_mismatch_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 2,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 5,
                "hwp_text_stem_problem_count": 0,
            }
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "hwp_conversion_quality": quality,
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="block-1",
                        block_type=BlockType.TITLE,
                        bbox=Box(80, 100, 560, 140),
                        reading_order=0,
                        text="1.",
                        metadata={"segmenter": "pdf-text-markers", "problem_number": 1},
                    ),
                    ContentBlock(
                        block_id="block-2",
                        block_type=BlockType.TITLE,
                        bbox=Box(80, 420, 560, 140),
                        reading_order=1,
                        text="2.",
                        metadata={"segmenter": "pdf-text-markers", "problem_number": 2},
                    ),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title="1.",
                        stem_block_ids=["block-1"],
                        metadata={"problem_number": 1, "problem_number_source": "pdf_text_marker"},
                    ),
                    ProblemUnit(
                        unit_id="page-1-problem-2",
                        subject=Subject.KOREAN,
                        title="2.",
                        stem_block_ids=["block-2"],
                        metadata={"problem_number": 2, "problem_number_source": "pdf_text_marker"},
                    ),
                ],
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "segmenter": "pdf-text-markers",
                    "hwp_conversion_quality": quality,
                },
            )

            with (
                mock.patch("build_mvp_export.prepare_source_pages", return_value=[prepared]),
                mock.patch("build_mvp_export.build_page_model", return_value=page),
            ):
                result = run_mvp_export(
                    source,
                    output_dir=root / "out",
                    subject_name="korean",
                    ocr="none",
                    sync_ui=False,
                )

            ui_session = result["ui_session"]
            self.assertIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertEqual("check_needed", ui_session["pages"][0]["reviewStatus"])
            self.assertTrue(
                any("HWP" in message and "5" in message and "2" in message for message in ui_session["warning_messages"])
            )

    def test_mvp_export_flags_hwp_oversegmentation_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            board_path = root / "board.png"
            crop_path = root / "crop.png"
            Image.new("RGB", (720, 960), "white").save(board_path)
            Image.new("RGB", (120, 80), "white").save(crop_path)
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 20,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 20,
                "hwp_text_stem_problem_count": 0,
            }
            problems = [
                ProblemUnit(
                    unit_id=f"page-1-problem-{index}",
                    subject=Subject.KOREAN,
                    title=f"{index}.",
                    metadata={"problem_number": index, "problem_number_source": "pdf_text_marker"},
                )
                for index in range(1, 56)
            ]
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                problems=problems,
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "segmenter": "pdf-text-markers",
                    "hwp_conversion_quality": quality,
                },
            )
            placements = [
                SimpleNamespace(
                    problem_id=problem.unit_id,
                    subject=Subject.KOREAN,
                    start_y_pages=0.0,
                    nominal_slot_height_pages=1.0,
                    actual_content_height_pages=0.5,
                    actual_bottom_y_pages=0.5,
                    snapped_next_start_y_pages=1.0,
                    overflow_allowed=False,
                    overflow_amount_pages=0.0,
                    overflow_violation=False,
                    slot_span_count=1,
                    board_capacity_exceeded=False,
                    metadata={},
                )
                for problem in problems
            ]
            export_plan = SimpleNamespace(
                template=LayoutTemplate(name="academy-default"),
                placements=placements,
            )
            ui_session = build_mvp_ui_session(
                [page],
                export_plan,
                [board_path],
                {problem.unit_id: crop_path for problem in problems},
                root / "out",
                None,
                [source],
            )

            self.assertIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertIn("hwp_oversegmentation", ui_session["pages"][0]["riskFlags"])
            self.assertEqual("check_needed", ui_session["pages"][0]["reviewStatus"])
            self.assertTrue(
                any(
                    "과분할" in message and "20" in message and "55" in message
                    for message in ui_session["warning_messages"]
                )
            )

    def test_problem_export_flags_hwp_problem_count_mismatch_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 2,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 5,
                "hwp_text_stem_problem_count": 0,
            }
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "hwp_conversion_quality": quality,
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=[
                    ContentBlock(
                        block_id="block-1",
                        block_type=BlockType.TITLE,
                        bbox=Box(80, 100, 560, 140),
                        reading_order=0,
                        text="1.",
                        metadata={"segmenter": "pdf-text-markers", "problem_number": 1},
                    ),
                    ContentBlock(
                        block_id="block-2",
                        block_type=BlockType.TITLE,
                        bbox=Box(80, 420, 560, 140),
                        reading_order=1,
                        text="2.",
                        metadata={"segmenter": "pdf-text-markers", "problem_number": 2},
                    ),
                ],
                problems=[
                    ProblemUnit(
                        unit_id="page-1-problem-1",
                        subject=Subject.KOREAN,
                        title="1.",
                        stem_block_ids=["block-1"],
                        metadata={"problem_number": 1, "problem_number_source": "pdf_text_marker"},
                    ),
                    ProblemUnit(
                        unit_id="page-1-problem-2",
                        subject=Subject.KOREAN,
                        title="2.",
                        stem_block_ids=["block-2"],
                        metadata={"problem_number": 2, "problem_number_source": "pdf_text_marker"},
                    ),
                ],
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "segmenter": "pdf-text-markers",
                    "hwp_conversion_quality": quality,
                },
            )

            with mock.patch("build_problem_board_edb.build_pages", return_value=([prepared], [page])):
                result = run_problem_export(
                    source,
                    output_dir=root / "out",
                    subject_name="korean",
                    ocr="none",
                    record_mode="image-only",
                    export_edb=False,
                    sync_ui=False,
                )

            ui_session = result["ui_session"]
            self.assertIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertEqual("check_needed", ui_session["pages"][0]["reviewStatus"])
            self.assertTrue(
                any("HWP" in message and "5" in message and "2" in message for message in ui_session["warning_messages"])
            )

    def test_problem_export_flags_hwp_oversegmentation_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            crop_path = root / "crop.png"
            Image.new("RGB", (120, 80), "white").save(crop_path)
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 20,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 20,
                "hwp_text_stem_problem_count": 0,
            }
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "hwp_conversion_quality": quality,
                },
            )
            problems = [
                ProblemUnit(
                    unit_id=f"page-1-problem-{index}",
                    subject=Subject.KOREAN,
                    title=f"{index}.",
                    metadata={"problem_number": index, "problem_number_source": "pdf_text_marker"},
                )
                for index in range(1, 56)
            ]
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                problems=problems,
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "segmenter": "pdf-text-markers",
                    "hwp_conversion_quality": quality,
                },
            )
            placements = [
                {
                    "problem_id": problem.unit_id,
                    "title": problem.title,
                    "problem_number": index,
                    "subject": "korean",
                    "crop_path": str(crop_path),
                    "source_path": str(source),
                    "source_page_id": "page-1",
                    "board_render_path": str(crop_path),
                    "actual_content_height_pages": 0.5,
                    "overflow_allowed": False,
                    "start_y_pages": 0.0,
                    "snapped_next_start_y_pages": 1.0,
                    "overflow_amount_pages": 0.0,
                    "overflow_violation": False,
                    "slot_span_count": 1,
                    "bbox": {"left": 0, "top": 0, "width": 120, "height": 80},
                    "risk_flags": [],
                    "record_mode": "image-only",
                    "text_record_count": 0,
                    "image_record_count": 1,
                }
                for index, problem in enumerate(problems, start=1)
            ]

            ui_session = build_problem_ui_session(
                [prepared],
                placements,
                root / "out",
                None,
                [source],
                record_mode="image-only",
                pages=[page],
                template=LayoutTemplate(name="academy-default"),
            )

            self.assertIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertIn("hwp_oversegmentation", ui_session["pages"][0]["riskFlags"])
            self.assertEqual("check_needed", ui_session["pages"][0]["reviewStatus"])
            self.assertTrue(
                any(
                    "과분할" in message and "20" in message and "55" in message
                    for message in ui_session["warning_messages"]
                )
            )

    def test_problem_ui_session_does_not_apply_layout_duplicate_skips_to_text_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            crop_path = root / "crop.png"
            Image.new("RGB", (120, 80), "white").save(crop_path)
            quality = {
                "has_pdf_text_markers": True,
                "pdf_text_marker_count": 20,
                "pdf_text_markers_reliable": True,
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 20,
                "hwp_text_stem_problem_count": 0,
                "hwp_layout_problem_marker_count": 31,
            }
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "hwp_conversion_quality": quality,
                },
            )
            problems = [
                ProblemUnit(
                    unit_id=f"page-1-problem-{index}",
                    subject=Subject.KOREAN,
                    title=f"{index}.",
                    metadata={"problem_number": index, "problem_number_source": "pdf_text_marker"},
                )
                for index in range(1, 21)
            ]
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                problems=problems,
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "document_like": True,
                    "segmenter": "pdf-text-markers",
                    "hwp_conversion_quality": quality,
                    "duplicate_problem_numbers_skipped": list(range(21, 32)),
                },
            )
            placements = [
                {
                    "problem_id": problem.unit_id,
                    "title": problem.title,
                    "problem_number": index,
                    "subject": "korean",
                    "crop_path": str(crop_path),
                    "source_path": str(source),
                    "source_page_id": "page-1",
                    "board_render_path": str(crop_path),
                    "actual_content_height_pages": 0.5,
                    "overflow_allowed": False,
                    "start_y_pages": 0.0,
                    "snapped_next_start_y_pages": 1.0,
                    "overflow_amount_pages": 0.0,
                    "overflow_violation": False,
                    "slot_span_count": 1,
                    "bbox": {"left": 0, "top": 0, "width": 120, "height": 80},
                    "risk_flags": [],
                    "record_mode": "image-only",
                    "text_record_count": 0,
                    "image_record_count": 1,
                }
                for index, problem in enumerate(problems, start=1)
            ]

            ui_session = build_problem_ui_session(
                [prepared],
                placements,
                root / "out",
                None,
                [source],
                record_mode="image-only",
                pages=[page],
                template=LayoutTemplate(name="academy-default"),
            )

            self.assertNotIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertNotIn("hwp_oversegmentation", ui_session["pages"][0]["riskFlags"])
            self.assertFalse(
                any("HWP 내부 텍스트 기준 문항 수" in message for message in ui_session["warning_messages"])
            )

    def test_problem_ui_session_uses_final_placements_for_hwp_count_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.hwp"
            source.write_bytes(b"hwp")
            crop_1 = root / "crop-1.png"
            crop_2 = root / "crop-2.png"
            crop_3 = root / "crop-3.png"
            Image.new("RGB", (120, 80), "white").save(crop_1)
            Image.new("RGB", (120, 80), "white").save(crop_2)
            Image.new("RGB", (120, 80), "white").save(crop_3)
            quality = {
                "hwp_text_extractor": "hwp5txt",
                "hwp_text_numbered_problem_count": 2,
                "hwp_text_stem_problem_count": 0,
            }
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.new("RGB", (720, 960), "white"),
                original_size=(720, 960),
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "hwp_conversion_quality": quality,
                },
            )
            page = PageModel(
                page_id="page-1",
                width_px=720,
                height_px=960,
                subject=Subject.KOREAN,
                source_path=str(source),
                blocks=[],
                problems=[
                    ProblemUnit(unit_id="kept-1", subject=Subject.KOREAN, title="1.", metadata={"problem_number": 1}),
                    ProblemUnit(unit_id="kept-2", subject=Subject.KOREAN, title="2.", metadata={"problem_number": 2}),
                    ProblemUnit(
                        unit_id="kept-continuation",
                        subject=Subject.KOREAN,
                        title="이어지는 자료",
                        metadata={"marker_document_continuation": True},
                    ),
                    ProblemUnit(unit_id="skipped-before-ui", subject=Subject.KOREAN, title="skip", metadata={}),
                ],
                metadata={
                    "source_type": "hwp",
                    "source_hwp_path": str(source),
                    "hwp_conversion_quality": quality,
                },
            )
            placements = [
                {
                    "problem_id": "kept-1",
                    "title": "1.",
                    "problem_number": 1,
                    "subject": "korean",
                    "crop_path": str(crop_1),
                    "source_path": str(source),
                    "source_page_id": "page-1",
                    "board_render_path": str(crop_1),
                    "actual_content_height_pages": 0.4,
                    "overflow_allowed": False,
                    "start_y_pages": 0.0,
                    "snapped_next_start_y_pages": 1.0,
                    "overflow_amount_pages": 0.0,
                    "overflow_violation": False,
                    "slot_span_count": 1,
                    "bbox": {"left": 0, "top": 0, "width": 120, "height": 80},
                    "risk_flags": [],
                    "record_mode": "image-only",
                    "text_record_count": 0,
                    "image_record_count": 1,
                },
                {
                    "problem_id": "kept-2",
                    "title": "2.",
                    "problem_number": 2,
                    "subject": "korean",
                    "crop_path": str(crop_2),
                    "source_path": str(source),
                    "source_page_id": "page-1",
                    "board_render_path": str(crop_2),
                    "actual_content_height_pages": 0.4,
                    "overflow_allowed": False,
                    "start_y_pages": 1.0,
                    "snapped_next_start_y_pages": 2.0,
                    "overflow_amount_pages": 0.0,
                    "overflow_violation": False,
                    "slot_span_count": 1,
                    "bbox": {"left": 0, "top": 100, "width": 120, "height": 80},
                    "risk_flags": [],
                    "record_mode": "image-only",
                    "text_record_count": 0,
                    "image_record_count": 1,
                },
                {
                    "problem_id": "kept-continuation",
                    "title": "이어지는 자료",
                    "problem_number": None,
                    "subject": "korean",
                    "crop_path": str(crop_3),
                    "source_path": str(source),
                    "source_page_id": "page-1",
                    "board_render_path": str(crop_3),
                    "actual_content_height_pages": 0.4,
                    "overflow_allowed": False,
                    "start_y_pages": 2.0,
                    "snapped_next_start_y_pages": 3.0,
                    "overflow_amount_pages": 0.0,
                    "overflow_violation": False,
                    "slot_span_count": 1,
                    "bbox": {"left": 0, "top": 200, "width": 120, "height": 80},
                    "risk_flags": ["marker_document_continuation"],
                    "record_mode": "image-only",
                    "text_record_count": 0,
                    "image_record_count": 1,
                },
            ]

            ui_session = build_problem_ui_session(
                [prepared],
                placements,
                root / "out",
                None,
                [source],
                record_mode="image-only",
                pages=[page],
                template=LayoutTemplate(name="academy-default"),
            )

            self.assertEqual(3, ui_session["detected_problem_count"])
            self.assertEqual(2, ui_session["core_problem_count"])
            self.assertEqual(1, ui_session["supplemental_item_count"])
            self.assertNotIn("hwp_problem_count_mismatch", ui_session["pages"][0]["riskFlags"])
            self.assertFalse([message for message in ui_session["warning_messages"] if "HWP 내부 텍스트" in message])

    def test_edge_vertical_guides_are_trimmed_without_removing_internal_lines(self):
        image = Image.new("RGB", (120, 90), "white")
        draw = ImageDraw.Draw(image)
        draw.line((8, 0, 8, 89), fill=(170, 170, 170), width=2)
        draw.line((112, 0, 112, 89), fill=(170, 170, 170), width=2)
        draw.line((60, 10, 60, 80), fill="black", width=2)
        trimmed = _trim_edge_vertical_guides(image)

        self.assertLess(trimmed.width, image.width)
        self.assertEqual(trimmed.height, image.height)
        gray = trimmed.convert("L")
        internal_dark_columns = [
            x
            for x in range(10, trimmed.width - 10)
            if sum(1 for y in range(trimmed.height) if gray.getpixel((x, y)) < 80) >= 50
        ]
        self.assertTrue(internal_dark_columns)

    def test_slanted_edge_vertical_guides_are_trimmed(self):
        image = Image.new("RGB", (180, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.text((24, 36), "1. problem", fill="black")
        draw.line((90, 24, 90, 154), fill="black", width=2)
        draw.line((156, 0, 148, 179), fill=(40, 40, 40), width=3)
        draw.text((164, 36), "4", fill="black")

        trimmed = _trim_edge_vertical_guides(image)

        self.assertLess(trimmed.width, 150)
        gray = trimmed.convert("L")
        right_band_dark_pixels = sum(
            1
            for x in range(max(0, trimmed.width - 8), trimmed.width)
            for y in range(trimmed.height)
            if gray.getpixel((x, y)) < 80
        )
        self.assertLess(right_band_dark_pixels, 20)
        internal_dark_columns = [
            x
            for x in range(50, trimmed.width - 20)
            if sum(1 for y in range(trimmed.height) if gray.getpixel((x, y)) < 80) >= 80
        ]
        self.assertTrue(internal_dark_columns)

    def test_problem_crop_bottom_padding_preserves_last_choice(self):
        image = Image.new("RGB", (120, 80), "white")
        draw = ImageDraw.Draw(image)
        draw.text((12, 64), "⑤", fill="black")

        padded = _pad_problem_crop_bottom(image, padding_px=18)

        self.assertEqual(padded.size, (120, 98))
        self.assertEqual(padded.getpixel((12, 96)), (255, 255, 255))

    def test_choice_bottom_survives_near_next_problem_clamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (600, 420), "white").save(source)
            prepared = PreparedPage(
                page_id="page-1",
                source_path=str(source),
                page_number=1,
                image=Image.open(source).convert("RGB"),
                original_size=(600, 420),
            )
            blocks = [
                ContentBlock(
                    block_id="p1-stem",
                    block_type=BlockType.STEM,
                    bbox=Box(60, 100, 360, 60),
                    reading_order=0,
                    text="1. problem",
                    metadata={"column_index": 0, "question_band_index": 0},
                ),
                ContentBlock(
                    block_id="p1-choice",
                    block_type=BlockType.CHOICE,
                    bbox=Box(72, 192, 330, 28),
                    reading_order=1,
                    text="⑤ choice",
                    metadata={"column_index": 0, "question_band_index": 0},
                ),
                ContentBlock(
                    block_id="p2-stem",
                    block_type=BlockType.STEM,
                    bbox=Box(60, 225, 360, 55),
                    reading_order=2,
                    text="2. next",
                    metadata={"column_index": 0, "question_band_index": 1},
                ),
            ]
            page = PageModel(
                page_id="page-1",
                width_px=600,
                height_px=420,
                subject=Subject.MATH,
                source_path=str(source),
                blocks=blocks,
                problems=[
                    ProblemUnit(
                        unit_id="problem-1",
                        subject=Subject.MATH,
                        title="1.",
                        stem_block_ids=["p1-stem"],
                        choice_block_ids=["p1-choice"],
                        metadata={"problem_number": 1, "column_index": 0, "question_band_index": 0},
                    ),
                    ProblemUnit(
                        unit_id="problem-2",
                        subject=Subject.MATH,
                        title="2.",
                        stem_block_ids=["p2-stem"],
                        metadata={"problem_number": 2, "column_index": 0, "question_band_index": 1},
                    ),
                ],
            )

            entries = build_problem_entries(
                [prepared],
                [page],
                root / "out",
                LayoutTemplate(name="academy-default"),
            )

            problem_1 = next(entry for entry in entries if entry.problem_number == 1)
            self.assertGreaterEqual(problem_1.bounds.bottom, 248)

    def test_session_source_images_point_to_rendered_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            self._make_source_image(source)

            result = run_problem_export(
                source,
                output_dir=root / "out",
                input_intent="single-problem",
                ocr="noop",
                record_mode="image-only",
                export_edb=False,
            )

            session = result["ui_session"]
            page_source = Path(session["pages"][0]["sourceImagePath"])
            problem_source = _path_from_file_uri(session["problems"][0]["sourceImagePath"])
            self.assertEqual(page_source.suffix.lower(), ".png")
            self.assertEqual(problem_source.suffix.lower(), ".png")
            self.assertTrue(page_source.exists())
            self.assertTrue(problem_source.exists())


if __name__ == "__main__":
    unittest.main()
