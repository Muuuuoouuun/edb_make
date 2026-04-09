#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import replace

from structured_schema import BlockType, ContentBlock, PageModel, ProblemUnit, Subject


PROBLEM_MARKER_RE = re.compile(r"^\s*(\d+|[0-9]+\)|[0-9]+\.)\s*")
CHOICE_MARKER_RE = re.compile(r"^\s*(①|②|③|④|⑤|1\)|2\)|3\)|4\)|5\)|A\.|B\.|C\.|D\.)\s*")


def infer_subject(page: PageModel) -> Subject:
    text = "\n".join(block.text or "" for block in page.blocks)
    lowered = text.lower()
    if any(token in lowered for token in ("lim", "sin", "cos", "tan", "확률", "함수", "미분", "적분")):
        return Subject.MATH
    if any(token in lowered for token in ("실험", "분자", "원자", "전류", "가속도", "광합성")):
        return Subject.SCIENCE
    if any(token in lowered for token in ("다음 글", "보기", "문단", "작가", "화자", "밑줄")):
        return Subject.KOREAN
    return page.subject


def sort_blocks_for_reading_order(blocks: list[ContentBlock]) -> list[ContentBlock]:
    return sorted(blocks, key=lambda block: (round(block.bbox.top, 4), round(block.bbox.left, 4), block.reading_order))


def relabel_reading_order(page: PageModel) -> PageModel:
    sorted_blocks = sort_blocks_for_reading_order(page.blocks)
    rewritten = [
        replace(block, reading_order=index)
        for index, block in enumerate(sorted_blocks)
    ]
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

    problems: list[ProblemUnit] = []
    current: ProblemUnit | None = None

    for index, block in enumerate(classified_blocks):
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
        else:
            if block.text:
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
