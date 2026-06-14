#!/usr/bin/env python3
"""Run the local HWP/HWPX export path against a folder of sample documents."""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_APP_URL = "http://127.0.0.1:8765"
HANGUL_EXTENSIONS = {".hwp", ".hwpx"}
NON_ACTIONABLE_RISK_FLAGS = {
    "marker_document_continuation",
    "ocr_disabled",
}
HWP_COUNT_MATCH_DISMISSIBLE_RISK_FLAGS = {
    "fallback_grouping",
    "large_block_dominance",
    "no_problem_markers",
    "problem_per_block",
    "sparse_segmentation",
}
HWP_PROBLEM_COUNT_MISMATCH_FLAG = "hwp_problem_count_mismatch"
HWP_OVERSEGMENTATION_FLAG = "hwp_oversegmentation"
SOURCE_PROBLEM_BBOX_OVERLAP_FLAG = "source_problem_bbox_overlap"
PASSAGE_GROUP_SOURCE_REUSE_FLAG = "passage_group_source_reuse"
CLASSIN_PREFLIGHT_BLOCKING_ISSUE_TYPES = {
    "board_placement_overlap",
    "low_ink_problem_image",
    "missing_problem_image",
    PASSAGE_GROUP_SOURCE_REUSE_FLAG,
    "small_problem_image",
    "source_problem_bbox_overlap",
    "unreadable_problem_image",
}
CLASSIN_PREFLIGHT_ISSUE_LABELS = {
    "board_placement_overlap": "판서 배치 겹침",
    "duplicate_problem_number": "중복 번호",
    "low_ink_problem_image": "이미지 내용 부족",
    "missing_problem_image": "문항 이미지 없음",
    "passage_missing_child_questions": "지문 하위 문항 누락",
    PASSAGE_GROUP_SOURCE_REUSE_FLAG: "지문 그룹 원본 중복",
    "passage_review_queue_remaining": "긴 지문 검수 남음",
    "review_flags_remaining": "검수 플래그 남음",
    "small_problem_image": "문항 이미지 작음",
    "source_problem_bbox_overlap": "원본 영역 겹침",
    "unreadable_problem_image": "문항 이미지 흐림",
}


def classin_preflight_issue_label(issue_type: Any) -> str:
    normalized = str(issue_type or "").strip()
    return CLASSIN_PREFLIGHT_ISSUE_LABELS.get(normalized, normalized)


def normalized_text(value: str | Path) -> str:
    return unicodedata.normalize("NFC", str(value))


def infer_subject(source_path: Path) -> str:
    name = normalized_text(source_path.name)
    if "수학" in name:
        return "수학"
    if "영어" in name:
        return "영어"
    if "과탐" in name:
        return "과학"
    if "사탐" in name:
        return "사회"
    if "국어" in name or "언어와 매체" in name or "화법과 작문" in name:
        return "국어"
    return "unknown"


def iter_hangul_files(source_dir: Path, *, include: list[str] | None = None, limit: int | None = None) -> list[Path]:
    files = sorted(
        [path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in HANGUL_EXTENSIONS],
        key=lambda path: normalized_text(path.name),
    )
    include_terms = [
        normalized_text(term).casefold()
        for term in (include or [])
        if str(term or "").strip()
    ]
    if include_terms:
        files = [
            path
            for path in files
            if all(term in normalized_text(path.name).casefold() for term in include_terms)
        ]
    if limit is not None and limit > 0:
        files = files[:limit]
    return files


def warning_messages(warnings: Any) -> list[str]:
    if not isinstance(warnings, list):
        return []
    messages: list[str] = []
    for warning in warnings:
        if isinstance(warning, dict):
            value = warning.get("message") or warning.get("error") or warning.get("kind")
        else:
            value = warning
        if value:
            messages.append(str(value))
    return messages


def collect_risk_flags(session: dict[str, Any]) -> list[str]:
    flags: set[str] = set()
    for key in ("pages", "problems", "items"):
        values = session.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            for flag in value.get("riskFlags") or value.get("risk_flags") or []:
                if flag:
                    flags.add(str(flag))
    return sorted(flags)


def _coerce_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, raw_count in value.items():
        flag = str(key or "").strip()
        if not flag:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[flag] = count
    return counts


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "match"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _risk_count(risk_counts: dict[str, int], flag: str) -> int:
    return _coerce_non_negative_int(_coerce_count_map(risk_counts).get(flag))


def hwp_problem_count_mismatch_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("hwp_problem_count_mismatch_count") or row.get("hwpProblemCountMismatchCount")
    )
    if explicit:
        return explicit
    flags = row.get("hwp_problem_count_mismatch_flags")
    if isinstance(flags, list) and flags:
        return len(flags)
    return _risk_count(row.get("risk_flag_counts") or {}, HWP_PROBLEM_COUNT_MISMATCH_FLAG)


def hwp_oversegmentation_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("hwp_oversegmentation_count") or row.get("hwpOversegmentationCount")
    )
    if explicit:
        return explicit
    return _risk_count(row.get("risk_flag_counts") or {}, HWP_OVERSEGMENTATION_FLAG)


def source_problem_bbox_overlap_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("source_problem_bbox_overlap_count") or row.get("sourceProblemBboxOverlapCount")
    )
    if explicit:
        return explicit
    return _risk_count(row.get("risk_flag_counts") or {}, SOURCE_PROBLEM_BBOX_OVERLAP_FLAG)


def source_problem_overlap_group_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("source_problem_overlap_group_count") or row.get("sourceProblemOverlapGroupCount")
    )
    if explicit:
        return explicit
    groups = row.get("source_problem_overlap_groups") or row.get("sourceProblemOverlapGroups")
    return len(groups) if isinstance(groups, list) else 0


def passage_group_source_reuse_group_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("passage_group_source_reuse_group_count")
        or row.get("passageGroupSourceReuseGroupCount")
    )
    if explicit:
        return explicit
    groups = row.get("passage_group_source_reuse_groups") or row.get("passageGroupSourceReuseGroups")
    return len(groups) if isinstance(groups, list) else 0


