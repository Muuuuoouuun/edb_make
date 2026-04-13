#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote

from PIL import Image

from assemble_page import detect_choice_block, detect_problem_start, group_problem_units
from pipeline_cache import PipelineCache
from pipeline_router import decide_page_route
from preprocess import PreparedPage
from structured_schema import BlockType, ContentBlock, PageModel


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
GEMINI_GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
AI_INTERVENTION_LABELS = {
    0: "structure_crop_layout",
    1: "parse_recolor",
    2: "rebuild_upscale",
}
AI_PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "openai": {
        "supported": True,
        "supported_modes": ["off", "auto", "force"],
        "api_key_envs": ("OPENAI_API_KEY",),
        "supports_vision": True,
        "default_model": "gpt-4o-mini",
    },
    "claude": {
        "supported": True,
        "supported_modes": ["off", "auto", "force"],
        "api_key_envs": ("ANTHROPIC_API_KEY",),
        "supports_vision": True,
        "default_model": "claude-sonnet-4-6",
    },
    "anthropic": {
        "supported": True,
        "supported_modes": ["off", "auto", "force"],
        "api_key_envs": ("ANTHROPIC_API_KEY",),
        "supports_vision": True,
        "default_model": "claude-sonnet-4-6",
    },
    "gemini": {
        "supported": True,
        "supported_modes": ["off", "auto", "force"],
        "api_key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "supports_vision": True,
        "default_model": "gemini-2.5-flash",
    },
}
AI_PROVIDER_ENV_NAMES = tuple(
    dict.fromkeys(
        env_name
        for spec in AI_PROVIDER_SPECS.values()
        for env_name in spec.get("api_key_envs", ())
    )
)
AI_ENV_FILE_CANDIDATES = (
    Path(".app_runtime/ai.env"),
    Path(".app_runtime/.env"),
    Path(".env.local"),
    Path(".env"),
    Path("ai.env"),
)


def normalize_ai_intervention_level(value: Any, default: int = 0) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        level = default
    return max(0, min(2, level))


def ai_intervention_label(level: int) -> str:
    return AI_INTERVENTION_LABELS.get(normalize_ai_intervention_level(level), AI_INTERVENTION_LABELS[0])


def ai_intervention_metadata(level: int) -> dict[str, Any]:
    normalized_level = normalize_ai_intervention_level(level)
    metadata = {
        "intervention_level": normalized_level,
        "intervention_label": ai_intervention_label(normalized_level),
        "supports_grouping_repair": True,
        "supports_parse_cleanup": normalized_level >= 1,
        "supports_output_rebuild": normalized_level >= 2,
    }
    if normalized_level == 0:
        metadata["description"] = "문제 인식, 크롭, 배치 보정 중심"
    elif normalized_level == 1:
        metadata["description"] = "파싱 안정화와 색/출력 보정까지 포함"
    else:
        metadata["description"] = "재구성 우선 판단과 업스케일 출력까지 포함"
    return metadata


def canonical_ai_provider_name(value: Any, default: str = "openai") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"claude", "anthropic"}:
        return "anthropic"
    if normalized in {"gemini", "google"}:
        return "gemini"
    if normalized in {"openai"}:
        return normalized
    return default


def ai_provider_api_key_envs(provider_name: Any) -> tuple[str, ...]:
    canonical = canonical_ai_provider_name(provider_name, default="")
    if canonical == "anthropic":
        return tuple(AI_PROVIDER_SPECS["anthropic"]["api_key_envs"])
    if canonical == "gemini":
        return tuple(AI_PROVIDER_SPECS["gemini"]["api_key_envs"])
    if canonical == "openai":
        return tuple(AI_PROVIDER_SPECS["openai"]["api_key_envs"])
    return ()


