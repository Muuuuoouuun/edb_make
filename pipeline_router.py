#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from structured_schema import BlockType, PageModel


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _round_score(value: float) -> float:
    return round(_clamp(value), 4)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


@dataclass
class PageDifficultyProfile:
    page_id: str
    segmentation_risk: float
    ocr_risk: float
    grouping_risk: float
    overall_risk: float
    tier: str
    reasons: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    page_id: str
    route: str
    should_use_ai: bool
    next_best_action: str | None
    trigger_reasons: list[str] = field(default_factory=list)
    profile: PageDifficultyProfile | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "route": self.route,
            "should_use_ai": self.should_use_ai,
            "next_best_action": self.next_best_action,
            "trigger_reasons": list(self.trigger_reasons),
            "profile": self.profile.to_metadata() if self.profile else None,
        }


def build_page_difficulty_profile(page: PageModel, *, ocr_mode: str) -> PageDifficultyProfile:
    segmentation_diagnostics = _collect_segmentation_diagnostics(page)
    ocr_diagnostics = _collect_ocr_diagnostics(page, ocr_mode=ocr_mode)
    grouping_diagnostics = _collect_grouping_diagnostics(page)

    segmentation_risk, segmentation_reasons = _score_segmentation(segmentation_diagnostics)
    ocr_risk, ocr_reasons = _score_ocr(ocr_diagnostics, ocr_mode=ocr_mode)
    grouping_risk, grouping_reasons = _score_grouping(grouping_diagnostics, page)

    overall_risk = _round_score(max(segmentation_risk, ocr_risk, grouping_risk))
    tier = "green"
    if overall_risk >= 0.66:
        tier = "red"
    elif overall_risk >= 0.33:
        tier = "yellow"

    reasons = list(dict.fromkeys([*segmentation_reasons, *ocr_reasons, *grouping_reasons]))
    return PageDifficultyProfile(
        page_id=page.page_id,
        segmentation_risk=segmentation_risk,
        ocr_risk=ocr_risk,
        grouping_risk=grouping_risk,
        overall_risk=overall_risk,
        tier=tier,
        reasons=reasons,
        diagnostics={
            "segmentation": segmentation_diagnostics,
            "ocr": ocr_diagnostics,
            "grouping": grouping_diagnostics,
        },
    )


def decide_page_route(
    page: PageModel,
    *,
    ocr_mode: str,
    ai_enabled: bool,
    ai_mode: str,
) -> RouteDecision:
    profile = build_page_difficulty_profile(page, ocr_mode=ocr_mode)
    normalized_ai_mode = (ai_mode or "").strip().lower()

    should_use_ai = False
    route = "local_only"
    next_best_action: str | None = None
    trigger_reasons = list(profile.reasons)

    if normalized_ai_mode == "force" and ai_enabled:
        should_use_ai = True
        route = "ai_patch"
        trigger_reasons = list(dict.fromkeys(["forced", *trigger_reasons]))
    elif ai_enabled and profile.tier == "red":
        should_use_ai = True
        route = "ai_patch"
    elif profile.tier == "yellow":
        route = "local_only"
        next_best_action = "local_retry"
    else:
        route = "local_only"

    return RouteDecision(
        page_id=page.page_id,
        route=route,
        should_use_ai=should_use_ai,
        next_best_action=next_best_action,
        trigger_reasons=trigger_reasons,
        profile=profile,
    )