def _passage_group_child_count(group: dict[str, Any]) -> int:
    for key in ("problemNumbers", "problem_numbers", "childProblemNumbers", "child_problem_numbers"):
        values = group.get(key)
        if isinstance(values, list) and values:
            return len({str(value).strip() for value in values if str(value).strip()})
    raw_count = _coerce_non_negative_int(group.get("problemCount") or group.get("problem_count"))
    fragment_count = _coerce_non_negative_int(group.get("fragmentProblemCount") or group.get("fragment_problem_count"))
    return max(0, raw_count - fragment_count)


def _infer_passage_metrics_from_problems(problems: Any) -> dict[str, int]:
    if not isinstance(problems, list):
        return {
            "passage_group_count": 0,
            "passage_problem_count": 0,
            "passage_fragment_count": 0,
            "cross_page_passage_group_count": 0,
        }

    groups: dict[str, dict[str, Any]] = {}
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        group_id = str(problem.get("passageGroupId") or problem.get("passage_group_id") or "").strip()
        if not group_id:
            continue
        group = groups.setdefault(
            group_id,
            {
                "child_keys": set(),
                "fragment_count": 0,
                "source_page_ids": set(),
                "cross_page_hint": False,
            },
        )
        role = str(problem.get("passageRole") or problem.get("passage_role") or "").strip()
        if role == "passage_fragment":
            group["fragment_count"] += 1
        else:
            child_key = (
                problem.get("problemNumber")
                or problem.get("problem_number")
                or problem.get("title")
                or problem.get("id")
                or problem.get("problem_id")
            )
            child_key_text = str(child_key or "").strip()
            if child_key_text:
                group["child_keys"].add(child_key_text)
        source_page_id = str(
            problem.get("sourcePageId")
            or problem.get("source_page_id")
            or problem.get("pageId")
            or problem.get("page_id")
            or ""
        ).strip()
        if source_page_id:
            group["source_page_ids"].add(source_page_id)
        risk_flags = {
            str(flag or "").strip()
            for flag in (problem.get("riskFlags") or problem.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if "passage_cross_page_merge_check" in risk_flags:
            group["cross_page_hint"] = True

    return {
        "passage_group_count": len(groups),
        "passage_problem_count": sum(len(group["child_keys"]) for group in groups.values()),
        "passage_fragment_count": sum(int(group["fragment_count"]) for group in groups.values()),
        "cross_page_passage_group_count": sum(
            1
            for group in groups.values()
            if len(group["source_page_ids"]) > 1 or bool(group["cross_page_hint"])
        ),
    }


def _passage_metrics(payload: dict[str, Any], session: dict[str, Any], review_summary: dict[str, Any]) -> dict[str, int]:
    sources = [
        session,
        payload,
        review_summary,
        session.get("publishSummary") if isinstance(session.get("publishSummary"), dict) else None,
        session.get("publish_summary") if isinstance(session.get("publish_summary"), dict) else None,
        payload.get("publishSummary") if isinstance(payload.get("publishSummary"), dict) else None,
        payload.get("publish_summary") if isinstance(payload.get("publish_summary"), dict) else None,
    ]
    groups: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        raw_groups = source.get("passageGroups") or source.get("passage_groups")
        if isinstance(raw_groups, list):
            groups = [group for group in raw_groups if isinstance(group, dict)]
            if groups:
                break

    def first_count(*keys: str) -> int:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                count = _coerce_non_negative_int(source.get(key))
                if count:
                    return count
        return 0

    group_count = first_count("passageGroupCount", "passage_group_count") or len(groups)
    problem_count = first_count("passageProblemCount", "passage_problem_count")
    cross_page_count = first_count("crossPagePassageGroupCount", "cross_page_passage_group_count")
    fragment_count = first_count("passageFragmentCount", "passage_fragment_count", "fragmentProblemCount", "fragment_problem_count")
    inferred_problem_metrics = _infer_passage_metrics_from_problems(session.get("problems"))
    if not group_count:
        group_count = inferred_problem_metrics["passage_group_count"]
    if not problem_count:
        problem_count = inferred_problem_metrics["passage_problem_count"]
    if not cross_page_count:
        cross_page_count = inferred_problem_metrics["cross_page_passage_group_count"]
    if groups:
        if not problem_count:
            problem_count = sum(_passage_group_child_count(group) for group in groups)
        if not cross_page_count:
            cross_page_count = sum(
                1
                for group in groups
                if group.get("continuesAcrossPages") or group.get("continues_across_pages")
            )
        if not fragment_count:
            fragment_count = sum(
                _coerce_non_negative_int(group.get("fragmentProblemCount") or group.get("fragment_problem_count"))
                for group in groups
            )
    if not fragment_count:
        fragment_count = inferred_problem_metrics["passage_fragment_count"]
    return {
        "passage_group_count": group_count,
        "passage_problem_count": problem_count,
        "passage_fragment_count": fragment_count,
        "cross_page_passage_group_count": cross_page_count,
    }


def _passage_review_metrics(
    payload: dict[str, Any],
    session: dict[str, Any],
    review_summary: dict[str, Any],
) -> dict[str, Any]:
    sources = [
        session,
        payload,
        review_summary,
        session.get("publishSummary") if isinstance(session.get("publishSummary"), dict) else None,
        session.get("publish_summary") if isinstance(session.get("publish_summary"), dict) else None,
        payload.get("publishSummary") if isinstance(payload.get("publishSummary"), dict) else None,
        payload.get("publish_summary") if isinstance(payload.get("publish_summary"), dict) else None,
    ]
    items: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        raw_items = source.get("passageReviewItems") or source.get("passage_review_items")
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
            if items:
                break

    def first_count(*keys: str) -> int:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                count = _coerce_non_negative_int(source.get(key))
                if count:
                    return count
        return 0

    item_count = first_count("passageReviewItemCount", "passage_review_item_count") or len(items)
    cross_page_count = first_count(
        "crossPagePassageReviewItemCount",
        "cross_page_passage_review_item_count",
    )
    if not cross_page_count:
        cross_page_count = sum(
            1
            for item in items
            if item.get("continuesAcrossPages") or item.get("continues_across_pages")
        )

    reason_counts: Counter[str] = Counter()
    labels: list[str] = []
    seen_labels: set[str] = set()
    for item in items:
        label = str(
            item.get("numberLabel")
            or item.get("number_label")
            or item.get("groupId")
            or item.get("group_id")
            or ""
        ).strip()
        if label and label not in seen_labels:
            seen_labels.add(label)
            labels.append(label)
        reasons = item.get("reviewReasonCodes") or item.get("review_reason_codes") or []
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            reason_text = str(reason or "").strip()
            if reason_text:
                reason_counts[reason_text] += 1

    return {
        "passage_review_item_count": item_count,
        "cross_page_passage_review_item_count": cross_page_count,
        "passage_review_labels": labels,
        "passage_review_reason_counts": dict(sorted(reason_counts.items())),
    }


def _classin_preflight(payload: dict[str, Any], session: dict[str, Any] | None = None) -> dict[str, Any]:
    for source in (payload, session or {}):
        for key in ("classinPreflight", "classin_preflight"):
            preflight = source.get(key)
            if isinstance(preflight, dict):
                return preflight
    return {}


def _classin_preflight_issue_types(preflight: dict[str, Any]) -> list[str]:
    issues = preflight.get("issues")
    if not isinstance(issues, list):
        return []
    issue_types: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_type = str(issue.get("type") or "").strip()
        if not issue_type or issue_type in seen:
            continue
        seen.add(issue_type)
        issue_types.append(issue_type)
    return issue_types


def _classin_preflight_issue_type_count(preflight: dict[str, Any], issue_type: str) -> int:
    issues = preflight.get("issues")
    if not isinstance(issues, list):
        return 0
    target = str(issue_type or "").strip()
    if not target:
        return 0
    return sum(
        1
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("type") or "").strip() == target
    )


def _classin_preflight_blocking_issue_count(preflight: dict[str, Any]) -> int:
    issues = preflight.get("issues")
    if not isinstance(issues, list):
        return 0
    count = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_type = str(issue.get("type") or "").strip()
        if issue_type in CLASSIN_PREFLIGHT_BLOCKING_ISSUE_TYPES:
            count += 1
    return count


def classin_preflight_issue_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("classin_preflight_issue_count") or row.get("classinPreflightIssueCount")
    )
    if explicit:
        return explicit
    issue_types = row.get("classin_preflight_issue_types") or row.get("classinPreflightIssueTypes")
    if isinstance(issue_types, list):
        return len([issue_type for issue_type in issue_types if str(issue_type or "").strip()])
    return 0


