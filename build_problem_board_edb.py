#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

try:
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    np = None

from build_structured_page_json import build_page_model
from edb_builder import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ImageRecordSpec,
    TextRecordSpec,
    build_edb,
    build_image_record,
    build_preview_image_bytes,
    build_text_record,
    normalize_height_px,
    normalize_width_px,
    normalize_x_px,
    normalize_y_px,
    write_edb,
)
from inspect_edb import parse_edb
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from page_repair import (
    AIFallbackConfig,
    ai_intervention_label,
    ai_intervention_metadata,
    build_ai_capabilities as build_runtime_ai_capabilities,
    build_ai_fallback_config as build_page_ai_fallback_config,
    normalize_ai_intervention_level,
)
from placement_engine import place_problems
from pipeline_feedback import build_parse_feedback
from preprocess import PreparedPage, prepare_source_pages
from segment import draw_segment_debug
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject, save_pages_json


LEFT_MARGIN_PX = 84.0
TOP_PADDING_PX = 20.0
RIGHT_PADDING_PX = 54.0
IMAGE_RECORD_LEFT_MARGIN_PX = 48.0
IMAGE_RECORD_TOP_PADDING_PX = 48.0
PROBLEM_PADDING_PX = 18.0
PROBLEM_VERTICAL_PADDING_PX = 6.0
DOCUMENT_PROBLEM_VERTICAL_PADDING_PX = 0.0
FOOTER_TRIM_PADDING_PX = 6.0
EDB_PREVIEW_MAX_SIZE = (640, 640)
VALIDATION_EPSILON = 1e-6
SOURCE_NATIVE_PDF_DPI = 72.0
MIN_HEIGHT_PAGES = 0.72
MAX_HEIGHT_PAGES = 4.8
MIN_PROBLEM_AREA_RATIO = 0.12
# Brightness above this value (0-255) is treated as a light background that
# should be removed from the exported problem image.
DARK_BOARD_BRIGHTNESS_THRESHOLD = 160
DEFAULT_BOARD_THEME = "charcoal"
BOARD_THEME_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "black": {
        "background": (10, 10, 12),
        "chalk": (250, 250, 248),
    },
    "charcoal": {
        "background": (24, 28, 32),
        "chalk": (248, 249, 246),
    },
    "green": {
        "background": (18, 42, 36),
        "chalk": (244, 248, 241),
    },
}


def _resolve_board_theme(board_theme: str | None) -> str:
    normalized = (board_theme or "").strip().lower()
    if normalized in BOARD_THEME_PALETTES:
        return normalized
    return DEFAULT_BOARD_THEME


def _extract_problem_cutout(image: Image.Image) -> Image.Image:
    """Remove paper-like backgrounds and keep problem ink as an RGBA cutout."""
    rgb = image.convert("RGB")
    gray = ImageOps.autocontrast(rgb.convert("L"))
    stat = ImageStat.Stat(gray)
    mean_brightness = stat.mean[0]
    if mean_brightness <= DARK_BOARD_BRIGHTNESS_THRESHOLD:
        return rgb.convert("RGBA")

    if np is None:
        rgba = rgb.convert("RGBA")
        mask = gray.point(lambda px: 0 if px >= 242 else 255, mode="L")
        rgba.putalpha(mask)
        return rgba

    rgb_array = np.asarray(rgb, dtype=np.float32) / 255.0
    gray_array = np.asarray(gray, dtype=np.float32)
    darkness = 255.0 - gray_array
    noise_floor = max(10.0, float(np.percentile(darkness, 62)) + 4.0)
    alpha_strength = np.clip((darkness - noise_floor) / max(1.0, 255.0 - noise_floor), 0.0, 1.0)
    alpha_strength = np.power(np.clip(alpha_strength * 1.45, 0.0, 1.0), 0.7)

    max_channel = rgb_array.max(axis=2)
    whiteness = gray_array / 255.0
    color_distance = np.linalg.norm(1.0 - rgb_array, axis=2) / np.sqrt(3.0)
    keep_color = np.clip((color_distance - 0.035) / 0.42, 0.0, 1.0)
    keep_dark = np.clip((1.0 - whiteness - 0.08) / 0.7, 0.0, 1.0)
    alpha = np.maximum(alpha_strength, np.maximum(keep_color * 0.92, keep_dark))
    alpha = np.where(max_channel > 0.985, alpha * 0.08, alpha)

    rgba_array = np.dstack(
        [
            np.clip(rgb_array[:, :, 0] * 255.0, 0.0, 255.0),
            np.clip(rgb_array[:, :, 1] * 255.0, 0.0, 255.0),
            np.clip(rgb_array[:, :, 2] * 255.0, 0.0, 255.0),
            np.clip(alpha * 255.0, 0.0, 255.0),
        ]
    ).astype("uint8")
    return Image.fromarray(rgba_array, mode="RGBA")


def _prepare_image_for_dark_board(image: Image.Image, *, board_theme: str = DEFAULT_BOARD_THEME) -> Image.Image:
    """Composite a light-background crop onto a dark ClassIn-style board."""
    resolved_theme = _resolve_board_theme(board_theme)
    palette = BOARD_THEME_PALETTES[resolved_theme]
    cutout = _extract_problem_cutout(image).convert("RGBA")
    alpha_mask = cutout.getchannel("A")

    board = Image.new("RGBA", cutout.size, palette["background"] + (255,))
    chalk_layer = Image.new("RGBA", cutout.size, palette["chalk"] + (0,))
    chalk_layer.putalpha(alpha_mask)
    return Image.alpha_composite(board, chalk_layer).convert("RGB")


def _build_board_render_image(
    image: Image.Image,
    *,
    dark_board: bool,
    board_theme: str,
    cutout: Image.Image | None = None,
) -> Image.Image:
    if not dark_board:
        return image.convert("RGB")
    return _prepare_image_for_dark_board(cutout or image, board_theme=board_theme)


def _enhance_problem_cutout(image: Image.Image, *, intervention_level: int) -> tuple[Image.Image, dict[str, Any]]:
    normalized_level = normalize_ai_intervention_level(intervention_level)
    cutout = _extract_problem_cutout(image).convert("RGBA")
    rgb = cutout.convert("RGB")
    alpha = cutout.getchannel("A")
    scale_factor = 1.0

    if normalized_level >= 1:
        rgb = ImageOps.autocontrast(rgb)
        rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
        rgb = ImageEnhance.Sharpness(rgb).enhance(1.1)
        alpha = ImageOps.autocontrast(alpha)
        alpha = alpha.point(lambda px: 0 if px < 10 else min(255, int(round(pow(px / 255.0, 0.86) * 255.0))))
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.1, percent=125, threshold=2))

    if normalized_level >= 2:
        longest_edge = max(rgb.size)
        scale_factor = 2.0 if longest_edge <= 1800 else 1.5
        target_size = (
            max(1, int(round(rgb.width * scale_factor))),
            max(1, int(round(rgb.height * scale_factor))),
        )
        rgb = rgb.resize(target_size, Image.Resampling.LANCZOS)
        alpha = alpha.resize(target_size, Image.Resampling.LANCZOS)
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.8, percent=160, threshold=2))
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.35))

    enhanced = rgb.convert("RGBA")
    enhanced.putalpha(alpha)
    return enhanced, {
        **ai_intervention_metadata(normalized_level),
        "render_scale_factor": scale_factor,
        "original_width_px": image.width,
        "original_height_px": image.height,
        "render_width_px": enhanced.width,
        "render_height_px": enhanced.height,
    }


