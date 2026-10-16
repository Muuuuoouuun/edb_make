#!/usr/bin/env python3
"""Compare full-page raster and direct PDF-clip paths for passage groups.

The benchmark reuses the production passage detector but does not alter the
production renderer. V0 renders each involved page and crops it. V1 renders
only the detected PDF clips. V2 selects V1 for structure-ready pages and V0
for raster/OCR pages. All paths use the same clip geometry and stitch cleanup
so the timing and pixel comparisons isolate the rendering decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz
import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_problem_board_edb import (
    PASSAGE_CENTER_DIVIDER_EXCLUSION_PX,
    PASSAGE_CROP_HORIZONTAL_SAFE_PADDING_PX,
    PASSAGE_FRAGMENT_STITCH_GAP_PX,
    _annotate_cross_page_passage_groups,
    _erase_passage_outer_margin_page_guides,
    _prepare_passage_segments_for_stitch,
    _trim_source_page_chrome,
    build_pages,
    detect_pdf_visual_column_divider_x,
    resolve_subject,
)
from scripts.work3_pdf_probe import profile_page


@dataclass(frozen=True, slots=True)
class PassageFragment:
    group_id: str
    label: str
    page_number: int
    fragment_index: int
    bbox_px: tuple[float, float, float, float]
    page_width_px: int
    page_height_px: int
    column_index: int = 0
    segmenter: str = ""
    cross_page_passage_inferred: bool = False


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def _passage_label(metadata: dict[str, Any]) -> str:
    passage_range = metadata.get("passage_range")
    if isinstance(passage_range, dict):
        start = passage_range.get("start")
        end = passage_range.get("end")
        if start is not None and end is not None:
            return f"{start}-{end}"
    return str(metadata.get("passage_group_id") or "passage")


def _collect_fragments(prepared_pages: Sequence[Any], pages: Sequence[Any]) -> list[PassageFragment]:
    prepared_by_id = {page.page_id: page for page in prepared_pages}
    fragments: list[PassageFragment] = []
    for page in pages:
        prepared = prepared_by_id.get(page.page_id)
        if prepared is None:
            continue
        block_by_id = {block.block_id: block for block in page.blocks}
        for problem in page.problems:
            metadata = problem.metadata
            if metadata.get("passage_role") != "passage_fragment":
                continue
            group_id = str(metadata.get("passage_group_id") or problem.unit_id)
            block_ids = metadata.get("shared_passage_block_ids") or problem.stem_block_ids
            blocks = [block_by_id[block_id] for block_id in block_ids if block_id in block_by_id]
            for block in sorted(
                blocks,
                key=lambda item: (
                    int(item.metadata.get("passage_fragment_index") or 0),
                    item.reading_order,
                ),
            ):
                fragments.append(
                    PassageFragment(
                        group_id=group_id,
                        label=_passage_label(metadata),
                        page_number=int(prepared.page_number),
                        fragment_index=int(block.metadata.get("passage_fragment_index") or 1),
                        bbox_px=(
                            float(block.bbox.left),
                            float(block.bbox.top),
                            float(block.bbox.right),
                            float(block.bbox.bottom),
                        ),
                        page_width_px=int(page.width_px),
                        page_height_px=int(page.height_px),
                        column_index=int(block.metadata.get("column_index") or 0),
                        segmenter=str(block.metadata.get("segmenter") or ""),
                        cross_page_passage_inferred=bool(
                            block.metadata.get("cross_page_passage_inferred")
                        ),
                    )
                )
    return fragments


def _expanded_bbox_px(
    fragment: PassageFragment,
    *,
    divider_x_px: float | None = None,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = fragment.bbox_px
    midpoint = (
        float(divider_x_px)
        if divider_x_px is not None
        else fragment.page_width_px * 0.5
    )
    divider_exclusion = float(PASSAGE_CENTER_DIVIDER_EXCLUSION_PX)
    outer_padding = float(PASSAGE_CROP_HORIZONTAL_SAFE_PADDING_PX)
    inner_recovery = 64.0
    if fragment.column_index == 1 or right <= midpoint:
        left -= outer_padding
        right = min(midpoint - divider_exclusion, right + inner_recovery)
    elif fragment.column_index == 2 or left >= midpoint:
        left = max(midpoint + divider_exclusion, left - inner_recovery)
        right += outer_padding
    else:
        left -= outer_padding
        right += outer_padding
    return (
        max(0.0, left),
        max(0.0, top),
        min(float(fragment.page_width_px), right),
        min(float(fragment.page_height_px), bottom),
    )


def _clip_points(
    fragment: PassageFragment,
    page_rect: fitz.Rect,
    *,
    divider_x_px: float | None = None,
) -> fitz.Rect:
    left, top, right, bottom = _expanded_bbox_px(
        fragment,
        divider_x_px=divider_x_px,
    )
    scale_x = fragment.page_width_px / max(1.0, page_rect.width)
    scale_y = fragment.page_height_px / max(1.0, page_rect.height)
    return fitz.Rect(left / scale_x, top / scale_y, right / scale_x, bottom / scale_y) & page_rect


def _problem_marker_intrusions(
    prepared_page: Any,
    expanded_bbox_px: tuple[float, float, float, float],
) -> list[int]:
    """Return numbered question markers crossed by a passage crop."""
    left, top, right, bottom = expanded_bbox_px
    intrusions: list[int] = []
    for marker in prepared_page.metadata.get("pdf_problem_markers") or []:
        if not isinstance(marker, dict) or not isinstance(marker.get("number"), int):
            continue
        bbox = marker.get("bbox")
        if not isinstance(bbox, dict):
            continue
        marker_left = float(bbox.get("left") or 0.0)
        marker_top = float(bbox.get("top") or 0.0)
        marker_right = float(
            bbox.get("right")
            if bbox.get("right") is not None
            else marker_left + float(bbox.get("width") or 0.0)
        )
        marker_bottom = float(
            bbox.get("bottom")
            if bbox.get("bottom") is not None
            else marker_top + float(bbox.get("height") or 0.0)
        )
        overlap_width = min(right, marker_right) - max(left, marker_left)
        overlap_height = min(bottom, marker_bottom) - max(top, marker_top)
        if overlap_width > 1.0 and overlap_height > 1.0:
            intrusions.append(int(marker["number"]))
    return intrusions


def _iter_page_chars(page: fitz.Page) -> Iterable[dict[str, Any]]:
    raw_dict = page.get_text("rawdict")
    for block in raw_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_text = "".join(
                str(char.get("c") or "")
                for span in line.get("spans", [])
                for char in span.get("chars", [])
                if isinstance(char, dict)
            )
            line_bbox = line.get("bbox")
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    if isinstance(char, dict) and str(char.get("c") or "").strip():
                        yield {**char, "_line_text": line_text, "_line_bbox": line_bbox}


def _is_page_chrome_char(char: dict[str, Any], page_rect: fitz.Rect) -> bool:
    line_bbox = char.get("_line_bbox")
    if not isinstance(line_bbox, (list, tuple)) or len(line_bbox) != 4:
        return False
    line_rect = fitz.Rect(*(float(value) for value in line_bbox))
    in_top_band = line_rect.y0 <= page_rect.height * 0.16
    in_bottom_band = line_rect.y1 >= page_rect.height * 0.90
    if not in_top_band and not in_bottom_band:
        return False
    normalized = re.sub(r"\s+", "", str(char.get("_line_text") or ""))
    if not normalized:
        return True
    if in_top_band and any(
        token in normalized
        for token in ("영역", "학년도", "문제지", "고2", "화법과작문", "언어와매체")
    ):
        return True
    if re.fullmatch(r"[━─―—·.ㆍ0-9]+", normalized):
        return True
    return in_bottom_band and len(normalized) <= 5


def _char_bbox_audit(
    page: fitz.Page,
    fragment: PassageFragment,
    clip: fitz.Rect,
    *,
    dpi: int,
) -> dict[str, Any]:
    left, top, right, bottom = fragment.bbox_px
    scale_x = fragment.page_width_px / max(1.0, page.rect.width)
    scale_y = fragment.page_height_px / max(1.0, page.rect.height)
    original = fitz.Rect(left / scale_x, top / scale_y, right / scale_x, bottom / scale_y)
    candidates: list[tuple[str, fitz.Rect]] = []
    for char in _iter_page_chars(page):
        if _is_page_chrome_char(char, page.rect):
            continue
        bbox = char.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        char_rect = fitz.Rect(*(float(value) for value in bbox))
        center = fitz.Point(
            (char_rect.x0 + char_rect.x1) * 0.5,
            (char_rect.y0 + char_rect.y1) * 0.5,
        )
        if not original.y0 <= center.y <= original.y1:
            continue
        if not clip.x0 <= center.x <= clip.x1:
            continue
        candidates.append((str(char.get("c") or ""), char_rect))

    included = [
        (value, rect)
        for value, rect in candidates
        if rect.x0 >= clip.x0 - 0.01
        and rect.y0 >= clip.y0 - 0.01
        and rect.x1 <= clip.x1 + 0.01
        and rect.y1 <= clip.y1 + 0.01
    ]
    recovered = [
        value
        for value, rect in candidates
        if (rect.x0 + rect.x1) * 0.5 < original.x0
        or (rect.x0 + rect.x1) * 0.5 > original.x1
    ]
    horizontal_margins = [
        min(rect.x0 - clip.x0, clip.x1 - rect.x1) * dpi / 72.0
        for _value, rect in included
    ]
    candidate_count = len(candidates)
    clipped_values = [
        value
        for value, rect in candidates
        if rect.x0 < clip.x0 - 0.01
        or rect.y0 < clip.y0 - 0.01
        or rect.x1 > clip.x1 + 0.01
        or rect.y1 > clip.y1 + 0.01
    ]
    return {
        "char_bbox_candidate_count": candidate_count,
        "char_bbox_included_count": len(included),
        "char_bbox_clipped_count": max(0, candidate_count - len(included)),
        "char_bbox_clipped_samples": "".join(clipped_values[:24]),
        "char_bbox_recall": round(len(included) / candidate_count, 6) if candidate_count else 1.0,
        "recovered_outside_block_char_count": len(recovered),
        "recovered_outside_block_char_samples": "".join(recovered[:24]),
        "minimum_horizontal_char_margin_px": (
            round(min(horizontal_margins), 3) if horizontal_margins else None
        ),
    }


def _pixmap_image(pixmap: fitz.Pixmap) -> Image.Image:
    mode = "RGBA" if pixmap.alpha else "RGB"
    return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples).convert("RGB")


def _full_page_crop(image: Image.Image, clip: fitz.Rect, *, dpi: int) -> Image.Image:
    scale = dpi / 72.0
    crop = (
        max(0, math.floor(clip.x0 * scale)),
        max(0, math.floor(clip.y0 * scale)),
        min(image.width, math.ceil(clip.x1 * scale)),
        min(image.height, math.ceil(clip.y1 * scale)),
    )
    return image.crop(crop)


def _stitch(images: Sequence[Image.Image]) -> Image.Image:
    prepared = _prepare_passage_segments_for_stitch(
        [
            _trim_source_page_chrome(image, preserve_horizontal_bounds=True)
            for image in images
        ]
    )
    width = max(image.width for image in prepared)
    gap = PASSAGE_FRAGMENT_STITCH_GAP_PX if len(prepared) > 1 else 0
    height = sum(image.height for image in prepared) + gap * max(0, len(prepared) - 1)
    stitched = Image.new("RGB", (width, height), "white")
    cursor = 0
    for image in prepared:
        stitched.paste(image.convert("RGB"), (0, cursor))
        cursor += image.height + gap
    return stitched


def _image_metrics(reference: Image.Image, candidate: Image.Image) -> dict[str, Any]:
    original_candidate_size = candidate.size
    if candidate.size != reference.size:
        candidate = candidate.resize(reference.size, Image.Resampling.LANCZOS)
    reference_array = np.asarray(reference.convert("RGB"), dtype=np.float32)
    candidate_array = np.asarray(candidate.convert("RGB"), dtype=np.float32)
    mae = float(np.abs(reference_array - candidate_array).mean())
    reference_ink = reference_array.mean(axis=2) < 240.0
    candidate_ink = candidate_array.mean(axis=2) < 240.0
    true_positive = int(np.logical_and(reference_ink, candidate_ink).sum())
    false_positive = int(np.logical_and(~reference_ink, candidate_ink).sum())
    false_negative = int(np.logical_and(reference_ink, ~candidate_ink).sum())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
    return {
        "reference_size": list(reference.size),
        "candidate_size": list(original_candidate_size),
        "size_match": original_candidate_size == reference.size,
        "mean_absolute_error": round(mae, 6),
        "pixel_similarity": round(max(0.0, 1.0 - mae / 255.0), 6),
        "ink_precision": round(precision, 6),
        "ink_recall": round(recall, 6),
        "ink_f1": round(f1, 6),
    }


def _outer_guide_cleanup_metrics(image: Image.Image) -> dict[str, Any]:
    """Measure how much source ink the same-size guide cleanup removes."""
    cleaned = _erase_passage_outer_margin_page_guides(image)
    source_ink = np.asarray(image.convert("L"), dtype=np.uint8) < 240
    cleaned_ink = np.asarray(cleaned.convert("L"), dtype=np.uint8) < 240
    source_count = int(source_ink.sum())
    retained_count = int(np.logical_and(source_ink, cleaned_ink).sum())
    removed_count = max(0, source_count - retained_count)
    return {
        "outer_guide_cleanup_ink_recall": round(
            retained_count / max(1, source_count),
            6,
        ),
        "outer_guide_cleanup_removed_ink_px": removed_count,
    }


def _median(values: Iterable[float]) -> float:
    materialized = list(values)
    return round(statistics.median(materialized), 6) if materialized else 0.0


def compare_pdf_paths(
    source: str | Path,
    *,
    subject: str,
    output_dir: str | Path,
    dpi: int = 200,
    machine_hour_usd: float = 0.0,
) -> dict[str, Any]:
    if subject not in {"korean", "english"}:
        raise ValueError("Work 3 supports only korean and english")
    source_path = Path(source).expanduser().resolve()
    result_dir = Path(output_dir).expanduser().resolve()
    v0_dir = result_dir / "v0-full-page"
    v1_dir = result_dir / "v1-direct-clip"
    v0_dir.mkdir(parents=True, exist_ok=True)
    v1_dir.mkdir(parents=True, exist_ok=True)

    segmentation_started = time.perf_counter()
    prepared_pages, pages = build_pages(
        source_path,
        subject=resolve_subject(subject),
        ocr_mode="noop",
        ai_fallback_config=None,
        pdf_dpi=dpi,
        detect_perspective=False,
        deskew=False,
        crop_margins=False,
        max_dimension=None,
        input_intent="auto",
    )
    _annotate_cross_page_passage_groups(pages)
    segmentation_seconds = time.perf_counter() - segmentation_started
    fragments = _collect_fragments(prepared_pages, pages)
    if not fragments:
        raise ValueError("no passage groups were detected")

    fragments_by_page: dict[int, list[PassageFragment]] = defaultdict(list)
    fragments_by_group: dict[str, list[PassageFragment]] = defaultdict(list)
    for fragment in fragments:
        fragments_by_page[fragment.page_number].append(fragment)
        fragments_by_group[fragment.group_id].append(fragment)

    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    prepared_by_page_number = {
        int(prepared.page_number): prepared
        for prepared in prepared_pages
    }
    full_page_images: dict[int, Image.Image] = {}
    page_dividers_px: dict[int, float | None] = {}
    page_routes: dict[int, str] = {}
    v0_full_render_seconds = 0.0
    with fitz.open(source_path) as document:
        for page_number in sorted(fragments_by_page):
            page = document[page_number - 1]
            page_routes[page_number] = str(profile_page(page, render_dpis=())["route"])
            started = time.perf_counter()
            full_page_images[page_number] = _pixmap_image(page.get_pixmap(matrix=matrix, alpha=False))
            page_dividers_px[page_number] = detect_pdf_visual_column_divider_x(
                full_page_images[page_number]
            )
            v0_full_render_seconds += time.perf_counter() - started

        v0_fragments: dict[tuple[str, int, int], Image.Image] = {}
        v1_fragments: dict[tuple[str, int, int], Image.Image] = {}
        fragment_rows: list[dict[str, Any]] = []
        v0_crop_seconds = 0.0
        v1_clip_render_seconds = 0.0
        for fragment in fragments:
            page = document[fragment.page_number - 1]
            divider_x_px = page_dividers_px.get(fragment.page_number)
            expanded_bbox_px = _expanded_bbox_px(
                fragment,
                divider_x_px=divider_x_px,
            )
            clip = _clip_points(
                fragment,
                page.rect,
                divider_x_px=divider_x_px,
            )
            marker_intrusions = _problem_marker_intrusions(
                prepared_by_page_number[fragment.page_number],
                expanded_bbox_px,
            )
            divider_points = (
                float(divider_x_px) / (fragment.page_width_px / max(1.0, page.rect.width))
                if divider_x_px is not None
                else None
            )
            center_divider_excluded = (
                divider_points is None
                or clip.x1 < divider_points - 0.25
                or clip.x0 > divider_points + 0.25
            )
            key = (fragment.group_id, fragment.page_number, fragment.fragment_index)

            started = time.perf_counter()
            v0_image = _full_page_crop(full_page_images[fragment.page_number], clip, dpi=dpi)
            v0_crop_seconds += time.perf_counter() - started

            started = time.perf_counter()
            v1_image = _pixmap_image(page.get_pixmap(matrix=matrix, clip=clip, alpha=False))
            v1_clip_render_seconds += time.perf_counter() - started

            v0_fragments[key] = v0_image
            v1_fragments[key] = v1_image
            clip_text = page.get_text("text", clip=clip)
            normalized_clip_text = re.sub(r"\s+", "", clip_text)
            page_chrome_tokens = [
                token
                for token in ("저작권", "문제지에관한저작권", "한국교육과정평가원")
                if token in normalized_clip_text
            ]
            fragment_rows.append(
                {
                    "group_id": fragment.group_id,
                    "label": fragment.label,
                    "page_number": fragment.page_number,
                    "fragment_index": fragment.fragment_index,
                    "column_index": fragment.column_index,
                    "segmenter": fragment.segmenter,
                    "cross_page_passage_inferred": fragment.cross_page_passage_inferred,
                    "page_route": page_routes[fragment.page_number],
                    "clip_points": [round(value, 3) for value in (clip.x0, clip.y0, clip.x1, clip.y1)],
                    "page_column_divider_points": (
                        round(divider_points, 3) if divider_points is not None else None
                    ),
                    "center_divider_excluded": center_divider_excluded,
                    "problem_marker_intrusion_count": len(marker_intrusions),
                    "problem_marker_intrusion_numbers": marker_intrusions,
                    "clip_text_char_count": sum(not char.isspace() for char in clip_text),
                    "clip_page_chrome_tokens": page_chrome_tokens,
                    "v0_size": list(v0_image.size),
                    "v1_size": list(v1_image.size),
                    **_outer_guide_cleanup_metrics(v1_image),
                    **_char_bbox_audit(page, fragment, clip, dpi=dpi),
                }
            )

    group_rows: list[dict[str, Any]] = []
    v0_output_bytes = 0
    v1_output_bytes = 0
    for group_id, group_fragments in sorted(
        fragments_by_group.items(), key=lambda item: min(fragment.label for fragment in item[1])
    ):
        ordered = sorted(group_fragments, key=lambda item: item.fragment_index)
        v0_stitched = _stitch(
            [v0_fragments[(group_id, item.page_number, item.fragment_index)] for item in ordered]
        )
        v1_stitched = _stitch(
            [v1_fragments[(group_id, item.page_number, item.fragment_index)] for item in ordered]
        )
        label = ordered[0].label
        group_suffix = hashlib.sha1(group_id.encode("utf-8", errors="ignore")).hexdigest()[:8]
        filename = f"passage_{_safe_name(label)}_{group_suffix}.png"
        v0_path = v0_dir / filename
        v1_path = v1_dir / filename
        v0_stitched.save(v0_path, optimize=True)
        v1_stitched.save(v1_path, optimize=True)
        v0_output_bytes += v0_path.stat().st_size
        v1_output_bytes += v1_path.stat().st_size
        group_rows.append(
            {
                "group_id": group_id,
                "label": label,
                "fragment_count": len(ordered),
                "artifact_filename": filename,
                **_image_metrics(v0_stitched, v1_stitched),
            }
        )

    v0_render_seconds = v0_full_render_seconds + v0_crop_seconds
    v1_render_seconds = v1_clip_render_seconds
    route_counts = Counter(page_routes.values())
    v2_render_seconds = (
        v1_render_seconds
        if route_counts.get("raster-ocr", 0) == 0
        else v0_render_seconds
    )
    # Mixed documents need per-page timing attribution in the next phase. For
    # now the conservative V0 estimate is used whenever any involved page
    # requires raster/OCR fallback.
    v2_policy = "all-direct-clips" if route_counts.get("raster-ocr", 0) == 0 else "conservative-full-page-fallback"

    def local_cost(seconds: float) -> float:
        return round(max(0.0, seconds) / 3600.0 * max(0.0, machine_hour_usd), 8)

    v0_end_to_end_seconds = segmentation_seconds + v0_render_seconds
    v1_end_to_end_seconds = segmentation_seconds + v1_render_seconds
    v2_end_to_end_seconds = segmentation_seconds + v2_render_seconds
    result = {
        "schema_version": 1,
        "experiment": "work3-pdf-path-comparison",
        "source": str(source_path),
        "subject": subject,
        "dpi": dpi,
        "passage_group_count": len(group_rows),
        "passage_fragment_count": len(fragment_rows),
        "involved_page_count": len(fragments_by_page),
        "segmentation_seconds_shared": round(segmentation_seconds, 6),
        "page_route_counts": dict(sorted(route_counts.items())),
        "v0": {
            "path": "full-page-render-then-crop",
            "full_page_render_seconds": round(v0_full_render_seconds, 6),
            "crop_seconds": round(v0_crop_seconds, 6),
            "render_seconds": round(v0_render_seconds, 6),
            "end_to_end_seconds": round(v0_end_to_end_seconds, 6),
            "output_bytes": v0_output_bytes,
            "local_compute_cost_usd": local_cost(v0_render_seconds),
            "end_to_end_local_compute_cost_usd": local_cost(v0_end_to_end_seconds),
            "external_api_cost_usd": 0.0,
        },
        "v1": {
            "path": "pdf-structure-direct-clip",
            "render_seconds": round(v1_render_seconds, 6),
            "end_to_end_seconds": round(v1_end_to_end_seconds, 6),
            "output_bytes": v1_output_bytes,
            "local_compute_cost_usd": local_cost(v1_render_seconds),
            "end_to_end_local_compute_cost_usd": local_cost(v1_end_to_end_seconds),
            "external_api_cost_usd": 0.0,
        },
        "v2": {
            "path": "page-routed-hybrid",
            "policy": v2_policy,
            "estimated_render_seconds": round(v2_render_seconds, 6),
            "estimated_end_to_end_seconds": round(v2_end_to_end_seconds, 6),
            "local_compute_cost_usd": local_cost(v2_render_seconds),
            "end_to_end_local_compute_cost_usd": local_cost(v2_end_to_end_seconds),
            "external_api_cost_usd": 0.0,
        },
        "v1_render_speedup_over_v0": round(v0_render_seconds / max(1e-9, v1_render_seconds), 4),
        "v1_end_to_end_speedup_over_v0": round(
            v0_end_to_end_seconds / max(1e-9, v1_end_to_end_seconds),
            4,
        ),
        "quality_summary": {
            "median_pixel_similarity": _median(row["pixel_similarity"] for row in group_rows),
            "minimum_pixel_similarity": round(min(row["pixel_similarity"] for row in group_rows), 6),
            "median_ink_f1": _median(row["ink_f1"] for row in group_rows),
            "minimum_ink_f1": round(min(row["ink_f1"] for row in group_rows), 6),
            "size_match_group_count": sum(bool(row["size_match"]) for row in group_rows),
            "minimum_char_bbox_recall": round(
                min(row["char_bbox_recall"] for row in fragment_rows),
                6,
            ),
            "clipped_char_bbox_count": sum(
                int(row["char_bbox_clipped_count"])
                for row in fragment_rows
            ),
            "recovered_outside_block_char_count": sum(
                int(row["recovered_outside_block_char_count"])
                for row in fragment_rows
            ),
            "minimum_horizontal_char_margin_px": round(
                min(
                    float(row["minimum_horizontal_char_margin_px"])
                    for row in fragment_rows
                    if row["minimum_horizontal_char_margin_px"] is not None
                ),
                3,
            ),
            "page_chrome_fragment_count": sum(
                bool(row["clip_page_chrome_tokens"])
                for row in fragment_rows
            ),
            "center_divider_checked_fragment_count": sum(
                row["page_column_divider_points"] is not None
                for row in fragment_rows
            ),
            "center_divider_violation_count": sum(
                row["page_column_divider_points"] is not None
                and not bool(row["center_divider_excluded"])
                for row in fragment_rows
            ),
            "problem_marker_intrusion_count": sum(
                int(row["problem_marker_intrusion_count"])
                for row in fragment_rows
            ),
            "minimum_outer_guide_cleanup_ink_recall": round(
                min(float(row["outer_guide_cleanup_ink_recall"]) for row in fragment_rows),
                6,
            ),
        },
        "groups": group_rows,
        "fragments": fragment_rows,
    }
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "benchmark.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--subject", required=True, choices=("korean", "english"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--machine-hour-usd", type=float, default=0.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = compare_pdf_paths(
        args.source,
        subject=args.subject,
        output_dir=args.output_dir,
        dpi=args.dpi,
        machine_hour_usd=args.machine_hour_usd,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