def classin_preflight_blocking_issue_count(row: dict[str, Any]) -> int:
    explicit = _coerce_non_negative_int(
        row.get("classin_preflight_blocking_issue_count")
        or row.get("classinPreflightBlockingIssueCount")
    )
    if explicit:
        return explicit
    issue_types = row.get("classin_preflight_issue_types") or row.get("classinPreflightIssueTypes")
    if not isinstance(issue_types, list):
        return 0
    return len(
        [
            issue_type
            for issue_type in issue_types
            if str(issue_type or "").strip() in CLASSIN_PREFLIGHT_BLOCKING_ISSUE_TYPES
        ]
    )


def has_classin_preflight(row: dict[str, Any]) -> bool:
    return bool(
        row.get("classin_preflight_expected")
        or row.get("classinPreflightExpected")
        or classin_preflight_issue_count(row)
        or classin_preflight_blocking_issue_count(row)
        or row.get("classin_preflight_issue_types")
        or row.get("classinPreflightIssueTypes")
    )


def format_classin_preflight_label(row: dict[str, Any]) -> str:
    if not has_classin_preflight(row):
        return "-"
    issue_count = classin_preflight_issue_count(row)
    blocking_count = classin_preflight_blocking_issue_count(row)
    passed = _coerce_bool(row.get("classin_preflight_passed") or row.get("classinPreflightPassed"))
    if passed and not issue_count and not blocking_count:
        return "OK"

    state = "BLOCK" if blocking_count else "WARN" if issue_count else "OK"
    parts = [f"{state} {blocking_count}/{issue_count}" if issue_count or blocking_count else state]
    passage_reuse_count = _coerce_non_negative_int(row.get("passage_group_source_reuse_count"))
    if passage_reuse_count:
        parts.append(f"passage reuse {passage_reuse_count}")
    issue_types = [
        classin_preflight_issue_label(issue_type)
        for issue_type in (row.get("classin_preflight_issue_types") or row.get("classinPreflightIssueTypes") or [])
        if str(issue_type or "").strip()
    ]
    if issue_types:
        parts.append(", ".join(issue_types[:4]))
    return " · ".join(parts)


def format_passage_label(row: dict[str, Any]) -> str:
    parts: list[str] = []
    passage_group_count = _coerce_non_negative_int(row.get("passage_group_count"))
    if passage_group_count:
        parts.append(
            "g{groups}/q{questions}/x{cross_page}".format(
                groups=passage_group_count,
                questions=_coerce_non_negative_int(row.get("passage_problem_count")),
                cross_page=_coerce_non_negative_int(row.get("cross_page_passage_group_count")),
            )
        )
    passage_review_count = _coerce_non_negative_int(row.get("passage_review_item_count"))
    if passage_review_count:
        parts.append(
            "review {count}/x{cross_page}".format(
                count=passage_review_count,
                cross_page=_coerce_non_negative_int(row.get("cross_page_passage_review_item_count")),
            )
        )
    passage_reuse_count = _coerce_non_negative_int(row.get("passage_group_source_reuse_count"))
    if passage_reuse_count:
        parts.append(f"reuse {passage_reuse_count}")
    return " · ".join(parts) if parts else "-"


