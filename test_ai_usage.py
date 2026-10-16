from ai_usage import (
    aggregate_token_usage,
    image_generation_usage_event,
    normalize_gemini_token_usage,
    summarize_ai_cost,
    summarize_token_efficiency,
)


def test_normalize_gemini_token_usage_keeps_provider_totals() -> None:
    usage = normalize_gemini_token_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 30,
                "thoughtsTokenCount": 8,
                "cachedContentTokenCount": 4,
                "toolUsePromptTokenCount": 2,
                "totalTokenCount": 158,
                "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 70}],
            }
        }
    )

    assert usage == {
        "prompt_token_count": 120,
        "candidates_token_count": 30,
        "thoughts_token_count": 8,
        "cached_content_token_count": 4,
        "tool_use_prompt_token_count": 2,
        "total_token_count": 158,
        "request_count": 1,
    }


def test_aggregate_token_usage_sums_requests_without_deriving_total() -> None:
    total = aggregate_token_usage(
        [
            {
                "request_count": 1,
                "prompt_token_count": 100,
                "candidates_token_count": 20,
                "thoughts_token_count": 5,
                "total_token_count": 125,
            },
            {
                "request_count": 1,
                "prompt_token_count": 40,
                "candidates_token_count": 10,
                "thoughts_token_count": 2,
                "total_token_count": 52,
            },
            None,
        ]
    )

    assert total["request_count"] == 2
    assert total["prompt_token_count"] == 140
    assert total["candidates_token_count"] == 30
    assert total["thoughts_token_count"] == 7
    assert total["total_token_count"] == 177


def test_summarize_ai_cost_prices_cached_input_output_and_thoughts() -> None:
    summary = summarize_ai_cost(
        [
            {
                "model": "gemini-3.5-flash-lite",
                "stage": "ocr",
                "request_count": 1,
                "prompt_token_count": 1_000_000,
                "cached_content_token_count": 200_000,
                "candidates_token_count": 100_000,
                "thoughts_token_count": 20_000,
            }
        ],
        usd_krw_rate=1400,
    )

    # 0.8M regular input + 0.2M cached input + 0.12M output.
    assert summary["estimated_usd"] == 0.546
    assert summary["estimated_krw"] == 764.4
    assert summary["priced_request_count"] == 1
    assert summary["by_model"]["gemini-3.5-flash-lite"]["request_count"] == 1


def test_image_generation_event_uses_fixed_gemini_image_price() -> None:
    event = image_generation_usage_event(
        provider="gemini",
        model="gemini-3.1-flash-image",
        usage={},
        image_size="1k",
    )
    summary = summarize_ai_cost([event], usd_krw_rate=1400)

    assert summary["estimated_usd"] == 0.067
    assert summary["estimated_krw"] == 93.8
    assert summary["by_stage"]["image_reconstruction"]["request_count"] == 1


def test_summarize_token_efficiency_exposes_cache_and_thinking_waste() -> None:
    summary = summarize_token_efficiency(
        [
            {
                "model": "gemini-3.5-flash-lite",
                "stage": "ocr",
                "request_count": 1,
                "prompt_token_count": 1000,
                "cached_content_token_count": 200,
                "candidates_token_count": 80,
                "thoughts_token_count": 20,
                "total_token_count": 1100,
            },
            {
                "model": "gemini-3.5-flash",
                "stage": "ocr_escalation",
                "request_count": 1,
                "prompt_token_count": 500,
                "candidates_token_count": 50,
                "thoughts_token_count": 0,
                "total_token_count": 550,
            },
        ]
    )

    assert summary["request_count"] == 2
    assert summary["total_token_count"] == 1650
    assert summary["avg_total_tokens_per_request"] == 825.0
    assert summary["cache_hit_token_ratio"] == 0.1333
    assert summary["thought_share_of_generated"] == 0.1333
    assert summary["by_stage"]["ocr"]["thought_token_count"] == 20
    assert summary["by_model"]["gemini-3.5-flash-lite"]["cached_prompt_token_count"] == 200
