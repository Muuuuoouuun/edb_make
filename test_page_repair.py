import json
import unittest
from unittest.mock import patch

from PIL import Image

import page_repair
from page_repair import build_ai_fallback_config
from preprocess import PreparedPage
from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject


class TestPageRepairConfig(unittest.TestCase):
    def test_max_tokens_propagates_to_gemini_payload(self):
        captured = {}

        def fake_post_json(url, payload, *, headers, timeout_ms):
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = headers
            captured["timeout_ms"] = timeout_ms
            return {
                "responseId": "response-1",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "problem_start_block_ids": ["block-1"],
                                            "choice_block_ids": [],
                                            "figure_block_ids": [],
                                            "display_titles": [],
                                            "notes": [],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
            }

        prepared_page = PreparedPage(
            page_id="page-1",
            source_path="sample.png",
            page_number=1,
            image=Image.new("RGB", (100, 120), "white"),
            original_size=(100, 120),
        )
        page = PageModel(
            page_id="page-1",
            width_px=100,
            height_px=120,
            subject=Subject.SCIENCE,
            blocks=[
                ContentBlock(
                    block_id="block-1",
                    block_type=BlockType.STEM,
                    bbox=Box(left=0, top=0, width=80, height=40),
                    reading_order=0,
                    text="1. 문제",
                )
            ],
        )
        config = build_ai_fallback_config(mode="force", max_tokens=6789, timeout_ms=12345)

        with patch.object(page_repair, "_image_to_base64", return_value="encoded-image"):
            with patch.object(page_repair, "_post_json", side_effect=fake_post_json):
                payload, response_id = page_repair._request_gemini_repair(
                    prepared_page=prepared_page,
                    page=page,
                    config=config,
                    trigger_reasons=["forced"],
                    api_key="test-key",
                )

        self.assertEqual(response_id, "response-1")
        self.assertEqual(payload["problem_start_block_ids"], ["block-1"])
        self.assertEqual(captured["timeout_ms"], 12345)
        self.assertEqual(captured["payload"]["generationConfig"]["maxOutputTokens"], 6789)

    def test_31_pro_falls_back_to_stable_pro_on_call_error(self):
        urls = []

        def fake_post_json(url, payload, *, headers, timeout_ms):
            urls.append(url)
            if "gemini-3.1-pro-preview" in url:
                raise RuntimeError("Gemini request failed with HTTP 404: model not found")
            return {
                "responseId": "fallback-response",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "problem_start_block_ids": ["block-1"],
                                            "choice_block_ids": [],
                                            "figure_block_ids": [],
                                            "display_titles": [],
                                            "notes": [],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
            }

        prepared_page = PreparedPage(
            page_id="page-1",
            source_path="sample.png",
            page_number=1,
            image=Image.new("RGB", (100, 120), "white"),
            original_size=(100, 120),
        )
        page = PageModel(
            page_id="page-1",
            width_px=100,
            height_px=120,
            subject=Subject.SCIENCE,
            blocks=[
                ContentBlock(
                    block_id="block-1",
                    block_type=BlockType.STEM,
                    bbox=Box(left=0, top=0, width=80, height=40),
                    reading_order=0,
                    text="1. 문제",
                )
            ],
        )
        config = build_ai_fallback_config(mode="force", timeout_ms=1000)

        with patch.object(page_repair, "_image_to_base64", return_value="encoded-image"):
            with patch.object(page_repair, "_post_json", side_effect=fake_post_json):
                payload, response_id, used_model, attempts = page_repair._request_ai_repair_with_model_fallback(
                    prepared_page=prepared_page,
                    page=page,
                    config=config,
                    trigger_reasons=["forced"],
                    api_key="test-key",
                )

        self.assertEqual(response_id, "fallback-response")
        self.assertEqual(payload["problem_start_block_ids"], ["block-1"])
        self.assertEqual(used_model, "gemini-2.5-pro")
        self.assertEqual(["error", "ok"], [attempt["status"] for attempt in attempts])
        self.assertTrue(any("gemini-3.1-pro-preview" in url for url in urls))
        self.assertTrue(any("gemini-2.5-pro" in url for url in urls))

    def test_repair_prompt_prioritizes_all_problem_starts_on_busy_pages(self):
        page = PageModel(
            page_id="page-1",
            width_px=100,
            height_px=500,
            subject=Subject.SCIENCE,
            blocks=[
                ContentBlock(
                    block_id=f"block-{idx}",
                    block_type=BlockType.STEM,
                    bbox=Box(left=0, top=idx * 40, width=80, height=30),
                    reading_order=idx,
                    text=f"{idx + 1}. 문제",
                )
                for idx in range(5)
            ],
        )

        prompt = page_repair._build_repair_prompt(page, ["forced"])

        self.assertIn("5 or more numbered questions", prompt)
        self.assertIn("Do not stop after the first 2 or 3", prompt)
        self.assertIn("omit problem_units before omitting any problem_start_block_ids", prompt)


if __name__ == "__main__":
    unittest.main()
