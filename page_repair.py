#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib import error, request

from PIL import Image

from assemble_page import detect_choice_block, detect_problem_start, group_problem_units
from pipeline_cache import PipelineCache
from pipeline_router import decide_page_route
from preprocess import PreparedPage
from structured_schema import BlockType, ContentBlock, PageModel


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_SUPPORTED_PROVIDER_ALIASES = {"gemini", "google", "claude", "anthropic", "openai"}


@dataclass(slots=True)
class AIFallbackConfig:
    mode: str = "off"
    provider: str = "gemini"
    model: str = ""
    threshold: float = 0.72
    max_regions: int = 30
    timeout_ms: int = 18000
    save_debug: bool = False
    fail_on_error: bool = False

    @property
    def resolved_model(self) -> str:
        if self.model.strip():
            return self.model.strip()
        return "gemini-2.5-pro"

    @property
    def normalized_provider(self) -> str:
        # All AI repair traffic is served by Gemini now. Legacy provider
        # values are accepted but normalized to keep existing configs working.
        return "gemini"

    @property
    def normalized_mode(self) -> str:
        normalized = self.mode.strip().lower()
        if normalized in {"auto", "force"}:
            return normalized
        return "off"

    @property
    def enabled(self) -> bool:
        return self.normalized_mode != "off"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "mode": self.normalized_mode,
            "provider": self.normalized_provider,
            "model": self.resolved_model,
            "threshold": self.threshold,
            "max_regions": self.max_regions,
            "timeout_ms": self.timeout_ms,
            "save_debug": self.save_debug,
            "fail_on_error": self.fail_on_error,
        }


def build_ai_fallback_config(
    *,
    mode: str = "off",
    provider: str = "gemini",
    model: str = "",
    threshold: float = 0.72,
    max_regions: int = 30,
    timeout_ms: int = 18000,
    save_debug: bool = False,
    fail_on_error: bool = False,
) -> AIFallbackConfig:
    return AIFallbackConfig(
        mode=mode,
        provider=provider,
        model=model,
        threshold=threshold,
        max_regions=max_regions,
        timeout_ms=timeout_ms,
        save_debug=save_debug,
        fail_on_error=fail_on_error,
    )


