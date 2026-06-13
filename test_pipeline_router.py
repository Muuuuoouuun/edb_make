import unittest

from pipeline_router import decide_page_route
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject


def _block(block_id: str, top: float, *, metadata=None, text=None) -> ContentBlock:
    return ContentBlock(
        block_id=block_id,
        block_type=BlockType.TITLE,
        bbox=Box(left=0, top=top, width=300, height=220),
        reading_order=int(top),
        text=text or f"{block_id}.",
        confidence=1.0,
        metadata={
            "large_block": True,
            "block_area_ratio": 0.22,
            **dict(metadata or {}),
        },
    )


def _problem(unit_id: str, number: int, *, source: str = "pdf_text_marker") -> ProblemUnit:
    return ProblemUnit(
        unit_id=unit_id,
        subject=Subject.MATH,
        title=f"{number}.",
        metadata={"problem_number": number, "problem_number_source": source},
    )


class TestPipelineRouter(unittest.TestCase):
    def test_reliable_pdf_text_markers_are_trusted_even_when_crops_are_large(self):
        page = PageModel(
            page_id="pdf-page",
            width_px=600,
            height_px=800,
            subject=Subject.MATH,
            blocks=[
                _block(
                    f"b-{number}",
                    number * 100,
                    metadata={
                        "segmenter": "pdf-text-markers",
                        "force_problem_start": True,
                        "problem_number": number,
                        "problem_number_source": "pdf_text_marker",
                    },
                )
                for number in range(1, 5)
            ],
            problems=[_problem(f"p-{number}", number) for number in range(1, 5)],
            metadata={
                "segmenter": "pdf-text-markers",
                "segmentation_mode": "document",
                "block_count": 4,
                "large_block_ratio": 1.0,
                "pdf_text_marker_count": 4,
            },
        )

        decision = decide_page_route(page, ocr_mode="none", ai_enabled=True, ai_mode="auto")
        metadata = decision.to_metadata()

        self.assertEqual("trusted", metadata["quality_status"])
        self.assertEqual("skip_ai", metadata["recommended_action"])
        self.assertEqual("local_only", decision.route)
        self.assertFalse(decision.should_use_ai)
        self.assertEqual([], decision.trigger_reasons)

    def test_unreliable_pdf_text_markers_are_suspicious_and_route_to_ai_when_enabled(self):
        page = PageModel(
            page_id="unreliable-pdf-page",
            width_px=600,
            height_px=800,
            subject=Subject.MATH,
            blocks=[
                _block(
                    "b-1",
                    100,
                    metadata={
                        "segmenter": "pdf-text-markers",
                        "force_problem_start": True,
                        "problem_number": 1,
                        "problem_number_source": "pdf_text_marker",
                    },
                )
            ],
            problems=[_problem("p-1", 1)],
            metadata={
                "segmenter": "pdf-text-markers",
                "block_count": 1,
                "pdf_text_marker_count": 1,
                "hwp_conversion_quality": {
                    "has_pdf_text_markers": True,
                    "pdf_text_marker_count": 1,
                    "pdf_text_markers_reliable": False,
                },
            },
        )

        decision = decide_page_route(page, ocr_mode="none", ai_enabled=True, ai_mode="auto")
        metadata = decision.to_metadata()

        self.assertEqual("suspicious", metadata["quality_status"])
        self.assertEqual("ai_repair", metadata["recommended_action"])
        self.assertEqual("ai_patch", decision.route)
        self.assertTrue(decision.should_use_ai)
        self.assertIn("unreliable_pdf_text_markers", decision.trigger_reasons)

    def test_hard_failure_recommends_ai_repair_even_when_ai_is_disabled(self):
        page = PageModel(
            page_id="merged-page",
            width_px=600,
            height_px=800,
            subject=Subject.MATH,
            blocks=[
                _block(
                    "merged",
                    100,
                    metadata={"internal_problem_marker_count": 3},
                    text="1. 문제\n2. 문제\n3. 문제",
                )
            ],
            problems=[_problem("fallback", 1, source="fallback")],
            metadata={"segmenter": "document-bands", "block_count": 1},
        )

        decision = decide_page_route(page, ocr_mode="gemini", ai_enabled=False, ai_mode="off")
        metadata = decision.to_metadata()

        self.assertEqual("failed", metadata["quality_status"])
        self.assertEqual("ai_repair", metadata["recommended_action"])
        self.assertEqual("local_only", decision.route)
        self.assertFalse(decision.should_use_ai)
        self.assertEqual("ai_repair", decision.next_best_action)
        self.assertIn("merged_problem_block", decision.trigger_reasons)


if __name__ == "__main__":
    unittest.main()
