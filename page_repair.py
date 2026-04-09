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
from preprocess import PreparedPage
from structured_schema import BlockType, ContentBlock, PageModel


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(slots=True)
class AIFallbackConfig:
    mode: str = "off"
    provider: str = "openai"
    model: str = "gpt-5.4-mini"
    threshold: float = 0.72
    max_regions: int = 18
    timeout_ms: int = 12000
    save_debug: bool = False
    fail_on_error: bool = False

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
            "provider": self.provider,
            "model": self.model,
            "threshold": self.threshold,
            "max_regions": self.max_regions,
            "timeout_ms": self.timeout_ms,
            "save_debug": self.save_debug,
            "fail_on_error": self.fail_on_error,
        }


def build_ai_fallback_config(
    *,
    mode: str = "off",
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    threshold: float = 0.72,
    max_regions: int = 18,
    timeout_ms: int = 12000,
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
) -> PageModel:
    resolved_config = config or AIFallbackConfig()
    baseline = group_problem_units(page)
    summary: dict[str, Any] = {
        "enabled": resolved_config.enabled,
        "mode": resolved_config.normalized_mode,
        "provider": resolved_config.provider,
        "model": resolved_config.model,
        "ocr_mode": ocr_mode,
        "attempted": False,
        "applied": False,
        "status": "disabled" if not resolved_config.enabled else "skipped",
        "trigger_reasons": [],
        "baseline_problem_count": len(baseline.problems),
        "baseline_block_count": len(baseline.blocks),
    }
    if not resolved_config.enabled:
        baseline.metadata["ai_fallback"] = summary
        return baseline

    trigger_reasons = _select_repair_reasons(baseline, resolved_config, ocr_mode=ocr_mode)
    summary["trigger_reasons"] = list(trigger_reasons)
    if not trigger_reasons:
        summary["status"] = "not_needed"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    if resolved_config.max_regions > 0 and len(baseline.blocks) > resolved_config.max_regions:
        summary["status"] = "too_many_blocks"
        summary["skip_reason"] = "max_regions_exceeded"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    if resolved_config.provider.strip().lower() != "openai":
        summary["status"] = "provider_pending"
        summary["skip_reason"] = "provider_not_implemented"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        summary["status"] = "missing_api_key"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    summary["attempted"] = True
    start_time = time.perf_counter()
    try:
        repair_payload, response_id = _request_openai_repair(
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


def _request_openai_repair(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    payload = {
        "model": config.model,
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _build_repair_prompt(page, trigger_reasons)},
                    {
                        "type": "input_image",
                        "image_url": _image_to_data_url(prepared_page.image),
                        "detail": "high",
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "question_page_repair",
                "strict": True,
                "schema": _repair_schema(),
            }
        },
    }
    raw_response = _post_json(
        OPENAI_RESPONSES_URL,
        payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout_ms=config.timeout_ms,
    )
    content_text = _extract_response_text(raw_response)
    parsed = json.loads(content_text)
    return parsed, raw_response.get("id")


def _repair_schema() -> dict[str, Any]:
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
                    "additionalProperties": False,
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
        "additionalProperties": False,
    }


def _build_repair_prompt(page: PageModel, trigger_reasons: list[str]) -> str:
    block_lines = []
    for index, block in enumerate(page.blocks, start=1):
        block_lines.append(
            json.dumps(
                {
                    "order": index,
                    "block_id": block.block_id,
                    "block_type": block.block_type.value,
                    "text": (block.text or "")[:180],
                    "confidence": block.confidence,
                    "bbox": {
                        "left": round(block.bbox.left, 1),
                        "top": round(block.bbox.top, 1),
                        "width": round(block.bbox.width, 1),
                        "height": round(block.bbox.height, 1),
                    },
                    "metadata": {
                        key: block.metadata.get(key)
                        for key in ("segmenter", "column_index", "question_band_index", "fallback_reason")
                        if key in block.metadata
                    },
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(
        [
            "You repair OCR-based question grouping for scanned exam pages.",
            "Use only the provided block_ids. Never invent or reorder blocks.",
            "Return the first block_id for each question in reading order.",
            "Mark answer-choice blocks only when they are standalone options, not the start of a question stem.",
            "Mark figure blocks only for image/diagram/table-style content.",
            "If the whole page is one question, include only the first block in problem_start_block_ids.",
            "Prefer minimal changes over aggressive rewrites.",
            f"Trigger reasons: {', '.join(trigger_reasons)}",
            "Blocks:",
            *block_lines,
        ]
    )


def _image_to_data_url(image: Image.Image) -> str:
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


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
        raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = payload.get("output")
    if isinstance(output, list):
        collected: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    collected.append(text)
        if collected:
            return "\n".join(collected)

    raise RuntimeError("OpenAI response did not include structured text output")


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