def _write_render_image(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def _encode_image_bytes(image: Image.Image, quality: int = 92) -> tuple[bytes, str]:
    """Encode a PIL image for use in an EDB image record."""
    buf = io.BytesIO()
    has_alpha = "A" in image.getbands()
    if has_alpha:
        image.save(buf, format="PNG")
        return buf.getvalue(), "PNG"
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "JPEG"


def _build_edb_image_payload(
    image: Image.Image,
    *,
    quality: int = 90,
    preview_quality: int = 80,
    preview_max_size: tuple[int, int] = EDB_PREVIEW_MAX_SIZE,
) -> tuple[bytes, str, bytes]:
    image_bytes, image_format = _encode_image_bytes(image, quality=quality)
    preview_bytes = build_preview_image_bytes(
        image_bytes,
        max_size=preview_max_size,
        format_hint=image_format,
        quality=preview_quality,
    )
    if len(preview_bytes) >= len(image_bytes):
        preview_bytes = build_preview_image_bytes(
            image_bytes,
            max_size=(320, 320),
            format_hint=image_format,
            quality=max(68, preview_quality - 8),
        )
    return image_bytes, image_format, preview_bytes


TEXT_ELIGIBLE_BLOCK_TYPES = {
    BlockType.TITLE,
    BlockType.SECTION,
    BlockType.STEM,
    BlockType.CHOICE,
    BlockType.EXPLANATION,
    BlockType.NOTE,
}
IMAGE_ONLY_BLOCK_TYPES = {
    BlockType.IMAGE,
    BlockType.DIAGRAM,
    BlockType.TABLE,
    BlockType.DECORATION,
    BlockType.FORMULA,
}


@dataclass
class ProblemEntry:
    problem_id: str
    title: str
    problem_number: int | None
    subject: Subject
    source_page_id: str
    source_path: str
    prepared_page: PreparedPage
    bounds: Box
    crop_path: Path
    cutout_path: Path
    board_render_path: Path
    blocks: list[ContentBlock]
    actual_height_pages: float
    edb_display_width_px: int
    edb_display_height_px: int
    overflow_allowed: bool
    reading_heavy: bool
    content_heavy: bool = False
    render_metadata: dict[str, Any] = field(default_factory=dict)


class ExportValidationError(RuntimeError):
    """Raised when the exported EDB or placement plan fails structural validation."""


def resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def iter_problem_block_ids(page: PageModel, problem: ProblemUnit) -> list[str]:
    ordered: list[str] = []
    for block_id in (
        *problem.stem_block_ids,
        *problem.choice_block_ids,
        *problem.explanation_block_ids,
        *problem.figure_block_ids,
    ):
        if block_id not in ordered:
            ordered.append(block_id)
    if ordered:
        return ordered
    return [block.block_id for block in page.blocks]


def _is_problem_footer_start(text: str | None) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("* 확인 사항"):
        return True
    if stripped.startswith("◦답안지의 해당란"):
        return True
    if stripped.startswith("답안지의 해당란"):
        return True
    if "답안지의 해당란에 필요한 내용을 정확히 기입" in stripped:
        return True
    if "저작권은 한국교육과정평가원에 있습니다" in stripped:
        return True
    return False


def _trim_problem_footer(box: Box, blocks: list[ContentBlock], *, page_height: int) -> Box:
    effective_bottom: float | None = None
    footer_detected = False

    for block in blocks:
        if not block.ocr_lines:
            continue

        for line in sorted(block.ocr_lines, key=lambda item: (item.bbox.top, item.bbox.left)):
            if _is_problem_footer_start(line.text):
                footer_detected = True
                break
            stripped = line.text.strip()
            if not stripped:
                continue
            if stripped.isdigit() and len(stripped) <= 3:
                continue
            effective_bottom = line.bbox.bottom if effective_bottom is None else max(effective_bottom, line.bbox.bottom)

        if footer_detected:
            break

    if not footer_detected or effective_bottom is None:
        return box

    trimmed_bottom = min(box.bottom, effective_bottom + FOOTER_TRIM_PADDING_PX)
    if trimmed_bottom <= box.top + 40.0:
        return box
    return Box.from_points(box.left, box.top, box.right, min(float(page_height), trimmed_bottom))


def merge_boxes(
    boxes: list[Box],
    *,
    page_width: int,
    page_height: int,
    padding_x_px: float = PROBLEM_PADDING_PX,
    padding_y_px: float = PROBLEM_VERTICAL_PADDING_PX,
) -> Box:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return Box.from_points(
        max(0.0, left - float(padding_x_px)),
        max(0.0, top - float(padding_y_px)),
        min(float(page_width), right + float(padding_x_px)),
        min(float(page_height), bottom + float(padding_y_px)),
    )


def estimate_height_pages(image_size: tuple[int, int], template: LayoutTemplate) -> float:
    width_px, height_px = image_size
    estimated = height_px / max(CANVAS_WIDTH, 1.0)
    return max(MIN_HEIGHT_PAGES, min(MAX_HEIGHT_PAGES, estimated))


def _source_native_scale(metadata: dict[str, Any]) -> float:
    source_type = str(metadata.get("source_type") or "").strip().lower()
    source_dpi = float(metadata.get("dpi") or 0.0)
    if source_type == "pdf" and source_dpi > SOURCE_NATIVE_PDF_DPI:
        return max(0.24, SOURCE_NATIVE_PDF_DPI / source_dpi)
    return 1.0


def _resolve_problem_display_size(
    image_size: tuple[int, int],
    *,
    page_metadata: dict[str, Any],
    template: LayoutTemplate,
) -> tuple[int, int, float]:
    width_px, height_px = image_size
    native_scale = _source_native_scale(page_metadata)
    display_width_px = max(1, int(round(width_px * native_scale)))
    display_height_px = max(1, int(round(height_px * native_scale)))
    max_width_px = int(round(CANVAS_HEIGHT * template.fixed_left_zone_ratio - IMAGE_RECORD_LEFT_MARGIN_PX - RIGHT_PADDING_PX))
    if display_width_px > max_width_px:
        shrink = max_width_px / max(display_width_px, 1)
        display_width_px = max(1, int(round(display_width_px * shrink)))
        display_height_px = max(1, int(round(display_height_px * shrink)))
    return display_width_px, display_height_px, native_scale


def _resize_for_edb_display(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgb = image.convert("RGB")
    if rgb.size == size:
        return rgb
    return rgb.resize(size, Image.Resampling.LANCZOS)


def build_pages(
    source: str | Path,
    *,
    subject: Subject,
    ocr_mode: str,
    ai_fallback_config: dict[str, Any] | None,
    ai_fallback_api_key: str = "",
    pdf_dpi: int,
    detect_perspective: bool,
    deskew: bool,
    crop_margins: bool,
    max_dimension: int | None,
    debug_segments_dir: Path | None = None,
) -> tuple[list[PreparedPage], list[PageModel]]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    page_ai_config = _to_page_ai_config(ai_fallback_config, api_key=ai_fallback_api_key)
    page_models = [
        build_page_model(prepared_page, subject=subject, ocr_mode=ocr_mode, ai_config=page_ai_config)
        for prepared_page in prepared_pages
    ]
    if debug_segments_dir is not None:
        for prepared_page, page in zip(prepared_pages, page_models):
            debug_path = debug_segments_dir / f"{page.page_id}_segments.png"
            draw_segment_debug(prepared_page, page.blocks, debug_path)
    return prepared_pages, page_models


def build_problem_entries(
    prepared_pages: list[PreparedPage],
    pages: list[PageModel],
    output_dir: Path,
    template: LayoutTemplate,
    *,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
    ai_intervention_level: int = 0,
) -> list[ProblemEntry]:
    crop_dir = output_dir / "problem_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir = output_dir / "problem_cutouts"
    cutout_dir.mkdir(parents=True, exist_ok=True)
    board_render_dir = output_dir / "board_renders"
    board_render_dir.mkdir(parents=True, exist_ok=True)
    prepared_by_page_id = {page.page_id: page for page in prepared_pages}
    entries: list[ProblemEntry] = []
    normalized_intervention_level = normalize_ai_intervention_level(ai_intervention_level)

    for page in pages:
        prepared_page = prepared_by_page_id.get(page.page_id)
        if prepared_page is None:
            continue
        block_by_id = {block.block_id: block for block in page.blocks}

        for index, problem in enumerate(page.problems):
            problem_block_ids = iter_problem_block_ids(page, problem)
            blocks = [block_by_id[block_id] for block_id in problem_block_ids if block_id in block_by_id]
            raw_problem_number = problem.metadata.get("problem_number")
            if isinstance(raw_problem_number, int):
                problem_number = raw_problem_number
            elif isinstance(raw_problem_number, str) and raw_problem_number.isdigit():
                problem_number = int(raw_problem_number)
            else:
                problem_number = None
            boxes = [block.bbox for block in blocks]
            has_document_band_metadata = any("question_band_index" in block.metadata for block in blocks)
            if not boxes:
                boxes = [Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))]
            merged_box = merge_boxes(
                boxes,
                page_width=page.width_px,
                page_height=page.height_px,
                padding_x_px=PROBLEM_PADDING_PX,
                padding_y_px=DOCUMENT_PROBLEM_VERTICAL_PADDING_PX if has_document_band_metadata else PROBLEM_PADDING_PX,
            )
            merged_box = _trim_problem_footer(merged_box, blocks, page_height=page.height_px)
            if not has_document_band_metadata and merged_box.area < float(page.width_px * page.height_px) * MIN_PROBLEM_AREA_RATIO:
                merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
                blocks = list(page.sorted_blocks())

            crop = prepared_page.image.crop(
                (
                    int(merged_box.left),
                    int(merged_box.top),
                    int(merged_box.right),
                    int(merged_box.bottom),
                )
            )
            crop_name = f"problem_{len(entries) + 1:03d}_{hashlib.sha1(problem.unit_id.encode('utf-8', errors='ignore')).hexdigest()[:8]}.png"
            crop_path = crop_dir / crop_name
            crop.save(crop_path)
            cutout_image, render_metadata = _enhance_problem_cutout(
                crop,
                intervention_level=normalized_intervention_level,
            )
            cutout_path = cutout_dir / crop_name
            _write_render_image(cutout_image, cutout_path)
            board_render_image = _build_board_render_image(
                crop,
                dark_board=dark_board,
                board_theme=board_theme,
                cutout=cutout_image,
            )
            board_render_path = board_render_dir / crop_name
            _write_render_image(board_render_image, board_render_path)
            display_width_px, display_height_px, source_native_scale = _resolve_problem_display_size(
                crop.size,
                page_metadata=prepared_page.metadata,
                template=template,
            )
            actual_height_pages = estimate_height_pages((display_width_px, display_height_px), template)
            has_figure_blocks = any(block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE} for block in blocks)
            has_choice_blocks = any(block.block_type == BlockType.CHOICE for block in blocks)
            reading_heavy = problem.subject in {Subject.KOREAN, Subject.ENGLISH}
            content_heavy = reading_heavy or has_figure_blocks or has_choice_blocks
            overflow_allowed = reading_heavy or (
                problem.subject == Subject.SCIENCE and (has_figure_blocks or actual_height_pages > template.base_slot_height_pages)
            )
            render_metadata = {
                **render_metadata,
                "source_native_scale": source_native_scale,
                "edb_display_width_px": display_width_px,
                "edb_display_height_px": display_height_px,
            }
            problem_title = problem.title or (f"\ubb38\ud56d {problem_number}" if problem_number is not None else f"\ubb38\ud56d {len(entries) + 1}")
            entries.append(
                ProblemEntry(
                    problem_id=problem.unit_id,
                    title=problem_title,
                    problem_number=problem_number,
                    subject=problem.subject,
                    source_page_id=page.page_id,
                    source_path=prepared_page.source_path,
                    prepared_page=prepared_page,
                    bounds=merged_box,
                    crop_path=crop_path,
                    cutout_path=cutout_path,
                    board_render_path=board_render_path,
                    blocks=sorted(blocks, key=lambda block: (block.reading_order, block.bbox.top, block.bbox.left)),
                    actual_height_pages=actual_height_pages,
                    edb_display_width_px=display_width_px,
                    edb_display_height_px=display_height_px,
                    overflow_allowed=overflow_allowed,
                    reading_heavy=reading_heavy,
                    content_heavy=content_heavy,
                    render_metadata=render_metadata,
                )
            )

    return entries