def _candidate_ai_env_roots() -> list[Path]:
    roots: list[Path] = [Path.cwd(), Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        key = str(resolved)
        if key in seen:
            continue
        unique_roots.append(resolved)
        seen.add(key)
    return unique_roots


def _candidate_ai_env_files() -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in _candidate_ai_env_roots():
        for relative_path in AI_ENV_FILE_CANDIDATES:
            candidate = (root / relative_path).resolve()
            if not candidate.is_file():
                continue
            key = str(candidate)
            if key in seen:
                continue
            files.append(candidate)
            seen.add(key)
    return files


def _parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        return None

    value = raw_value.strip()
    if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def load_runtime_ai_env() -> dict[str, str]:
    loaded: dict[str, str] = {}
    recognized_names = set(AI_PROVIDER_ENV_NAMES)
    for env_file in _candidate_ai_env_files():
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            parsed = _parse_env_assignment(line)
            if parsed is None:
                continue
            key, value = parsed
            value = value.strip()
            if key not in recognized_names or not value:
                continue
            loaded.setdefault(key, value)

    for key, value in loaded.items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
    return loaded


def resolve_ai_provider_api_key(
    provider_name: Any,
    *,
    explicit_api_key: str | None = None,
) -> tuple[str, str, list[str]]:
    envs = list(ai_provider_api_key_envs(provider_name))
    provided_key = (explicit_api_key or "").strip()
    if provided_key:
        return provided_key, "request", envs

    load_runtime_ai_env()
    for env_name in envs:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value, env_name, envs

    if not envs:
        return "", "", envs
    if len(envs) == 1:
        return "", envs[0], envs
    return "", " or ".join(envs), envs


def build_ai_capabilities() -> dict[str, Any]:
    loaded_runtime_env = load_runtime_ai_env()
    providers: dict[str, Any] = {}
    missing_api_keys: list[str] = []
    ready_providers: list[str] = []

    for provider_name, spec in AI_PROVIDER_SPECS.items():
        envs = list(spec.get("api_key_envs") or [])
        api_key_present = any(os.environ.get(env_name, "").strip() for env_name in envs)
        api_key_source = ""
        for env_name in envs:
            if not os.environ.get(env_name, "").strip():
                continue
            api_key_source = "env_file" if env_name in loaded_runtime_env else "environment"
            break
        supported = bool(spec.get("supported"))
        ready = supported and (api_key_present or not envs)
        status = "ready" if ready else ("missing_api_key" if supported and envs else str(spec.get("status") or "unsupported"))
        providers[provider_name] = {
            "supported": supported,
            "supported_modes": list(spec.get("supported_modes") or []),
            "api_key_env": envs[0] if len(envs) == 1 else " or ".join(envs),
            "api_key_envs": envs,
            "api_key_present": api_key_present,
            "api_key_source": api_key_source,
            "available": ready,
            "status": status,
            "supports_vision": bool(spec.get("supports_vision")),
        }
        if envs and not api_key_present and supported:
            missing_api_keys.extend(envs)
        if ready:
            ready_providers.append(provider_name)

    return {
        "available": bool(ready_providers),
        "supported_modes": ["off", "auto", "force"],
        "providers": providers,
        "ready_providers": ready_providers,
        "missing_api_keys": sorted(set(missing_api_keys)),
        "default_provider": "openai",
    }


@dataclass
class AIFallbackConfig:
    mode: str = "off"
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    threshold: float = 0.72
    max_regions: int = 18
    timeout_ms: int = 18000
    save_debug: bool = False
    fail_on_error: bool = False
    intervention_level: int = 0

    @property
    def resolved_model(self) -> str:
        if self.model.strip():
            return self.model.strip()
        provider_key = self.provider_key
        if provider_key == "anthropic":
            return str(AI_PROVIDER_SPECS["anthropic"]["default_model"])
        if provider_key == "gemini":
            return str(AI_PROVIDER_SPECS["gemini"]["default_model"])
        return str(AI_PROVIDER_SPECS["openai"]["default_model"])

    @property
    def provider_key(self) -> str:
        return canonical_ai_provider_name(self.provider)

    def _is_claude_provider(self) -> bool:
        return self.provider_key == "anthropic"

    def _is_gemini_provider(self) -> bool:
        return self.provider_key == "gemini"

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
            "model": self.resolved_model,
            "threshold": self.threshold,
            "max_regions": self.max_regions,
            "timeout_ms": self.timeout_ms,
            "save_debug": self.save_debug,
            "fail_on_error": self.fail_on_error,
            **ai_intervention_metadata(self.intervention_level),
        }


