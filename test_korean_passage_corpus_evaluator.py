from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import fitz

from scripts.evaluate_korean_passage_corpus import score_document


class TestKoreanPassageCorpusEvaluator(unittest.TestCase):
    def _write_fixture(
        self,
        root: Path,
        *,
        labels: list[str],
        marker_intrusions: int = 0,
        guide_cleanup_recall: float = 1.0,
    ) -> Path:
        source = root / "fixture.pdf"
        document = fitz.open()
        document.new_page(width=600, height=800)
        document.save(source)
        document.close()

        fragments = [
            {
                "group_id": f"group-{index}",
                "label": label,
                "page_number": 1,
                "fragment_index": 1,
                "clip_points": [40.0, 80.0, 280.0, 700.0],
                "cross_page_passage_inferred": False,
            }
            for index, label in enumerate(labels, start=1)
        ]
        benchmark = root / "benchmark.json"
        benchmark.write_text(
            json.dumps(
                {
                    "source": str(source),
                    "passage_fragment_count": len(fragments),
                    "groups": [
                        {
                            "group_id": fragment["group_id"],
                            "label": fragment["label"],
                        }
                        for fragment in fragments
                    ],
                    "fragments": fragments,
                    "quality_summary": {
                        "minimum_char_bbox_recall": 1.0,
                        "clipped_char_bbox_count": 0,
                        "center_divider_checked_fragment_count": len(fragments),
                        "center_divider_violation_count": 0,
                        "page_chrome_fragment_count": 0,
                        "problem_marker_intrusion_count": marker_intrusions,
                        "minimum_outer_guide_cleanup_ink_recall": guide_cleanup_recall,
                    },
                }
            ),
            encoding="utf-8",
        )
        return benchmark

    def test_perfect_isolated_passage_receives_full_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = self._write_fixture(Path(tmp), labels=["11-14"])

            result = score_document(
                benchmark,
                {"expectedRanges": ["11-14"], "forbiddenRanges": []},
            )

        self.assertEqual(100.0, result["score"])
        self.assertTrue(result["pass"])

    def test_non_passage_extra_range_fails_strict_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = self._write_fixture(Path(tmp), labels=["11-14", "13-14"])

            result = score_document(
                benchmark,
                {"expectedRanges": ["11-14"], "forbiddenRanges": ["13-14"]},
            )

        self.assertFalse(result["pass"])
        self.assertEqual(["13-14"], result["extra_ranges"])
        self.assertEqual(["13-14"], result["forbidden_ranges_detected"])

    def test_question_marker_inside_passage_crop_fails_strict_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = self._write_fixture(
                Path(tmp),
                labels=["11-14"],
                marker_intrusions=1,
            )

            result = score_document(
                benchmark,
                {"expectedRanges": ["11-14"], "forbiddenRanges": []},
            )

        self.assertFalse(result["pass"])
        self.assertEqual(1, result["problem_marker_intrusion_count"])

    def test_destructive_outer_guide_cleanup_fails_strict_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = self._write_fixture(
                Path(tmp),
                labels=["11-14"],
                guide_cleanup_recall=0.86,
            )

            result = score_document(
                benchmark,
                {"expectedRanges": ["11-14"], "forbiddenRanges": []},
            )

        self.assertFalse(result["pass"])
        self.assertEqual(0.86, result["minimum_outer_guide_cleanup_ink_recall"])


if __name__ == "__main__":
    unittest.main()