def repair_page_model(
    prepared_page: PreparedPage,
    page: PageModel,
    *,
    ocr_mode: str,
    config: AIFallbackConfig | None = None,
    cache: PipelineCache | None = None,
) -> PageModel:
    resolved_config = config or AIFallbackConfig()
    pipeline_cache = cache or PipelineCache.for_source(prepared_page.source_path)
    baseline = group_problem_units(page)
    route_decision = decide_page_route(
        baseline,
        ocr_mode=ocr_mode,
        ai_enabled=resolved_config.enabled,
        ai_mode=resolved_config.normalized_mode,
    )
    baseline.metadata["difficulty_profile"] = route_decision.profile.to_metadata() if route_decision.profile else {}
    baseline.metadata["route_decision"] = route_decision.to_metadata()
    summary: dict[str, Any] = {
        "enabled": resolved_config.enabled,
        "mode": resolved_config.normalized_mode,
        "provider": resolved_config.provider,
        "model": resolved_config.resolved_model,
        "ocr_mode": ocr_mode,
        "attempted": False,
        "applied": False,
        "cache_hit": False,
        "status": "disabled" if not resolved_config.enabled else "skipped",
        "route": route_decision.route,
        "route_tier": route_decision.profile.tier if route_decision.profile else "unknown",
        "trigger_reasons": list(route_decision.trigger_reasons),
        "baseline_problem_count": len(baseline.problems),
        "baseline_block_count": len(baseline.blocks),
    }
    if not resolved_config.enabled:
        baseline.metadata["ai_fallback"] = summary
        return baseline

    trigger_reasons = list(route_decision.trigger_reasons)
    if not route_decision.should_use_ai:
        summary["status"] = "local_retry_recommended" if route_decision.next_best_action == "local_retry" else "not_needed"
        if route_decision.next_best_action:
            summary["next_best_action"] = route_decision.next_best_action
        baseline.metadata["ai_fallback"] = summary
        return baseline

    # `force` mode is an explicit user opt-in to always attempt AI repair —
    # don't suppress it on busy pages.
    if (
        resolved_config.normalized_mode != "force"
        and resolved_config.max_regions > 0
        and len(baseline.blocks) > resolved_config.max_regions
    ):
        summary["status"] = "too_many_blocks"
        summary["skip_reason"] = "max_regions_exceeded"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    provider_key = resolved_config.normalized_provider
    summary["provider"] = provider_key
    if resolved_config.provider.strip().lower() not in _SUPPORTED_PROVIDER_ALIASES:
        summary["status"] = "provider_pending"
        summary["skip_reason"] = "provider_not_implemented"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    cached_repair = pipeline_cache.load_ai_repair(
        page=baseline,
        provider=provider_key,
        model=resolved_config.resolved_model,
        trigger_reasons=trigger_reasons,
    )
    if cached_repair is not None:
        repair_payload, response_id = cached_repair
        validation_error = _validate_repair_payload(repair_payload, baseline.blocks)
        if validation_error is None:
            repaired = _apply_repair_payload(
                baseline,
                repair_payload,
                trigger_reasons=trigger_reasons,
            )
            repaired = group_problem_units(replace(repaired, problems=[]))
            summary.update(
                {
                    "applied": True,
                    "cache_hit": True,
                    "status": "cache_hit",
                    "response_id": response_id,
                    "repaired_problem_count": len(repaired.problems),
                    "ai_notes": list(repair_payload.get("notes") or []),
                }
            )
            repaired.metadata["difficulty_profile"] = baseline.metadata.get("difficulty_profile", {})
            repaired.metadata["route_decision"] = baseline.metadata.get("route_decision", {})
            repaired.metadata["ai_fallback"] = summary
            _annotate_problem_metadata(repaired, trigger_reasons)
            return repaired

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        summary["status"] = "missing_api_key"
        summary["skip_reason"] = "GEMINI_API_KEY not set"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    summary["attempted"] = True
    start_time = time.perf_counter()
    try:
        repair_payload, response_id = _request_ai_repair_with_retry(
            prepared_page=prepared_page,
            page=baseline,
            config=resolved_config,
            trigger_reasons=trigger_reasons,
            api_key=api_key,
        )
        latency_ms = int(round((time.perf_counter() - start_time) * 1000.0))
    except Exception as exc:
        summary["status"] = "error"
        summary["error"] = str(exc)
        if resolved_config.fail_on_error:
            raise
        baseline.metadata["ai_fallback"] = summary
        return baseline

    validation_error = _validate_repair_payload(repair_payload, baseline.blocks)
    if validation_error:
        summary["status"] = "invalid_response"
        summary["error"] = validation_error
        baseline.metadata["ai_fallback"] = summary
        return baseline

    repaired = _apply_repair_payload(
        baseline,
        repair_payload,
        trigger_reasons=trigger_reasons,
    )
    repaired = group_problem_units(replace(repaired, problems=[]))
    pipeline_cache.save_ai_repair(
        page=baseline,
        provider=provider_key,
        model=resolved_config.resolved_model,
        trigger_reasons=trigger_reasons,
        repair_payload=repair_payload,
        response_id=response_id,
    )

    summary.update(
        {
            "applied": True,
            "status": "applied",
            "latency_ms": latency_ms,
            "response_id": response_id,
            "repaired_problem_count": len(repaired.problems),
            "ai_notes": list(repair_payload.get("notes") or []),
        }
    )
    repaired.metadata["difficulty_profile"] = baseline.metadata.get("difficulty_profile", {})
    repaired.metadata["route_decision"] = baseline.metadata.get("route_decision", {})
    repaired.metadata["ai_fallback"] = summary
    _annotate_problem_metadata(repaired, trigger_reasons)
    _maybe_write_debug_artifacts(
        prepared_page=prepared_page,
        page=repaired,
        repair_payload=repair_payload,
        summary=summary,
        config=resolved_config,
    )
    return repaired


def _select_repair_reasons(page: PageModel, config: AIFallbackConfig, *, ocr_mode: str) -> list[str]:
    reasons: list[str] = []
    if config.normalized_mode == "force":
        reasons.append("forced")

    if len(page.blocks) <= 1:
        return reasons

    if ocr_mode.strip().lower() in {"none", "noop"}:
        reasons.append("ocr_disabled")

    if not any(detect_problem_start(block) for block in page.blocks):
        reasons.append("no_problem_markers")

    if any(problem.metadata.get("fallback_grouping") for problem in page.problems):
        reasons.append("fallback_grouping")

    if len(page.problems) == len(page.blocks):
        reasons.append("problem_per_block")

    if _low_confidence_ratio(page) >= 0.5:
        reasons.append("low_confidence")

    if any(_block_has_overlap_marker(block) for block in page.blocks):
        reasons.append("choice_problem_marker_overlap")

    if _looks_like_full_page_image(page):
        reasons.append("full_page_image")

    return list(dict.fromkeys(reasons))