def build_ai_fallback_config(
    *,
    mode: str = "off",
    provider: str = "openai",
    model: str = "",
    api_key: str = "",
    threshold: float = 0.72,
    max_regions: int = 18,
    timeout_ms: int = 18000,
    save_debug: bool = False,
    fail_on_error: bool = False,
    intervention_level: int = 0,
) -> AIFallbackConfig:
    return AIFallbackConfig(
        mode=mode,
        provider=provider,
        model=model,
        api_key=api_key or "",
        threshold=threshold,
        max_regions=max_regions,
        timeout_ms=timeout_ms,
        save_debug=save_debug,
        fail_on_error=fail_on_error,
        intervention_level=normalize_ai_intervention_level(intervention_level),
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
    baseline.metadata["ai_intervention"] = ai_intervention_metadata(resolved_config.intervention_level)
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
        **ai_intervention_metadata(resolved_config.intervention_level),
    }
    if not resolved_config.enabled:
        if route_decision.profile and route_decision.profile.tier == "red":
            summary["status"] = "ai_recommended"
            summary["next_best_action"] = "ai_recommended"
        elif route_decision.next_best_action == "local_retry":
            summary["status"] = "local_retry_recommended"
            summary["next_best_action"] = route_decision.next_best_action
        baseline.metadata["ai_fallback"] = summary
        return baseline

    trigger_reasons = list(route_decision.trigger_reasons)
    if not route_decision.should_use_ai:
        summary["status"] = "local_retry_recommended" if route_decision.next_best_action == "local_retry" else "not_needed"
        if route_decision.next_best_action:
            summary["next_best_action"] = route_decision.next_best_action
        baseline.metadata["ai_fallback"] = summary
        return baseline

    if resolved_config.max_regions > 0 and len(baseline.blocks) > resolved_config.max_regions:
        summary["status"] = "too_many_blocks"
        summary["skip_reason"] = "max_regions_exceeded"
        baseline.metadata["ai_fallback"] = summary
        return baseline

    provider_key = resolved_config.provider_key
    if provider_key not in {"openai", "anthropic", "gemini"}:
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
            repaired.metadata["ai_intervention"] = baseline.metadata.get("ai_intervention", {})
            repaired.metadata["ai_fallback"] = summary
            _annotate_problem_metadata(repaired, trigger_reasons)
            return repaired

    api_key, key_env, _ = resolve_ai_provider_api_key(
        provider_key,
        explicit_api_key=resolved_config.api_key,
    )
    if not api_key:
        summary["status"] = "missing_api_key"
        summary["skip_reason"] = f"{key_env} not set"
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
    repaired.metadata["ai_intervention"] = baseline.metadata.get("ai_intervention", {})
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
    """Route to the correct AI provider and retry once on transient failure."""
    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(2.0)
        try:
            if config._is_claude_provider():
                return _request_anthropic_repair(
                    prepared_page=prepared_page,
                    page=page,
                    config=config,
                    trigger_reasons=trigger_reasons,
                    api_key=api_key,
                )
            if config._is_gemini_provider():
                return _request_gemini_repair(
                    prepared_page=prepared_page,
                    page=page,
                    config=config,
                    trigger_reasons=trigger_reasons,
                    api_key=api_key,
                )
            return _request_openai_repair(
                prepared_page=prepared_page,
                page=page,
                config=config,
                trigger_reasons=trigger_reasons,
                api_key=api_key,
            )
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"AI repair failed after retries: {last_exc}") from last_exc


def _request_openai_repair(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    payload = {
        "model": config.resolved_model,
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _build_repair_prompt(page, trigger_reasons, config)},
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


def _request_anthropic_repair(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    """Call the Claude Messages API using tool_use for structured JSON output."""
    tool_spec = {
        "name": "repair_question_grouping",
        "description": (
            "Classify exam page blocks into problem starts, answer-choice blocks, "
            "and figure blocks based on the page image and block metadata."
        ),
        "input_schema": _repair_schema(),
    }
    payload = {
        "model": config.resolved_model,
        "max_tokens": 1024,
        "tools": [tool_spec],
        "tool_choice": {"type": "tool", "name": "repair_question_grouping"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": _image_to_base64(prepared_page.image),
                        },
                    },
                    {"type": "text", "text": _build_repair_prompt(page, trigger_reasons, config)},
                ],
            }
        ],
    }
    raw_response = _post_json(
        ANTHROPIC_MESSAGES_URL,
        payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        },
        timeout_ms=config.timeout_ms,
    )
    for content_block in raw_response.get("content") or []:
        if (
            isinstance(content_block, dict)
            and content_block.get("type") == "tool_use"
            and content_block.get("name") == "repair_question_grouping"
        ):
            return content_block["input"], raw_response.get("id")
    raise RuntimeError("Claude response did not contain expected tool_use block")