def _collect_segmentation_diagnostics(page: PageModel) -> dict[str, Any]:
    page_metadata = dict(page.metadata or {})
    diagnostics: dict[str, Any] = {}
    page_area_px = max(page.width_px * page.height_px, 1)

    # Current HEAD emits segmentation stats directly on page.metadata, with a
    # nested segmentation_stats dict for the same values. Keep both readable.
    for source in (
        page_metadata,
        _mapping(page_metadata.get("segmentation_stats")),
        _mapping(page_metadata.get("segmentation_diagnostics")),
    ):
        for key in (
            "segmenter",
            "segmentation_mode",
            "page_area_px",
            "block_count",
            "text_block_count",
            "image_block_count",
            "large_block_threshold",
            "large_block_count",
            "large_block_ratio",
            "max_block_area_ratio",
            "mean_block_area_ratio",
            "fallback_reasons",
            "fallback_reason",
            "fallback_reason_count",
            "has_fallback_reason",
            "content_box_area_ratio",
            "board_region_area_ratio",
            "document_split_block_count",
            "document_split_applied",
            "candidate_count",
            "expanded_candidate_count",
            "merged_candidate_count",
            "split_applied",
        ):
            if key in source and key not in diagnostics:
                diagnostics[key] = source[key]

    # Keep a stable summary even if the metadata source only exposes the raw
    # page payload and not the nested stats objects.
    board_region = _mapping(page_metadata.get("board_region"))
    content_box = _mapping(page_metadata.get("content_box"))
    if "board_region_area_ratio" not in diagnostics and board_region:
        board_area_ratio = _coerce_float(board_region.get("area_ratio"), -1.0)
        if board_area_ratio < 0.0:
            board_area_ratio = (_coerce_float(board_region.get("width")) * _coerce_float(board_region.get("height"))) / page_area_px
        diagnostics["board_region_area_ratio"] = board_area_ratio
    if "content_box_area_ratio" not in diagnostics and content_box:
        content_box_area_ratio = _coerce_float(content_box.get("area_ratio"), -1.0)
        if content_box_area_ratio < 0.0:
            content_box_area_ratio = (_coerce_float(content_box.get("width")) * _coerce_float(content_box.get("height"))) / page_area_px
        diagnostics["content_box_area_ratio"] = content_box_area_ratio

    fallback_reasons = _coerce_list_of_strings(diagnostics.get("fallback_reasons"))
    if not fallback_reasons:
        raw_reason = diagnostics.get("fallback_reason")
        if raw_reason:
            fallback_reasons = [str(raw_reason)]
    if not fallback_reasons:
        fallback_reasons = [str(block.metadata.get("fallback_reason")) for block in page.blocks if block.metadata.get("fallback_reason")]
    diagnostics["fallback_reasons"] = list(dict.fromkeys(fallback_reasons))
    diagnostics["fallback_reason"] = diagnostics["fallback_reasons"][0] if diagnostics["fallback_reasons"] else diagnostics.get("fallback_reason")
    diagnostics["fallback_reason_count"] = _coerce_int(diagnostics.get("fallback_reason_count"), len(diagnostics["fallback_reasons"]))
    diagnostics["has_fallback_reason"] = bool(diagnostics["fallback_reasons"])

    block_area_ratios = [
        _coerce_float(block.metadata.get("block_area_ratio"))
        for block in page.blocks
        if isinstance(block.metadata.get("block_area_ratio"), (int, float, str))
    ]
    if "large_block_count" not in diagnostics:
        diagnostics["large_block_count"] = sum(1 for block in page.blocks if block.metadata.get("large_block"))
    diagnostics["large_block_count"] = _coerce_int(diagnostics.get("large_block_count"))
    if "large_block_ratio" not in diagnostics:
        diagnostics["large_block_ratio"] = diagnostics["large_block_count"] / max(len(page.blocks), 1)
    diagnostics["large_block_ratio"] = _coerce_float(diagnostics.get("large_block_ratio"), 0.0)
    if "max_block_area_ratio" not in diagnostics:
        diagnostics["max_block_area_ratio"] = max(block_area_ratios) if block_area_ratios else 0.0
    diagnostics["max_block_area_ratio"] = _coerce_float(diagnostics.get("max_block_area_ratio"), 0.0)
    if "mean_block_area_ratio" not in diagnostics:
        diagnostics["mean_block_area_ratio"] = (sum(block_area_ratios) / len(block_area_ratios)) if block_area_ratios else 0.0
    diagnostics["mean_block_area_ratio"] = _coerce_float(diagnostics.get("mean_block_area_ratio"), 0.0)
    diagnostics["block_count"] = _coerce_int(diagnostics.get("block_count"), len(page.blocks))
    diagnostics["page_area_px"] = _coerce_int(diagnostics.get("page_area_px"), page_area_px)
    diagnostics["document_split_block_count"] = _coerce_int(diagnostics.get("document_split_block_count"))
    diagnostics["document_split_applied"] = bool(diagnostics.get("document_split_applied"))
    diagnostics["candidate_count"] = _coerce_int(diagnostics.get("candidate_count"))
    diagnostics["expanded_candidate_count"] = _coerce_int(diagnostics.get("expanded_candidate_count"))
    diagnostics["merged_candidate_count"] = _coerce_int(diagnostics.get("merged_candidate_count"))
    diagnostics["split_applied"] = bool(diagnostics.get("split_applied"))

    return {
        "segmentation_mode": diagnostics.get("segmentation_mode") or diagnostics.get("segmenter") or "unknown",
        "segmenter": diagnostics.get("segmenter") or "unknown",
        "block_count": diagnostics["block_count"],
        "large_block_count": diagnostics["large_block_count"],
        "large_block_ratio": diagnostics["large_block_ratio"],
        "fallback_reasons": diagnostics["fallback_reasons"],
        "fallback_reason": diagnostics["fallback_reason"],
        "fallback_reason_count": diagnostics["fallback_reason_count"],
        "has_fallback_reason": diagnostics["has_fallback_reason"],
        "content_box_area_ratio": _coerce_float(diagnostics.get("content_box_area_ratio"), 0.0),
        "board_region_area_ratio": _coerce_float(diagnostics.get("board_region_area_ratio"), 0.0),
        "document_split_block_count": diagnostics["document_split_block_count"],
        "document_split_applied": diagnostics["document_split_applied"],
        "candidate_count": diagnostics["candidate_count"],
        "expanded_candidate_count": diagnostics["expanded_candidate_count"],
        "merged_candidate_count": diagnostics["merged_candidate_count"],
        "split_applied": diagnostics["split_applied"],
        "page_area_px": diagnostics["page_area_px"],
        "max_block_area_ratio": diagnostics["max_block_area_ratio"],
        "mean_block_area_ratio": diagnostics["mean_block_area_ratio"],
    }


