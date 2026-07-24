from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


GEMINI_TOKEN_FIELDS = {
    "promptTokenCount": "prompt_token_count",
    "candidatesTokenCount": "candidates_token_count",
    "thoughtsTokenCount": "thoughts_token_count",
    "cachedContentTokenCount": "cached_content_token_count",
    "toolUsePromptTokenCount": "tool_use_prompt_token_count",
    "totalTokenCount": "total_token_count",
}


def _nonnegative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def normalize_gemini_token_usage(response: Mapping[str, Any] | None) -> dict[str, int]:
    """Extract provider-reported token counts from a Gemini response.

    The response may contain extra per-modality details. The stable aggregate
    counters are enough for per-run cost reporting and avoid copying request or
    response content into diagnostic artifacts.
    """
    if not isinstance(response, Mapping):
        return {}
    raw_usage = response.get("usageMetadata")
    if not isinstance(raw_usage, Mapping):
        return {}

    usage = {
        output_name: _nonnegative_int(raw_usage.get(provider_name))
        for provider_name, output_name in GEMINI_TOKEN_FIELDS.items()
    }
    usage["request_count"] = 1
    return usage


def aggregate_token_usage(
    events: Iterable[Mapping[str, Any] | None],
) -> dict[str, int]:
    totals = {
        "request_count": 0,
        **{output_name: 0 for output_name in GEMINI_TOKEN_FIELDS.values()},
    }
    for event in events:
        if not isinstance(event, Mapping):
            continue
        for field in totals:
            totals[field] += _nonnegative_int(event.get(field))
    return totals


def token_usage_has_requests(usage: Mapping[str, Any] | None) -> bool:
    return bool(isinstance(usage, Mapping) and _nonnegative_int(usage.get("request_count")) > 0)
