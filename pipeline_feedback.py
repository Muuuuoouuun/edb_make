#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from structured_schema import PageModel


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _increment(counter: dict[str, int], key: str | None) -> None:
    if not key:
        return
    counter[key] = counter.get(key, 0) + 1


def build_parse_feedback(
    pages: list[PageModel],
    *,
    source_count: int,
) -> dict[str, Any]:
    issue_counts: dict[str, int] = {}
    route_tier_counts: dict[str, int] = {}
    ocr_error_count = 0
    ocr_error_page_count = 0
    segmentation_fallback_count = 0
    problem_count = sum(len(page.problems) for page in pages)

    for page in pages:
        page_has_ocr_error = False
        route_decision = _mapping(page.metadata.get("route_decision"))
        profile = _mapping(route_decision.get("profile"))
        tier = str(profile.get("tier") or route_decision.get("route_tier") or "unknown")
        _increment(route_tier_counts, tier)

        reasons = profile.get("reasons")
        if not isinstance(reasons, list):
            reasons = route_decision.get("trigger_reasons")
        if isinstance(reasons, list):
            for reason in reasons:
                _increment(issue_counts, str(reason))

        grouping = _mapping(page.metadata.get("grouping_diagnostics"))
        if grouping.get("fallback_grouping"):
            _increment(issue_counts, "fallback_grouping")

        if page.metadata.get("segmentation_fallback"):
            segmentation_fallback_count += 1
            _increment(issue_counts, "segmentation_error")

        for block in page.blocks:
            if block.metadata.get("ocr_failed"):
                ocr_error_count += 1
                page_has_ocr_error = True

        if page_has_ocr_error:
            ocr_error_page_count += 1

    warning_messages: list[str] = []
    if segmentation_fallback_count:
        warning_messages.append(
            f"세그먼트가 {segmentation_fallback_count}개 페이지에서 실패해 전체 이미지를 1개 문항 후보로 대체했습니다."
        )
    if any(issue_counts.get(key, 0) > 0 for key in ("sparse_segmentation", "large_block_dominance", "full_page_image")):
        warning_messages.append("문항이 한 덩어리로 인식된 페이지가 있어 감지 문항 수가 실제보다 적을 수 있습니다.")
    if any(issue_counts.get(key, 0) > 0 for key in ("fallback_grouping", "no_problem_markers", "no_problem_or_choice_markers")):
        warning_messages.append("문제 번호나 선택지 표지가 약해 임시 규칙으로 문항을 묶은 페이지가 있습니다.")
    if ocr_error_count:
        warning_messages.append(f"OCR이 {ocr_error_count}개 블록에서 실패해 해당 영역을 이미지 fallback으로 유지했습니다.")
    if any(issue_counts.get(key, 0) > 0 for key in ("textless_blocks", "low_ocr_confidence", "low_avg_confidence")):
        warning_messages.append("문자 인식 신뢰도가 낮아 일부 블록은 text 대신 image 위주로 유지됩니다.")
    if len(pages) == 1 and source_count == 1 and problem_count <= 1 and any(
        issue_counts.get(key, 0) > 0
        for key in ("segmentation_error", "sparse_segmentation", "large_block_dominance", "full_page_image")
    ):
        warning_messages.append("단일 이미지에서 문항이 1개만 감지되었습니다. 촬영 구도나 문제 경계가 약한 이미지일 수 있습니다.")

    return {
        "warning_messages": warning_messages,
        "parse_diagnostics": {
            "page_count": len(pages),
            "problem_count": problem_count,
            "source_count": source_count,
            "ocr_error_count": ocr_error_count,
            "ocr_error_page_count": ocr_error_page_count,
            "segmentation_fallback_count": segmentation_fallback_count,
            "route_tier_counts": route_tier_counts,
            "issue_counts": issue_counts,
        },
    }


def format_pipeline_error(exc: Exception) -> dict[str, str]:
    raw_message = str(exc).strip() or exc.__class__.__name__
    lowered = raw_message.lower()
    stage = "파싱"
    code = "parse_failed"
    hint = "입력 이미지와 설정을 다시 확인한 뒤 재시도해주세요."

    if isinstance(exc, FileNotFoundError) or "sourcepath does not exist" in lowered or "no such file" in lowered:
        stage = "입력"
        code = "source_not_found"
        hint = "업로드한 파일이나 sourcePath 경로를 다시 확인해주세요."
    elif "unsupported input type" in lowered:
        stage = "입력"
        code = "unsupported_input_type"
        hint = "지원 형식은 PDF, PNG, JPG, JPEG, WEBP, BMP, TIF, TIFF입니다."
    elif "cannot identify image file" in lowered or "truncated" in lowered or "decompressionbomb" in lowered:
        stage = "이미지 열기"
        code = "image_decode_failed"
        hint = "손상된 이미지이거나 확장자와 실제 파일 형식이 다를 수 있습니다."
    elif "opencv-python" in lowered or "numpy is required" in lowered or "pymupdf is required" in lowered:
        stage = "환경"
        code = "dependency_missing"
        hint = "전처리 또는 PDF 렌더에 필요한 패키지가 설치되지 않았습니다."
    elif "ocr" in lowered or "openai" in lowered or "anthropic" in lowered or "gemini" in lowered:
        stage = "문자 인식"
        code = "ocr_failed"
        hint = "OCR 모드를 바꾸거나 AI 보정을 끄고 다시 시도해보세요."
    elif "edb validation failed" in lowered or "header_flag" in lowered or "record_count_hint" in lowered:
        stage = "EDB 패키징"
        code = "edb_validation_failed"
        hint = "내보낸 칠판 파일 구조를 다시 점검했습니다. 미리보기 크기나 record 구성이 불안정한 경우입니다."
    elif "segment" in lowered or "candidate" in lowered or "grouping" in lowered:
        stage = "문항 분리"
        code = "segmentation_failed"
        hint = "촬영 구도, 기울어짐, 여백 상태를 다시 확인해주세요."

    message = f"{stage} 단계에서 실패했습니다: {raw_message}"
    if hint:
        message = f"{message} ({hint})"
    return {
        "code": code,
        "stage": stage,
        "message": message,
        "hint": hint,
        "raw_message": raw_message,
    }
