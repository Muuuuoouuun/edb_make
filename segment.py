#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps, ImageStat

from structured_schema import BlockType, Box, ContentBlock, PageModel, Subject

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    np = None


@dataclass(slots=True)
class SegmentOptions:
    min_area_ratio: float = 0.00025
    merge_gap_px: int = 16
    max_merge_gap_y_px: int = 28
    max_merge_gap_x_px: int = 48
    min_fill_ratio: float = 0.01
    ignore_large_border_ratio: float = 0.92
    fallback_row_density: float = 0.0035
    fallback_band_gap_px: int = 10
    fallback_min_band_height_px: int = 18
    fallback_padding_px: int = 10
    fallback_board_margin_ratio: float = 0.04
    fallback_board_min_hit_ratio: float = 0.08
    document_dark_threshold: int = 235
    document_projection_window_px: int = 10
    document_row_density_ratio: float = 0.11
    document_band_merge_gap_px: int = 42
    document_small_band_height_px: int = 150
    document_near_gap_px: int = 210
    document_min_band_height_px: int = 60
    document_band_padding_px: int = 24


def _pil_to_gray_array(image: Image.Image):
    if np is None:
        raise RuntimeError("numpy is required for segmentation")
    return np.array(image.convert("L"))


def _load_image(image_source: Any) -> Image.Image:
    if isinstance(image_source, Image.Image):
        return image_source.convert("RGB")

    for attr in ("image", "normalized_image"):
        image = getattr(image_source, attr, None)
        if isinstance(image, Image.Image):
            return image.convert("RGB")

    for attr in ("normalized_path", "source_path"):
        path = getattr(image_source, attr, None)
        if path:
            return Image.open(path).convert("RGB")

    return Image.open(image_source).convert("RGB")


