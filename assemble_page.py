#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import replace

from structured_schema import BlockType, ContentBlock, PageModel, ProblemUnit, Subject


# Matches problem number prefixes used in Korean exams:
#   "1.", "2)", "문항 1.", "문제 3.", "문항1)", "[1]", "[2]"
PROBLEM_MARKER_RE = re.compile(
    r"^\s*"
    r"(?:(?:문항|문제)\s*)?"
    r"(?:\[(?P<number_bracket>[1-9][0-9]{0,2})\]"
    r"|(?P<number>[1-9][0-9]{0,2})(?:[\.\)]))"
    r"(?:\s+|$)"
)
# Matches answer-choice prefixes: ①②③④⑤, (1)…(5), 1)…5), A)…H), ㄱ)…ㄷ) etc.
CHOICE_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"[\u2460-\u2469]|"          # ①–⑨ circled numbers
    r"\([1-9][0-9]?\)|"          # (1) (2) …
    r"[1-9][0-9]?\)|"            # 1) 2) …
    r"[A-Ha-h][\.\)]|"           # A) B) …
    r"[\u3131-\u314e][\.\)]"     # ㄱ) ㄴ) ㄷ) …
    r")\s*"
)


def _matches_problem_marker(text: str | None) -> bool:
    return bool(text and PROBLEM_MARKER_RE.match(text))


def _matches_choice_marker(text: str | None) -> bool:
    return bool(text and CHOICE_MARKER_RE.match(text))


def extract_problem_number(text: str | None) -> int | None:
    if not text:
        return None
    match = PROBLEM_MARKER_RE.match(text)
    if not match:
        return None
    try:
        raw = match.group("number") or match.group("number_bracket")
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def strip_problem_marker(text: str | None) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    match = PROBLEM_MARKER_RE.match(stripped)
    if not match:
        return stripped
    cleaned = stripped[match.end():].strip()
    return cleaned or None


def _problem_title_source(block: ContentBlock) -> str | None:
    display_title = block.metadata.get("display_title")
    if isinstance(display_title, str) and display_title.strip():
        return display_title.strip()
    if block.text and block.text.strip():
        return block.text.strip()
    return None


def _problem_display_title(block: ContentBlock) -> str | None:
    title_source = _problem_title_source(block)
    return strip_problem_marker(title_source) or title_source


def _block_grouping_diagnostics(block: ContentBlock) -> dict[str, object]:
    raw_text = block.text.strip() if block.text and block.text.strip() else None
    title_source = _problem_title_source(block)
    problem_marker = _matches_problem_marker(raw_text)
    choice_marker = _matches_choice_marker(raw_text)
    problem_number, problem_number_source = extract_problem_number_from_block(block)
    display_title_marker = bool(title_source and title_source != raw_text and _matches_problem_marker(title_source))
    marker_conflict = bool(problem_marker and choice_marker)
    if problem_number is not None and choice_marker:
        marker_conflict = True

    return {
        "raw_text_marker_source": raw_text,
        "display_title_marker_source": title_source if title_source != raw_text else None,
        "problem_marker": problem_marker or display_title_marker,
        "choice_marker": choice_marker,
        "problem_number": problem_number,
        "problem_number_source": problem_number_source,
        "force_problem_start": bool(block.metadata.get("force_problem_start")),
        "marker_conflict": marker_conflict,
        "fallback_reason": block.metadata.get("fallback_reason"),
        "display_title_present": bool(block.metadata.get("display_title")),
    }


def _extract_top_left_problem_number(block: ContentBlock) -> tuple[int | None, str | None]:
    if not block.ocr_lines:
        return None, None

    block_width = max(block.bbox.width, 1.0)
    block_height = max(block.bbox.height, 1.0)
    top_left_candidates: list[tuple[float, int]] = []
    fallback_candidates: list[tuple[float, int]] = []

    for index, line in enumerate(block.ocr_lines):
        number = extract_problem_number(line.text)
        if number is None:
            continue

        top_ratio = max(0.0, line.bbox.top / block_height)
        left_ratio = max(0.0, line.bbox.left / block_width)
        score = top_ratio * 2.4 + left_ratio + index * 0.05
        in_top_left_zone = top_ratio <= 0.32 and left_ratio <= 0.18

        if in_top_left_zone:
            top_left_candidates.append((score, number))
        else:
            fallback_candidates.append((score, number))

    if top_left_candidates:
        _, number = min(top_left_candidates, key=lambda item: item[0])
        return number, "ocr_top_left"
    if fallback_candidates:
        _, number = min(fallback_candidates, key=lambda item: item[0])
        return number, "ocr_line"
    return None, None