def _to_file_uri(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return Path(path).resolve().as_uri()


def _build_ai_fallback_config(
    *,
    enabled: bool,
    mode: str | None,
    provider: str,
    model: str,
    prompt: str,
    max_tokens: int | None,
    temperature: float | None,
    threshold: float,
    max_regions: int,
    timeout_ms: int,
    save_debug: bool,
    fail_on_error: bool,
    intervention_level: int = 0,
) -> dict[str, Any] | None:
    threshold = 0.72 if threshold is None else float(threshold)
    max_regions = 18 if max_regions is None else int(max_regions)
    timeout_ms = 12000 if timeout_ms is None else int(timeout_ms)
    normalized_intervention_level = normalize_ai_intervention_level(intervention_level)
    resolved_mode = (mode or "").strip().lower() or ("auto" if enabled else "off")
    if resolved_mode not in {"off", "auto", "force"}:
        resolved_mode = "auto" if enabled else "off"
    effective_enabled = resolved_mode != "off"
    if (
        not effective_enabled
        and provider == "openai"
        and not model
        and not prompt
        and max_tokens is None
        and temperature is None
        and threshold == 0.72
        and max_regions == 18
        and timeout_ms == 12000
        and not save_debug
        and not fail_on_error
        and normalized_intervention_level == 0
    ):
        return None
    resolved_model = build_page_ai_fallback_config(
        provider=provider,
        model=model,
        intervention_level=normalized_intervention_level,
    ).resolved_model
    return {
        "enabled": effective_enabled,
        "mode": resolved_mode,
        "provider": provider,
        "model": resolved_model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "threshold": threshold,
        "max_regions": max_regions,
        "timeout_ms": timeout_ms,
        "save_debug": save_debug,
        "fail_on_error": fail_on_error,
        **ai_intervention_metadata(normalized_intervention_level),
    }


def _to_page_ai_config(
    ai_fallback_config: dict[str, Any] | None,
    *,
    api_key: str = "",
) -> AIFallbackConfig:
    if not ai_fallback_config:
        return build_page_ai_fallback_config(api_key=api_key)
    return build_page_ai_fallback_config(
        mode=str(ai_fallback_config.get("mode") or ("auto" if bool(ai_fallback_config.get("enabled")) else "off")),
        provider=str(ai_fallback_config.get("provider") or "openai"),
        model=str(ai_fallback_config.get("model") or ""),
        api_key=api_key,
        threshold=float(ai_fallback_config.get("threshold") or 0.72),
        max_regions=int(ai_fallback_config.get("max_regions") or 18),
        timeout_ms=int(ai_fallback_config.get("timeout_ms") or 12000),
        save_debug=bool(ai_fallback_config.get("save_debug")),
        fail_on_error=bool(ai_fallback_config.get("fail_on_error")),
        intervention_level=normalize_ai_intervention_level(ai_fallback_config.get("intervention_level")),
    )


def _summarize_ai_fallback_usage(pages: list[PageModel], ai_fallback_config: dict[str, Any] | None) -> dict[str, Any] | None:
    attempted_page_count = 0
    applied_page_count = 0
    ai_cache_hit_count = 0
    ocr_cache_hit_count = 0
    ocr_cache_miss_count = 0
    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    route_tier_counts: dict[str, int] = {}

    for page in pages:
        ai_summary = page.metadata.get("ai_fallback")
        if not isinstance(ai_summary, dict):
            ai_summary = {}
        if ai_summary.get("attempted"):
            attempted_page_count += 1
        if ai_summary.get("applied"):
            applied_page_count += 1
        if ai_summary.get("cache_hit"):
            ai_cache_hit_count += 1
        status = str(ai_summary.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

        route_decision = page.metadata.get("route_decision")
        if isinstance(route_decision, dict):
            route = str(route_decision.get("route") or "unknown")
            route_counts[route] = route_counts.get(route, 0) + 1
            profile = route_decision.get("profile")
            if isinstance(profile, dict):
                tier = str(profile.get("tier") or "unknown")
                route_tier_counts[tier] = route_tier_counts.get(tier, 0) + 1

        for block in page.blocks:
            if block.metadata.get("ocr_cache_hit"):
                ocr_cache_hit_count += 1
            if block.metadata.get("ocr_cache_miss"):
                ocr_cache_miss_count += 1

    provider_name = (ai_fallback_config or {}).get("provider", "openai")
    resolved_model = build_page_ai_fallback_config(
        provider=str(provider_name or "openai"),
        model=str((ai_fallback_config or {}).get("model") or ""),
    ).resolved_model
    intervention_level = normalize_ai_intervention_level((ai_fallback_config or {}).get("intervention_level"))
    return {
        "requested": bool(ai_fallback_config.get("enabled")) if ai_fallback_config else False,
        "mode": (ai_fallback_config or {}).get("mode", "off"),
        "provider": provider_name,
        "model": resolved_model,
        "intervention_level": intervention_level,
        "intervention_label": ai_intervention_label(intervention_level),
        "attempted_page_count": attempted_page_count,
        "applied_page_count": applied_page_count,
        "ai_cache_hit_count": ai_cache_hit_count,
        "ocr_cache_hit_count": ocr_cache_hit_count,
        "ocr_cache_miss_count": ocr_cache_miss_count,
        "status_counts": status_counts,
        "route_counts": route_counts,
        "route_tier_counts": route_tier_counts,
        "recommended_page_count": int(status_counts.get("ai_recommended", 0)),
        "local_retry_recommended_page_count": int(status_counts.get("local_retry_recommended", 0)),
    }


def _build_ai_capabilities() -> dict[str, Any]:
    return build_runtime_ai_capabilities()


def _template_to_dict(template: LayoutTemplate) -> dict[str, Any]:
    return {
        "name": template.name,
        "board_page_count": template.board_page_count,
        "base_slot_height_pages": template.base_slot_height_pages,
        "fixed_left_zone_ratio": template.fixed_left_zone_ratio,
        "preserve_right_writing_zone": template.preserve_right_writing_zone,
        "default_overflow_subjects": [subject.value for subject in template.default_overflow_subjects],
        "metadata": dict(template.metadata),
    }


GENERIC_PROBLEM_TITLE_RE = re.compile(r"^\s*문항\s*\d+(?:\s*[·:\-].*)?$")


def _compact_problem_title_text(title: str | None) -> str:
    if not title:
        return ""
    compact = re.sub(r"\s+", " ", title).strip()
    return compact


def _prefer_generic_problem_title(title: str, *, problem_number: int | None) -> bool:
    if not title:
        return True
    if "\n" in title:
        return True
    if len(title) >= 36:
        return True
    if problem_number is not None and title.startswith(f"{problem_number}."):
        return True
    if any(marker in title for marker in ("<보기>", "①", "②", "③", "④", "⑤")):
        return True
    return False


def _normalize_problem_title(title: str | None, index: int, source_page_id: str, problem_number: int | None = None) -> str:
    raw = _compact_problem_title_text(title)
    label = f"문항 {problem_number}" if isinstance(problem_number, int) and problem_number > 0 else f"문항 {index + 1}"
    if not raw or "problem" in raw.lower() or GENERIC_PROBLEM_TITLE_RE.match(raw):
        return label
    if _prefer_generic_problem_title(raw, problem_number=problem_number):
        return label
    if raw.startswith(label):
        return raw
    if isinstance(problem_number, int) and problem_number > 0:
        return f"{label} · {raw}"
    return f"{label} · {raw or source_page_id}"


def build_ui_session(
    prepared_pages: list[PreparedPage],
    placements: list[dict[str, object]],
    output_dir: Path,
    edb_path: Path | None,
    source_paths: Sequence[str | Path],
    *,
    template: LayoutTemplate,
    record_mode: str,
    ai_fallback_config: dict[str, Any] | None = None,
    ai_summary: dict[str, Any] | None = None,
    warning_messages: list[str] | None = None,
    parse_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rendered_page_paths = [Path(page.source_path).resolve() for page in prepared_pages]
    resolved_warnings = list(warning_messages or [])
    if placements and len(placements) <= len(prepared_pages):
        resolved_warnings.append("문항 분리가 충분하지 않아 페이지 단위에 가깝게 묶인 항목이 있습니다.")

    problems: list[dict[str, Any]] = []
    for index, placement in enumerate(placements):
        crop_path = Path(str(placement["crop_path"])).resolve()
        cutout_path = Path(str(placement.get("cutout_path") or crop_path)).resolve()
        source_path = Path(str(placement["source_path"])).resolve()
        problems.append(
            {
                "id": placement["problem_id"],
                "title": _normalize_problem_title(
                    str(placement.get("title") or ""),
                    index,
                    str(placement["source_page_id"]),
                    int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                ),
                "problemNumber": int(placement["problem_number"]) if str(placement.get("problem_number") or "").isdigit() else None,
                "subject": str(placement["subject"]),
                "imagePath": _to_file_uri(cutout_path),
                "cropPath": _to_file_uri(crop_path),
                "sourceImagePath": _to_file_uri(source_path),
                "sourceFileName": source_path.name,
                "boardRenderPath": _to_file_uri(placement.get("board_render_path")),
                "actualHeightPages": float(placement["actual_content_height_pages"]),
                "overflowAllowed": bool(placement["overflow_allowed"]),
                "readingHeavy": bool(placement.get("reading_heavy")),
                "sourcePageId": str(placement["source_page_id"]),
                "startYPages": float(placement["start_y_pages"]),
                "snappedNextStartYPages": float(placement["snapped_next_start_y_pages"]),
                "overflowAmountPages": float(placement["overflow_amount_pages"]),
                "overflowViolation": bool(placement["overflow_violation"]),
                "slotSpanCount": int(placement["slot_span_count"]),
                "recordMode": str(placement.get("record_mode") or record_mode),
                "textRecordCount": int(placement.get("text_record_count", 0)),
                "imageRecordCount": int(placement.get("image_record_count", 0)),
                "aiInterventionLevel": int(placement.get("intervention_level", 0)),
                "aiInterventionLabel": str(placement.get("intervention_label") or ai_intervention_label(int(placement.get("intervention_level", 0)))),
                "renderScaleFactor": float(placement.get("render_scale_factor", 1.0)),
            }
        )

    return {
        "session_name": output_dir.name,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_source": "question_export",
        "output_dir": str(output_dir.resolve()),
        "source_mode": "batch" if len(source_paths) > 1 else "single",
        "input_file_count": len(source_paths),
        "input_files": [str(Path(path).resolve()) for path in source_paths],
        "source_page_count": len(prepared_pages),
        "detected_problem_count": len(placements),
        "export_mode": "question",
        "record_mode": record_mode,
        "pages_json_path": str((output_dir / "pages.json").resolve()),
        "placements_json_path": str((output_dir / "placements.json").resolve()),
        "board_render_dir": str((output_dir / "board_renders").resolve()),
        "edb_path": str(edb_path.resolve()) if edb_path else None,
        "edb_file_uri": _to_file_uri(edb_path),
        "rendered_page_paths": [str(path) for path in rendered_page_paths],
        "rendered_page_file_uris": [_to_file_uri(path) for path in rendered_page_paths],
        "template": _template_to_dict(template),
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "ai_capabilities": _build_ai_capabilities(),
        "warning_messages": list(dict.fromkeys(resolved_warnings)),
        "parse_diagnostics": dict(parse_diagnostics or {}),
        "problems": problems,
    }


def write_ui_session_bundle(output_dir: Path, ui_session: dict[str, Any], *, sync_ui: bool) -> tuple[Path, Path | None]:
    session_path = output_dir / "ui_session.json"
    session_path.write_text(json.dumps(ui_session, ensure_ascii=False, indent=2), encoding="utf-8")

    synced_path: Path | None = None
    if sync_ui:
        synced_path = Path(__file__).resolve().parent / "ui_prototype" / "generated_session.js"
        synced_path.write_text(
            "window.EDB_UI_SESSION = " + json.dumps(ui_session, ensure_ascii=False, indent=2) + ";\n",
            encoding="utf-8",
        )
    return session_path, synced_path


def normalize_text_payload(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def _count_private_use_characters(text: str) -> int:
    return sum(1 for char in text if 0xE000 <= ord(char) <= 0xF8FF)


def _estimate_nontext_ink_ratio(block: ContentBlock, crop: Image.Image) -> float:
    if not block.ocr_lines:
        return 0.0

    gray = ImageOps.autocontrast(crop.convert("L"))
    dark_mask = gray.point(lambda px: 255 if px < 225 else 0, mode="L")
    text_mask = Image.new("L", crop.size, 0)
    draw = ImageDraw.Draw(text_mask)

    block_left = float(block.bbox.left)
    block_top = float(block.bbox.top)
    for line in block.ocr_lines:
        line_left = int(round(line.bbox.left - block_left))
        line_top = int(round(line.bbox.top - block_top))
        line_right = int(round(line.bbox.right - block_left))
        line_bottom = int(round(line.bbox.bottom - block_top))
        draw.rectangle(
            (
                max(0, line_left - 4),
                max(0, line_top - 2),
                min(crop.width, line_right + 4),
                min(crop.height, line_bottom + 2),
            ),
            fill=255,
        )

    ink_count = 0
    nontext_ink_count = 0
    for dark_pixel, text_pixel in zip(dark_mask.getdata(), text_mask.getdata()):
        if not dark_pixel:
            continue
        ink_count += 1
        if not text_pixel:
            nontext_ink_count += 1

    if ink_count <= 0:
        return 0.0
    return nontext_ink_count / float(ink_count)


def _prefer_image_for_pdf_text_layer(
    block: ContentBlock,
    crop: Image.Image | None,
) -> tuple[bool, dict[str, object]]:
    if not block.metadata.get("pdf_text_layer"):
        return False, {}

    text = normalize_text_payload(block.text)
    private_use_char_count = _count_private_use_characters(text)
    nontext_ink_ratio = _estimate_nontext_ink_ratio(block, crop) if crop is not None else 0.0
    has_figure_language = any(token in text for token in ("그림", "(가)", "(나)", "(다)"))
    has_many_lines = len(block.ocr_lines) >= 10

    reason: str | None = None
    if private_use_char_count >= 2:
        reason = "pdf_private_use_glyphs"
    elif nontext_ink_ratio >= 0.58 and has_many_lines:
        reason = "pdf_nontext_content"
    elif nontext_ink_ratio >= 0.48 and has_figure_language:
        reason = "pdf_figure_heavy"

    diagnostics = {
        "pdf_text_layer": True,
        "pdf_private_use_char_count": private_use_char_count,
        "pdf_nontext_ink_ratio": round(nontext_ink_ratio, 4),
        "pdf_has_figure_language": has_figure_language,
    }
    if reason:
        diagnostics["local_image_fallback_reason"] = reason
        return True, diagnostics
    return False, diagnostics


def choose_block_record_mode(
    block: ContentBlock,
    *,
    text_confidence_threshold: float,
    crop_image: Image.Image | None = None,
) -> tuple[str, dict[str, object]]:
    diagnostics: dict[str, object] = {}
    if block.metadata.get("ai_prefer_image_fallback"):
        diagnostics["local_image_fallback_reason"] = "ai_prefer_image_fallback"
        return "image", diagnostics
    if block.block_type in IMAGE_ONLY_BLOCK_TYPES:
        diagnostics["local_image_fallback_reason"] = "image_only_block_type"
        return "image", diagnostics
    text = normalize_text_payload(block.text)
    if not text:
        diagnostics["local_image_fallback_reason"] = "empty_text"
        return "image", diagnostics
    if block.block_type not in TEXT_ELIGIBLE_BLOCK_TYPES:
        diagnostics["local_image_fallback_reason"] = "non_text_eligible_block_type"
        return "image", diagnostics

    prefer_image, pdf_diagnostics = _prefer_image_for_pdf_text_layer(block, crop_image)
    diagnostics.update(pdf_diagnostics)
    if prefer_image:
        return "image", diagnostics

    confidence = block.confidence if block.confidence is not None else 0.0
    if confidence < text_confidence_threshold:
        diagnostics["local_image_fallback_reason"] = "low_confidence"
        return "image", diagnostics
    return "text", diagnostics


def resolve_font_size(block: ContentBlock, scale: float) -> int:
    base_size = block.style.font_size if block.style and block.style.font_size else max(14.0, block.bbox.height * 0.32)
    scaled = base_size * max(scale, 0.25) * 0.9
    return int(max(10, min(40, round(scaled))))


def placement_inputs(problem_entries: list[ProblemEntry]) -> list[ProblemLayoutInput]:
    return [
        ProblemLayoutInput(
            problem_id=entry.problem_id,
            subject=entry.subject,
            actual_content_height_pages=entry.actual_height_pages,
            overflow_allowed=entry.overflow_allowed,
            reading_heavy=entry.reading_heavy,
            metadata={
                "title": entry.title,
                "problem_number": entry.problem_number,
                "crop_path": str(entry.crop_path),
                "cutout_path": str(entry.cutout_path),
                "board_render_path": str(entry.board_render_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
                "content_heavy": entry.content_heavy,
                "height_estimate_source": "display_size",
                "estimated_height_pages": entry.actual_height_pages,
                "edb_display_width_px": entry.edb_display_width_px,
                "edb_display_height_px": entry.edb_display_height_px,
                "source_type": entry.prepared_page.metadata.get("source_type"),
                "source_dpi": entry.prepared_page.metadata.get("dpi"),
                "render_metadata": dict(entry.render_metadata),
            },
        )
        for entry in problem_entries
    ]


def build_image_only_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    for record_id, placement in enumerate(placements):
        crop_path = Path(str(placement.metadata["crop_path"]))
        cutout_path = Path(str(placement.metadata.get("cutout_path") or crop_path))
        board_render_path = Path(str(placement.metadata["board_render_path"]))
        display_width_px = int(placement.metadata.get("edb_display_width_px") or 1)
        display_height_px = int(placement.metadata.get("edb_display_height_px") or 1)
        payload_image = _resize_for_edb_display(Image.open(crop_path), (display_width_px, display_height_px))
        image_bytes, image_format, preview_bytes = _build_edb_image_payload(
            payload_image,
            quality=88,
            preview_quality=80,
        )
        y_px = placement.start_y_pages * CANVAS_WIDTH + IMAGE_RECORD_TOP_PADDING_PX
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=record_id,
                    image_primary=image_bytes,
                    image_secondary=preview_bytes,
                    x=normalize_x_px(IMAGE_RECORD_LEFT_MARGIN_PX),
                    y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                    width_hint=normalize_width_px(display_width_px),
                    height_hint=normalize_height_px(display_height_px, page_count_hint=template.board_page_count),
                )
            )
        )
        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": placement.metadata["title"],
                "problem_number": placement.metadata.get("problem_number"),
                "subject": str(placement.subject),
                "crop_path": str(crop_path),
                "cutout_path": str(cutout_path),
                "board_render_path": str(board_render_path),
                "source_page_id": placement.metadata["source_page_id"],
                "source_path": placement.metadata["source_path"],
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "reading_heavy": placement.reading_heavy,
                "content_heavy": bool(placement.metadata.get("content_heavy")),
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "board_capacity_exceeded": placement.board_capacity_exceeded,
                "slot_span_count": placement.slot_span_count,
                "bbox": placement.metadata["bbox"],
                "record_mode": "image-only",
                "text_record_count": 0,
                "image_record_count": 1,
                "board_theme": _resolve_board_theme(board_theme),
                "height_estimate_source": placement.metadata.get("height_estimate_source"),
                "estimated_height_pages": placement.metadata.get("estimated_height_pages"),
                "edb_display_width_px": display_width_px,
                "edb_display_height_px": display_height_px,
                "edb_payload_source": "crop_rgb",
                **dict(placement.metadata.get("render_metadata") or {}),
            }
        )

    return records, placement_summaries


