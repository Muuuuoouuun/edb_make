#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from structured_schema import Subject


@dataclass(slots=True)
class LayoutTemplate:
    name: str
    board_page_count: int = 50
    base_slot_height_pages: float = 1.2
    fixed_left_zone_ratio: float = 0.52
    preserve_right_writing_zone: bool = True
    default_overflow_subjects: set[Subject] = field(
        default_factory=lambda: {Subject.KOREAN, Subject.ENGLISH}
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProblemLayoutInput:
    problem_id: str
    subject: Subject = Subject.UNKNOWN
    actual_content_height_pages: float = 1.2
    overflow_allowed: bool | None = None
    reading_heavy: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProblemPlacement:
    problem_id: str
    subject: Subject
    start_y_pages: float
    nominal_slot_height_pages: float
    actual_content_height_pages: float
    actual_bottom_y_pages: float
    snapped_next_start_y_pages: float
    overflow_allowed: bool
    overflow_amount_pages: float
    overflow_violation: bool
    slot_span_count: int
    board_capacity_exceeded: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BoardExportPlan:
    template: LayoutTemplate
    placements: list[ProblemPlacement]
    rendered_page_paths: list[str] = field(default_factory=list)
    edb_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def build_default_template(name: str = "MVP Board") -> LayoutTemplate:
    return LayoutTemplate(name=name)