def extract_problem_number_from_block(block: ContentBlock) -> tuple[int | None, str | None]:
    top_left_number, source = _extract_top_left_problem_number(block)
    if top_left_number is not None:
        return top_left_number, source

    text_number = extract_problem_number(_problem_title_source(block))
    if text_number is not None:
        return text_number, "text_prefix"
    return None, None


def infer_subject(page: PageModel) -> Subject:
    if page.subject != Subject.UNKNOWN:
        return page.subject
    text = "\n".join(block.text or "" for block in page.blocks)
    lowered = text.lower()
    if any(token in lowered for token in ("lim", "sin", "cos", "tan", "\ud655\ub960", "\ud568\uc218", "\ubbf8\ubd84", "\uc801\ubd84")):
        return Subject.MATH
    if any(
        token in lowered
        for token in ("\uc2e4\ud5d8", "\ubd84\uc790", "\uc6d0\uc790", "\uc804\ub958", "\uac00\uc18d\ub3c4", "\uad11\ud569\uc131")
    ):
        return Subject.SCIENCE
    if any(
        token in lowered
        for token in ("\ub2e4\uc74c \uae00", "\ubcf4\uae30", "\ubb38\ub2e8", "\uc791\uac00", "\ud654\uc790", "\ubc11\uc904")
    ):
        return Subject.KOREAN
    return page.subject


def sort_blocks_for_reading_order(blocks: list[ContentBlock]) -> list[ContentBlock]:
    if blocks and all(block.metadata.get("column_index") is not None for block in blocks):
        return sorted(
            blocks,
            key=lambda block: (
                int(block.metadata.get("column_index", 0)),
                int(block.metadata.get("question_band_index", block.reading_order)),
                round(block.bbox.top, 4),
                round(block.bbox.left, 4),
            ),
        )
    return sorted(blocks, key=lambda block: (round(block.bbox.top, 4), round(block.bbox.left, 4), block.reading_order))


def relabel_reading_order(page: PageModel) -> PageModel:
    sorted_blocks = sort_blocks_for_reading_order(page.blocks)
    rewritten = [replace(block, reading_order=index) for index, block in enumerate(sorted_blocks)]
    return replace(page, blocks=rewritten)


def detect_problem_start(block: ContentBlock) -> bool:
    if block.metadata.get("force_problem_start"):
        return True
    if block.block_type == BlockType.CHOICE:
        return False
    problem_number, _ = extract_problem_number_from_block(block)
    if problem_number is not None:
        return True
    if block.block_type in {BlockType.TITLE, BlockType.SECTION}:
        if not (block.text and block.text.strip()) and not block.metadata.get("display_title"):
            return False
        return True
    marker_source = _problem_title_source(block)
    if not marker_source:
        return False
    if _matches_choice_marker(marker_source):
        return False
    if _matches_problem_marker(marker_source):
        return True
    return False


def detect_choice_block(block: ContentBlock) -> bool:
    if block.metadata.get("force_problem_start"):
        return False
    problem_number, _ = extract_problem_number_from_block(block)
    if problem_number is not None:
        return False
    if block.block_type == BlockType.CHOICE:
        return True
    if not block.text:
        return False
    return _matches_choice_marker(block.text)


def classify_block(block: ContentBlock) -> ContentBlock:
    if not block.text:
        return block
    if detect_choice_block(block):
        return replace(block, block_type=BlockType.CHOICE)
    if detect_problem_start(block):
        return replace(block, block_type=BlockType.TITLE)
    return block