def build_mixed_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    output_dir: Path,
    text_confidence_threshold: float,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    entries_by_problem_id = {entry.problem_id: entry for entry in problem_entries}
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    block_crop_dir = output_dir / "block_crops"
    block_crop_dir.mkdir(parents=True, exist_ok=True)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        scale = available_width_px / max(entry.bounds.width, 1.0)
        problem_origin_x_px = LEFT_MARGIN_PX
        problem_origin_y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        block_summaries: list[dict[str, object]] = []
        text_record_count = 0
        image_record_count = 0

        for block in entry.blocks:
            x_px = problem_origin_x_px + max(0.0, block.bbox.left - entry.bounds.left) * scale
            y_px = problem_origin_y_px + max(0.0, block.bbox.top - entry.bounds.top) * scale
            width_px = max(40.0, min(available_width_px, block.bbox.width * scale))
            height_px = max(22.0, block.bbox.height * scale)
            crop = None
            if block.metadata.get("pdf_text_layer"):
                crop = entry.prepared_page.image.crop(
                    (
                        int(block.bbox.left),
                        int(block.bbox.top),
                        int(block.bbox.right),
                        int(block.bbox.bottom),
                    )
                )
            record_mode, record_diagnostics = choose_block_record_mode(
                block,
                text_confidence_threshold=text_confidence_threshold,
                crop_image=crop,
            )

            if record_mode == "text":
                text_payload = normalize_text_payload(block.text)
                records.append(
                    build_text_record(
                        TextRecordSpec(
                            record_id=next_record_id,
                            text=text_payload,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            font_size=resolve_font_size(block, scale),
                        )
                    )
                )
                text_record_count += 1
            else:
                if crop is None:
                    crop = entry.prepared_page.image.crop(
                        (
                            int(block.bbox.left),
                            int(block.bbox.top),
                            int(block.bbox.right),
                            int(block.bbox.bottom),
                        )
                    )
                crop_name = f"p{len(placement_summaries) + 1:03d}_b{len(block_summaries) + 1:03d}_{hashlib.sha1((entry.problem_id + block.block_id).encode('utf-8', errors='ignore')).hexdigest()[:8]}.png"
                crop_path = block_crop_dir / crop_name
                crop.save(crop_path)  # Save original for UI/debugging
                block_cutout, _ = _enhance_problem_cutout(
                    crop,
                    intervention_level=int(entry.render_metadata.get("intervention_level", 0)),
                )
                board_crop = _build_board_render_image(
                    crop,
                    dark_board=dark_board,
                    board_theme=board_theme,
                    cutout=block_cutout,
                )
                image_bytes, image_format, preview_bytes = _build_edb_image_payload(
                    board_crop,
                    quality=90,
                    preview_quality=80,
                )
                records.append(
                    build_image_record(
                        ImageRecordSpec(
                            record_id=next_record_id,
                            image_primary=image_bytes,
                            image_secondary=preview_bytes,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            height_hint=normalize_height_px(height_px, page_count_hint=template.board_page_count),
                        )
                    )
                )
                image_record_count += 1

            block_summaries.append(
                {
                    "block_id": block.block_id,
                    "block_type": str(block.block_type),
                    "record_mode": record_mode,
                    "text_present": bool(normalize_text_payload(block.text)),
                    "confidence": block.confidence,
                    "ai_prefer_image_fallback": bool(block.metadata.get("ai_prefer_image_fallback")),
                    **record_diagnostics,
                    "bbox": {
                        "left": block.bbox.left,
                        "top": block.bbox.top,
                        "width": block.bbox.width,
                        "height": block.bbox.height,
                    },
                }
            )
            next_record_id += 1

        if not block_summaries:
            fallback_image = Image.open(entry.board_render_path)
            image_bytes, image_format, preview_bytes = _build_edb_image_payload(
                fallback_image,
                quality=90,
                preview_quality=80,
            )
            records.append(
                build_image_record(
                    ImageRecordSpec(
                        record_id=next_record_id,
                        image_primary=image_bytes,
                        image_secondary=preview_bytes,
                        x=normalize_x_px(LEFT_MARGIN_PX),
                        y=normalize_y_px(problem_origin_y_px, page_count_hint=template.board_page_count),
                        width_hint=normalize_width_px(available_width_px),
                        height_hint=normalize_height_px(
                            placement.actual_content_height_pages * CANVAS_WIDTH,
                            page_count_hint=template.board_page_count,
                        ),
                    )
                )
            )
            image_record_count += 1
            next_record_id += 1

        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": entry.title,
                "problem_number": entry.problem_number,
                "subject": str(entry.subject),
                "crop_path": str(entry.crop_path),
                "cutout_path": str(entry.cutout_path),
                "board_render_path": str(entry.board_render_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "reading_heavy": placement.reading_heavy,
                "content_heavy": bool(placement.metadata.get("content_heavy")),
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "board_capacity_exceeded": placement.board_capacity_exceeded,
                "slot_span_count": placement.slot_span_count,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
                "record_mode": "mixed",
                "text_record_count": text_record_count,
                "image_record_count": image_record_count,
                "board_theme": _resolve_board_theme(board_theme),
                "height_estimate_source": placement.metadata.get("height_estimate_source"),
                "estimated_height_pages": placement.metadata.get("estimated_height_pages"),
                "blocks": block_summaries,
                **dict(entry.render_metadata),
            }
        )

    return records, placement_summaries