def _sample_board_like_pixel(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    brightness = (r + g + b) / 3.0
    color_spread = max(r, g, b) - min(r, g, b)
    green_bias = g - max(r, b)
    if brightness < 125.0:
        return True
    if brightness < 235.0 and color_spread > 18 and green_bias >= -12:
        return True
    return False


def _detect_board_region_pil(image: Image.Image, options: SegmentOptions) -> Box:
    width, height = image.size
    step = max(6, min(width, height) // 180)
    sampled_cols = len(range(0, width, step))
    sampled_rows = len(range(0, height, step))
    row_scores = [0] * sampled_rows
    col_scores = [0] * sampled_cols

    pixels = image.convert("RGB").load()
    for row_index, y in enumerate(range(0, height, step)):
        for col_index, x in enumerate(range(0, width, step)):
            if _sample_board_like_pixel(pixels[x, y]):
                row_scores[row_index] += 1
                col_scores[col_index] += 1

    row_threshold = max(2, int(sampled_cols * options.fallback_board_min_hit_ratio))
    col_threshold = max(2, int(sampled_rows * max(0.05, options.fallback_board_min_hit_ratio * 0.75)))
    active_rows = [index for index, score in enumerate(row_scores) if score >= row_threshold]
    active_cols = [index for index, score in enumerate(col_scores) if score >= col_threshold]

    if active_rows and active_cols:
        left = max(0, active_cols[0] * step - step)
        top = max(0, active_rows[0] * step - step)
        right = min(width, (active_cols[-1] + 1) * step + step)
        bottom = min(height, (active_rows[-1] + 1) * step + step)

        margin = max(8, int(min(width, height) * options.fallback_board_margin_ratio))
        left = max(0, left - margin)
        top = max(0, top - margin)
        right = min(width, right + margin)
        bottom = min(height, bottom + margin)

        if right > left and bottom > top:
            region = Box.from_points(float(left), float(top), float(right), float(bottom))
            if region.area >= float(width * height) * 0.08:
                return region

    margin_x = max(12, int(width * 0.08))
    margin_y = max(12, int(height * 0.08))
    return Box.from_points(float(margin_x), float(margin_y), float(width - margin_x), float(height - margin_y))


def _detect_board_region(image: Image.Image, options: SegmentOptions) -> Box:
    if cv2 is None or np is None:
        return _detect_board_region_pil(image, options)

    rgb = np.array(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    lower = np.array([35, 10, 20], dtype=np.uint8)
    upper = np.array([120, 255, 220], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0.0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = float(w * h)
        if area > best_area and w > image.width * 0.35 and h > image.height * 0.2:
            best = Box(left=float(x), top=float(y), width=float(w), height=float(h))
            best_area = area

    return best or _detect_board_region_pil(image, options)


def _source_metadata(image_source: Any) -> dict[str, Any]:
    metadata = getattr(image_source, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _is_document_like_page(image_source: Any, image: Image.Image) -> bool:
    metadata = _source_metadata(image_source)
    source_type = str(metadata.get("source_type") or "").lower()
    if source_type == "pdf" or metadata.get("document_like"):
        return True

    source_path = getattr(image_source, "source_path", None)
    if source_path and str(source_path).lower().endswith(".pdf"):
        return True

    stat = ImageStat.Stat(ImageOps.grayscale(image))
    return stat.mean[0] >= 220.0 and stat.stddev[0] <= 55.0


def _dark_mask(image: Image.Image, threshold: int) -> Image.Image:
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    return gray.point(lambda px: 255 if px < threshold else 0, mode="L")


def _smooth_projection(values: list[float], window: int) -> list[float]:
    if not values:
        return []
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - window)
        end = min(len(values), index + window + 1)
        smoothed.append(sum(values[start:end]) / max(1, end - start))
    return smoothed


def _find_document_content_box(mask: Image.Image, width: int, height: int) -> Box:
    bbox = mask.getbbox()
    if bbox is None:
        return Box(left=0.0, top=0.0, width=float(width), height=float(height))
    left, top, right, bottom = bbox
    return Box.from_points(float(left), float(top), float(right), float(bottom)).expanded(
        12.0,
        max_width=float(width),
        max_height=float(height),
    )


def _detect_document_columns(mask: Image.Image, content_box: Box, options: SegmentOptions) -> list[Box]:
    crop = mask.crop((int(content_box.left), int(content_box.top), int(content_box.right), int(content_box.bottom)))
    if crop.width <= 1 or crop.height <= 1:
        return [content_box]

    column_projection = [
        int(crop.crop((x, 0, x + 1, crop.height)).histogram()[255])
        for x in range(crop.width)
    ]
    smoothed = _smooth_projection(column_projection, max(12, options.document_projection_window_px * 2))
    if not smoothed:
        return [content_box]

    search_start = int(crop.width * 0.3)
    search_end = max(search_start + 1, int(crop.width * 0.7))
    center_slice = smoothed[search_start:search_end]
    if not center_slice:
        return [content_box]

    split_offset = min(range(len(center_slice)), key=lambda idx: center_slice[idx])
    split_x = search_start + split_offset
    valley_score = smoothed[split_x]
    peak_score = max(smoothed)
    if peak_score <= 0 or valley_score > peak_score * 0.22:
        return [content_box]

    left_box = Box.from_points(
        content_box.left,
        content_box.top,
        content_box.left + float(split_x - 15),
        content_box.bottom,
    )
    right_box = Box.from_points(
        content_box.left + float(split_x + 15),
        content_box.top,
        content_box.right,
        content_box.bottom,
    )
    if left_box.width < mask.width * 0.2 or right_box.width < mask.width * 0.2:
        return [content_box]
    return [left_box, right_box]


def _find_document_row_bands(mask: Image.Image, column_box: Box, options: SegmentOptions) -> list[tuple[int, int]]:
    crop = mask.crop((int(column_box.left), int(column_box.top), int(column_box.right), int(column_box.bottom)))
    if crop.width <= 1 or crop.height <= 1:
        return []

    row_projection = [
        int(crop.crop((0, y, crop.width, y + 1)).histogram()[255])
        for y in range(crop.height)
    ]
    smoothed = _smooth_projection(row_projection, options.document_projection_window_px)
    if not smoothed:
        return []

    threshold = max(8.0, max(smoothed) * options.document_row_density_ratio)
    bands: list[tuple[int, int]] = []
    band_start: int | None = None
    last_active: int | None = None
    for row_index, score in enumerate(smoothed):
        if score >= threshold:
            if band_start is None:
                band_start = row_index
            last_active = row_index
            continue
        if band_start is not None and last_active is not None and row_index - last_active <= 16:
            continue
        if band_start is not None and last_active is not None:
            bands.append((band_start, last_active))
        band_start = None
        last_active = None
    if band_start is not None and last_active is not None:
        bands.append((band_start, last_active))

    merged: list[list[int]] = []
    for band_top, band_bottom in bands:
        if not merged or band_top - merged[-1][1] > options.document_band_merge_gap_px:
            merged.append([band_top, band_bottom])
        else:
            merged[-1][1] = band_bottom

    return [
        (band_top, band_bottom)
        for band_top, band_bottom in merged
        if band_bottom - band_top >= options.document_min_band_height_px
    ]


def _merge_small_document_bands(
    bands: list[tuple[int, int]],
    options: SegmentOptions,
) -> list[tuple[int, int]]:
    if len(bands) <= 1:
        return bands

    merged = [[top, bottom] for top, bottom in bands]
    changed = True
    while changed and len(merged) > 1:
        changed = False
        index = 0
        while index < len(merged):
            band_top, band_bottom = merged[index]
            height = band_bottom - band_top
            if height > options.document_small_band_height_px:
                index += 1
                continue

            prev_gap = band_top - merged[index - 1][1] if index > 0 else 10**9
            next_gap = merged[index + 1][0] - band_bottom if index + 1 < len(merged) else 10**9
            if min(prev_gap, next_gap) > options.document_near_gap_px:
                index += 1
                continue

            if next_gap < prev_gap and index + 1 < len(merged):
                merged[index][1] = max(merged[index][1], merged[index + 1][1])
                merged.pop(index + 1)
            elif index > 0:
                merged[index - 1][1] = max(merged[index - 1][1], merged[index][1])
                merged.pop(index)
            elif index + 1 < len(merged):
                merged[index][1] = max(merged[index][1], merged[index + 1][1])
                merged.pop(index + 1)
            changed = True
            index = 0
    return [(top, bottom) for top, bottom in merged]


def _document_band_box(mask: Image.Image, column_box: Box, band: tuple[int, int], options: SegmentOptions) -> Box:
    band_top, band_bottom = band
    crop = mask.crop((int(column_box.left), int(column_box.top + band_top), int(column_box.right), int(column_box.top + band_bottom + 1)))
    bbox = crop.getbbox()
    if bbox is None:
        return Box.from_points(column_box.left, column_box.top + band_top, column_box.right, column_box.top + band_bottom)

    padding = float(options.document_band_padding_px)
    left = column_box.left + max(0.0, float(bbox[0]) - padding)
    right = column_box.left + min(column_box.width, float(bbox[2]) + padding)
    top = column_box.top + max(0.0, float(band_top) - padding)
    bottom = column_box.top + min(column_box.height, float(band_bottom) + padding)
    minimum_width = column_box.width * 0.72
    if right - left < minimum_width:
        left = column_box.left
        right = column_box.right

    return Box.from_points(left, top, right, bottom).expanded(
        8.0,
        max_width=float(mask.width),
        max_height=float(mask.height),
    )


def _segment_document_page(image: Image.Image, page_id: str, options: SegmentOptions) -> tuple[list[ContentBlock], dict[str, Any]]:
    mask = _dark_mask(image, options.document_dark_threshold)
    content_box = _find_document_content_box(mask, image.width, image.height)
    columns = _detect_document_columns(mask, content_box, options)
    blocks: list[ContentBlock] = []

    for column_index, column_box in enumerate(columns, start=1):
        row_bands = _find_document_row_bands(mask, column_box, options)
        row_bands = _merge_small_document_bands(row_bands, options)
        for band_index, band in enumerate(row_bands, start=1):
            box = _document_band_box(mask, column_box, band, options)
            blocks.append(
                ContentBlock(
                    block_id=f"{page_id}-block-{len(blocks) + 1:03d}",
                    block_type=BlockType.STEM,
                    bbox=box,
                    reading_order=len(blocks),
                    metadata={
                        "segmenter": "document-bands",
                        "column_index": column_index,
                        "question_band_index": band_index,
                    },
                )
            )

    if not blocks:
        blocks = [
            ContentBlock(
                block_id=f"{page_id}-block-001",
                block_type=BlockType.IMAGE,
                bbox=content_box,
                reading_order=0,
                metadata={"segmenter": "document-bands", "fallback_reason": "empty_document_segmentation"},
            )
        ]

    return blocks, {
        "segmenter": "document-bands",
        "content_box": {
            "left": content_box.left,
            "top": content_box.top,
            "width": content_box.width,
            "height": content_box.height,
        },
        "column_count": len(columns),
    }


def _find_candidate_boxes_cv2(image: Image.Image, region: Box, options: SegmentOptions) -> list[tuple[Box, float]]:
    if cv2 is None or np is None:
        return []

    region_image = image.crop((int(region.left), int(region.top), int(region.right), int(region.bottom)))
    region_width, region_height = region_image.size
    if region_width <= 1 or region_height <= 1:
        return []

    gray = _pil_to_gray_array(region_image)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, region_width // 30), max(3, region_height // 220)))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, region_width // 220), max(15, region_height // 30)))
    grouped = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, horizontal_kernel)
    grouped = cv2.bitwise_or(grouped, cv2.morphologyEx(binary, cv2.MORPH_CLOSE, vertical_kernel))

    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = region_width * region_height * options.min_area_ratio
    candidates: list[tuple[Box, float]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < min_area:
            continue
        if w >= region_width * options.ignore_large_border_ratio and h >= region_height * options.ignore_large_border_ratio:
            continue
        crop = binary[y : y + h, x : x + w]
        fill_ratio = float(np.count_nonzero(crop)) / float(crop.size) if crop.size else 0.0
        if fill_ratio < options.min_fill_ratio:
            continue
        candidates.append((Box(left=region.left + float(x), top=region.top + float(y), width=float(w), height=float(h)), fill_ratio))
    return candidates


def _find_candidate_boxes_pil(image: Image.Image, region: Box, options: SegmentOptions) -> list[tuple[Box, float]]:
    region_image = image.crop((int(region.left), int(region.top), int(region.right), int(region.bottom)))
    region_width, region_height = region_image.size
    if region_width <= 1 or region_height <= 1:
        return []

    gray = ImageOps.autocontrast(ImageOps.grayscale(region_image))
    stat = ImageStat.Stat(gray)
    threshold = int(max(120.0, min(235.0, stat.mean[0] + stat.stddev[0] * 0.85)))
    bright_mask = gray.point(lambda px: 255 if px >= threshold else 0, mode="L")

    def build_bands(min_row_pixels: int) -> list[tuple[int, int]]:
        row_counts: list[int] = []
        for row_index in range(region_height):
            row = bright_mask.crop((0, row_index, region_width, row_index + 1))
            row_counts.append(int(row.histogram()[255]))

        bands: list[tuple[int, int]] = []
        band_start: int | None = None
        last_active: int | None = None
        for row_index, count in enumerate(row_counts):
            if count >= min_row_pixels:
                if band_start is None:
                    band_start = row_index
                last_active = row_index
                continue
            if band_start is not None and last_active is not None and row_index - last_active <= options.fallback_band_gap_px:
                continue
            if band_start is not None and last_active is not None:
                bands.append((band_start, last_active))
            band_start = None
            last_active = None
        if band_start is not None and last_active is not None:
            bands.append((band_start, last_active))
        return bands

    bands = build_bands(max(2, int(region_width * options.fallback_row_density)))
    if not bands:
        bands = build_bands(max(2, int(region_width * options.fallback_row_density * 0.5)))

    candidates: list[tuple[Box, float]] = []
    min_area = region_width * region_height * max(options.min_area_ratio, 0.001)
    for band_top, band_bottom in bands:
        if band_bottom - band_top + 1 < options.fallback_min_band_height_px:
            continue
        band_mask = bright_mask.crop((0, band_top, region_width, band_bottom + 1))
        bbox = band_mask.getbbox()
        if bbox is None:
            continue
        left, top, right, bottom = bbox
        left = max(0, left - options.fallback_padding_px)
        top = max(0, top - options.fallback_padding_px)
        right = min(region_width, right + options.fallback_padding_px)
        bottom = min(region_height, bottom + options.fallback_padding_px)
        box = Box(left=region.left + float(left), top=region.top + float(top), width=float(max(0, right - left)), height=float(max(0, bottom - top)))
        if box.area < min_area:
            continue
        crop = bright_mask.crop((left, top, right, bottom))
        fill_ratio = float(crop.histogram()[255]) / float(crop.width * crop.height) if crop.width and crop.height else 0.0
        if fill_ratio < options.min_fill_ratio / 3.0:
            continue
        candidates.append((box, fill_ratio))

    if len(candidates) > 1:
        filtered = [
            item
            for item in candidates
            if item[0].area < region_width * region_height * 0.75 and item[1] >= 0.22
        ]
        if filtered:
            candidates = filtered

    if len(candidates) > 1:
        region_area = region_width * region_height
        contained_large_candidates = []
        for index, (box, fill_ratio) in enumerate(candidates):
            contains_other = False
            for other_index, (other_box, _) in enumerate(candidates):
                if index == other_index:
                    continue
                if (
                    box.left <= other_box.left
                    and box.top <= other_box.top
                    and box.right >= other_box.right
                    and box.bottom >= other_box.bottom
                ):
                    contains_other = True
                    break
            if contains_other and box.area >= region_area * 0.08:
                contained_large_candidates.append((box, fill_ratio))
        if contained_large_candidates and len(candidates) - len(contained_large_candidates) >= 1:
            candidates = [item for item in candidates if item not in contained_large_candidates]

    return candidates


def _find_candidate_boxes(image: Image.Image, region: Box, options: SegmentOptions) -> list[tuple[Box, float]]:
    cv2_candidates = _find_candidate_boxes_cv2(image, region, options)
    pil_candidates = _find_candidate_boxes_pil(image, region, options)

    if not cv2_candidates:
        return pil_candidates or [(Box(left=region.left, top=region.top, width=region.width, height=region.height), 1.0)]

    if len(cv2_candidates) == 1:
        candidate_box, _ = cv2_candidates[0]
        if candidate_box.area >= region.area * 0.7 and pil_candidates:
            pil_area = max(box.area for box, _ in pil_candidates)
            if pil_area < candidate_box.area * 0.95:
                return pil_candidates

    if len(pil_candidates) > len(cv2_candidates) and pil_candidates:
        return pil_candidates

    return cv2_candidates


def _split_large_candidate_box(image: Image.Image, box: Box, options: SegmentOptions) -> list[Box]:
    if box.height < max(260.0, image.height * 0.14):
        return [box]

    crop = image.crop((int(box.left), int(box.top), int(box.right), int(box.bottom)))
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    stat = ImageStat.Stat(gray)
    threshold = int(max(120.0, min(235.0, stat.mean[0] + stat.stddev[0] * 0.8)))
    mask = gray.point(lambda px: 255 if px >= threshold else 0, mode="L")

    row_counts: list[int] = []
    for row_index in range(mask.height):
        row = mask.crop((0, row_index, mask.width, row_index + 1))
        row_counts.append(int(row.histogram()[255]))

    if not row_counts:
        return [box]

    window = 7
    smooth_counts: list[float] = []
    for index in range(len(row_counts)):
        start = max(0, index - window)
        end = min(len(row_counts), index + window + 1)
        smooth_counts.append(sum(row_counts[start:end]) / max(1, end - start))

    search_start = int(len(smooth_counts) * 0.15)
    search_end = max(search_start + 1, int(len(smooth_counts) * 0.85))
    segment = smooth_counts[search_start:search_end]
    if not segment:
        return [box]

    split_offset = min(range(len(segment)), key=lambda idx: segment[idx])
    split_row = search_start + split_offset
    max_count = max(smooth_counts)
    min_count = smooth_counts[split_row]
    if max_count <= 0 or min_count > max_count * 0.92:
        return [box]
    if split_row < options.fallback_min_band_height_px or len(smooth_counts) - split_row < options.fallback_min_band_height_px:
        return [box]

    top_box = Box.from_points(box.left, box.top, box.right, box.top + split_row)
    bottom_box = Box.from_points(box.left, box.top + split_row, box.right, box.bottom)
    if top_box.area < box.area * 0.1 or bottom_box.area < box.area * 0.1:
        return [box]
    return [top_box, bottom_box]


def _merge_boxes(boxes: list[Box], options: SegmentOptions) -> list[Box]:
    merged = sorted(boxes, key=lambda item: (item.top, item.left))
    changed = True
    while changed:
        changed = False
        next_boxes: list[Box] = []
        while merged:
            current = merged.pop(0)
            i = 0
            while i < len(merged):
                other = merged[i]
                current_contains_other = (
                    current.left <= other.left
                    and current.top <= other.top
                    and current.right >= other.right
                    and current.bottom >= other.bottom
                )
                other_contains_current = (
                    other.left <= current.left
                    and other.top <= current.top
                    and other.right >= current.right
                    and other.bottom >= current.bottom
                )
                if (current_contains_other or other_contains_current) and max(current.area, other.area) >= min(current.area, other.area) * 1.4:
                    i += 1
                    continue
                vertical_overlap = min(current.bottom, other.bottom) - max(current.top, other.top)
                horizontal_overlap = min(current.right, other.right) - max(current.left, other.left)
                near_same_line = abs(other.top - current.top) <= options.max_merge_gap_y_px
                stacked = 0 <= other.top - current.bottom <= options.max_merge_gap_y_px and horizontal_overlap > -options.max_merge_gap_x_px
                side_by_side = 0 <= other.left - current.right <= options.max_merge_gap_x_px and vertical_overlap > -options.max_merge_gap_y_px
                overlapping = vertical_overlap > 0 or horizontal_overlap > 0
                if overlapping or near_same_line or stacked or side_by_side:
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
        merged = sorted(next_boxes, key=lambda item: (item.top, item.left))
    return merged


def _classify_geometry(image: Image.Image, box: Box, board_region: Box, fill_ratio: float) -> tuple[BlockType, dict[str, float]]:
    crop = image.crop((int(box.left), int(box.top), int(box.right), int(box.bottom))).convert("L")
    stat = ImageStat.Stat(crop)
    mean_intensity = float(stat.mean[0])
    stddev = float(stat.stddev[0])
    aspect_ratio = box.width / max(box.height, 1.0)
    board_area = max(board_region.area, 1.0)
    area_ratio = box.area / board_area

    metadata = {
        "fill_ratio": round(fill_ratio, 4),
        "mean_intensity": round(mean_intensity, 2),
        "stddev": round(stddev, 2),
        "aspect_ratio": round(aspect_ratio, 4),
        "area_ratio": round(area_ratio, 6),
    }

    relative_top = (box.top - board_region.top) / max(board_region.height, 1.0)
    if relative_top < 0.12 and aspect_ratio > 2.0:
        return BlockType.TITLE, metadata
    if area_ratio > 0.18 and stddev > 35:
        return BlockType.IMAGE, metadata
    if aspect_ratio > 4.5 and box.height < board_region.height * 0.08:
        return BlockType.FORMULA, metadata
    if area_ratio < 0.01 and box.height < board_region.height * 0.09:
        return BlockType.NOTE, metadata
    return BlockType.STEM, metadata


def segment_page(
    image_path: str | Path | Image.Image | Any,
    *,
    page_id: str,
    subject: Subject = Subject.UNKNOWN,
    options: SegmentOptions | None = None,
) -> PageModel:
    resolved_options = options or SegmentOptions()
    image = _load_image(image_path)
    if _is_document_like_page(image_path, image):
        blocks, metadata = _segment_document_page(image, page_id, resolved_options)
        source_path = getattr(image_path, "normalized_path", None) or getattr(image_path, "source_path", None)
        if source_path is None and not isinstance(image_path, Image.Image):
            source_path = str(image_path)
        return PageModel(
            page_id=page_id,
            width_px=image.width,
            height_px=image.height,
            subject=subject,
            source_path=source_path,
            blocks=blocks,
            metadata=metadata,
        )

    board_region = _detect_board_region(image, resolved_options)
    candidates = _find_candidate_boxes(image, board_region, resolved_options)
    expanded_candidates: list[tuple[Box, float]] = []
    split_applied = False
    for box, fill_ratio in candidates:
        split_boxes = _split_large_candidate_box(image, box, resolved_options)
        if len(split_boxes) > 1:
            split_applied = True
            for split_box in split_boxes:
                expanded_candidates.append((split_box, fill_ratio))
        else:
            expanded_candidates.append((box, fill_ratio))
    candidates = expanded_candidates
    merged_boxes = [box for box, _ in candidates] if split_applied else _merge_boxes([box for box, _ in candidates], resolved_options)

    blocks: list[ContentBlock] = []
    for index, box in enumerate(sorted(merged_boxes, key=lambda item: (item.top, item.left))):
        fill_ratio = next(
            (
                candidate_fill_ratio
                for candidate_box, candidate_fill_ratio in candidates
                if abs(candidate_box.left - box.left) < 2 and abs(candidate_box.top - box.top) < 2
            ),
            0.25,
        )
        block_type, metadata = _classify_geometry(image, box, board_region, fill_ratio)
        metadata["segmenter"] = "rule-based"
        blocks.append(
            ContentBlock(
                block_id=f"{page_id}-block-{index + 1:03d}",
                block_type=block_type,
                bbox=box,
                reading_order=index,
                metadata=metadata,
            )
        )

    if not blocks:
        blocks = [
            ContentBlock(
                block_id=f"{page_id}-block-001",
                block_type=BlockType.IMAGE,
                bbox=board_region,
                reading_order=0,
                metadata={"fill_ratio": 1.0, "fallback_reason": "empty_segmentation", "segmenter": "rule-based"},
            )
        ]

    source_path = getattr(image_path, "normalized_path", None) or getattr(image_path, "source_path", None)
    if source_path is None and not isinstance(image_path, Image.Image):
        source_path = str(image_path)

    return PageModel(
        page_id=page_id,
        width_px=image.width,
        height_px=image.height,
        subject=subject,
        source_path=source_path,
        blocks=blocks,
        metadata={
            "segmenter": "rule-based",
            "board_region": {
                "left": board_region.left,
                "top": board_region.top,
                "width": board_region.width,
                "height": board_region.height,
            },
        },
    )


def crop_block_images(image_path: str | Path | Image.Image | Any, blocks: Iterable[ContentBlock], output_dir: str | Path) -> dict[str, str]:
    image = _load_image(image_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for block in blocks:
        crop = image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom)))
        path = out_dir / f"{block.block_id}.png"
        crop.save(path)
        written[block.block_id] = str(path)
    return written


def blocks_from_page(prepared_page, config: SegmentOptions | None = None) -> list[ContentBlock]:
    page = segment_page(prepared_page, page_id=prepared_page.page_id, options=config)
    return page.blocks


def crop_block_image(prepared_page, block: ContentBlock) -> Image.Image:
    image = _load_image(prepared_page)
    return image.crop((int(block.bbox.left), int(block.bbox.top), int(block.bbox.right), int(block.bbox.bottom)))