def _build_grouping_diagnostics(
    *,
    page: PageModel,
    classified_blocks: list[ContentBlock],
    block_diagnostics: list[dict[str, object]],
    has_text_markers: bool,
    has_band_metadata: bool,
    fallback_grouping: bool,
) -> dict[str, object]:
    problem_marker_block_count = sum(1 for item in block_diagnostics if bool(item.get("problem_marker")))
    choice_marker_block_count = sum(1 for item in block_diagnostics if bool(item.get("choice_marker")))
    marker_conflict_block_count = sum(1 for item in block_diagnostics if bool(item.get("marker_conflict")))
    forced_problem_start_count = sum(1 for item in block_diagnostics if bool(item.get("force_problem_start")))
    problem_number_block_count = sum(1 for item in block_diagnostics if item.get("problem_number") is not None)
    fallback_reason_block_count = sum(1 for item in block_diagnostics if item.get("fallback_reason"))
    problem_number_source_counts: dict[str, int] = {}
    for item in block_diagnostics:
        source = item.get("problem_number_source")
        if not source:
            continue
        source_key = str(source)
        problem_number_source_counts[source_key] = problem_number_source_counts.get(source_key, 0) + 1

    grouping_mode = "fallback" if fallback_grouping else "marker"
    grouping_source = "fallback_grouping" if fallback_grouping else "marker_grouping"
    trigger_reasons: list[str] = []
    if fallback_grouping:
        if not has_text_markers:
            trigger_reasons.append("no_text_markers")
        if has_band_metadata:
            trigger_reasons.append("band_metadata_present")
        if len(classified_blocks) > 1:
            trigger_reasons.append("multi_block_page")
    else:
        trigger_reasons.append("text_markers_detected")

    return {
        "grouping_mode": grouping_mode,
        "grouping_source": grouping_source,
        "trigger_reasons": trigger_reasons,
        "has_text_markers": has_text_markers,
        "has_band_metadata": has_band_metadata,
        "block_count": len(classified_blocks),
        "problem_count": 0,
        "fallback_grouping": fallback_grouping,
        "fallback_grouping_stats": {
            "used": fallback_grouping,
            "trigger_reasons": trigger_reasons,
            "block_count": len(classified_blocks),
            "problem_marker_block_count": problem_marker_block_count,
            "choice_marker_block_count": choice_marker_block_count,
            "marker_conflict_block_count": marker_conflict_block_count,
            "fallback_reason_block_count": fallback_reason_block_count,
        },
        "marker_counts": {
            "problem_marker_block_count": problem_marker_block_count,
            "choice_marker_block_count": choice_marker_block_count,
            "marker_conflict_block_count": marker_conflict_block_count,
            "forced_problem_start_block_count": forced_problem_start_count,
            "problem_number_block_count": problem_number_block_count,
        },
        "problem_number_source_counts": problem_number_source_counts,
        "block_diagnostics": block_diagnostics,
        "problem_number_source": next(iter(problem_number_source_counts), None),
    }