def build_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    record_mode: str,
    output_dir: Path,
    text_confidence_threshold: float,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
) -> tuple[list[bytes], list[dict[str, object]], int]:
    if record_mode == "image-only":
        return (
            *build_image_only_records(
                problem_entries,
                template,
                dark_board=dark_board,
                board_theme=board_theme,
            ),
            4,
        )

    records, placement_summaries = build_mixed_records(
        problem_entries,
        template,
        output_dir=output_dir,
        text_confidence_threshold=text_confidence_threshold,
        dark_board=dark_board,
        board_theme=board_theme,
    )
    header_flag = 4 if any(item["image_record_count"] for item in placement_summaries) else 3
    return records, placement_summaries, header_flag


def write_ui_prototype_data(output_path: Path, placements: list[dict[str, object]]) -> None:
    payload = {
        "problems": [
            {
                "id": item["problem_id"],
                "title": item["title"],
                "subject": item["subject"],
                "imagePath": Path(str(item.get("cutout_path") or item["crop_path"])).resolve().as_uri(),
                "cropPath": Path(item["crop_path"]).resolve().as_uri(),
                "boardRenderPath": Path(item["board_render_path"]).resolve().as_uri() if item.get("board_render_path") else None,
                "actualHeightPages": item["actual_content_height_pages"],
                "overflowAllowed": item["overflow_allowed"],
                "readingHeavy": bool(item.get("reading_heavy")),
                "aiInterventionLevel": int(item.get("intervention_level", 0)),
                "aiInterventionLabel": str(item.get("intervention_label") or ai_intervention_label(int(item.get("intervention_level", 0)))),
            }
            for item in placements
        ]
    }
    output_path.write_text(
        "window.PROTOTYPE_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )


def build_placement_summary(placements: list[dict[str, object]]) -> dict[str, object]:
    if not placements:
        return {
            "problem_count": 0,
            "overflow_count": 0,
            "overflow_violation_count": 0,
            "board_capacity_exceeded_count": 0,
            "max_bottom_y_pages": 0.0,
            "text_record_count": 0,
            "image_record_count": 0,
            "multi_image_problem_count": 0,
            "max_image_record_count_per_problem": 0,
        }
    return {
        "problem_count": len(placements),
        "overflow_count": sum(1 for item in placements if float(item["overflow_amount_pages"]) > 0),
        "overflow_violation_count": sum(1 for item in placements if bool(item["overflow_violation"])),
        "board_capacity_exceeded_count": sum(1 for item in placements if bool(item.get("board_capacity_exceeded"))),
        "max_bottom_y_pages": max(float(item["actual_bottom_y_pages"]) for item in placements),
        "text_record_count": sum(int(item.get("text_record_count", 0)) for item in placements),
        "image_record_count": sum(int(item.get("image_record_count", 0)) for item in placements),
        "multi_image_problem_count": sum(1 for item in placements if int(item.get("image_record_count", 0)) > 1),
        "max_image_record_count_per_problem": max(int(item.get("image_record_count", 0)) for item in placements),
    }


def _placement_label(placement: dict[str, object], *, index: int) -> str:
    number = placement.get("problem_number")
    if str(number or "").isdigit():
        return f"{int(number)}번"
    title = str(placement.get("title") or "").strip()
    if title:
        return title
    return f"{index + 1}번 문항"


def _join_labels(labels: list[str], *, limit: int = 4) -> str:
    if not labels:
        return ""
    display = labels[:limit]
    if len(labels) > limit:
        display.append(f"외 {len(labels) - limit}개")
    return ", ".join(display)


def _merge_warning_messages(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for message in group:
            if message and message not in merged:
                merged.append(message)
    return merged


def validate_export_plan(
    placements: list[dict[str, object]],
    *,
    template: LayoutTemplate,
    record_mode: str,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    overflow_labels: list[str] = []
    capacity_labels: list[str] = []
    overlap_labels: list[str] = []
    empty_record_labels: list[str] = []

    total_image_records = sum(int(item.get("image_record_count", 0)) for item in placements)
    total_text_records = sum(int(item.get("text_record_count", 0)) for item in placements)
    multi_image_problem_labels = [
        _placement_label(item, index=index)
        for index, item in enumerate(placements)
        if int(item.get("image_record_count", 0)) > 1
    ]

    previous_snapped_next: float | None = None
    previous_label: str | None = None
    ordering_gap_count = 0

    for index, placement in enumerate(placements):
        label = _placement_label(placement, index=index)
        start_y_pages = float(placement.get("start_y_pages", 0.0) or 0.0)
        actual_height_pages = float(placement.get("actual_content_height_pages", 0.0) or 0.0)
        actual_bottom_y_pages = float(placement.get("actual_bottom_y_pages", 0.0) or 0.0)
        snapped_next_start_y_pages = float(placement.get("snapped_next_start_y_pages", 0.0) or 0.0)
        image_record_count = int(placement.get("image_record_count", 0) or 0)
        text_record_count = int(placement.get("text_record_count", 0) or 0)

        if actual_height_pages <= 0:
            errors.append(f"{label} 높이 추정값이 0 이하입니다.")
        if actual_bottom_y_pages + VALIDATION_EPSILON < start_y_pages:
            errors.append(f"{label} 하단 좌표가 시작 좌표보다 작습니다.")
        if actual_bottom_y_pages > snapped_next_start_y_pages + VALIDATION_EPSILON:
            errors.append(f"{label}의 실제 높이가 다음 스냅 시작점을 넘어 placement 계산이 깨졌습니다.")
        if not image_record_count and not text_record_count:
            empty_record_labels.append(label)
        if record_mode == "image-only" and text_record_count:
            errors.append(f"{label}은 image-only export인데 text record {text_record_count}개가 포함되어 있습니다.")
        if record_mode == "image-only" and image_record_count != 1:
            errors.append(f"{label}은 image-only export인데 image record가 {image_record_count}개입니다.")
        if bool(placement.get("overflow_violation")):
            overflow_labels.append(label)
        if bool(placement.get("board_capacity_exceeded")):
            capacity_labels.append(label)

        if previous_snapped_next is not None:
            if start_y_pages + VALIDATION_EPSILON < previous_snapped_next:
                overlap_labels.append(f"{previous_label} -> {label}")
            elif start_y_pages > previous_snapped_next + VALIDATION_EPSILON:
                ordering_gap_count += 1

        previous_snapped_next = snapped_next_start_y_pages
        previous_label = label

    if empty_record_labels:
        errors.append(f"record가 비어 있는 문항이 있습니다: {_join_labels(empty_record_labels)}")
    if overlap_labels:
        errors.append(f"다음 문항 시작점이 앞 문항 스냅 끝보다 앞선 케이스가 있습니다: {_join_labels(overlap_labels)}")
    if capacity_labels:
        errors.append(
            f"보드 최대 {template.board_page_count}페이지를 넘는 문항이 있습니다: {_join_labels(capacity_labels)}"
        )
    if overflow_labels:
        warnings.append(f"슬롯 높이를 넘는 문항이 있습니다: {_join_labels(overflow_labels)}")
    if ordering_gap_count:
        warnings.append(f"스냅 규칙 대비 예상보다 큰 세로 공백이 {ordering_gap_count}곳 있습니다.")
    if total_image_records > 1:
        warnings.append(
            f"이미지 record가 총 {total_image_records}개라 다중 이미지 ClassIn 안정성 검증이 함께 수행됩니다."
        )
    if multi_image_problem_labels:
        warnings.append(
            f"한 문항 안에 image record가 여러 개인 항목이 있습니다: {_join_labels(multi_image_problem_labels)}"
        )

    return {
        "status": "error" if errors else "warning" if warnings else "ok",
        "record_mode": record_mode,
        "problem_count": len(placements),
        "text_record_count": total_text_records,
        "image_record_count": total_image_records,
        "multi_image_export": total_image_records > 1,
        "multi_image_problem_count": len(multi_image_problem_labels),
        "overflow_violation_count": len(overflow_labels),
        "board_capacity_exceeded_count": len(capacity_labels),
        "ordering_gap_count": ordering_gap_count,
        "warnings": warnings,
        "errors": errors,
    }


def validate_written_edb(
    edb_path: Path,
    *,
    placements: list[dict[str, object]],
    expected_record_count: int,
    expected_header_flag: int,
) -> dict[str, Any]:
    parsed = parse_edb(edb_path)
    warnings: list[str] = []
    errors: list[str] = []

    image_records = [record for record in parsed.records if record.embedded_images]
    text_records = [record for record in parsed.records if record.text is not None]
    expected_image_records = sum(int(item.get("image_record_count", 0)) for item in placements)
    expected_text_records = sum(int(item.get("text_record_count", 0)) for item in placements)
    unsupported_primary_formats = 0
    preview_not_smaller_count = 0
    preview_dimension_issue_count = 0
    missing_secondary_count = 0

    if parsed.record_count_actual != expected_record_count:
        errors.append(
            f"EDB record 수가 예상({expected_record_count})과 다릅니다. 실제 파싱값은 {parsed.record_count_actual}개입니다."
        )
    if parsed.record_count_hint != expected_record_count:
        errors.append(
            f"EDB header의 record_count_hint({parsed.record_count_hint})가 실제 예상값({expected_record_count})과 다릅니다."
        )
    if parsed.header_flag != expected_header_flag:
        errors.append(
            f"EDB header_flag가 예상({expected_header_flag})과 다릅니다. 실제 값은 {parsed.header_flag}입니다."
        )
    if len(image_records) != expected_image_records:
        errors.append(
            f"EDB image record 수가 예상({expected_image_records})과 다릅니다. 실제 파싱값은 {len(image_records)}개입니다."
        )
    if len(text_records) != expected_text_records:
        errors.append(
            f"EDB text record 수가 예상({expected_text_records})과 다릅니다. 실제 파싱값은 {len(text_records)}개입니다."
        )

    for record in image_records:
        if len(record.embedded_images) < 2:
            missing_secondary_count += 1
            continue
        primary = record.embedded_images[0]
        preview = record.embedded_images[1]
        if primary.fmt != "jpeg":
            unsupported_primary_formats += 1
        if preview.length >= primary.length:
            preview_not_smaller_count += 1
        if (
            primary.width is not None
            and preview.width is not None
            and primary.height is not None
            and preview.height is not None
            and (preview.width > primary.width or preview.height > primary.height)
        ):
            preview_dimension_issue_count += 1
        if record.width_hint is None or record.width_hint <= 0 or record.height_hint is None or record.height_hint <= 0:
            errors.append(f"image record #{record.index}의 width/height hint가 유효하지 않습니다.")

    if missing_secondary_count:
        errors.append(f"secondary preview가 없는 image record가 {missing_secondary_count}개 있습니다.")
    if preview_not_smaller_count:
        warnings.append(
            f"preview 이미지가 primary보다 작지 않은 image record가 {preview_not_smaller_count}개 있습니다."
        )
    if preview_dimension_issue_count:
        warnings.append(
            f"preview 해상도가 primary보다 큰 image record가 {preview_dimension_issue_count}개 있습니다."
        )
    if unsupported_primary_formats:
        warnings.append(
            f"primary image가 JPEG가 아닌 record가 {unsupported_primary_formats}개 있습니다."
        )

    return {
        "status": "error" if errors else "warning" if warnings else "ok",
        "path": str(edb_path.resolve()),
        "record_count_actual": parsed.record_count_actual,
        "record_count_hint": parsed.record_count_hint,
        "header_flag": parsed.header_flag,
        "image_record_count": len(image_records),
        "text_record_count": len(text_records),
        "image_record_formats": sorted({record.embedded_images[0].fmt or "unknown" for record in image_records}),
        "preview_not_smaller_count": preview_not_smaller_count,
        "preview_dimension_issue_count": preview_dimension_issue_count,
        "missing_secondary_count": missing_secondary_count,
        "warnings": warnings,
        "errors": errors,
    }


def run_problem_export(
    source: str | Path | Sequence[str | Path],
    *,
    output_dir: str | Path = "mvp_export_question",
    subject_name: str = "unknown",
    ocr: str = "auto",
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    skip_deskew: bool = False,
    skip_crop: bool = False,
    max_dimension: int | None = None,
    export_edb: bool = True,
    edb_name: str = "mvp_board.edb",
    record_mode: str = "image-only",
    text_confidence_threshold: float = 0.78,
    dark_board: bool = True,
    board_theme: str = DEFAULT_BOARD_THEME,
    sync_ui: bool = False,
    template_name: str = "academy-default",
    board_pages: int = 50,
    slot_height: float = 1.2,
    debug_segments_dir: str | Path | None = None,
    prototype_data_out: str | Path | None = None,
    ai_fallback_enabled: bool = False,
    ai_fallback: str | None = None,
    ai_fallback_provider: str = "openai",
    ai_fallback_model: str = "",
    ai_fallback_prompt: str = "",
    ai_fallback_max_tokens: int | None = None,
    ai_fallback_temperature: float | None = None,
    ai_fallback_threshold: float = 0.72,
    ai_fallback_max_regions: int = 18,
    ai_fallback_timeout_ms: int = 12000,
    ai_fallback_save_debug: bool = False,
    ai_intervention_level: int = 0,
    ai_fallback_api_key: str = "",
    fail_on_ai_error: bool = False,
) -> dict[str, Any]:
    if isinstance(source, (str, Path)):
        source_paths = [Path(source).resolve()]
    else:
        source_paths = [Path(path).resolve() for path in source]
    if not source_paths:
        raise ValueError("At least one source path is required")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    subject = resolve_subject(subject_name)
    resolved_board_theme = _resolve_board_theme(board_theme)
    template = LayoutTemplate(
        name=template_name,
        board_page_count=board_pages,
        base_slot_height_pages=slot_height,
    )
    ai_fallback_config = _build_ai_fallback_config(
        enabled=ai_fallback_enabled,
        mode=ai_fallback,
        provider=ai_fallback_provider,
        model=ai_fallback_model,
        prompt=ai_fallback_prompt,
        max_tokens=ai_fallback_max_tokens,
        temperature=ai_fallback_temperature,
        threshold=ai_fallback_threshold,
        max_regions=ai_fallback_max_regions,
        timeout_ms=ai_fallback_timeout_ms,
        save_debug=ai_fallback_save_debug,
        fail_on_error=fail_on_ai_error,
        intervention_level=ai_intervention_level,
    )

    prepared_pages: list[PreparedPage] = []
    pages: list[PageModel] = []
    for source_path in source_paths:
        prepared, page_models = build_pages(
            source_path,
            subject=subject,
            ocr_mode=ocr,
            ai_fallback_config=ai_fallback_config,
            ai_fallback_api_key=ai_fallback_api_key,
            pdf_dpi=pdf_dpi,
            detect_perspective=detect_perspective,
            deskew=not skip_deskew,
            crop_margins=not skip_crop,
            max_dimension=max_dimension,
            debug_segments_dir=Path(debug_segments_dir) if debug_segments_dir else None,
        )
        prepared_pages.extend(prepared)
        pages.extend(page_models)

    save_pages_json(pages, out_dir / "pages.json")
    ai_summary = _summarize_ai_fallback_usage(pages, ai_fallback_config)
    parse_feedback = build_parse_feedback(pages, source_count=len(source_paths))
    problem_entries = build_problem_entries(
        prepared_pages,
        pages,
        out_dir,
        template,
        dark_board=dark_board,
        board_theme=resolved_board_theme,
        ai_intervention_level=ai_intervention_level,
    )
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=record_mode,
        output_dir=out_dir,
        text_confidence_threshold=text_confidence_threshold,
        dark_board=dark_board,
        board_theme=resolved_board_theme,
    )
    export_validation = validate_export_plan(
        placements,
        template=template,
        record_mode=record_mode,
    )
    warning_messages = _merge_warning_messages(
        parse_feedback["warning_messages"],
        export_validation["warnings"],
    )
    if export_validation["errors"]:
        raise ExportValidationError("EDB validation failed: " + " / ".join(export_validation["errors"]))

    summary = {
        "source_paths": [str(path) for path in source_paths],
        "output_dir": str(out_dir.resolve()),
        "pages_json_path": str((out_dir / "pages.json").resolve()),
        "problem_crop_dir": str((out_dir / "problem_crops").resolve()),
        "problem_cutout_dir": str((out_dir / "problem_cutouts").resolve()),
        "board_render_dir": str((out_dir / "board_renders").resolve()),
        "block_crop_dir": str((out_dir / "block_crops").resolve()),
        "record_count": len(records),
        "record_mode": record_mode,
        "dark_board": dark_board,
        "board_theme": resolved_board_theme,
        "header_flag": header_flag,
        "text_confidence_threshold": text_confidence_threshold,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "parse_diagnostics": parse_feedback["parse_diagnostics"],
        "warning_messages": warning_messages,
        "placement_summary": build_placement_summary(placements),
        "export_validation": export_validation,
        "edb_validation": None,
        "placements": placements,
        "ocr_backend_requested": ocr,
    }

    placements_path = out_dir / "placements.json"
    placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = out_dir / "board_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    edb_path: Path | None = None
    if export_edb:
        edb_path = out_dir / edb_name
        write_edb(
            edb_path,
            build_edb(records, header_flag=header_flag, page_count_hint=template.board_page_count),
        )
        edb_validation = validate_written_edb(
            edb_path,
            placements=placements,
            expected_record_count=len(records),
            expected_header_flag=header_flag,
        )
        summary["edb_validation"] = edb_validation
        summary["warning_messages"] = _merge_warning_messages(
            summary["warning_messages"],
            edb_validation["warnings"],
        )
        summary["edb_path"] = str(edb_path.resolve())
        placements_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if edb_validation["errors"]:
            raise ExportValidationError("EDB validation failed: " + " / ".join(edb_validation["errors"]))

    prototype_path: Path | None = None
    if prototype_data_out:
        prototype_path = Path(prototype_data_out)
        prototype_path.parent.mkdir(parents=True, exist_ok=True)
        write_ui_prototype_data(prototype_path, placements)

    ui_session = build_ui_session(
        prepared_pages,
        placements,
        out_dir,
        edb_path if export_edb else None,
        source_paths,
        template=template,
        record_mode=record_mode,
        ai_fallback_config=ai_fallback_config,
        ai_summary=ai_summary,
        warning_messages=summary["warning_messages"],
        parse_diagnostics=parse_feedback["parse_diagnostics"],
    )
    ui_session_path, synced_ui_path = write_ui_session_bundle(out_dir, ui_session, sync_ui=sync_ui)

    return {
        "output_dir": out_dir.resolve(),
        "edb_path": edb_path.resolve() if edb_path and edb_path.exists() else None,
        "pages_json_path": (out_dir / "pages.json").resolve(),
        "placements_json_path": placements_path.resolve(),
        "summary_path": summary_path.resolve(),
        "ui_session": ui_session,
        "ui_session_path": ui_session_path.resolve(),
        "synced_ui_path": synced_ui_path.resolve() if synced_ui_path else None,
        "prototype_data_path": prototype_path.resolve() if prototype_path else None,
        "summary": summary,
        "ai_fallback": ai_fallback_config,
        "ai_summary": ai_summary,
        "ai_capabilities": ui_session.get("ai_capabilities"),
        "parse_feedback": parse_feedback,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a problem-board EDB from a source image or PDF.")
    parser.add_argument("source", help="Path to a PDF or image source")
    parser.add_argument("--output-dir", default="mvp_export", help="Directory for pipeline artifacts and EDB output")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="noop", help="OCR backend: noop, auto, paddleocr, tesseract")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--template-name", default="academy-default", help="Layout template name")
    parser.add_argument("--board-pages", type=int, default=50, help="Board page count hint")
    parser.add_argument("--slot-height", type=float, default=1.2, help="Base slot height in board pages")
    parser.add_argument("--record-mode", choices=("mixed", "image-only"), default="image-only", help="Record generation strategy")
    parser.add_argument("--text-confidence-threshold", type=float, default=0.78, help="Minimum OCR confidence for text records in mixed mode")
    parser.add_argument(
        "--board-theme",
        choices=tuple(BOARD_THEME_PALETTES.keys()),
        default=DEFAULT_BOARD_THEME,
        help="Dark board palette used when converting light-background crops",
    )
    parser.add_argument("--light-board", action="store_true", help="Disable dark-board color conversion (keep original light background in image records)")
    parser.add_argument("--debug-segments", action="store_true", help="Save block overlay images to <output-dir>/debug_segments/ for segmentation inspection")
    parser.add_argument("--ai-fallback-enabled", action="store_true", help="Enable optional AI fallback settings")
    parser.add_argument("--ai-fallback", default=None, help="AI fallback mode override: off, auto, force")
    parser.add_argument("--ai-fallback-provider", default="openai", help="AI fallback provider name")
    parser.add_argument("--ai-fallback-model", default="", help="AI fallback model name")
    parser.add_argument("--ai-fallback-prompt", default="", help="AI fallback prompt template")
    parser.add_argument("--ai-fallback-max-tokens", type=int, default=None, help="AI fallback max output tokens")
    parser.add_argument("--ai-fallback-temperature", type=float, default=None, help="AI fallback sampling temperature")
    parser.add_argument("--ai-fallback-threshold", type=float, default=0.72, help="Low-confidence trigger threshold for AI fallback")
    parser.add_argument("--ai-fallback-max-regions", type=int, default=18, help="Maximum number of regions sent to AI fallback")
    parser.add_argument("--ai-fallback-timeout-ms", type=int, default=12000, help="Timeout in milliseconds for AI fallback")
    parser.add_argument("--ai-fallback-save-debug", action="store_true", help="Write AI fallback debug artifacts")
    parser.add_argument("--ai-intervention-level", type=int, choices=(0, 1, 2), default=0, help="AI intervention stage: 0 structure/crop/layout, 1 parse/recolor, 2 rebuild/upscale")
    parser.add_argument("--fail-on-ai-error", action="store_true", help="Raise an error if AI fallback fails")
    parser.add_argument("--prototype-data-out", default="ui_prototype/prototype_data.js", help="Path to write UI prototype data JS")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    debug_segments_dir = output_dir / "debug_segments" if args.debug_segments else None
    edb_name = f"{Path(args.source).stem}.edb"
    result = run_problem_export(
        args.source,
        output_dir=output_dir,
        subject_name=args.subject,
        ocr=args.ocr,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        skip_deskew=args.skip_deskew,
        skip_crop=args.skip_crop,
        max_dimension=args.max_dimension,
        export_edb=True,
        edb_name=edb_name,
        record_mode=args.record_mode,
        text_confidence_threshold=args.text_confidence_threshold,
        dark_board=not args.light_board,
        board_theme=args.board_theme,
        sync_ui=True,
        template_name=args.template_name,
        board_pages=args.board_pages,
        slot_height=args.slot_height,
        debug_segments_dir=debug_segments_dir,
        prototype_data_out=args.prototype_data_out,
        ai_fallback_enabled=args.ai_fallback_enabled,
        ai_fallback=args.ai_fallback,
        ai_fallback_provider=args.ai_fallback_provider,
        ai_fallback_model=args.ai_fallback_model,
        ai_fallback_prompt=args.ai_fallback_prompt,
        ai_fallback_max_tokens=args.ai_fallback_max_tokens,
        ai_fallback_temperature=args.ai_fallback_temperature,
        ai_fallback_threshold=args.ai_fallback_threshold,
        ai_fallback_max_regions=args.ai_fallback_max_regions,
        ai_fallback_timeout_ms=args.ai_fallback_timeout_ms,
        ai_fallback_save_debug=args.ai_fallback_save_debug,
        ai_intervention_level=args.ai_intervention_level,
        fail_on_ai_error=args.fail_on_ai_error,
    )
    summary = result["summary"]

    print(
        json.dumps(
            {
                "edb_path": str(result["edb_path"]) if result["edb_path"] else None,
                "pages_json_path": str(result["pages_json_path"]),
                "placements_json_path": str(result["placements_json_path"]),
                "board_run_summary_path": str(result["summary_path"]),
                "ui_session_path": str(result["ui_session_path"]),
                "synced_ui_path": str(result["synced_ui_path"]) if result["synced_ui_path"] else None,
                "ui_prototype_data_path": str(result["prototype_data_path"]) if result["prototype_data_path"] else None,
                "problem_count": int(summary["placement_summary"]["problem_count"]),
                "record_mode": args.record_mode,
                "text_record_count": summary["placement_summary"]["text_record_count"],
                "image_record_count": summary["placement_summary"]["image_record_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