def _review_summary(session: dict[str, Any]) -> dict[str, Any]:
    for summary_key in ("reviewSummary", "review_summary"):
        summary = session.get(summary_key)
        if isinstance(summary, dict):
            return summary
    return {}


def hwp_cache_counts(session: dict[str, Any]) -> dict[str, int]:
    summary = _review_summary(session)
    cache_hit = _coerce_non_negative_int(summary.get("hwpCacheHitPageCount") or summary.get("hwp_cache_hit_page_count"))
    renderer_hit = _coerce_non_negative_int(
        summary.get("hwpRendererCacheHitCount") or summary.get("hwp_renderer_cache_hit_count")
    )
    normalized_hit = _coerce_non_negative_int(
        summary.get("hwpNormalizedCacheHitCount") or summary.get("hwp_normalized_cache_hit_count")
    )
    if cache_hit or renderer_hit or normalized_hit:
        return {
            "hwp_cache_hit_page_count": cache_hit,
            "hwp_renderer_cache_hit_count": renderer_hit,
            "hwp_normalized_cache_hit_count": normalized_hit,
        }

    page_cache_hit = 0
    page_renderer_hit = 0
    page_normalized_hit = 0
    pages = session.get("pages")
    if not isinstance(pages, list):
        return {
            "hwp_cache_hit_page_count": 0,
            "hwp_renderer_cache_hit_count": 0,
            "hwp_normalized_cache_hit_count": 0,
        }
    for page in pages:
        if not isinstance(page, dict):
            continue
        metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else page
        renderer_cache_hit = _coerce_bool(metadata.get("hwp_renderer_cache_hit"))
        normalized_cache_hit = _coerce_bool(metadata.get("hwp_normalized_cache_hit"))
        if renderer_cache_hit:
            page_renderer_hit += 1
        if normalized_cache_hit:
            page_normalized_hit += 1
        if renderer_cache_hit or normalized_cache_hit:
            page_cache_hit += 1
    return {
        "hwp_cache_hit_page_count": page_cache_hit,
        "hwp_renderer_cache_hit_count": page_renderer_hit,
        "hwp_normalized_cache_hit_count": page_normalized_hit,
    }


def collect_risk_flag_counts(session: dict[str, Any]) -> dict[str, int]:
    summary = _review_summary(session)
    for counts_key in ("riskFlagCounts", "risk_flag_counts"):
        counts = _coerce_count_map(summary.get(counts_key))
        if counts:
            return dict(sorted(counts.items()))

    counts: Counter[str] = Counter()
    for key in ("pages", "problems", "items"):
        values = session.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            for flag in value.get("riskFlags") or value.get("risk_flags") or []:
                flag_text = str(flag or "").strip()
                if flag_text:
                    counts[flag_text] += 1
    return dict(sorted(counts.items()))


def hwp_count_matches(value: dict[str, Any]) -> bool:
    return (
        _coerce_bool(value.get("hwp_text_problem_count_matches"))
        or _coerce_bool(value.get("hwpTextProblemCountMatches"))
        or _coerce_bool(value.get("hwp_layout_problem_count_matches"))
        or _coerce_bool(value.get("hwpLayoutProblemCountMatches"))
        or str(value.get("hwp_text_problem_count_status") or value.get("hwpTextProblemCountStatus") or "").lower() == "match"
        or str(value.get("hwp_layout_problem_count_status") or value.get("hwpLayoutProblemCountStatus") or "").lower() == "match"
    )


def actionable_risk_flag_counts(risk_counts: dict[str, int], *, hwp_counts_match: bool = False) -> dict[str, int]:
    excluded = set(NON_ACTIONABLE_RISK_FLAGS)
    if hwp_counts_match:
        excluded.update(HWP_COUNT_MATCH_DISMISSIBLE_RISK_FLAGS)
    return {
        flag: count
        for flag, count in sorted(_coerce_count_map(risk_counts).items())
        if flag not in excluded
    }


def is_supplemental_problem(problem: dict[str, Any]) -> bool:
    risk_flags = {str(flag) for flag in (problem.get("riskFlags") or problem.get("risk_flags") or [])}
    if "marker_document_continuation" in risk_flags:
        return True
    metadata = problem.get("metadata")
    if isinstance(metadata, dict) and metadata.get("marker_document_continuation"):
        return True
    problem_id = str(problem.get("id") or problem.get("problem_id") or "")
    return problem_id.endswith("-continuation")


def supplemental_problem_count(problems: Any) -> int:
    if not isinstance(problems, list):
        return 0
    return sum(1 for problem in problems if isinstance(problem, dict) and is_supplemental_problem(problem))


