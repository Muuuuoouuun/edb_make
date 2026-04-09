#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import replace

from structured_schema import BlockType, ContentBlock, PageModel, ProblemUnit, Subject


PROBLEM_MARKER_RE = re.compile(r"^\s*(\d+|[0-9]+\)|[0-9]+\.)\s*")
CHOICE_MARKER_RE = re.compile(
    r"^\s*(\u2460|\u2461|\u2462|\u2463|\u2464|1\)|2\)|3\)|4\)|5\)|A\.|B\.|C\.|D\.)\s*"
)


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
    if block.block_type in {BlockType.TITLE, BlockType.SECTION}:
        return True
    if not block.text:
        return False
    return bool(PROBLEM_MARKER_RE.match(block.text))


def detect_choice_block(block: ContentBlock) -> bool:
    if block.block_type == BlockType.CHOICE:
        return True
    if not block.text:
        return False
    return bool(CHOICE_MARKER_RE.match(block.text))


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
    has_text_markers = any(detect_problem_start(block) for block in classified_blocks if block.text)
    has_band_metadata = any("question_band_index" in block.metadata for block in classified_blocks)

    if not has_text_markers and (has_band_metadata or len(classified_blocks) > 1):
        problems: list[ProblemUnit] = []
        for index, block in enumerate(classified_blocks, start=1):
            current = ProblemUnit(
                unit_id=f"{page.page_id}-problem-{index}",
                subject=infer_subject(relabeled),
                title=block.metadata.get("display_title"),
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
            problems.append(current)
        return replace(relabeled, subject=infer_subject(relabeled), blocks=classified_blocks, problems=problems)

    problems: list[ProblemUnit] = []
    current: ProblemUnit | None = None

    for block in classified_blocks:
        if detect_problem_start(block) or current is None:
            current = ProblemUnit(
                unit_id=f"{page.page_id}-problem-{len(problems) + 1}",
                subject=infer_subject(relabeled),
                title=block.text.strip() if block.text else None,
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
                "stem_blocks": list(problem.stem_block_ids),
                "choice_blocks": list(problem.choice_block_ids),
                "figure_blocks": list(problem.figure_block_ids),
            }
            for problem in grouped.problems
        ],
    }
