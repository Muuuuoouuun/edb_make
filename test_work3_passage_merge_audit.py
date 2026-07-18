import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from build_problem_board_edb import (
    _ProblemEntryDraft,
    _coalesce_cross_page_passage_drafts,
)
from preprocess import PreparedPage
from structured_schema import Box, PageModel, ProblemUnit, Subject
from scripts.work3_passage_merge_audit import (
    analyze_benchmark_layouts,
    analyze_multi_dpi_benchmarks,
    compare_layouts,
    render_actual_s2_preview,
    run_synthetic_audit,
)


class TestWork3PassageMergeAudit(unittest.TestCase):
    def _make_draft(self, root, problem_id, prepared, crop_path):
        render_path = root / f"render-{problem_id}-{crop_path.stem}.png"
        with Image.open(crop_path) as image:
            image.convert("RGBA").save(render_path)
        return _ProblemEntryDraft(
            problem_id=problem_id,
            title="passage",
            problem_number=None,
            subject=Subject.KOREAN,
            source_page_id=prepared.page_id,
            source_path=prepared.source_path,
            prepared_page=prepared,
            bounds=Box(left=0, top=0, width=240, height=180),
            crop_path=crop_path,
            board_render_path=render_path,
            blocks=[],
            overflow_allowed=True,
            reading_heavy=True,
            risk_flags=["passage_cross_page_merge_check"],
            processing_step="s2",
            placement_scale_ratio=None,
            input_intent=None,
            force_full_page_bounds=False,
            asset_task=None,
        )

    def test_synthetic_cross_page_merge_is_complete_and_chrome_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "audit"

            result = run_synthetic_audit(output_dir, max_record_height_pages=1.8)

            audit = result["merge_audit"]
            self.assertTrue(audit["pass"])
            self.assertEqual(100.0, audit["completeness_score"])
            self.assertEqual([1, 2, 3], audit["expected_source_pages"])
            self.assertEqual(16, audit["join_gap_px"])
            self.assertEqual({"header": 0, "footer": 0}, audit["remaining_page_chrome_pixels"])
            self.assertTrue(result["strict_matrix"]["pass"])
            self.assertEqual(9, result["strict_matrix"]["case_count"])
            self.assertEqual(9, result["strict_matrix"]["passed_case_count"])
            self.assertTrue(all(result["strict_matrix"]["negative_guardrails"].values()))
            self.assertTrue(result["pass"])
            self.assertTrue((output_dir / "merged-current.png").is_file())
            self.assertTrue((output_dir / "layout-comparison.png").is_file())
            self.assertTrue((output_dir / "audit.json").is_file())
            written = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
            self.assertTrue(written["pass"])
            self.assertEqual(2, written["schema_version"])

    def test_adaptive_layout_splits_only_when_record_height_exceeds_limit(self):
        fragments = [
            Image.new("RGB", (720, 500), "white"),
            Image.new("RGB", (720, 500), "white"),
            Image.new("RGB", (720, 500), "white"),
        ]

        layouts = {
            layout.name: layout
            for layout in compare_layouts(
                fragments,
                display_width_px=1142.0,
                max_record_height_pages=1.8,
            )
        }

        self.assertEqual(1, layouts["single-stitched-record"].record_count)
        self.assertEqual(3, layouts["page-fragment-records"].record_count)
        self.assertGreater(layouts["single-stitched-record"].maximum_record_height_pages, 1.8)
        self.assertEqual(3, layouts["adaptive-fragment-boundary"].record_count)
        self.assertEqual(20.0, layouts["adaptive-fragment-boundary"].inter_record_gap_px)
        self.assertEqual(0, layouts["adaptive-fragment-boundary"].overlap_count)

    def test_coalescer_uses_source_page_number_when_drafts_arrive_reversed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page_1_path = root / "page-1.png"
            page_2_path = root / "page-2.png"
            Image.new("RGB", (240, 180), (210, 30, 30)).save(page_1_path)
            Image.new("RGB", (240, 180), (30, 50, 210)).save(page_2_path)
            prepared_1 = PreparedPage(
                page_id="page-1",
                source_path=str(page_1_path),
                page_number=1,
                image=Image.open(page_1_path).convert("RGB"),
                original_size=(240, 180),
            )
            prepared_2 = PreparedPage(
                page_id="page-2",
                source_path=str(page_2_path),
                page_number=2,
                image=Image.open(page_2_path).convert("RGB"),
                original_size=(240, 180),
            )

            page_1_problem = ProblemUnit(
                unit_id="fragment-1",
                subject=Subject.KOREAN,
                title="passage",
                metadata={
                    "passage_group_id": "passage-1",
                    "passage_role": "passage_fragment",
                },
            )
            page_2_problem = ProblemUnit(
                unit_id="fragment-2",
                subject=Subject.KOREAN,
                title="passage",
                metadata={
                    "passage_group_id": "passage-1",
                    "passage_role": "passage_fragment",
                },
            )
            pages = [
                PageModel(
                    page_id="page-2",
                    width_px=240,
                    height_px=180,
                    subject=Subject.KOREAN,
                    source_path=str(page_2_path),
                    problems=[page_2_problem],
                ),
                PageModel(
                    page_id="page-1",
                    width_px=240,
                    height_px=180,
                    subject=Subject.KOREAN,
                    source_path=str(page_1_path),
                    problems=[page_1_problem],
                ),
            ]
            drafts = [
                self._make_draft(root, "fragment-2", prepared_2, page_2_path),
                self._make_draft(root, "fragment-1", prepared_1, page_1_path),
                self._make_draft(root, "fragment-1", prepared_1, page_1_path),
            ]

            merged, _sizes = _coalesce_cross_page_passage_drafts(
                drafts,
                [(240, 180), (240, 180), (240, 180)],
                pages,
            )

            self.assertEqual(["fragment-1"], [item.problem_id for item in merged])
            self.assertEqual(
                ["fragment-1", "fragment-2"],
                page_1_problem.metadata["passage_merged_fragment_ids"],
            )
            with Image.open(merged[0].crop_path).convert("RGB") as stitched:
                self.assertEqual((210, 30, 30), stitched.getpixel((20, 20)))
                self.assertEqual((30, 50, 210), stitched.getpixel((20, stitched.height - 20)))

    def test_coalescer_refuses_to_mark_incomplete_page_coverage_as_merged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page_1_path = root / "page-1.png"
            page_2_path = root / "page-2.png"
            Image.new("RGB", (240, 180), (210, 30, 30)).save(page_1_path)
            Image.new("RGB", (240, 180), (30, 50, 210)).save(page_2_path)
            prepared = []
            for number, path in ((1, page_1_path), (2, page_2_path)):
                with Image.open(path) as image:
                    loaded = image.convert("RGB").copy()
                prepared.append(
                    PreparedPage(
                        page_id=f"page-{number}",
                        source_path=str(path),
                        page_number=number,
                        image=loaded,
                        original_size=loaded.size,
                    )
                )
            expected_metadata = {
                "passage_group_id": "passage-1",
                "passage_role": "passage_fragment",
                "passage_source_page_ids": ["page-1", "page-2", "page-3"],
                "passage_fragment_count": 3,
            }
            problems = [
                ProblemUnit(
                    unit_id=f"fragment-{number}",
                    subject=Subject.KOREAN,
                    title="passage",
                    metadata=dict(expected_metadata),
                )
                for number in (1, 2)
            ]
            pages = [
                PageModel(
                    page_id=f"page-{number}",
                    width_px=240,
                    height_px=180,
                    subject=Subject.KOREAN,
                    source_path=str(path),
                    problems=[problem],
                )
                for number, path, problem in (
                    (1, page_1_path, problems[0]),
                    (2, page_2_path, problems[1]),
                )
            ]
            drafts = [
                self._make_draft(root, "fragment-1", prepared[0], page_1_path),
                self._make_draft(root, "fragment-2", prepared[1], page_2_path),
            ]

            merged, _sizes = _coalesce_cross_page_passage_drafts(
                drafts,
                [(240, 180), (240, 180)],
                pages,
            )

            self.assertEqual(["fragment-1", "fragment-2"], [item.problem_id for item in merged])
            for problem in problems:
                self.assertTrue(problem.metadata["passage_merge_incomplete"])
                self.assertEqual(
                    ["page-3"],
                    problem.metadata["passage_merge_missing_source_page_ids"],
                )
                self.assertNotIn("passage_fragments_merged", problem.metadata)

    def test_coalescer_orders_five_pages_and_removes_duplicate_draft(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            colors = [
                (210, 30, 30),
                (30, 50, 210),
                (30, 160, 80),
                (140, 60, 190),
                (12, 142, 157),
            ]
            expected_page_ids = [f"page-{number}" for number in range(1, 6)]
            prepared_by_number = {}
            problem_by_number = {}
            page_by_number = {}
            path_by_number = {}
            for number, color in enumerate(colors, start=1):
                path = root / f"page-{number}.png"
                image = Image.new("RGB", (240, 180), "white")
                ImageDraw.Draw(image).rectangle((20, 40, 100, 140), fill=color)
                image.save(path)
                with Image.open(path) as image:
                    loaded = image.convert("RGB").copy()
                prepared = PreparedPage(
                    page_id=f"page-{number}",
                    source_path=str(path),
                    page_number=number,
                    image=loaded,
                    original_size=loaded.size,
                )
                problem = ProblemUnit(
                    unit_id=f"fragment-{number}",
                    subject=Subject.KOREAN,
                    title="passage",
                    metadata={
                        "passage_group_id": "passage-1",
                        "passage_role": "passage_fragment",
                        "passage_source_page_ids": list(expected_page_ids),
                        "passage_fragment_count": 5,
                    },
                )
                page = PageModel(
                    page_id=f"page-{number}",
                    width_px=240,
                    height_px=180,
                    subject=Subject.KOREAN,
                    source_path=str(path),
                    problems=[problem],
                )
                prepared_by_number[number] = prepared
                problem_by_number[number] = problem
                page_by_number[number] = page
                path_by_number[number] = path

            drafts = [
                self._make_draft(
                    root,
                    f"fragment-{number}",
                    prepared_by_number[number],
                    path_by_number[number],
                )
                for number in (5, 4, 3, 2, 1)
            ]
            drafts.append(
                self._make_draft(root, "fragment-3", prepared_by_number[3], path_by_number[3])
            )

            merged, _sizes = _coalesce_cross_page_passage_drafts(
                drafts,
                [(240, 180)] * len(drafts),
                [page_by_number[number] for number in (5, 4, 3, 2, 1)],
            )

            self.assertEqual(["fragment-1"], [item.problem_id for item in merged])
            primary_metadata = problem_by_number[1].metadata
            self.assertEqual(
                [f"fragment-{number}" for number in range(1, 6)],
                primary_metadata["passage_merged_fragment_ids"],
            )
            self.assertEqual(expected_page_ids, primary_metadata["passage_merged_source_page_ids"])
            self.assertEqual(5, primary_metadata["passage_merged_fragment_count"])
            with Image.open(merged[0].crop_path).convert("RGB") as stitched:
                cursor = 0
                for color in colors:
                    self.assertEqual(color, stitched.getpixel((40, cursor + 60)))
                    cursor += 180 + 16

    def test_benchmark_layout_analysis_distinguishes_width_and_boundary_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            benchmark_path = root / "benchmark.json"
            benchmark_path.write_text(
                """{
  "source": "sample.pdf",
  "passage_group_count": 1,
  "passage_fragment_count": 2,
  "groups": [{
    "group_id": "g1", "label": "1-3", "fragment_count": 2,
    "reference_size": [900, 2400]
  }],
  "fragments": [
    {"group_id": "g1", "page_number": 1, "fragment_index": 1, "v1_size": [900, 1200]},
    {"group_id": "g1", "page_number": 2, "fragment_index": 2, "v1_size": [900, 1200]}
  ]
}\n""",
                encoding="utf-8",
            )

            result = analyze_benchmark_layouts(benchmark_path, max_record_height_pages=1.8)

            self.assertEqual(1, result["cross_page_group_count"])
            current = result["width_variants"]["current-left-column"]
            full = result["width_variants"]["full-board-width"]
            self.assertEqual("cross-page", current["groups"][0]["boundary_type"])
            self.assertGreater(
                full["summary"]["single-stitched-record"]["maximum_record_height_pages"],
                current["summary"]["single-stitched-record"]["maximum_record_height_pages"],
            )

    def test_multi_dpi_gate_requires_stable_counts_zero_clipping_and_margin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = []
            for dpi, margin in ((144, 16.2), (200, 22.6), (300, 17.9)):
                path = root / f"benchmark-{dpi}.json"
                path.write_text(
                    """{
  "dpi": %d,
  "passage_group_count": 11,
  "passage_fragment_count": 15,
  "quality_summary": {
    "minimum_pixel_similarity": 1.0,
    "minimum_ink_f1": 1.0,
    "minimum_char_bbox_recall": 1.0,
    "clipped_char_bbox_count": 0,
    "recovered_outside_block_char_count": 138,
    "minimum_horizontal_char_margin_px": %.1f
  }
}\n""" % (dpi, margin),
                    encoding="utf-8",
                )
                paths.append(path)

            result = analyze_multi_dpi_benchmarks(paths)

            self.assertTrue(result["pass"])
            self.assertEqual([144, 200, 300], [row["dpi"] for row in result["rows"]])
            self.assertTrue(all(result["checks"].values()))

    def test_s2_preview_reports_zero_halo_and_safe_horizontal_margin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_dir = root / "v1-direct-clip"
            image_dir.mkdir()
            source = Image.new("RGB", (240, 180), "white")
            ImageDraw.Draw(source).rectangle((40, 30, 190, 150), outline="black", width=4)
            source.save(image_dir / "passage_1-2.png")
            benchmark_path = root / "benchmark.json"
            benchmark_path.write_text(
                """{
  "groups": [{"label": "1-2", "reference_size": [240, 180]}]
}\n""",
                encoding="utf-8",
            )

            result = render_actual_s2_preview(benchmark_path, root / "preview.png")

            self.assertIsNotNone(result)
            self.assertTrue(result["pass"])
            self.assertEqual(0, result["low_alpha_halo_pixel_count"])
            self.assertEqual(1, result["foreground_rgb_color_count"])
            self.assertTrue(result["quality_checks"]["left_safe_margin"])
            self.assertTrue(result["quality_checks"]["right_safe_margin_or_vertical_rule"])


if __name__ == "__main__":
    unittest.main()