def review_status_counts(session: dict[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for key in ("pages", "problems", "items"):
        values = session.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            status = str(value.get("reviewStatus") or value.get("review_status") or "normal")
            counts[status] += 1
    return dict(sorted(counts.items()))


def summarize_export_response(
    payload: dict[str, Any],
    *,
    source_path: Path,
    subject: str,
    output_dir: Path,
    elapsed_s: float,
    expect_edb: bool = False,
) -> dict[str, Any]:
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    problems = session.get("items") or session.get("problems") or []
    pages = session.get("pages") or []
    problem_count = len(problems) if isinstance(problems, list) else 0
    page_count = len(pages) if isinstance(pages, list) else 0
    supplemental_count = supplemental_problem_count(problems)
    risk_flags = collect_risk_flags(session)
    risk_flag_counts = collect_risk_flag_counts(session)
    summary = _review_summary(session)
    cache_counts = hwp_cache_counts(session)
    cache_hit_page_count = int(cache_counts["hwp_cache_hit_page_count"])
    text_count_matches = _coerce_bool(summary.get("hwpTextProblemCountMatches") or summary.get("hwp_text_problem_count_matches"))
    layout_count_matches = _coerce_bool(summary.get("hwpLayoutProblemCountMatches") or summary.get("hwp_layout_problem_count_matches"))
    count_match = text_count_matches or layout_count_matches
    actionable_counts = actionable_risk_flag_counts(risk_flag_counts, hwp_counts_match=count_match)
    status_counts = review_status_counts(session)
    warnings = [
        *warning_messages(session.get("warnings")),
        *warning_messages(session.get("warning_messages")),
    ]
    mismatches = session.get("hwp_problem_count_mismatch_flags") or []
    hwp_mismatch_count = max(
        _coerce_non_negative_int(
            summary.get("hwpProblemCountMismatchCount") or summary.get("hwp_problem_count_mismatch_count")
        ),
        len(mismatches) if isinstance(mismatches, list) else 0,
        _risk_count(risk_flag_counts, HWP_PROBLEM_COUNT_MISMATCH_FLAG),
    )
    hwp_overseg_count = max(
        _coerce_non_negative_int(
            summary.get("hwpOversegmentationCount") or summary.get("hwp_oversegmentation_count")
        ),
        _risk_count(risk_flag_counts, HWP_OVERSEGMENTATION_FLAG),
    )
    source_overlap_groups = (
        session.get("sourceProblemOverlapGroups")
        or session.get("source_problem_overlap_groups")
        or []
    )
    source_overlap_group_count = max(
        _coerce_non_negative_int(
            session.get("sourceProblemOverlapGroupCount")
            or session.get("source_problem_overlap_group_count")
            or summary.get("sourceProblemOverlapGroupCount")
            or summary.get("source_problem_overlap_group_count")
        ),
        len(source_overlap_groups) if isinstance(source_overlap_groups, list) else 0,
    )
    source_overlap_problem_count = max(
        _coerce_non_negative_int(
            summary.get("sourceProblemBboxOverlapCount")
            or summary.get("source_problem_bbox_overlap_count")
        ),
        _risk_count(risk_flag_counts, SOURCE_PROBLEM_BBOX_OVERLAP_FLAG),
    )
    passage_metrics = _passage_metrics(payload, session, summary)
    passage_review_metrics = _passage_review_metrics(payload, session, summary)
    edb_validation = payload.get("edbValidation")
    if not isinstance(edb_validation, dict):
        edb_validation = payload.get("edb_validation")
    if not isinstance(edb_validation, dict):
        edb_validation = {}
    edb_path = str(payload.get("edbPath") or payload.get("edb_path") or "").strip()
    validation_flag = edb_validation.get("validated")
    edb_validated = bool(edb_validation) and (
        validation_flag is None or _coerce_bool(validation_flag)
    )
    edb_expected = bool(expect_edb or edb_path or edb_validation)
    classin_preflight = _classin_preflight(payload, session)
    classin_preflight_expected = bool(classin_preflight)
    classin_preflight_status = str(classin_preflight.get("status") or "").strip()
    classin_preflight_issues = classin_preflight.get("issues")
    classin_preflight_issue_types = _classin_preflight_issue_types(classin_preflight)
    passage_group_source_reuse_count = max(
        _classin_preflight_issue_type_count(
            classin_preflight,
            PASSAGE_GROUP_SOURCE_REUSE_FLAG,
        ),
        passage_group_source_reuse_group_count(session),
        passage_group_source_reuse_group_count(summary),
        passage_group_source_reuse_group_count(payload),
    )
    classin_preflight_issue_count = max(
        _coerce_non_negative_int(
            classin_preflight.get("issueCount") or classin_preflight.get("issue_count")
        ),
        len(classin_preflight_issues) if isinstance(classin_preflight_issues, list) else 0,
    )
    classin_preflight_blocking_issue_count = _classin_preflight_blocking_issue_count(classin_preflight)
    if classin_preflight_expected:
        passed_value = classin_preflight.get("passed")
        classin_preflight_passed = (
            _coerce_bool(passed_value)
            if passed_value is not None
            else classin_preflight_issue_count == 0 and classin_preflight_status not in {"failed", "blocked", "needs_attention"}
        )
    else:
        classin_preflight_passed = False
    failed_count = int(status_counts.get("failed") or 0)
    needs_review = bool(
        warnings
        or mismatches
        or hwp_mismatch_count
        or hwp_overseg_count
        or source_overlap_group_count
        or source_overlap_problem_count
        or passage_group_source_reuse_count
        or passage_review_metrics["passage_review_item_count"]
        or actionable_counts
        or (edb_expected and not edb_validated)
        or (classin_preflight_expected and not classin_preflight_passed)
        or classin_preflight_issue_count
        or failed_count
    )
    return {
        "file": normalized_text(source_path.name),
        "path": str(source_path),
        "subject": subject,
        "ok": bool(payload.get("ok")),
        "problem_count": problem_count,
        "core_problem_count": max(0, problem_count - supplemental_count),
        "supplemental_item_count": supplemental_count,
        "detected_problem_count": session.get("detected_problem_count"),
        "pages": page_count,
        "warnings": warnings,
        "hwp_problem_count_mismatch_flags": list(mismatches) if isinstance(mismatches, list) else [],
        "hwp_problem_count_mismatch_count": hwp_mismatch_count,
        "hwp_oversegmentation_count": hwp_overseg_count,
        "source_problem_bbox_overlap_count": source_overlap_problem_count,
        "source_problem_overlap_group_count": source_overlap_group_count,
        **passage_metrics,
        **passage_review_metrics,
        "risk_flags": risk_flags,
        "risk_flag_counts": risk_flag_counts,
        "actionable_risk_flag_counts": actionable_counts,
        "hwp_text_problem_count_matches": text_count_matches,
        "hwp_layout_problem_count_matches": layout_count_matches,
        **cache_counts,
        "hwp_cache_hit_rate": round(cache_hit_page_count / page_count, 3) if page_count else 0.0,
        "review_status_counts": status_counts,
        "needs_review": needs_review,
        "edb_expected": edb_expected,
        "edb_validated": edb_validated,
        "edb_path": edb_path,
        "edb_record_count_actual": int(edb_validation.get("recordCountActual") or 0),
        "edb_record_count_hint": int(edb_validation.get("recordCountHint") or 0),
        "edb_page_count_hint": int(edb_validation.get("pageCountHint") or 0),
        "classin_preflight_expected": classin_preflight_expected,
        "classin_preflight_passed": classin_preflight_passed,
        "classin_preflight_status": classin_preflight_status,
        "classin_preflight_issue_count": classin_preflight_issue_count,
        "classin_preflight_blocking_issue_count": classin_preflight_blocking_issue_count,
        "passage_group_source_reuse_count": passage_group_source_reuse_count,
        "classin_preflight_issue_types": classin_preflight_issue_types,
        "elapsed_s": round(elapsed_s, 2),
        "output_dir": str(output_dir),
    }


def summarize_batch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    risk_counts: Counter[str] = Counter()
    actionable_counts: Counter[str] = Counter()
    warning_count = 0
    mismatch_count = 0
    oversegmentation_count = 0
    source_overlap_problem_count = 0
    source_overlap_group_count = 0
    hwp_cache_hit_page_count = 0
    hwp_renderer_cache_hit_count = 0
    hwp_normalized_cache_hit_count = 0
    passage_group_count = 0
    passage_problem_count = 0
    passage_fragment_count = 0
    cross_page_passage_group_count = 0
    passage_review_item_count = 0
    cross_page_passage_review_item_count = 0
    passage_review_reason_counts: Counter[str] = Counter()
    passage_group_source_reuse_count = 0
    classin_preflight_issue_types: Counter[str] = Counter()
    for row in rows:
        row_risk_counts = _coerce_count_map(row.get("risk_flag_counts"))
        if row_risk_counts:
            risk_counts.update(row_risk_counts)
        else:
            for flag in row.get("risk_flags") or []:
                risk_counts[str(flag)] += 1
        row_actionable_counts = _coerce_count_map(row.get("actionable_risk_flag_counts"))
        source_counts = row_actionable_counts or row_risk_counts or dict.fromkeys(row.get("risk_flags") or [], 1)
        actionable_counts.update(actionable_risk_flag_counts(source_counts, hwp_counts_match=hwp_count_matches(row)))
        warning_count += len(row.get("warnings") or [])
        mismatch_count += hwp_problem_count_mismatch_count(row)
        oversegmentation_count += hwp_oversegmentation_count(row)
        source_overlap_problem_count += source_problem_bbox_overlap_count(row)
        source_overlap_group_count += source_problem_overlap_group_count(row)
        hwp_cache_hit_page_count += _coerce_non_negative_int(row.get("hwp_cache_hit_page_count"))
        hwp_renderer_cache_hit_count += _coerce_non_negative_int(row.get("hwp_renderer_cache_hit_count"))
        hwp_normalized_cache_hit_count += _coerce_non_negative_int(row.get("hwp_normalized_cache_hit_count"))
        passage_group_count += _coerce_non_negative_int(row.get("passage_group_count"))
        passage_problem_count += _coerce_non_negative_int(row.get("passage_problem_count"))
        passage_fragment_count += _coerce_non_negative_int(row.get("passage_fragment_count"))
        cross_page_passage_group_count += _coerce_non_negative_int(row.get("cross_page_passage_group_count"))
        passage_review_item_count += _coerce_non_negative_int(row.get("passage_review_item_count"))
        cross_page_passage_review_item_count += _coerce_non_negative_int(
            row.get("cross_page_passage_review_item_count")
        )
        passage_review_reason_counts.update(_coerce_count_map(row.get("passage_review_reason_counts")))
        passage_group_source_reuse_count += _coerce_non_negative_int(row.get("passage_group_source_reuse_count"))
        for issue_type in row.get("classin_preflight_issue_types") or []:
            issue_type_text = str(issue_type or "").strip()
            if issue_type_text:
                classin_preflight_issue_types[issue_type_text] += 1
    page_count = sum(int(row.get("pages") or 0) for row in rows)
    return {
        "sample_count": len(rows),
        "ok_count": sum(1 for row in rows if row.get("ok")),
        "failed_count": sum(1 for row in rows if not row.get("ok")),
        "needs_review_count": sum(1 for row in rows if row.get("needs_review")),
        "problem_count": sum(int(row.get("problem_count") or 0) for row in rows),
        "core_problem_count": sum(int(row.get("core_problem_count") or 0) for row in rows),
        "supplemental_item_count": sum(int(row.get("supplemental_item_count") or 0) for row in rows),
        "page_count": page_count,
        "hwp_cache_hit_page_count": hwp_cache_hit_page_count,
        "hwp_renderer_cache_hit_count": hwp_renderer_cache_hit_count,
        "hwp_normalized_cache_hit_count": hwp_normalized_cache_hit_count,
        "hwp_cache_hit_rate": round(hwp_cache_hit_page_count / page_count, 3) if page_count else 0.0,
        "elapsed_s": round(sum(float(row.get("elapsed_s") or 0) for row in rows), 2),
        "warning_count": warning_count,
        "hwp_problem_count_mismatch_count": mismatch_count,
        "hwp_oversegmentation_count": oversegmentation_count,
        "source_problem_bbox_overlap_count": source_overlap_problem_count,
        "source_problem_overlap_group_count": source_overlap_group_count,
        "passage_group_count": passage_group_count,
        "passage_problem_count": passage_problem_count,
        "passage_fragment_count": passage_fragment_count,
        "cross_page_passage_group_count": cross_page_passage_group_count,
        "passage_review_item_count": passage_review_item_count,
        "cross_page_passage_review_item_count": cross_page_passage_review_item_count,
        "passage_group_source_reuse_count": passage_group_source_reuse_count,
        "edb_expected_count": sum(1 for row in rows if row.get("edb_expected")),
        "edb_validated_count": sum(1 for row in rows if row.get("edb_expected") and row.get("edb_validated")),
        "edb_missing_count": sum(1 for row in rows if row.get("edb_expected") and not row.get("edb_validated")),
        "classin_preflight_expected_count": sum(1 for row in rows if has_classin_preflight(row)),
        "classin_preflight_passed_count": sum(
            1
            for row in rows
            if has_classin_preflight(row) and row.get("classin_preflight_passed")
        ),
        "classin_preflight_issue_count": sum(classin_preflight_issue_count(row) for row in rows),
        "classin_preflight_blocking_issue_count": sum(classin_preflight_blocking_issue_count(row) for row in rows),
        "top_risk_flags": [
            {"flag": flag, "count": count}
            for flag, count in sorted(risk_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
        "top_actionable_risk_flags": [
            {"flag": flag, "count": count}
            for flag, count in sorted(actionable_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
        "top_classin_preflight_issue_types": [
            {"type": issue_type, "count": count}
            for issue_type, count in sorted(classin_preflight_issue_types.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
        "top_passage_review_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(passage_review_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
    }


def post_export(
    *,
    app_url: str,
    source_path: Path,
    output_dir: Path,
    subject: str,
    timeout_seconds: int,
    export_edb: bool = False,
) -> dict[str, Any]:
    payload = {
        "files": [str(source_path)],
        "inputIntent": "exam",
        "ocr": "none",
        "exportEdb": bool(export_edb),
        "preview": True,
        "subject": subject,
        "outputDir": str(output_dir),
    }
    request = urllib.request.Request(
        f"{app_url.rstrip('/')}/api/export",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(body)
        except json.JSONDecodeError:
            error_payload = {"error": body}
        error_payload.setdefault("ok", False)
        error_payload["http_status"] = exc.code
        return error_payload


def run_batch(
    *,
    source_dir: Path,
    output_dir: Path,
    app_url: str = DEFAULT_APP_URL,
    timeout_seconds: int = 240,
    export_edb: bool = False,
    include: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, source_path in enumerate(iter_hangul_files(source_dir, include=include, limit=limit), 1):
        subject = infer_subject(source_path)
        sample_output_dir = output_dir / f"{index:02d}_{source_path.stem}"
        started = time.time()
        payload = post_export(
            app_url=app_url,
            source_path=source_path,
            output_dir=sample_output_dir,
            subject=subject,
            timeout_seconds=timeout_seconds,
            export_edb=export_edb,
        )
        if payload.get("ok"):
            row = summarize_export_response(
                payload,
                source_path=source_path,
                subject=subject,
                output_dir=sample_output_dir,
                elapsed_s=time.time() - started,
                expect_edb=export_edb,
            )
        else:
            row = {
                "file": normalized_text(source_path.name),
                "path": str(source_path),
                "subject": subject,
                "ok": False,
                "error": payload,
                "needs_review": True,
                "edb_expected": bool(export_edb),
                "edb_validated": False,
                "elapsed_s": round(time.time() - started, 2),
                "output_dir": str(sample_output_dir),
            }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    batch_summary = summarize_batch(rows)
    (output_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "batch_summary.json").write_text(
        json.dumps(batch_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        format_markdown_report(rows, batch_summary),
        encoding="utf-8",
    )
    return rows


def format_markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| file | ok | problems | pages | cache | review | passage | risk | preflight | edb | elapsed |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in rows:
        review = ", ".join(f"{k}:{v}" for k, v in (row.get("review_status_counts") or {}).items()) or "-"
        risk_counts = _coerce_count_map(row.get("risk_flag_counts"))
        if risk_counts:
            risk = ", ".join(
                f"{flag}:{count}"
                for flag, count in sorted(risk_counts.items(), key=lambda item: (-item[1], item[0]))
            )
        else:
            risk = ", ".join(row.get("risk_flags") or row.get("warnings") or []) or "-"
        problem_label = row.get("problem_count", "-")
        supplemental = int(row.get("supplemental_item_count") or 0)
        if supplemental:
            problem_label = f"{row.get('core_problem_count', '-')}+{supplemental}"
        pages = _coerce_non_negative_int(row.get("pages"))
        cache_hit = _coerce_non_negative_int(row.get("hwp_cache_hit_page_count"))
        renderer_hit = _coerce_non_negative_int(row.get("hwp_renderer_cache_hit_count"))
        normalized_hit = _coerce_non_negative_int(row.get("hwp_normalized_cache_hit_count"))
        cache_label = "-"
        if pages or cache_hit:
            cache_label = f"{cache_hit}/{pages}"
            if renderer_hit or normalized_hit:
                cache_label += f" · r{renderer_hit}/n{normalized_hit}"
        if row.get("edb_expected"):
            if row.get("edb_validated"):
                actual = int(row.get("edb_record_count_actual") or 0)
                hint = int(row.get("edb_record_count_hint") or 0)
                edb_label = f"OK {actual}/{hint}" if hint else f"OK {actual}"
            else:
                edb_label = "MISS"
        else:
            edb_label = "-"
        preflight_label = format_classin_preflight_label(row)
        passage_label = format_passage_label(row)
        lines.append(
            "| {file} | {ok} | {problems} | {pages} | {cache} | {review} | {passage} | {risk} | {preflight} | {edb} | {elapsed} |".format(
                file=str(row.get("file") or "").replace("|", "\\|"),
                ok="OK" if row.get("ok") else "FAIL",
                problems=problem_label,
                pages=row.get("pages", "-"),
                cache=cache_label,
                review=review.replace("|", "\\|"),
                passage=passage_label.replace("|", "\\|"),
                risk=risk.replace("|", "\\|"),
                preflight=preflight_label.replace("|", "\\|"),
                edb=edb_label,
                elapsed=row.get("elapsed_s", "-"),
            )
        )
    return "\n".join(lines)


def format_markdown_report(rows: list[dict[str, Any]], summary: dict[str, Any] | None = None) -> str:
    resolved_summary = summary if summary is not None else summarize_batch(rows)
    sections = [
        "# HWP Batch Verification",
        "",
        format_batch_summary(resolved_summary),
        "",
        format_markdown_table(rows),
        "",
    ]
    artifact_lines = format_artifact_lines(rows)
    if artifact_lines:
        sections.extend(["## Artifacts", "", *artifact_lines, ""])
    return "\n".join(sections)


def format_artifact_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        output_dir = str(row.get("output_dir") or "").strip()
        edb_path = str(row.get("edb_path") or "").strip()
        if not output_dir and not edb_path:
            continue
        file_name = str(row.get("file") or row.get("path") or "sample").strip()
        parts = []
        if output_dir:
            parts.append(f"Output: `{output_dir}`")
        if edb_path:
            parts.append(f"EDB: `{edb_path}`")
        lines.append(f"- **{file_name}** - " + " · ".join(parts))
    return lines


def format_batch_summary(summary: dict[str, Any]) -> str:
    supplemental = int(summary.get("supplemental_item_count") or 0)
    core = int(summary.get("core_problem_count") or 0)
    problem_label = f"{core}+{supplemental}" if supplemental else str(summary.get("problem_count") or core)
    edb_expected = int(summary.get("edb_expected_count") or 0)
    edb_part = (
        f"edb {summary.get('edb_validated_count', 0)}/{edb_expected} · "
        if edb_expected
        else ""
    )
    classin_preflight_expected = int(summary.get("classin_preflight_expected_count") or 0)
    classin_preflight_part = ""
    if classin_preflight_expected:
        preflight_issue_label = ", ".join(
            f"{classin_preflight_issue_label(item.get('type'))}:{item.get('count')}"
            for item in (summary.get("top_classin_preflight_issue_types") or [])[:4]
            if item.get("type")
        ) or "-"
        classin_preflight_part = (
            f"preflight {summary.get('classin_preflight_passed_count', 0)}/{classin_preflight_expected} · "
            f"blocking {summary.get('classin_preflight_blocking_issue_count', 0)} · "
            f"preflight issues {preflight_issue_label} · "
        )
    top_risk = ", ".join(
        f"{item.get('flag')}:{item.get('count')}"
        for item in (summary.get("top_risk_flags") or [])[:4]
        if item.get("flag")
    ) or "-"
    top_actionable_risk = ", ".join(
        f"{item.get('flag')}:{item.get('count')}"
        for item in (summary.get("top_actionable_risk_flags") or [])[:4]
        if item.get("flag")
    ) or "-"
    page_count = _coerce_non_negative_int(summary.get("page_count"))
    hwp_cache_hit_page_count = _coerce_non_negative_int(summary.get("hwp_cache_hit_page_count"))
    source_overlap_problem_count = _coerce_non_negative_int(summary.get("source_problem_bbox_overlap_count"))
    source_overlap_group_count = _coerce_non_negative_int(summary.get("source_problem_overlap_group_count"))
    passage_group_source_reuse_count = _coerce_non_negative_int(summary.get("passage_group_source_reuse_count"))
    passage_group_count = _coerce_non_negative_int(summary.get("passage_group_count"))
    passage_review_item_count = _coerce_non_negative_int(summary.get("passage_review_item_count"))
    passage_part = ""
    if passage_group_count or passage_group_source_reuse_count or passage_review_item_count:
        passage_part = (
            f"passage groups {passage_group_count} · "
            f"passage questions {_coerce_non_negative_int(summary.get('passage_problem_count'))} · "
            f"fragments {_coerce_non_negative_int(summary.get('passage_fragment_count'))} · "
            f"cross-page {_coerce_non_negative_int(summary.get('cross_page_passage_group_count'))} · "
            f"review {passage_review_item_count} · "
            f"review cross-page {_coerce_non_negative_int(summary.get('cross_page_passage_review_item_count'))} · "
            f"passage reuse {passage_group_source_reuse_count} · "
        )
    return (
        "Batch summary: "
        f"samples {summary.get('ok_count', 0)}/{summary.get('sample_count', 0)} OK · "
        f"failed {summary.get('failed_count', 0)} · "
        f"review {summary.get('needs_review_count', 0)} · "
        f"problems {problem_label} · "
        f"pages {summary.get('page_count', 0)} · "
        f"cache {hwp_cache_hit_page_count}/{page_count} · "
        f"warnings {summary.get('warning_count', 0)} · "
        f"mismatch {summary.get('hwp_problem_count_mismatch_count', 0)} · "
        f"overseg {summary.get('hwp_oversegmentation_count', 0)} · "
        f"source overlap {source_overlap_problem_count}/{source_overlap_group_count} · "
        f"{passage_part}"
        f"{edb_part}"
        f"{classin_preflight_part}"
        f"elapsed {summary.get('elapsed_s', 0)}s · "
        f"top risk {top_risk} · "
        f"actionable {top_actionable_risk}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify local HWP/HWPX samples through the running EDB app API.")
    parser.add_argument("source_dir", type=Path, help="Folder containing .hwp/.hwpx samples")
    parser.add_argument("--output-dir", type=Path, default=Path(".app_runtime/hwp_batch_verify"), help="Folder for per-sample export outputs")
    parser.add_argument("--app-url", default=DEFAULT_APP_URL, help="Running app URL")
    parser.add_argument("--timeout-seconds", type=int, default=240, help="Per-file HTTP timeout")
    parser.add_argument("--export-edb", action="store_true", help="Also generate and validate an EDB file for each sample")
    parser.add_argument("--include", action="append", default=[], help="Only run files whose name contains this text. Repeat to require multiple terms.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of matched samples to run")
    parser.add_argument("--fail-on-review", action="store_true", help="Exit non-zero when any sample has warnings or review risk")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = run_batch(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        app_url=args.app_url,
        timeout_seconds=args.timeout_seconds,
        export_edb=args.export_edb,
        include=args.include,
        limit=args.limit,
    )
    print(format_markdown_table(rows))
    print(format_batch_summary(summarize_batch(rows)))
    if any(not row.get("ok") for row in rows):
        return 1
    if any(row.get("edb_expected") and not row.get("edb_validated") for row in rows):
        return 3
    if any(classin_preflight_blocking_issue_count(row) for row in rows):
        return 4
    if args.fail_on_review and any(row.get("needs_review") for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
