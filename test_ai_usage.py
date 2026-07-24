from ai_usage import aggregate_token_usage, normalize_gemini_token_usage


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
