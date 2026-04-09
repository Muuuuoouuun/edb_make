#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    np = None


@dataclass(slots=True)
class SegmentOptions:
    min_area_ratio: float = 0.0005
    merge_gap_px: int = 12


def _pil_to_gray_array(image: Image.Image):
    if np is None:
        raise RuntimeError("numpy is required for segmentation")
    return np.array(image.convert("L"))


def _merge_boxes(boxes: list[Box], gap_px: int) -> list[Box]:
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        next_boxes: list[Box] = []
        while merged:
            current = merged.pop(0)
            i = 0
            while i < len(merged):
                other = merged[i]
                horizontal_overlap = min(current.right, other.right) - max(current.left, other.left)
                vertical_overlap = min(current.bottom, other.bottom) - max(current.top, other.top)
                close_vertically = abs(other.top - current.bottom) <= gap_px or abs(current.top - other.bottom) <= gap_px
                close_horizontally = abs(other.left - current.right) <= gap_px or abs(current.left - other.right) <= gap_px
                if horizontal_overlap > 0 or vertical_overlap > 0 or close_vertically or close_horizontally:
                    current = Box.from_points(
                        min(current.left, other.left),
                        min(current.top, other.top),
                        max(current.right, other.right),
                        max(current.bottom, other.bottom),
                    )
                    merged.pop(i)
                    changed = True
                    continue
                i += 1
            next_boxes.append(current)
        merged = next_boxes
    return merged


def _classify_geometry(box: Box, page_width: int, page_height: int, fill_ratio: float) -> BlockType:
    if box.top < page_height * 0.16 and box.width > page_width * 0.45 and box.height < page_height * 0.12:
        return BlockType.TITLE
    if fill_ratio < 0.12 and box.area > page_width * page_height * 0.01:
        return BlockType.IMAGE
    return BlockType.STEM


def _find_candidate_boxes(image: Image.Image, options: SegmentOptions) -> list[tuple[Box, float]]:
    page_width, page_height = image.size
    if cv2 is None or np is None:
        return [(Box(left=0.0, top=0.0, width=float(page_width), height=float(page_height)), 1.0)]

    gray = _pil_to_gray_array(image)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_width = max(12, page_width // 40)
    kernel_height = max(3, page_height // 200)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, kernel_height))
    grouped = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[Box, float]] = []
    min_area = page_width * page_height * options.min_area_ratio
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < min_area:
            continue
        crop = binary[y : y + h, x : x + w]
        fill_ratio = float(np.count_nonzero(crop)) / float(crop.size) if crop.size else 0.0
        candidates.append((Box(left=float(x), top=float(y), width=float(w), height=float(h)), fill_ratio))

    if not candidates:
        return [(Box(left=0.0, top=0.0, width=float(page_width), height=float(page_height)), 1.0)]
    return candidates


def segment_page(image_path: str | Path, *, page_id: str, subject: Subject = Subject.UNKNOWN, options: SegmentOptions | None = None) -> PageModel:
    resolved_options = options or SegmentOptions()
    image = Image.open(image_path).convert("RGB")
    page_width, page_height = image.size
    candidates = _find_candidate_boxes(image, resolved_options)

    merged_boxes = _merge_boxes([box for box, _ in candidates], resolved_options.merge_gap_px)
    blocks: list[ContentBlock] = []
    for index, box in enumerate(sorted(merged_boxes, key=lambda item: (item.top, item.left))):
        fill_ratio = 0.25
        for candidate_box, candidate_fill_ratio in candidates:
            if abs(candidate_box.left - box.left) < 1 and abs(candidate_box.top - box.top) < 1:
                fill_ratio = candidate_fill_ratio
                break
        block_type = _classify_geometry(box, page_width, page_height, fill_ratio)
        blocks.append(
            ContentBlock(
                block_id=f"{page_id}-block-{index + 1:03d}",
                block_type=block_type,
                bbox=box,
                reading_order=index,
                metadata={"fill_ratio": fill_ratio},
            )
        )

    return PageModel(
        page_id=page_id,
        width_px=page_width,
        height_px=page_height,
        subject=subject,
        source_path=str(image_path),
        blocks=blocks,
        metadata={"segmenter": "rule-based"},
    )


def crop_block_images(image_path: str | Path, blocks: Iterable[ContentBlock], output_dir: str | Path) -> dict[str, str]:
    image = Image.open(image_path).convert("RGB")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for block in blocks:
        crop = image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom)))
        path = out_dir / f"{block.block_id}.png"
        crop.save(path)
        written[block.block_id] = str(path)
    return written


def blocks_from_page(prepared_page) -> list[ContentBlock]:
    page = segment_page(prepared_page.normalized_path, page_id=prepared_page.page_id)
    return page.blocks


def crop_block_image(prepared_page, block: ContentBlock) -> Image.Image:
    image = Image.open(prepared_page.normalized_path).convert("RGB")
    return image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom)))


def blocks_from_page(prepared_page, config: SegmentOptions | None = None) -> list[ContentBlock]:
    resolved = config or SegmentOptions()
    image = prepared_page.image.convert("RGB")
    page_width, page_height = image.size
    candidates = _find_candidate_boxes(image, resolved)
    merged_boxes = _merge_boxes([box for box, _ in candidates], resolved.merge_gap_px)

    blocks: list[ContentBlock] = []
    for index, box in enumerate(sorted(merged_boxes, key=lambda item: (item.top, item.left))):
        fill_ratio = 0.25
        for candidate_box, candidate_fill_ratio in candidates:
            if abs(candidate_box.left - box.left) < 1 and abs(candidate_box.top - box.top) < 1:
                fill_ratio = candidate_fill_ratio
                break
        block_type = _classify_geometry(box, page_width, page_height, fill_ratio)
        blocks.append(
            ContentBlock(
                block_id=f"{prepared_page.page_id}-block-{index + 1:03d}",
                block_type=block_type,
                bbox=box,
                reading_order=index,
                metadata={"fill_ratio": fill_ratio},
            )
        )
    return blocks


def crop_block_image(prepared_page, block: ContentBlock) -> Image.Image:
    return prepared_page.image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom)))