def _low_confidence_ratio(page: PageModel) -> float:
    eligible = [
        block
        for block in page.blocks
        if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}
    ]
    if not eligible:
        return 0.0
    low_confidence = 0
    for block in eligible:
        if not (block.text and block.text.strip()):
            low_confidence += 1
            continue
        if block.confidence is None or block.confidence < 0.55:
            low_confidence += 1
    return low_confidence / len(eligible)


def _block_has_overlap_marker(block: ContentBlock) -> bool:
    if not block.text:
        return False
    stripped = block.text.strip()
    return stripped.startswith(tuple(f"{index})" for index in range(1, 10)))


def _looks_like_full_page_image(page: PageModel) -> bool:
    if len(page.blocks) != 1:
        return False
    block = page.blocks[0]
    if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
        return False
    return block.bbox.area >= float(page.width_px * page.height_px) * 0.75


def _request_ai_repair_with_retry(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    """Call Gemini for the repair. Retry once on transient failure."""
    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(2.0)
        try:
            return _request_gemini_repair(
                prepared_page=prepared_page,
                page=page,
                config=config,
                trigger_reasons=trigger_reasons,
                api_key=api_key,
            )
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"AI repair failed after retries: {last_exc}") from last_exc


def _request_gemini_repair(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    """Call the Gemini generateContent API with a JSON response schema."""
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": _image_to_base64(prepared_page.image),
                        }
                    },
                    {"text": _build_repair_prompt(page, trigger_reasons)},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _repair_schema(),
            "maxOutputTokens": 1536,
            "temperature": 0.0,
        },
    }
    url = f"{GEMINI_API_BASE}/{config.resolved_model}:generateContent?key={api_key}"
    raw_response = _post_json(
        url,
        payload,
        headers={"Content-Type": "application/json"},
        timeout_ms=config.timeout_ms,
    )
    candidates = raw_response.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini response did not include any candidates")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    json_text = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")
    )
    if not json_text:
        finish_reason = candidates[0].get("finishReason") or "unknown"
        raise RuntimeError(f"Gemini response contained no text (finish={finish_reason})")
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini response JSON decode failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini response was not a JSON object")
    return parsed, raw_response.get("responseId") or raw_response.get("id")


def _repair_schema() -> dict[str, Any]:
    # Schema follows Gemini's OpenAPI 3.0 subset — no additionalProperties.
    return {
        "type": "object",
        "properties": {
            "problem_start_block_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "choice_block_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "figure_block_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "display_titles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "required": ["block_id", "title"],
                },
            },
            "notes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "problem_start_block_ids",
            "choice_block_ids",
            "figure_block_ids",
            "display_titles",
            "notes",
        ],
    }


def _build_repair_prompt(page: PageModel, trigger_reasons: list[str]) -> str:
    block_lines = []
    for index, block in enumerate(page.blocks, start=1):
        # Include up to 3 top OCR lines for richer spatial context
        ocr_preview: list[str] = []
        for line in (block.ocr_lines or [])[:3]:
            if line.text and line.text.strip():
                ocr_preview.append(line.text.strip()[:80])

        entry: dict[str, Any] = {
            "order": index,
            "block_id": block.block_id,
            "block_type": block.block_type.value,
            "text": (block.text or "")[:300],
            "confidence": round(block.confidence, 3) if block.confidence is not None else None,
            "bbox": {
                "left": round(block.bbox.left, 1),
                "top": round(block.bbox.top, 1),
                "width": round(block.bbox.width, 1),
                "height": round(block.bbox.height, 1),
            },
        }
        if ocr_preview:
            entry["ocr_lines"] = ocr_preview
        meta_keys = ("segmenter", "column_index", "question_band_index", "fallback_reason", "split_from_band")
        meta = {k: block.metadata[k] for k in meta_keys if k in block.metadata}
        if meta:
            entry["meta"] = meta
        block_lines.append(json.dumps(entry, ensure_ascii=False))

    return "\n".join(
        [
            "You analyze a scanned Korean exam page and classify its text blocks.",
            f"Page size: {page.width_px}×{page.height_px}px  |  Subject: {page.subject.value}",
            "",
            "Korean exam conventions:",
            "  - Problems are numbered: '1.', '2)', '문제 3', '[4]', '문항5' etc.",
            "  - Boxed texts like <보기> or [조건] are NEVER the start of a problem.",
            "  - A ㄱ/ㄴ/ㄷ enumerated list inside the stem is NOT a choice block—",
            "    it is part of the question. Choice blocks are the final ①–⑤ options.",
            "  - Answer choices MUST BE final options like ① ② ③ ④ ⑤  or  (1) (2) …",
            "  - Figures / diagrams / physics–chemistry drawings appear below the stem.",
            "  - Never mix the choices of Problem 1 with Problem 2.",
            "",
            "Output rules (STRICT):",
            "  - Use ONLY the block_ids listed below. Do NOT invent IDs.",
            "  - problem_start_block_ids: first block of each NUMBERED question, reading order.",
            "  - choice_block_ids: standalone ①–⑤ (or A–E) answer-option blocks.",
            "  - figure_block_ids: image, diagram, graph, or table content blocks.",
            "  - If the page contains a single question, return only its first block as a problem start.",
            "  - Prefer minimal reassignment—only reclassify when clearly wrong.",
            f"  - Trigger reasons: {', '.join(trigger_reasons)}",
            "",
            "Blocks (JSON, reading order top→bottom):",
            *block_lines,
        ]
    )


