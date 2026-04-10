#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class Subject(StrEnum):
    MATH = "math"
    SCIENCE = "science"
    KOREAN = "korean"
    ENGLISH = "english"
    SOCIAL = "social"
    UNKNOWN = "unknown"


class BlockType(StrEnum):
    TITLE = "title"
    SECTION = "section"
    STEM = "stem"
    CHOICE = "choice"
    EXPLANATION = "explanation"
    NOTE = "note"
    FORMULA = "formula"
    TABLE = "table"
    DIAGRAM = "diagram"
    IMAGE = "image"
    FOOTNOTE = "footnote"
    DECORATION = "decoration"
    UNKNOWN = "unknown"


class AssetType(StrEnum):
    IMAGE = "image"
    CROP = "crop"
    DIAGRAM = "diagram"


@dataclass
class Box:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    @classmethod
    def from_points(cls, left: float, top: float, right: float, bottom: float) -> "Box":
        return cls(left=left, top=top, width=max(0.0, right - left), height=max(0.0, bottom - top))

    def normalize(self, page_width: float, page_height: float) -> "Box":
        return Box(
            left=self.left / page_width,
            top=self.top / page_height,
            width=self.width / page_width,
            height=self.height / page_height,
        )

    def denormalize(self, page_width: float, page_height: float) -> "Box":
        return Box(
            left=self.left * page_width,
            top=self.top * page_height,
            width=self.width * page_width,
            height=self.height * page_height,
        )

    def expanded(self, padding: float, max_width: float | None = None, max_height: float | None = None) -> "Box":
        left = max(0.0, self.left - padding)
        top = max(0.0, self.top - padding)
        right = self.right + padding
        bottom = self.bottom + padding
        if max_width is not None:
            right = min(max_width, right)
        if max_height is not None:
            bottom = min(max_height, bottom)
        return Box(left=left, top=top, width=max(0.0, right - left), height=max(0.0, bottom - top))


@dataclass
class TextStyle:
    font_size: float | None = None
    weight: str | None = None
    italic: bool = False
    color: str | None = None
    align: str | None = None
    math_like: bool = False


@dataclass
class AssetRef:
    asset_id: str
    asset_type: AssetType
    source_path: str | None = None
    crop_box: Box | None = None
    width_px: int | None = None
    height_px: int | None = None
    mime_type: str | None = None


@dataclass
class OcrWord:
    text: str
    bbox: Box
    confidence: float | None = None


@dataclass
class OcrLine:
    text: str
    bbox: Box
    confidence: float | None = None
    words: list[OcrWord] = field(default_factory=list)


@dataclass
class ContentBlock:
    block_id: str
    block_type: BlockType
    bbox: Box
    reading_order: int
    text: str | None = None
    style: TextStyle | None = None
    confidence: float | None = None
    asset: AssetRef | None = None
    ocr_lines: list[OcrLine] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProblemUnit:
    unit_id: str
    subject: Subject
    title: str | None
    stem_block_ids: list[str] = field(default_factory=list)
    choice_block_ids: list[str] = field(default_factory=list)
    explanation_block_ids: list[str] = field(default_factory=list)
    figure_block_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageModel:
    page_id: str
    width_px: int
    height_px: int
    subject: Subject
    source_path: str | None = None
    blocks: list[ContentBlock] = field(default_factory=list)
    problems: list[ProblemUnit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def sorted_blocks(self) -> list[ContentBlock]:
        return sorted(self.blocks, key=lambda block: (block.reading_order, block.bbox.top, block.bbox.left))

    def normalize(self) -> "PageModel":
        normalized_blocks = [
            ContentBlock(
                block_id=block.block_id,
                block_type=block.block_type,
                bbox=block.bbox.normalize(self.width_px, self.height_px),
                reading_order=block.reading_order,
                text=block.text,
                style=block.style,
                confidence=block.confidence,
                asset=block.asset,
                ocr_lines=list(block.ocr_lines),
                labels=list(block.labels),
                children=list(block.children),
                metadata=dict(block.metadata),
            )
            for block in self.blocks
        ]
        return PageModel(
            page_id=self.page_id,
            width_px=self.width_px,
            height_px=self.height_px,
            subject=self.subject,
            source_path=self.source_path,
            blocks=normalized_blocks,
            problems=list(self.problems),
            metadata=dict(self.metadata),
        )


def page_to_dict(page: PageModel) -> dict[str, Any]:
    return asdict(page)


def pages_to_json(pages: list[PageModel], indent: int = 2) -> str:
    payload = [page_to_dict(page) for page in pages]
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def save_pages_json(pages: list[PageModel], path: str | Path, indent: int = 2) -> None:
    Path(path).write_text(pages_to_json(pages, indent=indent), encoding="utf-8")


def infer_math_like_text(text: str) -> bool:
    markers = (
        "=",
        "lim",
        "sin",
        "cos",
        "tan",
        "log",
        "\u222b",
        "\u2211",
        "\u221a",
        "\u2264",
        "\u2265",
        "\ud655\ub960",
        "\ud568\uc218",
        "\ubbf8\ubd84",
        "\uc801\ubd84",
    )
    return any(marker in text for marker in markers)


def is_choice_marker(text: str) -> bool:
    prefixes = (
        "\u2460",
        "\u2461",
        "\u2462",
        "\u2463",
        "\u2464",
        "1)",
        "2)",
        "3)",
        "4)",
        "5)",
        "A.",
        "B.",
        "C.",
        "D.",
    )
    stripped = text.strip()
    return stripped.startswith(prefixes)


def classify_text_block(text: str) -> BlockType:
    stripped = text.strip()
    if not stripped:
        return BlockType.UNKNOWN
    if infer_math_like_text(stripped):
        return BlockType.FORMULA
    if is_choice_marker(stripped):
        return BlockType.CHOICE
    if len(stripped) <= 24 and stripped.endswith(("\uc7a5", "\ub2e8\uc6d0", "\uc8fc\uc81c")):
        return BlockType.SECTION
    return BlockType.STEM
