#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from structured_schema import BlockType, PageModel


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _round_score(value: float) -> float:
    return round(_clamp(value), 4)


@dataclass(slots=True)
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


@dataclass(slots=True)
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
    metadata = dict(page.metadata.get("segmentation_diagnostics") or {})
    fallback_reasons = []
    if metadata.get("fallback_reason"):
        fallback_reasons.append(str(metadata["fallback_reason"]))
    if isinstance(metadata.get("fallback_reasons"), list):
        fallback_reasons.extend(str(item) for item in metadata["fallback_reasons"] if item)
    block_area_ratios = [
        float(block.metadata.get("block_area_ratio", 0.0))
        for block in page.blocks
        if isinstance(block.metadata.get("block_area_ratio"), (int, float))
    ]
    large_block_count = sum(1 for block in page.blocks if block.metadata.get("large_block"))
    return {
        "segmentation_mode": metadata.get("segmentation_mode") or metadata.get("segmenter") or "unknown",
        "block_count": int(metadata.get("block_count") or len(page.blocks)),
        "large_block_count": large_block_count,
        "large_block_ratio": float(metadata.get("large_block_ratio") or (large_block_count / max(len(page.blocks), 1))),
        "fallback_reasons": list(dict.fromkeys(fallback_reasons)),
        "content_box_area_ratio": float(metadata.get("content_box_area_ratio") or 0.0),
        "board_region_area_ratio": float(metadata.get("board_region_area_ratio") or 0.0),
        "document_split_block_count": int(metadata.get("document_split_block_count") or 0),
        "document_split_applied": bool(metadata.get("document_split_applied")),
        "candidate_count": int(metadata.get("candidate_count") or 0),
        "expanded_candidate_count": int(metadata.get("expanded_candidate_count") or 0),
        "merged_candidate_count": int(metadata.get("merged_candidate_count") or 0),
        "max_block_area_ratio": max(block_area_ratios) if block_area_ratios else 0.0,
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
    metadata = dict(page.metadata.get("grouping_diagnostics") or {})
    problem_marker_count = sum(1 for block in page.blocks if block.metadata.get("problem_marker"))
    choice_marker_count = sum(1 for block in page.blocks if block.metadata.get("choice_marker"))
    marker_conflict_count = sum(1 for block in page.blocks if block.metadata.get("marker_conflict"))
    fallback_grouping_problem_count = sum(1 for problem in page.problems if problem.metadata.get("fallback_grouping"))
    problem_number_source_counts: dict[str, int] = {}
    for problem in page.problems:
        source = str(problem.metadata.get("problem_number_source") or "")
        if source:
            problem_number_source_counts[source] = problem_number_source_counts.get(source, 0) + 1
    return {
        "grouping_source": metadata.get("grouping_source") or "rule_based",
        "grouping_mode": metadata.get("grouping_mode") or "default",
        "problem_count": len(page.problems),
        "block_count": len(page.blocks),
        "problem_marker_count": int(metadata.get("problem_marker_count") or problem_marker_count),
        "choice_marker_count": int(metadata.get("choice_marker_count") or choice_marker_count),
        "marker_conflict_count": int(metadata.get("marker_conflict_count") or marker_conflict_count),
        "fallback_grouping_problem_count": int(metadata.get("fallback_grouping_problem_count") or fallback_grouping_problem_count),
        "problem_number_source_counts": metadata.get("problem_number_source_counts") or problem_number_source_counts,
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
    marker_conflict_count = int(diagnostics.get("marker_conflict_count") or 0)
    fallback_grouping_problem_count = int(diagnostics.get("fallback_grouping_problem_count") or 0)

    if block_count > 1 and problem_marker_count == 0:
        score += 0.45
        reasons.append("no_problem_markers")
    if marker_conflict_count > 0:
        score += 0.48
        reasons.append("marker_conflicts")
    if fallback_grouping_problem_count > 0:
        score += 0.52
        reasons.append("fallback_grouping")
    if block_count > 1 and problem_count == block_count:
        score += 0.42
        reasons.append("problem_per_block")
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