def _image_to_base64(image: Image.Image) -> str:
    """Return a base64-encoded JPEG string (no data-URL prefix)."""
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=86, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_ms: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    timeout_seconds = max(1.0, timeout_ms / 1000.0)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini request failed with HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc


def _validate_repair_payload(payload: dict[str, Any], blocks: list[ContentBlock]) -> str | None:
    known_ids = {block.block_id for block in blocks}
    start_ids = list(payload.get("problem_start_block_ids") or [])
    choice_ids = list(payload.get("choice_block_ids") or [])
    figure_ids = list(payload.get("figure_block_ids") or [])

    if not start_ids:
        return "problem_start_block_ids must include at least one block"

    invalid_ids = {
        block_id
        for block_id in [*start_ids, *choice_ids, *figure_ids]
        if block_id not in known_ids
    }
    if invalid_ids:
        return f"unknown block ids returned: {sorted(invalid_ids)}"

    if set(start_ids) & set(choice_ids):
        return "problem start and choice block ids overlap"

    if len(set(start_ids)) != len(start_ids):
        return "problem_start_block_ids must be unique"

    ordered_ids = [block.block_id for block in blocks]
    start_positions = [ordered_ids.index(block_id) for block_id in start_ids]
    if start_positions != sorted(start_positions):
        return "problem_start_block_ids must be in reading order"

    return None


def _apply_repair_payload(
    page: PageModel,
    payload: dict[str, Any],
    *,
    trigger_reasons: list[str],
) -> PageModel:
    start_ids = set(payload.get("problem_start_block_ids") or [])
    choice_ids = set(payload.get("choice_block_ids") or [])
    figure_ids = set(payload.get("figure_block_ids") or [])
    display_titles = {
        str(item["block_id"]): str(item["title"]).strip()
        for item in payload.get("display_titles") or []
        if isinstance(item, dict) and item.get("block_id") and str(item.get("title") or "").strip()
    }

    for block in page.blocks:
        block.metadata.pop("force_problem_start", None)
        block.metadata.pop("ai_grouping_role", None)
        block.metadata["grouping_source"] = "ai_fallback"
        block.metadata["grouping_reason"] = list(trigger_reasons)

        if block.block_id in display_titles:
            block.metadata["display_title"] = display_titles[block.block_id]

        if block.block_id in start_ids:
            block.metadata["force_problem_start"] = True
            block.metadata["ai_grouping_role"] = "problem_start"
            if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
                block.block_type = BlockType.TITLE
            continue

        if block.block_id in choice_ids:
            block.metadata["ai_grouping_role"] = "choice"
            block.block_type = BlockType.CHOICE
            continue

        if block.block_id in figure_ids:
            block.metadata["ai_grouping_role"] = "figure"
            if block.block_type not in {BlockType.DIAGRAM, BlockType.TABLE}:
                block.block_type = BlockType.IMAGE
            continue

        if block.block_type in {BlockType.TITLE, BlockType.SECTION} and not (block.text and block.text.strip()):
            block.block_type = BlockType.STEM

    return page


def _annotate_problem_metadata(page: PageModel, trigger_reasons: list[str]) -> None:
    for problem in page.problems:
        problem.metadata["grouping_source"] = "ai_fallback"
        problem.metadata["grouping_reason"] = list(trigger_reasons)


def _maybe_write_debug_artifacts(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    repair_payload: dict[str, Any],
    summary: dict[str, Any],
    config: AIFallbackConfig,
) -> None:
    if not config.save_debug:
        return
    source_path = Path(prepared_page.source_path) if prepared_page.source_path else None
    if source_path is None:
        return
    debug_dir = source_path.parent / ".pipeline_cache" / "ai_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_path = debug_dir / f"{page.page_id}_repair.json"
    debug_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "repair_payload": repair_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    page.metadata.setdefault("ai_fallback", {})
    page.metadata["ai_fallback"]["debug_path"] = str(debug_path)
