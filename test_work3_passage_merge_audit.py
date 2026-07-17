import tempfile
import unittest
from pathlib import Path

from PIL import Image

from build_problem_board_edb import (
    _ProblemEntryDraft,
    _coalesce_cross_page_passage_drafts,
)
from preprocess import PreparedPage
from structured_schema import Box, PageModel, ProblemUnit, Subject
from scripts.work3_passage_merge_audit import (
    analyze_benchmark_layouts,
    compare_layouts,
    run_synthetic_audit,
)


class TestWork3PassageMergeAudit(unittest.TestCase):
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
            self.assertTrue((output_dir / "merged-current.png").is_file())
            self.assertTrue((output_dir / "layout-comparison.png").is_file())
            self.assertTrue((output_dir / "audit.json").is_file())

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

            def draft(problem_id, prepared, crop_path):
                render_path = root / f"render-{problem_id}.png"
                Image.open(crop_path).convert("RGBA").save(render_path)
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
                draft("fragment-2", prepared_2, page_2_path),
                draft("fragment-1", prepared_1, page_1_path),
            ]

            merged, _sizes = _coalesce_cross_page_passage_drafts(
                drafts,
                [(240, 180), (240, 180)],
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


if __name__ == "__main__":
    unittest.main()
