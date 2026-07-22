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

    def test_pdf_text_marker_backend_stays_trusted_after_text_prefix_normalization(self):
        page = PageModel(
            page_id="pdf-page-text-prefix",
            width_px=600,
            height_px=800,
            subject=Subject.MATH,
            blocks=[
                _block(
                    f"b-{number}",
                    number * 100,
                    metadata={
                        "segmenter": "pdf-text-markers",
                        "ocr_backend": "pdf_text_marker",
                        "force_problem_start": True,
                        "problem_marker": True,
                        "problem_number": number,
                        "problem_number_source": "text_prefix",
                    },
                )
                for number in range(1, 5)
            ],
            problems=[_problem(f"p-{number}", number, source="text_prefix") for number in range(1, 5)],
            metadata={
                "segmenter": "pdf-text-markers",
                "segmentation_mode": "document",
                "block_count": 4,
                "large_block_ratio": 1.0,
                "pdf_text_marker_count": 4,
                "grouping_diagnostics": {
                    "grouping_source": "marker_grouping",
                    "grouping_mode": "marker",
                    "marker_counts": {"problem_marker_block_count": 4},
                    "fallback_grouping_stats": {
                        "used": False,
                        "problem_count": 4,
                    },
                    "problem_number_source_counts": {"text_prefix": 4},
                },
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

    def test_ocr_diagnostics_explain_fallback_timing_and_semantic_route(self):
        page = PageModel(
            page_id="ocr-backoff-page",
            width_px=600,
            height_px=800,
            subject=Subject.MATH,
            blocks=[
                _block(
                    "fallback-block",
                    100,
                    metadata={
                        "ocr_backend": "gemini",
                        "ocr_latency_ms": 120,
                        "ocr_fallback_reason": "network_or_timeout",
                        "ocr_fallback_message": (
                            "Gemini OCR is temporarily unavailable. "
                            "The block remains image-based for review."
                        ),
                        "ocr_retry_after_ms": 8000,
                        "ocr_circuit_open": True,
                        "ocr_cache_write_skipped": True,
                    },
                    text="fallback image",
                ),
                _block(
                    "recognized-block",
                    400,
                    metadata={"ocr_backend": "gemini", "ocr_latency_ms": 380},
                    text="2. recognized",
                ),
            ],
            problems=[_problem("p-2", 2, source="ocr_top_left")],
            metadata={
                "segmenter": "document-bands",
                "block_count": 2,
                "recognition_timing_ms": {"block_ocr": 510},
            },
        )

        decision = decide_page_route(page, ocr_mode="gemini", ai_enabled=False, ai_mode="off")
        diagnostics = decision.profile.diagnostics["ocr"]

        self.assertEqual({"network_or_timeout": 1}, diagnostics["fallback_reason_counts"])
        self.assertEqual(1, diagnostics["circuit_open_block_count"])
        self.assertEqual(1, diagnostics["ocr_cache_write_skipped_count"])
        self.assertEqual(8000, diagnostics["retry_after_ms_max"])
        self.assertEqual(250, diagnostics["backend_latency_ms_avg"])
        self.assertEqual(380, diagnostics["backend_latency_ms_p95"])
        self.assertEqual(510, diagnostics["processing_time_ms"])
        self.assertEqual("review_required_image_fallback", diagnostics["semantic_text_route"])
        self.assertIn("image-based for review", diagnostics["fallback_messages"][0])

    def test_ocr_p95_uses_nearest_rank_for_twenty_samples(self):
        page = PageModel(
            page_id="latency-p95",
            width_px=1000,
            height_px=1400,
            subject=Subject.MATH,
            blocks=[
                _block(
                    f"latency-{index}",
                    index * 20,
                    metadata={"ocr_backend": "local", "ocr_latency_ms": index},
                    text=f"{index}. problem",
                )
                for index in range(1, 21)
            ],
            problems=[],
            metadata={"segmenter": "document-bands", "block_count": 20},
        )

        decision = decide_page_route(page, ocr_mode="local", ai_enabled=False, ai_mode="off")

        self.assertEqual(19, decision.profile.diagnostics["ocr"]["backend_latency_ms_p95"])


if __name__ == "__main__":
    unittest.main()