def _request_gemini_repair(
    *,
    prepared_page: PreparedPage,
    page: PageModel,
    config: AIFallbackConfig,
    trigger_reasons: list[str],
    api_key: str,
) -> tuple[dict[str, Any], str | None]:
    model_name = quote(config.resolved_model, safe="")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _build_repair_prompt(page, trigger_reasons, config)},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": _image_to_base64(prepared_page.image),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _repair_schema(),
        },
    }
    raw_response = _post_json(
        GEMINI_GENERATE_CONTENT_URL.format(model=model_name),
        payload,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        timeout_ms=config.timeout_ms,
        service_name="Gemini",
    )
    content_text = _extract_gemini_response_text(raw_response)
    parsed = json.loads(content_text)
    return parsed, raw_response.get("responseId")


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
            "image_fallback_block_ids": {
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
            "image_fallback_block_ids",
            "display_titles",
            "notes",
        ],
        "additionalProperties": False,
    }


def _build_repair_prompt(page: PageModel, trigger_reasons: list[str], config: AIFallbackConfig) -> str:
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
            "  - Answer choices: ① ② ③ ④ ⑤  or  (1) (2) … or  ㄱ) ㄴ) ㄷ) style",
            "  - A ㄱ/ㄴ/ㄷ enumerated list inside the stem is NOT a choice block—",
            "    it is part of the question. Choice blocks are the final ①–⑤ options.",
            "  - Figures / diagrams / physics–chemistry drawings appear below the stem.",
            "",
            "Output rules (STRICT):",
            "  - Use ONLY the block_ids listed below. Do NOT invent IDs.",
            "  - problem_start_block_ids: first block of each numbered question, reading order.",
            "  - choice_block_ids: standalone ①–⑤ (or A–E) answer-option blocks.",
            "  - figure_block_ids: image, diagram, graph, or table content blocks.",
            "  - image_fallback_block_ids: blocks that should stay as image records because editable text reconstruction would be unsafe.",
            "  - If the page contains a single question, return only its first block as a problem start.",
            "  - Prefer minimal reassignment—only reclassify when clearly wrong.",
            f"  - Requested intervention level: {config.intervention_level} ({ai_intervention_label(config.intervention_level)})",
            "  - Level 0: focus on problem starts, grouping, and layout only.",
            "  - Level 1: additionally flag damaged formula/choice/text blocks for image fallback when parse quality looks risky.",
            "  - Level 2: be stricter about unsafe text reconstruction and aggressively mark complex blocks for image fallback.",
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


def _image_to_data_url(image: Image.Image) -> str:
    return f"data:image/jpeg;base64,{_image_to_base64(image)}"


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_ms: int,
    service_name: str = "AI",
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    timeout_seconds = max(1.0, timeout_ms / 1000.0)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{service_name} request failed with HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{service_name} request failed: {exc.reason}") from exc


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


def _extract_gemini_response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        collected: list[str] = []
        finish_reasons: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            finish_reason = str(candidate.get("finishReason") or "").strip()
            if finish_reason:
                finish_reasons.append(finish_reason)
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    collected.append(text)
        if collected:
            return "\n".join(collected)
        if finish_reasons:
            raise RuntimeError(
                f"Gemini response did not include structured text output (finish reason: {', '.join(sorted(set(finish_reasons)))})"
            )

    prompt_feedback = payload.get("promptFeedback")
    if prompt_feedback:
        raise RuntimeError(f"Gemini blocked the request: {json.dumps(prompt_feedback, ensure_ascii=False)}")
    raise RuntimeError("Gemini response did not include structured text output")


def _validate_repair_payload(payload: dict[str, Any], blocks: list[ContentBlock]) -> str | None:
    known_ids = {block.block_id for block in blocks}
    start_ids = list(payload.get("problem_start_block_ids") or [])
    choice_ids = list(payload.get("choice_block_ids") or [])
    figure_ids = list(payload.get("figure_block_ids") or [])
    image_fallback_ids = list(payload.get("image_fallback_block_ids") or [])

    if not start_ids:
        return "problem_start_block_ids must include at least one block"

    invalid_ids = {
        block_id
        for block_id in [*start_ids, *choice_ids, *figure_ids, *image_fallback_ids]
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
    image_fallback_ids = set(payload.get("image_fallback_block_ids") or [])
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
        if block.block_id in image_fallback_ids:
            block.metadata["ai_prefer_image_fallback"] = True
            block.metadata["ai_image_fallback_reason"] = "repair_recommendation"
        else:
            block.metadata.pop("ai_prefer_image_fallback", None)
            block.metadata.pop("ai_image_fallback_reason", None)

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