def group_problem_units(page: PageModel) -> PageModel:
    relabeled = relabel_reading_order(page)
    classified_blocks = [classify_block(block) for block in relabeled.blocks]
    block_diagnostics = [_block_grouping_diagnostics(block) for block in classified_blocks]
    has_text_markers = any(detect_problem_start(block) for block in classified_blocks)
    has_band_metadata = any("question_band_index" in block.metadata for block in classified_blocks)
    fallback_grouping = not has_text_markers and (has_band_metadata or len(classified_blocks) > 1)

    diagnostics = _build_grouping_diagnostics(
        page=relabeled,
        classified_blocks=classified_blocks,
        block_diagnostics=block_diagnostics,
        has_text_markers=has_text_markers,
        has_band_metadata=has_band_metadata,
        fallback_grouping=fallback_grouping,
    )
    relabeled.metadata["grouping_diagnostics"] = diagnostics
    relabeled.metadata["grouping_source"] = diagnostics["grouping_source"]
    relabeled.metadata["grouping_mode"] = diagnostics["grouping_mode"]
    relabeled.metadata["marker_counts"] = diagnostics["marker_counts"]
    relabeled.metadata["fallback_grouping_stats"] = diagnostics["fallback_grouping_stats"]
    relabeled.metadata["problem_number_source_counts"] = diagnostics["problem_number_source_counts"]

    if fallback_grouping:
        problems: list[ProblemUnit] = []
        for index, block in enumerate(classified_blocks, start=1):
            problem_number, number_source = extract_problem_number_from_block(block)
            block_diag = block_diagnostics[index - 1]
            current = ProblemUnit(
                unit_id=f"{page.page_id}-problem-{index}",
                subject=infer_subject(relabeled),
                title=_problem_display_title(block),
            )
            if block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
                current.figure_block_ids.append(block.block_id)
            elif block.block_type == BlockType.EXPLANATION:
                current.explanation_block_ids.append(block.block_id)
            elif block.block_type == BlockType.CHOICE:
                current.choice_block_ids.append(block.block_id)
            else:
                current.stem_block_ids.append(block.block_id)
            current.metadata.update(
                {
                    "fallback_grouping": True,
                    "grouping_mode": diagnostics["grouping_mode"],
                    "grouping_source": diagnostics["grouping_source"],
                    "grouping_index": index,
                    "question_band_index": block.metadata.get("question_band_index"),
                    "column_index": block.metadata.get("column_index"),
                    "marker_conflict": bool(block_diag.get("marker_conflict")),
                    "problem_marker": bool(block_diag.get("problem_marker")),
                    "choice_marker": bool(block_diag.get("choice_marker")),
                    "problem_number_source": block_diag.get("problem_number_source"),
                }
            )
            if problem_number is not None:
                current.metadata["problem_number"] = problem_number
                current.metadata["problem_number_source"] = number_source
            current.metadata["marker_counts"] = {
                "problem_marker": int(bool(block_diag.get("problem_marker"))),
                "choice_marker": int(bool(block_diag.get("choice_marker"))),
                "marker_conflict": int(bool(block_diag.get("marker_conflict"))),
            }
            problems.append(current)
        diagnostics["problem_count"] = len(problems)
        relabeled.metadata["grouping_diagnostics"] = diagnostics
        relabeled.metadata["fallback_grouping_stats"]["problem_count"] = len(problems)
        return replace(relabeled, subject=infer_subject(relabeled), blocks=classified_blocks, problems=problems)

    problems: list[ProblemUnit] = []
    current: ProblemUnit | None = None

    for index, block in enumerate(classified_blocks, start=1):
        block_diag = block_diagnostics[index - 1]
        if detect_problem_start(block) or current is None:
            problem_number, number_source = extract_problem_number_from_block(block)
            current = ProblemUnit(
                unit_id=f"{page.page_id}-problem-{len(problems) + 1}",
                subject=infer_subject(relabeled),
                title=_problem_display_title(block),
            )
            if problem_number is not None:
                current.metadata["problem_number"] = problem_number
                current.metadata["problem_number_source"] = number_source
            current.metadata.update(
                {
                    "grouping_mode": diagnostics["grouping_mode"],
                    "grouping_source": diagnostics["grouping_source"],
                    "grouping_index": len(problems) + 1,
                    "marker_conflict": bool(block_diag.get("marker_conflict")),
                    "problem_marker": bool(block_diag.get("problem_marker")),
                    "choice_marker": bool(block_diag.get("choice_marker")),
                    "problem_number_source": number_source or block_diag.get("problem_number_source"),
                    "marker_counts": {
                        "problem_marker": int(bool(block_diag.get("problem_marker"))),
                        "choice_marker": int(bool(block_diag.get("choice_marker"))),
                        "marker_conflict": int(bool(block_diag.get("marker_conflict"))),
                    },
                }
            )
            problems.append(current)

        if block.block_type in {BlockType.TITLE, BlockType.STEM, BlockType.FORMULA, BlockType.SECTION}:
            current.stem_block_ids.append(block.block_id)
        elif block.block_type == BlockType.CHOICE:
            current.choice_block_ids.append(block.block_id)
        elif block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
            current.figure_block_ids.append(block.block_id)
        elif block.block_type == BlockType.EXPLANATION:
            current.explanation_block_ids.append(block.block_id)
        elif block.text:
            current.stem_block_ids.append(block.block_id)

    diagnostics["problem_count"] = len(problems)
    relabeled.metadata["grouping_diagnostics"] = diagnostics
    relabeled.metadata["fallback_grouping_stats"]["problem_count"] = len(problems)
    return replace(relabeled, subject=infer_subject(relabeled), blocks=classified_blocks, problems=problems)


def summarize_page(page: PageModel) -> dict[str, object]:
    grouped = group_problem_units(page)
    return {
        "page_id": grouped.page_id,
        "subject": grouped.subject,
        "block_count": len(grouped.blocks),
        "problem_count": len(grouped.problems),
        "grouping_diagnostics": grouped.metadata.get("grouping_diagnostics"),
        "problems": [
            {
                "unit_id": problem.unit_id,
                "title": problem.title,
                "problem_number": problem.metadata.get("problem_number"),
                "problem_number_source": problem.metadata.get("problem_number_source"),
                "stem_blocks": list(problem.stem_block_ids),
                "choice_blocks": list(problem.choice_block_ids),
                "figure_blocks": list(problem.figure_block_ids),
            }
            for problem in grouped.problems
        ],
    }
