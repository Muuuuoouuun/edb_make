#!/usr/bin/env python3
from __future__ import annotations

import math
from typing import Iterable

from layout_template_schema import BoardExportPlan, LayoutTemplate, ProblemLayoutInput, ProblemPlacement, build_default_template
from structured_schema import PageModel, ProblemUnit, Subject


EPSILON = 1e-9


def snap_up_to_slot(value_pages: float, base_slot_height_pages: float) -> float:
    if base_slot_height_pages <= 0:
        raise ValueError("base_slot_height_pages must be greater than 0")
    if value_pages <= 0:
        return 0.0
    snapped_units = math.ceil((value_pages - EPSILON) / base_slot_height_pages)
    return round(snapped_units * base_slot_height_pages, 6)


def resolve_overflow_allowed(problem: ProblemLayoutInput, template: LayoutTemplate) -> bool:
    if problem.overflow_allowed is not None:
        return problem.overflow_allowed
    if problem.reading_heavy:
        return True
    return problem.subject in template.default_overflow_subjects


def place_problem(
    problem: ProblemLayoutInput,
    *,
    start_y_pages: float,
    template: LayoutTemplate,
) -> ProblemPlacement:
    nominal_slot_height_pages = template.base_slot_height_pages
    actual_height_pages = max(problem.actual_content_height_pages, 0.0)
    overflow_allowed = resolve_overflow_allowed(problem, template)

    actual_bottom_y_pages = round(start_y_pages + actual_height_pages, 6)
    snapped_next_start_y_pages = snap_up_to_slot(
        actual_bottom_y_pages,
        nominal_slot_height_pages,
    )
    overflow_amount_pages = max(0.0, actual_height_pages - nominal_slot_height_pages)
    overflow_violation = overflow_amount_pages > 0 and not overflow_allowed
    slot_span_count = max(
        1,
        int(
            round(
                (snapped_next_start_y_pages - start_y_pages) / nominal_slot_height_pages
            )
        ),
    )
    board_capacity_exceeded = actual_bottom_y_pages > template.board_page_count

    return ProblemPlacement(
        problem_id=problem.problem_id,
        subject=problem.subject,
        start_y_pages=round(start_y_pages, 6),
        nominal_slot_height_pages=nominal_slot_height_pages,
        actual_content_height_pages=actual_height_pages,
        actual_bottom_y_pages=actual_bottom_y_pages,
        snapped_next_start_y_pages=snapped_next_start_y_pages,
        overflow_allowed=overflow_allowed,
        overflow_amount_pages=round(overflow_amount_pages, 6),
        overflow_violation=overflow_violation,
        slot_span_count=slot_span_count,
        board_capacity_exceeded=board_capacity_exceeded,
        metadata=dict(problem.metadata),
    )


def place_problems(
    problems: Iterable[ProblemLayoutInput],
    *,
    template: LayoutTemplate,
    start_y_pages: float = 0.0,
) -> list[ProblemPlacement]:
    placements: list[ProblemPlacement] = []
    cursor_y_pages = snap_up_to_slot(
        max(start_y_pages, 0.0),
        template.base_slot_height_pages,
    )

    for problem in problems:
        placement = place_problem(
            problem,
            start_y_pages=cursor_y_pages,
            template=template,
        )
        placements.append(placement)
        cursor_y_pages = placement.snapped_next_start_y_pages

    return placements


def summarize_placements(placements: Iterable[ProblemPlacement]) -> dict[str, int | float]:
    placement_list = list(placements)
    if not placement_list:
        return {
            "problem_count": 0,
            "overflow_count": 0,
            "overflow_violation_count": 0,
            "max_bottom_y_pages": 0.0,
        }

    return {
        "problem_count": len(placement_list),
        "overflow_count": sum(1 for item in placement_list if item.overflow_amount_pages > 0),
        "overflow_violation_count": sum(1 for item in placement_list if item.overflow_violation),
        "max_bottom_y_pages": max(item.actual_bottom_y_pages for item in placement_list),
    }


def build_demo_problems() -> list[ProblemLayoutInput]:
    return [
        ProblemLayoutInput(
            problem_id="math-1",
            subject=Subject.MATH,
            actual_content_height_pages=0.92,
        ),
        ProblemLayoutInput(
            problem_id="korean-1",
            subject=Subject.KOREAN,
            actual_content_height_pages=1.43,
            reading_heavy=True,
        ),
        ProblemLayoutInput(
            problem_id="science-1",
            subject=Subject.SCIENCE,
            actual_content_height_pages=1.24,
            overflow_allowed=False,
        ),
    ]


def problem_title(problem: ProblemUnit) -> str | None:
    title = (problem.title or "").strip()
    return title or None


def estimate_problem_height_pages(page: PageModel, problem: ProblemUnit, *, min_height_pages: float = 0.35) -> float:
    lookup = {block.block_id: block for block in page.blocks}
    block_ids = list(problem.stem_block_ids) + list(problem.choice_block_ids) + list(problem.explanation_block_ids) + list(problem.figure_block_ids)
    selected = [lookup[block_id] for block_id in block_ids if block_id in lookup]
    if not selected:
        return round(max(min_height_pages, 0.9), 6)

    top = min(block.bbox.top for block in selected)
    bottom = max(block.bbox.bottom for block in selected)
    normalized_height = max(0.0, (bottom - top) / max(page.height_px, 1))
    figure_bonus = 0.18 if problem.figure_block_ids else 0.0
    choice_bonus = 0.08 if problem.choice_block_ids else 0.0
    reading_bonus = 0.08 if problem.subject in {Subject.KOREAN, Subject.ENGLISH} else 0.0
    estimated = normalized_height * 1.2 + figure_bonus + choice_bonus + reading_bonus
    return round(max(min_height_pages, min(2.4, estimated)), 6)


def is_reading_heavy(problem: ProblemUnit) -> bool:
    return problem.subject in {Subject.KOREAN, Subject.ENGLISH} or len(problem.choice_block_ids) > 0 or len(problem.figure_block_ids) > 0


def problem_input_from_page(page: PageModel, problem: ProblemUnit) -> ProblemLayoutInput:
    return ProblemLayoutInput(
        problem_id=problem.unit_id,
        subject=problem.subject,
        actual_content_height_pages=estimate_problem_height_pages(page, problem),
        overflow_allowed=None,
        reading_heavy=is_reading_heavy(problem),
        metadata={
            "source_page_id": page.page_id,
            "title": problem_title(problem),
            "stem_block_count": len(problem.stem_block_ids),
            "choice_block_count": len(problem.choice_block_ids),
            "figure_block_count": len(problem.figure_block_ids),
        },
    )


def problem_inputs_from_pages(page_models: Iterable[PageModel]) -> list[ProblemLayoutInput]:
    inputs: list[ProblemLayoutInput] = []
    for page in page_models:
        for problem in page.problems:
            inputs.append(problem_input_from_page(page, problem))
    return inputs


def build_export_plan(
    page_models: Iterable[PageModel],
    *,
    template: LayoutTemplate | None = None,
    start_y_pages: float = 0.0,
) -> BoardExportPlan:
    resolved_template = template or build_default_template()
    problems = problem_inputs_from_pages(page_models)
    placements = place_problems(problems, template=resolved_template, start_y_pages=start_y_pages)
    return BoardExportPlan(template=resolved_template, placements=placements)