def _collect_ocr_diagnostics(page: PageModel, *, ocr_mode: str) -> dict[str, Any]:
    text_blocks = [block for block in page.blocks if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}]
    empty_blocks = sum(1 for block in text_blocks if not (block.text and block.text.strip()))
    confidence_values = [float(block.confidence) for block in text_blocks if block.confidence is not None]
    low_confidence_blocks = sum(1 for block in text_blocks if block.confidence is None or block.confidence < 0.55 or not (block.text and block.text.strip()))
    cache_hits = sum(1 for block in page.blocks if block.metadata.get("ocr_cache_hit"))
    cache_misses = sum(1 for block in page.blocks if block.metadata.get("ocr_cache_miss"))
    line_count = 0
    latency_values: list[float] = []
    backend_names: dict[str, int] = {}
    for block in page.blocks:
        backend = str(block.metadata.get("ocr_backend") or "none")
        backend_names[backend] = backend_names.get(backend, 0) + 1
        if isinstance(block.metadata.get("ocr_line_count"), (int, float)):
            line_count += int(block.metadata["ocr_line_count"])
        elif block.ocr_lines:
            line_count += len(block.ocr_lines)
        if isinstance(block.metadata.get("ocr_latency_ms"), (int, float)):
            latency_values.append(float(block.metadata["ocr_latency_ms"]))
    text_block_count = len(text_blocks)
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
    return {
        "ocr_mode": ocr_mode,
        "text_block_count": text_block_count,
        "empty_text_block_count": empty_blocks,
        "empty_text_ratio": empty_blocks / max(text_block_count, 1),
        "low_confidence_block_count": low_confidence_blocks,
        "low_confidence_ratio": low_confidence_blocks / max(text_block_count, 1),
        "avg_confidence": avg_confidence,
        "recognized_line_count": line_count,
        "ocr_cache_hit_count": cache_hits,
        "ocr_cache_miss_count": cache_misses,
        "backend_counts": backend_names,
        "backend_latency_ms_avg": (sum(latency_values) / len(latency_values)) if latency_values else None,
    }


