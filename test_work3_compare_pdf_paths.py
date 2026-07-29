import tempfile
import unittest
from pathlib import Path

import fitz

from scripts.work3_compare_pdf_paths import (
    PassageFragment,
    _char_bbox_audit,
    _clip_points,
    compare_pdf_paths,
)


class TestWork3ComparePdfPaths(unittest.TestCase):
    def _write_two_column_passage(self, path: Path) -> None:
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
        # Keep the synthetic range header inside the left column. A header
        # deliberately crossing the divider is a separate malformed-layout
        # case and should not lower this path-equivalence fixture's recall.
        page.insert_text(
            (48, 82),
            "[1~2] Read the following passage and answer the questions.",
            fontsize=9,
        )
        for row, y in enumerate(range(120, 730, 28), start=1):
            page.insert_text((48, y), f"left passage line {row:02d}", fontsize=11)
        for row, y in enumerate(range(82, 300, 28), start=1):
            page.insert_text((330, y), f"continued passage line {row:02d}", fontsize=11)
        page.insert_text((330, 350), "1. first question", fontsize=14)
        page.insert_text((342, 410), "① a   ② b   ③ c", fontsize=12)
        page.insert_text((330, 540), "2. second question", fontsize=14)
        page.insert_text((342, 600), "① a   ② b   ③ c", fontsize=12)
        document.save(path)
        document.close()

    def test_direct_clip_matches_full_page_crop_for_detected_passage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "two-column.pdf"
            self._write_two_column_passage(pdf_path)

            result = compare_pdf_paths(
                pdf_path,
                subject="english",
                output_dir=root / "result",
                dpi=144,
            )

            self.assertEqual(1, result["passage_group_count"])
            self.assertEqual(2, result["passage_fragment_count"])
            self.assertEqual({"pdf-structure": 1}, result["page_route_counts"])
            self.assertGreater(result["quality_summary"]["minimum_pixel_similarity"], 0.98)
            self.assertGreater(result["quality_summary"]["minimum_ink_f1"], 0.95)
            self.assertEqual(1.0, result["quality_summary"]["minimum_char_bbox_recall"])
            self.assertEqual(0, result["quality_summary"]["clipped_char_bbox_count"])
            self.assertEqual("all-direct-clips", result["v2"]["policy"])
            self.assertGreater(result["v1_render_speedup_over_v0"], 0.0)
            self.assertGreater(result["v1_end_to_end_speedup_over_v0"], 0.0)
            self.assertGreater(
                result["v1"]["end_to_end_seconds"],
                result["v1"]["render_seconds"],
            )
            self.assertTrue((root / "result" / "benchmark.json").is_file())
            self.assertEqual(1, len(list((root / "result" / "v0-full-page").glob("*.png"))))
            self.assertEqual(1, len(list((root / "result" / "v1-direct-clip").glob("*.png"))))

    def test_char_bbox_audit_counts_glyph_recovered_outside_block_boundary(self):
        document = fitz.open()
        page = document.new_page(width=300, height=200)
        page.insert_text((40, 80), "boundary text", fontsize=12)
        fragment = PassageFragment(
            group_id="p1",
            label="1-2",
            page_number=1,
            fragment_index=1,
            # The block begins after the first glyph, while the 24px recovery
            # margin still includes that glyph in the direct PDF clip.
            bbox_px=(48.0, 60.0, 180.0, 90.0),
            page_width_px=300,
            page_height_px=200,
        )
        clip = _clip_points(fragment, page.rect)

        audit = _char_bbox_audit(page, fragment, clip, dpi=72)

        self.assertGreater(audit["recovered_outside_block_char_count"], 0)
        self.assertEqual(1.0, audit["char_bbox_recall"])
        self.assertEqual(0, audit["char_bbox_clipped_count"])
        document.close()

    def test_rejects_out_of_scope_subject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "two-column.pdf"
            self._write_two_column_passage(pdf_path)

            with self.assertRaisesRegex(ValueError, "korean and english"):
                compare_pdf_paths(
                    pdf_path,
                    subject="math",
                    output_dir=root / "result",
                    dpi=72,
                )


if __name__ == "__main__":
    unittest.main()
