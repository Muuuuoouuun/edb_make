#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import replace

from structured_schema import BlockType, ContentBlock, PageModel, ProblemUnit, Subject


PROBLEM_MARKER_RE = re.compile(r"^\s*(?:문항\s*)?(?P<number>[1-9][0-9]{0,2})(?:[\.\)])(?:\s+|$)")
CHOICE_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"[\u2460-\u2469]|"
    r"\([1-9][0-9]?\)|"
    r"[1-9][0-9]?\)|"
    r"[A-Ha-h][\.\)]|"
    r"[\u3131-\u314e][\.\)]"
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
        return int(match.group("number"))
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


def infer_subject(page: PageModel) -> Subject:
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
    if block.block_type in {BlockType.TITLE, BlockType.SECTION}:
        if not (block.text and block.text.strip()) and not block.metadata.get("display_title"):
            return False
        return True
    marker_source = _problem_title_source(block)
    if not marker_source:
        return False
    if _matches_choice_marker(marker_source):
        return False
    return _matches_problem_marker(marker_source)


def detect_choice_block(block: ContentBlock) -> bool:
    if block.metadata.get("force_problem_start"):
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


def group_problem_units(page: PageModel) -> PageModel:
    relabeled = relabel_reading_order(page)
    classified_blocks = [classify_block(block) for block in relabeled.blocks]
    has_text_markers = any(detect_problem_start(block) for block in classified_blocks)
    has_band_metadata = any("question_band_index" in block.metadata for block in classified_blocks)

    if not has_text_markers and (has_band_metadata or len(classified_blocks) > 1):
        problems: list[ProblemUnit] = []
        for index, block in enumerate(classified_blocks, start=1):
            problem_number = extract_problem_number(_problem_title_source(block))
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
                    "question_band_index": block.metadata.get("question_band_index"),
                    "column_index": block.metadata.get("column_index"),
                }
            )
            if problem_number is not None:
                current.metadata["problem_number"] = problem_number
            problems.append(current)
        return replace(relabeled, subject=infer_subject(relabeled), blocks=classified_blocks, problems=problems)

    problems: list[ProblemUnit] = []
    current: ProblemUnit | None = None

    for block in classified_blocks:
        if detect_problem_start(block) or current is None:
            problem_number = extract_problem_number(_problem_title_source(block))
            current = ProblemUnit(
                unit_id=f"{page.page_id}-problem-{len(problems) + 1}",
                subject=infer_subject(relabeled),
                title=_problem_display_title(block),
            )
            if problem_number is not None:
                current.metadata["problem_number"] = problem_number
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

    return replace(relabeled, subject=infer_subject(relabeled), blocks=classified_blocks, problems=problems)


def summarize_page(page: PageModel) -> dict[str, object]:
    grouped = group_problem_units(page)
    return {
        "page_id": grouped.page_id,
        "subject": grouped.subject,
        "block_count": len(grouped.blocks),
        "problem_count": len(grouped.problems),
        "problems": [
            {
                "unit_id": problem.unit_id,
                "title": problem.title,
                "problem_number": problem.metadata.get("problem_number"),
                "stem_blocks": list(problem.stem_block_ids),
                "choice_blocks": list(problem.choice_block_ids),
                "figure_blocks": list(problem.figure_block_ids),
            }
            for problem in grouped.problems
        ],
    }