def _collect_grouping_diagnostics(page: PageModel) -> dict[str, Any]:
    page_metadata = dict(page.metadata or {})
    metadata = _mapping(page_metadata.get("grouping_diagnostics"))

    marker_counts = _mapping(metadata.get("marker_counts"))
    fallback_stats = _mapping(metadata.get("fallback_grouping_stats"))
    problem_number_source_counts = _mapping(metadata.get("problem_number_source_counts"))
    block_diagnostics = metadata.get("block_diagnostics") if isinstance(metadata.get("block_diagnostics"), list) else []

    # Assemble-page now records the authoritative counts in nested dicts, so
    # prefer those and only fall back to recomputing from blocks/problems.
    problem_marker_count = _coerce_int(marker_counts.get("problem_marker_block_count") or metadata.get("problem_marker_count") or page_metadata.get("problem_marker_count"))
    choice_marker_count = _coerce_int(marker_counts.get("choice_marker_block_count") or metadata.get("choice_marker_count") or page_metadata.get("choice_marker_count"))
    marker_conflict_count = _coerce_int(marker_counts.get("marker_conflict_block_count") or metadata.get("marker_conflict_count") or page_metadata.get("marker_conflict_count"))
    fallback_grouping_problem_count = _coerce_int(metadata.get("fallback_grouping_problem_count") or page_metadata.get("fallback_grouping_problem_count"))
    forced_problem_start_count = _coerce_int(marker_counts.get("forced_problem_start_block_count") or metadata.get("forced_problem_start_count") or page_metadata.get("forced_problem_start_count"))
    problem_number_block_count = _coerce_int(marker_counts.get("problem_number_block_count") or metadata.get("problem_number_block_count") or page_metadata.get("problem_number_block_count"))

    if not problem_marker_count and block_diagnostics:
        problem_marker_count = sum(1 for item in block_diagnostics if bool(_mapping(item).get("problem_marker")))
    if not choice_marker_count and block_diagnostics:
        choice_marker_count = sum(1 for item in block_diagnostics if bool(_mapping(item).get("choice_marker")))
    if not marker_conflict_count and block_diagnostics:
        marker_conflict_count = sum(1 for item in block_diagnostics if bool(_mapping(item).get("marker_conflict")))
    if not problem_number_block_count and block_diagnostics:
        problem_number_block_count = sum(1 for item in block_diagnostics if _mapping(item).get("problem_number") is not None)
    if not problem_number_source_counts and block_diagnostics:
        for item in block_diagnostics:
            source = _mapping(item).get("problem_number_source")
            if source:
                source_key = str(source)
                problem_number_source_counts[source_key] = problem_number_source_counts.get(source_key, 0) + 1

    if not problem_marker_count:
        problem_marker_count = sum(1 for block in page.blocks if block.metadata.get("problem_marker"))
    if not choice_marker_count:
        choice_marker_count = sum(1 for block in page.blocks if block.metadata.get("choice_marker"))
    if not marker_conflict_count:
        marker_conflict_count = sum(1 for block in page.blocks if block.metadata.get("marker_conflict"))
    if not problem_number_block_count:
        problem_number_block_count = sum(1 for block in page.problems if block.metadata.get("problem_number") is not None)
    if not problem_number_source_counts:
        for problem in page.problems:
            source = str(problem.metadata.get("problem_number_source") or "")
            if source:
                problem_number_source_counts[source] = problem_number_source_counts.get(source, 0) + 1

    grouping_mode = str(page_metadata.get("grouping_mode") or metadata.get("grouping_mode") or "default")
    grouping_source = str(page_metadata.get("grouping_source") or metadata.get("grouping_source") or "rule_based")
    fallback_grouping = bool(page_metadata.get("fallback_grouping") or metadata.get("fallback_grouping"))
    if not fallback_grouping and isinstance(fallback_stats.get("used"), bool):
        fallback_grouping = bool(fallback_stats.get("used"))
    if fallback_grouping:
        fallback_grouping_problem_count = fallback_grouping_problem_count or _coerce_int(
            fallback_stats.get("problem_count"),
            sum(1 for problem in page.problems if problem.metadata.get("fallback_grouping")),
        )
    else:
        fallback_grouping_problem_count = 0
    return {
        "grouping_source": grouping_source,
        "grouping_mode": grouping_mode,
        "problem_count": len(page.problems),
        "block_count": len(page.blocks),
        "problem_marker_count": problem_marker_count,
        "choice_marker_count": choice_marker_count,
        "marker_conflict_count": marker_conflict_count,
        "forced_problem_start_count": forced_problem_start_count,
        "problem_number_block_count": problem_number_block_count,
        "fallback_grouping_problem_count": fallback_grouping_problem_count,
        "fallback_grouping": fallback_grouping,
        "marker_counts": {
            "problem_marker_block_count": problem_marker_count,
            "choice_marker_block_count": choice_marker_count,
            "marker_conflict_block_count": marker_conflict_count,
            "forced_problem_start_block_count": forced_problem_start_count,
            "problem_number_block_count": problem_number_block_count,
        },
        "fallback_grouping_stats": {
            "used": fallback_grouping,
            "trigger_reasons": _coerce_list_of_strings(_mapping(fallback_stats).get("trigger_reasons")),
            "block_count": len(page.blocks),
            "problem_count": len(page.problems),
            "problem_marker_block_count": problem_marker_count,
            "choice_marker_block_count": choice_marker_count,
            "marker_conflict_block_count": marker_conflict_count,
            "fallback_reason_block_count": _coerce_int(fallback_stats.get("fallback_reason_block_count")),
        },
        "problem_number_source_counts": dict(problem_number_source_counts),
    }


def _score_segmentation(diagnostics: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    block_count = int(diagnostics.get("block_count") or 0)
    large_block_ratio = float(diagnostics.get("large_block_ratio") or 0.0)
    max_block_area_ratio = float(diagnostics.get("max_block_area_ratio") or 0.0)
    fallback_reasons = [str(item) for item in diagnostics.get("fallback_reasons") or []]

    if block_count <= 1:
        score += 0.48
        reasons.append("sparse_segmentation")
    if large_block_ratio >= 0.5 or max_block_area_ratio >= 0.72:
        score += 0.4
        reasons.append("large_block_dominance")
    if diagnostics.get("document_split_applied"):
        score += 0.12
    if diagnostics.get("candidate_count") and int(diagnostics.get("candidate_count") or 0) <= 1:
        score += 0.18
    if fallback_reasons:
        score += 0.5
        reasons.extend(fallback_reasons)

    return _round_score(score), list(dict.fromkeys(reasons))


def _score_ocr(diagnostics: dict[str, Any], *, ocr_mode: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    low_confidence_ratio = float(diagnostics.get("low_confidence_ratio") or 0.0)
    empty_text_ratio = float(diagnostics.get("empty_text_ratio") or 0.0)
    avg_confidence = diagnostics.get("avg_confidence")

    if (ocr_mode or "").strip().lower() in {"none", "noop"}:
        score += 0.34
        reasons.append("ocr_disabled")
    if empty_text_ratio >= 0.5:
        score += 0.4
        reasons.append("textless_blocks")
    if low_confidence_ratio >= 0.5:
        score += 0.45
        reasons.append("low_ocr_confidence")
    if isinstance(avg_confidence, (int, float)) and float(avg_confidence) < 0.55:
        score += 0.3
        reasons.append("low_avg_confidence")

    return _round_score(score), list(dict.fromkeys(reasons))


def _score_grouping(diagnostics: dict[str, Any], page: PageModel) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    problem_count = int(diagnostics.get("problem_count") or len(page.problems))
    block_count = int(diagnostics.get("block_count") or len(page.blocks))
    problem_marker_count = int(diagnostics.get("problem_marker_count") or 0)
    choice_marker_count = int(diagnostics.get("choice_marker_count") or 0)
    marker_conflict_count = int(diagnostics.get("marker_conflict_count") or 0)
    fallback_grouping_problem_count = int(diagnostics.get("fallback_grouping_problem_count") or 0)
    problem_number_block_count = int(diagnostics.get("problem_number_block_count") or 0)
    fallback_grouping = bool(diagnostics.get("fallback_grouping"))

    if block_count > 1 and problem_marker_count == 0:
        score += 0.45
        reasons.append("no_problem_markers")
    if block_count > 1 and problem_marker_count == 0 and choice_marker_count == 0:
        score += 0.18
        reasons.append("no_problem_or_choice_markers")
    if marker_conflict_count > 0:
        score += 0.48
        reasons.append("marker_conflicts")
    if fallback_grouping_problem_count > 0:
        score += 0.52
        reasons.append("fallback_grouping")
    if fallback_grouping:
        score += 0.18
        reasons.append("fallback_grouping_mode")
    if block_count > 1 and problem_count == block_count:
        score += 0.42
        reasons.append("problem_per_block")
    if problem_number_block_count > 0 and block_count > 1 and problem_number_block_count == block_count:
        score += 0.18
        reasons.append("problem_number_per_block")
    if _looks_like_full_page_image(page):
        score += 0.6
        reasons.append("full_page_image")

    return _round_score(score), list(dict.fromkeys(reasons))


def _looks_like_full_page_image(page: PageModel) -> bool:
    if len(page.blocks) != 1:
        return False
    block = page.blocks[0]
    if block.block_type not in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
        return False
    return block.bbox.area >= float(page.width_px * page.height_px) * 0.75
